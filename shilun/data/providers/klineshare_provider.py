"""KlineShare 实时行情与 K 线客户端。

内部 ticker 继续使用 Tushare 风格（.SH/.SZ），仅在 HTTP 边界把上海市场
转换为 KlineShare 的 .SS。数据新鲜度只使用服务端 timestamp，不使用抓取时间。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


class KlineShareError(RuntimeError):
    def __init__(
        self,
        source: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        self.source = source
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(f"{source} failed: {message}")


@dataclass(frozen=True)
class KlineShareConfig:
    api_key: str | None = None
    base_url: str = "https://data.klineshare.cn"
    timeout: int = 15

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("KlineShare base_url 必须是 HTTP(S) 地址")
        if self.timeout <= 0:
            raise ValueError("KlineShare timeout 必须大于 0")


def to_klineshare_symbol(ticker: str) -> str:
    value = str(ticker).strip().upper()
    if value.endswith(".SH"):
        return f"{value[:-3]}.SS"
    return value


def from_klineshare_symbol(symbol: str) -> str:
    value = str(symbol).strip().upper()
    if value.endswith(".SS"):
        return f"{value[:-3]}.SH"
    return value


def _timestamp_ms_to_market_iso(value: Any) -> str | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    # 文档规定 timestamp 为毫秒；容忍测试/异常源返回秒级时间戳。
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return datetime.fromtimestamp(timestamp, tz=UTC).astimezone(MARKET_TIMEZONE).isoformat(timespec="seconds")


def _yuan_to_thousand(value: Any) -> float | None:
    """把 KlineShare 的人民币元成交额归一为项目统一的千元口径。"""

    try:
        amount_yuan = float(value)
    except (TypeError, ValueError):
        return None
    return amount_yuan / 1000.0


class KlineShareClient:
    def __init__(
        self,
        config: KlineShareConfig,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.trust_env = False

    def _request(
        self,
        path: str,
        *,
        params: list[tuple[str, Any]] | dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        if authenticated and not self.config.api_key:
            raise KlineShareError(path, "SHILUN_KLINESHARE_API_KEY 未配置")
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["X-API-Key"] = str(self.config.api_key)
        url = f"{self.config.base_url.rstrip('/')}{path}"
        try:
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=self.config.timeout,
            )
        except requests.RequestException as error:
            raise KlineShareError(path, str(error)) from error
        try:
            payload = response.json()
        except ValueError as error:
            raise KlineShareError(
                path,
                "响应不是合法 JSON",
                status_code=response.status_code,
            ) from error
        if response.status_code >= 400 or not payload.get("success", False):
            retry_header = response.headers.get("Retry-After")
            retry_after = payload.get("retry_after")
            try:
                retry_after = int(retry_after or retry_header) if (retry_after or retry_header) else None
            except (TypeError, ValueError):
                retry_after = None
            raise KlineShareError(
                path,
                str(payload.get("message") or f"HTTP {response.status_code}"),
                status_code=response.status_code,
                retry_after=retry_after,
            )
        return payload

    def health(self) -> dict[str, Any]:
        return self._request("/v1/health", authenticated=False)

    def account(self) -> dict[str, Any]:
        """回读当前 Key 的套餐、scope 和实时/K 线限制。"""

        return self._request("/v1/me")

    def fetch_calendar(
        self,
        *,
        date: str | None = None,
        start: str | None = None,
        end: str | None = None,
        window: int | None = None,
    ) -> dict[str, Any]:
        """读取交易日历原始响应；同步层负责统一落成 Mongo trade_calendar 契约。"""

        if (start is None) != (end is None):
            raise ValueError("KlineShare calendar 的 start/end 必须同时提供")
        params: dict[str, Any] = {}
        if date:
            params["date"] = str(date).replace("-", "")
        if start and end:
            params["start"] = str(start).replace("-", "")
            params["end"] = str(end).replace("-", "")
        if window is not None:
            params["window"] = max(1, min(int(window), 90))
        return self._request("/v1/calendar", params=params)

    def fetch_realtime(
        self,
        tickers: list[str],
        *,
        include_valuation: bool = False,
        include_depth: bool = False,
    ) -> pd.DataFrame:
        if not tickers:
            return pd.DataFrame()
        params = [("symbol", to_klineshare_symbol(ticker)) for ticker in tickers]
        if include_valuation:
            params.append(("include_valuation", "true"))
        if include_depth:
            params.append(("include_depth", "true"))
        payload = self._request("/v1/realtime", params=params)
        raw_data = payload.get("data")
        items = raw_data if isinstance(raw_data, list) else [raw_data] if isinstance(raw_data, dict) else []
        fetched_at = datetime.now(tz=MARKET_TIMEZONE).isoformat(timespec="seconds")
        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
            depth = item.get("depth") if isinstance(item.get("depth"), dict) else {}
            bids = depth.get("bids") if isinstance(depth.get("bids"), list) else []
            asks = depth.get("asks") if isinstance(depth.get("asks"), list) else []
            inner_volume = depth.get("inner_volume")
            outer_volume = depth.get("outer_volume")
            quote_at = _timestamp_ms_to_market_iso(item.get("timestamp"))
            rows.append({
                "ticker": from_klineshare_symbol(item.get("symbol") or ""),
                "name": item.get("name"),
                "current": item.get("price", item.get("close")),
                "pct_chg": item.get("change_percent"),
                "change": item.get("change"),
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "prev_close": item.get("pre_close", quote.get("pre_close")),
                "volume": item.get("volume"),
                # KlineShare turnover 是人民币元；石论日线/实时统一沿用
                # Tushare amount 的千元口径，避免页面把个股成交额放大 1000 倍。
                "amount": _yuan_to_thousand(item.get("turnover")),
                "amount_unit": "thousand_cny",
                "volume_ratio": item.get("volume_ratio", quote.get("volume_ratio")),
                "trade_status": item.get("trade_status"),
                "depth_bids": bids[:5],
                "depth_asks": asks[:5],
                "inner_volume": inner_volume,
                "outer_volume": outer_volume,
                "quote_at": quote_at,
                "update_at": quote_at,
                "fetched_at": fetched_at,
                "timestamp_kind": "source_quote_at",
                "source": "klineshare",
            })
        frame = pd.DataFrame(rows)
        numeric = (
            "current", "pct_chg", "change", "open", "high", "low",
            "prev_close", "volume", "amount", "volume_ratio",
            "inner_volume", "outer_volume",
        )
        for column in numeric:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame

    def fetch_trend(
        self,
        ticker: str,
        *,
        date: str | None = None,
    ) -> pd.DataFrame:
        """读取单只证券分时，并保留源端 ``avg_price`` 的每股人民币口径。

        KlineShare 的 trend 接口一次只能查询一个 symbol。成交额继续转换为项目
        统一的千元口径，但价格和均价不做任何倍率换算，避免把均价错误地当成
        成交额或分值使用。
        """

        params: dict[str, Any] = {"symbol": to_klineshare_symbol(ticker)}
        if date:
            normalized_date = str(date).replace("-", "")
            if len(normalized_date) != 8 or not normalized_date.isdigit():
                raise ValueError("KlineShare trend date 必须使用 YYYYMMDD 或 YYYY-MM-DD")
            params["date"] = normalized_date
        payload = self._request("/v1/trend", params=params)
        raw_data = payload.get("data")
        container = raw_data if isinstance(raw_data, dict) else {}
        items = (
            raw_data
            if isinstance(raw_data, list)
            else container.get("data") if isinstance(container.get("data"), list)
            else []
        )
        response_symbol = (
            (payload.get("meta") or {}).get("symbol")
            if isinstance(payload.get("meta"), dict)
            else None
        ) or container.get("symbol") or params["symbol"]
        pre_close = container.get("pre_close")
        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            quote_at = _timestamp_ms_to_market_iso(item.get("timestamp"))
            rows.append({
                "ticker": from_klineshare_symbol(
                    str(response_symbol)
                ),
                "datetime": pd.Timestamp(quote_at) if quote_at else pd.NaT,
                "price": item.get("price"),
                "avg_price": item.get("avg_price"),
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "change": item.get("change"),
                "pct_chg": item.get("change_percent"),
                "prev_close": pre_close,
                "volume": item.get("volume"),
                "amount": _yuan_to_thousand(item.get("turnover")),
                "price_unit": "cny_per_share",
                "amount_unit": "thousand_cny",
                "source": "klineshare",
            })
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        for column in (
            "price", "avg_price", "open", "high", "low", "change",
            "pct_chg", "prev_close", "volume", "amount",
        ):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return (
            frame.dropna(subset=["datetime", "price", "avg_price"])
            .sort_values("datetime")
            .reset_index(drop=True)
        )

    def fetch_kline(
        self,
        ticker: str,
        *,
        period: int = 300,
        count: int = 500,
        adjust_type: str = "none",
    ) -> pd.DataFrame:
        payload = self._request(
            "/v1/kline",
            params={
                "symbol": to_klineshare_symbol(ticker),
                "period": int(period),
                "count": int(count),
                "adjust_type": adjust_type,
            },
        )
        rows: list[dict[str, Any]] = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            quote_at = _timestamp_ms_to_market_iso(item.get("timestamp"))
            rows.append({
                "datetime": pd.Timestamp(quote_at) if quote_at else pd.NaT,
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close"),
                "volume": item.get("volume"),
                "amount": _yuan_to_thousand(item.get("turnover")),
                "amount_unit": "thousand_cny",
            })
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        for column in ("open", "high", "low", "close", "volume", "amount"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame.dropna(subset=["datetime", "close"]).sort_values("datetime").reset_index(drop=True)
