from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from research.backtest_local.backtest_attribution import (
    BacktestAttributionResult,
    build_backtest_attribution,
    render_attribution_markdown,
)
from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore


DEFAULT_OUTPUT_DIR = Path("research/backtest_local/reports")


@dataclass(frozen=True)
class CandidatePoolBacktestRequest:
    start_date: str
    end_date: str
    mode: str = "label_horizon"
    pool_status: str | None = "buy_pool"
    strategy_id: str | None = None
    horizon: int = 5
    top_n: int = 10
    rebalance: str = "daily"
    cost_bps: float = 20.0
    slippage_bps: float = 0.0
    benchmark_ticker: str = "000300.SH"
    exclude_st: bool = True
    output_dir: Path = DEFAULT_OUTPUT_DIR
    periods_per_year: float | None = None


@dataclass(frozen=True)
class CandidatePoolBacktestResult:
    summary: pd.DataFrame
    period_returns: pd.DataFrame
    trades: pd.DataFrame
    industry_attribution: pd.DataFrame
    tag_attribution: pd.DataFrame
    regime_attribution: pd.DataFrame
    summary_markdown_path: Path
    period_returns_csv_path: Path
    trades_csv_path: Path
    attribution_markdown_path: Path
    industry_attribution_csv_path: Path
    tag_attribution_csv_path: Path
    regime_attribution_csv_path: Path


def run_candidate_pool_backtest(
    request: CandidatePoolBacktestRequest,
    *,
    store: MongoSnapshotStore,
) -> CandidatePoolBacktestResult:
    states = store.find_candidate_pool_states_between(
        start_date=normalize_date_text(request.start_date),
        end_date=normalize_date_text(request.end_date),
        exclude_st=request.exclude_st,
        pool_status=request.pool_status,
    )
    snapshots = store.find_market_snapshot_records_between(
        start_date=normalize_date_text(request.start_date),
        end_date=normalize_date_text(request.end_date),
        exclude_st=request.exclude_st,
    )
    dataset = build_backtest_dataset(states=states, snapshots=snapshots, request=request)
    if request.mode == "daily_nav":
        tickers = sorted({str(ticker) for ticker in dataset.get("ticker", []) if str(ticker)})
        if request.benchmark_ticker:
            tickers.append(request.benchmark_ticker)
        daily_bars = store.find_daily_bars(
            start_date=normalize_date_text(request.start_date),
            end_date=normalize_date_text(request.end_date),
            tickers=sorted(set(tickers)),
        )
        period_returns, trades = build_daily_nav_returns(dataset, daily_bars, request)
    else:
        period_returns, trades = build_period_returns(dataset, request)
    summary = summarize_backtest(period_returns, trades, request)
    attribution = build_backtest_attribution(period_returns, trades)
    (
        summary_path,
        period_path,
        trades_path,
        attribution_path,
        industry_path,
        tag_path,
        regime_path,
    ) = write_backtest_outputs(summary, period_returns, trades, attribution, request)
    return CandidatePoolBacktestResult(
        summary=summary,
        period_returns=period_returns,
        trades=trades,
        industry_attribution=attribution.industry_attribution,
        tag_attribution=attribution.tag_attribution,
        regime_attribution=attribution.regime_attribution,
        summary_markdown_path=summary_path,
        period_returns_csv_path=period_path,
        trades_csv_path=trades_path,
        attribution_markdown_path=attribution_path,
        industry_attribution_csv_path=industry_path,
        tag_attribution_csv_path=tag_path,
        regime_attribution_csv_path=regime_path,
    )


def build_backtest_dataset(
    *,
    states: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    request: CandidatePoolBacktestRequest,
) -> pd.DataFrame:
    if not states or not snapshots:
        return pd.DataFrame()

    state_frame = pd.DataFrame(states).copy()
    snapshot_frame = pd.DataFrame(snapshots).copy()
    for frame in (state_frame, snapshot_frame):
        frame["analysis_date"] = frame["analysis_date"].map(normalize_date_text)
        frame["ticker"] = frame["ticker"].astype(str)

    metric_cols = [
        "analysis_date",
        "ticker",
        f"future_return_{request.horizon}d",
        f"benchmark_future_return_{request.horizon}d",
        f"excess_return_{request.horizon}d",
        f"outperform_benchmark_{request.horizon}d",
        "strategy_ids",
        "strategy_versions",
        "strategy_signals",
        "industry",
        "candidate_tags",
        "market_trend_score",
        "sector_trend_score",
        "pool_status",
        "pool_score",
        "execution_score",
        "risk_score",
    ]
    snapshot_cols = [col for col in metric_cols if col in snapshot_frame.columns]
    dataset = state_frame.merge(
        snapshot_frame[snapshot_cols],
        on=["analysis_date", "ticker"],
        how="left",
        suffixes=("", "_snapshot"),
    )
    if "strategy_ids_snapshot" in dataset.columns:
        if "strategy_ids" not in dataset.columns:
            dataset["strategy_ids"] = ""
        dataset["strategy_ids"] = dataset["strategy_ids"].fillna("")
        dataset["strategy_ids"] = dataset["strategy_ids"].where(dataset["strategy_ids"].astype(str) != "", dataset["strategy_ids_snapshot"])
    for col in (
        "industry",
        "candidate_tags",
        "market_trend_score",
        "sector_trend_score",
        "pool_status",
        "pool_score",
        "execution_score",
        "risk_score",
    ):
        snapshot_col = f"{col}_snapshot"
        if snapshot_col not in dataset.columns:
            continue
        if col not in dataset.columns:
            dataset[col] = pd.NA
        dataset[col] = dataset[col].where(~dataset[col].map(_is_blank_value), dataset[snapshot_col])
    if request.strategy_id:
        dataset = dataset.loc[dataset.apply(lambda row: _has_strategy(row, request.strategy_id or ""), axis=1)].copy()
    return dataset.reset_index(drop=True)


def build_period_returns(
    dataset: pd.DataFrame,
    request: CandidatePoolBacktestRequest,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    return_col = f"future_return_{request.horizon}d"
    benchmark_col = f"benchmark_future_return_{request.horizon}d"
    if dataset.empty or return_col not in dataset.columns:
        return pd.DataFrame(), pd.DataFrame()

    frame = dataset.copy()
    frame[return_col] = pd.to_numeric(frame[return_col], errors="coerce")
    frame[benchmark_col] = pd.to_numeric(frame.get(benchmark_col), errors="coerce") if benchmark_col in frame else pd.NA
    frame["rank"] = pd.to_numeric(frame.get("rank"), errors="coerce").fillna(999999).astype(int)
    frame["pool_score"] = pd.to_numeric(frame.get("pool_score"), errors="coerce").fillna(0.0)
    frame["execution_score"] = pd.to_numeric(frame.get("execution_score"), errors="coerce").fillna(0.0)
    frame = frame.dropna(subset=[return_col]).sort_values(["analysis_date", "rank", "pool_score", "execution_score"], ascending=[True, True, False, False])

    period_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    previous_weights: dict[str, float] = {}
    cost_rate = max(0.0, float(request.cost_bps)) / 10000.0

    for analysis_date, group in frame.groupby("analysis_date", sort=True):
        selected = group.head(max(1, int(request.top_n))).copy()
        if selected.empty:
            continue
        weight = 1.0 / len(selected)
        current_weights = {str(row["ticker"]): weight for row in selected.to_dict(orient="records")}
        turnover = _turnover(previous_weights, current_weights)
        gross_return = float(selected[return_col].mean())
        benchmark_return = _first_numeric(selected[benchmark_col]) if benchmark_col in selected else None
        cost = turnover * cost_rate
        net_return = gross_return - cost
        excess_return = None if benchmark_return is None else net_return - benchmark_return
        period_rows.append(
            {
                "analysis_date": analysis_date,
                "position_count": int(len(selected)),
                "turnover": round(turnover, 6),
                "cost": round(cost, 6),
                "gross_return": round(gross_return, 6),
                "net_return": round(net_return, 6),
                "benchmark_return": None if benchmark_return is None else round(benchmark_return, 6),
                "excess_return": None if excess_return is None else round(excess_return, 6),
                "tickers": ",".join(current_weights),
            }
        )
        for row in selected.to_dict(orient="records"):
            holding_return = row.get(return_col)
            trade_rows.append(
                {
                    "analysis_date": analysis_date,
                    "ticker": row.get("ticker"),
                    "name": row.get("name"),
                    "pool_status": row.get("pool_status"),
                    "rank": row.get("rank"),
                    "weight": round(weight, 6),
                    "contribution": _round_or_none(weight * float(holding_return)) if holding_return is not None and not pd.isna(holding_return) else None,
                    "pool_score": row.get("pool_score"),
                    "execution_score": row.get("execution_score"),
                    "risk_score": row.get("risk_score"),
                    "strategy_ids": row.get("strategy_ids"),
                    "industry": row.get("industry"),
                    "candidate_tags": _join_values(row.get("candidate_tags")),
                    "market_trend_score": row.get("market_trend_score"),
                    "sector_trend_score": row.get("sector_trend_score"),
                    return_col: row.get(return_col),
                    benchmark_col: row.get(benchmark_col),
                }
            )
        previous_weights = current_weights

    period_frame = pd.DataFrame(period_rows)
    if not period_frame.empty:
        period_frame = add_equity_metrics(period_frame)
    return period_frame, pd.DataFrame(trade_rows)


def build_daily_nav_returns(
    dataset: pd.DataFrame,
    daily_bars: list[dict[str, Any]],
    request: CandidatePoolBacktestRequest,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if dataset.empty or not daily_bars:
        return pd.DataFrame(), pd.DataFrame()

    selections = build_rebalance_selections(dataset, request)
    if not selections:
        return pd.DataFrame(), pd.DataFrame()

    prices = build_price_matrix(daily_bars)
    if prices.empty:
        return pd.DataFrame(), pd.DataFrame()

    trading_dates = list(prices.index)
    portfolio_rows: list[dict[str, Any]] = []
    position_rows: list[dict[str, Any]] = []
    active_weights: dict[str, float] = {}
    active_metadata: dict[str, dict[str, Any]] = {}
    previous_weights: dict[str, float] = {}
    total_cost_rate = max(0.0, float(request.cost_bps) + float(request.slippage_bps)) / 10000.0

    for index in range(1, len(trading_dates)):
        previous_date = trading_dates[index - 1]
        current_date = trading_dates[index]
        previous_date_text = previous_date.strftime("%Y-%m-%d")
        rebalance_rows = selections.get(previous_date_text)
        turnover = 0.0
        if rebalance_rows is not None:
            active_weights = _equal_weights([str(row.get("ticker") or "") for row in rebalance_rows])
            active_metadata = {str(row.get("ticker") or ""): dict(row) for row in rebalance_rows if str(row.get("ticker") or "")}
            turnover = _turnover(previous_weights, active_weights)
            previous_weights = dict(active_weights)
        if not active_weights:
            continue

        ticker_returns: dict[str, float] = {}
        gross_return = 0.0
        for ticker, weight in active_weights.items():
            daily_return = _ticker_return(prices, ticker, previous_date, current_date)
            if daily_return is None:
                daily_return = 0.0
            ticker_returns[ticker] = daily_return
            gross_return += weight * daily_return

        cost = turnover * total_cost_rate
        net_return = gross_return - cost
        benchmark_return = _ticker_return(prices, request.benchmark_ticker, previous_date, current_date) if request.benchmark_ticker else None
        excess_return = None if benchmark_return is None else net_return - benchmark_return
        portfolio_rows.append(
            {
                "analysis_date": current_date.strftime("%Y-%m-%d"),
                "signal_date": previous_date_text,
                "position_count": len(active_weights),
                "turnover": round(turnover, 6),
                "cost": round(cost, 6),
                "gross_return": round(gross_return, 6),
                "net_return": round(net_return, 6),
                "benchmark_return": None if benchmark_return is None else round(benchmark_return, 6),
                "excess_return": None if excess_return is None else round(excess_return, 6),
                "tickers": ",".join(active_weights),
            }
        )
        for ticker, weight in active_weights.items():
            metadata = active_metadata.get(ticker, {})
            daily_return = ticker_returns.get(ticker, 0.0)
            position_rows.append(
                {
                    "analysis_date": current_date.strftime("%Y-%m-%d"),
                    "signal_date": previous_date_text,
                    "ticker": ticker,
                    "name": metadata.get("name"),
                    "pool_status": metadata.get("pool_status"),
                    "rank": metadata.get("rank"),
                    "weight": round(weight, 6),
                    "contribution": round(weight * daily_return, 6),
                    "daily_return": round(daily_return, 6),
                    "pool_score": metadata.get("pool_score"),
                    "execution_score": metadata.get("execution_score"),
                    "risk_score": metadata.get("risk_score"),
                    "strategy_ids": metadata.get("strategy_ids"),
                    "industry": metadata.get("industry"),
                    "candidate_tags": _join_values(metadata.get("candidate_tags")),
                    "market_trend_score": metadata.get("market_trend_score"),
                    "sector_trend_score": metadata.get("sector_trend_score"),
                }
            )

    period_frame = pd.DataFrame(portfolio_rows)
    if not period_frame.empty:
        period_frame = add_equity_metrics(period_frame)
    return period_frame, pd.DataFrame(position_rows)


def build_rebalance_selections(dataset: pd.DataFrame, request: CandidatePoolBacktestRequest) -> dict[str, list[dict[str, Any]]]:
    frame = dataset.copy()
    if frame.empty:
        return {}
    frame["analysis_date"] = frame["analysis_date"].map(normalize_date_text)
    frame["rank"] = pd.to_numeric(frame.get("rank"), errors="coerce").fillna(999999).astype(int)
    frame["pool_score"] = pd.to_numeric(frame.get("pool_score"), errors="coerce").fillna(0.0)
    frame["execution_score"] = pd.to_numeric(frame.get("execution_score"), errors="coerce").fillna(0.0)
    frame = frame.sort_values(["analysis_date", "rank", "pool_score", "execution_score"], ascending=[True, True, False, False])

    selected_dates = sorted(frame["analysis_date"].dropna().unique().tolist())
    if request.rebalance == "weekly":
        selected_dates = _first_date_per_week(selected_dates)
    elif request.rebalance != "daily":
        raise ValueError("rebalance must be 'daily' or 'weekly'")

    selections: dict[str, list[dict[str, Any]]] = {}
    for analysis_date in selected_dates:
        day_group = frame.loc[frame["analysis_date"] == analysis_date]
        selections[str(analysis_date)] = day_group.head(max(1, int(request.top_n))).to_dict(orient="records")
    return selections


def summarize_backtest(
    period_returns: pd.DataFrame,
    trades: pd.DataFrame,
    request: CandidatePoolBacktestRequest,
) -> pd.DataFrame:
    periods_per_year = _periods_per_year(request)
    if period_returns.empty:
        return pd.DataFrame(
            [
                {
                    "start_date": normalize_date_text(request.start_date),
                    "end_date": normalize_date_text(request.end_date),
                    "mode": request.mode,
                    "pool_status": request.pool_status or "all",
                    "strategy_id": request.strategy_id or "",
                    "horizon": int(request.horizon),
                    "top_n": int(request.top_n),
                    "period_count": 0,
                    "trade_count": 0,
                }
            ]
        )

    net = pd.to_numeric(period_returns["net_return"], errors="coerce").dropna()
    benchmark = pd.to_numeric(period_returns.get("benchmark_return"), errors="coerce").dropna()
    aligned = period_returns[["net_return", "benchmark_return"]].dropna()
    alpha, beta = alpha_beta(aligned["net_return"], aligned["benchmark_return"]) if len(aligned) >= 2 else (None, None)
    total_return = _compound(net)
    benchmark_total_return = _compound(benchmark) if not benchmark.empty else None
    annualized_return = _annualize(total_return, len(net), periods_per_year)
    annualized_benchmark_return = _annualize(benchmark_total_return, len(benchmark), periods_per_year) if benchmark_total_return is not None else None
    volatility = float(net.std(ddof=1)) * (periods_per_year**0.5) if len(net) > 1 else None
    sharpe = None if not volatility or volatility == 0 else float(net.mean()) * periods_per_year / volatility
    summary = {
        "start_date": normalize_date_text(request.start_date),
        "end_date": normalize_date_text(request.end_date),
        "mode": request.mode,
        "rebalance": request.rebalance,
        "pool_status": request.pool_status or "all",
        "strategy_id": request.strategy_id or "",
        "horizon": int(request.horizon),
        "top_n": int(request.top_n),
        "cost_bps": float(request.cost_bps),
        "slippage_bps": float(request.slippage_bps),
        "benchmark_ticker": request.benchmark_ticker,
        "periods_per_year": round(float(periods_per_year), 4),
        "period_count": int(len(period_returns)),
        "trade_count": int(len(trades)),
        "avg_position_count": round(float(period_returns["position_count"].mean()), 4),
        "avg_turnover": round(float(period_returns["turnover"].mean()), 4),
        "total_return": _round_or_none(total_return),
        "annualized_return": _round_or_none(annualized_return),
        "benchmark_total_return": _round_or_none(benchmark_total_return),
        "annualized_benchmark_return": _round_or_none(annualized_benchmark_return),
        "mean_period_return": _round_or_none(float(net.mean()) if not net.empty else None),
        "mean_benchmark_return": _round_or_none(float(benchmark.mean()) if not benchmark.empty else None),
        "mean_excess_return": _round_or_none(float(period_returns["excess_return"].dropna().mean()) if "excess_return" in period_returns else None),
        "win_rate": _round_or_none(float((net > 0).mean()) if not net.empty else None),
        "outperform_rate": _round_or_none(float((aligned["net_return"] > aligned["benchmark_return"]).mean()) if not aligned.empty else None),
        "volatility": _round_or_none(volatility),
        "sharpe": _round_or_none(sharpe),
        "max_drawdown": _round_or_none(float(period_returns["drawdown"].min()) if "drawdown" in period_returns else None),
        "calmar": _round_or_none(_calmar(annualized_return, float(period_returns["drawdown"].min()) if "drawdown" in period_returns else None)),
        "information_ratio": _round_or_none(_information_ratio(period_returns.get("excess_return"), periods_per_year)),
        "alpha": _round_or_none(alpha),
        "alpha_annualized": _round_or_none(_annualize(alpha, 1, periods_per_year) if alpha is not None else None),
        "beta": _round_or_none(beta),
    }
    return pd.DataFrame([summary])


def add_equity_metrics(period_returns: pd.DataFrame) -> pd.DataFrame:
    frame = period_returns.copy()
    frame["equity"] = (1.0 + pd.to_numeric(frame["net_return"], errors="coerce").fillna(0.0)).cumprod()
    frame["benchmark_equity"] = (1.0 + pd.to_numeric(frame["benchmark_return"], errors="coerce").fillna(0.0)).cumprod()
    frame["equity_peak"] = frame["equity"].cummax()
    frame["drawdown"] = frame["equity"] / frame["equity_peak"] - 1.0
    return frame


def alpha_beta(portfolio_returns: Iterable[float], benchmark_returns: Iterable[float]) -> tuple[float | None, float | None]:
    frame = pd.DataFrame(
        {
            "portfolio": pd.to_numeric(pd.Series(list(portfolio_returns)), errors="coerce"),
            "benchmark": pd.to_numeric(pd.Series(list(benchmark_returns)), errors="coerce"),
        }
    ).dropna()
    if len(frame) < 2:
        return None, None
    benchmark_var = float(frame["benchmark"].var(ddof=0))
    if benchmark_var == 0:
        return float(frame["portfolio"].mean()), None
    beta = float(frame["portfolio"].cov(frame["benchmark"], ddof=0) / benchmark_var)
    alpha = float(frame["portfolio"].mean() - beta * frame["benchmark"].mean())
    return alpha, beta


def write_backtest_outputs(
    summary: pd.DataFrame,
    period_returns: pd.DataFrame,
    trades: pd.DataFrame,
    attribution: BacktestAttributionResult,
    request: CandidatePoolBacktestRequest,
) -> tuple[Path, Path, Path, Path, Path, Path, Path]:
    output_dir = Path(request.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _output_stem(request)
    summary_path = output_dir / f"{stem}_summary.md"
    period_path = output_dir / f"{stem}_period_returns.csv"
    trades_path = output_dir / f"{stem}_trades.csv"
    attribution_path = output_dir / f"{stem}_attribution_summary.md"
    industry_path = output_dir / f"{stem}_industry_attribution.csv"
    tag_path = output_dir / f"{stem}_tag_attribution.csv"
    regime_path = output_dir / f"{stem}_regime_attribution.csv"
    summary_path.write_text(render_summary_markdown(summary, request), encoding="utf-8")
    attribution_path.write_text(render_attribution_markdown(attribution), encoding="utf-8")
    period_returns.to_csv(period_path, index=False, encoding="utf-8-sig")
    trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    attribution.industry_attribution.to_csv(industry_path, index=False, encoding="utf-8-sig")
    attribution.tag_attribution.to_csv(tag_path, index=False, encoding="utf-8-sig")
    attribution.regime_attribution.to_csv(regime_path, index=False, encoding="utf-8-sig")
    return summary_path, period_path, trades_path, attribution_path, industry_path, tag_path, regime_path


def render_summary_markdown(summary: pd.DataFrame, request: CandidatePoolBacktestRequest) -> str:
    lines = [
        "# 候选池组合回测报告",
        "",
        f"- 区间: {normalize_date_text(request.start_date)} 至 {normalize_date_text(request.end_date)}",
        f"- 模式: {request.mode}",
        f"- 池状态: {request.pool_status or 'all'}",
        f"- 策略过滤: {request.strategy_id or '不限'}",
        f"- 持有期: {request.horizon}d",
        f"- 调仓: {request.rebalance}",
        f"- TopN: {request.top_n}",
        f"- 成本: {request.cost_bps} bps + 滑点 {request.slippage_bps} bps / 100% 换手",
        "- 数据源: Mongo `candidate_pool_states` + `market_snapshot_records` + `market_daily_bars`",
        "",
        "## 指标",
        "",
        _markdown_table(summary),
        "",
        "## 口径说明",
        "",
        "- `label_horizon` 使用 `future_return_Nd` 做持有期收益标签。",
        "- `daily_nav` 使用 Mongo `market_daily_bars` 做连续日频净值曲线，alpha/beta 为日频组合收益对基准收益的回归结果。",
        "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest candidate-pool portfolios from Mongo records.")
    parser.add_argument("--start-date", required=True, help="Start analysis_date, e.g. 2026-03-01.")
    parser.add_argument("--end-date", required=True, help="End analysis_date, e.g. 2026-05-16.")
    parser.add_argument("--mode", default="label_horizon", choices=("label_horizon", "daily_nav"), help="Backtest mode.")
    parser.add_argument("--pool-status", default="buy_pool", help="Pool status to backtest, or 'all'.")
    parser.add_argument("--strategy-id", default=None, help="Optional strategy_id filter.")
    parser.add_argument("--horizon", type=int, default=5, help="Forward return horizon in days.")
    parser.add_argument("--top-n", type=int, default=10, help="Max equal-weight positions per period.")
    parser.add_argument("--rebalance", default="daily", choices=("daily", "weekly"), help="Rebalance frequency for daily_nav mode.")
    parser.add_argument("--cost-bps", type=float, default=20.0, help="Cost in bps per 100%% turnover.")
    parser.add_argument("--slippage-bps", type=float, default=0.0, help="Slippage in bps per 100%% turnover.")
    parser.add_argument("--benchmark-ticker", default="000300.SH", help="Benchmark ticker for daily_nav mode.")
    parser.add_argument("--include-st", action="store_true", help="Use include-ST snapshot scope instead of the default exclude-ST scope.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Report output directory.")
    parser.add_argument("--mongo-uri", default=None, help="Override SHILUN_MONGO_URI.")
    parser.add_argument("--mongo-db", default=None, help="Override SHILUN_MONGO_DB.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    mongo_uri = args.mongo_uri or config.mongo_uri
    mongo_db = args.mongo_db or config.mongo_db
    if not mongo_uri:
        raise ValueError("SHILUN_MONGO_URI is required for candidate pool backtest.")

    request = CandidatePoolBacktestRequest(
        start_date=args.start_date,
        end_date=args.end_date,
        mode=args.mode,
        pool_status=None if args.pool_status == "all" else args.pool_status,
        strategy_id=args.strategy_id,
        horizon=args.horizon,
        top_n=args.top_n,
        rebalance=args.rebalance,
        cost_bps=args.cost_bps,
        slippage_bps=args.slippage_bps,
        benchmark_ticker=args.benchmark_ticker,
        exclude_st=not args.include_st,
        output_dir=Path(args.output_dir),
    )
    store = MongoSnapshotStore(mongo_uri, mongo_db)
    try:
        result = run_candidate_pool_backtest(request, store=store)
    finally:
        store.close()
    print(f"period_count={int(result.summary.loc[0, 'period_count']) if not result.summary.empty else 0}")
    print(f"trade_count={int(result.summary.loc[0, 'trade_count']) if not result.summary.empty else 0}")
    print(f"summary_markdown_path={result.summary_markdown_path}")
    print(f"period_returns_csv_path={result.period_returns_csv_path}")
    print(f"trades_csv_path={result.trades_csv_path}")
    print(f"attribution_markdown_path={result.attribution_markdown_path}")
    print(f"industry_attribution_csv_path={result.industry_attribution_csv_path}")
    print(f"tag_attribution_csv_path={result.tag_attribution_csv_path}")
    print(f"regime_attribution_csv_path={result.regime_attribution_csv_path}")


def normalize_date_text(value: str) -> str:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _has_strategy(row: pd.Series, strategy_id: str) -> bool:
    values = _split_values(row.get("strategy_ids")) + _split_values(row.get("strategy_ids_snapshot"))
    return strategy_id in values


def _split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _join_values(value: Any) -> str:
    return ",".join(_split_values(value))


def _is_blank_value(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _first_numeric(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.iloc[0])


def _turnover(previous: dict[str, float], current: dict[str, float]) -> float:
    if not previous:
        return round(sum(current.values()), 6)
    tickers = set(previous) | set(current)
    return round(sum(abs(current.get(ticker, 0.0) - previous.get(ticker, 0.0)) for ticker in tickers) / 2.0, 6)


def _compound(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float((1.0 + clean).prod() - 1.0)


def _annualize(total_return: float | None, period_count: int, periods_per_year: float) -> float | None:
    if total_return is None or period_count <= 0 or total_return <= -1.0:
        return None
    return float((1.0 + total_return) ** (periods_per_year / period_count) - 1.0)


def _periods_per_year(request: CandidatePoolBacktestRequest) -> float:
    if request.periods_per_year is not None:
        return float(request.periods_per_year)
    if request.mode == "daily_nav":
        return 252.0
    return 252.0 / max(1, int(request.horizon))


def _calmar(annualized_return: float | None, max_drawdown: float | None) -> float | None:
    if annualized_return is None or max_drawdown is None or max_drawdown == 0:
        return None
    return float(annualized_return / abs(max_drawdown))


def _information_ratio(excess_returns: pd.Series | None, periods_per_year: float) -> float | None:
    if excess_returns is None:
        return None
    clean = pd.to_numeric(excess_returns, errors="coerce").dropna()
    if len(clean) < 2:
        return None
    tracking_error = float(clean.std(ddof=1)) * (periods_per_year**0.5)
    if tracking_error == 0:
        return None
    return float(clean.mean()) * periods_per_year / tracking_error


def _round_or_none(value: float | None) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 6)


def _output_stem(request: CandidatePoolBacktestRequest) -> str:
    pool = request.pool_status or "all"
    strategy = request.strategy_id or "all_strategy"
    if request.mode == "daily_nav":
        return f"candidate_pool_daily_nav_{pool}_{strategy}_{request.rebalance}_top{request.top_n}"
    return f"candidate_pool_backtest_{pool}_{strategy}_{request.horizon}d_top{request.top_n}"


def build_price_matrix(daily_bars: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(daily_bars)
    if frame.empty:
        return pd.DataFrame()
    frame["ticker"] = frame["ticker"].astype(str)
    frame["date"] = pd.to_datetime(frame["date"].map(normalize_date_text), errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["ticker", "date", "close"])
    if frame.empty:
        return pd.DataFrame()
    return frame.pivot_table(index="date", columns="ticker", values="close", aggfunc="last").sort_index()


def _ticker_return(prices: pd.DataFrame, ticker: str, previous_date: pd.Timestamp, current_date: pd.Timestamp) -> float | None:
    if not ticker or ticker not in prices.columns:
        return None
    try:
        previous_close = prices.loc[previous_date, ticker]
        current_close = prices.loc[current_date, ticker]
    except KeyError:
        return None
    if pd.isna(previous_close) or pd.isna(current_close) or float(previous_close) == 0:
        return None
    return float(current_close) / float(previous_close) - 1.0


def _equal_weights(tickers: list[str]) -> dict[str, float]:
    cleaned = [ticker for ticker in tickers if ticker]
    if not cleaned:
        return {}
    weight = 1.0 / len(cleaned)
    return {ticker: weight for ticker in cleaned}


def _first_date_per_week(date_texts: list[str]) -> list[str]:
    seen: set[str] = set()
    selected: list[str] = []
    for date_text in date_texts:
        week_key = pd.Timestamp(date_text).to_period("W").strftime("%Y-%m-%d")
        if week_key in seen:
            continue
        seen.add(week_key)
        selected.append(date_text)
    return selected


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "暂无回测结果。"
    columns = [str(column) for column in frame.columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame.to_dict(orient="records"):
        lines.append("| " + " | ".join(_format_table_value(row.get(column)) for column in columns) + " |")
    return "\n".join(lines)


def _format_table_value(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return str(value)


if __name__ == "__main__":
    main()
