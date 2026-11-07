"""人工管理策略：只保存用户计划，不自动产生买卖点。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from shilun.strategies.contracts import StrategyContext


class ManualStrategy:
    strategy_id = "manual"
    version = "1.0.0"
    name = "人工计划"
    description = "不自动生成买卖点，只展示用户录入的计划和风险位。"

    def evaluate_stock(
        self,
        bars: list[dict[str, Any]],
        *,
        context: StrategyContext | None = None,
    ) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.version,
            "strategy_name": self.name,
            "as_of": (context or StrategyContext()).analysis_date,
            "recommendation": "manual_review",
            "recommendation_label": "人工判断",
            "trade_plan": None,
            "signals": [],
            "evidence": ["人工计划不自动生成买卖点。"],
        }

    def bind_execution_plan(
        self,
        proposed_plan: dict[str, Any],
        *,
        actual_entry_price: float,
    ) -> dict[str, Any]:
        return {
            **dict(proposed_plan or {}),
            "planned_entry_price": (proposed_plan or {}).get("entry_price"),
            "entry_price": round(float(actual_entry_price), 4),
            "strategy_id": self.strategy_id,
            "strategy_version": self.version,
            "bound_at": datetime.now().isoformat(timespec="seconds"),
        }

    def evaluate_holding(
        self,
        holding: dict[str, Any],
        bars: list[dict[str, Any]],
        *,
        analysis_date: str,
        market_gate: dict[str, Any] | None = None,
        sector_state: str = "",
        breakout_event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        latest = bars[-1] if bars else {}
        current = float(latest.get("close") or holding.get("entry_price") or 0.0)
        entry = float(holding.get("entry_price") or 0.0)
        pnl = (current - entry) / entry if entry else 0.0
        return {
            "ticker": str(holding.get("ticker") or "").upper(),
            "name": holding.get("name"),
            "sector_name": holding.get("sector_name"),
            "date": analysis_date,
            "holding_state": "normal_pullback",
            "triggered_sell_signals": [],
            "signal_details": [],
            "recommended_action": "hold",
            "reason": "人工计划未配置自动卖出规则，请按录入的风险位人工复核。",
            "current_price": round(current, 4),
            "entry_price": round(entry, 4),
            "entry_size": round(float(holding.get("entry_size") or 0.0), 4),
            "unrealized_pnl": round(pnl, 4),
            "market_permission": str((market_gate or {}).get("permission") or "unknown"),
            "sector_state": sector_state or "unknown",
            "metrics": {},
            "risk_levels": holding.get("current_plan") or {},
            "strategy_id": self.strategy_id,
            "strategy_version": self.version,
            "engine_version": "manual_holding_v1",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
