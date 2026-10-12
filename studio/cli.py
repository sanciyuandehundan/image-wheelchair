"""命令行生图（给 Claude 或脚本用）。和网页共用同一套逻辑，结果也会出现在网页的历史里。

  python -X utf8 -s studio/cli.py "把<image1>的背景换成海边日落" -i photo.jpg
  python -X utf8 -s studio/cli.py "赛博朋克城市夜景，霓虹招牌" --aspect 16:9 --quality 2k -n 2
  python -X utf8 -s studio/cli.py "" -i 底图.png -i 角色.png --mode swap_character   把图2角色的发型发色饰品换到图1角色身上
  python -X utf8 -s studio/cli.py "" -i 底图.png -i 角色.png --mode swap_character --toggle   同上，服装也一起换
  python -X utf8 -s studio/cli.py "两人在公园散步" --aspect 3:4 --people people.json   人物设定（格式见 --people 的说明）
  python -X utf8 -s studio/cli.py "……" --upscale 4096 [--repaint]   生成后超分到长边 4096；--repaint 再用生图模型重绘补细节
  python -X utf8 -s studio/cli.py "两边是海边沙滩" -i 原图.png --mode outpaint --aspect 16:9 [--anchor left]   扩图成 16:9
  python -X utf8 -s studio/cli.py "" -i 角色.png --mode turnaround [--toggle] --quality 2k   四视图（正面/左侧/背面/右侧，默认 4:3）；--toggle 加脸部特写（16:9）
  python -X utf8 -s studio/cli.py "黄昏的公园，动漫插画" -i 草图.png --mode sketch --marks '[{"color":"红色","text":"穿白裙的少女"}]'   草图生图
  python -X utf8 -s studio/cli.py "" -i 标注图.png --mode mark --marks '[{"color":"红色","kind":"circle","text":"把头发改成黑色"}]'   圈选/涂抹编辑
  python -X utf8 -s studio/cli.py "……" -i a.png --dry-run     只打印要提交的 ComfyUI 节点图，不生成
  python -X utf8 -s studio/cli.py --describe 图.png           反推提示词：看图写一段适合本模型的描述（最后一段是结果）
  python -X utf8 -s studio/cli.py "……" --people p.json --show-prompt   只打印实际送进模型的提示词

成功时最后几行是生成图片的绝对路径。
"""
import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jobs
import pipeline
from comfy_client import Comfy


async def describe(path, cfg):
    comfy = Comfy(cfg['server']['comfy_url'])
    job = jobs.DescribeJob(jobs.save_reference(Path(path).read_bytes(), cfg))
    task = asyncio.create_task(jobs.run_describe(job, comfy))
    shown = None
    try:
        while not task.done():
            line = job.phase + (f' {job.step}/{job.total}' if job.total else '')
            if line != shown and (not job.total or job.step % 100 == 0):
                print(line, flush=True)
                shown = line
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:  # Ctrl+C
        await job.cancel(comfy)
        await task
    finally:
        await comfy.close()
    if job.status != 'done':
        print(f'失败：{job.error}' if job.error else '已取消。', file=sys.stderr)
        return 1
    print(f'用时 {job.finished - job.created:.0f} 秒，原图最接近的比例 {job.aspect}' + (f'（{job.note}）' if job.note else '') + '\n')
    if job.facts:
        print(f'【核实的镜头和姿势】\n{job.facts}\n')
    if job.draft != job.text:
        print(f'【看图写出的描述】\n{job.draft}\n\n【扩写后（最终结果）】')
    print(job.text)
    return 0


async def generate(req, cfg):
    comfy = Comfy(cfg['server']['comfy_url'])
    job = jobs.Job(req)
    task = asyncio.create_task(jobs.run_job(job, comfy))
    shown = None
    try:
        while not task.done():
            line = f'[{job.index + 1}/{req["count"]}] {job.phase}' + (f' {job.step}/{job.total}' if job.total else '')
            if line != shown:
                print(line, flush=True)
                shown = line
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:  # Ctrl+C
        await job.cancel(comfy)
        await task
    finally:
        await comfy.close()
    if job.status == 'error':
        print(f'失败：{job.error}', file=sys.stderr)
    elif job.status == 'cancelled':
        print('已取消。', file=sys.stderr)
    if job.req.get('style_text'):
        print(f'【画风】{job.req["style_text"]}')
    print(f'种子 {req["seed"]}，用时 {job.finished - job.created:.0f} 秒')
    for image in job.images:
        print(jobs.OUTPUT / image['file'])
    return 0 if job.status == 'done' else 1


def main():
    cfg = jobs.load_config()
    modes = cfg.get('modes', {})
    parser = argparse.ArgumentParser(description='Qwen-Image-2.1 生图轮椅（命令行）')
    parser.add_argument('prompt', nargs='?', default='', help='自然语言描述；参考图按 -i 的顺序是 <image1>、<image2>…（也可以写「图1」）；用了 --mode 时是补充要求，可以留空 ""')
    parser.add_argument('-i', '--image', action='append', default=[], help='参考图路径，可重复；第一张是编辑对象')
    parser.add_argument('--aspect', default='auto', help="auto（有参考图时跟随图1，否则 1:1；扩图、四视图用模式的默认比例）或 3:4、16:9 等")
    parser.add_argument('--quality', help='config.toml 里 [size.quality] 的档位，如 1k / 1.5k / 2k')
    parser.add_argument('-n', '--count', type=int, default=1)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--steps', type=int)
    parser.add_argument('--transparent', action='store_true', help='透明背景（输出 RGBA PNG）')
    parser.add_argument('--upscale', type=int, default=0, help='生成后超分到这个长边：' + ' / '.join(map(str, cfg['upscale']['targets'])) + '；不写 = 原图')
    parser.add_argument('--repaint', action='store_true', help='超分时用生图模型低幅度重绘补细节（慢）；不写 = 只用超分模型')
    parser.add_argument('--anchor', default='center', choices=['center', 'left', 'right', 'top', 'bottom'],
                        help='扩图模式（--mode outpaint）里原图贴在新画布的哪边，默认居中')
    parser.add_argument('--mode', choices=list(modes), help='config.toml 里的预设模式：' + '，'.join(f"{k} = {v['label']}" for k, v in modes.items()))
    parser.add_argument('--toggle', action='store_true', help='勾选模式的分支：' + '，'.join(
        f"{k} = {v['toggle']['checkbox']}" for k, v in modes.items() if 'toggle' in v))
    parser.add_argument('--people', metavar='JSON', help='人物设定文件：{"scene": {"shot": "…"}, "persons": [{"name": "艾莉", "pose": "站立", "left": "叉腰"}, …]}'
                        '，或只写 persons 列表；字段名是 config.toml [people] 里的 key，"ref": 2 表示长相参照图2；'
                        '加 "strict": true = 严格手脚（负面提示词 + 调高 CFG，慢一倍）')
    parser.add_argument('--style', type=int, default=0, metavar='N', help='画风照第 N 张参考图：生成前先看这张图写出画法，要求人物和背景用同一种画风（见 config.toml [style]）')
    parser.add_argument('--marks', metavar='JSON', help='画板模式（--mode sketch / mark）里各颜色代表什么：'
                        '[{"color": "红色", "kind": "circle", "text": "把头发改成黑色"}, …]，color 是 config.toml [board] 里的颜色名，'
                        'kind = line（线条）/ circle（圈）/ paint（涂抹）；可以是 JSON 文件路径')
    parser.add_argument('--describe', metavar='IMAGE', help='反推提示词：看这张图写一段适合本模型的描述，不生成图片（写法见 config.toml [describe]）')
    parser.add_argument('--dry-run', action='store_true', help='只打印节点图')
    parser.add_argument('--show-prompt', action='store_true', help='只打印实际送进模型的提示词')
    args = parser.parse_args()

    if args.describe:
        if args.dry_run:
            print(json.dumps(pipeline.build_describe(jobs.save_reference(Path(args.describe).read_bytes(), cfg), cfg), ensure_ascii=False, indent=1))
            return 0
        try:
            return asyncio.run(describe(args.describe, cfg))
        except KeyboardInterrupt:
            return 1
    people = None
    if args.people:
        people = json.loads(Path(args.people).read_text('utf-8'))
        people = {'persons': people} if isinstance(people, list) else people
    marks = None
    if args.marks:
        marks = json.loads(Path(args.marks).read_text('utf-8') if Path(args.marks).is_file() else args.marks)
    refs = [jobs.save_reference(Path(path).read_bytes(), cfg) for path in args.image]
    try:
        req = jobs.make_request({'prompt': args.prompt, 'refs': refs, 'aspect': args.aspect, 'quality': args.quality,
                                 'count': args.count, 'seed': args.seed, 'steps': args.steps,
                                 'transparent': args.transparent, 'mode': args.mode, 'toggle': args.toggle, 'people': people,
                                 'marks': marks, 'upscale': args.upscale, 'repaint': args.repaint, 'anchor': args.anchor,
                                 'style': args.style}, cfg)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if args.show_prompt:
        print(pipeline.full_prompt(req, cfg))
        cfg_scale, negative = pipeline.guidance(req, cfg)
        if cfg_scale > 1:
            print(f'\n（CFG {cfg_scale}）负面提示词：{negative or "（空）"}')
        return 0
    if args.dry_run:
        graph = pipeline.build(req, cfg, f'studio/{date.today().isoformat()}/qwen')
        print(json.dumps(graph, ensure_ascii=False, indent=1))
        return 0
    try:
        return asyncio.run(generate(req, cfg))
    except KeyboardInterrupt:
        return 1


if __name__ == '__main__':
    sys.exit(main())
