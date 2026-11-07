"""R2 reads the complete dated catalog, independently of P2 qualification."""
from collections import Counter
from shilun.market.taxonomy import VERSION, SOURCE


def prepare_memberships(snapshot, analysis_date):
    if snapshot.get('date') != analysis_date or snapshot.get('version') != VERSION or snapshot.get('status') != 'ready':
        raise ValueError('所选日东方财富统一目录尚未完成同步；请检查分类同步任务。')
    from shilun.market.research_scope import topic_allowed
    accepted = [r for r in snapshot.get('rows', []) if r.get('research_eligible') and topic_allowed(r) and r.get('code', '').endswith('.DC')]
    names = Counter(r['name'] for r in accepted)
    result = {}
    for row in accepted:
        members = sorted({r['con_code'] for r in row.get('members', []) if r.get('con_code')})
        if len(members) < 5:
            continue
        code = row['code']
        if code in result:
            raise ValueError('统一分类目录代码重复')
        label = row['name'] if names[row['name']] == 1 else f"{row['name']} · {row['granularity_label']}"
        result[code] = {'sector_code': code, 'sector_name': label, 'sector_source': row['source_label'],
            'sector_base_name': row['name'], 'sector_type': row['type'], 'industry_level': row.get('industry_level'),
            'granularity': row.get('granularity'), 'industry_parents': row.get('industry_parents', []),
            'membership_date': snapshot['date'], 'taxonomy_version': VERSION,
            'historical_reconstruction': bool(row.get('historical_reconstruction')),
            'member_count': len(members), 'member_tickers': members}
    if not result:
        raise ValueError('东方财富目录缺少可核验的概念或三级行业成分')
    return result
