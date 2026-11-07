"""One-time, fail-closed local-to-ECS MongoDB cutover. Never accepts a password argument.

Run with .venv312/bin/python from a real Mac terminal. The SSH tunnel must already
be available on localhost:27018. Keep other database writers and the app launcher
closed throughout the cutover. Neither database is modified by the comparison.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, build_opener

from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = "cn.shilun.backend"
WRITERS = (
    "cn.shilun.minute-bars", "cn.shilun.tushare-daily", "cn.shilun.holding-check",
    "cn.shilun.trading-intraday", "cn.shilun.structure-samples",
)
KEYS = ("SHILUN_MONGO_URI", "SHILUN_MONGO_DB")


class CutoverError(Exception):
    """Only deliberately non-secret diagnostics may be placed in this exception."""


def cloud_uri(password: str) -> str:
    if not password or any(ord(char) < 32 for char in password):
        raise CutoverError("密码不能为空，也不能包含换行等控制字符。")
    return (
        f"mongodb://shilun_app:{quote(password, safe='')}@127.0.0.1:27018/shilun"
        "?authSource=shilun&authMechanism=SCRAM-SHA-256&directConnection=true"
        "&serverSelectionTimeoutMS=5000&connectTimeoutMS=5000"
    )


def replace_env(original: bytes, uri: str) -> bytes:
    replacements = dict(zip(KEYS, (uri, "shilun")))
    result = []
    for line in original.decode("utf-8").splitlines(keepends=True):
        key = line.split("=", 1)[0].strip()
        if key in replacements:
            continue  # Remove duplicate settings as well as the active one.
        result.append(line)
    text = "".join(result)
    if text and not text.endswith("\n"):
        text += "\n"
    return (text + "".join(f"{key}={value}\n" for key, value in replacements.items())).encode()


def private_backup(root: Path, original: bytes) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="mongo-cutover-", dir=root / "runtime"))
    backup = directory / "before.env"
    with backup.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    return backup


def write_env(path: Path, expected: bytes, updated: bytes) -> None:
    if path.is_symlink() or path.read_bytes() != expected:
        raise CutoverError("连接配置在核对期间发生变化，已停止，未覆盖它。")
    # Stage inside ignored runtime/, never in a potentially tracked root file.
    fd, temporary = tempfile.mkstemp(prefix="mongo-cutover-", dir=path.parent / "runtime")
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def launchctl(*args: str) -> subprocess.CompletedProcess:
    # Do not surface raw launchctl output: environment values may contain secrets.
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=25)


def loaded(label: str) -> bool:
    result = launchctl("print", f"gui/{os.getuid()}/{label}")
    if result.returncode == 0:
        return True
    if "Could not find service" in result.stderr:
        return False
    raise CutoverError("无法读取后台任务状态；请在已登录的 Mac 桌面终端中运行。")


def require_paused_writers() -> None:
    active = [label for label in WRITERS if loaded(label)]
    if active:
        raise CutoverError("这些采集任务尚未暂停，未切换：" + ", ".join(active))


def require_free_port(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", port))
                return
            except OSError:
                pass
        time.sleep(0.2)
    raise CutoverError("后台端口仍被占用，已停止切换；请检查是否还有另一个后台进程。")


def manifest(database) -> dict:
    metadata = {row["name"]: row for row in database.list_collections()}
    if not metadata or any(row["type"] != "collection" for row in metadata.values()):
        raise CutoverError("数据库为空或包含需单独处理的视图/时序集合，未自动切换。")
    names = sorted(metadata)
    hashes = database.command("dbHash", collections=names, maxTimeMS=120000)["collections"]
    if set(hashes) != set(names):
        raise CutoverError("部分集合没有返回内容校验值，未自动切换。")
    result = {}
    for name in names:
        indexes = {}
        for index in database[name].list_indexes():
            spec = {key: value for key, value in index.items() if key not in {"ns", "v"}}
            # Preserve compound-index field order while ignoring index metadata order.
            spec["key"] = list(spec["key"].items())
            indexes[index["name"]] = spec
        result[name] = {
            "documents": database[name].count_documents({}, maxTimeMS=120000),
            "hash": hashes[name], "indexes": indexes,
            "options": metadata[name].get("options", {}),
        }
    # Catch writes/TTL expiry during the count/index inspection, not just total-count changes.
    again = database.command("dbHash", collections=names, maxTimeMS=120000)["collections"]
    if hashes != again or set(database.list_collection_names()) != set(names):
        raise CutoverError("核对期间数据库发生变化；请暂停所有写入后重试。")
    return result


def compare(source: dict, target: dict) -> None:
    differences = [name for name in sorted(source.keys() | target.keys()) if source.get(name) != target.get(name)]
    if differences:
        raise CutoverError("两边数据或索引不一致，未切换。差异集合：" + ", ".join(differences))


def wait_ready(port: int) -> None:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        try:
            with opener.open(f"http://127.0.0.1:{port}/readyz", timeout=3) as response:
                if json.load(response).get("status") == "ready":
                    return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise CutoverError("新配置已保存，但后台就绪检查未通过。请保留当前配置，联系检查。")


def cutover(env_path: Path, original: bytes, uri: str, source, target, plist: Path, port: int) -> None:
    stopped = False
    changed = False
    backup = private_backup(env_path.parent, original)
    print(f"原配置已安全备份：{backup}", flush=True)
    try:
        require_paused_writers()
        if launchctl("bootout", f"gui/{os.getuid()}/{BACKEND}").returncode:
            raise CutoverError("未能暂停后台，未修改连接配置。")
        stopped = True
        require_free_port(port)
        print("后台已暂停，正在逐集合核对数量、内容校验值和索引，请稍候……", flush=True)
        source_manifest = manifest(source)
        target_manifest = manifest(target)
        compare(source_manifest, target_manifest)
        # Verify the source again after the remote scan, to catch another local writer.
        compare(source_manifest, manifest(source))
        require_paused_writers()
        require_free_port(port)
        print(f"核对通过：{len(source_manifest)} 个集合。正在切换配置并启动后台……", flush=True)
        # Fail closed even if interrupted immediately after the atomic rename.
        changed = True
        write_env(env_path, original, replace_env(original, uri))
        if launchctl("bootstrap", f"gui/{os.getuid()}", str(plist)).returncode:
            raise CutoverError("新配置已保存，但后台启动失败。请保留配置，联系检查。")
        wait_ready(port)
        print("切换成功：后台已重新启动，云端数据库就绪检查通过。", flush=True)
        print("本机旧库和原始备份均已保留；采集任务仍保持暂停。")
    finally:
        if stopped and not changed:
            if launchctl("bootstrap", f"gui/{os.getuid()}", str(plist)).returncode:
                print("注意：原连接配置未改，但原后台未能重新启动，请联系检查。")
            else:
                print("未切换数据库，已用原配置重新启动后台。")
        elif changed:
            # Once cloud writes may have started, silently rolling back risks split-brain.
            print(f"原配置备份保留于：{backup}（含敏感信息，请勿发送文件内容）。")


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    if sys.platform != "darwin" or not sys.stdin.isatty():
        print("请在 Mac 终端直接运行此程序，不能通过管道输入密码。")
        return 1
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from shilun.common.config import load_config

    try:
        env_path = ROOT / ".env"
        if env_path.is_symlink() or not env_path.is_file():
            raise CutoverError("未找到普通 .env 配置文件，未修改任何配置。")
        original = env_path.read_bytes()
        plist = ROOT / "石论后台.app/Contents/Resources/cn.shilun.backend.plist"
        settings = plistlib.loads(plist.read_bytes())
        if settings.get("Label") != BACKEND or settings.get("WorkingDirectory") != str(ROOT):
            raise CutoverError("后台启动配置与此项目不匹配，未自动切换。")
        for key in KEYS:
            if os.environ.get(key) or settings.get("EnvironmentVariables", {}).get(key) or launchctl("getenv", key).stdout.strip():
                raise CutoverError("存在覆盖 .env 的数据库环境变量，请先检查启动配置。")
        config = load_config()
        address = urlsplit(config.mongo_uri or "")
        if config.mongo_db != "shilun" or address.hostname not in {"localhost", "127.0.0.1"} or (address.port or 27017) != 27017:
            raise CutoverError("当前配置不是待迁移的本机 shilun 数据库，未自动改写。")
        require_paused_writers()
        if not loaded(BACKEND):
            raise CutoverError("未找到受管理的后台进程，未自动修改连接配置。")
        live = launchctl("print", f"gui/{os.getuid()}/{BACKEND}").stdout
        if f"path = {plist}\n" not in live:
            raise CutoverError("实际运行的后台使用了另一份启动配置，未自动切换。")
        print("请关闭后台页面、采集命令及应用启动器，核对期间不要启动它们。")
        if input("确认现在切换到 ECS？输入 YES 继续：").strip() != "YES":
            print("已取消，未修改配置。")
            return 1
        password = getpass.getpass("请输入云端 shilun_app 的数据库密码（输入不显示）：")
        uri = cloud_uri(password)
        del password
        options = dict(serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=135000)
        with MongoClient(config.mongo_uri, **options) as local, MongoClient(uri, **options) as remote:
            status = remote.shilun.command("connectionStatus")["authInfo"]
            if {"user": "shilun_app", "db": "shilun"} not in status["authenticatedUsers"]:
                raise CutoverError("云端账号验证未通过，未修改配置。")
            local.admin.command("ping")
            cutover(env_path, original, uri, local.shilun, remote.shilun, plist, config.api_port)
        return 0
    except CutoverError as error:
        print(f"停止：{error}")
    except KeyboardInterrupt:
        print("\n操作已中止。请根据上面的配置阶段提示确认当前状态。")
    except Exception as error:
        # MongoDB/URL/OS exceptions can embed credentials. Never print their messages.
        print(f"停止：{type(error).__name__}。请检查隧道和密码；不要发送密码或 .env 内容。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
