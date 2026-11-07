#!/usr/bin/env python3
"""Build an auditable keyword-event record and SSE index chart from public Hupu replies.

The script deliberately separates a literal keyword hit from a conservative,
manually reviewed target-stock expression.  It does not infer trades from
generic rhetoric or third-party market descriptions.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle
import pandas as pd

from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient


ROOT = Path(__file__).resolve().parents[1]
EXPORT_ROOT = ROOT / "output" / "hupu_real_xielaoban_2024-08-19_to_2026-08-19"
INPUT_JSONL = EXPORT_ROOT / "replies.jsonl"
OUT_DIR = EXPORT_ROOT / "signal_analysis"
CST = ZoneInfo("Asia/Shanghai")
START_DATE = "20240819"
END_DATE = "20260819"
SESSION_CLOSE = time(15, 0)


@dataclass(frozen=True)
class Alias:
    expected_name: str
    confidence: str
    note: str


@dataclass(frozen=True)
class TargetSpec:
    raw_name: str
    intent: str
    confidence: str
    chart_marker: bool
    note: str
    expected_name: str | None = None


# These terms are common community abbreviations in the matched text.  Each
# mapping is cross-checked against the current Tushare stock_basic result before
# a code is emitted.  Lower-confidence aliases remain clearly labeled as such.
ALIASES: dict[str, Alias] = {
    "川润": Alias("川润股份", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "遥望": Alias("遥望科技", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "友好": Alias("友好集团", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "雄韬": Alias("雄韬股份", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "顺纳": Alias("顺钠股份", "low", "疑似错别字，需人工复核"),
    "海德": Alias("海德股份", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "巨轮": Alias("巨轮智能", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "利欧": Alias("利欧股份", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "鸿博": Alias("鸿博股份", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "华胜": Alias("华胜天成", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "京华": Alias("京华激光", "medium", "由相邻语境“京华激光”辅助；需人工复核"),
    "复联": Alias("工业富联", "low", "别名/同音写法；需人工复核"),
    "巨索": Alias("巨力索具", "low", "疑似简称；需人工复核"),
    "泰嘉": Alias("泰嘉股份", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "汉缆": Alias("汉缆股份", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "韶能": Alias("韶能股份", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "拓日": Alias("拓日新能", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "平潭": Alias("平潭发展", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "大为": Alias("大为股份", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "光迅": Alias("光迅科技", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "天通": Alias("天通股份", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "百花": Alias("百花医药", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "哈药": Alias("哈药股份", "medium", "常用简称；当前 Tushare 名称交叉校验"),
    "网宿": Alias("网宿科技", "medium", "常用简称；当前 Tushare 名称交叉校验"),
}

# A raw term can still be recorded without forcing a single stock code.  These
# rows are intentionally emitted as unresolved candidates, not silently dropped.
AMBIGUOUS_TERMS: dict[str, str] = {
    "华立": "华立科技 / 华立股份",
    "福达": "福达合金 / 福达股份",
    "万通": "万通智控 / 万通发展 / 万通液压",
    "利通": "利通电子 / 利通科技",
    "中天": "中天服务 / 中天精装 / 中天火箭 / 中天科技",
    "锐捷": "星网锐捷 / 锐捷网络",
    "紫光": "紫光股份 / 紫光国微",
    "国光": "国光电器 / 国光股份 / 国光连锁 / 国光电气",
    "立新": "立新能源 / 凯立新材",
}


# Conservative content review of replies that actually link an action-like
# phrase to a candidate target.  All other keyword hits remain in the reply
# dataset but are not elevated into marker targets.  Intent is deliberately
# preserved, including exit/sarcasm counterexamples.
TARGET_SPECS: dict[str, list[TargetSpec]] = {
    "387181": [
        TargetSpec("华立", "manual_review", "unresolved", False, "简称存在多只当前上市标的，保留待复核"),
        TargetSpec("川润", "direct_buy_or_reco", "medium", True, "“值得猛干”语境", "川润股份"),
    ],
    "342524": [TargetSpec("来伊份", "direct_buy_or_reco", "high", True, "“猛干来伊份”")],
    "342541": [TargetSpec("巨轮", "direct_buy_or_reco", "medium", True, "“巨轮必须猛干”", "巨轮智能")],
    "348874": [TargetSpec("鸿博", "direct_buy_or_reco", "medium", True, "“疯狂猛干的鸿博”", "鸿博股份")],
    "348949": [
        TargetSpec("华胜", "conditional_or_reco", "medium", True, "首板套利候选", "华胜天成"),
        TargetSpec("天源迪科", "conditional_or_reco", "high", True, "首板套利候选"),
        TargetSpec("海南华铁", "conditional_or_reco", "high", True, "首板套利候选"),
    ],
    "349228": [TargetSpec("中超", "direct_buy_or_reco", "medium", True, "交易语境下的简称映射；需结合原文复核", "中超控股")],
    "350089": [TargetSpec("京华", "direct_buy_or_reco", "medium", True, "“满仓猛干京华”；以相邻语境辅助", "京华激光")],
    "350143": [TargetSpec("京华激光", "direct_buy_or_reco", "high", True, "“继续猛干京华激光”")],
    "356099": [TargetSpec("中国稀土", "direct_buy_or_reco", "high", True, "“我中国稀土打满”")],
    "356187": [
        TargetSpec("中国稀土", "exit_or_negative", "high", False, "“清仓中国稀土”是反例"),
        TargetSpec("国轩高科", "direct_buy_or_reco", "high", True, "“国轩高科疯狂猛干”"),
    ],
    "356981": [TargetSpec("工业富联", "direct_buy_or_reco", "high", True, "“来一起猛干工业富联”")],
    "363431": [TargetSpec("航天动力", "direct_buy_or_reco", "high", True, "“航天动力打满”")],
    "375213": [TargetSpec("华电辽能", "conditional_or_reco", "high", True, "“这么走…要加仓猛干”")],
    "375885": [TargetSpec("平潭", "direct_buy_or_reco", "medium", True, "“平潭，打满”", "平潭发展")],
    "377215": [TargetSpec("大为", "sarcastic_or_distribution", "medium", False, "“让兄弟们出货”语境，不作正向标记", "大为股份")],
    "378714": [TargetSpec("光迅", "direct_buy_or_reco", "medium", True, "“我今天猛干光迅”", "光迅科技")],
    "378930": [
        TargetSpec("天通", "direct_buy_or_reco", "medium", True, "“我猛干天通利通中天”", "天通股份"),
        TargetSpec("利通", "manual_review", "unresolved", False, "简称多义，不标准化"),
        TargetSpec("中天", "manual_review", "unresolved", False, "简称多义，不标准化"),
    ],
    "382101": [TargetSpec("工业富联", "conditional_or_reco", "high", True, "“大套利可以猛干”")],
    "385552": [TargetSpec("紫光", "manual_review", "unresolved", False, "简称多义，不标准化")],
    "386889": [TargetSpec("云南锗业", "direct_buy_or_reco", "high", True, "“云南锗业打满”")],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT_JSONL, help="public-reply JSONL input")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="output folder")
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(tz=CST).isoformat(timespec="seconds")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                rows.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
    return rows


def cst_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(CST)


def signal_type(text: str) -> str:
    has_menggan = "猛干" in text
    has_daman = "打满" in text
    if has_menggan and has_daman:
        return "猛干+打满"
    return "猛干" if has_menggan else "打满"


def phrase_counts(text: str) -> tuple[int, int]:
    return text.count("猛干"), text.count("打满")


def record_hash(record: dict[str, Any]) -> str:
    material = "|".join(
        str(record.get(key, ""))
        for key in ("replyId", "createdAt", "text", "url")
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalize_name(name: str) -> str:
    return re.sub(r"[\s*]", "", name or "")


def build_stock_lookup(stock_basic: pd.DataFrame) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for row in stock_basic.fillna("").to_dict(orient="records"):
        name = str(row.get("name", "")).strip()
        ticker = str(row.get("ts_code", "")).strip()
        if not name or not ticker:
            continue
        lookup.setdefault(
            normalize_name(name),
            {
                "canonical_name": name,
                "stock_code": ticker,
                "market": str(row.get("market", "")).strip(),
                "list_date": str(row.get("list_date", "")).strip(),
            },
        )
    return lookup


def resolve_entity(
    raw_name: str,
    lookup: dict[str, dict[str, str]],
    *,
    expected_name: str | None = None,
    default_confidence: str = "high",
    note: str = "",
) -> dict[str, str]:
    expected = expected_name or raw_name
    hit = lookup.get(normalize_name(expected))
    if hit:
        method = "exact_current_tushare_name" if normalize_name(raw_name) == normalize_name(expected) else "contextual_alias_to_current_tushare_name"
        return {
            "raw_name": raw_name,
            "canonical_name": hit["canonical_name"],
            "stock_code": hit["stock_code"],
            "market": hit["market"],
            "list_date": hit["list_date"],
            "resolution_method": method,
            "confidence": default_confidence,
            "resolution_note": note,
        }
    return {
        "raw_name": raw_name,
        "canonical_name": "",
        "stock_code": "",
        "market": "",
        "list_date": "",
        "resolution_method": "unresolved_or_ambiguous",
        "confidence": "unresolved",
        "resolution_note": note or "未在当前 Tushare stock_basic 中安全匹配",
    }


def extract_candidate_entities(text: str, lookup: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    """Find full current names, then manually curated abbreviations.

    This deliberately does not attempt NLP entity prediction.  It produces a
    transparent candidate inventory instead of pretending every noun is a
    stock or every stock mention is an intended target.
    """

    matches: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    current_names = sorted(lookup.values(), key=lambda item: len(item["canonical_name"]), reverse=True)
    for stock in current_names:
        name = stock["canonical_name"]
        if len(normalize_name(name)) < 3 or name not in text:
            continue
        if stock["stock_code"] in seen_codes:
            continue
        seen_codes.add(stock["stock_code"])
        matches.append(
            {
                "raw_name": name,
                "canonical_name": name,
                "stock_code": stock["stock_code"],
                "market": stock["market"],
                "list_date": stock["list_date"],
                "resolution_method": "exact_current_tushare_name",
                "confidence": "high",
                "resolution_note": "原文与当前 Tushare 名称完全匹配",
            },
        )

    for raw_name, alias in ALIASES.items():
        if raw_name not in text:
            continue
        resolved = resolve_entity(
            raw_name,
            lookup,
            expected_name=alias.expected_name,
            default_confidence=alias.confidence,
            note=alias.note,
        )
        identity = resolved["stock_code"] or f"unresolved:{raw_name}"
        if identity in seen_codes:
            continue
        seen_codes.add(identity)
        matches.append(resolved)
    for raw_name, choices in AMBIGUOUS_TERMS.items():
        if raw_name not in text:
            continue
        identity = f"unresolved:{raw_name}"
        if identity in seen_codes:
            continue
        seen_codes.add(identity)
        matches.append(
            {
                "raw_name": raw_name,
                "canonical_name": "",
                "stock_code": "",
                "market": "",
                "list_date": "",
                "resolution_method": "unresolved_ambiguous",
                "confidence": "unresolved",
                "resolution_note": f"简称可能对应：{choices}",
            },
        )
    return matches


def align_trade_date(posted: datetime, trading_days: list[pd.Timestamp]) -> str:
    date = pd.Timestamp(posted.date())
    if date in set(trading_days) and posted.timetz().replace(tzinfo=None) < SESSION_CLOSE:
        return date.strftime("%Y-%m-%d")
    for trade_day in trading_days:
        if trade_day > date:
            return trade_day.strftime("%Y-%m-%d")
    return ""


def write_csv(rows: list[dict[str, Any]], path: Path, fieldnames: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_audit_sqlite(
    output_dir: Path,
    reply_rows: list[dict[str, Any]],
    entity_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
    timeline_rows: list[dict[str, Any]],
    index_rows: pd.DataFrame,
    stats: dict[str, Any],
) -> None:
    """Materialize the report's source tables for executable SQLite provenance."""

    database_path = output_dir / "analysis.sqlite"
    with sqlite3.connect(database_path) as connection:
        pd.DataFrame(reply_rows).to_sql("reply_signals", connection, if_exists="replace", index=False)
        pd.DataFrame(entity_rows).to_sql("entity_signal_candidates", connection, if_exists="replace", index=False)
        pd.DataFrame(target_rows).to_sql("target_signal_records", connection, if_exists="replace", index=False)
        pd.DataFrame(timeline_rows).to_sql("signal_timeline", connection, if_exists="replace", index=False)
        index_export = index_rows.copy()
        index_export["date"] = pd.to_datetime(index_export["date"]).dt.strftime("%Y-%m-%d")
        index_export.rename(columns={"date": "trade_date"}).to_sql("sse_index_daily", connection, if_exists="replace", index=False)
        pd.DataFrame([stats]).to_sql("summary_metrics", connection, if_exists="replace", index=False)


def line_excerpt(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def load_chinese_font() -> FontProperties | None:
    candidates = [
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            font = FontProperties(fname=str(candidate))
            plt.rcParams["font.sans-serif"] = [font.get_name()]
            plt.rcParams["axes.unicode_minus"] = False
            return font
    return None


def plot_kline(
    index_df: pd.DataFrame,
    timeline_df: pd.DataFrame,
    output_png: Path,
    output_svg: Path,
    target_event_dates: set[str],
) -> None:
    font = load_chinese_font()
    font_kw: dict[str, Any] = {"fontproperties": font} if font else {}
    index_df = index_df.sort_values("date").copy()
    index_df["date"] = pd.to_datetime(index_df["date"])
    date_nums = mdates.date2num(index_df["date"].dt.to_pydatetime())
    timeline_by_date = timeline_df.set_index("trade_date").to_dict(orient="index")

    fig = plt.figure(figsize=(19, 10.8), dpi=155, facecolor="white")
    grid = fig.add_gridspec(5, 1, height_ratios=[4.4, 0.05, 1.25, 0.05, 0.38], hspace=0.10)
    ax = fig.add_subplot(grid[0])
    ax_events = fig.add_subplot(grid[2], sharex=ax)
    ax_note = fig.add_subplot(grid[4])
    ax_note.axis("off")

    up_color = "#C2413B"
    down_color = "#2E8B71"
    for x, (_, row) in zip(date_nums, index_df.iterrows()):
        open_price = float(row["open"])
        close_price = float(row["close"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        color = up_color if close_price >= open_price else down_color
        ax.vlines(x, low_price, high_price, color=color, linewidth=0.72, zorder=1)
        body_bottom = min(open_price, close_price)
        body_height = abs(close_price - open_price)
        if body_height < 0.75:
            body_bottom -= 0.375
            body_height = 0.75
        ax.add_patch(
            Rectangle(
                (x - 0.30, body_bottom),
                0.60,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.35,
                zorder=2,
            ),
        )

    price_span = float(index_df["high"].max() - index_df["low"].min())
    base_low = float(index_df["low"].min())
    all_marker_y = base_low - price_span * 0.065
    target_marker_y = base_low - price_span * 0.023
    category_colors = {"猛干": "#E0912D", "打满": "#7B5EA7", "猛干+打满": "#A7503C"}
    category_labels = {"猛干": "关键词：猛干", "打满": "关键词：打满", "猛干+打满": "两词同现"}
    legend_seen: set[str] = set()
    target_xs: list[float] = []
    for _, row in index_df.iterrows():
        day_text = row["date"].strftime("%Y-%m-%d")
        payload = timeline_by_date.get(day_text)
        if not payload or int(payload["keyword_reply_count"]) == 0:
            continue
        x = mdates.date2num(row["date"].to_pydatetime())
        category = payload["signal_type"]
        label = category_labels[category] if category not in legend_seen else None
        legend_seen.add(category)
        ax.scatter(
            x,
            all_marker_y,
            s=18 + int(payload["keyword_reply_count"]) * 19,
            color=category_colors[category],
            marker="o",
            alpha=0.92,
            linewidths=0.25,
            edgecolors="white",
            label=label,
            zorder=6,
        )
        if day_text in target_event_dates:
            target_xs.append(x)

    if target_xs:
        ax.scatter(
            target_xs,
            [target_marker_y] * len(target_xs),
            s=50,
            facecolors="white",
            edgecolors="#173F5F",
            linewidths=1.35,
            marker="D",
            label="已复核目标语境（含直接/条件式）",
            zorder=7,
        )

    ax.set_title("上证指数日K线与“猛干/打满”公开语料事件", fontsize=18, fontweight="bold", pad=18, **font_kw)
    ax.text(
        0.5,
        1.01,
        "圆点＝全部关键词回复日（大小＝当日回复数）；空心菱形＝经语境复核、且可标准化为个股的目标表达。",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=10.5,
        color="#4B5563",
        **font_kw,
    )
    ax.set_ylabel("上证指数点位", fontsize=10.5, **font_kw)
    ax.set_ylim(all_marker_y - price_span * 0.04, float(index_df["high"].max()) + price_span * 0.06)
    ax.grid(axis="y", color="#E5E7EB", linewidth=0.65)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="x", labelbottom=False)

    for category, color in category_colors.items():
        subset = timeline_df[timeline_df["signal_type"] == category]
        if subset.empty:
            continue
        dates = pd.to_datetime(subset["trade_date"])
        ax_events.bar(
            dates,
            subset["keyword_reply_count"].astype(float),
            width=0.7,
            color=color,
            alpha=0.86,
            label=category_labels[category],
        )
    target_daily = timeline_df[timeline_df["target_marker_reply_count"] > 0]
    if not target_daily.empty:
        dates = pd.to_datetime(target_daily["trade_date"])
        y = target_daily["keyword_reply_count"].astype(float) + 0.20
        ax_events.scatter(
            dates,
            y,
            s=36,
            marker="D",
            color="#173F5F",
            edgecolors="white",
            linewidths=0.7,
            label="含复核目标语境",
            zorder=5,
        )
    ax_events.set_ylabel("当日回复数", fontsize=10, **font_kw)
    ax_events.set_ylim(0, max(1.5, float(timeline_df["keyword_reply_count"].max()) + 1.05))
    ax_events.grid(axis="y", color="#E5E7EB", linewidth=0.65)
    ax_events.spines[["top", "right"]].set_visible(False)
    ax_events.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax_events.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    for label in ax_events.get_xticklabels():
        label.set_rotation(0)
        if font:
            label.set_fontproperties(font)

    handles, labels = ax.get_legend_handles_labels()
    handles2, labels2 = ax_events.get_legend_handles_labels()
    unique: dict[str, Any] = {}
    for handle, label in zip(handles + handles2, labels + labels2):
        unique.setdefault(label, handle)
    ax.legend(
        unique.values(),
        unique.keys(),
        loc="upper left",
        frameon=False,
        ncol=2,
        prop=font,
        fontsize=9.5,
    )
    ax_note.text(
        0.5,
        0.32,
        "时间口径：发帖日在交易日 15:00 前对齐当日，否则对齐下一交易日；标记为语料出现时间，不是成交记录、收益归因或投资建议。",
        ha="center",
        va="center",
        color="#4B5563",
        fontsize=9.3,
        **font_kw,
    )
    fig.savefig(output_png, bbox_inches="tight", facecolor="white")
    fig.savefig(output_svg, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def make_report_artifact(
    output_dir: Path,
    index_df: pd.DataFrame,
    timeline_df: pd.DataFrame,
    reply_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    image_path: Path,
) -> dict[str, Any]:
    png_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    image_data_uri = f"data:image/png;base64,{png_data}"
    generated_at = now_iso()
    report_table_rows = []
    for row in target_rows:
        report_table_rows.append(
            {
                "signal_date": row["message_date_cst"],
                "aligned_trade_date": row["effective_trade_date"],
                "stock_name": row["canonical_name"] or "未标准化",
                "stock_code": row["stock_code"] or "—",
                "intent": row["intent"],
                "confidence": row["confidence"],
                "raw_name": row["raw_name"],
                "sentence": line_excerpt(row["raw_text"], 145),
                "source_url": row["source_url"],
                "chart_marker": "是" if row["chart_marker"] else "否",
            },
        )

    index_dataset = []
    timeline_lookup = timeline_df.set_index("trade_date").to_dict(orient="index")
    for _, row in index_df.iterrows():
        trade_date = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
        event = timeline_lookup.get(trade_date, {})
        index_dataset.append(
            {
                "trade_date": trade_date,
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": round(float(row["volume"]), 2),
                "amount": round(float(row["amount"]), 2),
                "keyword_reply_count": int(event.get("keyword_reply_count", 0)),
                "target_marker_reply_count": int(event.get("target_marker_reply_count", 0)),
                "signal_type": event.get("signal_type", "无"),
            },
        )

    summary_metrics = [
        {
            "keyword_reply_hits": stats["keyword_reply_hits"],
            "keyword_event_days": stats["keyword_event_days"],
            "keyword_aligned_trade_days": stats["keyword_aligned_trade_days"],
            "reviewed_target_rows": stats["reviewed_target_rows"],
            "chart_marker_replies": stats["chart_marker_replies"],
        },
    ]
    source_inventory = [
        {
            "id": "signal_summary",
            "label": "关键词事件汇总指标",
            "path": "analysis.sqlite",
            "query": {
                "engine": "sqlite",
                "sql": "SELECT keyword_reply_hits, keyword_event_days, keyword_aligned_trade_days, reviewed_target_rows, chart_marker_replies, chart_marker_target_rows, standardized_target_rows FROM summary_metrics",
                "description": "本次关键词事件提取和语境复核的汇总指标。",
                "tables_used": ["summary_metrics"],
                "filters": ["关键词：猛干、打满"],
            },
        },
        {
            "id": "hupu_reply_signals",
            "label": "虎扑公开回帖关键词事件记录",
            "path": "analysis.sqlite",
            "query": {
                "engine": "sqlite",
                "sql": "SELECT reply_id, message_date_cst, effective_trade_date, signal_type, source_url, raw_text FROM reply_signals WHERE instr(raw_text, '猛干') > 0 OR instr(raw_text, '打满') > 0",
                "description": "从本地公开回帖导出中筛选包含‘猛干’或‘打满’的回复，并保留原文链接和时间。",
                "tables_used": ["reply_signals"],
                "filters": ["关键词：猛干、打满", "公开可见回复"],
            },
        },
        {
            "id": "tushare_sse_index_daily",
            "label": "Tushare 上证指数日线（000001.SH）",
            "path": "analysis.sqlite",
            "query": {
                "engine": "sqlite",
                "sql": "SELECT trade_date, open, high, low, close, volume, amount FROM sse_index_daily WHERE ticker = '000001.SH' ORDER BY trade_date",
                "description": "Tushare 000001.SH 上证指数日线快照。",
                "tables_used": ["sse_index_daily"],
                "filters": ["日期：2024-08-19 至 2026-08-19", "ticker：000001.SH"],
            },
        },
        {
            "id": "market_signal_join",
            "label": "上证指数与关键词事件对齐记录",
            "path": "analysis.sqlite",
            "query": {
                "engine": "sqlite",
                "sql": "SELECT i.trade_date, i.open, i.high, i.low, i.close, i.volume, i.amount, COALESCE(t.keyword_reply_count, 0) AS keyword_reply_count, COALESCE(t.target_marker_reply_count, 0) AS target_marker_reply_count, COALESCE(t.signal_type, '无') AS signal_type FROM sse_index_daily AS i LEFT JOIN signal_timeline AS t ON i.trade_date = t.trade_date WHERE i.ticker = '000001.SH' ORDER BY i.trade_date",
                "description": "将上证指数日线与按交易日对齐的关键词事件连接，用于 K 线和趋势参考。",
                "tables_used": ["sse_index_daily", "signal_timeline"],
                "filters": ["交易日对齐：15:00 前为当日，否则下一个交易日"],
            },
        },
        {
            "id": "reviewed_target_records",
            "label": "语境复核后的个股候选记录",
            "path": "analysis.sqlite",
            "query": {
                "engine": "sqlite",
                "sql": "SELECT reply_id, message_date_cst, effective_trade_date, raw_name, canonical_name, stock_code, confidence, intent, chart_marker, source_url, raw_text FROM target_signal_records ORDER BY message_date_cst, reply_id",
                "description": "对“个股 + 行为”候选做语境复核，保留正向、条件式与反例。",
                "tables_used": ["target_signal_records"],
                "filters": ["仅人工语境复核的候选记录"],
            },
        },
    ]
    custom_chart_html = (
        '<figure style="margin:0">'
        f'<img src="{image_data_uri}" alt="上证指数日K线。圆点显示所有含猛干或打满的公开回复日，空心菱形显示经语境复核且可标准化为个股的目标表达。" '
        'style="display:block;width:100%;height:auto;border:1px solid #e5e7eb;border-radius:10px" />'
        '<figcaption style="margin-top:8px;color:#4b5563;font-size:13px">'
        "图中标记用于复盘公开语料的出现时点；详尽日期、个股标准化结果和原文链接见下表与附随 CSV。"
        "</figcaption></figure>"
    )
    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": "上证指数与“猛干/打满”公开语料事件",
            "description": "关键词事件提取、个股标准化和指数日线对齐的可审计复盘。",
            "generatedAt": generated_at,
            "sources": source_inventory,
            "cards": [
                {
                    "id": "keyword_reply_hits",
                    "description": "公开回复中包含“猛干”或“打满”的回复条数。",
                    "dataset": "summary_metrics",
                    "sourceId": "signal_summary",
                    "metrics": [{"label": "关键词命中回复", "field": "keyword_reply_hits", "format": "number"}],
                },
                {
                    "id": "keyword_event_days",
                    "description": "关键词命中回复覆盖的自然日数。",
                    "dataset": "summary_metrics",
                    "sourceId": "signal_summary",
                    "metrics": [{"label": "关键词出现日", "field": "keyword_event_days", "format": "number"}],
                },
                {
                    "id": "reviewed_target_rows",
                    "description": "人工语境复核后保留的候选个股记录，含反例与未标准化项。",
                    "dataset": "summary_metrics",
                    "sourceId": "signal_summary",
                    "metrics": [{"label": "复核候选记录", "field": "reviewed_target_rows", "format": "number"}],
                },
                {
                    "id": "chart_marker_replies",
                    "description": "可标准化且属于直接或条件式正向目标表达的回复数。",
                    "dataset": "summary_metrics",
                    "sourceId": "signal_summary",
                    "metrics": [{"label": "图中复核标记回复", "field": "chart_marker_replies", "format": "number"}],
                },
            ],
            "charts": [
                {
                    "id": "sh_close_trend",
                    "title": "上证指数收盘趋势",
                    "subtitle": "日线收盘点位；关键词标记详见上方 K 线图。",
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
                            {"field": "keyword_reply_count", "type": "quantitative", "label": "关键词回复数"},
                            {"field": "target_marker_reply_count", "type": "quantitative", "label": "复核标记回复数"},
                        ],
                    },
                },
            ],
            "tables": [
                {
                    "id": "reviewed_target_table",
                    "title": "语境复核后的个股候选记录",
                    "subtitle": "保留原句摘录、原始简称、标准化结果和反例语境，便于逐条回看。",
                    "dataset": "reviewed_target_records",
                    "sourceId": "reviewed_target_records",
                    "defaultSort": {"field": "signal_date", "direction": "asc"},
                    "columns": [
                        {"field": "signal_date", "label": "发帖日", "type": "date"},
                        {"field": "aligned_trade_date", "label": "图表对齐日", "type": "date"},
                        {"field": "stock_name", "label": "标准名称", "type": "text"},
                        {"field": "stock_code", "label": "代码", "type": "text"},
                        {"field": "intent", "label": "语境", "type": "text"},
                        {"field": "confidence", "label": "置信度", "type": "text"},
                        {"field": "sentence", "label": "原句摘录", "type": "text"},
                        {"field": "source_url", "label": "原文链接", "type": "text"},
                        {"field": "chart_marker", "label": "图中标记", "type": "text"},
                    ],
                },
            ],
            "blocks": [
                {
                    "id": "title",
                    "type": "markdown",
                    "body": "# 上证指数与“猛干/打满”公开语料事件",
                },
                {
                    "id": "executive_summary",
                    "type": "markdown",
                    "sourceId": "signal_summary",
                    "body": (
                        "## Executive Summary\n\n"
                        f"- 在已导出的公开回复中，**{stats['keyword_reply_hits']}** 条含“猛干”或“打满”，覆盖 **{stats['keyword_event_days']}** 个自然日。\n"
                        f"- 我没有把关键词本身等同于买入信号：仅对 **{stats['reviewed_target_rows']}** 条“个股 + 行为”候选逐条保留语境；其中 **{stats['chart_marker_replies']}** 条回复满足“可标准化个股 + 直接或条件式正向表达”，才在图中加深标记。\n"
                        "- 该结果用于复盘公开文本的事件出现时间。它不验证实际成交、持仓、收益，也不构成投资建议。"
                    ),
                },
                {"id": "metric_strip", "type": "metric-strip", "cardIds": ["keyword_reply_hits", "keyword_event_days", "reviewed_target_rows", "chart_marker_replies"]},
                {
                    "id": "methodology",
                    "type": "markdown",
                    "sourceId": "reviewed_target_records",
                    "body": (
                        "## 提取与时间对齐口径\n\n"
                        "先保留全部关键词命中，再分离出需要人工语境复核的候选个股。代码只在当前 Tushare `stock_basic` 可交叉校验时填入；简称、错别字或同名歧义会保留原词并降级置信度。\n\n"
                        "图表对齐日并非实际交易日：交易日 15:00 前的发帖对齐当日，收盘后或非交易日对齐下一交易日。清仓、讽刺性“出货”等反例被保留在表中，但不会被绘成正向目标标记。"
                    ),
                },
                {
                    "id": "kline_explanation",
                    "type": "markdown",
                    "sourceId": "market_signal_join",
                    "body": (
                        "## 日K与关键词事件\n\n"
                        "K 线给出指数环境，底部圆点显示所有关键词出现日；空心菱形只显示语境复核后、可标准化为个股且属于直接/条件式正向表达的事件。两类标记的差异避免把“资金在猛干”“清仓”或玩笑话直接当作操作。"
                    ),
                },
                {"id": "kline_chart", "type": "html", "body": custom_chart_html, "sourceId": "market_signal_join"},
                {
                    "id": "close_context",
                    "type": "markdown",
                    "sourceId": "market_signal_join",
                    "body": (
                        "## 收盘趋势参考\n\n"
                        "下图以收盘点位提供可缩放的趋势参考。悬停可查看当日 OHLC 与已对齐的关键词/复核事件数量；具体语句和个股映射仍以表格和 CSV 为准。"
                    ),
                },
                {"id": "close_trend_chart", "type": "chart", "chartId": "sh_close_trend"},
                {
                    "id": "target_table_explanation",
                    "type": "markdown",
                    "sourceId": "reviewed_target_records",
                    "body": (
                        "## 个股候选审计表\n\n"
                        "表中同时保存正向、条件式、退出和讽刺反例。请以原句与原文链接复核含义；“中置信度”通常指简称需依赖上下文，而不是名称本身的当前 Tushare 匹配。"
                    ),
                },
                {"id": "target_table", "type": "table", "tableId": "reviewed_target_table"},
                {
                    "id": "caveats",
                    "type": "markdown",
                    "body": (
                        "## 使用边界\n\n"
                        "公开接口可见的历史从 2024 年 9 月下旬开始，早于该时间的两年窗口部分无法确认是否因无发言或平台历史边界所致。"
                        "Tushare `stock_basic` 是当前名称/代码快照，历史更名或简称歧义须以原文和当日行情进一步复核。"
                    ),
                },
            ],
        },
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "summary_metrics": summary_metrics,
                "sh_index_daily": index_dataset,
                "reviewed_target_records": report_table_rows,
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
    keyword_rows = [row for row in raw_rows if "猛干" in str(row.get("text", "")) or "打满" in str(row.get("text", ""))]
    if not keyword_rows:
        raise ValueError("No keyword rows found")

    client = TushareDailyClient(TushareConfig.from_env())
    index_df = client.fetch_index_daily("000001.SH", START_DATE, END_DATE)
    stock_basic = client.fetch_stock_basic(fields="ts_code,name,market,list_date")
    if index_df.empty or stock_basic.empty:
        raise RuntimeError("Tushare returned empty required data")
    index_df = index_df.sort_values("date").reset_index(drop=True)
    stock_lookup = build_stock_lookup(stock_basic)
    trading_days = [pd.Timestamp(item).normalize() for item in index_df["date"].tolist()]

    reply_rows: list[dict[str, Any]] = []
    entities_by_reply: dict[str, list[dict[str, str]]] = {}
    reply_map: dict[str, dict[str, Any]] = {}
    for raw in keyword_rows:
        text = str(raw.get("text", ""))
        reply_id = str(raw.get("replyId", ""))
        posted = cst_datetime(str(raw["createdAt"]))
        menggan_count, daman_count = phrase_counts(text)
        candidates = extract_candidate_entities(text, stock_lookup)
        entities_by_reply[reply_id] = candidates
        row = {
            "reply_id": reply_id,
            "thread_id": str(raw.get("threadId", "")),
            "created_at_utc": str(raw.get("createdAt", "")),
            "posted_at_cst": posted.isoformat(timespec="seconds"),
            "message_date_cst": posted.date().isoformat(),
            "effective_trade_date": align_trade_date(posted, trading_days),
            "signal_type": signal_type(text),
            "menggan_occurrences": menggan_count,
            "daman_occurrences": daman_count,
            "source_url": str(raw.get("url", "")),
            "raw_text": text,
            "record_hash": record_hash(raw),
            "candidate_stock_count": len(candidates),
            "candidate_stocks": "；".join(
                f"{item['raw_name']}→{item['canonical_name'] or '未标准化'}({item['stock_code'] or '—'})"
                for item in candidates
            ),
        }
        reply_rows.append(row)
        reply_map[reply_id] = row

    entity_rows: list[dict[str, Any]] = []
    for reply in reply_rows:
        for entity in entities_by_reply[reply["reply_id"]]:
            entity_rows.append(
                {
                    "reply_id": reply["reply_id"],
                    "message_date_cst": reply["message_date_cst"],
                    "effective_trade_date": reply["effective_trade_date"],
                    "signal_type": reply["signal_type"],
                    "raw_name": entity["raw_name"],
                    "canonical_name": entity["canonical_name"],
                    "stock_code": entity["stock_code"],
                    "market": entity["market"],
                    "list_date": entity["list_date"],
                    "resolution_method": entity["resolution_method"],
                    "confidence": entity["confidence"],
                    "entity_role": "mention_context_unreviewed",
                    "intent": "unreviewed_keyword_context",
                    "resolution_note": entity["resolution_note"],
                    "source_url": reply["source_url"],
                    "raw_text": reply["raw_text"],
                },
            )

    target_rows: list[dict[str, Any]] = []
    for reply_id, specs in TARGET_SPECS.items():
        reply = reply_map.get(reply_id)
        if not reply:
            raise RuntimeError(f"Reviewed target reply {reply_id} is absent from keyword corpus")
        for spec in specs:
            resolved = resolve_entity(
                spec.raw_name,
                stock_lookup,
                expected_name=spec.expected_name,
                default_confidence=spec.confidence,
                note=spec.note,
            )
            chart_marker = bool(spec.chart_marker and resolved["stock_code"])
            target_rows.append(
                {
                    "reply_id": reply_id,
                    "thread_id": reply["thread_id"],
                    "message_date_cst": reply["message_date_cst"],
                    "posted_at_cst": reply["posted_at_cst"],
                    "effective_trade_date": reply["effective_trade_date"],
                    "trigger_terms": reply["signal_type"],
                    "raw_name": spec.raw_name,
                    "canonical_name": resolved["canonical_name"],
                    "stock_code": resolved["stock_code"],
                    "market": resolved["market"],
                    "list_date": resolved["list_date"],
                    "resolution_method": resolved["resolution_method"],
                    "confidence": resolved["confidence"],
                    "entity_role": "target" if spec.intent not in {"exit_or_negative", "sarcastic_or_distribution"} else "mentioned_counterexample",
                    "intent": spec.intent,
                    "chart_marker": chart_marker,
                    "review_note": spec.note,
                    "source_url": reply["source_url"],
                    "raw_text": reply["raw_text"],
                },
            )

    # Include reviewed target tags in the generic entity inventory so every
    # target row has a companion entity-level audit record.
    entity_rows.extend(
        {
            "reply_id": row["reply_id"],
            "message_date_cst": row["message_date_cst"],
            "effective_trade_date": row["effective_trade_date"],
            "signal_type": row["trigger_terms"],
            "raw_name": row["raw_name"],
            "canonical_name": row["canonical_name"],
            "stock_code": row["stock_code"],
            "market": row["market"],
            "list_date": row["list_date"],
            "resolution_method": row["resolution_method"],
            "confidence": row["confidence"],
            "entity_role": row["entity_role"],
            "intent": row["intent"],
            "resolution_note": row["review_note"],
            "source_url": row["source_url"],
            "raw_text": row["raw_text"],
        }
        for row in target_rows
    )

    daily_reply_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in reply_rows:
        if row["effective_trade_date"]:
            daily_reply_map[row["effective_trade_date"]].append(row)
    marker_replies_by_day: dict[str, set[str]] = defaultdict(set)
    for row in target_rows:
        if row["chart_marker"] and row["effective_trade_date"]:
            marker_replies_by_day[row["effective_trade_date"]].add(row["reply_id"])

    timeline_rows: list[dict[str, Any]] = []
    for trade_day in trading_days:
        date_text = trade_day.strftime("%Y-%m-%d")
        rows = daily_reply_map.get(date_text, [])
        phrase_presence = {item["signal_type"] for item in rows}
        if not rows:
            day_type = "无"
        elif any(item["signal_type"] == "猛干+打满" for item in rows) or {"猛干", "打满"}.issubset(phrase_presence):
            day_type = "猛干+打满"
        elif "猛干" in phrase_presence:
            day_type = "猛干"
        else:
            day_type = "打满"
        timeline_rows.append(
            {
                "trade_date": date_text,
                "keyword_reply_count": len(rows),
                "menggan_reply_count": sum("猛干" in item["signal_type"] for item in rows),
                "daman_reply_count": sum("打满" in item["signal_type"] for item in rows),
                "target_marker_reply_count": len(marker_replies_by_day.get(date_text, set())),
                "signal_type": day_type,
            },
        )
    timeline_df = pd.DataFrame(timeline_rows)

    all_keyword_aligned_trade_days = sum(1 for row in timeline_rows if row["keyword_reply_count"] > 0)
    all_keyword_natural_days = len({row["message_date_cst"] for row in reply_rows})
    marker_reply_ids = {row["reply_id"] for row in target_rows if row["chart_marker"]}
    stats = {
        "source_reply_count": len(raw_rows),
        "keyword_reply_hits": len(reply_rows),
        "keyword_event_days": all_keyword_natural_days,
        "keyword_aligned_trade_days": all_keyword_aligned_trade_days,
        "menggan_reply_hits": sum("猛干" in row["signal_type"] for row in reply_rows),
        "daman_reply_hits": sum("打满" in row["signal_type"] for row in reply_rows),
        "both_terms_reply_hits": sum(row["signal_type"] == "猛干+打满" for row in reply_rows),
        "reviewed_target_rows": len(target_rows),
        "chart_marker_replies": len(marker_reply_ids),
        "chart_marker_target_rows": sum(row["chart_marker"] for row in target_rows),
        "standardized_target_rows": sum(bool(row["stock_code"]) for row in target_rows),
        "generated_at": now_iso(),
    }

    # Persist independently usable audit tables.
    reply_fieldnames = [
        "reply_id", "thread_id", "created_at_utc", "posted_at_cst", "message_date_cst", "effective_trade_date",
        "signal_type", "menggan_occurrences", "daman_occurrences", "candidate_stock_count", "candidate_stocks",
        "source_url", "raw_text", "record_hash",
    ]
    entity_fieldnames = [
        "reply_id", "message_date_cst", "effective_trade_date", "signal_type", "raw_name", "canonical_name", "stock_code",
        "market", "list_date", "resolution_method", "confidence", "entity_role", "intent", "resolution_note", "source_url", "raw_text",
    ]
    target_fieldnames = [
        "reply_id", "thread_id", "message_date_cst", "posted_at_cst", "effective_trade_date", "trigger_terms", "raw_name",
        "canonical_name", "stock_code", "market", "list_date", "resolution_method", "confidence", "entity_role", "intent",
        "chart_marker", "review_note", "source_url", "raw_text",
    ]
    write_csv(reply_rows, output_dir / "reply_signals.csv", reply_fieldnames)
    write_csv(entity_rows, output_dir / "entity_signal_candidates.csv", entity_fieldnames)
    write_csv(target_rows, output_dir / "target_signal_records.csv", target_fieldnames)
    write_csv(timeline_rows, output_dir / "signal_timeline.csv", list(timeline_rows[0].keys()))

    index_export = index_df.copy()
    index_export["date"] = pd.to_datetime(index_export["date"]).dt.strftime("%Y-%m-%d")
    index_export.to_csv(output_dir / "sse_000001_sh_daily.csv", index=False, encoding="utf-8-sig")

    target_event_dates = {
        row["effective_trade_date"]
        for row in target_rows
        if row["chart_marker"] and row["effective_trade_date"]
    }
    chart_png = output_dir / "sh_index_kline_keyword_markers.png"
    chart_svg = output_dir / "sh_index_kline_keyword_markers.svg"
    plot_kline(index_df, timeline_df, chart_png, chart_svg, target_event_dates)

    write_audit_sqlite(output_dir, reply_rows, entity_rows, target_rows, timeline_rows, index_df, stats)
    artifact = make_report_artifact(output_dir, index_df, timeline_df, reply_rows, target_rows, stats, chart_png)
    write_json(output_dir / "artifact.json", artifact)
    source_manifest = {
        "generated_at": stats["generated_at"],
        "input": {
            "dataset": "hupu_real_xielaoban public-reply export",
            "source_reply_count": len(raw_rows),
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "keyword_filter": ["猛干", "打满"],
        },
        "market_data": {
            "provider": "Tushare",
            "ticker": "000001.SH",
            "requested_date_range": ["2024-08-19", "2026-08-19"],
            "daily_rows": len(index_df),
            "stock_basic_rows": len(stock_basic),
        },
        "stats": stats,
        "alignment_rule": "交易日 15:00 前对齐当日；交易日收盘后或非交易日对齐下一交易日。",
        "marker_rule": "仅当人工复核为 direct_buy_or_reco 或 conditional_or_reco，且 Tushare 标准化有有效代码时标记。",
        "files": [
            "reply_signals.csv",
            "entity_signal_candidates.csv",
            "target_signal_records.csv",
            "signal_timeline.csv",
            "sse_000001_sh_daily.csv",
            "analysis.sqlite",
            "sh_index_kline_keyword_markers.png",
            "sh_index_kline_keyword_markers.svg",
            "artifact.json",
        ],
    }
    write_json(output_dir / "source_manifest.json", source_manifest)
    methodology = """# “猛干 / 打满”语料事件：方法与文件说明

## 口径

- `reply_signals.csv`：所有包含“猛干”或“打满”的公开回复；不等同于买入信号。
- `entity_signal_candidates.csv`：从命中回复中找到的当前 Tushare 名称或透明的简称映射；`mention_context_unreviewed` 不代表目标个股。
- `target_signal_records.csv`：逐条语境复核后的候选目标、条件表达以及清仓/讽刺反例。仅 `chart_marker=true` 的记录被绘入深色菱形标记。
- `effective_trade_date`：用于和日K对齐的日期，交易日 15:00 前为当日，其余为下一交易日；并非真实成交日。

## 个股标准化

原句中没有显式六位证券代码。代码来自当前 Tushare `stock_basic` 快照；全名原样匹配为高置信度，社区简称为中/低置信度。未安全匹配的候选（例如“中超”）保留原词且不写代码。

## 图表

`sh_index_kline_keyword_markers.png` / `.svg` 使用 Tushare `000001.SH` 日线。圆点表示全部关键词出现日；空心菱形表示已经过语境复核、且可标准化为个股的直接或条件式正向表达。标记不是交易建议、成交记录、持仓证明或收益归因。
"""
    (output_dir / "README.md").write_text(methodology, encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "stats": stats}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
