from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from shilun.trading import (
    EntryScenario,
    ExecutionLevelConfig,
    KeyLevelError,
    MaxEntryGuard,
    ScenarioStopInput,
    StopPlanError,
    build_entry_price_ceiling,
    build_frozen_key_levels,
    build_scenario_stop_plan,
    compare_holding_stop,
    evaluate_e3_confirmation,
    remaining_reward_risk,
)


def _daily_rows() -> list[dict]:
    rows: list[dict] = []
    for index in range(1, 31):
        close = 90.0 + index * 0.3
        rows.append({
            "date": f"2026-06-{index:02d}",
            "open": close - 0.2,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
        })
    # 最近 20 日内制造一个由左右完成日线确认的结构低点。
    rows[24]["low"] = 94.0
    return rows


class FrozenKeyLevelTests(unittest.TestCase):
    def test_same_premarket_facts_generate_identical_frozen_plan(self) -> None:
        rows = _daily_rows()

        first = build_frozen_key_levels(rows, reference_price=99.0)
        second = build_frozen_key_levels(list(reversed(rows)), reference_price=99.0)

        self.assertEqual(first, second)
        self.assertEqual(first.rule_version, "execution-levels-v0.3.0")
        self.assertEqual(first.as_of, "2026-06-30")
        self.assertIsNone(first.high_60, "不足 60 根前序日线时不得伪装成 60 日前高")
        self.assertEqual(first.target_1, first.pressure_level)
        self.assertGreater(first.atr14, 0)
        self.assertAlmostEqual(
            first.support_zone_high - first.support_level,
            first.atr14 * 0.25,
            places=3,
        )
        self.assertAlmostEqual(
            first.breakout_confirmation - first.pressure_level,
            first.atr14 * 0.15,
            places=3,
        )

    def test_box_high_excludes_latest_completed_day(self) -> None:
        rows = _daily_rows()
        rows[-1]["high"] = 120.0

        levels = build_frozen_key_levels(rows, reference_price=99.0)

        self.assertLess(levels.box_high_20, 120.0)
        self.assertEqual(levels.prev_high, 120.0)

    def test_requires_enough_valid_completed_daily_bars(self) -> None:
        with self.assertRaisesRegex(KeyLevelError, "至少需要 21 根"):
            build_frozen_key_levels(_daily_rows()[:20])

    def test_duplicate_trade_date_is_rejected_instead_of_silently_overwritten(self) -> None:
        rows = _daily_rows()
        rows[-1]["date"] = rows[-2]["date"]
        with self.assertRaisesRegex(KeyLevelError, "日期重复"):
            build_frozen_key_levels(rows)


class ScenarioStopTests(unittest.TestCase):
    def test_e1_to_e7_each_use_their_own_structural_stop(self) -> None:
        cases = (
            (
                EntryScenario.E1_LOW_OPEN_RECOVERY,
                {"early_session_low": 9.5},
                9.5,
                9.2,
            ),
            (
                EntryScenario.E2_PULLBACK_RECLAIM,
                {"pullback_swing_low": 9.4},
                9.4,
                9.1,
            ),
            (
                EntryScenario.E3_RANGE_SUPPORT_REVERSAL,
                {"range_lower": 9.2, "structure_low": 9.0},
                9.2,
                8.9,
            ),
            (
                EntryScenario.E4_BREAKOUT_CONTINUATION,
                {"breakout_level": 10.0, "breakout_bar_low": 10.2},
                10.2,
                9.9,
            ),
            (
                EntryScenario.E5_WEAK_TO_STRONG,
                {"weak_to_strong_low": 9.6},
                9.6,
                9.3,
            ),
            (
                EntryScenario.E6_HIGH_OPEN_STRONG_BREAKOUT,
                {"high_open_consolidation_low": 10.1, "breakout_level": 10.0},
                10.1,
                9.8,
            ),
            (
                EntryScenario.E7_OVERSOLD_REBOUND_OVERNIGHT,
                {"stabilization_low": 9.6},
                9.6,
                9.4,
            ),
        )
        for scenario, facts, expected_anchor, expected_stop in cases:
            with self.subTest(scenario=scenario):
                plan = build_scenario_stop_plan(ScenarioStopInput(
                    scenario=scenario,
                    entry_price=10.5,
                    atr14=2.0,
                    **facts,
                ))
                self.assertEqual(plan.structural_anchor, expected_anchor)
                self.assertEqual(plan.hard_stop, expected_stop)

    def test_e2_does_not_fall_back_to_unrelated_ma8(self) -> None:
        with self.assertRaisesRegex(StopPlanError, "本次回踩波段低点"):
            build_scenario_stop_plan(ScenarioStopInput(
                scenario=EntryScenario.E2_PULLBACK_RECLAIM,
                entry_price=10.5,
                atr14=2.0,
            ))

    def test_e3_does_not_use_structure_above_actual_range_support(self) -> None:
        plan = build_scenario_stop_plan(ScenarioStopInput(
            scenario=EntryScenario.E3_RANGE_SUPPORT_REVERSAL,
            entry_price=10.5,
            atr14=2.0,
            range_lower=9.2,
            structure_low=9.4,
        ))
        self.assertEqual(plan.structural_anchor, 9.2)
        self.assertEqual(plan.structural_source, "区间下沿")
        structure_only = build_scenario_stop_plan(ScenarioStopInput(
            scenario=EntryScenario.E3_RANGE_SUPPORT_REVERSAL,
            entry_price=10.5,
            atr14=2.0,
            structure_low=9.1,
        ))
        self.assertEqual(structure_only.structural_anchor, 9.1)

    def test_confirmed_existing_holding_stop_is_not_auto_migrated(self) -> None:
        comparison = compare_holding_stop(
            existing_stop=9.0,
            existing_stop_confirmed=True,
            scenario_suggested_stop=9.4,
        )
        self.assertEqual(comparison.effective_stop, 9.0)
        self.assertEqual(comparison.scenario_suggested_stop, 9.4)
        self.assertTrue(comparison.requires_user_confirmation)


class EntryCeilingAndInvalidationTests(unittest.TestCase):
    def test_max_entry_price_is_minimum_of_zone_rr_and_slippage_ceilings(self) -> None:
        ceiling = build_entry_price_ceiling(
            target_1=120.0,
            hard_stop=90.0,
            trigger_price=100.0,
            entry_zone=(99.0, 110.0),
            atr_pct14=0.04,
        )

        self.assertEqual(ceiling.reward_risk_ceiling, 100.0)
        self.assertEqual(ceiling.allowed_slippage_pct, 0.008)
        self.assertEqual(ceiling.slippage_ceiling, 100.8)
        self.assertEqual(ceiling.max_entry_price, 100.0)
        self.assertTrue(ceiling.executable_zone)
        self.assertEqual(
            remaining_reward_risk(price=100.0, target_1=120.0, hard_stop=90.0),
            2.0,
        )

    def test_slippage_ratio_is_clamped_to_frozen_bounds(self) -> None:
        low_vol = build_entry_price_ceiling(
            target_1=120,
            hard_stop=90,
            trigger_price=100,
            entry_zone=(99, 110),
            atr_pct14=0.005,
        )
        high_vol = build_entry_price_ceiling(
            target_1=120,
            hard_stop=90,
            trigger_price=100,
            entry_zone=(99, 110),
            atr_pct14=0.08,
        )
        self.assertEqual(low_vol.allowed_slippage_pct, 0.003)
        self.assertEqual(high_vol.allowed_slippage_pct, 0.008)

    def test_same_plan_cannot_recover_after_crossing_max_entry_price(self) -> None:
        first_seen = datetime(2026, 7, 14, 14, 10, tzinfo=UTC)
        guard = MaxEntryGuard(
            plan_id="20260714:600584.SH:e4",
            plan_revision=1,
            max_entry_price=100.0,
        )

        breached = guard.observe(100.01, observed_at=first_seen)
        recovered_price = breached.observe(
            99.0,
            observed_at=first_seen + timedelta(minutes=5),
        )

        self.assertTrue(recovered_price.breached)
        self.assertEqual(recovered_price.breached_at, first_seen)
        new_revision = MaxEntryGuard(
            plan_id=guard.plan_id,
            plan_revision=2,
            max_entry_price=101.0,
        )
        self.assertFalse(new_revision.breached)

    def test_e3_requires_closed_bar_above_intraday_average(self) -> None:
        blocked = evaluate_e3_confirmation(
            closed_5m_close=10.2,
            support_level=10.0,
            avg_price=10.3,
            previous_5m_high=10.1,
            lower_shadow_ratio=0.40,
        )
        confirmed = evaluate_e3_confirmation(
            closed_5m_close=10.4,
            support_level=10.0,
            avg_price=10.3,
            previous_5m_high=10.3,
            lower_shadow_ratio=0.10,
        )
        self.assertFalse(blocked.confirmed)
        self.assertFalse(blocked.above_avg_price)
        self.assertTrue(confirmed.confirmed)

    def test_level_config_rejects_unversioned_or_invalid_buffers(self) -> None:
        with self.assertRaisesRegex(ValueError, "version"):
            ExecutionLevelConfig(version="")
        with self.assertRaisesRegex(ValueError, "滑点"):
            ExecutionLevelConfig(min_slippage_pct=0.01, max_slippage_pct=0.008)


if __name__ == "__main__":
    unittest.main()
