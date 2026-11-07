"""Reproducible market evidence and explicit conditional plans (no orders).

Percentages are percentage points; amounts are yuan. No retrospectively chosen
core basket is used as a historical crowding cohort. All price reads are bounded
by the analysis date, including when an input accidentally contains future rows.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean, median
import math

from shilun.market.ma5_liquidity import market_turnover
from shilun.market.ma5_universe import iso, scan_universe, unique_rows
from shilun.market.research_scope import topic_allowed

VERSION = 'decision-pilot-event-v5'
BAND_NAMES = {'lt50': '50亿以下', '50to100': '50—100亿', 'ge100': '100亿及以上', 'unknown': '7日窗口缺失'}
INDICES = {'000001.SH': '上证指数', '000300.SH': '沪深300', '399001.SZ': '深证成指', '399006.SZ': '创业板指'}
HARDWARE = {'存储芯片': ('存储芯片',), '半导体设备': ('半导体设备',), 'MLCC': ('MLCC',),
            'PCB': ('PCB',), 'CPO': ('CPO概念',), '交换机': ('交换机',), '光纤': ('光纤概念',)}


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def ratio(a, b):
    return a / b if number(a) and number(b) and b > 0 else None


def ret(a, b):
    r = ratio(a, b)
    return (r - 1) * 100 if r is not None else None


def amount_band(amount):
    if not number(amount) or amount <= 0:
        return 'unknown'
    return 'lt50' if amount < 5e9 else '50to100' if amount < 1e10 else 'ge100'


def stats(values):
    good = [v for v in values if number(v)]
    return {'n': len(good), 'missing': len(values) - len(good),
            'mean': mean(good) if good else None, 'median': median(good) if good else None}


def valid_bar(r):
    return bool(r) and all(number(r.get(k)) and r[k] > 0 for k in ('open', 'high', 'low', 'close', 'volume')) and r['low'] <= min(r['open'], r['close']) <= max(r['open'], r['close']) <= r['high']


def price_path_valid(path):
    # Corporate-action days require adjustment factors not present in this intake.
    # Do not silently mix raw prices across a changed reference price or suspension.
    return all(valid_bar(r) for r in path) and all(number(b.get('pre_close')) and abs(b['pre_close'] - a['close']) <= .011 for a, b in zip(path, path[1:]))


class Evidence:
    def __init__(self, data):
        self.date = data['date']
        self.days = [d for d in data['sessions'] if d <= self.date]
        if len(self.days) < 24 or self.days != sorted(set(self.days)) or self.days[-1] != self.date:
            raise ValueError('需要至少24个连续、唯一且截至所选日的交易日')
        self.bars = [r for r in data['bars'] if iso(r.get('date')) in self.days]
        self.basic = [r for r in data['daily_basic'] if iso(r.get('trade_date')) in self.days]
        self.limits = [r for r in data['limits'] if iso(r.get('trade_date')) in self.days]
        for rows, keys in ((self.bars, ('ticker', 'date')), (self.basic, ('ts_code', 'trade_date')), (self.limits, ('ticker', 'trade_date'))):
            unique_rows(rows, keys, 'decision evidence')
        self.bar = {(r['ticker'], iso(r['date'])): r for r in self.bars}
        self.db = {(r['ts_code'], iso(r['trade_date'])): r for r in self.basic}
        self.limit = {(r['ticker'], iso(r['trade_date'])): r.get('up_limit') for r in self.limits}
        self.info = {r['ts_code']: r for r in data['basics'] if not r.get('list_date') or iso(r['list_date']) <= self.date}
        self.suspensions = {t:set(days) for t,days in (data.get('core_history') or {}).get('suspensions',{}).items()}
        self.flow = {(r['ts_code'], iso(r['trade_date'])): r for r in data.get('flows', []) if iso(r['trade_date']) <= self.date}

    def row(self, ticker, day):
        return self.bar.get((ticker, day), {})

    def avg_amount(self, ticker, i):
        ds = self.days[max(0, i - 6):i + 1]
        vals = [self.row(ticker, d).get('amount') for d in ds]
        return mean(vals) * 1000 if len(ds) == 7 and all(number(v) and v > 0 for v in vals) else None

    def stock(self, ticker):
        row = self.row(ticker, self.date)
        i = len(self.days) - 1
        ma = lambda n: mean([self.row(ticker, d)['close'] for d in self.days[-n:]]) if all(valid_bar(self.row(ticker, d)) for d in self.days[-n:]) else None
        histories = []
        for d in self.days[-5:]:
            idx = self.days.index(d)
            r = self.row(ticker, d)
            prev = [self.row(ticker, p) for p in self.days[max(0, idx - 10):idx]]
            base_turn = [self.db.get((ticker, p), {}).get('turnover_rate') for p in self.days[max(0, idx - 10):idx]]
            turn = self.db.get((ticker, d), {}).get('turnover_rate')
            histories.append({'date': d, 'return_pct': ret(r.get('close'), r.get('pre_close')),
                'red_k': r['close'] > r['open'] if valid_bar(r) else None,
                'amount_cny': r.get('amount', 0) * 1000 if number(r.get('amount')) else None,
                'volume_lots': r.get('volume'), 'turnover_pct': turn,
                'turnover_ratio_10': ratio(turn, mean(base_turn)) if len(base_turn) == 10 and all(number(x) for x in base_turn) else None,
                'volume_ratio_10': ratio(r.get('volume'), mean([x['volume'] for x in prev])) if len(prev) == 10 and all(valid_bar(x) for x in prev) else None,
                'one_price': r['high'] == r['low'] if valid_bar(r) else None})
        flow = self.flow.get((ticker, self.date), {})
        net = flow.get('net_mf_amount')
        vals = [flow.get(k) for k in ('buy_lg_amount', 'buy_elg_amount', 'sell_lg_amount', 'sell_elg_amount')]
        return {'ticker': ticker, 'name': self.info.get(ticker, {}).get('name', ticker),
            'close': row.get('close'), 'return_pct': ret(row.get('close'), row.get('pre_close')),
            'avg7_cny': self.avg_amount(ticker, i), 'amount_band': amount_band(self.avg_amount(ticker, i)),
            'ma5': ma(5), 'ma10': ma(10), 'ma20': ma(20), 'previous_low': row.get('low'), 'previous_high': row.get('high'),
            'net_flow_cny': net * 10000 if number(net) else None,
            'large_net_cny': (vals[0] + vals[1] - vals[2] - vals[3]) * 10000 if all(number(x) for x in vals) else None,
            'history': histories}

    def events(self):
        events, latest_streaks = [], {}
        for ticker in self.info:
            streak = None
            for i, d in enumerate(self.days):
                r, limit = self.row(ticker, d), self.limit.get((ticker, d))
                if not valid_bar(r) or not number(limit) or limit <= 0:
                    streak = None
                    continue
                up = abs(r['close'] - limit) < .005
                streak = None if up and streak is None else streak + 1 if up else 0
                if i >= 6 and streak in (1, 3):
                    events.append({'ticker': ticker, 'name': self.info[ticker].get('name', ticker), 'date': d,
                        'i': i, 'group': 'trend' if streak == 1 else 'relay',
                        'avg7_cny': self.avg_amount(ticker, i), 'band': amount_band(self.avg_amount(ticker, i))})
            latest_streaks[ticker] = streak
        return events, latest_streaks

def market_evidence(e, data):
    current = market_turnover(data['basics'], e.bars, e.basic, e.date)
    prior = market_turnover(data['basics'], e.bars, e.basic, e.days[-2])
    indices = []
    for ticker, name in INDICES.items():
        r, p = e.row(ticker, e.date), e.row(ticker, e.days[-2])
        averages, previous_ma, slopes = {}, {}, {}
        for n in (5, 10, 20):
            a = [e.row(ticker, d).get('close') for d in e.days[-n:]]
            b = [e.row(ticker, d).get('close') for d in e.days[-n-1:-1]]
            averages[str(n)] = mean(a) if all(number(x) and x > 0 for x in a) else None
            previous_ma[str(n)] = mean(b) if all(number(x) and x > 0 for x in b) else None
            slopes[str(n)] = ret(averages[str(n)], previous_ma[str(n)])
        available = number(r.get('close')) and all(number(x) for x in averages.values())
        indices.append({'ticker': ticker, 'name': name, 'close': r.get('close'), 'previous_close': p.get('close'),
            'change_pct': ret(r.get('close'), p.get('close')), 'red_k': r['close'] > r['open'] if valid_bar(r) else None,
            'ma': averages, 'previous_ma': previous_ma, 'ma_slopes_pct': slopes,
            'above_count': sum(r['close'] > a for a in averages.values()) if available else None,
            'previous_above_count': sum(p['close'] > a for a in previous_ma.values()) if available and number(p.get('close')) and all(number(a) for a in previous_ma.values()) else None,
            'bull_aligned': averages['5'] > averages['10'] > averages['20'] if available else None})
    return {'date': e.date, 'turnover': current, 'previous_turnover_cny': prior['market_amount_cny'],
        'turnover_change_pct': ret(current['market_amount_cny'], prior['market_amount_cny']), 'indices': indices,
        'main_index': '000001.SH', 'priority': '大盘 > 板块 > 个股'}


def direction_evidence(e, data, streaks):
    taxonomy = data.get('taxonomy', {})
    raw = list(taxonomy.get('rows', [])) if taxonomy.get('date') == e.date else []
    review = data.get('review', {}) if data.get('review', {}).get('analysis_date') == e.date else {}
    from shilun.review.event_theme_taxonomy import VERSION as EVENT_SCHEMA
    event_themes = data.get('event_themes', {})
    event_rows = event_themes.get('rows', []) if event_themes.get('date') == e.date and event_themes.get('topic_schema') == EVENT_SCHEMA else []
    raw.extend(event_rows)
    themes = {r['code'] for r in event_rows}
    sector = data.get('sector_cache', {})
    r2 = {r.get('sector_code'): r for r in sector.get('payload', {}).get('all_sectors', [])} if sector.get('analysis_date') == e.date else {}
    index_return = ret(e.row('000001.SH', e.date).get('close'), e.row('000001.SH', e.date).get('pre_close'))
    directions, hardware_found = [], set()
    for row in raw:
        if not row.get('research_eligible') or (row.get('taxonomy_kind') != EVENT_SCHEMA and not topic_allowed(row)):
            continue
        hw = [name for name, aliases in HARDWARE.items() if row.get('name') in aliases]
        sources = (['短线情绪'] if row['code'] in themes else []) + (['板块研究'] if row['code'] in r2 else []) + (['AI硬件重点'] if hw else [])
        if not sources:
            continue
        hardware_found.update(hw)
        ticks = sorted({m.get('con_code') for m in row.get('members', []) if m.get('con_code')})
        valid = [t for t in ticks if valid_bar(e.row(t, e.date))]
        changes = [ret(e.row(t, e.date)['close'], e.row(t, e.date).get('pre_close')) for t in valid]
        returns = stats(changes)
        relative = returns['median'] - index_return if returns['median'] is not None and index_return is not None else None
        cores = sorted(valid, key=lambda t: -(e.avg_amount(t, len(e.days)-1) or 0))[:3]
        leaders = sorted(valid, key=lambda t: -(streaks.get(t) or 0))[:3]
        amounts = [e.row(t, e.date).get('amount') for t in valid]
        prior = [e.row(t, e.days[-2]).get('amount') for t in ticks]
        amount_ratio = ratio(sum(amounts), sum(prior)) if len(valid) == len(ticks) and ticks and all(number(a) for a in amounts + prior) else None
        flows = [e.flow.get((t, e.date), {}).get('net_mf_amount') for t in ticks]
        directions.append({'code': row['code'], 'name': row['name'], 'sources': sources, 'hardware': hw,
            'membership_scope': row.get('membership_scope', 'dated_board_members'),
            'scope_note': row.get('scope_note'),
            'members': ticks, 'member_n': len(ticks), 'valid_n': len(valid), 'membership_date': row.get('membership_date'),
            'return_median_pct': returns['median'], 'relative_pct': relative,
            'up_pct': 100 * sum(c > 0 for c in changes if number(c)) / returns['n'] if returns['n'] else None,
            'amount_ratio': amount_ratio, 'flow_n': sum(number(x) for x in flows),
            'net_flow_cny': sum(flows) * 10000 if flows and all(number(x) for x in flows) else None,
            'core_refs': cores, 'relay_refs': [t for t in leaders if (streaks.get(t) or 0) >= 3],
            'reference_rule': '容量参照按当日7日均额前三；接力参照按已确认连板数。均为观察代理，非永久龙头认定。'})
    directions.sort(key=lambda r: (r['relative_pct'] is None, -(r['relative_pct'] or 0), r['code']))
    missing = [name for name in HARDWARE if name not in hardware_found]
    return directions, missing


def route_mode(market, feedback=None):
    feedback = feedback or {}
    amount = market['turnover'].get('market_amount_cny')
    main = next((r for r in market['indices'] if r['ticker'] == market['main_index']), {})
    change = main.get('change_pct')
    if amount is None or change is None:
        return {'mode': None, 'status': 'unknown', 'label': '大盘数据不足，待判断'}
    if amount > 2.3e12 and change > 0 and feedback.get('trend') is not False:
        return {'mode': 'A', 'status': 'confirmed' if feedback.get('trend') is True else 'conditional',
                'label': 'A · 大票首板套利' + ('' if feedback.get('trend') is True else '，等待趋势反馈确认')}
    if feedback.get('relay') is True or feedback.get('crowding') is True:
        return {'mode': 'B', 'status': 'confirmed', 'label': 'B · 情绪抱团／接力'}
    if feedback.get('relay') is False and feedback.get('crowding') is False:
        return {'mode': None, 'status': 'wait', 'label': '两类情绪反馈均不支持，等待'}
    return {'mode': 'B', 'status': 'conditional', 'label': '转查B · 情绪抱团／接力，等待反馈确认'}


def holding_plan(expectation, market_valid, direction_valid, no_better=False, trend_advantage=False):
    if market_valid is False or direction_valid is False or expectation == 'below':
        return 'exit'
    if market_valid is not True or direction_valid is not True:
        return 'unknown'
    if expectation == 'above' or expectation == 'normal' and (no_better or trend_advantage):
        return 'hold'
    return 'exit' if expectation == 'normal' else 'unknown'


BRANCHES = [
    {'if': '全天量能>2.3万亿、指数上涨、趋势反馈成立', 'then': '优先A；比较板块、容量核心，再匹配MA5候选'},
    {'if': '量能≤2.3万亿，或A指数/趋势条件失效', 'then': '转查B；比较连板与有生效日期的抱团核心，均弱则等待'},
    {'if': 'B成立但没有兼容的启用策略', 'then': '保留情绪观察名单；本版只启用MA5，不强塞执行候选'},
    {'if': '主选竞价正常、板块与核心正常', 'then': '保留优先观察；开盘后继续核对大盘、板块和主动性'},
    {'if': '主选弱、备选强且上游仍成立', 'then': '备选满足策略及预期条件时提升；净流入单项领先不足以换股'},
    {'if': '板块失效、个股仍强', 'then': '暂停该方向；大盘允许时重新比较其他方向'},
    {'if': '持有标的次日超预期，且上游与个股逻辑成立', 'then': '继续持有的预案；不是自动交易指令'},
    {'if': '次日符合预期', 'then': '默认离场；原逻辑成立且无更好去处或趋势优势明确，才开放续持例外'},
    {'if': '次日不及预期，或大盘/方向逻辑失效', 'then': '离场预案；不允许用别处不赚钱延长持有'},
]


def build_plan(data, research=None):
    from .directions import shortlist, choose_candidates
    e = Evidence(data)
    from .mode_research import build_mode_research, validate, reference
    from copy import deepcopy
    research = deepcopy(research) if research is not None else build_mode_research(data)
    validate(research, data)
    _, streaks = e.events()
    market, route, conclusion = (research[k] for k in ('market', 'route', 'conclusion'))
    default_band = research['default_band']
    next_day_first_boards = research['next_day_first_boards']
    cores = {'current': research['crowding_observations']}
    all_directions, hardware_missing = direction_evidence(e, data, streaks)
    directions, direction_scope, _ = shortlist(e, data, all_directions, route, cores)
    scanned = scan_universe(data['basics'], e.bars, e.basic, e.days)
    selected = choose_candidates(e, [r for r in scanned['audits'] if r['passed']], directions)
    referenced = set(r['ticker'] for r in selected)
    for d in directions:
        referenced.update(d['watch_refs'])
    core_observations = cores['current']
    referenced.update(r['ticker'] for r in core_observations)
    stocks = {t: e.stock(t) for t in referenced}
    steps = [
        ('C01', '指数量能', 'ready' if market['turnover']['available'] else 'missing', market['turnover']['label']),
        ('C02', '模式收益评估', 'partial', '三玩法独立评估；近5日对比此前10日，改善与支持度分开'),
        ('C03', '选择模式', route['status'], conclusion['headline']),
        ('C04', '方向与核心', 'ready' if directions else 'waiting', f'{direction_scope["main_n"]}个主观察、{direction_scope["backup_n"]}个条件备选；同核心合并'),
        ('C05', '策略与主备', 'conditional', f'经决策链保留{len(selected)}只MA5条件候选；B路径仅观察情绪核心'),
        ('C06', '次日预期', 'conditional', '冻结昨收、MA5/10/20与前日高低；正常区间需对照历史或人工确认'),
        ('C07', '竞价及盘中核验', 'waiting', '先看昨日首板次日正反馈，再逐层核验；竞价只有成交额时不判强弱'),
    ]
    return {'version': VERSION, 'analysis_date': e.date, 'next_date': data.get('next_date'),
        'built_at': datetime.now(timezone.utc).isoformat(), 'status': 'research_pilot',
        'provenance': {'scope': data.get('scope'), 'loaded_at': data.get('loaded_at'),
            'review_generated_at': data.get('review', {}).get('generated_at'),
            'taxonomy_fetched_at': data.get('taxonomy', {}).get('fetched_at'),
            'note': '日期成分为重建；当前证券名称/上市信息不证明历史时点完整性。收益为行情统计，非策略回测。'},
        'market': market, 'mode_research_ref': reference(research), 'route': route, 'conclusion': conclusion, 'default_band': default_band,
        'next_day_first_boards': next_day_first_boards,
        'directions': directions, 'direction_scope': direction_scope,
        'baseline_closes': {r['ticker']: r['close'] for r in e.bars if iso(r['date']) == e.date and valid_bar(r)},
        'hardware_missing': hardware_missing, 'hardware_watch': [{'name': d['name'], 'hardware':d['hardware'], 'selected':any(x['code']==d['code'] for x in directions)} for d in all_directions if d['hardware']],
        'candidates': selected, 'selection_scope': scanned['selection_scope'],
        'stocks': stocks, 'crowding_observations': core_observations,
        'steps': [{'id': i, 'title': t, 'status': s, 'conclusion': c} for i, t, s, c in steps],
        'branches': BRANCHES, 'band_names': research['band_names'],
        'method': {'rank': direction_scope['rule'], 'enabled_strategies': ['ma5_v0_2']}}
