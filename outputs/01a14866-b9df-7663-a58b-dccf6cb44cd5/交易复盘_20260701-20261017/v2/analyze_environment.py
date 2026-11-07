from pathlib import Path
import json,statistics,collections,csv,math
P=Path(__file__).parent; R=P.parent
def read(n):return json.loads((P/n).read_text())
def write(n,x):(P/n).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False))
def date(s):return s if '-' in s else s[:4]+'-'+s[4:6]+'-'+s[6:]
def ts(c):return c+('.SH' if c[0] in '569' else '.SZ')
bars=json.loads((R/'market/daily_bars.json').read_text())
eps=json.loads((R/'episodes_enriched.json').read_text())
secondary=[e for e in eps if e['status']=='closed' and e['initial_cost_known'] and e['origin']=='secondary']
added=[]
for ticker,localfile,apifile in [('399001.SZ','sz_index_local.json','sz_index_api.json'),('399107.SZ','399107.SZ_local.json','399107.SZ_api.json'),('000002.SH','000002.SH_local.json','000002.SH_api.json')]:
 for b in read(localfile):added.append({**b,'source_storage':'shilun_mongo','amount_unit':'thousand_cny'})
 for b in read(apifile):added.append({**{k:b.get(k) for k in ['open','high','low','close','pre_close','pct_chg','amount']},'ticker':ticker,'date':date(b['trade_date']),'volume':b['vol'],'source':'tushare_index_daily','source_storage':'configured_tushare_api','amount_unit':'thousand_cny'})
sw=read('sw_current.json');swmap={r['ts_code']:r for r in sw}
assert len(swmap)==len(sw)==90
basic={b['ts_code']:b for b in read('stock_basic_current.json')}
by=collections.defaultdict(list);mp={}
for b in bars+added:
 key=(b['ticker'],b['date']);assert key not in mp
 mp[key]=b;by[b['ticker']].append(b)
for seq in by.values():seq.sort(key=lambda b:b['date'])
market=[]
for sh in by['000001.SH']:
 d=sh['date'];sz=mp[('399107.SZ',d)];sz500=mp[('399001.SZ',d)];sha=mp[('000002.SH',d)]
 market.append({'date':d,'sh_amount_yuan':sh['amount']*1000,'sz_a_amount_yuan':sz['amount']*1000,'market_amount_yuan':(sh['amount']+sz['amount'])*1000,'strict_a_amount_yuan':(sha['amount']+sz['amount'])*1000,'incorrect_500_component_proxy_yuan':(sh['amount']+sz500['amount'])*1000})
marketmap={r['date']:r for r in market}
tech={'电子','计算机','通信'}
rows=[]
for e in secondary:
 code=e['code'];ticker=ts(code);d=e['first_buy_date'];hist=[b for b in by[ticker] if b['date']<d];today=mp[(ticker,d)];ref=today['adj_factor'];c=[b['close']*b['adj_factor']/ref for b in hist]
 f={k:e.get(k) for k in ['episode_id','code','name','asset_type','first_buy_date','last_sell_date','first_buy_price','entry_pattern','market_regime','growth_index_regime','analysis_pnl','analysis_return_pct','total_buy_cost','add_below_cost_count','buy_count']}
 f.update(history_count=len(hist),last_known_stock_date=hist[-1]['date'] if hist else None)
 f['stock_previous_close_adjusted']=c[-1] if c else None
 f['entry_vs_previous_close_pct']=(e['first_buy_price']/c[-1]-1)*100 if c else None
 f['entry_below_previous_close']=e['first_buy_price']<c[-1] if c else None
 for n in [5,10,20,60]:
  ma=statistics.mean(c[-n:]) if len(c)>=n else None
  f[f'ma{n}']=ma
  f[f'price_vs_ma{n}_pct']=(e['first_buy_price']/ma-1)*100 if ma else None
  for lag in [1,5]:
   old=statistics.mean(c[-n-lag:-lag]) if len(c)>=n+lag else None
   f[f'ma{n}_slope_{lag}d_pct']=(ma/old-1)*100 if ma and old else None
 def aligned(ns):return all(f[f'ma{a}']>f[f'ma{b}'] for a,b in zip(ns,ns[1:])) if all(f[f'ma{n}'] is not None for n in ns) else None
 f['short_bull_alignment']=aligned([5,10,20]);f['full_bull_alignment']=aligned([5,10,20,60])
 for label,ns in [('short',[5,10,20]),('full',[5,10,20,60])]:
  for lag in [1,5]:
   slopes=[f[f'ma{n}_slope_{lag}d_pct'] for n in ns]
   f[f'{label}_all_slopes_up_{lag}d']=all(v>0 for v in slopes) if all(v is not None for v in slopes) else None
 f['stock_prior_5d_avg_amount_yuan']=statistics.mean(b['amount']*1000 for b in hist[-5:]) if len(hist)>=5 else None
 f['stock_prior_5d_avg_amount_yi']=f['stock_prior_5d_avg_amount_yuan']/1e8 if f['stock_prior_5d_avg_amount_yuan'] is not None else None
 f['liquid_over_30yi']=f['stock_prior_5d_avg_amount_yuan']>30e8 if f['stock_prior_5d_avg_amount_yuan'] is not None else None
 mh=[m for m in market if m['date']<d];prev,prev2=mh[-1],mh[-2];cur=marketmap[d]
 prevamt=prev['market_amount_yuan'];prevmean=statistics.mean(x['market_amount_yuan'] for x in mh[-20:])
 f.update(market_previous_date=prev['date'],market_previous_amount_yuan=prevamt,market_previous_day_change_pct=(prevamt/prev2['market_amount_yuan']-1)*100,market_previous_vs_20d_ratio=prevamt/prevmean,market_entry_day_amount_yuan=cur['market_amount_yuan'],market_entry_day_change_pct_posthoc=(cur['market_amount_yuan']/prevamt-1)*100)
 f['market_prior_shrink']=f['market_previous_day_change_pct']<0;f['market_prior_low20']=f['market_previous_vs_20d_ratio']<1;f['market_prior_shrink_and_low20']=f['market_prior_shrink'] and f['market_prior_low20'];f['market_entry_day_shrink_posthoc']=f['market_entry_day_change_pct_posthoc']<0
 f['market_prior_shrink_5pct']=f['market_previous_day_change_pct']<-5;f['market_prior_shrink_10pct']=f['market_previous_day_change_pct']<-10;f['market_prior_low20_90pct']=f['market_previous_vs_20d_ratio']<.9
 f['strict_a_prior_change_pct']=(prev['strict_a_amount_yuan']/prev2['strict_a_amount_yuan']-1)*100
 f['incorrect_component_prior_change_pct']=(prev['incorrect_500_component_proxy_yuan']/prev2['incorrect_500_component_proxy_yuan']-1)*100
 label=swmap.get(ticker)
 if e['asset_type']=='ETF':
  f.update(industry_l1='ETF',industry_l2='ETF',industry_l3='ETF',technology=False,industry_source='ETF单列',industry_label_valid_on_entry=None,industry_in_date=None)
 elif label:
  indt=date(label['in_date']) if label.get('in_date') else None;outdt=date(label['out_date']) if label.get('out_date') else None
  f.update(industry_l1=label['l1_name'],industry_l2=label['l2_name'],industry_l3=label['l3_name'],technology=label['l1_name'] in tech,industry_source='Tushare index_member_all 申万当前标签及成员起止日期',industry_label_valid_on_entry=(indt is None or indt<=d) and (outdt is None or d<outdt),industry_in_date=indt)
 else:f.update(industry_l1='未知',industry_l2='未知',industry_l3='未知',technology=None,industry_source='missing',industry_label_valid_on_entry=False,industry_in_date=None)
 f['stock_basic_industry_current']=basic.get(ticker,{}).get('industry')
 rows.append(f)
assert len(rows)==158 and sum(r['entry_pattern']=='明显追涨或远离5日线' for r in rows)==104
def metrics(rs):
 vals=[r['analysis_pnl'] for r in rs];rets=[r['analysis_return_pct'] for r in rs];cost=sum(r['total_buy_cost'] for r in rs)
 return {'count':len(rs),'stocks':len({r['code'] for r in rs}),'pnl':round(sum(vals),2),'wins':sum(v>0 for v in vals),'losses':sum(v<0 for v in vals),'win_rate_pct':round(sum(v>0 for v in vals)/len(vals)*100,4) if vals else None,'mean_pnl':round(statistics.mean(vals),2) if vals else None,'mean_return_pct':round(statistics.mean(rets),4) if rets else None,'median_return_pct':round(statistics.median(rets),4) if rets else None,'pooled_return_pct':round(sum(vals)/cost*100,4) if cost else None,'cost':round(cost,2),'losing_pnl':round(sum(v for v in vals if v<0),2),'winning_pnl':round(sum(v for v in vals if v>0),2)}
def grouped(rs,key):
 groups=collections.defaultdict(list)
 for r in rs:groups[str(r.get(key))].append(r)
 return {name:metrics(v) for name,v in sorted(groups.items())}
cohorts={'all_158':rows,'up_nonchase_28':[r for r in rows if r['entry_pattern'] in ['上升趋势且靠近5日线','上升趋势其他买入']],'up_near_ma5_11':[r for r in rows if r['entry_pattern']=='上升趋势且靠近5日线'],'up_other_17':[r for r in rows if r['entry_pattern']=='上升趋势其他买入'],'chase_104':[r for r in rows if r['entry_pattern']=='明显追涨或远离5日线'],'chase_transition_37':[r for r in rows if r['entry_pattern']=='明显追涨或远离5日线' and r['market_regime']=='大盘震荡过渡']}
group_keys=['short_bull_alignment','full_bull_alignment','short_all_slopes_up_1d','short_all_slopes_up_5d','full_all_slopes_up_1d','full_all_slopes_up_5d','entry_below_previous_close','liquid_over_30yi','technology','industry_l1','industry_l3','market_prior_shrink','market_prior_low20','market_prior_shrink_and_low20','market_entry_day_shrink_posthoc','market_prior_shrink_5pct','market_prior_shrink_10pct','market_prior_low20_90pct']
result={}
for name,rs in cohorts.items():
 techrows=[r for r in rs if r['technology']]
 sub={'summary':metrics(rs),'groups':{key:grouped(rs,key) for key in group_keys},'tech_by_prior_shrink':grouped(techrows,'market_prior_shrink'),'tech_by_prior_low20':grouped(techrows,'market_prior_low20'),'tech_by_prior_shrink_and_low20':grouped(techrows,'market_prior_shrink_and_low20'),'tech_by_entry_day_shrink_posthoc':grouped(techrows,'market_entry_day_shrink_posthoc'),'tech_by_liquidity':grouped(techrows,'liquid_over_30yi')}
 sub['strict_short_bull_up_slopes']=metrics([r for r in rs if r['short_bull_alignment'] and r['short_all_slopes_up_5d']])
 sub['strict_full_bull_up_slopes']=metrics([r for r in rs if r['full_bull_alignment'] and r['full_all_slopes_up_5d']])
 sub['tech_shrink_large_amount']=metrics([r for r in rs if r['technology'] and r['market_prior_shrink'] and r['liquid_over_30yi']])
 sub['mean_price_vs_ma5_pct']=statistics.mean(r['price_vs_ma5_pct'] for r in rs if r['price_vs_ma5_pct'] is not None)
 sub['median_stock_prior_5d_avg_amount_yi']=statistics.median(r['stock_prior_5d_avg_amount_yi'] for r in rs if r['stock_prior_5d_avg_amount_yi'] is not None)
 sub['worst_episodes']=sorted(rs,key=lambda r:r['analysis_pnl'])[:8]
 sub['best_episodes']=sorted(rs,key=lambda r:r['analysis_pnl'],reverse=True)[:5]
 result[name]=sub
definitions={'market_amount_proxy':'(上证综指000001.SH amount + 深证A指399107.SZ amount) × 1000；沪深市场成交额代理，000001含沪市A/B口径，不含北交所；严格A股用000002.SH+399107.SZ作敏感性。','why_not_399001':'Tushare官方说明399001成交额仅500成分，不是深市全体A股。','amount_source_unit':'千元；股票30亿元门槛=3,000,000千元=3,000,000,000元。','prior_market_change':'入场前一交易日成交额/前前交易日成交额-1；入场时可知。','prior_market_vs20':'入场前一交易日成交额/此前含该日20交易日平均成交额。','entry_day_market_change':'入场当日完整日成交额/前一交易日成交额-1；仅收盘后事后标签，不可当成买入时已知信号。','ma':'入场前已完成日K，复权到入场当日价格单位；斜率分别是前日均线对1或5个交易日前均线变化。','liquidity':'证券入场前5根已完成交易日K的平均成交额，严格大于30亿元；非当日或持仓期成交额。','industry':'申万当前成员标签，保留in_date/out_date并校验覆盖交易日期；不声称已保存当时发布的行业快照。','technology':'申万一级行业严格集合：电子、计算机、通信；ETF独立，不按名称自动并入科技。','cohort_overlap':'37轮盈利组合是104轮追涨的子集；28轮非追涨上升趋势与104轮互斥。','outcome':'closed known-cost secondary episodes; trade_pnl excludes dividends/tax; mean/median return are equal episode weights, pooled_return uses cumulative buy cost.'}
doc_links={'index_daily':'https://tushare.pro/document/2?doc_id=95','sw_member':'https://tushare.pro/document/2?doc_id=335'}
audit={'cohort_total':len(rows),'sw_mapped_stocks':len(swmap),'industry_uncovered_episodes':[r['episode_id'] for r in rows if r['industry_label_valid_on_entry'] is False],'all_market_dates':len(market),'strict_a_shrink_disagreements':[r['episode_id'] for r in rows if (r['strict_a_prior_change_pct']<0)!=r['market_prior_shrink']],'incorrect_399001_shrink_disagreements':[r['episode_id'] for r in rows if (r['incorrect_component_prior_change_pct']<0)!=r['market_prior_shrink']]}
write('episode_features.json',rows)
with (P/'episode_features.csv').open('w',newline='',encoding='utf-8-sig') as f:
 w=csv.DictWriter(f,list(rows[0]));w.writeheader();w.writerows(rows)
write('market_additions.json',{'definitions':definitions,'source_docs':doc_links,'index_additional_bars':added,'market_amount_daily':market,'sw_current_members':sw,'current_stock_basic':list(basic.values()),'audit':audit})
write('environment_analysis.json',{'definitions':definitions,'source_docs':doc_links,'audit':audit,'cohorts':result})
print(json.dumps({'audit':audit,'cohorts':{k:{'summary':v['summary'],'short_bull':v['groups']['short_bull_alignment'],'full_bull':v['groups']['full_bull_alignment'],'liquid_over30yi':v['groups']['liquid_over_30yi'],'tech':v['groups']['technology'],'prior_shrink':v['groups']['market_prior_shrink'],'tech_shrink':v['tech_by_prior_shrink']} for k,v in result.items() if k in ['up_nonchase_28','chase_104','chase_transition_37']}},ensure_ascii=False,indent=2))
