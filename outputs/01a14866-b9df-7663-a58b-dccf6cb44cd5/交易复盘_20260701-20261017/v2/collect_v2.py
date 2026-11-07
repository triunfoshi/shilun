from pathlib import Path
import json,sys,datetime
sys.path.insert(0,'/Users/shibicheng/shilun_standalone')
from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.data.providers.tushare_provider import TushareDailyClient,TushareConfig
P=Path(__file__).parent
def dump(n,v): (P/n).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str))
cfg=load_config();store=MongoSnapshotStore(cfg.mongo_uri,cfg.mongo_db,initialize_indexes=False,socket_timeout_ms=30000)
local=list(store.collection('market_daily_bars').find({'ticker':'399001.SZ','date':{'$gte':'2026-04-01','$lte':'2026-10-16'}},{'_id':0}).sort('date',1))
dump('sz_index_local.json',local)
print(json.dumps({'stage':'local_index','rows':len(local),'last':local[-1]['date'] if local else None}),flush=True)
client=TushareDailyClient(TushareConfig(cfg.tushare_token,cfg.tushare_base_url,timeout=20,min_interval_seconds=cfg.tushare_min_interval_seconds))
start=(datetime.date.fromisoformat(local[-1]['date'])+datetime.timedelta(days=1)).strftime('%Y%m%d') if local else '20260401'
raw=client._call_api('index_daily',ts_code='399001.SZ',start_date=start,end_date='20261016').to_dict('records')
dump('sz_index_api.json',raw)
print(json.dumps({'stage':'api_index','rows':len(raw)}),flush=True)
codes={e['code'] for e in json.load(open(P.parent/'episodes_enriched.json'))}
basic=client.fetch_stock_basic(fields='ts_code,symbol,name,area,industry,market,list_date,list_status').to_dict('records')
basic=[r for r in basic if r['symbol'] in codes]
dump('stock_basic_current.json',basic)
for code in ['300285.SZ','000938.SZ','600127.SH']:
 try:
  sw=client._call_api('index_member_all',ts_code=code,is_new='Y').to_dict('records')
  dump('sw_sample_'+code+'.json',sw)
  print(json.dumps({'stage':'sw_sample','ticker':code,'rows':len(sw),'example':sw[:1]},ensure_ascii=False),flush=True)
 except Exception as ex:print(json.dumps({'stage':'sw_error','ticker':code,'type':type(ex).__name__}),flush=True)
