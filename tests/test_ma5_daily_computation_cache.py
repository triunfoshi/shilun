"""Daily reuse must preserve strict eligibility and recompute minute evidence."""
from copy import deepcopy
import json
from unittest.mock import patch

import pandas as pd
import pytest

from shilun.market import ma5_universe, sector


def _facts():
    days = pd.bdate_range(end="2026-03-25", periods=60).strftime("%Y-%m-%d").tolist()
    bars = []
    for offset, day in enumerate(days):
        close = (10 + offset * .05) * (1.2 if offset >= 56 else 1)
        bars.append({"ticker": "300001.SZ", "date": day, "open": close * .99,
                     "high": close * 1.01, "low": close * .98, "close": close,
                     "amount": 1_000_000., "volume": 1_000_000. / close})
    basics = [{"ts_code": "300001.SZ", "name": "测试股份", "industry": "光通信"}]
    daily = [{"ts_code": "300001.SZ", "trade_date": days[-1], "total_mv": 2_000_000.,
              "circ_mv": 1_500_000., "turnover_rate": 5.}]
    return basics, bars, daily, days[-24:]


def _sector_args():
    basics, bars, daily, days = _facts()
    frame = pd.DataFrame(bars)
    return dict(analysis_date=days[-1], market_bars=frame, stock_basic=pd.DataFrame(basics),
                daily_basic=pd.DataFrame(daily), benchmark_bars=frame.assign(ticker="000001.SH"),
                min_stock_count=1, include_daily_leaders=False)


def _minutes(day):
    return [{"datetime": f"{day}T{time}:00", "open": 10., "close": 10.1, "volume": 100.}
            for time in ("09:35", "10:30", "13:05", "13:35", "14:35")]


def _same_result(actual, expected):
    assert json.dumps(actual, sort_keys=True, default=str) == json.dumps(expected, sort_keys=True, default=str)


def test_scan_reuses_equal_contents_and_isolates_returned_audits():
    facts = _facts()
    cache = {}
    with patch.object(ma5_universe, "_scan_universe", wraps=ma5_universe._scan_universe) as scan:
        first = ma5_universe.scan_universe(*facts, daily_computation_cache=cache)
        baseline = deepcopy(first)
        first["audits"][0]["selection_gate"]["criteria"][0]["passed"] = False
        second = ma5_universe.scan_universe(*deepcopy(facts), daily_computation_cache=cache)
        _same_result(second, baseline)
        assert scan.call_count == 1
    assert second["audits"][0]["passed"]


@pytest.mark.parametrize("change", ["close", "amount", "cap", "name", "industry", "days", "missing", "duplicate"])
def test_changed_daily_facts_never_reuse_previous_scan(change):
    facts = _facts()
    changed = deepcopy(facts)
    basics, bars, daily, days = changed
    if change in ("close", "amount"):
        bars[-1][change] = 0.
    elif change == "cap":
        daily[-1]["total_mv"] = 0.
    elif change == "name":
        basics[0]["name"] = "*ST测试"
    elif change == "industry":
        basics[0]["industry"] = "另一分类"
    elif change == "days":
        days[0] = "2025-01-01"
    elif change == "missing":
        del bars[-24]
    else:
        bars.append(deepcopy(bars[-1]))
    cache = {}
    with patch.object(ma5_universe, "_scan_universe", wraps=ma5_universe._scan_universe) as scan:
        ma5_universe.scan_universe(*facts, daily_computation_cache=cache)
        if change == "duplicate":
            with pytest.raises(ValueError, match="Duplicate"):
                ma5_universe.scan_universe(*changed, daily_computation_cache=cache)
        else:
            actual = ma5_universe.scan_universe(*changed, daily_computation_cache=cache)
            _same_result(actual, ma5_universe.scan_universe(*changed))
        assert scan.call_count == (2 if change == "duplicate" else 3)


def test_cache_is_optional_and_not_shared_between_generations():
    facts = _facts()
    with patch.object(ma5_universe, "_scan_universe", wraps=ma5_universe._scan_universe) as scan:
        for cache in (None, None, {}, {}):
            ma5_universe.scan_universe(*facts, daily_computation_cache=cache)
        assert scan.call_count == 4


def test_frame_signature_preserves_object_types_and_nested_metadata():
    cache = {}
    first = pd.DataFrame({"amount": [1], "meta": [{"source": "one"}]}, dtype=object)
    changed = pd.DataFrame({"amount": ["1"], "meta": [{"source": "one"}]}, dtype=object)
    changed_again = changed.copy(deep=True)
    changed_again.at[0, "meta"] = {"source": "two"}
    compute = lambda: "first"
    assert ma5_universe.reuse_daily_computation(cache, "frame", (first,), compute) == "first"
    assert ma5_universe.reuse_daily_computation(cache, "frame", (changed,), lambda: "second") == "second"
    assert ma5_universe.reuse_daily_computation(cache, "frame", (changed_again,), lambda: "third") == "third"


def test_sector_two_phases_reuse_daily_work_and_refresh_strict_minute_evidence():
    args = _sector_args()
    cache = {}
    minutes = _minutes(args["analysis_date"])
    strict = dict(require_minute_data_for_ranking=True,
                  stock_minute_bars_lookup={"300001.SZ": minutes}, benchmark_minute_bars=minutes)
    with patch.object(sector, "_prepare_stock_frame", wraps=sector._prepare_stock_frame) as prepare, \
            patch.object(ma5_universe, "_scan_universe", wraps=ma5_universe._scan_universe) as scan:
        initial = sector.evaluate_sector_trends(**args, daily_computation_cache=cache)
        final = sector.evaluate_sector_trends(**deepcopy(args), **strict, daily_computation_cache=cache)
        assert scan.call_count == 1
        # Sector features and MA5 candidate features retain different preparation
        # semantics, but neither is calculated twice across the two phases.
        assert prepare.call_count == 2
    _same_result(final, sector.evaluate_sector_trends(**args, **strict))
    assert initial["selection_minute_coverage"]["pending_tickers"] == ["300001.SZ"]
    assert final["selection_minute_coverage"]["ready_count"] == 1
    assert not final["selection_minute_coverage"]["pending_tickers"]
    assert final["candidates"][0]["selection_minute_confirmation"]["has_data"]
    assert final["top_sectors"][0]["leader_candidates"]


def test_changed_moneyflow_refreshes_features_without_rescanning_daily_gate():
    args = _sector_args()
    cache = {}
    args["moneyflow"] = pd.DataFrame([{
        "ts_code": "300001.SZ", "trade_date": args["analysis_date"], "net_mf_amount": 100.,
        "buy_lg_amount": 100., "sell_lg_amount": 10., "buy_elg_amount": 100., "sell_elg_amount": 10.,
    }])
    with patch.object(sector, "_prepare_stock_frame", wraps=sector._prepare_stock_frame) as prepare, \
            patch.object(ma5_universe, "_scan_universe", wraps=ma5_universe._scan_universe) as scan:
        sector.evaluate_sector_trends(**args, daily_computation_cache=cache)
        args["moneyflow"].at[0, "net_mf_amount"] = -100.
        changed = sector.evaluate_sector_trends(**args, daily_computation_cache=cache)
        assert scan.call_count == 1
        assert prepare.call_count == 4
    _same_result(changed, sector.evaluate_sector_trends(**args))
