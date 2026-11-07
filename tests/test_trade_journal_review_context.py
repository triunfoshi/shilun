"""Review date defaults must use exact buys and a complete exchange calendar."""
from copy import deepcopy
from datetime import datetime, date, timedelta

from shilun.trade_journal.review_context import JournalReviewContextService
from shilun.trade_journal.service import TradeJournalService, TZ
from test_trade_journal import FakeStore, bundle, fill, import_ready


def setup_context(rows=None):
    store = FakeStore()
    journal = TradeJournalService(store, now_factory=lambda: datetime(2026, 10, 27, 17, tzinfo=TZ))
    import_ready(journal, bundle(rows or [fill("b", "2026-10-23", "buy", 100, 10, 100)]))
    for offset in range(5):
        day = date(2026, 10, 23) + timedelta(days=offset)
        store.collection("trade_calendar").documents.append({"exchange": "SSE", "cal_date": day.strftime("%Y%m%d"),
                                                            "is_open": int(day.weekday() < 5)})
    episode_id = journal.list_episodes()["items"][0]["episode_id"]
    return store, journal, JournalReviewContextService(store, journal=journal), episode_id


def test_next_trading_day_skips_confirmed_weekend_and_never_claims_coverage():
    store, journal, context, episode_id = setup_context()
    before = deepcopy(store.writes)
    result = context.get(episode_id)
    assert result["as_of_date"] == "2026-10-27"
    assert result["calendar_days_since_entry"] == 4
    assert result["buy_fills"][0]["next_trading_day"] == "2026-10-26"
    assert result["buy_fills"][0]["earliest_sell_date"] == "2026-10-26"
    assert result["freshness"]["gap_trading_days"] == 2
    assert result["freshness"]["latest_fill_date"] == "2026-10-23"
    assert result["freshness"]["latest_imported_at"].startswith("2026-10-27")
    assert result["freshness"]["statement_coverage_end"] is None
    assert result["freshness"]["needs_statement_confirmation"] is True
    assert result["import_automation"]["enabled"] is False
    assert result["read_only"] and store.writes == before


def test_each_addition_has_its_own_next_day():
    _, _, context, episode_id = setup_context([
        fill("b", "2026-10-23", "buy", 100, 10, 100),
        fill("a", "2026-10-26", "buy", 100, 11, 200),
    ])
    rows = context.get(episode_id)["buy_fills"]
    assert [(r["fill_id"], r["next_trading_day"]) for r in rows] == [("b", "2026-10-26"), ("a", "2026-10-27")]


def test_missing_or_duplicate_calendar_is_unknown():
    store, _, context, episode_id = setup_context()
    calendar = store.collection("trade_calendar").documents
    calendar[:] = [r for r in calendar if r["cal_date"] != "20261024"]
    result = context.get(episode_id)
    assert result["buy_fills"][0]["next_trading_day"] is None
    assert result["freshness"]["gap_trading_days"] is None
    calendar.append({"exchange": "SSE", "cal_date": "20261024", "is_open": 0})
    calendar.append({"exchange": "SSE", "cal_date": "20261026", "is_open": 1})
    assert context.get(episode_id)["buy_fills"][0]["next_trading_day"] is None


def test_declared_holiday_not_weekday_is_authoritative():
    store, _, context, episode_id = setup_context()
    next(r for r in store.collection("trade_calendar").documents if r["cal_date"] == "20261026")["is_open"] = 0
    assert context.get(episode_id)["buy_fills"][0]["next_trading_day"] == "2026-10-27"


def test_account_scope_does_not_take_another_accounts_recent_fill():
    _, journal, context, episode_id = setup_context()
    data = bundle([fill("other", "2026-10-27", "buy", 100, 10, 100)])
    data["account_alias"] = "another"
    import_ready(journal, data)
    assert context.get(episode_id)["freshness"]["latest_fill_date"] == "2026-10-23"


def test_prompt_date_is_current_when_last_fill_matches_latest_exchange_day():
    _, _, context, episode_id = setup_context([fill("today", "2026-10-27", "buy", 100, 10, 100)])
    f = context.get(episode_id)["freshness"]
    assert f["latest_expected_trade_date"] == "2026-10-27"
    assert f["date_is_current"] is True
    assert f["statement_coverage_end"] is None


def test_weekend_latest_date_uses_calendar_and_missing_calendar_stays_unknown():
    store, journal, _, episode_id = setup_context()
    context = JournalReviewContextService(store, journal=journal,
        now_factory=lambda: datetime(2026, 10, 25, 17, tzinfo=TZ))
    f = context.get(episode_id)["freshness"]
    assert f["latest_expected_trade_date"] == "2026-10-23" and f["date_is_current"] is True
    store.collection("trade_calendar").documents[:] = [r for r in store.collection("trade_calendar").documents if r["cal_date"] != "20261025"]
    f = context.get(episode_id)["freshness"]
    assert f["latest_expected_trade_date"] is None and f["date_is_current"] is None


def test_internal_market_snapshot_import_does_not_advance_statement_import_date():
    _, journal, context, episode_id = setup_context()
    original = journal._committed_batches()
    timestamp = context.get(episode_id)["freshness"]["latest_imported_at"]
    journal._committed_batches = lambda: original + [{"account_alias":"primary", "recorded_at":"2026-11-03T16:00:00+08:00", "source_ids":["market-check-example"], "fill_ids":[], "financial_event_ids":[]}]
    assert context.get(episode_id)["freshness"]["latest_imported_at"] == timestamp
