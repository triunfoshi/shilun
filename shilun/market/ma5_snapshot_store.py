"""Optional immutable storage for complete, JSON-shaped MA5 universe scans.

This store never authorizes a cache hit. The request-local computation cache
must still compare the saved signature against the actual daily input contents.
No prepared frames, minute evidence or candidate scores are persisted here.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Callable

from bson import BSON

from shilun.market.ma5_liquidity import MA5_SELECTION_RULE_VERSION

COLLECTION = "ma5_universe_cache"
FORMAT_VERSION = 1
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_SIGNATURE = re.compile(r"^[a-f0-9]{64}$")


def _hash(value: Any) -> str:
    # Stable under Mongo round trips, which need not preserve dictionary order.
    # Existing numeric NaN diagnostics are retained rather than changed to zero.
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


def _head_id(day: str) -> str:
    return f"head:{MA5_SELECTION_RULE_VERSION}:{day}"


def _snapshot_id(day: str, signature: str) -> str:
    return f"scan:{MA5_SELECTION_RULE_VERSION}:{day}:{signature}"


def _chunk_id(snapshot: str, index: int) -> str:
    return f"chunk:{snapshot}:{index:05d}"


def _validate_entry(day: str, entry: dict) -> None:
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("Exact ISO analysis date required")
    if not isinstance(entry, dict) or not _SIGNATURE.fullmatch(str(entry.get("signature") or "")):
        raise ValueError("Invalid daily input signature")
    value = entry.get("value")
    if not isinstance(value, dict) or not isinstance(value.get("audits"), list):
        raise ValueError("Complete universe audits required")
    scope = value.get("selection_scope") or {}
    days = scope.get("window_dates") or []
    if (scope.get("rule_version") != MA5_SELECTION_RULE_VERSION
            or scope.get("kind") != "full_market" or not days or days[-1] != day
            or scope.get("universe_count") != len(value["audits"])):
        raise ValueError("Scan date, rule or complete audit count does not match")


def _documents(day: str, entry: dict) -> tuple[list[dict], dict]:
    _validate_entry(day, entry)
    signature, value = entry["signature"], entry["value"]
    snapshot = _snapshot_id(day, signature)
    common = {"format_version": FORMAT_VERSION, "analysis_date": day,
              "rule_version": MA5_SELECTION_RULE_VERSION, "signature": signature}
    chunks: list[dict] = []

    def new_chunk():
        index = len(chunks)
        return {"_id": _chunk_id(snapshot, index), "type": "chunk", **common,
                "index": index, "audits": [], "value_hash": "0" * 64}

    def finish_chunk(chunk):
        chunk["value_hash"] = _hash(chunk["audits"])
        if len(BSON.encode(chunk)) > MAX_DOCUMENT_BYTES:
            raise ValueError("MA5 audit chunk exceeds document limit")
        chunks.append(chunk)

    chunk = new_chunk()
    size = len(BSON.encode(chunk))
    for audit in value["audits"]:
        # Exact BSON array-element size: document type + decimal index + NUL
        # + the embedded document. Avoid repeatedly encoding a growing array.
        audit_size = len(BSON.encode(audit))
        element_size = audit_size + len(str(len(chunk["audits"]))) + 2
        if size + element_size > MAX_DOCUMENT_BYTES:
            if not chunk["audits"]:
                raise ValueError("One audit exceeds document limit")
            finish_chunk(chunk)
            chunk = new_chunk()
            size = len(BSON.encode(chunk))
            element_size = audit_size + 3
            if size + element_size > MAX_DOCUMENT_BYTES:
                raise ValueError("One audit exceeds document limit")
        chunk["audits"].append(audit)
        size += element_size
    if chunk["audits"]:
        finish_chunk(chunk)
    manifest = {"_id": snapshot, "type": "manifest", **common, "status": "ready",
                "audit_count": len(value["audits"]), "chunk_count": len(chunks),
                "chunk_hashes": [row["value_hash"] for row in chunks],
                "value": {key: val for key, val in value.items() if key != "audits"},
                "value_hash": _hash(value),
                "created_at": datetime.now(timezone.utc).isoformat()}
    if len(BSON.encode(manifest)) > MAX_DOCUMENT_BYTES:
        raise ValueError("MA5 manifest exceeds document limit")
    return chunks, manifest


def _put_immutable(collection, document: dict) -> None:
    result = collection.update_one({"_id": document["_id"]},
                                   {"$setOnInsert": document}, upsert=True)
    if getattr(result, "upserted_id", None) == document["_id"]:
        return
    previous = collection.find_one({"_id": document["_id"]})
    if not previous:
        raise ValueError("Immutable snapshot write could not be verified")
    # Equal signatures must never overwrite a different calculation. Do not
    # promote an existing damaged document to the latest published snapshot.
    actual = {key: val for key, val in previous.items() if key != "created_at"}
    expected = {key: val for key, val in document.items() if key != "created_at"}
    if _hash(actual) != _hash(expected):
        raise ValueError("Immutable snapshot content conflict")


def save_scan_cache(store, day: str, entry: dict, before_publish: Callable[[], None]) -> dict:
    """Save one ``{signature, value}`` entry, then promote its ready head.

    Optional storage failures never replace the previous head or fail the main
    sector publication. Ownership callback errors, including LeaseLost, always
    propagate. The caller must pass the single scan slot, not its frame cache.
    """
    if not callable(before_publish):
        raise RuntimeError("MA5 snapshot publication requires an owned lease")
    try:
        chunks, manifest = _documents(day, entry)
    except Exception as error:
        return {"status": "skipped", "reason": "unsupported_scan", "error_type": type(error).__name__}

    before_publish()
    try:
        collection = store.collection(COLLECTION)
        for chunk in chunks:
            _put_immutable(collection, chunk)
        _put_immutable(collection, manifest)
    except Exception as error:
        return {"status": "skipped", "reason": "cache_write_failed", "error_type": type(error).__name__}

    # Recheck outside the optional-cache exception handler. An expired worker
    # may leave harmless immutable chunks, but cannot advance the latest head.
    before_publish()
    try:
        collection.update_one({"_id": _head_id(day)}, {"$set": {
            "type": "head", "format_version": FORMAT_VERSION, "analysis_date": day,
            "rule_version": MA5_SELECTION_RULE_VERSION, "signature": entry["signature"],
            "snapshot_id": manifest["_id"], "value_hash": manifest["value_hash"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}, upsert=True)
    except Exception as error:
        return {"status": "skipped", "reason": "head_write_failed", "error_type": type(error).__name__}
    return {"status": "ready", "id": manifest["_id"], "signature": entry["signature"],
            "rule_version": MA5_SELECTION_RULE_VERSION, "analysis_date": day,
            "audit_count": manifest["audit_count"], "chunk_count": manifest["chunk_count"]}


def load_scan_cache(store, day: str) -> dict | None:
    """Load the latest complete snapshot, or fall back to a normal daily scan.

    The returned input signature still requires verification by
    ``reuse_daily_computation`` against the actual inputs of this generation.
    """
    try:
        if date.fromisoformat(day).isoformat() != day:
            return None
        collection = store.collection(COLLECTION)
        head = collection.find_one({"_id": _head_id(day), "type": "head"})
        if (not head or head.get("format_version") != FORMAT_VERSION
                or head.get("analysis_date") != day or head.get("rule_version") != MA5_SELECTION_RULE_VERSION):
            return None
        signature = head.get("signature") or ""
        if not _SIGNATURE.fullmatch(signature) or head.get("snapshot_id") != _snapshot_id(day, signature):
            return None
        manifest = collection.find_one({"_id": head["snapshot_id"], "type": "manifest"})
        if not manifest or manifest.get("status") != "ready":
            return None
        common = {"format_version": FORMAT_VERSION, "analysis_date": day,
                  "rule_version": MA5_SELECTION_RULE_VERSION, "signature": signature}
        if any(manifest.get(key) != val for key, val in common.items()):
            return None
        hashes = manifest.get("chunk_hashes")
        if (not isinstance(hashes, list) or manifest.get("chunk_count") != len(hashes)
                or len(hashes) > 1024 or len(BSON.encode(manifest)) > MAX_DOCUMENT_BYTES):
            return None
        audits = []
        for index, expected_hash in enumerate(hashes):
            chunk = collection.find_one({"_id": _chunk_id(manifest["_id"], index), "type": "chunk"})
            if (not chunk or any(chunk.get(key) != val for key, val in common.items())
                    or chunk.get("index") != index or not isinstance(chunk.get("audits"), list)
                    or chunk.get("value_hash") != expected_hash
                    or _hash(chunk["audits"]) != expected_hash
                    or len(BSON.encode(chunk)) > MAX_DOCUMENT_BYTES):
                return None
            audits.extend(chunk["audits"])
        if len(audits) != manifest.get("audit_count"):
            return None
        value = {**manifest["value"], "audits": audits}
        if _hash(value) != manifest.get("value_hash") or head.get("value_hash") != manifest["value_hash"]:
            return None
        entry = {"signature": signature, "value": value}
        _validate_entry(day, entry)
        return entry
    except Exception:
        # Cache availability and corruption must not turn a valid computation
        # into a failed trading-research generation.
        return None
