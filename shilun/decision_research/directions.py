"""A bounded watchlist: mode, recent breadth, dated cores, then strategy."""
from .engine import number, stats, ret, price_path_valid


def shortlist(e, data, directions, route, core_history):
    review = data.get('review', {})
    themes = {r.get('code'): r for r in review.get('sentiment', {}).get('theme_evidence', {}).get('themes', [])} if review.get('analysis_date') == e.date else {}
    qualified = {r['ticker']: r for r in core_history['current']}
    stock_cache = {}
    def stock(t):
        if t not in stock_cache:
            stock_cache[t] = e.stock(t)
        return stock_cache[t]
    def change(t, n):
        path = [e.row(t, d) for d in e.days[-n-1:]]
        return ret(path[-1].get('close'), path[0].get('close')) if len(path) == n+1 and price_path_valid(path) else None
    evaluated = []
    for original in directions:
        d = dict(original)
        theme = themes.get(d['code'], {})
        coverage = d['valid_n']/d['member_n'] if d['member_n'] else 0
        gains = {n: stats([change(t, n) for t in d['members']]) for n in (3, 5)}
        index = {n: change('000001.SH', n) for n in (3, 5)}
        relative = {str(n): gains[n]['median']-index[n] if gains[n]['n'] >= .95*d['member_n'] and number(gains[n]['median']) and number(index[n]) else None for n in (3, 5)}
        leaders = [r['ticker'] for r in theme.get('round', {}).get('leaders', []) if r.get('ticker') in qualified and r['ticker'] in d['members']]
        # Keep broken-board qualified cores; >=3 current streak is a different role.
        crowd = list(dict.fromkeys(leaders))
        relay = d['relay_refs'] if theme else []
        emotional = list(dict.fromkeys(crowd + relay))
        capacity = [t for t in d['core_refs'] if number(stock(t)['return_pct']) and stock(t)['return_pct'] > 0
                    and number(stock(t)['ma5']) and stock(t)['close'] >= stock(t)['ma5'] and number(change(t, 5)) and change(t, 5) > 0]
        a_ready = coverage >= .95 and all(number(relative[str(n)]) and relative[str(n)] > 0 for n in (3,5)) and (d['up_pct'] or 0) >= 50 and bool(capacity)
        strong_emotional = [t for t in emotional if number(change(t, 3)) and change(t, 3) > 0 and (stock(t)['return_pct'] or 0) >= 0]
        b_ready = coverage >= .95 and bool(strong_emotional)
        mode = 'B' if b_ready and (route.get('mode') != 'A' or not a_ready) else 'A' if a_ready else None
        if mode is None:
            continue
        refs = emotional if mode == 'B' else capacity
        d.update(mode=mode, watch_refs=refs[:3], crowding_refs=crowd, relative_3_pct=relative['3'], relative_5_pct=relative['5'],
            core_refs=capacity, coverage=coverage,
            role='情绪核心／接力参照' if mode == 'B' else '容量参照',
            reference_rule='短线核心沿用独立资格，断板不自动剔除；当前三板以上另作接力参照。容量参照须近期上涨、收盘在MA5上，不直接认定龙头。')
        if mode == 'B':
            d['reason'] = '当日可识别情绪核心，核心近3日保持涨幅；结合其换手和同类反馈观察延续'
            d['counter'] = '核心集中不代表整个板块扩散；' + ('方向成交额未增加' if not number(d['amount_ratio']) or d['amount_ratio'] <= 1 else '放量后仍需验证价格能否保持')
            d['upgrade'] = '量能环境继续适配B，核心分歧后恢复主动且同类未扩散负反馈'
            d['failure'] = '核心和同类一起走弱、分歧后无法修复，或只剩不可换手的一字表现'
            d['_rank'] = (theme.get('attention_rank') or 999, -max(change(t, 3) or 0 for t in strong_emotional), d['code'])
        else:
            d['reason'] = '完整成员近3/5日收益中位数均跑赢上证，今日至少半数上涨，存在趋势仍强的容量参照'
            d['counter'] = '成交额未扩张，暂未确认增量助攻' if not number(d['amount_ratio']) or d['amount_ratio'] <= 1 else '已有量能扩张，仍需核查核心承接与次日延续'
            d['upgrade'] = '大盘量能与指数支持A，板块分时和容量核心同步转强，MA5候选再确认'
            d['failure'] = '指数或板块助攻失效，容量核心放量滞涨，候选仅孤立走强'
            d['_rank'] = (-relative['5'], -relative['3'], d['code'])
        evaluated.append(d)
    main, backup, used = [], [], []
    def overlaps(d):
        for prior in used:
            if d['watch_refs'][0] == prior['watch_refs'][0] or set(d['watch_refs']) <= set(prior['watch_refs']):
                return True
            a, b = set(d['members']), set(prior['members'])
            if len(a & b)/max(1,min(len(a),len(b))) >= .75:
                return True
        return False
    for tier, dest, limit in (('main', main, 3), ('backup', backup, 2)):
        pool = [d for d in evaluated if (d['mode'] == route.get('mode') if tier == 'main' else d not in main)]
        if tier == 'main' and route.get('status') in ('wait','unknown'):
            pool = []
        pool.sort(key=lambda d: (d['mode'] == route.get('mode') if tier == 'backup' else False, d['_rank']))
        for d in pool:
            if overlaps(d):
                continue
            used_refs = {t for prior in used for t in prior['watch_refs']}
            d['watch_refs'] = [t for t in d['watch_refs'] if t not in used_refs]
            if not d['watch_refs']:
                continue
            d['tier'] = tier
            if tier == 'backup':
                d['reason'] = '条件备选：' + d['reason']
            dest.append(d); used.append(d)
            if len(dest) == limit:
                break
    result = main+backup
    for d in result:
        d.pop('_rank', None)
    return result, {'examined_n':len(directions), 'eligible_n':len(evaluated), 'main_n':len(main), 'backup_n':len(backup),
        'rule':'最多3主观察＋2条件备选，不补足名额；A比较近3/5日相对收益、广度与容量参照，B比较当时资格核心和接力参照。同核心或成员重叠≥75%合并。完整价格覆盖至少95%。排序仅表示观察顺序。'}, stock_cache


def choose_candidates(e, scanned, directions):
    chosen, used = [], set()
    for d in directions:
        if d['mode'] != 'A':
            continue
        eligible = []
        for raw in scanned:
            if raw['ticker'] not in d['members'] or raw['ticker'] in used:
                continue
            s = e.stock(raw['ticker'])
            if not number(s['ma5']) or not number(s['close']) or s['close'] < s['ma5'] or not number(s['return_pct']) or s['return_pct'] <= 0:
                continue
            eligible.append((s['return_pct']-d['return_median_pct'], raw))
        eligible.sort(key=lambda pair:(-pair[0], pair[1]['ticker']))
        for i, (_, raw) in enumerate(eligible[:2]):
            c = dict(raw)
            c.update(directions=[d['code']], strategy_id='ma5_v0_2', direction_name=d['name'],
                tier=d['tier'], priority='主选观察' if i == 0 else '同方向备选',
                reason='方向近期相对强、容量参照有效；个股满足MA5原规则、收盘在MA5上且当日上涨，按相对方向强度比较',
                normal='指数与板块延续，个股相对板块不明显落后；回落量收敛、再上攻恢复主动',
                confirmation='竞价仅初筛；开盘后同时核查大盘、板块核心、个股换手与承接',
                failure='上游条件失效，或个股相对弱势且分歧后不能恢复；净流入领先不能单独改变判断')
            chosen.append(c); used.add(c['ticker'])
    return chosen
