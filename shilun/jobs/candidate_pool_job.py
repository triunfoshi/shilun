from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from shilun.candidate_pools import POOL_PRIORITY, CandidatePoolStatus, classify_candidate_pool
from shilun.common.config import AppConfig, load_config
from shilun.common.db import CandidatePoolStateStore, MarketSnapshotRecordStore, MongoSnapshotStore


@dataclass(frozen=True)
class CandidatePoolRequest:
    target_date: str | None = None
    exclude_st: bool = True
    top_n: int | None = None
    output_dir: str = "outputs"


@dataclass(frozen=True)
class CandidatePoolResult:
    analysis_date: str
    state_count: int
    event_count: int
    report_path: Path
    states_csv_path: Path


class CandidatePoolJob:
    """Build daily candidate pool states from Mongo market snapshot records."""

    def __init__(
        self,
        *,
        config: AppConfig | None = None,
        mongo_store: MongoSnapshotStore | None = None,
        market_snapshot_store: MarketSnapshotRecordStore | None = None,
        candidate_pool_store: CandidatePoolStateStore | None = None,
    ) -> None:
        self.config = config or load_config()
        self.mongo_store = mongo_store or self._build_mongo_store()
        self.market_snapshot_store = market_snapshot_store or (
            getattr(self.mongo_store, "market_snapshots", self.mongo_store) if self.mongo_store is not None else None
        )
        self.candidate_pool_store = candidate_pool_store or (
            getattr(self.mongo_store, "candidate_pools", self.mongo_store) if self.mongo_store is not None else None
        )

    def __del__(self) -> None:  # pragma: no cover
        if self.mongo_store is not None:
            try:
                self.mongo_store.close()
            except Exception:
                pass

    def run(self, request: CandidatePoolRequest) -> CandidatePoolResult:
        if self.market_snapshot_store is None or self.candidate_pool_store is None:
            raise ValueError("SHILUN_MONGO_URI is required for CandidatePoolJob.")

        analysis_date = _date_text(request.target_date or datetime.now().strftime("%Y-%m-%d"))
        records = self.market_snapshot_store.find_market_snapshot_records(
            analysis_date=analysis_date,
            exclude_st=request.exclude_st,
            limit=request.top_n,
        )
        if not records:
            raise RuntimeError("No Mongo market_snapshot_records found for candidate pool generation.")

        tickers = [str(record.get("ticker") or "") for record in records if record.get("ticker")]
        previous_states = self.candidate_pool_store.find_latest_candidate_pool_states_before(
            analysis_date=analysis_date,
            exclude_st=request.exclude_st,
            tickers=tickers,
        )
        states, events = build_candidate_pool_states(
            records=records,
            analysis_date=analysis_date,
            exclude_st=request.exclude_st,
            previous_states=previous_states,
        )

        self.candidate_pool_store.upsert_candidate_pool_states(
            analysis_date=analysis_date,
            exclude_st=request.exclude_st,
            states=states,
        )
        self.candidate_pool_store.upsert_candidate_pool_events(
            analysis_date=analysis_date,
            exclude_st=request.exclude_st,
            events=events,
        )

        output_dir = Path(request.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        suffix = "_no_st" if request.exclude_st else ""
        report_path = output_dir / f"candidate_pool_{analysis_date}{suffix}.md"
        states_csv_path = output_dir / f"candidate_pool_{analysis_date}{suffix}.csv"
        pd.DataFrame(states).to_csv(states_csv_path, index=False, encoding="utf-8-sig")
        report_path.write_text(
            render_candidate_pool_report(
                analysis_date=analysis_date,
                states=states,
                events=events,
                exclude_st=request.exclude_st,
            ),
            encoding="utf-8",
        )
        return CandidatePoolResult(
            analysis_date=analysis_date,
            state_count=len(states),
            event_count=len(events),
            report_path=report_path,
            states_csv_path=states_csv_path,
        )

    def _build_mongo_store(self) -> MongoSnapshotStore | None:
        if not self.config.mongo_uri:
            return None
        return MongoSnapshotStore(self.config.mongo_uri, self.config.mongo_db)


def build_candidate_pool_states(
    *,
    records: list[dict[str, Any]],
    analysis_date: str,
    exclude_st: bool,
    previous_states: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    previous_states = previous_states or {}
    states: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for record in records:
        ticker = str(record.get("ticker") or "")
        if not ticker:
            continue
        previous = previous_states.get(ticker)
        decision = classify_candidate_pool(record)
        event_type = resolve_event_type(
            previous_pool_status=str(previous.get("pool_status") or "") if previous else None,
            current_pool_status=decision.pool_status.value,
        )
        state = build_state_record(
            record=record,
            analysis_date=analysis_date,
            exclude_st=exclude_st,
            decision=decision.to_record(),
            previous_state=previous,
            event_type=event_type,
        )
        states.append(state)
        if event_type != "unchanged":
            events.append(build_event_record(state))

    states.sort(key=lambda state: (POOL_PRIORITY.get(str(state.get("pool_status")), 0) * -1, int(state.get("rank") or 999999)))
    events.sort(key=lambda event: (str(event.get("event_type") or ""), int(event.get("rank") or 999999)))
    return states, events


def build_state_record(
    *,
    record: dict[str, Any],
    analysis_date: str,
    exclude_st: bool,
    decision: dict[str, Any],
    previous_state: dict[str, Any] | None,
    event_type: str,
) -> dict[str, Any]:
    pool_status = str(decision["pool_status"])
    previous_pool_status = str(previous_state.get("pool_status") or "") if previous_state else ""
    same_pool = bool(previous_state) and previous_pool_status == pool_status
    days_in_pool = int(previous_state.get("days_in_pool") or 0) + 1 if same_pool else 1
    entered_at = str(previous_state.get("entered_at") or analysis_date) if same_pool else analysis_date
    return {
        "analysis_date": analysis_date,
        "exclude_st": bool(exclude_st),
        "ticker": str(record.get("ticker") or ""),
        "name": record.get("name") or "",
        "industry": record.get("industry") or "",
        "market": record.get("market") or "",
        "rank": _int(record.get("rank"), default=999999),
        "pool_status": pool_status,
        "previous_pool_status": previous_pool_status,
        "event_type": event_type,
        "pool_score": decision.get("score"),
        "pool_reasons": "; ".join(decision.get("reasons") or []),
        "days_in_pool": days_in_pool,
        "entered_at": entered_at,
        "last_seen_at": analysis_date,
        "candidate_tags": record.get("candidate_tags") or "",
        "strategy_ids": record.get("strategy_ids") or "",
        "action_label": record.get("action_label") or "",
        "conclusion_label": record.get("conclusion_label") or "",
        "trend_stage": record.get("trend_stage") or "",
        "execution_score": record.get("execution_score"),
        "risk_score": record.get("risk_score"),
        "entry_probability": record.get("entry_probability"),
        "p_fail_fast_3d": record.get("p_fail_fast_3d"),
        "p_continue_10d": record.get("p_continue_10d"),
        "target_position_pct": record.get("target_position_pct"),
    }


def build_event_record(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "analysis_date": state.get("analysis_date"),
        "exclude_st": state.get("exclude_st"),
        "ticker": state.get("ticker"),
        "name": state.get("name"),
        "industry": state.get("industry"),
        "rank": state.get("rank"),
        "event_type": state.get("event_type"),
        "pool_status": state.get("pool_status"),
        "previous_pool_status": state.get("previous_pool_status"),
        "pool_score": state.get("pool_score"),
        "pool_reasons": state.get("pool_reasons"),
        "candidate_tags": state.get("candidate_tags"),
        "strategy_ids": state.get("strategy_ids"),
    }


def resolve_event_type(previous_pool_status: str | None, current_pool_status: str) -> str:
    if not previous_pool_status:
        return "entered"
    if previous_pool_status == current_pool_status:
        return "unchanged"
    if current_pool_status == CandidatePoolStatus.REJECT.value:
        return "rejected"
    if previous_pool_status == CandidatePoolStatus.REJECT.value:
        return "reentered"
    previous_priority = POOL_PRIORITY.get(previous_pool_status, 0)
    current_priority = POOL_PRIORITY.get(current_pool_status, 0)
    if current_priority > previous_priority:
        return "promoted"
    return "demoted"


def render_candidate_pool_report(
    *,
    analysis_date: str,
    states: list[dict[str, Any]],
    events: list[dict[str, Any]],
    exclude_st: bool,
) -> str:
    title_suffix = "（已过滤 ST/*ST）" if exclude_st else ""
    state_counts = Counter(str(state.get("pool_status") or "") for state in states)
    event_counts = Counter(str(event.get("event_type") or "") for event in events)
    lines = [
        f"# {analysis_date} 候选池状态日报{title_suffix}",
        "",
        f"- 状态样本: {len(states)}",
        f"- 状态变化: {len(events)}",
        f"- 四池分布: candidate_pool={state_counts.get('candidate_pool', 0)} / watch_pool={state_counts.get('watch_pool', 0)} / buy_pool={state_counts.get('buy_pool', 0)} / reject_pool={state_counts.get('reject_pool', 0)}",
        f"- 事件分布: entered={event_counts.get('entered', 0)} / promoted={event_counts.get('promoted', 0)} / demoted={event_counts.get('demoted', 0)} / rejected={event_counts.get('rejected', 0)} / reentered={event_counts.get('reentered', 0)}",
        "",
        "## 重点变化",
        "",
    ]
    changed = [event for event in events if event.get("event_type") in {"promoted", "reentered", "rejected", "demoted", "entered"}]
    if not changed:
        lines.append("今日无明显候选池状态变化。")
    else:
        for event in changed[:30]:
            lines.append(
                "- "
                f"{event.get('event_type')} | {event.get('pool_status')} | "
                f"{event.get('ticker')} {event.get('name')} | "
                f"rank={event.get('rank')} | {event.get('pool_reasons')}"
            )
    lines.extend(["", "## 买入池 Top10", ""])
    buy_states = [state for state in states if state.get("pool_status") == CandidatePoolStatus.BUY.value]
    if not buy_states:
        lines.append("暂无买入池样本。")
    else:
        lines.extend(_state_lines(buy_states[:10]))
    lines.extend(["", "## 观察池 Top10", ""])
    watch_states = [state for state in states if state.get("pool_status") == CandidatePoolStatus.WATCH.value]
    if not watch_states:
        lines.append("暂无观察池样本。")
    else:
        lines.extend(_state_lines(watch_states[:10]))
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build daily candidate pool states from Mongo market_snapshot_records.")
    parser.add_argument("--date", dest="target_date", default=None, help="Target analysis date, e.g. 2026-05-16.")
    parser.add_argument("--top-n", type=int, default=None, help="Optional ranked record limit.")
    parser.add_argument("--include-st", action="store_true", help="Use include-ST snapshot scope instead of the default exclude-ST scope.")
    parser.add_argument("--output-dir", default="outputs", help="Report output directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = CandidatePoolJob().run(
        CandidatePoolRequest(
            target_date=args.target_date,
            exclude_st=not args.include_st,
            top_n=args.top_n,
            output_dir=args.output_dir,
        )
    )
    print(f"analysis_date={result.analysis_date}")
    print(f"state_count={result.state_count}")
    print(f"event_count={result.event_count}")
    print(f"report_path={result.report_path}")
    print(f"states_csv_path={result.states_csv_path}")


def _state_lines(states: list[dict[str, Any]]) -> list[str]:
    return [
        "- "
        f"{state.get('ticker')} {state.get('name')} | "
        f"rank={state.get('rank')} | score={state.get('pool_score')} | "
        f"days={state.get('days_in_pool')} | {state.get('pool_reasons')}"
        for state in states
    ]


def _date_text(value: str) -> str:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    main()
