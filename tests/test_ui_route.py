import unittest
from unittest.mock import Mock, patch

from datetime import datetime

from shilun.api import control_center, default_analysis_date, normalize_analysis_date, push_channel_status, tushare_health
from shilun.common.config import AppConfig


class AuthFailingTushareClient:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def fetch_stock_basic(self, **_kwargs):
        raise Exception("您的token不对，请确认。")

    def fetch_trade_calendar(self, **_kwargs):
        raise Exception("您的token不对，请确认。")

    def fetch_daily_for_trade_date(self, *_args, **_kwargs):
        raise Exception("您的token不对，请确认。")

    def fetch_daily_basic(self, **_kwargs):
        raise Exception("您的token不对，请确认。")


class HealthyTushareClient:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def fetch_stock_basic(self, **_kwargs):
        return [object()] * 1200

    def fetch_trade_calendar(self, **_kwargs):
        return [object()] * 20

    def fetch_daily_for_trade_date(self, *_args, **_kwargs):
        return [object()] * 1200

    def fetch_daily_basic(self, **_kwargs):
        return [object()] * 1200


class UIRouteTests(unittest.TestCase):
    def test_control_center_page_is_available(self) -> None:
        response = control_center()
        text = response.body.decode("utf-8")

        self.assertEqual(200, response.status_code)
        self.assertIn("石论控制台", text)
        self.assertIn("/api/v1/analyze", text)
        self.assertIn("/api/v1/market/permission", text)
        self.assertIn("/api/v1/market/sectors", text)
        self.assertIn("/api/v1/market/leaders", text)
        self.assertIn("/api/v1/tushare/health", text)
        self.assertIn("/api/v1/daily-push", text)
        self.assertIn("1 数据同步", text)
        self.assertIn("2 权限确认", text)
        self.assertIn("4 板块动向", text)
        self.assertIn('class="tab-button active" type="button" data-tab="data"', text)
        self.assertIn('id="tab-data" class="tab-panel active"', text)
        self.assertIn("PART2 板块动向", text)
        self.assertIn('value="000001.SH" selected>上证指数', text)
        self.assertIn("沪深300 · 000300.SH", text)
        self.assertIn("深证成指 · 399001.SZ", text)
        self.assertIn("创业板指 · 399006.SZ", text)
        self.assertIn('id="marketDate" type="hidden"', text)
        self.assertIn('id="sectorDate" type="hidden"', text)
        self.assertNotIn('type="date"', text)
        self.assertIn('class="date-wheel" data-target="marketDate"', text)
        self.assertIn('class="date-wheel" data-target="sectorDate"', text)
        self.assertIn("下拉滚轮选择年月日", text)
        self.assertIn("默认最近工作日", text)
        self.assertIn("周末自动回退", text)
        self.assertIn("info-popover", text)
        self.assertIn("定义说明", text)
        self.assertIn("metric-explain", text)
        self.assertIn("查询单票记录", text)
        self.assertIn("每日龙头榜", text)
        self.assertIn("近 30 个交易日龙头榜", text)
        self.assertIn("今日强力板块", text)
        self.assertIn("趋势板块", text)
        self.assertIn("trend-line-chart", text)
        self.assertIn("trend-point-tooltip", text)
        self.assertIn("30日总龙头榜", text)
        self.assertIn("单日龙头榜", text)
        self.assertIn("查询单个龙头的 30 日记录", text)
        self.assertIn("指标口径与边界", text)
        self.assertIn("sector-storyline", text)
        self.assertIn('id="sectorProgress"', text)
        self.assertIn("预计完整结果剩余约", text)
        self.assertIn("advanceSectorProgressToLeaders", text)
        self.assertIn("指标口径", text)
        self.assertIn("moneyflow 官方资金流", text)
        self.assertIn("查询并更新解读", text)
        self.assertIn("查询板块动向", text)

    def test_default_analysis_date_skips_weekends(self) -> None:
        self.assertEqual("2026-06-12", default_analysis_date(datetime(2026, 6, 14, 9, 0)))
        self.assertEqual("2026-06-12", normalize_analysis_date("2026-06-13"))
        self.assertEqual("2026-06-12", normalize_analysis_date("2026-06-12"))

    def test_push_channel_status_exposes_state_without_full_secrets(self) -> None:
        payload = push_channel_status()

        self.assertIn("feishu_configured", payload)
        self.assertIn("telegram_daily_push_enabled", payload)
        webhook = payload.get("feishu_webhook")
        if webhook:
            self.assertIn("...", webhook)

    def test_tushare_health_reports_auth_error_explicitly(self) -> None:
        config = AppConfig(
            tushare_token="configured-token",
            tushare_base_url="http://api.waditu.com/dataapi",
        )
        with (
            patch("shilun.api.load_config", return_value=config),
            patch("shilun.api.requests.get", return_value=Mock(status_code=200)),
            patch("shilun.data.TushareDailyClient", AuthFailingTushareClient),
        ):
            payload = tushare_health()

        self.assertEqual("auth_error", payload["overall_status"])
        self.assertEqual("Token无效", payload["overall_label"])
        data_checks = [check for check in payload["checks"] if check["name"] != "gateway_http"]
        self.assertTrue(data_checks)
        self.assertTrue(all(check["status"] == "auth_error" for check in data_checks))
        self.assertIn("SHILUN_TUSHARE_TOKEN", payload["recommendation"])

    def test_tushare_health_uses_sdk_probe_for_private_gateway(self) -> None:
        config = AppConfig(
            tushare_token="configured-token",
            tushare_base_url="https://tt.xiaodefa.cn",
            tushare_min_interval_seconds=0,
        )
        gateway_get = Mock(status_code=502)
        with (
            patch("shilun.api.load_config", return_value=config),
            patch("shilun.api.requests.get", gateway_get),
            patch("shilun.data.TushareDailyClient", HealthyTushareClient),
        ):
            payload = tushare_health()

        gateway_get.assert_not_called()
        self.assertEqual("available", payload["overall_status"])
        self.assertEqual("sdk_probe", payload["checks"][0]["status"])


if __name__ == "__main__":
    unittest.main()
