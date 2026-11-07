from copy import deepcopy
from unittest.mock import MagicMock

from shilun.review.topic_attention import attention_core, rank_theme_attention, within_review_member_limit
from shilun.review.theme_evidence import theme_evidence
from shilun.review.topic_details import refresh_topic_attention
from shilun.review.board_effects import build_effects

DAYS = [f'2026-09-{i:02}' for i in range(1, 16)]


def track(code, ticker, gain, strength=50, size=20):
    return dict(code=code, name=code, member_count=size, active_codes=[ticker, code+'-1', code+'-2'],
        strength_score=strength, two_plus_stock_count_7d=2,
        round={'leaders': [dict(ticker=ticker, name=ticker, max_board=5, core_qualified_at=DAYS[4], active_board_days=4,
            latest_board_date=DAYS[-2], series=[{'date': d, 'cum_return': gain} for d in DAYS])]},
        front_support={'rows': [{'ticker': ticker}]})


def test_history_priority_survives_weak_day_and_is_name_independent():
    old, other, hot = track('old', 'a', 1, 10), track('other', 'b', .8, 5), track('hot', 'c', .4, 99)
    result = rank_theme_attention([hot, other, old], DAYS[-1])
    assert [r['code'] for r in result] == ['old', 'other', 'hot']
    assert [r['strength_score'] for r in result] == [10, 5, 99]
    assert [r['attention_rank'] for r in result] == [1, 2, 3]
    original = deepcopy(result)
    for n, r in enumerate(result):
        r['name'] = '任意新名称'+str(n)
    assert [r['code'] for r in rank_theme_attention(result, DAYS[-1])] == [r['code'] for r in original]


def test_shared_core_prefers_active_concentration_but_keeps_distinct_members():
    broad, focused = track('broad', 'a', 1, 99, 50), track('focused', 'a', 1, 5, 20)
    result = rank_theme_attention([broad, focused], DAYS[-1])
    assert result[0]['code'] == 'focused' and not result[1]['selected']
    assert result[1]['overlap_with'] == 'focused'
    assert result[1]['active_codes'] == broad['active_codes']


def test_complete_membership_has_no_fifty_stock_cap():
    broad, focused = track('broad', 'a', 3, size=51), track('focused', 'a', 1, size=50)
    unknown = track('unknown', 'c', 2)
    unknown.pop('member_count')
    result = rank_theme_attention([broad, focused, unknown], DAYS[-1])
    assert {r['code'] for r in result} == {'focused','broad'}
    assert result[0]['attention_rank'] == 1 and result[0]['selected']
    # Active/front-row counts cannot shrink the full-member denominator.
    assert len(broad['active_codes']) == 3 and within_review_member_limit(broad)
    members = [{'con_code': str(i)} for i in range(50)]
    assert within_review_member_limit({'members': members + [members[0]], 'member_count': 51})
    assert within_review_member_limit({'members': members + [{'con_code':'extra'}], 'member_count': 3})


def test_perspective_without_core_is_excluded_before_history_calculation():
    from unittest.mock import patch
    perspectives = {'perspectives': [{'name':'large', 'members':[{'con_code':str(i)} for i in range(51)]}],
                    'version':'test', 'membership_note':'test', 'rule':'test', 'shortlist_count':1, 'missing_members':[]}
    with patch('shilun.review.theme_evidence.round_evidence') as round_calc:
        result = theme_evidence(analysis_date=DAYS[-1], limit_rows_by_date={}, current_bars={},
                                previous_bars={}, bars_by_ticker={}, moneyflow={}, perspectives=perspectives)
    round_calc.assert_not_called()
    assert result['themes'] == result['active_themes'] == []
    assert result['member_limit'] is None


def test_cached_snapshot_keeps_broad_theme_but_folds_shared_core():
    store = MagicMock()
    focused = track('focused', 'a', 1, size=50)
    full = deepcopy(focused)
    focused.update(detail_id='focused-id', front_support={})
    store.collection.return_value.find.return_value = [{'_id':'focused-id', 'theme':full}]
    evidence = {'attention_version':'topic-attention-1', 'themes':[track('large', 'a', 3, size=636), focused]}
    refresh_topic_attention(store, evidence, DAYS[-1])
    assert {r['name'] for r in evidence['themes']} == {'focused','large'}
    assert evidence['active_themes'] == ['focused']
    assert evidence['themes'][0]['attention_rank'] == 1
    assert evidence['themes'][0]['front_support']['rows'] == full['front_support']['rows']
    assert evidence['member_limit'] is None


def test_missing_future_and_stale_core_never_manufacture_return():
    leader = track('old', 'a', 1)['round']['leaders'][0]
    leader['series'].append({'date': '2026-09-16', 'cum_return': 100})
    assert attention_core([leader], DAYS[-1])['return'] == 1
    assert attention_core([leader], '2026-09-17') is None
    leader['series'][-2]['cum_return'] = None
    assert attention_core([leader], DAYS[-1]) is None
    leader['series'][-2]['cum_return'] = 1
    leader['latest_board_date'] = DAYS[0]
    assert attention_core([leader], DAYS[-1]) is None


def test_core_gain_truncates_earlier_history_at_twenty_sessions():
    leader = track('old', 'a', 1)['round']['leaders'][0]
    leader['series'] = [{'date': f'2026-08-{i:02}', 'cum_return': i/10} for i in range(1, 26)]
    leader['latest_board_date'] = '2026-08-25'
    core = attention_core([leader], '2026-08-25')
    assert core['sessions'] == 20 and core['date_from'] == '2026-08-06'
    assert core['return'] == (1+2.5)/(1+.5)-1


def test_zero_boards_two_days_does_not_erase_qualified_old_topic():
    days = DAYS[-7:]
    pools = {d: [] for d in days}
    pools[days[0]] = [dict(ticker='a', name='a', theme_name='old', consecutive_count=3),
                      dict(ticker='b', name='b', theme_name='old', consecutive_count=2)]
    result = theme_evidence(analysis_date=days[-1], previous_date=days[-2], sessions=days,
        limit_rows_by_date=pools, current_bars={}, previous_bars={}, bars_by_ticker={}, moneyflow={})
    assert result['themes'][0]['name'] == 'old'
    assert result['themes'][0]['current_count'] == result['themes'][0]['previous_count'] == 0


def test_cached_new_default_hydrates_members_and_keeps_new_order():
    store = MagicMock()
    old = track('old', 'a', 1, 10)
    full = deepcopy(old)
    old.update(detail_id='old-id', front_support={})
    store.collection.return_value.find.return_value = [{'_id': 'old-id', 'theme': full}]
    evidence = {'themes': [track('hot', 'b', .4, 99), old]}
    refresh_topic_attention(store, evidence, DAYS[-1])
    assert evidence['themes'][0]['front_support']['rows'] == [{'ticker': 'a'}]
    assert evidence['themes'][0]['attention_rank'] == 1
    assert 'detail_id' not in evidence['themes'][0]


def test_unknown_seal_removed_from_all_sealed_denominators_but_not_broken():
    rows = [dict(code='a', prev_boards=1, seal_time='093000', ret=2),
            dict(code='b', prev_boards=1, seal_time='000000', ret=90)]
    result = build_effects(dates=DAYS[-2:], per_day={DAYS[-1]: rows},
        highest_by_day={DAYS[-1]: [dict(rows[1], is_highest=True)]}, highest_ready={DAYS[-1]},
        broken_by_day={DAYS[-1]: [dict(code='c', ret=-3)]})
    assert result['excluded_count'] == 1
    for key in ['first', 'all']:
        group = next(g for g in result['strategies'] if g['id'] == key)
        assert group['overall']['sample_total'] == group['recent']['sample_total'] == 1
        assert group['overall']['avg'] == 2
        assert group['daily'][-1]['sample_total'] == 1
    assert result['strategies'][-2]['overall']['sample_total'] == 0
    assert result['strategies'][-1]['overall']['sample_total'] == 1
    assert all(r['bucket'] != '封板时间未知' for r in result['seal_curve'])
