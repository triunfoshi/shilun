"""Inspectable research judgments, separate from trading permission."""
from .engine import number, stats


def citic_evidence(data):
    day = data['date']
    futures = data.get('review', {}).get('market', {}).get('futures', {})
    rows = []
    for p in futures.get('products', []):
        fields = [p.get(k) for k in ('citic_long', 'citic_short', 'citic_long_change', 'citic_short_change')]
        ready = p.get('data_date') == day and all(number(v) for v in fields)
        consistent = p.get('source_consistency') != 'mismatch' and p.get('ths_consistency') != 'mismatch'
        rows.append({'product': p.get('product'), 'contract': p.get('contract'), 'date': p.get('data_date'),
            'long': fields[0] if ready and consistent else None, 'short': fields[1] if ready and consistent else None,
            'net_short': fields[1]-fields[0] if ready and consistent else None,
            'change': fields[3]-fields[2] if ready and consistent else None,
            'long_change': fields[2] if ready and consistent else None,
            'short_change': fields[3] if ready and consistent else None,
            'source': p.get('source_url'), 'status': 'ready' if ready and consistent else 'missing'})
    ready = len(rows) == 4 and len({r['product'] for r in rows}) == 4 and all(r['status'] == 'ready' for r in rows)
    return {'date': day, 'status': 'ready' if ready else 'missing', 'rows': rows,
        'net_short': sum(r['net_short'] for r in rows) if ready else None,
        'change': sum(r['change'] for r in rows) if ready else None,
        'long_change': sum(r['long_change'] for r in rows) if ready else None,
        'short_change': sum(r['short_change'] for r in rows) if ready else None,
        'note': '净空＝持卖单量－持买单量；变化＝空单变化－多单变化。仅合计四个展示合约，单位手，不代表全部交割月份或等额名义敞口。席位仅作旁证，不单独决定指数方向。'}


def market_outlook(market):
    rows = market['indices']
    valid = [r for r in rows if number(r.get('change_pct')) and number(r.get('above_count')) and number(r.get('previous_above_count'))]
    turnover, delta = market['turnover'].get('market_amount_cny'), market.get('turnover_change_pct')
    supports, counters = [], []
    if len(valid) != 4 or not number(turnover) or not number(delta):
        state, title = 'unknown', '大盘预期待判断'
        counters.append('指数均线或同口径成交额缺失，不能替代为下降预期')
    else:
        rising = sum(r['change_pct'] > 0 for r in valid)
        improving = sum(r['above_count'] > r['previous_above_count'] for r in valid)
        losing = sum(r['above_count'] < r['previous_above_count'] for r in valid)
        aligned = sum(r['bull_aligned'] is True for r in valid)
        supports.append(f'{rising}/{len(valid)}个指数较昨收上涨，{improving}个指数均线站位增加')
        if aligned < len(valid):
            counters.append(f'{len(valid)-aligned}个指数尚未形成MA5＞MA10＞MA20')
        (supports if delta > 0 else counters).append(f'全市场成交额{turnover/1e12:.3f}万亿，较前日{delta:+.2f}%')
        if rising >= 3 and aligned >= 3 and delta > 0:
            state, title = 'up', '上涨延续预期，继续核查资金去向'
        elif rising <= 1 and losing >= 2:
            state, title = 'down', '下降预期，先看修复能否重新成立'
        else:
            state, title = 'repair', '修复延续预期，等待量能与结构确认' if rising >= 3 else '震荡修复预期，方向尚未一致'
    f = market['citic']
    if f['status'] == 'ready':
        text = f'中信展示合约净空{f["net_short"]:,}手，较前日{f["change"]:+,}手'
        (counters if f['change'] > 0 else supports).append(text)
        if f['change'] > 0 and f['short_change'] < 0:
            counters.append('净空增加来自多单减仓更多，不能解释成主动新增同等数量空单')
    else:
        counters.append('中信当日完整合约数据待补，不使用其他日期代替')
    return {'state': state, 'title': title, 'support': supports, 'counter': counters,
        'upgrade': '指数继续上行、均线站位改善且同口径量能扩张，再结合目标板块和赚钱效应确认',
        'failure': '多数指数均线站位回落、修复不能恢复，并伴随核心负反馈时下调预期',
        'rule': '研究规则：至少3/4指数上涨且多头排列、成交额较昨增加→上涨；至多1/4上涨且至少2个指数丢失均线→下降；其余为修复或震荡。数据不齐为待判断。中信只作旁证。'}


def assess_group(g):
    daily = [r for r in g.get('daily', []) if r['return_pct']['n'] > 0]
    last = daily[-1] if daily else {}
    recent_returns = stats([r['return_pct']['median'] for r in daily[-3:]])
    recent = g.get('recent', {})
    premium = recent.get('premium', {}).get('median')
    continuation = recent.get('windows', {}).get('1', {}).get('continuation', {}).get('median')
    complete = len(daily) >= 3 and all(r['return_pct']['n'] >= max(3,r['cohort_n']*.95) for r in daily[-3:])
    value = recent_returns['median']
    today = last.get('return_pct', {}).get('median')
    price_support = value > 0 and today > 0 if complete and number(value) and number(today) else None
    turns = last.get('turnover_pct', {})
    health_ready = bool(last) and turns.get('n', 0) == last.get('valid_n') and last.get('volume_ratio', {}).get('n', 0) == last.get('valid_n')
    health = '已有逐日换手、量能证据，需结合核心量价核验' if health_ready else '换手或量能对照不完整，参与条件待核验'
    if last and last['one_price_n'] == last['valid_n']:
        health = '当前可见样本均为一字，未取得真实换手确认'
    return {'price_support': price_support, 'recent_return_median': value, 'today_return_median': today,
        'recent_premium_median': premium, 'recent_continuation_median': continuation,
        'health': health, 'health_ready': health_ready, 'days_n': len(daily),
        'label': '近期价格正反馈' if price_support is True else '近期价格反馈偏弱' if price_support is False else '近期样本不足',
        'note': '近期判断取近3个交易日固定样本日收益中位数的中位数，并核对最新日；至少3日、每日3个有效样本且收益覆盖≥95%才作方向判断。此为研究门槛，不代表统计显著性。不把红K比例当胜率，不以价格正反馈代替可参与性。'}


def decision_conclusion(market, groups, route, band):
    chosen = groups['trend'] if band == 'all' else groups['trend']['bands'][band]
    judged = {'trend': assess_group(chosen), 'relay': assess_group(groups['relay']), 'crowding': assess_group(groups['crowding'])}
    names = {'trend': '首板趋势', 'relay': '连板', 'crowding': '抱团核心'}
    eligible = ['trend'] if route.get('mode') == 'A' else ['crowding', 'relay'] if route.get('mode') == 'B' else []
    positive = [k for k in eligible if judged[k]['price_support'] is True]
    # Different admission stages are descriptive populations, not comparable
    # strategies. A larger conditional return must never select the B submode.
    if positive:
        headline = '、'.join(names[k] for k in positive) + '有价格正反馈；按环境与承接核验，不按收益排名选模式'
    elif eligible and all(judged[k]['price_support'] is False for k in eligible):
        headline = '当前适用群体反馈偏弱，等待新的确认机会'
    else:
        headline = '当前适用群体证据不足，保留条件观察'
    holding = ('默认次日套利；各组后续收益只描述本组历史，不据此判定跨模式的续持优势。'
               '超预期且原逻辑成立可留；符合预期仅在无更好机会或趋势优势另有证据时开放例外；不及预期不延长。')
    return {'headline': headline, 'preferred_group': None, 'ranking_applied': False,
        'positive_groups': positive, 'groups': judged,
        'existing_position': holding,
        'new_entry': ('A路径先确认指数、方向和容量核心，再观察MA5主备；次日开盘溢价属于先手反馈，不能算成新买收益。' if route.get('mode') == 'A' else
            'B路径分别核查抱团承接与接力生态。测试版未启用相应执行策略，保留观察；当前不输出MA5新开仓主选。' if route.get('mode') == 'B' else
            '当前没有确认的适用路径；先补齐证据，不依据单组收益跳过上游条件。'),
        'recent_rule': '日反馈使用相同最近5个交易日，判断参考最近3日；日历相同不代表入组阶段相同，只判断本组反馈，不排名。历史事件收益另列入组日期与成熟窗口。',
        'comparability': '首板是启动样本，三板是晋级样本，核心是已通过更强资格筛选的样本；同一只股票可跨组，含ST口径也不同。累计收益不能直接证明哪种模式更好，新开仓还受买点、换手和可成交性约束。'}


def first_board_next_day_check(events, day, next_day, band):
    """Freeze T's first boards for T+1 verification, without reading T+1."""
    formed = [r for r in events if r['group'] == 'trend' and r['date'] == day]
    allowed = {'lt50'} if band == 'lt50' else {'lt50', '50to100'} if band == 'lt100' else {'lt50', '50to100', 'ge100'}
    selected = {r['ticker']: r for r in formed if r.get('band') in allowed}
    return {'formation_date': day, 'observation_date': next_day, 'band': band,
        'status': 'pending', 'cohort_n': len(selected), 'total_n': len(formed),
        'unknown_amount_n': sum(r.get('band') not in {'lt50','50to100','ge100'} for r in formed),
        'members': [{'ticker': t, 'name': r.get('name', t)} for t, r in sorted(selected.items())],
        'opening_premium_median': None, 'positive_open_pct': None,
        'note': '名单在预案日收盘冻结；次日看开盘溢价中位数、正溢价占比、负反馈及开盘后能否保持。开盘溢价对昨收；盘中红K对当日开盘，两者分开。缺行情不填0，不按次日赢家重选名单。'}
