"""Operational boundaries around native archive/recovery, without live DBs."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from pymongo.errors import OperationFailure

from shilun.jobs import mongo_backup_job as job
from test_trade_journal import FakeStore


class Target:
    def __init__(self, name):
        self.name, self.collections = name, {}

    def list_collection_names(self):
        return list(self.collections)

    def command(self, *args, **kwargs):
        return {"dataSize": 1024, "indexSize": 512, "storageSize": 2048,
            "fsTotalSize": 1000000000, "fsUsedSize": 1000000}

    def create_collection(self, name):
        if name in self.collections:
            raise RuntimeError("destination already claimed")
        self.collections[name] = SimpleNamespace(insert_one=lambda row: None)

    def __getitem__(self, name):
        return self.collections[name]


class Client:
    def __init__(self):
        self.targets = {}
        self.existing = []

    def list_database_names(self):
        return self.existing

    def __getitem__(self, name):
        return self.targets.setdefault(name, Target(name))


@pytest.fixture
def environment(monkeypatch):
    from shilun.trade_journal import recovery
    store = FakeStore()
    store.db, store.client = Target("shilun"), Client()
    monkeypatch.setattr(job, "_tool", lambda name: "/official/" + name)
    manifest = {"valid": True, "version": "test-journal", "collections": {}, "ledger_digest": "fixed"}
    monkeypatch.setattr(recovery, "capture_journal_manifest", lambda store: manifest)
    monkeypatch.setattr(recovery, "verify_journal_manifest", lambda store, expected: {"valid": True, "errors": []})
    calls = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        if args[0].endswith("mongodump"):
            Path(next(a.split("=", 1)[1] for a in args if a.startswith("--archive="))).write_bytes(b"private archive" * 5)
            kwargs["heartbeat"]()
    return store, runner, calls


def backup(tmp_path, environment):
    store, runner, _ = environment
    directory = tmp_path / "private"
    result = job.create_backup(store, uri="mongodb://secret", directory=directory, runner=runner)
    return directory, result


def test_backup_reuses_lease_preserves_production_and_contains_whole_database(tmp_path, environment):
    directory, result = backup(tmp_path, environment)
    store, _, calls = environment
    assert result["status"] == "archived" and result["production_unchanged"]
    assert "not_point_in_time" in result["consistency"]
    assert all(name.startswith("trade_journal_") for name, *_ in store.writes)
    assert "--db=shilun" in calls[0][0] and "--collection" not in " ".join(calls[0][0])
    assert directory.stat().st_mode & 0o077 == 0
    assert (directory / job.ARCHIVE).stat().st_mode & 0o077 == 0
    assert (directory / job.MANIFEST).stat().st_mode & 0o077 == 0
    assert "mongodb://secret" not in (directory / job.MANIFEST).read_text()


def test_backup_never_overwrites_existing_bundle(tmp_path, environment):
    directory, _ = backup(tmp_path, environment)
    before = (directory / job.ARCHIVE).read_bytes()
    with pytest.raises(job.BackupError, match="新.*目录"):
        job.create_backup(environment[0], uri="private", directory=directory, runner=environment[1])
    assert (directory / job.ARCHIVE).read_bytes() == before


@pytest.mark.parametrize("target", [None, "shilun", "admin", "shilun_recovery_*", "shilun_recovery_a", "otherdb"])
def test_restore_refuses_production_or_unbounded_destinations(tmp_path, environment, target):
    directory, _ = backup(tmp_path, environment)
    with pytest.raises(job.BackupError, match="隔离"):
        job.restore_and_verify(environment[0], uri="private", directory=directory, database=target, runner=environment[1])
    assert environment[0].client.targets == {}


def test_modified_archive_blocks_before_database_creation(tmp_path, environment):
    directory, _ = backup(tmp_path, environment)
    with (directory / job.ARCHIVE).open("ab") as handle:
        handle.write(b"damage")
    with pytest.raises(job.BackupError, match="SHA"):
        job.restore_and_verify(environment[0], uri="private", directory=directory,
            database="shilun_recovery_20261029", runner=environment[1])
    assert environment[0].client.targets == {}


def test_restore_maps_only_source_business_database_to_new_claimed_namespace(tmp_path, environment):
    directory, original = backup(tmp_path, environment)
    store, runner, calls = environment
    result = job.restore_and_verify(store, uri="private", directory=directory,
        database="shilun_recovery_20261029", runner=runner)
    args = calls[-1][0]
    assert "--nsInclude=shilun.*" in args and "--nsFrom=shilun.*" in args
    assert "--nsTo=shilun_recovery_20261029.*" in args
    assert "--nsExclude=shilun.trade_journal_write_locks" in args
    assert not any(a.startswith(("--drop", "--oplogReplay", "--restoreDbUsersAndRoles")) for a in args)
    assert result["status"] == "verified" and result["production_unchanged"]
    assert result["backup_id"] == original["backup_id"]
    with pytest.raises(job.BackupError, match="已存在"):
        job.restore_and_verify(store, uri="private", directory=directory,
            database="shilun_recovery_20261029", runner=runner)


def test_bad_source_or_changed_journal_not_published_as_backup(tmp_path, environment, monkeypatch):
    from shilun.trade_journal import recovery
    monkeypatch.setattr(recovery, "capture_journal_manifest", lambda store: {"valid": False})
    with pytest.raises(job.BackupError, match="完整性"):
        job.create_backup(environment[0], uri="private", directory=tmp_path / "bad", runner=environment[1])
    assert not (tmp_path / "bad" / job.MANIFEST).exists()
    monkeypatch.setattr(recovery, "capture_journal_manifest", lambda store: {"valid": True})
    monkeypatch.setattr(recovery, "verify_journal_manifest", lambda store, expected: {"valid": False})
    with pytest.raises(job.BackupError, match="窗口"):
        job.create_backup(environment[0], uri="private", directory=tmp_path / "changed", runner=environment[1])
    assert not (tmp_path / "changed" / job.MANIFEST).exists()


def test_restored_mismatch_keeps_failed_evidence_and_does_not_claim_success(tmp_path, environment, monkeypatch):
    from shilun.trade_journal import recovery
    directory, _ = backup(tmp_path, environment)
    monkeypatch.setattr(recovery, "verify_journal_manifest", lambda store, expected: {"valid": False, "errors": ["missing"]})
    with pytest.raises(job.BackupError, match="不一致"):
        job.restore_and_verify(environment[0], uri="private", directory=directory,
            database="shilun_recovery_20261029", runner=environment[1])
    receipt = json.loads((directory / "restore-shilun_recovery_20261029.json").read_text())
    assert receipt["status"] == "verification_failed"
    assert environment[0].client.targets["shilun_recovery_20261029"].list_collection_names() == [job.GUARD]


def test_credentials_only_in_exclusive_private_config_and_removed_even_on_failure(tmp_path, monkeypatch):
    secret = "mongodb://private-user:private-password@127.0.0.1/db"
    def popen(args, **kwargs):
        assert all(secret not in arg and "private-password" not in arg for arg in args)
        config = Path(args[1].split("=", 1)[1])
        assert config.stat().st_mode & 0o077 == 0
        assert secret in config.read_text()
        return SimpleNamespace(poll=lambda: 2, returncode=2)
    monkeypatch.setattr(job.subprocess, "Popen", popen)
    with pytest.raises(job.BackupError) as error:
        job._run_tool(["mongodump", "--db=shilun"], uri=secret, directory=tmp_path,
            log_name="private.log", timeout=10)
    assert "private-password" not in str(error.value)
    assert not list(tmp_path.glob(".mongo-tools-*.yml"))


def test_public_or_symlink_bundle_is_rejected(tmp_path, environment):
    directory, _ = backup(tmp_path, environment)
    directory.chmod(0o755)
    with pytest.raises(job.BackupError, match="0700"):
        job.verify_bundle(directory)
    directory.chmod(0o700)
    link = tmp_path / "link"
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(job.BackupError, match="链接"):
        job.verify_bundle(link)


def test_backup_capacity_failure_does_not_create_bundle_or_acquire_write_lease(tmp_path, environment, monkeypatch):
    monkeypatch.setattr(job.shutil, "disk_usage", lambda path: SimpleNamespace(free=1024))
    with pytest.raises(job.BackupError, match="空间不足"):
        job.create_backup(environment[0], uri="private", directory=tmp_path / "too_large", runner=environment[1])
    assert not (tmp_path / "too_large").exists()
    assert environment[0].writes == []


def test_restore_capacity_failure_precedes_creating_isolated_database(tmp_path, environment, monkeypatch):
    directory, _ = backup(tmp_path, environment)
    monkeypatch.setattr(job, "_database_sizes", lambda store: {"filesystem_available_bytes": 1})
    with pytest.raises(job.BackupError, match="空间不足"):
        job.restore_and_verify(environment[0], uri="private", directory=directory,
            database="shilun_recovery_20261029", runner=environment[1])
    assert environment[0].client.targets == {}


ISOLATED_URI = "mongodb://restore-user:restore-private-secret@127.0.0.1:27119/?authSource=admin&directConnection=true"
DESTINATION = "shilun_recovery_20261030_independent"


def isolated_store():
    target = FakeStore()
    target.db, target.client = Target(DESTINATION), Client()
    target.client.address = ("127.0.0.1", 27119)
    return target


@pytest.mark.parametrize("filesystem", [
    {"fsTotalSize": 0, "fsUsedSize": 0},
    {"fsTotalSize": -1, "fsUsedSize": 0},
    {"fsTotalSize": 100, "fsUsedSize": -1},
    {"fsTotalSize": 100, "fsUsedSize": -0.5},
    {"fsTotalSize": 100, "fsUsedSize": 101},
    {"fsTotalSize": 0},
    {},
])
def test_unknown_or_invalid_filesystem_statistics_do_not_invent_available_space(filesystem):
    target = isolated_store()
    target.db.command = lambda *args, **kwargs: {
        "dataSize": 0, "indexSize": 0, "storageSize": 0, **filesystem}
    assert job._database_sizes(target) == {"dataSize": 0, "indexSize": 0, "storageSize": 0}


def test_unallocated_independent_target_does_not_fail_capacity_preflight(tmp_path, environment):
    directory, _ = backup(tmp_path, environment)
    source, runner, calls = environment
    target = isolated_store()
    target.db.command = lambda *args, **kwargs: {
        "dataSize": 0, "indexSize": 0, "storageSize": 0, "fsTotalSize": 0, "fsUsedSize": 0}
    result = job.restore_and_verify(source, uri=ISOLATED_URI, directory=directory,
        database=DESTINATION, runner=runner, restore_store=target)
    assert result["status"] == "verified" and result["native_tool_started"] is True
    assert target.client[DESTINATION].list_collection_names() == [job.GUARD]
    assert source.client.targets == {} and len(calls) == 2


@pytest.mark.parametrize("used", [99, 100])
def test_known_insufficient_independent_filesystem_still_blocks_before_claim(tmp_path, environment, used):
    directory, _ = backup(tmp_path, environment)
    source, runner, calls = environment
    target = isolated_store()
    target.db.command = lambda *args, **kwargs: {
        "dataSize": 0, "indexSize": 0, "storageSize": 0, "fsTotalSize": 100, "fsUsedSize": used}
    assert job._database_sizes(target)["filesystem_available_bytes"] == 100 - used
    with pytest.raises(job.BackupError, match="空间不足"):
        job.restore_and_verify(source, uri=ISOLATED_URI, directory=directory,
            database=DESTINATION, runner=runner, restore_store=target)
    assert target.client.targets == {} and source.client.targets == {} and len(calls) == 1
    receipt = json.loads((directory / ("restore-" + DESTINATION + ".failed.json")).read_text())
    assert receipt["stage"] == "preflight" and receipt["native_tool_started"] is False


def test_explicit_independent_target_uses_target_capacity_client_and_original_source_hashes(tmp_path, environment, monkeypatch):
    from shilun.trade_journal import recovery
    directory, original = backup(tmp_path, environment)
    source, runner, calls = environment
    target = isolated_store()
    capacity_reads, protected_reads, restored_reads = [], [], []
    original_protected = job.protected_state_hashes
    def capacity(store):
        capacity_reads.append(store)
        assert store is target
        return {"filesystem_available_bytes": 10**12}
    def protected(store):
        protected_reads.append(store)
        assert store is source
        return original_protected(store)
    def verify(store, manifest):
        restored_reads.append(store)
        assert store.client is target.client and store.db is target.client[DESTINATION]
        return {"valid": True, "errors": []}
    monkeypatch.setattr(job, "_database_sizes", capacity)
    monkeypatch.setattr(job, "protected_state_hashes", protected)
    monkeypatch.setattr(recovery, "verify_journal_manifest", verify)
    result = job.restore_and_verify(source, uri=ISOLATED_URI, directory=directory,
        database=DESTINATION, runner=runner, restore_store=target)
    assert capacity_reads == [target] and protected_reads == [source, source] and len(restored_reads) == 1
    assert source.client.targets == {} and target.client[DESTINATION].list_collection_names() == [job.GUARD]
    assert result["status"] == "verified" and result["production_unchanged"]
    assert result["independent_target_instance"] is True and result["backup_id"] == original["backup_id"]
    assert calls[-1][1]["uri"] == ISOLATED_URI
    assert ISOLATED_URI not in json.dumps(result)
    assert "restore-private-secret" not in (directory / ("restore-" + DESTINATION + ".json")).read_text()


def test_independent_capacity_failure_does_not_create_database_or_read_source_capacity(tmp_path, environment, monkeypatch):
    directory, _ = backup(tmp_path, environment)
    target = isolated_store()
    monkeypatch.setattr(job, "_database_sizes", lambda store: {"filesystem_available_bytes": 1} if store is target else pytest.fail("read source capacity"))
    with pytest.raises(job.BackupError, match="空间不足"):
        job.restore_and_verify(environment[0], uri=ISOLATED_URI, directory=directory,
            database=DESTINATION, runner=environment[1], restore_store=target)
    assert target.client.targets == {} and environment[0].client.targets == {}
    receipt = json.loads((directory / ("restore-" + DESTINATION + ".failed.json")).read_text())
    assert receipt["stage"] == "preflight" and receipt["native_tool_started"] is False


@pytest.mark.parametrize("boundary", ["capacity", "inventory", "claim"])
def test_preflight_permission_denied_is_readable_safe_and_saved_before_native_tool(tmp_path, environment, monkeypatch, boundary):
    directory, _ = backup(tmp_path, environment)
    source, runner, calls = environment
    def denied(*args, **kwargs):
        raise OperationFailure("authorization refused: " + ISOLATED_URI, code=13)
    if boundary == "capacity":
        monkeypatch.setattr(source.db, "command", denied)
    elif boundary == "inventory":
        monkeypatch.setattr(source.client, "list_database_names", denied)
    else:
        monkeypatch.setattr(Target, "create_collection", denied)
    with pytest.raises(job.BackupError, match="预检权限不足.*13") as error:
        job.restore_and_verify(source, uri=ISOLATED_URI, directory=directory,
            database=DESTINATION, runner=runner)
    assert "restore-private-secret" not in str(error.value)
    assert len(calls) == 1  # Only the preceding successful backup invoked a tool.
    receipt_path = directory / ("restore-" + DESTINATION + ".failed.json")
    receipt = json.loads(receipt_path.read_text())
    assert receipt["stage"] == "preflight" and receipt["native_tool_started"] is False
    assert receipt["mongo_error_code"] == 13 and receipt["failure_reason"] == "mongo_permission_denied"
    assert "restore-private-secret" not in receipt_path.read_text()
    assert receipt_path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("mode", ["same_store", "same_client", "same_address", "wrong_address"])
def test_independent_target_rejects_source_peer_or_uri_store_mismatch(tmp_path, environment, mode):
    directory, _ = backup(tmp_path, environment)
    source, runner, calls = environment
    target = isolated_store()
    if mode == "same_store":
        target = source
    elif mode == "same_client":
        target.client = source.client
    elif mode == "same_address":
        source.client.address = target.client.address
    else:
        target.client.address = ("127.0.0.1", 27120)
    with pytest.raises(job.BackupError, match="实例|连接"):
        job.restore_and_verify(source, uri=ISOLATED_URI, directory=directory,
            database=DESTINATION, runner=runner, restore_store=target)
    assert source.client.targets == {} and target.client.targets == {} and len(calls) == 1


@pytest.mark.parametrize("uri", [
    "mongodb://u:p@127.0.0.1:27017/", "mongodb://u:p@127.0.0.1:27018/", "mongodb://u:p@localhost:27119/",
    "mongodb://u:p@remote.example:27119/", "mongodb+srv://u:p@127.0.0.1/", "mongodb://127.0.0.1:27119/",
    "mongodb://u:p@127.0.0.1/", "mongodb://u:p@127.0.0.1:27119/#secret", "mongodb://u:p@127.0.0.1:27119/\n",
])
def test_independent_uri_file_never_falls_back_to_source_or_prints_invalid_values(tmp_path, uri):
    path = tmp_path / "target.json"
    path.write_text(json.dumps({"uri": uri}))
    path.chmod(0o600)
    with pytest.raises(job.BackupError) as error:
        job._target_uri_file(path, source_uri="mongodb://source:private@127.0.0.1:27018/shilun")
    assert uri not in str(error.value)


def test_private_target_uri_file_preserves_admin_auth_without_requiring_database_path(tmp_path):
    path = tmp_path / "target.json"
    path.write_text(json.dumps({"uri": ISOLATED_URI}))
    path.chmod(0o600)
    assert job._target_uri_file(path, source_uri="mongodb://source:private@127.0.0.1:27018/shilun") == ISOLATED_URI
    with pytest.raises(job.BackupError):
        job._target_uri_file(path, source_uri=ISOLATED_URI)
    path.chmod(0o644)
    with pytest.raises(job.BackupError):
        job._target_uri_file(path, source_uri="mongodb://localhost:27018/shilun")
    path.chmod(0o600)
    link = tmp_path / "target-link.json"
    link.symlink_to(path)
    with pytest.raises(job.BackupError):
        job._target_uri_file(link, source_uri="mongodb://localhost:27018/shilun")


def test_cli_separates_source_and_private_independent_target_and_keeps_credentials_out_of_output(tmp_path, monkeypatch, capsys):
    path = tmp_path / "target.json"
    path.write_text(json.dumps({"uri": ISOLATED_URI}))
    path.chmod(0o600)
    source_uri = "mongodb://source:source-private@127.0.0.1:27018/shilun"
    source, target = FakeStore(), isolated_store()
    closed, constructed, invoked = [], [], []
    source.close = lambda: closed.append("source")
    target.close = lambda: closed.append("target")
    def factory(uri, database, **kwargs):
        constructed.append((uri, database, kwargs))
        return source if uri == source_uri else target
    def restore(store, **kwargs):
        invoked.append((store, kwargs))
        return {"status": "verified", "target_database": DESTINATION, "production_unchanged": True}
    monkeypatch.setattr(job, "MongoSnapshotStore", factory)
    monkeypatch.setattr(job, "restore_and_verify", restore)
    monkeypatch.setattr(job, "load_config", lambda: SimpleNamespace(mongo_uri=source_uri, mongo_db="shilun"))
    monkeypatch.setattr(sys, "argv", ["mongo_backup_job", "restore-verify", "--directory", str(tmp_path),
        "--database", DESTINATION, "--target-uri-file", str(path)])
    job.main()
    assert constructed == [(source_uri, "shilun", {"initialize_indexes": False, "socket_timeout_ms": 30000}),
        (ISOLATED_URI, DESTINATION, {"initialize_indexes": False, "socket_timeout_ms": 30000})]
    assert invoked[0][0] is source and invoked[0][1]["restore_store"] is target
    assert invoked[0][1]["uri"] == ISOLATED_URI and invoked[0][1]["runner"] is job._run_tool
    assert closed == ["target", "source"]
    output = capsys.readouterr()
    assert "verified" in output.out and "private" not in output.out + output.err


@pytest.mark.parametrize("options", [
    ["backup", "--target-uri-file", "target.json"],
    ["restore-verify", "--target-uri-file", "target.json", "--ssh-target", "root@server", "--ssh-key", "key"],
])
def test_cli_private_target_is_restore_only_and_mutually_exclusive_with_ssh(tmp_path, monkeypatch, options):
    monkeypatch.setattr(sys, "argv", ["mongo_backup_job", *options, "--directory", str(tmp_path)])
    monkeypatch.setattr(job, "load_config", lambda: pytest.fail("invalid CLI must fail before config/store creation"))
    with pytest.raises(SystemExit) as error:
        job.main()
    assert error.value.code == 2


def test_cli_unexpected_target_connection_error_does_not_echo_uri(tmp_path, monkeypatch, capsys):
    path = tmp_path / "target.json"
    path.write_text(json.dumps({"uri": ISOLATED_URI}))
    path.chmod(0o600)
    closed = []
    source = SimpleNamespace(close=lambda: closed.append(True))
    def factory(uri, *args, **kwargs):
        if uri == ISOLATED_URI:
            raise ValueError(ISOLATED_URI)
        return source
    monkeypatch.setattr(job, "MongoSnapshotStore", factory)
    monkeypatch.setattr(job, "load_config", lambda: SimpleNamespace(mongo_uri="mongodb://localhost:27018/shilun", mongo_db="shilun"))
    monkeypatch.setattr(sys, "argv", ["mongo_backup_job", "restore-verify", "--directory", str(tmp_path),
        "--database", DESTINATION, "--target-uri-file", str(path)])
    with pytest.raises(SystemExit) as error:
        job.main()
    assert error.value.code == 1 and closed == [True]
    output = capsys.readouterr()
    assert ISOLATED_URI not in output.out + output.err and "restore-private-secret" not in output.err
