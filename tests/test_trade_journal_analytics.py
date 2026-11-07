"""Quantity conservation, complete denominators and current-risk time boundaries."""
from copy import deepcopy
from datetime import datetime

import pytest

from shilun.trade_journal.allocations import TradeAllocationService, AllocationError, COLLECTION
from shilun.trade_journal.analytics import JournalAnalyticsService, PortfolioRiskService, PORTFOLIO_COLLECTION
from shilun.trade_journal.definitions import get_definition
from shilun.trade_journal.service import TradeJournalService, TradeJournalError
from test_trade_journal import FakeStore, fill, bundle, import_ready, decision_payload, TZ


def ref(code):
    item = get_definition(code)
    return {"id": item["id"], "rule_version": item["rule_version"]}


def setup(rows=None, financial=None):
    store = FakeStore()
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 27, 12, tzinfo=TZ))
    if rows is None:
        rows = [fill("buy", "2026-10-19", "buy", 100, 10, 100, fees=10),
                fill("sell", "2026-10-20", "sell", 100, 12, 0, fees=20)]
    import_ready(journal, bundle(rows, financial))
    return store, journal, TradeAllocationService(store, journal=journal), journal.list_episodes()["items"][0]["episode_id"]


def assigned(fill_id, mode, quantity, **changes):
    return {"fill_id": fill_id, "mode_ref": ref(mode), "quantity": quantity, **changes}


def two_modes():
    return [assigned("buy", "mode.ma5_trend", 60), assigned("buy", "mode.relay", 40),
            assigned("sell", "mode.ma5_trend", 60), assigned("sell", "mode.relay", 40)]


def test_quantity_cost_fees_and_profit_are_partitioned_once_from_existing_fifo():
    store, journal, svc, eid = setup()
    before = deepcopy(journal.get_episode(eid)["episode"]["matches"])
    result = svc.append_allocations(eid, {"reason": "逐笔确认60/40两种意图", "assignments": two_modes()})
    rows = {row["mode_ref"]["id"]: row for row in result["allocation"]["mode_results"]}
    assert rows["mode.ma5_trend"]["net_pnl"] == 102
    assert rows["mode.relay"]["net_pnl"] == 68
    assert rows["mode.ma5_trend"]["fees"] == 18
    assert rows["mode.relay"]["fees"] == 12
    assert sum(row["buy_cost"] for row in rows.values()) == 1010
    assert result["allocation"]["reconciliation"] == {"account_fifo_net_pnl": 170, "partition_net_pnl": 170, "balanced": True}
    assert journal.get_episode(eid)["episode"]["matches"] == before
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes)


def test_allocations_are_full_append_versions_and_unknown_references_do_not_default():
    _, _, svc, eid = setup()
    first = svc.append_allocations(eid, {"reason": "初版", "assignments": two_modes()})
    with pytest.raises(AllocationError, match="版本"):
        svc.append_allocations(eid, {"reason": "漏了CAS", "assignments": two_modes()})
    second = svc.append_allocations(eid, {"expected_revision": 1, "reason": "清空分配保留未知", "assignments": []})
    assert first["record"]["revision"] == 1 and second["revision"] == 2
    assert svc.history(eid)["total"] == 2 and len(svc.get_allocations(eid, 1)["assignments"]) == 4
    assert svc.calculate(eid)["mode_results"] == []
    bad = assigned("buy", "mode.relay", 100)
    bad["mode_ref"]["rule_version"] = "invented-version"
    with pytest.raises(AllocationError):
        svc.append_allocations(eid, {"expected_revision": 2, "reason": "错版本", "assignments": [bad]})
    bad["mode_ref"] = {"id": "mode.no_such_mode", "rule_version": "v1"}
    with pytest.raises(AllocationError):
        svc.append_allocations(eid, {"expected_revision": 2, "reason": "错模式", "assignments": [bad]})


def test_quantity_cannot_exceed_fill_or_borrow_another_time_or_modes_inventory():
    _, _, svc, eid = setup()
    with pytest.raises(AllocationError, match="超过真实"):
        svc.append_allocations(eid, {"reason": "超额", "assignments": [assigned("buy", "mode.relay", 101)]})
    rows = two_modes(); rows[-2]["quantity"] = 70; rows[-1]["quantity"] = 30
    with pytest.raises(AllocationError, match="模式库存"):
        svc.append_allocations(eid, {"reason": "挪用另一个模式的股数", "assignments": rows})
    rows = two_modes(); rows[-1]["buy_lot_id"] = "future-buy"
    with pytest.raises(AllocationError, match="FIFO"):
        svc.append_allocations(eid, {"reason": "使用后来买入", "assignments": rows})
    with pytest.raises(AllocationError, match="逐成交"):
        svc.append_allocations(eid, {"reason": "不接受整轮比例", "allocations": {"mode.relay": 0.5}})


def test_multiple_fifo_lots_require_explicit_cost_lot_splits_and_unknowns_are_not_zero():
    rows = [fill("b1", "2026-10-19", "buy", 100, 10, 100), fill("b2", "2026-10-20", "buy", 100, 20, 200),
        fill("s1", "2026-10-21", "sell", 150, 30, 50)]
    _, _, svc, eid = setup(rows)
    assignments = [assigned("b1", "mode.relay", 100), assigned("b2", "mode.relay", 100), assigned("s1", "mode.relay", 150)]
    result = svc.append_allocations(eid, {"reason": "只知道卖出模式，成本批未明", "assignments": assignments})
    assert result["allocation"]["mode_results"][0]["net_pnl"] is None
    assert result["allocation"]["sell_attributions"][0]["account_slice_pnl"] is None
    assert result["allocation"]["reconciliation"]["balanced"]
    assignments[-1:] = [assigned("s1", "mode.relay", 100, buy_lot_id="b1"), assigned("s1", "mode.relay", 50, buy_lot_id="b2")]
    result = svc.append_allocations(eid, {"expected_revision": 1, "reason": "逐实际FIFO成本批确认", "assignments": assignments})
    assert result["allocation"]["mode_results"][0]["net_pnl"] == pytest.approx(2497.5)
    assert result["allocation"]["reconciliation"]["balanced"]


def test_non_numeric_labels_and_only_selling_label_never_invent_independent_mode_profit():
    _, journal, svc, eid = setup()
    journal.append_decision(eid, decision_payload(mode_id="mode.ma5_trend", mode="趋势原意"))
    result = svc.append_allocations(eid, {"reason": "只知道标签和卖出意图", "labels": ["mode.ma5_trend", "mode.relay"],
        "assignments": [assigned("sell", "mode.ma5_trend", 100)]})
    assert result["allocation"]["mode_results"][0]["net_pnl"] is None
    assert result["allocation"]["sell_attributions"][0]["account_slice_pnl"] == 170
    report = JournalAnalyticsService(svc.store, journal=journal).report(group_by=["mode"])
    assert report["overall"]["net_pnl"] == 170 and report["groups"][0]["dimensions"]["mode"] == "mode.unallocated"
    assert report["groups"][0]["members"][0]["mode_net_pnl"] is None
    assert report["groups"][0]["members"][0]["decision_labels"]["modes"] == ["mode.ma5_trend"]


def test_report_keeps_all_closed_known_ipo_etf_exclusions_and_drilldown():
    rows = [fill("a-buy", "2026-10-19", "buy", 100, 10, 100), fill("a-sell", "2026-10-20", "sell", 100, 12, 0),
        fill("etf-buy", "2026-10-19", "buy", 100, 2, 100, ticker="510300.SH"),
        fill("etf-sell", "2026-10-20", "sell", 100, 1.5, 0, ticker="510300.SH"),
        fill("unknown-sale", "2026-10-20", "sell", 100, 20, 0, ticker="002008.SZ"),
        fill("open-buy", "2026-10-21", "buy", 100, 5, 100, ticker="600001.SH"),
        fill("ipo-sale", "2026-07-24", "sell", 100, 8, 0, ticker="688825.SH")]
    financial = [{"financial_event_id": "pay", "kind": "ipo_payment", "ticker": "688825.SH", "date": "2026-07-16", "quantity": 100, "net_cash": -500,
        "source_ref": {"source_id": "raw", "row": 8}},
        {"financial_event_id": "credit", "kind": "stock_credit", "ticker": "688825.SH", "date": "2026-07-17", "settlement_date": "2026-07-24", "quantity": 100, "net_cash": 0, "quantity_after": 100,
        "source_ref": {"source_id": "raw", "row": 9}}]
    store, journal, _, _ = setup(rows, financial)
    report = JournalAnalyticsService(store, journal=journal).report(group_by=["security_type"])
    assert report["overall"]["n"] == 3 and report["overall"]["net_pnl"] == 445
    assert report["denominator"]["selected_episode_n"] == 5 and report["denominator"]["excluded_n"] == 2
    assert report["denominator"]["open_n"] == 1 and report["denominator"]["closed_unknown_n"] == 1
    groups = {row["dimensions"]["security_type"]: row for row in report["groups"]}
    assert groups["ipo"]["statistics"]["net_pnl"] == 299
    assert groups["etf"]["statistics"]["net_pnl"] == -52
    assert report["historical_portfolio"]["max_drawdown"] is None
    assert any(row["ticker"] == "002008.SZ" for row in report["excluded"])
    found = JournalAnalyticsService(store, journal=journal).group(groups["ipo"]["group_id"], group_by=["security_type"])
    assert found["statistics"]["n"] == 1 and found["members"][0]["ticker"] == "688825.SH"


def test_partial_mode_slices_reconcile_without_duplicating_episode_win_rate_or_cost():
    store, journal, svc, eid = setup()
    rows = [assigned("buy", "mode.relay", 40), assigned("sell", "mode.relay", 40)]
    svc.append_allocations(eid, {"reason": "明确40股，其余未知", "assignments": rows})
    journal.append_decision(eid, decision_payload(environment="弱势", role="容量核心"))
    # Missing execution evidence stays unknown; old entry patterns are not used.
    report = JournalAnalyticsService(store, journal=journal).report(group_by=["mode", "month", "environment", "role", "execution_difference"])
    assert report["overall"]["n"] == 1
    assert sum(group["statistics"]["net_pnl"] for group in report["groups"]) == 170
    assert sum(group["statistics"]["buy_cost"] for group in report["groups"]) == 1010
    assert sum(group["statistics"]["n"] for group in report["groups"]) == 2
    assert all(group["dimensions"]["environment"] == "弱势" for group in report["groups"])
    assert all(group["dimensions"]["role"] == "容量核心" for group in report["groups"])
    assert all(group["dimensions"]["execution_difference"] == "unknown" for group in report["groups"])
    assert all(group["statistics"]["profit_factor"] is None for group in report["groups"])


def test_largest_trade_sensitivity_removes_the_actual_outlier_and_keeps_members():
    rows = []
    for i, sell_price in enumerate((11, 9, 20)):
        ticker = f'60000{i}.SH'
        rows += [fill(f'b{i}', "2026-10-19", "buy", 100, 10, 100, ticker=ticker),
                 fill(f's{i}', "2026-10-20", "sell", 100, sell_price, 0, ticker=ticker)]
    store, journal, _, _ = setup(rows)
    report = JournalAnalyticsService(store, journal=journal).report(group_by=[])
    assert report["overall"]["n"] == 3 and report["overall"]["net_pnl"] == 994
    assert report["overall"]["median_pnl"] == 98
    assert report["overall"]["profit_factor"] == pytest.approx(1096/102)
    sensitive = report["overall"]["largest_trade_sensitivity"]["drop_largest_absolute"]
    assert sensitive["removed_pnl"] == 998 and sensitive["remaining"]["net_pnl"] == -4
    assert sensitive["remaining"]["n"] == 2 and len(report["members"]) == 3


def risk_facts(store):
    store.collection("account_risk_profiles").documents.append({"profile_id": "default", "total_assets": 100000,
        "available_cash": 40000, "max_loss_per_trade": 2000, "source": "user_configured",
        "updated_at": "2026-10-27T09:00:00+08:00"})
    store.collection("holdings").documents += [
        {"ticker": "000636.SZ", "name": "风华高科", "status": "active", "quantity": 1700,
            "available_quantity": 1000, "cost_basis": 38.08, "stop_loss_1": 36, "sector_name": "元件",
            "updated_at": "2026-10-27T10:00:00+08:00", "position_revision": 4},
        {"ticker": "002242.SZ", "name": "九阳股份", "status": "closed", "quantity": 0}]
    store.collection("trading_realtime_quotes").documents.append({"ticker": "000636.SZ", "current": 37,
        "quote_at": "2026-10-27T11:59:00+08:00", "source": "stored_quote_owner"})


def test_current_portfolio_reuses_owner_risk_without_defaults_historical_nav_or_writes():
    store = FakeStore(); journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 27, 12, tzinfo=TZ))
    svc = PortfolioRiskService(store, journal=journal)
    empty = svc.context()
    assert empty["profile"] is None and empty["configured_total_assets"] is None
    assert empty["historical"]["max_drawdown"] is None and store.writes == []
    risk_facts(store)
    context = svc.context()
    assert context["holding_n"] == 1 and context["market_value"] == 62900
    assert context["current_verified_net_asset_value"] is None
    assert context["holdings"][0]["dynamic_risk"]["max_loss_per_trade"] == 2000
    assert context["holdings"][0]["mode"] == "unknown"
    assert context["holdings"][0]["weight_on_configured_assets"] == 0.629
    assert store.writes == []


def test_user_t_add_scenario_preserves_known_before_after_and_unknown_fees_t1_without_order():
    store = FakeStore(); risk_facts(store)
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 27, 12, tzinfo=TZ))
    svc = PortfolioRiskService(store, journal=journal)
    before = deepcopy(store.collection("holdings").documents)
    context = svc.context(scenario={"action": "intraday_t", "reference_tickers": ["002242.SZ"],
        "user_limits": {"max_proposed_add_cash": 15000}, "legs": [
            {"ticker": "000636.SZ", "side": "buy", "quantity": 300, "price": 37},
            {"ticker": "000636.SZ", "side": "sell", "quantity": 1200, "price": 37.2, "fees": 20}]})
    scenario = context["scenario"]
    assert scenario["legs"][0]["before_quantity"] == 1700 and scenario["legs"][0]["after_quantity"] == 2000
    assert scenario["legs"][1]["after_quantity"] == 800 and scenario["legs"][1]["flags"]
    assert scenario["cash_delta"] is None and scenario["after_available_cash"] is None
    assert scenario["user_limits"]["max_proposed_add_cash"] == 15000 and not scenario["executed"]
    assert store.collection("holdings").documents == before and store.writes == []
    with pytest.raises(TradeJournalError, match="超过已知"):
        svc.context(scenario={"legs": [{"ticker": "000636.SZ", "side": "sell", "quantity": 1800, "price": 37}]})


def test_current_snapshot_cannot_be_backdated_or_overwritten_and_does_not_write_production():
    store = FakeStore(); risk_facts(store)
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 27, 12, tzinfo=TZ))
    svc = PortfolioRiskService(store, journal=journal)
    frozen = svc.capture_snapshot({"snapshot_id": "risk-one", "historical_at": "2026-10-23T10:00:00+08:00",
        "captured_at": "2026-10-23T10:00:00+08:00", "reason": "当前组合事实"})["record"]
    assert frozen["captured_at"].startswith("2026-10-27")
    assert frozen["content"]["historical"]["equity_curve"] is None
    assert "content" not in svc.list_snapshots()["items"][0]
    assert "content" not in svc.get_snapshot("risk-one")
    with pytest.raises(TradeJournalError, match="冲突"):
        svc.capture_snapshot({"snapshot_id": "risk-one", "reason": "试图覆盖原件"})
    assert svc.get_snapshot("risk-one", include_content=True)["reason"] == "当前组合事实"
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes)
    store.collection(PORTFOLIO_COLLECTION).documents[0]["content"]["available_cash"] = 999999
    with pytest.raises(TradeJournalError, match="校验"):
        svc.get_snapshot("risk-one", include_content=True)


def test_future_quote_and_missing_holding_inputs_remain_unknown_and_sizing_reuses_original_rules():
    store = FakeStore(); risk_facts(store)
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 27, 12, tzinfo=TZ))
    svc = PortfolioRiskService(store, journal=journal)
    store.collection("trading_realtime_quotes").documents[0]["quote_at"] = "2026-10-28T10:00:00+08:00"
    missing = svc.context()
    assert missing["market_value"] is None and missing["holdings"][0]["current_price"] is None
    assert missing["holdings"][0]["dynamic_risk"] is None
    scenario = {"entry_sizing": {"plan_id": "journal-review-only", "ticker": "000636.SZ", "scenario_id": "E3",
        "market_permission": "hold", "entry_price": 37, "hard_stop": 36, "unit_rule": {"buy_unit": 100}}}
    sized = svc.context(scenario=scenario)
    assert sized["sizing_preview"]["sizing_type"] == "entry"
    assert sized["owners"]["sizing_calculator"] == "shilun.trading.sizing.calculate_entry_sizing"
    assert store.writes == []


def test_open_numeric_mode_inventory_is_in_that_groups_exclusion_denominator():
    store, journal, svc, eid = setup([fill("buy", "2026-10-19", "buy", 100, 10, 100)])
    svc.append_allocations(eid, {"reason": "已知未闭模式库存", "assignments": [assigned("buy", "mode.relay", 100)]})
    report = JournalAnalyticsService(store, journal=journal).report(group_by=["mode"])
    group = report["groups"][0]
    assert group["dimensions"]["mode"].startswith("mode.relay@")
    assert group["statistics"]["net_pnl"] is None and group["statistics"]["n"] == 0
    assert group["excluded_n"] == 1 and group["excluded"][0]["episode_id"] == eid
    assert len(group["excluded"][0]["exclusion_reasons"]) == 1


def test_custom_mode_exact_version_is_frozen_in_quantity_allocation_and_retired_remains_readable(monkeypatch):
    from shilun.trade_journal.mode_cards import ModeCardService
    store, _, svc, eid = setup()
    cards = ModeCardService(store)
    collection = store.collection("trade_journal_mode_cards")
    monkeypatch.setattr(collection, "insert_one", lambda record: collection.update_one({"mode_id": record["mode_id"], "revision": record["revision"]}, {"$setOnInsert": record}, upsert=True), raising=False)
    first = cards.save_card({"mode_id": "mode.user.test", "name": "原始意图", "status": "trial", "purpose": "保存原文"})["record"]
    rows = [{"fill_id": fid, "mode_ref": {"id": "mode.user.test", "rule_version": first["rule_version"]}, "quantity": 100} for fid in ("buy", "sell")]
    frozen = svc.append_allocations(eid, {"reason": "确切自定义模式", "assignments": rows})
    cards.save_card({"mode_id": "mode.user.test", "expected_revision": 1, "revision_reason": "退役", "status": "retired", "purpose": "新原文"})
    snapshot = svc.get_allocations(eid)["assignments"][0]["definition_snapshot"]
    assert snapshot["parameters"]["card_content"]["purpose"] == "保存原文"
    assert frozen["allocation"]["mode_results"][0]["net_pnl"] == 170
    assert svc.calculate(eid)["mode_results"][0]["net_pnl"] == 170


def test_portfolio_snapshot_reference_checks_exact_original_and_pre_plan_time():
    store = FakeStore(); risk_facts(store)
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 27, 12, tzinfo=TZ))
    svc = PortfolioRiskService(store, journal=journal)
    result = svc.capture_snapshot({"reason": "计划前风险事实"})
    ref_value = result["snapshot_ref"]
    assert svc.resolve_reference(ref_value, before_at="2026-10-27T12:01:00+08:00")["content"]["holding_n"] == 1
    with pytest.raises(TradeJournalError, match="晚于"):
        svc.resolve_reference(ref_value, before_at="2026-10-27T11:59:00+08:00")
    with pytest.raises(TradeJournalError, match="不一致"):
        svc.resolve_reference({**ref_value, "content_hash": "fake"})


def test_partial_entry_ambiguity_cannot_pick_the_profitable_sell_by_payload_order():
    rows = [fill("buy", "2026-10-19", "buy", 100, 10, 100),
        fill("s-good", "2026-10-20", "sell", 50, 12, 50),
        fill("s-bad", "2026-10-21", "sell", 50, 9, 0)]
    _, _, svc, eid = setup(rows)
    assignments = [assigned("buy", "mode.relay", 60), assigned("s-good", "mode.relay", 50), assigned("s-bad", "mode.relay", 50)]
    first = svc.append_allocations(eid, {"reason": "未明确剩余40股模式，不能挑盈利卖出优先归属", "assignments": assignments})
    second = svc.append_allocations(eid, {"expected_revision": 1, "reason": "相反输入顺序", "assignments": list(reversed(assignments))})
    for result in (first, second):
        assert all(row["net_pnl"] is None for row in result["allocation"]["sell_attributions"])
        assert result["allocation"]["mode_results"][0]["net_pnl"] is None
        assert result["allocation"]["mode_results"][0]["known_pnl_component"] == 0
        assert result["allocation"]["reconciliation"]["balanced"]
