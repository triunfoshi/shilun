from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
from pymongo.errors import DuplicateKeyError
from fastapi import BackgroundTasks

from shilun.review.strong_stock_data import a_share, listing_no_limit_days, _future_sessions, load_snapshot, SNAPSHOTS, JOBS
from shilun.api.review import rebuild_strong_stocks


def test_scope_and_registered_ipo_unrestricted_days():
    assert all(a_share(t) for t in ['600127.SH','688001.SH','300001.SZ','000001.SZ','920001.BJ'])
    assert not a_share('200001.SZ') and not a_share('900901.SH') and not a_share('000001.SH')
    sessions = ['2026-09-21','2026-09-22','2026-09-23','2026-09-24','2026-09-25','2026-09-28']
    assert listing_no_limit_days({'ts_code':'600001.SH','list_date':'20260921'},sessions) == set(sessions[:5])
    assert listing_no_limit_days({'ts_code':'920001.BJ','list_date':'20260921'},sessions) == {sessions[0]}


def test_future_calendar_stops_at_a_gap_instead_of_guessing_weekdays():
    store=Mock()
    store.find_trade_calendar.return_value = [dict(cal_date='20260923',is_open=1),dict(cal_date='20260925',is_open=1)]
    assert _future_sessions(store,'2026-09-22') == ['2026-09-23']


def test_read_is_versioned_and_expired_worker_is_reported_without_hiding_cache():
    cols={k:Mock() for k in (SNAPSHOTS,JOBS)}
    cols[SNAPSHOTS].find_one.return_value={'status':'ready','date':'2026-09-22','rows':[]}
    cols[JOBS].find_one.return_value={'status':'running','expires_at':datetime.now(timezone.utc)-timedelta(minutes=1)}
    store=SimpleNamespace(collection=cols.__getitem__)
    result=load_snapshot(store,'2026-09-22')
    assert result['status']=='ready' and result['job']['status']=='failed'
    assert cols[SNAPSHOTS].find_one.call_args.args[0]['_id']=='core-qualification-v2:2026-09-22'
    for col in cols.values(): col.update_one.assert_not_called()


@pytest.mark.parametrize('busy', [False, True])
def test_rebuild_requests_coalesce_for_same_day(busy):
    store=Mock()
    if busy: store.collection.return_value.update_one.side_effect=DuplicateKeyError('duplicate')
    tasks=BackgroundTasks()
    with patch('shilun.api.review._open_store',return_value=store), patch('shilun.review.strong_stock_data.resolve_date',return_value='2026-09-22'):
        result=rebuild_strong_stocks(tasks,date(2026,9,22))
    assert result['status']==('running' if busy else 'queued')
    assert len(tasks.tasks)==(0 if busy else 1)
    store.close.assert_called_once()
