"""Reimport provenance must preserve canonical IDs, FIFO and original bytes."""
import base64
from copy import deepcopy
import json
from pathlib import Path

import pytest

from shilun.trade_journal.import_files import inspect_file,parse_file
from shilun.trade_journal.import_identity import reconcile_import
from shilun.trade_journal.service import TradeJournalError,TradeJournalService
from test_trade_journal import FakeStore,bundle,fill,source


def put(svc,payload):
    preview=svc.preview_import(payload)
    assert preview['can_import'],preview['conflicts']
    return svc.import_batch(payload,preview['preview_digest'])


def prior_and_incoming(*,same_source=False,broker_alias=False):
    old_source=source('legacy-source','identical original' if same_source else 'old JSON')
    new_source=source('new-file-source','identical original' if same_source else 'new XLSX',metadata={'parser_version':'file-v1','mapping':{'selected':'raw'}})
    common={'source_ref':{'source_id':'legacy-source','sheet':'交割单','row':6}}
    if broker_alias:common['broker_execution_id']='1234'
    else:common.update(time_precision='statement_event_time',statement_event_time=None)
    old=bundle([fill('canonical-old','2026-10-19','buy',100,10,100,**common)],sources=[old_source])
    new=deepcopy(old);new['sources']=[new_source];new['fills'][0].update(fill_id='new-generated',source_ref={'source_id':'new-file-source','sheet':'交割单','row':6})
    if broker_alias:new['fills'][0]['broker_execution_id']='00001234'
    else:new['fills'][0]['statement_event_time']='10:00:00'
    return old,new


def confirm(candidate,**changes):
    return {k:candidate[k] for k in ('incoming_source_ref','existing_fact_id','expected_existing_hash')}|{'identity_confirmation':True,'reason':'逐行核对原件，编号仅多前导0，确认同一成交'}|changes


def test_same_original_row_reuses_legacy_fact_and_original_blob_without_overwrite():
    store=FakeStore();svc=TradeJournalService(store)
    old,new=prior_and_incoming(same_source=True)
    put(svc,old);canonical=deepcopy(svc._facts());ledger=svc._ledger();sources=deepcopy(store.collection('trade_journal_sources').documents)
    preview=svc.preview_import(new)
    assert preview['can_import'] and preview['counts']['duplicate_fills']==1
    assert preview['counts']['new_sources']==preview['counts']['new_fills']==0
    expected_digest=preview['preview_digest']
    first=svc.import_batch(new,expected_digest)
    assert not first['idempotent'] and svc._facts()==canonical and svc._ledger()==ledger
    assert store.collection('trade_journal_sources').documents==sources
    receipt=svc._find('batches',{'batch_id':first['batch_id']})
    assert receipt['fill_ids']==['canonical-old']
    assert receipt['source_imports'][0]['metadata']==new['sources'][0]['metadata']
    assert receipt['source_imports'][0]['canonical_source_id']=='legacy-source'
    assert receipt['identity_reconciliations'][0]['match_type']=='source_sha_sheet_row'
    assert receipt['fill_source_refs'][0]['source_ref']=={'source_id':'legacy-source','sheet':'交割单','row':6}
    assert svc.preview_import(new)['preview_digest']==expected_digest
    assert svc.import_batch(new,expected_digest)['idempotent']


@pytest.mark.parametrize('field,value',[('price',11),('time','10:00:01'),('quantity',200),('fees',2)])
def test_source_row_anchor_material_conflict_never_creates_replacement(field,value):
    store=FakeStore();svc=TradeJournalService(store);old,new=prior_and_incoming(same_source=True);put(svc,old)
    # Keep amount/net coherent so the identity owner, not numeric validation,
    # detects the material difference.
    new['fills'][0][field]=value
    if field in ('price','quantity'):
        row=new['fills'][0];row['amount']=row['price']*row['quantity'];row['net_cash']=-row['amount']-row['fees']
    if field=='fees':new['fills'][0]['net_cash']=-1002
    before=deepcopy(store.writes);preview=svc.preview_import(new)
    assert not preview['can_import']
    assert 'source_row_identity_conflict' in {r['code'] for r in preview['conflicts']}
    with pytest.raises(TradeJournalError):svc.import_batch(new,preview['preview_digest'])
    assert store.writes==before and len(svc._facts()[0])==1


def test_different_source_same_price_quantity_without_broker_anchor_is_not_merged():
    svc=TradeJournalService(FakeStore());old,new=prior_and_incoming();put(svc,old)
    preview=svc.preview_import(new)
    assert preview['counts']['duplicate_fills']==0 and preview['counts']['new_fills']==1
    assert preview['identity_candidates']==[] and preview['identity_reconciliations']==[]


def test_leading_zero_broker_id_requires_explicit_frozen_confirmation_then_is_reusable():
    store=FakeStore();svc=TradeJournalService(store);old,new=prior_and_incoming(broker_alias=True);put(svc,old)
    canonical=deepcopy(svc._facts());ledger=svc._ledger();before=deepcopy(store.writes)
    preview=svc.preview_import(new)
    assert not preview['can_import'] and len(preview['identity_candidates'])==1 and store.writes==before
    assert preview['identity_candidates'][0]['existing_fact_id']=='canonical-old'
    assert 'broker_identity_confirmation_required' in {r['code'] for r in preview['conflicts']}
    new['identity_links']=[confirm(preview['identity_candidates'][0])]
    confirmed=svc.preview_import(new)
    assert confirmed['can_import'] and confirmed['counts']['duplicate_fills']==1 and confirmed['counts']['new_fills']==0
    result=svc.import_batch(new,confirmed['preview_digest'])
    assert svc._facts()==canonical and svc._ledger()==ledger
    receipt=svc._find('batches',{'batch_id':result['batch_id']})
    proof=receipt['identity_reconciliations'][0]
    assert proof['identity_confirmation'] and proof['incoming_broker_execution_id']=='00001234'
    assert proof['canonical_broker_execution_id']=='1234' and proof['incoming_source_sha256']==new['sources'][0]['sha256']
    assert svc.import_batch(new,confirmed['preview_digest'])['idempotent']
    without_confirmation=deepcopy(new);without_confirmation.pop('identity_links')
    assert svc.preview_import(without_confirmation)['can_import']
    # A later independently archived source with this exact confirmed broker
    # alias may reuse it. It is never inferred merely from equal money/quantity.
    later=deepcopy(without_confirmation);later['sources']=[source('third','third raw export')];later['fills'][0]['source_ref']['source_id']='third'
    assert svc.preview_import(later)['counts']['duplicate_fills']==1


@pytest.mark.parametrize('changes',[{'expected_existing_hash':'0'*64},{'existing_fact_id':'unknown'},{'identity_confirmation':False},{'reason':''},{'incoming_source_ref':{'source_id':'new-file-source','sheet':'交割单','row':99}}])
def test_invalid_or_stale_confirmation_cannot_grant_merge(changes):
    svc=TradeJournalService(FakeStore());old,new=prior_and_incoming(broker_alias=True);put(svc,old)
    candidate=svc.preview_import(new)['identity_candidates'][0]
    new['identity_links']=[confirm(candidate,**changes)]
    try:preview=svc.preview_import(new)
    except TradeJournalError:return
    assert not preview['can_import']
    with pytest.raises(TradeJournalError):svc.import_batch(new,preview['preview_digest'])


def test_same_broker_number_with_material_cash_or_time_difference_is_not_confirmable():
    svc=TradeJournalService(FakeStore());old,new=prior_and_incoming(broker_alias=True);put(svc,old)
    candidate=svc.preview_import(new)['identity_candidates'][0]
    new['identity_links']=[confirm(candidate)]
    new['fills'][0].update(fees=2,net_cash=-1002)
    preview=svc.preview_import(new)
    assert not preview['can_import'] and 'broker_identity_fact_conflict' in {r['code'] for r in preview['conflicts']}


def test_source_selection_is_deterministic_and_prefers_fact_referenced_original():
    svc=TradeJournalService(FakeStore());old,new=prior_and_incoming(same_source=True)
    normalized_old=svc._normalize(old);normalized_new=svc._normalize(new)
    unused=deepcopy(normalized_old['sources'][0]);unused['source_id']='aaa-unused'
    kwargs={'existing_sources':[unused,*normalized_old['sources']],'existing_fills':normalized_old['fills'],'existing_financial':[],'committed_batches':[]}
    result=reconcile_import(normalized_new,**kwargs)
    assert result['data']['sources'][0]['source_id']=='legacy-source'
    kwargs['existing_sources'].reverse()
    assert reconcile_import(normalized_new,**kwargs)['data']['sources'][0]['source_id']=='legacy-source'


def test_original_ipo_link_is_preserved_when_unmapped_in_raw_statement():
    svc=TradeJournalService(FakeStore())
    event={'code':'787825','date':'2026-10-19','time':'10:00:00','kind':'ipo_payment','quantity':500,'net_cash':-2000,'linked_ticker':'688825.SH','source_ref':{'source_id':'old','sheet':'原表','row':2}}
    old=bundle([],financial=[event],sources=[source('old','same original')]);put(svc,old)
    new=deepcopy(old);new['sources']=[source('new','same original')];new['financial_events'][0]['source_ref']['source_id']='new';new['financial_events'][0].pop('linked_ticker')
    preview=svc.preview_import(new)
    assert preview['can_import'] and preview['counts']['duplicate_financial_events']==1
    put(svc,new)
    assert svc._facts()[1][0]['linked_ticker']=='688825.SH'


def test_json_record_id_precedes_reused_page_row_for_different_dates():
    svc=TradeJournalService(FakeStore())
    rows=[fill('legacy-a','2026-10-19','buy',100,10,100,source_ref={'source_id':'old','sheet':1,'row':2,'record_id':'page-item-a'}),
        fill('legacy-b','2026-10-20','buy',100,11,200,source_ref={'source_id':'old','sheet':1,'row':2,'record_id':'page-item-b'})]
    old=bundle(rows,sources=[source('old','original JSON')]);put(svc,old)
    assert svc.preview_import(old)['can_import']
    new=deepcopy(old);new['sources']=[source('new','original JSON')]
    for i,row in enumerate(new['fills']):row.update(fill_id=f'new-{i}');row['source_ref']['source_id']='new'
    preview=svc.preview_import(new)
    assert preview['can_import'] and preview['counts']['duplicate_fills']==2
    assert {r['match_type'] for r in preview['identity_reconciliations']}=={'source_sha_record_id'}


@pytest.mark.parametrize('owner', ['reviews','tasks','allocations','execution_matches'])
def test_import_cannot_orphan_any_persisted_episode_owner(owner):
    from shilun.trade_journal.reviews import COLLECTION as REVIEWS
    from shilun.trade_journal.tasks import COLLECTION as TASKS
    from shilun.trade_journal.allocations import COLLECTION as ALLOCATIONS
    from shilun.trade_journal.execution_matches import COLLECTION as MATCHES
    names={'reviews':REVIEWS,'tasks':TASKS,'allocations':ALLOCATIONS,'execution_matches':MATCHES}
    store=FakeStore();svc=TradeJournalService(store)
    sale=fill('historical-sale','2026-10-20','sell',100,12,0)
    old=bundle([sale]);put(svc,old);episode=svc.list_episodes()['items'][0]['episode_id']
    # Minimal persisted owner reference: the import guard only projects this
    # field; each owner's own mutation invariants have separate domain tests.
    store.collection(names[owner]).documents.append({'episode_id':episode,'revision':1})
    new=bundle([fill('earlier-buy','2026-10-19','buy',100,10,100),sale])
    before=deepcopy(store.writes);preview=svc.preview_import(new)
    conflict=next(r for r in preview['conflicts'] if r['code']=='episode_identity_changed')
    assert conflict['id']==episode and owner in conflict['reference_owners']
    with pytest.raises(TradeJournalError):svc.import_batch(new,preview['preview_digest'])
    assert before==store.writes and svc.get_episode(episode)['episode']['episode_id']==episode


def test_real_confirmed_review_alone_prevents_unknown_opening_episode_disappearing():
    from shilun.trade_journal.reviews import JournalReviewService
    store=FakeStore();svc=TradeJournalService(store)
    sale=fill('old-sale','2026-10-20','sell',100,12,0);put(svc,bundle([sale]))
    episode=svc.list_episodes()['items'][0]['episode_id'];reviews=JournalReviewService(store,journal=svc)
    context=reviews.get_review(episode)
    saved=reviews.save_review(episode,{'expected_revision':0,'expected_fact_digest':context['fact_digest'],'state':'confirmed','summary':'用户确认旧交易过程，期初成本无法恢复','revision_reason':'按原记录复盘'})
    assert saved['record']['revision']==1 and not svc._all('decisions')
    incoming=bundle([fill('found-earlier-buy','2026-10-19','buy',100,10,100),sale])
    preview=svc.preview_import(incoming)
    assert not preview['can_import'] and any(r['code']=='episode_identity_changed' and r['reference_owners']==['reviews'] for r in preview['conflicts'])
    assert reviews.get_review(episode)['current_confirmed']


def test_actual_historical_archive_and_recent_raw_file_keep_canonical_fifo_and_pnl():
    root=Path(__file__).resolve().parents[1]
    fixture=Path('/private/tmp/shilun-trade-journal-import.json')
    old_file=root/'outputs/01a14866-b9df-7663-a58b-dccf6cb44cd5/东财交割单_20260701-20261017.xlsx'
    recent_file=root/'outputs/01a1549c-dbe3-7b11-b994-ec46badd33e8/东财交割单_20261019.xlsx'
    if not all(p.is_file() for p in (fixture,old_file,recent_file)):pytest.skip('private source material is not distributed')
    store=FakeStore();svc=TradeJournalService(store);original=json.loads(fixture.read_text());put(svc,original)
    original_preview=svc.preview_import(original)
    assert original_preview['can_import'] and original_preview['counts']['duplicate_fills']==495
    assert svc.import_batch(original,original_preview['preview_digest'])['idempotent']
    canonical=deepcopy(svc._facts());ledger=svc._ledger();source_count=len(store.collection('trade_journal_sources').documents)
    for path in (old_file,recent_file):
        content=base64.b64encode(path.read_bytes()).decode();mapping=inspect_file(path.name,content)['suggested_mapping']
        for item in mapping['ignored_sheets']:item['reason']='已核此表为汇总/派生展示，只取实际原始表'
        if path==recent_file:mapping['ignored_rows']=[{'sheet':'成交与委托','row':n,'reason':'已逐行核对该行属于委托或说明子表，不作为真实成交'} for n in range(14,26)]
        parsed=parse_file(path.name,content,mapping=mapping);assert parsed['can_preview'],parsed['issues']
        incoming=parsed['payload'];preview=svc.preview_import(incoming)
        if path==old_file:
            assert preview['can_import'] and (preview['counts']['duplicate_fills'],preview['counts']['duplicate_financial_events'],preview['counts']['new_sources'])==(462,11,0)
        else:
            assert not preview['can_import'] and len(preview['identity_candidates'])==3
            incoming['identity_links']=[confirm(c) for c in preview['identity_candidates']]
            preview=svc.preview_import(incoming)
            assert preview['can_import'] and preview['counts']['duplicate_fills']==5 and preview['counts']['new_fills']==0
        put(svc,incoming)
        assert svc._facts()==canonical and svc._ledger()==ledger
        assert svc.import_batch(incoming,preview['preview_digest'])['idempotent']
        if path==old_file:assert len(store.collection('trade_journal_sources').documents)==source_count
    assert len(store.collection('trade_journal_sources').documents)==source_count+1
