"""Pure rules for daily review, label baselines, and the three frozen tactics.

The module deliberately avoids a black-box total score.  Every output carries the
facts and conditions that caused it so the review page can show the rule trace.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from math import isfinite
from statistics import mean, median
from typing import Any, Iterable, Sequence

from shilun.review.contracts import (
    BoardNature,
    CYCLE_LABELS,
    EXPECTATION_LABELS,
    ExpectationState,
    MarketPosture,
    POSTURE_LABELS,
    ROLE_LABELS,
    SectorCycle,
    StockRole,
)


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("quantile requires at least one value")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * max(0.0, min(1.0, probability))
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def build_expectation_band(values: Iterable[Any], *, min_samples: int = 5) -> dict[str, Any]:
    """Turn one label's historical premium into low/base/high expectations.

    The three values are descriptive quartiles rather than predictions.  This is
    the core contract discussed with the user: split the last N sessions by label,
    then compare today's observation with the label's own history.
    """

    samples = [number for value in values if (number := _number(value)) is not None]
    if len(samples) < max(1, int(min_samples)):
        return {
            "status": ExpectationState.INSUFFICIENT.value,
            "status_label": EXPECTATION_LABELS[ExpectationState.INSUFFICIENT],
            "sample_size": len(samples),
            "minimum_samples": max(1, int(min_samples)),
            "low": None,
            "base": None,
            "high": None,
            "mean": round(mean(samples), 6) if samples else None,
            "positive_rate": round(sum(value > 0 for value in samples) / len(samples), 4) if samples else None,
        }
    return {
        "status": "ready",
        "status_label": "可用",
        "sample_size": len(samples),
        "minimum_samples": max(1, int(min_samples)),
        "low": round(_quantile(samples, 0.25), 6),
        "base": round(median(samples), 6),
        "high": round(_quantile(samples, 0.75), 6),
        "mean": round(mean(samples), 6),
        "positive_rate": round(sum(value > 0 for value in samples) / len(samples), 4),
    }


def classify_expectation(
    observed: Any,
    band: dict[str, Any] | None,
    *,
    consensus_confirmed: bool = False,
    logic_reversal: bool = False,
) -> dict[str, Any]:
    """Classify observed behaviour without confusing strength with consensus.

    ``above_expectation`` means the behaviour is stronger than its historical high
    band and the arbitrage can continue.  ``consensus`` is a separate terminal
    condition: the view has diffused into participation and price, so the mismatch
    has closed even if the tape remains strong.
    """

    value = _number(observed)
    if logic_reversal:
        state = ExpectationState.LOGIC_REVERSAL
        action = "逻辑前提失效，退出并记录失败原因"
    elif consensus_confirmed:
        state = ExpectationState.CONSENSUS
        action = "价值错位已被市场定价，兑现套利"
    elif value is None or not band or band.get("status") != "ready":
        state = ExpectationState.INSUFFICIENT
        action = "只记录事实，不生成主动动作"
    elif value < float(band["low"]):
        state = ExpectationState.BELOW
        action = "低于预期，离场或取消计划"
    elif value > float(band["high"]):
        state = ExpectationState.ABOVE
        action = "超预期继续持有，等待预期一致或逻辑反转"
    else:
        state = ExpectationState.IN_LINE
        action = "符合预期，按原计划管理，不因正常波动改逻辑"
    return {
        "state": state.value,
        "label": EXPECTATION_LABELS[state],
        "observed": value,
        "low": band.get("low") if band else None,
        "base": band.get("base") if band else None,
        "high": band.get("high") if band else None,
        "action": action,
    }


def classify_market_posture(
    indices: Sequence[dict[str, Any]],
    *,
    market_amount_yi: Any,
    previous_amount_yi: Any = None,
    amount_ma20_yi: Any = None,
    up_ratio: Any = None,
) -> dict[str, Any]:
    """Apply the compact doctrine: rising→trend, weak→crowding, volume→cap size."""

    amount = _number(market_amount_yi, 0.0) or 0.0
    previous = _number(previous_amount_yi)
    ma20 = _number(amount_ma20_yi)
    breadth = _number(up_ratio)
    positive = sum((_number(item.get("pct_chg"), 0.0) or 0.0) > 0 for item in indices)
    above_ma5 = sum(bool(item.get("above_ma5")) for item in indices)
    solid_red = sum(bool(item.get("solid_red_k")) for item in indices)

    weak_breadth = breadth is not None and breadth < 0.42
    strong_breadth = breadth is not None and breadth >= 0.58
    if positive >= 3 and above_ma5 >= 2 and not weak_breadth:
        posture = MarketPosture.TREND_ATTACK
        doctrine = "上升走趋势"
        position = "进攻仓位"
    elif positive <= 1 or above_ma5 <= 1 or weak_breadth:
        posture = MarketPosture.EMOTION_DEFENCE
        doctrine = "弱势走抱团"
        position = "防守仓位"
    else:
        posture = MarketPosture.ROTATION
        doctrine = "轮动中只做最强辨识度"
        position = "试错仓位"

    if amount >= 23_000:
        cap_scope = "可套利300亿以上容量大票"
        cap_code = "large_cap_open"
    elif amount >= 21_000:
        cap_scope = "健康量能，趋势核心与容量中军均可观察"
        cap_code = "healthy"
    else:
        cap_scope = "量能不足，回避300亿以上大票，优先情绪前排"
        cap_code = "small_cap_priority"

    change = amount - previous if previous is not None else None
    expansion_1000 = change is not None and change >= 1_000
    versus_ma20 = (amount / ma20 - 1.0) if ma20 and ma20 > 0 else None
    market_red_confirmed = solid_red >= 2 and positive >= 2
    reasons = [
        f"四大宽基 {positive} 只上涨、{above_ma5} 只站上 MA5",
        f"全市场成交额 {amount:.0f} 亿元",
    ]
    if breadth is not None:
        reasons.append(f"上涨家数占比 {breadth:.1%}")
    if expansion_1000:
        reasons.append("较昨日增加至少1000亿元，确认放量标记成立")
    if strong_breadth:
        reasons.append("市场广度支持进攻")
    return {
        "code": posture.value,
        "label": POSTURE_LABELS[posture],
        "doctrine": doctrine,
        "position": position,
        "cap_code": cap_code,
        "cap_scope": cap_scope,
        "market_amount_yi": round(amount, 2),
        "previous_amount_yi": round(previous, 2) if previous is not None else None,
        "amount_change_yi": round(change, 2) if change is not None else None,
        "amount_ma20_yi": round(ma20, 2) if ma20 is not None else None,
        "amount_vs_ma20": round(versus_ma20, 4) if versus_ma20 is not None else None,
        "expanded_by_1000": expansion_1000,
        "market_red_k_confirmed": market_red_confirmed,
        "reasons": reasons,
    }


def classify_board_type(
    limit_row: dict[str, Any],
    daily_bar: dict[str, Any] | None,
    *,
    amount_ratio_5: Any = None,
) -> str:
    """Classify a limit-up board into the user's four requested board labels."""

    bar = daily_bar or {}
    prices = [_number(bar.get(key)) for key in ("open", "high", "low", "close")]
    prices = [value for value in prices if value is not None]
    flat_bar = bool(prices) and max(prices) - min(prices) <= max(prices) * 0.001
    broken = int(_number(limit_row.get("broken_count"), 0.0) or 0)
    first_seal = str(limit_row.get("first_seal_time") or "").replace(":", "")
    last_seal = str(limit_row.get("last_seal_time") or "").replace(":", "")
    if flat_bar and broken == 0 and (not first_seal or first_seal <= "093000"):
        return "one_word"
    if broken >= 2 or (last_seal and last_seal >= "143000"):
        return "broken_messy"
    ratio = _number(amount_ratio_5)
    if ratio is not None and ratio >= 1.8:
        return "explosive_volume"
    if ratio is not None and ratio <= 0.8:
        return "shrinking_volume"
    return "normal_board"


def build_ladder(
    limit_rows: Iterable[dict[str, Any]],
    *,
    sector_by_ticker: dict[str, str] | None = None,
) -> dict[str, Any]:
    rows = [dict(row) for row in limit_rows]
    sector_lookup = sector_by_ticker or {}
    levels: dict[int, list[dict[str, Any]]] = defaultdict(list)
    sectors: dict[str, Counter[int]] = defaultdict(Counter)
    for row in rows:
        level = max(1, int(_number(row.get("consecutive_count"), 1.0) or 1))
        ticker = str(row.get("ticker") or row.get("ts_code") or "").upper()
        sector = str(row.get("sector_name") or row.get("industry") or sector_lookup.get(ticker) or "未分类")
        item = {**row, "ticker": ticker, "sector_name": sector, "board_level": level}
        levels[level].append(item)
        sectors[sector][level] += 1
    highest = max(levels, default=0)
    level_rows = [
        {
            "level": level,
            "count": len(levels[level]),
            "stocks": sorted(levels[level], key=lambda item: str(item.get("ticker") or "")),
        }
        for level in sorted(levels, reverse=True)
    ]
    sector_rows = []
    for sector, counts in sectors.items():
        sector_high = max(counts, default=0)
        present = set(counts)
        complete = sector_high >= 2 and all(level in present for level in range(1, sector_high + 1))
        sector_rows.append(
            {
                "sector_name": sector,
                "highest_board": sector_high,
                "low_board_count": counts[1] + counts[2],
                "total_count": sum(counts.values()),
                "complete": complete,
                "levels": {str(level): counts[level] for level in sorted(counts, reverse=True)},
            }
        )
    sector_rows.sort(
        key=lambda item: (bool(item["complete"]), int(item["highest_board"]), int(item["total_count"])),
        reverse=True,
    )
    highest_sectors = sorted({item["sector_name"] for item in levels.get(highest, [])}) if highest else []
    low_board_sectors = sorted(sector_rows, key=lambda item: int(item["low_board_count"]), reverse=True)[:5]
    return {
        "highest_board": highest,
        "highest_board_sectors": highest_sectors,
        "levels": level_rows,
        "sector_ladders": sector_rows,
        "low_board_concentration": low_board_sectors,
        "complete_sectors": [item["sector_name"] for item in sector_rows if item["complete"]],
    }


def classify_sector_cycle(
    *,
    first_board_count: int,
    max_board: int,
    board_count: int,
    broken_count: int,
    up_ratio: Any,
    net_flow: Any,
    prior_first_board_count: int | None = None,
) -> dict[str, Any]:
    breadth = _number(up_ratio)
    flow = _number(net_flow)
    prior_first = prior_first_board_count if prior_first_board_count is not None else 0
    if first_board_count >= 3 and (flow is None or flow > 0) and max_board <= 2:
        cycle = SectorCycle.STARTUP
        reason = "低位首板集中且资金未流出，处于启动/发酵"
    elif max_board >= 2 and board_count >= 2 and (breadth is None or breadth >= 0.45):
        cycle = SectorCycle.DIVERGENCE
        reason = "连板高度出现，资金开始向前排集中"
    elif max_board >= 2 and (broken_count > 0 or first_board_count < prior_first):
        cycle = SectorCycle.DISTRIBUTION_OR_RETURN
        reason = "高标仍在但低位供给或封板质量转弱，观察散货还是龙头带动回流"
    elif board_count == 0 and (flow is not None and flow < 0) and (breadth is not None and breadth < 0.4):
        cycle = SectorCycle.DECAY
        reason = "涨停供给消失、资金流出且广度转弱，进入题材衰减"
    else:
        cycle = SectorCycle.UNCONFIRMED
        reason = "现有硬指标不足以确认周期阶段"
    missing = [key for key, value in (("up_ratio", breadth), ("net_flow", flow)) if value is None]
    diagnostic = 'matched'
    label = CYCLE_LABELS[cycle]
    next_action = '继续跟踪次日涨停供给、广度与资金反馈。'
    if cycle == SectorCycle.UNCONFIRMED:
        if missing:
            diagnostic, label = 'data_missing', '周期数据待补齐'
            reason = '阶段规则未命中，且缺少' + '、'.join({'up_ratio': '成员涨跌广度', 'net_flow': '成员资金流'}[k] for k in missing) + '。'
            next_action = '补齐同日成员行情或资金流后重算。'
        elif board_count == 0:
            diagnostic, label = 'no_limit_structure', '暂无短线涨停结构'
            reason = '本方向当日无涨停，且未同时满足资金流出与广度转弱的衰减条件。'
            next_action = '保留趋势研究背景；出现涨停供给后再判短线阶段。'
        else:
            diagnostic, label = 'rule_unmatched', '阶段证据不足'
            reason = f'当日{board_count}家涨停、首板{first_board_count}家、最高{max_board}板，未命中当前阶段规则。'
            next_action = '观察首板扩散、连板高度与前排承接；完整状态机在周期功能升级时校准。'
    return {"code": cycle.value, "label": label, "reason": reason,
            'diagnostic_status': diagnostic, 'missing_inputs': missing, 'next_action': next_action,
            'rule_version': 'sector-cycle-diagnostics-v1',
            'inputs': {'first_board_count': first_board_count, 'max_board': max_board,
                       'board_count': board_count, 'broken_count': broken_count,
                       'up_ratio': breadth, 'net_flow': flow, 'prior_first_board_count': prior_first_board_count}}


def assign_exclusive_role(
    *,
    consecutive_count: int = 0,
    sector_highest_board: int = 0,
    market_cap_yi: Any = None,
    amount_yi: Any = None,
    trend_core: bool = False,
    front_row: bool = False,
) -> dict[str, Any]:
    """Assign exactly one role; front row can only be one of the three core roles."""

    cap = _number(market_cap_yi, 0.0) or 0.0
    amount = _number(amount_yi, 0.0) or 0.0
    if consecutive_count >= 2 and consecutive_count >= sector_highest_board:
        role = StockRole.EMOTION_LEADER
    elif cap >= 300 and amount >= 20:
        role = StockRole.CAPACITY_COMMANDER
    elif trend_core:
        role = StockRole.TREND_CORE
    elif front_row:
        # A front-row label without one of the three roles violates the domain
        # contract.  Keep it as trend core and expose the source for audit.
        role = StockRole.TREND_CORE
    else:
        role = StockRole.BACK_ROW
    return {
        "code": role.value,
        "label": ROLE_LABELS[role],
        "front_row": role is not StockRole.BACK_ROW,
    }


def classify_board_nature(
    *,
    posture: str,
    sector_resonance: bool,
    relative_strength: bool,
    opens_space: bool,
) -> dict[str, Any]:
    if posture == MarketPosture.TREND_ATTACK.value and sector_resonance and opens_space:
        nature = BoardNature.ATTACK
        reason = "与指数、量能和板块共振，承担价格发现与空间拓展"
    elif posture == MarketPosture.EMOTION_DEFENCE.value and relative_strength and not sector_resonance:
        nature = BoardNature.DEFENCE
        reason = "弱市中依靠稀缺性和抱团维持强度，承担风险规避"
    else:
        nature = BoardNature.NEUTRAL
        reason = "尚未形成明确的进攻或防御属性"
    return {"code": nature.value, "label": {BoardNature.ATTACK: "进攻板", BoardNature.DEFENCE: "防御板", BoardNature.NEUTRAL: "中性"}[nature], "reason": reason}


def build_position_chart(
    *,
    market: str,
    sector_cycle: str,
    role: str,
    price_shape: str,
) -> dict[str, Any]:
    return {
        "market": market,
        "sector_cycle": sector_cycle,
        "role": role,
        "price_shape": price_shape,
        "definition": "大盘位置 × 板块周期 × 个股角色 × 价格路径",
    }


def evaluate_tape_strength(
    *,
    proactive_flow: bool | None,
    absorption: bool | None,
    relative_strength: bool | None,
    linkage: bool | None,
    close_quality: bool | None,
) -> dict[str, Any]:
    checks = {
        "proactive_flow": proactive_flow,
        "absorption": absorption,
        "relative_strength": relative_strength,
        "linkage": linkage,
        "close_quality": close_quality,
    }
    available = [value for value in checks.values() if value is not None]
    passed = sum(value is True for value in available)
    if len(available) < 3:
        state = "insufficient"
        label = "证据不足"
    elif passed >= 4:
        state = "strong"
        label = "盘口强"
    elif passed >= 2:
        state = "normal"
        label = "盘口中性"
    else:
        state = "weak"
        label = "盘口弱"
    return {
        "state": state,
        "label": label,
        "checks": checks,
        "available_count": len(available),
        "passed_count": passed,
        "note": "只使用主动性、承接、相对强弱、联动和收盘质量；缺少深度行情时不虚构五档。",
    }


def detect_turnover_new_faces(
    current_rows: Iterable[dict[str, Any]],
    previous_active_tickers: set[str],
    *,
    threshold_yi: float = 20.0,
) -> list[dict[str, Any]]:
    result = []
    for raw in current_rows:
        row = dict(raw)
        ticker = str(row.get("ticker") or row.get("ts_code") or "").upper()
        amount_yi = _number(row.get("amount_yi"))
        if amount_yi is None:
            amount = _number(row.get("amount"), 0.0) or 0.0
            amount_yi = amount / 100_000.0
        if ticker and amount_yi >= threshold_yi and ticker not in previous_active_tickers:
            result.append({**row, "ticker": ticker, "amount_yi": round(amount_yi, 2), "new_face": True})
    result.sort(key=lambda item: float(item.get("amount_yi") or 0.0), reverse=True)
    return result


def _sorted_bars(bars: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((dict(bar) for bar in bars), key=lambda bar: str(bar.get("date") or bar.get("trade_date") or ""))


def detect_520(bars: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Detect the frozen 520 structure-repair entry, not a generic MA crossover."""

    ordered = _sorted_bars(bars)
    closes = [_number(bar.get("close")) for bar in ordered]
    lows = [_number(bar.get("low"), _number(bar.get("close"), 0.0)) for bar in ordered]
    if len(ordered) < 25 or any(value is None for value in closes[-21:]):
        return None
    values = [float(value) for value in closes if value is not None]
    ma5 = _mean(values[-5:])
    ma5_prev = _mean(values[-6:-1])
    ma5_prev2 = _mean(values[-7:-2])
    ma20 = _mean(values[-20:])
    ma20_prev = _mean(values[-21:-1])
    ma20_5ago = _mean(values[-25:-5])
    slope = ma5 - ma5_prev
    prior_slope = ma5_prev - ma5_prev2
    gap = (ma20 - ma5) / ma20 if ma20 else 1.0
    cross_today = ma5_prev < ma20_prev and ma5 >= ma20
    imminent = ma5 < ma20 and 0 <= gap <= 0.02 and slope > 0 and prior_slope <= 0
    downtrend_or_flat = ma20 <= ma20_5ago * 1.005
    recent_lows = [float(value or 0.0) for value in lows[-6:]]
    stabilized = recent_lows[-1] >= min(recent_lows[:-1]) and values[-1] >= ma5
    if not (downtrend_or_flat and stabilized and (cross_today or imminent)):
        return None
    state = "cross_confirmed" if cross_today else "turning_imminent_cross"
    return {
        "matched": True,
        "state": state,
        "label": "上穿确认" if cross_today else "5日线拐点 · 临界上穿",
        "buy_point": "5日线拐点并接近上穿20日线时；上穿当日属于确认",
        "sell_rule": "5日线斜率重新转负",
        "ma5": round(ma5, 4),
        "ma20": round(ma20, 4),
        "ma5_slope": round(slope, 6),
        "gap_to_ma20": round(gap, 6),
        "price_shape": "下跌后稳住 · 5日线拐点 · 临界上穿20日线",
    }


def detect_weekly_breakout(bars: Iterable[dict[str, Any]], *, sessions: int = 100) -> dict[str, Any] | None:
    ordered = _sorted_bars(bars)
    if len(ordered) < sessions + 1:
        return None
    current = ordered[-1]
    previous = ordered[-(sessions + 1):-1]
    highs = [_number(bar.get("high")) for bar in previous]
    highs = [float(value) for value in highs if value is not None]
    close = _number(current.get("close"))
    if not highs or close is None:
        return None
    prior_high = max(highs)
    if close <= prior_high:
        return None
    return {
        "matched": True,
        "state": "confirmed",
        "label": "日K突破过去20周新高",
        "buy_point": "日K收盘价首次站上过去100个交易日最高价",
        "breakout_level": round(prior_high, 4),
        "close": round(close, 4),
        "breakout_pct": round(close / prior_high - 1.0, 6),
        "price_shape": "20周压力收敛后由日K完成突破",
    }


def detect_red_k_net_inflow(
    bar: dict[str, Any],
    *,
    net_inflow: Any,
    market_red_k_confirmed: bool,
) -> dict[str, Any] | None:
    if not market_red_k_confirmed:
        return None
    open_price = _number(bar.get("open"))
    close = _number(bar.get("close"))
    high = _number(bar.get("high"))
    low = _number(bar.get("low"))
    flow = _number(net_inflow)
    if None in (open_price, close, high, low, flow) or high <= low:
        return None
    body_ratio = (close - open_price) / (high - low)
    close_position = (close - low) / (high - low)
    if close <= open_price or body_ratio < 0.45 or close_position < 0.65 or flow <= 0:
        return None
    return {
        "matched": True,
        "state": "same_day_confirmed",
        "label": "红K净流入",
        "buy_point": "大盘实心红K确认当日，按净流入降序选择共振前排",
        "net_inflow": round(flow, 4),
        "body_ratio": round(body_ratio, 4),
        "close_position": round(close_position, 4),
        "price_shape": "当日实心阳线 · 收盘靠近高位 · 主力净流入",
    }
