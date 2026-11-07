"""交易策略层的稳定契约。

事实层只描述行情、资金与事件；策略层把这些事实转换成信号、交易计划和持仓动作。
所有策略输出都必须携带 strategy_id/version，保证持仓和回测能够复现当时规则。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class StrategyError(ValueError):
    """策略不存在、输入不足或输出不合法。"""


@dataclass(frozen=True)
class StrategyContext:
    analysis_date: str = ""
    benchmark_ticker: str = "000001.SH"
    market_gate: dict[str, Any] = field(default_factory=dict)
    sector_name: str = ""
    sector_state: str = ""
    sector_score: float = 50.0
    benchmark_return_20d: float = 0.0


@runtime_checkable
class StrategyDefinition(Protocol):
    strategy_id: str
    version: str
    name: str
    description: str

    def evaluate_stock(
        self,
        bars: list[dict[str, Any]],
        *,
        context: StrategyContext | None = None,
    ) -> dict[str, Any]: ...

    def bind_execution_plan(
        self,
        proposed_plan: dict[str, Any],
        *,
        actual_entry_price: float,
    ) -> dict[str, Any]: ...

    def evaluate_holding(
        self,
        holding: dict[str, Any],
        bars: list[dict[str, Any]],
        *,
        analysis_date: str,
        market_gate: dict[str, Any] | None = None,
        sector_state: str = "",
        breakout_event: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


def strategy_public_info(strategy: StrategyDefinition) -> dict[str, str]:
    return {
        "strategy_id": strategy.strategy_id,
        "version": strategy.version,
        "name": strategy.name,
        "description": strategy.description,
    }
