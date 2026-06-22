from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore


DEFAULT_HORIZONS = (1, 3, 5, 10)
DEFAULT_OUTPUT_DIR = Path("research/validation/strategies")


@dataclass(frozen=True)
class StrategyValidationRequest:
    start_date: str
    end_date: str
    exclude_st: bool = True
    horizons: tuple[int, ...] = DEFAULT_HORIZONS
    output_dir: Path = DEFAULT_OUTPUT_DIR
    min_promote_samples: int = 30


@dataclass(frozen=True)
class StrategyValidationResult:
    signal_rows: pd.DataFrame
    summary: pd.DataFrame
    summary_markdown_path: Path
    summary_csv_path: Path
    strategy_report_paths: dict[str, Path]


def normalize_date_text(value: str) -> str:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def normalize_horizons(values: Iterable[int] | None) -> tuple[int, ...]:
    horizons = tuple(sorted({int(value) for value in values or DEFAULT_HORIZONS if int(value) > 0}))
    return horizons or DEFAULT_HORIZONS


def load_strategy_records_from_mongo(
    store: MongoSnapshotStore,
    request: StrategyValidationRequest,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return store.find_market_snapshot_records_between(
        start_date=normalize_date_text(request.start_date),
        end_date=normalize_date_text(request.end_date),
        exclude_st=request.exclude_st,
        limit=limit,
    )


def expand_strategy_signals(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in records:
        for signal in _signals_for_record(record):
            strategy_id = str(signal.get("strategy_id") or "").strip()
            if not strategy_id:
                continue
            row = {
                "analysis_date": normalize_date_text(str(record.get("analysis_date") or record.get("date") or "")),
                "ticker": str(record.get("ticker") or ""),
                "name": record.get("name") or "",
                "rank": record.get("rank"),
                "strategy_id": strategy_id,
                "strategy_version": str(signal.get("strategy_version") or ""),
                "status": str(signal.get("status") or "unknown"),
                "factor_groups": _join_values(signal.get("factor_groups")),
                "matched_candidate_tags": _join_values(
                    signal.get("matched_candidate_tags") or record.get("candidate_tags")
                ),
                "validation_report_path": str(signal.get("validation_report_path") or ""),
            }
            row.update(_copy_validation_metrics(record))
            rows.append(row)
    return pd.DataFrame(rows)


def summarize_strategy_performance(
    signal_rows: pd.DataFrame,
    horizons: Iterable[int] | None = None,
    *,
    min_promote_samples: int = 30,
) -> pd.DataFrame:
    horizons = normalize_horizons(horizons)
    if signal_rows.empty:
        return pd.DataFrame(columns=_summary_columns(horizons))

    frame = signal_rows.copy()
    frame["strategy_version"] = frame["strategy_version"].fillna("").astype(str)
    frame["status"] = frame["status"].fillna("unknown").astype(str)

    summaries: list[dict[str, Any]] = []
    group_cols = ["strategy_id", "strategy_version", "status"]
    for (strategy_id, strategy_version, status), group in frame.groupby(group_cols, dropna=False):
        row: dict[str, Any] = {
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "status": status,
            "sample_count": int(len(group)),
            "date_count": int(group["analysis_date"].nunique()) if "analysis_date" in group else 0,
            "ticker_count": int(group["ticker"].nunique()) if "ticker" in group else 0,
        }
        for horizon in horizons:
            returns = _numeric_series(group, f"future_return_{horizon}d")
            excess = _numeric_series(group, f"excess_return_{horizon}d")
            outperform = _numeric_series(group, f"outperform_benchmark_{horizon}d")
            drawdown = _numeric_series(group, f"future_max_drawdown_{horizon}d")
            row[f"mean_return_{horizon}d"] = _rounded_mean(returns)
            row[f"median_return_{horizon}d"] = _rounded_median(returns)
            row[f"win_rate_{horizon}d"] = _rounded_rate(returns > 0) if not returns.empty else None
            row[f"mean_excess_return_{horizon}d"] = _rounded_mean(excess)
            row[f"outperform_rate_{horizon}d"] = _rounded_rate(outperform > 0) if not outperform.empty else None
            row[f"mean_max_drawdown_{horizon}d"] = _rounded_mean(drawdown)
        row["decision"] = classify_strategy(row, min_promote_samples=min_promote_samples)
        summaries.append(row)

    summary = pd.DataFrame(summaries)
    sort_cols = ["status", "sample_count", "strategy_id"]
    return summary.sort_values(sort_cols, ascending=[True, False, True]).reset_index(drop=True)


def classify_strategy(row: dict[str, Any], *, min_promote_samples: int = 30, primary_horizon: int = 5) -> str:
    strategy_id = str(row.get("strategy_id") or "")
    if strategy_id == "shilun_v1":
        return "baseline_reference"

    sample_count = int(row.get("sample_count") or 0)
    if sample_count < min_promote_samples:
        return "testing_insufficient_samples"

    mean_excess = _optional_float(row.get(f"mean_excess_return_{primary_horizon}d"))
    outperform_rate = _optional_float(row.get(f"outperform_rate_{primary_horizon}d"))
    mean_drawdown = _optional_float(row.get(f"mean_max_drawdown_{primary_horizon}d"))
    if mean_excess is None or outperform_rate is None:
        return "testing_missing_labels"
    if mean_excess > 0 and outperform_rate >= 0.52 and (mean_drawdown is None or mean_drawdown >= -0.08):
        return "promote_candidate"
    if mean_excess < -0.02 and outperform_rate <= 0.45:
        return "retire_candidate"
    return "keep_testing"


def render_summary_markdown(summary: pd.DataFrame, request: StrategyValidationRequest) -> str:
    lines = [
        "# 策略收益验证汇总",
        "",
        f"- 验证区间: {normalize_date_text(request.start_date)} 至 {normalize_date_text(request.end_date)}",
        f"- 数据源: Mongo `market_snapshot_records`",
        f"- ST 过滤: {'排除 ST' if request.exclude_st else '包含 ST'}",
        f"- 观察窗口: {', '.join(f'{horizon}d' for horizon in normalize_horizons(request.horizons))}",
        "",
    ]
    if summary.empty:
        lines.extend(["暂无可验证策略信号。", ""])
        return "\n".join(lines)

    primary = 5 if 5 in normalize_horizons(request.horizons) else normalize_horizons(request.horizons)[0]
    table = summary[
        [
            "strategy_id",
            "strategy_version",
            "status",
            "sample_count",
            "date_count",
            "ticker_count",
            f"mean_return_{primary}d",
            f"mean_excess_return_{primary}d",
            f"win_rate_{primary}d",
            f"outperform_rate_{primary}d",
            f"mean_max_drawdown_{primary}d",
            "decision",
        ]
    ].copy()
    table.columns = [
        "策略ID",
        "版本",
        "状态",
        "样本",
        "日期",
        "股票",
        f"{primary}日均收益",
        f"{primary}日均超额",
        f"{primary}日胜率",
        f"{primary}日跑赢率",
        f"{primary}日均回撤",
        "验证结论",
    ]
    for column in table.columns:
        if any(token in column for token in ("收益", "超额", "胜率", "跑赢率", "回撤")):
            table[column] = table[column].map(_format_percent)
    lines.append(_render_markdown_table(table))
    lines.extend(
        [
            "",
            "## 门禁口径",
            "",
            "- `shilun_v1` 只作为基准对照，不因本报告自动升降级。",
            "- 测试策略样本不足时只保留观察，不进入排序、仓位或执行链。",
            "- 测试策略满足样本数、5 日超额收益、跑赢率和回撤约束后，才允许进入候选升级讨论。",
            "",
        ]
    )
    return "\n".join(lines)


def write_validation_reports(summary: pd.DataFrame, request: StrategyValidationRequest) -> tuple[Path, Path, dict[str, Path]]:
    output_dir = Path(request.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_markdown_path = output_dir / "strategy_validation_summary.md"
    summary_csv_path = output_dir / "strategy_validation_summary.csv"
    summary_markdown_path.write_text(render_summary_markdown(summary, request), encoding="utf-8")
    summary.to_csv(summary_csv_path, index=False)

    strategy_report_paths: dict[str, Path] = {}
    for row in summary.to_dict(orient="records"):
        strategy_id = str(row.get("strategy_id") or "")
        if not strategy_id:
            continue
        report_path = output_dir / f"{strategy_id}.md"
        report_path.write_text(_render_strategy_report(row, request), encoding="utf-8")
        strategy_report_paths[strategy_id] = report_path

    return summary_markdown_path, summary_csv_path, strategy_report_paths


def run_strategy_validation(
    request: StrategyValidationRequest,
    *,
    store: MongoSnapshotStore,
    limit: int | None = None,
) -> StrategyValidationResult:
    # 三期改进点：验证层只消费已沉淀的策略快照，不重新触发扫描或外部数据接口。
    records = load_strategy_records_from_mongo(store, request, limit=limit)
    signal_rows = expand_strategy_signals(records)
    summary = summarize_strategy_performance(
        signal_rows,
        request.horizons,
        min_promote_samples=request.min_promote_samples,
    )
    markdown_path, csv_path, report_paths = write_validation_reports(summary, request)
    return StrategyValidationResult(
        signal_rows=signal_rows,
        summary=summary,
        summary_markdown_path=markdown_path,
        summary_csv_path=csv_path,
        strategy_report_paths=report_paths,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate strategy-level returns from Mongo market_snapshot_records.")
    parser.add_argument("--start-date", required=True, help="Start analysis_date, e.g. 2026-03-01.")
    parser.add_argument("--end-date", required=True, help="End analysis_date, e.g. 2026-05-16.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Report output directory.")
    parser.add_argument("--horizons", default="1,3,5,10", help="Comma-separated forward return windows.")
    parser.add_argument("--include-st", action="store_true", help="Include ST records instead of the default exclude_st=True scope.")
    parser.add_argument("--min-promote-samples", type=int, default=30, help="Minimum samples before a testing strategy can be promoted.")
    parser.add_argument("--mongo-uri", default=None, help="Override SHILUN_MONGO_URI.")
    parser.add_argument("--mongo-db", default=None, help="Override SHILUN_MONGO_DB.")
    parser.add_argument("--limit", type=int, default=None, help="Optional record limit for debugging.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    mongo_uri = args.mongo_uri or config.mongo_uri
    mongo_db = args.mongo_db or config.mongo_db
    if not mongo_uri:
        raise ValueError("SHILUN_MONGO_URI is required for strategy validation.")

    horizons = tuple(int(item.strip()) for item in str(args.horizons).split(",") if item.strip())
    request = StrategyValidationRequest(
        start_date=args.start_date,
        end_date=args.end_date,
        exclude_st=not args.include_st,
        horizons=normalize_horizons(horizons),
        output_dir=Path(args.output_dir),
        min_promote_samples=args.min_promote_samples,
    )
    store = MongoSnapshotStore(mongo_uri, mongo_db)
    try:
        result = run_strategy_validation(request, store=store, limit=args.limit)
    finally:
        store.close()

    print(f"signal_rows={len(result.signal_rows)}")
    print(f"strategy_count={len(result.summary)}")
    print(f"summary_markdown_path={result.summary_markdown_path}")
    print(f"summary_csv_path={result.summary_csv_path}")


def _signals_for_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    signals = record.get("strategy_signals")
    if isinstance(signals, list) and signals:
        return [dict(signal) for signal in signals if isinstance(signal, dict)]

    strategy_ids = _split_values(record.get("strategy_ids"))
    versions = _version_lookup(record.get("strategy_versions"))
    paths = _split_values(record.get("strategy_validation_paths"))
    if strategy_ids:
        return [
            {
                "strategy_id": strategy_id,
                "strategy_version": versions.get(strategy_id, ""),
                "status": "unknown",
                "validation_report_path": paths[index] if index < len(paths) else "",
            }
            for index, strategy_id in enumerate(strategy_ids)
        ]

    # 兼容早期快照：没有策略字段时，仍可作为 shilun_v1 legacy 基准样本进入验证。
    return [{"strategy_id": "shilun_v1", "strategy_version": "legacy", "status": "baseline"}]


def _copy_validation_metrics(record: dict[str, Any]) -> dict[str, Any]:
    metric_prefixes = (
        "future_return_",
        "benchmark_future_return_",
        "excess_return_",
        "outperform_benchmark_",
        "future_max_runup_",
        "future_max_drawdown_",
    )
    return {key: value for key, value in record.items() if key.startswith(metric_prefixes)}


def _summary_columns(horizons: tuple[int, ...]) -> list[str]:
    columns = ["strategy_id", "strategy_version", "status", "sample_count", "date_count", "ticker_count"]
    for horizon in horizons:
        columns.extend(
            [
                f"mean_return_{horizon}d",
                f"median_return_{horizon}d",
                f"win_rate_{horizon}d",
                f"mean_excess_return_{horizon}d",
                f"outperform_rate_{horizon}d",
                f"mean_max_drawdown_{horizon}d",
            ]
        )
    columns.append("decision")
    return columns


def _split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _join_values(value: Any) -> str:
    return ",".join(_split_values(value))


def _version_lookup(value: Any) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for item in _split_values(value):
        if "@" in item:
            strategy_id, version = item.split("@", 1)
            lookup[strategy_id] = version
    return lookup


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").dropna()


def _rounded_mean(series: pd.Series) -> float | None:
    if series.empty:
        return None
    return round(float(series.mean()), 4)


def _rounded_median(series: pd.Series) -> float | None:
    if series.empty:
        return None
    return round(float(series.median()), 4)


def _rounded_rate(series: pd.Series) -> float | None:
    if series.empty:
        return None
    return round(float(series.mean()), 4)


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _format_percent(value: Any) -> str:
    numeric = _optional_float(value)
    if numeric is None:
        return "-"
    return f"{numeric:.2%}"


def _render_markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""
    columns = [str(column) for column in frame.columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame.to_dict(orient="records"):
        values = [str(row.get(column, "")) if row.get(column, "") is not None else "" for column in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _render_strategy_report(row: dict[str, Any], request: StrategyValidationRequest) -> str:
    strategy_id = str(row.get("strategy_id") or "")
    lines = [
        f"# {strategy_id} 策略收益验证",
        "",
        f"- 验证区间: {normalize_date_text(request.start_date)} 至 {normalize_date_text(request.end_date)}",
        "- 数据源: Mongo `market_snapshot_records`",
        f"- 样本数: {row.get('sample_count', 0)}",
        f"- 验证结论: {row.get('decision', '')}",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
    ]
    for key, value in row.items():
        if key in {"strategy_id", "strategy_version", "status", "decision"}:
            continue
        formatted = _format_percent(value) if any(token in key for token in ("return", "rate", "drawdown", "excess")) else value
        lines.append(f"| `{key}` | {formatted if formatted is not None else '-'} |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
