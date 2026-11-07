"""Rebuild the complete accepted shortline baseline with Vibe event labels.

Only cached same-day facts are read. Existing immutable revisions remain intact;
cycle and single-day evidence switch together through the existing atomic pointer.
"""
import argparse
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.jobs.theme_daily_job import ROOT, run, ThemeInputError
from shilun.review.board_data import REASON_COLLECTION
from shilun.review.theme_selection_data import read_gzip


def migrate(store, day, root=ROOT):
    root = Path(root)
    checkpoint = root/'inputs.json'
    source = root/json.loads(checkpoint.read_text())['source'] if checkpoint.exists() else root/'seed'
    cached = read_gzip(source/'market_inputs.json.gz')
    if cached['end'] != day or not cached['days'] or cached['days'][-1] != day:
        raise ThemeInputError([{'dataset': 'migration_baseline',
                               'reason': '迁移必须使用已验收且结束日等于所选日的完整缓存；先完成常规当日采集'}])
    if (source/'benchmark_inputs.json.gz').exists():
        cached['benchmark'] = read_gzip(source/'benchmark_inputs.json.gz')
    documents = store.collection(REASON_COLLECTION).find({'date': {'$in': cached['context_days']}}, {'_id': 0})
    for document in documents:
        if document.get('date') in cached['context_days'] and document.get('reasons'):
            cached.setdefault('reasons', {})[document['date']] = document
    return run(store, day, root=root, collector=lambda *_: deepcopy(cached))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=lambda value: date.fromisoformat(value).isoformat())
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    config = load_config()
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=60_000, initialize_indexes=False)
    try:
        result = migrate(store, args.date, args.root)
        print(json.dumps({k: result.get(k) for k in ('date', 'status', 'revision', 'quality_status', 'diagnostic_count')}, ensure_ascii=False))
    finally:
        store.close()


if __name__ == '__main__':
    main()
