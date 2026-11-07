"""周一 P0 的集中规则配置。

这些参数是可解释的产品规则，不是训练结果。变更必须提升 version，并通过
历史分钟线回放后才能上线。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

from shilun.trading.contracts import ContractError


@dataclass(frozen=True)
class FreshnessPolicy:
    required_sources: tuple[str, ...] = (
        "stock_quote",
        "stock_5m",
        "index_sse_quote",
        "index_chinext_quote",
    )
    optional_sources: tuple[str, ...] = ("sector_quote",)
    defensive_required_sources: tuple[str, ...] = (
        "stock_quote",
        "stock_5m",
    )
    max_age_seconds: dict[str, int] = field(
        default_factory=lambda: {
            "stock_quote": 45,
            "stock_5m": 7 * 60,
            "index_sse_quote": 45,
            "index_chinext_quote": 45,
            "sector_quote": 2 * 60,
        }
    )
    future_skew_seconds: int = 5

    def __post_init__(self) -> None:
        missing = (
            set(self.required_sources)
            | set(self.optional_sources)
            | set(self.defensive_required_sources)
        ) - set(self.max_age_seconds)
        if missing:
            raise ContractError(f"缺少数据新鲜度阈值：{sorted(missing)}")
        if self.future_skew_seconds < 0:
            raise ContractError("future_skew_seconds 不能为负数")
        if any(value <= 0 for value in self.max_age_seconds.values()):
            raise ContractError("数据新鲜度阈值必须大于 0")


@dataclass(frozen=True)
class TradingRuleConfig:
    version: str = "trading-p0-v0.2.0"
    timeframe: str = "5m"
    soft_break_confirmation_bars: int = 2
    hard_stop_confirmation_bars: int = 1
    gap_up_pct: float = 0.01
    gap_down_pct: float = -0.01
    breakout_confirmation_bars: int = 1
    reclaim_confirmation_bars: int = 1
    elevated_volume_ratio: float = 1.2
    freshness: FreshnessPolicy = field(default_factory=FreshnessPolicy)

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ContractError("规则 version 不能为空")
        if self.timeframe != "5m":
            raise ContractError("周一 P0 只支持 5m 确认周期")
        bar_counts = (
            self.soft_break_confirmation_bars,
            self.hard_stop_confirmation_bars,
            self.breakout_confirmation_bars,
            self.reclaim_confirmation_bars,
        )
        if any(value <= 0 for value in bar_counts):
            raise ContractError("确认 K 线数量必须大于 0")
        if self.gap_down_pct >= 0 or self.gap_up_pct <= 0:
            raise ContractError("高低开阈值方向不合法")
        if self.elevated_volume_ratio <= 1:
            raise ContractError("放量阈值必须大于 1")


DEFAULT_TRADING_RULES = TradingRuleConfig()


@dataclass(frozen=True)
class ExecutionLevelConfig:
    """Job 12B 冻结的关键位、ATR 缓冲和追价上限参数。"""

    version: str = "execution-levels-v0.3.0"
    atr_period: int = 14
    swing_lookback: int = 20
    box_lookback: int = 20
    pressure_lookback: int = 60
    base_noise_atr: float = 0.15
    e7_stop_atr: float = 0.10
    support_observation_atr: float = 0.25
    min_reward_risk: float = 2.0
    slippage_atr_pct_factor: float = 0.25
    min_slippage_pct: float = 0.003
    max_slippage_pct: float = 0.008
    e3_lower_shadow_ratio: float = 0.35

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ContractError("关键位规则 version 不能为空")
        windows = (
            self.atr_period,
            self.swing_lookback,
            self.box_lookback,
            self.pressure_lookback,
        )
        if any(value <= 0 for value in windows):
            raise ContractError("关键位回看窗口必须大于 0")
        atr_factors = (
            self.base_noise_atr,
            self.e7_stop_atr,
            self.support_observation_atr,
            self.slippage_atr_pct_factor,
        )
        if any(value <= 0 for value in atr_factors):
            raise ContractError("ATR 缓冲系数必须大于 0")
        if not self.e7_stop_atr <= self.base_noise_atr <= self.support_observation_atr:
            raise ContractError("ATR 缓冲应满足 E7 止损 <= 基础噪声 <= 支撑观察区")
        if self.pressure_lookback < self.box_lookback:
            raise ContractError("长期压力回看窗口不能短于箱体窗口")
        if self.min_reward_risk <= 0:
            raise ContractError("最低盈亏比必须大于 0")
        if not 0 < self.min_slippage_pct <= self.max_slippage_pct:
            raise ContractError("滑点比例上下界不合法")
        if not 0 < self.e3_lower_shadow_ratio < 1:
            raise ContractError("E3 下影比例阈值必须在 0 和 1 之间")


DEFAULT_EXECUTION_LEVEL_RULES = ExecutionLevelConfig()


@dataclass(frozen=True)
class ExecutionScenarioConfig:
    """Job 12D 的全场景确认、时间窗口和仲裁参数。"""

    version: str = "execution-scenarios-v0.3.0"
    preferred_sell_start: time = time(9, 30)
    preferred_sell_end: time = time(10, 30)
    target_trim_cutoff: time = time(14, 45)
    soft_risk_cutoff: time = time(14, 55)
    new_entry_start: time = time(14, 0)
    new_entry_cutoff: time = time(15, 0)
    e7_earliest_confirmation: time = time(14, 15)
    e7_exit_start: time = time(9, 30)
    e7_high_open_first_exit_end: time = time(9, 35)
    e7_flat_low_open_exit_end: time = time(9, 40)
    e7_exit_cutoff: time = time(10, 0)
    quote_max_age_seconds: int = 45
    gap_up_pct: float = 0.01
    gap_down_pct: float = -0.01
    breakout_volume_ratio: float = 1.20
    upper_shadow_failure_ratio: float = 0.40
    e7_market_stock_oversold_min: float = 1.0
    e7_sector_oversold_min: float = 0.8
    e7_sector_new_low_ratio_max: float = 0.30
    e7_sector_recent_decline_max: float = 0.05
    e7_low_floor_atr: float = 0.25
    e7_low_ceiling_atr: float = 0.50

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ContractError("全场景规则 version 不能为空")
        if not (
            self.preferred_sell_start < self.preferred_sell_end
            < self.target_trim_cutoff < self.soft_risk_cutoff
            < self.new_entry_cutoff
        ):
            raise ContractError("普通买卖时间窗口顺序不合法")
        if not (
            self.new_entry_start < self.e7_earliest_confirmation
            < self.new_entry_cutoff
        ):
            raise ContractError("E7 尾盘确认时间窗口不合法")
        if not (
            self.e7_exit_start < self.e7_high_open_first_exit_end
            < self.e7_flat_low_open_exit_end < self.e7_exit_cutoff
        ):
            raise ContractError("E7 次日退出时间窗口不合法")
        if self.quote_max_age_seconds <= 0:
            raise ContractError("报价新鲜度阈值必须大于 0")
        if self.gap_down_pct >= 0 or self.gap_up_pct <= 0:
            raise ContractError("高低开阈值方向不合法")
        if self.breakout_volume_ratio <= 1:
            raise ContractError("突破量比阈值必须大于 1")
        ratios = (
            self.upper_shadow_failure_ratio,
            self.e7_sector_new_low_ratio_max,
            self.e7_sector_recent_decline_max,
            self.e7_low_floor_atr,
            self.e7_low_ceiling_atr,
        )
        if any(value <= 0 for value in ratios):
            raise ContractError("场景比例阈值必须大于 0")
        if self.e7_low_floor_atr > self.e7_low_ceiling_atr:
            raise ContractError("E7 低位区间上下界不合法")


DEFAULT_EXECUTION_SCENARIO_RULES = ExecutionScenarioConfig()


@dataclass(frozen=True)
class ExecutionSizingConfig:
    """Job 12E 的仓位系数、风险严重度和现金预留参数。"""

    version: str = "execution-sizing-v0.3.0"
    attack_factor: float = 1.0
    hold_factor: float = 0.5
    rebound_permission_factor: float = 1.0
    ordinary_scenario_factor: float = 1.0
    hold_e3_e4_factor: float = 0.5
    e7_factor: float = 0.25
    target_unconfirmed_severity: float = 0.25
    pressure_failure_severity: float = 0.35
    soft_break_severity: float = 0.50
    compounded_soft_break_severity: float = 0.70
    forced_exit_severity: float = 1.0
    high_reward_risk_threshold: float = 2.0
    medium_reward_risk_threshold: float = 1.0
    high_reward_risk_discount: float = 0.75
    low_reward_risk_floor: float = 0.70

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ContractError("仓位规则 version 不能为空")
        factors = (
            self.attack_factor,
            self.hold_factor,
            self.rebound_permission_factor,
            self.ordinary_scenario_factor,
            self.hold_e3_e4_factor,
            self.e7_factor,
            self.target_unconfirmed_severity,
            self.pressure_failure_severity,
            self.soft_break_severity,
            self.compounded_soft_break_severity,
            self.forced_exit_severity,
            self.high_reward_risk_discount,
            self.low_reward_risk_floor,
        )
        if any(not 0 < value <= 1 for value in factors):
            raise ContractError("仓位系数和风险严重度必须在 0 与 1 之间")
        if not (
            self.target_unconfirmed_severity
            <= self.pressure_failure_severity
            <= self.soft_break_severity
            <= self.compounded_soft_break_severity
            <= self.forced_exit_severity
        ):
            raise ContractError("风险严重度顺序不合法")
        if not 0 < self.medium_reward_risk_threshold < self.high_reward_risk_threshold:
            raise ContractError("剩余盈亏比阈值顺序不合法")
        if self.e7_factor != 0.25:
            raise ContractError("Job 12E 第一版要求 E7 固定为正常计划的 25%")


DEFAULT_EXECUTION_SIZING_RULES = ExecutionSizingConfig()
