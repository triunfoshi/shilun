let runtimeStatus = {};
    let latestSectorData = null;
    let sectorRequestVersion = 0;
    let selectedSectorIndex = 0;
    let selectedLeaderView = "total";
    let selectedLeaderDate = "";
    let leaderTickerDraft = "";
    let sectorProgressTimer = null;
    let sectorProgressState = { version: 0, phase: "idle", startedAt: 0, phaseStartedAt: 0 };
    let latestMarketData = null;
    let latestAnalysisData = null;
    let selectedMarketChart = "distribution";
    let marketProgressTimer = null;
    let marketProgressState = { phase: "idle", startedAt: 0, progress: 0 };
    const pretty = (value) => JSON.stringify(value, null, 2);
    const chineseJsonLabels = {
      target_date: "同步目标日期", sync_trade_date: "实际同步交易日", start_date: "同步起始日期", end_date: "同步结束日期",
      calendar_start: "交易日历起始日期", calendar_end: "交易日历结束日期", daily_trade_date: "日线探测交易日",
      skipped: "是否跳过", message: "执行说明", stock_basic_count: "股票基础资料条数", trade_calendar_count: "交易日历条数",
      daily_bar_count: "日线记录总数", target_daily_bar_count: "日线记录总数", target_stock_daily_bar_count: "个股日线条数",
      daily_basic_count: "每日基础指标条数", moneyflow_count: "资金流向条数", benchmark_bar_count: "基准指数日线条数",
      failed_trade_dates: "失败交易日", synced_trade_dates: "已同步交易日", skipped_trade_dates: "跳过交易日",
      engine_version: "引擎版本", analysis_date: "分析日期", benchmark_ticker: "基准指数代码", benchmark_name: "基准指数名称",
      benchmark_meta: "基准指数说明", benchmark_options: "可选基准指数", benchmark_statuses: "基准指数覆盖状态",
      selected_benchmark: "当前基准状态", benchmark_ready: "当前基准是否就绪", latest_market_date: "当前基准最新日期",
      mongo_configured: "Mongo 是否配置", mongo_connected: "Mongo 是否连接", mongo_uri: "Mongo 地址", ticker: "证券代码",
      has_target_data: "目标日期是否有数据", latest_date: "最新数据日期", name: "名称", source: "数据来源", meaning: "用途说明",
      market_permission: "大盘权限", permission_label: "权限中文", permission_summary: "权限结论", action_permission: "操作权限",
      can_open: "是否允许开仓", max_new_position: "新增仓位上限", text: "动作说明", total_score: "总分", scores: "维度评分",
      trend_score: "趋势分", volume_score: "量能分", breadth_score: "广度分", theme_score: "主线分", risk_score: "风险分",
      metrics: "指标数据", levels: "支撑压力位", hard_triggers: "硬否决触发项", state_machine: "状态机规则",
      chart_data: "图表数据", theme_method: "主线识别口径", theme_candidates: "主线候选", data_quality: "数据质量",
      index_open: "指数开盘", index_high: "指数最高", index_low: "指数最低", index_close: "指数收盘", index_pct_chg: "指数涨跌幅",
      index_ma5: "指数MA5", index_ma10: "指数MA10", index_ma20: "指数MA20", ma5_slope: "MA5斜率",
      amount: "成交额", amount_prev: "昨日成交额", amount_change_vs_prev: "较昨日成交额变化", amount_ma5: "成交额5日均", amount_ma20: "成交额20日均",
      amount_ratio_5: "成交额相对5日均", amount_ratio_20: "成交额相对20日均", up_count: "上涨家数", down_count: "下跌家数",
      flat_count: "平盘家数", stock_count: "样本股票数", up_ratio: "上涨占比", up_count_ma5: "上涨家数5日均",
      up_count_ratio_ma5: "上涨家数相对5日均", limit_up_count: "涨停家数", limit_down_count: "跌停家数",
      limit_down_count_ma5: "跌停家数5日均", market_amount: "全市场成交额", market_amount_ma5: "全市场成交额5日均",
      market_amount_change_vs_prev: "全市场成交额较昨日变化", market_amount_ratio_ma5: "全市场成交额相对5日均",
      main_theme_status: "主线状态", main_theme_name: "代理主线", main_theme_return: "主线收益", main_theme_up_ratio: "主线上涨占比",
      main_theme_market_share: "主线成交额占比", weight_support_flag: "权重护盘标记", support_1: "第一支撑", support_1_source: "第一支撑依据",
      support_2: "第二支撑", support_2_source: "第二支撑依据", pressure_1: "第一压力", pressure_1_source: "第一压力依据",
      definition: "定义说明", formula: "计算公式", hard_veto: "硬否决规则", states: "状态定义", definition: "定义说明",
      summary: "总结", top_sectors: "今日强力板块", trend_sectors: "趋势板块", daily_leaders: "每日龙头", leader_summary: "总龙头榜",
      indicator_definitions: "指标定义", implementation_status: "实现状态", status: "状态", note: "备注", field: "字段",
      base_url: "网关地址", probe_dates: "探测日期", checks: "检查项", overall_status: "网关状态", overall_label: "网关状态说明",
      ok: "是否通过", count: "返回数量", row_count: "写入数量", cal_date: "日历日期", ts_code: "证券代码",
      recommendation: "处理建议", data_source: "数据来源", pushed_channels: "已推送通道", message_text: "消息内容"
    };
    const chineseJsonValues = {
      success: "成功", partial_error: "部分失败", error: "失败", skipped: "已跳过", available: "可用", unavailable: "不可用", ok: "正常",
      implemented: "已接入", proxy_only: "仅代理指标", data_pending: "待接入数据", manual_only: "仅人工维护",
      attack: "进攻", hold: "持有/观察", defense: "防守", empty: "空仓", yes: "是", no: "否",
      watch_only: "仅观察", no_heavy_new_position: "不允许新增重仓", confirmed_proxy: "确认代理主线",
      candidate_proxy: "候选代理主线", local_hotspot_proxy: "局部热点代理", weight_support_proxy: "权重护盘代理",
      moneyflow_data_pending: "资金流向待接入", index_daily: "指数日线", stock_basic: "股票基础资料", daily_basic: "每日基础指标",
      gateway_http: "网关 HTTP 探测", sdk_probe: "接口探测", trade_cal: "交易日历", daily: "日线数据"
    };
    const chineseJsonTokens = {
      target: "目标", date: "日期", sync: "同步", trade: "交易", stock: "股票", daily: "日线", basic: "基础",
      benchmark: "基准", market: "市场", index: "指数", bar: "记录", bars: "记录", count: "数量", latest: "最新",
      status: "状态", score: "评分", trend: "趋势", volume: "量能", breadth: "广度", theme: "主线", risk: "风险",
      moneyflow: "资金流向", sector: "板块", leader: "龙头", analysis: "分析", data: "数据", quality: "质量",
      source: "来源", amount: "成交额", ratio: "占比", return: "收益", support: "支撑", pressure: "压力",
      permission: "权限", state: "状态", machine: "机", trigger: "触发", config: "配置", connected: "连接"
    };
    function chineseJsonLabel(key) {
      if (chineseJsonLabels[key]) return chineseJsonLabels[key];
      const tokens = String(key).split("_");
      const translated = tokens.map((token) => chineseJsonTokens[token] || token);
      return translated.join("");
    }
    function chineseJsonValue(value) {
      if (typeof value !== "string") return value;
      if (chineseJsonValues[value]) return chineseJsonValues[value];
      if (/^[a-z][a-z0-9_]*$/.test(value) && value.includes("_")) {
        return value.split("_").map((token) => chineseJsonTokens[token] || token).join(" ");
      }
      return value;
    }
    function localizeJson(value) {
      if (Array.isArray(value)) return value.map(localizeJson);
      if (value && typeof value === "object") {
        return Object.fromEntries(Object.entries(value).map(([key, item]) => [chineseJsonLabel(key), localizeJson(item)]));
      }
      if (typeof value === "boolean") return value ? "是" : "否";
      if (value === null) return "暂无";
      return chineseJsonValue(value);
    }
    function prettyChineseJson(value) {
      return JSON.stringify(localizeJson(value), null, 2);
    }
    function localizeJsonText(value) {
      if (typeof value !== "string") return prettyChineseJson(value);
      try { return prettyChineseJson(JSON.parse(value)); } catch { return value; }
    }
    const formatError = (error, context = "generic") => {
      if (typeof error === "string") return error;
      if (error && error.name === "AbortError") {
        const timeoutMessages = {
          analysis: "请求超时：单票分析可能正在等待 Tushare 或 Mongo。建议确认 Mongo 已同步该日期数据。",
          sectors: "板块计算超时：主结果未在限定时间内完成，请重试或检查后台负载。",
          leaders: "近30个交易日龙头榜仍在计算，板块主结果不受影响。可稍后单独重试。",
        };
        return timeoutMessages[context] || "请求超时：服务未在限定时间内返回，请稍后重试。";
      }
      if (error && error.detail) return `请求失败：${error.detail}`;
      if (error && error.message) return `请求失败：${error.message}`;
      try {
        const rendered = JSON.stringify(error, null, 2);
        return rendered && rendered !== "{}" ? rendered : "请求失败：浏览器没有返回具体错误。请检查服务终端日志。";
      } catch {
        return "请求失败：未知错误。请检查服务终端日志。";
      }
    };
    const show = (id, value) => { document.getElementById(id).textContent = localizeJsonText(value); };
    const showHtml = (id, value) => {
      document.getElementById(id).innerHTML = value;
      scheduleStorylineDocking();
    };
    const formatWaitDuration = (seconds) => {
      const safe = Math.max(0, Math.round(Number(seconds) || 0));
      if (safe < 60) return `${safe} 秒`;
      const minutes = Math.floor(safe / 60);
      const rest = safe % 60;
      return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
    };
    function renderMarketProgressTick() {
      const root = document.getElementById("marketProgress");
      if (!root || marketProgressState.phase === "idle") return;
      const elapsed = Math.max(0, (Date.now() - marketProgressState.startedAt) / 1000);
      let progress = Math.min(94, 6 + (elapsed / 12) * 88);
      let label = elapsed < 3 ? "阶段 1/3：读取指数与全市场日线" : elapsed < 8 ? "阶段 2/3：计算趋势、广度、量能和主线" : "阶段 3/3：生成状态机结论与图表";
      let remaining = `预计剩余约 ${formatWaitDuration(Math.max(2, 12 - elapsed))}`;
      if (marketProgressState.phase === "complete") {
        progress = 100;
        label = "大盘计算与图表生成完成";
        remaining = "已完成，无需等待";
      } else if (marketProgressState.phase === "error") {
        progress = Math.max(6, marketProgressState.progress || 6);
        label = "大盘计算未完成";
        remaining = "已停止，请按错误提示处理后重试";
      }
      root.hidden = false;
      root.classList.toggle("is-complete", marketProgressState.phase === "complete");
      root.classList.toggle("is-error", marketProgressState.phase === "error");
      document.getElementById("marketProgressLabel").textContent = label;
      document.getElementById("marketProgressPercent").textContent = `${Math.round(progress)}%`;
      document.getElementById("marketProgressBar").style.width = `${progress}%`;
      document.getElementById("marketProgressElapsed").textContent = `已等待 ${formatWaitDuration(elapsed)}`;
      document.getElementById("marketProgressRemaining").textContent = remaining;
      marketProgressState.progress = progress;
    }
    function setMarketButtons(disabled, label) {
      ["marketQueryButton", "permissionQueryButton"].forEach((id) => {
        const button = document.getElementById(id);
        if (!button) return;
        button.disabled = disabled;
        button.textContent = label;
      });
    }
    function startMarketProgress() {
      if (marketProgressTimer) clearInterval(marketProgressTimer);
      marketProgressState = { phase: "running", startedAt: Date.now(), progress: 6 };
      setMarketButtons(true, "正在计算大盘...");
      renderMarketProgressTick();
      marketProgressTimer = setInterval(renderMarketProgressTick, 500);
    }
    function finishMarketProgress(success) {
      if (marketProgressTimer) clearInterval(marketProgressTimer);
      marketProgressTimer = null;
      marketProgressState.phase = success ? "complete" : "error";
      setMarketButtons(false, success ? "重新计算大盘" : "重新计算大盘");
      renderMarketProgressTick();
    }
    function renderSectorProgressTick() {
      const root = document.getElementById("sectorProgress");
      if (!root || sectorProgressState.phase === "idle") return;
      const now = Date.now();
      const elapsed = Math.max(0, (now - sectorProgressState.startedAt) / 1000);
      const phaseElapsed = Math.max(0, (now - sectorProgressState.phaseStartedAt) / 1000);
      let label = "准备计算";
      let progress = 0;
      let remainingText = "预计完整结果剩余约 65 秒";
      if (sectorProgressState.phase === "main") {
        label = "阶段 1/2：计算强力板块与趋势榜（估算进度）";
        progress = Math.min(68, 5 + (phaseElapsed / 34) * 63);
        const remaining = Math.max(5, 34 - phaseElapsed) + 32;
        remainingText = `预计完整结果剩余约 ${formatWaitDuration(remaining)}`;
      } else if (sectorProgressState.phase === "leaders") {
        label = "阶段 2/2：生成近 30 个交易日龙头榜（估算进度）";
        progress = Math.min(98, 70 + (phaseElapsed / 32) * 28);
        const remaining = Math.max(2, 32 - phaseElapsed);
        remainingText = `预计龙头榜剩余约 ${formatWaitDuration(remaining)}`;
      } else if (sectorProgressState.phase === "complete") {
        label = "板块动向与龙头榜均已完成";
        progress = 100;
        remainingText = "已完成，无需等待";
      } else if (sectorProgressState.phase === "partial_error") {
        label = "板块主结果已完成，龙头榜未能完成";
        progress = 70;
        remainingText = "已停止，可单独重试龙头榜";
      } else if (sectorProgressState.phase === "error") {
        label = "板块动向计算失败";
        progress = Math.max(5, sectorProgressState.progress || 5);
        remainingText = "已停止，请按错误提示处理后重试";
      }
      root.hidden = false;
      root.classList.toggle("is-complete", sectorProgressState.phase === "complete");
      root.classList.toggle("is-error", sectorProgressState.phase === "error" || sectorProgressState.phase === "partial_error");
      document.getElementById("sectorProgressLabel").textContent = label;
      document.getElementById("sectorProgressPercent").textContent = `${Math.round(progress)}%`;
      document.getElementById("sectorProgressBar").style.width = `${progress}%`;
      document.getElementById("sectorProgressElapsed").textContent = `已等待 ${formatWaitDuration(elapsed)}`;
      document.getElementById("sectorProgressRemaining").textContent = remainingText;
      sectorProgressState.progress = progress;
    }
    function startSectorProgress(version) {
      if (sectorProgressTimer) clearInterval(sectorProgressTimer);
      const now = Date.now();
      sectorProgressState = { version, phase: "main", startedAt: now, phaseStartedAt: now, progress: 5 };
      const button = document.getElementById("sectorQueryButton");
      if (button) {
        button.disabled = true;
        button.textContent = "正在计算主结果...";
      }
      renderSectorProgressTick();
      sectorProgressTimer = setInterval(renderSectorProgressTick, 1000);
    }
    function advanceSectorProgressToLeaders(version) {
      if (sectorProgressState.version !== version) return;
      sectorProgressState.phase = "leaders";
      sectorProgressState.phaseStartedAt = Date.now();
      const button = document.getElementById("sectorQueryButton");
      if (button) {
        button.disabled = false;
        button.textContent = "重新查询板块动向";
      }
      renderSectorProgressTick();
    }
    function completeSectorProgress(version) {
      if (sectorProgressState.version !== version) return;
      if (sectorProgressTimer) clearInterval(sectorProgressTimer);
      sectorProgressTimer = null;
      sectorProgressState.phase = "complete";
      const button = document.getElementById("sectorQueryButton");
      if (button) {
        button.disabled = false;
        button.textContent = "查询板块动向";
      }
      renderSectorProgressTick();
    }
    function failSectorProgress(version, partial = false) {
      if (sectorProgressState.version !== version) return;
      if (sectorProgressTimer) clearInterval(sectorProgressTimer);
      sectorProgressTimer = null;
      sectorProgressState.phase = partial ? "partial_error" : "error";
      const button = document.getElementById("sectorQueryButton");
      if (button) {
        button.disabled = false;
        button.textContent = "重新查询板块动向";
      }
      renderSectorProgressTick();
    }
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[char]));
    function stockLink(name, ticker, className = "") {
      if (!ticker) return escapeHtml(name || "-");
      return `<button type="button" class="stock-link ${escapeHtml(className)}" data-ticker="${escapeHtml(ticker)}" onclick="openStockPage(this.dataset.ticker)">${escapeHtml(name || ticker)} <span>${escapeHtml(ticker)}</span></button>`;
    }
    function openStockPage(ticker) {
      const normalized = String(ticker || "").trim().toUpperCase();
      if (!normalized) return;
      document.getElementById("analyzeTicker").value = normalized;
      switchTab("analysis");
      document.getElementById("tab-analysis").scrollIntoView({ behavior: "smooth", block: "start" });
      runAnalyze();
    }
    function scrollMarketFeature(id) {
      const node = document.getElementById(id);
      if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
      document.querySelectorAll(".market-story-link").forEach((button) => {
        button.classList.toggle("active", button.dataset.target === id);
      });
    }
    const statusCell = (label, ok, detail) => `<div class="pill"><strong>${label}</strong><span class="${ok ? "ok" : "bad"}">${ok ? "已配置" : "未配置"}</span><small>${detail || ""}</small></div>`;
    const activeTabId = () => document.querySelector(".tab-button.active")?.dataset.tab || "permission";
    const selectedBenchmarkTicker = () => document.getElementById("globalBenchmark").value;
    const selectedMarketDate = () => document.getElementById("globalDate").value;
    let storylineDockFrame = 0;
    function updateStorylineDocking() {
      const topbarBottom = document.querySelector(".topbar")?.getBoundingClientRect().bottom || 92;
      // The top bar can scroll out of view, so retain a stable dock line for the side navigation.
      const dockTop = Math.max(104, Math.round(topbarBottom + 12));
      document.querySelectorAll(".market-storyline, .sector-storyline").forEach((storyline) => {
        const resultRoot = storyline.closest(".market-output");
        const panel = storyline.closest(".tab-panel");
        const candidateCard = storyline.classList.contains("sector-storyline") ? document.getElementById("candidatesCard") : null;
        const resultRect = resultRoot?.getBoundingClientRect();
        const candidateRect = candidateCard && getComputedStyle(candidateCard).display !== "none"
          ? candidateCard.getBoundingClientRect()
          : null;
        const lowerBound = Math.max(resultRect?.bottom || 0, candidateRect?.bottom || 0);
        const shouldDock = Boolean(
          panel?.classList.contains("active")
          && resultRect
          && resultRect.top <= dockTop
          && lowerBound > dockTop + 96
        );
        storyline.classList.toggle("is-docked", shouldDock);
      });
    }
    function scheduleStorylineDocking() {
      if (storylineDockFrame) cancelAnimationFrame(storylineDockFrame);
      storylineDockFrame = requestAnimationFrame(() => {
        storylineDockFrame = 0;
        updateStorylineDocking();
      });
    }
    function syncBenchmarkSelects(value) {
      if (!value) return;
      document.getElementById("globalBenchmark").value = value;
    }
    function syncMarketDates(value) {
      if (!value) return;
      document.getElementById("globalDate").value = value;
    }

    function syntaxHighlightJson(value) {
      return prettyChineseJson(value).replace(/("(\u[a-zA-Z0-9]{4}|\[^u]|[^\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, (match) => {
        const safe = escapeHtml(match);
        if (/^"/.test(match)) {
          return /:$/.test(match) ? `<span class="json-key">${safe}</span>` : `<span class="json-string">${safe}</span>`;
        }
        if (/true|false/.test(match)) return `<span class="json-boolean">${safe}</span>`;
        if (/null/.test(match)) return `<span class="json-null">${safe}</span>`;
        return `<span class="json-number">${safe}</span>`;
      });
    }

    const tabPresentation = {
      permission: { title: "权限确认", subtitle: "交易前先确认风险边界", kicker: "研究主流程", description: "先确认今天可做什么、不能做什么，再进入大盘和板块推演。" },
      market: { title: "大盘计算", subtitle: "PART1 · 日线市场状态机", kicker: "市场研究", description: "用趋势、量能、广度、主线和风险，形成可执行的大盘权限。" },
      sectors: { title: "板块动向", subtitle: "PART2 · 强弱、趋势与龙头", kicker: "市场研究", description: "从板块扩散、资金口径和龙头/中军候选中识别市场主线。" },
      analysis: { title: "单票分析", subtitle: "个股结构与策略边界", kicker: "个股研究", description: "输入股票代码，查看行情、结构、支撑压力和执行边界。" },
      push: { title: "日报推送", subtitle: "研究结论交付", kicker: "系统与交付", description: "先预览，再将结构化日报发送到已配置通道。" },
      data: { title: "数据同步", subtitle: "Mongo 数据水位与网关", kicker: "系统与交付", description: "确认交易日数据、增量缺口与 Tushare 网关健康状态。" },
      system: { title: "系统状态", subtitle: "运行依赖检查", kicker: "系统与交付", description: "集中检查 Mongo、消息通道和运行时依赖。" },
    };
    function switchTab(tabId) {
      document.querySelectorAll(".tab-button").forEach((button) => {
        button.classList.toggle("active", button.dataset.tab === tabId);
      });
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.classList.toggle("active", panel.id === `tab-${tabId}`);
      });
      const view = tabPresentation[tabId] || tabPresentation.permission;
      document.getElementById("pageTitle").textContent = view.title;
      document.getElementById("pageSubtitle").textContent = view.subtitle;
      document.getElementById("pageKicker").textContent = view.kicker;
      document.getElementById("pageHeading").textContent = view.title;
      document.getElementById("pageDescription").textContent = view.description;
      scheduleStorylineDocking();
    }
    function refreshCurrentTab() {
      const tabId = activeTabId();
      if (tabId === "permission" || tabId === "market") return runMarketPermission();
      if (tabId === "sectors") return runSectorTrends();
      if (tabId === "analysis") return runAnalyze();
      if (tabId === "data") {
        loadDataStatus();
        return loadTushareHealth();
      }
      if (tabId === "system") return loadStatus();
      if (tabId === "push") return undefined;
    }

    function permissionClass(permission) {
      if (permission === "attack") return "ok";
      if (permission === "empty") return "bad";
      if (permission === "defense") return "bad";
      return "";
    }

    function renderPermissionSummary(data) {
      const cls = permissionClass(data.market_permission);
      document.getElementById("marketPermissionSummary").innerHTML = [
        `<div class="decision-card"><strong>权限状态</strong><span class="${cls}">${data.permission_label}</span></div>`,
        `<div class="decision-card"><strong>总分</strong><span>${data.total_score}</span></div>`,
        `<div class="decision-card"><strong>风险分</strong><span>${data.scores && data.scores.risk_score}</span></div>`,
        `<div class="decision-card"><strong>动作边界</strong><span>${data.action_permission && data.action_permission.max_new_position}</span></div>`
      ].join("");
      const lines = [
        `权限：${data.market_permission} / ${data.permission_label}`,
        `结论：${(data.interpretation && data.interpretation.headline) || data.permission_summary}`,
        `动作：${data.action_permission && data.action_permission.text}`,
        `总分：${data.total_score}`,
        `五维：趋势 ${data.scores.trend_score}，量能 ${data.scores.volume_score}，广度 ${data.scores.breadth_score}，主线 ${data.scores.theme_score}，风险 ${data.scores.risk_score}`,
        "",
        "核心判断：",
        ...((data.interpretation && data.interpretation.sections) || []).map((section) => `- ${section.title}：${section.conclusion}`),
        "",
        "关键证据：",
        ...(data.evidence || []).slice(0, 5).map((item) => `- ${item}`)
      ].join("\n");
      show("marketPermissionConfirmResult", lines);
    }

    function setMarketChart(view) {
      selectedMarketChart = view;
      if (latestMarketData) renderMarketPermissionDetail(latestMarketData);
    }

    function renderMarketDistribution(chartData) {
      const rows = chartData.return_distribution || [];
      const maxCount = Math.max(1, ...rows.map((item) => Number(item.count || 0)));
      return `
        <div class="market-bar-chart" aria-label="当日个股涨跌分布柱状图">
          ${rows.map((item) => {
            const height = 12 + (Number(item.count || 0) / maxCount) * 78;
            return `
              <div class="market-bar-column">
                <span class="market-bar-value">${escapeHtml(item.count || 0)}</span>
                <span class="market-bar ${item.direction === "up" ? "is-up" : "is-down"}" style="height:${height}%"></span>
                <span class="market-bar-label">${escapeHtml(item.label)}</span>
              </div>
            `;
          }).join("")}
        </div>
      `;
    }

    function renderDualMarketLine(rows, config) {
      if (!rows.length) return '<p class="section-conclusion">暂无趋势序列。</p>';
      const primaryValues = rows.map((row) => Number(row[config.primaryKey] || 0));
      const secondaryValues = rows.map((row) => Number(row[config.secondaryKey] || 0));
      const bounds = (values) => {
        let min = Math.min(...values);
        let max = Math.max(...values);
        if (max - min < 0.001) { min -= 0.001; max += 0.001; }
        return { min, max };
      };
      const primaryBounds = bounds(primaryValues);
      const secondaryBounds = bounds(secondaryValues);
      const points = rows.map((row, index) => {
        const x = rows.length === 1 ? 50 : 5 + (index * 90) / (rows.length - 1);
        const primary = Number(row[config.primaryKey] || 0);
        const secondary = Number(row[config.secondaryKey] || 0);
        const y1 = 12 + ((primaryBounds.max - primary) / (primaryBounds.max - primaryBounds.min)) * 68;
        const y2 = 12 + ((secondaryBounds.max - secondary) / (secondaryBounds.max - secondaryBounds.min)) * 68;
        return { row, x, y1, y2, primary, secondary, index };
      });
      return `
        <div class="market-line-chart">
          <div class="market-chart-legend"><span class="legend-primary">${escapeHtml(config.primaryLabel)}</span><span class="legend-secondary">${escapeHtml(config.secondaryLabel)}</span></div>
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            <line class="market-grid-line" x1="4" y1="80" x2="96" y2="80"></line>
            <line class="market-grid-line" x1="4" y1="46" x2="96" y2="46"></line>
            <line class="market-grid-line" x1="4" y1="12" x2="96" y2="12"></line>
            <polyline class="market-line-primary" points="${points.map((item) => `${item.x},${item.y1}`).join(" ")}"></polyline>
            <polyline class="market-line-secondary" points="${points.map((item) => `${item.x},${item.y2}`).join(" ")}"></polyline>
          </svg>
          ${points.map((item) => `
            <button type="button" class="market-chart-point" style="left:${item.x}%;top:${item.y1}%" aria-label="${escapeHtml(item.row.date)} 数据详情">
              <span class="market-chart-tooltip">
                <strong>${escapeHtml(item.row.date)}</strong>
                <span>${escapeHtml(config.primaryLabel)}</span><b>${escapeHtml(config.primaryFormat(item.primary))}</b>
                <span>${escapeHtml(config.secondaryLabel)}</span><b>${escapeHtml(config.secondaryFormat(item.secondary))}</b>
              </span>
            </button>
          `).join("")}
          <span class="market-chart-start">${escapeHtml(String(rows[0].date || "").slice(5))}</span>
          <span class="market-chart-end">${escapeHtml(String(rows[rows.length - 1].date || "").slice(5))}</span>
        </div>
      `;
    }

    function renderMarketChart(data) {
      const chartData = data.chart_data || {};
      const breadthMap = new Map((chartData.breadth_series || []).map((item) => [item.date, item]));
      const rows = (chartData.benchmark_series || []).map((item) => ({ ...item, ...(breadthMap.get(item.date) || {}) }));
      if (selectedMarketChart === "trend") {
        return renderDualMarketLine(rows, {
          primaryKey: "normalized_close",
          secondaryKey: "up_ratio",
          primaryLabel: "指数归一值",
          secondaryLabel: "上涨占比",
          primaryFormat: (value) => Number(value).toFixed(2),
          secondaryFormat: formatPercent,
        });
      }
      if (selectedMarketChart === "amount") {
        return renderDualMarketLine(rows, {
          primaryKey: "amount_ratio_5",
          secondaryKey: "market_amount_ratio_ma5",
          primaryLabel: "指数成交 / 5日均",
          secondaryLabel: "全市场成交 / 5日均",
          primaryFormat: formatRatio,
          secondaryFormat: formatRatio,
        });
      }
      return renderMarketDistribution(chartData);
    }

    function renderMarketDashboard(data) {
      const chartData = data.chart_data || {};
      const metrics = data.metrics || {};
      const temperature = chartData.temperature || {};
      const tabs = [
        ["distribution", "涨跌分布"],
        ["trend", "指数与广度"],
        ["amount", "成交温度"],
      ];
      return `
        <section id="market-visuals" class="market-feature market-visual-feature">
          <div class="feature-kicker">Market Evidence · Daily</div>
          <div class="feature-header">
            <div><h3>市场结构图</h3><p>${escapeHtml(chartData.frequency_note || "基于日线数据展示市场结构。")}</p></div>
          </div>
          <div class="market-dashboard-grid">
            <aside class="market-daily-facts">
              <div><span>上涨 / 下跌</span><strong>${escapeHtml(metrics.up_count || 0)} / ${escapeHtml(metrics.down_count || 0)}</strong></div>
              <div><span>涨停 / 跌停</span><strong>${escapeHtml(metrics.limit_up_count || 0)} / ${escapeHtml(metrics.limit_down_count || 0)}</strong></div>
              <div><span>全市场成交</span><strong>${escapeHtml(formatTradeAmount(metrics.market_amount))}</strong></div>
            </aside>
            <div class="market-chart-stage">
              <div class="market-chart-tabs" role="tablist" aria-label="大盘图表视图">
                ${tabs.map(([value, label]) => `<button class="market-chart-tab ${selectedMarketChart === value ? "is-active" : ""}" onclick="setMarketChart('${value}')">${label}</button>`).join("")}
              </div>
              ${renderMarketChart(data)}
            </div>
            <aside class="market-temperature" style="--temperature:${Math.max(0, Math.min(100, Number(temperature.score || 0)))}%">
              <span>综合温度</span>
              <div class="temperature-ring"><strong>${escapeHtml(temperature.score ?? "-")}</strong><small>/ 100</small></div>
              <p>${escapeHtml(temperature.label || "等待计算")}</p>
            </aside>
          </div>
        </section>
      `;
    }

    // 把 "收盘 4120.28，+0.23%" 这种文本拆成 [{label,value}] 数组
    function parseInsightValue(value) {
      const text = String(value || "").trim();
      if (!text) return { primary: "", chips: [] };
      // 用中英文逗号、顿号、斜杠、空格"，"作分隔
      const parts = text.split(/[，,、]\s*/).filter(Boolean);
      if (parts.length <= 1) return { primary: text, chips: [] };
      // 第一段当主数字，后面拆 chip
      return { primary: parts[0], chips: parts.slice(1) };
    }

    function renderMarketInsight(section, index) {
      const number = String(index + 1).padStart(2, "0");
      const isPatternSection = String(section.title || "").includes("今日形态") || String(section.title || "").includes("波浪结构");
      // 从 section row[0].value 里解析 signal_type（格式：信号性质：bullish）
      const rawSignalType = isPatternSection && section.rows && section.rows[0]
        ? String(section.rows[0].value || "").replace("信号性质：", "").trim()
        : "";
      const patternBadgeCls = { bullish: "pattern-bullish", bearish: "pattern-bearish", warning: "pattern-warning", neutral: "pattern-neutral" }[rawSignalType] || "pattern-neutral";
      const patternLabel = { bullish: "看多", bearish: "看空", warning: "预警", neutral: "中性" }[rawSignalType] || rawSignalType;

      return `
        <section id="market-insight-${index + 1}" class="market-feature market-insight-section${isPatternSection ? " pattern-forecast-section" : ""}">
          <div class="insight-heading">
            <span class="insight-number">${number}</span>
            <div>
              <div class="feature-kicker">${isPatternSection ? "Pattern Forecast" : "Evidence Block"}</div>
              <h3>${escapeHtml(String(section.title || "").replace(/^\d+\.\s*/, ""))}${isPatternSection && rawSignalType ? ` <span class="pattern-signal-badge ${patternBadgeCls}">${escapeHtml(patternLabel)}</span>` : ""}</h3>
            </div>
          </div>
          <p class="insight-conclusion">${escapeHtml(section.conclusion)}</p>
          <div class="insight-row-grid">
            ${(section.rows || []).map((row, i) => {
              const indicator = String(row.indicator || "");

              // 波浪结构主卡：宽幅叙述卡
              if (isPatternSection && indicator.includes("🌊")) {
                return `
                  <article class="insight-row-card insight-card-narrative wave-headline-card" style="grid-column: 1 / -1;">
                    <span class="metric-label">${escapeHtml(indicator)}</span>
                    <strong class="value-emphasis">${escapeHtml(String(row.value||"").split("，")[0])}</strong>
                    <div class="value-chips">${String(row.value||"").split("，").slice(1).map((c) => `<span class="value-chip">${escapeHtml(c.trim())}</span>`).join("")}</div>
                    <p>${escapeHtml(row.judgement)}</p>
                  </article>`;
              }

              // 端点序列：宽幅长条卡
              if (isPatternSection && indicator.includes("📍")) {
                return `
                  <article class="insight-row-card wave-pivots-card" style="grid-column: 1 / -1;">
                    <span class="metric-label">${escapeHtml(indicator)}</span>
                    <div class="wave-pivots-flow">${escapeHtml(row.value).replace(/→/g, '<span class="pivots-arrow">→</span>')}</div>
                    <p>${escapeHtml(row.judgement)}</p>
                  </article>`;
              }

              // 规则违反：橙色警告
              if (isPatternSection && indicator.includes("⚠️")) {
                return `
                  <article class="insight-row-card wave-violation-card" style="grid-column: 1 / -1;">
                    <span class="metric-label">${escapeHtml(indicator)}</span>
                    <strong class="value-emphasis">${escapeHtml(row.value)}</strong>
                    <p>${escapeHtml(row.judgement)}</p>
                  </article>`;
              }

              // K 线短期形态卡（保留原 narrative）
              if (isPatternSection && indicator.includes("🕯")) {
                return `
                  <article class="insight-row-card insight-card-narrative">
                    <span class="metric-label">${escapeHtml(indicator)}</span>
                    <strong class="value-emphasis">${escapeHtml(String(row.value||"").replace("信号性质：","").trim())}</strong>
                    <p>${escapeHtml(row.judgement)}</p>
                  </article>`;
              }

              // 数据缺口卡（pattern section 末尾）
              if (isPatternSection && indicator.includes("📋")) {
                return `
                  <article class="insight-row-card wave-gap-card" style="grid-column: 1 / -1;">
                    <span class="metric-label">${escapeHtml(indicator)}</span>
                    <strong class="value-emphasis" style="font-size: 14px;">${escapeHtml(row.value)}</strong>
                    <p>${escapeHtml(row.judgement)}</p>
                  </article>`;
              }

              // 支撑/压力/目标位：用颜色边框
              const isSupportRow = indicator.startsWith("支撑");
              const isResistRow = indicator.startsWith("压力");
              const isTargetRow = indicator.startsWith("目标");

              const { primary, chips } = parseInsightValue(row.value);
              const chipCls = (c) => {
                if (/[+]\s*\d/.test(c) || /上涨\s*\d/.test(c) || /放大/.test(c)) return " trend-up";
                if (/[-−]\s*\d/.test(c) || /下跌\s*\d/.test(c) || /缩量/.test(c)) return " trend-down";
                return "";
              };
              const chipsHtml = chips.length
                ? `<div class="value-chips">${chips.map((c) => `<span class="value-chip${chipCls(c)}">${escapeHtml(c)}</span>`).join("")}</div>`
                : "";
              const klass = [
                "insight-row-card",
                isSupportRow ? "level-support" : "",
                isResistRow ? "level-resistance" : "",
                isTargetRow ? "level-target" : "",
              ].filter(Boolean).join(" ");
              return `
              <article class="${klass}">
                <span class="metric-label">${escapeHtml(indicator)}</span>
                <strong class="value-emphasis">${escapeHtml(primary)}</strong>
                ${chipsHtml}
                <p>${escapeHtml(row.judgement)}</p>
              </article>`;
            }).join("")}
          </div>
        </section>
      `;
    }

    function compactMarketStoryLabel(title) {
      const value = String(title || "").replace(/^\d+\.\s*/, "");
      const labels = {
        "今日形态 + 明日关键点位": "波浪结构",
        "波浪结构 + 明日关键位": "波浪结构",
        "指数状态": "指数状态",
        "市场广度和成交额": "广度与成交",
        "主要板块和主线质量": "主线质量",
        "状态机结论": "状态机结论",
      };
      return labels[value] || value;
    }

    function renderInterpretation(data) {
      const interpretation = data.interpretation || {};
      const sections = interpretation.sections || [];
      const rawPayload = {
        scores: data.scores,
        metrics: data.metrics,
        levels: data.levels,
        chart_data: data.chart_data,
        theme_method: data.theme_method,
        theme_candidates: data.theme_candidates,
        hard_triggers: data.hard_triggers,
        state_machine: data.state_machine,
        data_quality: data.data_quality
      };
      const scorecards = (interpretation.scorecard || []).map((item) => `
        <div class="scorecard-item">
          <strong>${escapeHtml(item.dimension)}</strong>
          <span>${escapeHtml(item.score)}</span>
          <small>${escapeHtml(item.standard)}</small>
        </div>
      `).join("");
      return `
        <section class="market-story-layout">
          <aside class="market-storyline" aria-label="大盘解读故事线">
            <div class="story-title">PART1</div>
            <button class="market-story-link active" data-target="market-overview" onclick="scrollMarketFeature('market-overview')"><span>00</span><b class="story-link-label">今日结论</b></button>
            <button class="market-story-link" data-target="market-visuals" onclick="scrollMarketFeature('market-visuals')"><span>图</span><b class="story-link-label">结构图</b></button>
            ${sections.map((section, index) => `<button class="market-story-link" data-target="market-insight-${index + 1}" onclick="scrollMarketFeature('market-insight-${index + 1}')"><span>${String(index + 1).padStart(2, "0")}</span><b class="story-link-label">${escapeHtml(compactMarketStoryLabel(section.title))}</b></button>`).join("")}
          </aside>
          <div class="market-feature-stack">
            <section id="market-overview" class="market-hero-card part1-hero">
              <div class="market-eyebrow">${escapeHtml(data.benchmark_name || data.benchmark_ticker)} · ${escapeHtml(data.benchmark_ticker)}</div>
              <h2 class="market-headline part1-headline">${escapeHtml(interpretation.headline || data.permission_summary)}</h2>
              <p class="market-subtitle">${escapeHtml(data.action_permission && data.action_permission.text)}</p>
              <div class="market-meta">
                <span class="meta-chip">权限 ${escapeHtml(data.permission_label)}</span>
                <span class="meta-chip">总分 ${escapeHtml(data.total_score)}</span>
                <span class="meta-chip">趋势 ${escapeHtml(data.scores.trend_score)} · 量能 ${escapeHtml(data.scores.volume_score)} · 广度 ${escapeHtml(data.scores.breadth_score)} · 主线 ${escapeHtml(data.scores.theme_score)} · 风险 ${escapeHtml(data.scores.risk_score)}</span>
                ${data._from_cache ? `<span class="meta-chip cache-chip" title="缓存于 ${escapeHtml(data._cached_at || "")}">读取缓存</span>` : `<span class="meta-chip fresh-chip">实时计算</span>`}
              </div>
            </section>
            ${renderMarketDashboard(data)}
            ${sections.map(renderMarketInsight).join("")}
            <section class="market-feature scorecard-feature">
              <div class="feature-kicker">Decision Rules</div>
              <h3>评分卡和判断标准</h3>
              <p class="section-conclusion">每个分数都来自明确指标，避免只看一句“防守/进攻”而不知道原因。</p>
              <div class="scorecard-grid">${scorecards}</div>
            </section>
            <details class="json-viewer">
              <summary>查看中文字段明细（结构化数据）</summary>
              <pre class="json-code">${syntaxHighlightJson(rawPayload)}</pre>
            </details>
          </div>
        </section>
      `;
    }

    function renderMarketPermissionDetail(data) {
      showHtml("marketPermissionResult", renderInterpretation(data));
    }

    function definitionBubble(text) {
      return `
        <details class="info-popover">
          <summary aria-label="定义说明">!</summary>
          <div class="info-panel">${escapeHtml(text)}<a href="#sector-indicator-definitions">查看指标口径</a></div>
        </details>
      `;
    }
    function reasonList(items) {
      return `<ol class="reason-list">${items.map((item) => `<li>${item}</li>`).join("")}</ol>`;
    }
    function scoreRef(label, value, definition) {
      return `
        <span class="score-ref">
          ${escapeHtml(label)} ${escapeHtml(value ?? "-")}
          ${definitionBubble(definition)}
        </span>
      `;
    }
    function metricBlock(label, definition, value, explanation) {
      return `
        <div class="metric-row">
          <div class="metric-label">${escapeHtml(label)} ${definitionBubble(definition)}</div>
          <div class="metric-value">${value}</div>
          <div class="metric-explain">${explanation}</div>
        </div>
      `;
    }
    function leaderExplanation(leader, sector) {
      if (!leader || !leader.ticker) return "当前板块没有足够清晰的龙头候选，先按板块整体状态观察。";
      const subs = leader.leader_subscores || {};
      return reasonList([
        `综合分 ${escapeHtml(leader.leader_score || "-")}，角色 ${escapeHtml(leader.role_label || "-")}。`,
        `5日收益 ${escapeHtml(formatPercent(leader.return_5d))}，相对板块 ${escapeHtml(formatPercent(leader.relative_return_5d))}，5日涨停 ${escapeHtml(leader.limit_up_count_5d || 0)} 次，大涨 ${escapeHtml(leader.big_up_count_5d || 0)} 次。`,
        `子项：${scoreRef("启动", subs.startup_score, "启动分：涨停次数、相对板块收益和率先走强代理。")}${scoreRef("强度", subs.strength_score, "强度分：5日收益排名、涨停次数、大涨次数。")}${scoreRef("带动", subs.drive_score, "带动分：板块上涨占比、板块当日收益和个股成交额排名。")}${scoreRef("抗跌/修复", subs.resilience_score, "抗跌/修复分：趋势未破、收盘位置、近10日最大回撤。")}${scoreRef("封板质量代理", subs.board_quality_score, "封板质量代理：未接 limit_list_d 前，用涨停、收盘位置、上影线和成交额排名代替。")}`,
        `比较：板块上涨占比 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.up_ratio))}。如果个股强但板块不扩散，会降为“强势先锋”而不是总龙头。`,
      ]);
    }
    function zhongjunExplanation(zhongjun) {
      if (!zhongjun || !zhongjun.ticker) return "当前板块没有足够清晰的中军候选，说明容量或趋势稳定性还不够突出。";
      const subs = zhongjun.zhongjun_subscores || {};
      return reasonList([
        `综合分 ${escapeHtml(zhongjun.zhongjun_score || "-")}，角色 ${escapeHtml(zhongjun.role_label || "-")}。`,
        `容量：成交额 ${escapeHtml(formatTradeAmount(zhongjun.amount))}，流通市值 ${escapeHtml(formatMarketValue(zhongjun.circ_mv))}，换手率 ${escapeHtml(formatPercent((zhongjun.turnover_rate || 0) / 100))}。`,
        `资金：主力净流入 ${escapeHtml(formatMoneyWan(zhongjun.main_net_inflow))}，净流入占比 ${escapeHtml(formatPercent(zhongjun.main_net_inflow_rate))}。`,
        `趋势：收盘 ${escapeHtml(zhongjun.close || "-")}，MA10 ${escapeHtml(zhongjun.ma10 || "-")}，趋势${zhongjun.trend_unbroken ? "未破" : "已弱化"}。`,
        `子项：${scoreRef("容量", subs.capacity_score, "容量分：流通市值在板块内的分位。")}${scoreRef("成交稳定", subs.amount_stability_score, "成交稳定分：成交额排名和近5日成交额波动。")}${scoreRef("净流入", subs.net_flow_score ?? "待数据", "净流入分：moneyflow 大单+特大单净流入占成交额比例。")}${scoreRef("趋势稳定", subs.trend_stability_score, "趋势稳定分：MA10/MA20 趋势和近10日最大回撤。")}${scoreRef("换手稳定", subs.turnover_stability_score, "换手稳定分：换手率处于可承接区间更高。")}`,
      ]);
    }
    function divergenceExplanation(sector, divergence, repair) {
      const triggers = (divergence.triggers || []).map((item) => item.meaning).join("；");
      if (repair && repair.confirmed) {
        return reasonList([
          "前面存在分歧基础。",
          `今日板块强于大盘，上涨占比 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.up_ratio))}。`,
          `核心修复数量 ${escapeHtml(repair.core_repair_count || 0)}，因此判为 ${escapeHtml(repair.label || "修复")}。`,
        ]);
      }
      if (divergence && divergence.score) {
        return reasonList([
          `分歧分 ${escapeHtml(divergence.score)}。`,
          `触发项：${escapeHtml(triggers || "-")}。`,
        ]);
      }
      return reasonList([
        "当前成交额、广度和核心反馈没有触发明确分歧。",
        "接入分钟线后，可进一步判断盘中跳水和率先修复。",
      ]);
    }
    function setLeaderView(view) {
      leaderTickerDraft = document.getElementById("leaderTickerQuery")?.value || leaderTickerDraft;
      selectedLeaderView = view === "daily" ? "daily" : "total";
      if (latestSectorData) showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
    }
    function setSelectedLeaderDate(value) {
      leaderTickerDraft = document.getElementById("leaderTickerQuery")?.value || leaderTickerDraft;
      selectedLeaderDate = value;
      if (latestSectorData) showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
    }
    function renderLeaderQueryPanel(ready) {
      return `
        <div class="leader-query-panel">
          <h4>查询单个龙头的 30 日记录</h4>
          <p>输入股票代码后，统计上榜次数、第一名次数、平均名次、连续上榜和每日明细。</p>
          <div class="leader-query">
            <input id="leaderTickerQuery" value="${escapeHtml(leaderTickerDraft)}" oninput="leaderTickerDraft=this.value" placeholder="输入股票代码，例如 002484.SZ" ${ready ? "" : "disabled"} />
            <button class="secondary" onclick="queryLeaderStreak()" ${ready ? "" : "disabled"}>查询单票记录</button>
          </div>
          <div id="leaderStreakResult" class="leader-query-result hint">${ready ? "输入股票代码后查询近30个交易日的龙头榜记录。" : "龙头榜计算完成后可查询单票记录。"}</div>
        </div>
      `;
    }
    function renderLeaderSummary(summary) {
      const ranking = (summary && summary.ranking) || [];
      if (!ranking.length) return '<p class="section-conclusion">当前窗口没有形成可用的多日总龙头记录。</p>';
      return `
        <p class="section-conclusion">
          统计区间 ${escapeHtml(summary.window_start || "-")} 至 ${escapeHtml(summary.window_end || "-")}，共 ${escapeHtml(summary.trading_day_count || 0)} 个交易日。${escapeHtml(summary.formula || "")}
        </p>
        <div class="leader-summary-grid">
          ${ranking.map((leader, index) => `
            <article class="leader-summary-card ${index === 0 ? "is-champion" : ""}">
              <div class="leader-card-top">
                <div>
                  <div class="leader-card-rank">总榜 #${index + 1}</div>
                  <div class="leader-card-name">${stockLink(leader.name, leader.ticker, "compact")}</div>
                  <div class="leader-card-code">${escapeHtml(leader.ticker || "-")} · ${escapeHtml(leader.primary_sector || "-")}</div>
                </div>
                <div class="leader-summary-score">${escapeHtml(leader.summary_score || "-")}</div>
              </div>
              <div class="leader-role-label">${escapeHtml(leader.role_label || "-")} · ${escapeHtml(leader.primary_daily_role || "-")}</div>
              <div class="leader-stat-row">
                <span class="leader-stat">上榜 ${escapeHtml(leader.appearance_count || 0)} 次</span>
                <span class="leader-stat">第一名 ${escapeHtml(leader.rank_1_count || 0)} 次</span>
                <span class="leader-stat">平均名次 ${escapeHtml(leader.average_rank || "-")}</span>
                <span class="leader-stat">平均龙头分 ${escapeHtml(leader.average_leader_score || "-")}</span>
                <span class="leader-stat">最近 ${escapeHtml(leader.latest_date || "-")}</span>
              </div>
              ${reasonList((leader.evidence || []).map(escapeHtml))}
            </article>
          `).join("")}
        </div>
      `;
    }
    function renderSelectedLeaderDay(days) {
      if (!days.length) return '<p class="section-conclusion">暂无单日龙头数据。</p>';
      if (!selectedLeaderDate || !days.some((day) => day.date === selectedLeaderDate)) {
        selectedLeaderDate = days[days.length - 1].date;
      }
      const selectedDay = days.find((day) => day.date === selectedLeaderDate) || days[days.length - 1];
      return `
        <div class="leader-day-picker">
          <select aria-label="龙头榜日期" onchange="setSelectedLeaderDate(this.value)">
            ${[...days].reverse().map((day) => `<option value="${escapeHtml(day.date)}" ${day.date === selectedDay.date ? "selected" : ""}>${escapeHtml(day.date)}</option>`).join("")}
          </select>
          <p>单日榜只展示所选交易日的全市场前 5 名，不与30日总榜混排。</p>
        </div>
        <div class="leader-board">
          <div class="leader-day">
            <h4>${escapeHtml(selectedDay.date)} · 每日龙头前 5</h4>
            <ul class="leader-list">
              ${(selectedDay.leaders || []).map((leader) => `
                <li>
                  <span class="leader-rank">#${escapeHtml(leader.rank)}</span>
                  ${stockLink(leader.name, leader.ticker, "compact")}
                  <span> · ${escapeHtml(leader.sector_name || "-")} · ${escapeHtml(leader.role_label || "-")} · 分 ${escapeHtml(leader.leader_score || "-")}</span>
                </li>
              `).join("")}
            </ul>
          </div>
        </div>
      `;
    }
    function renderDailyLeaders(data) {
      const days = data.daily_leaders || [];
      const error = data.daily_leaders_error;
      if (!days.length) {
        return `
          <section id="dailyLeaderSection" class="sector-feature">
            <div class="feature-kicker">功能区三 · Leader Board</div>
            <div class="feature-header">
              <div>
                <h3>龙头榜</h3>
                <p>默认生成近30个交易日总龙头榜，同时保留单日榜和单票查询。</p>
              </div>
            </div>
            <div class="leader-status">
              <span class="leader-dot"></span>
              <span>${escapeHtml(error || "主板块和趋势榜已返回；30日龙头榜正在独立计算，完成后会自动刷新这里。")}</span>
            </div>
            ${error ? `<button class="secondary" onclick="loadDailyLeaders(document.getElementById('globalDate').value, document.getElementById('globalBenchmark').value)">重试龙头榜</button>` : ""}
            ${renderLeaderQueryPanel(false)}
          </section>
        `;
      }
      return `
        <section id="dailyLeaderSection" class="sector-feature">
          <div class="feature-kicker">功能区三 · Leader Board</div>
          <div class="feature-header">
            <div>
              <h3>龙头榜</h3>
              <p>总榜识别过去30个交易日反复成为市场情绪锚的股票；单日榜用于复盘某一个交易日。</p>
            </div>
          </div>
          <div class="leader-view-tabs" role="tablist" aria-label="龙头榜视图">
            <button class="leader-view-tab ${selectedLeaderView === "total" ? "is-active" : ""}" onclick="setLeaderView('total')">30日总龙头榜</button>
            <button class="leader-view-tab ${selectedLeaderView === "daily" ? "is-active" : ""}" onclick="setLeaderView('daily')">单日龙头榜</button>
          </div>
          ${selectedLeaderView === "daily" ? renderSelectedLeaderDay(days) : renderLeaderSummary(data.leader_summary || {})}
          ${renderLeaderQueryPanel(true)}
        </section>
      `;
    }
    function renderDataQuality(items) {
      return `
        <div class="boundary-grid compact">
          ${(items || []).map((item) => `
            <div class="boundary-item">
              <strong>${escapeHtml(item.group || item.field)}</strong>
              <span class="boundary-status">${escapeHtml(item.status_label || translateStatus(item.status))}</span>
              <h4>${escapeHtml(item.field)}</h4>
              <p>${escapeHtml(item.note)}</p>
            </div>
          `).join("")}
        </div>
      `;
    }
    function renderIndicatorDefinitions(items) {
      return `
        <div id="sector-indicator-definitions" class="indicator-grid compact">
          ${(items || []).map((item) => `
            <div class="indicator-item" id="indicator-${escapeHtml(item.indicator || "").replace(/[^a-zA-Z0-9_-]/g, "-")}">
              <h4>${escapeHtml(item.indicator)}</h4>
              <div class="indicator-formula">${escapeHtml(item.formula)}</div>
              <p>${escapeHtml(item.meaning)}</p>
            </div>
          `).join("")}
        </div>
      `;
    }
    function translateStatus(status) {
      return ({
        implemented: "已接入",
        "implemented/proxy_only": "已接入/代理口径",
        data_pending: "待接入",
        proxy_only: "代理口径",
      })[status] || status || "-";
    }
    function sectorStrengthClass(sector) {
      const score = Number((sector.scores && sector.scores.sector_score) || 0);
      if (score >= 75) return "strength-high";
      if (score >= 58) return "strength-mid";
      return "strength-watch";
    }
    function setSelectedSector(index) {
      selectedSectorIndex = Number(index) || 0;
      if (latestSectorData) showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
    }
    function scrollSectorFeature(id) {
      const node = document.getElementById(id);
      if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
      document.querySelectorAll(".story-link").forEach((button) => {
        button.classList.toggle("active", button.dataset.target === id);
      });
    }
    function renderSectorSwitcher(sectors) {
      if (!sectors.length) return "";
      selectedSectorIndex = Math.min(Math.max(selectedSectorIndex, 0), sectors.length - 1);
      return `
        <div class="sector-switcher" aria-label="切换强力板块">
          ${sectors.map((sector, index) => `
            <button class="sector-chip ${sectorStrengthClass(sector)} ${index === selectedSectorIndex ? "is-active" : ""}" onclick="setSelectedSector(${index})">
              <strong>${escapeHtml(sector.sector_name)}</strong>
              <small>${escapeHtml(sector.stage_label || "-")} · 分 ${escapeHtml(sector.scores && sector.scores.sector_score)}</small>
            </button>
          `).join("")}
        </div>
      `;
    }
    function renderSelectedSectorDetail(sector) {
      const leader = (sector.leader_candidates || [])[0] || {};
      const zhongjun = (sector.zhongjun_candidates || [])[0] || {};
      const divergence = sector.divergence || {};
      const repair = sector.repair || {};
      const fund = sector.fund_flow || {};
      return `
        <div class="sector-detail-grid">
          <article class="sector-detail-card full">
            <h3>${escapeHtml(sector.sector_name)} · ${escapeHtml(sector.stage_label)}</h3>
            <p class="section-conclusion">${escapeHtml(sector.stage_meaning)} ${escapeHtml(sector.action)}</p>
            ${metricBlock(
              "强弱和排名",
              "用 sector_score、近5日收益、相对大盘收益、近5日跑赢天数和成交额排名衡量板块是否持续强于市场。",
              `评分 ${escapeHtml(sector.scores && sector.scores.sector_score)}，5日收益 ${colorPercent(sector.metrics && sector.metrics.return_5d)}，相对大盘 ${colorPercent(sector.metrics && sector.metrics.relative_return_5d)}`,
              reasonList([
                `近5日跑赢 ${escapeHtml(sector.metrics && sector.metrics.outperform_days_5)} 天，成交额排名 ${escapeHtml(sector.metrics && sector.metrics.amount_rank)} / ${escapeHtml(sector.metrics && sector.metrics.total_sector_count)}。`,
                `${scoreRef("相对强弱分", sector.scores && sector.scores.relative_strength_score, "相对强弱分：5日跑赢大盘天数、5日相对收益和是否强于基准。")}${scoreRef("成交活跃分", sector.scores && sector.scores.amount_activity_score, "成交活跃分：成交额相对5日均、成交额排名和成交额占比变化。")}`,
              ])
            )}
          </article>
          <article class="sector-detail-card">
            ${metricBlock(
              "资金口径",
              "成交额只说明活跃度、容量和博弈强度；moneyflow 官方资金流接入后，用大单+特大单净额判断主力净流入。",
              `${escapeHtml(fund.label || "-")} · 成交额占比 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.market_share))} · 相对5日均 ${escapeHtml(formatRatio(sector.metrics && sector.metrics.amount_ratio_5))}`,
              reasonList([
                `${escapeHtml(fund.meaning || "moneyflow 未接入时，只能确认成交活跃，不能确认主力净流入。")}`,
                `主力净流入 ${escapeHtml(formatMoneyWan(fund.main_net_inflow))}，净流入占比 ${escapeHtml(formatPercent(fund.main_net_inflow_rate))}，净流入扩散 ${escapeHtml(formatPercent(fund.positive_moneyflow_ratio))}，近3日持续 ${escapeHtml(fund.moneyflow_persistence_3d ?? 0)} 天。`,
                `${scoreRef("资金状态", translateStatus(fund.data_status), "implemented 表示已读取 Tushare moneyflow；moneyflow_data_pending 表示 Mongo 暂无对应资金流。")}`,
              ])
            )}
            ${metricBlock(
              "广度",
              "上涨占比超过 55%-60% 才能说明板块不是孤立个股行情；低于 50% 时，即使板块指数上涨，也可能只是少数权重或个股拉动。",
              `上涨占比 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.up_ratio))}，涨停 ${escapeHtml(sector.metrics && sector.metrics.limit_up_count)}，大涨 ${escapeHtml(sector.metrics && sector.metrics.big_up_count)}`,
              reasonList([
                `上涨占比 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.up_ratio))}，涨停 ${escapeHtml(sector.metrics && sector.metrics.limit_up_count)}，大涨 ${escapeHtml(sector.metrics && sector.metrics.big_up_count)}。`,
                `${scoreRef("广度分", sector.scores && sector.scores.breadth_score, "广度分：上涨占比、大涨家数和涨停家数共同衡量板块扩散。")}`,
                "涨停和大涨个股用于判断是否从核心扩散到前排/后排。",
              ])
            )}
          </article>
          <article class="sector-detail-card">
            ${metricBlock(
              "龙头候选",
              "龙头看先动、最强、带动、抗跌和修复快；分钟启动时间和封板质量当前是日线代理，后续接 minute 与封板数据后升级。",
              `${stockLink(leader.name, leader.ticker)} · ${escapeHtml(leader.role_label || "-")} · ${escapeHtml(leader.leader_score || "-")}`,
              leaderExplanation(leader, sector)
            )}
            ${metricBlock(
              "中军候选",
              "中军看容量、成交稳定、趋势稳定和承接；不是大票就叫中军，必须同时有体量、成交和趋势稳定。",
              `${stockLink(zhongjun.name, zhongjun.ticker)} · ${escapeHtml(zhongjun.role_label || "-")} · ${escapeHtml(zhongjun.zhongjun_score || "-")}`,
              zhongjunExplanation(zhongjun)
            )}
          </article>
          <article class="sector-detail-card full">
            ${metricBlock(
              "分歧 / 修复",
              "分歧是涨幅、成交额、广度、核心股反馈之间出现不一致；修复必须有前置分歧，并且板块强于大盘、广度改善、核心先修复。",
              `${escapeHtml(divergence.label || "-")} / ${escapeHtml(repair.label || "-")}`,
              divergenceExplanation(sector, divergence, repair)
            )}
          </article>
        </div>
      `;
    }
    function renderTodayStrongSectors(data) {
      const sectors = data.top_sectors || [];
      const summary = data.summary || {};
      if (!sectors.length) {
        return `
          <section id="sector-today" class="sector-feature">
            <div class="feature-kicker">功能区一 · Today</div>
            <h3>今日强力板块</h3>
            <p class="section-conclusion">暂无可展示板块。</p>
          </section>
        `;
      }
      const selected = sectors[Math.min(Math.max(selectedSectorIndex, 0), sectors.length - 1)];
      return `
        <section id="sector-today" class="sector-feature">
          <div class="feature-kicker">功能区一 · Today</div>
          <div class="feature-header">
            <div>
              <h3>今日强力板块</h3>
              <p>点击下方板块切换详情；高亮芯片是当前查看板块，半透明芯片仍可点击切换。</p>
            </div>
          </div>
          <section class="market-hero-card">
            <div class="market-eyebrow">PART2 Sector Momentum · ${escapeHtml(data.benchmark_name || data.benchmark_ticker)}</div>
            <h2 class="market-headline">${escapeHtml(summary.headline || "板块动向等待确认")}</h2>
            <p class="market-subtitle">${escapeHtml(summary.conclusion || data.sector_source_note || "")}</p>
            <div class="market-meta">
              <span class="meta-chip">${escapeHtml(data.sector_source)}</span>
              <span class="meta-chip">确认/修复 ${escapeHtml(summary.confirmed_or_repair_count || 0)}</span>
              <span class="meta-chip">分歧 ${escapeHtml(summary.divergence_count || 0)}</span>
            </div>
          </section>
          ${renderSectorSwitcher(sectors)}
          ${renderSelectedSectorDetail(selected)}
        </section>
      `;
    }
    function renderTrendLineChart(points) {
      const rows = points || [];
      if (!rows.length) return '<div class="trend-line-chart"><span class="hint">暂无趋势点</span></div>';
      const values = rows.map((point) => Number(point.relative_return_1d || 0));
      let minValue = Math.min(0, ...values);
      let maxValue = Math.max(0, ...values);
      if (maxValue - minValue < 0.004) {
        maxValue += 0.002;
        minValue -= 0.002;
      }
      const range = maxValue - minValue;
      const positions = rows.map((point, index) => {
        const x = rows.length === 1 ? 50 : 8 + (index * 84) / (rows.length - 1);
        const value = Number(point.relative_return_1d || 0);
        const y = 17 + ((maxValue - value) / range) * 55;
        return { point, value, x, y, index };
      });
      const zeroY = 17 + ((maxValue - 0) / range) * 55;
      const pathPoints = positions.map((item) => `${item.x},${item.y}`).join(" ");
      return `
        <div class="trend-line-chart">
          <svg class="trend-line-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            <line class="trend-zero-line" x1="4" y1="${zeroY}" x2="96" y2="${zeroY}"></line>
            <polyline class="trend-line-path" points="${pathPoints}"></polyline>
          </svg>
          ${positions.map(({ point, value, x, y, index }) => {
            const signals = [
              point.strong ? "强势" : "",
              point.repair ? "修复" : "",
              point.resonance ? "共振" : "",
            ].filter(Boolean);
            const alignClass = index === 0 ? "align-left" : index === rows.length - 1 ? "align-right" : "";
            const aria = `${point.date}，板块 ${formatPercent(point.return_1d)}，相对大盘 ${formatPercent(value)}`;
            return `
              <button type="button" class="trend-point-hit ${value < 0 ? "is-negative" : ""} ${alignClass}" style="left:${x}%;top:${y}%" aria-label="${escapeHtml(aria)}">
                <span class="trend-point-label">${colorPercent(value)}</span>
                <span class="trend-point-tooltip">
                  <strong>${escapeHtml(point.date || "-")} · 数据明细</strong>
                  <span class="trend-tooltip-grid">
                    <span>板块涨跌</span><span>${colorPercent(point.return_1d)}</span>
                    <span>对比指数</span><span>${colorPercent(point.benchmark_return_1d)}</span>
                    <span>相对强弱</span><span>${colorPercent(value)}</span>
                    <span>上涨占比</span><span>${escapeHtml(formatPercent(point.up_ratio))}</span>
                    <span>量能 / 5日均</span><span>${escapeHtml(formatRatio(point.amount_ratio_5))}</span>
                    <span>主力净流入</span><span>${escapeHtml(formatMoneyWan(point.main_net_inflow))}</span>
                  </span>
                  ${signals.length ? `<span class="trend-signal-row">${signals.map((signal) => `<span class="trend-signal">${escapeHtml(signal)}</span>`).join("")}</span>` : ""}
                </span>
              </button>
              <span class="trend-date-label" style="left:${x}%">${escapeHtml(String(point.date || "").slice(5))}</span>
            `;
          }).join("")}
        </div>
      `;
    }
    function renderTrendSectors(data) {
      const trends = data.trend_sectors || [];
      return `
        <section id="sector-trends" class="sector-feature">
          <div class="feature-kicker">功能区二 · 5-Day Trend</div>
          <div class="feature-header">
            <div>
              <h3>趋势板块</h3>
              <p>用过去 5 日的多日强力、分歧后修复、与大盘共振和资金净流入次数综合排序。</p>
            </div>
          </div>
          ${trends.length ? `
            <div class="trend-board">
              ${trends.slice(0, 6).map((item, index) => `
                <article class="trend-card">
                  <h4>#${index + 1} ${escapeHtml(item.sector_name)} · ${escapeHtml(item.trend_label)}</h4>
                  <p class="rankline">
                    趋势分 ${escapeHtml(item.trend_score)}，5日收益 ${colorPercent(item.metrics && item.metrics.return_5d)}，相对大盘 ${colorPercent(item.metrics && item.metrics.relative_return_5d)}
                  </p>
                  ${renderTrendLineChart(item.trend_points)}
                  <div class="metric-explain">${reasonList((item.evidence || []).map(escapeHtml))}</div>
                </article>
              `).join("")}
            </div>
          ` : `<p class="section-conclusion">暂无趋势榜数据，先查看今日强力板块。</p>`}
        </section>
      `;
    }
    function renderKnowledgeSection(data) {
      return `
        <section id="sector-knowledge" class="sector-feature">
          <div class="feature-kicker">功能区四 · Definitions</div>
          <div class="feature-header">
            <div>
              <h3>指标口径与边界</h3>
              <p>把“怎么判断”和“哪些是真数据/代理数据”放在一起，所有小叹号都能跳回这里校验口径。</p>
            </div>
          </div>
          <div class="knowledge-grid">
            <article class="knowledge-panel">
              <h4>指标边界</h4>
              ${renderDataQuality(data.data_quality)}
            </article>
            <article class="knowledge-panel">
              <h4>指标口径</h4>
              ${renderIndicatorDefinitions(data.indicator_definitions)}
            </article>
          </div>
        </section>
      `;
    }
    function renderCandidates(candidates) {
      const card = document.getElementById("candidatesCard");
      if (!card) return;
      if (!candidates || !candidates.length) {
        card.style.display = "none";
        return;
      }
      card.style.display = "";
      const signalColors = { breakout_confirm: "signal-breakout", pullback_to_ma5: "signal-pullback", gentle_rise: "signal-gentle", watch: "signal-watch" };

      // 按 entry_quality 降序（后端已排好，直接用）
      const rows = candidates.filter((c) => c.signal !== "watch").concat(candidates.filter((c) => c.signal === "watch"));

      if (!rows.length) { card.style.display = "none"; return; }

      const qualityBadge = (q) => {
        const cls = q >= 70 ? "quality-high" : q >= 50 ? "quality-mid" : "quality-low";
        return `<span class="quality-badge ${cls}">${q}</span>`;
      };

      const riskTags = (flags) => {
        if (!flags || !flags.length) return "";
        return flags.map((f) => `<span class="risk-flag">${escapeHtml(f)}</span>`).join(" ");
      };

      const tableRows = rows.map((c) => `
        <tr class="candidate-row" onclick="jumpToAnalysis('${escapeHtml(c.ticker)}')">
          <td>
            <span class="signal-tag ${escapeHtml(signalColors[c.signal] || "signal-watch")}">${escapeHtml(c.signal_label)}</span>
            ${c.ma20_slope_up ? '<span class="badge-zhusheng">主升</span>' : ""}
          </td>
          <td>
            <strong>${escapeHtml(c.name)}</strong><br>
            <small class="muted">${escapeHtml(c.ticker)}</small>
            ${riskTags(c.risk_flags)}
          </td>
          <td>${escapeHtml(c.sector_name)}</td>
          <td><span class="role-label">${escapeHtml(c.role_label)}</span></td>
          <td class="num">${qualityBadge(c.entry_quality ?? 0)}</td>
          <td class="num">${escapeHtml(String(c.close ?? "-"))}</td>
          <td class="num">${escapeHtml(String(c.ma5 ?? "-"))}</td>
          <td class="num">${escapeHtml(String(c.stop_loss ?? "-"))}</td>
          <td class="num">${c.rsi != null ? c.rsi.toFixed(1) : "-"}</td>
          <td>${colorPercent(c.return_5d)}</td>
          <td class="reason-cell">${escapeHtml(c.reason)}</td>
        </tr>
      `).join("");
      showHtml("candidatesResult", `
        <table class="candidates-table">
          <thead><tr>
            <th>形态</th><th>股票</th><th>板块</th><th>角色</th>
            <th class="num">质量</th><th class="num">收盘</th><th class="num">MA5</th>
            <th class="num">止损</th><th class="num">RSI</th><th>5日收益</th><th>理由</th>
          </tr></thead>
          <tbody>${tableRows}</tbody>
        </table>
        <p class="hint">点击任意行跳转单票分析。止损位 = MA5 × 0.98，跌破收盘离场。「主升」= MA20 近 5 日向上。质量分 ≥ 70 为高质量入场。</p>
      `);
    }
    function jumpToAnalysis(ticker) {
      document.getElementById("analyzeTicker").value = ticker;
      switchTab("analysis");
      runAnalyze();
    }
    function renderSectorTrends(data) {
      const rawPayload = {
        summary: data.summary,
        top_sectors: data.top_sectors,
        trend_sectors: data.trend_sectors,
        daily_leaders: data.daily_leaders,
        leader_summary: data.leader_summary,
        state_machine: data.state_machine,
        indicator_definitions: data.indicator_definitions,
        data_quality: data.data_quality
      };
      return `
        <section class="sector-story-layout">
          <aside class="sector-storyline" aria-label="板块动向故事线">
            <div class="story-title">Storyline</div>
            <button class="story-link active" data-target="sector-today" onclick="scrollSectorFeature('sector-today')"><span>01</span><b class="story-link-label">今日强力</b></button>
            <button class="story-link" data-target="sector-trends" onclick="scrollSectorFeature('sector-trends')"><span>02</span><b class="story-link-label">趋势板块</b></button>
            <button class="story-link" data-target="dailyLeaderSection" onclick="scrollSectorFeature('dailyLeaderSection')"><span>03</span><b class="story-link-label">龙头榜</b></button>
            <button class="story-link" data-target="sector-knowledge" onclick="scrollSectorFeature('sector-knowledge')"><span>04</span><b class="story-link-label">口径边界</b></button>
          </aside>
          <div class="sector-feature-stack">
            ${renderTodayStrongSectors(data)}
            ${renderTrendSectors(data)}
            ${renderDailyLeaders(data)}
            ${renderKnowledgeSection(data)}
            <details class="json-viewer">
              <summary>查看中文字段明细（结构化数据）</summary>
              <pre class="json-code">${syntaxHighlightJson(rawPayload)}</pre>
            </details>
          </div>
        </section>
      `;
    }

    const formatPercent = (value) => {
      if (value === null || value === undefined) return "-";
      const number = Number(value || 0);
      return `${number >= 0 ? "+" : ""}${(number * 100).toFixed(2)}%`;
    };
    const colorPercent = (value) => {
      if (value === null || value === undefined) return '<span class="flat">-</span>';
      const number = Number(value || 0);
      const text = `${number >= 0 ? "+" : ""}${(number * 100).toFixed(2)}%`;
      const cls = number > 0.000099 ? "up" : number < -0.000099 ? "down" : "flat";
      return `<span class="${cls}">${text}</span>`;
    };
    const formatRatio = (value) => {
      if (value === null || value === undefined) return "-";
      return `${Number(value || 0).toFixed(2)}x`;
    };
    const formatTradeAmount = (value) => {
      if (value === null || value === undefined) return "-";
      const number = Number(value || 0);
      if (!number) return "-";
      return `${(number / 100000).toFixed(2)}亿`;
    };
    const formatMarketValue = (value) => {
      if (value === null || value === undefined) return "-";
      const number = Number(value || 0);
      if (!number) return "-";
      return `${(number / 10000).toFixed(2)}亿`;
    };
    const formatMoneyWan = (value) => {
      if (value === null || value === undefined) return "待接入";
      const number = Number(value || 0);
      if (!number) return "0.00亿";
      return `${number >= 0 ? "+" : ""}${(number / 10000).toFixed(2)}亿`;
    };
    const formatCompactNumber = (value, digits = 2) => {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
      return Number(value).toFixed(digits).replace(/\.00$/, "");
    };
    const formatVolume = (value) => {
      if (value === null || value === undefined) return "-";
      return `${(Number(value || 0) / 10000).toFixed(2)}万手`;
    };
    function renderStockPriceChart(points) {
      const rows = points || [];
      if (!rows.length) return '<p class="section-conclusion">暂无价格序列。</p>';
      const values = rows.flatMap((row) => [row.close, row.ma5, row.ma20]).map(Number).filter(Number.isFinite);
      let min = Math.min(...values);
      let max = Math.max(...values);
      if (max - min < 0.01) { min -= 0.01; max += 0.01; }
      const y = (value) => 10 + ((max - Number(value || 0)) / (max - min)) * 70;
      const positions = rows.map((row, index) => ({
        row,
        x: rows.length === 1 ? 50 : 4 + (index * 92) / (rows.length - 1),
        closeY: y(row.close),
        ma5Y: y(row.ma5),
        ma20Y: y(row.ma20),
      }));
      return `
        <div class="stock-price-chart">
          <div class="market-chart-legend"><span class="legend-primary">收盘</span><span class="legend-ma5">MA5</span><span class="legend-secondary">MA20</span></div>
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            <line class="market-grid-line" x1="4" y1="80" x2="96" y2="80"></line>
            <line class="market-grid-line" x1="4" y1="45" x2="96" y2="45"></line>
            <line class="market-grid-line" x1="4" y1="10" x2="96" y2="10"></line>
            <polyline class="stock-close-line" points="${positions.map((item) => `${item.x},${item.closeY}`).join(" ")}"></polyline>
            <polyline class="stock-ma5-line" points="${positions.map((item) => `${item.x},${item.ma5Y}`).join(" ")}"></polyline>
            <polyline class="stock-ma20-line" points="${positions.map((item) => `${item.x},${item.ma20Y}`).join(" ")}"></polyline>
          </svg>
          ${positions.map((item, index) => `
            <button type="button" class="stock-chart-point ${index === positions.length - 1 ? "is-last" : ""}" style="left:${item.x}%;top:${item.closeY}%" aria-label="${escapeHtml(item.row.date)} 收盘 ${escapeHtml(item.row.close)}">
              ${index === positions.length - 1 ? `<span class="stock-last-label">${escapeHtml(formatCompactNumber(item.row.close))}</span>` : ""}
              <span class="market-chart-tooltip">
                <strong>${escapeHtml(item.row.date)}</strong>
                <span>收盘</span><b>${escapeHtml(formatCompactNumber(item.row.close))}</b>
                <span>涨跌</span><b>${colorPercent(item.row.pct_chg)}</b>
                <span>MA5</span><b>${escapeHtml(formatCompactNumber(item.row.ma5))}</b>
                <span>MA20</span><b>${escapeHtml(formatCompactNumber(item.row.ma20))}</b>
                <span>成交额</span><b>${escapeHtml(formatTradeAmount(item.row.amount))}</b>
              </span>
            </button>
          `).join("")}
          <span class="market-chart-start">${escapeHtml(String(rows[0].date || "").slice(5))}</span>
          <span class="market-chart-end">${escapeHtml(String(rows[rows.length - 1].date || "").slice(5))}</span>
        </div>
      `;
    }
    function translateDecision(value) {
      const mapping = {
        defense_first: "防守优先",
        confirmation_needed: "等待确认",
        stand_aside: "空仓观察",
        exit_on_invalidation: "跌破失效位退出",
        attack: "进攻",
        hold: "持有观察",
      };
      return mapping[value] || value || "等待确认";
    }
    function renderStockAnalysis(data) {
      const overview = data.market_overview || {};
      const snapshot = data.snapshot || {};
      const metadata = snapshot.metadata_context || {};
      const decision = data.decision || {};
      const structure = snapshot.structure_assessment || {};
      const name = overview.name || metadata.name || data.ticker;
      const pct = Number(overview.pct_chg || 0);
      const rawPayload = { snapshot: data.snapshot, decision: data.decision, explanation: data.explanation, data_source: data.data_source };
      const metricRows = [
        ["今开", overview.open], ["最高", overview.high], ["最低", overview.low], ["昨收", overview.previous_close],
        ["成交量", formatVolume(overview.volume)], ["成交额", formatTradeAmount(overview.amount)], ["换手率", overview.turnover_rate === null || overview.turnover_rate === undefined ? "-" : `${formatCompactNumber(overview.turnover_rate)}%`],
        ["总市值", formatMarketValue(overview.total_mv)], ["流通市值", formatMarketValue(overview.circ_mv)], ["市盈率", formatCompactNumber(overview.pe)], ["市净率", formatCompactNumber(overview.pb)], ["市销率", formatCompactNumber(overview.ps)],
      ];
      return `
        <section class="stock-detail-page">
          <header class="stock-overview-hero">
            <div>
              <div class="stock-identity"><span class="stock-avatar">${escapeHtml(String(name).slice(0, 1))}</span><div><h2>${escapeHtml(name)}</h2><p>${escapeHtml(data.ticker)} · ${escapeHtml(overview.market || metadata.market || "-")} · ${escapeHtml(overview.industry || metadata.industry || "-")}</p></div></div>
              <div class="stock-price ${pct < 0 ? "is-down" : "is-up"}">${escapeHtml(formatCompactNumber(overview.close))}<span>${colorPercent(pct)}</span></div>
              <small>${escapeHtml(overview.analysis_date || data.date)} 收盘 · ${escapeHtml(data.data_source || "-")}</small>
            </div>
            <div class="stock-decision-badge"><span>石论结论</span><strong>${escapeHtml(translateDecision(decision.conclusion_label))}</strong><p>${escapeHtml(translateDecision(decision.watching_action))}</p></div>
          </header>
          <section class="stock-metric-grid">
            ${metricRows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "-")}</strong></div>`).join("")}
          </section>
          <section class="stock-main-grid">
            <article class="stock-chart-card">
              <div class="feature-kicker">60-Day Daily Chart</div>
              <h3>价格与均线</h3>
              <p>日线复盘，悬浮数据点可查看收盘、涨跌、均线和成交额。</p>
              ${renderStockPriceChart(overview.price_series)}
            </article>
            <article class="stock-analysis-card">
              <div class="feature-kicker">Decision Map</div>
              <h3>关键交易边界</h3>
              <div class="stock-boundary-grid">
                <div><span>主支撑</span><strong>${escapeHtml(formatCompactNumber(snapshot.support_main || structure.support_level))}</strong></div>
                <div><span>压力位</span><strong>${escapeHtml(formatCompactNumber(snapshot.pressure_main || structure.resistance_level))}</strong></div>
                <div><span>失效位</span><strong>${escapeHtml(formatCompactNumber(snapshot.invalidation_level || decision.invalidation))}</strong></div>
                <div><span>入场概率</span><strong>${escapeHtml(formatPercent(snapshot.entry_probability))}</strong></div>
              </div>
              <p class="stock-analysis-summary">${escapeHtml(String(data.explanation || "").split("\n")[0] || "等待分析结论。")}</p>
            </article>
          </section>
          <section class="stock-evidence-grid">
            ${Object.entries(snapshot.evidence_sections || {}).slice(0, 6).map(([key, items]) => `
              <article><span>${escapeHtml(key)}</span>${reasonList((items || []).slice(0, 4).map(escapeHtml))}</article>
            `).join("")}
          </section>
          <details class="json-viewer">
            <summary>查看单票中文字段明细</summary>
            <pre class="json-code">${syntaxHighlightJson(rawPayload)}</pre>
          </details>
        </section>
      `;
    }

    async function fetchJson(url, options = {}) {
      const timeoutMs = options.timeoutMs || 0;
      const requestOptions = { ...options };
      delete requestOptions.timeoutMs;
      let timeoutId;
      if (timeoutMs > 0) {
        const controller = new AbortController();
        requestOptions.signal = controller.signal;
        timeoutId = setTimeout(() => controller.abort(), timeoutMs);
      }
      const response = await fetch(url, requestOptions);
      if (timeoutId) clearTimeout(timeoutId);
      const text = await response.text();
      let payload;
      try { payload = JSON.parse(text); } catch { payload = { raw: text }; }
      if (!response.ok) throw payload;
      return payload;
    }

    async function loadStatus() {
      try {
        const data = await fetchJson("/api/v1/push-channel/status");
        runtimeStatus = data;
        document.getElementById("status").innerHTML = [
          statusCell("Mongo", data.mongo_configured, "日报和分析默认读取 Mongo"),
          statusCell("飞书", data.feishu_configured, data.feishu_webhook || "当前真实推送优先通道"),
          statusCell("Telegram Bot", data.telegram_bot_configured, "Bot token 状态"),
          statusCell("Telegram 日报", data.telegram_daily_push_enabled, "需要显式 push chat ids")
        ].join("");
        updateAnalyzeHint();
      } catch (error) {
        document.getElementById("status").innerHTML = `<pre>${formatError(error)}</pre>`;
      }
    }

    function switchDataSourceTab(name) {
      document.querySelectorAll(".data-source-tab").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.sourceTab === name);
      });
      document.querySelectorAll(".data-source-panel").forEach((panel) => {
        panel.style.display = panel.dataset.sourcePanel === name ? "" : "none";
      });
    }

    async function loadDataStatus() {
      const date = encodeURIComponent(document.getElementById("globalDate").value);
      const ticker = encodeURIComponent(selectedBenchmarkTicker());
      try {
        const data = await fetchJson(`/api/v1/data/status?date=${date}&ticker=${ticker}`);
        runtimeStatus.data = data;
        const overall = data.overall_status || "unknown";
        const overallClass = overall === "ready" ? "ok" : (overall === "blocked" ? "bad" : "");
        const overallLabel = { ready: "全部就绪", degraded: "降级可用", blocked: "核心数据缺失" }[overall] || overall;
        document.getElementById("dataStatus").innerHTML =
          `<strong class="${overallClass}">${overallLabel}</strong> | ${escapeHtml(String(data.message || ""))}`;

        // 网格视图：核心 vs 增强
        const datasets = data.datasets || [];
        const renderTier = (tier, title) => {
          const items = datasets.filter((d) => d.tier === tier);
          if (!items.length) return "";
          const cards = items.map((d) => {
            const okCls = d.ok ? "ds-ok" : "ds-miss";
            const okIcon = d.ok ? "✓" : "✗";
            const impact = d.ok ? "" : `<p class="ds-impact">缺失影响：${escapeHtml(d.impact_on_miss)}</p>`;
            const extras = d.extras && Object.keys(d.extras).length
              ? `<p class="ds-extras">${Object.entries(d.extras).map(([k, v]) => `${k}: ${v ? "✓" : "✗"}`).join(" · ")}</p>`
              : "";
            return `
              <article class="ds-card ${okCls}">
                <header><span class="ds-icon">${okIcon}</span><strong>${escapeHtml(d.label)}</strong></header>
                <p class="ds-detail">${escapeHtml(d.detail)}</p>
                <p class="ds-source">来源：${escapeHtml(d.source)}</p>
                ${extras}
                ${impact}
              </article>
            `;
          }).join("");
          return `<div class="ds-tier"><h4>${title}</h4><div class="ds-grid">${cards}</div></div>`;
        };
        document.getElementById("dataStatusGrid").innerHTML =
          renderTier("core", "核心数据（缺一不可）") +
          renderTier("enhanced", "增强数据（缺失自动降级）");
      } catch (error) {
        document.getElementById("dataStatus").textContent = formatError(error);
        document.getElementById("dataStatusGrid").innerHTML = "";
      }
    }

    async function syncIndexHistoryYear() {
      const target = "tushareSyncResult";
      show(target, "正在同步 4 个基准指数 1 年历史（不跑全市场个股）... 约 5-10 秒。");
      try {
        const data = await fetchJson("/api/v1/data/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          timeoutMs: 120000,
          body: JSON.stringify({
            target_date: document.getElementById("globalDate").value,
            mode: "index_history_year",
            benchmark_ticker: selectedBenchmarkTicker()
          })
        });
        show(target, data);
        await loadDataStatus();
      } catch (error) {
        show(target, formatError(error));
      }
    }

    async function syncAkshare(dataset) {
      const labels = {
        all: "全部",
        limit_up_pool: "涨停池",
        concept_boards: "概念板块",
        north_capital_flow: "北向资金",
      };
      const target = "akshareSyncResult";
      show(target, `正在同步 akshare ${labels[dataset] || dataset}... 单一来源策略，失败直接报错。`);
      try {
        const data = await fetchJson("/api/v1/data/sync-akshare", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          timeoutMs: 120000,
          body: JSON.stringify({
            target_date: document.getElementById("globalDate").value,
            dataset: dataset,
          })
        });
        show(target, data);
        await loadDataStatus();
      } catch (error) {
        show(target, formatError(error));
      }
    }

    // ── 波浪文档导入 ─────────────────────────────────────────────────────
    async function uploadWaveDoc() {
      const fileInput = document.getElementById("waveDocFile");
      const file = fileInput.files && fileInput.files[0];
      if (!file) {
        showHtml("waveDocResult", '<p class="hint" style="color:#d97706">请先选择文件。</p>');
        return;
      }
      const useAi = document.getElementById("waveUseAi").checked;
      const form = new FormData();
      form.append("file", file);
      form.append("target_date", document.getElementById("globalDate").value);
      form.append("benchmark_ticker", selectedBenchmarkTicker());
      form.append("use_ai", useAi ? "true" : "false");
      form.append("ai_provider", "anthropic");
      showHtml("waveDocResult", '<p class="hint">正在上传并解析文档...</p>');
      try {
        const resp = await fetch("/api/v1/market/upload-wave-doc", { method: "POST", body: form });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
        const data = await resp.json();
        renderWaveDocResult(data);
      } catch (error) {
        showHtml("waveDocResult", `<p class="hint" style="color:#d97706">${escapeHtml(formatError(error))}</p>`);
      }
    }

    async function loadWaveDocs() {
      const date = encodeURIComponent(document.getElementById("globalDate").value);
      const bm = encodeURIComponent(selectedBenchmarkTicker());
      try {
        const data = await fetchJson(`/api/v1/market/wave-docs?date=${date}&benchmark_ticker=${bm}`);
        if (!data.docs || !data.docs.length) {
          showHtml("waveDocResult", '<p class="hint">当前日期暂无上传文档。</p>');
          return;
        }
        const html = data.docs.map(renderWaveDocCard).join("");
        showHtml("waveDocResult", `<div class="wave-doc-list"><h4>已上传 ${data.docs.length} 份文档</h4>${html}</div>`);
      } catch (error) {
        showHtml("waveDocResult", `<p class="hint" style="color:#d97706">${escapeHtml(formatError(error))}</p>`);
      }
    }

    async function deleteWaveDocs() {
      if (!window.confirm("确认清空当前日期所有已上传的文档？此操作不可恢复。")) return;
      const date = encodeURIComponent(document.getElementById("globalDate").value);
      const bm = encodeURIComponent(selectedBenchmarkTicker());
      try {
        const resp = await fetch(`/api/v1/market/wave-docs?date=${date}&benchmark_ticker=${bm}`, { method: "DELETE" });
        const data = await resp.json();
        showHtml("waveDocResult", `<p class="hint">已删除 ${data.deleted_count || 0} 份文档。</p>`);
      } catch (error) {
        showHtml("waveDocResult", `<p class="hint" style="color:#d97706">${escapeHtml(formatError(error))}</p>`);
      }
    }

    function renderWaveDocCard(doc) {
      const kindColors = { support: "level-support", pressure: "level-resistance", wave: "level-resistance", breakdown: "level-resistance", breakout: "level-support", neutral: "" };
      const kindLabels = { support: "支撑", pressure: "压力", wave: "波浪", breakdown: "破位", breakout: "突破", neutral: "中性" };
      const levels = (doc.levels || []).slice(0, 12).map((lv) => `
        <article class="insight-row-card ${kindColors[lv.kind] || ""}">
          <span class="metric-label">${escapeHtml(kindLabels[lv.kind] || lv.kind)} · ${escapeHtml(lv.label)}</span>
          <strong>${lv.price}</strong>
          <p>${escapeHtml(lv.context)}</p>
        </article>
      `).join("");
      return `
        <div class="wave-doc-card">
          <header>
            <strong>${escapeHtml(doc.source_doc)}</strong>
            <small>上传 ${escapeHtml(doc.uploaded_at || "")} · 提取 ${doc.level_count || 0} 个关键位 · 文档 ${doc.text_length || 0} 字</small>
          </header>
          ${doc.ai_summary ? `<div class="wave-doc-ai"><h5>AI 摘要（${escapeHtml(doc.ai_provider || "")}）</h5><p>${escapeHtml(doc.ai_summary)}</p></div>` : ""}
          <div class="insight-row-grid">${levels || '<p class="hint">未提取到结构化关键位。可查看下方原文预览。</p>'}</div>
          ${doc.raw_text_preview ? `<details class="wave-doc-preview"><summary>原文预览（前 300 字）</summary><pre>${escapeHtml(doc.raw_text_preview)}</pre></details>` : ""}
        </div>
      `;
    }

    function renderWaveDocResult(data) {
      const summary = `<p class="hint">解析完成。提取 <strong>${data.level_count || 0}</strong> 个关键位${data.ai_summary ? "，已生成 AI 摘要" : ""}。下次刷新 PART1 时会自动叠加。</p>`;
      showHtml("waveDocResult", summary + renderWaveDocCard(data));
    }

    async function loadTushareHealth() {
      try {
        const data = await fetchJson("/api/v1/tushare/health", { timeoutMs: 90000 });
        const ok = data.overall_status === "available";
        const partial = data.overall_status === "partial";
        const cls = ok ? "ok" : (partial ? "" : "bad");
        document.getElementById("tushareHealthSummary").innerHTML =
          `<strong class="${cls}">${data.overall_label}</strong> | ${data.message}`;
        const lines = [
          `Tushare 网关：${data.overall_label}`,
          `说明：${data.message}`,
          `建议：${data.recommendation || "-"}`,
          "",
          prettyChineseJson({
            base_url: data.base_url,
            probe_dates: data.probe_dates,
            checks: data.checks
          })
        ].join("\n");
        show("tushareHealthResult", lines);
      } catch (error) {
        document.getElementById("tushareHealthSummary").innerHTML = `<strong class="bad">检查失败</strong> | ${formatError(error)}`;
        show("tushareHealthResult", formatError(error));
      }
    }

    async function syncLatestData() {
      const target = "tushareSyncResult";
      show(target, "正在同步最新交易日... 如果 Mongo 已有目标日期数据，会直接跳过。");
      try {
        const data = await fetchJson("/api/v1/data/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          timeoutMs: 60000,
          body: JSON.stringify({
            target_date: document.getElementById("globalDate").value,
            mode: "latest",
            force: false,
            benchmark_ticker: selectedBenchmarkTicker()
          })
        });
        show(target, data);
        await loadDataStatus();
      } catch (error) {
        show(target, formatError(error));
      }
    }

    async function syncIncrementalData() {
      const target = "tushareSyncResult";
      show(target, "正在按本地水位线增量同步... 只会请求 Mongo 缺失的交易日。");
      try {
        const data = await fetchJson("/api/v1/data/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          timeoutMs: 120000,
          body: JSON.stringify({
            target_date: document.getElementById("globalDate").value,
            mode: "incremental",
            force: false,
            benchmark_ticker: selectedBenchmarkTicker(),
            incremental_lookback_days: 14,
            incremental_overlap_days: 3
          })
        });
        show(target, data);
        await loadDataStatus();
      } catch (error) {
        show(target, formatError(error));
      }
    }

    async function syncHistoryYear() {
      const target = "tushareSyncResult";
      const confirmed = window.confirm("将从 Tushare 同步全市场最近一年历史数据到 Mongo，可能耗时几分钟。是否继续？");
      if (!confirmed) return;
      show(target, "正在导入全市场最近一年历史数据... 这一步可能需要几分钟。");
      try {
        const data = await fetchJson("/api/v1/data/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          timeoutMs: 600000,
          body: JSON.stringify({
            target_date: document.getElementById("globalDate").value,
            mode: "history_year",
            force: false,
            benchmark_ticker: selectedBenchmarkTicker()
          })
        });
        show(target, data);
        await loadDataStatus();
      } catch (error) {
        show(target, formatError(error));
      }
    }

    function useLatestHistoryDate() {
      const latest = runtimeStatus.data && runtimeStatus.data.latest_market_date;
      if (!latest) {
        alert("Mongo 暂无可用历史日期，请先同步数据。");
        return;
      }
      document.getElementById("globalDate").value = latest;
      loadDataStatus();
    }

    function updateAnalyzeHint() {
      const fallback = document.getElementById("allowFallback").checked;
      const hint = document.getElementById("analyzeHint");
      if (!runtimeStatus.mongo_configured && !fallback) {
        hint.textContent = "当前 Mongo 未配置。关闭 fallback 时，单票分析会直接提示缺少 Mongo 数据。";
      } else if (!runtimeStatus.mongo_configured && fallback) {
        hint.textContent = "当前 Mongo 未配置，已勾选 fallback：会调用 Tushare，可能较慢或因网络失败。";
      } else if (fallback) {
        hint.textContent = "已勾选 fallback：Mongo 查不到数据时会临时调用 Tushare，可能等待较久。";
      } else {
        hint.textContent = "Mongo-first 模式：只读 Mongo，不会调用 Tushare。";
      }
    }

    async function runAnalyze() {
      const ticker = encodeURIComponent(document.getElementById("analyzeTicker").value.trim());
      const date = encodeURIComponent(document.getElementById("globalDate").value);
      const fallback = document.getElementById("allowFallback").checked ? "true" : "false";
      showHtml("analyzeResult", `<div class="stock-loading"><span></span><strong>正在生成个股行情与策略分析</strong><p>${fallback === "true" ? "已允许 Tushare fallback，若 Mongo 无数据可能等待较久。" : "Mongo-first 模式，不会调用 Tushare。"}</p></div>`);
      try {
        latestAnalysisData = await fetchJson(`/api/v1/analyze?ticker=${ticker}&date=${date}&allow_tushare_fallback=${fallback}`, { timeoutMs: 25000 });
        showHtml("analyzeResult", renderStockAnalysis(latestAnalysisData));
      } catch (error) {
        showHtml("analyzeResult", `<div class="interpretation-card"><h3>分析失败</h3><p class="section-conclusion">${escapeHtml(formatError(error, "analysis"))}</p></div>`);
      }
    }

    async function runMarketPermission(force = false) {
      const date = encodeURIComponent(selectedMarketDate());
      const benchmark = encodeURIComponent(selectedBenchmarkTicker());
      startMarketProgress();
      const action = force ? "强制重算" : "正在确认";
      show("marketPermissionConfirmResult", `${action}权限...`);
      showHtml("marketPermissionResult", `<div class="interpretation-card"><h3>${force ? "正在重新计算" : "正在查询"}</h3><p class="section-conclusion">${force ? "已跳过缓存，正在重新计算 PART1 大盘权限..." : "正在按当前日期和指数口径计算 PART1 大盘权限（优先读缓存）..."}</p></div>`);
      try {
        const url = `/api/v1/market/permission?date=${date}&benchmark_ticker=${benchmark}${force ? "&force=true" : ""}`;
        const data = await fetchJson(url, { timeoutMs: 60000 });
        latestMarketData = data;
        syncBenchmarkSelects(data.benchmark_ticker);
        syncMarketDates(data.analysis_date);
        renderPermissionSummary(data);
        renderMarketPermissionDetail(data);
        finishMarketProgress(true);
      } catch (error) {
        document.getElementById("marketPermissionSummary").innerHTML = "";
        show("marketPermissionConfirmResult", formatError(error));
        showHtml("marketPermissionResult", `<div class="interpretation-card"><h3>查询失败</h3><p class="section-conclusion">${escapeHtml(formatError(error))}</p></div>`);
        finishMarketProgress(false);
      }
    }

    function forceRunMarketPermission() { return runMarketPermission(true); }

    function queryLeaderStreak() {
      const ticker = document.getElementById("leaderTickerQuery").value.trim().toUpperCase();
      leaderTickerDraft = ticker;
      const output = document.getElementById("leaderStreakResult");
      if (!ticker) {
        output.innerHTML = "请输入股票代码，例如 002484.SZ。";
        return;
      }
      if (!latestSectorData || !(latestSectorData.daily_leaders || []).length) {
        output.innerHTML = "请先点击“查询板块动向”，生成每日龙头榜后再查询。";
        return;
      }
      const days = [...(latestSectorData.daily_leaders || [])].sort((a, b) => String(b.date).localeCompare(String(a.date)));
      const records = [];
      for (const day of days) {
        const matched = (day.leaders || []).find((leader) => String(leader.ticker || "").toUpperCase() === ticker);
        if (matched) records.push({ date: day.date, ...matched });
      }
      let consecutive = 0;
      for (const day of days) {
        const matched = (day.leaders || []).some((leader) => String(leader.ticker || "").toUpperCase() === ticker);
        if (!matched) break;
        consecutive += 1;
      }
      if (!records.length) {
        output.innerHTML = `<div class="leader-day"><strong>${escapeHtml(ticker)}</strong><p class="section-conclusion">近30个交易日未进入每日龙头榜前 5。</p></div>`;
        return;
      }
      const rankOneCount = records.filter((record) => Number(record.rank) === 1).length;
      const averageRank = records.reduce((sum, record) => sum + Number(record.rank || 0), 0) / records.length;
      const summaryRecord = ((latestSectorData.leader_summary || {}).ranking || []).find((item) => String(item.ticker || "").toUpperCase() === ticker);
      output.innerHTML = `
        <div class="leader-summary-card is-champion">
          <div class="leader-card-top">
            <div>
              <div class="leader-card-rank">单票 30 日记录</div>
              <div class="leader-card-name">${stockLink(records[0].name, ticker, "compact")}</div>
              <div class="leader-card-code">${escapeHtml(ticker)} · ${escapeHtml(summaryRecord && summaryRecord.primary_sector || records[0].sector_name || "-")}</div>
            </div>
            <div class="leader-summary-score">${escapeHtml(summaryRecord && summaryRecord.summary_score || records.length)}</div>
          </div>
          <div class="leader-stat-row">
            <span class="leader-stat">上榜 ${escapeHtml(records.length)} 次</span>
            <span class="leader-stat">第一名 ${escapeHtml(rankOneCount)} 次</span>
            <span class="leader-stat">平均名次 ${escapeHtml(averageRank.toFixed(2))}</span>
            <span class="leader-stat">连续上榜 ${escapeHtml(consecutive)} 天</span>
          </div>
          <ul class="leader-list">
            ${records.map((record) => `
              <li>
                <span class="leader-rank">${escapeHtml(record.date)} #${escapeHtml(record.rank)}</span>
                ${stockLink(record.name, ticker, "compact")}
                <span> · ${escapeHtml(record.sector_name || "-")} · ${escapeHtml(record.role_label || "-")} · 分 ${escapeHtml(record.leader_score || "-")} · 5日收益 ${escapeHtml(formatPercent(record.return_5d))}</span>
              </li>
            `).join("")}
          </ul>
        </div>
      `;
    }

    async function runSectorTrends() {
      const dateValue = document.getElementById("globalDate").value;
      const benchmarkValue = document.getElementById("globalBenchmark").value;
      const date = encodeURIComponent(dateValue);
      const benchmark = encodeURIComponent(benchmarkValue);
      const requestVersion = ++sectorRequestVersion;
      startSectorProgress(requestVersion);
      showHtml("sectorTrendResult", '<div class="interpretation-card"><h3>正在查询</h3><p class="section-conclusion">正在计算 PART2 板块动向、龙头/中军候选和分歧/修复状态...</p></div>');
      try {
        const data = await fetchJson(`/api/v1/market/sectors?date=${date}&benchmark_ticker=${benchmark}`, { timeoutMs: 45000 });
        if (requestVersion !== sectorRequestVersion) return;
        selectedSectorIndex = 0;
        selectedLeaderView = "total";
        selectedLeaderDate = "";
        leaderTickerDraft = "";
        latestSectorData = { ...data, daily_leaders_loading: true };
        syncBenchmarkSelects(data.benchmark_ticker);
        syncMarketDates(data.analysis_date);
        showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
        renderCandidates(data.candidates || []);
        document.getElementById("leaderStreakResult").textContent = "板块结果已完成；近30个交易日龙头榜正在后台计算。";
        advanceSectorProgressToLeaders(requestVersion);
        void loadDailyLeaders(dateValue, benchmarkValue, requestVersion);
      } catch (error) {
        if (requestVersion !== sectorRequestVersion) return;
        failSectorProgress(requestVersion, false);
        showHtml("sectorTrendResult", `<div class="interpretation-card"><h3>查询失败</h3><p class="section-conclusion">${escapeHtml(formatError(error, "sectors"))}</p></div>`);
      }
    }

    async function loadDailyLeaders(dateValue, benchmarkValue, requestVersion = sectorRequestVersion) {
      try {
        const date = encodeURIComponent(dateValue);
        const benchmark = encodeURIComponent(benchmarkValue);
        const data = await fetchJson(`/api/v1/market/leaders?date=${date}&benchmark_ticker=${benchmark}`, { timeoutMs: 90000 });
        if (requestVersion !== sectorRequestVersion) return;
        latestSectorData = {
          ...latestSectorData,
          daily_leaders: data.daily_leaders || [],
          leader_summary: data.leader_summary || {},
          daily_leaders_loading: false,
          daily_leaders_error: "",
        };
        showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
        const ticker = document.getElementById("leaderTickerQuery").value.trim();
        if (ticker) {
          queryLeaderStreak();
        } else {
          document.getElementById("leaderStreakResult").textContent = "近30个交易日龙头榜已更新，可输入股票代码查询连续记录。";
        }
        completeSectorProgress(requestVersion);
      } catch (error) {
        if (requestVersion !== sectorRequestVersion) return;
        failSectorProgress(requestVersion, true);
        latestSectorData = {
          ...latestSectorData,
          daily_leaders_loading: false,
          daily_leaders_error: formatError(error, "leaders"),
        };
        if (latestSectorData) showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
        document.getElementById("leaderStreakResult").textContent = "板块主结果已完成；近30个交易日龙头榜暂未返回。";
      }
    }

    async function runTelegramText() {
      const ticker = encodeURIComponent(document.getElementById("analyzeTicker").value.trim());
      const date = encodeURIComponent(document.getElementById("globalDate").value);
      show("analyzeResult", "生成中...");
      try {
        const data = await fetchJson(`/api/v1/telegram/analyze?ticker=${ticker}&date=${date}`, { timeoutMs: 25000 });
        show("analyzeResult", data.text || data);
      } catch (error) {
        show("analyzeResult", formatError(error));
      }
    }

    async function runDailyPush() {
      show("pushResult", "执行中...");
      try {
        const data = await fetchJson("/api/v1/daily-push", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            target_date: document.getElementById("globalDate").value,
            dry_run: document.getElementById("dryRun").checked,
            message_top_k: Number(document.getElementById("messageTopK").value || 20),
            include_candidate_pool: document.getElementById("includePool").checked,
            allow_snapshot_fallback: document.getElementById("allowSnapshotFallback").checked
          })
        });
        show("pushResult", `channels=${(data.pushed_channels || []).join(",") || "-"}\ndata_source=${data.data_source}\n\n${data.message_text}`);
        loadStatus();
      } catch (error) {
        show("pushResult", formatError(error));
      }
    }

    document.addEventListener("DOMContentLoaded", () => {
      document.getElementById("allowFallback").addEventListener("change", updateAnalyzeHint);
      document.getElementById("analyzeTicker").addEventListener("change", loadDataStatus);
      document.getElementById("globalDate").addEventListener("change", loadDataStatus);
      window.addEventListener("scroll", scheduleStorylineDocking, { passive: true });
      window.addEventListener("resize", scheduleStorylineDocking);
      scheduleStorylineDocking();
    });
    loadStatus();
    loadDataStatus();
    loadTushareHealth();
