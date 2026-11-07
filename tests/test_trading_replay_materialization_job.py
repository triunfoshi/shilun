from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from shilun.jobs.trading_replay_materialization_job import (
    build_replay_materialization_report,
)


TZ = ZoneInfo("Asia/Shanghai")


class _Collection:
    def __init__(self, docs=None):
        self.docs = [deepcopy(item) for item in (docs or [])]

    @staticmethod
    def _match(document, query):
        return all(document.get(key) == expected for key, expected in query.items())

    def find(self, query=None, projection=None):
        return [
            deepcopy(item) for item in self.docs
            if self._match(item, query or {})
        ]

    def update_one(self, query, update, upsert=False):
        for document in self.docs:
            if self._match(document, query):
                document.update(deepcopy(update.get("$set", {})))
                return
        if upsert:
            self.docs.append({
                **deepcopy(query),
                **deepcopy(update.get("$setOnInsert", {})),
                **deepcopy(update.get("$set", {})),
            })


class _Store:
    def __init__(self, collections):
        self.collections = dict(collections)

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())


def _bar(ticker, at, close):
    return {
        "ticker": ticker,
        "trade_date": "20260721",
        "interval": "5",
        "bars": [{
            "datetime": at,
            "open": close - 0.05,
            "high": close + 0.10,
            "low": close - 0.10,
            "close": close,
            "volume": 1_000_000,
            "amount": 100_000_000,
        }],
    }


def _store(*, frozen=True, sources=True):
    at = "2026-07-21T14:05:00+08:00"
    plan = {
        "ticker": "000001.SZ",
        "position_state": "flat",
        "previous_close": 10.0,
        "open_price": 10.0,
        "atr": 0.2,
        "support": 9.6,
        "ma5": 9.8,
        "ma8": 9.7,
        "pressure": 10.8,
        "first_target": 11.5,
        "soft_risk": 9.5,
        "hard_stop": 9.4,
        "max_acceptable_price": 11.0,
        "entry_zone": [10.6, 10.9],
        "current_quantity": 0,
        "sellable_quantity": 0,
        "lot_size": 100,
        "missed_locked": False,
    }
    plan_document = {
        "ticker": "000001.SZ",
        "trade_date": "20260721",
        "playbook_id": "execution_scenarios_v0_3",
        "playbook_version": "0.3.0",
        "plan_id": "plan-1",
        "sector_name": "样例板块",
        "plan": plan,
    }
    if frozen:
        plan_document["frozen_at"] = "2026-07-21T09:31:00+08:00"
    facts = {
        "computed_at": at,
        "current_price": 10.8,
        "day_low": 10.2,
        "vwap": 10.5,
        "closed_bars": [{
            "closed_at": at,
            "open": 10.6,
            "high": 10.9,
            "low": 10.5,
            "close": 10.8,
            "volume_ratio": 1.3,
        }],
        "volume_ratio": 1.3,
        "relative_strength": 0.02,
        "market_permission": "attack",
        "intraday_market_state": "intraday_repair",
        "sector_state": "recovery_resonance",
        "required_data_fresh": True,
        "freshness_failed_sources": [],
        "quote_age_seconds": 4.0,
        "price_limit_locked": False,
        "suspended": False,
        "e7": None,
        "e7_support_deteriorating": False,
        "cancelled_scenarios": [],
        "source_timestamps": ([
            "2026-07-21T14:04:56+08:00",
            "2026-07-21T14:04:00+08:00",
            "2026-07-21T14:04:55+08:00",
            "2026-07-21T14:04:54+08:00",
            "2026-07-21T14:04:50+08:00",
        ] if sources else []),
    }
    return _Store({
        "trading_watch_plans": _Collection([plan_document]),
        "execution_scenario_fact_snapshots": _Collection([{
            "fact_id": "fact-1",
            "ticker": "000001.SZ",
            "trade_date": "20260721",
            "plan_id": "plan-1",
            "facts": facts,
            "computed_at": at,
        }]),
        "minute_bars_daily": _Collection([
            _bar("000001.SZ", at, 10.8),
            _bar("000001.SH", at, 4000),
            _bar("399006.SZ", at, 2500),
        ]),
    })


def test_materializes_and_persists_one_auditable_replay_idempotently():
    store = _store()
    now = datetime(2026, 7, 21, 15, 10, tzinfo=TZ)

    first = build_replay_materialization_report(
        store, trade_date="2026-07-21", persist=True, now=now,
    )
    second = build_replay_materialization_report(
        store, trade_date="20260721", persist=True, now=now,
    )

    assert first["status"] == "success"
    assert first["materialized_replay_count"] == 1
    assert first["audit_eligible_replay_count"] == 1
    assert first["output_digests"] == second["output_digests"]
    replays = store.collection("execution_scenario_replays").docs
    assert len(replays) == 1
    assert replays[0]["audit_eligible"] is True
    assert replays[0]["fact_frame_count"] == 1


def test_old_unfrozen_snapshot_is_materialized_but_not_counted_for_gate():
    store = _store(frozen=False, sources=False)

    report = build_replay_materialization_report(
        store,
        trade_date="20260721",
        persist=True,
        now=datetime(2026, 7, 21, 15, 10, tzinfo=TZ),
    )

    assert report["materialized_replay_count"] == 1
    assert report["audit_eligible_replay_count"] == 0
    replay = store.collection("execution_scenario_replays").docs[0]
    assert replay["audit_eligible"] is False
    assert any("frozen_at" in item for item in replay["audit_exclusion_reasons"])
    assert any("来源时间" in item for item in replay["audit_exclusion_reasons"])
