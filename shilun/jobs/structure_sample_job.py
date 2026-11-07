from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from typing import Any
from zoneinfo import ZoneInfo

from shilun.common.config import AppConfig, load_config
from shilun.common.db import MongoSnapshotStore
from shilun.market.structure_samples import (
    OUTCOME_HORIZONS,
    SAMPLE_VERSION,
    build_structure_sample,
    settle_structure_sample,
)


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
COLLECTION_NAME = "structure_operability_samples"
DEFAULT_BENCHMARK = "000001.SH"


@dataclass(frozen=True)
class StructureSampleRequest:
    target_date: str | None = None
    benchmark_ticker: str = DEFAULT_BENCHMARK
    max_candidates: int = 200
    settle_pending: bool = True


@dataclass(frozen=True)
class StructureSampleResult:
    analysis_date: str
    candidate_count: int
    inserted_count: int
    existing_count: int
    skipped_count: int
    settled_count: int
    pending_sample_count: int
    template_counts: dict[str, int]
    generated_at: str


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:10]


def _documents(cursor: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in cursor]


class StructureSampleJob:
    """Freeze daily operability/template facts, then settle forward outcomes.

    Frozen facts are insert-only for a given ``sample_id``. Re-running the job
    only fills outcome fields, so a later cache refresh cannot rewrite what the
    system knew on the sample date.
    """

    def __init__(
        self,
        *,
        config: AppConfig | None = None,
        mongo_store: MongoSnapshotStore | Any | None = None,
        now_factory: Any | None = None,
    ) -> None:
        self.config = config or load_config()
        self.store = mongo_store or self._build_store()
        self._owns_store = mongo_store is None
        self.now_factory = now_factory or (lambda: datetime.now(MARKET_TIMEZONE))

    def _build_store(self) -> MongoSnapshotStore:
        if not self.config.mongo_uri:
            raise ValueError("SHILUN_MONGO_URI is required for StructureSampleJob.")
        return MongoSnapshotStore(self.config.mongo_uri, self.config.mongo_db)

    def close(self) -> None:
        if self._owns_store and self.store is not None:
            self.store.close()

    def run(self, request: StructureSampleRequest) -> StructureSampleResult:
        now = self.now_factory()
        generated_at = now.isoformat(timespec="seconds")
        cache = self._load_candidate_cache(request.target_date, request.benchmark_ticker)
        if not cache:
            raise RuntimeError("No sector_trends_cache candidate pool is available for structure sampling.")
        analysis_date = _date_text(cache.get("analysis_date"))
        payload = cache.get("payload") or {}
        candidates = [dict(item) for item in (payload.get("candidates") or [])]
        candidates.sort(key=lambda item: (int(item.get("pool_rank") or 999999), str(item.get("ticker") or "")))
        candidates = candidates[: max(1, int(request.max_candidates))]
        if not candidates:
            raise RuntimeError(f"Candidate cache for {analysis_date} has no payload.candidates records.")

        tickers = [str(item.get("ticker") or "").upper() for item in candidates if item.get("ticker")]
        bars_by_ticker = self._load_bars([*tickers, request.benchmark_ticker], end_date=analysis_date)
        benchmark_bars = bars_by_ticker.get(request.benchmark_ticker, [])
        market_gate = self._load_market_gate(analysis_date, request.benchmark_ticker)
        sectors = {
            str(item.get("sector_name") or ""): dict(item)
            for item in (payload.get("top_sectors") or [])
            if item.get("sector_name")
        }

        collection = self.store.collection(COLLECTION_NAME)
        inserted = existing = skipped = 0
        template_counts = {"A": 0, "B": 0, "C": 0}
        for candidate in candidates:
            ticker = str(candidate.get("ticker") or "").upper()
            bars = bars_by_ticker.get(ticker) or []
            if not ticker or not bars:
                skipped += 1
                continue
            sample = build_structure_sample(
                analysis_date=analysis_date,
                candidate=candidate,
                bars=bars,
                benchmark_bars=benchmark_bars,
                sector_context=sectors.get(str(candidate.get("sector_name") or "")),
                market_gate=market_gate,
                cache_version=str(cache.get("engine_version") or payload.get("engine_version") or ""),
                generated_at=generated_at,
            )
            existing_sample = collection.find_one(
                {"sample_id": sample["sample_id"]},
                {"_id": 0, "matched_template_ids": 1},
            )
            if existing_sample:
                existing += 1
                counted_template_ids = existing_sample.get("matched_template_ids") or []
            else:
                collection.update_one(
                    {"sample_id": sample["sample_id"]},
                    {"$setOnInsert": sample},
                    upsert=True,
                )
                inserted += 1
                counted_template_ids = sample.get("matched_template_ids") or []
            for template_id in counted_template_ids:
                if template_id in template_counts:
                    template_counts[template_id] += 1

        settled_count = self.settle_pending(settled_at=generated_at) if request.settle_pending else 0
        pending_count = collection.count_documents({"settlement_status": {"$ne": "complete"}})
        result = StructureSampleResult(
            analysis_date=analysis_date,
            candidate_count=len(candidates),
            inserted_count=inserted,
            existing_count=existing,
            skipped_count=skipped,
            settled_count=settled_count,
            pending_sample_count=pending_count,
            template_counts=template_counts,
            generated_at=generated_at,
        )
        self._record_state(result)
        return result

    def settle_pending(self, *, settled_at: str | None = None, limit: int = 5000) -> int:
        settled_at = settled_at or self.now_factory().isoformat(timespec="seconds")
        collection = self.store.collection(COLLECTION_NAME)
        cursor = collection.find(
            {"settlement_status": {"$ne": "complete"}},
            {"_id": 0},
        ).sort([("analysis_date", 1), ("ticker", 1)]).limit(max(1, int(limit)))
        samples = _documents(cursor)
        if not samples:
            return 0
        tickers = sorted({str(item.get("ticker") or "").upper() for item in samples if item.get("ticker")})
        benchmark_tickers = sorted(
            {
                str((item.get("market_context") or {}).get("benchmark_ticker") or DEFAULT_BENCHMARK)
                for item in samples
            }
        )
        start_date = min(_date_text(item.get("analysis_date")) for item in samples)
        bars_by_ticker = self._load_bars([*tickers, *benchmark_tickers], start_date=start_date)
        updated = 0
        for sample in samples:
            ticker = str(sample.get("ticker") or "").upper()
            benchmark_ticker = str((sample.get("market_context") or {}).get("benchmark_ticker") or DEFAULT_BENCHMARK)
            settlement = settle_structure_sample(
                sample,
                future_bars=bars_by_ticker.get(ticker) or [],
                benchmark_future_bars=bars_by_ticker.get(benchmark_ticker) or [],
                settled_at=settled_at,
            )
            if settlement.get("outcomes") != (sample.get("outcomes") or {}):
                collection.update_one(
                    {"sample_id": sample.get("sample_id")},
                    {"$set": settlement},
                    upsert=False,
                )
                updated += 1
        return updated

    def _load_candidate_cache(self, target_date: str | None, benchmark_ticker: str) -> dict[str, Any] | None:
        query: dict[str, Any] = {"benchmark_ticker": benchmark_ticker}
        if target_date:
            query["analysis_date"] = _date_text(target_date)
        cursor = self.store.collection("sector_trends_cache").find(query, {"_id": 0}).sort(
            [("analysis_date", -1), ("computed_at", -1)]
        ).limit(1)
        return next(iter(cursor), None)

    def _load_market_gate(self, analysis_date: str, benchmark_ticker: str) -> dict[str, Any] | None:
        cursor = self.store.collection("market_part1_cache").find(
            {"analysis_date": analysis_date, "benchmark_ticker": benchmark_ticker},
            {"_id": 0, "payload.market_gate": 1, "payload.permission": 1, "computed_at": 1},
        ).sort("computed_at", -1).limit(1)
        cached = next(iter(cursor), None)
        if not cached:
            return None
        payload = cached.get("payload") or {}
        gate = dict(payload.get("market_gate") or {})
        if "state" not in gate and payload.get("permission"):
            gate["state"] = payload.get("permission")
        return gate or None

    def _load_bars(
        self,
        tickers: list[str],
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        normalized = sorted({str(ticker or "").upper() for ticker in tickers if ticker})
        if not normalized:
            return {}
        query: dict[str, Any] = {"ticker": {"$in": normalized}}
        date_filter: dict[str, Any] = {}
        if start_date:
            date_filter["$gte"] = _date_text(start_date)
        if end_date:
            date_filter["$lte"] = _date_text(end_date)
        if date_filter:
            query["date"] = date_filter
        cursor = self.store.collection("market_daily_bars").find(
            query,
            {"_id": 0, "ticker": 1, "date": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "vol": 1, "amount": 1},
        ).sort([("ticker", 1), ("date", 1)])
        result: dict[str, list[dict[str, Any]]] = {}
        for item in cursor:
            ticker = str(item.get("ticker") or "").upper()
            if ticker:
                result.setdefault(ticker, []).append(dict(item))
        return result

    def _record_state(self, result: StructureSampleResult) -> None:
        updater = getattr(self.store, "upsert_sync_state", None)
        if updater is None:
            return
        updater(
            {
                "dataset": COLLECTION_NAME,
                "scope": result.analysis_date.replace("-", ""),
                "status": "success" if result.skipped_count == 0 else "partial",
                "analysis_date": result.analysis_date,
                "row_count": result.inserted_count + result.existing_count,
                "inserted_count": result.inserted_count,
                "existing_count": result.existing_count,
                "skipped_count": result.skipped_count,
                "settled_count": result.settled_count,
                "pending_sample_count": result.pending_sample_count,
                "template_counts": result.template_counts,
                "sample_version": SAMPLE_VERSION,
                "outcome_horizons": list(OUTCOME_HORIZONS),
                "updated_at": result.generated_at,
            }
        )


def _print_result(result: StructureSampleResult) -> None:
    print(json.dumps({
        "analysis_date": result.analysis_date,
        "candidate_count": result.candidate_count,
        "inserted_count": result.inserted_count,
        "existing_count": result.existing_count,
        "skipped_count": result.skipped_count,
        "settled_count": result.settled_count,
        "pending_sample_count": result.pending_sample_count,
        "template_counts": result.template_counts,
        "generated_at": result.generated_at,
    }, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze structure operability and A/B/C template samples.")
    parser.add_argument("--date", dest="target_date", help="YYYY-MM-DD; default uses latest candidate cache")
    parser.add_argument("--benchmark", default=DEFAULT_BENCHMARK)
    parser.add_argument("--max-candidates", type=int, default=200)
    parser.add_argument("--no-settle", action="store_true")
    args = parser.parse_args(argv)
    job = StructureSampleJob()
    try:
        result = job.run(
            StructureSampleRequest(
                target_date=args.target_date,
                benchmark_ticker=args.benchmark,
                max_candidates=args.max_candidates,
                settle_pending=not args.no_settle,
            )
        )
    finally:
        job.close()
    _print_result(result)
    return 0


def scheduler_main() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    def run_latest() -> None:
        job = StructureSampleJob()
        try:
            result = job.run(StructureSampleRequest())
            print(
                f"structure_samples date={result.analysis_date} candidates={result.candidate_count} "
                f"inserted={result.inserted_count} existing={result.existing_count} "
                f"settled={result.settled_count} pending={result.pending_sample_count}",
                flush=True,
            )
        except Exception as error:  # noqa: BLE001 - scheduler must survive one unavailable cache
            print(f"structure_samples skipped: {error}", flush=True)
        finally:
            job.close()

    for identifier, hour, minute in (("after_close", 15, 40), ("nightly_catchup", 23, 5)):
        scheduler.add_job(
            run_latest,
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone="Asia/Shanghai"),
            id=f"structure_samples_{identifier}",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )
    scheduler.add_job(
        run_latest,
        DateTrigger(run_date=datetime.now(MARKET_TIMEZONE) + timedelta(seconds=5)),
        id="structure_samples_startup_catchup",
        replace_existing=True,
    )
    print("Structure sample scheduler started. Runs at 15:40 and 23:05 Asia/Shanghai.", flush=True)
    scheduler.start()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "COLLECTION_NAME",
    "StructureSampleJob",
    "StructureSampleRequest",
    "StructureSampleResult",
    "main",
    "scheduler_main",
]
