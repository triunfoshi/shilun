"""Publication and dependency contracts of the cloud sector/MA5 service."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from shilun.jobs.heavy_job_lease import LeaseLost
from shilun.market import sector_service as service
from shilun.market.taxonomy import VERSION as TAXONOMY_VERSION

DAY = "2026-11-02"
BENCHMARK = "000001.SH"


@pytest.fixture
def publication(monkeypatch):
    previous = {
        "analysis_date": DAY, "generation": {"id": "previous"},
        "candidates": [{"ticker": "600001.SH"}],
        "daily_leaders": [{"date": DAY, "leaders": [{"ticker": "600001.SH"}]}],
        "leader_summary": {"trading_day_count": 20},
    }
    archive = {"payload": deepcopy(previous)}
    computed = {
        "analysis_date": DAY, "minute_sync": {"ranking_gate": "strict", "status": "success"},
        "top_sectors": [{"sector_name": "光通信", "scores": {"sector_score": 80}}],
        "all_sectors": [{"sector_name": "光通信", "member_tickers": ["300001.SZ"]}],
        "candidates": [{"ticker": "300001.SZ", "selection_gate": {"passed": True}}],
        "daily_leaders": [], "leader_summary": {},
    }
    store, config = MagicMock(), SimpleNamespace()
    prepare = MagicMock(return_value={"index_sync": [], "repaired": []})
    compute = MagicMock(side_effect=lambda **kwargs: deepcopy(computed))
    revisions = MagicMock(side_effect=lambda *args, **kwargs:
                          "full-after" if kwargs.get("include_minutes", True) else "daily-stable")
    load = MagicMock(side_effect=lambda *args, **kwargs: deepcopy(archive))
    order = []

    def publish(*args, **kwargs):
        order.append("publish")
        archive["payload"] = deepcopy(args[4])

    save = MagicMock(side_effect=publish)
    monkeypatch.setattr(service, "prepare_dependencies", prepare)
    monkeypatch.setattr(service, "_compute_sector_trends_with_candidate_minutes", compute)
    monkeypatch.setattr(service, "input_revision", revisions)
    monkeypatch.setattr(service, "_load_sector_cache", load)
    monkeypatch.setattr(service, "_save_sector_cache", save)
    lease = MagicMock(side_effect=lambda: order.append("lease"))
    return SimpleNamespace(store=store, config=config, archive=archive, previous=previous,
                           computed=computed, prepare=prepare, compute=compute,
                           revisions=revisions, load=load, save=save, lease=lease, order=order)


def test_publication_requires_owned_lease_and_keeps_previous_archive(publication):
    p = publication
    with pytest.raises(RuntimeError, match="lease"):
        service.run_precompute(p.store, p.config, DAY, BENCHMARK)
    p.save.assert_not_called()
    assert p.archive["payload"] == p.previous


def test_losing_lease_before_publication_keeps_previous_archive(publication):
    p = publication
    p.lease.side_effect = LeaseLost("ownership changed")
    with pytest.raises(LeaseLost):
        service.run_precompute(p.store, p.config, DAY, BENCHMARK, before_publish=p.lease)
    p.save.assert_not_called()
    assert p.archive["payload"] == p.previous


@pytest.mark.parametrize("invalid", ["date", "non_strict", "missing_gate"])
def test_wrong_date_or_non_strict_result_is_never_published(publication, invalid):
    p = publication
    if invalid == "date":
        p.computed["analysis_date"] = "2026-10-30"
    elif invalid == "non_strict":
        p.computed["minute_sync"]["ranking_gate"] = "collecting"
    else:
        p.computed.pop("minute_sync")
    with pytest.raises(ValueError):
        service.run_precompute(p.store, p.config, DAY, BENCHMARK, before_publish=p.lease)
    p.save.assert_not_called()
    p.lease.assert_not_called()
    assert p.archive["payload"] == p.previous


def test_sector_and_ma5_publish_together_preserving_daily_leaders(publication):
    p = publication
    result = service.run_precompute(p.store, p.config, DAY, BENCHMARK, before_publish=p.lease)
    assert p.order == ["lease", "publish"]
    p.save.assert_called_once()
    published = p.archive["payload"]
    assert published["top_sectors"] == p.computed["top_sectors"]
    assert published["candidates"] == p.computed["candidates"]
    assert published["daily_leaders"] == p.previous["daily_leaders"]
    assert published["leader_summary"] == p.previous["leader_summary"]
    assert result["generation"]["id"] == published["generation"]["id"]
    assert published["generation"]["input_revision"] == "full-after"
    assert result["published_input_revision"] == "full-after"
    assert p.prepare.call_args.args == (p.store, DAY, BENCHMARK)
    assert p.compute.call_args.kwargs["target_date"] == DAY
    assert p.compute.call_args.kwargs["bm_ticker"] == BENCHMARK
    assert p.save.call_args.args[1:4] == (DAY, BENCHMARK, 60)


def test_requested_daily_leaders_replace_previous_ones(publication):
    p = publication
    p.computed["daily_leaders"] = [{"date": DAY, "leaders": [{"ticker": "300001.SZ"}]}]
    service.run_precompute(p.store, p.config, DAY, BENCHMARK,
                           before_publish=p.lease, include_daily_leaders=True)
    assert p.archive["payload"]["daily_leaders"] == p.computed["daily_leaders"]


def test_daily_revision_change_during_calculation_prevents_publication(publication):
    p = publication
    daily = iter(("daily-before", "daily-after"))
    p.revisions.side_effect = lambda *args, **kwargs: (
        "full-after" if kwargs.get("include_minutes", True) else next(daily))
    with pytest.raises((ValueError, RuntimeError)):
        service.run_precompute(p.store, p.config, DAY, BENCHMARK, before_publish=p.lease)
    p.save.assert_not_called()
    assert p.archive["payload"] == p.previous


def test_own_minute_repairs_may_change_full_revision_but_daily_must_be_stable(publication):
    p = publication
    result = service.run_precompute(p.store, p.config, DAY, BENCHMARK, before_publish=p.lease)
    daily_calls = [call for call in p.revisions.call_args_list
                   if call.kwargs.get("include_minutes") is False]
    assert len(daily_calls) == 2
    assert result["generation"]["input_revision"] == "full-after"
    p.save.assert_called_once()


def test_compute_only_never_publishes_without_a_lease(publication):
    p = publication
    service.run_precompute(p.store, p.config, DAY, BENCHMARK, publish=False)
    p.save.assert_not_called()
    assert p.archive["payload"] == p.previous


@pytest.mark.parametrize("top_n", [8, 3])
def test_cache_keys_isolate_top_n(top_n):
    store = MagicMock()
    store.collection.return_value.find_one.return_value = {
        "payload": {"membership_version": TAXONOMY_VERSION}}
    payload = {"membership_version": TAXONOMY_VERSION, "analysis_date": DAY}
    service._save_sector_cache(store, DAY, BENCHMARK, 60, payload, 1.5, top_n=top_n)
    saved_key = store.collection.return_value.update_one.call_args.args[0]
    service._load_sector_cache(store, DAY, BENCHMARK, 60, top_n=top_n)
    loaded_key = store.collection.return_value.find_one.call_args.args[0]
    assert saved_key == loaded_key
    assert saved_key["analysis_date"] == DAY
    assert saved_key["benchmark_ticker"] == BENCHMARK
    assert saved_key["top_n"] == top_n


def test_default_top_n_can_read_legacy_cache_without_top_n_field():
    store = MagicMock()
    previous = {"payload": {"membership_version": TAXONOMY_VERSION}}
    store.collection.return_value.find_one.side_effect = [None, previous]
    assert service._load_sector_cache(store, DAY, BENCHMARK, 60) == previous
    queries = [call.args[0] for call in store.collection.return_value.find_one.call_args_list]
    assert queries[0]["top_n"] == 8
    assert queries[1]["top_n"] == {"$exists": False}


def test_other_top_n_never_falls_back_to_legacy_default_cache():
    store = MagicMock()
    store.collection.return_value.find_one.return_value = None
    assert service._load_sector_cache(store, DAY, BENCHMARK, 60, top_n=3) is None
    store.collection.return_value.find_one.assert_called_once()


@pytest.mark.parametrize("cache_generation, cache_revision, expected", [
    ("current", "revision", True), ("superseded", "revision", False),
    (None, "revision", False),
])
def test_completed_task_can_only_reuse_its_current_published_generation(
        cache_generation, cache_revision, expected):
    task = {"analysis_date": DAY, "benchmark_ticker": BENCHMARK,
            "trend_window_days": 60, "top_n": 3, "published_input_revision": "revision",
            "result": {"generation": {"id": "current", "input_revision": "revision"}}}
    payload = {"analysis_date": DAY, "generation": {
        "id": cache_generation, "input_revision": cache_revision}}
    store = MagicMock()
    store.collection.return_value.find_one.return_value = {"payload": payload}
    assert service.published_result_matches(store, task) is expected
    key = store.collection.return_value.find_one.call_args.args[0]
    assert key["analysis_date"] == DAY
    assert key["benchmark_ticker"] == BENCHMARK
    assert key["trend_lookback_days"] == 60
    assert key["top_n"] == 3


def test_input_revision_ignores_own_minute_changes_only_when_requested(monkeypatch):
    from shilun.market.minute_bars_store import COLLECTION_NAME
    from shilun.market.taxonomy import SNAPSHOTS
    receipts = [{"stage": "dependency:daily_bars", "output_revision": "daily-1"}]
    minutes = [{"ticker": "300001.SZ", "fetched_at": "first", "source": "klineshare"}]
    collections = {}
    for name in ("data_pipeline_tasks", "sync_state", SNAPSHOTS, COLLECTION_NAME, "breakout_events"):
        collections[name] = MagicMock()
        collections[name].find.return_value = []
    collections["data_pipeline_tasks"].find.side_effect = lambda *args: deepcopy(receipts)
    collections[COLLECTION_NAME].find.side_effect = lambda *args: deepcopy(minutes)
    collections[SNAPSHOTS].find_one.return_value = {"revision": "taxonomy-1"}
    store = MagicMock()
    store.collection.side_effect = lambda name: collections[name]
    monkeypatch.setattr(service, "_load_market_gate", lambda *args: None)
    daily_before = service.input_revision(store, DAY, BENCHMARK, include_minutes=False)
    full_before = service.input_revision(store, DAY, BENCHMARK)
    minutes[0]["fetched_at"] = "repaired"
    assert service.input_revision(store, DAY, BENCHMARK, include_minutes=False) == daily_before
    assert service.input_revision(store, DAY, BENCHMARK) != full_before
    receipts[0]["output_revision"] = "daily-2"
    assert service.input_revision(store, DAY, BENCHMARK, include_minutes=False) != daily_before
    assert collections["data_pipeline_tasks"].find.call_args.args[0]["analysis_date"] == DAY
    assert collections[COLLECTION_NAME].find.call_args.args[0]["trade_date"] == DAY.replace("-", "")


@pytest.fixture
def prerequisites(monkeypatch):
    from shilun.review import index_sync
    from shilun.market import taxonomy
    from shilun.jobs import pipeline_dependencies
    store = MagicMock()
    days = pd.bdate_range(end=DAY, periods=40).strftime("%Y%m%d")
    store.find_trade_calendar.return_value = [{"cal_date": day, "is_open": 1} for day in days]
    collections = {name: MagicMock() for name in ("market_daily_bars", "daily_basic", "moneyflow")}
    for collection in collections.values():
        collection.find_one.return_value = {"_id": "same-day"}
    store.collection.side_effect = lambda name: collections[name]
    sync = MagicMock(return_value=[])
    load = MagicMock(return_value={"date": DAY, "revision": "taxonomy-ready"})
    repair_taxonomy = MagicMock(return_value=None)
    repair = MagicMock(return_value={"status": "ready"})
    monkeypatch.setattr(index_sync, "sync_index_history", sync)
    monkeypatch.setattr(taxonomy, "load_taxonomy", load)
    monkeypatch.setattr(taxonomy, "sync_taxonomy", repair_taxonomy)
    monkeypatch.setattr(pipeline_dependencies, "prepare_dependency", repair)
    return SimpleNamespace(store=store, collections=collections, sync=sync, taxonomy=load,
                           repair_taxonomy=repair_taxonomy, repair=repair)


def test_ready_dependencies_keep_requested_day_and_do_not_start_broad_sync(prerequisites):
    p = prerequisites
    service.prepare_dependencies(p.store, DAY, BENCHMARK)
    assert p.store.find_trade_calendar.call_args.kwargs["end_date"] == DAY
    p.sync.assert_called_once_with(p.store, DAY, (BENCHMARK,))
    p.taxonomy.assert_called_once_with(p.store, DAY)
    p.repair.assert_not_called()
    p.repair_taxonomy.assert_not_called()
    for name, field in (("market_daily_bars", "date"), ("daily_basic", "trade_date"), ("moneyflow", "trade_date")):
        query = p.collections[name].find_one.call_args.args[0]
        assert set(query[field]["$in"]) == {DAY, DAY.replace("-", "")}


@pytest.mark.parametrize("key, collection", [
    ("daily_bars", "market_daily_bars"), ("daily_basic", "daily_basic"), ("moneyflow", "moneyflow")])
def test_missing_exact_day_source_is_repaired_once_and_rechecked(prerequisites, key, collection):
    p = prerequisites
    p.collections[collection].find_one.side_effect = [None, {"_id": "repaired"}]
    result = service.prepare_dependencies(p.store, DAY, BENCHMARK)
    p.repair.assert_called_once_with(p.store, DAY, key)
    assert result["repaired"] == [{"key": key, "status": "ready"}]


def test_failed_exact_day_repair_blocks_analysis(prerequisites):
    p = prerequisites
    p.collections["moneyflow"].find_one.return_value = None
    with pytest.raises(ValueError, match="同日"):
        service.prepare_dependencies(p.store, DAY, BENCHMARK)
    p.repair.assert_called_once_with(p.store, DAY, "moneyflow")


def test_previous_trading_day_does_not_substitute_requested_calendar_date(prerequisites):
    p = prerequisites
    p.store.find_trade_calendar.return_value.pop()
    with pytest.raises(ValueError, match="24交易日"):
        service.prepare_dependencies(p.store, DAY, BENCHMARK)
    p.sync.assert_not_called()
    p.repair.assert_not_called()


def test_wrong_day_taxonomy_is_not_accepted(prerequisites):
    p = prerequisites
    p.taxonomy.return_value = {"date": "2026-10-30", "revision": "old"}
    with pytest.raises(ValueError, match="同日分类"):
        service.prepare_dependencies(p.store, DAY, BENCHMARK)


def test_unavailable_exact_day_index_blocks_analysis(prerequisites):
    p = prerequisites
    p.sync.return_value = [{"ticker": BENCHMARK, "date": DAY, "status": "unavailable"}]
    with pytest.raises(ValueError, match="基准指数"):
        service.prepare_dependencies(p.store, DAY, BENCHMARK)


@pytest.fixture
def loaded_facts(monkeypatch):
    from shilun.market import taxonomy
    days = pd.bdate_range(end=DAY, periods=40).strftime("%Y-%m-%d").tolist()
    basics = [{"ts_code": f"60000{i}.SH", "name": f"测试{i}", "industry": "测试行业",
               "list_date": "20200101"} for i in range(10)]
    # Neither not-yet-listed, delisted nor ST names belong in the current
    # non-ST price-coverage denominator.
    basics.extend([
        {"ts_code": "600090.SH", "name": "未来上市", "list_date": "20261103", "industry": "测试行业"},
        {"ts_code": "600091.SH", "name": "已退市", "delist_date": "20261001", "industry": "测试行业"},
        {"ts_code": "600092.SH", "name": "*ST测试", "list_date": "20200101", "industry": "测试行业"},
    ])
    bars = [{"ticker": basic["ts_code"], "date": day, "open": 10., "close": 10.,
             "high": 10.1, "low": 9.9, "amount": 1_000_000., "volume": 100_000.}
            for basic in basics[:10] for day in days]
    daily = [{"ts_code": basic["ts_code"], "trade_date": DAY.replace("-", ""),
              "total_mv": 2_000_000., "turnover_rate": 5.} for basic in basics[:10]]
    flows = [{"ts_code": basic["ts_code"], "trade_date": DAY.replace("-", ""),
              "net_mf_amount": 100.} for basic in basics[:10]]
    benchmark = [{"ticker": BENCHMARK, "date": day, "open": 100., "close": 101.,
                  "high": 102., "low": 99., "amount": 100_000.} for day in days]
    store = MagicMock()
    store.raw_market.find_stock_basic.side_effect = lambda: deepcopy(basics)
    store.raw_market.find_daily_bars.side_effect = lambda **kwargs: deepcopy(
        benchmark if kwargs.get("tickers") else bars)
    store.raw_market.find_daily_basic.side_effect = lambda **kwargs: deepcopy(daily)
    store.raw_market.find_moneyflow.side_effect = lambda **kwargs: deepcopy(flows)
    monkeypatch.setattr(taxonomy, "classify_stocks", lambda store, day, rows, **kwargs: rows)
    monkeypatch.setattr(taxonomy, "load_taxonomy", lambda *args: {"date": DAY, "revision": "same-day"})
    monkeypatch.setattr(service, "_load_market_gate", lambda *args: None)
    monkeypatch.setattr(service, "_load_breakout_events_lookup", lambda *args: {})
    monkeypatch.setattr(service, "_load_minute_bars_from_mongo", lambda *args: ({}, None))
    evaluate = MagicMock(return_value={"analysis_date": DAY, "candidates": [], "top_sectors": []})
    monkeypatch.setattr(service, "evaluate_sector_trends", evaluate)
    return SimpleNamespace(store=store, bars=bars, basics=basics, daily=daily,
                           flows=flows, benchmark=benchmark, evaluate=evaluate)


def _compute_loaded(facts):
    return service._compute_sector_trends_full(facts.store, DAY, BENCHMARK, 40, 60, 8)


@pytest.mark.parametrize("current_count", [1, 7])
def test_single_row_or_partial_batch_cannot_mark_daily_prices_ready(loaded_facts, current_count):
    p = loaded_facts
    current = {basic["ts_code"] for basic in p.basics[:current_count]}
    p.bars[:] = [row for row in p.bars if row["date"] != DAY or row["ticker"] in current]
    with pytest.raises(ValueError):
        _compute_loaded(p)
    p.evaluate.assert_not_called()


def test_eighty_percent_of_current_non_st_universe_can_be_evaluated(loaded_facts):
    p = loaded_facts
    current = {basic["ts_code"] for basic in p.basics[:8]}
    p.bars[:] = [row for row in p.bars if row["date"] != DAY or row["ticker"] in current]
    result = _compute_loaded(p)
    assert result["analysis_date"] == DAY
    p.evaluate.assert_called_once()
    received = p.evaluate.call_args.kwargs["market_bars"]
    assert len(received.loc[received["date"] == DAY]) == 8


def test_partial_basic_and_flow_remain_partial_instead_of_filling_missing_values(loaded_facts):
    p = loaded_facts
    p.daily[:] = p.daily[:4]
    p.flows[:] = p.flows[:3]
    result = _compute_loaded(p)
    assert result["analysis_date"] == DAY
    received = p.evaluate.call_args.kwargs
    assert len(received["daily_basic"]) == 4
    assert len(received["moneyflow"]) == 3


@pytest.mark.parametrize("invalid_close", [0., None, float("nan"), float("inf")])
def test_invalid_closes_do_not_count_as_current_daily_coverage(loaded_facts, invalid_close):
    p = loaded_facts
    invalid = {basic["ts_code"] for basic in p.basics[7:10]}
    for row in p.bars:
        if row["date"] == DAY and row["ticker"] in invalid:
            row["close"] = invalid_close
    with pytest.raises(ValueError):
        _compute_loaded(p)
    p.evaluate.assert_not_called()


@pytest.mark.parametrize("problem", ["missing", "invalid"])
def test_current_benchmark_is_required_even_with_complete_stock_prices(loaded_facts, problem):
    p = loaded_facts
    if problem == "missing":
        p.benchmark[:] = [row for row in p.benchmark if row["date"] != DAY]
    else:
        p.benchmark[-1]["close"] = 0.
    with pytest.raises(ValueError):
        _compute_loaded(p)
    p.evaluate.assert_not_called()


def test_short_global_history_cannot_be_evaluated(loaded_facts):
    p = loaded_facts
    first = sorted({row["date"] for row in p.bars})[-23]
    p.bars[:] = [row for row in p.bars if row["date"] >= first]
    p.benchmark[:] = [row for row in p.benchmark if row["date"] >= first]
    with pytest.raises(ValueError):
        _compute_loaded(p)
    p.evaluate.assert_not_called()


def test_absent_stock_universe_cannot_be_evaluated(loaded_facts):
    p = loaded_facts
    p.basics.clear()
    with pytest.raises(ValueError):
        _compute_loaded(p)
    p.evaluate.assert_not_called()
