from pathlib import Path
import json,io,contextlib,ast,html
P=Path(__file__).resolve().parent
cells=[]
def md(s):cells.append({'cell_type':'markdown','metadata':{},'source':s.splitlines(keepends=True)})
def code(s):cells.append({'cell_type':'code','metadata':{},'source':s.splitlines(keepends=True),'execution_count':None,'outputs':[]})
md('# 四项交易追问核验\n\n只复用已保存交割单及日K，不连接券商，不下单。价格基准与实际策略分开。')
code("import json, statistics as st\nfrom pathlib import Path\nP=Path.cwd()\nB=P.parent\nf=json.loads((B/'v2/episode_features.json').read_text())\ng=json.loads((B/'v2/gap_analysis.json').read_text())['episodes']\ne=json.loads((B/'episodes_enriched.json').read_text())\na=json.loads((P/'add_risk_rows.json').read_text())\nassert len(f)==len(g)==158\nprint('样本：158轮已平仓且成本已知的二级市场交易；数据截至2026-10-16。')")
md('## 1. 四条件交集\n下降趋势、市场前日成交额低于2.3万亿、科技、个股前5日平均成交额超过30亿。')
code("def cut(rs):\n    return [r for r in rs if r['market_regime']=='大盘下降趋势' and r['market_previous_amount_yuan']<2.3e12 and r['technology'] and r['liquid_over_30yi']]\nup=[r for r in f if r['entry_pattern'] in ['上升趋势且靠近5日线','上升趋势其他买入']]\nfor label,rs in [('全部158',cut(f)),('回落28',cut(up))]:\n    print(label,len(rs),'胜',sum(r['analysis_pnl']>0 for r in rs),'盈亏',round(sum(r['analysis_pnl'] for r in rs),2))\nassert len(cut(f))==12 and len(cut(up))==0")
md('## 2. 次日开盘\n58轮描述只用价格；含费基准排除3笔推定开盘跌停与1笔未重建公司行动，保留54轮。全追涨组另有1笔公司行动被排除，保留99轮。')
code("ch=[r for r in g if r['original_entry_pattern']=='明显追涨或远离5日线']\nlos=[r for r in ch if r['actual_pnl']<0]\nok=[r for r in ch if r['strategies']['next_market_open']['eligible']]\nassert len(ch)==104 and len(los)==58 and len(ok)==99\nprint('58轮开盘价格收益中位数',round(st.median(r['next_open_vs_first_day_cost_pct'] for r in los),6))\nfor label,rs in [('亏损54',[r for r in ok if r['actual_pnl']<0]),('全部99',ok)]:\n    ss=[r['strategies']['next_market_open'] for r in rs]\n    print(label,'含费中位数',round(st.median(s['return_on_first_day_cost_pct'] for s in ss),6),'净胜率',round(sum(s['price_benchmark_pnl']>0 for s in ss)/len(ss)*100,6),'开盘盈亏',round(sum(s['price_benchmark_pnl'] for s in ss),2),'同首日股份实际',round(sum(r['first_day_actual_pnl'] for r in rs),2))")
md('## 3. 过渡分类\n前日上证收盘与MA20位置和MA20相对5个交易日前的方向不一致。未包含量能与情绪。')
code("tr=[x for x in e if x.get('entry_pattern')=='明显追涨或远离5日线' and x.get('market_regime')=='大盘震荡过渡' and x['status']=='closed' and x['initial_cost_known'] and x['origin']=='secondary']\nassert len(tr)==37\nfor above in [False,True]:\n    rs=[x for x in tr if (x['index_features']['previousClose']>x['index_features']['ma20'])==above]\n    print('指数高于MA20',above,'轮数',len(rs),'盈亏',round(sum(x['trade_pnl'] for x in rs),2))")
md('## 4. 加仓归因\n浮亏加仓不自动等于买入逻辑失败。市场分类按每次加仓日前数据，实际归因只看新增股份。')
code("assert len(a)==84 and sum(x['below_cost'] for x in a)==42\nfor reg in ['上升','下降','过渡']:\n    rs=[x for x in a if x['below_cost'] and x['regime']==reg]\n    print(reg,len(rs),'条，新增股份实际盈亏',round(sum(x['actual_added_lot_pnl'] for x in rs),2))")
md('## 解释边界\n最终亏损是事后筛选，不能把54轮早卖改善当成全组策略优势。2.3万亿和30亿不是已证明最优的阈值。加仓禁令是待前瞻验证的风险纪律，原始记录不足以重建事前心理预期或失效点。')
ns={};count=0;parts=[]
import os
old=os.getcwd();os.chdir(P)
try:
 for c in cells:
  s=''.join(c['source'])
  if c['cell_type']=='code':
   ast.parse(s);buf=io.StringIO();count+=1
   with contextlib.redirect_stdout(buf):exec(compile(s,'audit-cell','exec'),ns)
   c['execution_count']=count;c['outputs']=[{'output_type':'stream','name':'stdout','text':buf.getvalue().splitlines(keepends=True)}]
   parts.append('<pre>'+html.escape(buf.getvalue())+'</pre>')
  else:parts.append('<div>'+html.escape(s).replace('\n','<br>')+'</div>')
finally:os.chdir(old)
nb={'nbformat':4,'nbformat_minor':4,'metadata':{'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'},'language_info':{'name':'python','version':'3'},'execution_note':'Cells executed top-to-bottom in one fresh Python namespace; no Jupyter kernel dependency.'},'cells':cells}
(P/'追问核验.ipynb').write_text(json.dumps(nb,ensure_ascii=False,indent=2))
(P/'追问核验预览.html').write_text('<!doctype html><meta charset="utf-8"><title>交易追问核验</title><style>body{font:16px system-ui;max-width:900px;margin:40px auto;line-height:1.7;color:#202020}div{margin:22px 0}pre{background:#f4f5f6;padding:16px;white-space:pre-wrap}</style>'+''.join(parts))
print('Notebook: 5 cells executed in order; all assertions passed. Preview saved.')
