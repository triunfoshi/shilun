"""按执行策略生成可审计的止损建议。

止损建议和有效止损是两个概念：本模块只根据已完成日线生成候选与推荐值，
由新建持仓提交或用户显式确认后，才写入 holdings.stop_loss_1。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from shilun.trading.config import DEFAULT_EXECUTION_LEVEL_RULES, ExecutionLevelConfig
from shilun.trading.contracts import EntryScenario


MA5_STOP_POLICY_VERSION = "ma5_stop_v0.1.0"
MANUAL_STOP_POLICY_VERSION = "manual_ma8_atr_v0.1.0"
E7_STOP_POLICY_VERSION = "e7_stabilization_atr_v1.0.0"


class StopPlanError(ValueError):
    """止损建议缺少必要输入或无法生成。"""


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _date_text(row: dict[str, Any]) -> str:
    return str(row.get("date") or row.get("trade_date") or "")[:10]


def _prepared_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for raw in sorted(rows or [], key=_date_text):
        close = _positive(raw.get("close"))
        low = _positive(raw.get("low"))
        if close is None or low is None:
            continue
        prepared.append({**raw, "date": _date_text(raw), "close": close, "low": low})
    return prepared


def _latest_confirmed_swing_low(rows: list[dict[str, Any]], reference_price: float) -> dict[str, Any] | None:
    """返回最近一个已经被后一根日线确认的局部低点。"""

    start = max(1, len(rows) - 20)
    for index in range(len(rows) - 2, start - 1, -1):
        previous_low = float(rows[index - 1]["low"])
        current_low = float(rows[index]["low"])
        next_low = float(rows[index + 1]["low"])
        if current_low <= previous_low and current_low < next_low and current_low < reference_price:
            return {
                "candidate_id": "previous_swing_low",
                "label": "最近确认前低",
                "value": round(current_low, 4),
                "as_of": rows[index]["date"],
                "reason": "该低点左右各有一根已完成日线，属于已确认结构低点。",
            }
    return None


def build_ma5_stop_plan(
    rows: list[dict[str, Any]],
    *,
    reference_price: float,
    as_of: str | None = None,
) -> dict[str, Any]:
    """用最近确认前低与 MA8 生成 MA5 战法止损建议。

    选择规则是取所有低于参考价的有效候选中较高者，让“MA8 趋势防线”和
    “前低结构防线”任一先被破坏时进入保护；盘中不会自动追踪移动该价位。
    """

    reference = _positive(reference_price)
    if reference is None:
        raise StopPlanError("reference_price 必须大于 0")
    prepared = _prepared_rows(rows)
    if len(prepared) < 8:
        raise StopPlanError("MA5 止损建议至少需要 8 根有效已完成日线")

    ma8 = sum(float(item["close"]) for item in prepared[-8:]) / 8
    candidates: list[dict[str, Any]] = []
    if 0 < ma8 < reference:
        candidates.append({
            "candidate_id": "ma8",
            "label": "MA8 趋势防线",
            "value": round(ma8, 4),
            "as_of": prepared[-1]["date"],
            "reason": "最近 8 根已完成日线收盘价均值，盘中冻结不移动。",
        })
    swing_low = _latest_confirmed_swing_low(prepared, reference)
    if swing_low is not None:
        candidates.append(swing_low)
    if not candidates:
        raise StopPlanError("MA8 与最近确认前低均未形成低于参考价的有效止损候选")

    candidates.sort(key=lambda item: float(item["value"]), reverse=True)
    recommended = dict(candidates[0])
    secondary = dict(candidates[-1]) if len(candidates) > 1 else None
    return {
        "status": "suggested",
        "strategy_id": "ma5_v0_2",
        "strategy_version": "0.2.0",
        "policy_version": MA5_STOP_POLICY_VERSION,
        "reference_price": round(reference, 4),
        "as_of": as_of or prepared[-1]["date"],
        "recommended_stop": recommended["value"],
        "recommended_source": recommended["candidate_id"],
        "recommended_label": recommended["label"],
        "secondary_stop": secondary["value"] if secondary else None,
        "secondary_source": secondary["candidate_id"] if secondary else None,
        "candidates": candidates,
        "selection_rule": "取低于参考价的 MA8 与最近确认前低中较高者；盘中冻结，修改需重新确认。",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_manual_stop_plan(
    rows: list[dict[str, Any]],
    *,
    reference_price: float,
    as_of: str | None = None,
) -> dict[str, Any]:
    """人工策略止损：取 MA8 与“支撑位减 1ATR”中较高的有效保护位。"""

    reference = _positive(reference_price)
    if reference is None:
        raise StopPlanError("reference_price 必须大于 0")
    prepared = _prepared_rows(rows)
    if len(prepared) < 15:
        raise StopPlanError("人工策略止损至少需要 15 根有效前复权日线")

    ma8 = sum(float(item["close"]) for item in prepared[-8:]) / 8
    true_ranges: list[float] = []
    for previous, current in zip(prepared[-15:-1], prepared[-14:]):
        high = _positive(current.get("high")) or float(current["close"])
        low = float(current["low"])
        previous_close = float(previous["close"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    atr14 = sum(true_ranges) / len(true_ranges)
    support = _latest_confirmed_swing_low(prepared, reference)
    if support is None:
        raise StopPlanError("人工策略止损缺少最近确认支撑位")
    support_minus_atr = float(support["value"]) - atr14

    candidates = []
    if 0 < ma8 < reference:
        candidates.append({
            "candidate_id": "ma8",
            "label": "MA8 趋势防线",
            "value": round(ma8, 4),
            "as_of": prepared[-1]["date"],
            "reason": "最近 8 根前复权日线收盘价均值。",
        })
    if 0 < support_minus_atr < reference:
        candidates.append({
            "candidate_id": "support_minus_atr",
            "label": "支撑位 - 1ATR",
            "value": round(support_minus_atr, 4),
            "as_of": support["as_of"],
            "reason": f"确认支撑 {float(support['value']):.4f} 减 ATR14 {atr14:.4f}。",
        })
    if not candidates:
        raise StopPlanError("MA8 与支撑位 - 1ATR 均未形成低于参考价的有效止损候选")
    candidates.sort(key=lambda item: float(item["value"]), reverse=True)
    recommended = candidates[0]
    secondary = candidates[1] if len(candidates) > 1 else None
    return {
        "status": "suggested",
        "strategy_id": "manual",
        "strategy_version": "1.0.0",
        "policy_version": MANUAL_STOP_POLICY_VERSION,
        "reference_price": round(reference, 4),
        "as_of": as_of or prepared[-1]["date"],
        "recommended_stop": recommended["value"],
        "recommended_source": recommended["candidate_id"],
        "recommended_label": recommended["label"],
        "secondary_stop": secondary["value"] if secondary else None,
        "secondary_source": secondary["candidate_id"] if secondary else None,
        "ma8": round(ma8, 4),
        "support": round(float(support["value"]), 4),
        "atr14": round(atr14, 4),
        "candidates": candidates,
        "selection_rule": "人工策略取低于参考价的 MA8 与（最近确认支撑位 - 1ATR14）中较高者。",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_e7_stop_plan(
    rows: list[dict[str, Any]],
    *,
    reference_price: float,
    stabilization_low: float | None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """E7 只用尾盘企稳低点减 0.10ATR，禁止混入 MA8/趋势支撑。"""

    reference = _positive(reference_price)
    anchor = _positive(stabilization_low)
    if reference is None:
        raise StopPlanError("reference_price 必须大于 0")
    if anchor is None:
        raise StopPlanError("E7 止损缺少 T-1 14:00 后尾盘企稳低点")
    prepared = _prepared_rows(rows)
    if len(prepared) < 15:
        raise StopPlanError("E7 止损至少需要入场日前 15 根有效已完成日线")
    true_ranges: list[float] = []
    for previous, current in zip(prepared[-15:-1], prepared[-14:]):
        high = _positive(current.get("high")) or float(current["close"])
        low = float(current["low"])
        previous_close = float(previous["close"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    atr14 = sum(true_ranges) / len(true_ranges)
    scenario_plan = build_scenario_stop_plan(ScenarioStopInput(
        scenario=EntryScenario.E7_OVERSOLD_REBOUND_OVERNIGHT,
        entry_price=reference,
        atr14=atr14,
        stabilization_low=anchor,
    ))
    candidate = {
        "candidate_id": "e7_stabilization_low",
        "label": "E7 尾盘企稳低点 - 0.10ATR",
        "value": scenario_plan.hard_stop,
        "as_of": as_of or prepared[-1]["date"],
        "reason": (
            f"尾盘企稳低点 {scenario_plan.structural_anchor:.4f} 减 "
            f"{scenario_plan.buffer_atr_factor:.2f}ATR（{scenario_plan.buffer_amount:.4f}）。"
        ),
    }
    return {
        "status": "suggested",
        "strategy_id": "e7_oversold_overnight",
        "strategy_version": "1.0.0",
        "policy_version": E7_STOP_POLICY_VERSION,
        "reference_price": round(reference, 4),
        "as_of": as_of or prepared[-1]["date"],
        "recommended_stop": scenario_plan.hard_stop,
        "recommended_source": candidate["candidate_id"],
        "recommended_label": candidate["label"],
        "secondary_stop": None,
        "secondary_source": None,
        "stabilization_low": scenario_plan.structural_anchor,
        "atr14": scenario_plan.atr14,
        "buffer_atr_factor": scenario_plan.buffer_atr_factor,
        "candidates": [candidate],
        "selection_rule": scenario_plan.selection_rule,
        "exit_window": "次一交易日 09:30–10:00",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_strategy_stop_plan(
    strategy_id: str,
    rows: list[dict[str, Any]],
    *,
    reference_price: float,
    as_of: str | None = None,
    stabilization_low: float | None = None,
) -> dict[str, Any]:
    normalized = str(strategy_id or "manual").strip().lower()
    if normalized in {"ma5_v0_2", "ma5_trend_v0_2"}:
        return build_ma5_stop_plan(rows, reference_price=reference_price, as_of=as_of)
    if normalized == "manual":
        return build_manual_stop_plan(rows, reference_price=reference_price, as_of=as_of)
    if normalized == "e7_oversold_overnight":
        return build_e7_stop_plan(
            rows,
            reference_price=reference_price,
            stabilization_low=stabilization_low,
            as_of=as_of,
        )
    raise StopPlanError(f"策略 {normalized} 不支持自动生成止损位")


@dataclass(frozen=True)
class ScenarioStopInput:
    """E1–E7 已确认的结构事实；未出现的结构不得用均线猜测补齐。"""

    scenario: EntryScenario
    entry_price: float
    atr14: float
    early_session_low: float | None = None
    pullback_swing_low: float | None = None
    range_lower: float | None = None
    structure_low: float | None = None
    breakout_level: float | None = None
    breakout_bar_low: float | None = None
    weak_to_strong_low: float | None = None
    high_open_consolidation_low: float | None = None
    stabilization_low: float | None = None


@dataclass(frozen=True)
class ScenarioStopPlan:
    rule_version: str
    scenario: EntryScenario
    entry_price: float
    structural_anchor: float
    structural_source: str
    atr14: float
    buffer_atr_factor: float
    buffer_amount: float
    hard_stop: float
    selection_rule: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scenario"] = self.scenario.value
        return payload


def _required_structure(value: Any, *, label: str) -> float:
    number = _positive(value)
    if number is None:
        raise StopPlanError(f"场景止损缺少{label}")
    return number


def _highest_below_entry(
    candidates: list[tuple[Any, str]],
    *,
    entry_price: float,
    label: str,
) -> tuple[float, str]:
    usable = [
        (number, source)
        for value, source in candidates
        if (number := _positive(value)) is not None and number < entry_price
    ]
    if not usable:
        raise StopPlanError(f"{label}没有低于入场价的有效结构档位")
    return max(usable, key=lambda item: item[0])


def build_scenario_stop_plan(
    inputs: ScenarioStopInput,
    *,
    rules: ExecutionLevelConfig = DEFAULT_EXECUTION_LEVEL_RULES,
) -> ScenarioStopPlan:
    """按 E1–E7 的失效结构只减一次 ATR 缓冲。"""

    entry = _required_structure(inputs.entry_price, label="入场价")
    atr14 = _required_structure(inputs.atr14, label="ATR14")
    try:
        scenario = (
            inputs.scenario
            if isinstance(inputs.scenario, EntryScenario)
            else EntryScenario(inputs.scenario)
        )
    except ValueError as error:
        raise StopPlanError(f"未知执行场景：{inputs.scenario}") from error

    if scenario is EntryScenario.E1_LOW_OPEN_RECOVERY:
        anchor = _required_structure(inputs.early_session_low, label="早盘低点")
        source = "早盘低点"
        selection_rule = "早盘低点是低开修复失效结构。"
    elif scenario is EntryScenario.E2_PULLBACK_RECLAIM:
        anchor = _required_structure(inputs.pullback_swing_low, label="本次回踩波段低点")
        source = "本次回踩波段低点"
        selection_rule = "只使用本次回踩结构低点，不用无关 MA8 自动收紧止损。"
    elif scenario is EntryScenario.E3_RANGE_SUPPORT_REVERSAL:
        range_lower = _positive(inputs.range_lower)
        structure_low = _positive(inputs.structure_low)
        eligible: list[tuple[float, str]] = []
        if range_lower is not None:
            eligible.append((range_lower, "区间下沿"))
        if structure_low is not None and (
            range_lower is None or structure_low <= range_lower
        ):
            eligible.append((structure_low, "区间内确认结构低点"))
        if not eligible:
            raise StopPlanError("场景止损缺少区间下沿或区间内确认结构低点")
        anchor, source = max(eligible, key=lambda item: item[0])
        selection_rule = "锚点不得高于实际区间支撑，不用更紧的无关均线。"
    elif scenario is EntryScenario.E4_BREAKOUT_CONTINUATION:
        anchor, source = _highest_below_entry(
            [
                (inputs.breakout_level, "突破位"),
                (inputs.breakout_bar_low, "突破K低点"),
            ],
            entry_price=entry,
            label="E4 突破止损",
        )
        selection_rule = "取突破位与突破K低点中较高且低于入场价的一档。"
    elif scenario is EntryScenario.E5_WEAK_TO_STRONG:
        anchor = _required_structure(inputs.weak_to_strong_low, label="转强前低点")
        source = "当日转强前低点"
        selection_rule = "转强前低点再次跌破即判定弱转强失败。"
    elif scenario is EntryScenario.E6_HIGH_OPEN_STRONG_BREAKOUT:
        anchor, source = _highest_below_entry(
            [
                (inputs.high_open_consolidation_low, "高开整理区低点"),
                (inputs.breakout_level, "突破位"),
            ],
            entry_price=entry,
            label="E6 高开突破止损",
        )
        selection_rule = "取高开整理区低点与突破位中较高且低于入场价的一档。"
    elif scenario is EntryScenario.E7_OVERSOLD_REBOUND_OVERNIGHT:
        anchor = _required_structure(inputs.stabilization_low, label="尾盘企稳低点")
        source = "T-1 尾盘企稳低点"
        selection_rule = "隔夜反弹使用更紧的企稳低点结构，不允许迁移为趋势止损。"
    else:  # pragma: no cover - EntryScenario 已封闭，保留防御分支
        raise StopPlanError(f"未知执行场景：{scenario}")

    if anchor >= entry:
        raise StopPlanError(f"{source}必须低于入场价")
    factor = (
        rules.e7_stop_atr
        if scenario is EntryScenario.E7_OVERSOLD_REBOUND_OVERNIGHT
        else rules.base_noise_atr
    )
    buffer_amount = factor * atr14
    hard_stop = anchor - buffer_amount
    if hard_stop <= 0:
        raise StopPlanError("ATR 缓冲后的场景止损必须大于 0")
    return ScenarioStopPlan(
        rule_version=rules.version,
        scenario=scenario,
        entry_price=round(entry, 4),
        structural_anchor=round(anchor, 4),
        structural_source=source,
        atr14=round(atr14, 4),
        buffer_atr_factor=factor,
        buffer_amount=round(buffer_amount, 4),
        hard_stop=round(hard_stop, 4),
        selection_rule=selection_rule,
    )


@dataclass(frozen=True)
class HoldingStopComparison:
    existing_stop: float | None
    existing_stop_confirmed: bool
    scenario_suggested_stop: float
    effective_stop: float
    requires_user_confirmation: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare_holding_stop(
    *,
    existing_stop: float | None,
    existing_stop_confirmed: bool,
    scenario_suggested_stop: float,
) -> HoldingStopComparison:
    """已确认旧止损只做并列展示，不因新公式自动迁移。"""

    suggested = _required_structure(scenario_suggested_stop, label="场景建议止损")
    current = _positive(existing_stop)
    if existing_stop_confirmed and current is None:
        raise StopPlanError("已确认止损缺少有效价格")
    if existing_stop_confirmed:
        different = round(float(current), 4) != round(suggested, 4)
        return HoldingStopComparison(
            existing_stop=round(float(current), 4),
            existing_stop_confirmed=True,
            scenario_suggested_stop=round(suggested, 4),
            effective_stop=round(float(current), 4),
            requires_user_confirmation=different,
            message=(
                "保留用户已确认的现有止损；确认后才可切换到场景建议止损。"
                if different
                else "现有止损与场景建议止损一致。"
            ),
        )
    return HoldingStopComparison(
        existing_stop=round(current, 4) if current is not None else None,
        existing_stop_confirmed=False,
        scenario_suggested_stop=round(suggested, 4),
        effective_stop=round(suggested, 4),
        requires_user_confirmation=False,
        message="现有止损未确认，使用本计划冻结的场景建议止损。",
    )
