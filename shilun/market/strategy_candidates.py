"""Versioned strategy-candidate views built from full-market snapshot records.

The existing MA5 candidate endpoint is sector-first.  Strategies whose T-1
selection must scan the whole market are stored as ``strategy_signals`` on
``market_snapshot_records`` and projected through this module.  Keeping the
projection here lets the API and the intraday monitor consume the same frozen
T-1 evidence without creating a second ranking chain.
"""

from __future__ import annotations

from typing import Any, Iterable


def _date_text(value: Any) -> str:
    text = str(value or "")[:10]
    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def strategy_signal(record: dict[str, Any], strategy_id: str) -> dict[str, Any] | None:
    """Return the matching strategy signal from one snapshot record."""

    normalized = str(strategy_id or "").strip().lower()
    for raw in record.get("strategy_signals") or ():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("strategy_id") or "").strip().lower() == normalized:
            return dict(raw)
    return None


def _selection_payload(record: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
    for value in (
        signal.get("selection"),
        signal.get("evidence"),
        record.get("oversold_first_board"),
        record.get("strategy_evidence"),
    ):
        if isinstance(value, dict):
            return dict(value)
    return {}


def is_selected_signal(signal: dict[str, Any], payload: dict[str, Any]) -> bool:
    explicit = payload.get("selection_passed")
    if explicit is None:
        explicit = signal.get("selection_passed")
    if explicit is not None:
        return bool(explicit)
    return str(signal.get("status") or "").lower() in {
        "selected",
        "matched",
        "eligible",
        "candidate",
    }


def build_strategy_candidate_rows(
    records: Iterable[dict[str, Any]],
    *,
    strategy_id: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Project snapshot records into the stable selection-page row shape."""

    rows: list[dict[str, Any]] = []
    normalized_id = str(strategy_id or "").strip().lower()
    for record in records:
        signal = strategy_signal(record, normalized_id)
        if signal is None:
            continue
        selection = _selection_payload(record, signal)
        if not is_selected_signal(signal, selection):
            continue
        selection_metrics = (
            dict(selection.get("metrics") or {})
            if isinstance(selection.get("metrics"), dict)
            else {}
        )
        evidence = {
            **selection_metrics,
            "summary": selection.get("summary") or signal.get("reason"),
            "evidence": list(selection.get("evidence") or []),
        }
        criteria = selection.get("criteria") or signal.get("criteria") or []
        score = _number(
            selection.get("selection_score")
            or selection.get("score")
            or signal.get("selection_score")
            or signal.get("score")
        )
        row = {
            **record,
            "sector_name": record.get("sector_name") or record.get("industry") or "",
            "strategy_id": normalized_id,
            "strategy_version": signal.get("strategy_version") or signal.get("version") or "",
            "strategy_name": signal.get("name") or "超跌首板点火",
            "selection_status": "selected",
            "selection_as_of": record.get("analysis_date"),
            "selection_score": round(score, 4) if score is not None else 0.0,
            "selection_evidence": dict(evidence) if isinstance(evidence, dict) else {},
            "selection_criteria": list(criteria) if isinstance(criteria, (list, tuple)) else [],
            "reason": signal.get("reason") or selection.get("reason") or "T-1 选股规则全部通过",
            "ranking_status": "eligible",
            "pool_status": "strategy_selected",
        }
        # Frequently used evidence is copied to the top level so existing UI
        # formatters and exports do not need to understand nested payloads.
        for key in (
            "avg_amount_15d",
            "avg_amount_15d_cny",
            "return_7d",
            "close_to_ma20",
            "ma20_distance",
            "ma60_overhead_ratio",
            "drawdown_from_high_20d",
            "rsi14",
            "prior_limit_up_count",
            "prior_limit_up_dates",
            "last_limit_up_date",
        ):
            if row.get(key) is None and isinstance(evidence, dict) and evidence.get(key) is not None:
                row[key] = evidence.get(key)
        row.setdefault("close_to_ma20", evidence.get("close_to_ma20_ratio"))
        row.setdefault("ma20_distance", evidence.get("close_to_ma20_ratio"))
        row.setdefault("prior_limit_up_count", evidence.get("limit_up_count_60d"))
        row.setdefault("prior_limit_up_dates", evidence.get("limit_up_dates_60d"))
        rows.append(row)

    rows.sort(
        key=lambda item: (
            -float(item.get("selection_score") or 0.0),
            str(item.get("ticker") or ""),
        )
    )
    for index, row in enumerate(rows, start=1):
        row["pool_rank"] = index
    return rows[: max(0, int(limit))] if limit is not None else rows


def find_latest_strategy_candidates(
    store: Any,
    *,
    target_date: str,
    strategy_id: str,
    exclude_st: bool = True,
    limit: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Read the latest frozen T-1 candidate set at or before ``target_date``."""

    target = _date_text(target_date)
    collection = store.collection("market_snapshot_records")
    query = {
        "analysis_date": {"$lte": target},
        "exclude_st": bool(exclude_st),
        "strategy_signals.strategy_id": str(strategy_id or "").strip().lower(),
    }
    try:
        cursor = collection.find(query, {"_id": 0})
        try:
            cursor = cursor.sort([("analysis_date", -1), ("rank", 1)])
        except TypeError:
            cursor = sorted(
                list(cursor),
                key=lambda item: (str(item.get("analysis_date") or ""), -int(item.get("rank") or 999999)),
                reverse=True,
            )
        records = [dict(item) for item in cursor]
    except Exception:
        records = []

    # Lightweight in-memory stores used by tests may not support nested-array
    # Mongo predicates.  A broad fallback is safe because we filter signals
    # again before returning anything.
    if not records:
        try:
            records = [
                dict(item)
                for item in collection.find({}, {"_id": 0})
                if _date_text(item.get("analysis_date")) <= target
                and bool(item.get("exclude_st", True)) is bool(exclude_st)
            ]
        except Exception:
            records = []
    dates = [
        _date_text(item.get("analysis_date"))
        for item in records
        if strategy_signal(item, strategy_id) is not None
    ]
    latest_date = max((value for value in dates if value), default="")
    latest_records = [
        item for item in records if _date_text(item.get("analysis_date")) == latest_date
    ]
    rows = build_strategy_candidate_rows(
        latest_records,
        strategy_id=strategy_id,
        limit=limit,
    )
    return latest_date, rows


__all__ = [
    "build_strategy_candidate_rows",
    "find_latest_strategy_candidates",
    "is_selected_signal",
    "strategy_signal",
]


REVIEW_TACTIC_IDS = ('red_k_net_inflow', 'structure_repair_520', 'weekly_breakout')


def review_facet(store, target_date, field):
    from shilun.review.service import REVIEW_COLLECTION, REVIEW_VERSION
    if field not in ('tactics', 'expectations', 'turnover_new_faces'):
        raise ValueError('Unknown review facet')
    doc = store.collection(REVIEW_COLLECTION).find_one(
        {'analysis_date': target_date, 'engine_version': REVIEW_VERSION},
        {'_id': 0, 'analysis_date': 1, field: 1})
    if field == 'turnover_new_faces' and doc and doc.get(field):
        from shilun.market.taxonomy import reconcile_dated_industries
        doc = {**doc, field: reconcile_dated_industries(store, target_date, doc[field], sector_field='sector_name')}
    return {'analysis_date': target_date, 'status': 'ok' if doc and field in doc else 'not_materialized',
            field: (doc or {}).get(field)}


def review_candidate_payload(store, target_date, strategy_id):
    snapshot = review_facet(store, target_date, 'tactics')
    tactic = (snapshot['tactics'] or {}).get(strategy_id, {})
    return {'analysis_date': target_date, 'strategy_id': strategy_id,
            'status': snapshot['status'], 'strategy_name': tactic.get('label'),
            'research_only': True, 'rule': tactic.get('buy_point'),
            'candidates': tactic.get('items', []), 'total_count': tactic.get('count'),
            '_from_cache': True, '_cache_source': 'daily_review_snapshots.tactics'}
