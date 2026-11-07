"""Read immutable, locally materialized acceptance evidence; no live data I/O."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re

from shilun.review.theme_selection_data import read_gzip
from shilun.review.event_theme_taxonomy import VERSION as EVENT_SCHEMA

ROOT = Path(__file__).resolve().parents[2] / 'runtime' / 'theme_acceptance'


def manifest(root=None):
    from shilun.review import theme_distribution
    if root is None and theme_distribution.enabled():
        return theme_distribution.read('acceptance', manifest_only=True)
    root = Path(root or ROOT)
    data = json.loads((root / 'manifest.json').read_text(encoding='utf8'))
    if data.get('topic_schema') != EVENT_SCHEMA:
        raise ValueError('旧分类归档尚未迁移到同日事件题材，请重新生成')
    if not re.fullmatch(r'[a-f0-9]{16}', str(data.get('revision', ''))):
        raise ValueError('验收归档版本无效')
    if not data.get('dates') or any(not re.fullmatch(r'\d{4}-\d{2}-\d{2}', d) for d in data['dates']):
        raise ValueError('验收归档日期无效')
    return data


@lru_cache(maxsize=8)
def _read_bundle(root, revision, day, expected_hash):
    path = Path(root) / revision / f'{day}.json.gz'
    if sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError('验收归档校验失败，请重新生成')
    data = read_gzip(path)
    if data.get('revision') != revision or data.get('date') != day:
        raise ValueError('验收证据与所选日期不一致')
    return data


def day_payload(day, *, revision=None, root=None):
    from shilun.review import theme_distribution
    if root is None and theme_distribution.enabled():
        return theme_distribution.read('acceptance', day, revision=revision)
    root = Path(root or ROOT)
    data = manifest(root)
    if revision and revision != data['revision']:
        raise ValueError('验收归档已更新，请重新读取日期列表')
    if day not in data['dates']:
        raise LookupError('所选日期没有验收归档；请选择已列出的交易日')
    return _read_bundle(str(root), data['revision'], day, data['payload_hashes'][day])
