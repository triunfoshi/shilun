from pathlib import Path
import json,sys,datetime
sys.path.insert(0,'/Users/shibicheng/shilun_standalone')
from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.data.providers.tushare_provider import TushareDailyClient,TushareConfig
P=Path(__file__).parent;cfg=load_config();store=MongoSnapshotStore(cfg.mongo_uri,cfg.mongo_db,initialize_indexes=False,socket_timeout_ms=30000)
client=TushareDailyClient(TushareConfig(cfg.tushare_token,cfg.tushare_base_url,timeout=20,min_interval_seconds=cfg.tushare_min_interval_seconds))
for ticker in ['399107.SZ','000002.SH']:
 rows=list(store.collection('market_daily_bars').find({'ticker':ticker,'date':{'$gte':'2026-04-01','$lte':'2026-10-16'}},{'_id':0}).sort('date',1))
 (P/(ticker+'_local.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2,default=str))
 start=(datetime.date.fromisoformat(rows[-1]['date'])+datetime.timedelta(days=1)).strftime('%Y%m%d') if rows else '20260401'
 raw=client._call_api('index_daily',ts_code=ticker,start_date=start,end_date='20261016').to_dict('records')
 (P/(ticker+'_api.json')).write_text(json.dumps(raw,ensure_ascii=False,indent=2,default=str))
 print(json.dumps({'ticker':ticker,'local':len(rows),'api':len(raw),'first':raw[-1] if raw else None,'last':raw[0] if raw else None}),flush=True)
