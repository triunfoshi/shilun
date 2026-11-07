"""Per-playbook, past-only feedback. Descriptive gates, never execution orders.

Daily observations freeze membership at the preceding close. Comparisons use
non-overlapping calendar windows, not the last N available observations.
"""
from collections import Counter
from math import ceil
from statistics import mean, median

from .engine import number, valid_bar, price_path_valid, ret, ratio, amount_band
from shilun.market.ma5_universe import iso

POLICY = {'recent_windows': [3, 5], 'default_recent': 5, 'baseline_days': 10,
          'minimum_daily_samples': 3, 'minimum_coverage': .95,
          'minimum_baseline_days': 8, 'loss_tail_pct': -5}
NAMES = {'trend': '首板套利', 'relay': '连板接力', 'crowding': '核心抱团'}
CHANGES = {'improving': '改善', 'deteriorating': '恶化', 'mixed': '分化', 'stable': '持平', 'unknown': '证据不足'}
SUPPORT = {'conditional': '有支持 · 参与待确认', 'unsupported': '当前反馈不合格',
           'conflicted': '存在反证 · 暂缓', 'unknown': '证据不足 · 未知'}
SPECS = {
 'trend': {
  'focus': '先手的次日开盘溢价是否兑现；后续趋势单独用于延长持有判断。',
  'cohort': '前一日确认首板，按首板日7日均额纳入当前容量档；次日成败均保留。',
  'primary': '次日开盘溢价', 'breadth': '正开盘溢价占比', 'negative': '开盘亏损≤−5%占比',
  'next': ['9:25／开盘：昨日冻结首板的溢价中位数、正溢价广度和低开尾部是否继续兑现。',
           '开盘后：是否普遍高开回落；指数与目标板块前排是否主动、扩散是否持续。',
           '仅在本股超预期且上游成立时讨论延长持有；不因T+5不如其他玩法否定首板套利。'],
  'gaps': ['目标板块盘中同步拉升和前排主动性待分钟证据确认。', '日线无法识别量化身份、撤单或保证封板时能够买入。'],
  'source': '蟹老板A145、A392—A393；2025-01-21 11:34虎扑回复。下述量化为整理补充。'},
 'relay': {
  'focus': '可换手梯队有没有真实晋级，掉队者能否退出；单看最高板不够。',
  'cohort': '前一日已≥2板且非一字的全部股票（日线换手代理），冻结后观察次日；失败者不删。另列1进2、2进3及更高梯队。',
  'primary': '昨日换手梯队次日收盘反馈', 'breadth': '换手梯队上涨占比', 'negative': '昨断板／炸板次日亏损≤−5%占比',
  'next': ['换手核心分歧后能否恢复主动，晋级是否由真实换手推动。',
           '昨日断板、炸板、掉队者是否继续低开、大跌或失去流动性。',
           '分别核对换手与一字梯队；不能将买不到的一字收益算作可参与机会。'],
  'gaps': ['非一字只代表日线有价格区间，不证明实际可成交。', '封单持续性、撤单及逐笔承接未接入；不套固定封单或通用换手健康线。'],
  'source': '蟹老板A023—A025、A145、A324、A423；换手生态优先。下述量化为整理补充。'},
 'crowding': {
  'focus': '昨日核心能否维持群体正反馈，分歧是否修复，是否只剩一只独撑。',
  'cohort': '前一收盘时点冻结的独立资格核心；当日失效、掉出名单者仍计入当天反馈。核心不等于最高连板。',
  'primary': '昨日核心次日收盘反馈', 'breadth': '昨日核心上涨占比', 'negative': '核心单日亏损≤−5%占比',
  'next': ['对照盘前正常预期，核心是否该弱不弱；分歧后的修复是否主动且有真实换手。',
           '其他核心和跟随者是否同步失去承接；不能只看单一最强股。',
           '核对容量中军、指数与板块分时；日线收回昨收不等于已验证盘中主动修复。'],
  'gaps': ['核心资格是历史行情重建代理，并非当时人工冻结的真实交易名单。', '高低价只有日线结果，不能证明分歧与主动修复的先后顺序。'],
  'source': '蟹老板A080—A082、A088—A089、A388；旧核心弱不自动推出新核心强。下述量化为整理补充。'}
}


def metric(values, rate=False):
    good = [float(v) for v in values if v is not None and number(v)]
    return {'value': (mean(good)*100 if rate else median(good)) if good else None,
            'n': len(good), 'total': len(values)}


def adequate(m):
    return m['n'] >= POLICY['minimum_daily_samples'] and m['n'] >= m['total']*POLICY['minimum_coverage']


def rate(rows, field):
    return metric([None if r.get(field) is None else int(r[field]) for r in rows], True)


def collect(rows, field):
    return metric([r.get(field) for r in rows])


def streak_history(e):
    history = {d: {} for d in e.days}
    for t in e.info:
        streak = None
        for d in e.days:
            r, limit = e.row(t, d), e.limit.get((t, d))
            if not valid_bar(r) or not number(limit) or limit <= 0:
                streak = None
            elif abs(r['close']-limit) < .005:
                streak = streak+1 if streak is not None else None
            else:
                streak = 0
            history[d][t] = streak
    return history


def observe(e, t, i):
    d, prev = e.days[i], e.days[i-1]
    r, p = e.row(t, d), e.row(t, prev)
    out = {'ticker': t, 'name': e.info.get(t, {}).get('name', t)}
    prior_days = e.days[max(0, i-10):i]
    turns = [e.db.get((t, x), {}).get('turnover_rate') for x in prior_days]
    listing_date=e.info.get(t,{}).get('list_date')
    listed=iso(listing_date) if listing_date else None
    out['missing_turnover_dates']=[day for day,v in zip(prior_days,turns) if not number(v) or v<=0]
    out['turnover_gap_details']=[{'date':day,'reason':
        '尚未上市' if listed and day<listed else '已确认停牌' if day in e.suspensions.get(t,set()) else
        '当日无成交' if e.row(t,day).get('volume')==0 else '每日指标缺失或无效'}
        for day in out['missing_turnover_dates']]
    if not price_path_valid([p, r]):
        out['price_gap_reason']='前日或当日日线缺失／无成交' if not valid_bar(p) or not valid_bar(r) else '当日前收参考价与前日收盘不连续，暂不能直接计算收益'
        return out
    change, opening = ret(r['close'], p['close']), ret(r['open'], p['close'])
    # Raw, unadjusted cached prices accompany each valid feedback observation.
    # Do not expose a calculable pair when continuity/corporate-action checks fail.
    out['prices'] = {'formation_date': prev, 'feedback_date': d,
        'previous_close': p['close'], 'open': r['open'], 'close': r['close']}
    out.update(close_return=change, opening=opening, red=int(r['close']>r['open']),
        close_positive=int(change>0), open_positive=int(opening>0),
        loss=int(change<=POLICY['loss_tail_pct']), open_loss=int(opening<=POLICY['loss_tail_pct']),
        low_open=int(opening<0), one_price=int(r['high']==r['low']),
        giveback=int(r['close']<r['open']) if opening>0 else None,
        recovered=int(r['close']>=p['close']) if r['low']<p['close'] else None,
        turnover=e.db.get((t, d), {}).get('turnover_rate'))
    limit = e.limit.get((t, d))
    out['promoted'] = int(abs(r['close']-limit)<.005) if number(limit) and limit>0 else None
    bars = [e.row(t, x) for x in prior_days]
    # Complete self-history only. High/low turnover alone is not a health verdict.
    if len(bars)==10 and price_path_valid(bars+[r]):
        volumes = [b['volume'] for b in bars]
        dry = r['volume'] < min(volumes) and change < 0
        hot = r['volume'] > max(volumes) and change <= 0
        out.update(volume_ratio=ratio(r['volume'], mean(volumes)), dry_weak=int(dry), hot_weak=int(hot), adverse=int(dry or hot))
    if len(turns)==10 and all(number(x) and x>0 for x in turns) and number(out['turnover']) and out['turnover']>0:
        out.update(turnover_ratio=ratio(out['turnover'], mean(turns)), turn_outside=int(out['turnover']<min(turns) or out['turnover']>max(turns)))
    path = [e.row(t, x) for x in e.days[max(0, i-4):i+1]]
    out['drawdown'] = ret(r['close'], max(b['high'] for b in path)) if len(path)==5 and price_path_valid(path) else None
    return out


def daily_evidence(e, cores, band):
    streaks = streak_history(e)
    allowed = {'lt50'} if band=='lt50' else {'lt50','50to100'} if band=='lt100' else {'lt50','50to100','ge100'}
    failures = {d: set() for d in e.days}
    for i, d in enumerate(e.days[1:], 1):
        for t in e.info:
            r, u = e.row(t,d), e.limit.get((t,d))
            if not valid_bar(r) or not number(u) or u<=0:
                continue
            if abs(r['close']-u)>=.005 and ((streaks[e.days[i-1]].get(t) or 0)>=2 or r['high']>=u-.005):
                failures[d].add(t)
    result = {k: [] for k in NAMES}
    for i in range(7, len(e.days)):
        d, prev = e.days[i], e.days[i-1]
        first = [t for t,s in streaks[prev].items() if s==1 and amount_band(e.avg_amount(t,i-1)) in allowed]
        all_relay = [t for t,s in streaks[prev].items() if s is not None and s>=2]
        exchange = [t for t in all_relay if e.row(t,prev)['high']>e.row(t,prev)['low']]
        one_price = [t for t in all_relay if t not in exchange]
        members = {'trend': first, 'relay': exchange, 'crowding': sorted({r['ticker'] for r in cores['snapshots'].get(prev,[])})}
        cache = {}
        def observation(t):
            if t not in cache: cache[t] = observe(e,t,i)
            return cache[t]
        failed = [observation(t) for t in sorted(failures[prev])]
        for k, ticks in members.items():
            rows = [dict(observation(t)) for t in sorted(ticks)]
            if k == 'trend':
                for row in rows:
                    t = row['ticker']
                    row['first_board_evidence'] = {
                        'date': prev, 'prior_date': e.days[i-2],
                        'prior_streak': streaks[e.days[i-2]].get(t),
                        'prior_close': e.row(t, e.days[i-2]).get('close'),
                        'prior_up_limit': e.limit.get((t, e.days[i-2])),
                        'close': e.row(t, prev).get('close'),
                        'up_limit': e.limit.get((t, prev)),
                        'avg7_cny': e.avg_amount(t, i-1)}
                    basic, bar = e.db.get((t, prev), {}), e.row(t, prev)
                    circ, shares, close = basic.get('circ_mv'), basic.get('float_share'), bar.get('close')
                    direct = number(circ) and circ > 0
                    derived = number(shares) and shares > 0 and number(close) and close > 0
                    row['first_board_evidence'].update(
                        amount_cny=bar['amount']*1000 if number(bar.get('amount')) and bar['amount']>0 else None,
                        float_cap_cny=circ*10000 if direct else shares*10000*close if derived else None,
                        float_cap_source='daily_basic.circ_mv' if direct else '同日float_share×收盘价' if derived else '缺同日流通股本／市值')
            primary = 'opening' if k=='trend' else 'close_return'
            breadth = 'open_positive' if k=='trend' else 'close_positive'
            negative_rows = failed if k=='relay' else rows
            metrics = {'primary': collect(rows,primary), 'red':rate(rows,'red'), 'breadth':rate(rows,breadth),
                'negative': rate(negative_rows,'open_loss' if k=='trend' else 'loss'),
                'participation':rate(rows,'adverse'), 'turnover':collect(rows,'turnover'),
                'turnover_ratio':collect(rows,'turnover_ratio'), 'volume_ratio':collect(rows,'volume_ratio'),
                'one_price':rate(rows,'one_price'), 'dry_weak':rate(rows,'dry_weak'), 'hot_weak':rate(rows,'hot_weak'),
                'turn_outside':rate(rows,'turn_outside'), 'low_open':rate(rows,'low_open'),
                'giveback':rate(rows,'giveback'), 'drawdown':collect(rows,'drawdown'), 'recovered':rate(rows,'recovered')}
            day = {'date':d, 'formation_date':prev, 'cohort_n':len(rows), 'metrics':metrics, 'members':rows}
            if k=='relay':
                locked = [observation(t) for t in one_price]
                metrics.update(promotion=rate(rows,'promoted'), failed_return=collect(failed,'close_return'),
                    locked_return=collect(locked,'close_return'), locked_promotion=rate(locked,'promoted'))
                day['failed_members'] = failed
                day['locked_members'] = locked
                day['ladder'] = []
                for height in (1,2,3,4):
                    ts = [t for t,s in streaks[prev].items() if s is not None and (s==height if height<4 else s>=4)]
                    rs = [observation(t) for t in ts]
                    day['ladder'].append({'stage':f'{height}进{height+1}' if height<4 else '4板及以上',
                        'all':rate(rs,'promoted'), 'exchange':rate([r for r in rs if e.row(r['ticker'],prev)['high']>e.row(r['ticker'],prev)['low']], 'promoted')})
                day['heights'] = {str(h):n for h,n in sorted(Counter(s for s in streaks[d].values() if s is not None and s>0).items())}
            if k=='crowding':
                valid = sorted(r['close_return'] for r in rows if number(r.get('close_return')))
                day['positive_n'] = sum(v>0 for v in valid)
                day['ex_best_median'] = median(valid[:-1]) if len(valid)>=3 else None
                day['lone_core'] = len(valid)>=3 and sum(v>0 for v in valid)==1
            result[k].append(day)
    return result


def period(rows, key, minimum_days):
    good = [r for r in rows if adequate(r['metrics'][key])]
    vals = [r['metrics'][key]['value'] for r in good]
    valid = len(good)>=minimum_days
    return {'value': (median(vals) if key=='primary' else mean(vals)) if valid else None,
        'days_n':len(good), 'days_total':len(rows), 'required_days':minimum_days,
        'samples_n':sum(r['metrics'][key]['n'] for r in rows),
        'cohort_n':sum(r['metrics'][key]['total'] for r in rows),
        'from':rows[0]['date'] if rows else None, 'to':rows[-1]['date'] if rows else None}


def compare(rows, recent_n, key, label, dimension, higher=True):
    recent_rows, baseline_rows = rows[-recent_n:], rows[-recent_n-10:-recent_n]
    recent, baseline = period(recent_rows,key,recent_n), period(baseline_rows,key,8)
    if len(baseline_rows)!=10:
        baseline['value']=None
    a,b=recent['value'],baseline['value']
    delta=a-b if number(a) and number(b) else None
    change = 'unknown' if delta is None else 'stable' if abs(delta)<1e-9 else 'improving' if (delta>0)==higher else 'deteriorating'
    return {'key':key, 'label':label, 'dimension':dimension, 'higher_is_better':higher,
            'recent':recent, 'baseline':baseline, 'delta_pp':delta, 'change':change,
            'change_label':({'improving':'风险收敛','deteriorating':'风险扩散'}.get(change,CHANGES[change]) if not higher else CHANGES[change])}


def evaluate(k, rows, recent_n):
    spec=SPECS[k]
    dims=[compare(rows,recent_n,'primary',spec['primary'],'收益'),
          compare(rows,recent_n,'red','次日红K频率（首板仅辅助）' if k=='trend' else '红K频率','正反馈'),
          compare(rows,recent_n,'breadth',spec['breadth'],'收益覆盖'),
          compare(rows,recent_n,'negative',spec['negative'],'负反馈',False),
          compare(rows,recent_n,'participation','缩量／放量走弱样本占比（自身前10日）','参与风险',False)]
    bykey={d['key']:d for d in dims}
    directions={d['change'] for d in dims}
    change='mixed' if {'improving','deteriorating'}<=directions else 'unknown' if 'unknown' in directions else 'improving' if 'improving' in directions else 'deteriorating' if 'deteriorating' in directions else 'stable'
    last=rows[-1] if rows else {'metrics':{}}
    current={key:last['metrics'].get(key,{'value':None,'n':0,'total':0}) for key in ('primary','breadth','participation','one_price','turnover_ratio')}
    primary, breadth = bykey['primary']['recent']['value'],bykey['breadth']['recent']['value']
    ready=number(primary) and number(breadth) and adequate(current['primary']) and adequate(current['breadth'])
    positive=primary>0 and breadth>50 and current['primary']['value']>0 and current['breadth']['value']>50 if ready else None
    reasons=[]
    if positive is None:
        status='unknown';reasons.append('近期或最新日有效样本不足，不补零、不顺延到更早日期。')
    elif not positive:
        status='unsupported';reasons.append('近期或最新日的本玩法收益未为正，或正反馈未覆盖多数样本；改善不等于合格。')
    else:
        status='conditional';reasons.append('近期与最新日本玩法反馈为正且覆盖多数样本；仍须确认参与条件和上游支持。')
    risks=[]
    if bykey['negative']['change']=='deteriorating':risks.append('本玩法亏损尾部较基准期扩散')
    if bykey['participation']['change']=='deteriorating':risks.append('相对自身历史的缩量／放量走弱增多')
    if adequate(current['one_price']) and current['one_price']['value']==100:risks.append('最新样本全部为一字，缺少真实换手确认')
    if k=='crowding' and last.get('lone_core'):risks.append('最新日仅一只核心上涨，其余核心未形成群体支持')
    if k=='relay':
        promo=compare(rows,recent_n,'promotion','换手晋级率','晋级')
        if promo['change']=='deteriorating':risks.append('可换手梯队晋级率较自身基准下降')
        failed=last['metrics'].get('failed_return',{})
        if adequate(failed) and failed['value']<0:risks.append('昨日断板／炸板样本最新反馈中位数仍为负')
    if positive is True and risks:status='conflicted'
    if positive is True and not all(adequate(current[k]) for k in ('participation','turnover_ratio')):
        if not risks:status='unknown'
        reasons.append('最新日自身换手／量能基准缺失，参与支持度保持未知。')
        missing=sorted({day for r in last.get('members',[]) for day in r.get('missing_turnover_dates',[])})
        if missing:reasons.append('样本自身换手历史缺口：'+'、'.join(missing)+'；成交量可用部分独立计算。')
    # A positive median never overrides worsening participation / failure feedback.
    selection_support = False if status in ('unsupported','conflicted') else True if status=='conditional' else None
    recent_rows=rows[-recent_n:]
    red_streak=0
    for r in reversed(recent_rows):
        m=r['metrics']['red']
        if not adequate(m) or m['value']<=50:break
        red_streak+=1
    checks=[]
    for key,title in [('primary','最新日主收益'),('breadth','最新日收益覆盖'),
                      ('participation','最新日量价风险基准'),('turnover_ratio','最新日换手基准')]:
        m=current[key]
        checks.append({'key':key,'title':title,'valid_n':m['n'],'total_n':m['total'],
            'required_n':max(POLICY['minimum_daily_samples'],ceil(m['total']*POLICY['minimum_coverage'])),
            'covered':adequate(m)})
    diagnostic={'price_support':positive,'coverage_checks':checks,
        'sample_gaps':[{'ticker':r['ticker'],'name':r['name'],
            'turnover_gaps':r.get('turnover_gap_details',[]),'price_reason':r.get('price_gap_reason')}
            for r in last.get('members',[]) if r.get('turnover_gap_details') or r.get('price_gap_reason')],
        'missing_turnover_dates':sorted({day for r in last.get('members',[]) for day in r.get('missing_turnover_dates',[])}),
        'comparison_gaps':[{'dimension':x['dimension'],'period':period_name, 'days_n':x[p]['days_n'],
            'days_total':x[p]['days_total'],'required_days':x[p]['required_days']}
            for x in dims for p,period_name in [('baseline','此前10日'),('recent',f'近{recent_n}日')] if not number(x[p]['value'])]}
    return {'key':k,'name':NAMES[k], 'recent_days':recent_n,'focus':spec['focus'],'cohort':spec['cohort'],
        'dimensions':dims,'change':change,'change_label':CHANGES[change],
        'status':status,'status_label':SUPPORT[status], 'price_support':positive,'selection_support':selection_support,
        'reasons':reasons,'risks':risks,'diagnostic':diagnostic,'next_checks':spec['next'],'gaps':spec['gaps'], 'source':spec['source'],
        'red_majority_streak':red_streak,'latest':last,
        'daily':rows[-recent_n-10:]}


def build_assessments(e, cores, band):
    series=daily_evidence(e,cores,band)
    windows={str(n):{k:evaluate(k,rs,n) for k,rs in series.items()} for n in POLICY['recent_windows']}
    return {'policy':POLICY, 'windows':windows,
        'method': '近3／5个完整交易日对比紧邻且不重叠的此前10日。每日同股去重，收益先取当日样本中位数，再取日中位数；比例按日等权。每天至少3个有效样本、覆盖≥95%；近期每一天须达标，基准至少8/10天达标。未知不补零、不跳过缺失日寻找替代日期。此为研究门槛，尚未完成窗口与阈值回测。',
        'participation_rule': '可参与性回答有没有真实换手、量能与退出空间；这里的参与风险只是其中一个日线代理，不代表可以成交。逐股比较当日前10个完整交易日：量小于前10日最小量且收盘下跌→缩量走弱；量大于前10日最大量且收盘不涨→放量走弱。两者都是风险，占比下降才叫风险收敛；不是放量走弱本身变好。换手与量比同时展示，单独高换手或放量不判坏；承接、撤单及盘中成交条件仍待核验。',
        'negative_rule': '≤−5%只作明显亏损尾部的研究切片，不是止损线或通用健康阈值。首板用开盘亏损，接力用昨断板／触板未封样本的次日收盘亏损，核心用昨日冻结核心的日亏损。跌停价未完整缓存时不将大跌冒充跌停。',
        'qualification_rule': '变化与支持度分开。近期及最新日主反馈>0且正反馈占比>50%只支持条件观察；亏损尾部、量价走弱扩散或接力失败反证可下调为暂缓。未设置自动合格或开仓许可。红K只表示收盘>开盘；首板红K和T+N延续不否定已兑现的开盘溢价。',
        'cohort_rule': '跨日群体会变化，比较的是同玩法生态，不宣称同一批个股人人改善。样本数量为股票日。收益覆盖是正收益样本比例；板块协同是首板日板块成员的共同表现，另作辅助分组，二者不能混称广度。'}


def conclusion_from_assessments(assessments, market):
    from .engine import route_mode
    modes=assessments['windows'][str(POLICY['default_recent'])]
    signals={k:v['selection_support'] for k,v in modes.items()}
    route=route_mode(market,signals)
    if not any(v is True for v in signals.values()) or route.get('mode')=='A' and signals['trend'] is not True or route.get('mode')=='B' and not any(signals[k] is True for k in ('relay','crowding')):
        if signals['relay'] is True or signals['crowding'] is True:
            route={'mode':'B','status':'conditional','label':'B · 情绪抱团／接力，逐项确认参与条件'}
        else:
            route={'mode':None,'status':'waiting','label':'等待 · 尚无充分支持的参与路径'}
    if route.get('status')=='confirmed':route['status']='conditional'
    eligible=['trend'] if route.get('mode')=='A' else ['relay','crowding'] if route.get('mode')=='B' else []
    positive=[k for k in eligible if signals[k] is True]
    headline=('、'.join(NAMES[k] for k in positive)+'有条件观察依据；次日核验环境与承接' if positive else '三种玩法分别核验，当前先等待支持与参与条件')
    judged={k:{'price_support':v['selection_support'], 'raw_price_support':v['price_support'],
        'label':v['status_label'], 'health':'；'.join(v['risks'] or v['gaps'][:1]),
        'change_label':v['change_label'], 'status':v['status'], 'change':v['change'],
        'recent_return_median':v['dimensions'][0]['recent']['value'],
        'today_return_median':v['latest']['metrics']['primary']['value'],
        'next_checks':v['next_checks'], 'reasons':v['reasons'], 'risks':v['risks']} for k,v in modes.items()}
    conclusion={'headline':headline,'groups':judged,'positive_groups':positive,'preferred_group':None,'ranking_applied':False,
        'evaluation_window':5, 'baseline_days':10,
        'existing_position':'首板默认吃次日开盘溢价。后续趋势收益仅辅助延长持有判断；超预期且上游成立可留，符合预期仅开放已约定例外，不及预期不延长。',
        'new_entry':'先按大盘、目标板块、各玩法参与条件核验。A路径再匹配MA5；连板／抱团当前仅观察，等待也不强配个股。',
        'recent_rule':assessments['method'],
        'comparability':'三种玩法各自与自身历史比较，不作累计收益排名；改善不等于合格，收益不能抵消参与反证。'}
    return route,conclusion
