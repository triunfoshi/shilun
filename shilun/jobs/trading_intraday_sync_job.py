"""执行监控池与双指数的 KlineShare 盘中事实采集任务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from time import perf_counter
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from shilun.data.providers.klineshare_provider import KlineShareClient, KlineShareError
from shilun.market.minute_bars_store import COLLECTION_NAME as MINUTE_COLLECTION
from shilun.market.minute_bars_store import upsert_minute_bars
from shilun.trading.market_facts import (
    ExecutionMonitorPool,
    MAX_REALTIME_BATCH_SIZE,
    MAX_TREND_REQUESTS_PER_MINUTE,
    SECTOR_FACT_COLLECTION,
    TREND_COLLECTION,
    aggregate_sector_resonance,
    collect_execution_monitor_pool,
    intraday_average_from_quote,
    latest_intraday_trend_snapshot,
    trend_target_tickers,
)
from shilun.trading.price_limits import FACT_COLLECTION as PRICE_LIMIT_FACT_COLLECTION
from shilun.trading.price_limits import build_price_limit_facts
from shilun.trading.latency import (
    COLLECTION_NAME as LATENCY_COLLECTION,
    record_quote_latency_samples,
)
from shilun.trading.order_lifecycle import expire_trade_orders, settle_t1_positions


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
REALTIME_COLLECTION = "trading_realtime_quotes"
SECTOR_FACT_SNAPSHOT_COLLECTION = "execution_market_fact_snapshots"
DEFAULT_BENCHMARKS = ("000001.SH", "399006.SZ")


@dataclass(frozen=True)
class TradingIntradaySyncResult:
    status: str
    trade_date: str
    ticker_count: int
    quote_count: int
    minute_count: int
    failed_tickers: tuple[str, ...]
    message: str
    decision_count: int = 0
    decision_failed_tickers: tuple[str, ...] = ()
    latency_sample_count: int = 0
    trend_count: int = 0
    trend_failed_tickers: tuple[str, ...] = ()
    sector_fact_count: int = 0
    price_limit_fact_count: int = 0
    t1_settled_count: int = 0
    expired_order_count: int = 0
    expiry_failed_order_ids: tuple[str, ...] = ()
    scenario_decision_count: int = 0
    scenario_failed_tickers: tuple[str, ...] = ()


def is_market_poll_time(now: datetime) -> bool:
    local = now.astimezone(MARKET_TIMEZONE)
    if local.weekday() >= 5:
        return False
    current = local.time().replace(tzinfo=None)
    return time(9, 30) <= current <= time(11, 35) or time(13, 0) <= current <= time(15, 5)


def closed_kline_frame(
    frame: pd.DataFrame,
    *,
    now: datetime,
    period_seconds: int = 300,
) -> pd.DataFrame:
    """保守丢弃可能仍在形成的当前周期，只保留完整结束的 K 线。"""

    if frame is None or frame.empty or "datetime" not in frame.columns:
        return pd.DataFrame(columns=list(frame.columns) if frame is not None else [])
    timestamps = pd.to_datetime(frame["datetime"], errors="coerce")
    if timestamps.dt.tz is None:
        timestamps = timestamps.dt.tz_localize(MARKET_TIMEZONE)
    local_now = pd.Timestamp(now.astimezone(MARKET_TIMEZONE))
    cutoff = local_now.floor(f"{period_seconds}s") - pd.Timedelta(seconds=period_seconds)
    result = frame.copy()
    result["datetime"] = timestamps
    local_timestamps = result["datetime"].dt.tz_convert(MARKET_TIMEZONE)
    same_trade_date = local_timestamps.dt.date == local_now.date()
    local_times = local_timestamps.dt.time
    # KlineShare 偶尔会返回 15:05-15:30 的盘后固定价/零成交记录。
    # A 股盘中趋势只保留连续竞价时段，避免尾部横线压扁真实波动。
    in_session = (
        ((local_times >= time(9, 30)) & (local_times <= time(11, 30)))
        | ((local_times >= time(13, 0)) & (local_times <= time(15, 0)))
    )
    return result[
        same_trade_date & in_session & (result["datetime"] <= cutoff)
    ].sort_values("datetime").reset_index(drop=True)


def minute_bar_target_tickers(
    pool: ExecutionMonitorPool,
    *,
    now: datetime,
    limit: int = MAX_TREND_REQUESTS_PER_MINUTE,
) -> tuple[str, ...]:
    """Rotate the 5-minute K quota without starving later pool members.

    Holdings and the two benchmark indices are always included.  The remaining
    quota rotates deterministically by market minute, so a 200-symbol execution
    pool is covered within a few sync cycles instead of repeatedly requesting
    the same first 60 symbols.
    """

    if limit <= 0:
        return ()
    ordered = list(dict.fromkeys(str(item).upper() for item in pool.tickers if item))
    core = list(dict.fromkeys([
        *DEFAULT_BENCHMARKS,
        *(str(item).upper() for item in pool.holding_tickers if item),
    ]))
    selected = [ticker for ticker in core if ticker in ordered][:limit]
    remaining = [ticker for ticker in ordered if ticker not in selected]
    capacity = limit - len(selected)
    if capacity <= 0 or not remaining:
        return tuple(selected)
    if len(remaining) <= capacity:
        return tuple([*selected, *remaining])
    local = now.astimezone(MARKET_TIMEZONE)
    minute_index = local.hour * 60 + local.minute
    offset = (minute_index * capacity) % len(remaining)
    rotated = remaining[offset:] + remaining[:offset]
    return tuple([*selected, *rotated[:capacity]])


def _clean_document(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        # KlineShare 即使未开五档权限也会归一化出空 list；pd.isna(list)
        # 返回数组，直接用于 if 会令整次盘中任务异常退出。
        if isinstance(value, (dict, list, tuple)):
            cleaned[key] = value
        elif pd.isna(value):
            cleaned[key] = None
        elif hasattr(value, "item"):
            cleaned[key] = value.item()
        else:
            cleaned[key] = value
    return cleaned


class TradingIntradaySyncJob:
    def __init__(
        self,
        store: Any,
        client: KlineShareClient,
        *,
        index_quote_client: Any | None = None,
        index_minute_client: Any | None = None,
        decision_refresher: Callable[[str, datetime], Any] | None = None,
        scenario_refresher: Callable[[datetime], dict[str, Any]] | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.index_quote_client = index_quote_client
        self.index_minute_client = index_minute_client
        self.decision_refresher = decision_refresher
        self.scenario_refresher = scenario_refresher

    def _fetch_minute_frame(self, ticker: str) -> tuple[pd.DataFrame, str]:
        """指数分钟线使用独立回退源，避免误送 KlineShare 个股接口。"""

        if ticker in DEFAULT_BENCHMARKS and self.index_minute_client is not None:
            frame = self.index_minute_client.fetch_minute_bars(ticker, period="5")
            return frame, frame.attrs.get("source", "eastmoney")
        return (
            self.client.fetch_kline(
                ticker,
                period=300,
                count=100,
                adjust_type="none",
            ),
            "klineshare",
        )

    def _fetch_realtime_quotes(
        self,
        tickers: tuple[str, ...] | list[str],
    ) -> tuple[list[dict[str, Any]], int, set[str], float, list[str]]:
        quote_records: list[dict[str, Any]] = []
        quote_count = 0
        quote_tickers: set[str] = set()
        request_latency_ms = 0.0
        failed: list[str] = []
        quote_collection = self.store.collection(REALTIME_COLLECTION)

        def fetch_batch(batch: list[str], *, retry_missing: bool = True) -> None:
            nonlocal quote_count, request_latency_ms
            if not batch:
                return
            try:
                request_started = perf_counter()
                quotes = self.client.fetch_realtime(batch)
                request_latency_ms += (perf_counter() - request_started) * 1000
                batch_records = quotes.to_dict("records")
                returned: set[str] = set()
                quote_records.extend(batch_records)
                for row in batch_records:
                    document = _clean_document(row)
                    ticker = str(document.get("ticker") or "")
                    if not ticker:
                        continue
                    returned.add(ticker.upper())
                    quote_collection.update_one(
                        {"ticker": ticker},
                        {"$set": document},
                        upsert=True,
                    )
                    quote_count += 1
                    quote_tickers.add(ticker)
                missing = [ticker for ticker in batch if ticker.upper() not in returned]
                if missing and retry_missing and len(batch) > 1:
                    for ticker in missing:
                        fetch_batch([ticker], retry_missing=False)
                elif missing:
                    failed.extend(missing)
            except KlineShareError:
                # One unsupported symbol must not invalidate the other 19
                # symbols in a realtime batch.  Bisect only the failed batch;
                # single-symbol failures remain explicit data blockers.
                if len(batch) > 1:
                    midpoint = len(batch) // 2
                    fetch_batch(batch[:midpoint], retry_missing=retry_missing)
                    fetch_batch(batch[midpoint:], retry_missing=retry_missing)
                else:
                    failed.extend(batch)

        for start in range(0, len(tickers), MAX_REALTIME_BATCH_SIZE):
            fetch_batch(list(tickers[start:start + MAX_REALTIME_BATCH_SIZE]))

        # KlineShare 的 /v1/realtime 不接受指数；指数必须从具有源端时间戳的
        # 新浪实时报价补齐，否则双指数事实会令所有场景都 data_blocked。
        missing_benchmarks = [
            ticker for ticker in DEFAULT_BENCHMARKS
            if ticker in tickers and ticker not in quote_tickers
        ]
        if missing_benchmarks and self.index_quote_client is not None:
            try:
                request_started = perf_counter()
                index_quotes = self.index_quote_client.fetch_index_realtime(missing_benchmarks)
                request_latency_ms += (perf_counter() - request_started) * 1000
                for row in index_quotes.to_dict("records"):
                    document = _clean_document(row)
                    ticker = str(document.get("ticker") or "").upper()
                    if ticker not in missing_benchmarks:
                        continue
                    document["ticker"] = ticker
                    document.setdefault("source", "tushare_sina")
                    quote_collection.update_one(
                        {"ticker": ticker},
                        {"$set": document},
                        upsert=True,
                    )
                    quote_records.append(document)
                    quote_count += 1
                    quote_tickers.add(ticker)
                failed = [ticker for ticker in failed if ticker not in quote_tickers]
            except Exception:  # noqa: BLE001 - 回退失败继续作为明确数据阻断
                pass
        return quote_records, quote_count, quote_tickers, request_latency_ms, failed

    def run_quotes_only(
        self,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> TradingIntradaySyncResult:
        """Fast lane used every minute so slow K/trend requests cannot age quotes."""

        explicit_now = now is not None
        current = now or datetime.now(tz=MARKET_TIMEZONE)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now 必须包含时区")
        trade_date = current.astimezone(MARKET_TIMEZONE).strftime("%Y%m%d")
        if not force and not is_market_poll_time(current):
            return TradingIntradaySyncResult(
                status="skipped",
                trade_date=trade_date,
                ticker_count=0,
                quote_count=0,
                minute_count=0,
                failed_tickers=(),
                message="当前不在盘中报价快车道时段",
            )
        pool = collect_execution_monitor_pool(self.store, trade_date)
        quote_records, quote_count, _, request_latency_ms, failed = self._fetch_realtime_quotes(pool.tickers)
        observed_at = current if explicit_now else datetime.now(tz=MARKET_TIMEZONE)
        latency_count = record_quote_latency_samples(
            self.store.collection(LATENCY_COLLECTION),
            [_clean_document(item) for item in quote_records],
            request_latency_ms=request_latency_ms,
            calibration_eligible=not force,
            decision_at=observed_at,
        )
        failed_unique = tuple(dict.fromkeys(failed))
        status = "success" if not failed_unique else "partial"
        updater = getattr(self.store, "upsert_sync_state", None)
        if callable(updater):
            updater({
                "dataset": "trading_realtime_quotes_fast",
                "scope": trade_date,
                "status": status,
                "ticker_count": len(pool.tickers),
                "quote_count": quote_count,
                "failed_tickers": list(failed_unique),
                "latency_sample_count": latency_count,
                "updated_at": observed_at.isoformat(timespec="seconds"),
            })
        return TradingIntradaySyncResult(
            status=status,
            trade_date=trade_date,
            ticker_count=len(pool.tickers),
            quote_count=quote_count,
            minute_count=0,
            failed_tickers=failed_unique,
            message=f"fast_quotes={quote_count}",
            latency_sample_count=latency_count,
        )

    def run(
        self,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> TradingIntradaySyncResult:
        explicit_now = now is not None
        current = now or datetime.now(tz=MARKET_TIMEZONE)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now 必须包含时区")
        trade_date = current.astimezone(MARKET_TIMEZONE).strftime("%Y%m%d")
        expiry = expire_trade_orders(
            orders_collection=self.store.collection("trade_orders"),
            holdings_collection=self.store.collection("holdings"),
            reservations_collection=self.store.collection("execution_cash_reservations"),
            position_event_collection=self.store.collection("holding_position_events"),
            order_event_collection=self.store.collection("trade_order_events"),
            as_of=current,
        )
        expired_order_ids = tuple(expiry["expired_order_ids"])
        expiry_failed_order_ids = tuple(item["order_id"] for item in expiry["failed_orders"])
        if not force and not is_market_poll_time(current):
            return TradingIntradaySyncResult(
                status="skipped",
                trade_date=trade_date,
                ticker_count=0,
                quote_count=0,
                minute_count=0,
                failed_tickers=(),
                message=(
                    f"当前不在盘中采集时段；已收尾 {len(expired_order_ids)} 笔到期委托"
                    if expired_order_ids else "当前不在盘中采集时段"
                ),
                expired_order_count=len(expired_order_ids),
                expiry_failed_order_ids=expiry_failed_order_ids,
            )

        t1_settled = settle_t1_positions(
            self.store.collection("holdings"),
            as_of_date=current.astimezone(MARKET_TIMEZONE).date(),
            position_event_collection=self.store.collection("holding_position_events"),
            settled_at=current,
        )
        pool = collect_execution_monitor_pool(self.store, trade_date)
        holding_tickers = list(pool.holding_tickers)
        tickers = pool.tickers
        quote_count = 0
        minute_count = 0
        failed: list[str] = []
        quote_tickers: set[str] = set()
        minute_tickers: set[str] = set()
        closed_bars_by_ticker: dict[str, list[dict[str, Any]]] = {}
        latency_sample_count = 0

        # 先更新已闭合 K 线，最后再批量取报价，缩短 quote_at 到决策计算的链路时间。
        minute_collection = self.store.collection(MINUTE_COLLECTION)
        for ticker in minute_bar_target_tickers(pool, now=current):
            try:
                frame, minute_source = self._fetch_minute_frame(ticker)
                frame = closed_kline_frame(frame, now=current, period_seconds=300)
                if frame.empty:
                    failed.append(ticker)
                    continue
                upsert_minute_bars(
                    minute_collection,
                    ticker=ticker,
                    trade_date=trade_date,
                    interval="5",
                    bars=frame.to_dict("records"),
                    source=minute_source,
                )
                minute_count += 1
                minute_tickers.add(ticker)
                closed_bars_by_ticker[ticker] = frame.to_dict("records")
            except KlineShareError:
                failed.append(ticker)

        trend_count = 0
        trend_failed: list[str] = []
        trend_documents: dict[str, dict[str, Any]] = {}
        trend_collection = self.store.collection(TREND_COLLECTION)
        if hasattr(self.client, "fetch_trend"):
            for ticker in trend_target_tickers(pool):
                try:
                    trend = self.client.fetch_trend(ticker)
                    snapshot = latest_intraday_trend_snapshot(
                        trend,
                        ticker=ticker,
                        trade_date=trade_date,
                        fetched_at=current,
                    )
                    if snapshot is None:
                        trend_failed.append(ticker)
                        continue
                    trend_collection.update_one(
                        {"ticker": ticker, "trade_date": trade_date},
                        {"$set": snapshot},
                        upsert=True,
                    )
                    trend_documents[ticker] = snapshot
                    trend_count += 1
                except (KlineShareError, ValueError):
                    trend_failed.append(ticker)

        # Quote last: minute K and per-symbol trend are the slow quota-bound
        # calls.  Fetching realtime after them keeps the quote fresh for sector
        # aggregation and the scenario decision that follows.
        quote_records, quote_count, quote_tickers, request_latency_ms, quote_failed = (
            self._fetch_realtime_quotes(tickers)
        )
        failed.extend(quote_failed)
        quote_collection = self.store.collection(REALTIME_COLLECTION)

        # 实时接口偶尔会在一个批次内少返回个别代码。板块宽度可以使用同一
        # 交易日已经落库的最近报价补齐样本，但持仓决策仍只认本轮实际返回
        # 的 quote_tickers，避免把缓存价误当成新动作依据。
        try:
            cached_quote_rows = list(quote_collection.find(
                {"ticker": {"$in": list(tickers)}},
                {"_id": 0},
            ))
        except Exception:  # noqa: BLE001 - 测试桩或空集合安全降级
            cached_quote_rows = []
        quote_documents = {
            str(item.get("ticker") or "").upper(): _clean_document(item)
            for item in cached_quote_rows
            if item.get("ticker")
            and str(item.get("quote_at") or item.get("update_at") or "")[:10].replace("-", "") == trade_date
        }
        quote_documents.update({
            str(item.get("ticker") or "").upper(): _clean_document(item)
            for item in quote_records
            if item.get("ticker")
        })
        # 板块宽度优先消费 KlineShare trend 的官方日内均价；未覆盖到的股票
        # 使用同一实时报价中的成交额/成交量计算真实日内均价。单位比例异常
        # 会被 intraday_average_from_quote 拒绝，不用现价冒充均价。
        sector_trends = {
            ticker: {
                "avg_price": average,
                "source": "realtime_turnover_vwap",
            }
            for ticker, quote in quote_documents.items()
            if (average := intraday_average_from_quote(quote)) is not None
        }
        sector_trends.update(trend_documents)
        sector_facts = aggregate_sector_resonance(
            pool.sector_samples,
            quotes=quote_documents,
            trends=sector_trends,
            trade_date=trade_date,
            computed_at=current,
        )
        sector_collection = self.store.collection(SECTOR_FACT_COLLECTION)
        sector_snapshot_collection = self.store.collection(SECTOR_FACT_SNAPSHOT_COLLECTION)
        for fact in sector_facts:
            sector_collection.update_one(
                {"trade_date": trade_date, "sector_name": fact["sector_name"]},
                {"$set": fact},
                upsert=True,
            )
            snapshot_id = (
                f"{trade_date}:{fact['sector_name']}:"
                f"{current.astimezone(MARKET_TIMEZONE).strftime('%Y%m%dT%H%M%S%z')}"
            )
            sector_snapshot_collection.update_one(
                {"snapshot_id": snapshot_id},
                {"$set": {**fact, "snapshot_id": snapshot_id}},
                upsert=True,
            )

        price_limit_fact_count = 0
        limit_collection = self.store.collection("daily_price_limits")
        limit_finder = getattr(limit_collection, "find_one", None)
        price_fact_collection = self.store.collection(PRICE_LIMIT_FACT_COLLECTION)
        if callable(limit_finder):
            for ticker, quote in quote_documents.items():
                if ticker in DEFAULT_BENCHMARKS:
                    continue
                limit_document = limit_finder(
                    {"trade_date": trade_date, "ticker": ticker},
                    {"_id": 0},
                )
                if not limit_document:
                    continue
                fact = build_price_limit_facts(
                    trade_date=trade_date,
                    ticker=ticker,
                    limit_document=limit_document,
                    quote=quote,
                    closed_5m_bars=closed_bars_by_ticker.get(ticker, []),
                    computed_at=current,
                )
                if fact is None:
                    continue
                price_fact_collection.update_one(
                    {"trade_date": trade_date, "ticker": ticker},
                    {"$set": fact},
                    upsert=True,
                )
                price_limit_fact_count += 1

        decision_observed_at = current if explicit_now else datetime.now(tz=MARKET_TIMEZONE)
        latency_sample_count = record_quote_latency_samples(
            self.store.collection(LATENCY_COLLECTION),
            [_clean_document(item) for item in quote_records],
            request_latency_ms=request_latency_ms,
            calibration_eligible=not force,
            decision_at=decision_observed_at,
        )

        decision_count = 0
        decision_failed: list[str] = []
        if self.decision_refresher is not None:
            for ticker in holding_tickers:
                if ticker not in quote_tickers or ticker not in minute_tickers:
                    continue
                try:
                    decision_now = current if explicit_now else datetime.now(tz=MARKET_TIMEZONE)
                    self.decision_refresher(ticker, decision_now)
                    decision_count += 1
                except Exception:  # noqa: BLE001 - 记录决策失败，但不篡改行情采集事实
                    decision_failed.append(ticker)

        scenario_decision_count = 0
        scenario_failed: list[str] = []
        if self.scenario_refresher is not None:
            try:
                scenario_now = current if explicit_now else datetime.now(tz=MARKET_TIMEZONE)
                scenario_result = self.scenario_refresher(scenario_now) or {}
                scenario_decision_count = int(scenario_result.get("decision_count") or 0)
                scenario_failed.extend(str(item) for item in (scenario_result.get("failed_tickers") or []))
            except Exception:  # noqa: BLE001 - 行情事实已落库；全场景桥失败单独记账
                scenario_failed.append("__scenario_refresh__")

        failed_unique = tuple(dict.fromkeys(failed))
        decision_failed_unique = tuple(dict.fromkeys(decision_failed))
        trend_failed_unique = tuple(dict.fromkeys(trend_failed))
        scenario_failed_unique = tuple(dict.fromkeys(scenario_failed))
        status = (
            "success"
            if not failed_unique and not decision_failed_unique and not trend_failed_unique and not scenario_failed_unique
            else "partial"
        )
        result = TradingIntradaySyncResult(
            status=status,
            trade_date=trade_date,
            ticker_count=len(tickers),
            quote_count=quote_count,
            minute_count=minute_count,
            failed_tickers=failed_unique,
            message=(
                f"quotes={quote_count}, minute_tickers={minute_count}, trends={trend_count}, "
                f"sector_facts={len(sector_facts)}, price_limit_facts={price_limit_fact_count}, "
                f"t1_settled={len(t1_settled)}, expired_orders={len(expired_order_ids)}, "
                f"decisions={decision_count}, scenario_decisions={scenario_decision_count}"
            ),
            decision_count=decision_count,
            decision_failed_tickers=decision_failed_unique,
            latency_sample_count=latency_sample_count,
            trend_count=trend_count,
            trend_failed_tickers=trend_failed_unique,
            sector_fact_count=len(sector_facts),
            price_limit_fact_count=price_limit_fact_count,
            t1_settled_count=len(t1_settled),
            expired_order_count=len(expired_order_ids),
            expiry_failed_order_ids=expiry_failed_order_ids,
            scenario_decision_count=scenario_decision_count,
            scenario_failed_tickers=scenario_failed_unique,
        )
        updater = getattr(self.store, "upsert_sync_state", None)
        if callable(updater):
            updater({
                "dataset": "trading_intraday_facts",
                "scope": trade_date,
                "status": status,
                "ticker_count": len(tickers),
                "quote_count": quote_count,
                "minute_count": minute_count,
                "failed_tickers": list(failed_unique),
                "decision_count": decision_count,
                "decision_failed_tickers": list(decision_failed_unique),
                "scenario_decision_count": scenario_decision_count,
                "scenario_failed_tickers": list(scenario_failed_unique),
                "latency_sample_count": latency_sample_count,
                "trend_count": trend_count,
                "trend_failed_tickers": list(trend_failed_unique),
                "sector_fact_count": len(sector_facts),
                "price_limit_fact_count": price_limit_fact_count,
                "t1_settled_count": len(t1_settled),
                "updated_at": current.isoformat(timespec="seconds"),
            })
        return result


def _build_market_dependencies() -> tuple[Any, KlineShareClient]:
    from shilun.common.config import load_config
    from shilun.common.db import MongoSnapshotStore
    from shilun.data.providers.klineshare_provider import KlineShareConfig

    config = load_config()
    if not config.mongo_uri:
        raise RuntimeError("SHILUN_MONGO_URI 未配置")
    if not config.klineshare_api_key:
        raise RuntimeError("SHILUN_KLINESHARE_API_KEY 未配置")
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    client = KlineShareClient(KlineShareConfig(
        api_key=config.klineshare_api_key,
        base_url=config.klineshare_base_url,
        timeout=config.klineshare_timeout,
    ))
    return store, client


def _build_quote_job() -> tuple[TradingIntradaySyncJob, Any]:
    store, client = _build_market_dependencies()
    from shilun.data.providers.tushare_realtime import TushareRealtimeClient

    return TradingIntradaySyncJob(
        store,
        client,
        index_quote_client=TushareRealtimeClient(),
    ), store


def _build_job() -> tuple[TradingIntradaySyncJob, Any]:
    store, client = _build_market_dependencies()
    from shilun.data.providers.minute_provider import MinuteClient
    from shilun.data.providers.tushare_realtime import TushareRealtimeClient
    from shilun.trading.execution_production import FullScenarioProductionService
    from shilun.trading.service import TradingDecisionService

    decision_service = TradingDecisionService(store)
    scenario_service = FullScenarioProductionService(store)
    return TradingIntradaySyncJob(
        store,
        client,
        index_quote_client=TushareRealtimeClient(),
        index_minute_client=MinuteClient(),
        decision_refresher=lambda ticker, now: decision_service.refresh(ticker, now=now),
        scenario_refresher=lambda now: scenario_service.refresh_all(now=now),
    ), store


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Sync execution watch pool and benchmark intraday facts")
    parser.add_argument("--force", action="store_true", help="允许在非盘中时段执行一次")
    args = parser.parse_args()
    job, store = _build_job()
    try:
        print(job.run(force=args.force), flush=True)
    finally:
        store.close()


def scheduler_main() -> None:
    """报价每分钟快采，慢事实独立采集，并固化 Job 12J 检查点。"""

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    def quote_once() -> None:
        try:
            job, store = _build_quote_job()
        except RuntimeError as error:
            print(f"trading_fast_quote skipped: {error}", flush=True)
            return
        try:
            result = job.run_quotes_only()
            if result.status != "skipped":
                print(result, flush=True)
        finally:
            store.close()

    def sync_once() -> None:
        try:
            job, store = _build_job()
        except RuntimeError as error:
            print(f"trading_intraday_sync skipped: {error}", flush=True)
            return
        try:
            result = job.run()
            if result.status != "skipped":
                print(result, flush=True)
        finally:
            store.close()
        if result.status != "skipped":
            try:
                from shilun.common.config import load_config
                from shilun.jobs.intraday_notification_job import run_once as run_notification_once

                if load_config().intraday_notifications_enabled:
                    print(run_notification_once(), flush=True)
            except Exception as error:  # noqa: BLE001 - notification must not kill market polling
                print(f"intraday_notification failed: {error}", flush=True)

    def observe_once() -> None:
        """只读取已落库事实；不补行情、不改阈值、不触发交易动作。"""

        from shilun.jobs.trading_live_observation_job import TradingLiveObservationJob

        try:
            job = TradingLiveObservationJob()
        except (RuntimeError, ValueError) as error:
            print(f"trading_live_observation skipped: {error}", flush=True)
            return
        try:
            report = job.run(persist=True)
            print(
                "trading_live_observation "
                f"status={report['status']} report_id={report['report_id']}",
                flush=True,
            )
        finally:
            job.close()

    def materialize_replays_once() -> None:
        """收盘后把当日冻结计划与生产快照幂等物化为 Job 12H 回放。"""

        from shilun.jobs.trading_replay_materialization_job import (
            TradingReplayMaterializationJob,
        )

        try:
            job = TradingReplayMaterializationJob()
        except (RuntimeError, ValueError) as error:
            print(f"trading_replay_materialization skipped: {error}", flush=True)
            return
        try:
            report = job.run(persist=True)
            print(
                "trading_replay_materialization "
                f"status={report['status']} "
                f"replays={report['materialized_replay_count']} "
                f"audit_eligible={report['audit_eligible_replay_count']}",
                flush=True,
            )
        finally:
            job.close()

    scheduler.add_job(
        quote_once,
        CronTrigger(day_of_week="mon-fri", minute="*", second=2, timezone="Asia/Shanghai"),
        id="trading_fast_quote_every_minute",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=50,
    )
    scheduler.add_job(
        sync_once,
        CronTrigger(day_of_week="mon-fri", minute="*", second=12, timezone="Asia/Shanghai"),
        id="trading_intraday_sync_every_minute",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=50,
    )
    scheduler.add_job(
        observe_once,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=36, second=20, timezone="Asia/Shanghai"),
        id="trading_live_observation_morning_open",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        observe_once,
        CronTrigger(day_of_week="mon-fri", hour=11, minute=36, second=20, timezone="Asia/Shanghai"),
        id="trading_live_observation_morning_close",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        observe_once,
        CronTrigger(day_of_week="mon-fri", hour=13, minute=6, second=20, timezone="Asia/Shanghai"),
        id="trading_live_observation_afternoon_open",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        observe_once,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=6, second=20, timezone="Asia/Shanghai"),
        id="trading_live_observation_market_close",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        materialize_replays_once,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=10, second=20, timezone="Asia/Shanghai"),
        id="trading_replay_materialization_market_close",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=900,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
