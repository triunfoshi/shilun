"""Attach reviewed historical notes by explicit fill selectors, idempotently."""
import argparse
import hashlib
import json
from pathlib import Path

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.trade_journal.indexes import ensure_journal_indexes
from shilun.trade_journal.service import TradeJournalService, TradeJournalError, COLLECTIONS
from shilun.jobs.trade_journal_import_job import protected_state_hashes


def _identity(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def prepare_notes(service, manifest):
    """Resolve every note before any write; ambiguous matches stop the batch."""
    episodes=service.list_episodes(limit=1000)['items']
    resolved=[]
    keys=[item.get('key') for item in manifest.get('decisions',[])]
    if None in keys or len(keys)!=len(set(keys)):
        raise TradeJournalError('补记key必须唯一且明确')
    for link in manifest.get('links',[]):
        if link.get('from_key') not in keys or link.get('to_key') not in keys:
            raise TradeJournalError('跨票关联须引用本次明确补记key')
        if link['from_key'] == link['to_key']:
            raise TradeJournalError('跨票关联不能引用同一补记key')
    for item in manifest.get('decisions',[]):
        match=item['episode']
        candidates=[e for e in episodes if all(e.get(k)==v for k,v in match.items())]
        if len(candidates)!=1:
            raise TradeJournalError(f"补记{item['key']}对应库存轮不是唯一匹配")
        episode=candidates[0]
        detail=service.get_episode(episode['episode_id'])
        selector=item.get('fills',{})
        selected=[f for f in detail['fills'] if all(
            f.get(k)==v for k,v in selector.items() if k!='after_time')]
        if 'after_time' in selector:
            selected=[f for f in selected if f['time']>selector['after_time']]
        expected=item.get('expected_fill_count')
        if not selected or expected is not None and len(selected)!=expected:
            raise TradeJournalError(f"补记{item['key']}的成交范围不符合已审查数量")
        decision_id='archive-note-'+_identity([manifest['manifest_id'],item['key']])[:32]
        fingerprint=_identity(item)
        history=service.store.collection(COLLECTIONS['decisions']).find({'decision_id':decision_id},{'_id':0})
        previous=next((r for r in history if (r.get('note') or {}).get('seed_fingerprint')==fingerprint),None)
        if previous is None and service.store.collection(COLLECTIONS['decisions']).find_one({'decision_id':decision_id}):
            raise TradeJournalError('已存在不同内容的补记种子；请人工追加版本，不覆盖用户修订')
        body={**item['payload'],'decision_id':decision_id,'fill_ids':[f['fill_id'] for f in selected],
            'evidence_class':'retrospective_recall',
            'note':{**(item['payload'].get('note') or {}),'seed_manifest':manifest['manifest_id'],'seed_fingerprint':fingerprint}}
        resolved.append({'key':item['key'],'episode_id':episode['episode_id'],'decision_id':decision_id,
            'payload':body,'already_seeded':previous is not None})
    return resolved


def apply_notes(service, manifest, *, apply=False):
    resolved=prepare_notes(service,manifest)
    source_payload={'account_alias':manifest.get('account_alias','primary'),'sources':manifest.get('sources',[]),'fills':[],'financial_events':[]}
    preview=service.preview_import(source_payload)
    if not preview['can_import']:
        raise TradeJournalError('补记来源预览存在冲突')
    result={'manifest_id':manifest['manifest_id'],'decisions':len(resolved),
        'new_decisions':sum(not r['already_seeded'] for r in resolved),'links':len(manifest.get('links',[])),
        'source_preview':preview,'applied':apply}
    if not apply:
        return result
    ensure_journal_indexes(service.store)
    service.import_batch(source_payload,preview['preview_digest'])
    for item in resolved:
        if not item['already_seeded']:
            service.append_decision(item['episode_id'],item['payload'])
    by_key={r['key']:r for r in resolved}
    created_links=0
    for link in manifest.get('links',[]):
        link_id='archive-link-'+_identity([manifest['manifest_id'],link])[:32]
        if service.store.collection(COLLECTIONS['links']).find_one({'link_id':link_id}):
            continue
        body={**link,'link_id':link_id,'from_decision_id':by_key[link['from_key']]['decision_id'],
            'to_decision_id':by_key[link['to_key']]['decision_id']}
        service.link_decisions(body);created_links+=1
    result['created_links']=created_links
    result['summary']=service.get_summary()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--expected-manifest-sha256')
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args()
    raw=args.manifest.read_bytes()
    manifest_sha=hashlib.sha256(raw).hexdigest()
    if args.apply and args.expected_manifest_sha256!=manifest_sha:
        parser.error('写入必须提供已审查manifest的--expected-manifest-sha256')
    config=load_config()
    if not config.mongo_uri:
        parser.error('未配置ECS Mongo连接；不回退本地存储')
    store=MongoSnapshotStore(config.mongo_uri,config.mongo_db,socket_timeout_ms=30000,initialize_indexes=False)
    try:
        service=TradeJournalService(store)
        before=protected_state_hashes(store) if args.apply else None
        result=apply_notes(service,json.loads(raw),apply=args.apply)
        result['manifest_sha256']=manifest_sha
        if args.apply:
            after=protected_state_hashes(store)
            result['production_collections_unchanged']=before==after
            result['protected_before']=before;result['protected_after']=after
        if args.receipt:
            args.receipt.parent.mkdir(parents=True,exist_ok=True)
            args.receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False))
    finally:
        store.close()


if __name__=='__main__':
    main()
