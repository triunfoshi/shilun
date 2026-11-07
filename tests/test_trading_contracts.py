from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from shilun.trading import (
    ActionIntent,
    ActionPresentation,
    ActionType,
    ContractError,
    DataState,
    DecisionResult,
    ExecutionPlan,
    ExecutionState,
    FactSourceSnapshot,
    FreshnessPolicy,
    MatchLevel,
    PositionState,
    RuleState,
    RuleTrace,
    ScenarioCandidate,
    ScenarioState,
    TradingAction,
    TradingFacts,
    TradingRuleConfig,
    build_action_presentation,
    canonical_action_type,
    evaluate_data_freshness,
    evaluate_action_freshness,
    execution_state_view,
    validate_action_for_position,
    validate_execution_transition,
)


NOW = datetime(2026, 7, 13, 10, 15, tzinfo=UTC)


def _sources(*, age_seconds: int = 10) -> tuple[FactSourceSnapshot, ...]:
    return tuple(
        FactSourceSnapshot(source=name, as_of=NOW - timedelta(seconds=age_seconds))
        for name in (
            "stock_quote",
            "stock_5m",
            "index_sse_quote",
            "index_chinext_quote",
            "sector_quote",
        )
    )


class TradingContractTests(unittest.TestCase):
    def test_default_rules_freeze_p0_confirmation_policy(self) -> None:
        rules = TradingRuleConfig()
        self.assertEqual(rules.version, "trading-p0-v0.2.0")
        self.assertEqual(rules.timeframe, "5m")
        self.assertEqual(rules.soft_break_confirmation_bars, 2)
        self.assertEqual(rules.hard_stop_confirmation_bars, 1)

    def test_rule_config_rejects_invalid_threshold_direction(self) -> None:
        with self.assertRaises(ContractError):
            TradingRuleConfig(gap_down_pct=0.01)

    def test_trading_facts_require_timezone_aware_computed_at(self) -> None:
        with self.assertRaisesRegex(ContractError, "时区"):
            TradingFacts(ticker="603986.SH", computed_at=datetime(2026, 7, 13), sources=())

    def test_trading_facts_reject_duplicate_sources(self) -> None:
        duplicate = FactSourceSnapshot(source="stock_quote", as_of=NOW)
        with self.assertRaisesRegex(ContractError, "重复"):
            TradingFacts(
                ticker="603986.SH",
                computed_at=NOW,
                sources=(duplicate, duplicate),
            )

    def test_scenario_reports_evidence_completeness_without_probability(self) -> None:
        scenario = ScenarioCandidate(
            scenario_id="ma5_low_open_reclaim",
            title="低开缩量后收回",
            priority=30,
            state=ScenarioState.WAITING_CONFIRMATION,
            match_level=MatchLevel.MEDIUM,
            traces=(
                RuleTrace(
                    rule_id="low_open",
                    label="低开",
                    state=RuleState.SATISFIED,
                    reason="开盘价低于低开阈值",
                ),
                RuleTrace(
                    rule_id="reclaim",
                    label="收回关键位",
                    state=RuleState.PENDING,
                    reason="等待当前 5 分钟 K 收盘",
                ),
            ),
        )
        self.assertEqual(scenario.evidence_completeness, "1/2")
        self.assertFalse(hasattr(scenario, "probability"))

    def test_invalid_execution_zone_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            TradingAction(
                action_type="adjust_position",
                label="减仓",
                execution_zone=(660.0, 655.0),
            )

    def test_execution_state_has_stable_single_line_chinese_view(self) -> None:
        expected = {
            ExecutionState.SELECTED: ("已入选", "观察中 · 暂不操作"),
            ExecutionState.CONFIRMING: ("等待确认", "暂不买入"),
            ExecutionState.ENTRY_READY: ("可建仓", "按计划价格与数量执行"),
            ExecutionState.EXIT_PENDING: ("清仓待成交", "立即清仓"),
            ExecutionState.MISSED: ("已错过", "不追"),
            ExecutionState.DATA_BLOCKED: ("数据阻断", "暂停新动作"),
        }
        for state, text in expected.items():
            view = execution_state_view(state)
            self.assertEqual((view.label, view.instruction), text)
            self.assertNotIn("\n", f"{view.label}{view.instruction}")

    def test_legacy_action_aliases_share_canonical_action(self) -> None:
        self.assertEqual(canonical_action_type("risk_reduce"), ActionType.PREPARE_REDUCE)
        self.assertEqual(canonical_action_type("hold_observe"), ActionType.HOLD)
        self.assertEqual(canonical_action_type("wait"), ActionType.WATCH_TRIGGER)

    def test_execution_state_rejects_terminal_plan_recovery(self) -> None:
        validate_execution_transition(ExecutionState.CONFIRMING, ExecutionState.MISSED)
        with self.assertRaisesRegex(ContractError, "非法执行状态迁移"):
            validate_execution_transition(ExecutionState.MISSED, ExecutionState.ARMED)

    def test_position_state_limits_action_vocabulary(self) -> None:
        with self.assertRaisesRegex(ContractError, "不允许动作 hold"):
            validate_action_for_position(
                PositionState.FLAT,
                TradingAction(action_type="hold", label="继续持有"),
            )
        with self.assertRaisesRegex(ContractError, "不得输出数量"):
            validate_action_for_position(
                PositionState.UNKNOWN,
                TradingAction(
                    action_type="confirm_position_facts",
                    label="核验持仓事实",
                    target_quantity=500,
                ),
            )
        with self.assertRaisesRegex(ContractError, "不允许动作 prepare_entry"):
            validate_action_for_position(
                PositionState.POSITIONED,
                TradingAction(action_type="prepare_entry", label="可建仓"),
            )

    def test_entry_presentation_is_actionable_and_single_line(self) -> None:
        presentation = build_action_presentation(
            execution_state=ExecutionState.ENTRY_READY,
            action=TradingAction(
                action_type="prepare_entry",
                label="准备建仓",
                target_quantity=500,
                change_quantity=500,
                execution_zone=(31.2, 31.5),
            ),
            reason="闭合五分钟K已站上关键位",
            next_condition="不高于31.50元",
            data_as_of=NOW,
            evidence_progress="4/4",
        )
        self.assertEqual(presentation.headline, "可建仓 500 股")
        self.assertTrue(presentation.executable)
        self.assertNotIn("\n", presentation.headline)

    def test_action_presentation_rejects_wrapped_primary_text(self) -> None:
        with self.assertRaisesRegex(ContractError, "必须单行"):
            ActionPresentation(
                state_label="等待确认",
                headline="等待\n确认",
                instruction="暂不买入",
                reason="等待闭合K线",
                next_condition="站上关键位",
                urgency="medium",
                executable=False,
            )

    def test_execution_plan_freezes_versions_and_revisions(self) -> None:
        plan = ExecutionPlan(
            plan_id="20260713:603986.SH:e2",
            ticker="603986.SH",
            trade_date=date(2026, 7, 13),
            strategy_id="ma5_v0_2",
            strategy_version="0.2",
            execution_playbook_id="pullback_reclaim",
            execution_rule_version="execution-v0.3",
            position_state=PositionState.FLAT,
            execution_state=ExecutionState.ARMED,
            plan_revision=1,
            position_revision=0,
            created_at=NOW,
            expires_at=NOW + timedelta(hours=5),
        )
        self.assertEqual(plan.plan_revision, 1)
        with self.assertRaisesRegex(ContractError, "版本号"):
            ExecutionPlan(**{**plan.__dict__, "plan_revision": 0})


class FreshnessGateTests(unittest.TestCase):
    def test_fresh_required_sources_allow_new_action(self) -> None:
        report = evaluate_data_freshness(
            _sources(),
            now=NOW,
            policy=FreshnessPolicy(),
        )
        self.assertEqual(report.state, DataState.NORMAL)
        self.assertTrue(report.can_generate_action)
        self.assertEqual(report.reasons, ())

    def test_missing_required_source_blocks_new_action(self) -> None:
        report = evaluate_data_freshness(
            tuple(item for item in _sources() if item.source != "index_chinext_quote"),
            now=NOW,
            policy=FreshnessPolicy(),
        )
        self.assertEqual(report.state, DataState.MISSING)
        self.assertFalse(report.can_generate_action)
        self.assertTrue(any("index_chinext_quote" in reason for reason in report.reasons))

    def test_missing_optional_sector_does_not_block_active_action(self) -> None:
        report = evaluate_data_freshness(
            tuple(item for item in _sources() if item.source != "sector_quote"),
            now=NOW,
            policy=FreshnessPolicy(),
        )
        self.assertEqual(report.state, DataState.NORMAL)
        self.assertTrue(report.can_generate_action)
        self.assertTrue(any("sector_quote" in reason for reason in report.reasons))

    def test_delayed_quote_blocks_new_action(self) -> None:
        report = evaluate_data_freshness(
            _sources(age_seconds=46),
            now=NOW,
            policy=FreshnessPolicy(),
        )
        self.assertEqual(report.state, DataState.DELAYED)
        self.assertFalse(report.can_generate_action)
        self.assertTrue(any("stock_quote" in reason for reason in report.reasons))

    def test_timestamp_regression_blocks_new_action(self) -> None:
        sources = list(_sources())
        sources[0] = FactSourceSnapshot(
            source="stock_quote",
            as_of=NOW - timedelta(seconds=10),
            previous_as_of=NOW - timedelta(seconds=5),
        )
        report = evaluate_data_freshness(
            tuple(sources),
            now=NOW,
            policy=FreshnessPolicy(),
        )
        self.assertEqual(report.state, DataState.MISSING)
        self.assertIn("时间戳倒退", report.reasons[0])

    def test_future_timestamp_beyond_clock_skew_blocks_new_action(self) -> None:
        sources = list(_sources())
        sources[0] = FactSourceSnapshot(
            source="stock_quote",
            as_of=NOW + timedelta(seconds=6),
        )
        report = evaluate_data_freshness(
            tuple(sources),
            now=NOW,
            policy=FreshnessPolicy(),
        )
        self.assertEqual(report.state, DataState.MISSING)
        self.assertIn("晚于计算时间", report.reasons[0])

    def test_blocked_data_cannot_be_wrapped_with_new_action(self) -> None:
        report = evaluate_data_freshness((), now=NOW, policy=FreshnessPolicy())
        with self.assertRaisesRegex(ContractError, "不得生成"):
            DecisionResult(
                ticker="603986.SH",
                computed_at=NOW,
                data_freshness=report,
                action=TradingAction(action_type="hold", label="继续持有"),
                reasons=("测试",),
                current_scenario=None,
            )

    def test_blocked_data_can_show_last_valid_action_as_non_executable_context(self) -> None:
        report = evaluate_data_freshness((), now=NOW, policy=FreshnessPolicy())
        presentation = build_action_presentation(
            execution_state=ExecutionState.DATA_BLOCKED,
            action=None,
            reason="五分钟K已过期",
            next_condition="等待行情恢复",
            data_as_of=NOW - timedelta(minutes=10),
            force_non_executable=True,
        )
        result = DecisionResult(
            ticker="603986.SH",
            computed_at=NOW,
            data_freshness=report,
            action=None,
            reasons=("数据暂停",),
            current_scenario=None,
            position_state=PositionState.POSITIONED,
            execution_state=ExecutionState.DATA_BLOCKED,
            presentation=presentation,
            last_valid_action=TradingAction(action_type="hold", label="继续持有"),
            last_valid_at=NOW - timedelta(minutes=10),
        )
        self.assertFalse(result.presentation.executable)
        self.assertEqual(result.last_valid_action.action_type, "hold")

    def test_defensive_action_can_continue_without_market_context(self) -> None:
        stock_only = tuple(
            item for item in _sources()
            if item.source in {"stock_quote", "stock_5m"}
        )
        defensive = evaluate_action_freshness(
            stock_only,
            now=NOW,
            policy=FreshnessPolicy(),
            intent=ActionIntent.DEFENSIVE,
        )
        active = evaluate_action_freshness(
            stock_only,
            now=NOW,
            policy=FreshnessPolicy(),
            intent=ActionIntent.ACTIVE,
        )
        self.assertTrue(defensive.can_generate_action)
        self.assertFalse(active.can_generate_action)


if __name__ == "__main__":
    unittest.main()
