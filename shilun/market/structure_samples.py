from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any, Iterable


SAMPLE_VERSION = "structure_operability_v1"
TEMPLATE_VERSION = "abc_templates_v1"
OUTCOME_HORIZONS = (1, 3, 5, 10)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def _clip(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, _f(value))), 2)


def _mean(values: Iterable[Any]) -> float:
    numbers = [_f(value) for value in values if value not in {None, ""}]
    return sum(numbers) / len(numbers) if numbers else 0.0


def _ratio(numerator: Any, denominator: Any, default: float = 0.0) -> float:
    denominator_value = _f(denominator)
    return _f(numerator) / denominator_value if denominator_value else default


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:10]


def _moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return _mean(values[-window:])


def _return(values: list[float], sessions: int) -> float | None:
    if len(values) <= sessions or values[-1 - sessions] <= 0:
        return None
    return values[-1] / values[-1 - sessions] - 1.0


def _slope(values: list[float], window: int, sessions: int = 5) -> float | None:
    if len(values) < window + sessions:
        return None
    current = _mean(values[-window:])
    previous = _mean(values[-window - sessions : -sessions])
    return current / previous - 1.0 if previous else None


def _true_range_mean(bars: list[dict[str, Any]], window: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    values: list[float] = []
    for index in range(max(1, len(bars) - window), len(bars)):
        bar = bars[index]
        previous_close = _f(bars[index - 1].get("close"))
        high = _f(bar.get("high"))
        low = _f(bar.get("low"))
        values.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return _mean(values)


def _efficiency_ratio(closes: list[float], window: int = 20) -> float:
    if len(closes) <= window:
        return 0.0
    path = sum(abs(closes[index] - closes[index - 1]) for index in range(len(closes) - window, len(closes)))
    return abs(closes[-1] - closes[-1 - window]) / path if path else 0.0


def _recent_swing_low(bars: list[dict[str, Any]], lookback: int = 25) -> float | None:
    if not bars:
        return None
    lows = [_f(bar.get("low") or bar.get("close")) for bar in bars]
    start = max(2, len(lows) - lookback)
    for index in range(len(lows) - 3, start - 1, -1):
        value = lows[index]
        if value <= min(lows[index - 2 : index]) and value <= min(lows[index + 1 : index + 3]):
            return value
    recent = [value for value in lows[-10:] if value > 0]
    return min(recent) if recent else None


def _nearest_below(reference: float, values: Iterable[Any]) -> float | None:
    eligible = [_f(value) for value in values if 0 < _f(value) < reference]
    return max(eligible) if eligible else None


def _nearest_above(reference: float, values: Iterable[Any]) -> float | None:
    eligible = [_f(value) for value in values if _f(value) > reference]
    return min(eligible) if eligible else None


def _status(score: float) -> str:
    if score >= 70:
        return "strong"
    if score >= 50:
        return "usable"
    if score >= 35:
        return "weak"
    return "blocked"


def build_feature_snapshot(
    bars: list[dict[str, Any]],
    *,
    benchmark_bars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ordered = sorted(bars, key=lambda item: _date_text(item.get("date") or item.get("trade_date")))
    closes = [_f(item.get("close")) for item in ordered if _f(item.get("close")) > 0]
    if not ordered or not closes:
        return {"history_count": 0, "insufficient_history": True}
    latest = ordered[-1]
    close = closes[-1]
    high = _f(latest.get("high") or close)
    low = _f(latest.get("low") or close)
    open_price = _f(latest.get("open") or close)
    volumes = [_f(item.get("volume") or item.get("vol")) for item in ordered]
    ma5 = _moving_average(closes, 5)
    ma10 = _moving_average(closes, 10)
    ma20 = _moving_average(closes, 20)
    ma60 = _moving_average(closes, 60)
    ma120 = _moving_average(closes, 120)
    atr14 = _true_range_mean(ordered)
    recent20 = ordered[-20:]
    prior20 = ordered[-21:-1] if len(ordered) > 20 else ordered[:-1]
    prior60 = ordered[-61:-1] if len(ordered) > 60 else ordered[:-1]
    previous_high_20 = max((_f(item.get("high")) for item in prior20), default=0.0)
    previous_high_60 = max((_f(item.get("high")) for item in prior60), default=0.0)
    recent_low_20 = min((_f(item.get("low") or item.get("close")) for item in recent20), default=0.0)
    recent_high_20 = max((_f(item.get("high") or item.get("close")) for item in recent20), default=0.0)
    mean_close_20 = _mean(_f(item.get("close")) for item in recent20)
    range_ratio_20 = (recent_high_20 - recent_low_20) / mean_close_20 if mean_close_20 else 0.0
    recent10 = ordered[-10:]
    range_ratio_10 = (
        max((_f(item.get("high")) for item in recent10), default=close)
        - min((_f(item.get("low")) for item in recent10), default=close)
    ) / (_mean(_f(item.get("close")) for item in recent10) or close)
    volume_ratio_20 = _ratio(volumes[-1], _mean(volumes[-20:]), 1.0)
    volume_shrink_5_20 = _ratio(_mean(volumes[-5:]), _mean(volumes[-20:]), 1.0)
    day_range = max(0.0, high - low)
    close_position = (close - low) / day_range if day_range else 0.5
    real_body_ratio = abs(close - open_price) / day_range if day_range else 0.0
    upper_shadow_ratio = (high - max(open_price, close)) / day_range if day_range else 0.0
    pullback_depth_20 = max(0.0, 1.0 - close / recent_high_20) if recent_high_20 else 0.0
    higher_low = False
    if len(ordered) >= 10:
        current_low = min(_f(item.get("low")) for item in ordered[-5:])
        prior_low = min(_f(item.get("low")) for item in ordered[-10:-5])
        higher_low = current_low > prior_low
    new_low_20 = bool(recent_low_20 and low <= recent_low_20)
    high_volume_stall = volume_ratio_20 >= 1.3 and close_position < 0.4
    fall_back_into_box = bool(previous_high_20 and high > previous_high_20 and close < previous_high_20)
    benchmark_closes = [
        _f(item.get("close"))
        for item in sorted(benchmark_bars or [], key=lambda item: _date_text(item.get("date") or item.get("trade_date")))
        if _f(item.get("close")) > 0
    ]
    return_5d = _return(closes, 5)
    return_20d = _return(closes, 20)
    benchmark_return_5d = _return(benchmark_closes, 5)
    benchmark_return_20d = _return(benchmark_closes, 20)
    return {
        "history_count": len(ordered),
        "insufficient_history": len(ordered) < 60,
        "date": _date_text(latest.get("date") or latest.get("trade_date")),
        "open": round(open_price, 4),
        "high": round(high, 4),
        "low": round(low, 4),
        "close": round(close, 4),
        "volume": round(volumes[-1], 4),
        "ma5": round(ma5, 4) if ma5 else None,
        "ma10": round(ma10, 4) if ma10 else None,
        "ma20": round(ma20, 4) if ma20 else None,
        "ma60": round(ma60, 4) if ma60 else None,
        "ma120": round(ma120, 4) if ma120 else None,
        "ma20_slope_5d": round(_slope(closes, 20) or 0.0, 6),
        "ma60_slope_5d": round(_slope(closes, 60) or 0.0, 6),
        "return_5d": round(return_5d, 6) if return_5d is not None else None,
        "return_20d": round(return_20d, 6) if return_20d is not None else None,
        "benchmark_return_5d": round(benchmark_return_5d, 6) if benchmark_return_5d is not None else None,
        "benchmark_return_20d": round(benchmark_return_20d, 6) if benchmark_return_20d is not None else None,
        "benchmark_entry": round(benchmark_closes[-1], 4) if benchmark_closes else None,
        "excess_return_5d": round((return_5d or 0.0) - (benchmark_return_5d or 0.0), 6),
        "excess_return_20d": round((return_20d or 0.0) - (benchmark_return_20d or 0.0), 6),
        "atr14": round(atr14, 4),
        "atr_pct_14": round(atr14 / close if close else 0.0, 6),
        "efficiency_ratio_20": round(_efficiency_ratio(closes), 4),
        "range_ratio_10": round(range_ratio_10, 6),
        "range_ratio_20": round(range_ratio_20, 6),
        "volume_ratio_20": round(volume_ratio_20, 4),
        "volume_shrink_5_20": round(volume_shrink_5_20, 4),
        "close_position": round(close_position, 4),
        "real_body_ratio": round(real_body_ratio, 4),
        "upper_shadow_ratio": round(upper_shadow_ratio, 4),
        "pullback_depth_20": round(pullback_depth_20, 6),
        "previous_high_20": round(previous_high_20, 4) if previous_high_20 else None,
        "previous_high_60": round(previous_high_60, 4) if previous_high_60 else None,
        "recent_low_20": round(recent_low_20, 4) if recent_low_20 else None,
        "recent_swing_low": round(_recent_swing_low(ordered) or 0.0, 4) or None,
        "previous_high_break": bool(previous_high_20 and close > previous_high_20),
        "fall_back_into_box": fall_back_into_box,
        "higher_low_5v5": higher_low,
        "new_low_20": new_low_20,
        "high_volume_stall": high_volume_stall,
    }


def build_risk_geometry(features: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    close = _f(features.get("close") or candidate.get("close"))
    atr14 = _f(features.get("atr14"))
    support = _nearest_below(
        close,
        [
            features.get("recent_swing_low"),
            features.get("ma20"),
            candidate.get("support_price"),
            (candidate.get("trade_plan") or {}).get("support_2"),
        ],
    )
    if support is None and close:
        support = close - max(atr14, close * 0.03)
    invalidation = support - max(atr14 * 0.5, close * 0.005) if support else None
    resistance = _nearest_above(
        close,
        [candidate.get("pressure_price"), features.get("previous_high_20"), features.get("previous_high_60")],
    )
    risk = close - _f(invalidation) if invalidation else 0.0
    projected_target = resistance or (close + 2.0 * risk if risk > 0 else None)
    reward_risk = (_f(projected_target) - close) / risk if risk > 0 and projected_target else 0.0
    stop_distance_pct = risk / close if close and risk > 0 else None
    score = 0.0
    if invalidation and 0 < invalidation < close:
        score += 25.0
    score += min(45.0, reward_risk * 18.0)
    if stop_distance_pct is not None:
        score += 20.0 if stop_distance_pct <= 0.08 else 10.0 if stop_distance_pct <= 0.12 else 0.0
    if features.get("recent_swing_low"):
        score += 10.0
    return {
        "score": _clip(score),
        "status": _status(_clip(score)),
        "entry_price": round(close, 4) if close else None,
        "support_price": round(support, 4) if support else None,
        "support_basis": "recent_swing_low/MA20/candidate_support",
        "invalidation_price": round(invalidation, 4) if invalidation else None,
        "resistance_price": round(resistance, 4) if resistance else None,
        "evaluation_target": round(projected_target, 4) if projected_target else None,
        "reward_risk_ratio": round(reward_risk, 3),
        "stop_distance_pct": round(stop_distance_pct, 6) if stop_distance_pct is not None else None,
    }


def build_operability_dimensions(
    features: dict[str, Any],
    candidate: dict[str, Any],
    sector_context: dict[str, Any] | None,
    risk_geometry: dict[str, Any],
) -> dict[str, Any]:
    sector_context = sector_context or {}
    close = _f(features.get("close"))
    ma5 = _f(features.get("ma5"))
    ma10 = _f(features.get("ma10"))
    ma20 = _f(features.get("ma20"))
    ma60 = _f(features.get("ma60"))
    direction = 0.0
    direction += 20 if ma20 and close > ma20 else 0
    direction += 20 if _f(features.get("ma20_slope_5d")) > 0 else 0
    direction += 15 if ma5 and ma10 and ma5 > ma10 else 0
    direction += 15 if ma10 and ma20 and ma10 > ma20 else 0
    direction += 15 if ma20 and ma60 and ma20 > ma60 else 0
    direction += min(15.0, _f(features.get("efficiency_ratio_20")) * 30.0)

    pullback = _f(features.get("pullback_depth_20"))
    adjustment = 40.0
    adjustment += 20 if 0.02 <= pullback <= 0.12 else 8 if pullback < 0.02 else -15
    adjustment += 15 if _f(features.get("volume_shrink_5_20"), 1.0) <= 0.9 else 0
    adjustment += 15 if features.get("higher_low_5v5") else 0
    adjustment += 10 if ma20 and close >= ma20 else -10
    adjustment -= 25 if features.get("new_low_20") else 0

    supply = 72.0
    supply -= _f(features.get("upper_shadow_ratio")) * 70.0
    supply -= 25 if features.get("high_volume_stall") else 0
    supply -= 30 if features.get("fall_back_into_box") else 0
    if features.get("previous_high_break") and _f(features.get("close_position")) >= 0.65:
        supply += 18
    elif features.get("previous_high_20") and close < _f(features.get("previous_high_20")):
        distance = _f(features.get("previous_high_20")) / close - 1.0 if close else 0.0
        if 0 <= distance <= 0.04:
            supply -= 12

    sector_metrics = sector_context.get("metrics") or {}
    sector_relative_5d = _f(sector_metrics.get("relative_return_5d"))
    relative = 50.0
    relative += _f(features.get("excess_return_5d")) * 300.0
    relative += _f(features.get("excess_return_20d")) * 220.0
    relative += sector_relative_5d * 120.0
    relative += 5 if not bool(candidate.get("sector_retreat_flag")) else -20

    price_volume = 50.0
    session_return = _f(candidate.get("session_return"))
    volume_ratio = _f(features.get("volume_ratio_20"), 1.0)
    price_volume += 18 if session_return > 0 and 1.05 <= volume_ratio <= 2.5 else 0
    price_volume += 12 if _f(features.get("close_position")) >= 0.65 else -10
    price_volume += 10 if _f(features.get("real_body_ratio")) >= 0.4 else 0
    price_volume += 12 if session_return <= 0 and _f(features.get("volume_shrink_5_20"), 1.0) <= 0.9 else 0
    price_volume -= 25 if features.get("high_volume_stall") else 0
    price_volume -= 20 if features.get("fall_back_into_box") else 0

    raw = {
        "direction_clarity": (direction, ["均线层级", "MA20/MA60斜率", "20日路径效率"]),
        "adjustment_quality": (adjustment, ["回撤深度", "调整缩量", "低点是否抬高"]),
        "supply_structure": (supply, ["前高距离", "上影线", "放量滞涨/假突破"]),
        "relative_strength": (relative, ["个股相对指数5/20日", "板块相对强弱"]),
        "price_volume_confirmation": (price_volume, ["成交量位置", "收盘位置", "实体与突破承接"]),
        "risk_geometry": (_f(risk_geometry.get("score")), ["结构支撑", "ATR失效位", "收益风险比"]),
    }
    return {
        key: {"score": _clip(score), "status": _status(_clip(score)), "evidence_basis": evidence}
        for key, (score, evidence) in raw.items()
    }


def _market_state(market_gate: dict[str, Any] | None, candidate: dict[str, Any]) -> str:
    market_gate = market_gate or {}
    state = str(market_gate.get("state") or market_gate.get("permission") or "").lower()
    if state in {"attack", "hold", "defense", "empty"}:
        return state
    multiplier = _f(candidate.get("market_multiplier"), 1.0)
    if multiplier >= 1.1:
        return "attack"
    if multiplier >= 0.95:
        return "hold"
    if multiplier > 0:
        return "defense"
    return "empty"


def build_template_states(
    *,
    features: dict[str, Any],
    candidate: dict[str, Any],
    dimensions: dict[str, Any],
    common_gate: dict[str, Any],
    risk_geometry: dict[str, Any],
    market_gate: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    score = lambda key: _f((dimensions.get(key) or {}).get("score"))
    market_state = _market_state(market_gate, candidate)
    market_trade_open = market_state in {"attack", "hold"}
    candidate_trade_open = bool(candidate.get("allowed_new_position"))
    trade_timing = _f(candidate.get("trade_timing_score"))
    buy_point = str(candidate.get("buy_point_type") or candidate.get("buy_point_pattern") or "")
    close = _f(features.get("close"))
    ma5 = _f(features.get("ma5"))
    ma10 = _f(features.get("ma10"))
    ma20 = _f(features.get("ma20"))

    a_fit = _clip(
        score("direction_clarity") * 0.30
        + score("adjustment_quality") * 0.22
        + score("relative_strength") * 0.18
        + score("price_volume_confirmation") * 0.15
        + score("supply_structure") * 0.08
        + score("risk_geometry") * 0.07
    )
    a_requirements = {
        "direction_up": score("direction_clarity") >= 60 and bool(ma20 and close > ma20),
        "ma20_rising": _f(features.get("ma20_slope_5d")) > 0,
        "relative_not_weak": score("relative_strength") >= 45,
        "sector_not_retreat": not bool(candidate.get("sector_retreat_flag")),
        "common_gate": bool(common_gate.get("passed")),
    }
    a_trigger = buy_point in {"ma5_pullback", "ma5_reclaim", "ma5_breakout", "hui_cai", "tu_po"} and trade_timing >= 58
    a_matched = all(a_requirements.values()) and a_fit >= 58
    a_status = "triggered" if a_matched and a_trigger else "waiting" if a_matched else "not_matched"
    a_trade = a_status == "triggered" and market_trade_open and candidate_trade_open

    range20 = _f(features.get("range_ratio_20"))
    range10 = _f(features.get("range_ratio_10"))
    compression = 30.0
    compression += 30 if 0 < range20 <= 0.18 else 10 if range20 <= 0.25 else 0
    compression += 20 if range20 and range10 <= range20 * 0.75 else 0
    compression += 20 if _f(features.get("volume_shrink_5_20"), 1.0) <= 0.95 else 0
    compression = _clip(compression)
    b_fit = _clip(
        compression * 0.30
        + score("supply_structure") * 0.15
        + score("relative_strength") * 0.15
        + score("price_volume_confirmation") * 0.20
        + score("risk_geometry") * 0.20
    )
    b_requirements = {
        "contraction_ready": compression >= 55,
        "not_hard_downtrend": score("direction_clarity") >= 35,
        "common_gate": bool(common_gate.get("passed")),
    }
    b_trigger = bool(features.get("previous_high_break")) and _f(features.get("volume_ratio_20"), 1.0) >= 1.1 and _f(features.get("close_position")) >= 0.65
    b_matched = all(b_requirements.values()) and b_fit >= 58
    b_status = "triggered" if b_matched and b_trigger else "waiting" if b_matched else "not_matched"
    breakout_tracking = bool(candidate.get("breakout_tracking"))
    b_observation_only = not breakout_tracking
    b_trade = b_status == "triggered" and market_trade_open and candidate_trade_open and not b_observation_only

    prior_downtrend = bool(
        _f(features.get("ma20_slope_5d")) <= 0
        or (ma5 and ma20 and ma5 < ma20)
        or (ma10 and ma20 and ma10 < ma20)
    )
    repair = 20.0
    repair += 25 if features.get("higher_low_5v5") else 0
    repair += 20 if ma5 and close > ma5 else 0
    repair += 20 if _f(features.get("return_5d")) > 0 else 0
    repair += 15 if not features.get("new_low_20") else -20
    repair = _clip(repair)
    c_fit = _clip(
        repair * 0.35
        + score("adjustment_quality") * 0.20
        + score("supply_structure") * 0.15
        + score("relative_strength") * 0.15
        + score("risk_geometry") * 0.15
    )
    c_requirements = {
        "prior_downtrend": prior_downtrend,
        "repair_evidence": repair >= 55,
        "common_gate": bool(common_gate.get("passed")),
    }
    c_trigger = bool(features.get("higher_low_5v5")) and bool(ma20 and close > ma20) and _f(features.get("volume_ratio_20"), 1.0) >= 1.0
    c_matched = all(c_requirements.values()) and c_fit >= 55
    c_status = "repair_confirmed" if c_matched and c_trigger else "repair_observation" if c_matched else "not_matched"

    def row(
        template_id: str,
        label: str,
        fit_score: float,
        status: str,
        requirements: dict[str, bool],
        triggered: bool,
        observation_only: bool,
        trade_eligible: bool,
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "template_label": label,
            "template_version": TEMPLATE_VERSION,
            "fit_score": fit_score,
            "status": status,
            "matched": status != "not_matched",
            "triggered": triggered,
            "requirements": requirements,
            "observation_only": observation_only,
            "trade_eligible_at_freeze": trade_eligible,
            "market_state": market_state,
            "invalidation_price": risk_geometry.get("invalidation_price"),
            "evaluation_target": risk_geometry.get("evaluation_target"),
            "reward_risk_ratio": risk_geometry.get("reward_risk_ratio"),
        }

    return [
        row("A", "上升趋势延续", a_fit, a_status, a_requirements, a_trigger, False, a_trade),
        row("B", "收敛整理突破", b_fit, b_status, b_requirements, b_trigger, b_observation_only, b_trade),
        row("C", "下跌后结构修复", c_fit, c_status, c_requirements, c_trigger, True, False),
    ]


def build_structure_sample(
    *,
    analysis_date: str,
    candidate: dict[str, Any],
    bars: list[dict[str, Any]],
    benchmark_bars: list[dict[str, Any]],
    sector_context: dict[str, Any] | None,
    market_gate: dict[str, Any] | None,
    cache_version: str | None,
    generated_at: str,
) -> dict[str, Any]:
    ticker = str(candidate.get("ticker") or "").upper()
    normalized_date = _date_text(analysis_date)
    frozen_bars = [item for item in bars if _date_text(item.get("date") or item.get("trade_date")) <= normalized_date]
    frozen_benchmark = [item for item in benchmark_bars if _date_text(item.get("date") or item.get("trade_date")) <= normalized_date]
    features = build_feature_snapshot(frozen_bars, benchmark_bars=frozen_benchmark)
    risk_geometry = build_risk_geometry(features, candidate)
    dimensions = build_operability_dimensions(features, candidate, sector_context, risk_geometry)
    data_ready = int(features.get("history_count") or 0) >= 60
    risk_ready = _f(risk_geometry.get("score")) >= 35 and bool(risk_geometry.get("invalidation_price"))
    supply_ready = _f((dimensions.get("supply_structure") or {}).get("score")) >= 25
    common_gate = {
        "passed": bool(data_ready and risk_ready and supply_ready),
        "checks": {
            "daily_history_60": data_ready,
            "risk_geometry": risk_ready,
            "supply_not_blocked": supply_ready,
        },
        "rule": "数据完整 + 风险几何有效 + 抛压结构未硬阻断；方向由模板解释，不作为通用硬门。",
    }
    templates = build_template_states(
        features=features,
        candidate=candidate,
        dimensions=dimensions,
        common_gate=common_gate,
        risk_geometry=risk_geometry,
        market_gate=market_gate,
    )
    sample_id = sha256(f"{SAMPLE_VERSION}|{normalized_date}|{ticker}".encode("utf-8")).hexdigest()
    matched = [item["template_id"] for item in templates if item["matched"]]
    triggered = [item["template_id"] for item in templates if item["triggered"] and item["matched"]]
    trade_eligible = [item["template_id"] for item in templates if item["trade_eligible_at_freeze"]]
    source_coverage = {
        "daily_history_count": features.get("history_count"),
        "daily_history_ready": data_ready,
        "benchmark_ready": len(frozen_benchmark) >= 20,
        "sector_context_ready": bool(sector_context),
        "market_gate_ready": bool(market_gate),
        "minute_ranking_ready": candidate.get("minute_ranking_status") == "eligible",
        "breakout_tracking_ready": bool(candidate.get("breakout_tracking")),
        "chip_distribution_ready": False,
        "chip_note": "首版不以未稳定全池落库的筹码分布作为硬门。",
    }
    return {
        "sample_id": sample_id,
        "sample_version": SAMPLE_VERSION,
        "template_version": TEMPLATE_VERSION,
        "analysis_date": normalized_date,
        "ticker": ticker,
        "name": str(candidate.get("name") or ticker),
        "sector_name": str(candidate.get("sector_name") or ""),
        "pool_rank": candidate.get("pool_rank"),
        "candidate_source": candidate.get("candidate_source"),
        "frozen_at": generated_at,
        "cache_version": cache_version,
        "entry_price": risk_geometry.get("entry_price"),
        "market_context": {
            "benchmark_ticker": str((frozen_benchmark[-1] if frozen_benchmark else {}).get("ticker") or "000001.SH"),
            "state": _market_state(market_gate, candidate),
            "market_multiplier": candidate.get("market_multiplier"),
            "allowed_new_position": bool(candidate.get("allowed_new_position")),
            "gate_reason": (market_gate or {}).get("gate_reason"),
        },
        "sector_context": {
            "stage": (sector_context or {}).get("stage"),
            "stage_label": (sector_context or {}).get("stage_label"),
            "sector_state": candidate.get("sector_state"),
            "sector_mainline_score": candidate.get("sector_mainline_score"),
            "sector_retreat_flag": bool(candidate.get("sector_retreat_flag")),
        },
        "feature_snapshot": features,
        "operability_dimensions": dimensions,
        "common_gate": common_gate,
        "risk_geometry": risk_geometry,
        "templates": templates,
        "matched_template_ids": matched,
        "triggered_template_ids": triggered,
        "trade_eligible_template_ids": trade_eligible,
        "source_coverage": source_coverage,
        "candidate_facts": {
            "buy_point_type": candidate.get("buy_point_type"),
            "buy_point_pattern": candidate.get("buy_point_pattern"),
            "trade_timing_score": candidate.get("trade_timing_score"),
            "stock_quality_score": candidate.get("stock_quality_score"),
            "final_trade_score": candidate.get("final_trade_score"),
            "ranking_status": candidate.get("ranking_status"),
            "ranking_exclusion_reason": candidate.get("ranking_exclusion_reason"),
        },
        "outcome_horizons": list(OUTCOME_HORIZONS),
        "outcomes": {},
        "settled_horizons": [],
        "pending_horizons": list(OUTCOME_HORIZONS),
        "settlement_status": "pending",
        "ranking_effect": "none",
        "execution_effect": "none",
    }


def settle_structure_sample(
    sample: dict[str, Any],
    *,
    future_bars: list[dict[str, Any]],
    benchmark_future_bars: list[dict[str, Any]],
    settled_at: str,
) -> dict[str, Any]:
    analysis_date = _date_text(sample.get("analysis_date"))
    entry = _f(sample.get("entry_price"))
    stock_rows = sorted(
        [item for item in future_bars if _date_text(item.get("date") or item.get("trade_date")) > analysis_date],
        key=lambda item: _date_text(item.get("date") or item.get("trade_date")),
    )
    benchmark_rows = sorted(
        [item for item in benchmark_future_bars if _date_text(item.get("date") or item.get("trade_date")) > analysis_date],
        key=lambda item: _date_text(item.get("date") or item.get("trade_date")),
    )
    outcomes = dict(sample.get("outcomes") or {})
    settled: list[int] = []
    pending: list[int] = []
    for horizon in OUTCOME_HORIZONS:
        key = f"T{horizon}"
        if len(stock_rows) < horizon or entry <= 0:
            pending.append(horizon)
            continue
        window = stock_rows[:horizon]
        exit_bar = window[-1]
        exit_close = _f(exit_bar.get("close"))
        benchmark_return = None
        if len(benchmark_rows) >= horizon:
            benchmark_start = _f(sample.get("feature_snapshot", {}).get("benchmark_close"))
            if not benchmark_start:
                benchmark_start = _f((sample.get("feature_snapshot") or {}).get("benchmark_entry"))
            if benchmark_start:
                benchmark_return = _f(benchmark_rows[horizon - 1].get("close")) / benchmark_start - 1.0
        stock_return = exit_close / entry - 1.0 if exit_close else None
        template_outcomes: dict[str, Any] = {}
        for template in sample.get("templates") or []:
            invalidation = _f(template.get("invalidation_price"))
            invalidation_date = next(
                (
                    _date_text(item.get("date") or item.get("trade_date"))
                    for item in window
                    if invalidation and _f(item.get("low")) <= invalidation
                ),
                None,
            )
            template_outcomes[str(template.get("template_id"))] = {
                "invalidated": bool(invalidation_date),
                "invalidation_date": invalidation_date,
            }
        outcomes[key] = {
            "horizon_sessions": horizon,
            "exit_date": _date_text(exit_bar.get("date") or exit_bar.get("trade_date")),
            "exit_close": round(exit_close, 4),
            "return": round(stock_return, 6) if stock_return is not None else None,
            "benchmark_return": round(benchmark_return, 6) if benchmark_return is not None else None,
            "excess_return": round(stock_return - benchmark_return, 6) if stock_return is not None and benchmark_return is not None else None,
            "mfe": round(max(_f(item.get("high")) / entry - 1.0 for item in window), 6),
            "mae": round(min(_f(item.get("low")) / entry - 1.0 for item in window), 6),
            "template_outcomes": template_outcomes,
            "settled_at": settled_at,
        }
        settled.append(horizon)
    status = "complete" if not pending else "partial" if settled else "pending"
    return {
        "outcomes": outcomes,
        "settled_horizons": settled,
        "pending_horizons": pending,
        "settlement_status": status,
        "last_settled_at": settled_at if settled else sample.get("last_settled_at"),
    }


__all__ = [
    "OUTCOME_HORIZONS",
    "SAMPLE_VERSION",
    "TEMPLATE_VERSION",
    "build_feature_snapshot",
    "build_operability_dimensions",
    "build_risk_geometry",
    "build_structure_sample",
    "build_template_states",
    "settle_structure_sample",
]
