import contextlib
import io
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import unquote, urlsplit

from scripts import configure_ecs_mongo as migration


class ConfigurationTests(unittest.TestCase):
    def test_password_round_trip_and_no_extra_uri_fields(self):
        password = "test@:/?#% &汉字'\""
        address = urlsplit(migration.cloud_uri(password))
        self.assertEqual(unquote(address.password), password)
        self.assertEqual(address.hostname, "127.0.0.1")
        self.assertEqual(address.port, 27018)
        self.assertEqual(address.fragment, "")
        self.assertIn("authSource=shilun", address.query)

    def test_reject_empty_or_control_character_password(self):
        for password in ("", "foo\nbar", "abc\x00"):
            with self.subTest(password=repr(password)), self.assertRaises(migration.CutoverError):
                migration.cloud_uri(password)

    def test_preserve_unrelated_env_bytes_and_remove_duplicates(self):
        original = b"# comment\r\nTOKEN='test-value'\r\nSHILUN_MONGO_URI=old\n SHILUN_MONGO_URI =duplicate\nSHILUN_MONGO_DB=old\nOTHER=keep"
        updated = migration.replace_env(original, "mongodb://example")
        self.assertTrue(updated.startswith(b"# comment\r\nTOKEN='test-value'\r\nOTHER=keep\n"))
        self.assertEqual(updated.count(b"SHILUN_MONGO_URI="), 1)
        self.assertIn(b"SHILUN_MONGO_DB=shilun\n", updated)

    def test_backup_permissions_and_atomic_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "runtime").mkdir()
            env = root / ".env"
            env.write_bytes(b"old")
            backup = migration.private_backup(root, b"old")
            migration.write_env(env, b"old", b"new")
            self.assertEqual(backup.read_bytes(), b"old")
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(backup.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(env.stat().st_mode & 0o777, 0o600)
            self.assertEqual(env.read_bytes(), b"new")

    def test_concurrent_change_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_bytes(b"someone else's edit")
            with self.assertRaises(migration.CutoverError):
                migration.write_env(env, b"old", b"new")
            self.assertEqual(env.read_bytes(), b"someone else's edit")

    def test_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "target"
            target.write_bytes(b"old")
            env = root / ".env"
            env.symlink_to(target)
            with self.assertRaises(migration.CutoverError):
                migration.write_env(env, b"old", b"new")
            self.assertEqual(target.read_bytes(), b"old")


class ManifestTests(unittest.TestCase):
    def database(self):
        database = MagicMock()
        database.list_collections.return_value = [{"name": "prices", "type": "collection", "options": {}}]
        database.list_collection_names.return_value = ["prices"]
        database.command.return_value = {"collections": {"prices": "hash"}}
        database.__getitem__.return_value.count_documents.return_value = 42
        database.__getitem__.return_value.list_indexes.return_value = [
            {"name": "_id_", "key": {"_id": 1}, "v": 2},
            {"name": "compound", "key": {"ticker": 1, "date": -1}, "unique": True},
        ]
        return database

    def test_manifest_includes_content_options_count_and_index_order(self):
        result = migration.manifest(self.database())["prices"]
        self.assertEqual(result["documents"], 42)
        self.assertEqual(result["hash"], "hash")
        self.assertEqual(result["indexes"]["compound"]["key"], [("ticker", 1), ("date", -1)])
        self.assertNotIn("v", result["indexes"]["_id_"])

    def test_missing_hash_is_not_success(self):
        database = self.database()
        database.command.return_value = {"collections": {}}
        with self.assertRaises(migration.CutoverError):
            migration.manifest(database)

    def test_detect_write_during_inspection(self):
        database = self.database()
        database.command.side_effect = [{"collections": {"prices": "old"}}, {"collections": {"prices": "new"}}]
        with self.assertRaises(migration.CutoverError):
            migration.manifest(database)

    def test_equal_totals_do_not_mask_changed_content(self):
        with self.assertRaises(migration.CutoverError):
            migration.compare({"prices": {"documents": 42, "hash": "old"}}, {"prices": {"documents": 42, "hash": "new"}})

    def test_missing_collection_is_detected(self):
        with self.assertRaises(migration.CutoverError):
            migration.compare({"a": 1}, {"a": 1, "b": 0})


class CutoverLifecycleTests(unittest.TestCase):
    def run_cutover(self, *, manifests=None, write_error=None, readiness_error=None):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(folder)
        (root / "runtime").mkdir()
        env = root / ".env"
        env.write_bytes(b"old")
        output = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(migration, "require_paused_writers"))
        self.enterContext(patch.object(migration, "require_free_port"))
        command = self.enterContext(patch.object(migration, "launchctl", return_value=subprocess.CompletedProcess([], 0)))
        self.enterContext(patch.object(migration, "manifest", side_effect=manifests or [{"a": 1}] * 3))
        writer = self.enterContext(patch.object(migration, "write_env", side_effect=write_error))
        ready = self.enterContext(patch.object(migration, "wait_ready", side_effect=readiness_error))
        self.output, self.command, self.writer, self.ready = output, command, writer, ready
        migration.cutover(env, b"old", "mongodb://test", object(), object(), root / "backend.plist", 8000)

    def test_successful_cutover_restarts_only_backend(self):
        self.run_cutover()
        self.assertEqual([call.args[0] for call in self.command.call_args_list], ["bootout", "bootstrap"])
        self.writer.assert_called_once()
        self.ready.assert_called_once_with(8000)

    def test_mismatch_preserves_config_and_resumes_original_backend(self):
        with self.assertRaises(migration.CutoverError):
            self.run_cutover(manifests=[{"a": 1}, {"a": 2}])
        self.writer.assert_not_called()
        self.assertEqual([call.args[0] for call in self.command.call_args_list], ["bootout", "bootstrap"])
        self.assertIn("未切换数据库", self.output.getvalue())

    def test_new_writes_after_scan_prevent_cutover(self):
        with self.assertRaises(migration.CutoverError):
            self.run_cutover(manifests=[{"a": 1}, {"a": 1}, {"a": 2}])
        self.writer.assert_not_called()

    def test_no_silent_rollback_after_readiness_failure(self):
        with self.assertRaises(migration.CutoverError):
            self.run_cutover(readiness_error=migration.CutoverError("failed"))
        self.writer.assert_called_once()
        self.assertEqual([call.args[0] for call in self.command.call_args_list], ["bootout", "bootstrap"])
        self.assertNotIn("未切换数据库", self.output.getvalue())

    def test_write_failure_stays_stopped_for_inspection(self):
        with self.assertRaises(OSError):
            self.run_cutover(write_error=OSError("test"))
        self.assertEqual([call.args[0] for call in self.command.call_args_list], ["bootout"])
        self.ready.assert_not_called()

    def test_non_interactive_execution_never_prompts_or_edits(self):
        with patch.object(sys := migration.sys, "argv", ["configure_ecs_mongo.py"]), patch.object(sys.stdin, "isatty", return_value=False), patch.object(migration.getpass, "getpass") as prompt, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(migration.main(), 1)
            prompt.assert_not_called()


class TunnelTests(unittest.TestCase):
    def test_tunnel_is_noninteractive_loopback_only_and_reconnecting(self):
        data = plistlib.loads((migration.ROOT / "deploy/cn.shilun.mongo-tunnel.plist").read_bytes())
        args = data["ProgramArguments"]
        self.assertIn("127.0.0.1:27018:127.0.0.1:27017", args)
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("ExitOnForwardFailure=yes", args)
        self.assertIn("BatchMode=yes", args)
        self.assertTrue(data["KeepAlive"])
        self.assertTrue(data["RunAtLoad"])


if __name__ == "__main__":
    unittest.main()
