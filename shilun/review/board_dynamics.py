"""Settled daily board rates and a small, explicitly grouped continuation estimate."""
from collections import defaultdict
from math import sqrt

from shilun.review.board_data import settled_target, _day
from shilun.review.suspensions import load_suspensions


def market_group(row):
    code = row.get('code') or row['ticker'].split('.')[0]
    if code.startswith(('300', '301')):
        return '创业板'
    if code.startswith(('688', '689')):
        return '科创板'
    if row['ticker'].endswith('.BJ'):
        return '北交所'
    return '主板ST' if 'ST' in row.get('name', '').upper() else '主板非ST'


def _stocks(pools, day):
    doc = pools.get(day)
    if not doc or not doc.get('complete') or doc.get('snapshot_mode') == 'live':
        return None
    return {r['ticker']: r for r in doc['stocks']}


def _scope(doc):
    # Tushare historical fallback excludes ST and Beijing; do not splice its
    # denominator into the Eastmoney full-pool series.
    return doc.get('metric_scope') or ('沪深非ST' if doc.get('source') == 'tushare_limit_list_d' else '东财完整池')


def comparable_pools(pools):
    """Intersect supported universes before combining providers, retaining provenance."""
    result = {}
    for day, doc in pools.items():
        # Never guess a universe for an unknown provider or incomplete archive.
        if doc.get('source') not in ('tushare_limit_list_d', 'eastmoney_dated_pool', 'shilun_limit_up_pool'):
            result[day] = doc
            continue
        result[day] = {**doc, 'metric_scope': '沪深非ST', 'stocks': [
            r for r in doc.get('stocks', []) if r.get('ticker', '').endswith(('.SH', '.SZ'))
            and 'ST' not in r.get('name', '').upper()]}
    return result


def _estimate(success, count):
    if count < 5:
        return {'probability': None, 'interval': None, 'status': 'insufficient'}
    p, z = success / count, 1.96
    centre = (p + z*z/(2*count)) / (1 + z*z/count)
    radius = z * sqrt(p*(1-p)/count + z*z/(4*count*count)) / (1 + z*z/count)
    return {'probability': (success+1)/(count+2),
            'interval': [max(0, centre-radius), min(1, centre+radius)],
            'status': 'low_sample' if count < 30 else 'available'}


def build_dynamics(*, target, dates, ups, broken, prices, suspended=None, days=30):
    """Only fully observed next-session outcomes on/before target can train."""
    dates = sorted({d for d in dates if d <= target})[-(days+1):]
    rate_ups, rate_broken = comparable_pools(ups), comparable_pools(broken)
    suspended = suspended or {}
    history, groups = [], defaultdict(lambda: {'success': 0, 'sample': 0, 'missing': 0, 'suspended': 0})
    for previous, day in zip(dates, dates[1:]):
        old, current, failed = _stocks(rate_ups, previous), _stocks(rate_ups, day), _stocks(rate_broken, day)
        comparable = old is not None and current is not None and _scope(rate_ups[previous]) == _scope(rate_ups[day])
        broken_comparable = current is not None and failed is not None and _scope(rate_ups[day]) == _scope(rate_broken[day])
        promoted = len(old.keys() & current.keys()) if comparable else None
        failed_count = len(failed.keys() - current.keys()) if broken_comparable else None
        attempts = len(current) + failed_count if broken_comparable else None
        history.append({'date': day, 'cohort_date': previous,
            'promotion_rate': promoted / len(old) if comparable and old else None,
            'promoted': promoted, 'previous_up': len(old) if old is not None else None,
            'broken_rate': failed_count / attempts if attempts else None,
            'broken': failed_count, 'attempted': attempts,
            'up': len(current) if current is not None else None,
            'source': ups.get(day, {}).get('source'),
            'scope': _scope(rate_ups[day]) if current is not None else None,
            'broken_source': broken.get(day, {}).get('source'),
            'promotion_status': 'ok' if comparable and old else 'no_sample' if comparable else 'missing_or_scope_changed',
            'broken_status': 'ok' if attempts else 'no_sample' if broken_comparable else 'missing_or_scope_changed'})
        # Individual market-group forecasts retain their original cohorts.
        old, current = _stocks(ups, previous), _stocks(ups, day)
        for ticker, row in (old or {}).items():
            height = row.get('boards')
            if not isinstance(height, int) or height < 2:
                continue
            stat = groups[(height, market_group(row))]
            if day in suspended.get(ticker, set()):
                stat['suspended'] += 1
            elif current is None or (_scope(ups[day]) == '沪深非ST' and market_group(row) in ('主板ST', '北交所')):
                stat['missing'] += 1
            elif ticker in current:
                stat['sample'] += 1
                stat['success'] += 1
            elif (day, ticker) in prices:
                stat['sample'] += 1
            else:
                stat['missing'] += 1
    estimates = {}
    for ticker, row in (_stocks(ups, target) or {}).items():
        height = row.get('boards')
        if not isinstance(height, int) or height < 2:
            continue
        group = market_group(row)
        stat = dict(groups[(height, group)])
        estimates[ticker] = {'ticker': ticker, 'name': row.get('name'), 'boards': height,
            'market_group': group, **stat, **_estimate(stat['success'], stat['sample'])}
    return {'date': target, 'days_requested': days, 'days_used': len(history),
        'window_start': history[0]['date'] if history else None,
        'window_end': history[-1]['date'] if history else None,
        'daily': history, 'estimates': estimates, 'version': 'board-dynamics-v2-common-scope',
        'probability_method': '同板数、同市场类别；近30个反馈交易日，(次日收盘封板次数+1)/(有效样本数+2)。少于5例不显示概率，少于30例标记低样本。停牌和行情缺失剔除；不使用所选日以后的结果。',
        'interval_note': '95% Wilson参考区间基于原始封板频率；同日、同股样本相关，区间仅作粗略参考。本版未做个股差异建模或样本外校准，不代表收益概率。'}


def dynamics_payload(data, target=None):
    requested = target or data._latest()
    settled = settled_target(requested)
    dates = data.sessions(settled, 30)
    ups = data.pools(dates, settled=True)
    broken = data.pools(dates, 'broken', settled=True)
    tickers = sorted({r['ticker'] for p in ups.values() for r in p['stocks'] if (r.get('boards') or 0) >= 2})
    prices = {(r['date'], r['ticker']) for r in data.store.collection('market_daily_bars').find(
        {'date': {'$in': dates}, 'ticker': {'$in': tickers}, 'close': {'$gt': 0}},
        {'_id': 0, 'date': 1, 'ticker': 1})}
    result = build_dynamics(target=settled, dates=dates, ups=ups, broken=broken, prices=prices,
                            suspended=load_suspensions(data.store, dates))
    from shilun.review.board_day_facts import build_day_facts
    previous = dates[-2] if len(dates) >= 2 and dates[-1] == settled else None
    # The three fact cards need only yesterday's frozen cohort on this date,
    # not another full-market/history download or a review rebuild.
    cohort = comparable_pools(ups).get(previous, {}).get('stocks', [])
    cohort_tickers = sorted({r['ticker'] for r in cohort})
    rows = list(data.store.collection('market_daily_bars').find(
        {'date': settled, 'ticker': {'$in': cohort_tickers}},
        {'_id': 0, 'ticker': 1, 'close': 1, 'pre_close': 1})) if cohort_tickers else []
    if len({r['ticker'] for r in rows}) != len(rows):
        raise ValueError('同股同日行情重复，昨日涨停反馈停止计算')
    limit_rows = list(data.store.collection('daily_price_limits').find(
        {'trade_date': {'$in': [settled, settled.replace('-', '')]}, 'ticker': {'$in': cohort_tickers}},
        {'_id': 0, 'ticker': 1, 'pre_close': 1})) if cohort_tickers else []
    suspended = load_suspensions(data.store, [settled])
    result['selected_day'] = build_day_facts(day=settled, previous=previous, ups=ups, broken=broken,
        bars={r['ticker']:r for r in rows}, limits={r['ticker']:r for r in limit_rows},
        suspended={ticker for ticker, days in suspended.items() if settled in days}) if requested == settled else {
            'date': requested, 'available': False, 'scope': '沪深非ST',
            'reason': '所选日尚未进入完整收盘统计；上一个已收盘日只用于下方历史趋势，不代替所选日。'}
    # Preserve the selected date; an intraday request must not inherit yesterday's forecasts.
    result['date'] = requested
    if requested != settled:
        result['estimates'] = {}
    next_row = data.store.collection('trade_calendar').find_one(
        {'cal_date': {'$gt': requested.replace('-', '')}, 'is_open': {'$in': [1, '1', True]}},
        {'_id': 0, 'cal_date': 1}, sort=[('cal_date', 1)])
    result['next_date'] = _day(next_row['cal_date']) if next_row else None
    return result
