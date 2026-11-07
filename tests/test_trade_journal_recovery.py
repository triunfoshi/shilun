"""Recovery compares native records and reuses journal accounting owners."""
from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace

from bson import Binary, Int64, ObjectId
import pytest

from shilun.trade_journal.ledger import digest
from shilun.trade_journal.recovery import capture_journal_manifest, verify_journal_manifest
from shilun.trade_journal.service import TradeJournalService
from shilun.trade_journal.reviews import JournalReviewService
from shilun.trade_journal.tasks import JournalTaskService, task_id_for
from shilun.trade_journal.allocations import TradeAllocationService
from shilun.trade_journal.definitions import get_definition
from shilun.trade_journal.mode_cards import ModeCardService
from test_trade_journal import FakeCollection, FakeStore, bundle, fill, import_ready, source, decision_payload, TZ


class IndexedCollection(FakeCollection):
    def __init__(self, name, store):
        super().__init__(name, store)
        self.indexes = {"_id_": {"v": 2, "key": [("_id", 1)], "ns": "source." + name}}

    def index_information(self):
        return deepcopy(self.indexes)

    def update_one(self, *args, **kwargs):
        assert not self.store.read_only
        return super().update_one(*args, **kwargs)

    def create_index(self, *args, **kwargs):
        raise AssertionError("Recovery must never create indexes")

    def insert_one(self, row):
        assert not self.store.read_only
        self.store.writes.append((self.name, {}, deepcopy(row)))
        self.documents.append(deepcopy(row))


class IndexedStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.read_only = False
        self.db = SimpleNamespace(list_collection_names=lambda: list(self.collections))

    def collection(self, name):
        if name not in self.collections:
            assert not self.read_only, "Recovery created a collection"
            self.collections[name] = IndexedCollection(name, self)
        return self.collections[name]


@pytest.fixture
def archive():
    store = IndexedStore()
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 28, 10, tzinfo=TZ))
    import_ready(journal, bundle([
        fill("buy", "2026-10-19", "buy", 100, 10, 100),
        fill("sell", "2026-10-20", "sell", 100, 12, 0),
        fill("unknown-opening", "2026-10-21", "sell", 100, 13, 0, ticker="002242.SZ", fees=None),
    ]))
    episode = next(row for row in journal.list_episodes()["items"] if row["ticker"] == "000636.SZ")["episode_id"]
    first = journal.append_decision(episode, decision_payload(decision_id="decision-a", fill_ids=["buy"]))
    journal.append_decision(episode, decision_payload(decision_id="decision-b", fill_ids=["sell"]))
    journal.link_decisions({"from_decision_id": first["decision_id"], "to_decision_id": "decision-b", "reason": "用户显式关联"})
    plan = journal.freeze_plan({"plan_id": "independent-plan", "ticker": "000636.SZ", "reason": "现时冻结，其他条件如实未知"})
    journal.bind_plan(plan["plan_id"], episode, {"revision": 1, "fill_ids": ["buy"], "reason": "明确关联原版本"})
    reviews = JournalReviewService(store, journal=journal)
    context = reviews.get_review(episode)
    reviews.save_review(episode, {"expected_revision": 0, "expected_fact_digest": context["fact_digest"],
        "state": "confirmed", "summary": "原件用户逐笔核对，其余未知",
        "evidence_refs": [{"source_ref": {"source_id": "raw", "row": 1}}]})
    # A later revision makes the review stale; that is valid durable history.
    journal.append_decision(episode, {"decision_id": "decision-a", "expected_revision": 1,
        "revision_reason": "新原话", "reason": "追加独立理由"})
    assert reviews.get_review(episode)["current_state"] == "stale"
    tasks = JournalTaskService(store, journal=journal)
    task = tasks.get_task(task_id_for(episode, "process_review"))["task"]
    tasks.append_resolution(task["task_id"], {"expected_revision": task["revision"],
        "expected_fingerprint": task["fact_fingerprint"], "action": "confirm_unknown", "reason": "无法恢复部分原过程"})
    mode = get_definition("mode.relay")
    TradeAllocationService(store, journal=journal).append_allocations(episode, {
        "reason": "逐笔明确归属", "assignments": [
            {"fill_id": key, "quantity": 100, "mode_ref": {"id": mode["id"], "rule_version": mode["rule_version"]}}
            for key in ("buy", "sell")]})
    mode_owner = ModeCardService(store, now_factory=journal.now_factory)
    card = mode_owner.save_card({"name": "实验模式", "purpose": "保留历史版本", "status": "trial"})
    mode_owner.save_card({"mode_id": card["mode_id"], "expected_revision": 1,
        "revision_reason": "追加新版本", "purpose": "更新实验边界"})
    store.collection("trade_journal_future_owner").documents.append({"_id": ObjectId(),
        "future_data": {"unknown": None, "zero": 0}, "binary": Binary(b"\x00\xff", 128), "long": Int64(1),
        "source_ref": {"source_id": "future-owner-private-namespace"}})
    store.collection("trade_journal_empty_owner")
    store.collection("holdings").documents.append({"private": "live holdings untouched"})
    for number, (name, collection) in enumerate(store.collections.items()):
        for offset, row in enumerate(collection.documents):
            row.setdefault("_id", ObjectId(f"{number:012x}{offset:012x}"))
    store.read_only = True
    return store, journal, episode


def rehash(row, excluded=()):
    row["content_hash"] = digest({key: value for key, value in row.items()
        if key not in {"_id", "content_hash", *excluded}})


def test_capture_and_verify_are_read_only_preserve_unknown_and_hide_originals(archive):
    store, _, _ = archive
    before = {name: deepcopy(collection.documents) for name, collection in store.collections.items()}
    writes, inventory = deepcopy(store.writes), list(store.collections)
    store.reads.clear()
    manifest = capture_journal_manifest(store)
    assert manifest["valid"], manifest["errors"]
    assert verify_journal_manifest(store, manifest) == {"valid": True, "errors": []}
    assert "trade_journal_future_owner" in manifest["included_collection_names"]
    assert manifest["collections"]["trade_journal_empty_owner"]["count"] == 0
    assert "trade_journal_write_locks" not in manifest["included_collection_names"]
    assert all(name.startswith("trade_journal_") and name != "trade_journal_write_locks" for name, *_ in store.reads)
    assert list(store.collections) == inventory and store.writes == writes
    assert before == {name: collection.documents for name, collection in store.collections.items()}
    report = json.dumps(manifest, ensure_ascii=False)
    assert "broker evidence" not in report and "live holdings untouched" not in report
    assert "无法恢复部分原过程" not in report and "追加独立理由" not in report
    assert manifest["summary"]["counts"]["fills"] == 3
    assert manifest["summary"]["qualification_counts"]["accounting"] == 1


def test_record_and_document_key_order_and_index_namespace_are_stable(archive):
    store, _, _ = archive
    manifest = capture_journal_manifest(store)
    for collection in store.collections.values():
        collection.documents[:] = [dict(reversed(list(row.items()))) for row in reversed(collection.documents)]
        for index in collection.indexes.values():
            index["ns"] = "isolated_restore." + collection.name
    assert verify_journal_manifest(store, manifest) == {"valid": True, "errors": []}


@pytest.mark.parametrize("change", ["original", "source_hash", "snapshot", "snapshot_hash"])
def test_corrupt_original_or_snapshot_fails_even_with_new_manifest(archive, change):
    store, _, _ = archive
    collection = store.collection("trade_journal_sources" if change.startswith("source") or change == "original" else "trade_journal_snapshots")
    row = collection.documents[0]
    if change == "original":
        row["content"] = "damaged original must never leak"
    elif change == "snapshot":
        row["content"]["reason"] = "damaged frozen plan"
    else:
        row["content_hash"] = "0" * 64
    manifest = capture_journal_manifest(store)
    assert not manifest["valid"]
    assert any("integrity" in error for error in manifest["errors"])
    assert "damaged" not in json.dumps(manifest)


@pytest.mark.parametrize("name", ["trade_journal_sources", "trade_journal_fills", "trade_journal_decisions", "trade_journal_snapshots"])
def test_missing_referenced_records_fail_capture_not_just_restore_comparison(archive, name):
    store, _, _ = archive
    store.collection(name).documents.pop(0)
    manifest = capture_journal_manifest(store)
    assert not manifest["valid"]
    assert any("reference" in error or "episode" in error for error in manifest["errors"]), manifest


@pytest.mark.parametrize("name,field,value", [
    ("trade_journal_links", "from_revision", 999),
    ("trade_journal_task_events", "episode_id", "missing-episode"),
    ("trade_journal_allocations", "fill_ids", ["missing-fill"]),
    ("trade_journal_reviews", "episode_id", "missing-episode"),
])
def test_dangling_owner_references_fail_with_otherwise_correct_content_hash(archive, name, field, value):
    store, _, _ = archive
    row = store.collection(name).documents[0]
    row[field] = value
    rehash(row, {"recorded_at"} if name == "trade_journal_links" else ())
    manifest = capture_journal_manifest(store)
    assert not manifest["valid"]
    assert any("reference" in error or "episode" in error for error in manifest["errors"])


def test_exact_review_sources_and_old_decisions_are_checked_without_requiring_latest(archive):
    store, _, _ = archive
    assert capture_journal_manifest(store)["valid"]
    review = store.collection("trade_journal_reviews").documents[0]
    review["evidence_refs"][0]["source_ref"]["content_hash"] = "1" * 64
    rehash(review)
    manifest = capture_journal_manifest(store)
    assert not manifest["valid"] and "reference_hash:trade_journal_reviews" in manifest["errors"]


@pytest.mark.parametrize("mutation", ["extra", "omit_empty", "index", "compound_order", "id", "unknown_to_zero", "bson_type"])
def test_restore_rejects_extra_missing_or_modified_records_and_indexes(archive, mutation):
    store, _, _ = archive
    collection = store.collection("trade_journal_future_owner")
    collection.indexes["compound"] = {"v": 2, "key": [("long", 1), ("binary", -1)],
        "unique": True, "partialFilterExpression": {"long": {"$type": "long"}}}
    manifest = capture_journal_manifest(store)
    assert manifest["valid"]
    if mutation == "extra":
        store.collections["trade_journal_unexpected"] = IndexedCollection("trade_journal_unexpected", store)
    elif mutation == "omit_empty":
        store.collections.pop("trade_journal_empty_owner")
    elif mutation == "index":
        collection.indexes["compound"]["unique"] = False
    elif mutation == "compound_order":
        collection.indexes["compound"]["key"].reverse()
    elif mutation == "id":
        collection.documents[0]["_id"] = ObjectId()
    elif mutation == "unknown_to_zero":
        collection.documents[0]["future_data"]["unknown"] = 0
    else:
        collection.documents[0]["long"] = 1  # BSON Int64 -> Int32 is not lossless.
    report = verify_journal_manifest(store, manifest)
    assert not report["valid"] and report["errors"]


def test_manifest_tampering_and_unavailable_discovery_fail_closed(archive):
    store, _, _ = archive
    manifest = capture_journal_manifest(store)
    for changes in ({"version": "future"}, {"valid": False}, {"included_collection_names": [{}]},
        {"collections": {}}, {"ledger_digest": None}, {"summary": None}):
        assert not verify_journal_manifest(store, {**manifest, **changes})["valid"]
    report = capture_journal_manifest(object())
    assert report["valid"] is False and report["errors"] == ["journal_capture_failed"]


def test_large_binary_original_is_verified_as_exact_bytes_without_embedding_it():
    import base64
    import hashlib
    store = IndexedStore()
    journal = TradeJournalService(store)
    original = bytes(range(256)) * 8192
    archived = source(content="")
    archived.update(content=base64.b64encode(original).decode(), content_encoding="base64",
        sha256=hashlib.sha256(original).hexdigest())
    import_ready(journal, bundle([], sources=[archived]))
    store.read_only = True
    manifest = capture_journal_manifest(store)
    assert manifest["valid"], manifest["errors"]
    assert len(json.dumps(manifest)) < 10000
    assert verify_journal_manifest(store, manifest)["valid"]


def test_execution_match_uses_archived_fills_and_frozen_live_evidence():
    from test_trade_journal_execution_matches import setup_link, confirm
    store, journal, owner, episode, live = setup_link()
    confirm(owner, episode, ["s"])
    manifest = capture_journal_manifest(store)
    assert manifest["valid"], manifest["errors"]
    # Mutable production changes legitimately leave the saved match stale.
    live["fees"] = 900
    assert verify_journal_manifest(store, manifest)["valid"]
    row = store.collection("trade_journal_execution_matches").documents[0]
    row["fill_ids"] = ["missing-fill"]
    rehash(row)
    assert "missing_reference:trade_journal_execution_matches" in capture_journal_manifest(store)["errors"]


def test_strategy_cohort_research_reference_is_not_a_journal_source_id(monkeypatch):
    from test_trade_journal_research_bridge import research, arguments
    store = IndexedStore()
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 28, 10, tzinfo=TZ))
    data = research()
    monkeypatch.setattr("shilun.decision_research.service.load_mode_research", lambda *_: deepcopy(data))
    result = journal.append_observation({"scope": "strategy_cohort", **arguments(data),
        "reason": "完整候选及反证一起冻结"})
    journal.append_observation({"scope": "manual_watchlist", "ticker": "600001.SH", "reason": "人工观察",
        "parent_observation_id": result["record"]["observation_id"]})
    store.read_only = True
    manifest = capture_journal_manifest(store)
    assert manifest["valid"], manifest["errors"]
    assert manifest["summary"]["counts"]["observations"] == 2
    assert verify_journal_manifest(store, manifest)["valid"]


def test_frozen_portfolio_reference_uses_owner_digest_without_reading_current_holdings():
    from shilun.trade_journal.analytics import PortfolioRiskService
    from test_trade_journal_analytics import risk_facts
    store = IndexedStore()
    risk_facts(store)
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 28, 10, tzinfo=TZ))
    risk = PortfolioRiskService(store, journal=journal)
    snapshot = risk.capture_snapshot({"snapshot_id": "portfolio-evidence", "reason": "冻结实际组合"})
    journal.freeze_plan({"ticker": "000636.SZ", "portfolio_snapshot_ref": snapshot["snapshot_ref"],
        "reason": "引用计划前实际组合"})
    store.read_only = True
    store.reads.clear()
    manifest = capture_journal_manifest(store)
    assert manifest["valid"], manifest["errors"]
    assert all(name.startswith("trade_journal_") for name, *_ in store.reads)
    store.collection("holdings").documents[0]["quantity"] = 1
    assert verify_journal_manifest(store, manifest)["valid"]
    row = store.collection("trade_journal_portfolio_snapshots").documents[0]
    row["content"]["available_cash"] = None
    assert not capture_journal_manifest(store)["valid"]


def test_financial_receipt_and_expectation_check_exact_decision_are_verified():
    store = IndexedStore()
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 28, 10, tzinfo=TZ))
    import_ready(journal, bundle([fill("b", "2026-10-19", "buy", 100, 10, 100)], financial=[
        {"financial_event_id": "dividend", "ticker": "000636.SZ", "date": "2026-10-20", "kind": "dividend",
            "quantity": 0, "net_cash": 17, "source_ref": {"source_id": "raw", "row": 2}}]))
    episode = journal.list_episodes()["items"][0]["episode_id"]
    decision = journal.append_decision(episode, decision_payload())
    journal.append_expectation_check(decision["decision_id"], {"status": "unknown"})
    store.read_only = True
    assert capture_journal_manifest(store)["valid"]
    check = store.collection("trade_journal_expectation_checks").documents[0]
    check["decision_revision"] = 19
    rehash(check)
    assert "missing_reference:trade_journal_expectation_checks" in capture_journal_manifest(store)["errors"]
    store.collection("trade_journal_financial_events").documents.clear()
    assert "missing_reference:trade_journal_batches" in capture_journal_manifest(store)["errors"]
