from copy import deepcopy
from unittest.mock import Mock

from shilun.review.event_downside import attach_event_groups


def run(docs, stocks, day='2026-11-02'):
    store = Mock()
    store.collection.return_value.find.return_value = docs
    risk = {'stocks': stocks, 'count_ready': True, 'limit_down_count': len(stocks), 'penalty': 12}
    before = deepcopy(risk)
    result = attach_event_groups(store, risk, day, ['2026-10-29', '2026-10-30', day])
    assert risk == before
    assert result['penalty'] == 12  # Event grouping is evidence, not a new risk scoring rule.
    return result


def test_groups_exact_tags_dedupes_and_dates_historical_reason():
    stocks = [{'ticker': '600001.SH', 'name': '甲'}, {'ticker': '600002.SH', 'name': '乙'}]
    result = run([{'date': '2026-10-30', 'reasons': {'600001': 'AI短剧+国企改革+AI短剧'}},
                  {'date': '2026-11-02', 'reasons': {'600002': 'AI短剧+AI漫剧'}}], stocks)
    assert result['event_clusters'][0]['theme'] == 'AI短剧'
    assert result['event_clusters'][0]['count'] == 2
    assert result['event_attribution']['historical'] == 1
    assert result['event_attribution']['complete']
    assert [r['reason_date'] for r in result['event_clusters'][0]['stocks']] == ['2026-10-30', '2026-11-02']


def test_missing_and_future_reason_never_become_industry_or_zero():
    result = run([{'date': '2026-11-03', 'reasons': {'600001': '未来题材'}}],
                 [{'ticker': '600001.SH', 'name': '甲', 'theme': '传媒'}])
    assert result['event_clusters'] == []
    assert result['theme_cluster_status'] == 'pending'
    assert result['event_attribution']['covered'] == 0
    assert len(result['event_attribution']['missing']) == 1


def test_current_explicit_generic_reason_does_not_reuse_old_event():
    result = run([{'date': '2026-10-30', 'reasons': {'600001': '旧题材'}},
                  {'date': '2026-11-02', 'reasons': {'600001': '国企改革'}}],
                 [{'ticker': '600001.SH', 'name': '甲'}])
    assert result['event_attribution']['covered'] == 0
    assert result['event_attribution']['missing'][0]['reason_date'] == '2026-11-02'


def test_no_limit_down_is_known_zero():
    result = run([], [])
    assert result['theme_cluster_status'] == 'none'
    assert result['event_attribution']['total'] == 0
    assert result['event_attribution']['complete']
