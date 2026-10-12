"""生图轮椅的安装脚本。双击「安装.cmd」→ scripts/bootstrap.ps1（装 uv 和 Python、建 .venv）→ 本脚本。

按顺序：
  1. 下载指定版本的 ComfyUI 源码到 .runtime/ComfyUI
  2. 往 .venv 装 PyTorch（CUDA 13.0）、ComfyUI 的依赖和 requirements.txt
  3. 下载模型到 models/，逐个核对 SHA-256；文本编码器下载后由 convert_heretic.py 转成 ComfyUI 格式
都按 scripts/models.json 从官方源下载，本仓库不附带任何第三方的代码或模型。
可以随时重跑：已经装好、校验过的会跳过；下载中断了重跑会接着下（断点在 models/.partial/）。

  .venv\\Scripts\\python.exe scripts\\install.py --uv .runtime\\uv-版本\\uv.exe [--skip-models] [--only 名字…] [--keep-downloads]
"""
import argparse
import concurrent.futures
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
COMFY = RUNTIME / 'ComfyUI'
MODELS = ROOT / 'models'
PARTIAL = MODELS / '.partial'
VERIFIED = MODELS / '.verified.json'
MANIFEST = json.loads((ROOT / 'scripts' / 'models.json').read_text('utf-8'))
CHUNK = 8 * 1024 * 1024
WORKERS = 8


def say(text=''):
    print(text, flush=True)


def gb(n):
    return f'{n / 1e9:.2f} GB'


# ---------- 1. ComfyUI ----------

def git_head(repo):
    """不依赖 git 命令，直接读 .git 里当前的提交号。"""
    head = (repo / '.git' / 'HEAD').read_text().strip()
    if not head.startswith('ref: '):
        return head
    ref = head[5:]
    loose = repo / '.git' / ref
    if loose.exists():
        return loose.read_text().strip()
    for line in (repo / '.git' / 'packed-refs').read_text().splitlines():
        if line.endswith(' ' + ref):
            return line.split()[0]
    return ''


def install_comfyui():
    info = MANIFEST['comfyui']
    marker = RUNTIME / 'comfyui-commit.txt'
    if (COMFY / 'main.py').exists():
        current = marker.read_text().strip() if marker.exists() else git_head(COMFY) if (COMFY / '.git').exists() else ''
        if current == info['commit']:
            marker.write_text(info['commit'])
            say(f'ComfyUI {info["version"]} 已就绪。')
            return
        old = RUNTIME / f'ComfyUI.old-{time.strftime("%Y%m%d-%H%M%S")}'
        say(f'.runtime/ComfyUI 不是本项目用的版本（{current[:12] or "未知"}），挪到 {old.name}，重新下载 {info["version"]}。')
        COMFY.rename(old)
    say(f'下载 ComfyUI {info["version"]}（{info["commit"][:12]}）……')
    urls = [info['url']] + [proxy + info['url'] for proxy in MANIFEST['github_proxies']]
    data = fetch_whole(urls, info['size'], info['sha256'], 'ComfyUI ')
    staging = RUNTIME / 'ComfyUI.staging'
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(staging)
    inner = next(staging.iterdir())
    version = (inner / 'comfyui_version.py').read_text('utf-8')
    if f'"{info["version"]}"' not in version:
        raise SystemExit(f'下载的 ComfyUI 版本不对：{version.strip()}')
    inner.rename(COMFY)
    staging.rmdir()
    marker.write_text(info['commit'])
    say(f'ComfyUI {info["version"]} 已安装到 .runtime/ComfyUI。')


def fetch_whole(urls, size, sha256, label):
    """整个文件一次下完（GitHub 临时打包的源码压缩包不支持分段下载），按顺序试各个来源，核对大小和 SHA-256。"""
    for attempt in range(1, 3):
        for url in urls:
            host = url.split('/')[2]
            try:
                response = Session.get().get(url, timeout=(15, 60))
                response.raise_for_status()
                data = response.content
                if len(data) == size and hashlib.sha256(data).hexdigest() == sha256:
                    say(f'  从 {host} 下载完成')
                    return data
                say(f'  {host} 下载到的文件和官方的不一致，换下一个来源')
            except Exception as error:
                say(f'  {host}：{type(error).__name__}: {str(error)[:120]}')
        time.sleep(5)
    raise SystemExit(f'{label}下载失败，请检查网络（或代理）后重新运行「安装.cmd」。')


# ---------- 2. Python 依赖 ----------

INDEXES = []


def ranked_indexes():
    """PyPI 和几个国内镜像按响应速度排好序（只测一次）；环境变量 IMAGE_WHEELCHAIR_PIP_INDEX 指定的源排最前。
    用标准库测：第一次装依赖时 requests 还没装。"""
    import urllib.request
    if not INDEXES:
        timings = []
        for index in MANIFEST['pypi_indexes']:
            began = time.monotonic()
            try:
                with urllib.request.urlopen(index.rstrip('/') + '/pip/', timeout=8) as response:
                    response.read(4096)
                timings.append((time.monotonic() - began, index))
            except Exception:
                timings.append((float('inf'), index))
        custom = os.environ.get('IMAGE_WHEELCHAIR_PIP_INDEX')
        INDEXES.extend(([custom] if custom else []) + [index for _, index in sorted(timings)])
        say('PyPI 源（按速度排序，失败了换下一个）：' + '、'.join(index.split('/')[2] for index in INDEXES))
    return INDEXES


def pip_install(uv, *arguments):
    """按测速顺序换 PyPI 源。镜像会缺刚发布的包（实测清华、中科大缺 comfyui-workflow-templates-media-assets-02），
    所以镜像当主源、pypi.org 兜底：uv 会先在 --extra-index-url（镜像）里找，镜像没有的包再去 --index-url（pypi.org）。"""
    official = MANIFEST['pypi_indexes'][0]
    for index in ranked_indexes():
        sources = ['--index-url', index] if index == official else ['--index-url', official, '--extra-index-url', index]
        command = [uv, 'pip', 'install', '--python', sys.executable, *sources, *map(str, arguments)]
        if subprocess.run(command).returncode == 0:
            return
        say(f'  用 {index.split("/")[2]} 安装失败，换下一个 PyPI 源')
    raise SystemExit('安装 Python 依赖失败：几个 PyPI 源都试过了，请检查网络后重新运行「安装.cmd」。')


def installed_version(package):
    from importlib import metadata
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def install_torch(uv, keep_downloads):
    """PyTorch 的 CUDA 版轮子有 2GB：自己断点续传下载（官方源和国内镜像里挑最快的）并核对 SHA-256，再从本地文件安装。"""
    torch = MANIFEST['torch']
    if all(installed_version(wheel['package']) == wheel['version'] for wheel in torch['wheels']):
        say('✓ PyTorch 已就绪')
        return
    files = []
    for wheel in torch['wheels']:
        target = RUNTIME / 'wheels' / wheel['filename']
        quoted = wheel['filename'].replace('+', '%2B')
        download([base + quoted for base in torch['base_urls']], target, wheel['size'], wheel['sha256'], wheel['filename'])
        files.append(target)
    say('安装 PyTorch……')
    pip_install(uv, *files)
    if not keep_downloads:
        for path in files:
            path.unlink(missing_ok=True)


def install_packages(uv, keep_downloads):
    install_torch(uv, keep_downloads)
    # ComfyUI 的依赖里也写了 torch/torchaudio：去掉这几行，并用约束文件锁住刚装的版本，免得被 PyPI 上的 CPU 版换掉
    lines = [line for line in (COMFY / 'requirements.txt').read_text('utf-8').splitlines()
             if line.split('=')[0].split('>')[0].split('<')[0].strip().lower() not in ('torch', 'torchvision', 'torchaudio')]
    requirements = RUNTIME / 'requirements-comfyui.txt'
    requirements.write_text('\n'.join(lines) + '\n', 'utf-8')
    constraints = RUNTIME / 'constraints.txt'
    constraints.write_text(''.join(f'{w["package"]}=={w["version"]}\n' for w in MANIFEST['torch']['wheels']), 'utf-8')
    say('安装 ComfyUI 和网页服务的依赖……')
    pip_install(uv, '-r', requirements, '-r', ROOT / 'requirements.txt', '-c', constraints)


# ---------- 3. 模型 ----------

def sha256_file(path, label=''):
    digest = hashlib.sha256()
    size, done, shown = path.stat().st_size, 0, 0
    with path.open('rb') as handle:
        while block := handle.read(16 * 1024 * 1024):
            digest.update(block)
            done += len(block)
            if label and time.monotonic() - shown > 3:
                say(f'  校验 {label}：{done * 100 // max(size, 1)}%')
                shown = time.monotonic()
    return digest.hexdigest()


def load_verified():
    try:
        return json.loads(VERIFIED.read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def save_verified(path, sha256):
    cache = load_verified()
    stat = path.stat()
    cache[path.relative_to(ROOT).as_posix()] = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'sha256': sha256}
    VERIFIED.write_text(json.dumps(cache, ensure_ascii=False, indent=1), 'utf-8')


def already_verified(path, size, sha256, label):
    """文件在、大小对、SHA-256 对。算过一次就记进 models/.verified.json（按大小和修改时间），下次不再重算。"""
    if not path.exists() or path.stat().st_size != size:
        return False
    stat = path.stat()
    cached = load_verified().get(path.relative_to(ROOT).as_posix())
    if cached and cached['size'] == size and cached['mtime_ns'] == stat.st_mtime_ns and cached['sha256'] == sha256:
        return True
    if sha256_file(path, label) != sha256:
        raise SystemExit(f'{path} 已经存在，但内容和官方文件不一致（SHA-256 不同）。请把它删掉或挪走后重新运行「安装.cmd」。')
    save_verified(path, sha256)
    return True


class Session:
    """每个线程一个 requests 会话；不让服务器压缩，Range 请求才准。"""
    local = threading.local()

    @classmethod
    def get(cls):
        import requests
        if not hasattr(cls.local, 'session'):
            cls.local.session = requests.Session()
            cls.local.session.headers.update({'Accept-Encoding': 'identity', 'User-Agent': 'image-wheelchair-installer'})
        return cls.local.session


def fetch_range(url, start, end):
    with Session.get().get(url, headers={'Range': f'bytes={start}-{end}'}, stream=True, timeout=(20, 60)) as response:
        response.raise_for_status()
        if response.status_code != 206:
            raise IOError(f'服务器不支持分段下载（HTTP {response.status_code}）')
        return response.content


def pick_url(urls, size):
    """每个下载源先试下 2MB，用最快的；都失败就报错。"""
    best, best_speed = None, 0
    probe = min(size, 2 * 1024 * 1024) - 1
    for url in urls:
        host = url.split('/')[2]
        for attempt in range(1, 4):  # 网络偶尔会断一下，多试两次再判它不可用
            try:
                began = time.monotonic()
                data = fetch_range(url, 0, probe)
                if len(data) != probe + 1:
                    raise IOError('返回的字节数不对')
                speed = len(data) / max(time.monotonic() - began, 1e-3)
                say(f'  {host}：{speed / 1e6:.1f} MB/s')
                if speed > best_speed:
                    best, best_speed = url, speed
                break
            except Exception as error:
                if attempt == 3:
                    say(f'  {host}：不可用（{type(error).__name__}: {str(error)[:120]}）')
                else:
                    time.sleep(2 * attempt)
    if best is None:
        raise SystemExit('所有下载源都连不上，请检查网络（或代理设置）后重新运行「安装.cmd」。')
    return best


def download(urls, dest, size, sha256, label):
    """多线程分段下载到 models/.partial/<文件名>.part，断点记在旁边的 .json 里；下完核对 SHA-256 再挪到 dest。"""
    if already_verified(dest, size, sha256, label):
        say(f'✓ {label} 已就绪')
        return
    say(f'下载 {label}（{gb(size)}）')
    PARTIAL.mkdir(parents=True, exist_ok=True)
    part = PARTIAL / (dest.name + '.part')
    state_path = PARTIAL / (dest.name + '.part.json')
    state = {}
    if part.exists() and state_path.exists():
        state = json.loads(state_path.read_text('utf-8'))
    if state.get('sha256') != sha256 or not part.exists() or part.stat().st_size != size:
        state = {'sha256': sha256, 'done': []}
        with part.open('wb') as handle:
            handle.truncate(size)
    count = (size + CHUNK - 1) // CHUNK
    done = set(state['done'])
    todo = [i for i in range(count) if i not in done]
    url = pick_url(urls, size)
    lock = threading.Lock()
    progress = {'bytes': sum(min(CHUNK, size - i * CHUNK) for i in done), 'saved': time.monotonic(), 'shown': 0.0}
    began, start_bytes = time.monotonic(), progress['bytes']

    def save_state(force=False):
        if force or time.monotonic() - progress['saved'] > 5:
            state['done'] = sorted(done)
            tmp = state_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(state), 'utf-8')
            tmp.replace(state_path)
            progress['saved'] = time.monotonic()

    def work(index):
        start = index * CHUNK
        end = min(start + CHUNK, size) - 1
        for attempt in range(1, 9):
            try:
                data = fetch_range(url, start, end)
                if len(data) != end - start + 1:
                    raise IOError(f'分段不完整：{len(data)}/{end - start + 1}')
                with part.open('r+b') as handle:
                    handle.seek(start)
                    handle.write(data)
                with lock:
                    done.add(index)
                    progress['bytes'] += len(data)
                    save_state()
                    now = time.monotonic()
                    if now - progress['shown'] > 3:
                        speed = (progress['bytes'] - start_bytes) / max(now - began, 1e-3)
                        left = (size - progress['bytes']) / max(speed, 1)
                        say(f'  {gb(progress["bytes"])} / {gb(size)}  {speed / 1e6:.1f} MB/s  还要约 {left / 60:.0f} 分钟')
                        progress['shown'] = now
                return
            except Exception as error:
                if attempt == 8:
                    raise
                time.sleep(min(30, attempt * 3))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(work, todo))
    finally:
        with lock:
            save_state(force=True)
    say(f'  下载完成，核对 SHA-256……')
    if sha256_file(part, label) != sha256:
        part.unlink()
        state_path.unlink(missing_ok=True)
        raise SystemExit(f'{label} 下载后校验不通过（文件可能被网络或代理改动），已删掉坏文件，请重新运行「安装.cmd」。')
    dest.parent.mkdir(parents=True, exist_ok=True)
    part.replace(dest)
    state_path.unlink(missing_ok=True)
    save_verified(dest, sha256)
    say(f'✓ {label} 已就绪')


def fetch_schema(info):
    """官方 ComfyUI 版文本编码器的文件头（张量名、形状、偏移），只读开头一小段，核对 SHA-256。"""
    for url in info['urls'] * 3:  # 每个源最多试 3 轮
        try:
            prefix = fetch_range(url, 0, 7)
            length = struct.unpack('<Q', prefix)[0]
            raw = fetch_range(url, 8, 7 + length)
            if hashlib.sha256(prefix + raw).hexdigest() == info['header_sha256']:
                return json.loads(raw)
            say(f'  {url.split("/")[2]} 返回的文件头校验不通过，换下一个源')
        except Exception as error:
            say(f'  {url.split("/")[2]}：{type(error).__name__}: {str(error)[:120]}')
            time.sleep(2)
    raise SystemExit('读取官方文本编码器的结构失败，请检查网络后重新运行「安装.cmd」。')


def install_text_encoder(keep_downloads):
    te = MANIFEST['text_encoder']
    dest = MODELS / te['path']
    if already_verified(dest, te['size'], te['sha256'], te['name']):
        say(f'✓ {te["name"]} 已就绪')
        return
    source = PARTIAL / 'heretic'
    source.mkdir(parents=True, exist_ok=True)
    say(f'{te["name"]}：先下载 {te["repo"]} 的原始权重（4 个分片，共 {gb(sum(s["size"] for s in te["shards"]))}），再转成 ComfyUI 格式')
    for item in [te['index'], *te['shards']]:
        download([base + item['filename'] for base in te['base_urls']], source / item['filename'],
                 item['size'], item['sha256'], item['filename'])
    schema = fetch_schema(te['schema'])
    index = json.loads((source / te['index']['filename']).read_text('utf-8'))
    sys.path.insert(0, str(ROOT / 'scripts'))
    import convert_heretic
    say('转换文本编码器（原样重新打包，不改权重，约 5–10 分钟）……')
    report = convert_heretic.convert(source, dest, te['shards'], index['weight_map'], schema,
                                     ROOT / 'logs' / 'heretic-conversion.json', te['revision'])
    if report['sha256'] != te['sha256']:
        dest.unlink()
        raise SystemExit('转换结果和预期不一致，已删掉。请把 logs/heretic-conversion.json 发给作者。')
    save_verified(dest, te['sha256'])
    if not keep_downloads:
        shutil.rmtree(source, ignore_errors=True)
        say('  已删掉下载的原始分片（--keep-downloads 可以保留）。')
    say(f'✓ {te["name"]} 已就绪')


def install_models(only, keep_downloads):
    for item in MANIFEST['models']:
        if only and not any(word in item['path'] for word in only):
            continue
        download(item['urls'], MODELS / item['path'], item['size'], item['sha256'], item['name'])
    if not only or any(word in MANIFEST['text_encoder']['path'] for word in only):
        install_text_encoder(keep_downloads)


LICENSES = """\
下载模型前请阅读这些模型的许可证（本项目不附带、不分发它们，是你从官方来源直接下载）：
  · Qwen-Image-2.1 生图模型和 VAE：Qwen RESEARCH LICENSE —— 只能用于研究、评估等非商业用途，商用需另向阿里申请授权
    https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE
  · 文本编码器 Qwen3-VL-8B-Instruct（Heretic 版，社区修改，非阿里官方）：Apache-2.0
    https://huggingface.co/pottokao/Qwen-Image-2.1-Text-Encoder-Heretic
  · 超分模型 4x-AnimeSharp：CC BY-NC-SA 4.0 —— 只能非商业使用
    https://huggingface.co/Kim2091/AnimeSharp
"""


def accept_licenses(assume_yes):
    """模型下载前让用户确认许可证；同意过一次记在 .runtime/licenses-accepted.txt，以后不再问。"""
    marker = RUNTIME / 'licenses-accepted.txt'
    if marker.exists():
        return
    say(LICENSES)
    if not assume_yes:
        try:
            answer = input('同意以上许可证并开始下载模型？输入 Y 回车继续：').strip().lower()
        except EOFError:
            answer = ''
        if answer not in ('y', 'yes'):
            raise SystemExit('没有同意许可证，不下载模型。程序部分已经装好；同意后重新运行「安装.cmd」即可下载模型。')
    marker.write_text(time.strftime('%Y-%m-%d %H:%M:%S') + ' 已确认模型许可证\n' + LICENSES, 'utf-8')


# ---------- 检查 ----------

def check_gpu():
    try:
        import torch
    except ImportError:
        say('⚠ PyTorch 没装上。')
        return
    if not torch.cuda.is_available():
        say('⚠ 没有检测到可用的 NVIDIA 显卡（或驱动太旧，CUDA 13.0 需要 580 版以上的驱动），生成会非常慢甚至无法运行。')
        return
    props = torch.cuda.get_device_properties(0)
    vram = props.total_memory / 1024 ** 3
    say(f'显卡：{props.name}，显存 {vram:.0f} GB')
    if vram < 15:
        say('⚠ 显存小于 16GB：模型会更频繁地在内存和显存之间搬运，速度会慢不少。')


def main():
    parser = argparse.ArgumentParser(description='生图轮椅安装脚本')
    parser.add_argument('--uv', required=True, help='uv.exe 的路径（bootstrap.ps1 会传进来）')
    parser.add_argument('--skip-models', action='store_true', help='只装程序，不下载模型')
    parser.add_argument('--only', nargs='*', default=[], help='只下载路径里含这些字的模型，例如 --only upscale')
    parser.add_argument('--keep-downloads', action='store_true', help='转换文本编码器后保留下载的原始分片')
    parser.add_argument('--yes', action='store_true', help='不再询问，直接同意模型许可证（无人值守安装用）')
    args = parser.parse_args()
    for name in ('input', 'output', 'temp', 'user', 'logs'):
        (ROOT / name).mkdir(exist_ok=True)
    say('== 1/3 ComfyUI ==')
    # 刚建好的 .venv 是空的：先装本项目自己用的几个小库（下载 ComfyUI 和模型要用 requests）
    pip_install(args.uv, '-q', '-r', ROOT / 'requirements.txt')
    install_comfyui()
    say('\n== 2/3 Python 依赖 ==')
    install_packages(args.uv, args.keep_downloads)
    if not args.skip_models:
        say('\n== 3/3 模型（共约 32GB，下载时另需约 17GB 临时空间）==')
        accept_licenses(args.yes)
        install_models(args.only, args.keep_downloads)
    say('\n== 检查 ==')
    check_gpu()
    free = shutil.disk_usage(ROOT).free
    say(f'磁盘剩余 {gb(free)}')
    say('\n安装完成。双击「生图轮椅.cmd」打开网页开始使用。')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(f'安装依赖失败（{error}），请检查网络后重新运行「安装.cmd」。')
    except KeyboardInterrupt:
        raise SystemExit('已中断。重新运行「安装.cmd」会接着装。')
