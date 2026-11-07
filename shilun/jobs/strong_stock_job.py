"""Rebuild a dated strong-stock snapshot without any external source requests."""
import argparse
import json
from shilun.api.review import _open_store
from shilun.review.strong_stock_data import materialize, resolve_date, sync_missing_history


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', default=None)
    parser.add_argument('--sync-missing', action='store_true', help='Explicitly repair historical daily references and official upper limits first')
    parser.add_argument('--sync-lhb', action='store_true', help='Archive the latest 20 exchange sessions of dragon-tiger reports')
    args = parser.parse_args(argv)
    store = _open_store()
    try:
        target = resolve_date(store, args.date)
        if args.sync_missing:
            sync_missing_history(store, target)
        payload = materialize(store, target)
        if args.sync_lhb:
            from shilun.review.strength_review import sync_lhb, selected_sessions
            print(json.dumps(sync_lhb(store, selected_sessions(store, target)), ensure_ascii=False), flush=True)
        print(json.dumps({k: payload[k] for k in ('date', 'version', 'counts', 'scanned_stocks', 'latest_known_stocks')}, ensure_ascii=False), flush=True)
    finally:
        store.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
