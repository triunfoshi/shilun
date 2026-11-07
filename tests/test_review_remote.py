"""Regression coverage for remote reads and dated review HTTP requests."""
from unittest.mock import Mock, patch
import asyncio
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI
from pymongo.errors import NetworkTimeout
import pytest

from shilun.api import review
from shilun.common.db import MongoSnapshotStore, get_mongo_client
from shilun.review.service import DailyReviewService, REVIEW_SOCKET_TIMEOUT_MS


class LocalASGIClient:
    """Exercise real FastAPI parsing/error responses without network or httpx."""

    def __init__(self, app):
        self.app = app

    def request(self, method, url):
        address = urlsplit(url)
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        asyncio.run(self.app({
            "type": "http", "http_version": "1.1", "method": method,
            "scheme": "http", "path": address.path, "raw_path": address.path.encode(),
            "query_string": address.query.encode(), "root_path": "", "headers": [],
            "client": ("127.0.0.1", 1), "server": ("test", 80),
        }, receive, send))
        body = b"".join(message.get("body", b"") for message in messages).decode()
        return SimpleNamespace(status_code=messages[0]["status"], text=body, json=lambda: json.loads(body))

    def get(self, url):
        return self.request("GET", url)

    def post(self, url):
        return self.request("POST", url)


def test_review_connection_budget_is_opt_in():
    with patch("shilun.common.db.MongoClient") as client:
        get_mongo_client("mongodb://localhost")
        assert client.call_args.kwargs["socketTimeoutMS"] == 3000
        get_mongo_client("mongodb://localhost", socket_timeout_ms=REVIEW_SOCKET_TIMEOUT_MS)
        assert client.call_args.kwargs["socketTimeoutMS"] == 60000
    with patch("shilun.common.db.get_mongo_client") as factory, patch.object(MongoSnapshotStore, "ensure_indexes"):
        MongoSnapshotStore("mongodb://localhost", "test", socket_timeout_ms=60000)
        assert factory.call_args.kwargs["socket_timeout_ms"] == 60000


def test_remote_batched_rows_preserve_history_order_and_exclude_indices():
    store = Mock()
    store.collection.return_value.find.return_value = [
        {"ticker": "000002.SZ", "date": "2026-08-12", "close": 12},
        {"ticker": "000001.SH", "date": "2026-08-12", "close": 4000},
        {"ticker": "000001.SZ", "date": "2026-08-12", "close": 11},
        {"ticker": "000001.SZ", "date": "2026-08-11", "close": 10},
    ]
    histories, daily = DailyReviewService(store)._load_bars(
        ["2026-08-11", "2026-08-12"], {"000001.SZ", "000002.SZ"},
    )
    assert [row["close"] for row in histories["000001.SZ"]] == [10, 11]
    assert list(daily["2026-08-12"]) == ["000001.SZ", "000002.SZ"]
    assert list(daily) == ["2026-08-11", "2026-08-12"]
    assert store.collection.return_value.find.call_args.kwargs["batch_size"] == 1000


def test_review_reuses_complete_shared_pool_without_an_external_read():
    store = Mock()
    store.collection.return_value.find.return_value = []
    archived = {'2026-08-12': {'source':'tushare_limit_list_d','stocks':[
        {'ticker':'000001.SZ','name':'甲','boards':2,'industry':'行业A','break_count':0,'seal_time':'093000'}]}}
    with patch('shilun.review.board_data.BoardResearchData') as data:
        data.return_value.pools.return_value = archived
        pools, latest = DailyReviewService(store)._limit_rows(['2026-08-12'], {})
    assert latest == '2026-08-12'
    assert pools[latest][0]['consecutive_count'] == 2
    assert pools[latest][0]['broken_count'] == 0
    assert pools[latest][0]['source'] == 'tushare_limit_list_d'


def test_history_repair_fetches_only_absent_dates():
    from shilun.review.dated_pools import sync_review_board_history
    store = Mock()
    document = {'date':'2026-08-13','kind':'up','complete':True,'stocks':[]}
    with patch('shilun.review.board_data.BoardResearchData') as data, \
         patch('shilun.review.board_data.fetch_historical_pool',return_value=document) as fetch, \
         patch('shilun.review.board_data.today',return_value='2026-09-14'):
        data.return_value.pools.return_value = {'2026-08-12':{'stocks':[]}}
        client = Mock()
        results = sync_review_board_history(store,['2026-08-12','2026-08-13'],client=client)
    assert [c.args for c in fetch.call_args_list] == [(client,'2026-08-13','up'), (client,'2026-08-13','broken')]
    assert results[0]['date'] == '2026-08-13'
    assert results[0]['status'] == 'ok'
    assert document['snapshot_mode'] == 'settled'


def test_incremental_sync_database_failure_is_actionable_json_detail():
    from shilun.api import data_sync, DataSyncHttpRequest
    from fastapi import HTTPException
    with patch('shilun.api.load_config',return_value=SimpleNamespace(mongo_uri='mongodb://test',mongo_db='test')), \
         patch('shilun.common.db.MongoSnapshotStore') as store, \
         patch('shilun.jobs.tushare_sync_job.TushareSyncJob') as job:
        job.return_value.run.side_effect = NetworkTimeout('secret-example-credential')
        with pytest.raises(HTTPException) as caught:
            data_sync(DataSyncHttpRequest(mode='incremental',target_date='2026-09-14'))
    assert caught.value.status_code == 503
    assert '已完成的数据会保留' in caught.value.detail
    assert 'secret-example-credential' not in caught.value.detail
    store.assert_called_once_with('mongodb://test','test',socket_timeout_ms=60000)
    store.return_value.close.assert_called_once()


@pytest.fixture
def endpoint(monkeypatch):
    app = FastAPI()
    app.include_router(review.router)
    store, service = Mock(), Mock()
    monkeypatch.setattr(review, "_open_store", lambda: store)
    monkeypatch.setattr(review, "DailyReviewService", lambda _: service)
    return LocalASGIClient(app), store, service


@pytest.mark.parametrize("method,path,operation", [("get", "dashboard", "get"), ("post", "refresh", "build_and_save")])
def test_selected_date_is_used_and_store_is_closed(endpoint, method, path, operation):
    client, store, service = endpoint
    getattr(service, operation).return_value = {"analysis_date": "2026-08-12"}
    response = getattr(client, method)(f"/api/v1/review/{path}?date=2026-08-12")
    assert response.status_code == 200
    assert getattr(service, operation).call_args.args == ("2026-08-12",)
    store.close.assert_called_once()


@pytest.mark.parametrize("method,path,operation", [("get", "dashboard", "get"), ("post", "refresh", "build_and_save")])
def test_network_error_has_actionable_safe_response(endpoint, method, path, operation):
    client, store, service = endpoint
    getattr(service, operation).side_effect = NetworkTimeout("secret-example-credential")
    response = getattr(client, method)(f"/api/v1/review/{path}?date=2026-08-12")
    assert response.status_code == 503
    assert "检查网络" in response.json()["detail"]
    assert "secret-example-credential" not in response.text
    store.close.assert_called_once()


def test_invalid_calendar_date_does_not_reach_database(endpoint):
    client, store, service = endpoint
    response = client.post("/api/v1/review/refresh?date=2026-02-30")
    assert response.status_code == 422
    service.build_and_save.assert_not_called()


def test_no_date_requests_latest_available_snapshot(endpoint):
    client, _, service = endpoint
    service.get.return_value = {"analysis_date": "2026-08-12"}
    assert client.get("/api/v1/review/dashboard").status_code == 200
    service.get.assert_called_once_with(None, allow_build=False)
