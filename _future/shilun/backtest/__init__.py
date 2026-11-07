"""Backtest adapters and lightweight signal policies."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd


_TS_TO_JQ_SUFFIX = {
    ".SH": ".XSHG",
    ".SZ": ".XSHE",
    ".BJ": ".XBEI",
}
_JQ_TO_TS_SUFFIX = {value: key for key, value in _TS_TO_JQ_SUFFIX.items()}


@dataclass(frozen=True)
class JoinQuantSignalPolicyConfig:
    max_positions: int = 5
    target_weight: float = 0.2
    trim_weight: float = 0.1
    min_structure_score: int = 60
    min_continue_probability: float = 0.55
    max_risk_score: int = 65


@dataclass(frozen=True)
class JoinQuantSignal:
    ticker: str
    jq_symbol: str
    conclusion_label: str
    opportunity_type: str
    entry_zone: str | None
    target_weight: float
    rank_score: float
    risk_score: int
    invalidation: float | None
    note: str


def to_joinquant_symbol(ts_code: str) -> str:
    base, suffix = _split_code(ts_code)
    try:
        return f"{base}{_TS_TO_JQ_SUFFIX[suffix]}"
    except KeyError as error:
        raise ValueError(f"Unsupported tushare ticker suffix: {ts_code}") from error


def to_tushare_symbol(jq_code: str) -> str:
    base, suffix = _split_code(jq_code)
    try:
        return f"{base}{_JQ_TO_TS_SUFFIX[suffix]}"
    except KeyError as error:
        raise ValueError(f"Unsupported JoinQuant ticker suffix: {jq_code}") from error


def normalize_joinquant_bars(frame: pd.DataFrame, security: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "volume", "amount"])

    bars = frame.copy()
    if isinstance(bars.columns, pd.MultiIndex):
        raise ValueError("normalize_joinquant_bars expects a single-security DataFrame, not a multi-index quote table.")

    if "date" not in bars.columns:
        index_name = bars.index.name or "index"
        bars = bars.reset_index().rename(columns={index_name: "date"})
    else:
        bars = bars.reset_index(drop=True)

    bars = bars.rename(columns={"money": "amount"})
    bars["ticker"] = to_tushare_symbol(security)
    required_columns = ["ticker", "date", "open", "high", "low", "close", "volume", "amount"]
    for column in required_columns:
        if column not in bars.columns:
            bars[column] = pd.NA
    return bars[required_columns]


def build_trade_signal(
    analysis: dict[str, Any],
    *,
    is_holding: bool,
    policy: JoinQuantSignalPolicyConfig | None = None,
) -> JoinQuantSignal:
    cfg = policy or JoinQuantSignalPolicyConfig()
    snapshot = analysis.get("snapshot", {})
    decision = analysis.get("decision", {})
    ticker = analysis.get("ticker") or snapshot.get("ticker")
    if not ticker:
        raise ValueError("analysis result does not include ticker")

    conclusion_label = decision.get("conclusion_label", "defense_first")
    structure_score = int(snapshot.get("structure_score", 0) or 0)
    continue_probability = float(snapshot.get("p_continue_10d", 0.0) or 0.0)
    entry_probability = float(snapshot.get("entry_probability", 0.0) or 0.0)
    fail_fast_probability = float(snapshot.get("p_fail_fast_3d", 0.0) or 0.0)
    risk_score = int(snapshot.get("risk_score", 100) or 100)
    opportunity_type = str(snapshot.get("opportunity_type", "observe") or "observe")
    entry_zone = snapshot.get("entry_zone")
    invalidation = snapshot.get("invalidation_level")
    target_weight = _resolve_target_weight(
        conclusion_label=conclusion_label,
        structure_score=structure_score,
        continue_probability=continue_probability,
        entry_probability=entry_probability,
        fail_fast_probability=fail_fast_probability,
        risk_score=risk_score,
        opportunity_type=opportunity_type,
        entry_zone=entry_zone,
        is_holding=is_holding,
        cfg=cfg,
    )
    rank_score = structure_score * 0.35 + entry_probability * 100.0 * 0.45 + continue_probability * 100.0 * 0.20 - risk_score - fail_fast_probability * 60.0
    note = (
        f"{conclusion_label} | opportunity={opportunity_type} | structure={structure_score} "
        f"| entry={entry_probability:.2f}({entry_zone}) | p_continue={continue_probability:.2f} "
        f"| p_fail_fast={fail_fast_probability:.2f} | risk={risk_score}"
    )
    return JoinQuantSignal(
        ticker=ticker,
        jq_symbol=to_joinquant_symbol(ticker),
        conclusion_label=conclusion_label,
        opportunity_type=opportunity_type,
        entry_zone=entry_zone,
        target_weight=target_weight,
        rank_score=rank_score,
        risk_score=risk_score,
        invalidation=invalidation,
        note=note,
    )


def build_target_weights(
    signals: Iterable[JoinQuantSignal],
    policy: JoinQuantSignalPolicyConfig | None = None,
) -> dict[str, float]:
    cfg = policy or JoinQuantSignalPolicyConfig()
    candidates = [signal for signal in signals if signal.target_weight > 0]
    ranked = sorted(candidates, key=lambda item: (item.target_weight, item.rank_score), reverse=True)
    selected = ranked[: cfg.max_positions]
    return {signal.jq_symbol: signal.target_weight for signal in selected}


def _resolve_target_weight(
    *,
    conclusion_label: str,
    structure_score: int,
    continue_probability: float,
    entry_probability: float,
    fail_fast_probability: float,
    risk_score: int,
    opportunity_type: str,
    entry_zone: str | None,
    is_holding: bool,
    cfg: JoinQuantSignalPolicyConfig,
) -> float:
    if opportunity_type == "reject":
        return 0.0
    if risk_score > cfg.max_risk_score:
        return 0.0
    if structure_score < cfg.min_structure_score:
        return cfg.trim_weight if is_holding and conclusion_label != "defense_first" else 0.0
    if entry_zone == "avoid":
        return 0.0
    if entry_probability < 0.5 and not is_holding:
        return 0.0
    if continue_probability < cfg.min_continue_probability and conclusion_label == "high_quality_continuation":
        return cfg.trim_weight if is_holding else 0.0
    if fail_fast_probability >= 0.45:
        return cfg.trim_weight if is_holding else 0.0

    if conclusion_label == "high_quality_continuation":
        return cfg.target_weight
    if conclusion_label in {"confirmation_needed", "momentum_but_heavy_overhead"}:
        return cfg.trim_weight if is_holding else 0.0
    return 0.0


def _split_code(code: str) -> tuple[str, str]:
    parts = code.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid security code: {code}")
    return parts[0], f".{parts[1].upper()}"


__all__ = [
    "JoinQuantSignal",
    "JoinQuantSignalPolicyConfig",
    "build_target_weights",
    "build_trade_signal",
    "normalize_joinquant_bars",
    "to_joinquant_symbol",
    "to_tushare_symbol",
]


for _module_name in ("joinquant_adapter", "joinquant_policy"):
    sys.modules[f"{__name__}.{_module_name}"] = sys.modules[__name__]
