"""Private native Mongo archive and isolated recovery proof for the journal.

Uses the existing configured database and journal lease. No production restore,
account mutation, user/role export or schedule installation is exposed here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import traceback
import uuid
from urllib.parse import urlsplit

from pymongo.errors import OperationFailure

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.jobs.trade_journal_import_job import protected_state_hashes
from shilun.trade_journal.service import TradeJournalService


VERSION = "shilun-private-mongo-recovery-v1"
ARCHIVE = "database.archive.gz"
MANIFEST = "manifest.json"
GUARD = "__shilun_recovery_guard"


class BackupError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _private_file(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)


def _save(path, payload):
    _private_file(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _failure(error):
    # Debuggable boundaries without serializing exception messages/arguments,
    # which PyMongo and subprocess errors can populate with connection secrets.
    return {"error_type": type(error).__name__, "frames": [
        {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
        for frame in traceback.extract_tb(error.__traceback__)[-8:]]}


def _permission_failure(error):
    for _ in range(8):
        if isinstance(error, OperationFailure) and error.code == 13:
            return True
        error = error.__cause__ if error is not None else None
        if error is None:
            break
    return False


def _isolated_uri(uri):
    """Require an explicitly authenticated local peer on a dedicated port."""
    try:
        if not isinstance(uri, str) or any(ord(char) < 32 for char in uri):
            raise ValueError
        parsed = urlsplit(uri)
        if (parsed.scheme != "mongodb" or parsed.hostname != "127.0.0.1" or parsed.fragment
            or not parsed.username or not parsed.password or parsed.port is None
            or not 1 <= parsed.port <= 65535 or parsed.port in {27017, 27018}):
            raise ValueError
        return parsed.hostname, parsed.port
    except (TypeError, ValueError):
        raise BackupError("独立恢复连接须为带认证的mongodb://127.0.0.1及明确专用端口，禁止27017/27018；未回显连接信息") from None


def _target_uri_file(path, *, source_uri):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "r") as handle:
            information = os.fstat(handle.fileno())
            if (not stat.S_ISREG(information.st_mode) or stat.S_IMODE(information.st_mode) != 0o600
                or information.st_nlink != 1 or information.st_size > 65536):
                raise ValueError
            payload = json.load(handle)
        if not isinstance(payload, dict) or set(payload) != {"uri"}:
            raise ValueError
        uri = payload["uri"]
        peer = _isolated_uri(uri)
        source = urlsplit(source_uri)
        if peer == (source.hostname, source.port or 27017):
            raise ValueError
        return uri
    except Exception:
        raise BackupError("目标连接文件须为无链接的0600私有JSON {uri:...}，并明确使用不同的本机隔离实例；未回显连接信息") from None


def _check_private(path, *, directory=False):
    if path.is_symlink() or (not path.is_dir() if directory else not path.is_file()):
        raise BackupError("备份路径须为普通私有目录/文件，不能是链接")
    if path.stat().st_mode & 0o077:
        raise BackupError("备份目录须为0700、文件须为0600，不能供其他用户读取")


def _tool(name):
    path = shutil.which(name)
    if not path:
        raise BackupError(f"缺少官方Mongo Database Tools：{name}；未尝试安装或使用替代格式")
    return path


def _database_sizes(store):
    try:
        raw = store.db.command("dbStats", scale=1)
        sizes = {key: int(raw[key]) for key in ("dataSize", "indexSize", "storageSize")}
        if any(value < 0 for value in sizes.values()):
            raise ValueError
        if "fsTotalSize" in raw and "fsUsedSize" in raw:
            total, used = raw["fsTotalSize"], raw["fsUsedSize"]
            # An unallocated database can report 0/0; this is unknown capacity.
            if total > 0 and 0 <= used <= total:
                sizes["filesystem_available_bytes"] = int(total - used)
        return sizes
    except Exception as error:
        raise BackupError("无法核实数据库容量；归档/恢复未开始，未回显连接信息") from error


def _run_tool(arguments, *, uri, directory, log_name, timeout, heartbeat=None):
    """Never place credentials in argv or return tool output to a caller."""
    config = directory / (".mongo-tools-" + uuid.uuid4().hex + ".yml")
    _private_file(config, "uri: " + json.dumps(uri) + "\n")
    log_path = directory / log_name
    try:
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as log:
            process = subprocess.Popen([arguments[0], "--config=" + str(config), *arguments[1:]],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        raise BackupError("Mongo归档/恢复超过时间预算；私有日志保留，未宣称成功")
                    if heartbeat:
                        heartbeat()
                    time.sleep(0.25)
                if process.returncode:
                    raise BackupError("Mongo归档/恢复失败；请在私有目录检查日志，未回显凭据或文档")
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                raise
    finally:
        # Only this invocation's generated credential file is removed.
        config.unlink(missing_ok=True)


def create_backup(store, *, uri, directory, timeout=1800, runner=_run_tool):
    from shilun.trade_journal.recovery import capture_journal_manifest, verify_journal_manifest
    executable = _tool("mongodump") if runner is _run_tool else "mongodump"
    directory = Path(directory).absolute()
    if directory.exists() or directory.is_symlink():
        raise BackupError("请使用新的备份目录，不覆盖旧归档或审计证据")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", store.db.name) or store.db.name in {"admin", "local", "config"}:
        raise BackupError("只备份已配置的业务数据库")
    if not directory.parent.is_dir():
        raise BackupError("备份父目录不存在")
    sizes = _database_sizes(store)
    required = max(512 * 1024 * 1024, int(sizes["dataSize"] * 1.1))
    if shutil.disk_usage(directory.parent).free < required:
        raise BackupError("本地可用空间不足数据库未压缩数据量及安全余量，停止创建备份")
    directory.mkdir(mode=0o700)
    journal = TradeJournalService(store)
    identity = uuid.uuid4().hex
    result = {"version": VERSION, "backup_id": identity, "source_database": store.db.name,
        "started_at": _now(), "status": "started", "archive_file": ARCHIVE,
        "consistency": "journal_shared_lease_and_exact_manifest; other_collections_logical_dump_not_point_in_time",
        "ephemeral_excluded_from_recovery": ["trade_journal_write_locks"],
        "scope": "whole_configured_business_database; no_database_users_or_roles", "source_sizes": sizes}
    try:
        protected = protected_state_hashes(store)
        with journal.write_guard():
            expected = capture_journal_manifest(store)
            if not expected.get("valid"):
                raise BackupError("原账本完整性核验失败，不能把有缺失的归档标为可恢复")
            journal.renew_write_lease()
            archive = directory / ARCHIVE
            _private_file(archive, "")
            runner([executable, "--db=" + store.db.name, "--archive=" + str(archive),
                "--gzip", "--numParallelCollections=1"], uri=uri, directory=directory,
                log_name="dump.private.log", timeout=timeout, heartbeat=journal.renew_write_lease)
            check = verify_journal_manifest(store, expected)
            if not check.get("valid"):
                raise BackupError("归档窗口内账本发生变化，不能将此次归档标为稳定截止版本")
            journal.renew_write_lease()
            if not archive.is_file() or archive.stat().st_size < 16:
                raise BackupError("Mongo未生成有效大小的归档")
            _check_private(archive)
            result.update(status="archived", journal=expected, completed_at=_now(),
                archive_sha256=_hash(archive), archive_bytes=archive.stat().st_size,
                production_before=protected, production_after=protected_state_hashes(store))
            result["production_unchanged"] = result["production_before"] == result["production_after"]
        _save(directory / MANIFEST, result)
        return result
    except BaseException as error:
        _save(directory / ("failed-" + identity + ".json"), {**result, "status": "failed", "finished_at": _now(),
            "diagnostic": _failure(error)})
        raise


def verify_bundle(directory):
    directory = Path(directory).absolute()
    _check_private(directory, directory=True)
    for name in (MANIFEST, ARCHIVE):
        _check_private(directory / name)
    try:
        manifest = json.loads((directory / MANIFEST).read_text())
    except (ValueError, OSError) as error:
        raise BackupError("备份manifest无法读取") from error
    if (manifest.get("version") != VERSION or manifest.get("status") != "archived"
        or manifest.get("archive_file") != ARCHIVE or not isinstance(manifest.get("journal"), dict)):
        raise BackupError("备份版本、状态或范围不受支持")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(manifest.get("source_database", ""))):
        raise BackupError("manifest数据库名无效")
    if (manifest.get("archive_bytes") != (directory / ARCHIVE).stat().st_size
        or manifest.get("archive_sha256") != _hash(directory / ARCHIVE)):
        raise BackupError("私有归档大小或SHA不一致；恢复未开始")
    return manifest


def restore_and_verify(store, *, uri, directory, database, timeout=1800, runner=_run_tool, restore_store=None):
    from shilun.trade_journal.recovery import verify_journal_manifest
    executable = _tool("mongorestore") if runner is _run_tool else "mongorestore"
    directory = Path(directory).absolute()
    manifest = verify_bundle(directory)
    if (not re.fullmatch(r"shilun_recovery_[a-z0-9_]{8,40}", str(database))
        or database in {store.db.name, manifest["source_database"]}):
        raise BackupError("只允许新的shilun_recovery_隔离验证库，禁止恢复到生产库")
    receipt = {"version": VERSION, "backup_id": manifest["backup_id"], "target_database": database,
        "archive_sha256": manifest["archive_sha256"], "started_at": _now(), "status": "started",
        "scope": "whole_database_restored; journal_counts_indexes_originals_references_and_fifo_verified",
        "automatic_cleanup": False, "stage": "preflight", "native_tool_started": False,
        "independent_target_instance": restore_store is not None}
    try:
        target_store = store if restore_store is None else restore_store
        if restore_store is not None:
            peer = _isolated_uri(uri)
            if target_store is store or target_store.client is store.client:
                raise BackupError("独立恢复目标必须使用不同的Mongo实例；禁止回退到源连接")
            source_address = getattr(store.client, "address", None)
            target_address = getattr(target_store.client, "address", None)
            if source_address is not None and (source_address == target_address or source_address == peer):
                raise BackupError("独立恢复目标与源Mongo实例相同；未启动恢复")
            if target_address is not None and target_address != peer:
                raise BackupError("独立恢复连接与目标store实例不一致；未启动恢复")
        sizes = _database_sizes(target_store)
        expected_size = manifest.get("source_sizes", {})
        required = int((expected_size.get("dataSize", 0) + expected_size.get("indexSize", 0)) * 1.2)
        if ("filesystem_available_bytes" in sizes and sizes["filesystem_available_bytes"] < required):
            raise BackupError("Mongo服务器可用空间不足隔离恢复和索引所需容量；恢复未开始")
        target = target_store.client[database]
        if database in target_store.client.list_database_names() or target.list_collection_names():
            raise BackupError("验证库已存在；不覆盖、不合并旧恢复结果")
        protected = protected_state_hashes(store)
        # Atomic claim: concurrent attempts cannot both use the same destination.
        target.create_collection(GUARD)
        target[GUARD].insert_one({"backup_id": manifest["backup_id"], "created_at": _now(),
            "purpose": "isolated_restore_verification", "production": False})
        receipt.update(stage="native_restore", native_tool_started=True)
        source = manifest["source_database"]
        runner([executable, "--archive=" + str(directory / ARCHIVE), "--gzip",
            "--nsInclude=" + source + ".*", "--nsFrom=" + source + ".*", "--nsTo=" + database + ".*",
            "--nsExclude=" + source + ".trade_journal_write_locks", "--stopOnError",
            "--numParallelCollections=1", "--numInsertionWorkersPerCollection=1"],
            uri=uri, directory=directory, log_name="restore-" + database + ".private.log", timeout=timeout)
        # Use the existing store owner, without initializing any production indexes.
        class RestoredStore:
            db = target
            client = target_store.client
            def collection(self, name):
                return self.db[name]
        receipt["stage"] = "journal_verification"
        check = verify_journal_manifest(RestoredStore(), manifest["journal"])
        receipt.update(status="verified" if check.get("valid") else "verification_failed", verification=check,
            finished_at=_now(), production_before=protected, production_after=protected_state_hashes(store))
        receipt["production_unchanged"] = receipt["production_before"] == receipt["production_after"]
        _save(directory / ("restore-" + database + ".json"), receipt)
        if not check.get("valid"):
            raise BackupError("隔离恢复后的原件、引用、索引或FIFO不一致；验证库与证据保留")
        return receipt
    except BaseException as error:
        failure = directory / ("restore-" + database + ".failed.json")
        if failure.exists() or failure.is_symlink():
            failure = directory / ("restore-" + database + ".failed-" + uuid.uuid4().hex + ".json")
        permission_denied = receipt["stage"] == "preflight" and _permission_failure(error)
        if permission_denied:
            receipt.update(failure_reason="mongo_permission_denied", mongo_error_code=13)
        _save(failure, {**receipt, "status": "failed", "finished_at": _now(), "diagnostic": _failure(error)})
        if permission_denied:
            raise BackupError("Mongo隔离恢复预检权限不足（code 13）；未启动恢复工具、未更改生产权限；检查私有失败回执") from None
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore-verify"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--database", help="仅用于新的隔离恢复库")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--receipt", type=Path)
    targets = parser.add_mutually_exclusive_group()
    targets.add_argument("--ssh-target", help="既有ECS的明确user@host；在服务器本机压缩/恢复，归档仍私有保存")
    targets.add_argument("--target-uri-file", type=Path,
        help="仅restore-verify：0600私有JSON连接文件，明确使用带认证的本机独立实例")
    parser.add_argument("--ssh-key", type=Path, help="既有管理私钥的路径；不读取或回显私钥内容")
    args = parser.parse_args()
    if not 10 <= args.timeout <= 7200:
        parser.error("时间预算须为10—7200秒")
    if bool(args.ssh_target) != bool(args.ssh_key):
        parser.error("远端工具须同时提供明确SSH目标和既有私钥路径")
    if args.target_uri_file and args.operation != "restore-verify":
        parser.error("--target-uri-file只用于restore-verify，不改变源数据库连接")
    runner = _run_tool
    if args.ssh_target:
        from shilun.jobs.mongo_backup_remote import remote_runner
        try:
            runner = remote_runner(args.ssh_target, args.ssh_key)
        except BackupError as error:
            parser.error(str(error))
    config = load_config()
    if not config.mongo_uri:
        parser.error("现有ECS Mongo未配置；不回退到本地数据库")
    store, restore_store = None, None
    try:
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, initialize_indexes=False, socket_timeout_ms=30000)
        tool_uri = config.mongo_uri
        if args.target_uri_file:
            if (not re.fullmatch(r"shilun_recovery_[a-z0-9_]{8,40}", str(args.database))
                or args.database == config.mongo_db):
                raise BackupError("只允许新的shilun_recovery_隔离验证库，禁止恢复到生产库")
            tool_uri = _target_uri_file(args.target_uri_file, source_uri=config.mongo_uri)
            restore_store = MongoSnapshotStore(tool_uri, args.database, initialize_indexes=False, socket_timeout_ms=30000)
        result = (create_backup(store, uri=config.mongo_uri, directory=args.directory, timeout=args.timeout, runner=runner)
            if args.operation == "backup" else restore_and_verify(store, uri=tool_uri,
                directory=args.directory, database=args.database, timeout=args.timeout, runner=runner,
                restore_store=restore_store))
        if args.receipt:
            _save(args.receipt, result)
        print(json.dumps({key: result.get(key) for key in ("version", "backup_id", "status", "archive_bytes",
            "archive_sha256", "target_database", "production_unchanged")}, ensure_ascii=False))
    except BackupError as error:
        # Domain messages never contain command stderr, URIs or document bodies.
        parser.exit(1, str(error) + "\n")
    except Exception:
        parser.exit(1, "数据库备份/恢复未完成；未回显连接信息，检查私有日志与失败回执\n")
    finally:
        if restore_store is not None:
            restore_store.close()
        if store is not None:
            store.close()


if __name__ == "__main__":
    main()
