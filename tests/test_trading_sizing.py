from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from shilun.trading.contracts import ActionType, ExecutionState, PositionState
from shilun.trading.playbooks.execution_scenarios import (
    ClosedFiveMinuteBar,
    FullScenarioPlaybook,
    IntradayMarketState,
    MarketPermission,
    ScenarioExecutionPlan,
    ScenarioMarketFacts,
    SectorExecutionState,
)
from shilun.trading.risk import AccountRiskProfile, RiskConfigurationError
from shilun.trading.sizing import (
    CashReservation,
    EntrySizingInput,
    ReductionSizingInput,
    ScenarioSizingContext,
    TradingUnitRule,
    calculate_entry_sizing,
    calculate_reduction_sizing,
    list_active_cash_reservations,
    sync_cash_reservation,
)


TZ = ZoneInfo("Asia/Shanghai")


class _Collection:
    def __init__(self):
        self.docs = []

    @staticmethod
    def _matches(document, query):
        return all(document.get(key) == value for key, value in query.items())

    def find(self, query, projection=None):
        return [dict(item) for item in self.docs if self._matches(item, query)]

    def find_one(self, query, projection=None):
        return next((dict(item) for item in self.docs if self._matches(item, query)), None)

    def update_one(self, query, update, upsert=False):
        for item in self.docs:
            if self._matches(item, query):
                item.update(update.get("$set", {}))
                return
        if upsert:
            self.docs.append({**query, **update.get("$set", {})})


def profile(**changes) -> AccountRiskProfile:
    values = {
        "total_assets": 100_000,
        "available_cash": 50_000,
        "max_loss_per_trade": 2_000,
        "conservative_fee_buffer_rate": 0,
    }
    values.update(changes)
    return AccountRiskProfile(**values)


def entry_input(**changes) -> EntrySizingInput:
    values = {
        "plan_id": "plan-main",
        "ticker": "000001.SZ",
        "scenario_id": "E2",
        "market_permission": "attack",
        "entry_price": 10.0,
        "hard_stop": 8.0,
        "unit_rule": TradingUnitRule(),
    }
    values.update(changes)
    return EntrySizingInput(**values)


class EntrySizingTests(unittest.TestCase):
    def test_account_profile_carries_available_cash_and_optional_cash_cap(self) -> None:
        item = profile(max_cash_per_trade=30_000)
        self.assertTrue(item.cash_configured)
        self.assertEqual(item.to_document()["available_cash"], 50_000)
        with self.assertRaises(RiskConfigurationError):
            profile(available_cash=100_001)

    def test_entry_quantity_is_limited_by_risk_cash_and_buy_unit(self) -> None:
        result = calculate_entry_sizing(
            entry_input(),
            profile(max_cash_per_trade=30_000),
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.risk_allowed_quantity, 1_000)
        self.assertEqual(result.cash_allowed_quantity, 3_000)
        self.assertEqual(result.planned_quantity, 1_000)
        self.assertEqual(result.planned_cash, 10_000)
        self.assertEqual(result.reserved_cash, 10_000)
        self.assertEqual(result.risk_amount, 2_000)

    def test_optional_cash_cap_and_security_buy_unit_limit_the_plan(self) -> None:
        result = calculate_entry_sizing(
            entry_input(unit_rule=TradingUnitRule(buy_unit=200)),
            profile(max_loss_per_trade=100_000, max_cash_per_trade=2_500),
        )
        self.assertEqual(result.cash_allowed_quantity, 250)
        self.assertEqual(result.base_quantity, 200)
        self.assertEqual(result.planned_quantity, 200)
        self.assertEqual(result.reserved_cash, 2_000)

    def test_existing_reservations_are_deduplicated_and_own_plan_is_reusable(self) -> None:
        reservations = (
            CashReservation("other", "600000.SH", 20_000, 2_000, 10),
            CashReservation("other", "600000.SH", 20_000, 2_000, 10),
            CashReservation("plan-main", "000001.SZ", 5_000, 500, 10),
        )
        result = calculate_entry_sizing(
            entry_input(active_reservations=reservations),
            profile(available_cash=30_000, max_loss_per_trade=100_000),
        )
        self.assertEqual(result.cash_after_other_reservations, 10_000)
        self.assertEqual(result.cash_allowed_quantity, 1_000)
        self.assertEqual(result.planned_quantity, 1_000)

    def test_backup_opportunity_has_quantity_but_reserves_no_cash(self) -> None:
        result = calculate_entry_sizing(
            entry_input(is_main_opportunity=False),
            profile(),
        )
        self.assertEqual(result.status, "alternative_unreserved")
        self.assertGreater(result.planned_quantity, 0)
        self.assertEqual(result.reserved_cash, 0)
        self.assertFalse(result.executable)

    def test_insufficient_cash_does_not_prepare_less_than_one_buy_unit(self) -> None:
        result = calculate_entry_sizing(
            entry_input(),
            profile(available_cash=500),
        )
        self.assertEqual(result.status, "insufficient_buy_unit")
        self.assertEqual(result.planned_quantity, 0)
        self.assertFalse(result.executable)

    def test_e7_is_exactly_25_percent_before_final_unit_rounding(self) -> None:
        base = calculate_entry_sizing(
            entry_input(scenario_id="E2"),
            profile(available_cash=12_000, max_loss_per_trade=2_400),
        )
        e7 = calculate_entry_sizing(
            entry_input(scenario_id="E7", market_permission="defense"),
            profile(available_cash=12_000, max_loss_per_trade=2_400),
        )
        self.assertEqual(base.base_quantity, 1_200)
        self.assertEqual(e7.scenario_size_factor, 0.25)
        self.assertEqual(e7.planned_quantity, 300)

    def test_hold_e4_is_50_percent_not_double_discounted(self) -> None:
        result = calculate_entry_sizing(
            entry_input(scenario_id="E4", market_permission="hold"),
            profile(available_cash=20_000, max_loss_per_trade=4_000),
        )
        self.assertEqual(result.base_quantity, 2_000)
        self.assertEqual(result.market_permission_factor, 1.0)
        self.assertEqual(result.scenario_size_factor, 0.5)
        self.assertEqual(result.planned_quantity, 1_000)


class ReductionSizingTests(unittest.TestCase):
    def reduction(self, **changes):
        values = {
            "current_quantity": 1_000,
            "available_quantity": 1_000,
            "current_price": 10.0,
            "target_price": 14.0,
            "hard_stop": 8.0,
            "risk_severity": 0.35,
            "unit_rule": TradingUnitRule(),
        }
        values.update(changes)
        return calculate_reduction_sizing(ReductionSizingInput(**values))

    def test_remaining_reward_risk_adjusts_quantity_factor(self) -> None:
        high = self.reduction()
        medium = self.reduction(target_price=13.0, hard_stop=8.0, current_price=10.0, risk_severity=0.5)
        low = self.reduction(target_price=11.0, hard_stop=8.0, current_price=10.0)
        none = self.reduction(target_price=10.0)
        self.assertEqual(high.quantity_factor, 0.2625)
        self.assertEqual(high.recommended_sell_quantity, 200)
        self.assertEqual(medium.quantity_factor, 0.5)
        self.assertEqual(medium.recommended_sell_quantity, 500)
        self.assertEqual(low.quantity_factor, 0.7)
        self.assertEqual(low.recommended_sell_quantity, 700)
        self.assertEqual(none.quantity_factor, 1.0)
        self.assertEqual(none.recommended_sell_quantity, 1_000)

    def test_ordinary_reduce_is_at_least_one_unit_and_never_exceeds_sellable(self) -> None:
        minimum = self.reduction(available_quantity=500, risk_severity=0.25, target_price=20.0)
        odd_lot = self.reduction(available_quantity=150, target_price=20.0)
        self.assertEqual(minimum.recommended_sell_quantity, 100)
        self.assertEqual(odd_lot.recommended_sell_quantity, 100)
        self.assertEqual(odd_lot.target_quantity, 900)

    def test_below_one_sell_unit_and_missing_quantity_do_not_create_invalid_order(self) -> None:
        below = self.reduction(available_quantity=50)
        missing = self.reduction(available_quantity=None)
        self.assertEqual(below.status, "below_sell_unit")
        self.assertEqual(below.recommended_sell_quantity, 0)
        self.assertFalse(below.executable)
        self.assertEqual(missing.status, "quantity_not_configured")
        self.assertIsNone(missing.change_quantity)

    def test_forced_exit_sells_odd_lot_and_always_targets_zero(self) -> None:
        forced = self.reduction(available_quantity=50, forced_exit=True, risk_severity=1.0)
        missing = self.reduction(available_quantity=None, forced_exit=True, risk_severity=1.0)
        self.assertEqual(forced.status, "forced_exit")
        self.assertEqual(forced.recommended_sell_quantity, 50)
        self.assertEqual(forced.target_quantity, 0)
        self.assertEqual(missing.target_quantity, 0)
        self.assertIsNone(missing.change_quantity)


class ReservationAndScenarioIntegrationTests(unittest.TestCase):
    @staticmethod
    def _plan(*, positioned: bool = False, sellable: int | None = 0):
        return ScenarioExecutionPlan(
            ticker="000001.SZ",
            position_state=PositionState.POSITIONED if positioned else PositionState.FLAT,
            previous_close=10,
            open_price=10,
            atr=1,
            support=9,
            ma5=9.5,
            ma8=9.3,
            pressure=11,
            first_target=20,
            soft_risk=9,
            hard_stop=8,
            max_acceptable_price=12,
            entry_zone=(9, 11.5),
            current_quantity=500 if positioned else 0,
            sellable_quantity=sellable,
        )

    @staticmethod
    def _facts(*, hard_stop: bool = False, soft_break: bool = False):
        now = datetime(2026, 7, 14, 14, 30, tzinfo=TZ)
        if hard_stop:
            current, day_low, vwap = 7.9, 7.8, 8.5
            closed = (ClosedFiveMinuteBar(now.replace(minute=25), 8.1, 8.2, 7.8, 7.9),)
        elif soft_break:
            current, day_low, vwap = 8.6, 8.5, 9.0
            closed = (
                ClosedFiveMinuteBar(now.replace(minute=20), 8.9, 9.0, 8.5, 8.7),
                ClosedFiveMinuteBar(now.replace(minute=25), 8.7, 8.8, 8.5, 8.6),
            )
        else:
            current, day_low, vwap = 11.3, 10.0, 10.5
            closed = (ClosedFiveMinuteBar(now.replace(minute=25), 10.8, 11.5, 10.7, 11.3, 1.3),)
        return ScenarioMarketFacts(
            computed_at=now,
            current_price=current,
            day_low=day_low,
            vwap=vwap,
            closed_bars=closed,
            volume_ratio=1.3,
            relative_strength=0.2,
            market_permission=MarketPermission.ATTACK,
            intraday_market_state=IntradayMarketState.REPAIR,
            sector_state=SectorExecutionState.DIVERGENCE,
        )

    def test_full_scenario_entry_receives_cash_backed_quantity(self) -> None:
        decision = FullScenarioPlaybook().evaluate(
            self._plan(),
            self._facts(),
            ScenarioSizingContext(profile=profile(), plan_id="plan-e4"),
        )
        self.assertEqual(decision.action.canonical_type, ActionType.PREPARE_ENTRY)
        self.assertGreater(decision.action.target_quantity, 0)
        self.assertEqual(decision.action.target_quantity, decision.sizing.planned_quantity)
        self.assertGreater(decision.sizing.reserved_cash, 0)

    def test_unfunded_or_backup_entry_is_not_exposed_as_prepare_entry(self) -> None:
        unfunded = FullScenarioPlaybook().evaluate(
            self._plan(),
            self._facts(),
            ScenarioSizingContext(
                profile=profile(available_cash=None),
                plan_id="plan-unfunded",
            ),
        )
        backup = FullScenarioPlaybook().evaluate(
            self._plan(),
            self._facts(),
            ScenarioSizingContext(
                profile=profile(),
                plan_id="plan-backup",
                is_main_opportunity=False,
            ),
        )
        self.assertEqual(unfunded.action.canonical_type, ActionType.PAUSE_NEW_ACTION)
        self.assertEqual(unfunded.execution_state, ExecutionState.CONFIRMING)
        self.assertEqual(backup.action.canonical_type, ActionType.WATCH_TRIGGER)
        self.assertEqual(backup.sizing.reserved_cash, 0)

    def test_full_scenario_hard_exit_uses_all_sellable_even_below_one_lot(self) -> None:
        decision = FullScenarioPlaybook().evaluate(
            self._plan(positioned=True, sellable=50),
            self._facts(hard_stop=True),
            ScenarioSizingContext(profile=profile(), plan_id="plan-r4"),
        )
        self.assertEqual(decision.primary_scenario.scenario_id, "R4")
        self.assertEqual(decision.action.canonical_type, ActionType.RISK_EXIT)
        self.assertEqual(decision.action.target_quantity, 0)
        self.assertEqual(decision.action.change_quantity, -50)

    def test_full_scenario_ordinary_reduce_has_quantity_or_pauses_below_one_lot(self) -> None:
        ready = FullScenarioPlaybook().evaluate(
            self._plan(positioned=True, sellable=500),
            self._facts(soft_break=True),
            ScenarioSizingContext(profile=profile(), plan_id="plan-r1-ready"),
        )
        below_lot = FullScenarioPlaybook().evaluate(
            self._plan(positioned=True, sellable=50),
            self._facts(soft_break=True),
            ScenarioSizingContext(profile=profile(), plan_id="plan-r1-small"),
        )
        self.assertEqual(ready.primary_scenario.scenario_id, "R1")
        self.assertEqual(ready.action.canonical_type, ActionType.PREPARE_REDUCE)
        self.assertEqual(ready.action.target_quantity, 400)
        self.assertEqual(ready.action.change_quantity, -100)
        self.assertEqual(below_lot.primary_scenario.scenario_id, "R1")
        self.assertEqual(below_lot.action.canonical_type, ActionType.PAUSE_NEW_ACTION)
        self.assertEqual(below_lot.sizing.status, "below_sell_unit")

    def test_cash_reservation_sync_is_idempotent_and_releasable(self) -> None:
        collection = _Collection()
        ready = calculate_entry_sizing(entry_input(), profile())
        first = sync_cash_reservation(
            collection,
            plan_id="plan-main",
            ticker="000001.SZ",
            sizing=ready,
            updated_at=datetime(2026, 7, 14, 14, 30, tzinfo=TZ),
            expires_at="2026-07-14T15:00:00+08:00",
        )
        sync_cash_reservation(
            collection,
            plan_id="plan-main",
            ticker="000001.SZ",
            sizing=ready,
            updated_at=datetime(2026, 7, 14, 14, 31, tzinfo=TZ),
        )
        self.assertEqual(first["status"], "active")
        self.assertEqual(len(collection.docs), 1)
        self.assertEqual(len(list_active_cash_reservations(collection)), 1)

        backup = calculate_entry_sizing(entry_input(is_main_opportunity=False), profile())
        released = sync_cash_reservation(
            collection,
            plan_id="plan-main",
            ticker="000001.SZ",
            sizing=backup,
            updated_at=datetime(2026, 7, 14, 14, 32, tzinfo=TZ),
        )
        self.assertEqual(released["status"], "released")
        self.assertEqual(list_active_cash_reservations(collection), ())


if __name__ == "__main__":
    unittest.main()
