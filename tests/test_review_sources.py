from unittest.mock import Mock

import pytest

from shilun.data.providers.eastmoney_provider import parse_dated_limit_pool
from shilun.review.sources import at_limit, load_futures_history, load_review_sessions, reconstruct_ladder, sync_review_limit_pools


DATES = ["2026-08-10", "2026-08-11", "2026-08-12"]


def evidence():
    bars = {
        DATES[0]: {"A": {"close": 9}, "B": {"close": 9}},
        DATES[1]: {"A": {"close": 10}, "B": {"close": 9}},
        DATES[2]: {"A": {"close": 11}, "B": {"close": 10}},
    }
    limits = {
        DATES[0]: {"A": 10, "B": 10},
        DATES[1]: {"A": 10, "B": 10},
        DATES[2]: {"A": 11, "B": 10},
    }
    return bars, limits


def test_ladder_counts_exact_consecutive_closes_and_source():
    bars, limits = evidence()
    result = reconstruct_ladder(DATES, bars, limits, {"A": {"name": "甲", "sector": "半导体"}})
    assert [(row["level"], row["count"]) for row in result["levels"]] == [(2, 1), (1, 1)]
    assert result["source_date"] == DATES[-1]
    assert result["source"] == "daily_close_and_price_limit"
    assert result["levels"][0]["stocks"][0]["name"] == "甲"
    assert "first_seal_time" not in result["levels"][0]["stocks"][0]


@pytest.mark.parametrize("gap", ["bar", "price_limit", "window_start"])
def test_ladder_excludes_unverifiable_streaks(gap):
    bars, limits = evidence()
    if gap == "bar":
        del bars[DATES[1]]["A"]
    elif gap == "price_limit":
        del limits[DATES[1]]["A"]
    else:
        bars[DATES[0]]["A"]["close"] = 10
    result = reconstruct_ladder(DATES, bars, limits, {})
    assert result["highest_board"] == 1
    assert result["unresolved_count"] == 1
    assert result["status"] == "partial"
    assert result["unresolved_stocks"][0]["ticker"] == "A"


def test_entire_missing_market_day_must_not_be_skipped():
    bars, limits = evidence()
    del bars[DATES[1]]
    result = reconstruct_ladder(DATES, bars, limits, {})
    assert result["levels"] == []
    assert result["unresolved_count"] == 2
    assert {item["minimum_board"] for item in result["unresolved_stocks"]} == {1}


def test_calendar_uses_real_sessions_not_available_bar_dates():
    store = Mock()
    store.find_trade_calendar.return_value = [
        {"cal_date": "20260807", "is_open": 1}, {"cal_date": "20260808", "is_open": 0},
        {"cal_date": "20260809", "is_open": 0},
        *[{"cal_date": date.replace("-", ""), "is_open": 1} for date in DATES],
    ]
    assert load_review_sessions(store, DATES[-1], "2026-08-07") == ["2026-08-07", *DATES]
    store.find_trade_calendar.return_value = [{"cal_date": "20260810", "is_open": 1},
                                               {"cal_date": "20260812", "is_open": 1}]
    # The missing calendar day is an uncertainty boundary, not a holiday.
    assert load_review_sessions(store, DATES[-1], DATES[0]) == [DATES[-1]]


def test_confirmed_empty_pool_is_persisted_distinctly():
    import pandas as pd
    from shilun.jobs.limit_pool_sync_job import LimitPoolSyncJob
    store, client = Mock(), Mock()
    store.collection.return_value.find_one.return_value = None
    client.fetch_zt_pool.return_value = pd.DataFrame()
    assert LimitPoolSyncJob(store, client).sync_limit_up_pool("20260812") == 0
    assert store.upsert_sync_state.call_args.args[0]["status"] == "empty_confirmed"


def test_no_limits_is_unavailable_not_zero():
    bars, _ = evidence()
    result = reconstruct_ladder(DATES, bars, {}, {})
    assert result["status"] == "unavailable"
    assert result["levels"] == []
    assert result["price_limit_coverage"] == 0


def test_verified_empty_is_different_from_unavailable():
    bars, limits = evidence()
    bars[DATES[-1]] = {"A": {"close": 10.5}, "B": {"close": 9.9}}
    assert reconstruct_ladder(DATES, bars, limits, {})["status"] == "empty"


@pytest.mark.parametrize("price,limit,expected", [(None, 10, None), (float("nan"), 10, None),
                                                  (10, float("inf"), None), (10, 0, None),
                                                  (9.99, 10, False), (10.0000001, 10, True),
                                                  (12, 12, True), (10.5, 10.5, True)])
def test_limit_match_uses_price_not_a_fixed_percent(price, limit, expected):
    assert at_limit(price, limit) is expected


class PoolCollection:
    def __init__(self, cached=()):
        self.cached = cached

    def find_one(self, query, projection):
        return {"ticker": "000001.SZ"} if any(date in self.cached for date in query["trade_date"]["$in"]) else None


def test_explicit_sync_only_requests_missing_last_two_dates_and_keeps_success():
    store = Mock()
    store.collection.return_value = PoolCollection()
    job = Mock()
    job.sync_limit_up_pool.side_effect = [RuntimeError("unavailable"), 12]
    result = sync_review_limit_pools(store, DATES, job=job)
    assert [call.args[0] for call in job.sync_limit_up_pool.call_args_list] == ["20260811", "20260812"]
    assert [row["status"] for row in result] == ["unavailable", "ok"]
    assert "unavailable" not in result[0].get("error", "")


def test_sync_accepts_hyphenated_cache_date_without_refetch():
    store = Mock()
    store.collection.return_value = PoolCollection([DATES[-1]])
    job = Mock()
    result = sync_review_limit_pools(store, [DATES[-1]], job=job)
    job.sync_limit_up_pool.assert_not_called()
    assert result[0]["status"] == "cached"


def test_futures_window_keeps_missing_days_and_filters_wrong_data_date():
    store = Mock()
    store.collection.return_value.find.return_value = [
        {"analysis_date": DATES[0], "payload": {"status": "ok", "products": [
            {"product": "IF", "status": "ok", "data_date": DATES[0], "top20_long": 10, "citic_long": None}]}},
        {"analysis_date": DATES[1], "payload": {"status": "ok", "products": [
            {"product": "IF", "status": "ok", "data_date": "2026-08-13", "top20_long": 15}]}},
    ]
    result = load_futures_history(store, DATES[-1], [*DATES, "2026-08-13"])
    assert [day["analysis_date"] for day in result["history"]] == DATES
    assert result["history_available_count"] == 1
    assert result["source_date"] == DATES[0]
    assert result["products"] == []
    assert result["history"][1]["products"] == []
    assert result["history"][0]["products"][0]["citic_long"] is None


def test_pool_parser_rejects_latest_date_substituted_for_history():
    with pytest.raises(ValueError, match="日期"):
        parse_dated_limit_pool({"rc": 0, "data": {"qdate": 20260904, "pool": []}}, "20260812")


def test_pool_parser_preserves_empty_only_for_confirmed_date():
    assert parse_dated_limit_pool({"rc": 0, "data": {"qdate": 20260812, "pool": []}}, "20260812").empty
    with pytest.raises(ValueError):
        parse_dated_limit_pool({"rc": 0, "data": None}, "20260812")


def test_pool_parser_uses_named_fields_and_board_count():
    row = {"lbc": 3, "c": "000001", "n": "示例", "p": 12340, "fbt": 93000, "hybk": "半导体", "zttj": {"days": 5, "ct": 3}}
    df = parse_dated_limit_pool({"rc": 0, "data": {"qdate": 20260812, "pool": [row]}}, "20260812")
    assert df.iloc[0]["连板数"] == 3
    assert df.iloc[0]["最新价"] == 12.34
    assert df.iloc[0]["首次封板时间"] == "093000"
    assert df.iloc[0]["涨停统计"] == "5/3"  # Not mistaken for consecutive boards.
    del row["lbc"]
    with pytest.raises(ValueError, match="连板数"):
        parse_dated_limit_pool({"rc": 0, "data": {"qdate": 20260812, "pool": [row]}}, "20260812")


def test_service_reads_both_pool_date_formats():
    from shilun.review.service import DailyReviewService
    store = Mock()
    store.collection.return_value.find.return_value = [{"ticker": "000001", "trade_date": "2026-08-12", "consecutive_count": 2}]
    rows, source_date = DailyReviewService(store)._limit_rows([DATES[-1]], {})
    assert source_date == DATES[-1]
    assert rows[DATES[-1]][0]["ticker"] == "000001.SZ"
    query = store.collection.return_value.find.call_args.args[0]
    assert set(query["trade_date"]["$in"]) == {"20260812", DATES[-1]}
