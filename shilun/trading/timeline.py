"""Decision Timeline 的指纹、幂等写入和查询。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime
from enum import Enum
from typing import Any

from shilun.trading.contracts import DecisionResult, canonical_action_type


COLLECTION_NAME = "trading_decision_events"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def decision_payload(result: DecisionResult) -> dict[str, Any]:
    return _jsonable(asdict(result))


def decision_fingerprint(result: DecisionResult) -> str:
    """只编码决策语义，不把每次价格跳动当作新的 Timeline 事件。"""

    scenario = result.current_scenario
    semantic = {
        "data_state": result.data_freshness.state.value,
        "can_generate_action": result.data_freshness.can_generate_action,
        "action": None if result.action is None else {
            "type": canonical_action_type(result.action.action_type).value,
            "target_quantity": result.action.target_quantity,
            "change_quantity": result.action.change_quantity,
            "execution_zone": result.action.execution_zone,
            "intent": result.action_intent.value if result.action_intent else None,
        },
        "position_state": result.position_state.value if result.position_state else None,
        "execution_state": result.execution_state.value if result.execution_state else None,
        "plan_id": result.plan_id,
        "trade_date": result.trade_date,
        "plan_revision": result.plan_revision,
        "position_revision": result.position_revision,
        "presentation": None if result.presentation is None else {
            "state_label": result.presentation.state_label,
            "headline": result.presentation.headline,
            "instruction": result.presentation.instruction,
            "executable": result.presentation.executable,
            "deadline": result.presentation.deadline,
            "evidence_progress": result.presentation.evidence_progress,
        },
        "scenario": None if scenario is None else {
            "id": scenario.scenario_id,
            "state": scenario.state.value,
            "match_level": scenario.match_level.value,
            "rules": [
                {"id": trace.rule_id, "state": trace.state.value}
                for trace in scenario.traces
            ],
        },
        "switch_conditions": result.switch_conditions,
    }
    encoded = json.dumps(_jsonable(semantic), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _summary(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    action = payload.get("action") or {}
    scenario = payload.get("current_scenario") or {}
    return {
        "action_type": action.get("action_type"),
        "canonical_action_type": (
            canonical_action_type(action.get("action_type")).value
            if action.get("action_type")
            else None
        ),
        "action_label": action.get("label"),
        "scenario_id": scenario.get("scenario_id"),
        "scenario_title": scenario.get("title"),
        "scenario_state": scenario.get("state"),
        "data_state": (payload.get("data_freshness") or {}).get("state"),
        "position_state": payload.get("position_state"),
        "execution_state": payload.get("execution_state"),
        "headline": (payload.get("presentation") or {}).get("headline"),
    }


def _namespace(
    result: DecisionResult,
    *,
    playbook_id: str,
    plan_id: str | None,
    trade_date: str | date | None,
    plan_revision: int | None,
    position_revision: int | None,
) -> tuple[str, str, int, int]:
    resolved_trade_date = result.trade_date or trade_date or result.computed_at.date()
    trade_date_value = (
        resolved_trade_date.isoformat()
        if isinstance(resolved_trade_date, date)
        else str(resolved_trade_date)
    )
    resolved_plan_id = result.plan_id or plan_id or (
        f"{playbook_id}:{result.ticker}:{trade_date_value}"
    )
    resolved_plan_revision = (
        result.plan_revision if result.plan_revision else (plan_revision or 0)
    )
    resolved_position_revision = (
        result.position_revision if result.position_revision else (position_revision or 0)
    )
    if resolved_plan_revision < 0 or resolved_position_revision < 0:
        raise ValueError("plan_revision 和 position_revision 不能为负数")
    return (
        resolved_plan_id,
        trade_date_value,
        resolved_plan_revision,
        resolved_position_revision,
    )


def persist_decision_if_changed(
    collection: Any,
    result: DecisionResult,
    *,
    playbook_id: str,
    playbook_version: str,
    facts_snapshot: dict[str, Any] | None = None,
    plan_id: str | None = None,
    trade_date: str | date | None = None,
    plan_revision: int | None = None,
    position_revision: int | None = None,
) -> tuple[bool, dict[str, Any]]:
    (
        resolved_plan_id,
        resolved_trade_date,
        resolved_plan_revision,
        resolved_position_revision,
    ) = _namespace(
        result,
        playbook_id=playbook_id,
        plan_id=plan_id,
        trade_date=trade_date,
        plan_revision=plan_revision,
        position_revision=position_revision,
    )
    fingerprint = decision_fingerprint(result)
    previous = collection.find_one(
        {
            "ticker": result.ticker,
            "trade_date": resolved_trade_date,
            "plan_id": resolved_plan_id,
            "plan_revision": resolved_plan_revision,
        },
        {"_id": 0},
        sort=[("computed_at", -1)],
    )
    previous_position_revision = int((previous or {}).get("position_revision") or 0)
    if previous and resolved_position_revision < previous_position_revision:
        raise ValueError(
            "position_revision 不能倒退："
            f"{resolved_position_revision} < {previous_position_revision}"
        )
    if (
        previous
        and previous.get("fingerprint") == fingerprint
        and previous_position_revision == resolved_position_revision
    ):
        return False, previous

    payload = decision_payload(result)
    computed_at = result.computed_at.isoformat(timespec="seconds")
    event_key = "|".join((
        result.ticker,
        resolved_trade_date,
        resolved_plan_id,
        str(resolved_plan_revision),
        str(resolved_position_revision),
        computed_at,
        fingerprint,
    ))
    event_id = hashlib.sha256(event_key.encode("utf-8")).hexdigest()
    document = {
        "event_id": event_id,
        "ticker": result.ticker,
        "trade_date": resolved_trade_date,
        "plan_id": resolved_plan_id,
        "plan_revision": resolved_plan_revision,
        "position_revision": resolved_position_revision,
        "computed_at": computed_at,
        "fingerprint": fingerprint,
        "playbook_id": playbook_id,
        "playbook_version": playbook_version,
        "previous": _summary((previous or {}).get("decision")),
        "current": _summary(payload),
        "reasons": list(result.reasons),
        "switch_conditions": list(result.switch_conditions),
        "rule_trace": (
            []
            if result.current_scenario is None
            else _jsonable([asdict(trace) for trace in result.current_scenario.traces])
        ),
        "facts_snapshot": _jsonable(facts_snapshot or {}),
        "decision": payload,
    }
    collection.update_one(
        {"event_id": event_id},
        {"$setOnInsert": document},
        upsert=True,
    )
    stored = collection.find_one({"event_id": event_id}, {"_id": 0}) or document
    return True, stored


def list_decision_events(
    collection: Any,
    ticker: str,
    *,
    limit: int = 100,
    trade_date: str | date | None = None,
    plan_id: str | None = None,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"ticker": ticker}
    if trade_date is not None:
        query["trade_date"] = trade_date.isoformat() if isinstance(trade_date, date) else str(trade_date)
    if plan_id is not None:
        query["plan_id"] = plan_id
    cursor = collection.find(query, {"_id": 0}).sort("computed_at", -1).limit(limit)
    return list(cursor)
