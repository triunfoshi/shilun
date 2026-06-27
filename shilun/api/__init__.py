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


@ui_router.get("/api/v1/market/sectors")
def market_sectors(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    lookback_days: int = 40,
    top_n: int = 8,
) -> dict[str, Any]:
    import pandas as pd

    from shilun.common.db import MongoSnapshotStore

    target_date = normalize_analysis_date(date)
    request = SectorTrendRequest(
        analysis_date=target_date,
        benchmark_ticker=benchmark_ticker.upper(),
        lookback_days=lookback_days,
        top_n=top_n,
    )
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，板块动向需要读取 Mongo 日线数据。")
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
        return evaluate_sector_trends(
            analysis_date=target_date,
            benchmark_ticker=request.benchmark_ticker,
            benchmark_bars=pd.DataFrame(benchmark_records),
            market_bars=pd.DataFrame(market_records),
            stock_basic=stock_basic,
            daily_basic=daily_basic,
            moneyflow=moneyflow,
            top_n=request.top_n,
            min_stock_count=request.min_stock_count,
            exclude_st=request.exclude_st,
            include_daily_leaders=False,
            include_all_sectors=False,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Market sectors failed: {error}") from error
    finally:
        store.close()


@ui_router.get("/api/v1/market/candidates")
def market_candidates(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    lookback_days: int = 40,
    top_n: int = 8,
) -> dict[str, Any]:
    import pandas as pd

    from shilun.common.db import MongoSnapshotStore

    target_date = normalize_analysis_date(date)
    request = SectorTrendRequest(
        analysis_date=target_date,
        benchmark_ticker=benchmark_ticker.upper(),
        lookback_days=lookback_days,
        top_n=top_n,
    )
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=400, detail="Mongo 未配置，候选池需要读取 Mongo 日线数据。")
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
        result = evaluate_sector_trends(
            analysis_date=target_date,
            benchmark_ticker=request.benchmark_ticker,
            benchmark_bars=pd.DataFrame(benchmark_records),
            market_bars=pd.DataFrame(market_records),
            stock_basic=stock_basic,
            daily_basic=daily_basic,
            moneyflow=moneyflow,
            top_n=request.top_n,
            min_stock_count=request.min_stock_count,
            exclude_st=request.exclude_st,
            include_daily_leaders=False,
            include_all_sectors=False,
        )
        return {
            "analysis_date": target_date,
            "benchmark_ticker": request.benchmark_ticker,
            "candidates": result.get("candidates", []),
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
