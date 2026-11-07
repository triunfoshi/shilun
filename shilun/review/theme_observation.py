"""Dated member observation, independent of confirmed theme-event attribution."""
from shilun.review.theme_selection import SelectionRules, finite

VERSION = 'theme-member-observation-v2'
MEMBER_OBSERVATION_SESSIONS = 10


def daily_return(replay, ticker, day):
    if day in replay.market.get('suspensions', {}).get(ticker, ()):
        return None
    bar = replay.market.get('bars', {}).get(ticker, {}).get(day) or {}
    close, previous = finite(bar.get('close')), finite(bar.get('pre_close'))
    if close is None or previous is None or close <= 0 or previous <= 0:
        return None
    return close / previous - 1


def member_observation(replay, day, candidate, rules=None):
    """Recent sealed members and active qualified cores stay visible without a reason hit.

    This is a review-day observation cohort, not a trading/PIT cohort. Co-movement
    supplies context only; it never rewrites editorial reasons or diffusion counts.
    """
    rules = rules or SelectionRules()
    code = candidate['code']
    members = replay.members.get((day, code))
    result = {'version': VERSION, 'date': day, 'membership_date': day,
              'lookback_sessions': MEMBER_OBSERVATION_SESSIONS,
              'status': 'ready', 'members': [], 'missing_dates': [], 'member_limit_count': None,
              'scope_note': f'当日已核验成分中，近{MEMBER_OBSERVATION_SESSIONS}个交易日（含复盘日）涨停过的成员保留观察；按股票去重。核心资格单独认证，扩散统计仍按近{rules.diffusion_sessions}个交易日计算。'}
    if replay.event_themes:
        result.update(membership_scope='dated_attributed_limit_up_observation',
                      scope_note=f'截至复盘日曾按同日原因归属于本事件的样本中，保留近{MEMBER_OBSERVATION_SESSIONS}日涨停股；不是全板块成分。昨日名单冻结，今日掉队成员继续跟踪。')
    if members is None:
        return {**result, 'status': 'membership_missing', 'missing_dates': [day]}
    members = {t for t in members if replay.allowed(t, day)}
    recent = replay.window(day, MEMBER_OBSERVATION_SESSIONS)
    diffusion_days = replay.window(day, rules.diffusion_sessions)
    active_days = replay.window(day, rules.core_active_sessions)
    qualified = replay.qualified(day)
    active_cores = {t: r for t, r in qualified.items()
                    if t in members and r.get('latest_limit_date') in active_days}
    evidence_by_ticker = {}
    missing = set()
    for event_day in sorted(set(recent) | set(active_days)):
        evidence = replay.events(code, event_day)
        if not evidence['complete']:
            if event_day in recent or active_cores:
                missing.add(event_day)
            continue
        for bucket in ('events', 'unknown', 'membership_only'):
            for event in evidence[bucket]:
                ticker = event['ticker']
                if ticker in members and (event_day in recent or ticker in active_cores):
                    evidence_by_ticker.setdefault(ticker, []).append(event)
    tickers = set(evidence_by_ticker) | set(active_cores)
    if day in replay.pools:
        result['member_limit_count'] = len(members & replay.pools[day].keys())
    supported_cores = [r for r in candidate.get('cores', []) if r['ticker'] in active_cores]
    for ticker in sorted(tickers):
        events = sorted(evidence_by_ticker.get(ticker, []), key=lambda e: e['date'])
        latest = events[-1] if events else {}
        core = qualified.get(ticker)
        attribution = latest.get('attribution') or {'status': 'missing', 'matches': [],
                'explanation': '缺少可核验的当日原因；题材成员关系保留。'}
        reference = next((r for r in supported_cores if r['ticker'] != ticker), None)
        own_return = daily_return(replay, ticker, day)
        reference_return = daily_return(replay, reference['ticker'], day) if reference else None
        if reference is None:
            linkage_status = 'self_reference' if any(r['ticker'] == ticker for r in supported_cores) else 'no_reference'
        elif own_return is None or reference_return is None:
            linkage_status = 'missing_prices'
        else:
            linkage_status = 'both_up' if own_return > 0 and reference_return > 0 else 'not_both_up'
        counted = bool(candidate.get('diffusion_evaluated') and ticker in candidate.get('followers', []) and not core)
        result['members'].append({
            'ticker': ticker, 'name': replay.market['stocks'][ticker].get('name') or latest.get('name') or ticker,
            'source_topics': [s for s in candidate.get('source_topics', [])
                              if ticker in replay.members.get((day, s['code']), set())],
            'membership_date': day, 'membership_status': 'confirmed',
            'role': 'core_candidate' if core else 'member', 'qualified_at': core.get('qualified_at') if core else None,
            'latest_limit_date': latest.get('date') or (core or {}).get('latest_limit_date'),
            'reason_date': latest.get('date'), 'reason': latest.get('reason', ''), 'attribution': attribution,
            'counted_follower': counted,
            'counting_note': '已认证核心，不重复计入独立跟随' if core else
                '计入本窗口已确认独立跟随' if counted else
                f'保留近{MEMBER_OBSERVATION_SESSIONS}日涨停观察，不计入近{rules.diffusion_sessions}日跟随' if latest.get('date') not in diffusion_days else
                '本题材未进入扩散计算，保留成员观察' if not candidate.get('diffusion_evaluated') else
                '保留成员观察，涨停归因待核验，暂不计入已确认跟随',
            'events': events,
            'linkage': {'date': day, 'status': linkage_status, 'stock_return': own_return,
                        'reference_core': {'ticker': reference['ticker'], 'name': reference['name'],
                                           'return': reference_return} if reference else None,
                        'member_limit_count': result['member_limit_count'],
                        'note': '仅展示复盘日同涨与成员涨停事实；不证明同一催化，不自动增加已确认扩散。'},
        })
    result['members'].sort(key=lambda r: (r['role'] != 'core_candidate', -(int((r['latest_limit_date'] or '0').replace('-', ''))), r['ticker']))
    result['missing_dates'] = sorted(missing)
    if missing or len(recent) < MEMBER_OBSERVATION_SESSIONS:
        result['status'] = 'partial'
    return result
