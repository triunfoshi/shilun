"""Complete, dated yesterday -> today -> next-session promotion cohorts."""
from datetime import datetime
import os
import requests

from shilun.common.config import _read_env_file
from shilun.review.board_data import _day, today, settled_target, CHINA
from shilun.review.board_effects import return_pct

COLLECTION = 'review_ladder_sources'


def refresh_reference(store):
    key = os.getenv('FUYAO_API_KEY') or _read_env_file().get('FUYAO_API_KEY')
    if not key:
        return {'status': 'unconfigured'}
    try:
        response = requests.get('https://fuyao.aicubes.cn/api/a-share/special-data/limit-up-ladder',
                                headers={'X-api-key': key}, timeout=20, allow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        data = payload.get('data')
        if payload.get('code') != 0 or not isinstance(data, dict) or not isinstance(data.get('item'), list):
            return {'status': 'unavailable'}
        store.collection(COLLECTION).replace_one({'source': 'fuyao'}, {
            'source': 'fuyao', 'fetched_at': datetime.now(CHINA).isoformat(),
            'payload': data}, upsert=True)
        return {'status': 'ok'}
    except (requests.RequestException, ValueError):
        return {'status': 'unavailable'}


def build_ladder(*, target, previous_date, next_date, pools, bars, reference=None):
    """No capped external array is used as the denominator. No latest-price fallback."""
    prior, current = pools.get(previous_date), pools.get(target)
    result = {'date': target, 'previous_date': previous_date, 'next_date': next_date,
              'available': prior is not None and current is not None, 'rows': [],
              'source': '完整日期涨停池 + 对应交易日行情',
              'reason': '昨日或当日完整涨停池尚未归档，刷新后重试。'}
    if not result['available']:
        return result
    if any(not isinstance(r.get('boards'), int) or r['boards'] < 1 for r in prior['stocks'] + current['stocks']):
        result.update(available=False, reason='完整名单中有板数缺失，暂不计算晋级人数。')
        return result
    current_map = {r['ticker']: r for r in current['stocks']}
    following = pools.get(next_date)
    result['next_status'] = 'observed' if following else 'missing' if next_date and next_date <= settled_target() else 'pending'
    next_map = {r['ticker']: r for r in following['stocks']} if following else {}
    def quote(row, day):
        bar = bars.get((day, row['ticker']), {})
        value = return_pct(bar.get('close'), bar.get('pre_close'))
        if value is None:
            dated = pools.get(day, {})
            match = next((r for r in dated.get('stocks', []) if r['ticker'] == row['ticker']), {})
            value = match.get('pct')
        return {'code': row['code'], 'ticker': row['ticker'], 'name': row['name'], 'pct': value,
                'quote_status': 'suspended' if value is None and bar.get('suspended') else 'observed' if value is not None else 'missing'}
    heights = sorted({r['boards'] for r in prior['stocks'] if r.get('boards')}, reverse=True)
    for level in heights:
        cohort = sorted([r for r in prior['stocks'] if r.get('boards') == level], key=lambda r: r['code'])
        promoted = [r for r in cohort if current_map.get(r['ticker'], {}).get('boards') == level + 1]
        next_promoted = [r for r in promoted if next_map.get(r['ticker'], {}).get('boards') == level + 2]
        result['rows'].append({'level': level, 'yesterday': [quote(r, target) for r in cohort],
            'today': [quote(r, target) for r in promoted],
            'next': [quote(r, next_date) for r in next_promoted] if following else None,
            'promotion_rate': len(promoted) / len(cohort),
            'observed': sum(quote(r, target)['pct'] is not None for r in cohort)})
    ref = (reference or {}).get('payload', {})
    item = next((r for r in ref.get('item', []) if _day(r.get('date')) == target), None)
    remote = {r.get('thscode') for rows in (item or {}).get('boards', {}).values() for r in rows}
    local = {r['ticker'] for r in current['stocks'] if (r.get('boards') or 0) >= 2}
    result['reference'] = {'available': item is not None, 'source': '扶摇 · 同花顺连板天梯',
        'fetched_at': (reference or {}).get('fetched_at'), 'returned_count': len(remote),
        'matched_count': len(remote & local), 'missing_from_reference': sorted(local - remote),
        'only_in_reference': sorted(remote - local), 'board_caps': ref.get('window', {}).get('board_caps'),
        'note': '接口窗口可能裁剪；晋级分母始终来自完整涨停池，不按接口展示容量截断。'}
    result.pop('reason', None)
    return result


def ladder_payload(data, target):
    target = target or data._latest()
    if target > today():
        raise ValueError('不能读取未来日期')
    sessions = data.sessions(target, 1)
    previous = sessions[-2] if len(sessions) >= 2 and sessions[-1] == target else None
    next_row = data.store.collection('trade_calendar').find_one(
        {'cal_date': {'$gt': target.replace('-', '')}, 'is_open': {'$in': [1, '1', True]}},
        {'_id': 0, 'cal_date': 1}, sort=[('cal_date', 1)])
    next_date = _day(next_row['cal_date']) if next_row else None
    dates = [d for d in [previous, target, next_date] if d and d <= today()]
    pools = data.pools(dates)
    tickers = sorted({r['ticker'] for p in pools.values() for r in p['stocks']})
    bars = {(r['date'], r['ticker']): r for r in data.store.collection('market_daily_bars').find(
        {'date': {'$in': dates}, 'ticker': {'$in': tickers}},
        {'_id': 0, 'ticker': 1, 'date': 1, 'close': 1, 'pre_close': 1})}
    # Legacy daily sync dropped pre_close. The dated official price-limit
    # reference preserves ex-rights adjustments; do not infer it from raw closes.
    for row in data.store.collection('daily_price_limits').find(
        {'trade_date': {'$in': dates + [d.replace('-', '') for d in dates]}, 'ticker': {'$in': tickers}},
        {'_id': 0, 'ticker': 1, 'trade_date': 1, 'pre_close': 1}):
        bar = bars.get((_day(row['trade_date']), row['ticker']))
        if bar is not None and not bar.get('pre_close'):
            bar['pre_close'] = row.get('pre_close')
    from shilun.review.suspensions import load_suspensions
    for ticker, days in load_suspensions(data.store, dates).items():
        for day in days:
            if (day, ticker) not in bars:
                bars[(day, ticker)] = {'suspended': True}
    reference = data.store.collection(COLLECTION).find_one({'source': 'fuyao'}, {'_id': 0})
    return build_ladder(target=target, previous_date=previous, next_date=next_date,
                        pools=pools, bars=bars, reference=reference)
