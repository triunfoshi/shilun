"""策略无关的单票技术事件。

这里仅描述已经发生的行情事实，不输出买点、卖点、仓位或推荐结论。策略层可以
消费这些事件，但单票分析页默认只展示本模块的结果。
"""

from __future__ import annotations

from typing import Any

import pandas as pd


GENERIC_EVENT_META: dict[str, dict[str, str]] = {
    "limit_up": {"name": "涨停", "direction": "bullish", "period": "short", "color": "red", "category": "generic"},
    "limit_down": {"name": "跌停", "direction": "bearish", "period": "short", "color": "green", "category": "generic"},
    "limit_up_open_board": {"name": "涨停开板", "direction": "warning", "period": "short", "color": "gray", "category": "generic"},
    "gap_up": {"name": "向上跳空", "direction": "bullish", "period": "short", "color": "gray", "category": "generic"},
    "gap_down": {"name": "向下跳空", "direction": "bearish", "period": "short", "color": "gray", "category": "generic"},
    "engulf_bullish": {"name": "阳线吞噬", "direction": "bullish", "period": "short", "color": "red", "category": "generic"},
    "engulf_bearish": {"name": "阴线吞噬", "direction": "bearish", "period": "short", "color": "green", "category": "generic"},
    "long_upper_shadow": {"name": "长上影线", "direction": "warning", "period": "short", "color": "gray", "category": "generic"},
    "long_lower_shadow": {"name": "长下影线", "direction": "bullish", "period": "short", "color": "gray", "category": "generic"},
    "ma_golden_cross": {"name": "MA5/MA10 金叉", "direction": "bullish", "period": "mid", "color": "red", "category": "generic"},
    "ma_death_cross": {"name": "MA5/MA10 死叉", "direction": "bearish", "period": "mid", "color": "green", "category": "generic"},
    "volume_spike": {"name": "成交量放大", "direction": "warning", "period": "short", "color": "gray", "category": "generic"},
    "new_high_20d": {"name": "20日新高", "direction": "bullish", "period": "mid", "color": "red", "category": "generic"},
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return default if pd.isna(number) else number
    except (TypeError, ValueError):
        return default


def _limit_threshold_for(ticker: str) -> float:
    normalized = str(ticker or "").upper()
    if normalized.startswith(("688", "300", "301")):
        return 0.195
    if normalized.startswith(("4", "8", "92")):
        return 0.295
    if "ST" in normalized:
        return 0.045
    return 0.095


def _event(index: int, code: str, note: str, price: float, date: str) -> dict[str, Any]:
    meta = GENERIC_EVENT_META[code]
    return {
        "date": date,
        "index": int(index),
        "code": code,
        "name": meta["name"],
        "direction": meta["direction"],
        "period": meta["period"],
        "color": meta["color"],
        "category": "generic",
        "price": round(price, 2),
        "note": note,
        "methodology": "daily_ohlcv_rule",
    }


def detect_generic_events_daily(bars: pd.DataFrame, ticker: str = "") -> list[dict[str, Any]]:
    """识别 K 线、涨跌停、缺口、均线交叉和量能等通用事件。"""
    if bars is None or len(bars) < 2:
        return []
    frame = bars.copy().reset_index(drop=True)
    for column in ("open", "high", "low", "close"):
        if column not in frame.columns:
            return []
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "volume" not in frame.columns:
        frame["volume"] = 0.0
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
    frame["pct_chg"] = frame["close"].pct_change().fillna(0.0)
    frame["ma5"] = frame["close"].rolling(5, min_periods=1).mean()
    frame["ma10"] = frame["close"].rolling(10, min_periods=1).mean()
    frame["volume_ma20"] = frame["volume"].rolling(20, min_periods=1).mean()
    threshold = _limit_threshold_for(ticker)
    events: list[dict[str, Any]] = []

    for index in range(1, len(frame)):
        row = frame.iloc[index]
        previous = frame.iloc[index - 1]
        date = pd.Timestamp(row.get("date") or row.get("trade_date")).strftime("%Y-%m-%d")
        close = _f(row["close"])
        open_price = _f(row["open"], close)
        high = _f(row["high"], close)
        low = _f(row["low"], close)
        previous_close = _f(previous["close"], close)
        previous_open = _f(previous["open"], previous_close)
        previous_high = _f(previous["high"], previous_close)
        previous_low = _f(previous["low"], previous_close)
        pct_chg = _f(row["pct_chg"])
        body = abs(close - open_price)
        daily_range = max(high - low, 1e-9)

        touched_limit = previous_close > 0 and high / previous_close - 1.0 >= threshold
        if pct_chg >= threshold:
            events.append(_event(index, "limit_up", f"收盘涨幅 {pct_chg * 100:.2f}%", close, date))
        elif pct_chg <= -threshold:
            events.append(_event(index, "limit_down", f"收盘跌幅 {pct_chg * 100:.2f}%", close, date))
        elif touched_limit:
            events.append(_event(index, "limit_up_open_board", "盘中触及涨停阈值但收盘未封住", close, date))

        if open_price > previous_high * 1.005:
            events.append(_event(index, "gap_up", f"开盘高于前高 {(open_price / previous_high - 1) * 100:.2f}%", close, date))
        elif open_price < previous_low * 0.995:
            events.append(_event(index, "gap_down", f"开盘低于前低 {(open_price / previous_low - 1) * 100:.2f}%", close, date))

        if close > open_price and previous_close < previous_open and open_price < previous_close and close > previous_open:
            events.append(_event(index, "engulf_bullish", "今日阳线实体覆盖前一日阴线实体", close, date))
        elif close < open_price and previous_close > previous_open and open_price > previous_close and close < previous_open:
            events.append(_event(index, "engulf_bearish", "今日阴线实体覆盖前一日阳线实体", close, date))

        upper_shadow = high - max(open_price, close)
        lower_shadow = min(open_price, close) - low
        if upper_shadow > body * 2 and upper_shadow / daily_range > 0.4:
            events.append(_event(index, "long_upper_shadow", f"上影占日内振幅 {upper_shadow / daily_range * 100:.0f}%", close, date))
        if lower_shadow > body * 2 and lower_shadow / daily_range > 0.4:
            events.append(_event(index, "long_lower_shadow", f"下影占日内振幅 {lower_shadow / daily_range * 100:.0f}%", close, date))

        ma5 = _f(row["ma5"])
        ma10 = _f(row["ma10"])
        previous_ma5 = _f(previous["ma5"])
        previous_ma10 = _f(previous["ma10"])
        if previous_ma5 <= previous_ma10 and ma5 > ma10:
            events.append(_event(index, "ma_golden_cross", f"MA5 {ma5:.2f} 上穿 MA10 {ma10:.2f}", close, date))
        elif previous_ma5 >= previous_ma10 and ma5 < ma10:
            events.append(_event(index, "ma_death_cross", f"MA5 {ma5:.2f} 下穿 MA10 {ma10:.2f}", close, date))

        volume_ma20 = _f(row["volume_ma20"])
        if index >= 5 and volume_ma20 and _f(row["volume"]) / volume_ma20 >= 1.8:
            events.append(_event(index, "volume_spike", f"成交量为20日均量 {_f(row['volume']) / volume_ma20:.2f} 倍", close, date))
        if index >= 20 and close >= _f(frame.iloc[index - 20:index]["high"].max()):
            events.append(_event(index, "new_high_20d", "收盘达到过去20个交易日新高", close, date))
    return events


def summarize_generic_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    recent = events[-30:]
    return {
        "total": len(events),
        "recent_count": len(recent),
        "bullish_count": sum(item.get("direction") == "bullish" for item in recent),
        "bearish_count": sum(item.get("direction") == "bearish" for item in recent),
        "warning_count": sum(item.get("direction") == "warning" for item in recent),
        "generic_count": len(events),
        "scope": "strategy_neutral",
    }
