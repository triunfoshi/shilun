import json,statistics as st
from pathlib import Path
B=Path(__file__).resolve().parent.parent;V=B/'v3';J=lambda f:json.loads((B/f).read_text())
f=J('v2/episode_features.json');g=J('v2/gap_analysis.json')['episodes'];e=J('episodes_enriched.json');a=J('v3/add_risk_rows.json');items=[]
def add(id,title,definition,rows,files,caveats,method):
 items.append({'id':id,'title':title,'queries':[{'id':id+'-query','source':{'label':'东财交割单与石论行情复盘存档','files':[{'label':x} for x in files],'metricDefinitions':[{'label':title,'definition':definition}],'caveats':caveats},'reportingPeriod':'2026-07-01 至 2026-10-16；158轮已平仓、初始成本可确认的二级市场交易','columns':list(rows[0]),'rows':rows,'methods':[{'language':'calculation','code':method}]}]})
rows=[]
for label,fs in [('全部158轮',f),('原均线上行回落28轮',[x for x in f if x['entry_pattern'] in ['上升趋势且靠近5日线','上升趋势其他买入']])]:
 for threshold in [2,2.2,2.3,2.4,2.5]:
  x=[r for r in fs if r['market_regime']=='大盘下降趋势' and r['market_previous_amount_yuan']<threshold*1e12 and r['technology'] and r['liquid_over_30yi']]
  rows.append({'样本':label,'市场成交额上限_万亿':threshold,'轮数':len(x),'盈利轮数':sum(r['analysis_pnl']>0 for r in x),'盈亏_元':round(sum(r['analysis_pnl'] for r in x),2)})
add('threshold','2.3万亿条件的交集','同时满足买入前上证下降分类、前日沪深成交额低于阈值、申万电子/计算机/通信、个股此前5日平均成交额超过30亿元。',rows,['episode_features.json','market_additions.json'],['市场金额用上证综指加深证A指，含沪市B股、不含北交所；金额按接口千元换算为元。','行业是已核对成员起止日期的当前申万标签；小样本与重复股票交易不能证明因果或阈值最优。'],'筛选四个条件的交集；按交易轮数计数和合计实际含费盈亏。相邻金额阈值仅用于敏感性检查。')
ch=[r for r in g if r['original_entry_pattern']=='明显追涨或远离5日线'];loss=[r for r in ch if r['actual_pnl']<0];valid=[r for r in ch if r['strategies']['next_market_open']['eligible']]
rows=[]
for label,rs in [('最终亏损58轮_价格比较',loss),('最终亏损54轮_含费可比',[r for r in valid if r['actual_pnl']<0]),('全部追涨99轮_含费可比',valid),('最终盈利45轮_含费可比',[r for r in valid if r['actual_pnl']>0])]:
 ss=[r['strategies']['next_market_open'] for r in rs];has=[s for s in ss if 'price_benchmark_pnl' in s];isfull=len(has)==len(ss)
 rows.append({'样本':label,'轮数':len(rs),'开盘价格相对首日含费成本中位数_pct':round(st.median(r['next_open_vs_first_day_cost_pct'] for r in rs),6),'开盘高于成本轮数':sum(r['next_open_vs_first_day_cost_pct']>0 for r in rs),'扣卖出费收益中位数_pct':round(st.median(s['return_on_first_day_cost_pct'] for s in ss),6) if isfull else None,'扣费盈利轮数':sum(s['price_benchmark_pnl']>0 for s in ss) if isfull else None,'扣费胜率_pct':round(sum(s['price_benchmark_pnl']>0 for s in ss)/len(ss)*100,6) if isfull else None,'开盘基准盈亏_元':round(sum(s['price_benchmark_pnl'] for s in ss),2) if isfull else None,'同首日股份实际盈亏_元':round(sum(r['first_day_actual_pnl'] for r in rs),2)})
add('next-open','次日开盘卖出能否改善','首日所有买入股份在次日按开盘价卖出，保留实际买入费并扣估计卖出费；实际对照只用同一批首日买入股份。',rows,['gap_analysis.json','matched_lots.json','normalized_rows.json','daily_bars.json'],['58轮价格比较使用复权因子统一价格单位，不能把公司行动后的价格收益直接当成完整现金总回报。','亏损58轮排除3轮推定开盘跌停及1轮公司行动未重建后为54轮；全部104轮另排除1轮公司行动后为99轮。','最终亏损是事后条件，不能提前选出；99轮仍不保证能按开盘价成交，未扣额外滑点。','卖出费用按原交割单拟合，非券商公布费率。'],'净盈亏 = 首日股份数量×次日开盘价 − 首日含费成本 − 估计卖出费。\n净胜率 = 净盈亏大于0的轮数 / 同口径轮数。收益率中位数各轮等权。\n54轮实际同首日股-32565.99元，开盘基准-26186.93元。\n99轮实际同首日股+1464.09元，开盘基准-3138.32元，差额-4602.41元。')
trans=[x for x in e if x.get('entry_pattern')=='明显追涨或远离5日线' and x.get('market_regime')=='大盘震荡过渡' and x['status']=='closed' and x['initial_cost_known'] and x['origin']=='secondary'];rows=[]
for label,pred in [('指数在线下、MA20向上',lambda f:f['previousClose']<f['ma20'] and f['ma20SlopePct']>0),('指数在线上、MA20向下',lambda f:f['previousClose']>f['ma20'] and f['ma20SlopePct']<0)]:
 rs=[x for x in trans if pred(x['index_features'])];rows.append({'分型':label,'轮数':len(rs),'盈利轮数':sum(x['trade_pnl']>0 for x in rs),'盈亏_元':round(sum(x['trade_pnl'] for x in rs),2)})
add('regime','震荡过渡的准确标准','用买入前一交易日上证收盘相对MA20的位置，以及MA20相对5个交易日前的方向分类；两个方向不一致或相等边界归为过渡。',rows,['analyze_patterns.py','episodes_enriched.json'],['过渡分类不包含量能、波动区间或情绪条件，并非唯一公认的震荡定义。','分类使用买入前已完成日K；MA20有滞后。'],'上涨：前收盘>前日MA20 且 前日MA20>5个交易日前MA20。\n下降：前收盘<前日MA20 且 前日MA20<5个交易日前MA20。\n过渡：其余具有足够历史的情形。\nMA20斜率=(最近20个已完成收盘均值/向前错开5日的20日均值-1)。')
rows=[]
for reg in ['上升','下降','过渡']:
 rs=[x for x in a if x['below_cost'] and x['regime']==reg];rows.append({'每次加仓前上证状态':reg,'买入记录数':len(rs),'交易轮数':len(set(x['episode_id'] for x in rs)),'新增股份实际盈亏_元':round(sum(x['actual_added_lot_pnl'] for x in rs),2)})
add('adds','浮亏加仓与失败后扩仓','买入前已有库存，新买每股含费成本低于当时剩余持仓移动均价，记为低于成本加仓。市场状态在每次加仓前计算；只归因该笔新增股份实际FIFO结果。',rows,['add_risk_rows.json','lots.json','matched_lots.json'],['42条低于成本加仓来自29轮交易，非42个独立实验；个股浮亏不自动等于原计划失效。','历史没有记录的失效价、预期和时限不能补造。新增股份损益归因不是取消加仓的因果收益。','本次提出的禁止加仓纪律属于前瞻风控草案，未作为完整策略回测。'],'买入前库存>0，新买含费单价<加仓前移动平均成本。\n新增股份盈亏=对应FIFO卖出净收入-分摊买入含费成本。\n84条加仓按数量、成本、利润与原匹配明细核对，252项核对通过。')
dongshan=next(r for r in f if r['code']=='002384' and r['first_buy_date']=='2026-07-17')
items[0]['queries'].append({'id':'dongshan-relative-volume','source':{'label':'东山精密7月17日交易前行情','files':[{'label':'episode_features.json'}],'metricDefinitions':[{'label':'固定金额与相对量能','definition':'入场前日市场成交额与截至该日20日平均成交额比较。'}]},'reportingPeriod':'2026-07-17买入；使用2026-07-16及以前数据','columns':['股票','买入日期','前日市场成交额_万亿','前日市场成交额对20日均额_pct','实际整轮盈亏_元'],'rows':[{'股票':'东山精密','买入日期':'2026-07-17','前日市场成交额_万亿':dongshan['market_previous_amount_yuan']/1e12,'前日市场成交额对20日均额_pct':dongshan['market_previous_vs_20d_ratio']*100,'实际整轮盈亏_元':dongshan['analysis_pnl']}],'methods':[{'language':'calculation','code':'市场前日金额 / 截至前日20个交易日平均金额。全部使用买入前已完成交易日。'}]})
(V/'answer_sources.json').write_text(json.dumps({'schemaVersion':1,'items':items},ensure_ascii=False,indent=2))
print(json.dumps({'items':len(items),'rows':sum(len(i['queries'][0]['rows']) for i in items),'next_open':items[1]['queries'][0]['rows']},ensure_ascii=False,indent=2))
