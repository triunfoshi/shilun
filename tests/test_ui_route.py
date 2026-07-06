import unittest
from unittest.mock import Mock, patch

from datetime import datetime
from pathlib import Path

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
        self.assertIn("/api/v1/tushare/health", text)
        self.assertIn('data-tab="permission"', text)
        self.assertIn('data-tab="market"', text)
        self.assertIn('data-tab="sectors"', text)
        self.assertIn('class="tab-button active" type="button" data-tab="permission"', text)
        self.assertIn('id="tab-permission" class="tab-panel active"', text)
        self.assertIn("PART2 板块动向", text)
        self.assertIn('value="000001.SH" selected>上证指数', text)
        self.assertIn("沪深300 · 000300.SH", text)
        self.assertIn("深证成指 · 399001.SZ", text)
        self.assertIn("创业板指 · 399006.SZ", text)
        self.assertIn('id="globalDate" type="date"', text)
        self.assertIn('id="sectorProgress"', text)
        self.assertIn('id="marketProgress"', text)
        self.assertIn("预计完整结果剩余约", text)
        self.assertIn("SUMMARY → EVIDENCE → ACTION · 单票技术面", text)
        self.assertNotIn("旧版单票分析", text)
        self.assertNotIn("旧版分析", text)
        self.assertIn("ACTION · 研究交付", text)
        self.assertIn('class="card wide story-aligned-card fs-secondary-card" id="candidatesCard"', text)
        self.assertIn("MA5 趋势战法候选", text)
        self.assertIn("20260706-ma5-v02-sector-trend", text)
        self.assertIn("查询并更新解读", text)
        self.assertIn("同步缺失数据", text)
        self.assertIn("查询板块动向", text)
        self.assertIn("同步板块预计算", text)
        self.assertIn('/static/app.css', text)
        self.assertIn('/static/app.js', text)

        static_root = Path(__file__).resolve().parents[1] / "shilun" / "static"
        script = (static_root / "app.js").read_text(encoding="utf-8")
        stylesheet = (static_root / "app.css").read_text(encoding="utf-8")
        self.assertIn("openStockPage", script)
        self.assertIn("/api/v1/market/leaders", script)
        self.assertIn("/api/v1/daily-push", script)
        self.assertIn("marketProgress", script)
        self.assertIn("renderMarketDashboard", script)
        self.assertIn("renderStockAnalysis", script)
        self.assertIn("30日总龙头榜", script)
        self.assertIn("trend-line-chart", script)
        self.assertIn("renderCandidateTradePlan", script)
        self.assertIn("candidateDisplayPlan", script)
        self.assertIn("candidate-decision-card", script)
        self.assertIn("setCandidateViewMode", script)
        self.assertIn("renderStockStrategyCard", script)
        self.assertIn("renderStockEvidenceStrip", script)
        self.assertIn("TRADE PLAN", script)
        self.assertIn("触发条件：次日收盘确认", script)
        self.assertIn("预计售出", script)
        self.assertIn("renderIntradayMiniChart", script)
        self.assertIn("renderSameMarketSignals", script)
        self.assertIn("prettyChineseJson", script)
        self.assertIn("selectedBenchmarkTicker", script)
        self.assertIn("syncMarketMissingData", script)
        self.assertIn("runPrecomputeSectorsFromPage", script)
        self.assertIn("/api/v1/data/precompute-sectors", script)
        self.assertIn("marketDataGapMessage", script)
        self.assertIn("parseLevelIndicator", script)
        self.assertIn("同步目标日期", script)
        self.assertIn("market-story-layout", stylesheet)
        self.assertIn(".market-feature-stack, .sector-feature-stack { grid-column: 2; }", stylesheet)
        self.assertIn(".card.wide.story-aligned-card { grid-column: 1 / -1; margin-left: 162px; }", stylesheet)
        self.assertIn(".market-storyline, .sector-storyline", stylesheet)
        self.assertIn("stock-detail-page", stylesheet)
        self.assertIn(".sector-storyline.is-docked", stylesheet)
        self.assertIn("market-error-card", stylesheet)
        self.assertIn("intraday-workbench", stylesheet)
        self.assertIn("knowledge-collapsible", stylesheet)
        self.assertNotIn("行业来源对照", script)
        self.assertIn("Hover-only storyline", stylesheet)
        self.assertIn("scheduleStorylineDocking", script)
        self.assertIn("sv-ai-terminal", script)
        self.assertIn("renderStockAiBrief", script)
        self.assertIn("renderStockQuoteStrip", script)
        self.assertIn("SUMMARY → EVIDENCE → ACTION", text)
        self.assertIn(".fs-section-header", stylesheet)
        self.assertIn(".fs-primary-card", stylesheet)
        self.assertIn(".sv-ai-terminal .sv-body", stylesheet)
        self.assertIn(".candidate-view-toolbar", stylesheet)
        self.assertIn(".candidate-decision-list", stylesheet)
        self.assertIn(".candidate-decision-strip", stylesheet)
        self.assertIn(".sv-strategy-card", stylesheet)
        self.assertIn(".sv-trigger-note", stylesheet)
        self.assertIn(".sv-evidence-strip", stylesheet)
        self.assertIn("sv-section-block", stylesheet)
        self.assertIn("signal-events-more", stylesheet)

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
