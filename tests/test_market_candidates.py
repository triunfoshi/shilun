import unittest

import pandas as pd

from shilun.market.candidates import build_candidates, evaluate_ma5_selection_gate
from shilun.market.ma5_liquidity import MA5_SELECTION_RULE_VERSION


class MarketCandidateRankingTests(unittest.TestCase):
    @staticmethod
    def _selection_bars(
        *,
        amount: float = 900_000.0,
        total_mv: float = 1_100_000.0,
    ) -> list[dict]:
        dates = pd.date_range("2026-06-01", periods=30, freq="B")
        closes: list[float] = []
        for index in range(len(dates)):
            if not closes:
                closes.append(10.0)
            elif index == 24:
                closes.append(closes[-1] * 1.10)
            else:
                closes.append(closes[-1] * 1.01)
        frame = pd.DataFrame(
            {
                "date": dates,
                "close": closes,
                "amount": [amount] * len(dates),
                "total_mv": [total_mv] * len(dates),
            }
        )
        for window in (5, 10, 20):
            frame[f"ma{window}"] = frame["close"].rolling(window, min_periods=1).mean()
        frame["pct_chg"] = frame["close"].pct_change().fillna(0.0)
        return frame.to_dict("records")

    def test_ma5_selection_gate_requires_original_conditions_and_market_liquidity(self) -> None:
        result = evaluate_ma5_selection_gate(
            ticker="600000.SH",
            name="测试股份",
            bars=self._selection_bars(),
            market_amount_cny=2e12,
        )

        self.assertTrue(result["passed"])
        self.assertEqual([], result["failure_ids"])
        self.assertEqual(5, result["metrics"]["aligned_days_5d"])
        self.assertEqual(1, result["metrics"]["limit_up_count_7d"])
        self.assertEqual(900_000_000.0, result["metrics"]["avg_amount_7d_cny"])
        self.assertEqual(11_000_000_000.0, result["metrics"]["total_mv_cny"])

    def test_ma5_selection_gate_reports_each_failed_condition(self) -> None:
        bars = self._selection_bars(amount=879_999.0, total_mv=999_999.0)
        for row in bars[-7:]:
            row["pct_chg"] = 0.01
        for row in bars[-5:]:
            row["ma5"] = row["ma10"]

        result = evaluate_ma5_selection_gate(
            ticker="600000.SH",
            name="*ST测试",
            bars=bars,
            market_amount_cny=2e12,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(
            {
                "ma_bullish_5d",
                "limit_up_7d",
                "avg_amount_7d",
                "total_market_cap",
                "non_st",
            },
            set(result["failure_ids"]),
        )
        self.assertIn("7日平均成交额", result["summary"])
        self.assertIn("总市值", result["summary"])

    def test_build_candidates_applies_selection_gate_before_effective_ranking(self) -> None:
        frame = pd.DataFrame(self._selection_bars())
        frame["ticker"] = "600000.SH"
        frame["open"] = frame["close"]
        frame["high"] = frame["close"] * 1.01
        frame["low"] = frame["close"] * 0.99
        frame["volume"] = 1_000_000.0
        frame["ma8"] = frame["close"].rolling(8, min_periods=1).mean()

        result = build_candidates(
            top_sectors=[
                {
                    "sector_name": "银行",
                    "candidate_universe": [
                        {
                            "ticker": "600000.SH",
                            "name": "浦发银行",
                            "sector_name": "银行",
                            "leader_score": 80,
                        }
                    ],
                    "metrics": {"resonance_score": 70},
                    "scores": {"sector_mainline_score": 70},
                }
            ],
            stock_frame=frame,
            analysis_date=frame.iloc[-1]["date"].strftime("%Y-%m-%d"),
            trend_sectors=[{"sector_name": "银行", "trend_score": 70}],
            market_amount_cny=2e12,
        )

        self.assertEqual(1, len(result))
        self.assertEqual("eligible", result[0]["ranking_status"])
        self.assertTrue(result[0]["selection_gate"]["passed"])
        self.assertEqual(MA5_SELECTION_RULE_VERSION, result[0]["selection_rule_version"])
        self.assertEqual(1, result[0]["pool_rank"])

    def test_candidate_universe_item_at_limit_down_is_kept_for_explanation_but_excluded(self) -> None:
        dates = pd.date_range("2026-06-01", periods=30, freq="B")
        closes = [10.0] * 29 + [9.0]
        frame = pd.DataFrame(
            {
                "ticker": ["002185.SZ"] * len(dates),
                "date": dates,
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1_000_000.0] * len(dates),
                "amount": [10_000_000.0] * len(dates),
            }
        )
        for window in (5, 8, 10, 20):
            frame[f"ma{window}"] = frame["close"].rolling(window, min_periods=1).mean()
        frame["pct_chg"] = frame["close"].pct_change().fillna(0.0)

        result = build_candidates(
            top_sectors=[
                {
                    "sector_name": "半导体",
                    "candidate_universe": [
                        {
                            "ticker": "002185.SZ",
                            "name": "华天科技",
                            "sector_name": "半导体",
                            "role_label": "总龙头候选",
                            "leader_score": 90,
                            "candidate_source": "session_limit_up",
                            "leader_candidate_rank": 11,
                            "zhongjun_candidate_rank": 36,
                        }
                    ],
                    "leader_candidates": [],
                    "zhongjun_candidates": [],
                    "metrics": {"resonance_score": 80},
                    "scores": {"sector_mainline_score": 80},
                }
            ],
            stock_frame=frame,
            analysis_date=dates[-1].strftime("%Y-%m-%d"),
            trend_sectors=[{"sector_name": "半导体", "trend_score": 80}],
            market_amount_cny=2e12,
        )

        self.assertEqual(1, len(result))
        self.assertEqual("excluded", result[0]["ranking_status"])
        self.assertEqual("风险否决", result[0]["final_signal_label"])
        self.assertEqual(0.0, result[0]["final_trade_score"])
        self.assertIsNone(result[0]["pool_rank"])
        self.assertIn("跌停硬否决", result[0]["risk_flags"])
        self.assertEqual("session_limit_up", result[0]["candidate_source"])
        self.assertEqual(11, result[0]["leader_candidate_rank"])
        self.assertEqual(36, result[0]["zhongjun_candidate_rank"])


if __name__ == "__main__":
    unittest.main()
