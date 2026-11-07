"""Materialize the complete dated Eastmoney taxonomy, without ranking or trades."""
import argparse
from datetime import date
import json
from shilun.api.review import _open_store
from shilun.market.taxonomy import sync_taxonomy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=date.fromisoformat)
    parser.add_argument('--start', type=date.fromisoformat, help='历史行业补齐起始日')
    parser.add_argument('--industries-only', action='store_true', help='只发布完整的三级行业投影，不发布完整概念目录')
    args = parser.parse_args(argv)
    if args.date > date.today():
        parser.error('不能同步未来日期')
    if args.start and (not args.industries_only or args.start > args.date):
        parser.error('--start 仅用于行业历史补齐，且不能晚于 --date')
    store = _open_store()
    try:
        if args.industries_only:
            from shilun.market.industry_history import sync_industry_history
            from shilun.market.taxonomy import day
            dates = [day(r['cal_date']) for r in store.find_trade_calendar(
                start_date=(args.start or args.date).isoformat(), end_date=args.date.isoformat())
                if str(r.get('is_open')) in ('1', 'True')]
            if not dates:
                raise ValueError('所选区间没有交易日')
            result = sync_industry_history(store, dates, progress=lambda p: print(json.dumps(p), flush=True))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0 if result['status'] == 'ok' else 1
        result = sync_taxonomy(store, args.date.isoformat(), progress=lambda p: print(json.dumps(p), flush=True))
        print(json.dumps({k: result[k] for k in ('date', 'version', 'catalog_count', 'counts',
            'stock_count', 'stock_mapping_counts', 'unresolved_parent_count')}, ensure_ascii=False), flush=True)
        return 0
    except Exception as error:
        print(f'taxonomy_sync failed: {type(error).__name__}', flush=True)
        return 1
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
