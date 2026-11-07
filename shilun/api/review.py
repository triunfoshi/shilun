"""HTTP boundary for the materialized daily review decision page."""

from __future__ import annotations

from datetime import date as CalendarDate
import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pymongo.errors import ConnectionFailure, ExecutionTimeout, PyMongoError

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.review.service import DailyReviewService, REVIEW_SOCKET_TIMEOUT_MS
from shilun.review.overnight import overseas_snapshot
from shilun.review.live_emotion import snapshot as live_emotion_snapshot
from shilun.review.board_data import BoardResearchData
from shilun.review import research_ai


router = APIRouter(prefix="/api/v1/review", tags=["daily-review"])
from shilun.api.theme_acceptance import router as theme_acceptance_router
router.include_router(theme_acceptance_router)
from shilun.api.theme_cycles import router as theme_cycles_router
router.include_router(theme_cycles_router)
from shilun.api.shortline_views import router as shortline_views_router
router.include_router(shortline_views_router)
logger = logging.getLogger(__name__)


@router.get('/sentiment-context')
def get_sentiment_context(date: CalendarDate = Query()):
    """Read the selected day's archived sentiment window and industry structure."""
    from shilun.review.sentiment_context import context_payload
    store = _open_store()
    try:
        return context_payload(BoardResearchData(store), date.isoformat())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except PyMongoError as error:
        logger.warning('Sentiment context unavailable: %s', type(error).__name__)
        raise HTTPException(status_code=503, detail='情绪周期与行业结构暂不可读取，请稍后重试。') from error
    finally:
        store.close()


@router.get('/market-facts')
def get_market_facts(date: CalendarDate = Query(), refresh: bool = False, read_only: bool = False):
    """Dated chart supplement; reads cached DB facts without rebuilding review."""
    from shilun.review.market_facts import cached, cached_supplement, load_market_facts
    day = date.isoformat()
    if read_only:
        if refresh:
            raise HTTPException(400, '只读查询不能刷新量能和广度补充。')
        result = cached_supplement(day)
        return ({**result, 'read_only': True} if result is not None else
                {'status': 'not_materialized', 'analysis_date': day, 'read_only': True,
                 'market': {}, 'reason': '所选日量能和广度补充尚未归档；只读查询不会生成缓存或写入文件。'})
    if not refresh and (result := cached(day)) is not None:
        return result
    store = _open_store()
    try:
        return load_market_facts(store, day, refresh=refresh)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except PyMongoError as error:
        logger.warning('Market facts unavailable: %s', type(error).__name__)
        raise HTTPException(status_code=503, detail='所选日量能和广度补充暂不可用，保留原复盘数据，请稍后重试。') from error
    finally:
        store.close()


@router.get('/strength')
def get_strength(date: CalendarDate | None = None, start: CalendarDate | None = None,
                 window: int = Query(20, ge=1, le=120), limit: int = Query(50, ge=1, le=200)):
    from shilun.review.strength_review import rank_range
    from shilun.review.strong_stock_data import resolve_date
    store = _open_store()
    try:
        return rank_range(store, resolve_date(store, date.isoformat() if date else None), start.isoformat() if start else None, window, limit)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        store.close()


@router.get('/strength/detail')
def get_strength_detail(ticker: str = Query(pattern=r'^\d{6}\.(SH|SZ|BJ)$'), date: CalendarDate = Query(), start: CalendarDate | None = None):
    from shilun.review.strength_review import stock_detail
    store = _open_store()
    try:
        return stock_detail(store, ticker, date.isoformat(), start.isoformat() if start else None)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        store.close()


@router.get('/strength/minutes')
def get_strength_minutes(ticker: str = Query(pattern=r'^\d{6}\.(SH|SZ|BJ)$'), date: CalendarDate = Query()):
    from shilun.review.strength_review import minutes
    store = _open_store()
    try:
        return minutes(store, ticker, date.isoformat())
    finally:
        store.close()


@router.post('/strength/minutes')
def sync_strength_minutes(ticker: str = Query(pattern=r'^\d{6}\.(SH|SZ|BJ)$'), date: CalendarDate = Query()):
    from shilun.review.strength_review import minutes
    store = _open_store()
    try:
        return minutes(store, ticker, date.isoformat(), refresh=True)
    except Exception as error:
        logger.warning('Strength minutes unavailable: %s', type(error).__name__)
        raise HTTPException(status_code=503, detail='所选日分时暂不可用，请稍后重试；不会用别的日期替代。') from error
    finally:
        store.close()


@router.post('/strength/evidence')
def sync_strength_evidence(date: CalendarDate, start: CalendarDate):
    from shilun.review.strength_review import sync_lhb, selected_sessions
    store = _open_store()
    try:
        days = selected_sessions(store, date.isoformat(), start.isoformat())
        from shilun.review.board_data import sync_reasons
        return {'results': sync_lhb(store, days) + [sync_reasons(store, d) for d in days]}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        store.close()


@router.get('/strong-stocks')
def get_strong_stocks(date: CalendarDate | None = None):
    from shilun.review.strong_stock_data import load_snapshot, resolve_date
    store = _open_store()
    try:
        return load_snapshot(store, resolve_date(store, date.isoformat() if date else None))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        store.close()


@router.get('/strong-stocks/detail')
def get_strong_stock_detail(detail_id: str = Query(min_length=1, max_length=160)):
    from shilun.review.strong_stock_data import DETAILS
    store = _open_store()
    try:
        doc = store.collection(DETAILS).find_one({'_id': detail_id}, {'_id': 0})
        if not doc:
            raise HTTPException(status_code=404, detail='该份认证明细尚未归档')
        return doc
    finally:
        store.close()


def _run_strong_stock_rebuild(key, target, run_id):
    from datetime import datetime, timezone
    from shilun.review.strong_stock_data import JOBS, materialize
    store = _open_store()
    try:
        store.collection(JOBS).update_one({'_id': key, 'run_id': run_id}, {'$set': {'status': 'running'}})
        materialize(store, target)
        update = {'status': 'completed', 'error': None}
    except Exception as error:
        logger.exception('Strong stock rebuild failed (%s)', type(error).__name__)
        update = {'status': 'failed', 'error': '认证未完成，请检查日线、涨停价与交易日历覆盖后重试。'}
    finally:
        store.collection(JOBS).update_one({'_id': key, 'run_id': run_id},
            {'$set': {**update, 'updated_at': datetime.now(timezone.utc)}})
        store.close()


@router.post('/strong-stocks/rebuild')
def rebuild_strong_stocks(background_tasks: BackgroundTasks, date: CalendarDate | None = None):
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    from pymongo.errors import DuplicateKeyError
    from shilun.review.strong_stock_data import JOBS, resolve_date
    from shilun.review.strong_stock_candidates import VERSION
    store = _open_store()
    try:
        target = resolve_date(store, date.isoformat() if date else None)
        key, now, run_id = f'{VERSION}:{target}', datetime.now(timezone.utc), uuid4().hex
        try:
            store.collection(JOBS).update_one({'_id': key, '$or': [
                {'status': {'$nin': ['queued', 'running']}}, {'expires_at': {'$lt': now}}]},
                {'$set': {'date': target, 'status': 'queued', 'run_id': run_id, 'error': None,
                          'updated_at': now, 'expires_at': now + timedelta(minutes=30)}}, upsert=True)
        except DuplicateKeyError:
            return {'status': 'running', 'date': target}
        background_tasks.add_task(_run_strong_stock_rebuild, key, target, run_id)
        return {'status': 'queued', 'date': target}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        store.close()


@router.get('/taxonomy')
def get_taxonomy(date: CalendarDate, q: str = '', kind: str = '') -> dict:
    """Inspect the independent catalog, including boards excluded by P2 gates."""
    from shilun.market.taxonomy import SNAPSHOTS, CATALOG, VERSION
    store = _open_store()
    try:
        key = f'{VERSION}:{date.isoformat()}'
        doc = store.collection(SNAPSHOTS).find_one({'_id': key, 'status': 'ready'}, {'_id': 0, 'member_refs': 0})
        if not doc:
            staged = store.collection(CATALOG).find_one({'_id': key}, {'_id': 0, 'missing_members': 1}) or {}
            return {'date': date.isoformat(), 'version': VERSION, 'status': 'pending', 'rows': [],
                    'reason': '所选日统一分类尚未完成同步；不会替换为其他日期或旧来源。', **staged}
        rows = [r for r in doc['rows'] if (not q or q.lower() in (r['name'] + r['code']).lower())
                and (not kind or r['kind'] == kind)]
        from shilun.market.research_scope import topic_allowed
        rows = [{**r, 'research_eligible': bool(r.get('research_eligible') and topic_allowed(r))} for r in rows]
        return {**doc, 'rows': rows, 'matched_count': len(rows)}
    finally:
        store.close()


@router.get("/overseas")
def get_overseas() -> dict:
    """Independent latest foreign quotes, never part of an A-share date snapshot."""
    return overseas_snapshot()


@router.get("/live-emotion")
def get_live_emotion(force: bool = Query(default=False)) -> dict:
    """Current board-sentiment pools; read-only and intentionally not a daily snapshot."""
    payload = live_emotion_snapshot(force=force is True)
    if payload.get('available') and payload.get('snapshot_mode') == 'settled':
        store = None
        try:
            from shilun.review.live_emotion import reconcile_settled_downside
            store = _open_store()
            payload = reconcile_settled_downside(payload, store)
        except (PyMongoError, HTTPException):
            payload = {**payload, 'downside_scope': '东方财富原始跌停池；全市场收盘核验暂不可用'}
        finally:
            if store is not None:
                store.close()
    return payload


def _open_store() -> MongoSnapshotStore:
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=503, detail="Mongo 未配置，无法读取复盘快照。")
    try:
        return MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=REVIEW_SOCKET_TIMEOUT_MS, initialize_indexes=False)
    except Exception as error:
        logger.warning("Review store initialization failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="云端数据库暂时无法连接，请检查网络与数据库通道后重试。") from error


def _database_error(error: PyMongoError) -> HTTPException:
    # Raw driver errors can contain server details or credentials.
    logger.warning("Daily review database operation failed: %s", type(error).__name__)
    if isinstance(error, (ConnectionFailure, ExecutionTimeout)):
        return HTTPException(status_code=503, detail="云端数据库读取超时或连接中断，请检查网络后重试；已有复盘快照可重新读取。")
    return HTTPException(status_code=503, detail="复盘数据库操作未完成，请稍后重试；持续失败时请检查后台日志。")


@router.get('/topic-detail')
def get_topic_detail(detail_id: str = Query(min_length=1, max_length=160)) -> dict:
    from shilun.review.topic_details import COLLECTION, refresh_topic_names
    store = _open_store()
    try:
        doc = store.collection(COLLECTION).find_one({'_id': detail_id}, {'_id': 0, 'theme': 1})
        if not doc:
            raise HTTPException(status_code=404, detail='题材快照已更新，请重新读取所选日复盘。')
        refresh_topic_names(store, [doc['theme']])
        return doc['theme']
    except PyMongoError as error:
        raise _database_error(error) from error
    finally:
        store.close()


@router.get("/dashboard")
def get_dashboard(
    date: CalendarDate | None = Query(default=None, description="YYYY-MM-DD; 默认最新有日线数据的交易日"),
) -> dict:
    store = _open_store()
    try:
        snapshot = DailyReviewService(store).get(date.isoformat() if date else None, allow_build=False)
        from shilun.review.market_facts import cached_supplement
        day = snapshot.get('analysis_date')
        supplement = cached_supplement(day) if isinstance(day, str) else None
        return {**snapshot, 'market_facts_supplement': supplement} if supplement is not None else snapshot
    except PyMongoError as error:
        raise _database_error(error) from error
    except RuntimeError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    finally:
        store.close()


@router.post("/refresh")
def refresh_dashboard(
    date: CalendarDate | None = Query(default=None, description="YYYY-MM-DD; 默认最新有日线数据的交易日"),
) -> dict:
    """Explicit, expensive full-market materialization.

    Normal browser refreshes only read the resulting snapshot.  Cloud schedulers
    call the job after data synchronization; this endpoint is retained for a
    deliberate manual rebuild.
    """

    store = _open_store()
    try:
        return DailyReviewService(store).build_and_save(date.isoformat() if date else None, sync_sources=True)
    except PyMongoError as error:
        raise _database_error(error) from error
    except RuntimeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        store.close()


@router.post("/futures/refresh")
def refresh_futures(date: CalendarDate | None = Query(default=None)) -> dict:
    """Sync only seven-session CFFEX evidence; never rebuild the full market."""
    from shilun.api import _load_cffex_positions_snapshot
    from shilun.review.service import REVIEW_COLLECTION
    store = _open_store()
    try:
        target = DailyReviewService(store).latest_date(date.isoformat() if date else None)
        payload = _load_cffex_positions_snapshot(store, target, refresh=True)
        # Keep the chart fresh without triggering another full-market scan.
        store.collection(REVIEW_COLLECTION).update_many({'analysis_date': target},
            {'$set': {'market.futures': payload}})
        if payload.get('status') != 'ok' or payload.get('refresh_errors'):
            payload['message'] = '部分期指席位未获得所选日数据；可用归档已保留，请稍后重试。'
        return payload
    except PyMongoError as error:
        raise _database_error(error) from error
    finally:
        store.close()


def _board_request(action):
    store = _open_store()
    try:
        return action(BoardResearchData(store), store)
    except PyMongoError as error:
        raise _database_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        store.close()


def _first_board_payload(data, store, target, refresh=False):
    from shilun.review.board_ladder import ladder_payload, refresh_reference
    result = data.refresh(target=target, first_only=True) if refresh else data.first_board(target)
    if refresh:
        sessions = data.sessions(result['date'], 1)
        if len(sessions) >= 2 and sessions[-1] == result['date'] and not data.pools([sessions[-2]]):
            data.refresh(target=sessions[-2], first_only=True)
        result['ladder_refresh'] = refresh_reference(store)
    result['ladder'] = ladder_payload(data, result['date'])
    result["ai"] = research_ai.status()
    result["notes"] = list(store.collection(research_ai.NOTE_COLLECTION).find(
        {"date": result["date"], "kind": "firstboard", "completed": True}, {"_id": 0}))
    return result


@router.get("/board-dynamics")
def get_board_dynamics(date: CalendarDate | None = Query(default=None)) -> dict:
    from shilun.review.board_dynamics import dynamics_payload
    return _board_request(lambda data, _: dynamics_payload(data, date.isoformat() if date else None))


@router.get("/first-board")
def get_first_board(date: CalendarDate | None = Query(default=None)) -> dict:
    return _board_request(lambda data, store: _first_board_payload(data, store, date.isoformat() if date else None))


@router.post("/first-board/refresh")
def refresh_first_board(date: CalendarDate | None = Query(default=None)) -> dict:
    return _board_request(lambda data, store: _first_board_payload(data, store, date.isoformat() if date else None, True))


@router.get("/new-faces")
def get_new_faces(date: CalendarDate):
    from shilun.market.strategy_candidates import review_facet
    return _board_request(lambda _, store: review_facet(store, date.isoformat(), 'turnover_new_faces'))


@router.get('/topic-flow')
def get_topic_flow(date: CalendarDate, code: str = Query(pattern=r'^\d{6}\.(TI|SI)$')):
    from shilun.review.minute_capital_flow import load_flow
    return _board_request(lambda _, store: load_flow(store, code, date.isoformat()))


@router.post('/topic-flow/refresh')
def refresh_topic_flow(date: CalendarDate, code: str = Query(pattern=r'^\d{6}\.(TI|SI)$')):
    from shilun.review.minute_capital_flow import refresh_flow
    return _board_request(lambda _, store: refresh_flow(store, code, date.isoformat()))


def _effects_with_expectations(data, store, days, target, refresh=False):
    from shilun.market.strategy_candidates import review_facet
    result = data.refresh(days, target) if refresh else data.effects(days, target)
    result['label_expectations'] = review_facet(store, result['window_to'], 'expectations')
    return result


@router.get("/board-effects")
def get_board_effects(days: int = Query(default=30, ge=20, le=30), date: CalendarDate | None = Query(default=None)) -> dict:
    if days not in (20, 30):
        raise HTTPException(status_code=422, detail="打板效应仅支持20或30个交易日")
    return _board_request(lambda data, store: _effects_with_expectations(data, store, days, date.isoformat() if date else None))


@router.post("/board-effects/refresh")
def refresh_board_effects(days: int = Query(default=30, ge=20, le=30), date: CalendarDate | None = Query(default=None)) -> dict:
    if days not in (20, 30):
        raise HTTPException(status_code=422, detail="打板效应仅支持20或30个交易日")
    return _board_request(lambda data, store: _effects_with_expectations(data, store, days, date.isoformat() if date else None, True))


class BoardResearchRequest(BaseModel):
    date: CalendarDate
    code: str = Field(pattern=r"^[0-9]{6}$")


@router.post("/research/analyze")
async def analyze_first_board(body: BoardResearchRequest):
    store = _open_store()
    try:
        data = await asyncio.to_thread(BoardResearchData(store).first_board, body.date.isoformat())
        stock = next((row for row in data["stocks"] if row["code"] == body.code), None)
        if stock is None:
            raise HTTPException(status_code=404, detail="所选日期的首板名单中没有该股票，请重新读取名单。")
    except Exception:
        store.close()
        raise
    async def stream():
        try:
            async for chunk in research_ai.stream_analysis(stock, body.date.isoformat(), store):
                yield chunk
        finally:
            store.close()
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


__all__ = ["router"]
