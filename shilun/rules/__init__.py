from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuleContext:
    """Runtime inputs shared by atomic candidate rules."""

    ticker: str
    snapshot: Mapping[str, Any]
    latest_bar: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateTag:
    """A non-ranking observation tag produced by the phase-3 rule center."""

    code: str
    factor_group: str
    reason: str
    score: float
    validation_path: str
    source: str


@dataclass(frozen=True)
class AtomicRule:
    """Smallest explainable rule unit for candidate tag generation."""

    code: str
    factor_group: str
    description: str
    required_fields: tuple[str, ...]
    validation_path: str
    evaluator: Callable[[RuleContext], CandidateTag | None]

    def evaluate(self, context: RuleContext) -> CandidateTag | None:
        return self.evaluator(context)


class RuleRegistry:
    """Ordered registry for atomic candidate-tag rules."""

    def __init__(self, rules: Iterable[AtomicRule] | None = None) -> None:
        self._rules: list[AtomicRule] = []
        self._codes: set[str] = set()
        for rule in rules or ():
            self.register(rule)

    def register(self, rule: AtomicRule) -> None:
        if rule.code in self._codes:
            raise ValueError(f"duplicate rule code: {rule.code}")
        self._rules.append(rule)
        self._codes.add(rule.code)

    def evaluate(self, context: RuleContext) -> list[CandidateTag]:
        tags: list[CandidateTag] = []
        for rule in self._rules:
            tag = rule.evaluate(context)
            if tag is not None:
                tags.append(tag)
        return tags

    @property
    def rules(self) -> tuple[AtomicRule, ...]:
        return tuple(self._rules)


def build_default_candidate_tag_registry() -> RuleRegistry:
    # External strategy ideas first become observable tags. Validation promotes
    # them later; this registry must not alter ranking or portfolio sizing.
    return RuleRegistry(
        [
            AtomicRule(
                code="rps_breakout",
                factor_group="trend,market_env,sector_env",
                description="强势相对表现叠加突破胜率的候选标签",
                required_fields=(
                    "structure_score",
                    "entry_probability",
                    "p_breakout_success",
                    "market_context.market_trend_score",
                    "sector_context.sector_strength_score",
                ),
                validation_path="research/validation/candidate_tags/rps_breakout.md",
                evaluator=_evaluate_rps_breakout,
            ),
            AtomicRule(
                code="turtle_breakout",
                factor_group="trend,volume_price,risk",
                description="海龟式真实突破观察标签",
                required_fields=("entry_style", "p_breakout_success", "gentle_expand_score", "distribution_score", "risk_score"),
                validation_path="research/validation/candidate_tags/turtle_breakout.md",
                evaluator=_evaluate_turtle_breakout,
            ),
            AtomicRule(
                code="high_tight_flag",
                factor_group="structure,volume_price,risk",
                description="强势整理/高位紧凑形态观察标签",
                required_fields=("mid_stage_score", "gentle_expand_score", "stall_score", "distribution_score", "risk_score"),
                validation_path="research/validation/candidate_tags/high_tight_flag.md",
                evaluator=_evaluate_high_tight_flag,
            ),
            AtomicRule(
                code="earnings_surprise",
                factor_group="event,quality",
                description="业绩预告或利润增速超预期观察标签",
                required_fields=("earnings_surprise_pct", "profit_growth_yoy", "revenue_growth_yoy"),
                validation_path="research/validation/candidate_tags/earnings_surprise.md",
                evaluator=_evaluate_earnings_surprise,
            ),
            AtomicRule(
                code="dividend_quality",
                factor_group="quality,risk",
                description="分红稳定性与股东回报质量观察标签",
                required_fields=("cash_dividend_yield", "dividend_years", "dividend_consistency_score"),
                validation_path="research/validation/candidate_tags/dividend_quality.md",
                evaluator=_evaluate_dividend_quality,
            ),
        ]
    )


def format_candidate_tags(tags: list[CandidateTag]) -> str:
    return ",".join(tag.code for tag in tags)


def format_candidate_tag_reasons(tags: list[CandidateTag]) -> str:
    return "; ".join(f"{tag.code}: {tag.reason}" for tag in tags)


def _evaluate_rps_breakout(context: RuleContext) -> CandidateTag | None:
    structure_score = _number(_value(context, "structure_score"))
    entry_probability = _number(_value(context, "entry_probability"))
    breakout_success = _number(_value(context, "p_breakout_success"))
    market_score = _number(_value(context, "market_context.market_trend_score"))
    sector_strength = _number(_value(context, "sector_context.sector_strength_score"))
    if not (
        structure_score >= 75
        and entry_probability >= 0.65
        and breakout_success >= 0.58
        and market_score >= 55
        and sector_strength >= 55
    ):
        return None

    score = (
        structure_score * 0.25
        + entry_probability * 100 * 0.2
        + breakout_success * 100 * 0.25
        + market_score * 0.15
        + sector_strength * 0.15
    )
    return _tag(
        code="rps_breakout",
        factor_group="trend,market_env,sector_env",
        reason="突破胜率、入场概率、结构分以及市场/板块强度同时达标；RPS 横截面强度将在 M2 验证中补全。",
        score=score,
        validation_path="research/validation/candidate_tags/rps_breakout.md",
        source="Sequoia-X",
    )


def _evaluate_turtle_breakout(context: RuleContext) -> CandidateTag | None:
    entry_style = str(_value(context, "entry_style", default="") or "")
    breakout_success = _number(_value(context, "p_breakout_success"))
    gentle_expand = _number(_value(context, "gentle_expand_score"))
    distribution = _number(_value(context, "distribution_score"))
    risk_score = _number(_value(context, "risk_score"))
    is_breakout_style = "breakout" in entry_style or breakout_success >= 0.6
    if not (
        is_breakout_style
        and breakout_success >= 0.55
        and gentle_expand >= 60
        and distribution <= 35
        and risk_score <= 55
    ):
        return None

    score = breakout_success * 100 * 0.35 + gentle_expand * 0.3 + (100 - risk_score) * 0.2 + (100 - distribution) * 0.15
    return _tag(
        code="turtle_breakout",
        factor_group="trend,volume_price,risk",
        reason="突破风格、放量质量、低分布压力和可控风险共同满足，用作海龟突破观察标签。",
        score=score,
        validation_path="research/validation/candidate_tags/turtle_breakout.md",
        source="Sequoia-X",
    )


def _evaluate_high_tight_flag(context: RuleContext) -> CandidateTag | None:
    mid_stage_score = _number(_value(context, "mid_stage_score"))
    gentle_expand = _number(_value(context, "gentle_expand_score"))
    stall_score = _number(_value(context, "stall_score"))
    distribution = _number(_value(context, "distribution_score"))
    risk_score = _number(_value(context, "risk_score"))
    if not (
        mid_stage_score >= 70
        and gentle_expand >= 60
        and stall_score <= 35
        and distribution <= 35
        and risk_score <= 50
    ):
        return None

    score = mid_stage_score * 0.35 + gentle_expand * 0.25 + (100 - stall_score) * 0.2 + (100 - distribution) * 0.1 + (100 - risk_score) * 0.1
    return _tag(
        code="high_tight_flag",
        factor_group="structure,volume_price,risk",
        reason="中期趋势、温和放量、低滞涨和低分布压力形成强势整理观察形态。",
        score=score,
        validation_path="research/validation/candidate_tags/high_tight_flag.md",
        source="Sequoia-X,Alpha",
    )


def _evaluate_earnings_surprise(context: RuleContext) -> CandidateTag | None:
    surprise_pct = _number(_value(context, "earnings_surprise_pct", "event_context.earnings_surprise_pct"))
    profit_growth = _number(_value(context, "profit_growth_yoy", "fundamental_context.profit_growth_yoy"))
    revenue_growth = _number(_value(context, "revenue_growth_yoy", "fundamental_context.revenue_growth_yoy"))
    market_score = _number(_value(context, "market_context.market_trend_score"))
    has_surprise = surprise_pct >= 0.15
    has_quality_growth = profit_growth >= 0.25 and revenue_growth >= 0.1
    if not ((has_surprise or has_quality_growth) and market_score >= 45):
        return None

    score = min(100.0, surprise_pct * 100 * 0.35 + profit_growth * 100 * 0.4 + revenue_growth * 100 * 0.25)
    return _tag(
        code="earnings_surprise",
        factor_group="event,quality",
        reason="业绩超预期或利润/收入增速组合达标，先作为事件型观察标签等待样本外验证。",
        score=score,
        validation_path="research/validation/candidate_tags/earnings_surprise.md",
        source="BaoStock",
    )


def _evaluate_dividend_quality(context: RuleContext) -> CandidateTag | None:
    dividend_yield = _number(_value(context, "cash_dividend_yield", "fundamental_context.cash_dividend_yield"))
    dividend_years = _number(_value(context, "dividend_years", "fundamental_context.dividend_years"))
    consistency = _number(_value(context, "dividend_consistency_score", "fundamental_context.dividend_consistency_score"))
    fundamental_score = _number(_value(context, "fundamental_context.fundamental_score"))
    if not (
        (dividend_yield >= 0.025 and dividend_years >= 3)
        or (consistency >= 70 and fundamental_score >= 60)
    ):
        return None

    score = min(100.0, dividend_yield * 1000 * 0.35 + min(dividend_years, 10) * 5 * 0.25 + consistency * 0.4)
    return _tag(
        code="dividend_quality",
        factor_group="quality,risk",
        reason="分红收益率/连续性或分红稳定性与基本面质量达标，用作股东回报质量观察标签。",
        score=score,
        validation_path="research/validation/candidate_tags/dividend_quality.md",
        source="BaoStock",
    )


def _tag(
    *,
    code: str,
    factor_group: str,
    reason: str,
    score: float,
    validation_path: str,
    source: str,
) -> CandidateTag:
    return CandidateTag(
        code=code,
        factor_group=factor_group,
        reason=reason,
        score=round(score, 4),
        validation_path=validation_path,
        source=source,
    )


def _number(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _value(context: RuleContext, *paths: str, default: Any = None) -> Any:
    for path in paths:
        value = _path_value(context.snapshot, path)
        if value is not None:
            return value
        value = _path_value(context.metadata, path)
        if value is not None:
            return value
        if context.latest_bar is not None:
            value = _path_value(context.latest_bar, path)
            if value is not None:
                return value
    return default


def _path_value(source: Any, path: str) -> Any:
    current = source
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
            continue
        if hasattr(current, "get"):
            current = current.get(part)
            continue
        return None
    return current


__all__ = [
    "AtomicRule",
    "CandidateTag",
    "RuleContext",
    "RuleRegistry",
    "build_default_candidate_tag_registry",
    "format_candidate_tag_reasons",
    "format_candidate_tags",
]
