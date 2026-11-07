"""Read-only audit of a review's prior limit-up cohort and price coverage.

Run with the project virtualenv. Does not sync, create indexes, or write MongoDB.
No credentials or raw upstream errors are printed.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import math
from statistics import median
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient
from shilun.common.config import load_config


def audit(db, requested_date=None):
    query = {"analysis_date": requested_date} if requested_date else {}
    snapshot = db.daily_review_snapshots.find_one(query, {
        "_id": 0, "analysis_date": 1, "previous_trade_date": 1, "generated_at": 1,
        "sentiment.previous_limit_up_count": 1, "sentiment.yesterday_limit_up_feedback": 1,
        "source_sync": 1,
    }, sort=[("analysis_date", -1), ("generated_at", -1)])
    if not snapshot:
        return {"status": "no_snapshot", "requested_date": requested_date}
    target, previous = snapshot["analysis_date"], snapshot.get("previous_trade_date")
    start = (date.fromisoformat(target) - timedelta(days=10)).isoformat()
    calendar = list(db.trade_calendar.find(
        {"cal_date": {"$gte": start.replace("-", ""), "$lte": target.replace("-", "")}},
        {"_id": 0, "cal_date": 1, "is_open": 1, "exchange": 1}))
    dates = sorted({target, *([previous] if previous else []), *[
        f'{str(row["cal_date"])[:4]}-{str(row["cal_date"])[4:6]}-{str(row["cal_date"])[6:8]}'
        for row in calendar if str(row.get("is_open")) in ("1", "True")
    ]})
    counts = []
    for day in dates:
        forms = [day, day.replace("-", "")]
        pool = list(db.limit_up_pool.find({"trade_date": {"$in": forms}}, {"_id": 0, "ticker": 1, "ts_code": 1}))
        keys = {row.get("ticker") or row.get("ts_code") for row in pool}
        states = list(db.sync_state.find(
            {"dataset": {"$in": ["eastmoney_limit_up_pool", "akshare_limit_up_pool"]}, "scope": {"$in": forms}},
            {"_id": 0, "status": 1, "scope": 1, "row_count": 1, "updated_at": 1, "last_success_at": 1}))
        counts.append({"date": day, "bars": db.market_daily_bars.count_documents({"date": day}),
            "pool_rows": len(pool), "pool_unique_tickers": len(keys - {None, ""}),
            "price_limit_rows": db.daily_price_limits.count_documents({"trade_date": {"$in": forms}}),
            "sync_state": states})
    prior_pool = list(db.limit_up_pool.find({"trade_date": {"$in": [previous, (previous or "").replace("-", "")]}},
        {"_id": 0, "ticker": 1, "ts_code": 1})) if previous else []
    tickers = {str(row.get("ticker") or row.get("ts_code") or "").upper() for row in prior_pool} - {""}
    joined = {}
    for day in [previous, target]:
        joined[day] = {row["ticker"]: row.get("close") for row in db.market_daily_bars.find(
            {"date": day, "ticker": {"$in": sorted(tickers)}}, {"_id": 0, "ticker": 1, "close": 1})} if tickers else {}
    premiums = []
    for ticker in tickers:
        try:
            before, current = float(joined[previous][ticker]), float(joined[target][ticker])
            if before > 0 and math.isfinite(before) and math.isfinite(current):
                premiums.append(current / before - 1)
        except (KeyError, TypeError, ValueError):
            pass
    return {"status": "ok", "checked_at": __import__("datetime").datetime.now().astimezone().isoformat(),
        "snapshot": snapshot, "daily_coverage": counts,
        "prior_cohort": {"unique_tickers": len(tickers), "previous_close_count": len(joined.get(previous, {})),
            "current_close_count": len(joined.get(target, {})), "valid_premium_samples": len(premiums),
            "median": median(premiums) if premiums else None}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date")
    args = parser.parse_args()
    config = load_config()
    if not config.mongo_uri:
        raise SystemExit("MongoDB is not configured.")
    try:
        with MongoClient(config.mongo_uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=15000) as client:
            print(json.dumps(audit(client[config.mongo_db], args.date), ensure_ascii=False, indent=2, default=str))
    except Exception as error:
        raise SystemExit(f"Read-only audit failed: {type(error).__name__}") from None
