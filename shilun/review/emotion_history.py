"""As-of-date emotion history; all prices and cohorts stop at the review date."""
from math import isfinite, prod
from statistics import median


def number(value):
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (ValueError, TypeError):
        return None


def level(row):
    return int(number(row.get('consecutive_count')) or 1)


from shilun.review.theme_scope import topic_name as group


def return_path(ticker, start, end, dates, bars, *, include_start=False):
    """Chain daily pre-close returns; disclose any raw-close fallback.

    Startup includes the first board day's return. Crown tracking starts at that
    day's close, exactly as Vibe's economic definition. Missing sessions break
    the chain rather than being compressed out of the chart.
    """
    days = [d for d in dates if start <= d <= end]
    by_date = {str(r.get('date'))[:10]: r for r in bars.get(ticker, [])}
    out, factors, valid, raw_close_fallback = [], [], True, False
    if not days or days[0] != start or days[-1] != end:
        return {'series': [], 'return': None, 'peak': None, 'drawdown': None, 'complete': False}
    for i, day in enumerate(days):
        row = by_date.get(day, {})
        close = number(row.get('close'))
        prior = number(row.get('pre_close'))
        if prior is None and (i or include_start):
            calendar_index = dates.index(day)
            if calendar_index > 0:
                prior = number(by_date.get(dates[calendar_index - 1], {}).get('close'))
                raw_close_fallback = raw_close_fallback or prior is not None
        if close is None or close <= 0 or ((i or include_start) and (prior is None or prior <= 0)):
            valid = False
        if valid:
            if i or include_start:
                factors.append(close / prior)
            value = prod(factors) - 1
        else:
            value = None
        out.append({'date': day, 'cum_return': value})
    values = [r['cum_return'] for r in out if r['cum_return'] is not None]
    current = out[-1]['cum_return']
    peak = max(values) if valid and values else None
    drawdown = (1 + current) / (1 + peak) - 1 if valid and len(days) > 1 and peak is not None else None
    return {'series': out, 'return': current, 'peak': peak, 'drawdown': drawdown, 'complete': valid,
            'price_basis': 'raw_close_fallback' if raw_close_fallback else 'daily_pre_close'}


def round_evidence(name, active_tickers, pools, dates, end, bars):
    """Trace the active group's round, not a seven/ten-day stock maximum.

    A round boundary is an auditable proxy: ten *verified* consecutive sessions
    with no boards in this group. Unknown pools never prove a quiet interval.
    With no boundary, mark the left-censored lookback instead of claiming an
    exact catalyst date. Theme attribution still needs independent validation.
    """
    dates = [d for d in dates if d <= end]
    # An unknown interval is not a proven new round, but it also cannot prove
    # that an isolated older peak belongs to this round. Expose the censoring.
    missing_history = [d for d in dates if d not in pools]
    observed_start = max((i + 1 for i, d in enumerate(dates) if d not in pools), default=0)
    quiet, boundary = 0, None
    for i, d in enumerate(dates):
        if i < observed_start:
            continue
        if d not in pools:
            quiet = 0
        elif any(group(r) == name for r in pools[d]):
            quiet = 0
        else:
            quiet += 1
            if quiet >= 10:
                boundary = i + 1
    round_dates = dates[boundary if boundary is not None else observed_start:]
    observations = [(d, r) for d in round_dates for r in pools.get(d, [])
                    if r.get('ticker') in active_tickers and group(r) == name]
    highest = max((level(r) for _, r in observations), default=None)
    candidates = []
    for ticker in sorted(active_tickers):
        history = [(d, r) for d, r in observations if r['ticker'] == ticker]
        if not history:
            continue
        first, row = history[0]
        first_index = dates.index(first) - level(row) + 1
        start = dates[first_index] if first_index >= 0 else None
        path = return_path(ticker, start, end, dates, bars, include_start=True) if start else {'return': None, 'drawdown': None, 'series': [], 'complete': False}
        candidate = {'ticker': ticker, 'name': row.get('name') or ticker,
            'max_board': max(level(r) for _, r in history), 'start_date': start,
            'first_observed_date': first, 'latest_board_date': history[-1][0],
            'startup_return': path['return'], 'drawdown': path['drawdown'],
            'price_basis': path.get('price_basis'),
            'return_complete': path['complete'], 'series': path['series'],
            'active_board_days': len(history), 'status': 'candidate',
            'reason': '高度与涨幅提供辨识度线索；题材带动性、消息持续发酵与分歧换手尚待验证。'}
        candidates.append(candidate)
    candidates.sort(key=lambda r: (r['max_board'], r['startup_return'] if r['startup_return'] is not None else -2,
                                   r['active_board_days']), reverse=True)
    from shilun.review.topic_attention import attention_core
    core = attention_core(candidates, end)
    missing = [d for d in round_dates if d not in pools]
    return {'max_board': highest, 'max_board_stocks': [{'ticker': r['ticker'], 'name': r.get('name') or r['ticker'], 'date': d}
                for d, r in observations if level(r) == highest],
        'window_start': round_dates[0] if round_dates else None, 'boundary_confirmed': boundary is not None,
        'missing_dates': missing, 'history_gap_dates': missing_history,
        'left_censored': boundary is None, 'complete': boundary is not None and not missing,
        'leaders': candidates[:3], 'attention_core': core, 'active_stock_count': len(active_tickers),
        'rule': '近7交易日活跃股，追溯同方向本轮最高连板，不截断在7日或10日。连续10个已核验交易日全方向无涨停，作为分轮代理；历史缺口以前的孤立高标不强行串入本轮。未找到静默边界时最高板仅为已核验窗口下限（≥），不是完整本轮最高认证。龙头候选按本轮高度、启动累计涨幅、活跃天数排序；题材带动性与分歧换手需另行验证。'}


def five_day_heat(end, dates, pools, bars, summaries=None):
    days, seen, lineage = [], set(), []
    dates = [d for d in dates if d <= end]
    for d in dates[-5:]:
        rows = pools.get(d)
        summary = (summaries or {}).get(d, {})
        count = len(rows) if rows is not None else number(summary.get('limit_up'))
        highest = max((level(r) for r in rows), default=0) if rows is not None else number(summary.get('highest_consec'))
        summary_agrees = number(summary.get('limit_up')) == count and number(summary.get('highest_consec')) == highest
        rate = number(summary.get('broken_rate')) if summary_agrees or summary.get('metric_scope') == '沪深非ST' else None
        rate = rate if rate is not None and 0 <= rate <= 1 else None
        leaders = [{**r, 'boards': level(r)} for r in (rows or []) if level(r) == highest]
        days.append({'date': d, 'limit_up': count, 'highest_consec': highest, 'broken_rate': rate,
                     'leaders': leaders, 'leaders_complete': rows is not None,
                     'source': next((r.get('source') for r in (rows or []) if r.get('source')), 'dated_limit_up_pool'),
                     'broken_scope': summary.get('metric_scope'),
                     'broken_source': summary.get('source') if rate is not None else None})
        for leader in leaders:
            ticker = leader['ticker']
            if ticker in seen:
                continue
            seen.add(ticker)
            path = return_path(ticker, d, end, dates, bars)
            lineage.append({'ticker': ticker, 'name': leader.get('name') or ticker, 'theme': group(leader),
                'appear_date': d, 'boards_then': highest, 'cum_return_since': path['return'],
                'peak_cum_return': path['peak'], 'drawdown_from_peak': path['drawdown'],
                'series': path['series'], 'complete': path['complete'], 'price_basis': path.get('price_basis')})
    tops = {r['ticker'] for r in days[-1]['leaders']} if days else set()
    for row in lineage:
        row['is_current_top'] = row['ticker'] in tops
    return {'days': days, 'leader_lineage': lineage,
        'rule': '所选复盘日及此前4个交易日。涨停池最高板并列全部保留；登顶后的累计收益和距最高收盘点回撤，优先逐日昨收收益连乘，昨收缺失时以相邻收盘价计算并标注未复权。不是题材龙头认证。'}


def load_heat_summaries(store, dates):
    from shilun.review.board_data import BoardResearchData
    from shilun.review.board_dynamics import comparable_pools, _stocks, _scope
    summaries = {r['date']: r for r in store.collection('review_heat_summaries').find({'date': {'$in': dates}}, {'_id': 0})}
    data = BoardResearchData(store)
    ups = comparable_pools(data.pools(dates, settled=True))
    broken = comparable_pools(data.pools(dates, 'broken', settled=True))
    for day in dates:
        current, failed = _stocks(ups, day), _stocks(broken, day)
        if current is None or failed is None or _scope(ups[day]) != _scope(broken[day]):
            continue
        count = len(failed.keys() - current.keys())
        attempts = len(current) + count
        summaries[day] = {'date': day, 'limit_up': len(current),
            'highest_consec': max((r.get('boards') or 0 for r in current.values()), default=0),
            'broken_rate': count / attempts if attempts else None,
            'metric_scope': _scope(ups[day]), 'source': broken[day]['source']}
    return summaries
