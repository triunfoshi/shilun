from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


SHILUN_V1_VERSION = "2026.05.12"
SHILUN_V1_RANKING_POLICY = (
    "execution_score -> target_position_pct -> mid_stage_score -> gentle_expand_score -> "
    "pullback_shrink_score -> anti_fail_fast -> market_trend_score -> sector_trend_score -> "
    "fundamental_score -> early_stage_score -> entry_probability -> p_acceptance_1d -> "
    "p_continue_10d -> late_stage_score/distribution/stall/risk"
)


@dataclass(frozen=True)
class StrategyContext:
    """Runtime record used by strategy definitions."""

    ticker: str
    record: Mapping[str, Any]

    @property
    def candidate_tag_set(self) -> set[str]:
        tags = self.record.get("candidate_tags") or ""
        if isinstance(tags, str):
            return {tag.strip() for tag in tags.split(",") if tag.strip()}
        if isinstance(tags, (list, tuple, set)):
            return {str(tag).strip() for tag in tags if str(tag).strip()}
        return set()


@dataclass(frozen=True)
class StrategySignal:
    """A strategy-version hit for one snapshot record."""

    strategy_id: str
    strategy_version: str
    status: str
    matched_candidate_tags: tuple[str, ...]
    factor_groups: tuple[str, ...]
    reason: str
    ranking_policy: str
    validation_report_path: str

    def to_record(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "status": self.status,
            "matched_candidate_tags": list(self.matched_candidate_tags),
            "factor_groups": list(self.factor_groups),
            "reason": self.reason,
            "ranking_policy": self.ranking_policy,
            "validation_report_path": self.validation_report_path,
        }


@dataclass(frozen=True)
class StrategyDefinition:
    """Versioned strategy definition built from fields and candidate tags."""

    strategy_id: str
    strategy_version: str
    name: str
    status: str
    factor_groups: tuple[str, ...]
    factor_fields: tuple[str, ...]
    candidate_tags: tuple[str, ...]
    candidate_tag_mode: str
    ranking_policy: str
    validation_report_path: str
    description: str

    def evaluate(self, context: StrategyContext) -> StrategySignal | None:
        matched_tags = self._matched_candidate_tags(context.candidate_tag_set)
        if self.candidate_tags and not matched_tags:
            return None
        return StrategySignal(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            status=self.status,
            matched_candidate_tags=matched_tags,
            factor_groups=self.factor_groups,
            reason=self._build_reason(matched_tags),
            ranking_policy=self.ranking_policy,
            validation_report_path=self.validation_report_path,
        )

    def _matched_candidate_tags(self, available_tags: set[str]) -> tuple[str, ...]:
        if not self.candidate_tags:
            return ()
        matched = tuple(tag for tag in self.candidate_tags if tag in available_tags)
        if self.candidate_tag_mode == "all":
            return matched if len(matched) == len(self.candidate_tags) else ()
        if self.candidate_tag_mode == "any":
            return matched
        raise ValueError(f"unsupported candidate_tag_mode: {self.candidate_tag_mode}")

    def _build_reason(self, matched_tags: tuple[str, ...]) -> str:
        if not self.candidate_tags:
            return "石论当前基准策略，使用现有排序因子与执行分体系。"
        tags = ",".join(matched_tags)
        return f"命中测试策略标签: {tags}；使用 {self.strategy_id}@{self.strategy_version} 做收益验证。"


class StrategyRegistry:
    """Ordered registry for versioned strategy definitions."""

    def __init__(self, strategies: Iterable[StrategyDefinition] | None = None) -> None:
        self._strategies: list[StrategyDefinition] = []
        self._keys: set[tuple[str, str]] = set()
        for strategy in strategies or ():
            self.register(strategy)

    def register(self, strategy: StrategyDefinition) -> None:
        key = (strategy.strategy_id, strategy.strategy_version)
        if key in self._keys:
            raise ValueError(f"duplicate strategy definition: {strategy.strategy_id}@{strategy.strategy_version}")
        self._strategies.append(strategy)
        self._keys.add(key)

    def evaluate(self, context: StrategyContext) -> list[StrategySignal]:
        signals: list[StrategySignal] = []
        for strategy in self._strategies:
            signal = strategy.evaluate(context)
            if signal is not None:
                signals.append(signal)
        return signals

    @property
    def strategies(self) -> tuple[StrategyDefinition, ...]:
        return tuple(self._strategies)


def build_default_strategy_registry() -> StrategyRegistry:
    # Strategies are versioned gates for validation; they do not change sorting
    # until a validation report promotes them into the main policy.
    return StrategyRegistry(
        [
            StrategyDefinition(
                strategy_id="shilun_v1",
                strategy_version=SHILUN_V1_VERSION,
                name="石论策略 v1",
                status="baseline",
                factor_groups=("trend", "structure", "volume_price", "chip", "risk", "market_env", "sector_env", "quality"),
                factor_fields=(
                    "execution_score",
                    "target_position_pct",
                    "mid_stage_score",
                    "gentle_expand_score",
                    "pullback_shrink_score",
                    "anti_fail_fast",
                    "market_trend_score",
                    "sector_trend_score",
                    "fundamental_score",
                    "early_stage_score",
                    "entry_probability",
                    "p_acceptance_1d",
                    "p_continue_10d",
                    "late_stage_score",
                    "distribution_score",
                    "stall_score",
                    "risk_score",
                    "overhang_ratio",
                    "structure_score",
                ),
                candidate_tags=(),
                candidate_tag_mode="all",
                ranking_policy=SHILUN_V1_RANKING_POLICY,
                validation_report_path="research/validation/strategies/shilun_v1.md",
                description="当前石论全市场排名基准策略，用于和后续测试策略做收益对照。",
            ),
            _test_strategy(
                "shilun_v1_rps_breakout_test",
                "石论 v1 + RPS 突破测试",
                ("trend", "market_env", "sector_env"),
                ("structure_score", "entry_probability", "p_breakout_success", "market_trend_score", "sector_strength_score"),
                ("rps_breakout",),
                "验证强势突破组合是否贡献超额收益。",
            ),
            _test_strategy(
                "shilun_v1_turtle_breakout_test",
                "石论 v1 + 海龟突破测试",
                ("trend", "volume_price", "risk"),
                ("entry_style", "p_breakout_success", "gentle_expand_score", "distribution_score", "risk_score"),
                ("turtle_breakout",),
                "验证真实突破过滤是否有效。",
            ),
            _test_strategy(
                "shilun_v1_high_tight_test",
                "石论 v1 + 强势整理测试",
                ("structure", "volume_price", "risk"),
                ("mid_stage_score", "gentle_expand_score", "stall_score", "distribution_score", "risk_score"),
                ("high_tight_flag",),
                "验证高位紧凑结构的延续收益。",
            ),
            _test_strategy(
                "shilun_v1_event_quality_test",
                "石论 v1 + 事件质量测试",
                ("event", "quality", "risk"),
                ("earnings_surprise_pct", "profit_growth_yoy", "revenue_growth_yoy", "cash_dividend_yield", "dividend_consistency_score"),
                ("earnings_surprise", "dividend_quality"),
                "验证事件/质量因子组合。",
                candidate_tag_mode="any",
            ),
        ]
    )


def format_strategy_ids(signals: list[StrategySignal]) -> str:
    return ",".join(signal.strategy_id for signal in signals)


def format_strategy_versions(signals: list[StrategySignal]) -> str:
    return ",".join(f"{signal.strategy_id}@{signal.strategy_version}" for signal in signals)


def format_strategy_signal_reasons(signals: list[StrategySignal]) -> str:
    return "; ".join(f"{signal.strategy_id}: {signal.reason}" for signal in signals)


def format_strategy_validation_paths(signals: list[StrategySignal]) -> str:
    paths = []
    seen = set()
    for signal in signals:
        if signal.validation_report_path in seen:
            continue
        paths.append(signal.validation_report_path)
        seen.add(signal.validation_report_path)
    return ",".join(paths)


def _test_strategy(
    strategy_id: str,
    name: str,
    factor_groups: tuple[str, ...],
    factor_fields: tuple[str, ...],
    candidate_tags: tuple[str, ...],
    validation_note: str,
    *,
    candidate_tag_mode: str = "all",
) -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id=strategy_id,
        strategy_version=SHILUN_V1_VERSION,
        name=name,
        status="testing",
        factor_groups=factor_groups,
        factor_fields=factor_fields,
        candidate_tags=candidate_tags,
        candidate_tag_mode=candidate_tag_mode,
        ranking_policy=SHILUN_V1_RANKING_POLICY,
        validation_report_path=f"research/validation/strategies/{strategy_id}.md",
        description=f"在石论 v1 基准上叠加候选标签，{validation_note}",
    )


__all__ = [
    "SHILUN_V1_VERSION",
    "StrategyContext",
    "StrategyDefinition",
    "StrategyRegistry",
    "StrategySignal",
    "build_default_strategy_registry",
    "format_strategy_ids",
    "format_strategy_signal_reasons",
    "format_strategy_validation_paths",
    "format_strategy_versions",
]
