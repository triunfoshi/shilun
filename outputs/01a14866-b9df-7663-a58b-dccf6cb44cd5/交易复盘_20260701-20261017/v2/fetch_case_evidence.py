from pathlib import Path
import json,sys,datetime
sys.path.insert(0,'/Users/shibicheng/shilun_standalone')
from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.data.providers.tushare_provider import TushareDailyClient,TushareConfig
P=Path(__file__).parent
cfg=load_config(); st=MongoSnapshotStore(cfg.mongo_uri,cfg.mongo_db,initialize_indexes=False,socket_timeout_ms=15000)
cl=TushareDailyClient(TushareConfig(cfg.tushare_token,cfg.tushare_base_url,timeout=20,min_interval_seconds=cfg.tushare_min_interval_seconds))
dates=['2026-09-22','2026-09-23','2026-09-24']
evidence={}
for coll, field in [('review_market_mood','date'),('daily_review_snapshots','analysis_date'),('review_board_pools','date'),('review_previous_limit_pools','date')]:
    rows=list(st.collection(coll).find({field:{'$in':dates}},{'_id':0}))
    evidence[coll]=rows
    print(coll, len(rows), [list(x.keys()) for x in rows[:1]],flush=True)
(P/'case_sentiment_local.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str))
pairs=[('600664.SH','2026-09-23'),('600664.SH','2026-09-24'),('002384.SZ','2026-07-17'),('601086.SH','2026-09-16'),('601086.SH','2026-09-17')]
minutes=[];errors=[]
for t,d in pairs:
    try:
        df=cl._call_api('stk_mins',ts_code=t,freq='5min',start_date=d+' 09:00:00',end_date=d+' 15:00:00')
        rows=df.to_dict('records')
        minutes.append({'ticker':t,'date':d,'source':'existing_configured_api_stk_mins','bars':rows})
        print('API_MINUTES',t,d,len(rows),list(df.columns),flush=True)
    except Exception as ex:
        errors.append({'ticker':t,'date':d,'error_type':type(ex).__name__})
        print('API_MINUTES_ERROR',t,d,type(ex).__name__,flush=True)
    (P/'case_minutes_api.json').write_text(json.dumps(minutes,ensure_ascii=False,indent=2,default=str))
(P/'case_minutes_errors.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2))
