#!/usr/bin/env python3
"""Build the 2025-to-date current-action chart for “猛干/打满”.

The review rule intentionally follows the user's broad semantic boundary:
keep same-day/current positive action language even when no stock is named;
exclude only non-current/future language, another actor's action, and
negated/hypothetical/non-action language.  Every literal hit is assigned to
exactly one side of the audit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
import pandas as pd

from build_hupu_signal_analysis import cst_datetime, read_jsonl


ROOT = Path(__file__).resolve().parents[1]
EXPORT_ROOT = ROOT / "output" / "hupu_real_xielaoban_2024-08-19_to_2026-08-19"
INPUT_JSONL = EXPORT_ROOT / "replies.jsonl"
INDEX_INPUT = EXPORT_ROOT / "strict_menggan_2025_to_2026-08-19" / "sse_000001_sh_daily.csv"
OUT_DIR = EXPORT_ROOT / "current_action_signals_2025_to_2026-08-19"
START_DATE = "2025-01-01"
END_DATE = "2026-08-19"
CST = ZoneInfo("Asia/Shanghai")
NO_STOCK = "无个股信息"
PINGFANG_PATH = Path(
    "/System/Library/AssetsV2/com_apple_MobileAsset_Font7/"
    "3419f2a427639ad8c8e139149a287865a90fa17e.asset/AssetData/PingFang.ttc"
)


# All other literal hits are explicitly assigned to one of the three exclusion
# families below.  This manual allow-list is the reviewed semantic result, not
# an automatic keyword heuristic.
INCLUDED_REPLY_IDS = {
    "342524", "342621", "343810", "345448", "346487", "349228", "350079", "350099",
    "350141", "350143", "350148", "350156", "350417", "350867", "352174", "352188",
    "354555", "354740", "354786", "354797", "355216", "355223", "356099", "356187",
    "356981", "357256", "358256", "358317", "359769", "363431", "365953", "370833",
    "372036", "372041", "372745", "373508", "373515", "373683", "375213", "375327",
    "375666", "375885", "376368", "377215", "378325", "378714", "378930", "379784",
    "379797", "381536", "381646", "382101", "382379", "382607", "384022", "384027",
    "384127", "384373", "385535", "386686", "386889", "387387",
}


NON_CURRENT_REPLY_IDS = {
    "341116", "341145", "342405", "344047", "346576", "350379", "353277", "355662",
    "356039", "356331", "357204", "357604", "373345", "373898", "382183", "383117",
    "384115",
}


OTHER_ACTOR_REPLY_IDS = {
    "341352", "346775", "348874", "348949", "348952", "349118", "349821", "350211",
    "350350", "352202", "352346", "352922", "357812", "372044", "374386", "375414",
    "376393", "378722", "380053", "384019", "385552", "387543",
}


NEGATED_OR_NON_ACTION_REPLY_IDS = {
    "339327", "342541", "343169", "346841", "346960", "350089", "352370", "352667",
    "356935", "357118", "358506", "358968", "370600", "378827", "380532", "386672",
}


# Only attach a stock when the current-action phrase itself has a defensible
# target.  Adjacent sectors, quoted context, and unrelated holdings are not
# promoted.  The chart table uses the literal fallback requested by the user.
STOCK_LABEL_BY_REPLY_ID = {
    "342524": "来伊份",
    "349228": "中超控股",
    "350143": "京华激光",
    "354740": "万通（原文简称）",
    "354786": "凯美特气",
    "356099": "中国稀土",
    "356187": "国轩高科",
    "356981": "工业富联",
    "363431": "航天动力",
    "375213": "华电辽能",
    "375885": "平潭发展",
    "377215": "大为股份",
    "378714": "光迅科技",
    "378930": "天通股份；利通；中天",
    "382101": "工业富联",
    "386889": "云南锗业",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT_JSONL)
    parser.add_argument("--index-csv", type=Path, default=INDEX_INPUT)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    return parser.parse_args()


def write_csv(rows: list[dict[str, Any]], path: Path, fieldnames: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def trigger_term(text: str) -> str:
    has_menggan = "猛干" in text
    has_daman = "打满" in text
    if has_menggan and has_daman:
        return "猛干+打满"
    return "猛干" if has_menggan else "打满"


def inclusion_basis(text: str) -> str:
    if "今天" in text:
        return "明确当天正向操作表述"
    if any(token in text for token in ("已经", "我已", "打满了", "猛干了", "下班", "继续")):
        return "当日/当前完成或持续态"
    return "盘中当前正向操作表述"


def chart_date_for(post_date: str, trading_days: list[pd.Timestamp]) -> str:
    day = pd.Timestamp(post_date)
    day_set = set(trading_days)
    if day in day_set:
        return day.strftime("%Y-%m-%d")
    later = [candidate for candidate in trading_days if candidate > day]
    return later[0].strftime("%Y-%m-%d") if later else ""


def configure_pingfang() -> fm.FontProperties:
    if not PINGFANG_PATH.exists():
        raise FileNotFoundError(f"PingFang font not found: {PINGFANG_PATH}")
    # Register the TTC and select its Simplified Chinese family explicitly.
    fm.fontManager.addfont(str(PINGFANG_PATH))
    matched = Path(fm.findfont(fm.FontProperties(family="PingFang SC"), fallback_to_default=False))
    if matched.resolve() != PINGFANG_PATH.resolve():
        raise RuntimeError(f"PingFang SC resolved to an unexpected file: {matched}")
    plt.rcParams.update({
        "font.family": "PingFang SC",
        "font.size": 11,
        "axes.titlesize": 11,
        "axes.labelsize": 11,
        "xtick.labelsize": 11,
        "ytick.labelsize": 11,
        "legend.fontsize": 11,
        "axes.unicode_minus": False,
    })
    return fm.FontProperties(family="PingFang SC", size=11)


def aggregate_event_days(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_date.setdefault(event["chart_date"], []).append(event)
    rows: list[dict[str, Any]] = []
    for chart_date in sorted(by_date):
        date_events = by_date[chart_date]
        labels: list[str] = []
        for event in date_events:
            label = event["stock_name"]
            if label not in labels:
                labels.append(label)
        rows.append({
            "chart_date": chart_date,
            "post_dates": "；".join(sorted({event["post_date"] for event in date_events})),
            "stock_name": "；".join(labels),
            "event_count": len(date_events),
            "trigger_terms": "；".join(sorted({event["trigger_term"] for event in date_events})),
            "reply_ids": "；".join(event["reply_id"] for event in date_events),
        })
    return rows


def draw_event_table(ax: plt.Axes, daily_rows: list[dict[str, Any]], font: fm.FontProperties) -> None:
    ax.axis("off")
    blocks = 4
    row_count = math.ceil(len(daily_rows) / blocks)
    headers: list[str] = []
    for _ in range(blocks):
        headers.extend(["日期", "个股"])
    table_rows: list[list[str]] = []
    for row_index in range(row_count):
        row_values: list[str] = []
        for block_index in range(blocks):
            item_index = row_index * blocks + block_index
            if item_index < len(daily_rows):
                item = daily_rows[item_index]
                row_values.extend([item["chart_date"], item["stock_name"]])
            else:
                row_values.extend(["", ""])
        table_rows.append(row_values)
    table = ax.table(
        cellText=table_rows,
        colLabels=headers,
        cellLoc="left",
        colLoc="left",
        colWidths=[0.085, 0.165] * blocks,
        bbox=[0.0, 0.02, 1.0, 0.96],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    for (row, column), cell in table.get_celld().items():
        cell.set_edgecolor("#D8DDE3")
        cell.set_linewidth(0.55)
        cell.PAD = 0.06
        cell.get_text().set_fontproperties(font)
        if row == 0:
            cell.set_facecolor("#F3ECDD")
            cell.get_text().set_weight("semibold")
            cell.get_text().set_color("#2F3337")
        else:
            cell.set_facecolor("#FFFFFF" if row % 2 else "#FAFAF8")
            cell.get_text().set_color("#343A40")


def plot_chart(
    index_df: pd.DataFrame,
    daily_rows: list[dict[str, Any]],
    png_path: Path,
    svg_path: Path,
) -> None:
    font = configure_pingfang()
    df = index_df.sort_values("date").copy()
    df["date"] = pd.to_datetime(df["date"])
    date_nums = mdates.date2num(df["date"].dt.to_pydatetime())
    fig = plt.figure(figsize=(28, 22), dpi=200, facecolor="white")
    grid = fig.add_gridspec(nrows=2, ncols=1, height_ratios=[3.1, 2.2], hspace=0.035)
    ax = fig.add_subplot(grid[0])
    table_ax = fig.add_subplot(grid[1])

    up_color = "#C23A35"
    down_color = "#24856F"
    event_color = "#A86D00"
    for x, (_, row) in zip(date_nums, df.iterrows()):
        open_price, high_price, low_price, close_price = map(
            float,
            (row["open"], row["high"], row["low"], row["close"]),
        )
        color = up_color if close_price >= open_price else down_color
        ax.vlines(x, low_price, high_price, color=color, linewidth=0.78, zorder=1)
        body_bottom = min(open_price, close_price)
        body_height = max(abs(close_price - open_price), 0.75)
        if abs(close_price - open_price) < 0.75:
            body_bottom -= (0.75 - abs(close_price - open_price)) / 2
        ax.add_patch(
            Rectangle(
                (x - 0.32, body_bottom),
                0.64,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.35,
                zorder=2,
            ),
        )

    low_lookup = df.set_index(df["date"].dt.strftime("%Y-%m-%d"))["low"].astype(float).to_dict()
    price_span = float(df["high"].max() - df["low"].min())
    marker_dates = [row["chart_date"] for row in daily_rows]
    marker_x = [mdates.date2num(pd.Timestamp(date).to_pydatetime()) for date in marker_dates]
    marker_y = [low_lookup[date] - price_span * 0.022 for date in marker_dates]
    ax.scatter(
        marker_x,
        marker_y,
        s=38,
        marker="o",
        facecolors=event_color,
        edgecolors="white",
        linewidths=0.65,
        zorder=6,
    )

    ax.set_title("上证指数日K线与当日/当前操作信号（2025年至今）", fontproperties=font, fontweight="semibold", pad=24)
    ax.text(
        0.5,
        1.012,
        "“猛干”和“打满”合并为同一种标记；排除非当日、他人动作及否定/反事实表达。",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        color="#555D66",
        fontproperties=font,
    )
    ax.set_ylabel("上证指数点位", fontproperties=font)
    ax.grid(axis="y", color="#E3E6EA", linewidth=0.65)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#6B7280")
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    for label in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
        label.set_fontproperties(font)
    chart_low = min(marker_y) - price_span * 0.025
    chart_high = float(df["high"].max()) + price_span * 0.065
    ax.set_ylim(chart_low, chart_high)
    legend_handle = Line2D(
        [0], [0], marker="o", linestyle="none", markersize=7,
        markerfacecolor=event_color, markeredgecolor="white",
        label="当日/当前正向操作（猛干/打满合并）",
    )
    ax.legend(handles=[legend_handle], loc="upper left", frameon=False, prop=font)

    draw_event_table(table_ax, daily_rows, font)
    fig.text(
        0.5,
        0.012,
        "表格日期与图中圆点一一对应；未明确个股的事件统一标为“无个股信息”。来源：虎扑公开回复、Tushare 000001.SH 日线。仅作文本复盘。",
        ha="center",
        va="bottom",
        color="#555D66",
        fontproperties=font,
    )
    fig.savefig(png_path, facecolor="white", bbox_inches="tight", pad_inches=0.18)
    fig.savefig(svg_path, facecolor="white", bbox_inches="tight", pad_inches=0.18)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    index_path = args.index_csv.resolve()
    output_dir = args.out.resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input corpus not found: {input_path}")
    if not index_path.exists():
        raise FileNotFoundError(f"Index CSV not found: {index_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_rows = read_jsonl(input_path)
    candidate_rows: list[tuple[dict[str, Any], datetime]] = []
    for raw in raw_rows:
        text = str(raw.get("text", ""))
        if "猛干" not in text and "打满" not in text:
            continue
        posted = cst_datetime(str(raw["createdAt"]))
        post_date = posted.date().isoformat()
        if START_DATE <= post_date <= END_DATE:
            candidate_rows.append((raw, posted))

    index_df = pd.read_csv(index_path)
    index_df["date"] = pd.to_datetime(index_df["date"])
    index_df = index_df.loc[
        (index_df["date"] >= pd.Timestamp(START_DATE)) & (index_df["date"] <= pd.Timestamp(END_DATE))
    ].sort_values("date").reset_index(drop=True)
    if index_df.empty:
        raise RuntimeError("Index input has no rows in the requested range")
    trading_days = [pd.Timestamp(date).normalize() for date in index_df["date"]]

    candidate_ids = {str(raw.get("replyId", "")) for raw, _ in candidate_rows}
    reviewed_ids = (
        INCLUDED_REPLY_IDS
        | NON_CURRENT_REPLY_IDS
        | OTHER_ACTOR_REPLY_IDS
        | NEGATED_OR_NON_ACTION_REPLY_IDS
    )
    if candidate_ids != reviewed_ids:
        missing = sorted(candidate_ids - reviewed_ids)
        stale = sorted(reviewed_ids - candidate_ids)
        raise RuntimeError(f"Semantic audit is not exhaustive; missing={missing}, stale={stale}")
    category_sets = [
        INCLUDED_REPLY_IDS,
        NON_CURRENT_REPLY_IDS,
        OTHER_ACTOR_REPLY_IDS,
        NEGATED_OR_NON_ACTION_REPLY_IDS,
    ]
    if sum(len(group) for group in category_sets) != len(reviewed_ids):
        raise RuntimeError("Semantic audit categories overlap")

    events: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for raw, posted in candidate_rows:
        reply_id = str(raw.get("replyId", ""))
        text = str(raw.get("text", ""))
        post_date = posted.date().isoformat()
        common = {
            "reply_id": reply_id,
            "thread_id": str(raw.get("threadId", "")),
            "posted_at_cst": posted.isoformat(timespec="seconds"),
            "post_date": post_date,
            "chart_date": chart_date_for(post_date, trading_days),
            "trigger_term": trigger_term(text),
            "raw_text": text,
            "source_url": str(raw.get("url", "")),
            "record_hash": hashlib.sha256(f"{reply_id}|{raw.get('createdAt')}|{text}".encode("utf-8")).hexdigest(),
        }
        if reply_id in INCLUDED_REPLY_IDS:
            events.append({
                **common,
                "stock_name": STOCK_LABEL_BY_REPLY_ID.get(reply_id, NO_STOCK),
                "inclusion_basis": inclusion_basis(text),
                "marker_include": True,
            })
            continue
        if reply_id in NON_CURRENT_REPLY_IDS:
            category = "非当日/非当前操作"
            reason = "表达明天、下周、昨天、一般规律或未来准备，不作为发帖当日操作。"
        elif reply_id in OTHER_ACTOR_REPLY_IDS:
            category = "他人或市场主体操作"
            reason = "主语是你们、资金、游资、炒家或其他主体，不作为作者当日操作。"
        else:
            category = "否定/反事实/非行动"
            reason = "属于否定、没买到、反问、条件或反事实表述，不作为已发生的正向操作。"
        exclusions.append({**common, "exclusion_category": category, "exclusion_reason": reason, "marker_include": False})

    events.sort(key=lambda row: row["posted_at_cst"])
    exclusions.sort(key=lambda row: row["posted_at_cst"])
    daily_rows = aggregate_event_days(events)
    if len(candidate_rows) != 117 or len(events) != 62 or len(exclusions) != 55 or len(daily_rows) != 50:
        raise RuntimeError(
            "Unexpected reviewed counts: "
            f"candidates={len(candidate_rows)}, events={len(events)}, exclusions={len(exclusions)}, days={len(daily_rows)}"
        )
    marker_dates = {row["chart_date"] for row in daily_rows}
    if marker_dates - set(index_df["date"].dt.strftime("%Y-%m-%d")):
        raise RuntimeError("An event table date is absent from the index series")

    event_fields = [
        "reply_id", "thread_id", "posted_at_cst", "post_date", "chart_date", "trigger_term", "stock_name",
        "inclusion_basis", "marker_include", "raw_text", "source_url", "record_hash",
    ]
    exclusion_fields = [
        "reply_id", "thread_id", "posted_at_cst", "post_date", "chart_date", "trigger_term",
        "exclusion_category", "exclusion_reason", "marker_include", "raw_text", "source_url", "record_hash",
    ]
    daily_fields = ["chart_date", "post_dates", "stock_name", "event_count", "trigger_terms", "reply_ids"]
    write_csv(events, output_dir / "current_action_events.csv", event_fields)
    write_csv(exclusions, output_dir / "exclusion_audit.csv", exclusion_fields)
    write_csv(daily_rows, output_dir / "daily_event_table.csv", daily_fields)
    index_export = index_df.copy()
    index_export["date"] = index_export["date"].dt.strftime("%Y-%m-%d")
    index_export.to_csv(output_dir / "sse_000001_sh_daily.csv", index=False, encoding="utf-8-sig")

    png_path = output_dir / "sh_index_kline_current_action_signals_2025_to_2026-08-19.png"
    svg_path = output_dir / "sh_index_kline_current_action_signals_2025_to_2026-08-19.svg"
    plot_chart(index_df, daily_rows, png_path, svg_path)

    summary = {
        "date_window": [START_DATE, END_DATE],
        "literal_keyword_replies": len(candidate_rows),
        "included_replies": len(events),
        "included_chart_dates": len(daily_rows),
        "excluded_replies": len(exclusions),
        "menggan_included": sum("猛干" in row["trigger_term"] for row in events),
        "daman_included": sum("打满" in row["trigger_term"] for row in events),
        "index_daily_rows": len(index_df),
        "font_family": "PingFang SC",
        "font_size_pt": 11,
        "generated_at": datetime.now(tz=CST).isoformat(timespec="seconds"),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
