"""策略注册表。通过稳定 ID 获取策略，业务层不再直接 import MA5 实现。"""

from __future__ import annotations

from typing import Any

from shilun.strategies.contracts import StrategyDefinition, StrategyError, strategy_public_info

_REGISTRY: dict[str, StrategyDefinition] = {}
_DEFAULTS_LOADED = False

E7_OVERSOLD_OVERNIGHT_STRATEGY_ID = "e7_oversold_overnight"
E7_OVERSOLD_OVERNIGHT_STRATEGY_VERSION = "1.0.0"


def scenario_code(value: str | None) -> str:
    """把持仓/委托里的场景写法归一成稳定的 E1–E7 代码。"""

    normalized = str(value or "").strip().upper()
    if normalized.startswith("E7") or normalized == "OVERSOLD_REBOUND_OVERNIGHT":
        return "E7"
    if len(normalized) >= 2 and normalized[0] == "E" and normalized[1].isdigit():
        return normalized[:2]
    return normalized


def resolve_strategy_binding(
    scenario_id: str | None,
    requested_strategy_id: str | None = None,
    requested_strategy_version: str | None = None,
    *,
    fallback_strategy_id: str = "ma5_v0_2",
) -> tuple[str, str]:
    """服务端唯一的“执行场景 → 持仓策略”绑定入口。

    E7 必须绑定隔夜套利策略，不能被前端默认值或旧 MA5 字段覆盖。其他场景
    保留显式策略；未指定时才回退到 MA5。
    """

    code = scenario_code(scenario_id)
    required = E7_OVERSOLD_OVERNIGHT_STRATEGY_ID if code == "E7" else None
    requested = str(requested_strategy_id or "").strip().lower() or None
    if required and requested and requested != required:
        raise StrategyError(
            f"场景 E7 必须绑定 {required}，不能绑定 {requested}。"
        )
    strategy = get_strategy(required or requested or fallback_strategy_id)
    requested_version = str(requested_strategy_version or "").strip()
    if requested_version and requested and requested_version != strategy.version:
        raise StrategyError(
            f"策略 {strategy.strategy_id} 当前版本为 {strategy.version}，"
            f"不能绑定 {requested_version}。"
        )
    return strategy.strategy_id, strategy.version


def register_strategy(strategy: StrategyDefinition) -> None:
    strategy_id = str(strategy.strategy_id or "").strip().lower()
    if not strategy_id:
        raise StrategyError("strategy_id 不能为空。")
    _REGISTRY[strategy_id] = strategy


def _load_defaults() -> None:
    global _DEFAULTS_LOADED
    if _DEFAULTS_LOADED:
        return
    from shilun.strategies.e7_oversold_overnight import E7OversoldOvernightStrategy
    from shilun.strategies.ma5_v0_2 import Ma5V02Strategy
    from shilun.strategies.manual import ManualStrategy

    register_strategy(ManualStrategy())
    register_strategy(Ma5V02Strategy())
    register_strategy(E7OversoldOvernightStrategy())
    _DEFAULTS_LOADED = True


def get_strategy(strategy_id: str | None) -> StrategyDefinition:
    _load_defaults()
    normalized = str(strategy_id or "manual").strip().lower()
    strategy = _REGISTRY.get(normalized)
    if strategy is None:
        raise StrategyError(f"未知策略 {strategy_id!r}，可用策略：{sorted(_REGISTRY)}")
    return strategy


def list_strategies() -> list[dict[str, Any]]:
    _load_defaults()
    return [strategy_public_info(_REGISTRY[key]) for key in sorted(_REGISTRY)]
