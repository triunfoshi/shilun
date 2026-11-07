"""Shared dated stock-index-futures snapshots used by refresh and scheduled sync.

The storage shape remains cffex_positions_daily.analysis_date/payload. This
module deliberately has no dependency on the HTTP API or full review pipeline.
"""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any


def store_snapshot(store, target_date: str, payload: dict[str, Any]) -> bool:
    """Upsert one day; an incomplete retry must not replace an audited snapshot.

    Return whether this attempt was stored. The conditional update and unique
    analysis_date index also protect a complete concurrent manual refresh.
    """
    from pymongo.errors import DuplicateKeyError

    collection = store.collection("cffex_positions_daily")
    query: dict[str, Any] = {"analysis_date": target_date}
    if payload.get("status") != "ok":
        cached = collection.find_one(query) or {}
        if (cached.get("payload") or {}).get("status") == "ok":
            return False
        query["payload.status"] = {"$ne": "ok"}
    try:
        result = collection.update_one(query, {"$set": {
            "analysis_date": target_date,
            "payload": payload,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }}, upsert=True)
    except DuplicateKeyError:
        if payload.get("status") == "ok":
            raise
        return False
    return result is None or bool(result.matched_count or result.upserted_id)


def assess_snapshot(payload: dict[str, Any], target_date: str) -> dict[str, Any]:
    """Validate the four displayed contracts without merging expiry months.

    Some upstream payloads label date-matched but inconsistent seats as ok;
    scheduled receipts must verify both the date and the original audit facts.
    """
    from shilun.data.providers.cffex_positions_provider import PRODUCTS

    grouped = {product: [] for product in PRODUCTS}
    for row in payload.get("products") or []:
        if isinstance(row, dict) and row.get("product") in grouped:
            grouped[row["product"]].append(row)
    required = ("top20_long", "top20_short", "top20_long_change", "top20_short_change",
                "citic_long", "citic_short", "citic_long_change", "citic_short_change")
    valid, failed = [], {}
    for product, rows in grouped.items():
        reason = None
        if len(rows) != 1:
            reason = "missing_or_duplicate_product"
        else:
            row = rows[0]
            contract = str(row.get("contract") or "")
            if payload.get("target_date") != target_date or row.get("data_date") != target_date:
                reason = "wrong_data_date"
            elif row.get("status") != "ok":
                reason = "source_unavailable"
            elif not (contract.startswith(product) and len(contract) == 6 and contract[2:].isdigit()):
                reason = "invalid_contract"
            elif row.get("source_consistency") != "matched" or row.get("source_mismatch_fields"):
                reason = "seat_crosscheck_failed"
            elif row.get("ths_consistency") == "mismatch":
                reason = "third_source_mismatch"
            elif any(not isinstance(row.get(key), (int, float)) or isinstance(row.get(key), bool)
                     or not math.isfinite(row[key]) for key in required):
                reason = "missing_position_fields"
        if reason:
            failed[product] = reason
        else:
            valid.append(product)
    return {"expected_count": len(PRODUCTS), "successful_count": len(valid),
            "successful_products": valid, "failed_products": failed}


def normalize_snapshot(payload: dict[str, Any], target_date: str) -> dict[str, Any]:
    """One validation owner for scheduled sync and explicit manual refresh."""
    assessment = assess_snapshot(payload, target_date)
    complete = assessment["successful_count"] == assessment["expected_count"]
    result = {**payload, "status": "ok" if complete else "partial", "sync_validation": assessment}
    if not complete:
        result["products"] = [
            {**row, "status": "unverified", "sync_validation_error": assessment["failed_products"][row["product"]]}
            if row.get("product") in assessment["failed_products"] else dict(row)
            for row in payload.get("products") or [] if isinstance(row, dict)
        ]
        result["institutional_summary"] = {
            "available": False, "risk_score": None,
            "behavior": "期指席位核验未完成", "conclusion": "四品种未全部通过同日及席位核验，不生成合计结论。",
        }
    return result


def history_dates(store, target_date: str, *, limit: int = 7) -> list[str]:
    """Use completed SSE sessions for the fixed seven-day institution window."""

    dates: list[str] = []
    try:
        cursor = (
            store.collection("market_daily_bars")
            .find(
                {"ticker": "000001.SH", "date": {"$lte": target_date}},
                {"_id": 0, "date": 1},
            )
            .sort("date", -1)
            .limit(limit)
        )
        dates = [str(item.get("date") or "")[:10] for item in cursor if item.get("date")]
    except Exception:  # noqa: BLE001 - minimal stores may not support cursor chaining
        dates = []
    if target_date not in dates:
        dates.insert(0, target_date)
    return list(dict.fromkeys(dates))[:limit]


def history_payload(
    payloads: dict[str, dict[str, Any]],
    dates: list[str],
) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for analysis_date in sorted(dates):
        payload = payloads.get(analysis_date)
        if not payload:
            continue
        history.append({
            "analysis_date": analysis_date,
            "status": payload.get("status"),
            "products": payload.get("products") or [],
            "institutional_summary": payload.get("institutional_summary") or {},
        })
    return history


def load_snapshot(store, target_date: str, *, refresh: bool) -> dict[str, Any]:
    """Read the audited CFFEX snapshot and a fixed seven-session history window."""

    collection = store.collection("cffex_positions_daily")
    requested_dates = history_dates(store, target_date)
    cached_documents: list[dict[str, Any]] = []
    try:
        cached_documents = list(collection.find(
            {"analysis_date": {"$in": requested_dates}},
            {"_id": 0, "analysis_date": 1, "payload": 1, "updated_at": 1},
        ))
    except Exception:  # noqa: BLE001 - fake/minimal stores may not expose this collection
        cached_documents = []
    cached_by_date = {
        str(item.get("analysis_date") or ""): item
        for item in cached_documents
        if item.get("analysis_date") and item.get("payload")
    }
    payloads = {
        analysis_date: dict(item["payload"])
        for analysis_date, item in cached_by_date.items()
    }

    refresh_errors: dict[str, str] = {}
    if refresh:
        from shilun.data.providers.cffex_positions_provider import fetch_cffex_positions

        # The explicit refresh always refreshes the selected day and fills any
        # missing sessions in the seven-day trend. Historical cached sessions
        # remain immutable unless they are missing, avoiding unnecessary load.
        fetch_dates = [
            analysis_date for analysis_date in requested_dates
            if analysis_date == target_date or analysis_date not in payloads or payloads[analysis_date].get("status") != "ok"
        ]
        for analysis_date in fetch_dates:
            try:
                payload = normalize_snapshot(fetch_cffex_positions(target_date=analysis_date), analysis_date)
                if payload.get('status') != 'ok':
                    refresh_errors[analysis_date] = '席位来源缺失、日期不符或核验未通过'
                    if payloads.get(analysis_date, {}).get('status') == 'ok':
                        continue
                if store_snapshot(store, analysis_date, payload):
                    payloads[analysis_date] = payload
                else:
                    # A complete concurrent refresh can win the partial-upsert
                    # guard. Return the stored winner, never an unstored attempt.
                    stored = collection.find_one({"analysis_date": analysis_date}) or {}
                    if stored.get("payload"):
                        payloads[analysis_date] = dict(stored["payload"])
                    refresh_errors[analysis_date] = "本次核验未通过，保留已完整归档的快照"
            except Exception as error:  # noqa: BLE001 - one date must not erase other audited dates
                refresh_errors[analysis_date] = str(error)[:400]

    history = history_payload(payloads, requested_dates)
    current = payloads.get(target_date)
    history_meta = {
        "history": history,
        "history_window": 7,
        "history_available_count": len(history),
        "history_complete": len(history) >= len(requested_dates),
        "history_dates_requested": sorted(requested_dates),
    }
    if current:
        cached = cached_by_date.get(target_date) or {}
        return {
            **current,
            **history_meta,
            "_from_cache": not refresh or target_date in refresh_errors,
            "_cached_at": cached.get("updated_at"),
            **({"refresh_errors": refresh_errors} if refresh_errors else {}),
        }
    if not refresh:
        return {
            "status": "not_synced",
            "target_date": target_date,
            "products": [],
            **history_meta,
            "institutional_summary": {
                "available": False,
                "risk_score": None,
                "behavior": "期指席位待同步",
                "conclusion": "点击“强制重算”后读取过去7个交易日席位；未读取前不填零值。",
            },
        }
    return {
        "status": "error",
        "target_date": target_date,
        "products": [],
        **history_meta,
        "error": refresh_errors.get(target_date, "目标日期席位读取失败"),
        "refresh_errors": refresh_errors,
        "institutional_summary": {
            "available": False,
            "risk_score": None,
            "behavior": "期指席位读取失败",
            "conclusion": "目标日期席位读取失败，机构模块不计入评分；历史趋势仍保留已核验日期。",
        },
    }
