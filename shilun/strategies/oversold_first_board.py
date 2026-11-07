"""超跌首板策略的 T-1 选股计算。

本模块只消费按交易日期严格升序排列的日线数据，不读取数据库、不访问网络，
也不产生交易计划。策略结果同时保留机器可读指标、逐项判定和人类可读证据，
便于候选池、回放和后续版本之间复现同一口径。

``amount`` 沿用项目内 Tushare 日线口径，单位为千元。因此 30 亿元对应
``3_000_000``。历史涨停采用收盘价相对前收盘价不低于 9.5% 的保守近似；
它只提供加分，不参与硬门判断。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite
from typing import Any, Mapping, Sequence


STRATEGY_ID = "oversold_first_board"
STRATEGY_VERSION = "0.1.0"
RULE_ID = f"{STRATEGY_ID}@{STRATEGY_VERSION}"


@dataclass(frozen=True)
class OversoldFirstBoardConfig:
    """0.1.0 版本的全部可审计阈值。"""

    min_bars: int = 60
    avg_amount_window: int = 15
    max_avg_amount: float = 3_000_000.0  # 千元，即 30 亿元
    return_window: int = 7
    max_return_7d: float = 0.15
    ma20_window: int = 20
    ma60_window: int = 60
    max_abs_close_to_ma20: float = 0.03
    min_ma60_overhead: float = 0.08
    drawdown_window: int = 20
    min_drawdown_from_high_20d: float = 0.05
    rsi_period: int = 14
    min_rsi14: float = 35.0
    max_rsi14: float = 50.0
    limit_up_lookback: int = 60
    limit_up_threshold: float = 0.095


DEFAULT_CONFIG = OversoldFirstBoardConfig()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _raw_date(row: Mapping[str, Any]) -> str:
    value = row.get("date") or row.get("trade_date") or ""
    return str(value).strip()


def _display_date(value: str) -> str:
    value = str(value or "").strip()
    if len(value) >= 8 and value[:8].isdigit() and "-" not in value[:10]:
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return value[:10]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _rsi(closes: Sequence[float], period: int) -> float:
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    sample = changes[-period:]
    avg_gain = sum(max(change, 0.0) for change in sample) / period
    avg_loss = sum(max(-change, 0.0) for change in sample) / period
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _criterion(
    *,
    criterion_id: str,
    label: str,
    hard: bool,
    passed: bool,
    actual_value: Any,
    actual: str,
    unit: str,
    operator: str,
    threshold: Any,
    rule: str,
    reason: str,
    score_awarded: float,
    score_max: float,
    role: str = "gate",
) -> dict[str, Any]:
    return {
        "id": criterion_id,
        "label": label,
        "hard": hard,
        "role": role,
        "passed": passed,
        "actual_value": actual_value,
        "actual": actual,
        "unit": unit,
        "operator": operator,
        "threshold": threshold,
        "rule": rule,
        "reason": reason,
        "score_awarded": score_awarded,
        "score_max": score_max,
    }


def _empty_metrics() -> dict[str, Any]:
    return {
        "avg_amount_15d": None,
        "avg_amount_15d_cny": None,
        "return_7d": None,
        "close": None,
        "ma20": None,
        "close_to_ma20_ratio": None,
        "ma60": None,
        "ma60_overhead_ratio": None,
        "drawdown_from_high_20d": None,
        "rsi14": None,
        "oversold_component_count": 0,
        "limit_up_count_60d": 0,
        "limit_up_dates_60d": [],
        "last_limit_up_date": None,
        "history_limit_up_bonus": 0.0,
    }


def _incomplete_result(
    *,
    as_of: str,
    bar_count: int,
    reason: str,
    config: OversoldFirstBoardConfig,
) -> dict[str, Any]:
    unavailable = "数据不足，无法计算"
    criteria = [
        _criterion(
            criterion_id="data_complete",
            label="日线数据完整",
            hard=True,
            passed=False,
            actual_value=bar_count,
            actual=f"{bar_count}根",
            unit="bars",
            operator=">=",
            threshold=config.min_bars,
            rule=f"至少 {config.min_bars} 根有效日线且日期严格升序",
            reason=reason,
            score_awarded=0.0,
            score_max=0.0,
        ),
        _criterion(
            criterion_id="avg_amount_15d",
            label="15日平均成交额",
            hard=True,
            passed=False,
            actual_value=None,
            actual=unavailable,
            unit="CNY",
            operator="<",
            threshold=3_000_000_000.0,
            rule="A15 严格小于 30 亿元",
            reason=unavailable,
            score_awarded=0.0,
            score_max=20.0,
        ),
        _criterion(
            criterion_id="return_7d",
            label="7日累计涨幅",
            hard=True,
            passed=False,
            actual_value=None,
            actual=unavailable,
            unit="ratio",
            operator="<",
            threshold=config.max_return_7d,
            rule="最新收盘价相对 7 个交易日前严格小于 15%",
            reason=unavailable,
            score_awarded=0.0,
            score_max=15.0,
        ),
    ]
    component_specs = (
        ("close_near_ma20", "接近MA20", "abs<=", config.max_abs_close_to_ma20, 10.0),
        ("ma60_overhead", "MA60上方空间", ">=", config.min_ma60_overhead, 20.0),
        ("drawdown_20d", "20日高点回撤", ">=", config.min_drawdown_from_high_20d, 15.0),
        (
            "rsi14_zone",
            "RSI14超跌区间",
            "between",
            [config.min_rsi14, config.max_rsi14],
            10.0,
        ),
    )
    for criterion_id, label, operator, threshold, score_max in component_specs:
        criteria.append(
            _criterion(
                criterion_id=criterion_id,
                label=label,
                hard=False,
                passed=False,
                actual_value=None,
                actual=unavailable,
                unit="ratio" if criterion_id != "rsi14_zone" else "index",
                operator=operator,
                threshold=threshold,
                rule="超跌组合的解释分项",
                reason=unavailable,
                score_awarded=0.0,
                score_max=score_max,
                role="oversold_component",
            )
        )
    criteria.extend(
        [
            _criterion(
                criterion_id="oversold_combo",
                label="超跌组合",
                hard=True,
                passed=False,
                actual_value=None,
                actual=unavailable,
                unit="boolean",
                operator="formula",
                threshold="MA60上方空间 AND 20日回撤 AND (接近MA20 OR RSI14区间)",
                rule="MA60上方空间≥8%、20日高点回撤≥5%，且接近MA20或RSI14处于35~50",
                reason=unavailable,
                score_awarded=0.0,
                score_max=0.0,
            ),
            _criterion(
                criterion_id="history_limit_up_60d",
                label="近60日历史涨停",
                hard=False,
                passed=False,
                actual_value=None,
                actual=unavailable,
                unit="count",
                operator=">=",
                threshold=1,
                rule="按相邻收盘价涨幅≥9.5%近似识别；仅加分，不影响入选",
                reason=unavailable,
                score_awarded=0.0,
                score_max=10.0,
                role="bonus",
            ),
        ]
    )
    return {
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "strategy_name": "超跌首板 T-1 选股",
        "rule_id": RULE_ID,
        "as_of": as_of,
        "passed": False,
        "data_complete": False,
        "score": 0.0,
        "score_max": 100.0,
        "criteria": criteria,
        "failure_ids": ["data_complete"],
        "failure_reasons": [reason],
        "metrics": _empty_metrics(),
        "evidence": [reason],
        "summary": reason,
    }


def evaluate_oversold_first_board(
    bars: Sequence[Mapping[str, Any]],
    *,
    config: OversoldFirstBoardConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """评估 T-1 超跌首板候选，输入日线必须按日期严格升序。

    硬门只有 A15、R7 和超跌组合；历史涨停是 10 分加分项。返回值不引用
    输入对象，调用过程不会修改 ``bars``。
    """
    rows = list(bars or [])
    raw_dates = [_raw_date(row) for row in rows]
    as_of = _display_date(raw_dates[-1]) if raw_dates else ""

    if raw_dates and any(not value for value in raw_dates):
        return _incomplete_result(
            as_of=as_of,
            bar_count=len(rows),
            reason="日线缺少 date/trade_date，无法复现计算时点",
            config=config,
        )
    if any(current <= previous for previous, current in zip(raw_dates, raw_dates[1:])):
        raise ValueError("bars 必须按 date/trade_date 严格升序且日期不得重复")
    if len(rows) < config.min_bars:
        return _incomplete_result(
            as_of=as_of,
            bar_count=len(rows),
            reason=f"日线不足：当前 {len(rows)} 根，至少需要 {config.min_bars} 根",
            config=config,
        )

    close_values = [_number(row.get("close")) for row in rows]
    required_closes = close_values[-config.ma60_window :]
    amount_values = [_number(row.get("amount")) for row in rows[-config.avg_amount_window :]]
    high_values = [_number(row.get("high")) for row in rows[-config.drawdown_window :]]
    if (
        len(required_closes) < config.ma60_window
        or any(value is None or value <= 0 for value in required_closes)
        or len(amount_values) < config.avg_amount_window
        or any(value is None or value < 0 for value in amount_values)
        or len(high_values) < config.drawdown_window
        or any(value is None or value <= 0 for value in high_values)
    ):
        return _incomplete_result(
            as_of=as_of,
            bar_count=len(rows),
            reason="最近60日存在无效 close/high/amount，无法完成策略计算",
            config=config,
        )

    recent_closes = [float(value) for value in required_closes if value is not None]
    amounts = [float(value) for value in amount_values if value is not None]
    highs = [float(value) for value in high_values if value is not None]
    close = recent_closes[-1]
    avg_amount_15d = _mean(amounts)
    return_7d = close / recent_closes[-(config.return_window + 1)] - 1.0
    ma20 = _mean(recent_closes[-config.ma20_window :])
    ma60 = _mean(recent_closes[-config.ma60_window :])
    close_to_ma20 = close / ma20 - 1.0
    ma60_overhead = ma60 / close - 1.0
    drawdown_20d = 1.0 - close / max(highs)
    rsi14 = _rsi(recent_closes, config.rsi_period)

    amount_passed = avg_amount_15d < config.max_avg_amount
    return_passed = return_7d < config.max_return_7d and not isclose(
        return_7d,
        config.max_return_7d,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    ma20_passed = abs(close_to_ma20) <= config.max_abs_close_to_ma20
    ma60_passed = ma60_overhead >= config.min_ma60_overhead
    drawdown_passed = drawdown_20d >= config.min_drawdown_from_high_20d
    rsi_passed = config.min_rsi14 <= rsi14 <= config.max_rsi14
    oversold_passed = ma60_passed and drawdown_passed and (ma20_passed or rsi_passed)

    limit_start = max(1, len(rows) - config.limit_up_lookback)
    limit_up_dates: list[str] = []
    for index in range(limit_start, len(rows)):
        previous_close = close_values[index - 1]
        current_close = close_values[index]
        if previous_close is None or previous_close <= 0 or current_close is None:
            continue
        if current_close / previous_close - 1.0 >= config.limit_up_threshold:
            limit_up_dates.append(_display_date(raw_dates[index]))
    limit_up_count = len(limit_up_dates)
    limit_up_passed = limit_up_count >= 1
    limit_up_bonus = 10.0 if limit_up_passed else 0.0

    amount_score = 20.0 if amount_passed else 0.0
    return_score = 15.0 if return_passed else 0.0
    ma20_score = 10.0 if ma20_passed else 0.0
    ma60_score = 20.0 if ma60_passed else 0.0
    drawdown_score = 15.0 if drawdown_passed else 0.0
    rsi_score = 10.0 if rsi_passed else 0.0
    score = (
        amount_score
        + return_score
        + ma20_score
        + ma60_score
        + drawdown_score
        + rsi_score
        + limit_up_bonus
    )

    criteria = [
        _criterion(
            criterion_id="data_complete",
            label="日线数据完整",
            hard=True,
            passed=True,
            actual_value=len(rows),
            actual=f"{len(rows)}根",
            unit="bars",
            operator=">=",
            threshold=config.min_bars,
            rule=f"至少 {config.min_bars} 根有效日线且日期严格升序",
            reason="日线数量和必需字段完整",
            score_awarded=0.0,
            score_max=0.0,
        ),
        _criterion(
            criterion_id="avg_amount_15d",
            label="15日平均成交额",
            hard=True,
            passed=amount_passed,
            actual_value=round(avg_amount_15d * 1_000.0, 2),
            actual=f"{avg_amount_15d / 100_000.0:.2f}亿元",
            unit="CNY",
            operator="<",
            threshold=config.max_avg_amount * 1_000.0,
            rule="A15 严格小于 30 亿元",
            reason=(
                "15日平均成交额低于上限"
                if amount_passed
                else "15日平均成交额达到或超过30亿元"
            ),
            score_awarded=amount_score,
            score_max=20.0,
        ),
        _criterion(
            criterion_id="return_7d",
            label="7日累计涨幅",
            hard=True,
            passed=return_passed,
            actual_value=round(return_7d, 8),
            actual=f"{return_7d * 100.0:.2f}%",
            unit="ratio",
            operator="<",
            threshold=config.max_return_7d,
            rule="最新收盘价相对 7 个交易日前严格小于 15%",
            reason="7日涨幅未过热" if return_passed else "7日累计涨幅达到或超过15%",
            score_awarded=return_score,
            score_max=15.0,
        ),
        _criterion(
            criterion_id="close_near_ma20",
            label="接近MA20",
            hard=False,
            passed=ma20_passed,
            actual_value=round(close_to_ma20, 8),
            actual=f"{close_to_ma20 * 100.0:+.2f}%",
            unit="ratio",
            operator="abs<=",
            threshold=config.max_abs_close_to_ma20,
            rule="收盘价相对MA20的绝对偏离不超过3%",
            reason="价格贴近MA20" if ma20_passed else "价格与MA20偏离超过3%",
            score_awarded=ma20_score,
            score_max=10.0,
            role="oversold_component",
        ),
        _criterion(
            criterion_id="ma60_overhead",
            label="MA60上方空间",
            hard=False,
            passed=ma60_passed,
            actual_value=round(ma60_overhead, 8),
            actual=f"{ma60_overhead * 100.0:.2f}%",
            unit="ratio",
            operator=">=",
            threshold=config.min_ma60_overhead,
            rule="MA60相对现价至少高8%",
            reason="距离上方MA60足够远" if ma60_passed else "距离上方MA60不足8%",
            score_awarded=ma60_score,
            score_max=20.0,
            role="oversold_component",
        ),
        _criterion(
            criterion_id="drawdown_20d",
            label="20日高点回撤",
            hard=False,
            passed=drawdown_passed,
            actual_value=round(drawdown_20d, 8),
            actual=f"{drawdown_20d * 100.0:.2f}%",
            unit="ratio",
            operator=">=",
            threshold=config.min_drawdown_from_high_20d,
            rule="现价距最近20日最高价至少回撤5%",
            reason="20日回撤达到超跌要求" if drawdown_passed else "20日回撤不足5%",
            score_awarded=drawdown_score,
            score_max=15.0,
            role="oversold_component",
        ),
        _criterion(
            criterion_id="rsi14_zone",
            label="RSI14超跌区间",
            hard=False,
            passed=rsi_passed,
            actual_value=round(rsi14, 4),
            actual=f"{rsi14:.2f}",
            unit="index",
            operator="between",
            threshold=[config.min_rsi14, config.max_rsi14],
            rule="RSI14处于35至50之间（含边界）",
            reason="RSI处于低位修复区" if rsi_passed else "RSI不在35至50区间",
            score_awarded=rsi_score,
            score_max=10.0,
            role="oversold_component",
        ),
        _criterion(
            criterion_id="oversold_combo",
            label="超跌组合",
            hard=True,
            passed=oversold_passed,
            actual_value=oversold_passed,
            actual="通过" if oversold_passed else "未通过",
            unit="boolean",
            operator="formula",
            threshold="MA60上方空间 AND 20日回撤 AND (接近MA20 OR RSI14区间)",
            rule="MA60上方空间≥8%、20日高点回撤≥5%，且接近MA20或RSI14处于35~50",
            reason=(
                "MA60、20日回撤和MA20/RSI确认共同构成超跌形态"
                if oversold_passed
                else "超跌组合条件不足"
            ),
            score_awarded=0.0,
            score_max=0.0,
        ),
        _criterion(
            criterion_id="history_limit_up_60d",
            label="近60日历史涨停",
            hard=False,
            passed=limit_up_passed,
            actual_value=limit_up_count,
            actual=f"{limit_up_count}次",
            unit="count",
            operator=">=",
            threshold=1,
            rule="按相邻收盘价涨幅≥9.5%近似识别；仅加分，不影响入选",
            reason=(
                "检测到历史涨停，奖励10分"
                if limit_up_passed
                else "未检测到历史涨停，不加分但不否决"
            ),
            score_awarded=limit_up_bonus,
            score_max=10.0,
            role="bonus",
        ),
    ]

    passed = amount_passed and return_passed and oversold_passed
    hard_failures = [item for item in criteria if item["hard"] and not item["passed"]]
    failure_reasons = [
        f"{item['label']}：{item['actual']}；要求{item['rule']}" for item in hard_failures
    ]
    evidence = [
        f"A15为{avg_amount_15d / 100_000.0:.2f}亿元，硬门要求严格小于30亿元。",
        f"R7为{return_7d * 100.0:.2f}%，硬门要求严格小于15%。",
        (
            f"超跌结构：现价相对MA20 {close_to_ma20 * 100.0:+.2f}%，"
            f"MA60高于现价{ma60_overhead * 100.0:.2f}%，"
            f"20日高点回撤{drawdown_20d * 100.0:.2f}%，RSI14为{rsi14:.2f}。"
        ),
        (
            f"近60日检测到{limit_up_count}次涨停近似事件，历史涨停加{limit_up_bonus:.0f}分；"
            "该项不影响硬门结果。"
        ),
    ]
    if passed:
        summary = f"T-1硬门全部通过，综合得分{score:.0f}/100"
    else:
        summary = "；".join(failure_reasons)

    return {
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "strategy_name": "超跌首板 T-1 选股",
        "rule_id": RULE_ID,
        "as_of": as_of,
        "passed": passed,
        "data_complete": True,
        "score": round(score, 2),
        "score_max": 100.0,
        "criteria": criteria,
        "failure_ids": [item["id"] for item in hard_failures],
        "failure_reasons": failure_reasons,
        "metrics": {
            "avg_amount_15d": round(avg_amount_15d, 2),
            "avg_amount_15d_cny": round(avg_amount_15d * 1_000.0, 2),
            "return_7d": round(return_7d, 8),
            "close": round(close, 4),
            "ma20": round(ma20, 4),
            "close_to_ma20_ratio": round(close_to_ma20, 8),
            "ma60": round(ma60, 4),
            "ma60_overhead_ratio": round(ma60_overhead, 8),
            "drawdown_from_high_20d": round(drawdown_20d, 8),
            "rsi14": round(rsi14, 4),
            "oversold_component_count": sum(
                (ma20_passed, ma60_passed, drawdown_passed, rsi_passed)
            ),
            "limit_up_count_60d": limit_up_count,
            "limit_up_dates_60d": limit_up_dates,
            "last_limit_up_date": limit_up_dates[-1] if limit_up_dates else None,
            "history_limit_up_bonus": limit_up_bonus,
        },
        "evidence": evidence,
        "summary": summary,
    }


class OversoldFirstBoardSelectionStrategy:
    """可注入配置的轻量策略门面，供候选池/回放调用。"""

    strategy_id = STRATEGY_ID
    version = STRATEGY_VERSION
    rule_id = RULE_ID
    name = "超跌首板 T-1 选股"
    description = "低成交额、短期不过热与均线/回撤/RSI超跌结构；历史涨停仅加分。"

    def __init__(self, config: OversoldFirstBoardConfig = DEFAULT_CONFIG) -> None:
        self.config = config

    def evaluate_selection(self, bars: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return evaluate_oversold_first_board(bars, config=self.config)


__all__ = [
    "DEFAULT_CONFIG",
    "RULE_ID",
    "STRATEGY_ID",
    "STRATEGY_VERSION",
    "OversoldFirstBoardConfig",
    "OversoldFirstBoardSelectionStrategy",
    "evaluate_oversold_first_board",
]
