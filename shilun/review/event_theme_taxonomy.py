"""Dated Vibe event labels for shortline cycles, never industry membership.

The catalog and observation membership are rebuilt from the available prefix of
closing limit-up reasons. A stock's old event label does not assert that today's
limit-up has the same catalyst: each event still requires its own dated reason.
"""
from collections import defaultdict
from hashlib import sha256

from shilun.review.vibe_shortline import clean_reason, tags_of

VERSION = 'vibe-event-themes-v1'
SCOPE_NOTE = ('题材按同日涨停原因拆分事件标签，与Vibe-Astock一致；过滤纯属性词，不合并行业、'
              'DC概念或旧主线。观察成员仅为截至复盘日曾明确归因于该事件的涨停股，不代表全板块成分。')


def topic_code(name):
    return 'EVENT.' + sha256(name.encode('utf8')).hexdigest()[:16]


def daily_direction_rows(pool, reason_doc, day):
    """Current event candidates for downstream plans, with honest sample scope."""
    if (pool.get('date') != day or not pool.get('complete') or pool.get('snapshot_mode') == 'live'
            or reason_doc.get('date') != day):
        return []
    members = defaultdict(set)
    for stock in pool.get('stocks', []):
        for label in set(tags_of(clean_reason(reason_doc.get('reasons', {}).get(stock['ticker'][:6], '')))):
            members[label].add(stock['ticker'])
    return [{'code': topic_code(label), 'name': label, 'taxonomy_kind': VERSION,
             'research_eligible': True, 'membership_date': day,
             'members': [{'con_code': ticker} for ticker in sorted(tickers)],
             'membership_scope': 'same_day_attributed_limit_up_sample', 'scope_note': SCOPE_NOTE}
            for label, tickers in sorted(members.items())]


def event_attribution(topic, reason, day):
    tags = tags_of(clean_reason(reason))
    status = 'matched' if topic['name'] in tags else 'missing' if not str(reason or '').strip() else 'unmatched'
    return {'status': status, 'date': day, 'version': VERSION,
            'matches': [topic['name']] if status == 'matched' else [],
            'explanation': '同日原因包含该事件标签' if status == 'matched' else
                           '同日涨停原因缺失，不能判断题材归属' if status == 'missing' else '同日原因未包含该事件标签'}


def collect_event_taxonomy(market):
    """Build every dated snapshot from its prefix, with stable event IDs.

    Existing frozen cohorts retain past members; new diffusion is counted from
    today's exact tag. Missing reasons remain unknown across topics because their
    event membership cannot be inferred from industry or a later reason string.
    """
    members, snapshots, diagnostics = defaultdict(set), {}, []
    reasons_by_stock = defaultdict(list)
    sessions = market['sessions']
    for day in market['context_days']:
        pool = market.get('pools', {}).get(day, {})
        ready = (pool.get('date') == day and pool.get('complete')
                 and pool.get('snapshot_mode') != 'live')
        doc = market.get('reasons', {}).get(day, {})
        reasons = doc.get('reasons', {}) if doc.get('date') == day else {}
        missing = []
        if ready:
            for row in pool.get('stocks', []):
                ticker = row['ticker']
                reason = reasons.get(ticker[:6], '')
                if not str(reason or '').strip():
                    missing.append(ticker)
                    continue
                labels = list(dict.fromkeys(tags_of(clean_reason(reason))))
                reasons_by_stock[ticker].append((day, labels))
                for label in labels:
                    members[label].add(ticker)
        active_days = sessions[max(0, sessions.index(day)-6):sessions.index(day)+1]
        cores = {r['ticker']: r for r in market.get('cores', {}).get(day, [])
                 if r.get('status') == 'qualified' and r.get('qualified_at')
                 and r['qualified_at'] <= day and r.get('date') == day}
        active_cores = {t: r for t, r in cores.items() if r.get('latest_limit_date') in active_days}
        unmapped = [t for t, row in active_cores.items()
                    if not any(d in active_days and tags for d, tags in reasons_by_stock[t])]
        rows = [{'code': topic_code(label), 'name': label, 'taxonomy_kind': VERSION,
                 'research_eligible': True, 'membership_date': day,
                 'membership_complete': bool(ready), 'member_count': len(tickers),
                 'reason_coverage_complete': bool(ready) and not missing,
                 'membership_scope': 'dated_attributed_limit_up_observation',
                 'has_qualified_core': bool(set(active_cores) & tickers),
                 'members': [{'con_code': ticker} for ticker in sorted(tickers)],
                 'source_topics': [], 'scope_note': SCOPE_NOTE}
                for label, tickers in sorted(members.items())]
        snapshots[day] = {'date': day, 'topic_schema': VERSION, 'rows': rows,
                          'core_mapping_complete': bool(ready) and not unmapped,
                          'pool_complete': bool(ready), 'missing_reason_tickers': missing,
                          'reason_coverage_complete': bool(ready) and not missing,
                          'unmapped_cores': unmapped, 'scope_note': SCOPE_NOTE}
        if not ready or missing or unmapped:
            diagnostics.append({'date': day, 'dataset': 'event_themes',
                                'pool_missing': not ready, 'missing_reason_tickers': missing,
                                'unmapped_cores': unmapped})
    return {'version': VERSION, 'topic_schema': VERSION, 'snapshots': snapshots,
            'diagnostics': diagnostics, 'scope_note': SCOPE_NOTE}
