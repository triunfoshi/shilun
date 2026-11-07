from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pymongo.errors import DuplicateKeyError

from shilun.jobs.theme_daily_job import run, extend_market, ThemeInputError
from shilun.jobs.data_pipeline_job import PipelineBusy
from shilun.review.theme_distribution import CONTROL, FILES, read
from shilun.review.theme_selection_data import write_gzip
from test_theme_cycles import inputs, DAYS


class Collection:
    def __init__(self):
        self.rows = {}
        self.fail_insert = False

    def matches(self, row, query):
        for k, v in query.items():
            if isinstance(v, dict):
                if '$lte' in v and (k not in row or row[k] > v['$lte']):
                    return False
            elif row.get(k) != v:
                return False
        return True

    def find_one(self, query, projection=None):
        return next((deepcopy(r) for r in self.rows.values() if self.matches(r, query)), None)

    def insert_one(self, row):
        if self.fail_insert:
            raise RuntimeError('upload interrupted')
        if row['_id'] in self.rows:
            raise DuplicateKeyError('duplicate')
        self.rows[row['_id']] = deepcopy(row)

    def update_one(self, query, update, upsert=False):
        found = self.find_one(query)
        if found:
            self.rows[found['_id']].update(deepcopy(update.get('$set', {})))
            return SimpleNamespace(modified_count=1, upserted_id=None)
        if upsert:
            self.insert_one({'_id': query['_id'], **update.get('$setOnInsert', {}), **update.get('$set', {})})
            return SimpleNamespace(modified_count=0, upserted_id=query['_id'])
        return SimpleNamespace(modified_count=0, upserted_id=None)

    def replace_one(self, query, row, upsert=False):
        found = self.find_one(query)
        if found:
            self.rows[found['_id']] = deepcopy(row)
        elif upsert:
            self.insert_one(row)
        return SimpleNamespace(matched_count=int(found is not None))

    def delete_one(self, query):
        found = self.find_one(query)
        if found:
            del self.rows[found['_id']]


class Store:
    def __init__(self):
        self.collections = {}
        self.states = []

    def collection(self, name):
        return self.collections.setdefault(name, Collection())

    def upsert_sync_state(self, value):
        self.states.append(deepcopy(value))


@pytest.fixture
def setup(tmp_path):
    market, tax = inputs()
    market.update(start=DAYS[0], end=DAYS[-1], scope_note='test', suspensions={},
                  benchmark={'ticker': '000001.SH', 'name': '上证指数', 'bars': {d: {'close': 100.0} for d in DAYS}})
    previous = deepcopy(market)
    previous.update(end=DAYS[-2], days=DAYS[:-1], context_days=DAYS[:-1])
    for key in ('pools', 'cores', 'reasons'):
        previous[key].pop(DAYS[-1])
    old_tax = deepcopy(tax);old_tax['snapshots'].pop(DAYS[-1])
    write_gzip(tmp_path/'seed'/'market_inputs.json.gz', previous)
    write_gzip(tmp_path/'seed'/'taxonomy_inputs.json.gz', old_tax)
    reader = Mock()
    reader.collect.return_value = {'snapshots': {DAYS[-1]: tax['snapshots'][DAYS[-1]]}, 'diagnostics': []}
    return Store(), market, reader, tmp_path, previous


def test_dated_run_publishes_both_archives_with_same_revision(setup):
    store, market, reader, root, previous = setup
    result = run(store, DAYS[-1], root=root, collector=lambda *a: market, reader=reader)
    assert result['status'] == 'complete' and result['date'] == DAYS[-1]
    assert reader.collect.call_args.args[0]['context_days'] == [DAYS[-1]]
    c = read('cycle', DAYS[-1], store=store)
    a = read('acceptance', DAYS[-1], store=store)
    assert c['revision'] == a['revision'] == result['revision']
    assert c['date'] == a['date'] == DAYS[-1]
    assert read('cycle', '2026-12-31', store=store)['status'] == 'not_materialized'
    with pytest.raises(ValueError): read('cycle', DAYS[-1], revision='a'*16, store=store)
    assert not store.collection('job_leases').rows
    assert (root/'inputs.json').exists()


def test_failure_preserves_previous_publication_and_retry_succeeds(setup):
    store, market, reader, root, _ = setup
    first = run(store, DAYS[-1], root=root, collector=lambda *a: market, reader=reader)
    broken = deepcopy(market);broken['pools'][DAYS[-1]]['complete'] = False
    with pytest.raises(ThemeInputError):
        run(store, DAYS[-1], root=root, collector=lambda *a: broken, reader=reader)
    old = read('cycle', DAYS[-1], store=store)
    assert old['revision'] == first['revision']
    assert old['job']['status'] == 'failed'
    assert old['job']['missing'][0]['dataset'] == 'settled_limit_pool'
    assert not store.collection('job_leases').rows
    resumed = run(store, DAYS[-1], root=root, collector=lambda *a: market, reader=reader)
    assert resumed['status'] == 'complete'
    assert read('acceptance', DAYS[-1], store=store)['revision'] == resumed['revision']


def test_upload_failure_never_switches_current_pointer(setup):
    store, market, reader, root, _ = setup
    first = run(store, DAYS[-1], root=root, collector=lambda *a: market, reader=reader)
    store.collection(FILES).fail_insert = True
    with pytest.raises(RuntimeError):
        run(store, DAYS[-1], root=root, collector=lambda *a: market, reader=reader)
    assert store.collection(CONTROL).rows['current']['revision'] == first['revision']
    assert read('cycle', DAYS[-1], store=store)['job']['stage'] == 'publish'


def test_corrupted_shared_payload_fails_closed(setup):
    store, market, reader, root, _ = setup
    result = run(store, DAYS[-1], root=root, collector=lambda *a: market, reader=reader)
    store.collection(FILES).rows[f'cycle:{result["revision"]}:{DAYS[-1]}']['data'] = b'bad'
    with pytest.raises(ValueError): read('cycle', DAYS[-1], store=store)


def test_concurrent_job_cannot_relabel_existing_job(setup):
    store, market, reader, root, _ = setup
    from datetime import timedelta
    store.collection('job_leases').insert_one({'_id':'theme_daily_pipeline','owner':'other',
        'expires_at':datetime.now(timezone.utc)+timedelta(hours=1)})
    with pytest.raises(PipelineBusy): run(store, DAYS[-1], root=root, collector=lambda *a: market, reader=reader)
    assert not store.collection(CONTROL).rows


def test_extending_keeps_past_cores_pools_and_baseline(setup):
    _, market, _, _, previous = setup
    fresh = deepcopy(market);fresh['cores'][DAYS[0]] = [];fresh['pools'][DAYS[0]] = {}
    result = extend_market(previous, fresh, DAYS[-1])
    assert result['cores'][DAYS[0]] == previous['cores'][DAYS[0]]
    assert result['pools'][DAYS[0]] == previous['pools'][DAYS[0]]
    fresh['context_days'] = fresh['context_days'][1:]
    with pytest.raises(ThemeInputError): extend_market(previous, fresh, DAYS[-1])


def test_repaired_historical_reasons_override_empty_checkpoint_without_future_text(setup):
    _, market, _, _, previous = setup
    day = DAYS[2]
    previous['reasons'][day] = {'date': day, 'reasons': {}}
    fresh = deepcopy(market)
    fresh['reasons'][day] = {'date': day, 'source': 'dated-repair', 'reasons': {'600001': 'AI短剧'}}
    result = extend_market(previous, fresh, DAYS[-1])
    assert result['reasons'][day]['reasons']['600001'] == 'AI短剧'
    from shilun.review.event_theme_taxonomy import collect_event_taxonomy
    names = {r['name'] for r in collect_event_taxonomy(result)['snapshots'][day]['rows']}
    assert 'AI短剧' in names
    fresh['reasons'][day]['date'] = DAYS[-1]
    assert extend_market(previous, fresh, DAYS[-1])['reasons'][day]['reasons'] == {}


@pytest.mark.parametrize('reason', [{}, {'600001': ''}])
def test_historical_reason_refresh_failure_keeps_valid_cached_entries(setup, reason):
    _, market, _, _, previous = setup
    day = DAYS[2]
    fresh = deepcopy(market)
    fresh['reasons'][day] = {'date': day, 'reasons': reason}
    assert extend_market(previous, fresh, DAYS[-1])['reasons'][day] == previous['reasons'][day]


@pytest.mark.parametrize('gap,dataset', [('benchmark','benchmark'), ('reasons','limit_reasons'),
                                      ('catalog','taxonomy'), ('members','members')])
def test_missing_current_input_blocks_publication_with_actionable_reason(setup, gap, dataset):
    store, market, reader, root, _ = setup
    if gap == 'benchmark': market['benchmark']['bars'].pop(DAYS[-1])
    if gap == 'reasons':
        market['pools'][DAYS[-1]]['stocks'] = [{'ticker':'600001.SH'}]
        market['reasons'].pop(DAYS[-1])
    if gap == 'catalog': reader.collect.return_value['snapshots'][DAYS[-1]]['core_mapping_complete'] = False
    if gap == 'members': reader.collect.return_value['snapshots'][DAYS[-1]]['rows'][0]['membership_date'] = DAYS[-2]
    with pytest.raises(ThemeInputError): run(store, DAYS[-1], root=root, collector=lambda *a: market, reader=reader)
    control = store.collection(CONTROL)
    assert 'current' not in control.rows
    assert dataset in [r['dataset'] for r in control.rows['job']['missing']]


def test_cloud_reads_use_same_publication_without_local_date_fallback(setup, monkeypatch):
    store, market, reader, root, _ = setup
    run(store, DAYS[-1], root=root, collector=lambda *a: market, reader=reader)
    store.close = Mock()
    monkeypatch.setattr('shilun.review.theme_distribution.enabled', lambda: True)
    monkeypatch.setattr('shilun.review.theme_distribution.open_store', lambda: store)
    from shilun.review import theme_cycle_store, theme_acceptance
    cycles = theme_cycle_store.day_payload(DAYS[-1])
    detail = theme_acceptance.day_payload(DAYS[-1])
    assert cycles['revision'] == detail['revision'] == theme_acceptance.manifest()['revision']
    assert theme_cycle_store.day_payload('2026-12-31')['active'] == []
