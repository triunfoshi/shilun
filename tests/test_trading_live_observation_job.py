from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from shilun.api import _load_cffex_positions_snapshot
from shilun.jobs.trading_live_observation_job import (
    TradingLiveObservationJob,
    build_live_observation_report,
)


TZ = ZoneInfo("Asia/Shanghai")


class _Cursor:
    def __init__(self, docs):
        self.docs = [dict(item) for item in docs]

    def sort(self, key, direction=1):
        self.docs.sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        return iter([dict(item) for item in self.docs])


class _Collection:
    def __init__(self, docs=None):
        self.docs = [dict(item) for item in (docs or [])]

    @staticmethod
    def _match(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                    return False
            elif actual != expected:
                return False
        return True

    def find(self, query=None, projection=None):
        return _Cursor(item for item in self.docs if self._match(item, query or {}))

    def find_one(self, query, projection=None, sort=None):
        docs = [item for item in self.docs if self._match(item, query)]
        if sort:
            key, direction = sort[0]
            docs.sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return dict(docs[0]) if docs else None

    def update_one(self, query, update, upsert=False):
        for item in self.docs:
            if self._match(item, query):
                item.update(update.get("$set", {}))
                return
        if upsert:
            self.docs.append({**query, **update.get("$setOnInsert", {}), **update.get("$set", {})})


class _Store:
    def __init__(self, collections=None):
        self.collections = dict(collections or {})

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())


def _latency(at, ticker="000001.SZ", age=4.0):
    return {
        "ticker": ticker,
        "observed_at": at,
        "quote_age_seconds": age,
        "over_threshold": age > 45,
        "calibration_eligible": True,
    }


def test_job12j_marks_only_a_complete_window_as_observed_and_persists_latest():
    store = _Store({
        "trading_quote_latency_samples": _Collection([
            _latency("2026-07-16T09:31:00+08:00"),
            _latency("2026-07-16T11:30:00+08:00"),
            _latency("2026-07-16T13:02:00+08:00"),
            _latency("2026-07-16T15:00:00+08:00"),
        ]),
        "execution_market_fact_snapshots": _Collection([{
            "snapshot_id": "s1", "trade_date": "20260716", "sector_name": "半导体",
            "computed_at": "2026-07-16T15:00:00+08:00",
        }]),
        "execution_scenario_fact_snapshots": _Collection([{
            "fact_id": "f1", "trade_date": "20260716", "ticker": "000001.SZ",
            "required_data_fresh": True, "computed_at": "2026-07-16T15:00:00+08:00",
        }]),
        "trading_decision_events": _Collection([{
            "event_id": "e1", "ticker": "000001.SZ", "fingerprint": "one",
            "computed_at": "2026-07-16T14:00:00+08:00", "current": {"scenario_id": "E7"},
        }]),
        "trading_notification_outbox": _Collection([{
            "outbox_id": "o1", "event_id": "e1", "status": "sent", "attempts": 1,
            "created_at": "2026-07-16T14:00:01+08:00",
        }]),
        "execution_e7_evaluations": _Collection([{
            "trade_date": "20260716", "ticker": "000001.SZ", "status": "not_triggered",
        }]),
        "account_risk_profiles": _Collection([{"profile_id": "default", "available_cash": 78_000}]),
    })
    now = datetime(2026, 7, 16, 15, 6, tzinfo=TZ)

    report = TradingLiveObservationJob(store).run(now=now, persist=True)

    assert report["status"] == "window_observed"
    assert report["full_window_observed"] is True
    assert report["quality_ready"] is True
    assert report["replay_gate_eligible"] is False
    assert report["quote_latency"]["quote_age_p95_seconds"] == 4.0
    assert report["scenario_facts"]["freshness_blocker_counts"] == {}
    assert report["events"]["semantic_duplicate_count"] == 0
    assert report["feishu"]["sent_duplicate_event_count"] == 0
    assert len(store.collection("trading_live_observation_reports").docs) == 2


def test_job12j_keeps_midday_checkpoint_in_progress_and_detects_semantic_duplicates():
    store = _Store({
        "trading_quote_latency_samples": _Collection([
            _latency("2026-07-16T09:31:00+08:00"),
            _latency("2026-07-16T11:30:00+08:00"),
        ]),
        "trading_decision_events": _Collection([
            {"ticker": "000001.SZ", "fingerprint": "same", "computed_at": "2026-07-16T10:00:00+08:00"},
            {"ticker": "000001.SZ", "fingerprint": "same", "computed_at": "2026-07-16T10:05:00+08:00"},
        ]),
        "account_risk_profiles": _Collection([{"profile_id": "default"}]),
    })

    report = build_live_observation_report(
        store,
        now=datetime(2026, 7, 16, 11, 31, tzinfo=TZ),
    )

    assert report["status"] == "in_progress"
    assert report["gates"]["full_window"]["passed"] is False
    assert report["events"]["semantic_duplicate_count"] == 1
    assert report["gates"]["semantic_stability"]["passed"] is False


def test_job12j_counts_each_scenario_freshness_blocker_source():
    store = _Store({
        "execution_scenario_fact_snapshots": _Collection([
            {
                "fact_id": "f1", "trade_date": "20260716", "ticker": "000001.SZ",
                "required_data_fresh": False,
                "freshness_failed_sources": ["stock_5m", "sector_coverage"],
            },
            {
                "fact_id": "f2", "trade_date": "20260716", "ticker": "000002.SZ",
                "required_data_fresh": False,
                "facts": {"freshness_failed_sources": ["sector_coverage"]},
            },
        ]),
        "account_risk_profiles": _Collection([{"profile_id": "default"}]),
    })

    report = build_live_observation_report(
        store,
        now=datetime(2026, 7, 16, 11, 31, tzinfo=TZ),
    )

    assert report["scenario_facts"]["freshness_blocker_counts"] == {
        "sector_coverage": 2,
        "stock_5m": 1,
    }


def test_cffex_refresh_fills_fixed_seven_session_history():
    dates = ["2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10", "2026-07-13", "2026-07-14", "2026-07-15"]
    store = _Store({
        "market_daily_bars": _Collection([{"ticker": "000001.SH", "date": value} for value in dates]),
        "cffex_positions_daily": _Collection(),
    })

    def fake_fetch(*, target_date):
        return {
            "status": "ok",
            "target_date": target_date,
            "products": [{
                "product": "IF", "contract": "IF2607", "status": "ok", "data_date": target_date,
                "top20_long": 100, "top20_short": 120, "top20_net_short": 20,
                "top20_long_change": 1, "top20_short_change": 2,
                "citic_long": 10, "citic_short": 12,
            }],
            "institutional_summary": {"available": True, "risk_score": 55, "behavior": "主力净空增加"},
        }

    with patch("shilun.data.providers.cffex_positions_provider.fetch_cffex_positions", side_effect=fake_fetch):
        payload = _load_cffex_positions_snapshot(store, "2026-07-15", refresh=True)

    assert payload["history_window"] == 7
    assert payload["history_available_count"] == 7
    assert payload["history_complete"] is True
    assert [item["analysis_date"] for item in payload["history"]] == dates
    assert payload["target_date"] == "2026-07-15"
