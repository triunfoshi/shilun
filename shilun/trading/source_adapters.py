"""把现有行情提供方输出转换为 Trading 的可验证数据时间。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from shilun.trading.contracts import FactSourceSnapshot


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _parse_timestamp(value: Any, *, assume_market_timezone: bool = False) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        if not assume_market_timezone:
            return None
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed


def quote_snapshot(source: str, quote: dict[str, Any] | None) -> FactSourceSnapshot:
    """实时报价只能信任提供方的 quote_at，不能用 fetched_at 冒充。"""

    payload = dict(quote or {})
    quote_at = _parse_timestamp(payload.get("quote_at") or payload.get("update_at"))
    return FactSourceSnapshot(source=source, as_of=quote_at, payload=payload)


def minute_bars_snapshot(
    source: str,
    bars: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> FactSourceSnapshot:
    payload_bars = list(bars or [])
    timestamps = [
        _parse_timestamp(item.get("datetime"), assume_market_timezone=True)
        for item in payload_bars
    ]
    valid = [item for item in timestamps if item is not None]
    return FactSourceSnapshot(
        source=source,
        as_of=max(valid) if valid else None,
        payload={"bars": payload_bars},
    )


def build_p0_source_snapshots(
    *,
    stock_quote: dict[str, Any] | None,
    stock_5m_bars: list[dict[str, Any]] | None,
    sse_quote: dict[str, Any] | None,
    chinext_quote: dict[str, Any] | None,
    sector_quote: dict[str, Any] | None = None,
) -> tuple[FactSourceSnapshot, ...]:
    """组装 P0 数据来源；板块没有真实 quote_at 时会自然降级为可选缺失。"""

    return (
        quote_snapshot("stock_quote", stock_quote),
        minute_bars_snapshot("stock_5m", stock_5m_bars),
        quote_snapshot("index_sse_quote", sse_quote),
        quote_snapshot("index_chinext_quote", chinext_quote),
        quote_snapshot("sector_quote", sector_quote),
    )
