"""Offline coverage for the dependency recovery trigger and deployed contract."""
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from shilun.jobs.cloud_scheduler import (
    PIPELINE_RELEASE_PATHS, _release_fingerprint, build_scheduler, scheduler_manifest,
)


TZ = ZoneInfo('Asia/Shanghai')
NOW = datetime(2026, 11, 3, 19, 40, 31, tzinfo=TZ)


@pytest.mark.parametrize('data_only', [True, False])
def test_reconcile_trigger_launches_only_due_worker_without_full_sync(data_only):
    with patch('shilun.jobs.cloud_scheduler._run') as launch:
        scheduler = build_scheduler(data_only=data_only, location='ECS')
        launch.assert_not_called()  # Constructing configuration performs no IO.
        job = scheduler.get_job('data_pipeline_reconcile')
        assert job is not None and job.executor == 'default'
        assert job.trigger.get_next_fire_time(None, NOW) == datetime(2026, 11, 3, 19, 45, 30, tzinfo=TZ)
        job.func()
        launch.assert_called_once_with('data_pipeline_reconcile', 'shilun.jobs.data_pipeline_retry_job')


def test_data_only_bulk_recovery_and_independent_evidence_keep_their_executors():
    scheduler = build_scheduler(data_only=True)
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert jobs['tushare_close_1915'].executor == jobs['data_pipeline_reconcile'].executor == 'default'
    assert scheduler._executors['default']._pool._max_workers == 1
    assert jobs['cffex_positions_1940'].executor == 'evidence'
    assert jobs['limit_pool_1935'].executor == 'evidence'
    assert jobs['scheduler_manifest_heartbeat'].executor == 'maintenance'
    assert not any(any(word in key for word in ('trading', 'holding', 'notification')) for key in jobs)


def test_manifest_names_real_classification_and_event_inputs_with_legacy_alias():
    scheduler = build_scheduler(data_only=True)
    manifest = scheduler_manifest(scheduler, data_only=True, location='ECS', now=NOW)
    jobs = {row['id']: row for row in manifest['jobs']}
    worker = jobs['data_pipeline_reconcile']
    assert worker['module'] == 'shilun.jobs.data_pipeline_retry_job'
    assert worker['minute'] == '*/5' and worker['executor'] == 'default'
    for key in ('unified_taxonomy', 'event_theme_inputs', 'previous_limit_pool', 'daily_review'):
        assert key in worker['dataset_keys']
    assert '不重跑全市场同步' in worker['dataset_notes']['daily_review']
    for row in (jobs['tushare_close_1915'], worker):
        assert 'topic_perspectives' in row['dataset_keys']  # Old status page remains connected.
        assert '不运行旧题材排名' in row['dataset_notes']['topic_perspectives']
        assert 'event_theme_inputs' in row['dataset_keys']
        assert '同日涨停原因' in row['dataset_notes']['event_theme_inputs']
    assert len(manifest['release_fingerprint']) == 64


@pytest.mark.parametrize('name', [
    'jobs/data_pipeline_job.py', 'jobs/data_pipeline_retry_job.py',
    'jobs/pipeline_dependencies.py', 'jobs/pipeline_state.py',
])
def test_release_fingerprint_detects_changed_or_missing_dependency_code(tmp_path, name):
    for path in PIPELINE_RELEASE_PATHS:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('original\n')
    original = _release_fingerprint(tmp_path)
    target = tmp_path / name
    target.write_text('changed\n')
    assert _release_fingerprint(tmp_path) != original
    target.write_text('original\n')
    assert _release_fingerprint(tmp_path) == original
    target.unlink()
    assert _release_fingerprint(tmp_path) != original
