"""Backfill exact-day L3 industry projections in the shared Eastmoney taxonomy.

Only publish a day after every L3 board's members have been verified. Full
taxonomy readers still require the full snapshot; no concepts are fabricated.
"""
from collections import defaultdict
from datetime import date, datetime, timezone

from pymongo import ReplaceOne

from shilun.market.taxonomy import (VERSION, SOURCE, CATALOG, MEMBERS, SNAPSHOTS,
    STOCKS, day, digest, normalize_catalog, normalize_members, stock_classifications)


def fetch_members(client, code, days):
    frame = client._call_api('dc_member', ts_code=code,
        start_date=days[0].replace('-', ''), end_date=days[-1].replace('-', ''))
    raw = frame.to_dict('records')
    if len(raw) >= 5000:
        if len(days) == 1:
            raise ValueError('单日行业成分达到截断上限')
        split = len(days) // 2
        return fetch_members(client, code, days[:split]) + fetch_members(client, code, days[split:])
    if any(r.get('ts_code') != code or not days[0] <= day(r.get('trade_date')) <= days[-1] for r in raw):
        raise ValueError('行业成分代码或日期与请求不符')
    stamp = datetime.now(timezone.utc).isoformat()
    return [{**r, 'fetched_at': stamp} for r in raw if day(r.get('trade_date')) in days]


def sync_industry_history(store, dates, *, client=None, progress=None):
    dates = sorted(set(dates))
    if not dates:
        return {'status': 'ok', 'dates': [], 'published': [], 'pending': [], 'diagnostics': []}
    if dates[-1] > date.today().isoformat():
        raise ValueError('不能同步未来行业日期')
    cal = store.find_trade_calendar(start_date=dates[0], end_date=dates[-1])
    opened = {day(r.get('cal_date')) for r in cal if str(r.get('is_open')) in ('1', 'True')}
    if any(d not in opened for d in dates):
        raise ValueError('行业历史同步只接受已核验交易日')
    ready = {r['date'] for r in store.collection(SNAPSHOTS).find(
        {'date': {'$in': dates}, 'version': VERSION, 'status': 'ready'},
        {'date': 1, 'scope': 1}) if r.get('scope', 'full') in ('full', 'industry_l3')}
    pending = [d for d in dates if d not in ready]
    report = {'status': 'ok', 'dates': dates, 'published': [], 'pending': [], 'diagnostics': []}
    if not pending:
        return report
    if client is None:
        from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
        client = TushareDailyClient(TushareConfig.from_env())
    catalogs = {}
    for d in pending:
        key = f'{VERSION}:{d}'
        catalog = store.collection(CATALOG).find_one({'_id': key})
        try:
            if not catalog:
                raw = client._call_api('dc_index', trade_date=d.replace('-', '')).to_dict('records')
                catalog = {'_id': key, 'date': d, 'rows': normalize_catalog(raw, d),
                           'fetched_at': datetime.now(timezone.utc).isoformat()}
                store.collection(CATALOG).replace_one({'_id': key}, catalog, upsert=True)
            leaves = [r for r in catalog['rows'] if r.get('industry_level') == 'l3']
            if not leaves:
                raise ValueError('目录没有三级行业')
            catalogs[d] = leaves
        except Exception as error:
            report['diagnostics'].append({'date': d, 'dataset': 'catalog', 'error_type': type(error).__name__})
        if progress:
            progress({'stage': 'catalog', 'date': d, 'done': len(catalogs), 'total': len(pending)})
    members = {(r['date'], r['code']): r for r in store.collection(MEMBERS).find(
        {'version': VERSION, 'date': {'$in': pending}})}
    requests = defaultdict(list)
    for d, entries in catalogs.items():
        for entry in entries:
            if (d, entry['code']) not in members:
                requests[entry['code']].append(d)
    for i, (code, days) in enumerate(sorted(requests.items()), 1):
        try:
            raw = fetch_members(client, code, days)
            operations, staged = [], {}
            for d in days:
                source = [r for r in raw if day(r['trade_date']) == d]
                normalized = normalize_members(source, code, d)
                entry = next(r for r in catalogs[d] if r['code'] == code)
                quoted = sum(float(entry['provider_fields'].get(k) or 0) for k in ('up_num', 'down_num'))
                if not normalized and quoted > 0:
                    raise ValueError('有行情行业返回空成分')
                doc = {'_id': f'{VERSION}:{d}:{code}:{digest(normalized)}', 'version': VERSION,
                       'date': d, 'code': code, 'members': normalized,
                       'fetched_at': max((r['fetched_at'] for r in source), default=datetime.now(timezone.utc).isoformat())}
                staged[(d, code)] = doc
                operations.append(ReplaceOne({'_id': doc['_id']}, doc, upsert=True))
            if operations:
                store.collection(MEMBERS).bulk_write(operations, ordered=False)
            members.update(staged)
        except Exception as error:
            report['diagnostics'].append({'code': code, 'dates': days, 'dataset': 'members', 'error_type': type(error).__name__})
        if progress and (i % 10 == 0 or i == len(requests)):
            progress({'stage': 'members', 'done': i, 'total': len(requests), 'errors': len(report['diagnostics'])})
    for d in pending:
        entries = catalogs.get(d, [])
        if not entries or any((d, r['code']) not in members for r in entries):
            report['pending'].append(d)
            continue
        rows = []
        for entry in entries:
            member = members[(d, entry['code'])]
            rows.append({**entry, 'members': member['members'], 'member_ref': member['_id'],
                'membership_date': d, 'member_count': len(member['members']),
                'fetched_at': member['fetched_at'], 'industry_parents': [],
                'parent_status': 'not_collected', 'historical_reconstruction': member['fetched_at'][:10] > d})
        revision = digest({'scope': 'industry_l3', 'members': [(r['code'], r['member_ref']) for r in rows]})
        stocks = stock_classifications({'date': d, 'rows': rows})
        operations = [ReplaceOne({'_id': f'{revision}:{ticker}'},
            {'_id': f'{revision}:{ticker}', 'ticker': ticker, 'revision': revision,
             'taxonomy_scope': 'industry_l3', **fields}, upsert=True) for ticker, fields in stocks.items()]
        if operations:
            store.collection(STOCKS).bulk_write(operations, ordered=False)
        key = f'{VERSION}:industry_l3:{d}'
        manifest = {'_id': key, 'version': VERSION, 'source': SOURCE, 'date': d,
            'membership_date': d, 'scope': 'industry_l3', 'status': 'ready', 'revision': revision,
            'member_refs': [r['member_ref'] for r in rows], 'stock_count': len(stocks),
            'catalog_count': len(rows), 'fetched_at': datetime.now(timezone.utc).isoformat(),
            'rows': [{k: v for k, v in r.items() if k not in ('members', 'provider_fields')} for r in rows]}
        store.collection(SNAPSHOTS).replace_one({'_id': key}, manifest, upsert=True)
        report['published'].append(d)
        if progress:
            progress({'stage': 'published', 'date': d, 'industries': len(rows), 'stocks': len(stocks)})
    report['status'] = 'partial' if report['pending'] else 'ok'
    return report
