"""Post-retreat facts, independent of theme admission and historical stage labels."""
from math import prod
from statistics import median

from shilun.review.theme_observation import daily_return
from shilun.review.theme_selection import finite

VERSION = 'theme-core-tracking-v1'


def period_return(replay, ticker, days):
    values, missing = [], []
    for day in days:
        if day in replay.market.get('suspensions', {}).get(ticker, ()):
            values.append(0.)  # Verified suspension carries the last valuation.
            continue
        value = daily_return(replay, ticker, day)
        if value is None:
            missing.append(day)
        else:
            values.append(value)
    return (prod(1+v for v in values)-1 if not missing else None), missing


def post_exit_feedback(replay, cycle, day):
    anchor = cycle['exit_date']
    days = [d for d in replay.sessions if anchor < d <= day]
    benchmark = replay.market.get('benchmark', {})
    bars = benchmark.get('bars', {})
    benchmark_days = [anchor] + days
    missing_benchmark = [d for d in benchmark_days if not (finite(bars.get(d, {}).get('close'), 0) > 0)]
    benchmark_return = (bars[day]['close']/bars[anchor]['close']-1 if not missing_benchmark else None)
    qualified = replay.qualified(day)
    rows = []
    for ticker in cycle['front'] + [t for t in cycle['back'] if t not in cycle['front']]:
        value, missing = period_return(replay, ticker, days)
        rows.append({'ticker': ticker, 'name': replay.market['stocks'].get(ticker, {}).get('name') or ticker,
                     'role': '入选核心' if ticker == cycle['core']['ticker'] else '原前排' if ticker in cycle['front'] else '原后排',
                     'return': value, 'excess_return': value-benchmark_return if value is not None and benchmark_return is not None else None,
                     'today_return': daily_return(replay, ticker, day), 'missing_dates': missing,
                     'currently_qualified': ticker in qualified})
    core = next(r for r in rows if r['ticker'] == cycle['core']['ticker'])
    back = [r['return'] for r in rows if r['role'] == '原后排']
    back_return = median(back) if back and all(v is not None for v in back) else None
    value, excess = core['return'], core['excess_return']
    if not days:
        label = '退出当日，开始跟踪'
    elif value is None:
        label = '核心行情待补'
    elif excess is None:
        label = '核心表现已记录，大盘对照待补'
    elif value > 0 and excess > 0 and back_return is not None and value > back_return:
        label = '核心相对强势 · 抱团观察'
    elif value > 0:
        label = '核心修复' if excess > 0 else '核心反弹，未跑赢大盘'
    elif excess > 0:
        label = '核心相对抗跌'
    else:
        label = '核心走弱'
    return {'version': VERSION, 'date': day, 'anchor_date': anchor, 'sessions': len(days),
            'label': label, 'core': core, 'rows': rows, 'back_return': back_return,
            'benchmark': {'ticker': benchmark.get('ticker', '000001.SH'), 'name': benchmark.get('name', '上证指数'),
                          'return': benchmark_return, 'missing_dates': missing_benchmark},
            'status': 'partial' if missing_benchmark or any(r['missing_dates'] for r in rows) else 'ready',
            'note': '以退出日收盘为起点，统计至所选复盘日；超额为个股累计涨幅减同期上证涨幅（百分点）。原样本保持不变，停牌日持平，缺行情不补零。抱团观察只描述相对价格表现，不认定资金流入或板块重新发酵。'}
