"""生图任务：保存参考图、提交到 ComfyUI、跟踪进度、写历史记录。网页和命令行共用。"""
import asyncio
import hashlib
import importlib
import io
import json
import random
import time
import tomllib
import uuid
from datetime import date
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps

import pipeline
from comfy_client import UNREACHABLE

STUDIO = Path(__file__).resolve().parent
ROOT = STUDIO.parent
INPUT = ROOT / 'input'      # 与 scripts/launch.py 传给 ComfyUI 的目录一致
OUTPUT = ROOT / 'output'
HISTORY = OUTPUT / 'studio' / 'history.jsonl'
MAX_REFS = 16               # TextEncodeQwenImage21 的 images 插槽数；官方建议不超过 10
LOST_AFTER = 30             # 秒，见 wait_prompt
STUCK_AFTER = 300           # 秒，见 wait_prompt；显存超额时正常任务也可能一分半钟不响应


class Cancelled(Exception):
    pass


def load_config():
    with open(STUDIO / 'config.toml', 'rb') as f:
        return tomllib.load(f)


def inside(base, relative):
    """base 下的相对路径 → 绝对路径；越界或不存在时返回 None。"""
    path = (base / relative).resolve()
    return path if path.is_relative_to(base.resolve()) and path.is_file() else None


def save_reference(data, cfg):
    """把上传的图片转成 PNG 存进 input/studio/（按内容去重），返回给 LoadImage 用的相对路径。"""
    name = f'studio/{hashlib.sha1(data).hexdigest()[:16]}.png'
    path = INPUT / name
    if not path.exists():
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        image = image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
        side = cfg['size']['reference_max_side']
        image.thumbnail((side, side), Image.LANCZOS)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
    return name


def make_people(data, cfg, ref_count):
    """校验人物设定 {'scene': {...}, 'persons': [{...}, ...], 'strict': 是否严格手脚}：只留 config.toml [people] 里有的字段，去掉空值。
    没有人物时返回 None。空白的人物也算一个人（计入人数）。"""
    spec = cfg.get('people')
    if not spec or not data or not data.get('persons'):
        return None
    if len(data['persons']) > spec['max']:
        raise ValueError(f"人物最多 {spec['max']} 个。")

    def clean(values, fields):
        values = {f['key']: str(values.get(f['key']) or '').strip() for f in fields}
        return {key: value for key, value in values.items() if value}

    persons = [clean(person, spec['person']) for person in data['persons']]
    for i, person in enumerate(persons, 1):
        for field in spec['person']:
            value = person.get(field['key'])
            if field.get('type') == 'ref' and value and not (value.isdigit() and 1 <= int(value) <= ref_count):
                raise ValueError(f"人物{i} 的「{field['label']}」选了图{value}，但只放了 {ref_count} 张参考图。")
    return {'scene': clean(data.get('scene') or {}, spec['scene']), 'persons': persons, 'strict': bool(data.get('strict'))}


def position_phrase(hit):
    """一种颜色的笔画（布尔掩码）在画面哪里：「画面中间偏右、从上部延伸到下部」。"""
    import numpy as np
    ys, xs = np.nonzero(hit)
    h, w = hit.shape
    if (xs.max() - xs.min()) / w > 0.7:
        horiz = '横跨整个画面'
    else:
        horiz = '画面' + ('左侧', '中间偏左', '中间', '中间偏右', '右侧')[min(4, int(xs.mean() / w * 5))]
    rows = ('上部', '中部', '下部')
    top, bottom = rows[min(2, int(ys.min() / h * 3))], rows[min(2, int(ys.max() / h * 3))]
    return f'{horiz}、' + (top if top == bottom else f'从{top}延伸到{bottom}')


def sketch_lines(req, cfg):
    """「草图生图」（模式里 sketch_lines = true）：彩色线条原样给模型会被照抄成彩色简笔画，所以模型只看黑线稿，
    每种颜色代表什么改成按位置写的文字。生成黑线稿存进 input（req['sketch_line']，生成时代替图1），
    涂满的色块只留轮廓（整块变黑会被当成黑色的东西）；给每条 marks 补上 where（这种颜色的笔画在画面哪里）。"""
    import numpy as np
    from PIL import ImageChops, ImageFilter
    with Image.open(INPUT / req['refs'][0]) as image:
        rgb = np.asarray(image.convert('RGB')).astype(int)
    h, w, _ = rgb.shape
    mask = Image.fromarray(((rgb.min(2) < 200) * 255).astype(np.uint8))   # 不是白底的地方都是笔画
    k = max(5, round(max(w, h) * 0.012)) | 1
    outline = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(k)))  # 细线条整条保留，粗色块只剩一圈边
    line = Image.new('RGB', (w, h), 'white')
    line.paste((30, 30, 30), mask=outline)
    buf = io.BytesIO()
    line.save(buf, 'PNG')
    req['sketch_line'] = save_reference(buf.getvalue(), cfg)
    hexes = {c['name']: c['hex'] for c in cfg['board']['colors']}
    for m in req['marks']:
        target = np.array([int(hexes[m['color']][i:i + 2], 16) for i in (1, 3, 5)])
        hit = ((rgb - target) ** 2).sum(2) < 75 ** 2
        m['where'] = position_phrase(hit) if hit.mean() > 0.0005 else ''


def make_marks(marks, cfg):
    """画板上各颜色的含义：[{color: config [board] 里的颜色名, kind: line/circle/paint, text}]，同一颜色只留一条。"""
    names = [c['name'] for c in cfg['board']['colors']]
    out = {}
    for m in marks or []:
        color, kind, text = m.get('color'), m.get('kind') or 'line', str(m.get('text') or '').strip()[:300]
        if color not in names:
            raise ValueError(f'画板里没有这个颜色：{color}')
        if kind not in ('line', 'circle', 'paint'):
            raise ValueError(f'不认识的标注方式：{kind}')
        out[color] = {'color': color, 'kind': kind, 'text': text.rstrip('。；;，, ')}
    return list(out.values())


def make_request(data, cfg):
    """校验网页/命令行传来的参数，补上默认值。"""
    refs = data.get('refs') or []
    missing = [ref for ref in refs if not inside(INPUT, ref)]
    if missing:
        raise ValueError(f'参考图找不到了（可能被删了）：{", ".join(missing)}')
    prompt = (data.get('prompt') or '').strip()
    people = make_people(data.get('people'), cfg, len(refs))
    mode = data.get('mode') or ''
    if mode and mode not in cfg.get('modes', {}):
        raise ValueError(f'不认识的模式：{mode}')
    toggle = bool(mode and data.get('toggle') and 'toggle' in cfg['modes'][mode])
    preset = pipeline.preset(cfg, {'mode': mode, 'toggle': toggle})
    if len(refs) < preset.get('min_refs', 0):
        raise ValueError(preset.get('need') or f"「{preset['label']}」至少要 {preset['min_refs']} 张参考图。")
    if not prompt and not refs and not people:
        raise ValueError('请写一句描述，或者至少放一张参考图。')
    marks = make_marks(data.get('marks'), cfg) if 'mark_line' in preset else []
    if preset.get('marks_required') and not prompt and not any(m['text'] for m in marks):
        raise ValueError(preset['marks_required'])
    if len(refs) > MAX_REFS:
        raise ValueError(f'参考图最多 {MAX_REFS} 张。')
    aspect = preset.get('aspect') or data.get('aspect') or 'auto'
    if aspect == 'auto' and preset.get('default_aspect'):  # 要新画布的模式（扩图、四视图）不能跟随图1
        aspect = preset['default_aspect']
    if aspect != 'auto' and aspect not in cfg['size']['aspects']:
        raise ValueError(f'不认识的比例：{aspect}')
    quality = data.get('quality') or cfg['size']['default_quality']
    if quality not in cfg['size']['quality']:
        raise ValueError(f'不认识的画质：{quality}')
    style = int(data.get('style') or 0)  # 画风照第几张参考图，0 = 不统一
    if not 0 <= style <= len(refs):
        raise ValueError(f'「画风」选的是图{style}，但只有 {len(refs)} 张参考图。')
    upscale = int(data.get('upscale') or 0)  # 0 = 原图（生图模型直接出的图）
    if upscale and upscale not in cfg['upscale']['targets']:
        raise ValueError(f'不认识的超分尺寸：{upscale}')
    seed = data.get('seed')
    req = {
        'prompt': prompt,
        'mode': mode,
        'toggle': toggle,
        'people': people,
        'marks': marks,
        'refs': refs,
        'aspect': aspect,
        'quality': quality,
        'count': min(max(int(data.get('count') or 1), 1), 8),
        'steps': min(max(int(data.get('steps') or cfg['sampling']['steps']), 1), 200),
        'seed': random.randrange(1, 2 ** 48) if seed in (None, '') else int(seed),
        'transparent': bool(data.get('transparent')),
        'upscale': upscale,
        'repaint': bool(upscale and data.get('repaint')),
        'style': style,
        'style_text': str(data.get('style_text') or '').strip() if style else '',  # 再来一次时沿用历史里的分析结果
    }
    if preset.get('sketch_lines') and refs:
        sketch_lines(req, cfg)
    if preset.get('outpaint'):  # 扩图：要知道原图尺寸才能算往哪边扩、扩多少
        if aspect == 'auto':
            raise ValueError('扩图要选一个目标比例（不能是「自动」）。')
        anchor = data.get('anchor') or 'center'
        if anchor not in ('center', 'left', 'right', 'top', 'bottom'):
            raise ValueError(f'不认识的原图位置：{anchor}')
        with Image.open(INPUT / refs[0]) as image:
            req.update(anchor=anchor, ref_size=list(image.size))
        layout = pipeline.outpaint_layout(req, cfg)
        if not any(layout[k] for k in ('left', 'top', 'right', 'bottom')):
            raise ValueError(f'图1 本来就差不多是 {aspect}，没有可扩的地方，换一个比例试试。')
    return req


class Job:
    def __init__(self, req):
        self.id = uuid.uuid4().hex[:12]
        self.created = time.time()
        self.finished = None
        self.req = req
        self.status = 'running'  # running / done / error / cancelled
        self.phase = '准备中'
        self.step = self.total = 0  # 当前阶段的进度（采样步数）
        self.index = 0              # 正在生成第几张（从 0 开始）
        self.images = []
        self.error = ''
        self.prompt_ids = []
        self.cancelled = False

    def record(self):
        return {'id': self.id, 'created': self.created, **self.req, 'status': self.status, 'error': self.error,
                'images': self.images, 'elapsed': round((self.finished or time.time()) - self.created, 1)}

    def public(self):
        return {**self.record(), 'phase': self.phase, 'step': self.step, 'total': self.total, 'index': self.index}

    async def cancel(self, comfy):
        self.cancelled = True
        if self.prompt_ids:
            await comfy.cancel(self.prompt_ids)


async def run_job(job, comfy):
    try:
        cfg = load_config()
        importlib.reload(pipeline)  # pipeline.py 改完立即生效
        job.phase = '启动 ComfyUI（首次约半分钟）' if not await comfy.alive() else '提交任务'
        await comfy.ensure_running()
        if job.req.get('style') and not job.req.get('style_text'):
            job.req['style_text'] = await style_text(job, comfy, cfg, job.req['refs'][job.req['style'] - 1])
        prefix = f'studio/{date.today().isoformat()}/qwen'
        graphs = [pipeline.build({**job.req, 'seed': job.req['seed'] + i}, cfg, prefix) for i in range(job.req['count'])]
        ids = []  # 只有生成的任务（job.prompt_ids 里可能还有前面分析画风的任务，取消时要一起取消）
        for graph in graphs:
            if job.cancelled:
                raise Cancelled
            ids.append(await comfy.queue(graph))
            job.prompt_ids.append(ids[-1])
        for i, (prompt_id, graph) in enumerate(zip(ids, graphs)):
            job.index = i
            for file in collect_images(await wait_prompt(job, comfy, prompt_id, graph)):
                with Image.open(OUTPUT / file) as image:
                    width, height = image.size
                job.images.append({'file': file, 'seed': job.req['seed'] + i, 'width': width, 'height': height})
        job.status = 'done'
    except Cancelled:
        job.status = 'cancelled'
    except Exception as error:
        job.status = 'error'
        job.error = str(error) or type(error).__name__
    finally:
        job.finished = time.time()
        if job.images or job.status == 'error':
            append_history(job.record())


async def style_text(job, comfy, cfg, ref):
    """「画风」选项：参考图的画法描述（看图模型写的，一两句中文）。同一张图、同一份 [style] system 只分析一次，
    结果缓存在 input/<ref>.style.json。"""
    key = hashlib.sha1((cfg['style']['system'] + cfg['style']['user']).encode()).hexdigest()[:12]
    cache = INPUT / (ref + '.style.json')
    if cache.exists():
        saved = json.loads(cache.read_text('utf-8'))
        if saved.get('key') == key:
            return saved['text']
    with Image.open(INPUT / ref) as image:
        size = image.size
    graph = pipeline.build_style(ref, size, cfg, seed=random.randrange(2 ** 32))
    job.prompt_ids.append(await comfy.queue(graph))
    text = ' '.join(collect_text(await wait_prompt(job, comfy, job.prompt_ids[-1], graph)).split())
    if not text.endswith(('。', '.', '！', '!')):
        text += '。'
    cache.write_text(json.dumps({'key': key, 'text': text}, ensure_ascii=False), 'utf-8')
    return text


class DescribeJob(Job):
    """反推提示词：看一张图（input 下的相对路径）写一段描述。不进历史记录，网页轮询 /api/jobs/<id> 拿进度和结果。"""

    def __init__(self, ref):
        super().__init__({'kind': 'describe', 'ref': ref})
        self.text = ''    # 最终结果（扩写过的，或者只是看图写的描述）
        self.draft = ''   # 第一步看图写出的描述（已删掉签名之类的句子）
        self.facts = ''   # 先核实的镜头和姿势
        self.note = ''    # 给用户的补充说明，比如扩写失败、用的是第一步的描述
        self.aspect = ''  # 原图最接近的比例，用来按原图比例重画

    def public(self):
        return {**super().public(), 'text': self.text, 'draft': self.draft, 'facts': self.facts, 'note': self.note, 'aspect': self.aspect}


async def run_describe(job, comfy):
    try:
        cfg = load_config()
        importlib.reload(pipeline)
        job.phase = '启动 ComfyUI（首次约半分钟）' if not await comfy.alive() else '提交任务'
        await comfy.ensure_running()
        with Image.open(INPUT / job.req['ref']) as image:
            size = image.size
        job.aspect = pipeline.nearest_aspect(*size, cfg)
        seed = random.randrange(2 ** 32)  # 每次都换种子，不满意可以再反推一次

        async def run(graph):
            job.prompt_ids.append(await comfy.queue(graph))
            return collect_text(await wait_prompt(job, comfy, job.prompt_ids[-1], graph))

        # 先单独核实镜头和姿势；再只看整图写整体描述（带上核实过的事实）；最后看整图 + 放大的局部，只列细节清单
        # （见 config.toml [describe] 的说明）
        facts = ''
        if cfg['describe'].get('check'):
            facts = job.facts = pipeline.check_answers(await run(pipeline.build_describe(job.req['ref'], size, cfg, seed=seed, check=True)), cfg)
        description = pipeline.clean_description(await run(pipeline.build_describe(job.req['ref'], size, cfg, seed=seed, facts=facts)), cfg)
        details = []
        if cfg['describe'].get('zoom'):
            details = pipeline.clean_details(await run(pipeline.build_describe(job.req['ref'], size, cfg, seed=seed, details=True)), cfg)
        job.draft = job.text = pipeline.merge_details(description, details, cfg, brief=False)
        expand = cfg.get('expand', {})
        if expand.get('enabled'):
            system = ROOT / expand['system_file']
            if not (ROOT / 'models' / 'text_encoders' / expand['model']).exists() or not system.exists():
                job.note = '没找到扩写模型，用的是看图写出的描述。'
            else:
                brief = pipeline.merge_details(description, details, cfg, brief=True)
                rewritten = pipeline.parse_expand(await run(pipeline.build_expand(brief, system.read_text('utf-8'), cfg, seed=seed)))
                if rewritten:
                    job.text = pipeline.clean_description(rewritten, cfg)
                else:
                    job.note = '扩写模型没有给出结果（可能思考太长被截断），用的是看图写出的描述。'
        job.status = 'done'
    except Cancelled:
        job.status = 'cancelled'
    except Exception as error:
        job.status = 'error'
        job.error = str(error) or type(error).__name__
    finally:
        job.finished = time.time()


async def wait_prompt(job, comfy, prompt_id, graph):
    """等一个 prompt 跑完，期间更新 job 的阶段和步数；返回 ComfyUI 历史里这个 prompt 的记录（交给 collect_images / collect_text）。

    ComfyUI 加载大模型时 HTTP 可能好几秒不响应，所以不响应先当作「忙」继续等：
    - 连接被拒绝且进程没了，或队列和历史里都找不到这个任务，持续 LOST_AFTER 秒 → 丢失；
    - 进程还在，但请求一直超时或被拒绝，持续 STUCK_AFTER 秒 → 卡死（见 CLAUDE.md「已知问题」），自动关掉 ComfyUI。"""
    missing_since = silent_since = None
    while True:
        if job.cancelled:
            raise Cancelled
        found = answered = True
        try:
            entry = await comfy.history(prompt_id)
            if entry:
                check_status(entry)
                return entry
            state = comfy.progress.get(prompt_id)
            if state:
                job.phase = graph.get(state['node'], {}).get('_meta', {}).get('title', '处理中')
                job.step, job.total = state['value'], state['max']
            else:
                running, pending = await comfy.queue_ids()
                job.step = job.total = 0
                if prompt_id in running:
                    job.phase = '处理中'
                elif prompt_id in pending:
                    job.phase = f'排队中，前面还有 {len(running) + pending.index(prompt_id)} 个任务'
                else:
                    found = False
        except asyncio.TimeoutError:
            answered = False
        except UNREACHABLE:
            if comfy.process_alive():  # 进程还在却拒绝连接：网页服务线程被堵住了
                answered = False
            else:
                found = False
                job.phase = '连不上 ComfyUI，重试中'
        now = time.monotonic()
        missing_since = None if found else missing_since or now
        silent_since = None if answered else silent_since or now
        if silent_since:
            silent = now - silent_since
            job.step = job.total = 0
            job.phase = 'ComfyUI 正忙（多半在加载模型），请稍等' if silent < 30 else f'ComfyUI 已经 {silent:.0f} 秒没有响应，可能卡住了'
            if silent > STUCK_AFTER:  # 卡死了不会自己恢复，关掉它，下次生成时会自动重新启动
                code, _ = await comfy.stop()
                raise RuntimeError(f'ComfyUI 卡住了：进程还在，但已经 {silent:.0f} 秒没有响应（多半是显存不够，加载模型时卡死）。' + (
                    '已自动关闭它，重新生成即可（会自动重新启动）。' if code == 0 and not comfy.process_alive() else
                    '自动关闭失败，请在网页右上角菜单里点「关闭 ComfyUI（释放显存）」，或运行 scripts/launch.py stop，再重新生成。'))
        if missing_since and now - missing_since > LOST_AFTER:
            raise RuntimeError('任务在 ComfyUI 中丢失了：ComfyUI 可能被关闭或重启，或在原界面清空了队列。')
        await asyncio.sleep(0.6)


def check_status(entry):
    status = entry.get('status') or {}
    if status.get('status_str') == 'error':
        for kind, data in status.get('messages', []):
            if kind == 'execution_interrupted':
                raise Cancelled
            if kind == 'execution_error':
                raise RuntimeError(f"{data.get('node_type')} 出错：{data.get('exception_message', '').strip()}")
        raise RuntimeError('ComfyUI 报告生成失败。')


def collect_images(entry):
    """生成的图片（output 下的相对路径）。"""
    return [str(PurePosixPath(image['subfolder'].replace('\\', '/'), image['filename']))
            for output in entry.get('outputs', {}).values()
            for image in output.get('images', []) if image.get('type') == 'output']


def collect_text(entry):
    """PreviewAny 节点显示的文字（反推提示词的结果）。"""
    texts = [text for output in entry.get('outputs', {}).values() for text in output.get('text', [])]
    if not texts or not texts[0].strip():
        raise RuntimeError('模型没有写出文字。')
    return texts[0].strip()


def append_history(record):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def read_history():
    if not HISTORY.exists():
        return []
    return [json.loads(line) for line in HISTORY.read_text('utf-8').splitlines() if line.strip()]


def remove_history(job_id):
    """只删记录，图片文件保留在 output/studio/。"""
    records = [r for r in read_history() if r['id'] != job_id]
    HISTORY.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), 'utf-8')
