"""Job 12D 的 E/N/H/R 全场景剧本，以及 Job 12E 的执行数量接入。

本模块只消费已经校验过的执行事实：场景层冻结命中、取消、时间硬规则与
冲突仲裁，数量层再按账户现金、交易单位和真实可卖数量补齐可执行股数。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from enum import StrEnum
from math import ceil
from statistics import median

from shilun.trading.config import (
    DEFAULT_EXECUTION_LEVEL_RULES,
    DEFAULT_EXECUTION_SCENARIO_RULES,
    DEFAULT_EXECUTION_SIZING_RULES,
    ExecutionLevelConfig,
    ExecutionScenarioConfig,
    ExecutionSizingConfig,
)
from shilun.trading.contracts import (
    ACTION_DEFINITIONS,
    ActionType,
    ContractError,
    ExecutionState,
    MatchLevel,
    PositionState,
    RuleState,
    RuleTrace,
    ScenarioCandidate,
    ScenarioState,
    TradingAction,
    require_aware_datetime,
)
from shilun.trading.sizing import (
    EntrySizingInput,
    ExecutionSizingResult,
    ReductionSizingInput,
    ScenarioSizingContext,
    TradingUnitRule,
    calculate_entry_sizing,
    calculate_reduction_sizing,
)


class MarketPermission(StrEnum):
    ATTACK = "attack"
    HOLD = "hold"
    DEFENSE = "defense"
    EMPTY = "empty"


class IntradayMarketState(StrEnum):
    REPAIR = "intraday_repair"
    RANGE = "intraday_range"
    DIVERGENCE = "intraday_divergence"
    RISK_OFF = "intraday_risk_off"


class SectorExecutionState(StrEnum):
    RECOVERY = "recovery_resonance"
    DIVERGENCE = "divergence"
    WEAK = "weak_resonance"
    UNAVAILABLE = "unavailable"


class E7Phase(StrEnum):
    OVERSOLD_CANDIDATE = "t_minus_2"
    STABILIZATION_ENTRY = "t_minus_1"
    NEXT_DAY_EXIT = "t"


SCENARIO_CODES = (
    "E1", "E2", "E3", "E4", "E5", "E6", "E7",
    "N1", "N2", "N3", "N4",
    "H1", "H2", "H3",
    "R1", "R2", "R3", "R4",
)

SCENARIO_TITLES = {
    "E1": "低开承接恢复",
    "E2": "均线回踩收复",
    "E3": "震荡区间支撑反转",
    "E4": "箱体/前高突破",
    "E5": "弱转强",
    "E6": "高开强突破",
    "E7": "超跌反弹隔夜套利",
    "N1": "高开冲高回落",
    "N2": "低开承接失败",
    "N3": "不追/已错过",
    "N4": "环境/数据阻断",
    "H1": "趋势持有",
    "H2": "区间持有",
    "H3": "区间压力兑现",
    "R1": "软破位减仓",
    "R2": "高开失败保护",
    "R3": "目标到达",
    "R4": "硬止损清仓",
}

_SCENARIO_PRIORITY = {code: len(SCENARIO_CODES) - index for index, code in enumerate(SCENARIO_CODES)}


@dataclass(frozen=True)
class ClosedFiveMinuteBar:
    closed_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume_ratio: float | None = None

    def __post_init__(self) -> None:
        require_aware_datetime(self.closed_at, field_name="ClosedFiveMinuteBar.closed_at")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ContractError("闭合 5 分钟 K 价格必须大于 0")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ContractError("闭合 5 分钟 K 的 OHLC 关系不合法")
        if self.volume_ratio is not None and self.volume_ratio < 0:
            raise ContractError("同时间槽量比不能为负数")

    @property
    def upper_shadow_ratio(self) -> float:
        span = self.high - self.low
        return (self.high - max(self.open, self.close)) / span if span > 0 else 0.0

    @property
    def lower_shadow_ratio(self) -> float:
        span = self.high - self.low
        return (min(self.open, self.close) - self.low) / span if span > 0 else 0.0


@dataclass(frozen=True)
class E7ThreeDayFacts:
    phase: E7Phase
    t_minus_2_date: date
    t_minus_1_date: date
    exit_date: date
    market_oversold_strength: float = 0.0
    sector_oversold_strength: float = 0.0
    stock_oversold_strength: float = 0.0
    t_minus_2_low: float = 0.0
    t_minus_1_low: float = 0.0
    sector_up_ratio_history: tuple[float, ...] = ()
    sector_new_low_ratio: float = 1.0
    stock_bars_after_1400: tuple[ClosedFiveMinuteBar, ...] = ()
    sse_three_bars_no_new_low: bool = False
    chinext_three_bars_no_new_low: bool = False
    sse_accelerating_breakdown: bool = False
    chinext_accelerating_breakdown: bool = False
    limit_down_locked: bool = False
    suspended: bool = False
    another_e7_position_open: bool = False

    def __post_init__(self) -> None:
        if not self.t_minus_2_date < self.t_minus_1_date < self.exit_date:
            raise ContractError("E7 必须使用严格有序的 T-2/T-1/T 三日事实")
        ratios = (*self.sector_up_ratio_history, self.sector_new_low_ratio)
        if any(not 0 <= value <= 1 for value in ratios):
            raise ContractError("E7 板块比例必须在 0 和 1 之间")
        if any(
            self.stock_bars_after_1400[index].closed_at
            > self.stock_bars_after_1400[index + 1].closed_at
            for index in range(len(self.stock_bars_after_1400) - 1)
        ):
            raise ContractError("E7 个股 5 分钟事实必须按时间升序")


@dataclass(frozen=True)
class ScenarioExecutionPlan:
    ticker: str
    position_state: PositionState
    previous_close: float
    open_price: float
    atr: float
    support: float
    ma5: float
    ma8: float
    pressure: float
    first_target: float
    soft_risk: float
    hard_stop: float
    max_acceptable_price: float
    entry_zone: tuple[float, float]
    current_quantity: int = 0
    sellable_quantity: int | None = 0
    lot_size: int = 100
    missed_locked: bool = False

    def __post_init__(self) -> None:
        prices = (
            self.previous_close, self.open_price, self.atr, self.support,
            self.ma5, self.ma8, self.pressure, self.first_target,
            self.soft_risk, self.hard_stop, self.max_acceptable_price,
            *self.entry_zone,
        )
        if not self.ticker.strip() or any(value <= 0 for value in prices):
            raise ContractError("全场景计划缺少股票代码或有效价格")
        if self.entry_zone[0] > self.entry_zone[1]:
            raise ContractError("入场区下界不能高于上界")
        if self.current_quantity < 0 or (
            self.sellable_quantity is not None and self.sellable_quantity < 0
        ):
            raise ContractError("持仓数量不能为负数")
        if self.sellable_quantity is not None and self.sellable_quantity > self.current_quantity:
            raise ContractError("可卖数量不能超过总持仓")
        if self.lot_size <= 0:
            raise ContractError("一手股数必须大于 0")
        if self.position_state is PositionState.POSITIONED and self.current_quantity <= 0:
            raise ContractError("已持仓状态必须有正的总股数")
        if self.position_state in {PositionState.FLAT, PositionState.CLOSED} and self.current_quantity:
            raise ContractError("空仓或已清仓状态不能保留总股数")


@dataclass(frozen=True)
class ScenarioMarketFacts:
    computed_at: datetime
    current_price: float
    day_low: float
    vwap: float
    closed_bars: tuple[ClosedFiveMinuteBar, ...]
    volume_ratio: float | None
    relative_strength: float | None
    market_permission: MarketPermission
    intraday_market_state: IntradayMarketState
    sector_state: SectorExecutionState
    required_data_fresh: bool = True
    freshness_failed_sources: tuple[str, ...] = ()
    quote_age_seconds: float = 0.0
    price_limit_locked: bool = False
    suspended: bool = False
    e7: E7ThreeDayFacts | None = None
    e7_support_deteriorating: bool = False
    cancelled_scenarios: frozenset[str] = field(default_factory=frozenset)
    source_timestamps: tuple[datetime, ...] = ()

    def __post_init__(self) -> None:
        require_aware_datetime(self.computed_at, field_name="ScenarioMarketFacts.computed_at")
        if min(self.current_price, self.day_low, self.vwap) <= 0:
            raise ContractError("全场景事实价格必须大于 0")
        if self.day_low > self.current_price:
            raise ContractError("日内最低价不能高于当前价")
        if self.volume_ratio is not None and self.volume_ratio < 0:
            raise ContractError("同时间槽量比不能为负数")
        if self.quote_age_seconds < 0:
            raise ContractError("报价年龄不能为负数")
        unknown = set(self.cancelled_scenarios) - set(SCENARIO_CODES)
        if unknown:
            raise ContractError(f"未知取消场景：{sorted(unknown)}")
        for index, source_at in enumerate(self.source_timestamps):
            require_aware_datetime(
                source_at,
                field_name=f"ScenarioMarketFacts.source_timestamps[{index}]",
            )
        if any(
            self.closed_bars[index].closed_at > self.closed_bars[index + 1].closed_at
            for index in range(len(self.closed_bars) - 1)
        ):
            raise ContractError("闭合 5 分钟 K 必须按时间升序")
        if any(bar.closed_at > self.computed_at for bar in self.closed_bars):
            raise ContractError("确认不得使用计算时刻之后的 5 分钟 K")
        if self.e7 is not None and any(
            bar.closed_at > self.computed_at for bar in self.e7.stock_bars_after_1400
        ):
            raise ContractError("E7 确认不得使用计算时刻之后的 5 分钟 K")


@dataclass(frozen=True)
class ScenarioSignal:
    candidate: ScenarioCandidate
    action: TradingAction
    execution_state: ExecutionState
    precedence: int
    is_risk: bool
    confirmed_at: datetime
    reason: str
    deadline: str | None = None

    def __post_init__(self) -> None:
        require_aware_datetime(self.confirmed_at, field_name="ScenarioSignal.confirmed_at")
        if not self.reason.strip():
            raise ContractError("场景信号必须给出仲裁原因")

    @property
    def satisfied_rule_count(self) -> int:
        return sum(trace.state is RuleState.SATISFIED for trace in self.candidate.traces)


@dataclass(frozen=True)
class SuppressedScenario:
    scenario_id: str
    suppressed_by: str
    reason: str


@dataclass(frozen=True)
class ScenarioDecision:
    ticker: str
    computed_at: datetime
    action: TradingAction
    execution_state: ExecutionState
    primary_scenario: ScenarioCandidate | None
    candidate_scenarios: tuple[ScenarioCandidate, ...]
    suppressed_scenarios: tuple[SuppressedScenario, ...]
    reasons: tuple[str, ...]
    preferred_execution_window: bool = False
    deadline: str | None = None
    sizing: ExecutionSizingResult | None = None

    def scenario(self, scenario_id: str) -> ScenarioCandidate:
        return next(item for item in self.candidate_scenarios if item.scenario_id == scenario_id)


def _trace(
    rule_id: str,
    label: str,
    result: bool | None,
    reason: str,
    *,
    actual=None,
    operator: str = "",
    expected=None,
    source: str = "execution_facts",
) -> RuleTrace:
    state = RuleState.PENDING if result is None else RuleState.SATISFIED if result else RuleState.UNSATISFIED
    return RuleTrace(
        rule_id=rule_id,
        label=label,
        state=state,
        reason=reason,
        actual=actual,
        operator=operator,
        expected=expected,
        evidence_source=source,
    )


def _candidate(
    code: str,
    traces: tuple[RuleTrace, ...],
    *,
    triggered: bool = False,
    waiting: bool = False,
    approaching: bool = False,
    invalidated: bool = False,
    cancelled: bool = False,
    cancel_conditions: tuple[str, ...] = (),
) -> ScenarioCandidate:
    if invalidated or cancelled:
        state = ScenarioState.INVALIDATED
        match = MatchLevel.LOW
    elif triggered:
        state = ScenarioState.TRIGGERED
        match = MatchLevel.HIGH
    elif waiting:
        state = ScenarioState.WAITING_CONFIRMATION
        match = MatchLevel.MEDIUM
    elif approaching:
        state = ScenarioState.APPROACHING
        match = MatchLevel.MEDIUM
    else:
        state = ScenarioState.FAR
        match = MatchLevel.LOW
    return ScenarioCandidate(
        scenario_id=code,
        title=f"{code} {SCENARIO_TITLES[code]}",
        priority=_SCENARIO_PRIORITY[code],
        state=state,
        match_level=match,
        traces=traces,
        cancel_conditions=cancel_conditions,
    )


def _action(
    action_type: ActionType,
    *,
    target_quantity: int | None = None,
    change_quantity: int | None = None,
    execution_zone: tuple[float, float] | None = None,
) -> TradingAction:
    return TradingAction(
        action_type=action_type.value,
        label=ACTION_DEFINITIONS[action_type].label,
        target_quantity=target_quantity,
        change_quantity=change_quantity,
        execution_zone=execution_zone,
    )


def _clock(value: datetime) -> time:
    return value.time().replace(tzinfo=None)


def _last_bar_time(facts: ScenarioMarketFacts) -> datetime:
    return facts.closed_bars[-1].closed_at if facts.closed_bars else facts.computed_at


def _is_continuous_auction(clock: time) -> bool:
    return time(9, 30) <= clock <= time(11, 30) or time(13, 0) <= clock <= time(15, 0)


def arbitrate_scenarios(
    signals: tuple[ScenarioSignal, ...],
) -> tuple[ScenarioSignal | None, tuple[SuppressedScenario, ...]]:
    """按层级、风险、硬规则数量、确认新鲜度和稳定优先级仲裁。"""

    if not signals:
        return None, ()
    ordered = sorted(
        signals,
        key=lambda item: (
            item.precedence,
            int(item.is_risk),
            item.satisfied_rule_count,
            item.confirmed_at.timestamp(),
            item.candidate.priority,
        ),
        reverse=True,
    )
    primary = ordered[0]
    suppressed: list[SuppressedScenario] = []
    for item in ordered[1:]:
        if primary.precedence != item.precedence:
            reason = f"{primary.candidate.scenario_id} 的仲裁层级更高"
        elif primary.is_risk != item.is_risk:
            reason = f"{primary.candidate.scenario_id} 是同层风险剧本"
        elif primary.satisfied_rule_count != item.satisfied_rule_count:
            reason = f"{primary.candidate.scenario_id} 完成的硬规则更多"
        elif primary.confirmed_at != item.confirmed_at:
            reason = f"{primary.candidate.scenario_id} 的确认时间更新"
        else:
            reason = f"{primary.candidate.scenario_id} 使用稳定场景优先级胜出"
        suppressed.append(SuppressedScenario(
            scenario_id=item.candidate.scenario_id,
            suppressed_by=primary.candidate.scenario_id,
            reason=reason,
        ))
    return primary, tuple(suppressed)


class FullScenarioPlaybook:
    playbook_id = "execution_scenarios_v0_3"
    version = "0.3.0"

    def __init__(
        self,
        rules: ExecutionScenarioConfig = DEFAULT_EXECUTION_SCENARIO_RULES,
        level_rules: ExecutionLevelConfig = DEFAULT_EXECUTION_LEVEL_RULES,
        sizing_rules: ExecutionSizingConfig = DEFAULT_EXECUTION_SIZING_RULES,
    ) -> None:
        self.rules = rules
        self.level_rules = level_rules
        self.sizing_rules = sizing_rules

    def evaluate(
        self,
        plan: ScenarioExecutionPlan,
        facts: ScenarioMarketFacts,
        sizing_context: ScenarioSizingContext | None = None,
    ) -> ScenarioDecision:
        clock = _clock(facts.computed_at)
        latest = facts.closed_bars[-1] if facts.closed_bars else None
        previous = facts.closed_bars[-2] if len(facts.closed_bars) >= 2 else None
        last_two = facts.closed_bars[-2:]
        quote_fresh = facts.quote_age_seconds <= self.rules.quote_max_age_seconds
        gap_pct = plan.open_price / plan.previous_close - 1.0
        low_open = gap_pct <= self.rules.gap_down_pct
        high_open = gap_pct >= self.rules.gap_up_pct
        volume_ratio = latest.volume_ratio if latest and latest.volume_ratio is not None else facts.volume_ratio
        support_zone_high = plan.support + self.level_rules.support_observation_atr * plan.atr
        support_touched = facts.day_low <= support_zone_high
        support_failed = len(last_two) == 2 and all(
            bar.close < plan.support and bar.close < facts.vwap for bar in last_two
        )
        hard_stop_hit = facts.current_price <= plan.hard_stop
        max_price_missed = plan.missed_locked or facts.current_price > plan.max_acceptable_price
        denominator = facts.current_price - plan.hard_stop
        remaining_rr = (
            (plan.first_target - facts.current_price) / denominator
            if denominator > 0 else float("-inf")
        )
        reward_risk_failed = remaining_rr < self.level_rules.min_reward_risk
        normal_permission = facts.market_permission in {MarketPermission.ATTACK, MarketPermission.HOLD}
        sector_blocked = facts.sector_state in {
            SectorExecutionState.WEAK,
            SectorExecutionState.UNAVAILABLE,
        }
        market_blocked = facts.intraday_market_state is IntradayMarketState.RISK_OFF
        data_blocked = not facts.required_data_fresh or facts.suspended
        common_entry_blocked = not normal_permission or sector_blocked or market_blocked or data_blocked
        in_entry_window = self.rules.new_entry_start <= clock < self.rules.new_entry_cutoff
        entry_expired = clock >= self.rules.new_entry_cutoff
        before_target_cutoff = clock <= self.rules.target_trim_cutoff
        before_soft_cutoff = clock <= self.rules.soft_risk_cutoff
        preferred_sell = self.rules.preferred_sell_start <= clock <= self.rules.preferred_sell_end
        latest_above_pressure = bool(latest and latest.high >= plan.pressure)
        latest_upper_failure = bool(
            latest and latest.upper_shadow_ratio >= self.rules.upper_shadow_failure_ratio
        )
        n1_hit = bool(high_open and latest_above_pressure and latest_upper_failure and latest.close < facts.vwap) if latest else False
        n2_hit = low_open and support_failed
        e4_confirmed = bool(
            latest
            and latest.close >= plan.pressure + self.level_rules.base_noise_atr * plan.atr
            and volume_ratio is not None
            and volume_ratio >= self.rules.breakout_volume_ratio
            and (
                facts.market_permission is MarketPermission.ATTACK
                or (
                    facts.market_permission is MarketPermission.HOLD
                    and facts.relative_strength is not None
                    and facts.relative_strength > 0
                    and facts.sector_state is not SectorExecutionState.WEAK
                )
            )
        )

        candidates: dict[str, ScenarioCandidate] = {}
        signals: list[ScenarioSignal] = []

        def store(candidate: ScenarioCandidate) -> ScenarioCandidate:
            candidates[candidate.scenario_id] = candidate
            return candidate

        def signal(
            candidate: ScenarioCandidate,
            action: TradingAction,
            execution_state: ExecutionState,
            precedence: int,
            *,
            risk: bool,
            reason: str,
            deadline: str | None = None,
            confirmed_at: datetime | None = None,
        ) -> None:
            if candidate.scenario_id in facts.cancelled_scenarios and candidate.scenario_id != "R4":
                return
            signals.append(ScenarioSignal(
                candidate=candidate,
                action=action,
                execution_state=execution_state,
                precedence=precedence,
                is_risk=risk,
                confirmed_at=confirmed_at or _last_bar_time(facts),
                reason=reason,
                deadline=deadline,
            ))

        # R4 使用最新可信报价，不等待闭合 K、指数或板块。
        r4_candidate = store(_candidate(
            "R4",
            (
                _trace("r4_positioned", "存在可退出持仓", plan.position_state is PositionState.POSITIONED, "持仓事实已核验" if plan.position_state is PositionState.POSITIONED else "没有活动持仓", actual=plan.current_quantity),
                _trace("r4_price", "最新价触及场景硬止损", hard_stop_hit, "已触及硬止损" if hard_stop_hit else "硬止损未触发", actual=facts.current_price, operator="<=", expected=plan.hard_stop, source="stock_quote"),
                _trace("r4_quote_fresh", "风险报价不超过 45 秒", quote_fresh, "报价可用于硬止损" if quote_fresh else "报价过期，必须人工核价", actual=facts.quote_age_seconds, operator="<=", expected=self.rules.quote_max_age_seconds, source="stock_quote"),
            ),
            triggered=plan.position_state is PositionState.POSITIONED and hard_stop_hit,
            cancelled=False,
        ))
        if r4_candidate.state is ScenarioState.TRIGGERED and _is_continuous_auction(clock):
            if quote_fresh:
                signal(
                    r4_candidate,
                    _action(
                        ActionType.RISK_EXIT,
                        target_quantity=0,
                        change_quantity=(
                            -plan.sellable_quantity
                            if plan.sellable_quantity is not None else None
                        ),
                    ),
                    ExecutionState.EXIT_PENDING,
                    100,
                    risk=True,
                    reason="可信最新价触及场景硬止损，立即清仓",
                )
            else:
                signal(
                    r4_candidate,
                    _action(ActionType.MANUAL_VERIFY_RISK),
                    ExecutionState.DATA_BLOCKED,
                    100,
                    risk=True,
                    reason="可见价格触及硬止损但报价超过 45 秒，立即人工核价",
                )

        # E7 使用独立的三日事实，并在 T 日复用同一场景执行强制退出。
        e7_traces, e7_triggered, e7_waiting, e7_invalidated = self._evaluate_e7(
            plan, facts, in_entry_window=in_entry_window, entry_expired=entry_expired
        )
        e7_candidate = store(_candidate(
            "E7",
            e7_traces,
            triggered=e7_triggered,
            waiting=e7_waiting,
            invalidated=e7_invalidated,
            cancelled="E7" in facts.cancelled_scenarios,
            cancel_conditions=("T-1 再创新低", "价格越过最高可接受价", "15:00 计划过期"),
        ))
        e7_is_exit = bool(
            facts.e7
            and facts.e7.phase is E7Phase.NEXT_DAY_EXIT
            and facts.computed_at.date() == facts.e7.exit_date
            and plan.position_state is PositionState.POSITIONED
        )
        if e7_is_exit and e7_candidate.state is ScenarioState.TRIGGERED:
            e7_signal = self._e7_exit_signal(plan, facts, e7_candidate, quote_fresh)
            if e7_signal is not None:
                signals.append(e7_signal)
        elif e7_candidate.state is ScenarioState.TRIGGERED and plan.position_state is PositionState.FLAT:
            signal(
                e7_candidate,
                _action(ActionType.PREPARE_ENTRY, execution_zone=plan.entry_zone),
                ExecutionState.ENTRY_READY,
                60,
                risk=False,
                reason="T-2 超跌与 T-1 三组维稳事实全部完成，反弹权限生效",
                deadline="15:00",
            )
        elif e7_candidate.state is ScenarioState.WAITING_CONFIRMATION and plan.position_state is PositionState.FLAT:
            signal(
                e7_candidate,
                _action(ActionType.WAIT_OVERSOLD_STABILIZATION),
                ExecutionState.CONFIRMING,
                50,
                risk=False,
                reason="E7 仅形成候选，等待 T-1 14:15 后的三组维稳事实",
                deadline="15:00",
            )
        elif (
            e7_candidate.state is ScenarioState.INVALIDATED
            and plan.position_state is PositionState.FLAT
            and entry_expired
            and facts.e7 is not None
            and facts.e7.phase is E7Phase.STABILIZATION_ENTRY
        ):
            signal(
                e7_candidate,
                _action(ActionType.NO_ACTION, target_quantity=0),
                ExecutionState.EXPIRED,
                65,
                risk=True,
                reason="E7 到 15:00 仍未完成建仓，今日计划过期",
            )

        # E1–E6 的确认和自然取消。
        latest_close = latest.close if latest else facts.current_price
        entry_cancel_common = common_entry_blocked or hard_stop_hit or max_price_missed or reward_risk_failed

        e1_trigger = low_open and support_touched
        e1_confirm = bool(e1_trigger and latest and latest.close >= plan.support and latest.close >= facts.vwap)
        e1_cancel = e1_trigger and (support_failed or entry_cancel_common or entry_expired)
        e1 = store(_candidate(
            "E1",
            (
                _trace("e1_environment", "普通权限、指数和板块允许", not common_entry_blocked, "环境允许低开修复" if not common_entry_blocked else "环境阻断普通新仓"),
                _trace("e1_low_open", "低开达到负缺口阈值", low_open, "低开成立" if low_open else "不是低开", actual=gap_pct, operator="<=", expected=self.rules.gap_down_pct, source="stock_quote"),
                _trace("e1_support_touch", "最低价进入支撑观察区", support_touched, "已进入支撑观察区" if support_touched else "尚未触及支撑区", actual=facts.day_low, operator="<=", expected=support_zone_high),
                _trace("e1_recovery", "闭合 K 站上支撑和分时均价", e1_confirm if latest else None, "低开承接恢复确认" if e1_confirm else "等待同时站上支撑与均价", actual=latest_close, expected=max(plan.support, facts.vwap), source="stock_5m"),
            ),
            triggered=e1_confirm,
            waiting=e1_trigger and not e1_confirm,
            invalidated=e1_cancel,
            cancelled="E1" in facts.cancelled_scenarios,
            cancel_conditions=("两根闭合 K 低于支撑和均价", "环境关闭", "硬止损或追价上限失效"),
        ))

        pullback_levels = tuple(
            level for level in (plan.ma5, plan.ma8, plan.support)
            if facts.day_low <= level + self.level_rules.support_observation_atr * plan.atr
        )
        pullback_touched = bool(pullback_levels)
        pullback_key = max(pullback_levels) if pullback_levels else plan.support
        e2_confirm = bool(
            pullback_touched and latest and latest.close >= pullback_key
            and (facts.vwap <= pullback_key or latest.close >= facts.vwap)
        )
        e2_cancel = pullback_touched and (entry_cancel_common or entry_expired)
        e2 = store(_candidate(
            "E2",
            (
                _trace("e2_environment", "日级进攻或观察持有", normal_permission and not market_blocked and not sector_blocked and not data_blocked, "环境允许回踩收复" if not common_entry_blocked else "环境阻断普通新仓"),
                _trace("e2_pullback_touch", "最低价进入均线/支撑观察区", pullback_touched, "已触发回踩观察" if pullback_touched else "尚未回踩", actual=facts.day_low, expected=(plan.ma5, plan.ma8, plan.support)),
                _trace("e2_reclaim", "闭合 K 收复关键位和必要均价", e2_confirm if latest else None, "回踩收复确认" if e2_confirm else "等待收复关键位", actual=latest_close, expected=max(pullback_key, facts.vwap if facts.vwap > pullback_key else pullback_key), source="stock_5m"),
            ),
            triggered=e2_confirm,
            waiting=pullback_touched and not e2_confirm,
            invalidated=e2_cancel,
            cancelled="E2" in facts.cancelled_scenarios,
            cancel_conditions=("板块退潮或指数风险关闭", "触发场景止损", "超过最高可接受价"),
        ))

        e3_environment = facts.market_permission is MarketPermission.HOLD or facts.intraday_market_state is IntradayMarketState.RANGE
        e3_reversal = bool(
            latest and previous and latest.close >= plan.support and latest.close >= facts.vwap
            and (latest.close > previous.high or latest.lower_shadow_ratio >= self.level_rules.e3_lower_shadow_ratio)
        )
        e3_trigger = e3_environment and support_touched and not hard_stop_hit
        e3_cancel = e3_trigger and (support_failed or entry_cancel_common or entry_expired)
        e3 = store(_candidate(
            "E3",
            (
                _trace("e3_environment", "观察持有或盘中震荡", e3_environment, "区间环境成立" if e3_environment else "不是区间执行环境"),
                _trace("e3_support_touch", "最低价触达区间支撑", support_touched and not hard_stop_hit, "区间支撑已触达" if support_touched else "尚未触达区间支撑", actual=facts.day_low, expected=support_zone_high),
                _trace("e3_above_vwap", "闭合 K 强制站上分时均价", bool(latest and latest.close >= facts.vwap) if latest else None, "已站上分时均价" if latest and latest.close >= facts.vwap else "分时均价硬确认未完成", actual=latest_close, expected=facts.vwap, source="intraday_trend"),
                _trace("e3_reversal", "突破前高或下影反转", e3_reversal if latest and previous else None, "区间反转确认" if e3_reversal else "等待反转 K", source="stock_5m"),
            ),
            triggered=e3_trigger and e3_reversal,
            waiting=e3_trigger and not e3_reversal,
            invalidated=e3_cancel,
            cancelled="E3" in facts.cancelled_scenarios,
            cancel_conditions=("两根闭合 K 低于支撑", "环境关闭", "剩余盈亏比不足"),
        ))

        e4_trigger = latest_above_pressure
        e4_cancel = e4_trigger and (
            entry_cancel_common or entry_expired or bool(latest and latest.close < plan.pressure)
        )
        e4 = store(_candidate(
            "E4",
            (
                _trace("e4_environment", "进攻或加强确认的观察持有", normal_permission and not market_blocked and not sector_blocked and not data_blocked, "突破权限可用" if normal_permission and not common_entry_blocked else "突破权限关闭"),
                _trace("e4_pressure_touch", "价格触达压力位", e4_trigger, "压力位已触达" if e4_trigger else "尚未触达压力位", actual=latest.high if latest else facts.current_price, operator=">=", expected=plan.pressure),
                _trace("e4_breakout_close", "闭合 K 站上压力位加 0.15 ATR", bool(latest and latest.close >= plan.pressure + self.level_rules.base_noise_atr * plan.atr) if latest else None, "价格确认完成" if latest and latest.close >= plan.pressure + self.level_rules.base_noise_atr * plan.atr else "等待价格确认", expected=plan.pressure + self.level_rules.base_noise_atr * plan.atr, source="stock_5m"),
                _trace("e4_volume", "同时间槽量比不低于 1.2", volume_ratio is not None and volume_ratio >= self.rules.breakout_volume_ratio if e4_trigger else False, "放量确认完成" if volume_ratio is not None and volume_ratio >= self.rules.breakout_volume_ratio else "等待量比确认", actual=volume_ratio, operator=">=", expected=self.rules.breakout_volume_ratio),
                _trace("e4_hold_overlay", "观察持有环境加强相对强弱和板块确认", facts.market_permission is not MarketPermission.HOLD or (facts.relative_strength is not None and facts.relative_strength > 0 and facts.sector_state is not SectorExecutionState.WEAK), "观察持有加强确认通过" if facts.market_permission is not MarketPermission.HOLD or (facts.relative_strength is not None and facts.relative_strength > 0 and facts.sector_state is not SectorExecutionState.WEAK) else "观察持有加强确认未通过", actual=facts.relative_strength),
            ),
            triggered=e4_trigger and e4_confirmed,
            waiting=e4_trigger and not e4_confirmed,
            invalidated=e4_cancel,
            cancelled="E4" in facts.cancelled_scenarios,
            cancel_conditions=("闭合 K 回落压力位下", "板块退潮或指数风险关闭", "超过最高可接受价"),
        ))

        weak_start = low_open or facts.day_low < plan.previous_close
        weak_barrier = max(plan.open_price, plan.previous_close, facts.vwap)
        e5_trigger = weak_start and facts.current_price >= weak_barrier - self.level_rules.support_observation_atr * plan.atr
        e5_confirm = bool(
            e5_trigger and latest and latest.close >= weak_barrier
            and facts.relative_strength is not None and facts.relative_strength > 0
        )
        e5_cancel = e5_trigger and (entry_cancel_common or entry_expired or support_failed)
        e5 = store(_candidate(
            "E5",
            (
                _trace("e5_weak_start", "早盘低开或一度弱于昨收", weak_start, "弱势起点成立" if weak_start else "没有弱转强起点"),
                _trace("e5_approach", "价格向三线最大值靠近", e5_trigger, "进入弱转强确认区" if e5_trigger else "尚未靠近确认线", actual=facts.current_price, expected=weak_barrier),
                _trace("e5_reclaim", "闭合 K 站上开盘、昨收和均价", bool(latest and latest.close >= weak_barrier) if latest else None, "三线收复完成" if latest and latest.close >= weak_barrier else "等待三线收复", expected=weak_barrier, source="stock_5m"),
                _trace("e5_relative_strength", "相对匹配指数强弱大于 0", facts.relative_strength is not None and facts.relative_strength > 0 if e5_trigger else False, "相对强弱确认" if facts.relative_strength is not None and facts.relative_strength > 0 else "相对强弱未确认", actual=facts.relative_strength, operator=">", expected=0),
            ),
            triggered=e5_confirm,
            waiting=e5_trigger and not e5_confirm,
            invalidated=e5_cancel,
            cancelled="E5" in facts.cancelled_scenarios,
            cancel_conditions=("再次跌回均价下且两根低于支撑", "超过最高可接受价"),
        ))

        gap_not_filled = facts.day_low > plan.previous_close
        e6_trigger = high_open and gap_not_filled and latest_above_pressure
        e6_confirm = bool(
            e6_trigger and latest and latest.close >= plan.pressure and latest.close >= facts.vwap
            and volume_ratio is not None and volume_ratio >= self.rules.breakout_volume_ratio
            and latest.upper_shadow_ratio < self.rules.upper_shadow_failure_ratio
            and facts.market_permission is MarketPermission.ATTACK
        )
        e6_cancel = e6_trigger and (entry_cancel_common or entry_expired or latest_upper_failure)
        e6 = store(_candidate(
            "E6",
            (
                _trace("e6_attack_high_open", "高开且日级进攻", high_open and facts.market_permission is MarketPermission.ATTACK, "高开进攻环境成立" if high_open and facts.market_permission is MarketPermission.ATTACK else "E6 只在高开进攻环境执行", actual=gap_pct),
                _trace("e6_gap_hold", "高开缺口未完全回补并触压", gap_not_filled and latest_above_pressure, "缺口与压力触发成立" if gap_not_filled and latest_above_pressure else "缺口或压力触发未完成"),
                _trace("e6_close_vwap", "闭合 K 站上压力和均价", bool(latest and latest.close >= max(plan.pressure, facts.vwap)) if latest else None, "价格确认完成" if latest and latest.close >= max(plan.pressure, facts.vwap) else "等待价格确认", expected=max(plan.pressure, facts.vwap), source="stock_5m"),
                _trace("e6_volume_shadow", "量比达标且上影小于 0.40", bool(latest and volume_ratio is not None and volume_ratio >= self.rules.breakout_volume_ratio and latest.upper_shadow_ratio < self.rules.upper_shadow_failure_ratio) if latest else None, "量价形态确认" if e6_confirm else "等待量比或上影确认", actual={"volume_ratio": volume_ratio, "upper_shadow": latest.upper_shadow_ratio if latest else None}),
            ),
            triggered=e6_confirm,
            waiting=e6_trigger and not e6_confirm,
            invalidated=e6_cancel,
            cancelled="E6" in facts.cancelled_scenarios,
            cancel_conditions=("高开失败上影", "环境关闭", "开盘即超过盈亏比最高价"),
        ))

        wait_actions = {
            "E1": ActionType.WAIT_LOW_OPEN_RECOVERY,
            "E2": ActionType.WAIT_PULLBACK_RECLAIM,
            "E3": ActionType.WAIT_RANGE_REVERSAL,
            "E4": ActionType.WAIT_BREAKOUT,
            "E5": ActionType.WAIT_WEAK_TO_STRONG,
            "E6": ActionType.WAIT_BREAKOUT,
        }
        entry_was_active = {
            "E1": e1_trigger,
            "E2": pullback_touched,
            "E3": e3_trigger,
            "E4": e4_trigger,
            "E5": e5_trigger,
            "E6": e6_trigger,
        }
        for code, candidate in (("E1", e1), ("E2", e2), ("E3", e3), ("E4", e4), ("E5", e5), ("E6", e6)):
            if plan.position_state is not PositionState.FLAT or code in facts.cancelled_scenarios:
                continue
            if candidate.state is ScenarioState.TRIGGERED:
                if in_entry_window:
                    signal(candidate, _action(ActionType.PREPARE_ENTRY, execution_zone=plan.entry_zone), ExecutionState.ENTRY_READY, 60, risk=False, reason=f"{code} 尾盘硬确认完成且价格可执行", deadline="15:00")
                else:
                    signal(candidate, _action(wait_actions[code]), ExecutionState.CONFIRMING, 50, risk=False, reason=f"{code} 已形成候选，14:00 后必须重新确认", deadline="15:00")
            elif candidate.state is ScenarioState.WAITING_CONFIRMATION:
                signal(candidate, _action(wait_actions[code]), ExecutionState.CONFIRMING, 50, risk=False, reason=f"{code} 触发成立，等待闭合 5 分钟硬确认", deadline="15:00")
            elif candidate.state is ScenarioState.INVALIDATED and entry_expired and entry_was_active[code]:
                signal(candidate, _action(ActionType.NO_ACTION, target_quantity=0), ExecutionState.EXPIRED, 65, risk=True, reason=f"{code} 到 15:00 未成交，今日新仓计划过期")
            elif candidate.state is ScenarioState.INVALIDATED and not (max_price_missed or common_entry_blocked or support_failed or entry_expired):
                signal(candidate, _action(ActionType.CANCEL_ENTRY, target_quantity=0), ExecutionState.CANCELLED, 65, risk=True, reason=f"{code} 的场景取消条件已成立")

        # N1–N4 覆盖失败、不可追价和环境/数据阻断。
        n1 = store(_candidate(
            "N1",
            (
                _trace("n1_high_open", "高开后触及压力", high_open and latest_above_pressure, "高开触压成立" if high_open and latest_above_pressure else "未形成高开触压"),
                _trace("n1_upper_shadow", "上影比例不低于 0.40", latest_upper_failure if latest else None, "冲高回落形态成立" if latest_upper_failure else "上影不足", actual=latest.upper_shadow_ratio if latest else None, operator=">=", expected=self.rules.upper_shadow_failure_ratio),
                _trace("n1_below_vwap", "闭合 K 低于分时均价", bool(latest and latest.close < facts.vwap) if latest else None, "闭合 K 跌回均价下" if latest and latest.close < facts.vwap else "尚未跌回均价下", source="intraday_trend"),
            ),
            triggered=n1_hit,
            cancelled="N1" in facts.cancelled_scenarios,
        ))
        n2 = store(_candidate(
            "N2",
            (
                _trace("n2_low_open", "低开承接环境", low_open, "低开成立" if low_open else "不是低开"),
                _trace("n2_two_below", "连续两根闭合 K 低于支撑和均价", support_failed if len(last_two) == 2 else None, "低开承接失败" if support_failed else "等待两根确认", actual=tuple(bar.close for bar in last_two), expected={"support": plan.support, "vwap": facts.vwap}, source="stock_5m"),
            ),
            triggered=n2_hit,
            waiting=low_open and len(last_two) == 1 and last_two[-1].close < min(plan.support, facts.vwap),
            cancelled="N2" in facts.cancelled_scenarios,
        ))
        n3_hit = max_price_missed or reward_risk_failed
        n3 = store(_candidate(
            "N3",
            (
                _trace("n3_max_price", "当前价不超过最高可接受价", not max_price_missed, "价格仍可执行" if not max_price_missed else "最高可接受价已永久失效", actual=facts.current_price, operator="<=", expected=plan.max_acceptable_price),
                _trace("n3_reward_risk", "剩余盈亏比不低于 2", not reward_risk_failed, "剩余盈亏比合格" if not reward_risk_failed else "剩余盈亏比不足", actual=remaining_rr, operator=">=", expected=self.level_rules.min_reward_risk),
            ),
            triggered=n3_hit,
            cancelled="N3" in facts.cancelled_scenarios,
        ))
        n4_hit = common_entry_blocked or not quote_fresh
        n4 = store(_candidate(
            "N4",
            (
                _trace("n4_permission", "普通日级权限允许", normal_permission, "普通权限开放" if normal_permission else "日级权限关闭普通新仓", actual=facts.market_permission.value),
                _trace("n4_intraday", "双指数非风险关闭", not market_blocked, "盘中权限可用" if not market_blocked else "双指数风险关闭", actual=facts.intraday_market_state.value),
                _trace("n4_sector", "板块非退潮且样本可用", not sector_blocked, "板块执行事实可用" if not sector_blocked else "板块走弱或样本不足", actual=facts.sector_state.value),
                _trace("n4_freshness", "必要数据和报价新鲜", not data_blocked and quote_fresh, "数据新鲜" if not data_blocked and quote_fresh else "必要数据过期或缺失", actual=facts.quote_age_seconds, expected=self.rules.quote_max_age_seconds),
            ),
            triggered=n4_hit,
            cancelled="N4" in facts.cancelled_scenarios,
        ))
        if plan.position_state is PositionState.FLAT:
            if n1.state is ScenarioState.TRIGGERED:
                signal(n1, _action(ActionType.CANCEL_ENTRY, target_quantity=0), ExecutionState.CANCELLED, 80, risk=True, reason="高开冲高回落，取消空仓建仓计划")
            if n2.state is ScenarioState.TRIGGERED:
                signal(n2, _action(ActionType.CANCEL_ENTRY, target_quantity=0), ExecutionState.CANCELLED, 80, risk=True, reason="低开承接连续两根失败，取消建仓")
            if n3.state is ScenarioState.TRIGGERED:
                signal(n3, _action(ActionType.NO_CHASE, target_quantity=0), ExecutionState.MISSED, 75, risk=True, reason="最高价或最低盈亏比失效，同计划永久不追")
            # 防守/空仓权限本身不压制已经完成独立三日确认的 E7。
            e7_permission_exception = (
                e7_candidate.state is ScenarioState.TRIGGERED
                and facts.e7 is not None
                and facts.e7.phase is E7Phase.STABILIZATION_ENTRY
                and not (sector_blocked or market_blocked or data_blocked or not quote_fresh)
            )
            if n4.state is ScenarioState.TRIGGERED and not e7_permission_exception:
                signal(n4, _action(ActionType.PAUSE_NEW_ACTION), ExecutionState.DATA_BLOCKED if data_blocked or not quote_fresh else ExecutionState.CANCELLED, 70, risk=True, reason="环境或必要数据阻断新动作")
        elif plan.position_state is PositionState.POSITIONED and n4.state is ScenarioState.TRIGGERED:
            signal(
                n4,
                _action(ActionType.PAUSE_NEW_ACTION),
                ExecutionState.DATA_BLOCKED,
                70,
                risk=True,
                reason="环境或数据异常暂停普通持仓动作；可信硬止损仍可覆盖",
            )

        # H/R 场景只对真实持仓生效。
        soft_threshold = plan.soft_risk - self.level_rules.base_noise_atr * plan.atr
        soft_break = len(last_two) == 2 and all(bar.close < soft_threshold for bar in last_two) and latest_close > plan.hard_stop
        pressure_failure = latest_upper_failure or (
            len(last_two) == 2 and all(bar.high >= plan.first_target and bar.close < plan.first_target for bar in last_two)
        )
        target_near = facts.current_price >= plan.first_target - self.level_rules.base_noise_atr * plan.atr
        target_reached = facts.current_price >= plan.first_target
        positioned = plan.position_state is PositionState.POSITIONED
        h3_hit = positioned and target_near and pressure_failure and before_target_cutoff
        r1_hit = positioned and soft_break and before_soft_cutoff
        r2_hit = positioned and n1_hit and before_soft_cutoff
        r3_hit = positioned and target_reached and before_target_cutoff
        safe_hold = positioned and not any((hard_stop_hit, soft_break, n1_hit, h3_hit, r3_hit)) and facts.current_price > plan.soft_risk
        h2_hit = safe_hold and facts.intraday_market_state is IntradayMarketState.RANGE and plan.support < facts.current_price < plan.first_target
        h1_hit = safe_hold and not h2_hit

        h1 = store(_candidate("H1", (
            _trace("h1_position", "存在真实持仓", positioned, "持仓有效" if positioned else "没有活动持仓"),
            _trace("h1_safe_zone", "价格在软风险和硬止损上且无失败", safe_hold, "趋势持有条件成立" if safe_hold else "存在更高优先级风险或兑现条件", actual=facts.current_price, expected={"soft_risk": plan.soft_risk, "hard_stop": plan.hard_stop}),
        ), triggered=h1_hit, invalidated=positioned and not h1_hit and hard_stop_hit, cancelled="H1" in facts.cancelled_scenarios))
        h2 = store(_candidate("H2", (
            _trace("h2_range", "盘中震荡且价格位于支撑和目标之间", facts.intraday_market_state is IntradayMarketState.RANGE and plan.support < facts.current_price < plan.first_target, "区间持有条件成立" if h2_hit else "不是区间持有条件", actual=facts.current_price, expected=(plan.support, plan.first_target)),
            _trace("h2_no_risk", "没有风险确认", safe_hold, "未触发风险" if safe_hold else "已有风险或兑现剧本"),
        ), triggered=h2_hit, invalidated=positioned and hard_stop_hit, cancelled="H2" in facts.cancelled_scenarios))
        h3 = store(_candidate("H3", (
            _trace("h3_target_near", "价格进入目标附近", positioned and target_near, "进入压力兑现区" if target_near else "尚未接近目标", actual=facts.current_price, expected=plan.first_target),
            _trace("h3_pressure_failure", "上影或两根触压未站上", pressure_failure if positioned else False, "压力兑现确认" if pressure_failure else "没有压力失败形态"),
            _trace("h3_cutoff", "不晚于 14:45", before_target_cutoff, "目标兑现仍在触发窗口" if before_target_cutoff else "14:45 后不再新触发", actual=clock.isoformat(), expected=self.rules.target_trim_cutoff.isoformat()),
        ), triggered=h3_hit, invalidated=positioned and target_near and pressure_failure and not before_target_cutoff, cancelled="H3" in facts.cancelled_scenarios))
        r1 = store(_candidate("R1", (
            _trace("r1_two_below", "连续两根低于软风险位减 0.15 ATR", soft_break if len(last_two) == 2 else None, "软破位确认" if soft_break else "软破位未完成", actual=tuple(bar.close for bar in last_two), expected=soft_threshold, source="stock_5m"),
            _trace("r1_above_hard_stop", "仍高于场景硬止损", latest_close > plan.hard_stop, "仍属于软风险" if latest_close > plan.hard_stop else "已升级硬止损", actual=latest_close, expected=plan.hard_stop),
            _trace("r1_cutoff", "不晚于 14:55", before_soft_cutoff, "软风险仍在触发窗口" if before_soft_cutoff else "14:55 后不再新触发", actual=clock.isoformat(), expected=self.rules.soft_risk_cutoff.isoformat()),
        ), triggered=r1_hit, waiting=positioned and len(last_two) == 1 and last_two[-1].close < soft_threshold, invalidated=positioned and soft_break and not before_soft_cutoff, cancelled="R1" in facts.cancelled_scenarios))
        r2 = store(_candidate("R2", (
            _trace("r2_n1", "持仓满足高开失败", positioned and n1_hit, "高开失败保护触发" if positioned and n1_hit else "高开失败未成立"),
            _trace("r2_cutoff", "不晚于 14:55", before_soft_cutoff, "软风险仍在触发窗口" if before_soft_cutoff else "14:55 后不再新触发", actual=clock.isoformat(), expected=self.rules.soft_risk_cutoff.isoformat()),
        ), triggered=r2_hit, invalidated=positioned and n1_hit and not before_soft_cutoff, cancelled="R2" in facts.cancelled_scenarios))
        r3 = store(_candidate("R3", (
            _trace("r3_target", "当前价达到第一目标", positioned and target_reached, "第一目标已达到" if target_reached else "尚未达到第一目标", actual=facts.current_price, operator=">=", expected=plan.first_target),
            _trace("r3_breakout", "E4 突破确认决定持有或兑现", e4_confirmed if target_reached else False, "突破确认，保护利润" if e4_confirmed else "突破未确认，准备兑现"),
            _trace("r3_cutoff", "不晚于 14:45", before_target_cutoff, "目标动作仍在触发窗口" if before_target_cutoff else "14:45 后不再新触发", actual=clock.isoformat(), expected=self.rules.target_trim_cutoff.isoformat()),
        ), triggered=r3_hit, invalidated=positioned and target_reached and not before_target_cutoff, cancelled="R3" in facts.cancelled_scenarios))

        if positioned:
            if n2.state is ScenarioState.TRIGGERED and not hard_stop_hit and before_soft_cutoff:
                signal(n2, _action(ActionType.PREPARE_REDUCE), ExecutionState.REDUCE_PENDING, 80, risk=True, reason="低开承接失败，持仓准备减仓", deadline="14:55")
            if h1.state is ScenarioState.TRIGGERED:
                signal(h1, _action(ActionType.HOLD, target_quantity=plan.current_quantity, change_quantity=0), ExecutionState.POSITIONED, 20, risk=False, reason="价格位于风险位上方且没有失败或兑现条件")
            if h2.state is ScenarioState.TRIGGERED:
                signal(h2, _action(ActionType.HOLD, target_quantity=plan.current_quantity, change_quantity=0), ExecutionState.POSITIONED, 20, risk=False, reason="震荡环境中价格位于支撑与目标之间")
            if h3.state is ScenarioState.TRIGGERED:
                signal(h3, _action(ActionType.PREPARE_REDUCE), ExecutionState.REDUCE_PENDING, 80, risk=True, reason="目标附近压力失败，准备动态兑现", deadline="14:45")
            if r1.state is ScenarioState.WAITING_CONFIRMATION:
                signal(r1, _action(ActionType.WAIT_SOFT_BREAK), ExecutionState.CONFIRMING, 50, risk=True, reason="首次跌破软风险位，等待第二根闭合 K", deadline="14:55")
            elif r1.state is ScenarioState.TRIGGERED:
                signal(r1, _action(ActionType.PREPARE_REDUCE), ExecutionState.REDUCE_PENDING, 80, risk=True, reason="连续两根软破位确认，准备动态减仓", deadline="14:55")
            if r2.state is ScenarioState.TRIGGERED:
                signal(r2, _action(ActionType.PREPARE_REDUCE), ExecutionState.REDUCE_PENDING, 80, risk=True, reason="高开失败保护触发，准备动态减仓", deadline="14:55")
            if r3.state is ScenarioState.TRIGGERED:
                if e4_confirmed:
                    signal(r3, _action(ActionType.PROTECT_PROFIT, target_quantity=plan.current_quantity, change_quantity=0), ExecutionState.POSITIONED, 80, risk=False, reason="目标到达且 E4 突破确认，保护利润继续持有", deadline="14:45")
                else:
                    signal(r3, _action(ActionType.PREPARE_REDUCE), ExecutionState.REDUCE_PENDING, 80, risk=True, reason="目标到达但突破未确认，准备动态兑现", deadline="14:45")

        ordered_candidates = tuple(candidates[code] for code in SCENARIO_CODES)
        if plan.position_state is PositionState.UNKNOWN:
            return ScenarioDecision(
                ticker=plan.ticker,
                computed_at=facts.computed_at,
                action=_action(ActionType.CONFIRM_POSITION_FACTS),
                execution_state=ExecutionState.DATA_BLOCKED,
                primary_scenario=None,
                candidate_scenarios=ordered_candidates,
                suppressed_scenarios=(),
                reasons=("持仓数量或成本事实待核验，禁止数量动作",),
            )
        if plan.position_state is PositionState.CLOSED:
            return ScenarioDecision(
                ticker=plan.ticker,
                computed_at=facts.computed_at,
                action=_action(ActionType.NO_ACTION, target_quantity=0),
                execution_state=ExecutionState.CLOSED,
                primary_scenario=None,
                candidate_scenarios=ordered_candidates,
                suppressed_scenarios=(),
                reasons=("原持仓生命周期已结束，新交易必须创建新计划",),
            )

        primary, suppressed = arbitrate_scenarios(tuple(signals))
        if primary is None:
            fallback_action = (
                _action(ActionType.HOLD, target_quantity=plan.current_quantity, change_quantity=0)
                if positioned else _action(ActionType.WATCH_TRIGGER)
            )
            fallback_state = ExecutionState.POSITIONED if positioned else ExecutionState.ARMED
            return ScenarioDecision(
                ticker=plan.ticker,
                computed_at=facts.computed_at,
                action=fallback_action,
                execution_state=fallback_state,
                primary_scenario=None,
                candidate_scenarios=ordered_candidates,
                suppressed_scenarios=(),
                reasons=("当前没有完成确认的场景，继续监控冻结关键位",),
            )
        if primary.candidate.scenario_id in {"N1", "N2", "N3", "N4"}:
            recorded = {item.scenario_id for item in suppressed}
            entry_activity_all = {
                **entry_was_active,
                "E7": e7_candidate.state in {
                    ScenarioState.TRIGGERED,
                    ScenarioState.WAITING_CONFIRMATION,
                    ScenarioState.INVALIDATED,
                },
            }
            extra = tuple(
                SuppressedScenario(
                    scenario_id=code,
                    suppressed_by=primary.candidate.scenario_id,
                    reason=f"{primary.candidate.scenario_id} 覆盖仍在活动的 {code} 新仓剧本",
                )
                for code, active in entry_activity_all.items()
                if active and code not in recorded
            )
            suppressed = (*suppressed, *extra)
        sell_actions = {
            ActionType.PREPARE_REDUCE,
            ActionType.RISK_EXIT,
        }
        decision = ScenarioDecision(
            ticker=plan.ticker,
            computed_at=facts.computed_at,
            action=primary.action,
            execution_state=primary.execution_state,
            primary_scenario=primary.candidate,
            candidate_scenarios=ordered_candidates,
            suppressed_scenarios=suppressed,
            reasons=(primary.reason,),
            preferred_execution_window=primary.action.canonical_type in sell_actions and preferred_sell,
            deadline=primary.deadline,
        )
        return self._apply_sizing(decision, plan, facts, sizing_context)

    def _apply_sizing(
        self,
        decision: ScenarioDecision,
        plan: ScenarioExecutionPlan,
        facts: ScenarioMarketFacts,
        context: ScenarioSizingContext | None,
    ) -> ScenarioDecision:
        if context is None or decision.primary_scenario is None:
            return decision
        action_type = decision.action.canonical_type
        code = decision.primary_scenario.scenario_id
        unit_rule = TradingUnitRule(
            buy_unit=plan.lot_size,
            ordinary_sell_unit=plan.lot_size,
        )
        if action_type is ActionType.PREPARE_ENTRY:
            sizing = calculate_entry_sizing(
                EntrySizingInput(
                    plan_id=context.plan_id,
                    ticker=plan.ticker,
                    scenario_id=code,
                    market_permission=facts.market_permission.value,
                    entry_price=facts.current_price,
                    hard_stop=plan.hard_stop,
                    unit_rule=unit_rule,
                    is_main_opportunity=context.is_main_opportunity,
                    active_reservations=context.active_reservations,
                    per_share_fee_slippage_buffer=context.per_share_fee_slippage_buffer,
                ),
                context.profile,
                self.sizing_rules,
            )
            if sizing.status == "ready":
                action = replace(
                    decision.action,
                    target_quantity=sizing.planned_quantity,
                    change_quantity=sizing.planned_quantity,
                )
                return replace(
                    decision,
                    action=action,
                    sizing=sizing,
                    reasons=(*decision.reasons, *sizing.explanation),
                )
            if sizing.status == "alternative_unreserved":
                return replace(
                    decision,
                    action=_action(ActionType.WATCH_TRIGGER),
                    execution_state=ExecutionState.ARMED,
                    sizing=sizing,
                    reasons=(*decision.reasons, *sizing.explanation),
                )
            return replace(
                decision,
                action=_action(ActionType.PAUSE_NEW_ACTION),
                execution_state=ExecutionState.CONFIRMING,
                sizing=sizing,
                reasons=(*decision.reasons, *sizing.explanation),
            )

        if action_type not in {ActionType.PREPARE_REDUCE, ActionType.RISK_EXIT}:
            return decision

        if code == "E7" and action_type is ActionType.PREPARE_REDUCE:
            staged = ExecutionSizingResult(
                sizing_type="reduction",
                status="e7_staged_exit",
                executable=bool(
                    decision.action.change_quantity is not None
                    and decision.action.change_quantity < 0
                ),
                target_quantity=decision.action.target_quantity,
                change_quantity=decision.action.change_quantity,
                available_quantity=plan.sellable_quantity,
                recommended_sell_quantity=(
                    abs(decision.action.change_quantity)
                    if decision.action.change_quantity is not None else None
                ),
                risk_severity=self.sizing_rules.forced_exit_severity,
                quantity_factor=1.0,
                explanation=("E7 高开首批退出沿用 12D 的不少于 50% 批次，10:00 目标归零",),
            )
            return replace(
                decision,
                sizing=staged,
                reasons=(*decision.reasons, *staged.explanation),
            )

        forced = action_type is ActionType.RISK_EXIT or code in {"R4"}
        if code == "R3":
            severity = self.sizing_rules.target_unconfirmed_severity
        elif code in {"H3", "R2", "N2"}:
            severity = self.sizing_rules.pressure_failure_severity
        elif code == "R1":
            severity = (
                self.sizing_rules.compounded_soft_break_severity
                if facts.sector_state is SectorExecutionState.WEAK
                or facts.intraday_market_state is IntradayMarketState.RISK_OFF
                else self.sizing_rules.soft_break_severity
            )
        else:
            severity = (
                self.sizing_rules.forced_exit_severity
                if forced else self.sizing_rules.pressure_failure_severity
            )
        sizing = calculate_reduction_sizing(
            ReductionSizingInput(
                current_quantity=plan.current_quantity,
                available_quantity=plan.sellable_quantity,
                current_price=facts.current_price,
                target_price=plan.first_target,
                hard_stop=plan.hard_stop,
                risk_severity=severity,
                unit_rule=unit_rule,
                forced_exit=forced,
            ),
            self.sizing_rules,
        )
        if sizing.status in {"reduce_ready", "forced_exit"}:
            action = replace(
                decision.action,
                target_quantity=sizing.target_quantity,
                change_quantity=sizing.change_quantity,
            )
            return replace(
                decision,
                action=action,
                sizing=sizing,
                reasons=(*decision.reasons, *sizing.explanation),
            )
        return replace(
            decision,
            action=_action(ActionType.PAUSE_NEW_ACTION),
            execution_state=(
                ExecutionState.DATA_BLOCKED
                if sizing.status in {"quantity_not_configured", "forced_quantity_unverified"}
                else ExecutionState.POSITIONED
            ),
            sizing=sizing,
            reasons=(*decision.reasons, *sizing.explanation),
        )

    def _evaluate_e7(
        self,
        plan: ScenarioExecutionPlan,
        facts: ScenarioMarketFacts,
        *,
        in_entry_window: bool,
        entry_expired: bool,
    ) -> tuple[tuple[RuleTrace, ...], bool, bool, bool]:
        e7 = facts.e7
        if e7 is None:
            return (
                (_trace("e7_three_day_facts", "存在 T-2/T-1/T 三日事实", False, "没有 E7 三日事实"),),
                False,
                False,
                False,
            )
        today = facts.computed_at.date()
        phase_date = {
            E7Phase.OVERSOLD_CANDIDATE: e7.t_minus_2_date,
            E7Phase.STABILIZATION_ENTRY: e7.t_minus_1_date,
            E7Phase.NEXT_DAY_EXIT: e7.exit_date,
        }[e7.phase]
        phase_valid = today == phase_date
        oversold = (
            e7.market_oversold_strength >= self.rules.e7_market_stock_oversold_min
            and e7.stock_oversold_strength >= self.rules.e7_market_stock_oversold_min
            and e7.sector_oversold_strength >= self.rules.e7_sector_oversold_min
        )
        history = e7.sector_up_ratio_history
        sector_three = False
        sector_recent_stable = False
        if len(history) >= 3:
            latest_three = history[-3:]
            previous_floor = min(history[:-3]) if len(history) > 3 else latest_three[0]
            sector_three = all(value >= previous_floor for value in latest_three)
            sector_recent_stable = history[-1] >= history[0] - self.rules.e7_sector_recent_decline_max
        stock_bars = e7.stock_bars_after_1400[-3:]
        stock_stable = False
        stock_latest_not_weak = False
        if len(stock_bars) == 3:
            stock_stable = all(bar.low >= stock_bars[0].low for bar in stock_bars[1:])
            stock_latest_not_weak = stock_bars[-1].close >= median(bar.close for bar in stock_bars)
        low_zone = (
            e7.t_minus_2_low > 0
            and e7.t_minus_2_low - self.rules.e7_low_floor_atr * plan.atr
            <= e7.t_minus_1_low
            <= e7.t_minus_2_low + self.rules.e7_low_ceiling_atr * plan.atr
        )
        benchmark_stable = (
            (e7.sse_three_bars_no_new_low and not e7.chinext_accelerating_breakdown)
            or (e7.chinext_three_bars_no_new_low and not e7.sse_accelerating_breakdown)
        )
        tradable = not any((
            e7.limit_down_locked,
            e7.suspended,
            e7.another_e7_position_open,
            facts.price_limit_locked,
            facts.suspended,
            not facts.required_data_fresh,
            facts.current_price > plan.max_acceptable_price,
        ))
        stabilized = all((
            oversold,
            sector_three,
            sector_recent_stable,
            e7.sector_new_low_ratio <= self.rules.e7_sector_new_low_ratio_max,
            low_zone,
            stock_stable,
            stock_latest_not_weak,
            benchmark_stable,
            tradable,
        ))
        clock = _clock(facts.computed_at)
        entry_time_ready = self.rules.e7_earliest_confirmation <= clock < self.rules.new_entry_cutoff
        traces = (
            _trace("e7_phase_date", "当前日期匹配 T-2/T-1/T 阶段", phase_valid, "三日阶段匹配" if phase_valid else "三日阶段与当前交易日不一致", actual=today, expected=phase_date),
            _trace("e7_oversold", "T-2 大盘、板块和个股共同超跌", oversold, "共同超跌门槛完成" if oversold else "共同超跌门槛不足", actual={"market": e7.market_oversold_strength, "sector": e7.sector_oversold_strength, "stock": e7.stock_oversold_strength}),
            _trace("e7_sector_stable", "T-1 上涨比例三组不创新低且 30 分钟降幅不超 5pct", sector_three and sector_recent_stable if history else None, "板块上涨比例维稳" if sector_three and sector_recent_stable else "板块上涨比例仍待维稳", actual=history, source="sector_sample"),
            _trace("e7_sector_new_low", "板块创新低样本占比不超过 30%", e7.sector_new_low_ratio <= self.rules.e7_sector_new_low_ratio_max, "创新低样本受控" if e7.sector_new_low_ratio <= self.rules.e7_sector_new_low_ratio_max else "板块仍有过多样本创新低", actual=e7.sector_new_low_ratio, operator="<=", expected=self.rules.e7_sector_new_low_ratio_max, source="sector_sample"),
            _trace("e7_low_zone", "T-1 最低价回到 T-2 低位区", low_zone, "低位区检查通过" if low_zone else "最低价位于半山腰或再次破位", actual=e7.t_minus_1_low, expected=(e7.t_minus_2_low - self.rules.e7_low_floor_atr * plan.atr, e7.t_minus_2_low + self.rules.e7_low_ceiling_atr * plan.atr)),
            _trace("e7_stock_stable", "14:00 后三根 K 不创新低且最新收盘不弱", stock_stable and stock_latest_not_weak if stock_bars else None, "个股三组维稳完成" if stock_stable and stock_latest_not_weak else "个股三组维稳未完成", source="stock_5m"),
            _trace("e7_indices_stable", "双指数至少一只三根不创新低且另一只不加速", benchmark_stable, "指数维稳门槛完成" if benchmark_stable else "指数仍在加速破底", source="index_5m"),
            _trace("e7_tradable", "非封死、停牌、过期、超价或重复持仓", tradable, "E7 价格和持有约束可执行" if tradable else "E7 交易约束阻断"),
            _trace("e7_entry_time", "T-1 最早 14:15 且早于 15:00", entry_time_ready, "尾盘确认时点合格" if entry_time_ready else "尚未到 14:15 或已过 15:00", actual=clock.isoformat(), expected=(self.rules.e7_earliest_confirmation.isoformat(), self.rules.new_entry_cutoff.isoformat())),
        )
        if not phase_valid:
            return traces, False, False, True
        if e7.phase is E7Phase.OVERSOLD_CANDIDATE:
            return traces, False, oversold, False
        if e7.phase is E7Phase.NEXT_DAY_EXIT:
            return traces, plan.position_state is PositionState.POSITIONED, False, plan.position_state is not PositionState.POSITIONED
        if entry_expired:
            return traces, False, False, oversold
        if stabilized and entry_time_ready and in_entry_window:
            return traces, True, False, False
        return traces, False, oversold, False

    def _e7_exit_signal(
        self,
        plan: ScenarioExecutionPlan,
        facts: ScenarioMarketFacts,
        candidate: ScenarioCandidate,
        quote_fresh: bool,
    ) -> ScenarioSignal | None:
        clock = _clock(facts.computed_at)
        if clock < self.rules.e7_exit_start:
            return ScenarioSignal(
                candidate=candidate,
                action=_action(ActionType.HOLD, target_quantity=plan.current_quantity, change_quantity=0),
                execution_state=ExecutionState.POSITIONED,
                precedence=90,
                is_risk=True,
                confirmed_at=facts.computed_at,
                reason="E7 已进入 T 日强制退出阶段，等待 09:30 连续竞价",
                deadline="10:00",
            )
        if not quote_fresh:
            return ScenarioSignal(
                candidate=candidate,
                action=_action(ActionType.MANUAL_VERIFY_RISK),
                execution_state=ExecutionState.DATA_BLOCKED,
                precedence=90,
                is_risk=True,
                confirmed_at=facts.computed_at,
                reason="E7 退出报价超过 45 秒，立即人工核验剩余可卖量和委托价",
                deadline="10:00",
            )
        if plan.sellable_quantity is None:
            return ScenarioSignal(
                candidate=candidate,
                action=_action(ActionType.MANUAL_VERIFY_RISK, target_quantity=0),
                execution_state=ExecutionState.DATA_BLOCKED,
                precedence=90,
                is_risk=True,
                confirmed_at=facts.computed_at,
                reason="E7 次日退出缺少可卖数量，目标仍为 0，立即核验持仓事实",
                deadline="10:00",
            )
        gap_pct = plan.open_price / plan.previous_close - 1.0
        force_all = (
            clock >= self.rules.e7_exit_cutoff
            or facts.current_price <= plan.hard_stop
            or facts.e7_support_deteriorating
            or gap_pct < self.rules.gap_up_pct
            or plan.sellable_quantity < 2 * plan.lot_size
        )
        if force_all:
            return ScenarioSignal(
                candidate=candidate,
                action=_action(ActionType.RISK_EXIT, target_quantity=0, change_quantity=-plan.sellable_quantity),
                execution_state=ExecutionState.EXIT_PENDING,
                precedence=90,
                is_risk=True,
                confirmed_at=facts.computed_at,
                reason="E7 T 日平低开、风险恶化、一手持仓或 10:00 截止，目标数量归零",
                deadline="10:00",
            )
        if clock <= self.rules.e7_high_open_first_exit_end:
            half = ceil((plan.current_quantity / 2) / plan.lot_size) * plan.lot_size
            exit_quantity = min(plan.sellable_quantity, max(plan.lot_size, half))
            target = max(0, plan.current_quantity - exit_quantity)
            return ScenarioSignal(
                candidate=candidate,
                action=_action(ActionType.PREPARE_REDUCE, target_quantity=target, change_quantity=-exit_quantity),
                execution_state=ExecutionState.REDUCE_PENDING,
                precedence=90,
                is_risk=True,
                confirmed_at=facts.computed_at,
                reason="E7 T 日高开且至少两手，09:30–09:35 先退出不少于 50%",
                deadline="10:00",
            )
        return ScenarioSignal(
            candidate=candidate,
            action=_action(ActionType.HOLD, target_quantity=plan.current_quantity, change_quantity=0),
            execution_state=ExecutionState.POSITIONED,
            precedence=90,
            is_risk=True,
            confirmed_at=facts.computed_at,
            reason="E7 高开首批退出后观察开盘强度，10:00 前必须全部退出",
            deadline="10:00",
        )
