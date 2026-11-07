"""HTTP integration: configured store, provenance, revision and write boundary."""
from dataclasses import replace
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from shilun.api import create_app
from shilun.api import trade_journal
from shilun.common.config import load_config
from test_trade_journal import FakeStore, FakeCollection, bundle, fill


class InsertableCollection(FakeCollection):
    """Match immutable insert/unique-revision semantics used by method owners."""
    def insert_one(self, record):
        key_name = "mode_id" if self.name == "trade_journal_mode_cards" else "hypothesis_id"
        key = {key_name: record[key_name], "revision": record["revision"]}
        if any(self.match(row, key) for row in self.documents):
            raise DuplicateKeyError("immutable revision already exists")
        self.store.writes.append((self.name, deepcopy(key), {"$setOnInsert": deepcopy(record)}))
        self.documents.append(deepcopy(record))


class APIFakeStore(FakeStore):
    def collection(self, name):
        return self.collections.setdefault(name, InsertableCollection(name, self))


@pytest.fixture
def setup(monkeypatch):
    store=APIFakeStore()
    store.close_count=0
    def close():
        store.close_count+=1
    store.close=close
    monkeypatch.setattr(trade_journal,"_open_store",lambda:store)
    import shilun.trade_journal.indexes as indexes
    indexes_created=[]
    monkeypatch.setattr(indexes,"ensure_journal_indexes",lambda s:indexes_created.append(s))
    monkeypatch.setattr("shilun.api.load_config",lambda:replace(load_config(),auth_required=False))
    client=TestClient(create_app(),raise_server_exceptions=False)
    payload=bundle([fill("buy","2026-10-19","buy",100,10,100),fill("sell","2026-10-20","sell",100,12,0)])
    return client,store,payload,indexes_created


def import_rows(client,payload):
    preview=client.post('/api/v1/trade-journal/imports/preview',json=payload)
    assert preview.status_code==200,preview.text
    result=client.post('/api/v1/trade-journal/imports',json={**payload,'preview_digest':preview.json()['preview_digest']})
    assert result.status_code==200,result.text
    return result.json()


def test_read_preview_and_applied_import_use_same_store_without_live_writes(setup):
    client,store,payload,indexes=setup
    assert client.get('/api/v1/trade-journal/definitions').status_code==200
    assert store.close_count==1  # dynamic user-mode catalogue is on the same ECS store
    preview=client.post('/api/v1/trade-journal/imports/preview',json=payload)
    assert preview.status_code==200 and not store.writes and not indexes
    assert client.post('/api/v1/trade-journal/imports',json=payload).status_code==422
    first=import_rows(client,payload)
    second=import_rows(client,payload)
    assert not first['idempotent'] and second['idempotent']
    summary=client.get('/api/v1/trade-journal/summary').json()
    assert summary['counts']['fills']==2 and summary['counts']['episodes']==1
    assert indexes and store.close_count>0
    assert all(name.startswith('trade_journal_') for name,_,_ in store.writes)


def test_episode_decision_revision_and_history_preserve_old_version(setup):
    client,_,payload,_=setup
    import_rows(client,payload)
    episode=client.get('/api/v1/trade-journal/episodes',params={'query':'风华'}).json()['items'][0]
    path='/api/v1/trade-journal/episodes/'+episode['episode_id']+'/decisions'
    body={'action':'建仓','mode_id':'mode.ma5_trend','trigger':'回踩确认','reason':'当时观察到承接',
          'observed_at':'2026-10-19T10:00:00+08:00','evidence_class':'retrospective_recall'}
    first=client.post(path,json=body)
    assert first.status_code==200,first.text
    decision=first.json()['decision_id']
    revised={**body,'decision_id':decision,'expected_revision':1,'revision_reason':'补充原文','reason':'补充后的独立理由'}
    second=client.post(path,json=revised)
    assert second.status_code==200,second.text
    assert client.post(path,json=revised).status_code==409
    history=client.get('/api/v1/trade-journal/decisions/'+decision+'/history').json()
    assert history['total']==2 and history['items'][0]['reason']==body['reason']
    assert history['items'][1]['evidence_class']=='retrospective_recall'
    detail=client.get('/api/v1/trade-journal/episodes/'+episode['episode_id']).json()
    assert not detail['qualification']['ex_ante']['eligible']


def test_sources_are_metadata_by_default_and_original_can_be_restored(setup):
    client,_,payload,_=setup
    import_rows(client,payload)
    sources=client.get('/api/v1/trade-journal/sources').json()
    assert 'content' not in sources['items'][0]
    restored=client.get('/api/v1/trade-journal/sources/raw',params={'include_content':'true'}).json()
    assert restored['content']==payload['sources'][0]['content']
    assert client.get('/api/v1/trade-journal/sources/missing').status_code==404


def test_cross_origin_rejected_and_pagination_validated(setup):
    client,store,payload,_=setup
    rejected=client.post('/api/v1/trade-journal/imports/preview',json=payload,headers={'Origin':'https://unrelated.example'})
    assert rejected.status_code==403 and not store.writes
    accepted=client.post('/api/v1/trade-journal/imports/preview',json=payload,headers={'Origin':'http://testserver'})
    assert accepted.status_code==200
    assert client.get('/api/v1/trade-journal/episodes',params={'limit':1001}).status_code==422
    assert client.get('/api/v1/trade-journal/episodes',params={'offset':-1}).status_code==422


def test_mutated_payload_cannot_reuse_import_preview(setup):
    client,store,payload,_=setup
    preview=client.post('/api/v1/trade-journal/imports/preview',json=payload).json()
    payload['fills'][0]['price']=11
    result=client.post('/api/v1/trade-journal/imports',json={**payload,'preview_digest':preview['preview_digest']})
    assert result.status_code==422 and not store.writes


BASE = '/api/v1/trade-journal'


def created_mode(client):
    result=client.post(BASE+'/mode-cards',json={'name':'用户反核试验','status':'trial','purpose':'预期修复的一段'})
    assert result.status_code==200,result.text
    return result.json()


def imported_episode(client,payload):
    import_rows(client,payload)
    response=client.get(BASE+'/episodes')
    assert response.status_code==200,response.text
    return response.json()['items'][0]


def test_mode_card_api_roundtrip_exact_versions_and_cas(setup):
    client,store,_,indexes=setup
    first=created_mode(client); mode=first['mode_id']
    assert first['definition_snapshot']['rule_version']=='mode-card-v1'
    assert client.get(BASE+'/mode-cards/'+mode).status_code==422
    revised={'mode_id':mode,'expected_revision':1,'revision_reason':'退役保留原定义','status':'retired'}
    second=client.post(BASE+'/mode-cards',json=revised)
    assert second.status_code==200,second.text
    assert client.get(BASE+'/mode-cards/'+mode,params={'revision':1}).json()['status']=='trial'
    page=client.get(BASE+'/mode-cards',params={'status':'retired'}).json()
    assert page['items'][0]['revisions']==[1,2]
    catalogue=client.get(BASE+'/definitions',params={'category':'mode'}).json()
    assert next(row for row in catalogue['definitions'] if row['id']==mode)['rule_version']=='mode-card-v2'
    conflict=client.post(BASE+'/mode-cards',json=revised)
    assert conflict.status_code==409,conflict.text
    assert client.post(BASE+'/mode-cards',json={'name':'错误策略','strategy_id':'does_not_exist','strategy_version':'1'}).status_code==422
    assert indexes and all(name.startswith('trade_journal_') for name,_,_ in store.writes)


def test_unknown_strategy_and_missing_method_versions_are_http_client_errors(setup):
    client,_,_,_=setup
    result=client.post(BASE+'/mode-cards',json={'name':'错误策略','strategy_id':'does_not_exist','strategy_version':'1'})
    assert result.status_code==422,result.text
    assert client.get(BASE+'/mode-cards/mode.user.missing',params={'revision':0}).status_code==422
    assert client.get(BASE+'/mode-cards/mode.user.missing').status_code==422
    assert client.get(BASE+'/mode-cards/mode.user.missing',params={'revision':1}).status_code==404
    assert client.get(BASE+'/hypotheses/hypothesis.user.missing').status_code==422
    assert client.get(BASE+'/hypotheses/hypothesis.user.missing',params={'revision':1}).status_code==404
    assert client.get(BASE+'/plans/plan-missing',params={'revision':1}).status_code==404


def test_dynamic_mode_reference_versions_and_categories_are_checked_at_plan_http_boundary(setup):
    client,_,_,_=setup
    mode=created_mode(client)['mode_id']
    assert client.post(BASE+'/mode-cards',json={'mode_id':mode,'expected_revision':1,'revision_reason':'产生第二版本','purpose':'新版条件'}).status_code==200
    base={'ticker':'000636.SZ','mode_id':mode,'reason':'明确冻结第一版，不随目录升级'}
    first={'id':mode,'rule_version':'mode-card-v1'}
    ambiguous=client.post(BASE+'/plans',json={**base,'definition_refs':[first,{'id':mode,'rule_version':'mode-card-v2'}]})
    assert ambiguous.status_code==422,ambiguous.text
    misplaced=client.post(BASE+'/plans',json={**base,'definition_refs':[{**first,'category':'action'}]})
    assert misplaced.status_code==422,misplaced.text
    accepted=client.post(BASE+'/plans',json={**base,'definition_refs':[first]})
    assert accepted.status_code==200,accepted.text
    assert accepted.json()['record']['definition_snapshots'][0]['rule_version']=='mode-card-v1'


def test_independent_plan_freezes_user_mode_before_episode_and_binding_requires_exact_version(setup):
    client,_,payload,_=setup
    mode=created_mode(client)['mode_id']
    body={'ticker':'000636.SZ','mode_id':mode,'trigger':'翘板承接','reason':'独立计划先保存',
          'target_date':'2026-10-19','expectation':'次日修复','captured_at':'2000-01-01T00:00:00+08:00'}
    assert client.post(BASE+'/plans',json=body).status_code==422  # A bare user ID must not choose latest.
    body['definition_refs']=[{'id':mode,'rule_version':'mode-card-v1'}]
    first=client.post(BASE+'/plans',json=body)
    assert first.status_code==200,first.text
    plan=first.json(); pid=plan['plan_id']
    assert not plan['record']['captured_at'].startswith('2000')
    assert client.get(BASE+'/episodes').json()['total']==0
    revision={**body,'plan_id':pid,'expected_revision':1,'revision_reason':'补充期限','holding_horizon':'一个交易日'}
    assert client.post(BASE+'/plans',json=revision).status_code==200
    assert client.post(BASE+'/plans',json=revision).status_code==409
    old=client.get(BASE+'/plans/'+pid,params={'revision':1})
    assert old.status_code==200 and old.json()['definition_snapshots'][0]['rule_version']=='mode-card-v1'
    episode=imported_episode(client,payload)
    binding={'episode_id':episode['episode_id'],'fill_ids':['buy'],'reason':'明确关联原预案版本'}
    assert client.post(BASE+'/plans/'+pid+'/bind',json=binding).status_code==422
    bound=client.post(BASE+'/plans/'+pid+'/bind',json={**binding,'plan_revision':1})
    assert bound.status_code==200,bound.text
    assert bound.json()['record']['frozen_plan_ref']['revision']==1
    assert not bound.json()['qualification']['ex_ante']['eligible']
    assert client.get(BASE+'/plans/'+pid,params={'revision':999}).status_code==404


def test_hypothesis_http_keeps_exact_refs_attempts_and_rejects_unsupported_metrics(setup):
    client,_,_,_=setup
    mode=created_mode(client)['mode_id']
    body={'question':'原确认条件是否值得继续验证','status':'trial',
          'mode_ref':{'mode_id':mode,'revision':1},
          'entry_rule_refs':[{'id':mode,'rule_version':'mode-card-v1'}],
          'development_window':{'start':'2026-07-01','end':'2026-09-30'},
          'validation_window':{'start':'2026-10-01','end':'2026-10-23'},
          'parameter_attempts':[{'attempt_id':'a-one','parameters':{'research_threshold':1},'reason':'只登记假设'}]}
    first=client.post(BASE+'/hypotheses',json=body)
    assert first.status_code==200,first.text
    h=first.json(); hid=h['hypothesis_id']
    assert client.get(BASE+'/hypotheses/'+hid).status_code==422
    exact=client.get(BASE+'/hypotheses/'+hid,params={'revision':1}).json()
    assert exact['mode_snapshot']['rule_version']=='mode-card-v1'
    amendment={'hypothesis_id':hid,'expected_revision':1,'revision_reason':'追加失败记录',
               'validation_results':[{'result_id':'r-one','outcome_type':'inconclusive','notes':'原资料不足'}]}
    assert client.post(BASE+'/hypotheses',json=amendment).status_code==200
    assert client.post(BASE+'/hypotheses',json=amendment).status_code==409
    current=client.get(BASE+'/hypotheses').json()['items'][0]
    assert current['revisions']==[1,2] and len(current['content']['parameter_attempts'])==1
    invalid={**amendment,'expected_revision':2,'validation_results':[{'outcome_type':'observation','metrics':{'win_rate':1},'sample_count':1,'winning_count':1}]}
    assert client.post(BASE+'/hypotheses',json=invalid).status_code==422
    assert client.get(BASE+'/hypotheses/'+hid,params={'revision':2}).json()['advantage_verified'] is False


def test_allocations_and_analytics_api_reconcile_one_real_round_and_two_exact_mode_parts(setup):
    client,_,payload,_=setup
    mode=created_mode(client)['mode_id']; episode=imported_episode(client,payload); eid=episode['episode_id']
    user={'id':mode,'rule_version':'mode-card-v1'}
    static=client.get(BASE+'/definitions',params={'category':'mode'}).json()['definitions']
    relay=next({'id':r['id'],'rule_version':r['rule_version']} for r in static if r['id']=='mode.relay')
    assignments=[{'fill_id':fid,'mode_ref':ref,'quantity':q,**({'buy_lot_id':'buy'} if fid=='sell' else {})}
                 for fid in ('buy','sell') for ref,q in ((user,60),(relay,40))]
    route=BASE+'/episodes/'+eid+'/allocations'; body={'reason':'完整逐笔60/40归属','assignments':assignments}
    result=client.post(route,json=body)
    assert result.status_code==200,result.text
    assert result.json()['allocation']['reconciliation']['balanced'] is True
    assert client.post(route,json={**body,'expected_revision':0}).status_code==409
    assert client.get(route,params={'revision':1}).json()['record']['assignments'][0]['mode_ref']==user
    assert client.get(route+'/history').json()['items'][0]['revision']==1
    bad=deepcopy(body);bad['expected_revision']=1;bad['assignments'][0]['mode_ref'].pop('rule_version')
    assert client.post(route,json=bad).status_code==422
    report=client.get(BASE+'/analytics',params={'group_by':'mode'}).json()
    assert report['overall']['n']==1 and report['overall']['net_pnl']==pytest.approx(198)
    assert len(report['groups'])==2
    assert sum(group['statistics']['net_pnl'] for group in report['groups'])==pytest.approx(198)
    group=report['groups'][0]
    drill=client.get(BASE+'/analytics/groups/'+group['group_id'],params={'group_by':'mode'})
    assert drill.status_code==200 and drill.json()['members']==group['members']
    assert client.get(BASE+'/analytics',params={'group_by':'invented_dimension'}).status_code==422


def test_portfolio_http_metadata_exact_hash_plan_reference_and_unknown_projection(setup):
    from test_trade_journal_analytics import risk_facts
    client,store,_,_=setup; risk_facts(store)
    before=deepcopy(store.collection('holdings').documents)
    context=client.get(BASE+'/portfolio-context')
    assert context.status_code==200 and context.json()['market_value']==62900
    assert context.json()['historical']['max_drawdown'] is None and not store.writes
    preview=client.post(BASE+'/portfolio-context/preview',json={'scenario':{'legs':[{'ticker':'000636.SZ','side':'sell','quantity':100,'price':37,'fees':None}]}})
    assert preview.status_code==200 and preview.json()['scenario']['cash_delta'] is None
    captured=client.post(BASE+'/portfolio-snapshots',json={'historical_at':'2000-01-01','captured_at':'2000-01-01','reason':'保存当前事实'})
    assert captured.status_code==200,captured.text
    ref=captured.json()['snapshot_ref']; sid=ref['snapshot_id']
    assert not ref['captured_at'].startswith('2000')
    assert 'content' not in client.get(BASE+'/portfolio-snapshots/'+sid).json()
    original=client.get(BASE+'/portfolio-snapshots/'+sid,params={'include_content':True}).json()
    assert original['content']['current_verified_net_asset_value'] is None
    assert client.get(BASE+'/portfolio-snapshots').json()['items'][0]['snapshot_id']==sid
    plan=client.post(BASE+'/plans',json={'ticker':'000636.SZ','reason':'采用当前组合证据','portfolio_snapshot_ref':ref})
    assert plan.status_code==200,plan.text
    assert plan.json()['record']['content']['portfolio_snapshot_ref']['content_hash']==ref['content_hash']
    assert client.post(BASE+'/plans',json={'ticker':'000636.SZ','reason':'篡改引用','portfolio_snapshot_ref':{**ref,'content_hash':'bad'}}).status_code==422
    assert store.collection('holdings').documents==before
    assert all(name.startswith('trade_journal_') for name,_,_ in store.writes)
