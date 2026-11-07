import unittest

from shilun.review.engine import (
    assign_exclusive_role,
    build_expectation_band,
    build_ladder,
    classify_expectation,
    classify_market_posture,
    classify_sector_cycle,
    detect_520,
    detect_red_k_net_inflow,
    detect_turnover_new_faces,
    detect_weekly_breakout,
)


class DailyReviewEngineTests(unittest.TestCase):
    def test_expectation_keeps_above_consensus_and_reversal_separate(self) -> None:
        band = build_expectation_band([-0.03, -0.01, 0.0, 0.01, 0.02, 0.03, 0.05])

        self.assertEqual("above_expectation", classify_expectation(0.08, band)["state"])
        self.assertEqual(
            "consensus",
            classify_expectation(0.08, band, consensus_confirmed=True)["state"],
        )
        self.assertEqual(
            "logic_reversal",
            classify_expectation(0.08, band, logic_reversal=True)["state"],
        )

    def test_market_posture_uses_21000_23000_and_1000_markers(self) -> None:
        indices = [
            {"pct_chg": 0.01, "above_ma5": True, "solid_red_k": True},
            {"pct_chg": 0.02, "above_ma5": True, "solid_red_k": True},
            {"pct_chg": 0.01, "above_ma5": True, "solid_red_k": False},
            {"pct_chg": -0.01, "above_ma5": False, "solid_red_k": False},
        ]
        healthy = classify_market_posture(
            indices,
            market_amount_yi=21_500,
            previous_amount_yi=20_400,
            amount_ma20_yi=20_000,
            up_ratio=0.62,
        )
        large_cap = classify_market_posture(
            indices,
            market_amount_yi=23_100,
            previous_amount_yi=22_900,
            amount_ma20_yi=21_000,
            up_ratio=0.62,
        )

        self.assertEqual("trend_attack", healthy["code"])
        self.assertEqual("healthy", healthy["cap_code"])
        self.assertTrue(healthy["expanded_by_1000"])
        self.assertEqual("large_cap_open", large_cap["cap_code"])

    def test_stock_role_is_one_exclusive_role(self) -> None:
        role = assign_exclusive_role(
            consecutive_count=4,
            sector_highest_board=4,
            market_cap_yi=800,
            amount_yi=50,
            trend_core=True,
            front_row=True,
        )
        self.assertEqual("emotion_leader", role["code"])
        self.assertTrue(role["front_row"])

    def test_ladder_tracks_highest_low_board_and_complete_sector(self) -> None:
        ladder = build_ladder(
            [
                {"ticker": "000001.SZ", "sector_name": "光纤", "consecutive_count": 1},
                {"ticker": "000002.SZ", "sector_name": "光纤", "consecutive_count": 2},
                {"ticker": "000003.SZ", "sector_name": "光纤", "consecutive_count": 3},
                {"ticker": "000004.SZ", "sector_name": "医药", "consecutive_count": 1},
            ]
        )

        self.assertEqual(3, ladder["highest_board"])
        self.assertEqual(["光纤"], ladder["highest_board_sectors"])
        self.assertIn("光纤", ladder["complete_sectors"])
        self.assertEqual("光纤", ladder["low_board_concentration"][0]["sector_name"])

    def test_sector_cycle_uses_decay_vocabulary(self) -> None:
        cycle = classify_sector_cycle(
            first_board_count=0,
            max_board=0,
            board_count=0,
            broken_count=0,
            up_ratio=0.2,
            net_flow=-10,
        )
        self.assertEqual("decay", cycle["code"])
        self.assertEqual("题材衰减", cycle["label"])

    def test_three_frozen_tactics_have_distinct_entry_shapes(self) -> None:
        repair_closes = [35, 34.5, 34, 33.5, 33, 32.5, 32, 31.5, 31, 30.5] + [30] * 10 + [29.5] * 4 + [30.5]
        repair_bars = [
            {"date": f"{index:03d}", "close": close, "low": close - 0.2}
            for index, close in enumerate(repair_closes)
        ]
        breakout_bars = [
            {"date": f"{index:03d}", "high": 10 + index * 0.01, "close": 9.9 + index * 0.01}
            for index in range(100)
        ] + [{"date": "100", "high": 12, "close": 11.2}]

        self.assertIsNotNone(detect_520(repair_bars))
        self.assertIsNotNone(detect_weekly_breakout(breakout_bars))
        self.assertIsNotNone(
            detect_red_k_net_inflow(
                {"open": 10, "high": 11.1, "low": 9.9, "close": 11},
                net_inflow=100,
                market_red_k_confirmed=True,
            )
        )

    def test_turnover_new_faces_only_returns_new_20_yi_names(self) -> None:
        rows = [
            {"ticker": "A", "amount_yi": 30},
            {"ticker": "B", "amount_yi": 25},
            {"ticker": "C", "amount_yi": 19},
        ]
        result = detect_turnover_new_faces(rows, {"B"})
        self.assertEqual(["A"], [item["ticker"] for item in result])


if __name__ == "__main__":
    unittest.main()
