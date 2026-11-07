"""Dated 09:25 opening-auction amounts; never substitute 09:30 minute bars."""
from datetime import date, datetime
import re
from zoneinfo import ZoneInfo

from shilun.review.sources import finite_number

COLLECTION = "review_opening_auctions"
SOURCE = "tushare_stk_auction"
VERSION = 1


def nonnegative_amount(value):
    value = None if isinstance(value, bool) else finite_number(value)
    return value if value is not None and value >= 0 else None


def normalize_opening_auction(frame, day):
    from shilun.review.dated_pools import stock_code
    target = date.fromisoformat(day).strftime("%Y%m%d")
    if frame.empty or len(frame) >= 8000:
        raise ValueError("竞价数据为空或可能被截断")
    amounts, seen = {}, set()
    for row in frame.to_dict("records"):
        ticker = str(row.get("ts_code") or "")
        code = ticker.split(".")[0]
        if (str(row.get("trade_date") or "").replace("-", "") != target
                or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", ticker) or code in seen):
            raise ValueError("竞价数据日期、代码或唯一性未通过核验")
        seen.add(code)
        # The configured gateway also returns ETFs, convertibles and B shares.
        # Only A shares belong to this CNY first-board archive (B shares may be USD/HKD).
        if not code.startswith(("00", "30", "60", "68", "43", "83", "87", "92")):
            continue
        if ticker != stock_code(code):
            raise ValueError("竞价股票交易所未通过核验")
        amount = nonnegative_amount(row.get("amount"))
        if amount is not None:
            amounts[code] = amount
    if not amounts:
        raise ValueError("竞价成交金额尚未返回")
    return {"date": day, "amounts": amounts, "source": SOURCE, "schema": VERSION,
            "unit": "CNY", "auction_time": "09:25",
            "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}


def load_opening_auction(store, day):
    return store.collection(COLLECTION).find_one(
        {"date": day, "source": SOURCE, "schema": VERSION}, {"_id": 0}) or {}


def sync_first_board_auction(store, day, *, client=None):
    from shilun.review.board_data import BoardResearchData
    from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
    report = {"date": day, "dataset": "first_board_auction"}
    pool = BoardResearchData(store).pools([day]).get(day)
    if pool is None:
        return {**report, "status": "unavailable", "reason": "missing_up_pool"}
    required = {r["code"] for r in pool["stocks"] if r.get("boards") == 1}
    cached = load_opening_auction(store, day)
    amounts = {code: value for code, value in cached.get("amounts", {}).items()
               if nonnegative_amount(value) is not None}
    if required <= amounts.keys():
        return {**report, "status": "ok", "cached": True, "count": len(required)}
    try:
        client = client or TushareDailyClient(TushareConfig.from_env())
        frame = client._call_api("stk_auction", trade_date=day.replace("-", ""),
                                 fields="ts_code,trade_date,amount", limit=8000)
        document = normalize_opening_auction(frame, day)
        document["amounts"] = {**amounts, **document["amounts"]}
        store.collection(COLLECTION).update_one({"_id": day}, {"$set": document}, upsert=True)
        count = len(required & document["amounts"].keys())
        return {**report, "status": "ok" if count == len(required) else "unavailable",
                "count": count, "expected": len(required)}
    except Exception as error:
        # Keep any previous verified amounts. Provider errors may contain secrets.
        return {**report, "status": "unavailable", "error_type": type(error).__name__}
