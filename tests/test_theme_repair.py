from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import shlex
import subprocess

import pytest

from shilun.review import theme_repair as repair
from shilun.review.board_data import REASON_COLLECTION
from shilun.review.theme_distribution import CONTROL
from shilun.jobs.theme_daily_job import ThemeInputError
from test_theme_daily_job import Collection, Store

DAY = '2026-11-02'


class QueueCollection(Collection):
    def matches(self, row, query):
        for key, value in query.items():
            if key == '$or':
                if not any(self.matches(row, part) for part in value): return False
            elif isinstance(value, dict) and '$nin' in value:
                if row.get(key) in value['$nin']: return False
            elif not super().matches(row, {key: value}): return False
        return True


class QueueStore(Store):
    def collection(self, name):
        return self.collections.setdefault(name, QueueCollection())


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr('shilun.review.board_data.settled_target', lambda d: d)
    monkeypatch.setattr('shilun.review.sources.load_review_sessions', lambda s, d: [d])
    return QueueStore()


def test_submit_is_persisted_and_repeated_or_other_date_click_is_reused(store):
    launcher = Mock()
    first = repair.start(store, DAY, launcher=launcher)
    assert first['date'] == DAY and first['status'] == 'queued'
    assert store.collection(repair.JOBS).rows['active']['run_id'] == first['run_id']
    second = repair.start(store, DAY, launcher=launcher)
    other = repair.start(store, '2026-11-03', launcher=launcher)
    assert second['reused'] and second['run_id'] == first['run_id']
    assert other['reused'] and other['date'] == DAY
    assert launcher.call_count == 1


def test_expired_task_can_be_retried_and_launch_error_is_visible_without_secrets(store):
    first = repair.start(store, DAY, launcher=Mock())
    jobs = store.collection(repair.JOBS)
    jobs.rows['active']['expires_at'] = datetime.now(timezone.utc)-timedelta(seconds=1)
    assert repair.status(store, DAY)['status'] == 'failed'
    result = repair.start(store, DAY, launcher=Mock(side_effect=RuntimeError('secret SSH debug')))
    assert result['status'] == 'failed' and result['stage'] == 'launch'
    assert result['run_id'] != first['run_id']
    assert 'secret' not in result['message']
    assert 'secret' not in str(jobs.rows['active'])


def test_dates_earlier_than_publication_and_unsettled_dates_do_not_launch(store, monkeypatch):
    launch = Mock()
    store.collection(CONTROL).insert_one({'_id': 'current', 'date': '2026-11-03'})
    with pytest.raises(ValueError, match='早于'):
        repair.start(store, DAY, launcher=launch)
    monkeypatch.setattr('shilun.review.board_data.settled_target', lambda d: '2026-10-30')
    with pytest.raises(ValueError, match='收盘'):
        repair.start(store, DAY, launcher=launch)
    launch.assert_not_called()


def test_lost_launch_ack_cannot_overwrite_worker_running_state(store):
    def launch(day, run_id):
        store.collection(repair.JOBS).rows['active'].update(status='running',stage='analyze')
        raise subprocess.TimeoutExpired('ssh',20)
    result=repair.start(store,DAY,launcher=launch)
    assert result['status']=='running' and result['stage']=='analyze'


def test_uncertain_launch_stays_queued_and_is_reused_until_deadline(store):
    launch=Mock(side_effect=subprocess.TimeoutExpired('ssh',20))
    result=repair.start(store,DAY,launcher=launch)
    assert result['status']=='queued' and result['stage_label']=='启动结果待确认'
    assert repair.start(store,DAY,launcher=launch)['reused']
    assert launch.call_count==1


def market():
    days = ['2026-08-21', '2026-10-08', '2026-10-09', DAY]
    return {'context_days': days, 'pools': {d: {'stocks': [{'code': '000001'}]} for d in days},
            'reasons': {DAY: {'date': DAY, 'reasons': {'000001': '已归档'}}}}


def test_backfill_only_requested_missing_dates_keeps_baseline_and_existing_reason(store):
    data, save, calls = market(), Mock(), []
    def sync(s, day):
        calls.append(day)
        s.collection(REASON_COLLECTION).insert_one({'_id': day, 'date': day, 'source': 'ths_dated_limit_reasons',
                                                  'reasons': {'000001': '同日原因'}})
        return {'status': 'ok'}
    result = repair.backfill_market(store, data, save, repair_days=['2026-10-08', '2026-10-09', DAY], sync=sync)
    assert calls == ['2026-10-08', '2026-10-09']
    assert '2026-08-21' not in result['reasons'], 'accepted baseline is not enlarged by recovery'
    assert result['reasons'][DAY]['reasons']['000001'] == '已归档'
    assert [r['date'] for r in save.call_args.kwargs['reports']] == calls
    assert save.call_args.kwargs['stage'] == 'analyze'


@pytest.mark.parametrize('response,record', [
    ({'status': 'unavailable'}, None),
    ({'status': 'ok'}, {'date': '2026-10-08', 'reasons': {}}),
    ({'status': 'ok'}, {'date': DAY, 'reasons': {'000001': '未来原因'}}),
])
def test_bad_or_wrong_day_reason_never_unblocks_analysis(store, response, record):
    if record:
        store.collection(REASON_COLLECTION).insert_one({'_id': 'bad', **record})
    data, save = market(), Mock()
    with pytest.raises(ThemeInputError) as error:
        repair.backfill_market(store, data, save, repair_days=['2026-10-08'], sync=lambda *a: response)
    assert error.value.missing[0]['date'] == '2026-10-08'
    assert '2026-10-08' not in data['reasons']
    assert all(c.kwargs['stage'] != 'analyze' for c in save.call_args_list)


def test_remote_launch_is_detached_fixed_command_and_rejects_untrusted_arguments(monkeypatch):
    monkeypatch.setattr(repair, 'runner_settings', lambda: ('user@host', Path('/tmp/test-key'), 'data-container'))
    execute = Mock(return_value=SimpleNamespace(returncode=0))
    repair.launch(DAY, 'a'*32, execute=execute)
    command = execute.call_args.args[0]
    remote = shlex.split(command[-1])
    assert remote[:7] == ['docker', 'exec', '-d', '-w', '/app', 'data-container', 'python']
    assert remote[-2:] == [DAY, 'a'*32]
    assert execute.call_args.kwargs['timeout'] == 20
    assert 'shell' not in execute.call_args.kwargs
    with pytest.raises(ValueError): repair.launch(DAY+';whoami', 'a'*32, execute=execute)
    with pytest.raises(ValueError): repair.launch(DAY, '$(whoami)', execute=execute)
    assert execute.call_count == 1


def test_runner_settings_rejects_invalid_target_and_container(tmp_path, monkeypatch):
    key=tmp_path/'key';key.write_text('placeholder')
    monkeypatch.setenv('SHILUN_THEME_REPAIR_SSH_KEY',str(key))
    monkeypatch.setenv('SHILUN_THEME_REPAIR_SSH_TARGET','root@host;invalid')
    with pytest.raises(ValueError): repair.runner_settings()
    monkeypatch.setenv('SHILUN_THEME_REPAIR_SSH_TARGET','root@host')
    monkeypatch.setenv('SHILUN_THEME_REPAIR_CONTAINER','--privileged')
    with pytest.raises(ValueError): repair.runner_settings()


def test_status_is_read_only_and_does_not_attach_a_stale_pipeline_stage(store):
    store.collection(repair.JOBS).insert_one({'_id':'active','date':DAY,'status':'running','stage':'analyze',
       'stage_label':'连续回放','started_at':'2026-11-02T20:00:00+00:00'})
    store.collection(CONTROL).insert_one({'_id':'job','date':DAY,'started_at':'2026-11-02T10:00:00+00:00',
       'stage_label':'旧任务失败'})
    before=deepcopy(store.collections)
    result=repair.status(store,DAY)
    assert result['stage_label']=='连续回放'
    assert store.collection(repair.JOBS).rows == before[repair.JOBS].rows


def test_api_read_survives_optional_task_status_failure_and_never_launches(monkeypatch):
    from datetime import date
    from shilun.api import theme_cycles as api
    monkeypatch.setattr(api.theme_distribution,'enabled',lambda:True)
    monkeypatch.setattr(api.theme_cycle_store,'day_payload',lambda *a,**k:{'date':DAY,'status':'ready','active':[]})
    monkeypatch.setattr(api,'open_store',Mock(side_effect=RuntimeError('private connection detail')))
    launch=Mock();monkeypatch.setattr(api.theme_repair,'start',launch)
    result=api.get_cycles(date.fromisoformat(DAY),None)
    assert result['status']=='ready' and result['repair_supported'] is False
    assert 'private' not in result['repair_unavailable_reason']
    launch.assert_not_called()


def test_api_post_maps_database_failure_to_readable_503(monkeypatch):
    from datetime import date
    from fastapi import HTTPException
    from pymongo.errors import ConnectionFailure
    from shilun.api import theme_cycles as api
    monkeypatch.setattr(api.theme_distribution,'enabled',lambda:True)
    monkeypatch.setattr(api,'open_store',Mock(side_effect=ConnectionFailure('private details')))
    with pytest.raises(HTTPException) as error: api.repair_cycles(date.fromisoformat(DAY))
    assert error.value.status_code==503 and 'private' not in error.value.detail
