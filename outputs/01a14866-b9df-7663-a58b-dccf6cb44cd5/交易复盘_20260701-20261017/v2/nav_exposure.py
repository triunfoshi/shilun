#!/usr/bin/env python3
"""Read-only reconstruction of observable holdings/cash; no total-NAV claim."""
from pathlib import Path
from decimal import Decimal
from collections import defaultdict
import json
from statistics import mean,median
B=Path('/private/tmp/shilun-trade-review-202607');D=lambda x:Decimal(str(x))
def load(n):return json.loads((B/n).read_text())
def num(x):return round(float(x),8)
rows=load('normalized_rows.json');eps=load('episodes_enriched.json');bars=load('market/daily_bars.json');bridge=load('cash_bridge.json')
primary=[e for e in eps if e['status']=='closed' and e['initial_cost_known'] and e['origin']=='secondary']
initial_buys=[]
for e in primary:
 first=next(ev for ev in sorted(e['event_details'],key=lambda x:x['within_security_order']) if ev['kind']=='证券买入')
 initial_buys.append({'episode_id':e['episode_id'],'code':e['code'],'name':e['name'],'row_id':first['row_id'],'date':first['date'],'reported_time':first['time'],'quantity':first['qty'],'reported_price':first['price'],'fee_inclusive_first_buy_cash_cost':-first['net']})
initial_buys.sort(key=lambda x:x['fee_inclusive_first_buy_cash_cost'],reverse=True)
target=next(x for x in initial_buys if x['episode_id']=='601086-E001');targetrow=next(x for x in rows if x['row_id']==target['row_id']);amount=D(target['fee_inclusive_first_buy_cash_cost'])
values=sorted(D(x['fee_inclusive_first_buy_cash_cost']) for x in initial_buys)
def percentile(p):
 index=D(len(values)-1)*D(p);lo=int(index);hi=min(lo+1,len(values)-1);return values[lo]+(values[hi]-values[lo])*(index-lo)
bycode=defaultdict(list)
for r in rows:
 if r['kind'] in ['证券买入','证券卖出','新股入账']:bycode[r['code']].append(r)
initial={};reconcile_errors=[]
for code,records in bycode.items():
 records.sort(key=lambda x:x['within_security_order'])
 first=records[0];delta=(-1 if first['kind']=='证券卖出' else 1)*D(first['qty']);initial[code]=D(first['balance_qty'])-delta
 q=initial[code]
 for r in records:
  q+=(-1 if r['kind']=='证券卖出' else 1)*D(r['qty'])
  if q!=D(r['balance_qty']):reconcile_errors.append(r['row_id'])
def snapshot(day,clock):
 result=[]
 for code,records in bycode.items():
  q=initial[code];last=None
  for r in records:
   if (r['date'],r['time'])<=(day,clock):q=D(r['balance_qty']);last=r
  if q:
   result.append({'code':code,'name':records[0]['name'],'quantity':num(q),'last_balance_source_row':last['row_id'] if last else None})
 return result
DAY='2026-09-16';after=snapshot(DAY,targetrow['time']);atclose=snapshot(DAY,'23:59:59')
opening_cash=D(bridge['opening_cash_inferred']);pre_day_cash=opening_cash+sum((D(r['net']) for r in rows if r['date']<DAY),D(0));day_rows=sorted([r for r in rows if r['date']==DAY],key=lambda r:r['time'])
cash=pre_day_cash;cash_checks=[]
for r in day_rows:
 before=cash;cash+=D(r['net'])
 cash_checks.append({'row_id':r['row_id'],'reported_time':r['time'],'kind':r['kind'],'code':r['code'],'previous_cash':num(before),'net_cashflow':r['net'],'calculated_cash':num(cash),'reported_cash':r['cash_balance'],'difference':num(cash-D(r['cash_balance']))})
assert all(r['difference']==0 for r in cash_checks)
market_total=D(0);missing=[]
for s in atclose:
 bar=next((b for b in bars if b['ticker'].split('.')[0]==s['code'] and b['date']==DAY),None)
 if bar is None:missing.append(s['code']);continue
 s.update(close=bar['close'],price_date=bar['date'],price_source=bar['source'],price_adjustment=bar['price_adjustment'],market_value=num(D(s['quantity'])*D(bar['close'])))
 market_total+=D(s['market_value'])
assets=market_total+cash;targetmv=D(next(s['market_value'] for s in atclose if s['code']==target['code']))
init_nonzero=[]
for code,q in initial.items():
 if q:
  firstzero=next((r for r in bycode[code] if D(r['balance_qty'])==0),None)
  init_nonzero.append({'code':code,'name':bycode[code][0]['name'],'inferred_quantity':num(q),'first_source_row':bycode[code][0]['row_id'],'zero_balance_date':firstzero['date'] if firstzero else None,'zero_balance_row':firstzero['row_id'] if firstzero else None})
RESULT={
 'target':target,
 'amount_rank':{'population':'158 closed, known-cost, secondary-market zero-to-zero episodes; one initial buy row per episode','n':len(primary),'rank_descending':1+sum(v>amount for v in values),'ties_at_same_amount':sum(v==amount for v in values),'strictly_lower_count':sum(v<amount for v in values),'mean':num(mean(values)),'median':num(median(values)),'p25':num(percentile('.25')),'p75':num(percentile('.75')),'p90':num(percentile('.90')),'p95':num(percentile('.95')),'amount_multiple_of_mean':num(amount/mean(values)),'amount_multiple_of_median':num(amount/median(values)),'second_largest':initial_buys[1],'multiple_of_second_largest':num(amount/D(initial_buys[1]['fee_inclusive_first_buy_cash_cost']))},
 'reported_buy_event_boundary':{'source_row_id':targetrow['row_id'],'reported_time':targetrow['time'],'reported_time_is_verified_execution':False,'reported_cash_before':num(D(targetrow['cash_balance'])-D(targetrow['net'])),'reported_cash_after':targetrow['cash_balance'],'share_of_prior_reported_cash_used_pct':num(amount/(D(targetrow['cash_balance'])-D(targetrow['net']))*100),'observed_holdings_after_ledger_event':after,'instantaneous_account_nav':None,'instantaneous_position_weight':None,'reason':'No synchronized market quote for other then-held securities and no verified execution timestamp. Reported cash is not verified available-to-trade cash; the event is not evidence of a full-account all-in position.'},
 'close_observable_assets':{'date':DAY,'holdings':atclose,'cash_from_cumulative_statement_net':num(cash),'cash_from_last_day_row':day_rows[-1]['cash_balance'],'last_cash_source_row':day_rows[-1]['row_id'],'security_market_value':num(market_total),'cash_plus_covered_security_market_value':num(assets),'guofang_market_value_weight_pct':num(targetmv/assets*100),'first_buy_cash_cost_divided_by_close_observable_assets_pct':num(amount/assets*100),'not_same_metric_note':'74.74% is end-of-day market-value weight. First-buy cash divided by end-of-day observable assets uses a different time/valuation and must not be presented as instantaneous entry weight.','missing_held_security_daily_bars':missing},
 'completeness_checks':{'source_rows':len(rows),'security_codes_with_inventory_activity':len(bycode),'nonzero_inferred_initial_positions':init_nonzero,'inventory_balance_errors':reconcile_errors,'day_open_cash_from_prior_statement_flows':num(pre_day_cash),'day_cash_chain':cash_checks,'cumulative_period_cash_bridge_difference':bridge['difference'],'all_observed_inventory_events_reconciled':not reconcile_errors},
 'limitations':['The statement proves inventory changes and post-event balances only for securities appearing in its records. It cannot exclude untouched positions existing before July, other accounts, products outside this statement, liabilities or other assets.','Cash reconciliation and no inventory errors support completeness within the supplied ledger; they do not independently prove full broker-account NAV.','The only detectable nonzero opening holding was 002008, 100 shares, cleared on July 1. Its unknown historical cost does not affect September observable market value.','Daily closes can value end-of-day known holdings; they cannot supply synchronized intraday prices. Therefore exact post-purchase NAV/weight are intentionally null.','Cumulative purchase turnover is never used as assets or NAV.','First-buy rank is conditional on the 158 closed known-cost secondary episodes; excludes IPO, unknown-opening-cost and still-open episodes.'],
 'initial_buy_distribution_rows':initial_buys
}
(B/'v2/nav_exposure.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2))
print(json.dumps({k:RESULT[k] for k in ['target','amount_rank','reported_buy_event_boundary','close_observable_assets','completeness_checks']},ensure_ascii=False,indent=2))
