"""Archive and remove one verified Feishu bot session's native Codex indexes.

The live Mongo session and completed storage cutover establish ownership. Only
rows keyed by that exact session UUID are exported; the archive stays in memory
until transferred to ECS. Deletion starts after its remote SHA-256 is verified.
No credential, rollout, other chat, or whole SQLite database is copied.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import tarfile
from typing import Any
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pymongo import MongoClient, timeout
from shilun.common.config import load_config


# Children precede their parent. Spawn edges are metadata, never child chats.
STATE_TABLES = (
    ("thread_dynamic_tools", ("thread_id",)),
    ("thread_attachments", ("thread_id",)),
    ("guardian_review_feedback", ("thread_id",)),
    ("thread_read_receipts", ("thread_id",)),
    ("thread_spawn_edges", ("parent_thread_id", "child_thread_id")),
    ("threads", ("id",)),
)
PLANS = (("main", STATE_TABLES), ("bot_logs", (("logs", ("thread_id",)),)))


class MigrationError(RuntimeError):
    """Fixed safe reason, containing no row data or connection details."""


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"sqlite_type": "blob", "base64": base64.b64encode(value).decode("ascii")}
    return value


def predicate(columns: tuple[str, ...]) -> str:
    return " OR ".join(quote_identifier(column) + " = ?" for column in columns)


def ssh_arguments() -> list[str]:
    return [
        "ssh", "-i", str(Path.home() / ".ssh/shilun_ecs_management_ed25519"), "-F", "/dev/null",
        "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=" + str(Path.home() / ".ssh/known_hosts"), "-o", "ConnectTimeout=10",
        "root@47.110.33.132",
    ]


def verify_archived_session(app_id: str, session_id: str, migration: dict[str, Any]) -> bool:
    """Read the checksum-pinned original bot SQLite archive entirely in RAM."""
    path, digest = migration.get("archive_path"), migration.get("archive_sha256")
    if not isinstance(path, str) or not re.fullmatch(
            r"/data/feishu-codex/archive/[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\.tar\.gz", path):
        raise MigrationError("ownership_archive_path_invalid")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise MigrationError("ownership_archive_hash_invalid")
    limit = 100 * 1024 * 1024
    # Bound the remote output as well as the local buffer; no archive is written
    # to a local temporary file, and oversized archives cannot prove ownership.
    fetched = subprocess.run(
        ssh_arguments() + ["head -c " + str(limit + 1) + " -- " + shlex.quote(path)],
        capture_output=True, timeout=60)
    if fetched.returncode or len(fetched.stdout) > limit:
        raise MigrationError("ownership_archive_read_failed")
    if hashlib.sha256(fetched.stdout).hexdigest() != digest:
        raise MigrationError("ownership_archive_hash_mismatch")
    member_name = "runtime/feishu-codex-" + app_id + ".sqlite3"
    with tarfile.open(fileobj=io.BytesIO(fetched.stdout), mode="r:gz") as archive:
        matches = [member for member in archive.getmembers() if member.name == member_name]
        if len(matches) != 1 or not matches[0].isfile() or matches[0].size > limit:
            raise MigrationError("ownership_archive_database_invalid")
        stream = archive.extractfile(matches[0])
        if stream is None:
            raise MigrationError("ownership_archive_database_missing")
        with stream:
            database_bytes = stream.read(limit + 1)
        if len(database_bytes) > limit:
            raise MigrationError("ownership_archive_database_exceeds_limit")
    connection = sqlite3.connect(":memory:")
    try:
        if database_bytes[:16] != b"SQLite format 3\x00":
            raise MigrationError("ownership_archive_database_invalid")
        # The migration checkpointed and truncated WAL before archiving. Only
        # the in-memory copy needs rollback-mode header flags for deserialize;
        # otherwise SQLite tries to open a WAL alongside its in-memory image.
        if database_bytes[18:20] == b"\x02\x02":
            database_bytes = database_bytes[:18] + b"\x01\x01" + database_bytes[20:]
        connection.deserialize(database_bytes)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        return connection.execute("SELECT 1 FROM sessions WHERE session_id = ? LIMIT 1",
                                  (session_id,)).fetchone() is not None
    finally:
        connection.close()


def verify_ownership(app_id: str, session_id: str) -> None:
    """Read only: do not acquire the production worker's lease or write Mongo."""
    settings = load_config()
    client = MongoClient(settings.mongo_uri, connectTimeoutMS=2000,
                         socketTimeoutMS=3000, serverSelectionTimeoutMS=3000)
    try:
        with timeout(8):
            database = client[settings.mongo_db]
            session = database.feishu_codex_sessions.find_one(
                {"app_id": app_id, "session_id": session_id}, {"_id": 1})
            answer = None if session else database.feishu_codex_inbox.find_one(
                {"app_id": app_id, "answer_session_id": session_id}, {"_id": 1})
            migration = database.feishu_codex_logs.find_one(
                {"app_id": app_id, "status": "local_storage_migrated", "session_count": {"$gte": 1}},
                {"logged_at": 1, "archive_path": 1, "archive_sha256": 1}, sort=[("logged_at", -1)])
            if not migration or not isinstance(migration.get("logged_at"), datetime):
                raise MigrationError("session_ownership_not_verified")
            online = database.feishu_codex_logs.find_one(
                {"app_id": app_id, "status": "connected", "logged_at": {"$gte": migration["logged_at"]}},
                {"_id": 1})
            if not online:
                raise MigrationError("cloud_storage_cutover_not_verified")
        if not session and not answer and not verify_archived_session(app_id, session_id, migration):
            raise MigrationError("session_ownership_not_verified")
    finally:
        client.close()


def connect_native(*, readonly: bool) -> sqlite3.Connection:
    base = Path.home() / ".codex"
    state, logs = base / "state_5.sqlite", base / "logs_2.sqlite"
    if not state.is_file() or not logs.is_file():
        raise MigrationError("native_database_missing")
    mode = "ro" if readonly else "rw"
    connection = sqlite3.connect(state.as_uri() + "?mode=" + mode, uri=True, timeout=5,
                                 isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("ATTACH DATABASE ? AS bot_logs", (logs.as_uri() + "?mode=" + mode,))
        return connection
    except BaseException:
        connection.close()
        raise


def snapshot(connection: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    records: dict[str, Any] = {}
    known = {name for name, _columns in STATE_TABLES}
    tables = connection.execute("SELECT name FROM main.sqlite_master WHERE type = 'table'").fetchall()
    for table in tables:
        name = table[0]
        for foreign in connection.execute("PRAGMA main.foreign_key_list(" + quote_identifier(name) + ")"):
            if foreign[2] == "threads" and name not in known:
                count = connection.execute(
                    "SELECT count(*) FROM main." + quote_identifier(name) + " WHERE " +
                    quote_identifier(foreign[3]) + " = ?", (session_id,)).fetchone()[0]
                if count:
                    raise MigrationError("unknown_thread_dependency")
    thread_columns = {row[1] for row in connection.execute("PRAGMA main.table_info(threads)")}
    if "attached_thread_parent_id" in thread_columns:
        if connection.execute(
                "SELECT 1 FROM main.threads WHERE attached_thread_parent_id = ? AND id != ? LIMIT 1",
                (session_id, session_id)).fetchone():
            raise MigrationError("another_chat_references_session")
    for database, plans in PLANS:
        for table, columns in plans:
            schema = connection.execute(
                "SELECT sql FROM " + quote_identifier(database) + ".sqlite_master WHERE type = 'table' AND name = ?",
                (table,)).fetchone()
            if schema is None:
                raise MigrationError("native_schema_changed")
            actual_columns = tuple(row[1] for row in connection.execute(
                "PRAGMA " + quote_identifier(database) + ".table_info(" + quote_identifier(table) + ")"))
            if not set(columns) <= set(actual_columns):
                raise MigrationError("native_schema_changed")
            rows = connection.execute(
                "SELECT * FROM " + quote_identifier(database) + "." + quote_identifier(table) +
                " WHERE " + predicate(columns), (session_id,) * len(columns)).fetchall()
            encoded = [{key: json_value(row[key]) for key in row.keys()} for row in rows]
            encoded.sort(key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
            records[database + "." + table] = {
                "schema": schema[0], "columns": actual_columns, "rows": encoded,
            }
    if len(records["main.threads"]["rows"]) > 1:
        raise MigrationError("unexpected_thread_identity")
    return records


def upload_archive(app_id: str, session_id: str, records: dict[str, Any]) -> tuple[str, str]:
    document = {"format": "feishu-native-index-v1", "app_id": app_id, "session_id": session_id,
                "created_at": datetime.now(timezone.utc).isoformat(), "tables": records}
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    if len(payload) > 25 * 1024 * 1024:
        raise MigrationError("archive_exceeds_limit")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("native-index/" + session_id + ".json")
        info.size, info.mode = len(payload), 0o600
        archive.addfile(info, io.BytesIO(payload))
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    destination = "/data/feishu-codex/archive/" + stamp + "-native-index-" + uuid.uuid4().hex + ".tar.gz"
    command = (
        "umask 077; mkdir -p /data/feishu-codex/archive && "
        "chmod 700 /data/feishu-codex /data/feishu-codex/archive && "
        "set -C; cat > " + shlex.quote(destination) + " && sha256sum " + shlex.quote(destination)
    )
    result = subprocess.run(ssh_arguments() + [command], input=data, capture_output=True, timeout=60)
    expected = digest + "  " + destination
    if result.returncode or result.stdout.decode("ascii", errors="replace").strip() != expected:
        raise MigrationError("server_archive_verification_failed")
    return destination, digest


def migrate(app_id: str, session_id: str) -> dict[str, Any]:
    verify_ownership(app_id, session_id)
    reader = connect_native(readonly=True)
    try:
        reader.execute("BEGIN")
        records = snapshot(reader, session_id)
        reader.execute("ROLLBACK")
    finally:
        reader.close()
    counts = {table: len(record["rows"]) for table, record in records.items()}
    if not any(counts.values()):
        return {"counts": counts, "archive_path": None, "archive_sha256": None}
    destination, digest = upload_archive(app_id, session_id, records)
    # An archive upload may take time. Recheck ownership and every exact row;
    # changed rows are retained locally rather than deleted from a stale copy.
    verify_ownership(app_id, session_id)
    writer = connect_native(readonly=False)
    try:
        writer.execute("BEGIN IMMEDIATE")
        if snapshot(writer, session_id) != records:
            raise MigrationError("native_rows_changed_after_archive")
        for database, plans in PLANS:
            for table, columns in plans:
                cursor = writer.execute(
                    "DELETE FROM " + quote_identifier(database) + "." + quote_identifier(table) +
                    " WHERE " + predicate(columns), (session_id,) * len(columns))
                if cursor.rowcount != counts[database + "." + table]:
                    raise MigrationError("native_delete_count_mismatch")
        writer.execute("COMMIT")
    except BaseException:
        if writer.in_transaction:
            writer.execute("ROLLBACK")
        raise
    finally:
        writer.close()
    return {"counts": counts, "archive_path": destination, "archive_sha256": digest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"cli_[A-Za-z0-9]+", args.app_id):
        parser.error("Invalid application id.")
    try:
        session_id = str(uuid.UUID(args.session_id))
    except ValueError:
        parser.error("Invalid session UUID.")
    if not args.apply:
        parser.error("Pass --apply to archive and remove the verified native index rows.")
    try:
        result = migrate(args.app_id, session_id)
    except Exception as exc:
        result = {"error_type": type(exc).__name__}
        if isinstance(exc, MigrationError):
            result["reason"] = str(exc)
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
