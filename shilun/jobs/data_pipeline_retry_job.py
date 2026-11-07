"""Reconcile persisted dated tasks; never launch a full-market sync on a tick."""
from datetime import datetime, timezone
import argparse
import socket
import uuid

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.jobs.data_pipeline_job import PipelineBusy, publish_reconciliation
from shilun.jobs.heavy_job_lease import HeavyJobLease
from shilun.jobs.data_pipeline_reconcile import reconcile
from shilun.jobs.pipeline_state import TaskLedger


def run_retries(store, day=None, *, limit=3, reconciler=None, now=None):
    fixed_now = now
    now = now or datetime.now(timezone.utc)
    owner = f'{socket.gethostname()}:retry:{uuid.uuid4()}'
    leases = store.collection('job_leases')
    # A supplied clock is for deterministic offline tests; production advances
    # normally even during a long dependency rebuild.
    clock = (lambda: fixed_now) if fixed_now is not None else None
    lease = HeavyJobLease(leases, owner, **({'now_fn': clock} if clock else {})).start()
    try:
        ledger = TaskLedger(store, now_fn=lambda: now)
        ledger.recover_expired(day)
        latest = store.find_sync_state(dataset='daily_data_pipeline', scope='latest') or {}
        if day:
            days = [day]
        else:
            # Observe the latest completed day even when it has no failed tasks:
            # a late CFFEX/pool write may have changed its input fingerprint.
            due_days = [task['analysis_date'] for task in ledger.due(limit=100)]
            days = list(dict.fromkeys(([latest['analysis_date']] if latest.get('analysis_date') else [])
                                     + due_days))[:limit]
        results = []
        for target in days:
            previous = store.find_sync_state(dataset='daily_data_pipeline', scope=target) or {}
            state = {**previous, 'dataset': 'daily_data_pipeline', 'scope': target,
                     'owner': owner, 'requested_date': target, 'analysis_date': target,
                     'trigger': 'dependency_reconciliation'}
            result = (reconciler or reconcile)(store, target, ledger=ledger, now=now)
            lease.assert_owned()
            publish_reconciliation(store, state, result, ledger=ledger)
            results.append({'analysis_date': target, 'status': result['status'],
                            'changed_stages': result['changed_stages'],
                            'pending_stages': result['pending_stages']})
        return {'status': 'idle' if not results else 'success' if all(r['status'] == 'success' for r in results) else 'degraded',
                'results': results}
    finally:
        lease.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=lambda value: datetime.strptime(value, '%Y-%m-%d').date().isoformat())
    parser.add_argument('--limit', type=int, default=3, choices=range(1, 11))
    args = parser.parse_args(argv)
    config = load_config()
    if not config.mongo_uri:
        raise RuntimeError('SHILUN_MONGO_URI not set')
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=180_000, initialize_indexes=False)
    try:
        result = run_retries(store, args.date, limit=args.limit)
        print(f'data_pipeline_reconcile status={result["status"]} days={len(result["results"])}', flush=True)
        return 0 if result['status'] in ('success', 'idle') else 2
    except PipelineBusy:
        print('data_pipeline_reconcile skipped: data pipeline lease held', flush=True)
        return 75
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
