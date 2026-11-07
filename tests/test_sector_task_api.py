from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from pymongo.errors import ServerSelectionTimeoutError

from shilun.api import start_sector_precompute, sector_precompute_status


@pytest.mark.parametrize('operation', ['start', 'status'])
def test_database_failure_returns_recoverable_error_and_closes_store(operation):
    store = Mock()
    store.collection.return_value.find_one.side_effect = ServerSelectionTimeoutError('private connection')
    with patch('shilun.jobs.sector_precompute_job._store', return_value=store), \
         patch('shilun.jobs.sector_precompute_job.start_task', side_effect=ServerSelectionTimeoutError('private connection')):
        with pytest.raises(HTTPException) as exc:
            if operation == 'start':
                start_sector_precompute('2026-09-21', '000001.SH')
            else:
                sector_precompute_status('a' * 24)
    assert exc.value.status_code == 503
    assert 'private' not in exc.value.detail
    store.close.assert_called_once()


def test_start_returns_task_and_invalid_status_does_not_open_database():
    store = Mock()
    with patch('shilun.jobs.sector_precompute_job._store', return_value=store) as factory, \
         patch('shilun.jobs.sector_precompute_job.start_task', return_value={'status':'queued', 'job_id':'a'*24}) as start:
        assert start_sector_precompute('2026-09-21')['status'] == 'queued'
        start.assert_called_once_with(store, '2026-09-21', '000001.SH')
        store.close.assert_called_once()
        factory.reset_mock()
        with pytest.raises(HTTPException) as exc:
            sector_precompute_status('invalid')
        assert exc.value.status_code == 422
        factory.assert_not_called()
