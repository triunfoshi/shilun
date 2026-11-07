"""Review priority from recent core history; never a buy-strength score."""
from shilun.review.sources import finite_number as num

VERSION = 'topic-attention-3-core5'
MAX_REVIEW_MEMBERS = None
RULE = ('先排除“昨日连板、历史新高”等行情筛选标签，只看成分名单完整的真实题材和细分行业，不再按50家封顶。'
        '先看已取得五板资格、最近7天仍有涨停活动的核心。比较这些核心近20个交易日还保留了多少涨幅；'
        '启动不足20天就从启动日算起。涨幅相近时，再看活跃天数、跟随股的连板数量，最后看今天表现。'
        '同一核心出现在多个题材里，先展示活跃成员更集中的那个，其他标签展开查看。'
        '今天没涨停不立即删除，仍看近7天的延续。缺数据会标明，不按0计算。排序只帮助安排复盘顺序，不表示可以买入。')


def review_member_count(track):
    """Use complete membership, never the limit-up or front-row sample size."""
    if 'members' in track:
        return len({r['con_code'] for r in track['members'] if r.get('con_code')})
    count = num(track.get('member_count'))
    return int(count) if count is not None and count >= 0 and count == int(count) else None


def within_review_member_limit(track):
    count = review_member_count(track)
    from shilun.market.research_scope import topic_allowed
    return count is not None and count > 0 and topic_allowed(track)


def attention_core(candidates, target):
    cores = []
    for candidate in candidates:
        series = sorted((r for r in candidate.get('series', []) if r['date'] <= target), key=lambda r: r['date'])
        if not series or series[-1]['date'] != target or not candidate.get('core_qualified_at'):
            continue
        if candidate.get('latest_board_date') not in {r['date'] for r in series[-7:]}:
            continue
        tail = series[-20:]
        values = [num(r.get('cum_return')) for r in tail]
        base = num(series[-21].get('cum_return')) if len(series) > 20 else 0
        if base is None or base <= -1 or any(v is None or v <= -1 for v in values):
            continue
        gain = (1 + values[-1]) / (1 + base) - 1
        cores.append({k: candidate.get(k) for k in ('ticker', 'name', 'max_board', 'price_basis', 'core_qualified_at')} | {
            'return': gain, 'sessions': len(tail), 'date_from': tail[0]['date'], 'date_to': target,
            'active_days': candidate.get('active_board_days', 0),
            'drawdown': (1 + values[-1]) / (1 + max(values)) - 1})
    return max(cores, key=lambda r: (r['return'], r['active_days'], r['max_board'], r['ticker']), default=None)


def rank_theme_attention(tracks, target):
    """Keep original scopes and today's strength; fold duplicate core exposure."""
    tracks = [track for track in tracks if within_review_member_limit(track)]
    for track in tracks:
        round_info = track.get('round', {})
        core = round_info.get('attention_core')
        if not core or core.get('date_to') != target or not core.get('core_qualified_at'):
            core = attention_core(round_info.get('leaders', []), target)
        track['attention_core'] = core
        track['active_density'] = len(set(track.get('active_codes', []))) / max(1, track.get('member_count') or 0)
        track.update(selected=True, overlap_with=None, overlap_reason=None, attention_rank=None)

    # A single popular stock may belong to many unrelated tags. Choose a
    # representative for screen space only, retaining each original boundary.
    ordered = sorted(tracks, key=lambda r: (-r['active_density'], -(r.get('two_plus_stock_count_7d') or 0),
        -(r.get('strength_score') or 0), r.get('code', r['name'])))
    by_core = {}
    for track in ordered:
        core = track['attention_core']
        if not core:
            continue
        duplicate = by_core.get(core['ticker'])
        if duplicate:
            track.update(selected=False, overlap_with=duplicate['name'], overlap_reason='相同核心，保留独立成分')
        else:
            by_core[core['ticker']] = track

    def priority(track):
        core = track['attention_core'] or {}
        gain = num(core.get('return'))
        return (gain is not None, gain if gain is not None else -2, core.get('active_days') or 0,
                track.get('two_plus_stock_count_7d') or 0, track.get('strength_score') or 0)

    ordered.sort(key=priority, reverse=True)
    selected = []
    for track in ordered:
        if not track['selected']:
            continue
        active = set(track.get('active_codes', []))
        duplicate = next((p for p in selected if len(active & set(p.get('active_codes', []))) >= 3
            and len(active & set(p['active_codes'])) / max(1, min(len(active), len(p['active_codes']))) >= .75), None)
        if duplicate:
            track.update(selected=False, overlap_with=duplicate['name'], overlap_reason='活跃成员重合至少75%')
        else:
            selected.append(track)
            track['attention_rank'] = len(selected)
    ordered.sort(key=lambda r: (r['selected'], priority(r)), reverse=True)
    return ordered
