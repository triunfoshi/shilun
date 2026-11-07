"""Read-only storage coverage and verified scheduler metadata for the sync page.

Every metadata query is bounded. Date spans are observations, never a claim that
all dates or all securities between the endpoints are present. The local cron
configuration is deliberately separate from the cloud scheduler's heartbeat.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from zoneinfo import ZoneInfo

from pymongo.errors import ExecutionTimeout, OperationFailure

TZ = ZoneInfo("Asia/Shanghai")
QUERY_BUDGET_MS = 350
HEARTBEAT_MAX_AGE = timedelta(minutes=20)


def dataset_usage(key: str) -> dict[str, Any]:
    """Describe audited source consumers, not request traffic or data readiness.

    A paused producer does not remove consumers, and a successful producer does
    not prove that every cached or frozen consumer has already read the new data.
    """
    contracts = {
        "stock_daily_bars": ("integrated", "已接入", ("指数与板块复盘", "个股研究", "候选与持仓分析"),
                             "按所选交易日读取日线；已归档的研究和复盘保留各自生成时的数据。"),
        "index_daily_bars": ("integrated", "已接入", ("指数复盘", "相对强弱比较", "候选基准判断"),
                             "当前所选指数作为市场及个股比较基准，覆盖范围与所选证券分别核验。"),
        "stock_basic": ("integrated", "已接入", ("证券名称与基础标注", "股票筛选"),
                        "读取当前股票名录；题材与细分行业归属另由统一分类目录提供。"),
        "moneyflow": ("integrated", "已接入", ("板块资金分析", "个股资金观察", "模式研究"),
                      "资金数据按交易日供研究读取；已生成的结果不会仅因补数自动改写。"),
        "limit_up_pool": ("integrated", "已接入", ("指数情绪旁证", "连板天梯", "首板池补充来源"),
                          "本表已有读取路径。复盘涨停/炸板池另存一套归档；首板模块优先使用其完整归档，缺失时才读取本表的已核验记录。"),
        "minute_bars": ("integrated", "已接入", ("板块分时共振", "盘中跟踪", "交易回放与模式研究"),
                        "仅在相应日期和证券已有分钟窗口时参与计算；缺口可能使依赖该窗口的功能降级。"),
        "topic_perspectives": ("integrated", "已接入", ("题材与行业归属", "板块及首板研究", "候选筛选与复盘"),
                               "统一目录和日期成分供多个功能共享，自动更新状态不决定这些读取路径是否存在。"),
        "daily_review": ("integrated", "已接入", ("指数复盘", "短线情绪复盘", "决策研究引用"),
                         "读取已有复盘快照；期指持仓等窗口会在读取时补入，其他冻结事实需重新生成归档后更新。"),
        "breakout_events": ("optional", "可选接入", ("MA5候选质量", "部分MA5持仓检查", "B结构样本"),
                            "这是MA5相关路径的可选增强。无事件时使用已有日线特征降级；交易日志和新三战法不直接依赖此表。"),
        "cffex_positions": ("integrated", "已接入", ("指数复盘机构旁证", "期指持仓趋势", "市场姿态研究"),
                            "指数复盘每次读取时会从已有期指快照更新机构持仓窗口；其他已归档复盘事实保持原版本。"),
    }
    status, label, consumers, note = contracts.get(
        key, ("unknown", "接入待确认", (), "尚未核对该数据的读取路径，不能据此认定没有功能使用。"))
    return {"status": status, "label": label, "consumers": list(consumers), "note": note,
            "basis": "source_contract"}


def date_text(value: Any) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    text = str(value or "")[:10]
    try:
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _timestamp(value: Any) -> datetime | None:
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=TZ) if stamp.tzinfo is None else stamp.astimezone(TZ)
    except (TypeError, ValueError):
        return None


def _job_keys(identifier: str) -> list[str]:
    if identifier == 'data_pipeline_reconcile':
        return ['unified_taxonomy', 'event_theme_inputs', 'topic_perspectives', 'previous_limit_pool', 'daily_review']
    if identifier.startswith(("tushare_", "daily_review_")):
        return ["stock_daily_bars", "index_daily_bars", "stock_basic", "moneyflow", "topic_perspectives", "daily_review"]
    if identifier.startswith("minute_bars_"):
        return ["minute_bars"]
    if identifier.startswith("cffex_positions_"):
        return ["cffex_positions"]
    if identifier.startswith(("limit_pool_", "limit_up_pool_")):
        return ["limit_up_pool"]
    return []


def configured_jobs(now: datetime) -> list[dict[str, Any]]:
    """Instantiate triggers only; never start a scheduler, subprocess or IO."""
    from shilun.jobs.cloud_scheduler import build_scheduler
    scheduler = build_scheduler(data_only=True)
    jobs = []
    for job in scheduler.get_jobs():
        fields = {field.name: str(field) for field in job.trigger.fields}
        hour, minute = fields.get("hour", ""), fields.get("minute", "")
        days = fields.get("day_of_week", "*")
        prefix = "每天" if days == "*" else "周一至周五" if days == "mon-fri" else days
        when = f"{int(hour):02d}:{int(minute):02d}" if hour.isdigit() and minute.isdigit() else f"{hour}:{minute}"
        fire = job.trigger.get_next_fire_time(None, now)
        jobs.append({"id": job.id, "label": job.id, "schedule": f"{prefix} {when}",
                     "hour": hour, "minute": minute, "day_of_week": days,
                     "dataset_keys": _job_keys(job.id), "next_run_at": fire.isoformat() if fire else None})
    return jobs


def scheduler_status(store, now: datetime) -> dict[str, Any]:
    manifest = store.find_sync_state(dataset="cloud_scheduler_manifest", scope="active") or {}
    published = manifest.get("jobs") or []
    stamp = _timestamp(manifest.get("updated_at"))
    fresh = bool(stamp and -timedelta(minutes=2) <= now - stamp <= HEARTBEAT_MAX_AGE)
    location = str(manifest.get("location") or "未声明")
    known_cloud = (location.upper() == "ECS" and manifest.get("status") in {"running", "active"}
                   and isinstance(published, list) and bool(published) and stamp is not None
                   and stamp <= now + timedelta(minutes=2))
    verified = known_cloud and fresh
    allowed = ("id", "label", "schedule", "hour", "minute", "day_of_week", "dataset_keys", "next_run_at")
    last_known_jobs = [{key: value for key, value in item.items() if key in allowed}
                       for item in published if isinstance(item, dict) and isinstance(item.get("id"), str)
                       and item["id"]] if known_cloud else []
    # Malformed or non-ECS manifests cannot establish either current or prior
    # deployment. Old cloud jobs remain available only as explicitly historical.
    known_cloud = known_cloud and bool(last_known_jobs)
    verified = verified and bool(last_known_jobs)
    status = "enabled" if verified else "stale" if known_cloud else "unknown"
    labels = {"enabled": "云端自动更新已确认", "stale": "云端自动更新状态待确认", "unknown": "云端自动更新状态待确认"}
    if verified:
        note = "已收到云端调度器的有效心跳；数据可用性和功能接入分别核对。"
    elif known_cloud:
        note = "云端最近心跳已超过20分钟，仅保留上次启用清单；这不表示已有数据失效或功能未接入。"
    else:
        note = "尚未确认当前云端自动更新状态；已有归档、定时配置和功能接入分别列示。"
    # Active jobs are only those published by the running scheduler. Local jobs
    # absent from this list remain configured, even if they share its timezone.
    return {"status": status, "label": labels[status],
            "timezone": str(manifest.get("timezone") or "Asia/Shanghai"),
            "location": location,
            "verified_at": manifest.get("updated_at"), "heartbeat_at": manifest.get("updated_at"),
            "checked_at": now.isoformat(),
            "last_verified_at": manifest.get("updated_at") if known_cloud else None,
            "jobs": last_known_jobs if verified else [], "last_known_jobs": last_known_jobs,
            "configured_jobs": configured_jobs(now),
            "note": note}


def storage_summary(store, collection: str, field: str | None, *, query=None,
                    record_count=None, date_count=None, count_dates=False) -> dict[str, Any]:
    base = {"status": "unknown", "earliest_date": None, "latest_date": None,
            "record_count": record_count, "date_count": date_count,
            "note": "日期跨度不代表逐日连续或全市场完整覆盖。"}
    if field is None:
        return {**base, "status": "available" if record_count else "empty", "note": "主数据当前快照，不保存逐日版本。"}
    col = store.collection(collection)
    condition = dict(query or {})
    condition[field] = {"$exists": True, "$ne": None}
    try:
        first = col.find_one(condition, {"_id": 0, field: 1}, sort=[(field, 1)], max_time_ms=QUERY_BUDGET_MS)
        last = col.find_one(condition, {"_id": 0, field: 1}, sort=[(field, -1)], max_time_ms=QUERY_BUDGET_MS)
    except (ExecutionTimeout, OperationFailure):
        return {**base, "note": "跨度查询超时或缺少可用索引，未将未知视为无数据。"}
    if not first and not last:
        return {**base, "status": "empty", "record_count": 0, "date_count": 0}
    base.update(status="available", earliest_date=date_text((first or {}).get(field)), latest_date=date_text((last or {}).get(field)))
    if base["earliest_date"] is None or base["latest_date"] is None:
        base.update(status="unknown", note="已存记录的日期格式无法核验。")
    if record_count is None:
        try:
            base["record_count"] = col.count_documents(condition, maxTimeMS=QUERY_BUDGET_MS)
        except (ExecutionTimeout, OperationFailure):
            base["note"] += " 总记录数未在查询预算内完成，保留未知。"
    if count_dates and date_count is None:
        try:
            values = list(col.aggregate([{"$match": condition}, {"$group": {"_id": f"${field}"}}, {"$count": "count"}], maxTimeMS=QUERY_BUDGET_MS))
            base["date_count"] = values[0]["count"] if values else 0
        except (ExecutionTimeout, OperationFailure):
            base["note"] += " 已存日期数暂未核验。"
    return base


def _receipt(store, dataset: str, scope: str) -> dict[str, Any]:
    raw = store.find_sync_state(dataset=dataset, scope=scope) or {}
    fields = ("status", "updated_at", "started_at", "finished_at", "requested_date", "analysis_date", "target_date", "stage",
              "row_count", "candidate_count", "missing_count", "failed_count", "expected_count", "successful_count", "failed_products", "error_type", "error_message", "next_retry_at", "pending_stages")
    result = {key: raw[key] for key in fields if key in raw}
    if result:
        result.update(dataset=dataset, scope=scope)
        result["fetched_count"] = raw.get("row_count", raw.get("successful_count"))
        result["detail"] = "最近一次任务回执；抓取数不是整日累计覆盖数。"
    return result


def _schedule_for(key: str, scheduler: dict[str, Any]) -> dict[str, Any]:
    local = [job for job in scheduler["configured_jobs"] if key in (job.get("dataset_keys") or _job_keys(job["id"]))]
    cloud = [job for job in scheduler["jobs"] if key in (job.get("dataset_keys") or _job_keys(job.get("id", "")))]
    known = [job for job in scheduler.get("last_known_jobs", []) if key in (job.get("dataset_keys") or _job_keys(job.get("id", "")))]
    verified = scheduler["status"] == "enabled" and bool(cloud)
    stale = scheduler["status"] == "stale" and bool(known)
    status = "enabled" if verified else "stale" if stale else "configured" if local else "manual"
    labels = {"enabled": "云端定时", "stale": "曾启用 · 当前待确认", "configured": "已配置 · 待确认", "manual": "手动 / 按需"}
    notes = {
        "enabled": "",
        "stale": "上次云端清单包含此任务，最近心跳已过期；不代表已有数据失效或功能未接入。",
        "configured": "已有定时配置，尚未确认云端已启用。",
        "manual": "当前未配置定时任务；可由手动同步或相关功能按需生成。",
    }
    note = notes[status]
    if status == "configured" and scheduler["status"] == "enabled":
        note = "本地已有定时配置，当前云端任务清单中尚未启用。"
    if key == "stock_basic":
        note += " 股票基本信息随日线首次初始化，已有记录后并非每日刷新。"
    return {"status": status, "label": labels[status], "timezone": scheduler["timezone"], "location": "ECS" if local or cloud or known else None,
            "jobs": cloud if verified else known if stale else local,
            "jobs_origin": "cloud_current" if verified else "cloud_last_known" if stale else "local_config" if local else "none",
            "last_known_jobs": known,
            "checked_at": scheduler.get("checked_at"),
            "verified_at": scheduler.get("last_verified_at") if cloud or known else None,
            "last_verified_at": scheduler.get("last_verified_at") if cloud or known else None, "note": note}


def _has_target_receipt(receipt, target_date):
    dates = [date_text(receipt.get(key)) for key in ("requested_date", "target_date", "analysis_date")]
    return target_date in dates or str(receipt.get("scope") or "").startswith(target_date.replace("-", "")) or receipt.get("scope") == target_date


def enrich_datasets(store, datasets, target_date, *, benchmark_ticker, benchmark_count, stock_basic_count, now=None):
    now = now or datetime.now(TZ)
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    scheduler = scheduler_status(store, now)
    from shilun.market.part1 import BENCHMARK_INDEX_OPTIONS
    from shilun.market.taxonomy import VERSION as TAXONOMY_VERSION
    from shilun.review.service import REVIEW_VERSION
    compact = target_date.replace("-", "")
    benchmark_tickers = [row["ticker"] for row in BENCHMARK_INDEX_OPTIONS]
    specs = {
        "stock_daily_bars": ("market_daily_bars", "date", {"ticker": {"$nin": benchmark_tickers}}, "全市场个股日线，不含基准指数", "日线", "tushare_daily", "SSE"),
        "index_daily_bars": ("market_daily_bars", "date", {"ticker": benchmark_ticker}, f"当前所选基准 {benchmark_ticker}", "日线", "tushare_daily", "SSE"),
        "stock_basic": ("stock_basic", None, {}, "当前股票基本信息快照", "当前快照", "tushare_stock_basic", "all"),
        "moneyflow": ("moneyflow", "trade_date", {}, "已归档个股资金流", "日线", "tushare_daily", "SSE"),
        "limit_up_pool": ("limit_up_pool", "trade_date", {}, "原始涨停股票池；与复盘涨停/炸板池分开存储", "每日股票记录", "eastmoney_limit_up_pool_runs", compact),
        "minute_bars": ("minute_bars_daily", "trade_date", {"interval": {"$in": ["5", 5]}}, "候选缓存中的证券及双基准，不含全市场或全部持仓", "5分钟证券日窗口", "minute_bars_daily", f"{compact}_5min"),
        "topic_perspectives": ("market_taxonomy_snapshots", "date", {"version": TAXONOMY_VERSION, "status": "ready"}, "东财题材和行业当前版本的日期成分；经Tushare兼容接口获取", "每日版本快照", "daily_data_pipeline", "latest"),
        "daily_review": ("daily_review_snapshots", "analysis_date", {"engine_version": REVIEW_VERSION}, "当前引擎版本的收盘复盘", "每日版本快照", "daily_data_pipeline", "latest"),
        "breakout_events": ("breakout_events", "breakout_date", {}, "MA5事件基线及后续T+1至T+5；不要求每日都有新事件", "事件及后续日线", "breakout_events", "latest"),
        "cffex_positions": ("cffex_positions_daily", "analysis_date", {}, "IF/IH/IC/IM四个当前展示合约的机构席位，不是全月份或期货K线", "每日席位快照", "cffex_positions_daily", target_date),
    }
    def enrich(item):
        key = item["key"]
        item["usage"] = dataset_usage(key)
        if key not in specs:
            return
        collection, field, query, scope, granularity, receipt_dataset, receipt_scope = specs[key]
        counts = {"record_count": benchmark_count, "date_count": benchmark_count} if key == "index_daily_bars" else {"record_count": stock_basic_count} if key == "stock_basic" else {}
        item["storage"] = storage_summary(store, collection, field, query=query, count_dates=key in {"daily_review", "topic_perspectives", "cffex_positions"}, **counts)
        item["coverage"] = {"scope": scope, "granularity": granularity}
        item["schedule"] = _schedule_for(key, scheduler)
        item["last_run"] = _receipt(store, receipt_dataset, receipt_scope)
        if key == "limit_up_pool" and not item["last_run"]:
            # Canonical receipts qualify stored raw pools for downstream use.
            # Scheduled attempts are separate so a later failure cannot revoke
            # those stored facts. Retain compatibility with manual-only days.
            item["last_run"] = _receipt(store, "eastmoney_limit_up_pool", compact)
        if key in {"stock_daily_bars", "index_daily_bars", "moneyflow"}:
            pipeline = _receipt(store, "daily_data_pipeline", "latest")
            if not item["last_run"] or (_has_target_receipt(pipeline, target_date) and pipeline.get("stage") == "daily_sync" and pipeline.get("status") in {"failed", "running"}):
                item["last_run"] = pipeline
        item["status"] = "ready" if item["ok"] else "missing"
        receipt = item["last_run"]
        relevant = _has_target_receipt(receipt, target_date)
        if key == "stock_daily_bars" and 0 < item["value"] < 500:
            item.update(status="partial", ok=False, status_note="当日个股截面不足500条，尚不能视为全市场日线就绪。")
        if key == "minute_bars" and receipt:
            if receipt.get("status") in {"partial", "partial_error"}:
                item.update(status="partial" if item["value"] else "failed", ok=False)
                item["detail"] += f"；本次失败 {receipt.get('failed_count', '未知')} 个目标"
            elif receipt.get("status") == "running":
                item.update(status="running", ok=False)
            elif receipt.get("status") == "failed":
                item.update(status="failed", ok=False)
        if key == "cffex_positions" and 0 < item["value"] < 4:
            item["status"] = "partial"
        if key == "cffex_positions" and not item["ok"] and relevant and receipt.get("status") in {"partial", "failed", "running"}:
            item.update(status=receipt["status"], ok=False)
        if relevant and not item["ok"] and receipt.get("status") == "running":
            item["status"] = "running"
        if relevant and not item["ok"] and receipt.get("status") == "failed":
            item["status"] = "failed"
        cutoff = (15, 15) if key == "minute_bars" else (17, 40) if key == "cffex_positions" else (17, 15)
        if not item["ok"] and item["status"] == "missing" and target_date == now.date().isoformat() and now.weekday() < 5 and item["schedule"]["status"] == "enabled":
            if (now.hour, now.minute) < cutoff:
                item.update(status="scheduled", status_note="尚未到当日首次同步时间。")
        if key == "breakout_events":
            item["status"] = "unknown" if item["value"] else "missing"
            item["status_note"] = "没有成功扫描/回填水位，不能只凭存在事件判断任务健康。"
    # Independent indexed reads share the Mongo pool. Bounded concurrency avoids
    # serial SSH round trips without increasing the per-query scan budget.
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="sync-metadata") as executor:
        list(executor.map(enrich, datasets))
    return scheduler


def cffex_dataset(store, target_date: str) -> dict[str, Any]:
    """Read an existing snapshot only; never refresh provider data on GET."""
    document = store.collection("cffex_positions_daily").find_one({"analysis_date": target_date}, {"_id": 0, "payload": 1}) or {}
    payload = document.get("payload") or {}
    from shilun.data.cffex_positions import assess_snapshot
    assessment = assess_snapshot(payload, target_date)
    count = assessment["successful_count"]
    ready = payload.get("status") == "ok" and count == assessment["expected_count"]
    return {"key": "cffex_positions", "label": "股指期货机构席位", "tier": "enhanced", "source": "新浪席位；IF/IH/IC经同花顺交叉核验",
            "value": count, "ok": ready, "detail": f"{target_date} 已核验 {count}/4 个品种",
            "impact_on_miss": "期指机构态度证据不足；不使用旧值或零值补齐。"}
