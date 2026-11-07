from pathlib import Path
import json
B=Path(__file__).resolve().parent.parent;O=B/'v4';d=json.loads((O/'monthly_modes.json').read_text());items=[]
def q(id,label,definition,rows,files,caveats,calc,period='2026-07-01至2026-10-16'):
 return {'id':id,'source':{'label':label,'files':[{'label':x} for x in files],'metricDefinitions':[{'label':label,'definition':definition}],'caveats':caveats},'reportingPeriod':period,'columns':list(rows[0]),'rows':rows,'methods':[{'language':'calculation','code':calc}]}
m=[{'月份_首次买入':r['entry_month'],'当月追涨总轮数':r['chase_count'],'亏损轮数':r['losing_count'],'占58轮_pct':round(r['share_of_58_pct'],2),'当月亏损比例_pct':round(r['loss_rate_pct'],2),'亏损交易合计_元':r['losing_sum'],'当月全部追涨净盈亏_元':r['all_chase_net_pnl'],'当月最大亏损股':r['largest_loss_name'],'当月最大亏损_元':r['largest_loss_pnl']} for r in d['monthly_chase_losses']]
closing=[{'月份_最后卖出':r['closing_month'],'亏损轮数':r['count'],'亏损合计_元':r['pnl']} for r in d['monthly_by_closing_date']]
items.append({'id':'loss-months','title':'58轮追涨亏损交易的月份分布','queries':[q('entry-months','东财交割单复盘','从104轮明显追涨或远离5日线交易中选最终亏损58轮，以首次买入月份归属整轮含费盈亏。',m,['episodes_enriched.json','monthly_modes.json'],['一轮是从建仓到清仓，不是单次成交；只含已平仓、成本已知的二级市场交易，排除分红税费、新股及未平仓。','10月只覆盖截至16日，不能与完整月份作等长比较。','亏损轮数占58轮的比例和当月亏损率使用不同分母。'],'亏损分布占比=当月亏损轮数/58。\n当月亏损率=当月亏损轮数/当月全部追涨轮数。\n58轮亏损合计-46507.41元；46轮盈利合计35263.35元，104轮净盈亏-11244.06元。'),q('exit-months','清仓月份敏感性','按最后一次卖出月份归属同样58轮，检验跨月影响。',closing,['episodes_enriched.json'],['汉森制药002412-E001于8月27日首买、9月1日清仓，亏损4387.12元；这是58轮中唯一跨月交易。'],'按last_sell_date月份分组；跨月整轮只归属一次。')]})
keys=[('chase_transition37','追涨或远离MA5×大盘过渡'),('chase_never_belowcost88','追涨且未低于成本加仓'),('chase_noadd76','追涨且不加仓'),('stock_down13','个股下跌形态中买入'),('up_nonchase28','均线上行的非追涨买入'),('chase_belowcost_add16','追涨且曾低于成本加仓'),('chase_entry_up3','首买较昨收上涨至少3%')]
rows=[]
for key,label in keys:
 r=d['cohorts'][key];rows.append({'统计标签':label,'轮数':r['count'],'净盈亏_元':r['pnl'],'胜率_pct':r['win_rate_pct'],'收益中位数_pct':r['median_return_pct'],'剔除最佳一轮盈亏_元':r['without_best'],'剔除8月盈亏_元':r['without_august']})
items.append({'id':'mode-results','title':'盈利候选与持续亏损组合','queries':[q('cohorts','交易分组复算','沿用原入场价格与均线分类，并检查盈利是否依赖单笔或单月。追涨优先于个股趋势分类，满足首买较昨收至少3%或较此前MA5至少8%。',rows,['episodes_enriched.json','monthly_modes.json'],['各行有重叠，不能相加；均线上行非追涨28轮与追涨104轮互斥。','原大盘过渡仅指上证前收盘与MA20方向不一致；没有确认主线、核心、承接或情绪。','未在成本下加仓是事后行为分组，不能把其净收益解释为事先禁加仓的可实现回测。','剔除最好交易或8月是集中度检查，不是可事先执行的筛选策略。','国芳被日线公式归入均线上行组，但用户明确说明原意为翘板；不能把28轮都认作主观趋势低吸。'],'每轮收益率=实际含费交易盈亏/累计买入含费成本；中位数各轮等权。\n37轮过渡追涨中27轮在8月，8月贡献6395.23元；剔除8月余30.03元。\n13轮个股下跌形态买入净赚2501.90元，剔除东山7月21日4339.71元后亏1837.81元。')]})
# Paired examples retain only pre-entry environmental inputs.
f=json.loads((B/'v2/episode_features.json').read_text());rows=[]
for ep in ['002428-E001','603618-E001','002384-E002','600664-E004','601086-E001']:
 r=next(x for x in f if x['episode_id']==ep);rows.append({'股票':r['name'],'首次买入':r['first_buy_date'],'实际盈亏_元':r['analysis_pnl'],'前日市场量变_pct':r['market_previous_day_change_pct'],'前日量对20日均量_pct':r['market_previous_vs_20d_ratio']*100,'前5日个股平均额_亿元':r['stock_prior_5d_avg_amount_yi'],'首买较昨收_pct':r['entry_vs_previous_close_pct'],'原大盘分类':r['market_regime']})
items.append({'id':'market-context','title':'成功与失败案例的当时可见环境','queries':[q('case-pairs','交割单及石论日K存档','环境量能只用首次买入前已完成交易日；具体买入意图、核心地位和盘中承接不由这些数字自动确认。',rows,['episode_features.json','market_additions.json'],['这些是解释性案例，不能据单笔成败认定方法有效。','总额用上证综指加深证A指，含沪市B股、不含北交所；不同风格可同时存在。','A392/A393是作者当时的盘后记录；次日盘后文章不能倒填成当天买入时已知证据。'],'前日量变=前日总成交额/前前日总成交额-1。\n前日量对20日均量=前日总成交额/截至前日20交易日平均额。')]})
refs=[
 {'出处':'A379:P04—P10','日期':'2026-07-17','类别':'原文核验后释义','含义':'东山和科技趋势破位环境；先观察指数企稳小阳后是否有套利。','边界':'未给2.3万亿或30亿通用阈值；量化归因未独立核验。'},
 {'出处':'A334:P04、P06','日期':'2026-05-14','类别':'原文核验后释义','含义':'趋势破5日线且次日未快速收回应退出，指数不再主升时不要盲用5日线。','边界':'针对当时趋势模式，不给每一笔失败交易统一多等一天。'},
 {'出处':'A279:P05—P06','日期':'2026-02-06','类别':'原文核验后释义','含义':'反对机械竞价不及预期就割；指数反弹套利不宜过高预期。','边界':'既不支持低开必割，也不支持低开必等。'},
 {'出处':'A024:P07—P08','日期':'2025-01-21','类别':'原文核验后释义','含义':'风险控制与仓位管理，不确定时空仓轻仓。','边界':'不复制作者激进仓位语气，不编造通用比例。'},
 {'出处':'A253:P12—P13','日期':'2025-12-31','类别':'原文核验后释义','含义':'无需参与所有行情，定位自己的模式，等待相合环境。','边界':'作者关于出手就赚钱的夸张表达不作为收益承诺。'},
 {'出处':'A385:P03—P05','日期':'2026-07-27','类别':'原文核验后释义','含义':'总增量可能集中于新股，应检查其余市场可用资金。','边界':'缩量抱团只是当时观察，不是缩量必有抱团的定律。'},
 {'出处':'A392:P05—P06、A393:P03—P05','日期':'2026-08-05至08-06','类别':'成对原文核验后蒸馏','含义':'先看放量日强股下一日是否给正溢价，再提高预期；集体回落时降低预期。','边界':'次日盘后确认属后续证据；作者当时4000点等参数不沿用。'}]
items.append({'id':'xie-framework','title':'蟹老板心法的出处与使用边界','queries':[q('verified-paragraphs','用户提供的蟹老板文章合集','通过本地原文查询脚本核验指定段落；本文的市场观察清单、放大与收缩流程为结合交割单的整理补充。',refs,['蟹老板心法 SKILL.md','原文查询脚本 source_lookup.py','原文章合集 DOCX'],['原文观点和资金归因不等于已验证行情事实或有效策略；已读演变与反例章节。','现有158轮未完整标注题材核心、换手接力、买入预期和时限，不把盈利统计直接命名为核心战法。'],'文章ID与P段落定位；4处由独立复核任务核验，A253、A385、A392、A393由主任务核验，原DOCX哈希校验通过。','2025-01-21至2026-08-06被引用原文；合集截至2026-09-21')]})
(O/'answer_sources.json').write_text(json.dumps({'schemaVersion':1,'items':items},ensure_ascii=False,indent=2))
print('4 findings,',sum(len(i['queries']) for i in items),'queries,',sum(len(q['rows']) for i in items for q in i['queries']),'rows')
