from datetime import datetime

from shilun.api import (
    _daily_record_date,
    _decorate_daily_baseline_payload,
    _latest_daily_bar_date,
    _request_is_open_daily_session,
)


def test_daily_record_date_accepts_mongo_date_shapes():
    assert _daily_record_date({"date": "20260710"}) == "2026-07-10"
    assert _daily_record_date({"trade_date": "2026-07-10"}) == "2026-07-10"
    assert _daily_record_date({"date": datetime(2026, 7, 10, 15, 0)}) == "2026-07-10"


def test_intraday_baseline_strictly_excludes_session_daily_bar():
    records = [
        {"date": "2026-07-09"},
        {"date": "2026-07-10"},
        {"date": "2026-07-13"},
    ]

    assert _latest_daily_bar_date(records, "2026-07-13") == "2026-07-13"
    assert (
        _latest_daily_bar_date(records, "2026-07-13", strictly_before=True)
        == "2026-07-10"
    )


def test_daily_permission_falls_back_to_latest_available_completed_bar():
    records = [{"date": "2026-07-09"}, {"date": "20260710"}]
    assert _latest_daily_bar_date(records, "2026-07-13") == "2026-07-10"


def test_current_daily_session_is_incomplete_until_post_close_sync_window():
    assert _request_is_open_daily_session(
        "2026-07-13", now=datetime(2026, 7, 13, 10, 30)
    )
    assert not _request_is_open_daily_session(
        "2026-07-13", now=datetime(2026, 7, 13, 15, 15)
    )
    assert not _request_is_open_daily_session(
        "2026-07-10", now=datetime(2026, 7, 13, 10, 30)
    )


def test_baseline_metadata_does_not_mutate_cached_payload():
    cached = {"analysis_date": "2026-07-10", "market_permission": "hold"}
    result = _decorate_daily_baseline_payload(
        cached,
        requested_date="2026-07-13",
        baseline_date="2026-07-10",
        from_cache=True,
        cached_at="2026-07-10T18:00:00",
    )

    assert result["requested_analysis_date"] == "2026-07-13"
    assert result["daily_baseline_date"] == "2026-07-10"
    assert result["baseline_mode"] == "latest_completed_daily"
    assert "requested_analysis_date" not in cached
