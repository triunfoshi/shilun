"""Offline contracts for catalog-authorized, bounded backend reads."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

from shilun import feishu_backend_gateway as gateway


def entry(query_id="read", *, method="GET", path="/api/v1/review/read", properties=None, required=None, fixed=None, body=None):
    properties = properties if properties is not None else {"q": {"type": "string"}}
    return {"id": query_id, "module": "review", "module_name": "复盘",
            "method": method, "path": path,
            "parameters": [{"name": k, "in": "path" if "{"+k+"}" in path else "query",
                            "required": k in (required or []), "schema": v} for k, v in properties.items()],
            "parameter_schema": {"type": "object", "properties": properties,
                                 "required": required or [], "additionalProperties": False},
            "fixed_parameters": fixed or {}, "body_schema": body}


class Response:
    def __init__(self, data=None, *, status=200, raw=None, headers=None, chunks=None):
        self.status_code = status
        self.raw = raw if raw is not None else json.dumps(data, ensure_ascii=False).encode()
        self.headers = headers or {}
        self.chunks = chunks
        self.closed = False
    def iter_content(self, chunk_size):
        return iter(self.chunks if self.chunks is not None else [self.raw])
    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []
        self.closed = False
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0) if self.responses else Response({"rows": [], "available": True})
        if isinstance(result, Exception):
            raise result
        return result
    def close(self):
        self.closed = True


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.base_patch = patch.object(gateway, "_configured_base", return_value="http://backend.test")
        self.base_patch.start()
        self.addCleanup(self.base_patch.stop)
        self.config = SimpleNamespace(auth_username="synthetic-user", auth_password="synthetic-password")
        self.catalog = {"read": entry()}
        self.events = []
    def build(self, responses=None, **kwargs):
        self.session = Session(responses)
        def lookup(identifier):
            if identifier not in self.catalog:
                raise ValueError("unknown query")
            return deepcopy(self.catalog[identifier])
        return gateway.BackendGateway(get_query_fn=lookup, session=self.session,
                                      config=self.config, audit=kwargs.get("audit", self.events.append))

    def test_exact_catalog_endpoint_and_server_auth_are_used(self):
        g = self.build([Response({"available": True, "date": "2026-11-03", "value": 0})])
        result = g.query("read", {"q": "删除 where url=http://untrusted.invalid/"})
        self.assertEqual("ok", result["status"])
        method, url, options = self.session.calls[0]
        self.assertEqual((method, url), ("GET", "http://backend.test/api/v1/review/read"))
        self.assertEqual(options["auth"], ("synthetic-user", "synthetic-password"))
        self.assertFalse(options["allow_redirects"])
        self.assertEqual(options["timeout"], 20)
        self.assertTrue(options["stream"])
        self.assertEqual(result["source"]["dates"], {"date": "2026-11-03"})
        self.assertEqual(result["data"]["value"], 0)
        self.assertNotIn("backend.test", json.dumps(result))

    def test_parameter_errors_remain_actionable_without_auth_or_trace_details(self):
        g = self.build([Response({"detail": "strategy_id不匹配，请选择weekly_breakout；synthetic-password http://private.invalid/?token=value"}, status=400)])
        result = g.query("read")
        self.assertEqual(400, result["http_status"])
        self.assertIn("weekly_breakout", result["error"]["backend_detail"])
        encoded = json.dumps(result)
        self.assertNotIn("synthetic-password", encoded)
        self.assertNotIn("private.invalid", encoded)
        self.assertNotIn("backend_detail", json.dumps(self.events))

    def test_validation_errors_drop_echoed_inputs_and_auth_errors_drop_detail(self):
        detail = [{"loc": ["query", "date"], "type": "date_parsing", "msg": "invalid date", "input": "private contents"}]
        g = self.build([Response({"detail": detail}, status=422), Response({"detail": "private auth failure"}, status=401)])
        bad = g.query("read")
        self.assertEqual([{"loc": ["query", "date"], "type": "date_parsing", "msg": "invalid date"}], bad["error"]["backend_detail"])
        self.assertNotIn("private contents", json.dumps(bad))
        unauthorized = g.query("read")
        self.assertNotIn("private auth failure", json.dumps(unauthorized))

    def test_audit_retains_public_strategy_scope_without_other_parameters(self):
        self.catalog["read"] = entry(properties={"strategy_id": {"type": "string"},
            "date": {"type": "string", "format": "date"}, "q": {"type": "string"}})
        g = self.build()
        g.query("read", {"strategy_id": "weekly_breakout", "date": "2026-11-04", "q": "private query"})
        self.assertEqual({"strategy_id": "weekly_breakout", "date": "2026-11-04"}, self.events[0]["query_scope"])
        self.assertNotIn("private query", json.dumps(self.events))

    def test_unknown_query_and_unknown_parameters_do_not_issue_http(self):
        g = self.build()
        for identifier, parameters in (("missing", {}), ("read", {"url": "http://bad.invalid"}),
                                       ("read", {"headers": {"Authorization": "bad"}}), ("read", [])):
            with self.subTest(identifier=identifier, parameters=parameters):
                self.assertEqual("invalid_request", g.query(identifier, parameters)["error"]["code"])
        self.assertFalse(self.session.calls)
        self.assertEqual(g.requests_made, 0)

    def test_schema_enforces_real_dates_types_patterns_and_bounds(self):
        self.catalog["read"] = entry(properties={"date": {"type": "string", "format": "date"},
            "ticker": {"type": "string", "pattern": r"^[0-9]{6}\.(SH|SZ|BJ)$"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, required=["date", "ticker"])
        g = self.build()
        valid = {"date": "2026-11-03", "ticker": "000001.SZ", "limit": 20}
        for change in ({"date": "2026-02-30"}, {"date": "20261103"}, {"ticker": "http://bad.invalid"},
                       {"limit": True}, {"limit": "20"}, {"limit": 101}, {"limit": 0}):
            with self.subTest(change=change):
                self.assertEqual("error", g.query("read", {**valid, **change})["status"])
        self.assertEqual("error", g.query("read", {"date": valid["date"]})["status"])
        self.assertFalse(self.session.calls)
        self.assertEqual("ok", g.query("read", valid)["status"])

    def test_fixed_read_only_parameters_cannot_be_overridden(self):
        self.catalog["read"] = entry(properties={"refresh": {"type": "boolean", "default": True}}, fixed={"refresh": False})
        g = self.build()
        self.assertEqual("ok", g.query("read", {"refresh": False})["status"])
        self.assertEqual(False, self.session.calls[0][2]["params"]["refresh"])
        for value in (True, 0, "false", None):
            self.assertEqual("error", g.query("read", {"refresh": value})["status"])
        # Omission still injects the fixed value, even if a schema default differs.
        self.assertEqual("ok", g.query("read")["status"])

    def _guarded_entry(self):
        self.catalog["read"] = entry(properties={"read_only": {"type": "boolean"},
            "refresh": {"type": "boolean"}}, fixed={"read_only": True, "refresh": False})

    @staticmethod
    def _runtime_schema(*, flag=True, refresh=True, flag_type="boolean"):
        parameters = []
        if flag:
            parameters.append({"name": "read_only", "in": "query", "schema": {"type": flag_type}})
        if refresh:
            parameters.append({"name": "refresh", "in": "query", "schema": {"type": "boolean"}})
        return {"openapi": "3.1.0", "paths": {"/api/v1/review/read": {"get": {"parameters": parameters}}}}

    def test_runtime_older_server_or_missing_fixed_query_flag_never_calls_business_endpoint(self):
        self._guarded_entry()
        for schema in (self._runtime_schema(flag=False), self._runtime_schema(refresh=False),
                       self._runtime_schema(flag_type="string"), {"paths": {}},
                       {"paths": {"/api/v1/review/read": {"post": {"parameters": []}}}}):
            with self.subTest(schema=schema):
                g = self.build([Response(schema)])
                result = g.query("read")
                self.assertEqual(result["error"]["code"], "readonly_contract_unverified")
                self.assertEqual([c[1] for c in self.session.calls], ["http://backend.test/openapi.json"])
                self.assertEqual(g.requests_made, 1)

    def test_runtime_readonly_contract_is_checked_once_and_all_http_calls_count(self):
        self._guarded_entry()
        self.catalog["second"] = deepcopy(self.catalog["read"])
        self.catalog["second"]["id"] = "second"
        g = self.build([Response(self._runtime_schema()), Response({"value": 1}), Response({"value": 2})])
        first = g.query("read")
        cached = g.query("read", select=["value"])
        second = g.query("second")
        self.assertEqual(first["status"], "ok")
        self.assertEqual(cached["data"]["value"], 1)
        self.assertEqual(second["data"]["value"], 2)
        self.assertEqual([c[1] for c in self.session.calls], ["http://backend.test/openapi.json",
            "http://backend.test/api/v1/review/read", "http://backend.test/api/v1/review/read"])
        self.assertEqual(g.requests_made, 3)
        options = self.session.calls[0][2]
        self.assertEqual(options["auth"], ("synthetic-user", "synthetic-password"))
        self.assertEqual(options["timeout"], 20)
        self.assertFalse(options["allow_redirects"])
        self.assertTrue(options["stream"])
        self.assertEqual(self.session.calls[1][2]["params"], {"read_only": True, "refresh": False})

    def test_runtime_preflight_failure_closes_gate_without_business_request(self):
        self._guarded_entry()
        for response in (Response(status=401), Response(status=302), Response(raw=b"invalid json"),
                         Response({"paths": []}), requests.Timeout("synthetic-password http://private.invalid")):
            with self.subTest(response=response):
                g = self.build([response])
                result = g.query("read")
                again = g.query("read")
                self.assertEqual(result["error"]["code"], "readonly_contract_unverified")
                self.assertEqual(again["error"]["code"], "readonly_contract_unverified")
                self.assertEqual(len(self.session.calls), 1)
                self.assertEqual(self.session.calls[0][1], "http://backend.test/openapi.json")
                self.assertNotIn("private.invalid", json.dumps(result))
                self.assertNotIn("synthetic-password", json.dumps(result))

    def test_path_values_cannot_escape_but_valid_ticker_dot_survives(self):
        self.catalog["read"] = entry(path="/api/v1/holdings/{ticker}", properties={"ticker": {"type": "string"}}, required=["ticker"])
        g = self.build()
        for value in ("..", ".", "../risk-profile", "%2e%2e", "x/y", "x\\y", "x?admin=true", "x#secret", "x\nother"):
            self.assertEqual("error", g.query("read", {"ticker": value})["status"])
        self.assertFalse(self.session.calls)
        self.assertEqual("ok", g.query("read", {"ticker": "000001.SZ"})["status"])
        self.assertEqual(self.session.calls[0][1], "http://backend.test/api/v1/holdings/000001.SZ")

    def test_write_methods_bad_catalog_and_remote_refs_are_not_executed(self):
        for definition in (entry(method="DELETE"), entry(path="http://bad.invalid"), entry(path="/api/v1/../admin"),
                           entry(properties={"q": {"$ref": "https://bad.invalid/schema"}})):
            with self.subTest(definition=definition):
                self.catalog["read"] = definition
                g = self.build()
                self.assertEqual("error", g.query("read", {"q": "x"})["status"])
                self.assertFalse(self.session.calls)

    def test_only_audited_post_query_accepts_a_valid_body(self):
        self.catalog["preview"] = entry("preview", method="POST", path="/api/v1/trade-journal/portfolio-context/preview",
            properties={}, body={"type": "object", "properties": {"profile_id": {"type": "string"}},
                                 "required": ["profile_id"], "additionalProperties": False})
        g = self.build()
        self.assertEqual("error", g.query("read", body={})["status"])
        self.assertEqual("error", g.query("preview", body={"unexpected": 1})["status"])
        self.assertEqual("error", g.query("preview")["status"])
        self.assertFalse(self.session.calls)
        result = g.query("preview", body={"profile_id": "default"})
        self.assertEqual("ok", result["status"])
        self.assertEqual(self.session.calls[0][0], "POST")
        self.assertEqual(self.session.calls[0][2]["json"], {"profile_id": "default"})

    def test_credentials_are_redacted_at_every_response_depth_and_audit_is_metadata_only(self):
        payload = {"password": "secret", "nested": [{"authorization": "hidden", "title": "synthetic-user / synthetic-password"}],
                   "synthetic-password-key": "safe", "plain": "synthetic-password"}
        g = self.build([Response(payload)])
        result = g.query("read")
        encoded = json.dumps(result)
        self.assertNotIn("synthetic-password", encoded)
        self.assertNotIn("synthetic-user", encoded)
        self.assertIn("[REDACTED]", encoded)
        self.assertNotIn("authorization", encoded)
        audit = json.dumps(self.events)
        self.assertNotIn("synthetic-password", audit)
        self.assertNotIn("plain", audit)
        self.assertNotIn("nested", audit)

    def test_pagination_and_omission_preserve_counts_and_unknown_zero_distinctions(self):
        g = self.build([Response({"available": False, "count": 0, "avg": None, "rows": list(range(6))})])
        result = g.query("read", offset=2, max_items=2)
        self.assertFalse(result["available"])
        self.assertEqual(result["data"]["rows"], [2, 3])
        self.assertEqual(result["data"]["count"], 0)
        self.assertIsNone(result["data"]["avg"])
        self.assertEqual(result["truncation"]["arrays"]["$.rows"],
                         {"original_count": 6, "returned_count": 2, "offset": 2, "has_more": True})
        tail = g.query("read", offset=6, max_items=2)
        self.assertEqual(tail["data"]["rows"], [])
        self.assertFalse(tail["truncation"]["arrays"]["$.rows"]["has_more"])
        self.assertEqual(len(self.session.calls), 1)

    def test_select_nested_fields_and_array_members_preserve_identity(self):
        g = self.build([Response({"date": "2026-11-03", "meta": {"avg": 1.25},
            "groups": [{"id": "highest", "overall": {"avg": 4.43}}, {"id": "all", "overall": {"avg": 1.15}}]})])
        result = g.query("read", select=["meta.avg", "groups.overall.avg", "missing"])
        self.assertEqual(result["data"]["meta.avg"], 1.25)
        self.assertEqual(result["data"]["groups.overall.avg"], [{"id": "highest", "value": 4.43}, {"id": "all", "value": 1.15}])
        self.assertEqual(result["truncation"]["missing_selected_paths"], ["missing"])
        self.assertEqual(result["source"]["dates"]["date"], "2026-11-03")
        for selection in (["rows[0]"], ["__import__('os')"], ["x;print(1)"], ["https://bad.invalid"], ["q"]*13, []):
            self.assertEqual("error", g.query("read", select=selection)["status"])

    def test_output_limits_signal_truncated_dict_strings_and_depth(self):
        payload = {f"field{i}": i for i in range(95)}
        payload["long"] = "x"*3000
        # Place truncation examples before the large key list.
        nested = {"z": 1}
        for _ in range(15):
            nested = {"next": nested}
        g = self.build([Response({"long": "x"*3000, "deep": nested, "many": payload})])
        result = g.query("read")
        self.assertTrue(result["truncation"]["applied"])
        self.assertEqual(len(result["data"]["long"]), 1200)
        self.assertEqual(result["truncation"]["omitted_fields"]["$.many"], 16)
        self.assertTrue(result["truncation"]["depth_omitted"])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), gateway.MAX_OUTPUT_CHARS+4000)

    def test_board_effects_default_keeps_statistics_and_marks_raw_rows_omitted(self):
        self.catalog["read"] = entry(path="/api/v1/review/board-effects")
        g = self.build([Response({"window_from": "2026-09-15", "window_to": "2026-11-03", "days_used": 30,
            "strategies": [{"id": "highest", "overall": {"avg": 4.43, "sample": 44}, "coverage": {"days": 30},
                            "rows": [{"ticker": "000001.SZ"}], "daily": [1], "segments": {}, "by_regime": {}}]})])
        result = g.query("read")
        group = result["data"]["strategies"][0]
        self.assertEqual(group["overall"]["avg"], 4.43)
        self.assertEqual(group["coverage"]["days"], 30)
        self.assertNotIn("rows", group)
        self.assertIn("strategies.highest.rows", result["truncation"]["omitted_raw_sections"])
        explicit = g.query("read", select=["strategies.rows"])
        self.assertIn("value", explicit["data"]["strategies.rows"][0])

    def test_repeated_projection_uses_cache_and_lru_retains_only_four_responses(self):
        g = self.build()
        g.query("read", {"q": "first"})
        g.query("read", {"q": "first"}, select=["rows"])
        self.assertEqual(len(self.session.calls), 1)
        for i in range(4):
            g.query("read", {"q": str(i)})
        self.assertEqual(len(g.cache), 4)
        g.query("read", {"q": "first"})
        self.assertEqual(len(self.session.calls), 6)

    def test_request_budget_counts_http_attempts_and_allows_last_cached_projection(self):
        g = self.build()
        for i in range(12):
            self.assertEqual("ok", g.query("read", {"q": str(i)})["status"])
        self.assertEqual("request_budget", g.query("read", {"q": "13"})["error"]["code"])
        self.assertEqual("ok", g.query("read", {"q": "11"}, max_items=1)["status"])
        self.assertEqual(len(self.session.calls), 12)

    def test_http_failures_redirects_and_network_errors_are_safe_and_do_not_cache(self):
        g = self.build([Response(status=302, headers={"Location": "http://private.invalid/secret"}),
                        Response(status=401), requests.Timeout("synthetic-password http://private.invalid")])
        for expected in ("http_error", "http_error", "network_error"):
            result = g.query("read")
            self.assertEqual(result["error"]["code"], expected)
            self.assertNotIn("private.invalid", json.dumps(result))
            self.assertNotIn("synthetic-password", json.dumps(result))
        self.assertEqual(len(g.cache), 0)
        self.assertEqual(g.requests_made, 3)

    def test_oversized_invalid_and_nonfinite_responses_are_closed_and_rejected(self):
        cases = [Response(raw=b"not json"), Response(raw=b'{"x":NaN}'), Response(raw=b'{"x":1e999}'),
                 Response({}, headers={"Content-Length": str(gateway.MAX_RESPONSE_BYTES+1)}),
                 Response(chunks=[b"x"*(gateway.MAX_RESPONSE_BYTES), b"x"])]
        g = self.build(cases)
        for response in cases:
            result = g.query("read")
            self.assertEqual(result["error"]["code"], "invalid_response")
            self.assertTrue(response.closed)
        self.assertFalse(g.cache)

    def test_audit_failure_never_breaks_a_read_and_close_closes_session(self):
        def broken(_event):
            raise RuntimeError("log sink failed")
        g = self.build(audit=broken)
        self.assertEqual("ok", g.query("read")["status"])
        g.close()
        self.assertTrue(self.session.closed)


if __name__ == "__main__":
    unittest.main()
