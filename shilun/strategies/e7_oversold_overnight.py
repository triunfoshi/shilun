"""E7 超跌反弹隔夜套利的持仓策略适配器。

入场判定和盘中退出继续由 FullScenarioPlaybook 负责；本适配器只冻结策略身份、
计划语义，并为日终持仓检查提供不会退化成 MA5 的兜底结论。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from shilun.strategies.contracts import StrategyContext


def _date_text(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class E7OversoldOvernightStrategy:
    strategy_id = "e7_oversold_overnight"
    version = "1.0.0"
    name = "E7 · 超跌反弹隔夜套利"
    description = "尾盘企稳后小仓隔夜；次一交易日 09:30–10:00 强制退出。"

    def evaluate_stock(
        self,
        bars: list[dict[str, Any]],
        *,
        context: StrategyContext | None = None,
    ) -> dict[str, Any]:
        ctx = context or StrategyContext()
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.version,
            "strategy_name": self.name,
            "as_of": ctx.analysis_date,
            "recommendation": "requires_intraday_e7_facts",
            "recommendation_label": "等待 E7 三日与分时事实",
            "trade_plan": {
                "position_factor": 0.25,
                "exit_window": "次一交易日 09:30–10:00",
                "target_type": "time_exit",
            },
            "signals": [],
            "evidence": [
                "E7 入场必须由 T-2 超跌、T-1 企稳和 14:00 后闭合 5 分钟 K 共同确认。",
                "日线不能替代分时事实生成 E7 买点。",
            ],
        }

    def bind_execution_plan(
        self,
        proposed_plan: dict[str, Any],
        *,
        actual_entry_price: float,
    ) -> dict[str, Any]:
        plan = dict(proposed_plan or {})
        planned = _number(plan.get("entry_price"), float(actual_entry_price))
        plan.pop("target_price", None)
        plan.update({
            "planned_entry_price": round(planned, 4),
            "entry_price": round(float(actual_entry_price), 4),
            "target_type": "time_exit",
            "target_source": "E7 次一交易日强制退出",
            "exit_window": "09:30–10:00",
            "exit_deadline": "次一交易日 10:00",
            "position_factor": 0.25,
            "strategy_id": self.strategy_id,
            "strategy_version": self.version,
            "bound_at": datetime.now().isoformat(timespec="seconds"),
        })
        return plan

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
        del breakout_event
        entry_date = _date_text(holding.get("entry_date"))
        check_date = _date_text(analysis_date)
        if not entry_date or not check_date:
            raise ValueError("E7 持仓必须包含入场日和检测日。")
        if check_date < entry_date:
            raise ValueError("E7 检测日不能早于入场日。")
        eligible = [
            dict(row) for row in bars
            if _date_text(row.get("date") or row.get("trade_date")) <= check_date
        ]
        eligible.sort(key=lambda row: _date_text(row.get("date") or row.get("trade_date")))
        latest = eligible[-1] if eligible else {}
        latest_date = _date_text(latest.get("date") or latest.get("trade_date"))
        if eligible and latest_date != check_date:
            raise ValueError(
                f"{holding.get('ticker')} 最新日线为 {latest_date}，不是检测日 {check_date}。"
            )
        current = _number(
            latest.get("close"),
            _number(holding.get("manual_current_price"), _number(holding.get("entry_price"))),
        )
        entry = _number(holding.get("cost_basis"), _number(holding.get("entry_price")))
        stop = _number(holding.get("stop_loss_1"))
        pnl = (current - entry) / entry if entry > 0 else 0.0
        next_day_or_later = check_date > entry_date
        hard_stop_hit = bool(stop > 0 and current <= stop)

        if next_day_or_later:
            state = "timed_exit"
            action = "sell_all"
            signals = ["e7_time_exit"]
            if hard_stop_hit:
                signals.insert(0, "e7_hard_stop")
            reason = (
                f"E7 已到次一交易日退出阶段；{'同时跌破专属止损，' if hard_stop_hit else ''}"
                "最迟 10:00 前清仓，不转为趋势持仓。"
            )
            phase = "next_day_exit"
        else:
            state = "overnight_locked"
            action = "hold"
            signals = []
            reason = "E7 建仓日受 T+1 锁定；保持隔夜仓，次一交易日 09:30 开始执行退出。"
            phase = "entry_day_t1_locked"

        return {
            "ticker": str(holding.get("ticker") or "").upper(),
            "name": holding.get("name"),
            "sector_name": holding.get("sector_name"),
            "date": check_date,
            "holding_state": state,
            "triggered_sell_signals": signals,
            "signal_details": [
                {
                    "code": code,
                    "label": "E7 专属硬止损" if code == "e7_hard_stop" else "E7 次日时间退出",
                    "observed": current,
                    "threshold": stop if code == "e7_hard_stop" else "次一交易日 10:00",
                    "meaning": "隔夜套利不允许迁移为 MA5 趋势持仓。",
                }
                for code in signals
            ],
            "recommended_action": action,
            "reason": reason,
            "current_price": round(current, 4),
            "entry_price": round(entry, 4),
            "entry_size": round(_number(holding.get("entry_size")), 4),
            "unrealized_pnl": round(pnl, 4),
            "market_permission": str((market_gate or {}).get("permission") or "unknown"),
            "sector_state": sector_state or "unknown",
            "metrics": {
                "execution_phase": phase,
                "exit_window": "09:30–10:00",
                "exit_deadline": "次一交易日 10:00",
                "hard_stop_hit": hard_stop_hit,
            },
            "risk_levels": {
                "target_price": None,
                "target_type": "time_exit",
                "stop_loss_1": holding.get("stop_loss_1"),
                "exit_deadline": "次一交易日 10:00",
            },
            "strategy_id": self.strategy_id,
            "strategy_version": self.version,
            "engine_version": "e7_overnight_holding_v1",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
