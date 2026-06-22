from __future__ import annotations

from typing import Any

import pandas as pd

from shilun.rules import (
    RuleContext,
    build_default_candidate_tag_registry,
    format_candidate_tag_reasons,
    format_candidate_tags,
)
from shilun.strategies import (
    StrategyContext,
    build_default_strategy_registry,
    format_strategy_ids,
    format_strategy_signal_reasons,
    format_strategy_validation_paths,
    format_strategy_versions,
)


CANDIDATE_TAG_REGISTRY = build_default_candidate_tag_registry()
STRATEGY_REGISTRY = build_default_strategy_registry()


def build_snapshot_record(
    *,
    ticker: str,
    analysis: dict[str, Any],
    latest_bar: pd.Series,
    metadata: dict[str, Any],
) -> dict[str, object]:
    snapshot = analysis["snapshot"]
    decision = analysis["decision"]
    entry_probability = float(snapshot.get("entry_probability", 0.0) or 0.0)
    p_acceptance_1d = float(snapshot.get("p_acceptance_1d", 0.0) or 0.0)
    p_fail_fast_3d = float(snapshot.get("p_fail_fast_3d", 0.0) or 0.0)
    p_continue_10d = float(snapshot.get("p_continue_10d", 0.0) or 0.0)
    p_breakout_success = float(snapshot.get("p_breakout_success", 0.0) or 0.0)
    expected_return_10d = float(snapshot.get("expected_return_10d", 0.0) or 0.0)
    risk_score = int(snapshot.get("risk_score", 0) or 0)
    execution_score = float(snapshot.get("execution_score", 0.0) or 0.0)
    target_position_pct = int(snapshot.get("target_position_pct", 0) or 0)
    overhang_ratio = float((snapshot.get("chip_context") or {}).get("overhang_ratio", 0.0) or 0.0)
    gentle_expand_score = float(snapshot.get("gentle_expand_score", 0.0) or 0.0)
    pullback_shrink_score = float(snapshot.get("pullback_shrink_score", 0.0) or 0.0)
    distribution_score = float(snapshot.get("distribution_score", 0.0) or 0.0)
    stall_score = float(snapshot.get("stall_score", 0.0) or 0.0)
    early_stage_score = float(snapshot.get("early_stage_score", 0.0) or 0.0)
    mid_stage_score = float(snapshot.get("mid_stage_score", 0.0) or 0.0)
    late_stage_score = float(snapshot.get("late_stage_score", 0.0) or 0.0)
    # 三期改进点：候选标签只作为可验证观察字段输出，暂不改变当前榜单排序。
    candidate_tags = CANDIDATE_TAG_REGISTRY.evaluate(
        RuleContext(ticker=ticker, snapshot=snapshot, latest_bar=latest_bar, metadata=metadata)
    )
    record = {
        "ticker": ticker,
        "name": metadata.get("name"),
        "industry": metadata.get("industry"),
        "market": metadata.get("market"),
        "close": round(float(latest_bar["close"]), 4),
        "conclusion_label": decision.get("conclusion_label"),
        "action_label": snapshot.get("action_label"),
        "entry_style": snapshot.get("entry_style"),
        "target_position_pct": target_position_pct,
        "opportunity_type": snapshot.get("opportunity_type"),
        "trigger_state": snapshot.get("trigger_state"),
        "trend_stage": snapshot.get("trend_stage"),
        "entry_zone": snapshot.get("entry_zone"),
        "structure_score": int(snapshot.get("structure_score", 0) or 0),
        "entry_probability": round(entry_probability, 4),
        "p_acceptance_1d": round(p_acceptance_1d, 4),
        "p_fail_fast_3d": round(p_fail_fast_3d, 4),
        "p_continue_10d": round(p_continue_10d, 4),
        "p_breakout_success": round(p_breakout_success, 4),
        "expected_return_10d": round(expected_return_10d, 4),
        "risk_score": risk_score,
        "risk_level": round(float(snapshot.get("risk_level", 0.0) or 0.0), 4),
        "execution_score": round(execution_score, 4),
        "execution_risk_score": round(float(snapshot.get("execution_risk_score", 0.0) or 0.0), 4),
        "gentle_expand_score": round(gentle_expand_score, 4),
        "pullback_shrink_score": round(pullback_shrink_score, 4),
        "distribution_score": round(distribution_score, 4),
        "stall_score": round(stall_score, 4),
        "early_stage_score": round(early_stage_score, 4),
        "mid_stage_score": round(mid_stage_score, 4),
        "late_stage_score": round(late_stage_score, 4),
        "market_trend_score": round(float((snapshot.get("market_context") or {}).get("market_trend_score", 0.0) or 0.0), 4),
        "sector_trend_score": round(float((snapshot.get("sector_context") or {}).get("sector_trend_score", 0.0) or 0.0), 4),
        "sector_strength_score": round(float((snapshot.get("sector_context") or {}).get("sector_strength_score", 0.0) or 0.0), 4),
        "fundamental_score": round(float((snapshot.get("fundamental_context") or {}).get("fundamental_score", 0.0) or 0.0), 4),
        "overhang_ratio": round(overhang_ratio, 4),
        "candidate_tags": format_candidate_tags(candidate_tags),
        "candidate_tag_count": len(candidate_tags),
        "candidate_tag_reasons": format_candidate_tag_reasons(candidate_tags),
        "decision_priority": decision_priority(decision.get("conclusion_label")),
        "entry_zone_priority": entry_zone_priority(snapshot.get("entry_zone")),
        "opportunity_priority": opportunity_priority(snapshot.get("opportunity_type")),
        "anti_fail_fast": round(1.0 - p_fail_fast_3d, 4),
    }
    # 三期改进点：策略层显式记录 strategy_id/version，候选标签只通过测试策略参与收益验证。
    strategy_signals = STRATEGY_REGISTRY.evaluate(StrategyContext(ticker=ticker, record=record))
    record.update(
        {
            "strategy_ids": format_strategy_ids(strategy_signals),
            "strategy_versions": format_strategy_versions(strategy_signals),
            "strategy_signal_count": len(strategy_signals),
            "strategy_signal_reasons": format_strategy_signal_reasons(strategy_signals),
            "strategy_validation_paths": format_strategy_validation_paths(strategy_signals),
            "strategy_signals": [signal.to_record() for signal in strategy_signals],
        }
    )
    return record


def rank_snapshot_records(records: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(records).sort_values(
        by=[
            "execution_score",
            "target_position_pct",
            "mid_stage_score",
            "gentle_expand_score",
            "pullback_shrink_score",
            "anti_fail_fast",
            "market_trend_score",
            "sector_trend_score",
            "fundamental_score",
            "early_stage_score",
            "entry_probability",
            "p_acceptance_1d",
            "p_continue_10d",
            "late_stage_score",
            "distribution_score",
            "stall_score",
            "risk_score",
            "overhang_ratio",
            "structure_score",
        ],
        ascending=[
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            True,
            True,
            True,
            True,
            False,
        ],
    ).reset_index(drop=True)


def build_output_table(top_rows: pd.DataFrame) -> pd.DataFrame:
    display = top_rows.copy().reset_index(drop=True)
    display.insert(0, "排名", range(1, len(display) + 1))
    display["执行分"] = display["execution_score"].map(lambda value: f"{value:.2f}")
    display["仓位"] = display["target_position_pct"].map(lambda value: f"{int(value)}%")
    display["入场概率"] = display["entry_probability"].map(lambda value: f"{value:.2%}")
    display["次日承接"] = display["p_acceptance_1d"].map(lambda value: f"{value:.2%}")
    display["快速失败"] = display["p_fail_fast_3d"].map(lambda value: f"{value:.2%}")
    display["10日延续概率"] = display["p_continue_10d"].map(lambda value: f"{value:.2%}")
    display["突破成功率"] = display["p_breakout_success"].map(lambda value: f"{value:.2%}")
    display["10日预期收益"] = display["expected_return_10d"].map(lambda value: f"{value:.2%}")
    display["模型风险"] = display["risk_level"].map(lambda value: f"{value:.2f}")
    display["收盘价"] = display["close"].map(lambda value: f"{value:.2f}")
    display["市场分"] = display["market_trend_score"].map(lambda value: f"{value:.1f}")
    display["板块分"] = display["sector_trend_score"].map(lambda value: f"{value:.1f}")
    display["基本面分"] = display["fundamental_score"].map(lambda value: f"{value:.1f}")
    display["Early分"] = display["early_stage_score"].map(lambda value: f"{value:.1f}")
    display["Mid分"] = display["mid_stage_score"].map(lambda value: f"{value:.1f}")
    display["Late分"] = display["late_stage_score"].map(lambda value: f"{value:.1f}")
    display["候选标签"] = display["candidate_tags"].fillna("").astype(str)
    display["策略ID"] = display["strategy_ids"].fillna("").astype(str)
    return display[
        [
            "排名",
            "ticker",
            "name",
            "industry",
            "market",
            "收盘价",
            "conclusion_label",
            "action_label",
            "trend_stage",
            "候选标签",
            "策略ID",
            "执行分",
            "仓位",
            "opportunity_type",
            "entry_zone",
            "入场概率",
            "次日承接",
            "快速失败",
            "Early分",
            "Mid分",
            "Late分",
            "市场分",
            "板块分",
            "基本面分",
            "structure_score",
            "10日延续概率",
            "突破成功率",
            "10日预期收益",
            "risk_score",
            "模型风险",
        ]
    ].rename(
        columns={
            "ticker": "股票代码",
            "name": "股票名称",
            "industry": "行业",
            "market": "市场板块",
            "conclusion_label": "结论标签",
            "action_label": "动作标签",
            "trend_stage": "趋势阶段",
            "opportunity_type": "机会类型",
            "entry_zone": "入场分区",
            "structure_score": "结构分",
            "risk_score": "风险分",
        }
    )


def render_markdown(
    *,
    analysis_date: str,
    scanned_count: int,
    skipped_count: int,
    table: pd.DataFrame,
    exclude_st: bool,
) -> str:
    title_suffix = "（已过滤 ST/*ST）" if exclude_st else ""
    header = [
        f"# {analysis_date} 全市场前 {len(table)} 名{title_suffix}",
        "",
        f"- 扫描成功: {scanned_count}",
        f"- 跳过数量: {skipped_count}",
        "- 排序方式: `执行分 -> 仓位 -> Mid分 -> 温和放量/缩量回调 -> 快速失败倒序 -> 市场分 -> 板块分 -> 基本面分 -> Early分 -> 入场概率 -> 次日承接 -> 延续概率 -> Late分/分布/滞涨惩罚 -> 风险`",
        "- 候选标签: 三期观察字段，仅说明策略来源与验证方向，当前不参与排序",
        "- 策略层: `shilun_v1` 为当前基准策略，其余 `_test` 策略用于收益验证，当前不参与排序",
        "",
    ]
    return "\n".join(header + markdown_table(table))


def decision_priority(label: str | None) -> int:
    mapping = {
        "high_quality_continuation": 4,
        "confirmation_needed": 3,
        "momentum_but_heavy_overhead": 2,
        "defense_first": 1,
    }
    return mapping.get(label or "", 0)


def entry_zone_priority(zone: str | None) -> int:
    mapping = {
        "ready": 4,
        "candidate": 3,
        "watch": 2,
        "avoid": 1,
    }
    return mapping.get(zone or "", 0)


def opportunity_priority(opportunity_type: str | None) -> int:
    mapping = {
        "trend_follow": 3,
        "first_buy": 2,
        "observe": 1,
        "reject": 0,
    }
    return mapping.get(opportunity_type or "", 0)


def markdown_table(table: pd.DataFrame) -> list[str]:
    if table.empty:
        return ["暂无数据"]

    headers = [str(column) for column in table.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in table.itertuples(index=False, name=None):
        values = [str(value) if value is not None else "" for value in row]
        lines.append("| " + " | ".join(values) + " |")
    return lines
