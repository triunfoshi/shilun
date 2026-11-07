"""Job 12B 的盘前关键位、ATR 缓冲与最高可接受价。

本模块只消费已完成日线和已明确的结构事实。所有函数都是纯计算：同一组
盘前事实与同一规则版本必须生成相同结果，盘中不得原地漂移关键位。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import math
from typing import Any

from shilun.trading.config import DEFAULT_EXECUTION_LEVEL_RULES, ExecutionLevelConfig
from shilun.trading.contracts import require_aware_datetime


class KeyLevelError(ValueError):
    """日线事实不足或关键位之间不满足可执行关系。"""


def _price(value: Any, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise KeyLevelError(f"{field_name} 必须是有效价格") from error
    if not math.isfinite(number) or number <= 0:
        raise KeyLevelError(f"{field_name} 必须大于 0")
    return number


def _date_text(row: dict[str, Any]) -> str:
    return str(row.get("date") or row.get("trade_date") or "")[:10]


def _round_price(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _prepare_daily_bars(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for raw in sorted(rows or [], key=_date_text):
        trade_date = _date_text(raw)
        if not trade_date:
            raise KeyLevelError("已完成日线缺少交易日期")
        if trade_date in seen_dates:
            raise KeyLevelError(f"已完成日线日期重复：{trade_date}")
        seen_dates.add(trade_date)
        open_price = _price(raw.get("open", raw.get("close")), field_name=f"{trade_date}.open")
        high = _price(raw.get("high"), field_name=f"{trade_date}.high")
        low = _price(raw.get("low"), field_name=f"{trade_date}.low")
        close = _price(raw.get("close"), field_name=f"{trade_date}.close")
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            raise KeyLevelError(f"{trade_date} 日线 OHLC 关系不合法")
        prepared.append({
            "date": trade_date,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
        })
    return prepared


def _mean_close(rows: list[dict[str, Any]], window: int) -> float:
    return sum(float(item["close"]) for item in rows[-window:]) / window


def _atr(rows: list[dict[str, Any]], period: int) -> float:
    true_ranges: list[float] = []
    for index, row in enumerate(rows):
        high = float(row["high"])
        low = float(row["low"])
        if index == 0:
            true_ranges.append(high - low)
            continue
        previous_close = float(rows[index - 1]["close"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(true_ranges[-period:]) / period


def _latest_confirmed_swing_low(
    rows: list[dict[str, Any]],
    *,
    lookback: int,
) -> tuple[float | None, str | None]:
    start = max(1, len(rows) - lookback - 1)
    for index in range(len(rows) - 2, start - 1, -1):
        previous_low = float(rows[index - 1]["low"])
        current_low = float(rows[index]["low"])
        next_low = float(rows[index + 1]["low"])
        if current_low <= previous_low and current_low < next_low:
            return current_low, str(rows[index]["date"])
    return None, None


def _nearest(
    candidates: list[tuple[float | None, str]],
    *,
    reference_price: float,
    direction: str,
) -> tuple[float | None, str | None]:
    usable: list[tuple[float, int, str]] = []
    for priority, (value, source) in enumerate(candidates):
        if value is None:
            continue
        if direction == "below" and value < reference_price:
            usable.append((value, -priority, source))
        elif direction == "above" and value > reference_price:
            usable.append((value, priority, source))
    if not usable:
        return None, None
    if direction == "below":
        value, _, source = max(usable, key=lambda item: (item[0], item[1]))
    else:
        value, _, source = min(usable, key=lambda item: (item[0], item[1]))
    return value, source


@dataclass(frozen=True)
class FrozenKeyLevels:
    rule_version: str
    as_of: str
    reference_price: float
    prev_close: float
    prev_high: float
    prev_low: float
    frozen_ma5: float
    frozen_ma8: float
    frozen_ma10: float
    frozen_ma20: float
    atr14: float
    atr_pct14: float
    swing_low: float | None
    swing_low_as_of: str | None
    box_high_20: float
    high_60: float | None
    support_level: float | None
    support_source: str | None
    pressure_level: float | None
    pressure_source: str | None
    target_1: float | None
    target_1_source: str | None
    support_zone_low: float | None
    support_zone_high: float | None
    breakout_confirmation: float | None
    soft_break_confirmation: float | None
    conservative_target_line: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_frozen_key_levels(
    rows: list[dict[str, Any]],
    *,
    reference_price: float | None = None,
    rules: ExecutionLevelConfig = DEFAULT_EXECUTION_LEVEL_RULES,
) -> FrozenKeyLevels:
    """用截至前一交易日的完成日线冻结一份确定性关键位快照。"""

    prepared = _prepare_daily_bars(rows)
    minimum = max(rules.atr_period, 20, rules.box_lookback) + 1
    if len(prepared) < minimum:
        raise KeyLevelError(f"冻结关键位至少需要 {minimum} 根有效已完成日线")

    latest = prepared[-1]
    previous_rows = prepared[:-1]
    prev_close = float(latest["close"])
    reference = _price(
        reference_price if reference_price is not None else prev_close,
        field_name="reference_price",
    )
    atr14 = _atr(prepared, rules.atr_period)
    if atr14 <= 0:
        raise KeyLevelError("ATR14 必须大于 0")

    ma5 = _mean_close(prepared, 5)
    ma8 = _mean_close(prepared, 8)
    ma10 = _mean_close(prepared, 10)
    ma20 = _mean_close(prepared, 20)
    swing_low, swing_low_as_of = _latest_confirmed_swing_low(
        prepared,
        lookback=rules.swing_lookback,
    )
    box_high = max(float(item["high"]) for item in previous_rows[-rules.box_lookback:])
    high_60 = (
        max(float(item["high"]) for item in previous_rows[-rules.pressure_lookback:])
        if len(previous_rows) >= rules.pressure_lookback
        else None
    )

    support, support_source = _nearest(
        [
            (ma5, "MA5"),
            (ma8, "MA8"),
            (ma10, "MA10"),
            (ma20, "MA20"),
            (swing_low, "最近确认结构低点"),
        ],
        reference_price=reference,
        direction="below",
    )
    pressure, pressure_source = _nearest(
        [
            (float(latest["high"]), "昨日高点"),
            (box_high, "20日箱体上沿"),
            (high_60, "60日前高"),
        ],
        reference_price=reference,
        direction="above",
    )

    observation_buffer = rules.support_observation_atr * atr14
    noise_buffer = rules.base_noise_atr * atr14
    return FrozenKeyLevels(
        rule_version=rules.version,
        as_of=str(latest["date"]),
        reference_price=round(reference, 4),
        prev_close=round(prev_close, 4),
        prev_high=round(float(latest["high"]), 4),
        prev_low=round(float(latest["low"]), 4),
        frozen_ma5=round(ma5, 4),
        frozen_ma8=round(ma8, 4),
        frozen_ma10=round(ma10, 4),
        frozen_ma20=round(ma20, 4),
        atr14=round(atr14, 4),
        atr_pct14=round(atr14 / prev_close, 6),
        swing_low=_round_price(swing_low),
        swing_low_as_of=swing_low_as_of,
        box_high_20=round(box_high, 4),
        high_60=_round_price(high_60),
        support_level=_round_price(support),
        support_source=support_source,
        pressure_level=_round_price(pressure),
        pressure_source=pressure_source,
        target_1=_round_price(pressure),
        target_1_source=pressure_source,
        support_zone_low=_round_price(support - observation_buffer) if support else None,
        support_zone_high=_round_price(support + observation_buffer) if support else None,
        breakout_confirmation=_round_price(pressure + noise_buffer) if pressure else None,
        soft_break_confirmation=_round_price(support - noise_buffer) if support else None,
        conservative_target_line=_round_price(pressure - noise_buffer) if pressure else None,
    )


@dataclass(frozen=True)
class EntryPriceCeiling:
    rule_version: str
    target_1: float
    hard_stop: float
    trigger_price: float
    entry_zone_low: float
    entry_zone_high: float
    min_reward_risk: float
    reward_risk_ceiling: float
    allowed_slippage_pct: float
    slippage_ceiling: float
    max_entry_price: float
    executable_zone: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_entry_price_ceiling(
    *,
    target_1: float,
    hard_stop: float,
    trigger_price: float,
    entry_zone: tuple[float, float],
    atr_pct14: float,
    rules: ExecutionLevelConfig = DEFAULT_EXECUTION_LEVEL_RULES,
) -> EntryPriceCeiling:
    """按自然目标、场景止损、入场区与滑点共同限制追价上限。"""

    target = _price(target_1, field_name="target_1")
    stop = _price(hard_stop, field_name="hard_stop")
    trigger = _price(trigger_price, field_name="trigger_price")
    zone_low = _price(entry_zone[0], field_name="entry_zone_low")
    zone_high = _price(entry_zone[1], field_name="entry_zone_high")
    if zone_low > zone_high:
        raise KeyLevelError("入场区下沿不能高于上沿")
    if not stop < trigger < target:
        raise KeyLevelError("最高价计算要求 hard_stop < trigger_price < target_1")
    if not math.isfinite(float(atr_pct14)) or float(atr_pct14) <= 0:
        raise KeyLevelError("atr_pct14 必须大于 0")

    reward_risk_ceiling = (
        target + rules.min_reward_risk * stop
    ) / (1 + rules.min_reward_risk)
    allowed_slippage = min(
        rules.max_slippage_pct,
        max(rules.min_slippage_pct, rules.slippage_atr_pct_factor * float(atr_pct14)),
    )
    slippage_ceiling = trigger * (1 + allowed_slippage)
    max_entry = min(zone_high, reward_risk_ceiling, slippage_ceiling)
    return EntryPriceCeiling(
        rule_version=rules.version,
        target_1=round(target, 4),
        hard_stop=round(stop, 4),
        trigger_price=round(trigger, 4),
        entry_zone_low=round(zone_low, 4),
        entry_zone_high=round(zone_high, 4),
        min_reward_risk=rules.min_reward_risk,
        reward_risk_ceiling=round(reward_risk_ceiling, 4),
        allowed_slippage_pct=round(allowed_slippage, 6),
        slippage_ceiling=round(slippage_ceiling, 4),
        max_entry_price=round(max_entry, 4),
        executable_zone=max_entry >= zone_low,
    )


def remaining_reward_risk(*, price: float, target_1: float, hard_stop: float) -> float:
    current = _price(price, field_name="price")
    target = _price(target_1, field_name="target_1")
    stop = _price(hard_stop, field_name="hard_stop")
    if current <= stop:
        raise KeyLevelError("当前价必须高于场景止损")
    return round((target - current) / (current - stop), 6)


@dataclass(frozen=True)
class MaxEntryGuard:
    """同一 plan_id + plan_revision 的最高价越界只允许单向迁移。"""

    plan_id: str
    plan_revision: int
    max_entry_price: float
    breached: bool = False
    breached_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.plan_id.strip() or self.plan_revision < 1:
            raise KeyLevelError("最高价守卫缺少有效计划命名空间")
        _price(self.max_entry_price, field_name="max_entry_price")
        if self.breached != (self.breached_at is not None):
            raise KeyLevelError("最高价越界状态与时间必须同时存在")
        if self.breached_at is not None:
            require_aware_datetime(self.breached_at, field_name="breached_at")

    def observe(self, latest_price: float, *, observed_at: datetime) -> "MaxEntryGuard":
        latest = _price(latest_price, field_name="latest_price")
        require_aware_datetime(observed_at, field_name="observed_at")
        if self.breached or latest <= self.max_entry_price:
            return self
        return replace(self, breached=True, breached_at=observed_at)


@dataclass(frozen=True)
class E3Confirmation:
    confirmed: bool
    above_support: bool
    above_avg_price: bool
    reversal_shape: bool
    reasons: tuple[str, ...]


def evaluate_e3_confirmation(
    *,
    closed_5m_close: float,
    support_level: float,
    avg_price: float,
    previous_5m_high: float,
    lower_shadow_ratio: float,
    rules: ExecutionLevelConfig = DEFAULT_EXECUTION_LEVEL_RULES,
) -> E3Confirmation:
    """冻结 E3 的三个硬确认，其中分时均价不得降级为软证据。"""

    close = _price(closed_5m_close, field_name="closed_5m_close")
    support = _price(support_level, field_name="support_level")
    average = _price(avg_price, field_name="avg_price")
    previous_high = _price(previous_5m_high, field_name="previous_5m_high")
    try:
        shadow = float(lower_shadow_ratio)
    except (TypeError, ValueError) as error:
        raise KeyLevelError("lower_shadow_ratio 必须是 0 到 1 的比例") from error
    if not math.isfinite(shadow) or not 0 <= shadow <= 1:
        raise KeyLevelError("lower_shadow_ratio 必须是 0 到 1 的比例")

    above_support = close >= support
    above_average = close >= average
    reversal_shape = close > previous_high or shadow >= rules.e3_lower_shadow_ratio
    reasons: list[str] = []
    if not above_support:
        reasons.append(f"闭合5分钟K {close:.4f} 尚未站上支撑 {support:.4f}")
    if not above_average:
        reasons.append(f"闭合5分钟K {close:.4f} 尚未站上分时均价 {average:.4f}")
    if not reversal_shape:
        reasons.append(
            f"未突破前一根高点 {previous_high:.4f}，下影比例 {shadow:.2f} 低于 {rules.e3_lower_shadow_ratio:.2f}"
        )
    return E3Confirmation(
        confirmed=above_support and above_average and reversal_shape,
        above_support=above_support,
        above_avg_price=above_average,
        reversal_shape=reversal_shape,
        reasons=tuple(reasons),
    )
