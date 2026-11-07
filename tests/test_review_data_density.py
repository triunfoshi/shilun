"""Regressions for dated prerequisites, dropped returns and bounded snapshots."""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import HTTPException

from shilun.review.index_sync import sync_index_history


def test_index_repair_rejects_stale_or_wrong_ticker_and_skips_ready():
    store, client = MagicMock(), MagicMock()
    store.collection.return_value.find_one.return_value = {'date': '2026-09-14'}
    frame = pd.DataFrame([{'ticker': '000001.SH', 'date': pd.Timestamp('2026-09-15'), 'close': 10}])
    client.fetch_index_daily.return_value = frame
    assert sync_index_history(store, '2026-09-15', ['000001.SH'], client=client)[0]['status'] == 'ok'
    store.upsert_daily_bars.reset_mock()
    for bad in [frame.assign(date=pd.Timestamp('2026-09-14')), frame.assign(ticker='000300.SH')]:
        client.fetch_index_daily.return_value = bad
        assert sync_index_history(store, '2026-09-15', ['000001.SH'], client=client)[0]['status'] == 'unavailable'
        store.upsert_daily_bars.assert_not_called()
    store.collection.return_value.find_one.return_value = {'date': '2026-09-15'}
    client.fetch_index_daily.reset_mock()
    assert sync_index_history(store, '2026-09-15', ['000001.SH'], client=client) == []
    client.fetch_index_daily.assert_not_called()


def test_ladder_legacy_price_uses_adjusted_dated_reference_for_failed_stock():
    from shilun.review.board_ladder import ladder_payload
    data = MagicMock()
    data.sessions.return_value = ['2026-09-14', '2026-09-15']
    data.pools.return_value = {'2026-09-14': {'stocks': [dict(ticker='000823.SZ', code='000823', name='超声电子', boards=3)]},
                              '2026-09-15': {'stocks': []}}
    collections = {name: MagicMock() for name in ['trade_calendar', 'market_daily_bars', 'daily_price_limits', 'review_ladder_sources', 'review_daily_suspensions']}
    data.store.collection.side_effect = collections.__getitem__
    collections['trade_calendar'].find_one.return_value = None
    collections['review_ladder_sources'].find_one.return_value = None
    collections['market_daily_bars'].find.return_value = [{'date': '2026-09-15', 'ticker': '000823.SZ', 'close': 23.9}]
    collections['daily_price_limits'].find.return_value = [{'trade_date': '20260915', 'ticker': '000823.SZ', 'pre_close': 22.63}]
    with patch('shilun.review.board_ladder.today', return_value='2026-09-15'):
        result = ladder_payload(data, '2026-09-15')
    assert result['rows'][0]['observed'] == 1
    assert result['rows'][0]['today'] == []
    assert result['rows'][0]['yesterday'][0]['pct'] == pytest.approx(5.612019)


def test_ladder_full_day_suspension_has_no_fabricated_return():
    from shilun.review.board_ladder import build_ladder
    previous = dict(ticker='603400.SH', code='603400', name='华之杰', boards=1)
    result = build_ladder(target='2026-09-15', previous_date='2026-09-14', next_date=None,
        pools={'2026-09-14': {'stocks': [previous]}, '2026-09-15': {'stocks': []}},
        bars={('2026-09-15', '603400.SH'): {'suspended': True}})
    row = result['rows'][0]['yesterday'][0]
    assert row['quote_status'] == 'suspended' and row['pct'] is None
    assert result['rows'][0]['observed'] == 0


def test_missing_candidate_cache_keeps_expected_http_400():
    from shilun.api import market_candidates
    store = MagicMock()
    with patch('shilun.common.db.MongoSnapshotStore', return_value=store), \
         patch('shilun.api.load_config', return_value=MagicMock(mongo_uri='test', mongo_db='test')), \
         patch('shilun.api._load_sector_cache', return_value=None):
        with pytest.raises(HTTPException) as error:
            market_candidates(date='2026-09-15', benchmark_ticker='000001.SH')
    assert error.value.status_code == 400
    assert '补齐数据并计算' in error.value.detail
    store.close.assert_called_once()


def test_topic_details_preserve_all_members_without_bloating_main_snapshot():
    from shilun.review.topic_details import split_topic_details
    from shilun.api.review import get_topic_detail
    full = [dict(code=str(i), name=str(i), front_support={'rows': [{'ticker': f'{i}-{j}'} for j in range(100)], 'front_count': 7}) for i in range(5)]
    payload = {'analysis_date': '2026-09-15', 'sentiment': {'theme_evidence': {'themes': full}}}
    store = MagicMock()
    split_topic_details(store, payload)
    writes = store.collection.return_value.bulk_write.call_args.args[0]
    assert len(writes) == 2
    assert len(full[0]['front_support']['rows']) == 100
    assert 'rows' not in full[3]['front_support']
    assert full[3]['front_support']['front_count'] == 7
    archived = writes[0]._doc
    assert len(archived['theme']['front_support']['rows']) == 100
    assert archived['_id'] == full[3]['detail_id']
    store.collection.return_value.find_one.return_value = archived
    with patch('shilun.api.review._open_store', return_value=store):
        result = get_topic_detail(full[3]['detail_id'])
    assert len(result['front_support']['rows']) == 100
    store.close.assert_called_once()


def test_expectation_fields_keep_yesterday_tags_and_today_breaks_seal():
    from shilun.review.service import DailyReviewService
    dates = ['2026-09-14', '2026-09-15']
    store = MagicMock()
    def collection(name):
        obj = MagicMock()
        obj.find.return_value = []
        obj.find_one.return_value = ({'date': dates[0], 'rows': [{'ts_code': 'A', 'l3_name': '印制电路板'}]}
            if name == 'review_industry_members' else {'date': dates[0], 'reasons': {'A': 'PCB+覆铜板'}, 'source': 'dated'})
        return obj
    store.collection.side_effect = collection
    bars = {t: [dict(date=dates[0], open=10, high=11, low=10, close=11, amount=100),
                dict(date=dates[1], open=11, high=12.1, low=10, close=close)] for t, close in [('A', 11.5), ('B', 12.1)]}
    yesterday = [dict(ticker=t, name=t, consecutive_count=3, broken_count=0) for t in bars]
    current = [dict(ticker='B', consecutive_count=4, broken_count=2, last_seal_time='145001')]
    with patch('shilun.market.taxonomy.classify_stocks', return_value=[{'ticker':'A','industry':'印制电路板','industry_code':'BK1340.DC','concepts':[{'code':'BK0877.DC','name':'PCB'}]}]), patch('shilun.review.board_data.BoardResearchData.pools', return_value={dates[1]: {'stocks': [dict(ticker='A', break_count=3, last_seal_time='140001')]}}):
        out = DailyReviewService(store)._expectations(dates=dates, analysis_date=dates[1], previous_date=dates[0],
            bars_by_ticker=bars, limit_rows_by_date={dates[0]: yesterday, dates[1]: current}, current_limits=current)
    rows = {r['ticker']: r for r in out['observations']}
    assert rows['A']['industry_name'] == '印制电路板'
    assert rows['A']['topics'] == ['PCB', '覆铜板']
    assert rows['A']['limit_reason'] == 'PCB+覆铜板'
    from shilun.review.event_theme_taxonomy import topic_code
    assert rows['A']['topic_ids'] == [topic_code('PCB'), topic_code('覆铜板')]
    assert rows['A']['consecutive_count'] == 3
    assert rows['A']['low_premium'] == pytest.approx(10 / 11 - 1)
    assert rows['A']['today_broken_count'] == 3 and rows['A']['today_last_seal_time'] is None
    assert rows['B']['today_broken_count'] == 2 and rows['B']['today_last_seal_time'] == '145001'


def test_independent_catalog_uses_all_members_without_index_shortlist():
    from shilun.review.topic_perspectives import sync_perspectives
    days = ['2026-09-09', '2026-09-10', '2026-09-11', '2026-09-14', '2026-09-15']
    rows = [dict(code=c, name=c, type='N', members=[{'con_code':str(i)} for i in range(5)],
                 member_count=5, research_eligible=True, historical_reconstruction=False)
            for c in ['BK1.DC', 'BK2.DC']]
    collections = {n: MagicMock() for n in ['market_daily_bars','review_topic_perspectives']}
    collections['market_daily_bars'].find.return_value = [dict(ticker=str(i),date=d,close=11,pre_close=10) for d in days[-2:] for i in range(5)]
    store = MagicMock(); store.collection.side_effect = collections.__getitem__
    archive = {'rows':rows,'version':'eastmoney-taxonomy-v1','revision':'r1','membership_note':'dated'}
    with patch('shilun.review.topic_perspectives.BoardResearchData') as data, \
         patch('shilun.market.taxonomy.sync_taxonomy', return_value=archive), \
         patch('shilun.review.topic_perspectives.short_list') as stale_screen:
        data.return_value.sessions.return_value=days; data.return_value.pools.return_value={}
        out=sync_perspectives(store,days[-1])
    stale_screen.assert_not_called()
    assert out['screening_mode']=='member_direct'
    assert {p['code'] for p in out['perspectives']}=={'BK1.DC','BK2.DC'}
    assert all(p['return_median']==pytest.approx(.1) for p in out['perspectives'])
    assert out['taxonomy_revision']=='r1'
