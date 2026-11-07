from pathlib import Path
import json,csv,statistics as st,collections as co
B=Path(__file__).resolve().parent.parent;O=B/'v4';J=lambda f:json.loads((B/f).read_text())
E=[e for e in J('episodes_enriched.json') if e['status']=='closed' and e['initial_cost_known'] and e['origin']=='secondary']
F={r['episode_id']:r for r in J('v2/episode_features.json')}; G={r['episode_id']:r for r in J('v2/gap_analysis.json')['episodes']}
C=[e for e in E if e['entry_pattern']=='明显追涨或远离5日线'];L=[e for e in C if e['trade_pnl']<0]
def summary(es):
 p=[e['trade_pnl'] for e in es];cost=sum(e['total_buy_cost'] for e in es)
 return {'count':len(es),'stocks':len(set(e['code'] for e in es)),'wins':sum(v>0 for v in p),'losses':sum(v<0 for v in p),'pnl':round(sum(p),2),'win_rate_pct':round(sum(v>0 for v in p)/len(es)*100,4) if es else None,'median_return_pct':round(st.median(e['trade_pnl']/e['total_buy_cost']*100 for e in es),4) if es else None,'gross_losses_abs':round(-sum(v for v in p if v<0),2),'gross_wins':round(sum(v for v in p if v>0),2),'cost':round(cost,2),'pooled_return_pct':round(sum(p)/cost*100,4) if cost else None,'without_best':round(sum(p)-max(p),2) if p else None,'without_august':round(sum(e['trade_pnl'] for e in es if e['first_buy_date'][:7]!='2026-08'),2),'month_counts':dict(co.Counter(e['first_buy_date'][:7] for e in es))}
months=[]
for m in ['2026-07','2026-08','2026-09','2026-10']:
 es=[e for e in C if e['first_buy_date'][:7]==m];ls=[e for e in es if e['trade_pnl']<0]
 z={'entry_month':m,'chase_count':len(es),'losing_count':len(ls),'share_of_58_pct':len(ls)/len(L)*100,'loss_rate_pct':len(ls)/len(es)*100,'losing_sum':round(sum(e['trade_pnl'] for e in ls),2),'share_of_gross_loss_pct':sum(e['trade_pnl'] for e in ls)/sum(e['trade_pnl'] for e in L)*100,'gross_wins':round(sum(e['trade_pnl'] for e in es if e['trade_pnl']>0),2),'all_chase_net_pnl':round(sum(e['trade_pnl'] for e in es),2),'loser_median_return_pct':st.median(e['trade_pnl']/e['total_buy_cost']*100 for e in ls),'median_next_open_cost_return_pct':st.median(G[e['episode_id']]['next_open_vs_first_day_cost_pct'] for e in ls),'losses_belowcost_add_count':sum(e['add_below_cost_count']>0 for e in ls),'largest_loss_name':min(ls,key=lambda e:e['trade_pnl'])['name'],'largest_loss_pnl':min(e['trade_pnl'] for e in ls),'loser_ids':[e['episode_id'] for e in ls]};months.append(z)
close_month=[{'closing_month':m,**summary([e for e in L if e['last_sell_date'][:7]==m])} for m in ['2026-07','2026-08','2026-09','2026-10']]
cohorts={'all158':E,'chase104':C,'chase_transition37':[e for e in C if e['market_regime']=='大盘震荡过渡'],'up_nonchase28':[e for e in E if e['entry_pattern'] in ['上升趋势且靠近5日线','上升趋势其他买入']],'stock_down13':[e for e in E if e['entry_pattern']=='下跌趋势中买入'],'chase_noadd76':[e for e in C if e['add_count']==0],'chase_belowcost_add16':[e for e in C if e['add_below_cost_count']>0],'chase_never_belowcost88':[e for e in C if e['add_below_cost_count']==0]}
# Predefined disjoint refinement: actual entry-day price strength vs merely stretched prior trend.
cohorts.update({'chase_entry_up3':[e for e in C if e['features']['entryVsPreviousPct']>=3], 'chase_stretched_only':[e for e in C if e['features']['entryVsPreviousPct']<3], 'chase_stretched_entry_down':[e for e in C if e['features']['entryVsPreviousPct']<0], 'chase_up3_transition':[e for e in C if e['features']['entryVsPreviousPct']>=3 and e['market_regime']=='大盘震荡过渡']})
sums={k:summary(es) for k,es in cohorts.items()}
submonths={k:[{'month':m,**summary([e for e in es if e['first_buy_date'][:7]==m])} for m in ['2026-07','2026-08','2026-09','2026-10']] for k,es in cohorts.items()}
case_lists={k:[{z:e.get(z) for z in ['episode_id','name','first_buy_date','last_sell_date','trade_pnl','add_count','add_below_cost_count']} for e in sorted(es,key=lambda e:e['trade_pnl'],reverse=True)[:5]] for k,es in cohorts.items()}
assert len(E)==158 and len(C)==104 and len(L)==58
assert sum(m['losing_count'] for m in months)==58
assert abs(sum(m['losing_sum'] for m in months)+46507.41)<.0001
assert sums['chase_entry_up3']['count']+sums['chase_stretched_only']['count']==104
out={'method':'Entry-month assigns a complete round to first buy date. Figures are fee-inclusive trading P&L excluding dividends/tax and IPO/unknown/open rounds. Month loss count uses 104 chase denominator for within-month loss rate; 58 denominator only for distribution. October is partial through Oct16. Refined strength labels do not prove breakout, core role or anticipation at entry.','monthly_chase_losses':months,'monthly_by_closing_date':close_month,'cross_month_losing_rounds':[{k:e[k] for k in ['episode_id','first_buy_date','last_sell_date','trade_pnl']} for e in L if e['first_buy_date'][:7]!=e['last_sell_date'][:7]],'cohorts':sums,'cohort_months':submonths,'case_lists':case_lists}
(O/'monthly_modes.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
with (O/'58笔亏损月份分布.csv').open('w',newline='',encoding='utf-8-sig') as f:
 rows=[{k:v for k,v in r.items() if k!='loser_ids'} for r in months];w=csv.DictWriter(f,list(rows[0]));w.writeheader();w.writerows(rows)
print(json.dumps({'months':months,'cohorts':sums,'cross_month':out['cross_month_losing_rounds']},ensure_ascii=False,indent=2))
