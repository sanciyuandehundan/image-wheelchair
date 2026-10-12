"""一次生图请求 → ComfyUI API 节点图。

想改生成方式（换模型、加 LoRA、换采样器、加放大、改尺寸规则……）就改这个文件。
每次生成前都会重新加载它，保存后下一次生成立即生效，不用重启。
可用的节点和参数见 logs/object-info.json（ComfyUI /object_info 的快照）。
"""
import json
import math
import re


def canvas_size(aspect, side):
    """按 'W:H' 比例算出总像素约 side×side 的宽高，取 32 的倍数。"""
    w, h = map(int, aspect.split(':'))
    return (round(math.sqrt(side * side * w / h) / 32) * 32,
            round(math.sqrt(side * side * h / w) / 32) * 32)


def preset(cfg, req):
    """请求用到的模式配置（config.toml 的 [modes.*]）；勾了分支时用 toggle 里的项覆盖。没选模式返回 {}。"""
    if not req.get('mode'):
        return {}
    mode = cfg['modes'][req['mode']]
    return {**mode, **mode['toggle']} if req.get('toggle') else mode


def fill(fields, values):
    """按字段的 render 把填写的内容渲染成句子：同一 group 的相邻字段用「，」连起来，句末加「。」。"""
    sentences, last = [], None
    for field in fields:
        value = values.get(field['key'], '').rstrip('。，,.；;、 ')
        if not value or 'render' not in field:
            continue
        if field.get('type') == 'ref':
            value = f'<image{value}>'
        text = field['render'].format(value)
        group = field.get('group', field['key'])
        if sentences and group == last:
            sentences[-1] += '，' + text
        else:
            sentences.append(text)
        last = group
    return ''.join(s + '。' for s in sentences)


def number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compare_heights(persons, spec):
    """填了身高的人不少于两个时，把厘米数换成看得见的高矮比较（config 的 height_compare）：
    以最高的人为准，身高差按最高者的头长（身高 / 头身比）折算，套进对应的句子。"""
    rules = spec.get('height_compare')
    known = [(i, number(p.get('height')), number(p.get('heads'), 7.5)) for i, p in enumerate(persons, 1)]
    known = [k for k in known if k[1]]
    if not rules or len(known) < 2:
        return ''
    tall, tall_height, tall_heads = max(known, key=lambda k: k[1])
    head = tall_height / tall_heads
    label = lambda i: persons[i - 1].get('name') or f'人物{i}'
    parts = [next(text for limit, text in rules if (tall_height - height) / head < limit).format(short=label(i), tall=label(tall))
             for i, height, _ in known if i != tall]
    return '，'.join(parts) + '。'


def people_text(people, cfg):
    """人物设定（jobs.make_people 校验过的 {'scene': {...}, 'persons': [{...}]}）→ 人物描述，规则见 config.toml 的 [people]。"""
    spec = cfg['people']
    persons, scene = people['persons'], people['scene']
    n = len(persons)
    count = spec['count_one'] if n == 1 else spec['count_many'].format('两三四五六七八九十'[n - 2] if n <= 10 else n)
    lines = [count + fill([f for f in spec['scene'] if not f.get('after')], scene)]
    for i, person in enumerate(persons, 1):
        title = (spec['title_named'] if person.get('name') else spec['title']).format(n=i, name=person.get('name'))
        body = fill(spec['person'], person)
        if body or person.get('name'):
            lines.append(f'{title}：{body}' if body else f'{title}。')
    lines += [compare_heights(persons, spec), fill([f for f in spec['scene'] if f.get('after')], scene), spec['anatomy']]
    return '\n'.join(line for line in lines if line)


def marks_text(req, mode):
    """画板上各颜色代表什么（req['marks']：[{color, kind, text, where}]）按模式的 mark_line 渲染，用「；」连成一句套进 marks。
    kind 是这个颜色主要用什么画的：line（线条）/ circle（圈）/ paint（涂抹）；where 是草图生图时这种颜色的笔画在画面哪里
    （jobs.sketch_lines 算的，没找到这种颜色时写「草图里」）。没写含义的颜色不写进去。"""
    lines = [mode['mark_line'][m['kind']].format(color=m['color'], text=m['text'], where=m.get('where') or '草图里')
             for m in req.get('marks') or [] if m['text']]
    return mode['marks'].format('；'.join(lines)) if lines else ''


def full_prompt(req, cfg):
    """实际送进模型的提示词：模式模板 + 用户写的 + 人物描述，勾了透明背景再包一层。"""
    prompt = req['prompt']
    mode = preset(cfg, req)
    if mode:  # 预设模式：用户写的只是补充要求，接在模板后面
        template = mode['template'].strip()
        if mode.get('outpaint'):  # 扩图：往哪边扩、扩成什么比例由程序算好填进去
            template = template.format(direction=mode['directions'][outpaint_layout(req, cfg)['direction']], aspect=req['aspect'])
        if 'mark_line' in mode:  # 画板模式：各颜色的含义填进 {marks}
            template = template.replace('{marks}', marks_text(req, mode))
        prompt = template + (mode['extra'].format(prompt) if prompt else '')
    if req.get('people'):
        prompt = '\n\n'.join(part for part in (prompt, people_text(req['people'], cfg)) if part)
    if req.get('style_text'):  # 「画风」选项：参考图的画法（jobs.style_text 看图写的），按 [style] position 放在最前或最后
        sentence = cfg['style']['sentence'].format(req['style_text'])
        parts = (sentence, prompt) if cfg['style'].get('position', 'start') == 'start' else (prompt, sentence)
        prompt = '\n'.join(part for part in parts if part)
    if req['transparent']:
        prompt = cfg['transparent']['prefix'] + prompt + cfg['transparent']['suffix']
    return prompt


def outpaint_layout(req, cfg):
    """扩图：图1（原图尺寸 req['ref_size']）放进目标比例的画布（按画质档位）后的位置。
    返回画布宽高 width/height、原图缩放后的宽高 inner、四边要扩的像素 left/top/right/bottom（8 的倍数），
    以及往哪边扩 direction（config 里扩图模式 directions 的键）。anchor：原图贴在画布的哪边，center 是居中。"""
    width, height = canvas_size(req['aspect'], cfg['size']['quality'][req['quality']])
    w0, h0 = req['ref_size']
    scale = min(width / w0, height / h0)
    pad_x, pad_y = 8 * round((width - w0 * scale) / 8), 8 * round((height - h0 * scale) / 8)
    anchor = req.get('anchor') or 'center'

    def split(extra, start, end):  # 原图贴在 start 边就只往 end 边扩，反之亦然；居中时两边平分
        if anchor == start:
            return 0, extra
        if anchor == end:
            return extra, 0
        first = 8 * (extra // 16)
        return first, extra - first

    left, right = split(pad_x, 'left', 'right')
    top, bottom = split(pad_y, 'top', 'bottom')
    if pad_x >= pad_y:
        direction = 'right' if left == 0 else 'left' if right == 0 else 'sides'
    else:
        direction = 'down' if top == 0 else 'up' if bottom == 0 else 'vertical'
    return dict(width=width, height=height, inner=(width - pad_x, height - pad_y),
                left=left, top=top, right=right, bottom=bottom, direction=direction)


def guidance(req, cfg):
    """(CFG, 负面提示词)。勾了人物设定的「严格手脚」时加上 [people] 的负面提示词并调高 CFG；cfg = 1 时负面提示词不起作用。"""
    sampling = cfg['sampling']
    if req.get('people') and req['people'].get('strict'):
        negative = '，'.join(part for part in (sampling['negative_prompt'], cfg['people']['negative']) if part)
        return max(sampling['cfg'], cfg['people']['strict_cfg']), negative
    return sampling['cfg'], sampling['negative_prompt']


def node(class_type, title, **inputs):
    # title 会显示在网页进度条上，表示当前阶段
    return {'class_type': class_type, 'inputs': inputs, '_meta': {'title': title}}


def clip_loader(cfg):
    # 生成和反推提示词用同一个节点和参数，ComfyUI 会复用已经加载好的文本编码器
    return node('CLIPLoader', '读取模型', clip_name=cfg['models']['clip'], type='qwen_image',
                device=cfg['models'].get('clip_device', 'default'))


def build(req, cfg, filename_prefix):
    """req: prompt, refs（input 目录下的相对路径，第 1 张就是 <image1>）, aspect（'auto' 或 'W:H'）,
    quality（config 里 [size.quality] 的键）, seed, steps, transparent,
    mode（config 里 [modes] 的键，'' = 自由描述）, toggle（是否勾选模式的分支）, people（人物设定，None = 没用）。"""
    side = cfg['size']['quality'][req['quality']]
    sampling = cfg['sampling']
    prompt = full_prompt(req, cfg)
    cfg_scale, negative = guidance(req, cfg)
    # 有参考图且比例选「自动」= 编辑模式：画布跟随图1；否则按比例新建画布
    follow_ref = bool(req['refs']) and req['aspect'] == 'auto'

    graph = {
        'unet': node('UNETLoader', '读取模型', unet_name=cfg['models']['unet'], weight_dtype='default'),
        'model': node('QwenImage21Cache', '读取模型', model=['unet', 0], **cfg['kv_cache']),
        'clip': clip_loader(cfg),
        'vae': node('VAELoader', '读取模型', vae_name=cfg['models']['vae']),
        'encode': node('TextEncodeQwenImage21', '理解提示词和参考图' if req['refs'] else '理解提示词', clip=['clip', 0], vae=['vae', 0],
                       prompt=prompt, negative_prompt=negative,
                       resolution=side if follow_ref else cfg['size']['reference_resolution']),
        'sample': node('KSampler', '绘制', model=['model', 0], positive=['encode', 0], negative=['encode', 1],
                       latent_image=['encode', 2], seed=req['seed'], steps=req['steps'], cfg=cfg_scale,
                       sampler_name=sampling['sampler'], scheduler=sampling['scheduler'], denoise=1.0),
        'decode': node('VAEDecode', '解码', samples=['sample', 0], vae=['vae', 0]),
        'save': node('SaveImage', '保存', images=['decode', 0], filename_prefix=filename_prefix),
    }
    for i, ref in enumerate(req['refs'], 1):
        if i == 1 and req.get('sketch_line'):  # 草图生图：模型看的是程序转好的黑线稿（jobs.sketch_lines）
            ref = req['sketch_line']
        graph[f'ref{i}'] = node('LoadImage', f'读取参考图{i}', image=ref)
        graph['encode']['inputs'][f'images.image_{i}'] = [f'ref{i}', 0]
    if not follow_ref:
        width, height = canvas_size('1:1' if req['aspect'] == 'auto' else req['aspect'], side)
        graph['canvas'] = node('EmptyLatentImage', '新建画布', width=width, height=height, batch_size=1)
        graph['sample']['inputs']['latent_image'] = ['canvas', 0]
    image = ['decode', 0]
    mode = preset(cfg, req)
    inpaint = mode.get('outpaint') and mode.get('method') == 'inpaint'
    if inpaint or req.get('upscale'):
        # VAE 解出来的总是 RGBA（模型原生支持透明），超分模型和合成只吃 RGB：先拆掉透明通道，透明背景时最后再合回去
        graph['rgb'] = node('SplitImageWithAlpha', '整理图片', image=image)
        image = ['rgb', 0]
    if inpaint:
        image = add_outpaint_inpaint(graph, req, cfg, mode, image)
    if req.get('upscale'):
        image = add_upscale(graph, image, req, cfg)
    if 'rgb' in graph and req['transparent']:
        graph['alpha'] = node('JoinImageWithAlpha', '整理图片', image=image, alpha=['rgb', 1])  # 透明通道自动缩放到最终尺寸
        image = ['alpha', 0]
    graph['save']['inputs']['images'] = image
    return graph


def add_outpaint_inpaint(graph, req, cfg, mode, image):
    """扩图（method = "inpaint"）：原图缩放后贴进画布，只重画四周新扩的部分（蒙版重绘 + 差分扩散，接缝羽化），
    最后把原图像素原样贴回去。图1 仍作为参考图送进模型，让它知道原图画的是什么。"""
    lay = outpaint_layout(req, cfg)
    graph.pop('canvas', None)
    graph['op_scale'] = node('ImageScale', '准备扩图', image=['ref1', 0], upscale_method='lanczos',
                             width=lay['inner'][0], height=lay['inner'][1], crop='disabled')
    graph['op_pad'] = node('ImagePadForOutpaint', '准备扩图', image=['op_scale', 0], left=lay['left'], top=lay['top'],
                           right=lay['right'], bottom=lay['bottom'], feathering=mode.get('feathering', 48))
    graph['op_latent'] = node('VAEEncode', '准备扩图', pixels=['op_pad', 0], vae=['vae', 0])
    graph['op_mask'] = node('SetLatentNoiseMask', '准备扩图', samples=['op_latent', 0], mask=['op_pad', 1])
    graph['op_model'] = node('DifferentialDiffusion', '读取模型', model=['model', 0])
    graph['sample']['inputs'].update(latent_image=['op_mask', 0], model=['op_model', 0])
    graph['op_keep'] = node('InvertMask', '合成', mask=['op_pad', 1])
    graph['op_paste'] = node('ImageCompositeMasked', '合成', destination=image, source=['op_pad', 0],
                             x=0, y=0, resize_source=False, mask=['op_keep', 0])
    return ['op_paste', 0]


def add_upscale(graph, image, req, cfg):
    """超分到 req['upscale'] 长边：先用超分模型放大；勾了重绘（req['repaint']）就缩到不超过 repaint_max_side，
    用生图模型按原提示词低幅度重画补细节，目标更大时再用超分模型放大一次；最后缩到目标长边。"""
    u = cfg['upscale']
    target = req['upscale']
    graph['up_model'] = node('UpscaleModelLoader', '读取超分模型', model_name=u['model'])
    graph['up'] = node('ImageUpscaleWithModel', '超分放大', upscale_model=['up_model', 0], image=image)
    image = ['up', 0]
    if req.get('repaint'):
        side = min(target, u['repaint_max_side'])
        first = graph['sample']['inputs']
        graph['rp_image'] = node('ImageScaleToMaxDimension', '重绘细节', image=image, upscale_method='lanczos', largest_size=side)
        graph['rp_latent'] = node('VAEEncode', '重绘细节', pixels=['rp_image', 0], vae=['vae', 0])
        graph['rp_sample'] = node('KSampler', '重绘细节', model=['model', 0], positive=first['positive'], negative=first['negative'],
                                  latent_image=['rp_latent', 0], seed=first['seed'], steps=first['steps'], cfg=first['cfg'],
                                  sampler_name=first['sampler_name'], scheduler=first['scheduler'], denoise=u['repaint_denoise'])
        graph['rp_decode'] = node('VAEDecode', '重绘细节', samples=['rp_sample', 0], vae=['vae', 0])
        graph['rp_rgb'] = node('SplitImageWithAlpha', '重绘细节', image=['rp_decode', 0])  # 解码出来又是 RGBA
        image = ['rp_rgb', 0]
        if target > side:
            graph['up2'] = node('ImageUpscaleWithModel', '超分放大', upscale_model=['up_model', 0], image=image)
            image = ['up2', 0]
    graph['up_final'] = node('ImageScaleToMaxDimension', '超分放大', image=image, upscale_method='lanczos', largest_size=target)
    return ['up_final', 0]


def nearest_aspect(width, height, cfg):
    """config 的比例列表里最接近 width:height 的一个（反推时建议用原图的比例重画）。"""
    return min(cfg['size']['aspects'], key=lambda a: abs(math.log(width / height) - math.log(eval(a.replace(':', '/')))))


ZOOM_PARTS = ('upper-left', 'upper-right', 'lower-left', 'lower-right')


def fit_pixels(width, height, megapixels):
    """按 width:height 算总像素约 megapixels 百万像素的宽高，取 32 的倍数。"""
    scale = math.sqrt(megapixels * 1e6 / (width * height))
    return max(32, round(width * scale / 32) * 32), max(32, round(height * scale / 32) * 32)


def textgen(title, clip, chat, max_tokens, thinking, image=None, **sampling):
    inputs = dict(clip=clip, prompt=chat, max_length=max_tokens, thinking=thinking, use_default_template=False, mtp='off',
                  sampling_mode='on', **{f'sampling_mode.{k}': v for k, v in sampling.items()})
    if image:
        inputs['image'] = image
    return node('TextGenerate', title, **inputs)


def build_describe(image, size, cfg, seed=0, details=False, check=False, facts=''):
    """反推提示词第一步：image（input 目录下的相对路径，原图宽高 size）→ 节点图，写出的文字在 'show' 节点的 text 里。
    默认：只看整张图（缩到约 megapixels 百万像素），按 system 写整体描述；facts 是先核实过的镜头和姿势，放在要求前面。
    check=True：只看整张图，按 check_system / check_user 回答镜头和每个人的姿势（哪些部位着地）。
    details=True：整图加上四块互相重叠、放大到同样大小的局部，按 details_system / details_user 只列细节清单（局部放大看细节）。
    原图比目标还小时用最近邻放大，像素画的颗粒不会糊。用 Qwen3-VL 自己的对话格式拼好整段文字，
    绕开 TextGenerate 默认套用的、为生成准备的系统提示词。"""
    d = cfg['describe']
    width, height = size
    tw, th = fit_pixels(width, height, d['megapixels'])
    method = 'nearest-exact' if tw > width else 'lanczos'
    graph = {
        'clip': clip_loader(cfg),
        'image': node('LoadImage', '读取图片', image=image),
        'whole': node('ImageScale', '读取图片', image=['image', 0], upscale_method=method, width=tw, height=th, crop='disabled'),
    }
    batch, labels = ['whole', 0], ['the whole picture']
    if details:
        cw, ch = round(width * d['zoom']), round(height * d['zoom'])
        for i, part in enumerate(ZOOM_PARTS):
            graph[f'crop{i}'] = node('ImageCrop', '读取图片', image=['image', 0], width=cw, height=ch,
                                     x=0 if 'left' in part else width - cw, y=0 if 'upper' in part else height - ch)
            graph[f'zoom{i}'] = node('ImageScale', '读取图片', image=[f'crop{i}', 0], upscale_method=method, width=tw, height=th, crop='disabled')
            graph[f'batch{i}'] = node('ImageBatch', '读取图片', image1=batch, image2=[f'zoom{i}', 0])
            batch = [f'batch{i}', 0]
            labels.append(f'an enlarged crop of the {part} part')
    vision = '<|vision_start|><|image_pad|><|vision_end|>'
    if details:
        vision = ''.join(f'Image {n} ({label}): {vision}\n' for n, label in enumerate(labels, 1))
        system, user, max_tokens, title = d['details_system'], vision + d['details_user'], d['details_max_tokens'], '放大看细节'
    elif check:
        question = d['check_user'].format(camera=choices(d['check_camera']), pose=choices(d['check_pose']))
        system, user, max_tokens, title = d['check_system'], vision + question, d['check_max_tokens'], '核实镜头和姿势'
    else:
        system = d['system'].strip().replace('{language}', d['language'])
        facts = d['check_facts'].format(facts=facts) if facts else ''
        user, max_tokens, title = vision + facts + d['user'], d['max_tokens'], '看图写提示词'
    chat = (f'<|im_start|>system\n{system}<|im_end|>\n'
            f'<|im_start|>user\n{user}<|im_end|>\n'
            f'<|im_start|>assistant\n')
    graph['describe'] = textgen(title, ['clip', 0], chat, max_tokens, False, image=batch,
                                temperature=d['temperature'], top_k=d['top_k'], top_p=d['top_p'], min_p=0.0,
                                repetition_penalty=d['repetition_penalty'], seed=seed)
    graph['show'] = node('PreviewAny', '整理结果', source=['describe', 0])
    return graph


def build_style(image, size, cfg, seed=0):
    """「画风」选项：让文本编码器看参考图（缩到约 megapixels 百万像素），按 [style] 的 system / user 只描述画法。
    写出的文字在 'show' 节点里（jobs.collect_text 取），由 jobs.style_text 缓存。"""
    s = cfg['style']
    tw, th = fit_pixels(*size, s['megapixels'])
    chat = (f"<|im_start|>system\n{s['system'].strip()}<|im_end|>\n"
            f"<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>{s['user']}<|im_end|>\n"
            f'<|im_start|>assistant\n')
    return {
        'clip': clip_loader(cfg),
        'image': node('LoadImage', '分析画风', image=image),
        'whole': node('ImageScale', '分析画风', image=['image', 0], upscale_method='lanczos', width=tw, height=th, crop='disabled'),
        'describe': textgen('分析画风', ['clip', 0], chat, s['max_tokens'], False, image=['whole', 0],
                            temperature=s['temperature'], top_k=s['top_k'], top_p=s['top_p'], min_p=0.0,
                            repetition_penalty=s['repetition_penalty'], seed=seed),
        'show': node('PreviewAny', '分析画风', source=['describe', 0]),
    }


def choices(options):
    return '; '.join(f'{chr(65 + i)}) {text}' for i, text in enumerate(options))


def check_answers(answer, cfg):
    """核实那一遍的回答 → 写描述用的事实：「Camera: B」「Person 1 (pink): F) …」里的字母换回选项原文（模型有时只答字母）。"""
    d = cfg['describe']
    lines = []
    for line in answer.splitlines():
        m = re.match(r'\s*(Camera|Person\s*\d+[^:]*)\s*:\s*\(?([A-Z])\b\)?[.:]?\s*(.*)', line.strip(), re.I)
        if not m:
            if line.strip() and not line.strip().lower().startswith('framing'):
                lines.append(line.strip())
            continue
        label, letter, rest = m.groups()
        options = d['check_camera'] if label.lower() == 'camera' else d['check_pose']
        index = ord(letter.upper()) - 65
        if not 0 <= index < len(options):
            lines.append(line.strip())
            continue
        text = options[index]
        rest = rest.strip()
        if rest.lower().startswith(text.lower()[:12]):  # 模型又抄了一遍选项（常有小改动）：去掉到第一个分号为止
            rest = rest.split(';', 1)[1] if ';' in rest else ''
        rest = rest.strip(' .;,')
        lines.append(f'{label}: {text}' + (f'; {rest}' if rest else '') + '.')
    return '\n'.join(lines)


def clean_description(text, cfg):
    """整句删掉提到签名、水印、账号、网址的句子（config 的 [describe] drop）。模型常不听「不要写签名」，就用程序删。"""
    sentences = re.split(r'(?<=[.!?。！？])\s*', text.strip())
    return ' '.join(s for s in sentences if s and not re.search(cfg['describe']['drop'], s)).strip()


def clean_details(text, cfg):
    """细节清单 → 一条条短语：去掉行首的「- 」「1.」之类，删掉提到签名、水印、账号的行。"""
    items = (re.sub(r'^\s*(?:[-*•·]|\d+[.)、])\s*', '', line).strip().rstrip('.。;；') for line in text.splitlines())
    return [item for item in items if item and not re.search(cfg['describe']['drop'], item)]


def merge_details(description, details, cfg, brief):
    """把整体描述和细节清单合成一段：brief=True 时用 details_brief（交给扩写模型），否则用 details_join（直接拿去生成）。"""
    if not details:
        return description
    d = cfg['describe']
    if brief:
        return d['details_brief'].format(description=description, details='\n'.join(f'- {item}' for item in details))
    return d['details_join'].format(description=description, details='; '.join(details))


def build_expand(text, system, cfg, seed=0):
    """反推提示词第二步：用官方提示词改写模型 PE-T2I 把第一步的描述改写成官方格式（开启思考，官方要求）。
    system 是官方 system_prompt.txt 的内容，原样使用。"""
    e = cfg['expand']
    chat = (f'<|im_start|>system\n{system.strip()}<|im_end|>\n'
            f'<|im_start|>user\n{text}<|im_end|>\n'
            f'<|im_start|>assistant\n<think>\n')
    return {
        'clip': node('CLIPLoader', '读取扩写模型', clip_name=e['model'], type='qwen_image', device='default'),
        'expand': textgen('扩写成官方格式', ['clip', 0], chat, e['max_tokens'], True,
                          temperature=e['temperature'], top_k=e['top_k'], top_p=e['top_p'], min_p=0.0,
                          repetition_penalty=1.0, presence_penalty=e['presence_penalty'], seed=seed),
        'show': node('PreviewAny', '整理结果', source=['expand', 0]),
    }


def parse_expand(output):
    """从 PE-T2I 的输出（思考 + 一行 JSON）里取 rewritten_prompt；取不到返回 None。"""
    starts = [m.start() for m in re.finditer(r'\{\s*"rewritten_prompt"', output)]
    for start in reversed(starts):
        try:
            data, _ = json.JSONDecoder().raw_decode(output[start:])
            if isinstance(data, dict) and str(data.get('rewritten_prompt', '')).strip():
                return data['rewritten_prompt'].strip()
        except ValueError:
            continue
    return None
