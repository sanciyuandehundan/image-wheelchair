"""启动、关闭本项目自带的 ComfyUI（端口 8189，只给网页服务用）。网页服务会自动调用，也可以手动：
  python scripts/launch.py start|stop|status [--no-browser]"""
import argparse
import json
import msvcrt
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

import psutil

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / 'logs'
JOB = LOGS / 'comfyui-job.json'
BOOT = ROOT / 'scripts' / 'run_comfy.py'
PORT = 8189
URL = f'http://127.0.0.1:{PORT}'
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(path):
    with HTTP.open(URL + path, timeout=2) as response:
        return json.load(response)


def owned_process():
    if not JOB.exists():
        return None
    job = json.loads(JOB.read_text('utf-8'))
    try:
        process = psutil.Process(job['pid'])
        if (abs(process.create_time() - job['created']) < 0.01
                and Path(process.exe()).resolve() == Path(sys.executable).resolve()
                and str(BOOT) in process.cmdline()):
            return process
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return None


def missing_parts():
    """安装脚本（安装.cmd）还没装好的东西：ComfyUI 源码、模型文件。"""
    missing = [] if (ROOT / '.runtime' / 'ComfyUI' / 'main.py').exists() else ['ComfyUI']
    manifest = json.loads((ROOT / 'scripts' / 'models.json').read_text('utf-8'))
    for item in [*manifest['models'], manifest['text_encoder']]:
        path = ROOT / 'models' / item['path']
        if not path.exists() or path.stat().st_size != item['size']:
            missing.append(item['name'])
    return missing


def start(open_browser):
    missing = missing_parts()
    if missing:
        raise RuntimeError('还没有安装完：' + '、'.join(missing) + '。请先双击「安装.cmd」（可以重复运行，会接着装）。')
    for name in ('input', 'output', 'temp', 'user'):
        (ROOT / name).mkdir(exist_ok=True)
    process = owned_process()
    if process is None:
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', PORT))
            except OSError as error:
                raise RuntimeError(f'端口 {PORT} 已被其他程序占用。') from error
        args = [sys.executable, '-X', 'utf8', '-s', str(BOOT),
                '--listen', '127.0.0.1', '--port', str(PORT),
                '--bf16-unet', '--bf16-text-enc', '--bf16-vae',
                # 显存上限：ComfyUI 自己最多用「总显存 - 5GB」，并且连同其他程序一起算时再留 1GB 空闲。
                # 不留的话它会把 16GB 占满，桌面程序一抢显存，WDDM 就来回换页：变慢几倍，或卡死在加载文本编码器
                '--enable-dynamic-vram', '--reserve-vram', '5', '--vram-headroom', '1', '--async-offload', '2',
                '--use-pytorch-cross-attention', '--disable-api-nodes',
                '--disable-all-custom-nodes', '--disable-auto-launch',
                '--extra-model-paths-config', str(ROOT / 'extra_model_paths.yaml'),
                '--user-directory', str(ROOT / 'user'),
                '--input-directory', str(ROOT / 'input'),
                '--output-directory', str(ROOT / 'output'),
                '--temp-directory', str(ROOT / 'temp')]
        stamp = time.strftime('%Y%m%d-%H%M%S')
        stdout = LOGS / f'comfyui-{stamp}.log'
        stderr = LOGS / f'comfyui-{stamp}.error.log'
        with stdout.open('w', encoding='utf-8') as out, stderr.open('w', encoding='utf-8') as err:
            child = subprocess.Popen(args, cwd=ROOT, stdout=out, stderr=err,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        process = psutil.Process(child.pid)
        job = {'pid': process.pid, 'created': process.create_time(),
               'python': sys.executable, 'url': URL,
               'stdout': str(stdout), 'stderr': str(stderr)}
        JOB.write_text(json.dumps(job, ensure_ascii=False, indent=2), 'utf-8')
        print(f'正在启动 ComfyUI，PID {process.pid}……', flush=True)
    for _ in range(55):
        if not process.is_running():
            raise RuntimeError(f'ComfyUI 启动失败，请查看 {LOGS} 中的 error.log。')
        try:
            stats = request('/system_stats')
            if 'system' in stats:
                print(f'ComfyUI 已就绪：{URL}')
                if open_browser:
                    webbrowser.open(URL)
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError(f'程序仍在启动，请稍后重新双击启动器。日志：{LOGS}')


def stop():
    process = owned_process()
    if process is None:
        print('Qwen ComfyUI 已停止。')
        return
    try:
        queue = request('/queue')
    except (OSError, ValueError):
        queue = {}
    if queue.get('queue_running') or queue.get('queue_pending'):
        raise RuntimeError('还有生成任务，请先在 ComfyUI 中停止任务或清空队列，再关闭。')
    process.terminate()
    process.wait(timeout=15)
    print('ComfyUI 已关闭。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'stop', 'status'])
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    LOGS.mkdir(exist_ok=True)
    try:
        with (LOGS / 'launcher.lock').open('a+b') as lock:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                print('启动器正在处理，请稍等。')
                raise SystemExit(0)
            if args.action == 'start':
                start(not args.no_browser)
            elif args.action == 'stop':
                stop()
            else:
                missing = missing_parts()
                print('还没有安装完：' + '、'.join(missing) if missing else '程序和模型都已安装。')
                process = owned_process()
                print(f'ComfyUI 正在运行：{URL}' if process else 'ComfyUI 尚未启动。')
    except Exception as error:
        print(f'错误：{error}', file=sys.stderr)
        raise SystemExit(1)
