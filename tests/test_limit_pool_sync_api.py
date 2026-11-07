from unittest.mock import Mock, patch

import pandas as pd
import pytest
from fastapi import HTTPException
from pymongo.errors import ServerSelectionTimeoutError

from shilun.api import DataDomainSyncHttpRequest, data_status, data_sync_limit_pool
from shilun.data.providers.eastmoney_provider import EastmoneyFetchError
from shilun.jobs.limit_pool_sync_job import LimitPoolSyncJob


@pytest.fixture
def config():
    with patch('shilun.api.load_config', return_value=Mock(mongo_uri='mongodb://test', mongo_db='shilun')):
        yield


@pytest.mark.parametrize('stage', ['constructor', 'ping'])
def test_connection_failure_prevents_provider_and_writes(config, stage):
    store = Mock()
    with patch('shilun.common.db.MongoSnapshotStore', return_value=store) as factory, \
         patch('shilun.jobs.limit_pool_sync_job.LimitPoolSyncJob') as job:
        error = ServerSelectionTimeoutError('private connection detail')
        if stage == 'constructor':
            factory.side_effect = error
        else:
            store.db.command.side_effect = error
        with pytest.raises(HTTPException) as exc:
            data_sync_limit_pool(DataDomainSyncHttpRequest(target_date='2026-09-23'))
        assert exc.value.status_code == 503
        assert '尚未抓取或写入' in exc.value.detail
        assert 'private' not in exc.value.detail
        job.assert_not_called()
        if stage == 'ping':
            store.close.assert_called_once()


@pytest.mark.parametrize('count,status', [(58, 'success'), (0, 'empty_confirmed')])
def test_success_uses_cloud_timeout_and_only_job_indexes(config, count, status):
    store = Mock()
    with patch('shilun.common.db.MongoSnapshotStore', return_value=store) as factory, \
         patch('shilun.jobs.limit_pool_sync_job.LimitPoolSyncJob') as job:
        job.return_value.sync_limit_up_pool.return_value = count
        result = data_sync_limit_pool(DataDomainSyncHttpRequest(target_date='2026-09-23'))
        assert result == {'target_date': '2026-09-23', 'count': count, 'source': 'eastmoney', 'status': status}
        factory.assert_called_once_with('mongodb://test', 'shilun', socket_timeout_ms=30_000, initialize_indexes=False)
        store.db.command.assert_called_once_with('ping')
        job.return_value.sync_limit_up_pool.assert_called_once_with('20260923')
        store.close.assert_called_once()


@pytest.mark.parametrize('error,code,message', [
    (ServerSelectionTimeoutError('private'), 503, '部分记录可能已保存'),
    (EastmoneyFetchError('zt_pool', ValueError('private')), 502, '未写入涨停池'),
])
def test_database_and_provider_errors_are_distinct(config, error, code, message):
    store = Mock()
    with patch('shilun.common.db.MongoSnapshotStore', return_value=store), \
         patch('shilun.jobs.limit_pool_sync_job.LimitPoolSyncJob') as job:
        job.return_value.sync_limit_up_pool.side_effect = error
        with pytest.raises(HTTPException) as exc:
            data_sync_limit_pool(DataDomainSyncHttpRequest(target_date='2026-09-23'))
        assert exc.value.status_code == code
        assert message in exc.value.detail
        assert 'private' not in exc.value.detail
        store.close.assert_called_once()


def test_status_connection_failure_is_unknown_not_missing_data(config):
    store = Mock()
    store.collection.return_value.count_documents.side_effect = ServerSelectionTimeoutError('private')
    with patch('shilun.common.db.MongoSnapshotStore', return_value=store) as factory:
        result = data_status('2026-09-23')
    assert result['mongo_connected'] is False
    assert '暂时无法核对' in result['message']
    assert 'datasets' not in result
    assert factory.call_args.kwargs['initialize_indexes'] is False
    store.close.assert_called_once()


def test_job_refuses_ingestion_without_unique_index():
    store, client = Mock(), Mock()
    store.collection.return_value.create_index.side_effect = ServerSelectionTimeoutError('offline')
    with pytest.raises(ServerSelectionTimeoutError):
        LimitPoolSyncJob(store, client).sync_limit_up_pool('20260923')
    client.fetch_zt_pool.assert_not_called()
    store._upsert_payloads.assert_not_called()
    store.upsert_sync_state.assert_not_called()


def test_write_failure_never_records_success_or_empty():
    store, client = Mock(), Mock()
    client.fetch_zt_pool.return_value = pd.DataFrame([{'ticker': '000017', 'trade_date': '20260923'}])
    store._upsert_payloads.side_effect = ServerSelectionTimeoutError('write interrupted')
    with pytest.raises(ServerSelectionTimeoutError):
        LimitPoolSyncJob(store, client).sync_limit_up_pool('20260923')
    store.upsert_sync_state.assert_not_called()


def test_sync_state_failure_keeps_written_rows_for_retry():
    store, client = Mock(), Mock()
    client.fetch_zt_pool.return_value = pd.DataFrame([{'ticker': '000017', 'trade_date': '20260923'}])
    store._upsert_payloads.return_value = 1
    store.upsert_sync_state.side_effect = ServerSelectionTimeoutError('state interrupted')
    with pytest.raises(ServerSelectionTimeoutError):
        LimitPoolSyncJob(store, client).sync_limit_up_pool('20260923')
    store._upsert_payloads.assert_called_once_with(
        'limit_up_pool', [{'ticker': '000017', 'trade_date': '20260923'}], ('ticker', 'trade_date'))
    store.collection.return_value.delete_many.assert_not_called()
