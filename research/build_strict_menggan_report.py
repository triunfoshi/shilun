#!/usr/bin/env python3
"""Build the 2025-to-date, strict semantic “猛干” event report.

Only a reply that unambiguously expresses the author's own current-session,
large-scale buying of a named stock is allowed onto the chart.  Literal
keyword hits are retained in an exclusion audit rather than silently promoted
to trades.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import pandas as pd

from build_hupu_signal_analysis import (
    build_stock_lookup,
    cst_datetime,
    load_chinese_font,
    read_jsonl,
    resolve_entity,
)
from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient


ROOT = Path(__file__).resolve().parents[1]
EXPORT_ROOT = ROOT / "output" / "hupu_real_xielaoban_2024-08-19_to_2026-08-19"
INPUT_JSONL = EXPORT_ROOT / "replies.jsonl"
OUT_DIR = EXPORT_ROOT / "strict_menggan_2025_to_2026-08-19"
CST = ZoneInfo("Asia/Shanghai")
START_DATE = "20250101"
END_DATE = "20260819"
SESSION_CLOSE = time(15, 0)


@dataclass(frozen=True)
class StrictEventSpec:
    event_id: str
    raw_targets: tuple[str, ...]
    primary_raw_target: str
    expected_primary_name: str
    time_linkage: str
    time_evidence: str
    self_action_evidence: str
    review_note: str


# These two events are the deliberately narrow main-chart set.  The second is
# included because “都在…只有我…” is an explicit current-session first-person
# contrast in a 09:52 intraday post, even though it does not literally use the
# word “今天”.  Unresolved co-mentioned abbreviations stay in the target audit.
STRICT_EVENT_SPECS: dict[str, StrictEventSpec] = {
    "378714": StrictEventSpec(
        event_id="MG-01",
        raw_targets=("光迅",),
        primary_raw_target="光迅",
        expected_primary_name="光迅科技",
        time_linkage="literal_today",
        time_evidence="原句含“今天”",
        self_action_evidence="原句含第一人称“我”",
        review_note="同句明确：本人 + 今天 + 猛干 + 个股；无否定、条件或出货语境。",
    ),
    "378930": StrictEventSpec(
        event_id="MG-02",
        raw_targets=("天通", "利通", "中天"),
        primary_raw_target="天通",
        expected_primary_name="天通股份",
        time_linkage="current_session",
        time_evidence="“都在…只有我…”为同一盘中现在进行式；发帖时间 09:52",
        self_action_evidence="原句含“只有我”",
        review_note="同句明确本人当前动作；仅“天通”可安全标准化，利通/中天保留未解析。",
    ),
}

NEAR_MISS_REASONS: dict[str, str] = {
    "342524": "相对时间“前一天…第二天”，不把推断出的当日性当作明确今天。",
    "342541": "反事实“否则就会猛干”，未确认实际买入。",
    "350089": "原句明确“没买到”，不是实际成交。",
    "350143": "策略/继续表达，未明确本人当日已买入。",
    "356187": "同一回复前句有第一人称交易叙事，但“国轩高科疯狂猛干”本句未明确本人或今天。",
    "372745": "“我已猛干”未在同句明确标的；不从被回复文本补填个股。",
    "377215": "原句明示“让 xdm 爽爽出货”，属于讽刺/出货语境。",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT_JSONL)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(tz=CST).isoformat(timespec="seconds")


def write_csv(rows: list[dict[str, Any]], path: Path, fieldnames: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def align_trade_date(posted: datetime, trading_days: list[pd.Timestamp]) -> str:
    post_day = pd.Timestamp(posted.date())
    day_set = set(trading_days)
    if post_day in day_set and posted.timetz().replace(tzinfo=None) < SESSION_CLOSE:
        return post_day.strftime("%Y-%m-%d")
    for day in trading_days:
        if day > post_day:
            return day.strftime("%Y-%m-%d")
    return ""


def literal_signal_category(reply_id: str, text: str) -> tuple[str, str]:
    """Explain why a literal hit does not qualify, without overclaiming NLP."""

    if reply_id in NEAR_MISS_REASONS:
        return "manual_boundary_exclusion", NEAR_MISS_REASONS[reply_id]
    if re.search(r"没买到|没买|未买|不买", text):
        return "negated_or_not_filled", "文本明确未买到/未成交，不能视作买入。"
    if re.search(r"出货|清仓", text):
        return "exit_or_distribution", "文本出现清仓、出货或分发语境，不视作正向买入。"
    if re.search(r"可以猛干|值得猛干|来一起猛干|要.*猛干|必须.*猛干|能.*猛干|如果.*猛干", text):
        return "conditional_or_recommendation", "属于条件、建议或号召，未明确本人当日已买入。"
    if re.search(r"大资金|资金|游资|炒家|别人|他们", text) and "我" not in text:
        return "third_party_or_market_description", "描述他人或市场资金，并非作者本人当日买入。"
    if re.search(r"昨天|前一天|第二天|之前|向来|一直|已经|我已", text):
        return "not_explicit_today", "不具备同句明确的今天/当前盘中买入时点。"
    if "我" not in text:
        return "no_explicit_self_action", "未在同句明确作者本人已执行买入。"
    return "does_not_meet_all_strict_conditions", "未同时满足本人、当前时点、明确标的和非否定/非条件四项条件。"


def plot_kline(index_df: pd.DataFrame, event_rows: list[dict[str, Any]], png_path: Path, svg_path: Path) -> None:
    font = load_chinese_font()
    font_kw: dict[str, Any] = {"fontproperties": font} if font else {}
    df = index_df.sort_values("date").copy()
    df["date"] = pd.to_datetime(df["date"])
    date_nums = mdates.date2num(df["date"].dt.to_pydatetime())
    fig, ax = plt.subplots(figsize=(19, 9.4), dpi=160, facecolor="white")

    up_color = "#C23A35"
    down_color = "#24856F"
    for x, (_, row) in zip(date_nums, df.iterrows()):
        open_price, high_price, low_price, close_price = map(float, (row["open"], row["high"], row["low"], row["close"]))
        color = up_color if close_price >= open_price else down_color
        ax.vlines(x, low_price, high_price, color=color, linewidth=0.72, zorder=1)
        body_bottom = min(open_price, close_price)
        body_height = abs(close_price - open_price)
        if body_height < 0.75:
            body_bottom -= 0.375
            body_height = 0.75
        ax.add_patch(Rectangle((x - 0.30, body_bottom), 0.60, body_height, facecolor=color, edgecolor=color, linewidth=0.35, zorder=2))

    span = float(df["high"].max() - df["low"].min())
    low_base = float(df["low"].min())
    marker_y = low_base - span * 0.030
    label_offsets = [(-72, 72), (78, 100)]
    for index, event in enumerate(event_rows):
        event_day = pd.Timestamp(event["chart_trade_date"])
        x = mdates.date2num(event_day.to_pydatetime())
        ax.scatter(
            x,
            marker_y,
            s=88,
            marker="D",
            facecolors="white",
            edgecolors="#143D63",
            linewidths=1.6,
            zorder=7,
            label="严格语义标记：作者当日/当场本人“猛干”" if index == 0 else None,
        )
        offset = label_offsets[index % len(label_offsets)]
        label = f"{event['event_id']}  {event['canonical_primary_target']}\n{event['stock_code']}"
        ax.annotate(
            label,
            xy=(x, marker_y),
            xytext=offset,
            textcoords="offset points",
            ha="right" if offset[0] < 0 else "left",
            va="bottom",
            fontsize=9.6,
            color="#143D63",
            arrowprops={"arrowstyle": "-", "color": "#143D63", "lw": 0.85},
            bbox={"boxstyle": "round,pad=0.30", "fc": "white", "ec": "#B9C7D5", "lw": 0.8},
            **font_kw,
        )

    ax.set_title("上证指数日K与严格“猛干”当日自述事件", fontsize=18, fontweight="bold", pad=20, **font_kw)
    ax.text(
        0.5,
        1.01,
        "2025 年 1 月 1 日至 2026 年 8 月 19 日；只标记本人当前/当日、明确个股、非否定且非条件式的“猛干”表述。",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=10.6,
        color="#4B5563",
        **font_kw,
    )
    ax.set_ylabel("上证指数点位", fontsize=11, **font_kw)
    ax.set_ylim(marker_y - span * 0.04, float(df["high"].max()) + span * 0.075)
    ax.grid(axis="y", color="#E5E7EB", linewidth=0.7)
    ax.spines[["top", "right"]].set_visible(False)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    for tick in ax.get_xticklabels():
        if font:
            tick.set_fontproperties(font)
    ax.legend(loc="upper left", frameon=False, prop=font, fontsize=10)
    ax.text(
        0.5,
        -0.13,
        "标记是公开文本中的自述事件，不是成交验证、仓位金额、持仓证明、收益归因或投资建议。",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=9.5,
        color="#4B5563",
        **font_kw,
    )
    fig.savefig(png_path, bbox_inches="tight", facecolor="white")
    fig.savefig(svg_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_sqlite(
    output_dir: Path,
    strict_events: list[dict[str, Any]],
    strict_targets: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
    index_df: pd.DataFrame,
    timeline_df: pd.DataFrame,
    summary: dict[str, Any],
) -> None:
    db_path = output_dir / "analysis.sqlite"
    with sqlite3.connect(db_path) as connection:
        pd.DataFrame(strict_events).to_sql("strict_menggan_events", connection, if_exists="replace", index=False)
        pd.DataFrame(strict_targets).to_sql("strict_menggan_targets", connection, if_exists="replace", index=False)
        pd.DataFrame(exclusions).to_sql("menggan_exclusion_audit", connection, if_exists="replace", index=False)
        index_export = index_df.copy()
        index_export["date"] = pd.to_datetime(index_export["date"]).dt.strftime("%Y-%m-%d")
        index_export.rename(columns={"date": "trade_date"}).to_sql("sse_index_daily", connection, if_exists="replace", index=False)
        timeline_df.to_sql("signal_timeline", connection, if_exists="replace", index=False)
        pd.DataFrame([summary]).to_sql("summary_metrics", connection, if_exists="replace", index=False)


def build_artifact(
    output_dir: Path,
    index_df: pd.DataFrame,
    strict_events: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
    timeline_df: pd.DataFrame,
    summary: dict[str, Any],
    chart_png: Path,
) -> dict[str, Any]:
    generated_at = now_iso()
    png_data_uri = "data:image/png;base64," + base64.b64encode(chart_png.read_bytes()).decode("ascii")
    source_inventory = [
        {
            "id": "summary_metrics",
            "label": "严格“猛干”语义筛选汇总",
            "path": "analysis.sqlite",
            "query": {
                "engine": "sqlite",
                "sql": "SELECT literal_menggan_hits, strict_marked_events, excluded_hits, safely_standardized_primary_targets FROM summary_metrics",
                "description": "2025 年至今的“猛干”字面命中及严格语义筛选汇总。",
                "tables_used": ["summary_metrics"],
                "filters": ["开始日期：2025-01-01", "关键词：猛干", "排除：打满、建议、条件、第三方、未成交、反讽/出货"],
            },
        },
        {
            "id": "strict_events",
            "label": "严格“猛干”当日自述事件",
            "path": "analysis.sqlite",
            "query": {
                "engine": "sqlite",
                "sql": "SELECT event_id, reply_id, posted_at_cst, post_date, chart_trade_date, raw_primary_target, canonical_primary_target, stock_code, time_linkage, self_action, raw_text, source_url FROM strict_menggan_events ORDER BY posted_at_cst",
                "description": "仅保留作者本人当前/当日明确“猛干”且标的可安全标准化的事件。",
                "tables_used": ["strict_menggan_events"],
                "filters": ["本人动作", "当前/当日语义", "同句明确标的", "非否定、非条件、非反讽"],
            },
        },
        {
            "id": "market_signal_join",
            "label": "Tushare 上证指数日线与严格事件对齐",
            "path": "analysis.sqlite",
            "query": {
                "engine": "sqlite",
                "sql": "SELECT i.trade_date, i.open, i.high, i.low, i.close, i.volume, i.amount, COALESCE(t.strict_event_count, 0) AS strict_event_count FROM sse_index_daily AS i LEFT JOIN signal_timeline AS t ON i.trade_date = t.trade_date WHERE i.ticker = '000001.SH' ORDER BY i.trade_date",
                "description": "Tushare 000001.SH 日线与严格事件的交易日对齐记录。",
                "tables_used": ["sse_index_daily", "signal_timeline"],
                "filters": ["日期：2025-01-01 至 2026-08-19", "交易日且 15:00 前对齐当日"],
            },
        },
        {
            "id": "exclusion_audit",
            "label": "“猛干”语义排除审计",
            "path": "analysis.sqlite",
            "query": {
                "engine": "sqlite",
                "sql": "SELECT reply_id, posted_at_cst, screen_category, exclusion_reason, raw_text, source_url FROM menggan_exclusion_audit ORDER BY posted_at_cst",
                "description": "未进入图表的字面“猛干”命中及其排除理由。",
                "tables_used": ["menggan_exclusion_audit"],
                "filters": ["2025 年至今", "严格标记之外的全部猛干字面命中"],
            },
        },
    ]
    index_dataset = []
    timeline_lookup = timeline_df.set_index("trade_date").to_dict(orient="index")
    for _, row in index_df.iterrows():
        date_text = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
        signal = timeline_lookup.get(date_text, {})
        index_dataset.append(
            {
                "trade_date": date_text,
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": round(float(row["volume"]), 2),
                "amount": round(float(row["amount"]), 2),
                "strict_event_count": int(signal.get("strict_event_count", 0)),
                "event_ids": signal.get("event_ids", ""),
            },
        )
    report_rows = [
        {
            "event_id": row["event_id"],
            "post_date": row["post_date"],
            "posted_at": row["posted_at_cst"],
            "chart_trade_date": row["chart_trade_date"],
            "stock": row["canonical_primary_target"],
            "stock_code": row["stock_code"],
            "time_evidence": row["time_evidence"],
            "self_action": row["self_action"],
            "sentence": row["raw_text"],
            "source_url": row["source_url"],
        }
        for row in strict_events
    ]
    custom_chart_html = (
        '<figure style="margin:0">'
        f'<img src="{png_data_uri}" alt="2025 年至今的上证指数日K线。仅有两枚菱形标记，表示语义上严格筛选出的作者当日或当前盘中本人猛干自述。" '
        'style="display:block;width:100%;height:auto;border:1px solid #e5e7eb;border-radius:10px" />'
        '<figcaption style="margin-top:8px;color:#4b5563;font-size:13px">'
        "只有通过严格语义门槛的两条原文才被标记；其余 86 条字面‘猛干’命中均保留在排除审计表中。"
        "</figcaption></figure>"
    )
    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": "2025年至今上证指数与严格“猛干”标记",
            "description": "只标记作者本人当日或当前盘中明确大规模买入的公开自述。",
            "generatedAt": generated_at,
            "sources": source_inventory,
            "cards": [
                {
                    "id": "literal_hits",
                    "description": "2025 年至今字面包含“猛干”的公开回复数。",
                    "dataset": "summary_metrics",
                    "sourceId": "summary_metrics",
                    "metrics": [{"label": "字面“猛干”命中", "field": "literal_menggan_hits", "format": "number"}],
                },
                {
                    "id": "strict_events",
                    "description": "通过本人、当前/当日、明确个股、非否定/非条件语义门槛的事件数。",
                    "dataset": "summary_metrics",
                    "sourceId": "summary_metrics",
                    "metrics": [{"label": "严格图表标记", "field": "strict_marked_events", "format": "number"}],
                },
                {
                    "id": "excluded_hits",
                    "description": "未达到严格语义门槛、仅保留在审计记录中的字面命中数。",
                    "dataset": "summary_metrics",
                    "sourceId": "summary_metrics",
                    "metrics": [{"label": "语义排除", "field": "excluded_hits", "format": "number"}],
                },
            ],
            "charts": [
                {
                    "id": "sh_close_trend",
                    "title": "上证指数收盘趋势",
                    "subtitle": "2025 年 1 月 1 日至 2026 年 8 月 19 日；严格事件出现日可在提示信息中核对。",
                    "type": "line",
                    "dataset": "sh_index_daily",
                    "sourceId": "market_signal_join",
                    "encodings": {
                        "x": {"field": "trade_date", "type": "temporal", "label": "交易日"},
                        "y": {"field": "close", "type": "quantitative", "label": "收盘点位"},
                        "tooltip": [
                            {"field": "open", "type": "quantitative", "label": "开盘"},
                            {"field": "high", "type": "quantitative", "label": "最高"},
                            {"field": "low", "type": "quantitative", "label": "最低"},
                            {"field": "strict_event_count", "type": "quantitative", "label": "严格事件数"},
                            {"field": "event_ids", "type": "text", "label": "事件编号"},
                        ],
                    },
                },
            ],
            "tables": [
                {
                    "id": "strict_event_table",
                    "title": "严格“猛干”具体日期记录",
                    "subtitle": "仅列入本人当前/当日明确自述、且个股在同句可安全标准化的事件。",
                    "dataset": "strict_menggan_events",
                    "sourceId": "strict_events",
                    "defaultSort": {"field": "post_date", "direction": "asc"},
                    "columns": [
                        {"field": "event_id", "label": "编号", "type": "text"},
                        {"field": "post_date", "label": "发帖日", "type": "date"},
                        {"field": "posted_at", "label": "发帖时间（CST）", "type": "text"},
                        {"field": "chart_trade_date", "label": "图表对齐日", "type": "date"},
                        {"field": "stock", "label": "个股", "type": "text"},
                        {"field": "stock_code", "label": "代码", "type": "text"},
                        {"field": "time_evidence", "label": "当天依据", "type": "text"},
                        {"field": "sentence", "label": "原句", "type": "text"},
                        {"field": "source_url", "label": "原文链接", "type": "text"},
                    ],
                },
            ],
            "blocks": [
                {"id": "title", "type": "markdown", "body": "# 2025年至今上证指数与严格“猛干”标记"},
                {
                    "id": "executive_summary",
                    "type": "markdown",
                    "sourceId": "summary_metrics",
                    "body": (
                        "## Executive Summary\n\n"
                        f"- **筛选结果：**2025 年至今 {summary['literal_menggan_hits']} 条字面“猛干”命中中，只有 **{summary['strict_marked_events']} 条**满足“作者本人 + 当前/当日 + 明确个股 + 非否定/非条件”的严格语义门槛。\n"
                        "- **标记对象：**光迅科技（2026 年 5 月 11 日）与天通股份（2026 年 5 月 13 日）。后者原句还提及利通、中天，但因简称多义未另行标准化或标记。\n"
                        "- **解释边界：**‘猛干’只表达公开文本中的强烈买入自述；它不证明成交、金额、持仓或收益。"
                    ),
                },
                {"id": "metric_strip", "type": "metric-strip", "cardIds": ["literal_hits", "strict_events", "excluded_hits"]},
                {
                    "id": "criteria",
                    "type": "markdown",
                    "sourceId": "strict_events",
                    "body": (
                        "## 只有两条文字经得起严格标记\n\n"
                        "标记必须同时满足：原句包含‘猛干’；第一人称明确为作者本人；语义指向当前盘中或当天；个股出现在同一回复；并排除建议、条件、第三方资金、未买到、清仓和出货语境。"
                        "这比把所有关键词直接画在图上更窄，但能避免把观点或段子伪装成实际买入。"
                    ),
                },
                {
                    "id": "kline_context",
                    "type": "markdown",
                    "sourceId": "market_signal_join",
                    "body": (
                        "## 指数日K只保留严格事件\n\n"
                        "下图不显示其他‘猛干’字样，也不显示‘打满’。每枚菱形只对应审计表中的一个回复日，编号与表格一一对应；这样可以直接把原文时点放回指数环境，而不把文字频率误读为交易频率。"
                    ),
                },
                {"id": "kline_chart", "type": "html", "body": custom_chart_html, "sourceId": "market_signal_join"},
                {
                    "id": "close_context",
                    "type": "markdown",
                    "sourceId": "market_signal_join",
                    "body": (
                        "## 收盘趋势参考\n\n"
                        "日线收盘趋势用于审视两条文本出现时的指数背景。提示信息保留当日 OHLC 与严格事件编号，精确语义仍以记录表中的原句为准。"
                    ),
                },
                {"id": "close_chart", "type": "chart", "chartId": "sh_close_trend"},
                {
                    "id": "event_record_intro",
                    "type": "markdown",
                    "sourceId": "strict_events",
                    "body": (
                        "## 具体日期与个股记录\n\n"
                        "每一行提供发帖时间、图表对齐日、当前 Tushare 代码、当天语义依据、原句及原文链接。多义简称不通过猜测补全。"
                    ),
                },
                {"id": "event_record_table", "type": "table", "tableId": "strict_event_table"},
                {
                    "id": "next_steps",
                    "type": "markdown",
                    "body": (
                        "## 后续复盘建议\n\n"
                        "- 后续新增记录时，继续以同一严格门槛处理，避免为了增加样本而放宽为推荐或市场描述。\n"
                        "- 若要扩大样本，可单独建立“次严格候选”层，但不要与主图标记混用。\n"
                        "- 重点复核每条原文的后续K线表现时，需另行定义观察窗口，不能从该文本直接推导收益。"
                    ),
                },
                {
                    "id": "caveats",
                    "type": "markdown",
                    "body": (
                        "## 需保留的边界\n\n"
                        f"其余 {summary['excluded_hits']} 条字面‘猛干’命中已落入 `menggan_exclusion_audit.csv`，包括相对时间、主语省略、条件建议、未成交、第三方叙述和讽刺/出货。"
                        "代码来自当前 Tushare `stock_basic` 快照；历史更名或简称歧义不在本轮自动推断范围内。"
                    ),
                },
            ],
        },
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "summary_metrics": [summary],
                "sh_index_daily": index_dataset,
                "strict_menggan_events": report_rows,
            },
        },
        "sources": source_inventory,
    }
    return artifact


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    output_dir = args.out.resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input corpus not found: {input_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_rows = read_jsonl(input_path)
    keyword_rows = []
    for row in raw_rows:
        text = str(row.get("text", ""))
        if "猛干" not in text:
            continue
        posted = cst_datetime(str(row["createdAt"]))
        if posted.date().isoformat() >= "2025-01-01":
            keyword_rows.append((row, posted))

    client = TushareDailyClient(TushareConfig.from_env())
    index_df = client.fetch_index_daily("000001.SH", START_DATE, END_DATE).sort_values("date").reset_index(drop=True)
    stock_basic = client.fetch_stock_basic(fields="ts_code,name,market,list_date")
    if index_df.empty or stock_basic.empty:
        raise RuntimeError("Tushare returned empty required data")
    stock_lookup = build_stock_lookup(stock_basic)
    trading_days = [pd.Timestamp(day).normalize() for day in index_df["date"].tolist()]

    strict_events: list[dict[str, Any]] = []
    strict_targets: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    event_by_reply: dict[str, dict[str, Any]] = {}
    for raw, posted in keyword_rows:
        reply_id = str(raw.get("replyId", ""))
        text = str(raw.get("text", ""))
        common = {
            "reply_id": reply_id,
            "thread_id": str(raw.get("threadId", "")),
            "posted_at_cst": posted.isoformat(timespec="seconds"),
            "post_date": posted.date().isoformat(),
            "chart_trade_date": align_trade_date(posted, trading_days),
            "raw_text": text,
            "source_url": str(raw.get("url", "")),
            "record_hash": hashlib.sha256(f"{reply_id}|{raw.get('createdAt')}|{text}".encode("utf-8")).hexdigest(),
        }
        spec = STRICT_EVENT_SPECS.get(reply_id)
        if not spec:
            category, reason = literal_signal_category(reply_id, text)
            exclusions.append({
                **common,
                "screen_category": category,
                "exclusion_reason": reason,
                "marker_include": False,
            })
            continue
        primary = resolve_entity(
            spec.primary_raw_target,
            stock_lookup,
            expected_name=spec.expected_primary_name,
            default_confidence="high" if spec.event_id == "MG-01" else "medium",
            note="同句原始简称经当前 Tushare stock_basic 标准化",
        )
        if not primary["stock_code"]:
            raise RuntimeError(f"Primary strict target could not be standardized: {reply_id}")
        event = {
            **common,
            "event_id": spec.event_id,
            "raw_primary_target": spec.primary_raw_target,
            "canonical_primary_target": primary["canonical_name"],
            "stock_code": primary["stock_code"],
            "resolution_method": primary["resolution_method"],
            "confidence": primary["confidence"],
            "stock_linkage": "same_reply",
            "self_action": "explicit_first_person",
            "time_linkage": spec.time_linkage,
            "time_evidence": spec.time_evidence,
            "size_expression": "猛干",
            "action_polarity": "actual_buy_assertion",
            "marker_tier": "strict_direct",
            "marker_include": True,
            "review_note": spec.review_note,
        }
        strict_events.append(event)
        event_by_reply[reply_id] = event
        for raw_target in spec.raw_targets:
            if raw_target == spec.primary_raw_target:
                resolved = primary
                target_note = "事件主标的，进入图表标签。"
            else:
                resolved = {
                    "canonical_name": "",
                    "stock_code": "",
                    "market": "",
                    "list_date": "",
                    "resolution_method": "unresolved_ambiguous",
                    "confidence": "unresolved",
                    "resolution_note": "简称存在多义，保留原词，不自动映射。",
                }
                target_note = "同句共提及，但简称多义，不单独标记。"
            strict_targets.append({
                "event_id": spec.event_id,
                "reply_id": reply_id,
                "post_date": event["post_date"],
                "chart_trade_date": event["chart_trade_date"],
                "raw_target": raw_target,
                "canonical_name": resolved["canonical_name"],
                "stock_code": resolved["stock_code"],
                "market": resolved["market"],
                "list_date": resolved["list_date"],
                "resolution_method": resolved["resolution_method"],
                "confidence": resolved["confidence"],
                "target_note": target_note,
                "source_url": event["source_url"],
            })

    strict_events.sort(key=lambda row: row["posted_at_cst"])
    exclusions.sort(key=lambda row: row["posted_at_cst"])
    if len(strict_events) != len(STRICT_EVENT_SPECS):
        raise RuntimeError("Not every manually reviewed strict event was found")

    timeline_rows = []
    event_date_map: dict[str, list[str]] = {}
    for event in strict_events:
        event_date_map.setdefault(event["chart_trade_date"], []).append(event["event_id"])
    for day in trading_days:
        day_text = day.strftime("%Y-%m-%d")
        ids = event_date_map.get(day_text, [])
        timeline_rows.append({"trade_date": day_text, "strict_event_count": len(ids), "event_ids": "、".join(ids)})
    timeline_df = pd.DataFrame(timeline_rows)

    summary = {
        "literal_menggan_hits": len(keyword_rows),
        "strict_marked_events": len(strict_events),
        "excluded_hits": len(exclusions),
        "safely_standardized_primary_targets": sum(bool(row["stock_code"]) for row in strict_events),
        "source_reply_count": len(raw_rows),
        "generated_at": now_iso(),
    }

    event_fields = [
        "event_id", "reply_id", "thread_id", "posted_at_cst", "post_date", "chart_trade_date", "raw_primary_target",
        "canonical_primary_target", "stock_code", "resolution_method", "confidence", "stock_linkage", "self_action",
        "time_linkage", "time_evidence", "size_expression", "action_polarity", "marker_tier", "marker_include",
        "review_note", "raw_text", "source_url", "record_hash",
    ]
    target_fields = [
        "event_id", "reply_id", "post_date", "chart_trade_date", "raw_target", "canonical_name", "stock_code", "market",
        "list_date", "resolution_method", "confidence", "target_note", "source_url",
    ]
    exclusion_fields = [
        "reply_id", "thread_id", "posted_at_cst", "post_date", "chart_trade_date", "screen_category", "exclusion_reason",
        "marker_include", "raw_text", "source_url", "record_hash",
    ]
    write_csv(strict_events, output_dir / "strict_menggan_events.csv", event_fields)
    write_csv(strict_targets, output_dir / "strict_menggan_targets.csv", target_fields)
    write_csv(exclusions, output_dir / "menggan_exclusion_audit.csv", exclusion_fields)
    write_csv(timeline_rows, output_dir / "signal_timeline.csv", ["trade_date", "strict_event_count", "event_ids"])
    index_export = index_df.copy()
    index_export["date"] = pd.to_datetime(index_export["date"]).dt.strftime("%Y-%m-%d")
    index_export.to_csv(output_dir / "sse_000001_sh_daily.csv", index=False, encoding="utf-8-sig")

    chart_png = output_dir / "sh_index_kline_strict_menggan_2025_to_2026-08-19.png"
    chart_svg = output_dir / "sh_index_kline_strict_menggan_2025_to_2026-08-19.svg"
    plot_kline(index_df, strict_events, chart_png, chart_svg)
    write_sqlite(output_dir, strict_events, strict_targets, exclusions, index_df, timeline_df, summary)
    artifact = build_artifact(output_dir, index_df, strict_events, exclusions, timeline_df, summary, chart_png)
    write_json(output_dir / "artifact.json", artifact)
    source_manifest = {
        "generated_at": summary["generated_at"],
        "input": {
            "dataset": "hupu_real_xielaoban public-reply export",
            "source_reply_count": len(raw_rows),
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "keyword_filter": "猛干 only",
            "date_window": ["2025-01-01", "2026-08-19"],
        },
        "market_data": {
            "provider": "Tushare",
            "ticker": "000001.SH",
            "daily_rows": len(index_df),
            "stock_basic_rows": len(stock_basic),
        },
        "strict_marker_rule": "本人 + 当前/当日 + 同句明确个股 + 猛干 + 非否定/非条件/非第三方/非反讽。",
        "summary": summary,
        "files": [
            "strict_menggan_events.csv",
            "strict_menggan_targets.csv",
            "menggan_exclusion_audit.csv",
            "signal_timeline.csv",
            "sse_000001_sh_daily.csv",
            "analysis.sqlite",
            "artifact.json",
            "report.html",
            chart_png.name,
            chart_svg.name,
        ],
    }
    write_json(output_dir / "source_manifest.json", source_manifest)
    readme = """# 严格“猛干”事件：2025年至今

## 主图标记规则

只有一条回复同时满足以下条件才可进入主图：

1. 原文含“猛干”（不含仅“打满”的回复）。
2. 明确是作者本人已经或正在执行的强烈买入自述。
3. 语义明确指向当前盘中或当天，且个股在同一回复中出现。
4. 不属于建议、条件、反事实、第三方资金、未成交、清仓或讽刺/出货语境。

`MG-02` 的当前性来自同一盘中“都在…只有我…”的现在进行式，时间证据已保留在表中；如果未来要进一步收紧为必须字面含“今天”，可以将其降至次严格层。

## 文件

- `strict_menggan_events.csv`：主图的两条严格事件。
- `strict_menggan_targets.csv`：事件内所有原始标的词；多义简称不会自动写入代码。
- `menggan_exclusion_audit.csv`：其余 2025 年至今的字面“猛干”命中与排除理由。
- `sse_000001_sh_daily.csv`：Tushare `000001.SH` 日线。
- `analysis.sqlite`：报告中的可执行 SQLite 审计数据源。

标记代表公开文本中的自述，不是成交、金额、持仓、收益或投资建议的验证。
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "summary": summary, "events": strict_events}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
