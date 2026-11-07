"""Plot five public-LHB intraday cases for Wuhan Ziyang East Road branch.

The case list is frozen from SSE public trade-disclosure results queried on
2026-07-22.  Duplicate rows caused by multiple disclosure reasons are removed
before the amounts below are aggregated.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
import subprocess
from urllib.parse import urlencode

import matplotlib.pyplot as plt
import pandas as pd

from shilun.common.config import load_config
from shilun.data.providers.klineshare_provider import KlineShareClient, KlineShareConfig

plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


OUTPUT_DIR = Path("outputs/pattern_research/ziyang_cases_20260722")
EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

CASES = [
    {
        "rank": 1,
        "ticker": "600602.SH",
        "name": "云赛智联",
        "dates": ["2026-07-20"],
        "interval_minutes": 5,
        "buy_amount": 127_623_386.68,
        "sell_amount": 129_518_744.00,
        "within_one_month": True,
    },
    {
        "rank": 2,
        "ticker": "603137.SH",
        "name": "恒尚节能",
        "dates": ["2026-07-08", "2026-07-13"],
        "interval_minutes": 5,
        "buy_amount": 28_577_934.00,
        "sell_amount": 35_014_677.00,
        "within_one_month": True,
    },
    {
        "rank": 3,
        "ticker": "603118.SH",
        "name": "共进股份",
        "dates": ["2026-07-17", "2026-07-20"],
        "interval_minutes": 5,
        "buy_amount": 296_418_241.76,
        "sell_amount": 323_278_883.95,
        "within_one_month": True,
    },
    {
        "rank": 4,
        "ticker": "600667.SH",
        "name": "太极实业",
        "dates": ["2026-06-25", "2026-06-26"],
        "interval_minutes": 5,
        "buy_amount": 332_416_062.00,
        "sell_amount": 401_235_790.00,
        "within_one_month": True,
    },
    {
        "rank": 5,
        "ticker": "603186.SH",
        "name": "华正新材",
        "dates": ["2026-06-09"],
        "interval_minutes": 15,
        "buy_amount": 142_413_010.00,
        "sell_amount": 149_814_308.00,
        "within_one_month": False,
        "selection_note": "最近一个月只有4只完整买卖样本；向前扩展补足的最近完整样本。",
    },
]


def _fetch_bars(ticker: str, trade_date: str) -> pd.DataFrame:
    period_seconds = 300 if trade_date >= "2026-06-24" else 900
    return _fetch_klineshare_history(ticker, trade_date, period_seconds)


@lru_cache(maxsize=8)
def _klineshare_frame(ticker: str, period_seconds: int) -> pd.DataFrame:
    config = load_config()
    client = KlineShareClient(
        KlineShareConfig(
            api_key=config.klineshare_api_key,
            base_url=config.klineshare_base_url,
            timeout=config.klineshare_timeout,
        )
    )
    frame = client.fetch_kline(ticker, period=period_seconds, count=1000, adjust_type="none")
    frame = frame.copy()
    frame["amount"] = frame["amount"] * 1000.0  # provider normalizes to thousand CNY
    return frame


def _fetch_klineshare_history(ticker: str, trade_date: str, period_seconds: int) -> pd.DataFrame:
    frame = _klineshare_frame(ticker, period_seconds)
    target = pd.Timestamp(trade_date).date()
    timestamps = pd.to_datetime(frame["datetime"])
    filtered = frame[timestamps.dt.date == target].copy().reset_index(drop=True)
    if filtered.empty:
        raise RuntimeError(f"No KlineShare 5-minute bars returned for {ticker} {trade_date}")
    # KlineShare volume is shares and amount is normalized above to CNY.
    filtered["vwap"] = filtered["amount"].cumsum() / filtered["volume"].cumsum()
    return filtered


def _fetch_eastmoney_history(ticker: str, trade_date: str) -> pd.DataFrame:
    symbol = ticker.split(".")[0]
    market = "1" if ticker.endswith(".SH") else "0"
    compact = trade_date.replace("-", "")
    params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": "5",
            "fqt": "0",
            "secid": f"{market}.{symbol}",
            "beg": compact,
            "end": compact,
        }
    response_text = subprocess.run(
        [
            "curl", "-L", "-sS", "--retry", "4", "--retry-all-errors", "--retry-delay", "1",
            f"{EASTMONEY_URL}?{urlencode(params)}", "-H", "User-Agent: Mozilla/5.0",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    payload = json.loads(response_text)
    rows = ((payload.get("data") or {}).get("klines") or [])
    parsed = []
    for row in rows:
        values = row.split(",")
        if len(values) < 8:
            continue
        parsed.append(
            {
                "datetime": pd.Timestamp(values[0]),
                "open": float(values[1]),
                "close": float(values[2]),
                "high": float(values[3]),
                "low": float(values[4]),
                "volume": float(values[5]),
                "amount": float(values[6]),
            }
        )
    frame = pd.DataFrame(parsed)
    if frame.empty:
        raise RuntimeError(f"No 5-minute bars returned for {ticker} {trade_date}")
    frame["vwap"] = frame["amount"].cumsum() / (frame["volume"].cumsum() * 100.0)
    return frame


def _plot_case(case: dict, frames: dict[str, pd.DataFrame]) -> Path:
    dates = case["dates"]
    fig, axes = plt.subplots(len(dates), 1, figsize=(13, 4.2 * len(dates)), squeeze=False)
    for axis, trade_date in zip(axes[:, 0], dates, strict=True):
        frame = frames[trade_date]
        x = range(len(frame))
        axis.plot(x, frame["close"], color="#1769aa", linewidth=1.8, label="intraday close")
        axis.plot(x, frame["vwap"], color="#d97706", linewidth=1.3, label="cumulative VWAP")
        axis.fill_between(x, frame["low"], frame["high"], color="#1769aa", alpha=0.10)
        tick_step = max(1, len(frame) // 8)
        tick_positions = list(range(0, len(frame), tick_step))
        axis.set_xticks(tick_positions)
        axis.set_xticklabels([frame.iloc[i]["datetime"].strftime("%H:%M") for i in tick_positions])
        axis.set_title(f"{case['ticker']} {case['name']} · {trade_date}")
        axis.set_ylabel("Price")
        axis.grid(alpha=0.18)
        axis.legend(loc="best")

        volume_axis = axis.twinx()
        volume_axis.bar(x, frame["volume"], color="#94a3b8", alpha=0.18, width=0.8)
        volume_axis.set_yticks([])

    difference = abs(case["buy_amount"] - case["sell_amount"])
    total = case["buy_amount"] + case["sell_amount"]
    scope = "strict one-month sample" if case["within_one_month"] else "extended fifth sample"
    fig.suptitle(
        f"SSE public LHB aggregate · {scope}\n"
        f"buy ¥{case['buy_amount']/1e6:.2f}m · sell ¥{case['sell_amount']/1e6:.2f}m · "
        f"abs diff ¥{difference/1e6:.2f}m · total ¥{total/1e6:.2f}m",
        fontsize=13,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.91))
    path = OUTPUT_DIR / f"{case['rank']:02d}_{case['ticker'].replace('.', '_')}.png"
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for case in CASES:
        frames = {trade_date: _fetch_bars(case["ticker"], trade_date) for trade_date in case["dates"]}
        chart = _plot_case(case, frames)
        item = {
            **case,
            "absolute_difference": round(abs(case["buy_amount"] - case["sell_amount"]), 2),
            "total_amount": round(case["buy_amount"] + case["sell_amount"], 2),
            "chart": str(chart),
            "minute_source": f"KlineShare public {case['interval_minutes']}-minute kline",
            "lhb_source": "SSE JYGKXX_XQ public query",
        }
        manifest.append(item)

    (OUTPUT_DIR / "cases.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 武汉紫阳东路营业部：首批5个分时/情绪研究样本",
        "",
        "> 龙虎榜金额为营业部级公开汇总，不是客户逐笔交割单。重复披露原因记录已去重。",
        "",
        "| 排名 | 股票 | 日期 | 买入(万元) | 卖出(万元) | 绝对差(万元) | 总额(万元) | 范围 | 图表 |",
        "|---:|---|---|---:|---:|---:|---:|---|---|",
    ]
    for item in manifest:
        lines.append(
            f"| {item['rank']} | {item['ticker']} {item['name']} | {', '.join(item['dates'])} | "
            f"{item['buy_amount']/1e4:.2f} | {item['sell_amount']/1e4:.2f} | "
            f"{item['absolute_difference']/1e4:.2f} | {item['total_amount']/1e4:.2f} | "
            f"{'最近1个月' if item['within_one_month'] else '向前扩展'} | {item['chart']} |"
        )
    (OUTPUT_DIR / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUTPUT_DIR)


if __name__ == "__main__":
    main()
