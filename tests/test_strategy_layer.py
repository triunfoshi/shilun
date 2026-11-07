from __future__ import annotations

import unittest
from datetime import date, timedelta

from shilun.strategies import (
    ExecutionStrategyContext,
    get_execution_strategy,
    list_execution_strategies,
)


def _bars(count: int = 70) -> list[dict]:
    start = date(2026, 4, 1)
    rows = []
    for index in range(count):
        close = 10.0 + index * 0.05
        rows.append({
            "date": (start + timedelta(days=index)).isoformat(),
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1000 + index * 5,
            "amount": 10000 + index * 100,
        })
    return rows


class StrategyLayerTest(unittest.TestCase):
    def test_registry_has_stable_public_ids(self) -> None:
        ids = {item["strategy_id"] for item in list_execution_strategies()}
        self.assertEqual(ids, {"manual", "ma5_v0_2", "e7_oversold_overnight"})

    def test_e7_binding_uses_time_exit_instead_of_ma5_target(self) -> None:
        plan = get_execution_strategy("e7_oversold_overnight").bind_execution_plan(
            {"entry_price": 20.0, "target_price": 26.0, "stop_loss_1": 19.2},
            actual_entry_price=20.2,
        )
        self.assertNotIn("target_price", plan)
        self.assertEqual(plan["target_type"], "time_exit")
        self.assertEqual(plan["exit_deadline"], "次一交易日 10:00")

    def test_ma5_strategy_returns_versioned_plan(self) -> None:
        result = get_execution_strategy("ma5_v0_2").evaluate_stock(
            _bars(),
            context=ExecutionStrategyContext(analysis_date="2026-06-09"),
        )
        self.assertEqual(result["strategy_id"], "ma5_v0_2")
        self.assertEqual(result["strategy_version"], "0.2.0")
        self.assertIn("trade_plan", result)
        self.assertIn("evidence", result)

    def test_binding_uses_actual_entry_and_preserves_planned_entry(self) -> None:
        plan = get_execution_strategy("ma5_v0_2").bind_execution_plan(
            {"entry_price": 10.0, "target_price": 12.0, "stop_loss_1": 9.0},
            actual_entry_price=10.5,
        )
        self.assertEqual(plan["planned_entry_price"], 10.0)
        self.assertEqual(plan["entry_price"], 10.5)
        self.assertEqual(plan["reward_risk_ratio"], 1.0)

    def test_manual_strategy_never_invents_buy_or_sell_points(self) -> None:
        result = get_execution_strategy("manual").evaluate_stock([], context=ExecutionStrategyContext())
        self.assertEqual(result["recommendation"], "manual_review")
        self.assertIsNone(result["trade_plan"])


if __name__ == "__main__":
    unittest.main()
