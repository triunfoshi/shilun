"""Read-only comparison of archived sector ranks and constituent money flow.

Run with --sectors PATH --output PATH. Memberships are frozen at the snapshot
date for the entire comparison window; this is descriptive, not a backtest.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import math
from pathlib import Path

from shilun.api.review import _open_store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sectors', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.sectors.read_text())
    end = payload['analysis_date']
    start = (date.fromisoformat(end) - timedelta(days=16)).isoformat()
    store = _open_store()
    try:
        basic = {r['ts_code']: r for r in store.find_stock_basic()}
        calendar = store.find_trade_calendar(start_date=start, end_date=end)
        date_text = lambda value: str(value) if '-' in str(value) else f'{str(value)[:4]}-{str(value)[4:6]}-{str(value)[6:8]}'
        days = sorted({date_text(r['cal_date']) for r in calendar if int(r.get('is_open') or 0) == 1})[-5:]
        if len(days) != 5 or days[-1] != end:
            raise ValueError('Five-day trading calendar is incomplete')
        fields = ('net_mf_amount', 'buy_lg_amount', 'buy_elg_amount', 'sell_lg_amount', 'sell_elg_amount')
        rows = list(store.collection('moneyflow').find(
            {'trade_date': {'$gte': start.replace('-', ''), '$lte': end.replace('-', '')}},
            {'_id': 0, 'ts_code': 1, 'trade_date': 1, **{k: 1 for k in fields}},
        ))
        for row in rows:
            row['trade_date'] = date_text(row['trade_date'])
    finally:
        store.close()
    duplicates = sum(n - 1 for n in Counter((r['ts_code'], r['trade_date']) for r in rows).values() if n > 1)
    if duplicates:
        raise ValueError(f'Duplicate ticker/date moneyflow rows: {duplicates}')
    valid = lambda r: all(isinstance(r.get(k), (int, float)) and math.isfinite(r[k]) for k in fields)
    lookup = {(r['ts_code'], r['trade_date']): r for r in rows if valid(r)}
    result = {'analysis_date': end, 'window_dates': days, 'unit': '亿元', 'duplicates': duplicates,
              'membership_note': '全窗口使用所选日成分快照；不构成事前回测。', 'sectors': []}
    codes = {'851436.SI', '850822.SI', '886033.TI', '886112.TI', '885959.TI'}
    for sector in payload['all_sectors']:
        if sector.get('sector_code') not in codes:
            continue
        full_members = set(sector['member_tickers'])
        members = {t for t in full_members if t in basic and 'ST' not in str(basic[t].get('name', '')).upper()}
        observations = []
        for day in days:
            covered = [lookup[(t, day)] for t in members if (t, day) in lookup]
            observations.append({'date': day, 'covered': len(covered), 'expected': len(members),
                                 'missing_tickers': sorted(t for t in members if (t, day) not in lookup),
                                 'net': round(sum(r['net_mf_amount'] for r in covered) / 10000, 4),
                                 'large': round(sum(r['buy_lg_amount'] + r['buy_elg_amount'] - r['sell_lg_amount'] - r['sell_elg_amount'] for r in covered) / 10000, 4)})
        complete = len(days) == 5 and all(r['covered'] == r['expected'] for r in observations)
        result['sectors'].append({'name': sector['sector_name'], 'code': sector['sector_code'],
            'trend_rank': sector['metrics']['mainline_rank'], 'amount': sector['metrics']['amount'] / 100000,
            'excluded_st_or_not_in_basic': sorted(full_members - members),
            'daily': observations, 'complete_window': complete,
            'five_day_net': round(sum(r['net'] for r in observations), 4) if complete else None,
            'five_day_large': round(sum(r['large'] for r in observations), 4) if complete else None})
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
