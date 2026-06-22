import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.decision import ActionMapper, build_case_review, snapshot_from_dict
from shilun.llm_bridge import LLMPayload, PromptRenderer


class ActionMapperTests(unittest.TestCase):
    def test_model_layer_can_drive_follow_trend_decision(self) -> None:
        snapshot = snapshot_from_dict(
            {
                "ticker": "600009.SH",
                "analysis_date": "2026-03-18",
                "weekly_trend": "up",
                "daily_state": "trend",
                "structure_type": "trend_continue",
                "structure_score": 76,
                "trigger_state": "confirmed",
                "opportunity_type": "trend_follow",
                "entry_probability": 0.66,
                "entry_zone": "candidate",
                "breakout_quality": "valid",
                "pullback_quality": "healthy",
                "price_action_quality": "healthy",
                "chip_pressure": "low",
                "chip_support": "high",
                "chip_vacuum": "open",
                "risk_score": 32,
                "invalidation_level": 13.2,
                "regime_label": "strong_up",
                "regime_score": 82.0,
                "regime_confidence": 0.79,
                "p_continue_10d": 0.71,
                "p_breakout_success": 0.63,
                "p_fail_5d": 0.22,
                "risk_level": 0.26,
            }
        )
        decision = ActionMapper().map_actions(snapshot)
        self.assertEqual("high_quality_continuation", decision.conclusion_label)
        self.assertEqual("buy_on_confirmation", decision.watching_action)
        self.assertIn("MODEL_CONTINUE_HIGH", decision.reason_codes)

    def test_high_quality_continuation_maps_to_follow_trend_actions(self) -> None:
        snapshot = snapshot_from_dict(
            {
                "ticker": "600000.SH",
                "analysis_date": "2026-03-18",
                "weekly_trend": "up",
                "daily_state": "trend",
                "structure_type": "trend_continue",
                "structure_score": 80,
                "trigger_state": "confirmed",
                "opportunity_type": "trend_follow",
                "entry_probability": 0.69,
                "entry_zone": "ready",
                "volume_state": "expand",
                "breakout_quality": "valid",
                "pullback_quality": "healthy",
                "price_action_quality": "healthy",
                "volume_score": 75,
                "chip_pressure": "low",
                "chip_support": "high",
                "chip_vacuum": "open",
                "chip_score": 70,
                "support_main": 10.2,
                "pressure_main": 11.4,
                "invalidation_level": 9.8,
                "risk_score": 35,
                "risk_tags": ["trend_ok"],
            }
        )
        decision = ActionMapper().map_actions(snapshot)
        self.assertEqual("high_quality_continuation", decision.conclusion_label)
        self.assertEqual("buy_on_confirmation", decision.watching_action)
        self.assertIn("BREAKOUT_VALID", decision.reason_codes)

    def test_confirmation_needed_blocks_aggressive_chasing(self) -> None:
        snapshot = snapshot_from_dict(
            {
                "ticker": "600001.SH",
                "analysis_date": "2026-03-18",
                "weekly_trend": "up",
                "daily_state": "transition",
                "structure_type": "breakout_pullback",
                "structure_score": 62,
                "trigger_state": "watch",
                "opportunity_type": "observe",
                "entry_probability": 0.44,
                "entry_zone": "watch",
                "breakout_quality": "suspicious",
                "price_action_quality": "unknown",
                "chip_pressure": "mid",
                "chip_support": "mid",
                "chip_vacuum": "mixed",
                "risk_score": 55,
                "invalidation_level": 12.1,
            }
        )
        decision = ActionMapper().map_actions(snapshot)
        self.assertEqual("confirmation_needed", decision.conclusion_label)
        self.assertEqual("wait_for_confirmation", decision.watching_action)
        self.assertIn("需要突破后的量价确认", decision.confirmation_needed)
        self.assertIn("需要把当日触发强度从观察提升到确认", decision.confirmation_needed)

    def test_failed_setup_falls_back_to_defense(self) -> None:
        snapshot = snapshot_from_dict(
            {
                "ticker": "600002.SH",
                "analysis_date": "2026-03-18",
                "weekly_trend": "down",
                "daily_state": "rebound",
                "structure_type": "weak_rebound",
                "structure_score": 40,
                "trigger_state": "exhausted",
                "opportunity_type": "reject",
                "entry_probability": 0.21,
                "entry_zone": "avoid",
                "breakout_quality": "invalid",
                "price_action_quality": "exhausting",
                "chip_pressure": "high",
                "chip_support": "low",
                "chip_vacuum": "blocked",
                "risk_score": 82,
                "invalidation_level": 7.6,
            }
        )
        decision = ActionMapper().map_actions(snapshot)
        self.assertEqual("defense_first", decision.conclusion_label)
        self.assertEqual("exit_on_invalidation", decision.holding_action)

    def test_suspicious_breakout_cannot_map_to_high_quality_continuation(self) -> None:
        snapshot = snapshot_from_dict(
            {
                "ticker": "600011.SH",
                "analysis_date": "2026-03-18",
                "weekly_trend": "up",
                "daily_state": "trend",
                "structure_type": "trend_continue",
                "structure_score": 84,
                "trigger_state": "watch",
                "opportunity_type": "observe",
                "entry_probability": 0.41,
                "entry_zone": "watch",
                "breakout_quality": "suspicious",
                "price_action_quality": "healthy",
                "risk_score": 38,
                "regime_label": "strong_up",
                "p_continue_10d": 0.71,
                "p_breakout_success": 0.60,
                "p_fail_5d": 0.24,
                "p_acceptance_1d": 0.43,
                "p_fail_fast_3d": 0.36,
            }
        )
        decision = ActionMapper().map_actions(snapshot)
        self.assertEqual("confirmation_needed", decision.conclusion_label)
        self.assertEqual("wait_for_confirmation", decision.watching_action)
        self.assertIn("BREAKOUT_SUSPICIOUS", decision.reason_codes)


class PromptRendererTests(unittest.TestCase):
    def test_renderer_includes_model_probability_phrase_when_available(self) -> None:
        snapshot = snapshot_from_dict(
            {
                "ticker": "600010.SH",
                "analysis_date": "2026-03-18",
                "weekly_trend": "up",
                "daily_state": "trend",
                "structure_type": "trend_continue",
                "structure_score": 75,
                "breakout_quality": "valid",
                "price_action_quality": "healthy",
                "trigger_state": "confirmed",
                "opportunity_type": "trend_follow",
                "entry_probability": 0.63,
                "entry_zone": "candidate",
                "chip_pressure": "mid",
                "chip_support": "high",
                "chip_vacuum": "open",
                "risk_score": 28,
                "invalidation_level": 12.8,
                "regime_label": "strong_up",
                "p_continue_10d": 0.68,
                "p_breakout_success": 0.62,
                "p_fail_5d": 0.24,
                "risk_level": 0.22,
                "market_context": {
                    "benchmark_ticker": "000300.SH",
                    "benchmark_weekly_trend": "up",
                    "excess_return_5d": 0.031,
                    "excess_return_20d": 0.082,
                    "relative_strength_label": "显著强于基准",
                },
                "evidence_sections": {
                    "structure": ["结构延续，且回踩质量健康"],
                    "market": ["相对基准显著走强"],
                    "model": ["模型层判断为 strong_up，延续概率 0.68，突破成功率 0.62，失效概率 0.24"],
                    "conflict": ["当前结构、相对强弱和模型判断没有明显对冲项"],
                },
            }
        )
        decision = ActionMapper().map_actions(snapshot)
        payload = LLMPayload.from_snapshot(snapshot, decision)
        rendered = PromptRenderer().render(payload)
        self.assertIn("【概率矩阵】", rendered)
        self.assertIn("延续 0.68", rendered)
        self.assertIn("【趋势判断】", rendered)

    def test_renderer_degrades_when_space_evidence_missing(self) -> None:
        snapshot = snapshot_from_dict(
            {
                "ticker": "600003.SH",
                "analysis_date": "2026-03-18",
                "weekly_trend": "range",
                "daily_state": "rebound",
                "structure_type": "weak_rebound",
                "structure_score": 58,
                "breakout_quality": "suspicious",
                "price_action_quality": "divergent",
                "trigger_state": "watch",
                "opportunity_type": "observe",
                "entry_probability": 0.39,
                "entry_zone": "avoid",
                "chip_pressure": "unknown",
                "chip_support": "unknown",
                "chip_vacuum": "unknown",
                "risk_score": 60,
                "invalidation_level": 5.2,
                "evidence_sections": {
                    "structure": ["结构仍偏弱反弹"],
                    "market": ["暂无基准上下文，当前相对强弱仍按个股自身特征解释"],
                    "model": ["模型层暂无增量证据"],
                    "conflict": ["突破质量尚未转为有效"],
                },
            }
        )
        decision = ActionMapper().map_actions(snapshot)
        payload = LLMPayload.from_snapshot(snapshot, decision)
        rendered = PromptRenderer().render(payload)
        self.assertIn("空间判断缺少筹码支持", rendered)
        self.assertIn("【动作建议】", rendered)
        self.assertIn("失效位 5.20", rendered)

    def test_case_review_keeps_rule_revision_context(self) -> None:
        snapshot = snapshot_from_dict(
            {
                "ticker": "600004.SH",
                "analysis_date": "2026-03-18",
                "weekly_trend": "up",
                "daily_state": "trend",
                "structure_type": "trend_continue",
                "structure_score": 73,
                "breakout_quality": "valid",
                "price_action_quality": "healthy",
                "chip_pressure": "mid",
                "chip_support": "high",
                "chip_vacuum": "open",
                "risk_score": 33,
                "invalidation_level": 15.5,
            }
        )
        decision = ActionMapper().map_actions(snapshot)
        case_review = build_case_review(
            snapshot,
            decision,
            outcome_verdict="validated",
            misjudgment_reason="",
            rule_revision_note="收紧高位放量场景的确认要求",
        )
        self.assertEqual("validated", case_review.outcome_verdict)
        self.assertIn("收紧高位放量场景", case_review.rule_revision_note)


if __name__ == "__main__":
    unittest.main()
