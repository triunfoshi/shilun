"""Move this bot's local records to ECS, then enable Mongo-only persistence.

Run explicitly with --apply. Credential files and unrelated Codex chats are not
included. Local source records are removed only after verified server archival,
Mongo import, and an actual connected event from the restarted service.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import sqlite3
import subprocess
import sys
import tarfile
import time
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shilun.common.config import load_config
from shilun.feishu_codex_store import RemoteMongoStore
from pymongo import MongoClient


def launch(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, timeout=25)


def migrate(app_id: str) -> dict:
    if not re.fullmatch(r"cli_[A-Za-z0-9]+", app_id):
        raise ValueError("invalid application id")
    domain = f"gui/{os.getuid()}"
    label = domain + "/cn.shilun.feishu-sdk"
    installed = Path.home() / "Library/LaunchAgents/cn.shilun.feishu-sdk.plist"
    template = ROOT / "deploy/cn.shilun.feishu-sdk.plist"
    config = plistlib.loads(template.read_bytes())
    assert config["Label"] == "cn.shilun.feishu-sdk"
    assert config["StandardOutPath"] == config["StandardErrorPath"] == "/dev/null"
    assert "--auto-reply" in config["ProgramArguments"]
    environment = ROOT / ".env"
    old_environment, old_service = environment.read_bytes(), installed.read_bytes()
    db_path = ROOT / "runtime" / f"feishu-codex-{app_id}.sqlite3"
    store = None
    committed = False
    stage = "stop_service"
    started = datetime.now(timezone.utc)
    try:
        state = launch("print", label)
        previous_pid = re.search(rb"\bpid = (\d+)", state.stdout)
        if state.returncode == 0 and launch("bootout", label).returncode:
            raise RuntimeError("cannot stop bot service")
        # Wait for the old worker to finish its bounded shutdown before reading
        # or removing any of its files. launchd deregistration is asynchronous.
        if previous_pid:
            deadline = time.monotonic() + 20
            while True:
                try:
                    os.kill(int(previous_pid[1]), 0)
                except ProcessLookupError:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("old bot service did not stop")
                time.sleep(0.25)
        if not db_path.is_file():
            raise RuntimeError("local bot database is missing")
        stage = "read_local_records"
        local = sqlite3.connect(db_path)
        local.row_factory = sqlite3.Row
        try:
            local.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            messages = [dict(row) for row in local.execute("SELECT * FROM inbox")]
            sessions = [dict(row) for row in local.execute("SELECT * FROM sessions")]
        finally:
            local.close()
        for row in messages:
            if isinstance(row.get("usage"), str):
                row["usage"] = json.loads(row["usage"])
        paths = sorted((ROOT / "runtime").glob(f"feishu-codex-{app_id}.*"))
        paths += [ROOT / "runtime/feishu-sdk.log", ROOT / "runtime/feishu-sdk.error.log"]
        sources: list[tuple[Path, str]] = [(path, str(path.relative_to(ROOT))) for path in paths if path.is_file()]
        for row in sessions:
            identifier = str(uuid.UUID(row["session_id"]))
            for path in (Path.home() / ".codex/sessions").glob(f"*/*/*/*{identifier}*.jsonl"):
                sources.append((path, "codex-rollouts/" + path.name))
        snapshot = io.BytesIO()
        with tarfile.open(fileobj=snapshot, mode="w:gz") as archive:
            for path, name in sources:
                assert path.name != "auth.json" and name.endswith((".log", ".sqlite3", ".lock", ".jsonl", "-wal", "-shm"))
                archive.add(path, arcname=name, recursive=False)
        archive_data = snapshot.getvalue()
        if len(archive_data) > 100 * 1024 * 1024:
            raise RuntimeError("archive exceeds migration limit")
        stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
        destination = f"/data/feishu-codex/archive/{stamp}-{uuid.uuid4().hex[:8]}.tar.gz"
        digest = hashlib.sha256(archive_data).hexdigest()
        stage = "archive_on_ecs"
        command = (
            "umask 077; mkdir -p /data/feishu-codex/archive && chmod 700 /data/feishu-codex /data/feishu-codex/archive && "
            "set -C; cat > " + shlex.quote(destination) + " && sha256sum " + shlex.quote(destination)
        )
        uploaded = subprocess.run([
            "ssh", "-i", str(Path.home() / ".ssh/shilun_ecs_management_ed25519"), "-F", "/dev/null",
            "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile=" + str(Path.home() / ".ssh/known_hosts"), "-o", "ConnectTimeout=10",
            "root@47.110.33.132", command,
        ], input=archive_data, capture_output=True, timeout=60)
        if uploaded.returncode or uploaded.stdout.decode("ascii").split()[0] != digest:
            raise RuntimeError("server archive verification failed")
        stage = "import_mongo"
        store = RemoteMongoStore(app_id)
        counts = store.import_history(messages, sessions)
        for row in messages:
            copied = store.inbox.find_one({"app_id": app_id, "message_id": row["message_id"]})
            if not copied or any(copied.get(key) != row.get(key) for key in ("text", "reply", "chat_id", "sender_id")):
                raise RuntimeError("server message verification failed")
        store.log({"status": "local_storage_migrated", "message_count": len(messages),
                   "session_count": len(sessions), "archive_path": destination, "archive_sha256": digest})
        store.close()
        store = None
        stage = "enable_mongo_only"
        lines = old_environment.decode("utf-8").splitlines()
        lines = [line for line in lines if not re.match(r"\s*SHILUN_FEISHU_STORE\s*=", line)]
        environment.write_text("\n".join(lines) + "\nSHILUN_FEISHU_STORE=mongo\n", encoding="utf-8")
        environment.chmod(0o600)
        installed.write_bytes(template.read_bytes())
        installed.chmod(0o644)
        if launch("bootstrap", domain, str(installed)).returncode:
            time.sleep(2)
            if launch("print", label).returncode and launch("bootstrap", domain, str(installed)).returncode:
                raise RuntimeError("cannot start mongo-backed service")
        stage = "verify_connected"
        settings = load_config()
        client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
        try:
            collection = client[settings.mongo_db]["feishu_codex_logs"]
            deadline = time.monotonic() + 45
            online = None
            while time.monotonic() < deadline:
                online = collection.find_one({"app_id": app_id, "status": "connected", "logged_at": {"$gte": started}})
                if online:
                    break
                time.sleep(1)
            if not online:
                raise RuntimeError("new service did not report a connected event")
        finally:
            client.close()
        committed = True
        stage = "remove_verified_local_copies"
        for path, _name in sources:
            path.unlink(missing_ok=True)
        return {"ok": True, "archive_path": destination, "archive_sha256": digest,
                "archive_files": len(sources), "messages": len(messages), "sessions": len(sessions),
                "imported": counts, "service_pid": online.get("pid"), "local_copies_removed": True}
    except Exception as exc:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
        if not committed:
            launch("bootout", label)
            environment.write_bytes(old_environment)
            installed.write_bytes(old_service)
            time.sleep(2)
            launch("bootstrap", domain, str(installed))
        return {"ok": False, "stage": stage, "error_type": type(exc).__name__, "cutover_committed": committed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        parser.error("Pass --apply to perform the authorized migration.")
    result = migrate(args.app_id)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
