"""Job 12E 的现金预留、新仓股数和动态减仓计算。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from shilun.trading.config import (
    DEFAULT_EXECUTION_SIZING_RULES,
    ExecutionSizingConfig,
)
from shilun.trading.risk import AccountRiskProfile, RiskConfigurationError


CASH_RESERVATION_COLLECTION = "execution_cash_reservations"


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal | float) -> float:
    return float(_d(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class TradingUnitRule:
    """由证券规则适配层提供买入单位和普通卖出取整单位。"""

    buy_unit: int = 100
    ordinary_sell_unit: int = 100
    forced_odd_lot_allowed: bool = True

    def __post_init__(self) -> None:
        if self.buy_unit <= 0 or self.ordinary_sell_unit <= 0:
            raise RiskConfigurationError("买卖交易单位必须大于 0")

    def round_buy_down(self, quantity: int | float | Decimal) -> int:
        value = max(0, int(_d(quantity)))
        return value // self.buy_unit * self.buy_unit

    def round_ordinary_sell_down(self, quantity: int | float | Decimal) -> int:
        value = max(0, int(_d(quantity)))
        return value // self.ordinary_sell_unit * self.ordinary_sell_unit


@dataclass(frozen=True)
class CashReservation:
    plan_id: str
    ticker: str
    reserved_cash: float
    planned_quantity: int
    planned_price: float
    status: str = "active"
    expires_at: str | None = None

    def __post_init__(self) -> None:
        if not self.plan_id.strip() or not self.ticker.strip():
            raise RiskConfigurationError("现金预留必须包含 plan_id 和 ticker")
        if self.reserved_cash < 0 or self.planned_quantity < 0 or self.planned_price < 0:
            raise RiskConfigurationError("现金预留金额、股数或价格不合法")
        if self.status not in {"active", "released", "consumed", "expired"}:
            raise RiskConfigurationError("未知现金预留状态")
        if self.status == "active" and (
            self.reserved_cash <= 0 or self.planned_quantity <= 0 or self.planned_price <= 0
        ):
            raise RiskConfigurationError("活动现金预留必须包含正的金额、股数和价格")


@dataclass(frozen=True)
class ScenarioSizingContext:
    profile: AccountRiskProfile
    plan_id: str
    is_main_opportunity: bool = True
    active_reservations: tuple[CashReservation, ...] = ()
    per_share_fee_slippage_buffer: float | None = None

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise RiskConfigurationError("仓位计算必须包含 plan_id")
        if (
            self.per_share_fee_slippage_buffer is not None
            and self.per_share_fee_slippage_buffer < 0
        ):
            raise RiskConfigurationError("每股费用滑点缓冲不能为负数")


@dataclass(frozen=True)
class EntrySizingInput:
    plan_id: str
    ticker: str
    scenario_id: str
    market_permission: str
    entry_price: float
    hard_stop: float
    unit_rule: TradingUnitRule
    is_main_opportunity: bool = True
    active_reservations: tuple[CashReservation, ...] = ()
    per_share_fee_slippage_buffer: float | None = None

    def __post_init__(self) -> None:
        if not self.plan_id.strip() or not self.ticker.strip() or not self.scenario_id.strip():
            raise RiskConfigurationError("新仓计算缺少计划、股票或场景")
        if self.entry_price <= 0 or self.hard_stop <= 0:
            raise RiskConfigurationError("计划入场价和场景止损必须大于 0")
        if self.entry_price <= self.hard_stop:
            raise RiskConfigurationError("计划入场价必须高于场景止损")
        if (
            self.per_share_fee_slippage_buffer is not None
            and self.per_share_fee_slippage_buffer < 0
        ):
            raise RiskConfigurationError("每股费用滑点缓冲不能为负数")


@dataclass(frozen=True)
class ReductionSizingInput:
    current_quantity: int
    available_quantity: int | None
    current_price: float
    target_price: float
    hard_stop: float
    risk_severity: float
    unit_rule: TradingUnitRule
    forced_exit: bool = False

    def __post_init__(self) -> None:
        if self.current_quantity <= 0:
            raise RiskConfigurationError("动态减仓要求真实持仓数量大于 0")
        if self.available_quantity is not None and not 0 <= self.available_quantity <= self.current_quantity:
            raise RiskConfigurationError("可卖数量必须在 0 与总持仓之间")
        if min(self.current_price, self.target_price, self.hard_stop) <= 0:
            raise RiskConfigurationError("现价、目标和止损必须大于 0")
        if not 0 < self.risk_severity <= 1:
            raise RiskConfigurationError("风险严重度必须在 0 与 1 之间")


@dataclass(frozen=True)
class ExecutionSizingResult:
    sizing_type: str
    status: str
    executable: bool
    planned_quantity: int | None = None
    target_quantity: int | None = None
    change_quantity: int | None = None
    planned_cash: float | None = None
    reserved_cash: float = 0.0
    available_cash: float | None = None
    cash_after_other_reservations: float | None = None
    risk_amount: float | None = None
    per_share_risk: float | None = None
    risk_allowed_quantity: int | None = None
    cash_allowed_quantity: int | None = None
    base_quantity: int | None = None
    market_permission_factor: float | None = None
    scenario_size_factor: float | None = None
    remaining_reward_risk: float | None = None
    risk_severity: float | None = None
    quantity_factor: float | None = None
    available_quantity: int | None = None
    recommended_sell_quantity: int | None = None
    explanation: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _active_reserved_cash(
    reservations: tuple[CashReservation, ...],
    *,
    exclude_plan_id: str,
) -> Decimal:
    # 同一 plan_id 的重复快照只算一次，避免重算或重复回读占用两遍现金。
    by_plan: dict[str, Decimal] = {}
    for item in reservations:
        if item.status != "active" or item.plan_id == exclude_plan_id:
            continue
        by_plan[item.plan_id] = max(by_plan.get(item.plan_id, Decimal("0")), _d(item.reserved_cash))
    return sum(by_plan.values(), Decimal("0"))


def _entry_factors(
    scenario_id: str,
    market_permission: str,
    rules: ExecutionSizingConfig,
) -> tuple[float, float]:
    code = scenario_id.upper()
    permission = market_permission.lower()
    if code == "E7":
        return rules.rebound_permission_factor, rules.e7_factor
    if permission == "attack":
        return rules.attack_factor, rules.ordinary_scenario_factor
    if permission == "hold":
        if code in {"E3", "E4"}:
            # E3/E4 的“观察持有最多 50%”是总效果，不与日级 0.5 重复相乘。
            return rules.attack_factor, rules.hold_e3_e4_factor
        return rules.hold_factor, rules.ordinary_scenario_factor
    return 0.0, rules.ordinary_scenario_factor


def calculate_entry_sizing(
    inputs: EntrySizingInput,
    profile: AccountRiskProfile,
    rules: ExecutionSizingConfig = DEFAULT_EXECUTION_SIZING_RULES,
) -> ExecutionSizingResult:
    market_factor, scenario_factor = _entry_factors(
        inputs.scenario_id,
        inputs.market_permission,
        rules,
    )
    if profile.available_cash is None:
        return ExecutionSizingResult(
            sizing_type="entry",
            status="cash_not_configured",
            executable=False,
            market_permission_factor=market_factor,
            scenario_size_factor=scenario_factor,
            explanation=("账户尚未配置可用现金，不能生成新仓股数",),
        )
    if market_factor <= 0:
        return ExecutionSizingResult(
            sizing_type="entry",
            status="permission_blocked",
            executable=False,
            planned_quantity=0,
            target_quantity=0,
            change_quantity=0,
            available_cash=profile.available_cash,
            market_permission_factor=market_factor,
            scenario_size_factor=scenario_factor,
            explanation=("当前市场权限不允许该普通新仓场景",),
        )

    entry = _d(inputs.entry_price)
    stop = _d(inputs.hard_stop)
    fee_buffer = (
        _d(inputs.per_share_fee_slippage_buffer)
        if inputs.per_share_fee_slippage_buffer is not None
        else entry * _d(profile.conservative_fee_buffer_rate)
    )
    per_share_risk = entry - stop + fee_buffer
    other_reserved = _active_reserved_cash(
        inputs.active_reservations,
        exclude_plan_id=inputs.plan_id,
    )
    cash_after_reservations = max(Decimal("0"), _d(profile.available_cash) - other_reserved)
    cash_cap = cash_after_reservations
    if profile.max_cash_per_trade is not None:
        cash_cap = min(cash_cap, _d(profile.max_cash_per_trade))

    risk_allowed = int(_d(profile.max_loss_per_trade) / per_share_risk)
    cash_allowed = int(cash_cap / entry)
    base_quantity = inputs.unit_rule.round_buy_down(min(risk_allowed, cash_allowed))
    factor = _d(market_factor) * _d(scenario_factor)
    planned_quantity = inputs.unit_rule.round_buy_down(_d(base_quantity) * factor)
    planned_cash = entry * _d(planned_quantity)
    risk_amount = per_share_risk * _d(planned_quantity)
    common = {
        "sizing_type": "entry",
        "planned_quantity": planned_quantity,
        "target_quantity": planned_quantity,
        "change_quantity": planned_quantity,
        "planned_cash": _money(planned_cash),
        "available_cash": profile.available_cash,
        "cash_after_other_reservations": _money(cash_after_reservations),
        "risk_amount": _money(risk_amount),
        "per_share_risk": float(per_share_risk),
        "risk_allowed_quantity": risk_allowed,
        "cash_allowed_quantity": cash_allowed,
        "base_quantity": base_quantity,
        "market_permission_factor": market_factor,
        "scenario_size_factor": scenario_factor,
    }
    if planned_quantity < inputs.unit_rule.buy_unit:
        return ExecutionSizingResult(
            **common,
            status="insufficient_buy_unit",
            executable=False,
            reserved_cash=0.0,
            explanation=(
                f"风险与现金约束后不足一个买入单位 {inputs.unit_rule.buy_unit} 股",
                "保持等待，不生成准备建仓动作",
            ),
        )
    if not inputs.is_main_opportunity:
        return ExecutionSizingResult(
            **common,
            status="alternative_unreserved",
            executable=False,
            reserved_cash=0.0,
            explanation=(
                f"备选机会可计划 {planned_quantity} 股，但不重复占用主机会现金",
            ),
        )
    return ExecutionSizingResult(
        **common,
        status="ready",
        executable=True,
        reserved_cash=_money(planned_cash),
        explanation=(
            f"风险允许 {risk_allowed} 股，现金允许 {cash_allowed} 股",
            f"交易单位取整后基础 {base_quantity} 股，最终计划 {planned_quantity} 股",
            f"为主机会预留现金 {_money(planned_cash):.2f} 元",
        ),
    )


def calculate_reduction_sizing(
    inputs: ReductionSizingInput,
    rules: ExecutionSizingConfig = DEFAULT_EXECUTION_SIZING_RULES,
) -> ExecutionSizingResult:
    denominator = inputs.current_price - inputs.hard_stop
    remaining_rr = (
        (inputs.target_price - inputs.current_price) / denominator
        if denominator > 0
        else 0.0
    )
    if inputs.forced_exit or remaining_rr <= 0:
        quantity_factor = 1.0
    elif remaining_rr >= rules.high_reward_risk_threshold:
        quantity_factor = inputs.risk_severity * rules.high_reward_risk_discount
    elif remaining_rr >= rules.medium_reward_risk_threshold:
        quantity_factor = inputs.risk_severity
    else:
        quantity_factor = max(inputs.risk_severity, rules.low_reward_risk_floor)

    common = {
        "sizing_type": "reduction",
        "remaining_reward_risk": round(remaining_rr, 6),
        "risk_severity": inputs.risk_severity,
        "quantity_factor": round(quantity_factor, 6),
        "available_quantity": inputs.available_quantity,
    }
    if inputs.available_quantity is None:
        return ExecutionSizingResult(
            **common,
            status="forced_quantity_unverified" if inputs.forced_exit else "quantity_not_configured",
            executable=False,
            target_quantity=0 if inputs.forced_exit else None,
            change_quantity=None,
            recommended_sell_quantity=None,
            explanation=("可卖数量缺失，保留动作类型但必须先核验持仓事实",),
        )
    if inputs.forced_exit:
        return ExecutionSizingResult(
            **common,
            status="forced_exit",
            executable=inputs.available_quantity > 0,
            target_quantity=0,
            change_quantity=-inputs.available_quantity,
            recommended_sell_quantity=inputs.available_quantity,
            explanation=(
                f"强制退出不受剩余盈亏比保护，目标数量为 0，可卖 {inputs.available_quantity} 股",
            ),
        )
    if inputs.available_quantity < inputs.unit_rule.ordinary_sell_unit:
        return ExecutionSizingResult(
            **common,
            status="below_sell_unit",
            executable=False,
            target_quantity=inputs.current_quantity,
            change_quantity=0,
            recommended_sell_quantity=0,
            explanation=(
                f"可卖 {inputs.available_quantity} 股，不足一个普通卖出单位 {inputs.unit_rule.ordinary_sell_unit} 股",
            ),
        )
    raw_sell = _d(inputs.available_quantity) * _d(quantity_factor)
    rounded_sell = inputs.unit_rule.round_ordinary_sell_down(raw_sell)
    maximum_lot_sell = inputs.unit_rule.round_ordinary_sell_down(inputs.available_quantity)
    sell_quantity = min(
        maximum_lot_sell,
        max(inputs.unit_rule.ordinary_sell_unit, rounded_sell),
    )
    target_quantity = inputs.current_quantity - sell_quantity
    return ExecutionSizingResult(
        **common,
        status="reduce_ready",
        executable=sell_quantity > 0,
        target_quantity=target_quantity,
        change_quantity=-sell_quantity,
        recommended_sell_quantity=sell_quantity,
        explanation=(
            f"风险严重度 {inputs.risk_severity:.2f}，剩余盈亏比 {remaining_rr:.2f}",
            f"数量系数 {quantity_factor:.2f}，建议减仓 {sell_quantity} 股",
        ),
    )


def list_active_cash_reservations(collection: Any) -> tuple[CashReservation, ...]:
    documents = collection.find({"status": "active"}, {"_id": 0})
    allowed = set(CashReservation.__dataclass_fields__)
    return tuple(
        CashReservation(**{key: value for key, value in document.items() if key in allowed})
        for document in documents
    )


def sync_cash_reservation(
    collection: Any,
    *,
    plan_id: str,
    ticker: str,
    sizing: ExecutionSizingResult,
    updated_at: datetime,
    expires_at: str | None = None,
) -> dict[str, Any]:
    if updated_at.tzinfo is None or updated_at.utcoffset() is None:
        raise RiskConfigurationError("现金预留更新时间必须包含时区")
    active = sizing.status == "ready" and sizing.reserved_cash > 0
    document = {
        "plan_id": plan_id,
        "ticker": ticker.upper(),
        "status": "active" if active else "released",
        "reserved_cash": sizing.reserved_cash if active else 0.0,
        "planned_quantity": sizing.planned_quantity or 0,
        "planned_price": (
            (sizing.planned_cash or 0) / sizing.planned_quantity
            if sizing.planned_quantity else 0.0
        ),
        "updated_at": updated_at.isoformat(timespec="seconds"),
        "expires_at": expires_at,
        "source": "execution_sizing_v0_3",
    }
    collection.update_one(
        {"plan_id": plan_id},
        {"$set": document},
        upsert=True,
    )
    return collection.find_one({"plan_id": plan_id}, {"_id": 0}) or document
