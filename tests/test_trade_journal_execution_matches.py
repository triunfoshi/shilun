"""Identity linking never reapplies an imported or manually written execution."""
from copy import deepcopy

import pytest

from shilun.trade_journal.execution_matches import ExecutionMatchError, ExecutionMatchService, COLLECTION
from test_trade_journal import service, bundle, fill, import_ready


def setup_link(*, partial=False, fees=1):
    store, journal = service()
    rows = [fill("b", "2026-10-19", "buy", 200 if partial else 100, 10, 200 if partial else 100)]
    rows += ([fill("s1", "2026-10-20", "sell", 100, 12, 100, fees=fees),
              fill("s2", "2026-10-20", "sell", 100, 12, 0, fees=fees, time="10:01:00")]
             if partial else [fill("s", "2026-10-20", "sell", 100, 12, 0, fees=fees)])
    import_ready(journal, bundle(rows))
    live = {"execution_id": "manual-1", "ticker": "000636.SZ", "side": "sell", "quantity": 200 if partial else 100,
            "price": 12, "fees": fees * (2 if partial else 1) if fees is not None else None,
            "cash_delta": 2400 - fees * 2 if partial and fees is not None else 1200 - fees if fees is not None else None,
            "executed_at": "2026-10-20T10:00:00+08:00", "source": "user_confirmed"}
    store.collection("trade_executions").documents.append(live)
    episode = journal.list_episodes()["items"][0]["episode_id"]
    owner = ExecutionMatchService(store, journal=journal)
    return store, journal, owner, episode, live


def confirm(owner, episode, ids, **changes):
    preview = owner.preview(episode, "manual-1", ids)
    body = {"execution_id": "manual-1", "action": "confirm", "fill_ids": ids,
            "expected_revision": preview["expected_revision"], "preview_digest": preview["preview_digest"],
            "reason": "已逐行核对原件与人工回写，是同一成交", "identity_confirmation": True,
            "account_confirmation": True, "time_difference_confirmation": True, **changes}
    return owner.append_match(episode, body)


def test_candidate_preview_are_read_only_and_link_leaves_cash_holdings_and_pnl_unchanged():
    store, journal, owner, episode, live = setup_link()
    before = deepcopy(live), journal.get_episode(episode)["episode"]["closed_pnl"]
    writes = len(store.writes)
    assert owner.candidates(episode)["items"][0]["state"] == "unmatched"
    assert owner.preview(episode, "manual-1", ["s"])["can_confirm"]
    assert len(store.writes) == writes
    result = confirm(owner, episode, ["s"])
    assert result["record"]["state"] == "confirmed"
    assert result["record"]["changes_production_account"] is False
    assert owner.get_match("manual-1")["history"][0] == result["record"]
    assert (live, journal.get_episode(episode)["episode"]["closed_pnl"]) == before
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes[writes:])


def test_real_partial_fills_can_match_one_aggregate_manual_execution_only_with_explicit_time_ack():
    _, _, owner, episode, _ = setup_link(partial=True)
    preview = owner.preview(episode, "manual-1", ["s1", "s2"])
    assert preview["can_confirm"] and preview["requires_time_difference_confirmation"]
    with pytest.raises(ExecutionMatchError, match="时刻或分笔"):
        confirm(owner, episode, ["s1", "s2"], time_difference_confirmation=False)
    assert confirm(owner, episode, ["s2", "s1"])["record"]["fill_ids"] == ["s1", "s2"]


@pytest.mark.parametrize("field,value", [("quantity", 200), ("price", 13), ("fees", 2), ("cash_delta", 1198),
                                         ("side", "buy"), ("ticker", "002242.SZ"), ("account_alias", "other")])
def test_disagreeing_facts_do_not_become_trusted_matches(field, value):
    _, _, owner, episode, live = setup_link()
    live[field] = value
    assert not owner.preview(episode, "manual-1", ["s"])["can_confirm"]
    with pytest.raises(ExecutionMatchError):
        confirm(owner, episode, ["s"])


def test_unknown_fees_and_cash_are_not_filled_and_do_not_upgrade_qualification():
    _, journal, owner, episode, _ = setup_link(fees=None)
    assert not journal.get_episode(episode)["qualification"]["accounting"]["eligible"]
    result = confirm(owner, episode, ["s"])
    assert len(result["record"]["warnings"]) == 2
    assert result["record"]["fills"][0]["fees"] is None
    assert not journal.get_episode(episode)["qualification"]["accounting"]["eligible"]


def test_no_automatic_identity_from_same_price_and_quantity_and_account_requires_confirmation():
    _, _, owner, episode, _ = setup_link()
    for key in ("identity_confirmation", "account_confirmation"):
        with pytest.raises(ExecutionMatchError, match="明确确认"):
            confirm(owner, episode, ["s"], **{key: False})
    assert owner.get_match("manual-1")["revision"] == 0


def test_live_change_invalidates_preview_and_marks_existing_link_stale():
    _, _, owner, episode, live = setup_link()
    preview = owner.preview(episode, "manual-1", ["s"])
    live["source"] = "user_corrected"
    with pytest.raises(ExecutionMatchError, match="预览冲突"):
        confirm(owner, episode, ["s"], preview_digest=preview["preview_digest"])
    confirm(owner, episode, ["s"])
    live["fees"] = 2
    assert owner.get_match("manual-1")["state"] == "stale"


def test_one_archived_fill_cannot_link_two_manual_executions_until_explicit_unlink():
    store, _, owner, episode, live = setup_link()
    confirm(owner, episode, ["s"])
    store.collection("trade_executions").documents.append({**live, "execution_id": "manual-2"})
    duplicate = owner.preview(episode, "manual-2", ["s"])
    assert not duplicate["can_confirm"] and any("另一份" in v for v in duplicate["conflicts"])
    current = owner.get_match("manual-1")
    owner.append_match(episode, {"execution_id": "manual-1", "action": "unlink", "expected_revision": 1,
                               "live_digest": current["live_digest"], "reason": "原人工记录选择错误，保留历史解除"})
    assert owner.get_match("manual-1")["state"] == "unlinked"
    assert len(owner.get_match("manual-1")["history"]) == 2
    assert owner.preview(episode, "manual-2", ["s"])["can_confirm"]


def test_cas_invalid_fill_scope_and_immutable_history():
    store, _, owner, episode, _ = setup_link()
    first = confirm(owner, episode, ["s"])
    with pytest.raises(ExecutionMatchError, match="版本已变化"):
        confirm(owner, episode, ["s"], expected_revision=0)
    for ids in (["s", "s"], ["missing"], [], "s"):
        with pytest.raises(ExecutionMatchError):
            owner.preview(episode, "manual-1", ids)
    store.collection(COLLECTION).documents[0]["reason"] = "被篡改"
    with pytest.raises(ExecutionMatchError, match="校验失败"):
        owner.get_match("manual-1")
