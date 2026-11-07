"""Refresh MA5 selection from dated facts, preserving existing sector calculations.

The --apply flag publishes only the versioned candidate cache; it places no orders.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from time import monotonic

import pandas as pd

from shilun.jobs.sector_precompute_job import _store as _open_store
from shilun.market import SECTOR_ENGINE_VERSION
from shilun.market.ma5_universe import build_full_market_candidates, scan_universe, iso


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    end = args.date.isoformat()
    if args.date > date.today():
        parser.error("Future dates are not allowed")
    started = monotonic()
    store = _open_store()
    try:
        if args.apply:
            from shilun.jobs.sector_precompute_job import start_task
            task = start_task(store, end, '000001.SH')
            args.output_dir.mkdir(parents=True, exist_ok=True)
            (args.output_dir / 'task.json').write_text(json.dumps(task, ensure_ascii=False, indent=2, default=str) + '\n')
            print(json.dumps({'applied': False, 'execution': 'ecs_queue', 'task': task}, ensure_ascii=False, default=str), flush=True)
            return
        from shilun.market.sector_service import _load_market_gate, _load_breakout_events_lookup, _load_minute_bars_from_mongo
        calendar = store.find_trade_calendar(start_date=(args.date - timedelta(days=150)).isoformat(), end_date=end)
        days = sorted({iso(r["cal_date"]) for r in calendar if int(r.get("is_open") or 0) == 1})[-24:]
        if len(days) < 24 or days[-1] != end:
            raise ValueError("Incomplete 24-session calendar")
        cache = store.collection("sector_trends_cache").find_one(
            {"analysis_date": end, "benchmark_ticker": "000001.SH", "trend_lookback_days": 60},
            {"_id": 0}, sort=[("computed_at", -1)])
        payload = (cache or {}).get("payload") or {}
        if payload.get("analysis_date") != end or not payload.get("all_sectors"):
            raise ValueError("Same-date sector archive is required")
        from shilun.market.taxonomy import classify_stocks, VERSION as TAXONOMY_VERSION
        if payload.get("membership_version") != TAXONOMY_VERSION:
            raise ValueError("先重算东方财富统一分类的板块归档")
        basics = classify_stocks(store, end, store.find_stock_basic())
        bars = store.find_daily_bars(start_date=days[0], end_date=end)
        daily_basic = store.find_daily_basic(trade_date=end)
        preliminary = scan_universe(basics, bars, daily_basic, days)
        liquidity = preliminary["liquidity"]
        if not liquidity["available"]:
            raise ValueError("Market turnover coverage incomplete; no cache was published")
        print(json.dumps({"liquidity": liquidity, "selected": preliminary["selection_scope"]["selected_count"]}, ensure_ascii=False), flush=True)
        tickers = [r["ticker"] for r in preliminary["audits"] if r["original_conditions_passed"]]
        if tickers:
            bars.extend(store.find_daily_bars(
                start_date=(args.date - timedelta(days=260)).isoformat(),
                end_date=(date.fromisoformat(days[0]) - timedelta(days=1)).isoformat(), tickers=tickers))
        flows = store.find_moneyflow(start_date=days[-5], end_date=end)
        gate = _load_market_gate(store, end, "000001.SH")
        events = _load_breakout_events_lookup(store, end)
        minute_lookup, benchmark_minutes = _load_minute_bars_from_mongo(store, end, "000001.SH")
        selection = build_full_market_candidates(
            market_bars=pd.DataFrame(bars), stock_basic=pd.DataFrame(basics), daily_basic=pd.DataFrame(daily_basic),
            moneyflow=pd.DataFrame(flows), days=days, sectors=payload["all_sectors"], market_gate=gate,
            breakout_events_lookup=events, stock_minute_bars_lookup=minute_lookup,
            benchmark_minute_bars=benchmark_minutes, require_minute_data=True)
        from research.scan_ma5_market import scan, markdown_report
        research = scan(basics, bars, daily_basic, days, flows, payload)
        research["publication"] = {"applied": args.apply, "rule_version": selection["selection_rule_version"],
                                   "engine_version": SECTOR_ENGINE_VERSION}
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "scan.json").write_text(json.dumps(research, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        (args.output_dir / "report.md").write_text(markdown_report(research))
        updated = {**payload, **selection, "engine_version": SECTOR_ENGINE_VERSION,
                   "sector_calculation_engine_version": payload.get("sector_calculation_engine_version") or cache.get("engine_version"),
                   "selection_refreshed_at": datetime.now().isoformat(timespec="seconds")}
        (args.output_dir / "candidates.json").write_text(json.dumps(selection, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        print(json.dumps({"applied": args.apply, "counts": research["counts"],
                          "rule": selection["selection_rule_version"], "report": str(args.output_dir / "report.md")}, ensure_ascii=False), flush=True)
    finally:
        store.close()


if __name__ == "__main__":
    main()
