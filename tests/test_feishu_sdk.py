"""Offline checks for SDK credential reuse and bounded diagnostics."""

from __future__ import annotations

import base64
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import logging
from pathlib import Path
import subprocess
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from shilun import feishu_sdk

try:
    from Crypto.Cipher import AES
except ImportError:
    AES = None


APP_ID = "cli_synthetic1"
APP_SECRET = "synthetic-app-secret-for-offline-tests"
MASTER_KEY = bytes(range(32))


def _wrapped_key(encoding: str, key: bytes = MASTER_KEY) -> bytes:
    encoded = base64.b64encode(key)
    if encoding == "base64":
        return b"go-keyring-base64:" + base64.b64encode(encoded) + b"\n"
    if encoding == "hex":
        return b"go-keyring-encoded:" + encoded.hex().encode("ascii") + b"\n"
    return encoded + b"\n"


@contextmanager
def _synthetic_store(*, encoding: str = "base64", tamper: bool = False):
    """Create an isolated fake CLI home and mock every Keychain subprocess."""
    with TemporaryDirectory(prefix="shilun-feishu-test-") as directory:
        home = Path(directory)
        config_dir = home / ".lark-cli"
        store_dir = home / "Library" / "Application Support" / "lark-cli"
        config_dir.mkdir()
        store_dir.mkdir(parents=True)
        config = {
            "currentApp": APP_ID,
            "apps": [{
                "appId": APP_ID,
                "brand": "feishu",
                "appSecret": {"source": "keychain", "id": f"appsecret:{APP_ID}"},
            }],
        }
        (config_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        nonce = bytes(range(12))
        cipher = AES.new(MASTER_KEY, AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(APP_SECRET.encode("utf-8"))
        blob = nonce + ciphertext + tag
        if tamper:
            blob = blob[:-1] + bytes([blob[-1] ^ 1])
        (store_dir / f"appsecret_{APP_ID}.enc").write_bytes(blob)
        result = subprocess.CompletedProcess([], 0, stdout=_wrapped_key(encoding), stderr=b"")
        with (
            patch.object(feishu_sdk, "_read_env_file", return_value={}),
            patch.dict(feishu_sdk.os.environ, {}, clear=True),
            patch.object(feishu_sdk.sys, "platform", "darwin"),
            patch.object(feishu_sdk.Path, "home", return_value=home),
            patch.object(feishu_sdk.subprocess, "run", return_value=result) as keychain,
        ):
            yield keychain


class MasterKeyTests(unittest.TestCase):
    def test_decodes_current_and_legacy_keyring_envelopes(self):
        for encoding in ("base64", "hex", "unwrapped"):
            with self.subTest(encoding=encoding):
                self.assertEqual(MASTER_KEY, feishu_sdk.decode_master_key(_wrapped_key(encoding)))

    def test_rejects_wrong_master_key_lengths(self):
        for size in (0, 16, 31, 33):
            for encoding in ("base64", "hex"):
                with self.subTest(size=size, encoding=encoding):
                    with self.assertRaises(ValueError):
                        feishu_sdk.decode_master_key(_wrapped_key(encoding, bytes(size)))


class CredentialTests(unittest.TestCase):
    def test_environment_credentials_do_not_access_cli_store_or_keychain(self):
        env = {"SHILUN_FEISHU_APP_ID": APP_ID, "SHILUN_FEISHU_APP_SECRET": APP_SECRET}
        with (
            patch.object(feishu_sdk, "_read_env_file", return_value={}),
            patch.dict(feishu_sdk.os.environ, env, clear=True),
            patch.object(feishu_sdk.Path, "read_text", side_effect=AssertionError("CLI file read")),
            patch.object(feishu_sdk.Path, "read_bytes", side_effect=AssertionError("CLI store read")),
            patch.object(feishu_sdk.subprocess, "run", side_effect=AssertionError("Keychain access")) as keychain,
        ):
            credentials = feishu_sdk.load_credentials()
        self.assertEqual(APP_ID, credentials.app_id)
        self.assertEqual(APP_SECRET, credentials.app_secret)
        self.assertEqual("environment", credentials.source)
        self.assertNotIn(APP_SECRET, repr(credentials))
        keychain.assert_not_called()

    @unittest.skipIf(AES is None, "Feishu optional dependency pycryptodome is not installed")
    def test_decrypts_synthetic_store_with_both_keyring_envelopes(self):
        for encoding in ("base64", "hex"):
            with self.subTest(encoding=encoding):
                with _synthetic_store(encoding=encoding) as keychain:
                    credentials = feishu_sdk.load_credentials()
                self.assertEqual(APP_ID, credentials.app_id)
                self.assertEqual(APP_SECRET, credentials.app_secret)
                self.assertEqual("lark-cli macOS keychain", credentials.source)
                self.assertNotIn(APP_SECRET, repr(credentials))
                argv = keychain.call_args.args[0]
                self.assertEqual("/usr/bin/security", argv[0])
                self.assertIn("master.key", argv)
                self.assertTrue(keychain.call_args.kwargs["capture_output"])

    @unittest.skipIf(AES is None, "Feishu optional dependency pycryptodome is not installed")
    def test_tampered_store_is_rejected_with_safe_error(self):
        with _synthetic_store(tamper=True):
            with self.assertRaises(feishu_sdk.SetupError) as raised:
                feishu_sdk.load_credentials()
        self.assertNotIn(APP_SECRET, str(raised.exception))
        self.assertNotIn(base64.b64encode(MASTER_KEY).decode(), str(raised.exception))


class ConnectionDiagnosticTests(unittest.TestCase):
    def test_probe_uses_stdin_and_never_places_secret_in_argv(self):
        credentials = feishu_sdk.Credentials(APP_ID, APP_SECRET)
        result = subprocess.CompletedProcess([], 0, stdout=APP_SECRET, stderr=APP_SECRET)
        with patch.object(feishu_sdk.subprocess, "run", return_value=result) as child:
            status = feishu_sdk.check_connection(credentials, timeout=4)
        argv = child.call_args.args[0]
        options = child.call_args.kwargs
        self.assertNotIn(APP_SECRET, " ".join(argv))
        self.assertEqual(APP_SECRET, json.loads(options["input"])["app_secret"])
        self.assertNotIn("env", options)
        self.assertEqual(4, options["timeout"])
        self.assertTrue(options["capture_output"])
        self.assertEqual({"long_connection": "connected", "probe_closed": True}, status)
        self.assertNotIn(APP_SECRET, json.dumps(status))

    def test_timeout_is_reported_as_safe_setup_error(self):
        credentials = feishu_sdk.Credentials(APP_ID, APP_SECRET)
        failure = subprocess.TimeoutExpired("synthetic-probe", 1, output=APP_SECRET, stderr=APP_SECRET)
        with patch.object(feishu_sdk.subprocess, "run", side_effect=failure):
            with self.assertRaises(feishu_sdk.SetupError) as raised:
                feishu_sdk.check_connection(credentials, timeout=1)
        self.assertIn("超时", str(raised.exception))
        self.assertNotIn(APP_SECRET, str(raised.exception))

    def test_main_does_not_forward_sensitive_child_output_on_failure(self):
        credentials = feishu_sdk.Credentials(APP_ID, APP_SECRET)
        failure = subprocess.CompletedProcess([], 1, stdout=APP_SECRET, stderr=APP_SECRET)
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch.object(feishu_sdk, "sdk"),
            patch.object(feishu_sdk, "load_credentials", return_value=credentials),
            patch.object(feishu_sdk.importlib.metadata, "version", return_value="1.7.5"),
            patch.object(feishu_sdk, "check_bot", return_value={"authenticated": True}),
            patch.object(feishu_sdk.subprocess, "run", return_value=failure),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            code = feishu_sdk.main(["--connect-check"])
        self.assertEqual(1, code)
        self.assertFalse(json.loads(errors.getvalue())["ok"])
        self.assertNotIn(APP_SECRET, output.getvalue() + errors.getvalue())


class AutoReplyConfigurationTests(unittest.TestCase):
    def test_dotenv_model_settings_reach_worker_and_existing_environment_takes_precedence(self):
        settings = {"SHILUN_CODEX_BIN": "/synthetic/codex", "SHILUN_CODEX_TIMEOUT_SECONDS": "60",
                    "SHILUN_CODEX_MODEL": "gpt-dotenv", "SHILUN_CODEX_REASONING_EFFORT": "low",
                    "SHILUN_CODEX_SERVICE_TIER": "priority", "SHILUN_CODEX_PROXY": "http://127.0.0.1:7897"}
        credentials = feishu_sdk.Credentials(APP_ID, APP_SECRET)
        for existing in ({}, {"SHILUN_CODEX_MODEL": "gpt-environment"}):
            with self.subTest(existing=existing):
                observed = {}
                bridge = Mock(users=["synthetic-owner"], bot_open_id="synthetic-bot")

                def create_bridge(*args, **kwargs):
                    observed.update({name: feishu_sdk.os.environ.get(name) for name in settings})
                    return bridge

                with (
                    patch.dict(feishu_sdk.os.environ, existing, clear=True),
                    patch.object(feishu_sdk, "sdk"),
                    patch.object(feishu_sdk, "load_credentials", return_value=credentials),
                    patch.object(feishu_sdk, "_read_env_file", return_value=settings),
                    patch.object(feishu_sdk.importlib.metadata, "version", return_value="synthetic"),
                    patch.object(feishu_sdk, "check_bot", return_value={"bot_open_id": "synthetic-bot"}),
                    patch.object(feishu_sdk, "build_client"),
                    patch.object(feishu_sdk, "build_connection"),
                    patch.object(feishu_sdk, "enable_connection_status"),
                    patch.object(feishu_sdk, "emit_status"),
                    patch.object(feishu_sdk.signal, "signal"),
                    patch.object(feishu_sdk.threading, "Thread"),
                    patch("shilun.feishu_codex_bridge.CodexBridge", side_effect=create_bridge),
                ):
                    self.assertEqual(0, feishu_sdk.main(["--auto-reply"]))
                self.assertEqual({**settings, **existing}, observed)
                bridge.start.assert_called_once_with()
                bridge.close.assert_called_once_with()


@contextmanager
def _isolated_status_logger():
    try:
        from lark_oapi.core.log import logger
    except ImportError as exc:
        raise unittest.SkipTest("Feishu optional dependency lark-oapi is not installed") from exc
    original_level = logger.level
    with (
        patch.object(logger, "handlers", []),
        patch.object(logger, "propagate", False),
        patch.object(logger, "disabled", True),
        patch.object(logger, "level", logging.WARNING),
    ):
        try:
            yield logger
        finally:
            # setLevel also clears logging's level cache before patch restores
            # the original shared logger attributes.
            logger.setLevel(original_level)


class ConnectionStatusTests(unittest.TestCase):
    def test_connection_lifecycle_emits_only_safe_status(self):
        credentials = feishu_sdk.Credentials(APP_ID, APP_SECRET)
        signed_url = "wss://feishu.invalid/events?ticket=synthetic-ticket&token=synthetic-token"
        output, errors = io.StringIO(), io.StringIO()
        with (
            _isolated_status_logger() as logger,
            patch.object(feishu_sdk.os, "getpid", return_value=2468),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            feishu_sdk.enable_connection_status(credentials)
            logger.info("connected to %s [conn_id=synthetic-connection]", signed_url)
            logger.info("disconnected to %s [conn_id=synthetic-connection]", signed_url)
        self.assertEqual([
            {"status": "connected", "app_id": APP_ID, "pid": 2468},
            {"status": "disconnected", "app_id": APP_ID, "pid": 2468},
        ], [json.loads(line) for line in output.getvalue().splitlines()])
        displayed = output.getvalue() + errors.getvalue()
        for sensitive in (signed_url, "synthetic-ticket", "synthetic-token", "synthetic-connection", APP_SECRET):
            self.assertNotIn(sensitive, displayed)

    def test_ordinary_logs_do_not_signal_ready_and_errors_are_sanitized(self):
        credentials = feishu_sdk.Credentials(APP_ID, APP_SECRET)
        signed_url = "wss://feishu.invalid/events?ticket=synthetic-ticket&token=synthetic-token"
        output, errors = io.StringIO(), io.StringIO()
        with (
            _isolated_status_logger() as logger,
            patch.object(feishu_sdk.os, "getpid", return_value=2468),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            feishu_sdk.enable_connection_status(credentials)
            logger.info("trying to reconnect for the 1st time")
            logger.info("waiting for connected to %s", signed_url)
            logger.warning("network waiting: %s", signed_url)
            self.assertEqual("", output.getvalue())
            logger.error("connect failed, err: %s %s", signed_url, APP_SECRET)
        self.assertEqual(
            {"status": "connection_error", "app_id": APP_ID, "pid": 2468},
            json.loads(output.getvalue()),
        )
        displayed = output.getvalue() + errors.getvalue()
        for sensitive in (signed_url, "synthetic-ticket", "synthetic-token", APP_SECRET):
            self.assertNotIn(sensitive, displayed)


class ReplyWorkerSupervisorTests(unittest.TestCase):
    def bridge(self, *, alive=True):
        stop = Mock()
        stop.wait.side_effect = [False, True]
        thread = Mock()
        thread.is_alive.return_value = alive
        return SimpleNamespace(stop_event=stop, thread=thread)

    def test_lost_storage_lease_cancels_even_while_model_worker_is_alive(self):
        bridge = self.bridge()
        with patch.object(feishu_sdk.os, "kill") as kill, patch.object(feishu_sdk.os, "getpid", return_value=2468):
            feishu_sdk._supervise_reply_worker(bridge, SimpleNamespace(healthy=False))
        kill.assert_called_once_with(2468, feishu_sdk.signal.SIGTERM)

    def test_healthy_storage_does_not_interrupt_active_worker(self):
        with patch.object(feishu_sdk.os, "kill") as kill:
            feishu_sdk._supervise_reply_worker(self.bridge(), SimpleNamespace(healthy=True))
        kill.assert_not_called()

    def test_exited_worker_still_triggers_restart_without_remote_store(self):
        with patch.object(feishu_sdk.os, "kill") as kill, patch.object(feishu_sdk.os, "getpid", return_value=2468):
            feishu_sdk._supervise_reply_worker(self.bridge(alive=False))
        kill.assert_called_once_with(2468, feishu_sdk.signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
