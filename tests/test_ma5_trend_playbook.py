from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from shilun.trading import (
    ActionIntent,
    DataState,
    FactSourceSnapshot,
    Ma5IntradayFacts,
    Ma5TrendPlaybook,
    Ma5WatchPlan,
    ScenarioState,
)


NOW = datetime(2026, 7, 13, 10, 15, tzinfo=UTC)


def _plan() -> Ma5WatchPlan:
    return Ma5WatchPlan(
        ticker="603986.SH",
        previous_close=660.0,
        open_price=650.0,
        frozen_ma5=655.0,
        hard_stop=640.0,
        previous_high=670.0,
        current_quantity=100,
    )


def _facts(
    closes: tuple[float, ...],
    *,
    current: float | None = None,
    vwap: float | None = 652.0,
    volume_ratio: float | None = 1.0,
) -> Ma5IntradayFacts:
    return Ma5IntradayFacts(
        computed_at=NOW,
        closed_5m_prices=closes,
        current_price=current if current is not None else closes[-1],
        vwap=vwap,
        volume_ratio=volume_ratio,
        sse_state="震荡",
        chinext_state="修复",
        sector_state="修复",
    )


def _sources(*, full: bool = True) -> tuple[FactSourceSnapshot, ...]:
    names = ["stock_quote", "stock_5m"]
    if full:
        names.extend(["index_sse_quote", "index_chinext_quote", "sector_quote"])
    return tuple(
        FactSourceSnapshot(source=name, as_of=NOW - timedelta(seconds=10))
        for name in names
    )


class Ma5TrendPlaybookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.playbook = Ma5TrendPlaybook()

    def test_first_close_below_ma5_waits_for_second_bar(self) -> None:
        result = self.playbook.evaluate(_plan(), _facts((658.0, 654.0)), _sources())
        self.assertEqual(result.current_scenario.scenario_id, "ma5_break")
        self.assertEqual(result.current_scenario.state, ScenarioState.WAITING_CONFIRMATION)
        self.assertEqual(result.action.action_type, "wait_confirmation")

    def test_second_close_below_ma5_triggers_break_scenario(self) -> None:
        result = self.playbook.evaluate(_plan(), _facts((654.0, 653.0)), _sources())
        self.assertEqual(result.current_scenario.scenario_id, "ma5_break")
        self.assertEqual(result.current_scenario.state, ScenarioState.TRIGGERED)
        self.assertEqual(result.action.action_type, "prepare_reduce")

    def test_reclaim_invalidates_break_path_and_holds(self) -> None:
        result = self.playbook.evaluate(_plan(), _facts((653.0, 656.0)), _sources())
        self.assertEqual(result.current_scenario.scenario_id, "ma5_reclaim")
        self.assertEqual(result.action.action_type, "hold")

    def test_low_open_reclaim_builds_acceptance_scenario(self) -> None:
        result = self.playbook.evaluate(_plan(), _facts((656.0, 657.0), vwap=653.0), _sources())
        self.assertEqual(result.current_scenario.scenario_id, "low_open_acceptance")
        self.assertEqual(result.current_scenario.state, ScenarioState.TRIGGERED)

    def test_breakout_requires_price_and_volume(self) -> None:
        result = self.playbook.evaluate(
            _plan(),
            _facts((668.0, 671.0), vwap=660.0, volume_ratio=1.3),
            _sources(),
        )
        self.assertEqual(result.current_scenario.scenario_id, "breakout_hold")
        self.assertEqual(result.current_scenario.state, ScenarioState.TRIGGERED)

    def test_hard_stop_survives_missing_index_and_sector_data(self) -> None:
        result = self.playbook.evaluate(_plan(), _facts((645.0, 639.0)), _sources(full=False))
        self.assertEqual(result.current_scenario.scenario_id, "hard_stop")
        self.assertEqual(result.action.action_type, "risk_exit")
        self.assertEqual(result.action_intent, ActionIntent.DEFENSIVE)
        self.assertEqual(result.data_freshness.state, DataState.NORMAL)

    def test_active_action_is_blocked_without_index_and_sector_data(self) -> None:
        result = self.playbook.evaluate(_plan(), _facts((656.0, 657.0)), _sources(full=False))
        self.assertIsNone(result.action)
        self.assertIsNone(result.current_scenario)
        self.assertEqual(result.data_freshness.state, DataState.MISSING)

    def test_scenarios_expose_rule_trace_not_probability(self) -> None:
        scenarios = self.playbook.build_scenarios(_plan(), _facts((658.0, 654.0)))
        self.assertTrue(all(scenario.traces for scenario in scenarios))
        self.assertTrue(all(not hasattr(scenario, "probability") for scenario in scenarios))


if __name__ == "__main__":
    unittest.main()
