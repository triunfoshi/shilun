import pytest

from shilun.trade_journal.definition_refs import resolve_definition_references
from shilun.trade_journal.mode_cards import ModeCardService
from shilun.trade_journal.service import TradeJournalService, TradeJournalError
from test_trade_journal_mode_cards import Store as FakeStore


def test_dynamic_definition_requires_exact_version_and_same_category():
    store=FakeStore();owner=ModeCardService(store)
    first=owner.save_card({'name':'原实验模式'})
    mode_id=first['mode_id'];ref={'id':mode_id,'rule_version':'mode-card-v1'}
    owner.save_card({'mode_id':mode_id,'name':'改名后的模式','status':'retired','expected_revision':1,'revision_reason':'保留旧定义'})
    assert not resolve_definition_references(store,[mode_id])['valid']
    assert not resolve_definition_references(store,[{**ref,'category':'action'}])['valid']
    assert not resolve_definition_references(store,[ref],expected_category='trigger')['valid']
    result=resolve_definition_references(store,[ref])
    assert result['valid'] and result['resolved'][0]['label']=='原实验模式'


def test_conflicting_versions_are_rejected_independently_of_array_order():
    store=FakeStore();owner=ModeCardService(store)
    first=owner.save_card({'name':'第一版'})
    owner.save_card({'mode_id':first['mode_id'],'name':'第二版','expected_revision':1,'revision_reason':'研究修订'})
    refs=[{'id':first['mode_id'],'rule_version':f'mode-card-v{n}'} for n in (1,2)]
    for values in (refs,list(reversed(refs))):
        result=resolve_definition_references(store,values)
        assert not result['valid'] and any(e['code']=='ambiguous_definition_version' for e in result['errors'])


def test_plan_freezes_old_card_version_and_unknown_remains_missing():
    store=FakeStore();owner=ModeCardService(store)
    first=owner.save_card({'name':'第一版'})
    owner.save_card({'mode_id':first['mode_id'],'name':'第二版','expected_revision':1,'revision_reason':'研究修订'})
    journal=TradeJournalService(store)
    result=journal.freeze_plan({'ticker':'000636.SZ','mode_id':first['mode_id'],
        'definition_refs':[{'id':first['mode_id'],'rule_version':'mode-card-v1'}],'invalidation':'未设'})
    assert result['record']['definition_snapshots'][0]['rule_version']=='mode-card-v1'
    assert 'invalidation' in result['missing_fields']
    assert not result['ex_ante_verifiable']
    with pytest.raises(TradeJournalError,match='确切rule_version'):
        journal.freeze_plan({'ticker':'000636.SZ','mode_id':first['mode_id']})
    with pytest.raises(TradeJournalError,match='列表'):
        journal.freeze_plan({'ticker':'000636.SZ','definition_refs':{'id':'mode.ma5_trend'}})
