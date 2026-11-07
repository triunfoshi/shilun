"""持仓（`holdings`）集合的数据层。

本模块封装 v0.2 战法第 11 章"卖出比买入更需要规则化"里持仓表的数据结构和
CRUD 操作。对应 PRD 阶段 7 · 第 8.2 节。

持仓生命周期：

    active   ── 常态。系统每日跑 daily-check 判定状态并写 holding_events。
       │
       ├──> reduced（减仓）：触发 weaken_reduce 状态但用户还没完全清仓。
       │        entry_size 保留原值，卖出的部分放到 realized_size；后续继续
       │        跟踪剩余仓位。
       │
       └──> closed（清仓）：invalid_exit 触发后用户执行了平仓，或用户主动
                 结束。exit_date / exit_price / realized_pnl 会被填充，
                 此后 daily-check 不再扫描。

状态迁移只能沿这个方向走：`active → reduced → closed`。回退需要新建持仓。

`invalid_price` 是 v0.2 战法 8.4 里判定 `invalid_exit` 时用到的关键字段，
语义是"信号 K 线的开盘价"（对应 `signal_candle_open_lost` 卖出信号）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

HOLDINGS_ENGINE_VERSION = "holdings_v2_strategy_bound"

COLLECTION_NAME = "holdings"
POSITION_EVENT_COLLECTION = "holding_position_events"

HOLDING_STATUSES = {"active", "reduced", "closed"}

DEFAULT_TERMINAL_STATUS = "closed"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class HoldingRecord:
    """PRD 8.2 定义的持仓记录。

    必填字段（对应 PRD 原文）：ticker、entry_date、entry_price、entry_signal、entry_size。
    其余是决策/追踪字段，daily-check 会用到但用户创建时可以留空由系统兜底。
    """

    ticker: str
    entry_date: str
    entry_price: float
    entry_signal: str
    entry_size: float
    quantity: int | None = None
    available_quantity: int | None = None
    frozen_quantity: int = 0
    t1_locked_quantity: int = 0
    t1_lots: list[dict[str, Any]] = field(default_factory=list)
    cost_basis: float | None = None
    manual_current_price: float | None = None
    manual_current_price_at: str | None = None
    realized_loss_amount: float = 0.0
    strategy_id: str = "ma5_v0_2"
    strategy_version: str = "0.2.0"
    strategy_params: dict[str, Any] = field(default_factory=dict)
    initial_plan: dict[str, Any] = field(default_factory=dict)
    current_plan: dict[str, Any] = field(default_factory=dict)
    plan_revisions: list[dict[str, Any]] = field(default_factory=list)
    position_revisions: list[dict[str, Any]] = field(default_factory=list)
    position_revision: int = 1
    pending_risk_action: dict[str, Any] = field(default_factory=dict)
    last_execution_at: str | None = None
    name: str | None = None
    sector_name: str | None = None
    # 决策价位
    target_price: float | None = None       # 期望止盈价 = trade_plan.target_price
    stop_loss_1: float | None = None        # 已确认有效止损（策略建议或人工）
    stop_loss_2: float | None = None        # 次级结构保护位
    stop_loss_mode: str | None = None       # strategy / manual
    stop_loss_plan: dict[str, Any] = field(default_factory=dict)
    stop_loss_confirmed_at: str | None = None
    breakdown_level: float | None = None    # MA20 主升破坏位
    add_point: float | None = None          # 加仓触发价
    signal_candle_open: float | None = None  # invalid_price，用于 signal_candle_open_lost 判定
    # 交易失效条件（文案，用户可读）
    invalid_conditions: list[str] = field(default_factory=list)
    # 用户备注
    note: str | None = None
    # 生命周期
    status: str = "active"
    realized_size: float = 0.0              # 已减掉的仓位比例（0-entry_size）
    realized_pnl: float | None = None       # 已实现收益率（closed 时才有）
    exit_date: str | None = None
    exit_price: float | None = None
    # 元
    engine_version: str = HOLDINGS_ENGINE_VERSION
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_document(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "sector_name": self.sector_name,
            "entry_date": self.entry_date,
            "entry_price": round(_f(self.entry_price), 4),
            "entry_signal": self.entry_signal,
            "entry_size": round(_f(self.entry_size), 4),
            "quantity": int(self.quantity) if self.quantity is not None else None,
            "available_quantity": (
                int(self.available_quantity)
                if self.available_quantity is not None
                else int(self.quantity) if self.quantity is not None else None
            ),
            "frozen_quantity": max(0, int(self.frozen_quantity or 0)),
            "t1_locked_quantity": max(0, int(self.t1_locked_quantity or 0)),
            "t1_lots": list(self.t1_lots or []),
            "cost_basis": round(_f(self.cost_basis if self.cost_basis is not None else self.entry_price), 4),
            "manual_current_price": round(_f(self.manual_current_price), 4) if self.manual_current_price is not None else None,
            "manual_current_price_at": self.manual_current_price_at,
            "realized_loss_amount": round(_f(self.realized_loss_amount), 2),
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_params": dict(self.strategy_params or {}),
            "initial_plan": dict(self.initial_plan or {}),
            "current_plan": dict(self.current_plan or {}),
            "plan_revisions": list(self.plan_revisions or []),
            "position_revisions": list(self.position_revisions or []),
            "position_revision": max(1, int(self.position_revision or 1)),
            "pending_risk_action": dict(self.pending_risk_action or {}),
            "last_execution_at": self.last_execution_at,
            "target_price": round(_f(self.target_price), 4) if self.target_price is not None else None,
            "stop_loss_1": round(_f(self.stop_loss_1), 4) if self.stop_loss_1 is not None else None,
            "stop_loss_2": round(_f(self.stop_loss_2), 4) if self.stop_loss_2 is not None else None,
            "stop_loss_mode": self.stop_loss_mode,
            "stop_loss_plan": dict(self.stop_loss_plan or {}),
            "stop_loss_confirmed_at": self.stop_loss_confirmed_at,
            "breakdown_level": round(_f(self.breakdown_level), 4) if self.breakdown_level is not None else None,
            "add_point": round(_f(self.add_point), 4) if self.add_point is not None else None,
            "signal_candle_open": round(_f(self.signal_candle_open), 4) if self.signal_candle_open is not None else None,
            "invalid_conditions": list(self.invalid_conditions or []),
            "note": self.note,
            "status": self.status,
            "realized_size": round(_f(self.realized_size), 4),
            "realized_pnl": round(_f(self.realized_pnl), 4) if self.realized_pnl is not None else None,
            "exit_date": self.exit_date,
            "exit_price": round(_f(self.exit_price), 4) if self.exit_price is not None else None,
            "engine_version": self.engine_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class HoldingsError(ValueError):
    """持仓层的领域错误。用 ValueError 派生方便 API 层直接映射到 400。"""


def _position_revision(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def publish_position_revision_event(
    collection: Any | None,
    *,
    ticker: str,
    position_revision: int,
    source: str,
    changes: dict[str, Any],
    previous: dict[str, Any],
    occurred_at: str,
) -> dict[str, Any] | None:
    if collection is None:
        return None
    event = {
        "event_id": f"{ticker}:{position_revision}",
        "ticker": ticker,
        "position_revision": position_revision,
        "source": source,
        "changes": dict(changes),
        "previous": dict(previous),
        "occurred_at": occurred_at,
    }
    collection.update_one(
        {"ticker": ticker, "position_revision": position_revision},
        {"$setOnInsert": event},
        upsert=True,
    )
    return event


def upsert_holding(
    collection: Any,
    holding: HoldingRecord,
    *,
    position_event_collection: Any | None = None,
    source: str | None = None,
) -> str:
    """写入或更新持仓（按 ticker 唯一）。

    - 已存在 active/reduced 状态的同 ticker → 视为重复开仓，抛错。
      用户想改现有持仓的价位应该调 `patch_holding`。
    - 已存在 closed 状态的同 ticker → 视为重新开仓，允许覆盖。老的 closed 记录
      按 PRD 阶段 8 会归档到 `trades` 集合（Job 8 处理），这里就地覆盖即可。

    返回 "inserted" / "reopened"。
    """
    if holding.status not in HOLDING_STATUSES:
        raise HoldingsError(f"status 必须是 {sorted(HOLDING_STATUSES)}，收到 {holding.status!r}")
    if holding.quantity is not None and int(holding.quantity) <= 0:
        raise HoldingsError("quantity 必须是正整数股数")
    if holding.available_quantity is not None:
        if int(holding.available_quantity) < 0:
            raise HoldingsError("available_quantity 不能为负数")
        if holding.quantity is None or int(holding.available_quantity) > int(holding.quantity):
            raise HoldingsError("available_quantity 不能超过 quantity")
    if int(holding.frozen_quantity or 0) < 0 or int(holding.t1_locked_quantity or 0) < 0:
        raise HoldingsError("frozen_quantity 和 t1_locked_quantity 不能为负数")
    if holding.quantity is not None:
        accounted = (
            int(holding.available_quantity or 0)
            + int(holding.frozen_quantity or 0)
            + int(holding.t1_locked_quantity or 0)
        )
        if accounted > int(holding.quantity):
            raise HoldingsError("可卖、冻结和 T+1 锁定数量合计不能超过总持仓")
    if holding.cost_basis is not None and _f(holding.cost_basis) <= 0:
        raise HoldingsError("cost_basis 必须大于 0")
    if holding.manual_current_price is not None and _f(holding.manual_current_price) <= 0:
        raise HoldingsError("manual_current_price 必须大于 0")
    if _f(holding.realized_loss_amount) < 0:
        raise HoldingsError("realized_loss_amount 不能为负数")
    if holding.stop_loss_mode not in {None, "strategy", "manual"}:
        raise HoldingsError("stop_loss_mode 仅支持 strategy / manual")

    doc = holding.to_document()
    existing = collection.find_one(
        {"ticker": doc["ticker"]},
        {"_id": 0, "status": 1, "entry_date": 1, "position_revision": 1},
    )
    if existing and existing.get("status") in {"active", "reduced"}:
        raise HoldingsError(
            f"{doc['ticker']} 已存在 {existing.get('status')} 持仓（入场日 {existing.get('entry_date')}），"
            f"如需修改止损/加仓价请用 patch_holding；如需重新开仓请先 close_holding。"
        )
    revision = _position_revision((existing or {}).get("position_revision")) + 1 if existing else 1
    doc["position_revision"] = revision
    collection.update_one(
        {"ticker": doc["ticker"]},
        {"$set": doc},
        upsert=True,
    )
    occurred_at = str(doc.get("updated_at") or _now_iso())
    event_source = source or ("holding_reopened" if existing else "holding_created")
    publish_position_revision_event(
        position_event_collection,
        ticker=doc["ticker"],
        position_revision=revision,
        source=event_source,
        changes={
            "quantity": doc.get("quantity"),
            "available_quantity": doc.get("available_quantity"),
            "cost_basis": doc.get("cost_basis"),
            "status": doc.get("status"),
        },
        previous=dict(existing or {}),
        occurred_at=occurred_at,
    )
    return "reopened" if existing else "inserted"


def get_holding(collection: Any, ticker: str) -> dict[str, Any] | None:
    return collection.find_one({"ticker": ticker}, {"_id": 0})


def list_holdings(
    collection: Any,
    *,
    statuses: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """按 status 过滤返回持仓。默认返回 active + reduced（正在跟踪的）。"""
    if statuses is None:
        allowed = ["active", "reduced"]
    else:
        allowed = [s for s in statuses if s in HOLDING_STATUSES]
    if not allowed:
        return []
    cursor = collection.find({"status": {"$in": allowed}}, {"_id": 0}).sort("entry_date", -1)
    return list(cursor)


def patch_holding(
    collection: Any,
    ticker: str,
    updates: dict[str, Any],
    *,
    position_event_collection: Any | None = None,
    source: str = "holding_patch",
) -> dict[str, Any]:
    """允许修改价位（stop_loss / target / add_point / breakdown_level / note）
    和 realized_size（记录部分减仓）。entry_price 仅允许作为成交录入纠错并写入
    position_revisions；不允许直接改 status/entry_date，避免状态机被绕过。
    用 `close_holding` 结束持仓。
    """
    if not updates:
        raise HoldingsError("patch_holding: updates 不能为空")
    doc = get_holding(collection, ticker)
    if not doc:
        raise HoldingsError(f"{ticker} 持仓不存在")
    if doc.get("status") == "closed":
        raise HoldingsError(f"{ticker} 已 closed，不能再 patch；请新开仓")

    editable = {
        "stop_loss_1", "stop_loss_2", "breakdown_level",
        "target_price", "add_point", "signal_candle_open",
        "invalid_conditions", "note", "realized_size", "name", "sector_name",
        "current_plan", "plan_revisions", "quantity", "available_quantity", "cost_basis", "realized_loss_amount",
        "stop_loss_mode", "stop_loss_plan", "stop_loss_confirmed_at",
        "entry_price", "manual_current_price", "manual_current_price_at",
    }
    invalid_keys = set(updates.keys()) - editable
    if invalid_keys:
        raise HoldingsError(f"patch_holding: 不允许直接修改 {sorted(invalid_keys)}")

    # 单独校验 realized_size：如果超过 entry_size，把 status 自动升到 reduced 或 closed
    payload = dict(updates)
    if "quantity" in payload:
        try:
            quantity = int(payload["quantity"])
        except (TypeError, ValueError) as error:
            raise HoldingsError("quantity 必须是正整数股数") from error
        if quantity <= 0:
            raise HoldingsError("quantity 必须是正整数股数；清仓请使用 close_holding")
        payload["quantity"] = quantity
    if "available_quantity" in payload:
        try:
            available_quantity = int(payload["available_quantity"])
        except (TypeError, ValueError) as error:
            raise HoldingsError("available_quantity 必须是非负整数股数") from error
        effective_quantity = int(payload.get("quantity") or doc.get("quantity") or 0)
        if available_quantity < 0 or available_quantity > effective_quantity:
            raise HoldingsError("available_quantity 必须在 0 到 quantity 之间")
        payload["available_quantity"] = available_quantity
    elif "quantity" in payload:
        previous_available = int(doc.get("available_quantity") or doc.get("quantity") or 0)
        payload["available_quantity"] = min(previous_available, int(payload["quantity"]))
    if "cost_basis" in payload:
        cost_basis = _f(payload["cost_basis"])
        if cost_basis <= 0:
            raise HoldingsError("cost_basis 必须大于 0")
        payload["cost_basis"] = round(cost_basis, 4)
    if "entry_price" in payload:
        entry_price = _f(payload["entry_price"])
        if entry_price <= 0:
            raise HoldingsError("entry_price 必须大于 0")
        payload["entry_price"] = round(entry_price, 4)
    if "manual_current_price" in payload:
        if payload["manual_current_price"] is None:
            payload["manual_current_price_at"] = None
        else:
            manual_current_price = _f(payload["manual_current_price"])
            if manual_current_price <= 0:
                raise HoldingsError("manual_current_price 必须大于 0")
            payload["manual_current_price"] = round(manual_current_price, 4)
            payload["manual_current_price_at"] = _now_iso()
    if "realized_loss_amount" in payload:
        realized_loss = _f(payload["realized_loss_amount"])
        if realized_loss < 0:
            raise HoldingsError("realized_loss_amount 不能为负数")
        payload["realized_loss_amount"] = round(realized_loss, 2)
    if "stop_loss_mode" in payload and payload["stop_loss_mode"] not in {"strategy", "manual"}:
        raise HoldingsError("stop_loss_mode 仅支持 strategy / manual")
    if "stop_loss_plan" in payload and not isinstance(payload["stop_loss_plan"], dict):
        raise HoldingsError("stop_loss_plan 必须是对象")
    if "stop_loss_1" in payload:
        stop_loss = _f(payload["stop_loss_1"])
        if stop_loss <= 0:
            raise HoldingsError("stop_loss_1 必须大于 0")
        payload["stop_loss_1"] = round(stop_loss, 4)
        payload.setdefault("stop_loss_mode", "manual")
        payload.setdefault("stop_loss_confirmed_at", _now_iso())
    if "realized_size" in payload:
        rs = _f(payload["realized_size"])
        entry_size = _f(doc.get("entry_size"))
        if rs < 0 or (entry_size and rs > entry_size + 1e-6):
            raise HoldingsError(
                f"realized_size 应在 [0, {entry_size}] 之间，收到 {rs}"
            )
        payload["realized_size"] = round(rs, 4)
        if entry_size and rs >= entry_size - 1e-6:
            # 全部减完，自动闭仓
            payload["status"] = "closed"
            payload["quantity"] = 0
            payload["available_quantity"] = 0
            payload["exit_date"] = payload.get("exit_date") or _now_iso().split("T")[0]
        elif rs > 0:
            payload["status"] = "reduced"

    position_keys = {
        "quantity", "available_quantity", "cost_basis", "entry_price",
        "manual_current_price",
    }
    position_changes = {key: payload[key] for key in position_keys if key in payload}
    if position_changes:
        revision = _position_revision(doc.get("position_revision")) + 1
        payload["position_revision"] = revision
        revisions = list(doc.get("position_revisions") or [])
        revisions.append({
            "updated_at": _now_iso(),
            "position_revision": revision,
            "source": source,
            "changes": position_changes,
            "previous": {key: doc.get(key) for key in position_changes},
        })
        payload["position_revisions"] = revisions[-100:]

    plan_keys = {
        "entry_price", "target_price", "stop_loss_1", "stop_loss_2", "breakdown_level",
        "add_point", "signal_candle_open", "stop_loss_mode", "stop_loss_plan",
        "stop_loss_confirmed_at",
    }
    changed_plan = {key: payload[key] for key in plan_keys if key in payload}
    if changed_plan:
        previous_plan = dict(doc.get("current_plan") or {})
        # 调用方可能同时补充目标来源、行情截面等非交易价字段；不能在规范化
        # stop/target 时把这些审计信息静默丢掉。
        requested_current_plan = dict(payload.get("current_plan") or {})
        current_plan = {**previous_plan, **requested_current_plan, **changed_plan}
        revisions = list(doc.get("plan_revisions") or [])
        revisions.append({
            "updated_at": _now_iso(),
            "changes": changed_plan,
            "previous_plan": previous_plan,
        })
        payload["current_plan"] = current_plan
        payload["plan_revisions"] = revisions[-50:]

    payload["updated_at"] = _now_iso()
    collection.update_one({"ticker": ticker}, {"$set": payload})
    if position_changes:
        publish_position_revision_event(
            position_event_collection,
            ticker=ticker,
            position_revision=int(payload["position_revision"]),
            source=source,
            changes=position_changes,
            previous={key: doc.get(key) for key in position_changes},
            occurred_at=payload["updated_at"],
        )
    return get_holding(collection, ticker) or {}


def close_holding(
    collection: Any,
    ticker: str,
    *,
    exit_date: str | None = None,
    exit_price: float | None = None,
    reason: str | None = None,
    position_event_collection: Any | None = None,
) -> dict[str, Any]:
    """把持仓推进到 closed。计算 realized_pnl。

    reason 可选（例如 "invalid_exit_triggered_by_ma5_lost_2d"），会存到
    holding.close_reason 字段方便回顾。
    """
    doc = get_holding(collection, ticker)
    if not doc:
        raise HoldingsError(f"{ticker} 持仓不存在")
    if doc.get("status") == "closed":
        # 幂等：已 closed 就直接返回
        return doc

    entry_price = _f(doc.get("entry_price"))
    payload: dict[str, Any] = {
        "status": "closed",
        "quantity": 0,
        "available_quantity": 0,
        "frozen_quantity": 0,
        "t1_locked_quantity": 0,
        "exit_date": exit_date or _now_iso().split("T")[0],
        "updated_at": _now_iso(),
        "position_revision": _position_revision(doc.get("position_revision")) + 1,
    }
    if exit_price is not None:
        payload["exit_price"] = round(_f(exit_price), 4)
        if entry_price:
            payload["realized_pnl"] = round((_f(exit_price) - entry_price) / entry_price, 4)
    if reason:
        payload["close_reason"] = reason
    # 全部仓位落地到 realized_size
    payload["realized_size"] = round(_f(doc.get("entry_size")), 4)
    collection.update_one({"ticker": ticker}, {"$set": payload})
    publish_position_revision_event(
        position_event_collection,
        ticker=ticker,
        position_revision=int(payload["position_revision"]),
        source="holding_closed",
        changes={
            "quantity": 0,
            "available_quantity": 0,
            "status": "closed",
        },
        previous={
            "quantity": doc.get("quantity"),
            "available_quantity": doc.get("available_quantity"),
            "status": doc.get("status"),
        },
        occurred_at=payload["updated_at"],
    )
    return get_holding(collection, ticker) or {}


def delete_holding(collection: Any, ticker: str) -> bool:
    """物理删除，仅供录入错误时清理用。正常关闭走 close_holding。"""
    result = collection.delete_one({"ticker": ticker})
    return bool(getattr(result, "deleted_count", 0))
