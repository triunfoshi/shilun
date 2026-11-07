"""Read-only all-market MA5 hard-condition research, independent of sector ranks.

Run: python -m research.scan_ma5_market --date YYYY-MM-DD --output-dir PATH
The production candidate/execution pools are never written by this command.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from shilun.market.ma5_universe import evaluate_stock, iso, finite, unique_rows, scan_universe
from shilun.market.ma5_liquidity import MA5_SELECTION_RULE_VERSION

FLOW_FIELDS = ("net_mf_amount", "buy_lg_amount", "buy_elg_amount", "sell_lg_amount", "sell_elg_amount")
RULE_ID = MA5_SELECTION_RULE_VERSION


def flow_windows(members: set[str], days: list[str], lookup: dict) -> dict:
    if len(days) < 5 or len(set(days)) != len(days) or sorted(days) != days:
        raise ValueError("Five ordered, unique market trading dates are required")
    result = {}
    for n in (1, 3, 5):
        keys = [(ticker, d) for ticker in sorted(members) for d in days[-n:]]
        covered_net = [lookup[k] for k in keys if k in lookup and finite(lookup[k].get("net_mf_amount"))]
        covered_large = [lookup[k] for k in keys if k in lookup and all(finite(lookup[k].get(f)) for f in FLOW_FIELDS[1:])]
        net_complete = bool(keys) and len(covered_net) == len(keys)
        large_complete = bool(keys) and len(covered_large) == len(keys)
        daily_net = [sum(lookup[(t, d)]["net_mf_amount"] for t in members) / 10_000 for d in days[-n:]] if net_complete else []
        result[str(n)] = {
            "dates": days[-n:], "expected_observations": len(keys),
            "net_covered": len(covered_net), "large_covered": len(covered_large),
            "net_complete": net_complete, "large_complete": large_complete,
            "net_yi": round(sum(r["net_mf_amount"] for r in covered_net) / 10_000, 6) if net_complete else None,
            "large_yi": round(sum(r["buy_lg_amount"] + r["buy_elg_amount"] - r["sell_lg_amount"] - r["sell_elg_amount"] for r in covered_large) / 10_000, 6) if large_complete else None,
            "positive_net_days": sum(v > 0 for v in daily_net) if net_complete else None,
            "missing_net": [{"ticker": t, "date": d} for t, d in keys if (t, d) not in lookup or not finite(lookup[(t, d)].get("net_mf_amount"))],
        }
    return result


def scan(basics: list[dict], bars: list[dict], daily_basic: list[dict], days: list[str], flows: list[dict], sectors: dict) -> dict:
    end = days[-1]
    unique_rows(basics, ("ts_code",), "stock_basic")
    unique_rows(bars, ("ticker", "date"), "daily_bars")
    unique_rows(daily_basic, ("ts_code", "trade_date"), "daily_basic")
    unique_rows(flows, ("ts_code", "trade_date"), "moneyflow")
    info = {r["ts_code"]: r for r in basics if r.get("ts_code", "").endswith((".SH", ".SZ", ".BJ"))
            and (not r.get("list_date") or iso(r["list_date"]) <= end)
            and (not r.get("delist_date") or iso(r["delist_date"]) > end)}
    grouped = defaultdict(list)
    for row in bars:
        if row["ticker"] in info and iso(row["date"]) <= end:
            grouped[row["ticker"]].append(row)
    caps = {r["ts_code"]: r.get("total_mv") for r in daily_basic if iso(r["trade_date"]) == end}
    lookup = {(r["ts_code"], iso(r["trade_date"])): r for r in flows if iso(r["trade_date"]) in days[-5:]}
    universe_scan = scan_universe(basics, bars, daily_basic, days)
    audited = universe_scan["audits"]
    liquidity = universe_scan["liquidity"]
    selected = [row for row in audited if row["passed"]]
    selected.sort(key=lambda r: (-r["metrics"]["avg_amount_7d"], r["ticker"]))
    original = [row for row in audited if row["original_conditions_passed"]]
    excluded_cap = [row for row in original if not row["passed"]]
    old_pool = {r["ticker"] for r in sectors.get("candidates", []) if r.get("ticker")}
    relevant = {r["ticker"] for r in selected}
    sector_results = []
    seen_members = {}
    for sector in sectors.get("all_sectors", []):
        full = set(sector.get("member_tickers") or [])
        if not full.intersection(relevant):
            continue
        # Identical memberships are one observation, even with several labels.
        signature = tuple(sorted(full))
        alias = {"name": sector.get("sector_name"), "code": sector.get("sector_code"),
                 "trend_rank": (sector.get("metrics") or {}).get("mainline_rank")}
        if signature in seen_members:
            seen_members[signature]["aliases"].append(alias)
            continue
        members = {t for t in full if t in info and "ST" not in str(info[t].get("name", "")).upper()}
        metrics = sector.get("metrics") or {}
        result = {"name": sector.get("sector_name"), "code": sector.get("sector_code"),
                  "trend_rank": metrics.get("mainline_rank"),
                  "trend_score": (sector.get("scores") or {}).get("sector_mainline_score"),
                  "full_member_count": len(full), "fund_member_count": len(members),
                  "excluded_members": sorted(full - members), "member_tickers": sorted(full),
                  "selected_tickers": sorted(full.intersection(relevant)), "aliases": [alias],
                  "flow": flow_windows(members, days, lookup)}
        sector_results.append(result)
        seen_members[signature] = result
    sector_results.sort(key=lambda s: (s["trend_rank"] or 99999, s["code"] or ""))
    for row in selected:
        row["flow"] = flow_windows({row["ticker"]}, days, lookup)
        row["in_original_sector_candidate_pool"] = row["ticker"] in old_pool
        matching = [s for s in sector_results if row["ticker"] in s["selected_tickers"]]
        row["matching_sector_codes"] = [s["code"] for s in matching]
        industries = sorted([s for s in matching if str(s["code"]).endswith(".SI")],
                            key=lambda s: (s["full_member_count"], s["code"]))
        row["primary_industry_sector_code"] = industries[0]["code"] if industries else None
        row["stock_flow_positive_1_3_5"] = all(row["flow"][str(n)]["net_complete"] and row["flow"][str(n)]["net_yi"] > 0 for n in (1, 3, 5))
    return {
        "analysis_date": end, "rule_id": RULE_ID, "liquidity": liquidity, "window_dates": days[-7:],
        "ma_history_dates": days[-24:], "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": "沪深北股票基础名单，全市场先筛个股；板块仅作后验交叉核对；生效状态见publication",
        "units": {"daily_amount": "千元", "total_mv": "万元", "moneyflow": "万元", "report": "亿元"},
        "rules": {"avg_amount_7d_min_cny": 880_000_000, "avg_amount_7d_max_exclusive_cny": liquidity["max_avg_amount_cny"],
                  "total_mv_min_cny": 10_000_000_000, "min_contiguous_bars": 24,
                  "sector_crosscheck": "仅列板块趋势排名与1/3/5日资金，不新增板块入选门槛；报告展示完整成分<=50的细分方向，JSON保留全部相关方向"},
        "counts": {"universe": len(info), "current_daily_bars": sum(end in {iso(b["date"]) for b in grouped[t]} for t in info),
                   "data_complete": sum(r["data_complete"] for r in audited),
                   "data_incomplete": sum(not r["data_complete"] for r in audited),
                   "original_hard_conditions": len(original), "excluded_by_cap": len(excluded_cap),
                   "selected": len(selected), "original_sector_pool": len(old_pool),
                   "overlap_original_sector_pool": len(relevant.intersection(old_pool)),
                   "stock_flow_positive_1_3_5": sum(r["stock_flow_positive_1_3_5"] for r in selected)},
        "criterion_pass_counts": dict(Counter(c["id"] for r in audited if r["data_complete"] for c in r["criteria"] if c["passed"])),
        "data_error_counts": dict(Counter(e for r in audited for e in r["data_errors"])),
        "selected": selected, "excluded_by_cap": excluded_cap, "sector_crosschecks": sector_results,
        "price_evidence": {r["ticker"]: {"daily_basic_date": end, "total_mv": caps[r["ticker"]],
                            "bars": sorted([b for b in grouped[r["ticker"]] if iso(b["date"]) in days[-24:]], key=lambda b: b["date"])}
                           for r in original},
        "audit": audited,
        "limitations": [
            "使用当前股票基础名单和扫描日板块成员快照；不是无幸存者偏差的历史回测。",
            "沿用生产未复权收盘价与相邻收盘涨幅；涨停以9.5%/19.5%/29.5%近似识别，未声称精确封板。",
            "交易日窗口包含扫描日；严格要求24个连续市场交易日有效收盘、7日有效成交额及扫描日总市值。",
            "1/3/5日累计净流为正不代表每天流入；另附5日正流入天数。资金缺失不补零。",
            "板块同成分去重；不同成分但高度重叠的方向仍可能相关，不能视为独立证据。",
            "本结果仅通过入池硬条件，未评估买点、择时、市场许可、分钟确认、止损或收益胜率。",
        ],
    }


def markdown_report(result: dict) -> str:
    c = result["counts"]
    applied = bool(result.get("publication", {}).get("applied"))
    out = [f"# 全市场 MA5 动态量能扫描 · {result['analysis_date']}", "",
           f"股票基础名单 **{c['universe']}** 只；扫描日有日线 **{c['current_daily_bars']}** 只；数据完整 **{c['data_complete']}** 只；原五项硬条件通过 **{c['original_hard_conditions']}** 只；大盘量能上限剔除 **{c['excluded_by_cap']}** 只；最终筛选名单 **{c['selected']}** 只。", "",
           f"大盘当日成交额：{(result['liquidity']['market_amount_cny'] or 0) / 1e12:.4f}万亿元。7个交易日（含扫描日）：{'、'.join(result['window_dates'])}。保留8.8亿元下限；**{result['liquidity']['label']}**。", "",
           f"与原前8板块候选池（{c['original_sector_pool']}只）交集 **{c['overlap_original_sector_pool']}** 只。个股1/3/5日累计净流入均为正 **{c['stock_flow_positive_1_3_5']}** 只。板块趋势与资金仅作交叉核对，不新增板块筛选门槛。", "",
           "名单按7日平均成交额从高到低展示，不代表买入优先级；金额均为亿元。", "",
           "|股票|代码|行业|7日均额|总市值|1日净流|3日净流|5日净流|5日流入天数|申万细分归属|",
           "|---|---|---|---:|---:|---:|---:|---:|---:|---|"]
    out.insert(2, "状态：已发布到现行MA5候选缓存，盘中买点和风险门继续独立校验。\n" if applied else "状态：研究试算，尚未发布。\n")
    sector_lookup = {s["code"]: s for s in result["sector_crosschecks"]}
    number = lambda v: "缺失" if v is None else f"{v:.2f}"
    for r in result["selected"]:
        m, f = r["metrics"], r["flow"]
        matched = (sector_lookup.get(r["primary_industry_sector_code"]) or {}).get("name") or "—"
        out.append(f"|{r['name']}|{r['ticker']}|{r['industry']}|{m['avg_amount_7d']/100_000:.2f}|{m['total_mv']/10_000:.2f}|{number(f['1']['net_yi'])}|{number(f['3']['net_yi'])}|{number(f['5']['net_yi'])}|{f['5']['positive_net_days']}|{matched}|")
    out += ["", "## 被当前量能上限排除", "", "|股票|代码|7日均额（亿元）|", "|---|---|---:|"]
    for r in sorted(result["excluded_by_cap"], key=lambda r: -r["metrics"]["avg_amount_7d"]):
        out.append(f"|{r['name']}|{r['ticker']}|{r['metrics']['avg_amount_7d']/100_000:.2f}|")
    out += ["", "## 板块趋势与资金交叉核对", "",
            "下表延续当日资金对照的展示口径，仅展示完整成分≤50家的细分方向；全部相关方向在JSON保留，不影响全市场个股入选。净流入与大单净额按非ST且在基础名单内的成分汇总；完整成分数与资金统计成分数分列，任一纳入成分缺字段则对应指标记为缺失。同成分方向合并，股票只计一次。股票主行业按最细的申万归属展示，不用最有利的概念标签替代主营归属。", "",
            "|板块|趋势排名|完整/资金成分|1日净流|3日净流|5日净流|5日大单净额|命中股票数|",
            "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s in result["sector_crosschecks"]:
        if s["full_member_count"] > 50:
            continue
        f = s["flow"]
        out.append(f"|{s['name']}|{s['trend_rank']}|{s['full_member_count']}/{s['fund_member_count']}|{number(f['1']['net_yi'])}|{number(f['3']['net_yi'])}|{number(f['5']['net_yi'])}|{number(f['5']['large_yi'])}|{len(s['selected_tickers'])}|")
    out += ["", "## 数据与边界", "", *[f"- {s}" for s in result["limitations"]],
            f"- 数据不足 {c['data_incomplete']} 只。原因可重叠：`{json.dumps(result['data_error_counts'], ensure_ascii=False)}`。逐股明细见[完整审计JSON](scan.json)。",
            "- 来源：本机连接的Mongo归档 market_daily_bars、daily_basic、moneyflow、stock_basic、trade_calendar、sector_trends_cache。研究读取归档事实；仅显式执行发布任务时更新候选缓存，发布状态见文首。",
            "- 字段单位：[Tushare日线](https://tushare.pro/document/2?doc_id=27)、[每日指标](https://tushare.pro/document/2?doc_id=32)、[个股资金流](https://tushare.pro/document/2?doc_id=170)。净流入与大单+特大单净额分列。", ""]
    return "\n".join(out)


def main() -> None:
    from shilun.api.review import _open_store

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    end = args.date.isoformat()
    if args.date > date.today():
        parser.error("Future analysis date is not allowed")
    start = (args.date - timedelta(days=150)).isoformat()
    store = _open_store()
    try:
        calendar = store.find_trade_calendar(start_date=start, end_date=end)
        days = sorted({iso(r["cal_date"]) for r in calendar if int(r.get("is_open") or 0) == 1})
        if len(days) < 24 or days[-1] != end:
            raise ValueError("Incomplete trading calendar or non-trading analysis date")
        days = days[-24:]
        print(f"Reading {len(days)} sessions through {end}", flush=True)
        basics = store.find_stock_basic()
        bars = list(store.collection("market_daily_bars").find(
            {"date": {"$gte": days[0], "$lte": end}},
            {"_id": 0, "ticker": 1, "date": 1, "close": 1, "amount": 1, "pre_close": 1}))
        daily_basic = store.find_daily_basic(trade_date=end)
        flows = list(store.collection("moneyflow").find(
            {"trade_date": {"$gte": days[-5].replace("-", ""), "$lte": end.replace("-", "")}},
            {"_id": 0, "ts_code": 1, "trade_date": 1, **{f: 1 for f in FLOW_FIELDS}}))
        from shilun.market import SECTOR_ENGINE_VERSION
        cache = store.collection("sector_trends_cache").find_one(
            {"analysis_date": end, "benchmark_ticker": "000001.SH", "trend_lookback_days": 60,
             "engine_version": SECTOR_ENGINE_VERSION}, {"_id": 0})
        if not bars or not daily_basic or not flows:
            raise ValueError("No daily bars, daily basic or moneyflow for the scan window")
        sectors = (cache or {}).get("payload") or {}
        if sectors.get("analysis_date") != end or not sectors.get("all_sectors"):
            raise ValueError("Same-date all-sector archive is required for cross-validation")
    finally:
        store.close()
    print(f"Read {len(bars)} bars, {len(flows)} flow rows; scanning all stocks", flush=True)
    result = scan(basics, bars, daily_basic, days, flows, sectors)
    result["sector_archive"] = {k: cache.get(k) for k in ("analysis_date", "benchmark_ticker", "engine_version", "computed_at")}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "scan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (args.output_dir / "report.md").write_text(markdown_report(result))
    print(json.dumps(result["counts"], ensure_ascii=False), flush=True)
    print(args.output_dir / "report.md", flush=True)


if __name__ == "__main__":
    main()
