import pandas as pd
import pytest
from shilun.market.sector import evaluate_sector_trends, _prepare_stock_frame, _build_sector_history
from shilun.market.sector_membership import prepare_memberships
from shilun.market.taxonomy import VERSION
from test_market_sector import _market_rows, _benchmark_bars, _moneyflow_rows


def snapshot():
    _, basic, _ = _market_rows()
    hot = basic[basic.ts_code.str.startswith('300')].ts_code.tolist()
    cold = basic[basic.ts_code.str.startswith('601')].ts_code.tolist()
    def row(code, name, kind, tickers, **kw):
        return dict(code=code, name=name, type=kind, kind='concept' if kind=='N' else 'industry',
                    members=[{'con_code':t} for t in tickers], source_label='东方财富概念' if kind=='N' else '东方财富三级行业',
                    membership_date='2026-03-25', selected=False, granularity_label='概念题材' if kind=='N' else '三级行业',
                    **kw)
    return {'date':'2026-03-25','version':VERSION,'status':'ready','rows':[
        row('BK1128.DC','CPO','N',hot,research_eligible=True),
        row('BK0877.DC','印制电路板','N',cold,research_eligible=True),
        row('BK1340.DC','印制电路板','I',hot,industry_level='l3',research_eligible=True),
        row('BK1200.DC','电子','I',hot,industry_level='l1',research_eligible=False),
        row('BK0001.DC','沪深300成份股','N',hot,research_eligible=False),
    ]}


def test_uses_same_fine_grain_and_keeps_homonyms_separate():
    m=prepare_memberships(snapshot(),'2026-03-25')
    assert len(m)==3
    assert len({r['sector_code'] for r in m.values()})==3
    pcb=[r for r in m.values() if r['sector_base_name']=='印制电路板']
    assert len(pcb)==2 and set(pcb[0]['member_tickers']).isdisjoint(pcb[1]['member_tickers'])
    assert 'BK1128.DC' in m  # No P2 selected/three-board qualification is applied to R2.


def test_empty_or_wrong_date_never_falls_back_to_industry():
    for x in ({}, {**snapshot(),'date':'2026-03-26'}, {**snapshot(),'rows':[]}):
        with pytest.raises(ValueError): prepare_memberships(x,'2026-03-25')


def test_r2_uses_members_even_without_stock_basic_industry():
    bars,basic,daily=_market_rows()
    result=evaluate_sector_trends(analysis_date='2026-03-25',market_bars=bars,stock_basic=basic.drop(columns='industry'),daily_basic=daily,benchmark_bars=_benchmark_bars(),sector_memberships=snapshot(),top_n=3,include_daily_leaders=False)
    assert result['membership_scope_count']==3
    assert result['sector_source'] == '东方财富'
    assert len(result['all_sectors'])==3
    for row in result['top_sectors']:
        members=set(row['member_tickers'])
        assert all(r['ticker'] in members for r in row['candidate_universe'])
        assert row['metrics']['stock_count']==6
    assert all(r['sector_code'] for r in result['trend_sectors'])
    assert not result['daily_leaders']


def test_overlap_does_not_inflate_market_denominator_or_member_counts():
    bars,basic,daily=_market_rows()
    frame=_prepare_stock_frame(bars,stock_basic=basic,daily_basic=daily,moneyflow=None,exclude_st=False)
    hot=set(basic[basic.ts_code.str.startswith('300')].ts_code)
    m={'A':hot,'B':hot}
    hist=_build_sector_history(frame,min_stock_count=3,memberships=m)
    day=frame.date.max();rows=hist[hist.date==day]
    expected=frame[(frame.date==day)&frame.ticker.isin(hot)].amount.sum()/frame[frame.date==day].amount.sum()
    assert len(rows)==2 and set(rows.stock_count)=={6}
    assert all(abs(x-expected)<1e-12 for x in rows.market_share)
    assert abs(rows.iloc[0].return_1d-rows.iloc[1].return_1d)<1e-12


def test_day_below_eighty_percent_coverage_not_aggregated():
    bars,basic,daily=_market_rows()
    frame=_prepare_stock_frame(bars,stock_basic=basic,daily_basic=daily,moneyflow=None,exclude_st=False)
    members=set(basic.ts_code)
    frame=frame[~((frame.date==frame.date.max())&frame.ticker.isin(list(members)[:3]))]
    hist=_build_sector_history(frame,min_stock_count=3,memberships={'A':members})
    assert hist.date.max()<bars.date.max()


def test_api_reuses_one_dated_membership_snapshot_for_both_ranking_passes():
    from unittest.mock import patch
    from shilun.api import _compute_sector_trends_full
    facts={'benchmark_records':[], 'market_records':[], 'stock_basic':pd.DataFrame(),
           'daily_basic':pd.DataFrame(), 'moneyflow':pd.DataFrame(), 'gate':{}, 'breakout_events_lookup':{}}
    archive=snapshot()
    with patch('shilun.market.taxonomy.load_taxonomy',return_value=archive) as read, \
         patch('shilun.market.sector_service._load_minute_bars_from_mongo',return_value=({},None)), \
         patch('shilun.market.sector_service.evaluate_sector_trends',return_value={}) as calculate:
        for strict in (False, True):
            _compute_sector_trends_full(object(),'2026-03-25','000001.SH',40,60,8,
                                        facts_cache=facts,require_minute_data_for_ranking=strict)
    assert read.call_count==1
    assert all(c.kwargs['sector_memberships'] is archive for c in calculate.call_args_list)


@pytest.mark.parametrize('cache_state', ['present', 'absent', 'timeout'])
def test_sector_cache_read_distinguishes_missing_data_from_cloud_timeout(cache_state):
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    from fastapi import HTTPException
    from pymongo.errors import NetworkTimeout
    from shilun.api import market_sectors

    store = MagicMock()
    find = store.collection.return_value.find_one
    find.return_value = {'payload': {'membership_version': VERSION}} if cache_state == 'present' else None
    if cache_state == 'timeout':
        find.side_effect = NetworkTimeout('test read timeout')
    with patch('shilun.api.load_config', return_value=SimpleNamespace(mongo_uri='mongodb://example', mongo_db='test')), \
         patch('shilun.common.db.MongoSnapshotStore', return_value=store) as factory:
        if cache_state == 'present':
            assert market_sectors(date='2026-03-25')['_from_cache'] is True
        else:
            with pytest.raises(HTTPException) as caught:
                market_sectors(date='2026-03-25')
            assert caught.value.status_code == (503 if cache_state == 'timeout' else 400)
            if cache_state == 'timeout':
                assert '无需重新计算' in caught.value.detail
        factory.assert_called_once_with('mongodb://example', 'test', socket_timeout_ms=30000, initialize_indexes=False)
    store.close.assert_called_once()
