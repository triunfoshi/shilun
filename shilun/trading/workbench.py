"""Job 12G 执行工作台读模型与同事件通知路由。

页面只消费这里生成的中文执行事件；飞书 Outbox 也保存同一事件对象。
本模块不计算交易规则，不把页面读取变成决策写入。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from shilun.trading.contracts import ACTION_DEFINITIONS, ActionType, canonical_action_type, execution_state_view
from shilun.trading.order_lifecycle import ACTIVE_ORDER_STATUSES
from shilun.trading.market_facts import execution_scope_tickers, timeline_scope_tickers


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
OUTBOX_COLLECTION = "trading_notification_outbox"

_ACTION_PRIORITY = {
    ActionType.RISK_EXIT.value: "P0",
    ActionType.MANUAL_VERIFY_RISK.value: "P0",
    ActionType.CONFIRM_POSITION_FACTS.value: "P0",
    ActionType.PREPARE_REDUCE.value: "P1",
    ActionType.PROTECT_PROFIT.value: "P1",
    ActionType.RECORD_PARTIAL_FILL.value: "P1",
    ActionType.AWAIT_FILL.value: "P1",
    ActionType.PREPARE_ENTRY.value: "P2",
    ActionType.WAIT_CONFIRMATION.value: "P3",
    ActionType.WATCH_TRIGGER.value: "P4",
    ActionType.OBSERVE_SETUP.value: "P5",
    ActionType.HOLD.value: "P5",
    ActionType.NO_ACTION.value: "P5",
}
_NOTIFIABLE_ACTIONS = {
    ActionType.RISK_EXIT.value,
    ActionType.MANUAL_VERIFY_RISK.value,
    ActionType.CONFIRM_POSITION_FACTS.value,
    ActionType.PREPARE_REDUCE.value,
    ActionType.PREPARE_ENTRY.value,
    ActionType.RECORD_PARTIAL_FILL.value,
}
_NOTIFIABLE_STATES = {
    "entry_ready",
    "entry_pending",
    "reduce_pending",
    "exit_pending",
    "expired",
    "cancelled",
    "data_blocked",
}


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(MARKET_TIMEZONE).isoformat(timespec="seconds")
    return str(value or "")


def _documents(collection: Any, query: dict[str, Any] | None = None,
               projection: dict[str, int] | None = None) -> list[dict[str, Any]]:
    return [dict(item) for item in collection.find(query or {}, projection or {"_id": 0})]


DECISION_DISPLAY_PROJECTION = {
    "_id": 0,
    **{key: 1 for key in ("event_id", "ticker", "name", "computed_at", "occurred_at",
                          "fingerprint", "current", "previous", "reasons")},
    **{f"decision.{key}": 1 for key in (
        "presentation", "position_state", "action", "execution_state", "reasons", "switch_conditions",
        "current_scenario.scenario_id", "current_scenario.title", "current_scenario.state",
    )},
}
CANDIDATE_DISPLAY_PROJECTION = {
    "_id": 0,
    **{key: 1 for key in ("event_id", "ticker", "name", "sector_name", "computed_at", "fingerprint",
                          "current", "previous", "reasons", "notification_eligible")},
}


def _event_name_map(store: Any) -> dict[str, str]:
    names: dict[str, str] = {}
    for collection_name in ("stock_basic", "holdings", "trade_orders", "intraday_watchlist"):
        try:
            rows = _documents(store.collection(collection_name),
                              projection={"_id": 0, "ticker": 1, "ts_code": 1, "name": 1})
        except Exception:  # noqa: BLE001 - 缺集合不阻断执行工作台
            continue
        for row in rows:
            ticker = str(row.get("ticker") or row.get("ts_code") or "").upper()
            name = str(row.get("name") or "").strip()
            if ticker and name and name.upper() != ticker:
                names[ticker] = name
    return names


def _event_id(namespace: str, *parts: Any) -> str:
    raw = "|".join([namespace, *(_iso(item) for item in parts)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _canonical_action(value: Any, *, position_state: str = "unknown") -> str:
    normalized = str(value or "").strip().lower()
    # 旧候选事件把空仓积极信号叫 hold_observe；12G 必须消除“空仓持有”。
    if normalized == "hold_observe" and position_state == "flat":
        return ActionType.WAIT_CONFIRMATION.value
    if normalized == "wait":
        return ActionType.OBSERVE_SETUP.value
    try:
        return canonical_action_type(normalized).value
    except (ValueError, TypeError):
        return ActionType.NO_ACTION.value


def _action_label(action_type: str) -> str:
    try:
        return ACTION_DEFINITIONS[ActionType(action_type)].label
    except (KeyError, ValueError):
        return "无新动作"


def _state_label(execution_state: str, fallback: str) -> str:
    try:
        return execution_state_view(execution_state).label
    except (ValueError, TypeError):
        return fallback


def _presentation(
    *,
    action_type: str,
    execution_state: str,
    headline: str | None,
    reason: str | None,
    next_condition: str | None,
    target_quantity: Any = None,
    change_quantity: Any = None,
    data_as_of: Any = None,
) -> dict[str, Any]:
    definition = ACTION_DEFINITIONS.get(ActionType(action_type), ACTION_DEFINITIONS[ActionType.NO_ACTION])
    return {
        "state_label": _state_label(execution_state, "执行状态待确认"),
        "headline": str(headline or definition.label),
        "instruction": {
            ActionType.RISK_EXIT.value: "立即处理，不等待普通确认",
            ActionType.MANUAL_VERIFY_RISK.value: "先核验事实，再继续执行",
            ActionType.CONFIRM_POSITION_FACTS.value: "先核对委托、现金与持仓",
            ActionType.PREPARE_ENTRY.value: "按计划价格与数量执行",
            ActionType.PREPARE_REDUCE.value: "按计划减仓",
            ActionType.AWAIT_FILL.value: "等待成交或主动撤单",
        }.get(action_type, _state_label(execution_state, "继续观察")),
        "reason": str(reason or "执行事实发生变化"),
        "next_condition": str(next_condition or "等待下一条可核验执行事实"),
        "urgency": definition.urgency,
        "executable": bool(definition.executable),
        "target_quantity": target_quantity,
        "change_quantity": change_quantity,
        "data_as_of": _iso(data_as_of) or None,
    }


def normalize_decision_event(event: dict[str, Any]) -> dict[str, Any]:
    current = dict(event.get("current") or {})
    decision = dict(event.get("decision") or {})
    raw_presentation = dict(decision.get("presentation") or {})
    position_state = str(current.get("position_state") or decision.get("position_state") or "unknown")
    action = dict(decision.get("action") or {})
    action_type = _canonical_action(
        current.get("canonical_action_type") or current.get("action_type") or action.get("action_type"),
        position_state=position_state,
    )
    execution_state = str(current.get("execution_state") or decision.get("execution_state") or "positioned")
    reasons = [str(item) for item in event.get("reasons") or decision.get("reasons") or [] if item]
    next_conditions = [str(item) for item in decision.get("switch_conditions") or [] if item]
    presentation = _presentation(
        action_type=action_type,
        execution_state=execution_state,
        headline=raw_presentation.get("headline") or current.get("headline") or current.get("action_label"),
        reason=raw_presentation.get("reason") or (reasons[0] if reasons else None),
        next_condition=raw_presentation.get("next_condition") or (next_conditions[0] if next_conditions else None),
        target_quantity=raw_presentation.get("target_quantity", action.get("target_quantity")),
        change_quantity=raw_presentation.get("change_quantity", action.get("change_quantity")),
        data_as_of=raw_presentation.get("data_as_of") or event.get("computed_at"),
    )
    occurred_at = _iso(event.get("computed_at") or event.get("occurred_at"))
    event_id = str(event.get("event_id") or _event_id("decision", event.get("ticker"), occurred_at, event.get("fingerprint")))
    scenario = dict(decision.get("current_scenario") or {})
    return {
        "event_id": event_id,
        "source_kind": "decision",
        "source_event_id": event_id,
        "ticker": str(event.get("ticker") or "").upper(),
        "name": event.get("name"),
        "occurred_at": occurred_at,
        "position_state": position_state,
        "execution_state": execution_state,
        "action_type": action_type,
        "action_name_cn": presentation["headline"],
        "priority": _ACTION_PRIORITY.get(action_type, "P5"),
        "presentation": presentation,
        "scenario": {
            "scenario_id": current.get("scenario_id") or scenario.get("scenario_id"),
            "scenario_name_cn": current.get("scenario_title") or scenario.get("title") or "当前剧本待确认",
            "state": current.get("scenario_state") or scenario.get("state"),
        },
        "previous_action_name_cn": (event.get("previous") or {}).get("headline") or (event.get("previous") or {}).get("action_label"),
        "notification_eligible": bool(
            event.get("previous")
            and (action_type in _NOTIFIABLE_ACTIONS or execution_state in _NOTIFIABLE_STATES)
        ),
    }


def normalize_candidate_event(event: dict[str, Any]) -> dict[str, Any]:
    current = dict(event.get("current") or {})
    raw_action = str(current.get("action_type") or "")
    action_type = _canonical_action(raw_action, position_state="flat")
    if action_type == ActionType.RISK_EXIT.value:
        action_type = ActionType.MANUAL_VERIFY_RISK.value
    execution_state = {
        ActionType.MANUAL_VERIFY_RISK.value: "data_blocked",
        ActionType.PREPARE_ENTRY.value: "entry_ready",
        ActionType.WAIT_CONFIRMATION.value: "confirming",
        ActionType.WATCH_TRIGGER.value: "armed",
    }.get(action_type, "selected")
    reasons = [str(item) for item in event.get("reasons") or current.get("reasons") or [] if item]
    switch = [str(item) for item in current.get("switch_conditions") or [] if item]
    presentation = _presentation(
        action_type=action_type,
        execution_state=execution_state,
        headline=None,
        reason=reasons[0] if reasons else None,
        next_condition=switch[0] if switch else "等待个股确认条件形成",
        data_as_of=current.get("quote_at") or event.get("computed_at"),
    )
    event_id = str(event.get("event_id") or _event_id("candidate", event.get("ticker"), event.get("computed_at"), event.get("fingerprint")))
    return {
        "event_id": event_id,
        "source_kind": "candidate",
        "source_event_id": event_id,
        "ticker": str(event.get("ticker") or "").upper(),
        "name": event.get("name"),
        "sector_name": event.get("sector_name"),
        "occurred_at": _iso(event.get("computed_at")),
        "position_state": "flat",
        "execution_state": execution_state,
        "action_type": action_type,
        "action_name_cn": presentation["headline"],
        "priority": _ACTION_PRIORITY.get(action_type, "P5"),
        "presentation": presentation,
        "scenario": {
            "scenario_id": current.get("scenario_id"),
            "scenario_name_cn": current.get("scenario_title") or "候选观察",
            "state": execution_state,
        },
        "market": {
            "current_price": current.get("current_price"),
            "pct_chg": current.get("pct_chg"),
            "market_gate_label": current.get("market_gate_label"),
        },
        "previous_action_name_cn": (event.get("previous") or {}).get("action_label"),
        "notification_eligible": bool(event.get("notification_eligible")),
    }


def normalize_order_event(event: dict[str, Any], orders: dict[str, dict[str, Any]]) -> dict[str, Any]:
    order = dict(orders.get(str(event.get("order_id"))) or {})
    event_type = str(event.get("event_type") or "")
    side = str(order.get("side") or "")
    intent = str(order.get("intent") or "")
    status = str(event.get("status") or order.get("status") or "")
    details = dict(event.get("details") or {})
    if event_type == "order_expired":
        action_type, headline, execution_state = ActionType.CANCEL_ENTRY.value, "委托已到期并释放", "expired"
    elif event_type == "order_cancelled":
        action_type, headline, execution_state = ActionType.CANCEL_ENTRY.value, "委托已撤销并释放", "cancelled"
    elif event_type == "fill_recorded" and int(details.get("remaining_quantity") or order.get("remaining_quantity") or 0) > 0:
        action_type, headline = ActionType.RECORD_PARTIAL_FILL.value, "已记录部分成交"
        execution_state = str(event.get("execution_state") or order.get("execution_state") or "entry_pending")
    elif event_type == "fill_recorded":
        action_type, headline = ActionType.NO_ACTION.value, "委托已全部成交"
        execution_state = str(event.get("execution_state") or order.get("execution_state") or "positioned")
    else:
        action_type, headline = ActionType.AWAIT_FILL.value, "等待买入成交" if side == "buy" else "等待卖出成交"
        execution_state = str(event.get("execution_state") or order.get("execution_state") or "entry_pending")
    reason = {
        "order_placed": "已记录券商侧委托，真实持仓只随成交变化",
        "fill_recorded": "已回写券商侧成交事实",
        "order_cancelled": "未成交部分已撤销，冻结资源已释放",
        "order_expired": "委托超过有效期，未成交部分已自动撤销并释放",
    }.get(event_type, "委托事实发生变化")
    next_condition = {
        "order_placed": "等待成交回写；若不再执行则撤单",
        "fill_recorded": "继续核对剩余数量与持仓版本",
        "order_cancelled": "如需执行，重新生成并记录新委托",
        "order_expired": "核对释放结果；如需执行，创建新委托",
    }.get(event_type, "核对委托事实")
    presentation = _presentation(
        action_type=action_type,
        execution_state=execution_state,
        headline=headline,
        reason=reason,
        next_condition=next_condition,
        data_as_of=event.get("occurred_at"),
    )
    occurred_at = _iso(event.get("occurred_at"))
    event_id = _event_id("order", event.get("order_id"), event.get("order_revision"), event_type)
    notification_eligible = bool(
        event_type in {"order_cancelled", "order_expired", "fill_recorded"}
        or (event_type == "order_placed" and side == "sell" and intent in {"reduce", "exit"})
    )
    return {
        "event_id": event_id,
        "source_kind": "order",
        "source_event_id": event_id,
        "ticker": str(event.get("ticker") or order.get("ticker") or "").upper(),
        "name": order.get("name"),
        "occurred_at": occurred_at,
        "position_state": "positioned" if side == "sell" or int(order.get("filled_quantity") or 0) > 0 else "flat",
        "execution_state": execution_state,
        "action_type": action_type,
        "action_name_cn": headline,
        "priority": "P1" if side == "sell" else "P2",
        "presentation": presentation,
        "scenario": {"scenario_id": order.get("scenario_id"), "scenario_name_cn": "委托成交闭环", "state": status},
        "order": {
            "order_id": event.get("order_id"),
            "side": side,
            "intent": intent,
            "status": status,
            "remaining_quantity": details.get("remaining_quantity", order.get("remaining_quantity")),
        },
        "previous_action_name_cn": None,
        "notification_eligible": notification_eligible,
    }


def _latest_by_ticker(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in sorted(events, key=lambda item: item.get("occurred_at") or "", reverse=True):
        ticker = str(event.get("ticker") or "")
        if ticker and ticker not in latest:
            latest[ticker] = event
    return latest


def _audit_orders(
    active_orders: list[dict[str, Any]],
    holdings: dict[str, dict[str, Any]],
    reservations: dict[str, dict[str, Any]],
    executions: list[dict[str, Any]],
    now_text: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    executions_by_order: dict[str, int] = {}
    for execution in executions:
        key = str(execution.get("order_id") or "")
        executions_by_order[key] = executions_by_order.get(key, 0) + int(execution.get("quantity") or 0)
    active_sell_by_ticker: dict[str, int] = {}
    for order in active_orders:
        order_id = str(order.get("order_id") or "")
        ticker = str(order.get("ticker") or "").upper()
        remaining = int(order.get("remaining_quantity") or 0)
        recorded_fills = executions_by_order.get(order_id, 0)
        if recorded_fills != int(order.get("filled_quantity") or 0):
            issues.append({"issue_id": f"fill:{order_id}", "ticker": ticker, "priority": "P0", "headline": "核验成交累计", "reason": f"委托累计成交 {order.get('filled_quantity') or 0} 股，成交明细合计 {recorded_fills} 股", "next_condition": "核对券商成交明细与执行事件后再继续"})
        if order.get("side") == "buy":
            reservation = reservations.get(str(order.get("plan_id") or ""))
            expected = round(remaining * float(order.get("limit_price") or 0), 2)
            actual = round(float((reservation or {}).get("reserved_cash") or 0), 2)
            if not reservation or reservation.get("status") != "active" or abs(expected - actual) > 0.01:
                issues.append({"issue_id": f"cash:{order_id}", "ticker": ticker, "priority": "P0", "headline": "核验现金预留", "reason": f"活动买单应预留 ¥{expected:.2f}，当前记录 ¥{actual:.2f}", "next_condition": "核对现金预留与委托剩余后再建新单"})
        else:
            active_sell_by_ticker[ticker] = active_sell_by_ticker.get(ticker, 0) + remaining
    for ticker, expected_frozen in active_sell_by_ticker.items():
        holding = holdings.get(ticker)
        actual_frozen = int((holding or {}).get("frozen_quantity") or 0)
        if holding is None or actual_frozen < expected_frozen:
            issues.append({"issue_id": f"frozen:{ticker}", "ticker": ticker, "priority": "P0", "headline": "核验卖出冻结", "reason": f"活动卖单剩余 {expected_frozen} 股，持仓仅冻结 {actual_frozen} 股", "next_condition": "核对券商委托与真实可卖数量后再处理"})
    for issue in issues:
        issue.update({
            "event_id": _event_id("audit", issue["issue_id"], now_text[:10]),
            "source_kind": "audit",
            "occurred_at": now_text,
            "position_state": "unknown",
            "execution_state": "data_blocked",
            "action_type": ActionType.CONFIRM_POSITION_FACTS.value,
            "action_name_cn": issue["headline"],
            "presentation": _presentation(
                action_type=ActionType.CONFIRM_POSITION_FACTS.value,
                execution_state="data_blocked",
                headline=issue["headline"],
                reason=issue["reason"],
                next_condition=issue["next_condition"],
                data_as_of=now_text,
            ),
            "notification_eligible": False,
        })
    return issues


class ExecutionWorkbenchService:
    """把既有决策、候选、委托和持仓事实投影成一个只读执行工作台。"""

    def __init__(self, store: Any, *, now_factory: Any | None = None) -> None:
        self.store = store
        self.now_factory = now_factory or (lambda: datetime.now(tz=MARKET_TIMEZONE))

    def events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        now = self.now_factory().astimezone(MARKET_TIMEZONE)
        allowed_tickers = set(execution_scope_tickers(self.store, now.strftime("%Y%m%d")))
        orders = {str(item.get("order_id")): item for item in _documents(self.store.collection("trade_orders"))}
        # Keep archived evidence for drill-down/replay, not every workbench read.
        scope = {"ticker": {"$in": sorted(allowed_tickers)}}
        events = [normalize_decision_event(item) for item in _documents(
            self.store.collection("trading_decision_events"), scope, DECISION_DISPLAY_PROJECTION)]
        events.extend(normalize_candidate_event(item) for item in _documents(
            self.store.collection("intraday_decision_events"), scope, CANDIDATE_DISPLAY_PROJECTION))
        events.extend(normalize_order_event(item, orders) for item in _documents(self.store.collection("trade_order_events")))
        scoped = [item for item in events if str(item.get("ticker") or "").upper() in allowed_tickers]
        names = _event_name_map(self.store)
        for item in scoped:
            ticker = str(item.get("ticker") or "").upper()
            if not item.get("name") or str(item.get("name")).upper() == ticker:
                item["name"] = names.get(ticker)
        return sorted(scoped, key=lambda item: item.get("occurred_at") or "", reverse=True)[:limit]

    def build(self, *, limit: int = 30) -> dict[str, Any]:
        now = self.now_factory()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now_factory 必须返回带时区时间")
        now_text = now.astimezone(MARKET_TIMEZONE).isoformat(timespec="seconds")
        holding_items = [
            item for item in _documents(self.store.collection("holdings"))
            if item.get("status") in {"active", "reduced"} and int(item.get("quantity") or 0) > 0
        ]
        holdings = {str(item.get("ticker") or "").upper(): item for item in holding_items}
        orders = _documents(self.store.collection("trade_orders"))
        active_orders = [item for item in orders if item.get("status") in ACTIVE_ORDER_STATUSES]
        reservations = {
            str(item.get("plan_id") or ""): item
            for item in _documents(self.store.collection("execution_cash_reservations"))
        }
        executions = _documents(self.store.collection("trade_executions"))
        events = self.events(limit=max(limit, 100))
        timeline_tickers = set(timeline_scope_tickers(self.store))
        timeline = [
            item for item in events
            if str(item.get("ticker") or "").upper() in timeline_tickers
        ][:limit]
        audits = _audit_orders(active_orders, holdings, reservations, executions, now_text)
        latest = _latest_by_ticker(item for item in events if item.get("source_kind") in {"decision", "candidate"})

        risk_queue = list(audits)
        ready: list[dict[str, Any]] = []
        waiting: list[dict[str, Any]] = []
        alternatives: list[dict[str, Any]] = []
        unchanged: list[dict[str, Any]] = []
        for event in latest.values():
            action_type = event.get("action_type")
            state = event.get("execution_state")
            ticker = str(event.get("ticker") or "")
            is_holding = ticker in holdings
            is_today = str(event.get("occurred_at") or "")[:10] == now.date().isoformat()
            if event.get("priority") in {"P0", "P1"} and action_type in {
                ActionType.RISK_EXIT.value,
                ActionType.MANUAL_VERIFY_RISK.value,
                ActionType.CONFIRM_POSITION_FACTS.value,
                ActionType.PREPARE_REDUCE.value,
            } and is_holding:
                # 风险处置只属于真实活动持仓。候选股的支撑/观察止损失守是
                # “候选失效”，不能冒充账户持仓风险，更不能触发卖出提醒。
                risk_queue.append(event)
            elif event.get("position_state") == "flat" and state == "entry_ready" and is_today:
                ready.append(event)
            elif event.get("position_state") == "flat" and state == "confirming" and is_today:
                waiting.append(event)
            elif event.get("position_state") == "flat" and state in {"selected", "armed"} and is_today:
                alternatives.append(event)
            elif is_holding:
                unchanged.append(event)

        for order in active_orders:
            if order.get("side") != "sell":
                continue
            order_event = next(
                (item for item in events if item.get("source_kind") == "order" and (item.get("order") or {}).get("order_id") == order.get("order_id")),
                None,
            )
            if order_event and all(item.get("event_id") != order_event.get("event_id") for item in risk_queue):
                risk_queue.append(order_event)

        rank = lambda item: (item.get("priority") or "P9", -(datetime.fromisoformat(str(item.get("occurred_at")).replace("Z", "+00:00")).timestamp() if item.get("occurred_at") else 0))
        risk_queue.sort(key=rank)
        ready.sort(key=rank)
        waiting.sort(key=rank)
        alternatives.sort(key=rank)
        unchanged.sort(key=rank)
        primary = ready[0] if ready else None
        secondary = [*ready[1:], *alternatives][:2]
        e7_today = [
            item for item in events
            if str((item.get("scenario") or {}).get("scenario_id") or "").upper() == "E7"
            and str(item.get("occurred_at") or "")[:10] == now.date().isoformat()
            and item.get("position_state") == "flat"
        ]
        e7_evaluations = _documents(
            self.store.collection("execution_e7_evaluations"),
            {"trade_date": now.strftime("%Y%m%d")},
        )
        e7_evaluations.sort(key=lambda item: item.get("computed_at") or "", reverse=True)
        e7_triggered_evaluation = next(
            (item for item in e7_evaluations if item.get("status") == "triggered"),
            None,
        )
        e7_nonblocked_evaluation = next(
            (item for item in e7_evaluations if item.get("status") == "not_triggered"),
            None,
        )
        e7_blocked_evaluation = next(
            (item for item in e7_evaluations if item.get("status") == "data_blocked"),
            None,
        )
        e7_ready = next((item for item in e7_today if item.get("execution_state") == "entry_ready"), None)
        e7_observed = e7_today[0] if e7_today else None
        if e7_triggered_evaluation or e7_ready:
            observed = e7_triggered_evaluation or e7_ready
            oversold_arbitrage = {
                "status": "triggered",
                "label": "已触发",
                "reason": observed.get("reason") or (observed.get("presentation") or {}).get("reason"),
                "next_condition": observed.get("next_condition") or (observed.get("presentation") or {}).get("next_condition"),
                "ticker": observed.get("ticker"),
            }
        elif e7_nonblocked_evaluation or e7_observed:
            observed = e7_nonblocked_evaluation or e7_observed
            oversold_arbitrage = {
                "status": "not_triggered",
                "label": "已评估，未触发",
                "reason": observed.get("reason") or (observed.get("presentation") or {}).get("reason"),
                "next_condition": observed.get("next_condition") or (observed.get("presentation") or {}).get("next_condition"),
                "ticker": observed.get("ticker"),
            }
        elif e7_blocked_evaluation:
            oversold_arbitrage = {
                "status": "data_blocked",
                "label": "已评估，数据阻断",
                "reason": e7_blocked_evaluation.get("reason"),
                "next_condition": e7_blocked_evaluation.get("next_condition"),
                "ticker": e7_blocked_evaluation.get("ticker"),
            }
        else:
            oversold_arbitrage = {
                "status": "not_evaluated",
                "label": "今日未评估",
                "reason": "E7 需要 T-2 超跌、T-1 企稳、14:00 后个股分钟 K、板块广度与双指数稳定性；今日尚未形成可核验的三日事实或有效实时价。",
                "next_condition": "点击“同步行情并重算”，补齐实时价与分钟 K 后重新评估。",
                "ticker": None,
            }
        profile = self.store.collection("account_risk_profiles").find_one({"profile_id": "default"}, {"_id": 0}) or {}
        return {
            "status": "ok",
            "as_of": now_text,
            "account": {
                "total_assets": profile.get("total_assets"),
                "available_cash": profile.get("available_cash"),
                "max_loss_per_trade": profile.get("max_loss_per_trade"),
                "active_reserved_cash": round(sum(float(item.get("reserved_cash") or 0) for item in reservations.values() if item.get("status") == "active"), 2),
            },
            "summary": {
                "risk_count": len(risk_queue),
                "primary_count": 1 if primary else 0,
                "alternative_count": len(secondary),
                "waiting_count": len(waiting),
                "holding_unchanged_count": len(unchanged),
                "active_order_count": len(active_orders),
                "audit_issue_count": len(audits),
                "holding_count": len(holdings),
            },
            "queues": {
                "risk": risk_queue,
                "primary_opportunity": primary,
                "alternatives": secondary,
                "waiting_confirmation": waiting,
                "holding_unchanged": unchanged,
            },
            "opportunity_discovery": {
                "scan_date": now.date().isoformat(),
                "generic_candidate_status": "scanned" if any((ready, waiting, alternatives)) else "no_current_event",
                "generic_counts": {
                    "ready": len(ready),
                    "waiting": len(waiting),
                    "alternatives": len(alternatives),
                },
                "oversold_arbitrage": oversold_arbitrage,
            },
            "timeline": timeline,
            "audit_issues": audits,
        }


def queue_workbench_notifications(
    store: Any,
    *,
    now: datetime | None = None,
    limit: int = 100,
    max_age_minutes: int = 30,
) -> int:
    """将可通知的执行事件幂等写入既有飞书 Outbox。"""

    current = now or datetime.now(tz=MARKET_TIMEZONE)
    now_text = current.astimezone(MARKET_TIMEZONE).isoformat(timespec="seconds")
    events = ExecutionWorkbenchService(store, now_factory=lambda: current).events(limit=limit)
    outbox = store.collection(OUTBOX_COLLECTION)
    queued = 0
    for event in events:
        if not event.get("notification_eligible"):
            continue
        if event.get("priority") not in {"P0", "P1", "P2"}:
            continue
        try:
            occurred_at = datetime.fromisoformat(str(event.get("occurred_at") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if current.astimezone(MARKET_TIMEZONE) - occurred_at.astimezone(MARKET_TIMEZONE) > timedelta(minutes=max_age_minutes):
            continue
        outbox_id = hashlib.sha256(f"{event.get('event_id')}|feishu".encode("utf-8")).hexdigest()
        existing = outbox.find_one({"outbox_id": outbox_id}, {"_id": 0})
        if existing:
            continue
        compatible_event = {
            **event,
            "computed_at": event.get("occurred_at"),
            "current": {
                "action_type": event.get("action_type"),
                "action_label": event.get("action_name_cn"),
                "severity": (event.get("presentation") or {}).get("urgency"),
                "scenario_title": (event.get("scenario") or {}).get("scenario_name_cn"),
                "quote_at": (event.get("presentation") or {}).get("data_as_of"),
            },
            "previous": {"action_label": event.get("previous_action_name_cn")},
            "reasons": [(event.get("presentation") or {}).get("reason")],
        }
        outbox.update_one(
            {"outbox_id": outbox_id},
            {"$setOnInsert": {
                "outbox_id": outbox_id,
                "event_id": event.get("event_id"),
                "ticker": event.get("ticker"),
                "channel": "feishu",
                "status": "pending",
                "attempts": 0,
                "next_retry_at": now_text,
                "created_at": now_text,
                "event": compatible_event,
            }},
            upsert=True,
        )
        queued += 1
    return queued


__all__ = [
    "ExecutionWorkbenchService",
    "normalize_candidate_event",
    "normalize_decision_event",
    "normalize_order_event",
    "queue_workbench_notifications",
]
