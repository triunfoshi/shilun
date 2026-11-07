"""Bounded, non-interactive Codex calls for the Feishu reply worker.

The adapter never reads authentication files, forwards CLI diagnostics, or puts
message contents on a command line. Login remains managed by the official CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time
import tomllib
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import UUID


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")
DEFAULT_TIMEOUT_SECONDS = 180.0
DISABLED_FEATURES = (
    "apps", "plugins", "remote_plugin", "plugin_discovery_prefetch",
    "shell_tool", "unified_exec", "browser_use", "browser_use_external",
    "browser_use_full_cdp_access", "computer_use", "image_generation",
    "multi_agent", "multi_agent_v2", "code_mode", "code_mode_host",
    "skill_search", "skill_mcp_dependency_install",
)
QUERY_SERVER = "shilun_readonly"
QUERY_TOOLS = ("list_backend_modules", "search_backend_queries", "query_backend")
QUERY_CONTEXT_ENV = {
    "app_id": "SHILUN_QUERY_APP_ID",
    "message_id": "SHILUN_QUERY_MESSAGE_ID",
    "chat_id": "SHILUN_QUERY_CHAT_ID",
    "sender_id": "SHILUN_QUERY_SENDER_ID",
}


@dataclass(frozen=True)
class CodexResult:
    text: str
    session_id: str
    model: str | None = None
    model_source: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    elapsed_seconds: float | None = None
    reasoning_effort: str | None = None
    reasoning_effort_source: str | None = None
    service_tier: str | None = None
    service_tier_source: str | None = None

    @property
    def total_tokens(self) -> int | None:
        """Cached input and reasoning output are subsets, not extra tokens."""
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens


class CodexRunnerError(RuntimeError):
    """A deliberately safe error for application logs and user-facing replies."""

    def __init__(self, message: str, kind: str = "execution") -> None:
        super().__init__(message)
        self.kind = kind


def _working_binary(candidate: str) -> bool:
    path = Path(candidate)
    if not path.is_file() or not os.access(path, os.X_OK):
        return False
    try:
        result = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.lstrip().startswith("codex")


def resolve_codex_binary() -> str:
    """Prefer an explicit executable, then the working desktop bundle, then PATH."""
    configured = os.environ.get("SHILUN_CODEX_BIN", "").strip()
    if configured:
        expanded = str(Path(configured).expanduser())
        candidate = shutil.which(expanded) if not Path(expanded).is_absolute() else expanded
        if candidate and _working_binary(candidate):
            return str(Path(candidate).resolve())
        raise CodexRunnerError("配置的 Codex 程序不可用，请检查 SHILUN_CODEX_BIN。", "unavailable")
    candidates = [str(DESKTOP_CODEX)]
    on_path = shutil.which("codex")
    if on_path:
        candidates.append(on_path)
    for candidate in candidates:
        if _working_binary(candidate):
            return str(Path(candidate).resolve())
    raise CodexRunnerError("没有找到可运行的 Codex 程序，请先安装或修复 Codex CLI。", "unavailable")


def _timeout_seconds(value: float | None) -> float:
    try:
        result = float(value if value is not None else os.environ.get(
            "SHILUN_CODEX_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS,
        ))
    except (TypeError, ValueError):
        raise CodexRunnerError("Codex 超时配置无效。", "config") from None
    if not math.isfinite(result) or not 0 < result <= 1800:
        raise CodexRunnerError("Codex 超时必须大于 0 且不超过 1800 秒。", "config")
    return result


def _model_preferences() -> dict[str, str]:
    """Overlay bot-only preferences without changing the user's Codex config."""
    codex_directory = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    config_path = codex_directory / "config.toml"
    config: dict[str, Any] = {}
    if config_path.exists():
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise CodexRunnerError("无法读取 Codex 模型偏好配置，请检查本机配置文件。", "config") from None
    preferences = {name: value for name in ("model", "model_reasoning_effort", "service_tier")
                   if isinstance(value := config.get(name), str) and value and len(value) <= 128
                   and "\n" not in value and "\r" not in value}
    for environment_name, preference_name in (
        ("SHILUN_CODEX_MODEL", "model"),
        ("SHILUN_CODEX_REASONING_EFFORT", "model_reasoning_effort"),
        ("SHILUN_CODEX_SERVICE_TIER", "service_tier"),
    ):
        if environment_name in os.environ:
            value = _metadata_string(os.environ[environment_name])
            if value is None:
                raise CodexRunnerError(f"{environment_name} 必须为有效的短标识。", "config")
            preferences[preference_name] = value
    return preferences


def _child_environment(
    *, ephemeral: bool, query_environment: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Apply the optional local proxy to this invocation, never to its host."""
    proxy = os.environ.get("SHILUN_CODEX_PROXY")
    if "SHILUN_CODEX_PROXY" in os.environ:
        try:
            if (not isinstance(proxy, str) or not proxy
                    or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in proxy)):
                raise ValueError
            parsed = urlsplit(proxy)
            host = parsed.hostname
            if (parsed.scheme not in {"http", "https", "socks5", "socks5h"}
                    or not host or parsed.port is None or not 1 <= parsed.port <= 65535
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path or "?" in proxy or "#" in proxy):
                raise ValueError
            if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise CodexRunnerError("SHILUN_CODEX_PROXY 必须为带合法端口的本机代理地址。", "config") from None
    if not ephemeral and proxy is None and query_environment is None:
        return None
    environment = dict(os.environ)
    if proxy is not None:
        for name in ("http_proxy", "https_proxy", "all_proxy"):
            environment.pop(name, None)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            environment[name] = proxy
        bypass = []
        for value in (environment.get("NO_PROXY", ""), environment.pop("no_proxy", ""),
                      "localhost,127.0.0.1,::1"):
            for host in value.split(","):
                host = host.strip()
                if host and host not in bypass:
                    bypass.append(host)
        environment["NO_PROXY"] = ",".join(bypass)
    if ephemeral:
        environment["RUST_LOG"] = "off"
    if query_environment is not None:
        # Request identity comes from the accepting service, never stale host
        # state or model arguments. MCP forwards only these four variable names.
        for name in QUERY_CONTEXT_ENV.values():
            environment.pop(name, None)
        environment.update(query_environment)
    return environment


def _query_environment(context: dict[str, str] | None) -> dict[str, str]:
    if context is None:
        return {}
    if not isinstance(context, dict) or not set(context) <= QUERY_CONTEXT_ENV.keys():
        raise CodexRunnerError("后台查询审计上下文无效。", "config")
    environment = {}
    for name, value in context.items():
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is None:
            raise CodexRunnerError("后台查询审计上下文无效。", "config")
        environment[QUERY_CONTEXT_ENV[name]] = value
    return environment


def _query_mcp_overrides(working_directory: Path) -> list[str]:
    """Disable inherited server names, then add one fixed read-only server.

    TOML overrides merge tables: an empty mcp_servers table is not an isolation
    boundary. Only names are used from existing config, never transport, env,
    credentials or tool policies. The global config is still ignored by exec.
    """
    codex_directory = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    user_config = (codex_directory / "config.toml").resolve()
    project_configs = {directory / ".codex" / "config.toml"
                       for directory in (working_directory, *working_directory.parents)}
    project_configs.discard(user_config)
    names: set[str] = set()
    for config_path in sorted(project_configs | {user_config}):
        if not config_path.exists():
            continue
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise CodexRunnerError("无法读取后台查询隔离配置，请检查 Codex 配置文件。", "config") from None
        servers = config.get("mcp_servers", {})
        if not isinstance(servers, dict):
            raise CodexRunnerError("后台查询隔离配置无效。", "config")
        names.update(servers)
    # CLI dotted paths split on every dot and do not understand quoted key
    # segments. A full inline table safely represents arbitrary server names.
    # Disabled entries still require a valid transport in this CLI version.
    disabled = [f'{json.dumps(name, ensure_ascii=False)}={{command="/usr/bin/false",enabled=false}}'
                for name in sorted(names) if name != QUERY_SERVER]
    server = (
        f"{QUERY_SERVER}={{"
        f"command={json.dumps(str(PROJECT_ROOT / '.venv-feishu-query' / 'bin' / 'python'))},"
        f"args=[\"-m\",\"shilun.feishu_readonly_mcp\"],cwd={json.dumps(str(PROJECT_ROOT))},"
        "enabled=true,required=true,startup_timeout_sec=20,tool_timeout_sec=30,"
        # 'never' is not an MCP approval-mode enum. This explicitly approves
        # the three vetted tools; the outer process keeps approval_policy=never.
        'default_tools_approval_mode="approve",'
        f"enabled_tools={json.dumps(QUERY_TOOLS)},"
        f"env_vars={json.dumps(tuple(QUERY_CONTEXT_ENV.values()))}"
        "}"
    )
    # Untrusted skips project config, hooks and rules. This prevents same-name
    # project transport/env fields from surviving table deep merges. It changes
    # only this invocation; it never writes the project's persisted trust state.
    project = "projects={" + json.dumps(str(working_directory)) + '={trust_level="untrusted"}}'
    return [project, "mcp_servers={" + ",".join([*disabled, server]) + "}"]


def _session_id(value: Any) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise CodexRunnerError("Codex 会话标识无效，请重新开始该飞书会话。", "session") from None


def _stop_process_group(process: subprocess.Popen[str]) -> None:
    """Stop both the invocation and descendants that inherited its process group."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        elif process.poll() is None:
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    # The main process can exit before a child does. Kill the group even when
    # process.poll() is already non-None rather than leaving a live descendant.
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=1)
    except (OSError, subprocess.SubprocessError):
        # Diagnostics are discarded, including partial output from a timeout.
        pass


def _metadata_string(value: Any) -> str | None:
    """Accept only short identifiers, never free-form event messages."""
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value):
        return value
    return None


def _token_count(value: Any) -> int | None:
    # bool is an int subclass, but is not a reported token count.
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _parse_result(
    stdout: str,
    previous_session: str | None,
    *,
    configured_preferences: dict[str, str] | None = None,
    elapsed_seconds: float | None = None,
    require_backend_tools: bool = False,
) -> CodexResult:
    session_id = previous_session
    completed = False
    last_text: str | None = None
    explicit_final: str | None = None
    reported: dict[str, str] = {}
    usage: dict[str, int | None] = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            raise CodexRunnerError("Codex 返回格式异常，请稍后重试。", "protocol") from None
        if not isinstance(event, dict):
            raise CodexRunnerError("Codex 返回格式异常，请稍后重试。", "protocol")
        event_type = event.get("type")
        item = event.get("item")
        if (require_backend_tools and event_type == "item.completed"
                and isinstance(item, dict) and item.get("type") == "error"):
            # Startup/runtime errors are also emitted as error *items*, even
            # when exec later exits 0 with a model-written apology. That text
            # cannot establish that the required backend tools were available.
            raise CodexRunnerError("后台只读查询工具不可用，请稍后重试。", "tools")
        if event_type in {"thread.started", "turn.started", "turn.completed"}:
            # Stable exec JSONL currently guarantees usage, but not a model
            # field. Honour model metadata if a CLI version reports it; otherwise
            # explicitly label the invocation's chosen configuration as such.
            containers = [event]
            nested = event.get("thread" if event_type == "thread.started" else "turn")
            if isinstance(nested, dict):
                containers.append(nested)
            for container in containers:
                for name, aliases in (
                    ("model", ("model",)),
                    ("reasoning_effort", ("reasoning_effort", "model_reasoning_effort")),
                    ("service_tier", ("service_tier",)),
                ):
                    for alias in aliases:
                        value = _metadata_string(container.get(alias))
                        if value is not None:
                            reported[name] = value
                            break
        if event_type == "turn.failed":
            raise CodexRunnerError("Codex 未完成本次处理，请检查登录状态或稍后重试。", "execution")
        if event_type == "thread.started":
            emitted = _session_id(event.get("thread_id"))
            if session_id and session_id != emitted:
                raise CodexRunnerError("Codex 返回的会话与请求不一致。", "protocol")
            session_id = emitted
        elif event_type == "turn.completed":
            completed = True
            raw_usage = event.get("usage")
            # Use the final completed turn's reported usage. Do not sum repeated
            # snapshots, derive estimates, or substitute absent counters with 0.
            usage = {name: _token_count(raw_usage.get(name)) if isinstance(raw_usage, dict) else None
                     for name in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")}
        elif event_type == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") != "agent_message":
                continue
            text = item.get("text")
            phase = item.get("phase")
            if isinstance(text, str) and text.strip() and phase != "commentary":
                last_text = text.strip()
                if phase in {"final_answer", "final"}:
                    explicit_final = last_text
    text = explicit_final or last_text
    if not completed or not session_id or not text:
        raise CodexRunnerError("Codex 没有返回完整回复，请稍后重试。", "protocol")
    for subset, total in (("cached_input_tokens", "input_tokens"), ("reasoning_output_tokens", "output_tokens")):
        if usage.get(subset) is not None and usage.get(total) is not None and usage[subset] > usage[total]:
            usage[subset] = None
    preferences = configured_preferences or {}

    def identifier(name: str, configured_name: str) -> tuple[str | None, str]:
        if name in reported:
            return reported[name], "reported"
        configured = _metadata_string(preferences.get(configured_name))
        return (configured, "configured") if configured else (None, "unavailable")

    model, model_source = identifier("model", "model")
    effort, effort_source = identifier("reasoning_effort", "model_reasoning_effort")
    tier, tier_source = identifier("service_tier", "service_tier")
    elapsed = (float(elapsed_seconds)
               if isinstance(elapsed_seconds, (int, float)) and not isinstance(elapsed_seconds, bool)
               and math.isfinite(elapsed_seconds) and elapsed_seconds >= 0 else None)
    return CodexResult(
        text=text, session_id=session_id, model=model, model_source=model_source,
        elapsed_seconds=elapsed, reasoning_effort=effort, reasoning_effort_source=effort_source,
        service_tier=tier, service_tier_source=tier_source, **usage,
    )


def _error_category(message: Any) -> str:
    message = message.lower() if isinstance(message, str) else ""
    if "code mode" in message or "code-mode" in message:
        return "tool_runtime"
    if "unauthorized" in message or "authentication" in message or re.search(r"\b401\b", message):
        return "authentication"
    if "rate limit" in message or re.search(r"\b429\b", message):
        return "rate_limit"
    if any(word in message for word in ("reconnect", "transport", "connection", "timeout", "disconnected")):
        return "connection"
    return "other"


def _progress_state(output: str | bytes | None) -> dict[str, Any] | None:
    """Extract only fixed lifecycle labels and counts, never CLI message text."""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    if not isinstance(output, str):
        return None
    phase, errors, category = None, 0, None
    tools_started: set[str] = set()
    tools_completed: set[str] = set()
    last_tool = None
    phases = {"thread.started": "session_started", "turn.started": "request_started",
              "turn.completed": "completed", "turn.failed": "failed"}
    for line_number, line in enumerate(output.splitlines()):
        try:
            event = json.loads(line)
        except (ValueError, TypeError, RecursionError):
            continue  # The last line of a running stream may be incomplete.
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if not isinstance(kind, str):
            continue
        if kind in phases:
            phase = phases[kind]
        elif kind == "error":
            errors += 1
            phase = "retrying"
            category = _error_category(event.get("message"))
        elif kind in {"item.started", "item.completed"}:
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "error":
                errors += 1
                phase = "failed"
                category = _error_category(item.get("message", item.get("text")))
            elif isinstance(item, dict) and item.get("type") in ("agent_message", "reasoning"):
                phase = "response_started"
            elif (isinstance(item, dict) and item.get("type") == "mcp_tool_call"
                  and item.get("server") == QUERY_SERVER and item.get("tool") in QUERY_TOOLS):
                identifier = item.get("id")
                identifier = identifier if isinstance(identifier, str) else str(line_number)
                if kind == "item.started":
                    tools_started.add(identifier)
                    phase = "tool_started"
                else:
                    tools_completed.add(identifier)
                    phase = "tool_completed"
                last_tool = item["tool"]
    if phase is None:
        return None
    state = {"phase": phase, "error_events": errors, "last_error_category": category}
    if last_tool is not None:
        state.update(server=QUERY_SERVER, tool=last_tool,
                     tool_calls_started=len(tools_started), tool_calls_completed=len(tools_completed))
    return state


def run_codex(
    prompt: str,
    session_id: str | None = None,
    *,
    timeout: float | None = None,
    cwd: Path | str | None = None,
    stop_event: threading.Event | None = None,
    ephemeral: bool = False,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    backend_queries: bool = False,
    query_context: dict[str, str] | None = None,
) -> CodexResult:
    """Run one turn, optionally resuming an explicitly owned conversation UUID.

    The final answer is returned only after a successful CLI exit *and* a
    ``turn.completed`` event. Service shutdown interrupts the whole process group.
    Ephemeral calls replay server-provided context in ``prompt`` rather than
    resuming a local rollout, and do not persist new CLI conversation files.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise CodexRunnerError("消息内容为空，无法交给 Codex 处理。", "input")
    if not isinstance(ephemeral, bool):
        raise CodexRunnerError("Codex 临时会话配置无效。", "config")
    if not isinstance(backend_queries, bool):
        raise CodexRunnerError("后台查询开关必须为布尔值。", "config")
    if not backend_queries and query_context is not None:
        raise CodexRunnerError("未启用后台查询，不能提供查询审计上下文。", "config")
    query_environment = _query_environment(query_context) if backend_queries else None
    if ephemeral and session_id is not None:
        raise CodexRunnerError("临时调用不支持恢复本机会话，请从服务器提供对话上下文。", "session")
    if session_id is not None:
        session_id = _session_id(session_id)
    budget = _timeout_seconds(timeout)
    if stop_event is not None and stop_event.is_set():
        raise CodexRunnerError("本次处理已取消。", "cancelled")
    binary = resolve_codex_binary()
    working_directory = Path(cwd).resolve() if cwd is not None else PROJECT_ROOT
    if not working_directory.is_dir():
        raise CodexRunnerError("Codex 工作目录不存在。", "config")
    argv = [
        binary, "--no-daemon", "-c", 'approval_policy="never"',
        "-c", 'sandbox_mode="read-only"', "-c", 'web_search="disabled"',
        "-c", "mcp_servers={}",
    ]
    preferences = _model_preferences()
    for name, value in preferences.items():
        argv += ["-c", f"{name}={json.dumps(value)}"]
    for name in DISABLED_FEATURES:
        argv += ["-c", f"features.{name}=false"]
    if backend_queries:
        # This model family requires CodeModeOnly according to the official
        # model catalog. The host runs a fresh V8 isolate, rejects all imports,
        # and has no process/fs/network API. Exclude the native namespace from
        # its executable tool set; only the dedicated MCP namespace remains.
        argv += ["-c", "features.code_mode_host=true", "-c", "features.view_image=false",
                 "-c", 'features.code_mode={enabled=false,excluded_tool_namespaces=["functions","clock"]}']
        for override in _query_mcp_overrides(working_directory):
            argv += ["-c", override]
    if ephemeral:
        argv += ["-c", 'history.persistence="none"']
    argv += ["exec"]
    if session_id is None:
        if ephemeral:
            argv += ["--ephemeral"]
        argv += ["--ignore-user-config", "--json", "--color", "never", "-"]
    else:
        argv += ["resume", "--ignore-user-config", "--json", session_id, "-"]
    child_environment = _child_environment(ephemeral=ephemeral, query_environment=query_environment)
    started = time.monotonic()
    try:
        options: dict[str, Any] = {}
        if child_environment is not None:
            # Keep official CLI auth discovery intact and never repurpose HOME
            # or CODEX_HOME when changing this child's tracing or proxy route.
            options["env"] = child_environment
        process = subprocess.Popen(
            argv, cwd=str(working_directory), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", start_new_session=True,
            **options,
        )
    except OSError:
        raise CodexRunnerError("无法启动 Codex 程序，请检查本机安装。", "unavailable") from None
    deadline = started + budget
    input_pipe = process.stdin
    # communicate(timeout=...) may time out before a large prompt has been
    # written. On Python 3.12, resuming it with input=None does not register
    # stdin again, so the child can wait forever for the rest of the prompt.
    # One writer owns stdin through EOF; communicate only drains the outputs.
    process.stdin = None
    input_failed = threading.Event()

    def write_prompt() -> None:
        try:
            if input_pipe is None:
                input_failed.set()
                return
            input_pipe.write(prompt)
            input_pipe.flush()
        except (OSError, ValueError):
            # Broken pipes are expected when cancellation terminates the child.
            # Keep all input and raw diagnostics out of logs and exceptions.
            input_failed.set()
        finally:
            if input_pipe is not None:
                try:
                    input_pipe.close()
                except (OSError, ValueError):
                    input_failed.set()

    input_writer = threading.Thread(target=write_prompt, name="feishu-codex-stdin", daemon=True)
    last_progress = None

    def report(output: str | bytes | None) -> None:
        nonlocal last_progress
        if on_progress is None:
            return
        state = _progress_state(output)
        if state is not None and state != last_progress:
            last_progress = state
            try:
                on_progress({**state, "elapsed_seconds": round(time.monotonic() - started, 1)})
            except Exception:
                pass  # Diagnostics must not interrupt a turn or write locally.

    try:
        input_writer.start()
        while True:
            if stop_event is not None and stop_event.is_set():
                _stop_process_group(process)
                raise CodexRunnerError("本次处理已取消。", "cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_process_group(process)
                raise CodexRunnerError("Codex 回复超时，请稍后重试。", "timeout")
            try:
                stdout, _stderr = process.communicate(
                    timeout=min(0.25, remaining),
                )
                report(stdout)
                break
            except subprocess.TimeoutExpired as exc:
                report(exc.output)
        if stop_event is not None and stop_event.is_set():
            _stop_process_group(process)
            raise CodexRunnerError("本次处理已取消。", "cancelled")
        if process.returncode != 0:
            raise CodexRunnerError("Codex 处理失败，请检查本机登录状态或稍后重试。", "execution")
        input_writer.join(timeout=1)
        if input_writer.is_alive():
            # A descendant may retain stdin after the main process exits.
            # Kill the whole invocation before joining its blocked writer.
            _stop_process_group(process)
            input_writer.join(timeout=2)
            raise CodexRunnerError("Codex 未能完整接收输入，请稍后重试。", "input")
        if input_failed.is_set():
            raise CodexRunnerError("Codex 未能完整接收输入，请稍后重试。", "input")
        return _parse_result(stdout, session_id, configured_preferences=preferences,
                             elapsed_seconds=time.monotonic() - started,
                             require_backend_tools=backend_queries)
    except CodexRunnerError:
        raise
    except BaseException:
        _stop_process_group(process)
        raise
    finally:
        if input_writer.ident is not None:
            if input_writer.is_alive():
                _stop_process_group(process)
            input_writer.join(timeout=2)
        elif input_pipe is not None:
            # Thread startup itself failed; there is no concurrent owner.
            try:
                input_pipe.close()
            except (OSError, ValueError):
                pass
