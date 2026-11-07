"""Journal accounting and evidence boundaries, without Mongo or broker writes."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from zoneinfo import ZoneInfo

import pytest

from shilun.trade_journal.service import COLLECTIONS, TradeJournalError, TradeJournalService


TZ = ZoneInfo("Asia/Shanghai")


class FakeCollection:
    def __init__(self, name, store):
        self.name, self.store, self.documents = name, store, []

    @staticmethod
    def match(document, query):
        return all(document.get(k) == v for k, v in query.items())

    def find(self, query, projection=None):
        self.store.reads.append((self.name, deepcopy(query), deepcopy(projection)))
        return [{k: deepcopy(v) for k, v in r.items() if not projection or projection.get(k, 1) != 0}
            for r in self.documents if self.match(r, query)]

    def find_one(self, query, projection=None):
        return next(iter(self.find(query, projection)), None)

    def update_one(self, query, changes, upsert=False):
        self.store.writes.append((self.name, deepcopy(query), deepcopy(changes)))
        old = next((r for r in self.documents if self.match(r, query)), None)
        if old is not None:
            old.update(deepcopy(changes.get("$set", {})))
        elif upsert:
            self.documents.append({**deepcopy(query), **deepcopy(changes.get("$setOnInsert", {})), **deepcopy(changes.get("$set", {}))})


class FakeStore:
    def __init__(self):
        self.collections, self.writes, self.reads = {}, [], []

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection(name, self))


def source(source_id="raw", content="broker evidence", **changes):
    return {"source_id": source_id, "filename": "source.json", "source_type": "broker_archive",
        "sha256": hashlib.sha256(content.encode()).hexdigest(), "content_encoding": "utf-8", "content": content, **changes}


def fill(fill_id, day, side, quantity, price, after, *, fees=1, ticker="000636.SZ", **changes):
    amount = quantity * price
    return {"fill_id": fill_id, "date": day, "time": "10:00:00", "ticker": ticker,
        "name": "风华高科" if ticker == "000636.SZ" else "参照股票", "side": side,
        "quantity": quantity, "price": price, "amount": amount, "fees": fees,
        "net_cash": ((-amount if side == "buy" else amount) - fees) if fees is not None else None,
        "quantity_after": after, "time_precision": "execution_time", "source_ref": {"source_id": "raw", "row": len(fill_id)}, **changes}


def bundle(rows, financial=None, sources=None):
    return {"account_alias": "primary", "sources": sources or [source()], "fills": rows, "financial_events": financial or []}


def service():
    store = FakeStore()
    return store, TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 26, 12, tzinfo=TZ))


def import_ready(svc, payload):
    preview = svc.preview_import(payload)
    assert preview["can_import"], preview
    return svc.import_batch(payload, preview["preview_digest"])


def test_preview_is_read_only_and_repeat_import_does_not_touch_live_account():
    store, svc = service()
    payload = bundle([fill("b", "2026-10-19", "buy", 100, 10, 100), fill("s", "2026-10-20", "sell", 100, 12, 0)])
    preview = svc.preview_import(payload)
    assert store.writes == [] and preview["counts"]["new_sources"] == 1
    first = svc.import_batch(payload, preview["preview_digest"])
    writes = len(store.writes)
    repeat = svc.import_batch(payload, preview["preview_digest"])
    assert first["idempotent"] is False and repeat["idempotent"] is True
    assert len(store.writes) == writes
    assert svc.get_summary()["counts"]["fills"] == 2
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes)
    assert svc.list_episodes()["items"][0]["closed_pnl"] == 198


def test_fifo_partial_sale_and_zero_to_zero_cash_bridge():
    _, svc = service()
    rows = [fill("b1", "2026-10-19", "buy", 100, 10, 100), fill("b2", "2026-10-20", "buy", 100, 20, 200),
        fill("s1", "2026-10-21", "sell", 100, 15, 100)]
    import_ready(svc, bundle(rows))
    ep = svc.list_episodes()["items"][0]
    assert ep["status"] == "open" and ep["realized_pnl"] == 498
    assert ep["remaining_cost"] == 2001 and ep["closed_pnl"] is None
    assert ep["unrealized_pnl"] is None  # no valuation invented from last trade
    import_ready(svc, bundle(rows + [fill("s2", "2026-10-22", "sell", 100, 25, 0)]))
    ep = svc.list_episodes()["items"][0]
    assert ep["closed_pnl"] == ep["realized_pnl"] == 996
    assert sum(r["realized_pnl"] for r in ep["matches"]) == 996


def test_unknown_opening_cost_stays_unknown_after_close():
    _, svc = service()
    import_ready(svc, bundle([fill("initial-sale", "2026-07-01", "sell", 100, 141.54, 0)]))
    ep = svc.list_episodes()["items"][0]
    assert ep["opening_quantity"] == 100 and ep["opening_cost_status"] == "unknown"
    assert ep["realized_pnl"] is None and ep["closed_pnl"] is None
    assert ep["qualification"]["accounting"]["eligible"] is False


def test_missing_fee_is_not_zero_and_does_not_produce_profit():
    _, svc = service()
    import_ready(svc, bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100, fees=None), fill("sale", "2026-10-20", "sell", 100, 12, 0)]))
    ep = svc.list_episodes()["items"][0]
    assert ep["fees"] is None and ep["closed_pnl"] is None
    assert ep["qualification"]["accounting"]["eligible"] is False


def test_same_day_zero_then_reopen_is_another_episode():
    _, svc = service()
    rows = [fill("b1", "2026-10-22", "buy", 100, 40, 100),
        fill("s1", "2026-10-23", "sell", 100, 38, 0, time="09:30:30"),
        fill("b2", "2026-10-23", "buy", 100, 37, 100, time="09:36:25")]
    import_ready(svc, bundle(rows))
    items = svc.list_episodes()["items"]
    assert len(items) == 2
    closed, opened = next(r for r in items if r["status"] == "closed"), next(r for r in items if r["status"] == "open")
    assert closed["closed_pnl"] == -202 and opened["buy_cost"] == 3701
    assert closed["episode_id"] != opened["episode_id"]


def test_two_equal_price_quantity_fills_are_preserved_and_overlap_is_idempotent():
    _, svc = service()
    a = fill("placeholder", "2026-10-23", "buy", 100, 37, 100)
    b = {**a, "quantity_after": 200, "source_ref": {"source_id": "raw", "row": 2}}
    a.pop("fill_id"); b.pop("fill_id")
    import_ready(svc, bundle([a, b]))
    assert svc.get_summary()["counts"]["fills"] == 2
    assert svc.list_episodes()["items"][0]["quantity"] == 200
    import_ready(svc, bundle([a, b], sources=[source(), source("corroboration", "another original")]))
    assert svc.get_summary()["counts"]["fills"] == 2


def test_source_or_fill_conflict_blocks_the_entire_import():
    store, svc = service()
    payload = bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100)])
    import_ready(svc, payload)
    old = deepcopy(store.collection(COLLECTIONS["fills"]).documents)
    bad = bundle([fill("buy", "2026-10-19", "buy", 200, 10, 200)])
    preview = svc.preview_import(bad)
    assert not preview["can_import"] and preview["counts"]["conflicts"] > 0
    writes = len(store.writes)
    with pytest.raises(TradeJournalError, match="冲突"):
        svc.import_batch(bad, preview["preview_digest"])
    assert len(store.writes) == writes and store.collection(COLLECTIONS["fills"]).documents == old
    changed_source = bundle(payload["fills"], sources=[source(content="changed original")])
    assert not svc.preview_import(changed_source)["can_import"]


def test_preview_digest_covers_notes_and_original_content_hash_is_checked():
    store, svc = service()
    payload = bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100)])
    preview = svc.preview_import(payload)
    payload["fills"][0]["note"] = "changed after preview"
    with pytest.raises(TradeJournalError, match="预览"):
        svc.import_batch(payload, preview["preview_digest"])
    payload["sources"][0]["content"] = "tampered"
    with pytest.raises(TradeJournalError, match="SHA256"):
        svc.preview_import(payload)
    assert store.writes == []


def test_source_is_recoverable_and_financial_overlap_does_not_duplicate_cash_event():
    _, svc = service()
    event = {"kind": "利息归本", "code": "-", "date": "2026-10-19", "quantity": 0, "net_cash": 3.23,
        "source_ref": {"source_id": "raw", "row": 3}}
    import_ready(svc, bundle([], [event]))
    same_event = {**event, "source_ref": {"source_id": "corroboration", "row": 19}}
    import_ready(svc, bundle([], [same_event], sources=[source("corroboration", "second exact source")]))
    assert svc.get_summary()["counts"]["financial_events"] == 1
    assert "content" not in svc.get_source("raw")
    archived = svc.get_source("raw", include_content=True)
    assert hashlib.sha256(archived["content"].encode()).hexdigest() == archived["sha256"]


def test_backfill_cannot_silently_rebind_an_existing_decision_episode():
    _, svc = service()
    sale = fill("initial-sale", "2026-07-01", "sell", 100, 20, 0)
    import_ready(svc, bundle([sale]))
    eid = svc.list_episodes()["items"][0]["episode_id"]
    svc.append_decision(eid, decision_payload())
    prior = fill("earlier-buy", "2026-06-30", "buy", 100, 10, 100)
    preview = svc.preview_import(bundle([prior, sale]))
    assert not preview["can_import"]
    assert any(r["code"] == "episode_identity_changed" for r in preview["conflicts"])


def test_ipo_payment_and_credit_are_not_double_counted_as_buys():
    _, svc = service()
    financial = [
        {"kind": "配售缴款", "code": "787825", "linked_ticker": "688825.SH", "date": "2026-07-16", "quantity": 500, "net_cash": -4330,
            "quantity_after": 0, "source_ref": {"source_id": "raw", "row": 1}},
        {"kind": "新股入账", "code": "688825", "date": "2026-07-16", "settlement_date": "2026-07-24", "quantity": 500,
            "net_cash": 0, "quantity_after": 500, "source_ref": {"source_id": "raw", "row": 2}},
        {"kind": "红利入账", "code": "688825", "date": "2026-07-24", "quantity": 500, "net_cash": 20,
            "quantity_after": 500, "source_ref": {"source_id": "raw", "row": 3}},
    ]
    import_ready(svc, bundle([fill("ipo-sale", "2026-07-24", "sell", 500, 10, 0, fees=5, ticker="688825.SH")], financial))
    ep = svc.list_episodes()["items"][0]
    assert ep["buy_count"] == 0 and ep["sell_count"] == 1
    assert ep["closed_pnl"] == ep["realized_pnl"] == 665
    assert svc.get_summary()["counts"]["financial_events"] == 3


def test_balance_reorders_unverified_statement_time_but_does_not_rewrite_it():
    _, svc = service()
    rows = [fill("initial", "2026-09-23", "buy", 500, 10, 500),
        fill("sell", "2026-09-24", "sell", 500, 10, 300, time="10:00:00", time_precision="statement_event_time"),
        fill("add", "2026-09-24", "buy", 300, 10, 800, time="11:00:00", time_precision="statement_event_time")]
    preview = svc.preview_import(bundle(rows))
    assert preview["can_import"] and len(preview["inventory_repairs"]) == 1
    svc.import_batch(bundle(rows), preview["preview_digest"])
    byid = {r["fill_id"]: r for r in svc.list_fills()["items"]}
    assert byid["add"]["quantity_before"] == 500 and byid["sell"]["quantity_after"] == 300
    assert byid["sell"]["time"] == "10:00:00"


def decision_payload(**changes):
    return {"action_id": "action.buy", "action": "首买", "mode_id": "mode.reversal_pry", "mode": "翘板原意",
        "reason": "用户确认的原始理由", "observed_at": "2026-10-19T09:50:00+08:00", "evidence_class": "retrospective_recall",
        "expectation": "次日观察承接", "evidence": [{"text": "事后回忆", "evidence_class": "retrospective_recall"}], **changes}


def test_append_revisions_keep_raw_mode_original_version_and_historical_status():
    _, svc = service()
    import_ready(svc, bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100)]))
    eid = svc.list_episodes()["items"][0]["episode_id"]
    first = svc.append_decision(eid, decision_payload(evidence_class="contemporaneous"))
    assert first["record"]["evidence_class"] == "retrospective_recall"
    assert first["record"]["mode"] == "翘板原意" and not first["qualification"]["ex_ante"]["eligible"]
    second = svc.append_decision(eid, {"decision_id": first["decision_id"], "expected_revision": 1,
        "revision_reason": "补充当时想法", "reason": "修订后的理由"})
    history = svc.get_decision_history(first["decision_id"])
    assert second["revision"] == 2 and history["items"][0]["reason"] == "用户确认的原始理由"
    assert history["items"][1]["previous_revision"] == 1
    with pytest.raises(TradeJournalError, match="版本"):
        svc.append_decision(eid, {"decision_id": first["decision_id"], "expected_revision": 1, "revision_reason": "stale"})


def test_definition_and_fill_references_are_validated():
    _, svc = service()
    import_ready(svc, bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100)]))
    eid = svc.list_episodes()["items"][0]["episode_id"]
    with pytest.raises(TradeJournalError):
        svc.append_decision(eid, decision_payload(mode_id="mode.does_not_exist"))
    with pytest.raises(TradeJournalError):
        svc.append_decision(eid, decision_payload(fill_ids=["another-episode-fill"]))


def test_future_evidence_does_not_upgrade_historical_decision():
    _, svc = service()
    import_ready(svc, bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100)]))
    eid = svc.list_episodes()["items"][0]["episode_id"]
    result = svc.append_decision(eid, decision_payload(evidence=[{"evidence_class": "contemporaneous", "observed_at": "2026-10-19T15:00:00+08:00",
        "available_at": "2026-10-19T15:01:00+08:00", "source_ref": {"source_id": "raw", "row": 1}}], frozen_plan={"plan_id": "old-plan", "recorded_at": "2026-10-19T09:00:00+08:00"}))
    assert result["record"]["temporal_errors"]
    assert result["record"]["frozen_plan"]["captured_at"].startswith("2026-10-26")
    assert not result["qualification"]["ex_ante"]["eligible"]


def test_cross_security_links_bind_exact_revisions_and_do_not_infer_motive():
    _, svc = service()
    import_ready(svc, bundle([fill("a", "2026-10-19", "buy", 100, 10, 100), fill("b", "2026-10-19", "buy", 100, 20, 100, ticker="300502.SZ")]))
    episodes = svc.list_episodes()["items"]
    a = svc.append_decision(episodes[0]["episode_id"], decision_payload())
    b = svc.append_decision(episodes[1]["episode_id"], decision_payload())
    link = svc.link_decisions({"from_decision_id": a["decision_id"], "to_decision_id": b["decision_id"], "relation": "capital_switch",
        "reason": "用户确认卖出后另选标的", "evidence_class": "retrospective_recall"})
    assert link["record"]["from_revision"] == 1 and link["record"]["to_revision"] == 1
    assert len(svc.get_episode(episodes[0]["episode_id"])["links"]) == 1
    with pytest.raises(TradeJournalError, match="自身"):
        svc.link_decisions({"from_decision_id": a["decision_id"], "to_decision_id": a["decision_id"], "reason": "bad"})


def test_expectation_manual_vs_numeric_and_future_remain_distinct():
    _, svc = service()
    content = json.dumps({"point": {"value": 42.5}}, sort_keys=True)
    payload = bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100)], sources=[source(content=content)])
    import_ready(svc, payload)
    eid = svc.list_episodes()["items"][0]["episode_id"]
    decision = svc.append_decision(eid, decision_payload(expectation={"metric": "close", "operator": ">=", "target": 42},
        verification_at="2026-10-20T15:00:00+08:00"))
    manual = svc.append_expectation_check(decision["decision_id"], {"status": "supported"})
    assert manual["status"] == "supported" and not manual["record"]["machine_verified"]
    actual = {"value": 42.5, "metric": "close", "observed_at": "2026-10-20T15:00:00+08:00",
        "available_at": "2026-10-20T15:01:00+08:00", "source_ref": {"source_id": "raw"}, "value_path": "point.value"}
    checked = svc.append_expectation_check(decision["decision_id"], {"evaluation_class": "numeric_comparison", "actual": actual})
    assert checked["status"] == "supported" and checked["record"]["machine_verified"]
    unknown = svc.append_expectation_check(decision["decision_id"], {"evaluation_class": "numeric_comparison", "actual": {**actual, "value": 99}})
    assert unknown["status"] == "unknown"
    future = svc.append_expectation_check(decision["decision_id"], {"status": "supported", "actual": {**actual, "available_at": "2026-10-27T15:00:00+08:00"}})
    assert future["status"] == "unknown" and not future["record"]["plan_was_ex_ante"]


def test_unfilled_watchlist_is_separate_from_real_trade_denominator():
    _, svc = service()
    svc.append_observation({"ticker": "000636.SZ", "name": "风华高科", "scope": "manual_watchlist", "state": "unfilled",
        "reason": "未成交", "evidence_class": "retrospective_recall"})
    assert svc.list_observations(query="风华")["total"] == 1
    assert svc.get_summary()["counts"]["fills"] == 0 and svc.list_episodes()["total"] == 0


def test_live_execution_bridge_is_read_only_and_not_counted_twice():
    store, svc = service()
    store.collection("trade_executions").documents.append({"execution_id": "live", "ticker": "000636.SZ", "quantity": 100, "price": 10, "side": "buy", "plan_id": "plan"})
    import_ready(svc, bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100)]))
    detail = svc.get_episode(svc.list_episodes()["items"][0]["episode_id"])
    assert detail["live_references"]["executions"][0]["execution_id"] == "live"
    assert svc.get_summary()["counts"]["fills"] == 1
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes)


def test_duplicate_payload_ids_cannot_hide_distinct_rows_and_broker_identity_is_unique():
    store, svc = service()
    row = fill("same", "2026-10-19", "buy", 100, 10, None)
    other = {**row, "source_ref": {"source_id": "raw", "row": 99}}
    result = svc.preview_import(bundle([row, other]))
    assert not result["can_import"]
    assert result["counts"]["input_fill_rows"] == 2 and result["counts"]["fills"] == 1
    assert any(r["code"] == "duplicate_fill_id_different_rows" for r in result["conflicts"])
    assert store.writes == []
    result = svc.preview_import(bundle([row, deepcopy(row)]))
    assert result["can_import"] and result["counts"]["payload_duplicate_fill_rows"] == 1
    left = {**row, "fill_id": "one", "broker_execution_id": "broker-trade"}
    right = {**row, "fill_id": "two", "broker_execution_id": "broker-trade"}
    result = svc.preview_import(bundle([left, right]))
    assert not result["can_import"] and any(r["code"] == "broker_execution_identity_conflict" for r in result["conflicts"])


def test_partial_import_is_invisible_and_retries_complete_without_duplicate_facts(monkeypatch):
    store, svc = service()
    payload = bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100), fill("sell", "2026-10-20", "sell", 100, 12, 0)])
    coll = store.collection(COLLECTIONS["fills"])
    original = coll.update_one
    failed = False
    def fail_second(query, changes, upsert=False):
        nonlocal failed
        if query.get("fill_id") == "sell" and not failed:
            failed = True
            raise RuntimeError("simulated worker death before commit")
        return original(query, changes, upsert=upsert)
    monkeypatch.setattr(coll, "update_one", fail_second)
    with pytest.raises(RuntimeError):
        import_ready(svc, payload)
    fresh = TradeJournalService(store, now_factory=svc.now_factory)
    assert fresh.get_summary()["counts"]["fills"] == 0
    assert fresh.list_sources()["total"] == 0 and fresh.list_batches()["total"] == 0
    assert len(coll.documents) == 1  # staged immutable document is recoverable
    import_ready(fresh, payload)
    assert fresh.get_summary()["counts"]["fills"] == 2 and len(coll.documents) == 2
    assert fresh.list_episodes()["items"][0]["closed_pnl"] == 198
    store.collection(COLLECTIONS["batches"]).documents[0]["status"] = "pending"
    pending = TradeJournalService(store, now_factory=svc.now_factory)
    assert pending.get_summary()["counts"]["fills"] == 0 and pending.list_sources()["total"] == 0


def test_cross_worker_lease_and_revision_are_not_protected_only_by_process_lock():
    from datetime import timedelta
    store, svc = service()
    payload = bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100)])
    import_ready(svc, payload)
    eid = svc.list_episodes()["items"][0]["episode_id"]
    first = svc.append_decision(eid, decision_payload())
    other = TradeJournalService(store, now_factory=svc.now_factory)
    assert other.get_decision_history(first["decision_id"])["latest_revision"] == 1
    svc.append_decision(eid, {"decision_id": first["decision_id"], "expected_revision": 1,
        "revision_reason": "first writer", "reason": "更新理由"})
    with pytest.raises(TradeJournalError, match="版本"):
        other.append_decision(eid, {"decision_id": first["decision_id"], "expected_revision": 1,
            "revision_reason": "stale other worker", "reason": "过时版本"})
    lock = store.collection(COLLECTIONS["write_locks"]).documents[0]
    lock.update(owner="foreign-worker", lease_until=(datetime.now(TZ)+timedelta(seconds=60)).isoformat())
    with pytest.raises(TradeJournalError, match="另一个请求"):
        other.append_observation({"ticker": "000636.SZ"})
    lock["lease_until"] = (datetime.now(TZ)-timedelta(seconds=1)).isoformat()
    assert other.append_observation({"ticker": "000636.SZ"})["record"]["ticker"] == "000636.SZ"


def test_unique_index_race_is_a_conflict_without_overwrite(monkeypatch):
    from pymongo.errors import DuplicateKeyError
    store, svc = service()
    coll = store.collection(COLLECTIONS["sources"])
    def racing(query, changes, upsert=False):
        coll.documents.append({**query, "content_hash": "different-winning-content"})
        raise DuplicateKeyError("another worker won unique key")
    monkeypatch.setattr(coll, "update_one", racing)
    with pytest.raises(TradeJournalError, match="并发写入冲突"):
        import_ready(svc, bundle([], sources=[source()]))
    assert coll.documents[0]["content_hash"] == "different-winning-content"
    assert svc.list_batches()["total"] == 0


def test_source_metadata_paths_do_not_fetch_original_and_restore_rejects_storage_tampering():
    store, svc = service()
    import_ready(svc, bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100)]))
    store.reads.clear()
    svc = TradeJournalService(store, now_factory=svc.now_factory)
    svc.get_summary(); svc.list_sources(); svc.get_source("raw")
    source_reads = [r for r in store.reads if r[0] == COLLECTIONS["sources"]]
    assert source_reads and all(r[2].get("content") == 0 for r in source_reads)
    assert svc.get_source("raw", include_content=True)["content"] == "broker evidence"
    store.collection(COLLECTIONS["sources"]).documents[0]["content"] = "corrupted original"
    with pytest.raises(TradeJournalError, match="校验失败"):
        svc.get_source("raw", include_content=True)


def test_impossible_initial_balance_and_reused_ipo_payment_never_make_fake_profit():
    _, svc = service()
    result = svc.preview_import(bundle([fill("buy", "2026-10-19", "buy", 100, 10, 50)]))
    assert not result["can_import"] and any(r["code"] == "negative_opening_inventory" for r in result["conflicts"])
    financial = [
        {"financial_event_id": "pay", "kind": "ipo_payment", "ticker": "688825.SH", "date": "2026-07-16", "quantity": 100, "net_cash": -500,
            "source_ref": {"source_id": "raw", "row": 1}},
        {"financial_event_id": "credit1", "kind": "stock_credit", "ticker": "688825.SH", "date": "2026-07-17", "quantity": 100, "net_cash": 0, "quantity_after": 100,
            "source_ref": {"source_id": "raw", "row": 2}},
        {"financial_event_id": "credit2", "kind": "stock_credit", "ticker": "688825.SH", "date": "2026-07-19", "quantity": 100, "net_cash": 0, "quantity_after": 100,
            "source_ref": {"source_id": "raw", "row": 3}}]
    rows = [fill("sale1", "2026-07-18", "sell", 100, 8, 0, ticker="688825.SH"),
        fill("sale2", "2026-07-20", "sell", 100, 8, 0, ticker="688825.SH")]
    import_ready(svc, bundle(rows, financial))
    episodes = sorted(svc.list_episodes()["items"], key=lambda r: r["start_date"])
    assert episodes[0]["closed_pnl"] == 299 and episodes[1]["closed_pnl"] is None
    assert "pay" not in episodes[1]["financial_event_ids"]
    financial[0]["net_cash"] = 500
    with pytest.raises(TradeJournalError, match="现金流不能为正"):
        svc.preview_import(bundle([], financial))


def test_cohort_persistence_is_complete_compact_deduplicated_and_separate_from_fills(monkeypatch):
    from test_trade_journal_research_bridge import research, arguments
    import shilun.decision_research.service as owner
    store, svc = service()
    data = research()
    monkeypatch.setattr(owner, "load_mode_research", lambda *args: deepcopy(data))
    payload = {"scope": "strategy_cohort", "observed_at": "2026-10-23T18:00:00+08:00",
        "evidence_class": "contemporaneous", **arguments(data)}
    created = svc.append_observation(payload)["record"]
    assert created["evidence_class"] == "historical_reconstruction" and created["member_count"] == 2
    assert "mode_research_snapshot" not in created and created["snapshot_ref"]
    svc.append_observation({**payload, "reason": "第二次观察，同一不可变群体"})
    assert len(store.collection(COLLECTIONS["snapshots"]).documents) == 1
    listing = svc.list_observations()
    assert listing["total"] == 2 and all("members" not in r and "content" not in r for r in listing["items"])
    sid = created["snapshot_ref"]["snapshot_id"]
    assert "content" not in svc.get_snapshot(sid)
    snapshot = svc.get_snapshot(sid, include_content=True)
    assert snapshot["content"]["daily"]["members"][1]["close_return"] == -9
    assert "assessments" not in snapshot["content"]
    assert svc.get_summary()["counts"]["fills"] == 0
    assert svc.get_observation(created["observation_id"])["cohort_digest"] == created["cohort_digest"]
    bad = deepcopy(payload); bad["members"].pop()
    with pytest.raises(TradeJournalError, match="候选全集"):
        svc.append_observation(bad)
    store.collection(COLLECTIONS["snapshots"]).documents[0]["content"]["daily"]["cohort_n"] = 999
    with pytest.raises(TradeJournalError, match="校验失败"):
        svc.get_snapshot(sid, include_content=True)


def test_backdated_contemporaneous_or_future_actual_trades_cannot_gain_ex_ante():
    _, svc = service()
    with pytest.raises(TradeJournalError, match="未来"):
        svc.preview_import(bundle([fill("future", "2026-10-27", "buy", 100, 10, 100)]))
    import_ready(svc, bundle([fill("buy", "2026-10-19", "buy", 100, 10, 100)]))
    eid = svc.list_episodes()["items"][0]["episode_id"]
    result = svc.append_decision(eid, decision_payload(evidence_class="contemporaneous",
        evidence=[{"evidence_class": "contemporaneous", "observed_at": "2026-10-19T10:00:00+08:00",
            "available_at": "2026-10-19T09:00:00+08:00", "source_ref": {"source_id": "raw", "row": 1}}],
        recorded_at="2026-10-19T09:00:00+08:00", fill_ids=["buy"]))
    assert result["record"]["evidence_class"] == "retrospective_recall"
    assert result["record"]["recorded_at"].startswith("2026-10-26")
    assert any("早于" in e for e in result["record"]["temporal_errors"])
    assert not result["qualification"]["ex_ante"]["eligible"]


def complete_plan(**changes):
    return {"ticker": "000636.SZ", "name": "风华高科", "target_date": "2026-10-27",
        "mode_id": "mode.reversal_pry", "mode": "原始模式文字", "trigger": "明确触发条件",
        "reason": "独立理由", "expectation": {"metric": "close", "operator": ">", "target": 10},
        "verification_at": "2026-10-27T15:00:00+08:00", "invalidation": "失效条件",
        "holding_horizon": "当日检查", "risk_constraints": "明确风险预算", "reference_tickers": ["000001.SZ"],
        "evidence": [{"text": "开盘前实际保存的事实", "evidence_class": "contemporaneous",
            "observed_at": "2026-10-27T08:29:00+08:00", "available_at": "2026-10-27T08:30:00+08:00",
            "source_ref": {"source_id": "raw", "row": 1}}], **changes}


def test_first_entry_plan_can_precede_any_episode_then_bind_without_backdating():
    store = FakeStore()
    clock = [datetime(2026, 10, 27, 8, 30, tzinfo=TZ)]
    svc = TradeJournalService(store, now_factory=lambda: clock[0])
    import_ready(svc, bundle([], sources=[source(evidence_class="contemporaneous")]))
    clock[0] = datetime(2026, 10, 27, 9, tzinfo=TZ)
    frozen = svc.freeze_plan(complete_plan())
    assert frozen["record"]["complete"] and frozen["ex_ante_verifiable"]
    assert svc.list_episodes()["total"] == 0 and svc.list_plans(ticker="000636")["total"] == 1
    assert svc.get_summary()["counts"]["fills"] == 0
    original = svc.get_plan(frozen["plan_id"], 1)
    clock[0] = datetime(2026, 10, 27, 11, tzinfo=TZ)
    row = fill("first-buy", "2026-10-27", "buy", 100, 10, 100)
    import_ready(svc, bundle([row], sources=[source(evidence_class="contemporaneous")]))
    eid = svc.list_episodes()["items"][0]["episode_id"]
    clock[0] = datetime(2026, 10, 27, 12, tzinfo=TZ)
    bound = svc.bind_plan(frozen["plan_id"], eid, {"revision": 1, "fill_ids": ["first-buy"], "reason": "用户明确关联"})
    assert bound["record"]["recorded_at"].startswith("2026-10-27T12:00")
    assert bound["record"]["evidence_class"] == "retrospective_recall"
    assert bound["record"]["frozen_plan"]["captured_at"] == original["captured_at"]
    assert bound["qualification"]["ex_ante"]["eligible"]
    assert "不代表" in bound["qualification"]["ex_ante"]["scope"]
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes)


def test_plan_revisions_do_not_retroactively_replace_an_old_exact_binding():
    store = FakeStore(); clock = [datetime(2026, 10, 27, 8, 30, tzinfo=TZ)]
    svc = TradeJournalService(store, now_factory=lambda: clock[0])
    import_ready(svc, bundle([], sources=[source(evidence_class="contemporaneous")]))
    clock[0] = datetime(2026, 10, 27, 9, tzinfo=TZ)
    first = svc.freeze_plan(complete_plan())
    clock[0] = datetime(2026, 10, 27, 11, tzinfo=TZ)
    import_ready(svc, bundle([fill("buy", "2026-10-27", "buy", 100, 10, 100)], sources=[source(evidence_class="contemporaneous")]))
    second = svc.freeze_plan({"plan_id": first["plan_id"], "expected_revision": 1,
        "revision_reason": "成交后改条件", "trigger": "事后新条件", "captured_at": "2026-10-27T08:00:00+08:00"})
    assert second["revision"] == 2 and second["record"]["captured_at"].startswith("2026-10-27T11")
    eid = svc.list_episodes()["items"][0]["episode_id"]
    late = svc.bind_plan(first["plan_id"], eid, {"revision": 2, "fill_ids": ["buy"]})
    assert not late["qualification"]["ex_ante"]["eligible"]
    early = svc.bind_plan(first["plan_id"], eid, {"revision": 1, "fill_ids": ["buy"]})
    assert early["qualification"]["ex_ante"]["eligible"]
    assert svc.get_plan(first["plan_id"], 1)["content"]["trigger"] == "明确触发条件"
    assert svc.list_plans()["items"][0]["revisions"] == [1, 2]
    with pytest.raises(TradeJournalError, match="版本"):
        svc.freeze_plan({"plan_id": first["plan_id"], "expected_revision": 1, "revision_reason": "stale"})


def test_incomplete_plan_and_old_live_reference_never_pretend_pretrade_evidence():
    store, svc = service()
    incomplete = svc.freeze_plan({"ticker": "000636.SZ", "target_date": "2026-10-19", "reason": "事后补预案"})
    assert incomplete["missing_fields"] and not incomplete["ex_ante_verifiable"]
    store.collection("trading_watch_plans").documents.append({"plan_id": "production-plan", "ticker": "000636.SZ", "plan_revision": 1,
        "frozen_at": "2026-10-01T08:00:00+08:00", "hard_stop": 9})
    before = deepcopy(store.collection("trading_watch_plans").documents)
    frozen = svc.freeze_plan(complete_plan(live_plan_ref={"plan_id": "production-plan", "version": 1}))
    assert frozen["record"]["captured_at"].startswith("2026-10-26")
    assert svc.get_plan(frozen["plan_id"])["live_reference"]["content"]["frozen_at"].startswith("2026-10-01")
    assert store.collection("trading_watch_plans").documents == before
    assert not frozen["ex_ante_verifiable"]  # missing separately archived contemporaneous evidence
    with pytest.raises(TradeJournalError, match="确切版本"):
        svc.freeze_plan(complete_plan(live_plan_ref={"plan_id": "production-plan", "version": 99}))


def test_a_first_plan_can_archive_its_evidence_original_without_any_trade_or_account_write():
    store = FakeStore()
    svc = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 27, 9, tzinfo=TZ))
    created = svc.freeze_plan(complete_plan(sources=[source(content="当时写下并冻结的原文事实")]))
    assert created["ex_ante_verifiable"] and created["record"]["inline_source_batch_id"]
    assert "sources" not in created["record"]["content"]
    assert svc.get_source("raw", include_content=True)["content"] == "当时写下并冻结的原文事实"
    assert svc.get_summary()["counts"]["sources"] == 1
    assert svc.get_summary()["counts"]["fills"] == 0
    assert svc.list_episodes()["total"] == 0
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes)


def test_inline_evidence_is_archived_before_plan_capture_and_manual_numbers_are_labelled():
    from datetime import timedelta
    store = FakeStore(); calls = [0]
    def clock():
        at = datetime(2026, 10, 27, 9, tzinfo=TZ) + timedelta(seconds=calls[0])
        calls[0] += 1
        return at
    svc = TradeJournalService(store, now_factory=clock)
    created = svc.freeze_plan(complete_plan(sources=[source(content=json.dumps({"point": {"value": 11}}))]))
    archived = svc.get_source("raw", include_content=True)
    assert archived["recorded_at"] <= created["record"]["captured_at"]
    assert archived["metadata"]["provenance_class"] == "manual_observation"
    assert not archived["metadata"]["market_data_verified"]
    # Binding is after the fact. Numeric arithmetic is separate from authentic
    # market measurements, even when the uploaded original is valid JSON.
    svc.now_factory = lambda: datetime(2026, 10, 27, 16, tzinfo=TZ)
    import_ready(svc, {"sources": [], "fills": [fill("buy", "2026-10-27", "buy", 100, 10, 100)], "financial_events": []})
    eid = svc.list_episodes()["items"][0]["episode_id"]
    decision = svc.bind_plan(created["plan_id"], eid, {"revision": 1, "fill_ids": ["buy"]})
    checked = svc.append_expectation_check(decision["decision_id"], {"evaluation_class": "numeric_comparison",
        "actual": {"metric": "close", "value": 11, "value_path": "point.value",
            "observed_at": "2026-10-27T15:00:00+08:00", "available_at": "2026-10-27T15:01:00+08:00",
            "source_ref": {"source_id": "raw", "row": 1}}})
    assert checked["record"]["machine_verified"]
    assert checked["record"]["comparison_scope"] == "archived_numeric_value_only"
    assert checked["record"]["actual_provenance"]["provenance_class"] == "manual_observation"
    assert not checked["record"]["actual_provenance"]["market_data_verified"]
    assert any("未核验为实测" in reason for reason in checked["reasons"])
