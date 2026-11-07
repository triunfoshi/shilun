from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


UNKNOWN_INDUSTRY = "unknown_industry"
UNTAGGED = "untagged"
UNKNOWN_REGIME = "unknown"


@dataclass(frozen=True)
class BacktestAttributionResult:
    industry_attribution: pd.DataFrame
    tag_attribution: pd.DataFrame
    regime_attribution: pd.DataFrame


def build_backtest_attribution(period_returns: pd.DataFrame, positions: pd.DataFrame) -> BacktestAttributionResult:
    """Build M4 P1 attribution tables from backtest return and position details."""
    normalized_positions = normalize_position_returns(positions)
    return BacktestAttributionResult(
        industry_attribution=build_industry_attribution(normalized_positions),
        tag_attribution=build_tag_attribution(normalized_positions),
        regime_attribution=build_regime_attribution(period_returns, normalized_positions),
    )


def normalize_position_returns(positions: pd.DataFrame) -> pd.DataFrame:
    if positions.empty or "analysis_date" not in positions or "weight" not in positions:
        return pd.DataFrame()

    frame = positions.copy()
    return_col = _position_return_column(frame)
    if return_col is None:
        return pd.DataFrame()

    frame["analysis_date"] = frame["analysis_date"].map(_normalize_date_text)
    frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce").fillna(0.0)
    frame["return_value"] = pd.to_numeric(frame[return_col], errors="coerce").fillna(0.0)
    if "contribution" in frame:
        frame["contribution"] = pd.to_numeric(frame["contribution"], errors="coerce")
        frame["contribution"] = frame["contribution"].where(frame["contribution"].notna(), frame["weight"] * frame["return_value"])
    else:
        frame["contribution"] = frame["weight"] * frame["return_value"]
    frame["industry"] = frame.get("industry", UNKNOWN_INDUSTRY)
    frame["industry"] = frame["industry"].map(_clean_industry)
    frame["candidate_tags"] = frame.get("candidate_tags", UNTAGGED)
    if "market_trend_score" in frame:
        frame["market_trend_score"] = pd.to_numeric(frame["market_trend_score"], errors="coerce")
    else:
        frame["market_trend_score"] = pd.NA
    return frame.dropna(subset=["analysis_date"]).reset_index(drop=True)


def build_industry_attribution(positions: pd.DataFrame) -> pd.DataFrame:
    if positions.empty:
        return pd.DataFrame()

    daily = (
        positions.groupby(["analysis_date", "industry"], dropna=False)
        .agg(
            weight=("weight", "sum"),
            contribution=("contribution", "sum"),
            ticker_count=("ticker", "nunique") if "ticker" in positions else ("weight", "size"),
        )
        .reset_index()
    )
    daily["industry_return"] = daily["contribution"] / daily["weight"].where(daily["weight"] != 0)
    hhi = daily.groupby("analysis_date")["weight"].apply(lambda values: float((values**2).sum()))
    avg_hhi = _round_or_none(float(hhi.mean()) if not hhi.empty else None)
    max_hhi = _round_or_none(float(hhi.max()) if not hhi.empty else None)

    rows: list[dict[str, Any]] = []
    for industry, group in daily.groupby("industry", sort=True):
        rows.append(
            {
                "industry": industry,
                "period_count": int(group["analysis_date"].nunique()),
                "sample_count": int(group["ticker_count"].sum()),
                "avg_weight": _round_or_none(float(group["weight"].mean())),
                "max_weight": _round_or_none(float(group["weight"].max())),
                "total_contribution": _round_or_none(float(group["contribution"].sum())),
                "mean_contribution": _round_or_none(float(group["contribution"].mean())),
                "mean_return": _round_or_none(float(group["industry_return"].dropna().mean()) if not group["industry_return"].dropna().empty else None),
                "win_rate": _round_or_none(float((group["industry_return"].dropna() > 0).mean()) if not group["industry_return"].dropna().empty else None),
                "avg_portfolio_industry_hhi": avg_hhi,
                "max_portfolio_industry_hhi": max_hhi,
            }
        )
    return pd.DataFrame(rows).sort_values("total_contribution", ascending=False).reset_index(drop=True)


def build_tag_attribution(positions: pd.DataFrame) -> pd.DataFrame:
    if positions.empty:
        return pd.DataFrame()

    expanded_rows: list[dict[str, Any]] = []
    for row in positions.to_dict(orient="records"):
        tags = _split_tags(row.get("candidate_tags")) or [UNTAGGED]
        for tag in tags:
            expanded_rows.append(
                {
                    "analysis_date": row.get("analysis_date"),
                    "ticker": row.get("ticker"),
                    "tag": tag,
                    "weight": row.get("weight", 0.0),
                    "return_value": row.get("return_value", 0.0),
                    "contribution": row.get("contribution", 0.0),
                }
            )
    expanded = pd.DataFrame(expanded_rows)
    if expanded.empty:
        return pd.DataFrame()

    daily = (
        expanded.groupby(["analysis_date", "tag"], dropna=False)
        .agg(
            weight=("weight", "sum"),
            contribution=("contribution", "sum"),
            ticker_count=("ticker", "nunique"),
        )
        .reset_index()
    )
    daily["tag_return"] = daily["contribution"] / daily["weight"].where(daily["weight"] != 0)

    rows: list[dict[str, Any]] = []
    for tag, group in daily.groupby("tag", sort=True):
        rows.append(
            {
                "tag": tag,
                "period_count": int(group["analysis_date"].nunique()),
                "sample_count": int(group["ticker_count"].sum()),
                "avg_weight": _round_or_none(float(group["weight"].mean())),
                "max_weight": _round_or_none(float(group["weight"].max())),
                "total_contribution": _round_or_none(float(group["contribution"].sum())),
                "mean_contribution": _round_or_none(float(group["contribution"].mean())),
                "mean_return": _round_or_none(float(group["tag_return"].dropna().mean()) if not group["tag_return"].dropna().empty else None),
                "win_rate": _round_or_none(float((group["tag_return"].dropna() > 0).mean()) if not group["tag_return"].dropna().empty else None),
            }
        )
    return pd.DataFrame(rows).sort_values("total_contribution", ascending=False).reset_index(drop=True)


def build_regime_attribution(period_returns: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    if period_returns.empty:
        return pd.DataFrame()

    returns = period_returns.copy()
    returns["analysis_date"] = returns["analysis_date"].map(_normalize_date_text)
    if positions.empty or "market_trend_score" not in positions:
        returns["market_regime"] = UNKNOWN_REGIME
    else:
        scores = (
            positions.groupby("analysis_date")["market_trend_score"]
            .mean()
            .reset_index()
            .rename(columns={"market_trend_score": "avg_market_trend_score"})
        )
        returns = returns.merge(scores, on="analysis_date", how="left")
        returns["market_regime"] = returns["avg_market_trend_score"].map(classify_market_regime)

    for column in ("net_return", "benchmark_return", "excess_return", "turnover"):
        if column in returns:
            returns[column] = pd.to_numeric(returns[column], errors="coerce")

    rows: list[dict[str, Any]] = []
    for regime, group in returns.groupby("market_regime", sort=True):
        net = group["net_return"].dropna() if "net_return" in group else pd.Series(dtype=float)
        benchmark = group["benchmark_return"].dropna() if "benchmark_return" in group else pd.Series(dtype=float)
        excess = group["excess_return"].dropna() if "excess_return" in group else pd.Series(dtype=float)
        aligned = group[["net_return", "benchmark_return"]].dropna() if {"net_return", "benchmark_return"}.issubset(group.columns) else pd.DataFrame()
        rows.append(
            {
                "market_regime": regime,
                "period_count": int(len(group)),
                "mean_return": _round_or_none(float(net.mean()) if not net.empty else None),
                "mean_benchmark_return": _round_or_none(float(benchmark.mean()) if not benchmark.empty else None),
                "mean_excess_return": _round_or_none(float(excess.mean()) if not excess.empty else None),
                "total_return": _round_or_none(_compound(net)),
                "win_rate": _round_or_none(float((net > 0).mean()) if not net.empty else None),
                "outperform_rate": _round_or_none(float((aligned["net_return"] > aligned["benchmark_return"]).mean()) if not aligned.empty else None),
                "avg_turnover": _round_or_none(float(group["turnover"].dropna().mean()) if "turnover" in group and not group["turnover"].dropna().empty else None),
            }
        )
    return pd.DataFrame(rows).sort_values("market_regime").reset_index(drop=True)


def classify_market_regime(score: Any) -> str:
    numeric = pd.to_numeric(pd.Series([score]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return UNKNOWN_REGIME
    if float(numeric) >= 60.0:
        return "strong"
    if float(numeric) >= 45.0:
        return "neutral"
    return "weak"


def render_attribution_markdown(result: BacktestAttributionResult) -> str:
    lines = [
        "# 候选池组合归因报告",
        "",
        "本报告是 M4 P1 归因层输出，用于解释组合收益来自哪些行业、候选标签和市场状态。",
        "",
        "## 行业归因",
        "",
        _markdown_table(result.industry_attribution),
        "",
        "## 候选标签归因",
        "",
        _markdown_table(result.tag_attribution),
        "",
        "## 市场状态归因",
        "",
        _markdown_table(result.regime_attribution),
        "",
        "## 口径说明",
        "",
        "- `contribution = weight * return`，`daily_nav` 使用日收益，`label_horizon` 使用持有期未来收益。",
        "- 行业 HHI 越高，说明组合行业集中度越高；这一版先做暴露和贡献拆解，不替代正式 Brinson 归因。",
        "- 候选标签允许一只股票同时命中多个标签，因此标签贡献是解释维度，不应直接相加为组合总收益。",
        "- 市场状态由持仓明细中的 `market_trend_score` 分桶：`strong >= 60`，`45 <= neutral < 60`，`weak < 45`。",
        "",
    ]
    return "\n".join(lines)


def _position_return_column(frame: pd.DataFrame) -> str | None:
    if "daily_return" in frame:
        return "daily_return"
    future_cols = sorted(column for column in frame.columns if str(column).startswith("future_return_"))
    return future_cols[0] if future_cols else None


def _clean_industry(value: Any) -> str:
    if value is None:
        return UNKNOWN_INDUSTRY
    try:
        if pd.isna(value):
            return UNKNOWN_INDUSTRY
    except TypeError:
        pass
    text = str(value).strip()
    return text if text else UNKNOWN_INDUSTRY


def _split_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_date_text(value: Any) -> str:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _compound(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float((1.0 + clean).prod() - 1.0)


def _round_or_none(value: float | None) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 6)


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "暂无归因结果。"
    columns = [str(column) for column in frame.columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame.to_dict(orient="records"):
        lines.append("| " + " | ".join(_format_table_value(row.get(column)) for column in columns) + " |")
    return "\n".join(lines)


def _format_table_value(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return str(value)
