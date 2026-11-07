#!/usr/bin/env python3
"""Build the canonical Data Analytics report artifact from analysis outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "output" / "sse_big_up_intraday_20260605_20260904"
SQL_PATH = ROOT / "research" / "sse_big_up_intraday" / "report_queries.sql"


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def load_csv(directory: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(directory / f"{name}.csv")


def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    clean = frame.replace({np.nan: None})
    return clean.to_dict(orient="records")


def iso_trade_date(value: Any) -> str:
    text = str(int(value)) if isinstance(value, (int, float, np.integer, np.floating)) else str(value)
    text = text.replace("-", "")
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def main() -> int:
    options = args()
    directory = options.output_dir.resolve()
    summary = json.loads((directory / "analysis_summary.json").read_text(encoding="utf-8"))
    individual = load_csv(directory, "chart_individual_paths")
    aggregate = load_csv(directory, "chart_aggregate_paths")
    events = load_csv(directory, "event_metrics")
    sensitivity = load_csv(directory, "threshold_sensitivity")
    common = load_csv(directory, "common_features")
    path_types = load_csv(directory, "path_type_counts")
    quality = load_csv(directory, "minute_data_quality")

    individual["trade_date"] = individual["trade_date"].map(iso_trade_date)
    events["trade_date"] = events["trade_date"].map(iso_trade_date)
    quality["trade_date"] = quality["trade_date"].map(iso_trade_date)
    event_columns = [
        "trade_date",
        "path_type",
        "daily_return",
        "body_return",
        "gap_return",
        "first_30_contribution",
        "morning_contribution",
        "afternoon_contribution",
        "last_30_contribution",
        "max_drawdown",
        "close_location",
        "above_open_share",
        "peak_time",
        "trough_time",
    ]
    sensitivity_columns = [
        "threshold_label",
        "sample_count",
        "median_morning_contribution",
        "median_last_30_contribution",
        "close_high_share",
        "tail_positive_share",
        "trough_in_morning_share",
        "peak_after_1445_share",
        "afternoon_dominant_share",
        "median_pairwise_path_correlation",
    ]
    common = common.sort_values(["meets_70pct_rule", "share"], ascending=[False, False])
    quality_primary = quality.loc[quality["trade_date"].isin(events["trade_date"])]
    primary_anchor_max = float(quality_primary["pre_anchor_vs_close_bp"].abs().max())

    source_id = "sse_big_up_intraday_sql"
    generated_at = summary["generated_at"]
    source_short = {
        "id": source_id,
        "label": "上证指数日线与1分钟线研究底稿",
        "path": "research/sse_big_up_intraday/report_queries.sql",
    }
    source_full = {
        **source_short,
        "query": {
            "engine": "sqlite",
            "sql": SQL_PATH.read_text(encoding="utf-8"),
            "description": "从可复算 SQLite 底稿提取逐日分时路径、事件指标、阈值敏感性与数据质量结果。",
            "tables_used": [
                "chart_individual_paths",
                "chart_aggregate_paths",
                "event_metrics",
                "threshold_sensitivity",
                "common_features",
                "local_daily_comparison",
            ],
            "filters": [
                "上证指数 000001.SH",
                "2026-06-05 至 2026-09-04",
                "主口径为较前收涨幅不低于1%且收盘高于开盘",
                "分钟图每5分钟抽样展示，统计使用完整1分钟路径",
            ],
            "metric_definitions": [
                "daily_return = daily_close / previous_close - 1",
                "body_return = daily_close / daily_open - 1",
                "normalized_body = (minute_close / daily_open - 1) / body_return",
                "morning_contribution = 11:30相对开盘涨幅 / body_return",
                "last_30_contribution = 14:30至15:00涨幅 / body_return",
                "close_location = (close - low) / (high - low)",
            ],
        },
    }

    manifest: dict[str, Any] = {
        "version": 1,
        "surface": "report",
        "title": "近三个月上证指数大阳日分时形态",
        "generatedAt": generated_at,
        "sources": [source_short],
        "charts": [
            {
                "id": "individual_paths",
                "title": "13个大阳日：相对前收分时路径",
                "type": "line",
                "dataset": "individual_paths",
                "sourceId": source_id,
                "encodings": {
                    "x": {
                        "field": "minute_index",
                        "type": "quantitative",
                        "label": "交易分钟（0=09:30，120=11:30，240=15:00）",
                    },
                    "y": {
                        "field": "return_vs_prev_close",
                        "type": "quantitative",
                        "label": "相对前收涨幅",
                        "format": "percent",
                    },
                    "color": {"field": "trade_date", "type": "nominal", "label": "大阳日"},
                    "tooltip": [
                        {"field": "trade_date", "type": "text", "label": "日期"},
                        {"field": "minute_label", "type": "text", "label": "北京时间"},
                        {
                            "field": "return_vs_prev_close",
                            "type": "quantitative",
                            "label": "相对前收",
                            "format": "percent",
                        },
                    ],
                },
                "valueFormat": "percent",
            },
            {
                "id": "aggregate_paths",
                "title": "阳线实体完成进度：中位数与25%-75%分位",
                "type": "line",
                "dataset": "aggregate_paths",
                "sourceId": source_id,
                "encodings": {
                    "x": {
                        "field": "minute_index",
                        "type": "quantitative",
                        "label": "交易分钟（0=09:30，120=11:30，240=15:00）",
                    },
                    "y": {
                        "field": "normalized_body",
                        "type": "quantitative",
                        "label": "最终阳线实体完成度",
                        "format": "percent",
                    },
                    "color": {"field": "statistic", "type": "nominal", "label": "统计量"},
                    "tooltip": [
                        {"field": "minute_label", "type": "text", "label": "北京时间"},
                        {"field": "statistic", "type": "text", "label": "统计量"},
                        {
                            "field": "normalized_body",
                            "type": "quantitative",
                            "label": "实体完成度",
                            "format": "percent",
                        },
                    ],
                },
                "valueFormat": "percent",
            },
        ],
        "tables": [
            {
                "id": "common_features",
                "title": "共同特征通过率",
                "dataset": "common_features",
                "sourceId": source_id,
                "defaultSort": {"field": "share", "direction": "desc"},
                "columns": [
                    {"field": "feature", "label": "候选共同点"},
                    {"field": "matching_days", "label": "满足天数"},
                    {"field": "sample_count", "label": "样本数"},
                    {"field": "share", "label": "占比", "format": "percent"},
                    {"field": "meets_70pct_rule", "label": "达到70%规则"},
                ],
            },
            {
                "id": "event_details",
                "title": "13个大阳日逐日指标",
                "dataset": "event_details",
                "sourceId": source_id,
                "defaultSort": {"field": "trade_date", "direction": "asc"},
                "columns": [
                    {"field": "trade_date", "label": "日期", "type": "date"},
                    {"field": "path_type", "label": "路径类型"},
                    {"field": "daily_return", "label": "较前收", "format": "percent"},
                    {"field": "body_return", "label": "阳线实体", "format": "percent"},
                    {"field": "gap_return", "label": "开盘缺口", "format": "percent"},
                    {"field": "morning_contribution", "label": "上午贡献", "format": "percent"},
                    {"field": "afternoon_contribution", "label": "午后贡献", "format": "percent"},
                    {"field": "last_30_contribution", "label": "尾盘30分贡献", "format": "percent"},
                    {"field": "max_drawdown", "label": "最大回撤", "format": "percent"},
                    {"field": "close_location", "label": "收盘位置", "format": "percent"},
                    {"field": "peak_time", "label": "高点时间"},
                    {"field": "trough_time", "label": "低点时间"},
                ],
            },
            {
                "id": "sensitivity",
                "title": "大阳阈值敏感性",
                "dataset": "sensitivity",
                "sourceId": source_id,
                "defaultSort": {"field": "sample_count", "direction": "desc"},
                "columns": [
                    {"field": "threshold_label", "label": "口径"},
                    {"field": "sample_count", "label": "样本数"},
                    {"field": "median_morning_contribution", "label": "上午中位贡献", "format": "percent"},
                    {"field": "median_last_30_contribution", "label": "尾盘中位贡献", "format": "percent"},
                    {"field": "close_high_share", "label": "强势收盘占比", "format": "percent"},
                    {"field": "tail_positive_share", "label": "尾盘上涨占比", "format": "percent"},
                    {"field": "trough_in_morning_share", "label": "上午见低占比", "format": "percent"},
                    {"field": "peak_after_1445_share", "label": "14:45后见高占比", "format": "percent"},
                    {"field": "median_pairwise_path_correlation", "label": "路径相关中位数"},
                ],
            },
        ],
        "blocks": [
            {"id": "title", "type": "markdown", "body": "# 近三个月上证指数大阳日分时形态"},
            {
                "id": "summary",
                "type": "markdown",
                "sourceId": source_id,
                "body": (
                    "## Executive Summary\n\n"
                    "- **核心共性在午后和尾盘，不在早盘。** 13天中有8天（61.5%）由午后完成主要阳线实体；上午中位只完成35.0%，午后完成65.0%，最后30分钟单独贡献28.8%。\n"
                    "- **低点早、高点晚、强势收盘最稳定。** 13/13天全天低点在上午，12/13天最后30分钟继续上涨，11/13天全天高点出现在14:45以后，12/13天收在全天振幅上部30%。\n"
                    "- **没有统一的高开或早盘急拉模板。** 高开仅7/13天，前30分钟盘中上涨也仅7/13天；中位路径在09:35反而低于开盘0.11%，到10:00只高于开盘0.03%。\n"
                    "- **形状有中等偏高一致性，但仍有例外。** 归一化路径两两相关系数中位数为0.665；2026-06-12是唯一明显的“高开早冲后回落”，也是唯一尾盘走弱且未强势收盘的样本。"
                ),
            },
            {
                "id": "scope",
                "type": "markdown",
                "sourceId": source_id,
                "body": (
                    "## 口径与样本\n\n"
                    "区间为2026-06-05至2026-09-04，共65个已完成交易日。主口径定义为上证指数当日较前收涨幅不低于1%，同时收盘高于开盘，共识别13天。所有发言时间与分钟标签均为北京时间（Asia/Shanghai）。统计使用完整1分钟路径；图表为提高可读性按5分钟锚点展示。"
                ),
            },
            {"id": "individual_chart", "type": "chart", "chartId": "individual_paths"},
            {
                "id": "shape_read",
                "type": "markdown",
                "sourceId": source_id,
                "body": (
                    "## 分时共同形态\n\n"
                    "相对前收的中位路径是：09:30约+0.07%，11:30约+0.51%，14:30约+1.18%，15:00收于+1.36%。这说明多数大阳日不是开盘一次性把涨幅打满，而是上午确认低点后，午后逐段抬升，最后半小时继续扩张。"
                ),
            },
            {"id": "aggregate_chart", "type": "chart", "chartId": "aggregate_paths"},
            {
                "id": "progress_read",
                "type": "markdown",
                "sourceId": source_id,
                "body": (
                    "## 阳线实体如何形成\n\n"
                    "按最终阳线实体归一化后，中位完成度依次为：09:35 -8.3%、10:00 2.2%、11:30 35.0%、13:30 51.1%、14:00 63.0%、14:30 71.2%、收盘100%。因此，早盘回踩并不罕见；真正高一致性的信号是午后接力与尾盘收高。"
                ),
            },
            {"id": "common_table", "type": "table", "tableId": "common_features"},
            {
                "id": "types",
                "type": "markdown",
                "sourceId": source_id,
                "body": (
                    "## 三类路径与反例\n\n"
                    "按上午/午后实体贡献划分：午后主导8天（61.5%）；上午先建立优势、午后续涨4天（30.8%）；早强后回落1天（7.7%）。6月12日的日涨幅主要来自0.77%的高开，上午一度远超最终实体，午后回吐，收盘只位于全天振幅的44.8%，所以它不代表主路径。"
                ),
            },
            {"id": "events_table", "type": "table", "tableId": "event_details"},
            {
                "id": "robustness",
                "type": "markdown",
                "sourceId": source_id,
                "body": (
                    "## 阈值敏感性\n\n"
                    "把阈值从0.8%提高到1.5%后，上午见低、尾盘上涨和强势收盘仍持续存在；阈值越高，午后主导越明显。1.25%口径剩8天，强势收盘与尾盘上涨均为100%，上午中位只完成10.5%，路径相关中位数0.719。1.5%口径仅4天，方向一致但样本太少，不宜单独外推。"
                ),
            },
            {"id": "sensitivity_table", "type": "table", "tableId": "sensitivity"},
            {
                "id": "next_steps",
                "type": "markdown",
                "sourceId": source_id,
                "body": (
                    "## 建议的下一步\n\n"
                    "1. 把窗口扩到至少1年，并留出最近3个月作为样本外验证。\n"
                    "2. 加入成交额、市场广度和当日行业贡献，判断“午后主导”是否只出现在某些市场状态。\n"
                    "3. 建立非大阳日对照组，检验“上午见低、尾盘走强”在普通上涨日的误报率；没有对照组前，不应把本报告直接改造成盘中信号。"
                ),
            },
            {
                "id": "questions",
                "type": "markdown",
                "sourceId": source_id,
                "body": (
                    "## 仍待验证的问题\n\n"
                    "午后主导是否由权重板块拉升造成？上午低点形成后需要满足哪些量价条件，才更可能演化为尾盘强收？6月12日这类高开早冲样本，在更长历史中出现频率和次日表现如何？"
                ),
            },
            {
                "id": "caveats",
                "type": "markdown",
                "sourceId": source_id,
                "body": (
                    "## Caveats and assumptions\n\n"
                    f"这是收盘后按结果筛选的描述性事件研究，存在天然的事后选择，不能在盘中提前知道当天最终属于大阳日。样本仅13天，不代表统计预测力。分钟接口原始覆盖09:31-14:59共239点，15:00使用同源日线收盘锚定；主样本中14:59与日收盘的最大差异为{primary_anchor_max:.2f}个基点。日线与本地Tushare重叠48个交易日，OHLC逐项完全一致；分钟路径未获得第二独立数据源的全期交叉验证。"
                ),
            },
        ],
    }

    artifact = {
        "surface": "report",
        "manifest": manifest,
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "individual_paths": records(individual),
                "aggregate_paths": records(aggregate),
                "event_details": records(events[event_columns]),
                "sensitivity": records(sensitivity[sensitivity_columns]),
                "common_features": records(
                    common[["feature", "matching_days", "sample_count", "share", "meets_70pct_rule"]]
                ),
                "path_type_counts": records(path_types),
            },
        },
        "sources": [source_full],
    }
    destination = directory / "artifact.json"
    destination.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(destination)
    print(f"bytes={destination.stat().st_size}")
    print(f"snapshot_rows={sum(len(rows) for rows in artifact['snapshot']['datasets'].values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
