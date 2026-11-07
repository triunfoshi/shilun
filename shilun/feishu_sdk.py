"""Feishu SDK checks, receive-only diagnostics, and authorized Codex replies.

Credentials stay in environment variables or lark-cli's existing macOS store.
Nothing starts when the web application imports this module.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import dataclass, field
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
from typing import Any, Callable

from shilun.common.config import _read_env_file

_status_sink: Callable[[dict[str, Any]], Any] | None = None


def emit_status(data: dict[str, Any], *, error: bool = False) -> None:
    if _status_sink is not None:
        try:
            _status_sink(data)
        except Exception:
            pass  # Remote logging never falls back to a local file.
        return
    print(json.dumps(data, ensure_ascii=False), file=sys.stderr if error else sys.stdout, flush=True)


class SetupError(RuntimeError):
    """A setup error whose message is safe to display."""


@dataclass(frozen=True)
class Credentials:
    app_id: str
    app_secret: str = field(repr=False)
    brand: str = "feishu"
    source: str = "environment"

    @property
    def domain(self) -> str:
        return "https://open.larksuite.com" if self.brand == "lark" else "https://open.feishu.cn"


def decode_master_key(raw: bytes) -> bytes:
    """Decode security(1) output, including go-keyring's storage envelope."""
    raw = raw.strip()
    if raw.startswith(b"go-keyring-base64:"):
        raw = base64.b64decode(raw.split(b":", 1)[1], validate=True)
    elif raw.startswith(b"go-keyring-encoded:"):
        raw = bytes.fromhex(raw.split(b":", 1)[1].decode("ascii"))
    key = base64.b64decode(raw, validate=True)
    if len(key) != 32:
        raise ValueError("unsupported master key format")
    return key


def load_credentials() -> Credentials:
    values = _read_env_file()

    def value(name: str) -> str:
        return (os.environ.get(name) or values.get(name) or "").strip()

    app_id = value("SHILUN_FEISHU_APP_ID")
    secret = value("SHILUN_FEISHU_APP_SECRET")
    brand = value("SHILUN_FEISHU_BRAND") or "feishu"
    if brand not in {"feishu", "lark"}:
        raise SetupError("SHILUN_FEISHU_BRAND 必须为 feishu 或 lark。")
    if secret:
        if not app_id:
            raise SetupError("提供 App Secret 时必须同时设置 SHILUN_FEISHU_APP_ID。")
        return Credentials(app_id, secret, brand)

    # Compatibility adapter for lark-cli v1.0.98. Fail closed on other formats.
    if sys.platform != "darwin":
        raise SetupError("请在运行环境配置 SHILUN_FEISHU_APP_ID 和 SHILUN_FEISHU_APP_SECRET。")
    config_path = Path.home() / ".lark-cli" / "config.json"
    try:
        cli_config = json.loads(config_path.read_text(encoding="utf-8"))
        apps = cli_config.get("apps", [])
        selected = app_id or cli_config.get("currentApp")
        choices = [app for app in apps if selected in {app.get("appId"), app.get("name")}] if selected else apps[:1]
        if len(choices) != 1:
            raise SetupError("无法唯一选择 CLI 应用；请指定 SHILUN_FEISHU_APP_ID 或配置完整凭据。")
        app = choices[0]
        app_id = app["appId"]
        reference = app.get("appSecret", {})
        if not re.fullmatch(r"cli_[A-Za-z0-9]+", app_id):
            raise SetupError("CLI 应用 ID 格式不受支持。")
        if reference != {"source": "keychain", "id": f"appsecret:{app_id}"}:
            raise SetupError("CLI 凭据存储格式不受支持；请显式配置 SDK 应用凭据。")
        store = Path.home() / "Library" / "Application Support" / "lark-cli"
        encrypted = (store / f"appsecret_{app_id}.enc").read_bytes()
        if len(encrypted) <= 28:
            raise SetupError("CLI 加密凭据不完整；请检查 CLI 应用配置。")
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", "lark-cli", "-a", "master.key", "-w"],
            capture_output=True,
            timeout=20,
            check=False,
        )
        if result.returncode:
            raise SetupError("无法访问 macOS 钥匙串；请在允许访问钥匙串的终端运行，不要降级凭据存储。")
        key = decode_master_key(result.stdout)
        from Crypto.Cipher import AES

        cipher = AES.new(key, AES.MODE_GCM, nonce=encrypted[:12])
        secret = cipher.decrypt_and_verify(encrypted[12:-16], encrypted[-16:]).decode("utf-8")
        if not secret:
            raise SetupError("CLI 应用凭据为空。")
        return Credentials(app_id, secret, app.get("brand", "feishu"), "lark-cli macOS keychain")
    except SetupError:
        raise
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        raise SetupError("无法读取 CLI 加密应用凭据；请检查 CLI 配置或显式配置 SDK 凭据。") from exc


def sdk() -> Any:
    try:
        import lark_oapi as lark
        from lark_oapi.core.log import logger
    except ImportError as exc:
        raise SetupError("尚未安装 SDK；请运行 pip install '.[feishu]'。") from exc
    # SDK INFO logs include signed WebSocket URLs. Emit only our safe status.
    logger.disabled = True
    return lark


def build_client(credentials: Credentials) -> Any:
    lark = sdk()
    return (
        lark.Client.builder()
        .app_id(credentials.app_id)
        .app_secret(credentials.app_secret)
        .domain(credentials.domain)
        .timeout(10)
        .log_level(lark.LogLevel.ERROR)
        .build()
    )


def check_bot(credentials: Credentials) -> dict[str, Any]:
    lark = sdk()
    request = (
        lark.BaseRequest.builder()
        .http_method(lark.HttpMethod.GET)
        .uri("/open-apis/bot/v3/info")
        .token_types({lark.AccessTokenType.TENANT})
        .build()
    )
    response = build_client(credentials).request(request)
    if not response.success():
        raise SetupError(f"飞书机器人认证或能力检查失败，code={response.code}。")
    payload = json.loads(response.raw.content)
    bot = payload.get("bot", {})
    return {"authenticated": True, "bot_name": bot.get("app_name"), "bot_open_id": bot.get("open_id")}


def build_connection(credentials: Credentials, *, reconnect: bool,
                     on_message: Callable[[Any], Any] | None = None) -> Any:
    lark = sdk()

    def receive(event: Any) -> None:
        if on_message is not None:
            on_message(event)
            return
        # Receive-only setup listener: never log message bodies or send replies.
        message = event.event.message
        emit_status({"status": "event_received", "message_id": message.message_id,
                     "message_type": message.message_type})

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(receive).build()
    return lark.ws.Client(credentials.app_id, credentials.app_secret, domain=credentials.domain,
                          event_handler=handler, log_level=lark.LogLevel.ERROR, auto_reconnect=reconnect)


def enable_connection_status(credentials: Credentials) -> None:
    """Report actual connection lifecycle without printing signed SDK URLs."""
    from lark_oapi.core.log import logger

    class StatusHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            message = record.getMessage()
            if message.startswith("connected to "):
                state = "connected"
            elif message.startswith("disconnected to "):
                state = "disconnected"
            elif record.levelno >= logging.ERROR:
                state = "connection_error"
            else:
                return
            emit_status({"status": state, "app_id": credentials.app_id, "pid": os.getpid()})

    logger.handlers = [StatusHandler()]
    logger.propagate = False
    logger.disabled = False
    logger.setLevel(logging.INFO)


def _probe_main() -> int:
    # The pinned SDK has no bounded public start method; keep private calls here
    # in an isolated subprocess. Its parent enforces a wall-clock timeout even
    # if the SDK's synchronous endpoint-discovery POST stalls.
    sdk()
    from lark_oapi.ws.client import loop

    supplied = json.load(sys.stdin)
    credentials = Credentials(**supplied)
    connection = build_connection(credentials, reconnect=False)

    async def probe() -> None:
        try:
            await connection._connect()
        finally:
            await connection._disconnect()

    try:
        loop.run_until_complete(probe())
        return 0
    except Exception as exc:
        # Print no SDK exception text: it can include a signed connection URL.
        print(json.dumps({"error_type": type(exc).__name__,
                          "error_code": getattr(exc, "code", None)}))
        return 1
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))


def check_connection(credentials: Credentials, timeout: float) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [sys.executable, "-c", "from shilun.feishu_sdk import _probe_main; raise SystemExit(_probe_main())"],
            # Use stdin rather than argv, environment, or an intermediate file.
            input=json.dumps({"app_id": credentials.app_id, "app_secret": credentials.app_secret,
                              "brand": credentials.brand}),
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SetupError("长连接握手超时；请检查网络和飞书事件订阅方式。") from exc
    if result.returncode:
        # Child output is deliberately withheld; only safe status reaches logs.
        raise SetupError("长连接握手失败；请检查应用凭据及事件订阅配置。")
    return {"long_connection": "connected", "probe_closed": True}


def _supervise_reply_worker(bridge: Any, store: Any = None) -> None:
    """Cancel model work promptly when its worker or storage lease is lost."""
    while not bridge.stop_event.wait(2):
        if ((store is not None and not store.healthy)
                or (bridge.thread is not None and not bridge.thread.is_alive())):
            os.kill(os.getpid(), signal.SIGTERM)
            return


def main(argv: list[str] | None = None) -> int:
    global _status_sink
    parser = argparse.ArgumentParser(description="石论飞书 SDK 检查、长连接与 Codex 自动回复")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="验证 SDK、应用认证和机器人能力（默认）")
    mode.add_argument("--connect-check", action="store_true", help="验证长连接握手后立即断开")
    mode.add_argument("--listen", action="store_true", help="持续接收消息事件，仅打印消息 ID，不自动回复")
    mode.add_argument("--auto-reply", action="store_true", help="接收允许用户私聊及群内@机器人，由 Codex 自动回复")
    parser.add_argument("--timeout", type=float, default=15, help="长连接握手超时秒数，范围 1–60")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 60:
        parser.error("--timeout 必须在 1–60 秒之间")
    bridge = None
    store = None
    previous_signal = None
    try:
        sdk()
        credentials = load_credentials()
        settings = _read_env_file() if args.auto_reply else {}
        if args.auto_reply:
            backend = os.environ.get("SHILUN_FEISHU_STORE") or settings.get("SHILUN_FEISHU_STORE", "sqlite")
            if backend not in {"sqlite", "mongo"}:
                raise SetupError("SHILUN_FEISHU_STORE 必须为 mongo 或 sqlite。")
            if backend == "mongo":
                from shilun.feishu_codex_store import RemoteMongoStore
                store = RemoteMongoStore(credentials.app_id)
                _status_sink = store.log
        status = {"ok": True, "sdk_version": importlib.metadata.version("lark-oapi"),
                  "app_id": credentials.app_id, "credential_source": credentials.source}
        status.update(check_bot(credentials))
        if args.connect_check:
            status.update(check_connection(credentials, args.timeout))
        emit_status(status)
        if args.listen or args.auto_reply:
            if args.auto_reply:
                for name in ("SHILUN_CODEX_BIN", "SHILUN_CODEX_TIMEOUT_SECONDS",
                             "SHILUN_CODEX_MODEL", "SHILUN_CODEX_REASONING_EFFORT",
                             "SHILUN_CODEX_SERVICE_TIER", "SHILUN_CODEX_PROXY"):
                    if not os.environ.get(name) and settings.get(name):
                        os.environ[name] = settings[name]
                from shilun.feishu_codex_bridge import CodexBridge
                bridge = CodexBridge(credentials.app_id, build_client(credentials), store=store,
                                     bot_open_id=status.get("bot_open_id"))
                bridge.start()
                emit_status({"status": "auto_reply_enabled", "allowed_user_count": len(bridge.users),
                             "group_owner_mentions_enabled": bridge.bot_open_id is not None,
                             "storage": "mongo" if store is not None else "sqlite"})
            emit_status({"status": "starting_listener", "receive_only": not args.auto_reply})
            connection = build_connection(credentials, reconnect=True, on_message=bridge.accept if bridge else None)
            enable_connection_status(credentials)

            def terminate(_signal: int, _frame: Any) -> None:
                raise KeyboardInterrupt

            previous_signal = signal.signal(signal.SIGTERM, terminate)
            if bridge is not None:
                threading.Thread(target=_supervise_reply_worker, args=(bridge, store),
                                 name="feishu-worker-supervisor", daemon=True).start()
            connection.start()
        return 0
    except KeyboardInterrupt:
        return 0
    except SetupError as exc:
        emit_status({"ok": False, "error": str(exc)}, error=True)
        return 1
    except Exception as exc:
        # Network/SDK exceptions can contain signed URLs or raw request details.
        emit_status({"ok": False, "error": "SDK 检查失败，请核实网络、应用权限及长连接配置。",
                     "error_type": type(exc).__name__}, error=True)
        return 1
    finally:
        if bridge is not None:
            bridge.close()
        elif store is not None:
            store.close()
        _status_sink = None
        if previous_signal is not None:
            signal.signal(signal.SIGTERM, previous_signal)


if __name__ == "__main__":
    raise SystemExit(main())
