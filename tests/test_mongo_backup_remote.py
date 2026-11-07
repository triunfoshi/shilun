"""Exercise the encrypted-stream runner with local fake SSH/tools, never ECS."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from shilun.jobs import mongo_backup_remote as remote
from shilun.jobs.mongo_backup_job import BackupError


URI = "mongodb://user:p%40ss%24%28hidden%29@localhost:27018/shilun?authSource=shilun&directConnection=true"
REMOTE_URI = URI.replace("localhost:27018", "127.0.0.1:27017")
PAYLOAD = bytes(range(256)) * 5000


@pytest.fixture
def environment(tmp_path, monkeypatch):
    directory = tmp_path / "bundle"
    directory.mkdir(mode=0o700)
    archive = directory / "database.archive.gz"
    archive.touch(mode=0o600)
    key = tmp_path / "operator-key"
    key.write_text("fake test key; never read by the runner")
    key.chmod(0o600)
    tools = tmp_path / "tools"
    tools.mkdir()
    observed = tmp_path / "observed.json"
    pid = tmp_path / "tool.pid"
    script = '''
import json, os, pathlib, sys, time
pathlib.Path(PID).write_text(str(os.getpid()))
config = pathlib.Path(next(a.split("=",1)[1] for a in sys.argv if a.startswith("--config=")))
assert config.stat().st_mode & 0o077 == 0
assert config.parent.stat().st_mode & 0o077 == 0
uri = json.loads(config.read_text().split("uri: ",1)[1])
assert uri not in repr(sys.argv)
row = {"uri":uri, "argv":sys.argv[1:]}
if MODE == "fail":
    sys.stderr.write("private diagnostic " + uri)
    pathlib.Path(OBSERVED).write_text(json.dumps(row))
    sys.exit(2)
if MODE == "sleep":
    time.sleep(20)
if MODE == "earlyexit":
    sys.exit(0)
if pathlib.Path(sys.argv[0]).name == "mongodump":
    sys.stdout.buffer.write(bytes(range(256)) * 5000)
else:
    data = sys.stdin.buffer.read()
    row["bytes"] = len(data)
    row["payload_ok"] = data == bytes(range(256)) * 5000
    sys.stdout.write("private restore stdout")
    sys.stderr.write("private restore stderr")
pathlib.Path(OBSERVED).write_text(json.dumps(row))
'''

    def install(mode="ok"):
        for name in ("mongodump", "mongorestore"):
            path = tools / name
            path.write_text("#!" + sys.executable + "\n" + "PID=" + repr(str(pid))
                + "\nOBSERVED=" + repr(str(observed)) + "\nMODE=" + repr(mode) + "\n" + script)
            path.chmod(0o700)

    install()
    commands = []
    actual_popen = subprocess.Popen

    def fake_ssh(command, **kwargs):
        commands.append(command)
        assert command[0] == "ssh" and command[-2] == "root@host.example"
        assert all("hidden" not in item and "mongodb://" not in item for item in command)
        assert not any("StrictHostKeyChecking" in item or "UserKnownHostsFile" in item for item in command)
        source = remote._REMOTE_SCRIPT.replace('"/usr/bin/"', repr(str(tools) + "/"))
        source = source.replace('tempfile.mkdtemp(prefix=', 'tempfile.mkdtemp(dir=' + repr(str(tmp_path)) + ', prefix=')
        return actual_popen([sys.executable, "-c", source], **kwargs)

    monkeypatch.setattr(remote.subprocess, "Popen", fake_ssh)
    return directory, archive, key, commands, observed, pid, install


def dump_args(archive):
    return ["/opt/homebrew/bin/mongodump", "--db=shilun", "--archive=" + str(archive),
        "--gzip", "--numParallelCollections=1"]


def restore_args(archive):
    return ["/opt/homebrew/bin/mongorestore", "--archive=" + str(archive), "--gzip",
        "--nsInclude=shilun.*", "--nsFrom=shilun.*", "--nsTo=shilun_recovery_20261029_a.*",
        "--nsExclude=shilun.trade_journal_write_locks", "--stopOnError",
        "--numParallelCollections=1", "--numInsertionWorkersPerCollection=1"]


def test_dump_is_compressed_remotely_and_streamed_to_existing_private_archive(environment):
    directory, archive, key, commands, observed, _, _ = environment
    heartbeats = []
    remote.remote_runner("root@host.example", key)(dump_args(archive), uri=URI,
        directory=directory, log_name="dump.private.log", timeout=10, heartbeat=lambda: heartbeats.append(True))
    assert archive.read_bytes() == PAYLOAD
    assert archive.stat().st_mode & 0o077 == 0
    assert (directory / "dump.private.log").stat().st_mode & 0o077 == 0
    row = json.loads(observed.read_text())
    assert row["uri"] == REMOTE_URI
    assert "--archive" in row["argv"] and "--gzip" in row["argv"]
    assert str(archive) not in repr(row["argv"])
    assert heartbeats and len(commands) == 1
    assert not list(directory.parent.glob("shilun-mongo-tools-*"))


def test_restore_keeps_binary_after_json_header_and_preserves_namespace_remap(environment):
    directory, archive, key, _, observed, _, _ = environment
    archive.write_bytes(PAYLOAD)
    remote.remote_runner("root@host.example", key)(restore_args(archive), uri=URI,
        directory=directory, log_name="restore.private.log", timeout=10)
    row = json.loads(observed.read_text())
    assert row["bytes"] == len(PAYLOAD) and row["payload_ok"]
    assert row["uri"] == REMOTE_URI
    assert "--nsFrom=shilun.*" in row["argv"]
    assert "--nsTo=shilun_recovery_20261029_a.*" in row["argv"]
    assert "--drop" not in row["argv"]
    log = (directory / "restore.private.log").read_text()
    assert "private restore stdout" in log and "private restore stderr" in log
    assert archive.read_bytes() == PAYLOAD
    assert not list(directory.parent.glob("shilun-mongo-tools-*"))


def test_remote_uri_preserves_encoded_credentials_path_and_query_without_argv():
    assert remote._remote_uri(URI, 27019) == URI.replace("localhost:27018", "127.0.0.1:27019")
    assert remote._remote_uri(URI.replace("localhost", "127.0.0.1"), 27017) == REMOTE_URI


@pytest.mark.parametrize("uri", ["mongodb://remote:27018/shilun", "mongodb://localhost:27017/shilun",
    "mongodb+srv://localhost/shilun", "mongodb://localhost:27018,other:27018/shilun",
    "mongodb://localhost:27018/shilun\nsecret", "mongodb://localhost:27018/shilun#fragment"])
def test_non_tunnel_uri_fails_before_ssh_or_archive_write(environment, uri):
    directory, archive, key, commands, _, _, _ = environment
    with pytest.raises(BackupError) as error:
        remote.remote_runner("root@host.example", key)(dump_args(archive), uri=uri,
            directory=directory, log_name="dump.private.log", timeout=10)
    assert uri not in str(error.value)
    assert commands == [] and archive.stat().st_size == 0


@pytest.mark.parametrize("extra", ["--uri=mongodb://secret", "--password=secret", "--config=/private/config",
    "--drop", "--oplog", "--out=/elsewhere"])
def test_unapproved_flags_fail_before_start(environment, extra):
    directory, archive, key, commands, _, _, _ = environment
    with pytest.raises(BackupError):
        remote.remote_runner("root@host.example", key)(dump_args(archive) + [extra], uri=URI,
            directory=directory, log_name="dump.private.log", timeout=10)
    assert commands == []


@pytest.mark.parametrize("target", ["shilun.*", "admin.*", "shilun_recovery_x.*"])
def test_restore_requires_bounded_isolated_namespace(environment, target):
    directory, archive, key, commands, _, _, _ = environment
    arguments = ["--nsTo=" + target if arg.startswith("--nsTo=") else arg for arg in restore_args(archive)]
    with pytest.raises(BackupError):
        remote.remote_runner("root@host.example", key)(arguments, uri=URI,
            directory=directory, log_name="restore.private.log", timeout=10)
    assert commands == []


def test_remote_failure_keeps_private_diagnostics_but_public_error_has_no_secret(environment, capsys):
    directory, archive, key, _, _, _, install = environment
    install("fail")
    with pytest.raises(BackupError) as error:
        remote.remote_runner("root@host.example", key)(dump_args(archive), uri=URI,
            directory=directory, log_name="failure.private.log", timeout=10)
    assert URI not in str(error.value) and "hidden" not in str(error.value)
    assert capsys.readouterr().out == ""
    assert REMOTE_URI in (directory / "failure.private.log").read_text()
    assert not list(directory.parent.glob("shilun-mongo-tools-*"))


@pytest.mark.parametrize("operation", ["dump", "restore"])
def test_timeout_terminates_tool_and_cleans_remote_private_config(environment, operation):
    directory, archive, key, _, _, pid, install = environment
    install("sleep")
    if operation == "restore":
        archive.write_bytes(PAYLOAD)
    started = time.monotonic()
    with pytest.raises(BackupError):
        remote.remote_runner("root@host.example", key)((dump_args if operation == "dump" else restore_args)(archive), uri=URI,
            directory=directory, log_name="timeout.private.log", timeout=0.6)
    assert time.monotonic() - started < 8
    if pid.exists():
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid.read_text()), 0)
    assert not list(directory.parent.glob("shilun-mongo-tools-*"))


def test_heartbeat_failure_stops_transfer_and_does_not_expose_exception(environment):
    directory, archive, key, _, _, _, install = environment
    install("sleep")
    def heartbeat():
        raise ValueError(URI)
    with pytest.raises(BackupError) as error:
        remote.remote_runner("root@host.example", key)(dump_args(archive), uri=URI,
            directory=directory, log_name="heartbeat.private.log", timeout=10, heartbeat=heartbeat)
    assert URI not in str(error.value)
    assert not list(directory.parent.glob("shilun-mongo-tools-*"))


def test_restore_tool_cannot_report_success_without_consuming_archive(environment):
    directory, archive, key, _, _, _, install = environment
    install("earlyexit")
    archive.write_bytes(PAYLOAD)
    with pytest.raises(BackupError):
        remote.remote_runner("root@host.example", key)(restore_args(archive), uri=URI,
            directory=directory, log_name="earlyexit.private.log", timeout=10)
    assert not list(directory.parent.glob("shilun-mongo-tools-*"))


def test_unbuffered_writer_retries_short_writes():
    received = bytearray()
    class ShortStream:
        def write(self, data):
            received.extend(data[:3])
            return len(data[:3])
    remote._write_all(ShortStream(), b"header\narchive")
    assert received == b"header\narchive"


def test_remote_script_parses_with_python36_grammar():
    ast.parse(remote._REMOTE_SCRIPT, feature_version=(3, 6))
