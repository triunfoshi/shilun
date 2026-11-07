"""Explicit, persisted recovery requests executed in the existing ECS data container.

Only a date and a run ID enter the fixed remote command. The worker reuses the
container's credentials, accepted checkpoint, leases and atomic publication.
"""
from datetime import date, datetime, timedelta, timezone
import base64
import os
from pathlib import Path
import re
import shlex
import subprocess
import uuid

from pymongo.errors import DuplicateKeyError, PyMongoError

JOBS = 'review_theme_repair_jobs'
VERSION = 'theme_repair_v1'
ACTIVE = ('queued', 'running')
LABELS = {'queued': '等待云端启动', 'backfill_reasons': '回补同日涨停原因',
          'analyze': '连续回放并发布周期', 'complete': '已完成', 'launch': '启动云端任务'}


def _now():
    return datetime.now(timezone.utc)


def _public(row):
    if not row:
        return {'status': 'missing'}
    result = {k: v for k, v in row.items() if k not in ('_id', 'expires_at')}
    if row.get('status') in ACTIVE and row.get('expires_at'):
        expires = row['expires_at'].replace(tzinfo=timezone.utc)
        if expires < _now():
            result.update(status='failed', message='上次回补任务已中断或超时，可重新回补分析。')
    return result


def status(store, day):
    job = store.collection(JOBS).find_one({'_id': 'active', 'date': day}, {'_id': 0})
    result = _public(job)
    if result.get('status') == 'running' and result.get('stage') == 'analyze':
        from shilun.review.theme_distribution import CONTROL
        current = store.collection(CONTROL).find_one({'_id': 'job'}) or {}
        if current.get('date') == day and str(current.get('started_at', '')) >= str(result.get('started_at', '')):
            result['stage_label'] = current.get('stage_label') or result['stage_label']
            result['missing'] = current.get('missing', [])
    return result


def runner_settings():
    target = os.getenv('SHILUN_THEME_REPAIR_SSH_TARGET', 'root@47.110.33.132')
    key = Path(os.getenv('SHILUN_THEME_REPAIR_SSH_KEY', str(Path.home()/'.ssh/shilun_ecs_management_ed25519'))).expanduser()
    container = os.getenv('SHILUN_THEME_REPAIR_CONTAINER', 'shilun-data-data-sync-1')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*', target):
        raise ValueError('题材回补SSH目标配置不合法')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', container) or not key.is_file():
        raise ValueError('题材回补管理身份或数据容器未配置')
    return target, key, container


def launch(day, run_id, *, execute=None):
    # The worker source is application code, not a free-form command supplied by a request.
    if date.fromisoformat(day).isoformat() != day or not re.fullmatch(r'[a-f0-9]{32}', run_id):
        raise ValueError('回补日期或任务标识不合法')
    target, key, container = runner_settings()
    encoded = base64.b64encode(Path(__file__).read_bytes()).decode('ascii')
    bootstrap = ('import base64,sys;'
                 'scope={"__name__":"theme_repair_worker"};'
                 f'exec(compile(base64.b64decode({encoded!r}),"theme_repair_worker","exec"),scope);'
                 'scope["remote_run"](sys.argv[1],sys.argv[2])')
    remote = shlex.join(['docker', 'exec', '-d', '-w', '/app', container, 'python', '-c', bootstrap, day, run_id])
    command = ['ssh', '-i', str(key), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
               '-o', 'ConnectTimeout=8', target, remote]
    result = (execute or subprocess.run)(command, capture_output=True, text=True, timeout=20, check=False)
    if result.returncode:
        raise RuntimeError('云端回补任务启动失败，请检查管理连接和数据容器')


def start(store, day, *, launcher=None):
    from shilun.review.board_data import settled_target
    from shilun.review.sources import load_review_sessions
    from shilun.review.theme_distribution import CONTROL
    if date.fromisoformat(day).isoformat() != day or settled_target(day) != day:
        raise ValueError('只可对已收盘交易日回补分析')
    sessions = load_review_sessions(store, day)
    if not sessions or sessions[-1] != day:
        raise ValueError('所选日不是已核验交易日')
    current = store.collection(CONTROL).find_one({'_id': 'current'})
    if current and day < current['date']:
        raise ValueError('目标日早于已发布归档；请选择最新交易日，避免缩短连续周期')
    now, run_id = _now(), uuid.uuid4().hex
    document = {'date': day, 'run_id': run_id, 'status': 'queued', 'stage': 'queued',
                'worker_version': VERSION,
                'stage_label': LABELS['queued'], 'started_at': now.isoformat(),
                'updated_at': now.isoformat(), 'expires_at': now+timedelta(minutes=60), 'reports': []}
    try:
        store.collection(JOBS).update_one({'_id': 'active', '$or': [
            {'status': {'$nin': list(ACTIVE)}}, {'expires_at': {'$lte': now}}]}, {'$set': document}, upsert=True)
    except DuplicateKeyError:
        other = store.collection(JOBS).find_one({'_id': 'active'})
        return {**_public(other), 'reused': True}
    try:
        (launcher or launch)(day, run_id)
    except Exception as error:
        uncertain = isinstance(error, subprocess.TimeoutExpired)
        store.collection(JOBS).update_one({'_id': 'active', 'run_id': run_id, 'status': 'queued'}, {'$set': {
            'status': 'queued' if uncertain else 'failed', 'stage': 'launch',
            'stage_label': '启动结果待确认' if uncertain else LABELS['launch'],
            'updated_at': _now().isoformat(), 'error_type': type(error).__name__,
            'expires_at': _now()+timedelta(minutes=3) if uncertain else now,
            'message': '启动回执超时，正在核对云端任务状态；请勿重复提交。' if uncertain
                       else '云端回补任务未能启动，请检查管理连接和数据容器后重试。'}})
    return status(store, day)


def backfill_market(store, market, save, *, repair_days, sync=None):
    """Repair only missing dated reason archives; never borrow a later day's text."""
    from shilun.review.board_data import REASON_COLLECTION, sync_reasons
    from shilun.jobs.theme_daily_job import ThemeInputError
    missing = [day for day in repair_days if day in market['context_days'] and market.get('pools', {}).get(day, {}).get('stocks')
               and (market.get('reasons', {}).get(day, {}).get('date') != day
                    or not market.get('reasons', {}).get(day, {}).get('reasons'))]
    reports = []
    for index, day in enumerate(missing):
        save(stage='backfill_reasons', progress={'done': index, 'total': len(missing), 'date': day}, reports=reports)
        report = (sync or sync_reasons)(store, day)
        saved = store.collection(REASON_COLLECTION).find_one({'date': day}, {'_id': 0}) or {}
        if report.get('status') != 'ok' or saved.get('date') != day or not saved.get('reasons'):
            raise ThemeInputError([{'date': day, 'dataset': 'limit_reasons',
                                    'reason': '同日涨停原因回补失败或源日期不一致'}])
        market.setdefault('reasons', {})[day] = saved
        reports.append({'date': day, 'count': len(saved['reasons']), 'source': saved.get('source')})
    save(stage='analyze', progress={'done': len(missing), 'total': len(missing)}, reports=reports)
    return market


def remote_run(day, run_id):
    """Detached ECS worker; it keeps running when the page or local API closes."""
    import threading
    from shilun.common.config import load_config
    from shilun.common.db import MongoSnapshotStore
    from shilun.jobs.theme_daily_job import run, ROOT
    from shilun.review.theme_selection_data import collect_market_inputs, read_gzip
    import json
    config = load_config()
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=60_000, initialize_indexes=False)
    jobs, stop = store.collection(JOBS), threading.Event()
    query = {'_id': 'active', 'date': day, 'run_id': run_id}
    def save(**values):
        values.update(updated_at=_now().isoformat(), expires_at=_now()+timedelta(minutes=60))
        if 'stage' in values:
            values['stage_label'] = LABELS.get(values['stage'], values['stage'])
        result = jobs.update_one(query, {'$set': values})
        if not result.matched_count:
            raise RuntimeError('回补任务所有权已失效')
    def heartbeat():
        while not stop.wait(20):
            try:
                save()
            except PyMongoError:
                # A transient database interruption must not permanently stop
                # lease renewal; the next tick retries the same owned record.
                continue
            except Exception:
                return
    pulse = threading.Thread(target=heartbeat, daemon=True)
    try:
        save(status='running', stage='backfill_reasons')
        pulse.start()
        checkpoint = ROOT/'inputs.json'
        source = ROOT/json.loads(checkpoint.read_text())['source'] if checkpoint.exists() else ROOT/'seed'
        previous = read_gzip(source/'market_inputs.json.gz')
        def collector(*args):
            market = collect_market_inputs(*args)
            days = [d for d in market['context_days'] if d > previous['end'] or d == day]
            return backfill_market(store, market, save, repair_days=days)
        result = run(store, day, collector=collector)
        save(status='complete', stage='complete', revision=result['revision'], finished_at=_now().isoformat(),
             message='同日原因已核验，周期与归因明细已共同发布。')
    except Exception as error:
        save(status='failed', error_type=type(error).__name__, missing=getattr(error, 'missing', []),
             message='回补分析未完成，上一完整归档保留；请查看缺口或云端任务阶段后重试。')
    finally:
        stop.set()
        if pulse.is_alive():
            pulse.join(timeout=2)
        store.close()
