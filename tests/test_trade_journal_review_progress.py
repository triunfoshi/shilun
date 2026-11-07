"""Completion is an explicit current-facts review, never inferred from a trade."""
from copy import deepcopy
from datetime import datetime

from shilun.trade_journal.reviews import JournalReviewService
from shilun.trade_journal.service import TradeJournalService
from test_trade_journal import FakeStore, bundle, decision_payload, fill, import_ready, TZ


def context():
    store = FakeStore()
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 11, 3, 17, tzinfo=TZ))
    import_ready(journal, bundle([
        fill("buy", "2026-10-26", "buy", 100, 10, 100),
        fill("sell", "2026-10-27", "sell", 100, 11, 0),
    ]))
    eid = journal.list_episodes()["items"][0]["episode_id"]
    return store, journal, JournalReviewService(store, journal=journal), eid


def save(owner, eid, state):
    current = owner.get_review(eid)
    return owner.save_review(eid, {
        "expected_revision": current["latest_revision"], "expected_fact_digest": current["fact_digest"],
        "state": state, "summary": "已逐笔核对，可回忆的过程已保存；未知判断保留",
        "revision_reason": "核对当前记录",
    })


def progress(journal, eid):
    rows = journal.list_episodes()["items"]
    listing = next(r for r in rows if r["episode_id"] == eid)["review_progress"]
    detail = journal.get_episode(eid)
    assert listing == detail["review_progress"] == detail["episode"]["review_progress"]
    return listing


def test_saved_decision_notes_start_review_but_profit_cannot_start_or_complete_it():
    store, journal, _, eid = context()
    assert progress(journal, eid)["status"] == "not_started"
    journal.append_decision(eid, decision_payload())
    before = deepcopy(store.writes)
    p = progress(journal, eid)
    assert (p["status"], p["label"], p["current_confirmed"]) == ("in_progress", "已填写，待确认", False)
    assert p["has_saved_notes"] is True and p["saved_note_count"] == 1
    assert p["current_state"] == "missing" and p["review_id"] is None
    assert "已保存1条操作复盘记录" in p["note"]
    assert store.writes == before
    assert journal.get_episode(eid)["episode"]["closed_pnl"] > 0


def test_latest_note_count_is_per_round_and_does_not_count_old_revisions():
    store, journal, owner, eid = context()
    assert progress(journal, eid)["has_saved_notes"] is False
    first = journal.append_decision(eid, decision_payload())
    journal.append_decision(eid, {**decision_payload(reason="补充原记录"),
        "decision_id": first["record"]["decision_id"], "expected_revision": 1,
        "revision_reason": "只补充原理由"})
    p = progress(journal, eid)
    assert p["saved_note_count"] == 1 and p["status"] == "in_progress"
    import_ready(journal, bundle([fill("another", "2026-10-28", "buy", 100, 10, 100, ticker="300502.SZ")]))
    other = next(r for r in journal.list_episodes()["items"] if r["ticker"] == "300502.SZ")["episode_id"]
    p = progress(journal, other)
    assert p["status"] == "not_started" and p["has_saved_notes"] is False
    before = deepcopy(store.writes)
    assert owner.get_review(eid)["record"] is None
    assert store.writes == before


def test_draft_confirmed_and_changed_facts_have_distinct_markers():
    store, journal, owner, eid = context()
    save(owner, eid, "draft")
    p = progress(journal, eid)
    assert (p["status"], p["label"], p["revision"]) == ("in_progress", "复盘中", 1)
    save(owner, eid, "confirmed")
    p = progress(journal, eid)
    assert (p["status"], p["label"], p["current_confirmed"]) == ("reviewed", "已复盘", True)
    original = deepcopy(owner.get_review(eid)["record"])
    # A later expectation or reason may invalidate a once-finished review.
    journal.append_decision(eid, decision_payload(reason="新补记的入场依据"))
    p = progress(journal, eid)
    assert (p["status"], p["label"], p["current_confirmed"]) == ("needs_update", "待更新", False)
    assert owner.get_review(eid)["record"] == original
    save(owner, eid, "confirmed")
    assert progress(journal, eid)["status"] == "reviewed"


def test_new_same_round_trade_requires_update_but_other_round_does_not():
    _, journal, owner, eid = context()
    save(owner, eid, "confirmed")
    import_ready(journal, bundle([fill("other", "2026-10-28", "buy", 100, 15, 100, ticker="300502.SZ")]))
    assert progress(journal, eid)["status"] == "reviewed"
    # A new record in an open round changes the current exact fact context.
    other = next(r for r in journal.list_episodes()["items"] if r["ticker"] == "300502.SZ")["episode_id"]
    save(owner, other, "confirmed")
    import_ready(journal, bundle([fill("other-add", "2026-10-29", "buy", 100, 14, 200, ticker="300502.SZ")]))
    assert progress(journal, other)["status"] == "needs_update"


def test_listing_reads_all_review_records_once_without_row_by_row_queries():
    store, journal, owner, eid = context()
    save(owner, eid, "confirmed")
    for index, ticker in enumerate(["300502.SZ", "600001.SH", "002242.SZ"]):
        import_ready(journal, bundle([fill(f"new-{index}", "2026-10-28", "buy", 100, 10, 100, ticker=ticker)]))
    store.reads.clear()
    before = deepcopy(store.writes)
    rows = journal.list_episodes()["items"]
    reads = [r for r in store.reads if r[0] == "trade_journal_reviews"]
    assert len(rows) == 4 and len(reads) == 1
    assert store.writes == before
    assert next(r for r in rows if r["episode_id"] == eid)["review_progress"]["status"] == "reviewed"
