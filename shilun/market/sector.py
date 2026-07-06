from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import re
from typing import Any

import pandas as pd

from shilun.market.part1 import DEFAULT_BENCHMARK_TICKER, benchmark_index_meta


SECTOR_ENGINE_VERSION = "market_sector_v3_ma5_v02_mainline"


@dataclass(frozen=True)
class SectorTrendRequest:
    analysis_date: str
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER
    lookback_days: int = 40
    trend_lookback_days: int = 60  # v2: 从 15 改为 60，捕获中期主线（元器件/半导体）
    top_n: int = 8
    min_stock_count: int = 3
    exclude_st: bool = True

    @property
    def start_date(self) -> str:
        target_dt = datetime.strptime(self.analysis_date, "%Y-%m-%d")
        # 60 日窗口需要至少 90 个自然日的数据（去掉周末），加 10 天缓冲
        return (target_dt - timedelta(days=max(20, self.lookback_days, int(self.trend_lookback_days * 1.5)))).strftime("%Y-%m-%d")


def evaluate_sector_trends(
    *,
    analysis_date: str,
    market_bars: pd.DataFrame,
    stock_basic: pd.DataFrame | None = None,
    daily_basic: pd.DataFrame | None = None,
    moneyflow: pd.DataFrame | None = None,
    benchmark_bars: pd.DataFrame | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    top_n: int = 8,
    min_stock_count: int = 3,
    exclude_st: bool = True,
    include_daily_leaders: bool = True,
    include_all_sectors: bool = True,
    market_gate: dict[str, Any] | None = None,
    trend_lookback_days: int = 60,
) -> dict[str, Any]:
    """Evaluate Part2 sector/theme momentum from synced daily data.

    v1 intentionally uses `stock_basic.industry` as a proxy sector source. It
    separates成交额活跃 from资金净流入 so the product does not over-claim
    without moneyflow data.
    """

    stock_frame = _prepare_stock_frame(
        market_bars,
        stock_basic=stock_basic,
        daily_basic=daily_basic,
        moneyflow=moneyflow,
        exclude_st=exclude_st,
    )
    if stock_frame.empty:
        raise ValueError(f"No stock market bars found before {analysis_date}.")
    if "industry" not in stock_frame.columns:
        raise ValueError("缺少 stock_basic.industry，无法生成板块动向代理指标。")

    target_date = pd.Timestamp(analysis_date)
    stock_frame = stock_frame.loc[stock_frame["date"] <= target_date].copy()
    target_rows = stock_frame.loc[stock_frame["date"] == target_date].dropna(subset=["industry"]).copy()
    if target_rows.empty:
        raise ValueError(f"No sector stock rows found for {analysis_date}.")

    benchmark_frame = _prepare_benchmark_frame(benchmark_bars, analysis_date)
    if benchmark_frame.empty:
        raise ValueError(f"No benchmark/index bars found for {benchmark_ticker} before {analysis_date}.")
    latest_benchmark_date = _date_text(benchmark_frame.iloc[-1]["date"])
    if latest_benchmark_date != analysis_date:
        raise ValueError(f"Benchmark latest date is {latest_benchmark_date}, not requested analysis_date {analysis_date}.")

    sector_history = _build_sector_history(stock_frame, min_stock_count=min_stock_count)
    if sector_history.empty:
        raise ValueError("行业样本不足，无法计算板块动向。")

    current_sector_names = set(target_rows["industry"].dropna().astype(str))
    current_history = sector_history.loc[
        (sector_history["date"] == target_date)
        & (sector_history["sector_name"].isin(current_sector_names))
    ].copy()
    if current_history.empty:
        raise ValueError(f"No sector aggregates found for {analysis_date}.")

    benchmark_returns = dict(zip(benchmark_frame["date"], benchmark_frame["pct_chg"]))
    benchmark_return_1d = _float(benchmark_frame.iloc[-1].get("pct_chg"), 0.0)
    benchmark_return_5d = _compound(benchmark_frame.tail(5)["pct_chg"])

    sector_items: list[dict[str, Any]] = []
    total_sector_count = len(current_history)
    ranked_amounts = current_history["amount"].rank(method="min", ascending=False)
    ranked_returns = current_history["return_1d"].rank(method="min", ascending=False)
    current_history["amount_rank"] = ranked_amounts.astype(int)
    current_history["return_rank"] = ranked_returns.astype(int)
    # v3 主线分依赖全市场横截面分位数，不能在评分前把板块池裁窄；
    # `_preselect_sector_names` 保留给后续性能优化或“只算候选板块”模式。

    for _, sector_row in current_history.iterrows():
        sector_name = str(sector_row["sector_name"])
        sector_rows = stock_frame.loc[stock_frame["industry"].astype(str) == sector_name].copy()
        sector_daily = sector_history.loc[sector_history["sector_name"] == sector_name].sort_values("date").reset_index(drop=True)
        sector_daily = _attach_sector_rolling(sector_daily)
        current = sector_daily.loc[sector_daily["date"] == target_date]
        if current.empty:
            continue
        current = current.iloc[-1]
        date_slice = sector_daily.tail(10)
        last_5 = sector_daily.tail(5)
        last_20 = sector_daily.tail(20)
        last_60 = sector_daily.tail(60)
        last_120 = sector_daily.tail(120)
        return_3d = _compound(sector_daily.tail(3)["return_1d"])
        return_5d = _compound(last_5["return_1d"])
        return_20d = _compound(last_20["return_1d"]) if len(last_20) >= 5 else None
        return_60d = _compound(last_60["return_1d"]) if len(last_60) >= 15 else None
        return_120d = _compound(last_120["return_1d"]) if len(last_120) >= 30 else None
        outperform_days_5 = int(
            sum(_float(row["return_1d"], 0.0) > _float(benchmark_returns.get(row["date"]), 0.0) for _, row in last_5.iterrows())
        )
        outperform_days_20 = int(
            sum(_float(row["return_1d"], 0.0) > _float(benchmark_returns.get(row["date"]), 0.0) for _, row in last_20.iterrows())
        )
        outperform_days_60 = int(
            sum(_float(row["return_1d"], 0.0) > _float(benchmark_returns.get(row["date"]), 0.0) for _, row in last_60.iterrows())
        )
        # 多周期基准回报，用来计算 relative return
        benchmark_return_20d = _compound(benchmark_frame.tail(20)["pct_chg"]) if len(benchmark_frame) >= 5 else 0.0
        benchmark_return_60d = _compound(benchmark_frame.tail(60)["pct_chg"]) if len(benchmark_frame) >= 15 else 0.0
        benchmark_return_120d = _compound(benchmark_frame.tail(120)["pct_chg"]) if len(benchmark_frame) >= 30 else 0.0

        # 多周期共振分（0-100）：跨窗口跑赢基准就加分
        resonance_score = 0
        if return_5d is not None and return_5d > _float(benchmark_return_5d, 0.0):
            resonance_score += 15  # 短期跑赢
        if return_20d is not None and return_20d > _float(benchmark_return_20d, 0.0):
            resonance_score += 25  # 中短期跑赢
        if return_60d is not None and return_60d > _float(benchmark_return_60d, 0.0):
            resonance_score += 30  # 中期跑赢（元器件/半导体在此维度）
        if return_120d is not None and return_120d > _float(benchmark_return_120d, 0.0):
            resonance_score += 30  # 中长期跑赢
        # 至少两个窗口都跑赢基准，才叫"共振"
        cross_period_windows = sum(1 for r, b in [
            (return_5d, benchmark_return_5d), (return_20d, benchmark_return_20d),
            (return_60d, benchmark_return_60d), (return_120d, benchmark_return_120d),
        ] if r is not None and r > _float(b, 0.0))

        target_sector_rows = target_rows.loc[target_rows["industry"].astype(str) == sector_name].copy()
        stock_profiles = _build_stock_profiles(
            sector_name=sector_name,
            sector_rows=sector_rows,
            target_rows=target_sector_rows,
            sector_return_5d=return_5d,
            sector_return_1d=_float(current.get("return_1d"), 0.0),
            sector_up_ratio=_float(current.get("up_ratio"), 0.0),
        )
        leader_candidates = _leader_candidates(stock_profiles)
        zhongjun_candidates = _zhongjun_candidates(stock_profiles)
        core_stats = _core_stats(stock_profiles, leader_candidates, zhongjun_candidates)
        divergence = _divergence_context(
            current=current,
            previous=sector_daily.iloc[-2] if len(sector_daily) >= 2 else current,
            leader_candidates=leader_candidates,
            zhongjun_candidates=zhongjun_candidates,
            benchmark_return_1d=benchmark_return_1d,
        )
        repair = _repair_context(
            sector_daily=sector_daily,
            current=current,
            leader_candidates=leader_candidates,
            zhongjun_candidates=zhongjun_candidates,
            benchmark_return_1d=benchmark_return_1d,
            divergence=divergence,
        )
        stage = _classify_sector_stage(
            current=current,
            return_5d=return_5d,
            benchmark_return_5d=benchmark_return_5d,
            outperform_days_5=outperform_days_5,
            amount_rank=int(sector_row["amount_rank"]),
            total_sector_count=total_sector_count,
            core_stats=core_stats,
            divergence=divergence,
            repair=repair,
        )
        scores = _sector_scores(
            current=current,
            return_5d=return_5d,
            benchmark_return_5d=benchmark_return_5d,
            outperform_days_5=outperform_days_5,
            amount_rank=int(sector_row["amount_rank"]),
            total_sector_count=total_sector_count,
            core_stats=core_stats,
            divergence=divergence,
            repair=repair,
        )
        metrics = {
            "return_1d": _round(current.get("return_1d"), 4),
            "return_3d": _round(return_3d, 4),
            "return_5d": _round(return_5d, 4),
            "return_20d": _round(return_20d, 4) if return_20d is not None else None,
            "return_60d": _round(return_60d, 4) if return_60d is not None else None,
            "return_120d": _round(return_120d, 4) if return_120d is not None else None,
            "benchmark_return_1d": _round(benchmark_return_1d, 4),
            "benchmark_return_5d": _round(benchmark_return_5d, 4),
            "benchmark_return_20d": _round(benchmark_return_20d, 4),
            "benchmark_return_60d": _round(benchmark_return_60d, 4),
            "benchmark_return_120d": _round(benchmark_return_120d, 4),
            "relative_return_5d": _round(return_5d - benchmark_return_5d, 4),
            "relative_return_20d": _round((return_20d or 0) - benchmark_return_20d, 4) if return_20d is not None else None,
            "relative_return_60d": _round((return_60d or 0) - benchmark_return_60d, 4) if return_60d is not None else None,
            "relative_return_120d": _round((return_120d or 0) - benchmark_return_120d, 4) if return_120d is not None else None,
            "outperform_days_5": outperform_days_5,
            "outperform_days_20": outperform_days_20,
            "outperform_days_60": outperform_days_60,
            "resonance_score": resonance_score,          # 0-100，跨周期共振分
            "cross_period_windows": cross_period_windows,  # 0-4，跑赢基准的窗口数
            "main_net_20d": _round(_float(current.get("main_net_20d"), 0.0)),
            "main_net_60d": _round(_float(current.get("main_net_60d"), 0.0)),
            # 主动买入结构分析
            "active_buy_ratio": _round(current.get("active_buy_ratio"), 4),
            "active_buy_structure": _round(current.get("active_buy_structure"), 4),
            "main_active_buy": _round(current.get("main_active_buy")),
            "main_active_sell": _round(current.get("main_active_sell")),
            "retail_active_buy": _round(current.get("retail_active_buy")),
            "retail_active_sell": _round(current.get("retail_active_sell")),
            "amount": _round(current.get("amount")),
            "amount_ma5": _round(current.get("amount_ma5")),
            "amount_ratio_5": _round(current.get("amount_ratio_5"), 4),
            "market_share": _round(current.get("market_share"), 4),
            "market_share_ma5": _round(current.get("market_share_ma5"), 4),
            "market_share_change_vs_ma5": _round(
                _float(current.get("market_share"), 0.0) - _float(current.get("market_share_ma5"), 0.0),
                4,
            ),
            "main_net_inflow": _round(current.get("large_net_amount"), 4),
            "main_net_inflow_rate": _round(current.get("large_net_ratio"), 4),
            "positive_moneyflow_ratio": _round(current.get("positive_moneyflow_ratio"), 4),
            "moneyflow_persistence_3d": int(current.get("moneyflow_persistence_3d") or 0),
            "up_ratio": _round(current.get("up_ratio"), 4),
            "limit_up_count": int(current.get("limit_up_count") or 0),
            "big_up_count": int(current.get("big_up_count") or 0),
            "down_ratio": _round(current.get("down_ratio"), 4),
            "close_position": _round(current.get("close_position"), 4),
            "upper_shadow_ratio": _round(current.get("upper_shadow_ratio"), 4),
            "stock_count": int(current.get("stock_count") or 0),
            "amount_rank": int(sector_row["amount_rank"]),
            "return_rank": int(sector_row["return_rank"]),
            "total_sector_count": total_sector_count,
            "core_unbroken_count": core_stats["core_unbroken_count"],
            "core_repair_count": repair["core_repair_count"],
        }
        sector_items.append(
            {
                "sector_name": sector_name,
                "sector_code": sector_name,
                "sector_source": "Tushare stock_basic.industry",
                "stage": stage["stage"],
                "stage_label": stage["label"],
                "stage_meaning": stage["meaning"],
                "action": stage["action"],
                "scores": scores,
                "metrics": metrics,
                "fund_flow": _fund_flow_status(current),
                "leader_candidates": leader_candidates[:3],
                "zhongjun_candidates": zhongjun_candidates[:3],
                "core_stats": core_stats,
                "divergence": divergence,
                "repair": repair,
                "evidence": _sector_evidence(
                    sector_name=sector_name,
                    stage=stage,
                    metrics=metrics,
                    divergence=divergence,
                    repair=repair,
                    leader_candidates=leader_candidates,
                    zhongjun_candidates=zhongjun_candidates,
                ),
                "history_tail": _sector_history_tail(date_slice),
            }
        )

    if not sector_items:
        raise ValueError(f"No sector trend result generated for {analysis_date}.")

    trend_sectors_all = _build_trend_sectors(
        sector_items=sector_items,
        sector_history=sector_history,
        benchmark_frame=benchmark_frame,
        analysis_date=analysis_date,
        top_n=max(1, len(sector_items)),
        trend_lookback_days=max(5, int(trend_lookback_days)),
    )
    trend_lookup = {str(item.get("sector_name") or ""): item for item in trend_sectors_all}
    for item in sector_items:
        trend = trend_lookup.get(str(item.get("sector_name") or ""))
        if not trend:
            continue
        item["trend_label"] = trend.get("trend_label")
        item["trend_meaning"] = trend.get("trend_meaning")
        item["sector_state"] = trend.get("sector_state")
        item["sector_state_label"] = trend.get("sector_state_label")
        item["sector_multiplier"] = trend.get("sector_multiplier")
        item["retreat_flag"] = trend.get("retreat_flag")
        item["state_reason"] = trend.get("state_reason")
        item["sector_mainline_score"] = trend.get("sector_mainline_score")
        item["trend_sort_score"] = trend.get("trend_sort_score")
        item["mainline_rank"] = trend.get("mainline_rank")
        item["scores"]["sector_mainline_score"] = trend.get("sector_mainline_score")
        for score_key in (
            "excess_return_20d_score",
            "excess_return_60d_score",
            "outperform_days_5_score",
            "sector_amount_ratio_score",
            "leader_zhongjun_score",
            "score_breakdown",
        ):
            if score_key in (trend.get("scores") or {}):
                item["scores"][score_key] = (trend.get("scores") or {}).get(score_key)
        item["scores"]["mainline_formula"] = (trend.get("scores") or {}).get("formula")
        item["metrics"]["trend_sort_score"] = trend.get("trend_sort_score")
        item["metrics"]["sector_multiplier"] = trend.get("sector_multiplier")
        item["metrics"]["mainline_rank"] = trend.get("mainline_rank")

    # 排序（v3）：60/20 日主线分优先，5 日收益只作为热度和同分辅助。
    sector_items = sorted(
        sector_items,
        key=lambda item: (
            _float(item.get("trend_sort_score"), 0.0),
            _float(item["scores"].get("sector_mainline_score"), 0.0),
            item["scores"]["sector_score"],
            item["metrics"]["market_share"],
            item["metrics"]["return_5d"],
        ),
        reverse=True,
    )
    top_sectors = sector_items[: max(1, int(top_n))]
    trend_sectors = trend_sectors_all[: max(6, int(top_n))]
    from shilun.market.candidates import build_candidates

    candidates = build_candidates(
        top_sectors=top_sectors,
        stock_frame=stock_frame,
        analysis_date=analysis_date,
        market_gate=market_gate,
        trend_sectors=trend_sectors,
    )
    return {
        "engine_version": SECTOR_ENGINE_VERSION,
        "analysis_date": analysis_date,
        "benchmark_ticker": benchmark_ticker,
        "benchmark_name": benchmark_index_meta(benchmark_ticker)["name"],
        "trend_lookback_days": max(5, int(trend_lookback_days)),
        "sector_source": "Tushare stock_basic.industry",
        "sector_source_note": "第一版按 stock_basic.industry 聚合个股日线，是行业代理板块；不是申万行业指数，也不是同花顺概念板块。",
        "summary": _build_summary(top_sectors),
        "top_sectors": top_sectors,
        "trend_sectors": trend_sectors,
        "candidates": candidates,
        "all_sectors": sector_items if include_all_sectors else [],
        "daily_leaders": (
            _build_daily_leaders(
                stock_frame=stock_frame,
                sector_history=sector_history,
                benchmark_frame=benchmark_frame,
                analysis_date=analysis_date,
                top_n=5,
            )
            if include_daily_leaders
            else []
        ),
        "state_machine": _state_machine_spec(),
        "indicator_definitions": _indicator_definitions(),
        "data_quality": _data_quality(stock_basic=stock_basic, daily_basic=daily_basic, moneyflow=moneyflow),
    }


def evaluate_daily_leaders(
    *,
    analysis_date: str,
    market_bars: pd.DataFrame,
    stock_basic: pd.DataFrame | None = None,
    daily_basic: pd.DataFrame | None = None,
    moneyflow: pd.DataFrame | None = None,
    benchmark_bars: pd.DataFrame | None = None,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    min_stock_count: int = 3,
    exclude_st: bool = True,
    top_n: int = 5,
) -> dict[str, Any]:
    stock_frame = _prepare_stock_frame(
        market_bars,
        stock_basic=stock_basic,
        daily_basic=daily_basic,
        moneyflow=moneyflow,
        exclude_st=exclude_st,
    )
    if stock_frame.empty or "industry" not in stock_frame.columns:
        raise ValueError(f"No sector stock rows found before {analysis_date}.")
    stock_frame = stock_frame.loc[stock_frame["date"] <= pd.Timestamp(analysis_date)].copy()
    sector_history = _build_sector_history(stock_frame, min_stock_count=min_stock_count)
    benchmark_frame = _prepare_benchmark_frame(benchmark_bars, analysis_date)
    if sector_history.empty or benchmark_frame.empty:
        raise ValueError(f"No daily leader source data found before {analysis_date}.")
    daily_leaders = _build_daily_leaders(
        stock_frame=stock_frame,
        sector_history=sector_history,
        benchmark_frame=benchmark_frame,
        analysis_date=analysis_date,
        top_n=top_n,
        max_dates=30,
    )
    return {
        "engine_version": SECTOR_ENGINE_VERSION,
        "analysis_date": analysis_date,
        "benchmark_ticker": benchmark_ticker,
        "benchmark_name": benchmark_index_meta(benchmark_ticker)["name"],
        "daily_leaders": daily_leaders,
        "leader_summary": _build_leader_summary(daily_leaders, window_days=30),
    }


def _prepare_stock_frame(
    market_bars: pd.DataFrame,
    *,
    stock_basic: pd.DataFrame | None,
    daily_basic: pd.DataFrame | None,
    moneyflow: pd.DataFrame | None,
    exclude_st: bool,
) -> pd.DataFrame:
    frame = _normalize_bars(market_bars)
    if frame.empty:
        return frame
    if stock_basic is not None and not stock_basic.empty and "ts_code" in stock_basic.columns:
        info = stock_basic.copy()
        info["ticker"] = info["ts_code"].astype(str)
        allowed = set(info["ticker"].dropna().astype(str))
        frame = frame.loc[frame["ticker"].isin(allowed)].copy()
        merge_columns = [column for column in ["ticker", "name", "industry", "market"] if column in info.columns]
        frame = frame.merge(info[merge_columns].drop_duplicates("ticker"), on="ticker", how="left")
    else:
        frame = frame.loc[frame["ticker"].map(_looks_like_stock_ticker)].copy()
    if exclude_st and "name" in frame.columns:
        names = frame["name"].fillna("").astype(str)
        frame = frame.loc[~names.str.contains("ST", case=False, regex=False)].copy()
    if daily_basic is not None and not daily_basic.empty:
        basic = daily_basic.copy()
        if "ts_code" in basic.columns and "ticker" not in basic.columns:
            basic = basic.rename(columns={"ts_code": "ticker"})
        if "trade_date" in basic.columns and "date" not in basic.columns:
            basic = basic.rename(columns={"trade_date": "date"})
        if "ticker" in basic.columns and "date" in basic.columns:
            basic["ticker"] = basic["ticker"].astype(str)
            basic["date"] = pd.to_datetime(basic["date"], errors="coerce")
            merge_columns = [
                column
                for column in ["ticker", "date", "turnover_rate", "turnover_rate_f", "volume_ratio", "pe_ttm", "pb", "total_mv", "circ_mv"]
                if column in basic.columns
            ]
            frame = frame.merge(basic[merge_columns].drop_duplicates(["ticker", "date"]), on=["ticker", "date"], how="left")
    flow = _prepare_moneyflow_frame(moneyflow)
    if not flow.empty:
        frame = frame.merge(flow, on=["ticker", "date"], how="left")
    frame = frame.dropna(subset=["industry"]).copy()
    frame["industry"] = frame["industry"].astype(str).replace({"": pd.NA, "nan": pd.NA})
    frame = frame.dropna(subset=["industry"]).sort_values(["ticker", "date"]).reset_index(drop=True)
    frame["pct_chg"] = frame.groupby("ticker", group_keys=False)["close"].pct_change()
    fallback = (frame["close"] / frame["open"]) - 1.0
    frame["pct_chg"] = frame["pct_chg"].fillna(fallback).fillna(0.0)
    for window in (5, 8, 10, 20):
        frame[f"ma{window}"] = frame.groupby("ticker", group_keys=False)["close"].transform(lambda series: series.rolling(window, min_periods=1).mean())
    frame["volume_ma5"] = frame.groupby("ticker", group_keys=False)["volume"].transform(lambda series: series.rolling(5, min_periods=1).mean())
    frame["amount_ma5"] = frame.groupby("ticker", group_keys=False)["amount"].transform(lambda series: series.rolling(5, min_periods=1).mean())
    frame["atr_14"] = _compute_atr_series(frame, period=14)
    frame["atr_pct_14"] = _safe_div_series(frame["atr_14"], frame["close"])
    frame["median_abs_return_20d"] = frame.groupby("ticker", group_keys=False)["pct_chg"].transform(
        lambda series: series.abs().rolling(20, min_periods=5).median()
    ).fillna(frame["pct_chg"].abs())
    volume_median_20 = frame.groupby("ticker", group_keys=False)["volume"].transform(lambda series: series.rolling(20, min_periods=5).median())
    amount_median_60 = frame.groupby("ticker", group_keys=False)["amount"].transform(lambda series: series.rolling(60, min_periods=15).median())
    range_median_20 = (frame["high"] - frame["low"]).groupby(frame["ticker"], group_keys=False).transform(
        lambda series: series.rolling(20, min_periods=5).median()
    )
    frame["volume_ratio_20"] = _safe_div_series(frame["volume"], volume_median_20).replace(0, 1.0)
    frame["amount_ratio_60"] = _safe_div_series(frame["amount"], amount_median_60).replace(0, 1.0)
    frame["range_ratio_20"] = _safe_div_series(frame["high"] - frame["low"], range_median_20).replace(0, 1.0)
    frame["volume_percentile_120"] = frame.groupby("ticker", group_keys=False)["volume"].transform(
        lambda series: _rolling_rank_pct(series, 120, 30)
    ).fillna(0.5)
    frame["return_percentile_120"] = frame.groupby("ticker", group_keys=False)["pct_chg"].transform(
        lambda series: _rolling_rank_pct(series, 120, 30)
    ).fillna(0.5)
    extension = _safe_div_series(frame["close"], frame["ma5"]) - 1.0
    frame["extension_percentile_120"] = extension.groupby(frame["ticker"], group_keys=False).transform(
        lambda series: _rolling_rank_pct(series, 120, 30)
    ).fillna(0.5)
    frame["close_position"] = _close_position_series(frame)
    frame["real_body_ratio"] = _safe_div_series((frame["close"] - frame["open"]).abs(), frame["high"] - frame["low"])
    frame["upper_shadow_ratio"] = _safe_div_series(frame["high"] - frame[["open", "close"]].max(axis=1), frame["high"] - frame["low"])
    frame["lower_shadow_ratio"] = _safe_div_series(frame[["open", "close"]].min(axis=1) - frame["low"], frame["high"] - frame["low"])
    return frame


def _prepare_benchmark_frame(benchmark_bars: pd.DataFrame | None, analysis_date: str) -> pd.DataFrame:
    frame = _normalize_bars(benchmark_bars)
    if frame.empty:
        return frame
    frame = frame.loc[frame["date"] <= pd.Timestamp(analysis_date)].sort_values("date").reset_index(drop=True)
    frame["pct_chg"] = frame["close"].pct_change().fillna((frame["close"] / frame["open"]) - 1.0).fillna(0.0)
    return frame


def _normalize_bars(frame: pd.DataFrame | None) -> pd.DataFrame:
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


def _prepare_moneyflow_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    """处理 Tushare moneyflow。

    Tushare moneyflow 的 buy_/sell_ 字段本身就是**主动方向**（外盘/内盘拆分）：
      buy_elg/lg/md/sm_amount = 特大/大/中/小单的**主动买入**（外盘）
      sell_elg/lg/md/sm_amount = 特大/大/中/小单的**主动卖出**（内盘）

    我们在这里同时计算：
      large_net_amount       主力（特大+大）净流入（现有）
      main_active_buy        主力主动买入总额（buy_elg + buy_lg）
      main_active_sell       主力主动卖出总额（sell_elg + sell_lg）
      retail_active_buy      散户主动买入总额（buy_md + buy_sm）
      retail_active_sell     散户主动卖出总额（sell_md + sell_sm）
      total_active           全部主动买+卖，用来算比率
      active_buy_ratio       外盘占比 = 主动买入 / 全部主动
      active_buy_structure   主力主动占比 - 散户主动占比（-1..1）
                             > 0 = 聪明钱进场；< 0 = 散户接盘（顶部特征）
    """
    if frame is None or frame.empty:
        return pd.DataFrame()
    normalized = frame.copy()
    if "ts_code" in normalized.columns and "ticker" not in normalized.columns:
        normalized = normalized.rename(columns={"ts_code": "ticker"})
    if "trade_date" in normalized.columns and "date" not in normalized.columns:
        normalized = normalized.rename(columns={"trade_date": "date"})
    if "ticker" not in normalized.columns or "date" not in normalized.columns:
        return pd.DataFrame()
    normalized["ticker"] = normalized["ticker"].astype(str)
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    amount_columns = [
        "buy_lg_amount", "sell_lg_amount",
        "buy_elg_amount", "sell_elg_amount",
        "buy_md_amount", "sell_md_amount",
        "buy_sm_amount", "sell_sm_amount",
        "net_mf_amount",
    ]
    for column in amount_columns:
        if column not in normalized.columns:
            normalized[column] = 0.0
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce").fillna(0.0)

    # 主力（特大+大单）净流入 = 现有字段
    normalized["large_net_amount"] = (
        normalized["buy_lg_amount"] + normalized["buy_elg_amount"]
        - normalized["sell_lg_amount"] - normalized["sell_elg_amount"]
    )

    # 主动买卖结构（v2）
    normalized["main_active_buy"] = normalized["buy_elg_amount"] + normalized["buy_lg_amount"]
    normalized["main_active_sell"] = normalized["sell_elg_amount"] + normalized["sell_lg_amount"]
    normalized["retail_active_buy"] = normalized["buy_md_amount"] + normalized["buy_sm_amount"]
    normalized["retail_active_sell"] = normalized["sell_md_amount"] + normalized["sell_sm_amount"]

    total_buy = normalized["main_active_buy"] + normalized["retail_active_buy"]
    total_sell = normalized["main_active_sell"] + normalized["retail_active_sell"]
    total_active = total_buy + total_sell

    # 外盘占比（主动买入 / 全部主动） 0..1
    normalized["active_buy_ratio"] = (total_buy / total_active.replace(0, pd.NA)).fillna(0.5)

    # 主力主动占比 - 散户主动占比（-1..1）
    total_active_safe = total_active.replace(0, pd.NA)
    main_share = (normalized["main_active_buy"] / total_active_safe).fillna(0.0)
    retail_share = (normalized["retail_active_buy"] / total_active_safe).fillna(0.0)
    normalized["active_buy_structure"] = main_share - retail_share

    return normalized[
        [
            "ticker", "date",
            "buy_lg_amount", "sell_lg_amount",
            "buy_elg_amount", "sell_elg_amount",
            "buy_md_amount", "sell_md_amount",
            "buy_sm_amount", "sell_sm_amount",
            "net_mf_amount",
            "large_net_amount",
            "main_active_buy", "main_active_sell",
            "retail_active_buy", "retail_active_sell",
            "active_buy_ratio", "active_buy_structure",
        ]
    ].dropna(subset=["ticker", "date"]).drop_duplicates(["ticker", "date"])


def _build_sector_history(stock_frame: pd.DataFrame, *, min_stock_count: int) -> pd.DataFrame:
    market_amount_by_date = stock_frame.groupby("date")["amount"].sum().to_dict()
    rows: list[dict[str, Any]] = []
    for (date, sector_name), group in stock_frame.groupby(["date", "industry"]):
        if len(group) < min_stock_count:
            continue
        amount = float(pd.to_numeric(group["amount"], errors="coerce").fillna(0.0).clip(lower=0).sum())
        weights = pd.to_numeric(group["amount"], errors="coerce").fillna(0.0).clip(lower=0)
        returns = pd.to_numeric(group["pct_chg"], errors="coerce").fillna(0.0)
        if float(weights.sum() or 0.0) > 0:
            sector_return = float((returns * weights / weights.sum()).sum())
            close_position = float((group["close_position"] * weights / weights.sum()).sum())
            upper_shadow = float((group["upper_shadow_ratio"] * weights / weights.sum()).sum())
        else:
            sector_return = float(returns.mean())
            close_position = float(group["close_position"].mean())
            upper_shadow = float(group["upper_shadow_ratio"].mean())
        market_amount = float(market_amount_by_date.get(date, 0.0) or 0.0)
        if "large_net_amount" in group.columns:
            large_net = pd.to_numeric(group["large_net_amount"], errors="coerce")
            has_moneyflow = bool(large_net.notna().any())
            large_net_amount = float(large_net.fillna(0.0).sum()) if has_moneyflow else None
            positive_moneyflow_ratio = float((large_net.fillna(0.0) > 0).mean()) if has_moneyflow else None
            net_mf_amount = float(pd.to_numeric(group.get("net_mf_amount"), errors="coerce").fillna(0.0).sum()) if has_moneyflow else None
        else:
            large_net_amount = None
            positive_moneyflow_ratio = None
            net_mf_amount = None

        # 主动买卖聚合（板块层）
        main_active_buy = None
        main_active_sell = None
        retail_active_buy = None
        retail_active_sell = None
        active_buy_ratio = None
        active_buy_structure = None
        if "main_active_buy" in group.columns:
            main_active_buy = float(pd.to_numeric(group["main_active_buy"], errors="coerce").fillna(0.0).sum())
            main_active_sell = float(pd.to_numeric(group["main_active_sell"], errors="coerce").fillna(0.0).sum())
            retail_active_buy = float(pd.to_numeric(group["retail_active_buy"], errors="coerce").fillna(0.0).sum())
            retail_active_sell = float(pd.to_numeric(group["retail_active_sell"], errors="coerce").fillna(0.0).sum())
            total_buy = main_active_buy + retail_active_buy
            total_sell = main_active_sell + retail_active_sell
            total_active = total_buy + total_sell
            if total_active > 0:
                active_buy_ratio = total_buy / total_active
                active_buy_structure = (main_active_buy - retail_active_buy) / total_active
            else:
                active_buy_ratio = 0.5
                active_buy_structure = 0.0

        rows.append(
            {
                "date": date,
                "sector_name": str(sector_name),
                "return_1d": sector_return,
                "amount": amount,
                "market_share": _safe_div(amount, market_amount),
                "large_net_amount": large_net_amount,
                "large_net_ratio": _safe_div(float(large_net_amount or 0.0), amount / 10.0) if large_net_amount is not None else None,
                "positive_moneyflow_ratio": positive_moneyflow_ratio,
                "net_mf_amount": net_mf_amount,
                "main_active_buy": main_active_buy,
                "main_active_sell": main_active_sell,
                "retail_active_buy": retail_active_buy,
                "retail_active_sell": retail_active_sell,
                "active_buy_ratio": active_buy_ratio,
                "active_buy_structure": active_buy_structure,
                "up_ratio": float((returns > 0).mean()),
                "down_ratio": float((returns < 0).mean()),
                "limit_up_count": int((returns >= 0.095).sum()),
                "big_up_count": int((returns >= 0.05).sum()),
                "stock_count": int(len(group)),
                "close_position": close_position,
                "upper_shadow_ratio": upper_shadow,
            }
        )
    return pd.DataFrame(rows).sort_values(["sector_name", "date"]).reset_index(drop=True)


def _attach_sector_rolling(sector_daily: pd.DataFrame) -> pd.DataFrame:
    frame = sector_daily.copy().sort_values("date").reset_index(drop=True)
    prior_amount = frame["amount"].shift(1).rolling(5, min_periods=1).mean()
    prior_share = frame["market_share"].shift(1).rolling(5, min_periods=1).mean()
    frame["amount_ma5"] = prior_amount.fillna(frame["amount"])
    frame["market_share_ma5"] = prior_share.fillna(frame["market_share"])
    frame["amount_ratio_5"] = _safe_div_series(frame["amount"], frame["amount_ma5"])
    frame["market_share_ratio_5"] = _safe_div_series(frame["market_share"], frame["market_share_ma5"])
    if "large_net_amount" in frame.columns:
        flow = pd.to_numeric(frame["large_net_amount"], errors="coerce")
        frame["moneyflow_persistence_3d"] = (flow > 0).astype(int).rolling(3, min_periods=1).sum()
        # 20 日累计主力净流入（判断中期资金持续入场）
        frame["main_net_20d"] = flow.rolling(20, min_periods=5).sum()
        frame["main_net_60d"] = flow.rolling(60, min_periods=15).sum()
    else:
        frame["moneyflow_persistence_3d"] = 0
        frame["main_net_20d"] = 0.0
        frame["main_net_60d"] = 0.0
    return frame


def _attach_stock_leader_rolling(stock_frame: pd.DataFrame) -> pd.DataFrame:
    """Precompute rolling leader features once for multi-day leaderboard queries."""

    frame = stock_frame.copy().sort_values(["ticker", "date"]).reset_index(drop=True)
    grouped = frame.groupby("ticker", group_keys=False)
    frame["return_5d"] = grouped["pct_chg"].transform(
        lambda series: (1.0 + series.fillna(0.0)).rolling(5, min_periods=1).apply(math.prod, raw=True) - 1.0
    )
    frame["limit_up_count_5d"] = grouped["pct_chg"].transform(
        lambda series: (series.fillna(0.0) >= 0.095).rolling(5, min_periods=1).sum()
    )
    frame["big_up_count_5d"] = grouped["pct_chg"].transform(
        lambda series: (series.fillna(0.0) >= 0.05).rolling(5, min_periods=1).sum()
    )
    amount_mean = grouped["amount"].transform(lambda series: series.rolling(5, min_periods=1).mean())
    amount_std = grouped["amount"].transform(lambda series: series.rolling(5, min_periods=1).std(ddof=0))
    frame["amount_cv_5d"] = _safe_div_series(amount_std, amount_mean).fillna(1.0)
    rolling_peak = grouped["close"].transform(lambda series: series.rolling(10, min_periods=1).max())
    drawdown = ((frame["close"] / rolling_peak.replace(0, pd.NA)) - 1.0).abs().fillna(0.0)
    frame["max_drawdown_10d"] = drawdown.groupby(frame["ticker"], group_keys=False).transform(
        lambda series: series.rolling(10, min_periods=1).max()
    )
    return frame


def _build_stock_profiles(
    *,
    sector_name: str,
    sector_rows: pd.DataFrame,
    target_rows: pd.DataFrame,
    sector_return_5d: float,
    sector_return_1d: float,
    sector_up_ratio: float,
) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    if target_rows.empty:
        return profiles
    target = target_rows.copy()
    precomputed_columns = {
        "return_5d",
        "limit_up_count_5d",
        "big_up_count_5d",
        "amount_cv_5d",
        "max_drawdown_10d",
    }
    if not precomputed_columns.issubset(target.columns):
        return_5d_by_ticker: dict[str, float] = {}
        limit_count_by_ticker: dict[str, int] = {}
        big_up_count_by_ticker: dict[str, int] = {}
        amount_cv_by_ticker: dict[str, float] = {}
        max_drawdown_by_ticker: dict[str, float] = {}
        for ticker, history in sector_rows.groupby("ticker"):
            history = history.sort_values("date").tail(10)
            last_5 = history.tail(5)
            return_5d_by_ticker[str(ticker)] = _compound(last_5["pct_chg"])
            limit_count_by_ticker[str(ticker)] = int((last_5["pct_chg"] >= 0.095).sum())
            big_up_count_by_ticker[str(ticker)] = int((last_5["pct_chg"] >= 0.05).sum())
            amounts = pd.to_numeric(last_5["amount"], errors="coerce").fillna(0.0)
            amount_mean = float(amounts.mean() or 0.0)
            amount_cv_by_ticker[str(ticker)] = float(amounts.std(ddof=0) / amount_mean) if amount_mean else 1.0
            closes = pd.to_numeric(history["close"], errors="coerce").fillna(0.0)
            peak = closes.cummax().replace(0, pd.NA)
            drawdowns = ((closes / peak) - 1.0).fillna(0.0)
            max_drawdown_by_ticker[str(ticker)] = abs(float(drawdowns.min() or 0.0))
        target["return_5d"] = target["ticker"].astype(str).map(return_5d_by_ticker).fillna(0.0)
        target["limit_up_count_5d"] = target["ticker"].astype(str).map(limit_count_by_ticker).fillna(0).astype(int)
        target["big_up_count_5d"] = target["ticker"].astype(str).map(big_up_count_by_ticker).fillna(0).astype(int)
        target["amount_cv_5d"] = target["ticker"].astype(str).map(amount_cv_by_ticker).fillna(1.0)
        target["max_drawdown_10d"] = target["ticker"].astype(str).map(max_drawdown_by_ticker).fillna(0.0)
    else:
        for column in precomputed_columns:
            target[column] = pd.to_numeric(target[column], errors="coerce").fillna(0.0)
    target["relative_return_5d"] = target["return_5d"] - sector_return_5d
    for column in ["amount", "circ_mv", "turnover_rate", "turnover_rate_f", "large_net_amount", "net_mf_amount"]:
        if column not in target.columns:
            target[column] = pd.NA
        target[column] = pd.to_numeric(target[column], errors="coerce")
    target["amount_pct_rank"] = target["amount"].rank(pct=True, ascending=True).fillna(0.5)
    target["return_pct_rank"] = target["return_5d"].rank(pct=True, ascending=True).fillna(0.5)
    target["circ_mv_pct_rank"] = target["circ_mv"].rank(pct=True, ascending=True).fillna(target["amount_pct_rank"])
    for _, row in target.iterrows():
        ticker = str(row.get("ticker") or "")
        trend_unbroken = bool(_float(row.get("close"), 0.0) >= _float(row.get("ma10"), 0.0) > 0)
        close_position = _float(row.get("close_position"), 0.5)
        strength_score = _clip_score(
            70.0 * _float(row.get("return_pct_rank"), 0.5)
            + 20.0 * min(1.0, _float(row.get("limit_up_count_5d"), 0.0) / 2.0)
            + 10.0 * min(1.0, _float(row.get("big_up_count_5d"), 0.0) / 3.0)
        )
        startup_score = _clip_score(
            55.0
            + 20.0 * min(1.0, _float(row.get("limit_up_count_5d"), 0.0))
            + 25.0 * max(0.0, _float(row.get("relative_return_5d"), 0.0) / 0.10)
        )
        drive_score = _clip_score(
            35.0
            + 35.0 * max(0.0, min(1.0, sector_up_ratio))
            + 20.0 * (1.0 if sector_return_1d > 0 else 0.0)
            + 10.0 * _float(row.get("amount_pct_rank"), 0.5)
        )
        resilience_score = _clip_score(
            35.0
            + (25.0 if trend_unbroken else 0.0)
            + 20.0 * close_position
            + 20.0 * max(0.0, 1.0 - _float(row.get("max_drawdown_10d"), 0.0) / 0.15)
        )
        board_quality_score = _clip_score(
            40.0
            + (30.0 if _float(row.get("pct_chg"), 0.0) >= 0.095 and close_position >= 0.80 else 0.0)
            + 20.0 * max(0.0, 1.0 - _float(row.get("upper_shadow_ratio"), 0.0) / 0.45)
            + 10.0 * _float(row.get("amount_pct_rank"), 0.5)
        )
        leader_score = _clip_score(
            0.25 * startup_score
            + 0.25 * strength_score
            + 0.20 * drive_score
            + 0.15 * resilience_score
            + 0.15 * board_quality_score
        )
        capacity_score = _clip_score(100.0 * _float(row.get("circ_mv_pct_rank"), _float(row.get("amount_pct_rank"), 0.5)))
        amount_stability_score = _clip_score(
            70.0 * _float(row.get("amount_pct_rank"), 0.5)
            + 30.0 * max(0.0, 1.0 - _float(row.get("amount_cv_5d"), 1.0))
        )
        turnover = _float(row.get("turnover_rate"), _float(row.get("turnover_rate_f"), 0.0))
        large_net_amount = _float(row.get("large_net_amount"), 0.0)
        amount_wan = _float(row.get("amount"), 0.0) / 10.0
        large_net_ratio = _safe_div(large_net_amount, amount_wan)
        net_flow_score = None
        if not pd.isna(row.get("large_net_amount")):
            net_flow_score = _clip_score(50.0 + 500.0 * large_net_ratio)
        turnover_stability_score = 60.0
        if turnover:
            turnover_stability_score = 80.0 if 1.0 <= turnover <= 12.0 else 55.0 if turnover <= 20.0 else 35.0
        trend_stability_score = _clip_score(
            35.0
            + (25.0 if trend_unbroken else 0.0)
            + (20.0 if _float(row.get("close"), 0.0) >= _float(row.get("ma20"), 0.0) > 0 else 0.0)
            + 20.0 * max(0.0, 1.0 - _float(row.get("max_drawdown_10d"), 0.0) / 0.12)
        )
        zhongjun_score = _clip_score(
            0.30 * capacity_score
            + 0.25 * amount_stability_score
            + 0.10 * (net_flow_score if net_flow_score is not None else 50.0)
            + 0.20 * trend_stability_score
            + 0.15 * turnover_stability_score
        )
        profiles.append(
            {
                "ticker": ticker,
                "name": str(row.get("name") or ticker),
                "sector_name": sector_name,
                "pct_chg": _round(row.get("pct_chg"), 4),
                "return_5d": _round(row.get("return_5d"), 4),
                "relative_return_5d": _round(row.get("relative_return_5d"), 4),
                "amount": _round(row.get("amount")),
                "main_net_inflow": _round(large_net_amount, 4) if not pd.isna(row.get("large_net_amount")) else None,
                "main_net_inflow_rate": _round(large_net_ratio, 4) if not pd.isna(row.get("large_net_amount")) else None,
                "circ_mv": _round(row.get("circ_mv")),
                "turnover_rate": _round(turnover, 4) if turnover else None,
                "close": _round(row.get("close")),
                "ma5": _round(row.get("ma5")),
                "ma10": _round(row.get("ma10")),
                "ma20": _round(row.get("ma20")),
                "close_position": _round(close_position, 4),
                "upper_shadow_ratio": _round(row.get("upper_shadow_ratio"), 4),
                "limit_up_count_5d": int(row.get("limit_up_count_5d") or 0),
                "big_up_count_5d": int(row.get("big_up_count_5d") or 0),
                "trend_unbroken": trend_unbroken,
                "leader_score": _round(leader_score, 2),
                "leader_subscores": {
                    "startup_score": _round(startup_score, 2),
                    "strength_score": _round(strength_score, 2),
                    "drive_score": _round(drive_score, 2),
                    "resilience_score": _round(resilience_score, 2),
                    "board_quality_score": _round(board_quality_score, 2),
                },
                "zhongjun_score": _round(zhongjun_score, 2),
                "zhongjun_subscores": {
                    "capacity_score": _round(capacity_score, 2),
                    "amount_stability_score": _round(amount_stability_score, 2),
                    "net_flow_score": _round(net_flow_score, 2) if net_flow_score is not None else None,
                    "trend_stability_score": _round(trend_stability_score, 2),
                    "turnover_stability_score": _round(turnover_stability_score, 2),
                    "fundamental_quality_score": None,
                },
            }
        )
    return profiles


def _leader_candidates(stock_profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(stock_profiles, key=lambda item: (item["leader_score"] or 0, item["return_5d"] or 0), reverse=True)
    result: list[dict[str, Any]] = []
    for item in ranked[:5]:
        score = _float(item.get("leader_score"), 0.0)
        drive_score = _float((item.get("leader_subscores") or {}).get("drive_score"), 0.0)
        if score >= 75 and drive_score >= 60:
            role = "total_leader"
            role_label = "总龙头候选"
        elif score >= 60:
            role = "strong_pioneer"
            role_label = "强势先锋"
        else:
            role = "follow_stock"
            role_label = "跟风/补涨"
        result.append({**item, "role": role, "role_label": role_label})
    return result


def _zhongjun_candidates(stock_profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(stock_profiles, key=lambda item: (item["zhongjun_score"] or 0, item["amount"] or 0), reverse=True)
    result: list[dict[str, Any]] = []
    for item in ranked[:5]:
        score = _float(item.get("zhongjun_score"), 0.0)
        subscores = item.get("zhongjun_subscores") or {}
        if score >= 70 and _float(subscores.get("capacity_score"), 0.0) >= 70 and _float(subscores.get("amount_stability_score"), 0.0) >= 60:
            role = "core_zhongjun"
            role_label = "核心中军候选"
        elif score >= 60:
            role = "trend_capacity_stock"
            role_label = "容量趋势股"
        else:
            role = "big_but_weak"
            role_label = "大但未确认中军"
        result.append({**item, "role": role, "role_label": role_label})
    return result


def _core_stats(
    stock_profiles: list[dict[str, Any]],
    leader_candidates: list[dict[str, Any]],
    zhongjun_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    core_tickers = []
    for item in leader_candidates[:3] + zhongjun_candidates[:3]:
        ticker = item.get("ticker")
        if ticker and ticker not in core_tickers:
            core_tickers.append(ticker)
    core_items = [item for item in stock_profiles if item["ticker"] in core_tickers]
    core_unbroken = [item for item in core_items if item.get("trend_unbroken")]
    core_down = [item for item in core_items if _float(item.get("pct_chg"), 0.0) < 0]
    core_hard_break = [
        item
        for item in core_items
        if _float(item.get("close"), 0.0) < _float(item.get("ma20"), 0.0) and _float(item.get("ma20"), 0.0) > 0
    ]
    return {
        "core_tickers": core_tickers,
        "core_count": len(core_items),
        "core_unbroken_count": len(core_unbroken),
        "core_down_count": len(core_down),
        "core_hard_break_count": len(core_hard_break),
        "core_unbroken_ratio": _round(_safe_div(len(core_unbroken), len(core_items)), 4) if core_items else 0.0,
    }


def _divergence_context(
    *,
    current: pd.Series,
    previous: pd.Series,
    leader_candidates: list[dict[str, Any]],
    zhongjun_candidates: list[dict[str, Any]],
    benchmark_return_1d: float,
) -> dict[str, Any]:
    return_1d = _float(current.get("return_1d"), 0.0)
    amount_ratio = _float(current.get("amount_ratio_5"), 1.0)
    close_position = _float(current.get("close_position"), 0.5)
    up_ratio = _float(current.get("up_ratio"), 0.0)
    previous_up_ratio = _float(previous.get("up_ratio"), up_ratio)
    core_candidates = leader_candidates[:2] + zhongjun_candidates[:2]
    core_down = [item for item in core_candidates if _float(item.get("pct_chg"), 0.0) < 0]
    core_weak = [
        item
        for item in core_candidates
        if _float(item.get("close"), 0.0) < _float(item.get("ma10"), 0.0) and _float(item.get("ma10"), 0.0) > 0
    ]
    triggers: list[dict[str, str]] = []
    if amount_ratio >= 1.3 and (return_1d <= 0.005 or close_position <= 0.45):
        triggers.append({"type": "price_volume_divergence", "meaning": "放量但收盘推进不足，疑似分歧或派发。"})
    if return_1d > benchmark_return_1d and (up_ratio < 0.50 or previous_up_ratio - up_ratio >= 0.15):
        triggers.append({"type": "breadth_divergence", "meaning": "板块涨但扩散不足，上涨占比没有跟上。"})
    if return_1d > 0 and (core_down or core_weak):
        triggers.append({"type": "core_divergence", "meaning": "板块上涨但龙头/中军反馈不一致。"})
    if close_position <= 0.35:
        triggers.append({"type": "intraday_divergence_proxy", "meaning": "日线收盘位置偏低，代理盘中冲高回落。"})
    if _float(current.get("limit_up_count"), 0.0) < _float(previous.get("limit_up_count"), 0.0) and up_ratio < previous_up_ratio:
        triggers.append({"type": "backrow_divergence", "meaning": "涨停减少且上涨占比下降，后排开始掉队。"})
    score = len(triggers)
    if score >= 4 or (core_weak and len(core_weak) >= 2):
        level = "severe_divergence"
        label = "严重分歧"
    elif score >= 2:
        level = "clear_divergence"
        label = "明确分歧"
    elif score == 1:
        level = "mild_divergence"
        label = "轻微分歧"
    else:
        level = "none"
        label = "未见明显分歧"
    return {
        "score": score,
        "level": level,
        "label": label,
        "triggers": triggers,
        "definition": "分歧 = 涨幅、成交额、广度、核心股反馈之间出现不一致。",
    }


def _repair_context(
    *,
    sector_daily: pd.DataFrame,
    current: pd.Series,
    leader_candidates: list[dict[str, Any]],
    zhongjun_candidates: list[dict[str, Any]],
    benchmark_return_1d: float,
    divergence: dict[str, Any],
) -> dict[str, Any]:
    recent = sector_daily.tail(4).head(3)
    prior_divergence_in_3d = bool(
        (recent["amount_ratio_5"].fillna(1.0) >= 1.15).any()
        and ((recent["close_position"].fillna(0.5) <= 0.45).any() or (recent["up_ratio"].fillna(1.0) < 0.50).any())
    )
    core_candidates = leader_candidates[:2] + zhongjun_candidates[:2]
    core_repair = [
        item
        for item in core_candidates
        if _float(item.get("close"), 0.0) >= _float(item.get("ma5"), 0.0) > 0
        and _float(item.get("pct_chg"), 0.0) >= _float(current.get("return_1d"), 0.0)
    ]
    no_core_hard_break = not any(
        _float(item.get("close"), 0.0) < _float(item.get("ma20"), 0.0) and _float(item.get("ma20"), 0.0) > 0
        for item in core_candidates
    )
    repair_confirmed = bool(
        (prior_divergence_in_3d or divergence.get("score", 0) > 0)
        and _float(current.get("return_1d"), 0.0) > benchmark_return_1d
        and _float(current.get("up_ratio"), 0.0) >= 0.55
        and _float(current.get("amount_ratio_5"), 1.0) >= 0.8
        and core_repair
        and no_core_hard_break
    )
    if not repair_confirmed and _float(current.get("return_1d"), 0.0) > 0 and _float(current.get("amount_ratio_5"), 1.0) < 0.8:
        repair_type = "weak_rebound"
        label = "弱反抽"
    elif repair_confirmed and _float(current.get("return_1d"), 0.0) > 0.02 and _float(current.get("up_ratio"), 0.0) >= 0.60:
        repair_type = "breakout_repair_proxy"
        label = "突破修复代理"
    elif repair_confirmed and core_repair:
        repair_type = "core_repair"
        label = "核心修复"
    else:
        repair_type = "none"
        label = "未确认修复"
    return {
        "confirmed": repair_confirmed,
        "type": repair_type,
        "label": label,
        "prior_divergence_in_3d": prior_divergence_in_3d,
        "core_repair_count": len(core_repair),
        "no_core_hard_break": no_core_hard_break,
        "definition": "修复必须有前置分歧；至少满足板块强于大盘、广度改善、核心先修复。",
    }


def _classify_sector_stage(
    *,
    current: pd.Series,
    return_5d: float,
    benchmark_return_5d: float,
    outperform_days_5: int,
    amount_rank: int,
    total_sector_count: int,
    core_stats: dict[str, Any],
    divergence: dict[str, Any],
    repair: dict[str, Any],
) -> dict[str, str]:
    return_1d = _float(current.get("return_1d"), 0.0)
    up_ratio = _float(current.get("up_ratio"), 0.0)
    amount_ratio = _float(current.get("amount_ratio_5"), 1.0)
    market_share = _float(current.get("market_share"), 0.0)
    market_share_ma5 = _float(current.get("market_share_ma5"), market_share)
    is_amount_top = amount_rank <= max(3, math.ceil(total_sector_count * 0.20))
    candidate = (
        return_5d > benchmark_return_5d
        and outperform_days_5 >= 3
        and (market_share > market_share_ma5 or amount_ratio >= 1.05)
        and int(core_stats.get("core_unbroken_count") or 0) >= 2
    )
    confirmed = candidate and is_amount_top and up_ratio >= 0.55
    if int(core_stats.get("core_hard_break_count") or 0) >= 2 and return_5d < benchmark_return_5d:
        return _stage("decline")
    if repair["confirmed"]:
        return _stage("repair")
    if divergence["score"] >= 2:
        return _stage("divergence")
    if confirmed and amount_ratio >= 1.50 and return_1d > 0.015 and up_ratio >= 0.60:
        return _stage("accelerate")
    if confirmed and int(core_stats.get("core_unbroken_count") or 0) >= 2:
        return _stage("main_uptrend")
    if candidate:
        return _stage("confirm")
    if return_1d > 0.005 and amount_ratio >= 1.10:
        return _stage("start")
    return _stage("watch")


def _stage(stage: str) -> dict[str, str]:
    mapping = {
        "start": ("启动", "少数核心点火，成交额开始放大，但持续性还不足。", "小仓观察，等待持续性确认。"),
        "confirm": ("确认", "近5日跑赢大盘，成交额占比上升，核心趋势未破。", "等回踩或缩量确认，不追情绪末端。"),
        "main_uptrend": ("主升", "板块趋势推进，龙头/中军保持趋势。", "重点跟踪龙头和中军，按买点边界执行。"),
        "accelerate": ("加速", "成交额快速放大，涨幅扩散，追高风险上升。", "持仓保护利润，未持仓不追加速末端。"),
        "divergence": ("分歧", "成交额、广度或核心反馈出现不一致。", "降低预期，只看核心承接。"),
        "repair": ("修复", "前期分歧后，板块强于大盘且核心率先修复。", "有先手观察，无先手不追末端。"),
        "decline": ("退潮", "龙头/中军同步破位，板块跑输大盘。", "离场观察，不做后排反抽。"),
        "second_wave": ("二波", "高位震荡后重新放量突破且回踩不破。", "重新纳入观察，不能提前幻想。"),
        "watch": ("观察", "暂未满足主线候选条件。", "只做观察，不按主线处理。"),
    }
    label, meaning, action = mapping[stage]
    return {"stage": stage, "label": label, "meaning": meaning, "action": action}


def _sector_scores(
    *,
    current: pd.Series,
    return_5d: float,
    benchmark_return_5d: float,
    outperform_days_5: int,
    amount_rank: int,
    total_sector_count: int,
    core_stats: dict[str, Any],
    divergence: dict[str, Any],
    repair: dict[str, Any],
) -> dict[str, Any]:
    relative_strength_score = 0
    if return_5d > benchmark_return_5d:
        relative_strength_score += 25
    relative_strength_score += min(25, outperform_days_5 * 5)
    relative_strength_score += min(20, max(0.0, return_5d - benchmark_return_5d) * 400)
    amount_score = min(35, max(0.0, _float(current.get("amount_ratio_5"), 1.0) - 0.8) * 50)
    amount_score += 25 if amount_rank <= max(3, math.ceil(total_sector_count * 0.20)) else 10
    amount_score += 20 if _float(current.get("market_share"), 0.0) > _float(current.get("market_share_ma5"), 0.0) else 0
    breadth_score = min(60, _float(current.get("up_ratio"), 0.0) * 80)
    breadth_score += min(20, _float(current.get("big_up_count"), 0.0) * 3)
    core_score = min(70, _float(core_stats.get("core_unbroken_ratio"), 0.0) * 70)
    if int(core_stats.get("core_unbroken_count") or 0) >= 2:
        core_score += 20
    repair_bonus = 12 if repair.get("confirmed") else 0
    risk_penalty = int(divergence.get("score") or 0) * 10 + int(core_stats.get("core_hard_break_count") or 0) * 12
    sector_score = _clip_score(
        0.25 * _clip_score(relative_strength_score)
        + 0.25 * _clip_score(amount_score)
        + 0.20 * _clip_score(breadth_score)
        + 0.20 * _clip_score(core_score)
        + repair_bonus
        - risk_penalty
    )
    return {
        "sector_score": _round(sector_score, 2),
        "relative_strength_score": _round(_clip_score(relative_strength_score), 2),
        "amount_activity_score": _round(_clip_score(amount_score), 2),
        "breadth_score": _round(_clip_score(breadth_score), 2),
        "core_feedback_score": _round(_clip_score(core_score), 2),
        "repair_bonus": repair_bonus,
        "risk_penalty": risk_penalty,
        "formula": "sector_score = 相对强弱25% + 成交活跃25% + 广度20% + 核心反馈20% + 修复加分 - 分歧/破位扣分",
    }


def _fund_flow_status(current: pd.Series) -> dict[str, Any]:
    amount_ratio = _float(current.get("amount_ratio_5"), 1.0)
    close_position = _float(current.get("close_position"), 0.5)
    main_net_inflow = current.get("large_net_amount")
    main_net_inflow_rate = current.get("large_net_ratio")
    positive_ratio = current.get("positive_moneyflow_ratio")
    persistence_3d = int(current.get("moneyflow_persistence_3d") or 0)
    if main_net_inflow is not None and not pd.isna(main_net_inflow):
        net_value = _float(main_net_inflow, 0.0)
        net_rate = _float(main_net_inflow_rate, 0.0)
        if net_value > 0 and persistence_3d >= 2:
            status = "net_inflow_confirmed"
            label = "主力净流入确认"
            meaning = "moneyflow 显示大单+特大单净流入为正，且近3日有持续性。"
        elif net_value > 0:
            status = "net_inflow_probe"
            label = "主力净流入初现"
            meaning = "moneyflow 显示当日大单+特大单净流入为正，但持续性仍需观察。"
        elif net_value < 0 and net_rate <= -0.02:
            status = "net_outflow"
            label = "主力净流出"
            meaning = "moneyflow 显示大单+特大单净流出，成交活跃也可能是兑现。"
        else:
            status = "flow_neutral"
            label = "资金中性"
            meaning = "moneyflow 未显示明显大单+特大单净流入或净流出。"
        return {
            "status": status,
            "label": label,
            "amount_ratio_5": _round(amount_ratio, 4),
            "main_net_inflow": _round(net_value, 4),
            "main_net_inflow_rate": _round(net_rate, 4),
            "positive_moneyflow_ratio": _round(positive_ratio, 4),
            "moneyflow_persistence_3d": persistence_3d,
            "data_status": "implemented",
            "unit": "万元",
            "meaning": meaning,
        }
    if amount_ratio >= 1.3 and close_position <= 0.45:
        status = "divergence_flow_proxy"
        label = "放量分歧代理"
        meaning = "成交额放大但收盘位置偏低，只能说明资金博弈激烈，不能确认净流入。"
    elif amount_ratio >= 1.05:
        status = "active_only"
        label = "成交活跃"
        meaning = "成交额高于5日均，说明活跃度提升；因缺少 moneyflow，不能说主力净流入。"
    else:
        status = "neutral_activity"
        label = "成交中性"
        meaning = "成交额未明显放大，资金聚焦度仍需观察。"
    return {
        "status": status,
        "label": label,
        "amount_ratio_5": _round(amount_ratio, 4),
        "main_net_inflow": None,
        "main_net_inflow_rate": None,
        "positive_moneyflow_ratio": None,
        "moneyflow_persistence_3d": 0,
        "data_status": "moneyflow_data_pending",
        "meaning": meaning,
    }


def _sector_evidence(
    *,
    sector_name: str,
    stage: dict[str, str],
    metrics: dict[str, Any],
    divergence: dict[str, Any],
    repair: dict[str, Any],
    leader_candidates: list[dict[str, Any]],
    zhongjun_candidates: list[dict[str, Any]],
) -> list[str]:
    leader = leader_candidates[0] if leader_candidates else {}
    zhongjun = zhongjun_candidates[0] if zhongjun_candidates else {}
    evidence = [
        (
            f"{sector_name} 当前阶段：{stage['label']}。近5日收益 {_pct_text(metrics.get('return_5d'))}，"
            f"相对大盘 {_pct_text(metrics.get('relative_return_5d'))}，近5日跑赢 {metrics.get('outperform_days_5')} 天。"
        ),
        _fund_evidence_text(metrics),
        (
            f"上涨占比 {_pct_text(metrics.get('up_ratio'))}，涨停 {metrics.get('limit_up_count')} 只，"
            f"大涨 {metrics.get('big_up_count')} 只。"
        ),
    ]
    if leader:
        evidence.append(f"龙头候选：{leader.get('name')}({leader.get('ticker')})，角色 {leader.get('role_label')}，评分 {leader.get('leader_score')}。")
    if zhongjun:
        evidence.append(f"中军候选：{zhongjun.get('name')}({zhongjun.get('ticker')})，角色 {zhongjun.get('role_label')}，评分 {zhongjun.get('zhongjun_score')}。")
    if divergence.get("score"):
        evidence.append(f"分歧：{divergence.get('label')}，触发项：" + "；".join(item["meaning"] for item in divergence.get("triggers", [])))
    if repair.get("confirmed"):
        evidence.append(f"修复：{repair.get('label')}，核心修复数量 {repair.get('core_repair_count')}。")
    return evidence


def _fund_evidence_text(metrics: dict[str, Any]) -> str:
    if metrics.get("main_net_inflow") is None:
        return (
            f"成交额占比 {_pct_text(metrics.get('market_share'))}，相对5日均 {_ratio_text(metrics.get('amount_ratio_5'))}；"
            "当前缺少 moneyflow，只能说明成交活跃，不等同主力净流入。"
        )
    return (
        f"成交额占比 {_pct_text(metrics.get('market_share'))}，相对5日均 {_ratio_text(metrics.get('amount_ratio_5'))}；"
        f"主力净流入 {_money_wan_text(metrics.get('main_net_inflow'))}，"
        f"净流入占比 {_pct_text(metrics.get('main_net_inflow_rate'))}，"
        f"净流入扩散 {_pct_text(metrics.get('positive_moneyflow_ratio'))}。"
    )


def _sector_history_tail(date_slice: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "date": _date_text(row["date"]),
            "return_1d": _round(row.get("return_1d"), 4),
            "amount_ratio_5": _round(row.get("amount_ratio_5"), 4) if "amount_ratio_5" in row else None,
            "market_share": _round(row.get("market_share"), 4),
            "up_ratio": _round(row.get("up_ratio"), 4),
        }
        for _, row in _attach_sector_rolling(date_slice).tail(5).iterrows()
    ]


def _preselect_sector_names(
    *,
    current_history: pd.DataFrame,
    sector_history: pd.DataFrame,
    benchmark_frame: pd.DataFrame,
    analysis_date: str,
    top_n: int,
) -> set[str]:
    """Select sectors worth stock-level deep scoring before expensive profile builds."""

    if current_history.empty:
        return set()
    current_limit = max(24, int(top_n) * 3)
    trend_limit = max(12, int(top_n) * 2)
    current = current_history.copy()
    current["return_rank_pct"] = current["return_1d"].rank(pct=True, ascending=True).fillna(0.5)
    current["amount_rank_pct"] = current["amount"].rank(pct=True, ascending=True).fillna(0.5)
    flow = (
        pd.to_numeric(current["large_net_amount"], errors="coerce")
        if "large_net_amount" in current.columns
        else pd.Series(0.0, index=current.index)
    )
    current["preselect_score"] = (
        42.0 * current["return_rank_pct"].astype(float)
        + 28.0 * current["amount_rank_pct"].astype(float)
        + 20.0 * current["up_ratio"].fillna(0.0).astype(float)
        + 10.0 * (flow.fillna(0.0) > 0).astype(float)
    )
    names = set(current.sort_values("preselect_score", ascending=False).head(current_limit)["sector_name"].astype(str))

    target = pd.Timestamp(analysis_date)
    benchmark_slice = benchmark_frame.loc[benchmark_frame["date"] <= target].copy()
    benchmark_returns = dict(zip(benchmark_slice["date"], benchmark_slice["pct_chg"]))
    benchmark_return_5d = _compound(benchmark_slice.tail(5)["pct_chg"])
    benchmark_return_20d = _compound(benchmark_slice.tail(20)["pct_chg"]) if len(benchmark_slice) >= 5 else 0.0
    benchmark_return_60d = _compound(benchmark_slice.tail(60)["pct_chg"]) if len(benchmark_slice) >= 15 else 0.0
    trend_rows: list[dict[str, Any]] = []
    for sector_name, history in sector_history.loc[sector_history["date"] <= target].groupby("sector_name"):
        history = _attach_sector_rolling(history.sort_values("date")).tail(60)
        if history.empty:
            continue
        last_5 = history.tail(5)
        last_20 = history.tail(20)
        return_5d = _compound(last_5["return_1d"])
        return_20d = _compound(last_20["return_1d"]) if len(last_20) >= 5 else 0.0
        return_60d = _compound(history["return_1d"]) if len(history) >= 15 else 0.0
        strong_days = int(
            sum(
                _float(row.get("return_1d"), 0.0) > _float(benchmark_returns.get(row.get("date")), 0.0)
                and _float(row.get("up_ratio"), 0.0) >= 0.50
                for _, row in history.iterrows()
            )
        )
        amount_ratio = _float(history.iloc[-1].get("amount_ratio_5"), 1.0)
        flow = pd.to_numeric(history.get("large_net_amount"), errors="coerce") if "large_net_amount" in history.columns else pd.Series(dtype=float)
        moneyflow_days = int((flow.fillna(0.0) > 0).sum()) if not flow.empty else 0
        actual_days = max(1, len(history))
        trend_rows.append(
            {
                "sector_name": str(sector_name),
                "score": max(0.0, return_60d - benchmark_return_60d) * 90.0
                + max(0.0, return_20d - benchmark_return_20d) * 130.0
                + max(0.0, return_5d - benchmark_return_5d) * 80.0
                + (strong_days / actual_days) * 45.0
                + (moneyflow_days / actual_days) * 25.0
                + max(0.0, min(amount_ratio, 2.0) - 1.0) * 10.0,
            }
        )
    names.update(
        row["sector_name"]
        for row in sorted(trend_rows, key=lambda item: item["score"], reverse=True)[:trend_limit]
    )
    return names


def _positive_return_score(value: Any, scale: float) -> float:
    if value is None:
        return 0.0
    number = _float(value, 0.0)
    return _clip_score(max(0.0, number) * scale)


def _ratio_score(count: int, total: int) -> float:
    return _clip_score(float(count) / max(1, int(total)) * 100.0)


def _percentile_score(value: Any, values: list[Any]) -> float:
    valid_values = sorted(_float(item, 0.0) for item in values if item is not None and not pd.isna(item))
    if not valid_values:
        return 0.0
    number = _float(value, 0.0)
    if len(valid_values) == 1:
        return 100.0
    lower = sum(1 for item in valid_values if item < number)
    equal = sum(1 for item in valid_values if item == number)
    # Mid-rank percentile keeps ties readable and avoids all top names becoming 100.
    return _clip_score((lower + equal * 0.5) / len(valid_values) * 100.0)


def _sector_mainline_state(
    *,
    score: float,
    rank: int,
    item: dict[str, Any],
    relative_return_5d: float,
    relative_return_20d: float | None,
    relative_return_60d: float | None,
    strong_days: int,
    actual_days: int,
) -> dict[str, Any]:
    stage = str(item.get("stage") or "")
    metrics = item.get("metrics") or {}
    scores = item.get("scores") or {}
    core_stats = item.get("core_stats") or {}
    divergence = item.get("divergence") or {}
    up_ratio = _float(metrics.get("up_ratio"), 0.0)
    amount_ratio = _float(metrics.get("amount_ratio_5"), 1.0)
    core_breaks = int(core_stats.get("core_hard_break_count") or 0)
    divergence_score = int(divergence.get("score") or 0)
    raw_risk = _float(scores.get("risk_penalty"), 0.0)
    rel20 = _float(relative_return_20d, 0.0) if relative_return_20d is not None else 0.0
    rel60 = _float(relative_return_60d, 0.0) if relative_return_60d is not None else 0.0

    retreat_flag = bool(
        stage == "decline"
        or core_breaks >= 2
        or (rel20 < 0 and rel60 < 0 and up_ratio < 0.45 and amount_ratio >= 1.15)
    )
    if retreat_flag:
        return {
            "sector_state": "retreat",
            "sector_state_label": "退潮",
            "sector_multiplier": 0.60,
            "retreat_flag": True,
            "state_reason": "核心破位、跑输大盘或放量转弱，板块信号进入硬降权。",
        }
    if score >= 75 and rank <= 3:
        return {
            "sector_state": "mainline_top3",
            "sector_state_label": "主线前三",
            "sector_multiplier": 1.30,
            "retreat_flag": False,
            "state_reason": "主线分进入全市场前三且超过 75，作为 MA5 候选的强主线加权。",
        }
    if score >= 60 and rank <= 8:
        return {
            "sector_state": "mainline_top8",
            "sector_state_label": "主线前八",
            "sector_multiplier": 1.10,
            "retreat_flag": False,
            "state_reason": "主线分进入全市场前八且超过 60，候选票按中期主线背景加权。",
        }
    if relative_return_5d > 0.03 or stage in {"start", "confirm", "repair", "accelerate"}:
        return {
            "sector_state": "hot",
            "sector_state_label": "短线热点",
            "sector_multiplier": 1.00,
            "retreat_flag": False,
            "state_reason": "短线热度较强，但 20/60 日主线证据不足，暂不提高策略乘数。",
        }
    if stage == "divergence" or divergence_score >= 2 or raw_risk >= 18:
        return {
            "sector_state": "divergence",
            "sector_state_label": "分歧观察",
            "sector_multiplier": 0.85,
            "retreat_flag": False,
            "state_reason": "板块存在成交、广度或核心反馈分歧，候选票只做降权观察。",
        }
    return {
        "sector_state": "neutral",
        "sector_state_label": "中性观察",
        "sector_multiplier": 0.85,
        "retreat_flag": False,
        "state_reason": "暂未形成明确主线或退潮信号，候选票不做额外加权。",
    }


def _build_trend_sectors(
    *,
    sector_items: list[dict[str, Any]],
    sector_history: pd.DataFrame,
    benchmark_frame: pd.DataFrame,
    analysis_date: str,
    top_n: int,
    trend_lookback_days: int,
) -> list[dict[str, Any]]:
    """Build a multi-day trend board without recomputing stock-level profiles."""

    target = pd.Timestamp(analysis_date)
    window = max(5, int(trend_lookback_days))
    strong_threshold = max(3, math.ceil(window * 0.50))
    resonance_threshold = max(2, math.ceil(window * 0.32))
    moneyflow_threshold = max(2, math.ceil(window * 0.28))
    benchmark_returns = dict(zip(benchmark_frame["date"], benchmark_frame["pct_chg"]))
    trend_items: list[dict[str, Any]] = []
    for item in sector_items:
        sector_name = str(item.get("sector_name") or "")
        if not sector_name:
            continue
        history = sector_history.loc[
            (sector_history["sector_name"].astype(str) == sector_name)
            & (sector_history["date"] <= target)
        ].sort_values("date")
        if history.empty:
            continue
        history = _attach_sector_rolling(history).tail(window).reset_index(drop=True)
        actual_days = len(history)
        strong_days = 0
        repair_days = 0
        resonance_days = 0
        moneyflow_days = 0
        points: list[dict[str, Any]] = []
        for idx, row in history.iterrows():
            date_value = row["date"]
            benchmark_return = _float(benchmark_returns.get(date_value), 0.0)
            return_1d = _float(row.get("return_1d"), 0.0)
            up_ratio = _float(row.get("up_ratio"), 0.0)
            amount_ratio = _float(row.get("amount_ratio_5"), 1.0)
            main_net_inflow = row.get("large_net_amount")
            strong = return_1d > benchmark_return and up_ratio >= 0.50
            resonance = return_1d > 0 and benchmark_return >= 0 and return_1d >= benchmark_return
            flow_positive = main_net_inflow is not None and not pd.isna(main_net_inflow) and _float(main_net_inflow, 0.0) > 0
            repair = False
            if idx > 0:
                previous = history.iloc[idx - 1]
                previous_divergent = (
                    _float(previous.get("amount_ratio_5"), 1.0) >= 1.15
                    and (
                        _float(previous.get("close_position"), 0.5) <= 0.45
                        or _float(previous.get("up_ratio"), 1.0) < 0.50
                    )
                )
                repair = bool(previous_divergent and return_1d > benchmark_return and up_ratio >= 0.55)
            strong_days += int(strong)
            resonance_days += int(resonance)
            moneyflow_days += int(flow_positive)
            repair_days += int(repair)
            points.append(
                {
                    "date": _date_text(date_value),
                    "return_1d": _round(return_1d, 4),
                    "benchmark_return_1d": _round(benchmark_return, 4),
                    "relative_return_1d": _round(return_1d - benchmark_return, 4),
                    "amount_ratio_5": _round(amount_ratio, 4),
                    "up_ratio": _round(up_ratio, 4),
                    "main_net_inflow": _round(main_net_inflow, 4) if main_net_inflow is not None and not pd.isna(main_net_inflow) else None,
                    "strong": strong,
                    "repair": repair,
                    "resonance": resonance,
                }
            )
        metrics = item.get("metrics") or {}
        scores = item.get("scores") or {}
        sector_score = _float(scores.get("sector_score"), 0.0)
        relative_return_5d = _float(metrics.get("relative_return_5d"), 0.0)
        relative_return_20d = metrics.get("relative_return_20d")
        relative_return_60d = metrics.get("relative_return_60d")
        return_5d = _float(metrics.get("return_5d"), 0.0)
        return_20d = metrics.get("return_20d")
        return_60d = metrics.get("return_60d")
        amount_ratio_5 = _float(metrics.get("amount_ratio_5"), 1.0)
        has_moneyflow_data = any(point.get("main_net_inflow") is not None for point in points)
        recent_points = points[-5:]
        strong_days_5 = sum(1 for point in recent_points if point.get("strong"))
        repair_days_5 = sum(1 for point in recent_points if point.get("repair"))
        resonance_days_5 = sum(1 for point in recent_points if point.get("resonance"))
        moneyflow_days_5 = sum(1 for point in recent_points if _float(point.get("main_net_inflow"), 0.0) > 0)
        outperform_days_5_score = _ratio_score(strong_days_5, len(recent_points))
        current_health_score = _clip_score(
            0.45 * sector_score
            + 0.30 * _float(scores.get("amount_activity_score"), 0.0)
            + 0.25 * _float(scores.get("breadth_score"), 0.0)
        )
        leader_zhongjun_raw = _clip_score(_float(scores.get("core_feedback_score"), 0.0))
        risk_penalty = min(28.0, _float(scores.get("risk_penalty"), 0.0) * 0.45)
        short_heat_score = _clip_score(
            _positive_return_score(relative_return_5d, 180.0) * 0.65
            + max(0.0, min(amount_ratio_5, 2.0) - 1.0) * 18.0
            + _float(scores.get("breadth_score"), 0.0) * 0.20
        )
        trend_items.append(
            {
                "sector_name": sector_name,
                "stage": item.get("stage"),
                "stage_label": item.get("stage_label"),
                "sector_state": None,
                "sector_state_label": None,
                "sector_multiplier": None,
                "retreat_flag": False,
                "state_reason": "",
                "trend_label": "",
                "trend_meaning": "",
                "trend_score": None,
                "trend_sort_score": None,
                "sector_mainline_score": None,
                "scores": {
                    "outperform_days_5_score": _round(outperform_days_5_score, 2),
                    "current_health_score": _round(current_health_score, 2),
                    "leader_zhongjun_raw_score": _round(leader_zhongjun_raw, 2),
                    "risk_penalty": _round(risk_penalty, 2),
                    "formula": "sector_mainline_score = 35%*20日相对强度分位 + 25%*60日相对强度分位 + 15%*近5日跑赢分 + 15%*成交额活跃分位 + 10%*龙头/中军反馈分位",
                },
                "metrics": {
                    "return_5d": _round(return_5d, 4),
                    "return_20d": _round(return_20d, 4) if return_20d is not None else None,
                    "return_60d": _round(return_60d, 4) if return_60d is not None else None,
                    "relative_return_5d": _round(relative_return_5d, 4),
                    "relative_return_20d": _round(relative_return_20d, 4) if relative_return_20d is not None else None,
                    "relative_return_60d": _round(relative_return_60d, 4) if relative_return_60d is not None else None,
                    "lookback_days": window,
                    "actual_days": actual_days,
                    "strong_days": strong_days,
                    "repair_days": repair_days,
                    "resonance_days": resonance_days,
                    "moneyflow_days": moneyflow_days,
                    "strong_days_5": strong_days_5,
                    "repair_days_5": repair_days_5,
                    "resonance_days_5": resonance_days_5,
                    "moneyflow_days_5": moneyflow_days_5,
                    "amount_ratio_5": _round(amount_ratio_5, 4),
                    "up_ratio": metrics.get("up_ratio"),
                    "market_share": metrics.get("market_share"),
                    "has_moneyflow_data": has_moneyflow_data,
                },
                "evidence": [],
                "trend_points": points,
                "_mainline_raw": {
                    "relative_return_20d": relative_return_20d,
                    "relative_return_60d": relative_return_60d,
                    "amount_ratio_5": amount_ratio_5,
                    "leader_zhongjun": leader_zhongjun_raw,
                    "short_heat_score": short_heat_score,
                },
                "_source_item": item,
            }
        )

    rel20_values = [row["_mainline_raw"]["relative_return_20d"] for row in trend_items]
    rel60_values = [row["_mainline_raw"]["relative_return_60d"] for row in trend_items]
    amount_values = [row["_mainline_raw"]["amount_ratio_5"] for row in trend_items]
    leader_values = [row["_mainline_raw"]["leader_zhongjun"] for row in trend_items]
    for row in trend_items:
        raw = row["_mainline_raw"]
        scores = row["scores"]
        metrics = row["metrics"]
        excess_20_score = _percentile_score(raw["relative_return_20d"], rel20_values)
        excess_60_score = _percentile_score(raw["relative_return_60d"], rel60_values)
        amount_ratio_score = _percentile_score(raw["amount_ratio_5"], amount_values)
        leader_zhongjun_score = _percentile_score(raw["leader_zhongjun"], leader_values)
        outperform_days_5_score = _float(scores.get("outperform_days_5_score"), 0.0)
        sector_mainline_score = _clip_score(
            0.35 * excess_20_score
            + 0.25 * excess_60_score
            + 0.15 * outperform_days_5_score
            + 0.15 * amount_ratio_score
            + 0.10 * leader_zhongjun_score
        )
        row["sector_mainline_score"] = _round(sector_mainline_score, 2)
        row["trend_score"] = _round(sector_mainline_score, 2)
        row["trend_sort_score"] = _round(sector_mainline_score, 2)
        scores.update(
            {
                "sector_mainline_score": _round(sector_mainline_score, 2),
                "excess_return_20d_score": _round(excess_20_score, 2),
                "excess_return_60d_score": _round(excess_60_score, 2),
                "sector_amount_ratio_score": _round(amount_ratio_score, 2),
                "leader_zhongjun_score": _round(leader_zhongjun_score, 2),
                "score_breakdown": {
                    "20日相对强度": _round(excess_20_score, 2),
                    "60日相对强度": _round(excess_60_score, 2),
                    "近5日跑赢": _round(outperform_days_5_score, 2),
                    "成交额活跃": _round(amount_ratio_score, 2),
                    "龙头中军反馈": _round(leader_zhongjun_score, 2),
                },
            }
        )
        metrics["short_heat_score"] = _round(raw["short_heat_score"], 2)

    trend_items = sorted(
        trend_items,
        key=lambda row: (
            _float(row.get("sector_mainline_score"), 0.0),
            _float((row.get("metrics") or {}).get("relative_return_20d"), 0.0),
            _float((row.get("metrics") or {}).get("relative_return_60d"), 0.0),
            _float((row.get("metrics") or {}).get("relative_return_5d"), 0.0),
        ),
        reverse=True,
    )

    for rank, row in enumerate(trend_items, start=1):
        metrics = row["metrics"]
        state = _sector_mainline_state(
            score=_float(row.get("sector_mainline_score"), 0.0),
            rank=rank,
            item=row["_source_item"],
            relative_return_5d=_float(metrics.get("relative_return_5d"), 0.0),
            relative_return_20d=metrics.get("relative_return_20d"),
            relative_return_60d=metrics.get("relative_return_60d"),
            strong_days=int(metrics.get("strong_days") or 0),
            actual_days=int(metrics.get("actual_days") or 0),
        )
        row["mainline_rank"] = rank
        row["sector_state"] = state["sector_state"]
        row["sector_state_label"] = state["sector_state_label"]
        row["sector_multiplier"] = state["sector_multiplier"]
        row["retreat_flag"] = state["retreat_flag"]
        row["state_reason"] = state["state_reason"]
        metrics["mainline_rank"] = rank
        metrics["sector_multiplier"] = state["sector_multiplier"]
        if state["sector_state"] == "retreat":
            trend_label = "退潮观察"
            trend_meaning = state["state_reason"]
        elif state["sector_state"] in {"mainline_top3", "mainline_top8"}:
            trend_label = state["sector_state_label"]
            trend_meaning = state["state_reason"]
        elif state["sector_state"] == "divergence":
            trend_label = "分歧观察"
            trend_meaning = state["state_reason"]
        elif state["sector_state"] == "hot":
            trend_label = "短线热点"
            trend_meaning = state["state_reason"]
        elif int(metrics.get("repair_days") or 0) >= 1:
            trend_label = "分歧后修复"
            trend_meaning = f"近{window}个交易日出现前置分歧后的重新跑赢和广度修复。"
        elif int(metrics.get("moneyflow_days") or 0) >= moneyflow_threshold:
            trend_label = "资金趋势"
            trend_meaning = f"近{window}个交易日多次出现大单+特大单净流入，板块强度有资金持续性支撑。"
        elif int(metrics.get("strong_days") or 0) >= strong_threshold and int(metrics.get("resonance_days") or 0) >= resonance_threshold:
            trend_label = "共振趋势"
            trend_meaning = "板块多日跑赢，并且多次与大盘同向上行。"
        elif int(metrics.get("strong_days") or 0) >= strong_threshold:
            trend_label = "多日强势"
            trend_meaning = f"近{window}个交易日多次强于基准，仍需观察资金和核心反馈。"
        else:
            trend_label = "中性观察"
            trend_meaning = state["state_reason"]
        row["trend_label"] = trend_label
        row["trend_meaning"] = trend_meaning
        scores = row["scores"]
        row["evidence"] = [
            f"主线分 {row['sector_mainline_score']}：20日相对分 {scores['excess_return_20d_score']}、60日相对分 {scores['excess_return_60d_score']}、近5日跑赢分 {scores['outperform_days_5_score']}、成交额分 {scores['sector_amount_ratio_score']}、龙头/中军分 {scores['leader_zhongjun_score']}。",
            f"近{metrics['actual_days']}/{window}个交易日跑赢/强势 {metrics['strong_days']} 天；60日相对大盘 {_pct_text(metrics.get('relative_return_60d'))}，20日相对大盘 {_pct_text(metrics.get('relative_return_20d'))}。",
            f"近5日相对大盘 {_pct_text(metrics.get('relative_return_5d'))}，只作为短线热度参考；状态 {state['sector_state_label']}，乘数 {state['sector_multiplier']}。",
        ]
        row.pop("_mainline_raw", None)
        row.pop("_source_item", None)

    return trend_items[: max(1, int(top_n))]


def _build_daily_leaders(
    *,
    stock_frame: pd.DataFrame,
    sector_history: pd.DataFrame,
    benchmark_frame: pd.DataFrame,
    analysis_date: str,
    top_n: int,
    max_dates: int = 30,
    max_sectors_per_day: int = 12,
) -> list[dict[str, Any]]:
    target = pd.Timestamp(analysis_date)
    leader_frame = _attach_stock_leader_rolling(stock_frame)
    dates = [value for value in sorted(leader_frame.loc[leader_frame["date"] <= target, "date"].dropna().unique())][-max(1, int(max_dates)) :]
    benchmark_returns = dict(zip(benchmark_frame["date"], benchmark_frame["pct_chg"]))
    rolling_sectors = {
        str(sector_name): _attach_sector_rolling(history.sort_values("date"))
        for sector_name, history in sector_history.groupby("sector_name")
    }
    target_groups = {
        (pd.Timestamp(date_value), str(sector_name)): group.copy()
        for (date_value, sector_name), group in leader_frame.loc[leader_frame["date"].isin(dates)].groupby(["date", "industry"])
    }
    board: list[dict[str, Any]] = []
    for date_value in dates:
        date_ts = pd.Timestamp(date_value)
        day_candidates: list[dict[str, Any]] = []
        day_sector_history = sector_history.loc[sector_history["date"] == date_ts].copy()
        if day_sector_history.empty:
            continue
        day_sector_history["return_rank_pct"] = day_sector_history["return_1d"].rank(pct=True, ascending=True).fillna(0.5)
        day_sector_history["amount_rank_pct"] = day_sector_history["amount"].rank(pct=True, ascending=True).fillna(0.5)
        flow = (
            pd.to_numeric(day_sector_history["large_net_amount"], errors="coerce")
            if "large_net_amount" in day_sector_history.columns
            else pd.Series(0.0, index=day_sector_history.index)
        )
        day_sector_history["candidate_score"] = (
            45.0 * day_sector_history["return_rank_pct"].astype(float)
            + 25.0 * day_sector_history["amount_rank_pct"].astype(float)
            + 20.0 * day_sector_history["up_ratio"].fillna(0.0).astype(float)
            + 10.0 * (flow.fillna(0.0) > 0).astype(float)
        )
        day_sector_history = day_sector_history.sort_values("candidate_score", ascending=False).head(
            max(1, int(max_sectors_per_day))
        )
        for _, sector_row in day_sector_history.iterrows():
            sector_name = str(sector_row["sector_name"])
            sector_daily = rolling_sectors.get(sector_name, pd.DataFrame())
            sector_daily = sector_daily.loc[sector_daily["date"] <= date_ts]
            if sector_daily.empty:
                continue
            current = sector_daily.iloc[-1]
            target_rows = target_groups.get((date_ts, sector_name), pd.DataFrame())
            return_5d = _compound(sector_daily.tail(5)["return_1d"])
            profiles = _build_stock_profiles(
                sector_name=sector_name,
                sector_rows=pd.DataFrame(),
                target_rows=target_rows,
                sector_return_5d=return_5d,
                sector_return_1d=_float(current.get("return_1d"), 0.0),
                sector_up_ratio=_float(current.get("up_ratio"), 0.0),
            )
            leaders = _leader_candidates(profiles)
            if leaders:
                leader = leaders[0]
                day_candidates.append(
                    {
                        "date": _date_text(date_ts),
                        "ticker": leader.get("ticker"),
                        "name": leader.get("name"),
                        "sector_name": sector_name,
                        "role": leader.get("role"),
                        "role_label": leader.get("role_label"),
                        "leader_score": leader.get("leader_score"),
                        "return_5d": leader.get("return_5d"),
                        "relative_return_5d": leader.get("relative_return_5d"),
                        "pct_chg": leader.get("pct_chg"),
                        "sector_return_1d": _round(current.get("return_1d"), 4),
                        "benchmark_return_1d": _round(benchmark_returns.get(date_ts), 4),
                    }
                )
        top = sorted(
            day_candidates,
            key=lambda item: (_float(item.get("leader_score"), 0.0), _float(item.get("return_5d"), 0.0)),
            reverse=True,
        )[: max(1, top_n)]
        board.append(
            {
                "date": _date_text(date_ts),
                "leaders": [{**item, "rank": rank} for rank, item in enumerate(top, start=1)],
            }
        )
    return board


def _build_leader_summary(
    daily_leaders: list[dict[str, Any]],
    *,
    window_days: int,
    top_n: int = 12,
) -> dict[str, Any]:
    rows_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for day in daily_leaders:
        for leader in day.get("leaders") or []:
            ticker = str(leader.get("ticker") or "")
            if ticker:
                rows_by_ticker.setdefault(ticker, []).append({"date": day.get("date"), **leader})
    ranking: list[dict[str, Any]] = []
    reversed_days = list(reversed(daily_leaders))
    for ticker, records in rows_by_ticker.items():
        ordered = sorted(records, key=lambda item: str(item.get("date") or ""))
        appearance_count = len(ordered)
        rank_1_count = sum(int(item.get("rank") or 0) == 1 for item in ordered)
        average_rank = sum(_float(item.get("rank"), 0.0) for item in ordered) / max(1, appearance_count)
        leader_scores = [_float(item.get("leader_score"), 0.0) for item in ordered]
        sector_counts = Counter(str(item.get("sector_name") or "-") for item in ordered)
        role_counts = Counter(str(item.get("role_label") or "-") for item in ordered)
        consecutive_latest_days = 0
        for day in reversed_days:
            if any(str(item.get("ticker") or "") == ticker for item in day.get("leaders") or []):
                consecutive_latest_days += 1
            else:
                break
        if appearance_count >= 5 or rank_1_count >= 3:
            role_label = "30日总龙头"
        elif appearance_count >= 3:
            role_label = "多日核心龙头"
        elif appearance_count >= 2:
            role_label = "持续强势龙头"
        else:
            role_label = "单日强势龙头"
        summary_score = _clip_score(
            appearance_count * 17.0
            + rank_1_count * 8.0
            + max(0.0, 6.0 - average_rank) * 4.0
            + (sum(leader_scores) / max(1, len(leader_scores))) * 0.18
            + consecutive_latest_days * 4.0
        )
        latest = ordered[-1]
        primary_sector = sector_counts.most_common(1)[0][0]
        ranking.append(
            {
                "ticker": ticker,
                "name": latest.get("name"),
                "role_label": role_label,
                "summary_score": _round(summary_score, 2),
                "appearance_count": appearance_count,
                "rank_1_count": rank_1_count,
                "best_rank": min(int(item.get("rank") or 99) for item in ordered),
                "average_rank": _round(average_rank, 2),
                "consecutive_latest_days": consecutive_latest_days,
                "average_leader_score": _round(sum(leader_scores) / max(1, len(leader_scores)), 2),
                "max_leader_score": _round(max(leader_scores) if leader_scores else 0.0, 2),
                "latest_date": latest.get("date"),
                "primary_sector": primary_sector,
                "sector_names": [name for name, _ in sector_counts.most_common(3)],
                "primary_daily_role": role_counts.most_common(1)[0][0],
                "evidence": [
                    f"近{window_days}个交易日进入每日前5榜 {appearance_count} 次，其中第1名 {rank_1_count} 次。",
                    f"平均名次 {average_rank:.2f}，平均龙头分 {sum(leader_scores) / max(1, len(leader_scores)):.2f}。",
                    f"主要所属板块 {primary_sector}，最近上榜 {latest.get('date')}。",
                ],
                "records": ordered[-8:],
            }
        )
    ranking = sorted(
        ranking,
        key=lambda item: (
            int(item.get("appearance_count") or 0),
            int(item.get("rank_1_count") or 0),
            _float(item.get("summary_score"), 0.0),
            -_float(item.get("average_rank"), 99.0),
        ),
        reverse=True,
    )[: max(1, int(top_n))]
    return {
        "window_days": window_days,
        "trading_day_count": len(daily_leaders),
        "window_start": daily_leaders[0].get("date") if daily_leaders else None,
        "window_end": daily_leaders[-1].get("date") if daily_leaders else None,
        "formula": "总榜优先按30日上榜次数，其次按第1名次数、平均名次、平均龙头分和连续上榜天数综合排序。",
        "ranking": ranking,
    }


def _build_summary(top_sectors: list[dict[str, Any]]) -> dict[str, Any]:
    leader = top_sectors[0]
    hot = [item for item in top_sectors if item["stage"] in {"confirm", "main_uptrend", "accelerate", "repair"}]
    divergent = [item for item in top_sectors if item["stage"] == "divergence"]
    has_moneyflow = any((item.get("fund_flow") or {}).get("data_status") == "implemented" for item in top_sectors)
    return {
        "headline": f"最强板块：{leader['sector_name']}，阶段 {leader['stage_label']}，评分 {leader['scores']['sector_score']}。",
        "conclusion": (
            f"{leader['sector_name']} 当前按 {leader['sector_source']} 代理口径排名第一；"
            f"动作建议：{leader['action']}"
        ),
        "confirmed_or_repair_count": len(hot),
        "divergence_count": len(divergent),
        "warning": (
            "moneyflow 已接入时，资金口径展示大单+特大单净流入；分钟启动时间和封板质量仍待接入。"
            if has_moneyflow
            else "moneyflow、分钟启动时间和封板质量尚未接入，页面不会把成交额活跃误写成主力净流入。"
        ),
    }


def _state_machine_spec() -> dict[str, Any]:
    return {
        "states": {
            "start": {
                "label": "启动",
                "definition": "少数核心点火，板块成交额开始放大。",
                "math": "sector_return_1d > benchmark_return_1d + 0.5% and amount_ratio_5 >= 1.10",
                "meaning": "启动观察，不等同主线确认。",
            },
            "confirm": {
                "label": "确认",
                "definition": "近5日持续跑赢，成交额占比上升，核心趋势未破。",
                "math": "return_5d > benchmark_return_5d and outperform_days_5 >= 3 and market_share > market_share_ma5 and core_unbroken_count >= 2",
                "meaning": "进入主线候选，可等回踩或缩量确认。",
            },
            "main_uptrend": {
                "label": "主升",
                "definition": "确认之上，核心沿 MA5/MA10 推进。",
                "math": "confirm and amount_rank in top20% and up_ratio >= 55% and core_unbroken_count >= 2",
                "meaning": "重点跟踪龙头和中军。",
            },
            "accelerate": {
                "label": "加速",
                "definition": "成交额显著放大，涨幅扩散，追高风险升高。",
                "math": "main_uptrend and amount_ratio_5 >= 1.50 and return_1d > 1.5% and up_ratio >= 60%",
                "meaning": "持仓保护利润，未持仓不追末端。",
            },
            "divergence": {
                "label": "分歧",
                "definition": "成交额、广度、涨幅、核心股反馈出现不一致。",
                "math": "divergence_score >= 2",
                "meaning": "降低预期，只看核心承接。",
            },
            "repair": {
                "label": "修复",
                "definition": "分歧后板块强于大盘，核心率先站回 MA5/MA10。",
                "math": "prior_divergence_in_3d and sector_return_1d > benchmark_return_1d and up_ratio >= 55% and core_repair_count >= 1",
                "meaning": "有先手观察，无先手不追末端。",
            },
            "decline": {
                "label": "退潮",
                "definition": "龙头/中军同步破位，板块跑输大盘。",
                "math": "core_hard_break_count >= 2 and return_5d < benchmark_return_5d",
                "meaning": "离场观察，不做后排反抽。",
            },
        },
        "priority": "退潮和分歧优先于确认；修复必须有前置分歧；机会逐级升级，风险可以跳级降级。",
    }


def _indicator_definitions() -> list[dict[str, str]]:
    return [
        {
            "indicator": "sector_amount / market_share",
            "formula": "sector_amount = sum(stock.amount); market_share = sector_amount / market_amount",
            "meaning": "成交活跃和容量，不代表净流入。",
        },
        {
            "indicator": "main_net_inflow",
            "formula": "buy_lg_amount + buy_elg_amount - sell_lg_amount - sell_elg_amount",
            "meaning": "Tushare moneyflow 的大单+特大单净流入，单位万元；日线 amount 是千元，计算占比时先转换为万元。",
        },
        {
            "indicator": "outperform_days_5",
            "formula": "近5日 sector_return_1d > benchmark_return_1d 的天数",
            "meaning": "板块是否持续跑赢大盘。",
        },
        {
            "indicator": "leader_score",
            "formula": "0.25*启动 + 0.25*强度 + 0.20*带动 + 0.15*抗跌 + 0.15*封板质量",
            "meaning": "龙头候选评分，分钟和封板字段暂为代理。",
        },
        {
            "indicator": "zhongjun_score",
            "formula": "0.30*容量 + 0.25*成交稳定 + 0.10*净流入 + 0.20*趋势稳定 + 0.15*换手稳定",
            "meaning": "中军代理评分；moneyflow 有数据时纳入净流入分，否则按中性分处理。",
        },
        {
            "indicator": "divergence_score",
            "formula": "放量滞涨 + 广度背离 + 核心背离 + 收盘位置弱 + 后排掉队",
            "meaning": "分歧不是感觉，而是多维指标不一致。",
        },
        {
            "indicator": "repair_confirmed",
            "formula": "前3日有分歧 + 今日跑赢大盘 + up_ratio>=55% + amount_ratio_5>=0.8 + core_repair_count>=1",
            "meaning": "反包和突破压力位都是强修复，但普通修复也必须有核心先修复。",
        },
    ]


def _data_quality(
    *,
    stock_basic: pd.DataFrame | None,
    daily_basic: pd.DataFrame | None,
    moneyflow: pd.DataFrame | None,
) -> list[dict[str, str]]:
    return [
        {
            "field": "daily_bars",
            "status": "implemented",
            "status_label": "已接入",
            "group": "基础行情",
            "note": "用于板块收益、成交额、上涨占比、趋势、分歧/修复代理。",
        },
        {
            "field": "stock_basic.industry",
            "status": "implemented/proxy_only" if stock_basic is not None and not stock_basic.empty else "data_pending",
            "status_label": "已接入/代理口径" if stock_basic is not None and not stock_basic.empty else "待接入",
            "group": "板块划分",
            "note": "第一版行业聚合代理板块；不是申万行业或同花顺概念板块。",
        },
        {
            "field": "daily_basic",
            "status": "implemented" if daily_basic is not None and not daily_basic.empty else "data_pending",
            "status_label": "已接入" if daily_basic is not None and not daily_basic.empty else "待接入",
            "group": "容量中军",
            "note": "用于流通市值、换手率等中军代理指标。",
        },
        {
            "field": "moneyflow",
            "status": "implemented" if moneyflow is not None and not moneyflow.empty else "data_pending",
            "status_label": "已接入" if moneyflow is not None and not moneyflow.empty else "待接入",
            "group": "资金流向",
            "note": "Tushare moneyflow；用于大单+特大单净流入、净流入占比、净流入扩散率。",
        },
        {
            "field": "minute_bars",
            "status": "data_pending",
            "status_label": "待接入",
            "group": "盘中结构",
            "note": "未接入前启动时间、率先修复、盘中跳水只能用日线代理。",
        },
        {
            "field": "stk_limit/orderbook",
            "status": "data_pending",
            "status_label": "待接入",
            "group": "封板质量",
            "note": "未接入前封单、炸板、回封快只能用涨停和上影线代理。",
        },
    ]


def _close_position_series(frame: pd.DataFrame) -> pd.Series:
    spread = frame["high"] - frame["low"]
    return _safe_div_series(frame["close"] - frame["low"], spread).replace(0, 0.5).clip(lower=0, upper=1)


def _compute_atr_series(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    def _one(group: pd.DataFrame) -> pd.Series:
        high = pd.to_numeric(group["high"], errors="coerce")
        low = pd.to_numeric(group["low"], errors="coerce")
        close = pd.to_numeric(group["close"], errors="coerce")
        prev_close = close.shift(1)
        true_range = pd.concat(
            [
                (high - low).abs(),
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return true_range.ewm(span=period, adjust=False).mean()

    result = frame.groupby("ticker", group_keys=False).apply(_one)
    return result.reindex(frame.index).fillna(0.0)


def _rolling_rank_pct(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    return series.rolling(window, min_periods=min_periods).apply(
        lambda values: pd.Series(values).rank(pct=True).iloc[-1],
        raw=False,
    )


def _safe_div_series(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.replace(0, pd.NA)
    return (numerator / denominator).replace([math.inf, -math.inf], pd.NA).fillna(0.0)


def _safe_div(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)


def _compound(values: pd.Series) -> float:
    result = 1.0
    for value in pd.to_numeric(values, errors="coerce").fillna(0.0):
        result *= 1.0 + float(value)
    return result - 1.0


def _clip_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


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


def _pct_text(value: Any) -> str:
    if value is None:
        return "-"
    return f"{_float(value, 0.0):+.2%}"


def _ratio_text(value: Any) -> str:
    if value is None:
        return "-"
    return f"{_float(value, 0.0):.2f}x"


def _money_wan_text(value: Any) -> str:
    if value is None:
        return "-"
    number = _float(value, 0.0)
    return f"{number / 10000.0:+.2f}亿"


def _looks_like_stock_ticker(ticker: str) -> bool:
    return bool(re.match(r"^\d{6}\.(SZ|SH|BJ)$", ticker)) and ticker not in {
        "000001.SH",
        "399001.SZ",
        "399006.SZ",
        "000300.SH",
        "000905.SH",
        "000852.SH",
    }
