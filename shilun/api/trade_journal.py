"""Review journal API over the configured ECS store; never routes to order writes."""
from contextlib import contextmanager
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from shilun.trade_journal.definitions import get_definitions
from shilun.trade_journal.service import TradeJournalError, TradeJournalService


def _same_origin(request: Request):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if origin:
        supplied, expected = urlsplit(origin), urlsplit(str(request.base_url))
        if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc):
            raise HTTPException(403, "请从当前石论页面提交复盘记录。")


router = APIRouter(prefix="/api/v1/trade-journal", tags=["trade-journal"],
                   dependencies=[Depends(_same_origin)])


def _open_store():
    # Reuse the existing connection configuration and timeout boundary. Index
    # creation belongs to the deployment/import step, never every page open.
    from shilun.api import _open_holdings_store
    return _open_holdings_store(read_only=True)


@contextmanager
def _service(*, write=False):
    store = _open_store()
    try:
        if write:
            from shilun.trade_journal.indexes import ensure_journal_indexes
            ensure_journal_indexes(store)
        yield TradeJournalService(store)
    except TradeJournalError as error:
        message = str(error)
        conflict = any(word in message for word in ("并发", "冲突", "版本已经", "版本已更新", "版本已变化", "最新版本", "预览已变化"))
        missing = message in {"库存轮不存在", "交易档案不存在", "决策不存在", "原始来源不存在", "来源不存在", "观察记录不存在", "冻结快照不存在", "计划不存在", "独立计划或确切版本不存在", "指定记录或确切版本不存在", "待办不存在", "生产成交不存在或标识不唯一", "过程复盘或确切版本不存在"}
        raise HTTPException(404 if missing else 409 if conflict else 422, message) from None
    finally:
        store.close()


@router.get("/definitions")
def definitions(category: str | None = None):
    result = get_definitions(category)
    if category is None or category == "mode":
        from shilun.trade_journal.mode_cards import ModeCardService
        with _service() as service:
            cards = ModeCardService(service.store)
            page = cards.list_cards(limit=1000)
            if page["total"] > len(page["items"]):
                raise HTTPException(503, "用户模式目录超过本次完整读取预算，请使用模式卡分页。")
            result["definitions"].extend(cards.get_definition_snapshot(row["mode_id"],row["revision"]) for row in page["items"])
            result["user_mode_catalog"] = {"count":page["total"],"scope":"当前目录；历史记录使用已冻结版本"}
    return result


@router.get("/summary")
def summary():
    with _service() as service:
        return service.get_summary()


@router.get("/episodes")
def episodes(query: str = "", start_date: str | None = None, end_date: str | None = None,
             status: str | None = None, qualification: str | None = None,
             mode_id: str | None = None, limit: int = Query(100, ge=1, le=1000),
             offset: int = Query(0, ge=0)):
    with _service() as service:
        return service.list_episodes(query=query, start_date=start_date, end_date=end_date,
            status=status, qualification=qualification, mode_id=mode_id, limit=limit, offset=offset)


@router.get("/episodes/{episode_id}")
def episode(episode_id: str):
    with _service() as service:
        result = service.get_episode(episode_id)
        if not result:
            raise HTTPException(404, "未找到这轮归档库存。")
        return result


@router.get("/episodes/{episode_id}/review-context")
def review_context(episode_id: str):
    from shilun.trade_journal.review_context import JournalReviewContextService
    with _service() as service:
        return JournalReviewContextService(service.store, journal=service).get(episode_id)


@router.get("/fills")
def fills(query: str = "", start_date: str | None = None, end_date: str | None = None,
          episode_id: str | None = None, source_id: str | None = None,
          limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)):
    with _service() as service:
        return service.list_fills(query=query, start_date=start_date, end_date=end_date,
            episode_id=episode_id, source_id=source_id, limit=limit, offset=offset)


@router.get("/financial-events")
def financial_events(query: str = "", start_date: str | None = None, end_date: str | None = None,
                     limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    with _service() as service:
        return service.list_financial_events(query=query, start_date=start_date, end_date=end_date,
                                              limit=limit, offset=offset)


@router.get("/sources")
def sources(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    with _service() as service:
        return service.list_sources(limit=limit, offset=offset)


@router.get("/sources/{source_id}")
def source(source_id: str, include_content: bool = False):
    with _service() as service:
        result = service.get_source(source_id, include_content=include_content)
        if result is None:
            raise HTTPException(404, "未找到归档来源。")
        return result


@router.get("/batches")
def batches(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    with _service() as service:
        return service.list_batches(limit=limit, offset=offset)


@router.post("/imports/preview")
def preview_import(payload: dict = Body(...)):
    with _service() as service:
        return service.preview_import(payload)


@router.post("/imports")
def import_batch(payload: dict = Body(...)):
    data = dict(payload)
    expected = data.pop("preview_digest", None)
    if not expected:
        raise HTTPException(422, "请先读取导入预览并携带preview_digest。")
    with _service(write=True) as service:
        return service.import_batch(data, expected)


@router.post("/episodes/{episode_id}/decisions")
def append_decision(episode_id: str, payload: dict = Body(...)):
    with _service(write=True) as service:
        return service.append_decision(episode_id, payload)


@router.get("/decisions/{decision_id}/history")
def decision_history(decision_id: str):
    with _service() as service:
        return service.get_decision_history(decision_id)


@router.post("/decisions/{decision_id}/expectation-checks")
def expectation_check(decision_id: str, payload: dict = Body(...)):
    with _service(write=True) as service:
        return service.append_expectation_check(decision_id, payload)


def _market_check_body(payload):
    body = dict(payload)
    decision_id = body.pop("decision_id", None)
    if not isinstance(decision_id, str) or not decision_id.strip():
        raise HTTPException(422, "请明确选择原决策及预期版本。")
    return decision_id, body


@router.post("/episodes/{episode_id}/market-checks/preview")
def preview_market_check(episode_id: str, payload: dict = Body(...)):
    from shilun.trade_journal.market_checks import JournalMarketCheckService
    decision_id, body = _market_check_body(payload)
    with _service() as service:
        return JournalMarketCheckService(service.store, journal=service).preview(episode_id, decision_id, body)


@router.post("/episodes/{episode_id}/market-checks")
def save_market_check(episode_id: str, payload: dict = Body(...)):
    from shilun.trade_journal.market_checks import JournalMarketCheckService
    decision_id, body = _market_check_body(payload)
    with _service(write=True) as service:
        return JournalMarketCheckService(service.store, journal=service).save(episode_id, decision_id, body)


@router.post("/episodes/{episode_id}/rule-comparison/preview")
def preview_rule_comparison(episode_id: str, payload: dict = Body(...)):
    from shilun.trade_journal.rule_comparison import JournalRuleComparisonService
    with _service() as service:
        return JournalRuleComparisonService(service.store, journal=service).preview(episode_id, payload)


@router.post("/links")
def link_decisions(payload: dict = Body(...)):
    with _service(write=True) as service:
        return service.link_decisions(payload)


@router.get("/observations")
def observations(query: str = "", start_date: str | None = None, end_date: str | None = None,
                 limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    with _service() as service:
        return service.list_observations(query=query, start_date=start_date, end_date=end_date,
                                          limit=limit, offset=offset)


@router.post("/observations")
def append_observation(payload: dict = Body(...)):
    with _service(write=True) as service:
        return service.append_observation(payload)


@router.get("/observations/{observation_id}")
def observation(observation_id: str):
    with _service() as service:
        return service.get_observation(observation_id)


@router.get("/snapshots/{snapshot_id}")
def snapshot(snapshot_id: str, include_content: bool = False):
    with _service() as service:
        return service.get_snapshot(snapshot_id, include_content=include_content)


@router.get("/plans")
def plans(query: str = "", start_date: str | None = None, end_date: str | None = None,
          date: str | None = None, ticker: str | None = None, status: str | None = None,
          limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    with _service() as service:
        return service.list_plans(query=query, start_date=start_date, end_date=end_date,
            date=date, ticker=ticker, status=status, limit=limit, offset=offset)


@router.post("/plans")
def freeze_plan(payload: dict = Body(...)):
    with _service(write=True) as service:
        return service.freeze_plan(payload)


@router.get("/plans/{plan_id}")
def plan(plan_id: str, revision: int | None = Query(None, ge=1)):
    with _service() as service:
        return service.get_plan(plan_id, revision=revision)


@router.post("/plans/{plan_id}/bind")
def bind_plan(plan_id: str, payload: dict = Body(...)):
    body = dict(payload)
    episode_id = body.pop("episode_id", None)
    if not episode_id:
        raise HTTPException(422, "绑定必须提供episode_id和确切计划版本。")
    if "revision" not in body and "plan_revision" in body:
        body["revision"] = body.pop("plan_revision")
    with _service(write=True) as service:
        return service.bind_plan(plan_id, episode_id, body)


@router.get("/episodes/{episode_id}/context")
def episode_context(episode_id: str, analysis_date: str, snapshot_id: str,
                    mode_key: str | None = None, window: str | None = None,
                    previous_analysis_date: str | None = None, previous_snapshot_id: str | None = None,
                    decision_id: str | None = None, decision_revision: int | None = Query(None, ge=1)):
    from shilun.trade_journal.research_bridge import load_journal_context, ResearchContextMissing
    with _service() as service:
        detail = service.get_episode(episode_id)
        try:
            return load_journal_context(service.store, detail, analysis_date=analysis_date,
                snapshot_id=snapshot_id, mode_key=mode_key, window=window,
                previous_analysis_date=previous_analysis_date, previous_snapshot_id=previous_snapshot_id,
                decision_id=decision_id, decision_revision=decision_revision)
        except ResearchContextMissing as error:
            raise HTTPException(404, str(error)) from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None


@router.get("/mode-cards")
def mode_cards(query: str = "", status: str | None = None,
               limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    from shilun.trade_journal.mode_cards import ModeCardService
    with _service() as service:
        return ModeCardService(service.store).list_cards(query=query,status=status,limit=limit,offset=offset)


@router.get("/mode-cards/{mode_id}")
def mode_card(mode_id: str, revision: int = Query(..., ge=1)):
    from shilun.trade_journal.mode_cards import ModeCardService
    with _service() as service:
        return ModeCardService(service.store).get_card(mode_id,revision)


@router.post("/mode-cards")
def save_mode_card(payload: dict = Body(...)):
    from shilun.trade_journal.mode_cards import ModeCardService
    with _service(write=True) as service:
        return ModeCardService(service.store).save_card(payload)


@router.get("/hypotheses")
def hypotheses(query: str = "", status: str | None = None,
                limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    from shilun.trade_journal.mode_cards import ModeCardService
    with _service() as service:
        return ModeCardService(service.store).list_hypotheses(query=query,status=status,limit=limit,offset=offset)


@router.get("/hypotheses/{hypothesis_id}")
def hypothesis(hypothesis_id: str, revision: int = Query(..., ge=1)):
    from shilun.trade_journal.mode_cards import ModeCardService
    with _service() as service:
        return ModeCardService(service.store).get_hypothesis(hypothesis_id,revision)


@router.post("/hypotheses")
def save_hypothesis(payload: dict = Body(...)):
    from shilun.trade_journal.mode_cards import ModeCardService
    with _service(write=True) as service:
        return ModeCardService(service.store).save_hypothesis(payload)


@router.get("/episodes/{episode_id}/allocations")
def allocations(episode_id: str, revision: int | None = Query(None, ge=1)):
    from shilun.trade_journal.allocations import TradeAllocationService
    with _service() as service:
        owner=TradeAllocationService(service.store,journal=service)
        return {"record":owner.get_allocations(episode_id,revision),"allocation":owner.calculate(episode_id,revision)}


@router.get("/episodes/{episode_id}/allocations/history")
def allocation_history(episode_id: str):
    from shilun.trade_journal.allocations import TradeAllocationService
    with _service() as service:
        return TradeAllocationService(service.store,journal=service).history(episode_id)


@router.post("/episodes/{episode_id}/allocations")
def save_allocations(episode_id: str, payload: dict = Body(...)):
    from shilun.trade_journal.allocations import TradeAllocationService
    with _service(write=True) as service:
        return TradeAllocationService(service.store,journal=service).append_allocations(episode_id,payload)


@router.get("/analytics")
def analytics(query: str = "", start_date: str | None = None, end_date: str | None = None,
              group_by: str | None = None):
    from shilun.trade_journal.analytics import JournalAnalyticsService
    with _service() as service:
        return JournalAnalyticsService(service.store,journal=service).report(query=query,start_date=start_date,end_date=end_date,group_by=group_by)


@router.get("/analytics/groups/{group_id}")
def analytics_group(group_id: str, query: str = "", start_date: str | None = None,
                    end_date: str | None = None, group_by: str | None = None):
    from shilun.trade_journal.analytics import JournalAnalyticsService
    with _service() as service:
        return JournalAnalyticsService(service.store,journal=service).group(group_id,query=query,start_date=start_date,end_date=end_date,group_by=group_by)


@router.get("/portfolio-context")
def portfolio_context(profile_id: str = "default"):
    from shilun.trade_journal.analytics import PortfolioRiskService
    with _service() as service:
        return PortfolioRiskService(service.store,journal=service).context(profile_id=profile_id)


@router.post("/portfolio-context/preview")
def portfolio_scenario(payload: dict = Body(...)):
    from shilun.trade_journal.analytics import PortfolioRiskService
    with _service() as service:
        return PortfolioRiskService(service.store,journal=service).context(
            profile_id=payload.get("profile_id","default"),scenario=payload.get("scenario"))


@router.get("/portfolio-snapshots")
def portfolio_snapshots(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    from shilun.trade_journal.analytics import PortfolioRiskService
    with _service() as service:
        return PortfolioRiskService(service.store,journal=service).list_snapshots(limit=limit,offset=offset)


@router.get("/portfolio-snapshots/{snapshot_id}")
def portfolio_snapshot(snapshot_id: str, include_content: bool = False):
    from shilun.trade_journal.analytics import PortfolioRiskService
    with _service() as service:
        return PortfolioRiskService(service.store,journal=service).get_snapshot(snapshot_id,include_content=include_content)


@router.post("/portfolio-snapshots")
def capture_portfolio(payload: dict = Body(default={})):
    from shilun.trade_journal.analytics import PortfolioRiskService
    with _service(write=True) as service:
        return PortfolioRiskService(service.store,journal=service).capture_snapshot(payload)


@router.get("/tasks")
def journal_tasks(query: str = "", start_date: str | None = None, end_date: str | None = None,
                  status: str | None = None, episode_id: str | None = None,
                  limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    from shilun.trade_journal.tasks import JournalTaskService
    with _service() as service:
        return JournalTaskService(service.store,journal=service).list_tasks(query=query,
            start_date=start_date,end_date=end_date,status=status,episode_id=episode_id,limit=limit,offset=offset)


@router.get("/tasks/{task_id}")
def journal_task(task_id: str):
    from shilun.trade_journal.tasks import JournalTaskService
    with _service() as service:
        return JournalTaskService(service.store,journal=service).get_task(task_id)


@router.post("/tasks/{task_id}/resolutions")
def resolve_journal_task(task_id: str, payload: dict = Body(...)):
    from shilun.trade_journal.tasks import JournalTaskService
    with _service(write=True) as service:
        return JournalTaskService(service.store,journal=service).append_resolution(task_id,payload)


def _import_file_args(payload):
    filename, content = payload.get("filename"), payload.get("content_base64")
    if not isinstance(filename,str) or not isinstance(content,str) or not filename or not content:
        raise HTTPException(422,"请提供原始文件名与base64内容。")
    encoding = payload.get("encoding")
    if encoding is not None and not isinstance(encoding,str):
        raise HTTPException(422,"文件编码必须为文本标识。")
    return filename, content, encoding


@router.post("/import-files/inspect")
def inspect_import_file(payload: dict = Body(...)):
    from shilun.trade_journal.import_files import inspect_file
    filename, content, encoding = _import_file_args(payload)
    try:
        options = {key:payload[key] for key in ("sheet","row_start","row_limit") if key in payload}
        return inspect_file(filename,content,encoding=encoding,**options)
    except TradeJournalError as error:
        raise HTTPException(422,str(error)) from None


@router.post("/import-files/parse")
def parse_import_file(payload: dict = Body(...)):
    from shilun.trade_journal.import_files import parse_file
    filename, content, encoding = _import_file_args(payload)
    try:
        result = parse_file(filename,content,mapping=payload.get("mapping"),
            account_alias=payload.get("account_alias","primary"),encoding=encoding)
    except TradeJournalError as error:
        raise HTTPException(422,str(error)) from None
    if not result["can_preview"]:
        return {**result,"preview":None}
    if "identity_links" in payload:
        result["payload"]["identity_links"] = payload["identity_links"]
    with _service() as service:
        return {**result,"preview":service.preview_import(result["payload"])}


@router.get("/episodes/{episode_id}/execution-matches")
def execution_match_candidates(episode_id: str):
    from shilun.trade_journal.execution_matches import ExecutionMatchService
    with _service() as service:
        return ExecutionMatchService(service.store,journal=service).candidates(episode_id)


@router.post("/episodes/{episode_id}/execution-matches/preview")
def preview_execution_match(episode_id: str, payload: dict = Body(...)):
    from shilun.trade_journal.execution_matches import ExecutionMatchService
    if not isinstance(payload.get("execution_id"),str) or not payload["execution_id"]:
        raise HTTPException(422,"请明确选择生产execution_id。")
    with _service() as service:
        return ExecutionMatchService(service.store,journal=service).preview(episode_id,payload["execution_id"],payload.get("fill_ids"))


@router.post("/episodes/{episode_id}/execution-matches")
def save_execution_match(episode_id: str, payload: dict = Body(...)):
    from shilun.trade_journal.execution_matches import ExecutionMatchService
    with _service(write=True) as service:
        return ExecutionMatchService(service.store,journal=service).append_match(episode_id,payload)


@router.get("/execution-matches/{execution_id}")
def execution_match(execution_id: str):
    from shilun.trade_journal.execution_matches import ExecutionMatchService
    with _service() as service:
        return ExecutionMatchService(service.store,journal=service).get_match(execution_id)


@router.get("/episodes/{episode_id}/review")
def process_review(episode_id: str, revision: int | None = Query(None, ge=1)):
    from shilun.trade_journal.reviews import JournalReviewService
    with _service() as service:
        return JournalReviewService(service.store,journal=service).get_review(episode_id,revision=revision)


@router.get("/episodes/{episode_id}/review/history")
def process_review_history(episode_id: str):
    from shilun.trade_journal.reviews import JournalReviewService
    with _service() as service:
        return JournalReviewService(service.store,journal=service).history(episode_id)


@router.post("/episodes/{episode_id}/review")
def save_process_review(episode_id: str, payload: dict = Body(...)):
    from shilun.trade_journal.reviews import JournalReviewService
    with _service(write=True) as service:
        return JournalReviewService(service.store,journal=service).save_review(episode_id,payload)
