#!/usr/bin/env python3
"""紫阳东路公开买榜与指数环境研究示例。

脚本只生成研究底稿和描述性图表，不输出交易结论。

主要产物：
1. 按交易日拉取 top_inst，筛选并去重紫阳东路公开买榜记录；
2. 对事件日期补充 top_list 与指数日线；
3. 用当日及此前数据计算指数三形态，避免时间穿越；
4. 对大额样本读取个股和指数历史分钟线；
5. 导出 Excel、时间轴图和重点样本图。

运行示例（从项目根目录执行）：

    ./.venv312/bin/python \
      research/ziyang_index_environment/analyze_ziyang_index_example.py \
      --start-date 20260621 \
      --end-date 20260720 \
      --benchmarks 000001.SH,000300.SH \
      --minute-event-limit 5

说明：
- 令牌从项目配置或环境变量读取，不写入本脚本；
- 默认使用教程推荐的 https://fast.xiaodefa.cn；
- end-date 同时是本次数据截止日，脚本不会查询更晚日期；
- T+1/T+5 仅用于图表中的后验描述，所有形态字段只使用 T 日及此前数据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import tushare as ts

try:
    import matplotlib.pyplot as plt
except ModuleNotFoundError:  # 项目主依赖未包含 matplotlib，Excel 产物仍可正常生成
    plt = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shilun.common.config import load_config  # noqa: E402


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "outputs" / "example_run"
DEFAULT_GATEWAY = "https://fast.xiaodefa.cn"
CANONICAL_BRANCH = "国泰海通证券股份有限公司武汉紫阳东路证券营业部"
BRANCH_ALIASES = (
    "国泰海通证券股份有限公司武汉紫阳东路证券营业部",
    "国泰君安证券股份有限公司武汉紫阳东路证券营业部",
)
WINDOW_REASON_KEYWORDS = (
    "连续三个交易日",
    "连续3个交易日",
    "三个交易日内",
    "3个交易日内",
    "严重异常波动",
)
SHAPE_COLORS = {
    "M_UP": "#D85845",
    "M_RANGE": "#C6A15B",
    "M_DOWN": "#4E7C9E",
    "UNKNOWN": "#A0A0A0",
}

if plt is not None:
    plt.rcParams["font.sans-serif"] = [
        "PingFang SC",
        "Arial Unicode MS",
        "Heiti SC",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


@dataclass(frozen=True)
class RunConfig:
    start_date: str
    end_date: str
    benchmarks: tuple[str, ...]
    gateway: str
    min_interval_seconds: float
    minute_freq: str
    minute_event_limit: int
    output_dir: Path
    refresh: bool
    max_retries: int


class GatewayClient:
    """带最小调用间隔和有限重试的 Tushare 代理客户端。"""

    def __init__(
        self,
        *,
        token: str,
        gateway: str,
        min_interval_seconds: float,
        max_retries: int,
    ) -> None:
        if not token:
            raise RuntimeError(
                "未找到 Tushare token。请设置 SHILUN_TUSHARE_TOKEN 或 TUSHARE_TOKEN。"
            )
        self.pro = ts.pro_api(token)
        self.pro._DataApi__http_url = gateway.rstrip("/")
        self.min_interval_seconds = max(0.55, float(min_interval_seconds))
        self.max_retries = max(1, int(max_retries))
        self._last_call_at = 0.0

    def query(self, endpoint: str, **params: Any) -> pd.DataFrame:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            elapsed = time.monotonic() - self._last_call_at
            if elapsed < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - elapsed)
            try:
                frame = self.pro.query(endpoint, **params)
                self._last_call_at = time.monotonic()
                return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
            except Exception as exc:  # noqa: BLE001 - 数据网关错误需保留原始信息
                self._last_call_at = time.monotonic()
                last_error = exc
                message = str(exc)
                retryable = any(
                    keyword in message.lower()
                    for keyword in (
                        "timeout",
                        "timed out",
                        "connection",
                        "超速",
                        "冷却",
                        "temporarily",
                    )
                )
                if not retryable or attempt >= self.max_retries:
                    raise
                time.sleep(min(8.0, 2.0**attempt))
        raise RuntimeError(f"{endpoint} 调用失败: {last_error}")


def parse_args() -> RunConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True, help="研究开始日，YYYYMMDD")
    parser.add_argument("--end-date", required=True, help="研究截止日，YYYYMMDD")
    parser.add_argument(
        "--benchmarks",
        default="000001.SH",
        help="逗号分隔的指数代码；第一个为主指数",
    )
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY)
    parser.add_argument("--min-interval-seconds", type=float, default=0.6)
    parser.add_argument(
        "--minute-freq",
        choices=("1min", "5min", "15min", "30min", "60min"),
        default="5min",
    )
    parser.add_argument(
        "--minute-event-limit",
        type=int,
        default=5,
        help="按公开买入金额排序后，读取分钟线的事件数量；0 表示跳过",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--refresh", action="store_true", help="忽略本地缓存重新请求")
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    benchmarks = tuple(
        item.strip().upper() for item in args.benchmarks.split(",") if item.strip()
    )
    if not benchmarks:
        parser.error("--benchmarks 至少需要一个指数代码")
    return RunConfig(
        start_date=normalize_date(args.start_date),
        end_date=normalize_date(args.end_date),
        benchmarks=benchmarks,
        gateway=args.gateway,
        min_interval_seconds=args.min_interval_seconds,
        minute_freq=args.minute_freq,
        minute_event_limit=max(0, args.minute_event_limit),
        output_dir=args.output_dir.resolve(),
        refresh=bool(args.refresh),
        max_retries=max(1, args.max_retries),
    )


def normalize_date(value: str) -> str:
    text = str(value).strip().replace("-", "")
    datetime.strptime(text, "%Y%m%d")
    return text


def display_date(value: str) -> str:
    return datetime.strptime(normalize_date(value), "%Y%m%d").strftime("%Y-%m-%d")


def validate_time_boundary(config: RunConfig) -> None:
    if config.start_date > config.end_date:
        raise ValueError("start-date 不能晚于 end-date")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    if config.end_date > today:
        raise ValueError(
            f"end-date={config.end_date} 晚于当前日期 {today}，为防时间穿越已拒绝运行"
        )


def cache_query(
    client: GatewayClient,
    *,
    endpoint: str,
    cache_path: Path,
    refresh: bool,
    **params: Any,
) -> pd.DataFrame:
    if cache_path.exists() and not refresh:
        return pd.read_csv(cache_path, dtype={"trade_date": str, "cal_date": str})
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    frame = client.query(endpoint, **params)
    frame.to_csv(cache_path, index=False, encoding="utf-8-sig")
    return frame


def fetch_trade_dates(
    client: GatewayClient,
    config: RunConfig,
    cache_dir: Path,
) -> tuple[pd.DataFrame, list[str]]:
    calendar = cache_query(
        client,
        endpoint="trade_cal",
        cache_path=cache_dir / f"trade_cal_{config.start_date}_{config.end_date}.csv",
        refresh=config.refresh,
        exchange="SSE",
        start_date=config.start_date,
        end_date=config.end_date,
        is_open="1",
    )
    if calendar.empty:
        return calendar, []
    calendar["cal_date"] = calendar["cal_date"].astype(str).map(normalize_date)
    open_dates = sorted(
        date
        for date in calendar.loc[calendar["is_open"].astype(int) == 1, "cal_date"]
        if config.start_date <= date <= config.end_date
    )
    return calendar, open_dates


def concat_frames(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [frame for frame in frames if frame is not None and not frame.empty]
    return pd.concat(non_empty, ignore_index=True) if non_empty else pd.DataFrame()


def fetch_top_inst_history(
    client: GatewayClient,
    trade_dates: list[str],
    config: RunConfig,
    cache_dir: Path,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    total = len(trade_dates)
    for index, trade_date in enumerate(trade_dates, start=1):
        print(f"[top_inst] {trade_date} ({index}/{total})", flush=True)
        frame = cache_query(
            client,
            endpoint="top_inst",
            cache_path=cache_dir / "top_inst" / f"{trade_date}.csv",
            refresh=config.refresh,
            trade_date=trade_date,
        )
        frames.append(frame)
    result = concat_frames(frames)
    if not result.empty:
        result["trade_date"] = result["trade_date"].astype(str).map(normalize_date)
    return result


def branch_mask(series: pd.Series) -> pd.Series:
    text = series.fillna("").astype(str)
    exact = text.isin(BRANCH_ALIASES)
    contains = text.str.contains("武汉紫阳东路", regex=False)
    return exact | contains


def normalize_side(value: Any) -> str:
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def joined_unique(values: Iterable[Any]) -> str:
    items = sorted({str(value).strip() for value in values if str(value).strip()})
    return " | ".join(items)


def max_number(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.max()) if not values.empty else math.nan


def build_events(raw_top_inst: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if raw_top_inst.empty:
        return pd.DataFrame(), pd.DataFrame()
    required = {"trade_date", "ts_code", "exalter", "side", "buy", "sell", "reason"}
    missing = required.difference(raw_top_inst.columns)
    if missing:
        raise ValueError(f"top_inst 缺少字段: {sorted(missing)}")

    target = raw_top_inst.loc[branch_mask(raw_top_inst["exalter"])].copy()
    if target.empty:
        return target, pd.DataFrame()
    target["side"] = target["side"].map(normalize_side)
    for column in ("buy", "sell", "net_buy"):
        if column in target.columns:
            target[column] = pd.to_numeric(target[column], errors="coerce")
    target["branch_name_raw"] = target["exalter"].astype(str)
    target["branch_name_canonical"] = CANONICAL_BRANCH

    rows: list[dict[str, Any]] = []
    group_columns = ["trade_date", "ts_code", "branch_name_canonical"]
    for (trade_date, ts_code, branch_name), group in target.groupby(
        group_columns, dropna=False, sort=True
    ):
        buy_side = group.loc[group["side"] == "0"]
        if buy_side.empty:
            continue
        buy_amount = max_number(buy_side["buy"])
        if not math.isfinite(buy_amount) or buy_amount <= 0:
            continue
        sell_side = group.loc[group["side"] == "1"]
        sell_amount = max_number(sell_side["sell"]) if not sell_side.empty else math.nan
        reasons = joined_unique(group["reason"])
        event_type = (
            "WINDOW_BUY"
            if any(keyword in reasons for keyword in WINDOW_REASON_KEYWORDS)
            else "EXACT_BUY"
        )
        event_key = (
            f"top_inst|{trade_date}|{ts_code}|{branch_name}|"
            f"{buy_amount:.2f}|{sell_amount if math.isfinite(sell_amount) else ''}"
        )
        rows.append(
            {
                "event_id": hashlib.sha256(event_key.encode("utf-8")).hexdigest()[:20],
                "source": "tushare_top_inst",
                "event_type": event_type,
                "anchor_date": str(trade_date),
                "ts_code": str(ts_code),
                "branch_name_canonical": branch_name,
                "branch_name_raw": joined_unique(group["branch_name_raw"]),
                "buy_amount": buy_amount,
                "sell_amount": sell_amount,
                "net_amount_public": (
                    buy_amount - sell_amount
                    if math.isfinite(sell_amount)
                    else math.nan
                ),
                "appeared_on_both_sides": not sell_side.empty,
                "reasons": reasons,
                "raw_row_count": int(len(group)),
                "distinct_reason_count": int(group["reason"].nunique(dropna=True)),
                "feature_cutoff": str(trade_date),
                "period_start": (
                    str(trade_date) if event_type == "EXACT_BUY" else ""
                ),
                "period_end": str(trade_date),
            }
        )
    events = pd.DataFrame(rows)
    if not events.empty:
        events = events.sort_values(
            ["anchor_date", "buy_amount", "ts_code"],
            ascending=[True, False, True],
        ).reset_index(drop=True)
    return target.reset_index(drop=True), events


def fetch_top_list_for_events(
    client: GatewayClient,
    events: pd.DataFrame,
    config: RunConfig,
    cache_dir: Path,
) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    dates = sorted(events["anchor_date"].astype(str).unique())
    for index, trade_date in enumerate(dates, start=1):
        print(f"[top_list] {trade_date} ({index}/{len(dates)})", flush=True)
        frames.append(
            cache_query(
                client,
                endpoint="top_list",
                cache_path=cache_dir / "top_list" / f"{trade_date}.csv",
                refresh=config.refresh,
                trade_date=trade_date,
            )
        )
    raw = concat_frames(frames)
    if raw.empty:
        return raw
    raw["trade_date"] = raw["trade_date"].astype(str).map(normalize_date)
    return raw


def collapse_top_list(raw_top_list: pd.DataFrame) -> pd.DataFrame:
    if raw_top_list.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for (trade_date, ts_code), group in raw_top_list.groupby(
        ["trade_date", "ts_code"], sort=True
    ):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "anchor_date": str(trade_date),
            "ts_code": str(ts_code),
            "stock_name": str(first.get("name") or ""),
            "top_list_reasons": joined_unique(group.get("reason", pd.Series(dtype=str))),
            "top_list_raw_rows": int(len(group)),
        }
        for column in (
            "close",
            "pct_change",
            "turnover_rate",
            "amount",
            "l_sell",
            "l_buy",
            "l_amount",
            "net_amount",
            "net_rate",
            "amount_rate",
            "float_values",
        ):
            if column in group.columns:
                row[f"top_list_{column}"] = max_number(group[column])
        rows.append(row)
    return pd.DataFrame(rows)


def merge_event_context(
    events: pd.DataFrame,
    collapsed_top_list: pd.DataFrame,
) -> pd.DataFrame:
    if events.empty:
        return events
    if collapsed_top_list.empty:
        result = events.copy()
        result["stock_name"] = ""
        return result
    return events.merge(
        collapsed_top_list,
        on=["anchor_date", "ts_code"],
        how="left",
        validate="one_to_one",
    )


def prewarm_start(start_date: str, calendar_days: int = 220) -> str:
    value = datetime.strptime(start_date, "%Y%m%d") - timedelta(days=calendar_days)
    return value.strftime("%Y%m%d")


def add_index_features(frame: pd.DataFrame, benchmark: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    result["trade_date"] = result["trade_date"].astype(str).map(normalize_date)
    result = result.sort_values("trade_date").drop_duplicates("trade_date").reset_index(
        drop=True
    )
    result["benchmark"] = benchmark
    for column in ("open", "high", "low", "close", "vol", "amount"):
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")

    # 以下所有特征只使用当前行和历史行：rolling、shift、pct_change 均不向未来取值。
    for window in (1, 3, 5, 10, 20):
        result[f"return_{window}d"] = result["close"].pct_change(window)
    for window in (5, 10, 20):
        result[f"ma{window}"] = result["close"].rolling(window).mean()
    result["ma20_5d_slope"] = result["ma20"].pct_change(5)
    if "amount" in result.columns:
        result["amount_ma5"] = result["amount"].rolling(5).mean()
        result["amount_ma20"] = result["amount"].rolling(20).mean()
        result["amount_ratio_5_20"] = (
            result["amount_ma5"] / result["amount_ma20"]
        )
    result["rolling_high_20"] = result["close"].rolling(20).max()
    result["drawdown_from_20d_high"] = (
        result["close"] / result["rolling_high_20"] - 1.0
    )

    up = (
        (result["close"] > result["ma20"])
        & (result["ma20_5d_slope"] > 0)
        & (result["return_5d"] > 0)
    )
    down = (
        (result["close"] < result["ma20"])
        & (result["ma20_5d_slope"] < 0)
        & (result["return_5d"] < 0)
    )
    ready = result[["ma20", "ma20_5d_slope", "return_5d"]].notna().all(axis=1)
    result["index_shape"] = np.select(
        [ready & up, ready & down, ready],
        ["M_UP", "M_DOWN", "M_RANGE"],
        default="UNKNOWN",
    )
    result["feature_cutoff"] = result["trade_date"]
    return result


def fetch_index_daily(
    client: GatewayClient,
    config: RunConfig,
    cache_dir: Path,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    start_date = prewarm_start(config.start_date)
    for benchmark in config.benchmarks:
        print(f"[index_daily] {benchmark} {start_date}~{config.end_date}", flush=True)
        raw = cache_query(
            client,
            endpoint="index_daily",
            cache_path=(
                cache_dir
                / "index_daily"
                / f"{benchmark.replace('.', '_')}_{start_date}_{config.end_date}.csv"
            ),
            refresh=config.refresh,
            ts_code=benchmark,
            start_date=start_date,
            end_date=config.end_date,
        )
        frames.append(add_index_features(raw, benchmark))
    return concat_frames(frames)


def build_event_index_snapshot(
    events: pd.DataFrame,
    index_daily: pd.DataFrame,
) -> pd.DataFrame:
    if events.empty or index_daily.empty:
        return pd.DataFrame()
    keep = [
        column
        for column in (
            "trade_date",
            "benchmark",
            "open",
            "high",
            "low",
            "close",
            "vol",
            "amount",
            "return_1d",
            "return_3d",
            "return_5d",
            "return_10d",
            "return_20d",
            "ma5",
            "ma10",
            "ma20",
            "ma20_5d_slope",
            "amount_ratio_5_20",
            "drawdown_from_20d_high",
            "index_shape",
            "feature_cutoff",
        )
        if column in index_daily.columns
    ]
    snapshot = index_daily[keep].rename(
        columns={
            "trade_date": "anchor_date",
            "feature_cutoff": "index_feature_cutoff",
        }
    )
    event_columns = [
        column
        for column in (
            "event_id",
            "event_type",
            "anchor_date",
            "ts_code",
            "stock_name",
            "buy_amount",
            "sell_amount",
            "reasons",
            "feature_cutoff",
        )
        if column in events.columns
    ]
    result = events[event_columns].merge(
        snapshot,
        on="anchor_date",
        how="left",
        validate="many_to_many",
    )
    invalid = (
        result["index_feature_cutoff"].fillna("").astype(str)
        > result["anchor_date"].astype(str)
    )
    if invalid.any():
        raise AssertionError("发现指数特征使用了事件日之后的数据")
    return result


def build_date_summary(
    events: pd.DataFrame,
    event_index_snapshot: pd.DataFrame,
    primary_benchmark: str,
) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    summary = (
        events.groupby(["anchor_date", "event_type"], as_index=False)
        .agg(
            event_count=("event_id", "nunique"),
            stock_count=("ts_code", "nunique"),
            total_buy_amount=("buy_amount", "sum"),
            max_single_buy_amount=("buy_amount", "max"),
            total_public_net_amount=(
                "net_amount_public",
                lambda values: values.sum(min_count=1),
            ),
        )
        .sort_values(["anchor_date", "event_type"])
    )
    if not event_index_snapshot.empty:
        primary = event_index_snapshot.loc[
            event_index_snapshot["benchmark"] == primary_benchmark,
            ["anchor_date", "index_shape", "return_5d", "amount_ratio_5_20"],
        ].drop_duplicates("anchor_date")
        summary = summary.merge(primary, on="anchor_date", how="left")
    return summary


def minute_cache_name(ts_code: str, trade_date: str, freq: str) -> str:
    return f"{ts_code.replace('.', '_')}_{trade_date}_{freq}.csv"


def fetch_minutes(
    client: GatewayClient,
    *,
    ts_code: str,
    trade_date: str,
    freq: str,
    config: RunConfig,
    cache_dir: Path,
) -> pd.DataFrame:
    date_text = display_date(trade_date)
    frame = cache_query(
        client,
        endpoint="stk_mins",
        cache_path=cache_dir / "stk_mins" / minute_cache_name(ts_code, trade_date, freq),
        refresh=config.refresh,
        ts_code=ts_code,
        freq=freq,
        start_date=f"{date_text} 09:00:00",
        end_date=f"{date_text} 16:00:00",
    )
    if not frame.empty:
        frame["trade_time"] = pd.to_datetime(frame["trade_time"])
        for column in ("open", "high", "low", "close", "vol", "amount"):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.sort_values("trade_time").reset_index(drop=True)
    return frame


def fetch_focus_minutes(
    client: GatewayClient,
    events: pd.DataFrame,
    config: RunConfig,
    cache_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if events.empty or config.minute_event_limit <= 0:
        return pd.DataFrame(), pd.DataFrame()
    focus = (
        events.sort_values(["buy_amount", "anchor_date"], ascending=[False, True])
        .head(config.minute_event_limit)
        .copy()
    )
    frames: list[pd.DataFrame] = []
    for _, event in focus.iterrows():
        event_id = str(event["event_id"])
        trade_date = str(event["anchor_date"])
        stock_code = str(event["ts_code"])
        symbols = [("stock", stock_code), ("index", config.benchmarks[0])]
        for series_kind, ts_code in symbols:
            print(
                f"[stk_mins] {event_id} {series_kind} {ts_code} {trade_date}",
                flush=True,
            )
            frame = fetch_minutes(
                client,
                ts_code=ts_code,
                trade_date=trade_date,
                freq=config.minute_freq,
                config=config,
                cache_dir=cache_dir,
            )
            if frame.empty:
                continue
            frame = frame.copy()
            frame["event_id"] = event_id
            frame["event_type"] = event["event_type"]
            frame["anchor_date"] = trade_date
            frame["event_stock_code"] = stock_code
            frame["series_kind"] = series_kind
            frame["series_code"] = ts_code
            frames.append(frame)
    return focus, concat_frames(frames)


def plot_timeline(
    date_summary: pd.DataFrame,
    primary_benchmark: str,
    output_path: Path,
) -> None:
    if plt is None or date_summary.empty:
        return
    plot_data = (
        date_summary.groupby("anchor_date", as_index=False)
        .agg(
            total_buy_amount=("total_buy_amount", "sum"),
            event_count=("event_count", "sum"),
            index_shape=("index_shape", "first"),
        )
        .sort_values("anchor_date")
    )
    dates = pd.to_datetime(plot_data["anchor_date"])
    colors = [
        SHAPE_COLORS.get(str(value), SHAPE_COLORS["UNKNOWN"])
        for value in plot_data["index_shape"]
    ]
    fig, ax = plt.subplots(figsize=(13, 5.5))
    ax.bar(
        dates,
        plot_data["total_buy_amount"] / 100_000_000,
        color=colors,
        width=0.75,
    )
    ax.set_title(f"紫阳东路公开买榜时间轴 × {primary_benchmark} 指数形态")
    ax.set_ylabel("去重公开买入额（亿元）")
    ax.grid(axis="y", alpha=0.2)
    for x, amount, count in zip(
        dates,
        plot_data["total_buy_amount"] / 100_000_000,
        plot_data["event_count"],
    ):
        ax.text(x, amount, f"{int(count)}只", ha="center", va="bottom", fontsize=8)
    legend_handles = [
        plt.Line2D([0], [0], color=color, lw=8, label=shape)
        for shape, color in SHAPE_COLORS.items()
    ]
    ax.legend(handles=legend_handles, ncol=4, loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def event_daily_window(
    index_daily: pd.DataFrame,
    benchmark: str,
    anchor_date: str,
    before: int = 20,
    after: int = 5,
) -> pd.DataFrame:
    data = (
        index_daily.loc[index_daily["benchmark"] == benchmark]
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    matches = data.index[data["trade_date"] == anchor_date].tolist()
    if not matches:
        return pd.DataFrame()
    position = matches[0]
    start = max(0, position - before)
    end = min(len(data), position + after + 1)
    result = data.iloc[start:end].copy()
    result["relative_trade_day"] = np.arange(start - position, end - position)
    result["is_post_event"] = result["relative_trade_day"] > 0
    return result


def normalized_close(frame: pd.DataFrame) -> pd.Series:
    if frame.empty or "close" not in frame.columns:
        return pd.Series(dtype=float)
    first = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if first.empty or first.iloc[0] == 0:
        return pd.Series(index=frame.index, dtype=float)
    return pd.to_numeric(frame["close"], errors="coerce") / first.iloc[0] * 100.0


def plot_focus_cases(
    focus_events: pd.DataFrame,
    minute_bars: pd.DataFrame,
    index_daily: pd.DataFrame,
    config: RunConfig,
    figure_dir: Path,
) -> None:
    if plt is None or focus_events.empty:
        return
    figure_dir.mkdir(parents=True, exist_ok=True)
    benchmark = config.benchmarks[0]
    for _, event in focus_events.iterrows():
        event_id = str(event["event_id"])
        anchor_date = str(event["anchor_date"])
        stock_code = str(event["ts_code"])
        stock_name = str(event.get("stock_name") or "")
        event_minutes = minute_bars.loc[minute_bars["event_id"] == event_id]
        stock_minutes = event_minutes.loc[event_minutes["series_kind"] == "stock"]
        index_minutes = event_minutes.loc[event_minutes["series_kind"] == "index"]
        daily = event_daily_window(index_daily, benchmark, anchor_date)

        fig = plt.figure(figsize=(13, 8))
        grid = fig.add_gridspec(2, 2, height_ratios=(1.2, 1.0))
        daily_ax = fig.add_subplot(grid[0, :])
        stock_ax = fig.add_subplot(grid[1, 0])
        index_ax = fig.add_subplot(grid[1, 1])

        if not daily.empty:
            x = daily["relative_trade_day"]
            daily_ax.plot(x, daily["close"], label="收盘", color="#202124", lw=1.8)
            if "ma5" in daily:
                daily_ax.plot(x, daily["ma5"], label="MA5", color="#D85845")
            if "ma20" in daily:
                daily_ax.plot(x, daily["ma20"], label="MA20", color="#4E7C9E")
            daily_ax.axvline(0, color="#111111", ls="--", lw=1)
            daily_ax.axvspan(0.1, 5.5, color="#9AA0A6", alpha=0.08)
            daily_ax.set_xlabel("相对交易日（T>0 仅作后验描述）")
            daily_ax.set_ylabel("指数点位")
            daily_ax.legend(loc="best")
        daily_ax.set_title(f"{benchmark}：T-20 至数据截止日")
        daily_ax.grid(alpha=0.2)

        if not stock_minutes.empty:
            stock_ax.plot(
                stock_minutes["trade_time"],
                normalized_close(stock_minutes),
                color="#D85845",
            )
        stock_ax.set_title(f"{stock_code} {stock_name} 分时归一化")
        stock_ax.set_ylabel("09:30=100")
        stock_ax.grid(alpha=0.2)
        stock_ax.tick_params(axis="x", rotation=25)

        if not index_minutes.empty:
            index_ax.plot(
                index_minutes["trade_time"],
                normalized_close(index_minutes),
                color="#4E7C9E",
            )
        index_ax.set_title(f"{benchmark} 分时归一化")
        index_ax.set_ylabel("09:30=100")
        index_ax.grid(alpha=0.2)
        index_ax.tick_params(axis="x", rotation=25)

        amount_yi = float(event["buy_amount"]) / 100_000_000
        fig.suptitle(
            f"{anchor_date} {event['event_type']}｜公开买入 {amount_yi:.2f} 亿元｜{stock_code}",
            fontsize=14,
        )
        fig.tight_layout()
        output_path = figure_dir / f"{anchor_date}_{stock_code.replace('.', '_')}.png"
        fig.savefig(output_path, dpi=180)
        plt.close(fig)


def build_quality_checks(
    raw_top_inst: pd.DataFrame,
    target_raw: pd.DataFrame,
    events: pd.DataFrame,
    raw_top_list: pd.DataFrame,
    index_snapshot: pd.DataFrame,
    minute_bars: pd.DataFrame,
) -> pd.DataFrame:
    checks = [
        ("top_inst_raw_rows", len(raw_top_inst)),
        ("target_branch_raw_rows", len(target_raw)),
        ("deduplicated_buy_events", len(events)),
        (
            "exact_buy_events",
            int((events.get("event_type", pd.Series(dtype=str)) == "EXACT_BUY").sum()),
        ),
        (
            "window_buy_events",
            int((events.get("event_type", pd.Series(dtype=str)) == "WINDOW_BUY").sum()),
        ),
        ("top_list_raw_rows", len(raw_top_list)),
        (
            "event_index_snapshot_missing",
            (
                int(index_snapshot["index_shape"].isna().sum())
                if "index_shape" in index_snapshot
                else len(index_snapshot)
            ),
        ),
        ("minute_bar_rows", len(minute_bars)),
    ]
    return pd.DataFrame(checks, columns=["check_name", "value"])


def write_excel(
    output_path: Path,
    *,
    config: RunConfig,
    calendar: pd.DataFrame,
    raw_top_inst: pd.DataFrame,
    target_raw: pd.DataFrame,
    events: pd.DataFrame,
    date_summary: pd.DataFrame,
    raw_top_list: pd.DataFrame,
    index_daily: pd.DataFrame,
    event_index_snapshot: pd.DataFrame,
    minute_bars: pd.DataFrame,
    quality_checks: pd.DataFrame,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    parameters = pd.DataFrame(
        [
            ("start_date", config.start_date),
            ("end_date", config.end_date),
            ("benchmarks", ",".join(config.benchmarks)),
            ("gateway", config.gateway),
            ("minute_freq", config.minute_freq),
            ("minute_event_limit", config.minute_event_limit),
            ("min_interval_seconds", config.min_interval_seconds),
            (
                "index_shape_rule",
                "UP: close>MA20 & MA20_5d_slope>0 & return_5d>0; "
                "DOWN 对称；否则 RANGE",
            ),
            (
                "time_travel_guard",
                "全部形态特征 feature_cutoff=事件日；脚本不请求 end_date 之后的数据",
            ),
        ],
        columns=["parameter", "value"],
    )
    sheets = {
        "参数": parameters,
        "交易日历": calendar,
        "龙虎榜原始": raw_top_inst,
        "紫阳原始": target_raw,
        "紫阳事件": events,
        "买入日期汇总": date_summary,
        "龙虎榜统计原始": raw_top_list,
        "指数日线": index_daily,
        "事件指数快照": event_index_snapshot,
        "分时样本": minute_bars,
        "质量检查": quality_checks,
    }
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet_name, frame in sheets.items():
            safe_frame = frame.copy() if frame is not None else pd.DataFrame()
            safe_frame.to_excel(writer, sheet_name=sheet_name[:31], index=False)


def write_manifest(
    output_path: Path,
    *,
    config: RunConfig,
    excel_path: Path,
    quality_checks: pd.DataFrame,
) -> None:
    payload = {
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
            timespec="seconds"
        ),
        "start_date": config.start_date,
        "end_date": config.end_date,
        "benchmarks": list(config.benchmarks),
        "excel_path": str(excel_path),
        "quality_checks": quality_checks.to_dict(orient="records"),
        "notes": [
            "公开龙虎榜不是营业部全部交易，也不是客户逐笔交割单。",
            "WINDOW_BUY 的 anchor_date 只是披露统计窗口结束日。",
            "形态特征只使用事件日及此前数据；T+1/T+5 仅用于后验描述。",
        ],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    config = parse_args()
    validate_time_boundary(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = config.output_dir / "cache"
    figure_dir = config.output_dir / "figures"

    app_config = load_config()
    client = GatewayClient(
        token=app_config.tushare_token or "",
        gateway=config.gateway,
        min_interval_seconds=config.min_interval_seconds,
        max_retries=config.max_retries,
    )

    calendar, trade_dates = fetch_trade_dates(client, config, cache_dir)
    if not trade_dates:
        raise RuntimeError("研究区间内没有可用交易日")

    raw_top_inst = fetch_top_inst_history(client, trade_dates, config, cache_dir)
    target_raw, events = build_events(raw_top_inst)
    raw_top_list = fetch_top_list_for_events(
        client, events, config, cache_dir
    )
    top_list_context = collapse_top_list(raw_top_list)
    events = merge_event_context(events, top_list_context)

    index_daily = fetch_index_daily(client, config, cache_dir)
    event_index_snapshot = build_event_index_snapshot(events, index_daily)
    date_summary = build_date_summary(
        events, event_index_snapshot, config.benchmarks[0]
    )
    focus_events, minute_bars = fetch_focus_minutes(
        client, events, config, cache_dir
    )
    quality_checks = build_quality_checks(
        raw_top_inst,
        target_raw,
        events,
        raw_top_list,
        event_index_snapshot,
        minute_bars,
    )

    excel_path = config.output_dir / "ziyang_index_analysis_example.xlsx"
    write_excel(
        excel_path,
        config=config,
        calendar=calendar,
        raw_top_inst=raw_top_inst,
        target_raw=target_raw,
        events=events,
        date_summary=date_summary,
        raw_top_list=raw_top_list,
        index_daily=index_daily,
        event_index_snapshot=event_index_snapshot,
        minute_bars=minute_bars,
        quality_checks=quality_checks,
    )
    plot_timeline(
        date_summary,
        config.benchmarks[0],
        figure_dir / "ziyang_buy_timeline.png",
    )
    plot_focus_cases(
        focus_events,
        minute_bars,
        index_daily,
        config,
        figure_dir,
    )
    write_manifest(
        config.output_dir / "run_manifest.json",
        config=config,
        excel_path=excel_path,
        quality_checks=quality_checks,
    )

    print("\n完成。", flush=True)
    print(f"Excel: {excel_path}", flush=True)
    if plt is None:
        print("图表: 已跳过（如需 PNG，请安装 matplotlib）", flush=True)
    else:
        print(f"图表: {figure_dir}", flush=True)
    print(quality_checks.to_string(index=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
