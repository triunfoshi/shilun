from __future__ import annotations

from typing import Any

import math


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if not denominator:
        return default
    return _f(numerator / denominator, default)


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, _f(value)))


def _round(value: Any, digits: int = 4) -> float:
    return round(_f(value), digits)


def _slope(bars: list[dict[str, Any]], column: str, n: int) -> float:
    if len(bars) <= n:
        return 0.0
    latest = _f(bars[-1].get(column))
    prior = _f(bars[-1 - n].get(column))
    return _safe_div(latest, prior) - 1.0 if latest and prior else 0.0


def _calc_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, len(closes)):
        delta = closes[idx] - closes[idx - 1]
        gains.append(max(0.0, delta))
        losses.append(max(0.0, -delta))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss <= 0:
        return 100.0
    rs = avg_gain / avg_loss
    return _clip(100.0 - 100.0 / (1.0 + rs))


def _ma_alignment_score(latest: dict[str, Any]) -> float:
    ma5 = _f(latest.get("ma5"))
    ma10 = _f(latest.get("ma10"))
    ma20 = _f(latest.get("ma20"))
    close = _f(latest.get("close"))
    score = 0.0
    if ma5 and close >= ma5:
        score += 30
    if ma5 and ma10 and ma5 > ma10:
        score += 25
    if ma10 and ma20 and ma10 > ma20:
        score += 20
    if ma20 and close >= ma20:
        score += 15
    if ma20 and _f(latest.get("ma20_slope_5d")) > 0:
        score += 10
    return _clip(score)


def _prior_extension(bars: list[dict[str, Any]], lookback: int = 8) -> float:
    window = bars[-lookback - 1:-1] if len(bars) > 1 else bars[-lookback:]
    best = 0.0
    for bar in window:
        close = _f(bar.get("close"))
        ma5 = _f(bar.get("ma5"))
        if close and ma5:
            best = max(best, close / ma5 - 1.0)
    return best


def _pullback_depth(bars: list[dict[str, Any]], lookback: int = 8) -> float:
    window = bars[-lookback:] if bars else []
    highs = [_f(bar.get("high") or bar.get("close")) for bar in window]
    lows = [_f(bar.get("low") or bar.get("close")) for bar in window]
    highs = [value for value in highs if value > 0]
    lows = [value for value in lows if value > 0]
    if not highs or not lows:
        return 0.0
    return max(0.0, 1.0 - min(lows) / max(highs))


def _reclaim_flag(latest: dict[str, Any], prev: dict[str, Any]) -> bool:
    return _f(latest.get("close")) > _f(latest.get("ma5")) and _f(prev.get("close")) <= _f(prev.get("ma5"))


def _breakout_flag(latest: dict[str, Any], prev: dict[str, Any]) -> bool:
    """MA5 突破加速：昨日已收在 MA5 上方，今日实体大幅拉开距离。

    与 _reclaim_flag（昨日 close <= prev_ma5、今日回站上）严格互斥。
    判定口径参照 v0.2 战法第八章"有效 MA5 突破"：
      1. 昨日 close > prev_ma5（避免与 reclaim 交叠）
      2. 今日 close > ma5 * 1.005（突破幅度 >= 0.5% 才算加速，避免贴线噪声）
      3. real_body_ratio > 0.35（阳线实体健康）
      4. close_position > 0.55（收在当日中枢以上）
    """
    close = _f(latest.get("close"))
    ma5 = _f(latest.get("ma5"))
    prev_close = _f(prev.get("close"))
    prev_ma5 = _f(prev.get("ma5"))
    if not (close and ma5 and prev_ma5):
        return False
    if prev_close <= prev_ma5:
        return False
    if close <= ma5 * 1.005:
        return False
    if _f(latest.get("real_body_ratio")) <= 0.35:
        return False
    if _f(latest.get("close_position"), 0.5) <= 0.55:
        return False
    return True


def _bullish_engulf_flag(latest: dict[str, Any], prev: dict[str, Any]) -> bool:
    latest_open = _f(latest.get("open"))
    latest_close = _f(latest.get("close"))
    prev_open = _f(prev.get("open"))
    prev_close = _f(prev.get("close"))
    return latest_close > latest_open and prev_close < prev_open and latest_open <= prev_close and latest_close >= prev_open


def _previous_high(bars: list[dict[str, Any]], window: int) -> float:
    prior = bars[-window - 1:-1] if len(bars) > 1 else bars[-window:]
    highs = [_f(bar.get("high") or bar.get("close")) for bar in prior]
    highs = [value for value in highs if value > 0]
    return max(highs) if highs else 0.0


def _new_high_without_volume(bars: list[dict[str, Any]]) -> bool:
    if len(bars) < 20:
        return False
    latest = bars[-1]
    previous_high = _previous_high(bars, 20)
    volume_pct = _f(latest.get("volume_percentile_120"), 0.5)
    return bool(previous_high and _f(latest.get("high") or latest.get("close")) > previous_high and volume_pct < 0.6)


def _volume_price_divergence(bars: list[dict[str, Any]]) -> bool:
    if len(bars) < 5:
        return False
    window = bars[-5:]
    closes = [_f(bar.get("close")) for bar in window]
    volumes = [_f(bar.get("volume")) for bar in window]
    if not closes or not volumes:
        return False
    price_new_high = closes[-1] >= max(closes)
    volume_falling = volumes[-1] < volumes[-2] < volumes[-3] if len(volumes) >= 3 else False
    return bool(price_new_high and volume_falling)


def build_ma_features(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """提取 MA5 v0.2 的 MVP 指标字段。

    bars 需要按日期升序；字段缺失时尽量返回安全默认值，方便候选池持续运行。
    """
    if not bars or len(bars) < 3:
        return {"insufficient_bars": True}

    latest = bars[-1]
    prev = bars[-2]
    close = _f(latest.get("close"))
    ma5 = _f(latest.get("ma5"))
    ma10 = _f(latest.get("ma10"))
    ma20 = _f(latest.get("ma20"))
    low = _f(latest.get("low") or close)
    high = _f(latest.get("high") or close)
    volume_ratio_20 = _f(latest.get("volume_ratio_20") or latest.get("volume_ma5_ratio") or latest.get("volume_ratio"), 1.0)
    amount_ratio_60 = _f(latest.get("amount_ratio_60"), 1.0)
    close_position = _f(latest.get("close_position"), 0.5)
    real_body_ratio = _f(latest.get("real_body_ratio"), 0.0)
    upper_shadow_ratio = _f(latest.get("upper_shadow_ratio"), 0.0)
    atr_pct_14 = _f(latest.get("atr_pct_14"), 0.02)
    median_abs_return_20d = _f(latest.get("median_abs_return_20d"), 0.02)
    previous_high_20 = _previous_high(bars, 20)
    previous_high_60 = _previous_high(bars, 60)
    return_20d = 0.0
    if len(bars) >= 20:
        prior_close = _f(bars[-20].get("close"))
        if prior_close:
            return_20d = close / prior_close - 1.0
    return_60d = 0.0
    if len(bars) >= 60:
        prior_close = _f(bars[-60].get("close"))
        if prior_close:
            return_60d = close / prior_close - 1.0

    ma5_slope_3d = _slope(bars, "ma5", 3)
    ma5_slope_5d = _slope(bars, "ma5", 5)
    ma20_slope_5d = _slope(bars, "ma20", 5)
    latest_with_slope = {**latest, "ma20_slope_5d": ma20_slope_5d}
    close_ma5_ratio = _safe_div(close, ma5) - 1.0 if close and ma5 else 0.0
    close_ma10_ratio = _safe_div(close, ma10) - 1.0 if close and ma10 else 0.0
    close_ma20_ratio = _safe_div(close, ma20) - 1.0 if close and ma20 else 0.0
    pullback_to_ma5_distance = _safe_div(low, ma5) - 1.0 if low and ma5 else 0.0
    previous_high_hold_ratio = _safe_div(low, previous_high_20) - 1.0 if low and previous_high_20 else 0.0

    features = {
        "insufficient_bars": False,
        "close": close,
        "open": _f(latest.get("open")),
        "high": high,
        "low": low,
        "ma5": ma5,
        "ma8": _f(latest.get("ma8")),
        "ma10": ma10,
        "ma20": ma20,
        "close_ma5_ratio": close_ma5_ratio,
        "ma5_slope_3d": ma5_slope_3d,
        "ma5_slope_5d": ma5_slope_5d,
        "ma5_distance": close_ma5_ratio,
        "close_ma10_ratio": close_ma10_ratio,
        "close_ma20_ratio": close_ma20_ratio,
        "ma20_slope_5d": ma20_slope_5d,
        "ma5_extension_percentile_120": _f(latest.get("extension_percentile_120"), 0.5),
        "ma_alignment_score": _ma_alignment_score(latest_with_slope),
        "prior_extension_from_ma5": _prior_extension(bars),
        "pullback_depth": _pullback_depth(bars),
        "pullback_to_ma5_distance": pullback_to_ma5_distance,
        "pullback_volume_ratio": volume_ratio_20,
        "volume_ratio_20": volume_ratio_20,
        "amount_ratio_60": amount_ratio_60,
        "ma5_reclaim_flag": _reclaim_flag(latest, prev),
        "bullish_engulf_flag": _bullish_engulf_flag(latest, prev),
        "ma5_hold_flag": bool(low >= ma5 * 0.98 and close >= ma5) if ma5 else False,
        "ma5_breakout_flag": _breakout_flag(latest, prev),
        "ma5_breakout_distance": close_ma5_ratio,
        "breakout_volume_ratio": volume_ratio_20,
        "close_position": close_position,
        "real_body_ratio": real_body_ratio,
        "previous_high_20": previous_high_20,
        "previous_high_60": previous_high_60,
        "previous_high_break_flag": bool(previous_high_20 and close > previous_high_20),
        "box_break_flag": bool(previous_high_20 and close > previous_high_20 * 1.01),
        "previous_high_hold_ratio": previous_high_hold_ratio,
        "fall_back_into_box_flag": bool(previous_high_20 and high > previous_high_20 and close < previous_high_20),
        "post_breakout_drawdown_5d": _pullback_depth(bars, 5),
        "post_breakout_shrink_ratio": volume_ratio_20,
        "mild_volume_up_flag": bool(_f(latest.get("pct_chg")) > 0 and 1.1 <= volume_ratio_20 <= 1.8),
        "shrink_pullback_flag": bool(_f(latest.get("pct_chg")) < 0 and volume_ratio_20 < 0.8),
        "high_volume_stall_flag": bool(volume_ratio_20 > 1.3 and close_position < 0.4),
        "volume_break_ma5_flag": bool(ma5 and close < ma5 and volume_ratio_20 > 1.3),
        "volume_down_risk_flag": bool(_f(latest.get("pct_chg")) < -0.02 and volume_ratio_20 > 1.3),
        "new_high_without_volume_flag": _new_high_without_volume(bars),
        "long_upper_shadow_flag": bool(upper_shadow_ratio > 0.4),
        "upper_shadow_ratio": upper_shadow_ratio,
        "lower_shadow_ratio": _f(latest.get("lower_shadow_ratio"), 0.0),
        "strong_real_body_flag": bool(real_body_ratio > 0.6),
        "volume_price_divergence_flag": _volume_price_divergence(bars),
        "atr_pct_14": atr_pct_14,
        "median_abs_return_20d": median_abs_return_20d,
        "dynamic_pullback_min": max(0.005, 0.5 * median_abs_return_20d),
        "dynamic_pullback_max": max(0.03, 2.5 * median_abs_return_20d),
        "dynamic_tolerance": max(0.01, 0.3 * atr_pct_14),
        "return_20d": return_20d,
        "return_60d": return_60d,
        "rsi": _calc_rsi([_f(bar.get("close")) for bar in bars]),
    }
    return {key: _round(value) if isinstance(value, float) else value for key, value in features.items()}


def _score_close_above_ma5(features: dict[str, Any]) -> float:
    ratio = _f(features.get("close_ma5_ratio"))
    if ratio > 0.03:
        return 100
    if ratio > 0:
        return 60 + ratio * 1333
    if ratio > -0.02:
        return 40 + (ratio + 0.02) * 1000
    if ratio > -0.05:
        return 20
    return 0


def _score_ma5_slope(features: dict[str, Any]) -> float:
    slope = _f(features.get("ma5_slope_3d"))
    if slope > 0.02:
        return 100
    if slope > 0:
        return 50 + slope * 2500
    if slope > -0.01:
        return 30
    return 0


def compute_trend_structure_score(features: dict[str, Any]) -> dict[str, Any]:
    parts = {
        "close_above_ma5": _clip(_score_close_above_ma5(features)),
        "ma5_slope": _clip(_score_ma5_slope(features)),
        "ma_alignment": _clip(_f(features.get("ma_alignment_score"))),
        "ma10_buffer": 100 if _f(features.get("close_ma10_ratio")) > 0 else 60 if _f(features.get("close_ma10_ratio")) > -0.02 else 30,
        "ma20_background": 100 if _f(features.get("close_ma20_ratio")) > 0.05 else 70 if _f(features.get("close_ma20_ratio")) > 0 else 40,
    }
    score = (
        parts["close_above_ma5"] * 0.30
        + parts["ma5_slope"] * 0.25
        + parts["ma_alignment"] * 0.20
        + parts["ma10_buffer"] * 0.15
        + parts["ma20_background"] * 0.10
    )
    return {"score": round(_clip(score), 2), "parts": parts}


def compute_volume_price_score(features: dict[str, Any]) -> dict[str, Any]:
    contributions: dict[str, float] = {}
    if features.get("mild_volume_up_flag"):
        contributions["mild_volume_up"] = 20
    if features.get("shrink_pullback_flag"):
        contributions["shrink_pullback"] = 20
    if features.get("high_volume_stall_flag"):
        contributions["high_volume_stall"] = -25
    if features.get("volume_down_risk_flag"):
        contributions["volume_down_risk"] = -30
    if features.get("new_high_without_volume_flag"):
        contributions["new_high_without_volume"] = -20
    if _f(features.get("upper_shadow_ratio")) > 0.4:
        contributions["upper_shadow_warning"] = -15
    if features.get("strong_real_body_flag"):
        contributions["strong_real_body"] = 10
    raw = sum(contributions.values())
    normalized = _clip(50 + raw)
    return {"score": round(normalized, 2), "raw": round(raw, 2), "parts": contributions}


def compute_ma5_pullback_score(features: dict[str, Any]) -> dict[str, Any]:
    prior = _clip(_safe_div(_f(features.get("prior_extension_from_ma5")), max(0.03, _f(features.get("dynamic_pullback_min"))), 0) * 80)
    depth = _f(features.get("pullback_depth"))
    dyn_min = _f(features.get("dynamic_pullback_min"), 0.01)
    dyn_max = _f(features.get("dynamic_pullback_max"), 0.08)
    if dyn_min <= depth <= dyn_max:
        depth_score = 100
    elif depth < dyn_min:
        depth_score = _clip(depth / max(dyn_min, 0.001) * 70)
    else:
        depth_score = _clip(100 - (depth - dyn_max) * 800)
    distance = abs(_f(features.get("pullback_to_ma5_distance")))
    tolerance = max(0.02, _f(features.get("dynamic_tolerance")) * 2)
    distance_score = _clip(100 - distance / tolerance * 70)
    volume = _f(features.get("pullback_volume_ratio"), 1.0)
    shrink_score = 100 if volume < 0.8 else _clip(100 - (volume - 0.8) * 160)
    reclaim_score = 100 if features.get("ma5_reclaim_flag") or features.get("bullish_engulf_flag") else 65 if features.get("ma5_hold_flag") else 20
    score = prior * 0.25 + depth_score * 0.25 + distance_score * 0.20 + shrink_score * 0.20 + reclaim_score * 0.10
    return {
        "score": round(_clip(score), 2),
        "passed_rules": int(sum([
            _f(features.get("prior_extension_from_ma5")) >= max(0.03, dyn_min * 0.6),
            dyn_min <= depth <= dyn_max,
            distance <= tolerance,
            volume < 0.8,
            bool(features.get("ma5_reclaim_flag") or features.get("bullish_engulf_flag") or features.get("ma5_hold_flag")),
        ])),
        "parts": {
            "prior_extension": round(prior, 2),
            "pullback_depth": round(depth_score, 2),
            "distance_to_ma5": round(distance_score, 2),
            "shrink_volume": round(shrink_score, 2),
            "reclaim_or_hold": round(reclaim_score, 2),
        },
    }


def compute_ma5_breakout_score(features: dict[str, Any]) -> dict[str, Any]:
    breakout = 100 if features.get("ma5_breakout_flag") else 25 if _f(features.get("close_ma5_ratio")) > 0 else 0
    volume = _clip((_f(features.get("breakout_volume_ratio"), 1.0) - 1.0) / 0.8 * 100)
    position = _clip((_f(features.get("close_position"), 0.5) - 0.4) / 0.35 * 100)
    body = _clip((_f(features.get("real_body_ratio")) - 0.2) / 0.4 * 100)
    previous_high = 100 if features.get("previous_high_break_flag") or features.get("box_break_flag") else 50
    score = breakout * 0.30 + volume * 0.25 + position * 0.20 + body * 0.15 + previous_high * 0.10
    return {
        "score": round(_clip(score), 2),
        "parts": {
            "breakout_flag": round(breakout, 2),
            "volume": round(volume, 2),
            "close_position": round(position, 2),
            "real_body": round(body, 2),
            "previous_high": round(previous_high, 2),
        },
    }


def compute_breakout_quality_score(
    features: dict[str, Any],
    *,
    breakout_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """真假突破分。

    - `breakout_event=None`：退化为当日快照评分（fallback），用于还没被 Job 3
      落库的票（例如没触发过突破的候选、或者刚起步系统还没建事件表）。
    - 传入 `breakout_event`：从 `breakout_events` 集合里的追踪字段读取真实的
      T+1..T+5 表现。此时 `next_day_score` 不再是常量 50，而是从
      `event["next_day_hold_flag"]` 得到 100 / 0 / 50（未追踪）。

    输出多一个 `source` 字段（"event" | "features"）便于调试和前端展示。
    """
    if breakout_event:
        hold = _f(breakout_event.get("previous_high_hold_ratio"))
        fall_back = bool(breakout_event.get("fall_back_into_box_flag"))
        shrink = _f(breakout_event.get("post_breakout_shrink_ratio"), 1.0)
        next_day_flag = breakout_event.get("next_day_hold_flag")  # True / False / None
        tracked_days = int(breakout_event.get("tracked_days") or 0)
        source = "event"
    else:
        hold = _f(features.get("previous_high_hold_ratio"))
        fall_back = bool(features.get("fall_back_into_box_flag"))
        shrink = _f(features.get("post_breakout_shrink_ratio"), 1.0)
        next_day_flag = None
        tracked_days = 0
        source = "features"

    tolerance = _f(features.get("dynamic_tolerance"), 0.01)
    if hold >= 0:
        hold_score = 100
        grade = "hold"
    elif hold >= -tolerance:
        hold_score = 70
        grade = "minor_break"
    elif hold >= -tolerance * 2:
        hold_score = 40
        grade = "suspicious"
    else:
        hold_score = 10
        grade = "clear_break"
    box_score = 0 if fall_back else 80
    shrink_score = 80 if shrink < 0.9 else 50
    if next_day_flag is True:
        next_day_score = 100
    elif next_day_flag is False:
        next_day_score = 0
    else:
        next_day_score = 50  # 未追踪或数据缺失

    score = hold_score * 0.35 + box_score * 0.25 + shrink_score * 0.20 + next_day_score * 0.20
    return {
        "score": round(_clip(score), 2),
        "grade": grade,
        "source": source,
        "tracked_days": tracked_days,
        "breakout_quality": breakout_event.get("breakout_quality") if breakout_event else None,
        "parts": {
            "previous_high_hold": hold_score,
            "box_hold": box_score,
            "post_breakout_shrink": shrink_score,
            "next_day": next_day_score,
        },
    }


def compute_trade_timing_score(
    features: dict[str, Any],
    *,
    breakout_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pullback = compute_ma5_pullback_score(features)
    breakout = compute_ma5_breakout_score(features)
    breakout_quality = compute_breakout_quality_score(features, breakout_event=breakout_event)
    volume = compute_volume_price_score(features)
    volume_confirm = _clip(volume["score"] + (10 if features.get("strong_real_body_flag") else 0))
    score = pullback["score"] * 0.35 + breakout["score"] * 0.25 + breakout_quality["score"] * 0.20 + volume_confirm * 0.20
    if pullback["score"] >= breakout["score"] and pullback["score"] >= 60:
        buy_point_type = "ma5_pullback"
        buy_point_label = "MA5回踩确认"
    elif features.get("ma5_breakout_flag") and breakout["score"] >= 60:
        buy_point_type = "ma5_breakout"
        buy_point_label = "MA5突破确认"
    elif features.get("ma5_reclaim_flag"):
        buy_point_type = "ma5_reclaim"
        buy_point_label = "MA5假跌破站回"
    else:
        buy_point_type = "watch"
        buy_point_label = "观察"
    return {
        "score": round(_clip(score), 2),
        "buy_point_type": buy_point_type,
        "buy_point_label": buy_point_label,
        "parts": {
            "ma5_pullback": pullback,
            "ma5_breakout": breakout,
            "breakout_quality": breakout_quality,
            "volume_confirmation": round(volume_confirm, 2),
        },
    }


def compute_stock_quality_score(
    features: dict[str, Any],
    *,
    sector_score: float = 50.0,
    benchmark_return_20d: float = 0.0,
) -> dict[str, Any]:
    trend = compute_trend_structure_score(features)
    volume = compute_volume_price_score(features)
    excess_20d = _f(features.get("return_20d")) - _f(benchmark_return_20d)
    relative_strength = _clip(50 + excess_20d * 500)
    score = trend["score"] * 0.35 + _clip(sector_score) * 0.25 + volume["score"] * 0.20 + relative_strength * 0.20
    return {
        "score": round(_clip(score), 2),
        "parts": {
            "trend_structure": trend,
            "sector": round(_clip(sector_score), 2),
            "volume_price": volume,
            "relative_strength": round(relative_strength, 2),
        },
    }


def compute_risk_adjustment(
    features: dict[str, Any],
    *,
    market_gate: dict[str, Any] | None = None,
    sector_state: str | None = None,
) -> dict[str, Any]:
    adjustment = 1.0
    factors: list[dict[str, Any]] = []

    def apply(code: str, label: str, factor: float, triggered: bool) -> None:
        nonlocal adjustment
        if triggered:
            adjustment *= factor
            factors.append({"code": code, "label": label, "factor": factor})

    apply("high_volume_stall", "高位放量滞涨", 0.80, bool(features.get("high_volume_stall_flag")))
    apply("upper_shadow_warning", "长上影线", 0.85, _f(features.get("upper_shadow_ratio")) > 0.4)
    apply("new_high_without_volume", "新高无量", 0.85, bool(features.get("new_high_without_volume_flag")))
    apply("volume_break_ma5", "放量跌破MA5", 0.60, bool(features.get("volume_break_ma5_flag")))
    apply("sector_retreat", "板块退潮", 0.60, str(sector_state or "") == "retreat")
    permission = str((market_gate or {}).get("permission") or "")
    apply("market_defense", "大盘防守", 0.70, permission == "defense")
    apply("market_empty", "大盘空仓", 0.00, permission == "empty")
    return {"score": round(max(0.0, min(1.0, adjustment)), 3), "factors": factors}


def compute_final_trade_score(
    trade_timing_score: float,
    *,
    market_multiplier: float = 1.0,
    sector_multiplier: float = 1.0,
    risk_adjustment: float = 1.0,
) -> float:
    return round(_f(trade_timing_score) * _f(market_multiplier, 1.0) * _f(sector_multiplier, 1.0) * _f(risk_adjustment, 1.0), 2)


def build_trade_plan(features: dict[str, Any]) -> dict[str, Any]:
    close = _f(features.get("close"))
    ma5 = _f(features.get("ma5"))
    ma8 = _f(features.get("ma8"))
    ma10 = _f(features.get("ma10"))
    ma20 = _f(features.get("ma20"))
    previous_high_60 = _f(features.get("previous_high_60"))
    median_abs = _f(features.get("median_abs_return_20d"), 0.02)
    support_1 = ma5 or close
    support_2 = ma10 or support_1
    support_3 = ma20 or support_2
    entry = close
    confirm = max(ma5, _f(features.get("open")), close * 0.995) if close else 0.0
    stop_1 = support_1 * 0.98 if support_1 else 0.0
    stop_2 = support_2 * 0.98 if support_2 else 0.0
    breakdown = support_3 * 0.99 if support_3 else 0.0
    target = previous_high_60 if previous_high_60 > entry * 1.02 else entry * (1 + max(0.08, 2.5 * median_abs * 5))
    add_point = max(entry * 1.07, target * 1.03) if entry and target else 0.0
    rr = _safe_div(target - entry, entry - stop_1) if entry and stop_1 and entry > stop_1 else 0.0
    return {
        "entry_price": round(entry, 2) if entry else None,
        "confirm_point": round(confirm, 2) if confirm else None,
        "support_1": round(support_1, 2) if support_1 else None,
        "support_1_source": "MA5",
        "support_2": round(support_2, 2) if support_2 else None,
        "support_2_source": "MA10",
        "support_3": round(support_3, 2) if support_3 else None,
        "support_3_source": "MA20",
        "stop_loss_1": round(stop_1, 2) if stop_1 else None,
        "stop_loss_2": round(stop_2, 2) if stop_2 else None,
        "breakdown_level": round(breakdown, 2) if breakdown else None,
        "target_price": round(target, 2) if target else None,
        "target_source": "60日前高" if previous_high_60 > entry * 1.02 else "波动率目标",
        "add_point": round(add_point, 2) if add_point else None,
        "ma8": round(ma8, 2) if ma8 else None,
        "reward_risk_ratio": round(rr, 2) if rr else None,
        "invalid_conditions": [
            f"收盘跌破 MA5 支撑 {support_1:.2f}" if support_1 else "收盘跌破 MA5",
            f"跌破 MA10 防线 {support_2:.2f}" if support_2 else "跌破 MA10",
            "板块状态变为退潮或大盘权限变为空仓",
        ],
    }
