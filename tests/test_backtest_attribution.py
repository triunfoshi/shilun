import unittest

import pandas as pd

from research.backtest_local.backtest_attribution import (
    build_backtest_attribution,
    build_industry_attribution,
    build_regime_attribution,
    build_tag_attribution,
)


class BacktestAttributionTests(unittest.TestCase):
    def test_builds_industry_attribution_with_concentration(self) -> None:
        industry = build_industry_attribution(sample_positions())

        self.assertEqual({"软件", "电子"}, set(industry["industry"]))
        software = industry.loc[industry["industry"] == "软件"].iloc[0]
        self.assertAlmostEqual(0.5, software["avg_weight"])
        self.assertAlmostEqual(0.03, software["total_contribution"])
        self.assertIn("avg_portfolio_industry_hhi", industry.columns)
        self.assertGreater(industry.loc[0, "avg_portfolio_industry_hhi"], 0)

    def test_builds_tag_attribution_with_overlapping_tags(self) -> None:
        tag = build_tag_attribution(sample_positions())

        self.assertEqual({"rps_breakout", "turtle_breakout", "dividend_quality"}, set(tag["tag"]))
        rps = tag.loc[tag["tag"] == "rps_breakout"].iloc[0]
        self.assertEqual(2, rps["period_count"])
        self.assertAlmostEqual(0.03, rps["total_contribution"])
        turtle = tag.loc[tag["tag"] == "turtle_breakout"].iloc[0]
        self.assertAlmostEqual(0.02, turtle["total_contribution"])

    def test_builds_regime_attribution_from_market_scores(self) -> None:
        regime = build_regime_attribution(sample_period_returns(), sample_positions())

        self.assertEqual({"strong", "weak"}, set(regime["market_regime"]))
        strong = regime.loc[regime["market_regime"] == "strong"].iloc[0]
        weak = regime.loc[regime["market_regime"] == "weak"].iloc[0]
        self.assertAlmostEqual(0.03, strong["mean_return"])
        self.assertAlmostEqual(-0.005, weak["mean_return"])
        self.assertAlmostEqual(1.0, strong["win_rate"])

    def test_builds_all_attribution_tables(self) -> None:
        result = build_backtest_attribution(sample_period_returns(), sample_positions())

        self.assertFalse(result.industry_attribution.empty)
        self.assertFalse(result.tag_attribution.empty)
        self.assertFalse(result.regime_attribution.empty)


def sample_positions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "analysis_date": "2026-05-02",
                "ticker": "000001.SZ",
                "industry": "软件",
                "candidate_tags": "rps_breakout,turtle_breakout",
                "weight": 0.5,
                "daily_return": 0.04,
                "contribution": 0.02,
                "market_trend_score": 66,
            },
            {
                "analysis_date": "2026-05-02",
                "ticker": "000002.SZ",
                "industry": "电子",
                "candidate_tags": "dividend_quality",
                "weight": 0.5,
                "daily_return": 0.02,
                "contribution": 0.01,
                "market_trend_score": 62,
            },
            {
                "analysis_date": "2026-05-03",
                "ticker": "000001.SZ",
                "industry": "软件",
                "candidate_tags": "rps_breakout",
                "weight": 0.5,
                "daily_return": 0.02,
                "contribution": 0.01,
                "market_trend_score": 42,
            },
            {
                "analysis_date": "2026-05-03",
                "ticker": "000003.SZ",
                "industry": "电子",
                "candidate_tags": "dividend_quality",
                "weight": 0.5,
                "daily_return": -0.03,
                "contribution": -0.015,
                "market_trend_score": 40,
            },
        ]
    )


def sample_period_returns() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "analysis_date": "2026-05-02",
                "net_return": 0.03,
                "benchmark_return": 0.01,
                "excess_return": 0.02,
                "turnover": 1.0,
            },
            {
                "analysis_date": "2026-05-03",
                "net_return": -0.005,
                "benchmark_return": 0.0,
                "excess_return": -0.005,
                "turnover": 0.5,
            },
        ]
    )


if __name__ == "__main__":
    unittest.main()
