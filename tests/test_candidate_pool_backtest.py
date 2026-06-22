import tempfile
import unittest
from pathlib import Path

from research.backtest_local.candidate_pool_backtest import (
    CandidatePoolBacktestRequest,
    alpha_beta,
    build_backtest_dataset,
    build_daily_nav_returns,
    build_period_returns,
    run_candidate_pool_backtest,
    summarize_backtest,
)


class FakeMongoStore:
    def __init__(self, states: list[dict], snapshots: list[dict], daily_bars: list[dict] | None = None) -> None:
        self.states = states
        self.snapshots = snapshots
        self.daily_bars = daily_bars or []

    def find_candidate_pool_states_between(
        self,
        *,
        start_date: str,
        end_date: str,
        exclude_st: bool,
        pool_status: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        matched = [
            dict(state)
            for state in self.states
            if start_date <= state["analysis_date"] <= end_date and bool(state["exclude_st"]) == bool(exclude_st)
        ]
        if pool_status:
            matched = [state for state in matched if state.get("pool_status") == pool_status]
        matched.sort(key=lambda state: (state["analysis_date"], int(state.get("rank") or 0)))
        return matched[:limit] if limit is not None else matched

    def find_market_snapshot_records_between(
        self,
        *,
        start_date: str,
        end_date: str,
        exclude_st: bool,
        limit: int | None = None,
    ) -> list[dict]:
        matched = [
            dict(snapshot)
            for snapshot in self.snapshots
            if start_date <= snapshot["analysis_date"] <= end_date and bool(snapshot["exclude_st"]) == bool(exclude_st)
        ]
        matched.sort(key=lambda snapshot: (snapshot["analysis_date"], int(snapshot.get("rank") or 0)))
        return matched[:limit] if limit is not None else matched

    def find_daily_bars(
        self,
        *,
        start_date: str,
        end_date: str,
        tickers: list[str] | None = None,
    ) -> list[dict]:
        ticker_set = set(tickers or [])
        matched = [
            dict(bar)
            for bar in self.daily_bars
            if start_date <= bar["date"] <= end_date and (not ticker_set or bar["ticker"] in ticker_set)
        ]
        matched.sort(key=lambda bar: (bar["date"], bar["ticker"]))
        return matched


class CandidatePoolBacktestTests(unittest.TestCase):
    def test_builds_period_returns_with_alpha_beta_and_costs(self) -> None:
        request = CandidatePoolBacktestRequest(
            start_date="2026-05-01",
            end_date="2026-05-03",
            pool_status="buy_pool",
            horizon=5,
            top_n=2,
            cost_bps=20,
            output_dir=Path("unused"),
        )
        dataset = build_backtest_dataset(states=sample_states(), snapshots=sample_snapshots(), request=request)
        period_returns, trades = build_period_returns(dataset, request)
        summary = summarize_backtest(period_returns, trades, request)

        self.assertEqual(3, len(period_returns))
        self.assertEqual(6, len(trades))
        self.assertEqual(0.038, period_returns.loc[0, "net_return"])
        self.assertEqual(0.5, period_returns.loc[1, "turnover"])
        self.assertIn("industry", trades.columns)
        self.assertIn("candidate_tags", trades.columns)
        self.assertIn("contribution", trades.columns)
        self.assertIn("alpha", summary.columns)
        self.assertIn("beta", summary.columns)
        self.assertEqual(3, summary.loc[0, "period_count"])
        self.assertEqual(6, summary.loc[0, "trade_count"])
        self.assertGreater(summary.loc[0, "beta"], 0)
        self.assertAlmostEqual(0.666667, summary.loc[0, "win_rate"])
        self.assertAlmostEqual(0.666667, summary.loc[0, "outperform_rate"])

    def test_strategy_filter_reduces_trades(self) -> None:
        request = CandidatePoolBacktestRequest(
            start_date="2026-05-01",
            end_date="2026-05-03",
            pool_status="buy_pool",
            strategy_id="shilun_v1_rps_breakout_test",
            horizon=5,
            top_n=2,
        )
        dataset = build_backtest_dataset(states=sample_states(), snapshots=sample_snapshots(), request=request)
        self.assertEqual({"000001.SZ"}, set(dataset["ticker"]))

    def test_builds_daily_nav_from_market_daily_bars(self) -> None:
        request = CandidatePoolBacktestRequest(
            start_date="2026-05-01",
            end_date="2026-05-04",
            mode="daily_nav",
            pool_status="buy_pool",
            horizon=5,
            top_n=2,
            cost_bps=0,
            slippage_bps=0,
            benchmark_ticker="000300.SH",
        )
        dataset = build_backtest_dataset(states=sample_states(), snapshots=sample_snapshots(), request=request)
        daily_nav, positions = build_daily_nav_returns(dataset, sample_daily_bars(), request)
        summary = summarize_backtest(daily_nav, positions, request)

        self.assertEqual(3, len(daily_nav))
        self.assertEqual(6, len(positions))
        self.assertEqual("2026-05-02", daily_nav.loc[0, "analysis_date"])
        self.assertAlmostEqual(0.005, daily_nav.loc[0, "net_return"])
        self.assertAlmostEqual(0.01, daily_nav.loc[0, "benchmark_return"])
        self.assertEqual(0.5, daily_nav.loc[1, "turnover"])
        self.assertIn("industry", positions.columns)
        self.assertIn("candidate_tags", positions.columns)
        self.assertIn("contribution", positions.columns)
        self.assertEqual("银行", positions.loc[0, "industry"])
        self.assertAlmostEqual(0.01, positions.loc[0, "contribution"])
        self.assertIn("calmar", summary.columns)
        self.assertIn("information_ratio", summary.columns)
        self.assertEqual("daily_nav", summary.loc[0, "mode"])
        self.assertGreater(summary.loc[0, "period_count"], 0)

    def test_run_backtest_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            request = CandidatePoolBacktestRequest(
                start_date="2026-05-01",
                end_date="2026-05-03",
                pool_status="buy_pool",
                horizon=5,
                top_n=2,
                output_dir=Path(tmp_dir),
            )
            result = run_candidate_pool_backtest(request, store=FakeMongoStore(sample_states(), sample_snapshots()))

            self.assertTrue(result.summary_markdown_path.exists())
            self.assertTrue(result.period_returns_csv_path.exists())
            self.assertTrue(result.trades_csv_path.exists())
            self.assertTrue(result.attribution_markdown_path.exists())
            self.assertTrue(result.industry_attribution_csv_path.exists())
            self.assertTrue(result.tag_attribution_csv_path.exists())
            self.assertTrue(result.regime_attribution_csv_path.exists())
            self.assertIn("alpha", result.summary_markdown_path.read_text(encoding="utf-8"))
            self.assertIn("行业归因", result.attribution_markdown_path.read_text(encoding="utf-8"))

    def test_run_daily_nav_backtest_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            request = CandidatePoolBacktestRequest(
                start_date="2026-05-01",
                end_date="2026-05-04",
                mode="daily_nav",
                pool_status="buy_pool",
                top_n=2,
                cost_bps=0,
                output_dir=Path(tmp_dir),
            )
            result = run_candidate_pool_backtest(
                request,
                store=FakeMongoStore(sample_states(), sample_snapshots(), sample_daily_bars()),
            )

            self.assertTrue(result.summary_markdown_path.exists())
            self.assertTrue(result.attribution_markdown_path.exists())
            self.assertIn("daily_nav", result.summary_markdown_path.read_text(encoding="utf-8"))
            self.assertIn("候选标签归因", result.attribution_markdown_path.read_text(encoding="utf-8"))
            self.assertEqual(3, len(result.period_returns))
            self.assertFalse(result.industry_attribution.empty)

    def test_alpha_beta_handles_simple_regression(self) -> None:
        alpha, beta = alpha_beta([0.02, 0.04, 0.06], [0.01, 0.02, 0.03])
        self.assertAlmostEqual(0.0, alpha)
        self.assertAlmostEqual(2.0, beta)


def sample_states() -> list[dict]:
    base = {"exclude_st": True, "pool_status": "buy_pool"}
    rows = [
        {
            **base,
            "analysis_date": "2026-05-01",
            "ticker": "000001.SZ",
            "name": "一号",
            "rank": 1,
            "pool_score": 50,
            "execution_score": 45,
            "risk_score": 20,
            "strategy_ids": "shilun_v1,shilun_v1_rps_breakout_test",
        },
        {
            **base,
            "analysis_date": "2026-05-01",
            "ticker": "000002.SZ",
            "name": "二号",
            "rank": 2,
            "pool_score": 45,
            "execution_score": 40,
            "risk_score": 25,
            "strategy_ids": "shilun_v1",
        },
        {
            **base,
            "analysis_date": "2026-05-02",
            "ticker": "000001.SZ",
            "name": "一号",
            "rank": 1,
            "pool_score": 48,
            "execution_score": 42,
            "risk_score": 22,
            "strategy_ids": "shilun_v1,shilun_v1_rps_breakout_test",
        },
        {
            **base,
            "analysis_date": "2026-05-02",
            "ticker": "000003.SZ",
            "name": "三号",
            "rank": 2,
            "pool_score": 44,
            "execution_score": 39,
            "risk_score": 30,
            "strategy_ids": "shilun_v1",
        },
        {
            **base,
            "analysis_date": "2026-05-03",
            "ticker": "000002.SZ",
            "name": "二号",
            "rank": 1,
            "pool_score": 42,
            "execution_score": 38,
            "risk_score": 24,
            "strategy_ids": "shilun_v1",
        },
        {
            **base,
            "analysis_date": "2026-05-03",
            "ticker": "000003.SZ",
            "name": "三号",
            "rank": 2,
            "pool_score": 40,
            "execution_score": 36,
            "risk_score": 28,
            "strategy_ids": "shilun_v1",
        },
    ]
    for row in rows:
        row.update(_metadata_for(row["ticker"]))
    return rows


def sample_snapshots() -> list[dict]:
    returns = {
        ("2026-05-01", "000001.SZ"): (0.05, 0.02),
        ("2026-05-01", "000002.SZ"): (0.03, 0.02),
        ("2026-05-02", "000001.SZ"): (0.01, 0.02),
        ("2026-05-02", "000003.SZ"): (0.04, 0.02),
        ("2026-05-03", "000002.SZ"): (-0.02, 0.01),
        ("2026-05-03", "000003.SZ"): (0.02, 0.01),
    }
    rows = []
    for index, ((analysis_date, ticker), (future_return, benchmark_return)) in enumerate(returns.items(), start=1):
        rows.append(
            {
                "analysis_date": analysis_date,
                "exclude_st": True,
                "ticker": ticker,
                "rank": index,
                "future_return_5d": future_return,
                "benchmark_future_return_5d": benchmark_return,
                "excess_return_5d": future_return - benchmark_return,
                "strategy_ids": "shilun_v1,shilun_v1_rps_breakout_test" if ticker == "000001.SZ" else "shilun_v1",
                **_metadata_for(ticker),
            }
        )
    return rows


def _metadata_for(ticker: str) -> dict:
    metadata = {
        "000001.SZ": {
            "industry": "银行",
            "candidate_tags": "rps_breakout,turtle_breakout",
            "market_trend_score": 66,
            "sector_trend_score": 58,
        },
        "000002.SZ": {
            "industry": "地产",
            "candidate_tags": "dividend_quality",
            "market_trend_score": 52,
            "sector_trend_score": 49,
        },
        "000003.SZ": {
            "industry": "电子",
            "candidate_tags": "high_tight_flag",
            "market_trend_score": 41,
            "sector_trend_score": 45,
        },
    }
    return dict(metadata[ticker])


def sample_daily_bars() -> list[dict]:
    closes = {
        "2026-05-01": {"000001.SZ": 100.0, "000002.SZ": 200.0, "000003.SZ": 50.0, "000300.SH": 1000.0},
        "2026-05-02": {"000001.SZ": 102.0, "000002.SZ": 198.0, "000003.SZ": 52.0, "000300.SH": 1010.0},
        "2026-05-03": {"000001.SZ": 104.0, "000002.SZ": 202.0, "000003.SZ": 51.0, "000300.SH": 1000.0},
        "2026-05-04": {"000001.SZ": 103.0, "000002.SZ": 204.0, "000003.SZ": 52.0, "000300.SH": 1015.0},
    }
    rows = []
    for date, values in closes.items():
        for ticker, close in values.items():
            rows.append({"date": date, "ticker": ticker, "close": close})
    return rows


if __name__ == "__main__":
    unittest.main()
