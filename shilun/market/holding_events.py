"""持仓每日状态判定与 `holding_events` 数据层。

本模块只生成可审计的卖出/持有建议，不自动修改 `holdings`。用户执行减仓或
清仓后，仍由 holdings REST API 推进 active -> reduced -> closed 状态机。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

from shilun.market.breakout_events import build_latest_events_lookup
from shilun.market.holdings import list_holdings

HOLDING_EVENTS_ENGINE_VERSION = "holding_events_ma5_v1"
COLLECTION_NAME = "holding_events"
HOLDING_STATES = {
    "strong_hold",
    "normal_pullback",
    "weaken_reduce",
    "invalid_exit",
    "overnight_locked",
    "timed_exit",
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _date_text(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _mean(values: list[float], window: int) -> float:
    sample = values[-window:]
    return sum(sample) / len(sample) if sample else 0.0


def _rolling_mean_at(values: list[float], window: int, end_offset: int = 0) -> float:
    end = len(values) - max(0, end_offset)
    start = max(0, end - window)
    sample = values[start:end]
    return sum(sample) / len(sample) if sample else 0.0


def _signal_detail(
    code: str,
    label: str,
    observed: float | str | None,
    threshold: float | str | None,
    meaning: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "observed": observed,
        "threshold": threshold,
        "meaning": meaning,
    }


def calculate_holding_features(
    holding: dict[str, Any],
    bars: Iterable[dict[str, Any]],
    *,
    analysis_date: str,
    breakout_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从截至 analysis_date 的日线计算九个卖出信号所需特征。"""
    rows = sorted(
        (
            dict(row)
            for row in bars
            if _date_text(row.get("date") or row.get("trade_date")) <= analysis_date
        ),
        key=lambda row: _date_text(row.get("date") or row.get("trade_date")),
    )
    if not rows:
        raise ValueError(f"{holding.get('ticker')} 在 {analysis_date} 前没有日线数据。")
    latest_date = _date_text(rows[-1].get("date") or rows[-1].get("trade_date"))
    if latest_date != analysis_date:
        raise ValueError(
            f"{holding.get('ticker')} 最新日线为 {latest_date}，不是检测日 {analysis_date}。"
        )

    closes = [_f(row.get("close")) for row in rows]
    volumes = [_f(row.get("volume") or row.get("vol")) for row in rows]
    highs = [_f(row.get("high") or row.get("close")) for row in rows]
    latest_close = closes[-1]
    previous_close = closes[-2] if len(closes) >= 2 else latest_close
    previous_2_close = closes[-3] if len(closes) >= 3 else previous_close
    ma5 = _mean(closes, 5)
    ma10 = _mean(closes, 10)
    ma20 = _mean(closes, 20)
    ma5_3d_ago = _rolling_mean_at(closes, 5, end_offset=3)
    ma5_slope_3d = ma5 - ma5_3d_ago if ma5_3d_ago else 0.0
    volume_ma20 = _mean(volumes, 20)
    volume_ratio_20 = volumes[-1] / volume_ma20 if volume_ma20 > 0 else 0.0

    entry_price = _f(holding.get("entry_price"))
    entry_date = _date_text(holding.get("entry_date"))
    since_entry_highs = [
        high
        for row, high in zip(rows, highs)
        if _date_text(row.get("date") or row.get("trade_date")) >= entry_date
    ]
    peak_price = max(since_entry_highs) if since_entry_highs else latest_close
    peak_unrealized_pnl = (peak_price - entry_price) / entry_price if entry_price > 0 else 0.0
    unrealized_pnl = (latest_close - entry_price) / entry_price if entry_price > 0 else 0.0

    breakout_level = _f(
        (breakout_event or {}).get("box_upper")
        or (breakout_event or {}).get("previous_high_20")
    )
    previous_high = _f((breakout_event or {}).get("previous_high_20"))
    signal_candle_open = _f(holding.get("signal_candle_open"))
    stop_loss_1 = _f(holding.get("stop_loss_1"))
    stop_loss_2 = _f(holding.get("stop_loss_2"))
    breakdown_level = _f(holding.get("breakdown_level"))
    target_price = _f(holding.get("target_price"))

    flags = {
        "close_below_ma5": bool(ma5 and latest_close < ma5),
        "ma5_lost_2d": bool(
            ma5
            and len(closes) >= 2
            and latest_close < ma5
            and previous_close < _rolling_mean_at(closes, 5, end_offset=1)
        ),
        "volume_break_ma5_flag": bool(ma5 and latest_close < ma5 and volume_ratio_20 > 1.3),
        "close_below_ma10": bool(ma10 and latest_close < ma10),
        "ma10_lost_2d": bool(
            ma10
            and len(closes) >= 2
            and latest_close < ma10
            and previous_close < _rolling_mean_at(closes, 10, end_offset=1)
        ),
        "volume_break_ma20_flag": bool(ma20 and latest_close < ma20 and volume_ratio_20 > 1.3),
        "breakout_failed_flag": bool(breakout_level and latest_close < breakout_level),
        "previous_high_lost_flag": bool(previous_high and latest_close < previous_high),
        "signal_candle_open_lost": bool(signal_candle_open and latest_close < signal_candle_open),
        # 曾有至少 8% 浮盈，随后收盘回到成本线上方 1% 以内，触发保本退出。
        "breakeven_stop_triggered": bool(
            entry_price
            and peak_unrealized_pnl >= 0.08
            and latest_close <= entry_price * 1.01
        ),
        "plan_stop_loss_1_triggered": bool(stop_loss_1 and latest_close < stop_loss_1),
        "plan_stop_loss_2_triggered": bool(stop_loss_2 and latest_close < stop_loss_2),
        "breakdown_level_lost": bool(breakdown_level and latest_close < breakdown_level),
        "target_reached": bool(target_price and latest_close >= target_price),
    }
    shrink_pullback_flag = bool(
        ma5
        and abs(latest_close / ma5 - 1.0) < 0.02
        and 0 < volume_ratio_20 <= 0.90
    )
    volume_price_score = 1 if latest_close >= previous_close else (-1 if volume_ratio_20 > 1.3 else 0)

    return {
        "analysis_date": analysis_date,
        "current_price": round(latest_close, 4),
        "previous_close": round(previous_close, 4),
        "previous_2_close": round(previous_2_close, 4),
        "ma5": round(ma5, 4),
        "ma10": round(ma10, 4),
        "ma20": round(ma20, 4),
        "ma5_slope_3d": round(ma5_slope_3d, 4),
        "volume": round(volumes[-1], 4),
        "volume_ma20": round(volume_ma20, 4),
        "volume_ratio_20": round(volume_ratio_20, 4),
        "shrink_pullback_flag": shrink_pullback_flag,
        "volume_price_score": volume_price_score,
        "unrealized_pnl": round(unrealized_pnl, 4),
        "peak_price_since_entry": round(peak_price, 4),
        "peak_unrealized_pnl": round(peak_unrealized_pnl, 4),
        "breakout_level": round(breakout_level, 4) if breakout_level else None,
        "previous_high": round(previous_high, 4) if previous_high else None,
        "signal_candle_open": round(signal_candle_open, 4) if signal_candle_open else None,
        "stop_loss_1": round(stop_loss_1, 4) if stop_loss_1 else None,
        "stop_loss_2": round(stop_loss_2, 4) if stop_loss_2 else None,
        "breakdown_level": round(breakdown_level, 4) if breakdown_level else None,
        "target_price": round(target_price, 4) if target_price else None,
        **flags,
    }


def classify_holding_state(
    holding: dict[str, Any],
    features: dict[str, Any],
    *,
    market_permission: str = "",
    sector_state: str = "",
) -> str:
    """按 PRD 8.4 的优先级输出四状态。"""
    fatal = bool(
        features.get("volume_break_ma5_flag")
        or (features.get("close_below_ma10") and features.get("ma10_lost_2d"))
        or features.get("volume_break_ma20_flag")
        or features.get("breakout_failed_flag")
        or features.get("signal_candle_open_lost")
        or features.get("breakeven_stop_triggered")
        or features.get("plan_stop_loss_2_triggered")
        or features.get("breakdown_level_lost")
        or market_permission == "empty"
    )
    if fatal:
        return "invalid_exit"
    if (
        features.get("close_below_ma5")
        or features.get("ma5_lost_2d")
        or features.get("previous_high_lost_flag")
        or features.get("plan_stop_loss_1_triggered")
        or sector_state == "retreat"
        or market_permission == "defense"
    ):
        return "weaken_reduce"
    current_price = _f(features.get("current_price"))
    ma5 = _f(features.get("ma5"))
    ma10 = _f(features.get("ma10"))
    ma20 = _f(features.get("ma20"))
    if (
        ma5
        and abs(current_price / ma5 - 1.0) < 0.02
        and features.get("shrink_pullback_flag")
        and (current_price > ma10 or current_price > ma20)
    ):
        return "normal_pullback"
    if (
        current_price > ma5
        and _f(features.get("ma5_slope_3d")) > 0
        and _f(features.get("volume_price_score")) >= 0
        and market_permission in {"attack", "hold"}
        and sector_state != "retreat"
    ):
        return "strong_hold"
    return "normal_pullback"


def _triggered_signal_details(features: dict[str, Any]) -> list[dict[str, Any]]:
    close = _f(features.get("current_price"))
    volume_ratio = _f(features.get("volume_ratio_20"))
    definitions = [
        ("close_below_ma5", "收盘跌破 MA5", close, features.get("ma5"), "短线趋势转弱，进入警戒。"),
        ("ma5_lost_2d", "连续两日失守 MA5", close, features.get("ma5"), "MA5 次日未收回，需要降低仓位。"),
        ("volume_break_ma5_flag", "放量跌破 MA5", volume_ratio, "> 1.30x", "价跌量增，短线结构发生破坏。"),
        ("close_below_ma10", "收盘跌破 MA10", close, features.get("ma10"), "中短期保护位失守。"),
        ("volume_break_ma20_flag", "放量跌破 MA20", volume_ratio, "> 1.30x", "主升结构破坏，优先退出。"),
        ("breakout_failed_flag", "跌回突破位下方", close, features.get("breakout_level"), "突破失败，原交易逻辑失效。"),
        ("previous_high_lost_flag", "失守前高", close, features.get("previous_high"), "突破后的承接不足。"),
        ("signal_candle_open_lost", "跌破信号 K 线开盘价", close, features.get("signal_candle_open"), "入场信号失效。"),
        ("breakeven_stop_triggered", "浮盈回撤到成本线", features.get("unrealized_pnl"), "<= +1%", "保护已经获得的浮盈，避免盈利单转亏。"),
        ("plan_stop_loss_1_triggered", "跌破交易计划第一止损", close, features.get("stop_loss_1"), "执行预先定义的风险控制线。"),
        ("plan_stop_loss_2_triggered", "跌破交易计划第二止损", close, features.get("stop_loss_2"), "保护位失守，交易计划进入退出状态。"),
        ("breakdown_level_lost", "跌破主升破坏位", close, features.get("breakdown_level"), "主升结构失效，优先退出。"),
    ]
    return [
        _signal_detail(code, label, observed, threshold, meaning)
        for code, label, observed, threshold, meaning in definitions
        if features.get(code)
    ]


def _action_for_state(state: str, features: dict[str, Any]) -> str:
    if state == "invalid_exit":
        return "sell_all"
    if state == "weaken_reduce":
        if features.get("ma5_lost_2d") or features.get("close_below_ma10"):
            return "reduce_50pct"
        return "reduce_20pct"
    return "hold"


def _reason_for_state(
    state: str,
    signal_details: list[dict[str, Any]],
    *,
    market_permission: str,
    sector_state: str,
) -> str:
    signal_labels = "、".join(str(item.get("label") or "") for item in signal_details if item.get("label"))
    if state == "invalid_exit":
        return f"触发致命退出条件：{signal_labels or '大盘空仓闸门'}。"
    if state == "weaken_reduce":
        context = []
        if market_permission == "defense":
            context.append("大盘防守")
        if sector_state == "retreat":
            context.append("板块退潮")
        return f"结构转弱：{'、'.join(filter(None, [signal_labels, *context])) or '风险条件升高'}。"
    if state == "strong_hold":
        return "收盘位于上行 MA5 上方，量价未破坏，且大盘闸门允许继续持有。"
    return "价格在 MA5 附近健康回踩或确认条件尚不充分，保持仓位并等待下一交易日。"


def _evaluate_ma5_holding_event(
    holding: dict[str, Any],
    bars: Iterable[dict[str, Any]],
    *,
    analysis_date: str,
    market_gate: dict[str, Any] | None = None,
    sector_state: str = "",
    breakout_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    features = calculate_holding_features(
        holding,
        bars,
        analysis_date=analysis_date,
        breakout_event=breakout_event,
    )
    market_permission = str((market_gate or {}).get("permission") or "")
    state = classify_holding_state(
        holding,
        features,
        market_permission=market_permission,
        sector_state=sector_state,
    )
    signal_details = _triggered_signal_details(features)
    return {
        "ticker": str(holding.get("ticker") or "").upper(),
        "name": holding.get("name"),
        "sector_name": holding.get("sector_name"),
        "date": analysis_date,
        "holding_state": state,
        "triggered_sell_signals": [item["code"] for item in signal_details],
        "signal_details": signal_details,
        "recommended_action": _action_for_state(state, features),
        "reason": _reason_for_state(
            state,
            signal_details,
            market_permission=market_permission,
            sector_state=sector_state,
        ),
        "current_price": features["current_price"],
        "entry_price": round(_f(holding.get("entry_price")), 4),
        "entry_size": round(_f(holding.get("entry_size")), 4),
        "unrealized_pnl": features["unrealized_pnl"],
        "market_permission": market_permission or "unknown",
        "sector_state": sector_state or "unknown",
        "metrics": features,
        "risk_levels": {
            "target_price": holding.get("target_price"),
            "stop_loss_1": holding.get("stop_loss_1"),
            "stop_loss_2": holding.get("stop_loss_2"),
            "breakdown_level": holding.get("breakdown_level"),
            "signal_candle_open": holding.get("signal_candle_open"),
        },
        "engine_version": HOLDING_EVENTS_ENGINE_VERSION,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def evaluate_holding_event(
    holding: dict[str, Any],
    bars: Iterable[dict[str, Any]],
    *,
    analysis_date: str,
    market_gate: dict[str, Any] | None = None,
    sector_state: str = "",
    breakout_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按持仓绑定的策略执行日检，而不是在持仓层硬编码 MA5。"""
    from shilun.strategies import (
        E7_OVERSOLD_OVERNIGHT_STRATEGY_ID,
        ExecutionStrategyError,
        get_execution_strategy,
        scenario_code,
    )

    stored_strategy_id = str(holding.get("strategy_id") or "ma5_v0_2").strip().lower()
    if scenario_code(holding.get("entry_signal")) == "E7":
        strategy_id = E7_OVERSOLD_OVERNIGHT_STRATEGY_ID
    else:
        strategy_id = (
            "ma5_v0_2"
            if stored_strategy_id in {"ma5_multi_scenario_execution", "ma5_execution_v0_3"}
            else stored_strategy_id
        )
    try:
        strategy = get_execution_strategy(strategy_id)
        event = strategy.evaluate_holding(
            holding,
            list(bars),
            analysis_date=analysis_date,
            market_gate=market_gate,
            sector_state=sector_state,
            breakout_event=breakout_event,
        )
    except ExecutionStrategyError:
        raise
    event.setdefault("strategy_id", strategy.strategy_id)
    event.setdefault("strategy_version", strategy.version)
    event.setdefault("strategy_name", strategy.name)
    if stored_strategy_id != strategy_id:
        event.setdefault("strategy_migrated_from", stored_strategy_id)
    return event


def upsert_holding_event(collection: Any, event: dict[str, Any]) -> str:
    ticker = str(event.get("ticker") or "").upper()
    date = _date_text(event.get("date"))
    if not ticker or not date:
        raise ValueError("holding event 必须包含 ticker 和 date。")
    key = {"ticker": ticker, "date": date}
    existing = collection.find_one(key, {"_id": 0, "ticker": 1})
    payload = {**event, **key}
    if not existing:
        payload["created_at"] = datetime.now().isoformat(timespec="seconds")
    collection.update_one(key, {"$set": payload}, upsert=True)
    return "updated" if existing else "inserted"


def list_holding_events(
    collection: Any,
    ticker: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"ticker": str(ticker or "").upper()}
    if start_date or end_date:
        date_query: dict[str, str] = {}
        if start_date:
            date_query["$gte"] = _date_text(start_date)
        if end_date:
            date_query["$lte"] = _date_text(end_date)
        query["date"] = date_query
    cursor = collection.find(query, {"_id": 0}).sort("date", -1)
    limit_method = getattr(cursor, "limit", None)
    if callable(limit_method):
        cursor = limit_method(max(1, min(int(limit), 365)))
    return list(cursor)


def build_sector_states_lookup(payload: dict[str, Any] | None) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for key in ("all_sectors", "trend_sectors", "top_sectors"):
        for item in (payload or {}).get(key) or []:
            name = str(item.get("sector_name") or "")
            state = str(item.get("sector_state") or "")
            if name and state:
                lookup[name] = state
    return lookup


def load_holding_check_context(
    store: Any,
    analysis_date: str,
    *,
    benchmark_ticker: str,
    trend_lookback_days: int = 60,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """读取 PART1 闸门与 PART2 板块状态；缓存缺失时安全降级为 unknown。"""
    from shilun.market.part1 import PART1_ENGINE_VERSION
    from shilun.market.sector import SECTOR_ENGINE_VERSION

    market_gate: dict[str, Any] | None = None
    try:
        cached = store.collection("market_part1_cache").find_one(
            {
                "analysis_date": analysis_date,
                "benchmark_ticker": benchmark_ticker.upper(),
                "engine_version": PART1_ENGINE_VERSION,
            },
            {"_id": 0, "payload": 1},
        )
        market_gate = ((cached or {}).get("payload") or {}).get("market_gate")
    except Exception:  # noqa: BLE001
        market_gate = None

    sector_states: dict[str, str] = {}
    try:
        cached = store.collection("sector_trends_cache").find_one(
            {
                "analysis_date": analysis_date,
                "benchmark_ticker": benchmark_ticker.upper(),
                "trend_lookback_days": int(trend_lookback_days),
                "engine_version": SECTOR_ENGINE_VERSION,
            },
            {"_id": 0, "payload": 1},
        )
        sector_states = build_sector_states_lookup((cached or {}).get("payload"))
    except Exception:  # noqa: BLE001
        sector_states = {}
    return market_gate, sector_states


def daily_check_holdings(
    store: Any,
    analysis_date: str,
    *,
    market_gate: dict[str, Any] | None = None,
    sector_states: dict[str, str] | None = None,
) -> dict[str, Any]:
    """扫描 active/reduced 持仓，按日线生成幂等事件。"""
    holdings = list_holdings(store.collection("holdings"))
    if not holdings:
        return {
            "analysis_date": analysis_date,
            "checked": 0,
            "inserted": 0,
            "updated": 0,
            "failed": 0,
            "state_counts": {},
            "events": [],
            "errors": [],
        }

    tickers = [str(item.get("ticker") or "").upper() for item in holdings]
    target_dt = datetime.strptime(analysis_date, "%Y-%m-%d")
    earliest_entry = min(
        (_date_text(item.get("entry_date")) for item in holdings if item.get("entry_date")),
        default=analysis_date,
    )
    history_start = min(
        earliest_entry,
        (target_dt - timedelta(days=120)).strftime("%Y-%m-%d"),
    )
    records = store.raw_market.find_daily_bars(
        start_date=history_start,
        end_date=analysis_date,
        tickers=tickers,
    )
    bars_lookup: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        ticker = str(row.get("ticker") or row.get("ts_code") or "").upper()
        if ticker:
            bars_lookup.setdefault(ticker, []).append(row)

    breakout_lookup = build_latest_events_lookup(
        store.collection("breakout_events"),
        tickers,
        on_or_before=analysis_date,
        lookback_days=60,
    )
    event_collection = store.collection(COLLECTION_NAME)
    result = {
        "analysis_date": analysis_date,
        "checked": 0,
        "inserted": 0,
        "updated": 0,
        "failed": 0,
        "state_counts": {},
        "events": [],
        "errors": [],
    }
    for holding in holdings:
        ticker = str(holding.get("ticker") or "").upper()
        try:
            event = evaluate_holding_event(
                holding,
                bars_lookup.get(ticker, []),
                analysis_date=analysis_date,
                market_gate=market_gate,
                sector_state=str((sector_states or {}).get(str(holding.get("sector_name") or "")) or ""),
                breakout_event=breakout_lookup.get(ticker),
            )
            action = upsert_holding_event(event_collection, event)
            result[action] += 1
            result["checked"] += 1
            state = event["holding_state"]
            result["state_counts"][state] = result["state_counts"].get(state, 0) + 1
            result["events"].append(event)
        except (ValueError, TypeError) as error:
            result["failed"] += 1
            result["errors"].append({"ticker": ticker, "error": str(error)})
    return result
