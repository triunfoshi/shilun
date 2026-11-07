from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from shilun.trading.config import ExecutionScenarioConfig
from shilun.trading.contracts import (
    ActionType,
    ContractError,
    ExecutionState,
    MatchLevel,
    PositionState,
    RuleState,
    RuleTrace,
    ScenarioCandidate,
    ScenarioState,
)
from shilun.trading.playbooks.execution_scenarios import (
    E7Phase,
    E7ThreeDayFacts,
    FullScenarioPlaybook,
    IntradayMarketState,
    MarketPermission,
    SCENARIO_CODES,
    SectorExecutionState,
    ScenarioExecutionPlan,
    ScenarioMarketFacts,
    ScenarioSignal,
    ClosedFiveMinuteBar,
    arbitrate_scenarios,
)


TZ = ZoneInfo("Asia/Shanghai")


def dt(hour: int, minute: int, second: int = 0, *, day: int = 14) -> datetime:
    return datetime(2026, 7, day, hour, minute, second, tzinfo=TZ)


def bar(
    hour: int,
    minute: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume_ratio: float | None = None,
    *,
    day: int = 14,
) -> ClosedFiveMinuteBar:
    return ClosedFiveMinuteBar(
        closed_at=dt(hour, minute, day=day),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume_ratio=volume_ratio,
    )


def flat_plan(**changes) -> ScenarioExecutionPlan:
    values = {
        "ticker": "000001.SZ",
        "position_state": PositionState.FLAT,
        "previous_close": 10.0,
        "open_price": 10.0,
        "atr": 1.0,
        "support": 9.0,
        "ma5": 9.5,
        "ma8": 9.3,
        "pressure": 11.0,
        "first_target": 20.0,
        "soft_risk": 9.0,
        "hard_stop": 8.0,
        "max_acceptable_price": 12.0,
        "entry_zone": (9.0, 11.5),
    }
    values.update(changes)
    return ScenarioExecutionPlan(**values)


def positioned_plan(**changes) -> ScenarioExecutionPlan:
    values = {
        "position_state": PositionState.POSITIONED,
        "current_quantity": 500,
        "sellable_quantity": 500,
    }
    values.update(changes)
    return flat_plan(**values)


def base_facts(**changes) -> ScenarioMarketFacts:
    values = {
        "computed_at": dt(14, 30),
        "current_price": 10.0,
        "day_low": 10.0,
        "vwap": 10.0,
        "closed_bars": (bar(14, 25, 10.0, 10.1, 10.0, 10.0, 0.8),),
        "volume_ratio": 0.8,
        "relative_strength": 0.1,
        "market_permission": MarketPermission.ATTACK,
        "intraday_market_state": IntradayMarketState.REPAIR,
        "sector_state": SectorExecutionState.DIVERGENCE,
    }
    values.update(changes)
    return ScenarioMarketFacts(**values)


def stable_e7(*, phase: E7Phase = E7Phase.STABILIZATION_ENTRY, exit_day: int = 15) -> E7ThreeDayFacts:
    return E7ThreeDayFacts(
        phase=phase,
        t_minus_2_date=date(2026, 7, 13),
        t_minus_1_date=date(2026, 7, 14),
        exit_date=date(2026, 7, exit_day),
        market_oversold_strength=1.2,
        sector_oversold_strength=0.9,
        stock_oversold_strength=1.4,
        t_minus_2_low=9.0,
        t_minus_1_low=8.9,
        sector_up_ratio_history=(0.15, 0.16, 0.18, 0.18, 0.19),
        sector_new_low_ratio=0.25,
        stock_bars_after_1400=(
            bar(14, 5, 9.0, 9.2, 8.9, 9.1),
            bar(14, 10, 9.1, 9.3, 8.95, 9.2),
            bar(14, 15, 9.2, 9.35, 9.0, 9.2),
        ),
        sse_three_bars_no_new_low=True,
    )


class ScenarioConfigAndContractTests(unittest.TestCase):
    def test_scenario_config_freezes_all_time_boundaries(self) -> None:
        config = ExecutionScenarioConfig()
        self.assertEqual(config.version, "execution-scenarios-v0.3.0")
        self.assertEqual(config.new_entry_start, time(14, 0))
        self.assertEqual(config.new_entry_cutoff, time(15, 0))
        self.assertEqual(config.target_trim_cutoff, time(14, 45))
        self.assertEqual(config.soft_risk_cutoff, time(14, 55))
        self.assertEqual(config.e7_exit_cutoff, time(10, 0))
        with self.assertRaises(ContractError):
            ExecutionScenarioConfig(target_trim_cutoff=time(14, 56))

    def test_e7_rejects_non_ordered_three_day_semantics(self) -> None:
        with self.assertRaises(ContractError):
            E7ThreeDayFacts(
                phase=E7Phase.STABILIZATION_ENTRY,
                t_minus_2_date=date(2026, 7, 14),
                t_minus_1_date=date(2026, 7, 14),
                exit_date=date(2026, 7, 15),
            )

    def test_candidate_catalog_contains_every_e_n_h_r_scenario(self) -> None:
        decision = FullScenarioPlaybook().evaluate(flat_plan(), base_facts())
        self.assertEqual(tuple(item.scenario_id for item in decision.candidate_scenarios), SCENARIO_CODES)
        self.assertTrue(all(item.state is ScenarioState.FAR for item in decision.candidate_scenarios))


class EntryScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.playbook = FullScenarioPlaybook()

    def entry_cases(self):
        e1_plan = flat_plan(open_price=9.8)
        e1_facts = base_facts(
            current_price=9.5,
            day_low=8.9,
            vwap=9.3,
            closed_bars=(bar(14, 25, 9.0, 9.6, 8.9, 9.5, 0.8),),
        )
        e2_facts = base_facts(
            current_price=9.8,
            day_low=9.4,
            vwap=9.7,
            closed_bars=(bar(14, 25, 9.5, 9.9, 9.4, 9.8, 0.7),),
        )
        e3_facts = base_facts(
            current_price=9.5,
            day_low=8.9,
            vwap=9.2,
            closed_bars=(
                bar(14, 20, 9.1, 9.3, 8.9, 9.15, 0.8),
                bar(14, 25, 9.15, 9.6, 9.0, 9.5, 0.9),
            ),
            market_permission=MarketPermission.HOLD,
            intraday_market_state=IntradayMarketState.RANGE,
        )
        breakout_bar = bar(14, 25, 10.8, 11.5, 10.7, 11.3, 1.3)
        e4_facts = base_facts(
            current_price=11.3,
            day_low=10.0,
            vwap=10.5,
            closed_bars=(breakout_bar,),
            volume_ratio=1.3,
        )
        e5_plan = flat_plan(open_price=9.8)
        e5_facts = base_facts(
            current_price=10.2,
            day_low=9.4,
            vwap=10.0,
            closed_bars=(bar(14, 25, 9.8, 10.3, 9.4, 10.2, 1.0),),
            relative_strength=0.2,
        )
        e6_plan = flat_plan(open_price=10.2)
        e6_facts = base_facts(
            current_price=11.3,
            day_low=10.1,
            vwap=10.5,
            closed_bars=(breakout_bar,),
            volume_ratio=1.3,
        )
        e7_facts = base_facts(
            computed_at=dt(14, 20),
            current_price=9.2,
            day_low=8.9,
            vwap=9.4,
            closed_bars=(bar(14, 15, 9.1, 9.35, 9.0, 9.2, 0.8),),
            market_permission=MarketPermission.DEFENSE,
            intraday_market_state=IntradayMarketState.RANGE,
            e7=stable_e7(),
        )
        return {
            "E1": (e1_plan, e1_facts),
            "E2": (flat_plan(), e2_facts),
            "E3": (flat_plan(), e3_facts),
            "E4": (flat_plan(), e4_facts),
            "E5": (e5_plan, e5_facts),
            "E6": (e6_plan, e6_facts),
            "E7": (flat_plan(), e7_facts),
        }

    def test_e1_through_e7_each_have_a_confirmed_hit(self) -> None:
        for code, (plan, facts) in self.entry_cases().items():
            with self.subTest(code=code):
                decision = self.playbook.evaluate(plan, facts)
                self.assertIs(decision.scenario(code).state, ScenarioState.TRIGGERED)

    def test_no_new_entry_before_1400_and_exact_boundary_opens(self) -> None:
        plan, facts = self.entry_cases()["E4"]
        before = self.playbook.evaluate(
            plan,
            replace(facts, computed_at=dt(13, 59), closed_bars=(replace(facts.closed_bars[0], closed_at=dt(13, 55)),)),
        )
        at_open = self.playbook.evaluate(
            plan,
            replace(facts, computed_at=dt(14, 0), closed_bars=(replace(facts.closed_bars[0], closed_at=dt(14, 0)),)),
        )
        self.assertNotEqual(before.action.canonical_type, ActionType.PREPARE_ENTRY)
        self.assertEqual(before.execution_state, ExecutionState.CONFIRMING)
        self.assertEqual(at_open.action.canonical_type, ActionType.PREPARE_ENTRY)
        self.assertEqual(at_open.execution_state, ExecutionState.ENTRY_READY)

    def test_1500_is_an_expiry_not_a_new_entry_boundary(self) -> None:
        plan, facts = self.entry_cases()["E4"]
        decision = self.playbook.evaluate(
            plan,
            replace(facts, computed_at=dt(15, 0), closed_bars=(replace(facts.closed_bars[0], closed_at=dt(15, 0)),)),
        )
        self.assertNotEqual(decision.action.canonical_type, ActionType.PREPARE_ENTRY)
        self.assertEqual(decision.execution_state, ExecutionState.EXPIRED)

    def test_hold_permission_still_allows_strict_e4(self) -> None:
        plan, facts = self.entry_cases()["E4"]
        decision = self.playbook.evaluate(
            plan,
            replace(
                facts,
                market_permission=MarketPermission.HOLD,
                relative_strength=0.2,
                sector_state=SectorExecutionState.DIVERGENCE,
            ),
        )
        self.assertIs(decision.scenario("E4").state, ScenarioState.TRIGGERED)
        self.assertEqual(decision.action.canonical_type, ActionType.PREPARE_ENTRY)

    def test_e7_rejects_half_mountain_and_requires_1415(self) -> None:
        plan, facts = self.entry_cases()["E7"]
        early = self.playbook.evaluate(
            plan,
            replace(
                facts,
                computed_at=dt(14, 14),
                closed_bars=(bar(14, 10, 9.1, 9.3, 8.95, 9.2, 0.8),),
                e7=replace(facts.e7, stock_bars_after_1400=facts.e7.stock_bars_after_1400[:2]),
            ),
        )
        half_mountain = self.playbook.evaluate(
            plan,
            replace(facts, e7=replace(facts.e7, t_minus_1_low=10.0)),
        )
        self.assertIsNot(early.scenario("E7").state, ScenarioState.TRIGGERED)
        self.assertIsNot(half_mountain.scenario("E7").state, ScenarioState.TRIGGERED)


class NegativeHoldingAndRiskScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.playbook = FullScenarioPlaybook()

    def test_n1_through_n4_each_have_a_hit(self) -> None:
        n1_plan = flat_plan(open_price=10.2)
        n1_facts = base_facts(
            current_price=10.6,
            day_low=10.1,
            vwap=10.8,
            closed_bars=(bar(14, 25, 11.0, 12.0, 10.5, 10.6, 1.4),),
        )
        n2_plan = flat_plan(open_price=9.8)
        n2_facts = base_facts(
            current_price=8.7,
            day_low=8.6,
            vwap=9.2,
            closed_bars=(
                bar(14, 20, 9.0, 9.1, 8.7, 8.8, 0.8),
                bar(14, 25, 8.8, 8.9, 8.6, 8.7, 0.8),
            ),
        )
        n3_facts = base_facts(current_price=12.1, day_low=10.0, vwap=11.0)
        n4_facts = base_facts(market_permission=MarketPermission.DEFENSE)
        for code, plan, facts in (
            ("N1", n1_plan, n1_facts),
            ("N2", n2_plan, n2_facts),
            ("N3", flat_plan(), n3_facts),
            ("N4", flat_plan(), n4_facts),
        ):
            with self.subTest(code=code):
                decision = self.playbook.evaluate(plan, facts)
                self.assertIs(decision.scenario(code).state, ScenarioState.TRIGGERED)

    def test_h1_through_h3_each_have_a_hit(self) -> None:
        h1 = self.playbook.evaluate(
            positioned_plan(),
            base_facts(current_price=12.0, day_low=10.0, vwap=11.0, closed_bars=(bar(14, 25, 11.8, 12.1, 11.7, 12.0),)),
        )
        h2 = self.playbook.evaluate(
            positioned_plan(),
            base_facts(current_price=12.0, day_low=10.0, vwap=11.0, closed_bars=(bar(14, 25, 11.8, 12.1, 11.7, 12.0),), intraday_market_state=IntradayMarketState.RANGE),
        )
        h3 = self.playbook.evaluate(
            positioned_plan(),
            base_facts(computed_at=dt(10, 0), current_price=19.9, day_low=10.0, vwap=19.5, closed_bars=(bar(9, 55, 19.5, 21.0, 19.0, 19.9),)),
        )
        self.assertIs(h1.scenario("H1").state, ScenarioState.TRIGGERED)
        self.assertIs(h2.scenario("H2").state, ScenarioState.TRIGGERED)
        self.assertIs(h3.scenario("H3").state, ScenarioState.TRIGGERED)

    def test_r1_through_r4_each_have_a_hit(self) -> None:
        r1 = self.playbook.evaluate(
            positioned_plan(),
            base_facts(current_price=8.6, day_low=8.5, vwap=9.0, closed_bars=(bar(14, 20, 8.9, 9.0, 8.6, 8.7), bar(14, 25, 8.7, 8.8, 8.5, 8.6))),
        )
        r2 = self.playbook.evaluate(
            positioned_plan(open_price=10.2),
            base_facts(computed_at=dt(10, 0), current_price=10.6, day_low=10.1, vwap=10.8, closed_bars=(bar(9, 55, 11.0, 12.0, 10.5, 10.6, 1.4),)),
        )
        r3 = self.playbook.evaluate(
            positioned_plan(),
            base_facts(computed_at=dt(10, 0), current_price=20.0, day_low=10.0, vwap=19.8, closed_bars=(bar(9, 55, 19.8, 20.2, 19.7, 20.0, 0.8),)),
        )
        r4 = self.playbook.evaluate(
            positioned_plan(),
            base_facts(computed_at=dt(13, 30), current_price=7.9, day_low=7.8, vwap=8.5, closed_bars=(bar(13, 25, 8.1, 8.2, 7.8, 7.9),)),
        )
        for code, decision in (("R1", r1), ("R2", r2), ("R3", r3), ("R4", r4)):
            with self.subTest(code=code):
                self.assertIs(decision.scenario(code).state, ScenarioState.TRIGGERED)

    def test_r4_overrides_every_other_rule_and_stale_quote_requires_manual_check(self) -> None:
        plan = positioned_plan(open_price=10.2)
        facts = base_facts(
            computed_at=dt(14, 50),
            current_price=7.9,
            day_low=7.8,
            vwap=10.0,
            closed_bars=(bar(14, 45, 10.5, 12.0, 7.8, 7.9, 1.5),),
        )
        fresh = self.playbook.evaluate(plan, facts)
        stale = self.playbook.evaluate(plan, replace(facts, quote_age_seconds=46))
        self.assertEqual(fresh.primary_scenario.scenario_id, "R4")
        self.assertEqual(fresh.action.canonical_type, ActionType.RISK_EXIT)
        self.assertEqual(fresh.action.target_quantity, 0)
        self.assertEqual(stale.primary_scenario.scenario_id, "R4")
        self.assertEqual(stale.action.canonical_type, ActionType.MANUAL_VERIFY_RISK)


class TimingE7AndArbitrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.playbook = FullScenarioPlaybook()

    def soft_break_facts(self, at: datetime) -> ScenarioMarketFacts:
        return base_facts(
            computed_at=at,
            current_price=8.6,
            day_low=8.5,
            vwap=9.0,
            closed_bars=(
                replace(bar(14, 45, 8.9, 9.0, 8.6, 8.7), closed_at=at - timedelta(minutes=10)),
                replace(bar(14, 50, 8.7, 8.8, 8.5, 8.6), closed_at=at - timedelta(minutes=5)),
            ),
        )

    def test_target_and_soft_risk_have_distinct_inclusive_cutoffs(self) -> None:
        target_plan = positioned_plan()
        target_bar = bar(14, 40, 19.5, 21.0, 19.0, 19.9)
        at_target_cutoff = self.playbook.evaluate(
            target_plan,
            base_facts(computed_at=dt(14, 45), current_price=19.9, day_low=10.0, vwap=19.5, closed_bars=(target_bar,)),
        )
        after_target_cutoff = self.playbook.evaluate(
            target_plan,
            base_facts(computed_at=dt(14, 45, 1), current_price=19.9, day_low=10.0, vwap=19.5, closed_bars=(target_bar,)),
        )
        at_soft_cutoff = self.playbook.evaluate(positioned_plan(), self.soft_break_facts(dt(14, 55)))
        after_soft_cutoff = self.playbook.evaluate(positioned_plan(), self.soft_break_facts(dt(14, 55, 1)))
        self.assertEqual(at_target_cutoff.action.canonical_type, ActionType.PREPARE_REDUCE)
        self.assertNotEqual(after_target_cutoff.action.canonical_type, ActionType.PREPARE_REDUCE)
        self.assertEqual(at_soft_cutoff.action.canonical_type, ActionType.PREPARE_REDUCE)
        self.assertNotEqual(after_soft_cutoff.action.canonical_type, ActionType.PREPARE_REDUCE)

    def test_sell_actions_mark_morning_as_preferred_without_making_it_exclusive(self) -> None:
        plan = positioned_plan()
        morning = self.playbook.evaluate(plan, self.soft_break_facts(dt(10, 0)))
        afternoon = self.playbook.evaluate(plan, self.soft_break_facts(dt(13, 30)))
        self.assertEqual(morning.action.canonical_type, ActionType.PREPARE_REDUCE)
        self.assertEqual(afternoon.action.canonical_type, ActionType.PREPARE_REDUCE)
        self.assertTrue(morning.preferred_execution_window)
        self.assertFalse(afternoon.preferred_execution_window)

    def test_e7_t_day_high_open_batches_and_1000_forces_zero_target(self) -> None:
        plan = positioned_plan(open_price=10.2, current_quantity=400, sellable_quantity=400)
        e7 = stable_e7(phase=E7Phase.NEXT_DAY_EXIT)
        common = {
            "current_price": 10.4,
            "day_low": 10.1,
            "vwap": 10.3,
            "closed_bars": (bar(9, 30, 10.2, 10.5, 10.1, 10.4, day=15),),
            "e7": e7,
        }
        first_batch = self.playbook.evaluate(plan, base_facts(computed_at=dt(9, 32, day=15), **common))
        deadline = self.playbook.evaluate(plan, base_facts(computed_at=dt(10, 0, day=15), **common))
        one_lot = self.playbook.evaluate(
            positioned_plan(open_price=10.2, current_quantity=100, sellable_quantity=100),
            base_facts(computed_at=dt(9, 32, day=15), **common),
        )
        self.assertEqual(first_batch.primary_scenario.scenario_id, "E7")
        self.assertLessEqual(first_batch.action.target_quantity, 200)
        self.assertEqual(deadline.action.target_quantity, 0)
        self.assertEqual(one_lot.action.target_quantity, 0)

    def test_explicit_cancellation_is_auditable_for_every_cancellable_scene(self) -> None:
        for code in SCENARIO_CODES:
            if code == "R4":
                continue
            with self.subTest(code=code):
                decision = self.playbook.evaluate(
                    flat_plan(),
                    base_facts(cancelled_scenarios=frozenset({code})),
                )
                self.assertIs(decision.scenario(code).state, ScenarioState.INVALIDATED)

    def test_r4_cannot_be_cancelled_by_persisted_plan_state(self) -> None:
        decision = self.playbook.evaluate(
            positioned_plan(),
            base_facts(
                computed_at=dt(13, 30),
                current_price=7.9,
                day_low=7.8,
                vwap=8.5,
                closed_bars=(bar(13, 25, 8.1, 8.2, 7.8, 7.9),),
                cancelled_scenarios=frozenset({"R4"}),
            ),
        )
        self.assertIs(decision.scenario("R4").state, ScenarioState.TRIGGERED)
        self.assertEqual(decision.action.canonical_type, ActionType.RISK_EXIT)

    def test_every_scenario_participates_in_deterministic_conflict_arbitration(self) -> None:
        now = dt(14, 30)

        def make_signal(code: str, precedence: int) -> ScenarioSignal:
            candidate = ScenarioCandidate(
                scenario_id=code,
                title=code,
                priority=1,
                state=ScenarioState.TRIGGERED,
                match_level=MatchLevel.HIGH,
                traces=(RuleTrace(code, code, RuleState.SATISFIED, "confirmed"),),
            )
            return ScenarioSignal(
                candidate=candidate,
                action=replace(
                    self.playbook.evaluate(flat_plan(), base_facts()).action,
                    action_type=ActionType.WATCH_TRIGGER.value,
                    label="监控触发",
                ),
                execution_state=ExecutionState.ARMED,
                precedence=precedence,
                is_risk=code.startswith(("N", "R")),
                confirmed_at=now,
                reason="conflict test",
            )

        for code in SCENARIO_CODES:
            with self.subTest(code=code):
                if code == "R4":
                    primary, suppressed = arbitrate_scenarios((make_signal("R4", 100), make_signal("E7", 90)))
                    expected_suppressed = "E7"
                else:
                    primary, suppressed = arbitrate_scenarios((make_signal(code, 50), make_signal("R4", 100)))
                    expected_suppressed = code
                self.assertEqual(primary.candidate.scenario_id, "R4")
                self.assertIn(expected_suppressed, {item.scenario_id for item in suppressed})

    def test_real_conflict_saves_suppression_reason(self) -> None:
        plan = flat_plan(open_price=9.8)
        facts = base_facts(
            current_price=9.5,
            day_low=8.9,
            vwap=9.3,
            closed_bars=(bar(14, 25, 9.0, 9.6, 8.9, 9.5),),
        )
        decision = self.playbook.evaluate(plan, facts)
        self.assertGreaterEqual(len(decision.suppressed_scenarios), 1)
        self.assertTrue(all(item.reason for item in decision.suppressed_scenarios))

    def test_n3_irreversibly_covers_other_new_entry_scenarios(self) -> None:
        plan = flat_plan(missed_locked=True)
        facts = base_facts(
            current_price=9.8,
            day_low=9.4,
            vwap=9.7,
            closed_bars=(bar(14, 25, 9.5, 9.9, 9.4, 9.8, 0.7),),
        )
        decision = self.playbook.evaluate(plan, facts)
        self.assertEqual(decision.primary_scenario.scenario_id, "N3")
        self.assertEqual(decision.action.canonical_type, ActionType.NO_CHASE)
        self.assertIn("E2", {item.scenario_id for item in decision.suppressed_scenarios})

    def test_same_layer_arbitration_prefers_risk_then_completed_rules_then_freshness(self) -> None:
        now = dt(14, 30)

        def make(code: str, *, risk: bool, rules: int, confirmed_at: datetime) -> ScenarioSignal:
            traces = tuple(
                RuleTrace(f"{code}-{index}", code, RuleState.SATISFIED, "confirmed")
                for index in range(rules)
            )
            return ScenarioSignal(
                candidate=ScenarioCandidate(code, code, 1, ScenarioState.TRIGGERED, MatchLevel.HIGH, traces),
                action=self.playbook.evaluate(flat_plan(), base_facts()).action,
                execution_state=ExecutionState.ARMED,
                precedence=50,
                is_risk=risk,
                confirmed_at=confirmed_at,
                reason="same layer",
            )

        risk_primary, _ = arbitrate_scenarios((
            make("E1", risk=False, rules=4, confirmed_at=now),
            make("N1", risk=True, rules=1, confirmed_at=now - timedelta(minutes=5)),
        ))
        rules_primary, _ = arbitrate_scenarios((
            make("E1", risk=False, rules=2, confirmed_at=now - timedelta(minutes=5)),
            make("E2", risk=False, rules=3, confirmed_at=now - timedelta(minutes=10)),
        ))
        fresh_primary, _ = arbitrate_scenarios((
            make("E1", risk=False, rules=2, confirmed_at=now - timedelta(minutes=5)),
            make("E2", risk=False, rules=2, confirmed_at=now),
        ))
        self.assertEqual(risk_primary.candidate.scenario_id, "N1")
        self.assertEqual(rules_primary.candidate.scenario_id, "E2")
        self.assertEqual(fresh_primary.candidate.scenario_id, "E2")


if __name__ == "__main__":
    unittest.main()
