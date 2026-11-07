"""Auditable entry screening for theme research, not a lifecycle or trade signal.

Production shortline identities are dated editorial event labels. Legacy DC
fixtures remain replayable for research comparison; they are not published as
current event-theme archives. Core qualification is independently versioned.
"""
from collections import Counter, defaultdict
from dataclasses import asdict
import math
import re
from statistics import median

from shilun.market.research_scope import stock_allowed, topic_allowed
from shilun.review.strong_stock_candidates import daily_evidence
from shilun.review.reason_attribution import match_reason
from shilun.review.theme_families import add_family_views, family_events, visible_topics
from shilun.review.event_theme_taxonomy import VERSION as EVENT_SCHEMA, event_attribution

VERSION = 'theme-selection-event-v4'


from shilun.review.theme_rules import SelectionRules


def reason_matches(name, reason):
    """Literal token attribution; no fuzzy industry or catalyst inference."""
    normalize = lambda text: re.sub(r'\s+', '', str(text or '')).casefold()
    names = {normalize(name)}
    if name.endswith('概念'):
        names.add(normalize(name[:-2]))
    tokens = {normalize(s) for s in re.split(r'[+＋、;；/|,，：:\n]', str(reason or ''))}
    return bool((names - {''}) & tokens)


def finite(value, default=None):
    return float(value) if isinstance(value, (float, int)) and math.isfinite(value) else default


class ThemeSelectionReplay:
    def __init__(self, market, taxonomy, *, attribution_mode='normalized'):
        if attribution_mode not in ('literal', 'normalized'):
            raise ValueError('不支持的归因版本')
        self.attribution_mode = attribution_mode
        self.event_themes = taxonomy.get('topic_schema') == EVENT_SCHEMA
        self.version = VERSION if attribution_mode == 'normalized' else 'theme-selection-research-v1'
        self.market, self.taxonomy = market, taxonomy.get('snapshots', {})
        self.sessions = market['sessions']
        if self.sessions != sorted(set(self.sessions)):
            raise ValueError('回放须提供有序、唯一的已核验交易日')
        self.catalogs, self.members = {}, {}
        for day, snapshot in self.taxonomy.items():
            if snapshot.get('date') != day:
                raise ValueError('分类快照日期不一致')
            rows = snapshot.get('rows', [])
            if len({r['code'] for r in rows}) != len(rows):
                raise ValueError('分类代码重复')
            self.catalogs[day] = {r['code']: r for r in rows}
            for row in rows:
                if row.get('membership_complete') and row.get('membership_date') == day:
                    self.members[day, row['code']] = {m['con_code'] for m in row.get('members', [])}
        if not self.event_themes:
            add_family_views(self.catalogs, self.members)
        self.event_names = {code: row['name'] for catalog in self.catalogs.values()
                            for code, row in catalog.items()} if self.event_themes else {}
        self.pools = {}
        for day, pool in market['pools'].items():
            if pool.get('date') != day or not pool.get('complete') or pool.get('snapshot_mode') == 'live':
                continue
            rows = pool.get('stocks', [])
            if len({r['ticker'] for r in rows}) != len(rows):
                raise ValueError('当日涨停池股票重复')
            self.pools[day] = {r['ticker']: r for r in rows if self.allowed(r['ticker'], day, r)}
        self._event_cache = {}

    def allowed(self, ticker, day, row=None):
        meta = self.market['stocks'].get(ticker)
        listed = str((meta or {}).get('list_date') or '').replace('-', '')[:8]
        return bool(meta and stock_allowed(meta, day) and stock_allowed(row or {}, day)
                    and (not listed or listed <= day.replace('-', '')))

    def window(self, day, count):
        if day not in self.sessions:
            raise ValueError('复盘日期不在已核验交易日历中')
        index = self.sessions.index(day)
        return self.sessions[max(0, index - count + 1):index + 1]

    def qualified(self, day):
        return {r['ticker']: r for r in self.market['cores'].get(day, [])
                if r.get('status') == 'qualified' and r.get('qualified_at')
                and r['qualified_at'] <= day and r.get('date') == day and self.allowed(r['ticker'], day)}

    def events(self, code, day):
        key = code, day
        if key in self._event_cache:
            return self._event_cache[key]
        family = family_events(self, code, day)
        if family is not None:
            self._event_cache[key] = family
            return family
        members = self.members.get((day, code))
        row = self.catalogs.get(day, {}).get(code)
        if self.event_themes and row is None and code in self.event_names:
            # Comparing today's tag with an earlier, fully observed date does
            # not require that tag to have existed then. It is a verified zero
            # unless that historical day's reasons are missing. Earlier-day
            # candidate enumeration still uses only its own dated catalog.
            row, members = {'name': self.event_names[code]}, set()
        if members is None or not row or day not in self.pools:
            return {'events': [], 'unknown': [], 'membership_only': [], 'complete': False}
        reason_doc = self.market.get('reasons', {}).get(day, {})
        reasons = reason_doc.get('reasons', {}) if reason_doc.get('date') == day else {}
        result = {'events': [], 'unknown': [], 'membership_only': [], 'complete': True}
        for ticker, stock in self.pools[day].items():
            # A missing reason has unknown event membership. Industry or old
            # membership must not turn it into a confirmed absence.
            reason = reasons.get(ticker.split('.')[0])
            if ticker not in members and not (self.event_themes and not str(reason or '').strip()):
                continue
            event = {'date': day, 'ticker': ticker, 'name': stock.get('name') or self.market['stocks'][ticker].get('name') or ticker,
                     'boards': stock.get('boards'), 'reason': reason or '', 'reason_source': reason_doc.get('source'),
                     'reason_fetched_at': reason_doc.get('fetched_at')}
            if self.event_themes:
                attribution = event_attribution(row, reason, day)
                event['attribution'] = attribution
                bucket = {'matched': 'events', 'missing': 'unknown'}.get(attribution['status'], 'membership_only')
            elif self.attribution_mode == 'literal':
                bucket = 'unknown' if not str(reason or '').strip() else 'events' if reason_matches(row['name'], reason) else 'membership_only'
            else:
                attribution = match_reason(row, reason, day)
                event['attribution'] = attribution
                bucket = 'events' if attribution['status'] == 'matched' else 'unknown' if attribution['status'] in ('missing', 'ambiguous') else 'membership_only'
            result[bucket].append(event)
        self._event_cache[key] = result
        return result

    def feedback(self, code, day):
        days = self.window(day, 2)
        if len(days) != 2:
            return {'status': 'missing_previous_session', 'cohort': [], 'promotion_rate': None, 'median_return': None}
        previous = days[0]
        events = self.events(code, previous)
        previous_cores = self.qualified(previous)
        cohort = []
        for event in events['events']:
            ticker = event['ticker']
            if ticker in previous_cores:
                continue
            evidence = daily_evidence(day, self.market.get('bars', {}).get(ticker, {}).get(day),
                                      self.market.get('limits', {}).get(ticker, {}).get(day),
                                      suspended=day in self.market.get('suspensions', {}).get(ticker, ()))
            cohort.append({**event, 'observation_date': day, 'state': evidence['state'],
                           'return': evidence.get('daily_return'), 'missing_inputs': evidence.get('missing_inputs', [])})
        observed = [r for r in cohort if r['state'] in ('sealed', 'not_sealed')]
        returns = [r['return'] for r in observed if finite(r['return']) is not None]
        return {'date': previous, 'status': 'partial' if not events['complete'] or events['unknown'] or len(observed) != len(cohort) else 'ready',
                'cohort': cohort, 'cohort_count': len(cohort), 'observed_count': len(observed),
                'unknown_reason_count': len(events['unknown']),
                'promoted': sum(r['state'] == 'sealed' for r in observed),
                'promotion_rate': sum(r['state'] == 'sealed' for r in observed) / len(observed) if observed else None,
                'median_return': median(returns) if returns else None,
                'negative_count': sum(r < 0 for r in returns)}

    def evaluate(self, day, rules=None):
        rules = rules or SelectionRules()
        diffusion_days = self.window(day, rules.diffusion_sessions)
        active_days = self.window(day, rules.core_active_sessions)
        all_cores = self.qualified(day)
        active = {t: r for t, r in all_cores.items() if r.get('latest_limit_date') in active_days}
        diagnostics = []
        snapshot = self.taxonomy.get(day, {})
        if not snapshot.get('core_mapping_complete'):
            diagnostics.append('核心的同日事件原因标签未完整覆盖，不能确认全量候选排序' if self.event_themes else '核心到题材的历史成分映射不完整，不能确认全量候选排序')
        missing_pools = [d for d in diffusion_days if d not in self.pools]
        if len(diffusion_days) < rules.diffusion_sessions or missing_pools:
            diagnostics.append('扩散窗口涨停池缺失：' + '、'.join(missing_pools))
        candidates = []
        for code, topic in sorted(visible_topics(self.catalogs.get(day, {})).items()):
            if not topic.get('research_eligible') or (not self.event_themes and not topic_allowed(topic)):
                continue
            row = {'code': code, 'name': topic['name'], 'source_topics': topic.get('source_topics', []),
                   'member_count': topic.get('member_count'), 'status': 'rejected',
                   'reason_codes': [], 'reasons': [], 'cores': [], 'followers': [], 'events': [], 'active_dates': [],
                   'unknown_reasons': [], 'membership_only_count': 0, 'membership_only_events': [],
                   'missing_membership_dates': [], 'feedback': {}, 'diffusion_evaluated': False}
            def reject(key, message, pending=False):
                row['reason_codes'].append(key)
                row['reasons'].append(message)
                if pending:
                    row['status'] = 'pending'
            if not topic.get('has_qualified_core'):
                reject('no_qualified_core', '没有已独立通过三连板及十日五板认证的核心', not snapshot.get('core_mapping_complete'))
                candidates.append(row)
                continue
            today_members = self.members.get((day, code))
            if today_members is None:
                reject('members_missing', '当日事件归因样本缺失' if self.event_themes else '当日完整成分缺失', True)
                candidates.append(row)
                continue
            related = {t: r for t, r in active.items() if t in today_members}
            if not related:
                reject('core_inactive', f'已认证核心近{rules.core_active_sessions}个交易日未涨停')
                candidates.append(row)
                continue
            attribution = Counter()
            attribution_evidence = defaultdict(list)
            unknown_core = set()
            for d in active_days:
                evidence = self.events(code, d)
                in_cycle = {t for t, r in related.items() if d >= (r.get('cycle', {}).get('start_date') or active_days[0])}
                if not evidence['complete']:
                    unknown_core.update(in_cycle)
                for event in evidence['events']:
                    if event['ticker'] in in_cycle:
                        attribution[event['ticker']] += 1
                        attribution_evidence[event['ticker']].append(event)
                unknown_core.update(e['ticker'] for e in evidence['unknown'] if e['ticker'] in in_cycle)
            supported = [{**r, 'attributed_limit_count': attribution[t], 'attribution_evidence': attribution_evidence[t]}
                         for t, r in related.items() if attribution[t]]
            supported.sort(key=lambda r: (-r['attributed_limit_count'], -int(r.get('cycle', {}).get('max_streak') or 0),
                                         -finite(r.get('cycle', {}).get('cumulative_return'), -1), r['ticker']))
            row['cores'] = supported
            if not supported:
                reject('core_attribution_missing' if unknown_core else 'core_attribution_mismatch',
                       '核心原因缺失、含义不明或历史成分缺失，归因待核验' if unknown_core else '仅共享成分标签，核心涨停原因未支持该题材', bool(unknown_core))
            row['diffusion_evaluated'] = True
            for d in diffusion_days:
                evidence = self.events(code, d)
                if not evidence['complete']:
                    row['missing_membership_dates'].append(d)
                row['events'].extend(e for e in evidence['events'] if e['ticker'] not in all_cores)
                row['unknown_reasons'].extend(e for e in evidence['unknown'] if e['ticker'] not in all_cores)
                row['membership_only_events'].extend(e for e in evidence['membership_only'] if e['ticker'] not in all_cores)
                row['membership_only_count'] += sum(e['ticker'] not in all_cores for e in evidence['membership_only'])
            row['followers'] = sorted({e['ticker'] for e in row['events']})
            row['active_dates'] = sorted({e['date'] for e in row['events']})
            incomplete = bool(missing_pools or row['missing_membership_dates'] or len(diffusion_days) != rules.diffusion_sessions)
            uncertain = incomplete or bool(row['unknown_reasons'])
            if incomplete:
                reject('window_incomplete', '扩散窗口缺涨停池或当日完整成分', True)
            if len(row['followers']) < rules.minimum_followers:
                reject('insufficient_followers', f'近{rules.diffusion_sessions}交易日仅{len(row["followers"])}只归因明确的非核心涨停股，要求至少{rules.minimum_followers}只', uncertain)
            if len(row['active_dates']) < rules.minimum_active_dates:
                reject('insufficient_persistence', f'跟随涨停只覆盖{len(row["active_dates"])}个交易日，要求至少{rules.minimum_active_dates}日', uncertain)
            row['feedback'] = self.feedback(code, day)
            row['attribution_partial'] = bool(unknown_core or row['unknown_reasons'])
            if not row['reason_codes']:
                row['status'] = 'eligible'
                row['reasons'] = [f'核心已认证且原因支持；{len(row["followers"])}只独立跟随股，跨{len(row["active_dates"])}个交易日涨停']
            candidates.append(row)
        # Fixed comparison, no synthetic total score. Missing feedback sorts
        # after a measured result; it is never silently converted to zero.
        def rank(row):
            feedback = row['feedback']
            core = row['cores'][0]
            observed = feedback.get('status') == 'ready' and feedback.get('median_return') is not None
            return (-len(row['active_dates']), -len(row['followers']),
                    -(feedback.get('promoted') if feedback.get('status') == 'ready' else -1), -int(observed),
                    -finite(feedback.get('median_return'), -999),
                    -int(core.get('cycle', {}).get('max_streak') or 0),
                    -finite(core.get('cycle', {}).get('cumulative_return'), -999), row['code'])
        eligible = sorted((r for r in candidates if r['status'] == 'eligible'), key=rank)
        groups = {}
        for row in eligible:
            core = row['cores'][0]
            if core['ticker'] not in groups:
                groups[core['ticker']] = {'id': core['ticker'], 'name': f'{row["name"]} · {core["name"]}',
                    'primary_code': row['code'], 'core': core, 'branches': [], 'events': []}
            group = groups[core['ticker']]
            group['branches'].append({'code': row['code'], 'name': row['name'], 'source_topics': row['source_topics'],
                                      'followers': len(row['followers']), 'active_dates': len(row['active_dates'])})
            group['events'].extend(row['events'])
        selected = list(groups.values())[:rules.maximum_directions]
        selected_ids = {g['id'] for g in selected}
        for group in groups.values():
            group['events'] = sorted({(e['date'], e['ticker']): e for e in group['events']}.values(), key=lambda e: (e['date'], e['ticker']))
            group['followers'] = sorted({e['ticker'] for e in group['events']})
            group['selected'] = group['id'] in selected_ids
        for row in eligible:
            row['selected'] = row['cores'][0]['ticker'] in selected_ids
            if not row['selected']:
                row['reason_codes'].append('outside_direction_limit')
                row['reasons'].append('符合准入，但按固定比较顺序未进入最多三个方向')
        individual = [{**r, 'selected_direction': t in selected_ids, 'active_for_theme_entry': t in active,
                       'observation_label': '核心个股观察；抱团性质需结合反馈核验'} for t, r in sorted(all_cores.items())]
        pending = sum(c['status'] == 'pending' for c in candidates)
        if pending:
            diagnostics.append(f'{pending}个候选存在原因或成分缺口，名单为已知证据下的结果')
        return {'date': day, 'version': self.version, 'rules': asdict(rules), 'diagnostics': diagnostics,
                'status': 'partial' if diagnostics else 'ready', 'directions': selected,
                'all_eligible_groups': list(groups.values()), 'candidates': candidates, 'individual_observation': individual,
                'reason_counts': dict(Counter(key for r in candidates for key in r['reason_codes'])),
                'scope_note': self.market.get('scope_note'),
                'topic_schema': EVENT_SCHEMA if self.event_themes else 'legacy-dc',
                'attribution_mode': self.attribution_mode,
                'ranking_note': '先比跟随涨停持续天数，再比独立跟随股数、昨日固定跟随队列晋级数、完整反馈及中位收益，最后比核心高度与走势涨幅；同主核心合并后取最多三个方向。'}
