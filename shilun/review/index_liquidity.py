"""Dated chart facts for the index review, using cached A-share daily bars.

Amounts use the same per-stock sample as the review, never overlapping index
turnover.  The exchange thresholds allocate the user's 1,000-yi full-market
rule by the preceding twenty sessions' turnover share.  This is a descriptive
display rule, not a new trading permission.
"""
from __future__ import annotations

from math import ceil, isfinite
from statistics import mean

from shilun.review.sources import at_limit

MARKETS = (("SH", "沪市"), ("SZ", "深市"), ("BJ", "北交所"))
EXPANSION_YI = 1000.0


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (ValueError, TypeError):
        return None


def exchange(ticker):
    suffix = str(ticker).rsplit(".", 1)[-1]
    return suffix if suffix in dict(MARKETS) else None


def change(bar, previous):
    close = number(bar.get("close"))
    # The source's ex-right reference wins over an unadjusted previous close.
    reference = number(bar.get("pre_close"))
    if reference is None or reference <= 0:
        reference = number(previous.get("close"))
    return close / reference - 1 if close is not None and close > 0 and reference is not None and reference > 0 else None


def breadth_distribution(current, previous, limits=None):
    limits = limits or {}
    bins = [{"key": "limit_up", "label": "涨停", "count": 0},
            {"key": "up_gt9", "label": ">9%", "count": 0}]
    bins += [{"key": f"up_{i}_{i+1}", "label": f"+{i}~{i+1}%", "count": 0} for i in range(8, -1, -1)]
    bins += [{"key": "flat", "label": "平", "count": 0}]
    bins += [{"key": f"down_{i}_{i+1}", "label": f"−{i}~{i+1}%", "count": 0} for i in range(9)]
    bins += [{"key": "down_gt9", "label": "<−9%", "count": 0},
             {"key": "limit_down", "label": "跌停", "count": 0}]
    by_key = {r["key"]: r for r in bins}
    missing = classified_limits = total = 0
    for ticker, bar in current.items():
        if exchange(ticker) is None:
            continue
        value = change(bar, previous.get(ticker, {}))
        if value is None:
            missing += 1
            continue
        total += 1
        limit = limits.get(ticker, {})
        up = number(limit.get("up_limit", limit.get("up")))
        down = number(limit.get("down_limit", limit.get("down")))
        if up is not None and up > 0 and down is not None and down > 0:
            classified_limits += 1
        if up is not None and up > 0 and at_limit(bar.get("close"), up) is True:
            key = "limit_up"
        elif down is not None and down > 0 and at_limit(bar.get("close"), down) is True:
            key = "limit_down"
        elif abs(value) <= .0001:
            key = "flat"
        else:
            magnitude = round(abs(value) * 100, 8)
            side = "up" if value > 0 else "down"
            key = f"{side}_gt9" if magnitude > 9 else f"{side}_{max(0, ceil(magnitude)-1)}_{max(0, ceil(magnitude)-1)+1}"
        by_key[key]["count"] += 1
    complete = bool(total) and not missing and classified_limits == total
    return {"status": "ready" if complete else "partial" if total else "missing",
            "bins": bins, "total": total, "missing_count": missing,
            "limit_classification_ready": classified_limits == total and bool(total),
            "limit_classification_count": classified_limits,
            "note": "同口径A股日线样本；涨跌停按当日价格上限／下限识别并先行分组，其余按涨跌幅分箱，平盘沿用±0.01%容差。"
                    + (" 部分涨跌停价格缺失，单列为已确认家数，其余按涨跌幅区间展示。" if classified_limits < total else "")}


def day_amounts(rows):
    grouped = {key: [] for key, _ in MARKETS}
    for ticker, bar in rows.items():
        market = exchange(ticker)
        if market:
            grouped[market].append(bar)
    amounts = {}
    coverage = {}
    for key, samples in grouped.items():
        values = [number(r.get("amount")) for r in samples]
        valid = [v for v in values if v is not None and v >= 0]
        coverage[key] = {"valid_n": len(valid), "total_n": len(samples)}
        amounts[key] = sum(valid) / 100_000 if samples and len(valid) == len(samples) else None
    return amounts, coverage


def market_rows(current, previous, *, sessions, bars_by_date):
    """One row per venue and a same-scope aggregate; no future day enters shares."""
    sessions = sorted(set(sessions))
    target = sessions[-1] if sessions else None
    prior_days = sessions[-21:-1]
    current_amount, current_coverage = day_amounts(current)
    previous_amount, _ = day_amounts(previous)
    history = [day_amounts(bars_by_date.get(day, {}))[0] for day in prior_days]
    history_ready = len(prior_days) == 20 and all(all(a[key] is not None for key, _ in MARKETS) for a in history)
    history_total = sum(sum(a.values()) for a in history) if history_ready else None
    shares = {key: sum(a[key] for a in history) / history_total if history_ready and history_total > 0 else None for key, _ in MARKETS}
    result = []
    for key, name in [*MARKETS, ("ALL", "全市场")]:
        samples = {t: r for t, r in current.items() if exchange(t) is not None and (key == "ALL" or exchange(t) == key)}
        changes = [change(r, previous.get(t, {})) for t, r in samples.items()]
        valid = [v for v in changes if v is not None]
        if key == "ALL":
            amount = sum(current_amount.values()) if all(v is not None for v in current_amount.values()) else None
            prior = sum(previous_amount.values()) if all(v is not None for v in previous_amount.values()) else None
            threshold, share = EXPANSION_YI, 1.0
            amount_valid = sum(v["valid_n"] for v in current_coverage.values())
        else:
            amount, prior, share = current_amount[key], previous_amount[key], shares[key]
            threshold = EXPANSION_YI * share if share is not None else None
            amount_valid = current_coverage[key]["valid_n"]
        delta = amount - prior if amount is not None and prior is not None else None
        expanded = delta >= threshold if delta is not None and threshold is not None else None
        result.append({"key": key, "name": name,
            "date": target,
            "up_count": sum(v > .0001 for v in valid),
            "down_count": sum(v < -.0001 for v in valid),
            "flat_count": sum(abs(v) <= .0001 for v in valid),
            "return_valid_count": len(valid), "stock_count": len(samples),
            "amount_valid_count": amount_valid,
            "amount_yi": round(amount, 4) if amount is not None else None,
            "previous_amount_yi": round(prior, 4) if prior is not None else None,
            "amount_change_yi": round(delta, 4) if delta is not None else None,
            "expansion_threshold_yi": round(threshold, 6) if threshold is not None else None,
            "expanded": expanded,
            "expansion_status": "unknown" if expanded is None else "expanded" if expanded else "not_expanded",
            "threshold_basis_days": 20 if history_ready else sum(all(a[key] is not None for key, _ in MARKETS) for a in history),
            "threshold_share": share,
            "coverage_note": "已缓存A股日线样本合计，不含指数／ETF重复成交；缺失金额不补0。"})
    return result


def volume_facts(sessions, bars_by_date):
    """Calendar-aligned series; a missing date breaks the full-window mean."""
    selected = sorted(set(sessions))[-61:]
    series = []
    for day in selected:
        amounts, coverage = day_amounts(bars_by_date.get(day, {}))
        total = sum(amounts.values()) if all(v is not None for v in amounts.values()) else None
        series.append({"date": day, "amount_yi": round(total, 4) if total is not None else None,
            "amount_valid_count": sum(v["valid_n"] for v in coverage.values()),
            "stock_count": sum(v["total_n"] for v in coverage.values())})
    result = {"series": series,
        "current_yi": series[-1]["amount_yi"] if series else None,
        "scope": "沪深北A股个股日线成交额合计，不含指数／ETF重复成交；单位亿元"}
    for n in (20, 60):
        values = [r["amount_yi"] for r in series[-n:]]
        good = [v for v in values if v is not None]
        result[f"ma{n}_days"] = len(good)
        result[f"ma{n}_yi"] = round(mean(good), 4) if len(good) == n else None
    return result


THRESHOLD_RULE = "全市场较前一交易日增加至少1000亿元；单市场阈值按此前20个交易日该市场成交额占同口径全市场的份额分配1000亿元，20日三市场金额未齐则单市场未知。此为展示用研究规则。"
