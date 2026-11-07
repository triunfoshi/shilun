/* Moved research evidence: one dated snapshot, one renderer per destination. */
(() => {
    "use strict";
    const esc = (value) => {
        const text = String(value ?? "");
        if (typeof window.escapeHtml === "function") return window.escapeHtml(text);
        return text.replace(/[&<>'"]/g, (character) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
        })[character]);
    };
    const number = (value, digits = 2) => {
        if (value == null || value === "") return "—";
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN", {maximumFractionDigits: digits}) : "—";
    };
    const percent = (value, digits = 2) => {
        if (value == null || value === "") return "—";
        const parsed = Number(value);
        return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(digits)}%` : "—";
    };
    const sealClock = (value) => {
        if (!value) return "—";
        const text = String(value).replaceAll(":", "").padStart(6, "0");
        return /^([01]\d|2[0-3])[0-5]\d[0-5]\d$/.test(text)
            ? `${text.slice(0, 2)}:${text.slice(2, 4)}:${text.slice(4)}` : "—";
    };
    const signed = (value, suffix = "") => {
        if (value == null || value === "") return "—";
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) return "—";
        return `${parsed > 0 ? "+" : ""}${number(parsed)}${suffix}`;
    };
    const tone = (state) => {
        const code = String(state || "");
        if (["above_expectation", "strong", "attack", "fit", "trend_attack", "ready", "pass", "proven", "resonance"].includes(code)) return "opportunity";
        if (["below_expectation", "logic_reversal", "weak", "defence", "avoid_large_cap", "blocked", "fail", "marketwide_contagion", "high_level_collapse", "counter_index"].includes(code)) return "risk";
        if (["insufficient_samples", "rotation", "unconfirmed", "historical_only", "degraded", "warn", "candidate", "pending", "mixed", "local_theme_retreat"].includes(code)) return "wait";
        if (["consensus", "in_line", "normal", "healthy", "none"].includes(code)) return "info";
        return "neutral";
    };
    const chip = (label, state = "") => `<span class="review-chip is-${tone(state)}">${esc(label || "待确认")}</span>`;
    function expectationBandCell(band) {
        if (!band || band.status !== "ready") return `<span class="review-muted">样本 ${number(band?.sample_size, 0)}</span>`;
        return `<strong>${percent(band.low)} / ${percent(band.base)} / ${percent(band.high)}</strong><small>低 / 平 / 高 · n=${number(band.sample_size, 0)}</small>`;
    }

    function renderExpectations(data) {
        const expectations = data.expectations || {};
        const baselines = expectations.board_type_baselines || {};
        const observations = expectations.observations || [];
        const order = ["one_word", "shrinking_volume", "explosive_volume", "broken_messy", "normal_board"];
        return `<section id="review-step-expectation" class="review-section review-scroll-target">
            <header class="review-section-head"><div><h2>标签溢价与预期管理</h2></div><p>过去20个交易日同标签的Q25 / 中位数 / Q75；按昨日标签验证今日反馈。</p></header>
            <div class="review-table-wrap">
                <table class="review-table review-baseline-table">
                    <thead><tr><th>昨日板型标签</th><th>开盘溢价：低 / 平 / 高</th><th>收盘溢价：低 / 平 / 高</th><th>最高溢价：低 / 平 / 高</th></tr></thead>
                    <tbody>${order.map((key) => {
                        const item = baselines[key] || {};
                        return `<tr><td data-label="板型"><strong>${esc(item.label || key)}</strong></td><td data-label="开盘">${expectationBandCell(item.open)}</td><td data-label="收盘">${expectationBandCell(item.close)}</td><td data-label="最高">${expectationBandCell(item.high)}</td></tr>`;
                    }).join("")}</tbody>
                </table>
            </div>
            <div class="review-expectation-definitions">
                <article><strong>预期一致</strong><p>${esc(expectations.consensus_definition)}</p></article>
                <article><strong>逻辑反转</strong><p>${esc(expectations.logic_reversal_definition)}</p></article>
            </div>
            <h3 class="review-subheading">今日验证 · 昨日涨停反馈</h3>
            <p class="review-source-note">昨日板型与连板标签 · 开盘、最高、最低、收盘均为今日较昨收涨跌幅 · 所属概念与三级行业取上一交易日东方财富统一目录；涨停原因保留为独立归因证据。</p>
            ${observations.length ? `<div class="review-table-wrap review-expectation-scroll" role="region" aria-label="昨日涨停个股今日反馈" tabindex="0"><table class="review-table review-expectation-observations"><thead><tr>${['个股名称','细分行业','所属概念','板型','昨日连板情况','今日是否断板','预期结果','开盘','最高','最低','收盘','炸板次数','封板时间'].map(h=>`<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${observations.map((item) => `<tr>
                <td data-label="个股名称"><strong>${esc(item.name || item.ticker)}</strong><small>${esc(item.ticker)}</small></td>
                <td data-label="细分行业">${esc(item.industry_name || '—')}</td><td data-label="所属概念"><span class="review-topics-line" title="${esc((item.topics || []).join(' · '))}">${esc((item.topics || []).slice(0, 3).join(' · ') || '所选日概念待补齐')}${item.topics?.length > 3 ? ` 等 ${item.topics.length} 项` : ''}</span></td>
                <td data-label="板型">${esc(item.board_type_label)}</td><td data-label="昨日连板情况">${number(item.consecutive_count, 0)}板</td>
                <td data-label="今日是否断板">${item.relimit === true ? '否' : item.relimit === false ? '是' : '—'}</td>
                <td data-label="预期结果">${chip(item.expectation?.label, item.expectation?.state)}</td>
                <td data-label="开盘">${percent(item.open_premium)}</td><td data-label="最高">${percent(item.high_premium)}</td><td data-label="最低">${percent(item.low_premium)}</td><td data-label="收盘">${percent(item.close_premium)}</td>
                <td data-label="炸板次数">${number(item.today_broken_count, 0)}</td><td data-label="封板时间">${item.relimit === false ? '-' : sealClock(item.today_last_seal_time)}</td>
            </tr>`).join('')}</tbody></table></div>` : `<div class="review-empty">当日连板增强数据存在缺口，历史基线保留，但不生成今日个股预期结论。</div>`}

        </section>`;
    }

    function tacticRows(tactic) {
        const items = tactic?.items || [];
        if (!items.length) return `<div class="review-empty">本次快照没有满足该战法硬条件的候选。</div>`;
        return `<div class="review-table-wrap"><table class="review-table review-tactic-table">
            <thead><tr><th>标的</th><th>板块</th><th>角色线索</th><th>进攻/防御</th><th>盘口</th><th>成交额</th><th>净流入</th><th>位置图形</th></tr></thead>
            <tbody>${items.map((item) => `<tr>
                <td data-label="标的"><strong>${esc(item.name || item.ticker)}</strong><small>${esc(item.ticker)}</small></td>
                <td data-label="板块">${esc(item.sector_name)}</td><td data-label="角色">${chip(item.role?.label, item.role?.code === "back_row" ? "unconfirmed" : "in_line")}</td>
                <td data-label="属性">${chip(item.board_nature?.label, item.board_nature?.code)}</td><td data-label="盘口">${chip(item.tape_strength?.label, item.tape_strength?.state)}</td>
                <td data-label="成交额">${number(item.amount_yi)}亿</td><td data-label="净流入" class="${Number(item.net_inflow_yi) >= 0 ? "is-positive" : "is-negative"}">${signed(item.net_inflow_yi, "亿")}</td>
                <td data-label="位置图形"><span class="review-position-chart">${esc(item.position_chart?.market)} × 中期 ${esc(item.position_chart?.sector_cycle)} × ${esc(item.position_chart?.role)}<small>${esc(item.position_chart?.price_shape)}</small>${item.cycle?.diagnostic_status && item.cycle.diagnostic_status !== "matched" ? `<small>${esc(item.cycle.reason)} ${esc(item.cycle.next_action)}</small>` : ""}</span></td>
            </tr>`).join("")}</tbody>
        </table></div>`;
    }

    function renderNewFaces(data) {
        const rows = data.turnover_new_faces || [];
        return `<section id="review-step-new-faces" class="review-section review-scroll-target">
            <p class="review-source-note">${esc(data.analysis_date)} · 行业读取复盘日的东方财富三级行业，不是涨停原因事件题材；同日分类尚未归档时保留待补齐。</p>
            ${rows.length ? `<div class="review-table-wrap"><table class="review-table"><thead><tr><th>标的</th><th>行业</th><th>成交额</th></tr></thead><tbody>${rows.slice(0, 30).map((item) => `<tr><td data-label="标的"><strong>${esc(item.name || item.ticker)}</strong><small>${esc(item.ticker)}</small></td><td data-label="行业">${esc(item.sector_name || '同日行业待补齐')}</td><td data-label="成交额">${number(item.amount_yi)}亿</td></tr>`).join("")}</tbody></table></div>` : `<div class="review-empty">本次快照没有新的20亿成交额标的。</div>`}
        </section>`;
    }


    let facesRequest = 0;
    async function loadNewFaces() {
        const root = document.getElementById('sectorNewFacesRoot');
        const day = document.getElementById('sectorNewFacesDate').value;
        const seq = ++facesRequest;
        const button = document.getElementById('sectorNewFacesLoad');
        button.disabled = true;
        root.setAttribute('aria-busy', 'true');
        root.innerHTML = '<p role="status">正在读取该日容量新晋…</p>';
        try {
            const data = await window.fetchJson(`/api/v1/review/new-faces?date=${encodeURIComponent(day)}`, {timeoutMs:75000});
            if (seq !== facesRequest) return;
            if (data.analysis_date !== day) throw new Error('返回日期不一致，请重试。');
            root.innerHTML = data.status === 'ok' ? renderNewFaces(data) : '<p class="review-empty">该日复盘尚未生成，请在 P1 或 P2 生成对应日期快照。</p>';
        } catch (error) {
            if (seq === facesRequest) root.innerHTML = `<p role="alert">容量名单读取失败：${esc(error.message)}，可点击重新读取。</p>`;
        } finally {
            if (seq === facesRequest) { root.setAttribute('aria-busy','false'); button.disabled=false; }
        }
    }
    window.ResearchEvidence = {
        expectations: facet => facet?.status === 'ok' ? `<div class="review-workspace"><p class="review-source-note">标签反馈日 ${esc(facet.analysis_date)} · 固定20日基线，与上方20/30日策略窗口分别标注。</p>${renderExpectations(facet)}</div>` : '<p class="br-notice">所选日期标签预期尚未生成，请在 P1 / P2 生成该日复盘。</p>',
        candidates: data => `<div class="review-workspace"><header class="review-section-head"><h3>${esc(data.strategy_name || '三战法研究')}</h3><p>${esc(data.analysis_date)} · ${number(data.total_count,0)}个形态候选，展示前30个</p></header><p>${esc(data.rule)}</p><p class="review-source-note">研究候选尚未验证执行收益；此处不生成委托。三战法共用复盘归档和选品入口。</p>${data.status === 'not_materialized' ? '<p class="review-empty">该日快照尚未生成，请先在 P1 / P2 生成复盘。</p>' : tacticRows({items:data.candidates})}</div>`,
        loadNewFaces
    };
})();
