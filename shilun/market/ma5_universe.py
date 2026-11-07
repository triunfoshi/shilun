"""Full-market MA5 daily selection shared by production and research."""
from __future__ import annotations
from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Callable, TypeVar
import math
import pandas as pd
from shilun.market.candidates import evaluate_ma5_selection_gate, build_candidates
from shilun.market.ma5_liquidity import MA5_SELECTION_RULE_VERSION, market_turnover

_DailyValue = TypeVar("_DailyValue")


def reuse_daily_computation(
    cache: dict[str, Any] | None,
    slot: str,
    inputs: tuple[Any, ...],
    compute: Callable[[], _DailyValue],
) -> _DailyValue:
    """Reuse daily-only work within one generation, never minute-dependent results.

    Input contents, not Python object identities, guard each slot. Callers may
    rebuild frames or update facts between phases; changed contents must miss.
    The cache is supplied by the request/worker and is never global or persisted.
    Copies isolate it from candidate enrichment and downstream frame mutations.
    """
    if cache is None:
        return compute()

    digest = sha256()
    for value in inputs:
        if isinstance(value, pd.DataFrame):
            digest.update(b"dataframe:")
            digest.update(repr((list(value.columns), list(map(str, value.dtypes)), str(value.index.dtype))).encode())
            native_types = ["number", "bool", "datetime", "datetimetz", "timedelta"]
            native = value.select_dtypes(include=native_types)
            if len(native.columns):
                digest.update(pd.util.hash_pandas_object(native, index=False).values.tobytes())
            # Object hashing coerces 1 and "1" to the same representation.
            # Preserve types and nested metadata for all non-native columns.
            other = value.select_dtypes(exclude=native_types)
            digest.update(json.dumps(
                (value.index.tolist(), other.to_numpy(dtype=object).tolist()),
                sort_keys=True, default=_signature_value,
            ).encode())
        else:
            digest.update(b"value:")
            digest.update(json.dumps(value, sort_keys=True, default=_signature_value).encode())
        digest.update(b"\0")
    signature = digest.hexdigest()
    entry = cache.get(slot)
    if entry is not None and entry["signature"] == signature:
        return deepcopy(entry["value"])
    result = compute()
    cache[slot] = {"signature": signature, "value": deepcopy(result)}
    return result


def _signature_value(value: Any) -> dict[str, str]:
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}", "value": str(value)}


def iso(value: Any) -> str:
    text = str(value)[:10]
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 and text.isdigit() else text


def finite(value: Any, *, positive: bool = False) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and (not positive or value > 0)


def unique_rows(rows: list[dict], keys: tuple[str, ...], label: str) -> None:
    counts = Counter(tuple(row.get(key) for key in keys) for row in rows)
    if any(n > 1 for n in counts.values()):
        raise ValueError(f"Duplicate {label} keys; scan aborted")


def evaluate_stock(ticker: str, name: str, bars: list[dict], days: list[str], total_mv: Any, *, market_amount_cny: float | None = None) -> dict:
    """Reuse the five production predicates; add cap and strict data eligibility.

    A full 24-session contiguous window is required to calculate all five MA20s.
    Prices and pct_change intentionally follow the existing unadjusted MA5 gate.
    """
    if len(days) < 24:
        raise ValueError("At least 24 market trading dates are required")
    rows = sorted((dict(r, date=iso(r.get("date"))) for r in bars if iso(r.get("date")) <= days[-1]), key=lambda r: r["date"])
    unique_rows(rows, ("date",), ticker)
    expected = days[-24:]
    by_date = {r["date"]: r for r in rows}
    errors = []
    missing = [d for d in expected if d not in by_date]
    if missing:
        errors.append("missing_daily_bars")
    if any(not finite(by_date[d].get("close"), positive=True) for d in expected if d in by_date):
        errors.append("invalid_close")
    if any(not finite(by_date.get(d, {}).get("amount"), positive=True) for d in days[-7:]):
        errors.append("incomplete_amount_7d")
    if not finite(total_mv, positive=True):
        errors.append("missing_current_market_cap")
    if not name:
        errors.append("missing_stock_name")
    # Only valid prices enter moving averages; invalid data still fails eligibility.
    valid_rows = [dict(r, amount=r.get("amount") if finite(r.get("amount"), positive=True) else None)
                  for r in rows if finite(r.get("close"), positive=True)]
    frame = pd.DataFrame(valid_rows)
    if not frame.empty:
        for window in (5, 10, 20):
            frame[f"ma{window}"] = frame["close"].rolling(window, min_periods=window).mean()
        frame["pct_chg"] = frame["close"].pct_change(fill_method=None)
        frame["total_mv"] = total_mv if finite(total_mv, positive=True) else None
        prepared = frame.to_dict("records")
    else:
        prepared = []
    gate = evaluate_ma5_selection_gate(ticker=ticker, name=name, bars=prepared, market_amount_cny=market_amount_cny)
    original_passed = all(c["passed"] for c in gate["criteria"] if c["id"] != "avg_amount_7d_upper")
    if errors:
        gate["criteria"].append({"id": "data_window", "label": "日线与市值覆盖", "passed": False,
                                 "actual": "、".join(errors), "rule": "24个连续交易日收盘、7日成交额和同日市值完整"})
        gate["failure_ids"].append("data_window")
        gate["failure_reasons"].append("数据不足：" + "、".join(errors))
        gate["passed"] = False
        gate["summary"] = "；".join(gate["failure_reasons"])
    criteria = gate["criteria"]
    return {"ticker": ticker, "name": name, "data_complete": not errors,
            "data_errors": errors, "missing_dates": missing,
            "original_conditions_passed": not errors and original_passed,
            "passed": not errors and all(c["passed"] for c in criteria),
            "selection_gate": gate, "criteria": criteria, "failure_ids": [c["id"] for c in criteria if not c["passed"]],
            "metrics": gate["metrics"],
            "close": by_date.get(days[-1], {}).get("close") if finite(by_date.get(days[-1], {}).get("close"), positive=True) else None,
            "window_dates": days[-7:]}


def scan_universe(basics: list[dict], bars: list[dict], daily_basic: list[dict], days: list[str], *,
                  daily_computation_cache: dict[str, Any] | None = None) -> dict:
    return reuse_daily_computation(
        daily_computation_cache, "ma5_universe_scan",
        (MA5_SELECTION_RULE_VERSION, basics, bars, daily_basic, days),
        lambda: _scan_universe(basics, bars, daily_basic, days),
    )


def _scan_universe(basics: list[dict], bars: list[dict], daily_basic: list[dict], days: list[str]) -> dict:
    unique_rows(basics, ("ts_code",), "stock_basic")
    unique_rows(bars, ("ticker", "date"), "daily_bars")
    unique_rows(daily_basic, ("ts_code", "trade_date"), "daily_basic")
    if len(days) < 24 or days != sorted(set(days)):
        raise ValueError("24 ordered unique trading dates required")
    end = days[-1]
    info = {r["ts_code"]: r for r in basics if str(r.get("ts_code", "")).endswith((".SH", ".SZ", ".BJ"))
            and (not r.get("list_date") or iso(r["list_date"]) <= end)
            and (not r.get("delist_date") or iso(r["delist_date"]) > end)}
    turnover = market_turnover(list(info.values()), bars, daily_basic, end)
    grouped = defaultdict(list)
    for r in bars:
        if r.get("ticker") in info and iso(r["date"]) <= end:
            grouped[r["ticker"]].append(r)
    caps = {r["ts_code"]: r.get("total_mv") for r in daily_basic if iso(r["trade_date"]) == end}
    audits = []
    for ticker, basic in sorted(info.items()):
        r = evaluate_stock(ticker, basic.get("name", ""), grouped[ticker], days, caps.get(ticker),
                           market_amount_cny=turnover["market_amount_cny"])
        r["industry"] = basic.get("industry") or "分类待补齐"
        r["industry_code"] = basic.get("industry_code")
        audits.append(r)
    return {"audits": audits, "liquidity": turnover,
            "selection_scope": {"kind": "full_market", "independent_from_sector_ranking": True,
                                "rule_version": MA5_SELECTION_RULE_VERSION, "universe_count": len(info),
                                "data_complete_count": sum(r["data_complete"] for r in audits),
                                "selected_count": sum(r["passed"] for r in audits),
                                "window_dates": days[-7:], "liquidity": turnover}}


def build_full_market_candidates(*, market_bars: pd.DataFrame, stock_basic: pd.DataFrame,
                                daily_basic: pd.DataFrame, moneyflow: pd.DataFrame | None,
                                days: list[str], sectors: list[dict], market_gate: dict | None,
                                breakout_events_lookup: dict | None = None,
                                stock_minute_bars_lookup: dict | None = None,
                                benchmark_minute_bars: list | None = None,
                                require_minute_data: bool = False,
                                daily_computation_cache: dict[str, Any] | None = None) -> dict:
    """Independent daily hard gate, then existing timing/risk scoring and sector context."""
    from shilun.market.sector import _prepare_stock_frame

    def records(frame):
        return frame.to_dict("records") if frame is not None and not frame.empty else []

    raw_bars = records(market_bars)
    for bar in raw_bars:
        bar["date"] = iso(bar.get("date") or bar.get("trade_date"))
        bar["ticker"] = bar.get("ticker") or bar.get("ts_code")
    if len(days) < 24:
        return {"candidates": [], "selection_rule_version": MA5_SELECTION_RULE_VERSION,
                "selection_scope": {"kind": "full_market", "independent_from_sector_ranking": True,
                                    "rule_version": MA5_SELECTION_RULE_VERSION, "status": "insufficient_history",
                                    "universe_count": len(records(stock_basic)), "data_complete_count": 0,
                                    "selected_count": 0, "reason": "至少需要24个交易日历史才能验证连续5日MA20排列"}}
    scanned = scan_universe(records(stock_basic), raw_bars, records(daily_basic), days,
                            daily_computation_cache=daily_computation_cache)
    # Retain former hard-condition hits that only fail the new cap for explanation.
    pool = [r for r in scanned["audits"] if r["original_conditions_passed"]]
    tickers = {r["ticker"] for r in pool}
    if not pool:
        return {"candidates": [], "selection_scope": scanned["selection_scope"],
                "selection_rule_version": MA5_SELECTION_RULE_VERSION}
    selected_bars = pd.DataFrame([b for b in raw_bars if b["ticker"] in tickers])
    frame = reuse_daily_computation(
        daily_computation_cache, "ma5_candidate_daily_frame",
        (MA5_SELECTION_RULE_VERSION, selected_bars, stock_basic, daily_basic, moneyflow),
        lambda: _prepare_stock_frame(selected_bars, stock_basic=stock_basic, daily_basic=daily_basic,
                                     moneyflow=moneyflow, exclude_st=False, require_industry=False),
    )
    groups = {}
    for row in pool:
        matches = [s for s in sectors if row["ticker"] in (s.get("member_tickers") or [])]
        # Industry context is joined by the canonical ID, never selected for return.
        sector = next((s for s in matches if s.get("sector_code") == row.get("industry_code")),
                      {"sector_name": row["industry"], "sector_code": row.get("industry_code")})
        name = sector["sector_name"]
        if name not in groups:
            groups[name] = {**sector, "candidate_universe": []}
        groups[name]["candidate_universe"].append({"ticker": row["ticker"], "name": row["name"],
                                                   "sector_name": name, "candidate_source": "full_market_ma5"})
    candidates = build_candidates(list(groups.values()), frame, days[-1], market_gate=market_gate,
                                  trend_sectors=sorted(sectors, key=lambda s: (s.get("metrics") or {}).get("mainline_rank") or 99999),
                                  breakout_events_lookup=breakout_events_lookup,
                                  market_amount_cny=scanned["liquidity"]["market_amount_cny"],
                                  selection_gate_lookup={r["ticker"]: r["selection_gate"] for r in pool})
    from shilun.market.intraday_resonance import compute_intraday_resonance_score
    pending, ready = [], []
    for row in candidates:
        row["selection_scope_kind"] = "full_market"
        resonance = compute_intraday_resonance_score(
            (stock_minute_bars_lookup or {}).get(row["ticker"]), benchmark_minute_bars)
        row["selection_minute_confirmation"] = resonance
        if (row.get("selection_gate") or {}).get("passed"):
            (ready if resonance["has_data"] else pending).append(row["ticker"])
            if require_minute_data and not resonance["has_data"]:
                row.update(allowed_new_position=False, size_hint=0.0, final_signal="watch",
                           final_signal_label="待分钟确认", gate_downgrade_reason="分钟共振覆盖不足，保留日线候选并禁止新仓")
    return {"candidates": candidates, "selection_scope": scanned["selection_scope"],
            "selection_minute_coverage": {"candidate_count": len(ready) + len(pending), "ready_count": len(ready),
                                          "pending_tickers": pending, "strict": require_minute_data},
            "selection_rule_version": MA5_SELECTION_RULE_VERSION}
