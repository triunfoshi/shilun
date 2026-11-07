"""Public submission paths cannot start synchronous local sector computation."""
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from shilun.api import precompute_sectors, start_sector_precompute


def test_legacy_post_enqueues_cloud_work_instead_of_running_analysis():
    task = {'status': 'queued', 'job_id': 'a' * 24}
    with patch('shilun.api.start_sector_precompute', return_value=task) as submit, \
         patch('shilun.api._compute_sector_trends_with_candidate_minutes', side_effect=AssertionError('local compute')):
        assert precompute_sectors('2026-11-03') == task
    submit.assert_called_once_with(date='2026-11-03', benchmark_ticker='000001.SH', top_n=8)


def test_explicit_failed_task_retry_passes_force_without_changing_other_params():
    store = Mock()
    with patch('shilun.jobs.sector_precompute_job._store', return_value=store), \
         patch('shilun.jobs.sector_precompute_job.start_task', return_value={'status': 'queued'}) as submit:
        assert start_sector_precompute('2026-11-03', '000300.SH', top_n=5, force=True)['status'] == 'queued'
    submit.assert_called_once_with(store, '2026-11-03', '000300.SH', top_n=5, force=True)
    store.close.assert_called_once()


def test_invalid_queue_parameter_returns_validation_error_and_closes_connection():
    store = Mock()
    with patch('shilun.jobs.sector_precompute_job._store', return_value=store), \
         patch('shilun.jobs.sector_precompute_job.start_task', side_effect=ValueError('top_n out of range')):
        with pytest.raises(HTTPException) as exc:
            start_sector_precompute('2026-11-03', top_n=21)
    assert exc.value.status_code == 422
    store.close.assert_called_once()


@pytest.mark.parametrize('options', [dict(lookback_days=100), dict(trend_lookback_days=30),
                                    dict(include_daily_leaders=True), dict(include_all_sectors=False)])
def test_legacy_unsupported_calculation_options_are_not_silently_discarded(options):
    with patch('shilun.api.start_sector_precompute') as submit:
        with pytest.raises(HTTPException) as exc:
            precompute_sectors('2026-11-03', **options)
    assert exc.value.status_code == 422
    submit.assert_not_called()
