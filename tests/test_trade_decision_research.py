from copy import deepcopy
from datetime import date, timedelta
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shilun.decision_research.engine import (Evidence, amount_band,
    holding_plan, market_evidence, route_mode, stats)
from shilun.decision_research.tracking import build_tracking, minute_observation
from shilun.decision_research import service
from shilun.decision_research.playbook_assessment import observe, daily_evidence


def dataset():
    start = date(2026, 8, 3)
    days = [(start + timedelta(days=i)).isoformat() for i in range(40) if (start + timedelta(days=i)).weekday() < 5][:27]
    bars = []
    for i, d in enumerate(days):
        close = 11 if i in (6, 12, 13, 14) else 10
        previous = bars[-1]['close'] if bars else 10
        bars.append({'ticker':'600001.SH','date':d,'open':10,'high':max(11,close),'low':9,'close':close,'pre_close':previous,'volume':100,'amount':10000})
    return {'date':days[-1], 'sessions':days, 'basics':[{'ts_code':'600001.SH','name':'样本'}],
        'bars':bars,'daily_basic':[{'ts_code':'600001.SH','trade_date':d.replace('-',''),'turnover_rate':2,'total_mv':1e7} for d in days],
        'limits':[{'ticker':'600001.SH','trade_date':d.replace('-',''),'up_limit':11} for d in days]}


@pytest.mark.parametrize('value,expected',[(None,'unknown'),(0,'unknown'),(5e9-1,'lt50'),(5e9,'50to100'),(1e10-1,'50to100'),(1e10,'ge100')])
def test_amount_boundaries(value, expected):
    assert amount_band(value) == expected


def test_mode_research_does_not_generate_directions_or_ma5(monkeypatch):
    from shilun.decision_research import engine
    from shilun.decision_research.mode_research import build_mode_research
    def forbidden(*args, **kwargs):
        raise AssertionError('独立研究不应生成个股预案')
    monkeypatch.setattr(engine, 'scan_universe', forbidden)
    monkeypatch.setattr(engine, 'direction_evidence', forbidden)
    result = build_mode_research(dataset())
    assert set(result['assessments']['windows']['5']) == {'trend', 'relay', 'crowding'}
    assert not {'groups','events','core_history'} & result.keys()
    assert 'first_board_sectors' in result
    assert not {'candidates', 'directions'} & result.keys()


def test_research_job_publishes_without_building_a_plan(tmp_path, monkeypatch):
    from shilun.api import review
    from shilun.decision_research.mode_research import source_fingerprint
    from datetime import datetime, timezone
    data = dataset()
    data['source_metadata'] = {'generated_at': datetime(2026, 9, 1, tzinfo=timezone.utc)}
    assert source_fingerprint(data) == source_fingerprint(json.loads(json.dumps(data, default=str)))
    class Store:
        closed = False
        def close(self): self.closed = True
    store = Store()
    def forbidden(*args, **kwargs):
        raise AssertionError('研究生成不应改写预案')
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    monkeypatch.setattr(service, 'RESEARCH_JOBS', {})
    monkeypatch.setattr(review, '_open_store', lambda: store)
    monkeypatch.setattr(service, 'read_dataset', lambda *_, **__: data)
    monkeypatch.setattr(service, 'build_plan', forbidden)
    monkeypatch.setattr(service, 'save_plan', forbidden)
    service._build(data['date'], research_only=True)
    assert service.RESEARCH_JOBS[data['date']]['status'] == 'ready'
    assert service.load_mode_research(data['date']) is not None
    assert service.load_plan(data['date']) is None
    assert store.closed


def test_completed_worker_exception_does_not_leave_running_state(tmp_path, monkeypatch):
    from concurrent.futures import Future
    class Pool:
        def submit(self, *args):
            future = Future()
            future.set_exception(SystemExit('worker aborted'))
            return future
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    monkeypatch.setattr(service, 'RESEARCH_JOBS', {})
    monkeypatch.setattr(service, 'JOBS', {})
    monkeypatch.setattr(service, 'FUTURES', {})
    monkeypatch.setattr(service, 'POOL', Pool())
    state = service.start_build('2026-09-01', research_only=True)
    assert state['status'] == 'error'
    assert '异常结束' in state['message']
    assert service.FUTURES == {}


def test_submit_failure_can_be_retried(tmp_path, monkeypatch):
    class Pool:
        def submit(self, *args):
            raise RuntimeError('executor closed')
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    monkeypatch.setattr(service, 'RESEARCH_JOBS', {})
    monkeypatch.setattr(service, 'JOBS', {})
    monkeypatch.setattr(service, 'POOL', Pool())
    for _ in range(2):
        state = service.start_build('2026-09-01', research_only=True)
        assert state['status'] == 'error'
        assert '启动失败' in state['message']


def test_read_failure_reports_stage_and_closes_store(monkeypatch):
    from shilun.api import review
    class Store:
        closed = False
        def close(self): self.closed = True
    store = Store()
    def fail_read(*args, progress):
        progress('读取历史换手与市值')
        assert service.job_status('2026-09-01', True)['stage'] == 'reading'
        raise TimeoutError('private connection details')
    monkeypatch.setattr(service, 'RESEARCH_JOBS', {})
    monkeypatch.setattr(review, '_open_store', lambda: store)
    monkeypatch.setattr(service, 'read_dataset', fail_read)
    service._build('2026-09-01', research_only=True)
    state = service.job_status('2026-09-01', True)
    assert state['status'] == 'error'
    assert '读取历史换手与市值未完成' in state['message']
    assert 'private' not in state['message']
    assert store.closed


def test_old_worker_completion_cannot_change_new_task(monkeypatch):
    from concurrent.futures import Future
    old, current = Future(), Future()
    old.set_result(None)
    monkeypatch.setattr(service, 'RESEARCH_JOBS', {'2026-09-01': {'status': 'running'}})
    monkeypatch.setattr(service, 'FUTURES', {('2026-09-01', True): current})
    service._finished('2026-09-01', True, old)
    assert service.RESEARCH_JOBS['2026-09-01']['status'] == 'running'
    assert service.FUTURES[('2026-09-01', True)] is current


def test_plan_consumes_snapshot_without_recalculating_mode_research(monkeypatch):
    from shilun.decision_research import mode_research, engine
    data = dataset()
    research = mode_research.build_mode_research(data)
    def forbidden(*args, **kwargs):
        raise AssertionError('预案必须使用传入评估')
    monkeypatch.setattr(mode_research, 'build_mode_research', forbidden)
    plan = engine.build_plan(data, research)
    assert plan['mode_research_ref'] == mode_research.reference(research)
    assert plan['conclusion'] == research['conclusion']
    assert not {'groups', 'events', 'core_history'} & plan.keys()
    plan['conclusion']['headline'] = '改动预案副本'
    assert research['conclusion']['headline'] != plan['conclusion']['headline']
    with pytest.raises(ValueError, match='日期或输入'):
        engine.build_plan({**data, 'date': data['sessions'][-2]}, research)
    changed = deepcopy(data)
    changed['bars'][-1]['close'] = 9
    with pytest.raises(ValueError, match='日期或输入'):
        engine.build_plan(changed, research)
    research['conclusion']['headline'] = '篡改评估'
    with pytest.raises(ValueError, match='校验失败'):
        engine.build_plan(data, research)


def test_research_refresh_preserves_plan_and_exact_historical_reference(tmp_path, monkeypatch):
    from shilun.decision_research.mode_research import build_mode_research
    from shilun.decision_research.engine import build_plan
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    data = dataset()
    original = service.save_mode_research(build_mode_research(data))
    same = build_mode_research({**data, 'loaded_at': 'later'})
    assert same['snapshot_id'] == original['snapshot_id']
    assert service.save_mode_research(same)['built_at'] == original['built_at']
    plan = service.save_plan(build_plan(data, original))
    frozen_bytes = service.path_for(data['date']).read_bytes()
    changed = deepcopy(data)
    changed['bars'][-1]['close'] = 9
    latest = service.save_mode_research(build_mode_research(changed))
    assert latest['snapshot_id'] != original['snapshot_id']
    assert service.load_mode_research(data['date']) == latest
    assert service.load_mode_research(data['date'], original['snapshot_id']) == original
    assert service.path_for(data['date']).read_bytes() == frozen_bytes
    assert service.load_plan(data['date'])['mode_research_ref'] == plan['mode_research_ref']
    assert len(list(tmp_path.glob('*/mode-research/*.json'))) == 2
    with pytest.raises(ValueError):
        service.save_plan({**plan, 'mode_research_ref': {}})
    with pytest.raises(ValueError):
        service.save_plan({**plan, 'conclusion': latest['conclusion'], 'market': {'tampered': True}})


def test_research_api_reads_only_exact_date_and_snapshot(tmp_path, monkeypatch):
    from shilun.api.trade_decision import router
    from shilun.decision_research.mode_research import build_mode_research
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    research = service.save_mode_research(build_mode_research(dataset()))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*.json')}
    app = FastAPI(); app.include_router(router); client = TestClient(app)
    url = '/api/v1/trade-decision/mode-research'
    assert client.get(url+'/dates').json()['dates'] == [research['analysis_date']]
    assert client.get(url, params={'date': research['analysis_date'], 'snapshot_id': research['snapshot_id']}).json()['research'] == research
    assert client.get(url, params={'date': '2026-09-21', 'snapshot_id': research['snapshot_id']}).json()['status'] == 'missing'
    assert client.get(url, params={'date': research['analysis_date'], 'snapshot_id': '0'*64}).json()['status'] == 'missing'
    assert client.get(url, params={'date': research['analysis_date'], 'snapshot_id': '../../plan'}).status_code == 422
    assert client.post(url+'/build?date=2099-01-01').status_code == 422
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*.json')} == before


def test_complete_event_denominator_and_next_open():
    data=dataset();e=Evidence(data);events,_=e.events()
    assert [x['group'] for x in events] == ['trend','trend','relay']
    event=events[0]
    data['bars'][7].update(open=10.5,close=9.5,high=10.7,low=9.3)
    data['bars'][8]['pre_close']=9.5
    e=Evidence(data);out=observe(e,event['ticker'],event['i']+1)
    assert out.get('opening') == pytest.approx((10.5/11-1)*100)
    assert out['close_return'] == pytest.approx((9.5/11-1)*100)
    # A suspension on the immediate next trading date does not skip to day 2.
    data['bars']=[r for r in data['bars'] if r['date']!=data['sessions'][7]]
    out=observe(Evidence(data),event['ticker'],event['i']+1)
    assert out.get('opening') is None
    assert 'prices' not in out


def test_future_rows_and_corporate_actions_do_not_leak():
    data=dataset();e=Evidence(data);event=e.events()[0][0]
    before=observe(e,event['ticker'],event['i']+1)
    data['bars'].append({**data['bars'][-1], 'date':'2099-01-01','close':9999})
    assert observe(Evidence(data),event['ticker'],event['i']+1)==before
    data['bars'][7]['pre_close']=5.5
    assert observe(Evidence(data),event['ticker'],event['i']+1).get('opening') is None


def test_left_censored_limit_streak_is_not_first_board():
    data=dataset()
    for r in data['bars'][:10]:r['close']=11
    assert not any(e['date']<=data['sessions'][9] for e in Evidence(data).events()[0])


def test_red_k_uses_open_and_true_zero_is_not_missing():
    data=dataset()
    data['bars'][-1].update(open=9.2,close=9.5,pre_close=10)
    out=observe(Evidence(data),'600001.SH',26)
    assert out['red']==1 and out['close_return']==pytest.approx(-5)
    data['bars'][-1]['open']=9.5
    assert observe(Evidence(data),'600001.SH',26)['red']==0
    assert stats([None,0]) == {'n':1,'missing':1,'mean':0,'median':0}


def market(amount, change=1):
    return {'turnover':{'market_amount_cny':amount},'main_index':'000001.SH','indices':[{'ticker':'000001.SH','change_pct':change}]}


def test_modes_require_feedback_and_do_not_force_ma5():
    assert route_mode(market(2.3e12))['mode']=='B'
    assert route_mode(market(2.3e12+1))['mode']=='A'
    assert route_mode(market(2.5e12))['status']=='conditional'
    assert route_mode(market(2.5e12),{'trend':True})['status']=='confirmed'
    assert route_mode(market(2.5e12),{'trend':False,'relay':False,'crowding':False})['status']=='wait'
    assert route_mode(market(2e12),{'relay':True})['mode']=='B'
    assert route_mode(market(2.5e12,-1))['mode']=='B'
    assert route_mode(market(None))['status']=='unknown'


@pytest.mark.parametrize('expectation,upstream,no_better,expected',[
    ('normal',True,False,'exit'),('normal',True,True,'hold'),
    ('below',True,True,'exit'),('above',False,True,'exit'),
    ('above',True,False,'hold'),('above',None,True,'unknown')])
def test_user_holding_exception_boundary(expectation,upstream,no_better,expected):
    assert holding_plan(expectation,upstream,True,no_better)==expected


def minute_doc():
    return {'ticker':'600001.SH','trade_date':'20260922','interval':'5','source':'tushare_stk_mins',
        'bars':[{'datetime':f'2026-09-22T{clock}:00','open':10,'high':12,'low':9,'close':11,'volume':100,'amount':1000} for clock in ('09:35','09:40','09:45','10:00')]}


def test_minute_clock_units_gaps_staleness_and_indices():
    doc=minute_doc()
    early=minute_observation(doc,'2026-09-22','09:40',10,10)
    assert early['bars_n']==2 and early['vwap_above_count'] is None
    later=minute_observation(doc,'2026-09-22','09:45',10,10)
    assert later['vwap_above_count']==3
    assert minute_observation(doc,'2026-09-22','10:00',10,10)['vwap_above_count'] is None
    assert minute_observation(doc,'2026-09-22','10:00',10,10)['vwap'] is None
    assert minute_observation(doc,'2026-09-22','10:10',10,10)['status']=='stale'
    assert minute_observation({**doc,'source':'klineshare'},'2026-09-22','09:45',10)['vwap'] is None
    assert minute_observation({**doc,'ticker':'000001.SH'},'2026-09-22','09:45',10)['vwap'] is None
    doc['bars'].append(doc['bars'][0])
    assert minute_observation(doc,'2026-09-22','09:45',10)['status']=='missing'


def test_tracking_rejects_wrong_baseline_and_future_auction():
    p={'analysis_date':'2026-09-21','next_date':'2026-09-22','built_at':'test','directions':[],
       'candidates':[{'ticker':'600001.SH','name':'sample','directions':[]}], 'route':{},'stocks':{},'baseline_closes':{'600001.SH':10}}
    with pytest.raises(ValueError): build_tracking(p,[],[],'2026-09-23','10:00')
    t=build_tracking(p,[minute_doc()],[{'date':'2026-09-22','auction_time':'09:25','amounts':{}}],'2026-09-22','09:20')
    assert t['auction']['status']=='missing'
    assert t['candidates'][0]['status']=='missing'


def test_snapshot_is_frozen_and_date_safe(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'ROOT',tmp_path)
    service.save_plan({'analysis_date':'2026-09-21','built_at':'first'})
    service.save_plan({'analysis_date':'2026-09-21','built_at':'second'})
    assert service.load_plan('2026-09-21')['built_at']=='first'
    assert service.load_plan('2026-09-22') is None
    with pytest.raises(ValueError):service.path_for('../../other')
    with pytest.raises(ValueError):service.start_build('2099-01-01')


def test_api_missing_date_not_silently_latest(tmp_path,monkeypatch):
    from shilun.api.trade_decision import router
    monkeypatch.setattr(service,'ROOT',tmp_path)
    app=FastAPI();app.include_router(router);client=TestClient(app)
    assert client.get('/api/v1/trade-decision/plan?date=2026-09-21').json()['status']=='missing'
    assert client.get('/api/v1/trade-decision/plan?date=invalid').status_code==422
    assert client.post('/api/v1/trade-decision/build?date=2099-01-01').status_code==422
    assert client.get('/api/v1/trade-decision/tracking?date=2026-09-21').status_code==422


def core_dataset():
    data = dataset()
    for i, r in enumerate(data['bars']):
        r.update(close=11 if 7 <= i <= 11 else 10, low=9, high=11)
        r['pre_close'] = data['bars'][i-1]['close'] if i else 10
    return data


def test_historical_cores_keep_failed_stocks_and_do_not_use_today_survivors():
    from shilun.decision_research.cohorts import reconstruct_cores
    data=core_dataset(); e=Evidence(data)
    history=reconstruct_cores(data,e)
    assert history['snapshots'][e.days[10]] == []
    assert history['current'] == []  # expired core remains in historical snapshots
    assert history['snapshots'][e.days[11]][0]['ticker'] == '600001.SH'
    data['core_snapshots']=[{'date':e.date,'rows':[]}]
    assert reconstruct_cores(data,e)['snapshots'] == history['snapshots']
    prefix={**data,'date':e.days[23]}
    prefix_history=reconstruct_cores(prefix,Evidence(prefix))
    assert all(prefix_history['snapshots'][d] == history['snapshots'][d] for d in prefix_history['snapshots'])


def test_daily_core_feedback_uses_yesterday_and_keeps_failure_day():
    data=core_dataset(); e=Evidence(data)
    day=e.days[-1]; previous=e.days[-2]
    baskets={previous:[{'ticker':'600001.SH'}],day:[]}
    data['bars'][-1].update(open=10,close=9.5,high=10,low=9,pre_close=10)
    summary=daily_evidence(Evidence(data),{'snapshots':baskets},'lt50')['crowding']
    assert summary[-1]['cohort_n']==1
    assert summary[-1]['metrics']['primary']['value']==pytest.approx(-5)
    assert summary[-1]['metrics']['red']['value']==0


def test_citic_net_change_and_stale_or_conflicting_data():
    from shilun.decision_research.assessment import citic_evidence
    rows=[{'product':p,'contract':p+'2612','data_date':'2026-09-21','citic_long':10,
           'citic_short':20,'citic_long_change':-5,'citic_short_change':-2} for p in ('IF','IH','IC','IM')]
    data={'date':'2026-09-21','review':{'market':{'futures':{'products':rows}}}}
    result=citic_evidence(data)
    assert result['net_short']==40 and result['change']==12
    rows[0]['data_date']='2026-09-18'
    assert citic_evidence(data)['net_short'] is None


def test_median_is_not_dominated_by_extreme_winners():
    assert stats([-2,-1,100])['median']==-1


def test_upgrade_archives_previous_version_and_refreezes(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'ROOT',tmp_path)
    service.save_plan({'analysis_date':'2026-09-21','built_at':'first','version':'v1'})
    service.save_plan({'analysis_date':'2026-09-21','built_at':'second','version':'v2'})
    service.save_plan({'analysis_date':'2026-09-21','built_at':'third','version':'v2'})
    current=service.load_plan('2026-09-21')
    assert current['built_at']=='second'
    assert json.loads((tmp_path/current['supersedes']['archive']).read_text())['built_at']=='first'


def test_direction_limits_deduplication_and_candidates_need_upstream():
    from shilun.decision_research.directions import shortlist, choose_candidates
    class E:
        date='2026-09-21'
        days=[f'2026-09-{i:02d}' for i in range(10,22)]
        def row(self,t,d):
            value=100 if t=='000001.SH' else 100+self.days.index(d)
            return {'open':value,'close':value,'high':value,'low':value,'pre_close':value if t=='000001.SH' else value-1,'volume':100}
        def stock(self,t): return {'return_pct':1,'ma5':100,'close':111}
    e=E();directions=[];themes=[];cores=[]
    for i in range(7):
        ticker=f'60000{i}.SH';code=f'theme{i}'
        directions.append({'code':code,'name':code,'members':[ticker], 'member_n':1,'valid_n':1,
            'core_refs':[ticker],'relay_refs':[ticker], 'up_pct':100,'amount_ratio':1.1,'return_median_pct':1})
        themes.append({'code':code,'attention_rank':i+1,'round':{'leaders':[{'ticker':ticker}]}})
        cores.append({'ticker':ticker})
    # Additional label for the exact same core must not consume a slot.
    directions.append({**directions[0],'code':'duplicate'})
    themes.append({'code':'duplicate','attention_rank':1,'round':themes[0]['round']})
    data={'review':{'analysis_date':e.date,'sentiment':{'theme_evidence':{'themes':themes}}}}
    selected,scope,_=shortlist(e,data,directions,{'mode':'B','status':'conditional'},{'current':cores})
    assert scope['main_n']==3 and scope['backup_n']==2
    assert len({t for d in selected for t in d['watch_refs']})==5
    assert choose_candidates(e,[{'ticker':'600000.SH'}],selected)==[]  # B not MA5
    waiting,scope,_=shortlist(e,data,directions,{'mode':None,'status':'wait'},{'current':cores})
    assert scope['main_n']==0 and scope['backup_n']<=2


def test_tracking_missing_input_does_not_falsely_change_direction():
    p={'analysis_date':'2026-09-21','next_date':'2026-09-22','built_at':'test','directions':[],
       'candidates':[], 'route':{},'stocks':{},'baseline_closes':{'000001.SH':10}}
    t=build_tracking(p,[],[],'2026-09-22','10:00')
    assert t['layer_changes'][0]['state']=='missing'
    assert t['market_turnover']['projected_full_day_cny'] is None


def test_mode_conclusion_never_ranks_different_admission_stages():
    from shilun.decision_research.assessment import decision_conclusion
    def group(value):
        return {'daily':[{'cohort_n':4,'valid_n':4,'one_price_n':0,
            'return_pct':{'n':4,'median':value},'turnover_pct':{'n':4},
            'volume_ratio':{'n':4}} for _ in range(3)]}
    groups={'trend':group(1),'relay':group(2),'crowding':group(50)}
    first=decision_conclusion({},groups,{'mode':'B'},'all')
    groups['relay']=group(100)
    second=decision_conclusion({},groups,{'mode':'B'},'all')
    assert first['headline']==second['headline']
    assert first['preferred_group'] is None and not first['ranking_applied']
    assert set(first['positive_groups'])=={'relay','crowding'}
    waiting=decision_conclusion({},groups,{'mode':None},'all')
    assert waiting['positive_groups']==[]
    assert 'B路径' not in waiting['new_entry']


def test_next_day_first_board_check_freezes_only_baseline_and_capacity_band():
    from shilun.decision_research.assessment import first_board_next_day_check
    events=[{'ticker':str(i),'date':'2026-09-21','group':'trend','band':band}
            for i,band in enumerate(['lt50','50to100','ge100','unknown'])]
    events += [{**events[0],'date':'2026-09-22'}, {**events[0],'group':'relay'}]
    check=first_board_next_day_check(events,'2026-09-21','2026-09-22','lt50')
    assert check['cohort_n']==1 and check['total_n']==4
    assert check['unknown_amount_n']==1
    assert check['members']==[{'ticker':'0','name':'0'}]
    assert check['opening_premium_median'] is None and check['positive_open_pct'] is None
    assert check['status']=='pending'
    assert first_board_next_day_check(events,'2026-09-21','2026-09-22','lt100')['cohort_n']==2
