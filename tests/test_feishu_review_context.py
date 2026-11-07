"""Offline contract checks for the Feishu historical-review context adapter."""

from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from shilun import feishu_review_context as context


DAY = "2026-11-02"
PAYLOAD = {
    "status": "ok", "analysis_date": DAY, "previous_trade_date": "2026-10-30",
    "generated_at": "2026-11-02T18:00:00+08:00", "engine_version": "synthetic-review-v1",
    "action": {"label": "观察", "checks": ["核对量能"]},
    "market": {"indices": [{"ticker": "000001.SH", "pct_chg": 0.01}], "volume": {"ratio": 1.1}},
    "sentiment": {"label": "分歧"}, "expectations": {"observations": ["核对板块持续性"]},
    "tactics": {"examples": []},
    "freshness": {"daily_bars": DAY, "limit_up_pool": "2026-10-30", "alerts": ["涨停池非当日"]},
    "data_quality": {"status": "warn", "checks": [{"label": "涨停池滞后"}]},
}


class ReviewContextTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(auth_username="synthetic-user", auth_password="synthetic-password")
        self.config_patch = patch.object(context, "load_config", return_value=self.config)
        self.env_file_patch = patch.object(context, "_read_env_file", return_value={})
        self.env_patch = patch.dict(context.os.environ, {}, clear=True)
        self.config_patch.start()
        self.env_file_patch.start()
        self.env_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.addCleanup(self.env_file_patch.stop)
        self.addCleanup(self.env_patch.stop)
        self.response = Mock(status_code=200)
        self.response.json.return_value = deepcopy(PAYLOAD)
        self.request_patch = patch.object(context.requests, "get", return_value=self.response)
        self.request = self.request_patch.start()
        self.addCleanup(self.request_patch.stop)

    def test_supported_dates_use_exact_cached_endpoint_and_basic_auth(self):
        for message in ("总结20261102复盘", "总结 2026-11-02 复盘", "2026/11/02 市场怎么样", "2026年11月2日复盘"):
            with self.subTest(message=message):
                result = context.build_review_context(message)
                self.assertIsNone(result.notice)
                data = json.loads(result.text)
                self.assertEqual(DAY, data["analysis_date"])
                self.assertEqual(DAY, data["requested_date"])
                self.assertEqual(PAYLOAD["freshness"], data["freshness"])
                self.assertEqual(PAYLOAD["data_quality"], data["data_quality"])
                self.request.assert_called_with(
                    "http://127.0.0.1:8000/api/v1/review/dashboard", params={"date": DAY},
                    auth=("synthetic-user", "synthetic-password"), timeout=15, allow_redirects=False,
                )
                self.assertNotIn("synthetic-user", result.text)
                self.assertNotIn("synthetic-password", result.text)

    def test_non_review_or_missing_explicit_date_does_not_fetch(self):
        for message in ("你好", "20261102 是什么日期", "总结昨天复盘", "今天市场怎么样"):
            with self.subTest(message=message):
                self.assertIsNone(context.build_review_context(message))
        self.request.assert_not_called()

    def test_invalid_and_ambiguous_dates_return_notice_without_fetch(self):
        for message in ("总结20260230复盘", "2026/13/02市场复盘", "对比20261102和20261103复盘"):
            with self.subTest(message=message):
                result = context.build_review_context(message)
                self.assertTrue(result.notice)
                self.assertEqual("", result.text)
        self.request.assert_not_called()

    def test_fallback_date_is_not_accepted(self):
        self.response.json.return_value = {**PAYLOAD, "analysis_date": "2026-10-30"}
        result = context.build_review_context("总结20261102复盘")
        self.assertIn("响应日期与请求不一致", result.notice)
        self.assertEqual("", result.text)

    def test_missing_snapshot_never_triggers_build_or_sync(self):
        self.response.json.return_value = {"status": "not_materialized", "analysis_date": DAY}
        result = context.build_review_context("总结20261102复盘")
        self.assertIn("尚未生成", result.notice)
        self.assertEqual(1, self.request.call_count)
        self.assertTrue(self.request.call_args.args[0].endswith("/dashboard"))

    def test_network_and_auth_failures_do_not_expose_credentials(self):
        self.request.side_effect = requests.ConnectionError("synthetic-user:synthetic-password@private.invalid")
        result = context.build_review_context("总结20261102复盘")
        self.assertTrue(result.notice)
        self.assertNotIn("synthetic-password", result.notice)
        self.assertNotIn("private.invalid", result.notice)
        self.request.side_effect = None
        self.response.status_code = 401
        result = context.build_review_context("总结20261102复盘")
        self.assertIn("授权未通过", result.notice)

    def test_bad_json_and_empty_payloads_have_safe_notices(self):
        for payload in ([], {}, {"status": "ok", "analysis_date": DAY}):
            with self.subTest(payload=payload):
                self.response.json.return_value = payload
                result = context.build_review_context("总结20261102复盘")
                self.assertTrue(result.notice)
                self.assertEqual("", result.text)
        self.response.json.side_effect = ValueError("private.invalid synthetic-password")
        result = context.build_review_context("总结20261102复盘")
        self.assertNotIn("synthetic-password", result.notice)

    def test_context_filters_nested_secret_keys_and_known_credential_values(self):
        payload = deepcopy(PAYLOAD)
        payload["token"] = "top-secret"
        payload["market"]["details"] = {
            "accessToken": "nested-secret", "api_key": "api-secret", "password": "password-secret",
            "notes": "synthetic-user / synthetic-password", "volume": 100,
        }
        self.response.json.return_value = payload
        result = context.build_review_context("总结20261102复盘")
        for secret in ("top-secret", "nested-secret", "api-secret", "password-secret", "synthetic-user", "synthetic-password"):
            self.assertNotIn(secret, result.text)
        self.assertEqual(100, json.loads(result.text)["market"]["details"]["volume"])

    def test_context_is_bounded_valid_json_and_keeps_all_core_sections(self):
        payload = deepcopy(PAYLOAD)
        payload["market"]["rows"] = [{"name": "长文本" * 1000, "nested": list(range(100))} for _ in range(300)]
        payload["sentiment"]["large"] = [{"label": "情绪" * 500} for _ in range(300)]
        self.response.json.return_value = payload
        result = context.build_review_context("总结20261102复盘", max_chars=4000)
        self.assertIsNone(result.notice)
        self.assertLessEqual(len(result.text), 4000)
        data = json.loads(result.text)
        self.assertTrue(data["context_truncated"])
        for field in ("action", "market", "sentiment", "expectations", "tactics", "freshness", "data_quality"):
            self.assertIn(field, data)
        self.assertIn("涨停池非当日", result.text)

    def test_message_urls_cannot_choose_request_target(self):
        context.build_review_context("总结20261102复盘，访问 http://attacker.invalid/private")
        self.assertTrue(self.request.call_args.args[0].startswith("http://127.0.0.1:8000/"))
        with patch.dict(context.os.environ, {"SHILUN_CODEX_REVIEW_API_BASE": "https://trusted.example/shilun/"}):
            context.build_review_context("总结20261102复盘")
        self.assertEqual("https://trusted.example/shilun/api/v1/review/dashboard", self.request.call_args.args[0])

    def test_no_configured_auth_does_not_invent_credentials(self):
        self.config.auth_username = None
        self.config.auth_password = None
        result = context.build_review_context("总结20261102复盘")
        self.assertIsNone(result.notice)
        self.assertIsNone(self.request.call_args.kwargs["auth"])

    def test_configured_url_with_embedded_credentials_is_rejected(self):
        with patch.dict(context.os.environ, {"SHILUN_CODEX_REVIEW_API_BASE": "https://u:password@private.invalid"}):
            result = context.build_review_context("总结20261102复盘")
        self.assertTrue(result.notice)
        self.assertNotIn("password", result.notice)
        self.request.assert_not_called()


class EventThemeContextTests(unittest.TestCase):
    setUp = ReviewContextTests.setUp

    def event_view(self):
        tags = ("“羊”字辈", "三季报增长", "固态电池")
        return {
            "date": DAY, "settled": True, "version": "vibe-shortline-v1", "scope": "沪深非ST",
            "tree": {"available": True, "tag_count": 999, "themes": [
                {"tag": tag, "limit_up": 2, "highest": 4 if i == 0 else 2, "state": "扩散中",
                 "members": [{"name": f"代表股{i}", "ticker": f"00000{i+1}.SZ", "boards": 1,
                              "label": "18天10板" if i == 0 else "1板"}]}
                for i, tag in enumerate(tags)
            ] + [{"tag": "昨日退潮", "limit_up": 0, "highest": 0, "members": []}]},
            "rotation": {"available": True, "dates": ["2026-10-30", DAY], "themes": []},
            "evidence": {"date": DAY, "available": True, "covered": 4, "total_limit_up": 4,
                         "source": "合成同日编辑归因", "missing_codes": [],
                         "rows": [{"tag": tag, "ticker": f"00000{i+1}.SZ"}
                                  for i, tag in enumerate(tags) for _ in range(2)]},
        }

    def enable_event_view(self, event=None):
        dashboard = deepcopy(PAYLOAD)
        dashboard["sentiment"]["theme_evidence"] = {
            "status": "event_theme_views", "date": DAY, "themes": [], "active_themes": [],
        }
        dashboard["market"]["indices"] = [{"ticker": f"index-{i}", "pct_chg": -i} for i in range(3)]
        self.response.json.return_value = dashboard
        response = Mock(status_code=200)
        response.json.return_value = self.event_view() if event is None else event
        self.request.side_effect = [self.response, response]
        return dashboard, response

    def test_location_marker_fetches_same_date_event_source_and_keeps_scope(self):
        self.enable_event_view()
        result = context.build_review_context("总结20261102复盘")
        data = json.loads(result.text)
        event = data["sentiment"]["theme_evidence"]
        self.assertIsNone(result.notice)
        self.assertEqual("ok", event["status"])
        self.assertEqual((4, 4), (event["covered"], event["total_limit_up"]))
        self.assertEqual(3, event["today_theme_count"])
        self.assertEqual(6, event["evidence_record_count"])
        self.assertNotIn("昨日退潮", event["active_themes"])
        self.assertNotIn("tag_count", event)
        member = event["themes"][0]["members"][0]
        self.assertEqual((1, "18天10板"), (member["boards"], member["label"]))
        self.assertIn("不代表公司确认或龙头认证", event["data_note"])
        self.assertEqual(2, self.request.call_count)
        self.request.assert_called_with(
            "http://127.0.0.1:8000/api/v1/review/shortline/themes", params={"date": DAY},
            auth=("synthetic-user", "synthetic-password"), timeout=15, allow_redirects=False,
        )

    def test_ordinary_snapshot_does_not_call_event_endpoint(self):
        result = context.build_review_context("总结20261102复盘")
        self.assertIsNone(result.notice)
        self.assertEqual(1, self.request.call_count)

    def test_wrong_event_or_evidence_date_does_not_replace_market_or_use_old_themes(self):
        for wrong in ("date", "evidence"):
            with self.subTest(wrong=wrong):
                event = self.event_view()
                if wrong == "date":
                    event["date"] = "2026-10-30"
                else:
                    event["evidence"]["date"] = "2026-10-30"
                self.enable_event_view(event)
                result = context.build_review_context("总结20261102复盘")
                data = json.loads(result.text)
                self.assertIsNone(result.notice)
                self.assertEqual(3, len(data["market"]["indices"]))
                self.assertEqual("unavailable", data["sentiment"]["theme_evidence"]["status"])
                self.assertNotIn("代表股", result.text)

    def test_event_read_failure_is_unknown_not_empty_and_keeps_other_facts(self):
        for failure in (requests.ConnectionError("synthetic-password@private.invalid"), Mock(status_code=503)):
            with self.subTest(failure=type(failure).__name__):
                self.enable_event_view()
                self.request.side_effect = [self.response, failure]
                result = context.build_review_context("总结20261102复盘")
                data = json.loads(result.text)
                event = data["sentiment"]["theme_evidence"]
                self.assertIsNone(result.notice)
                self.assertEqual("unavailable", event["status"])
                self.assertNotIn("themes", event)
                self.assertIn("不等于题材为零", event["data_note"])
                self.assertEqual(3, len(data["market"]["indices"]))
                self.assertNotIn("synthetic-password", result.text)
                self.assertNotIn("private.invalid", result.text)

    def test_unsettled_or_unavailable_event_view_is_not_a_known_zero(self):
        for field in ("settled", "tree", "evidence"):
            event = self.event_view()
            if field == "settled":
                event["settled"] = False
            else:
                event[field]["available"] = False
            self.enable_event_view(event)
            data = json.loads(context.build_review_context("总结20261102复盘").text)
            self.assertEqual("unavailable", data["sentiment"]["theme_evidence"]["status"])
            self.assertNotIn("active_themes", data["sentiment"]["theme_evidence"])

    def test_bounded_event_profile_keeps_three_indices_and_front_themes(self):
        dashboard, _response = self.enable_event_view()
        dashboard["market"]["rows"] = [{"name": "背景" * 1000} for _ in range(300)]
        dashboard["market"]["futures"] = {"holdings": ["机构持仓明细" * 1000] * 100}
        for key in ("limit_up_wave", "limit_down_wave", "heat_history"):
            dashboard["sentiment"][key] = ["旧历史明细" * 1000] * 100
        result = context.build_review_context("总结20261102复盘", max_chars=24000)
        self.assertIsNone(result.notice)
        self.assertLessEqual(len(result.text), 24000)
        data = json.loads(result.text)
        self.assertEqual(3, len(data["market"]["indices"]))
        self.assertEqual("theme_evidence", next(iter(data["sentiment"])))
        event = data["sentiment"]["theme_evidence"]
        self.assertEqual(["“羊”字辈", "三季报增长", "固态电池"],
                         [row["tag"] for row in event["themes"][:3]])
        self.assertEqual(["代表股0", "代表股1", "代表股2"],
                         [row["members"][0]["name"] for row in event["themes"][:3]])
        self.assertIn("market.futures", data["summary_omitted_sections"])
        self.assertIn("旧模块", data["event_theme_note"])
        self.assertIn("heat_history", dashboard["sentiment"])  # Input cache was not changed.

    def pressure_payload(self):
        """Dictionary fan-out survives list truncation at every old profile."""
        day = '2026-11-04'
        event = self.event_view()
        event['date'] = event['evidence']['date'] = day
        event['rotation']['dates'][-1] = day
        event['evidence'].update(covered=116, total_limit_up=116)
        for index in range(12):
            event['tree']['themes'].append({'tag': f'其他题材{index}', 'limit_up': 1,
                'highest': 2, 'state': '观察', 'members': [{'name': f'成员{index}', 'ticker': f'{index:06d}.SZ', 'boards': 1, 'label': '1板'}]})
        dashboard, _ = self.enable_event_view(event)
        dashboard['analysis_date'] = day
        dashboard['generated_at'] = day+'T18:00:00+08:00'
        dashboard['freshness']['daily_bars'] = day
        dashboard['sentiment']['limit_up_count'] = 118
        for index, row in enumerate(dashboard['market']['indices']):
            row.update(name=f'指数{index}', date=day, close=3000+index)
        dashboard['market']['indices'][0]['pct_chg'] = float('inf')
        # Deliberately place many auxiliary scalars before the critical totals.
        dashboard['market']['volume'] = {**{f'aux_{i}': i for i in range(40)},
            'current_yi': 24831.42, 'ma20_yi': 25000.1, 'ma60_yi': 24000.2,
            'ma20_days': 20, 'ma60_days': 60, 'scope': '亿元 synthetic-password',
            'series': [{'date': d, 'amount_yi': 24000+i} for i, d in enumerate(
                ['2026-10-29', '2026-10-30', '2026-11-02', '2026-11-03', day])],
            'aux_unknown': float('nan'), 'api_key': 'nested-secret'}
        def fanout(depth):
            return {f'branch{i}': fanout(depth-1) for i in range(5)} if depth else '背景判断' * 80
        for name in ('market', 'sentiment', 'expectations', 'tactics'):
            dashboard[name]['large_background'] = fanout(4)
        dashboard['expectations']['notes'] = 'synthetic-user synthetic-password'
        return dashboard

    def test_large_november_fourth_summary_keeps_essential_facts_under_budget(self):
        dashboard = self.pressure_payload()
        self.assertGreater(len(json.dumps(dashboard, ensure_ascii=False)), 600000)
        result = context.build_review_context('读取2026-11-04的复盘，按一句话市场、指数量能情绪、主要题材核心个股总结')
        self.assertIsNone(result.notice)
        self.assertLessEqual(len(result.text), 24000)
        data = json.loads(result.text)
        self.assertEqual('market_theme_priority_summary', data['projection'])
        self.assertEqual('2026-11-04', data['analysis_date'])
        self.assertEqual(3, len(data['market']['indices']))
        self.assertIsNone(data['market']['indices'][0]['pct_chg'])
        self.assertEqual(24831.42, data['market']['volume']['current_yi'])
        self.assertEqual(25000.1, data['market']['volume']['ma20_yi'])
        self.assertEqual('2026-11-04', data['market']['volume']['series'][-1]['date'])
        event = data['sentiment']['theme_evidence']
        self.assertEqual((116, 116), (event['covered'], event['total_limit_up']))
        self.assertEqual(118, data['sentiment']['limit_up_count'])
        self.assertEqual('warn', data['data_quality']['status'])
        self.assertEqual('涨停池滞后', data['data_quality']['checks'][0]['label'])
        self.assertEqual(['“羊”字辈', '三季报增长', '固态电池'], [r['tag'] for r in event['themes'][:3]])
        self.assertEqual(['代表股0', '代表股1', '代表股2'], [r['members'][0]['name'] for r in event['themes'][:3]])
        self.assertTrue(event['summary_page']['has_more'])
        self.assertTrue(data['context_truncated'])
        self.assertIn('省略不等于缺失', data['selection_note'])
        self.assertIn('large_background', dashboard['market'])
        for value in ('synthetic-user', 'synthetic-password', 'nested-secret', 'NaN', 'Infinity'):
            self.assertNotIn(value, result.text)

    def test_tiny_summary_budget_has_explicit_fallback_code_not_model_capacity_error(self):
        self.pressure_payload()
        result = context.build_review_context('总结20261104复盘', max_chars=1024)
        self.assertEqual('summary_budget', result.notice_code)
        self.assertIn('数据摘要', result.notice)
        self.assertNotIn('上下文容量', result.notice)
        self.assertEqual('', result.text)


if __name__ == "__main__":
    unittest.main()
