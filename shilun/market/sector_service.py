"""Shared sector/MA5 application service; no dependency on HTTP handlers.

All publishers preserve daily shortlist -> minute repair -> strict ranking.
The ECS worker owns execution; page reads only consume published archives.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from shilun.market.part1 import PART1_ENGINE_VERSION
from shilun.market.sector import SectorTrendRequest, evaluate_sector_trends

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


def input_revision(store, day: str, benchmark: str, *, top_n: int = 8,
                   trend_window_days: int = 60, include_minutes: bool = True) -> str:
    """Small dated receipts, not a second transfer of full-market history.

    Content revisions come from the dependency ledger. Legacy inputs without
    receipts retain their sync watermarks; an explicit force request can always
    refresh them. Minute metadata is included because repairs affect ranking.
    """
    from hashlib import sha256
    import json
    from shilun.market.sector import SECTOR_ENGINE_VERSION
    from shilun.market.ma5_liquidity import MA5_SELECTION_RULE_VERSION
    from shilun.market.taxonomy import SNAPSHOTS, VERSION

    receipts = list(store.collection('data_pipeline_tasks').find(
        {'analysis_date': day, 'stage': {'$in': [
            'dependency:' + name for name in ('trade_calendar', 'daily_bars',
            'previous_daily_bars', 'daily_basic', 'moneyflow', 'core_history',
            'benchmark_bars', 'index_history', 'industry_taxonomy')]}},
        {'_id': 0, 'stage': 1, 'input_revision': 1, 'output_revision': 1}))
    watermarks = list(store.collection('sync_state').find(
        {'dataset': {'$in': ['tushare_daily', 'tushare_stock_basic']}},
        {'_id': 0, 'dataset': 1, 'scope': 1, 'updated_at': 1, 'last_trade_date': 1}))
    taxonomy = store.collection(SNAPSHOTS).find_one(
        {'_id': f'{VERSION}:{day}', 'status': 'ready'}, {'_id': 0, 'revision': 1})
    from shilun.market.minute_bars_store import COLLECTION_NAME
    minutes = list(store.collection(COLLECTION_NAME).find(
        {'trade_date': day.replace('-', ''), 'interval': '5'},
        {'_id': 0, 'ticker': 1, 'fetched_at': 1, 'source': 1})) if include_minutes else []
    events = list(store.collection('breakout_events').find(
        {'breakout_date': {'$gte': (datetime.fromisoformat(day)-timedelta(days=30)).date().isoformat(), '$lte': day}},
        {'_id': 0, 'ticker': 1, 'breakout_date': 1, 'status': 1, 'updated_at': 1}))
    value = dict(day=day, benchmark=benchmark, top_n=top_n, window=trend_window_days,
        engine=SECTOR_ENGINE_VERSION, ma5=MA5_SELECTION_RULE_VERSION,
        taxonomy=taxonomy, receipts=receipts, watermarks=watermarks,
        minutes=minutes, events=events, gate=_load_market_gate(store, day, benchmark))
    for name in ('receipts', 'watermarks', 'minutes', 'events'):
        value[name].sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def published_result_matches(store, task: dict) -> bool:
    """A successful receipt is reusable only while its result is still published."""
    expected = ((task.get('result') or {}).get('generation') or {}).get('id')
    if not expected:
        return False
    key = _sector_cache_key(task['analysis_date'], task['benchmark_ticker'],
                            task.get('trend_window_days', 60), top_n=task.get('top_n', 8))
    row = store.collection('sector_trends_cache').find_one(key, {'_id': 0, 'payload.generation.id': 1})
    return bool(row and ((row.get('payload') or {}).get('generation') or {}).get('id') == expected)


def prepare_dependencies(store, day: str, benchmark: str) -> dict:
    """Repair exact-day missing feeds only; never launch a history-wide sync."""
    from shilun.review.index_sync import sync_index_history
    from shilun.market.taxonomy import load_taxonomy, sync_taxonomy
    from shilun.jobs.pipeline_dependencies import prepare_dependency
    from shilun.market.sector import SectorTrendRequest

    start = SectorTrendRequest(day).start_date
    calendar = store.find_trade_calendar(start_date=start, end_date=day)
    days = sorted({str(r.get('cal_date', '')).replace('-', '') for r in calendar
                   if str(r.get('is_open')) in ('1', 'True')})
    if not days or days[-1] != day.replace('-', '') or len(days) < 24:
        raise ValueError(f'{day} 的完整24交易日日历未就绪；请补齐目标日历史，未使用其他日期替代。')
    repaired = []
    for key, collection, field, value in (
        ('daily_bars', 'market_daily_bars', 'date', day),
        ('daily_basic', 'daily_basic', 'trade_date', day.replace('-', '')),
        ('moneyflow', 'moneyflow', 'trade_date', day.replace('-', '')),
    ):
        query = {field: {'$in': [day, day.replace('-', '')]}}
        if key == 'daily_bars':
            query['ticker'] = {'$regex': r'^\d{6}\.(SH|SZ|BJ)$'}
        if not store.collection(collection).find_one(query, {'_id': 1}):
            report = prepare_dependency(store, day, key)
            repaired.append({'key': key, 'status': report.get('status')})
            if not store.collection(collection).find_one(query, {'_id': 1}):
                raise ValueError(f'{day} {key} 同日数据未就绪，补数后仍有缺口。')
    index = sync_index_history(store, day, (benchmark,))
    if any(r['status'] != 'ok' for r in index):
        raise ValueError(f'{day} 的基准指数尚未就绪，自动补数未完成。')
    taxonomy = load_taxonomy(store, day) or sync_taxonomy(store, day)
    if not taxonomy or taxonomy.get('date') != day:
        raise ValueError(f'{day} 的完整同日分类尚未发布。')
    return {'index_sync': index, 'taxonomy_revision': taxonomy.get('revision'), 'repaired': repaired}


def run_precompute(store, config, day: str, benchmark: str = '000001.SH', *,
                   top_n: int = 8, before_publish=None, lookback_days: int = 40,
                   trend_lookback_days: int = 60, include_daily_leaders: bool = False,
                   include_all_sectors: bool = True, publish: bool = True) -> dict:
    """Compute and publish the sector and MA5 lists as one validated generation."""
    from time import perf_counter
    from uuid import uuid4
    started = perf_counter()
    dependencies = prepare_dependencies(store, day, benchmark)
    dependency_seconds = perf_counter() - started
    # Daily inputs must not move while this job repairs its own minute inputs.
    daily_revision = input_revision(store, day, benchmark, top_n=top_n,
        trend_window_days=trend_lookback_days, include_minutes=False)
    revision_before = input_revision(store, day, benchmark, top_n=top_n,
                                     trend_window_days=trend_lookback_days)
    from shilun.market.ma5_snapshot_store import load_scan_cache, save_scan_cache
    scan_entry = load_scan_cache(store, day)
    facts_cache = {'daily_computation_cache': {'ma5_universe_scan': scan_entry}} if scan_entry else {}
    result = _compute_sector_trends_with_candidate_minutes(
        store=store, config=config, target_date=day, bm_ticker=benchmark,
        lookback_days=lookback_days, trend_lookback_days=trend_lookback_days,
        top_n=top_n, include_daily_leaders=include_daily_leaders,
        include_all_sectors=include_all_sectors, facts_cache=facts_cache)
    revision_after = input_revision(store, day, benchmark, top_n=top_n,
                                    trend_window_days=trend_lookback_days)
    if input_revision(store, day, benchmark, top_n=top_n,
            trend_window_days=trend_lookback_days, include_minutes=False) != daily_revision:
        raise ValueError('计算期间日线或分类输入发生变化，等待下次重试；上一完整结果已保留。')
    if result.get('analysis_date') != day or result.get('minute_sync', {}).get('ranking_gate') != 'strict':
        raise ValueError('计算结果日期或分钟资格门校验失败，上一完整结果已保留。')
    if not include_daily_leaders:
        previous = (_load_sector_cache(store, day, benchmark, trend_lookback_days, top_n=top_n) or {}).get('payload') or {}
        if previous.get('daily_leaders'):
            result['daily_leaders'] = previous['daily_leaders']
            result['leader_summary'] = previous.get('leader_summary') or {}
    elapsed = perf_counter() - started
    generation = {'id': uuid4().hex, 'execution_location': 'ecs',
                  'input_revision': revision_after, 'submitted_input_revision': revision_before, 'top_n': top_n,
                  'published_at': datetime.now(timezone.utc).isoformat()}
    result['generation'] = generation
    if publish:
        if before_publish is None:
            raise RuntimeError('Publication requires an owned heavy-task lease')
        before_publish()
        computed_scan = facts_cache.get('daily_computation_cache', {}).get('ma5_universe_scan')
        if computed_scan:
            snapshot = save_scan_cache(store, day, computed_scan, before_publish)
            snapshot['reused'] = bool(scan_entry and scan_entry['signature'] == computed_scan['signature'])
            generation['ma5_snapshot'] = snapshot
            before_publish()
        _save_sector_cache(store, day, benchmark, trend_lookback_days, result, elapsed, top_n=top_n)
    return {'status': 'ok', 'analysis_date': day, 'benchmark_ticker': benchmark,
            'trend_lookback_days': trend_lookback_days, 'compute_time_seconds': round(elapsed, 2),
            'minute_sync': result['minute_sync'], 'index_sync': dependencies['index_sync'],
            'published_input_revision': revision_after, 'generation': generation,
            'compute_timings': {'dependencies_seconds': round(dependency_seconds, 3),
                                **result.get('compute_timings', {})},
            'top_sectors': [{'name': s['sector_name'], 'score': s['scores'].get('sector_score')}
                            for s in result.get('top_sectors', [])],
            'message': f'云端预计算完成，耗时 {elapsed:.1f}s；板块与MA5共享同一份已发布结果。'}

def _load_market_gate(store, target_date: str, bm_ticker: str) -> dict[str, Any] | None:
    """从 PART1 缓存拿 market_gate；没有缓存时返回 None（PART3 会按 no-gate 默认处理）。

    不做 fallback 计算，避免 PART2/PART3 隐式触发 PART1 全量重跑。
    调用方应先手动跑一次 /api/v1/market/permission 建缓存。
    """
    try:
        cache_col = store.collection("market_part1_cache")
        cached = cache_col.find_one(
            {"analysis_date": target_date, "benchmark_ticker": bm_ticker},
            {"_id": 0, "payload": 1, "engine_version": 1},
        )
        if cached and cached.get("payload") and cached.get("engine_version") == PART1_ENGINE_VERSION:
            return cached["payload"].get("market_gate")
    except Exception:  # noqa: BLE001
        return None
    return None

def _compute_sector_trends_full(
    store,
    target_date: str,
    bm_ticker: str,
    lookback_days: int,
    trend_lookback_days: int,
    top_n: int,
    include_daily_leaders: bool = False,
    include_all_sectors: bool = False,
    require_minute_data_for_ranking: bool = False,
    facts_cache: dict | None = None,
) -> dict[str, Any]:
    """完整跑一次 evaluate_sector_trends。这是慢路径，只在预计算里调用。"""
    import pandas as pd

    request = SectorTrendRequest(
        analysis_date=target_date,
        benchmark_ticker=bm_ticker,
        lookback_days=lookback_days,
        trend_lookback_days=trend_lookback_days,
        top_n=top_n,
    )

    facts = facts_cache if facts_cache is not None else {}
    if 'market_records' not in facts:
        benchmark_records = store.raw_market.find_daily_bars(
            start_date=request.start_date, end_date=target_date, tickers=[bm_ticker],
        )
        market_records = store.raw_market.find_daily_bars(
            start_date=request.start_date, end_date=target_date,
        )
        from shilun.market.taxonomy import classify_stocks
        stock_basic = pd.DataFrame(classify_stocks(store, target_date, store.raw_market.find_stock_basic()))
        daily_basic = pd.DataFrame(store.raw_market.find_daily_basic(trade_date=target_date))
        moneyflow_start = (
            datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=max(18, trend_lookback_days + 5))
        ).strftime("%Y-%m-%d")
        moneyflow = pd.DataFrame(
            store.raw_market.find_moneyflow(start_date=moneyflow_start, end_date=target_date)
        )
        gate = _load_market_gate(store, target_date, bm_ticker)
        # Job 6：把 breakout_events 集合里 target_date 之前 30 天内所有事件按 ticker
        # 一次拉进内存，`build_candidates` 里对每只候选票直接查表注入。避免每只票单发一次
        # Mongo query，也避免评分函数持有 store 句柄。
        breakout_events_lookup = _load_breakout_events_lookup(store, target_date)
        _validate_daily_inputs(stock_basic, market_records, benchmark_records, target_date,
                               exclude_st=request.exclude_st)
        facts.update(benchmark_records=benchmark_records, market_records=market_records,
                     stock_basic=stock_basic, daily_basic=daily_basic, moneyflow=moneyflow,
                     gate=gate, breakout_events_lookup=breakout_events_lookup)
    if 'sector_memberships' not in facts:
        from shilun.market.taxonomy import load_taxonomy, sync_taxonomy
        facts['sector_memberships'] = load_taxonomy(store, target_date) or sync_taxonomy(store, target_date)
    benchmark_records, market_records = facts['benchmark_records'], facts['market_records']
    from shilun.market.taxonomy import classify_stocks
    stock_basic = pd.DataFrame(classify_stocks(store, target_date, facts['stock_basic'].to_dict('records'), snapshot=facts['sector_memberships']))
    daily_basic, moneyflow = facts['daily_basic'], facts['moneyflow']
    gate, breakout_events_lookup = facts['gate'], facts['breakout_events_lookup']

    # 单次纯计算只读取 Mongo；外层统一编排器负责“日线初排 -> 候选分钟同步
    # -> 严格资格门重排”。这样算法层保持确定性，同时任何缓存写入入口都不能
    # 再绕过分钟同步。缺分钟数据可以留在待补短名单，但不得进入正式前三。
    stock_minute_bars_lookup, benchmark_minute_bars = _load_minute_bars_from_mongo(
        store, target_date, bm_ticker,
    )

    return evaluate_sector_trends(
        analysis_date=target_date,
        benchmark_ticker=bm_ticker,
        benchmark_bars=pd.DataFrame(benchmark_records),
        market_bars=pd.DataFrame(market_records),
        stock_basic=stock_basic,
        daily_basic=daily_basic,
        moneyflow=moneyflow,
        top_n=request.top_n,
        min_stock_count=request.min_stock_count,
        exclude_st=request.exclude_st,
        include_daily_leaders=include_daily_leaders,
        include_all_sectors=include_all_sectors,
        market_gate=gate,
        trend_lookback_days=trend_lookback_days,
        breakout_events_lookup=breakout_events_lookup,
        stock_minute_bars_lookup=stock_minute_bars_lookup,
        benchmark_minute_bars=benchmark_minute_bars,
        require_minute_data_for_ranking=require_minute_data_for_ranking,
        sector_memberships=facts['sector_memberships'],
        daily_computation_cache=facts.setdefault('daily_computation_cache', {}),
    )


def _validate_daily_inputs(stock_basic, market_records, benchmark_records, day, *, exclude_st=True):
    """Do not replace a complete archive with a partially imported daily batch.

    This is a dataset readiness check, not a stock selection predicate. Sparse
    individual gaps still reach the existing strict MA5 eligibility rules.
    """
    from math import isfinite
    from shilun.market.ma5_universe import iso
    from shilun.review.strong_stock_data import a_share

    expected = set()
    for row in stock_basic.to_dict('records'):
        ticker = str(row.get('ts_code') or row.get('ticker') or '')
        if (not a_share(ticker) or (exclude_st and 'ST' in str(row.get('name') or '').upper())
                or (row.get('list_date') and iso(row['list_date']) > day)
                or (row.get('delist_date') and iso(row['delist_date']) <= day)):
            continue
        expected.add(ticker)
    def valid_close(row):
        try:
            return isfinite(float(row.get('close'))) and float(row['close']) > 0
        except (TypeError, ValueError):
            return False
    current = {row.get('ticker') for row in market_records
               if iso(row.get('date')) == day and valid_close(row)} & expected
    dates = {iso(row.get('date')) for row in market_records if row.get('ticker') in expected
             and iso(row.get('date')) <= day and valid_close(row)}
    if not expected or len(current) / len(expected) < .8:
        raise ValueError(f'{day} 全市场同日日线仅覆盖 {len(current)}/{len(expected)}；等待批次补齐，旧归档保留。')
    if len(dates) < 24 or day not in dates:
        raise ValueError(f'{day} 的24交易日日线历史不足，旧归档保留。')
    if not any(iso(row.get('date')) == day and valid_close(row) for row in benchmark_records):
        raise ValueError(f'{day} 的同日有效基准指数缺失，旧归档保留。')

def _load_minute_bars_from_mongo(
    store, target_date: str, bm_ticker: str,
    *, interval: str = "5",
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]] | None]:
    """只读版：一次性拉当天所有已落库的分钟 K，供 v6 分时共振评分用。

    Mongo 里没有的票走 has_data=False 兜底。要预先落库请跑：
        python -m shilun.jobs.minute_bars_sync_job --date YYYYMMDD
    或者 `shilun-sync-minute-bars --date YYYYMMDD`。

    该函数不访问外部网络，保证 sector 预计算永远在 60-120s 内完成。
    """
    from shilun.market.minute_bars_store import COLLECTION_NAME as MINUTE_COLL, get_minute_bars

    yyyymmdd = target_date.replace("-", "")
    col = store.collection(MINUTE_COLL)
    stock_lookup: dict[str, list[dict[str, Any]]] = {}
    try:
        # 一次性把当日所有分钟 K 拉进内存（40-80 只票 × 48 根 5min bar ≈ 2000-4000 条记录，完全可控）
        cursor = col.find(
            {"trade_date": yyyymmdd, "interval": interval},
            {"_id": 0, "ticker": 1, "bars": 1},
        )
        for doc in cursor:
            tk = str(doc.get("ticker") or "").upper()
            if tk:
                stock_lookup[tk] = list(doc.get("bars") or [])
    except Exception:  # noqa: BLE001
        stock_lookup = {}
    # 大盘：先看批量结果里有没有；没有就单查一次（几乎不多花时间）
    benchmark_bars = stock_lookup.get(bm_ticker.upper())
    if not benchmark_bars:
        try:
            benchmark_bars = get_minute_bars(col, bm_ticker.upper(), yyyymmdd, interval=interval)
        except Exception:  # noqa: BLE001
            benchmark_bars = None
    return stock_lookup, benchmark_bars

def _sector_candidate_minute_targets(
    payload: dict[str, Any],
    *,
    top_n: int,
    max_candidates: int = 198,
) -> list[str]:
    """取正式榜及其缓冲板块的完整分钟短名单，稳定去重后受额度约束。

    不能只取页面前三：分钟维度本来就可能改变前三。这里消费每个板块由
    日线初排生成的 leader/zhongjun 短名单，并额外覆盖四个候选板块，避免
    分钟分进入板块健康度后第 9 名升入前 8 却没有数据。
    """

    sectors = (payload.get("all_sectors") or payload.get("top_sectors") or [])
    sector_count = max(1, int(top_n)) + 4
    targets: list[str] = list(dict.fromkeys(
        str(r["ticker"]).upper() for r in payload.get("candidates", [])
        if r.get("ticker") and (r.get("selection_gate") or {}).get("passed")
    ))[:max(1, int(max_candidates))]
    if len(targets) >= max(1, int(max_candidates)):
        return targets
    for sector in sectors[:sector_count]:
        for ticker in sector.get("minute_candidate_tickers") or []:
            normalized = str(ticker or "").strip().upper()
            if normalized and normalized not in targets:
                targets.append(normalized)
            if len(targets) >= max(1, int(max_candidates)):
                return targets
    return targets

def _minute_target_coverage(store: Any, target_date: str, tickers: list[str]) -> dict[str, Any]:
    from shilun.market.minute_bars_store import COLLECTION_NAME as MINUTE_COLL

    normalized = list(dict.fromkeys(str(ticker or "").upper() for ticker in tickers if ticker))
    if not normalized:
        return {"candidate_count": 0, "available_count": 0, "missing_tickers": []}
    available: set[str] = set()
    try:
        for doc in store.collection(MINUTE_COLL).find(
            {
                "trade_date": target_date.replace("-", ""),
                "interval": "5",
                "ticker": {"$in": normalized},
            },
            {"_id": 0, "ticker": 1, "bars": 1},
        ):
            if doc.get("bars"):
                available.add(str(doc.get("ticker") or "").upper())
    except Exception:  # noqa: BLE001 - coverage is diagnostic; strict ranks remain safe
        available = set()
    missing = [ticker for ticker in normalized if ticker not in available]
    return {
        "candidate_count": len(normalized),
        "available_count": len(available),
        "coverage_ratio": round(len(available) / len(normalized), 4) if normalized else 0.0,
        "missing_count": len(missing),
        "missing_tickers": missing,
    }

def _compute_sector_trends_with_candidate_minutes(
    *,
    store: Any,
    config: Any,
    target_date: str,
    bm_ticker: str,
    lookback_days: int,
    trend_lookback_days: int,
    top_n: int,
    include_daily_leaders: bool,
    include_all_sectors: bool,
    facts_cache: dict | None = None,
) -> dict[str, Any]:
    """日线初排 -> 候选分钟同步 -> 严格分钟资格门重排。

    所有会写 ``sector_trends_cache`` 的入口都必须走这里，避免“大盘强制重算”
    绕过分钟同步后再次把代理分榜单覆盖回缓存。
    """

    from time import perf_counter
    facts_cache = facts_cache if facts_cache is not None else {}
    phase_started = perf_counter()
    initial = _compute_sector_trends_full(
        store=store,
        target_date=target_date,
        bm_ticker=bm_ticker,
        lookback_days=lookback_days,
        trend_lookback_days=trend_lookback_days,
        top_n=top_n,
        include_daily_leaders=include_daily_leaders,
        include_all_sectors=True,
        require_minute_data_for_ranking=False,
        facts_cache=facts_cache,
    )
    minute_targets = _sector_candidate_minute_targets(initial, top_n=top_n)
    initial_seconds = perf_counter() - phase_started
    phase_started = perf_counter()
    minute_sync: dict[str, Any] = {
        "status": "blocked",
        "provider": "klineshare",
        "candidate_count": len(minute_targets),
        "fetched_count": 0,
        "failed_count": 0,
        "failed_tickers": [],
        "scores_recomputed": True,
        "ranking_gate": "strict",
        "reason": "候选分钟同步未执行；缺分钟事实的股票不会进入正式前三",
    }
    current_market_day = datetime.now(MARKET_TIMEZONE).date()
    target_market_day = datetime.strptime(target_date, "%Y-%m-%d").date()
    minute_sync_eligible = 0 <= (current_market_day - target_market_day).days <= 3
    if not minute_targets:
        minute_sync.update(status="empty", reason="日线初排没有生成分钟候选短名单")
    elif not config.klineshare_api_key:
        minute_sync.update(status="blocked", reason="KlineShare Key 未配置，正式榜已按分钟资格门阻断")
    elif not minute_sync_eligible:
        minute_sync.update(status="historical_blocked", reason="目标日超过三天且本地分钟 K 不完整，正式榜不使用代理分")
    else:
        from shilun.data.providers.klineshare_provider import KlineShareClient, KlineShareConfig
        from shilun.jobs.minute_bars_sync_job import MinuteBarsSyncJob

        minute_client = KlineShareClient(KlineShareConfig(
            api_key=config.klineshare_api_key,
            base_url=config.klineshare_base_url,
            timeout=config.klineshare_timeout,
        ))
        sync_result = MinuteBarsSyncJob(store, client=minute_client).run(
            target_date,
            explicit_tickers=minute_targets,
            max_workers=4,
        )
        minute_sync.update({
            "status": "partial" if sync_result.failed_tickers else "success",
            "candidate_count": sync_result.candidate_count,
            "missing_count_before_sync": sync_result.missing_count,
            "fetched_count": sync_result.fetched_count,
            "failed_count": len(sync_result.failed_tickers),
            "failed_tickers": sync_result.failed_tickers,
            "elapsed_seconds": sync_result.elapsed_seconds,
            "reason": "候选池分钟 K 已同步并在本次请求内重排" if not sync_result.failed_tickers else "部分品种源端不可用；这些股票已被正式排名资格门排除",
        })

    minute_seconds = perf_counter() - phase_started
    phase_started = perf_counter()
    # 无论同步成功、已存在还是失败，都再按严格资格门计算一次。这样页面绝不
    # 会因为 provider 失败而回退到日线代理/中性 50 后继续给出正式名次。
    final = _compute_sector_trends_full(
        store=store,
        target_date=target_date,
        bm_ticker=bm_ticker,
        lookback_days=lookback_days,
        trend_lookback_days=trend_lookback_days,
        top_n=top_n,
        include_daily_leaders=include_daily_leaders,
        include_all_sectors=include_all_sectors,
        require_minute_data_for_ranking=True,
        facts_cache=facts_cache,
    )
    coverage = _minute_target_coverage(store, target_date, minute_targets)
    minute_sync["coverage_after_sync"] = coverage
    minute_sync["ranking_ready"] = bool(
        coverage.get("candidate_count")
        and coverage.get("available_count") == coverage.get("candidate_count")
    )
    final["minute_sync"] = minute_sync
    final["minute_refresh_pending"] = False
    final['compute_timings'] = {'initial_seconds': round(initial_seconds, 3),
                                'minute_sync_seconds': round(minute_seconds, 3),
                                'strict_ranking_seconds': round(perf_counter() - phase_started, 3)}
    return final

def _load_breakout_events_lookup(store, target_date: str, lookback_days: int = 30) -> dict[str, dict[str, Any]]:
    """从 breakout_events 集合批量拉最近事件，供 build_candidates 注入评分。

    集合不存在或空表时静默返回 {}，不影响主流程。
    """
    try:
        from shilun.market.breakout_events import build_latest_events_lookup, COLLECTION_NAME
        return build_latest_events_lookup(
            store.collection(COLLECTION_NAME),
            tickers=None,
            on_or_before=target_date,
            lookback_days=lookback_days,
        )
    except Exception:  # noqa: BLE001
        return {}

def _sector_cache_key(target_date: str, bm_ticker: str, trend_lookback_days: int, *, top_n: int = 8) -> dict[str, Any]:
    from shilun.market import SECTOR_ENGINE_VERSION

    return {
        "analysis_date": target_date,
        "benchmark_ticker": bm_ticker,
        "trend_lookback_days": trend_lookback_days,
        "engine_version": SECTOR_ENGINE_VERSION,
        'top_n': top_n,
    }

def _sector_payload_fund_dates(payload: dict[str, Any] | None) -> set[str]:
    """返回板块缓存内实际出现的资金日期；兼容短横线与紧凑日期。"""

    dates: set[str] = set()
    for sector in [
        *((payload or {}).get("all_sectors") or []),
        *((payload or {}).get("top_sectors") or []),
    ]:
        value = (sector.get("fund_flow") or {}).get("as_of_date")
        if value:
            text = str(value)
            if len(text) == 8 and text.isdigit():
                text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
            dates.add(text)
    return dates

def _moneyflow_available_for_date(store: Any, target_date: str) -> bool:
    """只在目标日 moneyflow 已落库时允许把板块资金缓存推进到该日。"""

    try:
        return bool(store.raw_market.find_moneyflow(start_date=target_date, end_date=target_date))
    except Exception:  # noqa: BLE001 - 强制大盘重算仍可使用旧板块缓存
        return False

def _load_sector_cache(store, target_date: str, bm_ticker: str, trend_lookback_days: int, *, strict: bool = False, top_n: int = 8) -> dict[str, Any] | None:
    """尝试从 sector_trends_cache 读缓存。"""
    try:
        cache_col = store.collection("sector_trends_cache")
        cached = cache_col.find_one(
            _sector_cache_key(target_date, bm_ticker, trend_lookback_days, top_n=top_n),
            {"_id": 0, "payload": 1, "computed_at": 1, "engine_version": 1, "compute_time_seconds": 1},
        )
        if not cached and top_n == 8:
            legacy_key = _sector_cache_key(target_date, bm_ticker, trend_lookback_days)
            legacy_key['top_n'] = {'$exists': False}
            cached = cache_col.find_one(legacy_key,
                {"_id": 0, "payload": 1, "computed_at": 1, "engine_version": 1, "compute_time_seconds": 1})
        from shilun.market.taxonomy import VERSION as TAXONOMY_VERSION
        if cached and (cached.get('payload') or {}).get('membership_version') == TAXONOMY_VERSION:
            return cached
    except Exception:  # noqa: BLE001
        if strict:
            raise
        return None
    return None

def _save_sector_cache(
    store,
    target_date: str,
    bm_ticker: str,
    trend_lookback_days: int,
    payload: dict[str, Any],
    compute_time_seconds: float,
    *, top_n: int = 8,
) -> None:
    from shilun.market import SECTOR_ENGINE_VERSION
    cache_col = store.collection("sector_trends_cache")
    key = _sector_cache_key(target_date, bm_ticker, trend_lookback_days, top_n=top_n)
    cache_col.update_one(
        key,
        {
            "$set": {
                **key,
                "engine_version": SECTOR_ENGINE_VERSION,
                "payload": payload,
                "compute_time_seconds": round(compute_time_seconds, 2),
                "computed_at": datetime.now().isoformat(timespec="seconds"),
            }
        },
        upsert=True,
    )
