"""Resume dated consumers only when their actual cached dependencies permit it.

The caller owns the global data-pipeline lease. Providers are invoked only for
missing, due source tasks; observing a later independent source never reruns the
full-market sync. GET handlers only read the resulting task ledger.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json

from shilun.jobs.pipeline_state import TaskLedger


class DependencyUnavailable(ValueError):
    pass


def revision(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:24]


def _allowed(task, input_revision, now):
    if not task:
        return True
    if task.get('status') == 'running':
        return False
    if task.get('input_revision') != input_revision:
        return True
    if task.get('status') == 'failed' or int(task.get('attempts', 0)) >= int(task.get('max_attempts', 5)):
        return False
    stamp = task.get('next_retry_at')
    if stamp:
        due = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        return due.replace(tzinfo=due.tzinfo or timezone.utc) <= now
    return True


def _source_result(check):
    return {'input_revision': check['revision'], 'output_revision': check['revision'],
            'date': check.get('date'), 'key': check['key'], 'reason': check.get('reason'),
            'coverage': check.get('coverage'), 'ready': check.get('ready')}


def _needs_repair(check):
    return check.get('needs_retry', not check.get('ready'))


def reconcile(store, day, *, dependency_loader=None, dependency_runner=None,
              strong_builder=None, review_factory=None, theme_builder=None,
              ledger=None, now=None, progress=None):
    """Attempt due sources, validate caches again, then refresh changed consumers."""
    from shilun.jobs.pipeline_dependencies import inspect_dependencies, prepare_dependency
    from shilun.review.strong_stock_data import materialize
    from shilun.review.service import DailyReviewService, REVIEW_VERSION
    from shilun.jobs.theme_daily_job import run as build_themes

    now = now or datetime.now(timezone.utc)
    ledger = ledger or TaskLedger(store)
    ledger.recover_expired(day)
    history_cache = {}
    def all_checks(database, target):
        from shilun.jobs.pipeline_optional_sources import inspect_optional_sources
        from shilun.jobs.pipeline_dependencies import inspect_core_history, inspect_theme_history
        checks = inspect_dependencies(database, target, include_history=False)
        for key, inspector in (('core_history', inspect_core_history), ('theme_history', inspect_theme_history)):
            if key not in history_cache:
                history_cache[key] = inspector(database, target)
        return checks + list(history_cache.values()) + inspect_optional_sources(database, target)
    def prepare_one(database, target, key):
        from shilun.jobs.pipeline_optional_sources import OPTIONAL_KEYS, prepare_optional_source
        return prepare_optional_source(database, target, key) if key in OPTIONAL_KEYS else prepare_dependency(database, target, key)
    load, prepare = dependency_loader or all_checks, dependency_runner or prepare_one
    checks = load(store, day)
    reports, repaired = [], []
    attempted = []

    # Theme validation can reveal an exact historical reason/pool gap. Persist
    # that dated repair separately instead of replaying every source in history.
    for task in ledger.snapshot(day):
        stage = task.get('stage', '')
        if not stage.startswith('repair:') or task.get('status') in ('success', 'degraded'):
            continue
        _, key, source_day = stage.split(':', 2)
        if not _allowed(task, task.get('input_revision'), now):
            continue
        ledger.start(day, stage, input_revision=task.get('input_revision'))
        attempted.append(key)
        try:
            report = prepare(store, source_day, key)
            if report.get('status') in ('unavailable', 'missing', 'failed') or report.get('ready') is False:
                raise DependencyUnavailable(report.get('error_message') or report.get('reason') or f'{source_day} {key} 未就绪')
            ledger.succeed(day, stage, result={'source_date': source_day, 'output_revision': revision(report)})
            repaired.append(stage)
        except Exception as error:
            ledger.fail(day, stage, error)

    source_dependencies = {'daily_bars': ('trade_calendar',), 'previous_daily_bars': ('trade_calendar',),
        'daily_basic': ('daily_bars',), 'moneyflow': ('daily_bars',), 'price_limits': ('daily_bars',),
        'industry_taxonomy': ('trade_calendar', 'daily_bars'), 'benchmark_bars': ('trade_calendar',),
        'board_pools': ('trade_calendar',), 'limit_reasons': ('board_pools',),
        'first_board_auction': ('board_pools',), 'board_cohort_prices': ('daily_bars',),
        'review_history': ('trade_calendar',), 'board_history': ('trade_calendar',),
        'price_history': ('trade_calendar',), 'index_history': ('trade_calendar',)}
    by_key = {c['key']: c for c in checks}
    for key in list(by_key):
        check = by_key[key]
        stage, source_revision = 'dependency:' + check['key'], check['revision']
        task = ledger.get(day, stage)
        if check.get('ready') and not _needs_repair(check):
            if not task or task.get('output_revision') != source_revision or not ledger.ready(day, stage):
                ledger.succeed(day, stage, result=_source_result(check),
                               status='degraded' if check.get('status') == 'degraded' else 'success')
            continue
        blocked = ['dependency:' + dep for dep in source_dependencies.get(key, ())
                   if dep in by_key and not by_key[dep].get('ready')]
        if blocked:
            ledger.wait(day, stage, blocked)
            continue
        if not _allowed(task, source_revision, now):
            continue
        if progress:
            progress(stage=stage)
        ledger.start(day, stage, input_revision=source_revision)
        attempted.append(key)
        try:
            report = prepare(store, day, check['key'])
            reports.append(report)
            if report.get('status') in ('unavailable', 'missing', 'failed'):
                raise DependencyUnavailable(report.get('error_message') or report.get('reason') or f'{check["key"]} 未就绪')
        except Exception as error:
            ledger.fail(day, stage, error)
        by_key = {c['key']: c for c in load(store, day)}

    # A provider's success receipt alone is insufficient: inspect its stored date
    # and coverage. This also observes evidence written by the 19:35/19:40 jobs.
    if set(attempted) & {'daily_bars', 'previous_daily_bars', 'price_limits', 'review_history', 'price_history', 'suspensions'}:
        history_cache.pop('core_history', None)
    if set(attempted) & {'board_pools', 'board_history', 'limit_reasons'}:
        history_cache.pop('theme_history', None)
    checks = load(store, day)
    for check in checks:
        stage = 'dependency:' + check['key']
        task = ledger.get(day, stage)
        if check.get('ready') and not _needs_repair(check):
            if not task or task.get('output_revision') != check['revision'] or not ledger.ready(day, stage):
                ledger.succeed(day, stage, result=_source_result(check),
                               status='degraded' if check.get('status') == 'degraded' else 'success')
        elif task and task.get('status') == 'running':
            ledger.fail(day, stage, DependencyUnavailable(check.get('reason') or f'{check["key"]} 缓存复核未通过'))

    outputs, changed = {}, []
    stages = ('strong_stocks', 'review', 'theme_cycles')
    for stage in stages:
        relevant = [check for check in checks if stage in check.get('consumers', [])]
        blocked = ['dependency:' + check['key'] for check in relevant
                   if check.get('blocking') and not check.get('ready')]
        if stage != 'strong_stocks' and not ledger.ready(day, 'strong_stocks'):
            blocked.append('strong_stocks')
        repair_tasks = [task for task in ledger.snapshot(day) if str(task.get('stage', '')).startswith('repair:')]
        if stage == 'theme_cycles':
            blocked.extend(task['stage'] for task in repair_tasks if task.get('status') not in ('success', 'degraded'))
        if blocked:
            ledger.wait(day, stage, sorted(set(blocked)))
            continue
        input_revision = revision({'engine': REVIEW_VERSION,
            'inputs': [(c['key'], c['revision'], c.get('status')) for c in relevant],
            'strong': (ledger.get(day, 'strong_stocks') or {}).get('output_revision') if stage != 'strong_stocks' else None,
            'repairs': [(t['stage'], t.get('output_revision')) for t in repair_tasks] if stage == 'theme_cycles' else []})
        task = ledger.get(day, stage)
        if task and task.get('status') in ('success', 'degraded') and task.get('input_revision') == input_revision:
            continue
        if not _allowed(task, input_revision, now):
            continue
        if progress:
            progress(stage=stage)
        ledger.start(day, stage, dependencies=['dependency:' + c['key'] for c in relevant], input_revision=input_revision)
        try:
            if stage == 'strong_stocks':
                output = (strong_builder or materialize)(store, day)
                if output.get('date') != day or output.get('status') != 'ready':
                    raise ValueError('核心认证未完成同日发布')
            elif stage == 'review':
                output = (review_factory or DailyReviewService)(store).build_and_save(day, sync_sources=False)
                failed = [c['id'] for c in output.get('data_quality', {}).get('checks', [])
                          if c.get('status') == 'fail' and c.get('id') in ('daily_bar_cross_section', 'previous_session_join')]
                if output.get('analysis_date') != day or failed:
                    raise ValueError('复盘日期或必要数据质量核验未通过')
            else:
                output = (theme_builder or build_themes)(store, day)
                if output.get('date') != day or output.get('status') != 'complete':
                    raise ValueError('题材周期未完成同日发布')
            degraded = any(not c.get('ready') or c.get('status') == 'degraded' for c in relevant)
            quality = output.get('data_quality', {}).get('status') or output.get('quality_status')
            degraded = degraded or quality in ('degraded', 'blocked', 'historical_only')
            # Never hash generated_at: equivalent cache inputs must not cause
            # downstream rebuilds on every reconciliation heartbeat.
            output_revision = output.get('revision') or input_revision
            ledger.succeed(day, stage, result={'input_revision': input_revision, 'output_revision': output_revision,
                'generated_at': output.get('generated_at'), 'quality_status': quality,
                'counts': output.get('counts'), 'diagnostic_count': output.get('diagnostic_count')},
                status='degraded' if degraded else 'success')
            outputs[stage] = output
            changed.append(stage)
        except Exception as error:
            ledger.fail(day, stage, error)
            if stage == 'theme_cycles':
                for gap in getattr(error, 'missing', []):
                    key = {'limit_reasons': 'limit_reasons', 'settled_limit_pool': 'board_pools',
                           'benchmark': 'index_history'}.get(gap.get('dataset'))
                    source_day = gap.get('date')
                    if key and source_day and source_day <= day:
                        repair_stage = f'repair:{key}:{source_day}'
                        if not ledger.get(day, repair_stage):
                            ledger.wait(day, repair_stage, [])

    tasks = ledger.snapshot(day)
    pending = [t for t in tasks if t.get('status') not in ('success', 'degraded')]
    analysis_complete = all(ledger.ready(day, stage) for stage in stages)
    failed = any(t.get('status') == 'failed' for t in pending)
    status = 'failed' if failed and not analysis_complete else 'waiting' if not analysis_complete else 'degraded' if pending or any(t.get('status') == 'degraded' for t in tasks) else 'success'
    return {'analysis_date': day, 'status': status, 'stage': 'complete' if analysis_complete else 'waiting_dependencies',
            'changed_stages': changed, 'repaired_sources': repaired,
            'dependencies': checks, 'tasks': tasks, 'outputs': outputs, 'reports': reports,
            'pending_stages': [t['stage'] for t in pending]}
