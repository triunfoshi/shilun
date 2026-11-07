/* Vibe-Astock overseas layout: US indices → Magnificent Seven → HK indices. */
(() => {
    "use strict";
    const byId = id => document.getElementById(id);
    const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
    const format = value => typeof value === "number" && Number.isFinite(value)
        ? value.toLocaleString("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2}) : "—";
    let loading = false;
    let loadedAt = 0;
    let latestData = null;

    function snapshotContext(data) {
        const selected = byId("reviewDate")?.value;
        const sessions = [data.us_session ? `美股 ${data.us_session}` : "", data.hk_session ? `港股 ${data.hk_session}` : ""].filter(Boolean);
        const dates = [data.us_session, data.hk_session].filter(Boolean);
        const relation = selected
            ? dates.length && dates.every(day => day === selected)
                ? `复盘日 ${selected}；行情日期与所选日一致，来源仍为最新报价`
                : `所选复盘日 ${selected}；当前显示最新外围，不是该日历史回放`
            : "当前显示最新外围";
        return `${relation}${sessions.length ? ` · ${sessions.join(" / ")}` : " · 供应商未提供可核验的行情日期"}`;
    }

    function updateStatus(data) {
        const status = byId("reviewOverseasStatus");
        if (status) status.textContent = `${snapshotContext(data)} · ${data.source_label || "外围行情"} · 抓取 ${String(data.fetched_at || "时间未提供").replace("T", " ")}${data.partial ? " · 部分行情缺失" : ""}${data.note ? ` · ${data.note}` : ""}`;
    }

    function card(row) {
        const pct = row.change_pct;
        const valid = typeof pct === "number" && Number.isFinite(pct);
        const missing = row.available === false || row.price == null || !Number.isFinite(Number(row.price));
        const tone = !valid || pct === 0 ? "flat" : pct > 0 ? "up" : "down";
        return `<article class="review-overseas-quote"><header><h4 title="${esc(row.name)}">${esc(row.name)}</h4><span>${esc(row.ticker || row.symbol || '代码未提供')}</span></header>
            <strong class="is-${tone}">${missing ? "—" : format(row.price)}</strong>
            <span class="review-overseas-change is-${tone}">${missing ? "数据缺失" : valid ? `${pct > 0 ? "+" : ""}${format(pct)}%` : "涨跌幅待获取"}</span>
            <small>${esc(missing ? "未返回有效报价 · 不参与涨跌统计" : row.session || "行情日期未提供")}${row.source === "eastmoney" ? " · 东财备用" : ""}</small></article>`;
    }

    function render(data) {
        const indices = data.indices || [];
        const group = (title, label, rows, kind) => `<section class="review-overseas-group"><header><h3>${title}</h3><span>${esc(label || (rows.length ? "供应商未提供可核验的行情日期" : "暂无可核验报价"))}</span></header><div class="review-overseas-grid is-${kind}">${rows.length ? rows.map(card).join("") : '<p class="review-empty">行情暂不可用，稍后可点击“刷新外围”重试。</p>'}</div></section>`;
        const actualSessionLabel = (region, label, session) => {
            if (label) return label;
            const days = [...new Set(indices.filter(row => row.region === region).map(row => row.session).filter(Boolean))];
            const actual = session || (days.length === 1 ? days[0] : null);
            return actual ? `${region} · ${actual}（报价所属日）` : null;
        };
        const aiRows = [...(data.ai_hard_tech || [])];
        if (data.ai_index) aiRows.push(data.ai_index);
        return group("美股三大指数", actualSessionLabel("美股", data.us_label, data.us_session), indices.filter(row => row.region === "美股"), "indices")
            + group("美股七姐妹", "权重股带指数走，看它们比看指数细", data.mag7 || [], "mag7")
            + group("AI 硬科技", "算力、光模块与半导体产业链 · 最后看 SOXQ 行业指数", aiRows, "ai")
            + group("港股指数", actualSessionLabel("港股", data.hk_label, data.hk_session), indices.filter(row => row.region === "港股"), "hongkong");
    }

    async function loadReviewOverseas(options = {}) {
        const root = byId("reviewOverseasRoot");
        if (!root) return;
        if (latestData) updateStatus(latestData);
        if (loading || (!options.force && Date.now() - loadedAt < 60000)) return;
        const button = byId("reviewOverseasRefresh");
        const status = byId("reviewOverseasStatus");
        loading = true;
        root.setAttribute("aria-busy", "true");
        if (button) { button.disabled = true; button.textContent = "读取中…"; }
        if (status) status.textContent = `正在读取最新外围行情${byId("reviewDate")?.value ? `；所选复盘日 ${byId("reviewDate").value} 不会作为历史报价日期` : ""}…`;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 30000);
        try {
            const response = await fetch("/api/v1/review/overseas", {signal: controller.signal});
            if (!response.ok) throw new Error(`行情接口暂不可用（HTTP ${response.status}）`);
            const data = await response.json();
            if (!data.available) throw new Error(data.reason || "外围行情暂不可用");
            root.innerHTML = render(data);
            latestData = data;
            loadedAt = Date.now();
            updateStatus(data);
        } catch (error) {
            // Old quotes must not remain visible with a new refresh timestamp.
            latestData = null;
            loadedAt = 0;
            root.innerHTML = render({});
            if (status) status.textContent = `${error.name === "AbortError" ? "外围行情读取超时" : error.message}。上方指数复盘不受影响，可稍后刷新外围。`;
        } finally {
            clearTimeout(timer);
            loading = false;
            root.setAttribute("aria-busy", "false");
            if (button) { button.disabled = false; button.textContent = "刷新外围"; }
        }
    }
    window.loadReviewOverseas = loadReviewOverseas;
})();
