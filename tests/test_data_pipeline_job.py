from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pymongo.errors import DuplicateKeyError

from shilun.jobs.data_pipeline_job import PipelineBusy, acquire_lease, run_pipeline
from shilun.jobs.cloud_scheduler import build_scheduler, _run
from shilun.jobs.pipeline_state import TaskLedger
from pipeline_fakes import Store


@pytest.fixture(autouse=True)
def local_strong_builder(monkeypatch):
    monkeypatch.setattr('shilun.review.sources.load_review_sessions', lambda *_: ['2026-09-17', '2026-09-18'])
    monkeypatch.setattr('shilun.jobs.pipeline_dependencies.inspect_dependencies', lambda *_, **kw: [])
    monkeypatch.setattr('shilun.jobs.pipeline_dependencies.inspect_core_history', lambda *_: {'key':'core_history','date':'2026-09-18','ready':True,'status':'ready','blocking':False,'consumers':['strong_stocks','theme_cycles'],'reason':'历史已齐','revision':'core-v1'})
    monkeypatch.setattr('shilun.jobs.pipeline_dependencies.inspect_theme_history', lambda *_: {'key':'theme_history','date':'2026-09-18','ready':True,'status':'ready','blocking':False,'consumers':['theme_cycles'],'reason':'历史已齐','revision':'theme-v1'})
    monkeypatch.setattr('shilun.jobs.pipeline_optional_sources.inspect_optional_sources', lambda *_: [])
    monkeypatch.setattr('shilun.jobs.theme_daily_job.run',
                        lambda store, day: {'date': day, 'status': 'complete', 'revision': 'a'*16})
    monkeypatch.setattr('shilun.review.strength_review.sync_lhb', lambda store, days: [])
    monkeypatch.setattr('shilun.review.strong_stock_data.materialize',
                        lambda store, day: {'date':day,'status':'ready','counts':{'qualified':3}})


def setup_pipeline():
    store, sync, review = Store(), Mock(), Mock()
    sync.return_value.run.return_value = SimpleNamespace(failed_trade_dates=[], sync_trade_date="2026-09-18")
    review.return_value.build_and_save.return_value = {
        "analysis_date": "2026-09-18", "generated_at": "2026-09-20T20:00:00+08:00",
        "data_quality": {"status": "ready", "checks": []}, "source_sync": []}
    return store, sync, review


def test_competing_host_cannot_acquire_unexpired_lease():
    collection = Mock()
    collection.update_one.side_effect = DuplicateKeyError("owned")
    with pytest.raises(PipelineBusy):
        acquire_lease(collection, "second-host", datetime.now(timezone.utc))


def test_sync_failure_never_builds_review_and_releases_lease():
    store, sync, review = setup_pipeline()
    sync.return_value.run.side_effect = ConnectionError('daily API unavailable')
    with pytest.raises(ConnectionError):
        run_pipeline(store, "2026-09-20", sync_factory=sync, review_factory=review)
    review.assert_not_called()
    assert store.find_sync_state(dataset='daily_data_pipeline', scope='active_attempt')['status'] == 'failed'
    assert TaskLedger(store).get('2026-09-18', 'daily_sync')['status'] == 'retry'
    assert not store.collection('job_leases').rows


def test_review_uses_resolved_trading_date_and_records_optional_gaps():
    store, sync, review = setup_pipeline()
    deps = [{'key':'cffex_positions', 'date':'2026-09-18', 'ready':False,
             'status':'missing', 'blocking':False, 'consumers':['review'], 'revision':'missing', 'reason':'未发布'}]
    state = run_pipeline(store, "2026-09-20", sync_factory=sync, review_factory=review,
                         dependency_loader=lambda *_: deps, dependency_runner=Mock(side_effect=ValueError('尚未发布')))
    review.return_value.build_and_save.assert_called_once_with("2026-09-18", sync_sources=False)
    assert state["status"] == "degraded"
    assert state["analysis_date"] == "2026-09-18"
    assert not store.collection('job_leases').rows
    assert store.find_sync_state(dataset='daily_data_pipeline', scope='active_attempt')['status'] == 'degraded'


def test_wrong_review_date_fails_run():
    store, sync, review = setup_pipeline()
    review.return_value.build_and_save.return_value["analysis_date"] = "2026-09-17"
    state = run_pipeline(store, "2026-09-18", sync_factory=sync, review_factory=review)
    assert state['status'] == 'waiting'
    assert TaskLedger(store).get('2026-09-18','review')['status'] == 'retry'


def test_strong_stock_failure_is_recorded_and_releases_pipeline_lease():
    store, sync, review = setup_pipeline()
    strong=Mock(side_effect=ValueError('Missing calendar'))
    state = run_pipeline(store,'2026-09-18',sync_factory=sync,review_factory=review,strong_builder=strong)
    strong.assert_called_once_with(store,'2026-09-18')
    assert state['stage']=='waiting_dependencies' and state['status']=='waiting'
    assert TaskLedger(store).get('2026-09-18','strong_stocks')['error_message'] == 'Missing calendar'
    assert not store.collection('job_leases').rows


def test_data_only_schedule_has_no_trading_or_notification_jobs():
    ids = {job.id for job in build_scheduler(data_only=True).get_jobs()}
    assert "minute_bars_1515" in ids
    assert "price_limits_0845" in ids
    assert "daily_review_retry" in ids
    assert not any("trading" in key or "holding" in key or "structure" in key for key in ids)


def test_child_exit_code_is_not_swallowed(monkeypatch):
    monkeypatch.setattr("shilun.jobs.cloud_scheduler.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=2))
    assert _run("test", "unused") == 2


def test_theme_failure_keeps_pipeline_at_theme_stage_and_can_be_retried():
    store, sync, review = setup_pipeline()
    theme = Mock(side_effect=ValueError('missing dated taxonomy'))
    state = run_pipeline(store, '2026-09-18', sync_factory=sync, review_factory=review, theme_builder=theme)
    assert state['stage'] == 'waiting_dependencies' and state['status'] == 'waiting'
    assert TaskLedger(store).get('2026-09-18','theme_cycles')['status'] == 'retry'
    assert not store.collection('job_leases').rows


@pytest.mark.parametrize('date,status', [('2026-09-17','complete'), ('2026-09-18','failed')])
def test_wrong_or_unpublished_theme_cannot_mark_pipeline_success(date, status):
    store, sync, review = setup_pipeline()
    result = run_pipeline(store, '2026-09-18', sync_factory=sync, review_factory=review,
                          theme_builder=lambda *a: {'date':date,'status':status})
    assert result['status'] == 'waiting'
    assert TaskLedger(store).get('2026-09-18','theme_cycles')['status'] == 'retry'


def test_historical_full_run_cannot_overwrite_newer_pipeline_receipt():
    store, sync, review = setup_pipeline()
    store.upsert_sync_state({'dataset':'daily_data_pipeline','scope':'latest','analysis_date':'2026-11-03', 'status':'success'})
    run_pipeline(store,'2026-09-18',sync_factory=sync,review_factory=review)
    assert store.find_sync_state(dataset='daily_data_pipeline',scope='latest')['analysis_date'] == '2026-11-03'


def test_sync_intent_is_running_before_provider_call():
    store, sync, review = setup_pipeline()
    output = sync.return_value.run.return_value
    def provider(_request):
        assert TaskLedger(store).get('2026-09-18','daily_sync')['status'] == 'running'
        return output
    sync.return_value.run.side_effect = provider
    run_pipeline(store,'2026-09-18',sync_factory=sync,review_factory=review)
