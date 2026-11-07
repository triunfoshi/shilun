"""Trading Playbook 的历史 5 分钟逐根回放。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from shilun.trading.playbooks.ma5_trend import Ma5IntradayFacts, Ma5TrendPlaybook, Ma5WatchPlan
from shilun.trading.source_adapters import build_p0_source_snapshots
from shilun.trading.timeline import decision_payload


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


class ReplayError(ValueError):
    """历史回放输入不完整。"""


def _bar_time(bar: dict[str, Any]) -> datetime:
    value = bar.get("datetime")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ReplayError(f"无效分钟 K 时间 {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed.astimezone(MARKET_TIMEZONE)


def _valid_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for bar in bars or []:
        try:
            close = float(bar.get("close"))
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        result.append({**bar, "close": close, "_time": _bar_time(bar)})
    return sorted(result, key=lambda item: item["_time"])


def _latest_at_or_before(bars: list[dict[str, Any]], moment: datetime) -> dict[str, Any] | None:
    latest = None
    for bar in bars:
        if bar["_time"] > moment:
            break
        latest = bar
    return latest


def _market_state(bar: dict[str, Any] | None, first_open: float | None) -> str:
    if not bar or not first_open:
        return "unknown"
    change = float(bar["close"]) / first_open - 1.0
    if change >= 0.005:
        return "修复"
    if change <= -0.005:
        return "走弱"
    return "震荡"


def _signature(payload: dict[str, Any]) -> tuple[Any, ...]:
    action = payload.get("action") or {}
    scenario = payload.get("current_scenario") or {}
    freshness = payload.get("data_freshness") or {}
    return (
        action.get("action_type"),
        action.get("target_quantity"),
        scenario.get("scenario_id"),
        scenario.get("state"),
        freshness.get("state"),
    )


def replay_ma5_session(
    *,
    plan: Ma5WatchPlan,
    stock_bars: list[dict[str, Any]],
    sse_bars: list[dict[str, Any]],
    chinext_bars: list[dict[str, Any]],
    playbook: Ma5TrendPlaybook | None = None,
) -> dict[str, Any]:
    """逐根只使用当时已经闭合的 K 线，防止回放偷看未来。"""

    stock = _valid_bars(stock_bars)
    sse = _valid_bars(sse_bars)
    chinext = _valid_bars(chinext_bars)
    if not stock:
        raise ReplayError("缺少有效个股 5 分钟 K")
    if not sse or not chinext:
        raise ReplayError("缺少上证或创业板 5 分钟 K")
    engine = playbook or Ma5TrendPlaybook()
    sse_open = float(sse[0].get("open") or sse[0]["close"])
    chinext_open = float(chinext[0].get("open") or chinext[0]["close"])
    accumulated: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    previous_signature: tuple[Any, ...] | None = None

    for bar in stock:
        accumulated.append(bar)
        moment = bar["_time"]
        sse_bar = _latest_at_or_before(sse, moment)
        chinext_bar = _latest_at_or_before(chinext, moment)
        volumes = [float(item.get("volume") or 0) for item in accumulated]
        amounts = [float(item.get("amount") or 0) for item in accumulated]
        volume_sum = sum(volumes)
        amount_sum = sum(amounts)
        vwap = amount_sum / volume_sum if volume_sum > 0 and amount_sum > 0 else None
        baseline = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else 0
        volume_ratio = volumes[-1] / baseline if baseline > 0 else None
        historical_bars = [
            {key: value for key, value in item.items() if key != "_time"}
            for item in accumulated
        ]
        quote_at = moment.isoformat(timespec="seconds")
        sources = build_p0_source_snapshots(
            stock_quote={"ticker": plan.ticker, "current": bar["close"], "quote_at": quote_at},
            stock_5m_bars=historical_bars,
            sse_quote={"current": sse_bar["close"], "quote_at": sse_bar["_time"].isoformat(timespec="seconds")} if sse_bar else None,
            chinext_quote={"current": chinext_bar["close"], "quote_at": chinext_bar["_time"].isoformat(timespec="seconds")} if chinext_bar else None,
            sector_quote=None,
        )
        facts = Ma5IntradayFacts(
            computed_at=moment,
            closed_5m_prices=tuple(float(item["close"]) for item in accumulated),
            current_price=float(bar["close"]),
            vwap=vwap,
            volume_ratio=volume_ratio,
            sse_state=_market_state(sse_bar, sse_open),
            chinext_state=_market_state(chinext_bar, chinext_open),
            sector_state="unknown",
        )
        decision = decision_payload(engine.evaluate(plan, facts, sources))
        frame = {
            "computed_at": quote_at,
            "bar_index": len(accumulated),
            "close": round(float(bar["close"]), 4),
            "vwap": round(vwap, 4) if vwap is not None else None,
            "volume_ratio": round(volume_ratio, 4) if volume_ratio is not None else None,
            "sse_state": facts.sse_state,
            "chinext_state": facts.chinext_state,
            "decision": decision,
        }
        frames.append(frame)
        signature = _signature(decision)
        if signature != previous_signature:
            transitions.append(frame)
            previous_signature = signature

    return {
        "status": "ok",
        "ticker": plan.ticker,
        "bar_count": len(frames),
        "transition_count": len(transitions),
        "lookahead_protection": "每一步只使用当前及此前已闭合 5 分钟 K",
        "transitions": transitions,
        "frames": frames,
    }
