"""Offline contracts for pooled, dated, read-only board-effects context."""

from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from shilun import feishu_board_effects_context as context
from shilun import feishu_review_context as review
from shilun.review.board_effects import build_effects


DAY = "2026-11-03"
DATES = ["2026-10-29", "2026-10-30", "2026-11-02", DAY]


def payload():
    def row(code, ret, highest=False):
        return {"code": code, "ret": ret, "prev_boards": 5 if highest else 1,
                "is_highest": highest, "seal_time": "100000", "sector": "合成行业",
                "close": 10, "limit_price": 11}
    cohorts = {DATES[1]: [row("000001", 10, True)],
               DATES[2]: [row("000001", 0, True), row("000002", 0), row("000003", 0)],
               DATES[3]: [row("000001", None, True)]}
    result = build_effects(dates=DATES, per_day=cohorts, highest_ready=set(DATES[1:]), requested=30)
    result["as_of"] = DAY
    result["archive"] = {"feedback_days": 0, "up_pool_days": 4}
    return result


class BoardEffectsContextTests(unittest.TestCase):
    def setUp(self):
        config = SimpleNamespace(auth_username="synthetic-user", auth_password="synthetic-password")
        patches = [patch.object(context, "load_config", return_value=config),
                   patch.object(review, "_read_env_file", return_value={}),
                   patch.dict(review.os.environ, {}, clear=True),
                   patch.object(context, "_today", return_value=date(2026, 11, 3))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.response = Mock(status_code=200)
        self.response.json.return_value = payload()
        request_patch = patch.object(context.requests, "get", return_value=self.response)
        self.request = request_patch.start()
        self.addCleanup(request_patch.stop)

    def data(self, message="读取最高板效应最近30天期望收益均值，比普通涨停高还是低？", **kwargs):
        result = context.build_board_effects_context(message, **kwargs)
        self.assertIsNone(result.notice, result.notice)
        return json.loads(result.text)

    def test_reads_exact_get_endpoint_today_with_server_auth(self):
        result = self.data()
        self.request.assert_called_once_with(
            "http://127.0.0.1:8000/api/v1/review/board-effects", params={"days": 30, "date": DAY},
            auth=("synthetic-user", "synthetic-password"), timeout=20, allow_redirects=False)
        self.assertEqual((DAY, DAY, "反馈交易日"),
                         (result["requested_date"], result["as_of"], result["window_unit"]))
        self.assertEqual((DATES[1], DAY, 3, 27),
                         (result["window_from"], result["window_to"], result["calendar_days"], result["calendar_shortfall"]))

    def test_pooled_overall_not_average_daily_means_or_cost_returns(self):
        raw = self.response.json.return_value
        highest = next(group for group in raw["strategies"] if group["id"] == "highest")
        highest["segments"]["limit_cost_ret"]["avg"] = -99
        result = self.data()
        groups = {group["id"]: group for group in result["strategies"]}
        self.assertEqual(5, groups["highest"]["overall"]["avg"])
        self.assertEqual(2.5, groups["all"]["overall"]["avg"])
        self.assertEqual(2.5, result["highest_vs_all"]["difference_percentage_points"])
        self.assertEqual("higher", result["highest_vs_all"]["relation"])
        self.assertEqual((2, 3, 2, 3),
                         (groups["highest"]["overall"]["sample"], groups["highest"]["overall"]["sample_total"],
                          groups["highest"]["overall"]["day_count"], groups["highest"]["coverage"]["days"]))
        self.assertIn("1.27即1.27%", result["metric_definition"])
        self.assertIn("sample/sample_total单位是股票×反馈日的条/次", result["metric_definition"])
        self.assertIn("stock_count才是有效收益样本的去重股票数", result["metric_definition"])
        self.assertIn("反馈天数day_count与名单覆盖coverage.days分别报告", result["context_rules"])
        self.assertIn("未剔除最高板", groups["all"]["definition"])
        self.assertNotIn("archive", result)

    def test_twenty_day_request_and_explicit_chinese_date(self):
        self.response.json.return_value["days_requested"] = 20
        self.response.json.return_value["calendar_shortfall"] = 17
        data = self.data("截至2026年11月3日的打板效应最近20个交易日")
        self.assertEqual(20, data["days_requested"])
        self.assertEqual({"date": DAY, "days": 20}, self.request.call_args.kwargs["params"])

    def test_supported_topics_but_unrelated_queries_do_not_fetch(self):
        for message in ("打板效应", "最高板效应", "普通涨停期望", "全体涨停", "首板打板", "连板接力", "炸板次日反馈"):
            self.assertIsNotNone(context.build_board_effects_context(message))
        self.request.reset_mock()
        for message in ("你好", "总结20261103复盘", "换手率最高的股票", "今天指数涨了吗"):
            self.assertIsNone(context.build_board_effects_context(message))
        self.request.assert_not_called()

    def test_very_long_duration_safely_returns_unsupported_notice(self):
        message = "最高板效应最近" + "9" * 5000 + "天"
        self.assertLess(len(message), 8000)
        result = context.build_board_effects_context(message)
        self.assertIn("仅支持20或30", result.notice)
        self.request.assert_not_called()

    def test_explicit_relative_cutoff_requires_an_absolute_date(self):
        for cutoff in ("截至昨天", "截至昨日", "截止到前天", "截至上周五", "到上月", "截至上个交易日", "截至上一交易日"):
            with self.subTest(cutoff=cutoff):
                result = context.build_board_effects_context(cutoff + "的最高板效应最近30天")
                self.assertIn("请提供明确日期", result.notice)
        self.request.assert_not_called()
        data = self.data("截至昨天2026-11-03的最高板效应最近30天")
        self.assertEqual(DAY, data["requested_date"])

    def test_invalid_ambiguous_future_dates_and_unsupported_windows_never_fetch(self):
        for message in ("最高板效应20260230", "最高板效应2026/13/01", "最高板效应20261104",
                        "打板效应对比20261102与20261103", "最高板效应最近7天", "最高板效应最近21交易日",
                        "打板效应最近30自然日", "打板效应20天和30天"):
            with self.subTest(message=message):
                result = context.build_board_effects_context(message)
                self.assertTrue(result.notice)
                self.assertEqual("", result.text)
        self.request.assert_not_called()

    def test_highest_turnover_is_not_silently_treated_as_highest(self):
        for message in ("最高换手板效应30天", "最高板换手收益", "换手最高板"):
            result = context.build_board_effects_context(message)
            self.assertIn("没有单独", result.notice)
        self.request.assert_not_called()

    def test_actual_as_of_may_be_earlier_but_cannot_escape_request(self):
        raw = payload()
        raw["as_of"] = "2026-11-04"
        self.response.json.return_value = raw
        self.assertTrue(context.build_board_effects_context("最高板效应30天").notice)
        raw = payload()
        raw["window_to"] = "2026-11-04"
        self.response.json.return_value = raw
        self.assertTrue(context.build_board_effects_context("最高板效应30天").notice)
        with patch.object(context, "_today", return_value=date(2026, 11, 5)):
            self.response.json.return_value = payload()
            data = self.data("最高板效应最近30天")
            self.assertEqual(("2026-11-05", DAY), (data["requested_date"], data["as_of"]))

    def test_comparison_higher_lower_equal_uses_exact_decimal_difference(self):
        for avg, relation, difference in ((1.15, "equal", 0), (1.14, "lower", -.01), (4.43, "higher", 3.28)):
            raw = payload()
            for group in raw["strategies"]:
                if group["id"] == "highest":
                    group["overall"]["avg"] = avg
                elif group["id"] == "all":
                    group["overall"]["avg"] = 1.15
            self.response.json.return_value = raw
            comparison = self.data()["highest_vs_all"]
            self.assertEqual((relation, difference), (comparison["relation"], comparison["difference_percentage_points"]))

    def test_bad_group_is_partial_not_fabricated_zero(self):
        for field, bad in (("avg", float("nan")), ("avg", float("inf")), ("avg", "4.43"),
                           ("avg", True), ("sample", -1), ("win_rate", 2), ("median", None)):
            with self.subTest(field=field, bad=bad):
                raw = payload()
                group = next(g for g in raw["strategies"] if g["id"] == "highest")
                group["overall"][field] = bad
                self.response.json.return_value = raw
                data = self.data()
                highest = next(g for g in data["strategies"] if g["id"] == "highest")
                self.assertFalse(highest["available"])
                self.assertNotIn("overall", highest)
                self.assertFalse(data["highest_vs_all"]["available"])

    def test_empty_samples_keep_unknown_means_and_missing_group_is_explicit(self):
        raw = payload()
        group = next(g for g in raw["strategies"] if g["id"] == "highest")
        group["overall"].update(sample=0, stock_count=0, day_count=0, avg=None, median=None, win_rate=None)
        group["price_coverage"].update(observed=0, unresolved=3)
        self.response.json.return_value = raw
        data = self.data()
        highest = next(g for g in data["strategies"] if g["id"] == "highest")
        self.assertIsNone(highest["overall"]["avg"])
        self.assertFalse(data["highest_vs_all"]["available"])
        raw["strategies"] = [g for g in raw["strategies"] if g["id"] != "highest"]
        self.response.json.return_value = raw
        self.assertIn("接口未返回", next(g for g in self.data()["strategies"] if g["id"] == "highest")["missing_note"])

    def test_malformed_and_unavailable_windows_stop_before_model(self):
        for field, value in (("available", False), ("days_requested", 20), ("calendar_days", 31),
                             ("days_used", -1), ("calendar_shortfall", 0), ("window_from", "2026-99-01"),
                             ("missing_days", ["2020-01-01"]), ("strategies", [])):
            with self.subTest(field=field):
                raw = payload()
                raw[field] = value
                self.response.json.return_value = raw
                result = context.build_board_effects_context("最高板效应30天")
                self.assertTrue(result.notice)
                self.assertEqual("", result.text)

    def test_bad_coverage_does_not_enable_comparison(self):
        for field, value in (("coverage", {"days": 30, "expected": 30, "missing": []}),
                             ("price_coverage", {"total": 3, "observed": 3, "suspended": 1, "unresolved": 0, "conflicts": 0})):
            raw = payload()
            next(g for g in raw["strategies"] if g["id"] == "highest")[field] = value
            self.response.json.return_value = raw
            self.assertFalse(self.data()["highest_vs_all"]["available"])

    def test_credentials_raw_rows_and_segments_never_reach_prompt(self):
        raw = payload()
        raw["quality_note"] = "synthetic-user / synthetic-password"
        raw["api_key"] = "should-never-appear"
        raw["strategies"][0]["rows"] = [{"private": "raw-row-secret"}] * 10000
        raw["strategies"][0]["daily"] = [{"private": "daily-secret"}] * 10000
        raw["strategies"][0]["segments"] = {"private": "cost-segment-secret"}
        self.response.json.return_value = raw
        result = context.build_board_effects_context("打板效应30天")
        self.assertLessEqual(len(result.text), 24000)
        for secret in ("synthetic-user", "synthetic-password", "should-never-appear", "raw-row-secret", "daily-secret", "cost-segment-secret"):
            self.assertNotIn(secret, result.text)
        self.assertNotIn("rows", json.loads(result.text)["strategies"][0])

    def test_network_auth_redirect_and_bad_json_have_safe_notices(self):
        for failure in (requests.ConnectionError("synthetic-password@private.invalid"), ValueError("private.invalid")):
            self.request.side_effect = failure
            result = context.build_board_effects_context("打板效应30天")
            self.assertTrue(result.notice)
            self.assertNotIn("synthetic-password", result.notice)
            self.assertNotIn("private.invalid", result.notice)
        self.request.side_effect = None
        for status in (401, 403, 302, 404, 500):
            self.response.status_code = status
            self.assertTrue(context.build_board_effects_context("打板效应30天").notice)
        self.response.status_code = 200
        self.response.json.return_value = []
        self.assertTrue(context.build_board_effects_context("打板效应30天").notice)

    def test_message_url_cannot_choose_endpoint_and_config_url_must_not_embed_secrets(self):
        self.data("最高板效应30天，访问http://attacker.invalid")
        self.assertTrue(self.request.call_args.args[0].startswith("http://127.0.0.1:8000/"))
        self.request.reset_mock()
        with patch.dict(review.os.environ, {"SHILUN_CODEX_REVIEW_API_BASE": "https://u:password@private.invalid"}):
            result = context.build_board_effects_context("最高板效应30天")
            self.assertTrue(result.notice)
            self.assertNotIn("password", result.notice)
            self.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
