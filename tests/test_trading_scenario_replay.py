from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from shilun.trading.contracts import PositionState
from shilun.trading.playbooks.execution_scenarios import (
    IntradayMarketState,
    MarketPermission,
    ScenarioExecutionPlan,
    SectorExecutionState,
)
from shilun.trading.scenario_replay import (
    ScenarioReplayError,
    ScenarioReplayFrameContext,
    evaluate_full_scenario_launch_gate,
    replay_full_scenario_session,
)


TZ = ZoneInfo("Asia/Shanghai")


def _bars(values: list[tuple[float, float | None]], *, base: float, start: datetime) -> list[dict]:
    rows = []
    for index, (close, volume_ratio) in enumerate(values):
        moment = start + timedelta(minutes=5 * index)
        rows.append({
            "datetime": moment.isoformat(),
            "open": close - 0.05,
            "high": close + 0.05,
            "low": close - 0.10,
            "close": close,
            "volume": base + index,
            "amount": (base + index) * close,
            "volume_ratio": volume_ratio,
        })
    return rows


def _plan(*, positioned: bool = False) -> ScenarioExecutionPlan:
    return ScenarioExecutionPlan(
        ticker="000001.SZ",
        position_state=PositionState.POSITIONED if positioned else PositionState.FLAT,
        previous_close=10,
        open_price=10,
        atr=0.1,
        support=9.5,
        ma5=10,
        ma8=9.8,
        pressure=10.8,
        first_target=16,
        soft_risk=9.3,
        hard_stop=9,
        max_acceptable_price=11.5,
        entry_zone=(10.8, 11.1),
        current_quantity=100 if positioned else 0,
        sellable_quantity=100 if positioned else 0,
    )


def _contexts(start: datetime, count: int, *, fresh: bool = True) -> dict[datetime, ScenarioReplayFrameContext]:
    return {
        start + timedelta(minutes=5 * index): ScenarioReplayFrameContext(
            market_permission=MarketPermission.ATTACK,
            intraday_market_state=IntradayMarketState.REPAIR,
            sector_state=SectorExecutionState.RECOVERY,
            required_data_fresh=fresh,
            source_timestamps=(start + timedelta(minutes=5 * index),),
            fact_id=f"fact-{index}",
        )
        for index in range(count)
    }


class TradingScenarioReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 7, 10, 13, 55, tzinfo=TZ)
        self.stock = _bars([(10.7, 1.0), (11.0, 1.3)], base=100, start=self.start)
        self.sse = _bars([(4000, 1.0), (4001, 1.0)], base=1000, start=self.start)
        self.chinext = _bars([(3000, 1.0), (3001, 1.0)], base=1000, start=self.start)

    def replay(self, **overrides):
        kwargs = {
            "session_id": "20260710:000001.SZ",
            "plan": _plan(),
            "stock_bars": self.stock,
            "sse_bars": self.sse,
            "chinext_bars": self.chinext,
            "frame_contexts": _contexts(self.start, 2),
            "metadata": {"open_type": "flat", "data_state": "normal"},
        }
        kwargs.update(overrides)
        return replay_full_scenario_session(**kwargs)

    def test_uses_only_closed_facts_and_is_reproducible(self) -> None:
        first = self.replay()
        second = self.replay()

        self.assertEqual(first["bar_count"], 2)
        self.assertTrue(first["idempotent"])
        self.assertEqual(first["input_digest"], second["input_digest"])
        self.assertEqual(first["output_digest"], second["output_digest"])
        self.assertTrue(all(item["max_source_timestamp"] <= item["computed_at"] for item in first["frames"]))
        self.assertEqual(first["frames"][0]["decision"]["action"]["action_type"], "watch_trigger")
        self.assertEqual(first["frames"][1]["decision"]["action"]["action_type"], "prepare_entry")
        self.assertEqual(first["frames"][1]["decision"]["primary_scenario"]["scenario_id"], "E4")

    def test_rejects_context_source_from_the_future(self) -> None:
        contexts = _contexts(self.start, 2)
        contexts[self.start] = replace(
            contexts[self.start],
            source_timestamps=(self.start + timedelta(minutes=5),),
        )
        with self.assertRaisesRegex(ScenarioReplayError, "未来事实"):
            self.replay(frame_contexts=contexts)

    def test_missing_or_stale_context_never_prepares_entry(self) -> None:
        stale = self.replay(frame_contexts=_contexts(self.start, 2, fresh=False))
        missing = self.replay(frame_contexts={})

        self.assertNotIn("prepare_entry", [item["decision"]["action"]["action_type"] for item in stale["frames"]])
        self.assertNotIn("prepare_entry", [item["decision"]["action"]["action_type"] for item in missing["frames"]])
        self.assertEqual(missing["context_complete_count"], 0)

    def test_positioned_hard_stop_is_not_missed(self) -> None:
        at = datetime(2026, 7, 10, 10, 0, tzinfo=TZ)
        stock = _bars([(8.9, 1.0)], base=100, start=at)
        sse = _bars([(4000, 1.0)], base=1000, start=at)
        chinext = _bars([(3000, 1.0)], base=1000, start=at)
        result = self.replay(
            plan=_plan(positioned=True),
            stock_bars=stock,
            sse_bars=sse,
            chinext_bars=chinext,
            frame_contexts=_contexts(at, 1),
        )

        frame = result["frames"][0]
        self.assertEqual(frame["decision"]["action"]["action_type"], "risk_exit")
        self.assertEqual(frame["decision"]["primary_scenario"]["scenario_id"], "R4")

    def test_launch_gate_closes_when_sample_and_coverage_are_missing(self) -> None:
        report = evaluate_full_scenario_launch_gate([self.replay()])

        self.assertFalse(report["ready_for_launch"])
        self.assertEqual(report["decision"], "hold_launch")
        self.assertEqual(report["scenario_session_counts"]["E4"], 1)
        self.assertEqual(report["scenario_sample_gaps"]["E4"], 19)
        self.assertIn("每场景至少二十会话", report["blockers"])
        self.assertIn("极端与分化覆盖", report["blockers"])

    def test_launch_gate_does_not_count_audit_excluded_replays(self) -> None:
        replay = {**self.replay(), "audit_eligible": False}

        report = evaluate_full_scenario_launch_gate([replay])

        self.assertEqual(report["loaded_replay_count"], 1)
        self.assertEqual(report["replay_session_count"], 0)
        self.assertEqual(report["audit_excluded_replay_count"], 1)
        self.assertFalse(report["ready_for_launch"])


if __name__ == "__main__":
    unittest.main()
