"""Fallback dated labels; actual concept/industry members live in topic_perspectives."""
def topic_scope(row):
    for key in ('theme_name', 'concept_name', 'topic'):
        if row.get(key):
            return str(row[key]).strip(), 'dated_topic', '当日题材字段'
    industry = str(row.get('sector_name') or row.get('industry') or '').strip()
    return industry or '未分类', 'industry_proxy', '行业线索，非已确认题材'


def topic_name(row):
    return topic_scope(row)[0]
