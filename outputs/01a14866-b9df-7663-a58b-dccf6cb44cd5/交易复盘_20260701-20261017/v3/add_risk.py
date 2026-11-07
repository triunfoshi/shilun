from pathlib import Path
import json,statistics as st,collections as co,csv
B=Path(__file__).resolve().parent.parent;O=B/'v3'
read=lambda n:json.loads((B/n).read_text())
eps=[e for e in read('episodes_enriched.json') if e['status']=='closed' and e['initial_cost_known'] and e['origin']=='secondary']
bars=read('market/daily_bars.json'); market=read('v2/market_additions.json')['market_amount_daily']
index=sorted([b for b in bars if b['ticker']=='000001.SH'],key=lambda b:b['date'])
by=co.defaultdict(list)
for b in bars:by[b['ticker'].split('.')[0]].append(b)
for a in by.values():a.sort(key=lambda x:x['date'])
lots={l['buy_row_id']:l for l in read('lots.json')}
rows=[]
for e in eps:
 day_events=co.defaultdict(list)
 for ev in e['event_details']:day_events[ev['date']].append(ev)
 for day,evs in day_events.items():
  evs.sort(key=lambda x:x['within_security_order']);start=evs[0]['inventory_before'];end=evs[-1]['inventory_after']
  hist=[x for x in index if x['date']<day];c=[x['close'] for x in hist];ma20=st.mean(c[-20:]);oldma20=st.mean(c[-25:-5]);down=c[-1]<ma20 and ma20<oldma20;up=c[-1]>ma20 and ma20>oldma20
  mh=[x for x in market if x['date']<day];amt=mh[-1]['market_amount_yuan'];ratio=amt/st.mean(x['market_amount_yuan'] for x in mh[-20:]);change=amt/mh[-2]['market_amount_yuan']-1
  sh=[b for b in by[e['code']] if b['date']<day]
  today=next(b for b in by[e['code']] if b['date']==day)
  prevlow=sh[-1]['low']*(sh[-1].get('adj_factor') or 1)/(today.get('adj_factor') or 1)
  for ev in evs:
   if ev['kind']!='证券买入' or ev['inventory_before']<=0:continue
   lot=lots[ev['row_id']];price=ev['price'];cost=ev['average_cost_before'];r=price/cost-1
   rows.append(dict(episode_id=e['episode_id'],row_id=ev['row_id'],name=e['name'],date=day,entry_date=e['first_buy_date'],chase=e['entry_pattern']=='明显追涨或远离5日线',price=price,old_average_cost=cost,price_vs_cost_pct=r*100,qty=ev['qty'],old_qty=ev['inventory_before'],after_qty=ev['inventory_after'],day_start_qty=start,day_end_qty=end,day_net_added_qty=end-start,day_net_increase=end>start,same_entry_day=day==e['first_buy_date'],below_cost=lot['addition_relation']=='below',below_cost_3pct=r<=-.03,below_prior_low=price<prevlow,regime='下降' if down else '上升' if up else '过渡',prior_amount=amt,prior_amount_vs20=ratio,prior_shrink=change<0,prior_change_pct=change*100,amount_below23=amt<2.3e12,prior_low90=ratio<.9,actual_added_lot_pnl=lot['realized_pnl'],added_cost=lot['cost'],whole_episode_pnl=e['trade_pnl']))
def sumr(a):
 return dict(buy_rows=len(a),episodes=len(set(x['episode_id'] for x in a)),dates=len(set(x['date'] for x in a)),wins=sum(x['actual_added_lot_pnl']>0 for x in a),losses=sum(x['actual_added_lot_pnl']<0 for x in a),actual_added_lot_pnl=round(sum(x['actual_added_lot_pnl'] for x in a),2),cost=round(sum(x['added_cost'] for x in a),2),median_return_pct=round(st.median(x['actual_added_lot_pnl']/x['added_cost']*100 for x in a),5) if a else None)
res={}
for name,rr in [('all',rows),('chase',[r for r in rows if r['chase']])]:
 res[name]={}
 for grp,rs in [('all_additions',rr),('below_cost',[r for r in rr if r['below_cost']]),('below_cost3',[r for r in rr if r['below_cost_3pct']]),('belowcost_and_belowpriorlow',[r for r in rr if r['below_cost'] and r['below_prior_low']]),('later_day_belowcost',[r for r in rr if r['below_cost'] and not r['same_entry_day']])]:
  res[name][grp]={'all':sumr(rs),'by_market':{reg:sumr([r for r in rs if r['regime']==reg]) for reg in ['上升','下降','过渡']},'down_and_low90':sumr([r for r in rs if r['regime']=='下降' and r['prior_low90']]),'shrink_and_low90':sumr([r for r in rs if r['prior_shrink'] and r['prior_low90']]),'day_end_net_added':sumr([r for r in rs if r['day_net_increase']])}
assert len(rows)==sum(e['add_count'] for e in eps)
assert sum(x['below_cost'] for x in rows)==sum(e['add_below_cost_count'] for e in eps)
res['checks']={'rows_reconcile_add_count':len(rows),'belowcost_rows':sum(x['below_cost'] for x in rows),'note':'Actual matched-lot attribution, not a counterfactual strategy. Partial sell costs use original moving weighted average; new lot outcomes use original FIFO actual sale allocation. Multiple rows from the same episode are dependent. Regimes and volumes are computed before each addition date, not first entry date. End-day net increase is posthoc, and can understate interim risk. Fee-only belowcost flag not automatically failure.'}
(O/'add_risk.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
(O/'add_risk_rows.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
with (O/'add_risk_rows.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,list(rows[0]));w.writeheader();w.writerows(rows)
print(json.dumps(res,ensure_ascii=False,indent=2))
