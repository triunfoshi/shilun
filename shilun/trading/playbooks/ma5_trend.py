"""MA5 Trend v0.2 的周一 P0 硬规则 Playbook。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from shilun.trading.config import TradingRuleConfig
from shilun.trading.contracts import (
    ActionIntent,
    ContractError,
    DecisionResult,
    FactSourceSnapshot,
    MatchLevel,
    RuleState,
    RuleTrace,
    ScenarioCandidate,
    ScenarioState,
    TradingAction,
    require_aware_datetime,
)
from shilun.trading.freshness import evaluate_action_freshness


@dataclass(frozen=True)
class Ma5WatchPlan:
    ticker: str
    previous_close: float
    open_price: float
    frozen_ma5: float
    hard_stop: float
    previous_high: float
    current_quantity: int

    def __post_init__(self) -> None:
        prices = (
            self.previous_close,
            self.open_price,
            self.frozen_ma5,
            self.hard_stop,
            self.previous_high,
        )
        if not self.ticker.strip() or any(value <= 0 for value in prices):
            raise ContractError("Ma5WatchPlan 缺少 ticker 或有效价格")
        if self.current_quantity < 0:
            raise ContractError("current_quantity 不能为负数")


@dataclass(frozen=True)
class Ma5IntradayFacts:
    computed_at: datetime
    closed_5m_prices: tuple[float, ...]
    current_price: float
    vwap: float | None
    volume_ratio: float | None
    sse_state: str = "unknown"
    chinext_state: str = "unknown"
    sector_state: str = "unknown"

    def __post_init__(self) -> None:
        require_aware_datetime(self.computed_at, field_name="Ma5IntradayFacts.computed_at")
        if self.current_price <= 0:
            raise ContractError("current_price 必须大于 0")
        if any(value <= 0 for value in self.closed_5m_prices):
            raise ContractError("closed_5m_prices 只能包含有效收盘价")


def _trace(
    rule_id: str,
    label: str,
    state: RuleState,
    reason: str,
    *,
    actual=None,
    operator: str = "",
    expected=None,
    source: str = "stock_5m",
) -> RuleTrace:
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


class Ma5TrendPlaybook:
    playbook_id = "ma5_trend_v0_2"
    version = "0.2.0"

    def __init__(self, rules: TradingRuleConfig | None = None) -> None:
        self.rules = rules or TradingRuleConfig()

    def build_scenarios(
        self,
        plan: Ma5WatchPlan,
        facts: Ma5IntradayFacts,
    ) -> tuple[ScenarioCandidate, ...]:
        bars = facts.closed_5m_prices
        latest = bars[-1] if bars else facts.current_price
        previous = bars[-2] if len(bars) >= 2 else None

        hard_stop_hit = bool(bars) and latest <= plan.hard_stop
        hard_stop = ScenarioCandidate(
            scenario_id="hard_stop",
            title="硬止损确认",
            priority=100,
            state=ScenarioState.TRIGGERED if hard_stop_hit else ScenarioState.FAR,
            match_level=MatchLevel.HIGH if hard_stop_hit else MatchLevel.LOW,
            traces=(
                _trace(
                    "hard_stop_close",
                    "5 分钟 K 收于硬止损下方",
                    RuleState.SATISFIED if hard_stop_hit else RuleState.UNSATISFIED,
                    "硬止损已确认" if hard_stop_hit else "硬止损未触发",
                    actual=latest,
                    operator="<=",
                    expected=plan.hard_stop,
                ),
            ),
        )

        consecutive_below = 0
        for close in reversed(bars):
            if close < plan.frozen_ma5:
                consecutive_below += 1
            else:
                break
        if consecutive_below >= self.rules.soft_break_confirmation_bars:
            break_state = ScenarioState.TRIGGERED
            break_rule_state = RuleState.SATISFIED
            break_reason = "连续两根 5 分钟 K 收于冻结 MA5 下方"
            break_match = MatchLevel.HIGH
        elif consecutive_below == 1:
            break_state = ScenarioState.WAITING_CONFIRMATION
            break_rule_state = RuleState.PENDING
            break_reason = "首次跌破，等待第二根 5 分钟 K 确认"
            break_match = MatchLevel.MEDIUM
        else:
            break_state = ScenarioState.FAR
            break_rule_state = RuleState.UNSATISFIED
            break_reason = "当前没有连续跌破"
            break_match = MatchLevel.LOW
        ma5_break = ScenarioCandidate(
            scenario_id="ma5_break",
            title="MA5 软破位",
            priority=80,
            state=break_state,
            match_level=break_match,
            traces=(
                _trace(
                    "ma5_below_count",
                    "连续收盘低于冻结 MA5",
                    break_rule_state,
                    break_reason,
                    actual=consecutive_below,
                    operator=">=",
                    expected=self.rules.soft_break_confirmation_bars,
                ),
            ),
            cancel_conditions=(f"5 分钟 K 重新收于 {plan.frozen_ma5:.2f} 上方",),
        )

        reclaimed = previous is not None and previous < plan.frozen_ma5 <= latest
        reclaim = ScenarioCandidate(
            scenario_id="ma5_reclaim",
            title="跌破后快速收回",
            priority=70,
            state=ScenarioState.TRIGGERED if reclaimed else ScenarioState.FAR,
            match_level=MatchLevel.HIGH if reclaimed else MatchLevel.LOW,
            traces=(
                _trace(
                    "ma5_reclaim_close",
                    "跌破后重新收于冻结 MA5 上方",
                    RuleState.SATISFIED if reclaimed else RuleState.UNSATISFIED,
                    "破位剧本失效，关键位已收回" if reclaimed else "尚未形成跌破后收回",
                    actual=(previous, latest),
                    operator="cross_up",
                    expected=plan.frozen_ma5,
                ),
            ),
        )

        gap_pct = plan.open_price / plan.previous_close - 1.0
        low_open = gap_pct <= self.rules.gap_down_pct
        acceptance = (
            low_open
            and facts.vwap is not None
            and latest >= facts.vwap
            and latest >= plan.frozen_ma5
        )
        low_open_state = (
            ScenarioState.TRIGGERED
            if acceptance
            else ScenarioState.WAITING_CONFIRMATION
            if low_open
            else ScenarioState.FAR
        )
        low_open_acceptance = ScenarioCandidate(
            scenario_id="low_open_acceptance",
            title="低开后的承接恢复",
            priority=60,
            state=low_open_state,
            match_level=MatchLevel.HIGH if acceptance else MatchLevel.MEDIUM if low_open else MatchLevel.LOW,
            traces=(
                _trace(
                    "open_gap_down",
                    "达到低开阈值",
                    RuleState.SATISFIED if low_open else RuleState.UNSATISFIED,
                    "低开成立" if low_open else "不是低开剧本",
                    actual=round(gap_pct, 6),
                    operator="<=",
                    expected=self.rules.gap_down_pct,
                    source="stock_quote",
                ),
                _trace(
                    "acceptance_above_vwap_ma5",
                    "重新站上 VWAP 与冻结 MA5",
                    RuleState.SATISFIED if acceptance else RuleState.PENDING if low_open else RuleState.UNSATISFIED,
                    "分时承接恢复" if acceptance else "等待承接确认" if low_open else "不适用",
                    actual={"close": latest, "vwap": facts.vwap},
                    operator=">=",
                    expected=plan.frozen_ma5,
                ),
            ),
        )

        price_breakout = latest > plan.previous_high
        volume_confirmed = (
            facts.volume_ratio is not None
            and facts.volume_ratio >= self.rules.elevated_volume_ratio
        )
        breakout = price_breakout and volume_confirmed
        breakout_hold = ScenarioCandidate(
            scenario_id="breakout_hold",
            title="放量突破前高",
            # 即使当天低开，一旦量价完成前高突破，当前剧本应切换到突破持有。
            priority=75,
            state=ScenarioState.TRIGGERED if breakout else ScenarioState.WAITING_CONFIRMATION if price_breakout else ScenarioState.FAR,
            match_level=MatchLevel.HIGH if breakout else MatchLevel.MEDIUM if price_breakout else MatchLevel.LOW,
            traces=(
                _trace(
                    "break_previous_high",
                    "价格突破前高",
                    RuleState.SATISFIED if price_breakout else RuleState.UNSATISFIED,
                    "已突破前高" if price_breakout else "尚未突破前高",
                    actual=latest,
                    operator=">",
                    expected=plan.previous_high,
                ),
                _trace(
                    "breakout_volume",
                    "成交量达到放量阈值",
                    RuleState.SATISFIED if volume_confirmed else RuleState.PENDING if price_breakout else RuleState.UNSATISFIED,
                    "量价确认完成" if volume_confirmed else "等待成交量确认",
                    actual=facts.volume_ratio,
                    operator=">=",
                    expected=self.rules.elevated_volume_ratio,
                ),
            ),
        )

        weak_states = {"weak", "退潮", "走弱"}
        environment_weak = any(
            state in weak_states
            for state in (facts.sse_state, facts.chinext_state, facts.sector_state)
        )
        base_hold = ScenarioCandidate(
            scenario_id="base_hold",
            title="原计划持有",
            priority=20,
            state=ScenarioState.TRIGGERED,
            match_level=MatchLevel.MEDIUM if environment_weak else MatchLevel.HIGH,
            traces=(
                _trace(
                    "environment_separate_view",
                    "大盘、板块与个股环境分别检查",
                    RuleState.UNSATISFIED if environment_weak else RuleState.SATISFIED,
                    "环境中存在走弱项，持有但提高警戒" if environment_weak else "环境未触发风险覆盖",
                    actual={
                        "sse": facts.sse_state,
                        "chinext": facts.chinext_state,
                        "sector": facts.sector_state,
                    },
                    source="market_context",
                ),
            ),
        )
        return tuple(sorted(
            (hard_stop, ma5_break, reclaim, low_open_acceptance, breakout_hold, base_hold),
            key=lambda item: item.priority,
            reverse=True,
        ))

    def evaluate(
        self,
        plan: Ma5WatchPlan,
        facts: Ma5IntradayFacts,
        sources: tuple[FactSourceSnapshot, ...],
    ) -> DecisionResult:
        scenarios = self.build_scenarios(plan, facts)
        hard_stop = next(item for item in scenarios if item.scenario_id == "hard_stop")
        defensive_freshness = evaluate_action_freshness(
            sources,
            now=facts.computed_at,
            policy=self.rules.freshness,
            intent=ActionIntent.DEFENSIVE,
        )
        if hard_stop.state is ScenarioState.TRIGGERED and defensive_freshness.can_generate_action:
            return DecisionResult(
                ticker=plan.ticker,
                computed_at=facts.computed_at,
                data_freshness=defensive_freshness,
                action=TradingAction(
                    action_type="risk_exit",
                    label="执行风险退出",
                    target_quantity=0,
                    change_quantity=-plan.current_quantity,
                ),
                action_intent=ActionIntent.DEFENSIVE,
                reasons=("个股 5 分钟 K 已确认硬止损，防御动作不等待指数或板块数据",),
                current_scenario=hard_stop,
                candidate_scenarios=scenarios,
            )

        active_freshness = evaluate_action_freshness(
            sources,
            now=facts.computed_at,
            policy=self.rules.freshness,
            intent=ActionIntent.ACTIVE,
        )
        if not active_freshness.can_generate_action:
            return DecisionResult(
                ticker=plan.ticker,
                computed_at=facts.computed_at,
                data_freshness=active_freshness,
                action=None,
                action_intent=None,
                reasons=("主动判断所需环境数据不完整，停止生成新动作", *active_freshness.reasons),
                current_scenario=None,
                candidate_scenarios=scenarios,
            )

        current = next(
            item for item in scenarios
            if item.state in {
                ScenarioState.TRIGGERED,
                ScenarioState.WAITING_CONFIRMATION,
                ScenarioState.APPROACHING,
            }
        )
        if current.scenario_id == "ma5_break":
            if current.state is ScenarioState.WAITING_CONFIRMATION:
                action = TradingAction("wait_confirmation", "等待第二根 5 分钟 K 确认", plan.current_quantity, 0)
            else:
                action = TradingAction("prepare_reduce", "准备降低仓位", None, None)
        else:
            action = TradingAction("hold", "继续持有", plan.current_quantity, 0)
        reasons = tuple(trace.reason for trace in current.traces)
        return DecisionResult(
            ticker=plan.ticker,
            computed_at=facts.computed_at,
            data_freshness=active_freshness,
            action=action,
            action_intent=ActionIntent.ACTIVE,
            reasons=reasons,
            current_scenario=current,
            candidate_scenarios=scenarios,
            switch_conditions=(
                f"连续两根 5 分钟 K 收于 {plan.frozen_ma5:.2f} 下方 → MA5 破位",
                f"一根 5 分钟 K 收于 {plan.hard_stop:.2f} 下方 → 硬止损",
                f"放量突破 {plan.previous_high:.2f} → 突破持有",
            ),
        )
