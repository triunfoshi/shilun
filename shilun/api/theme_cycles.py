"""Shared cycle archive, exact date and version. Read requests never start jobs."""
from datetime import date as CalendarDate
from fastapi import APIRouter, HTTPException, Query
from pymongo.errors import PyMongoError
from shilun.review import theme_cycle_store, theme_repair, theme_distribution
from shilun.review.theme_distribution import open_store

router = APIRouter(prefix='/theme-cycles')


@router.get('')
def get_cycles(date: CalendarDate | None = None, revision: str | None = Query(None, pattern='^[a-f0-9]{16}$')):
    try:
        data = theme_cycle_store.day_payload(date.isoformat() if date else None, revision=revision)
        if data.get('date') and theme_distribution.enabled():
            try:
                store = open_store()
                try:
                    data['repair_job'] = theme_repair.status(store, data['date'])
                finally:
                    store.close()
                theme_repair.runner_settings()
                data['repair_supported'] = True
            except Exception:
                data['repair_supported'] = False
                data['repair_unavailable_reason'] = '云端回补状态或管理连接配置不可用；已有周期归档仍可读取。'
        return data
    except (ValueError, OSError, KeyError) as error:
        raise HTTPException(409, '短线周期归档更新或校验失败，请重新读取；仍失败需重建该日归档。') from error


@router.post('/repair')
def repair_cycles(date: CalendarDate):
    if not theme_distribution.enabled():
        raise HTTPException(503, '当前使用离线归档；回补分析需要已配置的云端题材归档。')
    store = None
    try:
        store = open_store()
        return theme_repair.start(store, date.isoformat())
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except PyMongoError as error:
        raise HTTPException(503, '云端回补任务状态暂不可用，请稍后重新读取后再试。') from error
    finally:
        if store:
            store.close()


@router.get('/detail')
def get_detail(date: CalendarDate, cycle_id: str = Query(min_length=1, max_length=160),
               revision: str = Query(pattern='^[a-f0-9]{16}$')):
    data = get_cycles(date, revision)
    cycle = next((c for c in data['active']+data['archived'] if c['id'] == cycle_id), None)
    if cycle is None:
        raise HTTPException(404, '同日同版本中不存在此观察周期')
    return {'date': data['date'], 'revision': data['revision'], 'rules': data['rules'], 'cycle': cycle}
