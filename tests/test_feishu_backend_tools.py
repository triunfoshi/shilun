"""Owner identity remains mandatory across generic discovery and query tools."""

from copy import deepcopy
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from shilun.feishu_backend_tools import BackendQueryTools


IDENTITY = {"app_id": "cli_test", "message_id": "om_test", "chat_id": "oc_chat", "sender_id": "ou_owner"}


class QueryToolTests(unittest.TestCase):
    def tools(self, identity=None):
        gateway = Mock()
        audit = Mock()
        tools = BackendQueryTools(identity=deepcopy(IDENTITY if identity is None else identity),
            gateway=gateway, allowed_fn=lambda _: {"ou_owner"}, audit_fn=audit)
        return tools, gateway, audit

    def test_other_owner_or_missing_identity_cannot_discover_or_query(self):
        for identity in ({}, {**IDENTITY, "sender_id": "ou_other"}, {**IDENTITY, "message_id": "om_bad\n"}):
            tools, gateway, audit = self.tools(identity)
            for result in (tools.list_modules(), tools.search(), tools.query("get_board_effects")):
                self.assertEqual("owner_context_required", result["error"]["code"])
            gateway.query.assert_not_called()
            audit.assert_not_called()

    def test_discovery_returns_catalog_without_exposing_identity(self):
        tools, gateway, audit = self.tools()
        with patch("shilun.feishu_backend_catalog.list_modules", return_value={"modules": [{"id": "holdings"}]}):
            self.assertEqual({"modules": [{"id": "holdings"}]}, tools.list_modules())
        self.assertEqual({"tool": "list_backend_modules", "outcome": "ok"}, audit.call_args.args[0])
        gateway.query.assert_not_called()

    def test_query_preserves_structured_parameters_without_generic_commands(self):
        tools, gateway, _ = self.tools()
        gateway.query.return_value = {"status": "ok", "data": []}
        result = tools.query("get_holdings", parameters={"status": "open"}, select=["holdings"], max_items=10)
        gateway.query.assert_called_once_with("get_holdings", parameters={"status": "open"}, body=None,
                                              select=["holdings"], offset=0, max_items=10)
        self.assertEqual("ok", result["status"])

    def test_bad_discovery_is_a_safe_result(self):
        tools, _, _ = self.tools()
        with patch("shilun.feishu_backend_catalog.search_queries", side_effect=ValueError("private detail")):
            result = tools.search(module="unknown")
        self.assertEqual("invalid_discovery", result["error"]["code"])
        self.assertNotIn("private detail", str(result))

    def test_query_resources_close(self):
        tools, gateway, _ = self.tools()
        tools.close()
        gateway.close.assert_called_once()

    def test_missing_remote_uri_never_falls_back_to_local_database(self):
        tools = BackendQueryTools(identity=IDENTITY, allowed_fn=lambda _: {"ou_owner"})
        with (patch("shilun.feishu_backend_tools.load_config", return_value=SimpleNamespace(mongo_uri=None)),
              patch("pymongo.MongoClient") as client):
            tools._audit({"tool": "list_backend_modules", "outcome": "ok"})
        client.assert_not_called()


class PackageImportTests(unittest.TestCase):
    def test_lightweight_import_does_not_require_pipeline_stack(self):
        result = subprocess.run([sys.executable, "-c",
            "import sys, shilun; assert 'shilun.pipeline' not in sys.modules; assert shilun.__all__ == ['PipelineConfig', 'ShilunPipeline']"],
            capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_existing_public_exports_keep_identity(self):
        from shilun import PipelineConfig, ShilunPipeline
        from shilun.pipeline import PipelineConfig as OriginalConfig, ShilunPipeline as OriginalPipeline
        self.assertIs(PipelineConfig, OriginalConfig)
        self.assertIs(ShilunPipeline, OriginalPipeline)


if __name__ == "__main__":
    unittest.main()
