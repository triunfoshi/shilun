from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from shilun.data.providers.eastmoney_provider import EastmoneyClient, EastmoneyConfig
from shilun.jobs.cloud_scheduler import build_scheduler, scheduler_manifest
from shilun.jobs.limit_pool_sync_job import DATASET, RUN_DATASET, LimitPoolSyncJob, main, run


DAY = "2026-11-02"
COMPACT = "20261102"
TZ = ZoneInfo("Asia/Shanghai")


class Collection:
    def __init__(self):
        self.rows = []
        self.index_calls = []

    def create_index(self, keys, **kwargs):
        self.index_calls.append((keys, kwargs))

    def find_one(self, query, projection=None):
        return next((row for row in self.rows if all(row.get(k) == v for k, v in query.items())), None)


class Store:
    def __init__(self, calendar=None):
        self.collections = {name: Collection() for name in ("limit_up_pool", "sync_state")}
        self.states, self.writes = [], []
        self.calendar = calendar or []

    def collection(self, name):
        return self.collections[name]

    def upsert_sync_state(self, state):
        self.states.append(deepcopy(state))

    def find_trade_calendar(self, **kwargs):
        assert kwargs["exchange"] == "SSE"
        return self.calendar

    def find_sync_state(self, *, dataset, scope):
        return next((state for state in reversed(self.states)
                     if state["dataset"] == dataset and state["scope"] == scope), None)

    def _upsert_payloads(self, name, rows, keys):
        assert name == "limit_up_pool" and keys == ("ticker", "trade_date")
        self.writes.append(deepcopy(rows))
        for row in rows:
            existing = self.collections[name].find_one({key: row[key] for key in keys})
            if existing is None:
                self.collections[name].rows.append(deepcopy(row))
            else:
                existing.update(deepcopy(row))
        return len(rows)


def client_for(rows):
    return SimpleNamespace(fetch_zt_pool=Mock(return_value=pd.DataFrame(rows)))


def row(code="000017", day=COMPACT):
    return {"ticker": code, "trade_date": day, "consecutive_count": 1}


def test_dated_pool_is_idempotent_and_does_not_rebuild_reviews():
    store, client = Store(), client_for([row()])
    for _ in range(2):
        state = run(store, DAY, client=client)
        assert state["status"] == "success"
        assert state["scope"] == COMPACT and state["requested_date"] == DAY
        assert state["row_count"] == 1
    assert len(store.collection("limit_up_pool").rows) == 1
    client.fetch_zt_pool.assert_called_with(COMPACT)
    assert store.states[-1]["calendar_source"] == "calendar_unknown"
    assert set(store.collections) == {"limit_up_pool", "sync_state"}


@pytest.mark.parametrize("rows", [
    [row(day="20261030")], [row(code="000017.SZ")], [row(code="000000")],
    [row(), row()], [{"ticker": "000017"}],
])
def test_malformed_or_wrong_day_rows_cannot_change_existing_archive(rows):
    store = Store()
    store.collection("limit_up_pool").rows = [row()]
    original = deepcopy(store.collection("limit_up_pool").rows)
    result = run(store, DAY, client=client_for(rows))
    assert result["status"] == "failed"
    assert result["error_type"] == "EastmoneyFetchError"
    assert store.writes == []
    assert store.collection("limit_up_pool").rows == original


def dated_client(payload):
    response = Mock()
    response.json.return_value = payload
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.return_value = response
    return EastmoneyClient(EastmoneyConfig(retries=0)), session


def test_real_provider_confirmed_empty_requires_matching_response_date():
    store = Store()
    client, session = dated_client({"rc": 0, "data": {"qdate": COMPACT, "pool": []}})
    with patch("shilun.data.providers.eastmoney_provider.requests.Session", return_value=session):
        result = run(store, DAY, client=client)
    assert result["status"] == "empty_confirmed"
    assert result["row_count"] == 0 and store.writes == []
    assert session.get.call_args.kwargs["params"]["date"] == COMPACT


@pytest.mark.parametrize("data", [
    {"qdate": "20261030", "pool": []}, {"pool": []},
    {"qdate": COMPACT}, {"qdate": COMPACT, "pool": None},
])
def test_invalid_empty_envelope_never_records_confirmed_empty(data):
    store = Store()
    client, session = dated_client({"rc": 0, "data": data})
    with patch("shilun.data.providers.eastmoney_provider.requests.Session", return_value=session):
        result = run(store, DAY, client=client)
    assert result["status"] == "failed"
    assert not any(state["status"] == "empty_confirmed" for state in store.states)
    assert store.writes == []


def test_empty_retry_cannot_relabel_nonempty_archive_as_empty():
    store = Store()
    run(store, DAY, client=client_for([row()]))
    original = deepcopy(store.collection("limit_up_pool").rows)
    result = run(store, DAY, client=client_for([]))
    assert result["status"] == "failed"
    assert store.collection("limit_up_pool").rows == original


def test_fetch_failure_is_sanitized_and_keeps_archived_rows():
    store = Store()
    run(store, DAY, client=client_for([row()]))
    original_watermark = deepcopy(store.find_sync_state(dataset=DATASET, scope=COMPACT))
    client = client_for([])
    client.fetch_zt_pool.side_effect = TimeoutError("private URL token must not enter logs or receipt")
    result = run(store, DAY, client=client)
    assert result["status"] == "failed" and result["error_type"] == "TimeoutError"
    assert "private" not in str(result)
    assert store.collection("limit_up_pool").rows == [row()]
    assert store.find_sync_state(dataset=DATASET, scope=COMPACT) == original_watermark
    assert store.find_sync_state(dataset=RUN_DATASET, scope=COMPACT)["status"] == "failed"


@pytest.mark.parametrize("day,calendar", [
    (DAY, [{"cal_date": COMPACT, "is_open": 0}]), ("2026-11-01", []),
])
def test_closed_calendar_and_weekend_skip_without_fetch(day, calendar):
    store, client = Store(calendar), client_for([row()])
    result = run(store, day, client=client)
    assert result["status"] == "skipped_closed"
    client.fetch_zt_pool.assert_not_called()
    assert store.writes == []


@pytest.mark.parametrize("invalid", ["20261332", "2026112", "2026--11-02", "2026-1-2"])
def test_invalid_request_date_is_rejected_before_any_io(invalid):
    store, client = Store(), client_for([row()])
    with pytest.raises(ValueError):
        LimitPoolSyncJob(store, client).sync_limit_up_pool(invalid)
    assert not any(col.index_calls for col in store.collections.values())
    client.fetch_zt_pool.assert_not_called()


def test_supported_iso_row_date_is_stored_under_the_canonical_partition():
    store = Store()
    result = run(store, DAY, client=client_for([row(code=600000, day=DAY)]))
    assert result["status"] == "success"
    assert store.collection("limit_up_pool").rows[0]["ticker"] == "600000"
    assert store.collection("limit_up_pool").rows[0]["trade_date"] == COMPACT


def test_data_only_scheduler_registers_raw_pool_independently_and_publishes_actual_jobs():
    with patch("shilun.jobs.cloud_scheduler._run") as launch:
        scheduler = build_scheduler(data_only=True, location="ECS")
        assert not launch.called
        manifest = scheduler_manifest(scheduler, data_only=True, location="ECS", now=datetime(2026, 11, 2, 16, tzinfo=TZ))
        by_id = {item["id"]: item for item in manifest["jobs"]}
        assert len(by_id) == 15
        for hour in (15, 17, 19):
            identifier = f"limit_pool_{hour}35"
            assert by_id[identifier]["module"] == "shilun.jobs.limit_pool_sync_job"
            assert by_id[identifier]["dataset_keys"] == ["limit_up_pool"]
            assert by_id[identifier]["hour"] == str(hour) and by_id[identifier]["minute"] == "35"
            assert by_id[identifier]["day_of_week"] == "mon-fri"
            assert by_id[identifier]["executor"] == "evidence"
            scheduler.get_job(identifier).func()
            assert launch.call_args.args == (identifier, "shilun.jobs.limit_pool_sync_job")


def test_cli_database_failure_does_not_print_private_connection_error(capsys):
    config = SimpleNamespace(mongo_uri="mongodb://unit-test.invalid", mongo_db="test_only")
    with patch("shilun.common.config.load_config", return_value=config), \
         patch("shilun.common.db.MongoSnapshotStore", side_effect=TimeoutError("private uri")):
        assert main(["--date", DAY]) == 1
    output = capsys.readouterr().out
    assert "TimeoutError" in output and "private" not in output
