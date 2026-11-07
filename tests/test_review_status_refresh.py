from copy import deepcopy
from unittest.mock import Mock, patch

from shilun.market.taxonomy import VERSION
from shilun.review.status_refresh import refresh_taxonomy_status


DAY = '2026-11-03'


def snapshot():
    return {'status': 'ok', 'analysis_date': DAY, 'generated_at': DAY+'T19:15:48+08:00',
        'taxonomy_coverage': {'taxonomy_missing': 3},
        'freshness': {'alerts': ['东方财富行业映射缺口 3 只；未使用旧来源补齐。',
                                DAY+' previous_limit_pool 上游补数不可用']},
        'source_sync': [{'date': DAY, 'dataset': 'previous_limit_pool', 'status': 'unavailable'}],
        'data_quality': {'status': 'ready', 'label': '数据可执行',
            'historical_review_ready': True, 'current_execution_ready': True,
            'checks': [{'id': 'daily_bar_cross_section', 'status': 'pass'},
                       {'id': 'unified_taxonomy', 'status': 'warn', 'observed': 0}]},
        'sentiment': {'fixed_cohort': ['600001.SH']}, 'market': {'price': 123}}


def store():
    result = Mock()
    result.collection.return_value.find.return_value = [
        {'date': DAY, 'ticker': '600001.SH'}, {'date': DAY, 'ticker': '600001.SH'},
        {'date': DAY, 'ticker': '600002.SH'}, {'date': DAY, 'ticker': '000001.SH'},
        {'date': '2026-11-04', 'ticker': '600003.SH'}]
    return result


def classified(code, state='ready', day=DAY):
    return {'ticker': code, 'classification_status': state,
            'taxonomy_date': day, 'taxonomy_version': VERSION}


def test_refresh_exact_cohort_keeps_real_gap_and_source_attempts_without_mutation():
    data, database = snapshot(), store()
    before = deepcopy(data)
    with patch('shilun.review.status_refresh.classify_stocks', return_value=[
            classified('600001.SH'), classified('600002.SH', 'industry_missing')]) as classifier:
        result = refresh_taxonomy_status(database, data)
    assert data == before
    assert classifier.call_args.args[1] == DAY
    assert classifier.call_args.args[2] == [{'ticker': '600001.SH'}, {'ticker': '600002.SH'}]
    assert classifier.call_args.kwargs == {'industry_only': True}
    assert result['taxonomy_coverage'] == {'ready': 1, 'industry_missing': 1}
    assert result['taxonomy_status']['coverage'] == .5
    assert result['taxonomy_status']['total'] == 2
    assert result['taxonomy_status']['snapshot_coverage'] == {'taxonomy_missing': 3}
    assert result['freshness']['alerts'] == [before['freshness']['alerts'][1],
                                            '东方财富行业映射缺口 1 只；未使用旧来源补齐。']
    assert result['data_quality']['status'] == 'degraded'
    assert result['source_sync'] == before['source_sync']
    assert result['market'] == before['market'] and result['sentiment'] == before['sentiment']
    assert result['generated_at'] == before['generated_at']
    assert result['data_quality']['current_execution_ready'] is True
    assert not database.collection.return_value.update_one.called
    assert not database.collection.return_value.replace_one.called


def test_fully_repaired_mapping_removes_only_managed_industry_alert():
    data = snapshot()
    with patch('shilun.review.status_refresh.classify_stocks', return_value=[
            classified('600001.SH'), classified('600002.SH')]):
        result = refresh_taxonomy_status(store(), data)
    assert result['freshness']['alerts'] == [data['freshness']['alerts'][1]]
    assert result['data_quality']['checks'][-1]['status'] == 'pass'
    assert result['data_quality']['status'] == 'ready'


def test_other_warn_or_stronger_quality_gate_is_not_cleared():
    for status in ('blocked', 'historical_only', 'degraded'):
        data = snapshot()
        data['data_quality'].update(status=status, current_execution_ready=False)
        data['data_quality']['checks'].append({'id': 'moneyflow_join', 'status': 'warn'})
        with patch('shilun.review.status_refresh.classify_stocks', return_value=[
                classified('600001.SH'), classified('600002.SH')]):
            result = refresh_taxonomy_status(store(), data)
        assert result['data_quality']['status'] == status
        assert result['data_quality']['current_execution_ready'] is False


def test_wrong_day_or_version_never_counts_as_ready():
    with patch('shilun.review.status_refresh.classify_stocks', return_value=[
            classified('600001.SH', day='2026-11-02'),
            {**classified('600002.SH'), 'taxonomy_version': 'legacy'}]):
        result = refresh_taxonomy_status(store(), snapshot())
    assert result['taxonomy_status']['ready'] == 0
    assert result['taxonomy_coverage'] == {'taxonomy_date_mismatch': 1, 'taxonomy_version_mismatch': 1}


def test_absent_bar_cohort_preserves_prior_warning_as_unknown_not_zero_gap():
    database, data = store(), snapshot()
    database.collection.return_value.find.return_value = []
    result = refresh_taxonomy_status(database, data)
    assert result['taxonomy_status']['available'] is False
    assert result['taxonomy_coverage'] == data['taxonomy_coverage']
    assert result['freshness']['alerts'] == data['freshness']['alerts']
