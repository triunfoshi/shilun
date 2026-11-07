from pathlib import Path
import json,statistics,datetime,hashlib
P=Path(__file__).parent
R=P.parent/'report/src/data.json'
def load(n): return json.load(open(P/n))
d=json.load(open(R)); env=load('environment_analysis.json'); fs=load('episode_features.json'); chase=load('chase_exit_analysis.json'); gap=load('gap_analysis.json'); sent=load('sentiment_analysis.json'); intr=load('case_intraday_analysis.json'); nav=load('nav_exposure.json')
base_source={'label':'交割单第二轮复核：均线、量能、加仓、次日开盘与分钟证据','files':['normalized_rows.json','episodes_enriched.json','market/daily_bars.json','v2/environment_analysis.json','v2/episode_features.json','v2/chase_exit_analysis.json','v2/gap_analysis.json','v2/case_intraday_analysis.json','v2/sentiment_analysis.json','v2/nav_exposure.json'],'timeRange':{'start':'2026-07-01','end':'2026-10-16'},'evidenceFlow':[{'title':'核算实际交易','detail':'158轮已平仓、成本已知的主动交易；含实际费用，分红单列；FIFO归因对账。'},{'title':'补足环境证据','detail':'优先石论历史数据，缺口使用已配置行情API；买前完整日与当日收盘后信息分别记录。'},{'title':'比较相同股份','detail':'开盘、收盘情景固定保留首日股份，估算卖费；另列实际整轮与实际首日股份，避免混淆资金规模。'}],'caveats':['全部分组为同一段历史样本的探索，不能当作样本外验证或因果结论。','日终成交额及次日开盘只在对应时点可知；已知开盘价再假设同价成交属于事后价格基准。','分钟B/S仅为交割记录时刻与5分钟价格区间一致，不是成交时钟经独立认证。','行业为当前申万标签及成员起止日校验，不冒充交易当时已存档标签。']}
def query(k,rows,extra=None):
 source=dict(base_source)
 if extra: source.update(extra)
 if k.startswith(('v2_environment','v2_uptrend')):
  source['files']=['episodes_enriched.json','market/daily_bars.json','v2/market_additions.json','v2/episode_features.json','v2/environment_analysis.json']
 elif k.startswith(('v2_chase','v2_fifo')):
  source['files']=['episodes_enriched.json','matched_lots.json','market/daily_bars.json','v2/chase_exit_analysis.json']
 elif k.startswith('v2_gap'):
  source['files']=['episodes_enriched.json','normalized_rows.json','matched_lots.json','market/daily_bars.json','v2/gap_analysis.json','v2/next_open_verification.json']
 elif k.startswith('v2_sentiment'):
  source['files']=['v2/case_sentiment_local.json','v2/sentiment_cohort_bars.json','v2/sentiment_analysis.json','market/daily_bars.json']
 elif k=='v2_nav':
  source['files']=['normalized_rows.json','episodes.json','market/daily_bars.json','v2/nav_exposure.json']
 elif k.startswith(('v2_minute','v2_intraday')):
  source['files']=['normalized_rows.json','market/daily_bars.json','v2/case_minutes_api.json','v2/case_intraday_analysis.json']
  source['evidenceFlow']=[{'title':'先查石论、再补现有接口','detail':'本地只查到东山当日46段；已配置API补5日各49条，包括09:30开盘记录。完整日线已与这245条分钟行情逐日聚合核对。'},{'title':'只标一致的记录区间','detail':'12条记录价格均落在对应5分钟段内；没有独立认证成交时钟，不推断段内高低先后。'}]
  source['caveats']=['分钟B/S只标记录所属区间，不保证是独立核验的成交时钟。','5日分钟聚合OHLC与日线完全一致，量额有微小尾差；09:30为集合竞价，其后每行截至标注时刻，午休省略。','未来高点不能当作此前可知信号或可实现卖价；股票当日新买股份不能直接当天卖出。']
 d['queries'][k]={'rows':rows,'source':source}
def narrative(id,title,text,ids):
 rows=d['queries']['narratives']['rows']; old=next((x for x in rows if x['id']==id),None)
 value={'id':id,'title':title,'text':text,'queryIds':ids}
 if old: old.update(value)
 else: rows.append(value)
groups=env['cohorts']; labels={'up_nonchase_28':'前日趋势上升，未归入追涨','chase_104':'追涨或远离5日线（全组）','chase_transition_37':'其中：追涨 × 大盘震荡过渡'}
out=[]
for key,label in labels.items():
 g=groups[key]; n=g['summary']['count']; true=lambda f:g['groups'].get(f,{}).get('True',{}).get('count',0)
 out.append({'group':label,'count':n,'pnl':g['summary']['pnl'],'winRatePct':g['summary']['win_rate_pct'],'alignedPct':100*true('short_bull_alignment')/n,'strictAlignedPct':100*true('full_bull_alignment')/n,'stockAmount5MedianYi':g['median_stock_prior_5d_avg_amount_yi'],'over30Count':true('liquid_over_30yi'),'techCount':true('technology'),'priorLiquidityWeakCount':true('market_prior_low20'),'entryExpandedCount':g['groups']['market_entry_day_shrink_posthoc']['False']['count']})
query('v2_environment_summary',out,{'caveats':base_source['caveats']+['多头=MA5>MA10>MA20；严格多头=MA5>MA10>MA20>MA60。均线均用入场前一日。量能偏弱=前日市场额低于当时20日均额。分母均为组内全部轮次，缺MA60不是否。37轮属于104轮，不得合计。']})
up=[r for r in fs if r['entry_pattern'].startswith('上升趋势')]
query('v2_uptrend_details',[{'name':r['name'],'entryDate':r['first_buy_date'],'industry':r['industry_l1']+' / '+r['industry_l3'],'maAligned':r['short_bull_alignment'],'strictAligned':r['full_bull_alignment'],'stockAmount5Yi':r['stock_prior_5d_avg_amount_yi'],'marketPriorChangePct':r['market_previous_day_change_pct'],'marketEntryChangePct':r['market_entry_day_change_pct_posthoc'],'pnl':r['analysis_pnl'],**r} for r in sorted(up,key=lambda x:x['analysis_pnl'])])
query('v2_environment_metrics',[{'cohort':key,'condition':cond,'value':val,**stats} for key,g in groups.items() for cond,by in g['groups'].items() for val,stats in by.items()])
query('v2_environment_features',fs)
query('v2_chase_stats',[{'group':'追涨全组',**chase['chase_summary']},{'group':'追涨且大盘过渡',**chase['combo_37']},{'group':'其余追涨',**chase['remaining_67']}])
addlabels={'no_add':'不加仓','any_below_cost_add':'曾向下加仓','adds_without_below_cost':'加仓但未向下加仓'}
query('v2_chase_adds',[{'group':addlabels.get(k,k),'count':v['count'],'pnl':v['net_trade_pnl'],'winRatePct':v['win_rate_pct']} for k,v in chase['chase_add_classes'].items()])
fifo=chase['chase_fifo_attribution']['groups']
query('v2_fifo_attribution',[{'group':label,'pnl':round(fifo[k]['pnl'],2),**{x:y for x,y in fifo[k].items() if x!='pnl'}} for k,label in [('initial_first_buy_row','每轮最初一笔'),('same_entry_day_add','首买当日后续买入'),('later_day_add','此后交易日新增买入')]])
query('v2_chase_giveback',[chase['chase_giveback']]); query('v2_chase_paths',chase['daily_close_paths'])
query('v2_gap_groups',[{'group':r['group'],'count':r['count'],'pnl':r['actual_pnl'],'winRatePct':r['win_rate_pct'],**r} for r in gap['actual_by_next_open_group'] if r['count']])
cf=next(x for x in gap['counterfactual_by_gap_group'] if x['group'].endswith('跌≥5%') and x['strategy']=='next_market_open')
common=gap['low5_common_coverage_across_three_strategies']; ar=common['strategies'][0]['same_first_day_lots_actual_pnl']
price_rows=[{'group':'28轮：实际首日股份','count':28,'pnl':cf['same_first_day_lots_actual_pnl'],'delta':0}, {'group':'28轮：次日开盘卖首日股份','count':28,'pnl':cf['eligible_benchmark_pnl'],'delta':cf['delta_vs_actual_first_day_lots']},{'group':'28轮：开盘价再下调0.1%','count':28,'pnl':cf['eligible_benchmark_pnl_10bp_slippage'],'delta':round(cf['eligible_benchmark_pnl_10bp_slippage']-cf['same_first_day_lots_actual_pnl'],2)},{'group':'28轮：实际整轮（含后来加仓）','count':28,'pnl':cf['same_covered_episodes_actual_pnl'],'delta':round(cf['same_covered_episodes_actual_pnl']-cf['same_first_day_lots_actual_pnl'],2)},{'group':'共同19轮：实际首日股份','count':19,'pnl':ar,'delta':0}]
names={'next_market_open':'次日开盘','next_market_close':'次日收盘','third_market_day_open':'第三交易日开盘'}
price_rows += [{'group':'共同19轮：'+names[x['strategy']],'count':x['eligible_count'],'pnl':x['eligible_benchmark_pnl'],'delta':x['delta_vs_actual_first_day_lots']} for x in common['strategies']]
query('v2_gap_same_shares',price_rows,{'caveats':base_source['caveats']+['28轮排除4轮开盘触及按板块规则推算的跌停价，排队成交未核验；19轮为三种退出方式均未触发排除的共同样本。差额分别对比本组实际首日股份，正数更好。实际整轮含新增本金，不能当作相同资金的策略回测。']})
query('v2_gap_episode_details',gap['episodes']); query('v2_gap_benchmarks',gap['counterfactual_by_gap_group']); query('v2_gap_cases',gap['cases']); query('v2_nav',[nav])
sr=[]
for x in sent['days']:
 evidence=next(c['evidence'] for c in x['components'] if c['name']=='赚钱效应')
 ratio=float(evidence.split(' ')[-1].rstrip('%'))
 sr.append({'date':x['date'],'upCount':x['limit_up_count'],'downCount':x['limit_down_count'],'upRatioPct':ratio,'highestBoard':x['highest_board'],'moodScore':x['mood_score'],'ssePct':x['index_returns']['000001.SH'],'chinextPct':x['index_returns']['399006.SZ'],**x})
query('v2_sentiment_days',sr,{'label':'石论收盘情绪归档与当日日线','caveats':['这些是收盘后归档，9月24日快照生成于9月28日，并非9月24日买入时即可读取的实时信号。','评分36升44，但上涨广度下降、跌停风险扩散；评分不能代表所有维度同步改善。']})
query('v2_sentiment_cohorts',sent['cohorts'])
query('v2_intraday_evidence',intr['days'])
minute_titles={('600664.SH','2026-09-23'):'哈药9月23日：第二次上冲买入，随后走弱',('600664.SH','2026-09-24'):'哈药9月24日：卖旧仓、再买入，隔夜仓位扩大',('002384.SZ','2026-07-17'):'东山7月17日：先清旧轮，随后重新抄底三次',('601086.SH','2026-09-16'):'国芳9月16日：跌停附近短暂打开时买入',('601086.SH','2026-09-17'):'国芳9月17日：早盘低位卖出，午后才出现日高'}
minute_meta=[]
for day in intr['days']:
 t,dt=day['ticker'],day['date']; key=t.split('.')[0]+'_'+dt.replace('-',''); qid='v2_minute_'+key
 ikey=key if key in intr['case_interpretations'] else '601086_20260916_17'
 it=intr['case_interpretations'][ikey]
 lines=it['observed_sequence']
 if t=='601086.SH': lines=[s for s in lines if ('9/16' if dt.endswith('16') else '9/17') in s]
 text='\n\n'.join(lines)+'\n\n'+it.get('risk_boundary','记录时刻只用于对应5分钟区间，不保证是经独立核验的成交时钟。')
 minute_meta.append({'caseId':key,'title':minute_titles[(t,dt)],'text':text,'queryId':qid,'date':dt,'ticker':t})
 query(qid,day['chart_rows'],{'label':minute_titles[(t,dt)]+' · 已配置API历史5分钟行情与交割单','caveats':base_source['caveats']+['分钟聚合OHLC与日线完全一致；成交额及成交量存在微小尾差，见v2分钟核对记录。','09:30为集合竞价记录，此后每行是截至标注时刻的5分钟段；午休在图上省略。']})
query('v2_minute_meta',minute_meta)

narrative('trade-v2-environment','追问1、4：多头排列与量能核对', '''## 追问1、4：多头排列确实存在，但没有守住回调买点

先纠正上一版的命名：**“上升趋势”当时只要求前日收盘在MA20上方、MA20较5日前上升，并未验证多条均线排列**。“均线向上，所以回落可以买”是对价格特征的解释，不是交割单证明的真实买入理由；国芳按你补充的意图应理解为翘板。

把追涨组优先分出后，余下28轮合计亏 **33,239.50元**。新核查发现：23轮满足 **MA5＞MA10＞MA20**，合计仍亏 **24,822.98元**；这23轮的三条均线较5日前也都上行。加入MA60的完整多头排列剩12轮，亏 **15,916.99元**；再要求四条均线较5日前都上行，9轮仍亏 **13,466.81元**。因此，补上多头排列条件可以排除部分交易，却没有把这批回落买入变为盈利模式。

**市场是否放量，必须区分两个时点。** 买前已知的前一日，15轮市场缩量且全部亏损，合计 **21,210.70元**；13轮前日放量仍亏 **12,028.80元**。23轮前日市场成交额低于20日均额，合计亏 **29,937.14元**。如果看买入当天结束后的完整成交额，则16轮当天最终放量仍亏 **20,451.15元**，12轮缩量亏 **12,788.35元**；日终放量不能等同于你下单时承接增强。

**股票并非集中在科技。** 医药6轮、电子4轮、公用事业4轮最多；亏损前三为电子 **10,024.25元**、医药 **6,679.33元**、商贸零售 **5,017.60元**。按申万电子、计算机、通信定义，科技共7轮且全亏 **12,214.45元**；其中前日市场缩量4轮亏 **10,024.25元**。进一步要求市场额低于20日均额，只有3轮，亏 **9,396.34元**，样本很小。

**30亿元也没有筛出优势。** 这28轮各自买入前5个完整交易日的平均成交额，中位数 **32.26亿元**，横跨轮次的均值 **58.31亿元**。超过30亿元的15轮，2胜13负，合计亏 **19,464.44元**。逐只股票的均额、行业和量能变化见下表；该门槛反映流动性，不等于次日会有人继续接力。

### 为什么“追涨 × 大盘震荡过渡”却赚钱？

37轮是104轮追涨的**子集**，不是另外一种独立买点；与28轮趋势回落组互斥。它的单轮收益中位数为 **+0.38%**，全追涨组为 **−0.33%**，趋势回落组为 **−3.50%**。37轮中短期多头排列仅16轮（43.2%），28轮组却有23轮（82.1%）；更完整的多头排列并没有解释胜负差异。前三组的前5日成交额中位数分别约 **37.13、31.78、32.26亿元**，也没有明显拉开。

真正有证据的差别是**所处时期、市场状态和亏损尾部**：37轮赚 **6,425.26元**，另外67轮亏 **17,669.32元**；其中大盘上升标签的36轮亏 **18,321.34元**，下降标签31轮却略赚 **652.02元**。37轮有27轮发生在8月，剔除8月只剩 **+30.03元**。它尚不足以证明“震荡中追涨”存在稳定优势。

也不能推出“缩量一律不做科技”：追涨全组中，前日缩量科技20轮合计 **+1,694.13元**；37轮候选里同类11轮 **+3,912.52元**。当前更该防的是**把已明显转弱的股票解释为普通回调**，而不是只按行业或单一量能条件做判断。

口径：均线取买前完成日，日均成交额取买前5日；市场量能用上证综指与深证A指之和，不重复加沪深300或创业板。深证成指仅覆盖500只成分，不能代表深市总成交额，见 [Tushare指数口径](https://tushare.pro/document/2?doc_id=95)。严格沪深A股口径复查未改变本样本缩量分类。行业标签来自当前申万成员表，已校验成员日期；ETF单列。''', ['v2_environment_summary','v2_uptrend_details','v2_environment_metrics','v2_environment_features','v2_chase_stats','candidate_robustness'])

narrative('trade-v2-chase','追问2：胜率、回吐与加仓', '''## 追问2：胜率没有超过半数，次日以后加仓扩大了损失

追涨组104轮是 **46赢、58亏，胜率44.2%**，只是比全样本38.6%高，仍低于一半。因此收益中位数 **−0.33%** 并不矛盾。平均赢 **766.59元**，平均亏 **801.85元**，按这个赚亏幅度需要约 **51.1%** 的胜率才能打平，实际净亏 **11,244.06元**。

下面两张表分别回答不同问题。第一张按整轮行为分组：不加仓76轮合计 **+136.12元**；曾向下加仓16轮合计 **−12,695.90元**。第二张按实际买入批次与卖出逐笔匹配：最初一笔贡献 **−2,098.46元**，首日后续买入 **+974.26元**，此后交易日新增买入 **−10,119.86元**。后一项不等于全部都是向下加仓；它是实际现金流归因，也不等于假设不加仓就能保留原来的卖法。

**盈利回吐存在，但不能把所有亏损都归于卖点。** 58个亏损轮中，22轮曾在仍持仓时收盘账面盈利，最终合计亏 **11,831.66元**；其中只有3轮在那个盈利收盘仍留有部分前日旧仓，只有2轮剩余全部为旧仓。多数正收益发生在首买当天，受股票交收前出售限制，不能认定那部分利润当时能卖出兑现。日K也无法排除其余轮次在盘中曾有浮盈。

亏损还较集中：前5大亏损占追涨组毛亏损 **47.9%**，哈药9月23日这一轮占毛亏损 **22.8%**。去掉哈药这一轮，追涨全组仍小亏 **617.48元**。所以重点是控制少数大亏及失败后的仓位扩大，同时研究退出时序，而不是只追求更高胜率。关于股票交收前出售限制，参见 [上交所2026年交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)。''', ['v2_chase_stats','v2_chase_adds','v2_fifo_attribution','v2_chase_giveback','v2_chase_paths','v2_intraday_evidence'])

narrative('trade-v2-gap','追问3、8：次日低开是否一律卖出', '''## 追问3、8：严重低于成本是风险信号，但尚未证明开盘卖最好

原来的 **“首买较昨收跌≥5%”** 是买入当天第一笔价格相对前一日收盘；你现在问的是 **“买入后次日开盘比买入成本低≥5%”**。这两个指标的日期、分母不同，原来那13轮仅7轮同时满足新的条件。

这次用**首日所有买入的含费加权均价**作为成本：次日开盘低于它5%以上，共32轮，实际全部亏损，合计 **45,498.64元**；改用第一笔原始成交价则为31轮，结论方向相同。32轮中只有4轮次日继续加仓，另外28轮没有次日加仓也亏 **30,657.10元**。因此，严重低于成本与后续亏损高度相关，但亏损并不全由补仓造成；这也是交易已经受损后的状态分组，不能当作买前预测。

**同一批原仓的比较，不支持“一律开盘割肉”。** 排除4次开盘触及按板块规则推算的跌停价、排队成交未核验后，28轮首日原仓按次日开盘卖出的价格基准为 **−32,692.55元**，比实际整轮 **−39,751.29元** 少亏7,058.74元；但同样的首日股份实际只亏 **31,450.93元**，开盘卖反而多亏 **1,241.62元**。总体看似改善，主要来自避免后来新增股份的 **8,300.36元** 损失，而不是普遍获得更好的卖价。

再对三种退出方式都可比较的共同19轮检查：次日开盘、次日收盘、第三交易日开盘，三种固定价格基准都比这些首日股份的实际退出更差，详见表格。它们还不是可执行的回测：知道最终开盘价后再假设同价成交存在时点问题；费用为交割单拟合估算，另列卖价下调0.1%的敏感性；没有用盘中最高价当卖点，也没有假设跌停一定卖得掉。

所以现有证据更支持把“显著低于成本”用于**停止自动补仓、重新检查失效条件**。是否立即减仓或退出，应由买前写好的失效条件和当时可成交价格决定。数据还不足以制定“次日低开5%无条件开盘清仓”，也不足以因为国芳后来反弹就认定低开应继续买。''', ['v2_gap_groups','v2_gap_same_shares','v2_gap_episode_details','v2_gap_benchmarks','trend_pullback'])

narrative('trade-v2-cases','追问5、6、7：三笔交易的具体问题', '''## 追问5、6、7：哈药、东山、国芳的问题并不相同

### 哈药：第二次上冲高位买入，次日把失败仓位扩大

9月23日首笔 **2300股×9.75元**，记录时刻09:57，位于第二次上冲阶段；此前第一段高9.74，第二段最高 **9.86出现在10:00—10:05**。事后可以看到两次上冲后走弱，但首买时第二个峰尚未完成，不能用未来图形说当时已经确认双头。更扎实的问题是首买已比昨收高 **7.38%**、比前日MA5高 **23.07%**，失败后的处理又继续扩大风险。

首日再买600股后，收盘总账面已亏 **1,925.28元**。9月24日先买1600股×8.99，再卖旧2900股×8.20，随后买2100股×8.33、2000股×8.34；由隔夜2900股变成5700股新仓，后者在9月28日卖于7.43。整轮亏 **10,626.58元**，其中9月24日新增股份贡献 **6,248.17元**损失。旧仓即便按9月24日最高9.08估值仍亏，不能把这笔主要归为盈利回吐。

你指出的市场风险扩散有证据：9月24日上证 **−1.22%**、创业板 **−2.68%**，上涨占比从33.8%降到20.0%，跌停从15家增到16家。但梯队呈现分化：涨停51→52家，最高4→5板，石论综合情绪分36→44，仍属普通；“没有任何改善、掉队都暴毙”过于绝对。9月23日12只连板股中，次日7只未晋级，其中5只下跌、4只跌超5%，1只收于核验跌停价。再看9月22日连板、23日掉队的15只，24日9只下跌，也有三羊马、内蒙新华等涨停修复。哈药属于受损的一端，不能用幸存龙头的修复代替它自身的承接证据。这里使用收盘后归档，不能声称当时所有信息已可见。

### 东山：7月17日是弱市中的重新抄底，不是有效多头回踩

当天09:31先清掉上一轮100股，随后新一轮分别买100股 **248.79、252.02、241.92元**，7月20日300股卖于223.02，亏 **7,423.48元**。首笔较昨收跌 **7.44%**；前日MA5=254.132、MA10=246.360、MA20=251.7675，**MA10低于MA20，不满足5＞10＞20**。

买前市场已是下降标签，前一日成交额缩 **6.52%**、只相当于20日均额 **76.26%**；东山自身前5日日均成交额高达 **291.20亿元**。当日市场最终放量10.46%，股票仍收跌停241.92。后续252.02一笔是反弹时加，241.92一笔是在跌停附近再加；这轮的新买仓位当天不能直接卖出。它更符合你说的弱市抄底失效，不能由靠近MA5或高成交额来合理化。

### 国芳：重仓翘板后早盘卖低；反弹成立不等于继续做T必赚

9月16日 **4400股×14.58元**，首笔含费 **64,160.33元**，是全部158轮首笔最大金额，为中位首笔的 **4.71倍**。按交割单覆盖证券重建的收盘资产85,771.97元，国芳市值占 **74.74%**。这不是已核验的完整账户资产：区间始终未交易的其他资产可能未出现在交割单；买入瞬间也没有全部持仓同步报价。你的“重仓影响客观判断”与高集中度相符，但情绪原因来自你的复盘陈述。

9月17日开14.03，较首日含费成本约 **−3.78%**，**不属于次日跌5%组**。你记录09:35:40卖13.45，位于09:35—09:40的低位区间；日低13.26也在这一段，日高15.87在13:10—13:15，分钟线确认先低后高，但段内最低在你的卖出前还是后仍未知。

如果原仓持到当天收盘14.60，估算含费仅 **+39.20元**；卖价再差0.1%，变为 **−25.00元**。而盘中13.26对应原仓账面 **−5,816.33元**，尚未扣卖费。实际卖出亏5,017.60元，说明这次早卖确实错过了恢复，但不能由午后高点倒推当时一定应再买。用旧仓做T在数量上可以讨论，前提是买卖路径与风险预算事先成立；当天新买股份本身不能当天直接卖出。你这次没有实际加仓记录，不能把它归为“低开加仓亏损”。''', ['v2_sentiment_days','v2_sentiment_cohorts','v2_intraday_evidence','v2_gap_cases','v2_nav','v2_environment_features','v2_chase_paths','v2_fifo_attribution'])

narrative('trade-v2-actions','追问8：更准确的亏损模式', '''## 更准确的总结：弱化中的买入，加上失败后扩大风险

“大盘下降趋势盲目抄底、次日低开加仓”能描述东山、哈药的部分过程，但不能包办全部亏损。按买前上证MA20分类，下降环境57轮亏 **19,805.95元**，上升环境52轮反而亏 **31,670.70元**；均线标签会滞后，不能把指数尚未转为空头理解为风险小。严重低于成本的32轮里，大多数并未继续加仓，也都亏损。

这次最值得先执行的复盘约束是：**把回调、破位、翘板、反核分开记录真实入场理由；首笔前确定整轮风险上限；次日明显弱于预期时先停止自动补仓；退出按原定失效条件判断，做T必须有独立依据与可用旧仓，不为摊低成本临时改变计划。** 这些是待新样本验证的管理约束，不是已证明能盈利的系统。

国芳应优先修正的是过大的初始仓位和临时改变处理计划；哈药应优先修正失败后新增仓位近乎翻倍；东山应优先修正弱市、均线结构已不完整时仍按抄底处理。不能把三种不同问题压缩成“低开一定割”或“低开就做T”。''', ['v2_gap_groups','v2_gap_episode_details','v2_nav','v2_intraday_evidence','v2_environment_features','market_regimes'])

old=next(r for r in d['queries']['narratives']['rows'] if r['id']=='trade-entry-patterns')
old['text']=old['text'].replace('## 最该警惕的是“均线向上，所以回落可以买”','## 原始分组：前日趋势上升，买入当天已可能转弱').replace('这里的“上升”只描述前一日MA20，不能证明买入当天仍然强。','这里的“上升”只描述前一日MA20，不能证明买入当天仍然强；多头排列、行业和量能的补充核查见上方第二轮复盘。')
old=next(r for r in d['queries']['narratives']['rows'] if r['id']=='trade-limitations')
old['text']=old['text'].replace('交割时间有夜间记账记录，不能验证精准盘中买点、打板成交或封板强弱。','交割时间含夜间记账记录，不能普遍当作真实成交时刻。本轮补查5个交易日的分钟线，12条记录价格与对应5分钟区间一致；仍不能独立认证成交时钟、逐笔封板或队列成交。')
d['buildStatus']='updating'
d['generatedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat()
R.write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False))
print('report_data_updated',len(d['queries']),d['id'])
