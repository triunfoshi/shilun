"""Archive the existing local benchmark for post-retreat comparisons; no DB writes."""
import argparse
from pathlib import Path

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.review.theme_selection_data import collect_benchmark_inputs, read_gzip, write_gzip


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('research/evidence/theme_selection_20260923'))
    args = parser.parse_args()
    market = read_gzip(args.source/'market_inputs.json.gz')
    config = load_config()
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=15000, initialize_indexes=False)
    try:
        benchmark = collect_benchmark_inputs(store, market['context_days'])
    finally:
        store.close()
    if not benchmark['bars']:
        raise ValueError('本地没有对应日期的上证指数行情，未覆盖已有归档')
    write_gzip(args.source/'benchmark_inputs.json.gz', benchmark)
    print(f"Archived benchmark: {len(benchmark['bars'])}/{len(market['context_days'])} exchange sessions")


if __name__ == '__main__':
    main()
