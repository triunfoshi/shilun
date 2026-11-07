"""Review SW leaf industries; retain level-1/2 only as parent context."""
from collections import defaultdict
from shilun.review.board_data import today

COLLECTION = 'review_industry_members'


def industry_candidates(store, client, target):
    known_at = today()
    cached = store.collection(COLLECTION).find_one({'date': known_at}, {'_id': 0})
    if cached:
        rows = cached['rows']
    else:
        catalog = client._call_api('index_classify', level='L1', src='SW2021').to_dict('records')
        if not catalog: raise ValueError('申万一级行业目录为空')
        rows = []
        for industry in catalog:
            code = industry['index_code']
            members = client._call_api('index_member_all', l1_code=code, is_new='Y').to_dict('records')
            if not members or len(members) >= 2000 or any(r.get('l1_code') != code for r in members):
                raise ValueError('申万行业成分不完整，保留已有目录')
            rows.extend(members)
        store.collection(COLLECTION).replace_one({'date': known_at}, {'date': known_at, 'rows': rows}, upsert=True)
    groups = defaultdict(dict)
    parents = {}
    for row in rows:
        code, name = row.get('l3_code'), row.get('l3_name')
        if not isinstance(code, str) or not isinstance(name, str) or not row.get('ts_code'):
            continue
        groups[(code, name)][row['ts_code']] = {'con_code': row['ts_code'], 'con_name': row['name'],
            'in_date': row.get('in_date'), 'out_date': row.get('out_date')}
        parents[code] = [{'code': row[level+'_code'], 'name': row[level+'_name'], 'level': level}
                         for level in ('l1', 'l2') if row.get(level+'_code') and row.get(level+'_name')]
    if not groups:
        raise ValueError('申万三级行业成分缺失，不回退到一级或二级行业')
    candidates = []
    for (code, name), members in groups.items():
        if len(members) < 5: continue
        values = list(members.values())
        store.collection('review_topic_members').replace_one({'code': code, 'date': known_at},
            {'code': code, 'date': known_at, 'members': values}, upsert=True)
        candidates.append({'code': code, 'name': name, 'type': 'I', 'members': values,
            'industry_level': 'l3', 'source_label': '申万三级行业',
            'granularity': 'industry_leaf', 'granularity_label': '细分行业', 'industry_parents': parents[code],
            'membership_date': known_at, 'historical_reconstruction': known_at > target})
    return candidates
