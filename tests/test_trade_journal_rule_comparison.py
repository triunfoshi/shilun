"""First-lot rule reference reuses FIFO and never invents missing evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, date

import pytest

from shilun.trade_journal.rule_comparison import JournalRuleComparisonService
from shilun.trade_journal.service import TradeJournalError, TradeJournalService
from test_trade_journal import FakeStore, TZ, bundle, fill, import_ready


RULE = {"rule": {"kind": "next_trading_day_close", "sell_fee_bps": 10, "slippage_bps": 5}}


def setup(rows=None, *, now=None):
    store = FakeStore()
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 27, 16, tzinfo=TZ))
    rows = rows or [fill("buy", "2026-10-19", "buy", 100, 10, 100, fees=10),
        fill("sell", "2026-10-21", "sell", 100, 12, 0, fees=20)]
    import_ready(journal, bundle(rows))
    eid = journal.list_episodes()["items"][0]["episode_id"]
    for offset in range(5):
        day = date(2026, 10, 19) + timedelta(days=offset)
        store.collection("trade_calendar").documents.append({"exchange": "SSE", "cal_date": day.strftime("%Y%m%d"), "is_open": 1})
    store.collection("market_daily_bars").documents.append({"ticker": "000636.SZ", "date": "2026-10-20", "close": 11, "vol": 1000})
    store.collection("daily_price_limits").documents.append({"ticker": "000636.SZ", "trade_date": "20261020", "down_limit": 9.9, "up_limit": 12.1})
    store.writes.clear()
    svc = JournalRuleComparisonService(store, journal=journal, now_factory=lambda: now or datetime(2026, 10, 27, 16, tzinfo=TZ))
    return store, journal, svc, eid


def test_comparison_uses_fee_inclusive_existing_fifo_and_explicit_fee_slippage():
    store, journal, svc, eid = setup()
    before = deepcopy(journal.get_episode(eid))
    result = svc.preview(eid, RULE)
    assert result["status"] == "compared"
    assert result["actual"]["net_pnl"] == 170
    assert result["actual"]["buy_cost"] == 1010
    assert result["actual"]["return_pct"] == pytest.approx(170 / 1010 * 100)
    # 1100 gross - 0.55 slippage - 1.09945 modeled sell fees - 1010 actual cost.
    assert result["reference"]["net_pnl"] == pytest.approx(88.35055)
    assert result["reference"]["exit_date"] == "2026-10-20"
    assert result["difference_pp"] == pytest.approx(result["actual_return"] - result["reference_return"])
    assert result["evidence_class"] == "historical_replay"
    assert not result["ex_ante_verifiable"] and not result["strategy_validated"]
    assert not result["reference"]["execution_verified"]
    assert len(result["sources"]) == 3
    assert result["sources"][0]["collection"] == "trade_calendar"
    assert store.writes == [] and journal.get_episode(eid) == before


def test_later_additions_cannot_change_first_lot_comparison():
    rows = [fill("buy", "2026-10-19", "buy", 100, 10, 100, fees=10),
        fill("later", "2026-10-20", "buy", 100, 30, 200, fees=30),
        fill("sell", "2026-10-21", "sell", 200, 12, 0, fees=40)]
    _, journal, svc, eid = setup(rows)
    result = svc.preview(eid, RULE)
    assert result["status"] == "compared"
    assert result["actual"]["net_pnl"] == 170
    assert result["actual"]["quantity"] == 100
    assert journal.get_episode(eid)["episode"]["closed_pnl"] < 0


@pytest.mark.parametrize("change, message", [
    ({"entry_fill_id": "other"}, "首笔"), ({"quantity": 50}, "完整数量"),
    ({"rule": {"kind": "best_high"}}, "next_trading_day_close"),
    ({"rule": {"kind": "next_trading_day_close", "sell_fee_bps": -1}}, "有效"),
    ({"rule": {"kind": "next_trading_day_close", "slippage_bps": float("nan")}}, "有效"),
    ({"rule": {"kind": "next_trading_day_close", "sell_fee_bps": 10000}}, "小于"),
])
def test_unsupported_or_invalid_protocol_rejected(change, message):
    _, _, svc, eid = setup()
    with pytest.raises(TradeJournalError, match=message):
        svc.preview(eid, {**RULE, **change})


def test_missing_fee_assumption_remains_unknown_and_explicit_zero_is_allowed():
    _, _, svc, eid = setup()
    result = svc.preview(eid, {"rule": {"kind": "next_trading_day_close"}})
    assert result["status"] == "unknown" and result["reference"]["net_pnl"] is None
    assert any("显式" in r for r in result["reasons"])
    result = svc.preview(eid, {"rule": {"kind": "next_trading_day_close", "sell_fee_bps": 0, "slippage_bps": 0}})
    assert result["status"] == "compared" and result["reference"]["net_pnl"] == 90


@pytest.mark.parametrize("problem, expected", [
    ("bar_missing", "日线缺失"), ("volume_zero", "成交量"),
    ("limits_missing", "涨跌停价"), ("limit_down", "跌停"),
    ("calendar_missing", "不连续"), ("bar_duplicate", "不唯一"),
    ("adjusted", "复权"), ("nonfinite", "收盘价"),
])
def test_missing_or_unexecutable_reference_never_invents_exit(problem, expected):
    store, _, svc, eid = setup()
    bars = store.collection("market_daily_bars").documents
    if problem == "bar_missing":
        bars.clear()
    elif problem == "volume_zero":
        bars[0]["vol"] = 0
    elif problem == "limits_missing":
        store.collection("daily_price_limits").documents.clear()
    elif problem == "limit_down":
        bars[0]["close"] = 9.9
    elif problem == "calendar_missing":
        store.collection("trade_calendar").documents[:] = [r for r in store.collection("trade_calendar").documents if r["cal_date"] != "20261020"]
    elif problem == "bar_duplicate":
        bars.append(deepcopy(bars[0]))
    elif problem == "adjusted":
        bars[0]["adjust"] = "qfq"
    elif problem == "nonfinite":
        bars[0]["close"] = float("nan")
    result = svc.preview(eid, RULE)
    assert result["status"] == "unknown" and result["difference_pp"] is None
    assert any(expected in r for r in result["reasons"])
    assert result["reference"] is None or result["reference"]["net_pnl"] is None
    assert store.writes == []


def test_target_not_settled_does_not_read_future_bar():
    store, _, svc, eid = setup(now=datetime(2026, 10, 20, 10, tzinfo=TZ))
    result = svc.preview(eid, RULE)
    assert result["status"] == "unknown" and result["reference"]["close"] is None
    assert result["actual"] is None
    assert any("未来实际收益" in r for r in result["reasons"])
    assert any("尚未收盘" in r for r in result["reasons"])
    assert not any(collection == "market_daily_bars" for collection, _, _ in store.reads)


def test_incomplete_first_lot_remains_unknown_even_if_reference_is_available():
    rows = [fill("buy", "2026-10-19", "buy", 100, 10, 100),
        fill("sell", "2026-10-21", "sell", 50, 12, 50)]
    _, _, svc, eid = setup(rows)
    result = svc.preview(eid, RULE)
    assert result["status"] == "unknown" and result["actual"] is None and result["difference_pp"] is None
    assert any("尚未全部卖出" in r for r in result["reasons"])


def test_missing_real_fee_does_not_become_zero():
    rows = [fill("buy", "2026-10-19", "buy", 100, 10, 100, fees=None),
        fill("sell", "2026-10-21", "sell", 100, 12, 0)]
    _, _, svc, eid = setup(rows)
    result = svc.preview(eid, RULE)
    assert result["status"] == "unknown" and result["actual"] is None
    assert any("费用" in r for r in result["reasons"])


def test_company_action_is_not_crossed_by_raw_close_comparison():
    _, journal, svc, eid = setup()
    original = journal.get_episode
    def with_dividend(episode_id):
        detail = original(episode_id)
        detail["security_financial_events"] = [{"kind": "dividend", "date": "2026-10-20"}]
        return detail
    journal.get_episode = with_dividend
    result = svc.preview(eid, RULE)
    assert result["status"] == "unknown" and result["reference"]["net_pnl"] is None
    assert any("公司行为" in r for r in result["reasons"])


def test_real_same_day_sale_does_not_pass_t_plus_one_check():
    rows = [fill("buy", "2026-10-19", "buy", 100, 10, 100),
        fill("sell", "2026-10-19", "sell", 100, 12, 0, time="14:00:00")]
    _, _, svc, eid = setup(rows)
    result = svc.preview(eid, RULE)
    assert result["status"] == "unknown" and result["actual"] is None
    assert any("可卖依据" in r for r in result["reasons"])


def test_weekend_is_read_from_calendar_not_skipped_by_guessing():
    store, _, svc, eid = setup()
    rows = store.collection("trade_calendar").documents
    for row in rows:
        if row["cal_date"] in {"20261020", "20261021"}:
            row["is_open"] = 0
    store.collection("market_daily_bars").documents[0]["date"] = "2026-10-22"
    store.collection("daily_price_limits").documents[0]["trade_date"] = "20261022"
    result = svc.preview(eid, RULE)
    assert result["reference"]["exit_date"] == "2026-10-22"
    assert result["status"] == "compared"


def test_slippage_cannot_imply_sale_below_the_limit_price():
    _, _, svc, eid = setup()
    result = svc.preview(eid, {"rule": {"kind": "next_trading_day_close", "sell_fee_bps": 10, "slippage_bps": 2000}})
    assert result["status"] == "unknown" and result["reference"]["net_pnl"] is None
    assert any("滑点假设" in r for r in result["reasons"])


def test_unverified_opening_balance_is_an_explicit_gap():
    store, journal, svc, eid = setup()
    original = journal.get_episode
    def unverified(episode_id):
        detail = original(episode_id)
        detail["episode"]["opening_balance_verified"] = False
        return detail
    journal.get_episode = unverified
    result = svc.preview(eid, RULE)
    assert result["status"] == "unknown"
    assert any("券商余额" in r for r in result["reasons"])
    assert store.writes == []
