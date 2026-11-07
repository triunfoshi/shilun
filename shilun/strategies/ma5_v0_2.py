"""MA5 v0.2 策略适配器。

第一阶段复用已验证的 ma5_features 和 holding_events 计算，外部业务只依赖本策略
接口。后续移动内部实现时，候选池、单票页、持仓和回测的调用契约无需变化。
"""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from shilun.market.buy_point_patterns import detect_buy_point_pattern
from shilun.market.ma5_features import (
    build_ma_features,
    build_trade_plan,
    compute_final_trade_score,
    compute_risk_adjustment,
    compute_stock_quality_score,
    compute_trade_timing_score,
)
from shilun.strategies.contracts import StrategyContext


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mean(values: list[float], window: int) -> float:
    sample = values[-window:]
    return sum(sample) / len(sample) if sample else 0.0


def prepare_ma5_bars(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    closes: list[float] = []
    volumes: list[float] = []
    amounts: list[float] = []
    true_ranges: list[float] = []
    returns: list[float] = []
    for raw in sorted(rows, key=lambda item: str(item.get("date") or item.get("trade_date") or "")):
        row = dict(raw)
        close = _f(row.get("close"))
        open_price = _f(row.get("open"), close)
        high = _f(row.get("high"), close)
        low = _f(row.get("low"), close)
        volume = _f(row.get("volume") or row.get("vol"))
        amount = _f(row.get("amount"))
        previous_close = closes[-1] if closes else close
        pct_chg = close / previous_close - 1.0 if previous_close else 0.0
        closes.append(close)
        volumes.append(volume)
        amounts.append(amount)
        returns.append(pct_chg)
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        range_value = max(high - low, 1e-9)
        amount_ma60 = _mean(amounts, 60)
        volume_ma20 = _mean(volumes, 20)
        row.update({
            "date": str(row.get("date") or row.get("trade_date") or "")[:10],
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": amount,
            "pct_chg": pct_chg,
            "ma5": _mean(closes, 5),
            "ma7": _mean(closes, 7),
            "ma8": _mean(closes, 8),
            "ma10": _mean(closes, 10),
            "ma20": _mean(closes, 20),
            "volume_ma5": _mean(volumes, 5),
            "volume_ratio_20": volume / volume_ma20 if volume_ma20 else 1.0,
            "amount_ratio_60": amount / amount_ma60 if amount_ma60 else 1.0,
            "close_position": (close - low) / range_value,
            "real_body_ratio": abs(close - open_price) / range_value,
            "upper_shadow_ratio": (high - max(open_price, close)) / range_value,
            "lower_shadow_ratio": (min(open_price, close) - low) / range_value,
            "atr_pct_14": _mean(true_ranges, 14) / close if close else 0.02,
            "median_abs_return_20d": median([abs(value) for value in returns[-20:]]) if returns else 0.02,
        })
        prepared.append(row)
    return prepared


class Ma5V02Strategy:
    strategy_id = "ma5_v0_2"
    version = "0.2.0"
    name = "MA5 趋势战法"
    description = "主升趋势、MA5 回踩/突破、量价确认及分层止损。"

    def evaluate_stock(
        self,
        bars: list[dict[str, Any]],
        *,
        context: StrategyContext | None = None,
    ) -> dict[str, Any]:
        ctx = context or StrategyContext()
        prepared = prepare_ma5_bars(bars)
        features = build_ma_features(prepared)
        if features.get("insufficient_bars"):
            return {
                "strategy_id": self.strategy_id,
                "strategy_version": self.version,
                "strategy_name": self.name,
                "as_of": ctx.analysis_date,
                "recommendation": "insufficient_data",
                "recommendation_label": "数据不足",
                "trade_plan": None,
                "signals": [],
                "evidence": ["至少需要 3 根日线，建议使用 60 根以上完成策略评估。"],
            }
        pattern = detect_buy_point_pattern(features)
        timing = compute_trade_timing_score(features)
        quality = compute_stock_quality_score(
            features,
            sector_score=ctx.sector_score,
            benchmark_return_20d=ctx.benchmark_return_20d,
        )
        risk = compute_risk_adjustment(
            features,
            market_gate=ctx.market_gate,
            sector_state=ctx.sector_state,
        )
        market_multiplier = _f(ctx.market_gate.get("market_multiplier"), 1.0)
        final_score = compute_final_trade_score(
            timing["score"],
            market_multiplier=market_multiplier,
            risk_adjustment=risk["score"],
        )
        plan = build_trade_plan(features)
        rr = _f(plan.get("reward_risk_ratio"))
        actionable = timing.get("buy_point_type") != "watch" and rr >= 2.0 and final_score >= 60
        recommendation = "candidate" if actionable else "watch"
        evidence = [
            f"买点识别：{timing.get('buy_point_label') or '观察'}，时机分 {timing.get('score')}。",
            f"股票质量分 {quality.get('score')}，策略最终分 {final_score}。",
            f"计划盈亏比 {rr:.2f}:1。" if rr else "计划盈亏比暂不可计算。",
        ]
        if risk.get("factors"):
            evidence.append("风险调整：" + "、".join(str(item.get("label")) for item in risk["factors"]))
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.version,
            "strategy_name": self.name,
            "as_of": ctx.analysis_date or (prepared[-1].get("date") if prepared else ""),
            "recommendation": recommendation,
            "recommendation_label": "策略候选" if actionable else "仅观察",
            "signal": {
                "code": timing.get("buy_point_type"),
                "label": timing.get("buy_point_label"),
                "pattern": pattern.get("pattern"),
                "pattern_label": pattern.get("label"),
            },
            "scores": {
                "stock_quality": quality.get("score"),
                "trade_timing": timing.get("score"),
                "risk_adjustment": risk.get("score"),
                "final_trade_score": final_score,
            },
            "trade_plan": plan,
            "risk_factors": risk.get("factors") or [],
            "evidence": evidence,
            "feature_snapshot": features,
        }

    def bind_execution_plan(
        self,
        proposed_plan: dict[str, Any],
        *,
        actual_entry_price: float,
    ) -> dict[str, Any]:
        plan = dict(proposed_plan or {})
        actual = float(actual_entry_price)
        planned = _f(plan.get("entry_price")) or actual
        target = _f(plan.get("target_price"))
        stop = _f(plan.get("stop_loss_1"))
        rr = (target - actual) / (actual - stop) if target > actual > stop else 0.0
        plan.update({
            "planned_entry_price": round(planned, 4),
            "entry_price": round(actual, 4),
            "reward_risk_ratio": round(rr, 2) if rr else None,
            "entry_slippage_pct": round(actual / planned - 1.0, 4) if planned else None,
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
        from shilun.market.holding_events import _evaluate_ma5_holding_event

        event = _evaluate_ma5_holding_event(
            holding,
            bars,
            analysis_date=analysis_date,
            market_gate=market_gate,
            sector_state=sector_state,
            breakout_event=breakout_event,
        )
        event["strategy_id"] = self.strategy_id
        event["strategy_version"] = self.version
        return event
