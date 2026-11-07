from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bson import BSON
import pandas as pd
import pytest

from shilun.jobs.heavy_job_lease import LeaseLost
from shilun.market import ma5_snapshot_store as snapshots
from shilun.market import ma5_universe

DAY = "2026-11-02"


class Collection:
    def __init__(self):
        self.rows = {}
        self.writes = []
        self.fail = lambda doc: False

    def find_one(self, query):
        row = self.rows.get(query["_id"])
        if row is None or any(row.get(key) != value for key, value in query.items()):
            return None
        return deepcopy(row)

    def update_one(self, query, update, upsert=False):
        values = update.get("$setOnInsert", update.get("$set", {}))
        if self.fail(values):
            raise OSError("simulated cache outage")
        key = query["_id"]
        inserted = key not in self.rows
        if inserted:
            self.rows[key] = {"_id": key}
        if "$set" in update or inserted:
            self.rows[key].update(deepcopy(values))
        self.writes.append(deepcopy(self.rows[key]))
        return SimpleNamespace(upserted_id=key if inserted else None)


@pytest.fixture
def store():
    collection = Collection()
    return SimpleNamespace(collection=lambda name: collection, data=collection)


def _entry(signature="a" * 64, *, count=3, text=""):
    return {"signature": signature, "value": {
        "audits": [{"ticker": f"600{i:03d}.SH", "passed": i == 0,
                    "data_complete": True, "metrics": {"amount": float(i)},
                    "evidence": text} for i in range(count)],
        "liquidity": {"available": True, "market_amount_cny": 2.2e12},
        "selection_scope": {"kind": "full_market", "independent_from_sector_ranking": True,
                            "rule_version": snapshots.MA5_SELECTION_RULE_VERSION,
                            "universe_count": count, "data_complete_count": count,
                            "selected_count": min(count, 1), "window_dates": [DAY]},
    }}


def _save(store, entry):
    return snapshots.save_scan_cache(store, DAY, entry, lambda: None)


def test_round_trip_preserves_all_audits_and_promotes_head_last(store):
    entry = _entry()
    metadata = _save(store, entry)
    assert metadata["status"] == "ready"
    assert metadata["signature"] == entry["signature"]
    assert metadata["audit_count"] == 3
    assert snapshots.load_scan_cache(store, DAY) == entry
    assert [row["type"] for row in store.data.writes] == ["chunk", "manifest", "head"]
    loaded = snapshots.load_scan_cache(store, DAY)
    loaded["value"]["audits"][0]["passed"] = False
    assert snapshots.load_scan_cache(store, DAY) == entry


def test_snapshot_larger_than_mongo_limit_uses_bounded_bson_chunks(store):
    entry = _entry(count=70, text="x" * 250_000)
    assert len(BSON.encode(entry)) > 16 * 1024 * 1024
    metadata = _save(store, entry)
    assert metadata["status"] == "ready"
    assert metadata["chunk_count"] > 1
    assert all(len(BSON.encode(row)) <= snapshots.MAX_DOCUMENT_BYTES for row in store.data.rows.values())
    assert snapshots.load_scan_cache(store, DAY) == entry


@pytest.mark.parametrize("corruption", ["missing", "audit", "hash", "count", "rule", "date", "pending"])
def test_missing_or_damaged_snapshot_falls_back_to_normal_scan(store, corruption):
    _save(store, _entry())
    chunk = next(row for row in store.data.rows.values() if row["type"] == "chunk")
    manifest = next(row for row in store.data.rows.values() if row["type"] == "manifest")
    if corruption == "missing":
        del store.data.rows[chunk["_id"]]
    elif corruption == "audit":
        chunk["audits"][0]["passed"] = False
    elif corruption == "hash":
        chunk["value_hash"] = "broken"
    elif corruption == "count":
        manifest["audit_count"] += 1
    elif corruption == "rule":
        manifest["rule_version"] = "older-rule"
    elif corruption == "date":
        manifest["analysis_date"] = "2026-10-30"
    else:
        manifest["status"] = "writing"
    assert snapshots.load_scan_cache(store, DAY) is None


def test_immutable_signature_never_overwrites_different_results(store):
    entry = _entry()
    _save(store, entry)
    changed = deepcopy(entry)
    changed["value"]["audits"][0]["passed"] = False
    before = deepcopy(store.data.rows)
    metadata = _save(store, changed)
    assert metadata["status"] == "skipped"
    assert store.data.rows == before
    assert snapshots.load_scan_cache(store, DAY) == entry


def test_same_immutable_snapshot_can_be_republished_without_changing_its_documents(store):
    entry = _entry()
    metadata = _save(store, entry)
    immutable = {key: deepcopy(row) for key, row in store.data.rows.items() if row["type"] != "head"}
    again = _save(store, deepcopy(entry))
    assert again["id"] == metadata["id"]
    assert again["status"] == "ready"
    assert all(store.data.rows[key] == row for key, row in immutable.items())


def test_snapshot_publication_requires_a_lease_callback(store):
    with pytest.raises(RuntimeError, match="lease"):
        snapshots.save_scan_cache(store, DAY, _entry(), None)
    assert not store.data.writes


@pytest.mark.parametrize("lose_on_check", [1, 2])
def test_lease_loss_is_not_swallowed_and_cannot_move_head(store, lose_on_check):
    original = _entry()
    _save(store, original)
    count = 0

    def owned():
        nonlocal count
        count += 1
        if count == lose_on_check:
            raise LeaseLost("worker superseded")

    with pytest.raises(LeaseLost):
        snapshots.save_scan_cache(store, DAY, _entry("b" * 64), owned)
    assert snapshots.load_scan_cache(store, DAY) == original


@pytest.mark.parametrize("fail_type", ["chunk", "manifest", "head"])
def test_optional_storage_failure_preserves_previous_published_head(store, fail_type):
    original = _entry()
    _save(store, original)
    store.data.fail = lambda doc: doc.get("type") == fail_type
    metadata = _save(store, _entry("b" * 64))
    assert metadata["status"] == "skipped"
    assert snapshots.load_scan_cache(store, DAY) == original


@pytest.mark.parametrize("invalid", ["signature", "date", "rule", "count", "frame", "huge_audit"])
def test_invalid_or_oversized_scan_is_skipped_without_writes(store, invalid):
    entry = _entry()
    if invalid == "signature":
        entry["signature"] = "not-a-content-signature"
    elif invalid == "date":
        entry["value"]["selection_scope"]["window_dates"] = ["2026-10-30"]
    elif invalid == "rule":
        entry["value"]["selection_scope"]["rule_version"] = "older-rule"
    elif invalid == "count":
        entry["value"]["audits"].pop()
    elif invalid == "frame":
        entry["value"]["accidental_frame"] = pd.DataFrame({"close": [10.]})
    else:
        entry["value"]["audits"][0]["evidence"] = "x" * snapshots.MAX_DOCUMENT_BYTES
    assert _save(store, entry)["status"] == "skipped"
    assert not store.data.writes


def test_different_date_or_rule_does_not_read_previous_head(store, monkeypatch):
    _save(store, _entry())
    assert snapshots.load_scan_cache(store, "2026-10-30") is None
    monkeypatch.setattr(snapshots, "MA5_SELECTION_RULE_VERSION", "new-rule")
    assert snapshots.load_scan_cache(store, DAY) is None


def test_cache_read_failure_returns_none():
    store = MagicMock()
    store.collection.side_effect = OSError("database unavailable")
    assert snapshots.load_scan_cache(store, DAY) is None


def test_loaded_entry_only_reuses_scan_after_actual_daily_inputs_match(store):
    days = pd.bdate_range(end=DAY, periods=24).strftime("%Y-%m-%d").tolist()
    basics = [{"ts_code": "600000.SH", "name": "测试股份", "industry": "光通信"}]
    bars = [{"ticker": "600000.SH", "date": day, "close": 10. * 1.01 ** index,
             "amount": 1_000_000.} for index, day in enumerate(days)]
    daily = [{"ts_code": "600000.SH", "trade_date": DAY, "total_mv": 2_000_000.}]
    first_cache = {}
    first = ma5_universe.scan_universe(basics, bars, daily, days,
                                       daily_computation_cache=first_cache)
    assert _save(store, first_cache["ma5_universe_scan"])["status"] == "ready"
    loaded = {"ma5_universe_scan": snapshots.load_scan_cache(store, DAY)}
    with patch.object(ma5_universe, "_scan_universe", wraps=ma5_universe._scan_universe) as compute:
        assert ma5_universe.scan_universe(basics, bars, daily, days,
                                          daily_computation_cache=loaded) == first
        compute.assert_not_called()
        bars[-1]["close"] = 0.
        changed = ma5_universe.scan_universe(basics, bars, daily, days,
                                            daily_computation_cache=loaded)
        compute.assert_called_once()
    assert not changed["audits"][0]["data_complete"]
