"""Job 12H：E/N/H/R 全场景的逐根 5 分钟回放与上线闸门。

回放器只把计算时刻及此前已经闭合的 K 线交给 ``FullScenarioPlaybook``。
市场、板块与 E7 三日事实必须由历史快照显式提供；缺失时固定降级为数据阻断，
不能用事后日线或人工猜测补齐。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, time
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from shilun.trading.contracts import ActionType, PositionState, ScenarioState
from shilun.trading.playbooks.execution_scenarios import (
    SCENARIO_CODES,
    ClosedFiveMinuteBar,
    E7ThreeDayFacts,
    FullScenarioPlaybook,
    IntradayMarketState,
    MarketPermission,
    ScenarioExecutionPlan,
    ScenarioMarketFacts,
    SectorExecutionState,
)


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
REQUIRED_REPLAY_COVERAGE: dict[str, tuple[str, ...]] = {
    "sse_state": ("strong", "weak"),
    "chinext_state": ("strong", "weak"),
    "index_relationship": ("aligned", "divergence"),
    "sector_state": ("mainline", "divergence", "retreat"),
    "open_type": ("high", "flat", "low"),
    "price_limit_context": ("near_limit",),
    "liquidity": ("low",),
    "data_state": ("normal", "interrupted"),
}


class ScenarioReplayError(ValueError):
    """历史事实缺失或可能引入未来函数。"""


@dataclass(frozen=True)
class ScenarioReplayFrameContext:
    """某根闭合 K 对应的、当时可见的非价格执行事实。"""

    market_permission: MarketPermission
    intraday_market_state: IntradayMarketState
    sector_state: SectorExecutionState
    required_data_fresh: bool = True
    quote_age_seconds: float = 0.0
    price_limit_locked: bool = False
    suspended: bool = False
    e7: E7ThreeDayFacts | None = None
    e7_support_deteriorating: bool = False
    cancelled_scenarios: frozenset[str] = frozenset()
    source_timestamps: tuple[datetime, ...] = ()
    fact_id: str | None = None


@dataclass(frozen=True)
class ScenarioReplayFactFrame:
    """生产事实快照的一个可复核帧。

    与仅有价格 K 线的历史回放不同，这里直接重放盘中当时已经
    落库的 ``ScenarioMarketFacts``，因此不会用收盘后数据重建当时的市场、
    板块或 E7 状态。
    """

    fact_id: str
    facts: ScenarioMarketFacts


def _aware_time(value: Any, *, field_name: str = "datetime") -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise ScenarioReplayError(f"无效 {field_name}：{value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed.astimezone(MARKET_TIMEZONE)


def _number(value: Any, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ScenarioReplayError(f"{field_name} 不是有效数字") from error
    if number <= 0:
        raise ScenarioReplayError(f"{field_name} 必须大于 0")
    return number


def _prepare_bars(bars: list[dict[str, Any]], *, source: str) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for index, raw in enumerate(bars or []):
        close = _number(raw.get("close"), field_name=f"{source}[{index}].close")
        open_price = _number(raw.get("open", close), field_name=f"{source}[{index}].open")
        high = _number(raw.get("high", max(open_price, close)), field_name=f"{source}[{index}].high")
        low = _number(raw.get("low", min(open_price, close)), field_name=f"{source}[{index}].low")
        if high < max(open_price, close) or low > min(open_price, close):
            raise ScenarioReplayError(f"{source}[{index}] OHLC 关系不合法")
        prepared.append({
            **raw,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "_time": _aware_time(raw.get("datetime"), field_name=f"{source}[{index}].datetime"),
        })
    prepared.sort(key=lambda item: item["_time"])
    if any(prepared[index]["_time"] == prepared[index - 1]["_time"] for index in range(1, len(prepared))):
        raise ScenarioReplayError(f"{source} 存在重复闭合时刻")
    return prepared


def _latest_at_or_before(bars: list[dict[str, Any]], moment: datetime) -> dict[str, Any] | None:
    latest = None
    for bar in bars:
        if bar["_time"] > moment:
            break
        latest = bar
    return latest


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _context_map(
    contexts: Mapping[str | datetime, ScenarioReplayFrameContext] | None,
) -> dict[datetime, ScenarioReplayFrameContext]:
    result: dict[datetime, ScenarioReplayFrameContext] = {}
    for key, context in (contexts or {}).items():
        moment = _aware_time(key, field_name="context timestamp")
        result[moment] = context
    return result


def _safe_missing_context() -> ScenarioReplayFrameContext:
    return ScenarioReplayFrameContext(
        market_permission=MarketPermission.EMPTY,
        intraday_market_state=IntradayMarketState.RANGE,
        sector_state=SectorExecutionState.UNAVAILABLE,
        required_data_fresh=False,
    )


def _bar_volume_ratio(bar: dict[str, Any]) -> float | None:
    value = bar.get("volume_ratio")
    if value in (None, ""):
        return None
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    return ratio if ratio >= 0 else None


def _vwap(bars: list[dict[str, Any]]) -> float:
    volumes = [max(0.0, float(item.get("volume") or 0)) for item in bars]
    volume_sum = sum(volumes)
    if volume_sum <= 0:
        return sum(float(item["close"]) for item in bars) / len(bars)
    amount_sum = sum(max(0.0, float(item.get("amount") or 0)) for item in bars)
    raw = amount_sum / volume_sum if amount_sum > 0 else 0.0
    day_low = min(float(item["low"]) for item in bars)
    day_high = max(float(item["high"]) for item in bars)
    if day_low * 0.5 <= raw <= day_high * 2:
        return raw
    return sum(float(item["close"]) * volume for item, volume in zip(bars, volumes)) / volume_sum


def _relative_strength(
    plan: ScenarioExecutionPlan,
    current_price: float,
    sse_bar: dict[str, Any] | None,
    sse_open: float,
    chinext_bar: dict[str, Any] | None,
    chinext_open: float,
) -> float | None:
    if sse_bar is None or chinext_bar is None:
        return None
    stock_return = current_price / plan.previous_close - 1.0
    index_return = (
        (float(sse_bar["close"]) / sse_open - 1.0)
        + (float(chinext_bar["close"]) / chinext_open - 1.0)
    ) / 2
    return stock_return - index_return


def _decision_signature(decision: dict[str, Any]) -> tuple[Any, ...]:
    action = decision.get("action") or {}
    primary = decision.get("primary_scenario") or {}
    return (
        action.get("action_type"),
        action.get("target_quantity"),
        action.get("change_quantity"),
        decision.get("execution_state"),
        primary.get("scenario_id"),
        primary.get("state"),
        decision.get("deadline"),
    )


def replay_full_scenario_session(
    *,
    session_id: str,
    plan: ScenarioExecutionPlan,
    stock_bars: list[dict[str, Any]],
    sse_bars: list[dict[str, Any]],
    chinext_bars: list[dict[str, Any]],
    frame_contexts: Mapping[str | datetime, ScenarioReplayFrameContext] | None = None,
    metadata: Mapping[str, Any] | None = None,
    playbook: FullScenarioPlaybook | None = None,
) -> dict[str, Any]:
    """逐根回放一个会话；缺失的历史执行事实只会阻断，不会被推断。"""

    if not str(session_id).strip():
        raise ScenarioReplayError("session_id 不能为空")
    stock = _prepare_bars(stock_bars, source="stock_bars")
    sse = _prepare_bars(sse_bars, source="sse_bars")
    chinext = _prepare_bars(chinext_bars, source="chinext_bars")
    if not stock:
        raise ScenarioReplayError("缺少有效个股 5 分钟 K")
    if not sse or not chinext:
        raise ScenarioReplayError("缺少上证或创业板 5 分钟 K")
    engine = playbook or FullScenarioPlaybook()
    contexts = _context_map(frame_contexts)
    sse_open = float(sse[0]["open"])
    chinext_open = float(chinext[0]["open"])
    accumulated: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    previous_signature: tuple[Any, ...] | None = None
    all_idempotent = True

    for raw_bar in stock:
        accumulated.append(raw_bar)
        moment = raw_bar["_time"]
        context = contexts.get(moment)
        context_complete = context is not None
        context = context or _safe_missing_context()
        sse_bar = _latest_at_or_before(sse, moment)
        chinext_bar = _latest_at_or_before(chinext, moment)
        indexes_complete = sse_bar is not None and chinext_bar is not None
        source_times = [moment]
        if sse_bar is not None:
            source_times.append(sse_bar["_time"])
        if chinext_bar is not None:
            source_times.append(chinext_bar["_time"])
        source_times.extend(_aware_time(value, field_name="context source timestamp") for value in context.source_timestamps)
        future_sources = [value for value in source_times if value > moment]
        if future_sources:
            raise ScenarioReplayError(
                f"{session_id} 在 {moment.isoformat()} 使用了未来事实 {max(future_sources).isoformat()}"
            )

        closed_bars = tuple(
            ClosedFiveMinuteBar(
                closed_at=item["_time"],
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume_ratio=_bar_volume_ratio(item),
            )
            for item in accumulated
        )
        vwap = _vwap(accumulated)
        facts = ScenarioMarketFacts(
            computed_at=moment,
            current_price=float(raw_bar["close"]),
            day_low=min(float(item["low"]) for item in accumulated),
            vwap=vwap,
            closed_bars=closed_bars,
            volume_ratio=_bar_volume_ratio(raw_bar),
            relative_strength=_relative_strength(
                plan,
                float(raw_bar["close"]),
                sse_bar,
                sse_open,
                chinext_bar,
                chinext_open,
            ),
            market_permission=context.market_permission,
            intraday_market_state=context.intraday_market_state,
            sector_state=context.sector_state,
            required_data_fresh=bool(context.required_data_fresh and indexes_complete),
            quote_age_seconds=context.quote_age_seconds,
            price_limit_locked=context.price_limit_locked,
            suspended=context.suspended,
            e7=context.e7,
            e7_support_deteriorating=context.e7_support_deteriorating,
            cancelled_scenarios=context.cancelled_scenarios,
        )
        decision = _jsonable(engine.evaluate(plan, facts))
        repeated_decision = _jsonable(engine.evaluate(plan, facts))
        frame_idempotent = decision == repeated_decision
        all_idempotent = all_idempotent and frame_idempotent
        frame = {
            "computed_at": moment.isoformat(timespec="seconds"),
            "bar_index": len(accumulated),
            "close": round(float(raw_bar["close"]), 4),
            "vwap": round(vwap, 4),
            "context_complete": context_complete and indexes_complete,
            "fact_id": context.fact_id,
            "required_data_fresh": facts.required_data_fresh,
            "quote_age_seconds": facts.quote_age_seconds,
            "max_source_timestamp": max(source_times).isoformat(timespec="seconds"),
            "lookahead_violation": False,
            "idempotent": frame_idempotent,
            "decision": decision,
        }
        frames.append(frame)
        signature = _decision_signature(decision)
        if signature != previous_signature:
            transitions.append(frame)
            previous_signature = signature

    digest_payload = {
        "session_id": session_id,
        "plan": plan,
        "metadata": dict(metadata or {}),
        "frames": frames,
    }
    return {
        "status": "ok",
        "session_id": session_id,
        "ticker": plan.ticker,
        "playbook_id": engine.playbook_id,
        "playbook_version": engine.version,
        "position_state": plan.position_state.value,
        "plan": _jsonable(plan),
        "metadata": _jsonable(dict(metadata or {})),
        "bar_count": len(frames),
        "transition_count": len(transitions),
        "context_complete_count": sum(bool(item["context_complete"]) for item in frames),
        "lookahead_protection": "每一步只使用当前及此前已闭合 K 与显式历史事实快照",
        "idempotent": all_idempotent,
        "input_digest": _stable_digest({
            "session_id": session_id,
            "plan": plan,
            "stock_bars": stock_bars,
            "sse_bars": sse_bars,
            "chinext_bars": chinext_bars,
            "contexts": contexts,
            "metadata": dict(metadata or {}),
        }),
        "output_digest": _stable_digest(digest_payload),
        "transitions": transitions,
        "frames": frames,
    }


def replay_persisted_scenario_session(
    *,
    session_id: str,
    plan: ScenarioExecutionPlan,
    fact_frames: list[ScenarioReplayFactFrame],
    metadata: Mapping[str, Any] | None = None,
    playbook: FullScenarioPlaybook | None = None,
) -> dict[str, Any]:
    """逐帧重放生产期间已持久化的全场景事实。

    每帧使用原始 ``computed_at`` 和原始来源时间；任何来源晚于
    计算时刻都直接拒绝。这是 Job 12I 生产快照到 Job 12H 正式回放的
    安全桥接，不使用事后日线补造当时事实。
    """

    if not str(session_id).strip():
        raise ScenarioReplayError("session_id 不能为空")
    if not fact_frames:
        raise ScenarioReplayError("缺少生产事实快照")
    ordered = sorted(fact_frames, key=lambda item: item.facts.computed_at)
    moments = [item.facts.computed_at for item in ordered]
    if len(set(moments)) != len(moments):
        raise ScenarioReplayError(f"{session_id} 存在重复事实时刻")

    engine = playbook or FullScenarioPlaybook()
    frames: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    previous_signature: tuple[Any, ...] | None = None
    all_idempotent = True

    for item in ordered:
        facts = item.facts
        moment = facts.computed_at
        source_times = list(facts.source_timestamps)
        future_sources = [value for value in source_times if value > moment]
        if future_sources:
            raise ScenarioReplayError(
                f"{session_id} 在 {moment.isoformat()} 使用了未来事实 "
                f"{max(future_sources).isoformat()}"
            )
        decision = _jsonable(engine.evaluate(plan, facts))
        repeated_decision = _jsonable(engine.evaluate(plan, facts))
        frame_idempotent = decision == repeated_decision
        all_idempotent = all_idempotent and frame_idempotent
        frame = {
            "computed_at": moment.isoformat(timespec="seconds"),
            "bar_index": len(facts.closed_bars),
            "close": round(float(facts.current_price), 4),
            "vwap": round(float(facts.vwap), 4),
            "context_complete": bool(source_times),
            "fact_id": item.fact_id,
            "required_data_fresh": facts.required_data_fresh,
            "freshness_failed_sources": list(facts.freshness_failed_sources),
            "quote_age_seconds": facts.quote_age_seconds,
            "max_source_timestamp": (
                max(source_times).isoformat(timespec="seconds")
                if source_times else moment.isoformat(timespec="seconds")
            ),
            "lookahead_violation": False,
            "idempotent": frame_idempotent,
            "decision": decision,
        }
        frames.append(frame)
        signature = _decision_signature(decision)
        if signature != previous_signature:
            transitions.append(frame)
            previous_signature = signature

    digest_payload = {
        "session_id": session_id,
        "plan": plan,
        "metadata": dict(metadata or {}),
        "frames": frames,
    }
    return {
        "status": "ok",
        "session_id": session_id,
        "ticker": plan.ticker,
        "playbook_id": engine.playbook_id,
        "playbook_version": engine.version,
        "position_state": plan.position_state.value,
        "plan": _jsonable(plan),
        "metadata": _jsonable(dict(metadata or {})),
        "bar_count": max((len(item.facts.closed_bars) for item in ordered), default=0),
        "fact_frame_count": len(frames),
        "transition_count": len(transitions),
        "context_complete_count": sum(bool(item["context_complete"]) for item in frames),
        "lookahead_protection": "逐帧重放实时落库事实，来源时间不得晚于计算时刻",
        "idempotent": all_idempotent,
        "input_digest": _stable_digest({
            "session_id": session_id,
            "plan": plan,
            "fact_frames": fact_frames,
            "metadata": dict(metadata or {}),
        }),
        "output_digest": _stable_digest(digest_payload),
        "transitions": transitions,
        "frames": frames,
    }


def _gate(gate_id: str, label: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"gate_id": gate_id, "label": label, "passed": bool(passed), "detail": detail}


def _frame_action(frame: dict[str, Any]) -> str:
    return str(((frame.get("decision") or {}).get("action") or {}).get("action_type") or "")


def _frame_primary(frame: dict[str, Any]) -> str:
    return str(((frame.get("decision") or {}).get("primary_scenario") or {}).get("scenario_id") or "")


def evaluate_full_scenario_launch_gate(
    replays: list[dict[str, Any]],
    *,
    min_sessions_per_scenario: int = 20,
) -> dict[str, Any]:
    """汇总 Job 12H 的安全性、样本与覆盖门禁。"""

    if min_sessions_per_scenario <= 0:
        raise ScenarioReplayError("每场景最低会话数必须大于 0")
    scenario_sessions: dict[str, set[str]] = {code: set() for code in SCENARIO_CODES}
    coverage: dict[str, set[str]] = {dimension: set() for dimension in REQUIRED_REPLAY_COVERAGE}
    lookahead_ok = True
    idempotence_ok = True
    hard_stop_ok = True
    stale_entry_ok = True
    time_rules_ok = True
    explainable_ok = True
    violations: list[dict[str, Any]] = []

    loaded_replay_count = len(replays)
    excluded_replays = [item for item in replays if item.get("audit_eligible") is False]
    replays = [item for item in replays if item.get("audit_eligible") is not False]

    for replay in replays:
        session_id = str(replay.get("session_id") or "unknown")
        metadata = replay.get("metadata") or {}
        for dimension in coverage:
            value = metadata.get(dimension)
            if value not in (None, ""):
                coverage[dimension].add(str(value))
        idempotence_ok = idempotence_ok and bool(replay.get("idempotent"))
        plan = replay.get("plan") or {}
        positioned = replay.get("position_state") == PositionState.POSITIONED.value
        hard_stop = float(plan.get("hard_stop") or 0)
        for frame in replay.get("frames") or []:
            moment = _aware_time(frame.get("computed_at"), field_name="replay frame time")
            source_time = _aware_time(frame.get("max_source_timestamp"), field_name="source time")
            if frame.get("lookahead_violation") or source_time > moment:
                lookahead_ok = False
                violations.append({"gate_id": "no_lookahead", "session_id": session_id, "at": moment.isoformat()})
            idempotence_ok = idempotence_ok and bool(frame.get("idempotent"))
            decision = frame.get("decision") or {}
            for candidate in decision.get("candidate_scenarios") or []:
                code = str(candidate.get("scenario_id") or "")
                if code in scenario_sessions and candidate.get("state") == ScenarioState.TRIGGERED.value:
                    scenario_sessions[code].add(session_id)

            action = _frame_action(frame)
            price = float(frame.get("close") or 0)
            quote_age = float(frame.get("quote_age_seconds") or 0)
            if positioned and hard_stop > 0 and price <= hard_stop:
                expected = (
                    ActionType.RISK_EXIT.value
                    if quote_age <= 45
                    else ActionType.MANUAL_VERIFY_RISK.value
                )
                if action != expected:
                    hard_stop_ok = False
                    violations.append({"gate_id": "hard_stop", "session_id": session_id, "at": moment.isoformat(), "action": action})
            if not frame.get("required_data_fresh") and action == ActionType.PREPARE_ENTRY.value:
                stale_entry_ok = False
                violations.append({"gate_id": "stale_no_entry", "session_id": session_id, "at": moment.isoformat()})

            clock = moment.time().replace(tzinfo=None)
            primary = _frame_primary(frame)
            invalid_time = False
            if action == ActionType.PREPARE_ENTRY.value:
                invalid_time = not (time(14, 0) <= clock < time(15, 0))
            elif action == ActionType.PREPARE_REDUCE.value and primary in {"H3", "R3"}:
                invalid_time = clock > time(14, 45)
            elif action == ActionType.PREPARE_REDUCE.value and primary in {"R1", "R2", "N2"}:
                invalid_time = clock > time(14, 55)
            if invalid_time:
                time_rules_ok = False
                violations.append({"gate_id": "time_rules", "session_id": session_id, "at": moment.isoformat(), "scenario": primary, "action": action})

        for transition in replay.get("transitions") or []:
            decision = transition.get("decision") or {}
            candidates = decision.get("candidate_scenarios") or []
            reasons = decision.get("reasons") or []
            traces_present = bool(candidates) and all(
                candidate.get("traces") for candidate in candidates
            )
            if not reasons or not traces_present:
                explainable_ok = False
                violations.append({"gate_id": "human_explainable", "session_id": session_id, "at": transition.get("computed_at")})

    scenario_counts = {code: len(sessions) for code, sessions in scenario_sessions.items()}
    missing_scenarios = {
        code: min_sessions_per_scenario - count
        for code, count in scenario_counts.items()
        if count < min_sessions_per_scenario
    }
    coverage_result = {
        dimension: {
            "observed": sorted(values),
            "required": list(REQUIRED_REPLAY_COVERAGE[dimension]),
            "missing": sorted(set(REQUIRED_REPLAY_COVERAGE[dimension]) - values),
        }
        for dimension, values in coverage.items()
    }
    sample_ok = not missing_scenarios
    coverage_ok = all(not item["missing"] for item in coverage_result.values())
    replay_exists = bool(replays)
    gates = [
        _gate("replay_exists", "存在可复核真实会话", replay_exists, f"已载入 {len(replays)} 个回放会话"),
        _gate("no_lookahead", "无未来函数", lookahead_ok and replay_exists, "来源时间不得晚于计算时刻"),
        _gate("reproducible", "关键位与动作可复算", idempotence_ok and replay_exists, "同一事实重复求值必须一致"),
        _gate("hard_stop", "硬止损无漏触发", hard_stop_ok and replay_exists, "新鲜报价清仓，过期报价人工核价"),
        _gate("snapshot_idempotent", "同一快照幂等", idempotence_ok and replay_exists, "帧与会话摘要稳定"),
        _gate("stale_no_entry", "过期数据不建仓", stale_entry_ok and replay_exists, "数据阻断帧不得输出可建仓"),
        _gate("time_rules", "买卖时间无旁路", time_rules_ok and replay_exists, "14:00 建仓与卖出截止均按规则执行"),
        _gate("human_explainable", "状态变化可解释", explainable_ok and replay_exists, "每次变化包含原因与规则轨迹"),
        _gate("scenario_samples", "每场景至少二十会话", sample_ok, f"缺口 {missing_scenarios}" if missing_scenarios else "样本达标"),
        _gate("coverage", "极端与分化覆盖", coverage_ok, "覆盖维度完整" if coverage_ok else "仍有覆盖缺口"),
    ]
    ready = all(item["passed"] for item in gates)
    blockers = [item["label"] for item in gates if not item["passed"]]
    return {
        "job": "12H",
        "ready_for_launch": ready,
        "decision": "launch" if ready else "hold_launch",
        "minimum_sessions_per_scenario": min_sessions_per_scenario,
        "replay_session_count": len(replays),
        "loaded_replay_count": loaded_replay_count,
        "audit_excluded_replay_count": len(excluded_replays),
        "scenario_session_counts": scenario_counts,
        "scenario_sample_gaps": missing_scenarios,
        "coverage": coverage_result,
        "gates": gates,
        "violations": violations[:100],
        "blockers": blockers,
    }
