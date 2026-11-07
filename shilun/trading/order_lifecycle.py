"""Job 12F 的人工委托、成交、现金与实时持仓闭环。

系统只记录用户已经在券商侧提交或成交的事实，不连接券商，也不自动下单。
委托负责冻结现金/可卖股数，成交负责更新真实现金与持仓；重复的 order_id 和
execution_id 必须幂等，避免页面重试重复扣款或重复改仓。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any
import uuid
from zoneinfo import ZoneInfo

from shilun.market.holdings import (
    HoldingRecord,
    get_holding,
    publish_position_revision_event,
    upsert_holding,
)
from shilun.trading.contracts import ExecutionState
from shilun.trading.risk import (
    AccountRiskProfile,
    RiskConfigurationError,
    get_account_risk_profile,
)


ORDER_COLLECTION_NAME = "trade_orders"
ORDER_EVENT_COLLECTION_NAME = "trade_order_events"
EXECUTION_COLLECTION_NAME = "trade_executions"
ACCOUNT_COLLECTION_NAME = "account_risk_profiles"
RESERVATION_COLLECTION_NAME = "execution_cash_reservations"
POSITION_EVENT_COLLECTION_NAME = "holding_position_events"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderIntent(StrEnum):
    ENTRY = "entry"
    ADD = "add"
    REDUCE = "reduce"
    EXIT = "exit"


class OrderStatus(StrEnum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"


ACTIVE_ORDER_STATUSES = {OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value}


class OrderLifecycleError(ValueError):
    """委托或成交事实不合法，或与当前现金/持仓冲突。"""


def _market_time(value: str | datetime | None, *, field_name: str) -> datetime:
    if value is None:
        return datetime.now(tz=MARKET_TIMEZONE)
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as error:
            raise OrderLifecycleError(f"{field_name} 必须是 ISO 时间") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed.astimezone(MARKET_TIMEZONE)


def _money(value: float) -> float:
    return round(float(value) + 0.0, 2)


@dataclass(frozen=True)
class TradeOrderInput:
    plan_id: str
    ticker: str
    side: str
    quantity: int
    limit_price: float
    intent: str
    order_id: str | None = None
    scenario_id: str | None = None
    placed_at: str | datetime | None = None
    expires_at: str | datetime | None = None
    name: str | None = None
    sector_name: str | None = None
    strategy_id: str | None = None
    strategy_version: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.plan_id.strip() or not self.ticker.strip():
            raise OrderLifecycleError("委托必须包含 plan_id 和 ticker")
        if self.order_id is not None and not self.order_id.strip():
            raise OrderLifecycleError("order_id 不能为空")
        try:
            side = OrderSide(self.side.lower())
            intent = OrderIntent(self.intent.lower())
        except ValueError as error:
            raise OrderLifecycleError("委托方向或意图不合法") from error
        if isinstance(self.quantity, bool) or int(self.quantity) <= 0:
            raise OrderLifecycleError("委托股数必须是正整数")
        if float(self.limit_price) <= 0:
            raise OrderLifecycleError("委托价格必须大于 0")
        if side is OrderSide.BUY and intent not in {OrderIntent.ENTRY, OrderIntent.ADD}:
            raise OrderLifecycleError("买入委托只支持 entry / add 意图")
        if side is OrderSide.SELL and intent not in {OrderIntent.REDUCE, OrderIntent.EXIT}:
            raise OrderLifecycleError("卖出委托只支持 reduce / exit 意图")


@dataclass(frozen=True)
class TradeFillInput:
    quantity: int
    price: float
    execution_id: str | None = None
    executed_at: str | datetime | None = None
    fees: float = 0.0
    note: str | None = None

    def __post_init__(self) -> None:
        if self.execution_id is not None and not self.execution_id.strip():
            raise OrderLifecycleError("execution_id 不能为空")
        if isinstance(self.quantity, bool) or int(self.quantity) <= 0:
            raise OrderLifecycleError("成交股数必须是正整数")
        if float(self.price) <= 0 or float(self.fees) < 0:
            raise OrderLifecycleError("成交价必须大于 0，费用不能为负")


def get_trade_order(collection: Any, order_id: str) -> dict[str, Any] | None:
    return collection.find_one({"order_id": order_id}, {"_id": 0})


def list_trade_orders(
    collection: Any,
    *,
    ticker: str | None = None,
    statuses: tuple[str, ...] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not 1 <= limit <= 500:
        raise OrderLifecycleError("limit 必须在 1 到 500 之间")
    documents = list(collection.find({}, {"_id": 0}))
    if ticker:
        normalized = ticker.upper()
        documents = [item for item in documents if item.get("ticker") == normalized]
    if statuses:
        allowed = {str(item) for item in statuses}
        documents = [item for item in documents if item.get("status") in allowed]
    documents.sort(key=lambda item: str(item.get("placed_at") or ""), reverse=True)
    return documents[:limit]


def _active_reserved_cash(collection: Any, *, exclude_plan_id: str) -> float:
    by_plan: dict[str, float] = {}
    for document in collection.find({"status": "active"}, {"_id": 0}):
        plan_id = str(document.get("plan_id") or "")
        if not plan_id or plan_id == exclude_plan_id:
            continue
        by_plan[plan_id] = max(by_plan.get(plan_id, 0.0), float(document.get("reserved_cash") or 0.0))
    return sum(by_plan.values())


def _set_reservation(
    collection: Any,
    *,
    order: dict[str, Any],
    status: str,
    reserved_cash: float,
    updated_at: str,
) -> dict[str, Any]:
    remaining = int(order.get("remaining_quantity") or 0)
    document = {
        "plan_id": order["plan_id"],
        "ticker": order["ticker"],
        "order_id": order["order_id"],
        "status": status,
        "reserved_cash": _money(reserved_cash),
        "planned_quantity": remaining if status == "active" else 0,
        "planned_price": float(order["limit_price"]) if status == "active" else 0.0,
        "updated_at": updated_at,
        "expires_at": order.get("expires_at"),
        "source": "order_lifecycle_v0_3",
    }
    collection.update_one({"plan_id": order["plan_id"]}, {"$set": document}, upsert=True)
    return collection.find_one({"plan_id": order["plan_id"]}, {"_id": 0}) or document


def _account_profile(collection: Any) -> AccountRiskProfile:
    try:
        return get_account_risk_profile(collection)
    except RiskConfigurationError as error:
        raise OrderLifecycleError(str(error)) from error


def _set_available_cash(
    collection: Any,
    *,
    profile: AccountRiskProfile,
    available_cash: float,
    updated_at: str,
) -> dict[str, Any]:
    if available_cash < -0.005:
        raise OrderLifecycleError("成交后可用现金不能为负数")
    cash = _money(max(0.0, available_cash))
    document = {
        "available_cash": cash,
        "cash_configured": True,
        "total_assets": max(float(profile.total_assets), cash),
        "updated_at": updated_at,
        "source": "user_configured",
    }
    collection.update_one({"profile_id": profile.profile_id}, {"$set": document}, upsert=True)
    return collection.find_one({"profile_id": profile.profile_id}, {"_id": 0}) or document


def _write_order_event(
    collection: Any | None,
    *,
    order: dict[str, Any],
    event_type: str,
    occurred_at: str,
    details: dict[str, Any] | None = None,
) -> None:
    if collection is None:
        return
    revision = int(order.get("order_revision") or 1)
    event = {
        "event_id": f"{order['order_id']}:{revision}",
        "order_id": order["order_id"],
        "plan_id": order["plan_id"],
        "ticker": order["ticker"],
        "order_revision": revision,
        "event_type": event_type,
        "status": order["status"],
        "execution_state": order["execution_state"],
        "occurred_at": occurred_at,
        "details": dict(details or {}),
    }
    collection.update_one(
        {"order_id": order["order_id"], "order_revision": revision},
        {"$setOnInsert": event},
        upsert=True,
    )


def _same_order(existing: dict[str, Any], requested: dict[str, Any]) -> bool:
    fields = (
        "plan_id",
        "ticker",
        "side",
        "quantity",
        "limit_price",
        "intent",
        "scenario_id",
        "strategy_id",
        "strategy_version",
    )
    return all(existing.get(field) == requested.get(field) for field in fields)


def create_trade_order(
    *,
    orders_collection: Any,
    holdings_collection: Any,
    account_collection: Any,
    reservations_collection: Any,
    position_event_collection: Any | None = None,
    order_event_collection: Any | None = None,
    inputs: TradeOrderInput,
) -> dict[str, Any]:
    moment = _market_time(inputs.placed_at, field_name="placed_at")
    expires = (
        _market_time(inputs.expires_at, field_name="expires_at")
        if inputs.expires_at is not None else None
    )
    if expires is not None and expires <= moment:
        raise OrderLifecycleError("expires_at 必须晚于 placed_at")
    order_id = inputs.order_id or uuid.uuid4().hex
    side = OrderSide(inputs.side.lower())
    intent = OrderIntent(inputs.intent.lower())
    holding = get_holding(holdings_collection, inputs.ticker.upper())
    active_holding = holding if holding and holding.get("status") in {"active", "reduced"} else None
    try:
        from shilun.strategies import (
            E7_OVERSOLD_OVERNIGHT_STRATEGY_ID,
            ExecutionStrategyError,
            resolve_strategy_binding,
            scenario_code,
        )

        if active_holding and (side is OrderSide.SELL or intent is OrderIntent.ADD):
            holding_scenario = active_holding.get("entry_signal")
            holding_strategy = str(active_holding.get("strategy_id") or "").strip().lower() or None
            if scenario_code(holding_scenario) == "E7":
                holding_strategy = E7_OVERSOLD_OVERNIGHT_STRATEGY_ID
            if (
                inputs.strategy_id
                and holding_strategy
                and str(inputs.strategy_id).strip().lower() != holding_strategy
            ):
                raise OrderLifecycleError(
                    f"{inputs.ticker.upper()} 已绑定 {holding_strategy}，委托不能改绑 {inputs.strategy_id}"
                )
            strategy_id, strategy_version = resolve_strategy_binding(
                holding_scenario or inputs.scenario_id,
                holding_strategy,
                active_holding.get("strategy_version") if holding_strategy == active_holding.get("strategy_id") else None,
            )
        else:
            strategy_id, strategy_version = resolve_strategy_binding(
                inputs.scenario_id,
                inputs.strategy_id,
                inputs.strategy_version,
            )
    except ExecutionStrategyError as error:
        raise OrderLifecycleError(str(error)) from error

    if (
        active_holding
        and side is OrderSide.BUY
        and intent is OrderIntent.ADD
        and strategy_id == E7_OVERSOLD_OVERNIGHT_STRATEGY_ID
    ):
        raise OrderLifecycleError("E7 隔夜套利持仓不允许次日加仓；请按退出计划处理。")
    execution_state = (
        ExecutionState.ENTRY_PENDING
        if side is OrderSide.BUY else
        ExecutionState.EXIT_PENDING
        if intent is OrderIntent.EXIT else ExecutionState.REDUCE_PENDING
    )
    order = {
        "order_id": order_id,
        "plan_id": inputs.plan_id,
        "ticker": inputs.ticker.upper(),
        "side": side.value,
        "intent": intent.value,
        "scenario_id": inputs.scenario_id,
        "quantity": int(inputs.quantity),
        "filled_quantity": 0,
        "remaining_quantity": int(inputs.quantity),
        "limit_price": round(float(inputs.limit_price), 4),
        "average_fill_price": None,
        "fees_total": 0.0,
        "status": OrderStatus.OPEN.value,
        "execution_state": execution_state.value,
        "order_revision": 1,
        "placed_at": moment.isoformat(timespec="seconds"),
        "expires_at": expires.isoformat(timespec="seconds") if expires else None,
        "name": inputs.name,
        "sector_name": inputs.sector_name,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "note": inputs.note,
        "source": "user_confirmed",
        "created_at": moment.isoformat(timespec="seconds"),
        "updated_at": moment.isoformat(timespec="seconds"),
    }
    existing = get_trade_order(orders_collection, order_id)
    if existing is not None:
        if not _same_order(existing, order):
            raise OrderLifecycleError("order_id 已存在且委托内容不一致")
        return existing

    if side is OrderSide.BUY:
        has_active_holding = holding is not None and holding.get("status") in {"active", "reduced"}
        if has_active_holding and intent is not OrderIntent.ADD:
            raise OrderLifecycleError("已有活动持仓，买入意图请选择 add（加仓）")
        if not has_active_holding and intent is OrderIntent.ADD:
            raise OrderLifecycleError("当前没有活动持仓，加仓意图不合法，请选择 entry（建仓）")
        profile = _account_profile(account_collection)
        if profile.available_cash is None:
            raise OrderLifecycleError("账户尚未配置可用现金，不能记录买入委托")
        required_cash = _money(order["quantity"] * order["limit_price"])
        other_reserved = _active_reserved_cash(
            reservations_collection,
            exclude_plan_id=order["plan_id"],
        )
        cash_after_other = float(profile.available_cash) - other_reserved
        if required_cash > cash_after_other + 0.005:
            raise OrderLifecycleError(
                f"买入委托需预留 {required_cash:.2f} 元，扣除其他预留后仅有 {cash_after_other:.2f} 元"
            )
        _set_reservation(
            reservations_collection,
            order=order,
            status="active",
            reserved_cash=required_cash,
            updated_at=order["updated_at"],
        )
        order["reserved_cash"] = required_cash
    else:
        if holding is None or holding.get("status") not in {"active", "reduced"}:
            raise OrderLifecycleError(f"{order['ticker']} 没有可卖的活动持仓")
        current_quantity = int(holding.get("quantity") or 0)
        available_quantity = int(
            holding.get("available_quantity")
            if holding.get("available_quantity") is not None else current_quantity
        )
        if order["quantity"] > available_quantity:
            raise OrderLifecycleError(
                f"卖出委托 {order['quantity']} 股不能超过当前可卖 {available_quantity} 股"
            )
        frozen_before = int(holding.get("frozen_quantity") or 0)
        revision = int(holding.get("position_revision") or 0) + 1
        updated_at = order["updated_at"]
        changes = {
            "available_quantity": available_quantity - order["quantity"],
            "frozen_quantity": frozen_before + order["quantity"],
        }
        revisions = list(holding.get("position_revisions") or [])
        revisions.append({
            "updated_at": updated_at,
            "position_revision": revision,
            "source": "sell_order_placed",
            "order_id": order_id,
            "changes": changes,
            "previous": {
                "available_quantity": available_quantity,
                "frozen_quantity": frozen_before,
            },
        })
        holding_updates: dict[str, Any] = {
            **changes,
            "position_revision": revision,
            "position_revisions": revisions[-100:],
            "updated_at": updated_at,
        }
        if intent is OrderIntent.EXIT:
            holding_updates["pending_risk_action"] = {
                "status": "pending",
                "action_type": "risk_exit",
                "order_id": order_id,
                "remaining_quantity": order["quantity"],
            }
        holdings_collection.update_one({"ticker": order["ticker"]}, {"$set": holding_updates})
        publish_position_revision_event(
            position_event_collection,
            ticker=order["ticker"],
            position_revision=revision,
            source="sell_order_placed",
            changes=changes,
            previous={
                "available_quantity": available_quantity,
                "frozen_quantity": frozen_before,
            },
            occurred_at=updated_at,
        )
        order["frozen_quantity"] = order["quantity"]

    orders_collection.update_one(
        {"order_id": order_id},
        {"$setOnInsert": order},
        upsert=True,
    )
    stored = get_trade_order(orders_collection, order_id) or order
    _write_order_event(
        order_event_collection,
        order=stored,
        event_type="order_placed",
        occurred_at=stored["placed_at"],
        details={"quantity": stored["quantity"], "limit_price": stored["limit_price"]},
    )
    return stored


def _append_position_revision(
    holding: dict[str, Any],
    *,
    source: str,
    occurred_at: str,
    execution_id: str,
    changes: dict[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    revision = int(holding.get("position_revision") or 0) + 1
    revisions = list(holding.get("position_revisions") or [])
    revisions.append({
        "updated_at": occurred_at,
        "position_revision": revision,
        "source": source,
        "execution_id": execution_id,
        "changes": dict(changes),
        "previous": {key: holding.get(key) for key in changes},
    })
    return revision, revisions[-100:]


def _record_buy_holding(
    *,
    holdings_collection: Any,
    position_event_collection: Any | None,
    order: dict[str, Any],
    execution_id: str,
    quantity: int,
    price: float,
    fees: float,
    executed_at: str,
    total_assets: float,
) -> dict[str, Any]:
    ticker = order["ticker"]
    holding = get_holding(holdings_collection, ticker)
    execution_cost = price * quantity + fees
    trade_date = executed_at.split("T", 1)[0]
    if holding is None or holding.get("status") == "closed":
        if order.get("intent") == OrderIntent.ADD.value:
            raise OrderLifecycleError("当前没有活动持仓，不能回写加仓成交")
        cost_basis = execution_cost / quantity
        entry_size = min(1.0, execution_cost / max(total_assets, 0.01))
        upsert_holding(
            holdings_collection,
            HoldingRecord(
                ticker=ticker,
                name=order.get("name"),
                sector_name=order.get("sector_name"),
                entry_date=trade_date,
                entry_price=price,
                entry_signal=order.get("scenario_id") or "execution_order",
                entry_size=max(0.0001, round(entry_size, 4)),
                quantity=quantity,
                available_quantity=0,
                t1_locked_quantity=quantity,
                t1_lots=[{
                    "trade_date": trade_date,
                    "quantity": quantity,
                    "status": "locked",
                    "execution_id": execution_id,
                }],
                cost_basis=cost_basis,
                strategy_id=order.get("strategy_id") or "ma5_v0_2",
                strategy_version=order.get("strategy_version") or "0.2.0",
                initial_plan={"plan_id": order["plan_id"], "order_id": order["order_id"]},
                current_plan={"plan_id": order["plan_id"], "order_id": order["order_id"]},
                position_revisions=[{
                    "updated_at": executed_at,
                    "source": "buy_execution",
                    "execution_id": execution_id,
                    "changes": {
                        "quantity": quantity,
                        "available_quantity": 0,
                        "cost_basis": round(cost_basis, 4),
                    },
                }],
                last_execution_at=executed_at,
            ),
            position_event_collection=position_event_collection,
            source="buy_execution",
        )
        holding = get_holding(holdings_collection, ticker) or {}
        extras = {
            "entry_plan_id": order["plan_id"],
            "entry_order_ids": [order["order_id"]],
            "t1_locked_quantity": quantity,
            "t1_lots": [{
                "trade_date": trade_date,
                "quantity": quantity,
                "status": "locked",
                "execution_id": execution_id,
            }],
            "applied_execution_ids": [execution_id],
            "last_execution_at": executed_at,
            "updated_at": executed_at,
        }
        holdings_collection.update_one({"ticker": ticker}, {"$set": extras})
        return get_holding(holdings_collection, ticker) or {**holding, **extras}

    if (
        holding.get("entry_plan_id") != order["plan_id"]
        and order.get("intent") != OrderIntent.ADD.value
    ):
        raise OrderLifecycleError("已有活动持仓不属于当前买入计划，禁止混入成交")
    current_quantity = int(holding.get("quantity") or 0)
    current_cost_basis = float(holding.get("cost_basis") or holding.get("entry_price") or 0)
    new_quantity = current_quantity + quantity
    new_cost_basis = (current_cost_basis * current_quantity + execution_cost) / new_quantity
    locked_before = int(holding.get("t1_locked_quantity") or 0)
    changes = {
        "quantity": new_quantity,
        "available_quantity": int(holding.get("available_quantity") or 0),
        "cost_basis": round(new_cost_basis, 4),
        "entry_size": round(min(1.0, new_quantity * new_cost_basis / max(total_assets, 0.01)), 4),
        "t1_locked_quantity": locked_before + quantity,
    }
    revision, revisions = _append_position_revision(
        holding,
        source="buy_execution",
        occurred_at=executed_at,
        execution_id=execution_id,
        changes=changes,
    )
    lots = list(holding.get("t1_lots") or [])
    lots.append({
        "trade_date": trade_date,
        "quantity": quantity,
        "status": "locked",
        "execution_id": execution_id,
    })
    order_ids = list(holding.get("entry_order_ids") or [])
    if order["order_id"] not in order_ids:
        order_ids.append(order["order_id"])
    applied_ids = list(holding.get("applied_execution_ids") or [])
    applied_ids.append(execution_id)
    updates = {
        **changes,
        "position_revision": revision,
        "position_revisions": revisions,
        "t1_lots": lots[-200:],
        "entry_order_ids": order_ids[-50:],
        "applied_execution_ids": applied_ids[-200:],
        "last_execution_at": executed_at,
        "updated_at": executed_at,
    }
    holdings_collection.update_one({"ticker": ticker}, {"$set": updates})
    publish_position_revision_event(
        position_event_collection,
        ticker=ticker,
        position_revision=revision,
        source="buy_execution",
        changes=changes,
        previous={key: holding.get(key) for key in changes},
        occurred_at=executed_at,
    )
    return get_holding(holdings_collection, ticker) or {**holding, **updates}


def _record_sell_holding(
    *,
    holdings_collection: Any,
    position_event_collection: Any | None,
    order: dict[str, Any],
    execution_id: str,
    quantity: int,
    price: float,
    fees: float,
    executed_at: str,
    total_assets: float,
) -> dict[str, Any]:
    ticker = order["ticker"]
    holding = get_holding(holdings_collection, ticker)
    if holding is None or holding.get("status") not in {"active", "reduced"}:
        raise OrderLifecycleError(f"{ticker} 没有可回写卖出成交的活动持仓")
    current_quantity = int(holding.get("quantity") or 0)
    frozen_quantity = int(holding.get("frozen_quantity") or 0)
    available_quantity = int(holding.get("available_quantity") or 0)
    if quantity > current_quantity or quantity > frozen_quantity:
        raise OrderLifecycleError("卖出成交超过当前持仓或委托冻结股数")
    remaining = current_quantity - quantity
    remaining_frozen = frozen_quantity - quantity
    remaining_available = min(available_quantity, max(0, remaining - remaining_frozen))
    cost_basis = float(holding.get("cost_basis") or holding.get("entry_price") or 0)
    realized_loss_delta = max(0.0, (cost_basis - price) * quantity) + fees
    realized_loss_total = float(holding.get("realized_loss_amount") or 0) + realized_loss_delta
    status = "closed" if remaining == 0 else "reduced"
    changes: dict[str, Any] = {
        "quantity": remaining,
        "available_quantity": 0 if remaining == 0 else remaining_available,
        "frozen_quantity": 0 if remaining == 0 else remaining_frozen,
        "entry_size": round(min(1.0, remaining * cost_basis / max(total_assets, 0.01)), 4),
        "realized_loss_amount": _money(realized_loss_total),
        "status": status,
    }
    revision, revisions = _append_position_revision(
        holding,
        source="sell_execution",
        occurred_at=executed_at,
        execution_id=execution_id,
        changes=changes,
    )
    applied_ids = list(holding.get("applied_execution_ids") or [])
    applied_ids.append(execution_id)
    pending = dict(holding.get("pending_risk_action") or {})
    if pending.get("order_id") == order["order_id"]:
        pending.update({
            "status": "completed" if remaining == 0 else "partially_executed",
            "remaining_quantity": max(0, int(order["remaining_quantity"]) - quantity),
            "last_execution_id": execution_id,
            "last_execution_at": executed_at,
        })
    updates: dict[str, Any] = {
        **changes,
        "position_revision": revision,
        "position_revisions": revisions,
        "applied_execution_ids": applied_ids[-200:],
        "pending_risk_action": pending,
        "last_execution_at": executed_at,
        "updated_at": executed_at,
    }
    if remaining == 0:
        entry_price = float(holding.get("entry_price") or cost_basis)
        updates.update({
            "exit_date": executed_at.split("T", 1)[0],
            "exit_price": round(price, 4),
            "realized_size": float(holding.get("entry_size") or 0.0),
            "realized_pnl": round(price / entry_price - 1.0, 4) if entry_price else None,
            "close_reason": "sell_order_filled",
            "t1_locked_quantity": 0,
        })
    holdings_collection.update_one({"ticker": ticker}, {"$set": updates})
    publish_position_revision_event(
        position_event_collection,
        ticker=ticker,
        position_revision=revision,
        source="sell_execution",
        changes=changes,
        previous={key: holding.get(key) for key in changes},
        occurred_at=executed_at,
    )
    return get_holding(holdings_collection, ticker) or {**holding, **updates}


def record_order_fill(
    *,
    orders_collection: Any,
    executions_collection: Any,
    holdings_collection: Any,
    account_collection: Any,
    reservations_collection: Any,
    position_event_collection: Any | None = None,
    order_event_collection: Any | None = None,
    order_id: str,
    inputs: TradeFillInput,
) -> dict[str, Any]:
    order = get_trade_order(orders_collection, order_id)
    if order is None:
        raise OrderLifecycleError(f"委托 {order_id} 不存在")
    execution_id = inputs.execution_id or uuid.uuid4().hex
    existing_execution = executions_collection.find_one({"execution_id": execution_id}, {"_id": 0})
    if existing_execution is not None:
        if existing_execution.get("order_id") != order_id:
            raise OrderLifecycleError("execution_id 已被其他委托使用")
        return {
            "execution": existing_execution,
            "order": order,
            "holding": get_holding(holdings_collection, order["ticker"]),
            "account_profile": account_collection.find_one({"profile_id": "default"}, {"_id": 0}),
            "idempotent_replay": True,
        }
    if order.get("status") not in ACTIVE_ORDER_STATUSES:
        raise OrderLifecycleError(f"委托状态 {order.get('status')} 不允许继续成交")
    fill_quantity = int(inputs.quantity)
    fill_price = float(inputs.price)
    fees = float(inputs.fees)
    remaining_before = int(order.get("remaining_quantity") or 0)
    if fill_quantity > remaining_before:
        raise OrderLifecycleError(f"成交股数不能超过委托剩余 {remaining_before} 股")
    side = OrderSide(order["side"])
    if side is OrderSide.BUY and fill_price > float(order["limit_price"]) + 1e-8:
        raise OrderLifecycleError("买入成交价不能高于限价")
    if side is OrderSide.SELL and fill_price + 1e-8 < float(order["limit_price"]):
        raise OrderLifecycleError("卖出成交价不能低于限价")
    moment = _market_time(inputs.executed_at, field_name="executed_at")
    executed_at = moment.isoformat(timespec="seconds")
    profile = _account_profile(account_collection)
    if side is OrderSide.BUY and profile.available_cash is None:
        raise OrderLifecycleError("账户尚未配置可用现金，不能回写成交")

    if side is OrderSide.BUY:
        cash_delta = -(fill_price * fill_quantity + fees)
        if float(profile.available_cash) + cash_delta < -0.005:
            raise OrderLifecycleError("实际买入金额和费用超过当前可用现金")
        holding = _record_buy_holding(
            holdings_collection=holdings_collection,
            position_event_collection=position_event_collection,
            order=order,
            execution_id=execution_id,
            quantity=fill_quantity,
            price=fill_price,
            fees=fees,
            executed_at=executed_at,
            total_assets=profile.total_assets,
        )
    else:
        cash_delta = fill_price * fill_quantity - fees
        if cash_delta < -0.005:
            raise OrderLifecycleError("卖出费用不能超过成交金额")
        holding = _record_sell_holding(
            holdings_collection=holdings_collection,
            position_event_collection=position_event_collection,
            order=order,
            execution_id=execution_id,
            quantity=fill_quantity,
            price=fill_price,
            fees=fees,
            executed_at=executed_at,
            total_assets=profile.total_assets,
        )
    account = (
        _set_available_cash(
            account_collection,
            profile=profile,
            available_cash=float(profile.available_cash) + cash_delta,
            updated_at=executed_at,
        )
        if profile.available_cash is not None else profile.to_document()
    )

    filled_before = int(order.get("filled_quantity") or 0)
    filled_after = filled_before + fill_quantity
    remaining_after = remaining_before - fill_quantity
    notional_before = float(order.get("average_fill_price") or 0.0) * filled_before
    average_fill = (notional_before + fill_price * fill_quantity) / filled_after
    status = OrderStatus.FILLED if remaining_after == 0 else OrderStatus.PARTIALLY_FILLED
    if status is OrderStatus.FILLED:
        execution_state = (
            ExecutionState.CLOSED
            if holding and holding.get("status") == "closed" else ExecutionState.POSITIONED
        )
    elif side is OrderSide.SELL and order.get("intent") == OrderIntent.EXIT.value:
        execution_state = ExecutionState.EXIT_PENDING
    else:
        execution_state = ExecutionState.POSITIONED
    revision = int(order.get("order_revision") or 1) + 1
    order_updates = {
        "filled_quantity": filled_after,
        "remaining_quantity": remaining_after,
        "average_fill_price": round(average_fill, 4),
        "fees_total": _money(float(order.get("fees_total") or 0.0) + fees),
        "status": status.value,
        "execution_state": execution_state.value,
        "last_execution_id": execution_id,
        "last_execution_at": executed_at,
        "order_revision": revision,
        "updated_at": executed_at,
    }
    orders_collection.update_one({"order_id": order_id}, {"$set": order_updates})
    updated_order = get_trade_order(orders_collection, order_id) or {**order, **order_updates}

    if side is OrderSide.BUY:
        reservation_status = "consumed" if remaining_after == 0 else "active"
        _set_reservation(
            reservations_collection,
            order=updated_order,
            status=reservation_status,
            reserved_cash=remaining_after * float(order["limit_price"]),
            updated_at=executed_at,
        )

    execution = {
        "execution_id": execution_id,
        "order_id": order_id,
        "plan_id": order["plan_id"],
        "ticker": order["ticker"],
        "side": side.value,
        "quantity": fill_quantity,
        "price": round(fill_price, 4),
        "fees": _money(fees),
        "cash_delta": _money(cash_delta),
        "executed_at": executed_at,
        "note": inputs.note,
        "order_filled_quantity_after": filled_after,
        "order_remaining_quantity_after": remaining_after,
        "quantity_after": holding.get("quantity") if holding else None,
        "available_quantity_after": holding.get("available_quantity") if holding else None,
        "cost_basis_after": holding.get("cost_basis") if holding else None,
        "account_available_cash_after": account.get("available_cash"),
        "source": "user_confirmed",
        "created_at": datetime.now(tz=MARKET_TIMEZONE).isoformat(timespec="seconds"),
    }
    executions_collection.update_one(
        {"execution_id": execution_id},
        {"$setOnInsert": execution},
        upsert=True,
    )
    _write_order_event(
        order_event_collection,
        order=updated_order,
        event_type="fill_recorded",
        occurred_at=executed_at,
        details={
            "execution_id": execution_id,
            "quantity": fill_quantity,
            "price": round(fill_price, 4),
            "remaining_quantity": remaining_after,
        },
    )
    return {
        "execution": execution,
        "order": updated_order,
        "holding": holding,
        "account_profile": account,
        "idempotent_replay": False,
    }


def cancel_trade_order(
    *,
    orders_collection: Any,
    holdings_collection: Any,
    reservations_collection: Any,
    position_event_collection: Any | None = None,
    order_event_collection: Any | None = None,
    order_id: str,
    cancelled_at: str | datetime | None = None,
    reason: str | None = None,
    event_type: str = "order_cancelled",
) -> dict[str, Any]:
    order = get_trade_order(orders_collection, order_id)
    if order is None:
        raise OrderLifecycleError(f"委托 {order_id} 不存在")
    if order.get("status") == OrderStatus.CANCELLED.value:
        return order
    if order.get("status") == OrderStatus.FILLED.value:
        raise OrderLifecycleError("已全部成交的委托不能撤销")
    moment = _market_time(cancelled_at, field_name="cancelled_at")
    timestamp = moment.isoformat(timespec="seconds")
    remaining = int(order.get("remaining_quantity") or 0)
    holding = get_holding(holdings_collection, order["ticker"])
    if order["side"] == OrderSide.BUY.value:
        _set_reservation(
            reservations_collection,
            order=order,
            status="released",
            reserved_cash=0.0,
            updated_at=timestamp,
        )
    elif remaining > 0 and holding is not None and holding.get("status") in {"active", "reduced"}:
        available_before = int(holding.get("available_quantity") or 0)
        frozen_before = int(holding.get("frozen_quantity") or 0)
        if remaining > frozen_before:
            raise OrderLifecycleError("委托剩余股数超过持仓冻结数量，必须先核验持仓")
        frozen_after = frozen_before - remaining
        current_quantity = int(holding.get("quantity") or 0)
        available_after = min(current_quantity - frozen_after, available_before + remaining)
        changes = {
            "available_quantity": available_after,
            "frozen_quantity": frozen_after,
        }
        revision = int(holding.get("position_revision") or 0) + 1
        revisions = list(holding.get("position_revisions") or [])
        revisions.append({
            "updated_at": timestamp,
            "position_revision": revision,
            "source": "sell_order_cancelled",
            "order_id": order_id,
            "changes": changes,
            "previous": {
                "available_quantity": available_before,
                "frozen_quantity": frozen_before,
            },
        })
        holding_updates: dict[str, Any] = {
            **changes,
            "position_revision": revision,
            "position_revisions": revisions[-100:],
            "updated_at": timestamp,
        }
        pending = dict(holding.get("pending_risk_action") or {})
        if pending.get("order_id") == order_id:
            pending["status"] = "cancelled"
            pending["remaining_quantity"] = remaining
            holding_updates["pending_risk_action"] = pending
        holdings_collection.update_one({"ticker": order["ticker"]}, {"$set": holding_updates})
        publish_position_revision_event(
            position_event_collection,
            ticker=order["ticker"],
            position_revision=revision,
            source="sell_order_cancelled",
            changes=changes,
            previous={
                "available_quantity": available_before,
                "frozen_quantity": frozen_before,
            },
            occurred_at=timestamp,
        )

    execution_state = (
        ExecutionState.POSITIONED
        if int(order.get("filled_quantity") or 0) > 0 or (
            holding is not None and holding.get("status") in {"active", "reduced"}
        )
        else ExecutionState.ARMED
    )
    revision = int(order.get("order_revision") or 1) + 1
    updates = {
        "status": OrderStatus.CANCELLED.value,
        "execution_state": execution_state.value,
        "cancelled_at": timestamp,
        "cancel_reason": reason,
        "order_revision": revision,
        "updated_at": timestamp,
    }
    orders_collection.update_one({"order_id": order_id}, {"$set": updates})
    updated_order = get_trade_order(orders_collection, order_id) or {**order, **updates}
    _write_order_event(
        order_event_collection,
        order=updated_order,
        event_type=event_type,
        occurred_at=timestamp,
        details={"remaining_quantity": remaining, "reason": reason},
    )
    return updated_order


def expire_trade_orders(
    *,
    orders_collection: Any,
    holdings_collection: Any,
    reservations_collection: Any,
    position_event_collection: Any | None = None,
    order_event_collection: Any | None = None,
    as_of: str | datetime | None = None,
) -> dict[str, Any]:
    """自动撤销已到期委托并释放现金/冻结股数。

    未显式填写 ``expires_at`` 的日内委托默认在委托当日 15:30 到期。
    单笔异常不会阻断其他委托收尾，而是进入工作台的事实核验队列。
    """

    moment = _market_time(as_of, field_name="as_of")
    expired: list[str] = []
    failed: list[dict[str, str]] = []
    active_orders = list_trade_orders(
        orders_collection,
        statuses=tuple(ACTIVE_ORDER_STATUSES),
        limit=500,
    )
    for order in active_orders:
        try:
            if order.get("expires_at"):
                cutoff = _market_time(order["expires_at"], field_name="expires_at")
            else:
                placed = _market_time(order.get("placed_at"), field_name="placed_at")
                cutoff = placed.replace(hour=15, minute=30, second=0, microsecond=0)
            if moment < cutoff:
                continue
            cancel_trade_order(
                orders_collection=orders_collection,
                holdings_collection=holdings_collection,
                reservations_collection=reservations_collection,
                position_event_collection=position_event_collection,
                order_event_collection=order_event_collection,
                order_id=str(order["order_id"]),
                cancelled_at=moment,
                reason="委托到期自动撤销",
                event_type="order_expired",
            )
            expired.append(str(order["order_id"]))
        except (OrderLifecycleError, TypeError, ValueError) as error:
            failed.append({"order_id": str(order.get("order_id") or ""), "reason": str(error)})
    return {"expired_order_ids": expired, "failed_orders": failed}


def settle_t1_positions(
    holdings_collection: Any,
    *,
    as_of_date: str | date,
    position_event_collection: Any | None = None,
    settled_at: str | datetime | None = None,
) -> tuple[dict[str, Any], ...]:
    try:
        session_date = as_of_date if isinstance(as_of_date, date) else date.fromisoformat(as_of_date)
    except ValueError as error:
        raise OrderLifecycleError("as_of_date 必须是 YYYY-MM-DD") from error
    moment = _market_time(settled_at, field_name="settled_at")
    timestamp = moment.isoformat(timespec="seconds")
    updated: list[dict[str, Any]] = []
    for raw in holdings_collection.find({}, {"_id": 0}):
        holding = dict(raw)
        if holding.get("status") not in {"active", "reduced"}:
            continue
        lots = list(holding.get("t1_lots") or [])
        unlock_quantity = 0
        changed = False
        for lot in lots:
            if lot.get("status") != "locked":
                continue
            try:
                trade_date = date.fromisoformat(str(lot.get("trade_date")))
            except ValueError:
                continue
            if trade_date < session_date:
                unlock_quantity += int(lot.get("quantity") or 0)
                lot["status"] = "settled"
                lot["settled_at"] = timestamp
                changed = True
        if not changed or unlock_quantity <= 0:
            continue
        quantity = int(holding.get("quantity") or 0)
        frozen = int(holding.get("frozen_quantity") or 0)
        available_before = int(holding.get("available_quantity") or 0)
        available_after = min(max(0, quantity - frozen), available_before + unlock_quantity)
        locked_after = max(0, int(holding.get("t1_locked_quantity") or 0) - unlock_quantity)
        changes = {
            "available_quantity": available_after,
            "t1_locked_quantity": locked_after,
        }
        revision = int(holding.get("position_revision") or 0) + 1
        revisions = list(holding.get("position_revisions") or [])
        revisions.append({
            "updated_at": timestamp,
            "position_revision": revision,
            "source": "t1_settlement",
            "changes": changes,
            "previous": {
                "available_quantity": available_before,
                "t1_locked_quantity": holding.get("t1_locked_quantity"),
            },
        })
        updates = {
            **changes,
            "t1_lots": lots[-200:],
            "position_revision": revision,
            "position_revisions": revisions[-100:],
            "updated_at": timestamp,
        }
        holdings_collection.update_one({"ticker": holding["ticker"]}, {"$set": updates})
        publish_position_revision_event(
            position_event_collection,
            ticker=holding["ticker"],
            position_revision=revision,
            source="t1_settlement",
            changes=changes,
            previous={
                "available_quantity": available_before,
                "t1_locked_quantity": holding.get("t1_locked_quantity"),
            },
            occurred_at=timestamp,
        )
        updated.append(get_holding(holdings_collection, holding["ticker"]) or {**holding, **updates})
    return tuple(updated)


__all__ = [
    "ACCOUNT_COLLECTION_NAME",
    "ACTIVE_ORDER_STATUSES",
    "EXECUTION_COLLECTION_NAME",
    "ORDER_COLLECTION_NAME",
    "ORDER_EVENT_COLLECTION_NAME",
    "POSITION_EVENT_COLLECTION_NAME",
    "RESERVATION_COLLECTION_NAME",
    "OrderIntent",
    "OrderLifecycleError",
    "OrderSide",
    "OrderStatus",
    "TradeFillInput",
    "TradeOrderInput",
    "cancel_trade_order",
    "create_trade_order",
    "expire_trade_orders",
    "get_trade_order",
    "list_trade_orders",
    "record_order_fill",
    "settle_t1_positions",
]
