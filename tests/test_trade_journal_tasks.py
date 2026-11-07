"""Root-cause tasks acknowledge gaps, never manufacture evidence/qualification."""
from copy import deepcopy
from datetime import datetime

import pytest

from shilun.trade_journal.tasks import JournalTaskService, JournalTaskError, COLLECTION, task_id_for
from shilun.trade_journal.service import TradeJournalService
from test_trade_journal import FakeStore, fill, bundle, import_ready, decision_payload, TZ


@pytest.fixture
def setup():
    store = FakeStore()
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 28, 9, tzinfo=TZ))
    import_ready(journal, bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100),
        fill("sell", "2026-10-20", "sell", 100, 11, 0)]))
    eid = journal.list_episodes()["items"][0]["episode_id"]
    return store, journal, JournalTaskService(store, journal=journal), eid


def pick(service, eid, root):
    return service.get_task(task_id_for(eid, root))["task"]


def act(service, task, action="ignore", reason="当时没有保存，暂不补造"):
    return service.append_resolution(task["task_id"], {"expected_revision": task["revision"],
        "expected_fingerprint": task["fact_fingerprint"], "action": action, "reason": reason})


def test_read_projection_is_stable_read_only_and_merges_many_fields_under_one_root(setup):
    store, journal, service, eid = setup
    before = len(store.writes)
    first = service.list_tasks(episode_id=eid)
    second = service.list_tasks(episode_id=eid)
    assert len(store.writes) == before
    assert [row["task_id"] for row in first["items"]] == [row["task_id"] for row in second["items"]]
    roots = [row["root_cause"] for row in first["items"]]
    assert len(roots) == len(set(roots)) and "decision_context" in roots
    intent = pick(service, eid, "decision_context")
    assert set(intent["missing_fields"]) == {"mode", "trigger", "reason", "reference_tickers"}
    assert intent["revision"] == 0 and intent["state"] == "open"
    assert all("current_facts" not in row for row in first["items"])
    assert all(row["root_cause"] != "rule_backtest" for row in first["items"])
    assert not journal.get_episode(eid)["qualification"]["rule_backtest"]["eligible"]
    assert service.list_tasks(query="风华", status="open")["total"] == first["total"]
    assert service.list_tasks(start_date="2026-11-01")["total"] == 0


def test_unknown_ignore_and_reopen_append_history_without_changing_financial_or_qualification(setup):
    store, journal, service, eid = setup
    original = journal.get_episode(eid)
    result = act(service, pick(service, eid, "decision_context"), "confirm_unknown", "用户确认当时未设模式")
    assert result["task"]["state"] == "confirmed_unknown" and result["latest_revision"] == 1
    assert result["record"]["recorded_at"].startswith("2026-10-28")
    reopened = act(service, result["task"], "reopen", "重新寻找原始记录")
    assert reopened["task"]["state"] == "open" and reopened["latest_revision"] == 2
    ignored = act(service, reopened["task"], "ignore", "仍无法恢复，暂忽略本项")
    detail = service.get_task(ignored["task"]["task_id"])
    assert [row["action"] for row in detail["history"]] == ["confirm_unknown", "reopen", "ignore"]
    assert detail["history"][0]["reason"] == "用户确认当时未设模式"
    after = journal.get_episode(eid)
    assert after["qualification"] == original["qualification"]
    assert after["episode"]["closed_pnl"] == original["episode"]["closed_pnl"]
    assert service.list_tasks(status="ignored")["total"] == 1
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes)


@pytest.mark.parametrize("mutation", ["reason", "fingerprint", "revision", "manual_resolved"])
def test_invalid_or_unversioned_resolution_is_rejected(setup, mutation):
    store, _, service, eid = setup
    task = pick(service, eid, "decision_context")
    body = {"expected_revision": 0, "expected_fingerprint": task["fact_fingerprint"], "action": "ignore", "reason": "明确原因"}
    if mutation == "reason": body["reason"] = " "
    elif mutation == "fingerprint": body.pop("expected_fingerprint")
    elif mutation == "revision": body["expected_revision"] = True
    else: body["action"] = "resolved"
    before = len(store.writes)
    with pytest.raises(JournalTaskError):
        service.append_resolution(task["task_id"], body)
    assert len(store.writes) == before and store.collection(COLLECTION).documents == []


def test_expected_fingerprint_blocks_stale_revision_zero_form_after_new_data(setup):
    store, journal, service, eid = setup
    old = pick(service, eid, "decision_context")
    journal.append_decision(eid, decision_payload(reason="新找到的理由，但触发与参照仍缺"))
    current = pick(service, eid, "decision_context")
    assert current["revision"] == 0 and current["fact_fingerprint"] != old["fact_fingerprint"]
    with pytest.raises(JournalTaskError, match="指纹冲突"):
        act(service, old)
    assert store.collection(COLLECTION).documents == []


def test_revision_cas_blocks_another_worker_and_all_old_reasons_survive(setup):
    store, journal, service, eid = setup
    old = pick(service, eid, "plan_conditions")
    act(service, old)
    other = JournalTaskService(store, journal=TradeJournalService(store, now_factory=journal.now_factory))
    with pytest.raises(JournalTaskError, match="版本已变化"):
        act(other, old)
    assert other.get_task(old["task_id"])["latest_revision"] == 1
    assert len(store.collection(COLLECTION).documents) == 1


def test_relevant_new_evidence_reopens_old_unknown_and_keeps_old_acknowledgment(setup):
    _, journal, service, eid = setup
    old = act(service, pick(service, eid, "evidence"), "confirm_unknown", "当时快照未存")
    journal.append_decision(eid, decision_payload(evidence=[{"text": "找到一份事后回忆", "evidence_class": "retrospective_recall"}]))
    current = service.get_task(old["task"]["task_id"])
    assert current["task"]["state"] == "open" and current["task"]["auto_reopened"]
    assert "旧确认" in current["task"]["reopen_reason"]
    assert current["history"][0]["reason"] == "当时快照未存"
    assert not current["task"]["qualification"]["ex_ante"]["eligible"]


def test_unrelated_reason_revision_does_not_reopen_evidence_root(setup):
    _, journal, service, eid = setup
    decision = journal.append_decision(eid, decision_payload())
    old = act(service, pick(service, eid, "evidence"))
    journal.append_decision(eid, {"decision_id": decision["decision_id"], "expected_revision": 1,
        "revision_reason": "只改独立理由", "reason": "新理由"})
    current = pick(service, eid, "evidence")
    assert current["fact_fingerprint"] == old["task"]["fact_fingerprint"]
    assert current["state"] == "ignored" and not current["auto_reopened"]


def test_data_completion_resolves_task_without_manual_eligibility_override(setup):
    _, journal, service, eid = setup
    ignored = act(service, pick(service, eid, "decision_context"))
    journal.append_decision(eid, decision_payload(trigger="原文确认", reference_tickers=["000001.SZ"]))
    current = service.get_task(ignored["task"]["task_id"])
    assert current["task"]["state"] == "resolved" and not current["task"]["actual_fact_missing"]
    assert current["task"]["missing_fields"] == []
    assert current["task"]["qualification"]["decision_review"]["eligible"]
    assert not current["task"]["qualification"]["ex_ante"]["eligible"]
    assert service.list_tasks(status="resolved")["total"] == 1
    with pytest.raises(JournalTaskError, match="事实补齐"):
        act(service, current["task"], "reopen")


def test_values_returning_to_an_old_gap_do_not_reuse_an_old_ignore(setup):
    _, journal, service, eid = setup
    initial = journal.append_decision(eid, decision_payload(mode_id="mode.unknown", mode="", trigger=None,
        trigger_id=None, reference_tickers=[]))
    ignored = act(service, pick(service, eid, "decision_context"))
    journal.append_decision(eid, {"decision_id": initial["decision_id"], "expected_revision": 1, "revision_reason": "找到原文",
        "mode_id": "mode.relay", "mode": "接力", "trigger": "确认", "reference_tickers": ["000001.SZ"]})
    assert pick(service, eid, "decision_context")["state"] == "resolved"
    journal.append_decision(eid, {"decision_id": initial["decision_id"], "expected_revision": 2, "revision_reason": "原文判断撤回",
        "mode_id": "mode.unknown", "mode": "", "trigger": None, "trigger_id": None, "reference_tickers": []})
    current = pick(service, eid, "decision_context")
    assert current["state"] == "open" and current["auto_reopened"]
    assert current["fact_fingerprint"] != ignored["task"]["fact_fingerprint"]


def test_review_is_due_only_at_saved_verification_and_unknown_check_counts_as_actual_review(setup):
    _, journal, service, eid = setup
    decision = journal.append_decision(eid, decision_payload(verification_at="2026-10-29T15:00:00+08:00"))
    assert not any(row["root_cause"] == "review" for row in service.list_tasks()["items"])
    journal.append_decision(eid, {"decision_id": decision["decision_id"], "expected_revision": 1, "revision_reason": "修正为真实验证日",
        "verification_at": "2026-10-27T15:00:00+08:00"})
    due = pick(service, eid, "review")
    assert due["state"] == "open"
    act(service, due, "confirm_unknown", "反馈资料尚未齐")
    journal.append_expectation_check(decision["decision_id"], {"decision_revision": 2, "status": "unknown", "note": "已核对但无法判断"})
    assert pick(service, eid, "review")["state"] == "resolved"


def test_historical_unknown_does_not_block_a_future_plan_or_change_other_episode(setup):
    store, journal, service, eid = setup
    act(service, pick(service, eid, "decision_context"), "confirm_unknown", "旧交易无法回忆")
    future = journal.freeze_plan({"ticker": "600001.SH", "target_date": "2026-10-29", "reason": "未来意图独立保存"})
    assert future["record"]["ticker"] == "600001.SH"
    import_ready(journal, bundle([fill("new-buy", "2026-10-27", "buy", 100, 20, 100, ticker="300502.SZ")]))
    other = journal.list_episodes(query="300502")["items"][0]["episode_id"]
    assert pick(service, other, "decision_context")["state"] == "open"
    assert pick(service, other, "decision_context")["task_id"] != task_id_for(eid, "decision_context")
    assert pick(service, eid, "decision_context")["state"] == "confirmed_unknown"


def test_accounting_root_is_not_reopened_by_unrelated_decision_notes():
    store = FakeStore(); journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 28, 9, tzinfo=TZ))
    import_ready(journal, bundle([fill("opening-sale", "2026-10-19", "sell", 100, 10, 0)]))
    eid = journal.list_episodes()["items"][0]["episode_id"]
    service = JournalTaskService(store, journal=journal)
    ignored = act(service, pick(service, eid, "accounting"), "confirm_unknown", "期初成本没有原件")
    journal.append_decision(eid, decision_payload(reason="只补历史意图"))
    current = pick(service, eid, "accounting")
    assert current["state"] == "confirmed_unknown" and current["fact_fingerprint"] == ignored["task"]["fact_fingerprint"]
    assert not current["qualification"]["accounting"]["eligible"]


def test_event_tampering_and_unknown_task_are_not_silently_accepted(setup):
    store, _, service, eid = setup
    with pytest.raises(JournalTaskError, match="待办不存在"):
        service.get_task("task-does-not-exist")
    task = act(service, pick(service, eid, "plan_conditions"))["task"]
    store.collection(COLLECTION).documents[0]["reason"] = "tampered"
    with pytest.raises(JournalTaskError, match="校验失败"):
        service.get_task(task["task_id"])


def test_projection_never_uses_live_episode_reads_and_pagination_preserves_denominators(setup, monkeypatch):
    store, journal, service, _ = setup
    def prohibited(*args, **kwargs):
        raise AssertionError("task projection must not load every episode/live references")
    monkeypatch.setattr(journal, "get_episode", prohibited)
    before = len(store.writes)
    full = service.list_tasks(limit=100)
    paged = service.list_tasks(limit=1)
    assert paged["total"] == full["total"] and len(paged["items"]) == 1
    assert paged["root_groups"] == full["root_groups"] and paged["state_counts"] == full["state_counts"]
    assert paged["facts_reference"]["fill_count"] == 2
    assert paged["facts_reference"]["facts_digest"] == full["facts_reference"]["facts_digest"]
    assert len(store.writes) == before


def test_mongo_record_order_changes_cannot_reopen_a_dismissed_root(setup):
    store, journal, service, eid = setup
    journal.append_decision(eid, decision_payload(reason="独立补记1"))
    journal.append_decision(eid, decision_payload(reason="独立补记2"))
    ignored = act(service, pick(service, eid, "evidence"))
    # These decisions have equal observed_at/recorded_at. Mongo natural order is
    # not part of the semantic fact fingerprint or the acknowledgment scope.
    from shilun.trade_journal.service import COLLECTIONS
    store.collection(COLLECTIONS["decisions"]).documents.reverse()
    journal._invalidate()
    current = pick(service, eid, "evidence")
    assert current["state"] == "ignored" and current["fact_fingerprint"] == ignored["task"]["fact_fingerprint"]
