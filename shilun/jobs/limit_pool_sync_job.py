"""Dated Eastmoney limit-up pool ingestion and independent close-time retries.

The raw limit_up_pool owner is separate from review_board_pools. Scheduled runs
never rebuild an existing review or substitute another day's pool.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from shilun.data.providers.eastmoney_provider import EastmoneyClient, EastmoneyFetchError


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
DATASET = "eastmoney_limit_up_pool"
RUN_DATASET = "eastmoney_limit_up_pool_runs"


def _dates(value: str) -> tuple[str, str]:
    text = str(value)
    compact = text.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError("Invalid trade date")
    day = datetime.strptime(compact, "%Y%m%d").date()
    if text not in {day.isoformat(), compact}:
        raise ValueError("Invalid trade date")
    return day.isoformat(), compact


class LimitPoolSyncJob:
    def __init__(self, mongo_store, client=None):
        self.store = mongo_store
        self.client = client or EastmoneyClient()

    def sync_limit_up_pool(self, trade_date: str) -> int:
        """同步指定交易日的涨停池到 Mongo。"""
        _, trade_date = _dates(trade_date)
        # Only prepare this job's two collections, not every application index.
        # The unique key also protects retries and concurrent daily ingestion.
        self.store.collection("limit_up_pool").create_index(
            [("ticker", 1), ("trade_date", 1)], name="uniq_limit_up_pool_ticker_date", unique=True)
        self.store.collection("sync_state").create_index(
            [("dataset", 1), ("scope", 1)], name="uniq_sync_state_scope", unique=True)
        df = self.client.fetch_zt_pool(trade_date)
        if df.empty:
            # fetch_zt_pool validates the upstream qdate before returning even
            # an empty DataFrame. A verified empty retry still conflicts with
            # any nonempty archived pool and must not claim the archive empty.
            existing = self.store.collection("limit_up_pool").find_one(
                {"trade_date": trade_date}, {"_id": 1})
            if existing:
                raise EastmoneyFetchError("zt_pool", ValueError("空池与已有非空归档冲突"))
            self._record_state(DATASET, trade_date, 0, "empty_confirmed")
            return 0
        payloads = df.to_dict("records")
        seen = set()
        for row in payloads:
            code = str(row.get("ticker") or "")
            day = str(row.get("trade_date") or "").replace("-", "")
            if (day != trade_date or len(code) != 6 or not code.isdigit()
                    or code == "000000" or code in seen):
                raise EastmoneyFetchError("zt_pool", ValueError("涨停池行日期、代码或唯一性不符"))
            row.update(ticker=code, trade_date=trade_date)
            seen.add(code)
        count = self.store._upsert_payloads("limit_up_pool", payloads, ("ticker", "trade_date"))
        self._record_state(DATASET, trade_date, count, "success")
        return count

    def _record_state(self, dataset: str, scope: str, row_count: int, status: str) -> None:
        self.store.upsert_sync_state(
            {
                "dataset": dataset,
                "scope": scope,
                "status": status,
                "row_count": row_count,
                "error_type": None,
                "updated_at": datetime.now(MARKET_TIMEZONE).isoformat(timespec="seconds"),
            }
        )


def run(store, target_date: str, *, client=None, now=None) -> dict:
    """Sync one dated raw pool, with safe receipts for scheduler monitoring."""
    from shilun.jobs.cffex_positions_sync_job import _is_open

    day, compact = _dates(target_date)
    now = now or datetime.now(MARKET_TIMEZONE)
    state = {
        # A failed attempt must not cancel the successful dated raw-data
        # watermark consumed by first-board fallback. Keep task and data
        # receipts separate in the existing sync_state collection.
        "dataset": RUN_DATASET, "scope": compact, "requested_date": day,
        "status": "running", "started_at": now.isoformat(), "finished_at": None,
        "row_count": 0, "error_type": None, "source": "eastmoney_dated_limit_up_pool",
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
        count = LimitPoolSyncJob(store, client=client).sync_limit_up_pool(compact)
        save(status="success" if count else "empty_confirmed", row_count=count,
             finished_at=datetime.now(MARKET_TIMEZONE).isoformat())
    except Exception as error:
        # The provider exception can carry private request details. Keep only
        # its class, and leave previously archived rows intact on any failure.
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
    store = None
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db,
                                   initialize_indexes=False, socket_timeout_ms=30000)
        state = run(store, args.date or datetime.now(MARKET_TIMEZONE).date().isoformat())
        print(f"limit_pool_sync date={state['requested_date']} status={state['status']} "
              f"rows={state['row_count']} error_type={state.get('error_type') or 'none'}", flush=True)
        return 0 if state["status"] in {"success", "empty_confirmed", "skipped_closed"} else 1
    except Exception as error:
        print(f"limit_pool_sync status=failed error_type={type(error).__name__}", flush=True)
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
