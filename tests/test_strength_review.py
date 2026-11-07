from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import Mock, patch
import math
import pandas as pd
import pytest

from shilun.market.research_scope import stock_allowed, topic_allowed
from shilun.review import strength_review as sr


@pytest.mark.parametrize('row', [{'name':'*ST示例'}, {'name':'S T示例'}, {'name':'示例退'}, {'list_status':'D'}, {'delist_date':'20260820'}])
def test_stock_scope_excludes_risk_and_delisted(row):
    assert not stock_allowed(row, '2026-09-22')


@pytest.mark.parametrize('name', ['昨日连板_含一字','历史新高','最近多板','昨日高换手','2026中报首亏'])
def test_factor_baskets_are_not_themes(name):
    assert not topic_allowed({'name':name})


def test_real_broad_topics_stay_in_scope():
    from shilun.review.topic_attention import within_review_member_limit
    assert within_review_member_limit({'name':'CPO概念','member_count':95})
    assert not within_review_member_limit({'name':'CPO概念','member_count':None})
    assert all(topic_allowed({'name':n}) for n in ['CPO概念','新零售','粮食概念','创新药','消费电子'])


def test_market_counts_include_three_exchanges_and_do_not_fake_missing_coverage():
    day='2026-09-22'; bars=defaultdict(dict); limits=defaultdict(dict)
    stocks=[{'ts_code':t} for t in ['600001.SH','000001.SZ','920001.BJ','600002.SH']]
    for t,close,high in [('600001.SH',11,11),('000001.SZ',10,11),('920001.BJ',9,10)]:
        bars[t][day]={'close':close,'high':high,'low':9,'volume':100}
        limits[t][day]={'up_limit':11,'down_limit':9}
    r=sr.market_day(day,stocks,bars,limits,{})['markets']
    assert (r['ALL']['up'],r['ALL']['broken'],r['ALL']['down']) == (1,1,1)
    assert r['ALL']['seal_rate']==.5 and not r['ALL']['complete']
    assert (r['ALL']['covered'],r['ALL']['expected'])==(3,4)
    assert r['BJ']['down']==1 and r['BJ']['seal_rate'] is None


def test_range_uses_verified_sessions_and_rejects_inverted_dates():
    days=['2026-09-17','2026-09-18','2026-09-21','2026-09-22']
    with patch.object(sr,'load_review_sessions',return_value=days):
        assert sr.selected_sessions(None,days[-1],'2026-09-19')==days[-2:]
        assert sr.selected_sessions(None,days[-1],window=2)==days[-2:]
        with pytest.raises(ValueError): sr.selected_sessions(None,days[-1],'2026-09-23')


def test_rank_compounds_daily_references_and_does_not_require_five_boards():
    cols=defaultdict(Mock);store=SimpleNamespace(collection=cols.__getitem__)
    cols['stock_basic'].find.return_value=[{'ts_code':'600001.SH','name':'普通股'},{'ts_code':'600002.SH','name':'*ST示例'}]
    cols['market_daily_bars'].aggregate.return_value=[{'_id':'600001.SH','log_return':math.log(1.1)+math.log(.9),'close':9.9,'pre_close':11}]
    with patch.object(sr,'selected_sessions',return_value=['2026-09-21','2026-09-22']),patch.object(sr,'load_review_sessions',return_value=['2026-09-21','2026-09-22']),patch('shilun.market.taxonomy.classify_stocks',side_effect=lambda s,d,r,**kw:r),patch('shilun.review.strong_stock_data.load_snapshot',return_value={'rows':[]}),patch('shilun.review.strong_stock_data._future_sessions',return_value=['2026-09-23']):
        r=sr.rank_range(store,'2026-09-22')
    assert r['rows'][0]['return']==pytest.approx(-.01)
    assert r['rows'][0]['core_status'] is None and r['limit']==50
    p=cols['market_daily_bars'].aggregate.call_args.args[0]
    assert p[0]['$match']['ticker']['$in']==['600001.SH']
    assert p[3]['$match']['valid']==2 and p[3]['$match']['observed']==2


@pytest.mark.parametrize('available', [True, False])
def test_rank_reads_same_day_industry_projection_without_needing_concepts(available):
    from shilun.market.taxonomy import SNAPSHOTS, STOCKS, VERSION
    day='2026-09-22'; ticker='600001.SH'; revision='same-day-l3'
    cols=defaultdict(Mock); store=SimpleNamespace(collection=cols.__getitem__)
    cols['stock_basic'].find.return_value=[{'ts_code':ticker,'name':'示例','industry':'当前证券资料行业'}]
    cols['market_daily_bars'].aggregate.return_value=[{'_id':ticker,'log_return':0,'close':10,'pre_close':10}]
    cols[SNAPSHOTS].find_one.side_effect=[None, {'revision':revision,'date':day} if available else None]
    cols[STOCKS].find.return_value=[{'ticker':ticker,'industry':'元件','industry_code':'BK-L3.DC',
        'industry_source':'东方财富','taxonomy_version':VERSION,'taxonomy_date':day,
        'taxonomy_scope':'industry_l3','classification_status':'ready'}]
    with patch.object(sr,'selected_sessions',return_value=[day]),patch.object(sr,'load_review_sessions',return_value=[day]),patch('shilun.review.strong_stock_data.load_snapshot',return_value={'rows':[]}),patch('shilun.review.strong_stock_data._future_sessions',return_value=[]):
        row=sr.rank_range(store,day)['rows'][0]
    assert cols[SNAPSHOTS].find_one.call_args.args[0] == {
        '_id':f'{VERSION}:industry_l3:{day}','status':'ready','scope':'industry_l3'}
    assert row['taxonomy_date']==day and row['industry_source']=='东方财富'
    if available:
        assert row['industry']=='元件' and row['classification_status']=='ready'
        assert row['taxonomy_scope']=='industry_l3' and row['industry_code']=='BK-L3.DC'
        assert cols[STOCKS].find.call_args.args[0] == {'revision':revision,'ticker':{'$in':[ticker]}}
    else:
        assert row['industry']=='分类待补齐' and row['classification_status']=='taxonomy_missing'
        cols[STOCKS].find.assert_not_called()
    for col in cols.values():
        col.replace_one.assert_not_called()
        col.update_one.assert_not_called()


def test_stock_history_amount_units_reasons_and_multi_reason_seats():
    day='2026-09-22';t='600001.SH'; cols=defaultdict(Mock);store=SimpleNamespace(collection=cols.__getitem__)
    cols['stock_basic'].find_one.return_value={'ts_code':t,'name':'示例'}
    bars=[{'ticker':t,'date':day,'open':10,'high':11,'low':10,'close':11,'pre_close':10,'volume':1,'amount':2500}]
    cols['market_daily_bars'].find.side_effect=[bars,[]]
    cols['daily_price_limits'].find.return_value=[{'trade_date':'20260922','up_limit':11}]
    cols['review_board_reasons'].find.return_value=[{'date':day,'reasons':{'600001':'创新药'},'source':'dated'}]
    cols[sr.MARKET].find.return_value=[]
    cols[sr.LHB].find.return_value=[{'date':day,'summaries':[{'ts_code':t,'reason':'A'},{'ts_code':t,'reason':'B'}],'seats':[{'ts_code':t,'reason':'A','buy':100,'side':'0'},{'ts_code':t,'reason':'B','buy':200,'side':'0'}]}]
    with patch.object(sr,'selected_sessions',return_value=[day]):r=sr.stock_detail(store,t,day)
    assert r['rows'][0]['amount']==2500000 and r['rows'][0]['reason']=='创新药'
    assert r['rows'][0]['market'] is None and r['rows'][0]['benchmark_return'] is None
    assert [e['seats'][0]['buy'] for e in r['lhb']]==[100,200]
    assert not r['lhb_missing_dates']


def test_lhb_failure_is_not_archived_as_no_events_and_valid_empty_day_is():
    col=Mock();col.find_one.return_value=None;store=SimpleNamespace(collection=lambda n:col);client=Mock()
    client._call_api.side_effect=RuntimeError('not available')
    assert sr.sync_lhb(store,['2026-09-22'],client)[0]['status']=='unavailable'
    col.replace_one.assert_not_called()
    client._call_api.side_effect=None;client._call_api.return_value=pd.DataFrame()
    assert sr.sync_lhb(store,['2026-09-22'],client)[0]['events']==0
    assert col.replace_one.call_args.args[1]['status']=='ready'


def test_minutes_never_substitute_another_date():
    col=Mock();col.find.return_value=[{'interval':'1','bars':[{'datetime':'2026-09-21T09:31:00','close':1}]}]
    store=SimpleNamespace(collection=lambda n:col)
    r=sr.minutes(store,'600001.SH','2026-09-22')
    assert r['status']=='missing' and r['bars']==[]
