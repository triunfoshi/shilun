"""Offline process/protocol checks; synthetic CLIs never call a model."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import tomllib
import unittest
from unittest.mock import Mock, patch

from shilun import feishu_codex_runner as runner


SESSION = "019a1234-5678-7abc-8def-0123456789ab"
OTHER_SESSION = "019a1234-5678-7abc-8def-0123456789ac"
PRIVATE_DIAGNOSTIC = "synthetic-private-token-never-forward"


def _events(*, text="你好，已接入。", session=SESSION, phase=None):
    message = {"type": "agent_message", "text": text}
    if phase:
        message["phase"] = phase
    return [
        {"type": "thread.started", "thread_id": session},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "reasoning", "text": PRIVATE_DIAGNOSTIC}},
        {"type": "item.completed", "item": message},
        {"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 2}},
    ]


def _jsonl(events):
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


def _fake_cli(directory: Path, *, events=None, returncode=0, hang=False, child_marker=None,
              read_delay=0.0):
    executable = directory / "synthetic-codex"
    capture = directory / "invocation.json"
    source = f"""#!{sys.executable}
import json, pathlib, subprocess, sys, time
if '--version' in sys.argv:
    print('codex-cli synthetic-offline')
    raise SystemExit(0)
time.sleep({read_delay!r})
prompt = sys.stdin.read()
pathlib.Path({str(capture)!r}).write_text(json.dumps({{'argv':sys.argv[1:], 'stdin':prompt}}))
"""
    if child_marker is not None:
        child_code = f"import time,pathlib; time.sleep(0.8); pathlib.Path({str(child_marker)!r}).write_text('child-survived')"
        source += f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
    if hang:
        source += "while True: time.sleep(0.05)\n"
    else:
        source += f"sys.stdout.write({_jsonl(_events() if events is None else events)!r})\n"
        source += f"sys.stderr.write({PRIVATE_DIAGNOSTIC!r})\n"
        source += f"raise SystemExit({returncode})\n"
    executable.write_text(source)
    executable.chmod(0o700)
    return executable, capture


class ResultProtocolTests(unittest.TestCase):
    def test_only_final_answer_is_returned(self):
        events = _events(text="最终总结", phase="final_answer")
        events.insert(-2, {"type": "item.completed", "item": {
            "type": "agent_message", "phase": "commentary", "text": "正在分析"}})
        events.insert(-1, {"type": "item.completed", "item": {
            "type": "command_execution", "aggregated_output": PRIVATE_DIAGNOSTIC}})
        result = runner._parse_result(_jsonl(events), None)
        self.assertEqual("最终总结", result.text)
        self.assertEqual(SESSION, result.session_id)
        self.assertNotIn(PRIVATE_DIAGNOSTIC, repr(result))

    def test_resume_accepts_stored_session_when_no_thread_event_is_repeated(self):
        events = _events()[1:]
        result = runner._parse_result(_jsonl(events), SESSION)
        self.assertEqual(SESSION, result.session_id)

    def test_failures_are_not_presented_as_complete_answers(self):
        examples = [
            _events() + [{"type": "turn.failed", "error": {"message": PRIVATE_DIAGNOSTIC}}],
            _events()[:-1],
            _events(text="中间进度", phase="commentary"),
            _events()[1:],
            [*(_events()[:1]), {"type": "thread.started", "thread_id": OTHER_SESSION}, *_events()[1:]],
        ]
        for events in examples:
            with self.subTest(events=events):
                with self.assertRaises(runner.CodexRunnerError) as caught:
                    runner._parse_result(_jsonl(events), None)
                self.assertNotIn(PRIVATE_DIAGNOSTIC, str(caught.exception))

    def test_recoverable_error_does_not_override_a_completed_turn(self):
        events = _events()
        events.insert(2, {"type": "error", "message": PRIVATE_DIAGNOSTIC})
        self.assertEqual("你好，已接入。", runner._parse_result(_jsonl(events), None).text)

    def test_backend_error_item_fails_closed_without_forwarding_model_apology(self):
        events = _events(text="工具不可调用，猜测没有数据")
        events.insert(1, {"type": "item.completed", "item": {
            "type": "error", "message": "Code Mode is unavailable " + PRIVATE_DIAGNOSTIC}})
        # Legacy text-only calls keep their existing tolerant parser.
        self.assertEqual("工具不可调用，猜测没有数据", runner._parse_result(_jsonl(events), None).text)
        with self.assertRaises(runner.CodexRunnerError) as caught:
            runner._parse_result(_jsonl(events), None, require_backend_tools=True)
        self.assertEqual("tools", caught.exception.kind)
        self.assertNotIn(PRIVATE_DIAGNOSTIC, str(caught.exception))
        self.assertNotIn("没有数据", str(caught.exception))

    def test_malformed_json_and_mismatched_session_fail_closed(self):
        for content, session in (("not-json " + PRIVATE_DIAGNOSTIC, None), ("[]", None),
                                 (_jsonl(_events(session=OTHER_SESSION)), SESSION)):
            with self.subTest(content=content):
                with self.assertRaises(runner.CodexRunnerError) as caught:
                    runner._parse_result(content, session)
                self.assertNotIn(PRIVATE_DIAGNOSTIC, str(caught.exception))

    def test_reported_per_call_usage_keeps_subsets_without_double_counting(self):
        events = _events()
        events[-1]["usage"] = {
            "input_tokens": 150, "cached_input_tokens": 100,
            "output_tokens": 20, "reasoning_output_tokens": 12,
        }
        result = runner._parse_result(_jsonl(events), None)
        self.assertEqual((150, 100, 20, 12), (
            result.input_tokens, result.cached_input_tokens,
            result.output_tokens, result.reasoning_output_tokens,
        ))
        self.assertEqual(170, result.total_tokens)

    def test_missing_usage_stays_unknown_and_reported_zero_is_preserved(self):
        for usage in (None, {}, {"input_tokens": 0, "output_tokens": 0}):
            events = _events()
            events[-1]["usage"] = usage
            with self.subTest(usage=usage):
                result = runner._parse_result(_jsonl(events), None)
                known_zero = bool(usage)
                self.assertEqual(0 if known_zero else None, result.input_tokens)
                self.assertEqual(0 if known_zero else None, result.output_tokens)
                self.assertEqual(0 if known_zero else None, result.total_tokens)
                self.assertIsNone(result.cached_input_tokens)
                self.assertIsNone(result.reasoning_output_tokens)

    def test_invalid_usage_values_are_not_estimated(self):
        events = _events()
        events[-1]["usage"] = {
            "input_tokens": True, "cached_input_tokens": -5,
            "output_tokens": "20", "reasoning_output_tokens": 2.0,
        }
        result = runner._parse_result(_jsonl(events), None)
        self.assertTrue(all(value is None for value in (
            result.input_tokens, result.cached_input_tokens, result.output_tokens,
            result.reasoning_output_tokens, result.total_tokens,
        )))
        events[-1]["usage"] = {
            "input_tokens": 10, "cached_input_tokens": 11,
            "output_tokens": 4, "reasoning_output_tokens": 5,
        }
        result = runner._parse_result(_jsonl(events), None)
        self.assertEqual(14, result.total_tokens)
        self.assertIsNone(result.cached_input_tokens)
        self.assertIsNone(result.reasoning_output_tokens)

    def test_repeated_completed_usage_is_not_added_twice(self):
        events = _events()
        events.append({"type": "turn.completed", "usage": {"input_tokens": 8, "output_tokens": 3}})
        result = runner._parse_result(_jsonl(events), None)
        self.assertEqual(11, result.total_tokens)

    def test_reported_model_metadata_wins_over_configuration(self):
        events = _events()
        events[0]["model"] = "gpt-alias"
        events[-1]["turn"] = {
            "model": "gpt-reported-snapshot", "reasoning_effort": "low", "service_tier": "default",
        }
        result = runner._parse_result(_jsonl(events), None, configured_preferences={
            "model": "gpt-configured", "model_reasoning_effort": "ultra", "service_tier": "priority",
        })
        self.assertEqual(("gpt-reported-snapshot", "reported"), (result.model, result.model_source))
        self.assertEqual(("low", "reported"), (result.reasoning_effort, result.reasoning_effort_source))
        self.assertEqual(("default", "reported"), (result.service_tier, result.service_tier_source))

    def test_configured_metadata_is_labelled_and_tool_output_is_not_metadata(self):
        events = _events()
        events.insert(-1, {"type": "item.completed", "model": PRIVATE_DIAGNOSTIC,
                           "item": {"type": "command_execution", "model": PRIVATE_DIAGNOSTIC}})
        result = runner._parse_result(_jsonl(events), None, configured_preferences={
            "model": "gpt-configured", "model_reasoning_effort": "ultra", "service_tier": "priority",
        })
        self.assertEqual(("gpt-configured", "configured"), (result.model, result.model_source))
        self.assertEqual(("ultra", "configured"), (result.reasoning_effort, result.reasoning_effort_source))
        self.assertEqual(("priority", "configured"), (result.service_tier, result.service_tier_source))
        self.assertNotIn(PRIVATE_DIAGNOSTIC, repr(result))
        unknown = runner._parse_result(_jsonl(events), None)
        self.assertIsNone(unknown.model)
        self.assertEqual("unavailable", unknown.model_source)

    def test_invalid_metadata_strings_and_elapsed_values_stay_unknown(self):
        events = _events()
        events[-1]["model"] = "invalid model=private"
        for elapsed in (None, True, -1, float("nan"), float("inf")):
            with self.subTest(elapsed=elapsed):
                result = runner._parse_result(_jsonl(events), None, elapsed_seconds=elapsed)
                self.assertIsNone(result.model)
                self.assertIsNone(result.elapsed_seconds)
        self.assertIsNone(runner.CodexResult("compatible", SESSION).total_tokens)


class InvocationTests(unittest.TestCase):
    def test_backend_queries_enable_only_the_fixed_server_and_vetted_tools(self):
        with TemporaryDirectory(prefix="shilun-query-isolation-") as directory:
            root = Path(directory)
            user = root / "global"
            project = root / "project"
            working = project / "nested"
            user.mkdir()
            working.mkdir(parents=True)
            (project / ".codex").mkdir()
            user_config = user / "config.toml"
            user_contents = ('[mcp_servers.unknown]\ncommand="' + PRIVATE_DIAGNOSTIC + '"\n'
                             '[mcp_servers.unknown.env]\nTOKEN="' + PRIVATE_DIAGNOSTIC + '"\n')
            user_config.write_text(user_contents)
            (user / "auth.json").write_text(PRIVATE_DIAGNOSTIC)
            project_config = project / ".codex" / "config.toml"
            project_contents = '[mcp_servers."custom.with-dots"]\nurl="https://private.invalid"\n'
            project_config.write_text(project_contents)
            process = Mock(returncode=0)
            process.communicate.return_value = (_jsonl(_events()), PRIVATE_DIAGNOSTIC)
            context = {"app_id": "cli_audit_app", "message_id": "om_audit_message",
                       "chat_id": "oc_audit_chat", "sender_id": "ou_audit_sender"}
            environment = {"CODEX_HOME": str(user), "HOME": "/synthetic/home",
                           "OPENAI_API_KEY": PRIVATE_DIAGNOSTIC,
                           "SHILUN_QUERY_MESSAGE_ID": "stale-message"}
            with (patch.dict(os.environ, environment, clear=True),
                  patch.object(runner, "resolve_codex_binary", return_value="/synthetic/codex"),
                  patch.object(runner, "_model_preferences", return_value={}),
                  patch.object(runner.subprocess, "Popen", return_value=process) as spawn):
                result = runner.run_codex("查询模块", timeout=5, cwd=working, ephemeral=True,
                                          backend_queries=True, query_context=context)
                self.assertEqual(environment, dict(os.environ))
            argv = spawn.call_args.args[0]
            joined = " ".join(argv)
            self.assertIn("--ignore-user-config", argv)
            self.assertIn("--ephemeral", argv)
            self.assertIn('approval_policy="never"', argv)
            self.assertIn('sandbox_mode="read-only"', argv)
            self.assertIn('history.persistence="none"', argv)
            for feature in runner.DISABLED_FEATURES:
                self.assertIn(f"features.{feature}=false", argv)
            self.assertIn("features.code_mode_host=true", argv)
            self.assertIn("features.view_image=false", argv)
            self.assertIn('features.code_mode={enabled=false,excluded_tool_namespaces=["functions","clock"]}', argv)
            self.assertGreater(argv.index("features.code_mode_host=true"), argv.index("features.code_mode_host=false"))
            self.assertFalse(any(f"features.{name}=true" in argv
                                 for name in runner.DISABLED_FEATURES if name != "code_mode_host"))
            server_override = next(arg for arg in argv if arg.startswith("mcp_servers={") and arg != "mcp_servers={}")
            servers = tomllib.loads(server_override)["mcp_servers"]
            self.assertEqual({"unknown", "custom.with-dots", runner.QUERY_SERVER}, set(servers))
            for name in ("unknown", "custom.with-dots"):
                self.assertEqual({"command": "/usr/bin/false", "enabled": False}, servers[name])
            trust = tomllib.loads(next(arg for arg in argv if arg.startswith("projects=")))
            self.assertEqual({str(working.resolve()): {"trust_level": "untrusted"}}, trust["projects"])
            server = servers[runner.QUERY_SERVER]
            self.assertEqual(str(runner.PROJECT_ROOT / ".venv-feishu-query" / "bin" / "python"),
                             server["command"])
            self.assertEqual(["-m", "shilun.feishu_readonly_mcp"], server["args"])
            self.assertEqual(str(runner.PROJECT_ROOT), server["cwd"])
            self.assertEqual(list(runner.QUERY_TOOLS), server["enabled_tools"])
            self.assertEqual((True, True, 20, 30, "approve"), (
                server["enabled"], server["required"], server["startup_timeout_sec"],
                server["tool_timeout_sec"], server["default_tools_approval_mode"],
            ))
            self.assertEqual(set(runner.QUERY_CONTEXT_ENV.values()), set(server["env_vars"]))
            self.assertNotIn("env", server)
            child = spawn.call_args.kwargs["env"]
            for name, value in context.items():
                self.assertEqual(value, child[runner.QUERY_CONTEXT_ENV[name]])
                self.assertNotIn(value, joined)
            self.assertNotIn(PRIVATE_DIAGNOSTIC, joined)
            self.assertNotIn("auth.json", joined)
            self.assertNotIn("private.invalid", joined)
            self.assertEqual("off", child["RUST_LOG"])
            self.assertEqual(str(user), child["CODEX_HOME"])
            self.assertEqual(user_contents, user_config.read_text())
            self.assertEqual(project_contents, project_config.read_text())
            self.assertEqual("你好，已接入。", result.text)

    def test_backend_queries_false_keeps_existing_invocation_and_environment(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (_jsonl(_events()), PRIVATE_DIAGNOSTIC)
        with (patch.object(runner, "resolve_codex_binary", return_value="/synthetic/codex"),
              patch.object(runner, "_model_preferences", return_value={}),
              patch.object(runner, "_query_mcp_overrides", side_effect=AssertionError("must not inspect")),
              patch.object(runner.subprocess, "Popen", return_value=process) as spawn):
            runner.run_codex("原有调用", timeout=5, backend_queries=False)
        self.assertIn("mcp_servers={}", spawn.call_args.args[0])
        self.assertFalse(any("shilun_readonly" in arg for arg in spawn.call_args.args[0]))
        self.assertNotIn("env", spawn.call_args.kwargs)

    def test_backend_context_is_strict_and_rejects_private_fields_before_startup(self):
        invalid_options = [
            {"backend_queries": value} for value in (None, "true", 0, 1, [], {})
        ] + [
            {"backend_queries": True, "query_context": value}
            for value in ([], "context", {"token": PRIVATE_DIAGNOSTIC},
                          {"app_id": ""}, {"message_id": "bad\nidentifier"},
                          {"sender_id": 1}, {"chat_id": "x" * 129})
        ] + [{"backend_queries": False, "query_context": {"app_id": "cli_test"}}]
        with patch.object(runner, "resolve_codex_binary", side_effect=AssertionError("must not resolve")):
            for options in invalid_options:
                with self.subTest(options=options), self.assertRaises(runner.CodexRunnerError) as caught:
                    runner.run_codex("查询", **options)
                self.assertEqual("config", caught.exception.kind)
                self.assertNotIn(PRIVATE_DIAGNOSTIC, str(caught.exception))

    def test_backend_context_does_not_inherit_stale_request_identity(self):
        environment = {name: "stale" for name in runner.QUERY_CONTEXT_ENV.values()}
        with patch.dict(os.environ, environment, clear=True):
            child = runner._child_environment(ephemeral=True, query_environment={})
            self.assertTrue(all(name not in child for name in runner.QUERY_CONTEXT_ENV.values()))
            self.assertEqual(environment, dict(os.environ))

    def test_backend_queries_exclude_same_name_project_transport_and_secrets(self):
        with TemporaryDirectory(prefix="shilun-query-collision-") as directory:
            root = Path(directory)
            (root / ".codex").mkdir()
            (root / ".codex" / "config.toml").write_text(
                '[mcp_servers.shilun_readonly]\nurl="https://private.invalid"\n'
                '[mcp_servers.shilun_readonly.http_headers]\nToken="' + PRIVATE_DIAGNOSTIC + '"\n')
            with (patch.dict(os.environ, {"CODEX_HOME": str(root / "global")}, clear=True),
                  patch.object(runner, "_model_preferences", return_value={})):
                overrides = runner._query_mcp_overrides(root)
            self.assertEqual({str(root): {"trust_level": "untrusted"}},
                             tomllib.loads(overrides[0])["projects"])
            server = tomllib.loads(overrides[1])["mcp_servers"][runner.QUERY_SERVER]
            self.assertNotIn("url", server)
            self.assertNotIn("http_headers", server)
            self.assertNotIn(PRIVATE_DIAGNOSTIC, " ".join(overrides))

    def test_tool_progress_uses_only_fixed_names_counts_and_phases(self):
        events = [
            {"type": "turn.started"},
            {"type": "item.started", "item": {
                "id": "query-1", "type": "mcp_tool_call", "server": runner.QUERY_SERVER,
                "tool": "query_backend", "arguments": {"secret": PRIVATE_DIAGNOSTIC}}},
        ]
        started = runner._progress_state(_jsonl(events))
        self.assertEqual(("tool_started", 1, 0), (
            started["phase"], started["tool_calls_started"], started["tool_calls_completed"]))
        events += [
            events[-1],
            {"type": "item.completed", "item": {
                "id": "query-1", "type": "mcp_tool_call", "server": runner.QUERY_SERVER,
                "tool": "query_backend", "result": PRIVATE_DIAGNOSTIC}},
            {"type": "item.completed", "item": {
                "id": "unknown", "type": "mcp_tool_call", "server": "private-server",
                "tool": PRIVATE_DIAGNOSTIC, "result": PRIVATE_DIAGNOSTIC}},
            {"type": "item.completed", "item": {
                "id": "unlisted", "type": "mcp_tool_call", "server": runner.QUERY_SERVER,
                "tool": PRIVATE_DIAGNOSTIC, "result": PRIVATE_DIAGNOSTIC}},
        ]
        completed = runner._progress_state(_jsonl(events))
        self.assertEqual(("tool_completed", 1, 1), (
            completed["phase"], completed["tool_calls_started"], completed["tool_calls_completed"]))
        self.assertEqual((runner.QUERY_SERVER, "query_backend"), (completed["server"], completed["tool"]))
        self.assertNotIn(PRIVATE_DIAGNOSTIC, json.dumps(completed))
        self.assertNotIn("private-server", json.dumps(completed))
        self.assertEqual("你好，已接入。", runner._parse_result(_jsonl([*_events(), *events]), None).text)

    def test_error_item_progress_reports_only_safe_runtime_category(self):
        events = [{"type": "item.completed", "item": {
            "type": "error", "message": "Code mode will fail closed " + PRIVATE_DIAGNOSTIC}}]
        state = runner._progress_state(_jsonl(events))
        self.assertEqual({"phase": "failed", "error_events": 1,
                          "last_error_category": "tool_runtime"}, state)
        self.assertNotIn(PRIVATE_DIAGNOSTIC, json.dumps(state))

    def test_backend_error_item_is_not_returned_as_a_successful_answer(self):
        events = _events()
        events.insert(1, {"type": "item.completed", "item": {
            "type": "error", "message": "Code Mode is unavailable " + PRIVATE_DIAGNOSTIC}})
        process = Mock(returncode=0)
        process.communicate.return_value = (_jsonl(events), PRIVATE_DIAGNOSTIC)
        with (patch.object(runner, "resolve_codex_binary", return_value="/synthetic/codex"),
              patch.object(runner, "_model_preferences", return_value={}),
              patch.object(runner, "_query_mcp_overrides", return_value=[]),
              patch.object(runner.subprocess, "Popen", return_value=process),
              self.assertRaises(runner.CodexRunnerError) as caught):
            runner.run_codex("查询", timeout=5, backend_queries=True)
        self.assertEqual("tools", caught.exception.kind)

    def test_ephemeral_turn_avoids_local_conversation_persistence(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (_jsonl(_events()), PRIVATE_DIAGNOSTIC)
        environment = {"HOME": "/synthetic/home", "CODEX_HOME": "/synthetic/codex", "RUST_LOG": "debug"}
        with (
            patch.dict(os.environ, environment),
            patch.object(runner, "resolve_codex_binary", return_value="/synthetic/codex"),
            patch.object(runner, "_model_preferences", return_value={}),
            patch.object(runner.subprocess, "Popen", return_value=process) as spawn,
        ):
            result = runner.run_codex("服务器提供的独立上下文", timeout=5, ephemeral=True)
            self.assertEqual("debug", os.environ["RUST_LOG"])
            self.assertEqual(environment["HOME"], os.environ["HOME"])
            self.assertEqual(environment["CODEX_HOME"], os.environ["CODEX_HOME"])
        argv = spawn.call_args.args[0]
        child_environment = spawn.call_args.kwargs["env"]
        self.assertIn("--ephemeral", argv)
        self.assertIn('history.persistence="none"', argv)
        self.assertFalse(any(arg.startswith("model_providers.openai.") for arg in argv))
        self.assertFalse(any("base_url=" in arg or "api_key=" in arg for arg in argv))
        self.assertNotIn("resume", argv)
        self.assertEqual("off", child_environment["RUST_LOG"])
        self.assertEqual(environment["HOME"], child_environment["HOME"])
        self.assertEqual(environment["CODEX_HOME"], child_environment["CODEX_HOME"])
        self.assertEqual(SESSION, result.session_id)

    def test_progress_reports_safe_retry_counts_before_completion(self):
        partial = _jsonl([{"type": "thread.started", "thread_id": SESSION},
                          {"type": "turn.started"},
                          {"type": "error", "message": "Reconnecting " + PRIVATE_DIAGNOSTIC}])
        process = Mock(returncode=0)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("synthetic", 0.25, output=partial.encode()),
            (_jsonl(_events()), PRIVATE_DIAGNOSTIC),
        ]
        states = []
        with (patch.object(runner, "resolve_codex_binary", return_value="/synthetic/codex"),
              patch.object(runner, "_model_preferences", return_value={}),
              patch.object(runner.subprocess, "Popen", return_value=process)):
            result = runner.run_codex("合成输入", timeout=5, on_progress=states.append)
        self.assertEqual("你好，已接入。", result.text)
        self.assertEqual(("retrying", 1, "connection"),
                         (states[0]["phase"], states[0]["error_events"], states[0]["last_error_category"]))
        self.assertEqual("completed", states[-1]["phase"])
        self.assertNotIn(PRIVATE_DIAGNOSTIC, json.dumps(states))
        self.assertNotIn(SESSION, json.dumps(states))

    def test_progress_failure_cannot_fail_a_successful_model_turn(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (_jsonl(_events()), PRIVATE_DIAGNOSTIC)
        with (patch.object(runner, "resolve_codex_binary", return_value="/synthetic/codex"),
              patch.object(runner, "_model_preferences", return_value={}),
              patch.object(runner.subprocess, "Popen", return_value=process)):
            result = runner.run_codex("合成输入", timeout=5,
                                      on_progress=Mock(side_effect=RuntimeError(PRIVATE_DIAGNOSTIC)))
        self.assertEqual("你好，已接入。", result.text)

    def test_ephemeral_resume_is_rejected_before_starting_codex(self):
        with patch.object(runner, "resolve_codex_binary", side_effect=AssertionError("must not resolve")):
            with self.assertRaises(runner.CodexRunnerError) as caught:
                runner.run_codex("服务器上下文", session_id=SESSION, ephemeral=True)
        self.assertEqual("session", caught.exception.kind)

    def test_persistent_turn_does_not_override_child_environment(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (_jsonl(_events()), PRIVATE_DIAGNOSTIC)
        with (
            patch.object(runner, "resolve_codex_binary", return_value="/synthetic/codex"),
            patch.object(runner, "_model_preferences", return_value={}),
            patch.object(runner.subprocess, "Popen", return_value=process) as spawn,
        ):
            runner.run_codex("原有调用", timeout=5)
        self.assertNotIn("--ephemeral", spawn.call_args.args[0])
        self.assertNotIn('history.persistence="none"', spawn.call_args.args[0])
        self.assertNotIn("env", spawn.call_args.kwargs)

    def test_local_proxy_is_child_only_and_preserves_bypass_and_ephemeral_logging(self):
        proxy = "http://127.0.0.1:7897"
        for ephemeral in (False, True):
            with self.subTest(ephemeral=ephemeral):
                environment = {"SHILUN_CODEX_PROXY": proxy, "HOME": "/synthetic/home",
                               "CODEX_HOME": "/synthetic/codex", "RUST_LOG": "debug",
                               "HTTP_PROXY": "http://old-upper.invalid:8000",
                               "http_proxy": "http://old-lower.invalid:8000",
                               "https_proxy": "http://old-lower.invalid:8000",
                               "all_proxy": "http://old-lower.invalid:8000",
                               "NO_PROXY": "internal.example, localhost",
                               "no_proxy": "other.internal,internal.example"}
                process = Mock(returncode=0)
                process.communicate.return_value = (_jsonl(_events()), PRIVATE_DIAGNOSTIC)
                with (patch.dict(os.environ, environment, clear=True),
                      patch.object(runner, "resolve_codex_binary", return_value="/synthetic/codex"),
                      patch.object(runner, "_model_preferences", return_value={}),
                      patch.object(runner.subprocess, "Popen", return_value=process) as spawn):
                    runner.run_codex("离线代理配置测试", timeout=5, ephemeral=ephemeral)
                    self.assertEqual(environment, dict(os.environ))
                child = spawn.call_args.kwargs["env"]
                for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                    self.assertEqual(proxy, child[name])
                for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
                    self.assertNotIn(name, child)
                self.assertEqual("internal.example,localhost,other.internal,127.0.0.1,::1", child["NO_PROXY"])
                self.assertEqual("off" if ephemeral else "debug", child["RUST_LOG"])
                for name in ("HOME", "CODEX_HOME"):
                    self.assertEqual(environment[name], child[name])
                self.assertFalse(any(proxy in argument for argument in spawn.call_args.args[0]))

    def test_invocation_reports_captured_preferences_and_cli_elapsed_time(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (_jsonl(_events()), PRIVATE_DIAGNOSTIC)
        preferences = {"model": "gpt-invocation", "model_reasoning_effort": "ultra", "service_tier": "priority"}
        with (
            patch.object(runner, "resolve_codex_binary", return_value="/synthetic/codex"),
            patch.object(runner, "_model_preferences", return_value=preferences) as model_preferences,
            patch.object(runner.subprocess, "Popen", return_value=process) as spawn,
            patch.object(runner.time, "monotonic", side_effect=[100.0, 100.01, 100.125]),
        ):
            result = runner.run_codex("离线计时测试", timeout=5)
        self.assertEqual(0.125, result.elapsed_seconds)
        self.assertEqual(("gpt-invocation", "configured"), (result.model, result.model_source))
        self.assertEqual("ultra", result.reasoning_effort)
        self.assertEqual("priority", result.service_tier)
        self.assertEqual(4, result.total_tokens)
        self.assertIn('model="gpt-invocation"', spawn.call_args.args[0])
        model_preferences.assert_called_once_with()

    def test_initial_turn_uses_stdin_and_fixed_restricted_options(self):
        with TemporaryDirectory(prefix="shilun-codex-test-") as directory:
            root = Path(directory)
            executable, capture = _fake_cli(root)
            prompt = f"你好; $(touch {root / 'must-not-exist'}) {PRIVATE_DIAGNOSTIC}"
            with patch.dict(os.environ, {"SHILUN_CODEX_BIN": str(executable)}):
                result = runner.run_codex(prompt, timeout=5, cwd=root)
            invocation = json.loads(capture.read_text())
            self.assertEqual(prompt, invocation["stdin"])
            self.assertNotIn(PRIVATE_DIAGNOSTIC, " ".join(invocation["argv"]))
            self.assertIn('approval_policy="never"', invocation["argv"])
            self.assertIn('sandbox_mode="read-only"', invocation["argv"])
            self.assertIn("--no-daemon", invocation["argv"])
            self.assertIn("--ignore-user-config", invocation["argv"])
            self.assertIn('web_search="disabled"', invocation["argv"])
            self.assertIn("mcp_servers={}", invocation["argv"])
            for feature in runner.DISABLED_FEATURES:
                self.assertIn(f"features.{feature}=false", invocation["argv"])
            self.assertEqual("-", invocation["argv"][-1])
            self.assertNotIn("--last", invocation["argv"])
            self.assertFalse((root / "must-not-exist").exists())
            self.assertEqual("你好，已接入。", result.text)

    def test_resume_targets_only_the_explicit_uuid(self):
        with TemporaryDirectory(prefix="shilun-codex-test-") as directory:
            root = Path(directory)
            executable, capture = _fake_cli(root)
            with patch.dict(os.environ, {"SHILUN_CODEX_BIN": str(executable)}):
                result = runner.run_codex("继续", session_id=SESSION, timeout=5, cwd=root)
            args = json.loads(capture.read_text())["argv"]
            self.assertEqual(["exec", "resume", "--ignore-user-config", "--json", SESSION, "-"], args[-6:])
            self.assertIn('approval_policy="never"', args)
            self.assertIn('sandbox_mode="read-only"', args)
            for feature in runner.DISABLED_FEATURES:
                self.assertIn(f"features.{feature}=false", args)
            self.assertEqual(SESSION, result.session_id)
            self.assertNotIn("--last", args)

    def test_large_prompt_is_fully_written_after_initial_communicate_timeout(self):
        with TemporaryDirectory(prefix="shilun-codex-test-") as directory:
            root = Path(directory)
            executable, capture = _fake_cli(root, read_delay=0.6)
            # 120,000 Chinese characters exceed the pipe buffer by a wide
            # margin. The child does not read until two 0.25s polls have passed.
            prompt = "复盘资料" * 30000
            self.assertEqual(360000, len(prompt.encode("utf-8")))
            with patch.dict(os.environ, {"SHILUN_CODEX_BIN": str(executable)}):
                result = runner.run_codex(prompt, timeout=5, cwd=root)
            self.assertEqual(prompt, json.loads(capture.read_text())["stdin"])
            self.assertEqual("你好，已接入。", result.text)
            self.assertFalse(any(thread.name == "feishu-codex-stdin"
                                 for thread in threading.enumerate()))

    def test_cancelled_large_prompt_joins_its_blocked_stdin_writer(self):
        with TemporaryDirectory(prefix="shilun-codex-test-") as directory:
            root = Path(directory)
            executable, capture = _fake_cli(root, read_delay=60)
            stop = threading.Event()
            timer = threading.Timer(0.35, stop.set)
            timer.start()
            started = time.monotonic()
            try:
                with patch.dict(os.environ, {"SHILUN_CODEX_BIN": str(executable)}):
                    with self.assertRaises(runner.CodexRunnerError) as caught:
                        runner.run_codex("复盘资料" * 30000, timeout=5, cwd=root, stop_event=stop)
                self.assertEqual("cancelled", caught.exception.kind)
                self.assertLess(time.monotonic() - started, 3)
                self.assertFalse(capture.exists(), "the delayed reader unexpectedly consumed input")
                self.assertFalse(any(thread.name == "feishu-codex-stdin"
                                     for thread in threading.enumerate()))
            finally:
                timer.cancel()

    def test_nonzero_exit_discards_stdout_and_stderr(self):
        with TemporaryDirectory(prefix="shilun-codex-test-") as directory:
            root = Path(directory)
            executable, _ = _fake_cli(root, events=_events(text=PRIVATE_DIAGNOSTIC), returncode=1)
            with patch.dict(os.environ, {"SHILUN_CODEX_BIN": str(executable)}):
                with self.assertRaises(runner.CodexRunnerError) as caught:
                    runner.run_codex("你好", timeout=5, cwd=root)
            self.assertEqual("execution", caught.exception.kind)
            self.assertNotIn(PRIVATE_DIAGNOSTIC, str(caught.exception))

    def test_invalid_session_and_pre_cancel_do_not_spawn(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(runner, "resolve_codex_binary", side_effect=AssertionError("must not resolve")):
            for options in ({"session_id": "--last"}, {"stop_event": cancelled}):
                with self.subTest(options=options):
                    with self.assertRaises(runner.CodexRunnerError):
                        runner.run_codex("你好", **options)

    @unittest.skipUnless(os.name == "posix", "POSIX process group check")
    def test_timeout_stops_the_invocation_and_its_child(self):
        with TemporaryDirectory(prefix="shilun-codex-test-") as directory:
            root = Path(directory)
            marker = root / "child-survived"
            executable, _ = _fake_cli(root, hang=True, child_marker=marker)
            started = time.monotonic()
            with patch.dict(os.environ, {"SHILUN_CODEX_BIN": str(executable)}):
                with self.assertRaises(runner.CodexRunnerError) as caught:
                    runner.run_codex("你好", timeout=0.3, cwd=root)
            self.assertEqual("timeout", caught.exception.kind)
            self.assertLess(time.monotonic() - started, 3)
            time.sleep(1)
            self.assertFalse(marker.exists(), "a descendant survived the process-group timeout")

    def test_shutdown_event_interrupts_an_active_invocation(self):
        with TemporaryDirectory(prefix="shilun-codex-test-") as directory:
            root = Path(directory)
            executable, _ = _fake_cli(root, hang=True)
            stop = threading.Event()
            timer = threading.Timer(0.3, stop.set)
            timer.start()
            try:
                with patch.dict(os.environ, {"SHILUN_CODEX_BIN": str(executable)}):
                    with self.assertRaises(runner.CodexRunnerError) as caught:
                        runner.run_codex("你好", timeout=10, cwd=root, stop_event=stop)
                self.assertEqual("cancelled", caught.exception.kind)
            finally:
                timer.cancel()


class BinaryAndConfigurationTests(unittest.TestCase):
    def test_proxy_accepts_only_explicit_loopback_endpoints_in_supported_schemes(self):
        for proxy in ("http://127.0.0.1:7897", "https://localhost:443", "socks5://[::1]:1080",
                      "socks5h://127.1.2.3:65535"):
            with self.subTest(proxy=proxy), patch.dict(os.environ, {"SHILUN_CODEX_PROXY": proxy}, clear=True):
                self.assertEqual(proxy, runner._child_environment(ephemeral=False)["HTTPS_PROXY"])

    def test_invalid_proxy_fails_before_starting_without_forwarding_the_url(self):
        values = ("", " ", "http://localhost", "http://localhost:0", "http://localhost:65536",
                  "http://localhost:bad", "http://localhost:-1", "http://localhost:7897/",
                  "http://localhost:7897/path", "http://localhost:7897?", "http://localhost:7897#",
                  "http://localhost:7897?secret=hidden", "http://localhost:7897#hidden",
                  "http://user:secret@localhost:7897", "http://@localhost:7897",
                  "ftp://localhost:7897", "http://example.com:7897", "http://192.168.1.1:7897",
                  "http://0.0.0.0:7897", "http://[::]:7897", "http://localhost:7897\n",
                  " http://localhost:7897", "http://local\thost:7897", "\x00http://localhost:7897",
                  None, 7897, ["http://localhost:7897"])
        for value in values:
            with (self.subTest(value=value),
                  patch.object(runner.os, "environ", {"SHILUN_CODEX_PROXY": value}),
                  patch.object(runner, "resolve_codex_binary", return_value="/synthetic/codex"),
                  patch.object(runner, "_model_preferences", return_value={}),
                  patch.object(runner.subprocess, "Popen") as spawn):
                with self.assertRaises(runner.CodexRunnerError) as caught:
                    runner.run_codex("离线代理校验", timeout=5)
                self.assertEqual("config", caught.exception.kind)
                self.assertEqual("SHILUN_CODEX_PROXY 必须为带合法端口的本机代理地址。", str(caught.exception))
                spawn.assert_not_called()

    def test_no_proxy_override_retains_inherited_environment_behavior(self):
        environment = {"HTTP_PROXY": "http://inherited.invalid:8000", "https_proxy": "inherited-lower",
                       "no_proxy": "existing", "RUST_LOG": "debug"}
        with patch.dict(os.environ, environment, clear=True):
            self.assertIsNone(runner._child_environment(ephemeral=False))
            self.assertEqual({**environment, "RUST_LOG": "off"}, runner._child_environment(ephemeral=True))
            self.assertEqual(environment, dict(os.environ))

    def test_bot_preferences_override_invocation_and_metadata_without_writing_global_config(self):
        with TemporaryDirectory(prefix="shilun-codex-config-") as directory:
            config = Path(directory) / "config.toml"
            original = (b'model="gpt-global"\nmodel_reasoning_effort="ultra"\n'
                        b'service_tier="default"\n[plugins.private]\nenabled=true\n')
            config.write_bytes(original)
            original_mtime = config.stat().st_mtime_ns
            environment = {"CODEX_HOME": directory, "SHILUN_CODEX_MODEL": "gpt-bot",
                           "SHILUN_CODEX_REASONING_EFFORT": "low", "SHILUN_CODEX_SERVICE_TIER": "priority"}
            process = Mock(returncode=0)
            process.communicate.return_value = (_jsonl(_events()), PRIVATE_DIAGNOSTIC)
            with (patch.dict(os.environ, environment, clear=True),
                  patch.object(runner, "resolve_codex_binary", return_value="/synthetic/codex"),
                  patch.object(runner.subprocess, "Popen", return_value=process) as spawn):
                result = runner.run_codex("独立模型配置", timeout=5)
            args = spawn.call_args.args[0]
            for setting in ('model="gpt-bot"', 'model_reasoning_effort="low"', 'service_tier="priority"'):
                self.assertIn(setting, args)
            self.assertNotIn('model="gpt-global"', args)
            self.assertEqual(("gpt-bot", "low", "priority"),
                             (result.model, result.reasoning_effort, result.service_tier))
            self.assertEqual(("configured",) * 3,
                             (result.model_source, result.reasoning_effort_source, result.service_tier_source))
            self.assertEqual(original, config.read_bytes())
            self.assertEqual(original_mtime, config.stat().st_mtime_ns)

    def test_bot_overrides_work_without_a_global_config_file(self):
        with TemporaryDirectory(prefix="shilun-codex-config-") as directory:
            environment = {"CODEX_HOME": directory, "SHILUN_CODEX_MODEL": "gpt-bot",
                           "SHILUN_CODEX_REASONING_EFFORT": "low", "SHILUN_CODEX_SERVICE_TIER": "priority"}
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual({"model": "gpt-bot", "model_reasoning_effort": "low",
                                  "service_tier": "priority"}, runner._model_preferences())
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_partial_override_preserves_other_global_preferences(self):
        with TemporaryDirectory(prefix="shilun-codex-config-") as directory:
            (Path(directory) / "config.toml").write_text(
                'model="gpt-global"\nmodel_reasoning_effort="ultra"\nservice_tier="default"\n')
            with patch.dict(os.environ, {"CODEX_HOME": directory, "SHILUN_CODEX_MODEL": "gpt-bot"}, clear=True):
                self.assertEqual({"model": "gpt-bot", "model_reasoning_effort": "ultra",
                                  "service_tier": "default"}, runner._model_preferences())

    def test_invalid_bot_override_is_rejected_before_spawning_without_echoing_its_value(self):
        with TemporaryDirectory(prefix="shilun-codex-config-") as directory:
            for name in ("SHILUN_CODEX_MODEL", "SHILUN_CODEX_REASONING_EFFORT", "SHILUN_CODEX_SERVICE_TIER"):
                for value in ("", " ", "gpt bot", "gpt\tbot", "gpt\nbot", "gpt\rbot", "gpt\x00bot",
                              'gpt"bot', "gpt=bot", "模组", "x" * 129, None, 42, ["gpt-bot"]):
                    # os.environ normally enforces strings; a plain mapping also
                    # verifies safe handling of malformed injected configuration.
                    with (self.subTest(name=name, value=value),
                          patch.object(runner.os, "environ", {"CODEX_HOME": directory, name: value}),
                          patch.object(runner, "resolve_codex_binary", return_value="/synthetic/codex"),
                          patch.object(runner.subprocess, "Popen") as spawn):
                        with self.assertRaises(runner.CodexRunnerError) as caught:
                            runner.run_codex("离线配置校验", timeout=5)
                        self.assertEqual("config", caught.exception.kind)
                        self.assertEqual(f"{name} 必须为有效的短标识。", str(caught.exception))
                        spawn.assert_not_called()

    def test_missing_global_config_and_no_overrides_keep_cli_defaults(self):
        with TemporaryDirectory(prefix="shilun-codex-config-") as directory:
            with patch.dict(os.environ, {"CODEX_HOME": directory}, clear=True):
                self.assertEqual({}, runner._model_preferences())

    def test_only_model_preferences_are_copied_from_user_config(self):
        with TemporaryDirectory(prefix="shilun-codex-config-") as directory:
            config = Path(directory) / "config.toml"
            config.write_text('model="gpt-test"\nmodel_reasoning_effort="ultra"\nservice_tier="fast"\n'
                              'model_provider="private-provider"\n'
                              '[mcp_servers.private]\ncommand="private-command"\n'
                              '[plugins.private]\nenabled=true\n')
            with patch.dict(os.environ, {"CODEX_HOME": directory}, clear=True):
                self.assertEqual({"model": "gpt-test", "model_reasoning_effort": "ultra",
                                  "service_tier": "fast"}, runner._model_preferences())

    def test_explicit_binary_failure_does_not_fall_back_to_another_account_context(self):
        with patch.dict(os.environ, {"SHILUN_CODEX_BIN": "/does/not/exist"}):
            with self.assertRaises(runner.CodexRunnerError) as caught:
                runner.resolve_codex_binary()
        self.assertEqual("unavailable", caught.exception.kind)

    def test_desktop_binary_is_preferred_over_path_wrapper(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(runner, "_working_binary", return_value=True) as check, \
             patch.object(runner.shutil, "which", return_value="/synthetic/path/codex"):
            self.assertEqual(str(runner.DESKTOP_CODEX.resolve()), runner.resolve_codex_binary())
        self.assertEqual([str(runner.DESKTOP_CODEX)], [call.args[0] for call in check.call_args_list])

    def test_invalid_timeout_is_reported_without_raw_environment_values(self):
        for value in (PRIVATE_DIAGNOSTIC, "nan", "inf", "-1", "1801"):
            with self.subTest(value=value), patch.dict(os.environ, {"SHILUN_CODEX_TIMEOUT_SECONDS": value}):
                with self.assertRaises(runner.CodexRunnerError) as caught:
                    runner._timeout_seconds(None)
                self.assertEqual("config", caught.exception.kind)
                self.assertNotIn(PRIVATE_DIAGNOSTIC, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
