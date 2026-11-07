from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
import re
from typing import Any

import pandas as pd

from shilun.backtest import build_trade_signal
from shilun.common.config import load_config
from shilun.data import TushareConfig, TushareDailyClient
from shilun.pipeline import PipelineConfig, ShilunPipeline


SECURITY_RE = re.compile(r"\(([^)]+)\)")


@dataclass(frozen=True)
class ClosedTrade:
    ticker: str
    name: str
    buy_date: str
    sell_date: str
    quantity: int
    buy_price: float
    sell_price: float
    hold_days: int
    net_pnl: float
    gross_return: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review transaction records against the full Shilun pipeline.")
    parser.add_argument("--transactions", required=True, help="Path to the transaction CSV export.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for review outputs.")
    parser.add_argument("--encoding", default="gbk", help="CSV encoding.")
    parser.add_argument("--model-dir", default=None, help="Optional trained model directory passed to the pipeline.")
    return parser.parse_args()


def normalize_ticker(jq_code: str) -> str:
    code, suffix = jq_code.split(".")
    mapping = {"XSHG": "SH", "XSHE": "SZ", "XBEI": "BJ"}
    return f"{code}.{mapping[suffix]}"


def parse_transactions(path: Path, encoding: str) -> pd.DataFrame:
    trades = pd.read_csv(path, encoding=encoding).copy()
    trades["security"] = trades["标的"].str.extract(SECURITY_RE)
    trades["ticker"] = trades["security"].map(normalize_ticker)
    trades["name"] = trades["标的"].str.replace(r"\([^)]*\)", "", regex=True)
    trades["date"] = pd.to_datetime(trades["日期"])
    trades["time"] = trades["委托时间"].astype(str)
    trades["quantity"] = trades["成交数量"].astype(str).str.replace("股", "", regex=False).astype(int)
    trades["abs_quantity"] = trades["quantity"].abs()
    trades["price"] = pd.to_numeric(trades["成交价"], errors="coerce")
    trades["net_close_pnl"] = pd.to_numeric(trades["平仓盈亏"], errors="coerce").fillna(0.0)
    trades["fee"] = pd.to_numeric(trades["手续费"], errors="coerce").fillna(0.0)
    trades = trades.sort_values(["date", "time"]).reset_index(drop=True)
    return trades


def build_closed_trades(transactions: pd.DataFrame) -> list[ClosedTrade]:
    open_lots: dict[str, deque[dict[str, Any]]] = {}
    closed: list[ClosedTrade] = []

    for trade in transactions.itertuples(index=False):
        ticker = trade.ticker
        lots = open_lots.setdefault(ticker, deque())
        quantity = int(trade.quantity)

        if trade.交易类型 == "买" or quantity > 0:
            lots.append(
                {
                    "date": pd.Timestamp(trade.date),
                    "price": float(trade.price),
                    "quantity": int(trade.abs_quantity),
                    "name": trade.name,
                }
            )
            continue

        remaining = int(trade.abs_quantity)
        total_sell_qty = remaining
        while remaining > 0 and lots:
            lot = lots[0]
            matched = min(remaining, int(lot["quantity"]))
            pnl_ratio = matched / total_sell_qty if total_sell_qty else 0.0
            gross_return = float(trade.price) / float(lot["price"]) - 1.0
            closed.append(
                ClosedTrade(
                    ticker=ticker,
                    name=str(lot["name"]),
                    buy_date=lot["date"].strftime("%Y-%m-%d"),
                    sell_date=pd.Timestamp(trade.date).strftime("%Y-%m-%d"),
                    quantity=matched,
                    buy_price=float(lot["price"]),
                    sell_price=float(trade.price),
                    hold_days=(pd.Timestamp(trade.date) - pd.Timestamp(lot["date"])).days,
                    net_pnl=round(float(trade.net_close_pnl) * pnl_ratio, 2),
                    gross_return=round(gross_return, 4),
                )
            )
            lot["quantity"] -= matched
            remaining -= matched
            if lot["quantity"] <= 0:
                lots.popleft()

    return closed


def build_client() -> TushareDailyClient:
    config = load_config()
    return TushareDailyClient(
        TushareConfig(
            token=config.tushare_token or "",
            base_url=config.tushare_base_url or "",
            timeout=config.tushare_timeout,
        )
    )


def fetch_histories(client: TushareDailyClient, tickers: list[str], start_date: str, end_date: str, cache_path: Path) -> dict[str, pd.DataFrame]:
    if cache_path.exists():
        payload = pd.read_pickle(cache_path)
        if all(ticker in payload and not payload[ticker].empty for ticker in tickers):
            return payload
        print(f"[review] cache missing required tickers, rebuilding: {cache_path}", flush=True)

    histories: dict[str, pd.DataFrame] = {}
    total = len(tickers)
    for index, ticker in enumerate(tickers, start=1):
        if ticker == "000300.SH":
            raw_df = client.pro_client.index_daily(ts_code=ticker, start_date=start_date, end_date=end_date)
            frame = client._normalize_daily_frame(raw_df)
        else:
            frame = client.fetch_daily(ticker, start_date=start_date, end_date=end_date)
        histories[ticker] = frame
        if index % 20 == 0 or index == total:
            print(f"[review] fetched {index}/{total} tickers", flush=True)
    pd.to_pickle(histories, cache_path)
    print(f"[review] cached histories to {cache_path}", flush=True)
    return histories


def score_trade_dates(
    pipeline: ShilunPipeline,
    histories: dict[str, pd.DataFrame],
    closed_trades: list[ClosedTrade],
) -> pd.DataFrame:
    benchmark_ticker = pipeline.config.benchmark_ticker or "000300.SH"
    benchmark_history = histories[benchmark_ticker]
    score_cache: dict[tuple[str, str], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    for index, trade in enumerate(closed_trades, start=1):
        buy_score = get_score(
            pipeline=pipeline,
            histories=histories,
            benchmark_history=benchmark_history,
            ticker=trade.ticker,
            date=trade.buy_date,
            cache=score_cache,
        )
        sell_score = get_score(
            pipeline=pipeline,
            histories=histories,
            benchmark_history=benchmark_history,
            ticker=trade.ticker,
            date=trade.sell_date,
            cache=score_cache,
        )
        rows.append(
            {
                "ticker": trade.ticker,
                "name": trade.name,
                "buy_date": trade.buy_date,
                "sell_date": trade.sell_date,
                "hold_days": trade.hold_days,
                "quantity": trade.quantity,
                "buy_price": trade.buy_price,
                "sell_price": trade.sell_price,
                "net_pnl": trade.net_pnl,
                "gross_return": trade.gross_return,
                **prefix_score("buy", buy_score),
                **prefix_score("sell", sell_score),
            }
        )
        if index % 30 == 0 or index == len(closed_trades):
            print(f"[review] scored {index}/{len(closed_trades)} closed trades", flush=True)

    return pd.DataFrame(rows)


def get_score(
    pipeline: ShilunPipeline,
    histories: dict[str, pd.DataFrame],
    benchmark_history: pd.DataFrame,
    ticker: str,
    date: str,
    cache: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    key = (ticker, date)
    cached = cache.get(key)
    if cached is not None:
        return cached

    history = histories[ticker]
    bars = history.loc[history["date"] <= pd.Timestamp(date)].copy()
    benchmark_bars = benchmark_history.loc[benchmark_history["date"] <= pd.Timestamp(date)].copy()
    analysis = pipeline.run_with_bars(
        ticker=ticker,
        analysis_date=date,
        bars=bars,
        benchmark_bars=benchmark_bars,
    )
    signal = build_trade_signal(analysis, is_holding=False)
    snapshot = analysis["snapshot"]
    decision = analysis["decision"]
    payload = {
        "conclusion_label": decision["conclusion_label"],
        "watching_action": decision["watching_action"],
        "holding_action": decision["holding_action"],
        "structure_score": snapshot["structure_score"],
        "trigger_state": snapshot.get("trigger_state"),
        "opportunity_type": snapshot.get("opportunity_type"),
        "entry_probability": snapshot.get("entry_probability"),
        "entry_zone": snapshot.get("entry_zone"),
        "risk_score": snapshot["risk_score"],
        "p_continue_10d": snapshot["p_continue_10d"],
        "p_fail_5d": snapshot["p_fail_5d"],
        "p_acceptance_1d": snapshot.get("p_acceptance_1d"),
        "p_fail_fast_3d": snapshot.get("p_fail_fast_3d"),
        "breakout_quality": snapshot["breakout_quality"],
        "volume_state": snapshot["volume_state"],
        "volume_pattern": snapshot.get("trigger_context", {}).get("volume_pattern"),
        "position_state": snapshot.get("trigger_context", {}).get("position_state"),
        "trend_truth_score": snapshot.get("trigger_context", {}).get("trend_truth_score"),
        "readiness_score": snapshot.get("trigger_context", {}).get("buy_readiness_score"),
        "relative_strength_label": snapshot["market_context"].get("relative_strength_label"),
        "rank_score": round(signal.rank_score, 4),
        "target_weight": round(signal.target_weight, 4),
    }
    cache[key] = payload
    return payload


def prefix_score(prefix: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {f"{prefix}_{key}": value for key, value in payload.items()}


def add_review_flags(frame: pd.DataFrame) -> pd.DataFrame:
    review = frame.copy()
    review["is_loss"] = review["net_pnl"] < 0
    review["buy_full_positive"] = review["buy_conclusion_label"].isin(["high_quality_continuation", "confirmation_needed"])
    review["buy_strong_positive"] = review["buy_conclusion_label"].eq("high_quality_continuation")
    review["sell_still_positive"] = review["sell_conclusion_label"].isin(["high_quality_continuation", "confirmation_needed"])
    review["sell_became_defensive"] = review["sell_conclusion_label"].eq("defense_first")
    review["new_trigger_filter"] = (
        review["buy_opportunity_type"].isin(["observe", "reject"])
        | (review["buy_trigger_state"] != "confirmed")
        | (review["buy_entry_probability"].fillna(0) < 0.5)
        | (review["buy_entry_zone"] == "avoid")
        | (review["buy_p_fail_fast_3d"].fillna(0) >= 0.45)
        | (review["buy_volume_pattern"].isin(["distribution", "high_level_stall", "impulsive_spike"]))
    )
    review["mismatch_type"] = "other"
    review.loc[review["is_loss"] & review["buy_strong_positive"], "mismatch_type"] = "strong_buy_but_loss"
    review.loc[
        review["is_loss"] & review["buy_full_positive"] & review["sell_still_positive"],
        "mismatch_type",
    ] = "system_stayed_positive_but_loss"
    review.loc[
        review["is_loss"] & review["buy_full_positive"] & review["sell_became_defensive"],
        "mismatch_type",
    ] = "buy_looked_good_then_broke"
    return review


def render_markdown(summary: dict[str, Any], cases: pd.DataFrame) -> str:
    lines = [
        "# 交易复盘与石论评分对照",
        "",
        f"- 闭环交易数: {summary['closed_trades']}",
        f"- 亏损交易数: {summary['loss_trades']}",
        f"- 买入日完整版石论给出正向结论且最终亏损: {summary['positive_buy_losses']}",
        f"- 其中买入日强看多且最终亏损: {summary['strong_buy_losses']}",
        f"- 卖出日系统仍偏正向但实际已亏损离场: {summary['sell_still_positive_losses']}",
        f"- 新 trigger 层理论可过滤的亏损交易: {summary['filtered_loss_trades']}",
        f"- 新 trigger 层会同时过滤掉的盈利交易: {summary['filtered_win_trades']}",
        "",
        "## 重点案例",
        "",
    ]
    if cases.empty:
        lines.append("没有匹配案例。")
        return "\n".join(lines)

    display = cases[
        [
            "ticker",
            "name",
            "buy_date",
            "sell_date",
            "hold_days",
            "net_pnl",
            "gross_return",
            "buy_conclusion_label",
            "buy_structure_score",
            "buy_trigger_state",
            "buy_opportunity_type",
            "buy_entry_probability",
            "buy_entry_zone",
            "buy_p_continue_10d",
            "buy_p_acceptance_1d",
            "buy_p_fail_fast_3d",
            "buy_risk_score",
            "buy_breakout_quality",
            "buy_volume_pattern",
            "buy_rank_score",
            "sell_conclusion_label",
            "sell_structure_score",
            "sell_p_continue_10d",
            "sell_risk_score",
            "mismatch_type",
        ]
    ].copy()
    display["gross_return"] = display["gross_return"].map(lambda value: f"{value:.2%}")
    display["buy_p_continue_10d"] = display["buy_p_continue_10d"].map(lambda value: f"{value:.2%}")
    display["sell_p_continue_10d"] = display["sell_p_continue_10d"].map(lambda value: f"{value:.2%}")
    lines.extend(markdown_table(display))
    return "\n".join(lines)


def markdown_table(frame: pd.DataFrame) -> list[str]:
    headers = [str(column) for column in frame.columns]
    rows = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in frame.itertuples(index=False, name=None):
        rows.append("| " + " | ".join(str(value) for value in row) + " |")
    return rows


def main() -> None:
    args = parse_args()
    transactions_path = Path(args.transactions)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    transactions = parse_transactions(transactions_path, args.encoding)
    closed_trades = build_closed_trades(transactions)

    min_buy_date = min(pd.Timestamp(item.buy_date) for item in closed_trades) - timedelta(days=420)
    max_sell_date = max(pd.Timestamp(item.sell_date) for item in closed_trades)
    tickers = sorted({item.ticker for item in closed_trades} | {"000300.SH"})

    cache_path = output_dir / "trade_review_histories.pkl"
    client = build_client()
    histories = fetch_histories(
        client=client,
        tickers=tickers,
        start_date=min_buy_date.strftime("%Y%m%d"),
        end_date=max_sell_date.strftime("%Y%m%d"),
        cache_path=cache_path,
    )

    pipeline_config = PipelineConfig(model_dir=args.model_dir) if args.model_dir else PipelineConfig()
    pipeline = ShilunPipeline(config=pipeline_config)
    review = score_trade_dates(pipeline, histories, closed_trades)
    review = add_review_flags(review)

    review_csv = output_dir / "trade_review_with_scores.csv"
    review.to_csv(review_csv, index=False, encoding="utf-8-sig")

    cases = review.loc[review["is_loss"] & review["buy_full_positive"]].copy()
    cases = cases.sort_values(["net_pnl", "gross_return", "buy_rank_score"])
    focus_cases = cases.head(25)

    summary = {
        "closed_trades": len(review),
        "loss_trades": int(review["is_loss"].sum()),
        "positive_buy_losses": int((review["is_loss"] & review["buy_full_positive"]).sum()),
        "strong_buy_losses": int((review["is_loss"] & review["buy_strong_positive"]).sum()),
        "sell_still_positive_losses": int((review["is_loss"] & review["sell_still_positive"]).sum()),
        "filtered_loss_trades": int((review["is_loss"] & review["new_trigger_filter"]).sum()),
        "filtered_win_trades": int((~review["is_loss"] & review["new_trigger_filter"]).sum()),
    }

    md_path = output_dir / "trade_review_summary.md"
    md_path.write_text(render_markdown(summary, focus_cases), encoding="utf-8")

    summary_path = output_dir / "trade_review_summary.csv"
    pd.DataFrame([summary]).to_csv(summary_path, index=False, encoding="utf-8-sig")

    print(f"review_csv={review_csv}")
    print(f"summary_csv={summary_path}")
    print(f"summary_md={md_path}")


if __name__ == "__main__":
    main()
