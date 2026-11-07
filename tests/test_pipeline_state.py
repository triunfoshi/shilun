from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from shilun.jobs.pipeline_state import (
    COLLECTION, MAX_ATTEMPTS, RUNNING_TTL, TaskLedger, error_details,
)


DAY = '2026-11-03'


def matches(row, query):
    for key, condition in query.items():
        if key == '$or':
            if not any(matches(row, option) for option in condition):
                return False
        elif isinstance(condition, dict):
            if '$in' in condition and row.get(key) not in condition['$in']:
                return False
            if '$lte' in condition and (row.get(key) is None or row[key] > condition['$lte']):
                return False
        elif row.get(key) != condition:
            return False
    return True


class MemoryCursor(list):
    def sort(self, fields, direction=None):
        pairs = fields if isinstance(fields, list) else [(fields, direction)]
        for field, sign in reversed(pairs):
            super().sort(key=lambda row: (row.get(field) is not None, row.get(field, '')), reverse=sign < 0)
        return self

    def limit(self, size):
        return MemoryCursor(self[:size]) if size else self


class MemoryCollection:
    """Minimal Mongo operator fake; tests observe persisted documents, not calls."""
    def __init__(self):
        self.rows = {}

    def find(self, query, projection=None):
        rows = [deepcopy(row) for row in self.rows.values() if matches(row, query)]
        if projection and projection.get('_id') == 0:
            for row in rows:
                row.pop('_id', None)
        return MemoryCursor(rows)

    def find_one(self, query, projection=None):
        return next(iter(self.find(query, projection)), None)

    def update_one(self, query, update, upsert=False):
        existing = self.rows.get(query['_id'])
        if existing is None:
            assert upsert
            existing = deepcopy(query)
            existing.update(deepcopy(update.get('$setOnInsert', {})))
            self.rows[query['_id']] = existing
        existing.update(deepcopy(update.get('$set', {})))
        for key in update.get('$unset', {}):
            existing.pop(key, None)
        for key, value in update.get('$push', {}).items():
            existing[key] = existing.get(key, []) + deepcopy(value['$each'])
            existing[key] = existing[key][value['$slice']:]


class MemoryStore:
    def __init__(self):
        self.tasks = MemoryCollection()

    def collection(self, name):
        assert name == COLLECTION
        return self.tasks


@pytest.fixture
def setup():
    database = MemoryStore()
    clock = [datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)]
    return database, clock, TaskLedger(database, now_fn=lambda: clock[0])


def test_retry_schedule_is_persistent_bounded_and_cleared_on_success(setup):
    database, clock, ledger = setup
    first = ledger.start(DAY, 'taxonomy', dependencies=['daily_sync'], input_revision='bars-v1')
    assert first['attempts'] == 1 and first['dependencies'] == ['daily_sync']
    failed = ledger.fail(DAY, 'taxonomy', ValueError('Daily history date mismatch'))
    assert failed['status'] == 'retry'
    assert failed['error_message'] == 'Daily history date mismatch'
    assert ledger.due(DAY) == []
    clock[0] += timedelta(minutes=5)
    # A new process sees the same task state; no in-memory retry queue required.
    restarted = TaskLedger(database, now_fn=lambda: clock[0])
    assert [row['stage'] for row in restarted.due(DAY)] == ['taxonomy']
    restarted.start(DAY, 'taxonomy', input_revision='bars-v1')
    complete = restarted.succeed(DAY, 'taxonomy', {'input_revision': 'bars-v1', 'output_revision': 'catalog-v2'})
    assert complete['attempts'] == 2 and complete['output_revision'] == 'catalog-v2'
    assert restarted.ready(DAY, 'taxonomy')
    for key in ('error_type', 'error_message', 'next_retry_at', 'expires_at', 'blocked_by'):
        assert key not in complete
    assert any(event.get('error_message') == 'Daily history date mismatch' for event in complete['history'])
    assert restarted.due(DAY) == []
    assert len(database.tasks.rows) == 1
    new_input = restarted.start(DAY, 'taxonomy', input_revision='bars-v3')
    assert new_input['attempts'] == 1
    assert 'output_revision' not in new_input and 'result' not in new_input


def test_five_failures_exhaust_budget_without_permanent_retry_loop(setup):
    _, clock, ledger = setup
    for attempt, delay in enumerate((5, 15, 30, 60, None), start=1):
        ledger.start(DAY, 'review', input_revision='same-input')
        failed = ledger.fail(DAY, 'review', RuntimeError('upstream not complete'))
        assert failed['attempts'] == attempt
        if delay is not None:
            assert failed['status'] == 'retry'
            assert datetime.fromisoformat(failed['next_retry_at']) - clock[0] == timedelta(minutes=delay)
            clock[0] += timedelta(minutes=delay)
            assert ledger.due(DAY)[0]['stage'] == 'review'
        else:
            assert failed['status'] == 'failed'
            assert 'next_retry_at' not in failed
            assert ledger.due(DAY) == []
    assert failed['attempts'] == MAX_ATTEMPTS
    reset = ledger.start(DAY, 'review', input_revision='new-ready-evidence')
    assert reset['attempts'] == 1 and reset['input_revision'] == 'new-ready-evidence'


def test_waiting_does_not_consume_attempts_and_dates_cannot_mix(setup):
    _, _, ledger = setup
    for _ in range(3):
        ledger.wait(DAY, 'strong_stocks', ['taxonomy'])
    assert ledger.get(DAY, 'strong_stocks')['attempts'] == 0
    assert not ledger.ready(DAY, 'strong_stocks')
    ledger.start('2026-11-02', 'taxonomy')
    ledger.succeed('2026-11-02', 'taxonomy')
    assert ledger.ready('2026-11-02', 'taxonomy')
    assert not ledger.ready(DAY, 'taxonomy')
    assert [row['analysis_date'] for row in ledger.due(DAY)] == [DAY]
    assert [row['stage'] for row in ledger.snapshot(DAY)] == ['strong_stocks']
    ledger.start(DAY, 'strong_stocks')
    ledger.fail(DAY, 'strong_stocks', RuntimeError('missing industry'))
    ledger.wait(DAY, 'strong_stocks', ['taxonomy'])
    assert ledger.get(DAY, 'strong_stocks')['attempts'] == 1


def test_crashed_attempt_is_recovered_after_expiry_and_still_respects_budget(setup):
    _, clock, ledger = setup
    ledger.start(DAY, 'taxonomy')
    assert ledger.recover_expired(DAY) == []
    assert ledger.due(DAY) == []
    clock[0] += RUNNING_TTL
    assert ledger.due(DAY)[0]['status'] == 'running'
    recovered = ledger.recover_expired(DAY)
    assert recovered[0]['error_type'] == 'TimeoutError'
    assert recovered[0]['status'] == 'retry' and recovered[0]['attempts'] == 1
    assert ledger.due(DAY) == []  # Backoff also applies to a crashed process.
    clock[0] += timedelta(minutes=5)
    assert ledger.due(DAY)[0]['stage'] == 'taxonomy'
    for _ in range(4):
        ledger.start(DAY, 'taxonomy')
    clock[0] += RUNNING_TTL
    assert ledger.recover_expired(DAY)[0]['status'] == 'failed'
    assert ledger.due(DAY) == []


def test_public_status_and_errors_never_expose_connection_secrets(setup):
    _, _, ledger = setup
    error = ValueError('date mismatch; token=hidden-token password="hidden password"; '
                       'mongodb://account:dbpassword@host/db?authSource=admin; '
                       'Authorization: Bearer hidden-bearer')
    detail = error_details(error)
    assert detail['error_type'] == 'ValueError'
    for secret in ('hidden-token', 'hidden password', 'account', 'dbpassword', 'hidden-bearer', 'authSource'):
        assert secret not in detail['error_message']
    assert 'date mismatch' in detail['error_message']
    ledger.start(DAY, 'review')
    ledger.fail(DAY, 'review', error)
    public = ledger.snapshot(DAY)
    assert '_id' not in public[0] and 'history' not in public[0]
    assert public[0]['error_message'] == detail['error_message']
    ledger.succeed(DAY, 'review', {'token': 'do-not-export', 'endpoint_uri': 'mongodb://a:b@host/db', 'count': 7})
    assert ledger.snapshot(DAY)[0]['result'] == {'count': 7}
    assert len(error_details(ValueError('x'*2000))['error_message']) == 500


def test_nonretryable_failure_is_terminal_and_invalid_limits_never_scan_all_rows(setup):
    _, _, ledger = setup
    ledger.start(DAY, 'review')
    ledger.fail(DAY, 'review', ValueError('invalid date'), retryable=False)
    assert ledger.get(DAY, 'review')['status'] == 'failed'
    ledger.wait(DAY, 'themes', ['review'])
    assert ledger.due(DAY, limit=0) == []
    assert ledger.due(DAY, limit=-1) == []
    with pytest.raises(ValueError):
        ledger.start('20261103', 'review')
    with pytest.raises(ValueError):
        ledger.start(DAY, 'review bad stage')
    with pytest.raises(ValueError):
        ledger.succeed(DAY, 'review', status='ready')
