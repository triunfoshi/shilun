"""Trading Decision Engine 的稳定领域契约。

本模块只定义事实、规则轨迹、剧本和动作，不包含 MA5 等具体 Playbook
规则。所有时间戳必须带时区，避免盘中判断因本地时区或字符串解析产生歧义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class ContractError(ValueError):
    """领域对象缺少必要字段或违反稳定契约。"""


class DataState(StrEnum):
    NORMAL = "normal"
    DELAYED = "delayed"
    MISSING = "missing"
    CLOSED = "closed"


class RuleState(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    PENDING = "pending"
    BLOCKED = "blocked"


class ScenarioState(StrEnum):
    FAR = "far"
    APPROACHING = "approaching"
    WAITING_CONFIRMATION = "waiting_confirmation"
    TRIGGERED = "triggered"
    INVALIDATED = "invalidated"
    SUPPRESSED = "suppressed"


class MatchLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionIntent(StrEnum):
    """主动动作需要完整环境；防御动作只依赖可信的个股风险事实。"""

    ACTIVE = "active"
    DEFENSIVE = "defensive"


class PositionState(StrEnum):
    """真实持仓生命周期；不能由行情建议反向推断。"""

    FLAT = "flat"
    UNKNOWN = "unknown"
    POSITIONED = "positioned"
    CLOSED = "closed"


class ExecutionState(StrEnum):
    """一份 ExecutionPlan 在盘中所处的唯一状态。"""

    SELECTED = "selected"
    ARMED = "armed"
    CONFIRMING = "confirming"
    ENTRY_READY = "entry_ready"
    ENTRY_PENDING = "entry_pending"
    POSITIONED = "positioned"
    REDUCE_PENDING = "reduce_pending"
    EXIT_PENDING = "exit_pending"
    CLOSED = "closed"
    MISSED = "missed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    DATA_BLOCKED = "data_blocked"


class EntryScenario(StrEnum):
    """Job 12 的七类新建仓执行剧本。"""

    E1_LOW_OPEN_RECOVERY = "low_open_recovery"
    E2_PULLBACK_RECLAIM = "pullback_reclaim"
    E3_RANGE_SUPPORT_REVERSAL = "range_support_reversal"
    E4_BREAKOUT_CONTINUATION = "breakout_continuation"
    E5_WEAK_TO_STRONG = "weak_to_strong"
    E6_HIGH_OPEN_STRONG_BREAKOUT = "high_open_strong_breakout"
    E7_OVERSOLD_REBOUND_OVERNIGHT = "oversold_rebound_overnight"


class ActionType(StrEnum):
    """稳定的执行动作代码；页面和通知只消费此层中文展示契约。"""

    OBSERVE_SETUP = "observe_setup"
    WATCH_TRIGGER = "watch_trigger"
    WAIT_CONFIRMATION = "wait_confirmation"
    WAIT_LOW_OPEN_RECOVERY = "wait_low_open_recovery"
    WAIT_PULLBACK_RECLAIM = "wait_pullback_reclaim"
    WAIT_RANGE_REVERSAL = "wait_range_reversal"
    WAIT_BREAKOUT = "wait_breakout"
    WAIT_WEAK_TO_STRONG = "wait_weak_to_strong"
    WAIT_OVERSOLD_STABILIZATION = "wait_oversold_stabilization"
    WAIT_HIGH_OPEN_FAILURE = "wait_high_open_failure"
    WAIT_LOW_OPEN_FAILURE = "wait_low_open_failure"
    WAIT_SOFT_BREAK = "wait_soft_break"
    PREPARE_ENTRY = "prepare_entry"
    NO_CHASE = "no_chase"
    CANCEL_ENTRY = "cancel_entry"
    AWAIT_FILL = "await_fill"
    RECORD_PARTIAL_FILL = "record_partial_fill"
    HOLD = "hold"
    PROTECT_PROFIT = "protect_profit"
    PREPARE_REDUCE = "prepare_reduce"
    RISK_EXIT = "risk_exit"
    MANUAL_VERIFY_RISK = "manual_verify_risk"
    CONFIRM_POSITION_FACTS = "confirm_position_facts"
    PAUSE_NEW_ACTION = "pause_new_action"
    NO_ACTION = "no_action"


@dataclass(frozen=True)
class ActionDefinition:
    label: str
    urgency: str
    executable: bool


ACTION_DEFINITIONS: dict[ActionType, ActionDefinition] = {
    ActionType.OBSERVE_SETUP: ActionDefinition("观察入选", "low", False),
    ActionType.WATCH_TRIGGER: ActionDefinition("监控触发", "low", False),
    ActionType.WAIT_CONFIRMATION: ActionDefinition("等待确认", "medium", False),
    ActionType.WAIT_LOW_OPEN_RECOVERY: ActionDefinition("等待低开修复", "medium", False),
    ActionType.WAIT_PULLBACK_RECLAIM: ActionDefinition("等待回踩收复", "medium", False),
    ActionType.WAIT_RANGE_REVERSAL: ActionDefinition("等待区间反转", "medium", False),
    ActionType.WAIT_BREAKOUT: ActionDefinition("等待突破", "medium", False),
    ActionType.WAIT_WEAK_TO_STRONG: ActionDefinition("等待弱转强", "medium", False),
    ActionType.WAIT_OVERSOLD_STABILIZATION: ActionDefinition("等待超跌企稳", "medium", False),
    ActionType.WAIT_HIGH_OPEN_FAILURE: ActionDefinition("等待高开失败确认", "medium", False),
    ActionType.WAIT_LOW_OPEN_FAILURE: ActionDefinition("等待低开失败确认", "medium", False),
    ActionType.WAIT_SOFT_BREAK: ActionDefinition("等待软破位确认", "high", False),
    ActionType.PREPARE_ENTRY: ActionDefinition("可建仓", "high", True),
    ActionType.NO_CHASE: ActionDefinition("已错过，不追", "low", False),
    ActionType.CANCEL_ENTRY: ActionDefinition("取消建仓", "low", False),
    ActionType.AWAIT_FILL: ActionDefinition("等待成交", "high", False),
    ActionType.RECORD_PARTIAL_FILL: ActionDefinition("记录部分成交", "high", True),
    ActionType.HOLD: ActionDefinition("继续持有", "low", False),
    ActionType.PROTECT_PROFIT: ActionDefinition("保护利润", "medium", True),
    ActionType.PREPARE_REDUCE: ActionDefinition("准备减仓", "high", True),
    ActionType.RISK_EXIT: ActionDefinition("立即清仓", "critical", True),
    ActionType.MANUAL_VERIFY_RISK: ActionDefinition("人工核验风险", "critical", False),
    ActionType.CONFIRM_POSITION_FACTS: ActionDefinition("核验持仓事实", "high", False),
    ActionType.PAUSE_NEW_ACTION: ActionDefinition("暂停新动作", "high", False),
    ActionType.NO_ACTION: ActionDefinition("无新动作", "low", False),
}


LEGACY_ACTION_ALIASES: dict[str, ActionType] = {
    "adjust_position": ActionType.PREPARE_REDUCE,
    "risk_reduce": ActionType.PREPARE_REDUCE,
    "hold_observe": ActionType.HOLD,
    "wait": ActionType.WATCH_TRIGGER,
}


def canonical_action_type(value: str | ActionType) -> ActionType:
    """把旧 DecisionResult 动作归一化，但不改写旧响应中的原始代码。"""

    if isinstance(value, ActionType):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in LEGACY_ACTION_ALIASES:
        return LEGACY_ACTION_ALIASES[normalized]
    try:
        return ActionType(normalized)
    except ValueError as error:
        raise ContractError(f"未知执行动作：{value}") from error


@dataclass(frozen=True)
class ExecutionStateView:
    """面向交易员的单行状态语言，不暴露机器代码。"""

    label: str
    instruction: str
    tone: str


EXECUTION_STATE_VIEWS: dict[ExecutionState, ExecutionStateView] = {
    ExecutionState.SELECTED: ExecutionStateView("已入选", "观察中 · 暂不操作", "neutral"),
    ExecutionState.ARMED: ExecutionStateView("已布防", "观察中 · 等待触发", "neutral"),
    ExecutionState.CONFIRMING: ExecutionStateView("等待确认", "暂不买入", "warning"),
    ExecutionState.ENTRY_READY: ExecutionStateView("可建仓", "按计划价格与数量执行", "positive"),
    ExecutionState.ENTRY_PENDING: ExecutionStateView("委托待成交", "等待成交回写", "warning"),
    ExecutionState.POSITIONED: ExecutionStateView("已持仓", "继续持有", "neutral"),
    ExecutionState.REDUCE_PENDING: ExecutionStateView("减仓待成交", "按计划减仓", "danger"),
    ExecutionState.EXIT_PENDING: ExecutionStateView("清仓待成交", "立即清仓", "critical"),
    ExecutionState.CLOSED: ExecutionStateView("已结束", "复盘交易", "neutral"),
    ExecutionState.MISSED: ExecutionStateView("已错过", "不追", "neutral"),
    ExecutionState.EXPIRED: ExecutionStateView("已过期", "今日计划结束", "neutral"),
    ExecutionState.CANCELLED: ExecutionStateView("已取消", "暂不操作", "neutral"),
    ExecutionState.DATA_BLOCKED: ExecutionStateView("数据阻断", "暂停新动作", "danger"),
}


def execution_state_view(state: ExecutionState | str) -> ExecutionStateView:
    try:
        normalized = state if isinstance(state, ExecutionState) else ExecutionState(state)
    except ValueError as error:
        raise ContractError(f"未知执行状态：{state}") from error
    return EXECUTION_STATE_VIEWS[normalized]


LEGAL_EXECUTION_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.SELECTED: frozenset({
        ExecutionState.ARMED,
        ExecutionState.POSITIONED,
        ExecutionState.CANCELLED,
        ExecutionState.EXPIRED,
        ExecutionState.DATA_BLOCKED,
    }),
    ExecutionState.ARMED: frozenset({
        ExecutionState.CONFIRMING,
        ExecutionState.POSITIONED,
        ExecutionState.MISSED,
        ExecutionState.CANCELLED,
        ExecutionState.EXPIRED,
        ExecutionState.DATA_BLOCKED,
    }),
    ExecutionState.CONFIRMING: frozenset({
        ExecutionState.ARMED,
        ExecutionState.ENTRY_READY,
        ExecutionState.POSITIONED,
        ExecutionState.REDUCE_PENDING,
        ExecutionState.EXIT_PENDING,
        ExecutionState.MISSED,
        ExecutionState.CANCELLED,
        ExecutionState.EXPIRED,
        ExecutionState.DATA_BLOCKED,
    }),
    ExecutionState.ENTRY_READY: frozenset({
        ExecutionState.ENTRY_PENDING,
        ExecutionState.MISSED,
        ExecutionState.CANCELLED,
        ExecutionState.EXPIRED,
        ExecutionState.DATA_BLOCKED,
    }),
    ExecutionState.ENTRY_PENDING: frozenset({
        ExecutionState.ARMED,
        ExecutionState.POSITIONED,
        ExecutionState.CANCELLED,
        ExecutionState.EXPIRED,
        ExecutionState.DATA_BLOCKED,
    }),
    ExecutionState.POSITIONED: frozenset({
        ExecutionState.REDUCE_PENDING,
        ExecutionState.EXIT_PENDING,
        ExecutionState.CLOSED,
        ExecutionState.DATA_BLOCKED,
    }),
    ExecutionState.REDUCE_PENDING: frozenset({
        ExecutionState.POSITIONED,
        ExecutionState.EXIT_PENDING,
        ExecutionState.CLOSED,
        ExecutionState.DATA_BLOCKED,
    }),
    ExecutionState.EXIT_PENDING: frozenset({
        ExecutionState.CLOSED,
        ExecutionState.DATA_BLOCKED,
    }),
    ExecutionState.DATA_BLOCKED: frozenset({
        ExecutionState.SELECTED,
        ExecutionState.ARMED,
        ExecutionState.CONFIRMING,
        ExecutionState.ENTRY_READY,
        ExecutionState.ENTRY_PENDING,
        ExecutionState.POSITIONED,
        ExecutionState.REDUCE_PENDING,
        ExecutionState.EXIT_PENDING,
        ExecutionState.CANCELLED,
        ExecutionState.EXPIRED,
    }),
    ExecutionState.CLOSED: frozenset(),
    ExecutionState.MISSED: frozenset(),
    ExecutionState.EXPIRED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
}


def validate_execution_transition(
    from_state: ExecutionState | str,
    to_state: ExecutionState | str,
) -> None:
    try:
        source = from_state if isinstance(from_state, ExecutionState) else ExecutionState(from_state)
        target = to_state if isinstance(to_state, ExecutionState) else ExecutionState(to_state)
    except ValueError as error:
        raise ContractError("执行状态迁移包含未知状态") from error
    if source == target:
        return
    if target not in LEGAL_EXECUTION_TRANSITIONS[source]:
        raise ContractError(f"非法执行状态迁移：{source.value} -> {target.value}")


def require_aware_datetime(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{field_name} 必须包含时区")


@dataclass(frozen=True)
class FactSourceSnapshot:
    """一个事实来源及其数据时间；previous_as_of 用于检测时间戳倒退。"""

    source: str
    as_of: datetime | None
    payload: dict[str, Any] = field(default_factory=dict)
    previous_as_of: datetime | None = None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ContractError("source 不能为空")
        if self.as_of is not None:
            require_aware_datetime(self.as_of, field_name=f"{self.source}.as_of")
        if self.previous_as_of is not None:
            require_aware_datetime(
                self.previous_as_of,
                field_name=f"{self.source}.previous_as_of",
            )


@dataclass(frozen=True)
class TradingFacts:
    """Decision Engine 的统一事实输入，不在事实层生成任何买卖结论。"""

    ticker: str
    computed_at: datetime
    sources: tuple[FactSourceSnapshot, ...]
    holding: dict[str, Any] = field(default_factory=dict)
    stock: dict[str, Any] = field(default_factory=dict)
    indices: dict[str, Any] = field(default_factory=dict)
    sector: dict[str, Any] = field(default_factory=dict)
    account: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ticker.strip():
            raise ContractError("ticker 不能为空")
        require_aware_datetime(self.computed_at, field_name="computed_at")
        names = [item.source for item in self.sources]
        if len(names) != len(set(names)):
            raise ContractError("TradingFacts.sources 不允许重复 source")


@dataclass(frozen=True)
class RuleTrace:
    rule_id: str
    label: str
    state: RuleState
    reason: str
    actual: Any = None
    operator: str = ""
    expected: Any = None
    evidence_source: str = ""

    def __post_init__(self) -> None:
        if not self.rule_id.strip() or not self.label.strip():
            raise ContractError("RuleTrace 必须包含 rule_id 和 label")
        if not self.reason.strip():
            raise ContractError("RuleTrace.reason 不能为空")


@dataclass(frozen=True)
class ScenarioCandidate:
    scenario_id: str
    title: str
    priority: int
    state: ScenarioState
    match_level: MatchLevel
    traces: tuple[RuleTrace, ...]
    cancel_conditions: tuple[str, ...] = ()

    @property
    def evidence_completeness(self) -> str:
        resolved = sum(
            trace.state in {RuleState.SATISFIED, RuleState.UNSATISFIED}
            for trace in self.traces
        )
        return f"{resolved}/{len(self.traces)}"


@dataclass(frozen=True)
class TradingAction:
    action_type: str
    label: str
    target_quantity: int | None = None
    change_quantity: int | None = None
    execution_zone: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if not self.action_type.strip() or not self.label.strip():
            raise ContractError("TradingAction 必须包含 action_type 和 label")
        if self.target_quantity is not None and self.target_quantity < 0:
            raise ContractError("target_quantity 不能为负数")
        if self.execution_zone is not None:
            low, high = self.execution_zone
            if low > high:
                raise ContractError("execution_zone 下界不能大于上界")

    @property
    def canonical_type(self) -> ActionType:
        return canonical_action_type(self.action_type)


_FLAT_ACTIONS = frozenset({
    ActionType.OBSERVE_SETUP,
    ActionType.WATCH_TRIGGER,
    ActionType.WAIT_CONFIRMATION,
    ActionType.WAIT_LOW_OPEN_RECOVERY,
    ActionType.WAIT_PULLBACK_RECLAIM,
    ActionType.WAIT_RANGE_REVERSAL,
    ActionType.WAIT_BREAKOUT,
    ActionType.WAIT_WEAK_TO_STRONG,
    ActionType.WAIT_OVERSOLD_STABILIZATION,
    ActionType.WAIT_HIGH_OPEN_FAILURE,
    ActionType.WAIT_LOW_OPEN_FAILURE,
    ActionType.PREPARE_ENTRY,
    ActionType.NO_CHASE,
    ActionType.CANCEL_ENTRY,
    ActionType.AWAIT_FILL,
    ActionType.RECORD_PARTIAL_FILL,
    ActionType.PAUSE_NEW_ACTION,
    ActionType.NO_ACTION,
})
_UNKNOWN_ACTIONS = frozenset({
    ActionType.CONFIRM_POSITION_FACTS,
    ActionType.MANUAL_VERIFY_RISK,
    ActionType.PAUSE_NEW_ACTION,
    ActionType.NO_ACTION,
})
_POSITIONED_ACTIONS = frozenset({
    ActionType.WAIT_CONFIRMATION,
    ActionType.WAIT_HIGH_OPEN_FAILURE,
    ActionType.WAIT_LOW_OPEN_FAILURE,
    ActionType.WAIT_SOFT_BREAK,
    ActionType.HOLD,
    ActionType.PROTECT_PROFIT,
    ActionType.PREPARE_REDUCE,
    ActionType.RISK_EXIT,
    ActionType.MANUAL_VERIFY_RISK,
    ActionType.PAUSE_NEW_ACTION,
    ActionType.NO_ACTION,
})
_CLOSED_ACTIONS = frozenset({ActionType.NO_ACTION})


def validate_action_for_position(
    position_state: PositionState | str,
    action: TradingAction,
) -> None:
    """阻止把选品结论错误翻译为持仓动作或数量动作。"""

    try:
        state = position_state if isinstance(position_state, PositionState) else PositionState(position_state)
    except ValueError as error:
        raise ContractError(f"未知持仓状态：{position_state}") from error
    action_type = action.canonical_type
    allowed = {
        PositionState.FLAT: _FLAT_ACTIONS,
        PositionState.UNKNOWN: _UNKNOWN_ACTIONS,
        PositionState.POSITIONED: _POSITIONED_ACTIONS,
        PositionState.CLOSED: _CLOSED_ACTIONS,
    }[state]
    if action_type not in allowed:
        raise ContractError(
            f"持仓状态 {state.value} 不允许动作 {action_type.value}"
        )
    if state is PositionState.UNKNOWN and (
        action.target_quantity is not None or action.change_quantity is not None
    ):
        raise ContractError("待核验持仓不得输出数量动作")


@dataclass(frozen=True)
class ActionPresentation:
    """后端统一生成的用户动作区；一个快照只能有一个主动作。"""

    state_label: str
    headline: str
    instruction: str
    reason: str
    next_condition: str
    urgency: str
    executable: bool
    target_quantity: int | None = None
    change_quantity: int | None = None
    execution_zone: tuple[float, float] | None = None
    deadline: str | None = None
    data_as_of: datetime | None = None
    evidence_progress: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("state_label", "headline", "instruction", "reason", "next_condition"):
            value = str(getattr(self, field_name) or "")
            if not value.strip():
                raise ContractError(f"ActionPresentation.{field_name} 不能为空")
            if "\n" in value or "\r" in value:
                raise ContractError(f"ActionPresentation.{field_name} 必须单行展示")
        if self.target_quantity is not None and self.target_quantity < 0:
            raise ContractError("ActionPresentation.target_quantity 不能为负数")
        if self.execution_zone is not None and self.execution_zone[0] > self.execution_zone[1]:
            raise ContractError("ActionPresentation.execution_zone 下界不能大于上界")
        if self.data_as_of is not None:
            require_aware_datetime(self.data_as_of, field_name="ActionPresentation.data_as_of")


def build_action_presentation(
    *,
    execution_state: ExecutionState | str,
    action: TradingAction | None,
    reason: str,
    next_condition: str,
    data_as_of: datetime | None = None,
    evidence_progress: str | None = None,
    deadline: str | None = None,
    force_non_executable: bool = False,
) -> ActionPresentation:
    """生成页面和通知共用的中文动作，避免前端自行翻译状态。"""

    state = execution_state if isinstance(execution_state, ExecutionState) else ExecutionState(execution_state)
    state_view = execution_state_view(state)
    definition = ACTION_DEFINITIONS[action.canonical_type] if action else ACTION_DEFINITIONS[ActionType.NO_ACTION]
    headline = definition.label if action else state_view.instruction
    quantity = None
    if action is not None:
        if action.canonical_type is ActionType.PREPARE_ENTRY:
            quantity = action.target_quantity
            if quantity is not None:
                headline = f"可建仓 {quantity} 股"
        elif action.canonical_type is ActionType.PREPARE_REDUCE:
            quantity = abs(action.change_quantity or 0) or None
            if quantity is not None:
                headline = f"减仓 {quantity} 股"
        elif action.canonical_type is ActionType.RISK_EXIT:
            headline = "立即清仓"
    executable = bool(definition.executable and not force_non_executable)
    if state in {
        ExecutionState.SELECTED,
        ExecutionState.ARMED,
        ExecutionState.CONFIRMING,
        ExecutionState.MISSED,
        ExecutionState.EXPIRED,
        ExecutionState.CANCELLED,
        ExecutionState.DATA_BLOCKED,
        ExecutionState.CLOSED,
    }:
        executable = False
    return ActionPresentation(
        state_label=state_view.label,
        headline=headline,
        instruction=state_view.instruction,
        reason=reason,
        next_condition=next_condition,
        urgency=definition.urgency,
        executable=executable,
        target_quantity=action.target_quantity if action else None,
        change_quantity=action.change_quantity if action else None,
        execution_zone=action.execution_zone if action else None,
        deadline=deadline,
        data_as_of=data_as_of,
        evidence_progress=evidence_progress,
    )


@dataclass(frozen=True)
class ExecutionPlan:
    """当日冻结的执行计划；规则升级和持仓变化分别版本化。"""

    plan_id: str
    ticker: str
    trade_date: date
    strategy_id: str
    strategy_version: str
    execution_playbook_id: str
    execution_rule_version: str
    position_state: PositionState
    execution_state: ExecutionState
    plan_revision: int
    position_revision: int
    created_at: datetime
    setup_type: str = ""
    frozen_levels: dict[str, float] = field(default_factory=dict)
    entry_rules: dict[str, Any] = field(default_factory=dict)
    sizing: dict[str, Any] = field(default_factory=dict)
    cancel_conditions: tuple[str, ...] = ()
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        required = {
            "plan_id": self.plan_id,
            "ticker": self.ticker,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "execution_playbook_id": self.execution_playbook_id,
            "execution_rule_version": self.execution_rule_version,
        }
        for name, value in required.items():
            if not str(value or "").strip():
                raise ContractError(f"ExecutionPlan.{name} 不能为空")
        if self.plan_revision < 1 or self.position_revision < 0:
            raise ContractError("ExecutionPlan 版本号不合法")
        require_aware_datetime(self.created_at, field_name="ExecutionPlan.created_at")
        if self.expires_at is not None:
            require_aware_datetime(self.expires_at, field_name="ExecutionPlan.expires_at")
            if self.expires_at <= self.created_at:
                raise ContractError("ExecutionPlan.expires_at 必须晚于 created_at")


@dataclass(frozen=True)
class ExecutionEvent:
    """仅记录语义变化；行情逐笔变化属于快照，不属于事件。"""

    plan_id: str
    ticker: str
    trade_date: date
    occurred_at: datetime
    from_state: ExecutionState | None
    to_state: ExecutionState
    action_type: ActionType
    plan_revision: int
    position_revision: int
    semantic_fingerprint: str
    reason: str
    presentation: ActionPresentation

    def __post_init__(self) -> None:
        if not self.plan_id.strip() or not self.ticker.strip():
            raise ContractError("ExecutionEvent 必须包含 plan_id 和 ticker")
        if not self.reason.strip() or not self.semantic_fingerprint.strip():
            raise ContractError("ExecutionEvent 必须包含 reason 和 semantic_fingerprint")
        if self.plan_revision < 1 or self.position_revision < 0:
            raise ContractError("ExecutionEvent 版本号不合法")
        require_aware_datetime(self.occurred_at, field_name="ExecutionEvent.occurred_at")
        if self.from_state is not None:
            validate_execution_transition(self.from_state, self.to_state)


@dataclass(frozen=True)
class SourceFreshness:
    source: str
    state: DataState
    age_seconds: float | None
    max_age_seconds: int
    reason: str


@dataclass(frozen=True)
class DataFreshnessReport:
    state: DataState
    can_generate_action: bool
    sources: tuple[SourceFreshness, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DecisionResult:
    """驾驶舱的统一输出；数据不安全时 action/current_scenario 必须为空。"""

    ticker: str
    computed_at: datetime
    data_freshness: DataFreshnessReport
    action: TradingAction | None
    reasons: tuple[str, ...]
    current_scenario: ScenarioCandidate | None
    candidate_scenarios: tuple[ScenarioCandidate, ...] = ()
    switch_conditions: tuple[str, ...] = ()
    action_intent: ActionIntent | None = None
    position_state: PositionState | None = None
    execution_state: ExecutionState | None = None
    plan_id: str | None = None
    trade_date: date | None = None
    plan_revision: int = 0
    position_revision: int = 0
    presentation: ActionPresentation | None = None
    last_valid_action: TradingAction | None = None
    last_valid_scenario: ScenarioCandidate | None = None
    last_valid_at: datetime | None = None

    def __post_init__(self) -> None:
        require_aware_datetime(self.computed_at, field_name="DecisionResult.computed_at")
        if not self.data_freshness.can_generate_action and (
            self.action is not None or self.current_scenario is not None
        ):
            raise ContractError("数据不可用时不得生成新 Action 或当前 Scenario")
        if self.action is not None and self.action_intent is None:
            raise ContractError("生成 Action 时必须声明 action_intent")
        if self.plan_revision < 0 or self.position_revision < 0:
            raise ContractError("DecisionResult 版本号不能为负数")
        if self.plan_id is not None and not self.plan_id.strip():
            raise ContractError("DecisionResult.plan_id 不能为空字符串")
        if self.last_valid_at is not None:
            require_aware_datetime(self.last_valid_at, field_name="DecisionResult.last_valid_at")
        if self.position_state is not None and self.action is not None:
            validate_action_for_position(self.position_state, self.action)
        if not self.data_freshness.can_generate_action and (
            self.presentation is not None and self.presentation.executable
        ):
            raise ContractError("数据不可用时展示契约必须为不可执行")
