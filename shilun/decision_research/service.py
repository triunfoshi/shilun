"""Local frozen analytical snapshots; source databases are strictly read-only."""
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import json
import logging
import re
import uuid

from .data import read_dataset, profile
from .engine import build_plan, VERSION
from .tracking import build_tracking
from .mode_research import build_mode_research, validate, source_fingerprint, reference
from .mode_research import VERSION as RESEARCH_VERSION

ROOT = Path(__file__).resolve().parents[2] / 'runtime' / 'trade_decision'
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='decision-research')
LOCK = Lock()
JOBS = {}
RESEARCH_JOBS = {}
FUTURES = {}
LOG = logging.getLogger(__name__)
TZ = ZoneInfo('Asia/Shanghai')


def path_for(day):
    canonical = date.fromisoformat(day).isoformat()
    if canonical != day:
        raise ValueError('日期须为YYYY-MM-DD')
    return ROOT / canonical / 'plan.json'


def load_plan(day):
    p = path_for(day)
    if not p.is_file():
        return None
    plan = json.loads(p.read_text())
    if plan.get('analysis_date') != day:
        raise ValueError('预案日期与文件不一致')
    return plan


def save_plan(plan, coverage=None):
    if plan.get('version') == VERSION:
        ref = plan.get('mode_research_ref', {})
        research = load_mode_research(plan['analysis_date'], ref.get('snapshot_id'))
        if not research or ref != reference(research) or any(plan.get(k) != research[k] for k in ('conclusion', 'route', 'market', 'default_band', 'next_day_first_boards')):
            raise ValueError('预案引用的模式评估尚未保存或不一致')
    path = path_for(plan['analysis_date'])
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation freezes the baseline. A later reload never overwrites it.
    with LOCK:
        if path.exists():
            previous = load_plan(plan['analysis_date'])
            if previous.get('version') == plan.get('version'):
                return previous
            archive = path.parent / 'revisions'
            archive.mkdir(exist_ok=True)
            revision_path = archive / f'{uuid.uuid4().hex}.json'
            revision_path.write_text(path.read_text())
            plan['supersedes'] = {'version': previous.get('version'), 'built_at': previous.get('built_at'),
                'archive': str(revision_path.relative_to(ROOT)), 'reason': '计算口径升级；原预案保留，不把重建当作当时已知结果'}
        temporary = path.with_name(f'.{uuid.uuid4().hex}.json')
        temporary.write_text(json.dumps(plan, ensure_ascii=False, allow_nan=False))
        temporary.replace(path)
        if coverage is not None:
            path.with_name('coverage.json').write_text(json.dumps(coverage, ensure_ascii=False, indent=2, allow_nan=False))
    return plan


def available_dates():
    return sorted([p.parent.name for p in ROOT.glob('????-??-??/plan.json')], reverse=True)


def research_path(day, snapshot_id=None):
    folder = path_for(day).parent
    if snapshot_id is not None:
        if not re.fullmatch(r'[0-9a-f]{64}', snapshot_id):
            raise ValueError('模式评估快照编号无效')
        return folder / 'mode-research' / f'{snapshot_id}.json'
    return folder / 'mode-research.json'


def load_mode_research(day, snapshot_id=None):
    path = research_path(day, snapshot_id)
    if not path.is_file():
        return None
    research = json.loads(path.read_text())
    validate(research)
    if research['analysis_date'] != day or snapshot_id and research['snapshot_id'] != snapshot_id:
        raise ValueError('模式评估日期或快照编号与文件不一致')
    return research


def save_mode_research(research):
    validate(research)
    day, sid = research['analysis_date'], research['snapshot_id']
    path = research_path(day, sid)
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            research = load_mode_research(day, sid)
        else:
            temporary = path.with_name(f'.{uuid.uuid4().hex}.json')
            temporary.write_text(json.dumps(research, ensure_ascii=False, allow_nan=False))
            temporary.replace(path)
        latest = research_path(day)
        temporary = latest.with_name(f'.{uuid.uuid4().hex}.json')
        temporary.write_text(json.dumps(research, ensure_ascii=False, allow_nan=False))
        temporary.replace(latest)
    return research


def research_dates():
    return sorted([p.parent.name for p in ROOT.glob('????-??-??/mode-research.json')], reverse=True)


def ensure_mode_research(data):
    research = load_mode_research(data['date'])
    if research and research['version'] == RESEARCH_VERSION and research['source_fingerprint'] == source_fingerprint(data):
        return research
    return save_mode_research(build_mode_research(data))


def _progress(day, research_only, stage, message):
    jobs = RESEARCH_JOBS if research_only else JOBS
    with LOCK:
        jobs[day] = {**jobs.get(day, {}), 'status': 'running', 'date': day,
                     'stage': stage, 'message': message, 'updated_at': datetime.now(TZ).isoformat()}
    LOG.info('Decision generation %s: %s', day, message)


def job_status(day, research_only=False):
    jobs = RESEARCH_JOBS if research_only else JOBS
    with LOCK:
        state = dict(jobs.get(day, {}))
        if state.get('started_at'):
            state['elapsed_seconds'] = max(0, int((datetime.now(TZ) - datetime.fromisoformat(state['started_at'])).total_seconds()))
        return state


def _finished(day, research_only, future):
    """Executor errors must not leave a completed task displayed as running."""
    jobs = RESEARCH_JOBS if research_only else JOBS
    with LOCK:
        if FUTURES.get((day, research_only)) is future:
            FUTURES.pop((day, research_only))
            if jobs.get(day, {}).get('status') == 'running':
                jobs[day] = {**jobs[day], 'status': 'error',
                             'message': '生成任务异常结束，请重新生成；详细原因已记录到后台日志'}
    if not future.cancelled() and (error := future.exception()) is not None:
        LOG.error('Decision worker exited for %s', day, exc_info=(type(error), error, error.__traceback__))


def _build(day, research_only=False):
    store = None
    jobs = RESEARCH_JOBS if research_only else JOBS
    try:
        from shilun.api.review import _open_store
        _progress(day, research_only, 'reading', '连接盘后数据库')
        store = _open_store()
        # Bound the entire cache read, including all cursor batches.
        from pymongo import timeout
        with timeout(600):
            data = read_dataset(store, day, progress=lambda message: _progress(day, research_only, 'reading', message))
        _progress(day, research_only, 'calculating', '重建历史核心并计算三类玩法评估')
        research = ensure_mode_research(data)
        if not research_only:
            _progress(day, research_only, 'planning', '生成方向与个股预案')
            plan = build_plan(data, research)
            save_plan(plan, profile(data))
        with LOCK:
            jobs[day] = {'status': 'ready', 'date': day}
    except Exception as exc:
        LOG.exception('Decision research generation failed for %s', day)
        with LOCK:
            stage = jobs.get(day, {}).get('message', '生成评估')
            jobs[day] = {'status': 'error', 'date': day, 'message': str(exc) if isinstance(exc, ValueError) else f'{stage}未完成，请检查数据连接后重新生成'}
    finally:
        if store is not None:
            store.close()


def start_build(day, research_only=False):
    path_for(day)
    now = datetime.now(TZ)
    if day > now.date().isoformat() or day == now.date().isoformat() and now.hour < 15:
        raise ValueError('盘后预案只能使用已收盘日期')
    existing = load_plan(day) if not research_only else None
    if existing and existing.get('version') == VERSION:
        return {'status': 'ready', 'date': day, 'message': '读取已冻结预案'}
    jobs = RESEARCH_JOBS if research_only else JOBS
    with LOCK:
        if jobs.get(day, {}).get('status') == 'running':
            return dict(jobs[day])
        if any(j.get('status') == 'running' for queue in (JOBS, RESEARCH_JOBS) for j in queue.values()):
            return {'status': 'busy', 'date': day, 'message': '另一日期正在生成，请稍后重试'}
        jobs[day] = {'status': 'running', 'date': day, 'stage': 'queued',
                     'message': '等待后台开始生成', 'started_at': now.isoformat()}
        try:
            future = POOL.submit(_build, day, research_only)
            FUTURES[(day, research_only)] = future
        except Exception:
            jobs[day] = {'status': 'error', 'date': day, 'message': '后台任务启动失败，请重新生成'}
            LOG.exception('Decision task submission failed for %s', day)
            return dict(jobs[day])
    # A finished Future invokes callbacks immediately; never register under LOCK.
    future.add_done_callback(lambda done: _finished(day, research_only, done))
    return job_status(day, research_only)


def tracking(day, clock=None):
    plan = load_plan(day)
    if not plan:
        raise ValueError('请先生成所选盘后日期的预案')
    target = plan.get('next_date')
    if not target:
        raise ValueError('下一交易日历未核验，不能开始跟踪')
    now = datetime.now(TZ)
    if target > now.date().isoformat():
        raise ValueError('下一交易日尚未开始')
    current_clock = now.strftime('%H:%M') if target == now.date().isoformat() else '15:00'
    clock = clock or current_clock
    if len(clock) != 5 or not ('00:00' <= clock <= '23:59') or clock[2] != ':' or not (clock[:2] + clock[3:]).isdigit() or int(clock[3:]) > 59 or clock > current_clock:
        raise ValueError('观察时点必须有效且不晚于当前时间')
    from shilun.api.review import _open_store
    store = _open_store()
    try:
        docs = list(store.collection('minute_bars_daily').find({'trade_date': target.replace('-', ''), 'interval': '5'}, {'_id': 0}))
        auctions = list(store.collection('review_opening_auctions').find({'date': target}, {'_id': 0}))
    finally:
        store.close()
    return build_tracking(plan, docs, auctions, target, clock)
