"""Independent dated sync of IF/IH/IC/IM institution positions.

17:40, 19:40 and 23:40 are retry windows, not a source publication guarantee.
The job does not rebuild reviews or require the daily-data pipeline to succeed.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from shilun.data.cffex_positions import normalize_snapshot, store_snapshot


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
DATASET = "cffex_positions_daily"


def _is_open(store, day: str) -> tuple[bool | None, str]:
    # Cached calendar is evidence; a missing daily bar is not proof of closure.
    try:
        rows = store.find_trade_calendar(start_date=day, end_date=day, exchange="SSE")
        for row in rows:
            if str(row.get("cal_date") or "").replace("-", "") == day.replace("-", ""):
                value = str(row.get("is_open"))
                if value in {"0", "0.0", "False"}:
                    return False, "cached_calendar"
                if value in {"1", "1.0", "True"}:
                    return True, "cached_calendar"
    except (AttributeError, TypeError):
        pass
    if date.fromisoformat(day).weekday() >= 5:
        return False, "weekend"
    return None, "calendar_unknown"


def run(store, target_date: str, *, fetcher=None, now: datetime | None = None) -> dict[str, Any]:
    day = date.fromisoformat(target_date).isoformat()
    now = now or datetime.now(MARKET_TIMEZONE)
    state: dict[str, Any] = {
        "dataset": DATASET, "scope": day, "requested_date": day,
        "status": "running", "started_at": now.isoformat(), "row_count": 0,
        "expected_count": 4, "successful_count": 0, "failed_products": {},
        "source": "sina_rankings_and_citic_with_ths_crosscheck",
    }

    def save(**values):
        state.update(values, updated_at=datetime.now(MARKET_TIMEZONE).isoformat())
        store.upsert_sync_state(dict(state))

    try:
        save()
        is_open, calendar_source = _is_open(store, day)
        state["calendar_source"] = calendar_source
        if is_open is False:
            save(status="skipped_closed", finished_at=datetime.now(MARKET_TIMEZONE).isoformat())
            return dict(state)
        if fetcher is None:
            from shilun.data.providers.cffex_positions_provider import fetch_cffex_positions
            fetcher = fetch_cffex_positions
        payload = normalize_snapshot(fetcher(target_date=day), day)
        assessment = payload["sync_validation"]
        complete = assessment["successful_count"] == assessment["expected_count"]
        stored = store_snapshot(store, day, payload)
        save(status="success" if complete else "partial", **assessment,
             row_count=int(stored), stored=stored, preserved_complete_snapshot=not stored,
             finished_at=datetime.now(MARKET_TIMEZONE).isoformat())
    except Exception as error:
        # Never print provider URLs or credential-bearing exception messages.
        save(status="failed", error_type=type(error).__name__,
             finished_at=datetime.now(MARKET_TIMEZONE).isoformat())
    return dict(state)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    args = parser.parse_args(argv)
    from shilun.common.config import load_config
    from shilun.common.db import MongoSnapshotStore
    config = load_config()
    if not config.mongo_uri:
        raise RuntimeError("SHILUN_MONGO_URI not set")
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=30000)
    try:
        state = run(store, args.date or datetime.now(MARKET_TIMEZONE).date().isoformat())
        print(f"cffex_positions_sync date={state['requested_date']} status={state['status']} "
              f"products={state['successful_count']}/{state['expected_count']} "
              f"failed_products={','.join(state['failed_products'])}", flush=True)
        return 0 if state["status"] in {"success", "skipped_closed"} else 2 if state["status"] == "partial" else 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
