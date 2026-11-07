"""Job 12C 的执行监控池、分时均价和板块样本共振事实。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import ceil
from statistics import median
from typing import Any, Iterable

import pandas as pd

from shilun.market.holdings import list_holdings
from shilun.market.strategy_candidates import find_latest_strategy_candidates


MAX_DISTINCT_SYMBOLS_PER_MINUTE = 200
MAX_REALTIME_BATCH_SIZE = 20
MAX_TREND_REQUESTS_PER_MINUTE = 60
MIN_SECTOR_VALID_SAMPLES = 8
MIN_SECTOR_COVERAGE = 0.60
PRIORITY_SECTOR_SAMPLE_SIZE = 12
SSE_TICKER = "000001.SH"
CHINEXT_TICKER = "399006.SZ"
DEFAULT_BENCHMARKS = (SSE_TICKER, CHINEXT_TICKER)
TREND_COLLECTION = "intraday_trend_snapshots"
SECTOR_FACT_COLLECTION = "execution_market_facts"
OVERSOLD_FIRST_BOARD_STRATEGY_ID = "oversold_first_board"

# Execution only needs candidate identity/gates and sector sample membership.
# Full research snapshots also contain multi-megabyte price/evidence histories.
SELECTION_POOL_PROJECTION = {
    "_id": 0, "analysis_date": 1, "payload.selection_rule_version": 1,
    **{f"payload.candidates.{field}": 1 for field in (
        "ticker", "name", "sector_name", "industry", "ranking_status", "selection_gate.passed",
    )},
}
SECTOR_MONITOR_PROJECTION = {
    **SELECTION_POOL_PROJECTION,
    **{f"payload.top_sectors.{field}": 1 for field in (
        "sector_name", "leader_candidates", "zhongjun_candidates",
        "minute_candidate_tickers", "candidates", "stocks",
    )},
}


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _latest(collection: Any, query: dict[str, Any], sort_field: str,
            projection: dict[str, int] | None = None) -> dict[str, Any] | None:
    projection = projection if projection is not None else {"_id": 0}
    direct = getattr(collection, "find_one", None)
    if callable(direct):
        try:
            found = direct(query, projection, sort=[(sort_field, -1)])
        except TypeError:
            found = direct(query, projection)
        return dict(found) if found else None
    cursor = collection.find(query, projection)
    try:
        cursor = cursor.sort(sort_field, -1)
    except TypeError:
        cursor = sorted(
            list(cursor),
            key=lambda item: str(item.get(sort_field) or ""),
            reverse=True,
        )
    limiter = getattr(cursor, "limit", None)
    if callable(limiter):
        cursor = limiter(1)
    return next((dict(item) for item in cursor), None)


@dataclass(frozen=True)
class ExecutionMonitorItem:
    ticker: str
    priority: int
    sources: tuple[str, ...]
    sectors: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    strategy_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionMonitorPool:
    items: tuple[ExecutionMonitorItem, ...]
    sector_samples: dict[str, tuple[dict[str, str], ...]]
    holding_tickers: tuple[str, ...]
    selection_pool_tickers: tuple[str, ...] = ()
    scenario_tickers: tuple[str, ...] = ()
    priority_sectors: tuple[str, ...] = ()

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(item.ticker for item in self.items)


def _latest_selection_pool_rows(store: Any, trade_date: str) -> list[dict[str, Any]]:
    """读取最近一次完整选品池；长期状态缺失时使用板块计算缓存。

    `candidate_pool_states` 是长期事实；当前选品页直接消费
    `sector_trends_cache.payload.candidates`，因此这里必须读取同一份完整池，不能
    把用户手工勾选的少量 `intraday_watchlist` 误当成完整选品池。淘汰项不进入
    执行观察范围。
    """

    date_text = str(trade_date or "").replace("-", "")
    iso_date = (
        f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}"
        if len(date_text) == 8 and date_text.isdigit()
        else str(trade_date or "")[:10]
    )
    try:
        cursor = store.collection("candidate_pool_states").find(
            {"analysis_date": {"$lte": iso_date}},
            {
                "_id": 0,
                "ticker": 1,
                "name": 1,
                "industry": 1,
                "pool_status": 1,
                "analysis_date": 1,
                "strategy_ids": 1,
            },
        )
        try:
            cursor = cursor.sort("analysis_date", -1)
        except TypeError:
            cursor = sorted(list(cursor), key=lambda item: str(item.get("analysis_date") or ""), reverse=True)
        limiter = getattr(cursor, "limit", None)
        if callable(limiter):
            cursor = limiter(MAX_DISTINCT_SYMBOLS_PER_MINUTE * 5)
        rows = [dict(item) for item in cursor]
    except Exception:  # noqa: BLE001 - 空集合或测试桩安全降级
        rows = []
    latest_date = max((str(item.get("analysis_date") or "") for item in rows), default="")
    pool_rows = (
        [
            item for item in rows
            if str(item.get("analysis_date") or "") == latest_date
            and str(item.get("pool_status") or "") != "reject_pool"
            and _normalize_ticker(item.get("ticker"))
        ]
        if latest_date else []
    )

    # A versioned full-market MA5 snapshot supersedes the former sector-first pool,
    # including an intentionally empty snapshot. Keep unrelated strategy rows.
    from shilun.market.ma5_liquidity import MA5_SELECTION_RULE_VERSION
    current = _latest(store.collection("sector_trends_cache"),
                      {"analysis_date": {"$lte": iso_date}, "benchmark_ticker": "000001.SH",
                       "trend_lookback_days": 60,
                       "payload.selection_rule_version": MA5_SELECTION_RULE_VERSION}, "analysis_date",
                      projection=SELECTION_POOL_PROJECTION)
    current_payload = (current or {}).get("payload") or {}
    has_current = current_payload.get("selection_rule_version") == MA5_SELECTION_RULE_VERSION
    if has_current:
        retained = []
        for item in pool_rows:
            ids = {v.strip().lower() for v in str(item.get("strategy_ids") or "ma5_v0_2").split(",") if v.strip()}
            ids.discard("ma5_v0_2")
            if ids:
                retained.append({**item, "strategy_ids": ",".join(sorted(ids))})
        pool_rows = retained
        for item in current_payload.get("candidates") or []:
            if not (item.get("selection_gate") or {}).get("passed") or item.get("ranking_status") == "excluded":
                continue
            pool_rows.append({**item, "industry": item.get("sector_name") or item.get("industry"),
                              "pool_status": "candidate_pool", "analysis_date": current.get("analysis_date"),
                              "strategy_ids": "ma5_v0_2"})

    # Whole-market T-1 strategies live on the ranked snapshot instead of the
    # sector-first candidate pool.  Merge the latest frozen selection so the
    # next session's monitor consumes exactly the same evidence as the UI.
    try:
        _, strategy_rows = find_latest_strategy_candidates(
            store,
            target_date=iso_date,
            strategy_id=OVERSOLD_FIRST_BOARD_STRATEGY_ID,
            exclude_st=True,
            limit=MAX_DISTINCT_SYMBOLS_PER_MINUTE,
        )
    except Exception:  # noqa: BLE001 - strategy layer is additive
        strategy_rows = []
    if strategy_rows:
        merged = {
            _normalize_ticker(item.get("ticker")): dict(item)
            for item in pool_rows
            if _normalize_ticker(item.get("ticker"))
        }
        for item in strategy_rows:
            ticker = _normalize_ticker(item.get("ticker"))
            if not ticker:
                continue
            previous = merged.get(ticker, {})
            strategy_ids = {
                value.strip().lower()
                for value in str(previous.get("strategy_ids") or "").split(",")
                if value.strip()
            }
            strategy_ids.add(OVERSOLD_FIRST_BOARD_STRATEGY_ID)
            merged[ticker] = {
                **previous,
                **item,
                "ticker": ticker,
                "strategy_ids": ",".join(sorted(strategy_ids)),
                "pool_status": item.get("pool_status") or "strategy_selected",
            }
        pool_rows = list(merged.values())

    if pool_rows or has_current:
        return pool_rows

    sector_cache = _sector_cache(store, date_text)
    payload = sector_cache.get("payload") or {}
    cached_date = str(sector_cache.get("analysis_date") or iso_date)
    return [
        {
            "ticker": _normalize_ticker(item.get("ticker")),
            "name": item.get("name"),
            "industry": item.get("sector_name") or item.get("industry"),
            "pool_status": "candidate_pool",
            "analysis_date": cached_date,
            "strategy_ids": "ma5_v0_2",
        }
        for item in (payload.get("candidates") or [])
        if _normalize_ticker(item.get("ticker"))
        and str(item.get("ranking_status") or "") != "excluded"
    ]


def execution_scope_tickers(store: Any, trade_date: str) -> tuple[str, ...]:
    """返回个股执行事件允许出现的安全范围：持仓、活动委托与完整选品池。"""

    tickers: list[str] = []

    def append(value: Any) -> None:
        ticker = _normalize_ticker(value)
        if ticker and ticker not in tickers:
            tickers.append(ticker)

    holdings_collection = store.collection("holdings")
    try:
        scoped_holdings = list_holdings(holdings_collection)
    except TypeError:  # 简单测试桩可能不实现 PyMongo Cursor.sort
        scoped_holdings = []
    # 部分只读适配器不理解 Mongo 的 `$in`，但会静默返回空列表；空仓时这次
    # 兜底查询同样为空，不改变生产语义。
    if not scoped_holdings:
        scoped_holdings = [
            dict(item) for item in holdings_collection.find({}, {"_id": 0})
            if item.get("status") in {"active", "reduced"}
        ]
    for holding in scoped_holdings:
        append(holding.get("ticker"))
    try:
        for order in store.collection("trade_orders").find(
            {"status": {"$in": ["open", "partially_filled", "pending_cancel"]}},
            {"_id": 0, "ticker": 1},
        ):
            append(order.get("ticker"))
    except Exception:  # noqa: BLE001
        pass
    for row in _latest_selection_pool_rows(store, trade_date):
        append(row.get("ticker"))
    try:
        watchlist = store.collection("intraday_watchlist").find(
            {"enabled": True}, {"_id": 0, "ticker": 1},
        )
        for item in watchlist:
            append(item.get("ticker"))
    except Exception:  # noqa: BLE001
        pass
    return tuple(tickers)


def timeline_scope_tickers(store: Any) -> tuple[str, ...]:
    """返回页面时间线的窄范围：活动持仓、活动委托与用户明确勾选项。"""

    tickers: list[str] = []

    def append(value: Any) -> None:
        ticker = _normalize_ticker(value)
        if ticker and ticker not in tickers:
            tickers.append(ticker)

    holdings_collection = store.collection("holdings")
    try:
        holdings = list_holdings(holdings_collection)
    except TypeError:
        holdings = []
    if not holdings:
        holdings = [
            dict(item) for item in holdings_collection.find({}, {"_id": 0})
            if item.get("status") in {"active", "reduced"}
            and int(item.get("quantity") or 0) > 0
        ]
    for holding in holdings:
        append(holding.get("ticker"))
    try:
        for order in store.collection("trade_orders").find({}, {"_id": 0, "ticker": 1, "status": 1}):
            if order.get("status") in {"open", "partially_filled", "pending_cancel"}:
                append(order.get("ticker"))
    except Exception:  # noqa: BLE001 - 测试桩或空集合安全降级
        pass
    try:
        for item in store.collection("intraday_watchlist").find({}, {"_id": 0, "ticker": 1, "enabled": 1}):
            if item.get("enabled") is True:
                append(item.get("ticker"))
    except Exception:  # noqa: BLE001
        pass
    return tuple(tickers)


def _sector_cache(store: Any, trade_date: str) -> dict[str, Any]:
    compact = str(trade_date).replace("-", "")
    date_text = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    collection = store.collection("sector_trends_cache")
    from shilun.market import SECTOR_ENGINE_VERSION
    current = _latest(collection, {"analysis_date": {"$lte": date_text},
                                  "engine_version": SECTOR_ENGINE_VERSION}, "analysis_date",
                      projection=SECTOR_MONITOR_PROJECTION)
    if current:
        return current
    exact = _latest(collection, {"analysis_date": date_text}, "computed_at", projection=SECTOR_MONITOR_PROJECTION)
    return exact or _latest(collection, {"analysis_date": {"$lte": date_text}}, "analysis_date",
                            projection=SECTOR_MONITOR_PROJECTION) or {}


def _normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _stock_industry_lookup(store: Any) -> tuple[dict[str, str], dict[str, list[str]]]:
    try:
        rows = list(store.collection("stock_basic").find(
            {}, {"_id": 0, "ts_code": 1, "ticker": 1, "industry": 1, "name": 1},
        ))
    except Exception:  # noqa: BLE001 - 行业映射是增强事实，缺失时安全降级
        rows = []
    by_ticker: dict[str, str] = {}
    by_industry: dict[str, list[str]] = {}
    for row in rows:
        ticker = _normalize_ticker(row.get("ts_code") or row.get("ticker"))
        industry = str(row.get("industry") or "").strip()
        name = str(row.get("name") or "").strip().upper()
        if not ticker or not industry or "ST" in name:
            continue
        by_ticker[ticker] = industry
        by_industry.setdefault(industry, []).append(ticker)
    return by_ticker, by_industry


def _industry_peer_rows(
    store: Any,
    *,
    industries: Iterable[str],
    analysis_date: str,
    by_industry: dict[str, list[str]],
) -> dict[str, list[dict[str, str]]]:
    """按最近完成日成交额选行业代表，避免用随机字母序补板块样本。"""

    industry_names = tuple(dict.fromkeys(str(item).strip() for item in industries if str(item).strip()))
    tickers = tuple(dict.fromkeys(
        ticker for industry in industry_names for ticker in by_industry.get(industry, [])
    ))
    if not tickers:
        return {industry: [] for industry in industry_names}
    date_text = str(analysis_date or "").replace("-", "")
    iso_date = (
        f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}"
        if len(date_text) == 8 and date_text.isdigit() else str(analysis_date or "")[:10]
    )
    try:
        daily_rows = list(store.collection("market_daily_bars").find(
            {"date": iso_date, "ticker": {"$in": list(tickers)}},
            {"_id": 0, "ticker": 1, "amount": 1},
        ))
    except Exception:  # noqa: BLE001
        daily_rows = []
    try:
        basic_rows = list(store.collection("daily_basic").find(
            {"trade_date": date_text, "ts_code": {"$in": list(tickers)}},
            {"_id": 0, "ts_code": 1, "total_mv": 1},
        ))
    except Exception:  # noqa: BLE001
        basic_rows = []
    amount_by_ticker = {
        _normalize_ticker(row.get("ticker")): float(row.get("amount") or 0)
        for row in daily_rows
    }
    market_value_by_ticker = {
        _normalize_ticker(row.get("ts_code")): float(row.get("total_mv") or 0)
        for row in basic_rows
    }
    result: dict[str, list[dict[str, str]]] = {}
    for industry in industry_names:
        ranked = sorted(
            by_industry.get(industry, []),
            key=lambda ticker: (
                amount_by_ticker.get(ticker, 0.0),
                market_value_by_ticker.get(ticker, 0.0),
                ticker,
            ),
            reverse=True,
        )
        result[industry] = [
            {"ticker": ticker, "role": "industry_peer"}
            for ticker in ranked[:PRIORITY_SECTOR_SAMPLE_SIZE]
        ]
    return result


def intraday_average_from_quote(quote: dict[str, Any]) -> float | None:
    """用源端当日成交额/成交量计算真实均价；单位异常时拒绝使用。"""

    current = _positive(quote.get("current"))
    amount = _positive(quote.get("amount"))
    volume = _positive(quote.get("volume"))
    if current is None or amount is None or volume is None:
        return None
    amount_unit = str(quote.get("amount_unit") or "thousand_cny")
    amount_yuan = amount * 1000 if amount_unit == "thousand_cny" else amount
    average = amount_yuan / volume
    if not 0.2 <= average / current <= 5.0:
        return None
    return round(average, 6)


def _sector_sample_rows(payload: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    for sector in payload.get("top_sectors") or []:
        name = str(sector.get("sector_name") or "").strip()
        if not name:
            continue
        rows: list[dict[str, str]] = []
        seen: set[str] = set()

        def append(values: Iterable[Any], role: str) -> None:
            for value in values:
                ticker = _normalize_ticker(value.get("ticker") if isinstance(value, dict) else value)
                if not ticker or ticker in seen or len(rows) >= 20:
                    continue
                seen.add(ticker)
                rows.append({"ticker": ticker, "role": role})

        append(sector.get("leader_candidates") or [], "leader")
        append(sector.get("zhongjun_candidates") or [], "zhongjun")
        append(sector.get("minute_candidate_tickers") or [], "top20")
        append(sector.get("candidates") or sector.get("stocks") or [], "top20")
        result[name] = rows
    return result


def collect_execution_monitor_pool(
    store: Any,
    trade_date: str,
    *,
    max_symbols: int = MAX_DISTINCT_SYMBOLS_PER_MINUTE,
) -> ExecutionMonitorPool:
    """合并持仓、完整选品池和板块样本，并稳定截断到行情额度。

    板块样本只服务板块共振事实；个股全场景只扫描持仓与完整选品池，避免
    把行业代表股误写入执行事件时间线。
    """

    if max_symbols < len(DEFAULT_BENCHMARKS):
        raise ValueError("max_symbols 必须至少容纳双基准指数")
    merged: dict[str, dict[str, Any]] = {}

    def add(
        ticker: Any,
        *,
        priority: int,
        source: str,
        sector: str = "",
        role: str = "",
        strategy_ids: Iterable[str] = (),
    ) -> None:
        normalized = _normalize_ticker(ticker)
        if not normalized:
            return
        item = merged.setdefault(normalized, {
            "priority": priority,
            "sources": set(),
            "sectors": set(),
            "roles": set(),
            "strategy_ids": set(),
        })
        item["priority"] = min(int(item["priority"]), priority)
        item["sources"].add(source)
        if sector:
            item["sectors"].add(sector)
        if role:
            item["roles"].add(role)
        for strategy_id in strategy_ids:
            normalized_strategy = str(strategy_id or "").strip().lower()
            if normalized_strategy:
                item["strategy_ids"].add(normalized_strategy)

    holdings = list_holdings(store.collection("holdings"))
    industry_by_ticker, tickers_by_industry = _stock_industry_lookup(store)
    holding_tickers: list[str] = []
    selection_pool_tickers: list[str] = []
    execution_by_sector: dict[str, list[str]] = {}
    priority_sector_tickers: dict[str, list[str]] = {}
    for holding in holdings:
        ticker = _normalize_ticker(holding.get("ticker"))
        if not ticker:
            continue
        sector_name = str(holding.get("sector_name") or industry_by_ticker.get(ticker) or "").strip()
        holding_tickers.append(ticker)
        if sector_name:
            priority_sector_tickers.setdefault(sector_name, []).append(ticker)
        add(
            ticker,
            priority=0,
            source="holding",
            sector=sector_name,
            role="holding",
        )

    for item in _latest_selection_pool_rows(store, trade_date):
        ticker = _normalize_ticker(item.get("ticker"))
        sector_name = str(item.get("industry") or industry_by_ticker.get(ticker) or "").strip()
        if not ticker:
            continue
        selection_pool_tickers.append(ticker)
        if sector_name:
            execution_by_sector.setdefault(sector_name, []).append(ticker)
            priority_sector_tickers.setdefault(sector_name, []).append(ticker)
        add(
            ticker,
            priority=1,
            source="selection_pool",
            sector=sector_name,
            role="execution_candidate",
            strategy_ids=(
                value.strip()
                for value in str(
                    item.get("strategy_ids") or item.get("strategy_id") or "ma5_v0_2"
                ).split(",")
                if value.strip()
            ),
        )

    try:
        watchlist = list(
            store.collection("intraday_watchlist")
            .find({"enabled": True}, {"_id": 0})
            .sort("updated_at", -1)
        )
    except Exception:  # noqa: BLE001 - 旧测试桩或尚未建集合时安全降级
        watchlist = []
    for item in watchlist:
        ticker = _normalize_ticker(item.get("ticker"))
        sector_name = str(item.get("sector_name") or industry_by_ticker.get(ticker) or "").strip()
        if sector_name and ticker:
            execution_by_sector.setdefault(sector_name, []).append(ticker)
            priority_sector_tickers.setdefault(sector_name, []).append(ticker)
        if ticker:
            selection_pool_tickers.append(ticker)
        add(
            ticker,
            priority=1,
            source="selection_pool",
            sector=sector_name,
            role="execution_candidate",
        )

    try:
        plans = list(store.collection("trading_watch_plans").find(
            {"trade_date": trade_date},
            {"_id": 0},
        ))
    except Exception:  # noqa: BLE001
        plans = []
    for plan in plans:
        ticker = _normalize_ticker(plan.get("ticker"))
        sector_name = str(plan.get("sector_name") or industry_by_ticker.get(ticker) or "").strip()
        if sector_name and ticker:
            execution_by_sector.setdefault(sector_name, []).append(ticker)
        add(
            ticker,
            priority=2,
            source="trading_watch_plan",
            sector=sector_name,
            role="execution_candidate",
        )

    sector_cache = _sector_cache(store, trade_date)
    sample_rows = _sector_sample_rows((sector_cache.get("payload") or {}))
    holding_by_sector: dict[str, list[str]] = {}
    for holding in holdings:
        ticker = _normalize_ticker(holding.get("ticker"))
        sector = str(holding.get("sector_name") or industry_by_ticker.get(ticker) or "").strip()
        if sector and ticker:
            holding_by_sector.setdefault(sector, []).append(ticker)
    for sector, tickers in holding_by_sector.items():
        rows = sample_rows.setdefault(sector, [])
        existing = {item["ticker"] for item in rows}
        for ticker in tickers:
            if ticker not in existing and len(rows) < 20:
                rows.append({"ticker": ticker, "role": "holding"})
                existing.add(ticker)
    for sector, tickers in execution_by_sector.items():
        rows = sample_rows.setdefault(sector, [])
        existing = {item["ticker"] for item in rows}
        for ticker in tickers:
            if ticker not in existing and len(rows) < 20:
                rows.append({"ticker": ticker, "role": "execution_candidate"})
                existing.add(ticker)
    peer_rows = _industry_peer_rows(
        store,
        industries=priority_sector_tickers,
        analysis_date=str(sector_cache.get("analysis_date") or trade_date),
        by_industry=tickers_by_industry,
    )
    for sector, required_tickers in priority_sector_tickers.items():
        existing_rows = sample_rows.get(sector, [])
        ordered_rows: list[dict[str, str]] = []
        seen: set[str] = set()

        def append_rows(rows: Iterable[dict[str, str]]) -> None:
            for row in rows:
                ticker = _normalize_ticker(row.get("ticker"))
                if not ticker or ticker in seen or len(ordered_rows) >= PRIORITY_SECTOR_SAMPLE_SIZE:
                    continue
                seen.add(ticker)
                ordered_rows.append({"ticker": ticker, "role": str(row.get("role") or "sample")})

        append_rows(row for row in existing_rows if row.get("role") in {"leader", "zhongjun"})
        append_rows({"ticker": ticker, "role": "execution_priority"} for ticker in required_tickers)
        append_rows(existing_rows)
        append_rows(peer_rows.get(sector, []))
        sample_rows[sector] = ordered_rows
    for sector, rows in sample_rows.items():
        for row in rows:
            # 行业样本只能占用完整选品池之后的剩余行情额度。否则样本数量较多时
            # 会把真正的选品池股票挤出场景计算，违背“时间线只看完整选品池”。
            priority = 2 if row["role"] in {"leader", "zhongjun"} else 3
            add(
                row["ticker"],
                priority=priority,
                source="sector_sample",
                sector=sector,
                role=row["role"],
            )

    for benchmark in DEFAULT_BENCHMARKS:
        add(benchmark, priority=-1, source="benchmark", role="benchmark")

    ordered = sorted(merged.items(), key=lambda pair: (pair[1]["priority"], pair[0]))
    selected = ordered[:max_symbols]
    # 保留完整样本作为覆盖率分母；被额度截断的股票应体现为缺失，而不是被悄悄
    # 移出分母后虚增覆盖率。
    filtered_samples = {
        sector: tuple(rows)
        for sector, rows in sample_rows.items()
    }
    items = tuple(
        ExecutionMonitorItem(
            ticker=ticker,
            priority=int(data["priority"]),
            sources=tuple(sorted(data["sources"])),
            sectors=tuple(sorted(data["sectors"])),
            roles=tuple(sorted(data["roles"])),
            strategy_ids=tuple(sorted(data["strategy_ids"])),
        )
        for ticker, data in selected
    )
    selected_tickers = {item.ticker for item in items}
    selection_scope = tuple(
        ticker for ticker in dict.fromkeys(selection_pool_tickers)
        if ticker in selected_tickers
    )
    scenario_scope = tuple(
        ticker for ticker in dict.fromkeys([*holding_tickers, *selection_scope])
        if ticker in selected_tickers
    )
    return ExecutionMonitorPool(
        items=items,
        sector_samples=filtered_samples,
        holding_tickers=tuple(dict.fromkeys(holding_tickers)),
        selection_pool_tickers=selection_scope,
        scenario_tickers=scenario_scope,
        priority_sectors=tuple(sorted(priority_sector_tickers)),
    )


def trend_target_tickers(
    pool: ExecutionMonitorPool,
    *,
    max_requests: int = MAX_TREND_REQUESTS_PER_MINUTE,
) -> tuple[str, ...]:
    """按监控优先级选取分时请求；绝不超过账户每分钟 60 次额度。"""

    limit = max(0, min(int(max_requests), MAX_TREND_REQUESTS_PER_MINUTE))
    selected: list[str] = []
    seen: set[str] = set()

    def append(ticker: Any) -> None:
        normalized = _normalize_ticker(ticker)
        if normalized and normalized not in seen and len(selected) < limit:
            seen.add(normalized)
            selected.append(normalized)

    for ticker in pool.holding_tickers:
        append(ticker)
    for sector in pool.priority_sectors:
        rows = pool.sector_samples.get(sector) or ()
        target = min(len(rows), max(MIN_SECTOR_VALID_SAMPLES, ceil(len(rows) * MIN_SECTOR_COVERAGE)))
        for row in rows[:target]:
            append(row.get("ticker"))
    for item in pool.items:
        append(item.ticker)
    return tuple(selected)


def latest_intraday_trend_snapshot(
    frame: pd.DataFrame,
    *,
    ticker: str,
    trade_date: str,
    fetched_at: datetime,
) -> dict[str, Any] | None:
    """校验分时价格单位后生成单票最新均价事实。"""

    if frame is None or frame.empty:
        return None
    row = frame.sort_values("datetime").iloc[-1]
    price = _positive(row.get("price"))
    avg_price = _positive(row.get("avg_price"))
    if price is None or avg_price is None:
        return None
    # 同一价格口径不应出现 100/1000 倍偏差；越界时拒绝发布硬规则事实。
    scale_ratio = avg_price / price
    if not 0.2 <= scale_ratio <= 5.0:
        raise ValueError(f"{ticker} 分时均价与现价单位不一致: ratio={scale_ratio:.6f}")
    source_at = pd.Timestamp(row.get("datetime"))
    return {
        "ticker": _normalize_ticker(ticker),
        "trade_date": trade_date,
        "price": round(price, 6),
        "avg_price": round(avg_price, 6),
        "price_unit": str(row.get("price_unit") or "cny_per_share"),
        "source_at": source_at.isoformat(),
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "source": str(row.get("source") or "klineshare"),
        "source_tick_count": len(frame),
    }


def _return_from_quote(quote: dict[str, Any]) -> float | None:
    current = _positive(quote.get("current"))
    previous = _positive(quote.get("prev_close"))
    if current is not None and previous is not None:
        return current / previous - 1.0
    try:
        return float(quote.get("pct_chg"))
    except (TypeError, ValueError):
        return None


def matching_benchmark(ticker: str) -> str:
    code = _normalize_ticker(ticker).split(".", 1)[0]
    return CHINEXT_TICKER if code.startswith(("300", "301")) else SSE_TICKER


def aggregate_sector_resonance(
    sector_samples: dict[str, tuple[dict[str, str], ...]],
    *,
    quotes: dict[str, dict[str, Any]],
    trends: dict[str, dict[str, Any]],
    trade_date: str,
    computed_at: datetime,
) -> list[dict[str, Any]]:
    """按 Job 12C 的硬阈值生成板块样本共振；覆盖不足时不造方向。"""

    facts: list[dict[str, Any]] = []
    for sector_name, samples in sorted(sector_samples.items()):
        expected = len(samples)
        valid_rows: list[dict[str, Any]] = []
        for sample in samples:
            ticker = sample["ticker"]
            quote = quotes.get(ticker) or {}
            trend = trends.get(ticker) or {}
            current = _positive(quote.get("current"))
            avg_price = _positive(trend.get("avg_price"))
            stock_return = _return_from_quote(quote)
            benchmark_ticker = matching_benchmark(ticker)
            benchmark_return = _return_from_quote(quotes.get(benchmark_ticker) or {})
            if None in {current, avg_price, stock_return, benchmark_return}:
                continue
            assert current is not None and avg_price is not None
            assert stock_return is not None and benchmark_return is not None
            valid_rows.append({
                "ticker": ticker,
                "role": sample.get("role") or "sample",
                "avg_price_source": str(trend.get("source") or "klineshare_trend"),
                "stock_return": stock_return,
                "benchmark_ticker": benchmark_ticker,
                "benchmark_return": benchmark_return,
                "up": stock_return > 0,
                "above_avg": current >= avg_price,
                "excess_return": stock_return - benchmark_return,
                "strengthening": current >= avg_price and stock_return > benchmark_return,
            })

        valid_count = len(valid_rows)
        coverage = valid_count / expected if expected else 0.0
        unavailable = valid_count < MIN_SECTOR_VALID_SAMPLES or coverage < MIN_SECTOR_COVERAGE
        up_ratio = sum(bool(row["up"]) for row in valid_rows) / valid_count if valid_count else None
        above_avg_ratio = (
            sum(bool(row["above_avg"]) for row in valid_rows) / valid_count if valid_count else None
        )
        median_excess = median(row["excess_return"] for row in valid_rows) if valid_rows else None
        median_return = median(row["stock_return"] for row in valid_rows) if valid_rows else None
        core_strengthening = any(
            row["role"] in {"leader", "zhongjun"} and row["strengthening"]
            for row in valid_rows
        )
        if unavailable:
            state, label = "unavailable", "样本不足"
            conclusion = None
        elif (
            up_ratio is not None and up_ratio >= 0.60
            and above_avg_ratio is not None and above_avg_ratio >= 0.55
            and median_excess is not None and median_excess > 0
            and core_strengthening
        ):
            state, label, conclusion = "recovery_resonance", "修复共振", "positive"
        elif (
            up_ratio is not None and up_ratio < 0.35
            and above_avg_ratio is not None and above_avg_ratio < 0.35
            and median_excess is not None and median_excess < 0
        ):
            state, label, conclusion = "weak_resonance", "走弱共振", "negative"
        else:
            state, label, conclusion = "divergence", "分歧", None
        facts.append({
            "trade_date": trade_date,
            "sector_name": sector_name,
            "state": state,
            "state_label": label,
            "directional_conclusion": conclusion,
            "expected_sample_count": expected,
            "industry_universe_count": expected,
            "valid_sample_count": valid_count,
            "coverage_ratio": round(coverage, 6),
            "up_ratio": round(up_ratio, 6) if up_ratio is not None else None,
            "above_intraday_avg_ratio": (
                round(above_avg_ratio, 6) if above_avg_ratio is not None else None
            ),
            "median_excess_return": round(median_excess, 8) if median_excess is not None else None,
            "median_return": round(median_return, 8) if median_return is not None else None,
            "core_strengthening": core_strengthening,
            "samples": valid_rows,
            "computed_at": computed_at.isoformat(timespec="seconds"),
            "source": "execution_monitor_sample_v1",
            "sample_scope": "execution_priority_industry_sample",
            "drives_hard_stop": False,
        })
    return facts
