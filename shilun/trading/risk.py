"""账户风险配置与可审计的动态目标数量计算。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo


COLLECTION_NAME = "account_risk_profiles"
DEFAULT_PROFILE_ID = "default"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


class RiskConfigurationError(ValueError):
    pass


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class AccountRiskProfile:
    """P0 默认值来自已确认 PRD；最大亏损只允许显式更新。"""

    profile_id: str = DEFAULT_PROFILE_ID
    total_assets: float = 86_000.0
    available_cash: float | None = None
    max_loss_per_trade: float = 6_880.0
    max_cash_per_trade: float | None = None
    commission_rate: float | None = None
    min_commission: float | None = None
    stamp_duty_rate: float | None = None
    slippage_bps: float | None = None
    conservative_fee_buffer_rate: float = 0.002
    auto_trade_enabled: bool = False
    source: str = "prd_confirmed_default"
    updated_at: str | None = None

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise RiskConfigurationError("profile_id 不能为空")
        if self.total_assets <= 0:
            raise RiskConfigurationError("账户总资产必须大于 0")
        if self.max_loss_per_trade <= 0:
            raise RiskConfigurationError("单笔最大亏损金额必须大于 0")
        if self.max_loss_per_trade > self.total_assets:
            raise RiskConfigurationError("单笔最大亏损金额不能超过账户总资产")
        if self.available_cash is not None and not 0 <= self.available_cash <= self.total_assets:
            raise RiskConfigurationError("可用现金必须在 0 与账户总资产之间")
        if self.max_cash_per_trade is not None and self.max_cash_per_trade <= 0:
            raise RiskConfigurationError("单笔最大现金占用必须大于 0")
        if self.max_cash_per_trade is not None and self.max_cash_per_trade > self.total_assets:
            raise RiskConfigurationError("单笔最大现金占用不能超过账户总资产")
        optional_rates = (
            self.commission_rate,
            self.min_commission,
            self.stamp_duty_rate,
            self.slippage_bps,
        )
        if any(value is not None and value < 0 for value in optional_rates):
            raise RiskConfigurationError("费用和滑点参数不能为负数")
        if self.conservative_fee_buffer_rate < 0:
            raise RiskConfigurationError("保守费用缓冲率不能为负数")
        if self.auto_trade_enabled:
            raise RiskConfigurationError("P0 不允许开启自动下单")

    @property
    def detailed_costs_configured(self) -> bool:
        return all(value is not None for value in (
            self.commission_rate,
            self.min_commission,
            self.stamp_duty_rate,
            self.slippage_bps,
        ))

    @property
    def cash_configured(self) -> bool:
        return self.available_cash is not None

    def to_document(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["detailed_costs_configured"] = self.detailed_costs_configured
        payload["cash_configured"] = self.cash_configured
        return payload


@dataclass(frozen=True)
class DynamicRiskInput:
    current_quantity: int
    cost_basis: float
    current_price: float
    effective_stop: float
    realized_loss_amount: float = 0.0
    lot_size: int = 100

    def __post_init__(self) -> None:
        if self.current_quantity <= 0:
            raise RiskConfigurationError("真实持仓数量必须大于 0")
        if any(value <= 0 for value in (self.cost_basis, self.current_price, self.effective_stop)):
            raise RiskConfigurationError("成本价、当前价和有效止损价必须大于 0")
        if self.realized_loss_amount < 0:
            raise RiskConfigurationError("已实现亏损金额不能为负数")
        if self.lot_size <= 0:
            raise RiskConfigurationError("交易单位必须大于 0")


@dataclass(frozen=True)
class DynamicRiskResult:
    status: str
    risk_allowed_quantity: int
    target_quantity: int
    change_quantity: int
    current_exit_loss: float
    full_hold_worst_loss: float
    target_worst_loss: float
    max_loss_per_trade: float
    effective_stop: float
    estimated_fees: float
    fee_source: str
    budget_breached_even_after_exit: bool
    explanation: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sell_fee(
    quantity: int,
    price: Decimal,
    profile: AccountRiskProfile,
) -> Decimal:
    if quantity <= 0:
        return Decimal("0")
    notional = _d(quantity) * price
    if not profile.detailed_costs_configured:
        return notional * _d(profile.conservative_fee_buffer_rate)
    commission = max(
        _d(profile.min_commission or 0),
        notional * _d(profile.commission_rate or 0),
    )
    stamp_duty = notional * _d(profile.stamp_duty_rate or 0)
    slippage = notional * _d(profile.slippage_bps or 0) / Decimal("10000")
    return commission + stamp_duty + slippage


def calculate_dynamic_target(
    inputs: DynamicRiskInput,
    profile: AccountRiskProfile,
) -> DynamicRiskResult:
    """枚举卖出后的剩余数量，返回满足最大亏损金额的最大 q。"""

    quantity = inputs.current_quantity
    cost = _d(inputs.cost_basis)
    current = _d(inputs.current_price)
    # 止损高于当前价时不能假设价格会反弹后再卖，保守使用当前价。
    stop = min(current, _d(inputs.effective_stop))
    realized = _d(inputs.realized_loss_amount)
    max_loss = _d(profile.max_loss_per_trade)

    def worst_loss(remaining: int) -> tuple[Decimal, Decimal]:
        sell_now = quantity - remaining
        current_loss = max(Decimal("0"), cost - current) * _d(sell_now)
        remaining_loss = max(Decimal("0"), cost - stop) * _d(remaining)
        fees = _sell_fee(sell_now, current, profile) + _sell_fee(remaining, stop, profile)
        return realized + current_loss + remaining_loss + fees, fees

    candidates = list(range(quantity, -1, -inputs.lot_size))
    if not candidates or candidates[-1] != 0:
        candidates.append(0)
    chosen: int | None = None
    chosen_loss = Decimal("0")
    chosen_fees = Decimal("0")
    for remaining in candidates:
        loss, fees = worst_loss(remaining)
        if loss <= max_loss:
            chosen = remaining
            chosen_loss = loss
            chosen_fees = fees
            break

    exit_loss, exit_fees = worst_loss(0)
    hold_loss, _ = worst_loss(quantity)
    breached = chosen is None
    target = 0 if chosen is None else chosen
    if chosen is None:
        chosen_loss, chosen_fees = exit_loss, exit_fees
    status = "budget_breached" if breached else "within_budget" if target == quantity else "reduce_required"
    fee_source = "configured_costs" if profile.detailed_costs_configured else "conservative_fee_buffer"
    explanation = (
        f"当前持有 {quantity} 股，风险预算允许最多保留 {target} 股",
        f"目标数量最坏损失约 {_money(chosen_loss):.2f} 元，单笔上限 {profile.max_loss_per_trade:.2f} 元",
        "即使立即退出也已超过风险上限" if breached else "目标数量满足风险上限",
    )
    return DynamicRiskResult(
        status=status,
        risk_allowed_quantity=target,
        target_quantity=target,
        change_quantity=target - quantity,
        current_exit_loss=_money(exit_loss),
        full_hold_worst_loss=_money(hold_loss),
        target_worst_loss=_money(chosen_loss),
        max_loss_per_trade=profile.max_loss_per_trade,
        effective_stop=float(stop),
        estimated_fees=_money(chosen_fees),
        fee_source=fee_source,
        budget_breached_even_after_exit=breached,
        explanation=explanation,
    )


def get_account_risk_profile(collection: Any, profile_id: str = DEFAULT_PROFILE_ID) -> AccountRiskProfile:
    document = collection.find_one({"profile_id": profile_id}, {"_id": 0})
    if not document:
        return AccountRiskProfile(profile_id=profile_id)
    allowed = set(AccountRiskProfile.__dataclass_fields__)
    return AccountRiskProfile(**{key: value for key, value in document.items() if key in allowed})


def upsert_account_risk_profile(
    collection: Any,
    profile: AccountRiskProfile,
) -> dict[str, Any]:
    now = datetime.now(tz=MARKET_TIMEZONE).isoformat(timespec="seconds")
    document = {
        **profile.to_document(),
        "source": "user_configured",
        "updated_at": now,
    }
    collection.update_one(
        {"profile_id": profile.profile_id},
        {"$set": document},
        upsert=True,
    )
    return collection.find_one({"profile_id": profile.profile_id}, {"_id": 0}) or document
