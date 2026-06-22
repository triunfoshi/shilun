"""Decision layer: semantic snapshot, action mapping, and execution scoring.

M5 simplification note:
Decision code was previously spread across `domain`, `policy`,
`action_mapper`, `case_review`, and `execution_engine`. Those files were
strongly coupled and mostly used together by the pipeline, so this module keeps
the same public API while making the decision layer a single readable boundary.
The compatibility aliases at the bottom keep older import paths working.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass, field
from importlib import resources
from typing import Any


# ---------------------------------------------------------------------------
# Domain schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionSnapshot:
    ticker: str
    analysis_date: str
    weekly_trend: str
    daily_state: str
    structure_type: str
    structure_score: int
    structure_bias: str = "neutral"
    structure_stage: str = "transition"
    confirmation_state: str = "pending"
    confirmation_score: int = 0
    trigger_score: int | None = None
    trigger_state: str = "watch"
    opportunity_type: str = "observe"
    entry_probability: float | None = None
    entry_zone: str | None = None
    structure_confidence: float = 0.0
    volume_state: str = "unknown"
    breakout_quality: str = "unknown"
    pullback_quality: str = "unknown"
    price_action_quality: str = "unknown"
    volume_score: int = 0
    chip_pressure: str = "unknown"
    chip_support: str = "unknown"
    chip_vacuum: str = "unknown"
    chip_score: int = 0
    support_main: float | None = None
    support_basis: str | None = None
    pressure_main: float | None = None
    resistance_basis: str | None = None
    invalidation_level: float | None = None
    risk_score: int = 0
    regime_label: str | None = None
    regime_score: float | None = None
    regime_confidence: float | None = None
    p_continue_10d: float | None = None
    p_breakout_success: float | None = None
    p_fail_5d: float | None = None
    p_acceptance_1d: float | None = None
    p_fail_fast_3d: float | None = None
    expected_return_10d: float | None = None
    expected_drawdown_10d: float | None = None
    risk_level: float | None = None
    model_version: str | None = None
    risk_tags: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    market_context: dict[str, Any] = field(default_factory=dict)
    evidence_sections: dict[str, list[str]] = field(default_factory=dict)
    structure_assessment: dict[str, Any] = field(default_factory=dict)
    trigger_context: dict[str, Any] = field(default_factory=dict)

    def has_structure_evidence(self) -> bool:
        return bool(self.structure_type) and self.structure_score > 0

    def has_strength_evidence(self) -> bool:
        return any(
            value not in {"unknown", "", None}
            for value in (self.breakout_quality, self.pullback_quality, self.price_action_quality, self.volume_state)
        )

    def has_space_evidence(self) -> bool:
        return any(value not in {"unknown", "", None} for value in (self.chip_pressure, self.chip_support, self.chip_vacuum))


@dataclass(frozen=True)
class ActionDecision:
    conclusion_label: str
    watching_action: str
    holding_action: str
    confirmation_needed: list[str]
    invalidation: float | None
    reason_codes: list[str]
    risk_summary: str


@dataclass(frozen=True)
class CaseReviewRecord:
    ticker: str
    snapshot_date: str
    case_type: str
    structure_label: str
    strength_label: str
    space_label: str
    support_main: float | None
    pressure_main: float | None
    invalidation: float | None
    decision_summary: str
    outcome_verdict: str
    misjudgment_reason: str | None = None
    rule_revision_note: str | None = None


@dataclass(frozen=True)
class ExecutionDecision:
    action_label: str
    entry_style: str
    target_position_pct: int
    execution_score: float
    execution_risk_score: float
    continuation_edge_score: float
    fast_fail_penalty_score: float
    execution_quality_score: float
    overhang_pressure_score: float
    regime_tailwind_score: float
    support_quality_score: float
    support_band_low: float | None
    support_band_high: float | None
    pressure_band_low: float | None
    pressure_band_high: float | None
    entry_band_low: float | None
    entry_band_high: float | None
    invalidation_price: float | None
    validation_next_day: list[str]
    weight_breakdown: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def snapshot_from_dict(payload: dict[str, Any]) -> DecisionSnapshot:
    return DecisionSnapshot(
        ticker=payload["ticker"],
        analysis_date=payload["analysis_date"],
        weekly_trend=payload["weekly_trend"],
        daily_state=payload["daily_state"],
        structure_type=payload["structure_type"],
        structure_score=int(payload.get("structure_score", 0)),
        structure_bias=payload.get("structure_bias", "neutral"),
        structure_stage=payload.get("structure_stage", "transition"),
        confirmation_state=payload.get("confirmation_state", "pending"),
        confirmation_score=int(payload.get("confirmation_score", 0)),
        trigger_score=None if payload.get("trigger_score") is None else int(payload.get("trigger_score", 0)),
        trigger_state=payload.get("trigger_state", "watch"),
        opportunity_type=payload.get("opportunity_type", "observe"),
        entry_probability=payload.get("entry_probability"),
        entry_zone=payload.get("entry_zone"),
        structure_confidence=float(payload.get("structure_confidence", 0.0) or 0.0),
        volume_state=payload.get("volume_state", "unknown"),
        breakout_quality=payload.get("breakout_quality", "unknown"),
        pullback_quality=payload.get("pullback_quality", "unknown"),
        price_action_quality=payload.get("price_action_quality", "unknown"),
        volume_score=int(payload.get("volume_score", 0)),
        chip_pressure=payload.get("chip_pressure", "unknown"),
        chip_support=payload.get("chip_support", "unknown"),
        chip_vacuum=payload.get("chip_vacuum", "unknown"),
        chip_score=int(payload.get("chip_score", 0)),
        support_main=payload.get("support_main"),
        support_basis=payload.get("support_basis"),
        pressure_main=payload.get("pressure_main"),
        resistance_basis=payload.get("resistance_basis"),
        invalidation_level=payload.get("invalidation_level"),
        risk_score=int(payload.get("risk_score", 0)),
        regime_label=payload.get("regime_label"),
        regime_score=payload.get("regime_score"),
        regime_confidence=payload.get("regime_confidence"),
        p_continue_10d=payload.get("p_continue_10d"),
        p_breakout_success=payload.get("p_breakout_success"),
        p_fail_5d=payload.get("p_fail_5d"),
        p_acceptance_1d=payload.get("p_acceptance_1d"),
        p_fail_fast_3d=payload.get("p_fail_fast_3d"),
        expected_return_10d=payload.get("expected_return_10d"),
        expected_drawdown_10d=payload.get("expected_drawdown_10d"),
        risk_level=payload.get("risk_level"),
        model_version=payload.get("model_version"),
        risk_tags=list(payload.get("risk_tags", [])),
        reason_codes=list(payload.get("reason_codes", [])),
        evidence=list(payload.get("evidence", [])),
        market_context=dict(payload.get("market_context", {})),
        evidence_sections=dict(payload.get("evidence_sections", {})),
        structure_assessment=dict(payload.get("structure_assessment", {})),
        trigger_context=dict(payload.get("trigger_context", {})),
    )


# ---------------------------------------------------------------------------
# Policy loading
# ---------------------------------------------------------------------------


def _load_resource_text(filename: str) -> str:
    return resources.files("shilun.config").joinpath(filename).read_text(encoding="utf-8")


def load_policy_document(filename: str) -> dict[str, Any]:
    raw_text = _load_resource_text(filename)
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid policy document: {filename}") from exc


def load_concepts() -> dict[str, Any]:
    return load_policy_document("concepts.yml")


def load_rules() -> dict[str, Any]:
    return load_policy_document("rules.yml")


def load_prompt_policy() -> dict[str, Any]:
    return load_policy_document("prompt_policy.yml")


# ---------------------------------------------------------------------------
# Action mapping
# ---------------------------------------------------------------------------


class ActionMapper:
    """Map semantic decision snapshots to constrained actions."""

    def __init__(self, rules: dict | None = None) -> None:
        self.rules = rules or load_rules()
        self.reason_codes = self.rules["reason_codes"]

    def map_actions(self, snapshot: DecisionSnapshot) -> ActionDecision:
        conclusion = (
            self._guard_conclusion(snapshot)
            or self._match_model_conclusion(snapshot)
            or self._match_conclusion(snapshot)
        )
        reason_codes = self._build_reason_codes(snapshot, conclusion["conclusion_label"])
        confirmation_needed = self._build_confirmation_list(snapshot)
        risk_summary = self._build_risk_summary(snapshot)
        return ActionDecision(
            conclusion_label=conclusion["conclusion_label"],
            watching_action=conclusion["watching_action"],
            holding_action=conclusion["holding_action"],
            confirmation_needed=confirmation_needed,
            invalidation=snapshot.invalidation_level,
            reason_codes=reason_codes,
            risk_summary=risk_summary,
        )

    def _match_conclusion(self, snapshot: DecisionSnapshot) -> dict[str, str]:
        for item in self.rules["conclusion_matrix"]:
            if self._rule_matches(snapshot, item["if"]):
                return {
                    "conclusion_label": item["name"],
                    "watching_action": item["watching_action"],
                    "holding_action": item["holding_action"],
                }
        fallback = self.rules["fallback"]
        return {
            "conclusion_label": fallback["conclusion_label"],
            "watching_action": fallback["watching_action"],
            "holding_action": fallback["holding_action"],
        }

    @staticmethod
    def _match_model_conclusion(snapshot: DecisionSnapshot) -> dict[str, str] | None:
        if snapshot.regime_label is None or snapshot.p_continue_10d is None or snapshot.p_fail_5d is None:
            return None

        if snapshot.confirmation_state == "failed" and snapshot.structure_confidence >= 0.6:
            return {
                "conclusion_label": "defense_first",
                "watching_action": "stand_aside",
                "holding_action": "exit_on_invalidation",
            }

        if (
            snapshot.p_fail_5d >= 0.58
            or (snapshot.p_fail_fast_3d or 0.0) >= 0.48
            or snapshot.regime_label == "risk_reversal"
        ):
            return {
                "conclusion_label": "defense_first",
                "watching_action": "stand_aside",
                "holding_action": "exit_on_invalidation",
            }

        if (
            snapshot.regime_label in {"strong_up", "weak_up"}
            and snapshot.p_continue_10d >= 0.62
            and (snapshot.p_breakout_success or 0.0) >= 0.55
            and snapshot.p_fail_5d <= 0.35
            and (snapshot.entry_probability or 0.0) >= 0.58
            and snapshot.confirmation_state != "failed"
            and snapshot.breakout_quality == "valid"
            and snapshot.trigger_state == "confirmed"
            and snapshot.opportunity_type in {"first_buy", "trend_follow"}
            and snapshot.entry_zone in {"ready", "candidate"}
        ):
            return {
                "conclusion_label": "high_quality_continuation",
                "watching_action": "buy_on_confirmation",
                "holding_action": "hold_above_support",
            }

        if (
            snapshot.regime_label in {"strong_up", "weak_up"}
            and snapshot.p_continue_10d >= 0.52
            and (snapshot.entry_probability or 0.0) >= 0.42
            and snapshot.confirmation_state in {"pending", "confirmed"}
            and snapshot.opportunity_type != "reject"
        ):
            return {
                "conclusion_label": "confirmation_needed",
                "watching_action": "wait_for_confirmation",
                "holding_action": "trim_on_failed_confirmation",
            }

        if snapshot.regime_label == "range" and snapshot.p_fail_5d <= 0.45:
            return {
                "conclusion_label": "momentum_but_heavy_overhead",
                "watching_action": "avoid_chasing",
                "holding_action": "scale_out_into_strength",
            }
        return None

    @staticmethod
    def _guard_conclusion(snapshot: DecisionSnapshot) -> dict[str, str] | None:
        if snapshot.opportunity_type == "reject" or snapshot.trigger_state == "exhausted":
            return {
                "conclusion_label": "defense_first",
                "watching_action": "stand_aside",
                "holding_action": "exit_on_invalidation",
            }

        if (
            snapshot.opportunity_type == "observe"
            and snapshot.breakout_quality == "suspicious"
            and (snapshot.entry_probability or 0.0) < 0.5
        ):
            return {
                "conclusion_label": "confirmation_needed",
                "watching_action": "wait_for_confirmation",
                "holding_action": "trim_on_failed_confirmation",
            }
        return None

    @staticmethod
    def _rule_matches(snapshot: DecisionSnapshot, condition: dict[str, object]) -> bool:
        for key, expected in condition.items():
            if key.endswith("_in"):
                field_name = key[:-3]
                if getattr(snapshot, field_name) not in expected:
                    return False
            elif key.endswith("_gte"):
                field_name = key[:-4]
                if getattr(snapshot, field_name) < expected:
                    return False
            elif key.endswith("_lte"):
                field_name = key[:-4]
                if getattr(snapshot, field_name) > expected:
                    return False
            else:
                if getattr(snapshot, key) != expected:
                    return False
        return True

    def _build_reason_codes(self, snapshot: DecisionSnapshot, conclusion_label: str) -> list[str]:
        codes = list(snapshot.reason_codes)
        if snapshot.structure_type in {"trend_continue", "breakout_pullback"}:
            codes.append("STRUCTURE_TREND_CONTINUE")
        if snapshot.confirmation_state == "confirmed":
            codes.append("STRUCTURE_CONFIRMATION_READY")
        if snapshot.confirmation_state == "failed":
            codes.append("STRUCTURE_CONFIRMATION_FAILED")
        if snapshot.trigger_state == "confirmed":
            codes.append("TRIGGER_CONFIRMED")
        if snapshot.trigger_state == "exhausted":
            codes.append("TRIGGER_EXHAUSTED")
        if snapshot.entry_zone == "ready":
            codes.append("ENTRY_ZONE_READY")
        if snapshot.entry_zone == "avoid":
            codes.append("ENTRY_ZONE_AVOID")
        if snapshot.opportunity_type == "first_buy":
            codes.append("OPPORTUNITY_FIRST_BUY")
        if snapshot.opportunity_type == "trend_follow":
            codes.append("OPPORTUNITY_TREND_FOLLOW")
        if snapshot.opportunity_type == "reject":
            codes.append("OPPORTUNITY_REJECT")
        if snapshot.breakout_quality == "valid":
            codes.append("BREAKOUT_VALID")
        if snapshot.breakout_quality == "suspicious":
            codes.append("BREAKOUT_SUSPICIOUS")
        if snapshot.pullback_quality == "healthy":
            codes.append("PULLBACK_HEALTHY")
        if snapshot.chip_vacuum == "open":
            codes.append("SPACE_OPEN")
        if snapshot.chip_pressure == "high":
            codes.append("SPACE_HEAVY")
        if snapshot.risk_score >= 70:
            codes.append("RISK_SCORE_HIGH")
        if snapshot.invalidation_level is not None:
            codes.append("RISK_INVALIDATION_NEAR")
        if snapshot.regime_label in {"strong_up", "weak_up"}:
            codes.append("MODEL_REGIME_UP")
        if snapshot.regime_label == "risk_reversal":
            codes.append("MODEL_REGIME_REVERSAL")
        if snapshot.p_continue_10d is not None and snapshot.p_continue_10d >= 0.6:
            codes.append("MODEL_CONTINUE_HIGH")
        if snapshot.p_fail_5d is not None and snapshot.p_fail_5d >= 0.5:
            codes.append("MODEL_FAIL_HIGH")
        if snapshot.p_fail_fast_3d is not None and snapshot.p_fail_fast_3d >= 0.45:
            codes.append("MODEL_FAIL_FAST_HIGH")
        if conclusion_label == "confirmation_needed":
            codes.append("STRUCTURE_CONFIRMATION_MISSING")
        return list(dict.fromkeys(codes))

    def _build_confirmation_list(self, snapshot: DecisionSnapshot) -> list[str]:
        items: list[str] = []
        if snapshot.confirmation_state == "failed":
            items.append("需要先把结构确认状态从失败修复到待确认以上")
        if snapshot.trigger_state == "watch":
            items.append("需要把当日触发强度从观察提升到确认")
        if snapshot.trigger_state == "exhausted":
            items.append("当日量价已有衰竭迹象，不能按确认买点处理")
        if snapshot.breakout_quality != "valid":
            items.append("需要突破后的量价确认")
        if snapshot.structure_stage in {"trend_pullback", "rebound_repair"}:
            items.append("需要回踩后重新出现承接，而不是继续放量回落")
        elif snapshot.pullback_quality not in {"healthy", "unknown"}:
            items.append("需要回踩承接恢复")
        if snapshot.chip_pressure == "high":
            items.append("需要先消化上方筹码压力")
        if snapshot.risk_score >= 70:
            items.append("需要先降低系统性风险敞口")
        if snapshot.p_breakout_success is not None and snapshot.p_breakout_success < 0.55:
            items.append("需要突破成功率进一步提升")
        if snapshot.p_continue_10d is not None and snapshot.p_continue_10d < 0.55:
            items.append("需要趋势延续概率重新抬升")
        if snapshot.p_acceptance_1d is not None and snapshot.p_acceptance_1d < 0.5:
            items.append("需要次日承接概率重新抬升后再考虑进攻")
        if snapshot.entry_probability is not None and snapshot.entry_probability < 0.45:
            items.append("当前入场概率曲线仍偏保守，更适合继续观察")
        return items or ["当前条件已基本齐备，仍需盯住失效位"]

    @staticmethod
    def _build_risk_summary(snapshot: DecisionSnapshot) -> str:
        summary_parts = []
        if snapshot.risk_tags:
            summary_parts.append("风险标签: " + ", ".join(snapshot.risk_tags))
        if snapshot.invalidation_level is not None:
            summary_parts.append(f"失效位: {snapshot.invalidation_level:.2f}")
        if snapshot.p_continue_10d is not None and snapshot.p_fail_5d is not None:
            summary_parts.append(
                f"模型概率: 延续 {snapshot.p_continue_10d:.2f} / 失效 {snapshot.p_fail_5d:.2f}"
            )
        if snapshot.p_acceptance_1d is not None or snapshot.p_fail_fast_3d is not None:
            acceptance = "--" if snapshot.p_acceptance_1d is None else f"{snapshot.p_acceptance_1d:.2f}"
            fail_fast = "--" if snapshot.p_fail_fast_3d is None else f"{snapshot.p_fail_fast_3d:.2f}"
            summary_parts.append(f"短线验证: 承接 {acceptance} / 快速失败 {fail_fast}")
        if snapshot.entry_probability is not None:
            zone = snapshot.entry_zone or "unknown"
            summary_parts.append(f"入场曲线: 概率 {snapshot.entry_probability:.2f} / 分区 {zone}")
        if snapshot.risk_score >= 70:
            summary_parts.append("综合风险偏高，优先防守")
        elif snapshot.risk_score >= 45:
            summary_parts.append("综合风险中等，避免追高")
        else:
            summary_parts.append("综合风险可控，但仍需条件确认")
        if snapshot.risk_level is not None:
            summary_parts.append(f"模型风险等级 {snapshot.risk_level:.2f}")
        return "；".join(summary_parts)


def build_case_review(
    snapshot: DecisionSnapshot,
    decision: ActionDecision,
    outcome_verdict: str,
    misjudgment_reason: str | None = None,
    rule_revision_note: str | None = None,
) -> CaseReviewRecord:
    return CaseReviewRecord(
        ticker=snapshot.ticker,
        snapshot_date=snapshot.analysis_date,
        case_type=decision.conclusion_label,
        structure_label=snapshot.structure_type,
        strength_label=snapshot.price_action_quality,
        space_label=snapshot.chip_pressure,
        support_main=snapshot.support_main,
        pressure_main=snapshot.pressure_main,
        invalidation=snapshot.invalidation_level,
        decision_summary=(
            f"未持仓={decision.watching_action}; 已持仓={decision.holding_action}; "
            f"确认事项={','.join(decision.confirmation_needed)}"
        ),
        outcome_verdict=outcome_verdict,
        misjudgment_reason=misjudgment_reason,
        rule_revision_note=rule_revision_note,
    )


# ---------------------------------------------------------------------------
# Execution scoring
# ---------------------------------------------------------------------------


class ExecutionEngine:
    def __init__(
        self,
        *,
        expert_weights: dict[str, float] | None = None,
        hard_gate_params: dict[str, float] | None = None,
    ) -> None:
        self.expert_weights = expert_weights or {
            "continuation_edge": 0.30,
            "execution_quality": 0.20,
            "regime_tailwind": 0.15,
            "support_quality": 0.15,
            "fast_fail_penalty": 0.25,
            "overhang_pressure": 0.15,
            "risk_penalty": 0.10,
        }
        self.hard_gate_params = hard_gate_params or {
            "max_fail_fast": 0.45,
            "max_risk_score": 70.0,
            "max_overhang_ratio": 0.12,
            "min_entry_probability": 0.45,
        }

    def evaluate(
        self,
        *,
        latest_close: float | None,
        atr_abs: float | None,
        snapshot: dict[str, Any],
        market_context: dict[str, Any],
        sector_context: dict[str, Any],
        fundamental_context: dict[str, Any],
        chip_context: dict[str, Any],
    ) -> ExecutionDecision:
        continuation_edge = self._continuation_edge(snapshot, market_context, sector_context, fundamental_context)
        fast_fail_penalty = self._fast_fail_penalty(snapshot)
        execution_quality = self._execution_quality(snapshot)
        overhang_pressure = self._overhang_pressure(snapshot, chip_context)
        regime_tailwind = self._regime_tailwind(market_context, sector_context)
        support_quality = self._support_quality(snapshot, chip_context)
        risk_penalty = self._risk_penalty(snapshot)

        metrics = {
            "continuation_edge": continuation_edge,
            "execution_quality": execution_quality,
            "regime_tailwind": regime_tailwind,
            "support_quality": support_quality,
            "fast_fail_penalty": fast_fail_penalty,
            "overhang_pressure": overhang_pressure,
            "risk_penalty": risk_penalty,
        }
        entropy_weights = self._entropy_weights(metrics)
        final_weights = {
            key: round(0.65 * entropy_weights.get(key, 0.0) + 0.35 * self.expert_weights.get(key, 0.0), 4)
            for key in metrics
        }
        execution_score = (
            final_weights["continuation_edge"] * continuation_edge
            + final_weights["execution_quality"] * execution_quality
            + final_weights["regime_tailwind"] * regime_tailwind
            + final_weights["support_quality"] * support_quality
            - final_weights["fast_fail_penalty"] * fast_fail_penalty
            - final_weights["overhang_pressure"] * overhang_pressure
            - final_weights["risk_penalty"] * risk_penalty
        )
        execution_risk_score = min(100.0, max(0.0, 0.55 * risk_penalty + 0.45 * fast_fail_penalty))

        support_band_low, support_band_high = self._support_band(snapshot, chip_context, atr_abs)
        pressure_band_low, pressure_band_high = self._pressure_band(snapshot, chip_context, atr_abs)
        entry_band_low, entry_band_high, entry_style = self._entry_band(
            snapshot=snapshot,
            support_band_low=support_band_low,
            support_band_high=support_band_high,
            pressure_band_low=pressure_band_low,
            pressure_band_high=pressure_band_high,
            atr_abs=atr_abs,
        )
        invalidation_price = self._invalidation_price(snapshot, support_band_low, atr_abs)
        validation_next_day = self._validation_next_day(snapshot, market_context, sector_context)

        gated = self._hard_gate(snapshot, chip_context, invalidation_price, latest_close)
        action_label, target_position_pct = self._resolve_action(
            execution_score=execution_score,
            execution_risk_score=execution_risk_score,
            entry_style=entry_style,
            gated=gated,
        )

        return ExecutionDecision(
            action_label=action_label,
            entry_style=entry_style,
            target_position_pct=target_position_pct,
            execution_score=round(execution_score, 4),
            execution_risk_score=round(execution_risk_score, 4),
            continuation_edge_score=round(continuation_edge, 4),
            fast_fail_penalty_score=round(fast_fail_penalty, 4),
            execution_quality_score=round(execution_quality, 4),
            overhang_pressure_score=round(overhang_pressure, 4),
            regime_tailwind_score=round(regime_tailwind, 4),
            support_quality_score=round(support_quality, 4),
            support_band_low=support_band_low,
            support_band_high=support_band_high,
            pressure_band_low=pressure_band_low,
            pressure_band_high=pressure_band_high,
            entry_band_low=entry_band_low,
            entry_band_high=entry_band_high,
            invalidation_price=invalidation_price,
            validation_next_day=validation_next_day,
            weight_breakdown=final_weights,
        )

    @staticmethod
    def _continuation_edge(
        snapshot: dict[str, Any],
        market_context: dict[str, Any],
        sector_context: dict[str, Any],
        fundamental_context: dict[str, Any],
    ) -> float:
        score = 0.0
        score += 35.0 * float(snapshot.get("p_continue_10d") or 0.0)
        score += 18.0 * float(snapshot.get("p_acceptance_1d") or 0.0)
        score += 12.0 * float(snapshot.get("entry_probability") or 0.0)
        score += 0.12 * float(snapshot.get("gentle_expand_score") or 0.0)
        score += 0.10 * float(snapshot.get("pullback_shrink_score") or 0.0)
        score += 0.10 * float(snapshot.get("mid_stage_score") or 0.0)
        score += 0.08 * float(snapshot.get("early_stage_score") or 0.0)
        score -= 0.14 * float(snapshot.get("late_stage_score") or 0.0)
        score += 8.0 * float(sector_context.get("sector_strength_score") or 0.0) / 100.0
        score += 6.0 * float(market_context.get("market_trend_score") or 0.0) / 100.0
        score += 6.0 * float(fundamental_context.get("fundamental_score") or 0.0) / 100.0
        return min(100.0, max(0.0, score))

    @staticmethod
    def _fast_fail_penalty(snapshot: dict[str, Any]) -> float:
        return min(100.0, max(0.0, float(snapshot.get("p_fail_fast_3d") or 0.0) * 100.0))

    @staticmethod
    def _execution_quality(snapshot: dict[str, Any]) -> float:
        score = 0.0
        score += 26.0 * float(snapshot.get("p_acceptance_1d") or 0.0)
        score += 20.0 * float(snapshot.get("entry_probability") or 0.0)
        score += 12.0 if snapshot.get("trigger_state") == "confirmed" else 0.0
        score += 8.0 if snapshot.get("breakout_quality") == "valid" else -4.0
        score += 6.0 if snapshot.get("pullback_quality") == "healthy" else 0.0
        score += 10.0 if snapshot.get("opportunity_type") in {"first_buy", "trend_follow"} else -6.0
        score += 0.16 * float(snapshot.get("gentle_expand_score") or 0.0)
        score += 0.18 * float(snapshot.get("pullback_shrink_score") or 0.0)
        score -= 0.14 * float(snapshot.get("distribution_score") or 0.0)
        score -= 0.12 * float(snapshot.get("stall_score") or 0.0)
        score -= 0.08 * float(snapshot.get("impulsive_spike_score") or 0.0)
        return min(100.0, max(0.0, score))

    @staticmethod
    def _overhang_pressure(snapshot: dict[str, Any], chip_context: dict[str, Any]) -> float:
        score = 15.0 if snapshot.get("chip_pressure") == "low" else 35.0 if snapshot.get("chip_pressure") == "mid" else 60.0
        score += 250.0 * float(chip_context.get("overhang_ratio") or 0.0)
        score += 20.0 * float(chip_context.get("pressure_density") or 0.0)
        score -= 8.0 * float(chip_context.get("vacuum_up_ratio") or 0.0)
        score -= 10.0 * float(chip_context.get("winner_ratio") or chip_context.get("winner_rate") or 0.0)
        return min(100.0, max(0.0, score))

    @staticmethod
    def _regime_tailwind(market_context: dict[str, Any], sector_context: dict[str, Any]) -> float:
        score = 45.0
        score += 0.35 * float(market_context.get("market_trend_score") or 0.0)
        score += 0.45 * float(sector_context.get("sector_trend_score") or 0.0)
        score += 0.20 * float(sector_context.get("sector_strength_score") or 0.0)
        return min(100.0, max(0.0, score))

    @staticmethod
    def _support_quality(snapshot: dict[str, Any], chip_context: dict[str, Any]) -> float:
        score = 0.0
        if snapshot.get("support_main") is not None:
            score += 35.0
        if snapshot.get("support_basis"):
            score += 15.0
        score += 25.0 * float(chip_context.get("support_density") or 0.0)
        score += 15.0 if snapshot.get("pullback_quality") == "healthy" else 0.0
        score += 0.12 * float(snapshot.get("pullback_shrink_score") or 0.0)
        score += 0.10 * float(snapshot.get("mid_stage_score") or 0.0)
        return min(100.0, max(0.0, score))

    @staticmethod
    def _risk_penalty(snapshot: dict[str, Any]) -> float:
        score = float(snapshot.get("risk_score") or 0.0)
        score += 30.0 * float(snapshot.get("risk_level") or 0.0)
        score += 0.12 * float(snapshot.get("late_stage_score") or 0.0)
        score += 0.10 * float(snapshot.get("distribution_score") or 0.0)
        score += 0.08 * float(snapshot.get("stall_score") or 0.0)
        score -= 0.06 * float(snapshot.get("early_stage_score") or 0.0)
        return min(100.0, max(0.0, score))

    def _hard_gate(
        self,
        snapshot: dict[str, Any],
        chip_context: dict[str, Any],
        invalidation_price: float | None,
        latest_close: float | None,
    ) -> bool:
        if float(snapshot.get("p_fail_fast_3d") or 0.0) >= self.hard_gate_params["max_fail_fast"]:
            return True
        if (
            float(snapshot.get("risk_score") or 0.0) >= self.hard_gate_params["max_risk_score"]
            and float(snapshot.get("late_stage_score") or 0.0) >= 60.0
        ):
            return True
        if float(chip_context.get("overhang_ratio") or 0.0) >= self.hard_gate_params["max_overhang_ratio"]:
            return True
        if float(snapshot.get("distribution_score") or 0.0) >= 65.0:
            return True
        if float(snapshot.get("entry_probability") or 0.0) < self.hard_gate_params["min_entry_probability"]:
            return True
        if snapshot.get("trigger_state") == "exhausted":
            return True
        if invalidation_price is not None and latest_close:
            invalidation_distance = abs(latest_close - invalidation_price) / latest_close
            if invalidation_distance <= 0.018:
                return True
        return False

    @staticmethod
    def _resolve_action(
        *,
        execution_score: float,
        execution_risk_score: float,
        entry_style: str,
        gated: bool,
    ) -> tuple[str, int]:
        if gated:
            return "stand_aside", 0
        if execution_score >= 42 and execution_risk_score <= 28:
            return "build", 50 if entry_style != "no_chase" else 35
        if execution_score >= 30 and execution_risk_score <= 40:
            return "probe", 20
        if execution_score >= 22 and execution_risk_score <= 55:
            return "watch", 10
        return "stand_aside", 0

    @staticmethod
    def _support_band(snapshot: dict[str, Any], chip_context: dict[str, Any], atr_abs: float | None) -> tuple[float | None, float | None]:
        support = _pick_first(snapshot.get("support_main"), chip_context.get("cost_50"), chip_context.get("cost_15"), chip_context.get("avg_cost"))
        if support is None:
            return None, None
        buffer_abs = max(0.0, float(atr_abs or 0.0) * 0.6)
        return round(support - buffer_abs, 4), round(support + buffer_abs, 4)

    @staticmethod
    def _pressure_band(snapshot: dict[str, Any], chip_context: dict[str, Any], atr_abs: float | None) -> tuple[float | None, float | None]:
        pressure = _pick_first(snapshot.get("pressure_main"), chip_context.get("cost_85"), chip_context.get("cost_95"))
        if pressure is None:
            return None, None
        buffer_abs = max(0.0, float(atr_abs or 0.0) * 0.6)
        return round(pressure - buffer_abs, 4), round(pressure + buffer_abs, 4)

    @staticmethod
    def _entry_band(
        *,
        snapshot: dict[str, Any],
        support_band_low: float | None,
        support_band_high: float | None,
        pressure_band_low: float | None,
        pressure_band_high: float | None,
        atr_abs: float | None,
    ) -> tuple[float | None, float | None, str]:
        if float(snapshot.get("late_stage_score") or 0.0) >= 60.0:
            return pressure_band_high, pressure_band_high, "no_chase"
        if (
            snapshot.get("opportunity_type") == "first_buy"
            or float(snapshot.get("pullback_shrink_score") or 0.0) >= 60.0
        ) and support_band_low is not None and support_band_high is not None:
            return support_band_low, support_band_high, "pullback_only"
        if (
            snapshot.get("trigger_state") == "confirmed"
            and float(snapshot.get("gentle_expand_score") or 0.0) >= 55.0
            and pressure_band_high is not None
        ):
            buffer_abs = max(0.0, float(atr_abs or 0.0) * 0.2)
            return round(pressure_band_high, 4), round(pressure_band_high + buffer_abs, 4), "breakout_only"
        return support_band_low, support_band_high, "breakout_or_pullback"

    @staticmethod
    def _invalidation_price(snapshot: dict[str, Any], support_band_low: float | None, atr_abs: float | None) -> float | None:
        base = _pick_first(snapshot.get("invalidation_level"), support_band_low)
        if base is None:
            return None
        buffer_abs = max(0.0, float(atr_abs or 0.0) * 0.2)
        return round(base - buffer_abs, 4)

    @staticmethod
    def _validation_next_day(snapshot: dict[str, Any], market_context: dict[str, Any], sector_context: dict[str, Any]) -> list[str]:
        items = []
        if float(snapshot.get("p_acceptance_1d") or 0.0) < 0.5:
            items.append("次日承接概率仍偏弱，需要看到收盘重新站稳确认区。")
        else:
            items.append("次日优先看是否继续承接，而不是冲高回落。")
        if float(snapshot.get("p_fail_fast_3d") or 0.0) > 0.25:
            items.append("快败风险不低，次日不能放量下破支撑带。")
        if float(snapshot.get("distribution_score") or 0.0) >= 58.0:
            items.append("高位分布分数偏高，次日若再放量但实体变小，应继续降预期。")
        if float(snapshot.get("late_stage_score") or 0.0) >= 60.0:
            items.append("趋势末期分数偏高，次日不能把冲高误判为新一轮主升。")
        if float(sector_context.get("sector_strength_score") or 0.0) < 45:
            items.append("板块强度一般，需要板块同步转强后才适合扩大仓位。")
        if float(market_context.get("market_trend_score") or 0.0) < 45:
            items.append("大盘顺风不足，先按试仓或观察处理。")
        return items or ["若次日量价继续稳定，则可以维持当前执行方案。"]

    @staticmethod
    def _entropy_weights(metrics: dict[str, float]) -> dict[str, float]:
        normalized = {key: min(1.0, max(0.0, float(value) / 100.0)) for key, value in metrics.items()}
        total = sum(normalized.values()) or 1.0
        probabilities = {key: value / total for key, value in normalized.items()}
        entropy_components = {}
        for key, probability in probabilities.items():
            entropy_components[key] = 0.0 if probability <= 0 else -(probability * math.log(probability))
        entropy_total = sum(entropy_components.values()) or 1.0
        divergence = {key: 1.0 - value / entropy_total for key, value in entropy_components.items()}
        divergence_total = sum(divergence.values()) or 1.0
        return {key: value / divergence_total for key, value in divergence.items()}


def _pick_first(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


__all__ = [
    "ActionDecision",
    "ActionMapper",
    "CaseReviewRecord",
    "DecisionSnapshot",
    "ExecutionDecision",
    "ExecutionEngine",
    "build_case_review",
    "load_concepts",
    "load_policy_document",
    "load_prompt_policy",
    "load_rules",
    "snapshot_from_dict",
]


_COMPAT_SUBMODULES = ("action_mapper", "case_review", "domain", "execution_engine", "policy")
for _module_name in _COMPAT_SUBMODULES:
    sys.modules[f"{__name__}.{_module_name}"] = sys.modules[__name__]
