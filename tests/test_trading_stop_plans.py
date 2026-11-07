from __future__ import annotations

import unittest

from shilun.trading.stop_plans import (
    StopPlanError,
    build_e7_stop_plan,
    build_ma5_stop_plan,
    build_manual_stop_plan,
)


def _rows(closes: list[float], lows: list[float] | None = None) -> list[dict]:
    low_values = lows or [value - 1 for value in closes]
    return [
        {
            "date": f"2026-06-{index:02d}",
            "close": close,
            "high": close + 1,
            "low": low,
        }
        for index, (close, low) in enumerate(zip(closes, low_values), start=1)
    ]


class TradingStopPlanTests(unittest.TestCase):
    def test_ma5_plan_selects_higher_valid_level_from_ma8_and_previous_low(self) -> None:
        rows = _rows(
            [90, 92, 94, 96, 98, 100, 102, 104, 106, 108],
            [89, 91, 93, 95, 97, 99, 96, 101, 103, 105],
        )

        plan = build_ma5_stop_plan(rows, reference_price=110, as_of="2026-06-10")

        # MA8 = 101，最近确认前低 = 96；默认选择更靠近参考价的 MA8。
        self.assertEqual(plan["recommended_source"], "ma8")
        self.assertEqual(plan["recommended_stop"], 101)
        self.assertEqual(plan["secondary_source"], "previous_swing_low")
        self.assertEqual(plan["secondary_stop"], 96)
        self.assertEqual(len(plan["candidates"]), 2)

    def test_previous_low_wins_when_it_is_above_ma8(self) -> None:
        rows = _rows(
            [90, 90, 90, 90, 90, 90, 90, 104, 100, 105],
            [89, 89, 89, 89, 89, 89, 89, 103, 99, 104],
        )

        plan = build_ma5_stop_plan(rows, reference_price=110)

        self.assertEqual(plan["recommended_source"], "previous_swing_low")
        self.assertEqual(plan["recommended_stop"], 99)

    def test_ma8_is_used_when_no_confirmed_previous_low_exists(self) -> None:
        plan = build_ma5_stop_plan(_rows([91, 92, 93, 94, 95, 96, 97, 98]), reference_price=100)

        self.assertEqual(plan["recommended_source"], "ma8")
        self.assertIsNone(plan["secondary_stop"])

    def test_requires_eight_completed_daily_bars(self) -> None:
        with self.assertRaisesRegex(StopPlanError, "至少需要 8 根"):
            build_ma5_stop_plan(_rows([95, 96, 97]), reference_price=100)

    def test_manual_strategy_selects_higher_of_ma8_and_support_minus_atr(self) -> None:
        closes = [90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104]
        lows = [89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 96, 100, 101, 102, 103]

        plan = build_manual_stop_plan(_rows(closes, lows), reference_price=110)

        self.assertEqual(plan["strategy_id"], "manual")
        self.assertEqual(plan["recommended_source"], "ma8")
        self.assertEqual(plan["recommended_stop"], 100.5)
        self.assertEqual(plan["support"], 96)
        self.assertIn("支撑位 - 1ATR", plan["selection_rule"])

    def test_manual_strategy_requires_enough_bars_for_atr14(self) -> None:
        with self.assertRaisesRegex(StopPlanError, "至少需要 15 根"):
            build_manual_stop_plan(_rows([91, 92, 93, 94, 95, 96, 97, 98]), reference_price=100)

    def test_e7_stop_uses_only_stabilization_low_minus_point_one_atr(self) -> None:
        plan = build_e7_stop_plan(
            _rows([10 + index * 0.1 for index in range(15)]),
            reference_price=12.0,
            stabilization_low=11.5,
            as_of="2026-07-16",
        )

        self.assertEqual(plan["strategy_id"], "e7_oversold_overnight")
        self.assertEqual(plan["recommended_source"], "e7_stabilization_low")
        self.assertAlmostEqual(plan["recommended_stop"], 11.3, places=4)
        self.assertEqual(plan["buffer_atr_factor"], 0.1)
        self.assertEqual(plan["exit_window"], "次一交易日 09:30–10:00")


if __name__ == "__main__":
    unittest.main()
