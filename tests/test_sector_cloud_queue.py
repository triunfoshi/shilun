"""Offline queue/lease contracts: no providers, Mongo server or subprocesses."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
import sys
from unittest.mock import Mock

import pytest
from pymongo.errors import DuplicateKeyError

from shilun.jobs.heavy_job_lease import HeavyJobLease, LEASE_ID, LEASE_TTL, LeaseLost
from shilun.jobs.sector_precompute_job import (
    COLLECTION, MAX_ATTEMPTS, TASK_TTL, consume_one, recover_expired, start_task, task_status,
)

DAY = '2026-11-03'
NOW = datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)


def matches(row, query):
    for key, condition in query.items():
        if key == '$or':
            if not any(matches(row, option) for option in condition):
                return False
        elif isinstance(condition, dict):
            value = row.get(key)
            if '$lte' in condition and (value is None or value > condition['$lte']):
                return False
            if '$gt' in condition and (value is None or value <= condition['$gt']):
                return False
        elif row.get(key) != condition:
            return False
    return True


class Cursor(list):
    def sort(self, fields):
        for field, direction in reversed(fields):
            super().sort(key=lambda row: row.get(field, ''), reverse=direction < 0)
        return self

    def limit(self, value):
        return Cursor(self[:value])


class Collection:
    def __init__(self):
        self.rows = {}

    def find(self, query):
        return Cursor(deepcopy(row) for row in self.rows.values() if matches(row, query))

    def find_one(self, query):
        return next(iter(self.find(query)), None)

    def update_one(self, query, update, upsert=False):
        row = next((row for row in self.rows.values() if matches(row, query)), None)
        inserted = row is None
        if inserted:
            if not upsert:
                return SimpleNamespace(modified_count=0, upserted_id=None)
            if query['_id'] in self.rows:
                raise DuplicateKeyError('unique _id already owned')
            row = {key: value for key, value in query.items() if not isinstance(value, dict) and not key.startswith('$')}
            row.update(deepcopy(update.get('$setOnInsert', {})))
            self.rows[row['_id']] = row
        original = deepcopy(row)
        row.update(deepcopy(update.get('$set', {})))
        for key, value in update.get('$inc', {}).items():
            row[key] = row.get(key, 0) + value
        for key in update.get('$unset', {}):
            row.pop(key, None)
        return SimpleNamespace(modified_count=int(row != original), upserted_id=row['_id'] if inserted else None)

    def find_one_and_update(self, query, update, *, upsert=False, sort=None, return_document=None):
        rows = list(self.find(query))
        for key, direction in reversed(sort or []):
            rows.sort(key=lambda row: row.get(key, ''), reverse=direction < 0)
        if rows:
            identifier = rows[0]['_id']
            self.update_one({'_id': identifier}, update)
            return deepcopy(self.rows[identifier])
        if not upsert:
            return None
        self.update_one(query, update, upsert=True)
        return self.find_one({'_id': query['_id']})

    def delete_one(self, query):
        row = self.find_one(query)
        if row:
            self.rows.pop(row['_id'])


class Store:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, Collection())


@pytest.fixture
def setup(monkeypatch):
    # Use a fixed service protocol rather than importing analytical dependencies.
    service = ModuleType('shilun.market.sector_service')
    revision = ['source-v1']
    service.input_revision = Mock(side_effect=lambda *_args, **_kwargs: revision[0])
    service.published_result_matches = Mock(return_value=True)
    monkeypatch.setitem(sys.modules, service.__name__, service)
    monkeypatch.setattr('shilun.jobs.sector_precompute_job.utc_now', lambda: NOW)
    return Store(), revision, service


def test_submission_never_launches_local_process_and_equal_work_is_reused(setup):
    store, _, _ = setup
    local_launch = Mock()
    first = start_task(store, DAY, '000001.sh', launch=local_launch)
    repeated = start_task(store, DAY, '000001.SH')
    assert first == repeated and first['status'] == 'queued'
    assert first['top_n'] == 8 and first['trend_window_days'] == 60
    assert not first.get('expires_at')
    local_launch.assert_not_called()
    assert len(store.collection(COLLECTION).rows) == 1


def test_completed_post_backfill_revision_reuses_result_but_new_inputs_enqueue(setup):
    store, revision, _ = setup
    original = start_task(store, DAY, '000001.SH')
    collection = store.collection(COLLECTION)
    row = collection.rows[original['job_id']]
    row.update(status='complete', published_input_revision='source-after-minutes', result={'ok': True})
    revision[0] = 'source-after-minutes'
    reused = start_task(store, DAY, '000001.SH')
    assert reused['job_id'] == original['job_id'] and reused['status'] == 'complete'
    revision[0] = 'new-flow-inputs'
    fresh = start_task(store, DAY, '000001.SH')
    assert fresh['job_id'] != original['job_id'] and fresh['status'] == 'queued'
    assert start_task(store, DAY, '000001.SH', top_n=3)['job_id'] != fresh['job_id']
    assert start_task(store, DAY, '000001.SH', force=True)['job_id'] == fresh['job_id']


@pytest.mark.parametrize('status', ['queued', 'running', 'retry'])
def test_changed_inputs_and_force_reuse_active_scope_without_merging_other_parameters(setup, status):
    store, revision, _ = setup
    first = start_task(store, DAY, '000001.SH')
    store.collection(COLLECTION).rows[first['job_id']]['status'] = status
    revision[0] = 'new-source-revision'
    assert start_task(store, DAY, '000001.SH')['job_id'] == first['job_id']
    assert start_task(store, DAY, '000001.SH', force=True)['job_id'] == first['job_id']
    assert start_task(store, DAY, '000001.SH', top_n=3)['job_id'] != first['job_id']
    assert start_task(store, DAY, '399006.SZ')['job_id'] != first['job_id']


def test_replaced_cache_is_rebuilt_instead_of_reusing_a_stale_complete_receipt(setup):
    store, _, service = setup
    original = start_task(store, DAY, '000001.SH')
    row = store.collection(COLLECTION).rows[original['job_id']]
    row.update(status='complete', published_input_revision='source-v1', result={'generation': {'id': 'old'}})
    service.published_result_matches.return_value = False
    refreshed = start_task(store, DAY, '000001.SH')
    assert refreshed['status'] == 'queued' and refreshed['attempts'] == 0


def test_queued_new_revision_reuses_its_predecessors_repaired_result_at_execution(setup):
    store, revision, _ = setup
    first = start_task(store, DAY, '000001.SH')
    # Model a newer revision already queued by an older deployed producer.
    store.collection(COLLECTION).rows[first['job_id']]['status'] = 'complete'
    revision[0] = 'source-v2'
    second = start_task(store, DAY, '000001.SH')
    store.collection(COLLECTION).rows[first['job_id']].update(
        status='complete', published_input_revision='source-v2',
        result={'published_input_revision': 'source-v2', 'generation': {'id': 'published'}})
    runner = Mock()
    assert consume_one(store, runner=runner, config=object(), now_fn=lambda: NOW)['status'] == 'complete'
    runner.assert_not_called()
    result = store.collection(COLLECTION).rows[second['job_id']]['result']
    assert result['reused_job_id'] == first['job_id'] and result['generation']['id'] == 'published'


def test_cloud_worker_claims_publishes_and_releases_shared_lease(setup):
    store, _, _ = setup
    task = start_task(store, DAY, '000001.SH')
    def calculate(database, config, day, benchmark, *, top_n, before_publish):
        assert database is store and day == DAY and top_n == 8
        assert store.collection('job_leases').find_one({'_id': LEASE_ID})
        assert store.collection(COLLECTION).rows[task['job_id']]['status'] == 'running'
        before_publish()
        return {'published_input_revision': 'source-v2', 'analysis_date': day}
    result = consume_one(store, runner=calculate, config=object(), now_fn=lambda: NOW)
    assert result['status'] == 'complete'
    published = store.collection(COLLECTION).rows[task['job_id']]
    assert published['published_input_revision'] == 'source-v2' and published['attempts'] == 1
    assert not published.get('owner') and not store.collection('job_leases').rows
    assert consume_one(store, runner=Mock(), config=object(), now_fn=lambda: NOW)['status'] == 'idle'


def test_an_existing_pipeline_lease_defers_without_spending_attempt(setup):
    store, _, _ = setup
    task = start_task(store, DAY, '000001.SH')
    leases = store.collection('job_leases')
    leases.rows[LEASE_ID] = {'_id': LEASE_ID, 'owner': 'pipeline', 'expires_at': NOW + LEASE_TTL}
    runner = Mock()
    assert consume_one(store, runner=runner, config=object(), now_fn=lambda: NOW)['status'] == 'busy'
    assert store.collection(COLLECTION).rows[task['job_id']]['attempts'] == 0
    assert leases.rows[LEASE_ID]['owner'] == 'pipeline'
    runner.assert_not_called()


def test_provider_failures_retry_with_backoff_and_never_leak_secrets(setup):
    store, _, _ = setup
    task = start_task(store, DAY, '000001.SH')
    clock = [NOW]
    runner = Mock(side_effect=ValueError('日线覆盖不足；mongodb://secret-user:password@private/db token=hidden-token'))
    for attempt in range(1, MAX_ATTEMPTS + 1):
        status = consume_one(store, runner=runner, config=object(), now_fn=lambda: clock[0])['status']
        row = store.collection(COLLECTION).rows[task['job_id']]
        assert row['attempts'] == attempt
        assert 'private' not in row['error'] and 'password' not in row['error']
        assert row['error_type'] == 'ValueError' and '日线覆盖不足' in row['error_message']
        assert all(secret not in row['error_message'] for secret in ('private', 'password', 'secret-user', 'hidden-token'))
        if attempt < MAX_ATTEMPTS:
            assert status == 'retry' and row['next_retry_at'] > clock[0]
            assert consume_one(store, runner=runner, config=object(), now_fn=lambda: clock[0])['status'] == 'idle'
            clock[0] = row['next_retry_at']
        else:
            assert status == 'failed' and 'next_retry_at' not in row
    assert runner.call_count == MAX_ATTEMPTS
    assert start_task(store, DAY, '000001.SH')['status'] == 'failed'
    retried = start_task(store, DAY, '000001.SH', force=True)
    assert retried['status'] == 'queued' and retried['job_id'] != task['job_id']


def test_crashed_running_task_is_recovered_with_backoff_not_expired_in_browser(setup):
    store, _, _ = setup
    task = start_task(store, DAY, '000001.SH')
    collection = store.collection(COLLECTION)
    row = collection.rows[task['job_id']]
    row.update(status='running', owner='dead-worker', attempts=1, expires_at=NOW + TASK_TTL)
    assert recover_expired(collection, NOW) == 0
    later = NOW + TASK_TTL
    assert task_status(row, now=later)['status'] == 'running'
    assert task_status(row, now=later)['recovery_pending'] is True
    assert recover_expired(collection, later) == 1
    assert row['status'] == 'retry' and row['next_retry_at'] > later and row['attempts'] == 1
    row['status'] = 'queued'
    assert task_status(row, now=NOW + timedelta(days=2))['status'] == 'queued'


def test_lost_lease_blocks_publication_and_cannot_delete_successors_lease(setup):
    store, _, _ = setup
    task = start_task(store, DAY, '000001.SH')
    published = []
    def calculate(database, config, day, benchmark, *, top_n, before_publish):
        store.collection('job_leases').rows[LEASE_ID]['owner'] = 'successor'
        before_publish()
        published.append(day)
    outcome = consume_one(store, runner=calculate, config=object(), now_fn=lambda: NOW)
    assert outcome['status'] == 'lost' and published == []
    assert store.collection(COLLECTION).rows[task['job_id']]['status'] == 'running'
    assert store.collection('job_leases').rows[LEASE_ID]['owner'] == 'successor'


def test_lease_renewal_and_expiry_never_revive_a_stale_worker():
    clock = [NOW]
    collection = Collection()
    lease = HeavyJobLease(collection, 'worker', now_fn=lambda: clock[0], heartbeat_seconds=3600).start()
    try:
        clock[0] += timedelta(minutes=1)
        lease.renew()
        assert collection.rows[LEASE_ID]['expires_at'] == clock[0] + LEASE_TTL
        clock[0] += LEASE_TTL
        with pytest.raises(LeaseLost):
            lease.renew()
        with pytest.raises(LeaseLost):
            lease.assert_owned()
    finally:
        lease.close()


def test_cloud_schedule_registers_queue_on_bulk_executor_without_io():
    from shilun.jobs.cloud_scheduler import build_scheduler, scheduler_manifest
    scheduler = build_scheduler(data_only=True, location='ECS')
    job = scheduler.get_job('data_pipeline_sector_precompute')
    assert job.executor == 'default'
    manifest = scheduler_manifest(scheduler, data_only=True, location='ECS', now=NOW)
    row = next(row for row in manifest['jobs'] if row['id'] == job.id)
    assert row['module'] == 'shilun.jobs.sector_precompute_job'
    assert row['dataset_keys'] == ['sector_trends', 'ma5_selection']
