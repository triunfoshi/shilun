#!/usr/bin/env python3
"""Read-only, reproducible next-session analysis of verified zero-to-zero episodes.

This is an event study, not a portfolio backtest or an executable trading rule.
Raw OHLC are used for hypothetical cashflows; adjusted ratios bridge ex-dates.
No new market observations or source-ledger edits are made here.
"""
import bisect
import collections
import csv
import hashlib
import json
import statistics
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

P = Path('/private/tmp/shilun-trade-review-202607')
OUT = P / 'v2'
OUT.mkdir(exist_ok=True)

def read(name):
    return json.loads((P / name).read_text())

EPISODES = [e for e in read('episodes.json') if e['origin'] == 'secondary' and e['status'] == 'closed' and e['initial_cost_known']]
ENRICHED = {e['episode_id']: e for e in read('episodes_enriched.json')}
ROWS = {r['row_id']: r for r in read('normalized_rows.json')}
MATCHES = read('matched_lots.json')
BARS = read('market/daily_bars.json')
BAR = {(b['ticker'][:6], b['date']): b for b in BARS if b['ticker'] not in ['000001.SH', '000300.SH', '399006.SZ']}
BY_CODE = collections.defaultdict(list)
for b in BARS:
    if (b['ticker'][:6], b['date']) in BAR and BAR[(b['ticker'][:6], b['date'])]['ticker'] == b['ticker']:
        BY_CODE[b['ticker'][:6]].append(b)
for values in BY_CODE.values():
    values.sort(key=lambda b: b['date'])
CAL = sorted({f"{d['cal_date'][:4]}-{d['cal_date'][4:6]}-{d['cal_date'][6:]}" for d in read('market/calendar_api.json') if d['is_open'] == 1})
SUSPEND = {(d['ts_code'][:6], f"{d['trade_date'][:4]}-{d['trade_date'][4:6]}-{d['trade_date'][6:8]}") for d in read('market/suspend_raw.json') if d['suspend_type'] == 'S'}
BASIC = {d['symbol']: d for d in read('market/stock_basic_api.json')}
STRATEGIES = ['next_market_open', 'next_market_close', 'third_market_day_open', 'first_available_open_if_suspended']
GROUPS = ['次日开盘较首日含费均价跌≥5%', '次日开盘较首日含费均价跌2%至不足5%', '其它（跌不足2%或上涨）', '次市场交易日停牌', '行情缺失']

def round2(v):
    return float(Decimal(str(v)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))

def pct(price, base):
    return (price / base - 1) * 100 if price is not None and base else None

def group(v, suspended=False):
    if suspended:
        return GROUPS[3]
    if v is None:
        return GROUPS[4]
    if v <= -5 + 1e-10:
        return GROUPS[0]
    if v <= -2 + 1e-10:
        return GROUPS[1]
    return GROUPS[2]

def next_market(day, offset=1):
    i = bisect.bisect_right(CAL, day) + offset - 1
    return CAL[i] if i < len(CAL) else None

def prior_bar(code, day):
    values = BY_CODE[code]
    i = bisect.bisect_left([b['date'] for b in values], day)
    return values[i-1] if i else None

def sell_fee(e, gross):
    """Empirical fee model fit to this statement, not a broker contract quote.

    Stock commission+regulatory max(CNY5, 0.012%); sell stamp 0.05%;
    Shanghai stock transfer approx0.001%. ETFs159337/1595990.005%; other
    sampled domestic stock ETFs0.012%, no minimum.
    Component rounding/broker transfer computation can differ by a few cents.
    """
    if e['asset_type'] == 'ETF':
        return round2(gross * (.00005 if e['code'] in ['159337','159599'] else .00012))
    return round2(max(5, gross * .00012)) + round2(gross * .0005) + (round2(gross * .00001) if e['code'].startswith('6') else 0)

def limit_status(e, b, field):
    """Conservative OHLC-only execution screen; no level2 queue inference."""
    tick = .001 if e['asset_type'] == 'ETF' else .01
    prev = b.get('pre_close')
    if not prev:
        pb = prior_bar(e['code'], b['date'])
        prev = pb['close'] * pb['adj_factor'] / b['adj_factor'] if pb else None
    ratio = .2 if e['code'].startswith(('30', '688')) or e['code'] in ['159915', '159599', '588000'] else .1
    listed = BASIC.get(e['code'], {}).get('list_date')
    no_limit_first5 = False
    if listed:
        listed = f'{listed[:4]}-{listed[4:6]}-{listed[6:8]}'
        no_limit_first5 = 0 <= sum(listed <= d <= b['date'] for d in CAL) <= 5 and listed <= b['date']
    down_limit = None
    if prev and not no_limit_first5:
        down_limit = float((Decimal(str(prev)) * (Decimal(1) - Decimal(str(ratio)))).quantize(Decimal(str(tick)), rounding=ROUND_HALF_UP))
    one_price = abs(b['high'] - b['low']) < tick * .5
    one_price_down = one_price and prev and b['close'] < prev - tick
    at_limit = down_limit is not None and abs(b[field] - down_limit) <= tick * .51
    reasons = []
    if one_price_down:
        reasons.append('one_price_down_day_queue_fill_unknown')
    elif at_limit:
        reasons.append(f'{field}_at_inferred_down_limit_queue_fill_unknown')
    return {'inferred_down_limit': down_limit, 'inferred_limit_ratio': None if no_limit_first5 else ratio,
            'at_inferred_down_limit': at_limit, 'one_price_down_day': bool(one_price_down),
            'price_limit_basis': 'code/board and listed-date screen, exchange price-limit feed unavailable',
            'execution_exclusions': reasons}

def simulate(e, summary, day, field, strategy):
    result = {'strategy': strategy, 'target_date': day, 'price_field': field, 'quantity': summary['first_day_quantity'],
              'original_buy_cash_cost': summary['first_day_cost'], 'actual_full_episode_pnl': e['trade_pnl'],
              'actual_first_day_lots_pnl': summary['first_day_actual_pnl'], 'eligible': False}
    if not day:
        result['exclusion_reasons'] = ['target_date_outside_available_calendar']
        return result
    b = BAR.get((e['code'], day))
    if not b:
        result['exclusion_reasons'] = ['suspended_on_target_day' if (e['code'], day) in SUSPEND else 'missing_target_bar']
        return result
    result.update({'raw_price': b[field], 'bar': b, 'exit_after_actual_episode_close': day > e['last_sell_date']})
    first_bar = BAR[(e['code'], e['first_buy_date'])]
    if abs(b['adj_factor'] / first_bar['adj_factor'] - 1) > 1e-9:
        result['exclusion_reasons'] = ['corporate_action_cash_share_entitlement_not_reconstructed']
        return result
    execution = limit_status(e, b, field)
    result.update(execution)
    gross = summary['first_day_quantity'] * b[field]
    fees = sell_fee(e, gross)
    scenario_pnl = round2(gross - fees - summary['first_day_cost'])
    result.update({'gross_proceeds': round2(gross), 'estimated_sell_fee': round2(fees),
                   'price_benchmark_pnl': scenario_pnl,
                   'pnl_after_10bp_sell_slippage': round2(gross * .999 - sell_fee(e, gross * .999) - summary['first_day_cost']),
                   'delta_vs_actual_full_episode': round2(scenario_pnl - e['trade_pnl']),
                   'delta_vs_actual_first_day_lots': round2(scenario_pnl - summary['first_day_actual_pnl']),
                   'return_on_first_day_cost_pct': pct(gross - fees, summary['first_day_cost']),
                   'exclusion_reasons': execution['execution_exclusions'],
                   'eligible': not execution['execution_exclusions']})
    return result

def t1_events(e):
    """Inventory chronology is source-derived; reported clocks are not verified."""
    output = []
    last_day = None
    inventory = 0
    available = 0
    for event in e['event_details']:
        if event['date'] != last_day:
            available = inventory
            last_day = event['date']
        row = dict(event, sellable_old_shares_before=available)
        if event['kind'] == '证券卖出':
            row['t1_pass'] = event['qty'] <= available + 1e-8
            available -= event['qty']
            inventory -= event['qty']
        else:
            inventory += event['qty']
        row['sellable_old_shares_after'] = available
        row['inventory_after_recomputed'] = inventory
        assert abs(inventory - event['inventory_after']) < 1e-8
        output.append(row)
    return output

DETAILS = []
for e in EPISODES:
    day = e['first_buy_date']
    first_buys = [r for r in e['event_details'] if r['date'] == day and r['kind'] == '证券买入']
    first_sells = [r for r in e['event_details'] if r['date'] == day and r['kind'] == '证券卖出']
    assert not first_sells, e['episode_id']
    q = sum(r['qty'] for r in first_buys)
    cost = -sum(r['net'] for r in first_buys)
    gross_cost = sum(ROWS[r['row_id']]['gross'] for r in first_buys)
    fm = [m for m in MATCHES if m['episode_id'] == e['episode_id'] and m['buy_date'] == day]
    assert abs(sum(m['quantity'] for m in fm) - q) < 1e-6
    next_day = next_market(day)
    third_day = next_market(day, 2)
    nb = BAR.get((e['code'], next_day))
    fb = BAR[(e['code'], day)]
    suspended = not nb and (e['code'], next_day) in SUSPEND
    available_bars = [b for b in BY_CODE[e['code']] if b['date'] > day]
    ab = available_bars[0] if available_bars else None
    first_lot_pnl = sum(m['pnl'] for m in fm)
    next_buys = [r for r in e['event_details'] if r['date'] == next_day and r['kind'] == '证券买入']
    next_adds = [r for r in next_buys if r['inventory_before'] > 0]
    next_below = [r for r in next_adds if -r['net']/r['qty'] < r['average_cost_before']]
    later_buys = [r for r in e['event_details'] if r['date'] > day and r['kind'] == '证券买入']
    value = nb['open'] * nb['adj_factor'] / fb['adj_factor'] if nb else None
    ef = ENRICHED[e['episode_id']]
    result = {
        'episode_id': e['episode_id'], 'code': e['code'], 'name': e['name'], 'asset_type': e['asset_type'],
        'first_buy_date': day, 'actual_exit_date': e['last_sell_date'], 'first_buy_price': e['first_buy_price'],
        'first_day_quantity': q, 'first_day_cost': round2(cost), 'first_day_gross_cost': round2(gross_cost),
        'first_day_fee_inclusive_average': cost/q, 'first_day_gross_average': gross_cost/q,
        'first_day_actual_pnl': round2(first_lot_pnl), 'actual_pnl': e['trade_pnl'],
        'actual_total_buy_cost': e['total_buy_cost'], 'later_buy_cost_avoided_in_scenario': round2(-sum(r['net'] for r in later_buys)),
        'next_market_date': next_day, 'third_market_date': third_day, 'next_day_suspended': suspended,
        'first_available_date': ab['date'] if ab else None, 'next_bar': nb, 'first_available_bar': ab,
        'next_open_vs_first_day_cost_pct': pct(value, cost/q),
        'next_open_vs_first_buy_price_pct': pct(value, e['first_buy_price']),
        'next_open_vs_first_day_cost_raw_pct': pct(nb['open'], cost/q) if nb else None,
        'next_open_gap_vs_previous_close_pct': pct(nb['open'], nb.get('pre_close') or fb['close']) if nb else None,
        'first_available_open_vs_first_day_cost_pct': pct(ab['open'] * ab['adj_factor'] / fb['adj_factor'], cost/q) if ab else None,
        'factor_change_to_next_day': nb['adj_factor'] / fb['adj_factor'] if nb else None,
        'entry_vs_prior_close_pct': ef['features'].get('entryVsPreviousPct'),
        'original_entry_pattern': ef['entry_pattern'],
        'next_day_buy_count': len(next_buys), 'next_day_buy_quantity': sum(r['qty'] for r in next_buys),
        'next_day_buy_cost': round2(-sum(r['net'] for r in next_buys)),
        'next_day_add_count': len(next_adds), 'next_day_add_below_cost_count': len(next_below),
        'next_day_add_below_cost_quantity': sum(r['qty'] for r in next_below),
        'source_first_day_buy_rows': [r['row_id'] for r in first_buys],
    }
    result['gap_group'] = group(result['next_open_vs_first_day_cost_pct'], suspended)
    result['first_buy_price_sensitivity_group'] = group(result['next_open_vs_first_buy_price_pct'], suspended)
    result['previous_close_gap_group'] = group(result['next_open_gap_vs_previous_close_pct'], suspended)
    result['original_uptrend_entry_down5'] = ef['entry_pattern'] in ['上升趋势且靠近5日线','上升趋势其他买入'] and ef['features'].get('entryVsPreviousPct', 100) <= -5
    result['strategies'] = {
        'next_market_open': simulate(e, result, next_day, 'open', 'next_market_open'),
        'next_market_close': simulate(e, result, next_day, 'close', 'next_market_close'),
        'third_market_day_open': simulate(e, result, third_day, 'open', 'third_market_day_open'),
    }
    if suspended:
        result['strategies']['first_available_open_if_suspended'] = simulate(e, result, ab['date'] if ab else None, 'open', 'first_available_open_if_suspended')
    DETAILS.append(result)

def summary(rows):
    pnl = sum(r['actual_pnl'] for r in rows)
    cost = sum(r['actual_total_buy_cost'] for r in rows)
    return {
        'count': len(rows), 'securities': len({r['code'] for r in rows}), 'actual_pnl': round2(pnl),
        'wins': sum(r['actual_pnl'] > 0 for r in rows),
        'win_rate_pct': sum(r['actual_pnl'] > 0 for r in rows) / len(rows) * 100 if rows else None,
        'actual_total_buy_cost': round2(cost), 'actual_pnl_over_buy_cost_pct': pnl/cost*100 if cost else None,
        'first_day_cost': round2(sum(r['first_day_cost'] for r in rows)),
        'actual_first_day_lots_pnl': round2(sum(r['first_day_actual_pnl'] for r in rows)),
        'next_day_add_episodes': sum(r['next_day_add_count'] > 0 for r in rows),
        'next_day_add_events': sum(r['next_day_add_count'] for r in rows),
        'next_day_add_below_cost_episodes': sum(r['next_day_add_below_cost_count'] > 0 for r in rows),
        'next_day_add_below_cost_events': sum(r['next_day_add_below_cost_count'] for r in rows),
        'next_day_buy_cost': round2(sum(r['next_day_buy_cost'] for r in rows)),
        'episode_ids': [r['episode_id'] for r in rows],
    }

def strategy_summary(rows, key):
    attempted = [r for r in rows if key in r['strategies']]
    eligible = [r for r in attempted if r['strategies'][key]['eligible']]
    excluded = [r for r in attempted if not r['strategies'][key]['eligible']]
    counts = collections.Counter(reason for r in excluded for reason in r['strategies'][key]['exclusion_reasons'])
    hypothetical = sum(r['strategies'][key]['price_benchmark_pnl'] for r in eligible)
    actual = sum(r['actual_pnl'] for r in eligible)
    actual_first = sum(r['first_day_actual_pnl'] for r in eligible)
    return {'strategy': key, 'attempted_count': len(attempted), 'eligible_count': len(eligible), 'excluded_count': len(excluded),
            'excluded_by_reason': dict(counts), 'eligible_benchmark_pnl': round2(hypothetical),
            'eligible_benchmark_pnl_10bp_slippage': round2(sum(r['strategies'][key]['pnl_after_10bp_sell_slippage'] for r in eligible)),
            'same_covered_episodes_actual_pnl': round2(actual), 'same_first_day_lots_actual_pnl': round2(actual_first),
            'delta_vs_actual_full_episode': round2(hypothetical - actual),
            'delta_vs_actual_first_day_lots': round2(hypothetical - actual_first),
            'initial_buy_cost': round2(sum(r['first_day_cost'] for r in eligible)),
            'actual_total_buy_cost': round2(sum(r['actual_total_buy_cost'] for r in eligible)),
            'estimated_sell_fees': round2(sum(r['strategies'][key]['estimated_sell_fee'] for r in eligible)),
            'improves_full_episode_count': sum(r['strategies'][key]['delta_vs_actual_full_episode'] > 0 for r in eligible),
            'worsens_full_episode_count': sum(r['strategies'][key]['delta_vs_actual_full_episode'] < 0 for r in eligible),
            'improves_full_episode_over1yuan_count': sum(r['strategies'][key]['delta_vs_actual_full_episode'] > 1 for r in eligible),
            'worsens_full_episode_over1yuan_count': sum(r['strategies'][key]['delta_vs_actual_full_episode'] < -1 for r in eligible),
            'within1yuan_full_episode_count': sum(abs(r['strategies'][key]['delta_vs_actual_full_episode']) <= 1 for r in eligible),
            'extends_past_actual_exit_count': sum(r['strategies'][key]['exit_after_actual_episode_close'] for r in eligible),
            'eligible_episode_ids': [r['episode_id'] for r in eligible],
            'excluded': [{'episode_id':r['episode_id'], 'reasons':r['strategies'][key]['exclusion_reasons'],
                          'benchmark_pnl_only_not_assumed_fill':r['strategies'][key].get('price_benchmark_pnl')} for r in excluded]}

GROUP_SUMMARIES = [{'group': g, **summary([r for r in DETAILS if r['gap_group'] == g])} for g in GROUPS]
GROUP_STRATEGIES = [{'group':g, **strategy_summary([r for r in DETAILS if r['gap_group']==g], key)} for g in GROUPS for key in STRATEGIES if any(key in r['strategies'] for r in DETAILS if r['gap_group']==g)]
LOW5 = [r for r in DETAILS if r['gap_group'] == GROUPS[0]]
COMMON = [r for r in LOW5 if all(r['strategies'][k]['eligible'] for k in STRATEGIES[:3])]
ORIGINAL13 = [r for r in DETAILS if r['original_uptrend_entry_down5']]
assert len(EPISODES) == 158
assert abs(sum(e['trade_pnl'] for e in EPISODES) + 48511.84) < 1e-6
assert len(ORIGINAL13) == 13
assert abs(sum(r['actual_pnl'] for r in ORIGINAL13) + 26264.59) < 1e-6

FEE_FIT = []
for e in EPISODES:
    for event in e['event_details']:
        if event['kind'] == '证券卖出':
            r=ROWS[event['row_id']]
            FEE_FIT.append({'row_id':r['row_id'], 'actual_fee':r['fee'], 'estimate':round2(sell_fee(e,r['gross'])), 'difference':round2(sell_fee(e,r['gross'])-r['fee'])})

CASES=[]
for e in EPISODES:
    if e['code'] not in ['601086','600664','002384']:
        continue
    r=next(r for r in DETAILS if r['episode_id']==e['episode_id'])
    event_rows=t1_events(e)
    next_bar=r['next_bar']
    case={'episode_id':e['episode_id'],'name':e['name'],'analysis':r,'events_with_sellable_inventory':event_rows}
    if next_bar:
        q=r['first_day_quantity'];cost=r['first_day_cost'];high=next_bar['high'];low=next_bar['low'];op=next_bar['open'];cl=next_bar['close']
        case['hold_original_shares_next_session_risk']={
            'max_adverse_vs_first_buy_price_pct':pct(low,e['first_buy_price']),
            'max_adverse_vs_first_day_cost_pct':pct(low,cost/q),
            'worst_mark_to_market_pnl_before_sell_fees':round2(low*q-cost),
            'full_day_peak_to_trough_drawdown_lower_bound_pct':max((op-low)/op,(high-cl)/high)*100,
            'full_day_peak_to_trough_drawdown_upper_bound_pct':(high-low)/high*100,
            'bound_note':'OHLC cannot order high/low; lower bound assumes low-before-high, upper bound high-before-low. Whole-day held position only.',
        }
    if e['code']=='601086':
        sale=e['event_details'][-1]['price']
        case['guofang_specific']={
            'actual_sell_price':sale,'actual_pnl':e['trade_pnl'],
            'hold_original_until_next_close':r['strategies']['next_market_close'],
            'additional_adverse_after_actual_sale_pct_bounds':[0, max(0,(sale-next_bar['low'])/sale*100)],
            'post_sale_low_timing_unknown':True,
            't_plus_one_interpretation':'9/17 may sell the 4400 shares bought9/16. Shares newly bought9/17 cannot themselves be sold9/17; a same-day sell after buying must use remaining old-share quota. Selling old shares then buying back carries the new position overnight.',
            't_trading_not_established':'OHLC does not establish the sequence, queue, liquidity, bid/ask or achievable buy-low/sell-high path. The14.60 close hold is a fixed-exit price benchmark, not proof that continuing to pry a limit-down board or day-trading would profit.',
        }
    CASES.append(case)

T1_VIOLATIONS=[{'episode_id':e['episode_id'],'row_id':r['row_id']} for e in EPISODES for r in t1_events(e) if r.get('t1_pass') is False]
RESULT={
 'metadata':{
    'analysis_scope':'158 completed secondary-market zero-to-zero episodes, same source ledger; excludes IPO, unknown opening cost and still-open episode.',
    'asof':'2026-10-16', 'source_sha256':{fn:hashlib.sha256((P/fn).read_bytes()).hexdigest() for fn in ['episodes.json','normalized_rows.json','matched_lots.json','market/daily_bars.json','market/calendar_api.json','market/suspend_raw.json']},
    'definitions':{
        'original_down5':'(first buy price on dayT / adjusted previous trading close T-1 -1)<=-5%; entry-day drawdown, not an overnight gap.',
        'next_day_down5':'(next market-session opening price adjusted to dayT scale / fee-inclusive weighted average of ALL first-day buys -1)<=-5%.',
        'first_buy_sensitivity':'Same next open divided by original first recorded buy price, excluding fees; changing denominator may change group membership.',
        'overnight_gap':'next open / next bar official pre_close -1; this is a different denominator from the user actual purchase cost.',
        'third_market_day_open':'Buy date is day1; third market trading day is T+2 open. Not the third day after purchase.',
        'next_day_add':'A buy on next market session while this episode has a positive inventory; below-cost uses fee-inclusive buy unit cost vs weighted holding cost immediately before that buy.',
    },
    'counterfactual':'Keep only all first-day acquired shares and their actual buy fees; sell those T+1-eligible shares in one order at fixed next-open/next-close/T+2-open benchmark; make no later buys. Compare same covered observations, separately against whole actual episode and only actual FIFO realization of first-day shares.',
    'fee_assumption':'Fit from statement, not a broker tariff: stock commission+regulatory max(5, gross*0.00012), sell stamp gross*0.0005, SH stock transfer gross*0.00001; ETFs159337/159599 gross*0.00005, other sampled ETFs gross*0.00012, all ETFs without minimum or stamp. Round components0.01. Actual first-day acquisition fees retained. 10bp adverse sell-price sensitivity provided.',
    'execution_policy':'Opening/closing benchmark does not guarantee a fill. Exclude one-price down days and opening/closing inferred down-limit prices because queue fill unknown. No automatic deferral assumed. Suspensions reported separately; first-available-open scenario only for suspended next session. Corporate action factors changing between entry and exit excluded from cash scenario until entitlement verified.',
    'non_causal_limits':['Groups selected by next open describe observed outcomes; do not optimize 2%/5% thresholds or infer causal benefit from adding/no-adding.','CONDITIONAL-OPEN TIMING: the final official opening price is known only when the opening auction is established. Selecting a down5 group from that price and then assuming execution at exactly that price is an ex-post benchmark, not a verified executable rule. A real conditional rule needs a pre-auction signal or post-open executable quotes and timestamps.','At next close, all opening-gap information is known; T+2-open benchmark is fixed in advance. No intraday high is used as an exit strategy.','Comparing first-day shares to an entire episode changes capital deployed and future trades. Same-share comparison is supplied; this is not a full account path, opportunity-cost, compounding or reinvestment simulation.','Episodes may overlap; T+2 hold may extend past actual exit and later reentry. Aggregates are independent episode price benchmarks.','Trades are selected only from completed known-cost episodes; no out-of-sample proof or guarantee follows.','Current holdings are excluded, creating closed-episode selection; no portfolio allocation recommendation is made.','Original reported clocks are not confirmed execution clocks; balances establish event order, not a precise intraday path.'],
    'official_rule_sources':[{'title':'SSE Trading Rules2026, effective2026-07-06;3.1.4 before-settlement sale restriction,3.5.1 price/time priority','url':'https://www.sse.com.cn/lawandrules/sselawsrules2025/fund/trading/c/c_20260424_10817739.shtml'}, {'title':'SZSE Trading Rules2023','url':'https://docs.static.szse.cn/www/lawrules/rule/allrules/bussiness/W020230217564423808793.pdf'}],
 },
 'actual_by_next_open_group':GROUP_SUMMARIES,
 'actual_by_first_buy_price_sensitivity':[{'group':g.replace('较首日含费均价','较首笔买入价'),**summary([r for r in DETAILS if r['first_buy_price_sensitivity_group']==g])} for g in GROUPS],
 'actual_by_gap_vs_previous_close':[{'group':g.replace('较首日含费均价','较昨收'),**summary([r for r in DETAILS if r['previous_close_gap_group']==g])} for g in GROUPS],
 'low5_add_behavior':[{'group':label,**summary(rows)} for label,rows in [('次日有加仓',[r for r in LOW5 if r['next_day_add_count']]),('次日无加仓',[r for r in LOW5 if not r['next_day_add_count']]),('次日有向下加仓',[r for r in LOW5 if r['next_day_add_below_cost_count']]),('次日没有向下加仓',[r for r in LOW5 if not r['next_day_add_below_cost_count']])]],
 'counterfactual_all':[strategy_summary(DETAILS,k) for k in STRATEGIES],
 'counterfactual_by_gap_group':GROUP_STRATEGIES,
 'low5_common_coverage_across_three_strategies':{'count':len(COMMON),'strategies':[strategy_summary(COMMON,k) for k in STRATEGIES[:3]]},
 'original_13_uptrend_entry_down5':{'count':len(ORIGINAL13),'actual_pnl':round2(sum(r['actual_pnl'] for r in ORIGINAL13)), 'next_open_groups':dict(collections.Counter(r['gap_group'] for r in ORIGINAL13)), 'rows':ORIGINAL13},
 'suspended_next_session':[r for r in DETAILS if r['next_day_suspended']],
 'fee_model_validation':{'actual_sell_rows_checked':len(FEE_FIT),'max_absolute_fee_difference':max(abs(r['difference']) for r in FEE_FIT),'sum_estimate_minus_actual':round2(sum(r['difference'] for r in FEE_FIT)), 'details':FEE_FIT},
 'checks':{'episode_count':len(EPISODES),'actual_pnl_sum':round2(sum(r['actual_pnl'] for r in DETAILS)), 'original13_count':len(ORIGINAL13),'t1_violations':T1_VIOLATIONS, 'missing_next_bar_non_suspension':[r['episode_id'] for r in DETAILS if not r['next_bar'] and not r['next_day_suspended']]},
 'cases':CASES,
 'episodes':DETAILS,
}
(OUT/'gap_analysis.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2))
fields=[k for k,v in DETAILS[0].items() if not isinstance(v,(dict,list))]
with (OUT/'next_open_episodes.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(DETAILS)
print(json.dumps({k:RESULT[k] for k in ['actual_by_next_open_group','low5_add_behavior','counterfactual_all','low5_common_coverage_across_three_strategies','checks']},ensure_ascii=False,indent=2))
