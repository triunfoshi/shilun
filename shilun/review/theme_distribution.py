"""Publish both immutable theme archives through one atomic Mongo pointer."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path

from bson import Binary

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.review.theme_rules import rule_config
from shilun.review.event_theme_taxonomy import VERSION as EVENT_SCHEMA

CONTROL = 'theme_archive_control'
FILES = 'theme_archive_files'


def enabled():
    return load_config().theme_archive_backend == 'mongo'


def open_store():
    config = load_config()
    if not config.mongo_uri:
        raise ValueError('统一题材归档数据库未配置')
    return MongoSnapshotStore(config.mongo_uri, config.mongo_db,
                              socket_timeout_ms=30_000, initialize_indexes=False)


def publish(store, roots):
    """Upload, read back, then atomically switch both consumers. Never delete history."""
    manifests = {kind: json.loads((Path(root)/'manifest.json').read_text()) for kind, root in roots.items()}
    cycle, acceptance = manifests['cycle'], manifests['acceptance']
    if cycle['dates'] != acceptance['dates'] or cycle['revision'] != acceptance['revision']:
        raise ValueError('两份题材归档的日期或版本不一致')
    if cycle.get('topic_schema') != EVENT_SCHEMA or acceptance.get('topic_schema') != EVENT_SCHEMA:
        raise ValueError('发布归档必须统一使用Vibe同日事件题材口径')
    previous = store.collection(CONTROL).find_one({'_id': 'current'})
    if previous and (previous['cycle']['warmup_start'] != cycle['warmup_start']
                     or not set(previous['cycle']['dates']).issubset(cycle['dates'])):
        raise ValueError('发布不能缩短已有日期或改变预热起点')
    for kind, meta in manifests.items():
        for day, digest in meta['payload_hashes'].items():
            raw = (Path(roots[kind])/meta['revision']/f'{day}.json.gz').read_bytes()
            data = json.loads(gzip.decompress(raw))
            if sha256(raw).hexdigest() != digest or data['date'] != day or data['revision'] != cycle['revision']:
                raise ValueError('发布前归档校验失败')
            key = f'{kind}:{meta["revision"]}:{day}'
            files = store.collection(FILES)
            files.update_one({'_id': key}, {'$setOnInsert': {
                'kind': kind, 'date': day, 'revision': meta['revision'], 'sha256': digest, 'data': Binary(raw)}}, upsert=True)
            saved = files.find_one({'_id': key})
            if saved['sha256'] != digest or sha256(bytes(saved['data'])).hexdigest() != digest:
                raise ValueError('云端归档回读校验失败，未切换发布版本')
    current = {'_id': 'current', **manifests, 'revision': cycle['revision'],
               'published_at': datetime.now(timezone.utc).isoformat(), 'date': cycle['dates'][-1]}
    control = store.collection(CONTROL)
    if previous:
        result = control.replace_one({'_id': 'current', 'revision': previous['revision']}, current)
        if not result.matched_count:
            raise ValueError('另一任务已更新发布版本，本次未覆盖')
    else:
        control.insert_one(current)
    return {'date': current['date'], 'revision': current['revision'], 'status': 'published'}


def read(kind, day=None, *, revision=None, manifest_only=False, store=None):
    own_store = store is None
    store = store or open_store()
    try:
        control = store.collection(CONTROL)
        current = control.find_one({'_id': 'current'})
        job = control.find_one({'_id': 'job'}, {'_id': 0}) or {'status': 'unknown'}
        if not current:
            if kind == 'acceptance':
                raise FileNotFoundError('云端题材归档尚未发布')
            return {'date': day, 'status': 'not_materialized', 'diagnostic_status': 'task_incomplete',
                    'active': [], 'archived': [], 'available_dates': [], 'job': job,
                    'message': '短线周期尚未发布，请完成当日收盘题材任务。'}
        meta = current[kind]
        if meta.get('topic_schema') != EVENT_SCHEMA:
            if kind == 'acceptance':
                raise ValueError('旧行业分类归档不再作为事件题材证据，请回补分析生成新归档')
            return {'date': day or current['date'], 'status': 'not_materialized',
                    'diagnostic_status': 'topic_schema_migration', 'topic_schema': EVENT_SCHEMA,
                    'active': [], 'archived': [], 'available_dates': meta['dates'], 'job': job,
                    'message': '题材口径已切换为Vibe同日涨停原因事件标签；旧DC周期仅保留历史，需回补分析生成新版本。'}
        if kind == 'cycle':
            from shilun.review.theme_cycles import VERSION
            if meta['version'] != VERSION or meta['rules'] != rule_config():
                raise ValueError('周期归档规则已过期')
        if revision and revision != meta['revision']:
            raise ValueError('统一题材归档已更新，请重新读取')
        if manifest_only:
            return {**deepcopy(meta), 'job': job}
        day = day or meta['dates'][-1]
        if day not in meta['dates']:
            if kind == 'acceptance':
                raise LookupError('所选日期尚未发布')
            return {'date': day, 'status': 'not_materialized', 'diagnostic_status': 'task_incomplete',
                    'active': [], 'archived': [], 'available_dates': meta['dates'], 'revision': meta['revision'], 'job': job,
                    'message': f'所选日短线周期尚未生成；最新归档为 {meta["dates"][-1]}。未用其他日期代替。'}
        doc = store.collection(FILES).find_one({'_id': f'{kind}:{meta["revision"]}:{day}'})
        if not doc or sha256(bytes(doc['data'])).hexdigest() != meta['payload_hashes'][day]:
            raise ValueError('统一题材归档校验失败')
        result = json.loads(gzip.decompress(bytes(doc['data'])))
        if result['date'] != day or result['revision'] != meta['revision']:
            raise ValueError('题材归档日期或版本不一致')
        return {**result, 'job': job}
    finally:
        if own_store:
            store.close()
