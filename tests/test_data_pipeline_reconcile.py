from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from pipeline_fakes import Store
from shilun.jobs.pipeline_state import TaskLedger
from shilun.jobs.data_pipeline_reconcile import reconcile
from shilun.jobs.data_pipeline_retry_job import run_retries
from shilun.jobs.theme_daily_job import ThemeInputError

DAY = '2026-11-03'


def check(key, ready=True, consumers=('strong_stocks', 'review', 'theme_cycles'), blocking=True, rev=None):
    return {'key': key, 'date': DAY, 'status': 'ready' if ready else 'missing', 'ready': ready,
            'blocking': blocking, 'consumers': list(consumers), 'reason': key + ('齐全' if ready else '缺失'),
            'revision': rev or key + str(ready)}


@pytest.fixture
def setup():
    store = Store()
    clock = [datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)]
    ledger = TaskLedger(store, now_fn=lambda: clock[0])
    checks = [check('daily_bars'), check('industry_taxonomy'),
              check('limit_reasons', consumers=('theme_cycles',))]
    calls = []
    strong = Mock(side_effect=lambda *_: calls.append('strong_stocks') or
                  {'date': DAY, 'status': 'ready', 'counts': {'qualified': 5}, 'revision': 'strong-v1'})
    review = Mock()
    review.return_value.build_and_save.side_effect = lambda *_args, **_kw: calls.append('review') or {
        'analysis_date': DAY, 'data_quality': {'status': 'ready'}, 'generated_at': DAY+'T20:00:00+08:00'}
    themes = Mock(side_effect=lambda *_: calls.append('theme_cycles') or {'date': DAY, 'status': 'complete', 'revision': 'themes-v1'})
    prepare = Mock(return_value={'status': 'ok'})
    kwargs = {'dependency_loader': lambda *_: deepcopy(checks), 'dependency_runner': prepare,
              'strong_builder': strong, 'review_factory': review, 'theme_builder': themes, 'ledger': ledger}
    return store, clock, ledger, checks, calls, kwargs


def test_missing_taxonomy_blocks_consumers_without_publishing_false_success(setup):
    store, clock, ledger, checks, calls, kwargs = setup
    checks[1] = check('industry_taxonomy', False)
    kwargs['dependency_runner'].side_effect = ValueError('分类返回日期不一致')
    result = reconcile(store, DAY, now=clock[0], **kwargs)
    assert calls == []
    assert result['status'] == 'waiting'
    assert ledger.get(DAY, 'review')['blocked_by'] == ['dependency:industry_taxonomy', 'strong_stocks']
    task = ledger.get(DAY, 'dependency:industry_taxonomy')
    assert task['attempts'] == 1 and task['error_message'] == '分类返回日期不一致'
    assert task['next_retry_at']


def test_only_due_failed_source_retries_then_unblocks_entire_chain(setup):
    store, clock, ledger, checks, calls, kwargs = setup
    checks[1] = check('industry_taxonomy', False)
    prepare = kwargs['dependency_runner']
    prepare.side_effect = ValueError('行业映射未完成')
    reconcile(store, DAY, now=clock[0], **kwargs)
    clock[0] += timedelta(minutes=1)
    reconcile(store, DAY, now=clock[0], **kwargs)
    assert prepare.call_count == 1 and calls == []
    clock[0] += timedelta(minutes=4)
    def repair(_store, day, key):
        assert day == DAY and key == 'industry_taxonomy'
        checks[1] = check(key)
        return {'status': 'ready', 'ready': True}
    prepare.side_effect = repair
    result = reconcile(store, DAY, now=clock[0], **kwargs)
    assert result['status'] == 'success'
    assert prepare.call_count == 2
    assert calls == ['strong_stocks', 'review', 'theme_cycles']
    assert not ledger.get(DAY, 'dependency:industry_taxonomy').get('error_message')
    kwargs['review_factory'].return_value.build_and_save.assert_called_once_with(DAY, sync_sources=False)
    reconcile(store, DAY, now=clock[0], **kwargs)
    assert calls == ['strong_stocks', 'review', 'theme_cycles']  # Stable inputs are idempotent.


def test_late_futures_refreshes_review_without_refetching_sources_or_rebuilding_core(setup):
    store, clock, ledger, checks, calls, kwargs = setup
    checks.append(check('cffex_positions', False, consumers=('review',), blocking=False))
    kwargs['dependency_runner'].side_effect = ValueError('席位尚未发布')
    first = reconcile(store, DAY, now=clock[0], **kwargs)
    assert first['status'] == 'degraded'
    checks[-1] = check('cffex_positions', consumers=('review',), blocking=False, rev='futures-late')
    second = reconcile(store, DAY, now=clock[0]+timedelta(minutes=1), **kwargs)
    assert second['changed_stages'] == ['review']
    assert calls.count('strong_stocks') == calls.count('theme_cycles') == 1
    assert calls.count('review') == 2
    assert kwargs['dependency_runner'].call_count == 1
    assert second['status'] == 'success'


def test_source_receipt_without_persisted_right_date_cannot_unblock(setup):
    store, clock, ledger, checks, calls, kwargs = setup
    checks[2] = check('limit_reasons', False, consumers=('theme_cycles',))
    kwargs['dependency_runner'].return_value = {'status': 'ok', 'date': '2026-11-02'}
    result = reconcile(store, DAY, now=clock[0], **kwargs)
    assert calls == ['strong_stocks', 'review']
    assert not ledger.ready(DAY, 'theme_cycles')
    assert ledger.get(DAY, 'dependency:limit_reasons')['status'] == 'retry'
    assert result['status'] == 'waiting'


def test_theme_failure_retries_only_theme_and_preserves_last_publication(setup):
    store, clock, ledger, checks, calls, kwargs = setup
    themes = kwargs['theme_builder']
    themes.side_effect = ValueError('发布校验失败')
    reconcile(store, DAY, now=clock[0], **kwargs)
    assert ledger.get(DAY, 'theme_cycles')['status'] == 'retry'
    clock[0] += timedelta(minutes=5)
    themes.side_effect = None
    themes.return_value = {'date': DAY, 'status': 'complete', 'revision': 'recovered'}
    outcome = reconcile(store, DAY, now=clock[0], **kwargs)
    assert outcome['changed_stages'] == ['theme_cycles']
    assert kwargs['strong_builder'].call_count == kwargs['review_factory'].return_value.build_and_save.call_count == 1
    assert ledger.get(DAY, 'theme_cycles')['attempts'] == 2


def test_exact_historical_reason_gap_becomes_separate_repair_task(setup):
    store, clock, ledger, checks, calls, kwargs = setup
    kwargs['theme_builder'].side_effect = ThemeInputError([{'date': '2026-11-02', 'dataset': 'limit_reasons'}])
    reconcile(store, DAY, now=clock[0], **kwargs)
    assert ledger.get(DAY, 'repair:limit_reasons:2026-11-02')['status'] == 'waiting'
    kwargs['theme_builder'].side_effect = None
    kwargs['theme_builder'].return_value = {'date': DAY, 'status': 'complete', 'revision': 'r2'}
    result = reconcile(store, DAY, now=clock[0], **kwargs)
    kwargs['dependency_runner'].assert_called_once_with(store, '2026-11-02', 'limit_reasons')
    assert result['changed_stages'] == ['theme_cycles']


def test_retry_tick_uses_persisted_date_and_never_rolls_back_latest_receipt():
    store = Store()
    latest = '2026-11-03'
    store.upsert_sync_state({'dataset': 'daily_data_pipeline', 'scope': 'latest', 'analysis_date': latest})
    def rebuild(_store, day, **kw):
        return {'analysis_date': day, 'status': 'success', 'stage': 'complete', 'dependencies': [],
                'outputs': {}, 'changed_stages': ['review'], 'pending_stages': []}
    run_retries(store, '2026-11-02', reconciler=rebuild)
    assert store.find_sync_state(dataset='daily_data_pipeline', scope='latest')['analysis_date'] == latest
    assert store.find_sync_state(dataset='daily_data_pipeline', scope='2026-11-02')['status'] == 'success'


def test_idle_retry_worker_does_no_sync_or_materialization():
    store, rebuild = Store(), Mock()
    result = run_retries(store, reconciler=rebuild)
    assert result == {'status': 'idle', 'results': []}
    rebuild.assert_not_called()


def test_partial_usable_source_keeps_retrying_without_blocking_review(setup):
    store, clock, ledger, checks, calls, kwargs = setup
    partial = check('cffex_positions', consumers=('review',), blocking=False, rev='1-of-4')
    partial.update(status='degraded', complete=False, needs_retry=True)
    checks.append(partial)
    kwargs['dependency_runner'].return_value = {'status':'degraded', 'ready':True}
    first = reconcile(store, DAY, now=clock[0], **kwargs)
    assert 'review' in first['changed_stages']
    assert first['status'] == 'degraded'
    assert ledger.get(DAY, 'dependency:cffex_positions')['status'] == 'retry'
    clock[0] += timedelta(minutes=5)
    reconcile(store, DAY, now=clock[0], **kwargs)
    assert kwargs['dependency_runner'].call_count == 2
    assert calls.count('review') == 1  # Same partial inputs do not rebuild.


def test_failed_source_stops_after_five_attempts_even_after_dependency_wait(setup):
    store, clock, ledger, checks, calls, kwargs = setup
    checks[1] = check('industry_taxonomy', False)
    kwargs['dependency_runner'].side_effect = ValueError('仍缺失')
    for minutes in (0, 5, 15, 30, 60):
        clock[0] += timedelta(minutes=minutes)
        reconcile(store, DAY, now=clock[0], **kwargs)
    assert ledger.get(DAY, 'dependency:industry_taxonomy')['status'] == 'failed'
    ledger.wait(DAY, 'dependency:industry_taxonomy', ['dependency:daily_bars'])
    clock[0] += timedelta(hours=2)
    reconcile(store, DAY, now=clock[0], **kwargs)
    assert kwargs['dependency_runner'].call_count == 5


def test_historical_waiting_dates_are_rotated_in_due_order():
    store = Store()
    now = datetime(2026,11,3,12,tzinfo=timezone.utc)
    clock = [now]
    ledger = TaskLedger(store,now_fn=lambda:clock[0])
    dates = ['2026-10-26','2026-10-27','2026-10-28','2026-10-29']
    for day in dates:
        ledger.wait(day,'theme_cycles',['dependency:limit_reasons'])
    store.upsert_sync_state({'dataset':'daily_data_pipeline','scope':'latest','analysis_date':DAY})
    visited = []
    def rebuild(_store, day, ledger, **kw):
        if day in dates:
            visited.append(day)
            ledger.wait(day,'theme_cycles',['dependency:limit_reasons'])
        return {'analysis_date':day,'status':'waiting','stage':'waiting_dependencies', 'dependencies':[],
                'outputs':{},'changed_stages':[],'pending_stages':['theme_cycles']}
    for offset in (1,2):
        clock[0] = now + timedelta(minutes=offset)
        run_retries(store,limit=3,reconciler=rebuild,now=clock[0])
    assert set(visited) == set(dates)
