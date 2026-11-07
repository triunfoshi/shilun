"""Seven-step decision evidence API; deliberately has no order dependencies."""
from datetime import date
from fastapi import APIRouter, HTTPException, Query
from shilun.decision_research import service

router = APIRouter(prefix='/api/v1/trade-decision', tags=['trade-decision'])


@router.get('/dates')
def dates():
    return {'dates': service.available_dates()}


@router.get('/plan')
def plan(date: date = Query(...)):
    day = date.isoformat()
    state = service.job_status(day)
    if state.get('status') in ('running', 'error'):
        return state
    payload = service.load_plan(day)
    return {'status': 'ready', 'plan': payload} if payload else {**service.JOBS.get(day, {'status': 'missing'}), 'date': day}


@router.get('/mode-research/dates')
def research_dates():
    return {'dates': service.research_dates()}


@router.get('/mode-research')
def mode_research(date: date = Query(...), snapshot_id: str | None = Query(None, pattern=r'^[0-9a-f]{64}$')):
    day = date.isoformat()
    state = service.job_status(day, research_only=True)
    if snapshot_id is None and state.get('status') in ('running', 'error'):
        return state
    try:
        payload = service.load_mode_research(day, snapshot_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {'status': 'ready', 'research': payload} if payload else {'status': 'missing', 'date': day}


@router.post('/mode-research/build')
def build_mode_research(date: date = Query(...)):
    try:
        return service.start_build(date.isoformat(), research_only=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post('/build')
def build(date: date = Query(...)):
    try:
        return service.start_build(date.isoformat())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get('/tracking')
def tracking(date: date = Query(...), at: str | None = Query(None, pattern=r'^\d{2}:\d{2}$')):
    try:
        return service.tracking(date.isoformat(), at)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        raise HTTPException(503, '分钟缓存读取失败，请稍后重试') from None
