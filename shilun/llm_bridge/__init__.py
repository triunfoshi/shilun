"""LLM bridge for turning structured decisions into constrained prompts.

M5 simplification note:
The bridge is intentionally small, so schema and renderer now live together.
This removes a circular-looking split between `schema.py` and `renderer.py`
while keeping old imports aliased for compatibility.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass

from shilun.decision import ActionDecision, DecisionSnapshot, load_prompt_policy


@dataclass(frozen=True)
class LLMPayload:
    ticker: str
    analysis_date: str
    weekly_trend: str
    daily_state: str
    structure_type: str
    structure_bias: str
    structure_stage: str
    confirmation_state: str
    confirmation_score: int
    structure_confidence: float
    support_main: float | None
    support_basis: str | None
    pressure_main: float | None
    resistance_basis: str | None
    invalidation_level: float | None
    volume_state: str
    breakout_quality: str
    pullback_quality: str
    price_action_quality: str
    chip_pressure: str
    chip_support: str
    chip_vacuum: str
    regime_label: str | None
    regime_score: float | None
    regime_confidence: float | None
    p_continue_10d: float | None
    p_breakout_success: float | None
    p_fail_5d: float | None
    p_acceptance_1d: float | None
    p_fail_fast_3d: float | None
    entry_probability: float | None
    entry_zone: str | None
    risk_level: float | None
    trigger_state: str
    opportunity_type: str
    position_state: str | None
    volume_pattern: str | None
    conclusion_label: str
    confirmation_needed: list[str]
    reason_codes: list[str]
    risk_score: int
    risk_tags: list[str]
    evidence: list[str]
    market_context: dict
    evidence_sections: dict[str, list[str]]
    has_structure_evidence: bool
    has_strength_evidence: bool
    has_space_evidence: bool
    trend_block: list[str]
    volume_block: list[str]
    probability_block: list[str]
    action_block: list[str]
    watching_action_text: str
    holding_action_text: str
    watch_items: list[str]
    risk_items: list[str]

    @classmethod
    def from_snapshot(cls, snapshot: DecisionSnapshot, decision: ActionDecision) -> "LLMPayload":
        watch_items = [
            "若放量站稳确认区，则可以升级为顺势跟随",
            "若回踩缩量且不破支撑，则可保留继续观察",
        ]
        risk_items = [
            f"若跌破失效位 {snapshot.invalidation_level:.2f} 则放弃当前乐观预期"
            if snapshot.invalidation_level is not None
            else "失效位尚未明确前，不宜提高仓位",
            "若量价继续走弱，则按确认不足处理",
        ]
        if snapshot.chip_pressure == "high":
            risk_items.append("上方阻力较重，即使走强也更可能边走边换手")
        position_state = snapshot.trigger_context.get("position_state")
        volume_pattern = snapshot.trigger_context.get("volume_pattern")
        return cls(
            ticker=snapshot.ticker,
            analysis_date=snapshot.analysis_date,
            weekly_trend=snapshot.weekly_trend,
            daily_state=snapshot.daily_state,
            structure_type=snapshot.structure_type,
            structure_bias=snapshot.structure_bias,
            structure_stage=snapshot.structure_stage,
            confirmation_state=snapshot.confirmation_state,
            confirmation_score=snapshot.confirmation_score,
            structure_confidence=snapshot.structure_confidence,
            support_main=snapshot.support_main,
            support_basis=snapshot.support_basis,
            pressure_main=snapshot.pressure_main,
            resistance_basis=snapshot.resistance_basis,
            invalidation_level=snapshot.invalidation_level,
            volume_state=snapshot.volume_state,
            breakout_quality=snapshot.breakout_quality,
            pullback_quality=snapshot.pullback_quality,
            price_action_quality=snapshot.price_action_quality,
            chip_pressure=snapshot.chip_pressure,
            chip_support=snapshot.chip_support,
            chip_vacuum=snapshot.chip_vacuum,
            regime_label=snapshot.regime_label,
            regime_score=snapshot.regime_score,
            regime_confidence=snapshot.regime_confidence,
            p_continue_10d=snapshot.p_continue_10d,
            p_breakout_success=snapshot.p_breakout_success,
            p_fail_5d=snapshot.p_fail_5d,
            p_acceptance_1d=snapshot.p_acceptance_1d,
            p_fail_fast_3d=snapshot.p_fail_fast_3d,
            entry_probability=snapshot.entry_probability,
            entry_zone=snapshot.entry_zone,
            risk_level=snapshot.risk_level,
            trigger_state=snapshot.trigger_state,
            opportunity_type=snapshot.opportunity_type,
            position_state=position_state,
            volume_pattern=volume_pattern,
            conclusion_label=decision.conclusion_label,
            confirmation_needed=decision.confirmation_needed,
            reason_codes=decision.reason_codes,
            risk_score=snapshot.risk_score,
            risk_tags=snapshot.risk_tags,
            evidence=snapshot.evidence,
            market_context=snapshot.market_context,
            evidence_sections=snapshot.evidence_sections,
            has_structure_evidence=snapshot.has_structure_evidence(),
            has_strength_evidence=snapshot.has_strength_evidence(),
            has_space_evidence=snapshot.has_space_evidence(),
            trend_block=_build_trend_block(snapshot),
            volume_block=_build_volume_block(snapshot, position_state, volume_pattern),
            probability_block=_build_probability_block(snapshot),
            action_block=_build_action_block(snapshot, decision),
            watching_action_text=_watching_action_text(decision.watching_action),
            holding_action_text=_holding_action_text(decision.holding_action),
            watch_items=watch_items,
            risk_items=risk_items,
        )


class PromptRenderer:
    """Render constrained analyst text from structured fields."""

    def __init__(self, policy: dict | None = None) -> None:
        self.policy = policy or load_prompt_policy()

    def render(self, payload: LLMPayload) -> str:
        degrade_prefixes = self._collect_degrade_prefixes(payload)
        header = self._build_conclusion_line(payload, degrade_prefixes)
        return (
            f"{header}\n"
            f"{self._build_section('趋势判断', payload.trend_block)}\n"
            f"{self._build_section('量价关系', payload.volume_block)}\n"
            f"{self._build_section('概率矩阵', payload.probability_block)}\n"
            f"{self._build_section('动作建议', payload.action_block)}"
        )

    def render_user_prompt(self, payload: LLMPayload) -> dict:
        return {
            "system_prompt": " ".join(self.policy["system_prompt"]),
            "input": asdict(payload),
            "rendered_text": self.render(payload),
        }

    def _collect_degrade_prefixes(self, payload: LLMPayload) -> list[str]:
        prefixes: list[str] = []
        degrade_rules = self.policy["degrade_rules"]
        if not payload.has_structure_evidence:
            prefixes.append(degrade_rules["missing_structure"]["fallback_phrase"])
        if not payload.has_strength_evidence:
            prefixes.append(degrade_rules["missing_strength"]["fallback_phrase"])
        if not payload.has_space_evidence:
            prefixes.append(degrade_rules["missing_space"]["fallback_phrase"])
        return prefixes

    @staticmethod
    def _build_conclusion_line(payload: LLMPayload, degrade_prefixes: list[str]) -> str:
        prefix = " ".join(degrade_prefixes).strip()
        prefix = f"{prefix} " if prefix else ""
        return (
            "【总览】"
            f"{prefix}当前更偏 {_translate(payload.conclusion_label)}。"
            f" 这里先看状态定义，再看量价确认，最后结合概率矩阵给动作，不把单日波动直接当成结论。"
        )

    @staticmethod
    def _build_section(title: str, items: list[str] | None) -> str:
        content = "；".join(items or ["暂无足够证据。"])
        return f"【{title}】{content}。"


def _watching_action_text(action: str) -> str:
    mapping = {
        "buy_on_pullback": "更适合等回踩确认后再跟随，不建议直接追高。",
        "buy_on_confirmation": "确认型机会可以跟随，但前提是当日触发与结构共振，不用强行等回踩。",
        "wait_for_confirmation": "先等量价补确认，暂不急于进场。",
        "avoid_chasing": "短线有冲劲但不划算，优先等待更好的风险收益比。",
        "stand_aside": "当前更适合旁观，等结构和风险重新收敛。",
    }
    return mapping.get(action, "先观察，不做激进预判。")


def _holding_action_text(action: str) -> str:
    mapping = {
        "hold_above_support": "已持仓以守住关键支撑为前提继续持有，跌破则收缩风险。",
        "trim_on_failed_confirmation": "若后续确认继续缺失，可先减仓再等二次信号。",
        "scale_out_into_strength": "若冲高接近压力区，可分批兑现，避免把短冲当趋势加速。",
        "exit_on_invalidation": "一旦跌破失效位，优先退出当前交易假设。",
    }
    return mapping.get(action, "先按防守思路处理，等下一次结构确认。")


def _build_trend_block(snapshot: DecisionSnapshot) -> list[str]:
    support = _format_level(snapshot.support_main)
    invalidation = _format_level(snapshot.invalidation_level)
    return [
        (
            f"当前更像{_translate(snapshot.daily_state)}，长周期方向偏{_translate(snapshot.weekly_trend)}；"
            f"结构上定义为{_translate(snapshot.structure_type)}，当前阶段是{_translate(snapshot.structure_stage)}。"
        ),
        (
            f"确认状态为{_translate(snapshot.confirmation_state)}，更关键的分水岭在"
            f"{support if support != '待确认' else '支撑区'}附近；若失守 {invalidation}，则先按结构失效处理。"
        ),
    ]


def _build_volume_block(
    snapshot: DecisionSnapshot,
    position_state: str | None,
    volume_pattern: str | None,
) -> list[str]:
    lines = [
        (
            f"位置更偏{_translate(position_state)}，量价类型更像{_translate(volume_pattern)}；"
            f"突破质量为{_translate(snapshot.breakout_quality)}，回踩质量为{_translate(snapshot.pullback_quality)}。"
        )
    ]
    if snapshot.trigger_state == "confirmed":
        lines.append("量价目前更偏确认成立，而不是单日情绪脉冲；若后续继续守住确认区，可把它按顺势延续理解。")
    elif snapshot.trigger_state == "exhausted":
        lines.append("量价更像分歧或衰竭，不能把当前动作直接定义为有效突破；若后续不能重新收拢承接，应继续降级表达。")
    else:
        lines.append("量价还在观察阶段，目前更像‘结构有想法，但强弱证据还差一口气’；若后续补量且不破位，再考虑升级。")
    return lines


def _build_probability_block(snapshot: DecisionSnapshot) -> list[str]:
    structure_state = "结构成立" if snapshot.structure_type in {"trend_continue", "breakout_pullback"} else "结构一般"
    if snapshot.trigger_state == "confirmed" and snapshot.breakout_quality == "valid":
        volume_state = "量价确认成立"
    elif snapshot.trigger_state == "exhausted" or snapshot.breakout_quality == "suspicious":
        volume_state = "量价偏弱或可疑"
    else:
        volume_state = "量价仍待确认"
    if snapshot.chip_pressure == "high" or snapshot.chip_vacuum == "blocked":
        space_state = "空间一般"
    elif snapshot.chip_vacuum == "open" and snapshot.chip_pressure == "low":
        space_state = "空间顺畅"
    else:
        space_state = "空间中性"
    return [
        (
            f"概率矩阵：延续 {_format_prob(snapshot.p_continue_10d)}｜突破 {_format_prob(snapshot.p_breakout_success)}"
            f"｜承接 {_format_prob(snapshot.p_acceptance_1d)}｜快败 {_format_prob(snapshot.p_fail_fast_3d)}"
            f"｜入场 {_format_prob(snapshot.entry_probability)}（{_translate(snapshot.entry_zone)}）。"
        ),
        f"矩阵表达：{structure_state} + {volume_state} + {space_state} -> {_matrix_conclusion(snapshot, structure_state, volume_state, space_state)}",
    ]


def _build_action_block(snapshot: DecisionSnapshot, decision: ActionDecision) -> list[str]:
    support = _format_level(snapshot.support_main)
    pressure = _format_level(snapshot.pressure_main)
    invalidation = _format_level(snapshot.invalidation_level)
    confirmation_item = decision.confirmation_needed[0] if decision.confirmation_needed else "继续盯住确认区与失效位"
    risk_item = snapshot.risk_tags[0] if snapshot.risk_tags else "风险仍需绑定失效位管理"
    return [
        f"关键区间：主支撑 {support}；主压力/确认区 {pressure}；失效位 {invalidation}。",
        f"未持仓：{_watching_action_text(decision.watching_action)} 已持仓：{_holding_action_text(decision.holding_action)}",
        f"若 {confirmation_item}，则维持当前判断；若失守 {invalidation} 或量价继续走弱，则按防守处理。风险提示：{risk_item}。",
    ]


def _matrix_conclusion(
    snapshot: DecisionSnapshot,
    structure_state: str,
    volume_state: str,
    space_state: str,
) -> str:
    if (
        structure_state == "结构成立"
        and volume_state == "量价确认成立"
        and space_state == "空间顺畅"
        and (snapshot.entry_probability or 0.0) >= 0.58
    ):
        return "若回踩不破支撑，则可以考虑按顺势思路处理，但仍需绑定失效位"
    if volume_state == "量价偏弱或可疑" or (snapshot.p_fail_fast_3d or 0.0) >= 0.45:
        return "目前更像确认不足或假强，优先观察，不把单日上冲当成趋势成立"
    if space_state == "空间一般":
        return "即使继续走强，也更可能边走边换手，适合降预期而不是激进追高"
    return "目前证据更偏向候选状态，若后续补齐量价确认，则可以把语气从观察升级到跟随"


def _format_level(value: float | None) -> str:
    return "待确认" if value is None else f"{value:.2f}"


def _format_prob(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f}"


def _translate(value: str | None) -> str:
    mapping = {
        "up": "上涨",
        "down": "下行",
        "range": "震荡",
        "trend": "趋势推进",
        "rebound": "反弹修复",
        "consolidation": "整理",
        "transition": "过渡",
        "exhaustion": "衰竭",
        "trend_continue": "趋势延续",
        "breakout_pullback": "突破后回踩",
        "weak_rebound": "弱反弹",
        "range_pivot": "区间枢轴",
        "bullish": "偏多",
        "bearish": "偏空",
        "neutral": "中性",
        "breakout_confirmed": "突破确认",
        "breakout_attempt": "突破尝试",
        "trend_pressing_high": "趋势逼近前高",
        "trend_pullback": "趋势回踩",
        "trend_advancing": "趋势推进",
        "distribution": "高位分布",
        "breakdown": "向下破位",
        "range_rotation": "区间轮动",
        "rebound_repair": "反弹修复",
        "confirmed": "已确认",
        "pending": "待确认",
        "failed": "确认失败",
        "watch": "观察中",
        "exhausted": "衰竭",
        "high_quality_continuation": "高质量延续",
        "confirmation_needed": "需要确认",
        "momentum_but_heavy_overhead": "上冲有动能但上方较重",
        "defense_first": "防守优先",
        "first_buy": "第一类买点",
        "trend_follow": "趋势确认跟随",
        "observe": "继续观察",
        "reject": "放弃",
        "ready": "可执行",
        "candidate": "候选",
        "avoid": "回避",
        "low_base": "低位蓄势",
        "rising": "上升途中",
        "high_zone": "高位区",
        "downtrend": "下跌途中",
        "gentle_expand": "温和放量",
        "impulsive_spike": "脉冲放量",
        "pullback_shrink": "回调缩量",
        "high_level_stall": "高位滞涨缩量",
        "down_shrink": "下跌途中缩量",
        "valid": "有效",
        "suspicious": "可疑",
        "invalid": "无效",
        "healthy": "健康",
        "damaged": "受损",
        "divergent": "分歧",
        "exhausting": "衰竭式波动",
        "low": "低",
        "mid": "中",
        "high": "高",
        "mixed": "混合",
        "open": "打开",
        "blocked": "受阻",
    }
    return mapping.get(value or "", value or "未知")


def build_payload(payload: LLMPayload) -> dict:
    """Return a plain dict that can be sent to OpenClaw/LLM."""
    return asdict(payload)


def build_llm_payload(snapshot: DecisionSnapshot, decision: ActionDecision) -> dict:
    payload = LLMPayload.from_snapshot(snapshot, decision)
    return PromptRenderer().render_user_prompt(payload)


__all__ = ["LLMPayload", "PromptRenderer", "build_llm_payload", "build_payload"]


for _module_name in ("renderer", "schema"):
    sys.modules[f"{__name__}.{_module_name}"] = sys.modules[__name__]
