"""Dated Eastmoney taxonomy shared by every research surface.

The full catalog is independent of P2/R2 ranking. Raw provider classifications
remain in their original collections; consumers join this catalog by DC ID.
Historical requests are reconstructions, not evidence of publication time.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

VERSION = 'eastmoney-taxonomy-v1'
SOURCE = '东方财富'
CATALOG = 'market_taxonomy_catalog'
MEMBERS = 'market_taxonomy_members'
SNAPSHOTS = 'market_taxonomy_snapshots'
STOCKS = 'market_taxonomy_stocks'
LEVELS = {'东财一级行业': 'l1', '东财二级行业': 'l2', '东财三级行业': 'l3'}
NON_TOPIC_NAMES = frozenset({'融资融券', '沪股通', '深股通', '证金持股', 'ST板块',
    '新股与次新股', '注册制次新股', '科创次新股', '中字头股票', '举牌', '摘帽'})
NON_TOPIC_PATTERN = re.compile(r'样本股|成[份分]股|^\d{4}.*(预增|预减|预盈|预亏)')


def day(value):
    value = str(value or '').replace('-', '')[:8]
    return f'{value[:4]}-{value[4:6]}-{value[6:]}' if len(value) == 8 and value.isdigit() else ''


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def normalize_catalog(raw, target):
    from shilun.market.research_scope import topic_allowed
    if not raw or len(raw) >= 5000:
        raise ValueError('东方财富分类目录为空或达到截断上限')
    rows, seen = [], set()
    for item in raw:
        code, name = item.get('ts_code', ''), str(item.get('name') or '').strip()
        if not re.fullmatch(r'BK\d+\.DC', code) or not name or day(item.get('trade_date')) != target or code in seen:
            raise ValueError('东方财富目录代码、日期或唯一性校验失败')
        seen.add(code)
        kind = {'概念板块': 'concept', '行业板块': 'industry', '地域板块': 'region'}.get(item.get('idx_type'))
        level = LEVELS.get(item.get('level'))
        if not kind or (kind == 'industry' and not level):
            raise ValueError('东方财富分类类型或行业层级发生变化')
        eligible = kind == 'concept' and name not in NON_TOPIC_NAMES and not NON_TOPIC_PATTERN.search(name)
        rows.append({'code': code, 'name': name, 'kind': kind, 'type': 'I' if kind == 'industry' else 'N' if kind == 'concept' else 'R',
            'industry_level': level, 'granularity': 'industry_leaf' if level == 'l3' else kind,
            'granularity_label': '三级行业' if level == 'l3' else '概念题材' if kind == 'concept' else item['level'] if level else '地域',
            'research_eligible': bool((eligible or level == 'l3') and topic_allowed({'name':name})),
            'source_label': SOURCE + ('概念' if kind == 'concept' else item['level'].replace('东财', '') if level else '地域'),
            'provider_fields': item})
    return rows


def normalize_members(raw, code, target):
    if len(raw) >= 5000:
        raise ValueError(f'{code} 成分达到接口截断上限')
    result, seen = [], set()
    for item in raw:
        ticker = str(item.get('con_code') or '').upper()
        if item.get('ts_code') != code or day(item.get('trade_date')) != target or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', ticker) or ticker in seen:
            raise ValueError(f'{code} 成分代码、日期或唯一性校验失败')
        seen.add(ticker)
        result.append({'con_code': ticker, 'name': item.get('name') or ticker})
    return sorted(result, key=lambda r: r['con_code'])


def attach_hierarchy(rows):
    """Resolve parents only when dated memberships identify a unique container.

    dc_index does not publish parent IDs. Ambiguities remain explicit; no
    name-prefix guesses or cross-provider mappings are permitted.
    """
    industries = [r for r in rows if r['kind'] == 'industry']
    sets = {r['code']: {m['con_code'] for m in r['members']} for r in industries}
    for row in rows:
        row['industry_parents'] = []
        row['parent_status'] = 'not_applicable'
        level = row.get('industry_level')
        if level not in ('l2', 'l3'):
            continue
        required = ('l1', 'l2') if level == 'l3' else ('l1',)
        ambiguous = []
        for parent_level in required:
            matches = [p for p in industries if p['industry_level'] == parent_level
                       and sets[row['code']] and sets[row['code']] <= sets[p['code']]]
            if len(matches) == 1:
                row['industry_parents'].append({k: matches[0][k] for k in ('code', 'name', 'industry_level')})
            else:
                ambiguous.append({'level': parent_level, 'candidate_ids': [p['code'] for p in matches]})
        row['parent_status'] = 'membership_inferred' if not ambiguous else 'unresolved'
        row['parent_diagnostics'] = ambiguous
    return rows


def load_taxonomy(store, target):
    manifest = store.collection(SNAPSHOTS).find_one({'_id': f'{VERSION}:{target}', 'status': 'ready'}, {'_id': 0})
    if not manifest:
        return {}
    documents = {r['_id']: r for r in store.collection(MEMBERS).find({'_id': {'$in': manifest['member_refs']}})}
    if len(documents) != len(manifest['member_refs']):
        return {}
    from shilun.market.research_scope import topic_allowed
    rows = [{**r, 'research_eligible': bool(r['research_eligible'] and topic_allowed(r)),
             'members': documents[r['member_ref']]['members']} for r in manifest['rows']]
    return {**manifest, 'rows': rows}


def sync_taxonomy(store, target, *, client=None, progress=None):
    """Resume dated sync; publish only after every catalog member request succeeds."""
    cached = load_taxonomy(store, target)
    if cached:
        return cached
    cal = store.find_trade_calendar(start_date=target, end_date=target)
    if not any(day(r.get('cal_date')) == target and str(r.get('is_open')) in ('1', 'True') for r in cal):
        raise ValueError('分类同步需要已核验的交易日，不能混入非交易日')
    if client is None:
        from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
        client = TushareDailyClient(TushareConfig.from_env())
    key = f'{VERSION}:{target}'
    catalog = store.collection(CATALOG).find_one({'_id': key})
    if not catalog:
        rows = normalize_catalog(client._call_api('dc_index', trade_date=target.replace('-', '')).to_dict('records'), target)
        catalog = {'_id': key, 'date': target, 'rows': rows, 'fetched_at': datetime.now(timezone.utc).isoformat()}
        store.collection(CATALOG).replace_one({'_id': key}, catalog, upsert=True)
    rows, missing = [], []
    for index, entry in enumerate(catalog['rows']):
        code = entry['code']
        member = store.collection(MEMBERS).find_one({'version': VERSION, 'date': target, 'code': code})
        try:
            if not member:
                data = normalize_members(client._call_api('dc_member', ts_code=code, trade_date=target.replace('-', '')).to_dict('records'), code, target)
                # A positively quoted board cannot legitimately have no members.
                quoted = sum(float(entry['provider_fields'].get(k) or 0) for k in ('up_num', 'down_num'))
                if not data and quoted > 0:
                    raise ValueError('有行情板块返回空成分')
                member = {'_id': f'{key}:{code}:{digest(data)}', 'version': VERSION, 'date': target,
                          'code': code, 'members': data, 'fetched_at': datetime.now(timezone.utc).isoformat()}
                store.collection(MEMBERS).replace_one({'_id': member['_id']}, member, upsert=True)
            rows.append({**entry, 'members': member['members'], 'member_ref': member['_id'],
                         'membership_date': target, 'member_count': len(member['members']),
                         'fetched_at': member['fetched_at'], 'taxonomy_version': VERSION,
                         'historical_reconstruction': member['fetched_at'][:10] > target})
        except Exception as error:
            missing.append({'code': code, 'error_type': type(error).__name__})
        if progress and (index % 25 == 0 or index + 1 == len(catalog['rows'])):
            progress({'date': target, 'done': index + 1, 'total': len(catalog['rows']), 'missing': len(missing)})
    if missing:
        # Keep any previously published revision intact. Staging is resumable.
        store.collection(CATALOG).update_one({'_id': key}, {'$set': {'missing_members': missing}})
        raise ValueError(f'东方财富 {len(missing)} 个板块成分待补齐；目录暂未发布')
    attach_hierarchy(rows)
    revision = digest([(r['code'], r['member_ref'], r['industry_parents']) for r in rows])
    manifest = {'date': target, 'version': VERSION, 'source': SOURCE, 'revision': revision, 'status': 'ready',
        'fetched_at': datetime.now(timezone.utc).isoformat(), 'member_refs': [r['member_ref'] for r in rows],
        'rows': [{k: v for k, v in r.items() if k not in ('members', 'provider_fields')} for r in rows],
        'catalog_count': len(rows), 'counts': dict(Counter(r['kind'] for r in rows)),
        'unresolved_parent_count': sum(r['parent_status'] == 'unresolved' for r in rows),
        'membership_date': target,
        'membership_note': f'东方财富 {target} 日期成分；采集时间单独记录。历史查询属于成分重建，未经当时发布时点验证。行业父级仅在同日成分唯一包含时关联，未确认关系保留缺口。'}
    from pymongo import ReplaceOne
    stock_rows = stock_classifications({**manifest, 'rows': rows})
    operations = [ReplaceOne({'_id': f'{revision}:{ticker}'},
        {'_id': f'{revision}:{ticker}', 'ticker': ticker, 'revision': revision, **fields}, upsert=True)
        for ticker, fields in stock_rows.items()]
    if operations:
        store.collection(STOCKS).bulk_write(operations, ordered=False)
    manifest['stock_count'] = len(stock_rows)
    manifest['stock_mapping_counts'] = dict(Counter(r['classification_status'] for r in stock_rows.values()))
    store.collection(SNAPSHOTS).replace_one({'_id': key}, {'_id': key, **manifest}, upsert=True)
    return {**manifest, 'rows': rows}


def stock_classifications(snapshot):
    buckets = defaultdict(list)
    for row in snapshot.get('rows', []):
        for member in row.get('members', []):
            buckets[member['con_code']].append(row)
    result = {}
    for ticker, rows in buckets.items():
        leaves = [r for r in rows if r.get('industry_level') == 'l3']
        primary = leaves[0] if len(leaves) == 1 else {}
        result[ticker] = {'industry': primary.get('name') or '分类待补齐', 'industry_code': primary.get('code'),
            'industry_source': SOURCE, 'taxonomy_version': VERSION, 'taxonomy_date': snapshot.get('date'),
            'classification_status': 'ready' if primary else 'industry_conflict' if leaves else 'industry_missing',
            'industry_candidates': [{'code': r['code'], 'name': r['name']} for r in leaves],
            'industry_parents': primary.get('industry_parents', []),
            'concepts': [{'code': r['code'], 'name': r['name']} for r in rows if r['kind'] == 'concept']}
    return result


def classify_stocks(store, target, records, *, snapshot=None, industry_only=False):
    records = list(records)
    if not records:
        return []
    if snapshot is None:
        manifest = store.collection(SNAPSHOTS).find_one({'_id': f'{VERSION}:{target}', 'status': 'ready'},
                                                     {'revision': 1, 'date': 1})
        if not manifest and industry_only:
            # A complete dated L3 projection shares the catalogue/member source,
            # but must never masquerade as a complete concept taxonomy.
            manifest = store.collection(SNAPSHOTS).find_one(
                {'_id': f'{VERSION}:industry_l3:{target}', 'status': 'ready', 'scope': 'industry_l3'},
                {'revision': 1, 'date': 1})
        tickers = [r.get('ts_code') or r.get('ticker') for r in records]
        lookup = {r['ticker']: {k: v for k, v in r.items() if k not in ('_id', 'revision', 'ticker')}
                  for r in store.collection(STOCKS).find({'revision': manifest['revision'], 'ticker': {'$in': tickers}})} if manifest else {}
        snapshot = manifest or {}
    else:
        lookup = stock_classifications(snapshot)
    result = []
    for row in records:
        ticker = row.get('ts_code') or row.get('ticker')
        classification = lookup.get(ticker) or {'industry': '分类待补齐', 'industry_code': None,
            'industry_source': SOURCE, 'taxonomy_version': VERSION, 'taxonomy_date': target,
            'classification_status': 'stock_unmapped' if snapshot else 'taxonomy_missing', 'concepts': [], 'industry_parents': []}
        result.append({**row, 'raw_industry': row.get('raw_industry', row.get('industry')), **classification})
    return result


def reconcile_dated_industries(store, target, records, *, sector_field=None):
    """Refresh display metadata after a same-day taxonomy repair, without writes.

    Qualification and selection snapshots keep their original facts. Their
    cached missing industry label must not hide a classification published
    later for that exact review day. L3 projections supply industries only;
    they never claim to provide event themes or a complete concept catalog.
    """
    records = list(records)
    if not records:
        return []
    fields = ('industry', 'industry_code', 'industry_source', 'taxonomy_version',
              'taxonomy_date', 'taxonomy_scope', 'classification_status')
    classified = classify_stocks(store, target, records, industry_only=True)
    result = []
    for original, fresh in zip(records, classified):
        known_same_day = (original.get('taxonomy_version') == VERSION
                          and original.get('taxonomy_date') == target
                          and original.get('classification_status') == 'ready')
        if fresh.get('classification_status') == 'taxonomy_missing' and known_same_day:
            # A temporary missing manifest cannot erase a verified same-day
            # industry. A newly reported mapping conflict still supersedes it.
            row = dict(original)
            if sector_field:
                row[sector_field] = original.get('industry') or '分类待补齐'
                row['sector_code'] = original.get('industry_code')
            result.append(row)
            continue
        row = {**original, **{key: fresh.get(key) for key in fields}}
        if sector_field:
            row[sector_field] = fresh.get('industry') or '分类待补齐'
            row['sector_code'] = fresh.get('industry_code')
        result.append(row)
    return result
