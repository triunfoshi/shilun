#!/usr/bin/env python3
"""Minute/day reconciliation and conditional placement of statement record times."""
from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
from datetime import datetime,timedelta
from collections import defaultdict
import json
B=Path('/private/tmp/shilun-trade-review-202607');D=lambda x:Decimal(str(x))
def load(n):return json.loads((B/n).read_text())
def num(x):return round(float(x),10)
minutes=load('v2/case_minutes_api.json');daily=load('market/daily_bars.json');rows=load('normalized_rows.json');episodes=load('episodes_enriched.json');lots=load('matched_lots.json')
byep={e['episode_id']:e for e in episodes}
def period(b):
 t=datetime.fromisoformat(b['trade_time'])
 return {'start':None if t.strftime('%H:%M')=='09:30' else (t-timedelta(minutes=5)).strftime('%H:%M:%S'),'end':t.strftime('%H:%M:%S'),'kind':'opening_record' if t.strftime('%H:%M')=='09:30' else 'five_minute_interval_ending_at_label'}
def locate(bars,time):
 for bar in bars:
  p=period(bar)
  if p['start'] and p['start']<time<=p['end']:return bar
  if not p['start'] and time==p['end']:return bar
 return None
results=[];allchecks=[]
for c in minutes:
 bs=sorted(c['bars'],key=lambda b:b['trade_time']);code=c['ticker'].split('.')[0];day=c['date'];db=next(b for b in daily if b['ticker']==c['ticker'] and b['date']==day)
 agg={'open':bs[0]['open'],'high':max(b['high'] for b in bs),'low':min(b['low'] for b in bs),'close':bs[-1]['close']}
 amount=sum((D(b['amount']) for b in bs),D(0));vol=sum((D(b['vol']) for b in bs),D(0));dailyamount=D(db['amount'])*1000;dailyvol=D(db['volume'])*100
 checks={'bar_count':len(bs),'expected_record_count_including_opening':49,'labels_unique':len({b['trade_time'] for b in bs})==len(bs),'minute_aggregated_ohlc':agg,'daily_ohlc':{k:db[k] for k in agg},'ohlc_exact_match':all(D(agg[k])==D(db[k]) for k in agg),'amount_sum_cny':num(amount),'daily_amount_cny':num(dailyamount),'amount_difference_cny':num(amount-dailyamount),'amount_relative_difference_pct':num((amount-dailyamount)/dailyamount*100),'minute_volume_shares':num(vol),'daily_volume_shares':num(dailyvol),'volume_difference_shares':num(vol-dailyvol),'unit_note':'Minute volume treated as shares and amount as CNY; reconciles with daily lots*100 and thousand-CNY*1000. Tiny differences retained.'}
 ownrows=sorted([r for r in rows if r['code']==code and r['date']==day and r['kind'] in ['证券买入','证券卖出']],key=lambda r:r['within_security_order'])
 markers=defaultdict(list);eventchecks=[]
 for r in ownrows:
  bar=locate(bs,r['time']);ok=bool(bar and D(bar['low'])<=D(r['price'])<=D(bar['high']));prior=[b for b in bs if b['trade_time'][11:]<r['time']]
  item={'row_id':r['row_id'],'episode_id':r['episode_id'],'recorded_time':r['time'],'kind':r['kind'],'marker':'B' if r['kind']=='证券买入' else 'S','quantity':r['qty'],'price':r['price'],'net_cash':r['net'],'inventory_before':r['inventory_before'],'inventory_after':r['inventory_after'],'time_is_execution_verified':False,'matching_interval':period(bar) if bar else None,'bar_low':bar['low'] if bar else None,'bar_high':bar['high'] if bar else None,'price_inside_interval':ok,'record_time_chart_marker_allowed':ok,'chart_label':('记录时刻 '+r['time']) if ok else None,'last_completed_bar_label_at_record_time':prior[-1]['trade_time'][11:] if prior else None,'max_high_in_completed_bars_before_record_time':max(b['high'] for b in prior) if prior else None,'min_low_in_completed_bars_before_record_time':min(b['low'] for b in prior) if prior else None,'scope_note':'Price inclusion supports consistency between recorded time and this interval only; it does not verify the clock as execution time or establish event order within the interval.'}
  eventchecks.append(item);allchecks.append(item)
  if ok:markers[bar['trade_time']].append(item)
 chart=[];cumamount=D(0);cumvol=D(0)
 for index,b in enumerate(bs):
  cumamount+=D(b['amount']);cumvol+=D(b['vol'])
  chart.append({'index':index,'timestamp':b['trade_time'],'time':b['trade_time'][11:16],'interval':period(b),'open':b['open'],'high':b['high'],'low':b['low'],'close':b['close'],'volume_shares':b['vol'],'amount_cny':b['amount'],'vwap_through_interval_end':num(cumamount/cumvol) if cumvol else None,'statement_record_markers':markers.get(b['trade_time'],[])})
 highs=[period(b) for b in bs if D(b['high'])==D(db['high'])];lows=[period(b) for b in bs if D(b['low'])==D(db['low'])]
 all_low_tail=[]
 for b in reversed(bs):
  if all(D(b[k])==D(db['low']) for k in ['open','high','low','close']):all_low_tail.insert(0,period(b))
  else:break
 downref=(D(db['pre_close'])*D('.90')).quantize(D('.01'),rounding=ROUND_HALF_UP)
 results.append({'ticker':c['ticker'],'date':day,'source':c['source'],'daily_source':db['source'],'reconciliation':checks,'day_high_periods':highs,'day_low_first_period':lows[0],'day_low_periods':lows,'continuous_all_ohlc_at_day_low_tail_from':all_low_tail[0] if all_low_tail else None,'reference_90pct_previous_close_rounded_cent':num(downref),'day_low_equals_90pct_reference':D(db['low'])==downref,'events':eventchecks,'chart_rows':chart})

def event(id):return next(x for x in allchecks if x['row_id']==id)
def ep(id):
 e=byep[id]
 return {'episode_id':id,'trade_pnl':e['trade_pnl'],'events':e['event_details'],'fifo_matched_lots':[m for m in lots if m['episode_id']==id]}
NARRATIVES={
 '600664_20260923':{
 'observed_sequence':[
 '09:30开盘记录9.42；09:30–09:35段高9.74，随后09:35–09:40段低9.49。',
 '记录09:57:04的2300股买入价9.75落入09:55–10:00段9.66–9.84，属于之后第二次上冲所在区间。',
 '全天最高9.86在10:00–10:05段；这是09:57记录之后，不能在首买当时使用这个未来峰值。',
 '10:05–10:10段低9.48，随后总体走低；10:47:27加600股9.51与10:45–10:50段9.49–9.58一致。',
 '首买前最后完整5分钟段截至09:55，已完成段最高9.74；9.75略高一分，不能声称此时已经确认第二峰失败。'],
 'shape_judgment':'全日回看可称早盘两次上冲后回落，第二峰高于第一峰。用“双头”是事后形态描述；09:57首买时第二峰尚未完成，不能当作已知卖出/避买信号。',
 'risk_boundary':'9/23新买2900股当日股票T+1不可卖；不能把当天9.86最高价算成其实际可兑现利润。五分钟段内买入与高低点的先后仍未知。'},
 '600664_20260924':{
 'observed_sequence':[
 '09:39:42买1600股8.99位于09:35–09:40段8.80–9.05；日高9.08在下一段09:40–09:45，随后下落。',
 '10:01:43卖2900股8.20位于10:00–10:05段8.15–8.25，恰好清掉9/23的2900股旧仓。',
 '10:27:49买2100股8.33、10:32:03买2000股8.34，分别落于10:25–10:30段8.20–8.35与10:30–10:35段8.25–8.35，是较早高点已回落后的局部反弹区间。',
 '10:50–10:55首次触及日低8.14，此价等于前收9.04的90%按分四舍五入；午前、13:00–13:05仍有8.15/8.16短暂上浮，13:05以后完整分钟段全部在8.14。'],
 'risk_boundary':'当日买5700股、卖旧仓2900股、收盘仍5700股，新增股份不能当日再卖。9.08全天高点仍不足以让9/23旧仓回本；此例主要是亏损延续和后续买入扩大实际亏损。',
 'timestamp_caveat':'分钟与记录一致，但仍非经核验的成交时钟；不能据5分钟区间精确复原分秒决策。'},
 '002384_20260717':{
 'observed_sequence':[
 '先有09:31:41卖100股248.00，位于09:30–09:35急跌段241.92–259.80，结束002384-E001。',
 '09:39:21重新买100股248.79，位于09:35–09:40从日低反弹的241.92–249.34段，开启002384-E002。',
 '09:53:23再买100股252.02，位于09:50–09:55段247.00–252.47，此笔高于前一笔含费成本248.84。',
 '14:01:10再买100股241.92，位于14:00–14:05段241.92–245.11，低于当时持仓含费均价250.455。午后此前多段全部在日低241.92，不能因14:05段出现245.11而认定买入前已经见到反弹。',
 '三个新买单共300股当日不能卖，7/20按223.02卖出；该轮净亏7423.48。'],
 'fifo':ep('002384-E002'),
 'risk_boundary':'首笔贡献-2595.83元，第二笔-2918.83元，第三笔-1908.83元。第二笔为涨价加仓、第三笔为低于成本加仓，不能把三笔都概括为越跌越加。'},
 '601086_20260916_17':{
 'observed_sequence':[
 '9/16开盘与绝大多数分钟在14.57，09:40–09:45段短暂上至14.89；09:44:27的4400股买价14.58在该段内，随后重新回到14.57，收盘亦14.57。',
 '9/17开盘14.03；09:30–09:35段最低13.52、收13.62；09:35:40卖4400股13.45，与09:35–09:40段13.26–13.70一致。',
 '日低13.26就在卖出记录所对应的09:35–09:40段。五分钟内最低在卖出前还是卖出后不能判定。',
 '之后09:40–09:45收13.55、09:45–09:50收13.74；10:15–10:20段第一次越过原买入成本附近，最高14.97、收14.84。',
 '全天最高15.87出现在13:10–13:15段，时间明确晚于早盘卖出区间；15:00收14.60。'],
 'interpretation':'卖出记录落在早盘急跌及全天低点所在区间，后来明显反弹。可描述为亏损退出后错过反弹，不能把13:15才形成的最高价当成09:35应当知道、能够成交的目标。',
 'profit_giveback_boundary':'9/16收盘对含费成本已亏52.33元；9/17卖出前完整分钟段最高14.13也低于14.581893元含费成本。9/16最高14.89在买入同一5分钟段，先后未知，所以不能断言本轮真实持仓曾先盈利再卖亏。',
 'fifo':ep('601086-E001')}
}
R={'scope':{'days':len(results),'minute_records':sum(len(x['chart_rows']) for x in results),'statement_trade_records':len(allchecks),'price_time_interval_consistent':sum(x['price_inside_interval'] for x in allchecks),'verified_execution_timestamps':0},'methodology':{'interval_convention':'5-minute timestamps treated as interval-end labels, (label-5min,label]; 09:30 retained separately as provider opening record. Exact tick sequence not available.','marker_policy':'Show B/S only where price lies in the corresponding interval; label every such annotation 记录时刻, not verified execution time. Price consistency is not timestamp provenance verification.','no_lookahead':'An interval containing a recorded trade is not yet complete at the record time. Later highs/lows are explicitly retrospective and never treated as prior knowledge.','execution_limits':'OHLC and volume do not establish executable depth, limit-order queue fills, or ability to sell at hindsight highs. T+1 restrictions apply to same-day stock purchases.','chart_note':'Chart rows retain 49 records per day including opening record; VWAP is through each complete interval end and cannot be used as the value known at an interior record time.'},'days':results,'case_interpretations':NARRATIVES}
(B/'v2/case_intraday_analysis.json').write_text(json.dumps(R,ensure_ascii=False,indent=2))
print(json.dumps({'scope':R['scope'],'days':[{'ticker':x['ticker'],'date':x['date'],'checks':x['reconciliation'],'events':[{'row_id':e['row_id'],'recorded_time':e['recorded_time'],'price':e['price'],'range':[e['bar_low'],e['bar_high']],'match':e['price_inside_interval']} for e in x['events']]} for x in results]},ensure_ascii=False,indent=2))
