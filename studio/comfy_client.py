"""ComfyUI 的最小客户端：启动/关闭、提交、查询、取消，以及通过 WebSocket 接收进度。"""
import asyncio
import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path

import aiohttp

LAUNCHER = Path(__file__).resolve().parents[1] / 'scripts' / 'launch.py'
UNREACHABLE = (aiohttp.ClientError, asyncio.TimeoutError, OSError)

_spec = importlib.util.spec_from_file_location('qwen_launch', LAUNCHER)
launch = importlib.util.module_from_spec(_spec)  # 只借用 owned_process()，不会启动什么
_spec.loader.exec_module(launch)


class ComfyError(Exception):
    pass


def describe_error(body):
    """把 /prompt 返回的校验错误整理成一句话。"""
    lines = [body.get('error', {}).get('message', '提交失败')]
    for node_id, info in body.get('node_errors', {}).items():
        for err in info.get('errors', []):
            lines.append(f"{node_id}（{info.get('class_type')}）：{err.get('message')} {err.get('details', '')}".strip())
    return '\n'.join(lines)


async def run_launcher(action):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, '-X', 'utf8', '-s', str(LAUNCHER), action, '--no-browser',
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW)
    out, err = await proc.communicate()
    text = (out + err).decode('utf-8', 'replace').strip()
    return proc.returncode, text


class Comfy:
    def __init__(self, url):
        self.url = url.rstrip('/')
        self.client_id = uuid.uuid4().hex
        self.progress = {}  # prompt_id → {'node', 'value', 'max'}，由 WebSocket 更新
        self.starting = False
        self._start_lock = asyncio.Lock()
        self._session = None
        self._listener = None

    def session(self):
        if self._session is None:
            # 不设总超时：WebSocket 是长连接；普通请求各自带超时
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=5))
        return self._session

    async def close(self):
        if self._listener:
            self._listener.cancel()
        if self._session:
            await self._session.close()

    async def get(self, path, timeout=10):
        async with self.session().get(self.url + path, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            r.raise_for_status()
            return await r.json()

    async def post(self, path, payload):
        async with self.session().post(self.url + path, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
            text = await r.text()
        try:
            return r.status, json.loads(text) if text else None
        except ValueError:  # 500 之类的错误页不是 JSON
            return r.status, {'error': {'message': text[:500]}}

    async def alive(self):
        try:
            await self.get('/system_stats', timeout=2)
            return True
        except UNREACHABLE:
            return False

    @staticmethod
    def process_alive():
        """scripts/launch.py 启动的 ComfyUI 进程还在不在（不管它响不响应）。"""
        try:
            return launch.owned_process() is not None
        except (OSError, ValueError, KeyError):
            return False

    async def ensure_running(self):
        async with self._start_lock:
            if not await self.alive():
                self.starting = True
                try:
                    was_running = self.process_alive()
                    code, text = await run_launcher('start')
                    if code and was_running and self.process_alive():
                        # 进程本来就在，launch.py 重试 55 次还是连不上：不是在启动，是卡住了
                        raise ComfyError('ComfyUI 没有响应：进程还在，但重试了几分钟都连不上，多半卡住了。'
                                         '请在网页右上角菜单里点「关闭 ComfyUI（释放显存）」，或运行 scripts/launch.py stop，再重新生成。')
                    # launch.py 最多等 55 秒；模型盘慢时 ComfyUI 可能还在启动，继续等
                    for _ in range(240):
                        if await self.alive():
                            break
                        await asyncio.sleep(1)
                    else:
                        raise ComfyError(f'ComfyUI 没有启动成功（日志在 logs/ 目录）。\n{text}')
                finally:
                    self.starting = False
            if self._listener is None:
                self._listener = asyncio.create_task(self.listen())

    async def stop(self):
        return await run_launcher('stop')

    async def queue(self, graph):
        prompt_id = str(uuid.uuid4())
        status, body = await self.post('/prompt', {'prompt': graph, 'client_id': self.client_id, 'prompt_id': prompt_id})
        if status != 200:
            raise ComfyError(describe_error(body or {}))
        return prompt_id

    async def history(self, prompt_id):
        return (await self.get(f'/history/{prompt_id}')).get(prompt_id)

    async def queue_ids(self):
        """返回 (正在运行的 id 列表, 排队中的 id 列表，按先后排序)。"""
        q = await self.get('/queue')
        return ([item[1] for item in q['queue_running']],
                [item[1] for item in sorted(q['queue_pending'], key=lambda item: item[0])])

    async def cancel(self, prompt_ids):
        await self.post('/queue', {'delete': prompt_ids})
        for prompt_id in prompt_ids:
            await self.post('/interrupt', {'prompt_id': prompt_id})

    async def listen(self):
        ws_url = self.url.replace('http', 'ws', 1) + f'/ws?clientId={self.client_id}'
        while True:
            try:
                async with self.session().ws_connect(ws_url, heartbeat=30) as ws:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self.on_message(json.loads(msg.data))
            except UNREACHABLE:
                pass
            await asyncio.sleep(2)

    def on_message(self, msg):
        data = msg.get('data') or {}
        prompt_id = data.get('prompt_id')
        if msg['type'] == 'executing' and prompt_id:
            if data['node'] is None:
                self.progress.pop(prompt_id, None)
            else:
                self.progress[prompt_id] = {'node': data['node'], 'value': 0, 'max': 0}
        elif msg['type'] == 'progress' and prompt_id in self.progress:
            self.progress[prompt_id].update(value=data['value'], max=data['max'])
        elif msg['type'] in ('execution_error', 'execution_interrupted'):
            self.progress.pop(prompt_id, None)
