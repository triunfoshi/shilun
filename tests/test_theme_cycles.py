from copy import deepcopy
from datetime import date, timedelta
import json
from urllib.parse import urlencode

from fastapi import FastAPI
import pytest

from shilun.review.theme_selection import ThemeSelectionReplay
from shilun.review.theme_cycles import ThemeCycleReplay, drawdown_warning, price_path, transition
from shilun.review.theme_rules import CycleRules
from shilun.review import theme_cycle_store as archive
from shilun.review.theme_selection_data import write_gzip
from shilun.review.strong_stock_candidates import VERSION as CORE_VERSION
from shilun.api.theme_cycles import router
from test_review_remote import LocalASGIClient

DAYS = [(date(2026, 9, 1)+timedelta(days=i)).isoformat() for i in range(16)
        if (date(2026, 9, 1)+timedelta(days=i)).weekday() < 5]
CORE, A, B = '600001.SH', '600002.SH', '600003.SH'


def inputs():
    stocks = {t: {'name': n} for t, n in [(CORE, '核心'), (A, '跟随甲'), (B, '跟随乙')]}
    m = {'sessions': DAYS, 'days': DAYS, 'context_days': DAYS, 'stocks': stocks, 'cores': {}, 'pools': {},
         'reasons': {}, 'bars': {t: {} for t in stocks}, 'limits': {t: {} for t in stocks}, 'core_version': CORE_VERSION}
    tax = {'snapshots': {}}
    previous = {t: 10. for t in stocks}
    for index, day in enumerate(DAYS):
        tickers = list(stocks) if index < 3 else []
        m['cores'][day] = [{'ticker': CORE, 'name': '核心', 'date': day, 'status': 'qualified', 'qualified_at': DAYS[0],
            'latest_limit_date': day if index < 3 else DAYS[2], 'leg': {'leg_id': 'leg1'},
            'cycle': {'start_date': DAYS[0], 'max_streak': 5, 'cumulative_return': .7}}]
        m['pools'][day] = {'date': day, 'complete': True, 'snapshot_mode': 'settled',
                         'stocks': [{'ticker': t, 'name': stocks[t]['name'], 'boards': 3} for t in tickers]}
        m['reasons'][day] = {'date': day, 'reasons': {t[:6]: '新零售' for t in tickers}}
        for t in stocks:
            pre = previous[t];close = pre*(1.1 if t in tickers else .98)
            m['bars'][t][day] = {'pre_close': pre, 'close': close, 'open': pre, 'low': min(pre, close),
                                 'high': max(pre, close), 'volume': 100}
            m['limits'][t][day] = {'up_limit': pre*1.1}
            previous[t] = close
        tax['snapshots'][day] = {'date': day, 'core_mapping_complete': True, 'rows': [
            {'code': 'BK0001.DC', 'name': '新零售', 'research_eligible': True, 'has_qualified_core': True,
             'membership_date': day, 'membership_complete': True, 'member_count': 3,
             'members': [{'con_code': t} for t in stocks]}]}
    return m, tax


def replay(m, t):
    return ThemeCycleReplay(ThemeSelectionReplay(m, t))


@pytest.mark.parametrize('value,warn', [(.14999, False), (.15, True), (.15001, True), (None, False)])
def test_drawdown_boundaries(value, warn):
    assert drawdown_warning(value) is warn


def test_warning_alone_and_one_weak_day_do_not_exit():
    metrics = dict(complete=True, conflict=False, drawdown_warning=True, strong=False, weak=True, mixed=True, balanced=False)
    stage, _, _ = transition('strengthening', metrics, {'weak': 1, 'strong': 0}, CycleRules())
    assert stage == 'disagreement'
    assert transition('strengthening', metrics, {'weak': 2, 'strong': 0}, CycleRules())[0] == 'retreat'


def test_fixed_losers_retained_single_disagreement_and_confirmed_exit_history():
    m, t = inputs(); rows = replay(m, t).run(DAYS)
    initial = rows[2]['active'][0]
    assert initial['back'] == [A, B]
    assert rows[3]['active'][0]['id'] == initial['id']
    assert rows[4]['active'][0]['back_feedback']['observed_count'] == 2
    assert rows[4]['active'][0]['back_feedback']['median_return'] < 0
    assert rows[-1]['archived'] and not rows[-1]['active']
    exited = rows[-1]['archived'][0]
    assert exited['back'] == initial['back']
    assert exited['history'][-1]['stage'] == 'retreat'
    assert initial['exit_date'] is None  # later results never mutate earlier snapshots


@pytest.mark.parametrize('fault,code', [('previous_pool', 'data_missing'), ('members', 'mapping_failed'),
                                      ('bar', 'data_missing'), ('opening_record', 'data_missing'), ('conflict', 'evidence_conflict')])
def test_injected_gaps_never_exit_and_repair_recovers(fault, code):
    m, t = inputs();day = DAYS[4]
    if fault == 'previous_pool': m['pools'].pop(DAYS[3])
    elif fault == 'members': t['snapshots'][day]['rows'][0]['membership_complete'] = False
    elif fault == 'bar': m['bars'][B].pop(day)
    elif fault == 'opening_record': m['bars'][B][day].pop('high')
    else: m['pools'][day]['stocks'].append({'ticker': B, 'name': '跟随乙'})
    rows = replay(m, t).run(DAYS[:5]);c = rows[-1]['active'][0]
    assert c['cycle_stage'] is None
    assert c['counts']['weak'] == 0
    assert code in [i['code'] for i in c['diagnostics']]
    if fault == 'members': assert c['metrics']['new_follower_count'] is None
    if fault in ('bar', 'opening_record'): assert c['back_feedback']['sealed'] is None
    assert c['last_confirmed_date'] < day
    good_m, good_t = inputs()
    repaired = replay(good_m, good_t).run(DAYS[:5])[-1]['active'][0]
    assert repaired['cycle_stage'] is not None
    assert not repaired['diagnostics']


def test_future_changes_do_not_change_prefix_and_price_peak():
    m, t = inputs(); before = replay(m, t).run(DAYS[:5])
    modified = deepcopy(m)
    for d in DAYS[5:]:
        modified['bars'][CORE][d]['close'] = 9999
        modified['cores'][d] = []
    assert replay(modified, t).run(DAYS[:5]) == before
    assert price_path(ThemeSelectionReplay(m, t), CORE, DAYS[0], DAYS[4])['drawdown'] == pytest.approx(1-.98**2)


def test_missing_price_chain_cannot_invent_zero_drawdown():
    m, t = inputs();m['bars'][CORE][DAYS[1]].pop('pre_close')
    p = price_path(ThemeSelectionReplay(m, t), CORE, DAYS[0], DAYS[4])
    assert p['drawdown'] is None and p['missing_dates'] == [DAYS[1]]


def test_missing_day_cannot_silently_extend_confirmation_window():
    m, t = inputs();engine = replay(m, t);engine.step(DAYS[0])
    with pytest.raises(ValueError): engine.step(DAYS[2])


def test_recovery_requires_two_confirmed_days_and_missing_resets_count():
    m = dict(complete=True, conflict=False, drawdown_warning=False, strong=True, weak=False, mixed=False, balanced=False)
    assert transition('disagreement', m, {'weak': 0, 'strong': 1}, CycleRules())[0] == 'disagreement'
    assert transition('disagreement', m, {'weak': 0, 'strong': 2}, CycleRules())[0] == 'recovery'
    m['complete'] = False
    assert transition('recovery', m, {'weak': 0, 'strong': 3}, CycleRules())[0] is None


def test_unmatched_is_not_forced_business_stage():
    metrics = dict(complete=True, conflict=False, drawdown_warning=False, strong=False, weak=False, mixed=False, balanced=False)
    assert transition('fermentation', metrics, {'weak': 0, 'strong': 0}, CycleRules())[:2] == (None, 'rule_unmatched')


def write_source(path, m, t):
    write_gzip(path/'market_inputs.json.gz', m);write_gzip(path/'taxonomy_inputs.json.gz', t)


def test_atomic_archive_api_details_dates_versions_corruption_and_failed_job(tmp_path, monkeypatch):
    monkeypatch.setattr('shilun.review.theme_distribution.enabled', lambda: False)
    source, root = tmp_path/'source', tmp_path/'archive'
    write_source(source, *inputs());info = archive.build(source, root)
    monkeypatch.setattr(archive, 'ROOT', root)
    app = FastAPI();app.include_router(router, prefix='/api/v1/review');client = LocalASGIClient(app)
    data = client.get('/api/v1/review/theme-cycles?date='+DAYS[2]).json()
    assert data == archive.day_payload(DAYS[2], root=root)
    detail = client.get('/api/v1/review/theme-cycles/detail?'+urlencode({'date': DAYS[2], 'revision': info['revision'], 'cycle_id': data['active'][0]['id']}))
    assert detail.status_code == 200 and detail.json()['cycle'] == data['active'][0]
    assert client.get('/api/v1/review/theme-cycles?date=2026-09-26').json()['diagnostic_status'] == 'task_incomplete'
    assert client.get('/api/v1/review/theme-cycles?date='+DAYS[2]+'&revision='+'b'*16).status_code == 409
    assert client.get('/api/v1/review/theme-cycles?date=../secret').status_code == 422
    with pytest.raises(FileNotFoundError): archive.build(tmp_path/'missing_source', root)
    assert json.loads((root/'job.json').read_text())['status'] == 'failed'
    assert archive.manifest(root)['revision'] == info['revision']
    archive._read.cache_clear();(root/info['revision']/f'{DAYS[2]}.json.gz').write_bytes(b'corrupt')
    assert client.get('/api/v1/review/theme-cycles?date='+DAYS[2]).status_code == 409


def test_quality_records_deduplicate_and_close_after_rebuild(tmp_path):
    source, root = tmp_path/'source', tmp_path/'archive'
    m, t = inputs(); m['bars'][B].pop(DAYS[4]);write_source(source, m, t)
    first = archive.build(source, root);assert first['quality_counts']['open'] > 0
    second = archive.build(source, root);assert second['quality_counts']['open'] == first['quality_counts']['open']
    write_source(source, *inputs());third = archive.build(source, root)
    assert third['quality_counts']['resolved'] > 0
