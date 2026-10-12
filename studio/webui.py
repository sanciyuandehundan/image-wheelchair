"""网页入口：双击根目录的「生图轮椅.cmd」，或 python -X utf8 -s studio/webui.py [--no-browser]

改了本文件、jobs.py、comfy_client.py 需要重启；config.toml、pipeline.py、web/index.html 不用（刷新网页即可）。
"""
import argparse
import asyncio
import hashlib
import importlib
import os
import socket
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 嵌入式 Python 不会自动把脚本目录加进 sys.path

from aiohttp import web
from PIL import Image, UnidentifiedImageError

import jobs
import pipeline
from comfy_client import Comfy

WEB = jobs.STUDIO / 'web'
THUMBS = jobs.ROOT / 'temp' / 'studio_thumbs'
AREAS = {'input': jobs.INPUT, 'output': jobs.OUTPUT}
NO_CACHE = {'Cache-Control': 'no-cache'}

routes = web.RouteTableDef()
COMFY: Comfy = None
JOBS = {}           # id → Job，包括刚结束的
TASKS = set()       # 防止后台任务被回收
_thumb_tasks = {}


def spawn(coro):
    task = asyncio.create_task(coro)
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (ValueError, UnidentifiedImageError) as error:
        return web.json_response({'error': str(error)}, status=400)
    except Exception as error:
        return web.json_response({'error': f'{type(error).__name__}: {error}'}, status=500)


def area_file(request):
    path = jobs.inside(AREAS[request.match_info['area']], request.match_info['path'])
    if path is None:
        raise web.HTTPNotFound()
    return path


@routes.get('/')
async def index(request):
    return web.FileResponse(WEB / 'index.html', headers=NO_CACHE)


@routes.get('/files/{area:input|output}/{path:.+}')
async def files(request):
    return web.FileResponse(area_file(request), headers=NO_CACHE)


def make_thumb(path, thumb):
    with Image.open(path) as image:
        image.thumbnail((768, 768), Image.LANCZOS)
        thumb.parent.mkdir(parents=True, exist_ok=True)
        image.save(thumb, 'WEBP', quality=88)


@routes.get('/thumbs/{area:input|output}/{path:.+}')
async def thumbs(request):
    path = area_file(request)
    key = hashlib.sha1(f'{path}|{path.stat().st_mtime_ns}'.encode()).hexdigest()
    thumb = THUMBS / f'{key}.webp'
    if not thumb.exists():
        if key not in _thumb_tasks:
            _thumb_tasks[key] = asyncio.ensure_future(asyncio.to_thread(make_thumb, path, thumb))
            _thumb_tasks[key].add_done_callback(lambda _: _thumb_tasks.pop(key, None))
        await asyncio.shield(_thumb_tasks[key])
    return web.FileResponse(thumb, headers=NO_CACHE)


def public_mode(mode):
    """网页只需要模式的界面文字，模板留在服务端。"""
    out = {k: v for k, v in mode.items() if k not in ('template', 'extra')}
    if 'toggle' in mode:
        out['toggle'] = public_mode(mode['toggle'])
    return out


@routes.get('/api/config')
async def config(request):
    cfg = jobs.load_config()
    return web.json_response({
        'aspects': cfg['size']['aspects'],
        'qualities': cfg['size']['quality'],
        'default_quality': cfg['size']['default_quality'],
        'steps': cfg['sampling']['steps'],
        'upscale_targets': cfg['upscale']['targets'],
        'max_refs': jobs.MAX_REFS,
        'comfy_url': cfg['server']['comfy_url'],
        'modes': {key: public_mode(mode) for key, mode in cfg.get('modes', {}).items()},
        'people': cfg.get('people'),
        'board': cfg.get('board'),
    })


@routes.post('/api/people-preview')
async def people_preview(request):
    """「人物」面板里的预览：按 config.toml 的 [people] 渲染人物描述（和生成时同一段代码）。"""
    data = await request.json()
    cfg = jobs.load_config()
    importlib.reload(pipeline)
    people = jobs.make_people(data.get('people'), cfg, int(data.get('refs') or 0))
    if not people:
        return web.json_response({'text': ''})
    text = pipeline.people_text(people, cfg)
    cfg_scale, negative = pipeline.guidance({'people': people}, cfg)
    if cfg_scale > 1:
        text += f'\n\n（CFG {cfg_scale}）负面提示词：{negative}'
    return web.json_response({'text': text})


@routes.get('/api/status')
async def status(request):
    state = ('starting' if COMFY.starting else 'ready' if await COMFY.alive()
             else 'busy' if COMFY.process_alive() else 'stopped')
    return web.json_response({'comfy': state, 'active': sum(job.status == 'running' for job in JOBS.values())})


@routes.post('/api/references')
async def references(request):
    """multipart 上传的图片，或 JSON {"output": "studio/…png"}（把生成结果当参考图）。"""
    cfg = jobs.load_config()
    blobs = []
    if request.content_type == 'application/json':
        path = jobs.inside(jobs.OUTPUT, (await request.json())['output'])
        if path is None:
            raise web.HTTPNotFound()
        blobs.append(path.read_bytes())
    else:
        async for part in await request.multipart():
            blobs.append(await part.read())
    refs = [await asyncio.to_thread(jobs.save_reference, data, cfg) for data in blobs]
    return web.json_response({'refs': refs})


@routes.post('/api/generate')
async def generate(request):
    job = jobs.Job(jobs.make_request(await request.json(), jobs.load_config()))
    for old in [j for j in JOBS.values() if j.finished and time.time() - j.finished > 3600]:
        del JOBS[old.id]
    JOBS[job.id] = job
    spawn(run_and_log(job))
    return web.json_response(job.public())


@routes.post('/api/describe')
async def describe(request):
    """反推提示词：{"ref": "studio/…png"}（先用 /api/references 把图片存进 input）。网页轮询 /api/jobs/<id>，完成后 text 里是结果。"""
    ref = (await request.json()).get('ref') or ''
    if not jobs.inside(jobs.INPUT, ref):
        raise ValueError('图片找不到了，请重新放一次。')
    job = jobs.DescribeJob(ref)
    JOBS[job.id] = job
    spawn(describe_and_log(job))
    return web.json_response(job.public())


async def describe_and_log(job):
    await jobs.run_describe(job, COMFY)
    note = f'，{job.error}' if job.error else f'，{len(job.text)} 字'
    print(f"[{time.strftime('%H:%M:%S')}] {job.status}：反推提示词 {job.req['ref']}（{job.finished - job.created:.0f} 秒{note}）", flush=True)


async def run_and_log(job):
    await jobs.run_job(job, COMFY)
    note = f'，{job.error}' if job.error else ''
    mode = jobs.load_config().get('modes', {}).get(job.req['mode'], {}).get('label')
    people = f"【{len(job.req['people']['persons'])} 个人物】" if job.req.get('people') else ''
    print(f"[{time.strftime('%H:%M:%S')}] {job.status}：{f'【{mode}】' if mode else ''}{people}{job.req['prompt'][:40]}（{len(job.images)} 张，{job.finished - job.created:.0f} 秒{note}）", flush=True)


@routes.get('/api/jobs')
async def active_jobs(request):
    return web.json_response([job.public() for job in JOBS.values() if job.status == 'running' and not isinstance(job, jobs.DescribeJob)])


def find_job(request):
    job = JOBS.get(request.match_info['id'])
    if job is None:
        raise web.HTTPNotFound()
    return job


@routes.get('/api/jobs/{id}')
async def job_state(request):
    return web.json_response(find_job(request).public())


@routes.post('/api/jobs/{id}/cancel')
async def cancel(request):
    job = find_job(request)
    await job.cancel(COMFY)
    return web.json_response(job.public())


@routes.get('/api/history')
async def history(request):
    records = jobs.read_history()
    if 'before' in request.query:
        before = float(request.query['before'])
        records = [r for r in records if r['created'] < before]
    limit = int(request.query.get('limit', 20))
    return web.json_response({'records': records[-limit:], 'more': len(records) > limit})


@routes.delete('/api/history/{id}')
async def remove(request):
    jobs.remove_history(request.match_info['id'])
    return web.json_response({'ok': True})


@routes.post('/api/comfy/stop')
async def stop_comfy(request):
    if any(job.status == 'running' for job in JOBS.values()):
        raise ValueError('还有任务在生成，先取消或等它完成。')
    code, text = await COMFY.stop()
    return web.json_response({'ok': code == 0, 'message': text})


@routes.post('/api/open-folder')
async def open_folder(request):
    folder = jobs.OUTPUT / 'studio'
    folder.mkdir(parents=True, exist_ok=True)
    os.startfile(folder)
    return web.json_response({'ok': True})


async def warm_up():
    """打开网页的同时先把 ComfyUI 拉起来，省得第一次点生成还要等启动。"""
    if await COMFY.alive():
        print('ComfyUI 已在运行。', flush=True)
    else:
        print('正在后台启动 ComfyUI……', flush=True)
    try:
        await COMFY.ensure_running()
        print('ComfyUI 已就绪。', flush=True)
    except Exception as error:
        print(f'ComfyUI 启动失败：{error}\n（点生成时会再试一次）', flush=True)


async def serve(open_browser):
    global COMFY
    cfg = jobs.load_config()
    url = f"http://127.0.0.1:{cfg['server']['port']}"
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', cfg['server']['port'])) == 0:
            print(f'生图轮椅已经在运行（或端口被占用）：{url}')
            if open_browser:
                webbrowser.open(url)
            return
    COMFY = Comfy(cfg['server']['comfy_url'])
    app = web.Application(middlewares=[errors], client_max_size=256 * 1024 * 1024)
    app.add_routes(routes)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', cfg['server']['port']).start()
    print(f'生图轮椅已启动：{url}\n关闭本窗口即退出；ComfyUI 会留在后台，可在网页右上角菜单里关闭。', flush=True)
    if open_browser:
        webbrowser.open(url)
    spawn(warm_up())
    try:
        await asyncio.Event().wait()
    finally:
        await COMFY.close()
        await runner.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    try:
        asyncio.run(serve(not parser.parse_args().no_browser))
    except KeyboardInterrupt:
        pass
