"""每日涨跌停价与实时/闭合 5 分钟 K 的粗粒度触板事实。"""

from __future__ import annotations

from datetime import datetime
from typing import Any


FACT_COLLECTION = "execution_price_limit_facts"


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _up_state(
    limit_price: float,
    bars: list[dict[str, Any]],
    quote: dict[str, Any],
    tolerance: float,
) -> dict[str, bool]:
    touched = opened = resealed = sealed = False
    for bar in bars:
        high = _number(bar.get("high"))
        close = _number(bar.get("close"))
        if high is not None and high >= limit_price - tolerance:
            touched = True
        if touched and close is not None and close < limit_price - tolerance:
            opened = True
            sealed = False
        elif close is not None and close >= limit_price - tolerance:
            if opened:
                resealed = True
            touched = True
            sealed = True
    current_high = _number(quote.get("high"))
    current = _number(quote.get("current"))
    if current_high is not None and current_high >= limit_price - tolerance:
        touched = True
    if current is not None:
        if touched and current < limit_price - tolerance:
            opened = True
            sealed = False
        elif current >= limit_price - tolerance:
            if opened:
                resealed = True
            touched = True
            sealed = True
    return {"touched": touched, "sealed": sealed, "opened": opened, "resealed": resealed}


def _down_state(
    limit_price: float,
    bars: list[dict[str, Any]],
    quote: dict[str, Any],
    tolerance: float,
) -> dict[str, bool]:
    touched = opened = resealed = sealed = False
    for bar in bars:
        low = _number(bar.get("low"))
        close = _number(bar.get("close"))
        if low is not None and low <= limit_price + tolerance:
            touched = True
        if touched and close is not None and close > limit_price + tolerance:
            opened = True
            sealed = False
        elif close is not None and close <= limit_price + tolerance:
            if opened:
                resealed = True
            touched = True
            sealed = True
    current_low = _number(quote.get("low"))
    current = _number(quote.get("current"))
    if current_low is not None and current_low <= limit_price + tolerance:
        touched = True
    if current is not None:
        if touched and current > limit_price + tolerance:
            opened = True
            sealed = False
        elif current <= limit_price + tolerance:
            if opened:
                resealed = True
            touched = True
            sealed = True
    return {"touched": touched, "sealed": sealed, "opened": opened, "resealed": resealed}


def build_price_limit_facts(
    *,
    trade_date: str,
    ticker: str,
    limit_document: dict[str, Any],
    quote: dict[str, Any],
    closed_5m_bars: list[dict[str, Any]],
    computed_at: datetime,
    tick_size: float = 0.01,
) -> dict[str, Any] | None:
    up_limit = _number(limit_document.get("up_limit"))
    down_limit = _number(limit_document.get("down_limit"))
    if up_limit is None or down_limit is None or tick_size <= 0:
        return None
    tolerance = float(tick_size) / 2.0
    bars = sorted(closed_5m_bars, key=lambda item: str(item.get("datetime") or ""))
    up = _up_state(up_limit, bars, quote, tolerance)
    down = _down_state(down_limit, bars, quote, tolerance)
    return {
        "trade_date": str(trade_date).replace("-", ""),
        "ticker": ticker.upper(),
        "pre_close": _number(limit_document.get("pre_close") or quote.get("prev_close")),
        "up_limit": up_limit,
        "down_limit": down_limit,
        "price_unit": "cny_per_share",
        "tick_size": tick_size,
        "equality_tolerance": tolerance,
        "up_limit_touched": up["touched"],
        "up_limit_sealed": up["sealed"],
        "up_limit_opened": up["opened"],
        "up_limit_resealed": up["resealed"],
        "down_limit_touched": down["touched"],
        "down_limit_sealed": down["sealed"],
        "down_limit_opened": down["opened"],
        "down_limit_resealed": down["resealed"],
        "closed_5m_count": len(bars),
        "computed_at": computed_at.isoformat(timespec="seconds"),
        "source": "tushare_stk_limit+klineshare_realtime_closed_5m",
        "precision": "coarse_5m",
    }
