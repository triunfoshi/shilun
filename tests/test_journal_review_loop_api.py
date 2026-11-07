"""New review-loop routes retain journal identity and never mutate production facts."""
from copy import deepcopy

import pytest

from test_trade_journal_api import setup, import_rows, BASE


def decision_in(client, payload, expectation):
    import_rows(client, payload)
    episode = client.get(BASE + '/episodes').json()['items'][0]['episode_id']
    saved = client.post(BASE + '/episodes/' + episode + '/decisions', json={
        'fill_ids': ['buy'], 'action': '买入', 'reason': '用户补记历史原意',
        'observed_at': '2026-10-19T10:00:00+08:00',
        'evidence_class': 'retrospective_recall', 'expectation': expectation,
        'verification_at': '2026-10-20T15:00:00+08:00'})
    assert saved.status_code == 200, saved.text
    return episode, saved.json()


@pytest.mark.parametrize('suffix', ['/market-checks/preview', '/market-checks'])
def test_market_check_http_requires_explicit_decision_identity(setup, suffix):
    client, store, payload, _ = setup
    import_rows(client, payload)
    eid = client.get(BASE + '/episodes').json()['items'][0]['episode_id']
    writes = len(store.writes)
    response = client.post(BASE + '/episodes/' + eid + suffix, json={})
    assert response.status_code == 422, response.text
    assert len(store.writes) == writes


@pytest.mark.parametrize('suffix', ['/market-checks/preview', '/market-checks', '/rule-comparison/preview'])
def test_review_loop_http_preserves_same_origin_write_boundary(setup, suffix):
    client, store, _, _ = setup
    result = client.post(BASE + '/episodes/absent' + suffix,
                         headers={'Origin': 'https://unrelated.invalid'}, json={})
    assert result.status_code == 403, result.text
    assert not store.writes


def test_free_text_expectation_stays_unknown_without_any_production_writes(setup):
    client, store, payload, _ = setup
    eid, saved = decision_in(client, payload, '等待次日反弹，未设数值基准')
    # Decisions are wrapped by the original owner; read back its exact identity.
    detail = client.get(BASE + '/episodes/' + eid).json()
    original = deepcopy(detail['decisions'][0])
    writes = len(store.writes)
    response = client.post(BASE + '/episodes/' + eid + '/market-checks/preview', json={
        'decision_id': original['decision_id'], 'decision_revision': original['revision']})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'unknown'
    assert len(store.writes) == writes
    assert client.get(BASE + '/episodes/' + eid).json()['decisions'][0] == original
    assert not any(name in store.collections and store.collections[name].documents
                   for name in ('holdings', 'trade_orders', 'trade_executions'))


def seed_market(store, close=10.5):
    for day in ('2026-10-19', '2026-10-20'):
        store.collection('trade_calendar').documents.append({
            'exchange': 'SSE', 'cal_date': day.replace('-', ''), 'is_open': 1})
    store.collection('market_daily_bars').documents.append({
        'ticker': '000636.SZ', 'date': '2026-10-20', 'close': close, 'vol': 1000})
    store.collection('daily_price_limits').documents.append({
        'ticker': '000636.SZ', 'trade_date': '20261020', 'down_limit': 9, 'up_limit': 11})


def test_frontend_condition_payload_roundtrips_real_owner_preview_archive_and_check(setup):
    import json
    import subprocess
    client, store, payload, _ = setup
    eid, _ = decision_in(client, payload, '原文：次日反弹，历史补记')
    detail = client.get(BASE + '/episodes/' + eid).json()
    original = deepcopy(detail['decisions'][0])
    values = {'target_date': '2026-10-20', 'metric': 'close_return_pct',
              'baseline': 'entry_price', 'operator': '>=', 'target': '2',
              'baseline_fill_id': 'buy', 'original_text': original['expectation'],
              'revision_reason': '用户明确补充基准，保留历史补记身份'}
    script = "const fs=require('node:fs'); const ui=require('./shilun/static/journal-workspace.js'); const p=JSON.parse(fs.readFileSync(0,'utf8')); process.stdout.write(JSON.stringify(ui.buildConditionPayload(p.decision,p.values)));"
    built = subprocess.run(['node', '-e', script], input=json.dumps({'decision': original, 'values': values}),
                           text=True, capture_output=True, check=True)
    supplemented = client.post(BASE + '/episodes/' + eid + '/decisions', json=json.loads(built.stdout))
    assert supplemented.status_code == 200, supplemented.text
    current = client.get(BASE + '/episodes/' + eid).json()['decisions'][0]
    assert current['revision'] == original['revision'] + 1
    assert current['expectation']['text'] == original['expectation']
    assert current['evidence_class'] == 'retrospective_recall'
    seed_market(store)
    path = BASE + '/episodes/' + eid + '/market-checks'
    writes = len(store.writes)
    preview = client.post(path + '/preview', json={
        'decision_id': current['decision_id'], 'decision_revision': current['revision']})
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result['status'] == 'supported' and result['actual']['value'] == pytest.approx(5)
    assert not result['ex_ante_verifiable'] and len(store.writes) == writes
    saved = client.post(path, json={**result['preview_request'], 'decision_id': current['decision_id'],
                                   'preview_digest': result['preview_digest']})
    assert saved.status_code == 200, saved.text
    assert saved.json()['saved_status'] == 'supported'
    assert saved.json()['record']['decision_revision'] == current['revision']
    assert not saved.json()['record']['actual_provenance']['market_data_verified']
    assert all(name.startswith('trade_journal_') for name, _, _ in store.writes[writes:])
    history = client.get(BASE + '/decisions/' + current['decision_id'] + '/history').json()
    assert history['items'][0]['expectation'] == original['expectation']


def test_frontend_post_hoc_high_target_preserves_price_vs_execution_split_after_http_reload(setup):
    """Exercise the exact browser payload builder against an isolated API store."""
    import json
    import subprocess
    from test_trade_journal import fill

    client, store, payload, _ = setup
    payload['fills'][1] = fill('sell', '2026-10-20', 'sell', 100, 10.1, 0)
    eid, saved = decision_in(client, payload, '当时没有制定预期')
    catalogue = client.get(BASE + '/definitions').json()['definitions']
    wanted = {'mode.core_crowding', 'action.buy', 'evidence.retrospective_recall'}
    refs = [{'id': row['id'], 'rule_version': row['rule_version']} for row in catalogue if row['id'] in wanted]
    assert len(refs) == 3
    first = client.get(BASE + '/episodes/' + eid).json()['decisions'][0]
    seeded = client.post(BASE + '/episodes/' + eid + '/decisions', json={
        **{key: first[key] for key in ('action', 'reason', 'observed_at', 'evidence_class', 'expectation', 'fill_ids')},
        'decision_id': first['decision_id'], 'expected_revision': first['revision'], 'revision_reason': '补充原模式原动作定义',
        'mode_id': 'mode.core_crowding', 'action_id': 'action.buy', 'definition_refs': refs})
    assert seeded.status_code == 200, seeded.text
    original = client.get(BASE + '/episodes/' + eid).json()['decisions'][0]
    values = {'target_date': '2026-10-20', 'metric': 'high_return_pct', 'baseline': 'entry_price',
        'operator': '>=', 'target': '2', 'baseline_fill_id': 'buy', 'expectation_origin': 'post_hoc_review',
        'original_text': original['expectation'], 'revision_reason': ''}
    script = "const fs=require('node:fs'); const ui=require('./shilun/static/journal-workspace.js'); const p=JSON.parse(fs.readFileSync(0,'utf8')); process.stdout.write(JSON.stringify(ui.buildConditionPayload(p.decision,p.values)));"
    built = subprocess.run(['node', '-e', script], input=json.dumps({'decision': original, 'values': values}),
        text=True, capture_output=True, check=True)
    body = json.loads(built.stdout)
    assert body['evidence_class'] == 'historical_reconstruction'
    assert {r['id'] for r in body['definition_refs']} == {'mode.core_crowding', 'action.buy'}
    assert body['verification_at'] == '2026-10-20T15:30:00+08:00'
    assert body['expectation']['scope_fill_id'] == body['expectation']['baseline_fill_id'] == 'buy'
    assert body['expectation']['origin_decision_ref'] == {'decision_id': original['decision_id'], 'revision': original['revision']}
    supplemented = client.post(BASE + '/episodes/' + eid + '/decisions', json=body)
    assert supplemented.status_code == 200, supplemented.text
    current = supplemented.json()['record']
    assert current['evidence_class'] == 'historical_reconstruction'
    assert current['expectation']['origin'] == 'post_hoc_review'
    seed_market(store, close=10.1)
    store.collection('market_daily_bars').documents[-1].update(open=10.0, high=10.6, low=9.9)
    path = BASE + '/episodes/' + eid + '/market-checks'
    writes = len(store.writes)
    preview = client.post(path + '/preview', json={'decision_id': current['decision_id'], 'decision_revision': current['revision']})
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert len(store.writes) == writes
    assert result['market_evaluation']['status'] == 'supported'
    assert result['execution_evaluation']['status'] == 'refuted'
    assert result['execution_evaluation']['fills'][0]['sell_fill_id'] == 'sell'
    assert result['evaluation_purpose'] == 'post_hoc_reference'
    assert not result['interprets_original_expectation'] and not result['plan_was_ex_ante']
    stored = client.post(path, json={**result['preview_request'], 'decision_id': current['decision_id'],
        'preview_digest': result['preview_digest']})
    assert stored.status_code == 200, stored.text
    detail = client.get(BASE + '/episodes/' + eid).json()
    check = detail['expectation_checks'][-1]
    evidence = check['evidence'][0]
    assert evidence['market_evaluation']['status'] == 'supported'
    assert evidence['execution_evaluation']['status'] == 'refuted'
    assert evidence['evaluation_purpose'] == 'post_hoc_reference' and not evidence['interprets_original_expectation']
    assert not check['plan_was_ex_ante']
    history = client.get(BASE + '/decisions/' + current['decision_id'] + '/history').json()
    assert history['items'][-2] == original
    assert all(name.startswith('trade_journal_') for name, _, _ in store.writes)
    assert not any(store.collection(name).documents for name in ('holdings', 'trade_orders', 'trade_executions'))


def test_rule_comparison_http_reuses_actual_fifo_and_does_not_write(setup):
    client, store, payload, _ = setup
    import_rows(client, payload)
    eid = client.get(BASE + '/episodes').json()['items'][0]['episode_id']
    seed_market(store)
    original = client.get(BASE + '/episodes/' + eid).json()
    writes = len(store.writes)
    result = client.post(BASE + '/episodes/' + eid + '/rule-comparison/preview', json={
        'rule': {'kind': 'next_trading_day_close', 'sell_fee_bps': 0, 'slippage_bps': 0}})
    assert result.status_code == 200, result.text
    compared = result.json()
    assert compared['status'] == 'compared', compared
    assert compared['actual']['net_pnl'] == original['episode']['closed_pnl']
    assert compared['difference_pp'] == pytest.approx(compared['actual_return'] - compared['reference_return'])
    assert not compared['write_effects'] and not compared['ex_ante_verifiable']
    assert len(store.writes) == writes
    assert client.get(BASE + '/episodes/' + eid).json() == original


def test_market_check_changed_source_is_http_conflict_without_archived_result(setup):
    client, store, payload, _ = setup
    eid, _ = decision_in(client, payload, {'metric': 'close_return_pct', 'operator': '>=',
        'target': 2, 'baseline': 'entry_price', 'target_date': '2026-10-20'})
    current = client.get(BASE + '/episodes/' + eid).json()['decisions'][0]
    seed_market(store)
    path = BASE + '/episodes/' + eid + '/market-checks'
    preview = client.post(path + '/preview', json={
        'decision_id': current['decision_id'], 'decision_revision': current['revision']}).json()
    store.collection('market_daily_bars').documents[-1]['close'] = 9.5
    writes = len(store.writes)
    saved = client.post(path, json={**preview['preview_request'], 'decision_id': current['decision_id'],
                                   'preview_digest': preview['preview_digest']})
    assert saved.status_code == 409, saved.text
    assert all(name == 'trade_journal_write_locks' for name, _, _ in store.writes[writes:])
