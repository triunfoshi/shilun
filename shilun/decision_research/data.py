"""Read-only, reproducible cache intake for the seven-step decision pilot.

No provider downloads, source refreshes, trading writes or future price reads.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import gzip
import json
from pathlib import Path

from shilun.review.sources import load_review_sessions


def read_dataset(store, target: str, window: int = 20, *, progress=None) -> dict:
    def report(message):
        if progress:
            progress(message)

    report('核验交易日历')
    start = (date.fromisoformat(target) - timedelta(days=(window + 30) * 2)).isoformat()
    sessions = load_review_sessions(store, target, start)[-(window + 7):]
    if len(sessions) < 24 or sessions[-1] != target:
        raise ValueError('所选日不是已核验交易日，或连续日历不足24个交易日')
    compact = [d.replace('-', '') for d in sessions]
    dataset = {'date': target, 'window': window, 'sessions': sessions,
               'loaded_at': datetime.now(timezone.utc).isoformat(),
               'scope': '按行情发生日截至所选日重建；不证明缓存当时已入库',
               'units': {'daily_amount': '千元', 'daily_volume': '手',
                         'moneyflow_amount': '万元', 'turnover_rate': '%'}}

    def rows(name, query, fields=None):
        labels = {'stock_basic': '证券基础资料', 'market_daily_bars': '历史日线',
                  'daily_basic': '历史换手与市值', 'daily_price_limits': '涨跌停价格',
                  'moneyflow': '历史资金流', 'review_board_pools': '涨停名单',
                  'review_strong_stock_snapshots': '历史核心名单',
                  'review_opening_auctions': '竞价缓存', 'minute_bars_daily': '分钟行情缓存'}
        report(f'读取{labels.get(name, name)}')
        # Remote Mongo round trips dominate these full-market windows.
        return list(store.collection(name).find(query, {'_id': 0, **(fields or {})}, batch_size=10000))

    future_calendar = store.find_trade_calendar(start_date=target, end_date=(date.fromisoformat(target) + timedelta(days=20)).isoformat())
    dataset['next_date'] = next((str(r.get('cal_date')) for r in sorted(future_calendar, key=lambda r: str(r.get('cal_date')))
        if str(r.get('is_open')) == '1' and str(r.get('cal_date')).replace('-', '') > target.replace('-', '')), None)
    if dataset['next_date']:
        d = dataset['next_date'].replace('-', '')
        dataset['next_date'] = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
    dataset['basics'] = rows('stock_basic', {}, {k: 1 for k in
        ('ts_code', 'name', 'list_date', 'delist_date', 'industry', 'market')})
    dataset['bars'] = rows('market_daily_bars', {'date': {'$in': sessions}}, {k: 1 for k in
        ('date', 'ticker', 'open', 'high', 'low', 'close', 'pre_close', 'volume', 'amount', 'source')})
    dataset['daily_basic'] = rows('daily_basic', {'trade_date': {'$in': compact}}, {k: 1 for k in
        ('trade_date', 'ts_code', 'turnover_rate', 'turnover_rate_f', 'total_mv', 'float_share', 'circ_mv')})
    dataset['limits'] = rows('daily_price_limits', {'trade_date': {'$in': compact + sessions}},
                            {'ticker': 1, 'trade_date': 1, 'up_limit': 1, 'pre_close': 1})
    dataset['flows'] = rows('moneyflow', {'trade_date': {'$in': compact[-7:]}}, {k: 1 for k in
        ('trade_date', 'ts_code', 'net_mf_amount', 'buy_lg_amount', 'buy_elg_amount', 'sell_lg_amount', 'sell_elg_amount')})
    dataset['pools'] = rows('review_board_pools', {'date': {'$in': sessions}, 'kind': 'up',
                            'complete': True, 'snapshot_mode': {'$ne': 'live'}})
    report('读取盘后复盘与板块缓存')
    dataset['review'] = store.collection('daily_review_snapshots').find_one(
        {'analysis_date': target}, {'_id': 0}, sort=[('generated_at', -1)]) or {}
    dataset['sector_cache'] = store.collection('sector_trends_cache').find_one(
        {'analysis_date': target, 'benchmark_ticker': '000001.SH'}, {'_id': 0},
        sort=[('computed_at', -1)]) or {}
    from shilun.market.taxonomy import load_taxonomy
    report('读取板块分类与成分')
    dataset['taxonomy'] = load_taxonomy(store, target)
    from shilun.review.board_data import BoardResearchData, REASON_COLLECTION
    from shilun.review.event_theme_taxonomy import daily_direction_rows, VERSION as EVENT_SCHEMA
    reason_doc = store.collection(REASON_COLLECTION).find_one({'date': target}, {'_id': 0}) or {}
    pool = BoardResearchData(store).pools([target], settled=True).get(target, {})
    dataset['event_themes'] = {'date': target, 'topic_schema': EVENT_SCHEMA,
                             'rows': daily_direction_rows(pool, reason_doc, target)}
    dataset['core_snapshots'] = rows('review_strong_stock_snapshots', {'date': {'$in': sessions}})
    # Warm-up is separate from the 20-session event window. Raw history permits
    # prefix reconstruction of cores that failed before today's snapshot.
    core_start = (date.fromisoformat(target) - timedelta(days=270)).isoformat()
    report('读取历史核心重建窗口')
    core_days = load_review_sessions(store, target, core_start)[-120:]
    from shilun.review.suspensions import load_suspensions
    dataset['core_history'] = {'sessions': core_days,
        'bars': rows('market_daily_bars', {'date': {'$in': core_days}}, {k: 1 for k in
            ('date', 'ticker', 'open', 'high', 'low', 'close', 'pre_close', 'volume')}),
        'limits': rows('daily_price_limits', {'trade_date': {'$in': core_days + [d.replace('-', '') for d in core_days]}},
            {'ticker': 1, 'trade_date': 1, 'up_limit': 1, 'pre_close': 1}),
        'suspensions': {t: sorted(ds) for t, ds in load_suspensions(store, core_days).items()}}
    dataset['auctions'] = rows('review_opening_auctions', {'date': {'$in': sessions[-2:]}})
    dataset['minutes'] = rows('minute_bars_daily', {'trade_date': {'$in': compact[-2:]}, 'interval': '5'})
    return dataset


def profile(data: dict) -> dict:
    target, sessions = data['date'], data['sessions']
    datasets = {}
    for label, fields, day in (
        ('bars', ('ticker', 'date'), 'date'),
        ('daily_basic', ('ts_code', 'trade_date'), 'trade_date'),
        ('limits', ('ticker', 'trade_date'), 'trade_date'),
        ('flows', ('ts_code', 'trade_date'), 'trade_date'),
        ('minutes', ('ticker', 'trade_date', 'interval'), 'trade_date')):
        records = data[label]
        counts = Counter(tuple(str(r.get(k, '')) for k in fields) for r in records)
        dated = Counter(str(r.get(day, '')).replace('-', '')[:8] for r in records)
        datasets[label] = {'rows': len(records), 'unique_keys': len(counts),
            'duplicate_rows': sum(n - 1 for n in counts.values()), 'rows_by_date': dict(sorted(dated.items())),
            'missing_dates': [d for d in (sessions[-2:] if label == 'minutes' else sessions[-7:] if label == 'flows' else sessions)
                              if d.replace('-', '') not in dated]}
    bars = [r for r in data['bars'] if r['date'] == target]
    basic = [r for r in data['daily_basic'] if r['trade_date'] == target.replace('-', '')]
    datasets['bars']['invalid_ohlc'] = sum(not all(isinstance(r.get(k), (int, float)) and r[k] > 0
        for k in ('open', 'high', 'low', 'close')) or not (r.get('low', 0) <= min(r.get('open', 0), r.get('close', 0))
        <= max(r.get('open', 0), r.get('close', 0)) <= r.get('high', 0)) for r in bars)
    datasets['daily_basic']['turnover_nonnull'] = sum(r.get('turnover_rate') is not None for r in basic)
    return {'date': target, 'sessions': sessions, 'datasets': datasets,
        'review': {k: data['review'].get(k) for k in ('analysis_date', 'status', 'engine_version', 'generated_at')},
        'sector_cache': {k: data['sector_cache'].get(k) for k in ('analysis_date', 'engine_version', 'computed_at')},
        'taxonomy': {k: data['taxonomy'].get(k) for k in ('date', 'status', 'version', 'member_count', 'fetched_at')},
        'taxonomy_rows': len(data['taxonomy'].get('rows', [])),
        'pool_dates': sorted({r['date'] for r in data['pools']}),
        'core_dates': sorted({r.get('date', '') for r in data['core_snapshots']}),
        'auction_coverage': [{'date': r['date'], 'count': len(r.get('amounts', {})),
                              'fields': list(r), 'source': r.get('source')} for r in data['auctions']],
        'point_in_time_note': data['scope']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=date.fromisoformat)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    from shilun.api.review import _open_store
    store = _open_store()
    try:
        print('读取日期限定的原始缓存…', flush=True)
        data = read_dataset(store, args.date.isoformat())
    finally:
        store.close()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.output_dir / 'dataset.json.gz', 'wt', encoding='utf-8', compresslevel=1) as f:
        json.dump(data, f, ensure_ascii=False, default=str, allow_nan=False)
    result = profile(data)
    (args.output_dir / 'coverage.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + '\n')
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)


if __name__ == '__main__':
    main()
