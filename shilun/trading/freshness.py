"""Decision Engine 的数据安全门。"""

from __future__ import annotations

from datetime import datetime

from shilun.trading.config import FreshnessPolicy
from shilun.trading.contracts import (
    ActionIntent,
    DataFreshnessReport,
    DataState,
    FactSourceSnapshot,
    SourceFreshness,
    require_aware_datetime,
)


def evaluate_data_freshness(
    sources: tuple[FactSourceSnapshot, ...],
    *,
    now: datetime,
    policy: FreshnessPolicy,
    required_sources: tuple[str, ...] | None = None,
    optional_sources: tuple[str, ...] | None = None,
) -> DataFreshnessReport:
    """评估必需来源；缺失、未来时间或时间倒退均阻止生成新动作。"""

    require_aware_datetime(now, field_name="now")
    indexed = {item.source: item for item in sources}
    results: list[SourceFreshness] = []

    required = required_sources or policy.required_sources
    optional = policy.optional_sources if optional_sources is None else optional_sources
    evaluated_sources = tuple(dict.fromkeys((*required, *optional)))
    for source in evaluated_sources:
        snapshot = indexed.get(source)
        max_age = policy.max_age_seconds[source]
        if snapshot is None or snapshot.as_of is None:
            results.append(SourceFreshness(
                source=source,
                state=DataState.MISSING,
                age_seconds=None,
                max_age_seconds=max_age,
                reason="缺少必需数据或数据时间",
            ))
            continue

        age = (now - snapshot.as_of).total_seconds()
        if snapshot.previous_as_of is not None and snapshot.as_of < snapshot.previous_as_of:
            results.append(SourceFreshness(
                source=source,
                state=DataState.MISSING,
                age_seconds=round(age, 3),
                max_age_seconds=max_age,
                reason="数据时间戳倒退",
            ))
        elif age < -policy.future_skew_seconds:
            results.append(SourceFreshness(
                source=source,
                state=DataState.MISSING,
                age_seconds=round(age, 3),
                max_age_seconds=max_age,
                reason="数据时间晚于计算时间",
            ))
        elif age > max_age:
            results.append(SourceFreshness(
                source=source,
                state=DataState.DELAYED,
                age_seconds=round(age, 3),
                max_age_seconds=max_age,
                reason=f"数据延迟超过 {max_age} 秒",
            ))
        else:
            results.append(SourceFreshness(
                source=source,
                state=DataState.NORMAL,
                age_seconds=round(max(age, 0.0), 3),
                max_age_seconds=max_age,
                reason="数据正常",
            ))

    blocking_results = [item for item in results if item.source in required]
    if any(item.state is DataState.MISSING for item in blocking_results):
        state = DataState.MISSING
    elif any(item.state is DataState.DELAYED for item in blocking_results):
        state = DataState.DELAYED
    else:
        state = DataState.NORMAL
    reasons = tuple(
        f"{item.source}：{item.reason}"
        for item in results
        if item.state is not DataState.NORMAL
    )
    return DataFreshnessReport(
        state=state,
        can_generate_action=state is DataState.NORMAL,
        sources=tuple(results),
        reasons=reasons,
    )


def evaluate_action_freshness(
    sources: tuple[FactSourceSnapshot, ...],
    *,
    now: datetime,
    policy: FreshnessPolicy,
    intent: ActionIntent,
) -> DataFreshnessReport:
    """按动作意图执行数据降级：硬止损不被板块或指数短暂故障阻断。"""

    required = (
        policy.defensive_required_sources
        if intent is ActionIntent.DEFENSIVE
        else policy.required_sources
    )
    return evaluate_data_freshness(
        sources,
        now=now,
        policy=policy,
        required_sources=required,
        optional_sources=() if intent is ActionIntent.DEFENSIVE else policy.optional_sources,
    )
