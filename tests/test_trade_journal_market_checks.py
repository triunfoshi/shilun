"""Stored market evidence is separated from recalled intent and live accounts."""
from copy import deepcopy
from datetime import datetime

import pytest

from shilun.trade_journal.market_checks import JournalMarketCheckService
from shilun.trade_journal.service import TZ, TradeJournalError
from test_trade_journal import service, import_ready, bundle, fill


def setup_case(*, expectation=None, verification="2026-10-20T15:30:00+08:00", extra_buy=False):
    store, journal = service()
    rows = [fill("buy", "2026-10-19", "buy", 100, 10, 100)]
    if extra_buy:
        rows.append(fill("add", "2026-10-20", "buy", 100, 11, 200))
    import_ready(journal, bundle(rows))
    episode = journal.list_episodes()["items"][0]["episode_id"]
    decision = journal.append_decision(episode, {"action": "buy", "reason": "等次日收盘验证",
        "observed_at": "2026-10-19T10:00:00+08:00", "evidence_class": "retrospective_recall",
        "fill_ids": [f["fill_id"] for f in rows], "verification_at": verification,
        "expectation": expectation if expectation is not None else {"metric": "close_return_pct", "operator": ">=", "target": 2,
            "baseline": "entry_price", "target_date": "2026-10-20"}})
    for day, close in [("2026-10-19", 10), ("2026-10-20", 10.5)]:
        store.collection("market_daily_bars").documents.append({"ticker": "000636.SZ", "date": day, "close": close})
        store.collection("trade_calendar").documents.append({"exchange": "SSE", "cal_date": day.replace("-", ""), "is_open": 1})
    owner = JournalMarketCheckService(store, journal=journal)
    return store, journal, owner, episode, decision["decision_id"]


def test_preview_reads_frozen_condition_and_never_writes():
    store, _, owner, episode, decision = setup_case()
    writes = deepcopy(store.writes)
    result = owner.preview(episode, decision)
    assert result["status"] == "supported"
    assert result["actual"]["value"] == pytest.approx(5)
    assert result["baseline"]["fill_id"] == "buy"
    assert result["evidence_class"] == "retrospective_recall"
    assert not result["ex_ante_verifiable"]
    assert not result["point_in_time_market_data_verified"]
    assert store.writes == writes


def test_save_archives_source_and_original_revision_without_account_writes():
    store, journal, owner, episode, decision = setup_case()
    preview = owner.preview(episode, decision)
    before = len(store.writes)
    result = owner.save(episode, decision, {**preview["preview_request"], "preview_digest": preview["preview_digest"]})
    assert result["saved"] and result["saved_status"] == "supported"
    assert result["check"]["decision_revision"] == 1
    assert not result["check"]["plan_was_ex_ante"]
    assert not result["check"]["actual_provenance"]["market_data_verified"]
    original = journal.get_source(result["source_id"], include_content=True)
    assert original["source_type"] == "stored_market_data_snapshot"
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes[before:])


def test_cas_rejects_source_change_before_any_archive_write():
    store, _, owner, episode, decision = setup_case()
    preview = owner.preview(episode, decision)
    store.collection("market_daily_bars").documents[-1]["close"] = 9
    before = deepcopy(store.writes)
    with pytest.raises(TradeJournalError, match="预览已变化"):
        owner.save(episode, decision, {**preview["preview_request"], "preview_digest": preview["preview_digest"]})
    # Lock maintenance is allowed; no source/check/batch may be committed.
    assert all(name == "trade_journal_write_locks" for name, _, _ in store.writes[len(before):])


def test_missing_or_ambiguous_baseline_stays_unknown():
    _, _, owner, episode, decision = setup_case(extra_buy=True)
    result = owner.preview(episode, decision)
    assert result["status"] == "unknown" and "不唯一" in result["reasons"][-1]
    _, _, owner, episode, decision = setup_case(expectation={"metric": "close_return_pct", "operator": ">=", "target": 2,
        "target_date": "2026-10-20"})
    assert owner.preview(episode, decision)["status"] == "unknown"


def test_specific_fill_baseline_and_previous_close_are_explicit():
    _, _, owner, episode, decision = setup_case(extra_buy=True, expectation={"metric": "close_return_pct", "operator": ">=", "target": 2,
        "baseline": "entry_price", "baseline_fill_id": "buy", "target_date": "2026-10-20"})
    assert owner.preview(episode, decision)["status"] == "supported"
    _, _, owner, episode, decision = setup_case(expectation={"metric": "close_return_pct", "operator": ">=", "target": 6,
        "baseline": "previous_close", "target_date": "2026-10-20"})
    result = owner.preview(episode, decision)
    assert result["status"] == "refuted" and result["baseline"]["date"] == "2026-10-19"


def test_missing_data_and_not_settled_are_not_failures():
    store, journal, owner, episode, decision = setup_case()
    store.collection("market_daily_bars").documents.pop()
    assert owner.preview(episode, decision)["status"] == "unknown"
    journal.now_factory = lambda: datetime(2026, 10, 20, 15, 20, tzinfo=TZ)
    owner.now_factory = journal.now_factory
    result = owner.preview(episode, decision)
    assert result["status"] == "unknown"


def test_intraday_deadline_cannot_be_proven_with_daily_close():
    _, _, owner, episode, decision = setup_case(verification="2026-10-20T10:00:00+08:00")
    result = owner.preview(episode, decision)
    assert result["status"] == "unknown" and "盘中" in result["reasons"][-1]


def test_rejects_future_asof_condition_override_and_missing_save_cas():
    _, _, owner, episode, decision = setup_case()
    with pytest.raises(TradeJournalError, match="晚于当前"):
        owner.preview(episode, decision, {"as_of": "2027-01-01T00:00:00+08:00"})
    with pytest.raises(TradeJournalError, match="临时改写"):
        owner.preview(episode, decision, {"target": -100})
    with pytest.raises(TradeJournalError, match="先预览"):
        owner.save(episode, decision, {"decision_revision": 1})


def test_offset_requires_complete_calendar_and_handles_weekend():
    store, _, owner, episode, decision = setup_case(expectation={"metric": "close_price", "operator": ">", "target": 10,
        "trading_day_offset": 1})
    assert owner.preview(episode, decision)["target_date"] == "2026-10-20"
    store.collection("trade_calendar").documents = []
    result = owner.preview(episode, decision)
    assert result["status"] == "unknown" and "日历缺失" in result["reasons"][-1]


def test_unknown_check_can_be_saved_without_claiming_machine_market_proof():
    _, _, owner, episode, decision = setup_case(expectation="期待反弹，未设阈值")
    preview = owner.preview(episode, decision)
    result = owner.save(episode, decision, {**preview["preview_request"], "preview_digest": preview["preview_digest"]})
    assert result["saved_status"] == "unknown" and not result["check"]["machine_verified"]


def test_exact_percentage_boundary_is_not_a_float_false_negative():
    store, _, owner, episode, decision = setup_case()
    store.collection("market_daily_bars").documents[-1]["close"] = 10.2
    result = owner.preview(episode, decision)
    assert result["actual"]["value"] == 2 and result["status"] == "supported"


def test_future_baseline_revision_and_corporate_action_ambiguity_stay_unknown():
    store, _, owner, episode, decision = setup_case(expectation={"metric": "close_return_pct", "operator": ">=", "target": 2,
        "baseline": "previous_close", "target_date": "2026-10-20"})
    store.collection("market_daily_bars").documents[0]["updated_at"] = "2027-01-01T00:00:00+08:00"
    assert owner.preview(episode, decision)["status"] == "unknown"
    store.collection("market_daily_bars").documents[0].pop("updated_at")
    store.collection("market_daily_bars").documents[-1]["pre_close"] = 5
    result = owner.preview(episode, decision)
    assert result["status"] == "unknown" and "除权" in result["reasons"][-1]


def test_preview_save_clock_movement_does_not_invalidate_unchanged_inputs():
    _, journal, owner, episode, decision = setup_case()
    preview = owner.preview(episode, decision)
    journal.now_factory = owner.now_factory = lambda: datetime(2026, 10, 26, 12, 1, tzinfo=TZ)
    result = owner.save(episode, decision, {**preview["preview_request"], "preview_digest": preview["preview_digest"]})
    assert result["saved_status"] == "supported"


@pytest.mark.parametrize("field,expected", [("open", "refuted"), ("high", "supported"), ("low", "refuted"), ("close", "supported")])
@pytest.mark.parametrize("kind", ["price", "return_pct"])
def test_daily_fields_are_explicit_and_never_claim_a_minute_touch(field, expected, kind):
    store, _, owner, episode, decision = setup_case(expectation={"metric": f"{field}_{kind}", "operator": ">=",
        "target": 2 if kind == "return_pct" else 10.2, "baseline": "entry_price", "target_date": "2026-10-20"})
    store.collection("market_daily_bars").documents[-1].update(open=9.8, high=10.6, low=9.7, close=10.5)
    result = owner.preview(episode, decision)
    assert result["status"] == result["market_evaluation"]["status"] == expected
    assert result["actual"]["price_field"] == field
    assert result["market_evaluation"]["touch_time"] is None
    assert result["market_evaluation"]["minute_check_status"] == "not_checked"
    assert len(result["price_observations"]) == 4
    assert result["execution_evaluation"]["status"] == "not_recorded"


def test_high_reached_but_full_actual_sale_missed_the_same_price_condition():
    store, journal, owner, episode, decision = setup_case(expectation={"metric": "high_return_pct", "operator": ">=", "target": 2,
        "baseline": "entry_price", "target_date": "2026-10-20"})
    import_ready(journal, bundle([fill("sale", "2026-10-20", "sell", 100, 10.1, 0)]))
    store.collection("market_daily_bars").documents[-1].update(open=10.0, high=10.6, low=9.9)
    result = owner.preview(episode, decision)
    assert result["market_evaluation"]["status"] == "supported"
    actual = result["execution_evaluation"]
    assert actual["status"] == "refuted"
    assert actual["evaluated_quantity"] == 100 and actual["target_met_quantity"] == 0
    assert actual["fills"][0]["sell_fill_id"] == "sale"
    assert actual["fills"][0]["price"] == 10.1
    assert actual["fills"][0]["quantity"] == 100
    assert not actual["execution_at_market_extreme_verified"]
    preview = result
    saved = owner.save(episode, decision, {**preview["preview_request"], "preview_digest": preview["preview_digest"]})
    original = journal.get_source(saved["source_id"], include_content=True)
    import json
    snapshot = json.loads(original["content"])
    assert snapshot["market_evaluation"]["status"] == "supported"
    assert snapshot["execution_evaluation"]["status"] == "refuted"
    assert snapshot["market_prices"]["high"] == 10.6
    assert len(snapshot["price_observations"]) == 4
    reloaded = journal.get_episode(episode)["expectation_checks"][-1]
    assert reloaded["evidence"][0]["market_evaluation"]["status"] == "supported"
    assert reloaded["evidence"][0]["execution_evaluation"]["status"] == "refuted"


def test_partial_sold_quantity_and_mixed_sale_prices_are_not_one_boolean():
    store, journal, owner, episode, decision = setup_case(expectation={"metric": "high_price", "operator": ">=", "target": 10.2,
        "target_date": "2026-10-20"})
    import_ready(journal, bundle([fill("sale1", "2026-10-20", "sell", 50, 10.5, 50)]))
    store.collection("market_daily_bars").documents[-1].update(high=10.6)
    result = owner.preview(episode, decision)
    assert result["execution_evaluation"]["status"] == "partial"
    assert result["execution_evaluation"]["evaluated_quantity"] == 50
    assert result["execution_evaluation"]["target_met_quantity"] == 50
    import_ready(journal, bundle([fill("sale2", "2026-10-20", "sell", 50, 10.1, 0, time="10:01:00")]))
    result = owner.preview(episode, decision)
    assert result["execution_evaluation"]["status"] == "partial"
    assert result["execution_evaluation"]["evaluated_quantity"] == 100
    assert result["execution_evaluation"]["target_met_quantity"] == 50


def test_addition_has_its_own_fill_date_anchor_and_does_not_reuse_first_entry():
    store, _, owner, episode, decision = setup_case(extra_buy=True, verification=None,
        expectation={"metric": "close_return_pct", "operator": ">=", "target": 2,
            "baseline": "entry_price", "baseline_fill_id": "add", "scope_fill_id": "add", "trading_day_offset": 1})
    store.collection("trade_calendar").documents.append({"exchange": "SSE", "cal_date": "20261021", "is_open": 1})
    store.collection("market_daily_bars").documents.append({"ticker": "000636.SZ", "date": "2026-10-21", "close": 11.22})
    result = owner.preview(episode, decision)
    assert result["target_date"] == "2026-10-21"
    assert result["date_anchor"] == {"kind": "buy_fill", "date": "2026-10-20", "fill_id": "add"}
    assert result["baseline"]["fill_id"] == "add" and result["actual"]["value"] == 2
    assert result["expectation_scope"]["entry_fill_id"] == "add"


def test_multiple_entries_need_explicit_offset_anchor():
    _, _, owner, episode, decision = setup_case(extra_buy=True, verification=None,
        expectation={"metric": "close_price", "operator": ">=", "target": 10, "trading_day_offset": 1})
    result = owner.preview(episode, decision)
    assert result["status"] == "unknown" and result["target_date"] is None
    assert "多次买入" in result["reasons"][-1]


def test_scope_binding_can_be_added_without_rewriting_original_action_fill_links():
    store, journal, owner, episode, decision = setup_case(extra_buy=True)
    original = journal.get_decision_history(decision)["items"][0]
    saved = journal.append_decision(episode, {"decision_id": decision, "expected_revision": 1, "revision_reason": "后设参考",
        "fill_ids": [], "verification_at": None, "evidence_class": "historical_reconstruction",
        "expectation": {"metric": "close_price", "operator": ">=", "target": 10.2,
            "scope_fill_id": "buy", "baseline_fill_id": "buy", "target_date": "2026-10-20", "origin": "post_hoc_review"}})
    result = owner.preview(episode, decision)
    assert result["status"] == "supported"
    assert result["expectation_scope"]["binding"] == "expectation_scope"
    assert result["expectation_scope"]["entry_fill_id"] == "buy"
    assert result["evaluation_purpose"] == "post_hoc_reference"
    assert not result["interprets_original_expectation"]
    assert not result["market_evaluation"]["interprets_original_expectation"]
    assert not result["plan_was_ex_ante"]
    assert saved["record"]["fill_ids"] == []
    assert journal.get_decision_history(decision)["items"][0] == original


@pytest.mark.parametrize("scope,basis", [("missing", "missing"), ("sale", "sale"), ("buy", "add")])
def test_invalid_or_conflicting_scope_is_never_evaluated(scope, basis):
    _, _, owner, episode, decision = setup_case(extra_buy=True, expectation={"metric": "close_price", "operator": ">=", "target": 1,
        "scope_fill_id": scope, "baseline_fill_id": basis, "target_date": "2026-10-20"})
    result = owner.preview(episode, decision)
    assert result["status"] == result["execution_evaluation"]["status"] == "unknown"
    assert result["reason_code"] == "invalid_fill_scope"


def test_open_price_uses_open_field_and_accepts_explicit_open_window():
    store, _, owner, episode, decision = setup_case(verification="2026-10-20T09:30:00+08:00",
        expectation={"metric": "open_price", "operator": ">=", "target": 10.2, "target_date": "2026-10-20"})
    store.collection("market_daily_bars").documents[-1].update(open=10.3, high=10.5, low=10.0, close=10.1)
    result = owner.preview(episode, decision)
    assert result["status"] == "supported" and result["actual"]["value"] == 10.3
    assert result["price_observations"][-1]["status"] == "refuted"


def test_absent_expectation_and_unlinked_execution_are_unknown_not_failure():
    _, _, owner, episode, decision = setup_case(expectation={})
    result = owner.preview(episode, decision)
    assert result["status"] == result["market_evaluation"]["status"] == "unknown"
    assert result["reason_code"] == "no_expectation"
    assert result["execution_evaluation"]["status"] == "unknown"


def test_target_day_sale_of_a_same_day_add_is_not_t_plus_one_eligible():
    _, _, owner, episode, decision = setup_case(extra_buy=True, expectation={"metric": "close_price", "operator": ">=", "target": 10,
        "scope_fill_id": "add", "target_date": "2026-10-20"})
    result = owner.preview(episode, decision)
    assert result["market_evaluation"]["status"] == "supported"
    assert result["execution_evaluation"]["status"] == "unknown"
    assert "当天加仓" in result["execution_evaluation"]["reason"]
