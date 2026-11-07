"""Dated cached minute observations against an immutable prior-close plan."""
from datetime import datetime
from statistics import median
from zoneinfo import ZoneInfo

from .engine import INDICES, number, ret, ratio, valid_bar

TZ = ZoneInfo('Asia/Shanghai')


def observed_bars(document, day, clock):
    rows, seen = [], set()
    for raw in document.get('bars', []):
        try:
            stamp = datetime.fromisoformat(str(raw.get('datetime')))
            stamp = stamp.replace(tzinfo=TZ) if stamp.tzinfo is None else stamp.astimezone(TZ)
        except (ValueError, TypeError):
            continue
        hhmm = stamp.strftime('%H:%M')
        if stamp.date().isoformat() != day or hhmm > clock or not ('09:35' <= hhmm <= '11:30' or '13:05' <= hhmm <= '15:00') or stamp.minute % 5:
            continue
        if hhmm in seen:
            return [], '分钟时点重复，停止判断'
        seen.add(hhmm)
        if not valid_bar(raw):
            return [], '分钟价格或成交量无效'
        rows.append({**raw, 'time': hhmm})
    rows.sort(key=lambda r: r['time'])
    return rows, None


def minute_observation(doc, day, clock, previous_close, ma5=None):
    rows, error = observed_bars(doc, day, clock)
    if not rows:
        return {'status': 'missing', 'reason': error or '该时点没有完整5分钟线', 'last_time': None}
    # 09:30 auction bar is not always stored; VWAP below explicitly starts 09:35.
    # Only this adapter has a verified shares/yuan contract. Other stored sources
    # have lost their unit metadata and are deliberately not guessed.
    expected = [f'{m//60:02d}:{m%60:02d}' for start, end in ((575, 690), (785, 900))
                for m in range(start, end + 1, 5) if f'{m//60:02d}:{m%60:02d}' <= rows[-1]['time']]
    missing_times = sorted(set(expected) - {r['time'] for r in rows})
    unit_ready = not missing_times and doc.get('source') in ('tushare_stk_mins', 'tushare_idx_mins')
    total_amount, total_volume, vwap_rows = 0, 0, []
    for r in rows:
        if not number(r.get('amount')) or r['amount'] < 0:
            unit_ready = False
        total_amount += r.get('amount') or 0
        total_volume += r['volume']
        vwap_rows.append(ratio(total_amount, total_volume))
    # Amount/volume should imply a tradable stock price. Index aggregate amount
    # divided by aggregate shares is NOT the index level; never compare it as VWAP.
    if doc.get('ticker') in INDICES:
        unit_ready = False
    if unit_ready and not all(rows[0]['low'] * .5 <= v <= max(r['high'] for r in rows) * 1.5 for v in vwap_rows if v is not None):
        unit_ready = False
    last = rows[-1]
    recent = rows[-3:]
    times = [int(r['time'][:2]) * 60 + int(r['time'][3:]) for r in recent]
    continuous = len(recent) == 3 and all(b - a == 5 for a, b in zip(times, times[1:]))
    stale_minutes = int(clock[:2]) * 60 + int(clock[3:]) - (int(last['time'][:2]) * 60 + int(last['time'][3:]))
    if '11:30' <= last['time'] <= '13:00' and clock >= '13:00':
        stale_minutes -= 90
    if '11:30' < clock < '13:05' and last['time'] == '11:30':
        stale_minutes = 0
    if clock > '15:00' and last['time'] == '15:00':
        stale_minutes = 0
    repair_bars = None
    if number(ma5):
        last_break = None
        for i, r in enumerate(rows):
            if r['close'] < ma5 and (i == 0 or rows[i-1]['close'] >= ma5):
                last_break = i
                repair_bars = None
            elif last_break is not None and r['close'] >= ma5 and repair_bars is None:
                repair_bars = i - last_break
    runs = []
    for i, r in enumerate(rows):
        delta = r['close'] - (rows[i-1]['close'] if i else r['open'])
        if not delta:
            continue
        sign = 1 if delta > 0 else -1
        if runs and runs[-1]['sign'] == sign:
            runs[-1]['volumes'].append(r['volume'])
        else:
            runs.append({'sign': sign, 'volumes': [r['volume']]})
    down_up, re_up = None, None
    if len(runs) >= 3 and [r['sign'] for r in runs[-3:]] == [1,-1,1]:
        a,b,c = [sum(r['volumes']) / len(r['volumes']) for r in runs[-3:]]
        down_up, re_up = ratio(b,a), ratio(c,b)
    if missing_times:
        repair_bars, down_up, re_up = None, None, None
    above = sum(r['close'] >= v for r, v in zip(recent, vwap_rows[-3:])) if unit_ready and continuous else None
    return {'status': 'stale' if stale_minutes > 5 else 'observed', 'last_time': last['time'],
        'close': last['close'], 'return_pct': ret(last['close'], previous_close),
        'above_ma5': last['close'] >= ma5 if number(ma5) else None,
        'missing_times': missing_times, 'coverage_complete': not missing_times,
        'ma5_repair_bars': repair_bars,
        'pullback_to_advance_volume_ratio': down_up, 'readvance_to_pullback_volume_ratio': re_up,
        'repair_note': 'MA5为普通观察位；按完整5分钟线计收回时间，不代替结构止损条件',
        'vwap': vwap_rows[-1] if unit_ready else None, 'vwap_above_count': above,
        'support_observation': above >= 2 and last['close'] >= vwap_rows[-1] if above is not None else None,
        'vwap_note': '从09:35完整线累计，未计竞价；≥2/3仅为承接试算证据，非参与确认' if unit_ready else '分钟有缺口、来源单位未核验或指数VWAP不适用，未计算',
        'bars_n': len(rows), 'source': doc.get('source'), 'fetched_at': doc.get('fetched_at'),
        'series': [{'time': r['time'], 'return_pct': ret(r['close'], previous_close), 'close': r['close']} for r in rows]}


def build_tracking(plan, documents, auctions, day, clock):
    if day != plan.get('next_date'):
        raise ValueError('跟踪日期必须是该冻结预案的下一交易日')
    lookup = {}
    duplicate = set()
    for doc in documents:
        if str(doc.get('trade_date', '')).replace('-', '') != day.replace('-', '') or str(doc.get('interval')) != '5':
            continue
        ticker = doc['ticker']
        if ticker in lookup:
            duplicate.add(ticker)
        lookup[ticker] = doc
    observations = {}
    for ticker, previous in plan.get('baseline_closes', {}).items():
        if ticker in duplicate:
            observations[ticker] = {'status': 'missing', 'reason': '同股同日分钟缓存重复', 'last_time': None}
        else:
            observations[ticker] = minute_observation(lookup.get(ticker, {}), day, clock, previous, plan.get('stocks', {}).get(ticker, {}).get('ma5'))
    directions = []
    for d in plan['directions']:
        # Require same timestamp; a strong stale stock cannot improve the group.
        current = [observations.get(t, {}) for t in d['members']]
        at = max((r.get('last_time') for r in current if r.get('last_time')), default=None)
        valid = [r for r in current if r.get('status') == 'observed' and r.get('last_time') == at and number(r.get('return_pct'))]
        directions.append({'code': d['code'], 'name': d['name'], 'time': at, 'valid_n': len(valid), 'member_n': len(current),
            'return_median_pct': median([r['return_pct'] for r in valid]) if valid else None,
            'complete': bool(current) and len(valid) == len(current),
            'mode': d.get('mode'), 'tier': d.get('tier'), 'failure': d.get('failure'),
            'core_observations': [{'ticker': t, 'name':plan.get('stocks',{}).get(t,{}).get('name',t), **observations.get(t, {'status': 'missing'})} for t in d.get('watch_refs', d['core_refs'])]})
    candidates = []
    for c in plan['candidates']:
        obs = observations.get(c['ticker'], {'status': 'missing'})
        refs = [d for d in directions if d['code'] in c['directions'] and d['complete'] and d['time'] == obs.get('last_time')]
        relative = obs.get('return_pct') - refs[0]['return_median_pct'] if refs and number(obs.get('return_pct')) else None
        candidates.append({'ticker': c['ticker'], 'name': c['name'], **obs, 'relative_direction_pct': relative,
            'relative_direction_name': refs[0]['name'] if refs else None,
            'expectation': 'pending', 'decision': '大盘量能及模式未确认，保留观察；不自动替换主选'})
    watchlist = list(candidates)
    for d in plan['directions']:
        for ticker in d.get('watch_refs', []):
            if any(r['ticker'] == ticker for r in watchlist):
                continue
            watchlist.append({'ticker':ticker, 'name':plan['stocks'].get(ticker,{}).get('name',ticker),
                'role':d.get('role','核心参照'), 'direction_name':d['name'], 'tier':d.get('tier'),
                **observations.get(ticker, {'status':'missing'}), 'decision':'观察核心用于验证方向，不等同执行候选'})
    layers = []
    indices = [observations.get(t,{}) for t in INDICES]
    ready_indices = [r for r in indices if r.get('status') == 'observed' and r.get('coverage_complete') and number(r.get('return_pct'))]
    same_time = len({r.get('last_time') for r in ready_indices}) == 1
    if len(ready_indices) == 4 and same_time:
        down = sum(r['return_pct'] < 0 for r in ready_indices)
        layers.append({'layer':'大盘', 'state':'changed' if down >= 3 else 'observed',
            'evidence':f'{down}/4个指数低于昨收；冻结预期：{plan.get("market",{}).get("outlook",{}).get("title","待判断")}',
            'action':'价格层出现反证，重新核查大盘路径；量能尚不足以确认模式切换' if down >= 3 else '继续观察指数与量能；价格反馈不能代替全天容量条件'})
    else:
        layers.append({'layer':'大盘','state':'missing','evidence':f'同时间完整指数覆盖不足（有效{len(ready_indices)}/4）',
            'action':'保留原预案为条件观察，不把缺数据当成转弱；全市场同时间量能待补'})
    for d in directions:
        refs = [r for r in d['core_observations'] if r.get('status')=='observed' and r.get('coverage_complete') and number(r.get('return_pct')) and r.get('last_time')==d['time']]
        weak = [r for r in refs if r['return_pct'] < 0]
        changed = d['complete'] and d['return_median_pct'] < 0 and len(refs) == len(d['core_observations']) and bool(refs) and len(weak) == len(refs)
        layers.append({'layer':d['name'], 'state':'changed' if changed else 'observed' if d['complete'] else 'missing',
            'evidence':f'同时间成员{d["valid_n"]}/{d["member_n"]}；可用核心{len(refs)}只，其中{len(weak)}只低于昨收',
            'action':'方向与核心共同出现负反馈，暂停提升该方向候选' if changed else '继续核对核心分歧能否恢复；成员未齐时不认定板块整体助攻'})
    for r in watchlist:
        ready = r.get('status')=='observed' and r.get('coverage_complete')
        weak = ready and r.get('above_ma5') is False and number(r.get('return_pct')) and r['return_pct'] < 0
        layers.append({'layer':r['name'], 'state':'changed' if weak else 'observed' if ready else 'missing',
            'evidence':'低于昨收及冻结MA5，个股价格出现反证' if weak else '已取得分钟观察，按同方向比较承接' if ready else '分钟缺失、过期或有缺口',
            'action':'降低观察优先级，等修复；MA5不是自动止损指令' if weak else '尚未冻结数值竞价预期，不自动认定超预期或提升备选'})
    auction = next((a for a in auctions if a.get('date') == day and str(a.get('auction_time', '09:25'))[:5] <= clock), {})
    return {'baseline_date': plan['analysis_date'], 'baseline_built_at': plan['built_at'], 'date': day, 'as_of': clock,
        'mode': 'cache_observation', 'route': plan['route'], 'status': 'partial',
        'market': [{'ticker': t, 'name': n, **observations.get(t, {'status': 'missing'})} for t, n in INDICES.items()],
        'market_turnover': {'status': 'missing', 'previous_confirmed_cny':plan.get('market',{}).get('turnover',{}).get('market_amount_cny'),
            'current_same_time_cny':None, 'projected_full_day_cny':None,
            'reason': '分钟缓存不是全市场覆盖；同时间量能及预计全天量能均待补，不直接套用全天门槛'},
        'auction': {'status': 'amount_only' if auction else 'missing', 'date': auction.get('date'),
            'source': auction.get('source'), 'unit': auction.get('unit'),
            'amounts': {c['ticker']: auction.get('amounts', {}).get(c['ticker']) for c in watchlist},
            'note': '现有竞价源仅含成交额，缺竞价价格/预期区间，不认定竞价超预期'},
        'directions': directions, 'candidates': candidates, 'watchlist':watchlist, 'layer_changes':layers,
        'baseline_version':plan.get('version'),
        'new_direction_detection': {'status': 'pending', 'reason': '尚缺全市场同时间分钟覆盖和历史同时间量能；不能声称已扫出盘中新爆发'},
        'notes': ['读取已有分钟缓存，非秒级实时行情；显示实际时点和过期状态',
            '分时回放只按行情发生时点截断；缓存入库时间未经当时可得性验证',
            '原始价格未接除权因子，除权日跨日相对强度需核验',
            '竞价、量能、方向和预期条件不完整时，C07保持待确认']}
