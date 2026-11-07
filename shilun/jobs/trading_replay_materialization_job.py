"""Job 12J -> 12H：把盘中生产快照物化为正式全场景回放。

任务只消费已落库的当日冻结计划、全场景事实快照和个股/双指数
5 分钟 K。它不请求行情，不用收盘后数据倒推盘中状态，也不改变
交易阈值。重复执行按 ``session_id`` 幂等更新。
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime
from hashlib import sha256
import json
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.trading.contracts import PositionState
from shilun.trading.playbooks.execution_scenarios import (
    ClosedFiveMinuteBar,
    E7Phase,
    E7ThreeDayFacts,
    IntradayMarketState,
    MarketPermission,
    ScenarioExecutionPlan,
    ScenarioMarketFacts,
    SectorExecutionState,
)
from shilun.trading.scenario_replay import (
    ScenarioReplayError,
    ScenarioReplayFactFrame,
    replay_persisted_scenario_session,
)


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
PLAN_COLLECTION = "trading_watch_plans"
FACT_COLLECTION = "execution_scenario_fact_snapshots"
MINUTE_COLLECTION = "minute_bars_daily"
REPLAY_COLLECTION = "execution_scenario_replays"
REPORT_COLLECTION = "trading_replay_materialization_reports"
PLAYBOOK_ID = "execution_scenarios_v0_3"
PLAYBOOK_VERSION = "0.3.0"
SSE_TICKER = "000001.SH"
CHINEXT_TICKER = "399006.SZ"


def _documents(
    collection: Any,
    query: dict[str, Any] | None = None,
    projection: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    try:
        cursor = collection.find(query or {}, projection)
    except TypeError:
        cursor = collection.find(query or {})
    return [dict(item) for item in cursor]


def _date_text(value: Any) -> str:
    text = str(value or "").strip().replace("-", "")
    return text[:8] if len(text) >= 8 else text


def _aware(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise ScenarioReplayError(f"无效 {field_name}：{value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed.astimezone(MARKET_TIMEZONE)


def _day(value: Any, *, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as error:
        raise ScenarioReplayError(f"无效 {field_name}：{value!r}") from error


def _bar(payload: dict[str, Any]) -> ClosedFiveMinuteBar:
    return ClosedFiveMinuteBar(
        closed_at=_aware(
            payload.get("closed_at") or payload.get("datetime"),
            field_name="closed_bar.closed_at",
        ),
        open=float(payload["open"]),
        high=float(payload["high"]),
        low=float(payload["low"]),
        close=float(payload["close"]),
        volume_ratio=(
            None
            if payload.get("volume_ratio") in (None, "")
            else float(payload["volume_ratio"])
        ),
    )


def _e7(payload: dict[str, Any] | None) -> E7ThreeDayFacts | None:
    if not payload:
        return None
    return E7ThreeDayFacts(
        phase=E7Phase(str(payload["phase"])),
        t_minus_2_date=_day(payload["t_minus_2_date"], field_name="e7.t_minus_2_date"),
        t_minus_1_date=_day(payload["t_minus_1_date"], field_name="e7.t_minus_1_date"),
        exit_date=_day(payload["exit_date"], field_name="e7.exit_date"),
        market_oversold_strength=float(payload.get("market_oversold_strength") or 0),
        sector_oversold_strength=float(payload.get("sector_oversold_strength") or 0),
        stock_oversold_strength=float(payload.get("stock_oversold_strength") or 0),
        t_minus_2_low=float(payload.get("t_minus_2_low") or 0),
        t_minus_1_low=float(payload.get("t_minus_1_low") or 0),
        sector_up_ratio_history=tuple(
            float(value) for value in (payload.get("sector_up_ratio_history") or [])
        ),
        sector_new_low_ratio=float(payload.get("sector_new_low_ratio", 1)),
        stock_bars_after_1400=tuple(
            _bar(item) for item in (payload.get("stock_bars_after_1400") or [])
        ),
        sse_three_bars_no_new_low=bool(payload.get("sse_three_bars_no_new_low")),
        chinext_three_bars_no_new_low=bool(payload.get("chinext_three_bars_no_new_low")),
        sse_accelerating_breakdown=bool(payload.get("sse_accelerating_breakdown")),
        chinext_accelerating_breakdown=bool(payload.get("chinext_accelerating_breakdown")),
        limit_down_locked=bool(payload.get("limit_down_locked")),
        suspended=bool(payload.get("suspended")),
        another_e7_position_open=bool(payload.get("another_e7_position_open")),
    )


def _plan(payload: dict[str, Any]) -> ScenarioExecutionPlan:
    sellable = payload.get("sellable_quantity")
    return ScenarioExecutionPlan(
        ticker=str(payload.get("ticker") or "").upper(),
        position_state=PositionState(str(payload.get("position_state") or "flat")),
        previous_close=float(payload["previous_close"]),
        open_price=float(payload["open_price"]),
        atr=float(payload["atr"]),
        support=float(payload["support"]),
        ma5=float(payload["ma5"]),
        ma8=float(payload["ma8"]),
        pressure=float(payload["pressure"]),
        first_target=float(payload["first_target"]),
        soft_risk=float(payload["soft_risk"]),
        hard_stop=float(payload["hard_stop"]),
        max_acceptable_price=float(payload["max_acceptable_price"]),
        entry_zone=tuple(float(value) for value in payload["entry_zone"]),
        current_quantity=int(payload.get("current_quantity") or 0),
        sellable_quantity=None if sellable is None else int(sellable),
        lot_size=int(payload.get("lot_size") or 100),
        missed_locked=bool(payload.get("missed_locked")),
    )


def _facts(document: dict[str, Any]) -> ScenarioReplayFactFrame:
    payload = dict(document.get("facts") or {})
    failed_sources = (
        payload.get("freshness_failed_sources")
        or document.get("freshness_failed_sources")
        or []
    )
    source_values = payload.get("source_timestamps") or document.get("source_timestamps") or []
    facts = ScenarioMarketFacts(
        computed_at=_aware(
            payload.get("computed_at") or document.get("computed_at"),
            field_name="facts.computed_at",
        ),
        current_price=float(payload["current_price"]),
        day_low=float(payload["day_low"]),
        vwap=float(payload["vwap"]),
        closed_bars=tuple(_bar(item) for item in (payload.get("closed_bars") or [])),
        volume_ratio=(
            None if payload.get("volume_ratio") in (None, "")
            else float(payload["volume_ratio"])
        ),
        relative_strength=(
            None if payload.get("relative_strength") in (None, "")
            else float(payload["relative_strength"])
        ),
        market_permission=MarketPermission(str(payload.get("market_permission") or "empty")),
        intraday_market_state=IntradayMarketState(
            str(payload.get("intraday_market_state") or "intraday_range")
        ),
        sector_state=SectorExecutionState(str(payload.get("sector_state") or "unavailable")),
        required_data_fresh=bool(payload.get("required_data_fresh")),
        freshness_failed_sources=tuple(str(item) for item in failed_sources),
        quote_age_seconds=float(payload.get("quote_age_seconds") or 0),
        price_limit_locked=bool(payload.get("price_limit_locked")),
        suspended=bool(payload.get("suspended")),
        e7=_e7(payload.get("e7")),
        e7_support_deteriorating=bool(payload.get("e7_support_deteriorating")),
        cancelled_scenarios=frozenset(
            str(item) for item in (payload.get("cancelled_scenarios") or [])
        ),
        source_timestamps=tuple(
            _aware(item, field_name="facts.source_timestamps") for item in source_values
        ),
    )
    return ScenarioReplayFactFrame(
        fact_id=str(document.get("fact_id") or ""),
        facts=facts,
    )


def _minute_key(document: dict[str, Any]) -> tuple[str, str]:
    return (
        str(document.get("ticker") or "").upper(),
        _date_text(document.get("trade_date")),
    )


def _index_metadata(document: dict[str, Any] | None) -> tuple[str | None, float | None]:
    bars = list((document or {}).get("bars") or [])
    if not bars:
        return None, None
    try:
        first = float(bars[0].get("open") or bars[0]["close"])
        last = float(bars[-1]["close"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None, None
    change = last / first - 1 if first > 0 else None
    if change is None:
        return None, None
    return ("strong" if change >= 0 else "weak"), change


def _coverage_metadata(
    *,
    plan_document: dict[str, Any],
    plan: ScenarioExecutionPlan,
    frames: list[ScenarioReplayFactFrame],
    stock_minutes: dict[str, Any],
    sse_minutes: dict[str, Any],
    chinext_minutes: dict[str, Any],
) -> dict[str, Any]:
    sse_state, sse_change = _index_metadata(sse_minutes)
    chinext_state, chinext_change = _index_metadata(chinext_minutes)
    relationship = None
    if sse_change is not None and chinext_change is not None:
        relationship = (
            "divergence"
            if (sse_change >= 0) != (chinext_change >= 0) or abs(sse_change - chinext_change) >= 0.01
            else "aligned"
        )
    latest_facts = frames[-1].facts
    sector_map = {
        SectorExecutionState.RECOVERY: "mainline",
        SectorExecutionState.DIVERGENCE: "divergence",
        SectorExecutionState.WEAK: "retreat",
        SectorExecutionState.UNAVAILABLE: "retreat",
    }
    open_change = plan.open_price / plan.previous_close - 1
    open_type = "high" if open_change >= 0.01 else "low" if open_change <= -0.01 else "flat"
    amounts = []
    for item in stock_minutes.get("bars") or []:
        try:
            amounts.append(max(0.0, float(item.get("amount") or 0)))
        except (TypeError, ValueError):
            continue
    return {
        "sample_mode": "live_production_snapshot",
        "trade_date": str(plan_document.get("trade_date") or ""),
        "sector_name": plan_document.get("sector_name"),
        "sse_state": sse_state,
        "chinext_state": chinext_state,
        "index_relationship": relationship,
        "sector_state": sector_map[latest_facts.sector_state],
        "open_type": open_type,
        "price_limit_context": (
            "near_limit" if any(item.facts.price_limit_locked for item in frames) else "ordinary"
        ),
        "liquidity": "low" if sum(amounts) < 100_000_000 else "normal",
        "data_state": (
            "normal" if all(item.facts.required_data_fresh for item in frames) else "interrupted"
        ),
    }


def _audit_reasons(
    *,
    plan_document: dict[str, Any],
    frames: list[ScenarioReplayFactFrame],
    minute_documents: Iterable[dict[str, Any] | None],
    parse_error_count: int,
) -> list[str]:
    reasons: list[str] = []
    if not plan_document.get("frozen_at"):
        reasons.append("计划缺少 frozen_at，无法证明盘中未被覆盖")
    if any(not item for item in minute_documents):
        reasons.append("缺少个股或双指数同日 5 分钟 K")
    if parse_error_count:
        reasons.append(f"有 {parse_error_count} 条事实快照无法还原")
    for item in frames:
        facts = item.facts
        if not facts.source_timestamps:
            reasons.append("事实快照缺少显式来源时间")
            break
        if facts.required_data_fresh and len(facts.source_timestamps) < 5:
            reasons.append("新鲜事实的来源时间覆盖不完整")
            break
        if not facts.required_data_fresh and not facts.freshness_failed_sources:
            reasons.append("数据中断帧未记录具体阻断来源")
            break
    return list(dict.fromkeys(reasons))


def build_replay_materialization_report(
    store: Any,
    *,
    trade_date: str,
    persist: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    target = _date_text(trade_date)
    generated_at = (now or datetime.now(MARKET_TIMEZONE)).astimezone(MARKET_TIMEZONE)
    plans = [
        item for item in _documents(store.collection(PLAN_COLLECTION))
        if _date_text(item.get("trade_date")) == target
        and str(item.get("playbook_id") or "") == PLAYBOOK_ID
        and str(item.get("playbook_version") or "") == PLAYBOOK_VERSION
    ]
    facts_by_plan: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in _documents(store.collection(FACT_COLLECTION)):
        if _date_text(item.get("trade_date")) == target:
            facts_by_plan[str(item.get("plan_id") or "")].append(item)
    minutes = {
        _minute_key(item): item
        for item in _documents(store.collection(MINUTE_COLLECTION))
        if _date_text(item.get("trade_date")) == target
        and str(item.get("interval") or "").lower().rstrip("m") == "5"
    }

    materialized: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for plan_document in plans:
        ticker = str(plan_document.get("ticker") or "").upper()
        plan_id = str(plan_document.get("plan_id") or "")
        session_id = f"{target}:{ticker}:{plan_id}"
        raw_fact_documents = sorted(
            facts_by_plan.get(plan_id) or [],
            key=lambda item: str(item.get("computed_at") or ""),
        )
        if not raw_fact_documents:
            skipped.append({"session_id": session_id, "reason": "缺少全场景事实快照"})
            continue
        frames: list[ScenarioReplayFactFrame] = []
        parse_errors: list[str] = []
        for document in raw_fact_documents:
            try:
                frames.append(_facts(document))
            except Exception as error:  # noqa: BLE001 - 会话降级为不可审计，不伪造帧
                parse_errors.append(str(error)[:240])
        if not frames:
            skipped.append({
                "session_id": session_id,
                "reason": "所有事实快照都无法还原",
                "errors": parse_errors[:3],
            })
            continue
        stock_minutes = minutes.get((ticker, target))
        sse_minutes = minutes.get((SSE_TICKER, target))
        chinext_minutes = minutes.get((CHINEXT_TICKER, target))
        try:
            typed_plan = _plan(dict(plan_document.get("plan") or {}))
            metadata = _coverage_metadata(
                plan_document=plan_document,
                plan=typed_plan,
                frames=frames,
                stock_minutes=stock_minutes or {},
                sse_minutes=sse_minutes or {},
                chinext_minutes=chinext_minutes or {},
            )
            replay = replay_persisted_scenario_session(
                session_id=session_id,
                plan=typed_plan,
                fact_frames=frames,
                metadata=metadata,
            )
        except Exception as error:  # noqa: BLE001 - 严格拒绝未来事实或损坏计划
            skipped.append({"session_id": session_id, "reason": str(error)[:300]})
            continue
        audit_reasons = _audit_reasons(
            plan_document=plan_document,
            frames=frames,
            minute_documents=(stock_minutes, sse_minutes, chinext_minutes),
            parse_error_count=len(parse_errors),
        )
        replay.update({
            "trade_date": target,
            "plan_id": plan_id,
            "audit_eligible": not audit_reasons,
            "audit_exclusion_reasons": audit_reasons,
            "materialized_at": generated_at.isoformat(timespec="seconds"),
        })
        materialized.append(replay)
        if persist:
            store.collection(REPLAY_COLLECTION).update_one(
                {"session_id": session_id},
                {"$set": replay},
                upsert=True,
            )

    core = {
        "job": "12J-replay-materialization",
        "trade_date": target,
        "status": (
            "no_data" if not materialized
            else "partial" if skipped
            else "success"
        ),
        "plan_count": len(plans),
        "materialized_replay_count": len(materialized),
        "audit_eligible_replay_count": sum(
            bool(item.get("audit_eligible")) for item in materialized
        ),
        "audit_excluded_replay_count": sum(
            not bool(item.get("audit_eligible")) for item in materialized
        ),
        "skipped_count": len(skipped),
        "skipped": skipped[:100],
        "session_ids": [item["session_id"] for item in materialized],
        "output_digests": [item["output_digest"] for item in materialized],
    }
    fingerprint = sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    report = {
        **core,
        "report_id": f"job12j-replay:{target}:{fingerprint[:16]}",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "persisted": bool(persist),
    }
    if persist:
        collection = store.collection(REPORT_COLLECTION)
        collection.update_one({"report_id": report["report_id"]}, {"$set": report}, upsert=True)
        latest = {**report, "report_id": f"job12j-replay:{target}:latest"}
        collection.update_one({"report_id": latest["report_id"]}, {"$set": latest}, upsert=True)
    return report


class TradingReplayMaterializationJob:
    def __init__(self, store: Any | None = None) -> None:
        config = load_config()
        if store is None:
            if not config.mongo_uri:
                raise ValueError("SHILUN_MONGO_URI is required for replay materialization.")
            store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
        self.store = store
        self._owns_store = isinstance(store, MongoSnapshotStore)

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def run(
        self,
        *,
        trade_date: str | None = None,
        persist: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = (now or datetime.now(MARKET_TIMEZONE)).astimezone(MARKET_TIMEZONE)
        return build_replay_materialization_report(
            self.store,
            trade_date=trade_date or current.strftime("%Y%m%d"),
            persist=persist,
            now=current,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize production scenario facts into Job 12H replays.")
    parser.add_argument("--date", dest="trade_date")
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args(argv)
    job = TradingReplayMaterializationJob()
    try:
        report = job.run(trade_date=args.trade_date, persist=args.persist)
    finally:
        job.close()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["materialized_replay_count"] > 0 else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "REPLAY_COLLECTION",
    "REPORT_COLLECTION",
    "TradingReplayMaterializationJob",
    "build_replay_materialization_report",
]
