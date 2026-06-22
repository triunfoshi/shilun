import tempfile
import unittest
from pathlib import Path

from research.validation.strategy_validation import (
    StrategyValidationRequest,
    expand_strategy_signals,
    render_summary_markdown,
    summarize_strategy_performance,
    write_validation_reports,
)


class StrategyValidationTests(unittest.TestCase):
    def test_expands_strategy_signals_and_summarizes_returns(self) -> None:
        rows = expand_strategy_signals(
            [
                {
                    "analysis_date": "2026-03-30",
                    "ticker": "000001.SZ",
                    "rank": 1,
                    "strategy_signals": [
                        {
                            "strategy_id": "shilun_v1",
                            "strategy_version": "2026.05.12",
                            "status": "baseline",
                        },
                        {
                            "strategy_id": "shilun_v1_rps_breakout_test",
                            "strategy_version": "2026.05.12",
                            "status": "testing",
                            "matched_candidate_tags": ["rps_breakout"],
                        },
                    ],
                    "future_return_5d": 0.05,
                    "excess_return_5d": 0.03,
                    "outperform_benchmark_5d": 1,
                    "future_max_drawdown_5d": -0.01,
                },
                {
                    "analysis_date": "2026-03-31",
                    "ticker": "000002.SZ",
                    "rank": 2,
                    "strategy_signals": [
                        {
                            "strategy_id": "shilun_v1",
                            "strategy_version": "2026.05.12",
                            "status": "baseline",
                        }
                    ],
                    "future_return_5d": -0.02,
                    "excess_return_5d": -0.04,
                    "outperform_benchmark_5d": 0,
                    "future_max_drawdown_5d": -0.05,
                },
            ]
        )

        self.assertEqual(3, len(rows))
        summary = summarize_strategy_performance(rows, horizons=(5,), min_promote_samples=2)
        baseline = summary.loc[summary["strategy_id"] == "shilun_v1"].iloc[0]
        rps = summary.loc[summary["strategy_id"] == "shilun_v1_rps_breakout_test"].iloc[0]

        self.assertEqual(2, baseline["sample_count"])
        self.assertEqual(0.015, baseline["mean_return_5d"])
        self.assertEqual("baseline_reference", baseline["decision"])
        self.assertEqual(1, rps["sample_count"])
        self.assertEqual(0.05, rps["mean_return_5d"])
        self.assertEqual("testing_insufficient_samples", rps["decision"])

    def test_writes_summary_and_strategy_reports(self) -> None:
        rows = expand_strategy_signals(
            [
                {
                    "analysis_date": "2026-03-30",
                    "ticker": "000001.SZ",
                    "strategy_ids": "shilun_v1",
                    "strategy_versions": "shilun_v1@2026.05.12",
                    "future_return_5d": 0.01,
                    "excess_return_5d": 0.002,
                    "outperform_benchmark_5d": 1,
                }
            ]
        )
        summary = summarize_strategy_performance(rows, horizons=(5,))
        with tempfile.TemporaryDirectory() as tmp_dir:
            request = StrategyValidationRequest(
                start_date="20260330",
                end_date="20260331",
                output_dir=Path(tmp_dir),
                horizons=(5,),
            )
            markdown = render_summary_markdown(summary, request)
            summary_path, csv_path, strategy_paths = write_validation_reports(summary, request)

            self.assertIn("Mongo `market_snapshot_records`", markdown)
            self.assertTrue(summary_path.exists())
            self.assertTrue(csv_path.exists())
            self.assertIn("shilun_v1", strategy_paths)


if __name__ == "__main__":
    unittest.main()
