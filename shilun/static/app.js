let runtimeStatus = {};
let latestSectorData = null;
let sectorRequestVersion = 0;
let selectedSectorIndex = 0;
let sectorProgressTimer = null;
let sectorProgressState = {version: 0, phase: "idle", startedAt: 0, phaseStartedAt: 0};
const TREND_POOL_STORAGE_KEY = "shilun.trendStrategyPool";
const CANDIDATE_VIEW_STORAGE_KEY = "shilun.candidateViewMode";
const DEFAULT_CANDIDATE_STRATEGY_ID = "ma5_v0_2";
const OVERSOLD_FIRST_BOARD_STRATEGY_ID = "oversold_first_board@0.1.0";
let selectedTrendPool = loadTrendPool();
let candidateViewMode = loadCandidateViewMode();
let latestCandidateRows = [];
let candidateRowsByStrategy = Object.create(null);
let selectedCandidateTicker = "";
let selectionSearchTimer = null;
let selectionSearchVersion = 0;
let selectionPoolRequestVersion = 0;
let trendPoolSyncTimer = null;
let selectedIntradayTicker = "";
let latestIntradayData = null;
let holdingsState = {items: [], events: {}, loading: false, draftStopPlan: null, totalAssets: null};
let tradingState = {
    workbench: null,
    dashboard: null,
    cockpit: null,
    timeline: [],
    riskProfile: null,
    readiness: null,
    orders: [],
    executions: [],
    orderPanelTone: "default",
    orderPanelMessage: "",
    fillOrderId: "",
    orderRequestId: "",
    fillRequestIds: {},
    selectedTicker: "",
    loading: false,
    computing: false,
};
const tradingStockPickers = {
    order: {query: "", selected: null, items: [], status: "idle", activeIndex: -1, error: ""},
    holding: {query: "", selected: null, items: [], status: "idle", activeIndex: -1, error: ""},
};
const tradingStockSearchTimers = {};
const tradingStockSearchVersions = {order: 0, holding: 0};
let tradingRefreshTimer = null;
let latestMarketData = null;
let latestAnalysisData = null;
let selectedMarketChart = "distribution";
let marketProgressTimer = null;
let marketProgressState = {phase: "idle", startedAt: 0, progress: 0, force: false};
const MARKET_READ_TIMEOUT_MS = 180000;
const MARKET_FORCE_TIMEOUT_MS = 360000;
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

function scheduleTrendPoolBackendSync() {
    if (trendPoolSyncTimer) clearTimeout(trendPoolSyncTimer);
    trendPoolSyncTimer = setTimeout(() => syncTrendPoolToBackend(), 180);
}

async function syncTrendPoolToBackend() {
    const items = selectedTrendPoolTickers().map((ticker) => ({
        ticker,
        name: selectedTrendPool[ticker]?.name || ticker,
        sector_name: selectedTrendPool[ticker]?.sector_name || "",
        source: selectedTrendPool[ticker]?.source || "selection_pool",
        feishu_enabled: true,
    }));
    try {
        await fetchJson("/api/v1/trading/intraday-watchlist", {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({items}),
            timeoutMs: 10000,
        });
        return true;
    } catch {
        return false;
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
    scheduleTrendPoolBackendSync();
    updateTrendPoolControlState(ticker);
    syncSelectionPickedControls();
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
    limit_up_count_ma5: "涨停家数5日均",
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
    top_sectors: "中期趋势候选",
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
            market: "大盘计算等待超时：后台可能仍在完成全量计算。请稍后点击“读取已有结果”；若连续超时，再检查后台日志。",
            sectors: "板块归档读取超时，请稍后重新查询；查询不会触发重算。",
            precompute: "任务提交或进度确认超时，后台任务可能仍在运行；请稍后继续查询。",
        };
        return timeoutMessages[context] || "请求超时：服务未在限定时间内返回，请稍后重试。";
    }
    if (["sectors", "precompute"].includes(context) && isFetchConnectionError(error)) {
        return "与本机后台的连接暂时中断。请稍后重新查询；若已提交计算，任务可能仍在后台运行，可点击计算继续查询同一任务。";
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
    const raw = formatError(error, "market");
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
            message: "当前选择的基准指数没有可用的最近已完成日线，趋势、均线和支撑压力无法建立盘前基线。盘中不要求今天的完整日 K。",
            action: "点击“同步缺失数据”会补齐最近已完成交易日的基准指数日线，并同时检查全市场日线缺口。",
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
    const estimatedSeconds = marketProgressState.force ? 240 : 60;
    let progress = Math.min(94, 6 + (elapsed / estimatedSeconds) * 88);
    let label;
    if (marketProgressState.force) {
        label = elapsed < 20
            ? "阶段 1/3：读取指数与全市场日线"
            : elapsed < 120
                ? "阶段 2/3：重算市场结构与板块资金"
                : "阶段 3/3：同步期指席位并生成结论";
    } else {
        label = elapsed < 5
            ? "阶段 1/3：查找当日缓存"
            : elapsed < 35
                ? "阶段 2/3：缓存缺失，计算趋势、广度与量能"
                : "阶段 3/3：生成状态机结论与图表";
    }
    let remaining = elapsed < estimatedSeconds
        ? `预计剩余约 ${formatWaitDuration(estimatedSeconds - elapsed)}`
        : "仍在处理中，首次全量计算或外部数据较慢时会超过预计时间";
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

function setMarketButtons(disabled, runningLabel = "正在计算大盘...") {
    const labels = {
        marketQueryButton: disabled ? runningLabel : "读取已有结果",
        marketForceButton: disabled ? "重算与同步中..." : "重算并同步期指",
        permissionQueryButton: disabled ? runningLabel : "确认今日权限",
    };
    Object.entries(labels).forEach(([id, label]) => {
        const button = document.getElementById(id);
        if (!button) return;
        button.disabled = disabled;
        button.textContent = label;
    });
}

function startMarketProgress(force = false) {
    if (marketProgressTimer) clearInterval(marketProgressTimer);
    marketProgressState = {phase: "running", startedAt: Date.now(), progress: 6, force: Boolean(force)};
    setMarketButtons(true, "正在计算大盘...");
    renderMarketProgressTick();
    marketProgressTimer = setInterval(renderMarketProgressTick, 500);
}

function finishMarketProgress(success) {
    if (marketProgressTimer) clearInterval(marketProgressTimer);
    marketProgressTimer = null;
    marketProgressState.phase = success ? "complete" : "error";
    setMarketButtons(false);
    renderMarketProgressTick();
    const progress = document.getElementById("marketProgress");
    if (success && progress) progress.hidden = true;
}

function renderSectorProgressTick() {
    const root = document.getElementById("sectorProgress");
    if (!root || sectorProgressState.phase === "idle") return;
    const now = Date.now();
    const elapsed = Math.max(0, (now - sectorProgressState.startedAt) / 1000);
    const phaseElapsed = Math.max(0, (now - sectorProgressState.phaseStartedAt) / 1000);
    let label = "准备计算";
    let progress = 0;
    let remainingText = "正在读取板块缓存";
    if (sectorProgressState.phase === "main") {
        label = "正在读取强力板块与趋势榜";
        progress = Math.min(68, 5 + (phaseElapsed / 34) * 63);
        const remaining = Math.max(5, 34 - phaseElapsed);
        remainingText = `预计完整结果剩余约 ${formatWaitDuration(remaining)}`;
    } else if (sectorProgressState.phase === "precompute") {
        label = "正在计算板块，并定向补齐龙头/中军分钟 K";
        progress = 5;
        remainingText = "后台任务持续运行，完成后自动读取结果";
    } else if (sectorProgressState.phase === "empty") {
        label = "该日期尚无板块归档";
        remainingText = "点击生成 / 查询任务，生成该日期数据";
    } else if (sectorProgressState.phase === "connection") {
        label = "板块任务状态暂不可确认";
        remainingText = "连接恢复后可继续查询，后台任务可能仍在运行";
    } else if (sectorProgressState.phase === "complete") {
        label = "板块动向已完成";
        progress = 100;
        remainingText = "已完成，无需等待";
    } else if (sectorProgressState.phase === "error") {
        label = "板块请求未完成";
        progress = Math.max(5, sectorProgressState.progress || 5);
        remainingText = "请按提示重试，已有归档保留";
    }
    root.hidden = false;
    root.classList.toggle("is-complete", sectorProgressState.phase === "complete");
    root.classList.toggle("is-error", sectorProgressState.phase === "error");
    document.getElementById("sectorProgressLabel").textContent = label;
    document.getElementById("sectorProgressPercent").textContent = sectorProgressState.phase === "precompute"
        ? "后台运行" : ["empty", "connection"].includes(sectorProgressState.phase) ? "—" : `${Math.round(progress)}%`;
    document.getElementById("sectorProgressBar").style.width = `${progress}%`;
    document.getElementById("sectorProgressElapsed").textContent = `已等待 ${formatWaitDuration(elapsed)}`;
    document.getElementById("sectorProgressRemaining").textContent = remainingText;
    sectorProgressState.progress = progress;
}

function setSectorPrecomputeButton(running, label) {
    const button = document.getElementById("sectorPrecomputeButton");
    if (!button) return;
    button.disabled = !!running;
    // 保留按钮里 <small class="button-hint"> 结构：running/idle 都拼一致的 HTML
    const hint = '<small class="button-hint">后台任务 · 可关闭页面</small>';
    const primary = label || (running ? "查询任务中..." : "生成 / 查询任务");
    button.innerHTML = `${escapeHtml(primary)} ${hint}`;
}

function startSectorProgress(version) {
    if (sectorProgressTimer) clearInterval(sectorProgressTimer);
    const now = Date.now();
    sectorProgressState = {version, phase: "main", startedAt: now, phaseStartedAt: now, progress: 5};
    const button = document.getElementById("sectorQueryButton");
    if (button) {
        button.disabled = true;
        button.textContent = "正在读取归档...";
    }
    setSectorPrecomputeButton(true, "查询中...");
    renderSectorProgressTick();
    sectorProgressTimer = setInterval(renderSectorProgressTick, 1000);
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

function failSectorProgress(version, phase = "error") {
    if (sectorProgressState.version !== version) return;
    if (sectorProgressTimer) clearInterval(sectorProgressTimer);
    sectorProgressTimer = null;
    sectorProgressState.phase = phase;
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

function finishSectorPrecomputeProgress(version, success, phase = "error") {
    if (sectorProgressState.version !== version) return;
    if (sectorProgressTimer) clearInterval(sectorProgressTimer);
    sectorProgressTimer = null;
    sectorProgressState.phase = success ? "complete" : phase;
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

function stockLogoMarkup(ticker, name, className = "") {
    const normalized = String(ticker || "").trim().toUpperCase();
    const match = normalized.match(/^(\d{6})\.(SH|SZ|BJ)$/);
    const fallback = escapeHtml(String(name || normalized || "股").slice(0, 1));
    if (!match) {
        return `<span class="stock-logo-shell ${escapeHtml(className)}"><span class="stock-logo-fallback">${fallback}</span></span>`;
    }
    const url = `https://baidu-finance.cdn.bcebos.com/imgs/logo/stocks/stock_ab_${match[1]}.svg`;
    return `<span class="stock-logo-shell ${escapeHtml(className)}"><img src="${url}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><span class="stock-logo-fallback" hidden>${fallback}</span></span>`;
}

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
const activeTabId = () => document.querySelector(".tab-button.active")?.dataset.tab || "review";
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
    firstboard: {
        workspace: "research", title: "涨停聚焦", subtitle: "首板名单 · 连板天梯 · AI深入分析",
        kicker: "研究工作区", description: "按交易日查看首次涨停个股，核对原因与证据，保存单股研究。"
    },
    boardeffects: {
        workspace: "research", title: "打板效应", subtitle: "群体反馈 · 最近7日变化 · 逐日趋势",
        kicker: "研究工作区", description: "比较封板、最高板与炸板的次日反馈，观察近期收益是否持续。"
    },
    review: {
        workspace: "review",
        title: "指数复盘",
        subtitle: "指数事实 → 市场姿态 → 次日验证",
        kicker: "复盘工作区",
        description: "先复原四大指数与市场广度，再用量能、关键位和机构旁证形成次日验证边界。"
    },
    sentiment: {
        workspace: "review",
        title: "短线情绪",
        subtitle: "今日情绪 → 题材轮动 → 情绪历史",
        kicker: "复盘工作区 · P2",
        description: "用昨日固定样本核对赚钱效应，并行观察短线方向的周期、前排承接与跌停风险。"
    },
    "trade-journal": {
        workspace: "review", title: "交易记录与复盘", subtitle: "记录预期 → 核验仓位与执行 → 比较收益",
        kicker: "复盘工作区 · P3", description: "从真实成交回看原计划、仓位和退出，积累有依据的模式样本。"
    },
    trading: {
        workspace: "trading",
        title: "持仓执行",
        subtitle: "Action → Reason → Evidence",
        kicker: "交易工作区",
        description: "管理真实持仓、执行动作和证据留痕；不替代收盘复盘与预期判断。"
    },
    permission: {
        workspace: "trading",
        title: "交易决策",
        subtitle: "模式收益 · 明日预案 · 今日跟踪 · 交易权限",
        kicker: "交易工作区",
        description: "按大盘、板块、个股的顺序形成条件预案，再用竞价与盘中证据逐层核验。"
    },
    "decision-research": {
        workspace: "research", title: "市场模式观察", subtitle: "市场群体反馈 · 历史样本 · 覆盖范围",
        kicker: "研究工作区", description: "观察不同市场群体的历史表现；个人模式收益在交易复盘中核对。"
    },
    "decision-plan": {
        workspace: "trading", title: "明日预案", subtitle: "引用模式评估 · 方向与核心 · 次日条件",
        kicker: "交易决策 · 分析", description: "指数量能 → 三类反馈 → 两类模式 → 方向与核心 → 策略与主备 → 次日预期。"
    },
    "decision-live": {
        workspace: "trading", title: "今日跟踪", subtitle: "盘前竞价 · 盘中证据对照",
        kicker: "交易决策 · 实时", description: "引用已冻结的盘后依据，按大盘 > 板块 > 个股核对预期与变化。"
    },
    sectors: {
        workspace: "research",
        title: "板块研究",
        subtitle: "PART2 · 强弱、趋势与龙头",
        kicker: "研究工作区",
        description: "从板块扩散、资金口径和龙头/中军候选中识别市场主线。"
    },
    analysis: {
        workspace: "research",
        title: "个股研究",
        subtitle: "个股结构与策略边界",
        kicker: "研究工作区",
        description: "输入股票代码，查看行情、结构、支撑压力和执行边界。"
    },
    intraday: {
        workspace: "trading",
        title: "盘中监控",
        subtitle: "实时行情 · 触位验证",
        kicker: "交易工作区",
        description: "把 PART1 预设的关键位和候选票信号，用实时行情做在线验证。"
    },
    selection: {
        workspace: "research",
        title: "选品研究",
        subtitle: "单品指标 · Playbook 筛选",
        kicker: "研究工作区",
        description: "以个股结构生成候选并保留证据；个人交易预期、仓位与实际操作在交易复盘中记录。"
    },
    holdings: {
        workspace: "trading",
        title: "成交与持仓",
        subtitle: "逐笔成交 · 自动派生持仓",
        kicker: "交易工作区",
        description: "记录真实委托与逐笔成交，自动汇总持仓数量、成本、仓位、收益和风险动作。"
    },
    push: {
        workspace: "system",
        title: "日报推送",
        subtitle: "研究结论交付",
        kicker: "系统与交付",
        description: "先预览，再将结构化日报发送到已配置通道。"
    },
    data: {
        workspace: "system",
        title: "数据同步",
        subtitle: "Mongo 数据水位与网关",
        kicker: "系统与交付",
        description: "确认交易日数据、增量缺口与 Tushare 网关健康状态。"
    },
    system: {
        workspace: "system",
        title: "系统状态",
        subtitle: "运行依赖检查",
        kicker: "系统与交付",
        description: "集中检查 Mongo、消息通道和运行时依赖。"
    },
};

const workspaceLabels = {
    review: "复盘",
    research: "研究",
    trading: "交易",
    system: "系统与交付",
};

function openJournalSection(sectionId) {
    if (window.JournalWorkspace?.openSection?.(sectionId)) return;
    const section = document.getElementById(sectionId);
    if (!section) return;
    for (let ancestor = section; ancestor; ancestor = ancestor.parentElement) {
        if (ancestor.tagName === "DETAILS") ancestor.open = true;
    }
    section.scrollIntoView?.({block: "start", behavior: "instant"});
    if (sectionId === "journalAccountTools" && !holdingsState.loading) loadHoldings();
}

function switchTab(tabId, {legacy = false} = {}) {
    const journalSections = {holdings: "journalAccountTools", "decision-plan": "tradeJournalPlanSection", "decision-live": "journalRecords"};
    const retired = ["holdings", "trading", "permission", "decision-plan", "decision-live"];
    const originalTabId = tabId === "market" ? "review" : tabId;
    const requestedTabId = !legacy && retired.includes(originalTabId) ? "trade-journal" : originalTabId;
    const resolvedTabId = Object.prototype.hasOwnProperty.call(tabPresentation, requestedTabId) ? requestedTabId : "review";
    document.querySelectorAll(".tab-button").forEach((button) => {
        const selected = button.dataset.tab === resolvedTabId;
        button.classList.toggle("active", selected);
        button.setAttribute("aria-selected", String(selected));
        button.tabIndex = selected ? 0 : -1;
        if (selected && button.closest(".nav-group")) button.closest(".nav-group").open = true;
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
        const selected = panel.id === `tab-${resolvedTabId}`;
        panel.classList.toggle("active", selected);
        panel.hidden = !selected;
    });
    const view = tabPresentation[resolvedTabId];
    const workspace = view.workspace || "review";
    const workspaceDomain = document.getElementById("workspaceDomain");
    if (workspaceDomain) {
        workspaceDomain.textContent = workspaceLabels[workspace] || workspaceLabels.review;
        workspaceDomain.className = `workspace-domain is-${workspace}`;
    }
    document.body.dataset.workspace = workspace;
    document.getElementById("pageTitle").textContent = view.title;
    document.getElementById("pageSubtitle").textContent = view.subtitle;
    document.getElementById("pageKicker").textContent = view.kicker;
    document.getElementById("pageHeading").textContent = view.title;
    document.getElementById("pageDescription").textContent = view.description;
    document.querySelector(".global-toolbar")?.classList.toggle("trading-mode", ["review", "sentiment", "trade-journal", "firstboard", "boardeffects", "trading", "intraday", "selection", "holdings"].includes(resolvedTabId));
    document.body.classList.toggle("intraday-mode", resolvedTabId === "intraday");
    document.body.classList.toggle("market-mode", resolvedTabId === "market");
    document.body.classList.toggle("board-research-mode", ["firstboard", "boardeffects"].includes(resolvedTabId));
    document.body.classList.toggle("decision-mode", ["decision-research", "decision-plan", "decision-live"].includes(resolvedTabId));
    scheduleStorylineDocking();
    if (resolvedTabId === "review" && typeof window.loadReviewDashboard === "function") window.loadReviewDashboard();
    if (resolvedTabId === "sentiment" && typeof window.loadSentimentDashboard === "function") window.loadSentimentDashboard();
    if (resolvedTabId === "trade-journal" && window.TradeJournal) window.TradeJournal.enter();
    if (resolvedTabId === "trade-journal") {
        window.JournalWorkspace?.enter();
        if (!legacy && journalSections[tabId]) openJournalSection(journalSections[tabId]);
    }
    if (["firstboard", "boardeffects"].includes(resolvedTabId) && window.BoardResearch) window.BoardResearch.load(resolvedTabId, {cached:true});
    if (resolvedTabId === "trading") loadTradingWorkspace();
    if (resolvedTabId === "holdings" && !holdingsState.loading) loadHoldings();
    if (resolvedTabId === "selection") loadSelectionPool(false);
    if (resolvedTabId === "sectors" && window.ResearchEvidence) window.ResearchEvidence.loadNewFaces();
    if (["decision-research", "decision-plan", "decision-live"].includes(resolvedTabId) && window.TradeDecision) window.TradeDecision.enter(resolvedTabId);
}

function handleWorkspaceTabKeydown(event) {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    const currentTab = event.target.closest(".tab-button");
    const tabList = currentTab?.closest('[role="tablist"]');
    if (!currentTab || !tabList) return;
    const tabs = [...tabList.querySelectorAll('[role="tab"]')].filter(tab => !tab.hidden);
    const currentIndex = tabs.indexOf(currentTab);
    if (currentIndex < 0) return;
    let targetIndex = currentIndex;
    if (event.key === "Home") targetIndex = 0;
    if (event.key === "End") targetIndex = tabs.length - 1;
    if (["ArrowLeft", "ArrowUp"].includes(event.key)) targetIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    if (["ArrowRight", "ArrowDown"].includes(event.key)) targetIndex = (currentIndex + 1) % tabs.length;
    event.preventDefault();
    const targetTab = tabs[targetIndex];
    targetTab.focus();
    switchTab(targetTab.dataset.tab);
}

function refreshCurrentTab() {
    const tabId = activeTabId();
    if (["firstboard", "boardeffects"].includes(tabId)) return window.BoardResearch?.load(tabId);
    if (tabId === "review" && typeof window.loadReviewDashboard === "function") return window.loadReviewDashboard({force: true});
    if (tabId === "sentiment" && typeof window.loadSentimentDashboard === "function") return window.loadSentimentDashboard({force: true});
    if (tabId === "trade-journal") return window.TradeJournal?.load();
    if (tabId === "trading") return loadTradingWorkspace();
    if (tabId === "permission" || tabId === "market") return runMarketPermission();
    if (tabId === "sectors") return runSectorTrends();
    if (tabId === "analysis") return runStockPanel();
    if (tabId === "intraday") return runIntraday();
    if (tabId === "selection") return loadSelectionPool(true);
    if (tabId === "holdings") return loadHoldings();
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

// Present cached and freshly generated wave counts through the same rule gate.
function getWavePresentation(marketData) {
    const forecast = marketData?.pattern_forecast || {};
    const wave = forecast.wave_analysis || {};
    const text = (value) => {
        if (value == null) return '';
        if (typeof value === 'string' || typeof value === 'number') return String(value);
        if (Array.isArray(value)) return value.map(text).filter(Boolean).join('；');
        return typeof value === 'object' ? [value.field || value.label || '', value.note || value.message || value.reason || value.description || ''].filter(Boolean).map(text).join('：') : '';
    };
    const points = (Array.isArray(wave.wave_points) ? wave.wave_points : []).filter(p => p && typeof p.date === 'string' && p.price != null && Number.isFinite(Number(p.price)) && Number(p.price) > 0);
    const violations = (Array.isArray(wave.wave_rule_violations) ? wave.wave_rule_violations : []).map(text).filter(Boolean);
    const impulse = /^wave_[1-5]/.test(wave.current_wave || '') || wave.wave_phase === 'impulse' || points.some(p => /^wave_/.test(p.label || ''));
    // Old snapshots may contain contradictory narrative even when rule findings
    // were omitted. Test only the explicit ascending impulse interpretation.
    if (impulse && points.length >= 3 && points[0].kind === 'L' && points[1].kind === 'H' && points[2].kind === 'L' && Number(points[2].price) < Number(points[0].price) && !violations.some(v => v.includes('第2浪'))) violations.push('候选第2浪低点低于第1浪起点，推动浪计数未通过。');
    if (impulse && points.length >= 5 && points[4].kind === 'L' && Number(points[4].price) < Number(points[1].price) && !violations.some(v => v.includes('第4浪'))) violations.push('候选第4浪低点进入第1浪区间，原推动浪计数需重新识别。');
    const invalid = wave.structure_status === 'invalidated' || (impulse && violations.length > 0);
    const labels = {wave_1_start:'第1浪起点', wave_1_end:'第1浪高点', wave_2_end:'第2浪低点', wave_3_end:'第3浪高点', wave_4_end:'第4浪低点'};
    const pointLabel = p => invalid ? (p.kind === 'H' ? '观察高点' : '观察低点') : labels[String(p.label || '').split(' (')[0]] || text(p.label) || '结构端点';
    const gaps = (Array.isArray(forecast.data_gaps) ? forecast.data_gaps : []).filter(g =>
        !(g && typeof g === 'object' && /外部.*文档/.test(g.field || '')) &&
        (typeof g === 'string' || (g && !['integrated', 'ready', 'ok'].includes(g.status)))).map(text).filter(Boolean);
    const keyLevels = invalid ? (forecast.key_levels_tomorrow || []).map(l => ({...l, kind:l.direction || 'reference'})) : (wave.fibonacci_levels || []);
    const levels = keyLevels.filter(l => l && l.level != null && Number.isFinite(Number(l.level)) && Number(l.level)>0)
        .filter((l,i,items)=>items.findIndex(v=>Number(v.level).toFixed(2)===Number(l.level).toFixed(2))===i);
    return {invalid, points, pointLabel, violations:[...new Set(violations)], gaps,
        title: invalid ? '推动浪假设未通过' : text(wave.current_wave_label) || '波浪结构待确认',
        reasoning: invalid ? '已识别出交替高低点，但规则反证否定了原推动浪计数。当前保留端点作结构观察，暂不采用原第5浪目标。' : text(wave.reasoning) || '目前没有足够证据确认具体浪型，先观察实际支撑和压力。',
        levels, forecast, wave};
}

function renderWaveKlineChart(marketData) {
    const state = getWavePresentation(marketData), fmt = value => Number(value).toLocaleString('zh-CN', {maximumFractionDigits:2});
    let rows = Array.isArray(marketData?.chart_data?.wave_series) ? marketData.chart_data.wave_series : [];
    rows = rows.filter(row => typeof row.date === 'string' && [row.open,row.high,row.low,row.close].every(value => value != null && value !== '' && Number.isFinite(Number(value)) && Number(value)>0)).slice().sort((a,b) => a.date.localeCompare(b.date));
    if (!rows.length) return '<div class="wave-chart-empty">日K数据不足，暂不绘制结构图。</div>';
    const earliest = state.points.map(p => p.date).sort()[0];
    if (earliest) {const i=rows.findIndex(row=>row.date>=earliest);if(i>=0)rows=rows.slice(Math.max(0,i-3));}
    rows = rows.slice(-60);
    const width=1000, height=320, top=24, bottom=296, left=12, right=988;
    const lo=Math.min(...rows.map(r=>Number(r.low))), hi=Math.max(...rows.map(r=>Number(r.high)));
    const padding=Math.max((hi-lo)*.12,hi*.004), low=lo-padding, high=hi+padding;
    const y=v=>bottom-(Number(v)-low)/(high-low)*(bottom-top), step=(right-left)/rows.length, x=i=>left+step*(i+.5);
    const indexByDate=new Map(rows.map((r,i)=>[r.date,i]));
    const levels=state.levels.filter(l=>Number(l.level)>=low && Number(l.level)<=high);
    const grid=Array.from({length:5},(_,i)=>{const price=high-(high-low)*i/4;return {price,py:y(price)};});
    const candleWidth=Math.max(3,Math.min(11,step*.62));
    const candles=rows.map((r,i)=>{const a=Number(r.open),b=Number(r.close),py=Math.min(y(a),y(b)),h=Math.max(1.5,Math.abs(y(a)-y(b)));return `<g class="rw-candle ${b>=a?'is-up':'is-down'}"><title>${escapeHtml(r.date)}　开 ${fmt(a)}　高 ${fmt(r.high)}　低 ${fmt(r.low)}　收 ${fmt(b)}</title><line x1="${x(i)}" y1="${y(r.high)}" x2="${x(i)}" y2="${y(r.low)}"/><rect x="${x(i)-candleWidth/2}" y="${py}" width="${candleWidth}" height="${h}" rx=".6"/></g>`;}).join('');
    const visiblePoints=state.points.map((p,i)=>({...p,index:i+1,row:indexByDate.get(p.date)})).filter(p=>p.row!=null);
    const marks=visiblePoints.map(p=>`<g class="rw-endpoint"><circle cx="${x(p.row)}" cy="${y(p.price)}" r="3.2"/></g>`).join('');
    const line=visiblePoints.length>1?`<polyline class="rw-structure" points="${visiblePoints.map(p=>`${x(p.row)},${y(p.price)}`).join(' ')}"/>`:'';
    const tickIndices=[...new Set([0,...Array.from({length:4},(_,i)=>Math.round((rows.length-1)*(i+1)/5)),rows.length-1])];
    const last=rows[rows.length-1], outside=state.levels.length-levels.length;
    return `<figure class="review-wave-figure" data-wave-structure="${state.invalid?'invalidated':'hypothesis'}">
      <figcaption class="rw-heading"><div><strong>日K与结构观察</strong><span>${escapeHtml(rows[0].date)} — ${escapeHtml(last.date)} · ${rows.length}个交易日</span></div><div class="rw-last"><span>收盘</span><b>${fmt(last.close)}</b></div></figcaption>
      <div class="rw-legend"><span class="rw-key is-price">日K · 红涨绿跌</span><span class="rw-key is-structure">交替高低点</span><span class="rw-key is-reference">${state.invalid?'实际支撑／压力':'模型参考位'}</span></div>
      <div class="rw-chart-grid"><div class="rw-y-axis" aria-hidden="true">${grid.map(t=>`<span style="top:${t.py/height*100}%">${fmt(t.price)}</span>`).join('')}</div>
      <div class="rw-plot"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(marketData?.benchmark_name||'指数')}日K及结构端点，${state.invalid?'原推动浪假设未通过':'模型仅为条件推演'}">
      ${grid.map(t=>`<line class="rw-grid" x1="0" y1="${t.py}" x2="${width}" y2="${t.py}"/>`).join('')}
      ${levels.map(l=>`<line class="rw-reference is-${l.kind==='support'?'support':'resistance'}" x1="0" y1="${y(l.level)}" x2="${width}" y2="${y(l.level)}"><title>${escapeHtml(l.label)} ${fmt(l.level)}</title></line>`).join('')}
      ${line}${candles}${marks}<line class="rw-close" x1="0" y1="${y(last.close)}" x2="${width}" y2="${y(last.close)}"/></svg>
      ${visiblePoints.map(p=>`<span class="rw-point-number" style="left:${x(p.row)/width*100}%;top:${Math.max(12,Math.min(308,y(p.price)+(p.kind==='H'?-13:13)))/height*100}%" title="${escapeHtml(state.pointLabel(p))} ${fmt(p.price)}">${p.index}</span>`).join('')}</div>
      <div class="rw-x-spacer"></div><div class="rw-x-axis" aria-hidden="true">${tickIndices.map(i=>`<span style="left:${x(i)/width*100}%">${escapeHtml(rows[i].date.slice(5))}</span>`).join('')}</div></div>
      <ol class="rw-point-ledger" aria-label="结构端点">${visiblePoints.map(p=>`<li><span class="rw-ledger-number">${p.index}</span><div><span>${escapeHtml(state.pointLabel(p))}</span><strong>${fmt(p.price)}</strong><time datetime="${escapeHtml(p.date)}">${escapeHtml(p.date.slice(5))}</time></div></li>`).join('')}</ol>
      ${state.levels.length?`<div class="rw-level-ledger">${state.levels.map(l=>`<div><span>${escapeHtml(l.label)}</span><strong>${fmt(l.level)}</strong></div>`).join('')}</div>`:''}
      <p class="rw-footnote">${state.invalid?'推动浪计数已被规则反证否定；编号仅定位已识别的高低点。':'端点来自历史高低点识别，浪型与目标位仍需后续走势验证。'}${outside?'超出当前纵轴范围的参考位仅列在图下。':''}</p>
    </figure>`;
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

function formatSignedContracts(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
    const number = Number(value);
    return `${number > 0 ? "+" : ""}${Math.round(number).toLocaleString("zh-CN")}`;
}

function aggregateFuturesHistorySnapshot(snapshot) {
    const products = (Array.isArray(snapshot?.products) ? snapshot.products : []).filter((item) => item.status === "ok");
    const sum = (key) => products.reduce((total, item) => total + Number(item?.[key] || 0), 0);
    const summary = snapshot?.institutional_summary || {};
    const top20Long = sum("top20_long");
    const top20Short = sum("top20_short");
    const citicLong = sum("citic_long");
    const citicShort = sum("citic_short");
    const netShort = summary.top20_net_short ?? (products.length ? top20Short - top20Long : null);
    const netShortChange = summary.top20_net_short_change ?? (products.length
        ? sum("top20_short_change") - sum("top20_long_change")
        : null);
    const citicNetShort = summary.citic_net_short ?? (products.length ? citicShort - citicLong : null);
    return {
        date: snapshot?.analysis_date || products[0]?.data_date || "-",
        productCount: products.length,
        top20Long: products.length ? top20Long : null,
        top20Short: products.length ? top20Short : null,
        netShort,
        netShortChange,
        citicNetShort,
        behavior: summary.behavior || (products.length ? "变化有限" : "数据待同步"),
    };
}

function futuresProductHistory(snapshots, product) {
    return (snapshots || []).map((snapshot) => {
        const products = (snapshot.products || []).filter(item => item.status === 'ok' && (!item.data_date || item.data_date === snapshot.analysis_date));
        let row = products.find((item) => String(item.product || "").toUpperCase() === product);
        if (product === 'TOTAL') {
            const channels = ['IF', 'IH', 'IC', 'IM'].map(code => products.find(item => item.product === code));
            const complete = channels.every(Boolean);
            row = {status: complete ? 'ok' : 'partial', contract: complete ? channels.map(item => item.contract).join(' / ') : `已到 ${channels.filter(Boolean).length}/4 品种`};
            for (const key of ['top20_long', 'top20_short', 'top20_long_change', 'top20_short_change', 'citic_long', 'citic_short', 'citic_long_change', 'citic_short_change']) {
                row[key] = complete && channels.every(item => typeof item[key] === 'number' && Number.isFinite(item[key]))
                    ? channels.reduce((sum, item) => sum + item[key], 0) : null;
            }
        }
        const valid = row?.status === "ok";
        const top20Long = valid && row.top20_long != null ? Number(row.top20_long) : null;
        const top20Short = valid && row.top20_short != null ? Number(row.top20_short) : null;
        const citicLong = valid && row.citic_long != null ? Number(row.citic_long) : null;
        const citicShort = valid && row.citic_short != null ? Number(row.citic_short) : null;
        const top20Change = valid && row.top20_long_change != null && row.top20_short_change != null
            ? Number(row.top20_short_change) - Number(row.top20_long_change)
            : null;
        const citicChange = valid && row.citic_long_change != null && row.citic_short_change != null
            ? Number(row.citic_short_change) - Number(row.citic_long_change)
            : null;
        return {
            date: snapshot.analysis_date || row?.data_date || "-",
            contract: row?.contract || null,
            top20Long,
            top20Short,
            top20NetShort: valid && row.top20_net_short != null ? Number(row.top20_net_short) : (top20Long != null && top20Short != null ? top20Short - top20Long : null),
            top20Change,
            citicLong,
            citicShort,
            citicNetShort: citicLong != null && citicShort != null ? citicShort - citicLong : null,
            citicChange,
        };
    });
}

function renderFuturesProductTable(product, snapshots) {
    const rows = futuresProductHistory(snapshots, product);
    const columns = [
        ["前20多单持仓", "top20Long"], ["前20空单持仓", "top20Short"], ["当前净空", "top20NetShort"],
        ["净空日增减", "top20Change"], ["中信多单持仓", "citicLong"], ["中信空单持仓", "citicShort"],
        ["中信当前净空", "citicNetShort"], ["中信净空日增减", "citicChange"],
    ];
    const cell = (item, label, key) => {
        const value = item[key];
        const signal = ["top20NetShort", "top20Change", "citicNetShort", "citicChange"].includes(key);
        const tone = signal && value != null ? (value > 0 ? "is-risk" : value < 0 ? "is-positive" : "") : "";
        return `<td data-label="${label}" class="${tone}">${escapeHtml(value == null ? "—" : signal ? formatSignedContracts(value) : value.toLocaleString('zh-CN'))}</td>`;
    };
    return `<section class="market-v2-futures-product-card">
      <header><strong>${product === 'TOTAL' ? '渠道合计' : escapeHtml(product)}</strong><span>当前持仓 · 单位：手 · 过去 7 个交易日</span></header>
      <div class="market-v2-futures-product-scroll"><table aria-label="${escapeHtml(product)}逐日机构持仓">
        <thead><tr><th scope="col">日期 / 合约</th>${columns.map(([label]) => `<th scope="col">${label}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((item) => `<tr><th scope="row">${escapeHtml(item.date)}<small class="futures-contract">${escapeHtml(item.contract || "待同步")}</small></th>${columns.map(([label, key]) => cell(item, label, key)).join("")}</tr>`).join("") || `<tr><td colspan="9">数据待同步</td></tr>`}</tbody>
      </table></div>
    </section>`;
}

function marketV2Tone(value) {
    const text = String(value || "");
    if (/净空下降|空头.*下降|风险释放/.test(text)) return "is-positive";
    if (/风险|下跌|退潮|撤离|下降|弱势/.test(text)) return "is-risk";
    if (/强势|偏强|上涨|释放|承接|增加/.test(text)) return "is-positive";
    return "is-neutral";
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
    if (!leader || !leader.ticker) return "当前没有通过分钟数据资格门的龙头候选；待目标日个股与基准分钟 K 完整后重排。";
    const subs = leader.leader_subscores || {};
    return reasonList([
        `综合分 ${escapeHtml(leader.leader_score || "-")}，角色 ${escapeHtml(leader.role_label || "-")}。`,
        `5日收益 ${escapeHtml(formatPercent(leader.return_5d))}，相对板块 ${escapeHtml(formatPercent(leader.relative_return_5d))}，5日涨停 ${escapeHtml(leader.limit_up_count_5d || 0)} 次，大涨 ${escapeHtml(leader.big_up_count_5d || 0)} 次。`,
        `子项：${scoreRef("启动", subs.startup_score, subs.intraday_data_used ? `启动分已混入真实 5 分钟 K：日线代理 ${subs.daily_startup_proxy ?? "-"}，分时率先启动 ${subs.intraday_startup_score ?? "-"}。` : "启动分当前使用涨停次数、相对板块收益和率先走强日线代理。")}${scoreRef("强度", subs.strength_score, "强度分：5日收益排名、涨停次数、大涨次数。")}${scoreRef("带动", subs.drive_score, "带动分：板块上涨占比、板块当日收益和个股成交额排名。")}${scoreRef("抗跌/修复", subs.resilience_score, "抗跌/修复分：趋势未破、收盘位置、近10日最大回撤。")}${scoreRef("封板质量代理", subs.board_quality_score, "封板质量仍是代理：目前用涨停、收盘位置、上影线和成交额排名；接入涨停/炸板快照后替换。")}`,
        subs.intraday_data_used
            ? `分钟启动：真实 5 分钟 K 已参与启动分，分时子分 ${escapeHtml(subs.intraday_startup_score ?? "-")}；封板质量仍待涨停/炸板真实快照。`
            : "分钟启动：数据资格异常；缺分钟事实的股票不允许进入正式排名。",
        `比较：板块上涨占比 ${escapeHtml(formatPercent(sector.metrics && sector.metrics.up_ratio))}。如果个股强但板块不扩散，会降为“强势先锋”而不是总龙头。`,
    ]);
}

function zhongjunExplanation(zhongjun) {
    if (!zhongjun || !zhongjun.ticker) return "当前没有通过分钟数据资格门的中军候选；待目标日个股与基准分钟 K 完整后重排。";
    const subs = zhongjun.zhongjun_subscores || {};
    const ir = zhongjun.intraday_resonance || {};
    // v6：中军说明加入分时共振（如有分钟数据）
    const resonanceLine = ir.has_data
        ? `分时共振：<b>${escapeHtml(subs.resonance_score ?? "-")}</b>分（早盘 ${escapeHtml((ir.segments?.early_open?.score) ?? "-")}，午后 ${escapeHtml((ir.segments?.afternoon_mid?.score) ?? "-")}，尾盘 ${escapeHtml((ir.segments?.tail?.score) ?? "-")}）${ir.volume_boost > 0 ? `，尾盘放量 +${escapeHtml(ir.volume_boost)}` : ""}`
        : `分时共振：数据资格异常；缺分钟事实的股票不允许进入正式排名`;
    return reasonList([
        `综合分 ${escapeHtml(zhongjun.zhongjun_score || "-")}，角色 ${escapeHtml(zhongjun.role_label || "-")}。`,
        `容量：成交额 ${escapeHtml(formatTradeAmount(zhongjun.amount))}，流通市值 ${escapeHtml(formatMarketValue(zhongjun.circ_mv))}，换手率 ${escapeHtml(formatPercent((zhongjun.turnover_rate || 0) / 100))}。`,
        `资金：主力净流入 ${escapeHtml(formatMoneyWan(zhongjun.main_net_inflow))}，净流入占比 ${escapeHtml(formatPercent(zhongjun.main_net_inflow_rate))}。`,
        `趋势：收盘 ${escapeHtml(zhongjun.close || "-")}，MA10 ${escapeHtml(zhongjun.ma10 || "-")}，趋势${zhongjun.trend_unbroken ? "未破" : "已弱化"}。`,
        resonanceLine,
        `子项：${scoreRef("容量", subs.capacity_score, "容量分：流通市值在板块内的分位。")}${scoreRef("成交稳定", subs.amount_stability_score, "成交稳定分：成交额排名和近5日成交额波动。")}${scoreRef("净流入", subs.net_flow_score ?? "待数据", "净流入分：moneyflow 大单+特大单净流入占成交额比例。Tushare 按订单大小分类，主力/机构常拆单进入，权重已从 10% 降到 4%。")}${scoreRef("趋势稳定", subs.trend_stability_score, "趋势稳定分：MA10/MA20 趋势和近10日最大回撤。")}${scoreRef("代表性", subs.representation_score, "代表性分：板块内成交额或收益分位任一较高即算代表性强。承接了 net_flow 让出的权重。")}${scoreRef("分时共振", subs.resonance_score, "分时共振（v6）：5min K 线在 5 个时段与大盘对比。同向且强于大盘=100，弱跟随=55，背离=20。抗跌上涨=100。")}${scoreRef("换手稳定", subs.turnover_stability_score, "换手稳定分：换手率处于可承接区间更高。")}`,
    ]);
}

/**
 * v6 · 展示某板块的 Top 3 龙头/中军候选，带 tabs 切换。
 * kind: "leader" | "zhongjun"
 * sectorKey: 板块唯一 ID，供每张卡的 state 独立
 */
function renderTopThreeCandidates(kind, sector, sectorKey) {
    const listKey = kind === "leader" ? "leader_candidates" : "zhongjun_candidates";
    const scoreKey = kind === "leader" ? "leader_score" : "zhongjun_score";
    const title = kind === "leader" ? "龙头候选" : "中军候选";
    const coverage = sector.minute_coverage || {};
    const coverageText = `${coverage.available_count ?? 0}/${coverage.candidate_count ?? 0}`;
    const definition = kind === "leader"
        ? `龙头看先动、最强、带动、抗跌和修复快；候选分钟覆盖 ${coverageText}，正式排名只接纳个股与基准分钟事实完整的股票。`
        : `中军看容量、成交稳定、趋势稳定和承接；候选分钟覆盖 ${coverageText}，正式排名必须先完成 5 分钟 K 共振计算。`;
    const explainFn = kind === "leader" ? (item) => leaderExplanation(item, sector) : (item) => zhongjunExplanation(item);

    const candidates = (sector[listKey] || []).slice(0, 3);
    if (!candidates.length) {
        const pendingCount = (sector.pending_minute_candidates || []).length;
        return metricBlock(
            title,
            definition,
            "分钟数据待补",
            `${explainFn({})}${pendingCount ? ` 当前有 ${escapeHtml(pendingCount)} 只日线短名单股票等待分钟事实。` : ""}`,
        );
    }
    // 每个板块 kind 独立的 stateId
    const stateId = `topthree_${kind}_${sectorKey}`;
    const selected = topThreeState[stateId] || 0;
    const activeIdx = Math.min(selected, candidates.length - 1);
    const active = candidates[activeIdx];

    // Tabs（Top 1/2/3）
    const tabs = candidates.map((c, i) => `
        <button
          type="button"
          class="topthree-tab ${i === activeIdx ? "is-active" : ""}"
          data-topthree-tab
          data-topthree-state="${escapeHtml(stateId)}"
          data-topthree-index="${i}"
          title="切换到 Top ${i + 1}"
        >
          <span class="topthree-rank">#${i + 1}</span>
          <span class="topthree-name">${escapeHtml(c.name || c.ticker || "-")}</span>
          <span class="topthree-score">${escapeHtml(c[scoreKey] ?? "-")}</span>
        </button>
    `).join("");

    const activeValue = `${stockLink(active.name, active.ticker)} · ${escapeHtml(active.role_label || "-")} · ${escapeHtml(active[scoreKey] ?? "-")}`;

    return `
        <div class="metric-row metric-topthree">
          <div class="metric-label">${escapeHtml(title)} ${definitionBubble(definition)}</div>
          <div class="topthree-tabs">${tabs}</div>
          <div class="metric-value">${activeValue}</div>
          <div class="metric-explain">${explainFn(active)}</div>
        </div>
    `;
}

// 每个板块 top 3 tab 的选中态。key = topthree_{kind}_{sectorKey}, value = 0/1/2
const topThreeState = {};

// 事件委派：点击 tab 更新 state 并局部重渲染当前板块详情
document.addEventListener("click", (evt) => {
    const btn = evt.target && evt.target.closest && evt.target.closest("[data-topthree-tab]");
    if (!btn) return;
    const stateId = btn.getAttribute("data-topthree-state");
    const idx = Number(btn.getAttribute("data-topthree-index"));
    if (!stateId || !Number.isFinite(idx)) return;
    topThreeState[stateId] = idx;
    // 触发当前 sector detail 重渲染
    if (typeof latestSectorData !== "undefined" && latestSectorData) {
        showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
    }
});

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
    if (latestSectorData) {
        showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
        document.querySelector('#sector-today .sector-chip[aria-pressed="true"]')?.focus({preventScroll: true});
    }
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
        <div class="sector-switcher" role="group" aria-label="切换中期趋势板块">
          ${sectors.map((sector, index) => `
            <button type="button" class="sector-chip ${sectorStrengthClass(sector)} ${index === selectedSectorIndex ? "is-active" : ""}" aria-pressed="${index === selectedSectorIndex}" aria-label="${escapeHtmlAttr(`${sector.sector_name}，中期趋势${sector.stage_label || "待确认"}，趋势分${scoreText(sectorMainlineScore(sector))}`)}" onclick="setSelectedSector(${index})">
              <strong title="${escapeHtmlAttr(sector.sector_name)}">${escapeHtml(sector.sector_name)}</strong>
              <span class="sector-chip-stage">${escapeHtml(sector.stage_label || "待确认")}</span>
              <span class="sector-chip-score" title="趋势分">${escapeHtml(scoreText(sectorMainlineScore(sector)))}</span>
            </button>
          `).join("")}
        </div>
      `;
}

function renderFundFlowBlock(sector, fund, metrics, values) {
    const status = translateStatus(fund.data_status);
    const netFlow = formatMoneyWan(fund.main_net_inflow);
    const netRate = formatPercent(fund.main_net_inflow_rate);
    const label = fund.label || "资金方向待同步";
    return `<div class="fund-flow-block">
      <header><span>日级资金结构 ${definitionBubble("方向使用 Tushare 官方 net_mf_amount；订单分档只解释规模结构，不等于盘口主动买卖。")}</span><small>${escapeHtml(values.flowDateLabel)}</small></header>
      <strong>${escapeHtml(label)}</strong>
      <div class="fund-flow-facts">
        <span><small>官方净流入</small><b>${escapeHtml(netFlow)}</b></span>
        <span><small>占成交额</small><b>${escapeHtml(netRate)}</b></span>
        <span><small>净流入扩散</small><b>${escapeHtml(formatPercent(fund.positive_moneyflow_ratio))}</b></span>
        <span><small>近3日流入</small><b>${escapeHtml(fund.moneyflow_persistence_3d ?? "-")} 天</b></span>
      </div>
      <p>成交额占比 ${escapeHtml(formatPercent(metrics.market_share))} · 相对5日均 ${escapeHtml(formatRatio(metrics.amount_ratio_5))} · 数据 ${escapeHtml(status)}</p>
      <details class="fund-flow-method"><summary>查看资金口径与订单规模明细</summary><div>
        <p>${escapeHtml(fund.meaning || "moneyflow 尚未同步，只保留成交活跃度观察。")}</p>
        <p><b>为什么不能四档相减：</b>官方净流入不能由四档买卖简单相减；四档两侧合计受成交恒等式约束，也不用于板块主线排名。</p>
        <dl>
          <div><dt>大/特大单买方</dt><dd>${escapeHtml(formatMoneyWan(values.mainOrderBuy))}</dd></div>
          <div><dt>大/特大单卖方</dt><dd>${escapeHtml(formatMoneyWan(values.mainOrderSell))}</dd></div>
          <div><dt>中/小单买方</dt><dd>${escapeHtml(formatMoneyWan(values.retailOrderBuy))}</dd></div>
          <div><dt>中/小单卖方</dt><dd>${escapeHtml(formatMoneyWan(values.retailOrderSell))}</dd></div>
        </dl>
      </div></details>
    </div>`;
}

function renderSelectedSectorDetail(sector) {
    // v6：龙头/中军改用 Top 3 tabs 展示，state 用板块名做 key（同一板块切换 tab 时保留选中）
    const sectorKey = (sector.sector_name || "").replace(/\s+/g, "_") || `idx${selectedSectorIndex}`;
    const divergence = sector.divergence || {};
    const repair = sector.repair || {};
    const fund = sector.fund_flow || {};
    const metrics = sector.metrics || {};
    const flowNumber = (value) => value == null || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
    const mainOrderBuy = flowNumber(metrics.main_order_buy);
    const mainOrderSell = flowNumber(metrics.main_order_sell);
    const retailOrderBuy = flowNumber(metrics.retail_order_buy);
    const retailOrderSell = flowNumber(metrics.retail_order_sell);
    const flowAsOf = fund.as_of_date || metrics.moneyflow_asof_date;
    const flowDateLabel = flowAsOf ? `${flowAsOf}${fund.is_stale || metrics.moneyflow_is_stale ? "（最近已完成资金日）" : "（当日）"}` : "待同步";
    return `
        <div class="sector-detail-grid">
          <article class="sector-detail-card full">
            <h3>${escapeHtml(sector.sector_name)} · 中期趋势 ${escapeHtml(sector.stage_label)}</h3>
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
          <article class="sector-detail-card sector-metric-column">
            ${renderFundFlowBlock(sector, fund, metrics, {
                flowDateLabel,
                mainOrderBuy: Number.isFinite(mainOrderBuy) ? mainOrderBuy : null,
                mainOrderSell: Number.isFinite(mainOrderSell) ? mainOrderSell : null,
                retailOrderBuy: Number.isFinite(retailOrderBuy) ? retailOrderBuy : null,
                retailOrderSell: Number.isFinite(retailOrderSell) ? retailOrderSell : null,
            })}
          </article>
          <article class="sector-detail-card sector-metric-column">
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
          <article class="sector-detail-card sector-metric-column">
            ${metricBlock(
        "分歧 / 修复",
        "分歧是涨幅、成交额、广度、核心股反馈之间出现不一致；修复必须有前置分歧，并且板块强于大盘、广度改善、核心先修复。",
        `${escapeHtml(divergence.label || "-")} / ${escapeHtml(repair.label || "-")}`,
        divergenceExplanation(sector, divergence, repair)
    )}
          </article>
          <article class="sector-detail-card full sector-core-candidates">
            ${renderTopThreeCandidates("leader", sector, sectorKey)}
            ${renderTopThreeCandidates("zhongjun", sector, sectorKey)}
          </article>
        </div>
      `;
}

function renderTodayStrongSectors(data) {
    const sectors = data.top_sectors || [];
    const summary = data.summary || {};
    if (!sectors.length) {
        return `
          <section id="sector-today" class="sector-feature sector-trend-candidates">
            <h3>中期趋势候选</h3>
            <p class="section-conclusion">暂无可展示板块。</p>
          </section>
        `;
    }
    const selected = sectors[Math.min(Math.max(selectedSectorIndex, 0), sectors.length - 1)];
    const leader = sectors[0];
    const leaderHeadline = `趋势排名第一：${leader.sector_name}，趋势分 ${scoreText(sectorMainlineScore(leader))}，当日健康分 ${scoreText(sectorHealthScore(leader))}。`;
    const leaderConclusion = `20 / 60 日相对强度权重合计 60%；排名不代表当日资金流入或入场信号。中期趋势阶段：${leader.stage_label || "-"}。`;
    return `
        <section id="sector-today" class="sector-feature sector-trend-candidates">
          <div class="feature-header">
            <div>
              <h3>中期趋势候选</h3>
              <p>MA5 从这些板块的候选中筛选。先核对上方当日资金，再查看个股是否通过全部入池条件。</p>
            </div>
          </div>
          <section class="sector-trend-summary" aria-label="中期趋势概览">
            <p class="sector-trend-leader">${escapeHtml(leaderHeadline)}</p>
            <p>${escapeHtml(leaderConclusion)}</p>
            <div class="sector-trend-meta">
              <span>基准 ${escapeHtml(data.benchmark_name || data.benchmark_ticker)}</span>
              <span>${escapeHtml(data.sector_source)}</span>
              <span>确认/修复 ${escapeHtml(summary.confirmed_or_repair_count || 0)}</span>
              <span>分歧 ${escapeHtml(summary.divergence_count || 0)}</span>
            </div>
            ${data.sector_source_note ? `<details class="sector-trend-method"><summary>数据来源与统计范围</summary><p>${escapeHtml(data.sector_source_note)}</p></details>` : ""}
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
          ` : `<p class="section-conclusion">暂无趋势榜数据，先查看中期趋势候选。</p>`}
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
              <small>${escapeHtml(data.sector_source_note || '东方财富统一概念与三级行业成分；以所选日归档注明的范围为准。')}</small>
            </summary>
            <div class="knowledge-grid">
              <article class="knowledge-panel full concept-roadmap-panel">
                <h4>成分范围与历史边界</h4>
                <p class="hint">概念与细分行业分别按真实成分汇总，成分可能重叠。资金对照只用完整成分已归档的真实概念和细分行业；MA5先做全市场个股筛选，再交叉核对板块趋势与资金。最新成分重建历史时须以归档说明为准，不将事后成分当作事前已知。</p>
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

function syncSelectionPickedControls() {
    const tickers = selectedTrendPoolTickers();
    const count = document.getElementById("selectionPickedCount");
    const list = document.getElementById("selectionPickedList");
    if (count) count.textContent = String(tickers.length);
    if (!list) return;
    if (!tickers.length) {
        list.innerHTML = "<p>尚未选择股票</p>";
        return;
    }
    list.innerHTML = tickers.map((ticker) => {
        const item = selectedTrendPool[ticker] || {};
        return `<button type="button" class="selection-picked-item" onclick="removeTrendPoolCandidate('${escapeHtml(ticker)}')" title="点击从当前选品中移除">
          <span><strong>${escapeHtml(item.name || ticker)}</strong><small>${escapeHtml(ticker)}${item.sector_name ? ` · ${escapeHtml(item.sector_name)}` : ""}</small></span><b>×</b>
        </button>`;
    }).join("");
}

function removeTrendPoolCandidate(ticker) {
    setTrendPoolCandidate({ticker}, false);
}

function scheduleSelectionStockSearch(value) {
    if (selectionSearchTimer) clearTimeout(selectionSearchTimer);
    const query = String(value || "").trim();
    const root = document.getElementById("selectionStockSearchResults");
    if (!query) {
        if (root) {
            root.hidden = true;
            root.innerHTML = "";
        }
        return;
    }
    selectionSearchTimer = setTimeout(() => runSelectionStockSearch(query), 180);
}

async function runSelectionStockSearch(query) {
    const root = document.getElementById("selectionStockSearchResults");
    if (!root) return;
    const version = ++selectionSearchVersion;
    root.hidden = false;
    root.innerHTML = '<p class="selection-search-state">正在匹配...</p>';
    try {
        const data = await fetchJson(`/api/v1/stocks/search?q=${encodeURIComponent(query)}&limit=10`, {timeoutMs: 8000});
        if (version !== selectionSearchVersion) return;
        const items = data.items || [];
        root.innerHTML = items.length ? items.map((item) => {
            const encoded = encodeURIComponent(JSON.stringify(item)).replace(/'/g, "%27");
            const selected = Boolean(selectedTrendPool[normalizeTrendPoolTicker(item.ticker)]);
            return `<button type="button" class="selection-search-item ${selected ? "is-selected" : ""}" onclick="addSelectionSearchResult('${encoded}')">
              <span><strong>${escapeHtml(item.name || item.ticker)}</strong><small>${escapeHtml(item.ticker)} · ${escapeHtml(item.industry || item.market || "-")}</small></span>
              <b>${selected ? "已选" : "+ 添加"}</b>
            </button>`;
        }).join("") : '<p class="selection-search-state">没有匹配的股票</p>';
    } catch (error) {
        if (version !== selectionSearchVersion) return;
        root.innerHTML = `<p class="selection-search-state error">${escapeHtml(formatError(error))}</p>`;
    }
}

function addSelectionSearchResult(encoded) {
    let item = null;
    try {
        item = JSON.parse(decodeURIComponent(encoded));
    } catch {
        return;
    }
    const ticker = normalizeTrendPoolTicker(item.ticker);
    const selected = Boolean(selectedTrendPool[ticker]);
    setTrendPoolCandidate({
        ticker,
        name: item.name || ticker,
        sector_name: item.industry || "",
        source: "stock_search",
    }, !selected);
    const input = document.getElementById("selectionStockSearch");
    if (input) input.value = "";
    const root = document.getElementById("selectionStockSearchResults");
    if (root) {
        root.hidden = true;
        root.innerHTML = "";
    }
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
    if (document.getElementById("tab-selection")?.classList.contains("active")) renderCandidates(latestCandidateRows);
}

const REVIEW_TACTICS = {red_k_net_inflow:'红K净流入', structure_repair_520:'结构修复520', weekly_breakout:'周K突破套利'};
const candidateEvidenceByStrategy = {};
let selectionQueryDate = '';

function selectedCandidateStrategyId() {
    return document.getElementById("candidateStrategySelect")?.value || DEFAULT_CANDIDATE_STRATEGY_ID;
}

function isOversoldFirstBoardStrategy(strategyId = selectedCandidateStrategyId()) {
    const normalized = String(strategyId || "").trim();
    return normalized === OVERSOLD_FIRST_BOARD_STRATEGY_ID || normalized === "oversold_first_board";
}

function syncCandidateStrategyChrome(strategyId = selectedCandidateStrategyId()) {
    const ruleStrip = document.getElementById("selectionRuleStrip");
    if (!ruleStrip) return;
    if (REVIEW_TACTICS[strategyId]) {
        const conditions = {red_k_net_inflow:'实心阳线 · 净流入 · 行业共振', structure_repair_520:'MA5拐点 · 接近或上穿MA20', weekly_breakout:'日K突破前100个交易日高点'};
        ruleStrip.innerHTML = `<span><b>范围</b> 全市场已覆盖日线</span><span><b>形态</b> ${conditions[strategyId]}</span><span><b>输出</b> 研究候选，待回测验证</span>`;
        ruleStrip.setAttribute('aria-label', '选品口径');
        return;
    }
    if (isOversoldFirstBoardStrategy(strategyId)) {
        ruleStrip.innerHTML = `
          <span><b>T-1 流动性</b> A15 &lt; 30 亿</span>
          <span><b>T-1 形态</b> R7 &lt; 15% · 远离上方均线</span>
          <span><b>T 日交接</b> 入选后明早自动监控</span>
        `;
        ruleStrip.setAttribute("aria-label", "超跌首板点火选品口径");
        return;
    }
    ruleStrip.innerHTML = `
      <span><b>范围</b> 全市场独立扫描</span>
      <span><b>条件</b> 原硬条件 + 大盘量能动态上限</span>
      <span><b>输出</b> 入池后验证触发条件</span>
    `;
    ruleStrip.setAttribute("aria-label", "选品口径");
}

function candidatePayloadRows(payload, strategyId) {
    const sourceRows = Array.isArray(payload)
        ? payload
        : payload?.candidates || payload?.items || payload?.signals || payload?.candidate_pool?.items || [];
    const analysisDate = payload?.analysis_date || payload?.selection_as_of || "";
    return (Array.isArray(sourceRows) ? sourceRows : []).map((row, index) => {
        const candidate = row?.candidate && typeof row.candidate === "object" ? row.candidate : {};
        const snapshot = row?.feature_snapshot && typeof row.feature_snapshot === "object" ? row.feature_snapshot : {};
        const selectionGate = row?.selection_gate || snapshot.selection_gate || candidate.selection_gate || {};
        const ticker = normalizeTrendPoolTicker(row?.ticker || candidate.ticker || snapshot.ticker);
        const gatePassed = selectionGate.passed;
        return {
            ...candidate,
            ...snapshot,
            ...row,
            ticker,
            name: row?.name || candidate.name || snapshot.name || ticker,
            sector_name: row?.sector_name || row?.industry || candidate.sector_name || snapshot.sector_name || snapshot.industry || "",
            strategy_id: row?.strategy_id || payload?.strategy_id || strategyId,
            strategy_version: row?.strategy_version || payload?.strategy_version || selectionGate.strategy_version,
            selection_gate: selectionGate,
            selection_as_of: row?.selection_as_of || selectionGate.as_of || payload?.selection_as_of || analysisDate,
            analysis_date: row?.analysis_date || analysisDate || String(selectionGate.as_of || "").slice(0, 10),
            ranking_status: row?.ranking_status || (gatePassed === false ? "excluded" : "eligible"),
            pool_rank: row?.pool_rank ?? row?.rank ?? (gatePassed === false ? null : index + 1),
        };
    }).filter((row) => row.ticker);
}

function changeCandidateStrategy(strategyId) {
    const select = document.getElementById("candidateStrategySelect");
    if (select && select.value !== strategyId) select.value = strategyId;
    selectedCandidateTicker = "";
    syncCandidateStrategyChrome(strategyId);
    return loadSelectionPool(true);
}

async function loadSelectionPool(forceRefresh = false) {
    const strategyId = selectedCandidateStrategyId();
    const queryDate = document.getElementById("globalDate")?.value || "";
    if (document.getElementById("selectionResearchDate")) document.getElementById("selectionResearchDate").value = queryDate;
    const queryScope = `${queryDate}|${document.getElementById('globalBenchmark')?.value || '000001.SH'}`;
    if (selectionQueryDate !== queryScope) {
        Object.keys(candidateRowsByStrategy).forEach(k => delete candidateRowsByStrategy[k]);
        Object.keys(candidateEvidenceByStrategy).forEach(k => delete candidateEvidenceByStrategy[k]);
        selectionQueryDate = queryScope;
    }
    syncCandidateStrategyChrome(strategyId);
    const hasCachedRows = Object.prototype.hasOwnProperty.call(candidateRowsByStrategy, strategyId);
    if (!forceRefresh && hasCachedRows) {
        renderCandidates(candidateRowsByStrategy[strategyId], strategyId);
        return;
    }
    const requestVersion = ++selectionPoolRequestVersion;
    const date = encodeURIComponent(document.getElementById("globalDate")?.value || "");
    const benchmark = encodeURIComponent(document.getElementById("globalBenchmark")?.value || "000001.SH");
    const strategyLabel = REVIEW_TACTICS[strategyId] || (isOversoldFirstBoardStrategy(strategyId) ? "超跌首板点火" : "MA5 趋势 Playbook");
    showHtml("candidatesResult", `<div class="candidate-terminal-empty is-loading" role="status"><strong>正在读取${strategyLabel}候选</strong><p>按当前分析日期读取已冻结的策略结果，请稍候。</p></div>`);
    try {
        const data = await fetchJson(`/api/v1/market/candidates?strategy_id=${encodeURIComponent(strategyId)}&date=${date}&benchmark_ticker=${benchmark}`, {timeoutMs: 45000});
        if (requestVersion !== selectionPoolRequestVersion || selectedCandidateStrategyId() !== strategyId) return;
        candidateEvidenceByStrategy[strategyId] = data;
        const rows = candidatePayloadRows(data, strategyId);
        renderCandidates(rows, strategyId);
    } catch (error) {
        if (requestVersion !== selectionPoolRequestVersion || selectedCandidateStrategyId() !== strategyId) return;
        const message = formatError(error, "candidates");
        const missingCache = message.includes('候选池数据未预计算');
        showHtml("candidatesResult", `<div class="candidate-terminal-empty is-error" role="alert"><strong>${strategyLabel}候选读取失败</strong><p>${escapeHtml(message)}</p>${missingCache ? '<button type="button" onclick="precomputeSelectionPool()">补齐数据并计算</button>' : '<button type="button" onclick="loadSelectionPool(true)">重新读取</button>'}</div>`);
    }
}

async function precomputeSelectionPool(force = false) {
    const version = ++selectionPoolRequestVersion;
    const strategy = selectedCandidateStrategyId();
    const date = document.getElementById('globalDate')?.value || '';
    const benchmark = document.getElementById('globalBenchmark')?.value || '000001.SH';
    const stillCurrent = () => version === selectionPoolRequestVersion && strategy === selectedCandidateStrategyId()
        && date === document.getElementById('globalDate')?.value && benchmark === (document.getElementById('globalBenchmark')?.value || '000001.SH')
        && !document.getElementById('tab-selection')?.hidden;
    showHtml('candidatesResult', '<div class="candidate-terminal-empty is-loading" role="status"><strong>正在提交后台计算</strong><p>任务完成后自动读取候选，关闭页面不会取消计算。</p></div>');
    try {
        const task = await submitSectorPrecomputeTask({date, benchmark, force, stillCurrent, onProgress: task => {
            showHtml('candidatesResult', `<div class="candidate-terminal-empty is-loading" role="status"><strong>${escapeHtml(sectorTaskStateLabel(task))}</strong><p>完成后自动显示候选。关闭页面不会取消任务，再次点击会继续查询同一任务。</p></div>`);
        }});
        if (task && stillCurrent()) await loadSelectionPool(true);
    } catch (error) {
        if (!stillCurrent()) return;
        const failed = error.taskStatus === 'failed';
        showHtml('candidatesResult', `<div class="candidate-terminal-empty is-error" role="alert"><strong>计算未完成</strong><p>${escapeHtml(formatError(error,'precompute'))}</p><button type="button" onclick="precomputeSelectionPool(${failed ? 'true' : ''})">${failed ? '重新发起计算' : '继续查询任务'}</button><button type="button" onclick="loadSelectionPool(true)">读取已有结果</button></div>`);
    }
}

function renderCandidatesLegacy(candidates) {
    const card = document.getElementById("candidatesCard");
    if (!card) return;
    latestCandidateRows = Array.isArray(candidates) ? candidates : [];
    syncSelectionPickedControls();
    if (!candidates || !candidates.length) {
        card.style.display = "";
        showHtml("candidatesResult", `
          <p class="hint">暂无 MA5 趋势候选。当前规则只保留单品趋势、位置和风险边界满足条件的股票。</p>
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

    // 后端已按有效排名排序；风险硬否决项必须留在末尾，不能再按原始 signal 重排。
    const rows = [...candidates].sort((a, b) => {
        const aExcluded = a.ranking_status === "excluded" ? 1 : 0;
        const bExcluded = b.ranking_status === "excluded" ? 1 : 0;
        if (aExcluded !== bExcluded) return aExcluded - bExcluded;
        return Number(a.pool_rank || 9999) - Number(b.pool_rank || 9999);
    });

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
        const candidateSourceLabels = {
            session_limit_up: "当日强启动",
            leader_top10: "龙头综合前10",
            zhongjun_top10: "中军综合前10",
        };
        const candidateSourceText = candidateSourceLabels[c.candidate_source] || c.role_label || "板块候选";
        const candidateRankTrace = [
            c.leader_candidate_rank ? `龙头综合第 ${c.leader_candidate_rank}` : "",
            c.zhongjun_candidate_rank ? `中军综合第 ${c.zhongjun_candidate_rank}` : "",
        ].filter(Boolean).join(" · ");
        return `
        <article class="candidate-decision-card tone-${tone} ${candidateViewMode === "expanded" ? "is-expanded" : "is-compact"} ${c.gate_downgraded ? 'row-downgraded' : ''} ${c.ranking_status === "excluded" ? "is-ranking-excluded" : ""}">
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
                <em>${c.ranking_status === "excluded" ? "不参与排名" : `有效 #${escapeHtml(c.pool_rank || "-")}`}</em>
              </strong>
            </button>
            <div class="candidate-signal-box">
              ${renderSignalCell(c)}
              ${c.ma20_slope_up ? '<span class="badge-zhusheng">主升</span>' : ""}
              ${riskTags(c.risk_flags)}
              <button type="button" class="candidate-holding-action" ${c.ranking_status === "excluded" ? "disabled title=\"风险硬否决，不能加入持仓\"" : `onclick="prefillHoldingFromCandidate('${escapeHtml(c.ticker)}')"`}>${c.ranking_status === "excluded" ? "风险否决" : "加入持仓"}</button>
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
            <p><b>排名</b><span>${escapeHtml(c.ranking_explanation || "按买点、大盘、板块和风险乘数计算")} · 数据截至 ${escapeHtml(c.analysis_date || "-")}</span></p>
            <p><b>入池</b><span>${escapeHtml(candidateSourceText)}${candidateRankTrace ? ` · ${escapeHtml(candidateRankTrace)}` : ""}；先进入战法评分，再由买点、大盘、板块和风险决定有效排名。</span></p>
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
    const excludedCount = rows.filter((item) => item.ranking_status === "excluded").length;
    const eligibleCount = rows.length - excludedCount;
    const banner = anyDowngraded ? `
        <p class="hint gate-active-hint">
          ⚠️ Market Gate 已启用：部分候选的原始信号被降级为"仅关注"。
          横线代表被降级的原始信号，→ 后面是 gate 后的最终信号。
        </p>` : "";
    showHtml("candidatesResult", `
        <div class="candidate-view-toolbar">
          <div>
            <strong>候选池扫描</strong>
            <span>数据截至 ${escapeHtml(rows[0]?.analysis_date || "-")} · 有效排名 ${eligibleCount} 只${excludedCount ? ` · 风险否决 ${excludedCount} 只` : ""}。</span>
          </div>
          <div class="candidate-view-actions">
            <button type="button" class="${candidateViewMode === "compact" ? "active" : ""}" onclick="setCandidateViewMode('compact')">紧凑模式</button>
            <button type="button" class="${candidateViewMode === "expanded" ? "active" : ""}" onclick="setCandidateViewMode('expanded')">展开模式</button>
          </div>
        </div>
        ${banner}
        ${excludedCount ? '<p class="hint candidate-veto-hint">跌停等硬风险项保留在列表底部用于解释，但不参与有效排名，也不能直接加入持仓。</p>' : ""}
        <div class="candidate-decision-list mode-${candidateViewMode}">${candidateCards}</div>
        <p class="hint">点击股票进入单票分析；勾选后加入盘中监控。买点、支撑、压力与退出位由 MA5 Playbook 的硬规则生成，板块和大盘仅校准排序、仓位与禁做条件。</p>
      `);
}

function selectCandidateDetail(ticker) {
    selectedCandidateTicker = normalizeTrendPoolTicker(ticker);
    renderCandidates(latestCandidateRows);
    requestAnimationFrame(() => {
        const drawer = document.getElementById("candidateDetailPanel");
        if (drawer) {
            drawer.scrollIntoView({behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: "start"});
        }
    });
}

function firstFiniteCandidateNumber(...values) {
    for (const value of values) {
        if (value === null || value === undefined || value === "") continue;
        const number = Number(value);
        if (Number.isFinite(number)) return number;
    }
    return null;
}

function candidateSelectionGate(c) {
    return c?.selection_gate || c?.feature_snapshot?.selection_gate || {};
}

function candidateIsExcluded(c) {
    return c?.ranking_status === "excluded" || candidateSelectionGate(c).passed === false;
}

function formatCandidateSignedPercent(value) {
    const number = firstFiniteCandidateNumber(value);
    if (number === null) return "-";
    return `${number > 0 ? "+" : ""}${(number * 100).toFixed(2)}%`;
}

function formatCandidateAmountYi(value) {
    const number = firstFiniteCandidateNumber(value);
    if (number === null || number < 0) return "-";
    return `${(number / 100_000_000).toFixed(2)}亿`;
}

function candidateSelectionAsOf(c) {
    const raw = c?.selection_as_of || candidateSelectionGate(c).as_of || c?.analysis_date;
    if (!raw) return "-";
    return String(raw).slice(0, 16).replace("T", " ");
}

function candidateOversoldMetrics(c) {
    const gate = candidateSelectionGate(c);
    const metrics = {...c, ...c?.selection_evidence, ...c?.metrics, ...gate.metrics};
    const amountFromSource = firstFiniteCandidateNumber(metrics.avg_amount_15d);
    const amountCny = firstFiniteCandidateNumber(
        metrics.avg_amount_15d_cny,
        amountFromSource === null ? null : amountFromSource * 1_000,
    );
    const criteria = Array.isArray(gate.criteria) ? gate.criteria : (c?.selection_criteria || []);
    const limitUpCriterion = criteria.find((item) => (
        String(item?.id || "").includes("limit_up") || String(item?.label || "").includes("涨停")
    ));
    const bonusRaw = metrics.history_limit_up_bonus ?? limitUpCriterion?.score_awarded;
    const bonusNumber = typeof bonusRaw === "boolean" ? null : firstFiniteCandidateNumber(bonusRaw);
    const limitUpCount = firstFiniteCandidateNumber(metrics.limit_up_count_60d, metrics.limit_up_count);
    const bonusText = bonusRaw === true
        ? "已加分"
        : bonusNumber !== null && bonusNumber > 0
            ? `+${bonusNumber.toFixed(Number.isInteger(bonusNumber) ? 0 : 1)}分`
            : limitUpCount !== null && limitUpCount > 0 ? "有记录" : "0分";
    return {
        gate,
        criteria,
        amountCny,
        return7d: firstFiniteCandidateNumber(metrics.return_7d, c?.return_7d),
        close: firstFiniteCandidateNumber(metrics.close, c?.close),
        ma20: firstFiniteCandidateNumber(metrics.ma20, c?.ma20),
        ma20Distance: firstFiniteCandidateNumber(metrics.close_to_ma20_ratio, c?.close_to_ma20_ratio),
        ma60: firstFiniteCandidateNumber(metrics.ma60, c?.ma60),
        ma60Overhead: firstFiniteCandidateNumber(metrics.ma60_overhead_ratio, c?.ma60_overhead_ratio),
        drawdown20d: firstFiniteCandidateNumber(metrics.drawdown_from_high_20d, c?.drawdown_from_high_20d),
        rsi14: firstFiniteCandidateNumber(metrics.rsi14, c?.rsi14, c?.rsi),
        limitUpCount,
        limitUpDates: metrics.limit_up_dates_60d || metrics.limit_up_dates || [],
        lastLimitUpDate: metrics.last_limit_up_date || "",
        bonusText,
        score: firstFiniteCandidateNumber(gate.score, c?.score, c?.final_trade_score),
        scoreMax: firstFiniteCandidateNumber(gate.score_max, c?.score_max),
    };
}

function candidateTerminalSignal(c) {
    if (isOversoldFirstBoardStrategy()) {
        const passed = !candidateIsExcluded(c);
        return {
            signal: passed ? "selected_t1" : "excluded",
            label: passed ? "T-1已入选" : "规则淘汰",
            tone: passed ? "confirm" : "watch",
        };
    }
    const signal = c.final_signal || c.signal || "watch";
    const label = c.final_signal_label || c.signal_label || "仅关注";
    const tone = signal === "breakout_confirm" ? "ready" : signal === "watch" || c.gate_downgraded ? "watch" : "confirm";
    return {signal, label, tone};
}

function candidateTerminalScore(c) {
    const oversoldScore = isOversoldFirstBoardStrategy() ? candidateOversoldMetrics(c).score : null;
    const value = Number(oversoldScore ?? c.final_trade_score ?? c.entry_quality ?? 0);
    return Number.isFinite(value) ? value : 0;
}

function candidateExclusionLabel(c) {
    const firstFailure = candidateSelectionGate(c)?.failure_reasons?.[0];
    if (firstFailure) return firstFailure;
    return c?.ranking_exclusion_label || "规则否决";
}

function toggleSelectedCandidateMonitoring(ticker) {
    const normalized = normalizeTrendPoolTicker(ticker);
    const candidate = latestCandidateRows.find((item) => normalizeTrendPoolTicker(item.ticker) === normalized) || {ticker: normalized};
    setTrendPoolCandidate({
        ticker: normalized,
        name: candidate.name || normalized,
        sector_name: candidate.sector_name || "",
        source: "selection_pool",
    }, !selectedTrendPool[normalized]);
    renderCandidates(latestCandidateRows);
}

function renderOversoldCandidateDetailPanel(c) {
    if (!c) {
        return `<aside id="candidateDetailPanel" class="candidate-terminal-detail is-empty"><strong>选择一只股票</strong><p>点击表格行核对 T-1 超跌证据与次日监控交接。</p></aside>`;
    }
    const metrics = candidateOversoldMetrics(c);
    const selected = Boolean(selectedTrendPool[normalizeTrendPoolTicker(c.ticker)]);
    const excluded = candidateIsExcluded(c);
    const criteriaMarkup = metrics.criteria.map((item) => {
        const isBonus = item?.hard === false;
        const stateClass = isBonus ? "is-bonus" : item?.passed ? "is-pass" : "is-fail";
        const marker = isBonus ? "+" : item?.passed ? "✓" : "×";
        const actual = item?.actual ?? item?.actual_value ?? "待核对";
        return `<span class="${stateClass}"><b>${marker}</b>${escapeHtml(item?.label || item?.id || "规则")} · ${escapeHtml(String(actual))}</span>`;
    }).join("");
    const limitUpDates = Array.isArray(metrics.limitUpDates) ? metrics.limitUpDates.filter(Boolean) : [];
    const scoreText = metrics.score === null
        ? "-"
        : metrics.scoreMax === null ? metrics.score.toFixed(1) : `${metrics.score.toFixed(1)}/${metrics.scoreMax.toFixed(1)}`;
    const roleLabel = c.role_label || "超跌首板候选";
    const statusNote = excluded
        ? candidateExclusionLabel(c)
        : "系统将在下一交易日开盘后接入行业、个股与资金门槛";
    return `<aside id="candidateDetailPanel" class="candidate-terminal-detail tone-${excluded ? "watch" : "confirm"} is-oversold" aria-label="${escapeHtml(c.name || c.ticker)} 超跌首板选品详情">
      <header class="candidate-detail-head">
        <div><span>T-1 SELECTION DETAIL</span><h3>${escapeHtml(c.name || c.ticker)}</h3><p>${escapeHtml(c.ticker)} · ${escapeHtml(c.sector_name || "行业待确认")} · ${escapeHtml(roleLabel)}</p></div>
        <b>${excluded ? "—" : `#${escapeHtml(c.pool_rank || "-")}`}</b>
      </header>
      <div class="candidate-detail-signal"><span>策略交接</span><strong>${excluded ? "T-1规则未通过" : "T-1已入选→明早自动监控"}</strong><small>选品时点 ${escapeHtml(candidateSelectionAsOf(c))} · ${escapeHtml(statusNote)}</small></div>
      <dl class="candidate-detail-plan is-oversold-evidence">
        <div><dt>A15</dt><dd>${escapeHtml(formatCandidateAmountYi(metrics.amountCny))}</dd><small>15日均额 &lt; 30亿</small></div>
        <div><dt>R7</dt><dd>${escapeHtml(formatCandidateSignedPercent(metrics.return7d))}</dd><small>7日累计 &lt; 15%</small></div>
        <div><dt>MA20 距离</dt><dd>${escapeHtml(formatCandidateSignedPercent(metrics.ma20Distance))}</dd><small>MA20 ${formatCandidatePrice(metrics.ma20)}</small></div>
        <div><dt>MA60 上方空间</dt><dd>${escapeHtml(formatCandidateSignedPercent(metrics.ma60Overhead))}</dd><small>MA60 ${formatCandidatePrice(metrics.ma60)}</small></div>
      </dl>
      <section class="candidate-detail-block is-evidence">
        <h4>超跌形态证据</h4>
        <p>20日回撤 ${escapeHtml(formatCandidateSignedPercent(metrics.drawdown20d))} · RSI14 ${metrics.rsi14 === null ? "-" : escapeHtml(metrics.rsi14.toFixed(1))} · 收盘 ${formatCandidatePrice(metrics.close)}</p>
        <p>均线距离只描述 T-1 位置，不在此处替代 T 日行业强度、放量资金与追价限制。</p>
      </section>
      <section class="candidate-detail-scores">
        <div><span>入池得分</span><strong>${escapeHtml(scoreText)}</strong></div>
        <div><span>历史涨停加分</span><strong>${escapeHtml(metrics.bonusText)}</strong></div>
        <div><span>60日涨停</span><strong>${metrics.limitUpCount === null ? "-" : `${escapeHtml(String(metrics.limitUpCount))}次`}</strong></div>
        <div><span>最近涨停</span><strong>${escapeHtml(metrics.lastLimitUpDate || limitUpDates.at(-1) || "-")}</strong></div>
      </section>
      ${criteriaMarkup ? `<section class="candidate-detail-block is-evidence"><h4>超跌首板入池规则</h4><div class="candidate-oversold-criteria">${criteriaMarkup}</div></section>` : ""}
      <section class="candidate-oversold-monitor-note" aria-label="次日监控说明"><strong>明早自动监控</strong><p>系统从候选池继续观察行业强度、个股相对强弱与暴量资金；手动勾选只用于固定到你的盘中自选栏。</p></section>
      <footer class="candidate-detail-actions is-oversold">
        <button type="button" data-trend-pool-button data-ticker="${escapeHtml(c.ticker)}" class="${selected ? "is-active" : ""}" aria-pressed="${selected ? "true" : "false"}" onclick="toggleSelectedCandidateMonitoring('${escapeHtml(c.ticker)}')">${selected ? "已加入自选监控" : "加入自选监控"}</button>
        <button type="button" class="secondary" onclick="jumpToAnalysis('${escapeHtml(c.ticker)}')">查看单票分析</button>
      </footer>
    </aside>`;
}

function renderCandidateDetailPanel(c) {
    if (isOversoldFirstBoardStrategy()) return renderOversoldCandidateDetailPanel(c);
    if (!c) {
        return `<aside id="candidateDetailPanel" class="candidate-terminal-detail is-empty"><strong>选择一只股票</strong><p>点击表格行查看交易计划、排名依据和风险边界。</p></aside>`;
    }
    if (candidateIsExcluded(c)) {
        const selected = Boolean(selectedTrendPool[normalizeTrendPoolTicker(c.ticker)]);
        const criteria = candidateSelectionGate(c).criteria || [];
        return `<aside id="candidateDetailPanel" class="candidate-terminal-detail" aria-label="${escapeHtml(c.name || c.ticker)} 未入选依据">
          <header class="candidate-detail-head"><div><h3>${escapeHtml(c.name || c.ticker)}</h3><p>${escapeHtml(c.ticker)} · ${escapeHtml(c.sector_name || '行业待确认')} · ${escapeHtml(c.analysis_date || '—')}</p></div></header>
          <section class="candidate-detail-block"><h4>未通过入池条件</h4><p>${escapeHtml(c.ranking_explanation || candidateExclusionLabel(c))}</p></section>
          <section class="candidate-detail-block is-evidence"><h4>逐项核对</h4><div class="candidate-detail-risks">${criteria.map(item => `<span>${item.passed ? '✓' : '×'} ${escapeHtml(item.label)} · ${escapeHtml(item.actual)}</span>`).join('')}</div></section>
          <p>收盘 ${formatCandidatePrice(c.close)} · MA5 ${formatCandidatePrice(c.ma5)} · 5日 ${colorPercent(c.return_5d)}</p>
          <footer class="candidate-detail-actions"><button type="button" data-trend-pool-button data-ticker="${escapeHtml(c.ticker)}" aria-pressed="${selected}" onclick="toggleSelectedCandidateMonitoring('${escapeHtml(c.ticker)}')">${selected ? '已加入盘中监控' : '加入盘中观察'}</button><button type="button" class="secondary" onclick="jumpToAnalysis('${escapeHtml(c.ticker)}')">查看单票分析</button></footer>
          <p>观察不代表通过入池条件。条件补齐前不生成执行计划。</p>
        </aside>`;
    }
    const plan = candidateDisplayPlan(c);
    const signal = candidateTerminalSignal(c);
    const rr = candidateRewardRisk(plan, c.stop_loss);
    const score = candidateTerminalScore(c);
    const selected = Boolean(selectedTrendPool[normalizeTrendPoolTicker(c.ticker)]);
    const scoreRows = [
        ["最终评分", score],
        ["股票质量", c.stock_quality_score],
        ["买点时机", c.trade_timing_score],
        ["旧入场质量", c.entry_quality],
    ];
    const riskFlags = (c.risk_flags || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("");
    const selectionCriteria = (c.selection_gate?.criteria || []).map((item) => (
        `<span>${item.passed ? "✓" : "×"} ${escapeHtml(item.label)} · ${escapeHtml(item.actual)}</span>`
    )).join("");
    const statusNote = c.ranking_status === "excluded"
        ? candidateExclusionLabel(c)
        : c.gate_downgraded ? "大盘闸门已降级" : "规则评分有效";
    return `<aside id="candidateDetailPanel" class="candidate-terminal-detail tone-${escapeHtml(signal.tone)}" aria-label="${escapeHtml(c.name || c.ticker)} 选品详情">
      <header class="candidate-detail-head">
        <div><span>SELECTION DETAIL</span><h3>${escapeHtml(c.name || c.ticker)}</h3><p>${escapeHtml(c.ticker)} · ${escapeHtml(c.sector_name || "行业待确认")} · ${escapeHtml(c.role_label || "候选")}</p></div>
        <b>${c.ranking_status === "excluded" ? escapeHtml(candidateExclusionLabel(c)) : `#${escapeHtml(c.pool_rank || "-")}`}</b>
      </header>
      <div class="candidate-detail-signal"><span>当前结论</span><strong>${escapeHtml(signal.label)}</strong><small>${escapeHtml(c.analysis_date || "-")} · ${escapeHtml(statusNote)}</small></div>
      <dl class="candidate-detail-plan">
        <div><dt>计划买点</dt><dd>${formatCandidatePrice(plan.buy)}</dd><small>${escapeHtml(plan.buyLabel || "计划区间")}</small></div>
        <div><dt>目标价</dt><dd>${formatCandidatePrice(plan.target)}</dd><small>${escapeHtml(plan.pressureSource || "压力目标")}</small></div>
        <div><dt>止损位</dt><dd>${escapeHtml(String(c.stop_loss ?? "-"))}</dd><small>退出边界</small></div>
        <div><dt>收益风险比</dt><dd>${escapeHtml(rr)}</dd><small>RR</small></div>
      </dl>
      <section class="candidate-detail-block">
        <h4>为什么排在这里</h4>
        <p>${escapeHtml(c.ranking_explanation || c.reason || "按买点、大盘、板块和风险乘数计算。")}</p>
        <small>${escapeHtml(c.reason || "暂无额外说明")}</small>
      </section>
      ${selectionCriteria ? `<section class="candidate-detail-block is-evidence"><h4>五日线战法入池硬条件</h4><div class="candidate-detail-risks">${selectionCriteria}</div></section>` : ""}
      <section class="candidate-detail-scores">
        ${scoreRows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${value == null ? "-" : escapeHtml(String(Number(value).toFixed(1)))}</strong></div>`).join("")}
      </section>
      <section class="candidate-detail-block is-evidence">
        <h4>价格与趋势证据</h4>
        <p>收盘 ${escapeHtml(String(c.close ?? "-"))} · MA5 ${escapeHtml(String(c.ma5 ?? "-"))} · MA8 ${formatCandidatePrice(plan.ma8)} · 5日 ${colorPercent(c.return_5d)}</p>
        <p>支撑 ${formatCandidatePrice(plan.support)} · 压力 ${formatCandidatePrice(plan.pressure)} · RSI ${Number.isFinite(Number(c.rsi)) ? Number(c.rsi).toFixed(1) : "-"}</p>
        ${riskFlags ? `<div class="candidate-detail-risks">${riskFlags}</div>` : ""}
      </section>
      <footer class="candidate-detail-actions">
        <button type="button" data-trend-pool-button data-ticker="${escapeHtml(c.ticker)}" class="${selected ? "is-active" : ""}" aria-pressed="${selected ? "true" : "false"}" onclick="toggleSelectedCandidateMonitoring('${escapeHtml(c.ticker)}')">${selected ? "已加入盘中监控" : "加入盘中监控"}</button>
        <button type="button" class="secondary" onclick="jumpToAnalysis('${escapeHtml(c.ticker)}')">查看单票分析</button>
        <button type="button" class="secondary" ${c.ranking_status === "excluded" ? "disabled" : `onclick="prefillHoldingFromCandidate('${escapeHtml(c.ticker)}')"`}>${c.ranking_status === "excluded" ? escapeHtml(candidateExclusionLabel(c)) : "加入持仓"}</button>
      </footer>
    </aside>`;
}

function renderCandidates(candidates, strategyId = selectedCandidateStrategyId()) {
    const card = document.getElementById("candidatesCard");
    if (!card) return;
    const normalizedStrategyId = String(strategyId || DEFAULT_CANDIDATE_STRATEGY_ID);
    const strategyRows = Array.isArray(candidates) ? candidates : [];
    candidateRowsByStrategy[normalizedStrategyId] = strategyRows;
    if (selectedCandidateStrategyId() !== normalizedStrategyId) return;
    latestCandidateRows = strategyRows;
    if (REVIEW_TACTICS[normalizedStrategyId]) {
        selectedCandidateTicker = '';
        showHtml('candidatesResult', window.SelectionResearch.research(candidateEvidenceByStrategy[normalizedStrategyId] || {strategy_id:normalizedStrategyId,candidates:strategyRows}));
        syncCandidateStrategyChrome(normalizedStrategyId);
        return;
    }
    syncCandidateStrategyChrome(normalizedStrategyId);
    syncSelectionPickedControls();
    const oversoldStrategy = isOversoldFirstBoardStrategy(normalizedStrategyId);
    const scopeHtml = window.SelectionResearch.scope(candidateEvidenceByStrategy[normalizedStrategyId] || {}, normalizedStrategyId);
    const rows = [...latestCandidateRows].sort((a, b) => {
        const excludedDelta = Number(candidateIsExcluded(a)) - Number(candidateIsExcluded(b));
        if (excludedDelta) return excludedDelta;
        return Number(a.pool_rank || 9999) - Number(b.pool_rank || 9999);
    });
    if (!rows.length) {
        selectedCandidateTicker = "";
        const emptyCopy = oversoldStrategy
            ? "当前日期没有通过 T-1 超跌首板规则的股票。请确认日线数据与策略结果已计算，再刷新选品池。"
            : "同步最新数据后刷新选品池；风险否决和数据缺失不会被包装成机会。";
        showHtml("candidatesResult", `${scopeHtml}<div class="candidate-terminal-empty"><strong>当前没有符合规则的股票</strong><p>${emptyCopy}</p><p>请求研究日 ${escapeHtml(document.getElementById('globalDate')?.value || '—')} · 信号归档 ${escapeHtml(candidateEvidenceByStrategy[normalizedStrategyId]?.selection_as_of || '暂无入选信号')}</p></div>`);
        return;
    }
    if (!rows.some((item) => normalizeTrendPoolTicker(item.ticker) === selectedCandidateTicker)) {
        selectedCandidateTicker = normalizeTrendPoolTicker(rows.find((item) => !candidateIsExcluded(item))?.ticker || '');
    }
    const selectedCandidate = rows.find((item) => normalizeTrendPoolTicker(item.ticker) === selectedCandidateTicker);
    const eligibleCount = rows.filter((item) => !candidateIsExcluded(item)).length;
    const excludedCount = rows.length - eligibleCount;
    const selectedCount = selectedTrendPoolTickers().length;
    const tableRows = rows.filter(c => !candidateIsExcluded(c)).map((c) => {
        const signal = candidateTerminalSignal(c);
        const isActive = normalizeTrendPoolTicker(c.ticker) === selectedCandidateTicker;
        const excluded = candidateIsExcluded(c);
        const commonStart = `<tr class="candidate-terminal-row ${isActive ? "is-active" : ""} ${excluded ? "is-excluded" : ""}" tabindex="0" aria-selected="${isActive ? "true" : "false"}" onclick="selectCandidateDetail('${escapeHtml(c.ticker)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();selectCandidateDetail('${escapeHtml(c.ticker)}')}">
          <td data-label="监控" class="candidate-terminal-check"><input type="checkbox" class="trend-pool-check" data-ticker="${escapeHtml(c.ticker)}" data-name="${escapeHtml(c.name || c.ticker)}" data-sector="${escapeHtml(c.sector_name || "")}" ${selectedTrendPool[normalizeTrendPoolTicker(c.ticker)] ? "checked" : ""} onclick="event.stopPropagation()" onchange="toggleTrendPoolCandidate(this)" aria-label="加入盘中监控 ${escapeHtml(c.ticker)}"></td>
          <td data-label="排名" class="candidate-terminal-rank">${excluded ? "—" : `#${escapeHtml(c.pool_rank || "-")}`}</td>
          <td data-label="股票"><button type="button" class="candidate-terminal-stock" onclick="event.stopPropagation();selectCandidateDetail('${escapeHtml(c.ticker)}')"><strong>${escapeHtml(c.name || c.ticker)}</strong><small>${escapeHtml(c.ticker)} · ${escapeHtml(c.sector_name || "-")}</small></button></td>`;
        if (oversoldStrategy) {
            const metrics = candidateOversoldMetrics(c);
            return `${commonStart}
              <td data-label="状态"><span class="candidate-terminal-signal tone-${escapeHtml(signal.tone)}">${escapeHtml(signal.label)}</span><small class="candidate-oversold-transition">${excluded ? "不进入次日扫描" : "明早自动监控"}</small></td>
              <td data-label="A15" class="candidate-terminal-number"><strong>${escapeHtml(formatCandidateAmountYi(metrics.amountCny))}</strong><small>&lt; 30亿</small></td>
              <td data-label="R7" class="candidate-terminal-number"><strong>${escapeHtml(formatCandidateSignedPercent(metrics.return7d))}</strong><small>&lt; 15%</small></td>
              <td data-label="MA20距离" class="candidate-terminal-number"><strong>${escapeHtml(formatCandidateSignedPercent(metrics.ma20Distance))}</strong><small>MA20 ${formatCandidatePrice(metrics.ma20)}</small></td>
              <td data-label="MA60空间" class="candidate-terminal-number"><strong>${escapeHtml(formatCandidateSignedPercent(metrics.ma60Overhead))}</strong><small>上方均线</small></td>
              <td data-label="20日回撤 / RSI" class="candidate-terminal-number is-risk"><strong>${escapeHtml(formatCandidateSignedPercent(metrics.drawdown20d))}</strong><small>RSI ${metrics.rsi14 === null ? "-" : escapeHtml(metrics.rsi14.toFixed(1))}</small></td>
              <td data-label="涨停加分" class="candidate-terminal-number"><strong>${escapeHtml(metrics.bonusText)}</strong><small>60日 ${metrics.limitUpCount === null ? "-" : `${escapeHtml(String(metrics.limitUpCount))}次`}</small></td>
            </tr>`;
        }
        const plan = candidateDisplayPlan(c);
        const score = candidateTerminalScore(c);
        const rr = candidateRewardRisk(plan, c.stop_loss);
        return `${commonStart}
          <td data-label="结论"><span class="candidate-terminal-signal tone-${escapeHtml(signal.tone)}">${escapeHtml(signal.label)}</span></td>
          <td data-label="评分" class="candidate-terminal-number"><strong>${score.toFixed(1)}</strong><small>${escapeHtml(c.buy_point_pattern_label || c.role_label || "规则分")}</small></td>
          <td data-label="收盘" class="candidate-terminal-number"><strong>${escapeHtml(String(c.close ?? "-"))}</strong><small>5日 ${colorPercent(c.return_5d)}</small></td>
          <td data-label="买点" class="candidate-terminal-number"><strong>${formatCandidatePrice(plan.buy)}</strong><small>${escapeHtml(plan.buyLabel || "-")}</small></td>
          <td data-label="目标" class="candidate-terminal-number"><strong>${formatCandidatePrice(plan.target)}</strong><small>${escapeHtml(plan.pressureSource || "-")}</small></td>
          <td data-label="止损" class="candidate-terminal-number is-risk"><strong>${escapeHtml(String(c.stop_loss ?? "-"))}</strong><small>退出位</small></td>
          <td data-label="RR" class="candidate-terminal-number"><strong>${escapeHtml(rr)}</strong><small>收益/风险</small></td>
        </tr>`;
    }).join("");
    const tableHeaders = oversoldStrategy
        ? "<th>监控</th><th>排名</th><th>股票</th><th>状态</th><th>A15</th><th>R7</th><th>MA20距离</th><th>MA60空间</th><th>20日回撤 / RSI</th><th>涨停加分</th>"
        : "<th>监控</th><th>排名</th><th>股票</th><th>结论</th><th>评分</th><th>收盘</th><th>买点</th><th>目标</th><th>止损</th><th>RR</th>";
    const dataAsOf = oversoldStrategy ? candidateSelectionAsOf(rows[0]) : rows[0]?.analysis_date || "-";
    showHtml("candidatesResult", `
      ${scopeHtml}
      <section class="candidate-terminal-summary" aria-label="选品池摘要">
        <div><span>全部候选</span><strong>${rows.length}</strong></div>
        <div><span>${oversoldStrategy ? "T-1入选" : "有效排名"}</span><strong>${eligibleCount}</strong></div>
        <div class="is-selected"><span>${oversoldStrategy ? "自选监控" : "盘中监控"}</span><strong>${selectedCount}</strong></div>
        <div><span>未入选</span><strong>${excludedCount}</strong></div>
        <p>${oversoldStrategy ? "选品时点" : "数据截至"} ${escapeHtml(dataAsOf)} · ${eligibleCount ? '点击任一行查看详细依据' : '未入选依据见下方'}</p>
      </section>
      <div class="candidate-terminal-layout">
        ${eligibleCount ? `<section class="candidate-terminal-table-shell" aria-label="选品池扫描表" tabindex="0">
          <table class="candidate-terminal-table ${oversoldStrategy ? "is-oversold" : ""}">
            <thead><tr>${tableHeaders}</tr></thead>
            <tbody>${tableRows}</tbody>
          </table>
        </section>` : '<div class="candidate-terminal-empty"><strong>该日没有通过全部入池条件的股票</strong><p>形态候选已计算。展开下方未入选名单，可核对均线、涨停、成交额和市值条件。</p></div>'}
        ${selectedCandidate ? renderCandidateDetailPanel(selectedCandidate) : ''}
      </div>
      ${window.SelectionResearch.excluded(rows.filter(candidateIsExcluded), selectedCandidateTicker)}
      <p class="candidate-terminal-footnote">${oversoldStrategy
          ? "T-1只负责入池；下一交易日由盘中策略继续验证行业强度、个股相对强弱、暴量资金和追价边界。"
          : "执行事件只扫描真实持仓与当前完整选品池；行业样本只用于板块共振，不进入个股时间线。"}</p>
    `);
}

function setOptionalNumberInput(id, value) {
    const input = document.getElementById(id);
    if (!input) return;
    const number = Number(value);
    input.value = Number.isFinite(number) && number > 0 ? number.toFixed(2) : "";
}

function prefillHoldingFromCandidate(ticker) {
    const normalized = normalizeTrendPoolTicker(ticker);
    const candidate = latestCandidateRows.find((item) => normalizeTrendPoolTicker(item.ticker) === normalized);
    if (!candidate) return;
    const plan = candidateDisplayPlan(candidate);
    document.getElementById("holdingTicker").value = normalized;
    setTradingStockPickerValue("holding", {
        ticker: normalized,
        name: candidate.name || normalized,
        industry: candidate.sector_name || "",
    });
    document.getElementById("holdingName").value = candidate.name || normalized;
    document.getElementById("holdingSector").value = candidate.sector_name || "";
    document.getElementById("holdingEntryDate").value = document.getElementById("globalDate").value;
    setOptionalNumberInput("holdingEntryPrice", plan.buy || candidate.close);
    document.getElementById("holdingEntrySize").value = "";
    document.getElementById("holdingEntrySignal").value = "ma5_candidate";
    const strategySelect = document.getElementById("holdingStrategyId");
    if (strategySelect) strategySelect.value = candidate.strategy_id || "ma5_v0_2";
    setOptionalNumberInput("holdingTargetPrice", plan.target);
    setOptionalNumberInput("holdingStopLoss1", candidate.stop_loss || (plan.support ? plan.support * 0.98 : null));
    setOptionalNumberInput("holdingStopLoss2", candidate.stop_loss_2 || candidate.ma10);
    setOptionalNumberInput("holdingBreakdownLevel", candidate.breakdown_level || candidate.ma20);
    setOptionalNumberInput("holdingSignalOpen", candidate.signal_candle_open || candidate.open);
    updateHoldingEntrySize();
    document.getElementById("holdingNote").value = [
        candidate.reason || "",
        candidate.role_label ? `候选角色：${candidate.role_label}` : "",
        candidate.gate_downgraded ? "大盘闸门已降级，录入前请确认是否真实成交。" : "",
    ].filter(Boolean).join("；");
    switchTab("holdings");
    const panel = document.getElementById("holdingCreatePanel");
    if (panel) {
        panel.open = true;
        panel.scrollIntoView({behavior: "smooth", block: "start"});
    }
    showHoldingNotice(`${candidate.name || normalized} 的交易计划已带入。请把计划买点改成实际成交价后确认。`, "info");
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

let sectorCapitalMetric = 'main_net_inflow';
function changeSectorCapitalMetric(metric) {
    sectorCapitalMetric = ['main_net_inflow','large_order_net','amount'].includes(metric) ? metric : 'main_net_inflow';
    const root = document.getElementById('sectorCapitalRoot');
    if (root && latestSectorData) {
        root.innerHTML = window.SelectionResearch.capital(latestSectorData, sectorCapitalMetric);
        document.getElementById('sectorCapitalMetric')?.focus();
    }
}

function renderSectorTrends(data) {
    const rawPayload = {
        summary: data.summary,
        top_sectors: data.top_sectors,
        trend_sectors: data.trend_sectors,
        state_machine: data.state_machine,
        indicator_definitions: data.indicator_definitions,
        data_quality: data.data_quality
    };
    return `
        <section class="sector-story-layout">
          <aside class="sector-storyline" aria-label="板块动向故事线">
            <div class="story-title">研究维度</div>
            <button class="story-link active" data-target="sector-capital" onclick="scrollSectorFeature('sector-capital')"><span>01</span><b class="story-link-label">当日资金</b></button>
            <button class="story-link" data-target="sector-today" onclick="scrollSectorFeature('sector-today')"><span>02</span><b class="story-link-label">中期趋势</b></button>
            <button class="story-link" data-target="sector-trends" onclick="scrollSectorFeature('sector-trends')"><span>03</span><b class="story-link-label">趋势跟踪</b></button>
            <button class="story-link" data-target="sector-knowledge" onclick="scrollSectorFeature('sector-knowledge')"><span>04</span><b class="story-link-label">口径边界</b></button>
          </aside>
          <div class="sector-feature-stack">
            <div id="sectorCapitalRoot">${window.SelectionResearch.capital(data, sectorCapitalMetric)}</div>
            ${renderTodayStrongSectors(data)}
            ${renderTrendSectors(data)}
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
            <div class="stock-decision-badge"><span>VC-Astock 结论</span><strong>${escapeHtml(translateDecision(decision.conclusion_label))}</strong><p>${escapeHtml(translateDecision(decision.watching_action))}</p></div>
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
    try {
        const response = await fetch(url, requestOptions);
        const text = await response.text();
        let payload;
        try {
            payload = JSON.parse(text);
        } catch {
            payload = {raw: text};
        }
        if (!response.ok) throw payload;
        return payload;
    } finally {
        if (timeoutId) clearTimeout(timeoutId);
    }
}

function isFetchConnectionError(error) {
    return error?.name === "TypeError" && /fetch|network|load failed/i.test(error.message || "");
}

async function fetchSectorJson(url, options = {}) {
    // Only retry reads. A lost POST response does not prove submission failed.
    const retries = ["GET", "HEAD"].includes((options.method || "GET").toUpperCase()) ? 2 : 0;
    for (let attempt = 0; ; attempt++) {
        try {
            return await fetchJson(url, options);
        } catch (error) {
            if (!isFetchConnectionError(error) || attempt >= retries) throw error;
            await new Promise(resolve => setTimeout(resolve, (attempt + 1) * 1000));
        }
    }
}

function sectorTaskStateLabel(task) {
    if (task.status === 'queued') return '等待服务器执行';
    if (task.status === 'retry') return '等待服务器自动重试';
    if (task.recovery_pending) return '等待服务器恢复任务';
    return '服务器正在计算';
}

async function submitSectorPrecomputeTask({date, benchmark, force = false, stillCurrent, onProgress}) {
    if (!stillCurrent()) return null;
    // Submit once; a lost response must never automatically replay this POST.
    let task = await fetchJson(
        `/api/v1/data/precompute-sectors/start?date=${encodeURIComponent(date)}&benchmark_ticker=${encodeURIComponent(benchmark)}${force ? '&force=true' : ''}`,
        {method: 'POST', timeoutMs: 30000}
    );
    while (['queued', 'running', 'retry'].includes(task.status)) {
        if (!stillCurrent()) return null;
        if (!task.job_id) throw new Error('后台任务缺少任务编号，请重新查询提交状态。');
        onProgress(task);
        await new Promise(resolve => setTimeout(resolve, 5000));
        // Leaving the page only stops polling; the server retains the task.
        if (!stillCurrent()) return null;
        try {
            task = await fetchSectorJson(`/api/v1/data/precompute-sectors/status/${encodeURIComponent(task.job_id)}`, {timeoutMs: 30000});
        } catch (error) {
            if (!stillCurrent()) return null;
            throw Object.assign(new Error('进度暂时读取失败，后台计算可能仍在运行；点击计算可继续查询同一任务。'), {taskStateUnknown: true});
        }
    }
    if (!stillCurrent()) return null;
    if (task.status !== 'complete') throw Object.assign(new Error(task.error || '后台计算未完成，已有归档保留。'), {taskStatus: task.status});
    return task;
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

let dataStatusRequestId = 0;

function syncDisplayTime(value) {
    if (!value) return "—";
    const text = String(value);
    if (/(Z|[+-]\d\d:\d\d)$/.test(text)) {
        const date = new Date(text);
        if (!Number.isNaN(date.getTime())) return new Intl.DateTimeFormat("sv-SE", {
            timeZone: "Asia/Shanghai", dateStyle: "short", timeStyle: "short"
        }).format(date);
    }
    return text.replace("T", " ").replace(/\.\d+$/, "");
}

function syncScheduleText(job) {
    const hour = String(job.hour ?? "");
    const minute = String(job.minute ?? "");
    if (/^\d+$/.test(hour) && /^\d+$/.test(minute)) {
        const days = job.day_of_week;
        const prefix = days === "*" ? "每天" : days === "mon-fri" ? "周一至周五" : days || "";
        return `${prefix} ${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`.trim();
    }
    return job.schedule || job.label || "时间待核验";
}

const syncOpenDetails = new Set();

function toggleSyncDetails(button) {
    const id = button.getAttribute("aria-controls");
    const row = document.getElementById(id);
    if (!row) return;
    const open = button.getAttribute("aria-expanded") !== "true";
    row.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    button.setAttribute("aria-label", `${open ? "收起" : "展开"}${button.dataset.label || "数据"}详情`);
    if (open) syncOpenDetails.add(id); else syncOpenDetails.delete(id);
}

function syncScheduleGroups(jobs) {
    const groups = new Map();
    for (const job of [...(jobs || [])].sort((a, b) => (Number(a.hour) * 60 + Number(a.minute)) - (Number(b.hour) * 60 + Number(b.minute)))) {
        if (/^\d+$/.test(String(job.hour)) && /^\d+$/.test(String(job.minute))) {
            const days = job.day_of_week === "*" ? "每日" : job.day_of_week === "mon-fri" ? "工作日" : job.day_of_week || "";
            if (!groups.has(days)) groups.set(days, []);
            groups.get(days).push(`${String(job.hour).padStart(2, "0")}:${String(job.minute).padStart(2, "0")}`);
        } else {
            const text = syncScheduleText(job);
            if (!groups.has(text)) groups.set(text, []);
        }
    }
    return [...groups].map(([days, times]) => `${days}${times.length ? " " + [...new Set(times)].join(" / ") : ""}`);
}

function renderDataStatusCard(d) {
    const state = d.status || (d.ok ? "ready" : "missing");
    const usage = d.usage || {};
    const optional = usage.status === "optional";
    const labels = {ready: "已有数据", scheduled: "待运行", running: "同步中", partial: "部分成功", failed: "执行失败", missing: optional ? "按需未生成" : "缺少数据", unknown: "待核验"};
    const tone = ["partial", "failed"].includes(state) || state === "missing" && !optional ? "attention" : "neutral";
    const storage = d.storage || {};
    const earliest = storage.earliest_date, latest = storage.latest_date;
    const range = earliest && latest ? `${earliest} 至 ${latest}` : d.coverage?.granularity === "当前快照" ? "当前快照" : storage.status === "empty" ? "尚无归档" : "日期范围待核验";
    const rangeHtml = earliest && latest ? `<span>${escapeHtml(earliest)}</span> 至 <span>${escapeHtml(latest)}</span>` : escapeHtml(range);
    const number = value => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN") : null;
    const count = number(storage.record_count), dates = number(storage.date_count);
    const unit = {minute_bars: "个证券日窗口", stock_basic: "只股票", index_daily_bars: "根日K", cffex_positions: "份日快照", topic_perspectives: "份日快照", daily_review: "份日快照"}[d.key] || "条记录";
    const stored = [count === null ? "总量未统计" : `${count} ${unit}`, dates !== null && dates !== count ? `${dates} 个归档日` : null].filter(Boolean).join(" · ");
    const schedule = d.schedule || {};
    const scheduleLabels = {enabled: "云端定时", configured: "已配置 · 待确认", stale: "曾启用 · 待确认", manual: "手动 / 按需", unknown: "更新状态待确认"};
    const groups = syncScheduleGroups(schedule.jobs);
    const next = schedule.status === "enabled" ? schedule.next_run_at || (schedule.jobs || []).map(job => job.next_run_at).filter(Boolean).sort()[0] : null;
    const run = d.last_run || {};
    const runLabels = {success: "成功", partial: "部分成功", running: "运行中", failed: "失败", degraded: "部分来源有缺口", skipped_closed: "休市跳过", empty_confirmed: "已核验空池", unknown: "暂无回执"};
    const runTitle = run.status === "failed" && run.stage && run.stage !== "daily_sync" ? "流水线后续失败" : run.status ? (runLabels[run.status] || "已有回执") : "暂无执行回执";
    const runTime = syncDisplayTime(run.updated_at || run.finished_at || run.started_at);
    const runCount = d.key === "cffex_positions" && run.successful_count != null ? `${run.successful_count}/${run.expected_count || 4} 个品种`
        : run.failed_count > 0 ? `失败 ${run.failed_count} 个目标` : null;
    const consumers = (usage.consumers || []).map(item => typeof item === "string" ? item : item.label || item.name || "").filter(Boolean);
    const usageTitle = usage.label || "接入情况待核验";
    const name = {stock_daily_bars: "个股日线", index_daily_bars: "基准指数日线", stock_basic: "股票基础信息", moneyflow: "个股资金流向", limit_up_pool: "涨停池", minute_bars: "5分钟行情", topic_perspectives: "统一分类目录", daily_review: "复盘归档", breakout_events: "MA5事件后续验证", cffex_positions: "股指期货机构席位"}[d.key] || d.label;
    const key = String(d.key || "unknown").replace(/[^a-zA-Z0-9_-]/g, "_");
    const id = `sync-detail-${key}`, open = syncOpenDetails.has(id);
    const coverage = [d.coverage?.scope, d.coverage?.granularity].filter(Boolean).join(" · ");
    const runDate = run.requested_date || run.target_date || run.scope;
    const stages = {daily_sync: "基础行情", strong_stocks: "核心资格", review: "题材与复盘", theme_cycles: "题材周期", complete: "全部阶段完成"};
    const runDetail = [runDate ? `目标 ${runDate}` : null, run.stage ? `流水线阶段：${stages[run.stage] || run.stage}` : null, run.detail,
        run.fetched_count != null ? `${d.key === "cffex_positions" ? "入库快照" : "本次获取"} ${run.fetched_count}` : null,
        run.failed_count != null ? `失败 ${run.failed_count}` : null].filter(Boolean).join(" · ");
    const extraLabels = {settled: "已完成追踪", tracking: "追踪中", latest_breakout_date: "最新事件日", can_calc_ma120: "可计算MA120", can_calc_ma250: "可计算MA250"};
    const extras = d.extras && Object.keys(d.extras).length ? `<dl class="sync-extra-values">${Object.entries(d.extras).map(([key, value]) => `<div><dt>${escapeHtml(extraLabels[key] || key)}</dt><dd>${escapeHtml(value == null ? "未知" : typeof value === "boolean" ? value ? "有" : "无" : typeof value === "object" ? JSON.stringify(value) : String(value))}</dd></div>`).join("")}</dl>` : "";
    return `<tr class="sync-ledger-row" data-key="${key}">
      <td data-column="数据"><div class="sync-dataset-name"><button type="button" class="sync-row-toggle" aria-expanded="${open}" aria-controls="${id}" aria-label="${open ? "收起" : "展开"}${escapeHtml(name)}详情" data-label="${escapeHtml(name)}" onclick="toggleSyncDetails(this)"><span aria-hidden="true">›</span></button><strong>${escapeHtml(name)}</strong></div><span class="sync-data-state sync-tone-${tone}">${labels[state] || "待核验"}</span></td>
      <td data-column="已存范围"><span class="sync-date-range" title="${escapeHtml(range)}">${rangeHtml}</span><small>${escapeHtml(stored)}</small></td>
      <td data-column="自动更新"><div class="sync-update-line"><span class="sync-update-state${schedule.status === "enabled" ? " is-online" : ""}">${scheduleLabels[schedule.status] || "更新状态待确认"}</span>${groups.map(group => `<small>${escapeHtml(group)}</small>`).join("")}</div></td>
      <td data-column="执行回执"><span>${escapeHtml(runTitle)}</span>${runTime !== "—" ? `<small>${escapeHtml(runTime)}</small>` : ""}${runCount ? `<small>${escapeHtml(runCount)}</small>` : ""}</td>
      <td data-column="使用位置" title="${escapeHtml(usageTitle)}"><span>${escapeHtml(consumers.join(" · ") || usageTitle)}</span>${consumers.length && usage.status !== "integrated" ? `<small>${escapeHtml(usageTitle)}</small>` : ""}</td>
    </tr>
    <tr id="${id}" class="sync-detail-row"${open ? "" : " hidden"}><td colspan="5"><div class="sync-detail-grid">
      <section><h4>数据与覆盖</h4><p>${escapeHtml(d.detail || "")}</p><p>${escapeHtml(coverage)}</p><p>来源：${escapeHtml(d.source || "待核验")}</p>${d.status_note ? `<p>${escapeHtml(d.status_note)}</p>` : ""}${extras}</section>
      <section><h4>自动更新与回执</h4><p>${escapeHtml(schedule.note || (schedule.status === "enabled" ? "运行中的云端任务已核验。" : "当前自动更新状态待确认。"))}</p>${schedule.verified_at ? `<p>任务确认：${escapeHtml(syncDisplayTime(schedule.verified_at))}</p>` : ""}${next ? `<p>下次运行：${escapeHtml(syncDisplayTime(next))}</p>` : ""}<p>${escapeHtml(runDetail || "暂无所选日任务回执。")}</p></section>
      <section><h4>功能接入</h4><p>${escapeHtml(usage.note || "本页暂未核验该项功能接入情况。")}</p>${!d.ok && state !== "scheduled" && d.impact_on_miss ? `<p class="sync-impact">当前影响：${escapeHtml(d.impact_on_miss)}</p>` : ""}</section>
    </div></td></tr>`;
}

function renderDataStatus(data) {
    const datasets = data.datasets || [];
    const overall = data.overall_status || "unknown";
    const labels = {ready: "所选日数据已归档", degraded: "部分数据待补齐", blocked: "核心数据缺失", scheduled: "等待计划任务", running: "正在同步"};
    document.getElementById("dataStatus").textContent = labels[overall] || "数据状态待核验";
    const ready = datasets.filter(d => d.status === "ready" || !d.status && d.ok).length;
    const partial = datasets.filter(d => d.status === "partial").length;
    const online = datasets.filter(d => d.schedule?.status === "enabled").length;
    const manual = datasets.filter(d => d.schedule?.status === "manual").length;
    const summary = document.getElementById("dataSyncSummary");
    if (summary) summary.innerHTML = `<span><strong>${ready}<small> / ${datasets.length}</small></strong> 所选日有数据</span><span><strong>${partial}</strong> 部分成功</span><span><strong>${online}</strong> 云端自动更新</span><span><strong>${manual}</strong> 手动或按需</span>`;
    const pipeline = data.pipeline || {};
    const stages = {daily_sync: "基础行情", strong_stocks: "核心资格", review: "题材与复盘", theme_cycles: "题材周期", complete: "已完成"};
    const statuses = {running: "运行中", success: "成功", degraded: "部分来源有缺口", failed: "失败"};
    document.getElementById("dataPipelineStatus").textContent = pipeline.status
        ? `日线流水线 · ${pipeline.requested_date || pipeline.analysis_date || "—"} · ${statuses[pipeline.status] || pipeline.status}${pipeline.stage ? `（${stages[pipeline.stage] || pipeline.stage}）` : ""} · ${syncDisplayTime(pipeline.updated_at)}`
        : "日线流水线暂无执行回执。";
    const scheduler = data.scheduler || {};
    const schedulerNode = document.getElementById("dataScheduleStatus");
    if (schedulerNode) schedulerNode.textContent = [scheduler.status === "enabled" ? "自动更新服务在线" : "自动更新服务待确认",
        scheduler.verified_at ? `任务心跳 ${syncDisplayTime(scheduler.verified_at)}` : null,
        scheduler.checked_at ? `本次核对 ${syncDisplayTime(scheduler.checked_at)}` : null].filter(Boolean).join(" · ");
    const ordered = [...datasets].sort((a, b) => Number(a.key === "breakout_events") - Number(b.key === "breakout_events"));
    document.getElementById("dataStatusGrid").innerHTML = datasets.length ? `<table class="sync-ledger"><caption>所选交易日 ${escapeHtml(data.target_date || document.getElementById("globalDate").value)} · 箭头展开来源与核验详情</caption><colgroup><col class="sync-col-name"><col class="sync-col-storage"><col class="sync-col-schedule"><col class="sync-col-run"><col class="sync-col-usage"></colgroup><thead><tr><th scope="col">数据</th><th scope="col">已存范围</th><th scope="col">自动更新</th><th scope="col">执行回执</th><th scope="col">使用位置</th></tr></thead><tbody>${ordered.map(renderDataStatusCard).join("")}</tbody></table>`
        : '<p class="sync-empty">暂无数据目录，刷新后重试。</p>';
}

async function loadDataStatus() {
    const requestId = ++dataStatusRequestId;
    const date = encodeURIComponent(document.getElementById("globalDate").value);
    const ticker = encodeURIComponent(selectedBenchmarkTicker());
    const grid = document.getElementById("dataStatusGrid");
    const refresh = document.getElementById("dataStatusRefresh");
    grid.setAttribute("aria-busy", "true");
    if (refresh) refresh.disabled = true;
    document.getElementById("dataStatus").textContent = `正在核对 ${decodeURIComponent(date)} 的数据与自动更新状态…`;
    try {
        const data = await fetchJson(`/api/v1/data/status?date=${date}&ticker=${ticker}`);
        if (requestId !== dataStatusRequestId) return;
        runtimeStatus.data = data;
        renderDataStatus(data);
    } catch (error) {
        if (requestId !== dataStatusRequestId) return;
        document.getElementById("dataStatus").textContent = `状态读取失败：${formatError(error)}。可点击刷新重试。`;
        grid.innerHTML = "";
        const summaryNode = document.getElementById("dataSyncSummary");
        if (summaryNode) summaryNode.textContent = "本次读取失败，请重试。";
        document.getElementById("dataPipelineStatus").textContent = "";
        const scheduleNode = document.getElementById("dataScheduleStatus");
        if (scheduleNode) scheduleNode.textContent = "";
    } finally {
        if (requestId === dataStatusRequestId) {
            grid.setAttribute("aria-busy", "false");
            if (refresh) refresh.disabled = false;
        }
    }
}

async function syncIndexHistoryYear() {
    const target = "tushareSyncResult";
    show(target, "正在同步 7 个基准指数 1 年历史（不跑全市场个股）... 约 5-10 秒。");
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

async function syncDataDomain(domain, button) {
    const day = document.getElementById("globalDate").value;
    const target = domain === "topics" ? "reviewSyncResult" : `${domain}SyncResult`;
    const urls = {minutes: "/api/v1/data/sync-minutes", limits: "/api/v1/data/sync-limit-pool",
        topics: "/api/v1/data/sync-topics", futures: `/api/v1/review/futures/refresh?date=${encodeURIComponent(day)}`, review: `/api/v1/review/refresh?date=${encodeURIComponent(day)}`};
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    show(target, `正在处理 ${day}，完成后会刷新状态。`);
    try {
        const data = await fetchJson(urls[domain], {method: "POST", headers: {"Content-Type": "application/json"},
            body: ["review", "futures"].includes(domain) ? undefined : JSON.stringify({target_date: day})});
        show(target, domain === "review" ? {date: data.analysis_date, generated_at: data.generated_at, quality: data.data_quality?.label} : data);
        await loadDataStatus();
    } catch (error) {
        show(target, `处理未完成：${formatError(error)}。已完成的数据保留，可刷新状态后重试。`);
    } finally {
        button.disabled = false;
        button.removeAttribute("aria-busy");
    }
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
    startMarketProgress(force);
    const action = force ? "强制重算" : "正在确认";
    show("marketPermissionConfirmResult", `${action}权限...`);
    try {
        const url = `/api/v1/market/permission?date=${date}&benchmark_ticker=${benchmark}${force ? "&force=true" : ""}`;
        const data = await fetchJson(url, {
            timeoutMs: force ? MARKET_FORCE_TIMEOUT_MS : MARKET_READ_TIMEOUT_MS,
        });
        latestMarketData = data;
        syncBenchmarkSelects(data.benchmark_ticker);
        syncMarketDates(data.requested_analysis_date || data.analysis_date);
        renderPermissionSummary(data);

        finishMarketProgress(true);
    } catch (error) {
        document.getElementById("marketPermissionSummary").innerHTML = "";
        const gap = marketDataGapMessage(error);
        show("marketPermissionConfirmResult", `${gap.title}：${gap.message}`);
        finishMarketProgress(false);
    }
}

async function runSectorTrends() {
    const dateValue = document.getElementById("globalDate").value;
    const benchmarkValue = document.getElementById("globalBenchmark").value;
    const date = encodeURIComponent(dateValue);
    const benchmark = encodeURIComponent(benchmarkValue);
    const requestVersion = ++sectorRequestVersion;
    startSectorProgress(requestVersion);
    showHtml("sectorTrendResult", '<div class="interpretation-card"><h3>正在读取</h3><p class="section-conclusion">正在读取 PART2 板块动向归档、龙头/中军候选和分歧/修复状态...</p></div>');
    try {
        const data = await fetchSectorJson(`/api/v1/market/sectors?date=${date}&benchmark_ticker=${benchmark}`, {timeoutMs: 45000});
        if (requestVersion !== sectorRequestVersion) return;
        selectedSectorIndex = 0;
        latestSectorData = data;
        syncBenchmarkSelects(data.benchmark_ticker);
        syncMarketDates(data.analysis_date);
        showHtml("sectorTrendResult", renderSectorTrends(latestSectorData));
        renderCandidates(data.candidates || [], DEFAULT_CANDIDATE_STRATEGY_ID);
        completeSectorProgress(requestVersion);
    } catch (error) {
        if (requestVersion !== sectorRequestVersion) return;
        const msg = String(error?.detail || error?.message || error || "");
        // 未预计算 → 显示引导卡（含一键预计算按钮）
        if (msg.includes("未预计算")) {
            failSectorProgress(requestVersion, "empty");
            showHtml("sectorTrendResult", renderPrecomputePrompt(dateValue, benchmarkValue, msg));
        } else {
            failSectorProgress(requestVersion, isFetchConnectionError(error) ? "connection" : "error");
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
          <p class="precompute-hint">60 日趋势窗口由服务器独立计算，可能需要十余分钟；已有同版本结果直接复用。关闭页面不会取消任务，重复点击会继续查询进度。</p>
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

function runPrecomputeSectorsFromPage(force = false) {
    const date = document.getElementById("globalDate").value;
    const benchmark = document.getElementById("globalBenchmark").value;
    return runPrecomputeSectors(date, benchmark, document.getElementById("sectorPrecomputeButton"), force);
}

async function runPrecomputeSectors(date, benchmark, sourceButton = null, force = false) {
    const requestVersion = ++sectorRequestVersion;
    const stillCurrent = () => requestVersion === sectorRequestVersion
        && date === document.getElementById('globalDate')?.value
        && benchmark === (document.getElementById('globalBenchmark')?.value || '000001.SH')
        && !document.getElementById('tab-sectors')?.hidden;
    const progressEl = document.getElementById("precomputeProgress");
    const elapsedEl = document.getElementById("precomputeElapsed");
    const btn = sourceButton;
    if (btn) btn.disabled = true;
    if (progressEl) progressEl.style.display = "";
    startSectorPrecomputeProgress(requestVersion);
    showHtml("sectorTrendResult", `<div class="interpretation-card"><h3>正在计算板块与候选分时</h3><p class="section-conclusion">先生成龙头/中军候选，再自动补齐最近交易日分钟 K；拉到新数据会在本次点击内重算并写入缓存。</p></div>`);
    const startAt = Date.now();
    if (precomputeTimer) clearInterval(precomputeTimer);
    const timer = setInterval(() => {
        if (elapsedEl) elapsedEl.textContent = Math.floor((Date.now() - startAt) / 1000);
    }, 500);
    precomputeTimer = timer;
    try {
        const task = await submitSectorPrecomputeTask({date, benchmark, force, stillCurrent, onProgress: task => {
            showHtml('sectorTrendResult', `<div class="interpretation-card" role="status"><h3>${escapeHtml(sectorTaskStateLabel(task))}</h3><p class="section-conclusion">${escapeHtml(date)} · 已等待 ${Math.floor((Date.now()-startAt)/1000)} 秒。关闭页面不会取消任务；重复点击会继续查询进度。</p></div>`);
        }});
        if (!task || !stillCurrent()) return;
        finishSectorPrecomputeProgress(requestVersion, true);
        // 预计算成功 → 立即重跑 runSectorTrends 拉缓存
        await runSectorTrends();
    } catch (error) {
        if (!stillCurrent()) return;
        const stateUnknown = error.taskStateUnknown || isFetchConnectionError(error) || error.name === "AbortError"
            || String(error.detail || "").includes("提交状态");
        finishSectorPrecomputeProgress(requestVersion, false, stateUnknown ? "connection" : "error");
        if (progressEl) progressEl.style.display = "none";
        if (requestVersion === sectorRequestVersion) showHtml("sectorTrendResult", `<div class="interpretation-card"><h3>板块任务状态</h3><p class="section-conclusion">${escapeHtml(formatError(error, "precompute"))}</p>${error.taskStatus === 'failed' ? '<button type="button" onclick="runPrecomputeSectorsFromPage(true)">重新发起计算</button>' : ''}</div>`);
    } finally {
        clearInterval(timer);
        if (precomputeTimer === timer) precomputeTimer = null;
        if (btn) btn.disabled = false;
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

// ── Trading Decision Engine 驾驶舱 ───────────────────────────────
const tradingDataStateMeta = {
    normal: {label: "数据正常", tone: "normal"},
    delayed: {label: "数据延迟", tone: "delayed"},
    missing: {label: "数据缺失", tone: "missing"},
    closed: {label: "休市", tone: "closed"},
};

const tradingScenarioStateMeta = {
    triggered: {label: "规则命中", tone: "triggered"},
    waiting_confirmation: {label: "等待确认", tone: "waiting"},
    approaching: {label: "正在接近", tone: "approaching"},
    far: {label: "尚未接近", tone: "far"},
    invalidated: {label: "已失效", tone: "invalid"},
    suppressed: {label: "被高优先级覆盖", tone: "suppressed"},
};

const tradingScenarioOptions = {
    buy: [
        ["E1", "E1 · 低开承接恢复"], ["E2", "E2 · 均线回踩收复"],
        ["E3", "E3 · 区间支撑反转"], ["E4", "E4 · 箱体/前高突破"],
        ["E5", "E5 · 弱转强"], ["E6", "E6 · 高开强突破"],
        ["E7", "E7 · 超跌反弹隔夜"],
    ],
    reduce: [
        ["N1", "N1 · 高开冲高回落"], ["N2", "N2 · 低开承接失败"],
        ["H3", "H3 · 区间压力兑现"], ["R1", "R1 · 软破位减仓"],
        ["R2", "R2 · 高开失败保护"], ["R3", "R3 · 目标到达"],
    ],
    exit: [["R4", "R4 · 硬止损清仓"], ["E7", "E7 · 次日强制退出"]],
};

function tradingStockPicker(key) {
    return tradingStockPickers[key] || tradingStockPickers.order;
}

function tradingStockInputId(key) {
    return key === "holding" ? "holdingTicker" : "tradingOrderTicker";
}

function tradingStockResultsId(key) {
    return `tradingStockResults-${key}`;
}

function tradingStockStatusId(key) {
    return `tradingStockStatus-${key}`;
}

function tradingHighlightedText(value, query) {
    const text = String(value || "");
    const needle = String(query || "").trim().toLocaleLowerCase("zh-CN");
    const index = text.toLocaleLowerCase("zh-CN").indexOf(needle);
    if (!needle || index < 0) return escapeHtml(text);
    return `${escapeHtml(text.slice(0, index))}<mark>${escapeHtml(text.slice(index, index + needle.length))}</mark>${escapeHtml(text.slice(index + needle.length))}`;
}

function renderTradingStockCombobox(key, {value = "", label = "股票代码"} = {}) {
    const picker = tradingStockPicker(key);
    const inputValue = picker.query || picker.selected?.ticker || value || "";
    const resultsId = tradingStockResultsId(key);
    const statusId = tradingStockStatusId(key);
    return `<div class="trading-stock-field" data-picker="${escapeHtmlAttr(key)}" data-state="${escapeHtmlAttr(picker.status)}">
      <label for="${escapeHtmlAttr(tradingStockInputId(key))}">${escapeHtml(label)}</label>
      <div class="trading-stock-combobox">
        <input id="${escapeHtmlAttr(tradingStockInputId(key))}" name="ticker" type="search" value="${escapeHtmlAttr(inputValue)}" placeholder="代码或名称" autocomplete="off" spellcheck="false" required role="combobox" aria-autocomplete="list" aria-controls="${escapeHtmlAttr(resultsId)}" aria-describedby="${escapeHtmlAttr(statusId)}" aria-expanded="false" oninput="scheduleTradingStockSearch('${escapeHtmlAttr(key)}', this.value)" onkeydown="handleTradingStockSearchKeydown(event, '${escapeHtmlAttr(key)}')" />
        <span class="trading-stock-field-mark" aria-hidden="true"></span>
        <div id="${escapeHtmlAttr(resultsId)}" class="trading-stock-results" role="listbox" hidden></div>
      </div>
      <p id="${escapeHtmlAttr(statusId)}" class="trading-stock-status" aria-live="polite">输入代码或名称，选择匹配股票。</p>
    </div>`;
}

function renderTradingStockSearchResults(key) {
    const picker = tradingStockPicker(key);
    const input = document.getElementById(tradingStockInputId(key));
    const root = document.getElementById(tradingStockResultsId(key));
    const status = document.getElementById(tradingStockStatusId(key));
    const field = input?.closest(".trading-stock-field");
    if (!input || !root || !status) return;
    field.dataset.state = picker.status;
    input.setAttribute("aria-busy", picker.status === "loading" ? "true" : "false");
    if (picker.status === "error") {
        input.setAttribute("aria-invalid", "true");
        root.hidden = false;
        root.innerHTML = `<p class="trading-stock-result-message">${escapeHtml(picker.error || "股票主数据读取失败，请稍后重试。")}</p>`;
        status.textContent = picker.error || "股票主数据读取失败，请稍后重试。";
        input.setAttribute("aria-expanded", "true");
        return;
    }
    input.removeAttribute("aria-invalid");
    if (picker.status === "loading") {
        root.hidden = false;
        root.innerHTML = `<p class="trading-stock-result-message">正在匹配股票…</p>`;
        status.textContent = "正在匹配股票…";
        input.setAttribute("aria-expanded", "true");
        return;
    }
    if (picker.status !== "open") {
        root.hidden = true;
        root.innerHTML = "";
        input.setAttribute("aria-expanded", "false");
        input.removeAttribute("aria-activedescendant");
        status.textContent = picker.selected
            ? `已选择 ${picker.selected.name || picker.selected.ticker} ${picker.selected.ticker}`
            : "输入代码或名称，选择匹配股票。";
        return;
    }
    root.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (!picker.items.length) {
        root.innerHTML = `<p class="trading-stock-result-message"><strong>没有匹配股票</strong><span>请检查代码或名称后重试。</span></p>`;
        status.textContent = "没有匹配股票，请检查代码或名称。";
        return;
    }
    root.innerHTML = picker.items.map((item, index) => {
        const active = index === picker.activeIndex;
        const optionId = `${tradingStockResultsId(key)}-${index}`;
        return `<button id="${escapeHtmlAttr(optionId)}" type="button" role="option" aria-selected="${active ? "true" : "false"}" class="trading-stock-result ${active ? "is-active" : ""}" onmousedown="event.preventDefault()" onclick="selectTradingStockResult('${escapeHtmlAttr(key)}', ${index})">
          <span><strong>${tradingHighlightedText(item.name || item.ticker, picker.query)}</strong><small>${tradingHighlightedText(item.ticker, picker.query)}</small></span>
          <em>${escapeHtml(item.industry || item.market || "板块待补")}</em>
        </button>`;
    }).join("");
    if (picker.activeIndex >= 0) input.setAttribute("aria-activedescendant", `${tradingStockResultsId(key)}-${picker.activeIndex}`);
    else input.removeAttribute("aria-activedescendant");
    status.textContent = `找到 ${picker.items.length} 只股票。`;
}

function scheduleTradingStockSearch(key, value) {
    const picker = tradingStockPicker(key);
    picker.query = String(value || "").trim();
    if (picker.selected && normalizeTrendPoolTicker(picker.query) !== picker.selected.ticker) picker.selected = null;
    picker.items = [];
    picker.activeIndex = -1;
    picker.error = "";
    if (tradingStockSearchTimers[key]) clearTimeout(tradingStockSearchTimers[key]);
    if (!picker.query) {
        picker.status = "idle";
        renderTradingStockSearchResults(key);
        return;
    }
    picker.status = "loading";
    renderTradingStockSearchResults(key);
    tradingStockSearchTimers[key] = setTimeout(() => runTradingStockSearch(key, picker.query), 250);
}

async function runTradingStockSearch(key, query) {
    const picker = tradingStockPicker(key);
    const version = ++tradingStockSearchVersions[key];
    try {
        const data = await fetchJson(`/api/v1/stocks/search?q=${encodeURIComponent(query)}&limit=10`, {timeoutMs: 8000});
        if (version !== tradingStockSearchVersions[key] || picker.query !== query) return;
        picker.items = data.items || [];
        picker.activeIndex = picker.items.length ? 0 : -1;
        picker.status = "open";
        renderTradingStockSearchResults(key);
    } catch (error) {
        if (version !== tradingStockSearchVersions[key]) return;
        picker.items = [];
        picker.status = "error";
        picker.error = formatError(error);
        renderTradingStockSearchResults(key);
    }
}

function selectTradingStockResult(key, index) {
    const picker = tradingStockPicker(key);
    const item = picker.items[index];
    if (!item) return;
    picker.selected = {...item, ticker: normalizeTrendPoolTicker(item.ticker)};
    picker.query = picker.selected.ticker;
    picker.items = [];
    picker.activeIndex = -1;
    picker.status = "success";
    const input = document.getElementById(tradingStockInputId(key));
    if (input) input.value = picker.selected.ticker;
    if (key === "holding") {
        const name = document.getElementById("holdingName");
        const sector = document.getElementById("holdingSector");
        if (name) name.value = picker.selected.name || picker.selected.ticker;
        if (sector) sector.value = picker.selected.industry || "";
        previewNewHoldingStopPlan({silent: true});
    }
    renderTradingStockSearchResults(key);
}

function handleTradingStockSearchKeydown(event, key) {
    const picker = tradingStockPicker(key);
    if (event.key === "Escape") {
        picker.status = picker.selected ? "success" : "idle";
        renderTradingStockSearchResults(key);
        return;
    }
    if (!picker.items.length || picker.status !== "open") return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const direction = event.key === "ArrowDown" ? 1 : -1;
        picker.activeIndex = (picker.activeIndex + direction + picker.items.length) % picker.items.length;
        renderTradingStockSearchResults(key);
    } else if (event.key === "Enter" && picker.activeIndex >= 0) {
        event.preventDefault();
        selectTradingStockResult(key, picker.activeIndex);
    }
}

function setTradingStockPickerValue(key, item = null) {
    const picker = tradingStockPicker(key);
    picker.selected = item?.ticker ? {...item, ticker: normalizeTrendPoolTicker(item.ticker)} : null;
    picker.query = picker.selected?.ticker || "";
    picker.items = [];
    picker.activeIndex = -1;
    picker.error = "";
    picker.status = picker.selected ? "success" : "idle";
}

const tradingInlineEditor = {type: "", ticker: "", field: ""};
const holdingInlineEditor = {type: "", ticker: "", field: "", plan: null, currentStop: null};

function closeTradingInlineEditor() {
    tradingInlineEditor.type = "";
    tradingInlineEditor.ticker = "";
    tradingInlineEditor.field = "";
    if (tradingState.cockpit) {
        const content = document.getElementById("tradingCockpitContent");
        if (content) content.innerHTML = renderTradingCockpit(tradingState.cockpit, tradingState.timeline || []);
    }
    renderTradingRiskProfile(tradingState.riskProfile);
}

function openTradingInlineEditor(type, field = "") {
    tradingInlineEditor.type = type;
    tradingInlineEditor.ticker = tradingState.cockpit?.holding?.ticker || "";
    tradingInlineEditor.field = field;
    if (type === "risk_profile") {
        renderTradingRiskProfile(tradingState.riskProfile);
        const focusIds = {assets: "tradingInlineAssets", cash: "tradingInlineCash", max_loss: "tradingInlineMaxLoss"};
        requestAnimationFrame(() => document.getElementById(focusIds[field] || "tradingInlineAssets")?.focus());
        return;
    }
    if (tradingState.cockpit) {
        const content = document.getElementById("tradingCockpitContent");
        if (content) content.innerHTML = renderTradingCockpit(tradingState.cockpit, tradingState.timeline || []);
    }
}

function tradingNumber(value, digits = 2) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits) : "-";
}

function tradingPercentPoint(value, digits = 2) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "-";
    return `${number > 0 ? "+" : ""}${(number * 100).toFixed(digits)}%`;
}

function tradingRatioPercent(value, digits = 1) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "-";
    return `${number > 0 ? "+" : ""}${(number * 100).toFixed(digits)}%`;
}

function tradingQuantity(value, {signed = false} = {}) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "待配置";
    const sign = signed && number > 0 ? "+" : "";
    return `${sign}${Math.trunc(number)} 股`;
}

function tradingDateTime(value) {
    if (!value) return "-";
    const text = String(value);
    return text.includes("T") ? `${text.slice(5, 10)} ${text.slice(11, 19)}` : text;
}

function tradingActionTone(action, freshness) {
    if (!action) return freshness?.state === "normal" ? "neutral" : "blocked";
    if (action.action_type === "risk_exit") return "exit";
    if (["risk_reduce", "prepare_reduce"].includes(action.action_type)) return "reduce";
    if (action.action_type === "wait_confirmation") return "wait";
    return "hold";
}

function tradingActionTitle(decision) {
    if (decision?.action?.label) return decision.action.label;
    if (decision?.data_freshness?.state === "delayed") return "暂停生成新动作";
    if (decision?.data_freshness?.state === "missing") return "等待必要数据恢复";
    return "当前没有可执行动作";
}

function renderTradingHoldingRail(items) {
    const list = document.getElementById("tradingHoldingList");
    const count = document.getElementById("tradingHoldingCount");
    if (count) count.textContent = String(items.length);
    if (!list) return;
    if (!items.length) {
        list.innerHTML = `<div class="trading-rail-empty"><strong>暂无活动持仓</strong><p>先在持仓台账录入实际成交。</p><button type="button" class="secondary" onclick="switchTab('holdings')">打开持仓台账</button></div>`;
        return;
    }
    list.innerHTML = items.map((item) => {
        const freshness = item.data_freshness || {};
        const stateMeta = tradingDataStateMeta[freshness.state] || {label: freshness.state || "未知", tone: "missing"};
        const active = item.ticker === tradingState.selectedTicker;
        const action = item.action;
        const scenario = item.current_scenario;
        return `
          <button type="button" class="trading-holding-item ${active ? "is-active" : ""}" onclick="selectTradingHolding('${escapeHtml(item.ticker)}')">
            <span class="trading-holding-identity">
              ${stockLogoMarkup(item.ticker, item.name, "trading-rail-logo")}
              <span><strong>${escapeHtml(item.name || item.ticker)}</strong><small>${escapeHtml(item.ticker)}</small></span>
            </span>
            <span class="trading-holding-decision">${escapeHtml(action?.label || "暂停新动作")}</span>
            <span class="trading-holding-meta"><small>${escapeHtml(scenario?.title || "等待可靠数据")}</small><b class="tone-${stateMeta.tone}">${escapeHtml(stateMeta.label)}</b></span>
          </button>`;
    }).join("");
}

function renderTradingRiskProfile(profilePayload) {
    const root = document.getElementById("tradingRiskProfile");
    if (!root) return;
    const profile = profilePayload?.profile || profilePayload || {};
    const configured = profilePayload?.configured ?? profile.source === "user_configured";
    if (tradingInlineEditor.type === "risk_profile") {
        root.innerHTML = `
          <form class="trading-inline-editor is-risk-profile" onsubmit="saveTradingRiskProfileInline(event)">
            <div class="trading-risk-profile-head"><span>ACCOUNT RISK · 页内编辑</span><b>${configured ? "已配置" : "默认值"}</b></div>
            <label><span>账户总资产（元）</span><input id="tradingInlineAssets" type="number" min="1" step="1" value="${escapeHtml(profile.total_assets || 86000)}" ${tradingInlineEditor.field === "assets" ? "autofocus" : ""} required></label>
            <label><span>可用现金（元）</span><input id="tradingInlineCash" type="number" min="0" step="1" value="${escapeHtml(profile.available_cash ?? 78000)}" ${tradingInlineEditor.field === "cash" ? "autofocus" : ""} required></label>
            <label><span>单笔最大亏损（元）</span><input id="tradingInlineMaxLoss" type="number" min="1" step="1" value="${escapeHtml(profile.max_loss_per_trade || 6880)}" ${tradingInlineEditor.field === "max_loss" ? "autofocus" : ""} required></label>
            <p id="tradingRiskInlineError" class="trading-inline-error" aria-live="polite">直接修改，保存后原位更新。</p>
            <div class="trading-inline-actions"><button type="submit">保存账户约束</button><button type="button" class="secondary" onclick="closeTradingInlineEditor()">取消</button></div>
          </form>`;
        return;
    }
    root.innerHTML = `
      <div class="trading-risk-profile-head"><span>ACCOUNT RISK</span><b>${configured ? "已配置" : "默认值"}</b></div>
      <button type="button" class="trading-risk-inline-value" onclick="editTradingRiskProfile('assets')"><span>账户资产</span><strong>¥${tradingNumber(profile.total_assets, 0)}</strong></button>
      <button type="button" class="trading-risk-inline-value" onclick="editTradingRiskProfile('cash')"><span>可用现金</span><strong>${profile.available_cash == null ? "待配置" : `¥${tradingNumber(profile.available_cash, 0)}`}</strong></button>
      <button type="button" class="trading-risk-inline-value" onclick="editTradingRiskProfile('max_loss')"><span>单笔最大亏损</span><strong>¥${tradingNumber(profile.max_loss_per_trade, 0)}</strong></button>
      <small>${profile.detailed_costs_configured ? "使用已配置费用明细" : `费用缓冲 ${tradingNumber(Number(profile.conservative_fee_buffer_rate || 0) * 100, 2)}%`}</small>
      <small>点击任一数值直接修改。</small>`;
}

const tradingOrderStatusMeta = {
    open: {label: "待成交", tone: "open"},
    partially_filled: {label: "部分成交", tone: "partial"},
    filled: {label: "全部成交", tone: "filled"},
    cancelled: {label: "已撤单", tone: "cancelled"},
};

function tradingOrderIsActive(order) {
    return ["open", "partially_filled"].includes(order?.status);
}

function tradingFactId(prefix) {
    const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `${prefix}-${random}`;
}

function resetTradingFactRequest(form) {
    if (!form) return;
    if (form.id === "tradingOrderForm") {
        tradingState.orderRequestId = "";
        return;
    }
    const orderId = form.dataset?.orderId;
    if (orderId) delete tradingState.fillRequestIds[orderId];
}

function setTradingOrderPanelState(tone, message = "") {
    tradingState.orderPanelTone = tone;
    tradingState.orderPanelMessage = message;
    renderTradingOrderPanel();
}

function tradingOrderIntentLabel(order) {
    if (order.side === "buy") return order.intent === "add" ? "加仓" : "建仓";
    return order.intent === "exit" ? "清仓" : "减仓";
}

function tradingOrderDefaultIntent(ticker) {
    const normalized = normalizeTrendPoolTicker(ticker);
    const holdings = [
        ...(tradingState.dashboard?.holdings || []),
        ...(holdingsState.items || []),
    ];
    return holdings.some((item) => item.ticker === normalized && item.status !== "closed" && Number(item.quantity || 0) > 0)
        ? "add"
        : "entry";
}

function tradingOrderStatusLabel(order) {
    return (tradingOrderStatusMeta[order.status] || {label: order.status || "未知"}).label;
}

function renderTradingScenarioOptions(side = "buy", intent = "entry") {
    const items = side === "buy" ? tradingScenarioOptions.buy : (tradingScenarioOptions[intent] || tradingScenarioOptions.reduce);
    return items.map(([value, label]) => `<option value="${escapeHtmlAttr(value)}">${escapeHtml(label)}</option>`).join("");
}

function renderTradingOrderFillForm(order) {
    if (tradingState.fillOrderId !== order.order_id || !tradingOrderIsActive(order)) return "";
    return `
      <form class="trading-order-fill-form" data-order-id="${escapeHtmlAttr(order.order_id)}" onsubmit="submitTradingOrderFill(event, '${escapeHtmlAttr(order.order_id)}')" oninput="resetTradingFactRequest(this)">
        <div class="trading-order-fill-heading">
          <div><strong>记录本次真实成交</strong><p>只回写券商侧已成交事实；未成交部分继续保留。</p></div>
          <button type="button" class="trading-order-icon-button" onclick="closeTradingOrderFill()" aria-label="关闭成交录入">×</button>
        </div>
        <div class="trading-order-fill-fields">
          <label><span>成交股数</span><input name="quantity" type="number" min="1" max="${escapeHtmlAttr(order.remaining_quantity)}" step="1" value="${escapeHtmlAttr(order.remaining_quantity)}" required></label>
          <label><span>成交均价</span><input name="price" type="number" min="0.01" step="0.01" value="${escapeHtmlAttr(order.limit_price)}" required></label>
          <label><span>费用</span><input name="fees" type="number" min="0" step="0.01" value="0" required></label>
          <button type="submit">确认成交</button>
        </div>
      </form>`;
}

function renderTradingOrderItem(order) {
    const meta = tradingOrderStatusMeta[order.status] || {label: order.status || "未知", tone: "cancelled"};
    const active = tradingOrderIsActive(order);
    const sideLabel = order.side === "buy" ? "买入" : "卖出";
    const filled = Number(order.filled_quantity || 0);
    const total = Number(order.quantity || 0);
    const progress = total > 0 ? Math.min(100, Math.max(0, filled / total * 100)) : 0;
    const fills = (tradingState.executions || [])
        .filter((item) => item.order_id === order.order_id)
        .sort((a, b) => String(b.executed_at || "").localeCompare(String(a.executed_at || "")));
    const identity = order.name && order.name !== order.ticker
        ? `${order.name} · ${order.ticker}`
        : order.ticker;
    return `
      <article class="trading-order-item tone-${meta.tone}">
        <div class="trading-order-item-head">
          <div class="trading-order-identity"><span class="trading-order-side side-${escapeHtml(order.side)}">${sideLabel}</span><strong>${escapeHtml(identity)}</strong><small>${escapeHtml(tradingOrderIntentLabel(order))}</small></div>
          <span class="trading-order-status">${escapeHtml(meta.label)}</span>
        </div>
        <div class="trading-order-progress" aria-label="成交进度 ${progress.toFixed(0)}%"><i style="width:${progress.toFixed(2)}%"></i></div>
        <div class="trading-order-metrics">
          <div><span>委托 / 已成</span><strong>${Math.trunc(total)} / ${Math.trunc(filled)}</strong></div>
          <div><span>剩余</span><strong>${Math.trunc(Number(order.remaining_quantity || 0))} 股</strong></div>
          <div><span>限价</span><strong>¥${tradingNumber(order.limit_price)}</strong></div>
          <div><span>均价</span><strong>${order.average_fill_price == null ? "-" : `¥${tradingNumber(order.average_fill_price)}`}</strong></div>
        </div>
        <div class="trading-order-item-foot">
          <small>${escapeHtml(order.scenario_id || "未标注场景")} · ${escapeHtml(tradingDateTime(order.placed_at))} · ${escapeHtml(String(order.order_id || "").slice(0, 12))}</small>
          ${active ? `<div><button type="button" onclick="openTradingOrderFill('${escapeHtmlAttr(order.order_id)}')">记录成交</button><button type="button" class="secondary" onclick="cancelTradingOrder('${escapeHtmlAttr(order.order_id)}')">撤单</button></div>` : ""}
        </div>
        ${fills.length ? `<div class="trading-fill-ledger" aria-label="逐笔成交明细">${fills.map((fill) => `<div><time>${escapeHtml(tradingDateTime(fill.executed_at))}</time><strong>${fill.side === "sell" ? "卖出" : "买入"} ${tradingQuantity(fill.quantity)}</strong><span>¥${tradingNumber(fill.price)} · 费用 ¥${tradingNumber(fill.fees)}</span><small>成交后 ${tradingQuantity(fill.quantity_after)} · 成本 ¥${tradingNumber(fill.cost_basis_after)}</small></div>`).join("")}</div>` : ""}
        ${renderTradingOrderFillForm(order)}
      </article>`;
}

function renderStandaloneExecutionItem(fill) {
    const sideLabel = fill.side === "sell" ? "卖出" : "买入";
    const identity = fill.name && fill.name !== fill.ticker ? `${fill.name} · ${fill.ticker}` : fill.ticker;
    return `<article class="trading-order-item trading-execution-only tone-filled">
      <div class="trading-order-item-head"><div class="trading-order-identity"><span class="trading-order-side side-${escapeHtml(fill.side || "buy")}">${sideLabel}</span><strong>${escapeHtml(identity || "未知股票")}</strong><small>真实成交</small></div><span class="trading-order-status">已成交</span></div>
      <div class="trading-order-metrics"><div><span>成交股数</span><strong>${tradingQuantity(fill.quantity)}</strong></div><div><span>成交价</span><strong>¥${tradingNumber(fill.price)}</strong></div><div><span>成交后持仓</span><strong>${tradingQuantity(fill.quantity_after)}</strong></div><div><span>成交后成本</span><strong>¥${tradingNumber(fill.cost_basis_after ?? fill.cost_basis)}</strong></div></div>
      <div class="trading-order-item-foot"><small>${escapeHtml(tradingDateTime(fill.executed_at))} · ${escapeHtml(String(fill.execution_id || "").slice(0, 12))}</small><span>费用 ¥${tradingNumber(fill.fees)}</span></div>
    </article>`;
}

function renderTradingOrderPanel() {
    const root = document.getElementById("tradingOrderPanel");
    if (!root) return;
    const profile = tradingState.riskProfile?.profile || {};
    const orders = tradingState.orders || [];
    const activeOrders = orders.filter(tradingOrderIsActive);
    const knownOrderIds = new Set(orders.map((order) => order.order_id).filter(Boolean));
    const standaloneExecutions = (tradingState.executions || []).filter((fill) => !fill.order_id || !knownOrderIds.has(fill.order_id));
    const reservedCash = activeOrders
        .filter((order) => order.side === "buy")
        .reduce((total, order) => total + Number(order.reserved_cash || 0), 0);
    const tone = tradingState.orderPanelTone || "default";
    const defaultBuyIntent = tradingOrderDefaultIntent(tradingState.selectedTicker);
    root.className = `trading-order-panel ${tone === "default" ? "" : `is-${tone}`}`.trim();
    root.setAttribute("aria-busy", tone === "loading" ? "true" : "false");
    root.innerHTML = `
      <details class="trading-order-disclosure" open>
        <summary class="trading-order-panel-head">
          <div><span>TRADE LEDGER · SOURCE OF TRUTH</span><h2 id="tradingOrderPanelTitle">交易流水与自动持仓</h2><p>成交是事实源；持仓、成本、仓位与收益由逐笔成交自动派生。</p></div>
          <div class="trading-order-account" aria-label="账户现金概览">
            <span>可用现金</span><strong>${profile.available_cash == null ? "待配置" : `¥${tradingNumber(profile.available_cash, 0)}`}</strong><small>${reservedCash > 0 ? `委托预留 ¥${tradingNumber(reservedCash)}` : "当前无买单预留"}</small>
          </div>
        </summary>
        <div class="trading-order-body">
        <form id="tradingOrderForm" class="trading-order-form" onsubmit="submitTradingOrder(event)" oninput="resetTradingFactRequest(this)">
          <div class="trading-order-form-title"><strong>记录券商委托</strong><span>未成交不改变持仓</span></div>
          <div class="trading-order-form-fields">
            ${renderTradingStockCombobox("order", {value: tradingState.selectedTicker || "", label: "股票代码或名称"})}
            <label><span>方向</span><select name="side" onchange="syncTradingOrderIntent(this.form)"><option value="buy">买入</option><option value="sell">卖出</option></select></label>
            <label><span>意图</span><select name="intent" onchange="syncTradingOrderIntent(this.form)"><option value="entry" ${defaultBuyIntent === "entry" ? "selected" : ""}>建仓</option><option value="add" ${defaultBuyIntent === "add" ? "selected" : ""}>加仓</option></select></label>
            <label><span>执行场景</span><select name="scenario_id" required>${renderTradingScenarioOptions()}</select></label>
            <label><span>委托股数</span><input name="quantity" type="number" min="1" step="1" placeholder="100" required></label>
            <label><span>委托限价</span><input name="limit_price" type="number" min="0.01" step="0.01" placeholder="0.00" required></label>
            <button id="tradingOrderSubmit" type="submit" ${tone === "loading" ? "disabled" : ""}>记录委托</button>
          </div>
        </form>
        <section class="trading-order-ledger" aria-label="交易与委托流水">
          <div class="trading-order-ledger-head"><div><strong>交易与委托流水</strong><span>${activeOrders.length} 笔进行中 · ${tradingState.executions.length} 笔成交</span></div><div><button type="button" class="secondary" onclick="TradeJournal.openForTicker(tradingState.selectedTicker || '')">交易复盘账本</button><button type="button" class="secondary" onclick="refreshTradingOrders()" ${tone === "loading" ? "disabled" : ""}>刷新</button></div></div>
          <div class="trading-order-list">
            ${orders.length || standaloneExecutions.length ? `${orders.slice(0, 12).map(renderTradingOrderItem).join("")}${standaloneExecutions.slice(0, 20).map(renderStandaloneExecutionItem).join("")}` : `<div class="trading-order-empty"><strong>暂无交易流水</strong><p>录入买入委托后只冻结现金；确认真实成交后才创建持仓。</p></div>`}
          </div>
        </section>
        </div>
      </details>
      <div class="trading-order-feedback" role="status" aria-live="polite">
        ${tone === "loading" ? `<span class="trading-order-loading-mark" aria-hidden="true"></span><strong>正在核对账户与委托事实…</strong>` : ""}
        ${tone === "error" ? `<strong>处理失败</strong><span>${escapeHtml(tradingState.orderPanelMessage)}</span>` : ""}
        ${tone === "success" ? `<strong>已完成</strong><span>${escapeHtml(tradingState.orderPanelMessage)}</span>` : ""}
      </div>`;
}

function syncTradingOrderIntent(form) {
    if (!form) return;
    const side = form.elements.side?.value || "buy";
    const intent = form.elements.intent;
    if (!intent) return;
    const requestedIntent = intent.value;
    intent.innerHTML = side === "buy"
        ? `<option value="entry">建仓</option><option value="add">加仓</option>`
        : `<option value="reduce">减仓</option><option value="exit">清仓</option>`;
    if (side === "buy") {
        intent.value = ["entry", "add"].includes(requestedIntent)
            ? requestedIntent
            : tradingOrderDefaultIntent(form.elements.ticker?.value);
    }
    if (side === "sell" && requestedIntent === "exit") intent.value = "exit";
    const scenario = form.elements.scenario_id;
    if (scenario) scenario.innerHTML = renderTradingScenarioOptions(side, intent.value);
    if (side === "sell" && tradingState.selectedTicker) {
        form.elements.ticker.value = tradingState.selectedTicker;
        setTradingStockPickerValue("order", {ticker: tradingState.selectedTicker, name: tradingState.selectedTicker});
    }
    resetTradingFactRequest(form);
}

async function refreshTradingOrders(options = {}) {
    if (!options.silent) setTradingOrderPanelState("loading");
    try {
        const [ordersPayload, executionsPayload, riskProfile] = await Promise.all([
            fetchJson("/api/v1/trading/orders?limit=50", {timeoutMs: 15000}),
            fetchJson("/api/v1/trading/executions?limit=200", {timeoutMs: 15000}),
            fetchJson("/api/v1/trading/risk-profile", {timeoutMs: 15000}),
        ]);
        tradingState.orders = ordersPayload.orders || [];
        tradingState.executions = executionsPayload.executions || [];
        tradingState.riskProfile = riskProfile;
        if (!options.preserveTone) {
            tradingState.orderPanelTone = "default";
            tradingState.orderPanelMessage = "";
        }
        renderTradingRiskProfile(riskProfile);
        renderTradingOrderPanel();
    } catch (error) {
        setTradingOrderPanelState("error", formatError(error));
    }
}

async function submitTradingOrder(event) {
    event?.preventDefault();
    const form = event?.currentTarget;
    if (!form) return;
    const ticker = normalizeTrendPoolTicker(form.elements.ticker?.value);
    const side = form.elements.side?.value;
    const intent = form.elements.intent?.value;
    const scenarioId = form.elements.scenario_id?.value;
    const quantity = Number(form.elements.quantity?.value);
    const limitPrice = Number(form.elements.limit_price?.value);
    if (!ticker || !["buy", "sell"].includes(side) || !Number.isInteger(quantity) || quantity <= 0 || !Number.isFinite(limitPrice) || limitPrice <= 0) {
        setTradingOrderPanelState("error", "请填写有效股票代码、正整数股数和大于 0 的委托价格。");
        return;
    }
    const orderId = tradingState.orderRequestId || tradingFactId("manual-order");
    tradingState.orderRequestId = orderId;
    const holding = (tradingState.dashboard?.holdings || []).find((item) => item.ticker === ticker) || {};
    const selectedStock = tradingStockPicker("order").selected || {};
    setTradingOrderPanelState("loading");
    try {
        await fetchJson("/api/v1/trading/orders", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                order_id: orderId,
                plan_id: `manual:${orderId}`,
                ticker,
                side,
                intent,
                quantity,
                limit_price: limitPrice,
                scenario_id: scenarioId || null,
                name: holding.name || selectedStock.name || null,
                sector_name: holding.sector_name || selectedStock.industry || null,
                note: "recorded_from_trading_order_panel",
            }),
            timeoutMs: 25000,
        });
        tradingState.fillOrderId = "";
        tradingState.orderRequestId = "";
        setTradingStockPickerValue("order");
        await loadTradingWorkspace({silent: true});
        setTradingOrderPanelState("default");
    } catch (error) {
        setTradingOrderPanelState("error", formatError(error));
    }
}

function openTradingOrderFill(orderId) {
    tradingState.fillOrderId = orderId;
    tradingState.orderPanelTone = "default";
    tradingState.orderPanelMessage = "";
    renderTradingOrderPanel();
}

function closeTradingOrderFill() {
    if (tradingState.fillOrderId) delete tradingState.fillRequestIds[tradingState.fillOrderId];
    tradingState.fillOrderId = "";
    renderTradingOrderPanel();
}

async function submitTradingOrderFill(event, orderId) {
    event?.preventDefault();
    const form = event?.currentTarget;
    const quantity = Number(form?.elements.quantity?.value);
    const price = Number(form?.elements.price?.value);
    const fees = Number(form?.elements.fees?.value || 0);
    if (!Number.isInteger(quantity) || quantity <= 0 || !Number.isFinite(price) || price <= 0 || !Number.isFinite(fees) || fees < 0) {
        setTradingOrderPanelState("error", "成交股数必须是正整数，成交价必须大于 0，费用不能为负。");
        return;
    }
    const executionId = tradingState.fillRequestIds[orderId] || tradingFactId("manual-fill");
    tradingState.fillRequestIds[orderId] = executionId;
    setTradingOrderPanelState("loading");
    try {
        const result = await fetchJson(`/api/v1/trading/orders/${encodeURIComponent(orderId)}/fills`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({quantity, price, fees, execution_id: executionId, note: "recorded_from_trading_order_panel"}),
            timeoutMs: 25000,
        });
        tradingState.fillOrderId = result.order?.status === "partially_filled" ? orderId : "";
        delete tradingState.fillRequestIds[orderId];
        await loadTradingWorkspace({silent: true});
        if (activeTabId() === "holdings") await loadHoldings({preserveNotice: true});
        if (result.holding_enrichment?.status === "deferred") {
            setTradingOrderPanelState("error", `成交已记录，但行情与策略价位生成失败：${result.holding_enrichment.reason || "请在持仓台账点击刷新后重试。"}`);
        } else {
            setTradingOrderPanelState("default");
        }
    } catch (error) {
        setTradingOrderPanelState("error", formatError(error));
    }
}

async function cancelTradingOrder(orderId) {
    setTradingOrderPanelState("loading");
    try {
        const result = await fetchJson(`/api/v1/trading/orders/${encodeURIComponent(orderId)}/cancel`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({reason: "cancelled_from_trading_order_panel"}),
            timeoutMs: 25000,
        });
        tradingState.fillOrderId = "";
        delete tradingState.fillRequestIds[orderId];
        await loadTradingWorkspace({silent: true});
        if (activeTabId() === "holdings") await loadHoldings({preserveNotice: true});
        setTradingOrderPanelState("default");
    } catch (error) {
        setTradingOrderPanelState("error", formatError(error));
    }
}

function executionWorkbenchMoney(value) {
    const number = Number(value);
    return Number.isFinite(number) ? `¥${number.toLocaleString("zh-CN", {maximumFractionDigits: 0})}` : "待配置";
}

function executionWorkbenchSourceLabel(source) {
    return {decision: "决策", candidate: "候选", order: "委托", audit: "核验"}[source] || "执行";
}

function renderExecutionEvent(event, {primary = false} = {}) {
    if (!event) return "";
    const presentation = event.presentation || {};
    const scenario = event.scenario || {};
    const market = event.market || {};
    const facts = [];
    if (presentation.target_quantity != null) facts.push(["目标", tradingQuantity(presentation.target_quantity)]);
    if (presentation.change_quantity != null) facts.push(["变动", tradingQuantity(presentation.change_quantity, {signed: true})]);
    if (market.current_price != null) facts.push(["现价", `¥${tradingNumber(market.current_price)}`]);
    if (presentation.data_as_of) facts.push(["数据", tradingDateTime(presentation.data_as_of)]);
    const ticker = String(event.ticker || "");
    const holdingExists = (tradingState.dashboard?.holdings || []).some((item) => item.ticker === ticker);
    const displayName = event.name && String(event.name).toUpperCase() !== ticker.toUpperCase()
        ? event.name
        : "名称待同步";
    const body = `
      <div class="execution-event-title">
        <strong>${escapeHtml(presentation.headline || event.action_name_cn || "执行事实待确认")}</strong>
        <b>${escapeHtml(event.priority || "P5")}</b>
      </div>
      <p class="execution-event-meta">${escapeHtml(ticker ? `${displayName} · ${ticker}` : "账户级事实")} · ${escapeHtml(scenario.scenario_name_cn || "执行事实")} · ${escapeHtml(tradingDateTime(event.occurred_at))}</p>
      <dl class="execution-event-copy">
        <div><dt>为什么</dt><dd>${escapeHtml(presentation.reason || "等待可审计原因")}</dd></div>
        <div><dt>怎么做</dt><dd>${escapeHtml(presentation.instruction || "继续观察")}</dd></div>
        <div><dt>还差什么</dt><dd>${escapeHtml(presentation.next_condition || "等待下一条执行事实")}</dd></div>
      </dl>
      ${facts.length ? `<dl class="execution-event-facts">${facts.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>` : ""}`;
    const classes = `execution-event-card priority-${escapeHtml(event.priority || "P5")} ${primary ? "is-primary" : ""}`;
    return `<article class="${classes}">${body}${ticker ? `<button type="button" class="execution-event-action" data-ticker="${escapeHtml(ticker)}" data-holding="${holdingExists ? "true" : "false"}" onclick="openExecutionEventDetail(this.dataset.ticker, this.dataset.holding === 'true')">打开详情</button>` : ""}</article>`;
}

async function openExecutionEventDetail(ticker, holdingExists = false) {
    const normalized = normalizeTrendPoolTicker(ticker);
    if (!normalized) return;
    if (holdingExists) {
        switchTab("trading");
        await selectTradingHolding(normalized);
        document.getElementById("tradingCockpitContent")?.scrollIntoView({behavior: "smooth", block: "start"});
        return;
    }
    switchTab("selection");
    await loadSelectionPool(false);
    if ((latestCandidateRows || []).some((item) => normalizeTrendPoolTicker(item.ticker) === normalized)) {
        selectCandidateDetail(normalized);
        document.querySelector(".selection-detail-card")?.scrollIntoView({behavior: "smooth", block: "start"});
        return;
    }
    jumpToAnalysis(normalized);
}

function renderExecutionQueue({className, title, description, items, emptyTitle, emptyCopy, primary = false}) {
    const rows = (items || []).filter(Boolean);
    return `<section class="execution-queue ${className}">
      <header class="execution-queue-head"><div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(description)}</p></div><span class="execution-queue-count">${rows.length}</span></header>
      <div class="execution-queue-body">
        ${rows.length ? rows.map((item, index) => renderExecutionEvent(item, {primary: primary && index === 0})).join("") : `<div class="execution-queue-empty"><strong>${escapeHtml(emptyTitle)}</strong><p>${escapeHtml(emptyCopy)}</p></div>`}
      </div>
    </section>`;
}

function renderExecutionTimeline(events) {
    const rows = (events || []).slice(0, 6);
    if (!rows.length) return `<div class="execution-queue-empty"><strong>尚无执行变化</strong><p>相同快照不会重复写事件；委托、成交或决策变化后会出现在这里。</p></div>`;
    return `<div class="execution-timeline">${rows.map((event) => {
        const presentation = event.presentation || {};
        return `<article class="execution-timeline-item">
          <time>${escapeHtml(tradingDateTime(event.occurred_at))} · ${escapeHtml(executionWorkbenchSourceLabel(event.source_kind))}</time>
          <strong>${escapeHtml(event.name || event.ticker || "账户事实")} · ${escapeHtml(presentation.headline || event.action_name_cn || "执行变化")}</strong>
          <p>${escapeHtml(presentation.reason || "执行事实发生变化")}</p>
          <small>事件 ${escapeHtml(String(event.event_id || "").slice(0, 12))}</small>
        </article>`;
    }).join("")}</div>`;
}

function renderOpportunityStatusItem(item) {
    const presentation = item?.presentation || {};
    const scenario = item?.scenario || {};
    const ticker = item?.ticker || "";
    const identity = item?.name && item.name !== ticker ? `${item.name} · ${ticker}` : ticker || "未知股票";
    return `<article class="execution-discovery-item">
      <header><div><span>${escapeHtml(presentation.state_label || item.action_name_cn || "状态")}</span><strong>${escapeHtml(identity)}</strong></div><em>${escapeHtml(scenario.scenario_name_cn || scenario.scenario_id || "常规机会")}</em></header>
      <p>${escapeHtml(presentation.reason || "等待更多可核验事实。")}</p>
      <small>下一条件：${escapeHtml(presentation.next_condition || "等待下一次扫描。")}</small>
      ${ticker ? `<button type="button" onclick="openExecutionEventDetail('${escapeHtmlAttr(ticker)}')">打开详情</button>` : ""}
    </article>`;
}

function renderOpportunityDiscovery(discovery, queues = {}) {
    const oversold = discovery?.oversold_arbitrage || {};
    const counts = discovery?.generic_counts || {};
    const genericTotal = Number(counts.ready || 0) + Number(counts.waiting || 0) + Number(counts.alternatives || 0);
    const genericItems = [
        ...(queues.primary_opportunity ? [queues.primary_opportunity] : []),
        ...(queues.alternatives || []),
        ...(queues.waiting_confirmation || []),
    ];
    const tone = oversold.status === "triggered" ? "is-positive" : ["not_evaluated", "data_blocked"].includes(oversold.status) ? "is-warning" : "is-neutral";
    const needsSync = ["not_evaluated", "data_blocked"].includes(oversold.status);
    return `<section class="execution-queue execution-queue-discovery ${tone}">
      <header class="execution-queue-head"><div><h3>新机会发现</h3><p>常规 E1–E6 与专项 E7 分开报告；数据阻断不冒充“未触发”</p></div><span class="execution-queue-count">${genericTotal}</span></header>
      <div class="execution-discovery-status">
        <div class="execution-discovery-row"><span>常规机会 · E1–E6</span><strong>${genericTotal ? `已发现 ${genericTotal} 条状态` : "今日暂无有效触发"}</strong><small>就绪 ${escapeHtml(counts.ready || 0)} · 等待 ${escapeHtml(counts.waiting || 0)} · 备选 ${escapeHtml(counts.alternatives || 0)}</small>${genericItems.length ? `<details class="execution-discovery-details"><summary>查看 ${genericItems.length} 条状态明细</summary><div>${genericItems.map(renderOpportunityStatusItem).join("")}</div></details>` : ""}</div>
        <div class="execution-discovery-row"><span>E7 · 超跌反弹隔夜</span><strong>${escapeHtml(oversold.label || "状态待确认")}</strong><p>${escapeHtml(oversold.reason || "等待扫描结果。")}</p><small>${escapeHtml(oversold.next_condition || "等待下一条可核验条件。")}</small>${needsSync ? `<button type="button" onclick="recomputeTradingWorkspace()">同步实时行情并重算</button>` : ""}</div>
      </div>
    </section>`;
}

function renderExecutionWorkbench(payload) {
    const root = document.getElementById("executionWorkbench");
    if (!root) return;
    const account = payload?.account || {};
    const summary = payload?.summary || {};
    const queues = payload?.queues || {};
    const primary = queues.primary_opportunity ? [queues.primary_opportunity] : [];
    root.className = "execution-workbench is-success";
    root.setAttribute("aria-busy", "false");
    root.innerHTML = `
      <header class="execution-workbench-head">
        <div><span class="execution-workbench-kicker">TODAY · EXECUTION</span><h2 id="executionWorkbenchTitle">今天先做什么</h2><p>真实持仓风险优先；机会只展示完整选品池中已完成条件的股票。</p></div>
        <div class="execution-workbench-account" aria-label="账户执行约束">
          <button type="button" onclick="editTradingRiskProfile('cash')"><span>可用现金</span><strong>${escapeHtml(executionWorkbenchMoney(account.available_cash))}</strong></button>
          <div class="execution-account-inline"><span>活动预留</span><strong>${escapeHtml(executionWorkbenchMoney(account.active_reserved_cash || 0))}</strong></div>
          <button type="button" onclick="editTradingRiskProfile('max_loss')"><span>单笔最大亏损</span><strong>${escapeHtml(executionWorkbenchMoney(account.max_loss_per_trade))}</strong></button>
        </div>
      </header>
      <div class="execution-workbench-summary" aria-label="执行队列摘要">
        <div class="${summary.risk_count ? "is-risk" : ""}"><span>风险处置</span><strong>${escapeHtml(summary.risk_count || 0)}</strong></div>
        <div><span>主机会</span><strong>${escapeHtml(summary.primary_count || 0)}</strong></div>
        <div><span>备选</span><strong>${escapeHtml(summary.alternative_count || 0)}</strong></div>
        <div><span>等待确认</span><strong>${escapeHtml(summary.waiting_count || 0)}</strong></div>
        <div><span>持有无变化</span><strong>${escapeHtml(summary.holding_unchanged_count || 0)}</strong></div>
        <div><span>活动委托</span><strong>${escapeHtml(summary.active_order_count || 0)}</strong></div>
      </div>
      <div class="execution-workbench-grid">
        ${renderExecutionQueue({className: "execution-queue-risk", title: "真实持仓风险", description: "只允许活动持仓、卖单与账户核验进入", items: queues.risk, emptyTitle: "持仓暂无待处置风险", emptyCopy: "空仓候选失效不会再冒充持仓风险。"})}
        ${renderExecutionQueue({className: "execution-queue-primary", title: "今日唯一主机会", description: "仅展示今天已完成全部建仓条件的机会", items: primary, emptyTitle: "今天尚无可建仓机会", emptyCopy: "点击“同步行情并重算”扫描完整选品池；等待确认不会包装成买点。", primary: true})}
        ${renderExecutionQueue({className: "execution-queue-holdings", title: "持仓状态", description: "真实持仓的最新动作与下一条件", items: queues.holding_unchanged, emptyTitle: "当前无活动持仓", emptyCopy: "真实成交后才进入这里。"})}
        ${renderOpportunityDiscovery(payload.opportunity_discovery, queues)}
        ${renderExecutionQueue({className: "execution-queue-waiting", title: "今日等待确认", description: "接近触发，但条件还没完成", items: queues.waiting_confirmation, emptyTitle: "今天暂无等待确认", emptyCopy: "尚未发现接近触发的候选。"})}
        ${renderExecutionQueue({className: "execution-queue-alternatives", title: "今日备选观察", description: "最多两只，不预占现金", items: queues.alternatives, emptyTitle: "今天暂无备选", emptyCopy: "候选尚未进入已布防状态。"})}
        <section class="execution-queue execution-queue-timeline">
          <header class="execution-queue-head"><div><h3>执行事件时间线</h3><p>只显示已勾选股票、活动持仓与活动委托；无变化不追加</p></div><span class="execution-queue-count">${Math.min((payload.timeline || []).length, 6)}</span></header>
          ${renderExecutionTimeline(payload.timeline)}
        </section>
      </div>`;
}

async function loadTradingWorkspace(options = {}) {
    const freshnessText = document.getElementById("tradingFreshnessText");
    if (tradingState.loading || tradingState.computing) {
        if (!options.silent && freshnessText) freshnessText.textContent = "已有读取任务正在进行，请稍候";
        return false;
    }
    tradingState.loading = true;
    const silent = Boolean(options.silent);
    if (!silent && freshnessText) freshnessText.textContent = "正在只读重载已有状态…";
    if (!silent) {
        const notice = document.getElementById("tradingCockpitNotice");
        if (notice) notice.textContent = "正在读取 Decision Engine...";
        tradingState.orderPanelTone = "loading";
        tradingState.orderPanelMessage = "";
        renderTradingOrderPanel();
    }
    try {
        const [workbench, dashboard, riskProfile, readiness, ordersPayload, executionsPayload] = await Promise.all([
            fetchJson("/api/v1/trading/workbench?limit=30", {timeoutMs: 25000}),
            fetchJson("/api/v1/trading/dashboard", {timeoutMs: 25000}),
            fetchJson("/api/v1/trading/risk-profile", {timeoutMs: 15000}),
            fetchJson("/api/v1/trading/readiness", {timeoutMs: 15000}),
            fetchJson("/api/v1/trading/orders?limit=50", {timeoutMs: 15000}),
            fetchJson("/api/v1/trading/executions?limit=200", {timeoutMs: 15000}),
        ]);
        tradingState.workbench = workbench;
        tradingState.dashboard = dashboard;
        tradingState.riskProfile = riskProfile;
        tradingState.readiness = readiness;
        tradingState.orders = ordersPayload.orders || [];
        tradingState.executions = executionsPayload.executions || [];
        tradingState.orderPanelTone = "default";
        tradingState.orderPanelMessage = "";
        renderExecutionWorkbench(workbench);
        const items = dashboard.holdings || [];
        if (!items.some((item) => item.ticker === tradingState.selectedTicker)) {
            tradingState.selectedTicker = items.find((item) => item.risk?.status !== "quantity_not_configured")?.ticker || items[0]?.ticker || "";
        }
        renderTradingHoldingRail(items);
        renderTradingRiskProfile(riskProfile);
        renderTradingOrderPanel();
        if (tradingState.selectedTicker) {
            await loadTradingCockpit(tradingState.selectedTicker, {silent});
        } else {
            const content = document.getElementById("tradingCockpitContent");
            if (content) content.innerHTML = `<div class="trading-empty-state"><strong>当前没有真实持仓</strong><p>风险、机会和等待确认仍显示在上方执行工作台；只有真实买入成交后才进入持仓详情。</p></div>`;
        }
        if (!silent && freshnessText) {
            freshnessText.textContent = `只读重载完成 · ${new Date().toLocaleTimeString("zh-CN", {hour12: false})} · 未生成新决策`;
        }
        return true;
    } catch (error) {
        const workbenchRoot = document.getElementById("executionWorkbench");
        if (workbenchRoot) {
            workbenchRoot.className = "execution-workbench is-error";
            workbenchRoot.setAttribute("aria-busy", "false");
            workbenchRoot.innerHTML = `<div class="execution-workbench-error"><div><h2>执行工作台读取失败</h2><p>${escapeHtml(formatError(error))}</p></div></div>`;
        }
        const content = document.getElementById("tradingCockpitContent");
        if (content) content.innerHTML = `<div class="trading-empty-state is-error"><strong>交易驾驶舱读取失败</strong><p>${escapeHtml(formatError(error))}</p></div>`;
        setTradingOrderPanelState("error", formatError(error));
    } finally {
        tradingState.loading = false;
    }
}

async function selectTradingHolding(ticker) {
    tradingState.selectedTicker = normalizeTrendPoolTicker(ticker);
    renderTradingHoldingRail(tradingState.dashboard?.holdings || []);
    await loadTradingCockpit(tradingState.selectedTicker);
}

async function loadTradingCockpit(ticker, options = {}) {
    const normalized = normalizeTrendPoolTicker(ticker);
    if (!normalized) return;
    const content = document.getElementById("tradingCockpitContent");
    const notice = document.getElementById("tradingCockpitNotice");
    if (!options.silent && content) content.innerHTML = `<div class="trading-empty-state"><strong>正在读取 ${escapeHtml(normalized)}</strong><p>组装动作、剧本、证据与时间线...</p></div>`;
    try {
        const [cockpit, timeline] = await Promise.all([
            fetchJson(`/api/v1/trading/holdings/${encodeURIComponent(normalized)}/cockpit`, {timeoutMs: 25000}),
            fetchJson(`/api/v1/trading/holdings/${encodeURIComponent(normalized)}/decision-timeline?limit=12`, {timeoutMs: 15000}),
        ]);
        tradingState.cockpit = cockpit;
        tradingState.timeline = timeline.events || [];
        if (content) content.innerHTML = renderTradingCockpit(cockpit, tradingState.timeline);
        if (notice) notice.textContent = "";
        const freshness = cockpit.decision?.data_freshness || {};
        const meta = tradingDataStateMeta[freshness.state] || {label: freshness.state || "未知"};
        const freshnessText = document.getElementById("tradingFreshnessText");
        const readiness = tradingState.readiness || {};
        const readinessLabel = readiness.job8_status === "validated"
            ? "实盘验收完成"
            : readiness.job8_status === "pending_live_samples"
                ? "等待盘中样本"
                : "实盘预检有阻断";
        if (freshnessText) freshnessText.textContent = `${meta.label} · ${readinessLabel} · 决策时间 ${tradingDateTime(cockpit.decision?.computed_at)}`;
    } catch (error) {
        if (content) content.innerHTML = `<div class="trading-empty-state is-error"><strong>${escapeHtml(normalized)} 驾驶舱不可用</strong><p>${escapeHtml(formatError(error))}</p></div>`;
        if (notice) notice.textContent = formatError(error);
    }
}

function renderTradingInlineEditor(cockpit) {
    const holding = cockpit.holding || {};
    if (!tradingInlineEditor.type || tradingInlineEditor.ticker !== holding.ticker) return "";
    if (tradingInlineEditor.type === "position") {
        return `<form class="trading-inline-editor" onsubmit="saveTradingPositionInline(event)">
          <strong>配置真实持仓</strong><p>保存后立即重算目标数量，不打开弹窗。</p>
          <div class="trading-inline-fields"><label><span>真实股数</span><input id="tradingInlineQuantity" type="number" min="1" step="1" value="${escapeHtml(holding.quantity || "")}" required></label><label><span>真实成本价</span><input id="tradingInlineCost" type="number" min="0.01" step="0.01" value="${escapeHtml(holding.cost_basis || holding.entry_price || "")}" required></label></div>
          <div class="trading-inline-actions"><button type="submit">保存并重算</button><button type="button" class="secondary" onclick="closeTradingInlineEditor()">取消</button></div>
        </form>`;
    }
    if (tradingInlineEditor.type === "execution") {
        const action = cockpit.decision?.action || {};
        const defaultQuantity = Math.abs(Number(action.change_quantity)) || Number(holding.quantity) || 0;
        return `<form class="trading-inline-editor" onsubmit="saveTradingExecutionInline(event)">
          <strong>回写真实卖出成交</strong><p>这里只记录成交事实，不会向券商发送订单。</p>
          <div class="trading-inline-fields is-three"><label><span>卖出股数</span><input id="tradingInlineExecutionQuantity" type="number" min="1" step="1" value="${escapeHtml(defaultQuantity || "")}" required></label><label><span>成交均价</span><input id="tradingInlineExecutionPrice" type="number" min="0.01" step="0.01" value="${escapeHtml(cockpit.stock?.current_price || "")}" required></label><label><span>费用</span><input id="tradingInlineExecutionFees" type="number" min="0" step="0.01" value="0" required></label></div>
          <div class="trading-inline-actions"><button type="submit">确认回写</button><button type="button" class="secondary" onclick="closeTradingInlineEditor()">取消</button></div>
        </form>`;
    }
    return "";
}

function renderTradingCockpit(cockpit, timeline) {
    const decision = cockpit.decision || {};
    const holding = cockpit.holding || {};
    const freshness = decision.data_freshness || {};
    const freshnessMeta = tradingDataStateMeta[freshness.state] || {label: freshness.state || "未知", tone: "missing"};
    const action = decision.action;
    const actionTone = tradingActionTone(action, freshness);
    const reasons = decision.reasons || [];
    const risk = cockpit.risk || {};
    const calculation = risk.calculation || {};
    const targetQuantity = action?.target_quantity ?? calculation.target_quantity;
    const changeQuantity = action?.change_quantity ?? calculation.change_quantity;
    const quantityUnavailable = risk.status === "quantity_not_configured";
    const currentScenario = decision.current_scenario;
    const switchConditions = decision.switch_conditions?.length
        ? decision.switch_conditions
        : freshness.state !== "normal"
            ? ["等待个股、5 分钟 K 与双指数数据恢复到安全阈值内"]
            : ["等待当前剧本的下一条确认条件"];
    return `
      <section class="trading-action-hero tone-${actionTone}">
        <header class="trading-stock-header">
          <div class="trading-stock-identity">
            ${stockLogoMarkup(holding.ticker, holding.name, "trading-stock-logo")}
            <div><h2>${escapeHtml(holding.name || holding.ticker)}</h2><p>${escapeHtml(holding.ticker || "-")} · ${escapeHtml(holding.sector_name || "板块待配置")} · ${escapeHtml(holdingStrategyLabels[holding.strategy_id] || holding.strategy_id || "策略待识别")}</p></div>
          </div>
          <div class="trading-header-badges"><span class="trading-data-badge tone-${freshnessMeta.tone}">${escapeHtml(freshnessMeta.label)}</span><small>${tradingDateTime(decision.computed_at)}</small></div>
        </header>
        <div class="trading-action-copy">
          <span>CURRENT ACTION</span>
          <h2>${escapeHtml(tradingActionTitle(decision))}</h2>
          <p>${escapeHtml(reasons[0] || "等待 Decision Engine 给出可审计原因。")}</p>
        </div>
        <div class="trading-action-metrics">
          <div><span>目标数量</span><strong>${quantityUnavailable ? "待配置" : targetQuantity == null ? "暂停计算" : tradingQuantity(targetQuantity)}</strong><small>${action ? "当前动作口径" : "风险计算口径"}</small></div>
          <div><span>建议变动</span><strong class="${Number(changeQuantity) < 0 ? "is-risk" : ""}">${quantityUnavailable ? "待配置" : changeQuantity == null ? "暂停计算" : tradingQuantity(changeQuantity, {signed: true})}</strong><small>负数表示卖出</small></div>
          <div><span>风险最坏损失</span><strong>${calculation.target_worst_loss == null ? "待计算" : `¥${tradingNumber(calculation.target_worst_loss)}`}</strong><small>上限 ¥${tradingNumber(calculation.max_loss_per_trade || risk.profile?.max_loss_per_trade)}</small></div>
          <div><span>当前剧本</span><strong>${escapeHtml(currentScenario?.title || "未生成")}</strong><small>${escapeHtml(currentScenario ? (tradingScenarioStateMeta[currentScenario.state]?.label || currentScenario.state) : "数据安全门阻断")}</small></div>
        </div>
        <div class="trading-action-footer">
          <div><span>下一切换条件</span>${switchConditions.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}</div>
          <div class="trading-action-buttons">
            ${["risk_exit", "prepare_reduce"].includes(action?.action_type) ? `<button type="button" onclick="recordTradingExecution()">记录实际卖出</button>` : ""}
            ${risk.status === "quantity_not_configured" ? `<button type="button" onclick="configureTradingPosition()">配置股数与成本</button>` : ""}
            <button type="button" class="secondary" onclick="switchTab('holdings')">打开持仓台账</button>
            <button type="button" class="secondary" onclick="jumpToAnalysis('${escapeHtml(holding.ticker)}')">查看单票研究</button>
          </div>
        </div>
        ${renderTradingInlineEditor(cockpit)}
      </section>

      <section class="trading-reason-section">
        <div class="trading-section-heading"><span>ACTION BASIS</span><h3>动作依据</h3><p>先看影响当前动作的规则事实。</p></div>
        <div class="trading-reason-list">
          ${reasons.slice(0, 3).map((reason, index) => `<article><b>${String(index + 1).padStart(2, "0")}</b><p>${escapeHtml(reason)}</p></article>`).join("") || `<article><b>--</b><p>暂无可用原因。</p></article>`}
        </div>
      </section>

      <section class="trading-scenario-section">
        <div class="trading-section-heading"><span>CONDITION PATH</span><h3>条件路径</h3><p>${freshness.state === "normal" ? "只执行最高优先级剧本。" : "数据恢复前仅用于诊断。"}</p></div>
        ${renderTradingScenarios(decision.candidate_scenarios || [], currentScenario, freshness.state)}
      </section>

      <section class="trading-evidence-section">
        <div class="trading-section-heading"><span>PRICE EVIDENCE</span><h3>价格证据</h3><p>关键位只解释动作，不替代动作。</p></div>
        ${renderTradingEvidence(cockpit)}
      </section>

      <details class="trading-market-section trading-compact-details">
        <summary class="trading-section-heading"><span>CONTEXT</span><h3>环境证据</h3><p>按需展开指数、板块与个股环境。</p></summary>
        ${renderTradingMarket(cockpit.market || {})}
      </details>

      <section class="trading-risk-section">
        <div class="trading-section-heading"><span>POSITION RISK</span><h3>数量与止损</h3><p>绝对最大亏损约束可执行数量。</p></div>
        ${renderTradingRisk(cockpit)}
      </section>

      <details class="trading-timeline-section trading-compact-details">
        <summary class="trading-section-heading"><span>HOLDING HISTORY</span><h3>历史决策时间线</h3><p>${escapeHtml(String((timeline || []).length))} 条变化 · 默认隐藏已过去记录</p></summary>
        ${renderTradingTimeline(timeline || [])}
      </details>

      <details class="trading-raw-data"><summary>查看原始 Decision 数据</summary><pre>${escapeHtml(pretty(cockpit))}</pre></details>`;
}

function renderTradingScenarios(scenarios, currentScenario, dataState) {
    if (!scenarios.length) return `<div class="trading-empty-inline">暂无候选剧本。</div>`;
    return `<div class="trading-scenario-grid">${scenarios.map((scenario) => {
        const meta = tradingScenarioStateMeta[scenario.state] || {label: scenario.state || "未知", tone: "far"};
        const isCurrent = currentScenario?.scenario_id === scenario.scenario_id;
        const traces = scenario.traces || [];
        return `
          <details class="trading-scenario-card tone-${meta.tone} ${isCurrent ? "is-current" : ""}" ${isCurrent ? "open" : ""}>
            <summary>
              <span><b>${escapeHtml(scenario.title || scenario.scenario_id)}</b><small>优先级 ${escapeHtml(scenario.priority ?? "-")} · 证据 ${escapeHtml(scenario.evidence_completeness || `${traces.length}/${traces.length}`)}</small></span>
              <em>${dataState !== "normal" && scenario.state === "triggered" ? "过期诊断" : escapeHtml(meta.label)}</em>
            </summary>
            <div class="trading-rule-traces">
              ${traces.map((trace) => `<article><span class="rule-state-${escapeHtml(trace.state)}"></span><div><strong>${escapeHtml(trace.label)}</strong><p>${escapeHtml(trace.reason)}</p><small>当前 ${escapeHtml(pretty(trace.actual))} · 条件 ${escapeHtml(trace.operator || "-")} ${escapeHtml(pretty(trace.expected))} · ${escapeHtml(trace.evidence_source || "-")}</small></div></article>`).join("") || `<p class="hint">暂无规则轨迹。</p>`}
              ${(scenario.cancel_conditions || []).map((item) => `<p class="trading-cancel-condition">取消条件：${escapeHtml(item)}</p>`).join("")}
            </div>
          </details>`;
    }).join("")}</div>`;
}

function renderTradingEvidence(cockpit) {
    const plan = cockpit.watch_plan || {};
    const stock = cockpit.stock || {};
    const sources = cockpit.decision?.data_freshness?.sources || [];
    const cells = [
        ["当前价", stock.current_price, "实时 quote"],
        ["冻结 MA5", plan.frozen_ma5, "开盘前日线"],
        ["硬止损", plan.hard_stop, `${plan.hard_stop_origin === "strategy" || plan.hard_stop_origin === "strategy_generated" ? "策略生成" : plan.hard_stop_origin === "manual" ? "人工确认" : "历史口径"} · 1 根 5 分钟 K`],
        ["前高", plan.previous_high, "突破剧本"],
        ["VWAP", stock.vwap, "当日已闭合 K"],
        ["量比", stock.volume_ratio, "放量阈值证据"],
        ["开盘", plan.open_price, "当日 quote"],
        ["昨收", plan.previous_close, "数据源昨收"],
    ];
    const availableCells = cells.filter(([, value]) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)));
    return `
      <div class="trading-evidence-grid">${availableCells.map(([label, value, source]) => `<article><span>${escapeHtml(label)}</span><strong>${tradingNumber(value)}</strong><small>${escapeHtml(source)}</small></article>`).join("") || `<p class="trading-empty-inline">价格证据待同步。</p>`}</div>
      <div class="trading-source-strip">${sources.filter((source) => source?.source).map((source) => {
        const meta = tradingDataStateMeta[source.state] || {label: source.state || "未知", tone: "missing"};
        return `<div><span>${escapeHtml(source.source)}</span><strong class="tone-${meta.tone}">${escapeHtml(meta.label)}</strong><small>${source.age_seconds == null ? "无时间戳" : `${tradingNumber(source.age_seconds, 1)} 秒`} · 阈值 ${escapeHtml(source.max_age_seconds)} 秒</small></div>`;
    }).join("")}</div>
      ${plan.stop_plan?.recommended_stop ? `<p class="trading-stop-evidence"><strong>止损规则：${escapeHtml(plan.stop_plan.recommended_label || plan.stop_plan.recommended_source)} ${tradingNumber(plan.stop_plan.recommended_stop)}</strong><span>${escapeHtml((plan.stop_plan.candidates || []).map((item) => `${item.label} ${tradingNumber(item.value)}`).join(" · "))}</span><small>${escapeHtml(plan.stop_plan.selection_rule || "盘中冻结，修改需确认")}</small></p>` : ""}
      <p class="trading-evidence-time">最新已闭合 5 分钟 K：${escapeHtml(stock.latest_closed_5m_at || "-")} · 共 ${escapeHtml(stock.closed_5m_count ?? 0)} 根</p>`;
}

function renderTradingMarket(market) {
    const cards = [
        {label: "上证", item: market.sse, detail: market.sse?.pct_chg == null ? "指数报价缺失" : `涨跌 ${tradingPercentPoint(market.sse.pct_chg)}`},
        {label: "创业板", item: market.chinext, detail: market.chinext?.pct_chg == null ? "指数报价缺失" : `涨跌 ${tradingPercentPoint(market.chinext.pct_chg)}`},
        {
            label: market.sector?.name || "所属板块",
            item: market.sector,
            detail: market.sector?.source === "sector_trends_cache"
                ? `${market.sector.trend_label || market.sector.stage_label || "日线参考"} · 5日 ${tradingRatioPercent(market.sector.return_5d)} · ${market.sector.analysis_date || "-"}`
                : "板块参考待补齐",
        },
        {
            label: market.stock?.name || "个股",
            item: market.stock,
            detail: market.stock?.current_price == null
                ? (market.stock?.ticker || "个股数据缺失")
                : `现价 ${tradingNumber(market.stock.current_price)} · ${tradingPercentPoint(market.stock.pct_chg)}`,
        },
    ].filter(({item}) => item && (item.current_price != null || item.pct_chg != null || item.state || item.state_code || item.analysis_date));
    if (!cards.length) return `<div class="trading-empty-inline">环境证据待同步，不为缺失数据预留空卡。</div>`;
    return `<div class="trading-market-grid">${cards.map(({label, item, detail}) => {
        const state = item?.state || "unknown";
        const stateCode = item?.state_code || "";
        const tone = ["走弱", "退潮", "weak", "retreat"].includes(state) || stateCode === "retreat" ? "weak" : ["修复", "主升", "strong", "主线前三", "主线前八"].includes(state) || ["mainline_top3", "mainline_top8", "hot"].includes(stateCode) ? "strong" : "neutral";
        return `<article class="tone-${tone}" title="${escapeHtml(detail)}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(state)}</strong><small>${escapeHtml(detail)}</small></article>`;
    }).join("")}</div>`;
}

function renderTradingRisk(cockpit) {
    const holding = cockpit.holding || {};
    const risk = cockpit.risk || {};
    const action = cockpit.decision?.action || null;
    const input = risk.inputs || {};
    const calc = risk.calculation || {};
    if (!calc.status) {
        return `<div class="trading-risk-missing"><strong>动态数量尚不可用</strong><p>${escapeHtml(risk.reason || "请配置真实股数与成本。")}</p><button type="button" onclick="configureTradingPosition()">配置 ${escapeHtml(holding.ticker || "持仓")}</button></div>`;
    }
    const rows = [
        ["当前数量", tradingQuantity(input.current_quantity)],
        ["真实成本", `¥${tradingNumber(input.cost_basis)}`],
        ["当前参考价", `¥${tradingNumber(input.current_price)}`],
        ["有效止损", `¥${tradingNumber(calc.effective_stop)}`],
        ["保持至止损最坏亏损", `¥${tradingNumber(calc.full_hold_worst_loss)}`],
        ["当前动作目标", tradingQuantity(action?.target_quantity ?? calc.target_quantity)],
        ["当前动作变动", tradingQuantity(action?.change_quantity ?? calc.change_quantity, {signed: true})],
    ];
    return `<div class="trading-risk-grid">${rows.map(([label, value], index) => `<article class="${index >= 5 ? "is-result" : ""}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`).join("")}</div><details class="trading-risk-method"><summary>查看数量与费用计算口径</summary><p>最大允许亏损 ¥${tradingNumber(calc.max_loss_per_trade)} · 风险允许数量 ${tradingQuantity(calc.risk_allowed_quantity)} · 预计费用 ¥${tradingNumber(calc.estimated_fees)} · ${escapeHtml((calc.explanation || []).join("；"))}</p></details>`;
}

function renderTradingTimeline(events) {
    if (!events.length) return `<div class="trading-timeline-empty"><strong>尚无决策变化记录</strong><p>点击“计算并记录决策”或等待盘中采集。相同判断不会重复写入。</p></div>`;
    return `<div class="trading-timeline-list">${events.map((event) => {
        const previous = event.previous || {};
        const current = event.current || {};
        return `<article><time>${escapeHtml(tradingDateTime(event.computed_at))}</time><div class="trading-timeline-change"><span>${escapeHtml(previous.action_label || "初始状态")}</span><b>→</b><strong>${escapeHtml(current.action_label || "暂停新动作")}</strong></div><p>${escapeHtml((event.reasons || []).join("；") || "无补充原因")}</p><small>${escapeHtml(current.scenario_title || "无当前剧本")} · ${escapeHtml(event.playbook_id || "-")} ${escapeHtml(event.playbook_version || "")}</small></article>`;
    }).join("")}</div>`;
}

async function recordTradingDecision() {
    const ticker = tradingState.selectedTicker;
    if (!ticker) return;
    const notice = document.getElementById("tradingCockpitNotice");
    if (notice) notice.textContent = `正在显式计算 ${ticker}，只有语义变化才会写入 Timeline...`;
    try {
        const result = await fetchJson(`/api/v1/trading/holdings/${encodeURIComponent(ticker)}/refresh`, {method: "POST", timeoutMs: 25000});
        const message = result.event_created ? "已记录一条新的决策变化。" : "决策语义未变化，Timeline 未重复写入。";
        await loadTradingWorkspace({silent: true});
        if (notice) notice.textContent = message;
    } catch (error) {
        if (notice) notice.textContent = formatError(error);
    }
}

async function recomputeTradingWorkspace() {
    if (tradingState.loading || tradingState.computing) return;
    const notice = document.getElementById("tradingCockpitNotice");
    const freshnessText = document.getElementById("tradingFreshnessText");
    const computeButton = document.getElementById("tradingComputeButton");
    const date = document.getElementById("globalDate")?.value || "";
    const benchmark = document.getElementById("globalBenchmark")?.value || "000001.SH";
    if (computeButton) computeButton.disabled = true;
    tradingState.computing = true;
    if (notice) notice.textContent = "正在同步行情并重算全部持仓与今日候选机会…";
    if (freshnessText) freshnessText.textContent = "计算进行中：持仓决策 + 今日候选";
    try {
        const result = await fetchJson(
            `/api/v1/trading/refresh?date=${encodeURIComponent(date)}&benchmark_ticker=${encodeURIComponent(benchmark)}&refresh_candidates=true`,
            {method: "POST", timeoutMs: 180000},
        );
        tradingState.computing = false;
        await loadTradingWorkspace({silent: true});
        const holdingChanges = Number(result.holdings_refresh?.event_created_count || 0);
        const candidateChanges = Number(result.candidate_refresh?.recorded_decision_count || 0);
        const candidateCount = Number(result.candidate_refresh?.candidate_count || 0);
        if (notice) notice.textContent = `计算完成：持仓变化 ${holdingChanges} 条；扫描候选 ${candidateCount} 只；机会状态变化 ${candidateChanges} 条。`;
        if (freshnessText) freshnessText.textContent = `已计算全部 · ${new Date().toLocaleTimeString("zh-CN", {hour12: false})}`;
    } catch (error) {
        if (notice) notice.textContent = formatError(error);
        if (freshnessText) freshnessText.textContent = "刷新计算失败，请查看页面提示";
    } finally {
        tradingState.computing = false;
        if (computeButton) computeButton.disabled = false;
    }
}

async function recordTradingExecution() {
    openTradingInlineEditor("execution");
}

async function saveTradingExecutionInline(event) {
    event?.preventDefault();
    const cockpit = tradingState.cockpit || {};
    const holding = cockpit.holding || {};
    if (!holding.ticker) return;
    const quantity = Number(document.getElementById("tradingInlineExecutionQuantity")?.value);
    const price = Number(document.getElementById("tradingInlineExecutionPrice")?.value);
    const fees = Number(document.getElementById("tradingInlineExecutionFees")?.value || 0);
    const notice = document.getElementById("tradingCockpitNotice");
    if (!Number.isInteger(quantity) || quantity <= 0 || !Number.isFinite(price) || price <= 0 || !Number.isFinite(fees) || fees < 0) {
        if (notice) notice.textContent = "成交股数必须是正整数，成交价必须大于 0，费用不能为负。";
        return;
    }
    try {
        const result = await fetchJson(`/api/v1/trading/holdings/${encodeURIComponent(holding.ticker)}/executions`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({side: "sell", quantity, price, fees, note: "recorded_from_trading_cockpit"}),
            timeoutMs: 25000,
        });
        let message = "";
        if (result.holding?.status === "closed") {
            tradingState.selectedTicker = "";
            message = `${holding.ticker} 的卖出成交已回写，持仓已关闭。`;
        } else {
            message = `${holding.ticker} 的卖出成交已回写，剩余 ${tradingQuantity(result.holding?.quantity)}。`;
        }
        tradingInlineEditor.type = "";
        tradingInlineEditor.ticker = "";
        await loadTradingWorkspace({silent: true});
        if (notice) notice.textContent = message;
    } catch (error) {
        if (notice) notice.textContent = formatError(error);
    }
}

async function configureTradingPosition() {
    openTradingInlineEditor("position");
}

async function saveTradingPositionInline(event) {
    event?.preventDefault();
    const holding = tradingState.cockpit?.holding;
    if (!holding) return;
    const quantity = Number(document.getElementById("tradingInlineQuantity")?.value);
    const costBasis = Number(document.getElementById("tradingInlineCost")?.value);
    if (!Number.isInteger(quantity) || quantity <= 0 || !Number.isFinite(costBasis) || costBasis <= 0) {
        const notice = document.getElementById("tradingCockpitNotice");
        if (notice) notice.textContent = "股数必须是正整数，成本价必须大于 0。";
        return;
    }
    try {
        await fetchJson(`/api/v1/holdings/${encodeURIComponent(holding.ticker)}`, {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({quantity, cost_basis: costBasis}),
        });
        tradingInlineEditor.type = "";
        tradingInlineEditor.ticker = "";
        await loadTradingWorkspace();
    } catch (error) {
        const notice = document.getElementById("tradingCockpitNotice");
        if (notice) notice.textContent = formatError(error);
    }
}

async function editTradingRiskProfile(field = "") {
    openTradingInlineEditor("risk_profile", field);
}

async function saveTradingRiskProfileInline(event) {
    event?.preventDefault();
    const profile = tradingState.riskProfile?.profile || {};
    const totalAssets = Number(document.getElementById("tradingInlineAssets")?.value);
    const availableCash = Number(document.getElementById("tradingInlineCash")?.value);
    const maxLoss = Number(document.getElementById("tradingInlineMaxLoss")?.value);
    const errorRoot = document.getElementById("tradingRiskInlineError");
    if (!Number.isFinite(totalAssets) || totalAssets <= 0 || !Number.isFinite(availableCash) || availableCash < 0 || availableCash > totalAssets || !Number.isFinite(maxLoss) || maxLoss <= 0 || maxLoss > totalAssets) {
        if (errorRoot) {
            errorRoot.className = "trading-inline-error is-error";
            errorRoot.textContent = "账户资产必须大于 0；可用现金和单笔最大亏损必须在账户资产范围内。请修正后保存。";
        }
        return;
    }
    try {
        await fetchJson("/api/v1/trading/risk-profile", {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                total_assets: totalAssets,
                available_cash: availableCash,
                max_loss_per_trade: maxLoss,
                commission_rate: profile.commission_rate,
                min_commission: profile.min_commission,
                stamp_duty_rate: profile.stamp_duty_rate,
                slippage_bps: profile.slippage_bps,
                conservative_fee_buffer_rate: profile.conservative_fee_buffer_rate ?? 0.002,
                auto_trade_enabled: false,
            }),
        });
        tradingInlineEditor.type = "";
        tradingInlineEditor.ticker = "";
        tradingInlineEditor.field = "";
        await loadTradingWorkspace({silent: true});
    } catch (error) {
        if (errorRoot) {
            errorRoot.className = "trading-inline-error is-error";
            errorRoot.textContent = formatError(error);
        }
    }
}

function startTradingAutoRefresh() {
    if (tradingRefreshTimer) clearInterval(tradingRefreshTimer);
    tradingRefreshTimer = setInterval(() => {
        const enabled = document.getElementById("tradingAutoRefresh")?.checked;
        if (enabled && activeTabId() === "trading") loadTradingWorkspace({silent: true});
    }, 30000);
}

const holdingStateMeta = {
    strong_hold: {label: "强持有", tone: "strong", action: "趋势与环境共振，按计划持有"},
    normal_pullback: {label: "正常回踩", tone: "normal", action: "回踩结构未坏，等待确认"},
    weaken_reduce: {label: "弱化减仓", tone: "reduce", action: "结构转弱，按建议降低仓位"},
    invalid_exit: {label: "失效退出", tone: "exit", action: "交易逻辑失效，优先退出"},
    overnight_locked: {label: "隔夜锁仓", tone: "normal", action: "T+1 锁定，次日按 E7 退出"},
    timed_exit: {label: "次日退出", tone: "exit", action: "E7 已到时间退出阶段"},
};

const holdingStrategyLabels = {
    ma5_v0_2: "MA5 趋势战法",
    e7_oversold_overnight: "E7 · 超跌反弹隔夜套利",
    manual: "人工计划",
};

const holdingActionLabels = {
    hold: "继续持有",
    reduce_20pct: "减仓 20%",
    reduce_50pct: "减仓 50%",
    sell_all: "全部清仓",
};

const holdingSignalLabels = {
    close_below_ma5: "收盘跌破 MA5",
    ma5_lost_2d: "连续两日失守 MA5",
    volume_break_ma5_flag: "放量跌破 MA5",
    close_below_ma10: "收盘跌破 MA10",
    volume_break_ma20_flag: "放量跌破 MA20",
    breakout_failed_flag: "突破失败",
    previous_high_lost_flag: "失守前高",
    signal_candle_open_lost: "跌破信号 K 开盘价",
    breakeven_stop_triggered: "触发保本退出",
    stop_loss_1_triggered: "触发第一止损",
    stop_loss_2_triggered: "触发第二止损",
    breakdown_level_triggered: "主升结构破坏",
    e7_hard_stop: "E7 专属硬止损",
    e7_time_exit: "E7 次日时间退出",
};

function showHoldingNotice(message, tone = "info") {
    const notice = document.getElementById("holdingsNotice");
    if (!notice) return;
    notice.className = `holdings-notice tone-${tone}`;
    notice.textContent = message || "";
}

function holdingNumber(id) {
    const value = Number(document.getElementById(id)?.value);
    return Number.isFinite(value) && value > 0 ? value : null;
}

function resetHoldingForm() {
    document.getElementById("holdingCreateForm")?.reset();
    setTradingStockPickerValue("holding");
    renderTradingStockSearchResults("holding");
    const dateInput = document.getElementById("holdingEntryDate");
    if (dateInput) dateInput.value = document.getElementById("globalDate")?.value || "";
    const sizeInput = document.getElementById("holdingEntrySize");
    if (sizeInput) sizeInput.value = "";
    holdingsState.draftStopPlan = null;
    const stop1 = document.getElementById("holdingStopLoss1");
    const stop2 = document.getElementById("holdingStopLoss2");
    const target = document.getElementById("holdingTargetPrice");
    if (stop1) delete stop1.dataset.stopMode;
    if (stop2) delete stop2.dataset.stopMode;
    if (target) delete target.dataset.targetMode;
    syncHoldingStrategyForScenario({silent: true});
    showHtml("holdingStopPlanPreview", "填写代码、买入日期和价格后，MA5 策略按 MA8 / 确认前低，人工计划按 MA8 / 支撑位−1ATR 生成止损。");
}

function updateHoldingEntrySize() {
    const input = document.getElementById("holdingEntrySize");
    if (!input) return null;
    const quantity = Number(document.getElementById("holdingQuantity")?.value);
    const cost = Number(document.getElementById("holdingEntryPrice")?.value);
    const totalAssets = Number(holdingsState.totalAssets || tradingState.riskProfile?.profile?.total_assets);
    if (![quantity, cost, totalAssets].every(Number.isFinite) || quantity <= 0 || cost <= 0 || totalAssets <= 0) {
        input.value = "";
        input.title = totalAssets > 0 ? "填写股数和买入价后自动计算" : "请先配置账户总资产";
        return null;
    }
    const ratio = (quantity * cost) / totalAssets;
    input.value = (ratio * 100).toFixed(2);
    input.title = `${quantity} × ${cost.toFixed(2)} ÷ ${totalAssets.toFixed(2)} = ${(ratio * 100).toFixed(2)}%`;
    return ratio;
}

function markHoldingStopManual() {
    holdingsState.draftStopPlan = null;
    ["holdingStopLoss1", "holdingStopLoss2"].forEach((id) => {
        const input = document.getElementById(id);
        if (input) input.dataset.stopMode = "manual";
    });
    showHtml("holdingStopPlanPreview", "当前止损由用户手工设置；系统不会在盘中自动移动。", "info");
}

function markHoldingTargetManual() {
    const input = document.getElementById("holdingTargetPrice");
    if (input) input.dataset.targetMode = "manual";
}

function syncHoldingStrategyForScenario(options = {}) {
    const signal = document.getElementById("holdingEntrySignal")?.value || "";
    const strategy = document.getElementById("holdingStrategyId");
    const target = document.getElementById("holdingTargetPrice");
    if (!strategy) return;
    const isE7 = signal === "E7";
    if (isE7) {
        strategy.value = "e7_oversold_overnight";
        strategy.disabled = true;
        if (target) {
            target.value = "";
            target.disabled = true;
            target.placeholder = "次日 10:00 前退出，无长期目标价";
            target.dataset.targetMode = "strategy";
        }
    } else {
        strategy.disabled = false;
        if (strategy.value === "e7_oversold_overnight") strategy.value = "ma5_v0_2";
        if (target) {
            target.disabled = false;
            target.placeholder = "";
        }
    }
    if (!options.silent) previewNewHoldingStopPlan({silent: true});
}

async function previewNewHoldingStopPlan(options = {}) {
    const strategyId = document.getElementById("holdingStrategyId")?.value || "manual";
    const ticker = normalizeTrendPoolTicker(document.getElementById("holdingTicker")?.value);
    const referencePrice = holdingNumber("holdingEntryPrice");
    const asOf = document.getElementById("holdingEntryDate")?.value;
    if (!ticker || !referencePrice || !asOf) {
        if (!options.silent) showHtml("holdingStopPlanPreview", "请先填写股票代码、买入日期和实际买入价。", "error");
        return;
    }
    showHtml("holdingStopPlanPreview", "正在读取已完成日线并生成止损候选...", "info");
    try {
        const query = new URLSearchParams({ticker, reference_price: String(referencePrice), as_of: asOf});
        const result = await fetchJson(`/api/v1/strategies/${encodeURIComponent(strategyId)}/stop-plan?${query}`);
        const plan = result.stop_plan || {};
        const targetPlan = result.target_plan || {};
        holdingsState.draftStopPlan = plan;
        const stop1 = document.getElementById("holdingStopLoss1");
        const stop2 = document.getElementById("holdingStopLoss2");
        const target = document.getElementById("holdingTargetPrice");
        if (stop1) {
            stop1.value = plan.recommended_stop || "";
            stop1.dataset.stopMode = "strategy";
        }
        if (stop2) {
            stop2.value = plan.secondary_stop || "";
            stop2.dataset.stopMode = "strategy";
        }
        if (target && target.dataset.targetMode !== "manual") {
            target.value = targetPlan.target_price || "";
            target.dataset.targetMode = "strategy";
        }
        const candidates = (plan.candidates || []).map((item) => `${escapeHtml(item.label)} ${holdingPrice(item.value)}`).join(" · ");
        const targetCopy = targetPlan.target_price
            ? `目标 ${holdingPrice(targetPlan.target_price)}（${escapeHtml(targetPlan.target_source || "策略生成")}） · `
            : targetPlan.trade_plan?.exit_deadline
                ? `退出期限 ${escapeHtml(targetPlan.trade_plan.exit_deadline)} · `
            : "";
        showHtml("holdingStopPlanPreview", `<strong>${targetCopy}有效止损 ${holdingPrice(plan.recommended_stop)}（${escapeHtml(plan.recommended_label || plan.recommended_source)}）</strong><span>${candidates || "无其他候选"}</span><small>${escapeHtml(plan.selection_rule || "")} · 数据截至 ${escapeHtml(plan.completed_through || plan.as_of || "-")} · ${escapeHtml(plan.price_adjustment === "forward" ? "前复权" : "口径待确认")} · 提交持仓后冻结</small>`, "success");
    } catch (error) {
        holdingsState.draftStopPlan = null;
        showHtml("holdingStopPlanPreview", escapeHtml(formatError(error)), "error");
    }
}

async function submitHolding(event) {
    event?.preventDefault();
    const ticker = normalizeTrendPoolTicker(document.getElementById("holdingTicker")?.value);
    const entryPrice = holdingNumber("holdingEntryPrice");
    const quantity = Number(document.getElementById("holdingQuantity")?.value || 0);
    const entrySize = updateHoldingEntrySize();
    const payload = {
        ticker,
        name: document.getElementById("holdingName")?.value.trim() || null,
        sector_name: document.getElementById("holdingSector")?.value.trim() || null,
        entry_date: document.getElementById("holdingEntryDate")?.value,
        entry_price: entryPrice,
        cost_basis: entryPrice,
        quantity,
        entry_signal: document.getElementById("holdingEntrySignal")?.value.trim(),
        entry_size: entrySize,
        strategy_id: document.getElementById("holdingStrategyId")?.value || "manual",
        target_price: holdingNumber("holdingTargetPrice"),
        stop_loss_1: holdingNumber("holdingStopLoss1"),
        stop_loss_2: holdingNumber("holdingStopLoss2"),
        breakdown_level: holdingNumber("holdingBreakdownLevel"),
        signal_candle_open: holdingNumber("holdingSignalOpen"),
        note: document.getElementById("holdingNote")?.value.trim() || null,
    };
    const stopMode = document.getElementById("holdingStopLoss1")?.dataset.stopMode;
    if (stopMode) payload.stop_loss_mode = stopMode;
    if (stopMode === "strategy" && holdingsState.draftStopPlan) {
        payload.stop_loss_plan = holdingsState.draftStopPlan;
    }
    payload.proposed_plan = {
        entry_price: entryPrice,
        target_price: payload.target_price,
        stop_loss_1: payload.stop_loss_1,
        stop_loss_2: payload.stop_loss_2,
        breakdown_level: payload.breakdown_level,
        signal_candle_open: payload.signal_candle_open,
    };
    Object.keys(payload.proposed_plan).forEach((key) => payload.proposed_plan[key] == null && delete payload.proposed_plan[key]);
    if (!ticker || !entryPrice || !Number.isInteger(quantity) || quantity <= 0 || !payload.entry_date || !payload.entry_signal || !Number.isFinite(entrySize) || entrySize <= 0 || entrySize > 1) {
        showHoldingNotice("请完整填写股票、买入价、真实股数、日期和买入信号；仓位比例必须能由股数 × 成本价 ÷ 总资产自动算出且不超过 100%。", "error");
        return;
    }
    Object.keys(payload).forEach((key) => payload[key] == null && delete payload[key]);
    showHoldingNotice(`正在保存 ${ticker} 持仓...`, "info");
    try {
        await fetchJson("/api/v1/holdings", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload),
        });
        resetHoldingForm();
        const panel = document.getElementById("holdingCreatePanel");
        if (panel) panel.open = false;
        showHoldingNotice(`${ticker} 已加入持仓。建议立即执行一次今日检测建立首条状态记录。`, "success");
        await loadHoldings({preserveNotice: true});
    } catch (error) {
        showHoldingNotice(formatError(error), "error");
    }
}

function latestHoldingEvent(ticker) {
    const events = holdingsState.events[normalizeTrendPoolTicker(ticker)] || [];
    return events[0] || null;
}

function holdingPercent(value, digits = 1) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "-";
    const sign = number > 0 ? "+" : "";
    return `${sign}${(number * 100).toFixed(digits)}%`;
}

function holdingPrice(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number.toFixed(2) : "-";
}

function holdingEditablePrice(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number.toFixed(2) : "";
}

function holdingPositionRatio(holding) {
    const quantity = Number(holding?.quantity);
    const cost = Number(holding?.cost_basis || holding?.entry_price);
    const totalAssets = Number(holdingsState.totalAssets);
    if (![quantity, cost, totalAssets].every(Number.isFinite) || quantity < 0 || cost <= 0 || totalAssets <= 0) return null;
    return (quantity * cost) / totalAssets;
}

function holdingPositionRatioText(holding) {
    const ratio = holdingPositionRatio(holding);
    return ratio == null ? "待配置资产" : `${(ratio * 100).toFixed(2)}%`;
}

function renderHoldingStopBasis(holding) {
    const plan = holding.stop_loss_plan || {};
    const candidates = Array.isArray(plan.candidates) ? plan.candidates : [];
    const dataDate = plan.completed_through || plan.as_of || "-";
    const adjustmentLabel = plan.price_adjustment === "forward" ? "前复权" : plan.price_adjustment === "none" ? "未复权" : "口径未知";
    if (!candidates.length) {
        return `<details class="holding-stop-basis"><summary>第一止损依据 · ${escapeHtml(holding.stop_loss_mode === "manual" ? "人工填写" : "历史口径")} · 数据日 ${escapeHtml(dataDate)}</summary><p>当前没有可展开的策略候选明细；可点击“策略止损”重新计算，或点击止损价人工修改。</p></details>`;
    }
    const candidateText = candidates.map((item) => `${escapeHtml(item.label || item.candidate_id || "候选")} ${holdingPrice(item.value)}（${escapeHtml(item.as_of || dataDate)}）`).join("；");
    return `<details class="holding-stop-basis"><summary>第一止损依据 · ${escapeHtml(plan.recommended_label || plan.recommended_source || "策略生成")} · ${escapeHtml(dataDate)}</summary><p>${escapeHtml(plan.selection_rule || "取有效候选中较高的一档作为第一保护位。")} 候选：${candidateText}。参考价 ${holdingPrice(plan.reference_price)}；价格口径 ${escapeHtml(adjustmentLabel)}（${escapeHtml(plan.daily_bar_source || "-")}）；只有低于参考价的候选才有效。</p></details>`;
}

function renderHoldingSummary(items) {
    const active = items.filter((item) => item.status === "active" || item.status === "reduced");
    const latestEvents = active.map((item) => latestHoldingEvent(item.ticker)).filter(Boolean);
    const needsAction = latestEvents.filter((item) => ["reduce_20pct", "reduce_50pct", "sell_all"].includes(item.recommended_action)).length;
    const pnlValues = latestEvents.map((item) => Number(item.unrealized_pnl)).filter(Number.isFinite);
    const averagePnl = pnlValues.length ? pnlValues.reduce((sum, value) => sum + value, 0) / pnlValues.length : null;
    const checkedToday = latestEvents.filter((item) => item.date === document.getElementById("globalDate")?.value).length;
    showHtml("holdingsSummary", `
      <article><span>活动持仓</span><strong>${active.length}</strong><small>含已减仓 ${active.filter((item) => item.status === "reduced").length} 只</small></article>
      <article class="${needsAction ? "needs-action" : ""}"><span>需处理</span><strong>${needsAction}</strong><small>减仓或退出建议</small></article>
      <article><span>平均浮盈</span><strong class="${averagePnl != null && averagePnl < 0 ? "negative" : "positive"}">${averagePnl == null ? "-" : holdingPercent(averagePnl)}</strong><small>最新日检等权平均</small></article>
      <article><span>今日检测</span><strong>${checkedToday}/${active.length}</strong><small>${checkedToday === active.length && active.length ? "已覆盖" : "可执行今日检测"}</small></article>
    `);
}

function holdingRiskDistance(current, level) {
    const price = Number(current);
    const risk = Number(level);
    if (!Number.isFinite(price) || !Number.isFinite(risk) || price <= 0) return "-";
    return holdingPercent((price - risk) / price);
}

function renderHoldingSignals(event) {
    const signals = event?.triggered_sell_signals || [];
    if (!signals.length) return `<li class="holding-signal-clear">未触发卖出信号</li>`;
    return signals.slice(0, 4).map((signal) => `
      <li><span></span><b>${escapeHtml(holdingSignalLabels[signal] || signal)}</b></li>
    `).join("");
}

function rerenderHoldingCards() {
    const list = document.getElementById("holdingsList");
    if (list) list.innerHTML = (holdingsState.items || []).map(renderHoldingCard).join("");
}

function openHoldingInlineEditor(ticker, type, field = "") {
    holdingInlineEditor.ticker = ticker;
    holdingInlineEditor.type = type;
    holdingInlineEditor.field = field;
    rerenderHoldingCards();
}

function closeHoldingInlineEditor() {
    holdingInlineEditor.ticker = "";
    holdingInlineEditor.type = "";
    holdingInlineEditor.field = "";
    holdingInlineEditor.plan = null;
    holdingInlineEditor.currentStop = null;
    rerenderHoldingCards();
}

function renderHoldingInlineEditor(holding, current) {
    if (holdingInlineEditor.ticker !== holding.ticker || !holdingInlineEditor.type) return "";
    const commonActions = `<div class="holding-inline-actions"><button type="submit">保存</button><button type="button" class="secondary" onclick="closeHoldingInlineEditor()">取消</button></div>`;
    if (holdingInlineEditor.type === "metric") {
        const definitions = {
            quantity: ["真实股数", holding.quantity || "", "1", "1"],
            entry_price: ["实际入场价", holdingEditablePrice(holding.entry_price), "0.01", "0.01"],
            cost_basis: ["实际买入成本价", holdingEditablePrice(holding.cost_basis || holding.entry_price), "0.01", "0.01"],
            target_price: ["目标价", holdingEditablePrice(holding.target_price), "0.01", "0.01"],
            manual_current_price: ["手工当前参考价（留空恢复行情）", holdingEditablePrice(holding.manual_current_price || current), "0.01", "0.01"],
        };
        const definition = definitions[holdingInlineEditor.field];
        if (!definition) return "";
        return `<form class="holding-inline-editor" data-state="default" onsubmit="saveHoldingMetricInline(event,'${escapeHtml(holding.ticker)}','${escapeHtml(holdingInlineEditor.field)}')"><strong>${definition[0]}</strong><label><span>${definition[0]}</span><input id="holdingInlineMetric" type="number" min="${definition[2]}" step="${definition[3]}" value="${escapeHtml(definition[1])}" ${holdingInlineEditor.field === "manual_current_price" ? "" : "required"}></label><p class="holding-inline-status" role="status" aria-live="polite"></p>${commonActions}</form>`;
    }
    if (holdingInlineEditor.type === "risk") {
        return `<form class="holding-inline-editor" onsubmit="saveHoldingRiskInline(event,'${escapeHtml(holding.ticker)}')"><strong>调整风险价位</strong><div class="holding-inline-fields"><label><span>第一止损</span><input id="holdingInlineStop1" type="number" min="0.01" step="0.01" value="${escapeHtml(holding.stop_loss_1 || "")}"></label><label><span>第二止损</span><input id="holdingInlineStop2" type="number" min="0.01" step="0.01" value="${escapeHtml(holding.stop_loss_2 || "")}"></label><label><span>目标价</span><input id="holdingInlineTarget" type="number" min="0.01" step="0.01" value="${escapeHtml(holding.target_price || "")}"></label></div>${commonActions}</form>`;
    }
    if (holdingInlineEditor.type === "reduce") {
        return `<form class="holding-inline-editor" onsubmit="saveHoldingReduceInline(event,'${escapeHtml(holding.ticker)}')"><strong>记录实际减仓</strong><label><span>本次减仓占原始仓位（%）</span><input id="holdingInlineReduce" type="number" min="0.01" max="100" step="0.01" value="10" required></label>${commonActions}</form>`;
    }
    if (holdingInlineEditor.type === "close") {
        return `<form class="holding-inline-editor tone-danger" onsubmit="saveHoldingCloseInline(event,'${escapeHtml(holding.ticker)}')"><strong>记录全部清仓</strong><p>保存后保留历史记录，不会向券商发送订单。</p><label><span>实际清仓价（可留空）</span><input id="holdingInlineClosePrice" type="number" min="0.01" step="0.01" value="${escapeHtml(current || "")}"></label>${commonActions}</form>`;
    }
    if (holdingInlineEditor.type === "stop_plan") {
        const plan = holdingInlineEditor.plan || {};
        const candidates = (plan.candidates || []).map((item) => `${escapeHtml(item.label || item.source || "候选")} ${holdingPrice(item.value)}`).join(" · ");
        return `<form class="holding-inline-editor" onsubmit="applyHoldingStopInline(event,'${escapeHtml(holding.ticker)}')"><strong>策略止损预览</strong><p>当前 ${holdingPrice(holdingInlineEditor.currentStop)} · 建议 ${holdingPrice(plan.recommended_stop)}（${escapeHtml(plan.recommended_label || plan.recommended_source || "策略生成")}）</p><small>${candidates || "暂无候选"}</small><div class="holding-inline-actions"><button type="submit">确认冻结止损</button><button type="button" class="secondary" onclick="closeHoldingInlineEditor()">取消</button></div></form>`;
    }
    return "";
}

function renderHoldingCard(holding) {
    const event = latestHoldingEvent(holding.ticker);
    const pendingRisk = holding.pending_risk_action || {};
    const hasPendingRisk = ["pending", "partially_executed"].includes(pendingRisk.status);
    const state = event?.holding_state || (holding.status === "closed" ? "closed" : "unchecked");
    const meta = holdingStateMeta[state] || {
        label: holding.status === "closed" ? "已结束" : "待检测",
        tone: holding.status === "closed" ? "closed" : "unchecked",
        action: holding.status === "closed" ? "该笔持仓已结束" : "执行今日检测后生成建议",
    };
    const action = hasPendingRisk ? "风险退出待成交" : (holdingActionLabels[event?.recommended_action] || meta.action);
    const marketCurrent = holding.current_price ?? event?.current_price;
    const hasManualCurrent = Number(holding.manual_current_price) > 0;
    const current = hasManualCurrent ? holding.manual_current_price : marketCurrent;
    const costBasis = Number(holding.cost_basis || holding.entry_price);
    const derivedPnl = Number.isFinite(Number(current)) && Number.isFinite(costBasis) && costBasis > 0
        ? (Number(current) - costBasis) / costBasis
        : null;
    const pnl = event?.unrealized_pnl ?? derivedPnl;
    const currentSource = hasManualCurrent
        ? "手工参考 · 点击恢复"
        : holding.current_price_kind === "realtime"
            ? `实时行情 · ${escapeHtml(tradingDateTime(holding.current_price_as_of))}`
            : holding.current_price_kind === "daily_close"
                ? `最新收盘 · ${escapeHtml(holding.current_price_as_of || "-")}`
                : event?.current_price != null
                    ? "今日检测行情"
                    : "行情待刷新";
    const statusLabel = {active: "持仓中", reduced: "已减仓", closed: "已结束"}[holding.status] || holding.status;
    const positionRatio = holdingPositionRatioText(holding);
    const positionCost = Number(holding.quantity) * Number(holding.cost_basis || holding.entry_price);
    const ratioFormula = Number.isFinite(positionCost) && Number(holdingsState.totalAssets) > 0
        ? `${tradingQuantity(holding.quantity)} × ¥${holdingPrice(holding.cost_basis || holding.entry_price)} ÷ ¥${tradingNumber(holdingsState.totalAssets, 2)}`
        : "股数 × 成本价 ÷ 总资产";
    return `
      <article class="holding-card tone-${hasPendingRisk ? "exit" : meta.tone}">
        <header class="holding-card-head">
          <button type="button" onclick="jumpToAnalysis('${escapeHtml(holding.ticker)}')">
            <span class="holding-identity">
              ${stockLogoMarkup(holding.ticker, holding.name, "holding-stock-logo")}
              <span class="holding-identity-copy">
                <strong>${escapeHtml(holding.name || holding.ticker)}</strong>
                <span>${escapeHtml(holding.ticker)} · ${escapeHtml(holding.sector_name || "未分板块")} · ${escapeHtml(holdingStrategyLabels[holding.strategy_id] || holding.strategy_id || "MA5 趋势战法")} ${escapeHtml(holding.strategy_version || "")}</span>
              </span>
            </span>
          </button>
          <div class="holding-pnl ${Number(pnl) < 0 ? "negative" : "positive"}">
            <strong>${pnl == null ? "待检测" : holdingPercent(pnl)}</strong>
            <small title="${escapeHtml(ratioFormula)}">${statusLabel} · 持仓比例 ${positionRatio}</small>
          </div>
        </header>
        <div class="holding-decision-row">
          <div><span>今日状态</span><strong>${meta.label}</strong><small>${escapeHtml(event?.date || "尚无日检记录")}</small></div>
          <div class="holding-action"><span>建议动作</span><strong>${escapeHtml(action)}</strong><small>${escapeHtml(hasPendingRisk ? "此前硬止损已经确认；实际成交回写前不会恢复为继续持有" : (event?.reason || meta.action))}</small></div>
        </div>
        ${hasPendingRisk ? `
          <div class="holding-risk-pending" role="status">
            <span>待执行</span>
            <strong>${escapeHtml(pendingRisk.label || "硬止损确认，等待实际卖出回写")}</strong>
            <small>剩余 ${tradingQuantity(holding.quantity)} · 触发于 ${tradingDateTime(pendingRisk.triggered_at)}</small>
          </div>` : ""}
        <div class="holding-level-strip">
          <div><span>股数</span><strong>${tradingQuantity(holding.quantity)}</strong><small>成交自动汇总</small></div>
          <div><span>持仓成本</span><strong>${holdingPrice(holding.cost_basis || holding.entry_price)}</strong><small>成交加权计算</small></div>
          <div><span>当前</span><button type="button" class="holding-editable-metric" onclick="editHoldingLedgerValue('${escapeHtml(holding.ticker)}','manual_current_price')"><strong>${holdingPrice(current)}</strong><small>${currentSource}</small></button></div>
          ${holding.strategy_id === "e7_oversold_overnight"
            ? `<div><span>退出期限</span><strong>次日 10:00</strong><small>09:30 起分批/清仓</small></div>`
            : `<div><span>目标</span><button type="button" class="holding-editable-metric" onclick="editHoldingLedgerValue('${escapeHtml(holding.ticker)}','target_price')"><strong>${holdingPrice(holding.target_price)}</strong><small>${escapeHtml(holding.current_plan?.target_source || "点击修改")}</small></button></div>`}
          <div><span>有效止损</span><button type="button" class="holding-editable-metric" onclick="adjustHoldingRisk('${escapeHtml(holding.ticker)}')"><strong>${holdingPrice(holding.stop_loss_1)}</strong><small>${holding.stop_loss_mode === "strategy" ? escapeHtml(holding.stop_loss_plan?.recommended_label || "策略生成") : holding.stop_loss_mode === "manual" ? "人工确认" : "历史口径"} · 距离 ${holdingRiskDistance(current, holding.stop_loss_1)}</small></button></div>
          <div><span>破坏位</span><strong>${holdingPrice(holding.breakdown_level)}</strong></div>
        </div>
        ${renderHoldingStopBasis(holding)}
        ${renderHoldingInlineEditor(holding, current)}
        <ul class="holding-signal-list">${renderHoldingSignals(event)}</ul>
        <footer class="holding-card-actions">
          <button type="button" class="secondary" onclick="loadHoldingEvents('${escapeHtml(holding.ticker)}')">查看日志</button>
          ${holding.status !== "closed" ? `
            <button type="button" class="secondary" onclick="refreshHoldingDerived('${escapeHtml(holding.ticker)}')">刷新行情与策略价位</button>
            ${hasPendingRisk ? `<button type="button" class="danger primary-risk-action" onclick="openTradingExecution('${escapeHtml(holding.ticker)}')">回写卖出成交</button>` : ""}
            <button type="button" class="secondary" onclick="adjustHoldingRisk('${escapeHtml(holding.ticker)}')">调整价位</button>
            ${["ma5_v0_2", "manual", "e7_oversold_overnight"].includes(holding.strategy_id) ? `<button type="button" class="secondary" onclick="previewAndApplyHoldingStop('${escapeHtml(holding.ticker)}')">${holding.strategy_id === "manual" ? "公式止损" : "策略止损"}</button>` : ""}
            <button type="button" class="secondary" onclick="openHoldingTrade('${escapeHtml(holding.ticker)}','buy')">记录加仓交易</button>
            <button type="button" class="danger" onclick="openHoldingTrade('${escapeHtml(holding.ticker)}','sell')">记录卖出交易</button>
          ` : ""}
        </footer>
      </article>
    `;
}

async function refreshHoldingDerived(ticker) {
    const normalized = normalizeTrendPoolTicker(ticker);
    if (!normalized) return;
    showHoldingNotice(`正在刷新 ${normalized} 的行情、目标价和止损位...`, "info");
    try {
        const result = await fetchJson(`/api/v1/holdings/${encodeURIComponent(normalized)}/refresh-derived?overwrite=true`, {
            method: "POST",
            timeoutMs: 30000,
        });
        await loadHoldings({preserveNotice: true});
        const holding = result.holding || {};
        const planSummary = holding.strategy_id === "e7_oversold_overnight"
            ? "次日 10:00 前退出"
            : `目标 ${holdingPrice(holding.target_price)}`;
        showHoldingNotice(`${holding.name || normalized} 已刷新：当前 ${holdingPrice(holding.current_price)} · ${planSummary} · 止损 ${holdingPrice(holding.stop_loss_1)}`, "success");
    } catch (error) {
        showHoldingNotice(`刷新失败：${formatError(error)}`, "error");
    }
}

async function openTradingExecution(ticker) {
    return openHoldingTrade(ticker, "sell");
}

async function openHoldingTrade(ticker, side = "sell") {
    const normalized = normalizeTrendPoolTicker(ticker);
    tradingState.selectedTicker = normalized;
    const holding = (holdingsState.items || []).find((item) => item.ticker === normalized) || {};
    setTradingStockPickerValue("order", {ticker: normalized, name: holding.name || normalized, industry: holding.sector_name || ""});
    switchTab("holdings");
    renderTradingOrderPanel();
    requestAnimationFrame(() => {
        const form = document.getElementById("tradingOrderForm");
        if (!form) return;
        form.elements.ticker.value = normalized;
        form.elements.side.value = side === "buy" ? "buy" : "sell";
        syncTradingOrderIntent(form);
        form.elements.intent.value = side === "buy" ? "add" : "reduce";
        form.elements.scenario_id.innerHTML = renderTradingScenarioOptions(form.elements.side.value, form.elements.intent.value);
        document.getElementById("tradingOrderPanel")?.scrollIntoView({behavior: "smooth", block: "start"});
    });
}

async function loadHoldings(options = {}) {
    const list = document.getElementById("holdingsList");
    if (!list || holdingsState.loading) return;
    holdingsState.loading = true;
    list.innerHTML = `<p class="hint">正在读取持仓和最新状态...</p>`;
    const status = document.getElementById("holdingsStatusFilter")?.value || "open";
    try {
        const statusQuery = status === "open" ? "" : `?status=${encodeURIComponent(status)}`;
        const [payload, riskPayload, ordersPayload, executionsPayload] = await Promise.all([
            fetchJson(`/api/v1/holdings${statusQuery}`),
            fetchJson("/api/v1/trading/risk-profile"),
            fetchJson("/api/v1/trading/orders?limit=50"),
            fetchJson("/api/v1/trading/executions?limit=200"),
        ]);
        const items = payload.holdings || [];
        const eventEntries = await Promise.all(items.map(async (holding) => {
            try {
                const history = await fetchJson(`/api/v1/holdings/${encodeURIComponent(holding.ticker)}/events?limit=60`);
                return [holding.ticker, history.events || []];
            } catch {
                return [holding.ticker, []];
            }
        }));
        holdingsState = {
            items,
            events: Object.fromEntries(eventEntries),
            loading: false,
            draftStopPlan: holdingsState.draftStopPlan || null,
            totalAssets: Number(riskPayload.profile?.total_assets) || null,
        };
        tradingState.riskProfile = riskPayload;
        tradingState.orders = ordersPayload.orders || [];
        tradingState.executions = executionsPayload.executions || [];
        renderHoldingSummary(items);
        renderTradingOrderPanel();
        list.innerHTML = items.length
            ? items.map(renderHoldingCard).join("")
            : `<div class="holdings-empty"><strong>暂无${status === "closed" ? "已结束" : "活动"}持仓</strong><p>请先在上方记录券商买入委托，再回写真正成交；成交后会自动生成持仓。</p><button type="button" onclick="document.getElementById('tradingOrderPanel').scrollIntoView({behavior:'smooth'})">记录第一笔交易</button></div>`;
        if (!options.preserveNotice) showHoldingNotice(`已读取 ${items.length} 条持仓记录。`, "info");
    } catch (error) {
        holdingsState.loading = false;
        list.innerHTML = `<div class="holdings-empty error"><strong>持仓读取失败</strong><p>${escapeHtml(formatError(error))}</p></div>`;
        showHoldingNotice(formatError(error), "error");
    }
}

async function runHoldingDailyCheck() {
    const date = document.getElementById("globalDate")?.value;
    const benchmark = selectedBenchmarkTicker();
    showHoldingNotice(`正在检测 ${date} 的全部活动持仓...`, "info");
    try {
        const result = await fetchJson(`/api/v1/holdings/daily-check?date=${encodeURIComponent(date)}&benchmark_ticker=${encodeURIComponent(benchmark)}`, {
            method: "POST",
            timeoutMs: 120000,
        });
        const states = Object.entries(result.state_counts || {}).map(([key, count]) => `${holdingStateMeta[key]?.label || key} ${count}`).join("、");
        tradingState.dashboard = null;
        tradingState.cockpit = null;
        showHoldingNotice(`检测完成：扫描 ${result.checked} 只，${states || "暂无活动持仓"}，失败 ${result.failed || 0}。进入驾驶舱计算后会同步退出动作。`, result.failed ? "error" : "success");
        await loadHoldings({preserveNotice: true});
    } catch (error) {
        showHoldingNotice(formatError(error), "error");
    }
}

async function reduceHolding(ticker) {
    openHoldingInlineEditor(ticker, "reduce");
}

async function saveHoldingReduceInline(event, ticker) {
    event?.preventDefault();
    const holding = holdingsState.items.find((item) => item.ticker === ticker);
    if (!holding) return;
    const input = document.getElementById("holdingInlineReduce")?.value || "";
    const amount = Number(input) / 100;
    const realized = Number(holding.realized_size || 0) + amount;
    if (!Number.isFinite(amount) || amount <= 0 || realized > Number(holding.entry_size || 0) + 1e-6) {
        showHoldingNotice(`减仓后累计比例不能超过原始仓位 ${(Number(holding.entry_size || 0) * 100).toFixed(0)}%。`, "error");
        return;
    }
    try {
        await fetchJson(`/api/v1/holdings/${encodeURIComponent(ticker)}`, {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({realized_size: realized}),
        });
        showHoldingNotice(`${ticker} 已记录本次减仓 ${input}%。`, "success");
        holdingInlineEditor.ticker = "";
        holdingInlineEditor.type = "";
        await loadHoldings({preserveNotice: true});
    } catch (error) {
        showHoldingNotice(formatError(error), "error");
    }
}

async function previewAndApplyHoldingStop(ticker) {
    showHoldingNotice(`正在用前复权日线计算 ${ticker} 的止损候选...`, "info");
    try {
        const preview = await fetchJson(`/api/v1/holdings/${encodeURIComponent(ticker)}/stop-plan`);
        holdingInlineEditor.ticker = ticker;
        holdingInlineEditor.type = "stop_plan";
        holdingInlineEditor.field = "";
        holdingInlineEditor.plan = preview.stop_plan || {};
        holdingInlineEditor.currentStop = preview.holding_stop?.effective_stop || null;
        rerenderHoldingCards();
        showHoldingNotice(`${ticker} 的策略止损已在卡片内展开，确认后才会冻结。`, "info");
    } catch (error) {
        showHoldingNotice(formatError(error), "error");
    }
}

async function applyHoldingStopInline(event, ticker) {
    event?.preventDefault();
    const plan = holdingInlineEditor.plan || {};
    try {
        const applied = await fetchJson(`/api/v1/holdings/${encodeURIComponent(ticker)}/stop-plan/apply`, {
            method: "POST",
            timeoutMs: 25000,
        });
        showHoldingNotice(
            applied.action === "unadjusted_stop_corrected"
                ? `${ticker} 的未复权旧止损已纠正为 ${holdingPrice(applied.effective_stop)}。`
                : applied.action === "tighter_existing_preserved"
                ? `${ticker} 现有止损更严格，已保留 ${holdingPrice(applied.effective_stop)}，未向下放宽至 ${holdingPrice(plan.recommended_stop)}。`
                : `${ticker} 已确认策略止损 ${holdingPrice(applied.effective_stop)}。`,
            "success",
        );
        holdingInlineEditor.ticker = "";
        holdingInlineEditor.type = "";
        holdingInlineEditor.plan = null;
        await loadHoldings({preserveNotice: true});
    } catch (error) {
        showHoldingNotice(formatError(error), "error");
    }
}

async function editHoldingLedgerValue(ticker, field) {
    openHoldingInlineEditor(ticker, "metric", field);
}

async function saveHoldingMetricInline(event, ticker, field) {
    event?.preventDefault();
    const holding = holdingsState.items.find((item) => item.ticker === ticker);
    if (!holding) return;
    const definitions = {
        quantity: {
            label: "真实股数",
            value: holding.quantity || "",
            help: "请输入正整数股数。修改会影响动态减仓数量。",
        },
        entry_price: {
            label: "实际入场价",
            value: holding.entry_price || "",
            help: "仅用于纠正成交录入并保留修订记录；不会自动覆盖真实成本价。",
        },
        cost_basis: {
            label: "实际买入成本价",
            value: holding.cost_basis || holding.entry_price || "",
            help: "用于计算持仓比例：真实股数 × 买入成本价 ÷ 账户总资产。",
        },
        manual_current_price: {
            label: "手工当前参考价",
            value: holding.manual_current_price || latestHoldingEvent(ticker)?.current_price || "",
            help: "只影响持仓台账显示，不参与 Decision Engine。留空可恢复行情值。",
        },
        target_price: {
            label: "目标价",
            value: holding.target_price || "",
            help: "由你填写并保留修订记录，不会被持仓比例或止损计算覆盖。",
        },
    };
    const definition = definitions[field];
    if (!definition) return;
    const form = event?.currentTarget;
    const inputElement = document.getElementById("holdingInlineMetric");
    const status = form?.querySelector(".holding-inline-status");
    const submit = form?.querySelector('button[type="submit"]');
    const showInlineError = (message) => {
        if (form) form.dataset.state = "error";
        if (inputElement) inputElement.setAttribute("aria-invalid", "true");
        if (status) status.textContent = message;
        showHoldingNotice(message, "error");
    };
    const input = inputElement?.value ?? "";
    let value = input.trim() === "" && field === "manual_current_price" ? null : Number(input);
    if (field === "quantity" && (!Number.isInteger(value) || value <= 0)) {
        showInlineError("真实股数必须是正整数。");
        return;
    }
    if (field !== "quantity" && value !== null && (!Number.isFinite(value) || value <= 0)) {
        showInlineError(`${definition.label}必须大于 0。`);
        return;
    }
    if (form) form.dataset.state = "loading";
    if (inputElement) inputElement.removeAttribute("aria-invalid");
    if (status) status.textContent = "正在保存…";
    if (submit) {
        submit.disabled = true;
        submit.setAttribute("aria-disabled", "true");
    }
    try {
        const result = await fetchJson(`/api/v1/holdings/${encodeURIComponent(ticker)}`, {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({[field]: value}),
        });
        const updated = result.holding || {};
        const message = field === "manual_current_price" && value == null
            ? `${ticker} 已恢复使用行情当前价。`
            : `${ticker} 的${definition.label}已更新为 ${field === "quantity" ? tradingQuantity(updated[field]) : holdingPrice(updated[field])}。`;
        showHoldingNotice(message, "success");
        if (form) form.dataset.state = "success";
        if (status) status.textContent = "已保存。";
        holdingInlineEditor.ticker = "";
        holdingInlineEditor.type = "";
        holdingInlineEditor.field = "";
        tradingState.dashboard = null;
        if (tradingState.cockpit?.holding?.ticker === ticker) tradingState.cockpit = null;
        await loadHoldings({preserveNotice: true});
    } catch (error) {
        if (form) form.dataset.state = "error";
        if (status) status.textContent = `保存失败：${formatError(error)}`;
        if (submit) {
            submit.disabled = false;
            submit.removeAttribute("aria-disabled");
        }
        showHoldingNotice(formatError(error), "error");
    }
}

async function adjustHoldingRisk(ticker) {
    openHoldingInlineEditor(ticker, "risk");
}

async function saveHoldingRiskInline(event, ticker) {
    event?.preventDefault();
    const holding = holdingsState.items.find((item) => item.ticker === ticker);
    if (!holding) return;
    const stop1 = document.getElementById("holdingInlineStop1")?.value || "";
    const stop2 = document.getElementById("holdingInlineStop2")?.value || "";
    const target = document.getElementById("holdingInlineTarget")?.value || "";
    const updates = {};
    [["stop_loss_1", stop1], ["stop_loss_2", stop2], ["target_price", target]].forEach(([key, value]) => {
        const number = Number(value);
        if (value !== "" && Number.isFinite(number) && number > 0) updates[key] = number;
    });
    if (!Object.keys(updates).length) return;
    try {
        await fetchJson(`/api/v1/holdings/${encodeURIComponent(ticker)}`, {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(updates),
        });
        showHoldingNotice(`${ticker} 的风险价位已更新。`, "success");
        holdingInlineEditor.ticker = "";
        holdingInlineEditor.type = "";
        await loadHoldings({preserveNotice: true});
    } catch (error) {
        showHoldingNotice(formatError(error), "error");
    }
}

async function closeHoldingPosition(ticker) {
    openHoldingInlineEditor(ticker, "close");
}

async function saveHoldingCloseInline(event, ticker) {
    event?.preventDefault();
    const holding = holdingsState.items.find((item) => item.ticker === ticker);
    if (!holding) return;
    const priceText = document.getElementById("holdingInlineClosePrice")?.value || "";
    const price = Number(priceText);
    const payload = {exit_date: document.getElementById("globalDate")?.value, reason: "closed_from_holdings_ui"};
    if (priceText !== "" && Number.isFinite(price) && price > 0) payload.exit_price = price;
    try {
        await fetchJson(`/api/v1/holdings/${encodeURIComponent(ticker)}/close`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload),
        });
        showHoldingNotice(`${ticker} 已记录清仓并保留事件历史。`, "success");
        holdingInlineEditor.ticker = "";
        holdingInlineEditor.type = "";
        await loadHoldings({preserveNotice: true});
    } catch (error) {
        showHoldingNotice(formatError(error), "error");
    }
}

function renderHoldingEvent(event) {
    const meta = holdingStateMeta[event.holding_state] || {label: event.holding_state || "未知", tone: "unchecked"};
    const signals = event.triggered_sell_signals || [];
    return `
      <article class="holding-event-item tone-${meta.tone}">
        <time>${escapeHtml(event.date || "-")}</time>
        <div class="holding-event-main">
          <div><strong>${escapeHtml(meta.label)}</strong><span>${escapeHtml(holdingActionLabels[event.recommended_action] || event.recommended_action || "-")}</span></div>
          <p>${escapeHtml(event.reason || "无补充说明")}</p>
          <small>${signals.length ? signals.map((signal) => escapeHtml(holdingSignalLabels[signal] || signal)).join(" · ") : "未触发卖出信号"}</small>
        </div>
        <div class="holding-event-metrics"><strong>${holdingPrice(event.current_price)}</strong><span>${holdingPercent(event.unrealized_pnl)}</span></div>
      </article>
    `;
}

async function loadHoldingEvents(ticker) {
    const panel = document.getElementById("holdingEventPanel");
    const list = document.getElementById("holdingEventList");
    if (!panel || !list) return;
    panel.hidden = false;
    document.getElementById("holdingEventTitle").textContent = `${ticker} · 持仓事件`;
    list.innerHTML = `<p class="hint">正在读取事件历史...</p>`;
    panel.scrollIntoView({behavior: "smooth", block: "start"});
    try {
        const payload = await fetchJson(`/api/v1/holdings/${encodeURIComponent(ticker)}/events?limit=60`);
        holdingsState.events[ticker] = payload.events || [];
        list.innerHTML = payload.events?.length
            ? payload.events.map(renderHoldingEvent).join("")
            : `<div class="holdings-empty"><strong>尚无事件记录</strong><p>执行今日检测后，这里会保留每日状态、卖出信号和建议动作。</p></div>`;
    } catch (error) {
        list.innerHTML = `<div class="holdings-empty error"><strong>事件读取失败</strong><p>${escapeHtml(formatError(error))}</p></div>`;
    }
}

function closeHoldingEventPanel() {
    const panel = document.getElementById("holdingEventPanel");
    if (panel) panel.hidden = true;
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
    const bm = encodeURIComponent(selectedBenchmarkTicker());
    const sourceEl = document.getElementById("intradaySource");
    const source = sourceEl ? sourceEl.value : "tushare";
    const selected = selectedTrendPoolTickers().map(encodeURIComponent).join(",");
    if (showLoading) {
        showHtml("intradayResult", `<p class="hint">正在拉取实时数据（source=${source}）...</p>`);
    }
    try {
        await syncTrendPoolToBackend();
        const data = await fetchJson(`/api/v1/market/intraday?benchmark_ticker=${bm}&include_candidates=true&source=${source}&selected_tickers=${selected}`, {timeoutMs: 60000});
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

function selectIntradayCandidate(ticker) {
    selectedIntradayTicker = normalizeTrendPoolTicker(ticker);
    if (latestIntradayData) renderIntradayDecisionWorkspace(latestIntradayData);
}

function intradayChartSeries(candidate) {
    const bars = Array.isArray(candidate?.minute_bars) ? candidate.minute_bars : [];
    if (bars.length) {
        return {
            source: `真实 ${candidate.minute_bar_interval || 5} 分钟 K`,
            points: bars.map((bar) => ({
                time: bar.datetime,
                open: Number(bar.open),
                price: Number(bar.close),
                high: Number(bar.high),
                low: Number(bar.low),
                volume: Number(bar.volume),
                amount: Number(bar.amount),
            })).filter((item) => {
                if (!Number.isFinite(item.price)) return false;
                const hm = String(item.time || "").slice(11, 16);
                return (hm >= "09:30" && hm <= "11:30") || (hm >= "13:00" && hm <= "15:00");
            }),
        };
    }
    const realtime = candidate?.realtime || {};
    return {
        source: "分钟 K 尚未落库 · 当前为轮询报价兜底",
        points: rememberIntradaySeriesPoint(candidate?.ticker, candidate?.name, realtime, candidate?.signal_events),
    };
}

function renderIntradayDecisionChart(candidate) {
    const series = intradayChartSeries(candidate);
    if (!series.points.length) return `<div class="intraday-chart-empty">暂无价格轨迹，等待行情接入。</div>`;
    let cumulativeVolume = 0;
    let cumulativeAmount = 0;
    let cumulativeTypicalAmount = 0;
    const chartPoints = series.points.map((item) => {
        const volume = Number(item.volume);
        const amount = Number(item.amount);
        let vwap = null;
        if (Number.isFinite(volume) && volume > 0) {
            cumulativeVolume += volume;
            cumulativeAmount += Number.isFinite(amount) && amount > 0 ? amount : Number(item.price) * volume;
            cumulativeTypicalAmount += Number(item.price) * volume;
            const rawVwap = cumulativeAmount / cumulativeVolume;
            // A 股成交额有时以元、千元或万元返回；只接受与价格同量级的结果。
            vwap = Number.isFinite(rawVwap) && rawVwap > Number(item.price) * 0.2 && rawVwap < Number(item.price) * 5
                ? rawVwap
                : cumulativeTypicalAmount / cumulativeVolume;
        }
        return {...item, vwap};
    });
    const prices = chartPoints.flatMap((item) => [Number(item.low), Number(item.high), Number(item.price), Number(item.vwap)]).filter(Number.isFinite);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = Math.max(max - min, Math.abs(max) * 0.0015, 0.01);
    const plotted = chartPoints.map((item, index) => ({
        ...item,
        x: chartPoints.length === 1 ? 50 : 3 + (index * 94) / (chartPoints.length - 1),
        y: 76 - ((Number(item.price) - min) / range) * 63,
        openY: Number.isFinite(item.open) ? 76 - ((Number(item.open) - min) / range) * 63 : null,
        highY: Number.isFinite(item.high) ? 76 - ((Number(item.high) - min) / range) * 63 : null,
        lowY: Number.isFinite(item.low) ? 76 - ((Number(item.low) - min) / range) * 63 : null,
        vwapY: Number.isFinite(item.vwap) ? 76 - ((Number(item.vwap) - min) / range) * 63 : null,
    }));
    const line = plotted.map((item) => `${item.x},${item.y}`).join(" ");
    const vwapLine = plotted.filter((item) => Number.isFinite(item.vwapY)).map((item) => `${item.x},${item.vwapY}`).join(" ");
    const maxVolume = Math.max(...plotted.map((item) => Number(item.volume) || 0), 0);
    const barWidth = Math.max(0.7, Math.min(2.6, 88 / Math.max(plotted.length, 1)));
    const candleWidth = Math.max(0.65, Math.min(1.55, barWidth * 0.72));
    const candles = plotted.map((item) => {
        if (![item.openY, item.highY, item.lowY].every(Number.isFinite)) return "";
        const rising = Number(item.price) >= Number(item.open);
        const top = Math.min(item.openY, item.y);
        const height = Math.max(0.8, Math.abs(item.y - item.openY));
        return `<g class="intraday-candle ${rising ? "is-up" : "is-down"}"><line x1="${item.x}" y1="${item.highY}" x2="${item.x}" y2="${item.lowY}"></line><rect x="${item.x - candleWidth / 2}" y="${top}" width="${candleWidth}" height="${height}"></rect></g>`;
    }).join("");
    const volumeBars = maxVolume > 0 ? plotted.map((item) => {
        const height = Math.max(0.8, ((Number(item.volume) || 0) / maxVolume) * 13);
        return `<rect class="intraday-volume-bar" x="${item.x - barWidth / 2}" y="${96 - height}" width="${barWidth}" height="${height}"></rect>`;
    }).join("") : "";
    const last = plotted[plotted.length - 1];
    const firstPrice = Number(chartPoints[0]?.open) || Number(chartPoints[0]?.price);
    const sessionChange = firstPrice > 0 ? (Number(last.price) - firstPrice) / firstPrice : null;
    const legend = `<small>${chartPoints.length} 根 · 高 ${max.toFixed(2)} · 低 ${min.toFixed(2)} · ${colorPercent(sessionChange)}</small>`;
    return `
      <div class="intraday-decision-chart">
        <div class="intraday-chart-caption"><span>${escapeHtml(series.source)}</span>${legend}<b>${escapeHtml(String(last.price))}</b></div>
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(candidate?.name || candidate?.ticker || "个股")}盘中价格趋势">
          <line x1="2" y1="14" x2="98" y2="14"></line>
          <line x1="2" y1="45" x2="98" y2="45"></line>
          <line x1="2" y1="78" x2="98" y2="78"></line>
          ${volumeBars}
          ${vwapLine ? `<polyline class="intraday-vwap-line" points="${vwapLine}"></polyline>` : ""}
          ${candles}
          <polyline class="intraday-price-line" points="${line}"></polyline>
          <circle cx="${last.x}" cy="${last.y}" r="1.8"></circle>
        </svg>
        <div class="intraday-chart-axis"><span>${escapeHtml(String(chartPoints[0]?.time || "").slice(11, 16))}</span><span>${escapeHtml(String(last.time || "").slice(11, 16))}</span></div>
      </div>`;
}

function intradayQuoteAgeLabel(value, serverTime) {
    const quote = Date.parse(value || "");
    const server = Date.parse(serverTime || "");
    if (!Number.isFinite(quote) || !Number.isFinite(server)) return "时效待确认";
    const seconds = Math.max(0, Math.round((server - quote) / 1000));
    return seconds < 60 ? `${seconds}秒前` : `${Math.floor(seconds / 60)}分钟前`;
}

function renderIntradayOrderBook(realtime, capabilities = {}) {
    const bids = Array.isArray(realtime?.depth_bids) ? realtime.depth_bids.slice(0, 3) : [];
    const asks = Array.isArray(realtime?.depth_asks) ? realtime.depth_asks.slice(0, 3) : [];
    const inner = Number(realtime?.inner_volume);
    const outer = Number(realtime?.outer_volume);
    const hasVolumes = Number.isFinite(inner) && Number.isFinite(outer) && inner + outer > 0;
    if (!bids.length && !asks.length && !hasVolumes) {
        const message = capabilities.depth_status === "plan_not_enabled"
            ? (capabilities.depth_note || "当前套餐未启用实时五档；实时价与 5 分钟 K 不受影响")
            : "当前源未返回五档；以 VWAP、量比和现价区间观察承接";
        return `<div class="intraday-orderbook-empty"><b>盘口</b><span>${escapeHtml(message)}</span></div>`;
    }
    const outerRatio = hasVolumes ? outer / (inner + outer) : null;
    const rows = [0, 1, 2].map((index) => {
        const bid = bids[index] || {};
        const ask = asks[index] || {};
        return `<div><span class="up">买${index + 1} ${escapeHtml(bid.price ?? "-")} · ${escapeHtml(bid.volume ?? "-")}</span><span class="down">卖${index + 1} ${escapeHtml(ask.price ?? "-")} · ${escapeHtml(ask.volume ?? "-")}</span></div>`;
    }).join("");
    return `<div class="intraday-orderbook"><header><b>盘口三档</b><span>${outerRatio == null ? "内外盘待返回" : `外盘 ${formatPercent(outerRatio)} · 内盘 ${formatPercent(1 - outerRatio)}`}</span></header>${rows}</div>`;
}

function renderIntradayCandidateRail(candidates) {
    return `
      <div class="intraday-candidate-rail" role="tablist" aria-label="切换盘中个股">
        ${(candidates || []).map((candidate) => {
        const rt = candidate.realtime || {};
        const active = normalizeTrendPoolTicker(candidate.ticker) === selectedIntradayTicker;
        return `<button type="button" role="tab" aria-selected="${active}" class="intraday-rail-card ${active ? "is-active" : ""}" onclick="selectIntradayCandidate('${escapeHtml(candidate.ticker)}')">
            <span><strong>${escapeHtml(candidate.name || candidate.ticker)}</strong><small>${escapeHtml(candidate.ticker || "")}</small></span>
            <b class="${Number(rt.pct_chg) >= 0 ? "up" : "down"}">${colorPercent(rt.pct_chg)}</b>
            <em>${escapeHtml(candidate.primary_event?.title || candidate.technical_rating?.label || "等待信号")}</em>
          </button>`;
    }).join("")}
      </div>`;
}

function intradayActionSummary(candidate) {
    const event = candidate?.primary_event || {};
    const rating = candidate?.technical_rating || {};
    if (["stop_loss_broken", "support_broken"].includes(event.code)) {
        return {action: "执行减仓 / 退出", tone: "bearish", reason: event.detail || event.action || "风险边界已经触发。"};
    }
    if (["pressure_breakout", "support_reclaimed", "ma8_reclaim"].includes(event.code)) {
        return {action: "继续持有并观察确认", tone: "bullish", reason: event.detail || event.action || "结构正在修复或突破。"};
    }
    if (event.code === "buy_zone_touched") {
        return {action: "进入触发观察", tone: "bullish", reason: event.detail || "价格进入计划区域，仍需承接确认。"};
    }
    return {action: "等待剧本触发", tone: intradayToneClass(rating.tone), reason: event.detail || event.action || "当前没有满足动作条件的硬触发。"};
}

function renderIntradayDecisionWorkspace(data) {
    latestIntradayData = data;
    const candidates = data.candidates_realtime || [];
    if (!candidates.length) {
        showHtml("intradayResult", '<div class="intraday-empty-state"><strong>盘中监控池为空</strong><p>请先到选品池勾选股票，再返回盘中行情。</p><button type="button" onclick="switchTab(\'selection\')">打开选品池</button></div>');
        return;
    }
    if (!candidates.some((item) => normalizeTrendPoolTicker(item.ticker) === selectedIntradayTicker)) {
        const signaled = candidates.find((item) => (item.signal_events || []).some((event) => !["no_trigger", "realtime_missing"].includes(event.code)));
        selectedIntradayTicker = normalizeTrendPoolTicker((signaled || candidates[0]).ticker);
    }
    const candidate = candidates.find((item) => normalizeTrendPoolTicker(item.ticker) === selectedIntradayTicker) || candidates[0];
    const realtime = candidate.realtime || {};
    const plan = candidate.trade_plan || {};
    const action = intradayActionSummary(candidate);
    const events = (candidate.signal_events || []).filter((event) => event.code !== "no_trigger");
    // Scenario 只描述未来切换条件，不再复述已经发生的 Timeline 事件。
    const scenarios = [
        plan.pressure_price ? {title: `站上 ${formatCandidatePrice(plan.pressure_price)}`, action: "放量确认后切换突破剧本"} : null,
        plan.ma8 ? {title: `回踩 ${formatCandidatePrice(plan.ma8)}`, action: "收回 MA8 才保留趋势剧本"} : null,
        plan.stop_loss ? {title: `跌破 ${formatCandidatePrice(plan.stop_loss)}`, action: "切换风险退出，不等待主观修复"} : null,
    ].filter(Boolean);
    const br = data.benchmark_realtime || {};
    const gate = data.market_gate || {};
    const marketTone = Number(br.pct_chg) > 0 ? "修复" : Number(br.pct_chg) < 0 ? "走弱" : "震荡";
    const openGap = candidate.open_verified?.open_gap_pct;
    const relativeStrength = candidate.market_alignment?.relative_strength;
    const quoteTime = realtime.quote_at || realtime.update_at || realtime.fetched_at;
    const timeline = candidate.decision_timeline || [];
    const currentPrice = Number(realtime.current);
    const stopPrice = Number(plan.stop_loss);
    const targetPrice = Number(plan.expected_sell_price || plan.pressure_price);
    const stopDistance = Number.isFinite(currentPrice) && Number.isFinite(stopPrice) && currentPrice > 0 ? (currentPrice - stopPrice) / currentPrice : null;
    const targetDistance = Number.isFinite(currentPrice) && Number.isFinite(targetPrice) && currentPrice > 0 ? (targetPrice - currentPrice) / currentPrice : null;
    const rewardRisk = Number.isFinite(stopDistance) && stopDistance > 0 && Number.isFinite(targetDistance) ? targetDistance / stopDistance : null;
    showHtml("intradayResult", `
      <div class="intraday-decision-shell">
        <div class="intraday-status-line"><span>会话 ${escapeHtml(data.session_date || data.analysis_date || "-")} · 日线基线 ${escapeHtml(data.daily_baseline_date || data.part1_analysis_date || "-")} · ${escapeHtml(data.realtime_source || "无实时源")} · ${escapeHtml(data.server_time || "-")}</span><b>${escapeHtml(gate.state_label || "市场权限待确认")}</b></div>
        ${renderIntradayCandidateRail(candidates)}
        <div class="intraday-decision-grid">
          <section class="intraday-decision-panel intraday-action-panel tone-${action.tone}">
            <span class="decision-step">ACTION</span>
            <div class="intraday-stock-heading"><div><strong>${escapeHtml(candidate.name || candidate.ticker)}</strong><small>${escapeHtml(candidate.ticker)} · ${escapeHtml(candidate.sector_name || candidate.industry || "-")}</small></div><b class="${Number(realtime.pct_chg) >= 0 ? "up" : "down"}">${escapeHtml(String(realtime.current ?? "-"))} · ${colorPercent(realtime.pct_chg)}</b></div>
            <h2>${escapeHtml(action.action)}</h2>
            <p>${escapeHtml(action.reason)}</p>
            <div class="decision-mini-facts"><span>现 ${realtime.current ?? "-"}</span><span>开 ${realtime.open ?? "-"}</span><span>量比 ${realtime.volume_ratio ?? "-"}</span></div>
          </section>
          <section class="intraday-decision-panel intraday-reason-panel">
            <span class="decision-step">REASON</span>
            <strong>${escapeHtml(candidate.technical_rating?.label || "等待触发")}</strong>
            <div class="intraday-reason-list">${(events.length ? events : [{title: "关键价位未触发", detail: candidate.pool_status || "继续等待硬规则。"}]).slice(0, 3).map((event) => `<div><b>${escapeHtml(event.title || "事实")}</b><small>${escapeHtml(event.detail || event.action || "-")}</small></div>`).join("")}</div>
          </section>
          <section class="intraday-decision-panel intraday-scenario-panel">
            <span class="decision-step">SCENARIO</span>
            ${scenarios.map((item, index) => `<div class="scenario-line"><b>${String.fromCharCode(65 + index)}</b><span><strong>${escapeHtml(item.title || item.code || "条件")}</strong><small>${escapeHtml(item.action || item.detail || "继续观察")}</small></span></div>`).join("")}
          </section>
          <section class="intraday-decision-panel intraday-evidence-panel">
            <span class="decision-step">EVIDENCE</span>
            ${renderIntradayDecisionChart(candidate)}
            <div class="decision-level-row"><span>支撑 <b>${formatCandidatePrice(plan.support_price)}</b></span><span>压力 <b>${formatCandidatePrice(plan.pressure_price)}</b></span><span>MA8 <b>${formatCandidatePrice(plan.ma8)}</b></span></div>
            <div class="intraday-evidence-facts"><span>开 ${realtime.open ?? "-"}</span><span>高 ${realtime.high ?? "-"}</span><span>低 ${realtime.low ?? "-"}</span><span>成交 ${escapeHtml(formatTradeAmount(realtime.amount))}</span></div>
          </section>
          <section class="intraday-decision-panel intraday-market-panel">
            <span class="decision-step">MARKET</span>
            <div class="market-triple"><span><small>大盘</small><b>${escapeHtml(marketTone)}</b></span><span><small>板块</small><b>${escapeHtml(candidate.sector_name || candidate.industry || "板块待同步")}</b></span><span><small>个股</small><b>${escapeHtml(candidate.technical_rating?.label || "观察")}</b></span></div>
            <div class="market-live-structure"><span>开盘差 <b>${colorPercent(openGap)}</b></span><span>相对强弱 <b>${colorPercent(relativeStrength)}</b></span><span>量比 <b>${escapeHtml(realtime.volume_ratio ?? "-")}</b></span><span>报价 <b>${escapeHtml(intradayQuoteAgeLabel(quoteTime, data.server_time))}</b></span></div>
            ${renderIntradayOrderBook(realtime, data.realtime_capabilities || {})}
            <p>${escapeHtml(br.name || data.benchmark_ticker || "大盘")} ${colorPercent(br.pct_chg)} · ${escapeHtml(gate.gate_reason || "按市场权限执行")}</p>
          </section>
          <section class="intraday-decision-panel intraday-risk-panel">
            <span class="decision-step">RISK</span>
            <strong>止损 ${formatCandidatePrice(plan.stop_loss)}</strong>
            <div class="intraday-risk-grid"><span><small>止损距离</small><b>${formatPercent(stopDistance)}</b></span><span><small>目标距离</small><b>${formatPercent(targetDistance)}</b></span><span><small>盈亏比</small><b>${Number.isFinite(rewardRisk) ? rewardRisk.toFixed(2) : "-"}</b></span><span><small>报价时效</small><b>${escapeHtml(intradayQuoteAgeLabel(quoteTime, data.server_time))}</b></span></div>
            <p>目标 ${formatCandidatePrice(plan.expected_sell_price)} · 数据源 ${escapeHtml(realtime.source || data.realtime_source || "-")} · 跌破后不等待主观修复。</p>
          </section>
          <section class="intraday-decision-panel intraday-timeline-panel">
            <span class="decision-step">DECISION TIMELINE</span>
            <div class="decision-timeline-list">${timeline.length ? timeline.map((event) => {
                const current = event.current || {};
                const previous = event.previous || {};
                return `<div><time>${escapeHtml(String(event.computed_at || "").slice(11, 16))}</time><span><b>${escapeHtml(previous.action_label || "建立基线")} → ${escapeHtml(current.action_label || "等待")}</b><small>${escapeHtml(current.scenario_title || (event.reasons || [])[0] || "决策语义变化")}</small></span></div>`;
            }).join("") : '<p>后台正在建立决策基线；相同判断不会重复记录或推送。</p>'}</div>
          </section>
        </div>
        ${[...(data.realtime_errors || []), ...(data.minute_errors || [])].length ? `<p class="intraday-source-warning">${escapeHtml([...(data.realtime_errors || []), ...(data.minute_errors || [])].join("；"))}</p>` : ""}
      </div>`);
}

// 新版盘中页统一走决策工作区；保留旧渲染函数作为历史兼容，不再作为入口。
function renderIntraday(data) {
    renderIntradayDecisionWorkspace(data);
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
    const strategyId = document.getElementById("stockStrategySelect")?.value || "";
    showHtml("stockPanelResult", '<p class="hint">正在加载单票面板...</p>');
    try {
        const data = await fetchJson(
            `/api/v1/stock/panel?ticker=${encodeURIComponent(ticker)}&date=${encodeURIComponent(date)}&benchmark_ticker=${encodeURIComponent(bm)}&period=5${strategyId ? `&strategy_id=${encodeURIComponent(strategyId)}` : ""}`,
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
    const sync = data.market_sync || {};
    const moneyflow = data.moneyflow || {};
    const summary = data.signal_summary || {};
    const daily = data.daily_bars || [];
    const latestDaily = daily.length ? daily[daily.length - 1].date : "";
    const stale = latestDaily && data.analysis_date && latestDaily < data.analysis_date;
    return `
        <div class="sv-evidence-strip">
          <div><span>分析范围</span><strong>通用行情事实</strong><small>${data.strategy_analysis ? `另叠加 ${escapeHtml(data.strategy_analysis.strategy_name || data.selected_strategy_id)}` : "未应用交易策略"}</small></div>
          <div><span>板块</span><strong>${escapeHtml(basic.industry || "-")}</strong><small>${escapeHtml(basic.market || "Tushare 行业")}</small></div>
          <div><span>资金方向代理</span><strong>${moneyflow.main_net != null ? `${escapeHtml(moneyflow.main_net)} 万元` : "待同步"}</strong><small>${moneyflow.active_passive_available ? "可区分主动/被动" : "不可逐笔区分主动/被动"}</small></div>
          <div><span>通用事件</span><strong>${escapeHtml(summary.recent_count ?? summary.generic_count ?? 0)} 个</strong><small>看涨 ${escapeHtml(summary.bullish_count ?? 0)} · 看跌 ${escapeHtml(summary.bearish_count ?? 0)} · 警示 ${escapeHtml(summary.warning_count ?? 0)}</small></div>
          <div class="${stale ? "is-stale" : ""}"><span>日K水位</span><strong>${escapeHtml(latestDaily || "-")}</strong><small>${stale ? `早于 ${escapeHtml(data.analysis_date)}，图表会补实时点` : "已匹配分析日"}</small></div>
        </div>
      `;
}

function renderStockStrategyWorkspace(data) {
    const analysis = data.strategy_analysis;
    if (!analysis) return "";
    const plan = analysis.trade_plan || {};
    const scores = analysis.scores || {};
    return `
      <section class="sv-strategy-workspace">
        <div class="sv-section-head">
          <span>STRATEGY LAYER · ${escapeHtml(analysis.strategy_id || "-")} / ${escapeHtml(analysis.strategy_version || "-")}</span>
          <h3>${escapeHtml(analysis.strategy_name || "策略分析")}</h3>
          <p>以下买点、目标、止损和建议由所选策略生成，不属于单票通用事实。</p>
        </div>
        <div class="sv-strategy-decision-row">
          <div class="sv-strategy-verdict"><span>策略结论</span><strong>${escapeHtml(analysis.recommendation_label || analysis.recommendation || "-")}</strong></div>
          <div><span>买点</span><strong>${formatCandidatePrice(plan.entry_price)}</strong></div>
          <div><span>目标</span><strong>${formatCandidatePrice(plan.target_price)}</strong></div>
          <div><span>止损</span><strong>${formatCandidatePrice(plan.stop_loss_1)}</strong></div>
          <div><span>盈亏比</span><strong>${escapeHtml(plan.reward_risk_ratio ?? "-")}</strong></div>
        </div>
        <div class="sv-strategy-evidence">
          ${(analysis.evidence || []).map((item, index) => `<p><b>${index + 1}</b>${escapeHtml(item)}</p>`).join("") || "<p>暂无策略证据。</p>"}
          ${Object.keys(scores).length ? `<small>评分：${Object.entries(scores).map(([key, value]) => `${escapeHtml(key)} ${escapeHtml(value ?? "-")}`).join(" · ")}</small>` : ""}
        </div>
      </section>`;
}

function renderStockMoneyflowCard(moneyflow) {
    const data = moneyflow || {};
    if (data.main_net == null) {
        return `<div class="sv-sub-card"><div class="sv-sub-title"><h4>资金流向代理</h4><small class="muted">Tushare moneyflow</small></div><p class="hint">目标日期暂无资金流数据。</p></div>`;
    }
    return `
      <div class="sv-sub-card sv-moneyflow-card">
        <div class="sv-sub-title"><h4>资金流向代理</h4><small class="muted">按订单规模和买卖方向聚合</small></div>
        <div class="sv-fact-metric-grid">
          <div><span>大/特大单净额</span><strong class="${Number(data.main_net) >= 0 ? "up" : "down"}">${Number(data.main_net) >= 0 ? "+" : ""}${escapeHtml(data.main_net)} 万元</strong></div>
          <div><span>近5日净额</span><strong class="${Number(data.cum_main_net_5d) >= 0 ? "up" : "down"}">${Number(data.cum_main_net_5d) >= 0 ? "+" : ""}${escapeHtml(data.cum_main_net_5d ?? "-")} 万元</strong></div>
          <div><span>当日大单方向</span><strong>${Number(data.main_net) > 0 ? "净流入" : Number(data.main_net) < 0 ? "净流出" : "平衡"}</strong></div>
          <div><span>近5日净流入天数</span><strong>${escapeHtml(data.main_active_persist_days_5d ?? 0)} / 5</strong></div>
        </div>
        <p class="sv-method-note">口径说明：${escapeHtml(data.methodology_note || "该数据不等同于逐笔主动/被动成交。")} 买卖侧金额不展示，也不进入主动性评分。</p>
      </div>`;
}

function renderStockPanel(data) {
    const basic = data.basic || {};
    const rt = data.realtime || {};
    const sync = data.market_sync || {};
    const pctChgCls = stockDirectionClass(rt.pct_chg);

    const html = `
        <div class="sv-page sv-ai-terminal">
          <div class="sv-header sv-neutral-header">
            <div class="sv-title-block sv-stock-compact-card">
              <div class="sv-name-row">
                ${stockLogoMarkup(data.ticker, basic.name || rt.name, "sv-stock-logo")}
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
            <aside class="sv-fact-scope-card">
              <span>RESEARCH SCOPE</span>
              <strong>${data.strategy_analysis ? "事实与策略分栏展示" : "策略中立的单票事实"}</strong>
              <p>行情、资金和通用事件不会直接生成买卖建议。${data.strategy_analysis ? "交易计划仅来自下方已选择策略。" : "如需买卖点，请在顶部选择策略后重新分析。"}</p>
            </aside>
          </div>
          ${renderStockEvidenceStrip(data)}
          ${renderStockQuoteStrip(rt, basic, sync)}

          <div class="sv-body">
            <div class="sv-container-left">
              <div class="sv-left-title">
                <div>
                  <span class="feature-kicker">PRICE & GENERIC EVENTS</span>
                  <h3>K 线与通用事件</h3>
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

          ${renderStockStrategyWorkspace(data)}

          <section class="sv-section-block">
            <div class="sv-section-head">
              <span>MARKET FACTS</span>
              <h3>资金与市场关系</h3>
              <p>这些内容用于描述事实，不直接输出买点、目标价或仓位建议。</p>
            </div>
            <div class="sv-insight-grid sv-insight-grid-primary">
              ${renderStockMoneyflowCard(data.moneyflow)}
              ${renderSyncCard(sync, basic)}
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
            <h3>通用技术事件 <small class="muted">${signals.length}</small></h3>
            <div class="signal-tabs">
              <span class="signal-tab-item">看涨 ${ss.bullish_count ?? 0}</span>
              <span class="signal-tab-item">看跌 ${ss.bearish_count ?? 0}</span>
              <span class="signal-tab-item">警示 ${ss.warning_count ?? 0}</span>
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
    document.getElementById("journalAccountTools")?.addEventListener("toggle", event => {
        if (event.currentTarget.open && !holdingsState.loading) loadHoldings();
    });
    document.querySelectorAll('.sidebar-nav [role="tablist"]').forEach((tabList) => {
        tabList.addEventListener("keydown", handleWorkspaceTabKeydown);
    });
    document.querySelectorAll('.sidebar-nav [role="tab"]').forEach((tab) => {
        tab.tabIndex = tab.getAttribute("aria-selected") === "true" ? 0 : -1;
    });
    document.querySelector(".global-toolbar")?.classList.add("trading-mode");
    startTradingAutoRefresh();
    if (typeof window.loadReviewDashboard === "function") window.loadReviewDashboard();
    const allowFallback = document.getElementById("allowFallback");
    if (allowFallback) allowFallback.addEventListener("change", updateAnalyzeHint);
    document.getElementById("analyzeTicker")?.addEventListener("change", loadDataStatus);
    document.getElementById("globalDate")?.addEventListener("change", loadDataStatus);
    const holdingEntryDate = document.getElementById("holdingEntryDate");
    if (holdingEntryDate && !holdingEntryDate.value) {
        holdingEntryDate.value = document.getElementById("globalDate")?.value || "";
    }
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
    syncSelectionPickedControls();
    // Opening a fresh browser must not replace the shared watchlist with an
    // empty local cache. Explicit pool edits trigger their own synchronization.
});
loadStatus();
loadDataStatus();
loadTushareHealth();
