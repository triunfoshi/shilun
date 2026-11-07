"""E8 首板候选的 T 日买点判定。

本模块只消费已经计算好的行业、个股和五分钟线事实，不读取数据库、不调用
行情接口，也不修改现有 E1-E7。行业门使用“缺失成员按未上涨处理”的保守
下界，避免因为行业成分覆盖不完整而高估上涨占比。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import StrEnum
import math
from statistics import median
from typing import Any, Iterable

from shilun.trading.contracts import ActionType, ContractError, RuleState, RuleTrace


class E8DecisionState(StrEnum):
    """E8 在买点观察窗内的稳定状态。"""

    AUCTION_ONLY = "auction_only"
    DATA_BLOCKED = "data_blocked"
    MISSED = "missed"
    CONFIRMING = "confirming"
    ENTRY_READY = "entry_ready"


class E8Setup(StrEnum):
    NONE = "none"
    SECONDARY_BURST = "secondary_burst"
    OPENING_BURST_RECLAIM = "opening_burst_reclaim"


class E8IndustryGatePath(StrEnum):
    NONE = "none"
    BREADTH_85 = "breadth_85"
    RELATIVE_OUTPERFORMANCE = "relative_outperformance"


@dataclass(frozen=True)
class E8RuleConfig:
    """E8 规则阈值；百分比均使用小数，例如 0.02 表示 2 个百分点。"""

    version: str = "e8-first-board-v0.1.0"
    entry_start: time = time(9, 35)
    entry_cutoff: time = time(10, 30)
    minimum_valid_peers: int = 8
    minimum_peer_coverage: float = 0.80
    direct_breadth_threshold: float = 0.85
    relative_breadth_threshold: float = 0.60
    sector_excess_index_threshold: float = 0.02
    stock_excess_index_threshold: float = 0.03
    stock_excess_sector_threshold: float = 0.01
    maximum_vwap_premium: float = 0.035
    maximum_intraday_gain: float = 0.07
    minimum_distance_to_limit: float = 0.02
    secondary_amount_ratio: float = 1.80
    secondary_baseline_bars: int = 3
    secondary_baseline_lookback: int = 5
    opening_amount_ratio: float = 3.00
    opening_contraction_ratio: float = 0.70
    maximum_opening_pullback: float = 0.02
    breakout_lookback_bars: int = 3
    maximum_bar_staleness_minutes: float = 10.0

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ContractError("E8RuleConfig.version 不能为空")
        if self.entry_start >= self.entry_cutoff:
            raise ContractError("E8 买点开始时间必须早于截止时间")
        if self.minimum_valid_peers < 1:
            raise ContractError("minimum_valid_peers 必须大于 0")
        if self.secondary_baseline_bars < 1:
            raise ContractError("secondary_baseline_bars 必须大于 0")
        if self.secondary_baseline_lookback < self.secondary_baseline_bars:
            raise ContractError("secondary_baseline_lookback 不能小于基准 bar 数")
        if self.breakout_lookback_bars < 1:
            raise ContractError("breakout_lookback_bars 必须大于 0")
        unit_interval_fields = (
            "minimum_peer_coverage",
            "direct_breadth_threshold",
            "relative_breadth_threshold",
            "maximum_vwap_premium",
            "maximum_intraday_gain",
            "minimum_distance_to_limit",
            "opening_contraction_ratio",
            "maximum_opening_pullback",
        )
        for field_name in unit_interval_fields:
            value = getattr(self, field_name)
            if not 0 <= value <= 1:
                raise ContractError(f"{field_name} 必须位于 [0, 1]")
        positive_fields = (
            "sector_excess_index_threshold",
            "stock_excess_index_threshold",
            "stock_excess_sector_threshold",
            "secondary_amount_ratio",
            "opening_amount_ratio",
            "maximum_bar_staleness_minutes",
        )
        for field_name in positive_fields:
            if getattr(self, field_name) <= 0:
                raise ContractError(f"{field_name} 必须大于 0")


DEFAULT_E8_RULES = E8RuleConfig()


@dataclass(frozen=True)
class IndustryPeerReturn:
    ticker: str
    return_pct: float | None


@dataclass(frozen=True)
class E8IndustryFacts:
    """行业横截面；expected_member_count 包含目标股。"""

    target_ticker: str
    expected_member_count: int | None
    peers: tuple[IndustryPeerReturn, ...]
    matching_index_return: float | None


@dataclass(frozen=True)
class E8FiveMinuteBar:
    closed_at: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    amount: float | None


@dataclass(frozen=True)
class E8StockFacts:
    ticker: str
    computed_at: datetime
    previous_close: float | None
    current_price: float | None
    vwap: float | None
    up_limit: float | None
    matching_index_return: float | None
    closed_bars: tuple[E8FiveMinuteBar, ...] = ()
    open_price: float | None = None
    opening_amount_ratio: float | None = None
    auction_only: bool = False
    up_limit_sealed: bool = False


@dataclass(frozen=True)
class E8Decision:
    state: E8DecisionState
    action_type: ActionType
    setup: E8Setup
    industry_gate_path: E8IndustryGatePath
    traces: tuple[RuleTrace, ...]
    evidence: tuple[str, ...]
    metrics: dict[str, Any] = field(default_factory=dict)
    cancel_conditions: tuple[str, ...] = (
        "行业门失效",
        "个股跌回 VWAP 下方或相对强度失效",
        "当前涨幅超过 7%",
        "距涨停不足 2%",
        "10:30 后不再追价",
    )
    scenario_id: str = "E8"
    title: str = "首板候选早盘确认"

    @property
    def evidence_completeness(self) -> str:
        resolved = sum(
            trace.state in {RuleState.SATISFIED, RuleState.UNSATISFIED}
            for trace in self.traces
        )
        return f"{resolved}/{len(self.traces)}"

    def to_record(self) -> dict[str, Any]:
        """转换为可直接持久化或作为 API evidence 使用的普通字典。"""

        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "state": self.state.value,
            "action_type": self.action_type.value,
            "setup": self.setup.value,
            "industry_gate_path": self.industry_gate_path.value,
            "evidence_completeness": self.evidence_completeness,
            "evidence": list(self.evidence),
            "metrics": self.metrics,
            "cancel_conditions": list(self.cancel_conditions),
            "traces": [
                {
                    "rule_id": trace.rule_id,
                    "label": trace.label,
                    "state": trace.state.value,
                    "reason": trace.reason,
                    "actual": trace.actual,
                    "operator": trace.operator,
                    "expected": trace.expected,
                    "evidence_source": trace.evidence_source,
                }
                for trace in self.traces
            ],
        }


@dataclass(frozen=True)
class _IndustryEvaluation:
    gate: bool | None
    path: E8IndustryGatePath
    sector_return: float | None
    metrics: dict[str, Any]
    traces: tuple[RuleTrace, ...]
    blocking_reasons: tuple[str, ...]


@dataclass(frozen=True)
class _BarEvaluation:
    bars: tuple[E8FiveMinuteBar, ...]
    metrics: dict[str, Any]
    blocking_reasons: tuple[str, ...]


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _positive(value: Any) -> float | None:
    result = _finite(value)
    return result if result is not None and result > 0 else None


def _normal_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _is_aware(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _rule(
    rule_id: str,
    label: str,
    state: RuleState,
    reason: str,
    *,
    actual: Any = None,
    operator: str = "",
    expected: Any = None,
    source: str,
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


def _threshold_trace(
    rule_id: str,
    label: str,
    actual: float | None,
    operator: str,
    expected: float,
    *,
    source: str,
    missing_reason: str,
) -> RuleTrace:
    if actual is None:
        return _rule(
            rule_id,
            label,
            RuleState.BLOCKED,
            missing_reason,
            actual=None,
            operator=operator,
            expected=expected,
            source=source,
        )
    passed = actual >= expected if operator == ">=" else actual <= expected
    return _rule(
        rule_id,
        label,
        RuleState.SATISFIED if passed else RuleState.UNSATISFIED,
        f"实际值 {actual:.4f} {'达到' if passed else '未达到'}阈值 {expected:.4f}",
        actual=actual,
        operator=operator,
        expected=expected,
        source=source,
    )


def _deduplicate_peers(
    peers: Iterable[IndustryPeerReturn], target_ticker: str
) -> tuple[dict[str, float | None], int, int]:
    rows: dict[str, float | None] = {}
    excluded_target_rows = 0
    duplicate_rows = 0
    for peer in peers:
        ticker = _normal_ticker(peer.ticker)
        if not ticker:
            continue
        if ticker == target_ticker:
            excluded_target_rows += 1
            continue
        if ticker in rows:
            duplicate_rows += 1
            continue
        rows[ticker] = _finite(peer.return_pct)
    return rows, excluded_target_rows, duplicate_rows


def _evaluate_industry(
    facts: E8IndustryFacts, config: E8RuleConfig
) -> _IndustryEvaluation:
    target = _normal_ticker(facts.target_ticker)
    rows, excluded_rows, duplicate_rows = _deduplicate_peers(facts.peers, target)
    valid_returns = [value for value in rows.values() if value is not None]
    valid_count = len(valid_returns)
    up_count = sum(value > 0 for value in valid_returns)
    observed_peer_count = len(rows)
    declared_total = facts.expected_member_count
    declared_peers = (
        declared_total - 1
        if isinstance(declared_total, int) and not isinstance(declared_total, bool) and declared_total > 1
        else None
    )
    expected_peer_count = (
        max(declared_peers, observed_peer_count) if declared_peers is not None else None
    )
    coverage = (
        valid_count / expected_peer_count
        if expected_peer_count is not None and expected_peer_count > 0
        else None
    )
    observed_up_ratio = up_count / valid_count if valid_count else None
    conservative_up_lower_bound = (
        up_count / expected_peer_count
        if expected_peer_count is not None and expected_peer_count > 0
        else None
    )
    sector_return = median(valid_returns) if valid_returns else None
    index_return = _finite(facts.matching_index_return)
    sector_excess = (
        sector_return - index_return
        if sector_return is not None and index_return is not None
        else None
    )

    count_ok = valid_count >= config.minimum_valid_peers
    coverage_ok = coverage is not None and coverage >= config.minimum_peer_coverage
    cross_section_reliable = count_ok and coverage_ok
    direct_pass = (
        cross_section_reliable
        and conservative_up_lower_bound is not None
        and conservative_up_lower_bound >= config.direct_breadth_threshold
    )
    relative_pass = (
        cross_section_reliable
        and conservative_up_lower_bound is not None
        and conservative_up_lower_bound >= config.relative_breadth_threshold
        and sector_excess is not None
        and sector_excess >= config.sector_excess_index_threshold
    )

    blocking_reasons: list[str] = []
    if expected_peer_count is None:
        blocking_reasons.append("行业预期成分数缺失或不合法")
    if not count_ok:
        blocking_reasons.append(
            f"行业有效样本仅 {valid_count} 只，少于 {config.minimum_valid_peers} 只"
        )
    if coverage is None or not coverage_ok:
        blocking_reasons.append("行业成分覆盖率不足，不能可靠计算上涨占比下界")
    if cross_section_reliable and not direct_pass and index_return is None:
        blocking_reasons.append("85% 广度门未通过且匹配指数收益缺失，无法验证相对强势分支")

    if blocking_reasons:
        gate: bool | None = None
        path = E8IndustryGatePath.NONE
    elif direct_pass:
        gate = True
        path = E8IndustryGatePath.BREADTH_85
    elif relative_pass:
        gate = True
        path = E8IndustryGatePath.RELATIVE_OUTPERFORMANCE
    else:
        gate = False
        path = E8IndustryGatePath.NONE

    lower_bound_trace = _threshold_trace(
        "e8.industry.breadth_85",
        "行业上涨占比保守下界达到 85%",
        conservative_up_lower_bound if cross_section_reliable else None,
        ">=",
        config.direct_breadth_threshold,
        source="industry_constituent_returns",
        missing_reason="行业样本数量或覆盖率不足，广度下界不可用",
    )
    relative_breadth_trace = _threshold_trace(
        "e8.industry.relative_breadth_60",
        "相对强势分支的上涨占比下界达到 60%",
        conservative_up_lower_bound if cross_section_reliable else None,
        ">=",
        config.relative_breadth_threshold,
        source="industry_constituent_returns",
        missing_reason="行业样本数量或覆盖率不足，相对分支广度不可用",
    )
    sector_excess_trace = _threshold_trace(
        "e8.industry.excess_index",
        "行业明显跑赢匹配指数",
        sector_excess if cross_section_reliable else None,
        ">=",
        config.sector_excess_index_threshold,
        source="industry_constituent_returns+matching_index_quote",
        missing_reason="行业收益或匹配指数收益不可用",
    )
    traces = (
        _rule(
            "e8.industry.exclude_target",
            "行业统计排除目标股",
            RuleState.SATISFIED,
            f"按股票代码排除了 {excluded_rows} 条目标股记录；即使输入中没有目标股，也不会将其计入分子或分母",
            actual=excluded_rows,
            operator=">=",
            expected=0,
            source="industry_constituent_returns",
        ),
        _rule(
            "e8.industry.valid_count",
            "行业有效样本数",
            RuleState.SATISFIED if count_ok else RuleState.UNSATISFIED,
            f"有效收益样本 {valid_count} 只",
            actual=valid_count,
            operator=">=",
            expected=config.minimum_valid_peers,
            source="industry_constituent_returns",
        ),
        _threshold_trace(
            "e8.industry.coverage",
            "行业成分覆盖率",
            coverage,
            ">=",
            config.minimum_peer_coverage,
            source="industry_constituent_returns",
            missing_reason="预期行业成分数不可用",
        ),
        lower_bound_trace,
        relative_breadth_trace,
        sector_excess_trace,
        _rule(
            "e8.industry.gate",
            "行业门通过",
            (
                RuleState.BLOCKED
                if gate is None
                else RuleState.SATISFIED if gate else RuleState.UNSATISFIED
            ),
            (
                "; ".join(blocking_reasons)
                if gate is None
                else f"通过路径：{path.value}" if gate else "85% 广度与相对强势两条路径均未通过"
            ),
            actual=path.value if gate else None,
            operator="OR",
            expected="breadth_85 | relative_outperformance",
            source="derived",
        ),
    )
    metrics = {
        "declared_member_count_including_target": declared_total,
        "expected_peer_count_excluding_target": expected_peer_count,
        "observed_unique_peer_count": observed_peer_count,
        "excluded_target_rows": excluded_rows,
        "duplicate_peer_rows_ignored": duplicate_rows,
        "valid_peer_count": valid_count,
        "missing_peer_count": (
            expected_peer_count - valid_count if expected_peer_count is not None else None
        ),
        "coverage": coverage,
        "up_peer_count": up_count,
        "observed_up_ratio": observed_up_ratio,
        "conservative_up_ratio_lower_bound": conservative_up_lower_bound,
        "sector_median_return": sector_return,
        "matching_index_return": index_return,
        "sector_excess_index": sector_excess,
        "direct_path_passed": direct_pass,
        "relative_path_passed": relative_pass,
        "gate": gate,
        "gate_path": path.value,
    }
    return _IndustryEvaluation(
        gate=gate,
        path=path,
        sector_return=sector_return,
        metrics=metrics,
        traces=traces,
        blocking_reasons=tuple(blocking_reasons),
    )


def _evaluate_bars(facts: E8StockFacts, config: E8RuleConfig) -> _BarEvaluation:
    computed_at = facts.computed_at
    blocking_reasons: list[str] = []
    normalized: list[E8FiveMinuteBar] = []
    seen_times: set[datetime] = set()
    if not _is_aware(computed_at):
        return _BarEvaluation((), {}, ("computed_at 缺少时区，无法校验五分钟线",))

    for index, bar in enumerate(facts.closed_bars):
        if not _is_aware(bar.closed_at):
            blocking_reasons.append(f"第 {index + 1} 根五分钟线时间缺少时区")
            continue
        if bar.closed_at in seen_times:
            blocking_reasons.append(f"五分钟线闭合时间重复：{bar.closed_at.isoformat()}")
            continue
        seen_times.add(bar.closed_at)
        if bar.closed_at > computed_at:
            blocking_reasons.append(f"存在晚于 computed_at 的未来五分钟线：{bar.closed_at.isoformat()}")
            continue
        if bar.closed_at.date() != computed_at.date():
            blocking_reasons.append(f"存在非当日五分钟线：{bar.closed_at.date().isoformat()}")
            continue
        prices = tuple(_positive(value) for value in (bar.open, bar.high, bar.low, bar.close))
        amount = _positive(bar.amount)
        if any(value is None for value in prices) or amount is None:
            blocking_reasons.append(f"{bar.closed_at.isoformat()} 的 OHLC 或成交额无效")
            continue
        open_price, high, low, close = prices
        if high < max(open_price, low, close) or low > min(open_price, high, close):
            blocking_reasons.append(f"{bar.closed_at.isoformat()} 的 OHLC 高低关系不合法")
            continue
        normalized.append(
            E8FiveMinuteBar(
                closed_at=bar.closed_at,
                open=open_price,
                high=high,
                low=low,
                close=close,
                amount=amount,
            )
        )

    normalized.sort(key=lambda item: item.closed_at)
    latest_at = normalized[-1].closed_at if normalized else None
    staleness_minutes = (
        (computed_at - latest_at).total_seconds() / 60 if latest_at is not None else None
    )
    if not normalized:
        blocking_reasons.append("买点观察窗内没有已闭合五分钟线")
    elif staleness_minutes is not None and staleness_minutes > config.maximum_bar_staleness_minutes:
        blocking_reasons.append(
            f"最新五分钟线滞后 {staleness_minutes:.1f} 分钟，超过允许的 {config.maximum_bar_staleness_minutes:.1f} 分钟"
        )

    return _BarEvaluation(
        bars=tuple(normalized),
        metrics={
            "completed_bar_count": len(normalized),
            "latest_bar_closed_at": latest_at.isoformat() if latest_at is not None else None,
            "bar_staleness_minutes": staleness_minutes,
            "invalid_bar_reason_count": len(blocking_reasons),
        },
        blocking_reasons=tuple(blocking_reasons),
    )


def _maximum_high_to_later_low_drawdown(bars: tuple[E8FiveMinuteBar, ...]) -> float | None:
    if not bars:
        return None
    running_high = _positive(bars[0].high)
    if running_high is None:
        return None
    maximum = 0.0
    for bar in bars:
        high = _positive(bar.high)
        low = _positive(bar.low)
        if high is None or low is None:
            return None
        running_high = max(running_high, high)
        maximum = max(maximum, (running_high - low) / running_high)
    return maximum


def _pending_rule(
    rule_id: str, label: str, reason: str, *, source: str, expected: Any = None
) -> RuleTrace:
    return _rule(
        rule_id,
        label,
        RuleState.PENDING,
        reason,
        actual=None,
        expected=expected,
        source=source,
    )


def _setup_evaluation(
    facts: E8StockFacts,
    bars: tuple[E8FiveMinuteBar, ...],
    current_price: float,
    config: E8RuleConfig,
) -> tuple[E8Setup, tuple[RuleTrace, ...], dict[str, Any]]:
    traces: list[RuleTrace] = []
    latest = bars[-1] if bars else None
    prior = bars[:-1]
    baseline = prior[-config.secondary_baseline_lookback :]
    secondary_history_ready = len(baseline) >= config.secondary_baseline_bars
    baseline_amount = (
        median(float(item.amount) for item in baseline)
        if secondary_history_ready
        else None
    )
    secondary_ratio = (
        float(latest.amount) / baseline_amount
        if latest is not None and baseline_amount is not None and baseline_amount > 0
        else None
    )
    platform_bars = prior[-config.breakout_lookback_bars :]
    platform_high = (
        max(float(item.high) for item in platform_bars) if platform_bars else None
    )
    breaks_platform = platform_high is not None and current_price > platform_high
    secondary_ratio_ok = (
        secondary_ratio is not None and secondary_ratio >= config.secondary_amount_ratio
    )
    secondary_ready = secondary_history_ready and secondary_ratio_ok and breaks_platform

    if secondary_history_ready:
        traces.append(
            _threshold_trace(
                "e8.setup.secondary_amount",
                "二次暴量成交额倍数",
                secondary_ratio,
                ">=",
                config.secondary_amount_ratio,
                source="minute_bars_5m",
                missing_reason="最新 bar 或历史成交额不可用",
            )
        )
    else:
        traces.append(
            _pending_rule(
                "e8.setup.secondary_amount",
                "二次暴量成交额倍数",
                f"仅有 {len(baseline)} 根历史 bar，尚不足 {config.secondary_baseline_bars} 根基准 bar",
                expected=config.secondary_amount_ratio,
                source="minute_bars_5m",
            )
        )
    traces.append(
        _rule(
            "e8.setup.break_platform",
            "价格突破最近平台高点",
            (
                RuleState.PENDING
                if platform_high is None
                else RuleState.SATISFIED if breaks_platform else RuleState.UNSATISFIED
            ),
            (
                "历史 bar 不足，平台高点尚未形成"
                if platform_high is None
                else f"当前价 {current_price:.4f} {'突破' if breaks_platform else '尚未突破'}平台高点 {platform_high:.4f}"
            ),
            actual=current_price,
            operator=">",
            expected=platform_high,
            source="stock_realtime_quote+minute_bars_5m",
        )
    )

    opening_ratio = _finite(facts.opening_amount_ratio)
    opening_ratio_ok = (
        opening_ratio is not None and opening_ratio >= config.opening_amount_ratio
    )
    if opening_ratio is None:
        traces.append(
            _rule(
                "e8.setup.opening_amount",
                "开盘暴量倍数",
                RuleState.BLOCKED,
                "开盘成交额相对基线倍数缺失；该分支不可用，但不阻断二次暴量分支",
                actual=None,
                operator=">=",
                expected=config.opening_amount_ratio,
                source="opening_amount_baseline",
            )
        )
    else:
        traces.append(
            _threshold_trace(
                "e8.setup.opening_amount",
                "开盘暴量倍数",
                opening_ratio,
                ">=",
                config.opening_amount_ratio,
                source="opening_amount_baseline",
                missing_reason="开盘暴量倍数缺失",
            )
        )

    opening_history_ready = len(bars) >= 4
    opening_baseline_amount = (
        median(float(item.amount) for item in bars[:2]) if opening_history_ready else None
    )
    recent_amount = (
        median(float(item.amount) for item in bars[-2:]) if opening_history_ready else None
    )
    contraction_ratio = (
        recent_amount / opening_baseline_amount
        if recent_amount is not None
        and opening_baseline_amount is not None
        and opening_baseline_amount > 0
        else None
    )
    contraction_ok = (
        contraction_ratio is not None
        and contraction_ratio <= config.opening_contraction_ratio
    )
    if opening_history_ready:
        traces.append(
            _threshold_trace(
                "e8.setup.opening_contraction",
                "开盘暴量后成交额收敛",
                contraction_ratio,
                "<=",
                config.opening_contraction_ratio,
                source="minute_bars_5m",
                missing_reason="开盘或最近成交额不可用",
            )
        )
    else:
        traces.append(
            _pending_rule(
                "e8.setup.opening_contraction",
                "开盘暴量后成交额收敛",
                f"仅有 {len(bars)} 根 bar，至少需要 4 根才能比较开盘与最近成交额",
                expected=config.opening_contraction_ratio,
                source="minute_bars_5m",
            )
        )

    opening_pullback = _maximum_high_to_later_low_drawdown(bars)
    pullback_ok = (
        opening_pullback is not None
        and opening_pullback <= config.maximum_opening_pullback
    )
    traces.append(
        _threshold_trace(
            "e8.setup.opening_pullback",
            "开盘暴量后的最大回撤受控",
            opening_pullback,
            "<=",
            config.maximum_opening_pullback,
            source="minute_bars_5m",
            missing_reason="五分钟线不足，无法计算最大回撤",
        )
    )

    opening_ready = (
        opening_ratio_ok
        and opening_history_ready
        and contraction_ok
        and pullback_ok
        and breaks_platform
    )
    setup = (
        E8Setup.OPENING_BURST_RECLAIM
        if opening_ready
        else E8Setup.SECONDARY_BURST if secondary_ready else E8Setup.NONE
    )
    traces.append(
        _rule(
            "e8.setup.confirmed",
            "资金形态完成确认",
            RuleState.SATISFIED if setup is not E8Setup.NONE else RuleState.UNSATISFIED,
            (
                f"命中 {setup.value}"
                if setup is not E8Setup.NONE
                else "开盘暴量承接与二次暴量两种形态均未完整确认"
            ),
            actual=setup.value,
            operator="OR",
            expected="opening_burst_reclaim | secondary_burst",
            source="derived",
        )
    )
    metrics = {
        "secondary_baseline_bar_count": len(baseline),
        "secondary_baseline_amount": baseline_amount,
        "secondary_amount_ratio": secondary_ratio,
        "secondary_ready": secondary_ready,
        "platform_high": platform_high,
        "breaks_platform": breaks_platform,
        "opening_amount_ratio": opening_ratio,
        "opening_baseline_amount": opening_baseline_amount,
        "recent_amount": recent_amount,
        "opening_contraction_ratio": contraction_ratio,
        "opening_max_pullback": opening_pullback,
        "opening_ready": opening_ready,
        "selected_setup": setup.value,
    }
    return setup, tuple(traces), metrics


def _finish(
    *,
    state: E8DecisionState,
    action: ActionType,
    setup: E8Setup,
    industry_path: E8IndustryGatePath,
    traces: list[RuleTrace],
    evidence: Iterable[str],
    metrics: dict[str, Any],
) -> E8Decision:
    return E8Decision(
        state=state,
        action_type=action,
        setup=setup,
        industry_gate_path=industry_path,
        traces=tuple(traces),
        evidence=tuple(item for item in evidence if item),
        metrics=metrics,
    )


def evaluate_e8_buy_point(
    industry: E8IndustryFacts,
    stock: E8StockFacts,
    config: E8RuleConfig = DEFAULT_E8_RULES,
) -> E8Decision:
    """判定 E8 T 日买点，返回状态、动作、指标快照和完整规则轨迹。"""

    traces: list[RuleTrace] = []
    structural_errors: list[str] = []
    industry_eval = _evaluate_industry(industry, config)
    bar_eval = _evaluate_bars(stock, config)

    stock_ticker = _normal_ticker(stock.ticker)
    industry_target = _normal_ticker(industry.target_ticker)
    if not stock_ticker:
        structural_errors.append("目标股票代码为空")
    if not industry_target:
        structural_errors.append("行业目标股票代码为空")
    if stock_ticker and industry_target and stock_ticker != industry_target:
        structural_errors.append(
            f"个股代码 {stock_ticker} 与行业统计目标 {industry_target} 不一致"
        )
    computed_at_valid = _is_aware(stock.computed_at)
    if not computed_at_valid:
        structural_errors.append("computed_at 必须包含时区")

    current_price = _positive(stock.current_price)
    previous_close = _positive(stock.previous_close)
    vwap = _positive(stock.vwap)
    up_limit = _positive(stock.up_limit)
    if current_price is None:
        structural_errors.append("当前价缺失或不合法")
    if previous_close is None:
        structural_errors.append("昨收价缺失或不合法")
    if vwap is None:
        structural_errors.append("VWAP 缺失或不合法")
    if up_limit is None:
        structural_errors.append("涨停价缺失或不合法")
    if current_price is not None and up_limit is not None and up_limit <= current_price:
        if not stock.up_limit_sealed:
            structural_errors.append("当前价不低于涨停价，但未标记为已封板")

    traces.append(
        _rule(
            "e8.input.target_consistency",
            "行业与个股目标一致",
            RuleState.SATISFIED if not any("代码" in item for item in structural_errors) else RuleState.BLOCKED,
            (
                "行业统计目标与个股代码一致"
                if stock_ticker and stock_ticker == industry_target
                else "；".join(item for item in structural_errors if "代码" in item)
            ),
            actual=stock_ticker or None,
            operator="==",
            expected=industry_target or None,
            source="input_contract",
        )
    )
    traces.append(
        _rule(
            "e8.input.timestamp",
            "判定时间带时区",
            RuleState.SATISFIED if computed_at_valid else RuleState.BLOCKED,
            "computed_at 可用于盘中窗口判断" if computed_at_valid else "computed_at 缺少时区",
            actual=stock.computed_at.isoformat() if computed_at_valid else None,
            source="input_contract",
        )
    )
    quote_errors = [
        item
        for item in structural_errors
        if any(
            token in item
            for token in ("当前价", "昨收价", "VWAP", "涨停价", "封板")
        )
    ]
    traces.append(
        _rule(
            "e8.input.quote_fields",
            "个股核心报价完整",
            RuleState.BLOCKED if quote_errors else RuleState.SATISFIED,
            "；".join(quote_errors) if quote_errors else "当前价、昨收、VWAP 和涨停价均有效",
            actual={
                "current_price": current_price,
                "previous_close": previous_close,
                "vwap": vwap,
                "up_limit": up_limit,
            },
            operator="all_valid",
            expected=True,
            source="stock_realtime_quote",
        )
    )
    traces.extend(industry_eval.traces)

    metrics: dict[str, Any] = {
        "rule_version": config.version,
        "computed_at": stock.computed_at.isoformat() if computed_at_valid else None,
        "industry": industry_eval.metrics,
        "minute_bars": bar_eval.metrics,
        "stock": {},
        "setups": {},
        "data_quality": {
            "structural_errors": list(structural_errors),
            "industry_blocking_reasons": list(industry_eval.blocking_reasons),
            "bar_blocking_reasons": list(bar_eval.blocking_reasons),
        },
    }

    if structural_errors:
        return _finish(
            state=E8DecisionState.DATA_BLOCKED,
            action=ActionType.PAUSE_NEW_ACTION,
            setup=E8Setup.NONE,
            industry_path=E8IndustryGatePath.NONE,
            traces=traces,
            evidence=("；".join(structural_errors),),
            metrics=metrics,
        )

    assert current_price is not None
    assert previous_close is not None
    assert vwap is not None
    assert up_limit is not None
    assert computed_at_valid

    now = stock.computed_at.timetz().replace(tzinfo=None)
    in_window = config.entry_start <= now <= config.entry_cutoff
    before_window = now < config.entry_start
    traces.append(
        _rule(
            "e8.window",
            "E8 买点观察窗",
            (
                RuleState.SATISFIED
                if in_window
                else RuleState.PENDING if before_window else RuleState.UNSATISFIED
            ),
            (
                "位于 09:35-10:30 买点观察窗"
                if in_window
                else "尚未到 09:35" if before_window else "已晚于 10:30"
            ),
            actual=now.isoformat(timespec="seconds"),
            operator="between",
            expected=f"{config.entry_start.isoformat()}-{config.entry_cutoff.isoformat()}",
            source="exchange_clock",
        )
    )
    traces.append(
        _rule(
            "e8.auction_only",
            "不是仅竞价观察标的",
            RuleState.UNSATISFIED if stock.auction_only else RuleState.SATISFIED,
            "上游标记为仅竞价观察，不生成盘中买点" if stock.auction_only else "可进入盘中确认",
            actual=stock.auction_only,
            operator="==",
            expected=False,
            source="stock_realtime_quote",
        )
    )
    if stock.auction_only:
        return _finish(
            state=E8DecisionState.AUCTION_ONLY,
            action=ActionType.OBSERVE_SETUP,
            setup=E8Setup.NONE,
            industry_path=industry_eval.path,
            traces=traces,
            evidence=("标的被标记为仅竞价观察，盘中不生成追价信号",),
            metrics=metrics,
        )
    if before_window:
        return _finish(
            state=E8DecisionState.CONFIRMING,
            action=ActionType.WAIT_CONFIRMATION,
            setup=E8Setup.NONE,
            industry_path=industry_eval.path,
            traces=traces,
            evidence=("09:35 前只观察竞价和开盘承接，等待买点窗口开启",),
            metrics=metrics,
        )
    if not in_window:
        return _finish(
            state=E8DecisionState.MISSED,
            action=ActionType.NO_CHASE,
            setup=E8Setup.NONE,
            industry_path=industry_eval.path,
            traces=traces,
            evidence=("已晚于 10:30，E8 当日买点作废，不追价",),
            metrics=metrics,
        )

    traces.append(
        _rule(
            "e8.data.minute_bars",
            "五分钟线完整且新鲜",
            RuleState.BLOCKED if bar_eval.blocking_reasons else RuleState.SATISFIED,
            (
                "；".join(bar_eval.blocking_reasons)
                if bar_eval.blocking_reasons
                else f"{len(bar_eval.bars)} 根已闭合 bar 可用，未发现未来数据、重复时间或异常 OHLC"
            ),
            actual={
                "completed_bar_count": len(bar_eval.bars),
                "bar_staleness_minutes": bar_eval.metrics.get("bar_staleness_minutes"),
            },
            operator="valid_and_fresh",
            expected=True,
            source="minute_bars_5m",
        )
    )
    data_errors = list(industry_eval.blocking_reasons) + list(bar_eval.blocking_reasons)
    if data_errors:
        return _finish(
            state=E8DecisionState.DATA_BLOCKED,
            action=ActionType.PAUSE_NEW_ACTION,
            setup=E8Setup.NONE,
            industry_path=E8IndustryGatePath.NONE,
            traces=traces,
            evidence=("；".join(data_errors),),
            metrics=metrics,
        )

    current_return = current_price / previous_close - 1
    distance_to_limit = up_limit / current_price - 1
    gain_ok = current_return <= config.maximum_intraday_gain
    distance_ok = distance_to_limit >= config.minimum_distance_to_limit
    not_sealed = not stock.up_limit_sealed
    chase_traces = (
        _threshold_trace(
            "e8.chase.gain_cap",
            "当前涨幅不超过 7%",
            current_return,
            "<=",
            config.maximum_intraday_gain,
            source="stock_realtime_quote",
            missing_reason="当前价或昨收价不可用",
        ),
        _threshold_trace(
            "e8.chase.limit_distance",
            "距涨停至少 2%",
            distance_to_limit,
            ">=",
            config.minimum_distance_to_limit,
            source="stock_realtime_quote",
            missing_reason="当前价或涨停价不可用",
        ),
        _rule(
            "e8.chase.not_sealed",
            "尚未封涨停",
            RuleState.SATISFIED if not_sealed else RuleState.UNSATISFIED,
            "尚未封板" if not_sealed else "已经封板，不再追价",
            actual=stock.up_limit_sealed,
            operator="==",
            expected=False,
            source="stock_realtime_quote",
        ),
    )
    traces.extend(chase_traces)
    metrics["stock"].update(
        {
            "ticker": stock_ticker,
            "current_price": current_price,
            "previous_close": previous_close,
            "current_return": current_return,
            "vwap": vwap,
            "up_limit": up_limit,
            "distance_to_limit": distance_to_limit,
            "up_limit_sealed": stock.up_limit_sealed,
        }
    )
    if not (gain_ok and distance_ok and not_sealed):
        failed = []
        if not gain_ok:
            failed.append(f"当前涨幅 {current_return:.2%} 超过 7%")
        if not distance_ok:
            failed.append(f"距涨停仅 {distance_to_limit:.2%}，不足 2%")
        if not not_sealed:
            failed.append("已经封涨停")
        return _finish(
            state=E8DecisionState.MISSED,
            action=ActionType.NO_CHASE,
            setup=E8Setup.NONE,
            industry_path=industry_eval.path,
            traces=traces,
            evidence=("；".join(failed) + "，按纪律不追",),
            metrics=metrics,
        )

    if industry_eval.gate is not True:
        return _finish(
            state=E8DecisionState.CONFIRMING,
            action=ActionType.WAIT_CONFIRMATION,
            setup=E8Setup.NONE,
            industry_path=E8IndustryGatePath.NONE,
            traces=traces,
            evidence=("行业门尚未通过，继续观察但不准备建仓",),
            metrics=metrics,
        )

    index_return = _finite(stock.matching_index_return)
    if index_return is None:
        index_return = _finite(industry.matching_index_return)
    if index_return is None or industry_eval.sector_return is None:
        missing = []
        if index_return is None:
            missing.append("匹配指数收益")
        if industry_eval.sector_return is None:
            missing.append("行业中位数收益")
        traces.append(
            _rule(
                "e8.stock.relative_strength",
                "个股相对强度可计算",
                RuleState.BLOCKED,
                "、".join(missing) + "缺失",
                source="stock_realtime_quote+industry_constituent_returns",
            )
        )
        return _finish(
            state=E8DecisionState.DATA_BLOCKED,
            action=ActionType.PAUSE_NEW_ACTION,
            setup=E8Setup.NONE,
            industry_path=industry_eval.path,
            traces=traces,
            evidence=("、".join(missing) + "缺失，不能验证个股相对强度",),
            metrics=metrics,
        )

    excess_index = current_return - index_return
    excess_sector = current_return - industry_eval.sector_return
    above_vwap = current_price >= vwap
    vwap_premium = current_price / vwap - 1
    excess_index_ok = excess_index >= config.stock_excess_index_threshold
    excess_sector_ok = excess_sector >= config.stock_excess_sector_threshold
    vwap_premium_ok = vwap_premium <= config.maximum_vwap_premium
    stock_traces = (
        _threshold_trace(
            "e8.stock.excess_index",
            "个股跑赢匹配指数",
            excess_index,
            ">=",
            config.stock_excess_index_threshold,
            source="stock_realtime_quote+matching_index_quote",
            missing_reason="个股或指数收益不可用",
        ),
        _threshold_trace(
            "e8.stock.excess_sector",
            "个股跑赢行业中位数",
            excess_sector,
            ">=",
            config.stock_excess_sector_threshold,
            source="stock_realtime_quote+industry_constituent_returns",
            missing_reason="个股或行业收益不可用",
        ),
        _rule(
            "e8.stock.above_vwap",
            "当前价位于 VWAP 上方",
            RuleState.SATISFIED if above_vwap else RuleState.UNSATISFIED,
            f"当前价 {current_price:.4f} {'不低于' if above_vwap else '低于'} VWAP {vwap:.4f}",
            actual=current_price,
            operator=">=",
            expected=vwap,
            source="stock_realtime_quote",
        ),
        _threshold_trace(
            "e8.stock.vwap_premium",
            "相对 VWAP 溢价不过热",
            vwap_premium,
            "<=",
            config.maximum_vwap_premium,
            source="stock_realtime_quote",
            missing_reason="当前价或 VWAP 不可用",
        ),
    )
    traces.extend(stock_traces)
    metrics["stock"].update(
        {
            "matching_index_return": index_return,
            "sector_median_return": industry_eval.sector_return,
            "excess_index": excess_index,
            "excess_sector": excess_sector,
            "above_vwap": above_vwap,
            "vwap_premium": vwap_premium,
        }
    )
    if not (excess_index_ok and excess_sector_ok and above_vwap and vwap_premium_ok):
        failed = [trace.label for trace in stock_traces if trace.state is RuleState.UNSATISFIED]
        return _finish(
            state=E8DecisionState.CONFIRMING,
            action=ActionType.WAIT_CONFIRMATION,
            setup=E8Setup.NONE,
            industry_path=industry_eval.path,
            traces=traces,
            evidence=("个股确认未完成：" + "、".join(failed),),
            metrics=metrics,
        )

    setup, setup_traces, setup_metrics = _setup_evaluation(
        stock, bar_eval.bars, current_price, config
    )
    traces.extend(setup_traces)
    metrics["setups"] = setup_metrics
    if setup is E8Setup.NONE:
        return _finish(
            state=E8DecisionState.CONFIRMING,
            action=ActionType.WAIT_CONFIRMATION,
            setup=setup,
            industry_path=industry_eval.path,
            traces=traces,
            evidence=(
                f"行业门已通过（{industry_eval.path.value}），个股 RS/VWAP 合格，但资金形态尚未确认",
            ),
            metrics=metrics,
        )

    return _finish(
        state=E8DecisionState.ENTRY_READY,
        action=ActionType.PREPARE_ENTRY,
        setup=setup,
        industry_path=industry_eval.path,
        traces=traces,
        evidence=(
            f"行业门通过路径：{industry_eval.path.value}",
            f"个股同时跑赢指数和行业，且位于 VWAP 上方不过热",
            f"资金形态确认：{setup.value}",
            f"当前涨幅 {current_return:.2%}，距涨停 {distance_to_limit:.2%}，仍在可执行区间",
        ),
        metrics=metrics,
    )


__all__ = [
    "DEFAULT_E8_RULES",
    "E8Decision",
    "E8DecisionState",
    "E8FiveMinuteBar",
    "E8IndustryFacts",
    "E8IndustryGatePath",
    "E8RuleConfig",
    "E8Setup",
    "E8StockFacts",
    "IndustryPeerReturn",
    "evaluate_e8_buy_point",
]
