from pathlib import Path
import json,sys,time,threading,concurrent.futures
sys.path.insert(0,'/Users/shibicheng/shilun_standalone')
from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.data.providers.tushare_provider import TushareDailyClient,TushareConfig
P=Path(__file__).parent;cfg=load_config();store=MongoSnapshotStore(cfg.mongo_uri,cfg.mongo_db,initialize_indexes=False,socket_timeout_ms=30000)
collections=[n for n in store.db.list_collection_names() if any(k in n.lower() for k in ['sw_industry','sw_member','shenwan','index_member_all'])]
print(json.dumps({'existing_sw_collection_names':collections}),flush=True)
eps=json.load(open(P.parent/'episodes_enriched.json'));stocks=sorted({e['code']+('.SH' if e['code'][0] in '569' else '.SZ') for e in eps if e['asset_type']!='ETF'})
local=[]
for name in collections:
 for r in store.collection(name).find({'ts_code':{'$in':stocks}},{'_id':0}): local.append({**r,'storage_collection':name})
(P/'sw_local_rows.json').write_text(json.dumps(local,ensure_ascii=False,indent=2,default=str))
current={r['ts_code']:r for r in local if r.get('is_new')=='Y' and r.get('l1_name')}
for t in stocks:
 sample=P/('sw_sample_'+t+'.json')
 if sample.exists():
  rows=json.load(open(sample))
  if len(rows)==1: current[t]=rows[0]
lock=threading.Lock();last=[0.0]
def call(t):
 client=TushareDailyClient(TushareConfig(cfg.tushare_token,cfg.tushare_base_url,timeout=20,min_interval_seconds=0))
 with lock:
  pause=max(0,0.6-(time.monotonic()-last[0]))
  if pause:time.sleep(pause)
  last[0]=time.monotonic()
 try:return t,client._call_api('index_member_all',ts_code=t,is_new='Y').to_dict('records'),None
 except Exception as ex:return t,[],type(ex).__name__
results=list(current.values());errors=[]
todo=[t for t in stocks if t not in current]
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
 for i,(t,rows,error) in enumerate(pool.map(call,todo)):
  results.extend(rows)
  if error:errors.append({'ticker':t,'error':error})
  if (i+1)%10==0:print(json.dumps({'done':i+1,'total':len(todo),'mapped_rows':len(results)}),flush=True)
(P/'sw_current.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
(P/'sw_errors.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2))
print(json.dumps({'complete':True,'stocks':len(stocks),'rows':len(results),'errors':errors}),flush=True)
