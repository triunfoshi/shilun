from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from shilun.data.cffex_positions import load_snapshot, store_snapshot
from shilun.jobs.cffex_positions_sync_job import run
from shilun.jobs.cloud_scheduler import build_scheduler, scheduler_manifest
from shilun.jobs.scheduler_manifest_job import publish


DAY = "2026-11-02"
TZ = ZoneInfo("Asia/Shanghai")


class Collection:
    def __init__(self):
        self.rows = []

    def find_one(self, query):
        def matches(row):
            for key, value in query.items():
                actual = row
                for part in key.split("."):
                    actual = actual.get(part) if isinstance(actual, dict) else None
                if isinstance(value, dict) and "$ne" in value:
                    if actual == value["$ne"]:
                        return False
                elif actual != value:
                    return False
            return True
        return next((row for row in self.rows if matches(row)), None)

    def update_one(self, query, update, upsert=False):
        row = self.find_one(query)
        if row is not None:
            row.update(deepcopy(update["$set"]))
            return SimpleNamespace(matched_count=1, upserted_id=None)
        if any(row["analysis_date"] == query["analysis_date"] for row in self.rows):
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError("same day")
        self.rows.append(deepcopy(update["$set"]))
        return SimpleNamespace(matched_count=0, upserted_id="new")


class Store:
    def __init__(self, calendar=None):
        self.snapshots = Collection()
        self.states = []
        self.calendar = calendar or []

    def collection(self, name):
        assert name == "cffex_positions_daily"
        return self.snapshots

    def upsert_sync_state(self, state):
        self.states.append(deepcopy(state))

    def find_trade_calendar(self, **kwargs):
        return self.calendar


def complete(day=DAY):
    return {"target_date": day, "status": "ok", "products": [
        {"product": product, "contract": product + "2611", "data_date": day, "status": "ok",
         "source_consistency": "matched", "source_mismatch_fields": [],
         "ths_consistency": "matched" if product != "IM" else "not_supported_for_im",
         "top20_long": 100, "top20_short": 110, "top20_long_change": 1, "top20_short_change": 2,
         "citic_long": 20, "citic_short": 25, "citic_long_change": -2, "citic_short_change": 3}
        for product in ("IF", "IH", "IC", "IM")],
        "institutional_summary": {"available": True, "risk_score": 55}}


def test_complete_day_is_idempotent_and_receipt_is_dated():
    store = Store()
    for _ in range(2):
        result = run(store, DAY, fetcher=lambda **kw: complete())
        assert result["status"] == "success"
        assert result["successful_count"] == 4
        assert result["scope"] == DAY
    assert len(store.snapshots.rows) == 1
    assert store.snapshots.rows[0]["analysis_date"] == DAY
    assert store.states[-1]["calendar_source"] == "calendar_unknown"


@pytest.mark.parametrize("change,reason", [
    (lambda p: p["products"][0].update(data_date="2026-10-30"), "wrong_data_date"),
    (lambda p: p["products"][0].update(source_consistency="mismatch"), "seat_crosscheck_failed"),
    (lambda p: p["products"][0].update(ths_consistency="mismatch"), "third_source_mismatch"),
    (lambda p: p["products"][0].update(citic_short=None), "missing_position_fields"),
    (lambda p: p["products"][0].update(citic_short=float("nan")), "missing_position_fields"),
    (lambda p: p["products"][0].update(top20_long=float("inf")), "missing_position_fields"),
    (lambda p: p["products"][0].update(contract="IH2611"), "invalid_contract"),
    (lambda p: p["products"].append(deepcopy(p["products"][0])), "missing_or_duplicate_product"),
])
def test_source_date_and_four_product_audits(change, reason):
    store, payload = Store(), complete()
    change(payload)
    result = run(store, DAY, fetcher=lambda **kw: payload)
    assert result["status"] == "partial"
    assert result["failed_products"]["IF"] == reason
    saved = store.snapshots.rows[0]["payload"]
    assert saved["status"] == "partial"
    assert saved["institutional_summary"]["available"] is False


def test_missing_product_is_not_four_of_four():
    store, payload = Store(), complete()
    payload["products"].pop()
    result = run(store, DAY, fetcher=lambda **kw: payload)
    assert result["status"] == "partial"
    assert result["successful_count"] == 3
    assert result["failed_products"] == {"IM": "missing_or_duplicate_product"}


def test_partial_retry_keeps_complete_snapshot_but_reports_failed_attempt():
    store = Store()
    run(store, DAY, fetcher=lambda **kw: complete())
    original = deepcopy(store.snapshots.rows[0])
    result = run(store, DAY, fetcher=lambda **kw: complete("2026-10-30"))
    assert result["status"] == "partial"
    assert result["successful_count"] == 0
    assert result["preserved_complete_snapshot"] is True
    assert store.snapshots.rows == [original]
    assert not store_snapshot(store, DAY, {"status": "partial", "products": []})
    assert store.snapshots.rows == [original]


def test_atomic_partial_guard_handles_concurrent_complete_insert():
    store = Store()
    original_find = store.snapshots.find_one

    def find(query):
        if query == {"analysis_date": DAY}:
            # Race: the first read saw no row; a complete manual refresh wins
            # before the partial conditional upsert reaches Mongo.
            store.snapshots.rows.append({"analysis_date": DAY, "payload": complete()})
            return None
        return original_find(query)
    store.snapshots.find_one = find
    assert not store_snapshot(store, DAY, {"status": "partial", "products": []})
    assert store.snapshots.rows[0]["payload"]["status"] == "ok"


def test_manual_refresh_keeps_audited_cache_when_upstream_ok_has_crosscheck_mismatch():
    from tests.test_trading_live_observation_job import _Collection, _Store
    store = _Store({"cffex_positions_daily": _Collection([
        {"analysis_date": DAY, "payload": complete(), "updated_at": "2026-11-02T17:45:00+08:00"},
    ])})
    bad = complete()
    bad["products"][0]["source_consistency"] = "mismatch"
    with patch("shilun.data.providers.cffex_positions_provider.fetch_cffex_positions", return_value=bad):
        response = load_snapshot(store, DAY, refresh=True)
    assert response["status"] == "ok"
    assert response["_from_cache"] is True
    assert DAY in response["refresh_errors"]
    assert response["products"][0]["source_consistency"] == "matched"
    assert store.collection("cffex_positions_daily").docs[0]["payload"] == complete()


def test_closed_session_does_not_request_or_save_old_source_data():
    store = Store(calendar=[{"cal_date": "20261102", "is_open": 0}])
    def fetch(**kw):
        pytest.fail("closed session should not request supplier")
    result = run(store, DAY, fetcher=fetch)
    assert result["status"] == "skipped_closed"
    assert store.snapshots.rows == []
    assert result["successful_count"] == 0


def test_fetch_failure_has_safe_receipt_and_does_not_erase_snapshot():
    store = Store()
    run(store, DAY, fetcher=lambda **kw: complete())
    def fetch(**kw):
        raise TimeoutError("a sensitive provider URL must not enter receipt")
    result = run(store, DAY, fetcher=fetch)
    assert result["status"] == "failed"
    assert result["error_type"] == "TimeoutError"
    assert "sensitive" not in str(result)
    assert store.snapshots.rows[0]["payload"]["status"] == "ok"


def test_data_only_registers_independent_futures_and_live_manifest_without_io():
    with patch("shilun.jobs.cloud_scheduler._run") as launch:
        scheduler = build_scheduler(data_only=True, location="ECS")
        assert not launch.called
        manifest = scheduler_manifest(scheduler, data_only=True, location="ECS", now=datetime(2026, 11, 2, 16, tzinfo=TZ))
        by_id = {row["id"]: row for row in manifest["jobs"]}
        assert len(by_id) == 15
        for hour in (17, 19, 23):
            row = by_id[f"cffex_positions_{hour}40"]
            assert row["dataset_keys"] == ["cffex_positions"]
            assert row["hour"] == str(hour) and row["minute"] == "40"
            assert row["day_of_week"] == "mon-fri"
            assert row["executor"] == "evidence"
        assert by_id["scheduler_manifest_heartbeat"]["executor"] == "maintenance"
        assert manifest["location"] == "ECS"
        assert len(manifest["release_fingerprint"]) == 64
        scheduler.get_job("cffex_positions_1740").func()
        assert launch.call_args.args[:2] == ("cffex_positions_1740", "shilun.jobs.cffex_positions_sync_job")
        scheduler.get_job("scheduler_manifest_heartbeat").func()
        assert launch.call_args.args[0] == "scheduler_manifest"


def test_manifest_publisher_uses_only_safe_metadata_and_closes_client():
    client, collection = MagicMock(), MagicMock()
    client.__getitem__.return_value = {"sync_state": collection}
    config = SimpleNamespace(mongo_uri="mongodb://unit-test.invalid", mongo_db="test_only")
    with patch("shilun.common.config.load_config", return_value=config):
        result = publish({"jobs": [{"id": "cffex_positions_1740"}], "location": "ECS",
                          "mongo_uri": "must_not_be_published", "status": "pretend"},
                         client_factory=lambda *a, **kw: client)
    assert "must_not_be_published" not in str(result)
    assert result["scope"] == "active" and result["status"] == "running"
    assert result["updated_at"].endswith("+00:00")
    assert collection.update_one.call_args.args[0] == {"dataset": "cloud_scheduler_manifest", "scope": "active"}
    client.close.assert_called_once()
