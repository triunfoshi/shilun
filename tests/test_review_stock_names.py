"""Old topic archives and current taxonomy use different stock-name fields."""
from copy import deepcopy
from unittest.mock import Mock, patch

from shilun.api.review import get_topic_detail
from shilun.review.service import DailyReviewService, PERSPECTIVE_VERSION
from shilun.review.theme_evidence import front_support
from shilun.review.topic_details import refresh_topic_names


def test_current_and_legacy_constituent_names_survive_front_support():
    result = front_support([], {}, {}, history_rows=[], members={
        '000017.SZ': {'name': '深中华A'},
        '600001.SH': {'con_name': '旧名称'},
        '600002.SH': {},
    })
    assert {r['ticker']: r['name'] for r in result['rows']} == {
        '000017.SZ': '深中华A', '600001.SH': '旧名称', '600002.SH': '600002.SH',
    }


def test_archive_names_are_batched_without_rewriting_history_or_missing_prices():
    rows = [
        {'ticker': '000017.SZ', 'name': '000017.SZ', 'today_return': None},
        {'ticker': '600001.SH', 'name': '历史旧名称', 'today_return': .03},
        {'ticker': '600002.SH', 'name': '600002'},
        {'ticker': '600003.SH', 'name': ''},
    ]
    themes = [{'front_support': {'rows': rows}}, {'front_support': {'rows': [dict(rows[0])]}}, {}]
    store = Mock()
    store.collection.return_value.find.return_value = [
        {'ts_code': '000017.SZ', 'name': '深中华A'},
        {'ts_code': '600001.SH', 'name': '现名称'},
        {'ticker': '600002.SH', 'name': '乙'},
    ]
    refresh_topic_names(store, themes)
    assert rows[0] == {'ticker': '000017.SZ', 'name': '深中华A', 'today_return': None}
    assert rows[1]['name'] == '历史旧名称'
    assert rows[2]['name'] == '乙'
    assert rows[3]['name'] == ''
    assert themes[1]['front_support']['rows'][0]['name'] == '深中华A'
    store.collection.return_value.find.assert_called_once()
    query, projection = store.collection.return_value.find.call_args.args
    assert query['$or'][0]['ts_code']['$in'] == ['000017.SZ', '600002.SH', '600003.SH']
    assert projection == {'_id': 0, 'ts_code': 1, 'ticker': 1, 'name': 1}


def test_named_archive_does_not_query_basic():
    store = Mock()
    refresh_topic_names(store, [{'front_support': {'rows': [{'ticker': '000017.SZ', 'name': '深中华A'}]}}])
    store.collection.assert_not_called()


def test_lazy_topic_detail_repairs_label_and_closes_store():
    theme = {'front_support': {'rows': [{'ticker': '000017.SZ', 'name': '000017.SZ'}]}}
    store = Mock()
    store.collection.return_value.find_one.return_value = {'theme': theme}
    store.collection.return_value.find.return_value = [{'ts_code': '000017.SZ', 'name': '深中华A'}]
    with patch('shilun.api.review._open_store', return_value=store):
        result = get_topic_detail('archived-topic')
    assert result['front_support']['rows'][0]['name'] == '深中华A'
    store.close.assert_called_once()


def test_main_review_repairs_already_ranked_archive():
    cached = {'sentiment': {'theme_evidence': {'perspective_version': PERSPECTIVE_VERSION,
        'themes': [{'front_support': {'rows': [{'ticker': '000017.SZ', 'name': '000017.SZ'}]}}]}}}
    store = Mock()
    store.collection.return_value.find_one.return_value = deepcopy(cached)
    store.collection.return_value.find.return_value = [{'ts_code': '000017.SZ', 'name': '深中华A'}]
    with patch.object(DailyReviewService, 'latest_date', return_value='2026-09-22'), \
         patch('shilun.review.service.load_review_sessions', return_value=[]), \
         patch('shilun.review.service.load_futures_history', return_value={}), \
         patch('shilun.review.topic_details.refresh_topic_attention'):
        result = DailyReviewService(store).get('2026-09-22')
    assert result['sentiment']['theme_evidence']['themes'][0]['front_support']['rows'][0]['name'] == '深中华A'
