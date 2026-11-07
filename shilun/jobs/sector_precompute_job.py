"""Persisted sector/MA5 queue consumed next to Mongo, independent of the browser."""
from datetime import date, timedelta
from hashlib import sha256
import argparse
import json
import socket
import threading
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.jobs.heavy_job_lease import HeavyJobLease, LeaseLost, PipelineBusy, as_utc, utc_now

COLLECTION = 'sector_precompute_tasks'
MAX_ATTEMPTS = 5
TASK_TTL = timedelta(minutes=3)
RETRY_MINUTES = (5, 15, 30, 60)


def _store():
    config = load_config()
    return MongoSnapshotStore(config.mongo_uri, config.mongo_db,
                              socket_timeout_ms=60000, initialize_indexes=False)


def ensure_indexes(store):
    """Cloud-owned indexes; browser submissions never initialize schemas."""
    collection = store.collection(COLLECTION)
    collection.create_index([('status', 1), ('created_at', 1)], name='sector_queue_created')
    collection.create_index([('status', 1), ('next_retry_at', 1)], name='sector_retry_due')
    collection.create_index([('status', 1), ('expires_at', 1)], name='sector_running_expiry')
    collection.create_index([('analysis_date', 1), ('benchmark_ticker', 1),
                             ('published_input_revision', 1)], name='sector_published_revision')


def task_status(row, *, now=None):
    if not row:
        return None
    result = {k: v for k, v in row.items() if k not in ('_id', 'owner', 'expires_at')}
    # Queued work can wait behind data materialization indefinitely. Recovery
    # belongs to the cloud consumer; the browser must not expire queued work.
    expires = row.get('expires_at')
    if expires and as_utc(expires) <= (now or utc_now()) and result.get('status') == 'running':
        result['recovery_pending'] = True
    return result


def _reusable_task(store, scope, revision):
    from shilun.market.sector_service import published_result_matches
    rows = store.collection(COLLECTION).find(
        {**scope, 'status': 'complete', 'published_input_revision': revision})
    for row in rows.sort([('finished_at', -1)]).limit(10):
        if published_result_matches(store, row):
            return row
    return None


def start_task(store, day, benchmark, *, top_n=8, trend_window_days=60, force=False, launch=None):
    """Only enqueue. ``launch`` remains accepted for legacy callers, never runs."""
    from shilun.market import SECTOR_ENGINE_VERSION
    from shilun.market.ma5_liquidity import MA5_SELECTION_RULE_VERSION
    from shilun.market.taxonomy import VERSION as TAXONOMY_VERSION
    from shilun.market.sector_service import input_revision, published_result_matches

    day = date.fromisoformat(day).isoformat()
    benchmark = benchmark.upper()
    if type(top_n) is not int or not 1 <= top_n <= 20:
        raise ValueError('top_n must be between 1 and 20')
    if trend_window_days != 60:
        raise ValueError('Only the 60-session sector window is supported')
    scope = {'analysis_date': day, 'benchmark_ticker': benchmark, 'top_n': top_n,
             'trend_window_days': trend_window_days, 'engine_version': SECTOR_ENGINE_VERSION,
             'selection_rule_version': MA5_SELECTION_RULE_VERSION, 'membership_version': TAXONOMY_VERSION}
    collection = store.collection(COLLECTION)
    active = collection.find_one({**scope, '$or': [
        {'status': status} for status in ('queued', 'running', 'retry')]})
    if active:
        # Repeated clicks (including explicit force) must not build an unbounded
        # same-scope queue. The worker reads fresh inputs when execution starts.
        return task_status(active)
    revision = input_revision(store, day, benchmark, top_n=top_n, trend_window_days=trend_window_days)
    # Dependency repair (e.g. minute backfill) changes the input revision during
    # execution. Match the published revision as well as the submission key.
    if not force:
        complete = _reusable_task(store, scope, revision)
        if complete:
            return task_status(complete)
    identity = {**scope, 'input_revision': revision}
    if force:
        identity['request_nonce'] = uuid.uuid4().hex
    key = sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]
    now = utc_now()
    row = {'job_id': key, **identity, 'status': 'queued', 'attempts': 0,
           'max_attempts': MAX_ATTEMPTS, 'created_at': now.isoformat(),
           'updated_at': now.isoformat(), 'result': None, 'error': None}
    # The unique _id coalesces running and completed equal-input work.
    try:
        published = collection.find_one_and_update(
            {'_id': key}, {'$setOnInsert': row}, upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        published = collection.find_one({'_id': key})
        if not published:
            raise
    if published.get('status') == 'complete' and (
            published.get('published_input_revision') != revision or not published_result_matches(store, published)):
        # The old task is complete but its published cache has been replaced.
        # Exhausted failures require force=True, preserving the retry budget.
        refreshed = collection.find_one_and_update(
            {'_id': key, 'status': published['status']},
            {'$set': row, '$unset': {'owner': '', 'expires_at': '', 'next_retry_at': '',
                                   'finished_at': '', 'error_type': '', 'error_message': '', 'published_input_revision': ''}},
            return_document=ReturnDocument.AFTER)
        published = refreshed or collection.find_one({'_id': key})
    return task_status(published)


def _failure_update(row, now, error_or_type):
    from shilun.jobs.pipeline_state import error_details
    details = ({'error_type': error_or_type,
                'error_message': '板块后台任务心跳超时，计算进程可能已中断。'}
               if isinstance(error_or_type, str) else error_details(error_or_type))
    attempts = int(row.get('attempts') or 0)
    retry = attempts < MAX_ATTEMPTS
    values = {'status': 'retry' if retry else 'failed', **details,
              'error': '板块计算未完成；已有缓存保留。自动重试已安排。' if retry else
                       '板块计算多次未完成；已有缓存保留。请核查数据后重新发起。',
              'finished_at': now.isoformat(), 'updated_at': now.isoformat()}
    if retry:
        values['next_retry_at'] = now + timedelta(minutes=RETRY_MINUTES[max(0, attempts - 1)])
    return {'$set': values, '$unset': {'owner': '', 'expires_at': '', **({} if retry else {'next_retry_at': ''})}}


def recover_expired(collection, now):
    """Lost workers consume an attempt and back off instead of looping forever."""
    recovered = 0
    for row in collection.find({'status': 'running', 'expires_at': {'$lte': now}}).limit(20):
        query = {'_id': row['_id'], 'status': 'running', 'owner': row.get('owner'),
                 'expires_at': {'$lte': now}}
        result = collection.update_one(query, _failure_update(row, now, 'WorkerInterrupted'))
        recovered += int(bool(result.modified_count))
    return recovered


def consume_one(store, *, config=None, runner=None, now_fn=utc_now, job_id=None):
    """One short-lived worker: at most one heavy calculation across all hosts."""
    config = config or load_config()
    collection = store.collection(COLLECTION)
    now = now_fn()
    recover_expired(collection, now)
    due = {'$or': [{'status': 'queued'}, {'status': 'retry', 'next_retry_at': {'$lte': now}}]}
    if job_id:
        due['_id'] = job_id
    if not collection.find_one(due):
        return {'status': 'idle'}
    owner = f'{socket.gethostname()}:sectors:{uuid.uuid4().hex}'
    try:
        lease = HeavyJobLease(store.collection('job_leases'), owner, now_fn=now_fn).start()
    except PipelineBusy:
        return {'status': 'busy'}
    stop, pulse = threading.Event(), None
    row, query = None, None
    try:
        now = now_fn()
        due['$or'][1]['next_retry_at']['$lte'] = now
        row = collection.find_one_and_update(due,
            {'$set': {'status': 'running', 'owner': owner, 'started_at': now.isoformat(),
                      'updated_at': now.isoformat(), 'heartbeat_at': now.isoformat(),
                      'expires_at': now + TASK_TTL, 'error': None},
             '$inc': {'attempts': 1}, '$unset': {'next_retry_at': '', 'error_type': '', 'error_message': ''}},
            sort=[('created_at', 1)], return_document=ReturnDocument.AFTER)
        if not row:
            return {'status': 'idle'}
        query = {'_id': row['_id'], 'owner': owner, 'status': 'running'}

        def assert_owned():
            lease.assert_owned()
            task = collection.find_one(query)
            if not task or as_utc(task['expires_at']) <= now_fn():
                raise LeaseLost('Sector task ownership expired or changed')

        def heartbeat():
            while not stop.wait(30):
                try:
                    lease.assert_owned()
                    tick = now_fn()
                    renewed = collection.update_one({**query, 'expires_at': {'$gt': tick}},
                        {'$set': {'heartbeat_at': tick.isoformat(), 'expires_at': tick + TASK_TTL}})
                    if not getattr(renewed, 'matched_count', renewed.modified_count):
                        return
                except LeaseLost:
                    return
                except Exception:
                    continue

        pulse = threading.Thread(target=heartbeat, daemon=True)
        pulse.start()
        from shilun.market.sector_service import input_revision
        revision = input_revision(store, row['analysis_date'], row['benchmark_ticker'],
                                  top_n=row.get('top_n', 8), trend_window_days=row.get('trend_window_days', 60))
        scope = {key: row[key] for key in ('analysis_date', 'benchmark_ticker', 'top_n',
                 'trend_window_days', 'engine_version', 'selection_rule_version', 'membership_version') if key in row}
        previous = None if row.get('request_nonce') else _reusable_task(store, scope, revision)
        if previous:
            # Work may have been queued before its predecessor repaired minute
            # data and published this revision. Reuse at execution time too.
            result = {**previous['result'], 'reused_job_id': previous['job_id']}
        else:
            if runner is None:
                from shilun.market.sector_service import run_precompute
                runner = run_precompute
            result = runner(store, config, row['analysis_date'], row['benchmark_ticker'],
                            top_n=row.get('top_n', 8), before_publish=assert_owned)
        assert_owned()
        revision = result.get('published_input_revision')
        if not revision:
            revision = input_revision(store, row['analysis_date'], row['benchmark_ticker'],
                                      top_n=row.get('top_n', 8), trend_window_days=row.get('trend_window_days', 60))
        finished = now_fn().isoformat()
        update = collection.update_one(query,
            {'$set': {'status': 'complete', 'result': result, 'published_input_revision': revision,
                      'finished_at': finished, 'updated_at': finished, 'error': None},
             '$unset': {'owner': '', 'expires_at': '', 'error_type': '', 'error_message': '', 'next_retry_at': ''}})
        return {'status': 'complete' if update.modified_count else 'lost', 'job_id': row['job_id']}
    except LeaseLost:
        # The successor owns recovery; do not mutate its row or publish old work.
        return {'status': 'lost', 'job_id': (row or {}).get('job_id')}
    except Exception as error:
        if row and query:
            collection.update_one(query, _failure_update(row, now_fn(), error))
            return {'status': 'retry' if row['attempts'] < MAX_ATTEMPTS else 'failed', 'job_id': row['job_id']}
        raise
    finally:
        stop.set()
        if pulse:
            pulse.join(timeout=2)
        lease.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id')
    for name in ('owner', 'date', 'benchmark'):
        parser.add_argument('--' + name, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    store = _store()
    try:
        ensure_indexes(store)
        result = consume_one(store, job_id=args.job_id)
        print(f'sector_precompute status={result["status"]}', flush=True)
        return 0 if result['status'] in ('complete', 'idle', 'busy') else 2
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
