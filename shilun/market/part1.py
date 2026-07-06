from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import re
from typing import Any

import pandas as pd
# 1. 准备指数数据
# 2. 准备全市场股票数据
# 3. 校验日期是否最新
# 4. 计算市场广度、题材、支撑压力
# 5. 分别计算趋势分、量能分、广度分、题材分、风险分
# 6. 计算总分
# 7. 判断硬触发风险
# 8. 输出市场权限、操作权限、证据、图表数据和解释


DEFAULT_BENCHMARK_TICKER = "000001.SH"
PART1_ENGINE_VERSION = "market_part1_v1"
BENCHMARK_INDEX_OPTIONS = (
    {
        "ticker": "000001.SH",
        "name": "上证指数",
        "short_name": "上证",
        "source": "Tushare index_daily",
        "meaning": "偏主板和权重情绪，适合观察大盘基础温度。",
    },
    {
        "ticker": "000300.SH",
        "name": "沪深300",
        "short_name": "沪深300",
        "source": "Tushare index_daily",
        "meaning": "偏机构权重基准，适合观察核心资产和权重方向。",
    },
    {
        "ticker": "399001.SZ",
        "name": "深证成指",
        "short_name": "深成指",
        "source": "Tushare index_daily",
        "meaning": "偏深市制造和成长风格，适合观察深市风险偏好。",
    },
    {
        "ticker": "399006.SZ",
        "name": "创业板指",
        "short_name": "创业板",
        "source": "Tushare index_daily",
        "meaning": "偏成长和科技风险偏好，适合观察弹性资产情绪。",
    },
)
WEIGHT_INDUSTRY_KEYWORDS = (
    "银行",
    "证券",
    "保险",
    "煤炭",
    "石油",
    "能源",
    "电力",
    "化工",
    "有色",
)


def benchmark_index_meta(ticker: str | None) -> dict[str, str]:
    normalized = (ticker or DEFAULT_BENCHMARK_TICKER).upper()
    for item in BENCHMARK_INDEX_OPTIONS:
        if item["ticker"] == normalized:
            return dict(item)
    return {
        "ticker": normalized,
        "name": normalized,
        "short_name": normalized,
        "source": "Tushare index_daily",
        "meaning": "自定义指数代码；需要 Mongo 中已有对应 index_daily 数据。",
    }


@dataclass(frozen=True)
class MarketPart1Request:
    analysis_date: str
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER
    lookback_days: int = 80
    exclude_st: bool = True

    @property
    def start_date(self) -> str:
        target_dt = datetime.strptime(self.analysis_date, "%Y-%m-%d")
        return (target_dt - timedelta(days=max(30, self.lookback_days))).strftime("%Y-%m-%d")


def evaluate_market_permission(
    *,
    analysis_date: str,
    index_bars: pd.DataFrame,
    market_bars: pd.DataFrame,
    stock_basic: pd.DataFrame | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
) -> dict[str, Any]:
    """Evaluate PART1 market permission from daily bars.

    This is the active Python implementation of the Notion v0.2 state machine.
    It intentionally keeps proxy fields explicit, so later Rust migration can
    preserve the same contract without pretending all signals are first-class
    exchange data.
    通过日数据计算PART1 大盘权限的结果
    符合文档：https://www.notion.so/v0-2-3694ca8a1a218118b911cbee910c9ba2?source=copy_link的生产要求
    先故意将字段做显示展示，方便日后迁移到架构上
    """

    index_frame = _prepare_index_frame(index_bars, analysis_date)
    if index_frame.empty:
        raise ValueError(
            f"No benchmark/index bars found for {benchmark_ticker} before {analysis_date}. "
            "请先同步最新交易日或增量同步缺口；指数基准需要 index_daily 数据。"
        )

    stock_frame = _prepare_market_frame(market_bars, stock_basic=stock_basic, exclude_st=True)
    if stock_frame.empty:
        raise ValueError(f"No stock market bars found before {analysis_date}.")

    benchmark_meta = benchmark_index_meta(benchmark_ticker)
    latest_index = index_frame.iloc[-1]
    latest_date = _date_text(latest_index["date"])
    if latest_date != analysis_date:
        raise ValueError(f"Benchmark latest date is {latest_date}, not requested analysis_date {analysis_date}.")

    breadth = _build_breadth_context(stock_frame, analysis_date)
    theme = _build_theme_context(
        stock_frame=stock_frame,
        stock_basic=stock_basic,
        analysis_date=analysis_date,
        index_pct_chg=_float(latest_index.get("pct_chg"), 0.0),
    )
    levels = _build_support_pressure(index_frame)

    trend = _score_trend(latest_index, levels)
    volume = _score_volume(latest_index)
    breadth_score = _score_breadth(breadth)
    theme_score = int(theme["theme_score"])
    risk = _score_risk(
        latest_index=latest_index,
        levels=levels,
        breadth=breadth,
        theme_score=theme_score,
        weight_support_flag=bool(theme["weight_support_flag"]),
    )

    # 先按1:1的权重来设计
    total_score = trend["score"] + volume["score"] + breadth_score["score"] + theme_score - risk["score"]
    hard_triggers = _build_hard_triggers(
        latest_index=latest_index,
        levels=levels,
        breadth=breadth,
        theme_score=theme_score,
        risk_score=risk["score"],
        amount_ratio_5=_float(latest_index.get("amount_ratio_5"), 0.0),
    )
    market_permission = _classify_permission(
        total_score=total_score,
        trend_score=trend["score"],
        breadth_score=breadth_score["score"],
        theme_score=theme_score,
        risk_score=risk["score"],
        hard_triggers=hard_triggers,
    )
    scores = {
        "trend_score": trend["score"],
        "volume_score": volume["score"],
        "breadth_score": breadth_score["score"],
        "theme_score": theme_score,
        "risk_score": risk["score"],
    }
    previous_index = index_frame.iloc[-2] if len(index_frame) >= 2 else latest_index
    metrics = {
        "index_open": _round(latest_index.get("open")),
        "index_high": _round(latest_index.get("high")),
        "index_low": _round(latest_index.get("low")),
        "index_close": _round(latest_index.get("close")),
        "index_pct_chg": _round(_float(latest_index.get("pct_chg"), 0.0), 4),
        "index_ma5": _round(latest_index.get("ma5")),
        "index_ma10": _round(latest_index.get("ma10")),
        "index_ma20": _round(latest_index.get("ma20")),
        "ma5_slope": _round(latest_index.get("ma5_slope"), 4),
        "upper_shadow_ratio": _round(latest_index.get("upper_shadow_ratio"), 4),
        "amount": _round(latest_index.get("amount")),
        "amount_prev": _round(previous_index.get("amount")),
        "amount_change_vs_prev": _round(
            _ratio_change(_float(latest_index.get("amount"), 0.0), _float(previous_index.get("amount"), 0.0)),
            4,
        ),
        "amount_ma5": _round(latest_index.get("amount_ma5")),
        "amount_ma20": _round(latest_index.get("amount_ma20")),
        "amount_ratio_5": _round(latest_index.get("amount_ratio_5"), 4),
        "amount_ratio_20": _round(latest_index.get("amount_ratio_20"), 4),
        **breadth["metrics"],
        **theme["metrics"],
    }
    action_permission = _action_permission(market_permission)
    market_gate = _build_market_gate(
        permission=market_permission,
        hard_triggers=hard_triggers,
        scores=scores,
        metrics=metrics,
        breadth_evidence=breadth_score["evidence"],
    )
    evidence = trend["evidence"] + volume["evidence"] + breadth_score["evidence"] + theme["evidence"] + risk["evidence"]
    chart_data = _build_market_chart_data(
        analysis_date=analysis_date,
        index_frame=index_frame,
        stock_frame=stock_frame,
        breadth=breadth,
        opportunity_score=trend["score"] + volume["score"] + breadth_score["score"] + theme_score,
        risk_score=risk["score"],
    )
    trend_summary = _market_trend_summary(latest_index)
    pattern_forecast = _build_pattern_forecast(index_frame, levels, trend_summary=trend_summary)

    return {
        "engine_version": PART1_ENGINE_VERSION,
        "analysis_date": analysis_date,
        "benchmark_ticker": benchmark_ticker,
        "benchmark_name": benchmark_meta["name"],
        "benchmark_meta": benchmark_meta,
        "benchmark_options": [dict(item) for item in BENCHMARK_INDEX_OPTIONS],
        "market_permission": market_permission,
        "permission_label": _permission_label(market_permission),
        "permission_summary": _permission_summary(market_permission),
        "action_permission": action_permission,
        "market_gate": market_gate,
        "total_score": total_score,
        "scores": scores,
        "metrics": metrics,
        "levels": levels,
        "hard_triggers": hard_triggers,
        "theme_method": theme.get("method", _theme_method()),
        "theme_candidates": theme.get("candidates", []),
        "chart_data": chart_data,
        "evidence": evidence,
        "interpretation": _build_interpretation(
            benchmark_ticker=benchmark_ticker,
            benchmark_name=benchmark_meta["name"],
            market_permission=market_permission,
            action_permission=action_permission,
            total_score=total_score,
            scores=scores,
            metrics=metrics,
            levels=levels,
            hard_triggers=hard_triggers,
            evidence=evidence,
            theme_candidates=theme.get("candidates", []),
            pattern_forecast=pattern_forecast,
        ),
        "trend_summary": trend_summary,
        "pattern_forecast": pattern_forecast,
        "implementation_status": _implementation_status(),
        "state_machine": _state_machine_spec(),
        "data_quality": _data_quality(index_frame=index_frame, stock_frame=stock_frame, stock_basic=stock_basic),
    }


def _prepare_index_frame(index_bars: pd.DataFrame, analysis_date: str) -> pd.DataFrame:
    frame = _normalize_bars(index_bars)
    if frame.empty:
        return frame
    frame = frame.loc[frame["date"] <= pd.Timestamp(analysis_date)].copy()
    frame = frame.sort_values("date").reset_index(drop=True)
    if frame.empty:
        return frame
    frame["pct_chg"] = frame["close"].pct_change().fillna((frame["close"] / frame["open"]) - 1.0)
    for window in (5, 10, 20, 50, 120, 250):
        # 历史不够长时 min_periods=1 会算"伪 MA"，所以 MA50+ 用更严格的最小样本
        min_p = 1 if window <= 20 else max(int(window * 0.6), 30)
        frame[f"ma{window}"] = frame["close"].rolling(window, min_periods=min_p).mean()
    frame["ma5_slope"] = frame["ma5"].diff().fillna(0.0)
    frame["ma10_slope"] = frame["ma10"].diff().fillna(0.0)
    frame["ma20_slope"] = frame["ma20"].diff().fillna(0.0)
    frame["ma50_slope_5d"] = frame["ma50"].diff(5).fillna(0.0)  # MA50 五日斜率
    frame["ma120_slope_5d"] = frame["ma120"].diff(5).fillna(0.0)  # MA120 五日斜率
    frame["amount_ma5"] = frame["amount"].rolling(5, min_periods=1).mean()
    frame["amount_ma20"] = frame["amount"].rolling(20, min_periods=1).mean()
    frame["amount_ratio_5"] = _safe_div_series(frame["amount"], frame["amount_ma5"])
    frame["amount_ratio_20"] = _safe_div_series(frame["amount"], frame["amount_ma20"])
    frame["rolling_low_20"] = frame["low"].rolling(20, min_periods=1).min()
    frame["rolling_high_20"] = frame["high"].rolling(20, min_periods=1).max()
    frame["upper_shadow_ratio"] = _safe_div_series(frame["high"] - frame[["open", "close"]].max(axis=1), frame["high"] - frame["low"])
    return frame


def _compute_limit_thresholds(frame: pd.DataFrame) -> pd.Series:
    """按板块 + 是否 ST 计算个股涨跌停阈值（向量化，一次算完全市场）。

    A 股涨跌停规则：
    - 主板（60x/000/001/002）：±10%（阈值 0.095）
    - 创业板（300/301）：±20%（阈值 0.195）
    - 科创板（688）：±20%（阈值 0.195）
    - 北交所（8xx/4xx）：±30%（阈值 0.295）
    - ST/*ST：±5%（阈值 0.045，覆盖上面所有板块的判定）
    - 新股首日：无限制（当前无法准确识别，按默认 0.095 处理，会有小误差）
    """
    if frame.empty:
        return pd.Series(dtype="float64")
    tickers = frame["ticker"].astype(str).str.upper()
    codes = tickers.str.split(".").str[0]
    names = frame.get("name", pd.Series("", index=frame.index)).fillna("").astype(str)

    thresholds = pd.Series(0.095, index=frame.index)  # 默认主板 10%
    thresholds.loc[codes.str.startswith("688")] = 0.195       # 科创板
    thresholds.loc[codes.str.startswith(("300", "301"))] = 0.195  # 创业板
    thresholds.loc[codes.str.startswith(("4", "8"))] = 0.295  # 北交所
    thresholds.loc[names.str.contains("ST", case=False, na=False)] = 0.045  # ST 优先级最高
    return thresholds


def _prepare_market_frame(
    market_bars: pd.DataFrame,
    *,
    stock_basic: pd.DataFrame | None,
    exclude_st: bool,
) -> pd.DataFrame:
    frame = _normalize_bars(market_bars)
    if frame.empty:
        return frame
    if stock_basic is not None and not stock_basic.empty and "ts_code" in stock_basic.columns:
        stock_info = stock_basic.copy()
        stock_info["ticker"] = stock_info["ts_code"].astype(str)
        allowed = set(stock_info["ticker"].dropna().astype(str))
        frame = frame.loc[frame["ticker"].isin(allowed)].copy()
        merge_columns = [column for column in ["ticker", "name", "industry", "market"] if column in stock_info.columns]
        frame = frame.merge(stock_info[merge_columns].drop_duplicates("ticker"), on="ticker", how="left")
    else:
        frame = frame.loc[frame["ticker"].map(_looks_like_stock_ticker)].copy()
    if exclude_st and "name" in frame.columns:
        names = frame["name"].fillna("").astype(str)
        frame = frame.loc[~names.str.contains("ST", case=False, regex=False)].copy()
    frame = frame.sort_values(["ticker", "date"]).reset_index(drop=True)
    frame["pct_chg"] = frame.groupby("ticker", group_keys=False)["close"].pct_change()
    fallback = (frame["close"] / frame["open"]) - 1.0
    frame["pct_chg"] = frame["pct_chg"].fillna(fallback).fillna(0.0)
    # 分板块涨跌停阈值，供 breadth 使用
    frame["limit_threshold"] = _compute_limit_thresholds(frame)
    return frame


def _normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "volume", "amount"])
    normalized = frame.copy()
    if "ts_code" in normalized.columns and "ticker" not in normalized.columns:
        normalized = normalized.rename(columns={"ts_code": "ticker"})
    if "trade_date" in normalized.columns and "date" not in normalized.columns:
        normalized = normalized.rename(columns={"trade_date": "date"})
    if "vol" in normalized.columns and "volume" not in normalized.columns:
        normalized = normalized.rename(columns={"vol": "volume"})
    for column in ["ticker", "date", "open", "high", "low", "close", "volume", "amount"]:
        if column not in normalized.columns:
            normalized[column] = pd.NA
    normalized["ticker"] = normalized["ticker"].astype(str)
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=["ticker", "date", "open", "high", "low", "close"])
    normalized["amount"] = normalized["amount"].fillna(0.0)
    return normalized.sort_values(["ticker", "date"]).reset_index(drop=True)


def _build_breadth_context(stock_frame: pd.DataFrame, analysis_date: str) -> dict[str, Any]:
    target = pd.Timestamp(analysis_date)
    grouped: list[dict[str, Any]] = []
    for trade_date, rows in stock_frame.groupby("date"):
        pct = pd.to_numeric(rows["pct_chg"], errors="coerce").fillna(0.0)
        # 分板块涨跌停阈值（主板 10% / 创业板+科创板 20% / 北交所 30% / ST 5%）
        threshold = pd.to_numeric(
            rows.get("limit_threshold", pd.Series(0.095, index=rows.index)),
            errors="coerce",
        ).fillna(0.095)
        up_count = int((pct > 0).sum())
        down_count = int((pct < 0).sum())
        flat_count = int((pct == 0).sum())
        limit_up_count = int((pct >= threshold).sum())
        limit_down_count = int((pct <= -threshold).sum())
        market_amount = float(pd.to_numeric(rows["amount"], errors="coerce").fillna(0.0).clip(lower=0).sum())
        grouped.append(
            {
                "date": trade_date,
                "up_count": up_count,
                "down_count": down_count,
                "flat_count": flat_count,
                "total_count": int(len(rows)),
                "limit_up_count": limit_up_count,
                "limit_down_count": limit_down_count,
                "market_amount": market_amount,
            }
        )
    breadth = pd.DataFrame(grouped).sort_values("date").reset_index(drop=True)
    breadth["up_ratio"] = breadth["up_count"] / breadth["total_count"].clip(lower=1)
    breadth["market_amount_ma5"] = breadth["market_amount"].rolling(5, min_periods=1).mean()
    breadth["market_amount_ratio_ma5"] = _safe_div_series(
        breadth["market_amount"],
        breadth["market_amount_ma5"],
    )
    current_rows = breadth.loc[breadth["date"] == target]
    if current_rows.empty:
        raise ValueError(f"No market breadth rows found for {analysis_date}.")
    idx = int(current_rows.index[-1])
    previous = breadth.iloc[max(0, idx - 5) : idx]
    previous_one = breadth.iloc[idx - 1] if idx > 0 else current_rows.iloc[-1]
    current = current_rows.iloc[-1].to_dict()
    up_count_ma5 = float(previous["up_count"].mean()) if not previous.empty else float(current["up_count"])
    limit_down_ma5 = float(previous["limit_down_count"].mean()) if not previous.empty else float(current["limit_down_count"])
    market_amount_ma5 = float(previous["market_amount"].mean()) if not previous.empty else float(current["market_amount"])
    total_count = max(1, int(current["total_count"]))
    history = breadth.iloc[max(0, idx - 19) : idx + 1]
    return {
        "metrics": {
            "up_count": int(current["up_count"]),
            "down_count": int(current["down_count"]),
            "flat_count": int(current["flat_count"]),
            "stock_count": total_count,
            "up_ratio": _round(current["up_count"] / total_count, 4),
            "up_count_ma5": _round(up_count_ma5, 2),
            "up_count_ratio_ma5": _round(_safe_div(float(current["up_count"]), up_count_ma5), 4),
            "limit_up_count": int(current["limit_up_count"]),
            "limit_down_count": int(current["limit_down_count"]),
            "limit_down_count_ma5": _round(limit_down_ma5, 2),
            "market_amount": _round(current["market_amount"]),
            "market_amount_ma5": _round(market_amount_ma5),
            "market_amount_change_vs_prev": _round(
                _ratio_change(float(current["market_amount"]), _float(previous_one.get("market_amount"), 0.0)),
                4,
            ),
            "market_amount_ratio_ma5": _round(_safe_div(float(current["market_amount"]), market_amount_ma5), 4),
        },
        "series": [
            {
                "date": _date_text(row["date"]),
                "up_count": int(row["up_count"]),
                "down_count": int(row["down_count"]),
                "flat_count": int(row["flat_count"]),
                "up_ratio": _round(row["up_ratio"], 4),
                "limit_up_count": int(row["limit_up_count"]),
                "limit_down_count": int(row["limit_down_count"]),
                "market_amount": _round(row["market_amount"]),
                "market_amount_ratio_ma5": _round(row["market_amount_ratio_ma5"], 4),
            }
            for _, row in history.iterrows()
        ],
    }


def _build_market_chart_data(
    *,
    analysis_date: str,
    index_frame: pd.DataFrame,
    stock_frame: pd.DataFrame,
    breadth: dict[str, Any],
    opportunity_score: int,
    risk_score: int,
) -> dict[str, Any]:
    index_history = index_frame.tail(20).copy()
    first_close = _float(index_history.iloc[0].get("close"), 0.0) if not index_history.empty else 0.0
    benchmark_series = [
        {
            "date": _date_text(row["date"]),
            "close": _round(row.get("close")),
            "normalized_close": _round(_safe_div(_float(row.get("close"), 0.0), first_close) * 100.0, 4),
            "pct_chg": _round(row.get("pct_chg"), 4),
            "ma5": _round(row.get("ma5")),
            "ma10": _round(row.get("ma10")),
            "amount": _round(row.get("amount")),
            "amount_ratio_5": _round(row.get("amount_ratio_5"), 4),
        }
        for _, row in index_history.iterrows()
    ]

    target_rows = stock_frame.loc[stock_frame["date"] == pd.Timestamp(analysis_date)].copy()
    pct = pd.to_numeric(target_rows.get("pct_chg"), errors="coerce").fillna(0.0)
    threshold = pd.to_numeric(
        target_rows.get("limit_threshold", pd.Series(0.095, index=target_rows.index)),
        errors="coerce",
    ).fillna(0.095)
    # 说明：档位是"回报分布"，中间 9 档按固定百分比切分；两端"涨停/跌停"用个股实际阈值
    distribution_specs = (
        ("跌停", pct <= -threshold),
        ("-9.5~-7%", (pct > -0.095) & (pct <= -0.07)),
        ("-7~-5%", (pct > -0.07) & (pct <= -0.05)),
        ("-5~-3%", (pct > -0.05) & (pct <= -0.03)),
        ("-3~0%", (pct > -0.03) & (pct <= 0.0)),
        ("0~3%", (pct > 0.0) & (pct < 0.03)),
        ("3~5%", (pct >= 0.03) & (pct < 0.05)),
        ("5~7%", (pct >= 0.05) & (pct < 0.07)),
        ("7~9.5%", (pct >= 0.07) & (pct < 0.095)),
        ("涨停", pct >= threshold),
    )
    return_distribution = [
        {
            "label": label,
            "count": int(mask.sum()),
            "direction": "down" if index < 5 else "up",
        }
        for index, (label, mask) in enumerate(distribution_specs)
    ]
    # 温度分：机会项(+6/项) - 风险项(-4/项)，clip 到 0-100
    # 修复历史 bug：原本传 total_score（已含 -risk），再 -risk_score*4 相当于 risk 被减两次（实际权重 -10）
    temperature_score = int(max(0, min(100, round(50 + opportunity_score * 6 - risk_score * 4))))
    if temperature_score >= 75:
        temperature_label = "偏强，可按权限参与"
    elif temperature_score >= 55:
        temperature_label = "中性偏暖，等待共振"
    elif temperature_score >= 35:
        temperature_label = "偏弱，优先控制仓位"
    else:
        temperature_label = "风险区，等待止跌"
    return {
        "data_frequency": "daily",
        "frequency_note": "当前图表基于日线复盘，不是盘中分钟走势；接入 minute_bars 后再升级为分时曲线。",
        "benchmark_series": benchmark_series,
        "breadth_series": breadth.get("series", []),
        "return_distribution": return_distribution,
        "temperature": {
            "score": temperature_score,
            "label": temperature_label,
            "formula": "clip(50 + opportunity_score*6 - risk_score*4, 0, 100)，opportunity=trend+volume+breadth+theme",
        },
    }


def _build_theme_context(
    *,
    stock_frame: pd.DataFrame,
    stock_basic: pd.DataFrame | None,
    analysis_date: str,
    index_pct_chg: float,
) -> dict[str, Any]:
    if "industry" not in stock_frame.columns:
        return {
            "theme_score": 0,
            "weight_support_flag": False,
            "metrics": {
                "main_theme_status": "unknown",
                "main_theme_name": None,
                "main_theme_return": None,
                "main_theme_up_ratio": None,
                "main_theme_market_share": None,
                "weight_support_flag": False,
            },
            "evidence": ["主线分：缺少 stock_basic.industry，主线质量暂按 unknown 处理。"],
            "candidates": [],
            "method": _theme_method(),
        }
    target_rows = stock_frame.loc[stock_frame["date"] == pd.Timestamp(analysis_date)].copy()
    target_rows = target_rows.dropna(subset=["industry"])
    if target_rows.empty:
        return {
            "theme_score": 0,
            "weight_support_flag": False,
            "metrics": {
                "main_theme_status": "unknown",
                "main_theme_name": None,
                "main_theme_return": None,
                "main_theme_up_ratio": None,
                "main_theme_market_share": None,
                "weight_support_flag": False,
            },
            "evidence": ["主线分：当日缺少行业归属，主线质量暂按 unknown 处理。"],
            "candidates": [],
            "method": _theme_method(),
        }
    total_amount = float(target_rows["amount"].clip(lower=0).sum() or 0.0)
    industry_rows: list[dict[str, Any]] = []
    for industry, rows in target_rows.groupby("industry"):
        if len(rows) < 3:
            continue
        amount = float(rows["amount"].clip(lower=0).sum() or 0.0)
        weights = rows["amount"].clip(lower=0)
        if float(weights.sum() or 0.0) > 0:
            industry_return = float((rows["pct_chg"] * weights / weights.sum()).sum())
        else:
            industry_return = float(rows["pct_chg"].mean())
        industry_rows.append(
            {
                "industry": str(industry),
                "return": industry_return,
                "up_ratio": float((rows["pct_chg"] > 0).mean()),
                "amount": amount,
                "market_share": _safe_div(amount, total_amount),
                "stock_count": int(len(rows)),
            }
        )
    if not industry_rows:
        return {
            "theme_score": 0,
            "weight_support_flag": False,
            "metrics": {
                "main_theme_status": "unknown",
                "main_theme_name": None,
                "main_theme_return": None,
                "main_theme_up_ratio": None,
                "main_theme_market_share": None,
                "weight_support_flag": False,
            },
            "evidence": ["主线分：行业样本不足，主线质量暂按 unknown 处理。"],
            "candidates": [],
            "method": _theme_method(),
        }
    industry_frame = pd.DataFrame(industry_rows)
    industry_frame["theme_rank_score"] = (
        industry_frame["return"] * 100.0
        + industry_frame["up_ratio"] * 2.0
        + industry_frame["market_share"].clip(upper=0.12) * 10.0
    )
    industry_frame = industry_frame.sort_values("theme_rank_score", ascending=False).reset_index(drop=True)
    candidates = [
        {
            "industry": str(row["industry"]),
            "return": _round(row["return"], 4),
            "up_ratio": _round(row["up_ratio"], 4),
            "market_share": _round(row["market_share"], 4),
            "stock_count": int(row["stock_count"]),
            "rank_score": _round(row["theme_rank_score"], 4),
            "status": _theme_candidate_status(row, index_pct_chg),
        }
        for _, row in industry_frame.head(5).iterrows()
    ]
    leader = industry_frame.iloc[0]
    score = 0
    status = "neutral"
    if leader["return"] > index_pct_chg + 0.005 and leader["up_ratio"] >= 0.60 and leader["market_share"] >= 0.03:
        score = 2
        status = "confirmed_proxy"
    elif leader["return"] > index_pct_chg and leader["up_ratio"] >= 0.50:
        score = 1
        status = "candidate_proxy"
    elif index_pct_chg > 0 and leader["up_ratio"] < 0.45:
        score = -2
        status = "index_up_theme_weak"

    weight_frame = industry_frame.loc[industry_frame["industry"].map(_is_weight_industry)].copy()
    weight_support_flag = False
    if not weight_frame.empty:
        weight_return = float((weight_frame["return"] * weight_frame["market_share"]).sum() / max(0.0001, weight_frame["market_share"].sum()))
        non_weight = industry_frame.loc[~industry_frame["industry"].map(_is_weight_industry)]
        non_weight_up_ratio = float(non_weight["up_ratio"].mean()) if not non_weight.empty else 0.0
        # 权重护盘判定（不再要求大盘必须红盘，抗跌+平盘也可能是护盘）：
        #   ① 权重加权收益 > 大盘 + 0.3%（权重明显强）
        #   ② 非权重扩散差（上涨占比 < 45%）
        #   ③ 大盘跌幅不超过 2%（真崩盘时权重也弱，那不叫护盘）
        weight_support_flag = bool(
            weight_return > index_pct_chg + 0.003
            and non_weight_up_ratio < 0.45
            and index_pct_chg > -0.02
        )
        if weight_support_flag:
            score = min(score, -2)
            status = "weight_support_proxy"

    evidence = [
        (
            f"主线分：{leader['industry']} 为当日代理主线，行业收益 {leader['return']:.2%}，"
            f"上涨占比 {leader['up_ratio']:.1%}，成交额占比 {leader['market_share']:.1%}，状态 {status}。"
        )
    ]
    if weight_support_flag:
        evidence.append("主线分：权重行业强于指数但非权重行业扩散不足，标记为疑似权重护盘。")
    return {
        "theme_score": int(score),
        "weight_support_flag": weight_support_flag,
        "metrics": {
            "main_theme_status": status,
            "main_theme_name": str(leader["industry"]),
            "main_theme_return": _round(leader["return"], 4),
            "main_theme_up_ratio": _round(leader["up_ratio"], 4),
            "main_theme_market_share": _round(leader["market_share"], 4),
            "weight_support_flag": weight_support_flag,
        },
        "evidence": evidence,
        "candidates": candidates,
        "method": _theme_method(),
    }


def _theme_method() -> dict[str, str]:
    return {
        "source": "Tushare stock_basic.industry",
        "method": "按个股所属行业聚合当日涨跌幅、上涨占比和成交额占比，形成行业主线代理指标。",
        "not_sw_index": "当前不是申万指数行情，也没有使用申万一级/二级行业指数涨跌幅。",
        "not_concept_index": "当前不是 Tushare 概念板块或主题指数，只是 stock_basic 行业字段的横截面聚合。",
        "upgrade_path": "后续可接入 Tushare 申万行业分类/行业指数或人工主线表，再替换 proxy。"
    }


def _theme_candidate_status(row: pd.Series, index_pct_chg: float) -> str:
    industry_return = _float(row.get("return"), 0.0)
    up_ratio = _float(row.get("up_ratio"), 0.0)
    market_share = _float(row.get("market_share"), 0.0)
    if industry_return > index_pct_chg + 0.005 and up_ratio >= 0.60 and market_share >= 0.03:
        return "confirmed_proxy"
    if industry_return > index_pct_chg and up_ratio >= 0.50:
        return "candidate_proxy"
    if industry_return > index_pct_chg and (up_ratio < 0.50 or market_share < 0.03):
        return "local_hotspot_proxy"
    if industry_return > 0:
        return "weak_repair_proxy"
    return "weak"


def _build_interpretation(
    *,
    benchmark_ticker: str,
    benchmark_name: str,
    market_permission: str,
    action_permission: dict[str, str],
    total_score: int,
    scores: dict[str, Any],
    metrics: dict[str, Any],
    levels: dict[str, Any],
    hard_triggers: list[dict[str, str]],
    evidence: list[str],
    theme_candidates: list[dict[str, Any]],
    pattern_forecast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    index_conclusion = _index_state_conclusion(metrics, levels)
    breadth_conclusion = _breadth_state_conclusion(metrics)
    theme_conclusion = _theme_state_conclusion(metrics)
    decision_conclusion = _decision_conclusion(
        market_permission=market_permission,
        scores=scores,
        total_score=total_score,
        hard_triggers=hard_triggers,
    )
    pattern_section = _pattern_forecast_section(pattern_forecast) if pattern_forecast else None
    sections = [
        *([pattern_section] if pattern_section else []),
        _index_state_section(benchmark_ticker, benchmark_name, metrics, levels, index_conclusion),
        _breadth_state_section(metrics, breadth_conclusion),
        _theme_state_section(metrics, theme_candidates, theme_conclusion),
        _decision_section(market_permission, action_permission, total_score, scores, hard_triggers, decision_conclusion),
    ]
    return {
        "headline": f"{_permission_label(market_permission)}：{decision_conclusion}",
        "conclusion": " ".join([index_conclusion, breadth_conclusion, theme_conclusion, decision_conclusion]),
        "sections": sections,
        "scorecard": _scorecard(scores),
        "key_evidence": evidence,
        "usage_note": "这是由日线/行业代理指标生成的结构化复盘；盘中分时、人工主线确认、龙头/中军反馈仍需 Part2/A池补齐。",
    }


def _index_state_section(
    benchmark_ticker: str,
    benchmark_name: str,
    metrics: dict[str, Any],
    levels: dict[str, Any],
    conclusion: str,
) -> dict[str, Any]:
    return {
        "title": "1. 指数状态",
        "conclusion": conclusion,
        "rows": [
            {
                "indicator": f"{benchmark_name}（{benchmark_ticker}）",
                "value": (
                    f"收盘 {_number_text(metrics.get('index_close'))}，"
                    f"{_pct_text(metrics.get('index_pct_chg'))}"
                ),
                "judgement": _index_close_judgement(metrics),
            },
            {
                "indicator": "盘中位置",
                "value": (
                    f"开盘 {_number_text(metrics.get('index_open'))} / "
                    f"最高 {_number_text(metrics.get('index_high'))} / "
                    f"最低 {_number_text(metrics.get('index_low'))}"
                ),
                "judgement": _intraday_position_judgement(metrics, levels),
            },
            {
                "indicator": "均线结构",
                "value": (
                    f"MA5 {_number_text(metrics.get('index_ma5'))}，"
                    f"MA10 {_number_text(metrics.get('index_ma10'))}，"
                    f"MA20 {_number_text(metrics.get('index_ma20'))}，"
                    f"MA5斜率 {_number_text(metrics.get('ma5_slope'), digits=4)}"
                ),
                "judgement": _ma_structure_judgement(metrics),
            },
            {
                "indicator": "支撑压力",
                "value": (
                    f"支撑1 {_number_text(levels.get('support_1'))}({levels.get('support_1_source')})，"
                    f"支撑2 {_number_text(levels.get('support_2'))}({levels.get('support_2_source')})，"
                    f"压力 {_number_text(levels.get('pressure_1'))}({levels.get('pressure_1_source')})"
                ),
                "judgement": "收盘与支撑/压力的关系用于确认今天是突破、修复还是破位。",
            },
        ],
    }


def _breadth_state_section(metrics: dict[str, Any], conclusion: str) -> dict[str, Any]:
    return {
        "title": "2. 市场广度和成交额",
        "conclusion": conclusion,
        "rows": [
            {
                "indicator": "上涨/下跌家数",
                "value": (
                    f"上涨 {int(metrics.get('up_count') or 0)} 家，"
                    f"下跌 {int(metrics.get('down_count') or 0)} 家，"
                    f"平盘 {int(metrics.get('flat_count') or 0)} 家，"
                    f"上涨占比 {_pct_text(metrics.get('up_ratio'))}"
                ),
                "judgement": _breadth_count_judgement(metrics),
            },
            {
                "indicator": "赚钱效应相对5日",
                "value": (
                    f"上涨家数5日均 {metrics.get('up_count_ma5')}，"
                    f"今日/5日均 {_ratio_text(metrics.get('up_count_ratio_ma5'))}"
                ),
                "judgement": "大于 1.10 才算明显扩散；低于 1.00 说明修复没有扩到多数个股。",
            },
            {
                "indicator": "涨跌停情绪",
                "value": (
                    f"涨停 {int(metrics.get('limit_up_count') or 0)} 家，"
                    f"跌停 {int(metrics.get('limit_down_count') or 0)} 家，"
                    f"跌停5日均 {metrics.get('limit_down_count_ma5')}"
                ),
                "judgement": _limit_mood_judgement(metrics),
            },
            {
                "indicator": "全市场成交额",
                "value": (
                    f"{_amount_yi_text(metrics.get('market_amount'))}，"
                    f"较昨日 {_pct_text(metrics.get('market_amount_change_vs_prev'))}，"
                    f"相对5日均 {_ratio_text(metrics.get('market_amount_ratio_ma5'))}"
                ),
                "judgement": _market_amount_judgement(metrics),
            },
            {
                "indicator": "指数成交温度",
                "value": (
                    f"指数成交额 {_amount_yi_text(metrics.get('amount'))}，"
                    f"相对MA5 {_ratio_text(metrics.get('amount_ratio_5'))}，"
                    f"相对MA20 {_ratio_text(metrics.get('amount_ratio_20'))}，"
                    f"上影占比 {_pct_text(metrics.get('upper_shadow_ratio'))}"
                ),
                "judgement": "用于区分健康放量、缩量修复、放量滞涨和放量下跌。",
            },
        ],
    }


def _theme_state_section(
    metrics: dict[str, Any],
    theme_candidates: list[dict[str, Any]],
    conclusion: str,
) -> dict[str, Any]:
    candidate_text = "；".join(
        (
            f"{item['industry']}({_pct_text(item.get('return'))}, "
            f"上涨{_pct_text(item.get('up_ratio'))}, 额占{_pct_text(item.get('market_share'))})"
        )
        for item in theme_candidates[:3]
    )
    return {
        "title": "3. 主要板块和主线质量",
        "conclusion": conclusion,
        "rows": [
            {
                "indicator": "代理主线",
                "value": (
                    f"{metrics.get('main_theme_name') or '-'}，"
                    f"收益 {_pct_text(metrics.get('main_theme_return'))}，"
                    f"上涨占比 {_pct_text(metrics.get('main_theme_up_ratio'))}，"
                    f"成交额占比 {_pct_text(metrics.get('main_theme_market_share'))}"
                ),
                "judgement": _main_theme_judgement(metrics),
            },
            {
                "indicator": "板块候选",
                "value": candidate_text or "暂无行业候选",
                "judgement": "这里只是 stock_basic.industry 聚合代理，用来判断扩散质量，不等同于人工确认主线。",
            },
            {
                "indicator": "板块划分依据",
                "value": "Tushare stock_basic.industry 个股行业字段",
                "judgement": "不是申万行业指数行情，也不是 Tushare 概念板块；当前用于 proxy，后续可升级为申万行业指数/人工主线表。",
            },
            {
                "indicator": "权重护盘",
                "value": "触发" if metrics.get("weight_support_flag") else "未触发",
                "judgement": "若权重强而非权重扩散弱，指数上涨也要降级看待。",
            },
        ],
    }


def _decision_section(
    market_permission: str,
    action_permission: dict[str, str],
    total_score: int,
    scores: dict[str, Any],
    hard_triggers: list[dict[str, str]],
    conclusion: str,
) -> dict[str, Any]:
    trigger_text = "；".join(item["reason"] for item in hard_triggers) if hard_triggers else "未触发硬否决"
    return {
        "title": "4. 状态机结论",
        "conclusion": conclusion,
        "rows": [
            {
                "indicator": "状态",
                "value": f"{market_permission} / {_permission_label(market_permission)}",
                "judgement": _permission_summary(market_permission),
            },
            {
                "indicator": "总分公式",
                "value": (
                    f"{total_score} = 趋势{_signed_score(scores.get('trend_score'))} "
                    f"+ 量能{_signed_score(scores.get('volume_score'))} "
                    f"+ 广度{_signed_score(scores.get('breadth_score'))} "
                    f"+ 主线{_signed_score(scores.get('theme_score'))} "
                    f"- 风险{_signed_score(scores.get('risk_score'))}"
                ),
                "judgement": "机会靠趋势/量能/广度/主线逐级确认，风险项可以直接降级。",
            },
            {
                "indicator": "硬否决",
                "value": trigger_text,
                "judgement": "硬否决优先级高于加权总分。",
            },
            {
                "indicator": "操作边界",
                "value": action_permission.get("text", "-"),
                "judgement": "这是大盘权限，不替代个股买卖点；个股还要过 Part2/策略条件。",
            },
        ],
    }


def _scorecard(scores: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "dimension": "趋势",
            "score": scores.get("trend_score"),
            "indicators": "收盘价 vs MA5/MA10/MA20、MA5斜率、支撑1/2、压力位",
            "standard": "站上短中均线加分；跌破 MA10/MA20 或支撑扣分。",
            "meaning": "确认指数结构是突破、修复、震荡还是破位。",
        },
        {
            "dimension": "量能",
            "score": scores.get("volume_score"),
            "indicators": "指数成交额/MA5、指数成交额/MA20、较昨日成交变化、上影占比",
            "standard": "上涨且 1.05-1.30 倍 MA5 为健康放量；放量下跌或爆量上影扣分。",
            "meaning": "确认修复是有资金承接，还是放量兑现/滞涨。",
        },
        {
            "dimension": "广度",
            "score": scores.get("breadth_score"),
            "indicators": "上涨家数、下跌家数、上涨占比、上涨家数/5日均、涨停/跌停、跌停/5日均",
            "standard": "上涨家数 >=3000 且高于5日均才算扩散；低于1800说明赚钱效应不足。",
            "meaning": "判断指数表现是否真正传导到多数个股。",
        },
        {
            "dimension": "主线",
            "score": scores.get("theme_score"),
            "indicators": "行业收益、行业上涨占比、行业成交额占比、权重护盘代理",
            "standard": "收益强、上涨占比 >=60%、成交额占比 >=3% 才算较强代理主线。",
            "meaning": "区分主线共振、候选主线、局部热点和指数涨题材弱。",
        },
        {
            "dimension": "风险",
            "score": scores.get("risk_score"),
            "indicators": "放量跌破支撑、跌破第二支撑、跌停扩散、爆量上影、权重护盘",
            "standard": "风险分 >=3 或硬否决触发时，权限可直接跳级降到防守/空仓。",
            "meaning": "避免总分看起来还行，但实际已经进入风险扩散。",
        },
    ]


def _index_state_conclusion(metrics: dict[str, Any], levels: dict[str, Any]) -> str:
    close = _float(metrics.get("index_close"), 0.0)
    low = _float(metrics.get("index_low"), close)
    pct_chg = _float(metrics.get("index_pct_chg"), 0.0)
    ma5 = _float(metrics.get("index_ma5"), close)
    ma10 = _float(metrics.get("index_ma10"), close)
    ma5_slope = _float(metrics.get("ma5_slope"), 0.0)
    support_1 = _float(levels.get("support_1"), close)
    if low < support_1 <= close:
        return f"指数盘中跌破第一支撑 {support_1:.2f} 后收回，更像急杀后的修复确认，不是强势突破日。"
    if close < support_1:
        return f"指数收盘跌破第一支撑 {support_1:.2f}，结构需要先降级。"
    if pct_chg > 0 and close > ma5 and ma5 > ma10 and ma5_slope <= 0:
        return "指数收涨并站上短均线，但 MA5 斜率仍未转强，属于修复站稳而非加速突破。"
    if pct_chg > 0 and close > ma5:
        return "指数收涨且站上 MA5，短线结构偏修复。"
    if pct_chg < 0 and close >= ma10:
        return "指数回落但仍守住 MA10，短线转弱但结构未完全破坏。"
    return "指数结构中性，单独不足以打开进攻权限。"


def _breadth_state_conclusion(metrics: dict[str, Any]) -> str:
    up_count = int(metrics.get("up_count") or 0)
    down_count = int(metrics.get("down_count") or 0)
    amount_ratio = _float(metrics.get("market_amount_ratio_ma5"), 1.0)
    if up_count >= 3000 and amount_ratio >= 1.0:
        return "市场广度和成交额同步修复，赚钱效应有扩散。"
    if up_count < 1800 and down_count > up_count:
        if amount_ratio >= 0.85:
            return "成交额不低，但上涨家数不足，说明不是缩量阴跌，而是高成交下的分歧修复。"
        return "上涨家数不足且成交额偏弱，赚钱效应没有恢复。"
    if 1800 <= up_count < 2500:
        return "市场广度中性，修复有但没有扩散到多数个股。"
    return "市场广度偏正面，但仍需要主线质量配合。"


def _theme_state_conclusion(metrics: dict[str, Any]) -> str:
    status = str(metrics.get("main_theme_status") or "unknown")
    name = metrics.get("main_theme_name") or "未知板块"
    if status == "confirmed_proxy":
        return f"{name} 的收益、上涨占比和成交额占比同时较强，可作为代理主线候选。"
    if status == "candidate_proxy":
        return f"{name} 有候选主线特征，但还没有达到强共振标准。"
    if status == "index_up_theme_weak":
        return f"{name} 虽然在行业代理里排名靠前，但扩散或成交额占比不足，指数上涨没有得到主线确认。"
    if status == "weight_support_proxy":
        return "指数上涨疑似由权重支撑，非权重扩散不足，进攻权限需要降级。"
    return "主线质量暂不明确，不能把指数修复直接理解为可进攻。"


def _decision_conclusion(
    *,
    market_permission: str,
    scores: dict[str, Any],
    total_score: int,
    hard_triggers: list[dict[str, str]],
) -> str:
    if any(item.get("target_state") == "empty" for item in hard_triggers):
        return "硬风险已经触发，当前只允许降仓、止损和等待止跌。"
    if market_permission == "attack":
        return "趋势、广度、主线和风险条件共振，计划内进攻权限打开。"
    if market_permission == "hold":
        return "结构没有破坏，但确认条件不完整，有先手观察，无先手不追。"
    if market_permission == "defense":
        if scores.get("trend_score", 0) > 0 and (scores.get("breadth_score", 0) < 0 or scores.get("theme_score", 0) < 0):
            return "指数结构尚可，但广度或主线没有跟上，所以从进攻降为防守。"
        return "总分或风险项不足以支持开新重仓，当前以防守和持仓处理为主。"
    if total_score < -3:
        return "加权总分跌入空仓区间，禁止主动开仓。"
    return "状态机结论偏谨慎，等待更多确认。"


def _index_close_judgement(metrics: dict[str, Any]) -> str:
    pct_chg = _float(metrics.get("index_pct_chg"), 0.0)
    if pct_chg >= 0.01:
        return "收盘涨幅较明显，但是否能进攻还要看广度和主线是否同步。"
    if pct_chg > 0:
        return "指数小幅修复，不能单独视为强突破。"
    if pct_chg >= -0.005:
        return "指数震荡偏弱，重点看是否守住支撑。"
    return "指数收跌，交易权限需要先降级检查。"


def _intraday_position_judgement(metrics: dict[str, Any], levels: dict[str, Any]) -> str:
    close = _float(metrics.get("index_close"), 0.0)
    low = _float(metrics.get("index_low"), close)
    support_1 = _float(levels.get("support_1"), close)
    support_2 = _float(levels.get("support_2"), close)
    if low < support_2 <= close:
        return "盘中打到第二支撑附近后收回，说明有承接，但早盘破位时不适合主动进攻。"
    if low < support_1 <= close:
        return "盘中跌破第一支撑后收回，是分歧后的修复确认。"
    if close < support_1:
        return "收盘没有收回第一支撑，防守优先。"
    return "盘中没有破坏核心支撑，结构暂未失守。"


def _ma_structure_judgement(metrics: dict[str, Any]) -> str:
    close = _float(metrics.get("index_close"), 0.0)
    ma5 = _float(metrics.get("index_ma5"), close)
    ma10 = _float(metrics.get("index_ma10"), close)
    ma20 = _float(metrics.get("index_ma20"), close)
    ma5_slope = _float(metrics.get("ma5_slope"), 0.0)
    if close > ma5 > ma10 > ma20 and ma5_slope > 0:
        return "多头排列且短均线斜率向上，趋势条件较强。"
    if close > ma5 and ma5 > ma10:
        return "站上 MA5 且 MA5 高于 MA10，短线结构偏好；若斜率为负，仍按修复而非加速处理。"
    if close < ma20:
        return "跌破 MA20，中期结构转弱。"
    if close < ma10:
        return "跌破 MA10，短线交易权限需要降级。"
    return "均线结构中性。"


def _breadth_count_judgement(metrics: dict[str, Any]) -> str:
    up_count = int(metrics.get("up_count") or 0)
    if up_count >= 3000:
        return "上涨家数超过 3000，赚钱效应明显扩散。"
    if up_count >= 2500:
        return "上涨家数偏多，但仍要看是否高于5日均。"
    if up_count >= 1800:
        return "上涨家数中性，市场不是全面修复。"
    return "上涨家数低于 1800，多数个股没有跟随指数修复。"


def _limit_mood_judgement(metrics: dict[str, Any]) -> str:
    limit_down_count = int(metrics.get("limit_down_count") or 0)
    limit_down_ma5 = _float(metrics.get("limit_down_count_ma5"), 0.0)
    if limit_down_count > max(5.0, limit_down_ma5 * 1.5):
        return "跌停数显著高于5日均，情绪风险扩散。"
    if limit_down_count >= 20:
        return "仍有一定跌停压力，说明分歧没有完全解除。"
    return "跌停风险未明显扩散。"


def _market_amount_judgement(metrics: dict[str, Any]) -> str:
    amount_ratio = _float(metrics.get("market_amount_ratio_ma5"), 1.0)
    amount_change = _float(metrics.get("market_amount_change_vs_prev"), 0.0)
    if amount_ratio >= 1.10 and amount_change > 0:
        return "高成交且较昨日放大，说明分歧/修复都有资金参与。"
    if amount_ratio >= 0.85:
        return "成交额接近5日均，不是典型缩量阴跌，但也不是明显放量突破。"
    return "成交额低于5日均，修复持续性需要打折。"


def _main_theme_judgement(metrics: dict[str, Any]) -> str:
    status = str(metrics.get("main_theme_status") or "unknown")
    if status == "confirmed_proxy":
        return "收益、扩散和成交额占比同时满足强代理主线条件。"
    if status == "candidate_proxy":
        return "有强度但还需要龙头/中军反馈确认。"
    if status == "local_hotspot_proxy":
        return "局部强，不代表全市场主线共振。"
    if status == "index_up_theme_weak":
        return "指数上涨但代理主线扩散不足，不能按强主线处理。"
    if status == "weight_support_proxy":
        return "疑似权重支撑指数，题材扩散不足。"
    return "主线代理质量不足或数据缺失。"


def _number_text(value: Any, *, digits: int = 2) -> str:
    if value is None:
        return "-"
    number = _float(value, 0.0)
    return f"{number:.{digits}f}"


def _pct_text(value: Any) -> str:
    if value is None:
        return "-"
    return f"{_float(value, 0.0):+.2%}"


def _ratio_text(value: Any) -> str:
    if value is None:
        return "-"
    return f"{_float(value, 0.0):.2f}x"


def _signed_score(value: Any) -> str:
    return f"{int(_float(value, 0.0)):+d}"


def _amount_yi_text(value: Any) -> str:
    if value is None:
        return "-"
    return f"{_float(value, 0.0) / 100000:.2f}亿"


# ─────────────────────────────────────────────────────────────────────────────
# 形态识别 + 明日关键点位预判
# 参考结构：volume_breakout 战法 YAML；每种形态产出2-3个明日关键价位 + 一句指引
# ─────────────────────────────────────────────────────────────────────────────

_PATTERN_META: dict[str, dict[str, str]] = {
    "volume_breakout":       {"display_name": "放量突破",   "signal_type": "bullish"},
    "volume_breakdown":      {"display_name": "放量破位",   "signal_type": "bearish"},
    "high_shadow_warning":   {"display_name": "爆量上影",   "signal_type": "warning"},
    "support_recovery":      {"display_name": "回踩守支撑", "signal_type": "neutral"},
    "strong_bullish":        {"display_name": "强势大阳",   "signal_type": "bullish"},
    "low_vol_consolidation": {"display_name": "缩量整理",   "signal_type": "neutral"},
    "weak_decline":          {"display_name": "弱势回落",   "signal_type": "bearish"},
    "support_breakdown":     {"display_name": "跌破支撑",   "signal_type": "bearish"},
    "mild_recovery":         {"display_name": "小阳修复",   "signal_type": "neutral"},
    "neutral_oscillation":   {"display_name": "震荡整理",   "signal_type": "neutral"},
}

# 当前可用数据 vs 波浪理论精确预判所需数据的缺口说明
_PATTERN_DATA_GAPS: list[dict[str, str]] = [
    {
        "field": "MA50 / MA120 / Swing High/Low",
        "status": "integrated",
        "impact": "high",
        "note": "✅ 已接入。MA50/MA120 已加入支撑压力候选；近 120 日 Swing High/Low 已识别为关键位；趋势方向由 MA50/MA120 五日斜率推导。",
    },
    {
        "field": "外部波浪理论文档（人工标注位）",
        "status": "missing",
        "impact": "high",
        "note": "黑兔量化等外部文档里的推动浪/调整浪、A/B/C 浪关键位。导入接口已规划，待实现。",
    },
    {
        "field": "价格密集成交区（VPOC / 筹码峰）",
        "status": "missing",
        "impact": "high",
        "note": "真正的支撑往往是筹码密集区，而非简单的均线。需要成交额加权的价格分布（Volume Profile）。",
    },
    {
        "field": "MA250 / 跨年级关键位",
        "status": "partial",
        "impact": "medium",
        "note": "当前指数同步 1 年（243 根），刚好够算 MA250。下一步可同步 2 年历史以稳定计算更长周期均线。",
    },
    {
        "field": "北向资金净流入（akshare）",
        "status": "integrated",
        "impact": "medium",
        "note": "✅ 已存到 Mongo。下一步把 north_capital_flow 拉入 PART1 评分维度。",
    },
    {
        "field": "涨停板开板比例 / 连板梯队（akshare）",
        "status": "integrated",
        "impact": "medium",
        "note": "✅ 已存到 Mongo。下一步把 limit_up_pool 的炸板率/连板分布拉入 PART1 情绪分。",
    },
    {
        "field": "股指期货升贴水（IF/IH/IC）",
        "status": "missing",
        "impact": "low",
        "note": "期货升贴水反映机构对明日预期。优先级低。",
    },
]


def _detect_index_pattern(latest: pd.Series, levels: dict[str, Any]) -> str:
    """根据最新一根日线的量价结构识别今日大盘形态。"""
    close        = _float(latest.get("close"), 0.0)
    high         = _float(latest.get("high"), close)
    low          = _float(latest.get("low"), close)
    pct_chg      = _float(latest.get("pct_chg"), 0.0)
    amount_ratio = _float(latest.get("amount_ratio_5"), 1.0)
    upper_shadow = _float(latest.get("upper_shadow_ratio"), 0.0)

    pressure_1 = _float(levels.get("pressure_1"), close * 1.05)
    support_1  = _float(levels.get("support_1"),  close * 0.97)

    rng = high - low
    close_pos = (close - low) / rng if rng > 0 else 0.5  # 0=最低, 1=最高

    # 1. 放量突破：收盘站上压力位 + 量比≥1.5 + 强势收盘（振幅上方70%）
    if close > pressure_1 and amount_ratio >= 1.5 and close_pos >= 0.70:
        return "volume_breakout"

    # 2. 放量破位：收盘跌破支撑 + 量比≥1.3 + 跌幅≥0.5%
    if close < support_1 and amount_ratio >= 1.3 and pct_chg < -0.005:
        return "volume_breakdown"

    # 3. 爆量上影：上影比>40% + 量比≥1.3（优先于强势大阳判断）
    if upper_shadow > 0.40 and amount_ratio >= 1.3:
        return "high_shadow_warning"

    # 4. 回踩守支撑：盘中探至支撑附近后收回（日内价格测试支撑）
    if low <= support_1 * 1.005 and close > support_1 and pct_chg >= -0.005:
        return "support_recovery"

    # 5. 强势大阳：涨幅≥1.5% + 收盘在振幅上方75% + 量能正常
    if pct_chg >= 0.015 and close_pos >= 0.75 and amount_ratio >= 1.0:
        return "strong_bullish"

    # 6. 跌破支撑（量不足以构成放量破位）
    if close < support_1:
        return "support_breakdown"

    # 7. 缩量整理：量比<0.85 + 振幅小于0.5%
    if amount_ratio < 0.85 and abs(pct_chg) < 0.005:
        return "low_vol_consolidation"

    # 8. 弱势回落：收跌但未破支撑
    if pct_chg < -0.003:
        return "weak_decline"

    # 9. 小阳修复：涨幅0.1%~1.5%
    if 0.001 <= pct_chg < 0.015:
        return "mild_recovery"

    return "neutral_oscillation"


def _build_pattern_forecast(
    index_frame: pd.DataFrame,
    levels: dict[str, Any],
    trend_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    基于今日指数日线形态，输出明日关键点位预判。

    v2 升级：
    - 候选位包含 MA50/MA120（已接入，足够算 MA120）
    - 加入近 120 日 Swing High/Low 关键位
    - 输出中长期趋势方向（MA50/MA120 斜率）+ 与之相关的"结构性研判"
    - 仍欠缺：波浪结构（A/B/C 浪、推动浪/调整浪），需人工文档叠加
    """
    if index_frame.empty or len(index_frame) < 2:
        return {
            "primary_pattern": None,
            "key_levels_tomorrow": [],
            "tomorrow_guide": "数据不足，无法预判。",
            "data_gaps": _PATTERN_DATA_GAPS,
        }

    trend_summary = trend_summary or {}

    latest = index_frame.iloc[-1]
    close  = _float(latest.get("close"), 0.0)
    open_  = _float(latest.get("open"), close)
    high   = _float(latest.get("high"), close)
    low    = _float(latest.get("low"), close)
    pct_chg      = _float(latest.get("pct_chg"), 0.0)
    amount_ratio = _float(latest.get("amount_ratio_5"), 1.0)
    ma5   = _float(latest.get("ma5"), close)
    ma10  = _float(latest.get("ma10"), close)
    ma20  = _float(latest.get("ma20"), close)

    pressure_1   = _float(levels.get("pressure_1"), close * 1.05)
    support_1    = _float(levels.get("support_1"),  close * 0.97)
    support_2    = _float(levels.get("support_2"),  close * 0.94)
    p1_src       = str(levels.get("pressure_1_source", "上方阻力"))
    s1_src       = str(levels.get("support_1_source", "下方支撑"))
    s2_src       = str(levels.get("support_2_source", "第二支撑"))

    rng = high - low
    close_pos = (close - low) / rng if rng > 0 else 0.5

    pattern = _detect_index_pattern(latest, levels)
    meta = _PATTERN_META.get(pattern, {"display_name": pattern, "signal_type": "neutral"})

    # 趋势上下文：用于补强每种形态的描述
    ma50 = trend_summary.get("ma50") or 0
    ma120 = trend_summary.get("ma120") or 0
    mid_label = trend_summary.get("mid_label", "")
    long_label = trend_summary.get("long_label", "")
    structure_note = ""
    if ma50 and ma120:
        if close > ma50 and close > ma120:
            structure_note = f"中长期结构：{mid_label}、{long_label}（MA50={ma50}、MA120={ma120} 均在收盘下方，构成中长期支撑）。"
        elif close < ma50 and close < ma120:
            structure_note = f"中长期结构：{mid_label}、{long_label}（MA50={ma50}、MA120={ma120} 均在收盘上方，构成中长期压力）。"
        elif close > ma50 and close < ma120:
            structure_note = f"中长期结构：{mid_label}但{long_label}（已站上 MA50={ma50}，仍在 MA120={ma120} 之下，处于中长期分歧区）。"
        else:
            structure_note = f"中长期结构：{mid_label}、{long_label}（MA50={ma50}、MA120={ma120}）。"
    elif ma50:
        structure_note = f"中期结构：{mid_label}（MA50={ma50}）；MA120 历史不足无法判断长期趋势。"

    key_levels: list[dict[str, Any]] = []
    description = ""
    guide = ""

    if pattern == "volume_breakout":
        description = (
            f"指数放量（量比 {amount_ratio:.1f}x）上穿{p1_src}（{pressure_1:.2f}），"
            f"收盘 {close:.2f} 站稳于振幅上方 {close_pos*100:.0f}%，满足放量突破三要素：\n"
            f"① 收盘站上阻力位；② 量比≥1.5；③ 强势收盘（振幅上方70%）。\n"
            f"参考 volume_breakout 战法：次日开盘守住突破位是区分真/假突破的核心验证。"
        )
        key_levels = [
            {"label": f"突破确认位（{p1_src}，核心）",
             "level": _round(pressure_1), "direction": "support", "significance": "high",
             "note": f"突破后压力转支撑。明日若守住此位（{pressure_1:.2f}）开盘，真突破概率高；"
                     f"若跌回此位下方，视为假突破，volume_breakout 战法要求止损撤退。"},
            {"label": f"{s1_src}（备用支撑）",
             "level": _round(support_1), "direction": "support", "significance": "medium",
             "note": "若明日出现快速回落测试，此位为第二档承接；若破则突破完全失败。"},
            {"label": "延伸目标参考（+3%）",
             "level": _round(pressure_1 * 1.03), "direction": "resistance", "significance": "low",
             "note": "无明确前高数据时，暂以突破位+3%作为短线参考目标（缺口：需历史关键高点数据）。"},
        ]
        guide = (
            f"明日核心验证：开盘是否守住 {pressure_1:.2f}。"
            f"缩量回踩此位不破可继续持仓；若放量跌破，执行假突破止损。"
        )

    elif pattern == "volume_breakdown":
        description = (
            f"指数放量（量比 {amount_ratio:.1f}x）跌破{s1_src}（{support_1:.2f}），"
            f"收盘 {close:.2f}，跌幅 {pct_chg*100:+.2f}%。\n"
            f"放量破位表明卖盘主动，与 volume_breakout 战法逻辑对应的空头版本：\n"
            f"支撑位在量能配合下被有效击穿，不宜在反弹初期抄底。"
        )
        key_levels = [
            {"label": f"破位压力（{s1_src}，已破转压）",
             "level": _round(support_1), "direction": "resistance", "significance": "high",
             "note": f"已破支撑变压力。明日若反弹至 {support_1:.2f} 下方受阻，确认破位有效；"
                     f"若放量收回此位上方，可能是假破位，需重新评估。"},
            {"label": "今日收盘（弱势延续参考）",
             "level": _round(close), "direction": "resistance", "significance": "medium",
             "note": "若明日反弹但收盘低于今日收盘，弱势延续；若超过则有修复迹象。"},
            {"label": f"{s2_src}（下一档支撑）",
             "level": _round(support_2), "direction": "support", "significance": "high",
             "note": f"关键下方支撑 {support_2:.2f}；若有效放量守住，可能是二次确认机会。"},
        ]
        guide = (
            f"明日若反弹至 {support_1:.2f} 下方承压，弱势延续；"
            f"只有放量收回 {support_1:.2f} 以上才能讨论修复，否则下一档看 {support_2:.2f}。"
        )

    elif pattern == "high_shadow_warning":
        upper_body = max(open_, close)
        description = (
            f"指数放量（量比 {amount_ratio:.1f}x）冲高后回落，"
            f"上影线占振幅 {_float(latest.get('upper_shadow_ratio'))*100:.0f}%，收盘 {close:.2f}。\n"
            f"这是压力区卖盘较重的信号。参考 volume_breakout 战法中的'风险过滤'：\n"
            f"收盘应在振幅上方30%，而当日收盘偏弱，说明突破动能被压制。"
        )
        key_levels = [
            {"label": "今日高点（上影压力区上沿）",
             "level": _round(high), "direction": "resistance", "significance": "high",
             "note": f"上影代表抛压区。明日若放量有效站上 {high:.2f}，说明抛压被消化；"
                     f"若缩量在此区域反复，震荡整理；若放量阴线，则是顶部确认。"},
            {"label": f"实体上沿 {upper_body:.2f}（日内承接位）",
             "level": _round(upper_body), "direction": "support", "significance": "medium",
             "note": "实体支撑。守住此位且明日能再次向上，上影是单日分歧；若实体跌破，转入警惕。"},
            {"label": f"{s1_src}（关键支撑）",
             "level": _round(support_1), "direction": "support", "significance": "medium",
             "note": "若今日实体支撑失守，此为关键下方支撑。"},
        ]
        guide = (
            f"明日观察能否放量站上今日高点 {high:.2f}（消化抛压）；"
            f"若守住实体 {upper_body:.2f} 但未突破高点，是正常整理；"
            f"若主动跌破实体，需降权看待当前行情。"
        )

    elif pattern == "support_recovery":
        description = (
            f"指数盘中低探至{s1_src}（{support_1:.2f}）附近，最低至 {low:.2f}，"
            f"但尾盘收回 {close:.2f}。\n"
            f"回踩守支撑：支撑位经过价格测试后仍然有效，尾盘承接说明多方未放弃。\n"
            f"真正的确认点是明日能否离开支撑区域向上走，而不是今天的修复。"
        )
        key_levels = [
            {"label": f"{s1_src}（今日确认支撑）",
             "level": _round(support_1), "direction": "support", "significance": "high",
             "note": f"今日已测试并守住。明日若再回踩 {support_1:.2f} 附近但不破，二次确认支撑有效；"
                     f"若放量跌破，之前的守支撑是诱多。"},
            {"label": "今日收盘（修复延续基准）",
             "level": _round(close), "direction": "support", "significance": "medium",
             "note": "明日若能在此位上方开盘并向上走，修复延续；若跌回支撑区，原地震荡。"},
            {"label": f"{p1_src}（短期修复目标）",
             "level": _round(pressure_1), "direction": "resistance", "significance": "medium",
             "note": "守支撑成功后的短线修复参考目标；需要量能配合才能触及。"},
        ]
        guide = (
            f"核心看明日能否离开支撑 {support_1:.2f} 向上走；"
            f"缩量守支撑健康；若再次快速跌破，支撑失效，需降仓。"
        )

    elif pattern == "strong_bullish":
        body_low = min(open_, close)
        description = (
            f"指数收涨 {pct_chg*100:+.2f}%，量比 {amount_ratio:.1f}x，"
            f"收盘位于振幅上方 {close_pos*100:.0f}%，强势大阳形态。\n"
            f"{'收盘站上' + p1_src + f'（{pressure_1:.2f}）' if close > pressure_1 else '收盘在关键压力位下方，强势但尚未突破。'}"
        )
        key_levels = [
            {"label": f"大阳实体低部 {body_low:.2f}（保护位）",
             "level": _round(body_low), "direction": "support", "significance": "high",
             "note": f"明日若缩量整理不破实体低部 {body_low:.2f}，形态健康；"
                     f"若主动放量阴包阳跌破此位，大阳线失效，需减仓。"},
            {"label": "今日收盘（持续性验证）",
             "level": _round(close), "direction": "support", "significance": "medium",
             "note": "次日高开后守住今日收盘位，趋势延续；若跌回实体低部附近，更谨慎。"},
            {"label": f"{p1_src}（上方目标参考）",
             "level": _round(pressure_1), "direction": "resistance", "significance": "medium",
             "note": "若大阳已突破压力位，此位不再适用；若未突破，此为上方参考阻力。"},
        ]
        guide = (
            f"保护位 {body_low:.2f}，跌破需警惕；"
            f"明日若缩量整理为正常；若放量继续，可向 {pressure_1:.2f} 进发。"
        )

    elif pattern == "low_vol_consolidation":
        description = (
            f"指数缩量整理（量比 {amount_ratio:.1f}x），"
            f"{'收涨' if pct_chg > 0 else '微跌'} {abs(pct_chg)*100:.2f}%，振幅收窄。\n"
            f"缩量说明主动抛压不大，多空双方都在等待；"
            f"方向需要放量突破来确认，当前是'蓄力'阶段。"
        )
        key_levels = [
            {"label": f"整理上沿（今日高点 {high:.2f}）",
             "level": _round(high), "direction": "resistance", "significance": "high",
             "note": f"明日若放量站上 {high:.2f}，是向上突破确认；"
                     f"若缩量在此附近反复，继续整理。"},
            {"label": f"整理下沿（今日低点 {low:.2f}）",
             "level": _round(low), "direction": "support", "significance": "high",
             "note": f"明日若放量跌破 {low:.2f}，可能向下突破；若缩量回踩，仍在整理区间内。"},
            {"label": f"{s1_src}（整理区间下方关键位）",
             "level": _round(support_1), "direction": "support", "significance": "medium",
             "note": "若整理下沿跌破，此为关键支撑；破则整理结束、转为弱势。"},
        ]
        guide = (
            f"方向待放量确认：明日放量站上 {high:.2f} 偏多；"
            f"放量跌破 {low:.2f} 偏空；缩量继续等待。"
        )

    elif pattern == "weak_decline":
        description = (
            f"指数收跌 {abs(pct_chg)*100:.2f}%，未破{s1_src}（{support_1:.2f}），"
            f"属于支撑上方的弱势回落。\n"
            f"MA5（{ma5:.2f}）{'在收盘上方，构成短期均线压力' if ma5 > close else '已被跌破，均线不再是支撑'}。\n"
            f"不需要立刻防守，但需要监控是否进一步破支撑。"
        )
        key_levels = [
            {"label": f"{s1_src}（关键支撑，勿破）",
             "level": _round(support_1), "direction": "support", "significance": "high",
             "note": f"守住 {support_1:.2f} 弱势延续但结构未破；若放量跌破，需降仓，下看 {support_2:.2f}。"},
            {"label": f"MA5 {ma5:.2f}（均线压力）",
             "level": _round(ma5), "direction": "resistance", "significance": "medium",
             "note": f"收复 MA5（{ma5:.2f}）是弱势转修复的第一步；若量能配合，可积极一些。"},
            {"label": f"{s2_src}（备用支撑）",
             "level": _round(support_2), "direction": "support", "significance": "low",
             "note": "若支撑 1 失守，此为下一档关键位。"},
        ]
        guide = (
            f"关注明日能否守住 {support_1:.2f}；"
            f"若收复 MA5（{ma5:.2f}），弱势修复；若跌破 {support_1:.2f}，减仓为先。"
        )

    elif pattern == "support_breakdown":
        description = (
            f"指数收盘 {close:.2f} 跌破{s1_src}（{support_1:.2f}），"
            f"量比 {amount_ratio:.1f}x（{'量不足，试探性破位，需观察' if amount_ratio < 1.3 else '放量破位，有效性较高'}）。\n"
            f"需区分是缩量的虚假破位还是有效破位，明日的行为是关键判断依据。"
        )
        key_levels = [
            {"label": f"{s1_src}（已破，转压力）",
             "level": _round(support_1), "direction": "resistance", "significance": "high",
             "note": f"若明日反弹在 {support_1:.2f} 下方受阻，确认破位有效；"
                     f"若放量收回此位上方，可能是假破位（量不足时尤其注意）。"},
            {"label": "今日收盘（弱势延续参考）",
             "level": _round(close), "direction": "support", "significance": "medium",
             "note": "若明日继续跌破今日收盘，弱势加速。"},
            {"label": f"{s2_src}（下一档关键支撑）",
             "level": _round(support_2), "direction": "support", "significance": "high",
             "note": f"关键下方支撑 {support_2:.2f}；若有效守住，可能出现二次确认机会。"},
        ]
        guide = (
            f"关键看明日反弹能否放量收回 {support_1:.2f}；"
            f"若不能，下方关注 {support_2:.2f} 是否有承接。"
        )

    elif pattern == "mild_recovery":
        description = (
            f"指数小幅修复（+{pct_chg*100:.2f}%），量比 {amount_ratio:.1f}x，"
            f"均线结构{'较好，MA5/MA10/MA20 有序' if ma5 > ma10 > ma20 else '尚待改善'}。\n"
            f"力度有限，不能视为强突破，但结构无破坏；需等待量能配合才能升级判断。"
        )
        key_levels = [
            {"label": f"{p1_src}（上方参考阻力）",
             "level": _round(pressure_1), "direction": "resistance", "significance": "medium",
             "note": "短线修复的参考目标；若明日放量突破此位，升级为突破处理。"},
            {"label": f"{s1_src}（支撑）",
             "level": _round(support_1), "direction": "support", "significance": "high",
             "note": f"若明日回踩至 {support_1:.2f} 附近且守住，修复结构不破。"},
        ]
        guide = (
            f"观察修复是否延续：守住 {support_1:.2f} 是前提；"
            f"若明日放量突破 {pressure_1:.2f}，升级为突破处理。"
        )

    else:  # neutral_oscillation
        description = (
            f"指数震荡整理，收盘 {close:.2f}（{pct_chg*100:+.2f}%），"
            f"量比 {amount_ratio:.1f}x，无明显量价异常。\n"
            f"关注支撑和压力方向是否有有效突破；突破需量能配合才能确认方向。"
        )
        key_levels = [
            {"label": f"{p1_src}（上方阻力）",
             "level": _round(pressure_1), "direction": "resistance", "significance": "medium",
             "note": "明日若放量站上此位，方向偏多。"},
            {"label": f"{s1_src}（下方支撑）",
             "level": _round(support_1), "direction": "support", "significance": "medium",
             "note": "明日若放量跌破此位，方向偏空；守住则继续震荡。"},
        ]
        guide = (
            f"无明确信号，等待突破方向；"
            f"关注是否放量站上 {pressure_1:.2f} 或跌破 {support_1:.2f}。"
        )

    # 把结构性补充注入到 description 尾部
    if structure_note and description:
        description = f"{description}\n{structure_note}"

    # 艾略特波浪分析（最小可用版）
    wave_analysis = _build_wave_analysis(index_frame, levels, trend_summary)

    return {
        "wave_analysis": wave_analysis,
        "primary_pattern": {
            "name": pattern,
            "display_name": meta["display_name"],
            "signal_type": meta["signal_type"],
            "description": description,
        },
        "key_levels_tomorrow": key_levels,
        "tomorrow_guide": guide,
        "structure_context": {
            "mid_trend": trend_summary.get("mid_trend"),
            "mid_label": trend_summary.get("mid_label"),
            "long_trend": trend_summary.get("long_trend"),
            "long_label": trend_summary.get("long_label"),
            "ma50": trend_summary.get("ma50"),
            "ma120": trend_summary.get("ma120"),
            "ma50_slope_5d": trend_summary.get("ma50_slope_5d"),
            "ma120_slope_5d": trend_summary.get("ma120_slope_5d"),
            "swing_highs": levels.get("swing_highs", []),
            "swing_lows": levels.get("swing_lows", []),
        },
        "data_gaps": _PATTERN_DATA_GAPS,
    }


_WAVE_ACTION_LABELS = {
    "buy_on_pullback": "🟢 买入信号",
    "hold": "🟡 持有",
    "wait": "⚪ 等待",
    "exit_at_target": "🟠 止盈区",
    "avoid": "🔴 规避",
}

_WAVE_CONFIDENCE_CN = {"high": "高", "medium": "中", "low": "低"}


def _pattern_forecast_section(pattern_forecast: dict[str, Any]) -> dict[str, Any]:
    """波浪理论分析 + 短期 K 线形态 + 明日关键位，统一展示为 PART1 第一个 section。"""
    wave = pattern_forecast.get("wave_analysis") or {}
    pf = pattern_forecast.get("primary_pattern") or {}
    guide = pattern_forecast.get("tomorrow_guide", "")
    levels = pattern_forecast.get("key_levels_tomorrow", [])

    wave_label = wave.get("current_wave_label", "波浪结构不清晰")
    wave_phase = wave.get("wave_phase", "unknown")
    wave_confidence = wave.get("confidence", "low")
    wave_action = wave.get("action", "wait")
    wave_action_label_full = wave.get("action_label", "等待信号")
    wave_action_icon = _WAVE_ACTION_LABELS.get(wave_action, "⚪ 等待")
    wave_score_adj = wave.get("score_adjustment", 0)
    wave_reasoning = wave.get("reasoning", "")
    fib_levels = wave.get("fibonacci_levels", [])
    violations = wave.get("wave_rule_violations", [])
    wave_points = wave.get("wave_points", [])

    # 顶部结论（合并波浪建议 + 明日指引）
    conclusion_parts = [
        f"{wave_action_icon} | {wave_action_label_full}",
    ]
    if guide:
        conclusion_parts.append(f"明日：{guide}")
    conclusion = " ｜ ".join(conclusion_parts)

    # row 1：波浪当前位置
    wave_value_chips = [
        f"置信度 {_WAVE_CONFIDENCE_CN.get(wave_confidence, wave_confidence)}",
        "推动浪" if wave_phase == "impulse" else ("调整浪" if wave_phase == "corrective" else "结构不明"),
        f"评分调整 {wave_score_adj:+d}",
    ]
    rows: list[dict[str, Any]] = [
        {
            "indicator": "🌊 波浪结构",
            "value": f"{wave_label}，{'、'.join(wave_value_chips)}",
            "judgement": wave_reasoning or "无法识别当前浪型，建议人工分析。",
        },
    ]

    # 波浪端点序列
    if wave_points:
        points_text = " → ".join(f"{p['label']}: {p['price']}" for p in wave_points)
        rows.append({
            "indicator": "📍 波浪端点序列",
            "value": points_text,
            "judgement": "从 Swing High/Low 推导出的关键端点。计数置信度受 Swing 窗口和样本数影响。",
        })

    # 斐波那契关键位（作为 row）
    for lv in fib_levels:
        kind = lv.get("kind", "support")
        kind_label = {"support": "支撑", "resistance": "压力", "target": "目标"}.get(kind, kind)
        rows.append({
            "indicator": f"{kind_label} {lv.get('level', 0):.2f}",
            "value": lv.get("label", ""),
            "judgement": f"波浪理论 / 斐波那契推导出的{kind_label}位。",
        })

    # 波浪规则违反提示
    if violations:
        rows.append({
            "indicator": "⚠️ 波浪规则违反",
            "value": "需重新归数",
            "judgement": "；".join(violations) + "。建议结合人工分析文档校正波浪计数。",
        })

    # K 线短期形态（作为补充）
    if pf and pf.get("display_name"):
        rows.append({
            "indicator": f"🕯 短期 K 线：{pf['display_name']}",
            "value": f"信号性质：{pf.get('signal_type', '-')}",
            "judgement": pf.get("description", ""),
        })

    # 明日关键位（来自原 K 线形态）
    for lv in levels:
        rows.append({
            "indicator": f"{'支撑' if lv['direction'] == 'support' else '压力'} {_round(lv['level']):.2f}（{lv.get('significance', 'medium')}）",
            "value": lv.get("label", ""),
            "judgement": lv.get("note", ""),
        })

    # 数据缺口
    data_gap_names = "、".join(g["field"] for g in _PATTERN_DATA_GAPS if g["impact"] == "high" and g.get("status") != "integrated")
    if data_gap_names:
        rows.append({
            "indicator": "📋 仍待补充",
            "value": "高影响数据缺口",
            "judgement": f"以下数据接入后可显著提升精度：{data_gap_names}。",
        })

    return {
        "title": "0. 波浪结构 + 明日关键位",
        "conclusion": conclusion,
        "rows": rows,
    }


def _detect_swing_points(index_frame: pd.DataFrame, window: int = 5, lookback: int = 120) -> dict[str, list[dict[str, Any]]]:
    """识别近 lookback 根日线里的 Swing High/Low。

    定义：某根 K 的 high 严格大于左右 window 根 K 的 high 即为 Swing High（对称 Swing Low）。
    """
    if index_frame.empty or len(index_frame) < window * 2 + 1:
        return {"swing_highs": [], "swing_lows": []}
    tail = index_frame.tail(lookback).reset_index(drop=True)
    highs = tail["high"].astype(float).tolist()
    lows = tail["low"].astype(float).tolist()
    dates = [_date_text(d) for d in tail["date"].tolist()]
    swing_highs: list[dict[str, Any]] = []
    swing_lows: list[dict[str, Any]] = []
    for i in range(window, len(tail) - window):
        h_window = highs[i - window : i + window + 1]
        l_window = lows[i - window : i + window + 1]
        if highs[i] == max(h_window) and h_window.count(highs[i]) == 1:
            swing_highs.append({"date": dates[i], "price": _round(highs[i])})
        if lows[i] == min(l_window) and l_window.count(lows[i]) == 1:
            swing_lows.append({"date": dates[i], "price": _round(lows[i])})
    # 保留最近 8 个（够波浪计数 1-2-3-4-5 + ABC = 8 个交替端点）
    return {"swing_highs": swing_highs[-8:], "swing_lows": swing_lows[-8:]}


def _alternating_pivots(swing_highs: list[dict], swing_lows: list[dict]) -> list[dict[str, Any]]:
    """把 Swing 高低点合并为时间序列，并过滤连续同向（保留更极端的）。"""
    all_pivots: list[dict[str, Any]] = []
    for h in swing_highs:
        all_pivots.append({"date": str(h.get("date", "")), "price": float(h.get("price", 0.0)), "kind": "H"})
    for l in swing_lows:
        all_pivots.append({"date": str(l.get("date", "")), "price": float(l.get("price", 0.0)), "kind": "L"})
    all_pivots.sort(key=lambda x: x["date"])

    alternated: list[dict[str, Any]] = []
    for p in all_pivots:
        if not alternated:
            alternated.append(p)
            continue
        last = alternated[-1]
        if p["kind"] == last["kind"]:
            # 同向：保留更极端的
            if p["kind"] == "H" and p["price"] > last["price"]:
                alternated[-1] = p
            elif p["kind"] == "L" and p["price"] < last["price"]:
                alternated[-1] = p
        else:
            alternated.append(p)
    return alternated


def _wave_unclear(reason: str) -> dict[str, Any]:
    """波浪结构不清晰时的默认返回。"""
    return {
        "current_wave": "unclear",
        "current_wave_label": "波浪结构不清晰",
        "wave_phase": "unknown",
        "confidence": "low",
        "reasoning": reason,
        "fibonacci_levels": [],
        "wave_rule_violations": [],
        "action": "wait",
        "action_label": "等待信号",
        "score_adjustment": 0,
        "wave_points": [],
    }


def _build_wave_analysis(
    index_frame: pd.DataFrame,
    levels: dict[str, Any],
    trend_summary: dict[str, Any],
) -> dict[str, Any]:
    """艾略特波浪理论分析（最小可用版）。

    基于近 120 日 Swing High/Low 识别当前所处浪型。
    不做严格波浪计数，只识别最常见的 6 种情况：
    第2浪回调（黄金坑）/ 第3浪推动 / 第4浪回调 / 第5浪末端 /
    ABC 调整中 / 调整结束新推动。
    """
    if index_frame.empty:
        return _wave_unclear("数据不足。")

    latest = index_frame.iloc[-1]
    close = _float(latest.get("close"), 0.0)
    amount_ratio = _float(latest.get("amount_ratio_5"), 1.0)

    swing_highs = levels.get("swing_highs", [])
    swing_lows = levels.get("swing_lows", [])
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return _wave_unclear("近 120 日 Swing 点不足以推导波浪结构，建议结合外部分析文档。")

    pivots = _alternating_pivots(swing_highs, swing_lows)
    if len(pivots) < 4:
        return _wave_unclear(f"交替端点仅 {len(pivots)} 个，至少需要 4 个端点。")

    # 取最近 5 个交替端点
    recent = pivots[-5:] if len(pivots) >= 5 else pivots
    if len(recent) == 5:
        p1, p2, p3, p4, p5 = recent
    else:  # 4 个
        p1, p2, p3, p4 = recent
        p5 = None

    # ── 上行序列：L → H → L → H → (L)，可能是 1-2-3-4 或 1-2-3-4-5 ──
    if p1["kind"] == "L" and p2["kind"] == "H" and p3["kind"] == "L" and p4["kind"] == "H":
        w1_start, w1_end = p1["price"], p2["price"]
        w2_end, w3_end = p3["price"], p4["price"]
        w1_len = w1_end - w1_start
        w3_len = w3_end - w2_end

        if w1_len <= 0 or w3_len <= 0:
            return _wave_unclear("Swing 端点价差异常，波浪结构无效。")

        violations: list[str] = []
        if w2_end < w1_start:
            violations.append(f"第2浪低点 {w2_end:.2f} 跌破第1浪起点 {w1_start:.2f}，违反波浪规则")

        # 第3浪不是最短浪（与第1浪比）：A股波浪常见情况
        wave1_shorter_than_wave3 = w1_len <= w3_len

        # case A: 已识别 4 个端点 + 第5个（low）→ 在第4浪或第5浪
        if p5 is not None and p5["kind"] == "L":
            w4_end = p5["price"]
            w4_pullback = (w3_end - w4_end) / w3_len if w3_len > 0 else 0

            if w4_end < w1_end:
                violations.append(f"第4浪低点 {w4_end:.2f} 侵入第1浪高点 {w1_end:.2f}，疑似 ABC 调整而非推动浪")

            if close > w4_end:
                # 第5浪上行中
                # 第5浪目标：常见 = 第1浪长度，或第1-3浪总幅 0.618
                w5_target_a = w3_end + w1_len * 0.618
                w5_target_b = w3_end + w1_len * 1.0
                # 量能减弱判定（第5浪 vs 第3浪平均量）
                wave_phase_note = "量能配合不足" if amount_ratio < 1.0 else "量能尚可"
                current_wave = "wave_5"
                current_wave_label = "第5浪上行（末端，警惕顶背离）"
                action = "exit_at_target"
                action_label = "止盈区，避免追高"
                score_adjustment = -10
                confidence = "medium" if not violations else "low"
                reasoning = (
                    f"识别到完整 1-2-3-4 浪结构（{w1_start:.0f} → {w1_end:.0f} → {w2_end:.0f} → "
                    f"{w3_end:.0f} → {w4_end:.0f}），当前 {close:.2f} 已突破第4浪低点，进入第5浪。"
                    f"第3浪长 {w3_len:.0f}（{'>' if wave1_shorter_than_wave3 else '<='}第1浪 {w1_len:.0f}）。{wave_phase_note}。"
                )
                fib_levels = [
                    {"label": "第5浪 0.618 目标位", "level": _round(w5_target_a), "kind": "target"},
                    {"label": "第5浪 1.0 目标位（与第1浪等长）", "level": _round(w5_target_b), "kind": "target"},
                    {"label": f"第4浪低点（破位 = 第5浪失败）", "level": _round(w4_end), "kind": "support"},
                    {"label": f"第1浪高点（核心保护位）", "level": _round(w1_end), "kind": "support"},
                ]
                return _wave_result(
                    current_wave, current_wave_label, "impulse", confidence, reasoning,
                    fib_levels, violations, action, action_label, score_adjustment,
                    [p1, p2, p3, p4, p5], ["wave_1_start", "wave_1_end", "wave_2_end", "wave_3_end", "wave_4_end"],
                )
            else:
                # 还在第4浪回调中
                current_wave = "wave_4"
                current_wave_label = f"第4浪调整中（回撤 {w4_pullback*100:.0f}%）"
                if 0.236 <= w4_pullback <= 0.382:
                    action, action_label = "buy_on_pullback", "第4浪买点（次优）"
                    score_adjustment = 8
                    confidence = "medium" if not violations else "low"
                elif w4_pullback > 0.618:
                    action, action_label = "wait", "第4浪回撤过深，疑似计数失效"
                    score_adjustment = -5
                    confidence = "low"
                else:
                    action, action_label = "wait", "等待第4浪企稳"
                    score_adjustment = 0
                    confidence = "low"
                reasoning = (
                    f"识别到 1-2-3 推动浪（{w1_start:.0f} → {w1_end:.0f} → {w2_end:.0f} → {w3_end:.0f}），"
                    f"第3浪长 {w3_len:.0f}。当前回踩至 {w4_end:.0f}，回撤 {w4_pullback*100:.0f}%。"
                )
                fib_0382 = w3_end - w3_len * 0.382
                fib_0500 = w3_end - w3_len * 0.500
                fib_0618 = w3_end - w3_len * 0.618
                fib_levels = [
                    {"label": "第3浪 0.382 回撤（理想买点）", "level": _round(fib_0382), "kind": "support"},
                    {"label": "第3浪 0.500 回撤", "level": _round(fib_0500), "kind": "support"},
                    {"label": "第3浪 0.618 回撤（极限）", "level": _round(fib_0618), "kind": "support"},
                    {"label": "第1浪高点（不得跌破）", "level": _round(w1_end), "kind": "support"},
                    {"label": "第3浪高点（突破 = 第5浪开始）", "level": _round(w3_end), "kind": "resistance"},
                ]
                return _wave_result(
                    current_wave, current_wave_label, "corrective", confidence, reasoning,
                    fib_levels, violations, action, action_label, score_adjustment,
                    [p1, p2, p3, p4, p5], ["wave_1_start", "wave_1_end", "wave_2_end", "wave_3_end", "wave_4_end (current)"],
                )

        # case B: 只识别到 4 个端点（没有 p5），在第3浪或第4浪初期
        else:
            # 当前位置判断
            w3_target_1618 = w1_end + w1_len * 1.618
            if close >= w3_end:
                # 第3浪进行中（在 wave3 end 之上）或刚结束开始第4浪
                w3_progress = (close - w2_end) / w3_len if w3_len > 0 else 0
                if w3_progress < 0.5:
                    current_wave = "wave_3_early"
                    current_wave_label = "第3浪初期"
                    action, action_label = "buy_on_pullback", "第3浪放量突破买点"
                    score_adjustment = 12
                    confidence = "medium" if amount_ratio >= 1.2 else "low"
                elif w3_progress < 1.0:
                    current_wave = "wave_3_mid"
                    current_wave_label = "第3浪中段"
                    action, action_label = "hold", "持有，等待第3浪末或第4浪回调"
                    score_adjustment = 5
                    confidence = "medium"
                else:
                    current_wave = "wave_3_late"
                    current_wave_label = "第3浪末段（警惕第4浪开始）"
                    action, action_label = "wait", "止盈或减仓"
                    score_adjustment = 0
                    confidence = "low"
                reasoning = (
                    f"识别到 1-2-3 浪推动结构（{w1_start:.0f} → {w1_end:.0f} → {w2_end:.0f} → {w3_end:.0f}），"
                    f"第3浪长 {w3_len:.0f}（vs 第1浪 {w1_len:.0f}）。"
                    f"量比 {amount_ratio:.1f}x。"
                )
                fib_levels = [
                    {"label": "第3浪 1.618 目标位", "level": _round(w3_target_1618), "kind": "target"},
                    {"label": "第3浪高点", "level": _round(w3_end), "kind": "resistance"},
                    {"label": "第1浪高点（核心保护）", "level": _round(w1_end), "kind": "support"},
                    {"label": "第2浪低点（破位则计数失效）", "level": _round(w2_end), "kind": "support"},
                ]
                return _wave_result(
                    current_wave, current_wave_label, "impulse", confidence, reasoning,
                    fib_levels, violations, action, action_label, score_adjustment,
                    [p1, p2, p3, p4], ["wave_1_start", "wave_1_end", "wave_2_end", "wave_3_end"],
                )
            elif close < w2_end:
                violations.append(f"当前价 {close:.2f} 已跌破第2浪低点 {w2_end:.2f}，原计数失效")
                return _wave_unclear(
                    f"原计数 1-2 浪（{w1_start:.0f} → {w1_end:.0f} → {w2_end:.0f}）已失效，"
                    f"当前 {close:.2f} 跌破第2浪低点，可能是 ABC 调整或下行结构。"
                )
            else:
                # 在 wave1_end ~ wave3_end 之间，向下回看，可能是第4浪初期
                w4_pullback = (w3_end - close) / w3_len if w3_len > 0 else 0
                current_wave = "wave_4"
                current_wave_label = f"第4浪初期回调（已回撤 {w4_pullback*100:.0f}%）"
                if 0.236 <= w4_pullback <= 0.382:
                    action, action_label = "buy_on_pullback", "第4浪买点（次优）"
                    score_adjustment = 8
                    confidence = "medium"
                else:
                    action, action_label = "wait", "等待第4浪企稳到 0.382 黄金回撤"
                    score_adjustment = 0
                    confidence = "low"
                reasoning = (
                    f"识别到 1-2-3 推动浪（{w1_start:.0f} → {w1_end:.0f} → {w2_end:.0f} → {w3_end:.0f}），"
                    f"当前 {close:.2f} 在第3浪高点下方，进入第4浪初期回调。"
                )
                fib_0382 = w3_end - w3_len * 0.382
                fib_0500 = w3_end - w3_len * 0.500
                fib_0618 = w3_end - w3_len * 0.618
                fib_levels = [
                    {"label": "第3浪 0.382 回撤（理想买点）", "level": _round(fib_0382), "kind": "support"},
                    {"label": "第3浪 0.500 回撤", "level": _round(fib_0500), "kind": "support"},
                    {"label": "第3浪 0.618 回撤（极限）", "level": _round(fib_0618), "kind": "support"},
                    {"label": "第1浪高点（不得跌破）", "level": _round(w1_end), "kind": "support"},
                    {"label": "第3浪高点（突破 = 第5浪开始）", "level": _round(w3_end), "kind": "resistance"},
                ]
                return _wave_result(
                    current_wave, current_wave_label, "corrective", confidence, reasoning,
                    fib_levels, violations, action, action_label, score_adjustment,
                    [p1, p2, p3, p4], ["wave_1_start", "wave_1_end", "wave_2_end", "wave_3_end"],
                )

    # ── 部分序列：L → H → L（仅3 个端点，可能在第2浪回调）──
    # 这个分支用 alternated 序列里最后 3 个端点判断
    if len(pivots) >= 3:
        q1, q2, q3 = pivots[-3:]
        if q1["kind"] == "L" and q2["kind"] == "H" and q3["kind"] == "L":
            w1_start, w1_end, w2_end = q1["price"], q2["price"], q3["price"]
            w1_len = w1_end - w1_start
            if w1_len > 0:
                violations: list[str] = []
                if w2_end < w1_start:
                    violations.append(f"第2浪低点 {w2_end:.2f} 跌破第1浪起点，违反波浪规则")
                if close >= w1_end:
                    # 已突破第1浪高点 → 进入第3浪
                    pass  # 这种情况会被上面 4 端点分支吸收，此处不会到达
                elif close > w2_end:
                    # 在第2浪回调中（close 在 w2_end ~ w1_end 之间）
                    w2_pullback = (w1_end - close) / w1_len if w1_len > 0 else 0
                    current_wave = "wave_2"
                    current_wave_label = f"第2浪回调中（回撤 {w2_pullback*100:.0f}%）"
                    if 0.382 <= w2_pullback <= 0.618:
                        action, action_label = "buy_on_pullback", "黄金坑！第2浪最优买点"
                        score_adjustment = 15
                        confidence = "medium"
                    elif 0.618 < w2_pullback < 1.0:
                        action, action_label = "buy_on_pullback", "深度回调买点（接近 1.0 极限）"
                        score_adjustment = 8
                        confidence = "low"
                    else:
                        action, action_label = "wait", "等待第2浪回撤到 0.382 以下"
                        score_adjustment = 0
                        confidence = "low"
                    reasoning = (
                        f"识别到第1浪（{w1_start:.0f} → {w1_end:.0f}），第2浪正在回调，"
                        f"当前 {close:.2f}，回撤 {w2_pullback*100:.0f}%。"
                    )
                    fib_0382 = w1_end - w1_len * 0.382
                    fib_0500 = w1_end - w1_len * 0.500
                    fib_0618 = w1_end - w1_len * 0.618
                    fib_levels = [
                        {"label": "0.382 回撤（理想买点）", "level": _round(fib_0382), "kind": "support"},
                        {"label": "0.500 回撤", "level": _round(fib_0500), "kind": "support"},
                        {"label": "0.618 回撤（黄金极限）", "level": _round(fib_0618), "kind": "support"},
                        {"label": "第1浪起点（破位 = 计数失效）", "level": _round(w1_start), "kind": "support"},
                        {"label": "第1浪高点（突破 = 第3浪开始）", "level": _round(w1_end), "kind": "resistance"},
                    ]
                    return _wave_result(
                        "wave_2", current_wave_label, "corrective", confidence, reasoning,
                        fib_levels, violations, action, action_label, score_adjustment,
                        [q1, q2, q3], ["wave_1_start", "wave_1_end", "wave_2_end (current)"],
                    )

    # ── 下行序列：H → L → H → L，可能是 A-B-C 调整 ──
    if p1["kind"] == "H" and p2["kind"] == "L" and p3["kind"] == "H" and p4["kind"] == "L":
        wa_start, wa_end = p1["price"], p2["price"]
        wb_end, wc_end = p3["price"], p4["price"]
        wa_len = wa_start - wa_end
        if wa_len <= 0:
            return _wave_unclear("ABC 波形端点价差异常。")
        violations: list[str] = []
        if wb_end > wa_start:
            violations.append(f"B浪高点 {wb_end:.2f} 超过 A浪起点 {wa_start:.2f}，可能是新一轮推动")
        wc_target_equal_a = wb_end - wa_len  # C浪目标 = A浪长度
        if close < wc_end:
            current_wave = "wave_c"
            current_wave_label = "C浪延伸下跌中"
            action, action_label = "avoid", "避免抄底"
            score_adjustment = -12
            confidence = "medium" if not violations else "low"
            reasoning = (
                f"识别到 A-B 调整结构（A：{wa_start:.0f} → {wa_end:.0f}，B：→ {wb_end:.0f}），"
                f"当前 {close:.2f} 已跌破 C浪低点 {wc_end:.0f}，C浪延伸下跌。"
            )
        elif close > wb_end:
            current_wave = "wave_1_new"
            current_wave_label = "调整结束，新一轮推动浪开始（需确认）"
            action, action_label = "buy_on_pullback", "新推动浪可能开始"
            score_adjustment = 10
            confidence = "low"
            reasoning = (
                f"识别到 A-B-C 调整可能完成（{wa_start:.0f} → {wa_end:.0f} → {wb_end:.0f} → {wc_end:.0f}），"
                f"当前 {close:.2f} 已突破 B浪高点，疑似新推动浪。"
            )
        else:
            current_wave = "wave_c"
            current_wave_label = "C浪反弹中（陷阱风险）"
            action, action_label = "wait", "等待 C浪完成或突破 B浪高点"
            score_adjustment = -5
            confidence = "low"
            reasoning = (
                f"识别到 A-B-C 调整（{wa_start:.0f} → {wa_end:.0f} → {wb_end:.0f} → {wc_end:.0f}），"
                f"当前 {close:.2f} 在 C浪反弹区。"
            )
        fib_levels = [
            {"label": "C浪目标位（= A浪长度）", "level": _round(wc_target_equal_a), "kind": "support"},
            {"label": "C浪低点", "level": _round(wc_end), "kind": "support"},
            {"label": "B浪高点（突破 = 调整结束）", "level": _round(wb_end), "kind": "resistance"},
            {"label": "A浪起点（不得超过）", "level": _round(wa_start), "kind": "resistance"},
        ]
        return _wave_result(
            current_wave, current_wave_label, "corrective", confidence, reasoning,
            fib_levels, violations, action, action_label, score_adjustment,
            [p1, p2, p3, p4], ["A浪起点", "A浪低点", "B浪高点", "C浪低点"],
        )

    return _wave_unclear("当前 Swing 序列方向不明（既非完整推动 1-4 也非 ABC 调整）。")


def _wave_result(
    current_wave: str,
    current_wave_label: str,
    wave_phase: str,
    confidence: str,
    reasoning: str,
    fibonacci_levels: list[dict[str, Any]],
    violations: list[str],
    action: str,
    action_label: str,
    score_adjustment: int,
    pivots: list[dict[str, Any]],
    pivot_labels: list[str],
) -> dict[str, Any]:
    """构造统一的波浪分析返回值。"""
    wave_points = [
        {"label": pivot_labels[i] if i < len(pivot_labels) else f"point_{i}",
         "price": _round(p["price"]),
         "date": p["date"],
         "kind": p["kind"]}
        for i, p in enumerate(pivots)
    ]
    return {
        "current_wave": current_wave,
        "current_wave_label": current_wave_label,
        "wave_phase": wave_phase,
        "confidence": confidence,
        "reasoning": reasoning,
        "fibonacci_levels": fibonacci_levels,
        "wave_rule_violations": violations,
        "action": action,
        "action_label": action_label,
        "score_adjustment": score_adjustment,
        "wave_points": wave_points,
    }


def _market_trend_summary(latest: pd.Series) -> dict[str, Any]:
    """根据 MA50/MA120 斜率判断中长期趋势方向。"""
    ma50_slope = _float(latest.get("ma50_slope_5d"), 0.0)
    ma120_slope = _float(latest.get("ma120_slope_5d"), 0.0)
    close = _float(latest.get("close"), 0.0)
    ma50 = _float(latest.get("ma50"), 0.0)
    ma120 = _float(latest.get("ma120"), 0.0)

    # 中期趋势：用 MA50 斜率和位置
    if ma50 > 0 and close > ma50 and ma50_slope > 0:
        mid_trend = "up"
        mid_label = "中期上行"
    elif ma50 > 0 and close < ma50 and ma50_slope < 0:
        mid_trend = "down"
        mid_label = "中期下行"
    else:
        mid_trend = "range"
        mid_label = "中期震荡"

    # 长期趋势：用 MA120 斜率和位置
    if ma120 > 0 and close > ma120 and ma120_slope > 0:
        long_trend = "up"
        long_label = "长期上行"
    elif ma120 > 0 and close < ma120 and ma120_slope < 0:
        long_trend = "down"
        long_label = "长期下行"
    elif ma120 == 0:
        long_trend = "unknown"
        long_label = "长期数据不足"
    else:
        long_trend = "range"
        long_label = "长期震荡"

    return {
        "mid_trend": mid_trend,
        "mid_label": mid_label,
        "long_trend": long_trend,
        "long_label": long_label,
        "ma50": _round(ma50),
        "ma120": _round(ma120),
        "ma50_slope_5d": _round(ma50_slope, 4),
        "ma120_slope_5d": _round(ma120_slope, 4),
    }


def _build_support_pressure(index_frame: pd.DataFrame) -> dict[str, Any]:
    latest = index_frame.iloc[-1]
    previous = index_frame.iloc[-2] if len(index_frame) >= 2 else latest
    close = _float(latest.get("close"), 0.0)

    # 基础均线候选
    ma_candidates = [
        ("MA5", _float(previous.get("ma5"), 0.0)),
        ("MA10", _float(previous.get("ma10"), 0.0)),
        ("MA20", _float(previous.get("ma20"), 0.0)),
        ("MA50", _float(previous.get("ma50"), 0.0)),
        ("MA120", _float(previous.get("ma120"), 0.0)),
    ]
    # 区间高低点
    range_candidates = [
        ("20日低点", _float(previous.get("rolling_low_20"), 0.0)),
        ("20日高点", _float(previous.get("rolling_high_20"), 0.0)),
    ]
    # Swing 高低点（近 120 日识别）
    swings = _detect_swing_points(index_frame, window=5, lookback=120)
    swing_candidates: list[tuple[str, float]] = []
    for sh in swings["swing_highs"]:
        swing_candidates.append((f"Swing高({sh['date']})", _float(sh["price"], 0.0)))
    for sl in swings["swing_lows"]:
        swing_candidates.append((f"Swing低({sl['date']})", _float(sl["price"], 0.0)))

    all_candidates = [c for c in ma_candidates + range_candidates + swing_candidates if c[1] > 0]
    below = sorted([c for c in all_candidates if c[1] <= close], key=lambda x: x[1], reverse=True)
    above = sorted([c for c in all_candidates if c[1] > close], key=lambda x: x[1])

    # 选取：第一支撑（最近下方）、第二支撑（其次）、压力（最近上方）
    fallback = ("MA5", _float(previous.get("ma5"), close))
    support_1 = below[0] if below else fallback
    support_2 = below[1] if len(below) >= 2 else (below[0] if below else fallback)
    pressure_1 = above[0] if above else ("20日高点", _float(previous.get("rolling_high_20"), close))

    return {
        "support_1": _round(support_1[1]),
        "support_1_source": support_1[0],
        "support_2": _round(support_2[1]),
        "support_2_source": support_2[0],
        "pressure_1": _round(pressure_1[1]),
        "pressure_1_source": pressure_1[0],
        "all_levels_below": [{"source": n, "level": _round(v)} for n, v in below[:5]],
        "all_levels_above": [{"source": n, "level": _round(v)} for n, v in above[:5]],
        "swing_highs": swings["swing_highs"],
        "swing_lows": swings["swing_lows"],
        "definition": (
            "v2 自动位：候选包含 MA5/MA10/MA20/MA50/MA120 + 20日高低点 + 近120日 Swing High/Low；"
            "选取低于当前价最近的两档为支撑、上方最近为压力。允许人工覆盖。"
        ),
    }


def _score_trend(latest: pd.Series, levels: dict[str, Any]) -> dict[str, Any]:
    close = _float(latest.get("close"), 0.0)
    ma5 = _float(latest.get("ma5"), close)
    ma10 = _float(latest.get("ma10"), close)
    ma20 = _float(latest.get("ma20"), close)
    score = 0
    evidence: list[str] = []
    if close > ma5 and ma5 > ma10:
        score += 2
        evidence.append("趋势分 +2：指数站上 MA5，且 MA5 > MA10，短线趋势向上。")
    if close > ma10 and ma10 > ma20:
        score += 2
        evidence.append("趋势分 +2：指数站上 MA10，且 MA10 > MA20，中短趋势未破。")
    if ma5 > ma10 > ma20:
        score += 2
        evidence.append("趋势分 +2：MA5/MA10/MA20 多头排列。")
    if close < ma5 and close >= ma10:
        score -= 1
        evidence.append("趋势分 -1：指数跌破 MA5 但仍在 MA10 上方，短线转弱但未破结构。")
    if close < ma10:
        score -= 2
        evidence.append("趋势分 -2：指数跌破 MA10，交易权限需要降级检查。")
    if close < ma20:
        score -= 3
        evidence.append("趋势分 -3：指数跌破 MA20，中期结构转弱。")
    support_1 = _float(levels.get("support_1"), close)
    if close < support_1:
        score -= 2
        evidence.append(f"趋势分 -2：指数跌破第一支撑 {support_1:.2f}。")
    return {"score": int(score), "evidence": evidence or ["趋势分 0：均线结构中性。"]}


def _score_volume(latest: pd.Series) -> dict[str, Any]:
    amount_ratio_5 = _float(latest.get("amount_ratio_5"), 1.0)
    pct_chg = _float(latest.get("pct_chg"), 0.0)
    upper_shadow_ratio = _float(latest.get("upper_shadow_ratio"), 0.0)
    score = 0
    evidence: list[str] = []
    if 1.05 <= amount_ratio_5 <= 1.30 and pct_chg > 0:
        score += 2
        evidence.append("量能分 +2：成交额相对 MA5 为 1.05-1.30 且指数上涨，属于健康放量。")
    elif 0.85 <= amount_ratio_5 < 1.05 and pct_chg >= -0.005:
        score += 1
        evidence.append("量能分 +1：成交额接近 MA5，指数小涨或震荡，属于健康震荡。")
    if amount_ratio_5 > 1.50 and pct_chg > 0:
        score += 1
        evidence.append("量能分 +1：指数大幅放量上涨，但只按加速处理，不能盲目加仓。")
    if amount_ratio_5 > 1.50 and upper_shadow_ratio > 0.45:
        score -= 2
        evidence.append("量能分 -2：爆量且上影线偏长，疑似放量滞涨或兑现。")
    if amount_ratio_5 > 1.30 and pct_chg < 0:
        score -= 3
        evidence.append("量能分 -3：成交额放大但指数下跌，属于放量下跌。")
    if amount_ratio_5 < 0.80 and pct_chg > 0:
        score -= 1
        evidence.append("量能分 -1：缩量上涨，持续性打折。")
    if amount_ratio_5 < 0.80 and pct_chg < 0:
        evidence.append("量能分 0：缩量回踩，不直接空仓，等待支撑确认。")
    return {"score": int(score), "evidence": evidence or ["量能分 0：成交额温度中性。"]}


def _score_breadth(breadth: dict[str, Any]) -> dict[str, Any]:
    metrics = breadth["metrics"]
    up_count = int(metrics["up_count"])
    up_count_ratio_ma5 = _float(metrics["up_count_ratio_ma5"], 1.0)
    limit_down_count = int(metrics["limit_down_count"])
    limit_down_ma5 = _float(metrics["limit_down_count_ma5"], 0.0)
    score = 0
    evidence: list[str] = []
    if up_count >= 3000 and up_count_ratio_ma5 > 1.10:
        score += 2
        evidence.append("广度分 +2：上涨家数 >=3000 且显著高于过去5日均值，赚钱效应扩散。")
    elif up_count >= 2500 and up_count_ratio_ma5 >= 1.00:
        score += 1
        evidence.append("广度分 +1：上涨家数 >=2500 且不弱于过去5日均值。")
    elif 1800 <= up_count < 2500:
        evidence.append("广度分 0：上涨家数处于 1800-2500，中性震荡。")
    elif up_count < 1200:
        score -= 2
        evidence.append("广度分 -2：上涨家数 <1200，市场广度极差。")
    elif up_count < 1800:
        score -= 1
        evidence.append("广度分 -1：上涨家数 <1800，赚钱效应不足。")
    if limit_down_count > max(3.0, limit_down_ma5 * 1.5):
        score -= 2
        evidence.append("广度分 -2：跌停家数显著高于过去5日均值，情绪风险扩散。")
    return {"score": int(score), "evidence": evidence}


def _score_risk(
    *,
    latest_index: pd.Series,
    levels: dict[str, Any],
    breadth: dict[str, Any],
    theme_score: int,
    weight_support_flag: bool,
) -> dict[str, Any]:
    close = _float(latest_index.get("close"), 0.0)
    amount_ratio_5 = _float(latest_index.get("amount_ratio_5"), 1.0)
    upper_shadow_ratio = _float(latest_index.get("upper_shadow_ratio"), 0.0)
    support_1 = _float(levels.get("support_1"), close)
    support_2 = _float(levels.get("support_2"), close)
    limit_down_count = int(breadth["metrics"]["limit_down_count"])
    limit_down_ma5 = _float(breadth["metrics"]["limit_down_count_ma5"], 0.0)
    score = 0
    evidence: list[str] = []
    if close < support_1 and amount_ratio_5 > 1.30:
        score += 3
        evidence.append("风险分 +3：放量跌破第一支撑。")
    if close < support_2:
        score += 4
        evidence.append("风险分 +4：跌破第二支撑。")
    if theme_score <= -3:
        score += 3
        evidence.append("风险分 +3：主线核心代理指标显示集体破位或退潮。")
    if limit_down_count > max(5.0, limit_down_ma5 * 1.5):
        score += 2
        evidence.append("风险分 +2：跌停家数快速增加。")
    if amount_ratio_5 > 1.50 and upper_shadow_ratio > 0.45:
        score += 2
        evidence.append("风险分 +2：指数冲高回落且成交额显著放大。")
    if weight_support_flag:
        score += 2
        evidence.append("风险分 +2：疑似权重护盘但题材扩散不足。")
    return {"score": int(score), "evidence": evidence or ["风险分 0：未触发硬风险代理项。"]}


def _build_hard_triggers(
    *,
    latest_index: pd.Series,
    levels: dict[str, Any],
    breadth: dict[str, Any],
    theme_score: int,
    risk_score: int,
    amount_ratio_5: float,
) -> list[dict[str, str]]:
    close = _float(latest_index.get("close"), 0.0)
    ma20 = _float(latest_index.get("ma20"), close)
    support_2 = _float(levels.get("support_2"), close)
    up_count = int(breadth["metrics"]["up_count"])
    limit_down_count = int(breadth["metrics"]["limit_down_count"])
    triggers: list[dict[str, str]] = []
    if close < support_2 and amount_ratio_5 > 1.30:
        triggers.append({"target_state": "empty", "reason": "放量跌破第二支撑。"})
    if theme_score <= -3:
        triggers.append({"target_state": "empty", "reason": "主线代理指标显示核心集体破位或退潮。"})
    if close < ma20 and amount_ratio_5 > 1.30:
        triggers.append({"target_state": "empty", "reason": "指数跌破 MA20 且成交额放大。"})
    if up_count < 1200 and limit_down_count >= 10:
        triggers.append({"target_state": "empty", "reason": "上涨家数极低且跌停数量显著。"})
    if risk_score >= 3 and not triggers:
        triggers.append({"target_state": "defense", "reason": "风险分 >=3，先降级防守。"})
    return triggers


def _classify_permission(
    *,
    total_score: int,
    trend_score: int,
    breadth_score: int,
    theme_score: int,
    risk_score: int,
    hard_triggers: list[dict[str, str]],
) -> str:
    hard_states = {trigger["target_state"] for trigger in hard_triggers}
    if "empty" in hard_states:
        return "empty"
    if risk_score <= 1 and trend_score >= 3 and breadth_score >= 1 and theme_score >= 1 and total_score >= 5:
        return "attack"
    if "defense" in hard_states:
        return "defense"
    if 1 <= total_score <= 4 and risk_score <= 2:
        return "hold"
    if -3 <= total_score <= 0 or risk_score >= 3:
        return "defense"
    if total_score < -3:
        return "empty"
    return "hold"


def _state_machine_spec() -> dict[str, Any]:
    return {
        "formula": "total_score = trend_score + volume_score + breadth_score + theme_score - risk_score",
        "hard_veto": "硬否决优先于加权确认；风险可以跳级降级，机会只能逐级确认。",
        "states": {
            "attack": {
                "definition": "指数、成交额、市场广度、主线质量共振，且没有硬风险。",
                "math": "risk_score <= 1 and trend_score >= 3 and breadth_score >= 1 and theme_score >= 1 and total_score >= 5",
                "meaning": "可执行计划内买点，优先主线龙头/中军；核心仓仍需失效位反推。",
            },
            "hold": {
                "definition": "趋势未明显破坏，但量能、广度或主线存在分歧。",
                "math": "1 <= total_score <= 4 and risk_score <= 2",
                "meaning": "有先手观察，无先手不追，只等支撑低吸确认或处理持仓。",
            },
            "defense": {
                "definition": "风险升高但尚未彻底破坏，或出现权重护盘/主线分歧。",
                "math": "-3 <= total_score <= 0 or risk_score >= 3",
                "meaning": "降低仓位，禁止新增重仓，高位持仓进入利润保护或风险升级。",
            },
            "empty": {
                "definition": "放量破位、主线退潮或市场广度极差。",
                "math": "hard empty trigger or total_score < -3",
                "meaning": "禁止开仓，只允许止损、降仓、等待止跌。",
            },
        },
    }


def _implementation_status() -> dict[str, dict[str, str]]:
    return {
        "trend_score": {"status": "implemented", "note": "指数均线、支撑、压力已接入。"},
        "volume_score": {"status": "implemented", "note": "成交额 MA5/MA20 与爆量上影已接入。"},
        "breadth_score": {"status": "implemented", "note": "上涨/下跌家数由全市场日线横截面计算。"},
        "theme_score": {"status": "proxy_only", "note": "当前用行业聚合代理主线质量，后续接 Part2。"},
        "risk_score": {"status": "implemented", "note": "放量破位、跌停扩散、权重护盘代理已接入。"},
        "core_stock_feedback": {"status": "manual_only/proxy_pending", "note": "等待 A池龙头/中军反馈接入。"},
        "api": {"status": "implemented", "note": "GET /api/v1/market/permission。"},
        "ui": {"status": "implemented", "note": "控制台新增 PART1 大盘权限卡片。"},
        "rust_core": {"status": "skeleton", "note": "shilun-core 已固化权限枚举与分类函数。"},
    }


def _action_permission(permission: str) -> dict[str, str]:
    mapping = {
        "attack": {
            "can_open": "yes",
            "max_new_position": "standard_or_core_by_risk_formula",
            "text": "可执行计划内买点，优先主线龙头/中军；不追加速末端。",
        },
        "hold": {
            "can_open": "limited",
            "max_new_position": "watch_or_low_standard",
            "text": "有先手观察，无先手不追；只看 A池核心回踩确认。",
        },
        "defense": {
            "can_open": "no_heavy_new_position",
            "max_new_position": "watch_only",
            "text": "降低仓位，只处理持仓；原则上不开新重仓。",
        },
        "empty": {
            "can_open": "no",
            "max_new_position": "zero",
            "text": "禁止开仓，只允许止损、降仓、等待止跌。",
        },
    }
    return mapping[permission]


def _permission_label(permission: str) -> str:
    return {
        "attack": "进攻",
        "hold": "持有",
        "defense": "防守",
        "empty": "空仓",
    }.get(permission, permission)


# ─────────────────────────────────────────────────────────────────────────────
# Market Gate（机器可执行的大盘闸门规则）
# 输出会被 PART3 消费用于降级候选、控制仓位建议。
# 与 permission/action_permission 分工：
#   permission        = 结论字段（展示用："进攻/持有/防守/空仓"）
#   action_permission = 人类可读文字（展示用）
#   market_gate       = 机器可执行字段（PART3 直接读，无需解析字符串）
# ─────────────────────────────────────────────────────────────────────────────

# 四种 gate 状态的固化规则表（关键字段）
_MARKET_GATE_RULES: dict[str, dict[str, Any]] = {
    "attack": {
        "allow_new_position": True,
        "allow_add_position": True,
        "market_multiplier": 1.2,
        "size_hint": 1.0,
        "signal_downgrade_map": {
            "breakout_confirm": "breakout_confirm",
            "pullback_to_ma5": "pullback_to_ma5",
            "gentle_rise": "gentle_rise",
            "watch": "watch",
        },
        "high_quality_exception": None,
        "holdings_advice": {
            "check_stop_loss": False,
            "reduce_percentage": None,
            "note": "正常持仓，遵守个股止损即可。可择机加仓强趋势品种。",
        },
    },
    "hold": {
        "allow_new_position": True,
        "allow_add_position": True,
        "market_multiplier": 1.0,
        "size_hint": 0.5,  # 均衡半仓
        "signal_downgrade_map": {
            "breakout_confirm": "breakout_confirm",
            "pullback_to_ma5": "pullback_to_ma5",
            "gentle_rise": "watch",  # 缩量上涨在持有状态下降级
            "watch": "watch",
        },
        "high_quality_exception": None,
        "holdings_advice": {
            "check_stop_loss": False,
            "reduce_percentage": None,
            "note": "正常持仓，谨慎开新仓，建议半仓单位。",
        },
    },
    "defense": {
        "allow_new_position": False,
        "allow_add_position": False,
        "market_multiplier": 0.7,
        "size_hint": 0.0,
        "signal_downgrade_map": {
            "breakout_confirm": "watch",
            "pullback_to_ma5": "watch",
            "gentle_rise": "watch",
            "watch": "watch",
        },
        # 防守下的例外：极高质量突破仍允许小仓
        "high_quality_exception": {
            "enabled": True,
            "condition": "entry_quality >= 85 AND signal == breakout_confirm",
            "min_entry_quality": 85,
            "allowed_signals": ["breakout_confirm"],
            "override_size_hint": 0.3,
            "note": "防守下仅允许极高质量突破买点，仓位限制为 30%（三成仓试探）。",
        },
        "holdings_advice": {
            "check_stop_loss": True,
            "reduce_percentage": None,
            "note": "检查每只持仓的止损位（MA5 × 0.98），跌破需减仓；不加仓。",
        },
    },
    "empty": {
        "allow_new_position": False,
        "allow_add_position": False,
        "market_multiplier": 0.0,
        "size_hint": 0.0,
        "signal_downgrade_map": {
            "breakout_confirm": "watch",
            "pullback_to_ma5": "watch",
            "gentle_rise": "watch",
            "watch": "watch",
        },
        "high_quality_exception": None,
        "holdings_advice": {
            "check_stop_loss": True,
            "reduce_percentage": 0.5,
            "note": (
                "空仓 gate 已触发，建议：① 立即检查每只持仓的 MA20，跌破的减仓 50% 以上；"
                "② 涨停后未创新高的直接止盈；③ 破位低点的品种当日止损；"
                "④ 保留强势主线龙头仅当其未破 MA20。目标降低总仓位到 30% 以内。"
            ),
        },
    },
}


def _build_market_gate(
    *,
    permission: str,
    hard_triggers: list[dict[str, str]],
    scores: dict[str, int],
    metrics: dict[str, Any],
    breadth_evidence: list[str],
) -> dict[str, Any]:
    """把大盘结论转成机器可执行的闸门规则。

    优先级：硬否决 > permission 状态查表。
    """
    hard_states = {t.get("target_state") for t in hard_triggers}
    hard_veto_active = "empty" in hard_states

    # 硬否决直接锁 empty gate（不再受其他分数影响）
    if hard_veto_active:
        effective_state = "empty"
        rule = _MARKET_GATE_RULES["empty"]
        veto_reasons = "；".join(t.get("reason", "") for t in hard_triggers if t.get("target_state") == "empty")
        gate_reason = f"硬否决触发：{veto_reasons}"
    else:
        effective_state = permission
        rule = _MARKET_GATE_RULES.get(permission, _MARKET_GATE_RULES["hold"])
        gate_reason = _compose_gate_reason(permission, scores, metrics, breadth_evidence)

    return {
        "state": effective_state,
        "state_label": _permission_label(effective_state),
        "allow_new_position": rule["allow_new_position"],
        "allow_add_position": rule["allow_add_position"],
        "market_multiplier": rule["market_multiplier"],
        "size_hint": rule["size_hint"],
        "gate_reason": gate_reason,
        "hard_veto_active": hard_veto_active,
        "high_quality_exception": rule["high_quality_exception"],
        "signal_downgrade_map": rule["signal_downgrade_map"],
        "holdings_advice": rule["holdings_advice"],
    }


def _compose_gate_reason(
    permission: str,
    scores: dict[str, int],
    metrics: dict[str, Any],
    breadth_evidence: list[str],
) -> str:
    """根据当前状态、分数和关键指标合成 gate_reason 文本。"""
    trend = scores.get("trend_score", 0)
    volume = scores.get("volume_score", 0)
    breadth = scores.get("breadth_score", 0)
    theme = scores.get("theme_score", 0)
    risk = scores.get("risk_score", 0)

    up_ratio = _float(metrics.get("up_ratio"), 0.0)
    weight_flag = bool(metrics.get("weight_support_flag"))
    theme_status = str(metrics.get("main_theme_status", ""))

    reasons: list[str] = []

    if permission == "attack":
        reasons.append("大盘处于进攻，趋势/量能/广度/主线共振")
        if theme == 2: reasons.append("主线确认扩散")
        return "，".join(reasons) + "。"

    if permission == "hold":
        reasons.append("大盘处于持有")
        if trend >= 2 and breadth < 1:
            reasons.append("结构未破但广度不足")
        elif theme <= 0:
            reasons.append("主线未确认")
        elif volume <= 0:
            reasons.append("量能偏弱")
        else:
            reasons.append("信号不足以打开进攻")
        return "，".join(reasons) + "，有先手观察、无先手不追。"

    if permission == "defense":
        reasons.append("大盘处于防守")
        if risk >= 3: reasons.append(f"风险分 {risk} 偏高")
        if up_ratio < 0.30: reasons.append(f"市场广度不足（上涨占比 {up_ratio:.0%}）")
        if weight_flag: reasons.append("权重护盘触发")
        if theme_status == "index_up_theme_weak": reasons.append("指数涨但主线扩散不足")
        if trend < 0: reasons.append("指数结构转弱")
        return "；".join(reasons) + "。禁止主动进攻。"

    if permission == "empty":
        reasons.append("大盘处于空仓")
        if risk >= 5: reasons.append(f"风险分极高（{risk}）")
        if up_ratio < 0.20: reasons.append(f"广度极差（上涨占比 {up_ratio:.0%}）")
        return "；".join(reasons) + "。禁止开仓，只允许止损/减仓/等待止跌。"

    return f"大盘状态：{permission}。"


def _permission_summary(permission: str) -> str:
    return {
        "attack": "大盘、量能、广度和主线共振，交易权限打开。",
        "hold": "市场尚未破坏，但存在分歧；有先手观察，无先手不追。",
        "defense": "风险信号升高，交易权限降级为防守。",
        "empty": "硬风险触发，禁止开仓，等待止跌确认。",
    }.get(permission, "状态未知。")


def _data_quality(
    *,
    index_frame: pd.DataFrame,
    stock_frame: pd.DataFrame,
    stock_basic: pd.DataFrame | None,
) -> list[dict[str, str]]:
    items = [
        {
            "field": "index_daily_bars",
            "status": "implemented",
            "note": f"指数样本 {len(index_frame)} 条，用于趋势、量能、支撑压力。",
        },
        {
            "field": "market_breadth",
            "status": "implemented",
            "note": "由全市场日线 pct_chg 计算上涨/下跌/涨跌停代理。",
        },
        {
            "field": "theme_quality",
            "status": "proxy_only",
            "note": "v1 使用 stock_basic.industry + 当日行业成交额/收益/上涨占比代理主线，不等同于人工确认主线。",
        },
        {
            "field": "core_stock_feedback",
            "status": "manual_only/proxy_pending",
            "note": "龙头/中军破位反馈仍需 A池和 Part2 核心池接入。",
        },
    ]
    if stock_basic is None or stock_basic.empty:
        items.append({"field": "stock_basic.industry", "status": "data_pending", "note": "缺少行业归属时，主线分只能按 unknown 处理。"})
    return items


def _safe_div_series(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.replace(0, pd.NA)
    return (numerator / denominator).replace([math.inf, -math.inf], pd.NA).fillna(0.0)


def _safe_div(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)


def _ratio_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return (float(current) / float(previous)) - 1.0


def _float(value: Any, default: float | None = None) -> float:
    try:
        if value is None or pd.isna(value):
            return 0.0 if default is None else default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return 0.0 if default is None else default
        return number
    except (TypeError, ValueError):
        return 0.0 if default is None else default


def _round(value: Any, digits: int = 2) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _date_text(value: Any) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _looks_like_stock_ticker(ticker: str) -> bool:
    return bool(re.match(r"^\d{6}\.(SZ|SH|BJ)$", ticker)) and ticker not in {
        "000001.SH",
        "399001.SZ",
        "399006.SZ",
        "000300.SH",
        "000905.SH",
        "000852.SH",
    }


def _is_weight_industry(industry: Any) -> bool:
    text = str(industry)
    return any(keyword in text for keyword in WEIGHT_INDUSTRY_KEYWORDS)
