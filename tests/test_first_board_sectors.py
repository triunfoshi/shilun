from copy import deepcopy
import pytest
from shilun.decision_research.first_board_sectors import quantiles, feedback_stats, coordination, build_sector_feedback
from shilun.decision_research.engine import Evidence
from shilun.decision_research.playbook_assessment import daily_evidence, evaluate
from test_playbook_assessment import fixture_data, series


def test_quartiles_linear_interpolation_missing_and_real_zero():
    assert quantiles([0,10,20,30,None]) == {'p25':7.5,'p50':15.,'p75':22.5,'n':4}
    assert quantiles([0,0,0])['p50'] == 0
    assert quantiles([None,None,None])['p50'] is None
    assert quantiles([1,2])['p25'] is None
    rows=[{'ticker':'a','feedback_date':'2026-09-21','opening':0},
          {'ticker':'b','feedback_date':'2026-09-21','opening':10},
          {'ticker':'c','feedback_date':'2026-09-21','opening':20}]
    result=feedback_stats(rows+rows)
    assert result['n']==3 and result['total']==3
    assert result['p25']==5 and result['p50']==10 and result['p75']==15
    assert result['positive_pct']==pytest.approx(200/3)
    result=feedback_stats(rows+[{'ticker':'d','feedback_date':'2026-09-21','opening':None}])
    assert result['n']==3 and result['total']==4 and result['status']=='partial'


def test_same_day_capital_units_missing_and_direct_priority():
    d=fixture_data();t='600000.SH';e=Evidence(d);first=e.days[12]
    e.db[t,first].update(float_share=123,circ_mv=234)
    f=next(x for x in daily_evidence(e,{'snapshots':{}},'lt50')['trend'] if x['date']==e.days[13])['members'][0]['first_board_evidence']
    assert f['float_cap_cny']==2340000
    assert f['amount_cny']==100000000
    del e.db[t,first]['circ_mv']
    f=next(x for x in daily_evidence(e,{'snapshots':{}},'lt50')['trend'] if x['date']==e.days[13])['members'][0]['first_board_evidence']
    assert f['float_cap_cny']==123*10000*e.row(t,first)['close']
    e.db[t,first].pop('float_share')
    e.db[t,e.date]['circ_mv']=999999
    f=next(x for x in daily_evidence(e,{'snapshots':{}},'lt50')['trend'] if x['date']==e.days[13])['members'][0]['first_board_evidence']
    assert f['float_cap_cny'] is None


def test_sector_overlap_dedup_scope_and_formation_day_only():
    d=fixture_data();e=Evidence(d)
    ticks=list(e.info)
    group={'code':'x','name':'示例概念','kind':'concept','research_eligible':True,'membership_date':e.date,
           'member_count':5,'members':[{'con_code':t} for t in ticks]}
    d['taxonomy']={'date':e.date,'rows':[group,{**deepcopy(group),'code':'y','name':'第二概念'},
        {**deepcopy(group),'code':'skip','name':'昨日涨停'},
        {**deepcopy(group),'code':'incomplete','member_count':6}]}
    daily=daily_evidence(e,{'snapshots':{}},'lt50')['trend']
    daily=[x for x in daily if x['date']==e.days[13]]
    result=build_sector_feedback(e,d,daily)
    assert len(result['groups'])==2
    assert result['windows']['5']['total']==5
    assert all(x['windows']['5']['total']==5 for x in result['groups'])
    g=result['groups'][0]['daily'][e.days[13]]
    assert g['coordination']['date']==e.days[12]
    assert g['coordination']['member_n']==5
    old=g['coordination']
    for t in ticks:e.row(t,e.days[13])['close']=10000
    assert coordination(e,ticks,e.days[12])==old
    d['taxonomy']['date']='2099-01-01'
    assert build_sector_feedback(e,d,daily)['groups']==[]


def test_coordination_includes_non_first_board_peers_and_missing_coverage():
    e=Evidence(fixture_data());ticks=list(e.info)
    c=coordination(e,ticks,e.days[12])
    assert c['median_pct']==pytest.approx(10) and c['first_board_n']==5 and c['amount_ratio']==1
    e.bar.pop((ticks[0],e.days[12]))
    c=coordination(e,ticks,e.days[12])
    assert c['valid_n']==4 and c['member_n']==5
    assert c['median_pct'] is None and c['amount_ratio'] is None
    assert c['first_board_n']==4 and not c['first_board_complete']
    assert c['limit_valid_n']==4


def test_risk_reduction_and_missing_data_are_explained():
    rows=series(1,2,health=20)
    for r in rows[-5:]:r['metrics']['participation']['value']=10
    r=evaluate('trend',rows,5)
    assert r['dimensions'][-1]['change_label']=='风险收敛'
    assert r['dimensions'][2]['dimension']=='收益覆盖'
    rows[-1]['metrics']['turnover_ratio'].update(n=0,value=None)
    r=evaluate('trend',rows,5)
    bad=next(x for x in r['diagnostic']['coverage_checks'] if x['key']=='turnover_ratio')
    assert not bad['covered'] and bad['required_n']==5 and bad['valid_n']==0
    assert r['price_support'] is True and r['status']=='unknown'


def test_sector_first_board_count_excludes_relay_and_ignores_capacity_filter():
    d=fixture_data();e=Evidence(d);ticks=list(e.info);day=e.days[12]
    # A known relay is not a first board; unknown prior state is not zero.
    streaks={day:{ticks[0]:1,ticks[1]:1,ticks[2]:2,ticks[3]:0,ticks[4]:None}}
    c=coordination(e,ticks,day,streaks)
    assert c['member_n']==5 and c['first_board_n']==2
    assert c['first_board_valid_n']==4 and not c['first_board_complete']
    assert [x['ticker'] for x in c['first_board_members']]==ticks[:2]
    # The return cohort may contain just one of two first boards (capacity filter).
    group={'code':'x','name':'示例概念','kind':'concept','research_eligible':True,
        'member_count':5,'members':[{'con_code':t} for t in ticks]}
    d['taxonomy']={'date':e.date,'rows':[group]}
    daily=daily_evidence(e,{'snapshots':{}},'lt50')['trend']
    daily=[x for x in daily if x['date']==e.days[13]]
    daily[0]['members']=daily[0]['members'][:1]
    result=build_sector_feedback(e,d,daily)['groups'][0]['daily'][e.days[13]]
    assert result['total']==1 and result['coordination']['first_board_n']==5


def test_turnover_gap_reasons_distinguish_suspension_listing_and_price_reference():
    from shilun.decision_research.playbook_assessment import observe
    d=fixture_data();t='600000.SH';e=Evidence(d);day=e.days[-5]
    e.suspensions[t]={day};e.db.pop((t,day),None)
    row=observe(e,t,len(e.days)-1)
    assert next(g for g in row['turnover_gap_details'] if g['date']==day)['reason']=='已确认停牌'
    e.suspensions={};e.info[t]['list_date']=e.days[-4]
    row=observe(e,t,len(e.days)-1)
    assert next(g for g in row['turnover_gap_details'] if g['date']==day)['reason']=='尚未上市'
    e.row(t,e.date)['pre_close']=999
    row=observe(e,t,len(e.days)-1)
    assert '不连续' in row['price_gap_reason'] and row.get('close_return') is None
