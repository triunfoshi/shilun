from copy import deepcopy

from shilun.review.sentiment_context import cycle_position, industry_structure


DAYS=['2026-10-28','2026-10-29','2026-10-30']


def stock(n=1,**fields):
    return {'ticker':f'{n:06d}.SZ','name':f'样本{n}','boards':1,'industry':'电子','seal_time':'093000',**fields}


def pool(day,rows,**fields):
    return {'date':day,'complete':True,'snapshot_mode':'settled','source':'eastmoney_dated_pool',
            'metric_scope':'沪深非ST','stocks':rows,**fields}


def sample():
    ups={day:pool(day,[stock(n,boards=i+1) for n in range(1,i+2)]) for i,day in enumerate(DAYS)}
    broken={day:pool(day,[stock(8)] if i<2 else []) for i,day in enumerate(DAYS)}
    return ups,broken


def test_cycle_matches_three_normalized_factors_and_has_no_future_leak():
    up,br=sample()
    # A later extreme must not change the selected-date scaling or window start.
    up['2026-11-02']=pool('2026-11-02',[stock(n,boards=100) for n in range(1,11)])
    cy=cycle_position(DAYS[-1],DAYS+['2026-11-02'],up,br)
    assert cy['available'] and cy['current_date']==DAYS[-1]
    assert cy['trough_date']==DAYS[0] and cy['day_n']==3
    assert cy['current_score']==1 and cy['series'][0]['score']==0
    assert cy['trend']=='连续两日走强'
    assert cy['range_position']==1 and len(cy['series'])==3


def test_equal_factors_have_no_invented_differences_or_percentile():
    up={day:pool(day,[stock()]) for day in DAYS}
    br={day:pool(day,[]) for day in DAYS}
    cy=cycle_position(DAYS[-1],DAYS,up,br)
    assert cy['current_score']==.5 and cy['trend']=='基本走平'
    assert cy['tie_count']==3 and cy['range_position'] is None


def test_missing_middle_day_or_height_cannot_claim_a_complete_cycle():
    up,br=sample()
    del br[DAYS[1]]
    cy=cycle_position(DAYS[-1],DAYS,up,br)
    assert not cy['available'] and len(cy['missing'])==1
    assert all(row['score'] is None for row in cy['series'])
    up,br=sample()
    up[DAYS[-1]]['stocks'][0]['boards']=None
    cy=cycle_position(DAYS[-1],DAYS,up,br)
    assert not cy['available'] and cy['series'][-1]['highest_consec'] is None


def test_missing_target_pool_and_intraday_scope_are_not_substituted():
    up,br=sample()
    for field,value in [('snapshot_mode','live'),('metric_scope','东财完整池'),('date',DAYS[0])]:
        changed=deepcopy(up);changed[DAYS[-1]][field]=value
        cy=cycle_position(DAYS[-1],DAYS,changed,br)
        assert not cy['available'] and cy['series'][-1]['limit_up'] is None
    cy=cycle_position('2026-11-02',DAYS,up,br)
    assert not cy['available'] and '其他日期' in cy['reason']


def test_industry_grouping_uses_pool_industry_and_first_seal_slots():
    day=DAYS[-1]
    rows=[stock(1,industry='芯片',seal_time='092500',boards=2),stock(2,industry='芯片',seal_time='093500'),
          stock(3,industry='零售',seal_time='100000'),stock(4,industry='零售',seal_time='140001'),
          stock(5,industry='',seal_time=None)]
    # A stock reclosed at the end is not counted as an unsealed failure.
    up={day:pool(day,rows)};br={day:pool(day,[rows[0],stock(6,industry='芯片')])}
    result=industry_structure(day,up,br)
    groups={row['sector']:row for row in result['themes']}
    assert groups['芯片']['limit_up']==2 and groups['芯片']['broken']==1
    assert groups['芯片']['highest']==2 and groups['芯片']['first_boards']==1
    assert groups['芯片']['first_seal']=='092500' and groups['芯片']['last_seal']=='093500'
    assert [row['count'] for row in result['timeline']]==[2,1,0,0,1]
    assert result['time_valid_n']==4 and result['missing_time_n']==1
    assert result['industry_valid_n']==4 and result['total_limit_up']==5
    assert groups['行业缺失']['limit_up']==1


def test_industry_missing_broken_time_and_height_stay_unknown():
    day=DAYS[-1];up={day:pool(day,[stock(boards=None,seal_time='126000')])}
    result=industry_structure(day,up,{})
    assert not result['broken_available']
    assert result['themes'][0]['broken'] is None and result['themes'][0]['highest'] is None
    assert result['time_valid_n']==0 and result['themes'][0]['unknown_height_n']==1
    assert sum(row['count'] for row in result['timeline'])==0


def test_verified_empty_pool_is_zero_industry_but_cycle_rate_is_undefined():
    day=DAYS[-1];up={day:pool(day,[])};br={day:pool(day,[])}
    result=industry_structure(day,up,br)
    assert result['available'] and result['total_limit_up']==0 and result['themes']==[]
    cy=cycle_position(day,DAYS,up,br)
    assert not cy['available'] and cy['series'][-1]['broken_rate'] is None


def test_duplicate_pool_rows_are_not_counted_twice():
    day=DAYS[-1];up={day:pool(day,[stock(),stock()])}
    assert not industry_structure(day,up,{})['available']
