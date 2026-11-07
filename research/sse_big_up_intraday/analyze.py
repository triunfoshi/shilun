#!/usr/bin/env python3
"""Analyze the intraday shape of SSE Composite big-up days.

The primary event definition is deliberately explicit:

    close / previous_close - 1 >= 1%, and close > open.

KlineShare v2 supplies 239 one-minute bars (09:31-14:59) for the index.  The
official daily close from the same response family is appended as the 15:00
anchor, so every eligible day has 240 continuous-auction minute closes plus
the 09:30 opening anchor used for path calculations.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from shilun.common.config import load_config
from shilun.data.providers.klineshare_provider import (
    KlineShareClient,
    KlineShareConfig,
    KlineShareError,
)


ROOT = Path(__file__).resolve().parents[2]
RESEARCH_DIR = Path(__file__).resolve().parent
MARKET_TZ = ZoneInfo("Asia/Shanghai")
SYMBOL_API = "000001.SS"
SYMBOL_INTERNAL = "000001.SH"
PRIMARY_THRESHOLD = 0.01
SENSITIVITY_THRESHOLDS = (0.008, 0.01, 0.0125, 0.015, 0.02)
EXPECTED_MINUTES = (
    pd.date_range("09:31", "11:30", freq="1min").strftime("%H:%M").tolist()
    + pd.date_range("13:01", "15:00", freq="1min").strftime("%H:%M").tolist()
)


@dataclass(frozen=True)
class AnalysisWindow:
    start: str
    end: str
    as_of: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-of",
        default=datetime.now(MARKET_TZ).date().isoformat(),
        help="Market-date cutoff in YYYY-MM-DD (default: today in Asia/Shanghai).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory. Default is derived from the completed data window.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore cached KlineShare responses and fetch them again.",
    )
    return parser.parse_args()


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if pd.isna(value):
        return None
    raise TypeError(f"Cannot JSON-encode {type(value)!r}")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def fetch_cached(
    client: KlineShareClient,
    cache_path: Path,
    *,
    params: dict[str, Any],
    refresh: bool,
) -> dict[str, Any]:
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            payload = client._request("/v2/index/kline", params=params)
            write_json(cache_path, payload)
            return payload
        except KlineShareError as error:
            last_error = error
            if attempt < 3:
                time.sleep(attempt)
    assert last_error is not None
    raise last_error


def compact_payload_frame(payload: dict[str, Any], symbol: str = SYMBOL_API) -> pd.DataFrame:
    fields = payload.get("fields")
    symbols = payload.get("symbols")
    if not isinstance(fields, list) or not isinstance(symbols, dict):
        raise ValueError("Unexpected KlineShare v2 response: fields/symbols missing")
    values = symbols.get(symbol)
    if not isinstance(values, list):
        raise ValueError(f"KlineShare v2 response does not contain {symbol}")
    frame = pd.DataFrame(values, columns=fields)
    if frame.empty:
        return frame
    frame["date"] = frame["date"].astype(str)
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def build_window(daily: pd.DataFrame, as_of: str) -> AnalysisWindow:
    cutoff = pd.Timestamp(as_of).strftime("%Y%m%d")
    eligible = daily.loc[daily["date"] <= cutoff]
    if eligible.empty:
        raise ValueError(f"No daily bar on or before {as_of}")
    end = pd.Timestamp(eligible["date"].max())
    # Inclusive trailing three-calendar-month window: Jun 5-Sep 4, for example.
    start = end - pd.DateOffset(months=3) + pd.Timedelta(days=1)
    return AnalysisWindow(
        start=start.strftime("%Y%m%d"),
        end=end.strftime("%Y%m%d"),
        as_of=pd.Timestamp(as_of).strftime("%Y-%m-%d"),
    )


def enrich_daily(daily: pd.DataFrame) -> pd.DataFrame:
    result = daily.sort_values("date").drop_duplicates("date", keep="last").copy()
    result["prev_close"] = result["close"].shift(1)
    result["daily_return"] = result["close"] / result["prev_close"] - 1
    result["body_return"] = result["close"] / result["open"] - 1
    result["gap_return"] = result["open"] / result["prev_close"] - 1
    result["amplitude"] = result["high"] / result["low"] - 1
    result["close_location"] = np.where(
        result["high"] > result["low"],
        (result["close"] - result["low"]) / (result["high"] - result["low"]),
        np.nan,
    )
    return result


def expected_time_index() -> dict[str, int]:
    return {label: index for index, label in enumerate(EXPECTED_MINUTES, start=1)}


def parse_minute_frame(payload: dict[str, Any], day: pd.Series) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = compact_payload_frame(payload)
    if frame.empty:
        raise ValueError(f"No minute bars for {day['date']}")
    local_times = pd.to_datetime(frame["timestamp"], unit="ms", utc=True).dt.tz_convert(MARKET_TZ)
    frame["minute_label"] = local_times.dt.strftime("%H:%M")
    frame = frame.loc[frame["minute_label"].isin(EXPECTED_MINUTES)].copy()
    frame = frame.drop_duplicates("minute_label", keep="last")
    time_to_index = expected_time_index()
    frame["minute_index"] = frame["minute_label"].map(time_to_index)
    frame = frame.sort_values("minute_index")

    expected_without_close = set(EXPECTED_MINUTES[:-1])
    actual = set(frame["minute_label"])
    missing = sorted(expected_without_close - actual)
    unexpected = sorted(actual - set(EXPECTED_MINUTES))
    if missing:
        raise ValueError(f"{day['date']} is missing continuous-auction minutes: {missing[:8]}")

    provider_has_1500 = "15:00" in actual
    pre_anchor_close = float(frame.iloc[-1]["close"])
    frame = frame.loc[frame["minute_label"] != "15:00"].copy()
    anchor = {
        "date": str(day["date"]),
        "timestamp": int(
            datetime.strptime(f"{day['date']} 15:00", "%Y%m%d %H:%M")
            .replace(tzinfo=MARKET_TZ)
            .timestamp()
            * 1000
        ),
        "open": float(day["close"]),
        "high": float(day["close"]),
        "low": float(day["close"]),
        "close": float(day["close"]),
        "volume": np.nan,
        "turnover": np.nan,
        "minute_label": "15:00",
        "minute_index": 240,
        "point_source": "same_source_daily_close_anchor",
    }
    frame["point_source"] = "klineshare_v2_1m"
    frame = pd.concat([frame, pd.DataFrame([anchor])], ignore_index=True, sort=False)
    frame = frame.sort_values("minute_index").reset_index(drop=True)
    if len(frame) != 240 or frame["minute_index"].nunique() != 240:
        raise ValueError(f"{day['date']} did not normalize to 240 minutes")
    quality = {
        "trade_date": str(day["date"]),
        "raw_minute_count": int(payload.get("meta", {}).get("count", len(frame) - 1)),
        "normalized_minute_count": int(len(frame)),
        "missing_non_close_minutes": int(len(missing)),
        "unexpected_minutes": int(len(unexpected)),
        "provider_had_1500": bool(provider_has_1500),
        "last_raw_minute": "15:00" if provider_has_1500 else str(frame.iloc[-2]["minute_label"]),
        "pre_anchor_vs_close_bp": (pre_anchor_close / float(day["close"]) - 1) * 10_000,
    }
    return frame, quality


def _first_hit(path: pd.DataFrame, target: float) -> tuple[int | None, str | None]:
    hit = path.loc[(path["minute_index"] > 0) & (path["normalized_body"] >= target)]
    if hit.empty:
        return None, None
    row = hit.iloc[0]
    return int(row["minute_index"]), str(row["minute_label"])


def _down_crosses(values: np.ndarray, level: float) -> int:
    if len(values) < 2:
        return 0
    return int(np.sum((values[:-1] >= level) & (values[1:] < level)))


def build_path_and_metrics(day: pd.Series, minute: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    opening = {
        "date": str(day["date"]),
        "timestamp": int(
            datetime.strptime(f"{day['date']} 09:30", "%Y%m%d %H:%M")
            .replace(tzinfo=MARKET_TZ)
            .timestamp()
            * 1000
        ),
        "open": float(day["open"]),
        "high": float(day["open"]),
        "low": float(day["open"]),
        "close": float(day["open"]),
        "volume": np.nan,
        "turnover": np.nan,
        "minute_label": "09:30",
        "minute_index": 0,
        "point_source": "same_source_daily_open_anchor",
    }
    path = pd.concat([pd.DataFrame([opening]), minute], ignore_index=True, sort=False)
    path["trade_date"] = str(day["date"])
    path["prev_close"] = float(day["prev_close"])
    path["daily_open"] = float(day["open"])
    path["daily_close"] = float(day["close"])
    path["return_vs_prev_close"] = path["close"] / float(day["prev_close"]) - 1
    path["return_from_open"] = path["close"] / float(day["open"]) - 1
    body_return = float(day["body_return"])
    path["normalized_body"] = path["return_from_open"] / body_return

    indexed = path.set_index("minute_index")
    close_values = path["close"].to_numpy(dtype=float)
    changes = np.diff(close_values)
    running_peak = np.maximum.accumulate(close_values)
    drawdowns = close_values / running_peak - 1
    hit50_index, hit50_time = _first_hit(path, 0.5)
    hit80_index, hit80_time = _first_hit(path, 0.8)

    def ret_at(index: int) -> float:
        return float(indexed.loc[index, "return_from_open"])

    segment_contributions: list[float] = []
    for end_index in range(40, 241, 40):
        start_index = end_index - 40
        segment_contributions.append((ret_at(end_index) - ret_at(start_index)) / body_return)

    minute_only = path.loc[path["minute_index"] > 0]
    peak_row = minute_only.loc[minute_only["high"].idxmax()]
    trough_row = minute_only.loc[minute_only["low"].idxmin()]
    metrics: dict[str, Any] = {
        "trade_date": str(day["date"]),
        "daily_return": float(day["daily_return"]),
        "body_return": body_return,
        "gap_return": float(day["gap_return"]),
        "amplitude": float(day["amplitude"]),
        "close_location": float(day["close_location"]),
        "first_5_return": ret_at(5),
        "first_15_return": ret_at(15),
        "first_30_return": ret_at(30),
        "first_60_return": ret_at(60),
        "first_5_contribution": ret_at(5) / body_return,
        "first_15_contribution": ret_at(15) / body_return,
        "first_30_contribution": ret_at(30) / body_return,
        "first_60_contribution": ret_at(60) / body_return,
        "morning_return": ret_at(120),
        "morning_contribution": ret_at(120) / body_return,
        "afternoon_contribution": (ret_at(240) - ret_at(120)) / body_return,
        "last_30_return": ret_at(240) - ret_at(210),
        "last_30_contribution": (ret_at(240) - ret_at(210)) / body_return,
        "hit_50_index": hit50_index,
        "hit_50_time": hit50_time,
        "hit_80_index": hit80_index,
        "hit_80_time": hit80_time,
        "peak_time": str(peak_row["minute_label"]),
        "peak_index": int(peak_row["minute_index"]),
        "trough_time": str(trough_row["minute_label"]),
        "trough_index": int(trough_row["minute_index"]),
        "max_drawdown": float(-drawdowns.min()),
        "drawdown_to_body": float(-drawdowns.min() / body_return),
        "path_efficiency": float(abs(close_values[-1] - close_values[0]) / np.abs(changes).sum()),
        "up_minute_share": float(np.mean(changes > 0)),
        "above_open_share": float(np.mean(minute_only["close"].to_numpy() >= float(day["open"]))),
        "above_prev_close_share": float(
            np.mean(minute_only["close"].to_numpy() >= float(day["prev_close"]))
        ),
        "drop_below_open_count": _down_crosses(close_values, float(day["open"])),
        "drop_below_prev_close_count": _down_crosses(close_values, float(day["prev_close"])),
    }
    for number, contribution in enumerate(segment_contributions, start=1):
        metrics[f"segment_{number}_contribution"] = float(contribution)
    if metrics["afternoon_contribution"] < 0:
        metrics["path_type"] = "早强后回落"
    elif metrics["morning_contribution"] >= 0.5:
        metrics["path_type"] = "上午建立优势、午后续涨"
    else:
        metrics["path_type"] = "午后主导"
    keep_columns = [
        "trade_date",
        "minute_index",
        "minute_label",
        "close",
        "return_vs_prev_close",
        "return_from_open",
        "normalized_body",
        "point_source",
    ]
    return path[keep_columns].copy(), metrics


def pairwise_path_correlation(paths: pd.DataFrame, dates: Iterable[str]) -> float | None:
    selected = paths.loc[paths["trade_date"].isin(list(dates))]
    pivot = selected.pivot(index="minute_index", columns="trade_date", values="normalized_body")
    if pivot.shape[1] < 2:
        return None
    corr = pivot.corr().to_numpy(dtype=float)
    values = corr[np.triu_indices_from(corr, k=1)]
    values = values[np.isfinite(values)]
    return float(np.median(values)) if len(values) else None


def summarize_subset(
    metrics: pd.DataFrame,
    paths: pd.DataFrame,
    dates: list[str],
) -> dict[str, Any]:
    subset = metrics.loc[metrics["trade_date"].isin(dates)]
    if subset.empty:
        return {
            "sample_count": 0,
            "median_daily_return": None,
            "median_body_return": None,
            "median_gap_return": None,
            "median_first_30_contribution": None,
            "median_morning_contribution": None,
            "median_last_30_contribution": None,
            "median_close_location": None,
            "median_max_drawdown": None,
            "median_above_open_share": None,
            "median_pairwise_path_correlation": None,
            "close_high_share": None,
            "tail_positive_share": None,
            "trough_in_morning_share": None,
            "peak_after_1445_share": None,
            "afternoon_dominant_share": None,
        }
    return {
        "sample_count": int(len(subset)),
        "median_daily_return": float(subset["daily_return"].median()),
        "median_body_return": float(subset["body_return"].median()),
        "median_gap_return": float(subset["gap_return"].median()),
        "median_first_30_contribution": float(subset["first_30_contribution"].median()),
        "median_morning_contribution": float(subset["morning_contribution"].median()),
        "median_last_30_contribution": float(subset["last_30_contribution"].median()),
        "median_close_location": float(subset["close_location"].median()),
        "median_max_drawdown": float(subset["max_drawdown"].median()),
        "median_above_open_share": float(subset["above_open_share"].median()),
        "median_pairwise_path_correlation": pairwise_path_correlation(paths, dates),
        "close_high_share": float((subset["close_location"] >= 0.7).mean()),
        "tail_positive_share": float((subset["last_30_return"] > 0).mean()),
        "trough_in_morning_share": float((subset["trough_index"] <= 120).mean()),
        "peak_after_1445_share": float((subset["peak_index"] >= 225).mean()),
        "afternoon_dominant_share": float((subset["afternoon_contribution"] >= 0.5).mean()),
    }


def build_sensitivity(
    period_daily: pd.DataFrame,
    metrics: pd.DataFrame,
    paths: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for threshold in SENSITIVITY_THRESHOLDS:
        mask = (period_daily["daily_return"] >= threshold) & (period_daily["body_return"] > 0)
        dates = period_daily.loc[mask, "date"].astype(str).tolist()
        summary = summarize_subset(metrics, paths, dates)
        rows.append(
            {
                "threshold": threshold,
                "threshold_label": f"较前收≥{threshold:.2%}且收阳",
                "event_dates": "、".join(dates),
                **summary,
            }
        )
    return pd.DataFrame(rows)


def build_definition_comparison(
    period_daily: pd.DataFrame,
    metrics: pd.DataFrame,
    paths: pd.DataFrame,
) -> pd.DataFrame:
    definitions = [
        (
            "主口径",
            "较前收≥1%且收盘高于开盘",
            (period_daily["daily_return"] >= 0.01) & (period_daily["body_return"] > 0),
        ),
        (
            "实体口径",
            "收盘较开盘≥1%",
            period_daily["body_return"] >= 0.01,
        ),
        (
            "双重严格口径",
            "较前收≥1%且收盘较开盘≥1%",
            (period_daily["daily_return"] >= 0.01) & (period_daily["body_return"] >= 0.01),
        ),
    ]
    rows: list[dict[str, Any]] = []
    for name, rule, mask in definitions:
        dates = period_daily.loc[mask, "date"].astype(str).tolist()
        rows.append(
            {
                "definition": name,
                "rule": rule,
                "event_dates": "、".join(dates),
                **summarize_subset(metrics, paths, dates),
            }
        )
    return pd.DataFrame(rows)


def build_common_features(metrics: pd.DataFrame) -> pd.DataFrame:
    tests: list[tuple[str, str, pd.Series]] = [
        ("开盘即高于前收", "gap_return > 0", metrics["gap_return"] > 0),
        ("前30分钟盘中涨幅为正", "first_30_return > 0", metrics["first_30_return"] > 0),
        ("上午完成至少一半阳线实体", "morning_contribution >= 0.5", metrics["morning_contribution"] >= 0.5),
        ("至少70%分钟站在开盘价上方", "above_open_share >= 0.7", metrics["above_open_share"] >= 0.7),
        ("收在全天振幅上部30%", "close_location >= 0.7", metrics["close_location"] >= 0.7),
        ("最后30分钟继续贡献正收益", "last_30_return > 0", metrics["last_30_return"] > 0),
        ("全天低点出现在上午", "trough_index <= 120", metrics["trough_index"] <= 120),
        ("全天高点出现在14:45以后", "peak_index >= 225", metrics["peak_index"] >= 225),
        ("日内最大回撤不超过0.5%", "max_drawdown <= 0.005", metrics["max_drawdown"] <= 0.005),
        ("14:00前曾达到最终实体80%", "hit_80_index <= 180", metrics["hit_80_index"] <= 180),
    ]
    rows: list[dict[str, Any]] = []
    n = len(metrics)
    for feature, rule, mask in tests:
        count = int(mask.fillna(False).sum())
        rows.append(
            {
                "feature": feature,
                "rule": rule,
                "matching_days": count,
                "sample_count": n,
                "share": count / n if n else None,
                "meets_70pct_rule": bool(n and count / n >= 0.7),
            }
        )
    return pd.DataFrame(rows)


def leave_one_out_checks(metrics: pd.DataFrame) -> pd.DataFrame:
    checks = [
        ("前30分钟中位贡献为正", "first_30_contribution", 0.0, "ge"),
        ("上午中位贡献至少一半", "morning_contribution", 0.5, "ge"),
        ("收盘位置中位数位于上部30%", "close_location", 0.7, "ge"),
        ("站上开盘价的分钟占比中位数至少70%", "above_open_share", 0.7, "ge"),
        ("中位最大回撤不超过0.5%", "max_drawdown", 0.005, "le"),
    ]
    rows: list[dict[str, Any]] = []
    for label, field, threshold, direction in checks:
        medians = []
        for excluded in metrics["trade_date"]:
            value = float(metrics.loc[metrics["trade_date"] != excluded, field].median())
            medians.append(value)
        passes = [value >= threshold if direction == "ge" else value <= threshold for value in medians]
        rows.append(
            {
                "check": label,
                "field": field,
                "threshold": threshold,
                "direction": direction,
                "full_sample_median": float(metrics[field].median()),
                "loo_min_median": min(medians),
                "loo_max_median": max(medians),
                "stable_under_leave_one_out": bool(all(passes)),
            }
        )
    return pd.DataFrame(rows)


def build_aggregate_paths(paths: pd.DataFrame, dates: list[str]) -> pd.DataFrame:
    subset = paths.loc[paths["trade_date"].isin(dates)].copy()
    aggregate = (
        subset.groupby(["minute_index", "minute_label"], as_index=False)
        .agg(
            return_vs_prev_close_q25=("return_vs_prev_close", lambda x: x.quantile(0.25)),
            return_vs_prev_close_median=("return_vs_prev_close", "median"),
            return_vs_prev_close_q75=("return_vs_prev_close", lambda x: x.quantile(0.75)),
            return_from_open_q25=("return_from_open", lambda x: x.quantile(0.25)),
            return_from_open_median=("return_from_open", "median"),
            return_from_open_q75=("return_from_open", lambda x: x.quantile(0.75)),
            normalized_body_q25=("normalized_body", lambda x: x.quantile(0.25)),
            normalized_body_median=("normalized_body", "median"),
            normalized_body_q75=("normalized_body", lambda x: x.quantile(0.75)),
            sample_count=("trade_date", "nunique"),
        )
        .sort_values("minute_index")
    )
    return aggregate


def chart_datasets(paths: pd.DataFrame, aggregate: pd.DataFrame, dates: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    anchors = {0, *range(5, 241, 5)}
    individual = paths.loc[
        paths["trade_date"].isin(dates) & paths["minute_index"].isin(anchors),
        ["trade_date", "minute_index", "minute_label", "return_vs_prev_close"],
    ].copy()
    aggregate_wide = aggregate.loc[aggregate["minute_index"].isin(anchors)].copy()
    aggregate_long = aggregate_wide.melt(
        id_vars=["minute_index", "minute_label", "sample_count"],
        value_vars=["normalized_body_q25", "normalized_body_median", "normalized_body_q75"],
        var_name="statistic",
        value_name="normalized_body",
    )
    labels = {
        "normalized_body_q25": "25%分位",
        "normalized_body_median": "中位数",
        "normalized_body_q75": "75%分位",
    }
    aggregate_long["statistic"] = aggregate_long["statistic"].map(labels)
    return individual.sort_values(["trade_date", "minute_index"]), aggregate_long.sort_values(
        ["statistic", "minute_index"]
    )


def validate_against_local_mongo(daily: pd.DataFrame, window: AnalysisWindow) -> tuple[pd.DataFrame, dict[str, Any]]:
    config = load_config()
    empty = pd.DataFrame(
        columns=[
            "trade_date",
            "field",
            "klineshare_value",
            "local_tushare_value",
            "absolute_difference",
            "relative_difference_bp",
        ]
    )
    if not config.mongo_uri:
        return empty, {"status": "not_configured"}
    try:
        from pymongo import MongoClient

        client = MongoClient(config.mongo_uri, serverSelectionTimeoutMS=3000)
        docs = list(
            client[config.mongo_db]["market_daily_bars"].find(
                {
                    "ticker": SYMBOL_INTERNAL,
                    "source": "tushare_index_daily",
                    "date": {"$gte": pd.Timestamp(window.start).strftime("%Y-%m-%d"), "$lte": pd.Timestamp(window.end).strftime("%Y-%m-%d")},
                },
                {"_id": 0, "date": 1, "open": 1, "high": 1, "low": 1, "close": 1},
            )
        )
    except Exception as error:  # Data quality output should survive a missing local daemon.
        return empty, {"status": "unavailable", "error": str(error)}
    local = pd.DataFrame(docs)
    if local.empty:
        return empty, {"status": "no_overlap"}
    local["trade_date"] = local["date"].str.replace("-", "", regex=False)
    remote = daily.loc[(daily["date"] >= window.start) & (daily["date"] <= window.end)].copy()
    merged = remote.merge(local, left_on="date", right_on="trade_date", suffixes=("_ks", "_local"))
    rows: list[dict[str, Any]] = []
    for _, item in merged.iterrows():
        for field in ("open", "high", "low", "close"):
            ks_value = float(item[f"{field}_ks"])
            local_value = float(item[f"{field}_local"])
            rows.append(
                {
                    "trade_date": str(item["date_ks"]),
                    "field": field,
                    "klineshare_value": ks_value,
                    "local_tushare_value": local_value,
                    "absolute_difference": abs(ks_value - local_value),
                    "relative_difference_bp": abs(ks_value / local_value - 1) * 10_000,
                }
            )
    comparison = pd.DataFrame(rows)
    summary = {
        "status": "ok",
        "overlap_trade_days": int(merged["date_ks"].nunique()),
        "local_first_date": str(local["trade_date"].min()),
        "local_last_date": str(local["trade_date"].max()),
        "max_absolute_difference_points": float(comparison["absolute_difference"].max()),
        "median_absolute_difference_points": float(comparison["absolute_difference"].median()),
        "max_relative_difference_bp": float(comparison["relative_difference_bp"].max()),
    }
    return comparison, summary


def save_outputs(output_dir: Path, frames: dict[str, pd.DataFrame], summary: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        frame.to_csv(output_dir / f"{name}.csv", index=False, encoding="utf-8-sig")
    database = output_dir / "analysis.sqlite"
    with sqlite3.connect(database) as connection:
        for name, frame in frames.items():
            frame.to_sql(name, connection, if_exists="replace", index=False)
    write_json(output_dir / "analysis_summary.json", summary)


def main() -> int:
    args = parse_args()
    config = load_config()
    client = KlineShareClient(
        KlineShareConfig(
            api_key=config.klineshare_api_key,
            base_url=config.klineshare_base_url,
            timeout=config.klineshare_timeout,
        )
    )

    provisional_cache = ROOT / "output" / "sse_big_up_intraday_cache"
    daily_payload = fetch_cached(
        client,
        provisional_cache / "daily_180.json",
        params={"symbol": SYMBOL_API, "period": 86400, "count": 180},
        refresh=args.refresh,
    )
    daily = enrich_daily(compact_payload_frame(daily_payload))
    window = build_window(daily, args.as_of)
    output_dir = args.output_dir or ROOT / "output" / f"sse_big_up_intraday_{window.start}_{window.end}"
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    write_json(raw_dir / "daily_180.json", daily_payload)

    period_daily = daily.loc[(daily["date"] >= window.start) & (daily["date"] <= window.end)].copy()
    primary_mask = (period_daily["daily_return"] >= PRIMARY_THRESHOLD) & (period_daily["body_return"] > 0)
    primary_dates = period_daily.loc[primary_mask, "date"].astype(str).tolist()
    body_dates = period_daily.loc[period_daily["body_return"] >= 0.01, "date"].astype(str).tolist()
    loose_dates = period_daily.loc[
        (period_daily["daily_return"] >= min(SENSITIVITY_THRESHOLDS)) & (period_daily["body_return"] > 0),
        "date",
    ].astype(str).tolist()
    fetch_dates = sorted(set(loose_dates) | set(body_dates))

    all_paths: list[pd.DataFrame] = []
    all_metrics: list[dict[str, Any]] = []
    minute_quality: list[dict[str, Any]] = []
    daily_by_date = period_daily.set_index("date", drop=False)
    for trade_date in fetch_dates:
        payload = fetch_cached(
            client,
            raw_dir / f"minute_{trade_date}.json",
            params={"symbol": SYMBOL_API, "period": 60, "date": trade_date},
            refresh=args.refresh,
        )
        minute, quality = parse_minute_frame(payload, daily_by_date.loc[trade_date])
        path, metrics = build_path_and_metrics(daily_by_date.loc[trade_date], minute)
        all_paths.append(path)
        all_metrics.append(metrics)
        minute_quality.append(quality)
        print(f"fetched {trade_date}: {quality['raw_minute_count']} raw -> 240 normalized")

    paths = pd.concat(all_paths, ignore_index=True)
    metrics_all = pd.DataFrame(all_metrics).sort_values("trade_date")
    event_metrics = metrics_all.loc[metrics_all["trade_date"].isin(primary_dates)].copy()
    path_type_counts = (
        event_metrics["path_type"]
        .value_counts()
        .rename_axis("path_type")
        .reset_index(name="sample_count")
    )
    path_type_counts["share"] = path_type_counts["sample_count"] / len(event_metrics)
    aggregate = build_aggregate_paths(paths, primary_dates)
    chart_individual, chart_aggregate = chart_datasets(paths, aggregate, primary_dates)
    sensitivity = build_sensitivity(period_daily, metrics_all, paths)
    definition_comparison = build_definition_comparison(period_daily, metrics_all, paths)
    common_features = build_common_features(event_metrics)
    loo = leave_one_out_checks(event_metrics)
    local_comparison, local_summary = validate_against_local_mongo(daily, window)
    minute_quality_frame = pd.DataFrame(minute_quality).sort_values("trade_date")

    numeric_event = event_metrics.select_dtypes(include=[np.number])
    event_summary = {
        column: {
            "q25": float(numeric_event[column].quantile(0.25)),
            "median": float(numeric_event[column].median()),
            "q75": float(numeric_event[column].quantile(0.75)),
        }
        for column in numeric_event.columns
        if column not in {"hit_50_index", "hit_80_index"}
    }
    summary: dict[str, Any] = {
        "generated_at": datetime.now(MARKET_TZ).isoformat(timespec="seconds"),
        "symbol": SYMBOL_INTERNAL,
        "symbol_name": "上证指数",
        "window": {
            "start": pd.Timestamp(window.start).strftime("%Y-%m-%d"),
            "end": pd.Timestamp(window.end).strftime("%Y-%m-%d"),
            "as_of": window.as_of,
            "completed_trade_days": int(len(period_daily)),
        },
        "primary_definition": "daily_return >= 1% and close > open",
        "primary_definition_cn": "较前收涨幅≥1%，且收盘高于开盘",
        "primary_sample_count": len(primary_dates),
        "primary_event_dates": primary_dates,
        "fetched_minute_dates": fetch_dates,
        "source": {
            "provider": "KlineShare",
            "endpoint": "/v2/index/kline",
            "daily_period_seconds": 86400,
            "minute_period_seconds": 60,
            "timezone": "Asia/Shanghai",
        },
        "minute_normalization": {
            "expected_provider_minutes": 239,
            "normalized_minutes": 240,
            "opening_anchor": "09:30 same-source daily open",
            "closing_anchor": "15:00 same-source daily close",
            "max_pre_anchor_close_difference_bp": float(
                minute_quality_frame["pre_anchor_vs_close_bp"].abs().max()
            ),
        },
        "event_metric_distribution": event_summary,
        "pairwise_path_correlation_median": pairwise_path_correlation(paths, primary_dates),
        "path_type_counts": path_type_counts.to_dict(orient="records"),
        "common_features": common_features.to_dict(orient="records"),
        "leave_one_out_checks": loo.to_dict(orient="records"),
        "local_daily_validation": local_summary,
        "report_source_sql": str(RESEARCH_DIR / "report_queries.sql"),
        "database": str(output_dir / "analysis.sqlite"),
    }
    frames = {
        "daily_bars": period_daily.reset_index(drop=True),
        "intraday_paths": paths.sort_values(["trade_date", "minute_index"]),
        "event_metrics": event_metrics,
        "aggregate_paths": aggregate,
        "chart_individual_paths": chart_individual,
        "chart_aggregate_paths": chart_aggregate,
        "threshold_sensitivity": sensitivity,
        "definition_comparison": definition_comparison,
        "common_features": common_features,
        "path_type_counts": path_type_counts,
        "leave_one_out_checks": loo,
        "minute_data_quality": minute_quality_frame,
        "local_daily_comparison": local_comparison,
    }
    save_outputs(output_dir, frames, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
    print(f"output_dir={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
