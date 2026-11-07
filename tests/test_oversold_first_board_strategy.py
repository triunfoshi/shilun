from __future__ import annotations

import copy
import unittest
from datetime import date, timedelta

from shilun.strategies.oversold_first_board import (
    RULE_ID,
    OversoldFirstBoardSelectionStrategy,
    evaluate_oversold_first_board,
)


def _sample_bars(
    *,
    with_limit_up: bool = False,
    flat_long_term: bool = False,
) -> list[dict[str, float | str]]:
    early_closes = (
        [8.0] * 40
        if flat_long_term
        else [12.0 - 0.05 * index for index in range(40)]
    )
    recent_closes = [
        8.8,
        8.7,
        8.6,
        8.5,
        8.4,
        8.3,
        8.2,
        8.1,
        8.0,
        7.9,
        7.8,
        7.9,
        7.8,
        7.9,
        7.8,
        7.9,
        7.8,
        7.9,
        7.8,
        7.9,
    ]
    closes = early_closes + recent_closes
    if with_limit_up:
        closes[10] = closes[9] * 1.10

    first_day = date(2026, 5, 1)
    return [
        {
            "date": (first_day + timedelta(days=index)).isoformat(),
            "open": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": 100_000.0,
            "amount": 1_000_000.0,
        }
        for index, close in enumerate(closes)
    ]


def _criteria_by_id(result: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        str(item["id"]): item
        for item in result["criteria"]  # type: ignore[index,union-attr]
    }


class OversoldFirstBoardSelectionTests(unittest.TestCase):
    def test_emits_versioned_complete_evidence_for_passing_candidate(self) -> None:
        bars = _sample_bars()

        result = evaluate_oversold_first_board(bars)

        self.assertTrue(result["passed"])
        self.assertTrue(result["data_complete"])
        self.assertEqual("oversold_first_board", result["strategy_id"])
        self.assertEqual("0.1.0", result["strategy_version"])
        self.assertEqual(RULE_ID, result["rule_id"])
        self.assertEqual("2026-06-29", result["as_of"])
        self.assertEqual([], result["failure_ids"])
        self.assertEqual(4, len(result["evidence"]))

        metrics = result["metrics"]
        self.assertAlmostEqual(1_000_000.0, metrics["avg_amount_15d"])
        self.assertAlmostEqual(1_000_000_000.0, metrics["avg_amount_15d_cny"])
        self.assertLess(metrics["return_7d"], 0.15)
        self.assertLessEqual(abs(metrics["close_to_ma20_ratio"]), 0.03)
        self.assertGreaterEqual(metrics["ma60_overhead_ratio"], 0.08)
        self.assertGreaterEqual(metrics["drawdown_from_high_20d"], 0.05)
        self.assertGreaterEqual(metrics["rsi14"], 35.0)
        self.assertLessEqual(metrics["rsi14"], 50.0)

        criteria = _criteria_by_id(result)
        self.assertEqual(
            {
                "data_complete",
                "avg_amount_15d",
                "return_7d",
                "close_near_ma20",
                "ma60_overhead",
                "drawdown_20d",
                "rsi14_zone",
                "oversold_combo",
                "history_limit_up_60d",
            },
            set(criteria),
        )
        for criterion in criteria.values():
            self.assertTrue(
                {
                    "hard",
                    "passed",
                    "actual_value",
                    "operator",
                    "threshold",
                    "reason",
                    "score_awarded",
                    "score_max",
                }.issubset(criterion)
            )

    def test_history_limit_up_adds_score_but_is_not_a_hard_gate(self) -> None:
        without_limit_up = evaluate_oversold_first_board(_sample_bars())
        with_limit_up = evaluate_oversold_first_board(_sample_bars(with_limit_up=True))

        self.assertTrue(without_limit_up["passed"])
        self.assertEqual(90.0, without_limit_up["score"])
        self.assertEqual(0, without_limit_up["metrics"]["limit_up_count_60d"])
        self.assertNotIn("history_limit_up_60d", without_limit_up["failure_ids"])

        self.assertTrue(with_limit_up["passed"])
        self.assertEqual(100.0, with_limit_up["score"])
        self.assertEqual(1, with_limit_up["metrics"]["limit_up_count_60d"])
        self.assertEqual(10.0, with_limit_up["metrics"]["history_limit_up_bonus"])
        limit_criterion = _criteria_by_id(with_limit_up)["history_limit_up_60d"]
        self.assertFalse(limit_criterion["hard"])
        self.assertTrue(limit_criterion["passed"])

    def test_a15_and_r7_are_strict_hard_thresholds(self) -> None:
        amount_boundary = _sample_bars()
        for bar in amount_boundary[-15:]:
            bar["amount"] = 3_000_000.0
        amount_result = evaluate_oversold_first_board(amount_boundary)

        self.assertFalse(amount_result["passed"])
        self.assertIn("avg_amount_15d", amount_result["failure_ids"])

        return_boundary = _sample_bars()
        return_boundary[-8]["close"] = return_boundary[-1]["close"] / 1.15
        return_result = evaluate_oversold_first_board(return_boundary)

        self.assertAlmostEqual(0.15, return_result["metrics"]["return_7d"])
        self.assertFalse(return_result["passed"])
        self.assertIn("return_7d", return_result["failure_ids"])

    def test_oversold_combo_requires_ma60_and_drawdown_with_ma20_or_rsi(self) -> None:
        result = evaluate_oversold_first_board(_sample_bars(flat_long_term=True))
        criteria = _criteria_by_id(result)

        self.assertFalse(result["passed"])
        self.assertFalse(criteria["ma60_overhead"]["passed"])
        self.assertFalse(criteria["oversold_combo"]["passed"])
        self.assertIn("oversold_combo", result["failure_ids"])

    def test_insufficient_or_invalid_data_is_auditable(self) -> None:
        result = evaluate_oversold_first_board(_sample_bars()[:59])

        self.assertFalse(result["passed"])
        self.assertFalse(result["data_complete"])
        self.assertEqual(["data_complete"], result["failure_ids"])
        self.assertEqual(9, len(result["criteria"]))
        self.assertIsNone(result["metrics"]["avg_amount_15d"])

        invalid_amount = _sample_bars()
        invalid_amount[-1]["amount"] = float("nan")
        invalid_result = evaluate_oversold_first_board(invalid_amount)
        self.assertFalse(invalid_result["data_complete"])

    def test_rejects_non_ascending_input_and_does_not_mutate_bars(self) -> None:
        bars = _sample_bars()
        original = copy.deepcopy(bars)

        strategy = OversoldFirstBoardSelectionStrategy()
        strategy.evaluate_selection(bars)
        self.assertEqual(original, bars)
        self.assertEqual("0.1.0", strategy.version)

        descending = list(reversed(bars))
        with self.assertRaisesRegex(ValueError, "严格升序"):
            evaluate_oversold_first_board(descending)


if __name__ == "__main__":
    unittest.main()
