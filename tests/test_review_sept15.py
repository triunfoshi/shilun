from unittest.mock import MagicMock, patch
from datetime import date, timedelta

from shilun.review.board_effects import build_effects
from shilun.review.theme_evidence import downside_risk, theme_evidence
from shilun.review.market_mood import regime_for_mood
from shilun.review.industry_perspectives import industry_candidates


def test_mood_groups_fixed_and_use_cohort_day_not_feedback_day():
    dates=[(date(2026,8,1)+timedelta(days=i)).isoformat() for i in range(32)]
    per_day={d:[dict(code='a',ret=2,prev_boards=1,seal_time='093100')] for d in dates[1:]}
    moods={d:{'code':'normal'} for d in dates}
    moods[dates[-2]]={'code':'cold'};moods[dates[-1]]={'code':'hot'}
    a=build_effects(dates=dates,per_day=per_day,moods=moods,requested=30)
    b=build_effects(dates=dates,per_day=per_day,moods=moods,requested=20)
    assert a['regime_dates'][dates[-1]] == b['regime_dates'][dates[-1]]
    assert a['regime_dates'][dates[-1]]['regime']=='情绪弱'
    assert a['strategies'][0]['by_regime']['情绪强']['sample']==0
    assert a['strategies'][0]['by_regime']['情绪弱']['sample']==1
    assert a['strategies'][0]['by_regime']['情绪中']['sample']==29
    assert b['strategies'][0]['by_regime']['情绪中']['sample']==19
    assert a['strategies'][0]['overall']['sample']==30
    assert a['strategies'][0]['overall']['avg']==2
    assert regime_for_mood({'code':'pending'})=='环境未覆盖'


def test_suspended_days_do_not_make_high_stock_unknown_but_missing_still_does():
    dates=[(date(2026,8,1)+timedelta(days=i)).isoformat() for i in range(25)]
    suspended=set(dates[-4:-1]);bars={d:{'a':dict(close=19,high=20,low=10)} for d in dates if d not in suspended}
    args=dict(analysis_date=dates[-1],limit_down_tickers={'a'},current_bars=bars[dates[-1]],previous_bars={},
        bars_by_ticker={},bars_by_date=bars,price_limits_by_date={},basic={},indices=[],sessions=dates)
    unknown=downside_risk(**args)
    assert unknown['stocks'][0]['position']=='unknown'
    valid=downside_risk(**args,suspensions={'a':suspended})
    assert valid['stocks'][0]['position']=='high'
    assert valid['stocks'][0]['history_days']==20
    del bars[dates[-6]]
    assert downside_risk(**args,suspensions={'a':suspended})['stocks'][0]['position']=='unknown'


def test_topic_floor_requires_distinct_stocks_and_keeps_two_plus_inclusive():
    def result(rows):
        return theme_evidence(analysis_date='2026-09-14',limit_rows_by_date={'2026-09-14':rows},
            current_bars={},previous_bars={},bars_by_ticker={},moneyflow={})['themes']
    def r(t,n):return dict(ticker=t,consecutive_count=n,theme_name='农业',broken_count=0)
    assert not result([r('a',4)])
    assert not result([r('a',2),r('b',2)])
    assert len(result([r('a',3),r('b',2)]))==1


def test_sw_views_rank_leaves_and_keep_l1_l2_as_context_only():
    rows=[dict(l1_code='801080.SI',l1_name='电子',l2_code='801083.SI',l2_name='元件',
        l3_code='850822.SI',l3_name='印制电路板',ts_code=str(i),name=str(i)) for i in range(6)]
    rows += [dict(rows[0],ts_code='other',l3_code='850823.SI',l3_name='被动元件')]
    store=MagicMock();store.collection.return_value.find_one.return_value={'rows':rows}
    views=industry_candidates(store,MagicMock(),'2026-09-09')
    assert {v['name'] for v in views}=={'印制电路板'}
    assert len(views[0]['members'])==6
    assert views[0]['industry_level']=='l3'
    assert [p['name'] for p in views[0]['industry_parents']]==['电子','元件']
    assert 'other' not in {r['con_code'] for r in views[0]['members']}


def test_missing_leaf_hierarchy_cannot_silently_fall_back_to_parent():
    import pytest
    rows=[dict(l1_code='801080.SI',l1_name='电子',ts_code=str(i),name=str(i)) for i in range(6)]
    store=MagicMock();store.collection.return_value.find_one.return_value={'rows':rows}
    with pytest.raises(ValueError,match='三级行业成分缺失'):
        industry_candidates(store,MagicMock(),'2026-09-09')


def test_missing_topic_materialization_cannot_use_legacy_industry_proxy():
    rows=[dict(ticker='a',consecutive_count=3,theme_name='电子'),
          dict(ticker='b',consecutive_count=2,theme_name='电子')]
    result=theme_evidence(analysis_date='2026-09-14',limit_rows_by_date={'2026-09-14':rows},
        current_bars={},previous_bars={},bars_by_ticker={},moneyflow={},perspectives={})
    assert result['status']=='pending' and result['themes']==[]


def test_old_topic_snapshot_is_hidden_without_changing_other_review_facts():
    from shilun.review.service import DailyReviewService
    from shilun.review.topic_perspectives import VERSION
    old={'market':{'marker':1},'sentiment':{'market_mood':{'code':'normal'},
         'theme_evidence':{'themes':[{'name':'电子'}]}}}
    store=MagicMock();store.collection.return_value.find_one.return_value=old
    svc=DailyReviewService(store)
    with patch.object(svc,'latest_date',return_value='2026-09-14'), \
         patch('shilun.review.service.load_review_sessions',return_value=[]), \
         patch('shilun.review.service.load_futures_history',return_value={}):
        result=svc.get('2026-09-14')
        assert result['sentiment']['theme_evidence']['status']=='pending'
        assert result['sentiment']['market_mood']==old['sentiment']['market_mood']
        assert result['market']['marker']==1
        assert old['sentiment']['theme_evidence']['themes']==[{'name':'电子'}]
        old['sentiment']['theme_evidence']['perspective_version']=VERSION
        assert svc.get('2026-09-14')['sentiment']['theme_evidence']==old['sentiment']['theme_evidence']


def test_futures_endpoint_does_not_call_full_market_permission():
    from shilun.api.review import refresh_futures
    store=MagicMock();payload={'status':'ok','target_date':'2026-09-14'}
    with patch('shilun.api.review._open_store',return_value=store), patch('shilun.api.review.DailyReviewService') as service, patch('shilun.api._load_cffex_positions_snapshot',return_value=payload) as sync, patch('shilun.api.market_permission') as full:
        service.return_value.latest_date.return_value='2026-09-14'
        result=refresh_futures(date(2026,9,14))
        assert result['status']=='ok';sync.assert_called_once_with(store,'2026-09-14',refresh=True);full.assert_not_called()
        store.collection.return_value.update_many.assert_called_once();store.close.assert_called_once()


def test_review_connection_skips_index_ddl_and_closes_on_failure():
    from shilun.common.db import MongoSnapshotStore
    with patch('shilun.common.db.get_mongo_client') as client, patch.object(MongoSnapshotStore, 'ensure_indexes') as ddl:
        store=MongoSnapshotStore('mongodb://example','test',initialize_indexes=False)
        ddl.assert_not_called();store.close();client.return_value.close.assert_called_once()
    with patch('shilun.common.db.get_mongo_client') as client, patch.object(MongoSnapshotStore, 'ensure_indexes',side_effect=RuntimeError('failed')):
        import pytest
        with pytest.raises(RuntimeError):MongoSnapshotStore('mongodb://example','test')
        client.return_value.close.assert_called_once()


def test_provider_nan_suspension_timing_means_full_day():
    from shilun.review.suspensions import load_suspensions
    store=MagicMock();store.collection.return_value.find.return_value=[
        {'ticker':'a','date':'2026-09-09','suspend_timing':float('nan')},
        {'ticker':'b','date':'2026-09-09','suspend_timing':'09:30-10:00'}]
    assert load_suspensions(store,['2026-09-09'])=={'a':{'2026-09-09'}}
