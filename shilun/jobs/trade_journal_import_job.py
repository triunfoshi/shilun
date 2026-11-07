"""Preview/apply a reviewed journal payload against the configured ECS MongoDB."""
import argparse
import hashlib
import json
from pathlib import Path

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.trade_journal.indexes import ensure_journal_indexes
from shilun.trade_journal.service import TradeJournalService


def protected_state_hashes(store):
    """Detect accidental writes to production holdings/cash/execution collections."""
    result={}
    for name in ("holdings","account_risk_profiles","trade_executions","trade_orders"):
        rows=list(store.collection(name).find({}, {"_id":0}))
        encoded=json.dumps(sorted(rows,key=lambda r:json.dumps(r,sort_keys=True,default=str)),
            ensure_ascii=False,sort_keys=True,default=str,separators=(",",":"))
        result[name]={"count":len(rows),"sha256":hashlib.sha256(encoded.encode()).hexdigest()}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload",type=Path,required=True)
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--expected-digest")
    parser.add_argument("--receipt",type=Path)
    args=parser.parse_args()
    config=load_config()
    if not config.mongo_uri:
        parser.error("现有MongoDB连接未配置；账本不回退到本地数据库")
    if args.apply and not args.expected_digest:
        parser.error("写入必须提供已审查预览的--expected-digest")
    payload=json.loads(args.payload.read_text())
    store=MongoSnapshotStore(config.mongo_uri,config.mongo_db,socket_timeout_ms=30000,initialize_indexes=False)
    try:
        service=TradeJournalService(store)
        preview=service.preview_import(payload)
        receipt={"operation":"apply" if args.apply else "preview","preview":preview}
        if args.apply:
            if preview["preview_digest"] != args.expected_digest:
                raise ValueError("输入或现有数据已变化，请重新审查预览")
            if not preview["can_import"]:
                raise ValueError("预览存在冲突，停止写入")
            before=protected_state_hashes(store)
            ensure_journal_indexes(store)
            receipt["result"]=service.import_batch(payload,args.expected_digest)
            after=protected_state_hashes(store)
            receipt["production_collections_unchanged"]=before==after
            receipt["protected_before"]=before;receipt["protected_after"]=after
            receipt["summary"]=service.get_summary()
            if before!=after:
                receipt["verification_note"]="写入窗口内生产集合发生变化；需核对并发真实操作，不执行自动回滚"
        if args.receipt:
            args.receipt.parent.mkdir(parents=True,exist_ok=True)
            args.receipt.write_text(json.dumps(receipt,ensure_ascii=False,indent=2,default=str))
        print(json.dumps(receipt,ensure_ascii=False,default=str))
    finally:
        store.close()


if __name__=="__main__":
    main()
