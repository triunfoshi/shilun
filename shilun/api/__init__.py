"""FastAPI application, HTTP routes, and Telegram client.

M5 simplification note:
The API layer had a tiny app factory, two small route modules, and a small
Telegram client module. They are now a single boundary so runtime entry remains
`shilun.api:app` and route tests can still import the old paths through aliases.
"""

from __future__ import annotations

import re
import logging
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, NoReturn

import requests
from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

from shilun.common.config import load_config
from shilun.market import (
    BENCHMARK_INDEX_OPTIONS,
    DEFAULT_BENCHMARK_TICKER,
    MarketPart1Request,
    PART1_ENGINE_VERSION,
    SectorTrendRequest,
    evaluate_daily_leaders,
    evaluate_market_permission,
    evaluate_sector_trends,
)
from shilun.services import AnalyzeRequest, MongoFirstAnalysisService


_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_INDEX_HTML_PATH = _STATIC_DIR / "index.html"

analysis_service = MongoFirstAnalysisService()
analyze_router = APIRouter()
telegram_router = APIRouter()
ui_router = APIRouter()
router = telegram_router


class TelegramBotClient:
    def __init__(self, bot_token: str, api_base: str = "https://api.telegram.org", timeout: int = 10) -> None:
        self.bot_token = bot_token
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        return self._post("sendMessage", {"chat_id": chat_id, "text": text})

    def set_webhook(self, webhook_url: str, secret_token: str | None = None, drop_pending_updates: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "url": webhook_url,
            "drop_pending_updates": drop_pending_updates,
        }
        if secret_token:
            payload["secret_token"] = secret_token
        return self._post("setWebhook", payload)

    def delete_webhook(self, drop_pending_updates: bool = False) -> dict[str, Any]:
        return self._post("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    def get_me(self) -> dict[str, Any]:
        return self._get("getMe")

    def get_updates(self, *, limit: int = 20, timeout: int = 0) -> dict[str, Any]:
        return self._get("getUpdates", {"limit": limit, "timeout": timeout})

    def _get(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.get(self._url(method), params=params, timeout=self.timeout)
        return self._handle_response(response)

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(self._url(method), json=payload, timeout=self.timeout)
        return self._handle_response(response)

    def _url(self, method: str) -> str:
        return f"{self.api_base}/bot{self.bot_token}/{method}"

    @staticmethod
    def _handle_response(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError(f"Telegram API returned non-JSON response: {response.text}") from error
        if not response.ok or not payload.get("ok", False):
            raise RuntimeError(payload.get("description") or f"Telegram API request failed with status {response.status_code}")
        return payload


class TelegramChat(BaseModel):
    id: int


class TelegramMessage(BaseModel):
    message_id: int | None = None
    text: str | None = None
    chat: TelegramChat


class TelegramUpdate(BaseModel):
    update_id: int | None = None
    message: TelegramMessage | None = None


class TelegramWebhookSetupRequest(BaseModel):
    public_base_url: str | None = Field(default=None, description="Public HTTPS base URL, e.g. https://your-domain.com")
    drop_pending_updates: bool = False


class DailyPushHttpRequest(BaseModel):
    target_date: str | None = None
    dry_run: bool = True
    message_top_k: int = 20
    candidate_pool_top_k: int = 10
    include_candidate_pool: bool = True
    allow_snapshot_fallback: bool = False
    fallback_latest_local: bool = True


class DataSyncHttpRequest(BaseModel):
    target_date: str | None = None
    mode: str = Field(
        default="latest",
        description="latest / incremental / history_year / index_history_year",
    )
    force: bool = False
    benchmark_ticker: str | None = DEFAULT_BENCHMARK_TICKER
    incremental_lookback_days: int = 14
    incremental_overlap_days: int = 3


class DataDomainSyncHttpRequest(BaseModel):
    target_date: str | None = None
    dataset: str = Field(
        default="all",
        description="all / limit_up_pool / concept_boards / north_capital_flow",
    )


class HoldingCreateHttpRequest(BaseModel):
    ticker: str = Field(min_length=1)
    entry_date: str = Field(min_length=1, description="YYYY-MM-DD")
    entry_price: float = Field(gt=0)
    entry_signal: str = Field(min_length=1)
    entry_size: float = Field(gt=0, le=1)
    quantity: int | None = Field(default=None, gt=0)
    available_quantity: int | None = Field(default=None, ge=0)
    cost_basis: float | None = Field(default=None, gt=0)
    realized_loss_amount: float = Field(default=0, ge=0)
    strategy_id: str | None = None
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    proposed_plan: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None
    sector_name: str | None = None
    target_price: float | None = Field(default=None, gt=0)
    stop_loss_1: float | None = Field(default=None, gt=0)
    stop_loss_2: float | None = Field(default=None, gt=0)
    stop_loss_mode: str | None = None
    stop_loss_plan: dict[str, Any] = Field(default_factory=dict)
    stop_loss_confirmed_at: str | None = None
    breakdown_level: float | None = Field(default=None, gt=0)
    add_point: float | None = Field(default=None, gt=0)
    signal_candle_open: float | None = Field(default=None, gt=0)
    invalid_conditions: list[str] = Field(default_factory=list)
    note: str | None = None


class HoldingPatchHttpRequest(BaseModel):
    name: str | None = None
    sector_name: str | None = None
    entry_price: float | None = Field(default=None, gt=0)
    manual_current_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    stop_loss_1: float | None = Field(default=None, gt=0)
    stop_loss_2: float | None = Field(default=None, gt=0)
    stop_loss_mode: str | None = None
    stop_loss_plan: dict[str, Any] | None = None
    stop_loss_confirmed_at: str | None = None
    breakdown_level: float | None = Field(default=None, gt=0)
    add_point: float | None = Field(default=None, gt=0)
    signal_candle_open: float | None = Field(default=None, gt=0)
    invalid_conditions: list[str] | None = None
    note: str | None = None
    realized_size: float | None = Field(default=None, ge=0, le=1)
    quantity: int | None = Field(default=None, gt=0)
    available_quantity: int | None = Field(default=None, ge=0)
    cost_basis: float | None = Field(default=None, gt=0)
    realized_loss_amount: float | None = Field(default=None, ge=0)


class HoldingCloseHttpRequest(BaseModel):
    exit_date: str | None = Field(default=None, description="YYYY-MM-DD")
    exit_price: float | None = Field(default=None, gt=0)
    reason: str | None = None


class TradeExecutionHttpRequest(BaseModel):
    side: str = "sell"
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    execution_id: str | None = None
    executed_at: str | None = None
    fees: float = Field(default=0, ge=0)
    note: str | None = None


class TradeOrderHttpRequest(BaseModel):
    plan_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    side: str
    quantity: int = Field(gt=0)
    limit_price: float = Field(gt=0)
    intent: str
    order_id: str | None = None
    scenario_id: str | None = None
    placed_at: str | None = None
    expires_at: str | None = None
    name: str | None = None
    sector_name: str | None = None
    strategy_id: str | None = None
    strategy_version: str | None = None
    note: str | None = None


class TradeFillHttpRequest(BaseModel):
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    execution_id: str | None = None
    executed_at: str | None = None
    fees: float = Field(default=0, ge=0)
    note: str | None = None


class TradeOrderCancelHttpRequest(BaseModel):
    cancelled_at: str | None = None
    reason: str | None = None


class T1SettlementHttpRequest(BaseModel):
    as_of_date: str = Field(min_length=10, max_length=10)
    settled_at: str | None = None


class AccountRiskProfileHttpRequest(BaseModel):
    total_assets: float = Field(gt=0)
    available_cash: float | None = Field(default=None, ge=0)
    max_loss_per_trade: float = Field(gt=0)
    max_cash_per_trade: float | None = Field(default=None, gt=0)
    commission_rate: float | None = Field(default=None, ge=0)
    min_commission: float | None = Field(default=None, ge=0)
    stamp_duty_rate: float | None = Field(default=None, ge=0)
    slippage_bps: float | None = Field(default=None, ge=0)
    conservative_fee_buffer_rate: float = Field(default=0.002, ge=0)
    auto_trade_enabled: bool = False


class IntradayWatchlistItemHttpRequest(BaseModel):
    ticker: str = Field(min_length=1)
    name: str | None = None
    sector_name: str | None = None
    source: str | None = None
    feishu_enabled: bool = True


class IntradayWatchlistReplaceHttpRequest(BaseModel):
    items: list[IntradayWatchlistItemHttpRequest] = Field(default_factory=list)


def _model_payload(model: BaseModel, *, exclude_unset: bool = False) -> dict[str, Any]:
    model_dump = getattr(model, "model_dump", None)
    if callable(model_dump):
        return model_dump(exclude_unset=exclude_unset)
    return model.dict(exclude_unset=exclude_unset)


def _normalize_holding_ticker(ticker: str) -> str:
    normalized = (ticker or "").strip().upper()
    if not normalized:
        raise HTTPException(status_code=400, detail="股票代码 ticker 不能为空。")
    return normalized


def _normalize_holding_date(value: str, field_name: str) -> str:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} 必须使用 YYYY-MM-DD 格式，收到 {value!r}。",
        ) from error


def _open_holdings_store(*, read_only: bool = False) -> Any:
    from shilun.common.db import MongoSnapshotStore

    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，持仓管理需要连接 Mongo。")
    try:
        return MongoSnapshotStore(config.mongo_uri, config.mongo_db,
                                  socket_timeout_ms=30000, initialize_indexes=not read_only)
    except PyMongoError as error:
        logging.getLogger(__name__).warning("Holdings database connection failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="云端数据库暂不可用，请检查网络后重试。") from error


def _strategy_daily_bars(
    store: Any,
    ticker: str,
    *,
    as_of: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """读取截至 as_of 的前复权日线，并返回实际最新数据日。

    Mongo 日线保留真实成交价，遇到送转/除权会出现价格断层，不能直接用于均线、
    ATR 和结构位。配置 KlineShare 时优先读取 forward 前复权日 K；只有不存在
    明显断层时才允许回退 Mongo 原始日线。
    """

    anchor = datetime.strptime(as_of, "%Y-%m-%d") if as_of else datetime.now()
    # 日线同步只会写入已完成的 K 线。以“是否已落库”为完成口径，不能机械减一天，
    # 否则收盘后同步了当日数据，止损仍会永久慢一个交易日。
    end = anchor
    start = end - timedelta(days=240)
    raw_rows = store.raw_market.find_daily_bars(
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        tickers=[ticker],
    )
    rows: list[dict[str, Any]] = []
    config = load_config()
    if config.klineshare_api_key:
        try:
            from shilun.data.providers.klineshare_provider import KlineShareClient, KlineShareConfig

            client = KlineShareClient(KlineShareConfig(
                api_key=config.klineshare_api_key,
                base_url=config.klineshare_base_url,
                timeout=config.klineshare_timeout,
            ))
            frame = client.fetch_kline(
                ticker,
                period=86_400,
                count=320,
                adjust_type="forward",
            )
            for record in frame.to_dict("records") if frame is not None and not frame.empty else []:
                value = record.get("datetime")
                date_text = value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else str(value)[:10]
                if start.strftime("%Y-%m-%d") <= date_text <= end.strftime("%Y-%m-%d"):
                    rows.append({
                        **record,
                        "ticker": ticker,
                        "date": date_text,
                        "price_adjustment": "forward",
                        "daily_bar_source": "klineshare",
                    })
        except Exception:  # noqa: BLE001 - 后续断层门会阻止错误回退
            rows = []
    if not rows:
        ordered_raw = sorted(raw_rows, key=lambda item: str(item.get("date") or item.get("trade_date") or ""))
        closes = [float(item.get("close") or 0) for item in ordered_raw]
        has_discontinuity = any(
            previous > 0 and current > 0 and abs(current / previous - 1.0) > 0.22
            for previous, current in zip(closes, closes[1:])
        )
        if has_discontinuity:
            raise HTTPException(
                status_code=409,
                detail=f"{ticker} 日线存在疑似除权断层，前复权数据不可用，已阻止错误计算 MA8/ATR。",
            )
        rows = [
            {**record, "price_adjustment": "none", "daily_bar_source": "mongo"}
            for record in ordered_raw
        ]
    completed_dates = [
        str(row.get("date") or row.get("trade_date") or "")[:10]
        for row in rows
        if row.get("date") or row.get("trade_date")
    ]
    completed_through = max(completed_dates) if completed_dates else end.strftime("%Y-%m-%d")
    return rows, completed_through


def _build_stop_plan_for_strategy(
    store: Any,
    *,
    strategy_id: str,
    ticker: str,
    reference_price: float,
    as_of: str | None = None,
    entry_date: str | None = None,
) -> dict[str, Any]:
    from shilun.trading.stop_plans import StopPlanError, build_strategy_stop_plan

    normalized_strategy = str(strategy_id or "").strip().lower()
    normalized_entry_date = str(entry_date or as_of or "")[:10] or None
    daily_as_of = as_of
    stabilization_low: float | None = None
    if normalized_strategy == "e7_oversold_overnight":
        if not normalized_entry_date:
            raise HTTPException(status_code=400, detail="E7 止损必须提供建仓日期。")
        try:
            entry_day = datetime.strptime(normalized_entry_date, "%Y-%m-%d").date()
        except ValueError as error:
            raise HTTPException(status_code=400, detail="E7 建仓日期必须使用 YYYY-MM-DD。") from error
        daily_as_of = (entry_day - timedelta(days=1)).isoformat()
        minute_doc = store.collection("minute_bars_daily").find_one(
            {
                "ticker": ticker,
                "trade_date": normalized_entry_date.replace("-", ""),
                "interval": "5",
            },
            {"_id": 0, "bars": 1},
        ) or {}
        tail_lows: list[float] = []
        for bar in minute_doc.get("bars") or []:
            stamp = str(bar.get("datetime") or "").replace(" ", "T")
            clock = stamp.split("T", 1)[1][:5] if "T" in stamp else ""
            try:
                low = float(bar.get("low") or 0)
            except (TypeError, ValueError):
                low = 0.0
            if clock >= "14:00" and low > 0:
                tail_lows.append(low)
        if not tail_lows:
            raise HTTPException(
                status_code=409,
                detail=f"{ticker} 在 {normalized_entry_date} 缺少 14:00 后 5 分钟 K，不能伪造 E7 企稳止损。",
            )
        stabilization_low = min(tail_lows)

    rows, completed_through = _strategy_daily_bars(store, ticker, as_of=daily_as_of)
    try:
        plan = build_strategy_stop_plan(
            strategy_id,
            rows,
            reference_price=reference_price,
            as_of=normalized_entry_date if normalized_strategy == "e7_oversold_overnight" else completed_through,
            stabilization_low=stabilization_low,
        )
    except StopPlanError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        **plan,
        "ticker": ticker,
        "daily_bar_count": len(rows),
        "completed_through": completed_through,
        "price_adjustment": rows[-1].get("price_adjustment") if rows else None,
        "daily_bar_source": rows[-1].get("daily_bar_source") if rows else None,
        "entry_date": normalized_entry_date if normalized_strategy == "e7_oversold_overnight" else None,
    }


def _holding_stop_reference(store: Any, holding: dict[str, Any]) -> float:
    """已有持仓优先使用最新可信报价；缺失时才退回成本/入场价。"""

    quote = store.collection("trading_realtime_quotes").find_one(
        {"ticker": holding.get("ticker")},
        {"_id": 0, "current": 1},
    )
    value = (quote or {}).get("current") or holding.get("cost_basis") or holding.get("entry_price")
    try:
        reference = float(value)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="持仓缺少当前价、成本价和入场价，无法生成止损") from error
    if reference <= 0:
        raise HTTPException(status_code=400, detail="止损参考价必须大于 0")
    return reference


def _holding_stop_strategy_context(
    store: Any,
    holding: dict[str, Any],
) -> tuple[str, str, float]:
    """Resolve the authoritative strategy and price basis for a holding stop.

    E7 is an entry-scene strategy: its stop must always be calculated from the
    actual entry/cost price.  Using a later quote can incorrectly reject the
    historical stabilization low once the stock has already pulled back.
    """

    from shilun.strategies import (
        E7_OVERSOLD_OVERNIGHT_STRATEGY_ID,
        ExecutionStrategyError,
        resolve_strategy_binding,
        scenario_code,
    )

    entry_signal = holding.get("entry_signal")
    requested_strategy = holding.get("strategy_id")
    # Old E7 holdings were sometimes persisted with the frontend's MA5 default.
    # Let the entry scene repair that binding instead of reproducing the error.
    if scenario_code(entry_signal) == "E7":
        requested_strategy = None
    try:
        strategy_id, strategy_version = resolve_strategy_binding(
            entry_signal,
            requested_strategy,
            holding.get("strategy_version"),
        )
    except ExecutionStrategyError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if strategy_id != E7_OVERSOLD_OVERNIGHT_STRATEGY_ID:
        return strategy_id, strategy_version, _holding_stop_reference(store, holding)

    value = holding.get("cost_basis") or holding.get("entry_price")
    try:
        reference = float(value)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="E7 持仓缺少成本价/入场价，无法生成止损") from error
    if reference <= 0:
        raise HTTPException(status_code=400, detail="E7 成本价/入场价必须大于 0")
    return strategy_id, strategy_version, reference


LEGACY_HOLDING_STRATEGY_IDS = {"ma5_multi_scenario_execution", "ma5_execution_v0_3"}


def _holding_market_snapshot(store: Any, ticker: str) -> dict[str, Any]:
    """读取持仓最新可信行情；实时快照优先，缺失时退回最新日线收盘。"""

    normalized = _normalize_holding_ticker(ticker)
    quote = store.collection("trading_realtime_quotes").find_one(
        {"ticker": normalized},
        {"_id": 0},
    ) or {}
    try:
        current = float(quote.get("current") or 0)
    except (TypeError, ValueError):
        current = 0.0
    if current > 0:
        return {
            "current_price": round(current, 4),
            "current_price_as_of": quote.get("quote_at") or quote.get("update_at") or quote.get("fetched_at"),
            "current_price_source": quote.get("source") or "trading_realtime_quotes",
            "current_price_kind": "realtime",
        }

    end = datetime.now().date()
    rows = store.raw_market.find_daily_bars(
        start_date=(end - timedelta(days=45)).isoformat(),
        end_date=end.isoformat(),
        tickers=[normalized],
    )
    ordered = sorted(rows, key=lambda item: str(item.get("date") or item.get("trade_date") or ""))
    latest = ordered[-1] if ordered else {}
    try:
        close = float(latest.get("close") or 0)
    except (TypeError, ValueError):
        close = 0.0
    return {
        "current_price": round(close, 4) if close > 0 else None,
        "current_price_as_of": str(latest.get("date") or latest.get("trade_date") or "")[:10] or None,
        "current_price_source": "market_daily_bars" if close > 0 else None,
        "current_price_kind": "daily_close" if close > 0 else "missing",
    }


def _build_holding_target_plan(
    store: Any,
    *,
    ticker: str,
    as_of: str | None = None,
) -> dict[str, Any]:
    """复用 MA5 战法的已完成日线目标价，避免持仓页另造一套算法。"""

    from shilun.strategies import ExecutionStrategyContext, get_execution_strategy

    rows, completed_through = _strategy_daily_bars(store, ticker, as_of=as_of)
    evaluation = get_execution_strategy("ma5_v0_2").evaluate_stock(
        rows,
        context=ExecutionStrategyContext(analysis_date=completed_through),
    )
    trade_plan = dict(evaluation.get("trade_plan") or {})
    try:
        target_price = float(trade_plan.get("target_price") or 0)
    except (TypeError, ValueError):
        target_price = 0.0
    if target_price <= 0:
        raise HTTPException(status_code=400, detail=f"{ticker} 已完成日线不足，暂时无法自动生成目标价。")
    return {
        "target_price": round(target_price, 4),
        "target_source": trade_plan.get("target_source") or "ma5_v0_2",
        "completed_through": completed_through,
        "trade_plan": trade_plan,
    }


def _refresh_holding_derived_plan(
    store: Any,
    ticker: str,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """用最新行情补齐持仓目标/止损，并迁移旧的不可日检策略 ID。"""

    from shilun.market.holdings import HoldingsError, get_holding, patch_holding
    from shilun.strategies import (
        E7_OVERSOLD_OVERNIGHT_STRATEGY_ID,
        get_execution_strategy,
        scenario_code,
    )

    normalized = _normalize_holding_ticker(ticker)
    collection = store.collection("holdings")
    holding = get_holding(collection, normalized)
    if holding is None:
        raise HTTPException(status_code=404, detail=f"{normalized} 持仓不存在")
    raw_strategy_id = str(holding.get("strategy_id") or "ma5_v0_2").strip().lower()
    if scenario_code(holding.get("entry_signal")) == "E7":
        strategy_id = E7_OVERSOLD_OVERNIGHT_STRATEGY_ID
    else:
        strategy_id = "ma5_v0_2" if raw_strategy_id in LEGACY_HOLDING_STRATEGY_IDS else raw_strategy_id
    try:
        strategy = get_execution_strategy(strategy_id)
    except Exception:
        strategy = get_execution_strategy("ma5_v0_2")
        strategy_id = strategy.strategy_id
    snapshot = _holding_market_snapshot(store, normalized)
    reference_price = float(
        (
            holding.get("cost_basis") or holding.get("entry_price")
            if strategy_id == E7_OVERSOLD_OVERNIGHT_STRATEGY_ID
            else snapshot.get("current_price") or holding.get("cost_basis") or holding.get("entry_price")
        )
        or 0
    )
    if reference_price <= 0:
        raise HTTPException(status_code=400, detail=f"{normalized} 缺少有效行情与成本价，无法生成策略价位。")
    as_of = str(snapshot.get("current_price_as_of") or holding.get("entry_date") or "")[:10] or None
    if strategy_id == E7_OVERSOLD_OVERNIGHT_STRATEGY_ID:
        target_plan = {
            "target_price": None,
            "target_source": "E7 次一交易日强制退出",
            "completed_through": as_of,
            "trade_plan": {
                "target_type": "time_exit",
                "exit_window": "09:30–10:00",
                "exit_deadline": "次一交易日 10:00",
            },
        }
    else:
        target_plan = _build_holding_target_plan(store, ticker=normalized, as_of=as_of)
    stop_plan = _build_stop_plan_for_strategy(
        store,
        strategy_id=strategy_id,
        ticker=normalized,
        reference_price=reference_price,
        as_of=as_of,
        entry_date=holding.get("entry_date"),
    )
    confirmed_at = datetime.now().isoformat(timespec="seconds")
    confirmed_stop_plan = {**stop_plan, "status": "confirmed", "confirmed_at": confirmed_at}
    updates: dict[str, Any] = {}

    def same_price(left: Any, right: Any) -> bool:
        try:
            return abs(float(left) - float(right)) < 1e-6
        except (TypeError, ValueError):
            return left is None and right is None

    target_needs_update = (
        strategy_id == E7_OVERSOLD_OVERNIGHT_STRATEGY_ID
        and holding.get("target_price") is not None
    ) or (
        strategy_id != E7_OVERSOLD_OVERNIGHT_STRATEGY_ID
        and (
            not holding.get("target_price")
            or (overwrite and not same_price(holding.get("target_price"), target_plan["target_price"]))
        )
    )
    if target_needs_update:
        updates["target_price"] = target_plan["target_price"]
    existing_stop_plan = dict(holding.get("stop_loss_plan") or {})
    stop_needs_update = not holding.get("stop_loss_1") or (
        overwrite and (
            not same_price(holding.get("stop_loss_1"), stop_plan["recommended_stop"])
            or not same_price(holding.get("stop_loss_2"), stop_plan.get("secondary_stop"))
            or holding.get("stop_loss_mode") != "strategy"
            or existing_stop_plan.get("completed_through") != stop_plan.get("completed_through")
        )
    )
    if stop_needs_update:
        updates.update({
            "stop_loss_1": stop_plan["recommended_stop"],
            "stop_loss_2": stop_plan.get("secondary_stop"),
            "stop_loss_mode": "strategy",
            "stop_loss_plan": confirmed_stop_plan,
            "stop_loss_confirmed_at": confirmed_at,
        })
    if overwrite and holding.get("manual_current_price") is not None:
        updates.update({"manual_current_price": None, "manual_current_price_at": None})
    current_plan = {
        **dict(holding.get("current_plan") or {}),
        "target_price": updates.get("target_price", holding.get("target_price")),
        "target_source": target_plan.get("target_source"),
        "stop_loss_1": updates.get("stop_loss_1", holding.get("stop_loss_1")),
        "stop_loss_2": updates.get("stop_loss_2", holding.get("stop_loss_2")),
        "stop_loss_mode": updates.get("stop_loss_mode", holding.get("stop_loss_mode")),
        "market_reference_price": round(reference_price, 4),
        "market_reference_at": snapshot.get("current_price_as_of"),
        "auto_plan_completed_through": target_plan.get("completed_through"),
        **dict(target_plan.get("trade_plan") or {}),
    }
    updates["current_plan"] = current_plan
    if raw_strategy_id != strategy_id:
        collection.update_one(
            {"ticker": normalized},
            {"$set": {"strategy_id": strategy_id, "strategy_version": strategy.version}},
        )
    try:
        refreshed = patch_holding(
            collection,
            normalized,
            updates,
            position_event_collection=store.collection("holding_position_events"),
            source="automatic_market_plan",
        )
    except HoldingsError as error:
        _raise_holdings_http(error)
    return {
        "status": "ok",
        "holding": {**refreshed, **snapshot},
        "market": snapshot,
        "target_plan": target_plan,
        "stop_plan": confirmed_stop_plan,
        "strategy_migrated_from": raw_strategy_id if raw_strategy_id != strategy_id else None,
    }


def _raise_holdings_http(error: Exception) -> NoReturn:
    status_code = 404 if "不存在" in str(error) else 400
    raise HTTPException(status_code=status_code, detail=str(error)) from error


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 12:
        return value[:2] + "***"
    return value[:8] + "..." + value[-6:]


@ui_router.get("/", response_class=HTMLResponse)
@ui_router.get("/ui", response_class=HTMLResponse)
def control_center() -> HTMLResponse:
    options = "\n".join(
        (
            f'<option value="{item["ticker"]}"{" selected" if item["ticker"] == DEFAULT_BENCHMARK_TICKER else ""}>'
            f'{item["name"]} · {item["ticker"]}</option>'
        )
        for item in BENCHMARK_INDEX_OPTIONS
    )
    template = _INDEX_HTML_PATH.read_text(encoding="utf-8")
    return HTMLResponse(
        template
        .replace("__DEFAULT_DATE__", default_analysis_date())
        .replace("__BENCHMARK_OPTIONS__", options)
    )


@ui_router.get("/api/v1/push-channel/status")
def push_channel_status() -> dict[str, Any]:
    config = load_config()
    return {
        "mongo_configured": bool(config.mongo_uri),
        "feishu_configured": bool(config.feishu_webhook_url),
        "feishu_webhook": _mask_secret(config.feishu_webhook_url),
        "telegram_bot_configured": bool(config.telegram_bot_token),
        "telegram_push_chat_ids": config.telegram_push_chat_ids,
        "telegram_webhook_base_url": config.telegram_webhook_base_url,
        "telegram_daily_push_enabled": bool(config.telegram_bot_token and config.telegram_push_chat_ids),
    }


@ui_router.get("/api/v1/trading/intraday-watchlist")
def get_intraday_watchlist() -> dict[str, Any]:
    from shilun.trading.intraday_alerts import WATCHLIST_COLLECTION, list_watchlist

    store = _open_holdings_store(read_only=True)
    try:
        items = list_watchlist(store.collection(WATCHLIST_COLLECTION))
        return {"status": "ok", "count": len(items), "items": items}
    finally:
        store.close()


@ui_router.put("/api/v1/trading/intraday-watchlist")
def put_intraday_watchlist(payload: IntradayWatchlistReplaceHttpRequest) -> dict[str, Any]:
    from shilun.trading.intraday_alerts import WATCHLIST_COLLECTION, replace_watchlist

    store = _open_holdings_store()
    try:
        items = replace_watchlist(
            store.collection(WATCHLIST_COLLECTION),
            [_model_payload(item) for item in payload.items],
        )
        return {"status": "ok", "count": len(items), "items": items}
    finally:
        store.close()


@ui_router.get("/api/v1/tushare/health")
def tushare_health() -> dict[str, Any]:
    from shilun.data import TushareConfig, TushareDailyClient

    config = load_config()
    token_configured = bool(config.tushare_token)
    base_url_configured = bool(config.tushare_base_url)
    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, *, count: int | None = None, status: str | None = None, message: str = "") -> None:
        checks.append(
            {
                "name": name,
                "ok": bool(ok),
                "status": status or ("ok" if ok else "failed"),
                "count": count,
                "message": message,
            }
        )

    def is_auth_error(message: str) -> bool:
        lowered = message.lower()
        auth_markers = ("token", "没有权限", "无权限", "权限不足")
        return any(marker in lowered for marker in auth_markers)

    if not token_configured or not base_url_configured:
        return {
            "configured": False,
            "overall_status": "unconfigured",
            "overall_label": "未配置",
            "message": "Tushare token 或 base_url 未配置。",
            "token_configured": token_configured,
            "base_url_configured": base_url_configured,
            "base_url": config.tushare_base_url,
            "checks": checks,
        }

    try:
        base_url = str(config.tushare_base_url).rstrip("/")
        if "xiaodefa.cn" in base_url:
            add_check(
                "gateway_http",
                True,
                status="sdk_probe",
                message="私有网关根路径 GET 不代表 API 可用性，以下数据接口探针为准。",
            )
        else:
            response = requests.get(base_url, timeout=3)
            add_check(
                "gateway_http",
                response.status_code < 500,
                status=f"http_{response.status_code}",
                message="网关 HTTP 可连接。" if response.status_code < 500 else "网关 HTTP 返回 5xx。",
            )
    except Exception as error:
        add_check("gateway_http", False, status="exception", message=str(error))

    try:
        client = TushareDailyClient(
            TushareConfig(
                token=config.tushare_token or "",
                base_url=config.tushare_base_url or "",
                timeout=config.tushare_timeout,
                min_interval_seconds=config.tushare_min_interval_seconds,
            )
        )
    except Exception as error:
        add_check("client_init", False, status="exception", message=str(error))
        return {
            "configured": True,
            "overall_status": "unavailable",
            "overall_label": "已配置但不可用",
            "message": "Tushare 客户端初始化失败。",
            "token_configured": token_configured,
            "base_url_configured": base_url_configured,
            "base_url": config.tushare_base_url,
            "checks": checks,
        }

    probes = [
        ("stock_basic", lambda: client.fetch_stock_basic(fields="ts_code,name,industry,market"), 1000, "股票基础信息应返回千级以上股票。"),
        (
            "trade_cal",
            lambda: client.fetch_trade_calendar(start_date="20260301", end_date="20260331", exchange="SSE"),
            10,
            "固定历史月份交易日历应返回多条记录。",
        ),
        ("daily", lambda: client.fetch_daily_for_trade_date("20260331"), 1000, "固定历史交易日日线应返回千级以上股票。"),
        ("daily_basic", lambda: client.fetch_daily_basic(trade_date="20260331"), 1000, "固定历史交易日 daily_basic 应返回千级以上股票。"),
    ]
    for name, probe, min_count, description in probes:
        try:
            frame = probe()
            count = int(len(frame))
            add_check(
                name,
                count >= min_count,
                count=count,
                status="ok" if count >= min_count else "empty_or_insufficient",
                message=description if count >= min_count else f"{description} 当前只返回 {count} 行。",
            )
        except Exception as error:
            message = str(error)
            add_check(name, False, status="auth_error" if is_auth_error(message) else "exception", message=message)

    data_checks = [check for check in checks if check["name"] != "gateway_http"]
    passed_count = sum(1 for check in data_checks if check["ok"])
    auth_error_count = sum(1 for check in data_checks if check["status"] == "auth_error")
    if auth_error_count == len(data_checks) and data_checks:
        overall_status = "auth_error"
        overall_label = "Token无效"
        message = "Tushare 网关可连接，但 token 认证失败或接口权限不足。"
    elif passed_count == len(data_checks) and data_checks:
        overall_status = "available"
        overall_label = "可用"
        message = "Tushare 网关和关键数据接口均可用。"
    elif passed_count > 0:
        overall_status = "partial"
        overall_label = "部分可用"
        message = "Tushare 部分接口可用，但不足以支撑最新交易日同步。"
    else:
        overall_status = "unavailable"
        overall_label = "已配置但不可用"
        message = "Tushare 已配置，但关键接口返回空或异常。"

    return {
        "configured": True,
        "overall_status": overall_status,
        "overall_label": overall_label,
        "message": message,
        "token_configured": token_configured,
        "base_url_configured": base_url_configured,
        "base_url": config.tushare_base_url,
        "probe_dates": {
            "calendar_start": "20260301",
            "calendar_end": "20260331",
            "daily_trade_date": "20260331",
        },
        "checks": checks,
        "recommendation": (
            "请更新 .env 中的 SHILUN_TUSHARE_TOKEN，确认它是官方 Tushare token，并具备 stock_basic/trade_cal/daily/daily_basic 权限。"
            if overall_status == "auth_error"
            else "若 stock_basic/trade_cal/daily 均返回 0 行，请检查 token、base_url、网关服务、接口权限或数据源是否过期。"
            if overall_status != "available"
            else "可继续执行最新交易日同步。"
        ),
    }


@ui_router.get("/api/v1/data/status")
def data_status(date: str | None = None, ticker: str | None = None) -> dict[str, Any]:
    """Check dated active datasets and their downstream archives."""
    from shilun.common.db import MongoSnapshotStore
    from shilun.market.part1 import BENCHMARK_INDEX_OPTIONS, benchmark_index_meta

    target_date = normalize_analysis_date(date)
    config = load_config()
    if not config.mongo_uri:
        return {
            "mongo_configured": False,
            "target_date": target_date,
            "overall_status": "blocked",
            "message": "Mongo 未配置，请先设置 SHILUN_MONGO_URI。",
        }
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db,
                                   socket_timeout_ms=30_000, initialize_indexes=False)
    except Exception as error:
        return {
            "mongo_configured": True,
            "mongo_connected": False,
            "target_date": target_date,
            "overall_status": "blocked",
            "message": "云端数据库连接未就绪，请检查数据库通道后刷新状态。",
        }
    try:
        bars_col = store.collection("market_daily_bars")
        selected_benchmark_ticker = (ticker or DEFAULT_BENCHMARK_TICKER).upper()
        selected_benchmark_meta = benchmark_index_meta(selected_benchmark_ticker)
        benchmark_tickers = [str(item["ticker"]).upper() for item in BENCHMARK_INDEX_OPTIONS]

        # ── 核心：个股日线 ─────────────────────────────────────────────
        target_daily_bar_count = bars_col.count_documents({"date": target_date})
        target_stock_count = bars_col.count_documents(
            {"date": target_date, "ticker": {"$nin": benchmark_tickers}}
        )
        has_target_stock = bool(target_stock_count)

        # ── 核心：基准指数 ─────────────────────────────────────────────
        benchmark_statuses: list[dict[str, Any]] = []
        for item in BENCHMARK_INDEX_OPTIONS:
            bm_ticker = str(item["ticker"]).upper()
            latest = list(
                bars_col.find({"ticker": bm_ticker}, {"_id": 0, "date": 1})
                .sort("date", -1).limit(1)
            )
            total = bars_col.count_documents({"ticker": bm_ticker})
            target_count = bars_col.count_documents({"date": target_date, "ticker": bm_ticker})
            benchmark_statuses.append({
                "ticker": bm_ticker,
                "name": item["name"],
                "latest_date": latest[0].get("date") if latest else None,
                "history_count": total,
                "has_target_data": bool(target_count),
                "can_calc_ma120": total >= 120,
                "can_calc_ma250": total >= 250,
            })
        selected_benchmark = next(
            (b for b in benchmark_statuses if b["ticker"] == selected_benchmark_ticker),
            {
                "ticker": selected_benchmark_ticker,
                "name": selected_benchmark_meta["name"],
                "latest_date": None,
                "history_count": 0,
                "has_target_data": False,
                "can_calc_ma120": False,
                "can_calc_ma250": False,
            },
        )

        # ── 核心：stock_basic ──────────────────────────────────────────
        stock_basic_count = store.collection("stock_basic").count_documents({})

        # ── 增强：Tushare moneyflow ────────────────────────────────────
        moneyflow_target_count = store.collection("moneyflow").count_documents(
            {"trade_date": target_date.replace("-", "")}
        )

        # ── 涨停池与复盘归档 ────────────────────────────────────────
        target_yyyymmdd = target_date.replace("-", "")
        limit_up_target_count = store.collection("limit_up_pool").count_documents(
            {"trade_date": target_yyyymmdd}
        )
        pool_state = (store.find_sync_state(dataset="eastmoney_limit_up_pool", scope=target_yyyymmdd)
                      or store.find_sync_state(dataset="akshare_limit_up_pool", scope=target_yyyymmdd) or {})
        from shilun.review.service import REVIEW_VERSION
        from shilun.market.taxonomy import VERSION as TAXONOMY_VERSION, SNAPSHOTS as TAXONOMY_SNAPSHOTS
        perspective = store.collection(TAXONOMY_SNAPSHOTS).find_one(
            {'_id': f'{TAXONOMY_VERSION}:{target_date}', 'status': 'ready'}, {'_id': 0, 'date': 1, 'catalog_count': 1, 'stock_mapping_counts': 1})
        review = store.collection("daily_review_snapshots").find_one(
            {"analysis_date": target_date, "engine_version": REVIEW_VERSION},
            {"_id": 0, "generated_at": 1, "data_quality.status": 1})
        pipeline = (store.find_sync_state(dataset="daily_data_pipeline", scope=target_date)
                    or store.find_sync_state(dataset="daily_data_pipeline", scope="latest") or {})
        from shilun.jobs.pipeline_state import TaskLedger
        pipeline = {**pipeline, 'tasks': TaskLedger(store).snapshot(target_date)}
        minute_count = store.collection("minute_bars_daily").count_documents(
            {"trade_date": target_yyyymmdd, "interval": {"$in": ["5", 5]}})

        # ── 增强：突破事件表（Job 8） ─────────────────────────────────
        breakout_events_col = store.collection("breakout_events")
        breakout_total = breakout_events_col.count_documents({})
        breakout_settled = breakout_events_col.count_documents({"status": "settled"})
        breakout_tracking = breakout_events_col.count_documents({"status": {"$in": ["pending", "tracking"]}})
        breakout_latest_doc = list(
            breakout_events_col.find({}, {"_id": 0, "breakout_date": 1})
            .sort("breakout_date", -1).limit(1)
        )
        breakout_latest_date = breakout_latest_doc[0].get("breakout_date") if breakout_latest_doc else None

        # ── 汇总：每类数据状态 ─────────────────────────────────────────
        datasets = [
            {
                "key": "stock_daily_bars",
                "label": "个股日线（Tushare）",
                "tier": "core",
                "source": "Tushare daily",
                "value": target_stock_count,
                "ok": has_target_stock,
                "detail": f"{target_date} 个股日线 {target_stock_count} 条",
                "impact_on_miss": "PART1/2/3 全部阻断",
            },
            {
                "key": "index_daily_bars",
                "label": "基准指数日线（Tushare）",
                "tier": "core",
                "source": "Tushare index_daily",
                "value": selected_benchmark["history_count"],
                "ok": selected_benchmark["has_target_data"],
                "detail": (
                    f"{selected_benchmark['name']}({selected_benchmark_ticker}) "
                    f"共 {selected_benchmark['history_count']} 根，最新 {selected_benchmark['latest_date'] or '暂无'}"
                ),
                "impact_on_miss": "PART1 阻断（指数趋势/支撑压力无法计算）",
                "extras": {
                    "can_calc_ma120": selected_benchmark["can_calc_ma120"],
                    "can_calc_ma250": selected_benchmark["can_calc_ma250"],
                },
            },
            {
                "key": "stock_basic",
                "label": "股票基础信息（Tushare）",
                "tier": "core",
                "source": "Tushare stock_basic",
                "value": stock_basic_count,
                "ok": stock_basic_count > 1000,
                "detail": f"已收录 {stock_basic_count} 只股票",
                "impact_on_miss": "PART1 主线分降级，PART2 个股 name/industry 字段丢失",
            },
            {
                "key": "moneyflow",
                "label": "个股资金流向（Tushare）",
                "tier": "enhanced",
                "source": "Tushare moneyflow",
                "value": moneyflow_target_count,
                "ok": moneyflow_target_count > 100,
                "detail": f"{target_date} 资金流 {moneyflow_target_count} 条",
                "impact_on_miss": "资金流观察不完整，应补齐所选交易日数据",
            },
            {
                "key": "limit_up_pool",
                "label": "涨停池（东方财富）",
                "tier": "enhanced",
                "source": "eastmoney dated limit pool",
                "value": limit_up_target_count,
                "ok": limit_up_target_count > 0 or pool_state.get("status") == "empty_confirmed",
                "detail": f"{target_date} 涨停 {limit_up_target_count} 只" + (" · 已核验空池" if pool_state.get("status") == "empty_confirmed" else " · 未同步" if limit_up_target_count == 0 else ""),
                "impact_on_miss": "连板与封板明细缺少原始证据；按日线和涨跌停价核验",
            },
            {"key": "minute_bars", "label": "分钟行情", "tier": "enhanced", "source": "KlineShare / Tushare / 东方财富",
             "value": minute_count, "ok": minute_count > 0, "detail": f"{target_date} 已归档 {minute_count} 个证券窗口",
             "impact_on_miss": "分时观察与盘中回放缺少分钟证据"},
            {"key": "topic_perspectives", "label": "统一分类目录", "tier": "enhanced", "source": "东方财富分类 · Tushare兼容接口 dc_index / dc_member",
             "value": (perspective or {}).get("catalog_count", 0), "ok": bool(perspective), "detail": f"{target_date} " + ("已有当前版本归档" if perspective else "尚未生成当前版本归档"),
             "impact_on_miss": "题材与细分行业复盘缺少成分证据"},
            {"key": "daily_review", "label": "复盘归档", "tier": "enhanced", "source": "同步后生成",
             "value": int(bool(review)), "ok": bool(review) and (review.get("data_quality") or {}).get("status") != "blocked",
             "detail": f"{target_date} 生成于 {review.get('generated_at')}" if review else f"{target_date} 尚未生成复盘",
             "impact_on_miss": "复盘页无所选日归档；完成行情同步后生成复盘"},
            {
                "key": "breakout_events",
                "label": "MA5事件后续验证",
                "tier": "enhanced",
                "source": "shilun.market.breakout_events",
                "value": breakout_total,
                # Existing events are useful evidence, not an execution watermark.
                "ok": bool(breakout_total > 0 and breakout_latest_date),
                "detail": (
                    f"共 {breakout_total} 条（settled {breakout_settled} · 追踪中 {breakout_tracking}）"
                    f"，最新突破日 {breakout_latest_date or '暂无'}"
                ),
                "impact_on_miss": "候选质量评分、部分MA5持仓特征和B结构样本降级；已有日线特征仍可作为备用证据。",
                "extras": {
                    "settled": breakout_settled,
                    "tracking": breakout_tracking,
                    "latest_breakout_date": breakout_latest_date,
                },
            },
        ]

        from shilun.market.data_status import cffex_dataset, enrich_datasets
        datasets.append(cffex_dataset(store, target_date))
        scheduler = enrich_datasets(
            store, datasets, target_date, benchmark_ticker=selected_benchmark_ticker,
            benchmark_count=selected_benchmark["history_count"], stock_basic_count=stock_basic_count)

        core_missing = [d for d in datasets if d["tier"] == "core" and not d["ok"] and d["status"] not in {"scheduled", "running"}]
        enhanced_missing = [d for d in datasets if d["tier"] == "enhanced" and (not d["ok"] or d["status"] == "unknown") and d["status"] not in {"scheduled", "running"}]
        pending = [d for d in datasets if d["status"] in {"scheduled", "running"}]
        if core_missing:
            overall_status = "blocked"
            top_message = f"核心数据缺失：{', '.join(d['label'] for d in core_missing)}。PART1/2/3 部分功能不可用。"
        elif pending:
            overall_status = "running" if any(d["status"] == "running" for d in pending) else "scheduled"
            top_message = "部分数据待定时任务执行或正在同步；现有归档与所选日就绪状态分开列示。"
        elif enhanced_missing:
            overall_status = "degraded"
            top_message = f"增强数据缺失（功能可降级运行）：{', '.join(d['label'] for d in enhanced_missing)}"
        else:
            overall_status = "ready"
            top_message = "全部数据源已就绪。"

        return {
            "mongo_configured": True,
            "mongo_connected": True,
            "mongo_uri": _mask_secret(config.mongo_uri),
            "target_date": target_date,
            "overall_status": overall_status,
            "message": top_message,
            "datasets": datasets,
            "scheduler": scheduler,
            "pipeline": pipeline,
            "selected_benchmark": selected_benchmark,
            "benchmark_statuses": benchmark_statuses,
            # 向后兼容旧前端
            "has_target_data": has_target_stock,
            "target_daily_bar_count": target_daily_bar_count,
            "target_stock_daily_bar_count": target_stock_count,
            "latest_market_date": selected_benchmark["latest_date"],
            "ticker": selected_benchmark_ticker,
            "benchmark_ready": selected_benchmark["has_target_data"],
        }
    except PyMongoError as error:
        logging.getLogger(__name__).warning("Data status database failure: %s", type(error).__name__)
        return {"mongo_configured": True, "mongo_connected": False,
                "target_date": target_date, "overall_status": "blocked",
                "message": "数据状态读取时数据库连接中断，暂时无法核对已保存记录；请在连接恢复后刷新。"}
    finally:
        store.close()


from shilun.market.sector_service import (
    _compute_sector_trends_full,
    _compute_sector_trends_with_candidate_minutes,
    _load_breakout_events_lookup,
    _load_market_gate,
    _load_minute_bars_from_mongo,
    _load_sector_cache,
    _minute_target_coverage,
    _moneyflow_available_for_date,
    _save_sector_cache,
    _sector_cache_key,
    _sector_candidate_minute_targets,
    _sector_payload_fund_dates,
)


def _daily_record_date(record: dict[str, Any]) -> str | None:
    """Normalize a Mongo daily-bar date without assuming one storage format."""
    value = record.get("date") or record.get("trade_date")
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


def _latest_daily_bar_date(
    records: list[dict[str, Any]],
    requested_date: str,
    *,
    strictly_before: bool = False,
) -> str | None:
    """Return the newest completed daily-bar date usable for a request.

    Intraday decisions pass ``strictly_before=True`` to prevent look-ahead: the
    session's minute data may use the session date, but its daily baseline must
    come from an already completed prior trading day.
    """
    dates = {
        value
        for record in records
        if (value := _daily_record_date(record))
        and (value < requested_date if strictly_before else value <= requested_date)
    }
    return max(dates) if dates else None


def _request_is_open_daily_session(requested_date: str, *, now: datetime | None = None) -> bool:
    """Whether today's daily bar must still be treated as incomplete."""
    current = now or datetime.now()
    return (
        requested_date == current.strftime("%Y-%m-%d")
        and (current.hour, current.minute) < (15, 15)
    )


def _decorate_daily_baseline_payload(
    payload: dict[str, Any],
    *,
    requested_date: str,
    baseline_date: str,
    from_cache: bool,
    cached_at: str | None = None,
) -> dict[str, Any]:
    result = {**payload}
    result["_from_cache"] = from_cache
    if cached_at:
        result["_cached_at"] = cached_at
    result["requested_analysis_date"] = requested_date
    result["daily_baseline_date"] = baseline_date
    result["baseline_mode"] = (
        "requested_completed_daily" if requested_date == baseline_date else "latest_completed_daily"
    )
    result["baseline_note"] = (
        f"使用 {baseline_date} 已完成日线作为盘前基线。"
        if requested_date != baseline_date
        else f"使用 {baseline_date} 已完成日线计算。"
    )
    return result


def _cffex_history_dates(store, target_date: str, *, limit: int = 7) -> list[str]:
    from shilun.data.cffex_positions import history_dates
    return history_dates(store, target_date, limit=limit)


def _cffex_history_payload(payloads: dict[str, dict[str, Any]], dates: list[str]) -> list[dict[str, Any]]:
    from shilun.data.cffex_positions import history_payload
    return history_payload(payloads, dates)


def _load_cffex_positions_snapshot(store, target_date: str, *, refresh: bool) -> dict[str, Any]:
    """Compatibility entry point shared with the independent scheduled sync."""
    from shilun.data.cffex_positions import load_snapshot
    return load_snapshot(store, target_date, refresh=refresh)


@ui_router.get("/api/v1/market/permission")
def market_permission(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    lookback_days: int = 280,  # 扩到 280 让 MA250 可算
    force: bool = False,
    read_only: bool = False,
) -> dict[str, Any]:
    """PART1 大盘权限。默认读缓存，传 force=true 强制重算。"""
    import pandas as pd

    from shilun.common.db import MongoSnapshotStore

    requested_date = normalize_analysis_date(date)
    bm_ticker = benchmark_ticker.upper()
    initial_request = MarketPart1Request(
        analysis_date=requested_date,
        benchmark_ticker=bm_ticker,
        lookback_days=lookback_days,
    )
    config = load_config()
    if read_only and force:
        raise HTTPException(400, '只读查询不能强制重算大盘权限。')
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，PART1 大盘权限需要读取 Mongo 日线数据。")
    try:
        # The review workbench reads the remote ECS MongoDB; a forced refresh
        # may fill several missing CFFEX sessions and must not use the generic
        # three-second local-database budget.
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=60_000,
                                   **({'initialize_indexes': False} if read_only else {}))
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 已配置但当前无法连接：{error}") from error
    try:
        cache_col = store.collection("market_part1_cache")
        # 先确认数据库里真正可用的已完成交易日。请求今天但今天尚未收盘或
        # 日线尚未同步时，自动使用最近已完成日线，不能要求一根未来日 K。
        initial_index_records = store.raw_market.find_daily_bars(
            start_date=initial_request.start_date,
            end_date=requested_date,
            tickers=[initial_request.benchmark_ticker],
        )
        target_date = _latest_daily_bar_date(
            initial_index_records,
            requested_date,
            strictly_before=_request_is_open_daily_session(requested_date),
        )
        if not target_date:
            raise ValueError(
                f"No benchmark/index bars found for {bm_ticker} before {requested_date}. "
                "请先同步最近一个已完成交易日的指数日线。"
            )
        request = MarketPart1Request(
            analysis_date=target_date,
            benchmark_ticker=bm_ticker,
            lookback_days=lookback_days,
        )
        cache_key = {"analysis_date": target_date, "benchmark_ticker": bm_ticker}
        if not force:
            cached = cache_col.find_one(cache_key, {"_id": 0, "payload": 1, "cached_at": 1, "engine_version": 1})
            if cached and cached.get("payload") and cached.get("engine_version") == PART1_ENGINE_VERSION:
                return _decorate_daily_baseline_payload(
                    cached["payload"],
                    requested_date=requested_date,
                    baseline_date=target_date,
                    from_cache=True,
                    cached_at=cached.get("cached_at"),
                )

        if read_only:
            raise HTTPException(404, '所选交易日的大盘权限基线尚未归档；只读查询不会重算或写入缓存。')

        index_records = store.raw_market.find_daily_bars(
            start_date=request.start_date,
            end_date=target_date,
            tickers=[request.benchmark_ticker],
        )
        market_records = store.raw_market.find_daily_bars(
            start_date=request.start_date,
            end_date=target_date,
        )
        from shilun.market.taxonomy import classify_stocks
        stock_basic = pd.DataFrame(classify_stocks(store, target_date, store.raw_market.find_stock_basic()))
        peer_tickers = [str(item["ticker"]).upper() for item in BENCHMARK_INDEX_OPTIONS]
        peer_index_records = store.raw_market.find_daily_bars(
            start_date=request.start_date,
            end_date=target_date,
            tickers=peer_tickers,
        )
        payload = evaluate_market_permission(
            analysis_date=target_date,
            benchmark_ticker=request.benchmark_ticker,
            index_bars=pd.DataFrame(index_records),
            market_bars=pd.DataFrame(market_records),
            stock_basic=stock_basic,
            peer_index_bars=pd.DataFrame(peer_index_records),
        )
        # V2 assembles the daily market answer from already-audited modules. Sector
        # data remains cache-only here, while CFFEX external pages refresh only on
        # an explicit force recompute so ordinary page loads stay deterministic.
        sector_cached = _load_sector_cache(store, target_date, bm_ticker, 60)
        sector_payload = (sector_cached or {}).get("payload") or {}
        # “强制重算”必须让 V2 与当日已经同步完成的资金事实一起前进。过去这里
        # 只重算指数、情绪和期指，板块仍静默复用旧缓存，导致 7/15 页面继续显示
        # 7/10 资金日。只有目标日 moneyflow 已真实落库时才重算，绝不伪造当日资金。
        if force and _moneyflow_available_for_date(store, target_date):
            cached_fund_dates = _sector_payload_fund_dates(sector_payload)
            if not sector_payload or target_date not in cached_fund_dates:
                from shilun.jobs.sector_precompute_job import start_task
                payload["sector_refresh_task"] = start_task(store, target_date, bm_ticker)

        try:
            limit_up_rows = list(store.collection("limit_up_pool").find(
                {"trade_date": {"$in": [target_date, target_date.replace("-", "")]}},
                {"_id": 0},
            ))
        except Exception:  # noqa: BLE001 - sentiment falls back to daily-bar proxies
            limit_up_rows = []
        institutional_futures = _load_cffex_positions_snapshot(store, target_date, refresh=force)
        from shilun.market.market_v2 import build_market_v2

        payload["market_v2"] = build_market_v2(
            payload,
            sector_payload=sector_payload,
            limit_up_rows=limit_up_rows,
            institutional_futures=institutional_futures,
        )
        cache_col.update_one(
            cache_key,
            {
                "$set": {
                    **cache_key,
                    "engine_version": PART1_ENGINE_VERSION,
                    "payload": payload,
                    "cached_at": datetime.now().isoformat(timespec="seconds"),
                }
            },
            upsert=True,
        )
        return _decorate_daily_baseline_payload(
            payload,
            requested_date=requested_date,
            baseline_date=target_date,
            from_cache=False,
        )
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Market permission failed: {error}") from error
    finally:
        store.close()
























@ui_router.get("/api/v1/market/sectors")
def market_sectors(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    lookback_days: int = 40,
    trend_lookback_days: int = 60,
    top_n: int = 8,
    force: bool = False,
) -> dict[str, Any]:
    """板块动向 — 只查缓存，未命中报 400。

    完整计算改到 POST /api/v1/data/precompute-sectors 里离线跑。
    """
    from shilun.common.db import MongoSnapshotStore

    target_date = normalize_analysis_date(date)
    bm_ticker = benchmark_ticker.upper()
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，板块动向需要读取 Mongo 日线数据。")
    try:
        # 细分成分缓存比旧行业代理更大；查询不重复初始化全库索引。
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=30000, initialize_indexes=False)
    except Exception as error:
        raise HTTPException(status_code=503, detail="云端数据库暂时无法连接，请检查数据库通道后重试。") from error

    try:
        if not force:
            from pymongo import timeout
            from pymongo.errors import PyMongoError

            try:
                # 包含重试的总预算低于页面的 45 秒等待时间。
                with timeout(30):
                    cached = _load_sector_cache(store, target_date, bm_ticker, trend_lookback_days, strict=True)
            except PyMongoError as error:
                raise HTTPException(status_code=503, detail="板块缓存读取超时或中断，请稍后重试；无需重新计算。") from error
            if cached:
                payload = cached["payload"]
                payload["_from_cache"] = True
                payload["_cached_at"] = cached.get("computed_at")
                payload["_compute_time_seconds"] = cached.get("compute_time_seconds")
                return payload

        # 未命中缓存：拒绝服务，引导用户跑预计算
        raise HTTPException(
            status_code=400,
            detail=(
                f"板块动向数据未预计算（{target_date} / {bm_ticker} / lookback={trend_lookback_days}）。"
                "请点击“生成 / 查询任务”生成该日期归档；后台完成后即可重新查询。"
            ),
        )
    finally:
        store.close()


@ui_router.post("/api/v1/data/precompute-sectors/start")
def start_sector_precompute(date: str, benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
                            top_n: int = 8, force: bool = False) -> dict:
    from shilun.jobs.sector_precompute_job import _store, start_task
    from pymongo import timeout
    from pymongo.errors import PyMongoError
    target = normalize_analysis_date(date)
    benchmark = benchmark_ticker.upper()
    if benchmark not in {item['ticker'] for item in BENCHMARK_INDEX_OPTIONS}:
        raise HTTPException(status_code=422, detail='请选择支持的基准指数。')
    store = None
    try:
        with timeout(20):
            store = _store()
            return start_task(store, target, benchmark, **({'top_n': top_n} if top_n != 8 else {}),
                              **({'force': True} if force else {}))
    except PyMongoError as error:
        raise HTTPException(status_code=503, detail='暂时无法确认板块任务提交状态；请稍后重试，同日正在运行的任务会继续复用。') from error
    except OSError as error:
        raise HTTPException(status_code=503, detail='板块后台任务未能启动，请稍后重试。') from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        if store is not None:
            store.close()


@ui_router.get("/api/v1/data/precompute-sectors/status/{job_id}")
def sector_precompute_status(job_id: str) -> dict:
    from shilun.jobs.sector_precompute_job import _store, COLLECTION, task_status
    from pymongo import timeout
    from pymongo.errors import PyMongoError
    if not re.fullmatch(r'[0-9a-f]{24}', job_id):
        raise HTTPException(status_code=422, detail='任务编号无效。')
    store = None
    try:
        with timeout(20):
            store = _store()
            row = store.collection(COLLECTION).find_one({'_id': job_id})
        if not row:
            raise HTTPException(status_code=404, detail='任务不存在，请重新发起计算。')
        return task_status(row)
    except PyMongoError as error:
        raise HTTPException(status_code=503, detail='板块任务进度暂时无法读取，后台任务可能仍在运行；请稍后继续查询。') from error
    finally:
        if store is not None:
            store.close()


@ui_router.post("/api/v1/data/precompute-sectors")
def precompute_sectors(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    lookback_days: int = 40,
    trend_lookback_days: int = 60,
    top_n: int = 8,
    include_daily_leaders: bool = False,
    include_all_sectors: bool = True,
) -> dict[str, Any]:
    """Compatibility submission endpoint; execution belongs to the ECS queue."""
    if lookback_days != 40 or trend_lookback_days != 60 or include_daily_leaders or not include_all_sectors:
        raise HTTPException(status_code=422, detail="云端预计算使用40日准备/60日趋势窗口及完整板块归档；独立龙头榜请使用原专用入口。")
    return start_sector_precompute(date=normalize_analysis_date(date), benchmark_ticker=benchmark_ticker, top_n=top_n)


@ui_router.post("/api/v1/data/precompute-breakout-events")
def precompute_breakout_events_endpoint(
    date: str | None = None,
    lookback_days: int = 90,
    max_age_days: int = 30,
    exclude_st: bool = True,
    skip_detect: bool = False,
    skip_backfill: bool = False,
) -> dict[str, Any]:
    """Job 8：日终手动触发突破事件的检测（Job 3）+ 回填（Job 4）。

    工作流：
      1. `detect_daily_breakouts` 扫全市场，把 `date` 当天新触发的突破按
         `BreakoutBaseline` 落库到 `breakout_events`（幂等）
      2. `backfill_post_bars` 扫所有 pending/tracking 事件，把 T+1..T+5 从
         `market_daily_bars` 追加进去（幂等）

    参数：
      - `skip_detect=true`：只跑回填，不检测新突破（例如白天想快速补 T+n 时用）
      - `skip_backfill=true`：只跑检测，不做回填（比如突破日当晚，还没有 T+1 日线）

    返回两阶段独立统计，便于运维日志留档。
    """
    import time
    from shilun.common.db import MongoSnapshotStore
    from shilun.market.breakout_events import (
        backfill_post_bars,
        precompute_breakout_events as _precompute_breakout_events,
    )

    target_date = normalize_analysis_date(date)
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置。")
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 连接失败：{error}") from error

    detect_result: dict[str, Any] | None = None
    backfill_result: dict[str, Any] | None = None
    try:
        t0 = time.time()
        if not skip_detect:
            detect_result = _precompute_breakout_events(
                store,
                target_date,
                lookback_days=lookback_days,
                exclude_st=exclude_st,
            )
        t1 = time.time()
        if not skip_backfill:
            backfill_result = backfill_post_bars(
                store,
                up_to_date=target_date,
                max_age_days=max_age_days,
            )
        t2 = time.time()

        message_parts: list[str] = []
        if detect_result:
            message_parts.append(
                f"检测 {detect_result.get('detected', 0)} 条（新增 {detect_result.get('inserted', 0)}，已存在 {detect_result.get('existing', 0)}）"
            )
        elif skip_detect:
            message_parts.append("已跳过检测")
        if backfill_result:
            message_parts.append(
                f"回填扫描 {backfill_result.get('scanned', 0)} 条，追加 {backfill_result.get('appended', 0)} 根 T+n，本次 {backfill_result.get('settled', 0)} 条 settled"
            )
        elif skip_backfill:
            message_parts.append("已跳过回填")

        return {
            "status": "ok",
            "analysis_date": target_date,
            "detect_time_seconds": round(t1 - t0, 2),
            "backfill_time_seconds": round(t2 - t1, 2),
            "detect": detect_result,
            "backfill": backfill_result,
            "message": " · ".join(message_parts) or "no-op",
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"突破事件预计算失败：{error}") from error
    finally:
        store.close()


@ui_router.get("/api/v1/market/candidates")
def market_candidates(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    lookback_days: int = 40,
    trend_lookback_days: int = 60,
    top_n: int = 8,
    strategy_id: str = "ma5_v0_2",
) -> dict[str, Any]:
    """按策略读取候选池；MA5使用全市场硬条件与当日量能规则归档。"""
    from shilun.common.db import MongoSnapshotStore
    from shilun.market.strategy_candidates import find_latest_strategy_candidates, REVIEW_TACTIC_IDS, review_candidate_payload

    target_date = normalize_analysis_date(date)
    bm_ticker = benchmark_ticker.upper()
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，候选池需要读取 Mongo 日线数据。")
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=60_000, initialize_indexes=False)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 已配置但当前无法连接：{error}") from error
    try:
        selected_strategy = str(strategy_id or "ma5_v0_2").strip().lower()
        if selected_strategy in REVIEW_TACTIC_IDS:
            return review_candidate_payload(store, target_date, selected_strategy)
        if selected_strategy != "ma5_v0_2":
            selection_date, candidates = find_latest_strategy_candidates(
                store,
                target_date=target_date,
                strategy_id=selected_strategy,
                exclude_st=True,
                limit=max(1, min(200, int(top_n or 8))),
            )
            from shilun.market.taxonomy import classify_stocks
            candidates = [{**r, 'sector_name': r['industry'], 'sector_code': r['industry_code']}
                          for r in classify_stocks(store, selection_date or target_date, candidates)]
            gate = _load_market_gate(store, selection_date or target_date, bm_ticker)
            return {
                "analysis_date": selection_date or target_date,
                "selection_as_of": selection_date or None,
                "benchmark_ticker": bm_ticker,
                "strategy_id": selected_strategy,
                "market_gate": gate,
                "candidates": candidates,
                "_from_cache": True,
                "_cache_source": "market_snapshot_records.strategy_signals",
            }
        cached = _load_sector_cache(store, target_date, bm_ticker, trend_lookback_days)
        if not cached:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"候选池数据未预计算（{target_date} / {bm_ticker} / lookback={trend_lookback_days}）。"
                    "请点击“补齐数据并计算”，完成后自动读取候选。"
                ),
            )
        payload = cached["payload"]
        from shilun.market.ma5_liquidity import MA5_SELECTION_RULE_VERSION
        if payload.get("selection_rule_version") != MA5_SELECTION_RULE_VERSION:
            raise HTTPException(status_code=400, detail="MA5候选规则已更新，请补齐数据并计算最新规则名单。")
        gate = _load_market_gate(store, target_date, bm_ticker)
        return {
            "analysis_date": target_date,
            "benchmark_ticker": bm_ticker,
            "strategy_id": "ma5_v0_2",
            "selection_scope": payload["selection_scope"],
            "selection_minute_coverage": payload.get("selection_minute_coverage"),
            "selection_rule_version": payload["selection_rule_version"],
            "market_gate": gate,
            "candidates": payload.get("candidates", []),
            "_from_cache": True,
            "_cached_at": cached.get("computed_at"),
        }
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Market candidates failed: {error}") from error
    finally:
        store.close()


@ui_router.post("/api/v1/holdings")
def create_holding(payload: HoldingCreateHttpRequest) -> dict[str, Any]:
    """新增一笔持仓；同一股票存在活动持仓时拒绝覆盖。"""
    from shilun.market.holdings import HoldingRecord, HoldingsError, get_holding, upsert_holding
    from shilun.strategies import (
        E7_OVERSOLD_OVERNIGHT_STRATEGY_ID,
        ExecutionStrategyError,
        get_execution_strategy,
        resolve_strategy_binding,
    )

    data = _model_payload(payload)
    data["ticker"] = _normalize_holding_ticker(data["ticker"])
    data["entry_date"] = _normalize_holding_date(data["entry_date"], "entry_date")
    data["entry_signal"] = (data.get("entry_signal") or "").strip()
    if not data["entry_signal"]:
        raise HTTPException(status_code=400, detail="入场信号 entry_signal 不能为空。")

    proposed_plan = dict(data.pop("proposed_plan", {}) or {})
    plan_keys = (
        "target_price", "stop_loss_1", "stop_loss_2", "breakdown_level",
        "add_point", "signal_candle_open",
    )
    for key in plan_keys:
        if data.get(key) is not None:
            proposed_plan[key] = data[key]
    proposed_plan.setdefault("entry_price", data["entry_price"])
    store = _open_holdings_store()
    try:
        market_snapshot = _holding_market_snapshot(store, data["ticker"])
        latest_market_date = str(market_snapshot.get("current_price_as_of") or "")[:10] or None
        # 仓位比例是账户事实的派生值，不接受前端手填口径。真实股数存在时，
        # 始终按 股数 × 成本价 ÷ 总资产 重算并落库，保留小数精度。
        if data.get("quantity"):
            risk_profile = store.collection("account_risk_profiles").find_one(
                {"profile_id": "default"}, {"_id": 0, "total_assets": 1},
            ) or {}
            total_assets = float(risk_profile.get("total_assets") or 0)
            position_cost = int(data["quantity"]) * float(data.get("cost_basis") or data["entry_price"])
            if total_assets <= 0:
                raise HTTPException(status_code=400, detail="账户总资产未配置，无法按股数 × 成本价自动计算持仓比例。")
            computed_entry_size = position_cost / total_assets
            if computed_entry_size <= 0 or computed_entry_size > 1:
                raise HTTPException(
                    status_code=400,
                    detail=f"按股数 × 成本价 ÷ 总资产计算的持仓比例为 {computed_entry_size:.2%}，必须在 0%-100% 之间。",
                )
            data["entry_size"] = round(computed_entry_size, 6)
        try:
            strategy_id, strategy_version = resolve_strategy_binding(
                data.get("entry_signal"),
                data.get("strategy_id"),
            )
            strategy = get_execution_strategy(strategy_id)
        except ExecutionStrategyError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        data["strategy_id"] = strategy_id
        data["strategy_version"] = strategy_version
        if strategy.strategy_id == E7_OVERSOLD_OVERNIGHT_STRATEGY_ID:
            data["target_price"] = None
            proposed_plan.pop("target_price", None)

        if strategy.strategy_id == "ma5_v0_2" and not proposed_plan.get("target_price"):
            target_plan = _build_holding_target_plan(
                store,
                ticker=data["ticker"],
                as_of=latest_market_date,
            )
            proposed_plan["target_price"] = target_plan["target_price"]
            proposed_plan["target_source"] = target_plan["target_source"]
            data["target_price"] = target_plan["target_price"]

        if strategy.strategy_id in {"ma5_v0_2", "manual", E7_OVERSOLD_OVERNIGHT_STRATEGY_ID} and not proposed_plan.get("stop_loss_1"):
            stop_reference = (
                float(data.get("cost_basis") or data["entry_price"])
                if strategy.strategy_id == E7_OVERSOLD_OVERNIGHT_STRATEGY_ID
                else _holding_stop_reference(store, data)
            )
            stop_plan = _build_stop_plan_for_strategy(
                store,
                strategy_id=strategy.strategy_id,
                ticker=data["ticker"],
                reference_price=stop_reference,
                as_of=latest_market_date,
                entry_date=data.get("entry_date"),
            )
            confirmed_at = datetime.now().isoformat(timespec="seconds")
            stop_plan = {**stop_plan, "status": "confirmed", "confirmed_at": confirmed_at}
            proposed_plan["stop_loss_1"] = stop_plan["recommended_stop"]
            if stop_plan.get("secondary_stop") is not None:
                proposed_plan.setdefault("stop_loss_2", stop_plan["secondary_stop"])
            data.update({
                "stop_loss_1": stop_plan["recommended_stop"],
                "stop_loss_2": stop_plan.get("secondary_stop"),
                "stop_loss_mode": "strategy",
                "stop_loss_plan": stop_plan,
                "stop_loss_confirmed_at": confirmed_at,
            })
            proposed_plan.update({
                "stop_loss_mode": "strategy",
                "stop_loss_plan": stop_plan,
                "stop_loss_confirmed_at": confirmed_at,
            })
        elif proposed_plan.get("stop_loss_1"):
            data["stop_loss_mode"] = data.get("stop_loss_mode") or "manual"
            data["stop_loss_confirmed_at"] = data.get("stop_loss_confirmed_at") or datetime.now().isoformat(timespec="seconds")
            if data["stop_loss_mode"] == "strategy" and data.get("stop_loss_plan"):
                data["stop_loss_plan"] = {
                    **data["stop_loss_plan"],
                    "status": "confirmed",
                    "confirmed_at": data["stop_loss_confirmed_at"],
                }
            proposed_plan.update({
                "stop_loss_mode": data["stop_loss_mode"],
                "stop_loss_plan": data.get("stop_loss_plan") or {},
                "stop_loss_confirmed_at": data["stop_loss_confirmed_at"],
            })

        bound_plan = strategy.bind_execution_plan(
            proposed_plan,
            actual_entry_price=float(data["entry_price"]),
        )
        data["strategy_id"] = strategy.strategy_id
        data["strategy_version"] = strategy.version
        data["initial_plan"] = dict(bound_plan)
        data["current_plan"] = dict(bound_plan)
        data["plan_revisions"] = []
        for key in plan_keys:
            if data.get(key) is None and bound_plan.get(key) is not None:
                data[key] = bound_plan[key]

        collection = store.collection("holdings")
        action = upsert_holding(
            collection,
            HoldingRecord(**data),
            position_event_collection=store.collection("holding_position_events"),
        )
        holding = get_holding(collection, data["ticker"])
        return {
            "status": "ok",
            "action": action,
            "holding": {**(holding or {}), **market_snapshot},
        }
    except HoldingsError as error:
        _raise_holdings_http(error)
    finally:
        store.close()


@ui_router.get("/api/v1/strategies")
def get_strategy_catalog() -> dict[str, Any]:
    """返回可绑定到单票分析、持仓和回测的统一策略目录。"""
    from shilun.strategies import list_execution_strategies

    strategies = list_execution_strategies()
    return {"strategies": strategies, "count": len(strategies)}


@ui_router.get("/api/v1/strategies/{strategy_id}/stop-plan")
def preview_strategy_stop_plan(
    strategy_id: str,
    ticker: str,
    reference_price: float,
    as_of: str | None = None,
) -> dict[str, Any]:
    """只读预览策略建议止损；不修改持仓，也不把建议当成已确认底线。"""

    normalized = _normalize_holding_ticker(ticker)
    if reference_price <= 0:
        raise HTTPException(status_code=400, detail="reference_price 必须大于 0")
    normalized_as_of = _normalize_holding_date(as_of, "as_of") if as_of else None
    from shilun.strategies import E7_OVERSOLD_OVERNIGHT_STRATEGY_ID

    normalized_strategy = str(strategy_id or "").strip().lower()
    store = _open_holdings_store(read_only=True)
    try:
        plan = _build_stop_plan_for_strategy(
            store,
            strategy_id=strategy_id,
            ticker=normalized,
            reference_price=reference_price,
            as_of=normalized_as_of,
            entry_date=normalized_as_of,
        )
        target_plan = (
            {
                "target_price": None,
                "target_source": "E7 次一交易日强制退出",
                "completed_through": normalized_as_of,
                "trade_plan": {
                    "target_type": "time_exit",
                    "exit_window": "09:30–10:00",
                    "exit_deadline": "次一交易日 10:00",
                },
            }
            if normalized_strategy == E7_OVERSOLD_OVERNIGHT_STRATEGY_ID
            else _build_holding_target_plan(store, ticker=normalized, as_of=normalized_as_of)
        )
        return {"status": "ok", "stop_plan": plan, "target_plan": target_plan}
    finally:
        store.close()


@ui_router.get("/api/v1/holdings")
def get_holdings(status: str | None = None) -> dict[str, Any]:
    """查询持仓列表；默认只返回 active/reduced，可用 status=all 查询全部。"""
    from shilun.market.holdings import HOLDING_STATUSES, list_holdings

    statuses: list[str] | None = None
    if status:
        requested = [item.strip().lower() for item in status.split(",") if item.strip()]
        if requested == ["all"]:
            statuses = sorted(HOLDING_STATUSES)
        else:
            invalid = sorted(set(requested) - HOLDING_STATUSES)
            if invalid:
                raise HTTPException(
                    status_code=400,
                    detail=f"status 仅支持 {sorted(HOLDING_STATUSES)} 或 all，收到 {invalid}。",
                )
            statuses = requested

    store = _open_holdings_store(read_only=True)
    try:
        holdings = [
            {**holding, **_holding_market_snapshot(store, holding["ticker"])}
            for holding in list_holdings(store.collection("holdings"), statuses=statuses)
        ]
        return {"status": "ok", "count": len(holdings), "holdings": holdings}
    finally:
        store.close()


@ui_router.get("/api/v1/holdings/{ticker}")
def get_holding_detail(ticker: str) -> dict[str, Any]:
    from shilun.market.holdings import get_holding

    normalized = _normalize_holding_ticker(ticker)
    store = _open_holdings_store(read_only=True)
    try:
        holding = get_holding(store.collection("holdings"), normalized)
        if holding is None:
            raise HTTPException(status_code=404, detail=f"{normalized} 持仓不存在")
        return {"status": "ok", "holding": {**holding, **_holding_market_snapshot(store, normalized)}}
    finally:
        store.close()


@ui_router.post("/api/v1/holdings/{ticker}/refresh-derived")
def refresh_holding_derived(ticker: str, overwrite: bool = False) -> dict[str, Any]:
    """同步最新行情并补齐目标/止损；overwrite 用于修复旧版人工补录记录。"""

    normalized = _normalize_holding_ticker(ticker)
    store = _open_holdings_store()
    try:
        return _refresh_holding_derived_plan(store, normalized, overwrite=overwrite)
    finally:
        store.close()


@ui_router.get("/api/v1/holdings/{ticker}/stop-plan")
def preview_holding_stop_plan(ticker: str) -> dict[str, Any]:
    """按持仓绑定策略生成最新建议；GET 严格只读。"""

    from shilun.market.holdings import get_holding

    normalized = _normalize_holding_ticker(ticker)
    store = _open_holdings_store(read_only=True)
    try:
        holding = get_holding(store.collection("holdings"), normalized)
        if holding is None:
            raise HTTPException(status_code=404, detail=f"{normalized} 持仓不存在")
        strategy_id, _, reference = _holding_stop_strategy_context(store, holding)
        plan = _build_stop_plan_for_strategy(
            store,
            strategy_id=strategy_id,
            ticker=normalized,
            reference_price=reference,
            entry_date=holding.get("entry_date"),
        )
        return {
            "status": "ok",
            "holding_stop": {
                "effective_stop": holding.get("stop_loss_1"),
                "mode": holding.get("stop_loss_mode") or "legacy",
                "confirmed_at": holding.get("stop_loss_confirmed_at"),
            },
            "stop_plan": plan,
        }
    finally:
        store.close()


@ui_router.post("/api/v1/holdings/{ticker}/stop-plan/apply")
def apply_holding_stop_plan(ticker: str) -> dict[str, Any]:
    """重新计算并显式确认策略止损，保留 plan revision 审计。"""

    from shilun.market.holdings import HoldingsError, get_holding, patch_holding

    normalized = _normalize_holding_ticker(ticker)
    store = _open_holdings_store()
    try:
        collection = store.collection("holdings")
        holding = get_holding(collection, normalized)
        if holding is None:
            raise HTTPException(status_code=404, detail=f"{normalized} 持仓不存在")
        strategy_id, strategy_version, reference = _holding_stop_strategy_context(store, holding)
        plan = _build_stop_plan_for_strategy(
            store,
            strategy_id=strategy_id,
            ticker=normalized,
            reference_price=reference,
            entry_date=holding.get("entry_date"),
        )
        confirmed_at = datetime.now().isoformat(timespec="seconds")
        existing_stop = holding.get("stop_loss_1")
        try:
            existing_stop = float(existing_stop) if existing_stop is not None else None
        except (TypeError, ValueError):
            existing_stop = None
        suggested_stop = float(plan["recommended_stop"])
        existing_plan = holding.get("stop_loss_plan") or {}
        correction_replaces_unadjusted = (
            plan.get("price_adjustment") == "forward"
            and existing_plan.get("price_adjustment") != "forward"
        )
        strategy_correction = (
            holding.get("stop_loss_mode") != "manual"
            and (
                holding.get("strategy_id") != strategy_id
                or existing_plan.get("strategy_id") != strategy_id
            )
        )
        effective_stop = (
            suggested_stop
            if correction_replaces_unadjusted or strategy_correction
            else max(suggested_stop, existing_stop or 0.0)
        )
        preserved_tighter_stop = (
            not correction_replaces_unadjusted
            and not strategy_correction
            and existing_stop is not None
            and existing_stop > suggested_stop
        )
        confirmed_plan = {
            **plan,
            "status": (
                "unadjusted_stop_corrected"
                if correction_replaces_unadjusted
                else "strategy_binding_corrected"
                if strategy_correction
                else "confirmed" if not preserved_tighter_stop
                else "tighter_existing_preserved"
            ),
            "confirmed_at": confirmed_at,
            "applied_stop": round(effective_stop, 4),
            "apply_reason": (
                "旧止损由未复权价格序列生成，已用前复权日线纠正。"
                if correction_replaces_unadjusted
                else "旧止损不属于当前入场场景策略，已按正确策略重新生成。"
                if strategy_correction
                else "现有有效止损更高，禁止策略更新向下放宽风险底线。"
                if preserved_tighter_stop
                else "策略建议已确认为新的有效止损。"
            ),
        }
        updates: dict[str, Any] = {
            "stop_loss_1": effective_stop,
            "stop_loss_mode": (
                holding.get("stop_loss_mode")
                if preserved_tighter_stop and holding.get("stop_loss_mode") in {"strategy", "manual"}
                else "manual" if preserved_tighter_stop else "strategy"
            ),
            "stop_loss_plan": confirmed_plan,
            "stop_loss_confirmed_at": confirmed_at,
        }
        if confirmed_plan.get("secondary_stop") is not None:
            updates["stop_loss_2"] = confirmed_plan["secondary_stop"]
        if (
            holding.get("strategy_id") != strategy_id
            or holding.get("strategy_version") != strategy_version
        ):
            collection.update_one(
                {"ticker": normalized},
                {"$set": {"strategy_id": strategy_id, "strategy_version": strategy_version}},
            )
        updated = patch_holding(collection, normalized, updates)
        return {
            "status": "ok",
            "action": (
                "unadjusted_stop_corrected"
                if correction_replaces_unadjusted
                else "strategy_binding_corrected"
                if strategy_correction
                else "tighter_existing_preserved" if preserved_tighter_stop
                else "strategy_stop_confirmed"
            ),
            "effective_stop": effective_stop,
            "holding": updated,
            "stop_plan": confirmed_plan,
        }
    except HoldingsError as error:
        _raise_holdings_http(error)
    finally:
        store.close()


@ui_router.patch("/api/v1/holdings/{ticker}")
def update_holding(ticker: str, payload: HoldingPatchHttpRequest) -> dict[str, Any]:
    from shilun.market.holdings import HoldingsError, patch_holding

    normalized = _normalize_holding_ticker(ticker)
    updates = _model_payload(payload, exclude_unset=True)
    store = _open_holdings_store()
    try:
        holding = patch_holding(
            store.collection("holdings"),
            normalized,
            updates,
            position_event_collection=store.collection("holding_position_events"),
            source="user_patch",
        )
        recalculation: dict[str, Any] | None = None
        if set(updates) & {"quantity", "available_quantity", "cost_basis", "entry_price"}:
            try:
                from shilun.trading.service import TradingDecisionService

                recalculation = TradingDecisionService(store).refresh(normalized)
            except Exception as error:  # noqa: BLE001 - 持仓事实已提交，缺行情时等待下一周期
                recalculation = {"status": "deferred", "reason": str(error)}
        return {
            "status": "ok",
            "action": "updated",
            "holding": holding,
            "decision_recalculation": recalculation,
        }
    except HoldingsError as error:
        _raise_holdings_http(error)
    finally:
        store.close()


@ui_router.post("/api/v1/holdings/{ticker}/close")
def close_holding_endpoint(
    ticker: str,
    payload: HoldingCloseHttpRequest | None = None,
) -> dict[str, Any]:
    from shilun.market.holdings import HoldingsError, close_holding

    normalized = _normalize_holding_ticker(ticker)
    close_data = _model_payload(payload, exclude_unset=True) if payload is not None else {}
    if close_data.get("exit_date"):
        close_data["exit_date"] = _normalize_holding_date(close_data["exit_date"], "exit_date")

    store = _open_holdings_store()
    try:
        holding = close_holding(
            store.collection("holdings"),
            normalized,
            position_event_collection=store.collection("holding_position_events"),
            **close_data,
        )
        return {"status": "ok", "action": "closed", "holding": holding}
    except HoldingsError as error:
        _raise_holdings_http(error)
    finally:
        store.close()


@ui_router.delete("/api/v1/holdings/{ticker}")
def delete_holding_endpoint(ticker: str, force: bool = False) -> dict[str, Any]:
    """默认执行逻辑关闭；force=true 仅用于清理误录入数据。"""
    from shilun.market.holdings import HoldingsError, close_holding, delete_holding

    normalized = _normalize_holding_ticker(ticker)
    store = _open_holdings_store()
    try:
        collection = store.collection("holdings")
        if force:
            if not delete_holding(collection, normalized):
                raise HTTPException(status_code=404, detail=f"{normalized} 持仓不存在")
            return {"status": "ok", "action": "deleted", "ticker": normalized}
        holding = close_holding(
            collection,
            normalized,
            reason="closed_by_delete_endpoint",
            position_event_collection=store.collection("holding_position_events"),
        )
        return {"status": "ok", "action": "closed", "holding": holding}
    except HoldingsError as error:
        _raise_holdings_http(error)
    finally:
        store.close()


@ui_router.post("/api/v1/holdings/daily-check")
def daily_check_holdings_endpoint(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    trend_lookback_days: int = 60,
) -> dict[str, Any]:
    """对全部 active/reduced 持仓执行一次日终状态判定。"""
    from shilun.market.holding_events import daily_check_holdings, load_holding_check_context

    target_date = normalize_analysis_date(date)
    bm_ticker = benchmark_ticker.upper()
    store = _open_holdings_store()
    try:
        market_gate, sector_states = load_holding_check_context(
            store,
            target_date,
            benchmark_ticker=bm_ticker,
            trend_lookback_days=trend_lookback_days,
        )
        result = daily_check_holdings(
            store,
            target_date,
            market_gate=market_gate,
            sector_states=sector_states,
        )
        return {
            "status": "ok",
            "benchmark_ticker": bm_ticker,
            "market_permission": str((market_gate or {}).get("permission") or "unknown"),
            "sector_context_count": len(sector_states),
            **result,
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"持仓每日检测失败：{error}") from error
    finally:
        store.close()


@ui_router.get("/api/v1/holdings/{ticker}/events")
def get_holding_events_endpoint(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 60,
) -> dict[str, Any]:
    from shilun.market.holding_events import list_holding_events

    normalized = _normalize_holding_ticker(ticker)
    if not 1 <= limit <= 365:
        raise HTTPException(status_code=400, detail="limit 必须在 1 到 365 之间。")
    normalized_start = _normalize_holding_date(start_date, "start_date") if start_date else None
    normalized_end = _normalize_holding_date(end_date, "end_date") if end_date else None
    if normalized_start and normalized_end and normalized_start > normalized_end:
        raise HTTPException(status_code=400, detail="start_date 不能晚于 end_date。")

    store = _open_holdings_store(read_only=True)
    try:
        events = list_holding_events(
            store.collection("holding_events"),
            normalized,
            start_date=normalized_start,
            end_date=normalized_end,
            limit=limit,
        )
        return {"status": "ok", "ticker": normalized, "count": len(events), "events": events}
    finally:
        store.close()


def _raise_trading_http(error: Exception) -> NoReturn:
    detail = str(error)
    status_code = 404 if "没有 active/reduced 持仓" in detail else 400
    raise HTTPException(status_code=status_code, detail=detail) from error


@ui_router.get("/api/v1/trading/dashboard")
def get_trading_dashboard() -> dict[str, Any]:
    """只读汇总全部活动持仓；不会因为页面刷新而新增决策事件。"""

    from shilun.trading.service import TradingDecisionService

    store = _open_holdings_store(read_only=True)
    try:
        return TradingDecisionService(store).dashboard()
    finally:
        store.close()


@ui_router.get("/api/v1/trading/workbench")
def get_trading_workbench(limit: int = 30) -> dict[str, Any]:
    """Job 12G 统一执行工作台；读取不写决策事件。"""

    from shilun.trading.workbench import ExecutionWorkbenchService

    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit 必须在 1 到 100 之间")
    store = _open_holdings_store(read_only=True)
    try:
        return ExecutionWorkbenchService(store).build(limit=limit)
    finally:
        store.close()


@ui_router.get("/api/v1/trading/replay-gate")
def get_trading_replay_gate(min_sessions: int = 20) -> dict[str, Any]:
    """Job 12H 只读门禁；历史事实不足时明确关闭上线权限。"""

    from shilun.jobs.trading_replay_gate_job import TradingReplayGateJob

    if min_sessions < 1 or min_sessions > 200:
        raise HTTPException(status_code=400, detail="min_sessions 必须在 1 到 200 之间")
    store = _open_holdings_store(read_only=True)
    try:
        return TradingReplayGateJob(store).run(
            min_sessions_per_scenario=min_sessions,
            persist=False,
        )
    finally:
        store.close()


@ui_router.get("/api/v1/trading/holdings/{ticker}/cockpit")
def get_trading_cockpit(ticker: str) -> dict[str, Any]:
    """返回 Action → Reason → Evidence → Data 的完整只读载荷。"""

    from shilun.trading.service import TradingDecisionError, TradingDecisionService

    normalized = _normalize_holding_ticker(ticker)
    store = _open_holdings_store(read_only=True)
    try:
        return TradingDecisionService(store).cockpit(normalized)
    except TradingDecisionError as error:
        _raise_trading_http(error)
    finally:
        store.close()


def _recalculate_order_holding(store: Any, ticker: str) -> dict[str, Any] | None:
    from shilun.market.holdings import get_holding

    holding = get_holding(store.collection("holdings"), ticker)
    if holding is None or holding.get("status") not in {"active", "reduced"}:
        return None
    try:
        from shilun.trading.service import TradingDecisionService

        return TradingDecisionService(store).refresh(ticker)
    except Exception as error:  # noqa: BLE001 - 账户事实已落库，缺行情时由下一采集周期补算
        return {"status": "deferred", "reason": str(error)}


@ui_router.get("/api/v1/trading/orders")
def get_trading_orders(
    ticker: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    from shilun.trading.order_lifecycle import (
        ORDER_COLLECTION_NAME,
        OrderLifecycleError,
        list_trade_orders,
    )

    normalized = _normalize_holding_ticker(ticker) if ticker else None
    statuses = tuple(item.strip() for item in status.split(",") if item.strip()) if status else None
    store = _open_holdings_store(read_only=True)
    try:
        try:
            orders = list_trade_orders(
                store.collection(ORDER_COLLECTION_NAME),
                ticker=normalized,
                statuses=statuses,
                limit=limit,
            )
        except OrderLifecycleError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"status": "ok", "count": len(orders), "orders": orders}
    finally:
        store.close()


@ui_router.get("/api/v1/trading/executions")
def get_trading_executions(
    ticker: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """返回真实成交流水；持仓是这组事实的派生视图。"""

    from shilun.trading.order_lifecycle import EXECUTION_COLLECTION_NAME

    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit 必须在 1 到 500 之间")
    normalized = _normalize_holding_ticker(ticker) if ticker else None
    store = _open_holdings_store(read_only=True)
    try:
        cursor = store.collection(EXECUTION_COLLECTION_NAME).find(
            {"ticker": normalized} if normalized else {}, {"_id": 0},
        ).sort("executed_at", -1).limit(limit)
        executions = [dict(item) for item in cursor]
        missing_names = sorted({str(item.get("ticker") or "").upper() for item in executions
                                if not item.get("name") and item.get("ticker")})
        names: dict[str, str] = {}
        for collection_name in (("stock_basic", "holdings") if missing_names else ()):
            for item in store.collection(collection_name).find(
                {"$or": [{"ts_code": {"$in": missing_names}}, {"ticker": {"$in": missing_names}}]},
                {"_id": 0, "ts_code": 1, "ticker": 1, "name": 1},
            ):
                item_ticker = str(item.get("ticker") or item.get("ts_code") or "").upper()
                item_name = str(item.get("name") or "").strip()
                if item_ticker and item_name and item_name.upper() != item_ticker:
                    names[item_ticker] = item_name
        for execution in executions:
            execution_ticker = str(execution.get("ticker") or "").upper()
            if not execution.get("name") and execution_ticker in names:
                execution["name"] = names[execution_ticker]
        return {
            "status": "ok",
            "ticker": normalized,
            "count": len(executions),
            "executions": executions,
        }
    finally:
        store.close()


@ui_router.post("/api/v1/trading/orders")
def create_trading_order(payload: TradeOrderHttpRequest) -> dict[str, Any]:
    from shilun.trading.order_lifecycle import (
        ACCOUNT_COLLECTION_NAME,
        ORDER_COLLECTION_NAME,
        ORDER_EVENT_COLLECTION_NAME,
        POSITION_EVENT_COLLECTION_NAME,
        RESERVATION_COLLECTION_NAME,
        OrderLifecycleError,
        TradeOrderInput,
        create_trade_order,
    )

    data = _model_payload(payload)
    data["ticker"] = _normalize_holding_ticker(data["ticker"])
    store = _open_holdings_store()
    try:
        try:
            order = create_trade_order(
                orders_collection=store.collection(ORDER_COLLECTION_NAME),
                holdings_collection=store.collection("holdings"),
                account_collection=store.collection(ACCOUNT_COLLECTION_NAME),
                reservations_collection=store.collection(RESERVATION_COLLECTION_NAME),
                position_event_collection=store.collection(POSITION_EVENT_COLLECTION_NAME),
                order_event_collection=store.collection(ORDER_EVENT_COLLECTION_NAME),
                inputs=TradeOrderInput(**data),
            )
        except OrderLifecycleError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        recalculation = (
            _recalculate_order_holding(store, order["ticker"])
            if order["side"] == "sell" else None
        )
        return {
            "status": "ok",
            "action": "order_recorded",
            "order": order,
            "decision_recalculation": recalculation,
        }
    finally:
        store.close()


@ui_router.get("/api/v1/trading/orders/{order_id}")
def get_trading_order(order_id: str) -> dict[str, Any]:
    from shilun.trading.order_lifecycle import ORDER_COLLECTION_NAME, get_trade_order

    store = _open_holdings_store(read_only=True)
    try:
        order = get_trade_order(store.collection(ORDER_COLLECTION_NAME), order_id)
        if order is None:
            raise HTTPException(status_code=404, detail=f"委托 {order_id} 不存在")
        return {"status": "ok", "order": order}
    finally:
        store.close()


@ui_router.post("/api/v1/trading/orders/{order_id}/fills")
def create_trading_order_fill(order_id: str, payload: TradeFillHttpRequest) -> dict[str, Any]:
    from shilun.trading.order_lifecycle import (
        ACCOUNT_COLLECTION_NAME,
        EXECUTION_COLLECTION_NAME,
        ORDER_COLLECTION_NAME,
        ORDER_EVENT_COLLECTION_NAME,
        POSITION_EVENT_COLLECTION_NAME,
        RESERVATION_COLLECTION_NAME,
        OrderLifecycleError,
        TradeFillInput,
        record_order_fill,
    )

    store = _open_holdings_store()
    try:
        try:
            result = record_order_fill(
                orders_collection=store.collection(ORDER_COLLECTION_NAME),
                executions_collection=store.collection(EXECUTION_COLLECTION_NAME),
                holdings_collection=store.collection("holdings"),
                account_collection=store.collection(ACCOUNT_COLLECTION_NAME),
                reservations_collection=store.collection(RESERVATION_COLLECTION_NAME),
                position_event_collection=store.collection(POSITION_EVENT_COLLECTION_NAME),
                order_event_collection=store.collection(ORDER_EVENT_COLLECTION_NAME),
                order_id=order_id,
                inputs=TradeFillInput(**_model_payload(payload)),
            )
        except OrderLifecycleError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        holding_enrichment: dict[str, Any] | None = None
        if result["order"].get("side") == "buy":
            try:
                holding_enrichment = _refresh_holding_derived_plan(
                    store,
                    result["order"]["ticker"],
                    overwrite=False,
                )
                result["holding"] = holding_enrichment["holding"]
            except Exception as error:  # noqa: BLE001 - 成交事实已落库，派生行情可稍后重试
                holding_enrichment = {"status": "deferred", "reason": str(error)}
        recalculation = _recalculate_order_holding(store, result["order"]["ticker"])
        return {
            "status": "ok",
            "action": "fill_recorded",
            **result,
            "holding_enrichment": holding_enrichment,
            "decision_recalculation": recalculation,
        }
    finally:
        store.close()


@ui_router.post("/api/v1/trading/orders/{order_id}/cancel")
def cancel_trading_order(
    order_id: str,
    payload: TradeOrderCancelHttpRequest | None = None,
) -> dict[str, Any]:
    from shilun.trading.order_lifecycle import (
        ORDER_COLLECTION_NAME,
        ORDER_EVENT_COLLECTION_NAME,
        POSITION_EVENT_COLLECTION_NAME,
        RESERVATION_COLLECTION_NAME,
        OrderLifecycleError,
        cancel_trade_order,
    )

    data = _model_payload(payload, exclude_unset=True) if payload is not None else {}
    store = _open_holdings_store()
    try:
        try:
            order = cancel_trade_order(
                orders_collection=store.collection(ORDER_COLLECTION_NAME),
                holdings_collection=store.collection("holdings"),
                reservations_collection=store.collection(RESERVATION_COLLECTION_NAME),
                position_event_collection=store.collection(POSITION_EVENT_COLLECTION_NAME),
                order_event_collection=store.collection(ORDER_EVENT_COLLECTION_NAME),
                order_id=order_id,
                **data,
            )
        except OrderLifecycleError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        recalculation = _recalculate_order_holding(store, order["ticker"])
        return {
            "status": "ok",
            "action": "order_cancelled",
            "order": order,
            "decision_recalculation": recalculation,
        }
    finally:
        store.close()


@ui_router.post("/api/v1/trading/positions/t1-settlement")
def settle_trading_t1_positions(payload: T1SettlementHttpRequest) -> dict[str, Any]:
    from shilun.trading.order_lifecycle import (
        POSITION_EVENT_COLLECTION_NAME,
        OrderLifecycleError,
        settle_t1_positions,
    )

    store = _open_holdings_store()
    try:
        try:
            holdings = settle_t1_positions(
                store.collection("holdings"),
                position_event_collection=store.collection(POSITION_EVENT_COLLECTION_NAME),
                **_model_payload(payload),
            )
        except OrderLifecycleError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        recalculations = {
            item["ticker"]: _recalculate_order_holding(store, item["ticker"])
            for item in holdings
        }
        return {
            "status": "ok",
            "action": "t1_positions_settled",
            "count": len(holdings),
            "holdings": holdings,
            "decision_recalculations": recalculations,
        }
    finally:
        store.close()


@ui_router.get("/api/v1/trading/holdings/{ticker}/executions")
def get_trade_executions(ticker: str, limit: int = 100) -> dict[str, Any]:
    from shilun.trading.executions import COLLECTION_NAME, list_executions

    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit 必须在 1 到 500 之间")
    normalized = _normalize_holding_ticker(ticker)
    store = _open_holdings_store(read_only=True)
    try:
        executions = list_executions(store.collection(COLLECTION_NAME), normalized, limit=limit)
        return {"status": "ok", "ticker": normalized, "count": len(executions), "executions": executions}
    finally:
        store.close()


@ui_router.post("/api/v1/trading/holdings/{ticker}/executions")
def create_trade_execution(ticker: str, payload: TradeExecutionHttpRequest) -> dict[str, Any]:
    """回写已经发生的成交；不连接券商，不执行订单。"""

    from shilun.trading.executions import COLLECTION_NAME, ExecutionError, record_sell_execution

    normalized = _normalize_holding_ticker(ticker)
    data = _model_payload(payload)
    if str(data.pop("side", "sell")).lower() != "sell":
        raise HTTPException(status_code=400, detail="P0 仅支持持仓卖出成交回写")
    store = _open_holdings_store()
    try:
        try:
            execution, holding = record_sell_execution(
                holdings_collection=store.collection("holdings"),
                executions_collection=store.collection(COLLECTION_NAME),
                account_collection=store.collection("account_risk_profiles"),
                position_event_collection=store.collection("holding_position_events"),
                ticker=normalized,
                **data,
            )
        except ExecutionError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        recalculation: dict[str, Any] | None = None
        if holding.get("status") in {"active", "reduced"}:
            try:
                from shilun.trading.service import TradingDecisionService

                recalculation = TradingDecisionService(store).refresh(normalized)
            except Exception as error:  # noqa: BLE001
                recalculation = {"status": "deferred", "reason": str(error)}
        return {
            "status": "ok",
            "action": "execution_recorded",
            "execution": execution,
            "holding": holding,
            "decision_recalculation": recalculation,
        }
    finally:
        store.close()


@ui_router.get("/api/v1/trading/holdings/{ticker}/scenarios")
def get_trading_scenarios(ticker: str) -> dict[str, Any]:
    from shilun.trading.service import TradingDecisionError, TradingDecisionService

    normalized = _normalize_holding_ticker(ticker)
    store = _open_holdings_store(read_only=True)
    try:
        return TradingDecisionService(store).scenarios(normalized)
    except TradingDecisionError as error:
        _raise_trading_http(error)
    finally:
        store.close()


@ui_router.get("/api/v1/trading/holdings/{ticker}/replay")
def get_trading_replay(ticker: str, trade_date: str) -> dict[str, Any]:
    """逐根回放已落库 5 分钟 K；只读且不写 Timeline。"""

    from shilun.trading.service import TradingDecisionError, TradingDecisionService

    normalized = _normalize_holding_ticker(ticker)
    normalized_date = trade_date.replace("-", "")
    if len(normalized_date) != 8 or not normalized_date.isdigit():
        raise HTTPException(status_code=400, detail="trade_date 必须使用 YYYYMMDD 或 YYYY-MM-DD")
    store = _open_holdings_store(read_only=True)
    try:
        return TradingDecisionService(store).replay(normalized, trade_date=normalized_date)
    except TradingDecisionError as error:
        _raise_trading_http(error)
    finally:
        store.close()


@ui_router.get("/api/v1/trading/holdings/{ticker}/decision-timeline")
def get_trading_decision_timeline(ticker: str, limit: int = 100) -> dict[str, Any]:
    from shilun.trading.service import TradingDecisionService

    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit 必须在 1 到 500 之间。")
    normalized = _normalize_holding_ticker(ticker)
    store = _open_holdings_store(read_only=True)
    try:
        events = TradingDecisionService(store).timeline(normalized, limit=limit)
        return {"status": "ok", "ticker": normalized, "count": len(events), "events": events}
    finally:
        store.close()


@ui_router.post("/api/v1/trading/holdings/{ticker}/refresh")
def refresh_trading_holding(ticker: str) -> dict[str, Any]:
    """显式执行一次 Playbook；仅在决策语义变化时写入 Timeline。"""

    from shilun.trading.service import TradingDecisionError, TradingDecisionService

    normalized = _normalize_holding_ticker(ticker)
    store = _open_holdings_store()
    try:
        return TradingDecisionService(store).refresh(normalized)
    except TradingDecisionError as error:
        _raise_trading_http(error)
    finally:
        store.close()


@ui_router.post("/api/v1/trading/refresh")
def refresh_trading_dashboard(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    refresh_candidates: bool = False,
) -> dict[str, Any]:
    """显式重算全部持仓；可选同步刷新当日候选机会并记录语义变化。"""

    from shilun.trading.execution_production import FullScenarioProductionService
    from shilun.trading.service import TradingDecisionService

    store = _open_holdings_store()
    try:
        holdings_result = TradingDecisionService(store).refresh_all()
    finally:
        store.close()
    candidate_result: dict[str, Any] | None = None
    if refresh_candidates:
        intraday = market_intraday(
            date=date,
            benchmark_ticker=benchmark_ticker,
            include_candidates=True,
            candidate_limit=20,
            source="auto",
            record_decisions=True,
        )
        candidate_result = {
            "session_date": intraday.get("session_date"),
            "source": intraday.get("realtime_source"),
            "candidate_count": len(intraday.get("candidates_realtime") or []),
            "recorded_decision_count": intraday.get("recorded_decision_count") or 0,
            "errors": [
                *(intraday.get("realtime_errors") or []),
                *(intraday.get("minute_errors") or []),
            ],
        }
    scenario_store = _open_holdings_store()
    try:
        scenario_result = FullScenarioProductionService(scenario_store).refresh_all()
    finally:
        scenario_store.close()
    return {
        **holdings_result,
        "holdings_refresh": holdings_result,
        "candidate_refresh": candidate_result,
        "scenario_refresh": scenario_result,
    }


@ui_router.get("/api/v1/trading/risk-profile")
def get_trading_risk_profile() -> dict[str, Any]:
    from shilun.trading.risk import COLLECTION_NAME, get_account_risk_profile

    store = _open_holdings_store(read_only=True)
    try:
        profile = get_account_risk_profile(store.collection(COLLECTION_NAME))
        return {
            "status": "ok",
            "configured": profile.source == "user_configured",
            "profile": profile.to_document(),
        }
    finally:
        store.close()


@ui_router.put("/api/v1/trading/risk-profile")
def update_trading_risk_profile(payload: AccountRiskProfileHttpRequest) -> dict[str, Any]:
    from shilun.trading.risk import (
        COLLECTION_NAME,
        AccountRiskProfile,
        RiskConfigurationError,
        upsert_account_risk_profile,
    )

    store = _open_holdings_store()
    try:
        try:
            profile = AccountRiskProfile(**_model_payload(payload))
            stored = upsert_account_risk_profile(store.collection(COLLECTION_NAME), profile)
        except RiskConfigurationError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"status": "ok", "configured": True, "profile": stored}
    finally:
        store.close()


@ui_router.get("/api/v1/trading/data-latency")
def get_trading_data_latency(
    ticker: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """读取真实 API 时效样本；本接口本身不会调用 KlineShare。"""

    from shilun.trading.latency import (
        COLLECTION_NAME,
        list_quote_latency_samples,
        summarize_quote_latency,
    )

    if not 1 <= limit <= 5000:
        raise HTTPException(status_code=400, detail="limit 必须在 1 到 5000 之间。")
    normalized = _normalize_holding_ticker(ticker) if ticker else None
    store = _open_holdings_store(read_only=True)
    try:
        samples = list_quote_latency_samples(
            store.collection(COLLECTION_NAME),
            ticker=normalized,
            limit=limit,
        )
        return {
            "status": "ok",
            "threshold_seconds": 45,
            "summary": summarize_quote_latency(samples),
            "samples": samples,
        }
    finally:
        store.close()


@ui_router.get("/api/v1/trading/readiness")
def get_trading_readiness() -> dict[str, Any]:
    """Job 8 预检：只读判断是否能开始真实盘中采样，以及哪些条件仍阻塞验收。"""

    from shilun.market.holdings import list_holdings
    from shilun.trading.latency import COLLECTION_NAME, list_quote_latency_samples, summarize_quote_latency
    from shilun.trading.readiness import REQUIRED_INDICES, build_trading_readiness

    config = load_config()
    store = _open_holdings_store(read_only=True)
    try:
        holdings = list_holdings(store.collection("holdings"))
        tickers = {
            *(str(item.get("ticker") or "").upper() for item in holdings),
            *REQUIRED_INDICES,
        }
        quote_tickers = {
            str(item.get("ticker") or "").upper()
            for item in store.collection("trading_realtime_quotes").find(
                {"ticker": {"$in": sorted(tickers)}},
                {"_id": 0, "ticker": 1},
            )
        }
        minute_tickers = {
            str(item.get("ticker") or "").upper()
            for item in store.collection("minute_bars_daily").find(
                {"ticker": {"$in": sorted(tickers)}, "interval": "5"},
                {"_id": 0, "ticker": 1},
            )
        }
        latency_summary = summarize_quote_latency(
            list_quote_latency_samples(store.collection(COLLECTION_NAME), limit=5000)
        )
        sync_states = list(
            store.collection("sync_state").find(
                {"dataset": "trading_intraday_facts"},
                {"_id": 0},
            ).sort("updated_at", -1).limit(1)
        )
        return build_trading_readiness(
            holdings=holdings,
            quote_tickers=quote_tickers,
            minute_tickers=minute_tickers,
            latency_summary=latency_summary,
            api_key_configured=bool(config.klineshare_api_key),
            now=datetime.now().astimezone(),
            last_sync_state=sync_states[0] if sync_states else None,
        )
    finally:
        store.close()


@ui_router.get("/api/v1/market/leaders")
def market_daily_leaders(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    lookback_days: int = 75,
    top_n: int = 5,
    read_only: bool = False,
) -> dict[str, Any]:
    import pandas as pd

    from shilun.common.db import MongoSnapshotStore

    target_date = normalize_analysis_date(date)
    request = SectorTrendRequest(
        analysis_date=target_date,
        benchmark_ticker=benchmark_ticker.upper(),
        lookback_days=lookback_days,
    )
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，每日龙头榜需要读取 Mongo 日线数据。")
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db,
                                   **({'initialize_indexes': False} if read_only else {}))
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 已配置但当前无法连接：{error}") from error
    try:
        cached = _load_sector_cache(store, target_date, request.benchmark_ticker, request.trend_lookback_days)
        cached_payload = (cached or {}).get("payload") or {}
        if cached_payload.get("daily_leaders") or read_only and 'daily_leaders' in cached_payload:
            return {
                "engine_version": cached_payload.get("engine_version"),
                "analysis_date": cached_payload.get("analysis_date", target_date),
                "benchmark_ticker": cached_payload.get("benchmark_ticker", request.benchmark_ticker),
                "daily_leaders": cached_payload.get("daily_leaders") or [],
                "leader_summary": cached_payload.get("leader_summary") or {},
                "_from_cache": True,
                "_cached_at": cached.get("computed_at"),
            }
        if read_only:
            raise HTTPException(404, '所选日龙头榜尚未归档；只读查询不会重新计算或补写缓存。')
        benchmark_records = store.raw_market.find_daily_bars(
            start_date=request.start_date,
            end_date=target_date,
            tickers=[request.benchmark_ticker],
        )
        market_records = store.raw_market.find_daily_bars(
            start_date=request.start_date,
            end_date=target_date,
        )
        from shilun.market.taxonomy import classify_stocks
        stock_basic = pd.DataFrame(classify_stocks(store, target_date, store.raw_market.find_stock_basic()))
        daily_basic = pd.DataFrame(store.raw_market.find_daily_basic(trade_date=target_date))
        moneyflow_start = (
            datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=18)
        ).strftime("%Y-%m-%d")
        moneyflow = pd.DataFrame(
            store.raw_market.find_moneyflow(
                start_date=moneyflow_start,
                end_date=target_date,
            )
        )
        leader_result = evaluate_daily_leaders(
            analysis_date=target_date,
            benchmark_ticker=request.benchmark_ticker,
            benchmark_bars=pd.DataFrame(benchmark_records),
            market_bars=pd.DataFrame(market_records),
            stock_basic=stock_basic,
            daily_basic=daily_basic,
            moneyflow=moneyflow,
            min_stock_count=request.min_stock_count,
            exclude_st=request.exclude_st,
            top_n=top_n,
        )
        # 龙头榜是相同分析日/基准下的确定性结果。首次慢算后补写进现有
        # sector_trends_cache 的 payload，下一次页面查询秒回；只更新龙头字段，
        # 不覆盖板块主结果及其 computed_at。
        if cached_payload:
            store.collection("sector_trends_cache").update_one(
                _sector_cache_key(target_date, request.benchmark_ticker, request.trend_lookback_days),
                {
                    "$set": {
                        "payload.daily_leaders": leader_result.get("daily_leaders") or [],
                        "payload.leader_summary": leader_result.get("leader_summary") or {},
                        "leader_computed_at": datetime.now().isoformat(timespec="seconds"),
                    }
                },
            )
        return {**leader_result, "_from_cache": False}
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Daily leaders failed: {error}") from error
    finally:
        store.close()


@ui_router.post("/api/v1/data/sync")
def data_sync(payload: DataSyncHttpRequest) -> dict[str, Any]:
    from shilun.common.db import MongoSnapshotStore
    from shilun.jobs.tushare_sync_job import TushareSyncJob, TushareSyncRequest
    from pymongo.errors import PyMongoError

    target_date = normalize_analysis_date(payload.target_date)
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，请先设置 SHILUN_MONGO_URI。")
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=60_000)
    except Exception as error:
        raise HTTPException(status_code=503, detail="数据库连接失败，请检查数据库服务与连接通道后重试。") from error
    try:
        if payload.mode == "latest":
            result = TushareSyncJob(mongo_store=store).run(
                TushareSyncRequest(
                    target_date=target_date,
                    latest_only=True,
                    skip_if_exists=not payload.force,
                    benchmark_ticker=(payload.benchmark_ticker or DEFAULT_BENCHMARK_TICKER).upper(),
                    sync_all_benchmarks=True,
                )
            )
        elif payload.mode == "incremental":
            result = TushareSyncJob(mongo_store=store).run(
                TushareSyncRequest(
                    target_date=target_date,
                    incremental=True,
                    skip_if_exists=not payload.force,
                    continue_on_error=True,
                    benchmark_ticker=(payload.benchmark_ticker or DEFAULT_BENCHMARK_TICKER).upper(),
                    sync_all_benchmarks=True,
                    incremental_lookback_days=payload.incremental_lookback_days,
                    incremental_overlap_days=payload.incremental_overlap_days,
                )
            )
        elif payload.mode == "history_year":
            target_dt = datetime.strptime(target_date, "%Y-%m-%d")
            result = TushareSyncJob(mongo_store=store).run(
                TushareSyncRequest(
                    target_date=target_date,
                    start_date=(target_dt - timedelta(days=365)).strftime("%Y%m%d"),
                    end_date=target_dt.strftime("%Y%m%d"),
                    lookback_days=365,
                    skip_if_exists=True,
                    continue_on_error=True,
                    benchmark_ticker=(payload.benchmark_ticker or DEFAULT_BENCHMARK_TICKER).upper(),
                    sync_all_benchmarks=True,
                )
            )
        elif payload.mode == "index_history_year":
            target_dt = datetime.strptime(target_date, "%Y-%m-%d")
            result = TushareSyncJob(mongo_store=store).run(
                TushareSyncRequest(
                    target_date=target_date,
                    start_date=(target_dt - timedelta(days=365)).strftime("%Y%m%d"),
                    end_date=target_dt.strftime("%Y%m%d"),
                    lookback_days=365,
                    continue_on_error=True,
                    benchmark_ticker=(payload.benchmark_ticker or DEFAULT_BENCHMARK_TICKER).upper(),
                    sync_all_benchmarks=True,
                    index_only=True,
                )
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="mode 只支持 latest / incremental / history_year / index_history_year。",
            )
        return {
            "target_date": result.target_date,
            "sync_trade_date": result.sync_trade_date,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "skipped": result.skipped,
            "message": result.message,
            "stock_basic_count": result.stock_basic_count,
            "trade_calendar_count": result.trade_calendar_count,
            "daily_bar_count": result.daily_bar_count,
            "daily_basic_count": result.daily_basic_count,
            "moneyflow_count": result.moneyflow_count,
            "benchmark_bar_count": result.benchmark_bar_count,
            "price_limit_count": result.price_limit_count,
            "status": "partial_error" if result.failed_trade_dates else "ok",
            "failed_trade_dates": result.failed_trade_dates,
            "synced_trade_dates": result.synced_trade_dates,
            "skipped_trade_dates": result.skipped_trade_dates,
        }
    except HTTPException:
        raise
    except PyMongoError as error:
        raise HTTPException(status_code=503, detail="同步时数据库读取或写入超时。已完成的数据会保留，请检查数据库连接后重新同步增量。") from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Tushare同步未完成（{type(error).__name__}），请检查行情服务与同步日志后重试。") from error
    finally:
        store.close()


@ui_router.post("/api/v1/data/sync-akshare", deprecated=True)
def data_sync_akshare(payload: DataDomainSyncHttpRequest) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="旧增强数据同步已下线。涨停池请使用 /api/v1/data/sync-limit-pool；题材成分随复盘更新。")


@ui_router.post("/api/v1/data/sync-limit-pool")
def data_sync_limit_pool(payload: DataDomainSyncHttpRequest) -> dict[str, Any]:
    from shilun.common.db import MongoSnapshotStore
    from shilun.data.providers.eastmoney_provider import EastmoneyFetchError
    from shilun.jobs.limit_pool_sync_job import LimitPoolSyncJob
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置")
    target = normalize_analysis_date(payload.target_date)
    store = None
    sync_started = False
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db,
                                   socket_timeout_ms=30_000, initialize_indexes=False)
        store.db.command("ping")
        sync_started = True
        count = LimitPoolSyncJob(store).sync_limit_up_pool(target.replace("-", ""))
        return {"target_date": target, "count": count, "source": "eastmoney",
                "status": "success" if count else "empty_confirmed"}
    except PyMongoError as error:
        logging.getLogger(__name__).warning("Limit pool database failure: date=%s stage=%s type=%s",
                                            target, "sync" if sync_started else "connect", type(error).__name__)
        detail = ("涨停池同步时数据库连接中断，部分记录可能已保存。连接恢复后可重新同步同一天，记录按股票和日期更新，不会重复新增。"
                  if sync_started else
                  "数据库连接未就绪，本次尚未抓取或写入涨停池。请检查云端数据库通道，恢复后可重试。")
        raise HTTPException(status_code=503, detail=detail) from error
    except EastmoneyFetchError as error:
        raise HTTPException(status_code=502, detail="该日期涨停池暂不可用，请检查日期或稍后重试；未写入涨停池，也未将缺数记为零。") from error
    except Exception as error:
        logging.getLogger(__name__).warning("Limit pool sync failed: date=%s type=%s", target, type(error).__name__)
        raise HTTPException(status_code=500, detail="涨停池同步未完成，请刷新数据状态并检查同步日志。") from error
    finally:
        if store is not None:
            store.close()


@ui_router.post("/api/v1/data/sync-minutes")
def data_sync_minutes(payload: DataDomainSyncHttpRequest) -> dict[str, Any]:
    from dataclasses import asdict
    from shilun.api.review import _open_store
    from shilun.jobs.minute_bars_sync_job import MinuteBarsSyncJob
    store = _open_store()
    try:
        result = MinuteBarsSyncJob(store).run(normalize_analysis_date(payload.target_date))
        return {**asdict(result), "status": "partial" if result.failed_tickers else "success"}
    finally:
        store.close()


@ui_router.post("/api/v1/data/sync-topics")
def data_sync_topics(payload: DataDomainSyncHttpRequest) -> dict[str, Any]:
    from shilun.api.review import _open_store
    from shilun.review.topic_perspectives import sync_perspectives
    store = _open_store()
    try:
        result = sync_perspectives(store, normalize_analysis_date(payload.target_date))
        return {"date": result.get("date"), "version": result.get("version"), "status": result.get("status", "complete")}
    except Exception as error:
        raise HTTPException(status_code=502, detail="题材成分同步未完成，请检查数据网关后重试。") from error
    finally:
        store.close()


def _fetch_realtime_via_klineshare(bm_ticker: str, candidate_tickers: list[str] | None):
    """用 KlineShare 批量获取基准和个股，返回统一的盘中行情结构。"""
    import pandas as pd

    from shilun.data.providers.klineshare_provider import (
        KlineShareClient,
        KlineShareConfig,
        KlineShareError,
    )

    config = load_config()
    if not config.klineshare_api_key:
        return None, None, ["klineshare: SHILUN_KLINESHARE_API_KEY 未配置"]
    client = KlineShareClient(KlineShareConfig(
        api_key=config.klineshare_api_key,
        base_url=config.klineshare_base_url,
        timeout=config.klineshare_timeout,
    ))
    requested = list(dict.fromkeys([bm_ticker, *(candidate_tickers or [])]))
    frames = []
    errors: list[str] = []
    # 基础版单次实时请求最多 20 个标的。显式分批，避免选品池扩大后整批失败。
    for start in range(0, len(requested), 20):
        batch = requested[start:start + 20]
        try:
            batch_frame = client.fetch_realtime(
                batch,
                include_depth=config.klineshare_include_depth,
            )
        except KlineShareError as error:
            errors.append(f"klineshare realtime ({','.join(batch)}): {error}")
            continue
        if batch_frame is not None and not batch_frame.empty:
            frames.append(batch_frame)
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if frame is None or frame.empty:
        return None, None, errors or ["klineshare realtime: empty response"]
    benchmark_rows = frame[frame["ticker"].str.upper() == bm_ticker.upper()]
    benchmark = None
    if not benchmark_rows.empty:
        row = benchmark_rows.iloc[0].to_dict()
        benchmark = {
            "ticker": row.get("ticker"),
            "name": row.get("name"),
            "current": _round_num(row.get("current")),
            "pct_chg": _round_num(row.get("pct_chg"), 4),
            "open": _round_num(row.get("open")),
            "high": _round_num(row.get("high")),
            "low": _round_num(row.get("low")),
            "prev_close": _round_num(row.get("prev_close")),
            "amount": _round_num(row.get("amount")),
            "trade_status": row.get("trade_status"),
            "update_at": row.get("update_at"),
            "quote_at": row.get("quote_at"),
            "fetched_at": row.get("fetched_at"),
            "timestamp_kind": row.get("timestamp_kind"),
        }
    stocks = frame[frame["ticker"].str.upper() != bm_ticker.upper()].copy()
    return benchmark, stocks, errors


def _sync_intraday_minutes_via_klineshare(
    store: Any,
    tickers: list[str],
    session_date: str,
) -> tuple[dict[str, list[dict[str, Any]]], list[str], set[str]]:
    """获取已闭合 5 分钟 K、落 Mongo，并回读统一分钟线契约。"""
    from zoneinfo import ZoneInfo

    from shilun.data.providers.klineshare_provider import (
        KlineShareClient,
        KlineShareConfig,
        KlineShareError,
    )
    from shilun.jobs.trading_intraday_sync_job import closed_kline_frame
    from shilun.market.minute_bars_store import (
        COLLECTION_NAME as MINUTE_COLL,
        bulk_load_lookup,
        upsert_minute_bars,
    )

    normalized = list(dict.fromkeys(str(ticker or "").upper() for ticker in tickers if ticker))
    collection = store.collection(MINUTE_COLL)
    trade_date = session_date.replace("-", "")
    config = load_config()
    errors: list[str] = []
    refreshed: set[str] = set()
    now = datetime.now(tz=ZoneInfo("Asia/Shanghai"))

    # 历史会话只读 Mongo，不能拿今天的实时 K 覆盖历史日期。
    if config.klineshare_api_key and session_date == now.strftime("%Y-%m-%d"):
        client = KlineShareClient(KlineShareConfig(
            api_key=config.klineshare_api_key,
            base_url=config.klineshare_base_url,
            timeout=config.klineshare_timeout,
        ))
        for ticker in normalized:
            try:
                frame = client.fetch_kline(
                    ticker,
                    period=300,
                    count=100,
                    adjust_type="none",
                )
                frame = closed_kline_frame(frame, now=now, period_seconds=300)
                if frame.empty:
                    errors.append(f"{ticker}: KlineShare 暂无已闭合 5 分钟 K")
                    continue
                upsert_minute_bars(
                    collection,
                    ticker=ticker,
                    trade_date=trade_date,
                    interval="5",
                    bars=frame.to_dict("records"),
                    source="klineshare",
                )
                refreshed.add(ticker)
            except KlineShareError as error:
                errors.append(f"{ticker}: {error}")

    return (
        bulk_load_lookup(collection, normalized, trade_date, interval="5"),
        errors,
        refreshed,
    )


def _persist_klineshare_realtime_snapshot(
    store: Any,
    benchmark: dict[str, Any] | None,
    stocks: Any,
) -> None:
    """让盘中行情读到的真实报价也成为驾驶舱可复用的 Mongo 事实。"""
    from shilun.jobs.trading_intraday_sync_job import REALTIME_COLLECTION, _clean_document

    records: list[dict[str, Any]] = []
    if benchmark:
        records.append(dict(benchmark))
    if stocks is not None and not stocks.empty:
        records.extend(stocks.to_dict("records"))
    collection = store.collection(REALTIME_COLLECTION)
    for record in records:
        document = _clean_document(record)
        ticker = str(document.get("ticker") or "").upper()
        if ticker:
            document["ticker"] = ticker
            document.setdefault("source", "klineshare")
            collection.update_one({"ticker": ticker}, {"$set": document}, upsert=True)


def _fetch_realtime_via_tushare(bm_ticker: str, candidate_tickers: list[str] | None):
    """用 Tushare（新浪源）拿实时行情。返回 (benchmark_dict|None, stock_df|None, errors)"""
    from shilun.data.providers.tushare_realtime import TushareRealtimeClient, TushareRealtimeError

    errors: list[str] = []
    benchmark = None
    stock_df = None

    client = TushareRealtimeClient()
    try:
        idx_df = client.fetch_index_realtime([bm_ticker])
        if idx_df is not None and not idx_df.empty:
            r = idx_df.iloc[0].to_dict()
            benchmark = {
                "ticker": r.get("ticker"),
                "name": r.get("name"),
                "current": _round_num(r.get("current")),
                "pct_chg": _round_num(r.get("pct_chg"), 4),
                "open": _round_num(r.get("open")),
                "high": _round_num(r.get("high")),
                "low": _round_num(r.get("low")),
                "prev_close": _round_num(r.get("prev_close")),
                "amount": _round_num(r.get("amount")),
                "update_at": r.get("update_at"),
                "quote_at": r.get("quote_at"),
                "fetched_at": r.get("fetched_at"),
                "timestamp_kind": r.get("timestamp_kind"),
            }
    except TushareRealtimeError as e:
        errors.append(f"tushare index_realtime: {e.underlying}")

    if candidate_tickers:
        try:
            stock_df = client.fetch_stock_realtime_batch(candidate_tickers)
        except TushareRealtimeError as e:
            errors.append(f"tushare stock_realtime: {e.underlying}")

    return benchmark, stock_df, errors


def _fetch_realtime_via_eastmoney(bm_ticker: str, candidate_tickers: list[str] | None):
    """用 东方财富直连拿实时行情。作为 tushare 失败时的回退。"""
    from shilun.data.providers.eastmoney_provider import EastmoneyClient, EastmoneyConfig, EastmoneyFetchError
    from shilun.data.providers.klineshare_provider import (
        KlineShareClient,
        KlineShareConfig,
        KlineShareError,
    )

    errors: list[str] = []
    benchmark = None
    stock_df = None

    client = EastmoneyClient(EastmoneyConfig())
    try:
        idx_df = client.fetch_index_realtime()
        if idx_df is not None and not idx_df.empty:
            match = idx_df[idx_df["ticker"].str.upper() == bm_ticker]
            if not match.empty:
                r = match.iloc[0].to_dict()
                benchmark = {
                    "ticker": r.get("ticker"),
                    "name": r.get("name"),
                    "current": _round_num(r.get("current")),
                    "pct_chg": _round_num(r.get("pct_chg"), 4),
                    "open": _round_num(r.get("open")),
                    "high": _round_num(r.get("high")),
                    "low": _round_num(r.get("low")),
                    "prev_close": _round_num(r.get("prev_close")),
                    "amount": _round_num(r.get("amount")),
                    "update_at": r.get("update_at"),
                }
    except EastmoneyFetchError as e:
        errors.append(f"eastmoney index_realtime: {e.underlying}")

    if candidate_tickers:
        try:
            stock_df = client.fetch_stock_realtime_batch(tickers=candidate_tickers)
        except EastmoneyFetchError as e:
            errors.append(f"eastmoney stock_realtime: {e.underlying}")

    return benchmark, stock_df, errors


@ui_router.get("/api/v1/stocks/search")
def search_stocks(q: str, limit: int = 10, read_only: bool = False) -> dict[str, Any]:
    """按证券代码或名称模糊检索本地股票主数据，供选品池快速添加。"""
    from shilun.common.db import MongoSnapshotStore

    keyword = str(q or "").strip()
    if not keyword:
        return {"query": "", "items": []}
    safe_limit = max(1, min(int(limit or 10), 20))
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，无法检索股票主数据。")
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db,
                                   **({'initialize_indexes': False} if read_only else {}))
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 连接失败：{error}") from error
    try:
        pattern = re.escape(keyword)
        rows = list(
            store.collection("stock_basic").find(
                {
                    "$or": [
                        {"ts_code": {"$regex": pattern, "$options": "i"}},
                        {"symbol": {"$regex": pattern, "$options": "i"}},
                        {"name": {"$regex": pattern, "$options": "i"}},
                    ]
                },
                {"_id": 0, "ts_code": 1, "symbol": 1, "name": 1, "industry": 1, "market": 1},
            ).limit(max(30, safe_limit * 4))
        )
        upper = keyword.upper()

        def rank(row: dict[str, Any]) -> tuple[int, str]:
            ticker = str(row.get("ts_code") or "").upper()
            symbol = str(row.get("symbol") or "").upper()
            name = str(row.get("name") or "")
            if upper in {ticker, symbol} or keyword == name:
                score = 0
            elif ticker.startswith(upper) or symbol.startswith(upper) or name.startswith(keyword):
                score = 1
            else:
                score = 2
            return score, ticker

        from shilun.market.taxonomy import classify_stocks
        from shilun.review.service import DailyReviewService
        taxonomy_date = DailyReviewService(store).latest_date(None)
        items = classify_stocks(store, taxonomy_date, sorted(rows, key=rank)[:safe_limit])
        return {
            "query": keyword,
            "items": [
                {
                    "ticker": item.get("ts_code") or item.get("symbol"),
                    "name": item.get("name") or item.get("ts_code"),
                    "industry": item.get("industry") or "",
                    "industry_code": item.get("industry_code"),
                    "taxonomy_date": item.get("taxonomy_date"),
                    "market": item.get("market") or "",
                }
                for item in items
            ],
        }
    finally:
        store.close()


def _read_archived_quotes(store: Any, tickers: list[str], target_date: str) -> dict[str, dict[str, Any]]:
    """Read provider-dated quotes without fetching or backdating a later quote."""
    from zoneinfo import ZoneInfo
    rows = store.collection('trading_realtime_quotes').find(
        {'ticker': {'$in': sorted(set(tickers))}}, {'_id': 0})
    result, timestamps = {}, {}
    for row in rows:
        ticker = str(row.get('ticker') or '').upper()
        stamp = row.get('quote_at') or row.get('update_at')
        try:
            parsed = stamp if isinstance(stamp, datetime) else datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
            zone = ZoneInfo('Asia/Shanghai')
            parsed = parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)
        except (TypeError, ValueError):
            continue
        if ticker not in tickers or parsed.date().isoformat() != target_date:
            continue
        if ticker not in timestamps or parsed > timestamps[ticker]:
            result[ticker], timestamps[ticker] = row, parsed
    return result


@ui_router.get("/api/v1/market/intraday")
def market_intraday(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    include_candidates: bool = True,
    candidate_limit: int = 20,
    selected_tickers: str | None = None,
    selected_only: bool = False,
    source: str = "auto",
    record_decisions: bool = False,
    read_only: bool = False,
) -> dict[str, Any]:
    """盘中实时监控。

    source 参数控制实时行情数据源：
    - "auto"（默认）：配置 Key 时优先 KlineShare，再回退 tushare / eastmoney
    - "klineshare"：只用 KlineShare
    - "tushare"：只用 tushare（推荐，本地网络下最稳定）
    - "eastmoney"：只用东方财富

    大盘关键位：自动读取或建立上一已完成交易日的 PART1 基线。
    候选票：从 candidate_pool_states 取 top N 只，实时拉取并对比。
    """
    from shilun.common.db import MongoSnapshotStore

    # date 表示盘中会话日（历史回放时可显式传入），不是 PART1 日线日。
    # 日线基线永远来自会话日前一个已完成交易日，避免盘中使用未来数据。
    session_date = normalize_analysis_date(date)
    bm_ticker = benchmark_ticker.upper()
    config = load_config()
    if read_only and record_decisions:
        raise HTTPException(400, '只读查询不能记录盘中决策或创建推送。')
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，盘中监控需要读取日线基线与分钟数据。")
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db,
                                   **({'initialize_indexes': False} if read_only else {}))
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 已配置但当前无法连接：{error}") from error

    try:
        # 1. 解析上一已完成交易日，并自动读取或建立它的 PART1 基线。
        baseline_probe_start = (
            datetime.strptime(session_date, "%Y-%m-%d") - timedelta(days=45)
        ).strftime("%Y-%m-%d")
        baseline_records = store.raw_market.find_daily_bars(
            start_date=baseline_probe_start,
            end_date=session_date,
            tickers=[bm_ticker],
        )
        baseline_date = _latest_daily_bar_date(
            baseline_records,
            session_date,
            strictly_before=True,
        )
        if not baseline_date:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"未找到 {session_date} 之前的已完成基准指数日线。"
                    "盘中监控需要上一交易日作为盘前基线，请先同步最近已完成交易日。"
                ),
            )
        payload = market_permission(
            date=baseline_date,
            benchmark_ticker=bm_ticker,
            lookback_days=280,
            force=False,
            **({'read_only': True} if read_only else {}),
        )
        part1_cached_at = payload.get("_cached_at")

        gate = payload.get("market_gate") or {}
        levels = payload.get("levels") or {}
        pattern_forecast = payload.get("pattern_forecast") or {}
        trend_summary = payload.get("trend_summary") or {}

        # 2. 决定候选票列表
        candidate_tickers: list[str] = []
        candidates_meta: list[dict[str, Any]] = []
        if include_candidates:
            parsed_selected_tickers = _parse_selected_tickers(selected_tickers)
            candidates_meta = _extract_candidates_from_cache(
                store,
                baseline_date,
                bm_ticker,
                gate,
                candidate_limit,
                selected_tickers=parsed_selected_tickers,
            )
            if selected_only:
                selected_set = {item.upper() for item in parsed_selected_tickers}
                candidates_meta = [
                    item for item in candidates_meta
                    if str(item.get("ticker") or "").upper() in selected_set
                ]
            candidates_meta = _enrich_intraday_candidate_plans(store, baseline_date, candidates_meta)
            candidate_tickers = [c["ticker"] for c in candidates_meta if c.get("ticker")]

        # 3. 按 source 参数选择实时数据源
        realtime_errors: list[str] = []
        benchmark_realtime = None
        stock_df = None
        source_used = None

        if read_only:
            import pandas as pd
            archived = _read_archived_quotes(store, [bm_ticker, *candidate_tickers], session_date)
            benchmark_realtime = archived.get(bm_ticker)
            stock_rows = [archived[ticker] for ticker in candidate_tickers if ticker in archived]
            stock_df = pd.DataFrame(stock_rows) if stock_rows else None
            source_used = 'mongo_archive' if archived else None
            realtime_errors.append('只读归档模式：保留提供方报价时间，不请求外部行情、不补分钟数据、不写决策。')

        if source not in {"auto", "klineshare", "tushare", "eastmoney"}:
            raise HTTPException(status_code=400, detail="source 只支持 auto / klineshare / tushare / eastmoney")

        if not read_only and source in ("auto", "klineshare"):
            if source == "klineshare" and not config.klineshare_api_key:
                raise HTTPException(status_code=400, detail="SHILUN_KLINESHARE_API_KEY 未配置")
            if config.klineshare_api_key:
                benchmark_realtime, stock_df, errs = _fetch_realtime_via_klineshare(
                    bm_ticker,
                    candidate_tickers,
                )
                if benchmark_realtime is not None:
                    source_used = "klineshare"
                    _persist_klineshare_realtime_snapshot(store, benchmark_realtime, stock_df)
                realtime_errors.extend(errs)

        if not read_only and benchmark_realtime is None and source in ("auto", "tushare"):
            benchmark_realtime, stock_df, errs = _fetch_realtime_via_tushare(bm_ticker, candidate_tickers)
            if benchmark_realtime is not None:
                source_used = "tushare"
            else:
                realtime_errors.extend(errs)

        if not read_only and benchmark_realtime is None and source in ("auto", "eastmoney"):
            benchmark_realtime, stock_df, errs = _fetch_realtime_via_eastmoney(bm_ticker, candidate_tickers)
            if benchmark_realtime is not None:
                source_used = "eastmoney"
            realtime_errors.extend(errs)

        # 4. 触发的关键位分析
        triggered_levels = _detect_triggered_levels(
            current=benchmark_realtime.get("current") if benchmark_realtime else None,
            high=benchmark_realtime.get("high") if benchmark_realtime else None,
            low=benchmark_realtime.get("low") if benchmark_realtime else None,
            open_=benchmark_realtime.get("open") if benchmark_realtime else None,
            levels=levels,
            pattern_forecast=pattern_forecast,
        )

        # 5. 候选票实时验证
        candidate_realtime = (
            _merge_candidates_with_realtime(candidates_meta, stock_df, benchmark_realtime)
            if candidates_meta
            else []
        )
        minute_errors: list[str] = []
        minute_refreshed: set[str] = set()
        # KlineShare 实时源同时回补并落库真实 5 分钟 K；其他源只读已有 Mongo。
        # 前端 30 秒轮询只负责触发刷新，不能被误当成一条完整趋势线。
        recorded_decision_count = 0
        if candidate_realtime:
            from shilun.market.minute_bars_store import COLLECTION_NAME as MINUTE_COLL, bulk_load_lookup

            minute_tickers = [item.get("ticker") for item in candidate_realtime if item.get("ticker")]
            if source_used == "klineshare":
                minute_lookup, minute_errors, minute_refreshed = _sync_intraday_minutes_via_klineshare(
                    store,
                    minute_tickers,
                    session_date,
                )
            else:
                minute_lookup = bulk_load_lookup(
                    store.collection(MINUTE_COLL),
                    minute_tickers,
                    session_date.replace("-", ""),
                    interval="5",
                )
            for item in candidate_realtime:
                ticker = str(item.get("ticker") or "").upper()
                item["minute_bars"] = minute_lookup.get(ticker, [])
                item["minute_bar_interval"] = "5"
                item["minute_bar_source"] = "klineshare" if ticker in minute_refreshed else "mongo"
                item["minute_bar_status"] = "available" if item["minute_bars"] else "missing"

        if candidate_realtime:
            from zoneinfo import ZoneInfo

            from shilun.trading.intraday_alerts import (
                EVENT_COLLECTION,
                OUTBOX_COLLECTION,
                WATCHLIST_COLLECTION,
                list_candidate_timeline,
                list_watchlist,
                record_candidate_decisions,
            )

            computed_at = datetime.now(tz=ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
            watchlist = list_watchlist(store.collection(WATCHLIST_COLLECTION))
            push_tickers = {
                str(item.get("ticker") or "").upper()
                for item in watchlist
                if item.get("feishu_enabled", True)
            }
            if record_decisions:
                recorded_events = record_candidate_decisions(
                    store.collection(EVENT_COLLECTION),
                    store.collection(OUTBOX_COLLECTION),
                    candidate_realtime,
                    session_date=session_date,
                    computed_at=computed_at,
                    market_gate=gate,
                    push_tickers=push_tickers,
                )
                recorded_decision_count = len(recorded_events)
            for item in candidate_realtime:
                item["decision_timeline"] = list_candidate_timeline(
                    store.collection(EVENT_COLLECTION),
                    str(item.get("ticker") or ""),
                    session_date=session_date,
                    limit=5,
                )

        depth_available = bool(source_used == "klineshare" and config.klineshare_include_depth)
        realtime_capabilities = {
            "quote": source_used is not None,
            "minute_kline": source_used == "klineshare",
            "depth": depth_available,
            "depth_status": (
                "available"
                if depth_available
                else "plan_not_enabled"
                if source_used == "klineshare"
                else "source_not_supported"
            ),
            "depth_note": (
                "当前 KlineShare Key 未启用 realtime_depth；实时价与 5 分钟 K 可正常使用。"
                if source_used == "klineshare" and not depth_available
                else None
            ),
        }

        return {
            **({'read_only': True, 'source_mode': 'cached_only'} if read_only else {}),
            "analysis_date": session_date,
            "session_date": session_date,
            "daily_baseline_date": baseline_date,
            "part1_analysis_date": baseline_date,
            "baseline_mode": "previous_completed_daily",
            "baseline_note": (
                f"盘中决策使用 {baseline_date} 已完成日线基线，"
                f"叠加 {session_date} 实时报价与分钟 K。"
            ),
            "benchmark_ticker": bm_ticker,
            "server_time": datetime.now().isoformat(timespec="seconds"),
            "part1_cached_at": part1_cached_at,
            "realtime_source": source_used,
            "realtime_source_requested": source,
            "realtime_capabilities": realtime_capabilities,
            "market_gate": gate,
            "benchmark_realtime": benchmark_realtime,
            "planned_levels": {
                "support_1": levels.get("support_1"),
                "support_1_source": levels.get("support_1_source"),
                "support_2": levels.get("support_2"),
                "support_2_source": levels.get("support_2_source"),
                "pressure_1": levels.get("pressure_1"),
                "pressure_1_source": levels.get("pressure_1_source"),
            },
            "pattern_forecast": pattern_forecast,
            "trend_summary": trend_summary,
            "triggered_levels": triggered_levels,
            "candidates_realtime": candidate_realtime,
            "recorded_decision_count": recorded_decision_count,
            "realtime_errors": realtime_errors,
            "minute_errors": minute_errors,
        }
    finally:
        store.close()


@ui_router.get("/api/v1/stock/panel")
def stock_panel(
    ticker: str,
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    period: str = "5",
    intraday_only: bool = False,
    strategy_id: str | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    """策略中立的单票事实面板，可选叠加一个显式策略。

    分钟数据源：东方财富直连（免费无 token）。
    默认事件：通用 K 线/量价事件；只有传入 strategy_id 才生成买卖计划。
    """
    import pandas as pd

    from shilun.common.db import MongoSnapshotStore
    from shilun.data.providers.minute_provider import MinuteClient, MinuteFetchError
    from shilun.data.providers.klineshare_provider import KlineShareClient, KlineShareConfig, KlineShareError
    from shilun.data.providers.tushare_realtime import TushareRealtimeClient, TushareRealtimeError
    from shilun.market.stock_events import (
        GENERIC_EVENT_META,
        detect_generic_events_daily,
        summarize_generic_events,
    )
    from shilun.strategies import (
        ExecutionStrategyContext,
        ExecutionStrategyError,
        get_execution_strategy,
        list_execution_strategies,
    )

    ticker_norm = ticker.upper().strip()
    if not ticker_norm:
        raise HTTPException(status_code=400, detail="ticker 参数必填")

    target_date = normalize_analysis_date(date)
    bm_ticker = benchmark_ticker.upper()
    selected_strategy_id = str(strategy_id or "").strip().lower()

    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置")

    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db,
                                   **({'initialize_indexes': False} if read_only else {}))
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 连接失败：{error}") from error

    try:
        errors: list[str] = []

        # 1. 分钟 K 线：配置 Key 时优先 KlineShare，失败回退 东方财富直连。
        minute_bars = None
        minute_source = None
        klineshare_client = None
        minute_frame = None
        if read_only:
            from shilun.market.minute_bars_store import COLLECTION_NAME, get_minute_bars
            archived_minutes = get_minute_bars(store.collection(COLLECTION_NAME), ticker_norm,
                                               target_date.replace('-', ''), interval=period) or []
            archived_minutes = [row for row in archived_minutes if str(row.get('datetime', ''))[:10] == target_date]
            if archived_minutes:
                minute_frame = pd.DataFrame(archived_minutes)
                minute_frame['datetime'] = pd.to_datetime(minute_frame['datetime'], errors='coerce')
                minute_frame = minute_frame.dropna(subset=['datetime'])
                minute_source = 'mongo_archive'
            errors.append('只读归档模式：不请求外部报价或分钟数据；未归档内容保持缺失，不挪用其他日期。')
        if not read_only and config.klineshare_api_key:
            klineshare_client = KlineShareClient(KlineShareConfig(
                api_key=config.klineshare_api_key,
                base_url=config.klineshare_base_url,
                timeout=config.klineshare_timeout,
            ))
            try:
                minute_frame = klineshare_client.fetch_kline(
                    ticker_norm,
                    period=int(period) * 60,
                    count=500,
                    adjust_type="none",
                )
                if minute_frame is not None and not minute_frame.empty:
                    minute_source = "klineshare"
            except KlineShareError as error:
                errors.append(f"klineshare_minute_bars: {error}")
        if not read_only and (minute_frame is None or minute_frame.empty):
            try:
                minute_frame = MinuteClient().fetch_minute_bars(
                    ticker_norm,
                    period=period, trade_date=target_date,
                )
                if minute_frame is not None and not minute_frame.empty:
                    minute_source = minute_frame.attrs.get("source", "eastmoney")
            except MinuteFetchError as error:
                errors.append(str(error))
        if minute_frame is not None and not minute_frame.empty:
            # 只保留最近 3 个交易日的分钟数据（避免负载过大）
            cutoff = minute_frame["datetime"].max() - pd.Timedelta(days=5)
            minute_frame = minute_frame[minute_frame["datetime"] > cutoff].reset_index(drop=True)
            minute_bars = minute_frame.to_dict("records")
            for row in minute_bars:
                row["datetime"] = pd.Timestamp(row["datetime"]).isoformat()
                for key in ("open", "high", "low", "close"):
                    row[key] = _round_num(row.get(key), 2)
                row["volume"] = int(_round_num(row.get("volume"), 0) or 0)
                row["amount"] = _round_num(row.get("amount"), 0)

        # 2. 日线 + 信号识别
        daily_bars = None
        records: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        signal_summary: dict[str, Any] = {}
        levels: dict[str, Any] = {}
        trade_plan: dict[str, Any] = {}
        buy_point_pattern_info: dict[str, Any] = {"pattern": "none", "label": "-"}
        active_buy_context: dict[str, Any] = {}

        if not intraday_only:
            # 从 Mongo 拿 60 天日线
            start_date = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
            records = store.raw_market.find_daily_bars(
                start_date=start_date, end_date=target_date, tickers=[ticker_norm]
            )
            if records:
                dfd = pd.DataFrame(records)
                dfd["date"] = pd.to_datetime(dfd["date"])
                dfd = dfd.sort_values("date").reset_index(drop=True)
                signals = detect_generic_events_daily(dfd, ticker=ticker_norm)
                signal_summary = summarize_generic_events(signals)

                # ── 主动买入结构 ────────────────────────────────
                # 拉最近 20 天资金流，计算最新和累计的主动/被动
                try:
                    flow_records = store.raw_market.find_moneyflow(
                        start_date=start_date, end_date=target_date, ts_code=ticker_norm
                    ) if hasattr(store.raw_market, 'find_moneyflow') else []
                    if flow_records:
                        flow_df = pd.DataFrame(flow_records)
                        for col in ("buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount",
                                    "buy_md_amount", "sell_md_amount", "buy_sm_amount", "sell_sm_amount"):
                            if col not in flow_df.columns:
                                flow_df[col] = 0.0
                            flow_df[col] = pd.to_numeric(flow_df[col], errors="coerce").fillna(0.0)
                        flow_df["date"] = pd.to_datetime(flow_df.get("trade_date", flow_df.get("date")), errors="coerce")
                        flow_df = flow_df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

                        if len(flow_df) > 0:
                            latest_flow = flow_df.iloc[-1]
                            main_buy = float(latest_flow.get("buy_elg_amount", 0)) + float(latest_flow.get("buy_lg_amount", 0))
                            main_sell = float(latest_flow.get("sell_elg_amount", 0)) + float(latest_flow.get("sell_lg_amount", 0))
                            retail_buy = float(latest_flow.get("buy_md_amount", 0)) + float(latest_flow.get("buy_sm_amount", 0))
                            retail_sell = float(latest_flow.get("sell_md_amount", 0)) + float(latest_flow.get("sell_sm_amount", 0))
                            total_active = main_buy + main_sell + retail_buy + retail_sell
                            active_buy_ratio_v = (main_buy + retail_buy) / total_active if total_active > 0 else 0.5
                            active_buy_structure_v = (main_buy - retail_buy) / total_active if total_active > 0 else 0.0

                            # 5 天累计
                            last5 = flow_df.tail(5)
                            cum_main_buy = float((last5["buy_elg_amount"] + last5["buy_lg_amount"]).sum())
                            cum_main_sell = float((last5["sell_elg_amount"] + last5["sell_lg_amount"]).sum())
                            cum_retail_buy = float((last5["buy_md_amount"] + last5["buy_sm_amount"]).sum())
                            cum_retail_sell = float((last5["sell_md_amount"] + last5["sell_sm_amount"]).sum())
                            cum_total = cum_main_buy + cum_main_sell + cum_retail_buy + cum_retail_sell
                            main_persist_days_5 = int((last5["buy_elg_amount"] + last5["buy_lg_amount"]
                                                        > last5["sell_elg_amount"] + last5["sell_lg_amount"]).sum())

                            active_buy_context = {
                                "date": pd.Timestamp(latest_flow["date"]).strftime("%Y-%m-%d"),
                                "main_active_buy": _round_num(main_buy, 2),
                                "main_active_sell": _round_num(main_sell, 2),
                                "retail_active_buy": _round_num(retail_buy, 2),
                                "retail_active_sell": _round_num(retail_sell, 2),
                                "main_net": _round_num(main_buy - main_sell, 2),
                                "retail_net": _round_num(retail_buy - retail_sell, 2),
                                "active_buy_ratio": _round_num(active_buy_ratio_v, 4),
                                "active_buy_structure": _round_num(active_buy_structure_v, 4),
                                "cum_main_buy_5d": _round_num(cum_main_buy, 2),
                                "cum_main_sell_5d": _round_num(cum_main_sell, 2),
                                "cum_main_net_5d": _round_num(cum_main_buy - cum_main_sell, 2),
                                "main_active_persist_days_5d": main_persist_days_5,
                                "label": _describe_active_buy(active_buy_ratio_v, active_buy_structure_v),
                            }
                except Exception as e:
                    errors.append(f"active_buy: {e}")

                # 序列化日线
                daily_bars = []
                for _, r in dfd.iterrows():
                    daily_bars.append({
                        "date": pd.Timestamp(r["date"]).strftime("%Y-%m-%d"),
                        "open": _round_num(r.get("open"), 2),
                        "high": _round_num(r.get("high"), 2),
                        "low": _round_num(r.get("low"), 2),
                        "close": _round_num(r.get("close"), 2),
                        "volume": int(_round_num(r.get("volume"), 0) or 0),
                        "amount": _round_num(r.get("amount"), 0),
                    })

                # 从日线算支撑压力
                closes = dfd["close"].astype(float)
                highs = dfd["high"].astype(float)
                lows = dfd["low"].astype(float)
                opens = dfd["open"].astype(float)
                volumes = dfd["volume"].astype(float) if "volume" in dfd.columns else None
                latest_close = float(closes.iloc[-1])
                # 阻力：最近 60 根内高于当前的历史高点
                recent_high = float(highs.tail(60).max())
                # 支撑分层（对齐 PRD 阶段 9）：MA5 > MA10 > MA20。
                # 60 日历史低点在这里**不再当作 support**——一旦价格穿破 MA5、MA10 到达
                # 60 日最低价，MA5 战法的入场时机就已经错过（东山精密 126 元 bug 的根源）。
                ma5 = float(closes.tail(5).mean())
                ma10 = float(closes.tail(10).mean()) if len(closes) >= 10 else ma5
                ma20 = float(closes.tail(20).mean()) if len(closes) >= 20 else ma10
                # support 取 close 之下最近的一层均线；如果全在 close 上方（弱势反弹），退回到 MA5
                support_candidates = [(ma5, "MA5"), (ma10, "MA10"), (ma20, "MA20")]
                below = [(v, l) for v, l in support_candidates if v > 0 and v <= latest_close * 1.005]
                if below:
                    support_value, support_source = max(below, key=lambda item: item[0])
                else:
                    support_value, support_source = ma5, "MA5"
                # PRD 阶段 9 权威买点 = 当前 close（trade_plan.entry_price）
                # 顺便把完整 trade_plan 也生成好，前端可以直接读，避免再从 levels 猜。
                # previous_high_60 / median_abs_return_20d 供 build_trade_plan 算 target/add_point
                previous_high_60 = float(highs.tail(60).max()) if len(highs) >= 20 else 0.0
                pct_chg = closes.pct_change().fillna(0.0)
                median_abs_return_20d = float(pct_chg.abs().tail(20).median()) if len(pct_chg) >= 5 else 0.02
                from shilun.market.ma5_features import build_trade_plan
                trade_plan_features = {
                    "close": latest_close,
                    "open": float(opens.iloc[-1]),
                    "ma5": ma5,
                    "ma8": float(closes.tail(8).mean()) if len(closes) >= 8 else ma5,
                    "ma10": ma10,
                    "ma20": ma20,
                    "previous_high_60": previous_high_60,
                    "median_abs_return_20d": median_abs_return_20d,
                }
                trade_plan = build_trade_plan(trade_plan_features)
                levels = {
                    "resistance": _round_num(recent_high, 2),
                    # support 现在语义是"MA5/MA10/MA20 里 close 之下最近一层"，不再是历史低点
                    "support": _round_num(support_value, 2),
                    "support_source": support_source,
                    "stop_loss_long": _round_num(ma5 * 0.98, 2),
                    "stop_loss_short": _round_num(recent_high * 1.02, 2),
                    "ma5": _round_num(ma5, 2),
                    "ma10": _round_num(ma10, 2),
                    "ma20": _round_num(ma20, 2),
                    "based_on_bars": len(dfd),
                }

                # Job C（§4.7 五买点体系）：stock_panel 直接从 sector_trends_cache 里
                # 查这只票的 buy_point_pattern，避免在这里重复算完整 features。
                # 缓存里没有（例如票不在候选池） → pattern="none"，前端徽章不显示。
                try:
                    if selected_strategy_id != "ma5_v0_2":
                        raise LookupError("未选择 MA5 策略")
                    cached_sectors = _load_sector_cache(store, target_date, bm_ticker, trend_lookback_days=60)
                    if cached_sectors and cached_sectors.get("payload"):
                        for c in (cached_sectors["payload"].get("candidates") or []):
                            if str(c.get("ticker") or "").upper() == ticker_norm:
                                pattern = c.get("buy_point_pattern") or "none"
                                buy_point_pattern_info = {
                                    "pattern": pattern,
                                    "label": c.get("buy_point_pattern_label") or "-",
                                    "context": c.get("buy_point_pattern_context"),
                                    "note": c.get("buy_point_pattern_note"),
                                    "strength": c.get("buy_point_pattern_strength"),
                                }
                                break
                except Exception:  # noqa: BLE001
                    pass

        # 3. 实时报价：KlineShare 批量获取个股 + 基准，失败后回退新浪。
        realtime = None
        realtime_source = None
        bm_rt_df = None
        rt_df = None
        if read_only:
            archived_quotes = _read_archived_quotes(store, [ticker_norm, bm_ticker], target_date)
            if ticker_norm in archived_quotes:
                rt_df = pd.DataFrame([archived_quotes[ticker_norm]])
                realtime_source = 'mongo_archive'
            if bm_ticker in archived_quotes:
                bm_rt_df = pd.DataFrame([archived_quotes[bm_ticker]])
        if klineshare_client is not None:
            try:
                combined = klineshare_client.fetch_realtime([ticker_norm, bm_ticker])
                rt_df = combined[combined["ticker"].str.upper() == ticker_norm]
                bm_rt_df = combined[combined["ticker"].str.upper() == bm_ticker]
                if rt_df is not None and not rt_df.empty:
                    realtime_source = "klineshare"
            except KlineShareError as error:
                errors.append(f"klineshare_realtime: {error}")
        ts_client = None if read_only else TushareRealtimeClient()
        if not read_only and (rt_df is None or rt_df.empty):
            try:
                rt_df = ts_client.fetch_stock_realtime_batch([ticker_norm])
                if rt_df is not None and not rt_df.empty:
                    realtime_source = "tushare_sina"
            except TushareRealtimeError as error:
                errors.append(f"realtime: {error.underlying}")
        if rt_df is not None and not rt_df.empty:
            row = rt_df.iloc[0].to_dict()
            realtime = {
                "current": _round_num(row.get("current")),
                "pct_chg": _round_num(row.get("pct_chg"), 4),
                "open": _round_num(row.get("open")),
                "high": _round_num(row.get("high")),
                "low": _round_num(row.get("low")),
                "prev_close": _round_num(row.get("prev_close")),
                "volume": row.get("volume"),
                "amount": _round_num(row.get("amount")),
                "name": row.get("name"),
                "trade_status": row.get("trade_status"),
                "update_at": row.get("update_at"),
                "quote_at": row.get("quote_at"),
                "fetched_at": row.get("fetched_at"),
                "timestamp_kind": row.get("timestamp_kind"),
            }

        # 4. 大盘穿透（该票 vs 大盘方向共振）
        market_sync = None
        if not read_only and (bm_rt_df is None or bm_rt_df.empty):
            try:
                bm_rt_df = ts_client.fetch_index_realtime([bm_ticker])
            except TushareRealtimeError as error:
                errors.append(f"market_sync: {error.underlying}")
                bm_rt_df = None
        try:
            if bm_rt_df is not None and not bm_rt_df.empty:
                bm_r = bm_rt_df.iloc[0].to_dict()
                bm_pct = float(bm_r.get("pct_chg") or 0)
                stock_pct = float((realtime or {}).get("pct_chg") or 0)
                if bm_pct > 0 and stock_pct > 0:
                    sync_status = "强于大盘" if stock_pct > bm_pct else "弱于大盘"
                    sync_direction = "sync_up"
                elif bm_pct < 0 and stock_pct < 0:
                    sync_status = "弱势跟跌" if stock_pct < bm_pct else "抗跌"
                    sync_direction = "sync_down"
                elif bm_pct > 0 and stock_pct < 0:
                    sync_status = "背离下行"
                    sync_direction = "diverge_down"
                elif bm_pct < 0 and stock_pct > 0:
                    sync_status = "背离上行"
                    sync_direction = "diverge_up"
                else:
                    sync_status = "同步震荡"
                    sync_direction = "flat"
                market_sync = {
                    "benchmark_name": bm_r.get("name"),
                    "benchmark_pct_chg": _round_num(bm_pct, 4),
                    "stock_pct_chg": _round_num(stock_pct, 4),
                    "sync_status": sync_status,
                    "sync_direction": sync_direction,
                    "strength_diff": _round_num(stock_pct - bm_pct, 4),
                }
        except Exception as e:  # noqa: BLE001
            errors.append(f"market_sync: {e}")

        # 5. 智能评级（0-100，聚合信号+趋势+RSI+涨跌幅动量）
        rating = _compute_stock_rating(signals, daily_bars or [], realtime)

        # 6. 股票基础信息
        basic = None
        try:
            from shilun.market.taxonomy import classify_stocks
            basic_list = classify_stocks(store, target_date, store.raw_market.find_stock_basic())
            for b in basic_list:
                if str(b.get("ts_code", "")).upper() == ticker_norm:
                    basic = {
                        "ticker": ticker_norm,
                        "name": b.get("name") or (realtime or {}).get("name", ""),
                        "industry": b.get("industry") or "",
                        "industry_code": b.get("industry_code"),
                        "taxonomy_date": b.get("taxonomy_date"),
                        "taxonomy_version": b.get("taxonomy_version"),
                        "classification_status": b.get("classification_status"),
                        "concepts": b.get("concepts", []),
                        "market": b.get("market") or "",
                        "list_date": b.get("list_date") or "",
                    }
                    break
        except Exception as e:
            errors.append(f"basic: {e}")

        strategy_analysis = None
        if selected_strategy_id:
            try:
                strategy = get_execution_strategy(selected_strategy_id)
                try:
                    market_gate = _load_market_gate(store, target_date, bm_ticker) or {}
                except Exception:  # noqa: BLE001
                    market_gate = {}
                strategy_analysis = strategy.evaluate_stock(
                    daily_bars or records,
                    context=ExecutionStrategyContext(
                        analysis_date=target_date,
                        benchmark_ticker=bm_ticker,
                        market_gate=market_gate,
                        sector_name=str((basic or {}).get("industry") or ""),
                    ),
                )
            except ExecutionStrategyError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error

        # Tushare moneyflow 是按成交方向和订单规模聚合的代理口径，不是逐笔 Level-2
        # 主动/被动成交还原。保留旧字段别名，避免已有调用方中断，但页面使用 moneyflow。
        moneyflow = {
            **active_buy_context,
            "source": "Tushare moneyflow",
            "classification": "按大/特大/中/小单买卖方向聚合",
            "active_passive_available": False,
            "methodology_note": "当前数据不能逐笔还原主动买入、主动卖出或被动承接，只能作为资金方向代理。",
        }
        strategy_trade_plan = (strategy_analysis or {}).get("trade_plan") or {}
        strategy_signal = (strategy_analysis or {}).get("signal") or {}

        return {
            **({'read_only': True, 'source_mode': 'cached_only'} if read_only else {}),
            "ticker": ticker_norm,
            "analysis_date": target_date,
            "benchmark_ticker": bm_ticker,
            "basic": basic,
            "realtime": realtime,
            "realtime_source": realtime_source,
            "market_sync": market_sync,
            "levels": levels if selected_strategy_id == "ma5_v0_2" else {},
            "trade_plan": strategy_trade_plan,
            # Job C（§4.7 五买点体系）：从 sector_trends_cache 查到的 buy_point_pattern。
            # 没有对应缓存时 pattern="none"，前端徽章不显示。
            "buy_point_pattern": strategy_signal.get("pattern") or buy_point_pattern_info.get("pattern") if selected_strategy_id else None,
            "buy_point_pattern_label": strategy_signal.get("pattern_label") or buy_point_pattern_info.get("label") if selected_strategy_id else None,
            "buy_point_pattern_context": buy_point_pattern_info.get("context") if selected_strategy_id else None,
            "buy_point_pattern_note": buy_point_pattern_info.get("note") if selected_strategy_id else None,
            "buy_point_pattern_strength": buy_point_pattern_info.get("strength") if selected_strategy_id else None,
            "rating": rating if selected_strategy_id == "ma5_v0_2" else {},
            "daily_bars": daily_bars,
            "minute_bars": minute_bars,
            "minute_source": minute_source,
            "signals": signals,
            "signal_summary": signal_summary,
            "signal_meta": GENERIC_EVENT_META,
            "active_buy": active_buy_context,
            "moneyflow": moneyflow,
            "generic_events": signals,
            "strategy_analysis": strategy_analysis,
            "selected_strategy_id": selected_strategy_id or None,
            "available_strategies": list_execution_strategies(),
            "analysis_scope": "strategy_plus_facts" if selected_strategy_id else "strategy_neutral_facts",
            "errors": errors,
            "server_time": datetime.now().isoformat(timespec="seconds"),
        }
    finally:
        store.close()


def _compute_stock_rating(signals: list[dict[str, Any]], daily_bars: list[dict[str, Any]], realtime: dict[str, Any] | None) -> dict[str, Any]:
    """把信号 + 近期动量 + 实时表现聚合成 0-100 的智能评级。

    返回：{short_term_score, long_term_score, short_label, long_label}
    """
    # 近 20 根信号权重
    recent_signals = signals[-30:] if len(signals) > 30 else signals
    bull = sum(1 for s in recent_signals if s["direction"] == "bullish")
    bear = sum(1 for s in recent_signals if s["direction"] == "bearish")
    warn = sum(1 for s in recent_signals if s["direction"] == "warning")

    # 短期评级（0-100, 50=中性）
    short = 50
    short += bull * 4 - bear * 4 - warn * 1
    if realtime:
        pct = float(realtime.get("pct_chg") or 0)
        short += int(pct * 100 * 3)  # 涨跌幅的 3 倍加权
    short = max(0, min(100, short))

    # 长期评级：基于近 20 日/60 日回报 + 均线结构
    long_ = 50
    if daily_bars and len(daily_bars) >= 20:
        recent = daily_bars[-20:]
        close_now = float(recent[-1]["close"] or 0)
        close_20d_ago = float(recent[0]["close"] or 0)
        if close_20d_ago > 0:
            return_20 = (close_now - close_20d_ago) / close_20d_ago
            long_ += int(return_20 * 100 * 2)
    if len(daily_bars) >= 60:
        c_now = float(daily_bars[-1]["close"] or 0)
        c_60 = float(daily_bars[-60]["close"] or 0)
        if c_60 > 0:
            return_60 = (c_now - c_60) / c_60
            long_ += int(return_60 * 100 * 1)
    long_ = max(0, min(100, long_))

    def _label(s):
        if s >= 75: return "强看涨"
        if s >= 60: return "弱看涨"
        if s >= 40: return "中性"
        if s >= 25: return "弱看跌"
        return "强看跌"

    return {
        "short_term_score": short,
        "short_term_label": _label(short),
        "long_term_score": long_,
        "long_term_label": _label(long_),
        "bull_events": bull,
        "bear_events": bear,
        "warning_events": warn,
    }


def _describe_active_buy(ratio: float, structure: float) -> str:
    """给主动买入结构一个人类可读标签（与 candidates._active_buy_label 一致）。"""
    if not ratio:
        return "无资金流数据"
    if ratio > 0.55 and structure > 0.1:
        return "主力强势买入"
    if ratio > 0.55 and structure < -0.05:
        return "散户情绪追涨"
    if ratio > 0.55:
        return "多空主动买入"
    if ratio < 0.45 and structure < -0.05:
        return "主力砸盘"
    if ratio < 0.45:
        return "被主动打压"
    return "多空平衡"


def _round_num(value: Any, digits: int = 2) -> float | None:
    """局部安全 round。"""
    try:
        if value is None:
            return None
        import math
        n = float(value)
        if math.isnan(n) or math.isinf(n):
            return None
        return round(n, digits)
    except (TypeError, ValueError):
        return None


def _detect_triggered_levels(
    *,
    current: float | None,
    high: float | None,
    low: float | None,
    open_: float | None,
    levels: dict[str, Any],
    pattern_forecast: dict[str, Any],
) -> list[dict[str, Any]]:
    """判断实时价是否触发了 PART1 预设的关键位（支撑/压力/斐波那契）。

    输出一个"触发事件"列表：每个事件含 level_type / price / delta（当前价距离）
    / status（held/broken/touched）/ note。
    """
    if current is None:
        return []

    events: list[dict[str, Any]] = []

    def _emit(level_type, source, level, tolerance_pct=0.003):
        if not level:
            return
        try:
            level = float(level)
        except (TypeError, ValueError):
            return
        delta_pct = (current - level) / level if level else 0

        if level_type in ("support_1", "support_2", "manual_support"):
            # 支撑判断
            touched_intraday = low is not None and low <= level * (1 + tolerance_pct)
            broke_close = current < level * (1 - tolerance_pct)
            if broke_close:
                events.append({
                    "level_type": "support_broken",
                    "level_label": source,
                    "price": _round_num(level),
                    "current": _round_num(current),
                    "delta_pct": _round_num(delta_pct, 4),
                    "severity": "high" if level_type == "support_2" else "medium",
                    "note": f"实时价 {current:.2f} 已跌破 {source} {level:.2f}（差 {delta_pct*100:+.2f}%）",
                })
            elif touched_intraday:
                events.append({
                    "level_type": "support_touched",
                    "level_label": source,
                    "price": _round_num(level),
                    "current": _round_num(current),
                    "delta_pct": _round_num(delta_pct, 4),
                    "severity": "medium",
                    "note": f"盘中低点触及 {source} {level:.2f} 后已收回，当前 {current:.2f}",
                })
        elif level_type in ("pressure_1", "manual_pressure"):
            # 压力判断
            touched_intraday = high is not None and high >= level * (1 - tolerance_pct)
            broke_up = current > level * (1 + tolerance_pct)
            if broke_up:
                events.append({
                    "level_type": "pressure_broken",
                    "level_label": source,
                    "price": _round_num(level),
                    "current": _round_num(current),
                    "delta_pct": _round_num(delta_pct, 4),
                    "severity": "high",
                    "note": f"实时价 {current:.2f} 已站上 {source} {level:.2f}（差 {delta_pct*100:+.2f}%）",
                })
            elif touched_intraday:
                events.append({
                    "level_type": "pressure_touched",
                    "level_label": source,
                    "price": _round_num(level),
                    "current": _round_num(current),
                    "delta_pct": _round_num(delta_pct, 4),
                    "severity": "low",
                    "note": f"盘中触及 {source} {level:.2f} 后回落，当前 {current:.2f}",
                })

    _emit("support_1", levels.get("support_1_source") or "支撑1", levels.get("support_1"))
    _emit("support_2", levels.get("support_2_source") or "支撑2", levels.get("support_2"))
    _emit("pressure_1", levels.get("pressure_1_source") or "压力1", levels.get("pressure_1"))

    # 斐波那契 / 波浪目标位
    fib_levels = (pattern_forecast.get("wave_analysis") or {}).get("fibonacci_levels", [])
    for fib in fib_levels:
        kind = fib.get("kind")
        label = fib.get("label")
        lvl = fib.get("level")
        if kind == "support":
            _emit("manual_support", label, lvl)
        elif kind in ("resistance", "target"):
            _emit("manual_pressure", label, lvl)

    return events


def _parse_selected_tickers(value: str | None) -> list[str]:
    if not value:
        return []
    tickers: list[str] = []
    seen: set[str] = set()
    for raw in str(value).replace("，", ",").split(","):
        ticker = raw.strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        tickers.append(ticker)
    return tickers[:50]


def _extract_candidates_from_cache(
    store,
    target_date: str,
    bm_ticker: str,
    gate: dict,
    limit: int,
    selected_tickers: list[str] | None = None,
) -> list[dict[str, Any]]:
    """从 PART2/PART3 缓存或其他集合里拿候选。

    实现策略：为避免耦合过重，这里直接从 PART1 已经计算过的候选池（如果 sectors
    API 曾被调用过就会落库）；否则返回空列表，前端会显示"暂无候选"提示。
    """
    from shilun.trading.market_facts import _latest_selection_pool_rows
    docs = sorted(_latest_selection_pool_rows(store, target_date),
                  key=lambda item: int(item.get("pool_rank") or item.get("rank") or 10_000))[:limit]

    selected = selected_tickers or []
    if selected:
        existing = {str(doc.get("ticker") or "").upper() for doc in docs}
        missing = [ticker for ticker in selected if ticker.upper() not in existing]
        if missing:
            stock_basic_docs: list[dict[str, Any]] = []
            try:
                stock_basic_docs = list(
                    store.collection("stock_basic").find(
                        {"ts_code": {"$in": missing}},
                        {"_id": 0, "ts_code": 1, "name": 1, "industry": 1},
                    )
                )
            except Exception:
                stock_basic_docs = []
            from shilun.market.taxonomy import classify_stocks
            stock_basic_docs = classify_stocks(store, target_date, stock_basic_docs)
            by_ticker = {str(item.get("ts_code") or "").upper(): item for item in stock_basic_docs}
            for offset, ticker in enumerate(missing, start=1):
                meta = by_ticker.get(ticker.upper(), {})
                docs.append({
                    "ticker": ticker,
                    "name": meta.get("name") or ticker,
                    "industry": meta.get("industry") or "",
                    "pool_status": "手动勾选趋势战法池",
                    "rank": 10_000 + offset,
                    "selected_pool": True,
                })

    from shilun.market.taxonomy import classify_stocks
    return [{**r, 'sector_name': r['industry'], 'sector_code': r['industry_code']}
            for r in classify_stocks(store, target_date, docs[: max(1, int(limit)) + len(selected)])]


def _mean_last(values: list[float], count: int) -> float | None:
    sample = [v for v in values[-count:] if v is not None]
    if not sample:
        return None
    return sum(sample) / len(sample)


def _first_positive_number(*values: Any) -> float | None:
    for value in values:
        try:
            if value is None:
                continue
            number = float(value)
            if number > 0:
                return number
        except (TypeError, ValueError):
            continue
    return None


_STALE_PRICE_TOLERANCE = 0.15  # 老候选缓存里价格偏离当前 close 超过 15% 视为陈旧不可用


def _sanity_price(value: float | None, close: float | None, tolerance: float = _STALE_PRICE_TOLERANCE) -> float | None:
    """把陈旧候选缓存里的价格与当前 close 做 sanity check。

    A 股正常波动一周之内不会偏离 15%，因此偏离过大的多是 stale cache 或者
    除权除息前的老口径（例如 603986.SH 曾在 243 元区间、现在 603 元），
    继续用它们做支撑/买点/压力会得到荒谬的结果。返回 None 表示丢弃这个值，
    让上游走 fallback（重新算 MA5/MA10）。
    """
    if value is None or close is None:
        return None
    try:
        v = float(value)
        c = float(close)
    except (TypeError, ValueError):
        return None
    if v <= 0 or c <= 0:
        return None
    lower = c * (1.0 - tolerance)
    upper = c * (1.0 + tolerance)
    if v < lower or v > upper:
        return None
    return v


def _nearest_below(price: float | None, candidates: list[tuple[float | None, str]]) -> tuple[float | None, str]:
    if not price:
        return None, ""
    valid = [(float(v), label) for v, label in candidates if v and float(v) > 0 and float(v) <= price * 1.012]
    if not valid:
        # 曾经在 fallback 里无差别接受所有 > 0 的候选，会让 stale 缓存里的 243 元支撑
        # 在当前 close=603 时被选出。改为不 fallback：返回 None，让上游用兜底 MA5。
        return None, ""
    return max(valid, key=lambda item: item[0])


def _nearest_above(price: float | None, candidates: list[tuple[float | None, str]]) -> tuple[float | None, str]:
    if not price:
        return None, ""
    valid = [(float(v), label) for v, label in candidates if v and float(v) >= price * 0.995]
    if not valid:
        # 同理，压力位的 fallback 也应该收紧：返回 None，让上游用 close*1.08 兜底。
        return None, ""
    return min(valid, key=lambda item: item[0])


def _enrich_intraday_candidate_plans(store, target_date: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """补齐盘中监控所需的趋势战法价位。

    优先使用 PART2/候选池已有字段；如果旧缓存没有这些字段，就用最近日线
    计算 MA5/MA8、20 日高低点和保守止损，避免盘中监控只剩实时价格。
    """
    if not candidates:
        return []
    tickers = [str(item.get("ticker") or "").upper() for item in candidates if item.get("ticker")]
    start_date = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=120)).strftime("%Y-%m-%d")
    bars_by_ticker: dict[str, list[dict[str, Any]]] = {}
    if tickers:
        try:
            bars = store.raw_market.find_daily_bars(start_date=start_date, end_date=target_date, tickers=tickers)
            for row in bars:
                ticker = str(row.get("ticker") or row.get("ts_code") or "").upper()
                if ticker:
                    bars_by_ticker.setdefault(ticker, []).append(row)
            for rows in bars_by_ticker.values():
                rows.sort(key=lambda item: str(item.get("date") or item.get("trade_date") or ""))
        except Exception:
            bars_by_ticker = {}

    enriched: list[dict[str, Any]] = []
    for candidate in candidates:
        item = {**candidate}
        ticker = str(item.get("ticker") or "").upper()
        rows = bars_by_ticker.get(ticker, [])
        closes: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        latest: dict[str, Any] = rows[-1] if rows else {}
        for row in rows:
            close = _round_num(row.get("close"))
            high = _round_num(row.get("high"))
            low = _round_num(row.get("low"))
            if close is not None:
                closes.append(close)
            if high is not None:
                highs.append(high)
            if low is not None:
                lows.append(low)

        close = _first_positive_number(item.get("close"), latest.get("close"))
        ma5 = _first_positive_number(item.get("ma5"), _mean_last(closes, 5))
        ma8 = _first_positive_number(item.get("ma8"), _mean_last(closes, 8), ma5)
        ma10_val = _mean_last(closes, 10)
        ma20_val = _mean_last(closes, 20)
        high20 = max(highs[-20:]) if highs else None

        # sanity check：老候选缓存里的 support/pressure/buy 可能是几周前的老口径，
        # 偏离当前 close 超过 15% 就丢弃，避免出现 close=603 时用 support=243 的荒谬情形。
        cached_support = _sanity_price(_first_positive_number(item.get("support_price")), close)
        cached_pressure = _sanity_price(_first_positive_number(item.get("pressure_price")), close)
        cached_predicted_buy = _sanity_price(_first_positive_number(item.get("predicted_buy_price"), item.get("entry_price")), close)

        # 支撑口径：只用均线（MA5/MA8/MA10/MA20），不用 20 日低点。
        # 因为一旦价格穿破 MA5 到达 20 日低点，MA5 战法的入场时机已经错过。
        support_price, support_source = _nearest_below(
            close,
            [
                (cached_support, item.get("support_source") or "候选支撑"),
                (ma5, "MA5"),
                (ma8, "MA8"),
                (ma10_val, "MA10"),
                (ma20_val, "MA20"),
            ],
        )
        # 如果所有均线都在 close 上方（弱势反弹），退化到 MA5 作参考。
        if support_price is None and ma5:
            support_price, support_source = ma5, "MA5"
        pressure_price, pressure_source = _nearest_above(
            close,
            [
                (cached_pressure, item.get("pressure_source") or "候选压力"),
                (high20, "20日高点"),
                (close * 1.08 if close else None, "8%目标"),
            ],
        )
        # 压力兜底：如果没有任何候选高于 close，用 close*1.08。
        if pressure_price is None and close:
            pressure_price, pressure_source = close * 1.08, "8%目标"
        # 买点下界：v0.2 战法要求买点不能显著低于 MA5，允许 MA5*0.995 的小幅低吸。
        ma5_floor = ma5 * 0.995 if ma5 else None
        predicted_buy = _first_positive_number(cached_predicted_buy, support_price, ma5, close)
        if ma5_floor and predicted_buy and predicted_buy < ma5_floor:
            predicted_buy = ma5_floor
        stop_loss = _first_positive_number(item.get("stop_loss"), support_price * 0.98 if support_price else None, ma5 * 0.97 if ma5 else None)
        expected_sell = _first_positive_number(
            item.get("expected_sell_price"),
            pressure_price if pressure_price and predicted_buy and pressure_price > predicted_buy * 1.02 else None,
            predicted_buy * 1.08 if predicted_buy else None,
        )

        item.update(
            {
                "close": _round_num(close),
                "ma5": _round_num(ma5),
                "ma8": _round_num(ma8),
                "predicted_buy_price": _round_num(predicted_buy),
                "predicted_buy_label": item.get("predicted_buy_label") or "趋势战法计划买点",
                "support_price": _round_num(support_price),
                "support_source": support_source or item.get("support_source") or "",
                "pressure_price": _round_num(pressure_price),
                "pressure_source": pressure_source or item.get("pressure_source") or "",
                "expected_sell_price": _round_num(expected_sell),
                "expected_sell_label": item.get("expected_sell_label") or "先看压力位，突破后再上移止盈",
                "stop_loss": _round_num(stop_loss),
                "trade_plan": {
                    "buy_price": _round_num(predicted_buy),
                    "buy_label": item.get("predicted_buy_label") or "趋势战法计划买点",
                    "support_price": _round_num(support_price),
                    "support_source": support_source,
                    "pressure_price": _round_num(pressure_price),
                    "pressure_source": pressure_source,
                    "ma5": _round_num(ma5),
                    "ma8": _round_num(ma8),
                    "stop_loss": _round_num(stop_loss),
                    "expected_sell_price": _round_num(expected_sell),
                    "expected_sell_label": item.get("expected_sell_label") or "先看压力位，突破后再上移止盈",
                    "source": "candidate_cache" if item.get("predicted_buy_price") else "daily_bar_fallback",
                },
            }
        )
        enriched.append(item)
    return enriched


def _candidate_signal_event(
    code: str,
    title: str,
    direction: str,
    color: str,
    severity: str,
    action: str,
    detail: str,
    *,
    price: float | None = None,
    delta_pct: float | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "direction": direction,
        "color": color,
        "severity": severity,
        "action": action,
        "detail": detail,
        "price": _round_num(price),
        "delta_pct": _round_num(delta_pct, 4),
    }


def _build_candidate_intraday_events(item: dict[str, Any], benchmark_pct_chg: float | None) -> list[dict[str, Any]]:
    realtime = item.get("realtime") or {}
    plan = item.get("trade_plan") or {}
    current = _first_positive_number(realtime.get("current"))
    if not current:
        return [
            _candidate_signal_event(
                "realtime_missing",
                "实时行情缺失",
                "neutral",
                "gray",
                "low",
                "先不动作，等待实时源恢复。",
                "当前候选票未拿到实时行情，只保留计划价位，不做触发判断。",
            )
        ]

    high = _first_positive_number(realtime.get("high"), current)
    low = _first_positive_number(realtime.get("low"), current)
    pct_chg = realtime.get("pct_chg")
    try:
        stock_pct_chg = float(pct_chg) if pct_chg is not None else None
    except (TypeError, ValueError):
        stock_pct_chg = None

    events: list[dict[str, Any]] = []
    buy = _first_positive_number(plan.get("buy_price"), item.get("predicted_buy_price"))
    support = _first_positive_number(plan.get("support_price"), item.get("support_price"))
    pressure = _first_positive_number(plan.get("pressure_price"), item.get("pressure_price"))
    ma8 = _first_positive_number(plan.get("ma8"), item.get("ma8"))
    stop_loss = _first_positive_number(plan.get("stop_loss"), item.get("stop_loss"))
    expected_sell = _first_positive_number(plan.get("expected_sell_price"), item.get("expected_sell_price"))

    if stop_loss and current <= stop_loss:
        events.append(
            _candidate_signal_event(
                "stop_loss_broken",
                "止损触发",
                "bearish",
                "green",
                "high",
                "先按风控处理，不把下跌当低吸。",
                f"现价 {current:.2f} 已低于止损位 {stop_loss:.2f}。",
                price=stop_loss,
                delta_pct=(current - stop_loss) / stop_loss,
            )
        )
    if support and low and low <= support * 1.005:
        reclaimed = current >= support
        events.append(
            _candidate_signal_event(
                "support_reclaimed" if reclaimed else "support_broken",
                "支撑回收" if reclaimed else "跌破支撑",
                "bullish" if reclaimed else "bearish",
                "red" if reclaimed else "green",
                "medium" if reclaimed else "high",
                "回收支撑只说明承接出现，仍需看收盘和大盘 gate。" if reclaimed else "支撑失守，先降低主动性。",
                f"盘中最低 {low:.2f} 触及支撑 {support:.2f}，当前 {current:.2f}。",
                price=support,
                delta_pct=(current - support) / support,
            )
        )
    if buy and low and low <= buy * 1.006 and current >= buy * 0.995:
        events.append(
            _candidate_signal_event(
                "buy_zone_touched",
                "计划买点触达",
                "bullish",
                "red",
                "medium",
                "进入观察买点，不等于立刻买；优先看分时承接和收盘确认。",
                f"计划买点 {buy:.2f} 已进入盘中触达区间，当前 {current:.2f}。",
                price=buy,
                delta_pct=(current - buy) / buy,
            )
        )
    if ma8 and low and low <= ma8 * 1.005 and current >= ma8:
        events.append(
            _candidate_signal_event(
                "ma8_reclaim",
                "MA8 回踩确认",
                "bullish",
                "red",
                "medium",
                "趋势线战法关注点：回踩不破且收回，优先观察次级买点。",
                f"MA8 {ma8:.2f} 被盘中测试后收回，当前 {current:.2f}。",
                price=ma8,
                delta_pct=(current - ma8) / ma8,
            )
        )
    if pressure and high and high >= pressure * 0.995:
        broke = current >= pressure
        events.append(
            _candidate_signal_event(
                "pressure_breakout" if broke else "pressure_touched",
                "突破压力" if broke else "触压回落",
                "bullish" if broke else "neutral",
                "red" if broke else "gray",
                "high" if broke else "medium",
                "放量站上压力才算升级；若回落则按压力处理。" if not broke else "突破后不要追太急，等回踩确认或用止盈保护。",
                f"压力位 {pressure:.2f}，盘中最高 {high:.2f}，当前 {current:.2f}。",
                price=pressure,
                delta_pct=(current - pressure) / pressure,
            )
        )
    if expected_sell and current >= expected_sell * 0.995:
        events.append(
            _candidate_signal_event(
                "target_reached",
                "接近预计售出位",
                "neutral",
                "gray",
                "medium",
                "优先兑现或上移止盈，不把目标位当追高理由。",
                f"预计售出位 {expected_sell:.2f}，当前 {current:.2f}。",
                price=expected_sell,
                delta_pct=(current - expected_sell) / expected_sell,
            )
        )

    if stock_pct_chg is not None and benchmark_pct_chg is not None:
        relative = stock_pct_chg - benchmark_pct_chg
        if relative >= 0.02:
            events.append(
                _candidate_signal_event(
                    "stronger_than_market",
                    "强于大盘",
                    "bullish",
                    "red",
                    "medium",
                    "强于大盘说明资金承接更好，可优先放进观察队列。",
                    f"个股涨幅 {stock_pct_chg * 100:+.2f}%，大盘 {benchmark_pct_chg * 100:+.2f}%，相对强弱 {relative * 100:+.2f}%。",
                    delta_pct=relative,
                )
            )
        elif relative <= -0.02:
            events.append(
                _candidate_signal_event(
                    "weaker_than_market",
                    "弱于大盘",
                    "bearish",
                    "green",
                    "medium",
                    "弱于大盘时降低优先级，除非有明确支撑回收。",
                    f"个股涨幅 {stock_pct_chg * 100:+.2f}%，大盘 {benchmark_pct_chg * 100:+.2f}%，相对强弱 {relative * 100:+.2f}%。",
                    delta_pct=relative,
                )
            )

    open_verified = item.get("open_verified") or {}
    if open_verified.get("status") == "strong":
        events.append(
            _candidate_signal_event(
                "open_strength",
                "强于开盘",
                "bullish",
                "red",
                "low",
                "开盘后继续走强，说明短线承接尚可。",
                "当前涨幅高于开盘跳空幅度，开盘承接暂时有效。",
            )
        )
    elif open_verified.get("status") == "weak":
        events.append(
            _candidate_signal_event(
                "open_weakness",
                "弱于开盘",
                "bearish",
                "green",
                "low",
                "高开低走或开盘承接不足，避免追涨。",
                "当前涨幅明显低于开盘跳空幅度，开盘强度被削弱。",
            )
        )

    if not events:
        events.append(
            _candidate_signal_event(
                "no_trigger",
                "未触发关键价位",
                "neutral",
                "gray",
                "low",
                "继续等待计划买点、支撑/压力或相对强弱信号。",
                "实时价格尚未触发趋势战法关键条件。",
            )
        )
    priority = {"high": 0, "medium": 1, "low": 2}
    events.sort(key=lambda event: (priority.get(event.get("severity"), 9), event.get("color") == "gray"))
    return events


def _candidate_technical_rating(events: list[dict[str, Any]]) -> dict[str, Any]:
    codes = {event.get("code") for event in events}
    if {"stop_loss_broken", "support_broken"} & codes:
        return {"label": "弱势看跌", "score": 25, "tone": "bearish"}
    if {"pressure_breakout", "buy_zone_touched", "support_reclaimed", "ma8_reclaim"} & codes:
        if "stronger_than_market" in codes:
            return {"label": "强势看涨", "score": 82, "tone": "bullish"}
        return {"label": "弱势看涨", "score": 66, "tone": "bullish"}
    if "weaker_than_market" in codes:
        return {"label": "弱于市场", "score": 42, "tone": "bearish"}
    return {"label": "中性观察", "score": 55, "tone": "neutral"}


def _merge_candidates_with_realtime(candidates: list[dict[str, Any]], stock_df, benchmark_realtime: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """把候选票和实时行情合并。"""
    benchmark_pct = None
    if benchmark_realtime:
        try:
            benchmark_pct = float(benchmark_realtime.get("pct_chg"))
        except (TypeError, ValueError):
            benchmark_pct = None
    if stock_df is None or stock_df.empty:
        return [
            {
                **c,
                "realtime": None,
                "open_verified": None,
                "signal_events": _build_candidate_intraday_events({**c, "realtime": None, "open_verified": None}, benchmark_pct),
                "technical_rating": {"label": "实时缺失", "score": 0, "tone": "neutral"},
            }
            for c in candidates
        ]
    rt = {row["ticker"]: row for _, row in stock_df.iterrows()}
    result = []
    for c in candidates:
        r = rt.get(c["ticker"])
        realtime = None
        open_verified = None
        if r is not None:
            realtime = {
                "current": _round_num(r.get("current")),
                "pct_chg": _round_num(r.get("pct_chg"), 4),
                "open": _round_num(r.get("open")),
                "high": _round_num(r.get("high")),
                "low": _round_num(r.get("low")),
                "prev_close": _round_num(r.get("prev_close")),
                "volume": _round_num(r.get("volume")),
                "volume_ratio": _round_num(r.get("volume_ratio"), 2),
                "amount": _round_num(r.get("amount")),
                "trade_status": r.get("trade_status"),
                "source": r.get("source"),
                "depth_bids": r.get("depth_bids") if isinstance(r.get("depth_bids"), list) else [],
                "depth_asks": r.get("depth_asks") if isinstance(r.get("depth_asks"), list) else [],
                "inner_volume": _round_num(r.get("inner_volume")),
                "outer_volume": _round_num(r.get("outer_volume")),
                "update_at": r.get("update_at"),
                "quote_at": r.get("quote_at"),
                "fetched_at": r.get("fetched_at"),
                "timestamp_kind": r.get("timestamp_kind"),
            }
            # 开盘验证：开盘价 > 昨收 = 高开，可能是好信号
            try:
                open_v = float(r.get("open") or 0)
                prev_close_v = float(r.get("prev_close") or 0)
                current_v = float(r.get("current") or 0)
                if prev_close_v > 0:
                    open_pct = (open_v - prev_close_v) / prev_close_v
                    current_pct = (current_v - prev_close_v) / prev_close_v
                    open_verified = {
                        "open_gap_pct": _round_num(open_pct, 4),
                        "current_pct": _round_num(current_pct, 4),
                        "held_above_open": current_v >= open_v * 0.99,
                        "status": (
                            "strong" if current_pct > open_pct + 0.005
                            else "weak" if current_pct < open_pct - 0.01
                            else "flat"
                        ),
                    }
            except (TypeError, ValueError):
                pass

        merged = {**c, "realtime": realtime, "open_verified": open_verified}
        events = _build_candidate_intraday_events(merged, benchmark_pct)
        rating = _candidate_technical_rating(events)
        merged["signal_events"] = events
        merged["technical_rating"] = rating
        merged["primary_event"] = events[0] if events else None
        merged["market_alignment"] = {
            "benchmark_pct_chg": _round_num(benchmark_pct, 4),
            "stock_pct_chg": realtime.get("pct_chg") if realtime else None,
            "relative_strength": (
                _round_num(float(realtime.get("pct_chg")) - benchmark_pct, 4)
                if realtime and realtime.get("pct_chg") is not None and benchmark_pct is not None
                else None
            ),
        }
        result.append(merged)
    return result


@ui_router.post("/api/v1/daily-push")
def daily_push(payload: DailyPushHttpRequest) -> dict[str, Any]:
    from shilun.jobs.daily_push_job import DailyPushJob, DailyPushRequest

    try:
        result = DailyPushJob().run(
            DailyPushRequest(
                target_date=payload.target_date,
                dry_run=payload.dry_run,
                message_top_k=payload.message_top_k,
                candidate_pool_top_k=payload.candidate_pool_top_k,
                include_candidate_pool=payload.include_candidate_pool,
                allow_snapshot_fallback=payload.allow_snapshot_fallback,
                fallback_latest_local=payload.fallback_latest_local,
            )
        )
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Daily push failed: {error}") from error
    return {
        "analysis_date": result.analysis_date,
        "data_source": result.data_source,
        "pushed_channels": result.pushed_channels,
        "failed_channels": result.failed_channels,
        "csv_path": str(result.csv_path),
        "report_path": str(result.report_path),
        "message_text": result.message_text,
    }


@analyze_router.get("/api/v1/analyze")
def analyze(ticker: str, date: str, allow_tushare_fallback: bool = False) -> dict:
    """
    DS-friendly Mongo-first entry:
    user provides ticker + date; synced Mongo data feeds pipeline analysis.
    """
    try:
        return analysis_service.analyze(
            AnalyzeRequest(
                ticker=ticker,
                analysis_date=date,
                allow_tushare_fallback=allow_tushare_fallback,
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Analyze failed: {error}") from error


def normalize_analysis_date(value: str | None = None, *, now: datetime | None = None) -> str:
    if value is None:
        return default_analysis_date(now)
    target = datetime.strptime(value, "%Y-%m-%d")
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target.strftime("%Y-%m-%d")


def default_analysis_date(now: datetime | None = None) -> str:
    target = now or datetime.now()
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    weekday_fallback = target.strftime("%Y-%m-%d")
    if now is not None:
        return weekday_fallback

    config = load_config()
    if not config.mongo_uri:
        return weekday_fallback
    client = None
    try:
        from shilun.common.db import get_mongo_client

        client = get_mongo_client(config.mongo_uri)
        latest = list(
            client[config.mongo_db]["trade_calendar"]
            .find(
                {
                    "is_open": 1,
                    "cal_date": {"$lte": weekday_fallback.replace("-", "")},
                },
                {"_id": 0, "cal_date": 1},
            )
            .sort("cal_date", -1)
            .limit(1)
        )
        normalized = _daily_record_date({"date": latest[0]["cal_date"]}) if latest else None
        return normalized or weekday_fallback
    except Exception:
        return weekday_fallback
    finally:
        if client is not None:
            client.close()


def parse_telegram_command(text: str | None, fallback_date: str | None = None) -> tuple[str, str]:
    if not text:
        raise ValueError("消息为空，请发送 /analyze 股票代码，或 /analyze 股票代码 YYYY-MM-DD。")

    parts = text.strip().split()
    command = parts[0].split("@")[0].lower()
    if command not in {"/analyze", "/a"}:
        raise ValueError("暂只支持 /analyze 股票代码 [日期] 命令。")
    if len(parts) < 2:
        raise ValueError("缺少股票代码。请使用 /analyze 600132.SH 或 /analyze 600132.SH 2026-03-25。")

    ticker = parts[1].upper()
    analysis_date = parts[2] if len(parts) >= 3 else (fallback_date or default_analysis_date())
    try:
        datetime.strptime(analysis_date, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError("日期格式必须是 YYYY-MM-DD。") from error
    return ticker, analysis_date


def build_telegram_text(result: dict[str, Any]) -> str:
    if result.get("explanation"):
        return f"{result['ticker']} {result['date']}\n{result['explanation']}"

    snapshot = result["snapshot"]
    decision = result["decision"]
    structure = snapshot.get("structure_assessment") or {}
    market_lines = (snapshot.get("evidence_sections") or {}).get("market") or ["当前没有可用市场基准上下文"]
    support = _fmt_price(snapshot.get("support_main"))
    pressure = _fmt_price(snapshot.get("pressure_main"))
    invalidation = _fmt_price(snapshot.get("invalidation_level"))
    position_state = (snapshot.get("trigger_context") or {}).get("position_state", "unknown")
    volume_pattern = (snapshot.get("trigger_context") or {}).get("volume_pattern", "unknown")
    entry_probability = snapshot.get("entry_probability")
    entry_zone = snapshot.get("entry_zone", "unknown")

    lines = [
        f"{result['ticker']} {result['date']}",
        f"【总览】当前更偏 {_translate(decision.get('conclusion_label'))}。",
        (
            f"【趋势判断】结构 {_translate(snapshot.get('structure_type'))} / {_translate(structure.get('structure_stage'))}；"
            f"确认 {_translate(structure.get('confirmation_state'))} ({structure.get('confirmation_score', '-')})；"
            f"关键位 支撑 {support} / 确认区 {pressure} / 失效 {invalidation}。"
        ),
        (
            f"【量价关系】位置 {_translate(position_state)}；量价类型 {_translate(volume_pattern)}；"
            f"相对强弱 {market_lines[0]}。"
        ),
        (
            f"【概率矩阵】延续 {snapshot.get('p_continue_10d', 0):.2f}｜失效 {snapshot.get('p_fail_5d', 0):.2f}"
            f"｜承接 {snapshot.get('p_acceptance_1d', 0) or 0:.2f}｜快败 {snapshot.get('p_fail_fast_3d', 0) or 0:.2f}"
            f"｜入场 {(entry_probability or 0):.2f}({_translate(entry_zone)})。"
        ),
        (
            f"【动作建议】未持仓 {_translate_action(decision.get('watching_action'))}；"
            f"已持仓 {_translate_action(decision.get('holding_action'))}；"
            f"若 {'；'.join(decision.get('confirmation_needed') or ['当前条件已基本齐备'])}，则维持当前判断。"
        ),
    ]
    return "\n".join(lines)


def build_telegram_reply(chat_id: int, text: str) -> dict[str, Any]:
    return {"method": "sendMessage", "chat_id": chat_id, "text": text}


def get_telegram_client() -> TelegramBotClient | None:
    config = load_config()
    if not config.telegram_bot_token:
        return None
    return TelegramBotClient(
        bot_token=config.telegram_bot_token,
        api_base=config.telegram_api_base,
        timeout=config.tushare_timeout,
    )


def verify_webhook_secret(secret_header: str | None) -> None:
    config = load_config()
    if not config.telegram_webhook_secret:
        return
    if secret_header != config.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret.")


def is_chat_allowed(chat_id: int) -> bool:
    allowed = load_config().telegram_allowed_chat_ids
    return not allowed or chat_id in allowed


@telegram_router.get("/api/v1/telegram/analyze")
def telegram_analyze(ticker: str, date: str | None = None, allow_tushare_fallback: bool = False) -> dict[str, Any]:
    analysis_date = date or default_analysis_date()
    try:
        result = analysis_service.analyze(
            AnalyzeRequest(
                ticker=ticker.upper(),
                analysis_date=analysis_date,
                allow_tushare_fallback=allow_tushare_fallback,
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Analyze failed: {error}") from error
    return {"text": build_telegram_text(result), "result": result}


@telegram_router.get("/api/v1/telegram/get-me")
def telegram_get_me() -> dict[str, Any]:
    client = get_telegram_client()
    if client is None:
        raise HTTPException(status_code=400, detail="Telegram bot token is not configured.")
    try:
        return client.get_me()
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@telegram_router.post("/api/v1/telegram/set-webhook")
def telegram_set_webhook(payload: TelegramWebhookSetupRequest) -> dict[str, Any]:
    config = load_config()
    client = get_telegram_client()
    if client is None:
        raise HTTPException(status_code=400, detail="Telegram bot token is not configured.")

    public_base_url = payload.public_base_url or config.telegram_webhook_base_url
    if not public_base_url:
        raise HTTPException(status_code=400, detail="public_base_url is required, or set SHILUN_TELEGRAM_WEBHOOK_BASE_URL.")

    webhook_url = public_base_url.rstrip("/") + "/api/v1/telegram/webhook"
    try:
        return client.set_webhook(
            webhook_url=webhook_url,
            secret_token=config.telegram_webhook_secret,
            drop_pending_updates=payload.drop_pending_updates,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@telegram_router.post("/api/v1/telegram/webhook")
def telegram_webhook(
    update: TelegramUpdate,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    verify_webhook_secret(x_telegram_bot_api_secret_token)
    if update.message is None:
        return {"ok": True}
    if not is_chat_allowed(update.message.chat.id):
        return {"ok": True}

    try:
        ticker, analysis_date = parse_telegram_command(update.message.text, fallback_date=default_analysis_date())
        result = analysis_service.analyze(AnalyzeRequest(ticker=ticker, analysis_date=analysis_date))
        text = build_telegram_text(result)
        client = get_telegram_client()
        if client is None:
            return build_telegram_reply(update.message.chat.id, text)
        client.send_message(update.message.chat.id, text)
        return {"ok": True}
    except ValueError as error:
        client = get_telegram_client()
        if client is None:
            return build_telegram_reply(update.message.chat.id, str(error))
        client.send_message(update.message.chat.id, str(error))
        return {"ok": True}
    except Exception as error:  # pragma: no cover
        client = get_telegram_client()
        if client is None:
            return build_telegram_reply(update.message.chat.id, f"分析失败: {error}")
        client.send_message(update.message.chat.id, f"分析失败: {error}")
        return {"ok": True}


def create_app() -> FastAPI:
    from shilun.api.ops import router as ops_router
    from shilun.api.review import router as review_router
    from shilun.api.trade_decision import router as trade_decision_router
    from shilun.api.trade_journal import router as trade_journal_router
    from shilun.api.security import install_basic_auth

    app = FastAPI(title="Shilun Standalone", version="0.1.0")
    @app.exception_handler(PyMongoError)
    async def database_unavailable(request, error):
        logging.getLogger(__name__).warning("Database request failed: %s %s (%s)",
                                           request.method, request.url.path, type(error).__name__)
        detail = ("云端数据库读取超时或连接中断，请检查网络后重试。"
                  if request.method in {"GET", "HEAD"} else
                  "数据库连接异常，请先核对记录是否已保存，再决定是否重试。")
        return JSONResponse(status_code=503, content={"detail": detail})

    install_basic_auth(app, load_config())
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(ops_router)
    app.include_router(review_router)
    app.include_router(trade_decision_router)
    app.include_router(trade_journal_router)
    app.include_router(ui_router)
    app.include_router(analyze_router)
    app.include_router(telegram_router)
    return app


def _fmt_price(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "待确认"


def _translate(value: str | None) -> str:
    mapping = {
        "high_quality_continuation": "高质量延续",
        "confirmation_needed": "需要确认",
        "momentum_but_heavy_overhead": "上冲有动能但上方较重",
        "defense_first": "防守优先",
        "trend_continue": "趋势延续",
        "breakout_pullback": "突破后回踩",
        "weak_rebound": "弱反弹",
        "range_pivot": "区间枢轴",
        "breakout_confirmed": "突破确认",
        "breakout_attempt": "突破尝试",
        "trend_pressing_high": "趋势逼近前高",
        "trend_pullback": "趋势回踩",
        "trend_advancing": "趋势推进",
        "distribution": "高位分布",
        "breakdown": "向下破位",
        "range_rotation": "区间轮动",
        "rebound_repair": "反弹修复",
        "confirmed": "已确认",
        "pending": "待确认",
        "failed": "确认失败",
        "ready": "可执行",
        "candidate": "候选",
        "watch": "观察中",
        "avoid": "回避",
        "low_base": "低位蓄势",
        "rising": "上升途中",
        "high_zone": "高位区",
        "downtrend": "下跌途中",
        "gentle_expand": "温和放量",
        "impulsive_spike": "脉冲放量",
        "pullback_shrink": "回调缩量",
        "high_level_stall": "高位滞涨缩量",
        "down_shrink": "下跌途中缩量",
        "strong_up": "强势上涨",
        "weak_up": "偏弱上涨",
        "range": "震荡",
        "weak_down": "偏弱下行",
        "risk_reversal": "风险反转",
    }
    return mapping.get(value or "", value or "未知")


def _translate_action(value: str | None) -> str:
    mapping = {
        "buy_on_pullback": "等回踩确认后再跟随",
        "buy_on_confirmation": "确认后再跟随",
        "wait_for_confirmation": "先等量价确认",
        "avoid_chasing": "不追高",
        "stand_aside": "先观望",
        "hold_above_support": "守住支撑继续持有",
        "trim_on_failed_confirmation": "确认失败先减仓",
        "scale_out_into_strength": "冲高分批兑现",
        "exit_on_invalidation": "跌破失效位退出",
    }
    return mapping.get(value or "", value or "待确认")


app = create_app()



__all__ = [
    "DataSyncHttpRequest",
    "DailyPushHttpRequest",
    "HoldingCloseHttpRequest",
    "HoldingCreateHttpRequest",
    "HoldingPatchHttpRequest",
    "T1SettlementHttpRequest",
    "TradeFillHttpRequest",
    "TradeOrderCancelHttpRequest",
    "TradeOrderHttpRequest",
    "TelegramBotClient",
    "TelegramChat",
    "TelegramMessage",
    "TelegramUpdate",
    "TelegramWebhookSetupRequest",
    "analyze",
    "analyze_router",
    "app",
    "build_telegram_text",
    "close_holding_endpoint",
    "create_app",
    "create_holding",
    "create_trading_order",
    "create_trading_order_fill",
    "control_center",
    "data_status",
    "data_sync",
    "daily_check_holdings_endpoint",
    "delete_holding_endpoint",
    "market_permission",
    "market_sectors",
    "get_holding_detail",
    "get_holding_events_endpoint",
    "get_holdings",
    "get_trading_order",
    "get_trading_orders",
    "get_trading_workbench",
    "parse_telegram_command",
    "push_channel_status",
    "cancel_trading_order",
    "settle_trading_t1_positions",
    "telegram_router",
    "tushare_health",
    "update_holding",
    "ui_router",
]


_routes_module = types.ModuleType(f"{__name__}.routes")
_routes_module.analyze = sys.modules[__name__]
_routes_module.telegram = sys.modules[__name__]
sys.modules[f"{__name__}.routes"] = _routes_module
sys.modules[f"{__name__}.routes.analyze"] = sys.modules[__name__]
sys.modules[f"{__name__}.routes.telegram"] = sys.modules[__name__]
sys.modules[f"{__name__}.telegram_client"] = sys.modules[__name__]
