"""Read-only evidence for native Mongo recovery, including unknown journal owners.

The native archive is the backup. This manifest contains hashes, indexes and a
recomputed summary, never originals. Capture while the caller holds the journal
write lease; this module neither acquires a lease nor creates indexes/records.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from bson import json_util

from .ledger import digest
from .service import TradeJournalService


MANIFEST_VERSION = "trade-journal-recovery-v1"
EXCLUDED_COLLECTIONS = frozenset({"trade_journal_write_locks"})


def _canonical(value):
    # Extended JSON retains ObjectId, binary subtype, Int64 and null versus 0.
    return json_util.dumps(value, json_options=json_util.CANONICAL_JSON_OPTIONS,
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _names(store):
    db = getattr(store, "db", None)
    if db is not None:
        names = db.list_collection_names()
    elif isinstance(getattr(store, "collections", None), dict):
        names = list(store.collections)  # In-memory stores used by owner tests.
    else:
        raise ValueError("collection discovery unavailable")
    return sorted(name for name in names if name.startswith("trade_journal_")
        and name not in EXCLUDED_COLLECTIONS)


def _indexes(collection):
    reader = getattr(collection, "index_information", None)
    # Legacy owner fakes have no index API. Mongo Collection always has it.
    raw = reader() if reader is not None else {}
    result = {}
    for name, definition in sorted(raw.items()):
        item = {key: deepcopy(value) for key, value in definition.items()
            if key not in {"ns", "v", "buildUUID"}}
        # Compound-key order affects query semantics and must survive sorting.
        item["key"] = [list(pair) for pair in definition["key"]]
        result[name] = item
    # Return JSON-compatible Extended JSON, even for unusual BSON index options.
    return json.loads(_canonical(result))


class _ReadCollection:
    """Frozen read adapter so validation cannot call a production write owner."""

    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection=None):
        result = []
        for raw in self.rows:
            if not all(raw.get(key) == value for key, value in query.items()):
                continue
            row = deepcopy(raw)
            for path, include in (projection or {}).items():
                if include != 0:
                    continue
                parts, parent = path.split("."), row
                for part in parts[:-1]:
                    parent = parent.get(part) if isinstance(parent, dict) else None
                if isinstance(parent, dict):
                    parent.pop(parts[-1], None)
            result.append(row)
        return result

    def find_one(self, query, projection=None):
        return next(iter(self.find(query, projection)), None)


class _ReadStore:
    def __init__(self, rows):
        self.rows = rows

    def collection(self, name):
        return _ReadCollection(self.rows.get(name, ()))


def _semantic_checks(rows, errors):
    frozen = _ReadStore(rows)
    journal = TradeJournalService(frozen)

    def fail(code, collection):
        # Never echo owner exceptions, raw identifiers, filenames or originals.
        errors.append(f"{code}:{collection}")

    def records(short):
        return [{key: value for key, value in row.items() if key != "_id"}
            for row in rows.get("trade_journal_" + short, ())]

    def indexed(short, fields):
        result = {}
        for row in records(short):
            key = tuple(row.get(field) for field in fields)
            if any(value is None for value in key) or key in result:
                fail("invalid_or_duplicate_identity", "trade_journal_" + short)
            result[key] = row
        return result

    sources = indexed("sources", ("source_id",))
    fills = indexed("fills", ("fill_id",))
    financial = indexed("financial_events", ("financial_event_id",))
    decisions = indexed("decisions", ("decision_id", "revision"))
    snapshots = indexed("snapshots", ("snapshot_id",))
    portfolio = indexed("portfolio_snapshots", ("snapshot_id",))
    batches = indexed("batches", ("batch_id",))
    checks = indexed("expectation_checks", ("check_id",))
    plans = {(row.get("plan_id"), row.get("revision")): row for row in snapshots.values()
        if row.get("kind") == "journal_plan"}

    # Use original owners for byte recovery and their exact immutable digests.
    for key in sources:
        try:
            journal.get_source(key[0], include_content=True)
        except Exception:
            fail("source_integrity", "trade_journal_sources")
    for key in snapshots:
        try:
            journal.get_snapshot(key[0], include_content=True)
        except Exception:
            fail("snapshot_integrity", "trade_journal_snapshots")
    if portfolio:
        from .analytics import PortfolioRiskService
        owner = PortfolioRiskService(frozen, journal=journal)
        for key in portfolio:
            try:
                owner.get_snapshot(key[0], include_content=True)
            except Exception:
                fail("snapshot_integrity", "trade_journal_portfolio_snapshots")

    # These owners store slightly different hash scopes. Unknown future owners
    # are byte-fingerprinted, without guessing their private record contract.
    full_hash = {"decisions", "expectation_checks", "observations", "allocations",
        "task_events", "execution_matches", "reviews", "mode_cards", "hypotheses"}
    for short in full_hash | {"fills", "financial_events", "batches", "links"}:
        for row in records(short):
            excluded = {"content_hash"}
            if short in {"fills", "financial_events", "batches", "links"}:
                excluded.add("recorded_at")
            if short in {"fills", "financial_events"}:
                excluded.update({"source_ref", "note"})
            if short == "fills":
                excluded.update({"inventory_order", "name"})
            if digest({key: value for key, value in row.items() if key not in excluded}) != row.get("content_hash"):
                fail("record_integrity", "trade_journal_" + short)
            if short in {"mode_cards", "hypotheses"} and digest(row.get("content")) != row.get("content_digest"):
                fail("content_integrity", "trade_journal_" + short)

    ledger, summary = journal._ledger(), journal.get_summary()
    episodes = {row["episode_id"]: row for row in ledger["episodes"]}
    fill_episodes = {row["fill_id"]: row["episode_id"] for row in ledger["fills"]}

    def reference(mapping, key, collection, expected_hash=None, alternate=False):
        target = mapping.get(key)
        if target is None:
            fail("missing_reference", collection)
        elif expected_hash is not None and expected_hash != target.get("content_hash") and not (
            alternate and expected_hash == target.get("content_digest")):
            fail("reference_hash", collection)
        return target

    def fill_reference(fill_id, episode, collection, expected_hash=None):
        target = reference(fills, (fill_id,), collection, expected_hash)
        if target is not None and episode is not None and fill_episodes.get(fill_id) != episode:
            fail("fill_episode_reference", collection)

    def walk(value, collection):
        """Validate explicit local evidence references, including nested reviews."""
        if isinstance(value, list):
            for item in value:
                walk(item, collection)
        elif isinstance(value, dict):
            source_ref = value.get("source_ref")
            if isinstance(source_ref, dict) and source_ref.get("source_id"):
                target = reference(sources, (source_ref["source_id"],), collection, source_ref.get("content_hash"))
                if target is not None and source_ref.get("sha256") and source_ref["sha256"] != target.get("sha256"):
                    fail("reference_sha256", collection)
            for field in ("snapshot_ref", "portfolio_snapshot_ref"):
                ref = value.get(field)
                if isinstance(ref, dict) and ref.get("snapshot_id"):
                    mapping = portfolio if field == "portfolio_snapshot_ref" or ref.get("collection") == "trade_journal_portfolio_snapshots" else snapshots
                    if (ref["snapshot_id"],) not in mapping and field == "snapshot_ref" and not ref.get("collection"):
                        mapping = portfolio
                    reference(mapping, (ref["snapshot_id"],), collection, ref.get("content_hash"), alternate=True)
            for item in value.values():
                walk(item, collection)

    reference_owners = full_hash | {"fills", "financial_events", "batches", "links", "snapshots", "portfolio_snapshots"}
    for collection, collection_rows in rows.items():
        if collection.removeprefix("trade_journal_") not in reference_owners:
            continue
        for row in collection_rows:
            walk(row, collection)

    for row in batches.values():
        collection = "trade_journal_batches"
        for source_id in row.get("source_ids", []):
            reference(sources, (source_id,), collection)
        for fill_id in row.get("fill_ids", []):
            reference(fills, (fill_id,), collection)
        for event_id in row.get("financial_event_ids", []):
            reference(financial, (event_id,), collection)
        for key, mapping, identity in (("fill_source_refs", fills, "fill_id"),
            ("financial_source_refs", financial, "financial_event_id")):
            for ref in row.get(key, []):
                reference(mapping, (ref.get(identity),), collection)

    for short, mapping in (("fills", fills), ("financial_events", financial)):
        for row in mapping.values():
            ref = row.get("source_ref")
            if not isinstance(ref, dict) or not ref.get("source_id"):
                fail("missing_source_reference", "trade_journal_" + short)

    for row in decisions.values():
        collection = "trade_journal_decisions"
        if row.get("episode_id") not in episodes:
            fail("missing_episode", collection)
        for fill_id in row.get("fill_ids", []):
            fill_reference(fill_id, row.get("episode_id"), collection)
        ref = row.get("frozen_plan_ref")
        if isinstance(ref, dict) and ref.get("plan_id"):
            target = reference(plans, (ref["plan_id"], ref.get("revision")), collection)
            if target is not None and ref.get("snapshot_id") != target["snapshot_id"]:
                fail("plan_snapshot_reference", collection)

    for row in records("links"):
        for side in ("from", "to"):
            target = reference(decisions, (row.get(side + "_decision_id"), row.get(side + "_revision")), "trade_journal_links")
            if target is not None and row.get(side + "_episode_id") != target["episode_id"]:
                fail("decision_episode_reference", "trade_journal_links")
    for row in checks.values():
        target = reference(decisions, (row.get("decision_id"), row.get("decision_revision")), "trade_journal_expectation_checks")
        if target is not None and row.get("episode_id") != target["episode_id"]:
            fail("decision_episode_reference", "trade_journal_expectation_checks")

    for short in ("reviews", "task_events", "allocations", "execution_matches"):
        collection = "trade_journal_" + short
        for row in records(short):
            episode = row.get("episode_id")
            if episode not in episodes:
                fail("missing_episode", collection)
            for fill_id in row.get("fill_ids", []):
                fill_reference(fill_id, episode, collection)
            for assignment in row.get("assignments", []):
                fill_reference(assignment.get("fill_id"), episode, collection)
            facts = row.get("facts_reference") or {}
            for ref in facts.get("fill_refs", []):
                fill_reference(ref.get("fill_id"), episode, collection, ref.get("content_hash"))
            for ref in facts.get("financial_refs", []):
                reference(financial, (ref.get("financial_event_id"),), collection, ref.get("content_hash"))
            for ref in facts.get("decision_refs", []):
                reference(decisions, (ref.get("decision_id"), ref.get("revision")), collection, ref.get("content_hash"))
            for ref in facts.get("expectation_check_refs", []):
                reference(checks, (ref.get("check_id"),), collection, ref.get("content_hash"))
            for batch_id in facts.get("committed_batch_ids", []):
                reference(batches, (batch_id,), collection)
            if short == "reviews":
                if digest(facts) != row.get("fact_digest") or facts.get("episode_id") != episode:
                    fail("review_fact_integrity", collection)
                if row.get("decision_refs") != facts.get("decision_refs"):
                    fail("review_decision_reference", collection)
            # A review can legitimately be stale after append-only revisions.
            # Its old episode digest is not compared with current episode data.
            if short == "execution_matches" and (digest(row.get("fills")) != row.get("fill_digest")
                or digest(row.get("live_execution")) != row.get("live_digest")):
                fail("execution_snapshot_integrity", collection)
    return ledger, summary


def capture_journal_manifest(store):
    """Fingerprint every durable journal collection and validate stored evidence.

    Failure is reported without exception text or source content. No owner write
    methods are called, including index setup and write-lock acquisition.
    """
    result = {"version": MANIFEST_VERSION, "valid": False, "errors": [],
        "included_collection_names": [], "collections": {}, "ledger_digest": None, "summary": None}
    errors, rows = result["errors"], {}
    try:
        result["included_collection_names"] = _names(store)
        for name in result["included_collection_names"]:
            collection = store.collection(name)
            documents = list(collection.find({}))
            documents.sort(key=_canonical)
            rows[name] = documents
            result["collections"][name] = {"count": len(documents), "sha256": _sha(documents),
                "indexes": _indexes(collection)}
        ledger, summary = _semantic_checks(rows, errors)
        result["ledger_digest"], result["summary"] = _sha(ledger), summary
        if _names(store) != result["included_collection_names"]:
            errors.append("collection_inventory_changed")
    except Exception:
        errors.append("journal_capture_failed")
    result["errors"] = sorted(set(errors))
    result["valid"] = not result["errors"]
    return result


def verify_journal_manifest(store, manifest):
    """Compare a restored journal against a valid pre-dump manifest, read-only."""
    if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION or manifest.get("valid") is not True or manifest.get("errors") != []:
        return {"valid": False, "errors": ["invalid_expected_manifest"]}
    names = manifest.get("included_collection_names")
    collections = manifest.get("collections")
    if (not isinstance(names, list) or any(not isinstance(name, str) or not name.startswith("trade_journal_")
        or name in EXCLUDED_COLLECTIONS for name in names) or len(names) != len(set(names))
        or not isinstance(collections, dict) or set(names) != set(collections)):
        return {"valid": False, "errors": ["invalid_expected_manifest"]}
    current = capture_journal_manifest(store)
    errors = list(current["errors"])
    if names != current["included_collection_names"]:
        errors.append("collection_inventory_mismatch")
    for name in names:
        old, new = collections.get(name), current["collections"].get(name)
        if not isinstance(old, dict) or set(old) != {"count", "sha256", "indexes"} or type(old.get("count")) is not int or old["count"] < 0 or not isinstance(old.get("sha256"), str) or len(old["sha256"]) != 64 or not isinstance(old.get("indexes"), dict):
            errors.append("invalid_expected_manifest")
            continue
        if new is None:
            errors.append("missing_collection:" + name)
            continue
        for field in ("count", "sha256", "indexes"):
            if old[field] != new[field]:
                errors.append("collection_" + field + "_mismatch:" + name)
    for field in ("ledger_digest", "summary"):
        if manifest.get(field) is None or manifest.get(field) != current[field]:
            errors.append(field + "_mismatch")
    return {"valid": not errors, "errors": sorted(set(errors))}
