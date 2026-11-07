"""One low-memory scheduler that launches heavy jobs as short-lived processes.

The previous desktop layout kept several Python runtimes alive all day.  This
process imports only APScheduler and the standard library; pandas, LightGBM,
Tushare, and market services are loaded inside child processes and released when
each run finishes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")

# ``topic_perspectives`` remains a compatibility alias for the status page's
# classification directory, never an instruction to run the retired ranker.
PIPELINE_DATASETS = ["stock_daily_bars", "index_daily_bars", "stock_basic", "moneyflow",
                     "unified_taxonomy", "event_theme_inputs", "topic_perspectives", "daily_review"]
RECONCILE_DATASETS = ["unified_taxonomy", "event_theme_inputs", "previous_limit_pool",
                      "topic_perspectives", "daily_review"]
PIPELINE_RELEASE_PATHS = (
    "jobs/cloud_scheduler.py", "jobs/scheduler_manifest_job.py",
    "jobs/data_pipeline_job.py", "jobs/data_pipeline_retry_job.py",
    "jobs/heavy_job_lease.py", "jobs/sector_precompute_job.py", "market/sector_service.py",
    "market/sector.py", "market/ma5_universe.py", "market/ma5_snapshot_store.py",
    "jobs/pipeline_dependencies.py", "jobs/pipeline_state.py",
    "jobs/pipeline_optional_sources.py", "jobs/data_pipeline_reconcile.py",
    "jobs/cffex_positions_sync_job.py", "data/cffex_positions.py",
    "jobs/limit_pool_sync_job.py", "review/status_refresh.py", "review/service.py",
    "review/board_data.py", "review/board_day_facts.py", "review/dated_pools.py",
    "review/strong_stock_data.py", "review/index_liquidity.py", "review/market_mood.py",
    "review/suspensions.py",
    "market/taxonomy.py", "market/industry_history.py", "jobs/theme_daily_job.py",
)


def _release_fingerprint(root=None):
    """Fingerprint both triggers and the dependency/retry code they execute."""
    root = root or Path(__file__).parent.parent
    digest = hashlib.sha256()
    for name in PIPELINE_RELEASE_PATHS:
        path = root / name
        digest.update(name.encode() + b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def scheduler_manifest(scheduler, *, data_only: bool, location: str, now=None):
    """Describe actual registered triggers, not a separate desired timetable."""
    now = now or datetime.now(MARKET_TIMEZONE)
    jobs = []
    for job in scheduler.get_jobs():
        identifier = job.id
        module, keys, label = "", [], identifier
        if identifier == "data_pipeline_reconcile":
            module, keys, label = "shilun.jobs.data_pipeline_retry_job", list(RECONCILE_DATASETS), "依赖核验与逐项重试"
        elif identifier == "data_pipeline_sector_precompute":
            module, keys, label = "shilun.jobs.sector_precompute_job", ["sector_trends", "ma5_selection"], "板块研究与全市场MA5队列"
        elif identifier.startswith(("tushare_", "daily_review_")):
            module, keys, label = "shilun.jobs.data_pipeline_job", list(PIPELINE_DATASETS), "日线与复盘流水线"
        elif identifier.startswith("price_limits_"):
            module, keys, label = "shilun.jobs.tushare_sync_job", ["price_limits"], "涨跌停价格"
        elif identifier.startswith("minute_bars_"):
            module, keys, label = "shilun.jobs.minute_bars_sync_job", ["minute_bars"], "候选与基准5分钟行情"
        elif identifier.startswith("cffex_positions_"):
            module, keys, label = "shilun.jobs.cffex_positions_sync_job", ["cffex_positions"], "股指期货席位"
        elif identifier.startswith("limit_pool_"):
            module, keys, label = "shilun.jobs.limit_pool_sync_job", ["limit_up_pool"], "原始涨停池"
        elif identifier == "scheduler_manifest_heartbeat":
            module, label = "shilun.jobs.scheduler_manifest_job", "调度运行配置心跳"
        fields = {field.name: str(field) for field in job.trigger.fields}
        jobs.append({"id": identifier, "label": label, "dataset_keys": keys,
                     "module": module, "schedule": str(job.trigger),
                     "hour": fields.get("hour"), "minute": fields.get("minute"),
                     "day_of_week": fields.get("day_of_week"),
                     "executor": job.executor,
                     "next_run_at": job.trigger.get_next_fire_time(None, now).isoformat()})
        notes = {}
        if "stock_basic" in keys:
            notes["stock_basic"] = "缺失时初始化基础名录，不保证每日强制刷新"
        if "topic_perspectives" in keys:
            notes["topic_perspectives"] = "兼容旧状态页键名：实际为统一分类目录，不运行旧题材排名派生任务"
            notes["unified_taxonomy"] = "先核验同日行业分类，再推进依赖分类的分析"
            notes["event_theme_inputs"] = "同日涨停原因事件标签及涨停、炸板归档，不以行业分类替代题材"
        if identifier == "data_pipeline_reconcile":
            notes["daily_review"] = "仅处理到期重试与输入变化，重用已就绪事实，不重跑全市场同步"
        if notes:
            jobs[-1]["dataset_notes"] = notes
    return {"dataset": "cloud_scheduler_manifest", "scope": "active", "status": "running",
            "timezone": str(MARKET_TIMEZONE), "location": location,
            "data_only": data_only, "updated_at": now.isoformat(),
            "release_fingerprint": _release_fingerprint(), "jobs": jobs}


def _publish_manifest(scheduler, *, data_only: bool, location: str):
    payload = scheduler_manifest(scheduler, data_only=data_only, location=location)
    return _run("scheduler_manifest", "shilun.jobs.scheduler_manifest_job", ("--payload", json.dumps(payload)))


def _run(identifier: str, module: str, arguments: Sequence[str] = ()) -> int:
    command = [sys.executable, "-m", module, *arguments]
    started = datetime.now(MARKET_TIMEZONE)
    print(f"cloud_scheduler start job={identifier} at={started.isoformat(timespec='seconds')}", flush=True)
    try:
        result = subprocess.run(command, check=False, timeout=60 * 45,
            env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
                 'MKL_NUM_THREADS': '1', 'MALLOC_ARENA_MAX': '2'})
    except subprocess.TimeoutExpired:
        print(f"cloud_scheduler timeout job={identifier}", flush=True)
        return 124
    elapsed = (datetime.now(MARKET_TIMEZONE) - started).total_seconds()
    print(
        f"cloud_scheduler finish job={identifier} code={result.returncode} elapsed={elapsed:.1f}s",
        flush=True,
    )

    return result.returncode

def _sync_latest(identifier: str, *, previous_day: bool = False) -> None:
    target = datetime.now(MARKET_TIMEZONE)
    if previous_day:
        target -= timedelta(days=1)
    _run(
        identifier,
        "shilun.jobs.data_pipeline_job",
        ("--date", target.strftime("%Y-%m-%d")),
    )


def build_scheduler(*, data_only: bool = False, location: str = "未声明"):
    scheduler = BlockingScheduler(
        timezone=MARKET_TIMEZONE,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 900},
        # Data-only deployment serializes bulk writers; a slow sync cannot
        # overlap a review build or another full-market sync.
        executors={"default": {"type": "threadpool", "max_workers": 1 if data_only else 10},
                   # Independent small evidence writes must not wait behind a
                   # 45-minute full-market job or miss their own retry windows.
                   "evidence": {"type": "threadpool", "max_workers": 1},
                   "maintenance": {"type": "threadpool", "max_workers": 1}},
    )

    def cron(identifier: str, function, *, day_of_week: str | None = None, executor: str = "default", **time_parts: object) -> None:
        if data_only and not identifier.startswith(("tushare_", "price_limits_", "daily_review_", "minute_bars_", "cffex_positions_", "limit_pool_", "scheduler_manifest_", "data_pipeline_")):
            return
        trigger = CronTrigger(day_of_week=day_of_week, timezone=MARKET_TIMEZONE, **time_parts)
        scheduler.add_job(function, trigger, id=identifier, replace_existing=True, executor=executor)

    # Data watermarks and pre-market limits.
    cron("tushare_morning_0500", lambda: _sync_latest("tushare_morning_0500", previous_day=True), hour=5, minute=0)
    cron("tushare_morning_0700", lambda: _sync_latest("tushare_morning_0700", previous_day=True), hour=7, minute=0)
    cron(
        "price_limits_0845",
        lambda: _run("price_limits_0845", "shilun.jobs.tushare_sync_job", ("--price-limits-only",)),
        day_of_week="mon-fri",
        hour=8,
        minute=45,
    )

    # A single short-lived intraday process replaces the always-resident heavy
    # schedulers.  The job itself checks trading hours and safely skips otherwise.
    cron(
        "trading_intraday_every_minute",
        lambda: _run("trading_intraday", "shilun.jobs.trading_intraday_sync_job"),
        day_of_week="mon-fri",
        hour="9-11,13-15",
        minute="*",
        second=12,
    )

    for hour in (17, 19):
        cron(
            f"tushare_close_{hour:02d}15",
            lambda run_hour=hour: _sync_latest(f"tushare_close_{run_hour:02d}15"),
            day_of_week="mon-fri",
            hour=hour,
            minute=15,
        )
    cron(
        "holding_check_0715",
        lambda: _run("holding_check_0715", "shilun.jobs.holding_daily_check_job"),
        day_of_week="mon-fri",
        hour=7,
        minute=15,
    )
    cron(
        "holding_check_1630",
        lambda: _run("holding_check_1630", "shilun.jobs.holding_daily_check_job"),
        day_of_week="mon-fri",
        hour=16,
        minute=30,
    )
    cron(
        "minute_bars_1515",
        lambda: _run("minute_bars_1515", "shilun.jobs.minute_bars_sync_job"),
        day_of_week="mon-fri",
        hour=15,
        minute=15,
    )
    for hour in (17, 19, 23):
        identifier = f"cffex_positions_{hour:02d}40"
        cron(identifier,
             lambda job_id=identifier: _run(job_id, "shilun.jobs.cffex_positions_sync_job"),
             day_of_week="mon-fri", hour=hour, minute=40, executor="evidence")
    for hour in (15, 17, 19):
        identifier = f"limit_pool_{hour:02d}35"
        cron(identifier,
             lambda job_id=identifier: _run(job_id, "shilun.jobs.limit_pool_sync_job"),
             day_of_week="mon-fri", hour=hour, minute=35, executor="evidence")
    cron("scheduler_manifest_heartbeat",
         lambda: _publish_manifest(scheduler, data_only=data_only, location=location),
         minute="*/5", second=10, executor="maintenance")
    # This worker reads persisted dated tasks; an empty due queue is a no-op.
    # It shares the bulk executor so recovery cannot race a full materialization.
    # Lightweight source writers and the scheduler heartbeat stay independent.
    cron("data_pipeline_reconcile",
         lambda: _run("data_pipeline_reconcile", "shilun.jobs.data_pipeline_retry_job"),
         minute="*/5", second=30)
    # Queue producers can be desktops; only this cloud scheduler consumes work.
    # Sharing the bulk executor plus Mongo lease serializes across processes and
    # hosts without holding a heavy Python runtime while the queue is empty.
    cron("data_pipeline_sector_precompute",
         lambda: _run("data_pipeline_sector_precompute", "shilun.jobs.sector_precompute_job"),
         minute="*", second=45)
    for identifier, hour, minute in (("structure_close", 15, 40), ("structure_night", 23, 5)):
        cron(
            identifier,
            lambda job_id=identifier: _run(job_id, "shilun.jobs.structure_sample_job"),
            day_of_week="mon-fri",
            hour=hour,
            minute=minute,
        )

    # The review runs after the two data-sync windows.  It is deterministic and
    # upserts one versioned snapshot, so a retry never creates duplicate reviews.
    for identifier, hour, minute in (("daily_review_retry", 23, 20),):
        cron(
            identifier,
            lambda job_id=identifier: _sync_latest(job_id),
            day_of_week="mon-fri",
            hour=hour,
            minute=minute,
        )

    return scheduler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-only", action="store_true", help="Only core data and daily reviews; serialize bulk jobs")
    parser.add_argument("--run-once", action="store_true", help="Run the complete dated data pipeline once")
    parser.add_argument("--date", default=None)
    parser.add_argument("--location", default="未声明", help="Declared deployment location for the status page")
    args = parser.parse_args()
    if args.run_once:
        raise SystemExit(_run("manual_pipeline", "shilun.jobs.data_pipeline_job", ("--date", args.date) if args.date else ()))
    Path("runtime").mkdir(exist_ok=True)
    lock = open("runtime/cloud-data-scheduler.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("A cloud scheduler already owns this runtime")
    scheduler = build_scheduler(data_only=args.data_only, location=args.location)
    _publish_manifest(scheduler, data_only=args.data_only, location=args.location)
    print(
        "Shilun cloud scheduler started: one lightweight parent, short-lived market jobs, "
        f"Asia/Shanghai timezone; data_only={args.data_only}.",
        flush=True,
    )
    scheduler.start()


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["main"]
