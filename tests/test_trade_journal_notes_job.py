"""Reviewed-note selector, provenance and retry boundaries without Mongo writes."""
from copy import deepcopy
from datetime import datetime

import pytest

from shilun.jobs import trade_journal_notes_job as job
from shilun.jobs.trade_journal_import_job import protected_state_hashes
from shilun.trade_journal.service import TradeJournalError, TradeJournalService, COLLECTIONS
from test_trade_journal import FakeStore, fill, bundle, source, import_ready, decision_payload, TZ


@pytest.fixture
def reviewed(monkeypatch):
    store = FakeStore()
    service = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 28, 9, tzinfo=TZ))
    rows = [fill("wind-b1", "2026-10-19", "buy", 100, 10, 100),
        fill("wind-s1", "2026-10-20", "sell", 100, 11, 0),
        fill("wind-b2a", "2026-10-21", "buy", 50, 12, 50, time="09:30:00"),
        fill("wind-b2b", "2026-10-21", "buy", 50, 12.1, 100, time="10:30:00"),
        fill("wind-s2", "2026-10-22", "sell", 100, 11, 0),
        fill("newyi-b", "2026-10-21", "buy", 100, 20, 100, ticker="300502.SZ"),
        fill("newyi-s", "2026-10-22", "sell", 100, 19, 0, ticker="300502.SZ")]
    import_ready(service, bundle(rows))
    store.collection("holdings").documents.append({"ticker": "000636.SZ", "quantity": 1700})
    store.collection("account_risk_profiles").documents.append({"profile_id": "default", "available_cash": 12345})
    store.collection("trade_executions").documents.append({"execution_id": "existing-live", "ticker": "000636.SZ"})
    index_calls = []
    monkeypatch.setattr(job, "ensure_journal_indexes", lambda target: index_calls.append(target))
    manifest = {"manifest_id": "reviewed-notes-fixture-v1", "account_alias": "primary",
        "sources": [source("reviewed-original", "用户已确认的两条历史原文与跨票关系")],
        "decisions": [
            {"key": "wind-followup", "episode": {"ticker": "000636.SZ", "start_date": "2026-10-21", "end_date": "2026-10-22"},
                "fills": {"date": "2026-10-21", "side": "buy", "after_time": "09:30:00"}, "expected_fill_count": 1,
                "payload": decision_payload(reason="保留风华原始补记", observed_at="2026-10-21T10:30:00+08:00",
                    evidence_class="contemporaneous", note={"original_reference": "用户原话第1条"})},
            {"key": "newyi-entry", "episode": {"ticker": "300502.SZ", "start_date": "2026-10-21", "end_date": "2026-10-22"},
                "fills": {"side": "buy"}, "expected_fill_count": 1,
                "payload": decision_payload(reason="保留新易盛原始补记", observed_at="2026-10-21T10:00:00+08:00")}
        ],
        "links": [{"from_key": "wind-followup", "to_key": "newyi-entry", "relation": "user_reported_switch",
            "reason": "用户明确补记两票之间的选择关系；不从时间或资金流猜测", "evidence_class": "retrospective_recall"}]}
    return store, service, manifest, index_calls


def test_unique_episode_and_strict_fill_selector_are_resolved_before_writing(reviewed):
    store, service, manifest, indexes = reviewed
    before = len(store.writes)
    rows = job.prepare_notes(service, manifest)
    assert len(store.writes) == before and not indexes
    assert rows[0]["payload"]["fill_ids"] == ["wind-b2b"]
    assert rows[1]["payload"]["fill_ids"] == ["newyi-b"]
    assert rows[0]["payload"]["evidence_class"] == "retrospective_recall"
    assert rows[0]["payload"]["note"]["original_reference"] == "用户原话第1条"
    assert rows[0]["payload"]["note"]["seed_manifest"] == manifest["manifest_id"]
    again = job.prepare_notes(service, deepcopy(manifest))
    assert [row["decision_id"] for row in again] == [row["decision_id"] for row in rows]
    assert all(not row["already_seeded"] for row in rows)


@pytest.mark.parametrize("selector", [{"ticker": "000636.SZ"}, {"ticker": "999999.SZ"}])
def test_zero_or_multiple_episode_matches_block_entire_manifest_before_writes(reviewed, selector):
    store, service, manifest, indexes = reviewed
    manifest["decisions"][1]["episode"] = selector
    before = len(store.writes)
    with pytest.raises(TradeJournalError, match="不是唯一匹配"):
        job.apply_notes(service, manifest, apply=True)
    assert len(store.writes) == before and not indexes
    assert service.get_summary()["counts"]["decisions"] == 0


@pytest.mark.parametrize("mutation", ["count", "empty_fills", "duplicate_key", "missing_key", "unknown_link_key"])
def test_unreviewed_selector_count_or_link_identity_never_partially_applies(reviewed, mutation):
    store, service, manifest, indexes = reviewed
    if mutation == "count":
        manifest["decisions"][1]["expected_fill_count"] = 2
    elif mutation == "empty_fills":
        manifest["decisions"][1]["fills"] = {"side": "buy", "after_time": "23:59:00"}
    elif mutation == "duplicate_key":
        manifest["decisions"][1]["key"] = manifest["decisions"][0]["key"]
    elif mutation == "missing_key":
        manifest["decisions"][1].pop("key")
    else:
        manifest["links"][0]["to_key"] = "not-a-reviewed-note"
    before = len(store.writes)
    with pytest.raises(TradeJournalError):
        job.apply_notes(service, manifest, apply=True)
    assert len(store.writes) == before and not indexes
    assert service.get_summary()["counts"]["decisions"] == 0


def test_preview_is_read_only_and_includes_real_source_reconciliation(reviewed):
    store, service, manifest, indexes = reviewed
    before = len(store.writes)
    production = protected_state_hashes(store)
    result = job.apply_notes(service, manifest)
    assert result["applied"] is False and result["new_decisions"] == 2 and result["links"] == 1
    assert result["source_preview"]["can_import"] and result["source_preview"]["counts"]["new_sources"] == 1
    assert result["source_preview"]["counts"]["fills"] == 0
    assert result["source_preview"]["projected_episode_count"] == service.list_episodes()["total"]
    assert len(store.writes) == before and not indexes and production == protected_state_hashes(store)
    assert service.get_summary()["counts"]["decisions"] == 0


def test_apply_is_idempotent_for_sources_decisions_and_cross_stock_links(reviewed):
    store, service, manifest, indexes = reviewed
    production = protected_state_hashes(store)
    baseline = service.get_summary()
    first = job.apply_notes(service, manifest, apply=True)
    after_first = len(store.writes)
    second = job.apply_notes(service, deepcopy(manifest), apply=True)
    assert first["new_decisions"] == 2 and first["created_links"] == 1
    assert second["new_decisions"] == 0 and second["created_links"] == 0
    assert len(store.writes) == after_first  # no extra fact/revision/lease writes
    assert len(indexes) == 2
    assert service.get_summary()["counts"]["fills"] == baseline["counts"]["fills"]
    assert service.get_summary()["counts"]["episodes"] == baseline["counts"]["episodes"]
    assert service.get_summary()["counts"]["decisions"] == 2
    assert production == protected_state_hashes(store)
    restored = service.get_source("reviewed-original", include_content=True)
    assert restored["content"] == manifest["sources"][0]["content"]
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes)


def test_rerun_preserves_later_user_revision_and_original_link_version(reviewed):
    store, service, manifest, _ = reviewed
    job.apply_notes(service, manifest, apply=True)
    resolved = job.prepare_notes(service, manifest)
    decision = resolved[0]
    service.append_decision(decision["episode_id"], {"decision_id": decision["decision_id"], "expected_revision": 1,
        "revision_reason": "用户之后修订", "reason": "后续经用户确认的独立理由", "note": {"user_note": "替换显示备注也不能丢掉历史种子"}})
    before = len(store.writes)
    rerun = job.apply_notes(service, manifest, apply=True)
    assert rerun["new_decisions"] == 0 and rerun["created_links"] == 0 and len(store.writes) == before
    history = service.get_decision_history(decision["decision_id"])
    assert history["total"] == 2 and history["items"][0]["reason"] == "保留风华原始补记"
    assert history["items"][1]["reason"] == "后续经用户确认的独立理由"
    links = store.collection(COLLECTIONS["links"]).documents
    assert len(links) == 1 and links[0]["from_revision"] == 1 and links[0]["to_revision"] == 1


def test_changed_seed_cannot_overwrite_existing_history_even_after_user_revision(reviewed):
    store, service, manifest, indexes = reviewed
    job.apply_notes(service, manifest, apply=True)
    resolved = job.prepare_notes(service, manifest)
    service.append_decision(resolved[0]["episode_id"], {"decision_id": resolved[0]["decision_id"], "expected_revision": 1,
        "revision_reason": "用户后续修改", "reason": "后续原文"})
    modified = deepcopy(manifest)
    modified["decisions"][0]["payload"]["reason"] = "修改同key种子试图覆盖"
    before, before_indexes = len(store.writes), len(indexes)
    with pytest.raises(TradeJournalError, match="不同内容的补记种子"):
        job.apply_notes(service, modified, apply=True)
    assert len(store.writes) == before and len(indexes) == before_indexes
    assert service.get_decision_history(resolved[0]["decision_id"])["latest_revision"] == 2


def test_explicit_cross_stock_link_keeps_two_inventories_and_pnl_separate(reviewed):
    store, service, manifest, _ = reviewed
    baseline = {row["episode_id"]: row["closed_pnl"] for row in service.list_episodes()["items"]}
    job.apply_notes(service, manifest, apply=True)
    resolved = {row["key"]: row for row in job.prepare_notes(service, manifest)}
    link = store.collection(COLLECTIONS["links"]).documents[0]
    assert link["from_decision_id"] == resolved["wind-followup"]["decision_id"]
    assert link["to_decision_id"] == resolved["newyi-entry"]["decision_id"]
    assert link["from_episode_id"] != link["to_episode_id"]
    assert link["reason"] == manifest["links"][0]["reason"]
    assert link["evidence_class"] == "retrospective_recall"
    assert {row["episode_id"]: row["closed_pnl"] for row in service.list_episodes()["items"]} == baseline


def test_self_link_is_rejected_before_source_import_or_note_creation(reviewed):
    store, service, manifest, indexes = reviewed
    manifest["links"][0]["to_key"] = manifest["links"][0]["from_key"]
    before = len(store.writes)
    summary = service.get_summary()
    with pytest.raises(TradeJournalError):
        job.apply_notes(service, manifest, apply=True)
    assert len(store.writes) == before and not indexes
    assert service.get_summary()["counts"] == summary["counts"]
    assert store.collection(COLLECTIONS["links"]).documents == []
