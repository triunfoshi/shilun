from __future__ import annotations

import math
import sys
from typing import Any

import pandas as pd


def compute_trend_features(df: pd.DataFrame) -> pd.DataFrame:
    bars = df.copy()
    grouped = bars.groupby("ticker", group_keys=False)

    for window in (5, 10, 20, 60, 120):
        bars[f"ma{window}"] = grouped["close"].transform(lambda series: series.rolling(window).mean())

    bars["ma_slope"] = grouped["close"].transform(lambda series: series.rolling(20).mean().diff())
    bars["ma5_slope"] = grouped["ma5"].transform(lambda series: series.diff())
    bars["ma10_slope"] = grouped["ma10"].transform(lambda series: series.diff())
    bars["ma20_slope"] = grouped["ma20"].transform(lambda series: series.diff())
    bars["ma60_slope"] = grouped["ma60"].transform(lambda series: series.diff())
    bars["ma120_slope"] = grouped["ma120"].transform(lambda series: series.diff())

    bars["return_1d"] = grouped["close"].transform(lambda series: series.pct_change(1))
    bars["return_5d"] = grouped["close"].transform(lambda series: series.pct_change(5))
    bars["return_10d"] = grouped["close"].transform(lambda series: series.pct_change(10))
    bars["return_20d"] = grouped["close"].transform(lambda series: series.pct_change(20))
    bars["log_return_1d"] = grouped["close"].transform(lambda series: (series / series.shift(1)).map(_log_or_na))

    bars["close_to_ma20"] = bars["close"] / bars["ma20"] - 1
    bars["close_to_ma60"] = bars["close"] / bars["ma60"] - 1
    bars["ma5_above_ma10"] = (bars["ma5"] > bars["ma10"]).astype(int)
    bars["ma10_above_ma20"] = (bars["ma10"] > bars["ma20"]).astype(int)
    bars["ma20_above_ma60"] = (bars["ma20"] > bars["ma60"]).astype(int)
    bars["close_above_ma20"] = (bars["close"] > bars["ma20"]).astype(int)
    bars["close_above_ma60"] = (bars["close"] > bars["ma60"]).astype(int)

    for window in (5, 20, 60):
        bars[f"recent_high_{window}"] = grouped["high"].transform(lambda series: series.rolling(window).max())
        bars[f"recent_low_{window}"] = grouped["low"].transform(lambda series: series.rolling(window).min())
        bars[f"close_to_recent_high_{window}"] = bars["close"] / bars[f"recent_high_{window}"] - 1
        bars[f"close_to_recent_low_{window}"] = bars["close"] / bars[f"recent_low_{window}"] - 1
    bars["close_to_recent_high"] = bars["close_to_recent_high_20"]
    bars["close_to_recent_low"] = bars["close_to_recent_low_20"]

    price_std_20 = grouped["close"].transform(lambda series: series.rolling(20).std())
    price_std_60 = grouped["close"].transform(lambda series: series.rolling(60).std())
    bars["price_vs_ma20_z"] = (bars["close"] - bars["ma20"]) / price_std_20.replace(0, pd.NA)
    bars["price_vs_ma60_z"] = (bars["close"] - bars["ma60"]) / price_std_60.replace(0, pd.NA)
    bars["trend_r2_20"] = grouped["close"].transform(lambda series: _rolling_trend_r2(series, 20))
    bars["trend_r2_60"] = grouped["close"].transform(lambda series: _rolling_trend_r2(series, 60))
    bars["efficiency_ratio_10"] = grouped["close"].transform(lambda series: _efficiency_ratio(series, 10))
    bars["efficiency_ratio_20"] = grouped["close"].transform(lambda series: _efficiency_ratio(series, 20))
    bars["efficiency_ratio_60"] = grouped["close"].transform(lambda series: _efficiency_ratio(series, 60))
    return bars


def compute_volatility_features(df: pd.DataFrame) -> pd.DataFrame:
    bars = df.copy()
    grouped = bars.groupby("ticker", group_keys=False)
    prev_close = grouped["close"].shift(1)
    true_range = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - prev_close).abs(),
            (bars["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["tr"] = true_range
    bars["atr14"] = grouped["tr"].transform(lambda series: series.rolling(14).mean())
    bars["atr_14"] = bars["atr14"]
    bars["atr_pct"] = bars["atr14"] / bars["close"]
    bars["volatility_10d"] = grouped["close"].transform(lambda series: series.pct_change().rolling(10).std())
    bars["realized_vol_20"] = grouped["close"].transform(lambda series: series.pct_change().rolling(20).std())
    bars["range_ratio_10"] = grouped["close"].transform(lambda series: (series.rolling(10).max() - series.rolling(10).min()) / series.rolling(10).mean())
    bars["range_ratio_20"] = grouped["close"].transform(lambda series: (series.rolling(20).max() - series.rolling(20).min()) / series.rolling(20).mean())
    bars["volume_ma5"] = grouped["volume"].transform(lambda series: series.rolling(5).mean())
    bars["volume_ratio"] = bars["volume"] / bars["volume_ma5"]
    bars["volume_ma20"] = grouped["volume"].transform(lambda series: series.rolling(20).mean())
    bars["volume_ratio_20"] = bars["volume"] / bars["volume_ma20"]
    bars["vol_compression_ratio"] = bars["volatility_10d"] / bars["realized_vol_20"]
    if "amount" in bars.columns:
        bars["amount_ratio"] = grouped["amount"].transform(lambda series: series / series.rolling(5).mean())
    return bars


def compute_volume_features(df: pd.DataFrame) -> pd.DataFrame:
    bars = df.copy()
    grouped = bars.groupby("ticker", group_keys=False)

    direction = grouped["close"].diff().fillna(0.0)
    signed_volume = bars["volume"] * direction.map(lambda value: 1 if value > 0 else -1 if value < 0 else 0)
    bars["obv"] = signed_volume.groupby(bars["ticker"]).cumsum()
    bars["obv_slope_10"] = grouped["obv"].transform(lambda series: series.diff(10) / 10)

    typical_price = (bars["high"] + bars["low"] + bars["close"]) / 3
    bars["tpv"] = typical_price * bars["volume"]
    rolling_turnover = grouped["tpv"].transform(lambda series: series.rolling(20).sum())
    rolling_volume = grouped["volume"].transform(lambda series: series.rolling(20).sum())
    bars["vwap_20"] = rolling_turnover / rolling_volume.replace(0, pd.NA)
    bars["vwap_distance"] = bars["close"] / bars["vwap_20"] - 1
    bars["volume_ratio_5"] = bars["volume"] / grouped["volume"].transform(lambda series: series.rolling(5).mean())
    bars["vol_pct_60"] = grouped["volume"].transform(lambda series: series.rolling(60).apply(_rolling_percent_rank_last, raw=False))
    bars["breakout_volume_percentile"] = grouped["volume"].transform(lambda series: series.rolling(60).apply(_rolling_percent_rank_last, raw=False))
    bars["pullback_volume_shrink_ratio"] = grouped["volume"].transform(lambda series: series.rolling(5).mean() / series.rolling(20).mean())
    return bars.drop(columns=["tpv"])


def build_chip_context(
    *,
    latest_close: float | None,
    support_main: float | None,
    pressure_main: float | None,
    daily_basic: dict[str, Any] | None = None,
    chip_perf: dict[str, Any] | None = None,
) -> dict[str, Any]:
    daily_basic = daily_basic or {}
    chip_perf = chip_perf or {}

    turnover_rate = _as_float(daily_basic.get("turnover_rate_f")) or _as_float(daily_basic.get("turnover_rate")) or 0.0
    free_share = _as_float(daily_basic.get("free_share")) or _as_float(daily_basic.get("float_share"))
    total_mv = _as_float(daily_basic.get("total_mv"))
    circ_mv = _as_float(daily_basic.get("circ_mv"))

    avg_cost = _first_float(chip_perf, ["weight_avg", "avg_cost", "cost_50"])
    cost_15 = _first_float(chip_perf, ["cost_15"])
    cost_50 = _first_float(chip_perf, ["cost_50"])
    cost_85 = _first_float(chip_perf, ["cost_85"])
    cost_95 = _first_float(chip_perf, ["cost_95"])
    winner_rate = _first_float(chip_perf, ["winner_rate"])
    winner_ratio = _first_float(chip_perf, ["winner_ratio", "winner_rate"])
    peak_shift_5d = _first_float(chip_perf, ["peak_shift_5d", "cost_shift_5d"])

    if avg_cost is None and latest_close is not None and support_main is not None and pressure_main is not None:
        avg_cost = (support_main + pressure_main) / 2.0

    overhang_ratio = support_density = pressure_density = cost_band_width = None
    vacuum_up_ratio = chip_concentration = cost_distance_50 = cost_distance_85 = None
    if latest_close is not None:
        upper_cost = cost_85 or cost_95 or pressure_main
        lower_cost = cost_50 or cost_15 or support_main
        if upper_cost is not None:
            overhang_ratio = max(0.0, upper_cost / latest_close - 1.0)
        if lower_cost is not None and latest_close != 0:
            support_density = max(0.0, 1.0 - abs(latest_close - lower_cost) / latest_close)
        if upper_cost is not None and latest_close != 0:
            pressure_density = max(0.0, 1.0 - abs(upper_cost - latest_close) / latest_close)
        if upper_cost is not None and lower_cost is not None and latest_close != 0:
            cost_band_width = abs(upper_cost - lower_cost) / latest_close
        if cost_50 is not None and latest_close != 0:
            cost_distance_50 = abs(latest_close - cost_50) / latest_close
        if cost_85 is not None and latest_close != 0:
            cost_distance_85 = abs(cost_85 - latest_close) / latest_close
        if pressure_density is not None:
            vacuum_up_ratio = max(0.0, 1.0 - pressure_density)
        if cost_band_width is not None:
            chip_concentration = max(0.0, min(1.0, 1.0 - cost_band_width / 0.25))

    if winner_ratio is None:
        winner_ratio = winner_rate
    if winner_ratio is None and cost_50 is not None and latest_close is not None and latest_close != 0:
        winner_ratio = max(0.0, min(1.0, 0.5 + (latest_close - cost_50) / (latest_close * 0.5)))

    source = "cyq_perf" if chip_perf else "approx_daily_basic" if daily_basic else "structure_proxy"
    return {
        "chip_source": source,
        "turnover_rate_f": turnover_rate,
        "free_share": free_share,
        "total_mv": total_mv,
        "circ_mv": circ_mv,
        "avg_cost": avg_cost,
        "cost_15": cost_15,
        "cost_50": cost_50,
        "cost_85": cost_85,
        "cost_95": cost_95,
        "winner_rate": winner_rate,
        "winner_ratio": winner_ratio,
        "overhang_ratio": overhang_ratio,
        "support_density": support_density,
        "pressure_density": pressure_density,
        "cost_band_width": cost_band_width,
        "vacuum_up_ratio": vacuum_up_ratio,
        "chip_concentration": chip_concentration,
        "cost_distance_50": cost_distance_50,
        "cost_distance_85": cost_distance_85,
        "peak_shift_5d": peak_shift_5d,
        "support_peak_strength": support_density,
        "pressure_peak_strength": pressure_density,
    }


def _rolling_trend_r2(series: pd.Series, window: int) -> pd.Series:
    def calc(values: pd.Series) -> float:
        if values.isna().any():
            return float("nan")
        x = pd.Series(range(len(values)), dtype="float64")
        y = values.astype("float64").reset_index(drop=True)
        x_mean = float(x.mean())
        y_mean = float(y.mean())
        cov = float(((x - x_mean) * (y - y_mean)).sum())
        var_x = float(((x - x_mean) ** 2).sum())
        var_y = float(((y - y_mean) ** 2).sum())
        if var_x == 0 or var_y == 0:
            return float("nan")
        corr = cov / (var_x**0.5 * var_y**0.5)
        return corr * corr

    return series.rolling(window).apply(calc, raw=False)


def _efficiency_ratio(series: pd.Series, window: int) -> pd.Series:
    direction = series.diff(window).abs()
    volatility = series.diff().abs().rolling(window).sum()
    return direction / volatility.replace(0, pd.NA)


def _rolling_percent_rank_last(window: pd.Series) -> float:
    ranked = pd.Series(window).rank(pct=True)
    if ranked.empty:
        return float("nan")
    return float(ranked.iloc[-1])


def _first_float(payload: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = _as_float(payload.get(key))
        if value is not None:
            return value
    return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _log_or_na(value: Any) -> float | pd._libs.missing.NAType:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric) or numeric <= 0:
        return pd.NA
    return math.log(float(numeric))


__all__ = [
    "build_chip_context",
    "compute_trend_features",
    "compute_volatility_features",
    "compute_volume_features",
]


for _module_name in ("chips", "trend", "volatility", "volume"):
    sys.modules[f"{__name__}.{_module_name}"] = sys.modules[__name__]
