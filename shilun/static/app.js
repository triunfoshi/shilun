let runtimeStatus = {};
let latestSectorData = null;
let sectorRequestVersion = 0;
let selectedSectorIndex = 0;
let selectedLeaderView = "total";
let selectedLeaderDate = "";
let leaderTickerDraft = "";
let sectorProgressTimer = null;
let sectorProgressState = {version: 0, phase: "idle", startedAt: 0, phaseStartedAt: 0};
const TREND_POOL_STORAGE_KEY = "shilun.trendStrategyPool";
const CANDIDATE_VIEW_STORAGE_KEY = "shilun.candidateViewMode";
let selectedTrendPool = loadTrendPool();
let candidateViewMode = loadCandidateViewMode();
let latestCandidateRows = [];
let latestMarketData = null;
let latestAnalysisData = null;
let selectedMarketChart = "distribution";
let marketProgressTimer = null;
let marketProgressState = {phase: "idle", startedAt: 0, progress: 0};
const pretty = (value) => JSON.stringify(value, null, 2);

function loadTrendPool() {
    try {
        const raw = localStorage.getItem(TREND_POOL_STORAGE_KEY);
        const parsed = raw ? JSON.parse(raw) : {};
        return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch {
        return {};
    }
}

function saveTrendPool() {
    try {
        localStorage.setItem(TREND_POOL_STORAGE_KEY, JSON.stringify(selectedTrendPool));
    } catch {
        // 浏览器禁用 localStorage 时，不阻断主流程。
    }
}

function loadCandidateViewMode() {
    try {
        return localStorage.getItem(CANDIDATE_VIEW_STORAGE_KEY) === "expanded" ? "expanded" : "compact";
    } catch {
        return "compact";
    }
}

function setCandidateViewMode(mode) {
    candidateViewMode = mode === "expanded" ? "expanded" : "compact";
    try {
        localStorage.setItem(CANDIDATE_VIEW_STORAGE_KEY, candidateViewMode);
    } catch {
        // localStorage 不可用时，当前会话仍可切换。
    }
    renderCandidates(latestCandidateRows);
}

function selectedTrendPoolTickers() {
    return Object.keys(selectedTrendPool).filter(Boolean);
}

function normalizeTrendPoolTicker(ticker) {
    return String(ticker || "").trim().toUpperCase();
}

function updateTrendPoolControlState(ticker) {
    const normalized = normalizeTrendPoolTicker(ticker);
    if (!normalized) return;
    document.querySelectorAll(".trend-pool-check").forEach((input) => {
        if (normalizeTrendPoolTicker(input.dataset && input.dataset.ticker) === normalized) {
            input.checked = Boolean(selectedTrendPool[normalized]);
        }
    });
    document.querySelectorAll("[data-trend-pool-button]").forEach((button) => {
        if (normalizeTrendPoolTicker(button.dataset && button.dataset.ticker) !== normalized) return;
        const active = Boolean(selectedTrendPool[normalized]);
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
        button.textContent = active ? "已加入盘中监控" : "加入盘中监控";
    });
    const summary = document.getElementById("trendPoolSummary");
    if (summary) summary.innerHTML = renderTrendPoolSummary();
}

function setTrendPoolCandidate(payload, selected) {
    const ticker = normalizeTrendPoolTicker(payload && payload.ticker);
    if (!ticker) return;
    if (selected) {
        selectedTrendPool[ticker] = {
            ticker,
            name: payload.name || ticker,
            sector_name: payload.sector_name || "",
            source: payload.source || "manual",
            selected_at: new Date().toISOString(),
        };
    } else {
        delete selectedTrendPool[ticker];
    }
    saveTrendPool();
    updateTrendPoolControlState(ticker);
}

const chineseJsonLabels = {
    target_date: "同步目标日期",
    sync_trade_date: "实际同步交易日",
    start_date: "同步起始日期",
    end_date: "同步结束日期",
    calendar_start: "交易日历起始日期",
    calendar_end: "交易日历结束日期",
    daily_trade_date: "日线探测交易日",
    skipped: "是否跳过",
    message: "执行说明",
    stock_basic_count: "股票基础资料条数",
    trade_calendar_count: "交易日历条数",
    daily_bar_count: "日线记录总数",
    target_daily_bar_count: "日线记录总数",
    target_stock_daily_bar_count: "个股日线条数",
    daily_basic_count: "每日基础指标条数",
    moneyflow_count: "资金流向条数",
    benchmark_bar_count: "基准指数日线条数",
    failed_trade_dates: "失败交易日",
    synced_trade_dates: "已同步交易日",
    skipped_trade_dates: "跳过交易日",
    engine_version: "引擎版本",
    analysis_date: "分析日期",
    benchmark_ticker: "基准指数代码",
    benchmark_name: "基准指数名称",
    benchmark_meta: "基准指数说明",
    benchmark_options: "可选基准指数",
    benchmark_statuses: "基准指数覆盖状态",
    selected_benchmark: "当前基准状态",
    benchmark_ready: "当前基准是否就绪",
    latest_market_date: "当前基准最新日期",
    mongo_configured: "Mongo 是否配置",
    mongo_connected: "Mongo 是否连接",
    mongo_uri: "Mongo 地址",
    ticker: "证券代码",
    has_target_data: "目标日期是否有数据",
    latest_date: "最新数据日期",
    name: "名称",
    source: "数据来源",
    meaning: "用途说明",
    market_permission: "大盘权限",
    permission_label: "权限中文",
    permission_summary: "权限结论",
    action_permission: "操作权限",
    can_open: "是否允许开仓",
    max_new_position: "新增仓位上限",
    text: "动作说明",
    total_score: "总分",
    scores: "维度评分",
    trend_score: "趋势分",
    volume_score: "量能分",
    breadth_score: "广度分",
    theme_score: "主线分",
    risk_score: "风险分",
    metrics: "指标数据",
    levels: "支撑压力位",
    hard_triggers: "硬否决触发项",
    state_machine: "状态机规则",
    chart_data: "图表数据",
    theme_method: "主线识别口径",
    theme_candidates: "主线候选",
    data_quality: "数据质量",
    index_open: "指数开盘",
    index_high: "指数最高",
    index_low: "指数最低",
    index_close: "指数收盘",
    index_pct_chg: "指数涨跌幅",
    index_ma5: "指数MA5",
    index_ma10: "指数MA10",
    index_ma20: "指数MA20",
    ma5_slope: "MA5斜率",
    amount: "成交额",
    amount_prev: "昨日成交额",
    amount_change_vs_prev: "较昨日成交额变化",
    amount_ma5: "成交额5日均",
    amount_ma20: "成交额20日均",
    amount_ratio_5: "成交额相对5日均",
    amount_ratio_20: "成交额相对20日均",
    up_count: "上涨家数",
    down_count: "下跌家数",
    flat_count: "平盘家数",
    stock_count: "样本股票数",
    up_ratio: "上涨占比",
    up_count_ma5: "上涨家数5日均",
    up_count_ratio_ma5: "上涨家数相对5日均",
    limit_up_count: "涨停家数",
    limit_down_count: "跌停家数",
    limit_down_count_ma5: "跌停家数5日均",
    market_amount: "全市场成交额",
    market_amount_ma5: "全市场成交额5日均",
    market_amount_change_vs_prev: "全市场成交额较昨日变化",
    market_amount_ratio_ma5: "全市场成交额相对5日均",
    main_theme_status: "主线状态",
    main_theme_name: "代理主线",
    main_theme_return: "主线收益",
    main_theme_up_ratio: "主线上涨占比",
    main_theme_market_share: "主线成交额占比",
    weight_support_flag: "权重护盘标记",
    support_1: "第一支撑",
    support_1_source: "第一支撑依据",
    support_2: "第二支撑",
    support_2_source: "第二支撑依据",
    pressure_1: "第一压力",
    pressure_1_source: "第一压力依据",
    definition: "定义说明",
    formula: "计算公式",
    hard_veto: "硬否决规则",
    states: "状态定义",
    definition: "定义说明",
    summary: "总结",
    top_sectors: "今日强力板块",
    trend_sectors: "趋势板块",
    daily_leaders: "每日龙头",
    leader_summary: "总龙头榜",
    indicator_definitions: "指标定义",
    implementation_status: "实现状态",
    status: "状态",
    note: "备注",
    field: "字段",
    base_url: "网关地址",
    probe_dates: "探测日期",
    checks: "检查项",
    overall_status: "网关状态",
    overall_label: "网关状态说明",
    ok: "是否通过",
    count: "返回数量",
    row_count: "写入数量",
    cal_date: "日历日期",
    ts_code: "证券代码",
    recommendation: "处理建议",
    data_source: "数据来源",
    pushed_channels: "已推送通道",
    message_text: "消息内容"
};
const chineseJsonValues = {
    success: "成功",
    partial_error: "部分失败",
    error: "失败",
    skipped: "已跳过",
    available: "可用",
    unavailable: "不可用",
    ok: "正常",
    implemented: "已接入",
    proxy_only: "仅代理指标",
    data_pending: "待接入数据",
    manual_only: "仅人工维护",
    attack: "进攻",
    hold: "持有/观察",
    defense: "防守",
    empty: "空仓",
    yes: "是",
    no: "否",
    watch_only: "仅观察",
    no_heavy_new_position: "不允许新增重仓",
    confirmed_proxy: "确认代理主线",
    candidate_proxy: "候选代理主线",
    local_hotspot_proxy: "局部热点代理",
    weight_support_proxy: "权重护盘代理",
    moneyflow_data_pending: "资金流向待接入",
    index_daily: "指数日线",
    stock_basic: "股票基础资料",
    daily_basic: "每日基础指标",
    gateway_http: "网关 HTTP 探测",
    sdk_probe: "接口探测",
    trade_cal: "交易日历",
    daily: "日线数据"
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
    try {
        return prettyChineseJson(JSON.parse(value));
    } catch {
        return value;
    }
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

function marketDataGapMessage(error) {
    const raw = formatError(error);
    if (/No market breadth rows found/i.test(raw)) {
        return {
            title: "市场广度数据缺失",
            message: "当前日期缺少全市场个股日线，无法计算上涨/下跌家数、涨跌停分布和赚钱效应。这个报错不是单纯的“大盘指数未同步”，而是 PART1 需要的市场广度数据没有补齐。",
            action: "点击“同步缺失数据”会强制补齐当前日期附近的个股日线、资金流向和基准指数，然后自动重新计算。",
            raw,
            canSync: true
        };
    }
    if (/No benchmark\/index bars found|Benchmark latest date/i.test(raw)) {
        return {
            title: "基准指数日线缺失",
            message: "当前选择的基准指数日线没有同步到请求日期，趋势、均线和支撑压力无法计算。",
            action: "点击“同步缺失数据”会补齐当前日期的基准指数日线，并同时检查全市场日线缺口。",
            raw,
            canSync: true
        };
    }
    if (/No stock market bars found/i.test(raw)) {
        return {
            title: "全市场日线缺失",
            message: "当前日期没有可用于 PART1 的个股日线样本，市场广度、主线代理和风险代理都无法计算。",
            action: "点击“同步缺失数据”会按当前日期做强制增量同步。",
            raw,
            canSync: true
        };
    }
    return {
        title: "查询失败",
        message: raw,
        action: "请按错误提示处理后重试；如果是数据缺口，可以尝试同步缺失数据。",
        raw,
        canSync: false
    };
}

function renderMarketError(error) {
    const info = marketDataGapMessage(error);
    const syncButton = info.canSync
        ? `<button class="secondary" onclick="syncMarketMissingData()">同步缺失数据</button>`
        : "";
    return `
        <div class="interpretation-card market-error-card">
          <h3>${escapeHtml(info.title)}</h3>
          <p class="section-conclusion">${escapeHtml(info.message)}</p>
          <p class="hint">${escapeHtml(info.action)}</p>
          <div class="market-error-actions">
            ${syncButton}
            <button class="secondary" onclick="forceRunMarketPermission()">重新计算</button>
          </div>
          <details class="json-details">
            <summary>查看原始错误</summary>
            <pre>${escapeHtml(info.raw)}</pre>
          </details>
        </div>
      `;
}

const show = (id, value) => {
    document.getElementById(id).textContent = localizeJsonText(value);
};
const showHtml = (id, value) => {
    document.getElementById(id).innerHTML = value;
    scheduleStorylineDocking();
    // 内容重渲染后如果包含趋势图，自动挂十字准星交互。requestAnimationFrame 是为了
    // 让 DOM 布局稳定后再测量 tooltip 尺寸，避免第一次 hover 时 getBoundingClientRect
    // 拿到 0 值。
    if (typeof attachAllTrendChartCrosshairs === "function") {
        requestAnimationFrame(() => {
            try { attachAllTrendChartCrosshairs(); } catch (e) { /* silent */ }
        });
    }
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
    marketProgressState = {phase: "running", startedAt: Date.now(), progress: 6};
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
    } else if (sectorProgressState.phase === "precompute") {
        label = "正在同步板块预计算（60 日趋势窗口）";
        progress = Math.min(95, 5 + (phaseElapsed / 120) * 90);
        const remaining = Math.max(5, 120 - phaseElapsed);
        remainingText = `预计预计算剩余约 ${formatWaitDuration(remaining)}`;
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
    // #7：partial_error 状态下显示"重试龙头榜"按钮；其他状态隐藏
    const retryBtn = document.getElementById("sectorProgressRetryLeaders");
    if (retryBtn) {
        retryBtn.hidden = sectorProgressState.phase !== "partial_error";
    }
    sectorProgressState.progress = progress;
}

// #7：单独重试龙头榜（不重跑板块动向主计算，只跑近30日龙头榜的独立后台任务）。
// 前置：板块主结果已经在 latestSectorData 里；本函数只调 loadDailyLeaders 触发独立请求。
function retryLeadersOnly() {
    const dateValue = document.getElementById("globalDate").value;
    const benchmarkValue = document.getElementById("globalBenchmark").value;
    if (!latestSectorData) {
        // 主结果都没有，就退化到全量重查
        return runSectorTrends();
    }
    // UI 上把 phase 切回 leaders 状态，让进度条显示"生成龙头榜中"
    sectorProgressState.phase = "leaders";
    sectorProgressState.phaseStartedAt = Date.now();
    latestSectorData = {...latestSectorData, daily_leaders_loading: true, daily_leaders_error: null};
    if (typeof showHtml === "function") {
        showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
    }
    renderSectorProgressTick();
    void loadDailyLeaders(dateValue, benchmarkValue, sectorProgressState.version);
}

function setSectorPrecomputeButton(running, label) {
    const button = document.getElementById("sectorPrecomputeButton");
    if (!button) return;
    button.disabled = !!running;
    // 保留按钮里 <small class="button-hint"> 结构：running/idle 都拼一致的 HTML
    const hint = running
        ? '<small class="button-hint">跑全量 · 60-120s</small>'
        : '<small class="button-hint">跑全量 · 60-120s</small>';
    const primary = label || (running ? "同步中..." : "强制重新计算");
    button.innerHTML = `${escapeHtml(primary)} ${hint}`;
}

function startSectorProgress(version) {
    if (sectorProgressTimer) clearInterval(sectorProgressTimer);
    const now = Date.now();
    sectorProgressState = {version, phase: "main", startedAt: now, phaseStartedAt: now, progress: 5};
    const button = document.getElementById("sectorQueryButton");
    if (button) {
        button.disabled = true;
        button.textContent = "正在计算主结果...";
    }
    setSectorPrecomputeButton(true, "查询中...");
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
        button.innerHTML = '重新查询板块动向 <small class="button-hint">读缓存 · 秒回</small>';
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
        button.innerHTML = '查询板块动向 <small class="button-hint">读缓存 · 秒回</small>';
    }
    setSectorPrecomputeButton(false);
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
        button.innerHTML = '重新查询板块动向 <small class="button-hint">读缓存 · 秒回</small>';
    }
    setSectorPrecomputeButton(false);
    renderSectorProgressTick();
}

function startSectorPrecomputeProgress(version) {
    if (sectorProgressTimer) clearInterval(sectorProgressTimer);
    const now = Date.now();
    sectorProgressState = {version, phase: "precompute", startedAt: now, phaseStartedAt: now, progress: 5};
    const queryButton = document.getElementById("sectorQueryButton");
    if (queryButton) {
        queryButton.disabled = true;
        queryButton.textContent = "预计算中...";
    }
    setSectorPrecomputeButton(true, "计算中...");
    renderSectorProgressTick();
    sectorProgressTimer = setInterval(renderSectorProgressTick, 1000);
}

function finishSectorPrecomputeProgress(version, success) {
    if (sectorProgressState.version !== version) return;
    if (sectorProgressTimer) clearInterval(sectorProgressTimer);
    sectorProgressTimer = null;
    sectorProgressState.phase = success ? "complete" : "error";
    const queryButton = document.getElementById("sectorQueryButton");
    if (queryButton) {
        queryButton.disabled = false;
        queryButton.innerHTML = '重新查询板块动向 <small class="button-hint">读缓存 · 秒回</small>';
    }
    setSectorPrecomputeButton(false);
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
    document.getElementById("tab-analysis").scrollIntoView({behavior: "smooth", block: "start"});
    runStockPanel();
}

function scrollMarketFeature(id) {
    const node = document.getElementById(id);
    if (node) node.scrollIntoView({behavior: "smooth", block: "start"});
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
    permission: {
        title: "权限确认",
        subtitle: "交易前先确认风险边界",
        kicker: "研究主流程",
        description: "先确认今天可做什么、不能做什么，再进入大盘和板块推演。"
    },
    market: {
        title: "大盘计算",
        subtitle: "PART1 · 日线市场状态机",
        kicker: "市场研究",
        description: "用趋势、量能、广度、主线和风险，形成可执行的大盘权限。"
    },
    sectors: {
        title: "板块动向",
        subtitle: "PART2 · 强弱、趋势与龙头",
        kicker: "市场研究",
        description: "从板块扩散、资金口径和龙头/中军候选中识别市场主线。"
    },
    analysis: {
        title: "单票分析",
        subtitle: "个股结构与策略边界",
        kicker: "个股研究",
        description: "输入股票代码，查看行情、结构、支撑压力和执行边界。"
    },
    intraday: {
        title: "盘中监控",
        subtitle: "实时行情 · 触位验证",
        kicker: "研究主流程",
        description: "把 PART1 预设的关键位和候选票信号，用实时行情做在线验证。"
    },
    push: {
        title: "日报推送",
        subtitle: "研究结论交付",
        kicker: "系统与交付",
        description: "先预览，再将结构化日报发送到已配置通道。"
    },
    data: {
        title: "数据同步",
        subtitle: "Mongo 数据水位与网关",
        kicker: "系统与交付",
        description: "确认交易日数据、增量缺口与 Tushare 网关健康状态。"
    },
    system: {
        title: "系统状态",
        subtitle: "运行依赖检查",
        kicker: "系统与交付",
        description: "集中检查 Mongo、消息通道和运行时依赖。"
    },
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
    if (tabId === "analysis") return runStockPanel();
    if (tabId === "intraday") return runIntraday();
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
        if (max - min < 0.001) {
            min -= 0.001;
            max += 0.001;
        }
        return {min, max};
    };
    const primaryBounds = bounds(primaryValues);
    const secondaryBounds = bounds(secondaryValues);
    const points = rows.map((row, index) => {
        const x = rows.length === 1 ? 50 : 9 + (index * 82) / (rows.length - 1);
        const primary = Number(row[config.primaryKey] || 0);
        const secondary = Number(row[config.secondaryKey] || 0);
        const y1 = 12 + ((primaryBounds.max - primary) / (primaryBounds.max - primaryBounds.min)) * 68;
        const y2 = 12 + ((secondaryBounds.max - secondary) / (secondaryBounds.max - secondaryBounds.min)) * 68;
        return {row, x, y1, y2, primary, secondary, index};
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
          ${points.map((item) => {
        const alignClass = item.x < 18 ? "align-left" : item.x > 82 ? "align-right" : "";
        return `
            <button type="button" class="market-chart-point ${alignClass}" style="left:${item.x}%;top:${item.y1}%" aria-label="${escapeHtml(item.row.date)} 数据详情">
              <span class="market-chart-tooltip">
                <strong>${escapeHtml(item.row.date)}</strong>
                <span>${escapeHtml(config.primaryLabel)}</span><b>${escapeHtml(config.primaryFormat(item.primary))}</b>
                <span>${escapeHtml(config.secondaryLabel)}</span><b>${escapeHtml(config.secondaryFormat(item.secondary))}</b>
              </span>
            </button>
          `;
    }).join("")}
          <span class="market-chart-start">${escapeHtml(String(rows[0].date || "").slice(5))}</span>
          <span class="market-chart-end">${escapeHtml(String(rows[rows.length - 1].date || "").slice(5))}</span>
        </div>
      `;
}

function renderMarketChart(data) {
    const chartData = data.chart_data || {};
    const breadthMap = new Map((chartData.breadth_series || []).map((item) => [item.date, item]));
    const rows = (chartData.benchmark_series || []).map((item) => ({...item, ...(breadthMap.get(item.date) || {})}));
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

function renderMarketGate(gate) {
    if (!gate) return "";
    const stateClass = {
        attack: "gate-attack",
        hold: "gate-hold",
        defense: "gate-defense",
        empty: "gate-empty",
    }[gate.state] || "gate-hold";

    const yes = (v) => v ? "✅ 允许" : "❌ 禁止";
    const dm = gate.signal_downgrade_map || {};
    const dowgradeRows = Object.entries(dm)
        .filter(([src, dst]) => src !== dst)
        .map(([src, dst]) => `<span class="gate-downgrade-item">${escapeHtml(src)} → <b>${escapeHtml(dst)}</b></span>`)
        .join(" ") || "<span class='muted'>无降级（全部保留原始信号）</span>";

    const exc = gate.high_quality_exception;
    const excBlock = exc && exc.enabled ? `
        <div class="gate-exception">
          <strong>⭐ 高质量例外</strong>
          <p>${escapeHtml(exc.note || exc.condition || "")}</p>
        </div>` : "";

    const holdings = gate.holdings_advice || {};
    const holdingsAlert = holdings.check_stop_loss || holdings.reduce_percentage;

    return `
        <section class="market-feature market-gate-card ${stateClass}">
          <div class="feature-kicker">Market Gate · 机器可执行闸门</div>
          <div class="gate-headline">
            <h3>${escapeHtml(gate.state_label || gate.state)}${gate.hard_veto_active ? ' <span class="gate-hard-veto">硬否决</span>' : ''}</h3>
            <p class="gate-reason">${escapeHtml(gate.gate_reason || "")}</p>
          </div>
          <div class="gate-rules-grid">
            <div class="gate-rule-cell"><span class="metric-label">新开仓</span><strong class="value-emphasis">${yes(gate.allow_new_position)}</strong></div>
            <div class="gate-rule-cell"><span class="metric-label">加仓</span><strong class="value-emphasis">${yes(gate.allow_add_position)}</strong></div>
            <div class="gate-rule-cell"><span class="metric-label">市场乘数</span><strong class="value-emphasis">${escapeHtml(String(gate.market_multiplier ?? "-"))}</strong><p class="muted">影响候选排序</p></div>
            <div class="gate-rule-cell"><span class="metric-label">仓位建议</span><strong class="value-emphasis">${escapeHtml(String((gate.size_hint ?? 0) * 100).slice(0, 4))}%</strong><p class="muted">建议单只仓位</p></div>
          </div>
          <div class="gate-downgrade-block">
            <span class="metric-label">PART3 信号降级映射</span>
            <div class="gate-downgrade-list">${dowgradeRows}</div>
          </div>
          ${excBlock}
          <div class="gate-holdings ${holdingsAlert ? 'gate-holdings-alert' : ''}">
            <span class="metric-label">持仓建议 ${holdingsAlert ? '⚠️' : ''}</span>
            <p>${escapeHtml(holdings.note || "无特殊建议")}</p>
          </div>
        </section>
      `;
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
    if (!text) return {primary: "", chips: []};
    // 用中英文逗号、顿号、斜杠、空格"，"作分隔
    const parts = text.split(/[，,、]\s*/).filter(Boolean);
    if (parts.length <= 1) return {primary: text, chips: []};
    // 第一段当主数字，后面拆 chip
    return {primary: parts[0], chips: parts.slice(1)};
}

function parseLevelIndicator(indicator, value) {
    const text = String(indicator || "");
    const match = text.match(/^(支撑|压力|目标)\s*([0-9.]+)(?:（([^）]+)）)?/);
    if (!match) return null;
    const kind = match[1];
    const price = match[2];
    const significance = match[3] || "";
    const label = String(value || "").trim();
    return {kind, price, significance, label};
}

function levelActionText(level) {
    const label = level.label || "";
    if (level.kind === "目标") {
        return "接近目标位时优先兑现或收紧止盈，不把目标位当成追高理由。";
    }
    if (level.kind === "压力") {
        if (/突破|调整结束|第5浪|开始/.test(label)) return "只有放量站上才算确认；冲高回落则不追，按压力处理。";
        return "压力位附近先观察承接，放量突破才升级，缩量上冲容易回落。";
    }
    if (/破位|失效|不得|核心保护/.test(label)) {
        return "这是风险边界；放量跌破要降低仓位或重新评估结构。";
    }
    if (/目标|C浪|低点/.test(label)) {
        return "这是止跌观察位；先看是否企稳，不用单独作为抄底信号。";
    }
    return "回踩不破可观察，放量跌破则说明支撑失效。";
}

function renderLevelCard(row, level) {
    const klass = [
        "insight-row-card",
        "level-focus-card",
        level.kind === "支撑" ? "level-support" : "",
        level.kind === "压力" ? "level-resistance" : "",
        level.kind === "目标" ? "level-target" : "",
    ].filter(Boolean).join(" ");
    const title = [level.kind, level.label].filter(Boolean).join(" · ");
    const meta = level.significance ? `重要度 ${level.significance}` : "关键价位";
    return `
        <article class="${klass}">
          <span class="metric-label">${escapeHtml(title || level.kind)}</span>
          <strong class="value-emphasis level-price">${escapeHtml(level.price)}</strong>
          <div class="level-action"><b>动作</b><span>${escapeHtml(levelActionText(level))}</span></div>
          <p><strong>${escapeHtml(meta)}</strong> · ${escapeHtml(row.judgement || "")}</p>
        </article>
      `;
}

function renderMarketInsight(section, index) {
    const number = String(index + 1).padStart(2, "0");
    const isPatternSection = String(section.title || "").includes("今日形态") || String(section.title || "").includes("波浪结构");
    const rawRows = section.rows || [];
    const displayRows = isPatternSection
        ? [
            ...rawRows.filter((row) => !String(row.indicator || "").includes("⚠️")),
            ...rawRows.filter((row) => String(row.indicator || "").includes("⚠️")),
        ]
        : rawRows;
    // 从 section row[0].value 里解析 signal_type（格式：信号性质：bullish）
    const rawSignalType = isPatternSection && rawRows && rawRows[0]
        ? String(rawRows[0].value || "").replace("信号性质：", "").trim()
        : "";
    const patternBadgeCls = {
        bullish: "pattern-bullish",
        bearish: "pattern-bearish",
        warning: "pattern-warning",
        neutral: "pattern-neutral"
    }[rawSignalType] || "pattern-neutral";
    const patternLabel = {
        bullish: "看多",
        bearish: "看空",
        warning: "预警",
        neutral: "中性"
    }[rawSignalType] || rawSignalType;

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
            ${displayRows.map((row, i) => {
        const indicator = String(row.indicator || "");

        // 波浪结构主卡：宽幅叙述卡
        if (isPatternSection && indicator.includes("🌊")) {
            return `
                  <article class="insight-row-card insight-card-narrative wave-headline-card" style="grid-column: 1 / -1;">
                    <span class="metric-label">${escapeHtml(indicator)}</span>
                    <strong class="value-emphasis">${escapeHtml(String(row.value || "").split("，")[0])}</strong>
                    <div class="value-chips">${String(row.value || "").split("，").slice(1).map((c) => `<span class="value-chip">${escapeHtml(c.trim())}</span>`).join("")}</div>
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
                    <strong class="value-emphasis">${escapeHtml(String(row.value || "").replace("信号性质：", "").trim())}</strong>
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
        const levelInfo = parseLevelIndicator(indicator, row.value);
        if (isPatternSection && levelInfo) return renderLevelCard(row, levelInfo);

        const {primary, chips} = parseInsightValue(row.value);
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
            ${renderMarketGate(data.market_gate)}
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
    // 用 span + tabindex 实现"hover 悬停 + 键盘 focus" 双触发，
    // 替代 <details> 的点击语义。CSS 里 .info-popover:hover / :focus-within .info-panel 显示。
    return `
        <span class="info-popover" tabindex="0" role="button" aria-label="定义说明">
          <span class="info-popover-marker">!</span>
          <span class="info-panel">${escapeHtml(text)}<a href="#sector-indicator-definitions">查看指标口径</a></span>
        </span>
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

function sectorMainlineScore(sector) {
    const scores = sector.scores || {};
    const metrics = sector.metrics || {};
    return scores.sector_mainline_score ?? sector.trend_sort_score ?? metrics.trend_sort_score ?? sector.sector_mainline_score ?? scores.sector_score ?? 0;
}

function sectorHealthScore(sector) {
    return (sector.scores && sector.scores.sector_score) ?? 0;
}
function scoreText(value) {
      if (value === null || value === undefined || value === "") return "-";
      const number = Number(value);
      if (!Number.isFinite(number)) return String(value);
      return String(Math.round(number * 100) / 100);
    }
function sectorStrengthClass(sector) {
    const score = Number(sectorMainlineScore(sector) || 0);
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
    if (node) node.scrollIntoView({behavior: "smooth", block: "start"});
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
              <small>${escapeHtml(sector.stage_label || "-")} · 主线分 ${escapeHtml(scoreText(sectorMainlineScore(sector)))}</small>
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
        "用主线分决定板块动向排序；当日健康分只作为当天强弱、广度和核心反馈的辅助观察。",
        `主线分 ${escapeHtml(scoreText(sectorMainlineScore(sector)))}，当日健康分 ${escapeHtml(scoreText(sectorHealthScore(sector)))}，5日收益 ${colorPercent(sector.metrics && sector.metrics.return_5d)}，相对大盘 ${colorPercent(sector.metrics && sector.metrics.relative_return_5d)}`,
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
    const leader = sectors[0];
    const leaderHeadline = `最强板块：${leader.sector_name}，阶段 ${leader.stage_label || "-"}，主线风 ${scoreText(sectorMainlineScore(leader))}，当日健康分 ${scoreText(sectorHealthScore(leader))}。`;
    const leaderConclusion = `${leader.sector_name} 当前按主线分排序第一；动作建议：${leader.action || "-"}`;
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
            <h2 class="market-headline">${escapeHtml(leaderHeadline)}</h2>
            <p class="market-subtitle">${escapeHtml(leaderConclusion)}</p>
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
    const visibleRows = rows.slice(-60);
    const chart = {width: 640, height: 250, left: 58, right: 18, top: 22, bottom: 46};
    const innerWidth = chart.width - chart.left - chart.right;
    const innerHeight = chart.height - chart.top - chart.bottom;
    let cumulative = 0;
    const enriched = visibleRows.map((point, index) => {
        const daily = Number(point.relative_return_1d || 0);
        cumulative += daily;
        return {point, index, daily, cumulative};
    });
    const ySource = enriched.flatMap((item) => [item.daily, item.cumulative, 0]);
    let minValue = Math.min(...ySource);
    let maxValue = Math.max(...ySource);
    if (maxValue - minValue < 0.004) {
        maxValue += 0.002;
        minValue -= 0.002;
    }
    const pad = (maxValue - minValue) * 0.12;
    minValue -= pad;
    maxValue += pad;
    const range = maxValue - minValue || 1;
    const xForIndex = (index) => chart.left + (enriched.length === 1 ? innerWidth / 2 : (index * innerWidth) / (enriched.length - 1));
    const yForValue = (value) => chart.top + ((maxValue - value) / range) * innerHeight;
    const zeroY = yForValue(0);
    const positions = enriched.map((item) => ({
        ...item,
        x: xForIndex(item.index),
        yDaily: yForValue(item.daily),
        yCumulative: yForValue(item.cumulative),
    }));
    const linePath = positions.map((item, index) => `${index ? "L" : "M"}${item.x.toFixed(2)} ${item.yCumulative.toFixed(2)}`).join(" ");
    const barWidth = Math.max(3, Math.min(9, innerWidth / Math.max(1, enriched.length) * 0.46));
    const yTicks = [minValue, (minValue + maxValue) / 2, maxValue];
    const tickStep = Math.max(1, Math.ceil(enriched.length / 5));
    const xTickIndexes = Array.from(new Set([
        0,
        ...enriched.map((_, index) => index).filter((index) => index > 0 && index < enriched.length - 1 && index % tickStep === 0),
        enriched.length - 1,
    ]));
    const maxDailyIndex = positions.reduce((best, item) => item.daily > positions[best].daily ? item.index : best, 0);
    const minDailyIndex = positions.reduce((best, item) => item.daily < positions[best].daily ? item.index : best, 0);
    const latestIndex = positions.length - 1;
    const keyIndexes = new Set([maxDailyIndex, minDailyIndex, latestIndex]);
    // 每个点的完整数据（供十字准星 mousemove 查找最近点用）。
    // JSON.stringify 后放在 data-* 里，交给 attachTrendChartCrosshair 反序列化。
    const pointsData = positions.map(({point, daily, cumulative, x, yCumulative, index}) => ({
        i: index,
        x, y: yCumulative,
        date: String(point.date || ""),
        return_1d: point.return_1d,
        benchmark_return_1d: point.benchmark_return_1d,
        daily, cumulative,
        up_ratio: point.up_ratio,
        amount_ratio_5: point.amount_ratio_5,
        main_net_inflow: point.main_net_inflow,
        strong: !!point.strong,
        repair: !!point.repair,
        resonance: !!point.resonance,
    }));
    const chartId = `trend-chart-${Math.random().toString(36).slice(2, 10)}`;
    const chartMeta = {
        chartWidth: chart.width, chartHeight: chart.height,
        left: chart.left, right: chart.right, top: chart.top, bottom: chart.bottom,
        innerWidth, innerHeight,
    };
    return `
        <div class="trend-line-chart" id="${chartId}" data-trend-points='${escapeHtmlAttr(JSON.stringify(pointsData))}' data-trend-meta='${escapeHtmlAttr(JSON.stringify(chartMeta))}'>
          <svg class="trend-line-svg" viewBox="0 0 ${chart.width} ${chart.height}" role="img" aria-label="60日趋势图：柱状为单日相对收益，折线为累计相对收益">
            <text class="trend-axis-title y-title" x="16" y="${chart.top + innerHeight / 2}" transform="rotate(-90 16 ${chart.top + innerHeight / 2})">相对大盘收益</text>
            <text class="trend-axis-title x-title" x="${chart.left + innerWidth / 2}" y="${chart.height - 6}">交易日期</text>
            ${yTicks.map((tick) => `
              <line class="trend-grid-line" x1="${chart.left}" y1="${yForValue(tick).toFixed(2)}" x2="${chart.left + innerWidth}" y2="${yForValue(tick).toFixed(2)}"></line>
              <text class="trend-y-tick" x="${chart.left - 8}" y="${(yForValue(tick) + 4).toFixed(2)}">${escapeHtml(formatPercent(tick))}</text>
            `).join("")}
            <line class="trend-axis-line" x1="${chart.left}" y1="${chart.top + innerHeight}" x2="${chart.left + innerWidth}" y2="${chart.top + innerHeight}"></line>
            <line class="trend-axis-line" x1="${chart.left}" y1="${chart.top}" x2="${chart.left}" y2="${chart.top + innerHeight}"></line>
            <line class="trend-zero-line" x1="${chart.left}" y1="${zeroY.toFixed(2)}" x2="${chart.left + innerWidth}" y2="${zeroY.toFixed(2)}"></line>
            ${positions.map((item) => {
        const y = Math.min(item.yDaily, zeroY);
        const height = Math.max(2, Math.abs(item.yDaily - zeroY));
        return `<rect class="trend-bar ${item.daily < 0 ? "is-negative" : "is-positive"}" x="${(item.x - barWidth / 2).toFixed(2)}" y="${y.toFixed(2)}" width="${barWidth.toFixed(2)}" height="${height.toFixed(2)}"></rect>`;
    }).join("")}
            <path class="trend-line-path" d="${linePath}"></path>
            ${positions.map((item) => `<circle class="trend-line-dot ${item.daily < 0 ? "is-negative" : ""}" cx="${item.x.toFixed(2)}" cy="${item.yCumulative.toFixed(2)}" r="3"></circle>`).join("")}
            ${xTickIndexes.map((index) => {
        const item = positions[index];
        return `<text class="trend-x-tick" x="${item.x.toFixed(2)}" y="${chart.top + innerHeight + 20}">${escapeHtml(String(item.point.date || "").slice(5))}</text>`;
    }).join("")}
            <g class="trend-chart-legend">
              <line x1="${chart.left}" y1="12" x2="${chart.left + 20}" y2="12" class="legend-line"></line>
              <text x="${chart.left + 26}" y="15">累计相对</text>
              <rect x="${chart.left + 96}" y="6" width="10" height="10" class="trend-bar is-positive"></rect>
              <text x="${chart.left + 110}" y="15">单日相对</text>
            </g>
            <!-- 十字准星（默认隐藏，mousemove 时更新位置） -->
            <line class="trend-crosshair-v" x1="0" y1="${chart.top}" x2="0" y2="${chart.top + innerHeight}" style="display:none"></line>
            <line class="trend-crosshair-h" x1="${chart.left}" y1="0" x2="${chart.left + innerWidth}" y2="0" style="display:none"></line>
            <circle class="trend-crosshair-dot" r="4" cx="0" cy="0" style="display:none"></circle>
            <!-- 关键点静态标签（最高/最低/最后） -->
            ${[...keyIndexes].map((idx) => {
        const item = positions[idx];
        const isLeftEdge = idx === 0;
        const isRightEdge = idx === latestIndex;
        const anchor = isLeftEdge ? "start" : isRightEdge ? "end" : "middle";
        const dx = isLeftEdge ? 4 : isRightEdge ? -4 : 0;
        // 标签放在数据点上方 8px
        return `<text class="trend-key-label" text-anchor="${anchor}" x="${(item.x + dx).toFixed(2)}" y="${(item.yCumulative - 8).toFixed(2)}">${escapeHtml(formatPercent(item.daily))}</text>`;
    }).join("")}
            <!-- hitzone：透明矩形捕获整个绘图区的 mousemove -->
            <rect class="trend-chart-hitzone" x="${chart.left}" y="${chart.top}" width="${innerWidth}" height="${innerHeight}" fill="transparent"></rect>
          </svg>
          <!-- 十字准星联动的 tooltip，绝对定位；JS 更新 left/top 和内容 -->
          <div class="trend-crosshair-tooltip" style="display:none">
            <strong class="tooltip-date">-</strong>
            <div class="tooltip-grid">
              <span>板块涨跌</span><span class="tt-return"></span>
              <span>对比指数</span><span class="tt-bench"></span>
              <span>单日相对</span><span class="tt-daily"></span>
              <span>累计相对</span><span class="tt-cum"></span>
              <span>上涨占比</span><span class="tt-up"></span>
              <span>量能/5日均</span><span class="tt-vol"></span>
              <span>主力净流入</span><span class="tt-flow"></span>
            </div>
            <div class="tooltip-signals"></div>
          </div>
        </div>
      `;
}

// escapeHtml 只处理常见 HTML 转义；attribute 里额外需要处理引号。
function escapeHtmlAttr(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/'/g, "&#39;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

// 给指定 chart 容器挂上十字准星交互。renderSectorTrends 完成后调用。
// 逻辑：mousemove 时把鼠标坐标转到 SVG viewBox 坐标系，二分/线性查找最近的 point，
// 更新准星位置、准星红点位置、tooltip 内容与位置；mouseleave 时全部隐藏。
function attachTrendChartCrosshair(container) {
    if (!container || container.dataset.crosshairBound === "1") return;
    const raw = container.getAttribute("data-trend-points");
    const rawMeta = container.getAttribute("data-trend-meta");
    if (!raw || !rawMeta) return;
    let points, meta;
    try {
        points = JSON.parse(raw);
        meta = JSON.parse(rawMeta);
    } catch (e) {
        return;
    }
    if (!points.length) return;
    const svg = container.querySelector(".trend-line-svg");
    const hitzone = container.querySelector(".trend-chart-hitzone");
    const crossV = container.querySelector(".trend-crosshair-v");
    const crossH = container.querySelector(".trend-crosshair-h");
    const crossDot = container.querySelector(".trend-crosshair-dot");
    const tooltip = container.querySelector(".trend-crosshair-tooltip");
    if (!svg || !hitzone || !tooltip) return;
    container.dataset.crosshairBound = "1";

    function svgCoordFromEvent(evt) {
        const rect = svg.getBoundingClientRect();
        const xRatio = (evt.clientX - rect.left) / rect.width;
        const yRatio = (evt.clientY - rect.top) / rect.height;
        return {
            svgX: xRatio * meta.chartWidth,
            svgY: yRatio * meta.chartHeight,
            containerX: evt.clientX - container.getBoundingClientRect().left,
            containerY: evt.clientY - container.getBoundingClientRect().top,
        };
    }

    function findNearest(svgX) {
        let bestIdx = 0;
        let bestDist = Infinity;
        for (let i = 0; i < points.length; i++) {
            const d = Math.abs(points[i].x - svgX);
            if (d < bestDist) { bestDist = d; bestIdx = i; }
        }
        return points[bestIdx];
    }

    function formatPct(v) {
        if (v == null || Number.isNaN(Number(v))) return "-";
        const num = Number(v);
        const sign = num > 0 ? "+" : "";
        return `${sign}${(num * 100).toFixed(2)}%`;
    }
    function formatRatioLocal(v) {
        if (v == null || Number.isNaN(Number(v))) return "-";
        return `${Number(v).toFixed(2)}x`;
    }
    function formatFlowLocal(v) {
        if (v == null || Number.isNaN(Number(v))) return "-";
        const wan = Number(v) / 10000;
        return wan >= 100 ? `${(wan / 10000).toFixed(2)}亿` : `${wan.toFixed(2)}万`;
    }
    function toneClass(v) {
        const num = Number(v);
        if (!Number.isFinite(num)) return "";
        if (num > 0) return "tt-up-color";
        if (num < 0) return "tt-down-color";
        return "";
    }

    function updateAt(evt) {
        const {svgX, containerX, containerY} = svgCoordFromEvent(evt);
        if (svgX < meta.left || svgX > meta.left + meta.innerWidth) {
            hide();
            return;
        }
        const p = findNearest(svgX);
        // 更新准星：竖线走 p.x，横线走 p.y
        crossV.setAttribute("x1", p.x); crossV.setAttribute("x2", p.x);
        crossV.style.display = "";
        crossH.setAttribute("y1", p.y); crossH.setAttribute("y2", p.y);
        crossH.style.display = "";
        crossDot.setAttribute("cx", p.x); crossDot.setAttribute("cy", p.y);
        crossDot.style.display = "";
        // tooltip 内容
        tooltip.querySelector(".tooltip-date").textContent = `${p.date} · 数据明细`;
        const grid = tooltip.querySelector(".tooltip-grid");
        const spans = grid.querySelectorAll("span");
        // spans 是 label/value 交替，我们只更新 value（class 命名的那些）
        const setter = (cls, txt, tone) => {
            const el = grid.querySelector(`.${cls}`);
            if (!el) return;
            el.textContent = txt;
            el.className = `${cls}${tone ? " " + tone : ""}`;
        };
        setter("tt-return", formatPct(p.return_1d), toneClass(p.return_1d));
        setter("tt-bench", formatPct(p.benchmark_return_1d), toneClass(p.benchmark_return_1d));
        setter("tt-daily", formatPct(p.daily), toneClass(p.daily));
        setter("tt-cum", formatPct(p.cumulative), toneClass(p.cumulative));
        setter("tt-up", formatPct(p.up_ratio), "");
        setter("tt-vol", formatRatioLocal(p.amount_ratio_5), "");
        setter("tt-flow", formatFlowLocal(p.main_net_inflow), "");
        const signals = [
            p.strong ? "强势" : "",
            p.repair ? "修复" : "",
            p.resonance ? "共振" : "",
        ].filter(Boolean);
        const signalsEl = tooltip.querySelector(".tooltip-signals");
        signalsEl.innerHTML = signals.length
            ? signals.map((s) => `<span class="tt-signal">${s}</span>`).join("")
            : "";
        // tooltip 位置：默认放数据点右侧；如果太靠右会溢出，就放左侧
        const containerRect = container.getBoundingClientRect();
        tooltip.style.display = "";
        // 先测量 tooltip 实际宽度
        const ttRect = tooltip.getBoundingClientRect();
        const tooltipWidth = ttRect.width || 180;
        const padding = 12;
        let leftPx = containerX + 14;
        if (leftPx + tooltipWidth + padding > containerRect.width) {
            leftPx = containerX - tooltipWidth - 14;
        }
        if (leftPx < padding) leftPx = padding;
        let topPx = containerY - 24;
        if (topPx < padding) topPx = padding;
        if (topPx + (ttRect.height || 160) > containerRect.height) {
            topPx = containerRect.height - (ttRect.height || 160) - padding;
        }
        tooltip.style.left = `${leftPx}px`;
        tooltip.style.top = `${topPx}px`;
    }

    function hide() {
        crossV.style.display = "none";
        crossH.style.display = "none";
        crossDot.style.display = "none";
        tooltip.style.display = "none";
    }

    // 用 svg 整体作为事件源（而不是 hitzone），保证鼠标在坐标轴留白也能触发。
    svg.addEventListener("mousemove", updateAt);
    svg.addEventListener("mouseleave", hide);
}

// 页面渲染完趋势图后，扫描所有 trend-line-chart 挂 crosshair。
function attachAllTrendChartCrosshairs() {
    document.querySelectorAll(".trend-line-chart[data-trend-points]").forEach(attachTrendChartCrosshair);
}

function trendReferenceTitle(label, ref) {
    if (!ref || typeof ref !== "object" || !ref.sample_count) return "";
    const median = ref.median === null || ref.median === undefined ? "-" : Number(ref.median).toFixed(4);
    const mean = ref.mean === null || ref.mean === undefined ? "-" : Number(ref.mean).toFixed(4);
    const raw = ref.raw_value === null || ref.raw_value === undefined ? "-" : Number(ref.raw_value).toFixed(4);
    const z = ref.z_score === null || ref.z_score === undefined ? "-" : Number(ref.z_score).toFixed(2);
    return `${label}：当前值 ${raw}；全板块中位数 ${median}，均值 ${mean}，样本 ${ref.sample_count}，标准化 ${z}`;
}

function renderTrendScoreBreakdown(item) {
    const scores = item.scores || {};
    const references = scores.score_references || {};
    const parts = [
        ["20日相对", scores.excess_return_20d_score],
        ["60日相对", scores.excess_return_60d_score],
        ["近5日跑赢", scores.outperform_days_5_score],
        ["成交额", scores.sector_amount_ratio_score],
        ["龙头中军", scores.leader_zhongjun_score],
    ];
    return `
        <div class="trend-score-breakdown" aria-label="主线分拆解">
          ${parts.map(([label, value]) => {
        const refKey = label === "20日相对" ? "20日相对强度"
            : label === "60日相对" ? "60日相对强度"
                : label === "成交额" ? "成交额活跃"
                    : label === "龙头中军" ? "龙头中军反馈"
                        : label;
        const title = trendReferenceTitle(refKey, references[refKey]);
        return `
            <span class="trend-score-chip" ${title ? `title="${escapeHtml(title)}"` : ""}>
              <b>${escapeHtml(label)}</b>
              <strong>${escapeHtml(value === null || value === undefined ? "-" : Number(value).toFixed(1))}</strong>
            </span>`;
    }).join("")}
        </div>
      `;
}

function renderTrendSectors(data) {
    const trends = data.trend_sectors || [];
    const lookback = data.trend_lookback_days || (trends[0] && trends[0].metrics && trends[0].metrics.lookback_days) || 60;
    const engineVersion = String(data.engine_version || "");
    const stalePayload = engineVersion === "market_sector_v1" || trends.some((item) => {
        const scores = item.scores || {};
        return !scores.score_breakdown || !scores.score_references || !item.sector_state_label || item.sector_mainline_score === null || item.sector_mainline_score === undefined;
    });
    return `
        <section id="sector-trends" class="sector-feature">
          <div class="feature-kicker">功能区二 · ${escapeHtml(lookback)}-Day Trend</div>
          <div class="feature-header">
            <div>
              <h3>趋势板块</h3>
              <p>用过去 ${escapeHtml(lookback)} 个交易日的 20日相对强度、60日相对强度、近5日跑赢、成交额活跃和龙头/中军反馈综合排序；5日收益只作为短线热度参考。</p>
            </div>
          </div>
          ${stalePayload ? `
            <div class="stale-cache-warning">
              当前板块趋势仍是旧缓存或旧后台输出（${escapeHtml(engineVersion || "unknown")}），缺少 MA5 v0.2 相对参考分。请重启后台后点击“预计算板块数据”，再重新查询。
            </div>
          ` : ""}
          ${trends.length ? `
            <div class="trend-board">
              ${trends.slice(0, 6).map((item, index) => `
                <article class="trend-card">
                  <h4>#${escapeHtml(item.mainline_rank || index + 1)} ${escapeHtml(item.sector_name)} · ${escapeHtml(item.trend_label || item.sector_state_label || "等待重算")}</h4>
                  <p class="rankline">
                    主线分 ${escapeHtml(item.sector_mainline_score ?? item.trend_score ?? "-")}，状态 ${escapeHtml(item.sector_state_label || "需重新预计算")}，乘数 ${escapeHtml(item.sector_multiplier || "-")}；20日相对大盘 ${colorPercent(item.metrics && item.metrics.relative_return_20d)}，60日相对大盘 ${colorPercent(item.metrics && item.metrics.relative_return_60d)}，5日相对 ${colorPercent(item.metrics && item.metrics.relative_return_5d)}
                  </p>
                  ${renderTrendScoreBreakdown(item)}
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
              <p>默认折叠展示，避免挤占主分析区；需要核对公式、数据来源和代理口径时再展开。</p>
            </div>
          </div>
          <details class="knowledge-collapsible">
            <summary>
              <span>展开指标边界、公式口径和主题层说明</span>
              <small>当前板块仍使用 Tushare 行业代理；CPO、存储芯片等概念主题后续单独建设。</small>
            </summary>
            <div class="knowledge-grid">
              <article class="knowledge-panel full concept-roadmap-panel">
                <h4>概念/主题层待建设</h4>
                <p class="hint">Tushare 行业更适合传统行业统计，不适合直接表达 CPO、存储芯片、算力、铜缆等交易主线。下一步应新增“概念/主题映射 + 人工主线池”，而不是用 AKShare 涨停池行业简单替换。</p>
              </article>
              <article class="knowledge-panel">
                <h4>指标边界</h4>
                ${renderDataQuality(data.data_quality)}
              </article>
              <article class="knowledge-panel">
                <h4>指标口径</h4>
                ${renderIndicatorDefinitions(data.indicator_definitions)}
              </article>
            </div>
          </details>
        </section>
      `;
}

function formatCandidatePrice(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return "-";
    return n.toFixed(2);
}

function firstFinitePositive(...values) {
    for (const value of values) {
        const n = Number(value);
        if (Number.isFinite(n) && n > 0) return n;
    }
    return null;
}

function candidateDisplayPlan(c) {
    const close = firstFinitePositive(c.close);
    const ma5 = firstFinitePositive(c.ma5);
    const ma8 = firstFinitePositive(c.ma8, ma5);
    const stop = firstFinitePositive(c.stop_loss);
    const support = firstFinitePositive(c.support_price, ma8, ma5, stop ? stop / 0.98 : null);
    const buy = firstFinitePositive(c.predicted_buy_price, c.entry_price, support, close);
    const pressure = firstFinitePositive(c.pressure_price, c.expected_sell_price, close ? close * 1.08 : null);
    const target = firstFinitePositive(c.expected_sell_price, pressure, buy ? buy * 1.08 : null);
    return {
        buy,
        buyLabel: c.predicted_buy_label || (c.predicted_buy_price ? "" : "兜底估算"),
        support,
        supportSource: c.support_source || (c.support_price ? "" : (ma8 ? "MA8/MA5兜底" : "兜底")),
        pressure,
        pressureSource: c.pressure_source || (c.pressure_price ? "" : "8%目标兜底"),
        ma8,
        target,
    };
}

function renderCandidateTradePlan(c) {
    const plan = candidateDisplayPlan(c);
    const buy = formatCandidatePrice(plan.buy);
    const support = formatCandidatePrice(plan.support);
    const pressure = formatCandidatePrice(plan.pressure);
    const ma8 = formatCandidatePrice(plan.ma8);
    const target = formatCandidatePrice(plan.target);
    return `
        <div class="candidate-trade-plan">
          <div><b>买</b><span>${buy}</span><small>${escapeHtml(plan.buyLabel || "")}</small></div>
          <div><b>撑</b><span>${support}</span><small>${escapeHtml(plan.supportSource || "")}</small></div>
          <div><b>压</b><span>${pressure}</span><small>${escapeHtml(plan.pressureSource || "")}</small></div>
          <div><b>MA8</b><span>${ma8}</span><small>目标 ${target}</small></div>
        </div>
      `;
}

function candidateRewardRisk(plan, stop) {
    const buy = Number(plan.buy);
    const target = Number(plan.target);
    const stopLoss = Number(stop);
    if (!Number.isFinite(buy) || !Number.isFinite(target) || !Number.isFinite(stopLoss) || buy <= stopLoss) return "-";
    const ratio = (target - buy) / (buy - stopLoss);
    if (!Number.isFinite(ratio)) return "-";
    return ratio.toFixed(1);
}

function candidateDecisionTone(c) {
    const finalSig = c.final_signal || c.signal;
    if (c.gate_downgraded || finalSig === "watch" || Number(c.size_hint || 0) === 0) return "watch";
    if ((c.entry_quality || 0) >= 70 || finalSig === "breakout_confirm") return "strong";
    return "normal";
}

function renderTrendPoolSummary() {
    const tickers = selectedTrendPoolTickers();
    if (!tickers.length) {
        return `<div class="trend-pool-summary muted">尚未勾选趋势战法池。勾选后可在盘中监控里实时验证。</div>`;
    }
    return `
        <div class="trend-pool-summary">
          <strong>已勾选 ${tickers.length} 只</strong>
          <span>${tickers.slice(0, 8).map((ticker) => escapeHtml(ticker)).join("、")}${tickers.length > 8 ? "…" : ""}</span>
          <button type="button" class="mini-action" onclick="switchTab('intraday')">去盘中监控</button>
        </div>
      `;
}

function toggleTrendPoolCandidate(checkbox) {
    const ticker = checkbox && checkbox.dataset ? checkbox.dataset.ticker : "";
    if (!ticker) return;
    setTrendPoolCandidate({
        ticker,
        name: checkbox.dataset.name || ticker,
        sector_name: checkbox.dataset.sector || "",
        source: "sector_candidate",
    }, checkbox.checked);
}

function renderCandidates(candidates) {
    const card = document.getElementById("candidatesCard");
    if (!card) return;
    latestCandidateRows = Array.isArray(candidates) ? candidates : [];
    if (!candidates || !candidates.length) {
        card.style.display = "";
        showHtml("candidatesResult", `
          <div id="trendPoolSummary">${renderTrendPoolSummary()}</div>
          <p class="hint">暂无 MA5 趋势战法候选。请先查询板块动向，或等待数据同步完成后重试。</p>
        `);
        return;
    }
    card.style.display = "";
    const signalColors = {
        breakout_confirm: "signal-breakout",
        pullback_to_ma5: "signal-pullback",
        gentle_rise: "signal-gentle",
        watch: "signal-watch"
    };

    // 按 entry_quality 降序（后端已排好，直接用）
    const rows = candidates.filter((c) => c.signal !== "watch").concat(candidates.filter((c) => c.signal === "watch"));

    if (!rows.length) {
        card.style.display = "none";
        return;
    }

    const qualityBadge = (q) => {
        const cls = q >= 70 ? "quality-high" : q >= 50 ? "quality-mid" : "quality-low";
        return `<span class="quality-badge ${cls}">${q}</span>`;
    };

    const riskTags = (flags) => {
        if (!flags || !flags.length) return "";
        return flags.map((f) => `<span class="risk-flag">${escapeHtml(f)}</span>`).join(" ");
    };

    // Job 7：真假突破追踪徽章。读 candidate.breakout_tracking，四挡颜色 + hover 明细。
    const breakoutQualityBadge = (tracking) => {
        if (!tracking || !tracking.breakout_quality) return "";
        const labels = {
            valid: "突破有效",
            pending_confirmation: "突破待确认",
            suspicious: "突破可疑",
            failed: "突破失败",
        };
        const grade = String(tracking.breakout_quality || "").toLowerCase();
        const label = labels[grade] || "突破追踪";
        const pct = (v) => (v == null || Number.isNaN(Number(v)))
            ? "-"
            : (Number(v) * 100).toFixed(2) + "%";
        const nextDay = tracking.next_day_hold_flag === true ? "守"
            : tracking.next_day_hold_flag === false ? "破" : "-";
        const tooltipParts = [
            `突破日 ${tracking.breakout_date || "-"}`,
            `T+${tracking.tracked_days ?? 0}`,
            `前高守 ${pct(tracking.previous_high_hold_ratio)}`,
            `缩量比 ${tracking.post_breakout_shrink_ratio == null ? "-" : Number(tracking.post_breakout_shrink_ratio).toFixed(2)}`,
            `次日 ${nextDay}`,
            tracking.fall_back_into_box_flag ? "跌回箱体" : "未跌回",
            `状态 ${tracking.status || "-"}`,
        ];
        return `<span class="breakout-badge breakout-${grade}" title="${escapeHtml(tooltipParts.join(" · "))}">${escapeHtml(label)}</span>`;
    };

    // Job D：五买点形态徽章。读 candidate.buy_point_pattern，5 挡颜色 + 上下文/note 走 hover。
    const buyPointPatternBadge = (c) => {
        const pattern = c && c.buy_point_pattern;
        if (!pattern || pattern === "none") return "";
        const label = c.buy_point_pattern_label || pattern;
        const tooltipParts = [];
        if (c.buy_point_pattern_context) tooltipParts.push(c.buy_point_pattern_context);
        if (c.buy_point_pattern_strength) tooltipParts.push(`强度 ${c.buy_point_pattern_strength}`);
        if (c.buy_point_pattern_note) tooltipParts.push(c.buy_point_pattern_note);
        const title = tooltipParts.length ? tooltipParts.join(" · ") : label;
        return `<span class="pattern-badge pattern-${pattern}" title="${escapeHtml(title)}">${escapeHtml(label)}</span>`;
    };

    // 判断整体是否有任何一只候选发生了 gate 降级；决定是否显示"降级"列
    const hasGate = rows.some((c) => c.original_signal !== undefined);
    const anyDowngraded = rows.some((c) => c.gate_downgraded);

    const renderSignalCell = (c) => {
        const finalSig = c.final_signal || c.signal;
        const finalLabel = c.final_signal_label || c.signal_label;
        const origSig = c.original_signal || c.signal;
        const origLabel = c.original_signal_label || c.signal_label;
        const finalTag = `<span class="signal-tag ${escapeHtml(signalColors[finalSig] || "signal-watch")}">${escapeHtml(finalLabel)}</span>`;
        if (c.gate_downgraded && origSig !== finalSig) {
            return `
            <div class="signal-downgrade">
              <span class="signal-tag ${escapeHtml(signalColors[origSig] || "signal-watch")} signal-tag-strike">${escapeHtml(origLabel)}</span>
              <span class="downgrade-arrow">→</span>
              ${finalTag}
            </div>`;
        }
        // 高质量例外情况：final 未变但 allowed_new_position 特批
        if (c.allowed_new_position && !anyDowngradedRow(c) && hasGate && c.size_hint > 0 && c.size_hint < 0.5) {
            return `${finalTag}<span class="badge-exception" title="满足高质量例外，允许小仓">⭐例外</span>`;
        }
        return finalTag;
    };
    const anyDowngradedRow = (c) => c.gate_downgraded;

    const sizeCell = (c) => {
        if (c.size_hint === undefined || c.size_hint === null) return "-";
        const pct = Math.round(c.size_hint * 100);
        if (pct === 0) return '<span class="size-hint-zero">禁止</span>';
        if (pct >= 100) return '<span class="size-hint-full">满仓</span>';
        return `<span class="size-hint-partial">${pct}%</span>`;
    };

    const candidateCards = rows.map((c) => {
        const plan = candidateDisplayPlan(c);
        const tone = candidateDecisionTone(c);
        const finalLabel = c.final_signal_label || c.signal_label || "仅关注";
        const rr = candidateRewardRisk(plan, c.stop_loss);
        const size = sizeCell(c);
        const buyLabel = plan.buyLabel || "计划买点";
        const targetSource = plan.pressureSource || "压力/目标";
        const detailOpen = candidateViewMode === "expanded" ? "open" : "";
        const reasonText = c.reason || "-";
        const trendBoost = c.trend_score != null
            ? `趋势板块 #${c.trend_rank || "-"} · ${c.trend_label || "-"} · 主线分 ${c.sector_mainline_score ?? c.trend_score} · 乘数 ${c.sector_multiplier || "-"}`
            : "";
        return `
        <article class="candidate-decision-card tone-${tone} ${candidateViewMode === "expanded" ? "is-expanded" : "is-compact"} ${c.gate_downgraded ? 'row-downgraded' : ''}">
          <header class="candidate-card-head">
            <label class="candidate-monitor-check" onclick="event.stopPropagation()">
              <input
                type="checkbox"
                class="trend-pool-check"
                data-ticker="${escapeHtml(c.ticker)}"
                data-name="${escapeHtml(c.name || c.ticker)}"
                data-sector="${escapeHtml(c.sector_name || "")}"
                ${selectedTrendPool[c.ticker] ? "checked" : ""}
                onchange="toggleTrendPoolCandidate(this)"
                aria-label="加入盘中监控勾选池 ${escapeHtml(c.ticker)}"
              />
              <span>监控</span>
            </label>
            <button class="candidate-stock-title" type="button" onclick="jumpToAnalysis('${escapeHtml(c.ticker)}')">
              <strong>
                ${escapeHtml(c.name || c.ticker)}
                <span>${escapeHtml(c.ticker)}</span>
                <em>${escapeHtml(c.sector_name || "-")}</em>
                <em>${escapeHtml(c.role_label || "-")}</em>
              </strong>
            </button>
            <div class="candidate-signal-box">
              ${renderSignalCell(c)}
              ${c.ma20_slope_up ? '<span class="badge-zhusheng">主升</span>' : ""}
              ${riskTags(c.risk_flags)}
            </div>
          </header>
          <div class="candidate-decision-strip">
            <div><span>推荐</span><strong>${escapeHtml(finalLabel)}</strong><small>${hasGate ? `仓位 ${size.replace(/<[^>]+>/g, "")}` : "大盘闸门"}</small></div>
            <div><span>买点</span><strong>${formatCandidatePrice(plan.buy)}</strong><small>${escapeHtml(buyLabel)}</small></div>
            <div><span>目标</span><strong>${formatCandidatePrice(plan.target)}</strong><small>${escapeHtml(targetSource)}</small></div>
            <div><span>止损</span><strong>${escapeHtml(String(c.stop_loss ?? "-"))}</strong><small>退出位</small></div>
            <div><span>RR</span><strong>${escapeHtml(rr)}</strong><small>收益/风险</small></div>
          </div>
          <div class="candidate-scan-lines">
            <p><b>评分</b><span>最终 ${qualityBadge(c.final_trade_score ?? c.entry_quality ?? 0)} · 股票 ${escapeHtml(String(c.stock_quality_score ?? "-"))} · 买点 ${escapeHtml(String(c.trade_timing_score ?? "-"))} · 风险系数 ${escapeHtml(String(c.risk_adjustment ?? "-"))} ${buyPointPatternBadge(c)} ${breakoutQualityBadge(c.breakout_tracking)}</span></p>
            <p><b>趋势</b><span>MA5 ${escapeHtml(String(c.ma5 ?? "-"))} · MA8 ${formatCandidatePrice(plan.ma8)} · RSI ${Number.isFinite(Number(c.rsi)) ? Number(c.rsi).toFixed(1) : "-"} · 旧质量 ${qualityBadge(c.entry_quality ?? 0)} · 5日 ${colorPercent(c.return_5d)}</span></p>
            <p><b>位置</b><span>收盘 ${escapeHtml(String(c.close ?? "-"))} · 支撑 ${formatCandidatePrice(plan.support)} · 压力 ${formatCandidatePrice(plan.pressure)}</span></p>
          </div>
          <details class="candidate-reason" ${detailOpen}>
            <summary><b>AI理由</b><span>${escapeHtml(reasonText)}</span></summary>
            <p>${escapeHtml(reasonText)}${trendBoost ? `<br><small>${escapeHtml(trendBoost)}</small>` : ""}</p>
          </details>
        </article>
      `;
    }).join("");
    const banner = anyDowngraded ? `
        <p class="hint gate-active-hint">
          ⚠️ Market Gate 已启用：部分候选的原始信号被降级为"仅关注"。
          横线代表被降级的原始信号，→ 后面是 gate 后的最终信号。
        </p>` : "";
    showHtml("candidatesResult", `
        <div id="trendPoolSummary">${renderTrendPoolSummary()}</div>
        <div class="candidate-view-toolbar">
          <div>
            <strong>候选池扫描</strong>
            <span>默认紧凑模式，一屏快速比较 4-5 只股票。</span>
          </div>
          <div class="candidate-view-actions">
            <button type="button" class="${candidateViewMode === "compact" ? "active" : ""}" onclick="setCandidateViewMode('compact')">紧凑模式</button>
            <button type="button" class="${candidateViewMode === "expanded" ? "active" : ""}" onclick="setCandidateViewMode('expanded')">展开模式</button>
          </div>
        </div>
        ${banner}
        <div class="candidate-decision-list mode-${candidateViewMode}">${candidateCards}</div>
        <p class="hint">点击任意行跳转单票分析。勾选框会把股票加入盘中监控池；预测买入/支撑/压力/预计售出由 MA5 战法和近20日价位自动推导。质量分 ≥ 70 为高质量入场，最终仓位由大盘 gate 决定。</p>
      `);
}

function jumpToAnalysis(ticker) {
    if (!ticker) return;
    document.getElementById("analyzeTicker").value = ticker;
    switchTab("analysis");
    // v2：优先跳新版单票面板，如果 runStockPanel 不存在再回退到 runAnalyze
    if (typeof runStockPanel === "function") {
        runStockPanel();
    } else {
        runAnalyze();
    }
}

function jumpToSectorByName(sectorName) {
    if (!sectorName) return;
    switchTab("sectors");
    // 在 sectors Tab 里高亮该板块
    setTimeout(() => {
        const cards = document.querySelectorAll(".sector-summary-card, .sector-card, [data-sector-name]");
        cards.forEach((card) => {
            const name = card.getAttribute("data-sector-name") || card.querySelector(".sector-name")?.textContent || "";
            if (name && name.trim() === sectorName.trim()) {
                card.classList.add("sector-highlight");
                card.scrollIntoView({behavior: "smooth", block: "center"});
                setTimeout(() => card.classList.remove("sector-highlight"), 3000);
            }
        });
    }, 400);
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
            <button class="story-link" data-target="candidatesCard" onclick="scrollSectorFeature('candidatesCard')"><span>04</span><b class="story-link-label">趋势战法</b></button>
            <button class="story-link" data-target="sector-knowledge" onclick="scrollSectorFeature('sector-knowledge')"><span>05</span><b class="story-link-label">口径边界</b></button>
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
    if (max - min < 0.01) {
        min -= 0.01;
        max += 0.01;
    }
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
          ${positions.map((item, index) => {
        const alignClass = item.x < 18 ? "align-left" : item.x > 82 ? "align-right" : "";
        return `
            <button type="button" class="stock-chart-point ${index === positions.length - 1 ? "is-last" : ""} ${alignClass}" style="left:${item.x}%;top:${item.closeY}%" aria-label="${escapeHtml(item.row.date)} 收盘 ${escapeHtml(item.row.close)}">
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
          `;
    }).join("")}
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
    const rawPayload = {
        snapshot: data.snapshot,
        decision: data.decision,
        explanation: data.explanation,
        data_source: data.data_source
    };
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
    const requestOptions = {...options};
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
    try {
        payload = JSON.parse(text);
    } catch {
        payload = {raw: text};
    }
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
        const overallLabel = {ready: "全部就绪", degraded: "降级可用", blocked: "核心数据缺失"}[overall] || overall;
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
            headers: {"Content-Type": "application/json"},
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
            headers: {"Content-Type": "application/json"},
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
        const resp = await fetch("/api/v1/market/upload-wave-doc", {method: "POST", body: form});
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
        const resp = await fetch(`/api/v1/market/wave-docs?date=${date}&benchmark_ticker=${bm}`, {method: "DELETE"});
        const data = await resp.json();
        showHtml("waveDocResult", `<p class="hint">已删除 ${data.deleted_count || 0} 份文档。</p>`);
    } catch (error) {
        showHtml("waveDocResult", `<p class="hint" style="color:#d97706">${escapeHtml(formatError(error))}</p>`);
    }
}

function renderWaveDocCard(doc) {
    const kindColors = {
        support: "level-support",
        pressure: "level-resistance",
        wave: "level-resistance",
        breakdown: "level-resistance",
        breakout: "level-support",
        neutral: ""
    };
    const kindLabels = {
        support: "支撑",
        pressure: "压力",
        wave: "波浪",
        breakdown: "破位",
        breakout: "突破",
        neutral: "中性"
    };
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
        const data = await fetchJson("/api/v1/tushare/health", {timeoutMs: 90000});
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
            headers: {"Content-Type": "application/json"},
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
            headers: {"Content-Type": "application/json"},
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
            headers: {"Content-Type": "application/json"},
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
    const hint = document.getElementById("analyzeHint");
    const fallbackEl = document.getElementById("allowFallback");
    if (!hint || !fallbackEl) return;
    const fallback = fallbackEl.checked;
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
    const fallback = document.getElementById("allowFallback")?.checked ? "true" : "false";
    const targetId = document.getElementById("analyzeResult") ? "analyzeResult" : "stockPanelResult";
    showHtml(targetId, `<div class="stock-loading"><span></span><strong>正在生成个股行情与策略分析</strong><p>${fallback === "true" ? "已允许 Tushare fallback，若 Mongo 无数据可能等待较久。" : "Mongo-first 模式，不会调用 Tushare。"}</p></div>`);
    try {
        latestAnalysisData = await fetchJson(`/api/v1/analyze?ticker=${ticker}&date=${date}&allow_tushare_fallback=${fallback}`, {timeoutMs: 25000});
        showHtml(targetId, renderStockAnalysis(latestAnalysisData));
    } catch (error) {
        showHtml(targetId, `<div class="interpretation-card"><h3>分析失败</h3><p class="section-conclusion">${escapeHtml(formatError(error, "analysis"))}</p></div>`);
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
        const data = await fetchJson(url, {timeoutMs: 60000});
        latestMarketData = data;
        syncBenchmarkSelects(data.benchmark_ticker);
        syncMarketDates(data.analysis_date);
        renderPermissionSummary(data);
        renderMarketPermissionDetail(data);
        finishMarketProgress(true);
    } catch (error) {
        document.getElementById("marketPermissionSummary").innerHTML = "";
        const gap = marketDataGapMessage(error);
        show("marketPermissionConfirmResult", `${gap.title}：${gap.message}`);
        showHtml("marketPermissionResult", renderMarketError(error));
        finishMarketProgress(false);
    }
}

function forceRunMarketPermission() {
    return runMarketPermission(true);
}

async function syncMarketMissingData() {
    const syncButton = document.getElementById("marketSyncMissingButton");
    const date = selectedMarketDate();
    const benchmark = selectedBenchmarkTicker();
    if (syncButton) {
        syncButton.disabled = true;
        syncButton.textContent = "正在同步...";
    }
    startMarketProgress();
    show("marketPermissionConfirmResult", "正在补齐 PART1 缺失数据...");
    showHtml("marketPermissionResult", `
        <div class="interpretation-card">
          <h3>正在同步缺失数据</h3>
          <p class="section-conclusion">目标日期 ${escapeHtml(date)}，基准指数 ${escapeHtml(benchmark)}。将强制增量补齐当前日期附近的个股日线、资金流向和基准指数。</p>
          <p class="hint">如果之前同步被“已有部分数据”跳过，这次会用 force 模式重新检查当前日期缺口。</p>
        </div>
      `);
    try {
        const data = await fetchJson("/api/v1/data/sync", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            timeoutMs: 180000,
            body: JSON.stringify({
                target_date: date,
                mode: "incremental",
                force: true,
                benchmark_ticker: benchmark,
                incremental_lookback_days: 14,
                incremental_overlap_days: 5
            })
        });
        showHtml("marketPermissionResult", `
          <div class="interpretation-card">
            <h3>缺失数据同步完成，正在重新计算</h3>
            <p class="section-conclusion">已完成当前日期附近的强制增量同步，下面会自动重新跑 PART1。</p>
            <details class="json-details" open>
              <summary>同步结果</summary>
              <pre>${escapeHtml(prettyChineseJson(data))}</pre>
            </details>
          </div>
        `);
        await loadDataStatus();
        if (syncButton) {
            syncButton.disabled = false;
            syncButton.textContent = "同步缺失数据";
        }
        await runMarketPermission(true);
    } catch (error) {
        const message = formatError(error);
        show("marketPermissionConfirmResult", message);
        showHtml("marketPermissionResult", `
          <div class="interpretation-card market-error-card">
            <h3>同步失败</h3>
            <p class="section-conclusion">${escapeHtml(message)}</p>
            <p class="hint">请检查 Tushare 网关健康、token、限速状态，或到“数据同步”页查看详细返回。</p>
          </div>
        `);
        finishMarketProgress(false);
        if (syncButton) {
            syncButton.disabled = false;
            syncButton.textContent = "同步缺失数据";
        }
    }
}

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
        if (matched) records.push({date: day.date, ...matched});
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
        const data = await fetchJson(`/api/v1/market/sectors?date=${date}&benchmark_ticker=${benchmark}`, {timeoutMs: 45000});
        if (requestVersion !== sectorRequestVersion) return;
        selectedSectorIndex = 0;
        selectedLeaderView = "total";
        selectedLeaderDate = "";
        leaderTickerDraft = "";
        latestSectorData = {...data, daily_leaders_loading: true};
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
        const msg = String(error && error.message || error || "");
        // 未预计算 → 显示引导卡（含一键预计算按钮）
        if (msg.includes("未预计算")) {
            showHtml("sectorTrendResult", renderPrecomputePrompt(dateValue, benchmarkValue, msg));
        } else {
            showHtml("sectorTrendResult", `<div class="interpretation-card"><h3>查询失败</h3><p class="section-conclusion">${escapeHtml(formatError(error, "sectors"))}</p></div>`);
        }
    }
}

function renderPrecomputePrompt(date, benchmark, msg) {
    return `
        <div class="precompute-card">
          <div class="precompute-icon">⚡</div>
          <h3>板块数据未预计算</h3>
          <p class="precompute-hint">${escapeHtml(msg)}</p>
          <p class="precompute-hint">60 日趋势窗口计算需要 60-120 秒。点击下方按钮开始预计算，完成后自动重新加载板块动向。</p>
          <div class="precompute-actions">
            <button onclick="runPrecomputeSectors('${escapeHtml(date)}', '${escapeHtml(benchmark)}', this)">立即预计算</button>
            <a class="link-button secondary" href="/api/v1/market/sectors?date=${encodeURIComponent(date)}&benchmark_ticker=${encodeURIComponent(benchmark)}" target="_blank">查看错误 JSON</a>
          </div>
          <div id="precomputeProgress" class="precompute-progress" style="display:none">
            <div class="precompute-spinner"></div>
            <p>正在预计算 <span id="precomputeElapsed">0</span>s ...</p>
          </div>
        </div>`;
}

let precomputeTimer = null;

function runPrecomputeSectorsFromPage() {
    const date = document.getElementById("globalDate").value;
    const benchmark = document.getElementById("globalBenchmark").value;
    return runPrecomputeSectors(date, benchmark, document.getElementById("sectorPrecomputeButton"));
}

async function runPrecomputeSectors(date, benchmark, sourceButton = null) {
    const requestVersion = ++sectorRequestVersion;
    const progressEl = document.getElementById("precomputeProgress");
    const elapsedEl = document.getElementById("precomputeElapsed");
    const btn = sourceButton;
    if (btn) btn.disabled = true;
    if (progressEl) progressEl.style.display = "";
    startSectorPrecomputeProgress(requestVersion);
    showHtml("sectorTrendResult", `<div class="interpretation-card"><h3>正在同步板块预计算</h3><p class="section-conclusion">正在调用 POST /api/v1/data/precompute-sectors?date=${escapeHtml(date)}，完成后会自动重新查询板块动向。</p></div>`);
    const startAt = Date.now();
    if (precomputeTimer) clearInterval(precomputeTimer);
    precomputeTimer = setInterval(() => {
        if (elapsedEl) elapsedEl.textContent = Math.floor((Date.now() - startAt) / 1000);
    }, 500);
    try {
        const data = await fetchJson(
            `/api/v1/data/precompute-sectors?date=${encodeURIComponent(date)}&benchmark_ticker=${encodeURIComponent(benchmark)}`,
            {method: "POST", timeoutMs: 300000}
        );
        clearInterval(precomputeTimer);
        precomputeTimer = null;
        finishSectorPrecomputeProgress(requestVersion, true);
        // 预计算成功 → 立即重跑 runSectorTrends 拉缓存
        await runSectorTrends();
    } catch (error) {
        clearInterval(precomputeTimer);
        precomputeTimer = null;
        finishSectorPrecomputeProgress(requestVersion, false);
        if (btn) btn.disabled = false;
        if (progressEl) progressEl.style.display = "none";
        showHtml("sectorTrendResult", `<div class="interpretation-card"><h3>预计算失败</h3><p class="section-conclusion">${escapeHtml(formatError(error, "precompute"))}</p></div>`);
    }
}

async function loadDailyLeaders(dateValue, benchmarkValue, requestVersion = sectorRequestVersion) {
    try {
        const date = encodeURIComponent(dateValue);
        const benchmark = encodeURIComponent(benchmarkValue);
        const data = await fetchJson(`/api/v1/market/leaders?date=${date}&benchmark_ticker=${benchmark}`, {timeoutMs: 90000});
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
        const data = await fetchJson(`/api/v1/telegram/analyze?ticker=${ticker}&date=${date}`, {timeoutMs: 25000});
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
            headers: {"Content-Type": "application/json"},
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

// ── 盘中监控 ─────────────────────────────────────────────────────
let intradayTimer = null;
let intradayCountdownTimer = null;
let intradayNextRefresh = 0;
const intradayHistory = {};

function stopIntradayAutoRefresh() {
    if (intradayTimer) {
        clearInterval(intradayTimer);
        intradayTimer = null;
    }
    if (intradayCountdownTimer) {
        clearInterval(intradayCountdownTimer);
        intradayCountdownTimer = null;
    }
    const el = document.getElementById("intradayCountdown");
    if (el) el.textContent = "";
}

function startIntradayAutoRefresh() {
    stopIntradayAutoRefresh();
    const el = document.getElementById("intradayCountdown");
    intradayNextRefresh = Date.now() + 30_000;
    intradayCountdownTimer = setInterval(() => {
        const remain = Math.max(0, Math.ceil((intradayNextRefresh - Date.now()) / 1000));
        if (el) el.textContent = `下次刷新 ${remain}s`;
    }, 500);
    intradayTimer = setInterval(() => {
        intradayNextRefresh = Date.now() + 30_000;
        runIntraday(false);
    }, 30_000);
}

async function runIntraday(showLoading = true) {
    const date = encodeURIComponent(document.getElementById("globalDate").value);
    const bm = encodeURIComponent(selectedBenchmarkTicker());
    const sourceEl = document.getElementById("intradaySource");
    const source = sourceEl ? sourceEl.value : "tushare";
    const selected = selectedTrendPoolTickers().map(encodeURIComponent).join(",");
    if (showLoading) {
        showHtml("intradayResult", `<p class="hint">正在拉取实时数据（source=${source}）...</p>`);
    }
    try {
        const data = await fetchJson(`/api/v1/market/intraday?date=${date}&benchmark_ticker=${bm}&include_candidates=true&source=${source}&selected_tickers=${selected}`, {timeoutMs: 30000});
        renderIntraday(data);
        // 自动开启定时器
        const auto = document.getElementById("intradayAutoRefresh");
        if (auto && auto.checked && !intradayTimer) startIntradayAutoRefresh();
    } catch (error) {
        showHtml("intradayResult", `<p class="hint" style="color:#dc2626">${escapeHtml(formatError(error))}</p>`);
        stopIntradayAutoRefresh();
    }
}

function intradayToneClass(value) {
    if (!value) return "neutral";
    if (["red", "bullish"].includes(value)) return "bullish";
    if (["green", "bearish"].includes(value)) return "bearish";
    return "neutral";
}

function rememberIntradaySeriesPoint(ticker, label, realtime, events) {
    if (!ticker || !realtime || realtime.current == null) return [];
    const key = String(ticker);
    if (!intradayHistory[key]) intradayHistory[key] = [];
    const series = intradayHistory[key];
    const last = series[series.length - 1];
    const now = realtime.update_at || new Date().toISOString();
    const point = {
        time: now,
        label,
        price: Number(realtime.current),
        pct_chg: realtime.pct_chg,
        events: events || [],
    };
    if (!last || last.time !== point.time || last.price !== point.price) {
        series.push(point);
    }
    if (series.length > 80) series.splice(0, series.length - 80);
    return series;
}

function renderIntradayMiniChart(ticker, label, realtime, events) {
    const series = rememberIntradaySeriesPoint(ticker, label, realtime, events);
    if (!series.length) {
        return `<div class="intraday-chart-empty">暂无可绘制的实时轨迹，等待下一次刷新。</div>`;
    }
    const prices = series.map((item) => Number(item.price)).filter(Number.isFinite);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = Math.max(max - min, max * 0.002, 0.01);
    const points = series.map((item, index) => {
        const x = series.length === 1 ? 50 : 5 + (index * 90) / (series.length - 1);
        const y = 84 - ((Number(item.price) - min) / range) * 68;
        return {...item, x, y};
    });
    const path = points.map((item) => `${item.x},${item.y}`).join(" ");
    const latest = series[series.length - 1];
    const latestEvents = (latest.events || []).filter((event) => event.code !== "no_trigger").slice(0, 5);
    return `
        <div class="intraday-chart-card">
          <div class="intraday-chart-head">
            <div>
              <strong>${escapeHtml(label || ticker)}</strong>
              <small>${escapeHtml(ticker || "")} · ${escapeHtml(String(latest.time || "").replace("T", " "))}</small>
            </div>
            <b class="${Number(latest.pct_chg) >= 0 ? "up" : "down"}">${colorPercent(latest.pct_chg)}</b>
          </div>
          <div class="intraday-chart-stage">
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
              <line x1="4" y1="84" x2="96" y2="84"></line>
              <polyline points="${path}"></polyline>
            </svg>
            ${points.map((item, index) => {
        const hasEvent = index === points.length - 1 && latestEvents.length;
        const primary = hasEvent ? latestEvents[0] : null;
        const tone = intradayToneClass(primary && (primary.color || primary.direction));
        return `
                <button type="button" class="intraday-chart-dot ${hasEvent ? "has-event" : ""} ${tone}" style="left:${item.x}%;top:${item.y}%" title="${escapeHtml(item.label || ticker)} ${escapeHtml(String(item.price))}">
                  ${hasEvent ? `<span>${latestEvents.length}</span>` : ""}
                </button>
              `;
    }).join("")}
          </div>
          <div class="intraday-chart-events">
            ${latestEvents.length ? latestEvents.map((event) => `<span class="event-pill ${intradayToneClass(event.color || event.direction)}">${escapeHtml(event.title)}</span>`).join("") : `<span class="muted">暂无触发信号</span>`}
          </div>
        </div>
      `;
}

function renderIntradayPlanGrid(plan) {
    const cells = [
        ["预测买入", plan.buy_price, plan.buy_label],
        ["支撑位", plan.support_price, plan.support_source],
        ["压力位", plan.pressure_price, plan.pressure_source],
        ["MA8", plan.ma8, "趋势线"],
        ["止损位", plan.stop_loss, "风险边界"],
        ["预计售出", plan.expected_sell_price, plan.expected_sell_label],
    ];
    return `
        <div class="intraday-plan-grid">
          ${cells.map(([label, value, note]) => `
            <span>
              <small>${escapeHtml(label)}</small>
              <strong>${escapeHtml(formatCandidatePrice(value))}</strong>
              <em>${escapeHtml(note || "")}</em>
            </span>
          `).join("")}
        </div>
      `;
}

function renderIntradayEventList(events) {
    const items = (events || []).slice(0, 8);
    if (!items.length) return `<p class="hint">暂无技术事件。</p>`;
    return `
        <div class="technical-event-list">
          ${items.map((event) => `
            <article class="technical-event-card ${intradayToneClass(event.color || event.direction)}">
              <div>
                <span class="event-dot"></span>
                <strong>${escapeHtml(event.title || event.code || "-")}</strong>
                <small>${escapeHtml(event.severity || "")}</small>
              </div>
              <p>${escapeHtml(event.detail || "")}</p>
              <b>动作：${escapeHtml(event.action || "继续观察。")}</b>
            </article>
          `).join("")}
        </div>
      `;
}

function renderSameMarketSignals(candidates) {
    const buckets = {};
    (candidates || []).forEach((candidate) => {
        (candidate.signal_events || []).forEach((event) => {
            if (!event.code || event.code === "no_trigger" || event.code === "realtime_missing") return;
            if (!buckets[event.code]) buckets[event.code] = {event, items: []};
            buckets[event.code].items.push(candidate);
        });
    });
    const groups = Object.values(buckets).sort((a, b) => b.items.length - a.items.length).slice(0, 6);
    if (!groups.length) return `<p class="hint">暂无同市场技术信号。</p>`;
    return `
        <div class="same-signal-list">
          ${groups.map((group) => `
            <article>
              <div><span class="event-pill ${intradayToneClass(group.event.color || group.event.direction)}">${escapeHtml(group.event.title)}</span><b>${group.items.length} 只</b></div>
              <p>${group.items.slice(0, 6).map((item) => `${escapeHtml(item.name || item.ticker)} <small>${escapeHtml(item.ticker || "")}</small>`).join("、")}</p>
            </article>
          `).join("")}
        </div>
      `;
}

function renderIntradayCandidateCards(candidates) {
    if (!candidates || !candidates.length) {
        return `<p class="hint">暂无候选票。请先在板块动向里勾选趋势战法池，或先运行板块动向。</p>`;
    }
    return `
        <div class="intraday-candidate-grid">
          ${candidates.slice(0, 12).map((candidate) => {
        const realtime = candidate.realtime || {};
        const rating = candidate.technical_rating || {};
        const primary = candidate.primary_event || {};
        return `
              <article class="intraday-candidate-card ${intradayToneClass(rating.tone)}">
                <header>
                  <div>
                    ${stockLink(candidate.name || candidate.ticker, candidate.ticker, "compact")}
                    <small>${escapeHtml(candidate.industry || candidate.sector_name || "-")} · ${escapeHtml(candidate.pool_status || candidate.role_label || "-")}</small>
                  </div>
                  <strong class="${Number(realtime.pct_chg) >= 0 ? "up" : "down"}">${colorPercent(realtime.pct_chg)}</strong>
                </header>
                <div class="candidate-live-line">
                  <span>现价 <b>${escapeHtml(String(realtime.current ?? "-"))}</b></span>
                  <span>评级 <b>${escapeHtml(rating.label || "-")}</b></span>
                  <span>主信号 <b>${escapeHtml(primary.title || "-")}</b></span>
                </div>
                ${renderIntradayPlanGrid(candidate.trade_plan || {})}
                ${renderIntradayMiniChart(candidate.ticker, candidate.name, realtime, candidate.signal_events)}
                ${renderIntradayEventList(candidate.signal_events || [])}
              </article>
            `;
    }).join("")}
        </div>
      `;
}

function renderIntraday(data) {
    const gate = data.market_gate || {};
    const br = data.benchmark_realtime;
    const pl = data.planned_levels || {};
    const te = data.triggered_levels || [];
    const cs = data.candidates_realtime || [];
    const errs = data.realtime_errors || [];

    const gateBadge = `<span class="gate-badge gate-${gate.state || 'hold'}">${escapeHtml(gate.state_label || '—')}</span>`;

    // 实时基准指数卡片
    const brCard = br ? `
        <div class="intraday-benchmark-card">
          <div class="benchmark-header">
            <strong>${escapeHtml(br.name || br.ticker)}</strong>
            <small class="muted">${escapeHtml(br.ticker || '')} · 更新 ${escapeHtml(br.update_at || '')}</small>
          </div>
          <div class="benchmark-price">
            <span class="benchmark-current ${br.pct_chg > 0 ? 'up' : (br.pct_chg < 0 ? 'down' : '')}">${br.current}</span>
            <span class="benchmark-pct ${br.pct_chg > 0 ? 'up' : (br.pct_chg < 0 ? 'down' : '')}">${br.pct_chg != null ? (br.pct_chg * 100).toFixed(2) + '%' : '-'}</span>
          </div>
          <div class="benchmark-meta">
            <span>开 ${br.open ?? '-'}</span>
            <span>高 ${br.high ?? '-'}</span>
            <span>低 ${br.low ?? '-'}</span>
            <span>昨收 ${br.prev_close ?? '-'}</span>
          </div>
        </div>` : `<div class="intraday-benchmark-card"><p class="hint">实时基准指数暂不可用${errs.length ? '：' + escapeHtml(errs.join('; ')) : ''}</p></div>`;

    // 关键位状态
    const levelStatus = (label, level, current, isSupport) => {
        if (!level || current == null) return `<span class="level-cell"><small class="muted">${escapeHtml(label)}</small><strong>${escapeHtml(String(level ?? '-'))}</strong><small class="muted">未触发</small></span>`;
        const delta = ((current - level) / level * 100).toFixed(2);
        const held = isSupport ? current > level : current < level;
        const cls = held ? "held" : (isSupport ? "support-broken" : "pressure-broken");
        const txt = isSupport
            ? (held ? `站上（+${delta}%）` : `破位（${delta}%）`)
            : (held ? `未破（${delta}%）` : `突破（+${delta}%）`);
        return `<span class="level-cell level-${cls}"><small class="muted">${escapeHtml(label)}</small><strong>${escapeHtml(String(level))}</strong><small>${escapeHtml(txt)}</small></span>`;
    };

    const currentPrice = br ? br.current : null;
    const levelsRow = `
        <div class="intraday-levels-row">
          ${levelStatus(`压力1 ${pl.pressure_1_source || ''}`, pl.pressure_1, currentPrice, false)}
          ${levelStatus(`支撑1 ${pl.support_1_source || ''}`, pl.support_1, currentPrice, true)}
          ${levelStatus(`支撑2 ${pl.support_2_source || ''}`, pl.support_2, currentPrice, true)}
        </div>`;

    // 触发事件
    const triggerCard = te.length ? `
        <div class="intraday-triggers">
          <h4>⚡ 已触发的关键位（${te.length}）</h4>
          <ul>
            ${te.map(e => `<li class="trigger-${e.severity}"><b>${escapeHtml(e.level_label)}</b> · ${escapeHtml(e.note)}</li>`).join("")}
          </ul>
        </div>` : '';

    const focusCandidate = cs.find((candidate) => (candidate.signal_events || []).some((event) => !["no_trigger", "realtime_missing"].includes(event.code))) || cs[0];
    const focusChart = focusCandidate
        ? renderIntradayMiniChart(focusCandidate.ticker, focusCandidate.name, focusCandidate.realtime, focusCandidate.signal_events)
        : renderIntradayMiniChart(data.benchmark_ticker, br && br.name, br, te);
    const focusEvents = focusCandidate ? (focusCandidate.signal_events || []) : te;

    const gateReason = gate.gate_reason ? `<p class="intraday-gate-reason">${gateBadge} ${escapeHtml(gate.gate_reason)}</p>` : '';

    const html = `
        <div class="intraday-container">
          <div class="intraday-meta">
            <small>服务器时间 ${escapeHtml(data.server_time || '')} · PART1 缓存 ${escapeHtml(data.part1_cached_at || '')} · 实时源 <b>${escapeHtml(data.realtime_source || '未知')}</b>${data.realtime_source_requested && data.realtime_source_requested !== data.realtime_source ? ' (请求 ' + escapeHtml(data.realtime_source_requested) + ' → 已回退)' : ''}</small>
          </div>
          ${gateReason}
          ${brCard}
          ${levelsRow}
          ${triggerCard}
          <div class="intraday-workbench">
            <section class="intraday-workbench-main">
              <div class="intraday-section-head">
                <div>
                  <h3>实时趋势与信号</h3>
                  <p>当前为 30 秒轮询采样趋势；接入 minute_bars 后可升级为真实分时图。</p>
                </div>
              </div>
              ${focusChart}
            </section>
            <aside class="intraday-event-panel">
              <h3>技术事件解释</h3>
              ${renderIntradayEventList(focusEvents)}
              <h3>同市场信号</h3>
              ${renderSameMarketSignals(cs)}
            </aside>
          </div>
          <section class="intraday-candidates">
            <div class="intraday-section-head">
              <div>
                <h3>趋势战法候选实时验证（${cs.length}）</h3>
                <p>候选票会显示计划买点、支撑/压力、MA8、止损、预计售出和实时触发事件。</p>
              </div>
            </div>
            ${renderIntradayCandidateCards(cs)}
          </section>
          ${errs.length ? `<p class="hint" style="color:#dc2626">实时数据错误：${escapeHtml(errs.join('; '))}</p>` : ''}
        </div>
      `;
    showHtml("intradayResult", html);
}

// ── 单票面板（图 4 风格） ─────────────────────────────────────────
let stockPanelData = null;
let stockChart = null;
let signalFilter = {period: "all", direction: "all", category: "all", code: null};

async function runStockPanel() {
    const ticker = document.getElementById("analyzeTicker").value.trim().toUpperCase();
    if (!ticker) {
        showHtml("stockPanelResult", '<p class="hint" style="color:#dc2626">请输入股票代码</p>');
        return;
    }
    const date = document.getElementById("globalDate").value;
    const bm = selectedBenchmarkTicker();
    showHtml("stockPanelResult", '<p class="hint">正在加载单票面板...</p>');
    try {
        const data = await fetchJson(
            `/api/v1/stock/panel?ticker=${encodeURIComponent(ticker)}&date=${encodeURIComponent(date)}&benchmark_ticker=${encodeURIComponent(bm)}&period=5`,
            {timeoutMs: 60000}
        );
        stockPanelData = data;
        renderStockPanel(data);
    } catch (error) {
        showHtml("stockPanelResult", `<p class="hint" style="color:#dc2626">${escapeHtml(formatError(error))}</p>`);
    }
}

function stockDirectionClass(value) {
    const n = Number(value || 0);
    if (n > 0) return "up";
    if (n < 0) return "down";
    return "flat";
}

function stockPanelVerdict(data) {
    const rating = data.rating || {};
    const signals = data.signals || [];
    const latest = signals.length ? signals[signals.length - 1] : null;
    const shortScore = Number(rating.short_term_score ?? 50);
    const riskCount = signals.filter((s) => s.category === "ma5_risk" || s.direction === "warning" || s.direction === "bearish").slice(-20).length;
    const ma5Count = signals.filter((s) => s.category === "ma5_strategy").slice(-20).length;
    if (shortScore >= 75 && riskCount <= 2) {
        return {
            tone: "bull",
            title: "强势看涨",
            action: "跟踪回踩或突破确认",
            text: `短期评分 ${shortScore}，近20个信号里 MA5 战法 ${ma5Count} 个，风险信号 ${riskCount} 个。`,
        };
    }
    if (shortScore >= 60) {
        return {
            tone: "warm",
            title: "偏强观察",
            action: "等关键位确认，不追高",
            text: latest ? `最新信号：${latest.name}，${latest.note}` : `短期评分 ${shortScore}，信号仍需继续验证。`,
        };
    }
    if (shortScore < 40 || riskCount >= 4) {
        return {
            tone: "bear",
            title: "防守优先",
            action: "先看止损位和支撑承接",
            text: `短期评分 ${shortScore}，近20个风险/看跌信号 ${riskCount} 个。`,
        };
    }
    return {
        tone: "neutral",
        title: "中性观察",
        action: "等待方向选择",
        text: latest ? `最新信号：${latest.name}，${latest.note}` : "暂未形成明确方向信号。",
    };
}

function renderStockQuoteStrip(rt, basic, sync) {
    const amount = rt.amount != null ? formatTradeAmount(rt.amount) : "-";
    return `
        <div class="sv-quote-strip">
          <div><span>开盘</span><strong>${escapeHtml(rt.open ?? "-")}</strong></div>
          <div><span>最高</span><strong class="up">${escapeHtml(rt.high ?? "-")}</strong></div>
          <div><span>最低</span><strong class="down">${escapeHtml(rt.low ?? "-")}</strong></div>
          <div><span>成交额</span><strong>${escapeHtml(amount)}</strong></div>
          <div><span>所属板块</span><strong>${escapeHtml(basic.industry || "-")}</strong></div>
          <div><span>穿透</span><strong>${escapeHtml((sync || {}).sync_status || "待接入")}</strong></div>
        </div>
      `;
}

function renderStockAiBrief(data) {
    const verdict = stockPanelVerdict(data);
    const signals = data.signals || [];
    const latest = signals.length ? signals[signals.length - 1] : null;
    const confidence = Math.max(0, Math.min(100, Number((data.rating || {}).short_term_score ?? 50)));
    const riskLabel = verdict.tone === "bear" ? "高" : verdict.tone === "warm" ? "中" : verdict.tone === "bull" ? "低-中" : "中";
    return `
        <aside class="sv-ai-brief tone-${verdict.tone}">
          <div class="sv-ai-kicker">每日 AI 技术结论</div>
          <h3>${escapeHtml(verdict.title)}</h3>
          <p>${escapeHtml(verdict.text)}</p>
          <div class="sv-ai-decision-row">
            <span><b>${confidence}</b><small>置信度</small></span>
            <span><b>${escapeHtml(riskLabel)}</b><small>风险等级</small></span>
          </div>
          <strong>${escapeHtml(verdict.action)}</strong>
          ${latest ? `<small>最新事件 ${escapeHtml(latest.date)} · ${escapeHtml(latest.name)}</small>` : "<small>等待更多技术事件确认</small>"}
        </aside>
      `;
}

function stockStrategySnapshot(data) {
    const lv = data.levels || {};
    const tp = data.trade_plan || {};
    const rt = data.realtime || {};
    // 对齐 PRD 阶段 9：权威买点 = trade_plan.entry_price（= 当前 close）
    // 次选实时价、levels.entry_price。绝不再拿 lv.support 当买点——那可能是历史低点。
    const buy = firstFinitePositive(tp.entry_price, lv.entry_price, rt.current, lv.ma5);
    // 目标价：优先 trade_plan.target_price（60 日前高或波动率目标）
    const target = firstFinitePositive(tp.target_price, lv.resistance, buy ? buy * 1.08 : null);
    // 止损：优先 trade_plan.stop_loss_1 = MA5*0.98
    const stop = firstFinitePositive(tp.stop_loss_1, lv.stop_loss_long, buy ? buy * 0.96 : null);
    const rr = tp.reward_risk_ratio || candidateRewardRisk({buy, target}, stop);
    return {buy, target, stop, rr};
}

function stockPanelTrendPoolPayload(data) {
    const basic = (data && data.basic) || {};
    const rt = (data && data.realtime) || {};
    const ticker = normalizeTrendPoolTicker(data && data.ticker);
    return {
        ticker,
        name: basic.name || rt.name || ticker,
        sector_name: basic.industry || "",
        source: "stock_panel",
    };
}

function renderStockMonitorButton(data) {
    const payload = stockPanelTrendPoolPayload(data);
    if (!payload.ticker) return "";
    const active = Boolean(selectedTrendPool[payload.ticker]);
    return `
        <button
          type="button"
          class="secondary sv-monitor-button ${active ? "is-active" : ""}"
          data-trend-pool-button="stock-panel"
          data-ticker="${escapeHtml(payload.ticker)}"
          onclick="toggleStockPanelTrendPool()"
          aria-pressed="${active ? "true" : "false"}"
        >${active ? "已加入盘中监控" : "加入盘中监控"}</button>
      `;
}

function toggleStockPanelTrendPool() {
    if (!stockPanelData) return;
    const payload = stockPanelTrendPoolPayload(stockPanelData);
    if (!payload.ticker) return;
    setTrendPoolCandidate(payload, !selectedTrendPool[payload.ticker]);
}

// Job D（§4.7 五买点体系）：单票分析用的形态徽章，逻辑跟候选池 buyPointPatternBadge 一致。
function stockPanelPatternBadge(data) {
    const pattern = data && data.buy_point_pattern;
    if (!pattern || pattern === "none") return "";
    const label = data.buy_point_pattern_label || pattern;
    const tooltipParts = [];
    if (data.buy_point_pattern_context) tooltipParts.push(data.buy_point_pattern_context);
    if (data.buy_point_pattern_strength) tooltipParts.push(`强度 ${data.buy_point_pattern_strength}`);
    if (data.buy_point_pattern_note) tooltipParts.push(data.buy_point_pattern_note);
    const title = tooltipParts.length ? tooltipParts.join(" · ") : label;
    return `<span class="pattern-badge pattern-${pattern}" title="${escapeHtml(title)}">${escapeHtml(label)}</span>`;
}

function renderStockStrategyCard(data) {
    const strategy = stockStrategySnapshot(data);
    const verdict = stockPanelVerdict(data);
    return `
        <section class="sv-strategy-card">
          <div class="sv-card-kicker">TRADE PLAN · 今日策略</div>
          <h3>${escapeHtml(verdict.action)} ${stockPanelPatternBadge(data)}</h3>
          <div class="sv-strategy-grid">
            <div><span>买点</span><strong>${formatCandidatePrice(strategy.buy)}</strong><small>交易计划入场价</small></div>
            <div><span>目标</span><strong>${formatCandidatePrice(strategy.target)}</strong><small>60日前高/波动率</small></div>
            <div><span>止损</span><strong>${formatCandidatePrice(strategy.stop)}</strong><small>MA5 × 0.98</small></div>
            <div><span>盈亏比</span><strong>${escapeHtml(strategy.rr)}</strong><small>目标/止损</small></div>
          </div>
          <p class="sv-trigger-note">触发条件：次日收盘确认；建议仓位按大盘 gate 与质量分执行。</p>
        </section>
      `;
}

function renderStockEvidenceStrip(data) {
    const basic = data.basic || {};
    const rating = data.rating || {};
    const sync = data.market_sync || {};
    const active = data.active_buy || {};
    const summary = data.signal_summary || {};
    const daily = data.daily_bars || [];
    const latestDaily = daily.length ? daily[daily.length - 1].date : "";
    const stale = latestDaily && data.analysis_date && latestDaily < data.analysis_date;
    return `
        <div class="sv-evidence-strip">
          <div><span>趋势</span><strong>${escapeHtml(rating.short_term_label || "-")}</strong><small>短期 ${escapeHtml(rating.short_term_score ?? "-")}</small></div>
          <div><span>板块</span><strong>${escapeHtml(basic.industry || "-")}</strong><small>${escapeHtml(basic.market || "Tushare 行业")}</small></div>
          <div><span>资金</span><strong>${escapeHtml(active.label || "待确认")}</strong><small>${active.main_net != null ? `主力净额 ${escapeHtml(active.main_net)}` : "moneyflow 待同步"}</small></div>
          <div><span>事件</span><strong>${escapeHtml((summary.ma5_strategy_count ?? 0) + " 个战法")}</strong><small>风险 ${escapeHtml(summary.ma5_risk_count ?? 0)} · 通用 ${escapeHtml(summary.generic_count ?? 0)}</small></div>
          <div class="${stale ? "is-stale" : ""}"><span>日K水位</span><strong>${escapeHtml(latestDaily || "-")}</strong><small>${stale ? `早于 ${escapeHtml(data.analysis_date)}，图表会补实时点` : "已匹配分析日"}</small></div>
        </div>
      `;
}

function renderStockPanel(data) {
    const basic = data.basic || {};
    const rt = data.realtime || {};
    const sync = data.market_sync || {};
    const lv = data.levels || {};
    const rating = data.rating || {};
    const ss = data.signal_summary || {};

    const pctChgCls = stockDirectionClass(rt.pct_chg);

    const html = `
        <div class="sv-page sv-ai-terminal">
          <div class="sv-header sv-decision-header">
            ${renderStockAiBrief(data)}
            ${renderStockStrategyCard(data)}
            <div class="sv-title-block sv-stock-compact-card">
              <div class="sv-name-row">
                <span class="sv-stock-logo">${escapeHtml(String(basic.name || rt.name || data.ticker).slice(0, 1))}</span>
                <div>
                  <h2>${escapeHtml(basic.name || rt.name || data.ticker)}</h2>
                  <p>
                    <span>${escapeHtml(data.ticker)}</span>
                    ${basic.market ? `<span>${escapeHtml(basic.market)}</span>` : ""}
                    ${basic.industry ? `<span class="clickable-sector" onclick="jumpToSectorByName('${String(basic.industry).replace(/'/g, "&#39;")}')" title="点击跳到板块动向">${escapeHtml(basic.industry)} →</span>` : ""}
                    <span>${escapeHtml(data.benchmark_ticker || "")}</span>
                  </p>
                </div>
              </div>
              <div class="sv-price-row">
                <span class="sv-current ${pctChgCls}">${rt.current ?? '-'}</span>
                <span class="sv-unit muted">元</span>
                <span class="sv-pct ${pctChgCls}">${rt.pct_chg != null ? ((rt.pct_chg * 100).toFixed(2) + '%') : '-'}</span>
                <span class="sv-meta muted">已收盘/实时 · ${escapeHtml(data.analysis_date || "-")}</span>
              </div>
              <div class="sv-header-actions">
                ${renderStockMonitorButton(data)}
                <button onclick="runStockPanel()" class="secondary">刷新</button>
                ${rt.update_at ? `<small class="muted">行情 ${escapeHtml(rt.update_at.slice(11, 19))}</small>` : '<small class="muted">行情时间待更新</small>'}
              </div>
            </div>
          </div>
          ${renderStockEvidenceStrip(data)}
          ${renderStockQuoteStrip(rt, basic, sync)}

          <div class="sv-body">
            <div class="sv-container-left">
              <div class="sv-left-title">
                <div>
                  <span class="feature-kicker">AI TECHNICAL ANALYSIS</span>
                  <h3>走势与信号</h3>
                </div>
                <div class="chart-view-switch">
                  <button class="chart-view-btn active" onclick="switchStockChartView('daily', event)">日 K</button>
                  <button class="chart-view-btn" onclick="switchStockChartView('minute', event)">5min 分时</button>
                </div>
              </div>
              <div class="sv-left-body">
                <div class="sv-left-content">
                  <div class="sv-chart-container">
                    <canvas id="stockChartCanvas"></canvas>
                  </div>
                </div>
              </div>
            </div>

            <div class="sv-container-right">
              <div class="sv-right-top">
                ${renderSignalEventsPanel(data)}
              </div>
              <div class="sv-right-bottom">
                ${renderSameMarketPlaceholder(data)}
              </div>
            </div>
          </div>

          <section class="sv-section-block">
            <div class="sv-section-head">
              <span>DECISION EVIDENCE</span>
              <h3>核心交易分析</h3>
              <p>先看评级、关键位、资金结构和穿透强弱，这四块决定今天是否值得行动。</p>
            </div>
            <div class="sv-insight-grid sv-insight-grid-primary">
              ${renderRatingCard(rating)}
              ${renderLevelsSubCard(lv)}
              ${renderActiveBuyCard(data.active_buy)}
              ${renderSyncCard(sync, basic)}
            </div>
          </section>

          <section class="sv-section-block sv-section-risk">
            <div class="sv-section-head">
              <span>RISK & EXIT</span>
              <h3>风险与退出</h3>
              <p>把止损和退出规则单独放置，避免和买点、目标价混在一起。</p>
            </div>
            <div class="sv-insight-grid sv-insight-grid-secondary">
              ${renderStopLossCard(lv, data)}
            </div>
          </section>

          ${data.errors && data.errors.length ? `<p class="hint" style="color:#dc2626;padding:0 16px">警告：${escapeHtml(data.errors.join('; '))}</p>` : ''}
        </div>
      `;
    showHtml("stockPanelResult", html);
    setTimeout(() => drawStockChart("daily"), 50);
}

function switchStockChartView(view, event) {
    document.querySelectorAll(".chart-view-btn").forEach(b => b.classList.remove("active"));
    const btn = event ? event.target : null;
    if (btn) btn.classList.add("active");
    drawStockChart(view);
}

function rollingAverage(values, size) {
    return values.map((_, index) => {
        const start = Math.max(0, index - size + 1);
        const slice = values.slice(start, index + 1).map(Number).filter(Number.isFinite);
        if (!slice.length) return null;
        return Number((slice.reduce((sum, item) => sum + item, 0) / slice.length).toFixed(2));
    });
}

function stockChartTickLabel(label, dailyView) {
    const text = String(label || "");
    if (dailyView) return text;
    if (text.includes("T")) return text.slice(11, 16);
    return text.length > 8 ? text.slice(-8) : text;
}

function drawStockChart(view) {
    if (!stockPanelData) return;
    const canvas = document.getElementById("stockChartCanvas");
    if (!canvas) return;
    if (stockChart) {
        stockChart.destroy();
        stockChart = null;
    }

    let bars = view === "minute" ? (stockPanelData.minute_bars || []) : (stockPanelData.daily_bars || []);
    const dailyView = view !== "minute";
    if (dailyView) {
        bars = [...bars];
        const rt = stockPanelData.realtime || {};
        const latest = bars.length ? bars[bars.length - 1] : null;
        const latestDate = latest ? String(latest.date || "") : "";
        const analysisDate = String(stockPanelData.analysis_date || "");
        const current = Number(rt.current);
        if (analysisDate && Number.isFinite(current) && (!latestDate || latestDate < analysisDate)) {
            const open = Number(rt.open);
            const high = Number(rt.high);
            const low = Number(rt.low);
            bars.push({
                date: analysisDate,
                open: Number.isFinite(open) ? open : current,
                high: Number.isFinite(high) ? Math.max(high, current) : current,
                low: Number.isFinite(low) ? Math.min(low, current) : current,
                close: current,
                realtime_patch: true,
            });
        }
    }
    if (!bars.length) {
        canvas.getContext("2d").fillText("暂无数据", 20, 40);
        return;
    }
    const labels = bars.map(b => b.date || b.datetime);
    const closes = bars.map(b => Number(b.close));
    const ma5 = dailyView ? rollingAverage(closes, 5) : [];
    const ma8 = dailyView ? rollingAverage(closes, 8) : [];
    const lv = stockPanelData.levels || {};
    const levelLine = (value) => labels.map(() => (value == null || value === "" ? null : Number(value)));

    // 信号点：把日期对应到 index
    const signals = stockPanelData.signals || [];
    const filteredSignals = signals.filter(s => {
        if (view === "minute") return false;  // 分时只画收盘线，不叠信号（信号是日线级别）
        if (signalFilter.period !== "all" && s.period !== signalFilter.period) return false;
        if (signalFilter.direction !== "all" && s.direction !== signalFilter.direction) return false;
        return true;
    });

    const signalPoints = filteredSignals.map(s => {
        const signalDate = String(s.date || "");
        const idx = bars.findIndex(b => String(b.date || b.datetime || "").startsWith(signalDate));
        if (idx < 0) return null;
        const price = Number(s.price);
        const close = Number(bars[idx].close);
        const yValue = Number.isFinite(price) ? price : close;
        if (!Number.isFinite(yValue)) return null;
        return {
            x: labels[idx],
            y: yValue,
            sig: {...s, date: signalDate || String(labels[idx] || "")},
            barIndex: idx,
        };
    }).filter(Boolean);

    const bullPts = signalPoints.filter(p => p.sig.direction === "bullish");
    const bearPts = signalPoints.filter(p => p.sig.direction === "bearish");
    const warnPts = signalPoints.filter(p => p.sig.direction === "warning");

    const ctx = canvas.getContext("2d");
    stockChart = new Chart(ctx, {
        type: "line",
        data: {
            labels: labels,
            datasets: [
                {
                    label: view === "minute" ? "现价" : "收盘价",
                    data: closes,
                    borderColor: "#e5484d",
                    backgroundColor: "rgba(229, 72, 77, 0.06)",
                    borderWidth: 1.8,
                    pointRadius: 0,
                    fill: true,
                    tension: 0.22,
                    order: 3,
                },
                ...(dailyView ? [
                    {
                        label: "MA5",
                        data: ma5,
                        borderColor: "#f59e0b",
                        backgroundColor: "transparent",
                        borderWidth: 1.2,
                        pointRadius: 0,
                        tension: 0.22,
                        order: 4,
                    },
                    {
                        label: "MA8",
                        data: ma8,
                        borderColor: "#2f80ed",
                        backgroundColor: "transparent",
                        borderWidth: 1.2,
                        pointRadius: 0,
                        tension: 0.22,
                        order: 4,
                    },
                    {
                        label: "支撑",
                        data: levelLine(lv.support),
                        borderColor: "rgba(21, 155, 114, 0.7)",
                        borderDash: [5, 5],
                        borderWidth: 1,
                        pointRadius: 0,
                        fill: false,
                        order: 5,
                    },
                    {
                        label: "压力",
                        data: levelLine(lv.resistance),
                        borderColor: "rgba(229, 72, 77, 0.7)",
                        borderDash: [5, 5],
                        borderWidth: 1,
                        pointRadius: 0,
                        fill: false,
                        order: 5,
                    },
                ] : []),
                {
                    label: "看涨事件",
                    data: bullPts,
                    type: "scatter",
                    backgroundColor: "#e5484d",
                    borderColor: "#ffffff",
                    borderWidth: 1.5,
                    pointRadius: 5,
                    pointHoverRadius: 8,
                    pointStyle: "triangle",
                    parsing: false,
                    showLine: false,
                    order: 1,
                },
                {
                    label: "看跌事件",
                    data: bearPts,
                    type: "scatter",
                    backgroundColor: "#159b72",
                    borderColor: "#ffffff",
                    borderWidth: 1.5,
                    pointRadius: 5,
                    pointHoverRadius: 8,
                    pointStyle: "rectRot",
                    parsing: false,
                    showLine: false,
                    order: 1,
                },
                {
                    label: "警告事件",
                    data: warnPts,
                    type: "scatter",
                    backgroundColor: "#9ca3af",
                    borderColor: "#ffffff",
                    borderWidth: 1.5,
                    pointRadius: 4,
                    pointHoverRadius: 7,
                    parsing: false,
                    showLine: false,
                    order: 2,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {mode: dailyView ? "nearest" : "index", intersect: false},
            layout: {padding: {top: 8, right: 10, bottom: 0, left: 4}},
            plugins: {
                legend: {
                    display: true,
                    position: "bottom",
                    labels: {boxWidth: 10, padding: 8, usePointStyle: true, font: {size: 11}, color: "#7f8fa6"}
                },
                tooltip: {
                    backgroundColor: "rgba(255,255,255,0.96)",
                    titleColor: "#172033",
                    bodyColor: "#415066",
                    borderColor: "#dbe5f0",
                    borderWidth: 1,
                    padding: 12,
                    callbacks: {
                        title: (items) => {
                            const signalItem = items.find((item) => item.raw && item.raw.sig);
                            if (signalItem) return signalItem.raw.sig.date || signalItem.raw.x || signalItem.label || "";
                            return items.length ? items[0].label : "";
                        },
                        label: (ctx) => {
                            if (ctx.datasetIndex <= (dailyView ? 4 : 0)) {
                                const row = bars[ctx.dataIndex] || {};
                                const patch = row.realtime_patch ? " · 实时补点" : "";
                                if (ctx.datasetIndex === 0) {
                                    return [
                                        `${ctx.dataset.label} ${ctx.parsed.y}${patch}`,
                                        `开 ${row.open ?? "-"} / 高 ${row.high ?? "-"} / 低 ${row.low ?? "-"}`,
                                    ];
                                }
                                return `${ctx.dataset.label} ${ctx.parsed.y}`;
                            }
                            const raw = ctx.raw;
                            return raw && raw.sig ? [`${raw.sig.name} · ${raw.sig.price}`, raw.sig.note || ""] : "";
                        },
                    },
                },
            },
            scales: {
                x: {
                    grid: {display: false},
                    ticks: {
                        autoSkip: false,
                        maxRotation: 0,
                        font: {size: 11},
                        color: "#8da0b8",
                        callback: function (value) {
                            const lastIndex = labels.length - 1;
                            if (lastIndex <= 0) return stockChartTickLabel(this.getLabelForValue(value), dailyView);
                            const step = Math.max(1, Math.ceil(lastIndex / 6));
                            if (value === 0 || value === lastIndex || value % step === 0) {
                                return stockChartTickLabel(this.getLabelForValue(value), dailyView);
                            }
                            return "";
                        },
                    },
                    border: {display: false},
                },
                y: {
                    grid: {color: "rgba(148, 163, 184, 0.18)"},
                    ticks: {font: {size: 11}, color: "#8da0b8"},
                    border: {display: false}
                },
            },
            onClick: (evt, elements) => {
                if (elements.length > 0) {
                    const el = elements[0];
                    if (el.datasetIndex >= (dailyView ? 5 : 1)) {
                        const dataset = stockChart.data.datasets[el.datasetIndex];
                        const point = dataset.data[el.index];
                        if (point && point.sig) {
                            focusSignal(point.sig);
                        }
                    }
                }
            },
        },
    });
}

function focusSignal(sig) {
    const panel = document.getElementById("signalEventsList");
    if (!panel) return;
    const items = panel.querySelectorAll(".signal-event-item");
    items.forEach(el => el.classList.remove("focused"));
    const targetId = `signal-event-${sig.date}-${sig.code}`;
    const target = document.getElementById(targetId);
    if (target) {
        target.classList.add("focused");
        target.scrollIntoView({behavior: "smooth", block: "center"});
    }
}

function renderRatingCard(rating) {
    const st = rating.short_term_score ?? 50;
    const lt = rating.long_term_score ?? 50;
    const ratingColor = (score) => {
        if (score >= 75) return "#dc2626";
        if (score >= 60) return "#f97316";
        if (score >= 40) return "#6b7280";
        if (score >= 25) return "#22c55e";
        return "#16a34a";
    };
    const arc = (score) => {
        const pct = Math.max(0, Math.min(100, score)) / 100;
        const angle = 180 * pct;
        return `<div class="rating-gauge" style="--gauge-color:${ratingColor(score)};--gauge-angle:${angle}deg">
          <div class="gauge-arc"></div>
          <div class="gauge-value">${score}</div>
        </div>`;
    };
    return `
        <div class="sv-sub-card sv-rating-card">
          <div class="sv-sub-title">
            <h4>智能评级</h4>
            <small class="muted">信号 + 趋势</small>
          </div>
          <div class="sv-rating-panel">
          <div class="rating-block">
            ${arc(st)}
            <small class="muted">短期</small>
            <strong style="color:${ratingColor(st)}">${escapeHtml(rating.short_term_label || '-')}</strong>
          </div>
          <div class="rating-block">
            ${arc(lt)}
            <small class="muted">长期</small>
            <strong style="color:${ratingColor(lt)}">${escapeHtml(rating.long_term_label || '-')}</strong>
          </div>
          <div class="rating-events">
            <span class="event-count bullish">看涨 ${rating.bull_events ?? 0}</span>
            <span class="event-count bearish">看跌 ${rating.bear_events ?? 0}</span>
            <span class="event-count warning">警告 ${rating.warning_events ?? 0}</span>
          </div>
          </div>
        </div>`;
}

function renderLevelsSubCard(lv) {
    return `
        <div class="sv-sub-card">
          <div class="sv-sub-title">
            <h4>支撑位 / 阻力位</h4>
            <small class="muted">基于近 ${lv.based_on_bars ?? 60} 根日线</small>
          </div>
          <div class="sv-sub-body">
            <div class="sv-level-row">
              <span class="metric-label">支撑位</span>
              <strong class="lv-support">${lv.support ?? '-'}</strong>
            </div>
            <div class="sv-level-row">
              <span class="metric-label">阻力位</span>
              <strong class="lv-resistance">${lv.resistance ?? '-'}</strong>
            </div>
          </div>
        </div>`;
}

function renderStopLossCard(lv, data) {
    return `
        <div class="sv-sub-card">
          <div class="sv-sub-title">
            <h4>止损价格</h4>
            <small class="muted">分析日 ${escapeHtml((data || {}).analysis_date || new Date().toISOString().slice(0, 10))}</small>
          </div>
          <div class="sv-sub-body">
            <div class="sv-level-row">
              <span class="metric-label">针对多头头寸</span>
              <strong class="lv-stop-long">${lv.stop_loss_long ?? '-'}</strong>
              <small class="muted">MA5 × 0.98</small>
            </div>
            <div class="sv-level-row">
              <span class="metric-label">针对空头头寸</span>
              <strong class="lv-stop-short">${lv.stop_loss_short ?? '-'}</strong>
              <small class="muted">阻力 × 1.02</small>
            </div>
          </div>
        </div>`;
}

function renderSameMarketPlaceholder(data) {
    const groups = {};
    (data.signals || []).slice(-40).forEach((signal) => {
        const key = signal.code || signal.name || "signal";
        if (!groups[key]) groups[key] = {signal, count: 0};
        groups[key].count += 1;
    });
    const rows = Object.values(groups).sort((a, b) => b.count - a.count).slice(0, 4);
    return `
        <div class="sv-same-market">
          <div class="sv-sub-title">
            <h4>同类技术事件 <span class="badge-todo">检索规划中</span></h4>
          </div>
          <div class="sv-same-market-list">
            ${rows.length ? rows.map(({signal, count}) => `
              <article>
                <span class="signal-icon ${signal.direction === "bullish" ? "signal-icon-bull" : signal.direction === "bearish" ? "signal-icon-bear" : "signal-icon-warn"}">${signal.direction === "bullish" ? "▲" : signal.direction === "bearish" ? "▼" : "◆"}</span>
                <div><strong>${escapeHtml(signal.name || signal.code)}</strong><small>${escapeHtml(signal.category || "generic")} · 本票近40信号 ${count} 次</small></div>
              </article>
            `).join("") : `<p class="hint">暂无可聚合信号；同板块检索接口接入后会展示其他个股。</p>`}
          </div>
        </div>`;
}

function renderActiveBuyCard(ab) {
    if (!ab || !ab.active_buy_ratio) {
        return `
          <div class="sv-sub-card">
            <div class="sv-sub-title"><h4>主动买入结构</h4><small class="muted">无资金流数据</small></div>
            <div class="sv-sub-body"><p class="hint" style="padding:8px 0;font-size:11px;color:var(--muted)">Tushare moneyflow 未覆盖或该日缺失</p></div>
          </div>`;
    }
    const ratio = ab.active_buy_ratio;
    const structure = ab.active_buy_structure;
    const labelColor = ratio > 0.55 && structure > 0.1 ? "#dc2626" :
        ratio < 0.45 && structure < -0.05 ? "#16a34a" :
            (ratio > 0.55 && structure < -0.05) ? "#f97316" : "#64748b";

    // 主散比：>0 = 主力主导（红），<0 = 散户主导（灰）
    const structCls = structure > 0.05 ? "up" : (structure < -0.05 ? "down" : "flat");

    // 外盘/内盘 bar
    const ratioPct = ratio * 100;
    return `
        <div class="sv-sub-card">
          <div class="sv-sub-title">
            <h4>主动买入结构</h4>
            <small style="color:${labelColor};font-weight:700">${escapeHtml(ab.label || '')}</small>
          </div>
          <div class="sv-sub-body">
            <div class="active-buy-ratio-bar">
              <div class="ratio-fill" style="width:${ratioPct.toFixed(1)}%"></div>
              <span class="ratio-label">外盘 ${ratioPct.toFixed(1)}% · 内盘 ${(100 - ratioPct).toFixed(1)}%</span>
            </div>
            <div class="active-buy-rows">
              <div class="ab-row">
                <span class="metric-label">主力主动净</span>
                <strong class="${(ab.main_net || 0) > 0 ? 'up' : 'down'}">${(ab.main_net || 0) > 0 ? '+' : ''}${(ab.main_net / 10000 || 0).toFixed(0)}万</strong>
              </div>
              <div class="ab-row">
                <span class="metric-label">散户主动净</span>
                <strong class="${(ab.retail_net || 0) > 0 ? 'up' : 'down'}">${(ab.retail_net || 0) > 0 ? '+' : ''}${(ab.retail_net / 10000 || 0).toFixed(0)}万</strong>
              </div>
              <div class="ab-row">
                <span class="metric-label">主散结构差</span>
                <strong class="${structCls}">${structure > 0 ? '+' : ''}${structure.toFixed(3)}</strong>
              </div>
              <div class="ab-row">
                <span class="metric-label">近5日主力净</span>
                <strong class="${(ab.cum_main_net_5d || 0) > 0 ? 'up' : 'down'}">${(ab.cum_main_net_5d || 0) > 0 ? '+' : ''}${(ab.cum_main_net_5d / 10000 || 0).toFixed(0)}万</strong>
              </div>
              <div class="ab-row">
                <span class="metric-label">主力持续买入</span>
                <strong>${ab.main_active_persist_days_5d || 0}/5 天</strong>
              </div>
            </div>
          </div>
        </div>`;
}

function renderSyncCard(sync, basic) {
    const cls = {
        sync_up: "sync-up",
        sync_down: "sync-down",
        diverge_up: "diverge-up",
        diverge_down: "diverge-down",
        flat: ""
    }[sync.sync_direction] || "";
    const stockPct = sync.stock_pct_chg != null ? sync.stock_pct_chg * 100 : 0;
    const bmPct = sync.benchmark_pct_chg != null ? sync.benchmark_pct_chg * 100 : 0;
    // 用相对差值画一个 bar
    const diff = stockPct - bmPct;
    const barCls = diff > 0 ? "diff-up" : (diff < 0 ? "diff-down" : "diff-flat");
    return `
        <div class="sv-sub-card ${cls}">
          <div class="sv-sub-title">
            <h4>穿透分析</h4>
            <small class="muted">${escapeHtml(sync.sync_status || '-')}</small>
          </div>
          <div class="sv-sub-body">
            <div class="sync-diff-bar ${barCls}">
              <div class="sync-diff-track">
                <div class="sync-diff-fill" style="--diff-width:${Math.min(50, Math.abs(diff) * 10)}%"></div>
              </div>
              <span class="sync-diff-value">${diff >= 0 ? '+' : ''}${diff.toFixed(2)}%</span>
            </div>
            <div class="sync-row">
              <span class="metric-label">${escapeHtml(sync.benchmark_name || '大盘')}</span>
              <strong>${bmPct.toFixed(2)}%</strong>
            </div>
            <div class="sync-row">
              <span class="metric-label">本股</span>
              <strong>${stockPct.toFixed(2)}%</strong>
            </div>
          </div>
        </div>`;
}

function renderSignalEventsPanel(data) {
    let signals = (data.signals || []).slice().reverse();
    // 应用过滤
    signals = signals.filter(s => {
        if (signalFilter.period !== "all" && s.period !== signalFilter.period) return false;
        if (signalFilter.direction !== "all" && s.direction !== signalFilter.direction) return false;
        if (signalFilter.category && signalFilter.category !== "all" && s.category !== signalFilter.category) return false;
        return true;
    });

    const filterHtml = `
        <div class="signal-filters">
          <label>类别
            <select onchange="signalFilter.category=this.value;runFilterSignals()">
              <option value="all" ${signalFilter.category === 'all' ? 'selected' : ''}>全部</option>
              <option value="ma5_strategy" ${signalFilter.category === 'ma5_strategy' ? 'selected' : ''}>MA5 战法</option>
              <option value="ma5_risk" ${signalFilter.category === 'ma5_risk' ? 'selected' : ''}>MA5 风险</option>
              <option value="generic" ${signalFilter.category === 'generic' ? 'selected' : ''}>通用形态</option>
            </select>
          </label>
          <label>周期
            <select onchange="signalFilter.period=this.value;runFilterSignals()">
              <option value="all" ${signalFilter.period === 'all' ? 'selected' : ''}>全部</option>
              <option value="short" ${signalFilter.period === 'short' ? 'selected' : ''}>短期</option>
              <option value="mid" ${signalFilter.period === 'mid' ? 'selected' : ''}>中期</option>
            </select>
          </label>
          <label>方向
            <select onchange="signalFilter.direction=this.value;runFilterSignals()">
              <option value="all" ${signalFilter.direction === 'all' ? 'selected' : ''}>全部</option>
              <option value="bullish" ${signalFilter.direction === 'bullish' ? 'selected' : ''}>看涨</option>
              <option value="bearish" ${signalFilter.direction === 'bearish' ? 'selected' : ''}>看跌</option>
              <option value="warning" ${signalFilter.direction === 'warning' ? 'selected' : ''}>警告</option>
            </select>
          </label>
        </div>`;

    const visibleSignals = signals.slice(0, 5);
    const hiddenCount = Math.max(0, signals.length - visibleSignals.length);
    const items = visibleSignals.map(s => {
        const iconClass = {
            bullish: "signal-icon-bull",
            bearish: "signal-icon-bear",
            warning: "signal-icon-warn"
        }[s.direction] || "";
        const icon = {bullish: "▲", bearish: "▼", warning: "◆"}[s.direction] || "●";
        const periodLabel = {short: "短期", mid: "中期", long: "长期"}[s.period] || s.period;
        const catBadge = {
            ma5_strategy: '<span class="cat-badge cat-ma5">MA5</span>',
            ma5_risk: '<span class="cat-badge cat-risk">风险</span>',
            generic: ''
        }[s.category] || '';
        const qualityBadge = s.entry_quality != null ? `<span class="quality-mini">质量 ${s.entry_quality}</span>` : '';
        return `
          <div id="signal-event-${s.date}-${s.code}" class="signal-event-item cat-${s.category || 'generic'}" onclick='focusSignal(${JSON.stringify(s).replace(/'/g, "&#39;")})'>
            <div class="signal-event-head">
              <span class="signal-icon ${iconClass}">${icon}</span>
              <div class="signal-event-body">
                <strong>${escapeHtml(s.name)} ${catBadge} ${qualityBadge}</strong>
                <small class="muted">${escapeHtml(s.date)} · ${periodLabel} · 价 ${s.price}</small>
              </div>
            </div>
            <p class="signal-event-note">${escapeHtml(s.note)}</p>
          </div>`;
    }).join("");

    const ss = data.signal_summary || {};
    return `
        <div class="signal-events-panel">
          <div class="signal-events-header">
            <h3>活跃技术事件 <small class="muted">${signals.length}</small></h3>
            <div class="signal-tabs">
              <span class="signal-tab-item">战法 ${ss.ma5_strategy_count ?? 0}</span>
              <span class="signal-tab-item">风险 ${ss.ma5_risk_count ?? 0}</span>
              <span class="signal-tab-item">通用 ${ss.generic_count ?? 0}</span>
            </div>
          </div>
          ${filterHtml}
          <div id="signalEventsList" class="signal-events-list">${items || '<p class="hint">当前筛选下暂无信号</p>'}</div>
          ${hiddenCount ? `<div class="signal-events-more">仅显示最近 5 条，另有 ${hiddenCount} 条可通过筛选查看。</div>` : ""}
        </div>`;
}

function runFilterSignals() {
    drawStockChart("daily");
    if (stockPanelData) renderStockPanel(stockPanelData);
}

document.addEventListener("DOMContentLoaded", () => {
    const allowFallback = document.getElementById("allowFallback");
    if (allowFallback) allowFallback.addEventListener("change", updateAnalyzeHint);
    document.getElementById("analyzeTicker")?.addEventListener("change", loadDataStatus);
    document.getElementById("globalDate")?.addEventListener("change", loadDataStatus);
    const autoIntraday = document.getElementById("intradayAutoRefresh");
    if (autoIntraday) {
        autoIntraday.addEventListener("change", () => {
            if (autoIntraday.checked) startIntradayAutoRefresh();
            else stopIntradayAutoRefresh();
        });
    }
    window.addEventListener("scroll", scheduleStorylineDocking, {passive: true});
    window.addEventListener("resize", scheduleStorylineDocking);
    scheduleStorylineDocking();
});
loadStatus();
loadDataStatus();
loadTushareHealth();
