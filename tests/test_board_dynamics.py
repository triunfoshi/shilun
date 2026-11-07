from shilun.review.board_dynamics import build_dynamics, market_group


def stock(ticker='000001.SZ', boards=2, name='样本'):
    return dict(ticker=ticker, code=ticker.split('.')[0], boards=boards, name=name)


def pool(*rows, **extra):
    return dict(complete=True, stocks=list(rows), source='eastmoney_dated_pool', **extra)


def calculate(dates, ups, broken=None, prices=None, **kw):
    return build_dynamics(target=dates[-1], dates=dates, ups=ups, broken=broken or {}, prices=prices or set(), **kw)


def test_promotion_denominator_includes_first_boards_and_reclosed_not_broken():
    a,b,c=stock(),stock('300001.SZ',1),stock('600001.SH',1)
    result=calculate(['2026-09-14','2026-09-15'],{'2026-09-14':pool(a,b),'2026-09-15':pool(a,c)},
                     {'2026-09-15':pool(a,b)})['daily'][0]
    assert result['promotion_rate']==0.5
    assert result['broken']==1 and result['attempted']==3
    assert result['broken_rate']==1/3


def test_missing_empty_and_changed_scope_are_not_zero():
    dates=['2026-09-14','2026-09-15']
    for ups in ({}, {d:pool() for d in dates}):
        row=calculate(dates,ups)['daily'][0]
        assert row['promotion_rate'] is None and row['broken_rate'] is None
    ups={dates[0]:pool(stock()),dates[1]:pool(stock())}
    ups[dates[1]]['source']='tushare_limit_list_d'
    assert calculate(dates,ups)['daily'][0]['promotion_rate'] == 1
    ups[dates[1]]['source'] = 'unknown_provider'
    assert calculate(dates,ups)['daily'][0]['promotion_rate'] is None


def test_training_excludes_future_live_missing_and_suspension():
    dates=[f'2026-09-{i:02}' for i in range(1,10)]
    a=stock(); ups={d:pool(a) for d in dates}
    ups[dates[3]]=pool(a,snapshot_mode='live')
    result=build_dynamics(target=dates[7],dates=dates,ups=ups,broken={},prices=set(),suspended={a['ticker']:{dates[5]}})
    stat=result['estimates'][a['ticker']]
    assert stat['sample']==4 and stat['missing']==1 and stat['suspended']==1
    assert stat['probability'] is None
    assert result['window_end']==dates[7]


def test_grouped_estimate_and_unobserved_failure():
    dates=[f'2026-09-{i:02}' for i in range(1,10)]
    a=stock(); other=stock('300001.SZ')
    ups={d:pool(a,other) for d in dates}
    # One genuine failure, one missing daily quote; neither conflated with each other.
    ups[dates[3]]=pool(other)
    ups[dates[6]]=pool(other)
    result=calculate(dates,ups,prices={(dates[3],a['ticker'])})
    stat=result['estimates'][a['ticker']]
    assert stat['success']==4 and stat['sample']==5 and stat['missing']==1
    assert stat['probability']==5/7 and stat['status']=='low_sample'
    assert stat['interval'][0]<4/5<stat['interval'][1]
    assert result['estimates'][other['ticker']]['sample']==8


def test_market_groups_are_distinct():
    assert market_group(stock(name='*ST样本'))=='主板ST'
    assert market_group(stock('688001.SH'))=='科创板'
    assert market_group(stock('300001.SZ'))=='创业板'
    assert market_group(stock('920001.BJ'))=='北交所'


def test_common_market_probability_can_cross_scope_but_st_cannot():
    dates=[f'2026-09-{i:02}' for i in range(1,9)]
    a,b=stock(),stock('600001.SH',name='ST样本')
    ups={d:pool(a,b) for d in dates}
    ups[dates[3]]=pool(a)
    ups[dates[3]]['source']='tushare_limit_list_d'
    result=calculate(dates,ups)
    assert result['estimates'][a['ticker']]['sample']==7
    assert result['estimates'][b['ticker']]['missing']==1
    assert result['daily'][2]['promotion_rate'] == 1
    assert result['daily'][2]['previous_up'] == 1
    assert result['daily'][2]['scope'] == '沪深非ST'


def test_same_day_live_pool_does_not_publish_forecasts():
    dates=['2026-09-14','2026-09-15']
    result=calculate(dates,{dates[0]:pool(stock()),dates[1]:pool(stock(),snapshot_mode='live')})
    assert result['estimates']=={}
    assert result['daily'][0]['promotion_rate'] is None
