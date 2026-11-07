"""Explicit research families; original dated DC categories remain intact."""
from copy import deepcopy

VERSION = 'theme-families-v1'
RETAIL = 'THEME.RETAIL'
FAMILIES = {RETAIL: {'name': '零售主线', 'codes': ('BK0825.DC', 'BK1655.DC')}}


def add_family_views(catalogs, members):
    """Require both catalog identities; absent membership never becomes an empty set."""
    for day, catalog in catalogs.items():
        for code, family in FAMILIES.items():
            if not all(c in catalog for c in family['codes']):
                continue
            sources = [{'code': c, 'name': catalog[c]['name']} for c in family['codes']]
            complete = all((day, c) in members for c in family['codes'])
            union = set().union(*(members[day, c] for c in family['codes'])) if complete else None
            if complete:
                members[day, code] = union
            catalog[code] = {'code': code, 'name': family['name'], 'research_eligible': True,
                            'has_qualified_core': any(catalog[c].get('has_qualified_core') for c in family['codes']),
                            'member_count': len(union) if union is not None else None,
                            'membership_complete': complete, 'membership_date': day,
                            'source_topics': sources, 'family_version': VERSION}


def visible_topics(catalog):
    merged = {source['code'] for row in catalog.values() for source in row.get('source_topics', [])}
    return {code: row for code, row in catalog.items() if code not in merged}


def family_events(replay, code, day):
    sources = replay.catalogs.get(day, {}).get(code, {}).get('source_topics')
    if not sources:
        return None
    result = {'events': [], 'unknown': [], 'membership_only': [], 'complete': True}
    by_ticker = {}
    priority = {'events': 0, 'unknown': 1, 'membership_only': 2}
    for source in sources:
        evidence = replay.events(source['code'], day)
        result['complete'] &= evidence['complete']
        for bucket in priority:
            for event in evidence[bucket]:
                by_ticker.setdefault(event['ticker'], []).append((bucket, source, event))
    for ticker in sorted(by_ticker):
        records = sorted(by_ticker[ticker], key=lambda r: (priority[r[0]], r[1]['code']))
        bucket, _, event = records[0]
        event = deepcopy(event)
        event['source_topics'] = [source for _, source, _ in records]
        event['source_attributions'] = [{**source, 'bucket': b, 'attribution': e.get('attribution')}
                                        for b, source, e in records]
        # A match in one verified source suffices; an unrelated source cannot
        # overwrite it. One ticker/date still contributes just one event.
        result[bucket].append(event)
    return result
