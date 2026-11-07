"""Dated, persistent task receipts for the dependency-driven data pipeline.

The pipeline's global lease serializes executors. This ledger records each
stage independently so a worker can resume only failed or newly unblocked work.
Times are UTC ISO strings; the date key is always the resolved trading day.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import re


COLLECTION = 'data_pipeline_tasks'
MAX_ATTEMPTS = 5
RETRY_MINUTES = (5, 15, 30, 60)
RUNNING_TTL = timedelta(hours=2)
_STAGE = re.compile(r'^[A-Za-z0-9_.:/-]{1,120}$')
_SECRET_KEY = re.compile(r'token|secret|password|passwd|api[_-]?key|authorization|credential|(?:^|_)uri$|(?:^|_)url$', re.I)
_URL = re.compile(r'\b[a-z][a-z0-9+.-]*://[^\s\'"<>]+', re.I)
_SECRET_VALUE = re.compile(
    r'''(?ix)(\b[a-z0-9_-]*(?:token|secret|password|passwd|api[_-]?key|authorization|credential|uri)[a-z0-9_-]*\b["']?\s*[:=]\s*)'''
    r'''(?:\[redacted(?:-url)?\]|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|[^\s,;}\]]+)''')


def _redact(value):
    text = _URL.sub('[redacted-url]', str(value))
    text = re.sub(r'(?i)\b(Bearer|Basic)\s+\S+', r'\1 [redacted]', text)
    text = _SECRET_VALUE.sub(r'\1[redacted]', text)
    return ' '.join(text.split())[:500]


def error_details(error):
    """Keep a useful bounded diagnostic without credentials or URL values."""
    return {'error_type': type(error).__name__ if isinstance(error, BaseException) else 'Error',
            'error_message': _redact(error)}


def safe_error_message(error):
    return _redact(error)


def _safe_data(value, depth=0):
    if depth > 6:
        return '[truncated]'
    if isinstance(value, dict):
        return {str(key): _safe_data(item, depth+1) for key, item in list(value.items())[:100]
                if key != '_id' and not _SECRET_KEY.search(str(key))}
    if isinstance(value, (tuple, list)):
        return [_safe_data(item, depth+1) for item in value[:100]]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return _redact(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact(value)


class TaskLedger:
    """One current receipt per date/stage, with bounded sanitized event history.

    Callers acquire the global pipeline lease before transitions or recovery.
    ``start`` is an explicit attempt; the worker controls whether retries are
    due. A changed non-null input revision opens a fresh five-attempt budget.
    """

    def __init__(self, store, now_fn=None):
        self.collection = store.collection(COLLECTION)
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def _now(self):
        now = self.now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    @staticmethod
    def _key(day, stage):
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError('Task date must be an ISO trading date')
        if not isinstance(stage, str) or not _STAGE.fullmatch(stage):
            raise ValueError('Invalid pipeline stage')
        return {'_id': f'{day}:{stage}'}

    def get(self, day, stage):
        return self.collection.find_one(self._key(day, stage), {'_id': 0})

    def _save(self, day, stage, values, *, clear=(), event=None):
        now = self._now().isoformat()
        payload = {'analysis_date': day, 'stage': stage, 'updated_at': now, **values}
        update = {'$set': payload, '$setOnInsert': {'created_at': now}}
        unset = {key: '' for key in clear if key not in payload}
        if unset:
            update['$unset'] = unset
        if event:
            update['$push'] = {'history': {'$each': [{'at': now, **_safe_data(event)}], '$slice': -20}}
        self.collection.update_one(self._key(day, stage), update, upsert=True)
        return self.get(day, stage)

    def start(self, day, stage, dependencies=(), input_revision=None):
        previous = self.get(day, stage) or {}
        changed = input_revision is not None and input_revision != previous.get('input_revision')
        attempts = 1 if changed else int(previous.get('attempts', 0)) + 1
        now = self._now()
        payload = {'status': 'running', 'attempts': attempts, 'max_attempts': MAX_ATTEMPTS,
                   'started_at': now.isoformat(), 'expires_at': (now+RUNNING_TTL).isoformat(),
                   'dependencies': list(dependencies)}
        if input_revision is not None:
            payload['input_revision'] = str(input_revision)
        clear = ('error_type', 'error_message', 'next_retry_at', 'finished_at', 'blocked_by')
        if changed:
            clear += ('output_revision', 'result')
        return self._save(day, stage, payload,
            clear=clear,
            event={'status': 'running', 'attempt': attempts, 'input_revision': payload.get('input_revision')})

    def succeed(self, day, stage, result=None, status='success'):
        if status not in ('success', 'degraded'):
            raise ValueError('A completed task must be success or degraded')
        payload = {'status': status, 'finished_at': self._now().isoformat(), 'result': _safe_data(result)}
        if isinstance(result, dict):
            for key in ('input_revision', 'output_revision'):
                if result.get(key) is not None:
                    payload[key] = str(result[key])
        return self._save(day, stage, payload,
            clear=('error_type', 'error_message', 'next_retry_at', 'expires_at', 'blocked_by'),
            event={'status': status})

    def fail(self, day, stage, error, retryable=True):
        previous = self.get(day, stage) or {}
        attempts = int(previous.get('attempts', 0))
        retry = retryable and attempts < MAX_ATTEMPTS
        payload = {'status': 'retry' if retry else 'failed', 'finished_at': self._now().isoformat(),
                   'attempts': attempts, 'max_attempts': MAX_ATTEMPTS, **error_details(error)}
        if retry:
            delay = RETRY_MINUTES[min(max(attempts-1, 0), len(RETRY_MINUTES)-1)]
            payload['next_retry_at'] = (self._now()+timedelta(minutes=delay)).isoformat()
        return self._save(day, stage, payload,
            clear=('expires_at', 'blocked_by', 'next_retry_at'),
            event={'status': payload['status'], 'attempt': attempts, **error_details(error)})

    def wait(self, day, stage, blocked_by):
        previous = self.get(day, stage) or {}
        blocked = list(blocked_by)
        return self._save(day, stage,
            {'status': 'waiting', 'attempts': int(previous.get('attempts', 0)),
             'max_attempts': MAX_ATTEMPTS, 'blocked_by': blocked,
             'dependencies': previous.get('dependencies', blocked)},
            clear=('error_type', 'error_message', 'next_retry_at', 'expires_at', 'finished_at'),
            event={'status': 'waiting', 'blocked_by': blocked})

    def ready(self, day, stage):
        return (self.get(day, stage) or {}).get('status') in ('success', 'degraded')

    def due(self, day=None, limit=20):
        """Discover work without changing receipts; caller checks dependencies.

        Expired running tasks are included for crash recovery. The executor must
        call ``recover_expired`` after acquiring its lease, before starting work,
        so the last allowed attempt cannot silently become a sixth attempt.
        """
        limit = max(0, min(int(limit), 100))
        if not limit:
            return []
        now = self._now().isoformat()
        query = {'$or': [{'status': {'$in': ['pending', 'waiting']}},
                         {'status': 'retry', 'next_retry_at': {'$lte': now}},
                         {'status': 'running', 'expires_at': {'$lte': now}}]}
        if day is not None:
            self._key(day, 'date_validation')
            query['analysis_date'] = day
        return list(self.collection.find(query, {'_id': 0}).sort(
            [('next_retry_at', 1), ('updated_at', 1)]).limit(limit))

    def recover_expired(self, day=None):
        """Record crashed attempts and back off; requires the global lease."""
        query = {'status': 'running', 'expires_at': {'$lte': self._now().isoformat()}}
        if day is not None:
            self._key(day, 'date_validation')
            query['analysis_date'] = day
        expired = list(self.collection.find(query, {'_id': 0}))
        return [self.fail(row['analysis_date'], row['stage'],
                         TimeoutError('Previous stage process expired before a completion receipt'))
                for row in expired]

    def snapshot(self, day):
        """Return current public receipts for a single resolved trading day."""
        self._key(day, 'date_validation')
        fields = ('analysis_date', 'stage', 'status', 'attempts', 'max_attempts', 'dependencies',
                  'blocked_by', 'input_revision', 'output_revision', 'started_at', 'finished_at',
                  'updated_at', 'next_retry_at', 'expires_at', 'error_type', 'error_message', 'result')
        return [_safe_data({key: deepcopy(row[key]) for key in fields if key in row})
                for row in self.collection.find({'analysis_date': day}, {'_id': 0}).sort('stage', 1)]
