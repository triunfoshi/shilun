"""Selected-date, coverage and Vibe parity checks, without provider requests."""
from copy import deepcopy
import json
from unittest.mock import MagicMock

import pytest

from shilun.review.vibe_shortline import (
    READINGS, build_history, clean_reason, day_tags, history_context, history_day,
    history_diff, rotation_status, tags_of, theme_evidence, theme_rotation, theme_tree,
)

DAYS = ['2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-11-02']


def stock(n, **fields):
    return {'code': f'{n:06d}', 'ticker': f'{n:06d}.SZ', 'name': f'股票{n}', 'boards': 1,
            'seal_time': '093000', 'last_seal_time': '145500', 'break_count': 0, **fields}


def pool(day, rows, **fields):
    return {'date': day, 'stocks': rows, 'complete': True, 'metric_scope': '沪深非ST',
            'source': 'eastmoney_dated_pool', 'snapshot_mode': 'settled', **fields}


def reason(day, mapping):
    return {'date': day, 'reasons': {f'{n:06d}': value for n, value in mapping.items()},
            'source_note': f'同花顺涨停揭秘 {day}'}


def test_exact_vibe_cleaning_whole_tags_and_generic_attributes():
    assert clean_reason(' AI应用，半导体,深圳国资+国企改革+超额第五项 ') == 'AI应用+半导体+深圳国资+国企改革'
    assert tags_of(clean_reason('AI应用+半导体+深圳国资+国企改革')) == ['AI应用', '半导体']
    assert tags_of('破净+低价股+小盘股+微盘股+次新股+央企改革+通信板块') == []
    assert clean_reason('A'*30+'+'+'B'*10) == 'A'*30
    assert clean_reason('A'*50+'+芯片') == 'A'*40


def test_tree_uses_event_tags_many_to_many_and_previous_frozen_cohort():
    previous, today = DAYS[-2:]
    old = [stock(1, boards=2), stock(2), stock(3)]
    current = [stock(1, boards=3), stock(4), stock(5), stock(6)]
    ups = {previous: pool(previous, old), today: pool(today, current)}
    reasons = {previous: reason(previous, {1:'CPO+光模块', 2:'CPO', 3:'白酒'}),
               today: reason(today, {1:'CPO+光模块', 4:'CPO', 5:'CPO', 6:'CPO'})}
    result = theme_tree(today, previous, ups, {today:pool(today,[stock(2)])},
                        {today:pool(today,[stock(3)])}, reasons)
    groups = {g['tag']:g for g in result['themes']}
    assert result['covered'] == result['total_limit_up'] == 4
    assert result['prev_covered'] == 3
    assert groups['CPO']['state'] == '扩散中'
    assert groups['CPO']['first_boards'] == 3 and groups['CPO']['consec_boards'] == 1
    assert groups['CPO']['prev_members'] == 2 and groups['CPO']['prev_still_up'] == 1
    assert groups['CPO']['continuation_rate'] == .5 and groups['CPO']['broken'] == 1
    assert groups['白酒']['limit_up'] == 0 and groups['白酒']['limit_down'] == 1
    assert groups['白酒']['state'] == '昨日题材未再涨停'
    assert groups['光模块']['limit_up'] == 1
    assert result['concentration'] == 1


def test_tree_missing_reason_must_not_fallback_to_industry_or_wrong_date():
    day = DAYS[-1]
    ups = {day:pool(day,[stock(1,industry='半导体')])}
    reasons = {day:reason(DAYS[-2],{1:'CPO'})}
    result = theme_tree(day, DAYS[-2], ups, {}, {}, reasons)
    assert not result['available'] and '行业分类' in result['reason']
    result = theme_tree(day,DAYS[-2],ups,{},{},{day:reason(day,{1:'CPO'})})
    assert result['themes'][0]['broken'] is None and result['themes'][0]['limit_down'] is None
    assert result['themes'][0]['state'] == '无昨日题材数据'


def test_tree_does_not_invent_unknown_height_and_reclosed_not_broken():
    day = DAYS[-1]
    row = stock(1,boards=None,seal_time='000000')
    ups = {day:pool(day,[row])}
    result=theme_tree(day,None,ups,{day:pool(day,[row])},{day:pool(day,[])},{day:reason(day,{1:'CPO'})})
    group=result['themes'][0]
    assert group['highest'] is None and group['members'][0]['boards'] is None
    assert group['first_seal'] is None and group['broken'] == 0


def test_rotation_rank_ties_zeros_unknowns_and_faded_follow_source():
    ups = {day:pool(day,[stock(1),stock(2)]) for day in DAYS}
    reasons = {day:reason(day,{1:'CPO',2:'PCB'}) for day in DAYS}
    reasons[DAYS[-2]]=reason(DAYS[-2],{1:'PCB',2:'PCB'})
    reasons[DAYS[-1]]=reason(DAYS[-1],{1:'CPO',2:'CPO'})
    result=theme_rotation(DAYS[-1],DAYS,ups,reasons)
    groups={g['tag']:g for g in result['themes']}
    assert result['available'] and groups['CPO']['ranks'][0] == groups['PCB']['ranks'][0] == 1
    assert groups['CPO']['series'] == [1,1,1,0,2]
    assert groups['CPO']['status'] == '隔 1 天回流'
    assert groups['PCB']['today'] == 0 and groups['PCB']['series'][-1] == 0
    assert groups['PCB']['active_days'] == 4
    reasons[DAYS[-1]]=reason(DAYS[-1],{1:'CPO'})
    result=theme_rotation(DAYS[-1],DAYS,ups,reasons)
    assert result['partial_days'] == [DAYS[-1]]
    assert all(g['tag']!='PCB' for g in result['themes'])  # unknown absence must not become faded


def test_rotation_middle_missing_instead_of_zero_and_no_future_leak():
    ups={day:pool(day,[stock(1)]) for day in DAYS}
    reasons={day:reason(day,{1:'CPO'}) for day in DAYS}
    del reasons[DAYS[1]]
    ups['2026-11-03']=pool('2026-11-03',[stock(1)])
    reasons['2026-11-03']=reason('2026-11-03',{1:'未来事件'})
    result=theme_rotation(DAYS[-1],DAYS+['2026-11-03'],ups,reasons)
    assert result['dates'] == DAYS and result['themes'][0]['series'] == [1,None,1,1,1]
    assert result['themes'][0]['status'] == '连续 3 天以上'
    assert result['missing_days'] == [DAYS[1]]
    assert result['tag_count'] == 1


def test_rotation_header_counts_raw_beijing_pool_without_changing_event_tags_or_missing():
    from shilun.review.board_dynamics import comparable_pools
    day = DAYS[1]  # The real mismatch was October 28, inside a five-day window.
    raw = {day:pool(day,[stock(n) for n in range(1,71)]) for day in DAYS}
    raw[day]['stocks'] += [stock(920896,ticker='920896.BJ',name='旺成科技'),
                           stock(920220,ticker='920220.BJ',name='朗信电气')]
    # Reproduce the 633-tag count across the window without requiring a database.
    reasons = {day:reason(day,{n:f'题材{(i*140+(n-1)*2)%633}+题材{(i*140+(n-1)*2+1)%633}'
                              for n in range(1,71)}) for i,day in enumerate(DAYS)}
    del reasons[day]['reasons']['000070']
    reasons[DAYS[-1]]['reasons']['000070'] += '+题材278+题材279'
    comparable = comparable_pools(raw)
    baseline = theme_rotation(DAYS[-1],DAYS,comparable,reasons)
    result = theme_rotation(DAYS[-1],DAYS,comparable,reasons,raw_pools=raw)
    header = result['days'][1]
    assert header['limit_up'] == header['raw_limit_up'] == 72
    assert header['scoped_limit_up'] == 70 and header['excluded_n'] == 2
    assert header['unknown'] == 1 and header['covered'] == 69
    assert '北交所' in header['scope_note']
    assert result['themes'] == baseline['themes']
    assert result['tag_count'] == baseline['tag_count'] == 633


@pytest.mark.parametrize('series,label',[
    ([0,0,0,0,1],'窗口内首次出现'),
    ([1,0,None,0,1],'回流（中间有缺数，隔几天说不准）'),
    ([0,0,None,0,1],'窗口内前几天有缺数，不确定是不是新出现'),
    ([1,1,1,None,1],'昨天缺数，新旧不确定'),
    ([1,1,1,1,1],'连续 5 天以上'),
])
def test_rotation_status_parity(series,label):
    assert rotation_status(series)[0] == label


def test_rotation_top12_and_faded_max6_are_discovery_not_three_direction_cap():
    yesterday,today=DAYS[-2:]
    old=[stock(n) for n in range(1,17)]
    current=[stock(n) for n in range(30,45)]
    ups={yesterday:pool(yesterday,old),today:pool(today,current)}
    reasons={yesterday:reason(yesterday,{n:f'旧题材{(n-1)//2}' for n in range(1,17)}),
             today:reason(today,{n:f'新题材{n}' for n in range(30,45)})}
    result=theme_rotation(today,[yesterday,today],ups,reasons)
    assert len([g for g in result['themes'] if g['today']]) == 12
    assert len([g for g in result['themes'] if not g['today']]) == 6


def test_evidence_and_tree_share_exact_labels_and_source_date():
    day=DAYS[-1]
    ups={day:pool(day,[stock(1),stock(2)])}
    reasons={day:reason(day,{1:'CPO+光模块+国企改革'})}
    evidence=theme_evidence(day,ups,reasons)
    assert [row['tag'] for row in evidence['rows']] == ['CPO','光模块']
    assert evidence['covered'] == 1 and evidence['missing_codes'] == ['000002']
    assert all(day in row['reason_source'] for row in evidence['rows'])


def test_history_outcomes_use_yesterdays_whole_cohort_and_deep_loss_not_market():
    previous,today=DAYS[-2:]
    ups={previous:pool(previous,[stock(1),stock(2,boards=2),stock(3)]),today:pool(today,[stock(1,boards=2)])}
    broken={today:pool(today,[stock(4)])}
    bars={stock(n)['ticker']:{'close':close,'pre_close':10} for n,close in [(1,11),(2,9.5),(3,9),(99,1)]}
    result=history_day(today,previous,ups,broken,bars=bars)
    assert result['values'] == {'limit_up':1,'highest_board':2,'broken_rate':.5,
        'money_effect':-5,'deep_loss':2,'promotion_1to2':.5}
    assert result['coverage']['expected'] == result['coverage']['sample'] == 3


def test_history_suspension_missing_returns_are_not_zero_and_unknown_height_not_first():
    previous,today=DAYS[-2:]
    ups={previous:pool(previous,[stock(1),stock(2,boards=None)]),today:pool(today,[stock(1)])}
    result=history_day(today,previous,ups,{today:pool(today,[])},
        previous_rows=[{'ticker':stock(1)['ticker'],'return_1d':.1}],suspended={stock(2)['ticker']})
    assert result['values']['money_effect'] == 10
    assert result['values']['promotion_1to2'] is None
    assert result['status'] == 'partial' and result['coverage']['missing'] == 1
    assert result['values']['deep_loss'] == 0


def readings(n):
    return {'limit_up':n,'highest_board':n,'broken_rate':n/100,'money_effect':n,
            'deep_loss':n,'promotion_1to2':n/100}


def test_percentiles_exclude_today_and_need_ten_valid_samples_per_metric():
    rows=[{'date':str(i),'values':readings(i)} for i in range(11)]
    result=history_context('10',rows)
    values={item['key']:item for item in result['items']}
    assert values['limit_up']['percentile'] == 1 and values['limit_up']['hist_max'] == 9
    assert values['broken_rate']['extreme'] == '偏冷'
    assert values['promotion_1to2']['extreme'] == '偏热'
    rows[0]['values']['money_effect']=None
    result=history_context('10',rows)
    assert next(item for item in result['items'] if item['key']=='money_effect')['percentile'] is None
    assert history_context('10',rows[:-1])['available'] is False


def test_diff_exact_noise_threshold_and_reverse_direction():
    before=readings(0);after=readings(0)
    before['broken_rate']=.427;after['broken_rate']=.507
    result=history_diff('today',[{'date':'prior','values':before},{'date':'today','values':after}])
    assert not result['changes']
    after['broken_rate']=.52;after['limit_up']=20
    result=history_diff('today',[{'date':'prior','values':before},{'date':'today','values':after}])
    changes={row['key']:row for row in result['changes']}
    assert changes['broken_rate']['hotter'] is False and changes['limit_up']['hotter'] is True
    assert result['prev_date'] == 'prior'


def test_build_history_missing_target_keeps_date_and_no_future_rows():
    result=build_history(DAYS[-1],DAYS+['2026-11-03'],{}, {},days=10)
    assert result['date'] == DAYS[-1] and result['series'][-1]['date'] == DAYS[-1]
    assert len(result['series']) == 5 and not result['context']['available']
    assert all(row['status']=='missing' and all(v is None for v in row['values'].values()) for row in result['series'])
    json.dumps(result,allow_nan=False)


def test_unsettled_or_mismatched_pool_is_not_counted():
    day=DAYS[-1];reasons={day:reason(day,{1:'CPO'})}
    for fields in [{'date':DAYS[0]},{'snapshot_mode':'live'},{'complete':False}]:
        ups={day:pool(day,[stock(1)],**fields)}
        assert not day_tags(day,ups,reasons)['available']


def test_invalid_history_window_rejected():
    with pytest.raises(ValueError):
        build_history(DAYS[-1],DAYS,{}, {},days=15)


def test_normalized_pool_preserves_discontinuous_board_stat_and_exact_day_restore():
    from shilun.review.board_data import BoardResearchData, normalize_pool
    row={'ticker':'000001.SZ','consecutive_count':1,'limit_up_stat':'8/6'}
    assert normalize_pool([row])[0]['zt_stat'] == '8/6'
    assert normalize_pool([{'c':'000001','lbc':1,'zttj':{'days':8,'ct':6}}],eastmoney=True)[0]['zt_stat'] == {'days':8,'ct':6}
    day=DAYS[-1]
    store=MagicMock()
    docs=[pool(day,[stock(1)])]
    raw=[{**row,'trade_date':day}]
    store.collection.side_effect=lambda name: MagicMock(**{'find.return_value': docs if name=='review_board_pools' else raw})
    result=BoardResearchData(store).pools([day],settled=True)
    assert result[day]['stocks'][0]['zt_stat'] == '8/6'
    from shilun.review.vibe_shortline import board_label
    assert board_label(1,'8/6') == board_label(1,{'days':8,'ct':6}) == '8天6板'


def test_duplicate_feedback_fails_instead_of_changing_stock_weights():
    previous,today=DAYS[-2:]
    ups={previous:pool(previous,[stock(1)]),today:pool(today,[stock(1)])}
    rows=[{'ticker':stock(1)['ticker'],'return_1d':.1}]*2
    with pytest.raises(ValueError,match='重复'):
        history_day(today,previous,ups,{},previous_rows=rows)


def test_api_cache_is_bounded_date_specific_refreshable_and_mutation_safe(monkeypatch):
    from datetime import date
    from shilun.api import shortline_views as api
    api._cache.clear()
    calls=[]
    def compute(builder,day,**kwargs):
        calls.append((day,kwargs))
        return {'date':day.isoformat(),'rows':[len(calls)]}
    monkeypatch.setattr(api,'_read_store',compute)
    day=date.fromisoformat(DAYS[-1])
    first=api._read(build_history,day,days=30)
    first['rows'].clear()
    second=api._read(build_history,day,days=30)
    assert second['rows'] == [1] and len(calls) == 1
    api._read(build_history,day,days=20)
    api._read(build_history,day,days=30,refresh=True)
    api._read(build_history,date.fromisoformat(DAYS[-2]),days=30)
    assert len(calls) == 4
    api._cache.clear()


def test_api_validation_does_not_touch_database(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from shilun.api import shortline_views as api
    app=FastAPI();app.include_router(api.router)
    def blocked(*args,**kwargs):
        raise AssertionError('invalid query must not open store')
    monkeypatch.setattr(api,'_read_store',blocked)
    client=TestClient(app)
    assert client.get('/shortline/history?date=2026-11-02&days=15').status_code == 422
    assert client.get('/shortline/themes?date=2026-02-30').status_code == 422
    assert client.get('/shortline/history?days=30').status_code == 422
