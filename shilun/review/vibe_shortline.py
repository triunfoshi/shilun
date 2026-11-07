"""Dated event themes, rotation and six-reading history, ported from Vibe AStock.

Source e0d08d19848b50d22a772729a06287cdff511629 (Apache-2.0; licenses/vibe-astock.txt):
shortline_themes.py, shortline_boards.py and orchestrator/finance/shortline/history.ts.
This adapter reads archived facts only. No request downloads data or rewrites a
historical cohort, and an industry classification never substitutes for a theme.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
import re
from statistics import median

from shilun.review.board_data import BoardResearchData, CHINA, REASON_COLLECTION, settled_target
from shilun.review.board_dynamics import comparable_pools
from shilun.review.sentiment_context import integer, rows_for, time_value

VERSION = 'vibe-shortline-v1'
SCOPE = '沪深非ST'
GENERIC = {'国企改革', '央企改革', '破净', '低价股', '高送转', '摘帽', '壳资源',
           '小盘股', '微盘股', '次新股', '超跌反弹', '股权激励', '员工持股'}
GENERIC_SUFFIX = ('国资', '国企', '板块')


def clean_reason(text):
    """Vibe whole-tag truncation: up to four tags and forty characters."""
    result = []
    for tag in str(text or '').replace('，', '+').replace(',', '+').split('+'):
        tag = tag.strip()
        if not tag:
            continue
        if result and len('+'.join(result + [tag])) > 40:
            break
        result.append(tag[:40])
        if len(result) >= 4:
            break
    return '+'.join(result)


def tags_of(reason):
    """Vibe event labels; generic attributes and local-state assets are excluded."""
    return [tag for p in str(reason or '').split('+')
            if (tag := p.strip()) and tag not in GENERIC and not tag.endswith(GENERIC_SUFFIX)]


def num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None


def unique_records(rows, key, label):
    result = {}
    for row in rows:
        identity = key(row)
        if identity in result:
            raise ValueError(f'{label}存在重复记录，停止计算以免改变样本权重')
        result[identity] = row
    return result


def code_of(row):
    return row.get('code') or row['ticker'].split('.')[0]


def reasons_for(documents, day):
    """Never use a document labelled as another trading date."""
    doc = documents.get(day) or {}
    if doc.get('date') != day:
        return {}, ''
    raw = doc.get('reasons') or {}
    return {str(code).split('.')[0]: clean_reason(value) for code, value in raw.items() if value}, doc.get('source_note') or doc.get('source') or ''


def parse_stat(stat):
    if isinstance(stat, dict):
        days, count = integer(stat.get('days')), integer(stat.get('ct'))
        return (days, count) if days and count else None
    match = re.fullmatch(r'\s*(\d+)\s*(?:天|/)\s*(\d+)\s*板?\s*', str(stat or ''))
    return (int(match[1]), int(match[2])) if match else None


def board_label(boards, stat):
    parsed = parse_stat(stat)
    if parsed:
        days, count = parsed
        if count > 0 and days > count:
            return f'{days}天{count}板'
        if count > 0:
            return f'{count}板'
    return f'{boards}板' if boards is not None else '板数待补'


def state_of(group, has_prev=True, prev_complete=True):
    lu, high, prev = group['limit_up'], group['highest'], group['prev_members']
    cont, failed = group['continuation_rate'], group['broken_rate'] or 0
    if not has_prev:
        return '无昨日题材数据'
    if lu == 0 and prev:
        return '昨日题材未再涨停'
    if prev == 0:
        return '今日新出现' if prev_complete else '昨日覆盖样本未见'
    if cont is not None and cont <= .15 and lu <= 2:
        return '接力断档'
    if failed >= .5:
        return '分歧加大'
    if lu >= 4 and group['first_boards'] >= 2:
        return '扩散中'
    if high is not None and high >= 3 and lu <= 2:
        return '高标独活'
    if cont is not None and cont >= .5:
        return '延续'
    return '维持'


def theme_tree(target, previous, ups, broken, downs, reasons, top=10):
    current, failed, fallen = rows_for(ups, target), rows_for(broken, target), rows_for(downs, target)
    if current is None:
        return {'available': False, 'reason': f'{target} 同日完整收盘涨停池缺失'}
    today_reasons, note = reasons_for(reasons, target)
    if not today_reasons:
        return {'available': False, 'reason': '同日涨停原因串未归档；不用行业分类顶替题材，可点击回补分析。'}
    prev_reasons, _ = reasons_for(reasons, previous)
    old = rows_for(ups, previous)
    old_by = {code_of(row): row for row in old or []}
    by_code = {code_of(row): row for row in current}
    failed_codes = {code_of(row) for row in failed or []} - by_code.keys()
    fallen_codes = {code_of(row) for row in fallen or []}
    has_prev = bool(prev_reasons) and bool(old_by)
    prev_covered = sum(bool(tags_of(prev_reasons.get(code, ''))) for code in old_by)
    complete = bool(old_by) and prev_covered == len(old_by)
    groups = {}

    def group(tag):
        return groups.setdefault(tag, {'tag': tag, 'limit_up': 0, 'first_boards': 0, 'consec_boards': 0,
            'highest': 0, 'broken': 0 if failed is not None else None, 'limit_down': 0 if fallen is not None else None,
            'seal_times': [], 'members': [], 'prev_members': 0, 'prev_still_up': 0, 'prev_broken_or_down': 0,
            'height_missing': 0})

    covered = 0
    # Vibe iterates source attribution order; equal count/height retain that order.
    for code, reason in today_reasons.items():
        row = by_code.get(code)
        if row is None:
            continue
        tags = tags_of(reason)
        if not tags:
            continue
        covered += 1
        height = integer(row.get('boards'))
        first_seal = time_value(row.get('seal_time'))
        for tag in tags:
            g = group(tag)
            g['limit_up'] += 1
            if height is None:
                g['height_missing'] += 1
            else:
                g['highest'] = max(g['highest'], height)
                g['first_boards' if height <= 1 else 'consec_boards'] += 1
            if first_seal:
                g['seal_times'].append(first_seal)
            stat = row.get('zt_stat')
            effective = max(height or 0, (parse_stat(stat) or (0, 0))[1])
            g['members'].append({'code': code, 'ticker': row['ticker'], 'name': row.get('name') or code,
                'boards': height, 'zt_stat': stat, 'label': board_label(height, stat), '_h': effective,
                'first_seal': first_seal, 'broken_times': num(row.get('break_count'))})
    for code, reason in prev_reasons.items():
        if code in old_by:
            for tag in tags_of(reason):
                group(tag)
    for codes, key in [(failed_codes, 'broken'), (fallen_codes, 'limit_down')]:
        for code in codes:
            for tag in tags_of(today_reasons.get(code) or prev_reasons.get(code) or ''):
                if tag in groups:
                    groups[tag][key] += 1
    for code in old_by:
        for tag in tags_of(prev_reasons.get(code, '')):
            g = groups.get(tag)
            if g is None:
                continue
            g['prev_members'] += 1
            if code in by_code:
                g['prev_still_up'] += 1
            elif code in failed_codes or code in fallen_codes:
                g['prev_broken_or_down'] += 1
    if not covered and not any(g['prev_members'] for g in groups.values()):
        return {'available': False, 'reason': '涨停原因与同日完整涨停池未匹配出事件题材'}
    themes = []
    for g in groups.values():
        times = sorted(g.pop('seal_times'))
        attempts = g['limit_up'] + g['broken'] if g['broken'] is not None else None
        g['first_seal'], g['last_seal'] = (times[0], times[-1]) if times else (None, None)
        g['broken_rate'] = round(g['broken']/attempts, 3) if attempts else None
        g['continuation_rate'] = round(g['prev_still_up']/g['prev_members'], 3) if g['prev_members'] else None
        if g['height_missing']:
            g['highest'] = None
        g['state'] = state_of(g, has_prev, complete)
        g['members'] = sorted(g['members'], key=lambda row: -row['_h'])[:8]
        for row in g['members']:
            row.pop('_h')
        themes.append(g)
    themes.sort(key=lambda g: (-g['limit_up'], -(g['highest'] or 0)))
    shown = themes[:top] + [g for g in themes[top:] if not g['limit_up'] and g['prev_members']][:6]
    src = f'昨日有效题材覆盖 {prev_covered}/{len(old_by)} 只，只对已覆盖的样本算延续。列出当天前 {top} 个题材，另补最多 6 个昨日有、今天没再涨停的题材。{note}'
    if not has_prev:
        src += ' 昨日涨停池或昨日题材串没取到，不能判断题材是不是新出现。'
    if failed is None or fallen is None:
        src += ' 同日炸板或跌停池缺失，对应风险数量显示未知。'
    return {'available': True, 'source_note': src, 'tag_count': len(themes), 'prev_covered': prev_covered,
        'prev_total': len(old_by), 'has_prev': has_prev, 'themes': shown, 'covered': covered,
        'total_limit_up': len(current), 'coverage_rate': round(covered/len(current), 3) if current else None,
        'concentration': round(themes[0]['limit_up']/len(current), 3) if themes and current else None}


def day_tags(day, ups, reasons, raw_pools=None):
    current = rows_for(ups, day)
    if current is None:
        return {'date': day, 'available': False, 'reason': f'{day} 同日完整收盘涨停池未归档'}
    source, note = reasons_for(reasons, day)
    if not source:
        return {'date': day, 'available': False, 'reason': f'{day} 同日涨停原因未归档'}
    tags, matched = {}, 0
    for row in current:
        labels = tags_of(source.get(code_of(row), ''))
        if not labels:
            continue
        matched += 1
        height = integer(row.get('boards'))
        for label in labels:
            g = tags.setdefault(label, {'count': 0, 'highest': 0, 'members': []})
            g['count'] += 1
            g['highest'] = max(g['highest'], height or 0)
            g['members'].append((height or 0, row.get('name') or code_of(row)))
    ranked = sorted(tags, key=lambda tag: (-tags[tag]['count'], -tags[tag]['highest'], tag))
    for g in tags.values():
        g['rank'] = 1 + sum(other['count'] > g['count'] for other in tags.values())
        g['members'] = [name for _, name in sorted(g['members'], key=lambda row: -row[0])[:3]]
    scoped_count = len(current)
    raw_doc = (raw_pools or {}).get(day) or {}
    raw_rows = raw_doc.get('stocks')
    raw_ready = (raw_doc.get('date') == day and raw_doc.get('complete')
                 and raw_doc.get('snapshot_mode') != 'live' and isinstance(raw_rows, list))
    if raw_ready:
        tickers = [row.get('ticker') for row in raw_rows]
        raw_ready = all(tickers) and len(set(tickers)) == len(tickers) and len(raw_rows) >= scoped_count
    raw_count = len(raw_rows) if raw_ready else scoped_count
    return {'date': day, 'available': True, 'limit_up': raw_count, 'raw_limit_up': raw_count,
        'scoped_limit_up': scoped_count, 'excluded_n': raw_count-scoped_count,
        'scope_note': '列头涨停家数为该日来源完整池总数，保留北交所等原始样本；题材成分按沪深非ST归因，北交所不计入缺原因。',
        'covered': matched, 'tags': tags, 'top': ranked[:3], 'note': note,
        'unknown': sum(code_of(row) not in source for row in current)}


def rotation_status(series):
    today = series[-1]
    if today is None:
        return '当天题材串没取到', None, False
    if today == 0:
        return '今天没有涨停', 0, False
    streak, partial = 0, False
    for value in reversed(series):
        if value is None:
            partial = True
            break
        if value == 0:
            break
        streak += 1
    else:
        partial = True
    if streak >= 2:
        return f'连续 {streak} 天' + ('以上' if partial else ''), streak, partial
    earlier = series[:-1]
    if partial:
        return '昨天缺数，新旧不确定', 1, True
    last = max((i for i, value in enumerate(earlier) if value), default=None)
    if last is not None:
        between = earlier[last+1:]
        if any(value is None for value in between):
            return '回流（中间有缺数，隔几天说不准）', 1, False
        return f'隔 {len(between)} 天回流', 1, False
    if any(value is None for value in earlier):
        return '窗口内前几天有缺数，不确定是不是新出现', 1, True
    return '窗口内首次出现', 1, False


def theme_rotation(target, dates, ups, reasons, days=5, raw_pools=None):
    dates = sorted({day for day in dates if day <= target})[-days:]
    if not dates or dates[-1] != target:
        return {'available': False, 'reason': f'交易日历中没有 {target}，不使用其他日期替代'}
    per = [day_tags(day, ups, reasons, raw_pools=raw_pools) for day in dates]
    heads = [{key: value for key, value in day.items() if key not in ('tags', 'note')} for day in per]
    if not per[-1]['available']:
        return {'available': False, 'reason': per[-1]['reason'], 'days': heads}

    def cell(day, tag, key):
        if not day['available']:
            return None
        if tag in day['tags']:
            return day['tags'][tag][key]
        return None if day['unknown'] or key == 'rank' else 0

    themes = []
    for tag in sorted({tag for day in per if day['available'] for tag in day['tags']}):
        series = [cell(day, tag, 'count') for day in per]
        ranks = [cell(day, tag, 'rank') for day in per]
        highs = [cell(day, tag, 'highest') for day in per]
        status, streak, partial = rotation_status(series)
        known = [v for v in series if v is not None]
        themes.append({'tag': tag, 'series': series, 'ranks': ranks, 'highest': highs, 'today': series[-1] or 0,
            'total': sum(known), 'active_days': sum(bool(v) for v in known), 'top3_days': sum(r is not None and r <= 3 for r in ranks),
            'streak': streak, 'streak_partial': partial, 'status': status,
            'first_day': next((day for day, value in zip(dates, series) if value), None),
            'members': (per[-1]['tags'].get(tag) or {}).get('members', [])})
    themes.sort(key=lambda row: (-row['today'], -row['total'], -max(h or 0 for h in row['highest']), row['tag']))
    active = [row for row in themes if row['today']][:12]
    faded = sorted([row for row in themes if row['series'][-1] == 0 and len(row['series']) >= 2 and (row['series'][-2] or 0) >= 2],
        key=lambda row: (-(row['series'][-2] or 0), row['tag']))[:6]
    return {'available': True, 'dates': dates, 'days': heads, 'themes': active+faded, 'tag_count': len(themes),
        'missing_days': [day['date'] for day in per if not day['available']],
        'partial_days': [day['date'] for day in per if day['available'] and day['unknown']],
        'prev_available': len(per) >= 2 and per[-2]['available']}


def theme_evidence(target, ups, reasons):
    current = rows_for(ups, target)
    source, note = reasons_for(reasons, target)
    rows, missing = [], []
    for row in current or []:
        code = code_of(row)
        reason = source.get(code, '')
        tags = tags_of(reason)
        if not tags:
            missing.append(code)
        for tag in tags:
            rows.append({'tag': tag, 'ticker': row['ticker'], 'code': code, 'name': row.get('name') or code,
                'boards': integer(row.get('boards')), 'first_seal': time_value(row.get('seal_time')),
                'last_seal': time_value(row.get('last_seal_time')), 'broken_times': num(row.get('break_count')),
                'reason': reason, 'reason_source': note})
    rows.sort(key=lambda row: (row['tag'], -(row['boards'] or 0), row['code']))
    return {'date': target, 'available': current is not None and bool(source), 'source': note,
        'rows': rows, 'covered': len(current)-len(missing) if current is not None else 0,
        'total_limit_up': len(current) if current is not None else None, 'missing_codes': missing,
        'reason': '同日涨停池或涨停原因未归档' if current is None or not source else ''}


def themes_payload(data, target):
    settled = settled_target(target)
    if target != settled:
        absent = {'available': False, 'reason': '所选日尚未进入完整收盘统计；不使用前一交易日替代。'}
        return {'date': target, 'prev_date': None, 'version': VERSION, 'scope': SCOPE, 'settled': False,
            'tree': absent.copy(), 'rotation': absent.copy(), 'evidence': {**absent, 'date': target, 'rows': []}}
    dates = [day for day in data.sessions(target, 5) if day <= target][-5:]
    previous = dates[-2] if len(dates) >= 2 and dates[-1] == target else None
    raw_ups = data.pools(dates, settled=True)
    ups = comparable_pools(raw_ups)
    broken = comparable_pools(data.pools([target], 'broken', settled=True))
    # BoardResearchData.down has no provider fallback: only an explicit dated archive.
    downs = comparable_pools(data.pools([target], 'down', settled=True))
    if rows_for(downs, target) is None:
        # The P2 full-market close already verifies this roster against price
        # limits. Reuse that exact day's fact instead of making a new request.
        from shilun.review.service import REVIEW_COLLECTION, REVIEW_VERSION
        snapshot = data.store.collection(REVIEW_COLLECTION).find_one(
            {'analysis_date': target, 'engine_version': REVIEW_VERSION},
            {'_id': 0, 'sentiment.downside_risk': 1}) or {}
        risk = snapshot.get('sentiment', {}).get('downside_risk', {})
        count, stocks = risk.get('limit_down_count'), risk.get('stocks')
        if risk.get('count_ready') and type(count) is int and isinstance(stocks, list) and len(stocks) == count:
            downs[target] = {'date': target, 'complete': True, 'snapshot_mode': 'settled', 'metric_scope': SCOPE,
                'source': 'dated_review_verified_downside', 'stocks': [
                    {**row, 'code': row['ticker'].split('.')[0]} for row in stocks
                    if row.get('ticker', '').endswith(('.SH', '.SZ')) and 'ST' not in row.get('name', '').upper()]}
    reasons = unique_records(data.store.collection(REASON_COLLECTION).find({'date': {'$in': dates}}, {'_id': 0}),
                             lambda row: row['date'], '同日涨停原因归档')
    return {'date': target, 'prev_date': previous, 'version': VERSION, 'scope': SCOPE, 'settled': True,
        'tree': theme_tree(target, previous, ups, broken, downs, reasons),
        'rotation': theme_rotation(target, dates, ups, reasons, raw_pools=raw_ups), 'evidence': theme_evidence(target, ups, reasons)}


# Six daily readings and statistical definitions are kept identical to history.ts.
READINGS = [
    {'key': 'limit_up', 'label': '涨停家数', 'unit': '家', 'kind': 'count', 'higher_is_hotter': True, 'diff_eps': 8},
    {'key': 'highest_board', 'label': '最高连板', 'unit': '板', 'kind': 'count', 'higher_is_hotter': True, 'diff_eps': .5},
    {'key': 'broken_rate', 'label': '炸板率', 'unit': '', 'kind': 'rate', 'higher_is_hotter': False, 'diff_eps': .08},
    {'key': 'money_effect', 'label': '赚钱效应中位数', 'unit': '%', 'kind': 'pct', 'higher_is_hotter': True, 'diff_eps': 1.0},
    {'key': 'deep_loss', 'label': '跌超5%家数', 'unit': '家', 'kind': 'count', 'higher_is_hotter': False, 'diff_eps': 8},
    {'key': 'promotion_1to2', 'label': '1进2 晋级率', 'unit': '', 'kind': 'rate', 'higher_is_hotter': True, 'diff_eps': .08},
]
MIN_SAMPLES = 10


def history_context(target, rows):
    if not rows:
        return {'available': False, 'reason': '历史读数一天都没有'}
    current = rows[-1]
    if current['date'] != target:
        return {'available': False, 'reason': f'{target} 的读数没取到'}
    past, items = rows[:-1], []
    for reading in READINGS:
        key = reading['key']
        value = num(current['values'].get(key))
        hist = [v for row in past if (v := num(row['values'].get(key))) is not None]
        percentile = round(sum(v <= value for v in hist)/len(hist), 3) if value is not None and len(hist) >= MIN_SAMPLES else None
        extreme = None
        if percentile is not None:
            if percentile >= .85:
                extreme = '偏热' if reading['higher_is_hotter'] else '偏冷'
            elif percentile <= .15:
                extreme = '偏冷' if reading['higher_is_hotter'] else '偏热'
        items.append({**{key: reading[key] for key in ('key', 'label', 'unit', 'kind', 'higher_is_hotter')},
            'value': value, 'percentile': percentile, 'extreme': extreme,
            'hist_min': min(hist) if hist else None, 'hist_max': max(hist) if hist else None,
            'hist_median': round(median(hist), 2) if hist else None, 'valid_hist_days': len(hist)})
    return {'available': True, 'date': target, 'sample_days': len(past), 'min_samples': MIN_SAMPLES,
        'items': items, 'extreme_count': sum(item['extreme'] is not None for item in items)}


def history_diff(target, rows):
    if len(rows) < 2:
        return {'available': False, 'reason': '历史不足两天,没法比较'}
    current, previous = rows[-1], rows[-2]
    if current['date'] != target:
        return {'available': False, 'reason': f'{target} 的读数没取到'}
    changes = []
    for reading in READINGS:
        key = reading['key']
        before, after = num(previous['values'].get(key)), num(current['values'].get(key))
        if before is None or after is None or abs(after-before) <= reading['diff_eps']+1e-9:
            continue
        changes.append({**{key: reading[key] for key in ('key', 'label', 'unit', 'kind')},
            'prev': before, 'cur': after, 'delta': round(after-before, 2),
            'hotter': ((after-before) > 0) == reading['higher_is_hotter']})
    thresholds = {reading['key']: reading['diff_eps'] for reading in READINGS}
    changes.sort(key=lambda row: -abs(row['delta'])/max(thresholds[row['key']], 1e-9))
    return {'available': True, 'date': target, 'prev_date': previous['date'], 'changes': changes,
        'unchanged_count': len(READINGS)-len(changes)}


def history_day(day, previous, ups, broken, *, previous_rows=None, bars=None, limits=None, suspended=None):
    """Yesterday's whole sealed cohort, today's outcomes; deep loss is NOT all market."""
    current, old, failed = rows_for(ups, day), rows_for(ups, previous), rows_for(broken, day)
    values = {reading['key']: None for reading in READINGS}
    notes, coverage = [], {}
    if current is not None:
        values['limit_up'] = len(current)
        heights = [integer(row.get('boards')) for row in current]
        values['highest_board'] = max(heights, default=0) if all(v is not None for v in heights) else None
        if failed is not None:
            codes = {row['ticker'] for row in current}
            count = len({row['ticker'] for row in failed}-codes)
            values['broken_rate'] = round(count/(len(current)+count), 3) if len(current)+count else None
    if old is not None and current is not None:
        first = [row for row in old if integer(row.get('boards')) == 1]
        # Unknown height may hide first boards, so don't claim a complete denominator.
        if all(integer(row.get('boards')) is not None for row in old) and first:
            today_codes = {row['ticker'] for row in current}
            values['promotion_1to2'] = round(sum(row['ticker'] in today_codes for row in first)/len(first), 3)
            coverage['promotion_base'] = len(first)
    returns = []
    source = 'dated_limit_pool_and_daily_bars'
    bars, limits, suspended = bars or {}, limits or {}, suspended or set()
    settled = unique_records(previous_rows or [], lambda row: row['ticker'], '昨日涨停当日反馈')
    if old is not None:
        for row in old:
            ticker = row['ticker']
            if ticker in suspended:
                continue
            item = settled.get(ticker, {})
            ret = num(item.get('return_1d'))
            if ret is not None:
                returns.append(ret*100)
                source = 'dated_previous_limit_pool'
                continue
            bar, limit = bars.get(ticker, {}), limits.get(ticker, {})
            close = num(bar.get('close'))
            base = num(bar.get('pre_close')) or num(limit.get('pre_close'))
            if close is not None and close > 0 and base is not None and base > 0:
                returns.append((close/base-1)*100)
        coverage.update(sample=len(returns), expected=len(old), missing=len(old)-len(returns))
        if returns:
            values['money_effect'] = round(median(returns), 2)
            # Use a tiny tolerance only for arithmetic price conversions at exactly -5%.
            values['deep_loss'] = sum(value <= -5+1e-9 for value in returns)
        if len(returns) != len(old):
            notes.append(f'昨日涨停股当日反馈覆盖 {len(returns)}/{len(old)}；停牌和缺行情不按0填充')
    missing = [reading['label'] for reading in READINGS if values[reading['key']] is None]
    if missing:
        notes.append('缺 '+ '、'.join(missing))
    status = 'missing' if len(missing) == len(READINGS) else 'partial' if missing or coverage.get('missing') else 'ok'
    return {'date': day, 'values': values, 'status': status, 'settled': True, 'note': '；'.join(notes),
        'cached': True, 'fetched_at': ups.get(day, {}).get('fetched_at'), 'source': source, 'coverage': coverage}


def build_history(target, dates, ups, broken, *, days=30, previous=None, bars=None, limits=None, suspended=None):
    if days not in (10, 20, 30, 60):
        raise ValueError('情绪历史窗口仅支持10、20、30、60个交易日')
    dates = sorted({day for day in dates if day <= target})[-(days+1):]
    previous, bars, limits, suspended = previous or {}, bars or {}, limits or {}, suspended or {}
    predecessors = dict(zip(dates[1:], dates))
    shown = dates[-days:]
    # Explicit non-trading/missing target must stay missing, never show the prior date as current.
    if target not in shown:
        shown = (shown+[target])[-days:]
    series = []
    for day in shown:
        prev = predecessors.get(day)
        doc = previous.get(day) or {}
        prior_rows = doc.get('rows') if doc.get('analysis_date') == day and doc.get('previous_date') == prev else None
        series.append(history_day(day, prev, ups, broken, previous_rows=prior_rows,
            bars={ticker: row for (d, ticker), row in bars.items() if d == day},
            limits={ticker: row for (d, ticker), row in limits.items() if d == day},
            suspended={ticker for ticker, dates in suspended.items() if day in dates}))
    rows = [row for row in series if row['status'] != 'missing']
    gaps = []
    if len(shown) < days:
        gaps.append(f'交易日历仅覆盖 {len(shown)}/{days} 日；未按自然日补齐。')
    missing = [row['date'] for row in series if row['status'] == 'missing']
    if missing:
        gaps.append(f'{len(missing)}个交易日未取到读数，不进入统计：'+ '、'.join(missing))
    partial = [row['date'] for row in series if row['status'] == 'partial']
    if partial:
        gaps.append(f'{len(partial)}个交易日部分字段或反馈样本不完整，详见逐日读数。')
    return {'date': target, 'days': days, 'version': VERSION, 'scope': SCOPE, 'readings': [dict(r) for r in READINGS],
        'series': series, 'context': history_context(target, rows), 'diff': history_diff(target, rows),
        'gaps': gaps, 'generated_at': datetime.now(CHINA).isoformat(),
        'source': '已归档同日沪深非ST涨停／炸板池、昨日完整涨停名单及当日行情；跌超5%家数指昨日涨停股，不是全市场。分位只与复盘日前的有效读数比较。'}


def history_payload(data, target, days=30):
    from shilun.review.board_data import _day
    from shilun.review.dated_pools import COLLECTION
    from shilun.review.suspensions import load_suspensions
    if days not in (10, 20, 30, 60):
        raise ValueError('情绪历史窗口仅支持10、20、30、60个交易日')
    dates = [day for day in data.sessions(target, days) if day <= target]
    ups = comparable_pools(data.pools(dates, settled=True))
    broken = comparable_pools(data.pools(dates, 'broken', settled=True))
    previous = unique_records(data.store.collection(COLLECTION).find(
        {'analysis_date': {'$in': dates}}, {'_id': 0}), lambda row: row['analysis_date'], '昨日涨停反馈归档')
    tickers_by_day = {day: sorted({row['ticker'] for row in ups.get(prior, {}).get('stocks', [])}) for prior, day in zip(dates, dates[1:])}
    queries = [{'date': day, 'ticker': {'$in': tickers}} for day, tickers in tickers_by_day.items() if tickers]
    bars = unique_records(data.store.collection('market_daily_bars').find(
        {'$or': queries}, {'_id': 0, 'date': 1, 'ticker': 1, 'close': 1, 'pre_close': 1}),
        lambda row: (row['date'], row['ticker']), '同股同日行情') if queries else {}
    limit_queries = [{'trade_date': {'$in': [day, day.replace('-', '')]}, 'ticker': {'$in': tickers}} for day, tickers in tickers_by_day.items() if tickers]
    limits = unique_records(data.store.collection('daily_price_limits').find(
        {'$or': limit_queries}, {'_id': 0, 'trade_date': 1, 'ticker': 1, 'pre_close': 1}),
        lambda row: (_day(row['trade_date']), row['ticker']), '同股同日计价基准') if limit_queries else {}
    if target != settled_target(target):
        ups.pop(target, None)
        broken.pop(target, None)
        previous.pop(target, None)
        bars = {key: row for key, row in bars.items() if key[0] != target}
    result = build_history(target, dates, ups, broken, days=days, previous=previous, bars=bars, limits=limits,
        suspended=load_suspensions(data.store, dates))
    if target != settled_target(target):
        row = next((row for row in result['series'] if row['date'] == target), None)
        if row:
            row.update(settled=False, note='所选日尚未进入完整收盘统计，不使用盘中读数替代。')
        result['gaps'].insert(0, f'{target} 尚未进入完整收盘统计。')
    return result
