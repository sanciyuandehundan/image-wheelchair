"""把官方提示词改写模型 Qwen-Image-2.1-PE-T2I 的 4 个分片原样合并成一个文件，给 ComfyUI 的 CLIPLoader 加载。

    python -X utf8 -s scripts/merge_pe_t2i.py

输入：models/pe_t2i/（从魔搭 Qwen/Qwen-Image-2.1-PE-T2I 下载的原始文件，含 model.safetensors.index.json）
输出：models/text_encoders/qwen_image_2.1_pe_t2i_bf16.safetensors
不改精度、不改键名（ComfyUI 自己认 model.language_model. / model.visual. / lm_head. 这些官方键名），只是把分片拼起来。
"""
import json
from pathlib import Path

from safetensors import safe_open
from safetensors.torch import save_file

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'models' / 'pe_t2i'
DST = ROOT / 'models' / 'text_encoders' / 'qwen_image_2.1_pe_t2i_bf16.safetensors'


def main():
    weight_map = json.loads((SRC / 'model.safetensors.index.json').read_text('utf-8'))['weight_map']
    tensors = {}
    for shard in sorted(set(weight_map.values())):
        with safe_open(SRC / shard, 'pt') as f:
            for key in f.keys():
                tensors[key] = f.get_tensor(key)
        print(f'读完 {shard}，累计 {len(tensors)} 个张量', flush=True)
    missing = set(weight_map) - set(tensors)
    if missing:
        raise SystemExit(f'分片里缺少 {len(missing)} 个张量，例如 {sorted(missing)[:3]}')
    dtypes = sorted({str(t.dtype) for t in tensors.values()})
    tmp = DST.with_suffix('.safetensors.tmp')
    save_file(tensors, str(tmp), metadata={'format': 'pt', 'source': 'Qwen/Qwen-Image-2.1-PE-T2I (merged shards, unchanged)'})
    tmp.replace(DST)
    print(f'已写出 {DST}（{DST.stat().st_size / 1e9:.2f} GB，{len(tensors)} 个张量，精度 {dtypes}）')


if __name__ == '__main__':
    main()
