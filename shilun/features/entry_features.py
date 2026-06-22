from __future__ import annotations

import pandas as pd


def compute_entry_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute online trigger features using only the current and prior bars."""

    bars = df.copy()
    grouped = bars.groupby("ticker", group_keys=False)

    pullback_volume_shrink = (
        bars["pullback_volume_shrink_ratio"]
        if "pullback_volume_shrink_ratio" in bars.columns
        else grouped["volume"].transform(lambda s: s.rolling(5).mean() / s.rolling(20).mean())
    )
    efficiency_20 = (
        bars["efficiency_ratio_20"]
        if "efficiency_ratio_20" in bars.columns
        else pd.Series(0.0, index=bars.index, dtype="float64")
    )
    trend_r2_20 = (
        bars["trend_r2_20"]
        if "trend_r2_20" in bars.columns
        else pd.Series(0.0, index=bars.index, dtype="float64")
    )
    vol_pct_60 = (
        bars["vol_pct_60"]
        if "vol_pct_60" in bars.columns
        else grouped["volume"].transform(lambda s: s.rolling(60).apply(_rolling_percent_rank_last, raw=False))
    )

    prev_close = grouped["close"].shift(1)
    intraday_range = (bars["high"] - bars["low"]).replace(0, pd.NA)
    body_high = bars[["open", "close"]].max(axis=1)
    body_low = bars[["open", "close"]].min(axis=1)

    bars["close_near_high_pct"] = (bars["high"] - bars["close"]) / intraday_range
    bars["close_near_high_pct"] = _clip(bars["close_near_high_pct"].fillna(1.0), low=0.0, high=1.0)
    bars["close_near_high_inv"] = 1.0 - bars["close_near_high_pct"]
    bars["upper_shadow_ratio"] = _clip((bars["high"] - body_high) / intraday_range, low=0.0, high=1.0)
    bars["lower_shadow_ratio"] = _clip((body_low - bars["low"]) / intraday_range, low=0.0, high=1.0)
    bars["body_ratio"] = _clip((bars["close"] - bars["open"]).abs() / intraday_range, low=0.0, high=1.0)
    bars["body_ratio_inv"] = 1.0 - bars["body_ratio"]
    bars["gap_pct"] = bars["open"] / prev_close.replace(0, pd.NA) - 1.0

    volume_ma20 = grouped["volume"].transform(lambda s: s.rolling(20).mean())
    bars["volume_spike_ratio"] = bars["volume"] / volume_ma20.replace(0, pd.NA)
    bars["vol_pct_60"] = vol_pct_60

    bars["ma20_slope_5"] = grouped["ma20"].transform(lambda s: s.diff(5) / 5)
    bars["ma20_slope_delta"] = grouped["ma20_slope"].transform(lambda s: s.diff(5))
    bars["return_3d"] = grouped["close"].transform(lambda s: s.pct_change(3))
    bars["price_center_shift_5"] = grouped["close"].transform(
        lambda s: s.rolling(5).mean() / s.rolling(5).mean().shift(5) - 1.0
    )
    bars["price_center_shift_10"] = grouped["close"].transform(
        lambda s: s.rolling(10).mean() / s.rolling(10).mean().shift(10) - 1.0
    )
    bars["efficiency_ratio_20_inv"] = 1.0 - _clip(efficiency_20.fillna(0.0), low=0.0, high=1.0)

    bars["acceleration_score"] = _clip(
        0.35 * bars["return_1d"].fillna(0.0)
        + 0.25 * bars["return_3d"].fillna(0.0)
        + 0.20 * (bars["volume_spike_ratio"].fillna(1.0) - 1.0)
        + 0.15 * bars["ma20_slope_5"].fillna(0.0)
        - 0.10 * bars["upper_shadow_ratio"].fillna(0.0),
        low=-1.0,
        high=1.0,
    )

    bars["low_base_flag"] = (
        (bars["close_to_recent_high_20"].fillna(-1.0) <= -0.08)
        & (bars["close_to_ma20"].fillna(-1.0) >= -0.03)
    ).astype(int)
    bars["high_zone_flag"] = (
        (bars["close_to_recent_high_20"].fillna(-1.0) > -0.03)
        & (bars["return_20d"].fillna(0.0) > 0.12)
    ).astype(int)
    bars["breakout_recently_flag"] = (
        (bars["close_to_recent_high_20"].fillna(-1.0) > -0.03)
        & (bars["return_20d"].fillna(0.0).between(0.0, 0.12))
    ).astype(int)
    bars["trend_alignment_flag"] = (
        (bars["close_to_ma20"].fillna(-1.0) > 0.0)
        & (bars["ma20_slope"].fillna(0.0) > 0.0)
        & (bars.get("ma60_slope", pd.Series(0.0, index=bars.index)).fillna(0.0) >= 0.0)
    ).astype(int)
    bars["peak_shift_up_flag"] = (
        (bars["price_center_shift_5"].fillna(0.0) > 0.0)
        & (bars["ma20_slope_5"].fillna(0.0) > 0.0)
    ).astype(int)

    breakout_ready = (
        bars["close_to_recent_high_20"].fillna(-1.0) > -0.012
    ) & (
        bars["volume_spike_ratio"].fillna(0.0) >= 1.15
    ) & (
        bars["close_near_high_pct"].fillna(1.0) <= 0.35
    ) & (
        bars["body_ratio"].fillna(0.0) >= 0.42
    ) & (
        bars["return_1d"].fillna(0.0) >= 0
    )
    bars["breakout_confirm_flag"] = breakout_ready.astype(int)

    false_breakout = (
        bars["close_to_recent_high_20"].fillna(-1.0) > -0.02
    ) & (
        (bars["upper_shadow_ratio"].fillna(0.0) >= 0.55)
        | (bars["body_ratio"].fillna(1.0) <= 0.25)
        | (bars["return_1d"].fillna(0.0) < -0.02)
        | (bars["close"] < bars["open"])
    )
    bars["false_breakout_risk_flag"] = false_breakout.astype(int)

    gentle_expand_unit = _weighted_unit_sum(
        (
            0.30,
            _trap(
                bars["volume_spike_ratio"].fillna(1.0),
                1.00,
                1.10,
                1.80,
                2.30,
            ),
        ),
        (0.20, _trap(bars["vol_pct_60"].fillna(0.5), 0.55, 0.65, 0.90, 0.97)),
        (0.20, _trap(bars["close_near_high_inv"].fillna(0.0), 0.55, 0.70, 1.00, 1.00)),
        (0.15, _trap(bars["body_ratio"].fillna(0.0), 0.30, 0.45, 0.80, 0.95)),
        (0.15, _trap(efficiency_20.fillna(0.0), 0.20, 0.35, 0.70, 0.90)),
    ) - 0.15 * _trap(bars["upper_shadow_ratio"].fillna(0.0), 0.25, 0.40, 1.00, 1.00) - 0.15 * bars["high_zone_flag"].astype(
        float
    )
    bars["gentle_expand_score"] = _clip(gentle_expand_unit * 100.0, low=0.0, high=100.0)

    pullback_shrink_unit = _weighted_unit_sum(
        (0.30, _trap(pullback_volume_shrink.fillna(9.0), 0.45, 0.55, 0.85, 0.95)),
        (0.20, _trap(bars["close_to_ma20"].fillna(-1.0), -0.03, -0.01, 0.02, 0.05)),
        (0.20, _trap(efficiency_20.fillna(0.0), 0.25, 0.35, 0.75, 0.90)),
        (0.15, _trap(bars["lower_shadow_ratio"].fillna(0.0), 0.05, 0.15, 0.50, 0.70)),
        (0.15, _trap(bars["close_near_high_inv"].fillna(0.0), 0.40, 0.55, 1.00, 1.00)),
    )
    pullback_shrink_unit += 0.08 * bars["trend_alignment_flag"].astype(float)
    pullback_shrink_unit -= 0.08 * bars["high_zone_flag"].astype(float)
    bars["pullback_shrink_score"] = _clip(pullback_shrink_unit * 100.0, low=0.0, high=100.0)

    impulsive_spike_unit = _weighted_unit_sum(
        (0.40, _trap(bars["volume_spike_ratio"].fillna(1.0), 2.00, 2.50, 10.0, 10.0)),
        (0.20, _trap(bars["vol_pct_60"].fillna(0.5), 0.92, 0.97, 1.00, 1.00)),
        (0.20, _trap(bars["return_1d"].abs().fillna(0.0), 0.04, 0.07, 0.15, 0.20)),
        (0.20, _trap(bars["upper_shadow_ratio"].fillna(0.0), 0.20, 0.35, 1.00, 1.00)),
    )
    bars["impulsive_spike_score"] = _clip(impulsive_spike_unit * 100.0, low=0.0, high=100.0)

    distribution_unit = _weighted_unit_sum(
        (0.25, bars["high_zone_flag"].astype(float)),
        (0.25, _trap(bars["volume_spike_ratio"].fillna(1.0), 1.40, 1.80, 10.0, 10.0)),
        (0.20, _trap(bars["upper_shadow_ratio"].fillna(0.0), 0.30, 0.45, 1.00, 1.00)),
        (0.15, _trap(bars["body_ratio_inv"].fillna(0.0), 0.50, 0.65, 1.00, 1.00)),
        (0.15, _trap(bars["efficiency_ratio_20_inv"].fillna(1.0), 0.30, 0.45, 1.00, 1.00)),
    )
    bars["distribution_score"] = _clip(distribution_unit * 100.0, low=0.0, high=100.0)

    stall_unit = _weighted_unit_sum(
        (0.30, bars["high_zone_flag"].astype(float)),
        (0.20, _trap(bars["volume_spike_ratio"].fillna(1.0), 0.40, 0.55, 0.95, 1.05)),
        (0.20, _trap(bars["return_5d"].fillna(0.0), -0.01, 0.00, 0.02, 0.03)),
        (0.15, _trap(1.0 - _clip(bars["price_center_shift_5"].fillna(0.0) + 0.1, 0.0, 1.0), 0.0, 0.15, 0.60, 1.00)),
        (0.15, _trap(bars["efficiency_ratio_20_inv"].fillna(1.0), 0.20, 0.35, 1.00, 1.00)),
    )
    bars["stall_score"] = _clip(stall_unit * 100.0, low=0.0, high=100.0)

    bars["distribution_risk_flag"] = (
        (bars["distribution_score"] >= 58.0)
        | (
            (bars["close_to_recent_high_20"].fillna(-1.0) > -0.03)
            & (bars["volume_spike_ratio"].fillna(0.0) >= 1.6)
            & (
                (bars["upper_shadow_ratio"].fillna(0.0) >= 0.45)
                | ((bars["close"] < bars["open"]) & (bars["body_ratio"].fillna(0.0) >= 0.35))
            )
        )
    ).astype(int)

    bars["stall_risk_flag"] = (
        (bars["stall_score"] >= 58.0)
        | (
            (bars["close_to_recent_high_20"].fillna(-1.0) > -0.03)
            & (bars["volume_spike_ratio"].fillna(1.0) <= 0.95)
            & (bars["price_center_shift_5"].fillna(0.0) <= 0.0)
            & (bars["return_5d"].fillna(0.0) <= 0.02)
        )
    ).astype(int)

    bars["position_state"] = "transition"
    bars.loc[bars["low_base_flag"] == 1, "position_state"] = "low_base"
    bars.loc[
        (bars["close_to_ma20"].fillna(-1.0) > 0.0)
        & (bars["price_center_shift_10"].fillna(0.0) > 0.0),
        "position_state",
    ] = "rising"
    bars.loc[bars["high_zone_flag"] == 1, "position_state"] = "high_zone"
    bars.loc[
        (bars["close_to_ma20"].fillna(0.0) < -0.03)
        & (bars["price_center_shift_10"].fillna(0.0) < 0.0),
        "position_state",
    ] = "downtrend"

    bars["volume_pattern"] = "neutral"
    bars.loc[
        (bars["gentle_expand_score"] >= 60.0)
        & (bars["return_1d"].fillna(0.0) >= -0.005)
        & (bars["high_zone_flag"] == 0),
        "volume_pattern",
    ] = "gentle_expand"
    bars.loc[
        (bars["pullback_shrink_score"] >= 60.0)
        & (bars["return_1d"].fillna(0.0) <= 0.01)
        & (bars["close_to_ma20"].fillna(-1.0) >= -0.03),
        "volume_pattern",
    ] = "pullback_shrink"
    bars.loc[
        (bars["close_to_ma20"].fillna(0.0) < -0.03)
        & (bars["return_5d"].fillna(0.0) < 0.0)
        & (bars["volume_spike_ratio"].fillna(1.0) < 0.95),
        "volume_pattern",
    ] = "down_shrink"
    bars.loc[bars["impulsive_spike_score"] >= 60.0, "volume_pattern"] = "impulsive_spike"
    bars.loc[bars["stall_score"] >= 60.0, "volume_pattern"] = "high_level_stall"
    bars.loc[bars["distribution_score"] >= 58.0, "volume_pattern"] = "distribution"

    bars["acceptance_strength"] = _clip(
        0.34
        + 0.20 * bars["body_ratio"].fillna(0.0)
        + 0.18 * bars["close_near_high_inv"].fillna(0.0)
        + 0.10 * bars["lower_shadow_ratio"].fillna(0.0)
        + 0.10 * _clip((bars["volume_spike_ratio"].fillna(1.0) - 1.0) / 1.2, 0.0, 1.0)
        + 0.08 * efficiency_20.fillna(0.0)
        + 0.08 * _clip(bars["gentle_expand_score"].fillna(0.0) / 100.0, 0.0, 1.0)
        + 0.05 * _clip(bars["pullback_shrink_score"].fillna(0.0) / 100.0, 0.0, 1.0),
        low=0.0,
        high=1.0,
    )

    bars["trigger_strength"] = _clip(
        44.0
        + 18.0 * bars["breakout_confirm_flag"].fillna(0.0)
        + 12.0 * bars["acceptance_strength"].fillna(0.5)
        + 8.0 * _clip(bars["gentle_expand_score"].fillna(0.0) / 100.0, 0.0, 1.0)
        + 6.0 * _clip(bars["pullback_shrink_score"].fillna(0.0) / 100.0, 0.0, 1.0)
        - 18.0 * bars["false_breakout_risk_flag"].fillna(0.0)
        - 12.0 * _clip(bars["distribution_score"].fillna(0.0) / 100.0, 0.0, 1.0)
        - 10.0 * _clip(bars["stall_score"].fillna(0.0) / 100.0, 0.0, 1.0)
        + 6.0 * bars["acceleration_score"].fillna(0.0),
        low=0.0,
        high=100.0,
    )

    bars["trend_truth_score"] = _clip(
        42.0
        + 16.0 * efficiency_20.fillna(0.0)
        + 12.0 * trend_r2_20.fillna(0.0)
        + 10.0 * _clip(bars["gentle_expand_score"].fillna(0.0) / 100.0, 0.0, 1.0)
        + 12.0 * _clip(bars["pullback_shrink_score"].fillna(0.0) / 100.0, 0.0, 1.0)
        + 8.0 * bars["trend_alignment_flag"].fillna(0.0)
        - 14.0 * bars["false_breakout_risk_flag"].fillna(0.0)
        - 14.0 * _clip(bars["distribution_score"].fillna(0.0) / 100.0, 0.0, 1.0)
        - 12.0 * _clip(bars["stall_score"].fillna(0.0) / 100.0, 0.0, 1.0)
        - 8.0 * _clip(bars["impulsive_spike_score"].fillna(0.0) / 100.0, 0.0, 1.0),
        low=0.0,
        high=100.0,
    )

    bars["buy_readiness_score"] = _clip(
        0.30 * bars["trend_truth_score"].fillna(50.0)
        + 0.30 * bars["trigger_strength"].fillna(50.0)
        + 20.0 * bars["acceptance_strength"].fillna(0.5)
        + 0.10 * bars["gentle_expand_score"].fillna(0.0)
        + 0.10 * bars["pullback_shrink_score"].fillna(0.0)
        - 0.10 * bars["distribution_score"].fillna(0.0)
        - 0.10 * bars["stall_score"].fillna(0.0),
        low=0.0,
        high=100.0,
    )

    early_stage_unit = _weighted_unit_sum(
        (0.20, bars["low_base_flag"].astype(float)),
        (0.20, bars["breakout_recently_flag"].astype(float)),
        (0.15, _clip(bars["gentle_expand_score"].fillna(0.0) / 100.0, 0.0, 1.0)),
        (0.15, _trap(_clip(bars["ma20_slope_5"].fillna(0.0) * 120.0, 0.0, 1.0), 0.05, 0.15, 0.60, 0.90)),
        (0.10, _trap(bars["return_20d"].fillna(0.0), 0.00, 0.03, 0.10, 0.14)),
        (0.10, 1.0 - bars["high_zone_flag"].astype(float)),
        (0.10, bars["peak_shift_up_flag"].astype(float)),
    )
    bars["early_stage_score_base"] = _clip(early_stage_unit * 100.0, low=0.0, high=100.0)

    mid_stage_unit = _weighted_unit_sum(
        (0.20, bars["trend_alignment_flag"].astype(float)),
        (0.20, _clip(bars["pullback_shrink_score"].fillna(0.0) / 100.0, 0.0, 1.0)),
        (0.15, _clip(bars["gentle_expand_score"].fillna(0.0) / 100.0, 0.0, 1.0)),
        (0.15, _trap(efficiency_20.fillna(0.0), 0.35, 0.45, 0.80, 0.95)),
        (0.10, _trap(trend_r2_20.fillna(0.0), 0.45, 0.55, 0.90, 1.00)),
        (0.10, _trap(bars["close_to_ma20"].fillna(-1.0), 0.00, 0.02, 0.10, 0.20)),
        (0.10, 1.0 - _clip(bars["distribution_score"].fillna(0.0) / 100.0, 0.0, 1.0)),
    )
    bars["mid_stage_score_base"] = _clip(mid_stage_unit * 100.0, low=0.0, high=100.0)

    late_stage_unit = _weighted_unit_sum(
        (0.20, bars["high_zone_flag"].astype(float)),
        (0.20, _clip(bars["distribution_score"].fillna(0.0) / 100.0, 0.0, 1.0)),
        (0.15, _clip(bars["stall_score"].fillna(0.0) / 100.0, 0.0, 1.0)),
        (0.15, _clip(bars["impulsive_spike_score"].fillna(0.0) / 100.0, 0.0, 1.0)),
        (0.10, _trap(bars["return_20d"].fillna(0.0), 0.10, 0.18, 0.35, 0.60)),
        (0.10, _trap(bars["upper_shadow_ratio"].fillna(0.0), 0.25, 0.40, 1.00, 1.00)),
        (0.10, _trap(bars["efficiency_ratio_20_inv"].fillna(1.0), 0.20, 0.35, 1.00, 1.00)),
    )
    bars["late_stage_score_base"] = _clip(late_stage_unit * 100.0, low=0.0, high=100.0)
    return bars


def _clip(series: pd.Series | float, low: float, high: float) -> pd.Series | float:
    if isinstance(series, pd.Series):
        return series.clip(lower=low, upper=high)
    return max(low, min(high, float(series)))


def _trap(series: pd.Series, a: float, b: float, c: float, d: float) -> pd.Series:
    result = pd.Series(0.0, index=series.index, dtype="float64")
    values = series.astype("float64")
    if b > a:
        rising = (values > a) & (values < b)
        result.loc[rising] = (values.loc[rising] - a) / (b - a)
    plateau = (values >= b) & (values <= c)
    result.loc[plateau] = 1.0
    if d > c:
        falling = (values > c) & (values < d)
        result.loc[falling] = (d - values.loc[falling]) / (d - c)
    return result.clip(lower=0.0, upper=1.0).fillna(0.0)


def _weighted_unit_sum(*items: tuple[float, pd.Series]) -> pd.Series:
    base = None
    for weight, series in items:
        component = series.fillna(0.0) * float(weight)
        base = component if base is None else base + component
    if base is None:
        return pd.Series(dtype="float64")
    return base


def _rolling_percent_rank_last(window: pd.Series) -> float:
    ranked = pd.Series(window).rank(pct=True)
    if ranked.empty:
        return float("nan")
    return float(ranked.iloc[-1])
