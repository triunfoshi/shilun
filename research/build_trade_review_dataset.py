from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from review_trade_decisions import build_closed_trades, parse_transactions
from shilun.models import (
    label_acceptance_1d,
    label_breakout_success,
    label_continue_10d,
    label_drawdown_bucket,
    label_entry_success_3d,
    label_exhaustion_5d,
    label_fail_fast_3d,
    label_fail_5d,
    label_return_profile,
)
from shilun.pipeline import ShilunPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a model dataset from real closed trades and cached histories.")
    parser.add_argument("--transactions", required=True, help="Transaction CSV path.")
    parser.add_argument("--histories", required=True, help="Pickle path produced by review_trade_decisions.py")
    parser.add_argument("--output", required=True, help="Output parquet/csv path.")
    parser.add_argument("--encoding", default="gbk", help="Transaction CSV encoding.")
    return parser.parse_args()


def build_label_lookup(histories: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for ticker, history in histories.items():
        if ticker == "000300.SH" or history is None or history.empty:
            continue
        frame = history.sort_values("date").reset_index(drop=True).copy()
        labels = pd.DataFrame({"ticker": ticker, "date": frame["date"]})
        labels["continue_10d"] = label_continue_10d(frame)
        labels["breakout_success"] = label_breakout_success(frame)
        labels["fail_5d"] = label_fail_5d(frame)
        labels["fail_fast_3d"] = label_fail_fast_3d(frame)
        labels["acceptance_1d"] = label_acceptance_1d(frame)
        labels["entry_success_3d"] = label_entry_success_3d(frame)
        labels["exhaustion_5d"] = label_exhaustion_5d(frame)
        labels["return_profile"] = label_return_profile(frame)
        labels["drawdown_bucket"] = label_drawdown_bucket(frame)
        labels["expected_return_10d"] = frame["close"].shift(-10) / frame["close"] - 1.0
        future_min = frame["close"].shift(-1).iloc[::-1].rolling(10, min_periods=1).min().iloc[::-1]
        labels["expected_drawdown_10d"] = (future_min / frame["close"] - 1.0).abs()
        frames.append(labels)
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    args = parse_args()
    transactions = parse_transactions(Path(args.transactions), args.encoding)
    closed_trades = build_closed_trades(transactions)
    histories: dict[str, pd.DataFrame] = pd.read_pickle(args.histories)
    benchmark_history = histories["000300.SH"]
    pipeline = ShilunPipeline()
    label_lookup = build_label_lookup(histories)
    label_lookup["date"] = pd.to_datetime(label_lookup["date"])
    label_index = label_lookup.set_index(["ticker", "date"])

    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for trade in closed_trades:
        key = (trade.ticker, trade.buy_date)
        if key in seen:
            continue
        seen.add(key)
        history = histories[trade.ticker]
        bars = history.loc[history["date"] <= pd.Timestamp(trade.buy_date)].copy()
        benchmark_bars = benchmark_history.loc[benchmark_history["date"] <= pd.Timestamp(trade.buy_date)].copy()
        prepared_bars = pipeline._prepare_bars(ticker=trade.ticker, analysis_date=trade.buy_date, bars=bars)
        prepared_benchmark = pipeline._prepare_bars(
            ticker=pipeline.config.benchmark_ticker or "000300.SH",
            analysis_date=trade.buy_date,
            bars=benchmark_bars,
        )
        payload = pipeline._build_snapshot_payload(
            ticker=trade.ticker,
            analysis_date=trade.buy_date,
            bars=prepared_bars,
            benchmark_bars=prepared_benchmark,
        )
        feature_row = {
            "ticker": trade.ticker,
            "date": pd.Timestamp(trade.buy_date),
            **payload.get("feature_context", {}),
            **payload.get("structure_context", {}),
        }
        labels = label_index.loc[(trade.ticker, pd.Timestamp(trade.buy_date))].to_dict()
        feature_row.update(labels)
        rows.append(feature_row)

    dataset = pd.DataFrame(rows).sort_values(["ticker", "date"]).reset_index(drop=True)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix == ".parquet":
        dataset.to_parquet(output_path, index=False)
    else:
        dataset.to_csv(output_path, index=False)
    print(f"dataset_rows={len(dataset)}")
    print(f"dataset_tickers={dataset['ticker'].nunique() if not dataset.empty else 0}")
    print(f"dataset_path={output_path}")


if __name__ == "__main__":
    main()
