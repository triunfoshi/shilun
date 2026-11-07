"""Read-only dated coverage and safe ingestion receipts; no provider requests."""
import json
from datetime import datetime, timezone
from pathlib import Path
from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore

config=load_config()
store=MongoSnapshotStore(config.mongo_uri,config.mongo_db,initialize_indexes=False,socket_timeout_ms=30000)
try:
    dates=['2026-10-28','2026-10-29','2026-10-30','2026-11-02']
    result={'captured_at_utc':datetime.now(timezone.utc).isoformat(),'source':'configured_ecs_mongo','dates':{}}
    for date in dates:
        scope=date.replace('-','')
        result['dates'][date]={
            'daily_bars':store.collection('market_daily_bars').count_documents({'date':date}),
            'minute_windows':store.collection('minute_bars_daily').count_documents({'trade_date':scope}),
            'limit_up_pool_rows':store.collection('limit_up_pool').count_documents({'trade_date':scope}),
            'minute_by_interval':list(store.collection('minute_bars_daily').aggregate([
                {'$match':{'trade_date':scope}}, {'$group':{'_id':'$interval','windows':{'$sum':1},'tickers':{'$addToSet':'$ticker'}}},
                {'$project':{'_id':0,'interval':'$_id','windows':1,'ticker_count':{'$size':'$tickers'}}}]))}
    fields=['dataset','scope','status','updated_at','started_at','finished_at','trade_date','analysis_date','requested_date',
        'error_type','row_count','record_count','fetched_count','fetched','failed','skipped','success','missing','candidates',
        'failed_tickers','interval','minute_interval','count','source','recorded_at','generated_at']
    projection={key:1 for key in fields};projection['_id']=0
    result['sync_receipts']=list(store.collection('sync_state').find({'dataset':{'$regex':'minute|limit_up|daily_data_pipeline|tushare|taxonomy'}},projection))
    for name in ('minute_bars_daily','limit_up_pool'):
        row=store.collection(name).find_one({'trade_date':'20261102'}) or {}
        result[name+'_sample_schema']=sorted(row)
        result[name+'_sample_metadata']={key:row[key] for key in ('ticker','trade_date','interval','bar_count','row_count','source','fetched_at','updated_at','generated_at') if key in row}
    result['breakout_events_total']=store.collection('breakout_events').count_documents({})
    out=Path('outputs/01a17178-dec2-7a91-91e1-cc7e68aa661e/sync_audit_20261102')
    out.mkdir(parents=True,exist_ok=True)
    (out/'mongo_coverage.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    print(json.dumps(result,ensure_ascii=False,default=str))
finally:
    store.close()
