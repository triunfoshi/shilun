"""交易动作回写与持仓数量闭环。

P0 只处理持仓卖出回写；系统不连接券商、不自动下单。用户提交的是已经发生的
成交事实，随后才更新数量、已实现亏损和风险动作状态。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from shilun.market.holdings import get_holding, publish_position_revision_event


COLLECTION_NAME = "trade_executions"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


class ExecutionError(ValueError):
    """成交事实不合法或与当前持仓冲突。"""


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(tz=MARKET_TIMEZONE)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ExecutionError("executed_at 必须是 ISO 时间") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed.astimezone(MARKET_TIMEZONE)


def list_executions(collection: Any, ticker: str, *, limit: int = 100) -> list[dict[str, Any]]:
    return list(
        collection.find({"ticker": ticker.upper()}, {"_id": 0})
        .sort("executed_at", -1)
        .limit(limit)
    )


def record_sell_execution(
    *,
    holdings_collection: Any,
    executions_collection: Any,
    account_collection: Any | None = None,
    position_event_collection: Any | None = None,
    ticker: str,
    quantity: int,
    price: float,
    execution_id: str | None = None,
    executed_at: str | None = None,
    fees: float = 0.0,
    note: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = ticker.upper()
    stable_execution_id = execution_id or uuid.uuid4().hex
    existing_execution = executions_collection.find_one(
        {"execution_id": stable_execution_id},
        {"_id": 0},
    )
    if existing_execution is not None:
        if existing_execution.get("ticker") != normalized:
            raise ExecutionError("execution_id 已被其他股票使用")
        return existing_execution, get_holding(holdings_collection, normalized) or {}
    holding = get_holding(holdings_collection, normalized)
    if holding is None or holding.get("status") not in {"active", "reduced"}:
        raise ExecutionError(f"{normalized} 没有可回写成交的活动持仓")
    try:
        sell_quantity = int(quantity)
        execution_price = float(price)
        execution_fees = float(fees)
        current_quantity = int(holding.get("quantity"))
        available_quantity = int(
            holding.get("available_quantity")
            if holding.get("available_quantity") is not None
            else current_quantity
        )
        cost_basis = float(holding.get("cost_basis") or holding.get("entry_price"))
    except (TypeError, ValueError) as error:
        raise ExecutionError("成交股数、成交价、费用及持仓成本必须是有效数字") from error
    if sell_quantity <= 0 or execution_price <= 0 or execution_fees < 0:
        raise ExecutionError("卖出股数和成交价必须大于 0，费用不能为负")
    if execution_fees > execution_price * sell_quantity:
        raise ExecutionError("卖出费用不能超过成交金额")
    if current_quantity <= 0 or sell_quantity > current_quantity:
        raise ExecutionError(f"卖出股数不能超过当前持仓 {current_quantity} 股")
    if sell_quantity > available_quantity:
        raise ExecutionError(f"卖出股数不能超过当前可卖 {available_quantity} 股")

    moment = _parse_time(executed_at)
    remaining = current_quantity - sell_quantity
    remaining_available = max(0, available_quantity - sell_quantity)
    realized_loss_delta = max(0.0, (cost_basis - execution_price) * sell_quantity) + execution_fees
    realized_loss_total = float(holding.get("realized_loss_amount") or 0.0) + realized_loss_delta
    execution = {
        "execution_id": stable_execution_id,
        "ticker": normalized,
        "side": "sell",
        "quantity": sell_quantity,
        "price": round(execution_price, 4),
        "fees": round(execution_fees, 2),
        "executed_at": moment.isoformat(timespec="seconds"),
        "note": note,
        "quantity_before": current_quantity,
        "quantity_after": remaining,
        "available_quantity_before": available_quantity,
        "available_quantity_after": remaining_available,
        "cost_basis": round(cost_basis, 4),
        "realized_loss_delta": round(realized_loss_delta, 2),
        "realized_loss_total": round(realized_loss_total, 2),
        "source": "user_confirmed",
        "created_at": datetime.now(tz=MARKET_TIMEZONE).isoformat(timespec="seconds"),
    }
    if account_collection is not None:
        from shilun.trading.risk import get_account_risk_profile

        profile = get_account_risk_profile(account_collection)
        cash_delta = execution_price * sell_quantity - execution_fees
        execution["cash_delta"] = round(cash_delta, 2)
        if profile.available_cash is not None:
            available_cash_after = round(float(profile.available_cash) + cash_delta, 2)
            account_collection.update_one(
                {"profile_id": profile.profile_id},
                {"$set": {
                    "available_cash": available_cash_after,
                    "cash_configured": True,
                    "total_assets": max(float(profile.total_assets), available_cash_after),
                    "updated_at": moment.isoformat(timespec="seconds"),
                    "source": "user_configured",
                }},
                upsert=True,
            )
            execution["account_available_cash_after"] = available_cash_after
        else:
            execution["account_available_cash_after"] = None
            execution["cash_update_status"] = "cash_not_configured"
    executions_collection.update_one(
        {"execution_id": execution["execution_id"]},
        {"$setOnInsert": execution},
        upsert=True,
    )

    pending = dict(holding.get("pending_risk_action") or {})
    if pending:
        pending.update({
            "status": "completed" if remaining == 0 else "partially_executed",
            "remaining_quantity": remaining,
            "last_execution_id": execution["execution_id"],
            "last_execution_at": execution["executed_at"],
        })
    position_revision = int(holding.get("position_revision") or 0) + 1
    revisions = list(holding.get("position_revisions") or [])
    revisions.append({
        "updated_at": execution["executed_at"],
        "source": "trade_execution",
        "position_revision": position_revision,
        "execution_id": execution["execution_id"],
        "changes": {
            "quantity": remaining,
            "available_quantity": remaining_available,
            "realized_loss_amount": round(realized_loss_total, 2),
        },
        "previous": {
            "quantity": current_quantity,
            "available_quantity": available_quantity,
            "realized_loss_amount": holding.get("realized_loss_amount"),
        },
    })
    updates: dict[str, Any] = {
        "quantity": remaining,
        "available_quantity": remaining_available,
        "position_revision": position_revision,
        "realized_loss_amount": round(realized_loss_total, 2),
        "pending_risk_action": pending,
        "last_execution_at": execution["executed_at"],
        "position_revisions": revisions[-100:],
        "updated_at": execution["executed_at"],
    }
    if remaining == 0:
        entry_price = float(holding.get("entry_price") or cost_basis)
        updates.update({
            "status": "closed",
            "frozen_quantity": 0,
            "t1_locked_quantity": 0,
            "exit_date": moment.strftime("%Y-%m-%d"),
            "exit_price": round(execution_price, 4),
            "realized_size": float(holding.get("entry_size") or 0.0),
            "realized_pnl": round(execution_price / entry_price - 1.0, 4) if entry_price else None,
            "close_reason": "sell_execution_completed",
        })
    else:
        updates["status"] = "reduced"
    holdings_collection.update_one({"ticker": normalized}, {"$set": updates})
    publish_position_revision_event(
        position_event_collection,
        ticker=normalized,
        position_revision=position_revision,
        source="trade_execution",
        changes={
            "quantity": remaining,
            "available_quantity": remaining_available,
            "realized_loss_amount": round(realized_loss_total, 2),
            "status": updates["status"],
        },
        previous={
            "quantity": current_quantity,
            "available_quantity": available_quantity,
            "realized_loss_amount": holding.get("realized_loss_amount"),
            "status": holding.get("status"),
        },
        occurred_at=execution["executed_at"],
    )
    return execution, get_holding(holdings_collection, normalized) or {}
