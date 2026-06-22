"""Portfolio ranking and position-planning primitives.

M5 减法说明：组合层先保持一个文件，避免 `domain/builder/ranker` 三层
为了几十行代码形成额外引用边。后续若组合约束显著变复杂，再拆出子模块。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PortfolioCandidate:
    ticker: str
    score: float
    risk_score: float = 0.0
    target_weight_hint: float = 0.0
    snapshot: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: Mapping[str, Any],
        *,
        ticker_field: str = "ticker",
        score_field: str = "execution_score",
        risk_field: str = "risk_score",
        weight_hint_field: str = "target_position_pct",
    ) -> "PortfolioCandidate":
        ticker = str(snapshot.get(ticker_field) or "").strip()
        if not ticker:
            raise ValueError("snapshot does not include a ticker")
        score = float(snapshot.get(score_field, 0.0) or 0.0)
        risk_score = float(snapshot.get(risk_field, 0.0) or 0.0)
        target_weight_hint = float(snapshot.get(weight_hint_field, 0.0) or 0.0) / 100.0
        return cls(
            ticker=ticker,
            score=score,
            risk_score=risk_score,
            target_weight_hint=target_weight_hint,
            snapshot=dict(snapshot),
        )


@dataclass(frozen=True)
class PositionTarget:
    ticker: str
    weight: float
    score: float
    risk_score: float


@dataclass(frozen=True)
class PortfolioPlan:
    positions: tuple[PositionTarget, ...]
    cash_weight: float
    candidate_count: int


@dataclass(frozen=True)
class PortfolioConstraints:
    max_positions: int = 5
    max_single_weight: float = 0.2
    min_score: float = 0.0
    max_risk_score: float = 100.0


class SnapshotFieldRanker:
    """Convert snapshot rows into rankable portfolio candidates."""

    def __init__(
        self,
        *,
        score_field: str = "execution_score",
        risk_field: str = "risk_score",
        weight_hint_field: str = "target_position_pct",
    ) -> None:
        self.score_field = score_field
        self.risk_field = risk_field
        self.weight_hint_field = weight_hint_field

    def build_candidates(
        self,
        snapshots: Iterable[Mapping[str, Any]],
    ) -> list[PortfolioCandidate]:
        return [
            PortfolioCandidate.from_snapshot(
                snapshot,
                score_field=self.score_field,
                risk_field=self.risk_field,
                weight_hint_field=self.weight_hint_field,
            )
            for snapshot in snapshots
        ]

    def rank(
        self,
        snapshots: Iterable[Mapping[str, Any]],
    ) -> list[PortfolioCandidate]:
        candidates = self.build_candidates(snapshots)
        return sorted(
            candidates,
            key=lambda item: (item.score, item.target_weight_hint, -item.risk_score, item.ticker),
            reverse=True,
        )


class EqualWeightPortfolioBuilder:
    """Build a simple equal-weight portfolio from ranked candidates."""

    def __init__(self, constraints: PortfolioConstraints | None = None) -> None:
        self.constraints = constraints or PortfolioConstraints()

    def build(
        self,
        candidates: Iterable[PortfolioCandidate],
    ) -> PortfolioPlan:
        eligible = [
            candidate
            for candidate in candidates
            if candidate.score >= self.constraints.min_score and candidate.risk_score <= self.constraints.max_risk_score
        ]
        selected = eligible[: self.constraints.max_positions]
        if not selected:
            return PortfolioPlan(positions=(), cash_weight=1.0, candidate_count=len(eligible))

        equal_weight = min(1.0 / len(selected), self.constraints.max_single_weight)
        positions = tuple(
            PositionTarget(
                ticker=candidate.ticker,
                weight=round(equal_weight, 6),
                score=round(candidate.score, 6),
                risk_score=round(candidate.risk_score, 6),
            )
            for candidate in selected
        )
        invested_weight = sum(position.weight for position in positions)
        cash_weight = round(max(0.0, 1.0 - invested_weight), 6)
        return PortfolioPlan(
            positions=positions,
            cash_weight=cash_weight,
            candidate_count=len(eligible),
        )

__all__ = [
    "EqualWeightPortfolioBuilder",
    "PortfolioCandidate",
    "PortfolioConstraints",
    "PortfolioPlan",
    "PositionTarget",
    "SnapshotFieldRanker",
]
