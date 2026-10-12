"""把 pottokao/Qwen-Image-2.1-Text-Encoder-Heretic 的 transformers 分片原样重新打包成 ComfyUI 能读的单个 safetensors：
只改张量名（去掉 model.language_model. 前缀）、按官方 ComfyUI 版的结构（schema）核对每个张量的名字、精度和形状，权重字节一个不改。
由 scripts/install.py 调用。"""
import hashlib
import json
from pathlib import Path
import struct
import time

from safetensors import safe_open

BUFFER = 16 * 1024 * 1024


def read_header(path):
    with path.open('rb') as source:
        prefix = source.read(8)
        if len(prefix) != 8:
            raise ValueError(f'Truncated safetensors file: {path}')
        length = struct.unpack('<Q', prefix)[0]
        if not 0 < length <= 16 * 1024 * 1024:
            raise ValueError(f'Invalid safetensors header length: {path}')
        raw = source.read(length)
        if len(raw) != length:
            raise ValueError(f'Truncated safetensors header: {path}')
    return json.loads(raw), prefix + raw


def mapped_name(name):
    prefix = 'model.language_model.'
    if name.startswith(prefix):
        return 'model.' + name[len(prefix):]
    return name


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while data := source.read(BUFFER):
            digest.update(data)
    return digest.hexdigest()


def convert(source_dir, output, shards, weight_map, schema, report_path, revision):
    output_header = {'__metadata__': {
        'format': 'pt', 'source': 'pottokao/Qwen-Image-2.1-Text-Encoder-Heretic',
        'source_revision': revision, 'conversion': 'BF16 byte-preserving shard merge and key mapping',
    }}
    plans, seen, cursor = [], set(), 0
    for item in shards:
        source = source_dir / item['filename']
        if source.stat().st_size != item['size']:
            raise ValueError(f'Incomplete source shard: {source}')
        header, raw = read_header(source)
        tensors = sorted(((k, v) for k, v in header.items() if k != '__metadata__'),
                         key=lambda pair: pair[1]['data_offsets'][0])
        shard_cursor = 0
        for name, tensor in tensors:
            start, end = tensor['data_offsets']
            if start != shard_cursor or end < start:
                raise ValueError(f'Invalid or overlapping tensor offsets: {name}')
            if name in seen or weight_map.get(name) != item['filename']:
                raise ValueError(f'Shard index mismatch: {name}')
            seen.add(name)
            key = mapped_name(name)
            expected = schema.get(key)
            if key in output_header or expected is None:
                raise ValueError(f'Unexpected or duplicate ComfyUI tensor: {key}')
            if tensor['dtype'] != 'BF16' or tensor['dtype'] != expected['dtype'] or tensor['shape'] != expected['shape']:
                raise ValueError(f'Precision or shape mismatch: {key}')
            if end - start != expected['data_offsets'][1] - expected['data_offsets'][0]:
                raise ValueError(f'Tensor byte length mismatch: {key}')
            output_header[key] = {**tensor, 'data_offsets': [cursor + start, cursor + end]}
            shard_cursor = end
        if len(raw) + shard_cursor != item['size']:
            raise ValueError(f'Unexpected trailing or missing shard bytes: {source}')
        plans.append((source, item, raw, shard_cursor))
        cursor += shard_cursor
    if seen != set(weight_map) or set(output_header) - {'__metadata__'} != set(schema) - {'__metadata__'}:
        raise ValueError('Incomplete tensor set; language and vision weights must both be preserved')

    raw_header = json.dumps(output_header, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    raw_header += b' ' * (-len(raw_header) % 8)
    prefix = struct.pack('<Q', len(raw_header)) + raw_header
    temporary = output.with_name(output.name + '.converting')
    output.parent.mkdir(parents=True, exist_ok=True)
    output_digest = hashlib.sha256(prefix)
    verified_sources = []
    with temporary.open('wb') as destination:
        destination.write(prefix)
        for source, item, raw, length in plans:
            source_digest = hashlib.sha256(raw)
            with source.open('rb') as handle:
                handle.seek(len(raw))
                remaining = length
                while remaining:
                    data = handle.read(min(BUFFER, remaining))
                    if not data:
                        raise ValueError(f'Source changed during conversion: {source}')
                    destination.write(data)
                    source_digest.update(data)
                    output_digest.update(data)
                    remaining -= len(data)
            if source_digest.hexdigest() != item['sha256']:
                raise ValueError(f'Source SHA-256 mismatch; output not installed: {source}')
            verified_sources.append({'filename': item['filename'], 'sha256': item['sha256']})
            print(f'Repacked and verified {item["filename"]}', flush=True)

    digest = output_digest.hexdigest()
    if file_hash(temporary) != digest:
        raise ValueError('Repacked file verification failed; output not installed')
    with safe_open(str(temporary), framework='pt', device='cpu') as weights:
        if set(weights.keys()) != set(schema) - {'__metadata__'}:
            raise ValueError('Safetensors reader found an incomplete tensor set')
        for key in weights.keys():
            tensor = weights.get_slice(key)
            if tensor.get_dtype() != 'BF16' or tensor.get_shape() != schema[key]['shape']:
                raise ValueError(f'Safetensors reader found an incompatible tensor: {key}')
    if output.exists():
        if file_hash(output) != digest:
            raise ValueError(f'Existing converted model differs; retained: {output}')
        temporary.unlink()  # 已有的一模一样，临时文件不留（以前这里会留下一个 17GB 的 .converting）
    else:
        temporary.replace(output)
    report = {
        'stage': 'verified', 'source_revision': revision, 'source_shards': verified_sources,
        'output': str(output), 'size': output.stat().st_size, 'sha256': digest,
        'mtime_ns': output.stat().st_mtime_ns, 'tensor_count': len(seen), 'dtype': 'BF16',
        'tensor_payloads_preserved': True,
        'vision_tensors': sum(k.startswith('model.visual.') for k in output_header),
        'comfyui_schema_matches': True, 'safetensors_reader_verified': True,
        'model_inference_tested': False,
        'verified_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp_report = report_path.with_suffix('.json.tmp')
    temp_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    temp_report.replace(report_path)
    print(f'Installed {output.name}: {len(seen)} BF16 tensors; tensor bytes unchanged.', flush=True)
    return report
