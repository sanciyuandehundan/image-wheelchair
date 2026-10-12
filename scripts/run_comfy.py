"""在 .runtime/ComfyUI 里启动 ComfyUI（由 launch.py 调用，参数原样传给 ComfyUI 的 main.py）。"""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
COMFY = ROOT / '.runtime' / 'ComfyUI'
sys.path.insert(0, str(COMFY))
os.chdir(COMFY)
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
sys.argv[0] = str(COMFY / 'main.py')
runpy.run_path(sys.argv[0], run_name='__main__')
