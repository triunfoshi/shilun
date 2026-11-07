from shilun.review.board_ladder import build_ladder
from shilun.review.minute_capital_flow import parse_flow, aggregate_flow
from shilun.review.topic_perspectives import short_list, rank_perspectives
from shilun.review.theme_evidence import front_support


def stock(code, boards):
    return dict(ticker=code+'.SZ',code=code,name=code,boards=boards)


def test_ladder_keeps_failed_members_and_never_uses_capped_denominator():
    prior=[stock(str(n).zfill(6),1) for n in range(7)]
    current=[dict(r,boards=2,pct=10) for r in prior[:5]]
    pools={'2026-09-11':{'stocks':prior},'2026-09-14':{'stocks':current}}
    bars={('2026-09-14',r['ticker']):dict(close=9,pre_close=10) for r in prior[5:]}
    result=build_ladder(target='2026-09-14',previous_date='2026-09-11',next_date='2026-09-15',pools=pools,bars=bars,
        reference={'payload':{'window':{'board_caps':{'two_board':4}},'item':[]}})
    row=result['rows'][0]
    assert len(row['today'])==5 and len(row['yesterday'])==7
    assert row['next'] is None and row['promotion_rate']==5/7
    assert round(row['yesterday'][-1]['pct'],6)==-10
    assert row['observed']==7
    missing=build_ladder(target='2026-09-14',previous_date='2026-09-11',next_date=None,pools={},bars={})
    assert not missing['available']


def test_flow_cumulative_difference_and_missing_member_are_not_zero():
    payload={'data':{'code':'000001','klines':['2026-09-14 09:31,30,-10,-20,10,20','2026-09-14 09:32,40,-10,-30,15,25','2026-09-11 09:31,900,0,0,100,800']}}
    a=parse_flow(payload,'000001.SZ','2026-09-14')
    assert len(a)==2 and a[-1]['main']==40
    b=[dict(a[0],main=50),dict(a[1],main=70)]
    rows=aggregate_flow({'a':a,'b':b},['a','b','b'])
    assert [r['main_net_yuan'] for r in rows]==[80,110]
    assert rows[1]['minute_change_yuan']==30
    missing=aggregate_flow({'a':a,'b':b[:1]},['a','b'])
    assert missing[1]['main_net_yuan'] is None and missing[1]['coverage']==1
    assert not parse_flow(payload,'000002.SZ','2026-09-14')


def test_screen_concept_and_industry_separately():
    cat=[dict(ts_code='885001.TI',type='N',count=20),dict(ts_code='884001.TI',type='I',count=20),dict(ts_code='881001.TI',type='I',count=20),dict(ts_code='703001.TI',type='I',count=20)]
    daily=[[dict(ts_code=r['ts_code'],pct_change=n+1) for n,r in enumerate(cat)]]
    assert {r['ts_code'] for r in short_list(cat,daily,1)}=={'885001.TI','884001.TI'}


def test_cpo_pcb_are_separate_scopes_and_index_baskets_cannot_win():
    cat=[dict(ts_code=c,name=n,type=t,count=40) for c,n,t in [
        ('886033.TI','共封装光学(CPO)','N'),('885959.TI','PCB概念','N'),
        ('884092.TI','印制电路板','I'),('881270.TI','元件','I'),
        ('885001.TI','沪深300样本股','N'),('885002.TI','融资融券','N'),
        ('885003.TI','同花顺漂亮100','N'),('885004.TI','2026中报预增','N')]]
    daily=[[dict(ts_code=r['ts_code'],pct_change=i+1) for i,r in enumerate(cat)]]
    assert {r['name'] for r in short_list(cat,daily)}=={'共封装光学(CPO)','PCB概念','印制电路板'}


def test_actual_constituents_without_limit_up_are_visible_not_fabricated_boards():
    leader=dict(ticker='a',name='a',consecutive_count=4,theme_name='概念A')
    members={'a':{'con_name':'a'},'b':{'con_name':'未涨停成分'}}
    args=dict(current_rows=[],history_rows=[('2026-09-08',leader)],members=members)
    current={'a':dict(close=10,high=11,low=9),'b':dict(close=9,high=10,low=8)}
    result=front_support([leader],current,{'a':{'close':10},'b':{'close':10}},**args)
    rows={r['ticker']:r for r in result['rows']}
    assert rows['b']['front'] is False and rows['b']['boards']==0 and rows['b']['active_days']==0
    assert rows['b']['role_label']=='后排观察成分'
    assert 'first_active_date' not in rows['b']
    # Today's price cannot decide yesterday's role.
    current['b']['close']=20
    again=front_support([leader],current,{'a':{'close':10},'b':{'close':10}},**args)
    assert [r['front'] for r in again['rows']]==[r['front'] for r in result['rows']]


def test_trend_front_does_not_require_a_limit_up_or_use_future_prices():
    from datetime import date,timedelta
    leader=dict(ticker='a',name='高度股',consecutive_count=4,theme_name='A')
    members={'a':{'con_name':'高度股'},'b':{'con_name':'趋势股'}}
    first=date(2026,8,1)
    history={'a':[], 'b':[]}
    for i in range(22):
        stamp=(first+timedelta(days=i)).isoformat()
        history['a'].append(dict(date=stamp,close=10,pre_close=10))
        history['b'].append(dict(date=stamp,close=10*1.025**i))
    previous=(first+timedelta(days=21)).isoformat()
    kwargs=dict(history_rows=[(previous,leader)],members=members,price_history=history,previous_date=previous)
    result=front_support([leader],{}, {},**kwargs)
    row=next(r for r in result['rows'] if r['ticker']=='b')
    assert row['front'] and row['boards']==0 and row['active_days']==0
    assert row['price_evidence']['price_basis']=='raw_close_fallback'
    history['b'].append(dict(date='2026-09-14',close=.01))
    same=front_support([leader],{}, {},**kwargs)
    assert next(r for r in same['rows'] if r['ticker']=='b')['price_evidence']==row['price_evidence']
