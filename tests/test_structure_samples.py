from __future__ import annotations

from datetime import datetime

import pandas as pd

from shilun.market.structure_samples import build_structure_sample, settle_structure_sample


def _bars(closes: list[float], *, volumes: list[float] | None = None, ticker: str = "000001.SZ") -> list[dict]:
    dates = pd.bdate_range("2026-01-02", periods=len(closes))
    volumes = volumes or [1_000_000.0] * len(closes)
    rows = []
    for index, (date, close, volume) in enumerate(zip(dates, closes, volumes)):
        previous = closes[index - 1] if index else close
        open_price = previous * 0.998
        rows.append({
            "ticker": ticker,
            "date": date.strftime("%Y-%m-%d"),
            "open": round(open_price, 4),
            "high": round(max(open_price, close) * 1.012, 4),
            "low": round(min(open_price, close) * 0.988, 4),
            "close": round(close, 4),
            "volume": volume,
            "amount": volume * close,
        })
    return rows


def _candidate(close: float, **overrides) -> dict:
    payload = {
        "ticker": "000001.SZ",
        "name": "样本股",
        "sector_name": "半导体",
        "pool_rank": 1,
        "candidate_source": "leader_top10",
        "close": close,
        "support_price": close * 0.94,
        "pressure_price": close * 1.12,
        "trade_timing_score": 72.0,
        "stock_quality_score": 80.0,
        "final_trade_score": 75.0,
        "buy_point_type": "ma5_pullback",
        "buy_point_pattern": "hui_cai",
        "allowed_new_position": True,
        "market_multiplier": 1.2,
        "sector_retreat_flag": False,
        "sector_mainline_score": 78.0,
        "session_return": 0.015,
        "minute_ranking_status": "eligible",
        "ranking_status": "eligible",
    }
    payload.update(overrides)
    return payload


def _sector() -> dict:
    return {
        "sector_name": "半导体",
        "stage": "main_uptrend",
        "stage_label": "主升",
        "metrics": {"relative_return_5d": 0.025},
    }


def test_uptrend_continuation_matches_template_a_without_changing_execution() -> None:
    closes = [10.0 + index * 0.06 for index in range(130)]
    closes[-5:] = [17.62, 17.69, 17.75, 17.71, 17.78]
    bars = _bars(closes)
    bars[-1].update({"open": 17.70, "high": 17.82, "low": 17.60, "close": 17.78})
    benchmark = _bars([3000 + index * 1.2 for index in range(130)], ticker="000001.SH")
    sample = build_structure_sample(
        analysis_date=bars[-1]["date"],
        candidate=_candidate(closes[-1]),
        bars=bars,
        benchmark_bars=benchmark,
        sector_context=_sector(),
        market_gate={"state": "attack", "gate_reason": "测试进攻"},
        cache_version="test",
        generated_at="2026-07-22T15:40:00+08:00",
    )

    templates = {item["template_id"]: item for item in sample["templates"]}
    assert templates["A"]["matched"] is True
    assert templates["A"]["status"] in {"waiting", "triggered"}
    assert sample["ranking_effect"] == "none"
    assert sample["execution_effect"] == "none"
    assert sample["source_coverage"]["chip_distribution_ready"] is False


def test_breakout_template_stays_observation_only_without_event_tracking() -> None:
    closes = [10.0 + index * 0.002 for index in range(105)]
    closes.extend([10.10, 10.08, 10.12, 10.09, 10.11, 10.10, 10.12, 10.11, 10.13, 10.55])
    volumes = [1_000_000.0] * (len(closes) - 1) + [2_000_000.0]
    bars = _bars(closes, volumes=volumes)
    benchmark = _bars([3000 + index * 0.3 for index in range(len(closes))], ticker="000001.SH")
    candidate = _candidate(
        closes[-1],
        buy_point_type="ma5_breakout",
        buy_point_pattern="tu_po",
        breakout_tracking=None,
        session_return=0.04,
    )
    sample = build_structure_sample(
        analysis_date=bars[-1]["date"],
        candidate=candidate,
        bars=bars,
        benchmark_bars=benchmark,
        sector_context=_sector(),
        market_gate={"state": "attack"},
        cache_version="test",
        generated_at="2026-07-22T15:40:00+08:00",
    )

    template_b = next(item for item in sample["templates"] if item["template_id"] == "B")
    assert template_b["matched"] is True
    assert template_b["observation_only"] is True
    assert template_b["trade_eligible_at_freeze"] is False


def test_repair_template_is_never_directly_trade_eligible() -> None:
    falling = [20.0 - index * 0.08 for index in range(105)]
    repair = [11.65, 11.55, 11.45, 11.38, 11.42, 11.48, 11.56, 11.64, 11.72, 11.82]
    closes = falling + repair
    bars = _bars(closes)
    benchmark = _bars([3000 - index * 1.0 for index in range(len(closes))], ticker="000001.SH")
    sample = build_structure_sample(
        analysis_date=bars[-1]["date"],
        candidate=_candidate(closes[-1], buy_point_type="watch", buy_point_pattern=None),
        bars=bars,
        benchmark_bars=benchmark,
        sector_context={**_sector(), "stage": "repair", "stage_label": "修复"},
        market_gate={"state": "defense"},
        cache_version="test",
        generated_at="2026-07-22T15:40:00+08:00",
    )

    template_c = next(item for item in sample["templates"] if item["template_id"] == "C")
    assert template_c["observation_only"] is True
    assert template_c["trade_eligible_at_freeze"] is False


def test_settlement_fills_available_horizons_and_keeps_future_pending() -> None:
    sample = {
        "analysis_date": "2026-07-20",
        "entry_price": 10.0,
        "feature_snapshot": {"benchmark_entry": 100.0},
        "templates": [
            {"template_id": "A", "invalidation_price": 9.5},
            {"template_id": "C", "invalidation_price": 9.0},
        ],
        "outcomes": {},
    }
    future = [
        {"date": "2026-07-21", "high": 10.5, "low": 9.7, "close": 10.2},
        {"date": "2026-07-22", "high": 10.8, "low": 9.4, "close": 10.6},
        {"date": "2026-07-23", "high": 11.0, "low": 10.2, "close": 10.9},
    ]
    benchmark = [
        {"date": "2026-07-21", "close": 101.0},
        {"date": "2026-07-22", "close": 102.0},
        {"date": "2026-07-23", "close": 103.0},
    ]
    settled = settle_structure_sample(
        sample,
        future_bars=future,
        benchmark_future_bars=benchmark,
        settled_at=datetime(2026, 7, 23, 16, 0).isoformat(),
    )

    assert settled["settled_horizons"] == [1, 3]
    assert settled["pending_horizons"] == [5, 10]
    assert settled["settlement_status"] == "partial"
    assert settled["outcomes"]["T3"]["return"] == 0.09
    assert settled["outcomes"]["T3"]["template_outcomes"]["A"]["invalidated"] is True
    assert settled["outcomes"]["T3"]["template_outcomes"]["C"]["invalidated"] is False
