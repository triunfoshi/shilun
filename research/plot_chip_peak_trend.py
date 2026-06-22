from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd

from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient


def resolve_stock_name(client: TushareDailyClient, ticker: str) -> str:
    try:
        basic = client.fetch_stock_basic(fields="ts_code,name")
    except Exception:
        return ticker
    if basic.empty:
        return ticker
    matched = basic.loc[basic["ts_code"] == ticker, "name"]
    return str(matched.iloc[0]) if not matched.empty else ticker


def fetch_peak_series(
    client: TushareDailyClient,
    ticker: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    chips_df = client.pro_client.cyq_chips(ts_code=ticker, start_date=start_date, end_date=end_date)
    if chips_df is None or chips_df.empty:
        return pd.DataFrame(columns=["date", "peak_price", "peak_percent", "chip_total_percent"])

    chips = chips_df.copy()
    chips["trade_date"] = pd.to_datetime(chips["trade_date"], format="%Y%m%d", errors="coerce")
    chips["price"] = pd.to_numeric(chips["price"], errors="coerce")
    chips["percent"] = pd.to_numeric(chips["percent"], errors="coerce")
    chips = chips.dropna(subset=["trade_date", "price", "percent"]).sort_values(["trade_date", "price"]).reset_index(drop=True)
    if chips.empty:
        return pd.DataFrame(columns=["date", "peak_price", "peak_percent", "chip_total_percent"])

    rows: list[dict[str, float | pd.Timestamp]] = []
    for trade_date, day_df in chips.groupby("trade_date", sort=True):
        peak_idx = day_df["percent"].idxmax()
        peak_row = day_df.loc[peak_idx]
        rows.append(
            {
                "date": pd.Timestamp(trade_date),
                "peak_price": float(peak_row["price"]),
                "peak_percent": float(peak_row["percent"]),
                "chip_total_percent": float(day_df["percent"].sum()),
            }
        )

    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def fetch_chip_heatmap_data(
    client: TushareDailyClient,
    ticker: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    chips_df = client.pro_client.cyq_chips(ts_code=ticker, start_date=start_date, end_date=end_date)
    if chips_df is None or chips_df.empty:
        return pd.DataFrame(columns=["date", "price", "percent"])

    chips = chips_df.copy()
    chips["date"] = pd.to_datetime(chips["trade_date"], format="%Y%m%d", errors="coerce")
    chips["price"] = pd.to_numeric(chips["price"], errors="coerce")
    chips["percent"] = pd.to_numeric(chips["percent"], errors="coerce")
    chips = chips.dropna(subset=["date", "price", "percent"])
    return chips[["date", "price", "percent"]].sort_values(["date", "price"]).reset_index(drop=True)


def build_svg(title: str, subtitle: str, df: pd.DataFrame) -> str:
    width = 1200
    height = 720
    margin_left = 88
    margin_right = 40
    margin_top = 90
    margin_bottom = 90
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom

    x_count = len(df)
    y_max = max(10.0, float(df["peak_percent"].max()) * 1.15)
    y_top = round_up(y_max, 2.0)

    def x_pos(index: int) -> float:
        if x_count <= 1:
            return margin_left + plot_width / 2
        return margin_left + index * plot_width / (x_count - 1)

    def y_pos(value: float) -> float:
        return margin_top + plot_height - (value / y_top) * plot_height

    tick_count = 6
    y_ticks = [y_top * idx / tick_count for idx in range(tick_count + 1)]
    x_tick_step = max(1, x_count // 6)

    background = "#fbfaf7"
    panel = "#ffffff"
    grid = "#d9d2c3"
    axis = "#6b6258"
    line = "#0b6e4f"
    point = "#157a5b"
    text = "#2f2a24"
    subtext = "#7a7268"
    accent_fill = "#dff3ea"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="{background}"/>',
        f'<rect x="24" y="24" width="{width - 48}" height="{height - 48}" rx="24" fill="{panel}" stroke="#ece4d8"/>',
        f'<text x="{margin_left}" y="54" font-size="28" font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{text}" font-weight="700">{escape(title)}</text>',
        f'<text x="{margin_left}" y="82" font-size="14" font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{subtext}">{escape(subtitle)}</text>',
        f'<rect x="{margin_left}" y="{margin_top}" width="{plot_width}" height="{plot_height}" rx="16" fill="{accent_fill}" opacity="0.35"/>',
    ]

    for tick in y_ticks:
        y = y_pos(float(tick))
        label = f"{tick:.0f}%"
        parts.append(f'<line x1="{margin_left}" y1="{y:.2f}" x2="{margin_left + plot_width}" y2="{y:.2f}" stroke="{grid}" stroke-dasharray="4 6"/>')
        parts.append(
            f'<text x="{margin_left - 12}" y="{y + 5:.2f}" text-anchor="end" font-size="12" '
            f'font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{axis}">{label}</text>'
        )

    parts.append(
        f'<line x1="{margin_left}" y1="{margin_top + plot_height}" x2="{margin_left + plot_width}" y2="{margin_top + plot_height}" stroke="{axis}" stroke-width="1.5"/>'
    )
    parts.append(
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" stroke="{axis}" stroke-width="1.5"/>'
    )

    for idx in range(0, x_count, x_tick_step):
        x = x_pos(idx)
        label = pd.Timestamp(df.iloc[idx]["date"]).strftime("%m-%d")
        parts.append(f'<line x1="{x:.2f}" y1="{margin_top + plot_height}" x2="{x:.2f}" y2="{margin_top + plot_height + 6}" stroke="{axis}"/>')
        parts.append(
            f'<text x="{x:.2f}" y="{margin_top + plot_height + 26}" text-anchor="middle" font-size="12" '
            f'font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{axis}">{label}</text>'
        )

    last_idx = x_count - 1
    if last_idx % x_tick_step != 0:
        x = x_pos(last_idx)
        label = pd.Timestamp(df.iloc[last_idx]["date"]).strftime("%m-%d")
        parts.append(f'<line x1="{x:.2f}" y1="{margin_top + plot_height}" x2="{x:.2f}" y2="{margin_top + plot_height + 6}" stroke="{axis}"/>')
        parts.append(
            f'<text x="{x:.2f}" y="{margin_top + plot_height + 26}" text-anchor="middle" font-size="12" '
            f'font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{axis}">{label}</text>'
        )

    polyline_points = []
    for idx, row in df.reset_index(drop=True).iterrows():
        x = x_pos(idx)
        y = y_pos(float(row["peak_percent"]))
        polyline_points.append(f"{x:.2f},{y:.2f}")
    parts.append(
        f'<polyline fill="none" stroke="{line}" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round" '
        f'points="{" ".join(polyline_points)}"/>'
    )

    latest = df.iloc[-1]
    latest_x = x_pos(last_idx)
    latest_y = y_pos(float(latest["peak_percent"]))
    parts.append(f'<circle cx="{latest_x:.2f}" cy="{latest_y:.2f}" r="6.5" fill="{point}" stroke="#ffffff" stroke-width="2"/>')
    parts.append(
        f'<text x="{latest_x - 10:.2f}" y="{latest_y - 14:.2f}" text-anchor="end" font-size="12" '
        f'font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{line}">{float(latest["peak_percent"]):.2f}%</text>'
    )

    parts.append(
        f'<text x="{width / 2:.2f}" y="{height - 24}" text-anchor="middle" font-size="14" '
        f'font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{text}">日期</text>'
    )
    parts.append(
        f'<text x="26" y="{height / 2:.2f}" text-anchor="middle" font-size="14" '
        f'font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{text}" '
        f'transform="rotate(-90 26 {height / 2:.2f})">筹码分布占比</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def build_heatmap_svg(title: str, subtitle: str, chips: pd.DataFrame) -> str:
    width = 1280
    height = 820
    margin_left = 92
    margin_right = 70
    margin_top = 92
    margin_bottom = 98
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom

    dates = sorted(pd.to_datetime(chips["date"]).drop_duplicates().tolist())
    prices = sorted(pd.to_numeric(chips["price"], errors="coerce").dropna().unique().tolist())
    grid = chips.pivot_table(index="price", columns="date", values="percent", aggfunc="sum", fill_value=0.0)
    grid = grid.reindex(index=prices, columns=dates, fill_value=0.0)

    cols = len(dates)
    rows = len(prices)
    cell_width = plot_width / max(1, cols)
    cell_height = plot_height / max(1, rows)
    max_percent = float(chips["percent"].max())

    background = "#f7f4ef"
    panel = "#ffffff"
    text = "#2f2a24"
    subtext = "#746b62"
    axis = "#61584f"
    border = "#e6ddd0"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="{background}"/>',
        f'<rect x="24" y="24" width="{width - 48}" height="{height - 48}" rx="24" fill="{panel}" stroke="{border}"/>',
        f'<text x="{margin_left}" y="54" font-size="28" font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{text}" font-weight="700">{escape(title)}</text>',
        f'<text x="{margin_left}" y="82" font-size="14" font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{subtext}">{escape(subtitle)}</text>',
    ]

    for row_idx, price in enumerate(reversed(prices)):
        y = margin_top + row_idx * cell_height
        for col_idx, date in enumerate(dates):
            x = margin_left + col_idx * cell_width
            percent = float(grid.at[price, date])
            parts.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{cell_width + 0.4:.2f}" height="{cell_height + 0.4:.2f}" '
                f'fill="{heatmap_color(percent, max_percent)}"/>'
            )

    parts.append(
        f'<rect x="{margin_left}" y="{margin_top}" width="{plot_width}" height="{plot_height}" fill="none" stroke="{axis}" stroke-width="1.2"/>'
    )

    y_tick_count = 8
    if prices:
        for idx in range(y_tick_count + 1):
            price_idx = round(idx * (rows - 1) / max(1, y_tick_count))
            price = prices[rows - 1 - price_idx]
            y = margin_top + price_idx * cell_height + cell_height / 2
            parts.append(
                f'<text x="{margin_left - 12}" y="{y + 4:.2f}" text-anchor="end" font-size="12" '
                f'font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{axis}">{price:.2f}</text>'
            )

    x_tick_step = max(1, cols // 8)
    for idx in range(0, cols, x_tick_step):
        x = margin_left + idx * cell_width + cell_width / 2
        label = pd.Timestamp(dates[idx]).strftime("%m-%d")
        parts.append(
            f'<text x="{x:.2f}" y="{margin_top + plot_height + 24}" text-anchor="middle" font-size="12" '
            f'font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{axis}">{label}</text>'
        )
    if cols > 0 and (cols - 1) % x_tick_step != 0:
        x = margin_left + (cols - 1) * cell_width + cell_width / 2
        label = pd.Timestamp(dates[-1]).strftime("%m-%d")
        parts.append(
            f'<text x="{x:.2f}" y="{margin_top + plot_height + 24}" text-anchor="middle" font-size="12" '
            f'font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{axis}">{label}</text>'
        )

    legend_x = width - margin_right - 24
    legend_y = margin_top
    legend_height = 220
    legend_width = 18
    gradient_id = "heatLegend"
    parts.append(
        f'<defs><linearGradient id="{gradient_id}" x1="0%" y1="100%" x2="0%" y2="0%">'
        f'<stop offset="0%" stop-color="{heatmap_color(0.0, max_percent)}"/>'
        f'<stop offset="100%" stop-color="{heatmap_color(max_percent, max_percent)}"/>'
        f'</linearGradient></defs>'
    )
    parts.append(
        f'<rect x="{legend_x}" y="{legend_y}" width="{legend_width}" height="{legend_height}" fill="url(#{gradient_id})" stroke="{border}"/>'
    )
    for ratio in [0.0, 0.25, 0.5, 0.75, 1.0]:
        y = legend_y + legend_height - ratio * legend_height
        value = max_percent * ratio
        parts.append(f'<line x1="{legend_x + legend_width}" y1="{y:.2f}" x2="{legend_x + legend_width + 6}" y2="{y:.2f}" stroke="{axis}"/>')
        parts.append(
            f'<text x="{legend_x + legend_width + 12}" y="{y + 4:.2f}" font-size="12" '
            f'font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{axis}">{value:.2f}%</text>'
        )
    parts.append(
        f'<text x="{legend_x - 8}" y="{legend_y - 10}" font-size="12" font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{axis}">占比</text>'
    )

    parts.append(
        f'<text x="{width / 2:.2f}" y="{height - 22}" text-anchor="middle" font-size="14" '
        f'font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{text}">日期</text>'
    )
    parts.append(
        f'<text x="28" y="{height / 2:.2f}" text-anchor="middle" font-size="14" '
        f'font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="{text}" '
        f'transform="rotate(-90 28 {height / 2:.2f})">价格</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def heatmap_color(value: float, max_value: float) -> str:
    if max_value <= 0:
        ratio = 0.0
    else:
        ratio = max(0.0, min(1.0, value / max_value))

    # 从浅米色过渡到深绿蓝，便于识别高密度筹码堆积。
    start = (248, 241, 227)
    mid = (244, 181, 98)
    end = (11, 88, 111)
    if ratio < 0.5:
        local = ratio / 0.5
        rgb = tuple(int(start[i] + (mid[i] - start[i]) * local) for i in range(3))
    else:
        local = (ratio - 0.5) / 0.5
        rgb = tuple(int(mid[i] + (end[i] - mid[i]) * local) for i in range(3))
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def round_up(value: float, step: float) -> float:
    quotient = int(value / step)
    if value % step == 0:
        return float(quotient * step)
    return float((quotient + 1) * step)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot chip peak percent trend from Tushare cyq_chips.")
    parser.add_argument("--ticker", required=True, help="Ticker such as 000539.SZ")
    parser.add_argument("--days", type=int, default=90, help="Lookback natural days, default 90")
    parser.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"), help="Analysis end date, YYYY-MM-DD")
    parser.add_argument("--price-min", type=float, default=None, help="Optional minimum price bound for heatmap")
    parser.add_argument("--price-max", type=float, default=None, help="Optional maximum price bound for heatmap")
    parser.add_argument("--output", default="", help="SVG output path")
    parser.add_argument("--heatmap-output", default="", help="Heatmap SVG output path")
    parser.add_argument("--csv-output", default="", help="Optional CSV output path")
    args = parser.parse_args()

    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
    start_date = as_of - timedelta(days=max(1, args.days) - 1)

    client = TushareDailyClient(TushareConfig.from_env())
    stock_name = resolve_stock_name(client, args.ticker)
    chips_df = fetch_chip_heatmap_data(
        client=client,
        ticker=args.ticker,
        start_date=start_date.strftime("%Y%m%d"),
        end_date=as_of.strftime("%Y%m%d"),
    )
    peak_df = fetch_peak_series(
        client=client,
        ticker=args.ticker,
        start_date=start_date.strftime("%Y%m%d"),
        end_date=as_of.strftime("%Y%m%d"),
    )
    if peak_df.empty or chips_df.empty:
        raise SystemExit(f"No chip peak data found for {args.ticker} between {start_date} and {as_of}.")

    if args.price_min is not None:
        chips_df = chips_df.loc[chips_df["price"] >= args.price_min].copy()
    if args.price_max is not None:
        chips_df = chips_df.loc[chips_df["price"] <= args.price_max].copy()
    if chips_df.empty:
        raise SystemExit(
            f"No heatmap data remains for {args.ticker} after applying price range "
            f"{args.price_min} to {args.price_max}."
        )

    output_path = Path(args.output) if args.output else Path("outputs") / f"{args.ticker}_chip_peak_trend_{as_of.isoformat()}.svg"
    heatmap_path = Path(args.heatmap_output) if args.heatmap_output else Path("outputs") / f"{args.ticker}_chip_heatmap_{as_of.isoformat()}.svg"
    csv_path = Path(args.csv_output) if args.csv_output else output_path.with_suffix(".csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    heatmap_path.parent.mkdir(parents=True, exist_ok=True)

    peak_df["date"] = pd.to_datetime(peak_df["date"])
    peak_df["date_str"] = peak_df["date"].dt.strftime("%Y-%m-%d")
    peak_df.to_csv(csv_path, index=False)

    title = f"{stock_name}({args.ticker})近{args.days}天主筹码峰占比趋势"
    subtitle = (
        f"口径：每个交易日 cyq_chips 中 percent 最大值；区间 {peak_df['date_str'].iloc[0]} 至 {peak_df['date_str'].iloc[-1]}，"
        f"共 {len(peak_df)} 个交易日"
    )
    svg = build_svg(title=title, subtitle=subtitle, df=peak_df)
    output_path.write_text(svg, encoding="utf-8")
    heatmap_title = f"{stock_name}({args.ticker})近{args.days}天筹码分布热力图"
    price_note = ""
    if args.price_min is not None or args.price_max is not None:
        min_label = f"{args.price_min:.2f}" if args.price_min is not None else "自动"
        max_label = f"{args.price_max:.2f}" if args.price_max is not None else "自动"
        price_note = f"，价格轴 {min_label} 到 {max_label}"
    heatmap_subtitle = (
        f"横轴日期，纵轴价格，颜色越深表示该价位筹码占比越高；区间 {peak_df['date_str'].iloc[0]} 至 "
        f"{peak_df['date_str'].iloc[-1]}{price_note}"
    )
    heatmap_svg = build_heatmap_svg(title=heatmap_title, subtitle=heatmap_subtitle, chips=chips_df)
    heatmap_path.write_text(heatmap_svg, encoding="utf-8")

    latest = peak_df.iloc[-1]
    print(f"svg_path={output_path.resolve()}")
    print(f"heatmap_path={heatmap_path.resolve()}")
    print(f"csv_path={csv_path.resolve()}")
    print(f"points={len(peak_df)}")
    print(f"latest_date={latest['date_str']}")
    print(f"latest_peak_percent={float(latest['peak_percent']):.2f}")
    print(f"latest_peak_price={float(latest['peak_price']):.2f}")


if __name__ == "__main__":
    main()
