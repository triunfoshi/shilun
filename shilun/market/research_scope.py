"""Shared research exclusions; original source records remain unchanged."""
import re


def stock_allowed(row, target=None):
    name = re.sub(r'\s+', '', str(row.get('name') or '')).upper()
    if 'ST' in name or '退' in name or row.get('list_status') in ('D', 'P'):
        return False
    end = str(row.get('delist_date') or '').replace('-', '')[:8]
    return not (end and target and end <= target.replace('-', ''))


FACTOR_TOPIC_PATTERN = (
    r'^昨日|最近多板|历史新高|百日新高|百元股|连板股|强势股|涨停板|近期新高'
    r'|^20\d{2}.*(报|扭亏|首亏)|风格$|^[高中小大微]盘(精选|股|价值|成长)$'
    r'|^(券商金股|低市净率|高市净率|高成长股|题材股|反转股|趋势股|密集调研|股权分散|股权集中|近期摘帽|破增发价股|破发股|超跌股|权重股|行业龙头|微利股|红利股|价值股|周期股|东方财富热股|红利破净股|长期破净|破净股|低价股|宁组合|茅指数|标准普尔|GDR|富时罗素|养老金|MSCI中国|深证100R|创业板综|上证380|中证500|创业成份|B股|上证180_|上证50_|央视50_|深成500|机构重仓|基金重仓|QFII重仓|转债标的|社保重仓|ST股|次新股|HS300_|AH股|AB股|科创板做市股)$'
)


def topic_allowed(row):
    name = str(row.get('name') or row.get('sector_base_name') or row.get('sector_name') or '')
    # Performance screens describe the selected prices themselves, not a theme.
    return not re.search(FACTOR_TOPIC_PATTERN, name)
