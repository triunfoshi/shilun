"""Shared MA5 liquidity rule. All public amounts in this module are RMB yuan."""
from __future__ import annotations

import math

MA5_SELECTION_RULE_VERSION = "ma5_selection_v2_market_liquidity"


def liquidity_rule(market_amount_cny: float | None) -> dict:
    valid = isinstance(market_amount_cny, (int, float)) and not isinstance(market_amount_cny, bool) and math.isfinite(market_amount_cny) and market_amount_cny > 0
    if not valid:
        return {"available": False, "regime": "missing", "market_amount_cny": None,
                "max_avg_amount_cny": None, "label": "大盘成交额缺失，暂不入池"}
    if market_amount_cny < 2_100_000_000_000:
        regime, cap, label = "low", 5_000_000_000, "大盘不足2.1万亿：7日均额低于50亿"
    elif market_amount_cny <= 2_300_000_000_000:
        regime, cap, label = "medium", 10_000_000_000, "大盘2.1至2.3万亿：7日均额低于100亿"
    else:
        regime, cap, label = "high", None, "大盘超过2.3万亿：7日均额不设上限"
    return {"available": True, "regime": regime, "market_amount_cny": market_amount_cny,
            "max_avg_amount_cny": cap, "label": label}


def market_turnover(basics: list[dict], bars: list[dict], daily_basic: list[dict], day: str) -> dict:
    """Sum dated SH/SZ/BJ stock bars, including ST; never sum overlapping indices."""
    tickers = {r["ts_code"] for r in basics if str(r.get("ts_code", "")).endswith((".SH", ".SZ", ".BJ"))}
    current = [r for r in bars if str(r.get("date"))[:10] == day and r.get("ticker") in tickers]
    observed = [r["ticker"] for r in current]
    if len(set(observed)) != len(observed):
        raise ValueError("Duplicate stock/date rows in market turnover")
    expected = {r["ts_code"] for r in daily_basic if str(r.get("trade_date", "")).replace("-", "") == day.replace("-", "") and r.get("ts_code") in tickers}
    invalid = [r["ticker"] for r in current if not isinstance(r.get("amount"), (int, float)) or isinstance(r.get("amount"), bool) or not math.isfinite(r["amount"]) or r["amount"] < 0]
    missing = sorted(expected - set(observed))
    complete = bool(expected and current) and not invalid and not missing
    amount = math.fsum(r["amount"] for r in current) * 1000 if complete else None
    return {**liquidity_rule(amount), "analysis_date": day, "scope": "沪深北A股当日成交额，含ST，不含指数/ETF重复成交",
            "stock_count": len(current), "daily_basic_count": len(expected), "missing_tickers": missing,
            "invalid_amount_tickers": invalid, "source": "market_daily_bars.amount（千元转元）"}
