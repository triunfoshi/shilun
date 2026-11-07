"""Read-only acceptance endpoints, mounted below the existing review router."""
from datetime import date as CalendarDate
from fastapi import APIRouter, HTTPException, Query

from shilun.review import theme_acceptance

router = APIRouter(prefix='/theme-selection')


@router.get('')
def get_manifest():
    try:
        return theme_acceptance.manifest()
    except FileNotFoundError as error:
        raise HTTPException(404, '归因验收归档尚未生成；请先完成专项回放。') from error
    except (ValueError, OSError) as error:
        raise HTTPException(503, '归因验收归档暂不可读，请重新生成后重试。') from error


@router.get('/day')
def get_day(date: CalendarDate = Query(), revision: str | None = Query(None, pattern=r'^[a-f0-9]{16}$')):
    try:
        return theme_acceptance.day_payload(date.isoformat(), revision=revision)
    except FileNotFoundError as error:
        raise HTTPException(404, '所选日归因验收归档缺失，请重新生成。') from error
    except LookupError as error:
        raise HTTPException(404, '所选日期没有验收归档；请选择已列出的交易日。') from error
    except (ValueError, OSError) as error:
        raise HTTPException(409, '归因验收归档已更新或校验失败，请重新读取日期列表。') from error
