from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import re
from typing import Any

import pandas as pd


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
    evidence = trend["evidence"] + volume["evidence"] + breadth_score["evidence"] + theme["evidence"] + risk["evidence"]

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
        "total_score": total_score,
        "scores": scores,
        "metrics": metrics,
        "levels": levels,
        "hard_triggers": hard_triggers,
        "theme_method": theme.get("method", _theme_method()),
        "theme_candidates": theme.get("candidates", []),
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
        ),
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
    for window in (5, 10, 20):
        frame[f"ma{window}"] = frame["close"].rolling(window, min_periods=1).mean()
    frame["ma5_slope"] = frame["ma5"].diff().fillna(0.0)
    frame["ma10_slope"] = frame["ma10"].diff().fillna(0.0)
    frame["amount_ma5"] = frame["amount"].rolling(5, min_periods=1).mean()
    frame["amount_ma20"] = frame["amount"].rolling(20, min_periods=1).mean()
    frame["amount_ratio_5"] = _safe_div_series(frame["amount"], frame["amount_ma5"])
    frame["amount_ratio_20"] = _safe_div_series(frame["amount"], frame["amount_ma20"])
    frame["rolling_low_20"] = frame["low"].rolling(20, min_periods=1).min()
    frame["rolling_high_20"] = frame["high"].rolling(20, min_periods=1).max()
    frame["upper_shadow_ratio"] = _safe_div_series(frame["high"] - frame[["open", "close"]].max(axis=1), frame["high"] - frame["low"])
    return frame


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
        up_count = int((pct > 0).sum())
        down_count = int((pct < 0).sum())
        flat_count = int((pct == 0).sum())
        limit_up_count = int((pct >= 0.095).sum())
        limit_down_count = int((pct <= -0.095).sum())
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
        }
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
        weight_support_flag = bool(index_pct_chg > 0 and weight_return > index_pct_chg + 0.003 and non_weight_up_ratio < 0.45)
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
    sections = [
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


def _build_support_pressure(index_frame: pd.DataFrame) -> dict[str, Any]:
    latest = index_frame.iloc[-1]
    previous = index_frame.iloc[-2] if len(index_frame) >= 2 else latest
    close = _float(latest.get("close"), 0.0)
    candidates = [
        ("MA5", _float(previous.get("ma5"), close)),
        ("MA10", _float(previous.get("ma10"), close)),
        ("MA20", _float(previous.get("ma20"), close)),
        ("20日低点", _float(previous.get("rolling_low_20"), close)),
    ]
    below = [(name, value) for name, value in candidates if value <= close and value > 0]
    above = [(name, value) for name, value in candidates + [("20日高点", _float(previous.get("rolling_high_20"), close))] if value > close]
    below = sorted(below, key=lambda item: item[1], reverse=True)
    above = sorted(above, key=lambda item: item[1])
    support_1 = below[0] if below else min(candidates, key=lambda item: item[1])
    support_2 = below[1] if len(below) >= 2 else min(candidates, key=lambda item: item[1])
    pressure_1 = above[0] if above else ("20日高点", _float(previous.get("rolling_high_20"), close))
    return {
        "support_1": _round(support_1[1]),
        "support_1_source": support_1[0],
        "support_2": _round(support_2[1]),
        "support_2_source": support_2[0],
        "pressure_1": _round(pressure_1[1]),
        "pressure_1_source": pressure_1[0],
        "definition": "v1 自动位：从上一交易日 MA5/MA10/MA20/20日低点中选取低于当前价的最近两档为支撑，最近上方位为压力；允许人工覆盖。",
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
