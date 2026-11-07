#!/usr/bin/env python3
"""Reproduce descriptive chase / additions / close-to-close giveback analysis.
All inputs are existing, read-only local artifacts. No API or trade instructions.
"""
from pathlib import Path
import json
from decimal import Decimal
from collections import Counter, defaultdict
from statistics import mean, median

BASE=Path('/private/tmp/shilun-trade-review-202607')
D=lambda x:Decimal(str(x))
def load(n):return json.loads((BASE/n).read_text())
def rounded(x,n=6):return round(float(x),n) if x is not None else None
def pnl(e):return D(e['trade_pnl'])
EPS=load('episodes_enriched.json')
MATCHES=load('matched_lots.json')
BARS=load('market/daily_bars.json')
INDEX_TRADING_DATES={b['date'] for b in BARS if b['ticker']=='000001.SH'}
PRIMARY=[e for e in EPS if e['status']=='closed' and e['initial_cost_known'] and e['origin']=='secondary']
CHASE=[e for e in PRIMARY if e['entry_pattern']=='明显追涨或远离5日线']
COMBO=[e for e in CHASE if e['market_regime']=='大盘震荡过渡']
OTHER=[e for e in CHASE if e not in COMBO]
MATCH_BY_EP=defaultdict(list)
for m in MATCHES:MATCH_BY_EP[m['episode_id']].append(m)
BAR_BY_CODE=defaultdict(dict)
for b in BARS:BAR_BY_CODE[b['ticker'].split('.')[0]][b['date']]=b

def descriptor(a):
 a=[float(v) for v in a if v is not None]
 return {'mean':rounded(mean(a)),'median':rounded(median(a)),'min':min(a),'max':max(a)} if a else None

def summary(es):
 ps=[pnl(e) for e in es];win=[p for p in ps if p>0];loss=[p for p in ps if p<0]
 gp=sum(win,D(0));gl=-sum(loss,D(0));cost=sum((D(e['total_buy_cost']) for e in es),D(0))
 aw=gp/len(win) if win else None;al=gl/len(loss) if loss else None
 return {'count':len(es),'stocks':len(set(e['code'] for e in es)), 'wins':len(win),'losses':len(loss),'flat':len(es)-len(win)-len(loss),
 'win_rate_pct':rounded(100*len(win)/len(es)) if es else None,'net_trade_pnl':rounded(sum(ps,D(0))),
 'gross_wins':rounded(gp),'gross_losses_abs':rounded(gl),'mean_win':rounded(aw),'mean_loss_abs':rounded(al),
 'win_loss_size_ratio':rounded(aw/al) if aw and al else None,'profit_factor':rounded(gp/gl) if gl else None,
 'breakeven_win_rate_pct_at_observed_avg_payoffs':rounded(100*al/(aw+al)) if aw and al else None,
 'median_trade_pnl':rounded(median(ps)) if ps else None,'mean_trade_pnl':rounded(mean(ps)) if ps else None,
 'total_buy_cost_turnover':rounded(cost),'pnl_over_buy_cost_pct':rounded(sum(ps,D(0))/cost*100) if cost else None,
 'episode_return_pct':descriptor([float(pnl(e))/e['total_buy_cost']*100 for e in es]),
 'holding_trading_days':descriptor([e.get('trade_holding_days') for e in es]),
 'buy_cost_per_episode':descriptor([e['total_buy_cost'] for e in es]),
 'initial_buy_cost_per_episode':descriptor([-next(ev['net'] for ev in e['event_details'] if ev['kind']=='证券买入') for e in es]),
 'max_wa_cost_exposure':descriptor([e['max_wa_cost_exposure'] for e in es]),
 'first_buy_vs_previous_close_pct':descriptor([e['features'].get('entryVsPreviousPct') for e in es]),
 'first_buy_vs_prior_ma5_pct':descriptor([e['features'].get('entryVsMa5Pct') for e in es]),
 'entry_month_counts':dict(sorted(Counter(e['first_buy_date'][:7] for e in es).items())),
 'market_regime_counts':dict(Counter(e['market_regime'] for e in es))}

def addclass(e):
 if e['add_below_cost_count']:return 'any_below_cost_add'
 if not e['add_count']:return 'no_add'
 if e['add_unknown_cost_count']:return 'unknown_cost_add_without_below'
 return 'adds_without_below_cost'

def grouped(es,key):
 groups=defaultdict(list)
 for e in es:groups[key(e)].append(e)
 return {g:summary(v) for g,v in groups.items()}

def episode_brief(e):
 return {k:e.get(k) for k in ['episode_id','code','name','first_buy_date','last_sell_date','trade_pnl','total_buy_cost','add_count','add_below_cost_count','trade_holding_days','market_regime']}

def concentration(es):
 losers=sorted([e for e in es if pnl(e)<0],key=pnl); grossloss=-sum((pnl(e) for e in losers),D(0));net=sum((pnl(e) for e in es),D(0))
 top=losers[:5]
 return {'top5': [episode_brief(e) for e in top], 'top5_loss':rounded(-sum((pnl(e) for e in top),D(0))),
 'top5_share_of_gross_losses_pct':rounded(-sum((pnl(e) for e in top),D(0))/grossloss*100),
 'worst_loss_share_of_gross_losses_pct':rounded(-pnl(losers[0])/grossloss*100),
 'net_excluding_worst':rounded(net-pnl(losers[0])), 'net_excluding_top5':rounded(net-sum((pnl(e) for e in top),D(0))),
 'exclusion_note':'Exclusions show concentration, not an achievable selection rule.'}

def fifo(es):
 detailed=[]; buckets=defaultdict(lambda:{'pnl':D(0),'cost':D(0),'quantity':D(0),'episodes':set(),'buy_rows':set(),'match_count':0})
 for e in es:
  buy_events=[x for x in e['event_details'] if x['kind']=='证券买入'];first_id=buy_events[0]['row_id'];first_date=buy_events[0]['date']
  own=defaultdict(Decimal)
  for m in MATCH_BY_EP[e['episode_id']]:
   assert m['pnl'] is not None
   category='initial_first_buy_row' if m['buy_row_id']==first_id else ('same_entry_day_add' if m['buy_date']==first_date else 'later_day_add')
   keys=[category,'all_later_buy_rows' if m['buy_row_id']!=first_id else 'initial_only', 'entry_day_all_buys' if m['buy_date']==first_date else 'after_entry_day_buys']
   if m['buy_row_id']!=first_id:keys.append('add_relation_'+str(m['addition_relation']))
   for k in keys:
    v=buckets[k];v['pnl']+=D(m['pnl']);v['cost']+=D(m['allocated_buy_cost']);v['quantity']+=D(m['quantity']);v['episodes'].add(e['episode_id']);v['buy_rows'].add(m['buy_row_id']);v['match_count']+=1
   own[category]+=D(m['pnl'])
  residual=sum(own.values(),D(0))-pnl(e)
  assert abs(residual)<D('.000001'),(e['episode_id'],residual)
  detailed.append({'episode_id':e['episode_id'],'initial_buy_row':first_id,'initial_date':first_date,'initial_first_buy_pnl':rounded(own['initial_first_buy_row']), 'same_entry_day_add_pnl':rounded(own['same_entry_day_add']), 'later_day_add_pnl':rounded(own['later_day_add']), 'total_trade_pnl':rounded(pnl(e))})
 out={k:{'pnl':rounded(v['pnl']),'allocated_buy_cost':rounded(v['cost']),'return_on_cost_pct':rounded(v['pnl']/v['cost']*100),'quantity':rounded(v['quantity']),'episode_count':len(v['episodes']),'buy_row_count':len(v['buy_rows']),'match_fragment_count':v['match_count']} for k,v in buckets.items()}
 return {'groups':out,'episodes':detailed,'note':'P&L is actual FIFO-matched cost and net-sale cashflow attribution, not causal profit of an alternative no-add strategy. The initial base is exactly the first buy row; every later buy while holding is an addition, including the same date. entry_day_all_buys is an alternate sensitivity definition.'}

def daily_paths(es):
 results=[];errors=[]
 for e in es:
  events=sorted([ev for ev in e['event_details'] if ev['kind'] in ('证券买入','证券卖出')],key=lambda x:x['within_security_order'])
  bydate=defaultdict(list)
  for ev in events:bydate[ev['date']].append(ev)
  sellmatches=defaultdict(list)
  for m in MATCH_BY_EP[e['episode_id']]:sellmatches[m['sell_row_id']].append(m)
  bars=BAR_BY_CODE[e['code']]
  expected_days={d for d in INDEX_TRADING_DATES if e['first_buy_date']<=d<=e['last_sell_date']}
  for missing_day in sorted(expected_days-set(bars)):
   errors.append({'episode_id':e['episode_id'],'date':missing_day,'type':'missing_bar_on_benchmark_trading_date'})
  days=sorted(set(d for d in bars if e['first_buy_date']<=d<=e['last_sell_date'])|set(bydate))
  qty=D(0);cost=D(0);realized=D(0);cash=D(0);path=[]
  for day in days:
   old=qty;sold=D(0);bought=D(0)
   for ev in bydate.get(day,[]):
    q=D(ev['qty']);cash+=D(ev['net'])
    if ev['kind']=='证券买入':qty+=q;cost-=D(ev['net']);bought+=q
    else:
     qty-=q;sold+=q
     matched_cost=sum((D(m['allocated_buy_cost']) for m in sellmatches[ev['row_id']]),D(0))
     matched_qty=sum((D(m['quantity']) for m in sellmatches[ev['row_id']]),D(0))
     assert matched_qty==q
     cost-=matched_cost;realized+=D(ev['net'])-matched_cost
   assert qty>=0
   if e['asset_type']=='stock' and sold>old:errors.append({'episode_id':e['episode_id'],'date':day,'type':'stock_daily_sales_exceed_prior_close_quantity','sales':float(sold),'prior_qty':float(old)})
   bar=bars.get(day)
   if not bar:
    errors.append({'episode_id':e['episode_id'],'date':day,'type':'missing_daily_bar'});continue
   value=D(bar['close'])*qty;floating=value-cost;total=realized+floating
   assert abs(total-(cash+value))<D('.000001')
   eligible_left=max(old-sold,D(0))
   path.append({'date':day,'close':bar['close'],'quantity_at_previous_close':float(old),'quantity_bought_today':float(bought),'quantity_sold_today':float(sold),'quantity_at_close':float(qty),'prior_day_quantity_remaining_at_close':float(eligible_left),'same_day_quantity_remaining_at_close':float(qty-eligible_left),'fifo_cost_remaining':rounded(cost),'cumulative_realized_trade_pnl':rounded(realized),'close_unrealized_pnl':rounded(floating),'close_total_mark_to_market_trade_pnl':rounded(total),'entire_remaining_position_from_prior_days':bool(qty>0 and eligible_left==qty),'price_source':bar.get('source')})
  assert qty==0,(e['episode_id'],qty)
  assert abs(realized-pnl(e))<D('.000001'),(e['episode_id'],realized,pnl(e))
  held=[p for p in path if p['quantity_at_close']>0];positive=[p for p in held if p['close_total_mark_to_market_trade_pnl']>0];oldpositive=[p for p in positive if p['prior_day_quantity_remaining_at_close']>0];fullypositive=[p for p in positive if p['entire_remaining_position_from_prior_days']]
  results.append({'episode_id':e['episode_id'],'name':e['name'],'final_trade_pnl':float(pnl(e)),'any_positive_held_close':bool(positive),'positive_close_with_some_prior_day_inventory':bool(oldpositive),'positive_close_with_entire_inventory_from_prior_days':bool(fullypositive),'max_held_close_mark_to_market_pnl':max((p['close_total_mark_to_market_trade_pnl'] for p in held),default=None),'max_held_close_date':max(held,key=lambda p:p['close_total_mark_to_market_trade_pnl'])['date'] if held else None,'path':path})
 return results,errors

PATHS,ERRORS=daily_paths(CHASE)
PATH_BY_ID={p['episode_id']:p for p in PATHS}

def giveback(es):
 paths=[PATH_BY_ID[e['episode_id']] for e in es];losers=[p for p in paths if p['final_trade_pnl']<0]
 def stat(a):return {'count':len(a),'final_trade_pnl':rounded(sum(p['final_trade_pnl'] for p in a)),'episode_ids':[p['episode_id'] for p in a]}
 return {'all':len(paths),'losers':len(losers),'closed_positive_then_final_loss':stat([p for p in losers if p['any_positive_held_close']]), 'closed_positive_then_loss_some_old_inventory':stat([p for p in losers if p['positive_close_with_some_prior_day_inventory']]), 'closed_positive_then_loss_entire_inventory_old':stat([p for p in losers if p['positive_close_with_entire_inventory_from_prior_days']]),'no_positive_held_close_final_loss':stat([p for p in losers if not p['any_positive_held_close']])}

H=next(e for e in CHASE if e['episode_id']=='600664-E004')
HF=fifo([H]);hdates=['2026-09-23','2026-09-24','2026-09-28'];hbars=[BAR_BY_CODE['600664'][d] for d in hdates]
prevdaybuys=[ev for ev in H['event_details'] if ev['kind']=='证券买入' and ev['date']=='2026-09-23'];oldq=sum(D(x['qty']) for x in prevdaybuys);oldcost=-sum(D(x['net']) for x in prevdaybuys)
high24=D(BAR_BY_CODE['600664']['2026-09-24']['high'])
RESULT={
 'methodology':{'primary_population':'closed, initial_cost_known, origin secondary; trade_pnl excludes dividends and tax distributions','chase_definition':'existing entry_pattern=明显追涨或远离5日线; based on buy price vs prior completed closes/MA, not intraday chasing evidence','combo_definition':'chase and market_regime=大盘震荡过渡 using existing benchmark classification; remaining 67 are rising/falling regime','fifo_attribution':'fees included through allocated buy cost and net sell cashflow; matched fragments exactly reconcile each episode','close_path':'After each trading date: cumulative realized FIFO P&L + close * actual remaining quantity - remaining FIFO cost; equivalent to cumulative buy/sell net cashflow + close inventory mark. No hypothetical future sell fees deducted. A positive buying-day close is a mark, not proof it could have been liquidated under T+1.','daily_coverage':'Every in-episode daily bar is checked against the existing Shanghai benchmark trading-date set; missing security days are flagged, not filled.', 't_plus_one':'For stocks, daily sales checked against prior-close inventory. At-close old remaining shares=max(prior shares-today sold,0). All-old position identifies only a feasible inventory date, not available depth or actual closing auction execution. ETF product rules not generalized.','daily_high_limitation':'Daily high is not used for profit-giveback classification. Intraday high time, inventory at that time and whether before/after a particular fill are unknown. Closing-day high after an exit is not an available exit price.','causality':'Sample associations and cashflow attribution cannot establish whether a different entry, exit, or no-add policy would have been followed or profitable.'},
 'primary_count':len(PRIMARY),'chase_summary':summary(CHASE),'primary_summary':summary(PRIMARY),
 'chase_add_classes':grouped(CHASE,addclass),'all_primary_add_classes':grouped(PRIMARY,addclass),
 'chase_market_groups':grouped(CHASE,lambda e:e['market_regime']),
 'combo_37':summary(COMBO),'remaining_67':summary(OTHER),
 'combo_add_classes':grouped(COMBO,addclass),'remaining_add_classes':grouped(OTHER,addclass),
 'chase_loss_concentration':concentration(CHASE),
 'chase_fifo_attribution':fifo(CHASE),'combo_fifo_attribution':fifo(COMBO),'remaining_fifo_attribution':fifo(OTHER),
 'chase_giveback':giveback(CHASE),'combo_giveback':giveback(COMBO),'remaining_giveback':giveback(OTHER),
 'daily_close_paths':PATHS,'path_validation_errors':ERRORS,
 'harbin_pharmaceutical_600664_E004':{'episode':episode_brief(H),'features':H['features'],'events':H['event_details'],'bars':hbars,'fifo':HF,'daily_path':PATH_BY_ID[H['episode_id']],
 'september_24_old_inventory_day_high_bound':{'old_quantity':float(oldq),'old_fee_inclusive_cost':float(oldcost),'whole_day_high':float(high24),'hypothetical_all_old_shares_gross_mark_at_high':float(oldq*high24),'gross_mark_minus_cost_before_sell_fees':float(oldq*high24-oldcost),'interpretation':'Even marking all prior-day shares at the entire day high remains negative before sell fees. This is a retrospective optimistic bound, not an assertion the high was reachable with this inventory or known at the time.'},
 'supported':['9/23买入两笔且均价下方加仓，收盘持仓账面已亏。','9/24全天最高价9.08仍低于9/23两笔买入价；旧仓并无日K可支持的盈利卖出机会。','9/24卖出2900股恰为9/23买入总量；当天新增5700股按股票T+1不能当日售出，后于9/28清仓。'],
 'unsupported':['9/23盘中双头的两峰时刻、回落顺序，日K无法判定。','9/24日高位于某笔买入或卖出的前后，日K无法判定。','无法根据成交结果推测主观加仓动机，无法断言卖在当日最高价是可执行选择。']}}
assert len(CHASE)==104 and len(COMBO)==37 and len(OTHER)==67
out=BASE/'v2/chase_exit_analysis.json';out.write_text(json.dumps(RESULT,ensure_ascii=False,indent=2))
print(json.dumps({k:RESULT[k] for k in ['chase_summary','chase_add_classes','combo_37','remaining_67','chase_loss_concentration','chase_giveback','path_validation_errors']},ensure_ascii=False,indent=2))
print('OUTPUT',out)
