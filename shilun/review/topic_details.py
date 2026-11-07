"""Store full topic evidence separately so the review stays below BSON limits."""
from uuid import uuid4
from datetime import datetime, timezone
from pymongo import ReplaceOne

COLLECTION = 'review_topic_details'


def refresh_topic_names(store, themes):
    """Fill missing labels in old snapshots; preserve historical names and facts."""
    missing = {}
    for theme in themes:
        for row in theme.get('front_support', {}).get('rows', []):
            ticker = str(row.get('ticker') or '').strip().upper()
            name = str(row.get('name') or '').strip()
            if ticker and (not name or name.upper() in {ticker, ticker.split('.')[0]}):
                missing.setdefault(ticker, []).append(row)
    if not missing:
        return
    tickers = sorted(missing)
    for basic in store.collection('stock_basic').find(
        {'$or': [{'ts_code': {'$in': tickers}}, {'ticker': {'$in': tickers}}]},
        {'_id': 0, 'ts_code': 1, 'ticker': 1, 'name': 1},
    ):
        ticker = str(basic.get('ts_code') or basic.get('ticker') or '').strip().upper()
        name = str(basic.get('name') or '').strip()
        if name and name.upper() not in {ticker, ticker.split('.')[0]}:
            for row in missing.get(ticker, []):
                row['name'] = name


def split_topic_details(store, payload):
    themes = payload.get('sentiment', {}).get('theme_evidence', {}).get('themes', [])
    revision = uuid4().hex
    writes = []
    for index, theme in enumerate(themes):
        if index < 3:
            continue
        detail_id = f"{payload['analysis_date']}:{revision}:{theme['code']}"
        writes.append(ReplaceOne({'_id': detail_id}, {'_id': detail_id,
            'analysis_date': payload['analysis_date'], 'revision': revision,
            'created_at': datetime.now(timezone.utc), 'theme': theme}, upsert=True))
        # Keep rank, gates and aggregate support in the main snapshot. Full
        # members are loaded only when the user selects that particular topic.
        themes[index] = {**theme, 'detail_id': detail_id,
            'front_support': {k: v for k, v in theme.get('front_support', {}).items() if k != 'rows'}}
    if writes:
        store.collection(COLLECTION).bulk_write(writes, ordered=False)
    return revision


def refresh_topic_attention(store, evidence, target):
    """Upgrade cached display order without rebuilding unrelated market data."""
    from shilun.review.topic_attention import rank_theme_attention, VERSION, RULE, MAX_REVIEW_MEMBERS
    if evidence.get('attention_version') == VERSION or not evidence.get('themes'):
        return
    themes = rank_theme_attention(evidence['themes'], target)
    ids = [r['detail_id'] for r in themes[:3] if r.get('detail_id')]
    details = {r['_id']: r['theme'] for r in store.collection(COLLECTION).find({'_id': {'$in': ids}})} if ids else {}
    for index, theme in enumerate(themes[:3]):
        full = details.get(theme.get('detail_id'))
        if full:
            themes[index] = {**full, **theme, 'front_support': full.get('front_support', {})}
            themes[index].pop('detail_id', None)
    evidence.update(themes=themes, active_themes=[r['name'] for r in themes if r['selected']][:3],
                    attention_version=VERSION, attention_rule=RULE, member_limit=MAX_REVIEW_MEMBERS)
