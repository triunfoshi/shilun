"""分时 K 线（`minute_bars_daily`）存取层。

用途（v6 分时共振）：
    sector 预计算时，对每个 top_sectors 里的候选票拉当日 5min K 线，
    存到 Mongo `minute_bars_daily` 集合。后续 sector 评估时秒查表就好，
    不再重复请求 分钟源（避免限流）。

    集合命名带 _daily 后缀，语义是"按日期存储的分钟 K 线"，跟 market_daily_bars
    的存储粒度形成对比（前者一天一 bar，本集合一天一批 bar）。

Schema:
    {
        ticker: "688256.SH",              # Tushare 格式
        trade_date: "20260709",           # YYYYMMDD
        interval: "5",                    # 5 / 15 / 30
        bars: [
            {datetime: iso str, open, high, low, close, volume, amount},
            ...
        ],
        source: "eastmoney",
        fetched_at: iso str,
    }

主键（`(ticker, trade_date, interval)`）由 ensure_indexes 定义为唯一索引。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

COLLECTION_NAME = "minute_bars_daily"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def upsert_minute_bars(
    collection: Any,
    *,
    ticker: str,
    trade_date: str,
    interval: str,
    bars: list[dict[str, Any]],
    source: str = "eastmoney",
) -> None:
    """写入或更新单只票某日的分钟 K 线。已存在则覆盖 bars 和 fetched_at。"""
    key = {"ticker": str(ticker), "trade_date": str(trade_date), "interval": str(interval)}
    doc = {
        **key,
        "bars": _normalize_bars(bars),
        "source": source,
        "fetched_at": _now_iso(),
    }
    collection.update_one(key, {"$set": doc}, upsert=True)


def _normalize_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 pandas.to_dict('records') 或 分钟源 原生列裁剪成统一 schema，去掉 NaN。"""
    normalized: list[dict[str, Any]] = []
    for bar in bars or []:
        dt = bar.get("datetime")
        if hasattr(dt, "isoformat"):
            dt = dt.isoformat()
        record = {
            "datetime": str(dt) if dt is not None else None,
            "open": _num(bar.get("open")),
            "high": _num(bar.get("high")),
            "low": _num(bar.get("low")),
            "close": _num(bar.get("close")),
            "volume": _num(bar.get("volume")),
            "amount": _num(bar.get("amount")),
        }
        if record["datetime"] and record["close"] is not None:
            normalized.append(record)
    return normalized


def _num(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    import math
    if math.isnan(n) or math.isinf(n):
        return None
    return n


def get_minute_bars(
    collection: Any,
    ticker: str,
    trade_date: str,
    interval: str = "5",
) -> list[dict[str, Any]] | None:
    """按 (ticker, trade_date, interval) 查单只票某日的分钟 K 线。未找到返回 None。"""
    doc = collection.find_one(
        {"ticker": ticker, "trade_date": str(trade_date), "interval": str(interval)},
        {"_id": 0, "bars": 1},
    )
    if not doc:
        return None
    return list(doc.get("bars") or [])


def find_missing_tickers(
    collection: Any,
    tickers: Iterable[str],
    trade_date: str,
    interval: str = "5",
) -> list[str]:
    """从传入 ticker 列表里挑出**尚未落库**的那些，供预计算增量拉取。"""
    target_list = [str(t) for t in tickers]
    if not target_list:
        return []
    existing_cursor = collection.find(
        {"ticker": {"$in": target_list}, "trade_date": str(trade_date), "interval": str(interval)},
        {"_id": 0, "ticker": 1},
    )
    existing = {doc.get("ticker") for doc in existing_cursor}
    return [t for t in target_list if t not in existing]


def bulk_load_lookup(
    collection: Any,
    tickers: Iterable[str],
    trade_date: str,
    interval: str = "5",
) -> dict[str, list[dict[str, Any]]]:
    """一次 Mongo 查询批量拉多只票的分钟 K 线，返回 {ticker: bars}。

    sector 评估时用：一次查完所有候选票的分钟数据，避免逐票查表。
    """
    target_list = [str(t) for t in tickers]
    if not target_list:
        return {}
    cursor = collection.find(
        {"ticker": {"$in": target_list}, "trade_date": str(trade_date), "interval": str(interval)},
        {"_id": 0, "ticker": 1, "bars": 1},
    )
    lookup: dict[str, list[dict[str, Any]]] = {}
    for doc in cursor:
        lookup[str(doc.get("ticker"))] = list(doc.get("bars") or [])
    return lookup
