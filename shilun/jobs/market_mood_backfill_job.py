"""Repair historical P2 mood archives with the shared sentiment engine."""
import argparse
from datetime import date
import json

from shilun.api.review import _open_store
from shilun.review.mood_history import backfill_moods


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=date.fromisoformat)
    parser.add_argument('--days', type=int, default=31)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    if args.date > date.today() or not 1 <= args.days <= 120:
        parser.error('日期不能在未来；天数应在1至120之间')
    store = _open_store()
    try:
        result = backfill_moods(store, args.date.isoformat(), args.days, force=args.force,
            progress=lambda row: print(json.dumps(row, ensure_ascii=False), flush=True))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 2 if result['pending'] else 0
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
