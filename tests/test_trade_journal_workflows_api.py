"""P0 workflow boundaries through real domain implementations and HTTP."""
from copy import deepcopy
import base64

from test_trade_journal_api import setup, import_rows, BASE
from test_trade_journal import bundle, fill


def test_execution_matching_http_preview_confirm_history_unlink_and_write_boundary(setup):
    client, store, payload, _ = setup
    import_rows(client, payload)
    episode = client.get(BASE+'/episodes').json()['items'][0]['episode_id']
    live = {'execution_id':'live-sale','ticker':'000636.SZ','side':'sell','quantity':100,
            'price':12,'fees':1,'cash_delta':1199,'executed_at':'2026-10-20T10:00:00+08:00','source':'user_confirmed'}
    store.collection('trade_executions').documents.append(deepcopy(live))
    path = BASE+'/episodes/'+episode+'/execution-matches'
    candidates = client.get(path)
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()['items'][0]['state'] == 'unmatched'
    writes = len(store.writes)
    preview = client.post(path+'/preview',json={'execution_id':'live-sale','fill_ids':['sell']})
    assert preview.status_code == 200, preview.text
    assert preview.json()['can_confirm'] and len(store.writes) == writes
    body = {'action':'confirm','execution_id':'live-sale','fill_ids':['sell'],'expected_revision':0,
            'preview_digest':preview.json()['preview_digest'],'identity_confirmation':True,
            'account_confirmation':True,'reason':'已核对是同一账户同笔真实成交'}
    saved = client.post(path,json=body)
    assert saved.status_code == 200,saved.text
    assert saved.json()['record']['state'] == 'confirmed'
    assert client.post(path,json=body).status_code == 409
    current = client.get(BASE+'/execution-matches/live-sale').json()
    assert current['history'][0]['fill_ids'] == ['sell']
    unlink = client.post(path,json={'action':'unlink','execution_id':'live-sale','expected_revision':1,
                                   'live_digest':current['live_digest'],'reason':'保留旧关联历史，重新核对'})
    assert unlink.status_code == 200,unlink.text
    assert unlink.json()['match']['state'] == 'unlinked'
    assert len(unlink.json()['match']['history']) == 2
    assert store.collection('trade_executions').documents == [live]
    assert all(name.startswith('trade_journal_') for name,_,_ in store.writes[writes:])
    assert client.get(BASE+'/summary').json()['counts']['fills'] == 2


def test_execution_matching_http_missing_identity_and_invalid_inputs_are_client_errors(setup):
    client, store, payload, _ = setup
    import_rows(client, payload)
    episode = client.get(BASE+'/episodes').json()['items'][0]['episode_id']
    path = BASE+'/episodes/'+episode+'/execution-matches'
    assert client.post(path+'/preview',json={}).status_code == 422
    assert client.get(BASE+'/execution-matches/absent').status_code == 404
    assert client.post(path+'/preview',json={'execution_id':'absent','fill_ids':['sell']}).status_code == 404
    assert client.post(path,json={'execution_id':'absent','action':'confirm'}).status_code == 422


def test_task_http_lifecycle_does_not_fill_fields_or_change_qualification(setup):
    client, store, payload, _ = setup
    import_rows(client,payload)
    episode = client.get(BASE+'/episodes').json()['items'][0]['episode_id']
    before = client.get(BASE+'/episodes/'+episode).json()
    writes = len(store.writes)
    tasks = client.get(BASE+'/tasks',params={'episode_id':episode,'status':'open'})
    assert tasks.status_code == 200,tasks.text
    assert len(store.writes) == writes
    task = next(row for row in tasks.json()['items'] if row['root_cause']=='decision_context')
    path = BASE+'/tasks/'+task['task_id']
    body = {'expected_revision':0,'expected_fingerprint':task['fact_fingerprint'],
            'action':'confirm_unknown','reason':'用户确认无法恢复当时原始理由'}
    confirmed = client.post(path+'/resolutions',json=body)
    assert confirmed.status_code == 200,confirmed.text
    assert confirmed.json()['task']['state'] == 'confirmed_unknown'
    assert client.post(path+'/resolutions',json=body).status_code == 409
    original = client.get(path).json()
    assert original['history'][0]['reason'] == body['reason']
    reopened = client.post(path+'/resolutions',json={**body,'expected_revision':1,'action':'reopen','reason':'发现新线索待核对'})
    assert reopened.status_code == 200,reopened.text
    assert reopened.json()['task']['state'] == 'open'
    assert client.get(BASE+'/episodes/'+episode).json()['qualification'] == before['qualification']
    assert client.get(BASE+'/tasks/absent').status_code == 404
    assert client.post(path+'/resolutions',json={'expected_revision':2,'action':'ignore','reason':'缺指纹应拒绝'}).status_code == 422


def csv_upload():
    content = ('成交日期,成交时间,证券代码,证券名称,买卖类别,成交数量,成交价格,成交金额,费用合计,净收付,股票余额,成交编号\n'
               '2026-10-19,10:00:00,000636,风华高科,买入,100,10,1000,5,-1005,100,000001\n'
               '2026-10-20,10:00:00,000636,风华高科,卖出,100,11,1100,5,1095,0,000002\n')
    return {'filename':'真实导出.csv','content_base64':base64.b64encode(content.encode()).decode()}


def test_raw_file_inspect_mapping_parse_preview_and_final_confirmation_share_existing_import_owner(setup):
    client,store,_,indexes = setup
    upload = csv_upload()
    inspected = client.post(BASE+'/import-files/inspect',json=upload)
    assert inspected.status_code == 200,inspected.text
    mapping = inspected.json()['suggested_mapping']
    parsed = client.post(BASE+'/import-files/parse',json={**upload,'mapping':mapping,'account_alias':'primary'})
    assert parsed.status_code == 200,parsed.text
    result = parsed.json()
    assert result['can_preview'] and result['preview']['can_import']
    assert not store.writes and not indexes
    assert len(result['payload']['fills']) == 2
    assert result['payload']['fills'][0]['code'] == '000636'
    committed = client.post(BASE+'/imports',json={**result['payload'],'preview_digest':result['preview']['preview_digest']})
    assert committed.status_code == 200,committed.text
    episode = client.get(BASE+'/episodes').json()['items'][0]
    assert episode['closed_pnl'] == 90 and episode['ticker'] == '000636.SZ'
    restored = client.get(BASE+'/sources/'+result['payload']['sources'][0]['source_id'],params={'include_content':True}).json()
    assert base64.b64decode(restored['content']) == base64.b64decode(upload['content_base64'])
    assert all(name.startswith('trade_journal_') for name,_,_ in store.writes)


def test_file_unresolved_rows_and_invalid_bytes_never_return_importable_partial_payload(setup):
    client,store,_,_=setup
    upload=csv_upload()
    assert client.post(BASE+'/import-files/inspect',json={**upload,'content_base64':'invalid'}).status_code == 422
    inspected = client.post(BASE+'/import-files/inspect',json=upload).json()
    mapping = inspected['suggested_mapping']
    raw = base64.b64decode(upload['content_base64']).decode().replace('风华高科,买入','风华高科,无法识别业务')
    upload['content_base64'] = base64.b64encode(raw.encode()).decode()
    parsed = client.post(BASE+'/import-files/parse',json={**upload,'mapping':mapping})
    assert parsed.status_code == 200,parsed.text
    assert not parsed.json()['can_preview'] and parsed.json()['payload'] is None and parsed.json()['preview'] is None
    assert parsed.json()['issues'] and not store.writes


def test_file_leading_zero_alias_requires_explicit_original_row_confirmation_through_http(setup):
    client,store,_,_=setup
    seed=bundle([fill('legacy-fact','2026-10-19','buy',100,10,100,fees=5,broker_execution_id='1234')])
    import_rows(client,seed)
    raw=('成交日期,成交时间,证券代码,证券名称,买卖类别,成交数量,成交价格,成交金额,费用合计,净收付,股票余额,成交编号\n'
         '2026-10-19,10:00:00,000636,风华高科,买入,100,10,1000,5,-1005,100,00001234\n')
    upload={'filename':'原编号.csv','content_base64':base64.b64encode(raw.encode()).decode()}
    inspected=client.post(BASE+'/import-files/inspect',json=upload).json()
    request={**upload,'mapping':inspected['suggested_mapping']}
    first=client.post(BASE+'/import-files/parse',json=request)
    assert first.status_code==200,first.text
    result=first.json()
    assert not result['preview']['can_import']
    assert client.get(BASE+'/summary').json()['counts']['fills']==1
    candidate=result['preview']['identity_candidates'][0]
    assert candidate['incoming_broker_execution_id']=='00001234'
    assert candidate['existing_broker_execution_id']=='1234'
    link={k:candidate[k] for k in ('incoming_source_ref','existing_fact_id','expected_existing_hash')}
    link.update(identity_confirmation=True,reason='逐原件核对同一成交，原归档显示编号缺少前导零')
    confirmed=client.post(BASE+'/import-files/parse',json={**request,'identity_links':[link]})
    assert confirmed.status_code==200,confirmed.text
    parsed=confirmed.json()
    assert parsed['preview']['can_import'] and parsed['preview']['counts']['duplicate_fills']==1
    commit=client.post(BASE+'/imports',json={**parsed['payload'],'preview_digest':parsed['preview']['preview_digest']})
    assert commit.status_code==200,commit.text
    assert client.get(BASE+'/summary').json()['counts']['fills']==1
    row=client.get(BASE+'/fills').json()['items'][0]
    assert row['fill_id']=='legacy-fact' and row['broker_execution_id']=='1234'
    assert client.post(BASE+'/imports',json={**parsed['payload'],'preview_digest':parsed['preview']['preview_digest']}).json()['idempotent']
    repeated=client.post(BASE+'/import-files/parse',json=request).json()
    assert repeated['preview']['can_import'] and repeated['preview']['counts']['new_fills']==0
    assert all(name.startswith('trade_journal_') for name,_,_ in store.writes)
