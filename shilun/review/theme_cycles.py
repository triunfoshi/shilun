"""Chronological theme lifecycle. Frozen cohorts, dated facts, no future ranking.

Entry is delegated to theme_selection. Retention never depends on today's rank.
Every published day is a deep copy; later transitions cannot rewrite history.
"""
from copy import deepcopy
from math import prod
from statistics import median

from shilun.review.strong_stock_candidates import daily_evidence
from shilun.review.theme_observation import daily_return, member_observation
from shilun.review.theme_rules import CycleRules, SelectionRules, rule_config
from shilun.review.theme_core_tracking import post_exit_feedback

VERSION = 'theme-cycle-event-v3'
LABELS = {'fermentation': '发酵', 'strengthening': '加强', 'disagreement': '分歧',
          'recovery': '回流', 'retreat': '退潮'}


def drawdown_warning(drawdown, threshold=.15):
    return drawdown is not None and drawdown >= threshold - 1e-9


def price_path(replay, ticker, start, day):
    """Chain same-day close/pre-close to avoid ex-right gaps. Peak is as-of only."""
    if not start or start not in replay.sessions:
        return {'drawdown': None, 'cumulative_return': None, 'missing_dates': [start or day]}
    days = [d for d in replay.sessions if start <= d <= day]
    value, peak, missing = 1., None, []
    for d in days:
        if d in replay.market.get('suspensions', {}).get(ticker, ()):
            continue
        change = daily_return(replay, ticker, d)
        if change is None:
            missing.append(d)
            continue
        value *= 1 + change
        peak = max(peak or value, value)
    return {'drawdown': 1-value/peak if peak and not missing else None,
            'cumulative_return': value-1 if peak and not missing else None, 'missing_dates': missing}


def diagnostic(topic, day, code, fields, action, candidate=None):
    return {'key': f'{topic}:{day}:{code}', 'topic_id': topic, 'date': day, 'code': code,
            'missing_or_conflicting_fields': fields, 'candidate_stage': candidate,
            'unmet_conditions': fields, 'action': action, 'rule_version': rule_config()['version']}


def fixed_feedback(replay, tickers, day):
    rows = []
    for ticker in tickers:
        row = daily_evidence(day, replay.market.get('bars', {}).get(ticker, {}).get(day),
                             replay.market.get('limits', {}).get(ticker, {}).get(day),
                             suspended=day in replay.market.get('suspensions', {}).get(ticker, ()))
        row.update(ticker=ticker, name=replay.market.get('stocks', {}).get(ticker, {}).get('name') or ticker)
        # Suspensions/delistings never disappear from the frozen denominator.
        row['return'] = daily_return(replay, ticker, day) if row['state'] in ('sealed', 'not_sealed') else None
        row['opened'] = (row['state'] == 'not_sealed' and row['high'] >= row['up_limit']-.005
                         if row['state'] in ('sealed', 'not_sealed') else None)
        rows.append(row)
    values = [r['return'] for r in rows if r['return'] is not None]
    return {'rows': rows, 'count': len(rows), 'observed_count': len(values),
            'median_return': median(values) if len(values) == len(rows) and values else None,
            'sealed': sum(r['state'] == 'sealed' for r in rows) if len(values) == len(rows) else None,
            'opened': sum(r['opened'] is True for r in rows) if len(values) == len(rows) else None,
            'complete': bool(rows) and len(values) == len(rows)}


def rolling_return(replay, ticker, day, count):
    days = replay.window(day, count)
    values = [daily_return(replay, ticker, d) for d in days]
    return prod(1+v for v in values)-1 if len(days) == count and all(v is not None for v in values) else None


def transition(previous, metrics, counts, rules):
    """Ordered branches: missing/conflict -> confirmed retreat -> repair -> weakness."""
    if not metrics['complete']:
        return None, 'data_missing', '数据未齐，暂停阶段与连续天数判断'
    if metrics.get('conflict'):
        return None, 'evidence_conflict', '涨停池与日线涨停状态冲突，先核对来源'
    if counts['weak'] >= rules.weakness_sessions or (
            metrics['drawdown_warning'] and counts['weak'] >= rules.warning_weakness_sessions):
        return 'retreat', 'ready', f'连续{counts["weak"]}次观察：核心与固定后排近{rules.feedback_sessions}日均亏损，固定后排没有涨停接力'
    if metrics['strong']:
        if previous == 'disagreement':
            return ('recovery' if counts['strong'] >= rules.recovery_sessions else 'disagreement'), 'ready', (
                f'修复{counts["strong"]}日；要求连续{rules.recovery_sessions}日核心、后排与接力同步改善')
        return 'strengthening', 'ready', '核心上涨、固定后排不弱，并有已确认涨停接力'
    if metrics['weak'] or metrics['mixed'] or metrics['drawdown_warning']:
        return 'disagreement', 'ready', '出现分歧，保留观察；单日断板或15%预警均不单独退出'
    if metrics['balanced']:
        return previous or 'fermentation', 'ready', '核心与固定后排未转弱，暂缺加强证据，延续观察'
    return None, 'rule_unmatched', '现有证据未满足阶段条件，保留观察并列出指标供核验'


class ThemeCycleReplay:
    def __init__(self, selection, rules=None):
        self.selection = selection
        self.rules = rules or CycleRules()
        self.entry_rules = SelectionRules()
        self.active, self.archived, self.history = [], [], []
        self.eligible_runs = {}
        self.last_day = None

    def new_cycle(self, group, day, selection):
        codes = {b['code'] for b in group['branches']}
        front = sorted({r['ticker'] for c in selection['candidates'] if c['code'] in codes for r in c['cores']})
        core = deepcopy(group['core'])
        return {'id': f'{group["primary_code"]}:{core["ticker"]}:{day}', 'primary_code': group['primary_code'],
                'name': group['branches'][0]['name'], 'branches': deepcopy(group['branches']),
                'core': core, 'entered_at': day, 'cohort_date': day,
                'front': front or [core['ticker']], 'back': sorted(set(group['followers'])-set(front)),
                'entry_evidence': deepcopy(group['events']), 'cycle_stage': 'fermentation',
                'last_confirmed_stage': 'fermentation', 'last_confirmed_date': day,
                'counts': {'weak': 0, 'strong': 0}, 'history': [], 'exit_date': None, 'exit_reason': None}

    def evidence(self, cycle, day):
        replay = self.selection
        front, back = fixed_feedback(replay, cycle['front'], day), fixed_feedback(replay, cycle['back'], day)
        core = next(r for r in front['rows'] if r['ticker'] == cycle['core']['ticker'])
        path = price_path(replay, core['ticker'], cycle['core'].get('cycle', {}).get('start_date'), day)
        issues = []
        def issue(code, fields, action):
            issues.append(diagnostic(cycle['id'], day, code, fields, action))
        recent = replay.window(day, 2)
        missing_pools = [d for d in recent if d not in replay.pools]
        if len(recent) != 2 or missing_pools:
            issue('data_missing', [{'date': d, 'field': 'settled_up_pool'} for d in missing_pools or [day]], '补齐当日及上一交易日收盘涨停池后重算')
        missing_members = [b['code'] for b in cycle['branches'] if (day, b['code']) not in replay.members]
        if missing_members:
            issue('mapping_failed', [{'date': day, 'field': 'event_reason_members' if replay.event_themes else 'dc_members', 'code': code} for code in missing_members], '补齐对应交易日涨停原因归因样本，禁止用其他日期原因回填' if replay.event_themes else '补齐对应交易日东方财富完整成分，禁止用当前成分回填')
        missing_quotes = [{'date': day, 'ticker': r['ticker'], 'field': r['missing_inputs'] or r['state']}
                          for r in front['rows']+back['rows'] if r['return'] is None]
        if missing_quotes or path['missing_dates']:
            issue('data_missing', missing_quotes + [{'date': d, 'ticker': core['ticker'], 'field': 'adjusted_close_chain'} for d in path['missing_dates']], '补齐固定队列行情、官方涨停价及停牌信息后重算；名单保持不变')
        events, unknown = {}, {}
        for branch in cycle['branches']:
            e = replay.events(branch['code'], day)
            for item in e['events']:
                events[item['ticker']] = item
            for item in e['unknown']:
                unknown[item['ticker']] = item
        independent = set(events) - set(cycle['front']) - replay.qualified(day).keys()
        uncertain = set(unknown) - set(events) - set(cycle['front']) - replay.qualified(day).keys()
        # Unknown reasons are not zero diffusion; only block a zero-based exit.
        if not independent and uncertain:
            issue('attribution_pending', [{'date': day, 'ticker': t, 'field': 'limit_reason'} for t in sorted(uncertain)], '复核原始涨停原因；成员仍保留，未确认原因不能判定跟随归零')
        conflicts = [r['ticker'] for r in front['rows']+back['rows'] if r['state'] in ('sealed', 'not_sealed')
                     and day in replay.pools and ((r['state'] == 'sealed') != (r['ticker'] in replay.pools[day]))
                     and replay.allowed(r['ticker'], day)]
        if conflicts:
            issue('evidence_conflict', [{'date': day, 'ticker': t, 'field': 'pool_vs_daily_limit'} for t in conflicts], '核对收盘板池与官方涨停价，确认同一市场范围后重新生成')
        cr, br = core['return'], back['median_return']
        core_window = rolling_return(replay, core['ticker'], day, self.rules.feedback_sessions)
        back_windows = [rolling_return(replay, t, day, self.rules.feedback_sessions) for t in cycle['back']]
        back_window = median(back_windows) if back_windows and all(v is not None for v in back_windows) else None
        if core_window is None or back_window is None:
            issue('data_missing', [{'date': day, 'field': 'fixed_cohort_three_session_returns'}], '补齐近三交易日固定队列收盘及前收盘；不将缺失收益视为零')
        ready = cr is not None and br is not None
        weak = (ready and core_window is not None and back_window is not None
                and core_window < 0 and back_window < 0 and back['sealed'] == 0)
        strong = ready and not weak and cr > 0 and br >= 0 and bool(independent or back['sealed'])
        metrics = {'complete': not any(i['code'] != 'evidence_conflict' for i in issues), 'conflict': bool(conflicts),
                   'core_return': cr, 'back_return': br,
                   'new_follower_count': len(independent) if day in replay.pools and not missing_members else None,
                   'core_window_return': core_window, 'back_window_return': back_window,
                   'pending_reason_count': len(uncertain), 'drawdown': path['drawdown'],
                   'drawdown_warning': drawdown_warning(path['drawdown'], self.rules.drawdown_warning),
                   'core_cumulative_return': path['cumulative_return'], 'weak': bool(weak), 'strong': bool(strong),
                   'mixed': bool(ready and (cr < 0 or br < 0)), 'balanced': bool(ready and cr >= 0 and br >= 0)}
        return {'front_feedback': front, 'back_feedback': back, 'metrics': metrics, 'diagnostics': issues,
                'today_followers': [events[t] for t in sorted(independent)]}

    def step(self, day):
        replay = self.selection
        if self.last_day and (day <= self.last_day or replay.window(day, 2)[0] != self.last_day):
            raise ValueError('周期回放必须按连续交易日逐日推进')
        self.last_day = day
        selected = replay.evaluate(day)
        groups = selected['all_eligible_groups']
        entry_complete = (replay.taxonomy.get(day, {}).get('core_mapping_complete') and
                          all(d in replay.pools for d in replay.window(day, self.entry_rules.diffusion_sessions)))
        keys = {(g['primary_code'], g['core']['ticker'], g['core'].get('leg', {}).get('leg_id')) for g in groups}
        self.eligible_runs = {key: self.eligible_runs.get(key, 0)+1 for key in keys} if entry_complete else {}
        today_exits, diagnostics = [], []
        for cycle in self.active:
            data = self.evidence(cycle, day)
            cycle.update(data)
            m = data['metrics']
            for key in ('weak', 'strong'):
                cycle['counts'][key] = cycle['counts'][key]+1 if m['complete'] and not m['conflict'] and m[key] else 0
            stage, status, reason = transition(cycle['last_confirmed_stage'], m, cycle['counts'], self.rules)
            cycle.update(cycle_stage=stage, diagnostic_status=status, transition_reason=reason, date=day)
            if stage:
                cycle.update(last_confirmed_stage=stage, last_confirmed_date=day)
            elif not data['diagnostics']:
                cycle['diagnostics'].append(diagnostic(cycle['id'], day, status, [m], '按显示的指标核验阶段规则，保持当前名单'))
            if stage == 'retreat':
                cycle.update(exit_date=day, exit_reason=reason)
                today_exits.append(cycle)
        # A stronger alternative can replace only an already twice-weak incumbent.
        # This branch never removes a healthy direction merely because its rank fell.
        occupied_codes = {b['code'] for c in self.active for b in c['branches']}
        occupied_cores = {c['core']['ticker'] for c in self.active}
        waiting = []
        for group in groups:
            if group['core']['ticker'] in occupied_cores or any(b['code'] in occupied_codes for b in group['branches']):
                continue
            key = (group['primary_code'], group['core']['ticker'], group['core'].get('leg', {}).get('leg_id'))
            exited = [c for c in self.archived+today_exits if any(b['code'] == group['primary_code'] for b in c['branches'])]
            if exited:
                # A theme exit does not manufacture a new stock board leg. The
                # selection evaluator already checks the current leg's own
                # qualification. Still require fresh entry evidence after exit.
                after_exit = [d for d in replay.sessions if exited[-1]['exit_date'] < d <= day]
                enough = min(len(after_exit), self.eligible_runs.get(key, 0)) >= self.rules.restart_sessions
                trial_metrics = self.evidence(self.new_cycle(group, day, selected), day)['metrics']
                if not enough or not trial_metrics['complete'] or trial_metrics['conflict'] or not trial_metrics['strong']:
                    waiting.append({'name': group['name'], 'reason': '退出后需连续两日重新满足准入，且核心和新跟随承接改善；新连板段须通过自身资格认证'})
                    continue
            available = len(self.active)-len(today_exits) < self.entry_rules.maximum_directions
            replacement = None
            if not available and self.eligible_runs.get(key, 0) >= self.rules.replacement_sessions:
                trial = self.new_cycle(group, day, selected)
                metrics = self.evidence(trial, day)['metrics']
                if metrics['complete'] and not metrics['conflict'] and metrics['strong']:
                    replacement = next((c for c in self.active if c not in today_exits and c['counts']['weak'] >= self.rules.replacement_sessions), None)
            if not entry_complete or not available and replacement is None:
                waiting.append({'name': group['name'], 'reason': '准入来源缺口，暂缓新增' if not entry_complete else '已有观察方向继续保留；尚未满足退出或替换条件'})
                continue
            if replacement:
                replacement.update(cycle_stage='retreat', last_confirmed_stage='retreat', exit_date=day,
                    exit_reason=f'连续{replacement["counts"]["weak"]}日同弱；替代方向{group["name"]}连续准入且当日有接力', last_confirmed_date=day)
                replacement['transition_reason'] = replacement['exit_reason']
                today_exits.append(replacement)
            cycle = self.new_cycle(group, day, selected)
            cycle.update(self.evidence(cycle, day), date=day, diagnostic_status='ready', transition_reason='核心资格与跨日扩散达标，开始固定队列观察')
            if cycle['diagnostics']:
                cycle.update(cycle_stage=None, diagnostic_status=cycle['diagnostics'][0]['code'], last_confirmed_stage=None, last_confirmed_date=None)
            self.active.append(cycle)
            occupied_cores.add(cycle['core']['ticker'])
            occupied_codes.update(b['code'] for b in cycle['branches'])
        for cycle in self.active:
            cycle['stage_label'] = LABELS.get(cycle['cycle_stage'], '待核验')
            cycle['history'].append({'date': day, 'stage': cycle['cycle_stage'], 'stage_label': cycle['stage_label'],
                                     'reason': cycle['transition_reason'], 'diagnostic_status': cycle['diagnostic_status'],
                                     'metrics': deepcopy(cycle['metrics'])})
            diagnostics.extend(cycle['diagnostics'])
            candidates = {c['code']: c for c in selected['candidates']}
            cycle['member_observation'] = member_observation(replay, day, candidates.get(cycle['primary_code'], {'code': cycle['primary_code']}))
        self.archived.extend(deepcopy(today_exits))
        self.active = [c for c in self.active if c['exit_date'] is None]
        if not entry_complete:
            diagnostics.append(diagnostic('universe', day, 'mapping_failed' if not replay.taxonomy.get(day, {}).get('core_mapping_complete') else 'data_missing',
                [{'date': day, 'field': 'core_mapping/three_session_pools'}], '补齐核心同日事件原因及三日收盘板池后重算，新方向暂缓入选' if replay.event_themes else '补齐核心到DC成分及三日收盘板池后重算，新方向暂缓入选'))
        payload = {'date': day, 'version': VERSION, 'rules': rule_config(self.rules),
                   'topic_schema': selected['topic_schema'],
                   'status': 'partial' if diagnostics else 'ready' if self.active else 'empty',
                   'active': self.active,
                   'archived': [{**c, 'post_exit': post_exit_feedback(replay, c, day)} for c in self.archived],
                   'waiting': waiting, 'diagnostics': diagnostics,
                   'selection_diagnostics': selected['diagnostics'], 'scope_note': replay.market.get('scope_note'),
                   'note': '板块退潮与核心相对强势分别跟踪；退出不删除原样本，核心反弹不自动判定板块回流。历史数据事后重建，尚未做收益验证。'}
        result = deepcopy(payload)
        self.history.append(result)
        return result

    def run(self, days):
        return [self.step(day) for day in days]
