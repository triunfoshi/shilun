from shilun.review.board_day_facts import build_day_facts

DAY, PREVIOUS = '2026-10-30', '2026-10-29'


def stock(ticker, height=1, **fields):
    return {'ticker':ticker, 'boards':height, 'name':ticker, 'break_count':0,
            'seal_time':'093000', **fields}


def pool(*stocks, **fields):
    return {'complete':True, 'source':'eastmoney_dated_pool', 'stocks':list(stocks), **fields}


def calculate(old, now, failed=(), **fields):
    return build_day_facts(day=DAY, previous=PREVIOUS, ups={PREVIOUS:pool(*old),DAY:pool(*now)},
                           broken={DAY:pool(*failed)}, **fields)


def test_frozen_cohort_keeps_failures_and_separates_promotion_levels():
    a,b,c = stock('000001.SZ'), stock('600001.SH',2), stock('300001.SZ',3)
    result=calculate([a,b,c],[stock('000001.SZ',2),stock('300001.SZ',4)],
        bars={'000001.SZ':{'close':11,'pre_close':10},
              '600001.SH':{'close':9,'pre_close':10},
              '300001.SZ':{'close':10.2,'pre_close':10}})
    m,p=result['money_effect'],result['promotion']
    assert m['sample_n']==m['total_n']==3
    assert abs(m['median_pct']-2)<1e-8 and abs(m['mean_pct']-2/3)<1e-8
    assert m['up_rate']==2/3 and m['repeat_limit_rate']==2/3
    assert [(r['base'],r['promoted'],r['rate']) for r in p['stages']]==[(1,1,1),(1,0,0),(1,1,1)]


def test_price_gap_suspension_and_reference_do_not_turn_into_zero_feedback():
    old=[stock('000001.SZ'),stock('600001.SH'),stock('300001.SZ')]
    result=calculate(old,[],bars={'000001.SZ':{'close':11},'600001.SH':{'close':9,'pre_close':10}},
                     suspended={'600001.SH'})['money_effect']
    assert result['median_pct'] is None and result['mean_pct'] is None
    assert result['sample_n']==0 and result['total_n']==3
    assert result['suspended_n']==1 and result['missing_n']==3
    assert {r['reason'] for r in result['exceptions']}=={'当日收盘行情缺失','当日前收参考价缺失','全天停牌'}


def test_same_day_ex_right_reference_has_priority_and_limit_reference_is_valid_fallback():
    a,b=stock('000001.SZ'),stock('600001.SH')
    result=calculate([a,b],[],bars={a['ticker']:{'close':5.5,'pre_close':5},b['ticker']:{'close':10}},
                     limits={b['ticker']:{'pre_close':10}})['money_effect']
    assert abs(result['median_pct']-5)<1e-8
    assert result['sample_n']==2


def test_never_broken_and_reclosed_are_complementary_but_seal_time_is_cross_cut():
    a=stock('000001.SZ',2,break_count=0,seal_time='093500')
    b=stock('600001.SH',1,break_count=3,seal_time='143000')
    c=stock('300001.SZ',1,break_count=1,seal_time='100000')
    # b also appearing in the broken pool is a reclose, not an unsealed failure.
    failed=stock('600002.SH')
    q=calculate([], [a,b,c], [b,failed])['seal_quality']
    assert q['never_broken']==1 and q['reopened']==2 and q['total']==3
    assert q['never_broken']+q['reopened']==q['total']
    assert q['opening_seconds']==1 and q['late_seal']==1
    assert q['broken_pool']==1 and q['broken_rate']==1/4 and q['avg_broken_times']==4/3


def test_unknown_break_counts_or_times_have_explicit_coverage_not_inferred_zero():
    q=calculate([], [stock('000001.SZ'),stock('600001.SH',break_count=None,seal_time=None)])['seal_quality']
    assert q['never_broken'] is None and q['reopened'] is None and q['avg_broken_times'] is None
    assert q['known_never_broken']==1 and q['break_count_valid_n']==1
    assert q['opening_seconds'] is None and q['late_seal'] is None and q['seal_time_valid_n']==1
    assert q['status']=='partial'


def test_scope_excludes_st_beijing_and_live_or_missing_pool_never_claims_completed_day():
    a=stock('000001.SZ'); st=stock('600001.SH',name='ST样本'); bj=stock('920001.BJ')
    fact=calculate([a,st,bj],[a,st,bj])
    assert fact['promotion']['overall']['base']==1 and fact['seal_quality']['total']==1
    fact=build_day_facts(day=DAY,previous=PREVIOUS,ups={PREVIOUS:pool(a),DAY:pool(a,snapshot_mode='live')},broken={})
    assert fact['promotion']['overall']['rate'] is None
    assert fact['seal_quality']['total'] is None
    assert fact['money_effect']['repeat_limit_rate'] is None
