"""Decision boundaries: frozen populations, distinct payoffs, unknown != zero."""
from copy import deepcopy
from datetime import date, timedelta
import pytest

from shilun.decision_research.engine import Evidence
from shilun.decision_research.playbook_assessment import (
    evaluate, observe, daily_evidence, compare, conclusion_from_assessments)


def series(before=1, recent=2, recent_breadth=70, red=60, health=0):
    out=[]
    for i in range(20):
        values={'primary':before if i<15 else recent,'breadth':60 if i<15 else recent_breadth,
                'red':red,'negative':0,'participation':health,'one_price':0,
                'promotion':60,'failed_return':1,'turnover_ratio':1}
        out.append({'date':f'2026-09-{i+1:02}', 'formation_date':f'2026-09-{i:02}',
            'cohort_n':5,'metrics':{k:{'value':v,'n':5,'total':5} for k,v in values.items()},
            'members':[]})
    return out


def test_improving_losses_are_still_ineligible():
    rows=series(-5,-1,40)
    for r in rows[:15]:r['metrics']['breadth']['value']=20
    result=evaluate('trend',rows,5)
    assert result['change']=='improving'
    assert result['status']=='unsupported' and result['selection_support'] is False


def test_first_board_opening_premium_does_not_require_red_candles_or_t5():
    result=evaluate('trend',series(1,2,70,red=0),5)
    assert result['status']=='conditional'
    assert result['selection_support'] is True
    # No T+5 / other group's return enters the independent judgement.
    assert result['focus'].startswith('先手的次日开盘溢价')


@pytest.mark.parametrize('key',['negative','participation'])
def test_high_returns_cannot_override_worsening_risk(key):
    rows=series(1,20)
    for r in rows[-5:]:r['metrics'][key]['value']=40
    result=evaluate('crowding',rows,5)
    assert result['price_support'] is True
    assert result['status']=='conflicted'
    assert result['selection_support'] is False


def test_missing_latest_date_is_not_replaced_by_earlier_winners():
    rows=series()
    rows[-1]['metrics']['primary']={'value':None,'n':0,'total':5}
    result=evaluate('trend',rows,5)
    assert result['status']=='unknown'
    assert result['dimensions'][0]['recent']['value'] is None
    assert result['dimensions'][0]['recent']['days_n']==4
    rows=series()
    rows[-1]['metrics']['participation']={'value':None,'n':0,'total':5}
    assert evaluate('trend',rows,5)['status']=='unknown'


def test_windows_are_non_overlapping_and_thin_groups_remain_unknown():
    rows=series()
    c=compare(rows,5,'primary','开盘','收益')
    assert c['baseline']['to'] < c['recent']['from']
    assert c['baseline']['days_total']==10 and c['recent']['days_total']==5
    for r in rows:
        r['metrics']['primary'].update(n=2,total=2)
    assert evaluate('crowding',rows,5)['status']=='unknown'


def fixture_data():
    start=date(2026,8,3)
    days=[(start+timedelta(days=i)).isoformat() for i in range(40) if (start+timedelta(days=i)).weekday()<5][:27]
    data={'date':days[-1],'sessions':days,'basics':[],'bars':[],'daily_basic':[],'limits':[]}
    for j in range(5):
        t=f'60000{j}.SH';data['basics'].append({'ts_code':t,'name':t})
        previous=10
        for i,d in enumerate(days):
            close=round(previous*1.1,2) if i in (12,13) else round(previous*.9,2) if i==14 else round(previous*.95,2) if i==15 else previous
            r={'ticker':t,'date':d,'open':previous,'high':max(previous,close)+.1,
               'low':min(previous,close)-.1,'close':close,'pre_close':previous,
               'volume':10 if i==15 else 100,'amount':100000}
            data['bars'].append(r)
            data['daily_basic'].append({'ts_code':t,'trade_date':d,'turnover_rate':2})
            data['limits'].append({'ticker':t,'trade_date':d,'up_limit':round(previous*1.1,2)})
            previous=close
    return data


def test_failure_cohorts_and_yesterday_core_members_do_not_disappear():
    data=fixture_data();e=Evidence(data)
    yesterday=e.days[14];today=e.days[15]
    cores={'snapshots':{yesterday:[{'ticker':r['ts_code']} for r in data['basics']],today:[]}}
    result=daily_evidence(e,cores,'lt50')
    r=next(x for x in result['relay'] if x['date']==e.days[14])
    assert r['cohort_n']==5 and r['metrics']['promotion']['value']==0
    later=next(x for x in result['relay'] if x['date']==today)
    assert len(later['failed_members'])==5 and later['metrics']['failed_return']['value']<0
    core=next(x for x in result['crowding'] if x['date']==today)
    assert core['cohort_n']==5 and core['metrics']['primary']['value']<0
    assert core['metrics']['participation']['value']==100 # personal 10-day low volume + falling prices


def test_todays_winners_and_future_rows_cannot_change_frozen_membership():
    data=fixture_data();e=Evidence(data)
    core={'snapshots':{e.days[-2]:[{'ticker':'600000.SH'}],e.days[-1]:[{'ticker':'600001.SH'}]}}
    first=daily_evidence(e,core,'lt50')
    assert first['crowding'][-1]['members'][0]['ticker']=='600000.SH'
    data['bars'].append({**data['bars'][-1],'date':'2099-01-01','close':100000})
    assert daily_evidence(Evidence(data),core,'lt50')==first


def test_self_history_excludes_today_and_missing_history_is_unknown():
    data=fixture_data();e=Evidence(data)
    r=observe(e,'600000.SH',15)
    assert r['volume_ratio']==pytest.approx(.1) and r['dry_weak']==1
    data['daily_basic']=[r for r in data['daily_basic'] if not (r['ts_code']=='600000.SH' and r['trade_date']==e.days[10])]
    r=observe(Evidence(data),'600000.SH',15)
    assert r['adverse']==1 and 'turnover_ratio' not in r
    assert e.days[10] in r['missing_turnover_dates']


def test_one_price_winners_do_not_enter_exchange_relay_population():
    data=fixture_data()
    for r in data['bars']:
        if r['ticker']=='600000.SH' and r['date']==data['sessions'][13]:
            for k in ('open','high','low'):r[k]=r['close']
    rs=daily_evidence(Evidence(data),{'snapshots':{}},'lt50')['relay']
    day=next(r for r in rs if r['date']==data['sessions'][14])
    assert day['cohort_n']==4 and len(day['locked_members'])==1
    assert all(r['ticker']!='600000.SH' for r in day['members'])


def test_no_automatic_b_fallback_when_only_first_boards_have_support():
    modes={k:evaluate(k,series(),5) for k in ('trend','relay','crowding')}
    for k in ('relay','crowding'):modes[k]['selection_support']=None
    market={'turnover':{'market_amount_cny':2e12},'main_index':'000001.SH','indices':[{'ticker':'000001.SH','change_pct':1}]}
    route,_=conclusion_from_assessments({'windows':{'5':modes},'method':'test'},market)
    assert route['mode'] is None


def test_audit_prices_reproduce_feedback_and_preserve_true_zero():
    data = fixture_data()
    e = Evidence(data)
    flat = observe(e, '600000.SH', 11)
    assert flat['opening'] == 0 and flat['close_return'] == 0
    p = flat['prices']
    assert p['formation_date'] == e.days[10]
    assert p['feedback_date'] == e.days[11]
    assert (p['open'] / p['previous_close'] - 1) * 100 == flat['opening']
    assert (p['close'] / p['previous_close'] - 1) * 100 == flat['close_return']
    # A discontinuous pair must not offer apparently valid verification prices.
    e.row('600000.SH', e.days[11])['pre_close'] += 1
    missing = observe(e, '600000.SH', 11)
    assert 'prices' not in missing and 'opening' not in missing


def test_first_board_audit_preserves_formation_capacity_and_predecessor():
    e = Evidence(fixture_data())
    days = daily_evidence(e, {'snapshots': {}}, 'lt50')['trend']
    first = next(d for d in days if d['date'] == e.days[13])
    assert first['cohort_n'] == 5
    for row in first['members']:
        f = row['first_board_evidence']
        assert f['date'] == e.days[12] and f['prior_date'] == e.days[11]
        assert f['prior_streak'] == 0 and f['prior_close'] != f['prior_up_limit']
        assert f['close'] == f['up_limit']
        assert f['avg7_cny'] == e.avg_amount(row['ticker'], 12)
