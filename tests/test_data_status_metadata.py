from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
from pymongo.errors import ExecutionTimeout

from shilun.market.data_status import (
    cffex_dataset, dataset_usage, date_text, enrich_datasets, scheduler_status, storage_summary,
)

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 2, 16, 0, tzinfo=TZ)
DAY = "2026-11-02"
JOBS = [
    {"id": "tushare_close_1715", "schedule": "周一至周五 17:15", "dataset_keys": ["stock_daily_bars", "daily_review"]},
    {"id": "minute_bars_1515", "schedule": "周一至周五 15:15", "dataset_keys": ["minute_bars"]},
    {"id": "cffex_positions_1740", "schedule": "周一至周五 17:40", "dataset_keys": ["cffex_positions"]},
]


def store_with(receipts):
    store = Mock()
    store.find_sync_state.side_effect = lambda *, dataset, scope="default": receipts.get((dataset, scope))
    return store


def manifest(now=NOW, jobs=JOBS):
    return {"status": "running", "updated_at": now.isoformat(), "timezone": "Asia/Shanghai", "location": "ECS", "jobs": jobs}


def enrich(rows, receipts=None, now=NOW):
    store = store_with({("cloud_scheduler_manifest", "active"): manifest(), **(receipts or {})})
    with patch("shilun.market.data_status.configured_jobs", return_value=JOBS), \
         patch("shilun.market.data_status.storage_summary", return_value={"status": "available", "earliest_date": "2026-07-01", "latest_date": DAY, "record_count": None, "date_count": None}):
        scheduler = enrich_datasets(store, rows, DAY, benchmark_ticker="000001.SH", benchmark_count=123, stock_basic_count=5595, now=now)
    return rows, scheduler


@pytest.mark.parametrize("value,expected", [("20261102", DAY), (DAY, DAY), (NOW, DAY), ("not-a-date", None), ("20261332", None)])
def test_normalize_storage_date_without_inventing_dates(value, expected):
    assert date_text(value) == expected


def test_storage_span_uses_scoped_bounded_queries_and_exact_counts():
    store, col = Mock(), Mock()
    store.collection.return_value = col
    col.find_one.side_effect = [{"trade_date": "20260701"}, {"trade_date": "20261102"}]
    col.count_documents.return_value = 1200
    col.aggregate.return_value = [{"count": 65}]
    result = storage_summary(store, "minute_bars_daily", "trade_date", query={"interval": "5"}, count_dates=True)
    assert (result["earliest_date"], result["latest_date"], result["record_count"], result["date_count"]) == ("2026-07-01", DAY, 1200, 65)
    assert "不代表" in result["note"]
    for call in col.find_one.call_args_list:
        assert call.args[0]["interval"] == "5"
        assert call.kwargs["max_time_ms"] == 350
    assert col.count_documents.call_args.kwargs["maxTimeMS"] == 350
    assert col.aggregate.call_args.kwargs["maxTimeMS"] == 350
    col.estimated_document_count.assert_not_called()
    col.update_one.assert_not_called()
    col.create_index.assert_not_called()


def test_storage_timeout_is_unknown_instead_of_empty():
    store = Mock()
    store.collection.return_value.find_one.side_effect = ExecutionTimeout("private server text")
    result = storage_summary(store, "market_daily_bars", "date")
    assert result["status"] == "unknown"
    assert result["record_count"] is None
    assert "private" not in str(result)


def test_count_timeout_keeps_verified_span_without_fabricated_count():
    store = Mock()
    col = store.collection.return_value
    col.find_one.side_effect = [{"date": "2026-07-01"}, {"date": DAY}]
    col.count_documents.side_effect = ExecutionTimeout("bounded")
    result = storage_summary(store, "market_daily_bars", "date")
    assert result["status"] == "available" and result["latest_date"] == DAY
    assert result["record_count"] is None


def test_empty_storage_is_explicit_zero_and_master_snapshot_has_no_date_history():
    store = Mock()
    store.collection.return_value.find_one.return_value = None
    empty = storage_summary(store, "breakout_events", "breakout_date")
    assert empty["status"] == "empty" and empty["date_count"] == empty["record_count"] == 0
    master = storage_summary(store, "stock_basic", None, record_count=5595)
    assert master["earliest_date"] is None and "不保存逐日版本" in master["note"]


def test_local_config_is_not_cloud_deployment_proof():
    store = store_with({})
    with patch("shilun.market.data_status.configured_jobs", return_value=JOBS):
        result = scheduler_status(store, NOW)
    assert result["status"] == "unknown"
    assert result["jobs"] == [] and len(result["configured_jobs"]) == 3


@pytest.mark.parametrize("location", [None, "未声明", "Mac"])
def test_local_or_undeclared_manifest_is_not_cloud_deployment_proof(location):
    store = store_with({("cloud_scheduler_manifest", "active"): {**manifest(), "location": location}})
    with patch("shilun.market.data_status.configured_jobs", return_value=JOBS):
        assert scheduler_status(store, NOW)["status"] == "unknown"


@pytest.mark.parametrize("stamp,expected", [(NOW - timedelta(minutes=21), "stale"), (NOW + timedelta(minutes=3), "unknown")])
def test_stale_or_future_heartbeat_never_claims_enabled(stamp, expected):
    store = store_with({("cloud_scheduler_manifest", "active"): manifest(stamp)})
    with patch("shilun.market.data_status.configured_jobs", return_value=JOBS):
        result = scheduler_status(store, NOW)
    assert result["status"] == expected
    assert result["jobs"] == []
    assert result["checked_at"] == NOW.isoformat()
    assert bool(result["last_known_jobs"]) is (expected == "stale")


def test_expired_heartbeat_preserves_last_cloud_schedule_without_invalidating_data_usage():
    stamp = NOW - timedelta(minutes=21)
    rows, scheduler = enrich([{"key": "minute_bars", "value": 120, "ok": True, "detail": "120窗口"}], {
        ("cloud_scheduler_manifest", "active"): manifest(stamp),
    })
    row = rows[0]
    assert scheduler["status"] == "stale"
    assert scheduler["last_verified_at"] == stamp.isoformat()
    assert row["schedule"]["status"] == "stale"
    assert row["schedule"]["jobs_origin"] == "cloud_last_known"
    assert row["schedule"]["jobs"][0]["id"] == "minute_bars_1515"
    assert row["status"] == "ready" and row["ok"] is True
    assert row["usage"]["status"] == "integrated"
    assert "不代表已有数据失效" in row["schedule"]["note"]


def test_stale_manifest_does_not_claim_a_new_local_job_was_ever_enabled():
    rows, _ = enrich([{"key": "cffex_positions", "value": 4, "ok": True, "detail": "4/4"}], {
        ("cloud_scheduler_manifest", "active"): manifest(NOW - timedelta(minutes=21), jobs=JOBS[:2]),
    })
    row = rows[0]
    assert row["schedule"]["status"] == "configured"
    assert row["schedule"]["jobs_origin"] == "local_config"
    assert row["schedule"]["last_verified_at"] is None
    assert row["usage"]["status"] == "integrated" and row["ok"] is True


def test_malformed_cloud_job_list_does_not_establish_deployment():
    store = store_with({("cloud_scheduler_manifest", "active"): manifest(jobs=[None, {}, {"id": 123}])})
    with patch("shilun.market.data_status.configured_jobs", return_value=JOBS):
        result = scheduler_status(store, NOW)
    assert result["status"] == "unknown"
    assert result["last_known_jobs"] == []


def test_new_futures_job_stays_configured_when_old_cloud_manifest_lacks_it():
    receipts = {("cloud_scheduler_manifest", "active"): manifest(jobs=JOBS[:2])}
    rows, _ = enrich([{"key": "cffex_positions", "value": 0, "ok": False, "detail": "0/4"}], receipts)
    assert rows[0]["schedule"]["status"] == "configured"
    assert rows[0]["status"] == "missing"
    assert "当前云端任务清单中尚未启用" in rows[0]["schedule"]["note"]


def test_today_before_close_is_scheduled_and_morning_previous_day_failure_not_today_failure():
    rows, _ = enrich([{"key": "stock_daily_bars", "value": 0, "ok": False, "detail": "0条"}], {
        ("daily_data_pipeline", "latest"): {"status": "failed", "requested_date": "2026-11-01", "stage": "daily_sync", "updated_at": "2026-11-02T07:03:14+08:00"},
    })
    assert rows[0]["status"] == "scheduled" and rows[0]["ok"] is False


def test_today_failed_close_pipeline_does_not_disappear_behind_old_tushare_success():
    rows, _ = enrich([{"key": "stock_daily_bars", "value": 0, "ok": False, "detail": "0条"}], {
        ("tushare_daily", "SSE"): {"status": "success", "target_date": "2026-10-30"},
        ("daily_data_pipeline", "latest"): {"status": "failed", "requested_date": DAY, "stage": "daily_sync", "error_type": "ReadTimeout"},
    }, now=NOW.replace(hour=18))
    assert rows[0]["status"] == "failed" and rows[0]["last_run"]["error_type"] == "ReadTimeout"


def test_partial_minutes_are_not_green_and_last_fetch_count_not_claimed_as_total():
    rows, _ = enrich([{"key": "minute_bars", "value": 120, "ok": True, "detail": "120个窗口"}], {
        ("minute_bars_daily", "20261102_5min"): {"status": "partial", "row_count": 120, "candidate_count": 124, "missing_count": 126, "failed_count": 6, "updated_at": NOW.isoformat()},
    })
    row = rows[0]
    assert row["status"] == "partial" and row["ok"] is False
    assert row["last_run"]["failed_count"] == 6 and row["last_run"]["fetched_count"] == 120
    assert "不是整日累计" in row["last_run"]["detail"]
    assert "不含全市场" in row["coverage"]["scope"]


def test_sparse_stock_cross_section_is_partial_not_whole_market_ready():
    rows, _ = enrich([{"key": "stock_daily_bars", "value": 1, "ok": True, "detail": "1条"}])
    assert rows[0]["status"] == "partial" and rows[0]["ok"] is False


def test_failed_cffex_retry_does_not_invalidate_an_audited_stored_snapshot():
    rows, _ = enrich([{"key": "cffex_positions", "value": 4, "ok": True, "detail": "4/4"}], {
        ("cffex_positions_daily", DAY): {"status": "partial", "requested_date": DAY, "successful_count": 2},
    })
    assert rows[0]["status"] == "ready" and rows[0]["last_run"]["status"] == "partial"


def test_no_event_execution_watermark_is_not_claimed_healthy_from_existing_rows():
    rows, _ = enrich([{"key": "breakout_events", "value": 12, "ok": True, "detail": "共12条"}])
    assert rows[0]["status"] == "unknown" and rows[0]["schedule"]["status"] == "manual"
    assert rows[0]["usage"]["status"] == "optional"


def test_function_integration_is_independent_of_producer_and_dataset_readiness():
    rows, _ = enrich([{"key": key, "value": 0, "ok": False, "detail": "无所选日归档"}
                      for key in ("minute_bars", "topic_perspectives", "cffex_positions", "limit_up_pool")], {
        ("cloud_scheduler_manifest", "active"): {},
    })
    for row in rows:
        assert row["status"] == "missing"
        assert row["usage"]["status"] == "integrated"
        assert row["usage"]["label"] == "已接入"
        assert row["usage"]["basis"] == "source_contract"
        assert row["usage"]["consumers"]
        assert row["schedule"]["status"] != "enabled"


def test_failed_raw_pool_attempt_keeps_stored_data_and_consumers_available():
    rows, _ = enrich([{"key": "limit_up_pool", "value": 125, "ok": True, "detail": "125只"}], {
        ("eastmoney_limit_up_pool", "20261102"): {"status": "success", "row_count": 125},
        ("eastmoney_limit_up_pool_runs", "20261102"): {"status": "failed", "requested_date": DAY, "error_type": "ReadTimeout", "row_count": 0},
    })
    row = rows[0]
    assert row["last_run"]["dataset"] == "eastmoney_limit_up_pool_runs"
    assert row["last_run"]["status"] == "failed"
    assert row["status"] == "ready" and row["ok"] is True
    assert row["usage"]["status"] == "integrated"


def test_raw_pool_execution_receipt_falls_back_to_manual_canonical_receipt():
    rows, _ = enrich([{"key": "limit_up_pool", "value": 125, "ok": True, "detail": "125只"}], {
        ("eastmoney_limit_up_pool", "20261102"): {"status": "success", "row_count": 125},
    })
    assert rows[0]["last_run"]["dataset"] == "eastmoney_limit_up_pool"
    assert rows[0]["last_run"]["fetched_count"] == 125


def test_usage_unknown_and_mutable_consumer_lists_do_not_invent_live_traffic():
    unknown = dataset_usage("not_a_known_dataset")
    assert unknown["status"] == "unknown" and unknown["consumers"] == []
    minute = dataset_usage("minute_bars")
    minute["consumers"].clear()
    assert dataset_usage("minute_bars")["consumers"]
    assert "点击" not in str(dataset_usage("minute_bars"))
    assert "每次读取" in dataset_usage("cffex_positions")["note"]


@pytest.mark.parametrize("wrong_date,expected", [(False, True), (True, False)])
def test_cffex_readiness_requires_all_four_audited_products_on_selected_date(wrong_date, expected):
    store = Mock()
    products = [{"product": code, "contract": f"{code}2611", "status": "ok", "source_consistency": "matched", "data_date": "2026-10-30" if wrong_date and code == "IM" else DAY,
                 **{key: 5 for key in ("top20_long", "top20_short", "top20_long_change", "top20_short_change", "citic_long", "citic_short", "citic_long_change", "citic_short_change")}}
                for code in ("IF", "IH", "IC", "IM")]
    store.collection.return_value.find_one.return_value = {"payload": {"status": "ok", "target_date": DAY, "products": products}}
    row = cffex_dataset(store, DAY)
    assert row["ok"] is expected
    assert row["value"] == (4 if expected else 3)
    store.collection.return_value.update_one.assert_not_called()


def test_date_matched_cffex_labels_without_seat_audit_are_not_four_of_four():
    store = Mock()
    store.collection.return_value.find_one.return_value = {"payload": {"status": "ok", "target_date": DAY, "products": [
        {"product": code, "contract": f"{code}2611", "status": "ok", "data_date": DAY} for code in ("IF", "IH", "IC", "IM")]}}
    row = cffex_dataset(store, DAY)
    assert row["ok"] is False and row["value"] == 0
