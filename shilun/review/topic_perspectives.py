"""Rank concept/industry perspectives first, then compare their actual members.

Production reads the independent Eastmoney taxonomy. Historical reconstructions
never masquerade as point-in-time backtests; legacy helpers are offline-only.
"""
from collections import defaultdict
from datetime import datetime
import re
from statistics import median

from shilun.review.board_data import CHINA, BoardResearchData, _day, today
from shilun.review.sources import finite_number as num

COLLECTION = 'review_topic_perspectives'
MEMBERS = 'review_topic_members'
CATALOG = 'review_topic_catalog'
VERSION = 'perspectives-v5-core5'

# ths_index marks some index baskets and trading/ownership tags as N too.
# These are explicit review-scope exclusions, not provider industry levels.
NON_TOPIC_NAMES = frozenset({
    '融资融券', '沪股通', '深股通', '证金持股', '国家大基金持股', 'ST板块',
    '新股与次新股', '注册制次新股', '科创次新股', '高股息精选', '专精特新',
    '中字头股票', '举牌', '摘帽', '超级品牌', '独角兽概念', '中国AI 50',
})
NON_TOPIC_PATTERN = re.compile(r'样本股|成[份分]股|^同花顺|^\d{4}.*(预增|预减)')


def ths_topic_kind(row):
    """Provider concepts have no L1/L2/L3 hierarchy; 884 is the fine industry family."""
    if row.get('type') == 'N':
        name = row.get('name', '')
        return 'concept' if name not in NON_TOPIC_NAMES and not NON_TOPIC_PATTERN.search(name) else None
    if row.get('type') == 'I' and row.get('ts_code', '').startswith('884'):
        return 'industry_leaf'
    return None


def short_list(catalog, daily, limit=20):
    """Separate concept/industry screens; union today's and 3-session leaders."""
    by_code = defaultdict(list)
    for day in daily:
        for row in day:
            if num(row.get('pct_change')) is not None:
                by_code[row['ts_code']].append(float(row['pct_change']))
    selected = {}
    for kind in ('N', 'I'):
        rows = [r for r in catalog if r.get('type') == kind and r['ts_code'] in by_code
                and (num(r.get('count')) or 0) >= 5
                and ths_topic_kind(r)]
        for key in (lambda r: by_code[r['ts_code']][-1], lambda r: sum(by_code[r['ts_code']][-3:])):
            for row in sorted(rows, key=key, reverse=True)[:limit]:
                selected[row['ts_code']] = row
    return list(selected.values())


def rank_perspectives(candidates, current_bars, previous_bars, pools, sessions, target):
    prior_dates = [d for d in sessions if d < target][-7:]
    current_pool = {r['ticker']: r for r in pools.get(target, [])}
    previous = prior_dates[-1] if prior_dates else ''
    results = []
    for candidate in candidates:
        members = {r['con_code']: r for r in candidate['members']}
        returns = {t: (num(current_bars.get(t, {}).get('close')) / base - 1)
                   for t in members if (base := num(current_bars.get(t, {}).get('pre_close')) or num(previous_bars.get(t, {}).get('close')))
                   and num(current_bars.get(t, {}).get('close')) is not None}
        if len(returns) < 5 or len(returns) / max(1, len(members)) < .8:
            continue
        active = {r['ticker'] for d in prior_dates for r in pools.get(d, []) if r['ticker'] in members}
        today_codes = set(current_pool) & set(members)
        coverage = len(returns) / len(members)
        size = len(returns)
        results.append({**candidate, 'member_count': len(members), 'price_coverage': coverage,
            'return_median': median(returns.values()), 'up_ratio': sum(v > 0 for v in returns.values()) / size,
            'limit_up_count': len(today_codes), 'limit_up_ratio': len(today_codes) / len(members),
            'active_codes': sorted(active | today_codes), 'previous_date': previous,
            'feedback_median': median([returns[t] for t in active if t in returns]) if active & returns.keys() else None})
    # Percentile components keep currencies, percentages and member counts from
    # competing on arbitrary raw scales. Equal weights are an engineering rule.
    fields = ('return_median', 'up_ratio', 'limit_up_ratio', 'feedback_median')
    for row in results:
        components = []
        for field in fields:
            value = row[field]
            peers = [r[field] for r in results if r[field] is not None and r['type'] == row['type']]
            components.append(sum(v <= value for v in peers) / len(peers) if value is not None and peers else None)
        row['strength_score'] = round(100 * sum(v for v in components if v is not None) / len(fields), 1)
        row['score_coverage'] = sum(v is not None for v in components)
    results.sort(key=lambda r: (r['strength_score'], r['limit_up_count']), reverse=True)
    selected = []
    for row in results:
        a = set(row['active_codes'])
        duplicate = next((prior for prior in selected if len(a & set(prior['active_codes'])) >= 3
                          and len(a & set(prior['active_codes'])) / max(1, min(len(a), len(prior['active_codes']))) >= .75), None)
        row['selected'] = duplicate is None
        row['overlap_with'] = duplicate['name'] if duplicate else None
        if duplicate is None:
            selected.append(row)
    return results


def sync_perspectives(store, target):
    from shilun.market.taxonomy import sync_taxonomy
    data = BoardResearchData(store)
    sessions = data.sessions(target, 7)
    if len(sessions) < 4 or sessions[-1] != target:
        raise ValueError('题材筛选需要所选日期及此前交易日日历')
    taxonomy = sync_taxonomy(store, target)
    candidates = [r for r in taxonomy['rows'] if r['research_eligible'] and r['member_count'] >= 5]
    tickers = sorted({r['con_code'] for c in candidates for r in c['members']})
    bars = list(store.collection('market_daily_bars').find({'date': {'$in': sessions[-2:]}, 'ticker': {'$in': tickers}},
        {'_id': 0, 'ticker': 1, 'date': 1, 'close': 1, 'pre_close': 1}))
    by_date = {d: {r['ticker']: r for r in bars if r['date'] == d} for d in sessions[-2:]}
    pools = {d: p['stocks'] for d, p in data.pools(sessions).items()}
    ranks = rank_perspectives(candidates, by_date[target], by_date[sessions[-2]], pools, sessions, target)
    if not ranks:
        raise ValueError('没有成分和行情同时可核验的题材视角，保留原结果')
    result = {'date': target, 'version': VERSION, 'fetched_at': datetime.now(CHINA).isoformat(),
        'taxonomy_version': taxonomy['version'], 'taxonomy_revision': taxonomy['revision'],
        'perspectives': ranks, 'shortlist_count': len(candidates), 'missing_members': [],
        'screening_mode': 'member_direct', 'missing_index_dates': [],
        'membership_date': target, 'historical_reconstruction': any(r['historical_reconstruction'] for r in candidates),
        'rule': '全站共用东方财富概念与三级行业；一级、二级仅作归属背景。完整分类目录独立保存，题材筛选不改变目录。至少5只有行情成分且覆盖≥80%；短线观察需有已获五板资格且近7日活跃的核心，以及跟随股。完整成分不再按50家封顶。',
        'membership_note': taxonomy['membership_note']}
    store.collection(COLLECTION).replace_one({'date': target, 'version': VERSION}, result, upsert=True)
    return result


def load_perspectives(store, target):
    return store.collection(COLLECTION).find_one({'date': target, 'version': VERSION}, {'_id': 0}) or {}
