"""One leased, dated data sync followed by review materialization."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import socket
import uuid

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.jobs.heavy_job_lease import HeavyJobLease, PipelineBusy, acquire_lease


def run_pipeline(store, target_date, *, sync_factory=None, review_factory=None, strong_builder=None,
                 theme_builder=None, dependency_loader=None, dependency_runner=None):
    from shilun.jobs.tushare_sync_job import TushareSyncJob, TushareSyncRequest
    from shilun.jobs.pipeline_state import TaskLedger, error_details
    from shilun.jobs.data_pipeline_reconcile import reconcile

    owner = f"{socket.gethostname()}:{uuid.uuid4()}"
    leases = store.collection("job_leases")
    lease = HeavyJobLease(leases, owner).start()
    state = {"dataset": "daily_data_pipeline", "scope": "active_attempt", "owner": owner,
             "requested_date": target_date, "status": "running", "stage": "daily_sync",
             "analysis_date": None, "intent_date": None,
             "error_type": None, "error_message": None, "unavailable_sources": [],
             "finished_at": None}
    ledger, day, intent_day = TaskLedger(store), None, None

    def save(**values):
        lease.assert_owned()
        state.update(values, updated_at=datetime.now(timezone.utc).isoformat())
        store.upsert_sync_state(dict(state))
        if state.get('analysis_date'):
            store.upsert_sync_state({**state, 'scope': state['analysis_date']})
            latest = store.find_sync_state(dataset='daily_data_pipeline', scope='latest') or {}
            if not latest.get('analysis_date') or latest['analysis_date'] <= state['analysis_date']:
                store.upsert_sync_state({**state, 'scope': 'latest'})
        print(f"data_pipeline stage={state['stage']} status={state['status']} date={state.get('analysis_date', target_date)}", flush=True)

    try:
        from shilun.review.sources import load_review_sessions
        from zoneinfo import ZoneInfo
        requested = target_date or datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        verified = load_review_sessions(store, requested)
        intent_day = verified[-1] if verified else requested
        # Persist intent before the long provider call so a process crash is
        # discoverable even before the first analysis_date receipt exists.
        ledger.start(intent_day, 'daily_sync', input_revision=owner)
        state['intent_date'] = intent_day
        save()
        job = (sync_factory or TushareSyncJob)(mongo_store=store)
        result = job.run(TushareSyncRequest(target_date=target_date, latest_only=True,
            sync_all_benchmarks=True, skip_if_exists=False))
        if not result.sync_trade_date:
            raise RuntimeError("Daily sync returned no verified trading date")
        day = result.sync_trade_date
        if day != intent_day:
            ledger.succeed(intent_day, 'daily_sync', result={'resolved_date': day, 'request_only': True})
            ledger.start(day, 'daily_sync', input_revision=owner)
        if result.failed_trade_dates:
            ledger.fail(day, 'daily_sync', RuntimeError('; '.join(result.failed_trade_dates)))
        else:
            ledger.succeed(day, 'daily_sync')
        save(stage='dependencies', analysis_date=day)
        outcome = reconcile(store, day, dependency_loader=dependency_loader, dependency_runner=dependency_runner,
                            strong_builder=strong_builder, review_factory=review_factory,
                            theme_builder=theme_builder, ledger=ledger, progress=save)
        lease.assert_owned()
        publish_reconciliation(store, state, outcome, ledger=ledger)
        return dict(state)
    except Exception as error:
        if intent_day and state['stage'] == 'daily_sync':
            ledger.fail(intent_day, 'daily_sync', error)
        save(status="failed", **error_details(error))
        raise
    finally:
        lease.close()


def publish_reconciliation(store, state, outcome, *, ledger):
    """Publish date-specific and current receipts without stale error fields."""
    day = outcome['analysis_date']
    checks = outcome['dependencies']
    if all(c.get('ready') for c in checks if c.get('blocking') and
           'strong_stocks' in c.get('consumers', [])):
        sync = ledger.get(day, 'daily_sync')
        if sync and sync.get('status') not in ('success', 'degraded'):
            ledger.succeed(day, 'daily_sync', result={'resolved_by': 'verified_dependency_repair'})
    tasks = ledger.snapshot(day)
    failures = [t for t in tasks if t.get('status') not in ('success', 'degraded')]
    review = outcome['outputs'].get('review', {})
    strong = outcome['outputs'].get('strong_stocks', {})
    themes = outcome['outputs'].get('theme_cycles', {})
    state.update(analysis_date=day, stage=outcome['stage'], status=outcome['status'],
                 updated_at=datetime.now(timezone.utc).isoformat(),
                 finished_at=datetime.now(timezone.utc).isoformat(),
                 error_type=None, error_message=None,
                 unavailable_sources=[{'dataset': t['stage'], 'date': day, 'status': t['status'],
                     'error_type': t.get('error_type'), 'error_message': t.get('error_message'),
                     'next_retry_at': t.get('next_retry_at'), 'blocked_by': t.get('blocked_by', [])}
                     for t in failures],
                 changed_stages=outcome['changed_stages'], tasks=tasks,
                 dependencies=checks)
    if review:
        state.update(generated_at=review.get('generated_at'), quality_status=review.get('data_quality', {}).get('status'))
    if strong:
        state['strong_stock_counts'] = strong.get('counts', {})
    if themes:
        state.update(theme_revision=themes.get('revision'), theme_diagnostic_count=themes.get('diagnostic_count', 0))
    store.upsert_sync_state({**state, 'dataset': 'daily_data_pipeline', 'scope': day})
    active = store.find_sync_state(dataset='daily_data_pipeline', scope='active_attempt') or {}
    if active.get('owner') == state.get('owner'):
        store.upsert_sync_state({**state, 'dataset': 'daily_data_pipeline', 'scope': 'active_attempt'})
    # Historical retry must never replace the latest day's live status.
    latest = store.find_sync_state(dataset='daily_data_pipeline', scope='latest') or {}
    if not latest.get('analysis_date') or latest['analysis_date'] <= day:
        store.upsert_sync_state({**state, 'dataset': 'daily_data_pipeline', 'scope': 'latest'})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    args = parser.parse_args(argv)
    config = load_config()
    if not config.mongo_uri:
        raise RuntimeError("SHILUN_MONGO_URI not set")
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=180000)
    try:
        return 0 if run_pipeline(store, args.date)["status"] == "success" else 2
    except PipelineBusy:
        print("data_pipeline skipped: another instance owns the lease", flush=True)
        return 75
    except Exception as error:
        print(f"data_pipeline failed: {type(error).__name__}", flush=True)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
