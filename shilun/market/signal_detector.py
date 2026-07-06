"""盘中/日线信号点识别器（复用 candidates.detect_ma5_signal）。

**核心变更 (v2)**：底层复用 `shilun.market.candidates.detect_ma5_signal`
逐日回溯识别 5 日线趋势战法信号，与 PART3 候选池计算逻辑完全一致。

信号分两类：
1. **MA5 战法核心信号**（PART3 直接采用）：
   - breakout_confirm 突破确认
   - pullback_to_ma5 回踩 MA5 买点
   - gentle_rise 缩量上涨
   - ma_alignment_up 多头排列建立
   - ma20_uptrend_confirmed 主升段确认
   - risk_flag（触发的具体风险：量价背离 / 高位过热 / 涨停炸板）

2. **通用 K 线形态**（作为补充参考，不影响 PART3 决策）：
   - 涨跌停、跳空、吞噬、影线、MA 金叉死叉
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from shilun.market.candidates import detect_ma5_signal


# 信号库配置
SIGNAL_META: dict[str, dict[str, str]] = {
    # ── MA5 战法核心 5 个 ─────────────────────────────────
    "breakout_confirm": {"name": "突破确认", "direction": "bullish", "period": "short", "color": "red", "category": "ma5_strategy"},
    "pullback_to_ma5": {"name": "回踩 MA5 买点", "direction": "bullish", "period": "short", "color": "red", "category": "ma5_strategy"},
    "gentle_rise": {"name": "缩量上涨", "direction": "bullish", "period": "short", "color": "orange", "category": "ma5_strategy"},
    "ma_alignment_up": {"name": "多头排列建立", "direction": "bullish", "period": "mid", "color": "red", "category": "ma5_strategy"},
    "ma20_uptrend_confirmed": {"name": "MA20 主升段确认", "direction": "bullish", "period": "mid", "color": "red", "category": "ma5_strategy"},

    # ── MA5 战法风险信号（3 个 risk_flag 拆开）───────────
    "risk_volume_price_diverge": {"name": "量价背离", "direction": "warning", "period": "short", "color": "orange", "category": "ma5_risk"},
    "risk_overheated": {"name": "高位过热", "direction": "warning", "period": "short", "color": "orange", "category": "ma5_risk"},
    "risk_limit_up_break": {"name": "涨停炸板", "direction": "bearish", "period": "short", "color": "green", "category": "ma5_risk"},

    # ── 通用 K 线形态（辅助参考） ───────────────────────
    "limit_up": {"name": "涨停", "direction": "bullish", "period": "short", "color": "red", "category": "generic"},
    "limit_down": {"name": "跌停", "direction": "bearish", "period": "short", "color": "green", "category": "generic"},
    "gap_up": {"name": "跳空上涨", "direction": "bullish", "period": "short", "color": "gray", "category": "generic"},
    "gap_down": {"name": "跳空下跌", "direction": "bearish", "period": "short", "color": "gray", "category": "generic"},
    "engulf_bullish": {"name": "阳线吞噬", "direction": "bullish", "period": "short", "color": "red", "category": "generic"},
    "engulf_bearish": {"name": "被覆盖线（看跌）", "direction": "bearish", "period": "short", "color": "green", "category": "generic"},
    "long_upper_shadow": {"name": "长上影线", "direction": "warning", "period": "short", "color": "gray", "category": "generic"},
    "long_lower_shadow": {"name": "长下影（探底回升）", "direction": "bullish", "period": "short", "color": "gray", "category": "generic"},
    "ma_golden_cross": {"name": "MA5/MA10 金叉", "direction": "bullish", "period": "mid", "color": "red", "category": "generic"},
    "ma_death_cross": {"name": "MA5/MA10 死叉", "direction": "bearish", "period": "mid", "color": "green", "category": "generic"},
}


def _f(v: Any, default: float = 0.0) -> float:
    try:
        n = float(v)
        if pd.isna(n): return default
        return n
    except (TypeError, ValueError):
        return default


def _limit_threshold_for(ticker: str) -> float:
    """按 ticker 前缀判定涨跌停阈值。"""
    t = str(ticker).upper()
    if t.startswith(("688", "300", "301")): return 0.195
    if t.startswith(("4", "8")): return 0.295
    if "ST" in t: return 0.045
    return 0.095


def _make_signal(idx: int, code: str, note: str, price: float, date_str: str, extra: dict | None = None) -> dict[str, Any]:
    meta = SIGNAL_META.get(code, {"name": code, "direction": "warning", "period": "short", "color": "gray", "category": "generic"})
    return {
        "date": date_str,
        "index": int(idx),
        "code": code,
        "name": meta["name"],
        "direction": meta["direction"],
        "period": meta["period"],
        "color": meta["color"],
        "category": meta["category"],
        "price": round(float(price), 2),
        "note": note,
        **(extra or {}),
    }


def detect_signals_daily(bars: pd.DataFrame, ticker: str = "") -> list[dict[str, Any]]:
    """在日线数据上识别信号点。

    v2 变更：MA5 战法信号由 `detect_ma5_signal` 逐日回溯识别，与 PART3 计算保持一致。

    Args:
        bars: DataFrame 含 date/open/high/low/close/volume/ma5/ma10/ma20（缺 MA 会就地算）
              至少 25 根（为了让 detect_ma5_signal 有 20 根窗口 + 状态转换判断）
        ticker: 用于判定涨跌停阈值

    Returns:
        list of signals（按日期升序，每个含 category=ma5_strategy/ma5_risk/generic）
    """
    if bars is None or len(bars) < 5:
        return []

    frame = bars.copy().reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        if col not in frame.columns:
            return []
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    # 若无 MA，就地算
    for w in (5, 10, 20):
        col = f"ma{w}"
        if col not in frame.columns:
            frame[col] = frame["close"].rolling(w, min_periods=1).mean()

    frame["volume_ma5"] = frame["volume"].rolling(5, min_periods=1).mean()
    frame["pct_chg"] = frame["close"].pct_change().fillna(0.0)

    limit_thresh = _limit_threshold_for(ticker)
    signals: list[dict[str, Any]] = []

    # 用于跟踪 MA 状态转换
    prev_ma_aligned = False
    prev_slope_up = False
    prev_signal_code = None

    for i in range(4, len(frame)):
        row = frame.iloc[i]
        date_str = pd.Timestamp(row["date"]).strftime("%Y-%m-%d") if "date" in row else str(i)
        close = _f(row["close"])
        open_ = _f(row["open"])
        high = _f(row["high"])
        low = _f(row["low"])

        # ── 通用信号（先识别，不影响 MA5 战法主流程） ─────
        prev = frame.iloc[i - 1]
        prev_close = _f(prev["close"])
        prev_open = _f(prev["open"])
        prev_high = _f(prev["high"])
        prev_low = _f(prev["low"])
        pct = _f(row["pct_chg"])
        vol_ma5 = _f(row["volume_ma5"]) or 1.0
        vol_ratio = _f(row["volume"]) / vol_ma5 if vol_ma5 else 0
        body = abs(close - open_)
        range_ = high - low if high > low else 1e-9

        # 涨跌停
        if pct >= limit_thresh:
            signals.append(_make_signal(i, "limit_up", f"涨停 {pct*100:.2f}%", close, date_str))
        elif pct <= -limit_thresh:
            signals.append(_make_signal(i, "limit_down", f"跌停 {pct*100:.2f}%", close, date_str))
        else:
            # 跳空（涨跌停时不叠）
            if open_ > prev_high * 1.005:
                signals.append(_make_signal(i, "gap_up", f"跳空 {(open_-prev_high)/max(prev_close,1)*100:+.1f}%", close, date_str))
            elif open_ < prev_low * 0.995:
                signals.append(_make_signal(i, "gap_down", f"跳空 {(open_-prev_low)/max(prev_close,1)*100:+.1f}%", close, date_str))

            # 吞噬
            prev_bearish = prev_close < prev_open
            prev_bullish = prev_close > prev_open
            if close > open_ and prev_bearish and open_ < prev_close and close > prev_open:
                signals.append(_make_signal(i, "engulf_bullish", f"阳吞前日阴线，收 {close:.2f}", close, date_str))
            elif close < open_ and prev_bullish and open_ > prev_close and close < prev_open:
                signals.append(_make_signal(i, "engulf_bearish", f"阴覆盖前日阳线，收 {close:.2f}", close, date_str))

            # 影线
            upper_shadow = high - max(open_, close)
            lower_shadow = min(open_, close) - low
            if upper_shadow > body * 2 and upper_shadow / range_ > 0.4:
                signals.append(_make_signal(i, "long_upper_shadow", f"上影 {upper_shadow/range_*100:.0f}%", close, date_str))
            if lower_shadow > body * 2 and lower_shadow / range_ > 0.4 and close >= open_:
                signals.append(_make_signal(i, "long_lower_shadow", f"下影 {lower_shadow/range_*100:.0f}%，探底回升", close, date_str))

        # MA 金叉死叉
        ma5 = _f(row["ma5"])
        ma10 = _f(row["ma10"])
        prev_ma5 = _f(prev["ma5"])
        prev_ma10 = _f(prev["ma10"])
        if prev_ma5 <= prev_ma10 and ma5 > ma10:
            signals.append(_make_signal(i, "ma_golden_cross", f"MA5 {ma5:.2f} 上穿 MA10 {ma10:.2f}", close, date_str))
        elif prev_ma5 >= prev_ma10 and ma5 < ma10:
            signals.append(_make_signal(i, "ma_death_cross", f"MA5 {ma5:.2f} 下穿 MA10 {ma10:.2f}", close, date_str))

        # ── MA5 战法：调 detect_ma5_signal 拿今日结果 ─────
        # 需要至少 20 根窗口
        window_start = max(0, i - 19)
        window = frame.iloc[window_start:i + 1]
        if len(window) >= 3:
            cols_needed = ["date", "close", "ma5", "ma10", "ma20", "volume", "volume_ma5"]
            bars_dict = window[cols_needed].to_dict("records")
            ma5_result = detect_ma5_signal(bars_dict)

            # 战法信号（只在信号出现的日子发一次）
            sig_code = ma5_result["signal"]
            if sig_code in ("breakout_confirm", "pullback_to_ma5", "gentle_rise"):
                # 同一形态连续多日只报第一次
                if prev_signal_code != sig_code:
                    signals.append(_make_signal(
                        i, sig_code, ma5_result["reason"], close, date_str,
                        extra={
                            "entry_quality": ma5_result.get("entry_quality"),
                            "ma_aligned": ma5_result.get("ma_aligned"),
                            "ma20_slope_up": ma5_result.get("ma20_slope_up"),
                            "rsi": ma5_result.get("rsi"),
                        }
                    ))

            # MA 结构变化
            ma_aligned = ma5_result.get("ma_aligned", False)
            slope_up = ma5_result.get("ma20_slope_up", False)

            # 多头排列首次建立
            if ma_aligned and not prev_ma_aligned:
                signals.append(_make_signal(
                    i, "ma_alignment_up",
                    f"MA5 {ma5:.2f} > MA10 {ma10:.2f} > MA20 {_f(row['ma20']):.2f}",
                    close, date_str,
                ))

            # 主升段首次确认（多头排列 + MA20 斜率转正）
            if ma_aligned and slope_up and not prev_slope_up:
                signals.append(_make_signal(
                    i, "ma20_uptrend_confirmed",
                    f"MA20 五日斜率向上，主升段确认",
                    close, date_str,
                ))

            # 风险信号（把 risk_flags 拆开）
            for flag in ma5_result.get("risk_flags", []):
                code = {
                    "量价背离": "risk_volume_price_diverge",
                    "高位过热": "risk_overheated",
                    "涨停炸板": "risk_limit_up_break",
                }.get(flag)
                if code:
                    signals.append(_make_signal(
                        i, code, f"MA5 战法风险：{flag}",
                        close, date_str,
                        extra={"rsi": ma5_result.get("rsi")}
                    ))

            prev_ma_aligned = ma_aligned
            prev_slope_up = slope_up
            prev_signal_code = sig_code

    return signals


def summarize_signals(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """信号统计摘要：多空计数、按类别分组、最新一条。"""
    bullish = [s for s in signals if s["direction"] == "bullish"]
    bearish = [s for s in signals if s["direction"] == "bearish"]
    warning = [s for s in signals if s["direction"] == "warning"]

    ma5_strategy = [s for s in signals if s.get("category") == "ma5_strategy"]
    ma5_risk = [s for s in signals if s.get("category") == "ma5_risk"]
    generic = [s for s in signals if s.get("category") == "generic"]

    return {
        "total": len(signals),
        "bullish_count": len(bullish),
        "bearish_count": len(bearish),
        "warning_count": len(warning),
        "ma5_strategy_count": len(ma5_strategy),
        "ma5_risk_count": len(ma5_risk),
        "generic_count": len(generic),
        "latest": signals[-1] if signals else None,
        "latest_ma5_strategy": ma5_strategy[-1] if ma5_strategy else None,
        "latest_ma5_risk": ma5_risk[-1] if ma5_risk else None,
    }
