"""持仓每日检测任务。

收盘后先跑一次，次日早晨在 Tushare 补数后再幂等补偿一次。
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.market import DEFAULT_BENCHMARK_TICKER
from shilun.market.holding_events import daily_check_holdings, load_holding_check_context


def _date_text(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and "-" in text:
        return text[:10]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def resolve_check_date(store: Any, requested_date: str | None = None) -> str:
    """按交易日历选择检测日；早晨默认检查上一交易日。"""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if requested_date:
        target = date.fromisoformat(_date_text(requested_date))
    else:
        target = now.date() if now.hour >= 12 else now.date() - timedelta(days=1)
    start = target - timedelta(days=14)
    try:
        rows = store.raw_market.find_trade_calendar(
            start_date=start.strftime("%Y%m%d"),
            end_date=target.strftime("%Y%m%d"),
            exchange="SSE",
        )
        open_dates = [
            _date_text(row.get("cal_date") or row.get("date"))
            for row in rows
            if str(row.get("is_open") or "0") in {"1", "1.0", "True", "true"}
        ]
        if open_dates:
            return max(open_dates)
    except Exception:  # noqa: BLE001
        pass
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target.isoformat()


class HoldingDailyCheckJob:
    def __init__(self, store: Any) -> None:
        self.store = store

    def run(
        self,
        requested_date: str | None = None,
        *,
        benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
        trend_lookback_days: int = 60,
    ) -> dict[str, Any]:
        analysis_date = resolve_check_date(self.store, requested_date)
        benchmark = benchmark_ticker.upper()
        market_gate, sector_states = load_holding_check_context(
            self.store,
            analysis_date,
            benchmark_ticker=benchmark,
            trend_lookback_days=trend_lookback_days,
        )
        result = daily_check_holdings(
            self.store,
            analysis_date,
            market_gate=market_gate,
            sector_states=sector_states,
        )
        payload = {
            "dataset": "holding_events",
            "scope": analysis_date.replace("-", ""),
            "analysis_date": analysis_date,
            "benchmark_ticker": benchmark,
            "market_permission": str((market_gate or {}).get("permission") or "unknown"),
            "sector_context_count": len(sector_states),
            "checked": result["checked"],
            "failed": result["failed"],
            "state_counts": result["state_counts"],
            "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        }
        upsert_state = getattr(self.store, "upsert_sync_state", None)
        if callable(upsert_state):
            upsert_state(payload)
        return {**result, **payload}


def _run_once(requested_date: str | None, benchmark_ticker: str, trend_lookback_days: int) -> dict[str, Any]:
    config = load_config()
    if not config.mongo_uri:
        raise RuntimeError("SHILUN_MONGO_URI not configured")
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    try:
        return HoldingDailyCheckJob(store).run(
            requested_date,
            benchmark_ticker=benchmark_ticker,
            trend_lookback_days=trend_lookback_days,
        )
    finally:
        store.close()


def _print_result(result: dict[str, Any], prefix: str = "holding_daily_check") -> None:
    print(
        f"{prefix} date={result['analysis_date']} checked={result['checked']} "
        f"failed={result['failed']} states={result['state_counts']}",
        flush=True,
    )
    if result.get("errors"):
        print(f"  errors={result['errors']}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Shilun holding daily check")
    parser.add_argument("--date", default="", help="YYYY-MM-DD or YYYYMMDD; defaults to latest relevant trade date")
    parser.add_argument("--benchmark-ticker", default=DEFAULT_BENCHMARK_TICKER)
    parser.add_argument("--trend-lookback-days", type=int, default=60)
    args = parser.parse_args()
    result = _run_once(args.date or None, args.benchmark_ticker, args.trend_lookback_days)
    _print_result(result)


def scheduler_main() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    def run_scheduled() -> None:
        try:
            _print_result(_run_once(None, DEFAULT_BENCHMARK_TICKER, 60), prefix="scheduled_holding_check")
        except Exception as error:  # noqa: BLE001
            print(f"scheduled_holding_check failed: {error}", flush=True)

    for job_id, hour, minute in (
        ("holding_check_close", 16, 30),
        ("holding_check_morning_retry", 7, 15),
    ):
        scheduler.add_job(
            run_scheduled,
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, second=0, timezone="Asia/Shanghai"),
            id=job_id,
            replace_existing=True,
        )
    print("Holding daily-check scheduler started. Runs at 16:30 and 07:15 Asia/Shanghai on weekdays.", flush=True)
    scheduler.start()


if __name__ == "__main__":
    main()

