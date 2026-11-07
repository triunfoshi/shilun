from pathlib import Path
import json,sys,datetime
sys.path.insert(0,'/Users/shibicheng/shilun_standalone')
from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
P=Path(__file__).parent
cfg=load_config();st=MongoSnapshotStore(cfg.mongo_uri,cfg.mongo_db,initialize_indexes=False,socket_timeout_ms=15000)
COLLS=st.collection('minute_bars_daily').database.list_collection_names()
print('relevant_collections',json.dumps([n for n in COLLS if any(s in n for s in ['limit','emotion','sentiment','breadth','market','review','snapshot'])],ensure_ascii=False))
pairs=[('600664.SH','20260923'),('600664.SH','20260924'),('002384.SZ','20260717'),('601086.SH','20260916'),('601086.SH','20260917')]
raw=[]
for t,d in pairs:
 docs=list(st.collection('minute_bars_daily').find({'ticker':t,'trade_date':d},{'_id':0}))
 raw+=docs
 print('minute_local',t,d,[(x.get('interval'),len(x.get('bars',[]))) for x in docs])
(P/'case_minutes_local.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2,default=str))
