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
from typing import Any

import requests
from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from shilun.common.config import load_config
from shilun.market import (
    BENCHMARK_INDEX_OPTIONS,
    DEFAULT_BENCHMARK_TICKER,
    MarketPart1Request,
    SectorTrendRequest,
    evaluate_daily_leaders,
    evaluate_market_permission,
    evaluate_sector_trends,
)
from shilun.services import AnalyzeRequest, MongoFirstAnalysisService


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
    mode: str = Field(default="latest", description="latest, incremental, or history_year")
    force: bool = False
    benchmark_ticker: str | None = DEFAULT_BENCHMARK_TICKER
    incremental_lookback_days: int = 14
    incremental_overlap_days: int = 3


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
    return HTMLResponse(
        _CONTROL_CENTER_HTML
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
    from shilun.common.db import MongoSnapshotStore

    target_date = normalize_analysis_date(date)
    config = load_config()
    if not config.mongo_uri:
        return {
            "mongo_configured": False,
            "target_date": target_date,
            "has_target_data": False,
            "target_daily_bar_count": 0,
            "latest_market_date": None,
            "message": "Mongo 未配置，请先设置 SHILUN_MONGO_URI。",
        }
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    except Exception as error:
        return {
            "mongo_configured": True,
            "mongo_connected": False,
            "target_date": target_date,
            "has_target_data": False,
            "target_daily_bar_count": 0,
            "latest_market_date": None,
            "message": f"Mongo 已配置但当前无法连接：{error}",
        }
    try:
        target_rows = store.raw_market.find_daily_bars_for_trade_date(target_date)
        latest_query: dict[str, Any] = {}
        if ticker:
            latest_query["ticker"] = ticker.upper()
        latest = list(store.collection("market_daily_bars").find(latest_query, {"_id": 0, "date": 1}).sort("date", -1).limit(1))
        latest_market_date = latest[0].get("date") if latest else None
        has_target_data = bool(target_rows)
        return {
            "mongo_configured": True,
            "mongo_connected": True,
            "mongo_uri": _mask_secret(config.mongo_uri),
            "target_date": target_date,
            "has_target_data": has_target_data,
            "target_daily_bar_count": len(target_rows),
            "latest_market_date": latest_market_date,
            "ticker": ticker.upper() if ticker else None,
            "message": (
                f"Mongo 已有 {target_date} 日线数据，共 {len(target_rows)} 条。"
                if has_target_data
                else f"Mongo 暂无 {target_date} 日线数据，可同步 Tushare 最新交易日，或使用最近历史日期 {latest_market_date or '暂无'}。"
            ),
        }
    finally:
        store.close()


@ui_router.get("/api/v1/market/permission")
def market_permission(
    date: str | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    lookback_days: int = 80,
) -> dict[str, Any]:
    import pandas as pd

    from shilun.common.db import MongoSnapshotStore

    target_date = normalize_analysis_date(date)
    request = MarketPart1Request(
        analysis_date=target_date,
        benchmark_ticker=benchmark_ticker.upper(),
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
        return evaluate_market_permission(
            analysis_date=target_date,
            benchmark_ticker=request.benchmark_ticker,
            index_bars=pd.DataFrame(index_records),
            market_bars=pd.DataFrame(market_records),
            stock_basic=stock_basic,
        )
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
        else:
            raise HTTPException(status_code=400, detail="mode 只支持 latest、incremental 或 history_year。")
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


_CONTROL_CENTER_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>石论控制台</title>
  <style>
    :root {
      --ink: #f4f0e8;
      --muted: #9d988f;
      --soft: #c9c1b4;
      --bg: #0d0f12;
      --bg-soft: #15171b;
      --card: rgba(22, 24, 28, 0.88);
      --card-strong: rgba(28, 30, 35, 0.96);
      --line: rgba(244, 240, 232, 0.12);
      --line-strong: rgba(244, 240, 232, 0.22);
      --accent: #8fe6c4;
      --accent-dark: #2f9d78;
      --accent-cool: #9ad7ff;
      --green: #8fe6c4;
      --danger: #ff7a7a;
      --shadow: 0 28px 70px rgba(0, 0, 0, 0.42);
    }
    * { box-sizing: border-box; }
    html, body { max-width: 100%; overflow-x: hidden; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: "PingFang SC", "SF Pro Display", "SF Pro Text", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
      background:
        radial-gradient(circle at 10% 0%, rgba(143, 230, 196, 0.10), transparent 26rem),
        radial-gradient(circle at 88% 8%, rgba(154, 215, 255, 0.08), transparent 30rem),
        linear-gradient(135deg, #0b0c0f 0%, #111318 48%, #0c0d10 100%);
      min-height: 100vh;
      -webkit-font-smoothing: antialiased;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image:
        linear-gradient(rgba(244, 240, 232, 0.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(244, 240, 232, 0.035) 1px, transparent 1px);
      background-size: 36px 36px;
      mask-image: linear-gradient(to bottom, rgba(0,0,0,0.52), transparent 78%);
    }
    .shell { width: min(1180px, calc(100% - 36px)); max-width: 100%; margin: 0 auto; padding: 36px 0 48px; position: relative; }
    .hero {
      position: relative;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 30px;
      padding: 34px;
      background:
        linear-gradient(135deg, rgba(31, 33, 38, 0.96), rgba(15, 16, 20, 0.92)),
        linear-gradient(90deg, rgba(143, 230, 196, 0.08), rgba(154, 215, 255, 0.06));
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }
    .hero::after {
      content: "";
      position: absolute;
      right: -90px;
      top: -110px;
      width: 340px;
      height: 340px;
      border-radius: 999px;
      background:
        radial-gradient(circle, rgba(143, 230, 196, 0.14), transparent 58%),
        conic-gradient(from 90deg, rgba(143, 230, 196, 0.10), rgba(154, 215, 255, 0.08), transparent);
      filter: blur(2px);
    }
    .eyebrow { color: var(--accent); font-weight: 800; letter-spacing: 0.18em; text-transform: uppercase; }
    h1 { margin: 10px 0 8px; font-size: clamp(34px, 6vw, 72px); line-height: 0.95; letter-spacing: -0.06em; }
    .hero p { max-width: 720px; color: var(--muted); font-size: 18px; line-height: 1.8; }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 18px; margin-top: 18px; }
    .card {
      grid-column: span 6;
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 22px;
      background:
        linear-gradient(180deg, rgba(31, 33, 38, 0.88), rgba(17, 18, 22, 0.82)),
        var(--card);
      backdrop-filter: blur(18px);
      box-shadow: 0 18px 44px rgba(0, 0, 0, 0.28);
    }
    .card.wide { grid-column: span 12; min-width: 0; max-width: 100%; }
    h2 { margin: 0 0 14px; font-size: 24px; }
    label { display: block; margin: 12px 0 6px; color: var(--muted); font-size: 14px; }
    input, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
      background: rgba(8, 9, 11, 0.72);
      color: var(--ink);
      font: inherit;
      outline: none;
      transition: border-color 140ms ease, box-shadow 140ms ease, background 140ms ease;
    }
    input:focus, select:focus {
      border-color: rgba(143, 230, 196, 0.72);
      box-shadow: 0 0 0 4px rgba(143, 230, 196, 0.10);
      background: rgba(11, 12, 15, 0.92);
    }
    .row > div > select, .row > div > input:not([type="hidden"]) {
      min-height: 76px;
    }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .tabs { margin-top: 18px; }
    .tab-bar {
      display: flex;
      gap: 10px;
      overflow-x: auto;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(12, 13, 16, 0.70);
      backdrop-filter: blur(18px);
      box-shadow: 0 16px 40px rgba(0, 0, 0, 0.22);
    }
    .tab-button {
      margin: 0;
      white-space: nowrap;
      color: var(--soft);
      background: transparent;
      border: 1px solid transparent;
      box-shadow: none;
      padding: 10px 16px;
    }
    .tab-button:hover {
      transform: none;
      box-shadow: none;
      background: rgba(244, 240, 232, 0.06);
    }
    .tab-button.active {
      color: #061511;
      background: linear-gradient(135deg, #d7f8e8, var(--accent) 52%, #35a47a);
      border-color: rgba(255, 255, 255, 0.20);
      box-shadow: 0 14px 30px rgba(143, 230, 196, 0.16);
    }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; animation: panelIn 180ms ease-out; }
    @keyframes panelIn {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .permission-summary {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin: 14px 0;
    }
    .decision-card {
      border-radius: 20px;
      padding: 16px;
      border: 1px solid var(--line);
      background: rgba(9, 10, 12, 0.70);
    }
    .decision-card strong { display: block; color: var(--muted); font-size: 13px; margin-bottom: 8px; }
    .decision-card span { display: block; font-size: 22px; font-weight: 900; }
    .checks { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; }
    .check { display: inline-flex; align-items: center; gap: 8px; color: var(--muted); }
    .check input { width: auto; }
    .hint { margin: 6px 0 0; color: var(--soft); font-size: 14px; line-height: 1.7; }
    button, .link-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      margin: 8px 8px 0 0;
      color: #061511;
      background: linear-gradient(135deg, #d7f8e8, var(--accent) 52%, #35a47a);
      font: inherit;
      font-weight: 800;
      cursor: pointer;
      text-decoration: none;
      box-shadow: 0 14px 30px rgba(143, 230, 196, 0.16);
      transition: transform 140ms ease, box-shadow 140ms ease, filter 140ms ease;
    }
    button:hover, .link-button:hover {
      transform: translateY(-1px);
      filter: saturate(1.1);
      box-shadow: 0 18px 38px rgba(143, 230, 196, 0.22);
    }
    button:disabled {
      cursor: wait;
      opacity: 0.66;
      transform: none;
      filter: saturate(0.72);
    }
    button.secondary, .link-button.secondary {
      color: var(--ink);
      background: linear-gradient(135deg, rgba(48, 50, 56, 0.96), rgba(31, 33, 38, 0.96));
      border: 1px solid var(--line-strong);
      box-shadow: 0 12px 26px rgba(0, 0, 0, 0.20);
    }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      min-height: 120px;
      margin: 14px 0 0;
      padding: 16px;
      border-radius: 18px;
      border: 1px solid rgba(244, 240, 232, 0.12);
      background: rgba(7, 8, 10, 0.86);
      color: #efe8dc;
      line-height: 1.65;
      overflow: auto;
    }
    .status { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
    .pill { border-radius: 18px; padding: 14px; background: rgba(10, 11, 14, 0.72); border: 1px solid var(--line); }
    .pill strong { display: block; font-size: 13px; color: var(--muted); }
    .pill span { display: block; margin-top: 6px; font-weight: 800; }
    .ok { color: var(--green); }
    .bad { color: var(--danger); }
    .market-output {
      margin-top: 16px;
      display: grid;
      gap: 14px;
      min-width: 0;
      max-width: 100%;
    }
    .market-hero-card {
      position: relative;
      overflow: hidden;
      border: 1px solid rgba(244, 240, 232, 0.14);
      border-radius: 28px;
      padding: 24px;
      background:
        radial-gradient(circle at 92% 10%, rgba(143, 230, 196, 0.18), transparent 18rem),
        linear-gradient(135deg, rgba(37, 39, 44, 0.96), rgba(17, 18, 21, 0.94));
      box-shadow: 0 24px 56px rgba(0, 0, 0, 0.32);
    }
    .market-eyebrow {
      color: var(--accent);
      font-size: 12px;
      font-weight: 900;
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }
    .market-headline {
      margin: 10px 0;
      font-size: clamp(26px, 4vw, 42px);
      line-height: 1.08;
      letter-spacing: -0.04em;
    }
    .market-subtitle { color: var(--soft); line-height: 1.8; margin: 0; }
    .market-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 16px;
    }
    .meta-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      padding: 8px 12px;
      color: var(--soft);
      background: rgba(244, 240, 232, 0.06);
      border: 1px solid rgba(244, 240, 232, 0.10);
      font-size: 13px;
    }
    .interpretation-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .interpretation-card {
      border: 1px solid rgba(244, 240, 232, 0.12);
      border-radius: 24px;
      padding: 18px;
      background: linear-gradient(180deg, rgba(31, 33, 38, 0.92), rgba(15, 16, 19, 0.86));
    }
    .interpretation-card h3 {
      margin: 0;
      font-size: 20px;
      letter-spacing: -0.02em;
    }
    .section-conclusion {
      margin: 8px 0 16px;
      color: var(--soft);
      line-height: 1.75;
    }
    .metric-row {
      display: grid;
      gap: 8px;
      padding: 14px 0;
      border-top: 1px solid rgba(244, 240, 232, 0.08);
      min-width: 0;
    }
    .metric-label {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 900;
      letter-spacing: 0.02em;
    }
    .metric-value { color: var(--ink); font-size: 17px; font-weight: 900; line-height: 1.55; letter-spacing: -0.01em; overflow-wrap: anywhere; }
    .metric-judgement, .metric-explain { color: var(--soft); font-size: 14px; line-height: 1.7; }
    .metric-explain {
      border-left: 2px solid rgba(143, 230, 196, 0.42);
      padding-left: 10px;
      color: #d8d0c3;
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .reason-list {
      margin: 0;
      padding-left: 24px;
      display: grid;
      gap: 7px;
    }
    .reason-list li::marker { color: var(--accent); font-weight: 900; }
    .score-ref {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      margin: 0 6px 4px 0;
      padding: 5px 8px;
      border: 1px solid rgba(244, 240, 232, 0.10);
      border-radius: 999px;
      background: rgba(244, 240, 232, 0.045);
      max-width: 100%;
      white-space: normal;
      overflow-wrap: anywhere;
    }
    .info-popover {
      position: relative;
      display: inline-flex;
    }
    .info-popover summary {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      border-radius: 999px;
      border: 1px solid rgba(143, 230, 196, 0.52);
      color: var(--accent);
      font-size: 12px;
      font-weight: 950;
      cursor: pointer;
      list-style: none;
      background: rgba(143, 230, 196, 0.08);
    }
    .info-popover summary::-webkit-details-marker { display: none; }
    .info-popover[open] summary { background: var(--accent); color: #061511; }
    .info-panel {
      position: absolute;
      z-index: 10;
      top: 26px;
      left: -8px;
      width: min(320px, calc(100vw - 56px));
      padding: 12px;
      border-radius: 16px;
      color: var(--ink);
      background: rgba(18, 19, 23, 0.98);
      border: 1px solid rgba(143, 230, 196, 0.28);
      box-shadow: 0 18px 48px rgba(0, 0, 0, 0.38);
      font-size: 13px;
      line-height: 1.7;
      letter-spacing: 0;
    }
    .info-panel a {
      display: inline-flex;
      margin-top: 8px;
      color: var(--accent);
      font-weight: 900;
      text-decoration: none;
    }
    .date-wheel {
      display: grid;
      grid-template-columns: 1fr 0.84fr 0.84fr;
      gap: 10px;
      align-items: center;
      min-height: 76px;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background:
        linear-gradient(180deg, rgba(244, 240, 232, 0.045), rgba(244, 240, 232, 0.015)),
        rgba(8, 9, 11, 0.72);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
    }
    .date-wheel select {
      min-height: 58px;
      border-radius: 13px;
      border-color: rgba(244, 240, 232, 0.10);
      background: rgba(10, 11, 14, 0.88);
      font-weight: 850;
      text-align: center;
    }
    .date-wheel-caption {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin: 8px 2px 0;
      color: var(--muted);
      font-size: 12px;
    }
    .date-today {
      border: 1px solid rgba(143, 230, 196, 0.28);
      border-radius: 999px;
      padding: 3px 8px;
      color: var(--accent);
      background: rgba(143, 230, 196, 0.08);
      font-weight: 850;
    }
    .scorecard-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
    }
    .scorecard-item {
      border: 1px solid rgba(244, 240, 232, 0.10);
      border-radius: 20px;
      padding: 14px;
      background: rgba(244, 240, 232, 0.045);
    }
    .scorecard-item strong { display: block; font-size: 15px; }
    .scorecard-item span { display: block; margin: 6px 0; color: var(--accent); font-size: 26px; font-weight: 900; }
    .scorecard-item small { display: block; color: var(--muted); line-height: 1.6; }
    .leader-board, .boundary-grid, .indicator-grid {
      display: grid;
      gap: 12px;
    }
    .leader-view-tabs {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      max-width: 560px;
      padding: 5px;
      margin: 6px 0 18px;
      border: 1px solid rgba(244, 240, 232, 0.10);
      border-radius: 999px;
      background: rgba(8, 9, 11, 0.58);
    }
    .leader-view-tab {
      width: 100%;
      margin: 0;
      color: var(--soft);
      background: transparent;
      border: 1px solid transparent;
      box-shadow: none;
    }
    .leader-view-tab:hover { transform: none; box-shadow: none; background: rgba(244, 240, 232, 0.05); }
    .leader-view-tab.is-active {
      color: #061511;
      background: linear-gradient(135deg, #d7f8e8, #8fe6c4 62%, #35a47a);
      box-shadow: 0 10px 24px rgba(143, 230, 196, 0.16);
    }
    .leader-summary-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .leader-summary-card {
      position: relative;
      min-width: 0;
      padding: 16px;
      border: 1px solid rgba(244, 240, 232, 0.10);
      border-radius: 20px;
      background:
        radial-gradient(circle at 100% 0%, rgba(143, 230, 196, 0.10), transparent 13rem),
        rgba(8, 9, 11, 0.42);
    }
    .leader-summary-card.is-champion {
      border-color: rgba(143, 230, 196, 0.36);
      box-shadow: inset 0 0 0 1px rgba(143, 230, 196, 0.06), 0 16px 38px rgba(0, 0, 0, 0.22);
    }
    .leader-card-top {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
    }
    .leader-card-rank { color: var(--accent); font-size: 13px; font-weight: 950; }
    .leader-card-name { margin: 4px 0 2px; color: var(--ink); font-size: 19px; font-weight: 950; }
    .leader-card-code { color: var(--muted); font-size: 12px; }
    .leader-summary-score { color: var(--accent); font-size: 25px; font-weight: 950; white-space: nowrap; }
    .leader-role-label { margin: 10px 0; color: var(--soft); font-weight: 850; }
    .leader-stat-row { display: flex; flex-wrap: wrap; gap: 7px; }
    .leader-stat {
      padding: 6px 9px;
      border: 1px solid rgba(244, 240, 232, 0.09);
      border-radius: 999px;
      color: var(--soft);
      background: rgba(244, 240, 232, 0.04);
      font-size: 11px;
    }
    .leader-summary-card .reason-list { margin-top: 12px; color: var(--soft); font-size: 12px; }
    .leader-day-picker {
      display: grid;
      grid-template-columns: minmax(180px, 360px) minmax(0, 1fr);
      gap: 12px;
      align-items: center;
      margin-bottom: 14px;
    }
    .leader-day-picker select { min-height: 52px; }
    .leader-day-picker p { margin: 0; color: var(--muted); line-height: 1.6; }
    .leader-day {
      border: 1px solid rgba(244, 240, 232, 0.10);
      border-radius: 20px;
      padding: 14px;
      background: rgba(244, 240, 232, 0.045);
    }
    .leader-day h4, .boundary-item h4, .indicator-item h4 {
      margin: 0 0 10px;
      font-size: 16px;
    }
    .leader-list {
      display: grid;
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .leader-list li, .boundary-item, .indicator-item {
      border: 1px solid rgba(244, 240, 232, 0.08);
      border-radius: 16px;
      padding: 11px 12px;
      background: rgba(8, 9, 11, 0.42);
    }
    .leader-rank {
      color: var(--accent);
      font-weight: 950;
      margin-right: 8px;
    }
    .leader-query {
      display: grid;
      grid-template-columns: minmax(160px, 1fr) auto;
      gap: 10px;
      align-items: center;
      margin-top: 14px;
    }
    .leader-query input { min-height: 54px; }
    .leader-query-panel {
      margin-top: 18px;
      padding: 16px;
      border: 1px solid rgba(143, 230, 196, 0.16);
      border-radius: 20px;
      background: rgba(8, 12, 13, 0.50);
    }
    .leader-query-panel h4 { margin: 0; font-size: 17px; }
    .leader-query-panel > p { margin: 6px 0 0; color: var(--muted); line-height: 1.6; }
    .leader-query-result { margin-top: 12px; }
    .boundary-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .boundary-item strong {
      display: inline-flex;
      margin-bottom: 8px;
      color: var(--accent);
      font-size: 15px;
    }
    .boundary-status {
      display: inline-flex;
      border-radius: 999px;
      padding: 3px 8px;
      margin-left: 8px;
      color: #061511;
      background: var(--accent);
      font-size: 12px;
      font-weight: 900;
    }
    .indicator-item p, .boundary-item p {
      margin: 6px 0 0;
      color: var(--soft);
      line-height: 1.65;
    }
    .indicator-formula {
      color: var(--ink);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 13px;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .json-viewer {
      border: 1px solid rgba(244, 240, 232, 0.12);
      border-radius: 22px;
      background: rgba(8, 9, 11, 0.92);
      overflow: hidden;
    }
    .json-viewer summary {
      cursor: pointer;
      list-style: none;
      padding: 14px 18px;
      color: var(--soft);
      font-weight: 850;
      background: linear-gradient(180deg, rgba(31, 33, 38, 0.96), rgba(19, 20, 23, 0.96));
      border-bottom: 1px solid rgba(244, 240, 232, 0.08);
    }
    .json-viewer summary::-webkit-details-marker { display: none; }
    .json-viewer summary::after {
      content: "展开";
      float: right;
      color: var(--accent);
      font-size: 13px;
    }
    .json-viewer[open] summary::after { content: "收起"; }
    .json-code {
      margin: 0;
      border: 0;
      border-radius: 0;
      min-height: 0;
      background: #07080a;
      color: #d8dee9;
      font-size: 13px;
      line-height: 1.7;
    }
    .json-key { color: #8bd5ff; }
    .json-string { color: #b9f6ca; }
    .json-number { color: #f6c177; }
    .json-boolean { color: #c4a7e7; }
    .json-null { color: #8b949e; }
    .sector-story-layout {
      display: grid;
      grid-template-columns: 172px minmax(0, 1fr);
      gap: 18px;
      align-items: start;
      min-width: 0;
      max-width: 100%;
    }
    .sector-storyline {
      position: sticky;
      top: 18px;
      display: grid;
      gap: 10px;
      padding: 12px;
      border: 1px solid rgba(244, 240, 232, 0.10);
      border-radius: 24px;
      background: rgba(10, 11, 14, 0.76);
      backdrop-filter: blur(18px);
      box-shadow: 0 18px 48px rgba(0, 0, 0, 0.28);
    }
    .story-title {
      padding: 4px 6px 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 900;
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }
    .story-link {
      width: 100%;
      justify-content: flex-start;
      gap: 9px;
      margin: 0;
      padding: 10px 12px;
      color: var(--soft);
      background: transparent;
      border: 1px solid transparent;
      box-shadow: none;
      font-size: 13px;
    }
    .story-link:hover {
      transform: none;
      box-shadow: none;
      background: rgba(244, 240, 232, 0.06);
    }
    .story-link.active {
      color: #061511;
      background: linear-gradient(135deg, rgba(215, 248, 232, 0.98), rgba(143, 230, 196, 0.92));
      border-color: rgba(255, 255, 255, 0.18);
    }
    .story-link span {
      color: inherit;
      opacity: 0.72;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .sector-feature-stack {
      display: grid;
      gap: 16px;
      min-width: 0;
    }
    .sector-feature {
      scroll-margin-top: 18px;
      border: 1px solid rgba(244, 240, 232, 0.12);
      border-radius: 28px;
      padding: 22px;
      background:
        linear-gradient(180deg, rgba(31, 33, 38, 0.94), rgba(15, 16, 19, 0.88)),
        rgba(16, 18, 22, 0.88);
      box-shadow: 0 22px 56px rgba(0, 0, 0, 0.26);
      min-width: 0;
      max-width: 100%;
    }
    .feature-kicker {
      color: var(--accent);
      font-size: 12px;
      font-weight: 950;
      letter-spacing: 0.15em;
      text-transform: uppercase;
    }
    .feature-header {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: flex-start;
      margin-bottom: 14px;
    }
    .feature-header h3 {
      margin: 6px 0 0;
      font-size: clamp(24px, 3vw, 36px);
      letter-spacing: -0.04em;
    }
    .feature-header p {
      max-width: 760px;
      margin: 8px 0 0;
      color: var(--soft);
      line-height: 1.75;
    }
    .sector-switcher {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(138px, 1fr));
      gap: 10px;
      padding: 8px 2px 14px;
      margin-bottom: 8px;
      min-width: 0;
    }
    .sector-chip {
      width: 100%;
      min-width: 0;
      display: grid;
      justify-items: start;
      gap: 5px;
      margin: 0;
      padding: 12px 14px;
      color: var(--soft);
      background: rgba(244, 240, 232, 0.045);
      border: 1px solid rgba(244, 240, 232, 0.10);
      box-shadow: none;
      opacity: 0.72;
    }
    .sector-chip:hover {
      transform: translateY(-1px);
      box-shadow: 0 14px 28px rgba(0, 0, 0, 0.24);
      opacity: 0.92;
    }
    .sector-chip.is-active {
      color: #061511;
      background: linear-gradient(135deg, rgba(215, 248, 232, 0.98), rgba(143, 230, 196, 0.92));
      border-color: rgba(255, 255, 255, 0.24);
      opacity: 1;
    }
    .sector-chip.strength-high:not(.is-active) {
      border-color: rgba(143, 230, 196, 0.34);
      background: rgba(143, 230, 196, 0.10);
    }
    .sector-chip.strength-mid:not(.is-active) {
      border-color: rgba(154, 215, 255, 0.22);
      background: rgba(154, 215, 255, 0.07);
    }
    .sector-chip small {
      font-size: 12px;
      color: inherit;
      opacity: 0.78;
    }
    .sector-detail-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 14px;
    }
    .sector-detail-card {
      border-radius: 24px;
      border: 1px solid rgba(244, 240, 232, 0.10);
      background: rgba(8, 9, 11, 0.34);
      padding: 16px;
      min-width: 0;
    }
    .sector-detail-card.full {
      grid-column: 1 / -1;
    }
    .trend-board {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .trend-card {
      border: 1px solid rgba(244, 240, 232, 0.10);
      border-radius: 22px;
      padding: 16px;
      background:
        radial-gradient(circle at 100% 0%, rgba(143, 230, 196, 0.10), transparent 14rem),
        rgba(8, 9, 11, 0.42);
      min-width: 0;
    }
    .trend-card h4 {
      margin: 0 0 8px;
      font-size: 20px;
      letter-spacing: -0.02em;
    }
    .trend-card .rankline {
      color: var(--soft);
      line-height: 1.65;
      margin: 0 0 12px;
    }
    .trend-line-chart {
      position: relative;
      height: 188px;
      margin: 8px 0 14px;
      padding: 12px 12px 30px;
      border: 1px solid rgba(244, 240, 232, 0.08);
      border-radius: 16px;
      background:
        linear-gradient(rgba(244, 240, 232, 0.035) 1px, transparent 1px),
        rgba(0, 0, 0, 0.22);
      background-size: 100% 36px;
      overflow: visible;
    }
    .trend-line-svg {
      position: absolute;
      inset: 10px 8px 26px;
      width: calc(100% - 16px);
      height: calc(100% - 36px);
      overflow: visible;
    }
    .trend-line-path {
      fill: none;
      stroke: #8fe6c4;
      stroke-width: 2.2;
      vector-effect: non-scaling-stroke;
      filter: drop-shadow(0 0 5px rgba(143, 230, 196, 0.32));
    }
    .trend-zero-line {
      stroke: rgba(244, 240, 232, 0.20);
      stroke-width: 1;
      stroke-dasharray: 4 4;
      vector-effect: non-scaling-stroke;
    }
    .trend-point-hit {
      position: absolute;
      width: 18px;
      height: 18px;
      min-width: 0;
      margin: 0;
      padding: 0;
      border-radius: 999px;
      border: 4px solid rgba(8, 12, 13, 0.92);
      background: #8fe6c4;
      box-shadow: 0 0 0 2px rgba(143, 230, 196, 0.36), 0 0 18px rgba(143, 230, 196, 0.24);
      transform: translate(-50%, -50%);
      z-index: 3;
    }
    .trend-point-hit.is-negative { background: #ff8e8e; box-shadow: 0 0 0 2px rgba(255, 142, 142, 0.30); }
    .trend-point-hit:hover, .trend-point-hit:focus-visible {
      transform: translate(-50%, -50%) scale(1.12);
      filter: none;
      box-shadow: 0 0 0 4px rgba(143, 230, 196, 0.16), 0 0 24px rgba(143, 230, 196, 0.34);
      outline: none;
    }
    .trend-point-label {
      position: absolute;
      left: 50%;
      bottom: 17px;
      transform: translateX(-50%);
      color: var(--ink);
      font-size: 10px;
      font-weight: 900;
      white-space: nowrap;
      text-shadow: 0 1px 8px rgba(0, 0, 0, 0.92);
    }
    .trend-date-label {
      position: absolute;
      bottom: 7px;
      transform: translateX(-50%);
      color: var(--muted);
      font-size: 10px;
      white-space: nowrap;
    }
    .trend-point-tooltip {
      position: absolute;
      left: 50%;
      bottom: 28px;
      width: 232px;
      padding: 12px;
      border: 1px solid rgba(143, 230, 196, 0.26);
      border-radius: 15px;
      color: var(--soft);
      background: rgba(10, 13, 15, 0.98);
      box-shadow: 0 20px 46px rgba(0, 0, 0, 0.46);
      transform: translateX(-50%) translateY(6px);
      opacity: 0;
      visibility: hidden;
      pointer-events: none;
      transition: opacity 140ms ease, transform 140ms ease;
      text-align: left;
      font-size: 12px;
      line-height: 1.6;
      z-index: 20;
    }
    .trend-point-hit.align-left .trend-point-tooltip { left: -6px; transform: translateY(6px); }
    .trend-point-hit.align-right .trend-point-tooltip { left: auto; right: -6px; transform: translateY(6px); }
    .trend-point-hit:hover .trend-point-tooltip,
    .trend-point-hit:focus-visible .trend-point-tooltip {
      opacity: 1;
      visibility: visible;
      transform: translateX(-50%) translateY(0);
    }
    .trend-point-hit.align-left:hover .trend-point-tooltip,
    .trend-point-hit.align-left:focus-visible .trend-point-tooltip,
    .trend-point-hit.align-right:hover .trend-point-tooltip,
    .trend-point-hit.align-right:focus-visible .trend-point-tooltip { transform: translateY(0); }
    .trend-point-tooltip strong { display: block; margin-bottom: 6px; color: var(--ink); font-size: 13px; }
    .trend-tooltip-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px; }
    .trend-tooltip-grid span:nth-child(even) { color: var(--ink); text-align: right; }
    .trend-signal-row { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
    .trend-signal {
      padding: 3px 7px;
      border-radius: 999px;
      color: #061511;
      background: rgba(143, 230, 196, 0.88);
      font-size: 10px;
      font-weight: 900;
    }
    .leader-status {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--soft);
      line-height: 1.7;
    }
    .leader-dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 0 8px rgba(143, 230, 196, 0.08);
      flex: 0 0 auto;
    }
    .knowledge-grid {
      display: grid;
      grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
      gap: 14px;
      align-items: start;
    }
    .knowledge-panel {
      border: 1px solid rgba(244, 240, 232, 0.10);
      border-radius: 22px;
      padding: 16px;
      background: rgba(8, 9, 11, 0.38);
      min-width: 0;
    }
    .knowledge-panel h4 {
      margin: 0 0 12px;
      font-size: 18px;
    }
    .boundary-grid.compact, .indicator-grid.compact {
      grid-template-columns: 1fr;
    }
    .boundary-grid.compact .boundary-item, .indicator-grid.compact .indicator-item {
      padding: 12px;
    }
    .boundary-grid.compact .boundary-status {
      color: #061511;
    }
    .task-progress {
      margin: 14px 0 8px;
      padding: 15px 16px;
      border: 1px solid rgba(143, 230, 196, 0.20);
      border-radius: 20px;
      background:
        radial-gradient(circle at 100% 0%, rgba(143, 230, 196, 0.10), transparent 18rem),
        rgba(8, 12, 13, 0.66);
    }
    .task-progress[hidden] { display: none; }
    .task-progress-head, .task-progress-meta {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
    }
    .task-progress-head strong { font-size: 14px; }
    .task-progress-percent {
      color: var(--accent);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-weight: 900;
    }
    .task-progress-track {
      height: 9px;
      margin: 11px 0 9px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(244, 240, 232, 0.08);
      box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.32);
    }
    .task-progress-bar {
      display: block;
      width: 0;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #35a47a, #8fe6c4 70%, #d7f8e8);
      box-shadow: 0 0 20px rgba(143, 230, 196, 0.38);
      transition: width 600ms ease;
    }
    .task-progress-meta {
      color: var(--muted);
      font-size: 12px;
    }
    .task-progress.is-complete { border-color: rgba(143, 230, 196, 0.38); }
    .task-progress.is-error { border-color: rgba(255, 122, 122, 0.42); }
    .task-progress.is-error .task-progress-bar { background: linear-gradient(90deg, #a94242, #ff9a9a); }
    @media (max-width: 1100px) {
      .sector-story-layout { grid-template-columns: 1fr; }
      .sector-storyline {
        position: sticky;
        top: 8px;
        z-index: 8;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 6px;
      }
      .story-title { display: none; }
      .story-link { justify-content: center; min-width: 0; padding: 9px 8px; }
    }
    @media (max-width: 820px) {
      .card, .card.wide { grid-column: span 12; }
      .row, .status, .permission-summary, .interpretation-grid, .scorecard-grid, .boundary-grid, .leader-query, .leader-summary-grid, .leader-day-picker, .sector-story-layout, .sector-detail-grid, .trend-board, .knowledge-grid { grid-template-columns: 1fr; }
      .sector-storyline { position: static; grid-template-columns: repeat(2, minmax(0, 1fr)); overflow: visible; }
      .sector-switcher { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .task-progress-head, .task-progress-meta { align-items: flex-start; }
      .hero { padding: 24px; }
      .tab-bar { flex-wrap: wrap; overflow: visible; border-radius: 24px; align-items: stretch; }
      .tab-button { flex: 1 1 126px; padding: 10px 12px; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="eyebrow">Shilun Control Center</div>
      <h1>石论控制台</h1>
      <p>把当前已有能力放到一个页面里：单票分析、日报预览、飞书推送和接口入口都可以从这里点击访问。默认坚持 Mongo-first，不会偷偷调用 Tushare。</p>
      <a class="link-button secondary" href="/docs" target="_blank">打开 API Docs</a>
      <a class="link-button secondary" href="/api/v1/push-channel/status" target="_blank">查看通道状态 JSON</a>
      <a class="link-button secondary" href="/api/v1/tushare/health" target="_blank">查看 Tushare 健康 JSON</a>
    </section>

    <section class="tabs">
      <div class="tab-bar" role="tablist" aria-label="石论控制台功能分区">
        <button class="tab-button active" type="button" data-tab="data" onclick="switchTab('data')">1 数据同步</button>
        <button class="tab-button" type="button" data-tab="permission" onclick="switchTab('permission')">2 权限确认</button>
        <button class="tab-button" type="button" data-tab="market" onclick="switchTab('market')">3 大盘计算</button>
        <button class="tab-button" type="button" data-tab="sectors" onclick="switchTab('sectors')">4 板块动向</button>
        <button class="tab-button" type="button" data-tab="analysis" onclick="switchTab('analysis')">5 单票分析</button>
        <button class="tab-button" type="button" data-tab="push" onclick="switchTab('push')">6 日报推送</button>
        <button class="tab-button" type="button" data-tab="system" onclick="switchTab('system')">7 系统状态</button>
      </div>

      <section id="tab-permission" class="tab-panel" role="tabpanel">
        <div class="grid">
          <div class="card wide">
            <h2>权限确认</h2>
            <div class="row">
              <div>
                <label>权限日期</label>
                <input id="marketDate" type="hidden" value="__DEFAULT_DATE__" />
                <div class="date-wheel" data-target="marketDate"></div>
                <div class="date-wheel-caption"><span>下拉滚轮选择年月日，周末自动回退</span><span class="date-today">默认最近工作日</span></div>
              </div>
              <div>
                <label>基准指数</label>
                <select id="benchmarkTicker">
                  __BENCHMARK_OPTIONS__
                </select>
              </div>
            </div>
            <div class="hint">第一步只确认交易权限和动作边界：000300.SH 是沪深300指数代码；也可以切换上证指数、深成指或创业板指。详细评分和证据链在“大盘计算”TAB。</div>
            <button onclick="runMarketPermission()">确认今日权限</button>
            <button class="secondary" onclick="switchTab('market')">查看大盘计算</button>
            <div id="marketPermissionSummary" class="permission-summary"></div>
            <pre id="marketPermissionConfirmResult">等待确认...</pre>
          </div>

        </div>
      </section>

      <section id="tab-data" class="tab-panel active" role="tabpanel">
        <div class="grid">

      <div class="card wide">
        <h2>数据链路</h2>
        <div id="dataStatus" class="hint">正在检查 Mongo 数据状态...</div>
        <button class="secondary" onclick="loadDataStatus()">刷新数据状态</button>
        <button onclick="syncLatestData()">同步最新交易日</button>
        <button onclick="syncIncrementalData()">增量同步缺口</button>
        <button class="secondary" onclick="useLatestHistoryDate()">使用最近历史日期分析</button>
        <button class="secondary" onclick="syncHistoryYear()">导入最近一年历史</button>
        <pre id="syncResult">链路规则：先查 Mongo；Mongo 有目标日期数据则提示已更新；没有数据时再手动同步 Tushare 或切换到最近历史日期。</pre>
      </div>

      <div class="card wide">
        <h2>Tushare 网关健康</h2>
        <div id="tushareHealthSummary" class="hint">正在检查 Tushare 网关与关键接口...</div>
        <button onclick="loadTushareHealth()">检查网关健康</button>
        <a class="link-button secondary" href="/api/v1/tushare/health" target="_blank">查看健康 JSON</a>
        <pre id="tushareHealthResult">健康检查会固定测试 stock_basic、trade_cal、daily、daily_basic，避免把“已配置”误认为“可用”。</pre>
      </div>
        </div>
      </section>

      <section id="tab-market" class="tab-panel" role="tabpanel">
        <div class="grid">

      <div class="card wide">
        <h2>PART1 大盘计算和解读</h2>
        <div class="hint">基于“权限确认”TAB 的日期和基准指数，输出五维评分、硬否决项、支撑压力、证据链和状态机规则。当前板块口径是 Tushare stock_basic.industry 行业聚合 proxy，不是申万行业指数。</div>
        <div class="row">
          <div>
            <label>查询日期</label>
            <input id="marketQueryDate" type="hidden" value="__DEFAULT_DATE__" />
            <div class="date-wheel" data-target="marketQueryDate"></div>
            <div class="date-wheel-caption"><span>下拉滚轮选择年月日，周末自动回退</span><span class="date-today">默认最近工作日</span></div>
          </div>
          <div>
            <label>指数口径</label>
            <select id="marketBenchmarkTicker">
              __BENCHMARK_OPTIONS__
            </select>
          </div>
        </div>
        <button onclick="runMarketPermission()">查询并更新解读</button>
        <a class="link-button secondary" href="/api/v1/market/permission?date=__DEFAULT_DATE__" target="_blank">查看 PART1 JSON</a>
        <div id="marketPermissionResult" class="market-output">等待计算...</div>
      </div>
        </div>
      </section>

      <section id="tab-sectors" class="tab-panel" role="tabpanel">
        <div class="grid">

      <div class="card wide">
        <h2>PART2 板块动向</h2>
        <div class="hint">用日线数据落地板块生命周期、资金流向、龙头/中军候选、分歧和修复。Tushare moneyflow 已接入；分钟启动时间和封板质量仍会明确标记为待接入。</div>
        <div class="row">
          <div>
            <label>查询日期</label>
            <input id="sectorDate" type="hidden" value="__DEFAULT_DATE__" />
            <div class="date-wheel" data-target="sectorDate"></div>
            <div class="date-wheel-caption"><span>下拉滚轮选择年月日，周末自动回退</span><span class="date-today">默认最近工作日</span></div>
          </div>
          <div>
            <label>对比指数</label>
            <select id="sectorBenchmarkTicker">
              __BENCHMARK_OPTIONS__
            </select>
          </div>
        </div>
        <button id="sectorQueryButton" onclick="runSectorTrends()">查询板块动向</button>
        <a class="link-button secondary" href="/api/v1/market/sectors?date=__DEFAULT_DATE__" target="_blank">查看板块 JSON</a>
        <div id="sectorProgress" class="task-progress" hidden aria-live="polite">
          <div class="task-progress-head">
            <strong id="sectorProgressLabel">准备计算</strong>
            <span id="sectorProgressPercent" class="task-progress-percent">0%</span>
          </div>
          <div class="task-progress-track"><span id="sectorProgressBar" class="task-progress-bar"></span></div>
          <div class="task-progress-meta">
            <span id="sectorProgressElapsed">已等待 0 秒</span>
            <span id="sectorProgressRemaining">预计完整结果剩余约 65 秒</span>
          </div>
        </div>
        <div id="sectorTrendResult" class="market-output">等待计算...</div>
      </div>
        </div>
      </section>

      <section id="tab-analysis" class="tab-panel" role="tabpanel">
        <div class="grid">

      <div class="card">
        <h2>单票分析</h2>
        <div class="row">
          <div>
            <label>股票代码</label>
            <input id="analyzeTicker" value="600132.SH" />
          </div>
          <div>
            <label>分析日期</label>
            <input id="analyzeDate" type="hidden" value="__DEFAULT_DATE__" />
            <div class="date-wheel" data-target="analyzeDate"></div>
            <div class="date-wheel-caption"><span>下拉滚轮选择年月日，周末自动回退</span><span class="date-today">默认最近工作日</span></div>
          </div>
        </div>
        <div class="checks">
          <label class="check"><input id="allowFallback" type="checkbox" /> 允许显式 Tushare fallback</label>
        </div>
        <div id="analyzeHint" class="hint"></div>
        <button onclick="runAnalyze()">运行分析</button>
        <button class="secondary" onclick="runTelegramText()">生成 Telegram 文本</button>
        <pre id="analyzeResult">等待操作...</pre>
      </div>
        </div>
      </section>

      <section id="tab-push" class="tab-panel" role="tabpanel">
        <div class="grid">

      <div class="card">
        <h2>每日日报 / 飞书</h2>
        <div class="row">
          <div>
            <label>日报日期</label>
            <input id="pushDate" type="hidden" value="__DEFAULT_DATE__" />
            <div class="date-wheel" data-target="pushDate"></div>
            <div class="date-wheel-caption"><span>下拉滚轮选择年月日，周末自动回退</span><span class="date-today">默认最近工作日</span></div>
          </div>
          <div>
            <label>消息 Top K</label>
            <input id="messageTopK" type="number" min="1" max="100" value="20" />
          </div>
        </div>
        <div class="checks">
          <label class="check"><input id="dryRun" type="checkbox" checked /> 只预览，不发送</label>
          <label class="check"><input id="includePool" type="checkbox" checked /> 包含候选池</label>
          <label class="check"><input id="allowSnapshotFallback" type="checkbox" /> 允许快照 fallback</label>
        </div>
        <button onclick="runDailyPush()">执行日报</button>
        <pre id="pushResult">建议先保持“只预览”，确认内容后再取消勾选发送飞书。</pre>
      </div>

      <div class="card wide">
        <h2>快捷入口</h2>
        <a class="link-button" href="/api/v1/telegram/get-me" target="_blank">Telegram getMe</a>
        <a class="link-button" href="/openapi.json" target="_blank">OpenAPI JSON</a>
        <a class="link-button secondary" href="/api/v1/analyze?ticker=600132.SH&date=__DEFAULT_DATE__" target="_blank">示例单票 JSON</a>
      </div>
        </div>
      </section>

      <section id="tab-system" class="tab-panel" role="tabpanel">
        <div class="grid">
          <div class="card wide">
            <h2>系统状态</h2>
            <div class="hint">集中检查后台依赖：Mongo、飞书、Telegram Bot 和日报推送配置，不混在业务判断 TAB 里。</div>
            <div id="status" class="status"></div>
          </div>
        </div>
      </section>
    </section>
  </main>

  <script>
    let runtimeStatus = {};
    let latestSectorData = null;
    let sectorRequestVersion = 0;
    let selectedSectorIndex = 0;
    let selectedLeaderView = "total";
    let selectedLeaderDate = "";
    let leaderTickerDraft = "";
    let sectorProgressTimer = null;
    let sectorProgressState = { version: 0, phase: "idle", startedAt: 0, phaseStartedAt: 0 };
    const pretty = (value) => JSON.stringify(value, null, 2);
    const formatError = (error, context = "generic") => {
      if (typeof error === "string") return error;
      if (error && error.name === "AbortError") {
        const timeoutMessages = {
          analysis: "请求超时：单票分析可能正在等待 Tushare 或 Mongo。建议确认 Mongo 已同步该日期数据。",
          sectors: "板块计算超时：主结果未在限定时间内完成，请重试或检查后台负载。",
          leaders: "近30个交易日龙头榜仍在计算，板块主结果不受影响。可稍后单独重试。",
        };
        return timeoutMessages[context] || "请求超时：服务未在限定时间内返回，请稍后重试。";
      }
      if (error && error.detail) return `请求失败：${error.detail}`;
      if (error && error.message) return `请求失败：${error.message}`;
      try {
        const rendered = JSON.stringify(error, null, 2);
        return rendered && rendered !== "{}" ? rendered : "请求失败：浏览器没有返回具体错误。请检查服务终端日志。";
      } catch {
        return "请求失败：未知错误。请检查服务终端日志。";
      }
    };
    const show = (id, value) => { document.getElementById(id).textContent = typeof value === "string" ? value : pretty(value); };
    const showHtml = (id, value) => { document.getElementById(id).innerHTML = value; };
    const formatWaitDuration = (seconds) => {
      const safe = Math.max(0, Math.round(Number(seconds) || 0));
      if (safe < 60) return `${safe} 秒`;
      const minutes = Math.floor(safe / 60);
      const rest = safe % 60;
      return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
    };
    function renderSectorProgressTick() {
      const root = document.getElementById("sectorProgress");
      if (!root || sectorProgressState.phase === "idle") return;
      const now = Date.now();
      const elapsed = Math.max(0, (now - sectorProgressState.startedAt) / 1000);
      const phaseElapsed = Math.max(0, (now - sectorProgressState.phaseStartedAt) / 1000);
      let label = "准备计算";
      let progress = 0;
      let remainingText = "预计完整结果剩余约 65 秒";
      if (sectorProgressState.phase === "main") {
        label = "阶段 1/2：计算强力板块与趋势榜（估算进度）";
        progress = Math.min(68, 5 + (phaseElapsed / 34) * 63);
        const remaining = Math.max(5, 34 - phaseElapsed) + 32;
        remainingText = `预计完整结果剩余约 ${formatWaitDuration(remaining)}`;
      } else if (sectorProgressState.phase === "leaders") {
        label = "阶段 2/2：生成近 30 个交易日龙头榜（估算进度）";
        progress = Math.min(98, 70 + (phaseElapsed / 32) * 28);
        const remaining = Math.max(2, 32 - phaseElapsed);
        remainingText = `预计龙头榜剩余约 ${formatWaitDuration(remaining)}`;
      } else if (sectorProgressState.phase === "complete") {
        label = "板块动向与龙头榜均已完成";
        progress = 100;
        remainingText = "已完成，无需等待";
      } else if (sectorProgressState.phase === "partial_error") {
        label = "板块主结果已完成，龙头榜未能完成";
        progress = 70;
        remainingText = "已停止，可单独重试龙头榜";
      } else if (sectorProgressState.phase === "error") {
        label = "板块动向计算失败";
        progress = Math.max(5, sectorProgressState.progress || 5);
        remainingText = "已停止，请按错误提示处理后重试";
      }
      root.hidden = false;
      root.classList.toggle("is-complete", sectorProgressState.phase === "complete");
      root.classList.toggle("is-error", sectorProgressState.phase === "error" || sectorProgressState.phase === "partial_error");
      document.getElementById("sectorProgressLabel").textContent = label;
      document.getElementById("sectorProgressPercent").textContent = `${Math.round(progress)}%`;
      document.getElementById("sectorProgressBar").style.width = `${progress}%`;
      document.getElementById("sectorProgressElapsed").textContent = `已等待 ${formatWaitDuration(elapsed)}`;
      document.getElementById("sectorProgressRemaining").textContent = remainingText;
      sectorProgressState.progress = progress;
    }
    function startSectorProgress(version) {
      if (sectorProgressTimer) clearInterval(sectorProgressTimer);
      const now = Date.now();
      sectorProgressState = { version, phase: "main", startedAt: now, phaseStartedAt: now, progress: 5 };
      const button = document.getElementById("sectorQueryButton");
      if (button) {
        button.disabled = true;
        button.textContent = "正在计算主结果...";
      }
      renderSectorProgressTick();
      sectorProgressTimer = setInterval(renderSectorProgressTick, 1000);
    }
    function advanceSectorProgressToLeaders(version) {
      if (sectorProgressState.version !== version) return;
      sectorProgressState.phase = "leaders";
      sectorProgressState.phaseStartedAt = Date.now();
      const button = document.getElementById("sectorQueryButton");
      if (button) {
        button.disabled = false;
        button.textContent = "重新查询板块动向";
      }
      renderSectorProgressTick();
    }
    function completeSectorProgress(version) {
      if (sectorProgressState.version !== version) return;
      if (sectorProgressTimer) clearInterval(sectorProgressTimer);
      sectorProgressTimer = null;
      sectorProgressState.phase = "complete";
      const button = document.getElementById("sectorQueryButton");
      if (button) {
        button.disabled = false;
        button.textContent = "查询板块动向";
      }
      renderSectorProgressTick();
    }
    function failSectorProgress(version, partial = false) {
      if (sectorProgressState.version !== version) return;
      if (sectorProgressTimer) clearInterval(sectorProgressTimer);
      sectorProgressTimer = null;
      sectorProgressState.phase = partial ? "partial_error" : "error";
      const button = document.getElementById("sectorQueryButton");
      if (button) {
        button.disabled = false;
        button.textContent = "重新查询板块动向";
      }
      renderSectorProgressTick();
    }
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[char]));
    const statusCell = (label, ok, detail) => `<div class="pill"><strong>${label}</strong><span class="${ok ? "ok" : "bad"}">${ok ? "已配置" : "未配置"}</span><small>${detail || ""}</small></div>`;
    const activeTabId = () => document.querySelector(".tab-button.active")?.dataset.tab || "data";
    const selectedBenchmarkTicker = () => (
      activeTabId() === "market"
        ? document.getElementById("marketBenchmarkTicker").value
        : activeTabId() === "sectors"
        ? document.getElementById("sectorBenchmarkTicker").value
        : document.getElementById("benchmarkTicker").value
    );
    const selectedMarketDate = () => (
      activeTabId() === "market"
        ? document.getElementById("marketQueryDate").value
        : activeTabId() === "sectors"
        ? document.getElementById("sectorDate").value
        : document.getElementById("marketDate").value
    );
    const pad2 = (value) => String(value).padStart(2, "0");
    const parseIsoDate = (value) => {
      const fallback = new Date();
      const match = String(value || "").match(/^(\\d{4})-(\\d{2})-(\\d{2})$/);
      if (!match) return { year: fallback.getFullYear(), month: fallback.getMonth() + 1, day: fallback.getDate() };
      return { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]) };
    };
    const daysInMonth = (year, month) => new Date(year, month, 0).getDate();
    const buildIsoDate = (year, month, day) => `${year}-${pad2(month)}-${pad2(day)}`;
    const normalizeWeekdayDate = (value) => {
      const { year, month, day } = parseIsoDate(value);
      const date = new Date(year, month - 1, day, 12, 0, 0);
      if (date.getDay() === 6) date.setDate(date.getDate() - 1);
      if (date.getDay() === 0) date.setDate(date.getDate() - 2);
      return buildIsoDate(date.getFullYear(), date.getMonth() + 1, date.getDate());
    };
    function setDateControl(id, value) {
      const target = document.getElementById(id);
      if (!target) return;
      target.value = value;
      const wheel = document.querySelector(`.date-wheel[data-target="${id}"]`);
      if (!wheel) return;
      const { year, month, day } = parseIsoDate(value);
      const yearSelect = wheel.querySelector('[data-part="year"]');
      const monthSelect = wheel.querySelector('[data-part="month"]');
      const daySelect = wheel.querySelector('[data-part="day"]');
      if (yearSelect) yearSelect.value = String(year);
      if (monthSelect) monthSelect.value = String(month);
      if (daySelect) {
        rebuildDayOptions(daySelect, year, month);
        daySelect.value = String(Math.min(day, daysInMonth(year, month)));
      }
    }
    function rebuildDayOptions(daySelect, year, month) {
      const maxDay = daysInMonth(year, month);
      const previous = Number(daySelect.value || 1);
      daySelect.innerHTML = Array.from({ length: maxDay }, (_, index) => {
        const day = index + 1;
        return `<option value="${day}">${pad2(day)} 日</option>`;
      }).join("");
      daySelect.value = String(Math.min(previous, maxDay));
    }
    function initDateWheels() {
      const now = new Date();
      const currentYear = now.getFullYear();
      document.querySelectorAll(".date-wheel[data-target]").forEach((wheel) => {
        const targetId = wheel.dataset.target;
        const target = document.getElementById(targetId);
        if (!target) return;
        const { year, month, day } = parseIsoDate(target.value);
        const years = [];
        for (let item = currentYear - 4; item <= currentYear + 1; item += 1) years.push(item);
        wheel.innerHTML = `
          <select data-part="year" aria-label="年份">${years.map((item) => `<option value="${item}">${item} 年</option>`).join("")}</select>
          <select data-part="month" aria-label="月份">${Array.from({ length: 12 }, (_, index) => `<option value="${index + 1}">${pad2(index + 1)} 月</option>`).join("")}</select>
          <select data-part="day" aria-label="日期"></select>
        `;
        const yearSelect = wheel.querySelector('[data-part="year"]');
        const monthSelect = wheel.querySelector('[data-part="month"]');
        const daySelect = wheel.querySelector('[data-part="day"]');
        yearSelect.value = String(year);
        monthSelect.value = String(month);
        rebuildDayOptions(daySelect, year, month);
        daySelect.value = String(day);
        const commit = () => {
          const nextYear = Number(yearSelect.value);
          const nextMonth = Number(monthSelect.value);
          rebuildDayOptions(daySelect, nextYear, nextMonth);
          const nextDay = Number(daySelect.value);
          target.value = normalizeWeekdayDate(buildIsoDate(nextYear, nextMonth, nextDay));
          setDateControl(targetId, target.value);
          target.dispatchEvent(new Event("change", { bubbles: true }));
        };
        yearSelect.addEventListener("change", commit);
        monthSelect.addEventListener("change", commit);
        daySelect.addEventListener("change", commit);
      });
    }

    function syncBenchmarkSelects(value) {
      document.getElementById("benchmarkTicker").value = value;
      document.getElementById("marketBenchmarkTicker").value = value;
      document.getElementById("sectorBenchmarkTicker").value = value;
    }

    function syncMarketDates(value) {
      ["marketDate", "marketQueryDate", "sectorDate", "analyzeDate", "pushDate"].forEach((id) => setDateControl(id, value));
    }

    function syntaxHighlightJson(value) {
      return pretty(value).replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\\s*:)?|\\b(true|false|null)\\b|-?\\d+(?:\\.\\d*)?(?:[eE][+\\-]?\\d+)?)/g, (match) => {
        const safe = escapeHtml(match);
        if (/^"/.test(match)) {
          return /:$/.test(match) ? `<span class="json-key">${safe}</span>` : `<span class="json-string">${safe}</span>`;
        }
        if (/true|false/.test(match)) return `<span class="json-boolean">${safe}</span>`;
        if (/null/.test(match)) return `<span class="json-null">${safe}</span>`;
        return `<span class="json-number">${safe}</span>`;
      });
    }

    function switchTab(tabId) {
      document.querySelectorAll(".tab-button").forEach((button) => {
        button.classList.toggle("active", button.dataset.tab === tabId);
      });
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.classList.toggle("active", panel.id === `tab-${tabId}`);
      });
    }

    function permissionClass(permission) {
      if (permission === "attack") return "ok";
      if (permission === "empty") return "bad";
      if (permission === "defense") return "bad";
      return "";
    }

    function renderPermissionSummary(data) {
      const cls = permissionClass(data.market_permission);
      document.getElementById("marketPermissionSummary").innerHTML = [
        `<div class="decision-card"><strong>权限状态</strong><span class="${cls}">${data.permission_label}</span></div>`,
        `<div class="decision-card"><strong>总分</strong><span>${data.total_score}</span></div>`,
        `<div class="decision-card"><strong>风险分</strong><span>${data.scores && data.scores.risk_score}</span></div>`,
        `<div class="decision-card"><strong>动作边界</strong><span>${data.action_permission && data.action_permission.max_new_position}</span></div>`
      ].join("");
      const lines = [
        `权限：${data.market_permission} / ${data.permission_label}`,
        `结论：${(data.interpretation && data.interpretation.headline) || data.permission_summary}`,
        `动作：${data.action_permission && data.action_permission.text}`,
        `总分：${data.total_score}`,
        `五维：趋势 ${data.scores.trend_score}，量能 ${data.scores.volume_score}，广度 ${data.scores.breadth_score}，主线 ${data.scores.theme_score}，风险 ${data.scores.risk_score}`,
        "",
        "核心判断：",
        ...((data.interpretation && data.interpretation.sections) || []).map((section) => `- ${section.title}：${section.conclusion}`),
        "",
        "关键证据：",
        ...(data.evidence || []).slice(0, 5).map((item) => `- ${item}`)
      ].join("\\n");
      show("marketPermissionConfirmResult", lines);
    }

    function renderInterpretation(data) {
      const interpretation = data.interpretation || {};
      const rawPayload = {
        scores: data.scores,
        metrics: data.metrics,
        levels: data.levels,
        theme_method: data.theme_method,
        theme_candidates: data.theme_candidates,
        hard_triggers: data.hard_triggers,
        state_machine: data.state_machine,
        data_quality: data.data_quality
      };
      const sections = (interpretation.sections || []).map((section) => `
        <article class="interpretation-card">
          <h3>${escapeHtml(section.title)}</h3>
          <p class="section-conclusion">${escapeHtml(section.conclusion)}</p>
          ${(section.rows || []).map((row) => `
            <div class="metric-row">
              <div class="metric-label">${escapeHtml(row.indicator)}</div>
              <div class="metric-value">${escapeHtml(row.value)}</div>
              <div class="metric-judgement">${escapeHtml(row.judgement)}</div>
            </div>
          `).join("")}
        </article>
      `).join("");
      const scorecards = (interpretation.scorecard || []).map((item) => `
        <div class="scorecard-item">
          <strong>${escapeHtml(item.dimension)}</strong>
          <span>${escapeHtml(item.score)}</span>
          <small>${escapeHtml(item.standard)}</small>
        </div>
      `).join("");
      return `
        <section class="market-hero-card">
          <div class="market-eyebrow">${escapeHtml(data.benchmark_name || data.benchmark_ticker)} · ${escapeHtml(data.benchmark_ticker)}</div>
          <h2 class="market-headline">${escapeHtml(interpretation.headline || data.permission_summary)}</h2>
          <p class="market-subtitle">${escapeHtml(data.action_permission && data.action_permission.text)}</p>
          <div class="market-meta">
            <span class="meta-chip">权限 ${escapeHtml(data.market_permission)} / ${escapeHtml(data.permission_label)}</span>
            <span class="meta-chip">总分 ${escapeHtml(data.total_score)}</span>
            <span class="meta-chip">趋势 ${escapeHtml(data.scores.trend_score)} · 量能 ${escapeHtml(data.scores.volume_score)} · 广度 ${escapeHtml(data.scores.breadth_score)} · 主线 ${escapeHtml(data.scores.theme_score)} · 风险 ${escapeHtml(data.scores.risk_score)}</span>
          </div>
        </section>
        <section class="interpretation-grid">${sections}</section>
        <section class="interpretation-card">
          <h3>评分卡和判断标准</h3>
          <p class="section-conclusion">每个分数都来自明确指标，避免只看一句“防守/进攻”而不知道原因。</p>
          <div class="scorecard-grid">${scorecards}</div>
        </section>
        <details class="json-viewer">
          <summary>查看标准化 JSON 原始字段</summary>
          <pre class="json-code">${syntaxHighlightJson(rawPayload)}</pre>
        </details>
      `;
    }

    function renderMarketPermissionDetail(data) {
      showHtml("marketPermissionResult", renderInterpretation(data));
    }

    function definitionBubble(text) {
      return `
        <details class="info-popover">
          <summary aria-label="定义说明">!</summary>
          <div class="info-panel">${escapeHtml(text)}<a href="#sector-indicator-definitions">查看指标口径</a></div>
        </details>
      `;
    }
    function reasonList(items) {
      return `<ol class="reason-list">${items.map((item) => `<li>${item}</li>`).join("")}</ol>`;
    }
    function scoreRef(label, value, definition) {
      return `
        <span class="score-ref">
          ${escapeHtml(label)} ${escapeHtml(value ?? "-")}
          ${definitionBubble(definition)}
        </span>
      `;
    }
    function metricBlock(label, definition, value, explanation) {
      return `
        <div class="metric-row">
          <div class="metric-label">${escapeHtml(label)} ${definitionBubble(definition)}</div>
          <div class="metric-value">${value}</div>
          <div class="metric-explain">${explanation}</div>
        </div>
      `;
    }
    function leaderExplanation(leader, sector) {
      if (!leader || !leader.ticker) return "当前板块没有足够清晰的龙头候选，先按板块整体状态观察。";
      const subs = leader.leader_subscores || {};
      return reasonList([
        `综合分 ${escapeHtml(leader.leader_score || "-")}，角色 ${escapeHtml(leader.role_label || "-")}。`,
        `5日收益 ${escapeHtml(formatPercent(leader.return_5d))}，相对板块 ${escapeHtml(formatPercent(leader.relative_return_5d))}，5日涨停 ${escapeHtml(leader.limit_up_count_5d || 0)} 次，大涨 ${escapeHtml(leader.big_up_count_5d || 0)} 次。`,
        `子项：${scoreRef("启动", subs.startup_score, "启动分：涨停次数、相对板块收益和率先走强代理。")}${scoreRef("强度", subs.strength_score, "强度分：5日收益排名、涨停次数、大涨次数。")}${scoreRef("带动", subs.drive_score, "带动分：板块上涨占比、板块当日收益和个股成交额排名。")}${scoreRef("抗跌/修复", subs.resilience_score, "抗跌/修复分：趋势未破、收盘位置、近10日最大回撤。")}${scoreRef("封板质量代理", subs.board_quality_score, "封板质量代理：未接 limit_list_d 前，用涨停、收盘位置、上影线和成交额排名代替。")}`,
        `比较：板块上涨占比 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.up_ratio))}。如果个股强但板块不扩散，会降为“强势先锋”而不是总龙头。`,
      ]);
    }
    function zhongjunExplanation(zhongjun) {
      if (!zhongjun || !zhongjun.ticker) return "当前板块没有足够清晰的中军候选，说明容量或趋势稳定性还不够突出。";
      const subs = zhongjun.zhongjun_subscores || {};
      return reasonList([
        `综合分 ${escapeHtml(zhongjun.zhongjun_score || "-")}，角色 ${escapeHtml(zhongjun.role_label || "-")}。`,
        `容量：成交额 ${escapeHtml(formatTradeAmount(zhongjun.amount))}，流通市值 ${escapeHtml(formatMarketValue(zhongjun.circ_mv))}，换手率 ${escapeHtml(formatPercent((zhongjun.turnover_rate || 0) / 100))}。`,
        `资金：主力净流入 ${escapeHtml(formatMoneyWan(zhongjun.main_net_inflow))}，净流入占比 ${escapeHtml(formatPercent(zhongjun.main_net_inflow_rate))}。`,
        `趋势：收盘 ${escapeHtml(zhongjun.close || "-")}，MA10 ${escapeHtml(zhongjun.ma10 || "-")}，趋势${zhongjun.trend_unbroken ? "未破" : "已弱化"}。`,
        `子项：${scoreRef("容量", subs.capacity_score, "容量分：流通市值在板块内的分位。")}${scoreRef("成交稳定", subs.amount_stability_score, "成交稳定分：成交额排名和近5日成交额波动。")}${scoreRef("净流入", subs.net_flow_score ?? "待数据", "净流入分：moneyflow 大单+特大单净流入占成交额比例。")}${scoreRef("趋势稳定", subs.trend_stability_score, "趋势稳定分：MA10/MA20 趋势和近10日最大回撤。")}${scoreRef("换手稳定", subs.turnover_stability_score, "换手稳定分：换手率处于可承接区间更高。")}`,
      ]);
    }
    function divergenceExplanation(sector, divergence, repair) {
      const triggers = (divergence.triggers || []).map((item) => item.meaning).join("；");
      if (repair && repair.confirmed) {
        return reasonList([
          "前面存在分歧基础。",
          `今日板块强于大盘，上涨占比 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.up_ratio))}。`,
          `核心修复数量 ${escapeHtml(repair.core_repair_count || 0)}，因此判为 ${escapeHtml(repair.label || "修复")}。`,
        ]);
      }
      if (divergence && divergence.score) {
        return reasonList([
          `分歧分 ${escapeHtml(divergence.score)}。`,
          `触发项：${escapeHtml(triggers || "-")}。`,
        ]);
      }
      return reasonList([
        "当前成交额、广度和核心反馈没有触发明确分歧。",
        "接入分钟线后，可进一步判断盘中跳水和率先修复。",
      ]);
    }
    function setLeaderView(view) {
      leaderTickerDraft = document.getElementById("leaderTickerQuery")?.value || leaderTickerDraft;
      selectedLeaderView = view === "daily" ? "daily" : "total";
      if (latestSectorData) showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
    }
    function setSelectedLeaderDate(value) {
      leaderTickerDraft = document.getElementById("leaderTickerQuery")?.value || leaderTickerDraft;
      selectedLeaderDate = value;
      if (latestSectorData) showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
    }
    function renderLeaderQueryPanel(ready) {
      return `
        <div class="leader-query-panel">
          <h4>查询单个龙头的 30 日记录</h4>
          <p>输入股票代码后，统计上榜次数、第一名次数、平均名次、连续上榜和每日明细。</p>
          <div class="leader-query">
            <input id="leaderTickerQuery" value="${escapeHtml(leaderTickerDraft)}" oninput="leaderTickerDraft=this.value" placeholder="输入股票代码，例如 002484.SZ" ${ready ? "" : "disabled"} />
            <button class="secondary" onclick="queryLeaderStreak()" ${ready ? "" : "disabled"}>查询单票记录</button>
          </div>
          <div id="leaderStreakResult" class="leader-query-result hint">${ready ? "输入股票代码后查询近30个交易日的龙头榜记录。" : "龙头榜计算完成后可查询单票记录。"}</div>
        </div>
      `;
    }
    function renderLeaderSummary(summary) {
      const ranking = (summary && summary.ranking) || [];
      if (!ranking.length) return '<p class="section-conclusion">当前窗口没有形成可用的多日总龙头记录。</p>';
      return `
        <p class="section-conclusion">
          统计区间 ${escapeHtml(summary.window_start || "-")} 至 ${escapeHtml(summary.window_end || "-")}，共 ${escapeHtml(summary.trading_day_count || 0)} 个交易日。${escapeHtml(summary.formula || "")}
        </p>
        <div class="leader-summary-grid">
          ${ranking.map((leader, index) => `
            <article class="leader-summary-card ${index === 0 ? "is-champion" : ""}">
              <div class="leader-card-top">
                <div>
                  <div class="leader-card-rank">总榜 #${index + 1}</div>
                  <div class="leader-card-name">${escapeHtml(leader.name || "-")}</div>
                  <div class="leader-card-code">${escapeHtml(leader.ticker || "-")} · ${escapeHtml(leader.primary_sector || "-")}</div>
                </div>
                <div class="leader-summary-score">${escapeHtml(leader.summary_score || "-")}</div>
              </div>
              <div class="leader-role-label">${escapeHtml(leader.role_label || "-")} · ${escapeHtml(leader.primary_daily_role || "-")}</div>
              <div class="leader-stat-row">
                <span class="leader-stat">上榜 ${escapeHtml(leader.appearance_count || 0)} 次</span>
                <span class="leader-stat">第一名 ${escapeHtml(leader.rank_1_count || 0)} 次</span>
                <span class="leader-stat">平均名次 ${escapeHtml(leader.average_rank || "-")}</span>
                <span class="leader-stat">平均龙头分 ${escapeHtml(leader.average_leader_score || "-")}</span>
                <span class="leader-stat">最近 ${escapeHtml(leader.latest_date || "-")}</span>
              </div>
              ${reasonList((leader.evidence || []).map(escapeHtml))}
            </article>
          `).join("")}
        </div>
      `;
    }
    function renderSelectedLeaderDay(days) {
      if (!days.length) return '<p class="section-conclusion">暂无单日龙头数据。</p>';
      if (!selectedLeaderDate || !days.some((day) => day.date === selectedLeaderDate)) {
        selectedLeaderDate = days[days.length - 1].date;
      }
      const selectedDay = days.find((day) => day.date === selectedLeaderDate) || days[days.length - 1];
      return `
        <div class="leader-day-picker">
          <select aria-label="龙头榜日期" onchange="setSelectedLeaderDate(this.value)">
            ${[...days].reverse().map((day) => `<option value="${escapeHtml(day.date)}" ${day.date === selectedDay.date ? "selected" : ""}>${escapeHtml(day.date)}</option>`).join("")}
          </select>
          <p>单日榜只展示所选交易日的全市场前 5 名，不与30日总榜混排。</p>
        </div>
        <div class="leader-board">
          <div class="leader-day">
            <h4>${escapeHtml(selectedDay.date)} · 每日龙头前 5</h4>
            <ul class="leader-list">
              ${(selectedDay.leaders || []).map((leader) => `
                <li>
                  <span class="leader-rank">#${escapeHtml(leader.rank)}</span>
                  <strong>${escapeHtml(leader.name || "-")} (${escapeHtml(leader.ticker || "-")})</strong>
                  <span> · ${escapeHtml(leader.sector_name || "-")} · ${escapeHtml(leader.role_label || "-")} · 分 ${escapeHtml(leader.leader_score || "-")}</span>
                </li>
              `).join("")}
            </ul>
          </div>
        </div>
      `;
    }
    function renderDailyLeaders(data) {
      const days = data.daily_leaders || [];
      const error = data.daily_leaders_error;
      if (!days.length) {
        return `
          <section id="dailyLeaderSection" class="sector-feature">
            <div class="feature-kicker">功能区三 · Leader Board</div>
            <div class="feature-header">
              <div>
                <h3>龙头榜</h3>
                <p>默认生成近30个交易日总龙头榜，同时保留单日榜和单票查询。</p>
              </div>
            </div>
            <div class="leader-status">
              <span class="leader-dot"></span>
              <span>${escapeHtml(error || "主板块和趋势榜已返回；30日龙头榜正在独立计算，完成后会自动刷新这里。")}</span>
            </div>
            ${error ? `<button class="secondary" onclick="loadDailyLeaders(document.getElementById('sectorDate').value, document.getElementById('sectorBenchmarkTicker').value)">重试龙头榜</button>` : ""}
            ${renderLeaderQueryPanel(false)}
          </section>
        `;
      }
      return `
        <section id="dailyLeaderSection" class="sector-feature">
          <div class="feature-kicker">功能区三 · Leader Board</div>
          <div class="feature-header">
            <div>
              <h3>龙头榜</h3>
              <p>总榜识别过去30个交易日反复成为市场情绪锚的股票；单日榜用于复盘某一个交易日。</p>
            </div>
          </div>
          <div class="leader-view-tabs" role="tablist" aria-label="龙头榜视图">
            <button class="leader-view-tab ${selectedLeaderView === "total" ? "is-active" : ""}" onclick="setLeaderView('total')">30日总龙头榜</button>
            <button class="leader-view-tab ${selectedLeaderView === "daily" ? "is-active" : ""}" onclick="setLeaderView('daily')">单日龙头榜</button>
          </div>
          ${selectedLeaderView === "daily" ? renderSelectedLeaderDay(days) : renderLeaderSummary(data.leader_summary || {})}
          ${renderLeaderQueryPanel(true)}
        </section>
      `;
    }
    function renderDataQuality(items) {
      return `
        <div class="boundary-grid compact">
          ${(items || []).map((item) => `
            <div class="boundary-item">
              <strong>${escapeHtml(item.group || item.field)}</strong>
              <span class="boundary-status">${escapeHtml(item.status_label || translateStatus(item.status))}</span>
              <h4>${escapeHtml(item.field)}</h4>
              <p>${escapeHtml(item.note)}</p>
            </div>
          `).join("")}
        </div>
      `;
    }
    function renderIndicatorDefinitions(items) {
      return `
        <div id="sector-indicator-definitions" class="indicator-grid compact">
          ${(items || []).map((item) => `
            <div class="indicator-item" id="indicator-${escapeHtml(item.indicator || "").replace(/[^a-zA-Z0-9_-]/g, "-")}">
              <h4>${escapeHtml(item.indicator)}</h4>
              <div class="indicator-formula">${escapeHtml(item.formula)}</div>
              <p>${escapeHtml(item.meaning)}</p>
            </div>
          `).join("")}
        </div>
      `;
    }
    function translateStatus(status) {
      return ({
        implemented: "已接入",
        "implemented/proxy_only": "已接入/代理口径",
        data_pending: "待接入",
        proxy_only: "代理口径",
      })[status] || status || "-";
    }
    function sectorStrengthClass(sector) {
      const score = Number((sector.scores && sector.scores.sector_score) || 0);
      if (score >= 75) return "strength-high";
      if (score >= 58) return "strength-mid";
      return "strength-watch";
    }
    function setSelectedSector(index) {
      selectedSectorIndex = Number(index) || 0;
      if (latestSectorData) showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
    }
    function scrollSectorFeature(id) {
      const node = document.getElementById(id);
      if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
      document.querySelectorAll(".story-link").forEach((button) => {
        button.classList.toggle("active", button.dataset.target === id);
      });
    }
    function renderSectorSwitcher(sectors) {
      if (!sectors.length) return "";
      selectedSectorIndex = Math.min(Math.max(selectedSectorIndex, 0), sectors.length - 1);
      return `
        <div class="sector-switcher" aria-label="切换强力板块">
          ${sectors.map((sector, index) => `
            <button class="sector-chip ${sectorStrengthClass(sector)} ${index === selectedSectorIndex ? "is-active" : ""}" onclick="setSelectedSector(${index})">
              <strong>${escapeHtml(sector.sector_name)}</strong>
              <small>${escapeHtml(sector.stage_label || "-")} · 分 ${escapeHtml(sector.scores && sector.scores.sector_score)}</small>
            </button>
          `).join("")}
        </div>
      `;
    }
    function renderSelectedSectorDetail(sector) {
      const leader = (sector.leader_candidates || [])[0] || {};
      const zhongjun = (sector.zhongjun_candidates || [])[0] || {};
      const divergence = sector.divergence || {};
      const repair = sector.repair || {};
      const fund = sector.fund_flow || {};
      return `
        <div class="sector-detail-grid">
          <article class="sector-detail-card full">
            <h3>${escapeHtml(sector.sector_name)} · ${escapeHtml(sector.stage_label)}</h3>
            <p class="section-conclusion">${escapeHtml(sector.stage_meaning)} ${escapeHtml(sector.action)}</p>
            ${metricBlock(
              "强弱和排名",
              "用 sector_score、近5日收益、相对大盘收益、近5日跑赢天数和成交额排名衡量板块是否持续强于市场。",
              `评分 ${escapeHtml(sector.scores && sector.scores.sector_score)}，5日收益 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.return_5d))}，相对大盘 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.relative_return_5d))}`,
              reasonList([
                `近5日跑赢 ${escapeHtml(sector.metrics && sector.metrics.outperform_days_5)} 天，成交额排名 ${escapeHtml(sector.metrics && sector.metrics.amount_rank)} / ${escapeHtml(sector.metrics && sector.metrics.total_sector_count)}。`,
                `${scoreRef("相对强弱分", sector.scores && sector.scores.relative_strength_score, "相对强弱分：5日跑赢大盘天数、5日相对收益和是否强于基准。")}${scoreRef("成交活跃分", sector.scores && sector.scores.amount_activity_score, "成交活跃分：成交额相对5日均、成交额排名和成交额占比变化。")}`,
              ])
            )}
          </article>
          <article class="sector-detail-card">
            ${metricBlock(
              "资金口径",
              "成交额只说明活跃度、容量和博弈强度；moneyflow 官方资金流接入后，用大单+特大单净额判断主力净流入。",
              `${escapeHtml(fund.label || "-")} · 成交额占比 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.market_share))} · 相对5日均 ${escapeHtml(formatRatio(sector.metrics && sector.metrics.amount_ratio_5))}`,
              reasonList([
                `${escapeHtml(fund.meaning || "moneyflow 未接入时，只能确认成交活跃，不能确认主力净流入。")}`,
                `主力净流入 ${escapeHtml(formatMoneyWan(fund.main_net_inflow))}，净流入占比 ${escapeHtml(formatPercent(fund.main_net_inflow_rate))}，净流入扩散 ${escapeHtml(formatPercent(fund.positive_moneyflow_ratio))}，近3日持续 ${escapeHtml(fund.moneyflow_persistence_3d ?? 0)} 天。`,
                `${scoreRef("资金状态", translateStatus(fund.data_status), "implemented 表示已读取 Tushare moneyflow；moneyflow_data_pending 表示 Mongo 暂无对应资金流。")}`,
              ])
            )}
            ${metricBlock(
              "广度",
              "上涨占比超过 55%-60% 才能说明板块不是孤立个股行情；低于 50% 时，即使板块指数上涨，也可能只是少数权重或个股拉动。",
              `上涨占比 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.up_ratio))}，涨停 ${escapeHtml(sector.metrics && sector.metrics.limit_up_count)}，大涨 ${escapeHtml(sector.metrics && sector.metrics.big_up_count)}`,
              reasonList([
                `上涨占比 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.up_ratio))}，涨停 ${escapeHtml(sector.metrics && sector.metrics.limit_up_count)}，大涨 ${escapeHtml(sector.metrics && sector.metrics.big_up_count)}。`,
                `${scoreRef("广度分", sector.scores && sector.scores.breadth_score, "广度分：上涨占比、大涨家数和涨停家数共同衡量板块扩散。")}`,
                "涨停和大涨个股用于判断是否从核心扩散到前排/后排。",
              ])
            )}
          </article>
          <article class="sector-detail-card">
            ${metricBlock(
              "龙头候选",
              "龙头看先动、最强、带动、抗跌和修复快；分钟启动时间和封板质量当前是日线代理，后续接 minute 与封板数据后升级。",
              `${escapeHtml(leader.name || "-")} ${leader.ticker ? `(${escapeHtml(leader.ticker)})` : ""} · ${escapeHtml(leader.role_label || "-")} · ${escapeHtml(leader.leader_score || "-")}`,
              leaderExplanation(leader, sector)
            )}
            ${metricBlock(
              "中军候选",
              "中军看容量、成交稳定、趋势稳定和承接；不是大票就叫中军，必须同时有体量、成交和趋势稳定。",
              `${escapeHtml(zhongjun.name || "-")} ${zhongjun.ticker ? `(${escapeHtml(zhongjun.ticker)})` : ""} · ${escapeHtml(zhongjun.role_label || "-")} · ${escapeHtml(zhongjun.zhongjun_score || "-")}`,
              zhongjunExplanation(zhongjun)
            )}
          </article>
          <article class="sector-detail-card full">
            ${metricBlock(
              "分歧 / 修复",
              "分歧是涨幅、成交额、广度、核心股反馈之间出现不一致；修复必须有前置分歧，并且板块强于大盘、广度改善、核心先修复。",
              `${escapeHtml(divergence.label || "-")} / ${escapeHtml(repair.label || "-")}`,
              divergenceExplanation(sector, divergence, repair)
            )}
          </article>
        </div>
      `;
    }
    function renderTodayStrongSectors(data) {
      const sectors = data.top_sectors || [];
      const summary = data.summary || {};
      if (!sectors.length) {
        return `
          <section id="sector-today" class="sector-feature">
            <div class="feature-kicker">功能区一 · Today</div>
            <h3>今日强力板块</h3>
            <p class="section-conclusion">暂无可展示板块。</p>
          </section>
        `;
      }
      const selected = sectors[Math.min(Math.max(selectedSectorIndex, 0), sectors.length - 1)];
      return `
        <section id="sector-today" class="sector-feature">
          <div class="feature-kicker">功能区一 · Today</div>
          <div class="feature-header">
            <div>
              <h3>今日强力板块</h3>
              <p>点击下方板块切换详情；高亮芯片是当前查看板块，半透明芯片仍可点击切换。</p>
            </div>
          </div>
          <section class="market-hero-card">
            <div class="market-eyebrow">PART2 Sector Momentum · ${escapeHtml(data.benchmark_name || data.benchmark_ticker)}</div>
            <h2 class="market-headline">${escapeHtml(summary.headline || "板块动向等待确认")}</h2>
            <p class="market-subtitle">${escapeHtml(summary.conclusion || data.sector_source_note || "")}</p>
            <div class="market-meta">
              <span class="meta-chip">${escapeHtml(data.sector_source)}</span>
              <span class="meta-chip">确认/修复 ${escapeHtml(summary.confirmed_or_repair_count || 0)}</span>
              <span class="meta-chip">分歧 ${escapeHtml(summary.divergence_count || 0)}</span>
            </div>
          </section>
          ${renderSectorSwitcher(sectors)}
          ${renderSelectedSectorDetail(selected)}
        </section>
      `;
    }
    function renderTrendLineChart(points) {
      const rows = points || [];
      if (!rows.length) return '<div class="trend-line-chart"><span class="hint">暂无趋势点</span></div>';
      const values = rows.map((point) => Number(point.relative_return_1d || 0));
      let minValue = Math.min(0, ...values);
      let maxValue = Math.max(0, ...values);
      if (maxValue - minValue < 0.004) {
        maxValue += 0.002;
        minValue -= 0.002;
      }
      const range = maxValue - minValue;
      const positions = rows.map((point, index) => {
        const x = rows.length === 1 ? 50 : 8 + (index * 84) / (rows.length - 1);
        const value = Number(point.relative_return_1d || 0);
        const y = 17 + ((maxValue - value) / range) * 55;
        return { point, value, x, y, index };
      });
      const zeroY = 17 + ((maxValue - 0) / range) * 55;
      const pathPoints = positions.map((item) => `${item.x},${item.y}`).join(" ");
      return `
        <div class="trend-line-chart">
          <svg class="trend-line-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            <line class="trend-zero-line" x1="4" y1="${zeroY}" x2="96" y2="${zeroY}"></line>
            <polyline class="trend-line-path" points="${pathPoints}"></polyline>
          </svg>
          ${positions.map(({ point, value, x, y, index }) => {
            const signals = [
              point.strong ? "强势" : "",
              point.repair ? "修复" : "",
              point.resonance ? "共振" : "",
            ].filter(Boolean);
            const alignClass = index === 0 ? "align-left" : index === rows.length - 1 ? "align-right" : "";
            const aria = `${point.date}，板块 ${formatPercent(point.return_1d)}，相对大盘 ${formatPercent(value)}`;
            return `
              <button type="button" class="trend-point-hit ${value < 0 ? "is-negative" : ""} ${alignClass}" style="left:${x}%;top:${y}%" aria-label="${escapeHtml(aria)}">
                <span class="trend-point-label">${escapeHtml(formatPercent(value))}</span>
                <span class="trend-point-tooltip">
                  <strong>${escapeHtml(point.date || "-")} · 数据明细</strong>
                  <span class="trend-tooltip-grid">
                    <span>板块涨跌</span><span>${escapeHtml(formatPercent(point.return_1d))}</span>
                    <span>对比指数</span><span>${escapeHtml(formatPercent(point.benchmark_return_1d))}</span>
                    <span>相对强弱</span><span>${escapeHtml(formatPercent(value))}</span>
                    <span>上涨占比</span><span>${escapeHtml(formatPercent(point.up_ratio))}</span>
                    <span>量能 / 5日均</span><span>${escapeHtml(formatRatio(point.amount_ratio_5))}</span>
                    <span>主力净流入</span><span>${escapeHtml(formatMoneyWan(point.main_net_inflow))}</span>
                  </span>
                  ${signals.length ? `<span class="trend-signal-row">${signals.map((signal) => `<span class="trend-signal">${escapeHtml(signal)}</span>`).join("")}</span>` : ""}
                </span>
              </button>
              <span class="trend-date-label" style="left:${x}%">${escapeHtml(String(point.date || "").slice(5))}</span>
            `;
          }).join("")}
        </div>
      `;
    }
    function renderTrendSectors(data) {
      const trends = data.trend_sectors || [];
      return `
        <section id="sector-trends" class="sector-feature">
          <div class="feature-kicker">功能区二 · 5-Day Trend</div>
          <div class="feature-header">
            <div>
              <h3>趋势板块</h3>
              <p>用过去 5 日的多日强力、分歧后修复、与大盘共振和资金净流入次数综合排序。</p>
            </div>
          </div>
          ${trends.length ? `
            <div class="trend-board">
              ${trends.slice(0, 6).map((item, index) => `
                <article class="trend-card">
                  <h4>#${index + 1} ${escapeHtml(item.sector_name)} · ${escapeHtml(item.trend_label)}</h4>
                  <p class="rankline">
                    趋势分 ${escapeHtml(item.trend_score)}，5日收益 ${escapeHtml(formatPercent(item.metrics && item.metrics.return_5d))}，相对大盘 ${escapeHtml(formatPercent(item.metrics && item.metrics.relative_return_5d))}
                  </p>
                  ${renderTrendLineChart(item.trend_points)}
                  <div class="metric-explain">${reasonList((item.evidence || []).map(escapeHtml))}</div>
                </article>
              `).join("")}
            </div>
          ` : `<p class="section-conclusion">暂无趋势榜数据，先查看今日强力板块。</p>`}
        </section>
      `;
    }
    function renderKnowledgeSection(data) {
      return `
        <section id="sector-knowledge" class="sector-feature">
          <div class="feature-kicker">功能区四 · Definitions</div>
          <div class="feature-header">
            <div>
              <h3>指标口径与边界</h3>
              <p>把“怎么判断”和“哪些是真数据/代理数据”放在一起，所有小叹号都能跳回这里校验口径。</p>
            </div>
          </div>
          <div class="knowledge-grid">
            <article class="knowledge-panel">
              <h4>指标边界</h4>
              ${renderDataQuality(data.data_quality)}
            </article>
            <article class="knowledge-panel">
              <h4>指标口径</h4>
              ${renderIndicatorDefinitions(data.indicator_definitions)}
            </article>
          </div>
        </section>
      `;
    }
    function renderSectorTrends(data) {
      const rawPayload = {
        summary: data.summary,
        top_sectors: data.top_sectors,
        trend_sectors: data.trend_sectors,
        daily_leaders: data.daily_leaders,
        leader_summary: data.leader_summary,
        state_machine: data.state_machine,
        indicator_definitions: data.indicator_definitions,
        data_quality: data.data_quality
      };
      return `
        <section class="sector-story-layout">
          <aside class="sector-storyline" aria-label="板块动向故事线">
            <div class="story-title">Storyline</div>
            <button class="story-link active" data-target="sector-today" onclick="scrollSectorFeature('sector-today')"><span>01</span>今日强力</button>
            <button class="story-link" data-target="sector-trends" onclick="scrollSectorFeature('sector-trends')"><span>02</span>趋势板块</button>
            <button class="story-link" data-target="dailyLeaderSection" onclick="scrollSectorFeature('dailyLeaderSection')"><span>03</span>龙头榜</button>
            <button class="story-link" data-target="sector-knowledge" onclick="scrollSectorFeature('sector-knowledge')"><span>04</span>口径边界</button>
          </aside>
          <div class="sector-feature-stack">
            ${renderTodayStrongSectors(data)}
            ${renderTrendSectors(data)}
            ${renderDailyLeaders(data)}
            ${renderKnowledgeSection(data)}
            <details class="json-viewer">
              <summary>查看标准化 JSON 原始字段</summary>
              <pre class="json-code">${syntaxHighlightJson(rawPayload)}</pre>
            </details>
          </div>
        </section>
      `;
    }

    const formatPercent = (value) => {
      if (value === null || value === undefined) return "-";
      const number = Number(value || 0);
      return `${number >= 0 ? "+" : ""}${(number * 100).toFixed(2)}%`;
    };
    const formatRatio = (value) => {
      if (value === null || value === undefined) return "-";
      return `${Number(value || 0).toFixed(2)}x`;
    };
    const formatTradeAmount = (value) => {
      if (value === null || value === undefined) return "-";
      const number = Number(value || 0);
      if (!number) return "-";
      return `${(number / 100000).toFixed(2)}亿`;
    };
    const formatMarketValue = (value) => {
      if (value === null || value === undefined) return "-";
      const number = Number(value || 0);
      if (!number) return "-";
      return `${(number / 10000).toFixed(2)}亿`;
    };
    const formatMoneyWan = (value) => {
      if (value === null || value === undefined) return "待接入";
      const number = Number(value || 0);
      if (!number) return "0.00亿";
      return `${number >= 0 ? "+" : ""}${(number / 10000).toFixed(2)}亿`;
    };

    async function fetchJson(url, options = {}) {
      const timeoutMs = options.timeoutMs || 0;
      const requestOptions = { ...options };
      delete requestOptions.timeoutMs;
      let timeoutId;
      if (timeoutMs > 0) {
        const controller = new AbortController();
        requestOptions.signal = controller.signal;
        timeoutId = setTimeout(() => controller.abort(), timeoutMs);
      }
      const response = await fetch(url, requestOptions);
      if (timeoutId) clearTimeout(timeoutId);
      const text = await response.text();
      let payload;
      try { payload = JSON.parse(text); } catch { payload = { raw: text }; }
      if (!response.ok) throw payload;
      return payload;
    }

    async function loadStatus() {
      try {
        const data = await fetchJson("/api/v1/push-channel/status");
        runtimeStatus = data;
        document.getElementById("status").innerHTML = [
          statusCell("Mongo", data.mongo_configured, "日报和分析默认读取 Mongo"),
          statusCell("飞书", data.feishu_configured, data.feishu_webhook || "当前真实推送优先通道"),
          statusCell("Telegram Bot", data.telegram_bot_configured, "Bot token 状态"),
          statusCell("Telegram 日报", data.telegram_daily_push_enabled, "需要显式 push chat ids")
        ].join("");
        updateAnalyzeHint();
      } catch (error) {
        document.getElementById("status").innerHTML = `<pre>${formatError(error)}</pre>`;
      }
    }

    async function loadDataStatus() {
      const date = encodeURIComponent(document.getElementById("analyzeDate").value);
      const ticker = encodeURIComponent(document.getElementById("analyzeTicker").value.trim());
      try {
        const data = await fetchJson(`/api/v1/data/status?date=${date}&ticker=${ticker}`);
        runtimeStatus.data = data;
        const targetState = data.has_target_data ? "已更新" : "未更新";
        const targetClass = data.has_target_data ? "ok" : "bad";
        document.getElementById("dataStatus").innerHTML =
          `<strong class="${targetClass}">${targetState}</strong> | ${data.message}`;
      } catch (error) {
        document.getElementById("dataStatus").textContent = formatError(error);
      }
    }

    async function loadTushareHealth() {
      try {
        const data = await fetchJson("/api/v1/tushare/health", { timeoutMs: 90000 });
        const ok = data.overall_status === "available";
        const partial = data.overall_status === "partial";
        const cls = ok ? "ok" : (partial ? "" : "bad");
        document.getElementById("tushareHealthSummary").innerHTML =
          `<strong class="${cls}">${data.overall_label}</strong> | ${data.message}`;
        const lines = [
          `Tushare 网关：${data.overall_label}`,
          `说明：${data.message}`,
          `建议：${data.recommendation || "-"}`,
          "",
          pretty({
            base_url: data.base_url,
            probe_dates: data.probe_dates,
            checks: data.checks
          })
        ].join("\\n");
        show("tushareHealthResult", lines);
      } catch (error) {
        document.getElementById("tushareHealthSummary").innerHTML = `<strong class="bad">检查失败</strong> | ${formatError(error)}`;
        show("tushareHealthResult", formatError(error));
      }
    }

    async function syncLatestData() {
      show("syncResult", "正在同步最新交易日... 如果 Mongo 已有目标日期数据，会直接跳过。");
      try {
        const data = await fetchJson("/api/v1/data/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          timeoutMs: 60000,
          body: JSON.stringify({
            target_date: document.getElementById("analyzeDate").value,
            mode: "latest",
            force: false,
            benchmark_ticker: selectedBenchmarkTicker()
          })
        });
        show("syncResult", data);
        await loadDataStatus();
      } catch (error) {
        show("syncResult", formatError(error));
      }
    }

    async function syncIncrementalData() {
      show("syncResult", "正在按本地水位线增量同步... 只会请求 Mongo 缺失的交易日。");
      try {
        const data = await fetchJson("/api/v1/data/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          timeoutMs: 120000,
          body: JSON.stringify({
            target_date: document.getElementById("analyzeDate").value,
            mode: "incremental",
            force: false,
            benchmark_ticker: selectedBenchmarkTicker(),
            incremental_lookback_days: 14,
            incremental_overlap_days: 3
          })
        });
        show("syncResult", data);
        await loadDataStatus();
      } catch (error) {
        show("syncResult", formatError(error));
      }
    }

    async function syncHistoryYear() {
      const confirmed = window.confirm("将从 Tushare 同步最近一年历史数据到 Mongo，可能耗时较久。是否继续？");
      if (!confirmed) return;
      show("syncResult", "正在导入最近一年历史数据... 这一步可能需要几分钟。");
      try {
        const data = await fetchJson("/api/v1/data/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          timeoutMs: 300000,
          body: JSON.stringify({
            target_date: document.getElementById("analyzeDate").value,
            mode: "history_year",
            force: false,
            benchmark_ticker: selectedBenchmarkTicker()
          })
        });
        show("syncResult", data);
        await loadDataStatus();
      } catch (error) {
        show("syncResult", formatError(error));
      }
    }

    function useLatestHistoryDate() {
      const latest = runtimeStatus.data && runtimeStatus.data.latest_market_date;
      if (!latest) {
        show("syncResult", "Mongo 暂无可用历史日期，请先导入最近一年历史数据。");
        return;
      }
      document.getElementById("analyzeDate").value = latest;
      document.getElementById("pushDate").value = latest;
      show("syncResult", `已切换到最近历史日期：${latest}`);
      loadDataStatus();
    }

    function updateAnalyzeHint() {
      const fallback = document.getElementById("allowFallback").checked;
      const hint = document.getElementById("analyzeHint");
      if (!runtimeStatus.mongo_configured && !fallback) {
        hint.textContent = "当前 Mongo 未配置。关闭 fallback 时，单票分析会直接提示缺少 Mongo 数据。";
      } else if (!runtimeStatus.mongo_configured && fallback) {
        hint.textContent = "当前 Mongo 未配置，已勾选 fallback：会调用 Tushare，可能较慢或因网络失败。";
      } else if (fallback) {
        hint.textContent = "已勾选 fallback：Mongo 查不到数据时会临时调用 Tushare，可能等待较久。";
      } else {
        hint.textContent = "Mongo-first 模式：只读 Mongo，不会调用 Tushare。";
      }
    }

    async function runAnalyze() {
      const ticker = encodeURIComponent(document.getElementById("analyzeTicker").value.trim());
      const date = encodeURIComponent(document.getElementById("analyzeDate").value);
      const fallback = document.getElementById("allowFallback").checked ? "true" : "false";
      show("analyzeResult", fallback === "true" ? "分析中... 已允许 Tushare fallback，若 Mongo 无数据可能等待较久。" : "分析中... Mongo-first 模式，不会调用 Tushare。");
      try {
        show("analyzeResult", await fetchJson(`/api/v1/analyze?ticker=${ticker}&date=${date}&allow_tushare_fallback=${fallback}`, { timeoutMs: 25000 }));
      } catch (error) {
        show("analyzeResult", formatError(error));
      }
    }

    async function runMarketPermission() {
      const date = encodeURIComponent(selectedMarketDate());
      const benchmark = encodeURIComponent(selectedBenchmarkTicker());
      show("marketPermissionConfirmResult", "正在确认权限...");
      showHtml("marketPermissionResult", '<div class="interpretation-card"><h3>正在查询</h3><p class="section-conclusion">正在按当前日期和指数口径计算 PART1 大盘权限...</p></div>');
      try {
        const data = await fetchJson(`/api/v1/market/permission?date=${date}&benchmark_ticker=${benchmark}`, { timeoutMs: 25000 });
        syncBenchmarkSelects(data.benchmark_ticker);
        syncMarketDates(data.analysis_date);
        renderPermissionSummary(data);
        renderMarketPermissionDetail(data);
      } catch (error) {
        document.getElementById("marketPermissionSummary").innerHTML = "";
        show("marketPermissionConfirmResult", formatError(error));
        showHtml("marketPermissionResult", `<div class="interpretation-card"><h3>查询失败</h3><p class="section-conclusion">${escapeHtml(formatError(error))}</p></div>`);
      }
    }

    function queryLeaderStreak() {
      const ticker = document.getElementById("leaderTickerQuery").value.trim().toUpperCase();
      leaderTickerDraft = ticker;
      const output = document.getElementById("leaderStreakResult");
      if (!ticker) {
        output.innerHTML = "请输入股票代码，例如 002484.SZ。";
        return;
      }
      if (!latestSectorData || !(latestSectorData.daily_leaders || []).length) {
        output.innerHTML = "请先点击“查询板块动向”，生成每日龙头榜后再查询。";
        return;
      }
      const days = [...(latestSectorData.daily_leaders || [])].sort((a, b) => String(b.date).localeCompare(String(a.date)));
      const records = [];
      for (const day of days) {
        const matched = (day.leaders || []).find((leader) => String(leader.ticker || "").toUpperCase() === ticker);
        if (matched) records.push({ date: day.date, ...matched });
      }
      let consecutive = 0;
      for (const day of days) {
        const matched = (day.leaders || []).some((leader) => String(leader.ticker || "").toUpperCase() === ticker);
        if (!matched) break;
        consecutive += 1;
      }
      if (!records.length) {
        output.innerHTML = `<div class="leader-day"><strong>${escapeHtml(ticker)}</strong><p class="section-conclusion">近30个交易日未进入每日龙头榜前 5。</p></div>`;
        return;
      }
      const rankOneCount = records.filter((record) => Number(record.rank) === 1).length;
      const averageRank = records.reduce((sum, record) => sum + Number(record.rank || 0), 0) / records.length;
      const summaryRecord = ((latestSectorData.leader_summary || {}).ranking || []).find((item) => String(item.ticker || "").toUpperCase() === ticker);
      output.innerHTML = `
        <div class="leader-summary-card is-champion">
          <div class="leader-card-top">
            <div>
              <div class="leader-card-rank">单票 30 日记录</div>
              <div class="leader-card-name">${escapeHtml(records[0].name || ticker)}</div>
              <div class="leader-card-code">${escapeHtml(ticker)} · ${escapeHtml(summaryRecord && summaryRecord.primary_sector || records[0].sector_name || "-")}</div>
            </div>
            <div class="leader-summary-score">${escapeHtml(summaryRecord && summaryRecord.summary_score || records.length)}</div>
          </div>
          <div class="leader-stat-row">
            <span class="leader-stat">上榜 ${escapeHtml(records.length)} 次</span>
            <span class="leader-stat">第一名 ${escapeHtml(rankOneCount)} 次</span>
            <span class="leader-stat">平均名次 ${escapeHtml(averageRank.toFixed(2))}</span>
            <span class="leader-stat">连续上榜 ${escapeHtml(consecutive)} 天</span>
          </div>
          <ul class="leader-list">
            ${records.map((record) => `
              <li>
                <span class="leader-rank">${escapeHtml(record.date)} #${escapeHtml(record.rank)}</span>
                <strong>${escapeHtml(record.name || "-")}</strong>
                <span> · ${escapeHtml(record.sector_name || "-")} · ${escapeHtml(record.role_label || "-")} · 分 ${escapeHtml(record.leader_score || "-")} · 5日收益 ${escapeHtml(formatPercent(record.return_5d))}</span>
              </li>
            `).join("")}
          </ul>
        </div>
      `;
    }

    async function runSectorTrends() {
      const dateValue = document.getElementById("sectorDate").value;
      const benchmarkValue = document.getElementById("sectorBenchmarkTicker").value;
      const date = encodeURIComponent(dateValue);
      const benchmark = encodeURIComponent(benchmarkValue);
      const requestVersion = ++sectorRequestVersion;
      startSectorProgress(requestVersion);
      showHtml("sectorTrendResult", '<div class="interpretation-card"><h3>正在查询</h3><p class="section-conclusion">正在计算 PART2 板块动向、龙头/中军候选和分歧/修复状态...</p></div>');
      try {
        const data = await fetchJson(`/api/v1/market/sectors?date=${date}&benchmark_ticker=${benchmark}`, { timeoutMs: 45000 });
        if (requestVersion !== sectorRequestVersion) return;
        selectedSectorIndex = 0;
        selectedLeaderView = "total";
        selectedLeaderDate = "";
        leaderTickerDraft = "";
        latestSectorData = { ...data, daily_leaders_loading: true };
        syncBenchmarkSelects(data.benchmark_ticker);
        syncMarketDates(data.analysis_date);
        showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
        document.getElementById("leaderStreakResult").textContent = "板块结果已完成；近30个交易日龙头榜正在后台计算。";
        advanceSectorProgressToLeaders(requestVersion);
        void loadDailyLeaders(dateValue, benchmarkValue, requestVersion);
      } catch (error) {
        if (requestVersion !== sectorRequestVersion) return;
        failSectorProgress(requestVersion, false);
        showHtml("sectorTrendResult", `<div class="interpretation-card"><h3>查询失败</h3><p class="section-conclusion">${escapeHtml(formatError(error, "sectors"))}</p></div>`);
      }
    }

    async function loadDailyLeaders(dateValue, benchmarkValue, requestVersion = sectorRequestVersion) {
      try {
        const date = encodeURIComponent(dateValue);
        const benchmark = encodeURIComponent(benchmarkValue);
        const data = await fetchJson(`/api/v1/market/leaders?date=${date}&benchmark_ticker=${benchmark}`, { timeoutMs: 90000 });
        if (requestVersion !== sectorRequestVersion) return;
        latestSectorData = {
          ...latestSectorData,
          daily_leaders: data.daily_leaders || [],
          leader_summary: data.leader_summary || {},
          daily_leaders_loading: false,
          daily_leaders_error: "",
        };
        showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
        const ticker = document.getElementById("leaderTickerQuery").value.trim();
        if (ticker) {
          queryLeaderStreak();
        } else {
          document.getElementById("leaderStreakResult").textContent = "近30个交易日龙头榜已更新，可输入股票代码查询连续记录。";
        }
        completeSectorProgress(requestVersion);
      } catch (error) {
        if (requestVersion !== sectorRequestVersion) return;
        failSectorProgress(requestVersion, true);
        latestSectorData = {
          ...latestSectorData,
          daily_leaders_loading: false,
          daily_leaders_error: formatError(error, "leaders"),
        };
        if (latestSectorData) showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
        document.getElementById("leaderStreakResult").textContent = "板块主结果已完成；近30个交易日龙头榜暂未返回。";
      }
    }

    async function runTelegramText() {
      const ticker = encodeURIComponent(document.getElementById("analyzeTicker").value.trim());
      const date = encodeURIComponent(document.getElementById("analyzeDate").value);
      show("analyzeResult", "生成中...");
      try {
        const data = await fetchJson(`/api/v1/telegram/analyze?ticker=${ticker}&date=${date}`, { timeoutMs: 25000 });
        show("analyzeResult", data.text || data);
      } catch (error) {
        show("analyzeResult", formatError(error));
      }
    }

    async function runDailyPush() {
      show("pushResult", "执行中...");
      try {
        const data = await fetchJson("/api/v1/daily-push", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            target_date: document.getElementById("pushDate").value,
            dry_run: document.getElementById("dryRun").checked,
            message_top_k: Number(document.getElementById("messageTopK").value || 20),
            include_candidate_pool: document.getElementById("includePool").checked,
            allow_snapshot_fallback: document.getElementById("allowSnapshotFallback").checked
          })
        });
        show("pushResult", `channels=${(data.pushed_channels || []).join(",") || "-"}\\ndata_source=${data.data_source}\\n\\n${data.message_text}`);
        loadStatus();
      } catch (error) {
        show("pushResult", formatError(error));
      }
    }

    document.addEventListener("DOMContentLoaded", () => {
      initDateWheels();
      document.getElementById("allowFallback").addEventListener("change", updateAnalyzeHint);
      document.getElementById("analyzeDate").addEventListener("change", () => {
        syncMarketDates(document.getElementById("analyzeDate").value);
        loadDataStatus();
      });
      document.getElementById("analyzeTicker").addEventListener("change", loadDataStatus);
      document.getElementById("marketDate").addEventListener("change", () => {
        syncMarketDates(document.getElementById("marketDate").value);
        loadDataStatus();
      });
      document.getElementById("marketQueryDate").addEventListener("change", () => {
        syncMarketDates(document.getElementById("marketQueryDate").value);
        loadDataStatus();
      });
      document.getElementById("sectorDate").addEventListener("change", () => {
        syncMarketDates(document.getElementById("sectorDate").value);
        loadDataStatus();
      });
      document.getElementById("benchmarkTicker").addEventListener("change", () => {
        syncBenchmarkSelects(document.getElementById("benchmarkTicker").value);
      });
      document.getElementById("marketBenchmarkTicker").addEventListener("change", () => {
        syncBenchmarkSelects(document.getElementById("marketBenchmarkTicker").value);
      });
      document.getElementById("sectorBenchmarkTicker").addEventListener("change", () => {
        syncBenchmarkSelects(document.getElementById("sectorBenchmarkTicker").value);
      });
    });
    loadStatus();
    loadDataStatus();
    loadTushareHealth();
  </script>
</body>
</html>
"""

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
