"""Settled, same-scope board ecology facts; no strategy ranking or simulated fills."""
from statistics import mean, median

from shilun.review.board_effects import seal_time
from shilun.review.sources import finite_number


def number(value):
    return None if isinstance(value, bool) else finite_number(value)


def positive(value):
    value = number(value)
    return value if value is not None and value > 0 else None


def whole(value):
    value = number(value)
    return int(value) if value is not None and value >= 0 and value == int(value) else None


def height(value):
    value = whole(value)
    return value if value is not None and value >= 1 else None


def build_day_facts(*, day, previous, ups, broken, bars=None, limits=None, suspended=None):
    # Reuse the existing provider/universe contract, keeping ST and Beijing out
    # of the comparable history rather than mixing raw pool counts.
    from shilun.review.board_dynamics import comparable_pools, _stocks, _scope
    pools, failed_pools = comparable_pools(ups), comparable_pools(broken)
    old, current, failed = _stocks(pools, previous), _stocks(pools, day), _stocks(failed_pools, day)
    old_scope = _scope(pools.get(previous, {})) if old is not None else None
    current_scope = _scope(pools.get(day, {})) if current is not None else None
    comparable = old is not None and current is not None and old_scope == current_scope == '沪深非ST'
    broken_ready = current is not None and failed is not None and _scope(failed_pools[day]) == current_scope == '沪深非ST'
    bars, limits, suspended = bars or {}, limits or {}, suspended or set()
    old_rows = list(old.values()) if old is not None and old_scope == '沪深非ST' else None
    today_rows = list(current.values()) if current is not None and current_scope == '沪深非ST' else None
    count = len(old_rows) if old_rows is not None else None
    promoted = len(old.keys() & current.keys()) if comparable else None
    overall = {'base': count, 'promoted': promoted,
               'rate': promoted / count if comparable and count else None}
    stages = []
    for key, label, include in [('one_two', '1进2', lambda h:h == 1),
            ('two_three', '2进3', lambda h:h == 2), ('three_plus', '3板及以上晋级', lambda h:h >= 3)]:
        group = [r for r in old_rows or [] if (h := height(r.get('boards'))) is not None and include(h)]
        base = len(group) if old_rows is not None else None
        success = sum(r['ticker'] in current for r in group) if comparable else None
        stages.append({'key': key, 'label': label, 'base': base, 'promoted': success,
                       'rate': success / base if success is not None and base else None})
    unknown_height = sum(height(r.get('boards')) is None for r in old_rows or [])
    values, exceptions = [], []
    for row in old_rows or []:
        ticker = row['ticker']
        bar, limit = bars.get(ticker, {}), limits.get(ticker, {})
        close = positive(bar.get('close'))
        reference = positive(bar.get('pre_close')) or positive(limit.get('pre_close'))
        if ticker in suspended:
            reason = '全天停牌'
        elif close is None:
            reason = '当日收盘行情缺失'
        elif reference is None:
            reason = '当日前收参考价缺失'
        else:
            values.append((close/reference-1)*100)
            continue
        exceptions.append({'ticker':ticker, 'name':row.get('name',ticker), 'reason':reason})
    money = {'status': 'missing' if old_rows is None else 'partial' if exceptions else 'ready',
        'median_pct': median(values) if values else None, 'mean_pct': mean(values) if values else None,
        'up_rate': sum(v > 0 for v in values)/len(values) if values else None,
        'up_count': sum(v > 0 for v in values) if values else None,
        'sample_n': len(values), 'total_n': count,
        'missing_n': len(exceptions), 'suspended_n':sum(r['reason'] == '全天停牌' for r in exceptions),
        'repeat_limit_n': promoted, 'repeat_limit_base': count,
        'repeat_limit_rate': overall['rate'], 'exceptions':exceptions,
        'note': '昨日完整收盘涨停名单冻结后，逐股按今日收盘／今日前收参考价−1计算；先算收益再取中位数或均值。失利样本保留，停牌和缺行情单列。'}
    total = len(today_rows) if today_rows is not None else None
    breaks = [whole(r.get('break_count')) for r in today_rows or []]
    known_breaks = [v for v in breaks if v is not None]
    break_ready = today_rows is not None and len(known_breaks) == total
    seals = [seal_time(r.get('seal_time')) for r in today_rows or []]
    known_seals = [v for v in seals if v]
    seal_ready = today_rows is not None and len(known_seals) == total
    broken_count = len(failed.keys() - current.keys()) if broken_ready else None
    attempts = total + broken_count if broken_count is not None else None
    quality = {'status': 'missing' if today_rows is None else 'ready' if break_ready and seal_ready and broken_ready else 'partial',
        'total': total,
        'never_broken': sum(v == 0 for v in known_breaks) if break_ready else None,
        'reopened': sum(v > 0 for v in known_breaks) if break_ready else None,
        'never_broken_rate': sum(v == 0 for v in known_breaks)/total if break_ready and total else None,
        'known_never_broken':sum(v == 0 for v in known_breaks), 'known_reopened':sum(v > 0 for v in known_breaks),
        'break_count_valid_n':len(known_breaks),
        'opening_seconds':sum(v <= '093500' for v in known_seals) if seal_ready else None,
        'late_seal':sum(v >= '143000' for v in known_seals) if seal_ready else None,
        'seal_time_valid_n':len(known_seals),
        'broken_pool':broken_count, 'attempted':attempts,
        'broken_rate':broken_count/attempts if attempts else None,
        'avg_broken_times':mean(known_breaks) if break_ready and total else None,
        'note': '未炸与炸后回封按当天最终封板股的炸板次数分组，互补且不重复；首封≤09:35与≥14:30是另一个交叉维度，早封不保证秒级可成交。平均炸次以最终封板组为分母。炸板率分母为最终封板＋炸板未回封。'}
    return {'date':day, 'cohort_date':previous, 'scope':'沪深非ST', 'snapshot_mode':'settled',
        'money_effect':money,
        'promotion':{'status':'ready' if comparable and not unknown_height else 'partial' if comparable else 'missing',
            'overall':overall, 'stages':stages, 'unknown_height_n':unknown_height},
        'seal_quality':quality,
        'source':pools.get(day, {}).get('source'), 'cohort_source':pools.get(previous, {}).get('source')}
