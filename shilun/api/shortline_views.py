"""Read-only, dated Vibe shortline views. Included under the review router."""
from datetime import date as CalendarDate
from copy import deepcopy
from concurrent.futures import Future
import logging
from threading import Lock
from time import monotonic

from fastapi import APIRouter, HTTPException, Query
from pymongo.errors import PyMongoError

from shilun.review.board_data import BoardResearchData
from shilun.review.vibe_shortline import history_payload, themes_payload
from shilun.review.vibe_shortline import VERSION

router = APIRouter(prefix='/shortline', tags=['shortline-views'])
logger = logging.getLogger(__name__)
_cache, _pending, _cache_lock = {}, {}, Lock()
_TTL_SECONDS = 30


def _read(builder, day, refresh=False, **kwargs):
    key = (VERSION, builder.__name__, day.isoformat(), tuple(sorted(kwargs.items())))
    with _cache_lock:
        hit = _cache.get(key)
        if not refresh and hit and monotonic()-hit[0] < _TTL_SECONDS:
            return deepcopy(hit[1])
        future = _pending.get(key)
        owner = future is None
        if owner:
            future = _pending[key] = Future()
    if not owner:
        return deepcopy(future.result(timeout=120))
    try:
        result = _read_store(builder, day, **kwargs)
        with _cache_lock:
            if len(_cache) >= 32:
                del _cache[min(_cache, key=lambda item: _cache[item][0])]
            _cache[key] = (monotonic(), deepcopy(result))
        future.set_result(result)
        return result
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with _cache_lock:
            _pending.pop(key, None)


def _read_store(builder, day, **kwargs):
    # Imported only on a request: parent review router can include us safely.
    from shilun.api.review import _open_store
    store = _open_store()
    try:
        return builder(BoardResearchData(store), day.isoformat(), **kwargs)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except PyMongoError as error:
        logger.warning('Shortline view unavailable: %s', type(error).__name__)
        raise HTTPException(503, '所选日短线归档暂不可读取，请稍后重试；不会用其他日期替代。') from error
    finally:
        store.close()


@router.get('/themes')
def get_themes(date: CalendarDate = Query(), refresh: bool = False):
    return _read(themes_payload, date, refresh=refresh)


@router.get('/history')
def get_history(date: CalendarDate = Query(), days: int = Query(30, ge=10, le=60), refresh: bool = False):
    if days not in (10, 20, 30, 60):
        raise HTTPException(422, '情绪历史窗口仅支持10、20、30、60个交易日')
    return _read(history_payload, date, days=days, refresh=refresh)
