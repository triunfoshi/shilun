"""Dated review evidence: no network on read, no stale-day substitution."""

from __future__ import annotations

from math import isfinite
from datetime import date as calendar_date, timedelta
from typing import Any

from shilun.review.engine import build_ladder


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError):
        return None


def at_limit(close: Any, limit: Any) -> bool | None:
    price, upper = finite_number(close), finite_number(limit)
    if price is None or upper is None or upper <= 0:
        return None
    # A half-tick tolerance handles decimal storage, not a percent-change proxy.
    return abs(price - upper) < 0.005


def load_review_sessions(store, target_date: str, start_date: str | None = None) -> list[str]:
    """Walk a verified calendar suffix, never skip missing whole-market days."""
    end = calendar_date.fromisoformat(target_date)
    start = calendar_date.fromisoformat(start_date) if start_date else end - timedelta(days=40)
    rows = store.find_trade_calendar(start_date=start.isoformat(), end_date=target_date)
    def _is_open(value: Any) -> str:
        # Mongo keeps Tushare's flag as an integer; test doubles and old rows
        # may carry the same value as a string or boolean.
        if value is True or value == 1 or str(value).strip() == "1":
            return "1"
        if value is False or value == 0 or str(value).strip() == "0":
            return "0"
        return ""

    calendar = {str(row.get("cal_date", "")).replace("-", ""): _is_open(row.get("is_open")) for row in rows}
    sessions, cursor = [], end
    while cursor >= start:
        state = calendar.get(cursor.strftime("%Y%m%d"))
        if state not in ("0", "1"):
            break
        if state == "1":
            sessions.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return sorted(sessions)


def reconstruct_ladder(dates, bars_by_date, limits_by_date, basic) -> dict[str, Any]:
    """Count only fully verified consecutive closes, excluding any history gap.

    This daily-only fallback never invents intraday seal times/broken-board counts
    and must not be merged into the upstream limit-up pool.
    """
    dates = sorted(set(dates))
    target = dates[-1] if dates else ""
    verified, unresolved, covered = [], [], 0
    for ticker, bar in bars_by_date.get(target, {}).items():
        state = at_limit(bar.get("close"), limits_by_date.get(target, {}).get(ticker))
        if state is not None:
            covered += 1
        if state is not True:
            continue
        streak, complete = 0, False
        for date in reversed(dates):
            day_bar = bars_by_date.get(date, {}).get(ticker, {})
            hit = at_limit(day_bar.get("close"), limits_by_date.get(date, {}).get(ticker))
            if hit is None:
                break
            if not hit:
                complete = True
                break
            streak += 1
        stock = basic.get(ticker, {})
        if not complete:
            unresolved.append({"ticker": ticker, "name": stock.get("name") or ticker,
                               "minimum_board": streak, "reason": "历史日线、涨停价或交易日历有缺口"})
            continue
        verified.append({"ticker": ticker, "name": stock.get("name") or ticker,
                         "sector_name": stock.get("sector") or "未分类",
                         "consecutive_count": streak, "trade_date": target})
    total = len(bars_by_date.get(target, {}))
    unknown = len(unresolved)
    result = build_ladder(verified)
    result.update({
        "status": "partial" if unknown or covered < total else "ok" if verified else "empty",
        "source": "daily_close_and_price_limit", "source_label": "本地日线与涨停价核验",
        "source_date": target, "verified_count": len(verified),
        "unresolved_count": unknown, "price_limit_coverage": covered,
        "unresolved_stocks": unresolved,
        "note": f"涨停池缺失时逐日核对收盘价与当日涨停价。涨停价覆盖 {covered}/{total} 只；"
                f"{unknown} 只因历史缺口无法确认板数，未计入。无封板时间与炸板次数。",
    })
    if not covered:
        result["status"] = "unavailable"
    return result


def load_futures_history(store, target_date: str, dates: list[str]) -> dict[str, Any]:
    """Use the same per-product payload as market research; keep missing days."""
    window = sorted({date for date in dates if date <= target_date})[-7:]
    documents = store.collection("cffex_positions_daily").find(
        {"analysis_date": {"$in": window}}, {"_id": 0, "analysis_date": 1, "payload": 1})
    payloads = {row["analysis_date"]: row.get("payload") or {} for row in documents}
    history = []
    for date in window:
        payload = payloads.get(date, {})
        products = []
        for row in payload.get("products") or []:
            if row.get("data_date") not in (None, "", date):
                continue
            products.append(dict(row))
        history.append({"analysis_date": date, "products": products,
                        "status": payload.get("status", "not_synced")})
    available = [day for day in history if any(row.get("status") == "ok" for row in day["products"])]
    current = next((day for day in history if day["analysis_date"] == target_date), {})
    return {**current, "target_date": target_date, "history": history, "history_window": 7,
            "history_available_count": len(available),
            "source_date": available[-1]["analysis_date"] if available else None}


def sync_review_limit_pools(store, dates: list[str], *, job=None) -> list[dict[str, Any]]:
    """Explicit materialization only: fill the latest two missing daily pools."""
    from shilun.data.providers.eastmoney_provider import EastmoneyClient, EastmoneyConfig
    from shilun.jobs.limit_pool_sync_job import LimitPoolSyncJob

    results = []
    for date in sorted(set(dates))[-2:]:
        compact = date.replace("-", "")
        if store.collection("limit_up_pool").find_one({"trade_date": {"$in": [date, compact]}}, {"_id": 0, "ticker": 1}):
            results.append({"date": date, "status": "cached"})
            continue
        try:
            if job is None:
                job = LimitPoolSyncJob(store, EastmoneyClient(EastmoneyConfig(timeout=10, retries=0)))
            count = job.sync_limit_up_pool(compact)
            results.append({"date": date, "status": "ok" if count else "empty", "row_count": count})
        except Exception as error:
            # No raw upstream response/URL in the snapshot or logs.
            results.append({"date": date, "status": "unavailable", "error_type": type(error).__name__})
    return results
