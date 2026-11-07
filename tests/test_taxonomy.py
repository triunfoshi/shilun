from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
import pytest

from shilun.market.taxonomy import (VERSION, CATALOG, MEMBERS, SNAPSHOTS, normalize_catalog,
    normalize_members, attach_hierarchy, stock_classifications, classify_stocks, sync_taxonomy)
from shilun.review.cycle_diagnostics import require_cycle_inputs, context_cycle
from shilun.review.engine import classify_sector_cycle

DAY = '2026-09-21'


def board(code='BK1128.DC', name='CPO概念', kind='概念板块', level=None):
    return dict(ts_code=code, name=name, idx_type=kind, level=level, trade_date='20260921')


def test_full_catalog_keeps_large_concepts_and_three_industry_levels():
    raw = [board(), board('BK1.DC', '电子', '行业板块', '东财一级行业'),
           board('BK2.DC', '电子Ⅱ', '行业板块', '东财二级行业'),
           board('BK3.DC', '元件', '行业板块', '东财三级行业'),
           board('BK4.DC', '融资融券'), board('BK5.DC', '上海板块', '地域板块', float('nan'))]
    rows = normalize_catalog(raw, DAY)
    assert len(rows) == 6
    assert [r['code'] for r in rows if r['research_eligible']] == ['BK1128.DC', 'BK3.DC']
    assert rows[2]['industry_level'] == 'l2'
    assert rows[-1]['source_label'] == '东方财富地域'
    assert rows[-1]['granularity_label'] == '地域'


@pytest.mark.parametrize('bad', ['date', 'duplicate', 'truncated', 'foreign_source'])
def test_catalog_rejects_cross_date_duplicates_truncation_and_other_sources(bad):
    rows = [board()]
    if bad == 'date': rows[0]['trade_date'] = '20260918'
    if bad == 'duplicate': rows *= 2
    if bad == 'truncated': rows *= 5000
    if bad == 'foreign_source': rows[0]['ts_code'] = '886033.TI'
    with pytest.raises(ValueError): normalize_catalog(rows, DAY)


def test_members_require_exact_requested_date_and_no_duplicates():
    row = dict(ts_code='BK1128.DC', con_code='600001.SH', trade_date='20260921', name='甲')
    assert normalize_members([row], 'BK1128.DC', DAY)[0]['con_code'] == '600001.SH'
    for raw in ([row, row], [{**row, 'trade_date': '20260922'}], [row]*5000):
        with pytest.raises(ValueError): normalize_members(raw, 'BK1128.DC', DAY)


def test_parent_and_primary_industry_ambiguity_is_never_guessed():
    def r(code, level, tickers):
        return dict(code=code, name=code, kind='industry', industry_level=level,
                    members=[{'con_code': t} for t in tickers])
    rows = [r('parent', 'l1', ['A','B']), r('middle1', 'l2', ['A','B']),
            r('middle2', 'l2', ['A','B']), r('leaf1', 'l3', ['A']), r('leaf2', 'l3', ['A'])]
    attach_hierarchy(rows)
    assert rows[-1]['industry_parents'] == [{'code':'parent','name':'parent','industry_level':'l1'}]
    assert rows[-1]['parent_status'] == 'unresolved'
    classification = stock_classifications({'date': DAY, 'rows': rows})['A']
    assert classification['industry_code'] is None
    assert classification['classification_status'] == 'industry_conflict'


def test_missing_snapshot_clears_old_industry_instead_of_silent_fallback():
    rows = classify_stocks(object(), DAY, [{'ts_code':'A', 'industry':'旧申万行业'}], snapshot={})
    assert rows[0]['industry'] == '分类待补齐'
    assert rows[0]['raw_industry'] == '旧申万行业'
    assert rows[0]['classification_status'] == 'taxonomy_missing'


def test_failed_member_request_cannot_publish_half_catalog():
    collections = {k: Mock() for k in (CATALOG, MEMBERS, SNAPSHOTS)}
    for col in collections.values(): col.find_one.return_value = None
    store = SimpleNamespace(collection=collections.__getitem__, find_trade_calendar=lambda **kw: [{'cal_date':'20260921','is_open':1}])
    client = Mock()
    client._call_api.side_effect = [pd.DataFrame([board()]), ValueError('upstream unavailable')]
    with pytest.raises(ValueError, match='暂未发布'): sync_taxonomy(store, DAY, client=client)
    collections[SNAPSHOTS].replace_one.assert_not_called()
    assert collections[CATALOG].update_one.call_args.args[1]['$set']['missing_members'][0]['code'] == 'BK1128.DC'


def test_holiday_never_enters_taxonomy_catalog():
    store = Mock()
    store.collection.return_value.find_one.return_value = None
    store.find_trade_calendar.return_value = [{'cal_date':'20260920','is_open':0}]
    client = Mock()
    with pytest.raises(ValueError, match='交易日'): sync_taxonomy(store, '2026-09-20', client=client)
    client._call_api.assert_not_called()


def test_cycle_diagnostics_separate_data_mapping_calculation_and_rule_gaps():
    inputs = dict(first_board_count=0, max_board=0, board_count=0, broken_count=0, up_ratio=.6, net_flow=1)
    no_boards = classify_sector_cycle(**inputs)
    assert no_boards['diagnostic_status'] == 'no_limit_structure'
    assert classify_sector_cycle(**{**inputs, 'net_flow':None})['diagnostic_status'] == 'data_missing'
    assert classify_sector_cycle(**{**inputs,'first_board_count':1,'max_board':1,'board_count':1})['diagnostic_status'] == 'rule_unmatched'
    missing = require_cycle_inputs(no_boards, ['previous_limit_pool'])
    assert missing['code'] == 'pending' and missing['missing_inputs'] == ['previous_limit_pool']
    assert context_cycle({}, {})['diagnostic_status'] == 'stock_unmapped'
    assert context_cycle({'industry_code':'BK3.DC'}, {})['diagnostic_status'] == 'calculation_missing'


def test_official_preclose_resolves_resume_or_adjustment_gap_in_breadth():
    from shilun.review.service import DailyReviewService
    rows = DailyReviewService(object())._sectors(
        current_bars={'A': {'close':11,'pre_close':10,'amount':100}}, previous_bars={},
        moneyflow={'A': {'net_mf_amount':100}}, daily_basic={},
        basic={'A': {'name':'甲','sector':'元件','industry_code':'BK3.DC','taxonomy_date':DAY}},
        current_limits=[], previous_limits=[])
    assert rows[0]['up_ratio'] == 1
    assert rows[0]['cycle']['diagnostic_status'] == 'no_limit_structure'
    assert rows[0]['cycle']['missing_inputs'] == []


def test_repaired_same_day_industry_refreshes_display_without_changing_frozen_selection():
    from unittest.mock import patch
    from shilun.market.taxonomy import reconcile_dated_industries
    rows = [{'ticker':'600001.SH', 'sector_name':'分类待补齐', 'industry':'分类待补齐',
             'status':'qualified', 'qualified_at':'2026-09-17', 'amount_yi':26.7,
             'cycle':{'drawdown':-.2}}]
    frozen = deepcopy(rows)
    mapping = [{**rows[0], 'industry':'元件', 'industry_code':'BK3.DC',
                'industry_source':'东方财富', 'taxonomy_date':DAY, 'classification_status':'ready',
                'concepts':[{'name':'不得混入事件题材'}]}]
    with patch('shilun.market.taxonomy.classify_stocks', return_value=mapping) as classified:
        result = reconcile_dated_industries(object(), DAY, rows, sector_field='sector_name')
    assert classified.call_args.args[1] == DAY
    assert classified.call_args.kwargs == {'industry_only':True}
    assert result[0]['sector_name'] == '元件'
    assert result[0]['sector_code'] == 'BK3.DC'
    assert result[0]['industry'] == '元件' and result[0]['taxonomy_date'] == DAY
    assert result[0]['qualified_at'] == rows[0]['qualified_at']
    assert result[0]['cycle'] == rows[0]['cycle']
    assert result[0]['amount_yi'] == rows[0]['amount_yi']
    assert 'concepts' not in result[0]
    assert rows == frozen


def test_missing_historical_industry_never_uses_current_raw_stock_industry():
    from unittest.mock import patch
    from shilun.market.taxonomy import reconcile_dated_industries
    rows = [{'ticker':'600001.SH', 'industry':'今天的证券目录原始行业'}]
    unknown = [{'ticker':'600001.SH', 'industry':'分类待补齐',
                'taxonomy_date':DAY, 'classification_status':'taxonomy_missing'}]
    with patch('shilun.market.taxonomy.classify_stocks', return_value=unknown):
        result = reconcile_dated_industries(object(), DAY, rows)
    assert result[0]['industry'] == '分类待补齐'
    assert result[0]['classification_status'] == 'taxonomy_missing'
    assert result[0]['taxonomy_date'] == DAY


@pytest.mark.parametrize('old_version,old_date,fresh_status,keep', [
    (VERSION, DAY, 'taxonomy_missing', True),
    ('legacy', DAY, 'taxonomy_missing', False),
    (VERSION, '2026-09-18', 'taxonomy_missing', False),
    (VERSION, DAY, 'industry_conflict', False),
    (VERSION, DAY, 'stock_unmapped', False),
])
def test_transient_manifest_gap_retains_only_verified_same_day_canonical_industry(old_version, old_date, fresh_status, keep):
    from unittest.mock import patch
    from shilun.market.taxonomy import reconcile_dated_industries
    row = {'ticker':'600001.SH', 'industry':'元件', 'industry_code':'BK3.DC',
           'taxonomy_version':old_version, 'taxonomy_date':old_date, 'classification_status':'ready'}
    unknown = {'ticker':row['ticker'], 'industry':'分类待补齐', 'industry_code':None,
               'taxonomy_version':VERSION, 'taxonomy_date':DAY, 'classification_status':fresh_status}
    with patch('shilun.market.taxonomy.classify_stocks', return_value=[unknown]):
        result = reconcile_dated_industries(object(), DAY, [row], sector_field='sector_name')[0]
    assert result['industry'] == ('元件' if keep else '分类待补齐')
    assert result['sector_name'] == result['industry']
    assert result['sector_code'] == ('BK3.DC' if keep else None)
    assert result['classification_status'] == ('ready' if keep else fresh_status)
