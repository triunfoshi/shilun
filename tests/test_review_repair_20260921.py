from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from pymongo.errors import DuplicateKeyError

from shilun.review.live_emotion import reconcile_settled_downside
from shilun.review.emotion_history import load_heat_summaries, five_day_heat
from shilun.jobs.sector_precompute_job import start_task, task_status


def test_settled_downside_keeps_raw_pool_and_uses_exact_dated_full_market():
    store = Mock()
    store.collection.return_value.find_one.return_value = {'sentiment': {'downside_risk': {
        'count_ready': True, 'limit_down_count': 1, 'stocks': [{'ticker': '002528.SZ', 'name': '*ST英飞'}]}}}
    store.collection.return_value.find.return_value = []
    raw = {'available': True, 'snapshot_mode': 'settled', 'snapshot_date': '2026-09-18',
           'dt_count': 0, 'raw_pool_counts': {'dt_count': 0}}
    result = reconcile_settled_downside(raw, store)
    assert result['dt_count'] == 1 and result['raw_pool_counts']['dt_count'] == 0
    assert raw['dt_count'] == 0
    assert store.collection.return_value.find_one.call_args.args[0]['analysis_date'] == '2026-09-18'
    store.reset_mock()
    assert reconcile_settled_downside({**raw, 'snapshot_mode': 'live'}, store)['dt_count'] == 0
    store.collection.assert_not_called()


def test_partial_daily_count_does_not_override_live_pool():
    store = Mock()
    store.collection.return_value.find_one.return_value = {'sentiment': {'downside_risk': {
        'count_ready': False, 'limit_down_count': 0, 'stocks': []}}}
    raw = {'available': True, 'snapshot_mode': 'settled', 'snapshot_date': '2026-09-18', 'dt_count': 3}
    assert reconcile_settled_downside(raw, store)['dt_count'] == 3


def test_heat_uses_same_normalized_up_and_broken_cohort_as_trend():
    store = Mock()
    store.collection.return_value.find.return_value = []
    day = '2026-09-18'
    a = {'ticker': '000001.SZ', 'name': '甲', 'boards': 2}
    st = {'ticker': '600001.SH', 'name': 'ST乙', 'boards': 3}
    b = {'ticker': '300001.SZ', 'name': '丙', 'boards': 1}
    ups = {day: {'complete': True, 'source': 'eastmoney_dated_pool', 'stocks': [a, st]}}
    broken = {day: {'complete': True, 'source': 'tushare_limit_list_d', 'stocks': [a, b]}}
    with patch('shilun.review.board_data.BoardResearchData') as data:
        data.return_value.pools.side_effect = [ups, broken]
        summaries = load_heat_summaries(store, [day])
    heat = five_day_heat(day, [day], {day: [{'ticker':'000001.SZ','name':'甲','consecutive_count':2},
                                         {'ticker':'600001.SH','name':'ST乙','consecutive_count':3}]}, {}, summaries)
    assert heat['days'][0]['broken_rate'] == .5
    assert heat['days'][0]['limit_up'] == 2  # Original pool retained and scope labelled.
    assert heat['days'][0]['broken_scope'] == '沪深非ST'


def test_duplicate_sector_submit_reuses_running_job_without_launch():
    store, launch = Mock(), Mock()
    store.collection.return_value.find_one_and_update.side_effect = DuplicateKeyError('duplicate')
    store.collection.return_value.find_one.return_value = {'job_id': 'a'*24, 'status':'running', 'owner':'private'}
    assert start_task(store, '2026-09-18', '000001.SH', launch=launch) == {'job_id':'a'*24,'status':'running'}
    launch.assert_not_called()


def test_sector_submit_returns_before_compute_and_launch_failure_is_persisted():
    store, launch = Mock(), Mock()
    result = start_task(store, '2026-09-18', '000001.SH', launch=launch)
    assert result['status'] == 'queued' and 'owner' not in result
    launch.assert_called_once()
    assert launch.call_args.args[2:] == ('2026-09-18', '000001.SH')
    launch.side_effect = OSError('not public')
    import pytest
    with pytest.raises(OSError):
        start_task(store, '2026-09-18', '000001.SH', launch=launch)
    assert store.collection.return_value.update_one.call_args.args[1]['$set']['status'] == 'failed'


def test_stale_task_is_explicit_not_running_forever():
    result = task_status({'status':'running','expires_at':datetime.now(timezone.utc)-timedelta(seconds=1)})
    assert result['status'] == 'expired'
