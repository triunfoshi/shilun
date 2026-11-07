"""Daily, prefix-only core reconstruction using the existing P2 qualification rule.

Rebuild from the whole security directory, never from today's survivors. Yesterday's
frozen membership remains in the feedback population after a core fails. No database writes.
"""
from collections import defaultdict

from shilun.market.ma5_universe import iso
from shilun.market.research_scope import stock_allowed
from shilun.review.strong_stock_candidates import evaluate_stock, VERSION, RULE
from shilun.review.strong_stock_data import a_share, listing_no_limit_days


def reconstruct_cores(data, evidence):
    history = data.get('core_history') or {}
    days = sorted({d for d in history.get('sessions', evidence.days) if d <= evidence.date})
    bars, limits = defaultdict(dict), defaultdict(dict)
    for r in history.get('bars', data['bars']):
        d = iso(r.get('date'))
        if d in days:
            if d in bars[r['ticker']]:
                raise ValueError('历史核心日线存在重复证券日期')
            bars[r['ticker']][d] = r
    for r in history.get('limits', data['limits']):
        d, t = iso(r.get('trade_date')), r.get('ticker') or r.get('ts_code')
        if d in days:
            if d in limits[t] and limits[t][d].get('up_limit') != r.get('up_limit'):
                raise ValueError('历史核心涨停价存在同日冲突')
            limits[t][d] = r
    snapshots = {d: [] for d in evidence.days}
    evaluated = 0
    for meta in data['basics']:
        ticker = meta['ts_code']
        if not a_share(ticker):
            continue
        # Necessary superset only: this cannot exclude a stock qualified on any
        # earlier prefix. Membership itself is recalculated at each day's close.
        sealed = sum(r.get('up_limit') is not None and bars[ticker].get(d, {}).get('close') is not None
                     and abs(bars[ticker][d]['close'] - r['up_limit']) < .005 for d, r in limits[ticker].items())
        if sealed < 5:
            continue
        stock_days = [d for d in days if not meta.get('list_date') or d >= iso(meta['list_date'])]
        no_limit = listing_no_limit_days(meta, stock_days)
        evaluated += 1
        for day in evidence.days:
            if day not in stock_days or not stock_allowed(meta, day):
                continue
            result = evaluate_stock(ticker, stock_days, day, bars[ticker], limits[ticker],
                suspensions=history.get('suspensions', {}).get(ticker, ()), no_limit_dates=no_limit)
            cycle = result.get('current_cycle') or {}
            recent = days[max(0, days.index(day)-6):days.index(day)+1]
            if result['status'] != 'qualified' or result['latest_limit_date'] not in recent or not cycle.get('price_complete'):
                continue
            record = {'ticker': ticker, 'name': meta.get('name') or ticker, 'date': day,
                'qualified_at': result['qualified_at'], 'cycle_id': cycle['cycle_id'],
                'latest_limit_date': result['latest_limit_date'], 'drawdown_pct': (cycle.get('drawdown') or 0)*100,
                'current_streak': result['current_streak']}
            snapshots[day].append(record)
    return {'snapshots': snapshots, 'current': snapshots.get(evidence.date, []),
        'method': {'version': VERSION, 'rule': RULE, 'history_from': days[0] if days else None,
            'history_sessions': len(days), 'evaluated_stocks': evaluated,
            'reconstructed': True, 'note': '逐日仅使用当日及以前行情重建独立核心资格，并要求近7日仍有涨停活动；每日反馈使用前一日名单，同股去重。昨日名单的后续失败仍保留在次日反馈中。核心群体作为抱团观察代理，并非当时人工指定名单。证券名称及目录为现有归档，历史ST及退市覆盖仍有限。'}}
