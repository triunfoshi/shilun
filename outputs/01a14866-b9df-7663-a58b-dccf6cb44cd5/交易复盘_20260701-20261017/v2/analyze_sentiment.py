from pathlib import Path
import json,sys,statistics
sys.path.insert(0,'/Users/shibicheng/shilun_standalone')
from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
P=Path(__file__).parent
raw=json.load(open(P/'case_sentiment_local.json'))
sn={r['analysis_date']:r for r in raw['daily_review_snapshots'] if r['engine_version']=='daily_review_v1.9_eastmoney_taxonomy'}
stocks={d:[x for l in r['sentiment']['ladder']['levels'] for x in l['stocks']] for d,r in sn.items()}
tickers=sorted({x['ticker'] for rs in stocks.values() for x in rs})
cfg=load_config();st=MongoSnapshotStore(cfg.mongo_uri,cfg.mongo_db,initialize_indexes=False,socket_timeout_ms=15000)
bars=list(st.collection('market_daily_bars').find({'ticker':{'$in':tickers},'date':{'$in':list(sn)}},{'_id':0}))
limits=list(st.collection('daily_price_limits').find({'ticker':{'$in':tickers},'trade_date':{'$in':list(sn)+[d.replace('-','') for d in sn]}},{'_id':0}))
bm={(r['ticker'],r['date']):r for r in bars}
lm={(r['ticker'],str(r['trade_date'])[:10].replace('-','')):r for r in limits}
out={'source':'石论收盘归档，按当前日复盘引擎重建；并非盘中可见状态','days':[],'cohorts':[]}
for d,r in sorted(sn.items()):
 s=r['sentiment'];m=s['market_mood'];indices=[x for x in json.load(open(P.parent/'market/daily_bars.json')) if x['date']==d and x['ticker'] in ['000001.SH','000300.SH','399006.SZ']]
 out['days'].append({'date':d,'generated_at':r['generated_at'],'mood_score':m.get('score'),'mood_label':m.get('label'),'limit_up_count':s['limit_up_count'],'limit_down_count':s['limit_down_count'],'highest_board':s['ladder']['highest_board'],'consecutive_count':sum(x.get('consecutive_count',0)>=2 for x in stocks[d]),'components':m['components'],'downside_risk_label':s['downside_risk']['label'],'downside_penalty':s['downside_risk']['penalty'],'down_breadth':s['downside_risk'].get('down_breadth'),'index_returns':{x['ticker']:x.get('pct_chg',100*(x['close']/x['pre_close']-1)) for x in indices}})
def cohort(prior,failday,follow):
 priorrows=[x for x in stocks[prior] if x.get('consecutive_count',0)>=2];now={x['ticker'] for x in stocks[failday]}
 failed=[x for x in priorrows if x['ticker'] not in now];rs=[]
 for x in failed:
  b=bm.get((x['ticker'],follow),{});l=lm.get((x['ticker'],follow.replace('-','')),{});pc=b.get('pre_close') or l.get('pre_close');ret=100*(b['close']/pc-1) if b.get('close') and pc else None
  down=l.get('down_limit') or l.get('limit_down');rs.append({'ticker':x['ticker'],'name':x['name'],'prior_boards':x['consecutive_count'],'close':b.get('close'),'return_pct':ret,'down_limit':down,'at_down_limit':abs(b['close']-down)<.005 if b.get('close') and down else None})
 rets=[x['return_pct'] for x in rs if x['return_pct'] is not None]
 return {'prior_date':prior,'failure_date':failday,'outcome_date':follow,'prior_count':len(priorrows),'failed_count':len(failed),'promoted_count':len(priorrows)-len(failed),'return_count':len(rets),'mean_return_pct':statistics.mean(rets) if rets else None,'median_return_pct':statistics.median(rets) if rets else None,'down_count':sum(v<0 for v in rets),'down5_count':sum(v<=-5 for v in rets),'verified_down_limit_count':sum(x['at_down_limit'] is True for x in rs),'rows':rs}
out['cohorts']=[cohort('2026-09-23','2026-09-24','2026-09-24'),cohort('2026-09-22','2026-09-23','2026-09-24')]
(P/'sentiment_analysis.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str))
(P/'sentiment_cohort_bars.json').write_text(json.dumps({'bars':bars,'limits':limits},ensure_ascii=False,indent=2,default=str))
print(json.dumps(out,ensure_ascii=False,indent=2))
