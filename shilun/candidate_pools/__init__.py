"""Candidate pool state layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class CandidatePoolStatus(StrEnum):
    CANDIDATE = "candidate_pool"
    WATCH = "watch_pool"
    BUY = "buy_pool"
    REJECT = "reject_pool"


POOL_PRIORITY: dict[str, int] = {
    CandidatePoolStatus.REJECT.value: 0,
    CandidatePoolStatus.CANDIDATE.value: 1,
    CandidatePoolStatus.WATCH.value: 2,
    CandidatePoolStatus.BUY.value: 3,
}


@dataclass(frozen=True)
class CandidatePoolDecision:
    pool_status: CandidatePoolStatus
    reasons: list[str]
    score: float

    def to_record(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["pool_status"] = self.pool_status.value
        payload["reasons"] = list(self.reasons)
        return payload


def classify_candidate_pool(record: dict[str, Any]) -> CandidatePoolDecision:
    """Classify a ranked snapshot record into one of the four phase-3 pools."""

    # Candidate pools consume flat snapshot fields only; they must not call
    # pipeline, ranker, Tushare, or notification code.
    rank = _int(record.get("rank"), default=999999)
    action_label = str(record.get("action_label") or "")
    candidate_tag_count = _int(record.get("candidate_tag_count"), default=0)
    strategy_signal_count = _int(record.get("strategy_signal_count"), default=0)
    execution_score = _float(record.get("execution_score"))
    risk_score = _float(record.get("risk_score"))
    entry_probability = _float(record.get("entry_probability"))
    p_fail_fast_3d = _float(record.get("p_fail_fast_3d"))
    p_continue_10d = _float(record.get("p_continue_10d"))
    target_position_pct = _int(record.get("target_position_pct"), default=0)
    trend_stage = str(record.get("trend_stage") or "")
    conclusion_label = str(record.get("conclusion_label") or "")

    reject_reasons = []
    if action_label == "stand_aside" or target_position_pct <= 0:
        reject_reasons.append("执行层建议观望或仓位为 0")
    if risk_score >= 70:
        reject_reasons.append("风险分过高")
    if p_fail_fast_3d >= 0.45:
        reject_reasons.append("3 日快败概率过高")
    if _float(record.get("distribution_score")) >= 65:
        reject_reasons.append("分布/派发压力过高")
    if reject_reasons:
        return CandidatePoolDecision(CandidatePoolStatus.REJECT, reject_reasons, _pool_score(record))

    buy_reasons = []
    if action_label in {"build", "probe"} and target_position_pct >= 20:
        buy_reasons.append("执行动作和目标仓位达到买入池门槛")
    if execution_score >= 30 and risk_score <= 55:
        buy_reasons.append("执行分与风险约束匹配")
    if entry_probability >= 0.55 and p_fail_fast_3d <= 0.35:
        buy_reasons.append("入场概率与快败风险可接受")
    if len(buy_reasons) >= 3:
        return CandidatePoolDecision(CandidatePoolStatus.BUY, buy_reasons, _pool_score(record))

    watch_reasons = []
    if action_label in {"watch", "probe", "build"}:
        watch_reasons.append("执行层仍给出观察或试仓信号")
    if candidate_tag_count > 0:
        watch_reasons.append("命中候选标签")
    if strategy_signal_count > 1:
        watch_reasons.append("命中测试策略")
    if execution_score >= 22 and risk_score <= 65:
        watch_reasons.append("执行分未失效且风险可控")
    if p_continue_10d >= 0.45 or trend_stage in {"early", "mid"}:
        watch_reasons.append("延续概率或趋势阶段仍可观察")
    if len(watch_reasons) >= 3:
        return CandidatePoolDecision(CandidatePoolStatus.WATCH, watch_reasons, _pool_score(record))

    candidate_reasons = []
    if rank <= 500:
        candidate_reasons.append("进入当日全市场候选排名范围")
    if candidate_tag_count > 0 or strategy_signal_count > 0:
        candidate_reasons.append("存在候选标签或策略信号")
    if conclusion_label in {"high_quality_continuation", "confirmation_needed"}:
        candidate_reasons.append("结论标签仍具备候选意义")
    if not candidate_reasons:
        candidate_reasons.append("保留为基础候选观察样本")
    return CandidatePoolDecision(CandidatePoolStatus.CANDIDATE, candidate_reasons, _pool_score(record))


def _pool_score(record: dict[str, Any]) -> float:
    execution_score = _float(record.get("execution_score"))
    entry_probability = _float(record.get("entry_probability")) * 100.0
    continuation = _float(record.get("p_continue_10d")) * 100.0
    risk_score = _float(record.get("risk_score"))
    fail_fast = _float(record.get("p_fail_fast_3d")) * 100.0
    score = 0.42 * execution_score + 0.22 * entry_probability + 0.18 * continuation - 0.12 * risk_score - 0.06 * fail_fast
    return round(max(0.0, min(100.0, score)), 4)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

__all__ = ["CandidatePoolDecision", "CandidatePoolStatus", "POOL_PRIORITY", "classify_candidate_pool"]
