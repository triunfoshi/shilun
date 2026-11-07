from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from shilun.market.industry_history import sync_industry_history, fetch_members
from shilun.market.taxonomy import (VERSION, SNAPSHOTS, MEMBERS, classify_stocks, load_taxonomy)

DATES = ['2026-08-12', '2026-08-13']


class Collection:
    def __init__(self):
        self.rows = {}

    def find(self, query, projection=None):
        return [deepcopy(r) for r in self.rows.values() if all(
            r.get(k) in v['$in'] if isinstance(v, dict) else r.get(k) == v for k, v in query.items())]

    def find_one(self, query, projection=None):
        return next(iter(self.find(query)), None)

    def replace_one(self, query, value, upsert=False):
        self.rows[query['_id']] = deepcopy(value)

    def bulk_write(self, operations, **kwargs):
        for op in operations:
            self.replace_one(op._filter, op._doc, True)


def store():
    collections = {}
    return SimpleNamespace(collection=lambda n: collections.setdefault(n, Collection()),
        find_trade_calendar=lambda **kw: [{'cal_date': d.replace('-', ''), 'is_open': 1} for d in DATES])


def client(fail=False):
    def call(api, **params):
        if api == 'dc_index':
            return pd.DataFrame([{'ts_code': 'BK0001.DC', 'name': '零售', 'idx_type': '行业板块',
                'level': '东财三级行业', 'trade_date': params['trade_date'], 'up_num': 1, 'down_num': 0}])
        if fail:
            raise ValueError('unavailable')
        return pd.DataFrame([{'ts_code': 'BK0001.DC', 'con_code': '000001.SZ', 'name': '甲',
                             'trade_date': d.replace('-', '')} for d in DATES])
    return SimpleNamespace(_call_api=Mock(side_effect=call))


def test_exact_day_projection_is_resumable_and_not_a_full_taxonomy():
    s, c = store(), client()
    report = sync_industry_history(s, DATES, client=c)
    assert report['published'] == DATES
    assert len(s.collection(MEMBERS).rows) == 2
    for d in DATES:
        assert not load_taxonomy(s, d)
        ordinary = classify_stocks(s, d, [{'ticker': '000001.SZ'}])[0]
        assert ordinary['classification_status'] == 'taxonomy_missing'
        mapped = classify_stocks(s, d, [{'ticker': '000001.SZ'}], industry_only=True)[0]
        assert mapped['industry_code'] == 'BK0001.DC'
        assert mapped['taxonomy_date'] == d
        assert mapped['taxonomy_scope'] == 'industry_l3'
    c._call_api.reset_mock()
    assert sync_industry_history(s, DATES, client=c)['published'] == []
    c._call_api.assert_not_called()


def test_missing_members_never_publish_ready_snapshot_and_retry_repairs():
    s = store()
    assert sync_industry_history(s, DATES, client=client(True))['pending'] == DATES
    assert not s.collection(SNAPSHOTS).rows
    assert sync_industry_history(s, DATES, client=client())['pending'] == []


def test_full_snapshot_takes_precedence_over_projection():
    s = store()
    sync_industry_history(s, DATES, client=client())
    # A complete catalogue with no match must retain its explicit mapping gap.
    s.collection(SNAPSHOTS).replace_one({'_id': f'{VERSION}:{DATES[0]}'},
        {'_id': f'{VERSION}:{DATES[0]}', 'status': 'ready', 'revision': 'full'}, upsert=True)
    row = classify_stocks(s, DATES[0], [{'ticker': '000001.SZ'}], industry_only=True)[0]
    assert row['industry_code'] is None


def test_nontrading_dates_and_wrong_provider_dates_rejected():
    with pytest.raises(ValueError, match='交易日'):
        sync_industry_history(store(), ['2026-08-15'], client=client())
    c = client()
    c._call_api.side_effect = None
    c._call_api.return_value = pd.DataFrame([{'ts_code': 'BK0001.DC', 'trade_date': '20260923'}])
    with pytest.raises(ValueError, match='日期'):
        fetch_members(c, 'BK0001.DC', DATES)


def test_truncated_ranges_split_and_single_day_truncation_rejected():
    c = client()
    raw = {'ts_code': 'BK0001.DC', 'con_code': '000001.SZ', 'trade_date': '20260812'}
    c._call_api.side_effect = [pd.DataFrame([raw]*5000), pd.DataFrame([raw]),
                             pd.DataFrame([{**raw, 'trade_date': '20260813'}])]
    assert len(fetch_members(c, 'BK0001.DC', DATES)) == 2
    c._call_api.side_effect = None
    c._call_api.return_value = pd.DataFrame([raw]*5000)
    with pytest.raises(ValueError, match='截断'):
        fetch_members(c, 'BK0001.DC', DATES[:1])
