"""KlineShare 报价时效采样，用于交易时段校准 45 秒安全阈值。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


COLLECTION_NAME = "trading_quote_latency_samples"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed


def record_quote_latency_samples(
    collection: Any,
    quotes: list[dict[str, Any]],
    *,
    request_latency_ms: float,
    threshold_seconds: int = 45,
    calibration_eligible: bool = True,
    decision_at: datetime | None = None,
) -> int:
    count = 0
    for quote in quotes:
        ticker = str(quote.get("ticker") or "").upper()
        quote_at = _parse(quote.get("quote_at") or quote.get("update_at"))
        fetched_at = _parse(quote.get("fetched_at"))
        if not ticker or quote_at is None or fetched_at is None:
            continue
        decision_time = decision_at or fetched_at
        fetch_age = max(0.0, (fetched_at - quote_at).total_seconds())
        decision_age = max(0.0, (decision_time - quote_at).total_seconds())
        observed_at = decision_time.isoformat(timespec="seconds")
        raw_id = f"{ticker}|{quote_at.isoformat()}|{fetched_at.isoformat()}|{observed_at}"
        sample_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
        document = {
            "sample_id": sample_id,
            "ticker": ticker,
            "source": quote.get("source"),
            "trade_status": quote.get("trade_status"),
            "quote_at": quote_at.isoformat(timespec="seconds"),
            "fetched_at": fetched_at.isoformat(timespec="seconds"),
            "observed_at": observed_at,
            "quote_age_at_fetch_seconds": round(fetch_age, 3),
            "quote_age_at_decision_seconds": round(decision_age, 3),
            "quote_age_seconds": round(decision_age, 3),
            "request_latency_ms": round(max(0.0, request_latency_ms), 3),
            "threshold_seconds": threshold_seconds,
            "over_threshold": decision_age > threshold_seconds,
            "calibration_eligible": calibration_eligible,
        }
        collection.update_one(
            {"sample_id": sample_id},
            {"$setOnInsert": document},
            upsert=True,
        )
        count += 1
    return count


def list_quote_latency_samples(
    collection: Any,
    *,
    ticker: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    query = {"ticker": ticker.upper()} if ticker else {}
    return list(
        collection.find(query, {"_id": 0})
        .sort("observed_at", -1)
        .limit(limit)
    )


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.999999)))
    return round(ordered[index], 3)


def summarize_quote_latency(samples: list[dict[str, Any]]) -> dict[str, Any]:
    eligible_samples = [item for item in samples if item.get("calibration_eligible", True)]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sample in eligible_samples:
        ticker = str(sample.get("ticker") or "")
        if ticker:
            grouped.setdefault(ticker, []).append(sample)
    tickers: list[dict[str, Any]] = []
    for ticker, rows in sorted(grouped.items()):
        ages = [float(row.get("quote_age_seconds") or 0) for row in rows]
        request_latencies = [float(row.get("request_latency_ms") or 0) for row in rows]
        over_count = sum(bool(row.get("over_threshold")) for row in rows)
        tickers.append({
            "ticker": ticker,
            "sample_count": len(rows),
            "quote_age_p50_seconds": _percentile(ages, 0.50),
            "quote_age_p95_seconds": _percentile(ages, 0.95),
            "request_p95_ms": _percentile(request_latencies, 0.95),
            "over_threshold_count": over_count,
            "over_threshold_rate": round(over_count / len(rows), 4),
            "latest_observed_at": max(str(row.get("observed_at") or "") for row in rows),
        })
    return {
        "sample_count": len(eligible_samples),
        "excluded_sample_count": len(samples) - len(eligible_samples),
        "ticker_count": len(tickers),
        "tickers": tickers,
        "calibration_ready": bool(tickers) and all(item["sample_count"] >= 15 for item in tickers),
        "calibration_rule": "每个标的至少 15 个真实交易时段样本后，才评估是否调整 45 秒阈值",
    }
