"""A saved user review is a fact; its judgments are not machine conclusions."""
from copy import deepcopy
from datetime import datetime

import pytest

from shilun.trade_journal.reviews import JournalReviewService, JournalReviewError, COLLECTION, AXES
from shilun.trade_journal.tasks import JournalTaskService, task_id_for
from shilun.trade_journal.service import TradeJournalService
from test_trade_journal import FakeStore, fill, bundle, source, import_ready, decision_payload, TZ


@pytest.fixture
def setup():
    store = FakeStore()
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 28, 9, tzinfo=TZ))
    import_ready(journal, bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100),
        fill("sell", "2026-10-20", "sell", 100, 11, 0)]))
    eid = journal.list_episodes()["items"][0]["episode_id"]
    return store, journal, JournalReviewService(store, journal=journal), eid


def save(owner, eid, **changes):
    current = owner.get_review(eid)
    body = {"expected_revision": current["latest_revision"], "expected_fact_digest": current["fact_digest"],
        "state": "confirmed", "summary": "用户已经逐笔核对过程，无法还原的判断保持未知",
        "revision_reason": "根据当前原件追加确认", **changes}
    return owner.save_review(eid, body)


def test_planless_closed_episode_has_process_task_without_invented_deadline(setup):
    _, journal, owner, eid = setup
    tasks = JournalTaskService(owner.store, journal=journal)
    rows = tasks.list_tasks(episode_id=eid)["items"]
    assert any(row["root_cause"] == "process_review" for row in rows)
    assert not any(row["root_cause"] == "review" for row in rows)  # no expectation deadline
    assert owner.get_review(eid)["current_state"] == "missing"
    task = tasks.get_task(task_id_for(eid, "process_review"))["task"]
    tasks.append_resolution(task["task_id"], {"expected_revision": 0, "expected_fingerprint": task["fact_fingerprint"],
        "action": "confirm_unknown", "reason": "旧过程无法回忆"})
    assert tasks.get_task(task["task_id"])["task"]["state"] == "confirmed_unknown"
    assert not owner.get_review(eid)["current_confirmed"]
    save(owner, eid)
    assert tasks.get_task(task["task_id"])["task"]["state"] == "resolved"


def test_confirmed_unknown_dimensions_are_actual_user_review_without_qualification_or_pnl_change(setup):
    store, journal, owner, eid = setup
    original = journal.get_episode(eid)
    result = save(owner, eid, captured_at="2000-01-01T00:00:00+08:00", recorded_at="2000-01-01T00:00:00+08:00")
    record = result["record"]
    assert result["review"]["current_confirmed"] and record["state"] == "confirmed"
    assert record["captured_at"].startswith("2026-10-28") and record["recorded_at"] == record["captured_at"]
    assert set(record["judgments"]) == set(AXES)
    assert all(row["judgment"] == "unknown" and not row["user_supplied"] and not row["machine_verified"] for row in record["judgments"].values())
    assert not record["machine_verified"] and record["qualification_effect"] == "none"
    assert journal.get_episode(eid)["qualification"] == original["qualification"]
    assert journal.get_episode(eid)["episode"]["closed_pnl"] == original["episode"]["closed_pnl"]
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes)


def test_profit_cannot_overwrite_user_judgment_of_deviation_and_losses_do_not_imply_violation(setup):
    _, journal, owner, eid = setup
    assert journal.get_episode(eid)["episode"]["closed_pnl"] > 0
    record = save(owner, eid, overall_conclusion="盈利但入场偏离原确认", judgments={"entry": {
        "judgment": "deviation", "reason": "用户明确：未等原确认条件", "evidence_refs": [{"text": "原始回忆，未冒充行情"}]}})["record"]
    assert record["judgments"]["entry"]["judgment"] == "deviation" and record["judgments"]["entry"]["user_supplied"]
    assert not record["judgments"]["exit"]["user_supplied"]
    assert not record["judgments"]["entry"]["evidence_refs"][0]["restoration_verified"]
    assert record["overall_conclusion"] == "盈利但入场偏离原确认"


def test_exact_original_decision_versions_are_frozen_and_new_facts_require_reconfirmation(setup):
    _, journal, owner, eid = setup
    decision = journal.append_decision(eid, decision_payload())
    first = save(owner, eid)
    original = deepcopy(first["record"])
    assert original["decision_refs"][0]["revision"] == 1
    journal.append_decision(eid, {"decision_id": decision["decision_id"], "expected_revision": 1,
        "revision_reason": "找到新原话", "reason": "新增与入场有关的真实补记"})
    current = owner.get_review(eid)
    assert current["current_state"] == "stale" and not current["current_confirmed"]
    assert current["record"] == original
    process = JournalTaskService(owner.store, journal=journal).get_task(task_id_for(eid, "process_review"))["task"]
    assert process["state"] == "open" and process["auto_reopened"]
    assert "原事实" in process["reopen_reason"]
    with pytest.raises(JournalReviewError, match="事实指纹冲突"):
        save(owner, eid, expected_fact_digest=original["fact_digest"])
    second = save(owner, eid, summary="新版本已重核，仍保留未知判断")
    assert second["record"]["revision"] == 2 and second["review"]["current_confirmed"]
    assert second["record"]["decision_refs"][0]["revision"] == 2
    assert owner.history(eid)["items"][0] == original


def test_other_episode_or_new_unrelated_plan_does_not_stale_a_confirmed_review(setup):
    _, journal, owner, eid = setup
    first = save(owner, eid)
    journal.freeze_plan({"ticker": "600001.SH", "target_date": "2026-10-29", "reason": "另一个未来计划"})
    import_ready(journal, bundle([fill("other-buy", "2026-10-27", "buy", 100, 20, 100, ticker="300502.SZ")]))
    current = owner.get_review(eid)
    assert current["current_confirmed"] and current["fact_digest"] == first["record"]["fact_digest"]


def test_unknown_financial_data_remains_unknown_after_explicit_process_confirmation():
    store = FakeStore(); journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 28, 9, tzinfo=TZ))
    import_ready(journal, bundle([fill("opening-sale", "2026-10-19", "sell", 100, 10, 0, fees=None)]))
    eid = journal.list_episodes()["items"][0]["episode_id"]
    owner = JournalReviewService(store, journal=journal)
    save(owner, eid, summary="已经核对，缺期初成本和费用，结论保持unknown")
    assert owner.get_review(eid)["current_confirmed"]
    detail = journal.get_episode(eid)
    assert detail["episode"]["closed_pnl"] is None and not detail["qualification"]["accounting"]["eligible"]


@pytest.mark.parametrize("mutation", ["no_digest", "old_revision", "summary", "bad_axis", "bad_judgment", "string_evidence", "fake_source", "fake_snapshot"])
def test_review_validation_does_not_accept_invented_evidence_or_unversioned_confirmation(setup, mutation):
    store, _, owner, eid = setup
    current = owner.get_review(eid)
    body = {"expected_revision": 0, "expected_fact_digest": current["fact_digest"], "state": "confirmed", "summary": "明确用户复盘"}
    if mutation == "no_digest": body.pop("expected_fact_digest")
    elif mutation == "old_revision": body["expected_revision"] = 1
    elif mutation == "summary": body["summary"] = " "
    elif mutation == "bad_axis": body["judgments"] = {"magic_score": {"judgment": "unknown"}}
    elif mutation == "bad_judgment": body["judgments"] = {"entry": {"judgment": "machine_proved_violation"}}
    elif mutation == "string_evidence": body["evidence_refs"] = ["pretend-source"]
    elif mutation == "fake_source": body["evidence_refs"] = [{"source_ref": {"source_id": "missing", "row": 1}}]
    else: body["evidence_refs"] = [{"snapshot_ref": {"snapshot_id": "latest-or-invented", "content_hash": "fake"}}]
    with pytest.raises(JournalReviewError):
        owner.save_review(eid, body)
    assert store.collection(COLLECTION).documents == []


def test_recoverable_sources_and_exact_snapshots_are_references_not_duplicate_large_originals(setup):
    _, journal, owner, eid = setup
    plan = journal.freeze_plan({"ticker": "000636.SZ", "target_date": "2026-10-29", "reason": "现在保存的参照，未冒充当时预案"})["record"]
    record = save(owner, eid, evidence_refs=[{"text": "核对已保存原件", "source_ref": {"source_id": "raw", "row": 1}},
        {"text": "当前参考计划，只作为用户复盘证据", "snapshot_ref": {"snapshot_id": plan["snapshot_id"], "content_hash": plan["content_hash"]}}])["record"]
    assert all(row["restoration_verified"] for row in record["evidence_refs"])
    assert record["evidence_refs"][0]["source_ref"]["sha256"]
    assert "content" not in record["evidence_refs"][0]["source_ref"] and "content" not in record["evidence_refs"][1]["snapshot_ref"]
    assert not journal.get_episode(eid)["qualification"]["ex_ante"]["eligible"]


def test_stale_first_form_and_revisions_cannot_overwrite_a_later_process_review(setup):
    store, journal, owner, eid = setup
    old = owner.get_review(eid)
    journal.append_decision(eid, decision_payload())
    with pytest.raises(JournalReviewError, match="事实指纹冲突"):
        save(owner, eid, expected_fact_digest=old["fact_digest"])
    first = save(owner, eid)
    with pytest.raises(JournalReviewError, match="版本已变化"):
        save(owner, eid, expected_revision=0)
    with pytest.raises(JournalReviewError, match="revision_reason"):
        save(owner, eid, revision_reason="")
    assert owner.history(eid)["total"] == 1
    assert owner.get_review(eid, 1)["record"] == first["record"]
    assert len(store.collection(COLLECTION).documents) == 1


def test_event_order_is_not_evidence_change_and_stored_review_tampering_is_detected(setup):
    store, journal, owner, eid = setup
    journal.append_decision(eid, decision_payload(reason="第一条"))
    journal.append_decision(eid, decision_payload(reason="第二条"))
    first = save(owner, eid)
    from shilun.trade_journal.service import COLLECTIONS
    store.collection(COLLECTIONS["decisions"]).documents.reverse()
    journal._invalidate()
    assert owner.get_review(eid)["fact_digest"] == first["record"]["fact_digest"]
    assert owner.get_review(eid)["current_confirmed"]
    store.collection(COLLECTION).documents[0]["summary"] = "tampered"
    with pytest.raises(JournalReviewError, match="校验失败"):
        owner.get_review(eid)


def test_acknowledging_a_stale_review_task_does_not_claim_a_new_process_review_or_keep_auto_badge(setup):
    _, journal, owner, eid = setup
    save(owner, eid)
    journal.append_decision(eid, decision_payload(reason="新资料需要重核"))
    tasks = JournalTaskService(owner.store, journal=journal)
    old = tasks.get_task(task_id_for(eid, "process_review"))["task"]
    assert old["state"] == "open" and old["auto_reopened"]
    ignored = tasks.append_resolution(old["task_id"], {"expected_revision": old["revision"],
        "expected_fingerprint": old["fact_fingerprint"], "action": "ignore", "reason": "已看见新资料，暂不重核"})["task"]
    assert ignored["state"] == "ignored" and not ignored["auto_reopened"]
    assert owner.get_review(eid)["current_state"] == "stale" and not owner.get_review(eid)["current_confirmed"]
