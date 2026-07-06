"""FastAPI application, HTTP routes, and Telegram client.

M5 simplification note:
The API layer had a tiny app factory, two small route modules, and a small
Telegram client module. They are now a single boundary so runtime entry remains
`shilun.api:app` and route tests can still import the old paths through aliases.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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


class AkshareSyncHttpRequest(BaseModel):
    target_date: str | None = None
    dataset: str = Field(
        default="all",
        description="all / limit_up_pool / concept_boards / north_capital_flow",
    )


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
    """全面检查所有数据源，标注每项缺失阻断的功能。

    数据源分类与阻断规则（核心数据缺一不可，增强数据按维度降级）：

    | 数据集 | 类别 | 缺失影响 |
    |---|---|---|
    | market_daily_bars(个股) | 核心 | PART1/2/3 全部阻断 |
    | market_daily_bars(指数) | 核心 | PART1 阻断（指数趋势/支撑压力） |
    | stock_basic | 核心 | PART1 主线分降级、PART2 个股 name/industry 丢失 |
    | moneyflow | 增强 | PART2 资金流向分降级 |
    | limit_up_pool (akshare) | 增强 | PART1 涨停情绪精度降低，仍可估算 |
    | concept_boards (akshare) | 增强 | PART2 概念主线维度缺失 |
    | north_capital_flow (akshare) | 增强 | PART1 北向维度缺失 |
    """
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
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    except Exception as error:
        return {
            "mongo_configured": True,
            "mongo_connected": False,
            "target_date": target_date,
            "overall_status": "blocked",
            "message": f"Mongo 已配置但当前无法连接：{error}",
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

        # ── 增强：akshare 三类 ────────────────────────────────────────
        target_yyyymmdd = target_date.replace("-", "")
        limit_up_target_count = store.collection("limit_up_pool").count_documents(
            {"trade_date": target_yyyymmdd}
        )
        concept_total = store.collection("concept_boards").count_documents({})
        concept_latest_doc = list(
            store.collection("concept_boards")
            .find({}, {"_id": 0, "snapshot_date": 1})
            .sort("snapshot_date", -1).limit(1)
        )
        concept_latest_date = concept_latest_doc[0].get("snapshot_date") if concept_latest_doc else None

        north_total = store.collection("north_capital_flow").count_documents({})
        north_latest_doc = list(
            store.collection("north_capital_flow")
            .find({}, {"_id": 0, "trade_date": 1})
            .sort("trade_date", -1).limit(1)
        )
        north_latest_date = north_latest_doc[0].get("trade_date") if north_latest_doc else None

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
                "impact_on_miss": "PART2 资金流维度按 0 处理，仍可计算其他维度",
            },
            {
                "key": "limit_up_pool",
                "label": "涨停池（akshare）",
                "tier": "enhanced",
                "source": "akshare stock_zt_pool_em",
                "value": limit_up_target_count,
                "ok": limit_up_target_count > 0,
                "detail": f"{target_date} 涨停 {limit_up_target_count} 只" + (" · 未同步" if limit_up_target_count == 0 else ""),
                "impact_on_miss": "PART1 涨停情绪精度降低（仍用 pct_chg>=9.5% 估算）",
            },
            {
                "key": "concept_boards",
                "label": "概念板块快照（akshare）",
                "tier": "enhanced",
                "source": "akshare stock_board_concept_name_em",
                "value": concept_total,
                "ok": concept_total > 50 and concept_latest_date == target_yyyymmdd,
                "detail": f"概念板块 {concept_total} 条，最新快照 {concept_latest_date or '暂无'}",
                "impact_on_miss": "PART2 概念主线维度缺失",
            },
            {
                "key": "north_capital_flow",
                "label": "北向资金（akshare）",
                "tier": "enhanced",
                "source": "akshare stock_hsgt_hist_em",
                "value": north_total,
                "ok": north_total > 100,
                "detail": f"北向资金 {north_total} 条历史，最新 {north_latest_date or '暂无'}",
                "impact_on_miss": "PART1 北向资金维度缺失",
            },
        ]

        core_missing = [d for d in datasets if d["tier"] == "core" and not d["ok"]]
        enhanced_missing = [d for d in datasets if d["tier"] == "enhanced" and not d["ok"]]
        if core_missing:
            overall_status = "blocked"
            top_message = f"核心数据缺失：{', '.join(d['label'] for d in core_missing)}。PART1/2/3 部分功能不可用。"
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
    finally:
        store.close()


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


@ui_router.get("/api/v1/market/permission")
def market_permission(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    lookback_days: int = 280,  # 扩到 280 让 MA250 可算
    force: bool = False,
) -> dict[str, Any]:
    """PART1 大盘权限。默认读缓存，传 force=true 强制重算。"""
    import pandas as pd

    from shilun.common.db import MongoSnapshotStore

    target_date = normalize_analysis_date(date)
    bm_ticker = benchmark_ticker.upper()
    request = MarketPart1Request(
        analysis_date=target_date,
        benchmark_ticker=bm_ticker,
        lookback_days=lookback_days,
    )
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，PART1 大盘权限需要读取 Mongo 日线数据。")
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 已配置但当前无法连接：{error}") from error
    try:
        cache_col = store.collection("market_part1_cache")
        cache_key = {"analysis_date": target_date, "benchmark_ticker": bm_ticker}
        if not force:
            cached = cache_col.find_one(cache_key, {"_id": 0, "payload": 1, "cached_at": 1, "engine_version": 1})
            if cached and cached.get("payload") and cached.get("engine_version") == PART1_ENGINE_VERSION:
                payload = cached["payload"]
                payload["_from_cache"] = True
                payload["_cached_at"] = cached.get("cached_at")
                return payload

        index_records = store.raw_market.find_daily_bars(
            start_date=request.start_date,
            end_date=target_date,
            tickers=[request.benchmark_ticker],
        )
        market_records = store.raw_market.find_daily_bars(
            start_date=request.start_date,
            end_date=target_date,
        )
        stock_basic = pd.DataFrame(store.raw_market.find_stock_basic())
        payload = evaluate_market_permission(
            analysis_date=target_date,
            benchmark_ticker=request.benchmark_ticker,
            index_bars=pd.DataFrame(index_records),
            market_bars=pd.DataFrame(market_records),
            stock_basic=stock_basic,
        )
        # 叠加用户上传的波浪/外部文档的人工标注位
        wave_docs = list(
            store.collection("wave_docs").find(
                {"analysis_date": target_date, "benchmark_ticker": bm_ticker},
                {"_id": 0},
            ).sort("uploaded_at", -1).limit(5)
        )
        if wave_docs:
            manual_levels: list[dict[str, Any]] = []
            for doc in wave_docs:
                for lv in doc.get("levels") or []:
                    manual_levels.append({
                        **lv,
                        "from_doc": doc.get("source_doc"),
                        "uploaded_at": doc.get("uploaded_at"),
                    })
            payload["manual_levels"] = manual_levels
            payload["wave_doc_count"] = len(wave_docs)
            payload["wave_doc_summaries"] = [
                {
                    "source_doc": d.get("source_doc"),
                    "ai_summary": d.get("ai_summary"),
                    "ai_provider": d.get("ai_provider"),
                    "level_count": d.get("level_count"),
                    "uploaded_at": d.get("uploaded_at"),
                }
                for d in wave_docs
            ]
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
        payload["_from_cache"] = False
        return payload
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Market permission failed: {error}") from error
    finally:
        store.close()


def _compute_sector_trends_full(
    store,
    target_date: str,
    bm_ticker: str,
    lookback_days: int,
    trend_lookback_days: int,
    top_n: int,
    include_daily_leaders: bool = False,
    include_all_sectors: bool = False,
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

    benchmark_records = store.raw_market.find_daily_bars(
        start_date=request.start_date, end_date=target_date, tickers=[bm_ticker],
    )
    market_records = store.raw_market.find_daily_bars(
        start_date=request.start_date, end_date=target_date,
    )
    stock_basic = pd.DataFrame(store.raw_market.find_stock_basic())
    daily_basic = pd.DataFrame(store.raw_market.find_daily_basic(trade_date=target_date))
    moneyflow_start = (
        datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=max(18, trend_lookback_days + 5))
    ).strftime("%Y-%m-%d")
    moneyflow = pd.DataFrame(
        store.raw_market.find_moneyflow(start_date=moneyflow_start, end_date=target_date)
    )
    gate = _load_market_gate(store, target_date, bm_ticker)
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
    )


def _sector_cache_key(target_date: str, bm_ticker: str, trend_lookback_days: int) -> dict[str, Any]:
    from shilun.market import SECTOR_ENGINE_VERSION

    return {
        "analysis_date": target_date,
        "benchmark_ticker": bm_ticker,
        "trend_lookback_days": trend_lookback_days,
        "engine_version": SECTOR_ENGINE_VERSION,
    }


def _load_sector_cache(store, target_date: str, bm_ticker: str, trend_lookback_days: int) -> dict[str, Any] | None:
    """尝试从 sector_trends_cache 读缓存。"""
    try:
        cache_col = store.collection("sector_trends_cache")
        cached = cache_col.find_one(
            _sector_cache_key(target_date, bm_ticker, trend_lookback_days),
            {"_id": 0, "payload": 1, "computed_at": 1, "engine_version": 1, "compute_time_seconds": 1},
        )
        if cached and cached.get("payload"):
            return cached
    except Exception:  # noqa: BLE001
        return None
    return None


def _save_sector_cache(
    store,
    target_date: str,
    bm_ticker: str,
    trend_lookback_days: int,
    payload: dict[str, Any],
    compute_time_seconds: float,
) -> None:
    from shilun.market import SECTOR_ENGINE_VERSION
    cache_col = store.collection("sector_trends_cache")
    key = _sector_cache_key(target_date, bm_ticker, trend_lookback_days)
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
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 已配置但当前无法连接：{error}") from error

    try:
        if not force:
            cached = _load_sector_cache(store, target_date, bm_ticker, trend_lookback_days)
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
                f"请先调用 POST /api/v1/data/precompute-sectors?date={target_date} 触发离线计算。"
                f"预计需 60-120 秒。"
            ),
        )
    finally:
        store.close()


@ui_router.post("/api/v1/data/precompute-sectors")
def precompute_sectors(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    lookback_days: int = 40,
    trend_lookback_days: int = 60,
    top_n: int = 8,
    include_daily_leaders: bool = True,
    include_all_sectors: bool = False,
) -> dict[str, Any]:
    """离线预计算板块动向 + 落 sector_trends_cache。

    这个 API 是**慢路径**（60-120 秒），跑完把结果写入缓存。
    之后 GET /api/v1/market/sectors 只读缓存，秒回。
    """
    import time
    from shilun.common.db import MongoSnapshotStore

    target_date = normalize_analysis_date(date)
    bm_ticker = benchmark_ticker.upper()
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置。")
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 连接失败：{error}") from error

    try:
        t0 = time.time()
        result = _compute_sector_trends_full(
            store=store,
            target_date=target_date,
            bm_ticker=bm_ticker,
            lookback_days=lookback_days,
            trend_lookback_days=trend_lookback_days,
            top_n=top_n,
            include_daily_leaders=include_daily_leaders,
            include_all_sectors=include_all_sectors,
        )
        elapsed = time.time() - t0
        _save_sector_cache(store, target_date, bm_ticker, trend_lookback_days, result, elapsed)
        return {
            "status": "ok",
            "analysis_date": target_date,
            "benchmark_ticker": bm_ticker,
            "trend_lookback_days": trend_lookback_days,
            "compute_time_seconds": round(elapsed, 2),
            "top_sectors": [
                {"name": s["sector_name"], "score": s["scores"].get("sector_score")}
                for s in (result.get("top_sectors") or [])
            ],
            "message": f"预计算完成，耗时 {elapsed:.1f}s。可访问 GET /api/v1/market/sectors 秒回。",
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"预计算失败：{error}") from error
    finally:
        store.close()


@ui_router.get("/api/v1/market/candidates")
def market_candidates(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    lookback_days: int = 40,
    trend_lookback_days: int = 60,
    top_n: int = 8,
) -> dict[str, Any]:
    """候选池 — 从 sector_trends_cache 直接读，未命中报 400。"""
    from shilun.common.db import MongoSnapshotStore

    target_date = normalize_analysis_date(date)
    bm_ticker = benchmark_ticker.upper()
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，候选池需要读取 Mongo 日线数据。")
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 已配置但当前无法连接：{error}") from error
    try:
        cached = _load_sector_cache(store, target_date, bm_ticker, trend_lookback_days)
        if not cached:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"候选池数据未预计算（{target_date} / {bm_ticker} / lookback={trend_lookback_days}）。"
                    f"请先调用 POST /api/v1/data/precompute-sectors?date={target_date} 触发离线计算。"
                ),
            )
        payload = cached["payload"]
        gate = _load_market_gate(store, target_date, bm_ticker)
        return {
            "analysis_date": target_date,
            "benchmark_ticker": bm_ticker,
            "market_gate": gate,
            "candidates": payload.get("candidates", []),
            "_from_cache": True,
            "_cached_at": cached.get("computed_at"),
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Market candidates failed: {error}") from error
    finally:
        store.close()


@ui_router.get("/api/v1/market/leaders")
def market_daily_leaders(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    lookback_days: int = 75,
    top_n: int = 5,
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
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 已配置但当前无法连接：{error}") from error
    try:
        benchmark_records = store.raw_market.find_daily_bars(
            start_date=request.start_date,
            end_date=target_date,
            tickers=[request.benchmark_ticker],
        )
        market_records = store.raw_market.find_daily_bars(
            start_date=request.start_date,
            end_date=target_date,
        )
        stock_basic = pd.DataFrame(store.raw_market.find_stock_basic())
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
        return evaluate_daily_leaders(
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

    target_date = normalize_analysis_date(payload.target_date)
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，请先设置 SHILUN_MONGO_URI。")
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 已配置但当前无法连接：{error}") from error
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
            "failed_trade_dates": result.failed_trade_dates,
            "synced_trade_dates": result.synced_trade_dates,
            "skipped_trade_dates": result.skipped_trade_dates,
        }
    finally:
        store.close()


@ui_router.post("/api/v1/data/sync-akshare")
def data_sync_akshare(payload: AkshareSyncHttpRequest) -> dict[str, Any]:
    """同步 akshare 增强数据。dataset 可选：all/limit_up_pool/concept_boards/north_capital_flow。"""
    from shilun.common.db import MongoSnapshotStore
    from shilun.data.providers.akshare_provider import AkshareFetchError
    from shilun.jobs.akshare_sync_job import AkshareSyncJob

    target_date = normalize_analysis_date(payload.target_date)
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，请先设置 SHILUN_MONGO_URI。")
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 已配置但当前无法连接：{error}") from error

    try:
        job = AkshareSyncJob(mongo_store=store)
        trade_date_yyyymmdd = target_date.replace("-", "")
        try:
            if payload.dataset == "all":
                result = job.sync_all(trade_date_yyyymmdd)
                return {
                    "target_date": result.target_date,
                    "limit_up_count": result.limit_up_count,
                    "concept_board_count": result.concept_board_count,
                    "north_capital_count": result.north_capital_count,
                    "message": result.message,
                }
            elif payload.dataset == "limit_up_pool":
                count = job.sync_limit_up_pool(trade_date_yyyymmdd)
                return {"dataset": "limit_up_pool", "target_date": target_date, "count": count, "message": f"涨停池同步完成，{count} 只。"}
            elif payload.dataset == "concept_boards":
                count = job.sync_concept_boards()
                return {"dataset": "concept_boards", "count": count, "message": f"概念板块同步完成，{count} 个板块。"}
            elif payload.dataset == "north_capital_flow":
                count = job.sync_north_capital_flow()
                return {"dataset": "north_capital_flow", "count": count, "message": f"北向资金同步完成，{count} 条历史记录。"}
            else:
                raise HTTPException(
                    status_code=400,
                    detail="dataset 只支持 all / limit_up_pool / concept_boards / north_capital_flow。",
                )
        except AkshareFetchError as error:
            underlying = str(error.underlying)
            # 友好化错误：东财对 push2 接口高频限流
            if "RemoteDisconnected" in underlying or "Connection aborted" in underlying:
                detail = (
                    f"AkShare 接口 {error.source} 连接被服务端断开。"
                    f"通常是东方财富对当前 IP 的高频限流（push2 域），"
                    f"请等待 5-10 分钟后重试，或在网络环境（VPN/代理）切换后重试。"
                )
            elif "ProxyError" in underlying or "Unable to connect to proxy" in underlying:
                detail = (
                    f"AkShare 接口 {error.source} 走代理失败：{underlying}。"
                    f"已尝试绕过系统代理；如仍失败请检查 macOS 系统代理设置（系统设置 → 网络 → 代理）。"
                )
            else:
                detail = f"AkShare 接口 {error.source} 失败：{underlying}"
            raise HTTPException(status_code=502, detail=detail) from error
    finally:
        store.close()


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
            }
    except TushareRealtimeError as e:
        errors.append(f"tushare index_realtime: {e.underlying}")

    if candidate_tickers:
        try:
            stock_df = client.fetch_stock_realtime_batch(candidate_tickers)
        except TushareRealtimeError as e:
            errors.append(f"tushare stock_realtime: {e.underlying}")

    return benchmark, stock_df, errors


def _fetch_realtime_via_akshare(bm_ticker: str, candidate_tickers: list[str] | None):
    """用 akshare（东财 push2）拿实时行情。作为 tushare 失败时的回退。"""
    from shilun.data.providers.akshare_provider import AkshareClient, AkshareConfig, AkshareFetchError

    errors: list[str] = []
    benchmark = None
    stock_df = None

    client = AkshareClient(AkshareConfig())
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
    except AkshareFetchError as e:
        errors.append(f"akshare index_realtime: {e.underlying}")

    if candidate_tickers:
        try:
            stock_df = client.fetch_stock_realtime_batch(tickers=candidate_tickers)
        except AkshareFetchError as e:
            errors.append(f"akshare stock_realtime: {e.underlying}")

    return benchmark, stock_df, errors


@ui_router.get("/api/v1/market/intraday")
def market_intraday(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    include_candidates: bool = True,
    candidate_limit: int = 20,
    selected_tickers: str | None = None,
    source: str = "auto",
) -> dict[str, Any]:
    """盘中实时监控。

    source 参数控制实时行情数据源：
    - "auto"（默认）：优先 tushare（新浪源），失败再回退到 akshare（东财）
    - "tushare"：只用 tushare（推荐，本地网络下最稳定）
    - "akshare"：只用 akshare

    大盘关键位：从 PART1 缓存里读取 levels + pattern_forecast。
    候选票：从 candidate_pool_states 取 top N 只，实时拉取并对比。
    """
    from shilun.common.db import MongoSnapshotStore

    target_date = normalize_analysis_date(date)
    bm_ticker = benchmark_ticker.upper()
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，盘中监控需要读取 PART1 缓存。")
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 已配置但当前无法连接：{error}") from error

    try:
        # 1. 拿最近一次 PART1 缓存（gate + levels + pattern_forecast）
        cache_col = store.collection("market_part1_cache")
        cached = cache_col.find_one(
            {"analysis_date": target_date, "benchmark_ticker": bm_ticker},
            {"_id": 0, "payload": 1, "cached_at": 1},
        )
        if not cached:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"未找到 {target_date} 的 PART1 缓存。"
                    f"请先访问 /api/v1/market/permission?date={target_date} 计算并建立缓存。"
                ),
            )
        payload = cached["payload"]
        part1_cached_at = cached.get("cached_at")

        gate = payload.get("market_gate") or {}
        levels = payload.get("levels") or {}
        pattern_forecast = payload.get("pattern_forecast") or {}
        trend_summary = payload.get("trend_summary") or {}

        # 2. 决定候选票列表
        candidate_tickers: list[str] = []
        candidates_meta: list[dict[str, Any]] = []
        if include_candidates:
            candidates_meta = _extract_candidates_from_cache(
                store,
                target_date,
                bm_ticker,
                gate,
                candidate_limit,
                selected_tickers=_parse_selected_tickers(selected_tickers),
            )
            candidates_meta = _enrich_intraday_candidate_plans(store, target_date, candidates_meta)
            candidate_tickers = [c["ticker"] for c in candidates_meta if c.get("ticker")]

        # 3. 按 source 参数选择实时数据源
        realtime_errors: list[str] = []
        benchmark_realtime = None
        stock_df = None
        source_used = None

        if source in ("auto", "tushare"):
            benchmark_realtime, stock_df, errs = _fetch_realtime_via_tushare(bm_ticker, candidate_tickers)
            if benchmark_realtime is not None:
                source_used = "tushare"
            else:
                realtime_errors.extend(errs)

        if benchmark_realtime is None and source in ("auto", "akshare"):
            benchmark_realtime, stock_df, errs = _fetch_realtime_via_akshare(bm_ticker, candidate_tickers)
            if benchmark_realtime is not None:
                source_used = "akshare"
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

        return {
            "analysis_date": target_date,
            "benchmark_ticker": bm_ticker,
            "server_time": datetime.now().isoformat(timespec="seconds"),
            "part1_cached_at": part1_cached_at,
            "realtime_source": source_used,
            "realtime_source_requested": source,
            "market_gate": gate,
            "benchmark_realtime": benchmark_realtime,
            "planned_levels": {
                "support_1": levels.get("support_1"),
                "support_1_source": levels.get("support_1_source"),
                "support_2": levels.get("support_2"),
                "support_2_source": levels.get("support_2_source"),
                "pressure_1": levels.get("pressure_1"),
                "pressure_1_source": levels.get("pressure_1_source"),
                "manual_levels": payload.get("manual_levels") or [],
            },
            "pattern_forecast": pattern_forecast,
            "trend_summary": trend_summary,
            "triggered_levels": triggered_levels,
            "candidates_realtime": candidate_realtime,
            "realtime_errors": realtime_errors,
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
) -> dict[str, Any]:
    """单票面板数据（图 4 风格）：日线+分钟 K + 信号点 + 支撑压力 + 穿透 + 评级。

    分钟数据源：akshare 新浪源（免费无 token）。
    信号识别：shilun.market.signal_detector.detect_signals_daily。
    """
    import pandas as pd

    from shilun.common.db import MongoSnapshotStore
    from shilun.data.providers.akshare_provider import AkshareClient, AkshareConfig, AkshareFetchError
    from shilun.data.providers.tushare_realtime import TushareRealtimeClient, TushareRealtimeError
    from shilun.market.signal_detector import detect_signals_daily, summarize_signals, SIGNAL_META

    ticker_norm = ticker.upper().strip()
    if not ticker_norm:
        raise HTTPException(status_code=400, detail="ticker 参数必填")

    target_date = normalize_analysis_date(date)
    bm_ticker = benchmark_ticker.upper()

    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置")

    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Mongo 连接失败：{error}") from error

    try:
        errors: list[str] = []

        # 1. 分钟 K 线（新浪源）
        ak_client = AkshareClient(AkshareConfig())
        minute_bars = None
        try:
            df = ak_client.fetch_minute_bars(ticker_norm, period=period)
            if df is not None and not df.empty:
                # 只保留最近 3 个交易日的分钟数据（避免负载过大）
                cutoff = df["datetime"].max() - pd.Timedelta(days=5)
                df = df[df["datetime"] > cutoff].reset_index(drop=True)
                minute_bars = df.to_dict("records")
                # datetime 转 iso 字符串
                for row in minute_bars:
                    row["datetime"] = pd.Timestamp(row["datetime"]).isoformat()
                    for k in ("open", "high", "low", "close"):
                        row[k] = _round_num(row.get(k), 2)
                    row["volume"] = int(_round_num(row.get("volume"), 0) or 0)
                    row["amount"] = _round_num(row.get("amount"), 0)
        except AkshareFetchError as e:
            errors.append(f"minute_bars: {e.underlying}")

        # 2. 日线 + 信号识别
        daily_bars = None
        signals: list[dict[str, Any]] = []
        signal_summary: dict[str, Any] = {}
        levels: dict[str, Any] = {}
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
                signals = detect_signals_daily(dfd, ticker=ticker_norm)
                signal_summary = summarize_signals(signals)

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

                # 从日线算支撑压力（简化版：取近 500 根的高低点为压力支撑）
                closes = dfd["close"].astype(float)
                highs = dfd["high"].astype(float)
                lows = dfd["low"].astype(float)
                latest_close = float(closes.iloc[-1])
                # 阻力：最近 60 根内高于当前的历史高点
                recent_high = float(highs.tail(60).max())
                # 支撑：最近 60 根内低于当前的历史低点
                recent_low = float(lows.tail(60).min())
                # MA5 × 0.98 作为止损位
                ma5 = float(closes.tail(5).mean())
                levels = {
                    "resistance": _round_num(recent_high, 2),
                    "support": _round_num(recent_low, 2),
                    "stop_loss_long": _round_num(ma5 * 0.98, 2),
                    "stop_loss_short": _round_num(recent_high * 1.02, 2),
                    "ma5": _round_num(ma5, 2),
                    "based_on_bars": len(dfd),
                }

        # 3. 实时报价（Tushare 新浪源，最稳定）
        realtime = None
        try:
            ts_client = TushareRealtimeClient()
            rt_df = ts_client.fetch_stock_realtime_batch([ticker_norm])
            if rt_df is not None and not rt_df.empty:
                r = rt_df.iloc[0].to_dict()
                realtime = {
                    "current": _round_num(r.get("current")),
                    "pct_chg": _round_num(r.get("pct_chg"), 4),
                    "open": _round_num(r.get("open")),
                    "high": _round_num(r.get("high")),
                    "low": _round_num(r.get("low")),
                    "prev_close": _round_num(r.get("prev_close")),
                    "volume": r.get("volume"),
                    "amount": _round_num(r.get("amount")),
                    "name": r.get("name"),
                    "update_at": r.get("update_at"),
                }
        except TushareRealtimeError as e:
            errors.append(f"realtime: {e.underlying}")

        # 4. 大盘穿透（该票 vs 大盘方向共振）
        market_sync = None
        try:
            bm_rt_df = ts_client.fetch_index_realtime([bm_ticker])
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
        except (TushareRealtimeError, Exception) as e:
            errors.append(f"market_sync: {e}")

        # 5. 智能评级（0-100，聚合信号+趋势+RSI+涨跌幅动量）
        rating = _compute_stock_rating(signals, daily_bars or [], realtime)

        # 6. 股票基础信息
        basic = None
        try:
            basic_list = store.raw_market.find_stock_basic()
            for b in basic_list:
                if str(b.get("ts_code", "")).upper() == ticker_norm:
                    basic = {
                        "ticker": ticker_norm,
                        "name": b.get("name") or (realtime or {}).get("name", ""),
                        "industry": b.get("industry") or "",
                        "market": b.get("market") or "",
                        "list_date": b.get("list_date") or "",
                    }
                    break
        except Exception as e:
            errors.append(f"basic: {e}")

        return {
            "ticker": ticker_norm,
            "analysis_date": target_date,
            "benchmark_ticker": bm_ticker,
            "basic": basic,
            "realtime": realtime,
            "market_sync": market_sync,
            "levels": levels,
            "rating": rating,
            "daily_bars": daily_bars,
            "minute_bars": minute_bars,
            "signals": signals,
            "signal_summary": signal_summary,
            "signal_meta": SIGNAL_META,
            "active_buy": active_buy_context,
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
    docs: list[dict[str, Any]] = []
    try:
        col = store.collection("candidate_pool_states")
        docs.extend(
            col.find(
                {"analysis_date": target_date},
                {"_id": 0},
            )
            .sort("rank", 1)
            .limit(limit)
        )
    except Exception:
        docs = []

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

    return docs[: max(1, int(limit)) + len(selected)]


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


def _nearest_below(price: float | None, candidates: list[tuple[float | None, str]]) -> tuple[float | None, str]:
    if not price:
        return None, ""
    valid = [(float(v), label) for v, label in candidates if v and float(v) > 0 and float(v) <= price * 1.012]
    if not valid:
        valid = [(float(v), label) for v, label in candidates if v and float(v) > 0]
    if not valid:
        return None, ""
    return max(valid, key=lambda item: item[0])


def _nearest_above(price: float | None, candidates: list[tuple[float | None, str]]) -> tuple[float | None, str]:
    if not price:
        return None, ""
    valid = [(float(v), label) for v, label in candidates if v and float(v) >= price * 0.995]
    if not valid:
        valid = [(float(v), label) for v, label in candidates if v and float(v) > 0]
    if not valid:
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
        high20 = max(highs[-20:]) if highs else None
        low20 = min(lows[-20:]) if lows else None

        support_price, support_source = _nearest_below(
            close,
            [
                (_first_positive_number(item.get("support_price")), item.get("support_source") or "候选支撑"),
                (ma8, "MA8"),
                (ma5, "MA5"),
                (low20, "20日低点"),
            ],
        )
        pressure_price, pressure_source = _nearest_above(
            close,
            [
                (_first_positive_number(item.get("pressure_price")), item.get("pressure_source") or "候选压力"),
                (high20, "20日高点"),
                (close * 1.08 if close else None, "8%目标"),
            ],
        )
        predicted_buy = _first_positive_number(item.get("predicted_buy_price"), item.get("entry_price"), support_price, ma8, ma5, close)
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
                "volume_ratio": _round_num(r.get("volume_ratio"), 2),
                "amount": _round_num(r.get("amount")),
                "update_at": r.get("update_at"),
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


@ui_router.post("/api/v1/market/upload-wave-doc")
async def upload_wave_doc(
    file: UploadFile = File(...),
    target_date: str | None = Form(None),
    benchmark_ticker: str = Form(DEFAULT_BENCHMARK_TICKER),
    use_ai: bool = Form(False),
    ai_provider: str = Form("anthropic"),
) -> dict[str, Any]:
    """上传外部波浪/技术分析文档（黑兔等），解析关键价位并落库到 wave_docs 集合。"""
    from shilun.common.db import MongoSnapshotStore
    from shilun.market.wave_doc_parser import parse_wave_document

    if not file.filename:
        raise HTTPException(status_code=400, detail="未指定文件名。")
    try:
        file_bytes = await file.read()
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"读取上传文件失败：{error}") from error
    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")
    if len(file_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件超过 20MB 限制。")

    try:
        result = parse_wave_document(
            filename=file.filename,
            file_bytes=file_bytes,
            use_ai=use_ai,
            ai_provider=ai_provider,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"解析文档失败：{error}") from error

    target = normalize_analysis_date(target_date)
    bm = benchmark_ticker.upper()
    payload = result.to_dict()
    payload.update({
        "analysis_date": target,
        "benchmark_ticker": bm,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    })

    config = load_config()
    if config.mongo_uri:
        try:
            store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
            store.collection("wave_docs").insert_one({**payload})  # 允许同一日期多份文档
            store.close()
        except Exception as error:  # noqa: BLE001
            payload["_storage_error"] = str(error)
    return payload


@ui_router.get("/api/v1/market/wave-docs")
def list_wave_docs(date: str | None = None, benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER) -> dict[str, Any]:
    """列出指定日期已上传的波浪文档。"""
    from shilun.common.db import MongoSnapshotStore

    target = normalize_analysis_date(date)
    bm = benchmark_ticker.upper()
    config = load_config()
    if not config.mongo_uri:
        return {"target_date": target, "benchmark_ticker": bm, "docs": []}
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    try:
        cursor = store.collection("wave_docs").find(
            {"analysis_date": target, "benchmark_ticker": bm},
            {"_id": 0},
        ).sort("uploaded_at", -1).limit(10)
        docs = list(cursor)
        return {"target_date": target, "benchmark_ticker": bm, "docs": docs, "count": len(docs)}
    finally:
        store.close()


@ui_router.delete("/api/v1/market/wave-docs")
def delete_wave_docs(date: str | None = None, benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER) -> dict[str, Any]:
    """删除指定日期所有上传文档（避免脏数据）。"""
    from shilun.common.db import MongoSnapshotStore

    target = normalize_analysis_date(date)
    bm = benchmark_ticker.upper()
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置。")
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    try:
        result = store.collection("wave_docs").delete_many(
            {"analysis_date": target, "benchmark_ticker": bm}
        )
        return {"target_date": target, "benchmark_ticker": bm, "deleted_count": result.deleted_count}
    finally:
        store.close()


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
        return _display_date(latest[0]["cal_date"]) if latest else weekday_fallback
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
    app = FastAPI(title="Shilun Standalone", version="0.1.0")
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
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
    "TelegramBotClient",
    "TelegramChat",
    "TelegramMessage",
    "TelegramUpdate",
    "TelegramWebhookSetupRequest",
    "analyze",
    "analyze_router",
    "app",
    "build_telegram_text",
    "create_app",
    "control_center",
    "data_status",
    "data_sync",
    "market_permission",
    "market_sectors",
    "parse_telegram_command",
    "push_channel_status",
    "telegram_router",
    "tushare_health",
    "ui_router",
]


_routes_module = types.ModuleType(f"{__name__}.routes")
_routes_module.analyze = sys.modules[__name__]
_routes_module.telegram = sys.modules[__name__]
sys.modules[f"{__name__}.routes"] = _routes_module
sys.modules[f"{__name__}.routes.analyze"] = sys.modules[__name__]
sys.modules[f"{__name__}.routes.telegram"] = sys.modules[__name__]
sys.modules[f"{__name__}.telegram_client"] = sys.modules[__name__]
