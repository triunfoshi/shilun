"""Stream the existing backup job's official tools over an explicit SSH target.

The Mongo URI travels only in an encrypted stdin header and a short-lived 0600
remote config. Archive bytes never enter a command line or an in-memory buffer.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from shilun.jobs.mongo_backup_job import BackupError


# This source is passed as a single quoted, static python3 -c argument. It must
# remain compatible with the ECS system Python 3.6; no secret is interpolated.
_REMOTE_SCRIPT = r'''
import json, os, shutil, signal, subprocess, sys, tempfile, threading, time

process = None
private = None
interrupted = threading.Event()
input_failed = threading.Event()
input_done = threading.Event()

def stop_signal(signum, frame):
    raise RuntimeError("remote operation interrupted")

def terminate():
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

def feed_archive(stream):
    try:
        received = 0
        while True:
            chunk = sys.stdin.buffer.read(1024 * 1024)
            if not chunk:
                break
            received += len(chunk)
            stream.write(chunk)
        if received != config["archive_bytes"]:
            input_failed.set()
    except Exception:
        input_failed.set()
    finally:
        try:
            stream.close()
        except Exception:
            input_failed.set()
        input_done.set()

def watch_disconnect():
    try:
        # The dump client deliberately keeps stdin open until completion.
        sys.stdin.buffer.read(1)
    except Exception:
        pass
    interrupted.set()

for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(sig, stop_signal)

code = 1
try:
    header = sys.stdin.buffer.readline(1024 * 1024)
    if not header.endswith(b"\n"):
        raise ValueError("invalid header")
    config = json.loads(header.decode("utf-8"))
    if config["operation"] not in ("mongodump", "mongorestore"):
        raise ValueError("unsupported tool")
    timeout = float(config["timeout"])
    if not 0 < timeout <= 7200:
        raise ValueError("invalid timeout")
    private = tempfile.mkdtemp(prefix="shilun-mongo-tools-")
    os.chmod(private, 0o700)
    config_path = os.path.join(private, "connection.yml")
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("uri: " + json.dumps(config["uri"]) + "\n")
    arguments = ["/usr/bin/" + config["operation"], "--config=" + config_path] + config["arguments"]
    dump = config["operation"] == "mongodump"
    process = subprocess.Popen(arguments,
        stdin=subprocess.DEVNULL if dump else subprocess.PIPE,
        stdout=sys.stdout.buffer if dump else sys.stderr.buffer,
        stderr=sys.stderr.buffer)
    worker = threading.Thread(target=watch_disconnect if dump else feed_archive,
        args=() if dump else (process.stdin,))
    worker.daemon = True
    worker.start()
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        if time.monotonic() >= deadline or interrupted.is_set() or input_failed.is_set():
            raise RuntimeError("remote operation interrupted")
        time.sleep(0.1)
    if not dump:
        worker.join(timeout=5)
        if not input_done.is_set():
            raise RuntimeError("remote archive stream incomplete")
    if process.returncode or input_failed.is_set():
        raise RuntimeError("remote tool failed")
    code = 0
except BaseException:
    sys.stderr.write("Remote Mongo operation failed; private log retained.\n")
    sys.stderr.flush()
finally:
    try:
        terminate()
    finally:
        if private is not None:
            shutil.rmtree(private)
os._exit(code)
'''


def _remote_uri(uri, port):
    try:
        if not isinstance(uri, str) or any(ord(c) < 32 for c in uri):
            raise ValueError
        parsed = urlsplit(uri)
        if (parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost"}
                or parsed.port != 27018 or parsed.fragment):
            raise ValueError
        userinfo, separator, _ = parsed.netloc.rpartition("@")
        authority = (userinfo + separator if separator else "") + "127.0.0.1:" + str(port)
        return urlunsplit((parsed.scheme, authority, parsed.path, parsed.query, ""))
    except (TypeError, ValueError):
        raise BackupError("远端Mongo工具只接受现有127.0.0.1:27018隧道配置；未回显连接信息") from None


def _tool_arguments(arguments, directory):
    """Accept only the bounded operations emitted by mongo_backup_job."""
    try:
        operation = Path(arguments[0]).name
        if operation not in {"mongodump", "mongorestore"}:
            raise ValueError
        flags = list(arguments[1:])
        archives = [value for value in flags if value.startswith("--archive=")]
        if len(archives) != 1:
            raise ValueError
        archive = Path(archives[0].split("=", 1)[1])
        if archive.parent.absolute() != directory.absolute():
            raise ValueError
        remote = []
        for value in flags:
            if value == archives[0]:
                remote.append("--archive")
            elif value in {"--gzip", "--stopOnError"}:
                remote.append(value)
            elif re.fullmatch(r"--(?:numParallelCollections|numInsertionWorkersPerCollection)=[1-9][0-9]*", value):
                remote.append(value)
            elif operation == "mongodump" and re.fullmatch(r"--db=[A-Za-z0-9_-]+", value):
                if value.split("=", 1)[1] in {"admin", "local", "config"}:
                    raise ValueError
                remote.append(value)
            elif operation == "mongorestore" and re.fullmatch(r"--ns(?:Include|From|To|Exclude)=[A-Za-z0-9_-]+\.(?:\*|trade_journal_write_locks)", value):
                remote.append(value)
            else:
                raise ValueError
        if "--gzip" not in remote or len(set(remote)) != len(remote):
            raise ValueError
        if operation == "mongodump":
            if len([value for value in remote if value.startswith("--db=")]) != 1:
                raise ValueError
        else:
            namespaces = {}
            for value in remote:
                if value.startswith("--ns"):
                    key, content = value.split("=", 1)
                    if key in namespaces:
                        raise ValueError
                    namespaces[key] = content
            source = namespaces["--nsFrom"]
            target = namespaces["--nsTo"]
            if (namespaces["--nsInclude"] != source
                    or not re.fullmatch(r"[A-Za-z0-9_-]+\.\*", source)
                    or not re.fullmatch(r"shilun_recovery_[a-z0-9_]{8,40}\.\*", target)
                    or source == target
                    or namespaces.get("--nsExclude") != source[:-1] + "trade_journal_write_locks"
                    or "--stopOnError" not in remote):
                raise ValueError
        return operation, archive, remote
    except (IndexError, KeyError, TypeError, ValueError):
        raise BackupError("远端Mongo工具参数不受支持；未启动SSH或恢复") from None


def _private_open(path, flags, *, create=False):
    descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0)
        | (os.O_CREAT | os.O_EXCL if create else 0), 0o600)
    information = os.fstat(descriptor)
    if (not stat.S_ISREG(information.st_mode) or information.st_mode & 0o077
            or information.st_nlink != 1):
        os.close(descriptor)
        raise BackupError("归档与日志必须为普通0600私有文件")
    return descriptor


def _terminate(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _write_all(stream, data):
    """Unbuffered pipe writes may be short; never silently truncate a header."""
    remaining = memoryview(data)
    while remaining:
        written = stream.write(remaining)
        if not written:
            raise OSError("stream closed")
        remaining = remaining[written:]


def remote_runner(ssh_target, ssh_key, remote_mongo_port=27017):
    """Return a runner compatible with mongo_backup_job._run_tool.

    The operator supplies the SSH identity explicitly. The existing known-hosts
    policy remains in force; no connection configuration or software is changed.
    """
    if (not isinstance(ssh_target, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9][A-Za-z0-9_.-]*", ssh_target)
            or isinstance(remote_mongo_port, bool) or not isinstance(remote_mongo_port, int)
            or not 1 <= remote_mongo_port <= 65535):
        raise BackupError("请明确提供有效SSH目标及远端Mongo端口")
    key = Path(ssh_key).expanduser().absolute()
    if not key.is_file() or key.is_symlink():
        raise BackupError("请明确提供已有SSH私钥文件；不读取或生成密钥")
    command = ["ssh", "-T", "-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=12", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
        "--", ssh_target, "python3 -c " + shlex.quote(_REMOTE_SCRIPT)]

    def run(arguments, *, uri, directory, log_name, timeout, heartbeat=None):
        process = None
        worker = None
        stopped = threading.Event()
        feed_failed = threading.Event()
        directory = Path(directory)
        if (directory.is_symlink() or not directory.is_dir() or directory.stat().st_mode & 0o077
                or Path(log_name).name != log_name or log_name in {"", ".", ".."}):
            raise BackupError("远端Mongo工具需要现有0700私有目录与本目录日志")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 7200:
            raise BackupError("远端Mongo时间预算须在0到7200秒之间")
        operation, archive, flags = _tool_arguments(arguments, directory)
        remote_uri = _remote_uri(uri, remote_mongo_port)
        try:
            archive_fd = _private_open(archive, os.O_WRONLY if operation == "mongodump" else os.O_RDONLY)
            with os.fdopen(archive_fd, "wb" if operation == "mongodump" else "rb") as archive_stream:
                if operation == "mongodump" and os.fstat(archive_stream.fileno()).st_size:
                    raise BackupError("远端归档目标须为已创建的空私有文件")
                payload = (json.dumps({"operation": operation, "arguments": flags,
                    "uri": remote_uri, "timeout": timeout,
                    "archive_bytes": os.fstat(archive_stream.fileno()).st_size}) + "\n").encode()
                log_fd = _private_open(directory / log_name, os.O_WRONLY, create=True)
                with os.fdopen(log_fd, "wb") as log:
                    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                        stdout=archive_stream if operation == "mongodump" else log, stderr=log, bufsize=0)

                    def feed():
                        try:
                            _write_all(process.stdin, payload)
                            if operation == "mongorestore":
                                while not stopped.is_set():
                                    chunk = archive_stream.read(1024 * 1024)
                                    if not chunk:
                                        break
                                    _write_all(process.stdin, chunk)
                                process.stdin.close()
                            else:
                                # EOF means cancellation to the remote dump monitor.
                                stopped.wait()
                        except Exception:
                            feed_failed.set()

                    worker = threading.Thread(target=feed, daemon=True)
                    worker.start()
                    deadline = time.monotonic() + timeout
                    while process.poll() is None:
                        if time.monotonic() >= deadline or feed_failed.is_set():
                            raise BackupError("远端Mongo归档/恢复超时或传输失败；检查私有日志")
                        if heartbeat:
                            heartbeat()
                        time.sleep(0.25)
                    if operation == "mongorestore":
                        worker.join(timeout=5)
                        if worker.is_alive():
                            raise BackupError("远端Mongo归档传输未完成；检查私有日志")
                    if process.returncode or feed_failed.is_set():
                        raise BackupError("远端Mongo归档/恢复失败；检查私有日志，未回显连接信息")
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise BackupError("远端Mongo归档/恢复未完成；检查私有日志，未回显连接信息") from None
        finally:
            stopped.set()
            try:
                _terminate(process)
            finally:
                if worker is not None:
                    worker.join(timeout=5)
                if process is not None and process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()

    return run
