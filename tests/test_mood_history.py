from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from shilun.review.market_mood import (
    MOOD_COLLECTION, MOOD_RULE_VERSION, load_market_moods, regime_for_mood,
)
from shilun.review.mood_history import backfill_moods
from shilun.review.service import REVIEW_COLLECTION, REVIEW_VERSION


DATES = ['2026-09-18', '2026-09-21', '2026-09-22', '2026-09-23']


class Collection:
    def __init__(self):
        self.rows = []
        self.updates = []

    def find(self, query, projection=None):
        return [deepcopy(r) for r in self.rows if all(
            r.get(k) in v['$in'] if isinstance(v, dict) else r.get(k) == v
            for k, v in query.items())]

    def replace_one(self, query, doc, upsert=False):
        self.rows = [r for r in self.rows if not all(r.get(k) == v for k, v in query.items())]
        self.rows.append(deepcopy(doc))

    def update_many(self, query, update):
        self.updates.append((query, update))


def store():
    collections = {}
    return SimpleNamespace(collection=lambda name: collections.setdefault(name, Collection()))


def mood(code='normal'):
    return {'code': code, 'rules_version': MOOD_RULE_VERSION, 'score': 50 if code == 'normal' else None,
            'reason': '待补齐：当日跌停价' if code == 'pending' else '',
            'missing_inputs': ['当日跌停价'] if code == 'pending' else []}


def archive(s, day, value, version=REVIEW_VERSION):
    s.collection(MOOD_COLLECTION).rows.append({'date': day, 'engine_version': version, 'mood': value})


def snapshot(s, day, value):
    s.collection(REVIEW_COLLECTION).rows.append({'analysis_date': day,
        'engine_version': REVIEW_VERSION, 'sentiment': {'market_mood': value}})


def service():
    svc = MagicMock()
    svc._stock_basic.side_effect = lambda day, **kw: {'a': {'sector': day}}
    dated = {day: {'a': {'close': i + 10}} for i, day in enumerate(DATES)}
    svc._load_bars.return_value = ({}, dated)
    svc._price_limits_history.return_value = {}
    svc._limit_rows.return_value = ({}, {})
    svc._amount_series.return_value = [{'amount_yi': 100}]
    svc._breadth.return_value = {'up_ratio': .5}
    svc._sentiment.return_value = {'market_mood': mood(),
        'downside_risk': {'position_counts': {}, 'stocks': []}}
    return svc


def test_source_precedence_and_old_versions_are_explicitly_unknown():
    s = store()
    archive(s, DATES[0], mood('active'))
    snapshot(s, DATES[0], mood('pending'))
    archive(s, DATES[1], mood('normal'))
    snapshot(s, DATES[1], mood('hot'))
    archive(s, DATES[2], {**mood('cold'), 'rules_version': 'old'})
    archive(s, DATES[3], mood(), version='old-engine')
    result = load_market_moods(s, DATES)
    assert result[DATES[0]]['code'] == 'active'
    assert result[DATES[0]]['source'] == 'P2共享情绪归档'
    assert result[DATES[1]]['code'] == 'hot'
    assert result[DATES[1]]['source'] == 'P2收盘快照'
    assert result[DATES[2]]['missing_inputs'] == ['mood_rule_version']
    assert regime_for_mood(result[DATES[2]]) == '环境未覆盖'
    assert DATES[3] not in result


def test_backfill_retries_pending_and_uses_each_days_taxonomy_and_bars():
    s, svc = store(), service()
    archive(s, DATES[1], mood('pending'))
    archive(s, DATES[3], mood())
    with patch('shilun.review.service.DailyReviewService', return_value=svc), \
         patch('shilun.review.mood_history.load_review_sessions', return_value=DATES):
        result = backfill_moods(s, DATES[-1], 3)
        assert result['built'] == 2 and result['pending'] == []
        assert svc._stock_basic.call_args_list[0].args == (DATES[1],)
        assert svc._stock_basic.call_args_list[1].args == (DATES[2],)
        assert all(c.kwargs == {'industry_only': True} for c in svc._stock_basic.call_args_list)
        for call, day in zip(svc._sentiment.call_args_list, DATES[1:3]):
            args = call.kwargs
            assert args['analysis_date'] == day
            assert args['basic']['a']['sector'] == day
            assert args['sessions'][-1] == day
            assert args['current_bars'] == svc._load_bars.return_value[1][day]
            assert args['previous_date'] == DATES[DATES.index(day) - 1]
        svc._sentiment.reset_mock()
        assert backfill_moods(s, DATES[-1], 3)['built'] == 0
        svc._sentiment.assert_not_called()


def test_incomplete_snapshot_keeps_recomputed_gap_diagnostics():
    s = store()
    archive(s, DATES[0], mood('pending'))
    snapshot(s, DATES[0], {})
    result = load_market_moods(s, DATES)
    assert result[DATES[0]]['missing_inputs'] == ['当日跌停价']
    assert result[DATES[0]]['reason'] == '待补齐：当日跌停价'


def test_pending_diagnostics_persist_and_forced_retry_keeps_verified_result():
    s, svc = store(), service()
    archive(s, DATES[2], mood())
    archive(s, DATES[3], mood())
    svc._sentiment.return_value['market_mood'] = mood('pending')
    with patch('shilun.review.service.DailyReviewService', return_value=svc), \
         patch('shilun.review.mood_history.load_review_sessions', return_value=DATES):
        result = backfill_moods(s, DATES[-1], 3, force=True)
    assert result['built'] == 0
    assert result['pending'] == DATES[1:]
    assert result['diagnostics'][0]['missing_inputs'] == ['当日跌停价']
    result = load_market_moods(s, DATES)
    assert result[DATES[1]]['code'] == 'pending'
    assert result[DATES[2]]['code'] == result[DATES[3]]['code'] == 'normal'
    assert not s.collection(REVIEW_COLLECTION).updates
