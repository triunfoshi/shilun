from copy import deepcopy

from shilun.review.event_theme_taxonomy import (VERSION, collect_event_taxonomy,
                                               daily_direction_rows, topic_code)
from shilun.review.theme_selection import ThemeSelectionReplay
from test_theme_selection import inputs, DAYS, CORE, FIRST, SECOND


def market_inputs():
    market, _ = inputs()
    market['context_days'] = DAYS[:]
    return market


def test_exact_vibe_topics_replace_dc_aliases_and_generic_attributes():
    market = market_inputs()
    market['reasons'][DAYS[-3]]['reasons'][CORE[:6]] = 'AI短剧+AI漫剧+国企改革+地方国资'
    tax = collect_event_taxonomy(market)
    names = {r['name'] for r in tax['snapshots'][DAYS[-3]]['rows']}
    assert {'AI短剧', 'AI漫剧'} <= names
    assert not names & {'国企改革', '地方国资', '人工智能', '零售主线'}
    assert all(r['code'].startswith('EVENT.') for r in tax['snapshots'][DAYS[-1]]['rows'])
    assert tax['topic_schema'] == VERSION


def test_snapshot_prefix_and_labeled_members_never_look_into_future():
    market = market_inputs()
    before = collect_event_taxonomy(market)
    modified = deepcopy(market)
    modified['pools'][DAYS[-1]]['stocks'] = [{'ticker': SECOND, 'name': '跟随乙'}]
    modified['reasons'][DAYS[-1]]['reasons'] = {SECOND[:6]: '未来事件'}
    after = collect_event_taxonomy(modified)
    assert before['snapshots'][DAYS[-2]] == after['snapshots'][DAYS[-2]]
    assert '未来事件' not in {r['name'] for r in after['snapshots'][DAYS[-2]]['rows']}
    assert '未来事件' in {r['name'] for r in after['snapshots'][DAYS[-1]]['rows']}


def test_events_require_current_literal_tag_and_unknown_membership_is_not_zero():
    market = market_inputs()
    market['reasons'][DAYS[-3]]['reasons'][CORE[:6]] = '新零售'
    market['reasons'][DAYS[-2]]['reasons'].pop(SECOND[:6])
    tax = collect_event_taxonomy(market)
    replay = ThemeSelectionReplay(market, tax)
    events = replay.events(topic_code('新零售'), DAYS[-2])
    assert {r['ticker'] for r in events['unknown']} == {SECOND}
    assert not tax['snapshots'][DAYS[-2]]['reason_coverage_complete']
    market['reasons'][DAYS[-2]]['reasons'][FIRST[:6]] = '消费概念'
    replay = ThemeSelectionReplay(market, collect_event_taxonomy(market))
    assert not replay.events(topic_code('新零售'), DAYS[-2])['events']


def test_new_event_has_verified_zero_before_first_appearance_with_unknown_kept():
    market = market_inputs()
    new_day, before = DAYS[-2], DAYS[-3]
    market['reasons'][new_day]['reasons'][FIRST[:6]] = 'AI短剧'
    replay = ThemeSelectionReplay(market, collect_event_taxonomy(market))
    prior = replay.events(topic_code('AI短剧'), before)
    assert prior['complete'] and prior['events'] == [] and prior['unknown'] == []
    assert topic_code('AI短剧') not in replay.catalogs[before]
    market['reasons'][before]['reasons'].pop(CORE[:6])
    replay = ThemeSelectionReplay(market, collect_event_taxonomy(market))
    assert {r['ticker'] for r in replay.events(topic_code('AI短剧'), before)['unknown']} == {CORE}


def test_previous_core_event_stays_observable_when_not_limit_up_today():
    market = market_inputs()
    replay = ThemeSelectionReplay(market, collect_event_taxonomy(market))
    assert CORE in replay.members[DAYS[-1], topic_code('新零售')]
    assert all(not row.get('source_topics') for row in replay.catalogs[DAYS[-1]].values())
    assert not replay.events(topic_code('新零售'), DAYS[-1])['events']


def test_same_day_direction_uses_reasons_not_industry_and_rejects_wrong_date():
    market = market_inputs()
    day = DAYS[-3]
    pool, doc = market['pools'][day], market['reasons'][day]
    rows = daily_direction_rows(pool, doc, day)
    assert {r['name'] for r in rows} == {'新零售', '消费概念'}
    assert all(r['membership_scope'] == 'same_day_attributed_limit_up_sample' for r in rows)
    assert daily_direction_rows(pool, {**doc, 'date': DAYS[-2]}, day) == []


def test_old_cloud_archive_is_explicit_migration_gap_not_current_topics():
    from shilun.review.theme_distribution import read, CONTROL
    from test_theme_daily_job import Store
    store = Store()
    store.collection(CONTROL).insert_one({'_id': 'current', 'date': DAYS[-1],
        'cycle': {'version': 'theme-cycle-v2', 'dates': DAYS}})
    result = read('cycle', DAYS[-1], store=store)
    assert result['diagnostic_status'] == 'topic_schema_migration'
    assert result['active'] == [] and result['date'] == DAYS[-1]
