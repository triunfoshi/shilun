"""Publish safe active-scheduler metadata without loading analytics libraries."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json


def publish(payload, *, client_factory=None):
    from pymongo import MongoClient
    from shilun.common.config import load_config

    config = load_config()
    if not config.mongo_uri:
        raise RuntimeError("SHILUN_MONGO_URI not set")
    client = (client_factory or MongoClient)(config.mongo_uri, serverSelectionTimeoutMS=10000,
                                           socketTimeoutMS=10000, connectTimeoutMS=10000)
    # Only known safe fields from the live scheduler, never argv/env or a URI.
    safe = {key: payload[key] for key in ("timezone", "location", "data_only", "release_fingerprint", "jobs") if key in payload}
    safe.update(dataset="cloud_scheduler_manifest", scope="active", status="running",
                updated_at=datetime.now(timezone.utc).isoformat())
    try:
        client[config.mongo_db]["sync_state"].update_one(
            {"dataset": "cloud_scheduler_manifest", "scope": "active"},
            {"$set": safe}, upsert=True)
    finally:
        client.close()
    return safe


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True)
    args = parser.parse_args(argv)
    try:
        publish(json.loads(args.payload))
    except Exception as error:
        print(f"scheduler_manifest failed error_type={type(error).__name__}", flush=True)
        return 1
    print("scheduler_manifest published scope=active", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
