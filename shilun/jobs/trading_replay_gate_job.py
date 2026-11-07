"""Job 12H：盘点真实历史事实并执行全场景上线闸门。"""

from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
from typing import Any
from zoneinfo import ZoneInfo

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.trading.scenario_replay import evaluate_full_scenario_launch_gate


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
REPLAY_COLLECTION = "execution_scenario_replays"
REPORT_COLLECTION = "trading_replay_gate_reports"
INDEX_TICKERS = frozenset({"000001.SH", "399006.SZ"})


def _documents(collection: Any, query: dict[str, Any] | None = None, projection: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        cursor = collection.find(query or {}, projection)
    except TypeError:
        cursor = collection.find(query or {})
    return [dict(item) for item in cursor]


def _date_text(value: Any) -> str:
    text = str(value or "").strip().replace("-", "")
    return text[:8] if len(text) >= 8 else text


def _event_scenario_id(event: dict[str, Any]) -> str:
    direct = event.get("scenario_id")
    if direct:
        return str(direct).upper()
    for key in ("current", "scenario"):
        nested = event.get(key) or {}
        value = nested.get("scenario_id") or nested.get("id") or nested.get("code")
        if value:
            return str(value).upper()
    return ""


def collect_replay_inventory(store: Any) -> dict[str, Any]:
    minute_docs = _documents(
        store.collection("minute_bars_daily"),
        {},
        {"_id": 0, "ticker": 1, "trade_date": 1, "interval": 1, "bars": 1},
    )
    minute_sessions = {
        (str(item.get("ticker") or "").upper(), _date_text(item.get("trade_date")))
        for item in minute_docs
        if item.get("ticker") and item.get("trade_date")
    }
    index_dates: dict[str, set[str]] = {
        ticker: {date for observed_ticker, date in minute_sessions if observed_ticker == ticker}
        for ticker in INDEX_TICKERS
    }
    paired_index_dates = set.intersection(*index_dates.values()) if index_dates else set()
    stock_sessions = {
        (ticker, date)
        for ticker, date in minute_sessions
        if ticker not in INDEX_TICKERS
    }
    paired_stock_sessions = {
        (ticker, date) for ticker, date in stock_sessions if date in paired_index_dates
    }
    fact_docs = _documents(
        store.collection("execution_market_facts"),
        {},
        {"_id": 0, "trade_date": 1, "sector_name": 1, "computed_at": 1},
    )
    fact_dates = {_date_text(item.get("trade_date") or item.get("computed_at")) for item in fact_docs}
    plan_docs = _documents(
        store.collection("trading_watch_plans"),
        {},
        {"_id": 0, "ticker": 1, "trade_date": 1, "playbook_id": 1, "playbook_version": 1},
    )
    full_plans = [
        item for item in plan_docs
        if str(item.get("playbook_id") or "") == "execution_scenarios_v0_3"
    ]
    plan_sessions = {
        (str(item.get("ticker") or "").upper(), _date_text(item.get("trade_date")))
        for item in full_plans
    }
    full_fact_sessions = {
        session for session in paired_stock_sessions
        if session in plan_sessions and session[1] in fact_dates
    }
    event_docs = _documents(
        store.collection("trading_decision_events"),
        {},
        {"_id": 0, "ticker": 1, "computed_at": 1, "scenario_id": 1, "current": 1, "scenario": 1},
    )
    scenario_event_counts: dict[str, int] = {}
    for event in event_docs:
        code = _event_scenario_id(event)
        if code:
            scenario_event_counts[code] = scenario_event_counts.get(code, 0) + 1
    try:
        replay_docs = _documents(store.collection(REPLAY_COLLECTION), {}, {"_id": 0})
    except Exception:
        replay_docs = []
    missing: list[str] = []
    if not paired_stock_sessions:
        missing.append("缺少个股与上证、创业板同日 5 分钟 K")
    if not full_plans:
        missing.append("缺少 execution_scenarios_v0_3 历史冻结计划")
    if not fact_docs:
        missing.append("缺少 execution_market_facts 历史板块事实")
    if not replay_docs:
        missing.append("尚无通过完整事实组装器生成的全场景回放会话")
    return {
        "minute_bar_document_count": len(minute_docs),
        "minute_trade_dates": sorted({date for _, date in minute_sessions}),
        "stock_session_count": len(stock_sessions),
        "paired_index_dates": sorted(paired_index_dates),
        "paired_stock_session_count": len(paired_stock_sessions),
        "historical_sector_fact_count": len(fact_docs),
        "historical_sector_fact_dates": sorted(value for value in fact_dates if value),
        "full_scenario_plan_count": len(full_plans),
        "full_fact_session_count": len(full_fact_sessions),
        "stored_replay_count": len(replay_docs),
        "scenario_event_counts": dict(sorted(scenario_event_counts.items())),
        "missing_requirements": missing,
        "replays": replay_docs,
    }


class TradingReplayGateJob:
    def __init__(self, store: Any | None = None) -> None:
        config = load_config()
        if store is None:
            if not config.mongo_uri:
                raise ValueError("SHILUN_MONGO_URI is required for Job 12H.")
            store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
        self.store = store
        self._owns_store = isinstance(store, MongoSnapshotStore)

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def run(
        self,
        *,
        min_sessions_per_scenario: int = 20,
        persist: bool = False,
    ) -> dict[str, Any]:
        inventory = collect_replay_inventory(self.store)
        replays = inventory.pop("replays")
        gate = evaluate_full_scenario_launch_gate(
            replays,
            min_sessions_per_scenario=min_sessions_per_scenario,
        )
        for requirement in inventory["missing_requirements"]:
            if requirement not in gate["blockers"]:
                gate["blockers"].append(requirement)
        if inventory["missing_requirements"]:
            gate["ready_for_launch"] = False
            gate["decision"] = "hold_launch"
        fingerprint_payload = {
            "inventory": inventory,
            "gate": {
                "minimum_sessions_per_scenario": min_sessions_per_scenario,
                "scenario_session_counts": gate["scenario_session_counts"],
                "coverage": gate["coverage"],
                "gates": gate["gates"],
            },
        }
        fingerprint = sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        report = {
            **gate,
            "report_id": f"job12h:{fingerprint[:24]}",
            "inventory_fingerprint": fingerprint,
            "generated_at": datetime.now(MARKET_TIMEZONE).isoformat(timespec="seconds"),
            "inventory": inventory,
            "persisted": False,
        }
        if persist:
            report["persisted"] = True
            self.store.collection(REPORT_COLLECTION).update_one(
                {"report_id": report["report_id"]},
                {"$set": report},
                upsert=True,
            )
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Job 12H historical replay launch gate.")
    parser.add_argument("--min-sessions", type=int, default=20)
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args(argv)
    job = TradingReplayGateJob()
    try:
        report = job.run(min_sessions_per_scenario=args.min_sessions, persist=args.persist)
    finally:
        job.close()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ready_for_launch"] else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
