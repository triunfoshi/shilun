"""Build a reproducible journal import from reviewed archives, without DB writes.

The normalized July archive is checked against every original worksheet cell
used as an execution fact. Recent records retain the broker's real fill IDs.
"""
import argparse
import base64
from decimal import Decimal
import hashlib
import json
from pathlib import Path


def source(path, source_id, *, binary=False):
    content = path.read_bytes()
    return {"source_id": source_id, "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_encoding": "base64" if binary else "utf-8",
        "content": base64.b64encode(content).decode() if binary else content.decode("utf-8"),
        "source_type": "broker_page_archive", "evidence_class": "historical_reconstruction"}


def build_payload(old_xlsx, old_normalized, recent_json, *, account_alias="primary"):
    import openpyxl
    rows = json.loads(old_normalized.read_text())
    recent = json.loads(recent_json.read_text())
    workbook = openpyxl.load_workbook(old_xlsx, read_only=True, data_only=True)
    sheet = workbook["交割单"]
    raw = list(sheet.iter_rows(min_row=6, max_col=17, values_only=True))
    populated = [r for r in raw if r[0] and r[5]]
    if len(populated) != len(rows):
        raise ValueError("原件与标准化档案记录数不一致；停止导入")
    for row in rows:
        actual = raw[int(row["source_row_number"]) - 1]
        if str(actual[1])[:10] != row["date"] or actual[5] != row["kind"] or actual[4] != row["name"]:
            raise ValueError("原始来源日期、业务或名称与归档不符")
        for index, field in ((6,"qty"),(7,"price"),(8,"gross"),(9,"net"),
                (10,"commission"),(11,"regulatory_fee"),(12,"stamp_tax"),(13,"transfer_fee"),(14,"balance_qty")):
            if Decimal(str(actual[index] or 0)) != Decimal(str(row[field] or 0)):
                raise ValueError(f"原始行{row['source_row_number']}字段{field}与归档不符")
        if row["kind"] in {"证券买入","证券卖出","新股入账","配售缴款"}:
            if str(actual[3]).zfill(6) != row["code"]:
                raise ValueError("证券代码与原件不符")
    workbook.close()
    old_source = source(old_xlsx, "broker-archive-" + hashlib.sha256(old_xlsx.read_bytes()).hexdigest(), binary=True)
    recent_source = source(recent_json, "broker-archive-" + hashlib.sha256(recent_json.read_bytes()).hexdigest())
    fills, financial = [], []
    for row in rows:
        fact = dict(row, source_ref={"source_id":old_source["source_id"], "sheet":"交割单", "row":int(row["source_row_number"])+5})
        if row["kind"] in {"证券买入", "证券卖出"}:
            fact["fill_id"] = f"archive-{old_source['sha256'][:16]}-{row['row_id']}"
            fills.append(fact)
        else:
            fact["financial_event_id"] = f"financial-{old_source['sha256'][:16]}-{row['row_id']}"
            if row["code"] == "787825":
                fact["linked_ticker"] = "688825.SH"
            financial.append(fact)
    for row in recent["records"]:
        fills.append({"code":row["code"], "name":row["name"], "date":row["date"], "time":row["time"],
            "statement_event_time":row["occurrence_time"], "side":row["side"],
            "quantity":row["quantity"], "price":row["price"], "amount":row["amount"],
            "fees":row["fees"], "net_cash":row["net_cash"], "quantity_after":row["stock_balance"],
            "broker_execution_id":row["broker_deal_id"], "time_precision":"execution_time",
            "source_ref":{"source_id":recent_source["source_id"], "record_id":row["record_id"],
                "row":row["source_row"], "sheet":row["source_page"]}})
    if len(fills) != 495 or len(financial) != 11:
        raise ValueError("本次已核对归档应为495笔成交、11笔非买卖事件，收到不同范围")
    return {"account_alias":account_alias, "sources":[old_source,recent_source],
            "fills":fills, "financial_events":financial}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-xlsx", type=Path, required=True)
    parser.add_argument("--old-normalized", type=Path, required=True)
    parser.add_argument("--recent-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--account-alias", default="primary")
    args=parser.parse_args()
    result=build_payload(args.old_xlsx,args.old_normalized,args.recent_json,account_alias=args.account_alias)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({"payload":str(args.output),"fills":len(result["fills"]),"financial_events":len(result["financial_events"]),
        "source_hashes":[s["sha256"] for s in result["sources"]]},ensure_ascii=False))


if __name__ == "__main__":
    main()
