"""Independent identity-link boundaries: actual money, precision and stale claims."""
from copy import deepcopy

import pytest

from shilun.trade_journal.execution_matches import ExecutionMatchService, ExecutionMatchError, COLLECTION
from test_trade_journal_execution_matches import setup_link, confirm
from test_trade_journal import service, fill, bundle, import_ready


def body(preview):
    return {"execution_id": preview["execution_id"], "action": "confirm", "fill_ids": preview["fill_ids"],
        "expected_revision": preview["expected_revision"], "preview_digest": preview["preview_digest"],
        "reason": "明确核对原成交身份", "identity_confirmation": True, "account_confirmation": True,
        "time_difference_confirmation": True}


def test_archived_actual_amount_cannot_be_ignored_when_live_cash_is_unknown():
    store, journal = service()
    # The normalizer accepts separately supplied actual statement amount. The
    # identity layer must reconcile that known amount, not silently substitute
    # an inferred price*quantity when the other cash side is unavailable.
    row = fill("buy", "2026-10-19", "buy", 100, 12, 100, amount=1200.01, net_cash=-1201.01)
    import_ready(journal, bundle([row]))
    live = {"execution_id": "actual-amount-edge", "ticker": "000636.SZ", "side": "buy", "quantity": 100,
        "price": 12, "fees": 1, "cash_delta": None, "executed_at": "2026-10-19T10:00:00+08:00", "source": "user_confirmed"}
    store.collection("trade_executions").documents.append(live)
    eid = journal.list_episodes()["items"][0]["episode_id"]
    owner = ExecutionMatchService(store, journal=journal)
    assert not owner.preview(eid, live["execution_id"], ["buy"])["can_confirm"]


def test_partial_aggregate_uses_actual_cash_when_live_vwap_is_stored_to_four_decimals():
    store, journal = service()
    rows = [fill("buy", "2026-10-19", "buy", 300, 10, 300),
        fill("s1", "2026-10-20", "sell", 100, 12, 200),
        fill("s2", "2026-10-20", "sell", 200, 13, 0, time="10:01:00")]
    import_ready(journal, bundle(rows))
    # record_sell_execution stores price rounded to 4 dp but cash from the
    # original supplied price. 3800/300 rounds to 12.6667; actual gross is 3800.
    live = {"execution_id": "vwap-rounding", "ticker": "000636.SZ", "side": "sell", "quantity": 300,
        "price": 12.6667, "fees": 2, "cash_delta": 3798, "executed_at": "2026-10-20T10:01:00+08:00", "source": "user_confirmed"}
    store.collection("trade_executions").documents.append(live)
    eid = journal.list_episodes()["items"][0]["episode_id"]
    owner = ExecutionMatchService(store, journal=journal)
    preview = owner.preview(eid, live["execution_id"], ["s1", "s2"])
    assert preview["can_confirm"], preview["conflicts"]
    before = journal.get_episode(eid)["episode"]["closed_pnl"]
    result = owner.append_match(eid, body(preview))
    assert result["record"]["state"] == "confirmed"
    assert journal.get_episode(eid)["episode"]["closed_pnl"] == before


def test_equivalent_timezone_is_same_time_but_statement_event_clock_never_proves_execution():
    _, _, owner, eid, live = setup_link()
    live["executed_at"] = "2026-10-20T02:00:00Z"
    precise = owner.preview(eid, "manual-1", ["s"])
    assert precise["can_confirm"] and not precise["requires_time_difference_confirmation"]
    store, journal = service()
    import_ready(journal, bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100),
        fill("sale", "2026-10-20", "sell", 100, 12, 0, time_precision="statement_event_time")]))
    store.collection("trade_executions").documents.append({**live, "executed_at": "2026-10-20T10:00:00+08:00"})
    eid = journal.list_episodes()["items"][0]["episode_id"]
    preview = ExecutionMatchService(store, journal=journal).preview(eid, "manual-1", ["sale"])
    assert preview["can_confirm"] and preview["requires_time_difference_confirmation"]


def test_old_preview_cannot_claim_a_fill_after_another_execution_claims_it():
    store, _, owner, eid, live = setup_link()
    store.collection("trade_executions").documents.append({**live, "execution_id": "manual-2"})
    old = owner.preview(eid, "manual-2", ["s"])
    confirm(owner, eid, ["s"])
    with pytest.raises(ExecutionMatchError, match="预览冲突"):
        owner.append_match(eid, body(old))
    assert owner.get_match("manual-2")["state"] == "unmatched"
    assert len(store.collection(COLLECTION).documents) == 1


def test_stale_link_retains_its_claim_until_explicit_unlink_using_current_live_digest():
    store, _, owner, eid, live = setup_link()
    confirm(owner, eid, ["s"])
    old = owner.get_match("manual-1")
    live["source"] = "user_corrected"
    assert owner.get_match("manual-1")["state"] == "stale"
    store.collection("trade_executions").documents.append({**live, "execution_id": "manual-2"})
    assert not owner.preview(eid, "manual-2", ["s"])["can_confirm"]
    unlink = {"execution_id": "manual-1", "action": "unlink", "expected_revision": 1,
        "live_digest": old["live_digest"], "reason": "原身份待纠正，明确解除"}
    with pytest.raises(ExecutionMatchError, match="内容已变化"):
        owner.append_match(eid, unlink)
    unlink["live_digest"] = owner.get_match("manual-1")["live_digest"]
    owner.append_match(eid, unlink)
    assert owner.preview(eid, "manual-2", ["s"])["can_confirm"]


def test_known_account_conflict_cannot_be_overridden_by_manual_checkboxes():
    store, _, owner, eid, live = setup_link()
    live["account_alias"] = "different-account"
    preview = owner.preview(eid, "manual-1", ["s"])
    assert not preview["can_confirm"]
    with pytest.raises(ExecutionMatchError, match="账户别名不一致"):
        owner.append_match(eid, body(preview))
    assert store.collection(COLLECTION).documents == []
