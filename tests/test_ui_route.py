import unittest
from unittest.mock import Mock, patch

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from shilun.api import control_center, default_analysis_date, normalize_analysis_date, push_channel_status, search_stocks, tushare_health
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
    def test_external_wave_document_routes_are_retired(self) -> None:
        from shilun.api import app

        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertNotIn("/api/v1/market/upload-wave-doc", paths)
        self.assertNotIn("/api/v1/market/wave-docs", paths)
        self.assertIn("/api/v1/market/permission", paths)

    def test_stock_search_ranks_exact_and_returns_local_master_data(self) -> None:
        stores = []

        class FakeCursor(list):
            def limit(self, count):
                return FakeCursor(self[:count])

        class FakeCollection:
            def __init__(self, name, store):
                self.name, self.store = name, store

            def find(self, query, *_args, **_kwargs):
                self.store.reads.append((self.name, query))
                if self.name == "stock_basic":
                    return FakeCursor([
                        {"ts_code": "688123.SH", "symbol": "688123", "name": "兆易测试", "industry": "旧测试行业", "market": "科创板"},
                        {"ts_code": "603986.SH", "symbol": "603986", "name": "兆易创新", "industry": "旧行业不应直出", "market": "主板"},
                    ])
                if self.name == "market_taxonomy_stocks":
                    return FakeCursor([{"ticker": "603986.SH", "revision": "dated-taxonomy", "industry": "半导体",
                        "industry_code": "BK0001.DC", "taxonomy_date": "2026-10-23"}])
                raise AssertionError(f"Unexpected search collection: {self.name}")

            def find_one(self, query, *_args, **_kwargs):
                self.store.reads.append((self.name, query))
                if self.name == "daily_basic":
                    return {"trade_date": "20261023"}
                if self.name == "market_taxonomy_snapshots":
                    if query == {"_id": "eastmoney-taxonomy-v1:2026-10-23", "status": "ready"}:
                        return {"revision": "dated-taxonomy", "date": "2026-10-23"}
                    raise AssertionError(f"Unexpected taxonomy date: {query}")
                raise AssertionError(f"Unexpected search lookup: {self.name}")

        class FakeStore:
            def __init__(self, *_args, **_kwargs):
                self.closed = False
                self.reads = []
                stores.append(self)

            def collection(self, name):
                return FakeCollection(name, self)

            def close(self):
                self.closed = True

        config = SimpleNamespace(mongo_uri="mongodb://test", mongo_db="shilun")
        with patch("shilun.api.load_config", return_value=config), patch("shilun.common.db.MongoSnapshotStore", FakeStore):
            result = search_stocks("603986", 10)

        self.assertEqual("603986.SH", result["items"][0]["ticker"])
        self.assertEqual("兆易创新", result["items"][0]["name"])
        self.assertEqual("半导体", result["items"][0]["industry"])
        self.assertEqual("BK0001.DC", result["items"][0]["industry_code"])
        self.assertEqual("2026-10-23", result["items"][0]["taxonomy_date"])
        self.assertEqual("分类待补齐", result["items"][1]["industry"])
        self.assertTrue(stores[0].closed)
        self.assertIn(("market_taxonomy_stocks", {"revision": "dated-taxonomy", "ticker": {"$in": ["603986.SH", "688123.SH"]}}), stores[0].reads)

    def test_control_center_page_is_available(self) -> None:
        response = control_center()
        text = response.body.decode("utf-8")

        self.assertEqual(200, response.status_code)
        self.assertIn("VC-Astock", text)
        self.assertNotIn("瑞势投研", text)
        self.assertIn("/static/vc-astock-mark.svg", text)
        self.assertIn("/static/brand.css", text)
        self.assertIn("/static/workspace-navigation.css", text)
        requested_icon = '<i class="fa-thin fa-chart-candlestick" style="color: rgb(239, 20, 20);"></i>'
        self.assertEqual(2, text.count(requested_icon))
        self.assertIn("/api/v1/analyze", text)
        self.assertIn("/static/trade-decision.js", text)
        self.assertIn("/api/v1/market/sectors", text)
        self.assertIn("/api/v1/tushare/health", text)
        self.assertIn('data-tab="permission"', text)
        self.assertNotIn('data-tab="market"', text)  # The old entry now aliases P1; do not restore a duplicate tab.
        self.assertIn('data-tab="sectors"', text)
        self.assertIn('data-tab="holdings"', text)
        self.assertIn('id="nav-review" class="tab-button active" type="button" role="tab"', text)
        self.assertIn('aria-controls="tab-review" aria-selected="true" data-tab="review" data-workspace="review"', text)
        self.assertIn('id="tab-review" class="tab-panel active"', text)
        self.assertIn('id="tab-trading" class="tab-panel"', text)
        review_start = text.index('aria-label="VC-Astock 复盘工作区"')
        research_start = text.index('aria-label="VC-Astock 研究工作区"')
        trading_start = text.index('aria-label="VC-Astock 交易工作区"')
        system_start = text.index('aria-label="VC-Astock 系统与交付工作区"')
        self.assertLess(review_start, research_start)
        self.assertLess(research_start, trading_start)
        self.assertLess(trading_start, system_start)
        review_navigation = text[review_start:research_start]
        research_navigation = text[research_start:trading_start]
        trading_navigation = text[trading_start:system_start]
        self.assertIn("指数复盘", review_navigation)
        for label in ("板块研究", "选品研究", "个股研究", "涨停聚焦", "打板效应"):
            self.assertIn(label, research_navigation)
        self.assertIn("市场模式观察", research_navigation)
        self.assertIn("交易记录与复盘", review_navigation)
        for label in ("交易决策", "明日预案", "今日跟踪", "持仓执行", "盘中监控", "成交台账"):
            self.assertIn(label, trading_navigation)
        for key in ("permission", "trading", "holdings", "decision-plan", "decision-live"):
            self.assertIn(f'id="nav-{key}" hidden data-review-retired="true"', trading_navigation)
        self.assertNotIn('id="nav-decision-research"', trading_navigation)
        journal_start = text.index('<section id="tab-trade-journal"')
        journal_end = text.index('<section id="tab-analysis"', journal_start)
        journal_workspace = text[journal_start:journal_end]
        self.assertIn('id="journalAccountTools"', journal_workspace)
        self.assertIn('id="tab-holdings" aria-label="账户事实工具"', journal_workspace)
        self.assertIn('/static/journal-workspace.js', text)
        self.assertNotIn("大盘研究", review_navigation)
        self.assertNotIn("指数复盘", research_navigation)
        self.assertNotIn("交易决策", research_navigation)
        self.assertNotIn("选品研究", trading_navigation)
        for anchor in ("#review-step-conclusion", "#review-step-facts"):
            self.assertIn(f'href="{anchor}"', text)
        for anchor in ("#review-step-expectation", "#review-step-candidates", "#review-step-summary"):
            self.assertNotIn(f'href="{anchor}"', text)  # Extended P2 content lives in the collapsed V2 renderer.
        self.assertIn('id="nav-sentiment"', text)
        self.assertIn('id="sentimentDate"', text)
        self.assertIn('/static/review-dashboard.css', text)
        self.assertIn('/static/review-dashboard.js', text)
        self.assertIn('id="tab-permission" class="tab-panel"', text)
        self.assertIn("PART2 板块动向", text)
        self.assertIn('value="000001.SH" selected>上证指数', text)
        self.assertIn("沪深300 · 000300.SH", text)
        self.assertIn("深证成指 · 399001.SZ", text)
        self.assertIn("创业板指 · 399006.SZ", text)
        self.assertIn('id="globalDate" type="date"', text)
        self.assertIn('id="sectorProgress"', text)
        self.assertIn('id="reviewDashboardRoot"', text)
        self.assertIn('id="reviewBuildButton"', text)
        self.assertNotIn("外部形态证据文档", text)
        self.assertNotIn('id="waveDocFile"', text)
        self.assertNotIn('id="waveDocResult"', text)
        self.assertNotIn("uploadWaveDoc", text)
        self.assertIn("FACTS → EVENTS → OPTIONAL STRATEGY · 单票研究", text)
        self.assertIn('id="stockStrategySelect"', text)
        self.assertIn("通用分析（不应用策略）", text)
        self.assertNotIn("旧版单票分析", text)
        self.assertNotIn("旧版分析", text)
        self.assertIn("ACTION · 研究交付", text)
        self.assertIn('class="card wide fs-primary-card selection-primary-card" id="candidatesCard"', text)
        self.assertIn('id="candidateStrategySelect"', text)
        self.assertIn('value="ma5_v0_2" selected>MA5 趋势', text)
        self.assertIn("20260720-index-align-v1", text)
        self.assertIn("20260903-vc-astock-v1", text)
        self.assertIn("/static/workspace-controls.css", text)
        self.assertIn("/static/market-v2.css", text)
        self.assertIn("指数事实 → 市场姿态 → 次日验证", text)
        self.assertIn('id="tradingHoldingList"', text)
        self.assertIn('id="tradingCockpitContent"', text)
        self.assertIn('id="tradingRiskProfile"', text)
        self.assertIn('id="tradingOrderPanel"', text)
        self.assertIn('/static/trading-order-panel.css', text)
        self.assertIn('/static/trading-inline-edit.css', text)
        self.assertIn('role="combobox"', text)
        self.assertIn('id="tradingStockResults-holding"', text)
        self.assertIn("E7 · 超跌反弹隔夜", text)
        self.assertIn('id="holdingStopPlanPreview"', text)
        self.assertIn("previewNewHoldingStopPlan", text)
        self.assertIn('id="tab-holdings"', text)
        self.assertIn('id="holdingsSummary"', text)
        self.assertIn('id="holdingsList"', text)
        self.assertIn('id="holdingCreateForm"', text)
        self.assertIn('id="holdingStrategyId"', text)
        self.assertIn("账户成交与当前库存", text)
        self.assertIn('onclick="loadReviewDashboard({force:true})"', text)
        self.assertIn('onclick="materializeReviewDashboard()"', text)
        self.assertIn('id="reviewDateHint"', text)
        self.assertIn("查询板块动向", text)  # 主按钮："查询板块动向 · 读缓存 · 秒回"
        self.assertIn("生成 / 查询任务", text)  # 同版本结果复用，失败后单独显式重试。
        self.assertIn('/static/app.css', text)
        self.assertIn('/static/app.js', text)

        static_root = Path(__file__).resolve().parents[1] / "shilun" / "static"
        script = (static_root / "app.js").read_text(encoding="utf-8")
        stylesheet = (static_root / "app.css").read_text(encoding="utf-8")
        brand_stylesheet = (static_root / "brand.css").read_text(encoding="utf-8")
        navigation_stylesheet = (static_root / "workspace-navigation.css").read_text(encoding="utf-8")
        review_script = (static_root / "review-dashboard.js").read_text(encoding="utf-8")
        review_stylesheet = (static_root / "review-dashboard.css").read_text(encoding="utf-8")
        inline_stylesheet = (static_root / "trading-inline-edit.css").read_text(encoding="utf-8")
        controls_stylesheet = (static_root / "workspace-controls.css").read_text(encoding="utf-8")
        strong_stock_script = (static_root / "strong-stocks.js").read_text(encoding="utf-8")
        self.assertIn('workspace: "review"', script)
        self.assertIn('workspace: "research"', script)
        self.assertIn('workspace: "trading"', script)
        self.assertIn('workspace: "system"', script)
        self.assertIn('button.setAttribute("aria-selected", String(selected))', script)
        self.assertIn("button.tabIndex = selected ? 0 : -1", script)
        self.assertIn("handleWorkspaceTabKeydown", script)
        self.assertIn('tabList.addEventListener("keydown", handleWorkspaceTabKeydown)', script)
        self.assertIn('document.body.dataset.workspace = workspace', script)
        self.assertIn("VC-Astock 结论", script)
        self.assertNotIn("瑞势结论", script)
        self.assertIn("fa-thin.fa-chart-candlestick", brand_stylesheet)
        self.assertNotIn("fontawesome", text.lower())
        self.assertIn(".workspace-domain", navigation_stylesheet)
        self.assertIn("min-height: 44px", controls_stylesheet)
        self.assertIn(":focus-visible", controls_stylesheet)
        self.assertIn("macrostructure: Workbench", review_stylesheet)
        self.assertIn(".review-theme-track", review_stylesheet)
        self.assertIn(".review-downside-strip", review_stylesheet)
        self.assertIn(".review-loop-nav", review_stylesheet)
        self.assertIn("renderIndexRecap", review_script)
        self.assertIn('aria-label="指数复盘"', review_script)
        self.assertIn("review-index-recap-grid", review_stylesheet)
        self.assertIn("/static/board-dynamics.js", text)
        self.assertIn('id="sentiment-view-today"', text)
        self.assertIn('id="sentiment-view-rotation"', text)
        self.assertIn('id="sentiment-view-history"', text)
        self.assertIn('/static/shortline-views.js', text)
        self.assertNotIn('review-legacy-topic-evidence', review_script)
        self.assertNotIn('review-v2-archive', review_script)
        self.assertIn('renderSentiment(data)', review_script)
        # P1 and P2 now own their numbering; V2 is not a continuation of P1.
        self.assertIn('review-section-number">02', review_script)
        for section_id in (
            "review-step-conclusion",
            "review-step-facts",
            "review-patterns",
        ):
            self.assertIn(f'id="{section_id}"', review_script)
        self.assertIn('id="review-step-overseas"', text)
        self.assertIn('tabId === "market" ? "review" : tabId', script)
        self.assertIn('/api/v1/market/permission', script)
        self.assertIn("openStockPage", script)
        self.assertIn("stockLogoMarkup", script)
        self.assertIn("baidu-finance.cdn.bcebos.com/imgs/logo/stocks/stock_ab_", script)
        self.assertIn("/api/v1/review/strong-stocks", strong_stock_script)
        self.assertIn("/api/v1/daily-push", script)
        self.assertIn("marketProgress", script)
        self.assertIn("MARKET_READ_TIMEOUT_MS = 180000", script)
        self.assertIn("MARKET_FORCE_TIMEOUT_MS = 360000", script)
        self.assertIn("toggleStockPanelTrendPool", script)
        self.assertIn("stockChartTickLabel", script)
        self.assertIn("renderDashboard(data)", review_script)
        self.assertIn("renderStockAnalysis", script)
        self.assertIn("window.StrongStocks=", strong_stock_script)
        self.assertIn("renderWaveKlineChart", script)
        self.assertIn("renderFuturesProductTable", script)
        self.assertIn("中信净空日增减", script)
        self.assertIn("renderIndexRecap(data)", review_script)
        self.assertNotIn('<span class="insight-number">02</span>', script)
        self.assertIn("查看资金口径与订单规模明细", script)
        self.assertIn("风险硬否决", script)
        self.assertIn("rt.pct_chg * 100", script)  # Price changes retain their explicit display-unit conversion.
        self.assertIn("candidate_source", script)
        self.assertIn("当日强启动", script)
        self.assertIn("trend-line-chart", script)
        self.assertIn("renderCandidateTradePlan", script)
        self.assertIn("candidateDisplayPlan", script)
        self.assertIn("prefillHoldingFromCandidate", script)
        self.assertIn("runHoldingDailyCheck", script)
        self.assertIn("loadHoldingEvents", script)
        self.assertIn("/api/v1/holdings", script)
        self.assertIn("/api/v1/trading/dashboard", script)
        self.assertIn("/api/v1/trading/workbench?limit=30", script)
        self.assertIn("/api/v1/trading/risk-profile", script)
        self.assertIn("/api/v1/trading/orders?limit=50", script)
        self.assertIn("/api/v1/trading/executions?limit=200", script)
        self.assertIn("submitTradingOrder", script)
        self.assertIn("submitTradingOrderFill", script)
        self.assertIn("cancelTradingOrder", script)
        self.assertIn("/api/v1/trading/readiness", script)
        self.assertIn("/api/v1/strategies/", script)
        self.assertIn("/stop-plan", script)
        self.assertIn("previewAndApplyHoldingStop", script)
        self.assertIn("editHoldingLedgerValue", script)
        self.assertIn("manual_current_price", script)
        self.assertIn("recordTradingExecution", script)
        self.assertIn("openTradingExecution", script)
        self.assertIn("renderTradingCockpit", script)
        self.assertIn("renderExecutionWorkbench", script)
        self.assertIn("execution-discovery-details", script)
        self.assertIn("查看 ${genericItems.length} 条状态明细", script)
        self.assertIn("refreshHoldingDerived", script)
        self.assertIn("刷新行情与策略价位", script)
        self.assertIn("markHoldingTargetManual", script)
        self.assertIn("今天先做什么", script)
        self.assertIn("完整选品池", script)
        self.assertIn("candidate-terminal-table", script)
        terminal_stylesheet = (static_root / "trading-terminal.css").read_text(encoding="utf-8")
        self.assertIn("macrostructure: Priority Stack + Scanner Ledger", terminal_stylesheet)
        self.assertIn("candidate-terminal-detail", terminal_stylesheet)
        self.assertIn("空仓候选失效不会再冒充持仓风险", script)
        self.assertIn("数据阻断不冒充", script)
        self.assertIn("风险计算口径", script)
        self.assertIn("板块参考待补齐", script)
        self.assertIn("renderTradingScenarios", script)
        self.assertIn("renderTradingTimeline", script)
        self.assertIn("recordTradingDecision", script)
        self.assertIn("candidate-decision-card", script)
        self.assertIn("setCandidateViewMode", script)
        self.assertIn("renderStockStrategyCard", script)
        self.assertIn("renderStockEvidenceStrip", script)
        self.assertIn("TRADE PLAN", script)
        self.assertIn("触发条件：次日收盘确认", script)
        self.assertIn("预计售出", script)
        self.assertIn("renderIntradayMiniChart", script)
        self.assertIn("renderIntradayDecisionWorkspace", script)
        self.assertIn("真实 ${candidate.minute_bar_interval || 5} 分钟 K", script)
        self.assertIn("selectIntradayCandidate", script)
        self.assertIn("loadSelectionPool", script)
        self.assertIn("scheduleSelectionStockSearch", script)
        self.assertIn("/api/v1/stocks/search", script)
        self.assertIn("scheduleTradingStockSearch", script)
        self.assertIn("handleTradingStockSearchKeydown", script)
        self.assertIn("scenario_id: scenarioId", script)
        self.assertIn("editTradingRiskProfile('cash')", script)
        self.assertNotIn("window.prompt", script)
        self.assertIn("intraday-vwap-line", script)
        self.assertIn("market-live-structure", script)
        self.assertIn("renderIntradayOrderBook", script)
        self.assertIn("renderTradingMarket", script)
        self.assertIn("renderSameMarketSignals", script)
        self.assertIn("prettyChineseJson", script)
        self.assertIn("selectedBenchmarkTicker", script)
        self.assertIn("/api/v1/data/sync", script)
        self.assertIn("runPrecomputeSectorsFromPage", script)
        self.assertIn("/api/v1/data/precompute-sectors", script)
        self.assertIn("marketDataGapMessage", script)
        self.assertIn("renderPatterns(data)", review_script)
        self.assertIn("同步目标日期", script)
        self.assertIn("market-story-layout", stylesheet)
        self.assertIn(".market-feature-stack, .sector-feature-stack { grid-column: 2; }", stylesheet)
        self.assertIn(".card.wide.story-aligned-card { grid-column: 1 / -1; margin-left: 162px; }", stylesheet)
        self.assertIn(".market-storyline, .sector-storyline", stylesheet)
        self.assertIn("stock-detail-page", stylesheet)
        self.assertIn(".sector-storyline.is-docked", stylesheet)
        self.assertIn("market-error-card", stylesheet)
        self.assertIn("intraday-workbench", stylesheet)
        self.assertIn("intraday-candidate-rail", stylesheet)
        self.assertIn("market-decision-workspace", stylesheet)
        self.assertIn("data-tab=\"selection\"", text)
        self.assertIn("knowledge-collapsible", stylesheet)
        self.assertNotIn("行业来源对照", script)
        self.assertIn("Hover-only storyline", stylesheet)
        self.assertIn("scheduleStorylineDocking", script)
        self.assertIn("sv-ai-terminal", script)
        self.assertIn("renderStockAiBrief", script)
        self.assertIn("renderStockQuoteStrip", script)
        self.assertIn("FACTS → EVENTS → OPTIONAL STRATEGY", text)
        self.assertIn(".fs-section-header", stylesheet)
        self.assertIn(".fs-primary-card", stylesheet)
        self.assertIn(".sv-ai-terminal .sv-body", stylesheet)
        self.assertIn(".candidate-view-toolbar", stylesheet)
        self.assertIn(".candidate-decision-list", stylesheet)
        self.assertIn(".candidate-decision-strip", stylesheet)
        self.assertIn(".trading-stock-results", inline_stylesheet)
        self.assertIn('[data-state="loading"]', inline_stylesheet)
        self.assertIn("prefers-reduced-motion", inline_stylesheet)
        self.assertIn(".sv-strategy-card", stylesheet)
        self.assertIn(".sv-trigger-note", stylesheet)
        self.assertIn(".sv-evidence-strip", stylesheet)
        self.assertIn("sv-section-block", stylesheet)
        self.assertIn("signal-events-more", stylesheet)
        self.assertIn(".holding-summary-grid", stylesheet)
        self.assertIn(".holding-decision-row", stylesheet)
        self.assertIn(".holding-event-list", stylesheet)
        self.assertIn(".stock-logo-shell", stylesheet)
        self.assertIn(".holding-stock-logo", stylesheet)
        self.assertIn(".trading-action-hero", stylesheet)
        self.assertIn(".trading-scenario-grid", stylesheet)
        self.assertIn(".trading-market-grid", stylesheet)
        self.assertIn(".trading-timeline-list", stylesheet)
        self.assertIn(".holding-stop-plan-preview", stylesheet)
        self.assertIn(".holding-editable-metric", stylesheet)
        self.assertIn(".holding-risk-pending", stylesheet)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", stylesheet)
        self.assertIn("#tab-holdings .holding-level-strip strong", stylesheet)

        order_stylesheet = (static_root / "trading-order-panel.css").read_text(encoding="utf-8")
        order_preview = (static_root / "trading-order-panel.preview.html").read_text(encoding="utf-8")
        self.assertTrue(order_stylesheet.startswith("/* Hallmark · component: trading order lifecycle panel"))
        for state in ("hover", "focus", "active", "disabled", "loading", "error", "success"):
            self.assertIn(f"is-{state}", order_stylesheet)
            self.assertIn(f">{state}<", order_preview)

        workbench_stylesheet = (static_root / "execution-workbench.css").read_text(encoding="utf-8")
        self.assertTrue(workbench_stylesheet.startswith("/* Hallmark · genre: modern-minimal · macrostructure: Workbench"))
        self.assertIn("/static/execution-workbench.css", text)
        self.assertIn('id="executionWorkbench"', text)
        for state in ("hover", "focus", "active", "disabled", "loading", "error", "success"):
            self.assertIn(f"is-{state}", workbench_stylesheet)

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
