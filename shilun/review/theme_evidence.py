"""Dated P2 evidence. Missing observations are never zero or safe risk."""
from collections import Counter, defaultdict
from math import isfinite
from statistics import mean, median

from shilun.review.engine import classify_sector_cycle
from shilun.review.sources import at_limit
from shilun.review.emotion_history import round_evidence
from shilun.review.theme_scope import topic_name, topic_scope


def num(value):
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (TypeError, ValueError):
        return None


def ratio(a, b):
    a, b = num(a), num(b)
    return a / b - 1 if a is not None and b not in (None, 0) else None


def price_limit_hit(close, limits, field='down_limit'):
    # An explicitly supplied zero lower bound cannot be reached by a positive
    # close (e.g. listing-day sentinel). An absent bound remains unknown.
    if field == 'down_limit' and num(limits.get(field)) == 0 and (num(close) or 0) > 0 and (num(limits.get('up_limit')) or 0) > 0:
        return False
    return at_limit(close, limits.get(field))


def med(values):
    values = [v for v in values if v is not None]
    return round(median(values), 6) if values else None


def board(row):
    return int(num(row.get('consecutive_count')) or 1)


theme_name = topic_name

def labels(row, keys):
    values = set()
    for key in keys:
        value = row.get(key)
        if isinstance(value, str):
            values.update(v.strip() for v in value.replace('；', ',').replace('、', ',').split(',') if v.strip())
        elif isinstance(value, (list, tuple)):
            values.update(str(v).strip() for v in value if v)
    return values


def one_price_limit_up(bar, upper, breaks, *, in_pool):
    """Tri-state lock evidence; missing highs/lows need not hide a proven lock."""
    opening, close, high, low = (num(bar.get(k)) for k in ('open', 'close', 'high', 'low'))
    if not in_pool:
        return False if high is not None and low is not None and high > low else None
    if at_limit(opening, upper) is True and at_limit(close, upper) is True and num(breaks) == 0:
        if low is not None and at_limit(low, upper) is not True:
            return None  # Conflicting evidence, not a proven all-day lock.
        return True
    if high is not None and low is not None:
        if high > low:
            return False
        if high == low == close and num(breaks) in (None, 0):
            return True if opening is None or at_limit(opening, close) is True else None
    return None


def front_support(rows, current_bars, previous_bars, *, current_rows=None, previous_date='', theme='',
                  current_price_limits=None, previous_price_limits=None, history_rows=None, members=None,
                  price_history=None):
    """Fix yesterday's cohort before observing today; retain failed leaders.

    One-price boards are strong price retention but untested tradable support.
    Missing intraday fields never turn into zero breaks or failed support.
    """
    today = {r['ticker']: r for r in (current_rows or [])}
    highest = max((board(r) for r in rows), default=0)
    threshold = max(2, highest - 1) if highest >= 2 else 1
    role_evidence = {}
    if history_rows is not None:
        observed = defaultdict(list)
        for day, row in history_rows:
            observed[row['ticker']].append((day, row))
        yesterday = {r['ticker']: r for r in rows}
        peaks = {t: max(board(r) for _, r in h) for t, h in observed.items()}
        peak = max(peaks.values(), default=0)
        anchors = {t for t, h in observed.items() if
                   (highest >= 3 and t in yesterday and board(yesterday[t]) >= max(3, highest - 1))
                   or peaks[t] >= max(3, peak - 1)
                   or (peaks[t] >= 2 and len(h) >= 2
                       and (num(previous_bars.get(t, {}).get('amount')) or 0) >= 2_000_000)}
        price_evidence = {}
        for ticker in (members or {}):
            h = sorted([r for r in (price_history or {}).get(ticker, []) if str(r.get('date'))[:10] <= previous_date], key=lambda r: str(r.get('date')))
            if len(h) < 21:
                continue
            used_raw_reference = False
            def compound(window):
                nonlocal used_raw_reference
                value = 1.0
                for index in range(len(h) - window, len(h)):
                    bar = h[index]
                    close, base = num(bar.get('close')), num(bar.get('pre_close'))
                    if not base:
                        base = num(h[index - 1].get('close'))
                        used_raw_reference = True
                    if close is None or not base or base <= 0:
                        return None
                    value *= close / base
                return value - 1
            r10, r20 = compound(10), compound(20)
            closes = [num(r.get('close')) for r in h[-5:]]
            if r10 is None or r20 is None or any(v is None for v in closes):
                continue
            price_evidence[ticker] = {'return_10d': r10, 'return_20d': r20,
                'price_basis': 'raw_close_fallback' if used_raw_reference else 'pre_close_chain',
                'above_ma5': closes[-1] >= sum(closes) / 5,
                'amount_yi': (num(previous_bars.get(ticker, {}).get('amount')) or 0) / 100000}
        for ticker, evidence in price_evidence.items():
            evidence['relative_rank'] = sum(r['return_20d'] <= evidence['return_20d'] for r in price_evidence.values()) / len(price_evidence)
            trend = (evidence['return_10d'] >= .20 and evidence['return_20d'] >= .30
                     and evidence['relative_rank'] >= .8 and evidence['above_ma5'])
            capacity = (evidence['amount_yi'] >= 20 and evidence['return_10d'] >= .10
                        and evidence['relative_rank'] >= .8 and evidence['above_ma5'])
            evidence['trend_front'], evidence['capacity_front'] = trend, capacity
            if trend or capacity:
                anchors.add(ticker)
        rows = []
        for ticker, history in observed.items():
            prior = yesterday.get(ticker)
            latest = history[-1][1]
            source = topic_scope(latest)
            is_front = ticker in anchors if anchors else None
            rows.append({**latest, **(prior or {}), 'consecutive_count': board(prior) if prior else 0,
                         '_in_previous_pool': prior is not None})
            role_evidence[ticker] = {
                'front': is_front, 'prior_peak_boards': peaks[ticker],
                'first_active_date': history[0][0], 'last_active_date': history[-1][0],
                'active_days': len(history), 'membership_source': source[1], 'membership_note': source[2],
                'role_label': '前排候选' if is_front else '后排候选' if is_front is False else '角色待确认',
                'role_reason': ('昨日梯队或近7日高度领先，或持续活跃且成交额达到20亿；带动关系仍待验证' if is_front else
                                '同方向近7日活跃成员，未达到前排证据；不等于弱股或补涨龙' if is_front is False else
                                '当前没有足够前排锚点，暂不强分前后排'),
            }
            if ticker in price_evidence:
                role_evidence[ticker]['price_evidence'] = price_evidence[ticker]
                if price_evidence[ticker]['trend_front'] or price_evidence[ticker]['capacity_front']:
                    role_evidence[ticker].update(role_label='趋势/容量前排候选', role_reason='截至昨日20日涨幅位于本视角前20%，近10日涨幅和MA5保留满足规则；容量另需成交额≥20亿。带动仍待验证。')
        # Full constituents stay visible even if they never appeared in a limit
        # pool. Do not manufacture an active date or a one-board observation.
        for ticker, member in (members or {}).items():
            if ticker in observed:
                continue
            rows.append({'ticker': ticker, 'name': member.get('name') or member.get('con_name') or ticker,
                         'consecutive_count': 0, '_in_previous_pool': False})
            is_front = ticker in anchors if anchors else None
            role_evidence[ticker] = {'front': is_front,
                'prior_peak_boards': 0, 'active_days': 0,
                'role_label': '趋势/容量前排候选' if is_front else '后排观察成分' if anchors else '角色待确认',
                'price_evidence': price_evidence.get(ticker),
                'role_reason': '截至昨日同视角20日相对涨幅、近10日强度和MA5保留满足规则，近7日无涨停记录。' if is_front else '属于所选视角的实际成分，近7日无涨停记录；尚未证明跟随、补涨或带动。'}
    details = []
    for row in sorted(rows, key=lambda r: (-board(r), r['ticker'])):
        ticker = row['ticker']
        bar, prev, pool = current_bars.get(ticker, {}), previous_bars.get(ticker, {}), today.get(ticker, {})
        ret = ratio(bar.get('close'), num(bar.get('pre_close')) or prev.get('close'))
        high, low, close = (num(bar.get(k)) for k in ('high', 'low', 'close'))
        one_price = one_price_limit_up(bar, (current_price_limits or {}).get(ticker, {}).get('up_limit'),
                                       pool.get('broken_count'), in_pool=ticker in today)
        position = (close - low) / (high - low) if None not in (high, low, close) and high > low else None
        prev_one = one_price_limit_up(prev, (previous_price_limits or {}).get(ticker, {}).get('up_limit'),
                                     row.get('broken_count'), in_pool=row.get('_in_previous_pool', True))
        status = ('locked' if one_price is True else
                  'pending' if ret is None or high is None or low is None or high == low else
                  'supported' if ret >= -.01 and position is not None and position >= .55 else 'weak')
        details.append({'ticker': ticker, 'name': row.get('name') or ticker, 'theme': theme,
            'boards': int(row.get('consecutive_count') or 0) if history_rows is not None else board(row), 'today_boards': board(pool) if pool else None,
            'front': board(row) >= threshold if highest >= 2 else None, **role_evidence.get(ticker, {}), 'return_1d': ret, 'close_position': position,
            'intraday_drawdown': ratio(low, high), 'volume_ratio': ratio(bar.get('volume'), prev.get('volume')),
            'broken_count': num(pool.get('broken_count')), 'seal_time': pool.get('last_seal_time'),
            'relimit': ticker in today if current_rows is not None else None, 'one_price': one_price, 'previous_one_price': prev_one,
            'previous_bar_ready': prev_one is not None,
            'status': status, 'supported': status == 'supported'})
    front = [r for r in details if r['front'] is True]
    back = [r for r in details if r['front'] is False]
    unranked = [r for r in details if r['front'] is None]
    observable = [r for r in front if r['status'] in ('supported', 'weak')]
    supported = sum(r['supported'] for r in observable)
    rate = supported / len(observable) if observable else None
    f, b = med([r['return_1d'] for r in front]), med([r['return_1d'] for r in back])
    spread = f - b if f is not None and b is not None else None
    status = 'pending' if rate is None else 'strong' if rate >= .67 else 'weak' if rate <= .33 else 'medium'
    promotions = []
    for level in (1, 2):
        cohort = [r for r in details if r['boards'] == level]
        eligible = [r for r in cohort if r['previous_bar_ready'] and not r['previous_one_price']]
        observed = [r for r in eligible if r['return_1d'] is not None]
        count = sum(r['relimit'] is True for r in observed)
        complete = current_rows is not None and bool(eligible) and len(observed) == len(eligible) and all(r['previous_bar_ready'] for r in cohort)
        promotions.append({'label': '一进二' if level == 1 else '二进三', 'sample': len(eligible),
            'coverage': len(observed), 'promoted': count, 'rate': count / len(eligible) if complete else None})
    return {'status': status, 'label': {'pending': '承接待确认', 'strong': '价格承接较强', 'weak': '价格承接偏弱', 'medium': '价格承接一般'}[status],
        'theme': theme, 'cohort_date': previous_date, 'front_count': len(front), 'back_count': len(back),
        'unranked_count': len(unranked), 'unranked_median_return': med([r['return_1d'] for r in unranked]),
        'rank_note': ('在所选视角完整成分中，按截至昨日的近7日高度/持续性/容量冻结前排候选，其余为后排观察成分；成分时点见来源。' if members is not None else '按昨日收盘已知的近7日活跃成员冻结角色，保留已断板者；当日新面孔另列。' if history_rows is not None else '昨日样本均为首板，单凭板数无法确认前后排，列为同梯队待分层。' if unranked else ''),
        'observable_count': len(observable), 'supported_count': supported,
        'locked_count': sum(r['status'] == 'locked' for r in front),
        'missing_count': sum(r['status'] == 'pending' or r['return_1d'] is None for r in front), 'support_rate': rate,
        'front_median_return': f, 'back_median_return': b, 'front_back_spread': spread,
        'promotions': promotions, 'rows': details,
        'rule': ('同一方向、截至昨日近7日出现过涨停的固定活跃成员。前排候选=昨日梯队或窗口最高高度及下一档且至少3板，或至少2板且两日活跃、昨日成交额≥20亿；有前排锚点时其余为后排候选。角色不按今日涨跌重排，今日新增另列；前排候选不等于已验证龙头。' if history_rows is not None else '昨日同方向涨停股为固定样本；有连板时，前排=最高板及下一档且最低2板，其余为后排；全为首板时列同梯队待分层。') + '收益≥−1%且收盘在日内区间55%以上为价格承接。开盘=收盘=涨停价且炸板0次，或已封板且高低收同价，识别一字封死；一字不证明换手承接，晋级率排除昨日一字。缺分钟回封过程，不判断资金主体。'}


def theme_evidence(*, analysis_date, limit_rows_by_date, current_bars, previous_bars, bars_by_ticker,
                   moneyflow, moneyflow_by_date=None, sessions=None, basic=None, previous_date='',
                   current_price_limits=None, previous_price_limits=None, perspectives=None, full_members=None, certified_cores=None):
    if perspectives is not None and not perspectives.get('perspectives'):
        return {'status': 'pending', 'date': analysis_date, 'themes': [], 'active_themes': [],
                'data_note': '概念题材与细分行业成分尚未生成，请生成所选日复盘；不回退到宽泛行业。'}
    if perspectives and perspectives.get('perspectives'):
        from shilun.review.topic_attention import within_review_member_limit, MAX_REVIEW_MEMBERS
        tracks = []
        core_dates = sorted({str(r['date'])[:10] for history in bars_by_ticker.values() for r in history if str(r['date'])[:10] <= analysis_date})
        for perspective in perspectives['perspectives']:
            if not within_review_member_limit(perspective):
                continue
            from shilun.market.research_scope import stock_allowed
            members = {r['con_code']: r for r in perspective['members'] if stock_allowed((basic or {}).get(r['con_code'], r), analysis_date)}
            scoped = {d: [{**r, 'theme_name': perspective['name']} for r in rows if r['ticker'] in members]
                      for d, rows in limit_rows_by_date.items()}
            # Apply the same entry gate before expensive history calculations.
            # A full-catalog screen must not rescan all-market bars per topic.
            gate_dates = [d for d in (sessions or sorted(scoped)) if d <= analysis_date][-7:]
            heights = {}
            for d in gate_dates:
                for r in scoped.get(d, []):
                    heights[r['ticker']] = max(heights.get(r['ticker'], 0), board(r))
            cores = [r for t, r in (certified_cores or {}).items() if t in members and t in heights]
            if not cores or sum(v >= 2 for v in heights.values()) < 2:
                continue
            member_history = {t: bars_by_ticker[t] for t in members if t in bars_by_ticker}
            evidence = theme_evidence(analysis_date=analysis_date, limit_rows_by_date=scoped,
                current_bars=current_bars, previous_bars=previous_bars, bars_by_ticker=member_history,
                moneyflow=moneyflow, moneyflow_by_date=moneyflow_by_date, sessions=sessions,
                basic=basic, previous_date=previous_date, current_price_limits=current_price_limits,
                previous_price_limits=previous_price_limits, full_members={perspective['name']: set(members)}, certified_cores=certified_cores)
            if not evidence['themes']:
                continue
            track = evidence['themes'][0]
            prior_dates = [d for d in (sessions or sorted(scoped)) if d < analysis_date][-7:]
            track['front_support'] = front_support(scoped.get(previous_date, []), current_bars, previous_bars,
                current_rows=scoped.get(analysis_date), previous_date=previous_date, theme=perspective['name'],
                current_price_limits=current_price_limits, previous_price_limits=previous_price_limits,
                history_rows=[(d, r) for d in prior_dates for r in scoped.get(d, [])], members=members,
                price_history=member_history)
            track.update({k: v for k, v in perspective.items() if k != 'members'})
            track['member_count'] = perspective.get('member_count', len(perspective['members']))
            track['research_member_count'] = len(members)
            from shilun.review.emotion_history import return_path
            from shilun.review.topic_attention import attention_core
            leaders = []
            for core in cores:
                cycle, leg = core.get('cycle') or {}, core.get('leg') or {}
                path = return_path(core['ticker'], cycle.get('start_date'), analysis_date, core_dates, member_history, include_start=True)
                leaders.append({'ticker':core['ticker'], 'name':core['name'], 'max_board':cycle.get('max_streak'),
                    'start_date':cycle.get('start_date'), 'startup_return':cycle.get('cumulative_return'),
                    'core_qualified_at':core.get('qualified_at'), 'latest_board_date':core.get('latest_limit_date'),
                    'active_board_days':sum(core['ticker'] == r['ticker'] for rows in scoped.values() for r in rows),
                    'series':path['series'], 'price_basis':path.get('price_basis'),
                    'qualification_boards':leg.get('qualification_board_count'),
                    'reason':'已通过独立核心资格核验；板块带动程度由跟随股反馈另外判断。'})
            leaders.sort(key=lambda r: (r['max_board'] or 0, r['startup_return'] if r['startup_return'] is not None else -2, r['active_board_days']), reverse=True)
            track['round'].update(leaders=leaders, attention_core=attention_core(leaders, analysis_date), rule='核心必须在本段先有三连板、并从首板起十个交易日内至少五次涨停；走势累计涨幅单独保留。候选先比较本轮高度，再比较启动涨幅及活跃天数。')
            track['gates'].pop('two_label_agreement', None)
            track['gates'].pop('three_board_in_7d', None)
            track['gates']['core_qualified'] = True
            track['cycle'].update(sector_code=perspective['code'], taxonomy_date=analysis_date)
            track['source_label'] = (perspective.get('source_label') or ('东方财富概念' if perspective['type'] == 'N' else '东方财富三级行业')) + ' · ' + perspective['code']
            track['membership_note'] = perspectives['membership_note']
            # Whole-member money calculations must not inherit a limit-pool proxy.
            paired = [t for t in members if num(current_bars.get(t, {}).get('amount')) is not None
                      and num(previous_bars.get(t, {}).get('amount')) is not None]
            ready = len(paired) / len(members) >= .95
            amount = sum(current_bars[t]['amount'] for t in paired) if ready else None
            before = sum(previous_bars[t]['amount'] for t in paired) if ready else None
            track.update(capital_scope='所选视角成分（剔除ST及退市股）', capital_coverage=len(paired) / len(members),
                         amount_yi=amount / 100000 if amount is not None else None,
                         amount_ratio_vs_previous=amount / before if before else None,
                         net_flow_yi=None, prior_net_flow_yi=None,
                         capital_signal='成交额改善' if before and amount > before else '成交额未改善' if ready else '成交额待补')
            track['gates']['capital_improves'] = amount / before >= 1.10 if before else None
            track['status'] = 'proven' if all(v is True for v in track['gates'].values()) else 'candidate'
            track['validation_note'] = ('核心及跟随股条件已满足；成交额也达到改善条件。' if track['gates']['capital_improves'] is True else
                '核心及跟随股条件已满足；成交额较昨日未增加10%。' if track['gates']['capital_improves'] is False else
                '核心及跟随股条件已满足；完整成分成交额覆盖不足，资金条件待补。')
            track['front_support']['rule'] = ('先选视角再分层：完整成分中按截至昨日证据冻结角色。高度前排=昨日最高梯队及下一档、最低3板，或近7日最高高度及下一档、最低3板。趋势前排=20日收益本视角前20%、20日涨幅≥30%、10日≥20%、收盘≥MA5；容量前排=同一相对排名、10日≥10%、收盘≥MA5且成交额≥20亿。阈值是研究规则，未验证为胜率优势。其余列后排观察；不等于补涨龙或同一催化，一字不作换手确认。')
            tracks.append(track)
        from shilun.review.topic_attention import rank_theme_attention, VERSION as ATTENTION_VERSION, RULE as ATTENTION_RULE
        tracks = rank_theme_attention(tracks, analysis_date)
        return {'status': 'candidate', 'label': '概念题材与细分行业', 'date': analysis_date, 'themes': tracks,
                'perspective_version': perspectives.get('version'),
                'attention_version': ATTENTION_VERSION, 'attention_rule': ATTENTION_RULE,
                'member_limit': MAX_REVIEW_MEMBERS,
                'active_themes': [r['name'] for r in tracks if r['selected']][:3],
                'data_note': perspectives['membership_note'], 'perspective_rule': perspectives['rule'],
                'shortlist_count': perspectives['shortlist_count'], 'missing_members': perspectives['missing_members']}
    dates = [d for d in (sessions or sorted(limit_rows_by_date)) if d <= analysis_date][-7:]
    previous_date = previous_date or (dates[-2] if len(dates) > 1 else '')
    grouped = defaultdict(lambda: {'rows': [], 'levels': {}, 'today': [], 'previous': []})
    for d in dates:
        for row in limit_rows_by_date.get(d, []):
            item = grouped[theme_name(row)]
            item['rows'].append(row)
            item['levels'][row['ticker']] = max(item['levels'].get(row['ticker'], 0), board(row))
            if d == analysis_date: item['today'].append(row)
            if d == previous_date: item['previous'].append(row)
    observed_days = sum(d in limit_rows_by_date for d in dates)
    complete_window = len(dates) == 7 and observed_days == 7
    history_dates = sorted({str(r.get('date'))[:10] for history in bars_by_ticker.values() for r in history
                            if str(r.get('date'))[:10] <= analysis_date} | set(sessions or [])
                           | {d for d in limit_rows_by_date if d <= analysis_date})
    prior_dates = [d for d in (sessions or sorted(limit_rows_by_date)) if d < analysis_date][-7:]
    themes = []
    for name, item in grouped.items():
        today, yesterday = item['today'], item['previous']
        scope_kinds = {topic_scope(r)[1] for r in item['rows']}
        chain = bool(scope_kinds & {'industry_chain_candidate', 'retrospective_annotation'})
        genuine_theme = any(any(r.get(k) for k in ('theme_name', 'concept_name', 'topic')) for r in item['rows'])
        # Full industry membership for the proxy, fixed 7-session cohort otherwise.
        members = {t for t, r in (basic or {}).items() if r.get('sector') == name} if not genuine_theme and not chain else set()
        members = set((full_members or {}).get(name, [])) or members
        full_membership = bool(members)
        members = members or set(item['levels'])
        paired = [t for t in members if num(current_bars.get(t, {}).get('amount')) is not None and num(previous_bars.get(t, {}).get('amount')) is not None]
        amounts_ready = bool(members) and len(paired) / len(members) >= .95
        current_amount = sum(num(current_bars[t]['amount']) for t in paired) / 100000 if amounts_ready else None
        prior_amount = sum(num(previous_bars[t]['amount']) for t in paired) / 100000 if amounts_ready else None
        amount_ratio = ratio(current_amount, prior_amount)
        previous_flow = (moneyflow_by_date or {}).get(previous_date, {})
        flow_members = [t for t in members if num(moneyflow.get(t, {}).get('net_mf_amount')) is not None and num(previous_flow.get(t, {}).get('net_mf_amount')) is not None]
        flow_ready = bool(members) and len(flow_members) / len(members) >= .95
        flow = sum(num(moneyflow[t]['net_mf_amount']) for t in flow_members) / 10000 if flow_ready else None
        prior_flow = sum(num(previous_flow[t]['net_mf_amount']) for t in flow_members) / 10000 if flow_ready else None
        amount_improves = amount_ratio is not None and amount_ratio >= .10
        flow_improves = flow_ready and flow > 0 and flow > prior_flow
        east, pool_labels = set(), set()
        for row in today:
            east.update(labels(row, ('eastmoney_concepts', 'eastmoney_concept', 'em_concepts', 'em_concept')))
            pool_labels.update(labels(row, ('theme_name', 'concept_name', 'topic', 'industry', 'sector_name')))
        agreement = sorted(east & pool_labels)
        round_info = round_evidence(name, set(item['levels']), limit_rows_by_date, history_dates, analysis_date, bars_by_ticker)
        max_board = max(item['levels'].values(), default=0)
        two_plus = sum(v >= 2 for v in item['levels'].values())
        gates = {'three_board_in_7d': max_board >= 3, 'two_stocks_two_plus_in_7d': two_plus >= 2,
            'capital_improves': (True if amount_improves or flow_improves else False if amounts_ready or flow_ready else None) if full_membership else None,
            'two_label_agreement': len(agreement) >= 2 if east else None}
        # Positive existence remains provable with holes; absence does not.
        if not complete_window:
            for k in ('three_board_in_7d', 'two_stocks_two_plus_in_7d'):
                if not gates[k]: gates[k] = None
        broken_ready = all(num(r.get('broken_count')) is not None for r in today)
        returns = [ratio(current_bars.get(t, {}).get('close'), num(current_bars.get(t, {}).get('pre_close')) or previous_bars.get(t, {}).get('close')) for t in members]
        returns = [v for v in returns if v is not None]
        cycle_ready = analysis_date in limit_rows_by_date and previous_date in limit_rows_by_date and broken_ready
        cycle = classify_sector_cycle(first_board_count=sum(board(r) == 1 for r in today),
            prior_first_board_count=sum(board(r) == 1 for r in yesterday), max_board=max((board(r) for r in today), default=0),
            board_count=len(today), broken_count=sum((num(r.get('broken_count')) or 0) > 0 for r in today),
            up_ratio=sum(v > 0 for v in returns) / len(returns) if returns else None, net_flow=flow) if cycle_ready else {
                'code': 'pending', 'label': '周期数据待补齐', 'reason': '昨日板池或今日开板记录缺失，暂不判突发、分化或回流。'}
        from shilun.review.cycle_diagnostics import require_cycle_inputs
        missing_inputs = ([] if analysis_date in limit_rows_by_date else ['current_limit_pool']) + ([] if previous_date in limit_rows_by_date else ['previous_limit_pool']) + ([] if broken_ready else ['broken_count'])
        cycle = require_cycle_inputs(cycle, missing_inputs)
        if cycle_ready and cycle['code'] != 'unconfirmed':
            cycle['reason'] = (f"今日{len(today)}家涨停、最高{max((board(r) for r in today), default=0)}板；"
                f"首板由{sum(board(r) == 1 for r in yesterday)}家变为{sum(board(r) == 1 for r in today)}家。"
                "阶段是价格与供给特征判断，不等同于已验证资金集中或消息催化。")
        core_gate = any(t in (certified_cores or {}) for t in item['levels']) if certified_cores is not None else max_board >= 3
        if not core_gate or two_plus < 2:
            continue
        themes.append({'name': name, 'status': 'proven' if all(v is True for v in gates.values()) else 'candidate',
            'source_kind': 'theme' if genuine_theme else 'industry_chain_candidate' if chain else 'industry_proxy',
            'source_label': '题材字段' if genuine_theme else '跨行业农业链候选 · 当日催化待核验' if chain else '行业线索 · 待题材交叉验证',
            'max_board_7d': max_board, 'two_plus_stock_count_7d': two_plus, 'current_count': len(today),
            'round': round_info,
            'previous_count': len(yesterday) if previous_date in limit_rows_by_date else None,
            'first_board_count': sum(board(r) == 1 for r in today), 'amount_yi': current_amount,
            'amount_ratio_vs_previous': amount_ratio + 1 if amount_ratio is not None else None,
            'capital_scope': '同一行业全体成员' if full_membership else '固定7日样本（非全板块）',
            'capital_coverage': len(paired) / len(members) if members else 0,
            'net_flow_yi': flow, 'prior_net_flow_yi': prior_flow,
            'capital_signal': '成交额改善' if amount_improves else '净流入改善' if flow_improves else '未改善' if amounts_ready or flow_ready else '资金待补齐',
            'agreement_count': len(agreement), 'agreement_labels': agreement, 'gates': gates, 'cycle': cycle,
            'membership_note': '农业链只提供跨行业候选，不将所有农化个股判为同一催化。红棉在9月8日至9日的归属由用户9月14日复盘补录，不用于事前回测。' if chain else '',
            'new_members': [{**r, 'membership_note': topic_scope(r)[2]} for r in today if r['ticker'] not in {r['ticker'] for d in prior_dates for r in limit_rows_by_date.get(d, []) if theme_name(r) == name}],
            'front_support': front_support(yesterday, current_bars, previous_bars,
                current_rows=today if analysis_date in limit_rows_by_date else None, previous_date=previous_date, theme=name,
                current_price_limits=current_price_limits, previous_price_limits=previous_price_limits,
                history_rows=[(d, r) for d in prior_dates for r in limit_rows_by_date.get(d, []) if theme_name(r) == name])})
    themes.sort(key=lambda r: (r['status'] == 'proven', r['current_count'], r['max_board_7d'], r['amount_yi'] or 0), reverse=True)
    return {'status': 'proven' if any(r['status'] == 'proven' for r in themes) else 'candidate', 'label': '多方向并行观察',
        'themes': themes[:12], 'active_themes': [r['name'] for r in themes[:3]], 'window_dates': dates,
        'window_days': 7, 'observed_days': observed_days,
        'rule': '准入：近7个交易日至少1只达到3板、至少2只不同个股达到2板（包含3板股）。未过门槛不列为题材；资金与标签用于进一步验证。',
        'data_note': f'7日板池覆盖 {observed_days}/{len(dates)}；默认并行展示前三个方向。行业线索不是已确认题材，缺概念归属时不判“已证明”。'}


def limit_up_resonance(*, analysis_date, previous_date, current_count, previous_count, limit_rows_by_date, indices, sessions=None, summaries=None):
    dates = [d for d in (sessions or sorted(limit_rows_by_date)) if d < analysis_date][-20:]
    dated_counts = {d: len(limit_rows_by_date[d]) if d in limit_rows_by_date else num((summaries or {}).get(d, {}).get('limit_up')) for d in dates}
    counts = [v for v in dated_counts.values() if v is not None]
    baseline = med(counts) if len(counts) == 20 else None
    moves = [num(i.get('pct_chg')) for i in indices]
    gaps = [num(i.get('index_vs_ma20')) for i in indices]
    index_move = mean(moves) if len(moves) == 4 and all(v is not None for v in moves) else None
    index_gap = mean(gaps) if len(gaps) == 4 and all(v is not None for v in gaps) else None
    change, deviation = ratio(current_count, previous_count), ratio(current_count, baseline)
    relation = 'pending'
    if all(v is not None for v in (change, deviation, index_move, index_gap)):
        supply_sign = 1 if change > 0 and deviation > 0 else -1 if change < 0 and deviation < 0 else 0
        index_sign = 1 if index_move > 0 and index_gap > 0 else -1 if index_move < 0 and index_gap < 0 else 0
        relation = 'mixed' if not supply_sign or not index_sign else 'resonance' if supply_sign == index_sign else 'counter_index'
    daily_relation = ('pending' if change is None or index_move is None else
        'same_up' if change > 0 and index_move > 0 else
        'same_down' if change < 0 and index_move < 0 else
        'opposite' if change * index_move < 0 else 'flat')
    daily_label = {'pending': '当日方向待确认', 'same_up': '当日同涨', 'same_down': '当日同跌',
                   'opposite': '当日供给与指数背离', 'flat': '当日方向未一致'}[daily_relation]
    return {'relation': relation, 'label': {'pending': '共振待确认', 'mixed': '供给与指数信号混合', 'resonance': '涨停供给与指数共振', 'counter_index': '涨停供给与指数反指'}[relation],
        'daily_relation': daily_relation, 'daily_label': daily_label,
        'structure_label': {'pending':'20日结构待确认', 'mixed':'20日结构分歧', 'resonance':'20日结构同向', 'counter_index':'20日结构反向'}[relation],
        'current_count': current_count, 'previous_count': previous_count, 'baseline_20d': baseline, 'supply_change': change,
        'supply_vs_baseline': deviation, 'index_change': index_move, 'index_vs_ma20': index_gap, 'sample_days': len(counts),
        'missing_dates': [d for d, v in dated_counts.items() if v is None],
        'available_sample_median': med(counts),
        'rule': f'当日方向只比较涨停数较昨日变化与四宽基等权日涨跌。20日结构另要求供给较昨日和较前20交易日中位数同向、指数日涨跌与相对MA20同向；结构分歧不表示今日一涨一跌。基准覆盖 {len(counts)}/20；不足不替代20日基准。'}


def downside_risk(*, analysis_date, limit_down_tickers, current_bars, previous_bars, bars_by_ticker,
                  bars_by_date, price_limits_by_date, basic, indices, sessions=None, count_ready=True, suspensions=None):
    dates = [d for d in (sessions or sorted(bars_by_date)) if d < analysis_date][-20:]
    positions = dict.fromkeys(('high', 'middle', 'low', 'unknown'), 0 if count_ready else None)
    themes, rows, baseline_counts = Counter(), [], []
    for ticker in sorted(limit_down_tickers):
        suspended = (suspensions or {}).get(ticker, set())
        position_dates = [d for d in (sessions or sorted(bars_by_date)) if d < analysis_date and d not in suspended][-20:]
        history = [bars_by_date.get(d, {}).get(ticker, {}) for d in position_dates]
        highs, lows = [num(r.get('high')) for r in history], [num(r.get('low')) for r in history]
        close = num(previous_bars.get(ticker, {}).get('close'))
        if close is None and dates and dates[-1] in suspended and history:
            close = num(history[-1].get('close'))
        complete = len(history) == 20 and close is not None and all(v is not None for v in highs + lows)
        location = (close - min(lows)) / (max(highs) - min(lows)) if complete and max(highs) > min(lows) else None
        position = 'unknown' if location is None else 'high' if location >= .7 else 'middle' if location >= .35 else 'low'
        if count_ready: positions[position] += 1
        name = basic.get(ticker, {}).get('sector') or '未分类'
        if name == '分类待补齐' or basic.get(ticker, {}).get('classification_status', 'ready') != 'ready':
            name = '未分类'
        themes[name] += 1
        rows.append({'ticker': ticker, 'name': basic.get(ticker, {}).get('name') or ticker, 'theme': name,
            'position': position, 'position_ratio': location,
            'history_days': sum(h is not None and l is not None for h, l in zip(highs, lows)),
            'missing_dates': [d for d, h, l in zip(position_dates, highs, lows) if h is None or l is None],
            'suspended_dates': sorted(d for d in dates if d in suspended),
            'position_as_of': position_dates[-1] if position_dates else None,
            'position_note': '以前20个有效交易日区间定位；仅跳过已核验的全天停牌日，真实缺失不补零。'})
    for d in dates:
        bars, limits = bars_by_date.get(d, {}), price_limits_by_date.get(d, {})
        hits = [price_limit_hit(r.get('close'), limits.get(t, {})) for t, r in bars.items()]
        if hits and all(v is not None for v in hits): baseline_counts.append(sum(v is True for v in hits))
    baseline = med(baseline_counts) if len(baseline_counts) == 20 else None
    changes = [ratio(r.get('close'), previous_bars.get(t, {}).get('close')) for t, r in current_bars.items()]
    changes = [r for r in changes if r is not None]
    breadth = sum(v < 0 for v in changes) / len(changes) if changes else None
    count = len(limit_down_tickers) if count_ready else None
    known = themes.copy(); known.pop('未分类', None)
    top = known.most_common(3)
    top1 = top[0][1] / count if top and count else None
    top3 = sum(v for _, v in top) / count if top and count else None
    high_mid = ((positions['high'] or 0) + (positions['middle'] or 0)) / count if count else None
    negative = sum(num(i.get('pct_chg')) is not None and num(i.get('pct_chg')) < 0 for i in indices)
    risk, label, penalty = 'pending', '数据不足，暂不判断扩散', None
    if count == 0: risk, label, penalty = 'none', '当日无收盘跌停股', 0
    elif count_ready:
        if baseline is not None and count >= max(10, baseline * 2) and breadth is not None and breadth >= .65 and top3 is not None and top3 < .6 and not themes['未分类'] and negative >= 2:
            risk, label, penalty = 'marketwide_contagion', '全市场亏钱效应扩散', 18
        elif (positions['high'] or 0) >= 3 or count >= 5 and high_mid is not None and high_mid >= .4:
            risk, label, penalty = 'high_level_collapse', '中高位亏钱效应集中', 12
        elif count >= 3 and top1 is not None and top1 >= .5 and breadth is not None and breadth < .65:
            risk, label, penalty = 'local_theme_retreat', '局部行业风险集中', 6
        elif baseline is not None: risk, label, penalty = 'none', '未达到批量扩散门槛', 0
    return {'status': 'pending' if risk == 'pending' else 'confirmed', 'risk_type': risk, 'label': label, 'penalty': penalty,
        'limit_down_count': count, 'count_ready': count_ready, 'observed_count': len(limit_down_tickers),
        'position_counts': positions, 'baseline_20d': baseline, 'baseline_sample_days': len(baseline_counts),
        'down_breadth': breadth, 'high_middle_share': high_mid, 'top1_share': top1, 'top3_share': top3,
        'concentration_source': '行业代理（非概念题材）', 'stocks': rows,
        'theme_concentration': [{'theme': n, 'count': v, 'share': v / count if count else None} for n, v in top],
        'theme_cluster_status': 'none' if count == 0 else 'pending',
        'theme_cluster_note': '当日没有跌停股。' if count == 0 else '概念题材归属未齐，不能确认题材集中跌停；以下仅列行业同向聚集，不等同题材风险。',
        'industry_clusters': [{'theme': n, 'count': v, 'share': v / count if count else None,
            'stocks': [r['name'] for r in rows if r['theme'] == n]} for n, v in known.most_common() if v >= 2],
        'state_rules': [
            {'code': 'marketwide_contagion', 'label': '全市场扩散', 'penalty': 18,
             'rule': '跌停≥max(10家,20日中位基准×2)，下跌广度≥65%，行业Top3占比<60%，行业归属完整且至少两个宽基下跌。'},
            {'code': 'high_level_collapse', 'label': '中高位集中', 'penalty': 12,
             'rule': '高位跌停≥3家；或跌停≥5家且中高位合计占比≥40%。'},
            {'code': 'local_theme_retreat', 'label': '局部集中', 'penalty': 6,
             'rule': '跌停≥3家，单一行业占比≥50%，且下跌广度<65%；当前是行业代理，非已确认题材。'}],
        'data_note': '涨跌停价未覆盖当日日线，已识别数仅是下限。' if not count_ready else '无跌停样本，位置计数为0，集中度不适用。' if count == 0 else '位置按昨日收盘在此前20交易日高低区间计算；历史不足归未知。',
        'rule': '高位≥70%，中位35%–70%，低位<35%；全市场扩散优先扣18分，中高位集中扣12分，局部集中扣6分。每日重新判定，只扣最高优先级一项，不叠加。缺必要数据待确认；有完整基准但均未命中则未达阈值。行业代理不等同于主题归属。'}
