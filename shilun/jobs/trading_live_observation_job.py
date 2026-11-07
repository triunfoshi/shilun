"""Job 12J：真实交易窗口观测与样本积累报告。

本 Job 只读取已经落库的盘中事实，不请求行情、不补造事件，也不改变交易
阈值。完整窗口、事件稳定性、工作台密度和飞书重复率分别计算；盘中提前
运行只会得到 ``in_progress``，不会把半日样本标成验收通过。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, time
from hashlib import sha256
import json
from typing import Any
from zoneinfo import ZoneInfo

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.trading.playbooks.execution_scenarios import SCENARIO_CODES
from shilun.trading.workbench import ExecutionWorkbenchService


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
REPORT_COLLECTION = "trading_live_observation_reports"


def _documents(collection: Any) -> list[dict[str, Any]]:
    try:
        return [dict(item) for item in collection.find({}, {"_id": 0})]
    except TypeError:
        return [dict(item) for item in collection.find({})]
    except Exception:  # noqa: BLE001 - missing optional collection is an explicit zero fact
        return []


def _aware(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed.astimezone(MARKET_TIMEZONE)


def _trade_date(value: Any) -> str:
    text = str(value or "").strip().replace("-", "")
    return text[:8] if len(text) >= 8 else text


def _same_session(value: Any, trade_date: str) -> bool:
    parsed = _aware(value)
    return bool(parsed and parsed.strftime("%Y%m%d") == trade_date)


def _percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * ratio)))
    return round(ordered[index], 3)


def _within(value: datetime, start: time, end: time) -> bool:
    observed = value.time().replace(tzinfo=None)
    return start <= observed <= end


def _scenario_id(event: dict[str, Any]) -> str:
    current = event.get("current") or {}
    scenario = event.get("scenario") or {}
    decision = event.get("decision") or {}
    candidate = decision.get("current_scenario") or {}
    value = (
        event.get("scenario_id")
        or current.get("scenario_id")
        or scenario.get("scenario_id")
        or candidate.get("scenario_id")
    )
    return str(value or "").upper()


def _duplicate_count(values: list[str]) -> int:
    counts = Counter(value for value in values if value)
    return sum(max(0, count - 1) for count in counts.values())


def build_live_observation_report(
    store: Any,
    *,
    now: datetime | None = None,
    trade_date: str | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(MARKET_TIMEZONE)).astimezone(MARKET_TIMEZONE)
    target = _trade_date(trade_date or current.strftime("%Y%m%d"))

    latency = [
        item for item in _documents(store.collection("trading_quote_latency_samples"))
        if _same_session(item.get("observed_at"), target)
        and item.get("calibration_eligible", True)
    ]
    observed_times = sorted(
        parsed for item in latency
        if (parsed := _aware(item.get("observed_at"))) is not None
    )
    minute_buckets = {item.strftime("%Y-%m-%dT%H:%M") for item in observed_times}
    checkpoints = {
        "morning_open": any(_within(item, time(9, 30), time(9, 35)) for item in observed_times),
        "morning_close": any(_within(item, time(11, 25), time(11, 35)) for item in observed_times),
        "afternoon_open": any(_within(item, time(13, 0), time(13, 5)) for item in observed_times),
        "market_close": any(_within(item, time(14, 55), time(15, 5)) for item in observed_times),
    }
    full_window = all(checkpoints.values())
    ages = [float(item.get("quote_age_seconds") or 0) for item in latency]
    over_threshold = sum(bool(item.get("over_threshold")) for item in latency)

    sector_snapshots = [
        item for item in _documents(store.collection("execution_market_fact_snapshots"))
        if _trade_date(item.get("trade_date") or item.get("computed_at")) == target
    ]
    sector_times = sorted(
        parsed for item in sector_snapshots
        if (parsed := _aware(item.get("computed_at"))) is not None
    )
    sector_minutes = {item.strftime("%Y-%m-%dT%H:%M") for item in sector_times}

    scenario_facts = [
        item for item in _documents(store.collection("execution_scenario_fact_snapshots"))
        if _trade_date(item.get("trade_date") or item.get("computed_at")) == target
    ]
    fresh_fact_count = sum(bool(item.get("required_data_fresh")) for item in scenario_facts)
    freshness_blockers = Counter(
        str(source)
        for item in scenario_facts
        for source in (
            item.get("freshness_failed_sources")
            or (item.get("facts") or {}).get("freshness_failed_sources")
            or []
        )
        if source
    )

    events = [
        item for item in _documents(store.collection("trading_decision_events"))
        if _same_session(item.get("computed_at"), target)
    ]
    events.sort(key=lambda item: (str(item.get("ticker") or ""), str(item.get("computed_at") or "")))
    previous_fingerprint: dict[str, str] = {}
    semantic_duplicates = 0
    scenario_sessions: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for event in events:
        ticker = str(event.get("ticker") or "").upper()
        fingerprint = str(event.get("fingerprint") or "")
        if ticker and fingerprint and previous_fingerprint.get(ticker) == fingerprint:
            semantic_duplicates += 1
        if ticker and fingerprint:
            previous_fingerprint[ticker] = fingerprint
        scenario = _scenario_id(event)
        if scenario:
            scenario_sessions[scenario].add((ticker, target))

    outbox = [
        item for item in _documents(store.collection("trading_notification_outbox"))
        if _same_session(item.get("created_at") or item.get("sent_at"), target)
    ]
    outbox_duplicates = _duplicate_count([str(item.get("event_id") or "") for item in outbox])
    sent = [item for item in outbox if item.get("status") == "sent"]
    sent_duplicates = _duplicate_count([str(item.get("event_id") or "") for item in sent])

    e7_rows = [
        item for item in _documents(store.collection("execution_e7_evaluations"))
        if _trade_date(item.get("trade_date") or item.get("computed_at")) == target
    ]
    e7_status = Counter(str(item.get("status") or "unknown") for item in e7_rows)

    try:
        workbench_summary = ExecutionWorkbenchService(
            store,
            now_factory=lambda: current,
        ).build(limit=30).get("summary") or {}
    except Exception as error:  # noqa: BLE001 - observation survives optional projection failures
        workbench_summary = {"status": "unavailable", "error": str(error)[:300]}

    p95 = _percentile(ages, 0.95)
    latency_gate = bool(latency) and p95 is not None and p95 <= 45 and over_threshold / len(latency) <= 0.05
    density_gate = (
        workbench_summary.get("primary_count") is not None
        and int(workbench_summary.get("primary_count") or 0) <= 1
        and int(workbench_summary.get("alternative_count") or 0) <= 2
    )
    gates = {
        "full_window": {"passed": full_window, "detail": checkpoints},
        "quote_freshness": {
            "passed": latency_gate,
            "detail": "报价年龄 P95 ≤45秒，超阈值比例 ≤5%",
        },
        "semantic_stability": {"passed": semantic_duplicates == 0, "detail": f"连续同语义重复 {semantic_duplicates} 条"},
        "workbench_density": {"passed": density_gate, "detail": workbench_summary},
        "feishu_deduplication": {
            "passed": outbox_duplicates == 0 and sent_duplicates == 0,
            "detail": f"Outbox重复 {outbox_duplicates} 条，已发送重复 {sent_duplicates} 条",
        },
    }
    session_counts = {code: len(scenario_sessions.get(code, set())) for code in SCENARIO_CODES}
    replay_gate_eligible = all(count >= 20 for count in session_counts.values())
    quality_ready = all(item["passed"] for item in gates.values())
    status = "window_observed" if quality_ready else ("in_progress" if not full_window else "needs_attention")
    blockers = [name for name, item in gates.items() if not item["passed"]]
    if not replay_gate_eligible:
        blockers.append("每场景20个独立会话尚未满足")

    report_core = {
        "job": "12J",
        "trade_date": target,
        "status": status,
        "full_window_observed": full_window,
        "quality_ready": quality_ready,
        "replay_gate_eligible": replay_gate_eligible,
        "gates": gates,
        "quote_latency": {
            "sample_count": len(latency),
            "ticker_count": len({str(item.get("ticker") or "") for item in latency if item.get("ticker")}),
            "minute_bucket_count": len(minute_buckets),
            "first_observed_at": observed_times[0].isoformat(timespec="seconds") if observed_times else None,
            "last_observed_at": observed_times[-1].isoformat(timespec="seconds") if observed_times else None,
            "quote_age_p50_seconds": _percentile(ages, 0.5),
            "quote_age_p95_seconds": p95,
            "over_threshold_count": over_threshold,
            "over_threshold_rate": round(over_threshold / len(latency), 4) if latency else None,
        },
        "sector_facts": {
            "snapshot_count": len(sector_snapshots),
            "sector_count": len({str(item.get("sector_name") or "") for item in sector_snapshots if item.get("sector_name")}),
            "minute_bucket_count": len(sector_minutes),
            "first_computed_at": sector_times[0].isoformat(timespec="seconds") if sector_times else None,
            "last_computed_at": sector_times[-1].isoformat(timespec="seconds") if sector_times else None,
        },
        "scenario_facts": {
            "fact_count": len(scenario_facts),
            "ticker_count": len({str(item.get("ticker") or "") for item in scenario_facts if item.get("ticker")}),
            "fresh_count": fresh_fact_count,
            "fresh_rate": round(fresh_fact_count / len(scenario_facts), 4) if scenario_facts else None,
            "freshness_blocker_counts": dict(sorted(freshness_blockers.items())),
        },
        "events": {
            "event_count": len(events),
            "ticker_count": len({str(item.get("ticker") or "") for item in events if item.get("ticker")}),
            "semantic_duplicate_count": semantic_duplicates,
            "scenario_session_counts": session_counts,
        },
        "workbench": workbench_summary,
        "e7": {"evaluation_count": len(e7_rows), "status_counts": dict(sorted(e7_status.items()))},
        "feishu": {
            "outbox_count": len(outbox),
            "sent_count": len(sent),
            "pending_count": sum(item.get("status") == "pending" for item in outbox),
            "failed_count": sum(item.get("status") in {"failed", "dead"} for item in outbox),
            "retried_count": sum(int(item.get("attempts") or 0) > 1 for item in outbox),
            "duplicate_event_count": outbox_duplicates,
            "sent_duplicate_event_count": sent_duplicates,
        },
        "blockers": blockers,
    }
    fingerprint = sha256(
        json.dumps(report_core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **report_core,
        "report_id": f"job12j:{target}:{fingerprint[:16]}",
        "generated_at": current.isoformat(timespec="seconds"),
    }


class TradingLiveObservationJob:
    def __init__(self, store: Any | None = None) -> None:
        config = load_config()
        if store is None:
            if not config.mongo_uri:
                raise ValueError("SHILUN_MONGO_URI is required for Job 12J.")
            store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
        self.store = store
        self._owns_store = isinstance(store, MongoSnapshotStore)

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def run(
        self,
        *,
        now: datetime | None = None,
        trade_date: str | None = None,
        persist: bool = False,
    ) -> dict[str, Any]:
        report = build_live_observation_report(self.store, now=now, trade_date=trade_date)
        report["persisted"] = bool(persist)
        if persist:
            self.store.collection(REPORT_COLLECTION).update_one(
                {"report_id": report["report_id"]},
                {"$set": report},
                upsert=True,
            )
            self.store.collection(REPORT_COLLECTION).update_one(
                {"report_id": f"job12j:{report['trade_date']}:latest"},
                {"$set": {**report, "report_id": f"job12j:{report['trade_date']}:latest"}},
                upsert=True,
            )
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Job 12J live trading-window observation.")
    parser.add_argument("--date", dest="trade_date")
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args(argv)
    job = TradingLiveObservationJob()
    try:
        report = job.run(trade_date=args.trade_date, persist=args.persist)
    finally:
        job.close()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["status"] == "window_observed" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["REPORT_COLLECTION", "TradingLiveObservationJob", "build_live_observation_report"]
