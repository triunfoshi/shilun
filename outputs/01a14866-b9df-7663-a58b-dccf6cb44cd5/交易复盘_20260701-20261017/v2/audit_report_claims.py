"""Independent numeric spot-checks for v2 narrative; no network or report edits."""
import collections
import hashlib
import json
import math
import statistics as stats
from pathlib import Path

B = Path(__file__).resolve().parent.parent
read = lambda p: json.loads((B / p).read_text())
features = read('v2/episode_features.json')
episodes = {e['episode_id']: e for e in read('episodes_enriched.json')}
bars = read('market/daily_bars.json')
additions = read('v2/market_additions.json')
gap = read('v2/gap_analysis.json')
report_path = B / 'report/src/data.json'
report = json.loads(report_path.read_text())
checks = []

def check(name, actual, expected, tolerance=1e-7):
    if isinstance(expected, (float, int)) and not isinstance(expected, bool):
        ok = actual is not None and abs(actual - expected) <= tolerance
    else:
        ok = actual == expected
    checks.append({'name': name, 'passed': ok, 'actual': actual, 'expected': expected})

by_ticker = collections.defaultdict(list)
lookup = {}
for bar in bars + additions['index_additional_bars']:
    lookup[(bar['ticker'], bar['date'])] = bar
for (ticker, _), bar in lookup.items():
    by_ticker[ticker].append(bar)
for rows in by_ticker.values():
    rows.sort(key=lambda b: b['date'])
market_dates = sorted(b['date'] for b in by_ticker['000001.SH'])
market = {d: (lookup[('000001.SH', d)]['amount'] + lookup[('399107.SZ', d)]['amount']) * 1000 for d in market_dates}

def ticker_for(code):
    return code + ('.SH' if code[0] in '569' else '.SZ')

numeric_mismatches = []
numeric_count = 0
for f in features:
    d = f['first_buy_date']
    ticker = ticker_for(f['code'])
    now = lookup[(ticker, d)]
    prior = [b for b in by_ticker[ticker] if b['date'] < d]
    prices = [b['close'] * b['adj_factor'] / now['adj_factor'] for b in prior]
    expected = {}
    for n in (5, 10, 20, 60):
        if len(prices) >= n:
            expected[f'ma{n}'] = stats.mean(prices[-n:])
            expected[f'price_vs_ma{n}_pct'] = (f['first_buy_price'] / expected[f'ma{n}'] - 1) * 100
        for lag in (1, 5):
            if len(prices) >= n + lag:
                expected[f'ma{n}_slope_{lag}d_pct'] = (stats.mean(prices[-n:]) / stats.mean(prices[-n-lag:-lag]) - 1) * 100
    if len(prior) >= 5:
        expected['stock_prior_5d_avg_amount_yuan'] = stats.mean(b['amount'] for b in prior[-5:]) * 1000
    i = market_dates.index(d)
    prev, prev2 = market_dates[i-1:i+1][0], market_dates[i-2]
    expected['market_previous_day_change_pct'] = (market[prev] / market[prev2] - 1) * 100
    expected['market_previous_vs_20d_ratio'] = market[prev] / stats.mean(market[x] for x in market_dates[i-20:i])
    expected['market_entry_day_change_pct_posthoc'] = (market[d] / market[prev] - 1) * 100
    for key, value in expected.items():
        numeric_count += 1
        if not math.isclose(f[key], value, rel_tol=1e-10, abs_tol=1e-7):
            numeric_mismatches.append({'episode_id': f['episode_id'], 'field': key, 'stored': f[key], 'recomputed': value})
check('prior-only MA / amount / timing fields', numeric_mismatches, [])

chase = [f for f in features if f['entry_pattern'] == '明显追涨或远离5日线']
combo = [f for f in chase if f['market_regime'] == '大盘震荡过渡']
up = [f for f in features if f['entry_pattern'] in ('上升趋势且靠近5日线', '上升趋势其他买入')]
ids = lambda rows: {r['episode_id'] for r in rows}
pnl = lambda rows: round(sum(r['analysis_pnl'] for r in rows), 2)
check('37 subset 104', ids(combo) <= ids(chase), True)
check('28 disjoint 104', bool(ids(up) & ids(chase)), False)
for rows, n, total in ((features,158,-48511.84),(up,28,-33239.50),(chase,104,-11244.06),(combo,37,6425.26)):
    check(f'cohort count {n}',len(rows),n)
    check(f'cohort pnl {n}',pnl(rows),total)
for flag, n, total in [('short_bull_alignment',23,-24822.98),('full_bull_alignment',12,-15916.99),('liquid_over_30yi',15,-19464.44),('market_prior_shrink',15,-21210.70),('market_prior_low20',23,-29937.14),('technology',7,-12214.45)]:
    group = [f for f in up if f[flag]]
    check(flag+' count',len(group),n)
    check(flag+' pnl',pnl(group),total)
check('full alignment plus 5d slopes count',len([f for f in up if f['full_bull_alignment'] and f['full_all_slopes_up_5d']]),9)
check('full alignment plus 5d slopes pnl',pnl([f for f in up if f['full_bull_alignment'] and f['full_all_slopes_up_5d']]),-13466.81)
check('tech shrink and low20 count',len([f for f in up if f['technology'] and f['market_prior_shrink_and_low20']]),3)
check('tech shrink and low20 pnl',pnl([f for f in up if f['technology'] and f['market_prior_shrink_and_low20']]),-9396.34)
check('up mean liquidity yi',round(stats.mean(f['stock_prior_5d_avg_amount_yi'] for f in up),2),58.31)
for label, rows, amount, ret in [('up',up,32.26,-3.50),('chase',chase,31.78,-.33),('combo',combo,37.13,.38)]:
    check(label+' median liquidity yi',round(stats.median(f['stock_prior_5d_avg_amount_yi'] for f in rows),2),amount)
    check(label+' median return pct',round(stats.median(f['analysis_return_pct'] for f in rows),2),ret)

gap_mismatches = []
for row in gap['episodes']:
    ep = episodes[row['episode_id']]
    buys = [e for e in ep['event_details'] if e['kind'] == '证券买入' and e['date'] == ep['first_buy_date']]
    cost = -sum(e['net'] for e in buys)
    qty = sum(e['qty'] for e in buys)
    ticker = ticker_for(ep['code'])
    entry_bar = lookup[(ticker, ep['first_buy_date'])]
    next_date = market_dates[market_dates.index(ep['first_buy_date'])+1]
    next_bar = lookup[(ticker,next_date)]
    change = (next_bar['open'] * next_bar['adj_factor'] / entry_bar['adj_factor'] / (cost/qty) - 1) * 100
    if abs(cost-row['first_day_cost']) > .001 or abs(qty-row['first_day_quantity']) > .001 or abs(change-row['next_open_vs_first_day_cost_pct']) > 1e-7:
        gap_mismatches.append(row['episode_id'])
check('158 first-day fee-weighted cost and adjusted next open',gap_mismatches,[])
low5 = [r for r in gap['episodes'] if r['next_open_vs_first_day_cost_pct'] <= -5]
check('next open -5 count',len(low5),32)
check('next open -5 pnl',round(sum(r['actual_pnl'] for r in low5),2),-45498.64)
check('next open -5 wins',sum(r['actual_pnl'] > 0 for r in low5),0)
check('first-price sensitivity count',sum(r['next_open_vs_first_buy_price_pct'] <= -5 for r in gap['episodes']),31)
check('old13/new32 overlap',sum(r['original_uptrend_entry_down5'] for r in low5),7)
check('new32 next-day adds',sum(r['next_day_add_count'] > 0 for r in low5),4)
eligible = [r for r in low5 if r['strategies']['next_market_open']['eligible']]
check('next-open benchmark eligible count',len(eligible),28)
check('next-open price benchmark pnl',round(sum(r['strategies']['next_market_open']['price_benchmark_pnl'] for r in eligible),2),-32692.55)
check('eligible full actual pnl',round(sum(r['actual_pnl'] for r in eligible),2),-39751.29)
check('eligible same-lot actual pnl',round(sum(r['first_day_actual_pnl'] for r in eligible),2),-31450.93)

narratives = {r['id']:r['text'] for r in report['queries']['narratives']['rows']}
minor = [
    {'id':'inferred_down_limit', 'severity':'wording_precision', 'narrative_id':'trade-v2-gap', 'detail':'The four excluded opening limits were inferred from board/listing rules; exchange limit-price feed was unavailable.', 'recommendation':'Say 按板块规则推算的跌停价; retain queue/execution uncertainty.', 'still_present':'排除4次开盘位于跌停价' in narratives['trade-v2-gap']},
    {'id':'old_intent_label', 'severity':'wording_consistency', 'narrative_id':'trade-actions', 'detail':'Old action paragraph groups 国芳 with trend-pullback failures; new narrative correctly preserves user-supplied 翘板 intent.', 'recommendation':'Separate 东山弱市抄底 from 国芳重仓翘板 in old action text.', 'still_present':'国芳集团与东山精密是应优先对照的失败案例' in narratives['trade-actions']}
]
audit = {
    'status':('passed_with_wording_notes' if any(f['still_present'] for f in minor) else 'passed') if all(c['passed'] for c in checks) else 'failed',
    'report_sha256':hashlib.sha256(report_path.read_bytes()).hexdigest(),
    'reviewed_narratives':[k for k in narratives if k.startswith('trade-v2-')],
    'read_only_report':True,
    'numeric_field_checks':numeric_count,
    'checks':checks,
    'minor_findings':minor,
    'source_files':['episodes_enriched.json','market/daily_bars.json','v2/episode_features.json','v2/market_additions.json','v2/environment_analysis.json','v2/gap_analysis.json','v2/chase_exit_analysis.json','v2/case_intraday_analysis.json','v2/nav_exposure.json','v2/sentiment_analysis.json'],
    'narrative_review': {
        'units_timing':'Stock amount and index amount are thousand CNY, converted ×1000; >30亿元 threshold is >3e9 CNY. Prior-day fields use completed data, entry-day totals explicitly post hoc.',
        'industry':'Current SW labels with membership dates, technology is exactly electronics/computer/communication; ETF separate.',
        'overfitting':'37-subset profit is labelled unstable and August-concentrated, not a validated strategy. Benchmark comparisons distinguish same first-day shares from full episodes.',
        'case_boundaries':'5-minute bars do not verify timestamps or within-bar ordering; narrative explicitly avoids future double-top confirmation and future-high exits. Emotional intent attributed to user, not inferred from statement.',
        'old_report':'No core numeric conflict found with top-level 158 episodes / 61 wins / -48511.84 CNY or 37-subset; old action-intent wording noted separately.',
        'limits':'No new data query, no exchange source revalidation or browser/visual audit; independent arithmetic rechecks documented above; narrative source comparison for the other case/sentiment claims.'
    }
}
(B/'v2/report_claim_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
print(json.dumps({'status':audit['status'],'numeric_fields':numeric_count,'checks':len(checks),'failed':[c for c in checks if not c['passed']],'minor':minor},ensure_ascii=False,indent=2))
