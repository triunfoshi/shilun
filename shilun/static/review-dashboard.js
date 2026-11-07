(() => {
    "use strict";

    // Each page owns its date, request state and root; both read the same snapshot.
    function createWorkspace(prefix) {
    const isIndex = prefix === "review";
    const elementId = (id) => id.replace(/^review/, prefix);
    const handlerName = (name) => isIndex ? name : name.replace("Review", "Sentiment");
    let loadedRequestDate = "";
    let loading = false;

    const byId = (id) => document.getElementById(elementId(id));
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
    const list = (items) => (items || []).map((item) => `<li>${esc(item)}</li>`).join("");

    function requestDate() {
        return byId("reviewDate")?.value || "";
    }

    function validDate() {
        const field = byId("reviewDate");
        const valid = !field?.validity || field.validity.valid;
        field?.setAttribute("aria-invalid", valid ? "false" : "true");
        if (!valid) {
            setNotice("复盘日期无效，请从日历选择一个完整日期。", "error");
            if (byId("reviewDateHint")) byId("reviewDateHint").textContent = "日期无效，请重新选择。";
        }
        return valid;
    }

    function renderDateContext(data, selectedDate) {
        const actual = data.analysis_date || "";
        if (!selectedDate && actual && byId("reviewDate")) byId("reviewDate").value = actual;
        const hint = byId("reviewDateHint");
        if (hint) hint.textContent = selectedDate && actual && selectedDate !== actual
            ? `所选 ${selectedDate} 暂无完整收盘数据，实际展示 ${actual}；如需所选日，请先同步该日数据。`
            : `当前复盘日 ${actual || "待确认"}；切换日期读取历史快照，生成按钮按所选日期执行。`;
        const status = byId("reviewFreshnessText");
        if (status && data.status === "not_materialized") status.textContent = `复盘日 ${actual || "待确认"} · 尚未生成`;
    }

    function setBusy(value, label = "") {
        loading = value;
        if (value && !isIndex) syncMarketMood(null);
        const root = byId("reviewDashboardRoot");
        const bar = byId("reviewCommandBar");
        if (bar) {
            bar.setAttribute("aria-busy", value ? "true" : "false");
            if (value) bar.dataset.state = "loading";
            else if (bar.dataset.state === "loading") bar.dataset.state = "default";
        }
        if (root) root.setAttribute("aria-busy", value ? "true" : "false");
        for (const id of ["reviewBuildButton", "reviewEmptyBuildButton", "reviewReadButton", "reviewLatestButton", "reviewFuturesSyncButton"]) {
            const button = byId(id);
            if (!button) continue;
            button.disabled = value;
            button.setAttribute("aria-disabled", value ? "true" : "false");
            if (id === "reviewBuildButton" || id === "reviewEmptyBuildButton") {
                button.textContent = value ? (label || "正在生成...") : "生成所选日复盘";
            }
        }
    }

    function setNotice(message, kind = "info") {
        const node = byId("reviewNotice");
        if (!node) return;
        node.className = `review-notice${message ? " is-visible" : ""} is-${kind}`;
        node.textContent = message || "";
        const bar = byId("reviewCommandBar");
        if (bar && ["error", "success"].includes(kind)) bar.dataset.state = kind;
    }

    async function jsonRequest(url, options = {}) {
        const response = await fetch(url, options);
        let payload = null;
        try {
            payload = await response.json();
        } catch (_error) {
            payload = null;
        }
        if (!response.ok) {
            const detail = typeof payload?.detail === "string" ? payload.detail : "";
            throw new Error(detail || (response.status === 422
                ? "日期格式无效，请从日历重新选择。"
                : `服务暂时未能完成请求（HTTP ${response.status}），请稍后重试。`));
        }
        return payload;
    }

    function renderFreshness(data) {
        const freshness = data.freshness || {};
        const alerts = freshness.alerts || [];
        const generationAlerts = alerts.filter(item => String(item).includes('上游补数不可用'));
        const currentAlerts = alerts.filter(item => !generationAlerts.includes(item));
        const tasks = data.pipeline_status?.tasks || [];
        const pendingTasks = tasks.filter(task => !['success', 'degraded'].includes(task.status));
        const taskNames = {daily_sync:'基础同步', strong_stocks:'核心资格分析', review:'盘后复盘', theme_cycles:'题材周期',
            trade_calendar:'交易日历', daily_bars:'当日日线', previous_daily_bars:'昨日日线', benchmark_bars:'指数行情',
            daily_basic:'市值与换手', moneyflow:'个股资金流', price_limits:'涨跌停价', industry_taxonomy:'行业分类',
            board_pools:'涨停与炸板池', limit_reasons:'同日涨停原因', previous_limit_pool:'昨日涨停反馈',
            cffex_positions:'期货机构持仓', review_history:'复盘历史行情', board_history:'历史板池', price_history:'历史涨跌停价',
            index_history:'指数历史', core_history:'核心历史输入', theme_history:'题材历史输入', first_board_auction:'首板竞价',
            suspensions:'停复牌', board_cohort_prices:'固定样本价格', heat_summary:'本地热度归档', lhb:'龙虎榜'};
        const taskName = stage => String(stage || '').split(':').map(part => taskNames[part] || part).filter(part => !['dependency', 'repair'].includes(part)).join(' · ');
        const taskState = {pending:'待执行', running:'正在处理', waiting:'等待依赖', retry:'等待重试', failed:'重试已停止，需核查'};
        const quality = data.data_quality || {};
        const checks = quality.checks || [];
        const issues = checks.filter(item => !["pass", "ready"].includes(item.status));
        const status = byId("reviewFreshnessText");
        if (status) status.textContent = `复盘日 ${data.analysis_date || "—"} · 生成 ${String(data.generated_at || "—").replace("T", " ")}`;
        if (!checks.length && !alerts.length && !tasks.length) return "";
        const readable = value => String(value || "").replaceAll("previous_limit_pool", "前一交易日涨停样本");
        return `<details class="review-data-status${issues.length || currentAlerts.length || pendingTasks.length ? " has-gaps" : ""}">
            <summary><strong>数据状态</strong><span>${esc(quality.label || "快照来源核验")}</span>${currentAlerts.length ? `<span>${currentAlerts.length}项当前提醒</span>` : ""}${pendingTasks.length ? `<span>${pendingTasks.length}项同步待处理</span>` : ''}<small>展开核对</small></summary>
            <div class="review-data-status-body">
                ${currentAlerts.length ? `<ul class="review-data-alerts">${currentAlerts.map(item => `<li>${esc(readable(item))}</li>`).join("")}</ul>` : ""}
                ${generationAlerts.length ? `<details><summary>生成时补数记录 · ${esc(String(data.generated_at || '时间未记录').replace('T', ' '))}</summary><p>以下记录生成快照时的失败尝试；不表示当前所有来源仍然缺失，当前覆盖见下方核验。</p><ul class="review-data-alerts">${generationAlerts.map(item => `<li>${esc(readable(item))}</li>`).join('')}</ul></details>` : ''}
                ${pendingTasks.length ? `<details><summary>同步进度 · ${pendingTasks.length}项待处理</summary><dl>${pendingTasks.map(task => `<div class="is-gap"><dt>${esc(taskName(task.stage))}</dt><dd>${esc(taskState[task.status] || task.status)}${task.attempts ? ` · 已尝试${number(task.attempts, 0)}/${number(task.max_attempts || 5, 0)}次` : ''}${task.error_message ? `<small>${esc(task.error_message)}</small>` : ''}${task.blocked_by?.length ? `<small>等待：${esc(task.blocked_by.map(taskName).join('、'))}</small>` : ''}${task.next_retry_at ? `<small>下次重试：${esc(new Date(task.next_retry_at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}))}</small>` : ''}</dd></div>`).join('')}</dl><p>每5分钟检查已到重试时间的缺失项；已完成的来源会复用。</p></details>` : ''}
                <dl>${checks.map(item => {
                    const observed = item.coverage != null ? `覆盖 ${percent(item.coverage, 1)}` : item.lag_days != null ? `距复盘基准 ${number(item.lag_days, 0)} 天` : String(item.observed || "待核验");
                    return `<div class="${["pass", "ready"].includes(item.status) ? "" : "is-gap"}"><dt>${esc(item.label)}</dt><dd>${esc(observed)}${item.impact ? `<small>${esc(item.impact)}</small>` : ""}</dd></div>`;
                }).join("")}</dl>
                <p>按所选复盘日核验来源与覆盖；缺失项不补为0。${quality.grain ? esc(quality.grain) : ""}</p>
            </div>
        </details>`;
    }

    function renderIndexRecap(data) {
        const market = data.market || {};
        const indices = market.indices || [];
        const available = indices.filter(item => item.status !== "missing");
        const rising = available.filter(item => Number(item.pct_chg) > 0).length;
        const aboveMa5 = available.filter(item => item.above_ma5).length;
        return `<div class="review-index-recap" aria-label="指数复盘事实">
            <header class="review-index-recap-head"><div><span>主要指数 · ${esc(data.analysis_date || "待确认")}</span><strong>${rising}/${available.length} 上涨 · ${aboveMa5}/${available.length} 站上 MA5</strong></div></header>
            <div class="review-index-recap-grid">${indices.map(item => {
                if (item.status === "missing") return `<article class="review-index-recap-card is-missing"><span>${esc(item.name)}</span><strong>数据缺失</strong><small>不参与姿态判断</small></article>`;
                const cls = item.pct_chg == null ? "" : Number(item.pct_chg) > 0 ? "is-positive" : Number(item.pct_chg) < 0 ? "is-negative" : "is-flat";
                return `<article class="review-index-recap-card">
                    <div class="review-index-card-title"><span>${esc(item.name)}</span><span class="review-index-position">${item.above_ma5 == null ? "均线待核验" : item.above_ma5 ? "MA5上方" : "MA5下方"}</span></div>
                    <strong class="review-index-close ${cls}">${number(item.close)}</strong>
                    <div class="review-index-change ${cls}">${Number(item.pct_chg) > 0 ? "+" : ""}${percent(item.pct_chg)}</div>
                    <dl class="review-index-ohlc"><div><dt>开盘</dt><dd>${number(item.open)}</dd></div><div><dt>最高</dt><dd>${number(item.high)}</dd></div><div><dt>最低</dt><dd>${number(item.low)}</dd></div><div><dt>振幅</dt><dd>${percent(item.amplitude)}</dd></div></dl>
                    <p class="review-index-structure">${esc(item.trend_stage || "结构待生成")}<span>MA20 ${number(item.ma20)}</span></p>
                    <p class="review-index-levels">支撑 ${number(item.support_1)}<span>压力 ${number(item.pressure_1)}</span></p>
                </article>`;
            }).join("")}</div>
        </div>`;
    }

    function renderAction(data) {
        const action = data.action || {};
        const posture = data.market?.posture || {};
        return `<section id="review-step-conclusion" class="review-section review-action-section review-scroll-target" aria-label="指数复盘">
            <header class="review-section-head"><div><h2><span class="review-section-number">01</span>指数复盘</h2></div><p>指数事实、市场姿态与次日动作边界。</p></header>
            <article class="review-action-main">
                <div class="review-action-title">
                    <div class="review-action-verdict"><h3>${esc(action.headline || "市场状态待确认")}</h3><p>${esc(posture.cap_scope || posture.doctrine || "市值边界待确认")}</p></div>
                    <div class="review-posture-evidence"><span>判断依据</span><ul>${(posture.reasons || []).slice(0,4).map(item => `<li>${esc(item)}</li>`).join("") || "<li>指数、量能与广度数据待确认</li>"}</ul><p>较昨增量反映成交变化，总成交额反映容量；放量与低量能可以同时成立。</p></div>
                </div>
                ${renderIndexRecap(data)}
            </article>
        </section>`;
    }

    function validChartValue(value) {
        return (typeof value === "number" || typeof value === "string" && value.trim() !== "") && Number.isFinite(Number(value)) && Number(value) >= 0;
    }

    function volumeChart(volume, windowSize = 60) {
        const series=(volume?.series || []).slice(-windowSize), valid=validChartValue;
        const values=series.filter(r=>valid(r.amount_yi)).map(r=>Number(r.amount_yi));
        const refs=[{value:volume.ma20_yi,label:"20日均",key:"ma20"},{value:volume.ma60_yi,label:"60日均",key:"ma60"},{value:20000,label:"2万亿",key:"baseline"}].filter(r=>valid(r.value)).map(r=>({...r,value:Number(r.value)}));
        const last=series.at(-1);
        const periods=`<div class="review-volume-periods" role="group" aria-label="成交额显示范围">${[5,10,20,60].map(n=>`<button type="button" data-volume-window="${n}" aria-pressed="${n===windowSize}" ${!series.length?"disabled":""}>${n}日</button>`).join("")}</div>`;
        const title=`<figcaption class="review-volume-toolbar"><div><strong>全市场成交额（亿元）</strong><span>${esc(last?.date || "日期待确认")}${valid(last?.amount_yi)?` · 最新 ${number(last.amount_yi)}亿`:""}</span></div>${periods}</figcaption>`;
        const daily=series.length?`<details class="review-volume-data"><summary>每日数据</summary><div class="review-table-wrap"><table class="review-table"><caption>全市场成交额 · 亿元</caption><thead><tr><th>交易日</th><th>成交额</th></tr></thead><tbody>${series.map(r=>`<tr><td data-label="交易日">${esc(r.date)}</td><td data-label="成交额">${valid(r.amount_yi)?number(r.amount_yi):"—"}</td></tr>`).join("")}</tbody></table></div></details>`:"";
        if(values.length<2)return `<figure class="review-volume-chart">${title}<p class="review-empty">成交额有效数据不足，暂不绘图。</p>${daily}</figure>`;
        const high=Math.max(...values,...refs.map(r=>r.value)),rough=high/6, magnitude=10**Math.floor(Math.log10(rough || 1));
        const step=[1,2,2.5,5,10].map(n=>n*magnitude).find(n=>n>=rough)||magnitude*10;
        const maximum=Math.max(step,Math.ceil(high/step)*step),w=1000,h=240,slot=w/series.length;
        const barWidth=Math.min(8,slot*.56), y=value=>(maximum-value)/maximum*h;
        const ticks=[];for(let v=0;v<=maximum+.01*step;v+=step)ticks.push(v);
        const grid=ticks.map(v=>`<line class="review-volume-grid" x1="0" y1="${y(v)}" x2="${w}" y2="${y(v)}"/>`).join("");
        const bars=series.map((r,i)=>valid(r.amount_yi)?`<rect class="review-volume-bar" x="${(i+.5)*slot-barWidth/2}" y="${y(Number(r.amount_yi))}" width="${barWidth}" height="${h-y(Number(r.amount_yi))}"><title>${esc(r.date)} · ${number(r.amount_yi)}亿元</title></rect>`:"").join("");
        const rules=refs.map(r=>`<line class="review-volume-rule is-${r.key}" x1="0" x2="${w}" y1="${y(r.value)}" y2="${y(r.value)}"/>`).join("");
        const dateIndices=[...new Set(Array.from({length:Math.min(8,series.length)},(_,i)=>Math.round(i*(series.length-1)/(Math.min(8,series.length)-1))))];
        let lastLabel=-10;
        const referenceLabels=[...refs].sort((a,b)=>b.value-a.value).map(r=>{
            const at=y(r.value)/h*100,label=Math.min(94,Math.max(5,at,lastLabel+9));lastLabel=label;
            return `<span class="review-volume-ref-name is-${r.key}" style="top:${label}%">${esc(r.label)}</span><svg class="review-volume-ref-leader is-${r.key}" viewBox="0 0 24 100" preserveAspectRatio="none" aria-hidden="true"><path d="M0 ${at} L8 ${at} L20 ${label}"/></svg>`;
        }).join("");
        return `<figure class="review-volume-chart" aria-label="全市场成交额柱状趋势，单位亿元">${title}
            <ul class="review-volume-references" aria-label="参考线">${refs.map(r=>`<li class="is-${r.key}"><i></i>${esc(r.label)} <b>${number(r.value)}亿</b></li>`).join("")}</ul>
            ${!valid(volume.ma20_yi)||!valid(volume.ma60_yi)?'<p class="review-volume-gap">'+(!valid(volume.ma20_yi)?'20日均':'60日均')+'历史窗口未齐，暂不画该参考线。</p>':''}
            <div class="review-volume-plot"><div class="review-volume-axis" aria-hidden="true">${ticks.map(v=>`<span style="top:${y(v)/h*100}%">${number(v,0)}</span>`).join("")}</div><div class="review-volume-canvas" tabindex="0" role="group" aria-label="成交额图，方向键逐日查看，Home和End跳至首末日" data-volume-selected="${series.length-1}"><svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">${grid}${bars}${rules}</svg><div class="review-volume-cursor" hidden></div></div><div class="review-volume-ref-labels" aria-hidden="true">${referenceLabels}</div></div>
            <div class="review-volume-dates">${dateIndices.map(i=>`<time style="left:${(i+.5)/series.length*100}%">${esc(series[i].date.slice(5))}</time>`).join("")}</div>
            <p class="review-volume-readout" role="status"><time data-volume-date>${esc(last.date)}</time><strong data-volume-value>${valid(last.amount_yi)?number(last.amount_yi):"数据缺失"}</strong><span data-volume-unit>${valid(last.amount_yi)?"亿元":""}</span><small>共${series.length}个交易日 · 均线截至复盘日</small></p>
            ${values.length!==series.length?`<p class="review-volume-gap">缺 ${series.length-values.length} 日成交额，缺口留空。</p>`:""}${daily}</figure>`;
    }

    function bindVolumeChart(volume, windowSize = 60) {
        const widget=byId("reviewVolumeWidget");if(!widget)return;
        const series=(volume?.series || []).slice(-windowSize);
        widget.querySelectorAll('[data-volume-window]').forEach(button=>button.addEventListener('click',()=>{
            const size=Number(button.dataset.volumeWindow);if(size===windowSize)return;
            const expanded=widget.querySelector('details')?.open;
            widget.innerHTML=volumeChart(volume,size);bindVolumeChart(volume,size);
            if(expanded&&widget.querySelector('details'))widget.querySelector('details').open=true;
            widget.querySelector(`[data-volume-window="${size}"]`)?.focus({preventScroll:true});
        }));
        const canvas=widget.querySelector('.review-volume-canvas');if(!canvas||!series.length)return;
        const cursor=widget.querySelector('.review-volume-cursor');let selected=series.length-1;
        const select=index=>{
            const day=Math.max(0,Math.min(series.length-1,Math.round(index)));if(!Number.isFinite(day))return;
            selected=day;const row=series[day],ok=validChartValue(row.amount_yi);
            widget.querySelector('[data-volume-date]').textContent=row.date;
            widget.querySelector('[data-volume-value]').textContent=ok?number(row.amount_yi):'数据缺失';
            widget.querySelector('[data-volume-unit]').textContent=ok?'亿元':'';
            canvas.dataset.volumeSelected=String(day);
            if(cursor){cursor.hidden=false;cursor.style.left=`${(day+.5)/series.length*100}%`;}
        };
        const pointer=event=>{const r=canvas.getBoundingClientRect();if(r.width)select(Math.floor((event.clientX-r.left)/r.width*series.length));};
        canvas.addEventListener('pointermove',event=>{if(event.pointerType==='mouse')pointer(event);});
        canvas.addEventListener('click',pointer);
        canvas.addEventListener('pointerleave',()=>{if(cursor)cursor.hidden=true;});
        canvas.addEventListener('keydown',event=>{
            if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
            event.preventDefault();select(event.key==='Home'?0:event.key==='End'?series.length-1:selected+(event.key==='ArrowRight'?1:-1));
        });
    }

    function renderBreadth(breadth, volume = {}, posture = {}) {
        const validCount = value => validChartValue(value) && Number.isInteger(Number(value));
        const categories=[{key:"up",label:"上涨"},{key:"flat",label:"平盘"},{key:"down",label:"下跌"}];
        const complete=categories.every(({key})=>validCount(breadth[`${key}_count`]));
        const total=complete?categories.reduce((n,{key})=>n+Number(breadth[`${key}_count`]),0):null;
        const distribution=breadth.distribution;
        const bins=(distribution?.bins || []).filter(bin=>validCount(bin.count));
        const max=Math.max(1,...bins.map(bin=>Number(bin.count))), tick=Math.max(1,Math.ceil(max/4/10)*10), axisMax=Math.ceil(max/tick)*tick;
        const ticks=Array.from({length:Math.round(axisMax/tick)+1},(_,i)=>i*tick);
        const cls=key=>key==='flat'?'flat':String(key).startsWith('down')||String(key).startsWith('limit_down')?'down':'up';
        const histogram=bins.length?`<div class="review-breadth-histogram" aria-label="个股涨跌幅分布，单位家"><div class="review-histogram-axis" aria-hidden="true">${ticks.map(n=>`<span style="bottom:${n/axisMax*100}%">${number(n,0)}</span>`).join("")}</div><div class="review-histogram-grid" aria-hidden="true">${ticks.map(n=>`<i style="bottom:${n/axisMax*100}%"></i>`).join("")}</div><div class="review-histogram-bars">${bins.map(bin=>`<div class="review-histogram-bin is-${cls(bin.key)}" tabindex="0" role="img" aria-label="${esc(bin.label)}：${number(bin.count,0)}家"><div class="review-histogram-column"><span class="review-histogram-count" style="bottom:${Number(bin.count)/axisMax*100}%">${number(bin.count,0)}</span><i style="height:${Number(bin.count)/axisMax*100}%"></i></div><span class="review-histogram-label">${esc(bin.label)}</span><span class="review-histogram-tip" aria-hidden="true">${esc(bin.label)} · ${number(bin.count,0)}家</span></div>`).join("")}</div></div>`:
            `<p class="review-breadth-gap">${complete?(total===0?"统计家数为 0，暂无涨跌分布。":"本快照仅保存涨跌总家数；重新生成后展示逐档分布。"):"涨跌家数未齐，暂不绘制分布；请核对数据同步状态。"}</p>`;
        const markets=Array.isArray(breadth.markets)&&breadth.markets.length?breadth.markets:[{key:"ALL",name:"全市场",up_count:breadth.up_count,down_count:breadth.down_count,flat_count:breadth.flat_count,amount_yi:volume.current_yi,amount_change_yi:posture.amount_change_yi,expansion_threshold_yi:1000,expanded:posture.expanded_by_1000}];
        const rowHTML=markets.map(row=>`<tr class="${row.key==='ALL'?'review-market-total':''}"><th scope="row">${esc(row.name)}</th>${categories.filter(c=>c.key!=='flat').concat(categories.filter(c=>c.key==='flat')).map(c=>`<td data-label="${c.label}" class="is-${c.key}">${validCount(row[`${c.key}_count`])?number(row[`${c.key}_count`],0):"—"}</td>`).join("")}<td data-label="成交额">${number(row.amount_yi)}<small>亿元</small></td><td data-label="较昨日">${signed(row.amount_change_yi)}<small>亿元</small></td><td data-label="放量门槛">${row.expansion_threshold_yi==null?"待核验":`+${number(row.expansion_threshold_yi)}亿`}</td><td data-label="放量判断">${row.expanded==null?"证据不足":row.expanded?"达到门槛":"未达门槛"}</td></tr>`).join("");
        return `<section class="review-breadth-strip" aria-label="市场广度"><div class="review-breadth-title"><h3>涨跌分布</h3><span>上涨占比 <strong>${percent(breadth.up_ratio,1)}</strong></span>${total!=null?`<small>统计 ${number(total,0)} 家</small>`:""}</div>
            <div class="review-breadth-totals">${categories.map(c=>`<span class="is-${c.key}">${c.label}<b>${validCount(breadth[`${c.key}_count`])?number(breadth[`${c.key}_count`],0):"—"}</b><small>${total?percent(Number(breadth[`${c.key}_count`])/total,1):""}</small></span>`).join("")}</div>
            ${histogram}${distribution?.note?`<p class="review-source-note">${esc(distribution.note)}</p>`:""}
            <div class="review-table-wrap"><table class="review-table review-market-breadth-table"><caption>分市场广度与成交额 · 同口径A股样本</caption><thead><tr><th>市场</th><th>上涨</th><th>下跌</th><th>平盘</th><th>成交额</th><th>较昨日</th><th>放量门槛</th><th>放量判断</th></tr></thead><tbody>${rowHTML}</tbody></table></div>
            <details class="review-breadth-method"><summary>统计口径与放量标准</summary><p>全市场沿用较前一交易日增加至少1000亿元的展示标准。单市场门槛按此前20个有效交易日该市场成交额占全市场的份额分配1000亿；窗口或金额缺失时保持未知。此为研究标记，需结合价格方向理解放量。</p><p>${esc(markets.find(r=>r.coverage_note)?.coverage_note || "旧快照未保存分市场样本，重新生成后补充；不以指数成交额替代个股合计。")}</p><p>${esc(distribution?.note || "涨跌分档须有当日个股收益及涨跌停价证据，旧快照不据总家数推算。")}</p></details>
        </section>`;
    }

    function renderFutures(futures) {
        const history = (futures?.history || []).length ? futures.history :
            (futures?.products?.length ? [{analysis_date: futures.target_date, products: futures.products}] : []);
        if (typeof window.renderFuturesProductTable !== "function") {
            return `<div class="review-empty">尚无期指逐日持仓数据，请到“大盘研究”同步期指席位后重新生成复盘。</div>`;
        }
        const available = history.filter((day) => (day.products || []).some((row) => row.status === "ok")).length;
        const products = ["TOTAL", "IF", "IH", "IC", "IM"];
        const historyNote = history.length
            ? `最近 ${esc(futures?.history_window || 7)} 个交易日 · 已有 ${available} 日数据`
            : "尚无期指逐日持仓数据";
        return `<div class="review-futures-toolbar"><p class="review-source-note">${historyNote} · 多空列是当日未平仓持仓量，不是每日变化累加。合计为 IF / IH / IC / IM 各一个展示合约的手数相加，不是所有到期月份或名义金额；前20席位含客户持仓，不等于机构自营。净空 = 空单 − 多单；日增减使用同合约源端数据，缺项不补零。</p><button id="reviewFuturesSyncButton" type="button" class="secondary" onclick="syncReviewFutures()">同步期指缺口</button></div>
            <div class="review-futures-tabs" role="tablist" aria-label="股指期货品种">
                ${products.map((product, index) => `<button type="button" role="tab" id="review-futures-tab-${product}" aria-controls="review-futures-panel-${product}" aria-selected="${index === 0 ? "true" : "false"}" class="${index === 0 ? "is-active" : ""}" data-review-futures-tab="${product}" onclick="selectReviewFutures('${product}')">${product === 'TOTAL' ? '渠道合计' : product}</button>`).join("")}
            </div>
            <div class="review-futures-panels">${products.map((product, index) => `<section id="review-futures-panel-${product}" role="tabpanel" aria-labelledby="review-futures-tab-${product}" data-review-futures-panel="${product}" ${index === 0 ? "" : "hidden"}>${window.renderFuturesProductTable(product, history)}</section>`).join("")}</div>`;
    }

    function renderMarket(data) {
        const market = data.market || {};
        const posture = market.posture || {};
        const breadth = market.breadth || {};
        const volume = market.volume || {};
        return `<section id="review-step-facts" class="review-section review-scroll-target">
            <header class="review-section-head"><div><h2><span class="review-section-number">02</span>大盘与量能</h2></div><p>成交额节奏、涨跌分布与期指持仓旁证。</p></header>
            <div class="review-market-overview">
                <div class="review-volume-summary">
                    <div class="review-volume-current"><h3>全市场成交额</h3><p><strong>${number(volume.current_yi, 0)}</strong><span>亿元</span></p></div>
                    <dl class="review-volume-facts">
                        <div><dt>MA20</dt><dd>${number(volume.ma20_yi, 0)} <small>亿</small></dd></div>
                        <div><dt>较昨日</dt><dd>${signed(posture.amount_change_yi)} <small>亿</small></dd></div>
                        <div><dt>+1000亿放量</dt><dd>${posture.expanded_by_1000 == null ? "待确认" : posture.expanded_by_1000 ? "成立" : "不成立"}</dd></div>
                    </dl>
                </div>
                <div id="reviewVolumeWidget">${volumeChart(volume)}</div>
                ${renderBreadth(breadth, volume, posture)}
                <p id="reviewMarketFactsStatus" class="review-source-note" role="status">${esc(market.chart_facts_note || "")}</p>
            </div>
            <article class="review-surface review-futures-surface" aria-label="股指期货机构多空日趋势">
                <div class="review-futures-head"><h3>股指期货机构多空</h3><span id="reviewFuturesSyncStatus" aria-live="polite">默认展示渠道合计 · 可切换品种</span></div>
                ${renderFutures(market.futures)}
            </article>
        </section>`;
    }

    function renderPatterns(data) {
        return `<section class="review-section review-scroll-target" id="review-patterns"><header class="review-section-head"><h2><span class="review-section-number">03</span>形态推演</h2><p>先看价格结构，再核对关键位与失效条件。</p></header>
        ${(data.market?.indices || []).filter(item => item.ticker === "000001.SH").map((item, i) => {
            const evidence = item.pattern_evidence || {}, forecast = evidence.pattern_forecast || {};
            if (evidence.status !== 'ready') return `<details class="review-pattern" ${i === 0 ? 'open' : ''}><summary>${esc(item.name)} · 形态待补</summary><p>${esc(evidence.note || '请重新生成该日复盘以补充形态证据。')}</p></details>`;
            const state = typeof window.getWavePresentation === 'function' ? window.getWavePresentation(evidence) : {
                invalid:false, title:'结构待确认', reasoning:'模型解释暂不可用。', violations:[], gaps:[]};
            const levels = (forecast.key_levels_tomorrow || []).filter(v => v.level != null)
                .filter((v,i,items)=>items.findIndex(item=>Number(item.level).toFixed(2)===Number(v.level).toFixed(2))===i);
            return `<details class="review-pattern" ${i === 0 ? 'open' : ''}><summary>${esc(item.name)} · ${esc(forecast.primary_pattern?.display_name || '形态待确认')}</summary>
            <div class="review-pattern-brief ${state.invalid?'is-unconfirmed':''}"><span>结构判断</span><strong>${esc(state.title)}</strong><p>${esc(state.reasoning)}</p></div>
            <p class="review-pattern-description">${esc(forecast.primary_pattern?.description)}</p>
            <div class="review-pattern-next"><strong>次日验证</strong><p>${esc(forecast.tomorrow_guide || '等待支撑、压力和量价证据进一步确认。')}</p></div>
            ${typeof window.renderWaveKlineChart === 'function' ? window.renderWaveKlineChart(evidence) : ''}
            <div class="review-table-wrap"><table class="review-table"><thead><tr><th>关键位</th><th>点位</th><th>确认条件</th></tr></thead><tbody>${levels.map(v => `<tr><td data-label="关键位">${esc(v.label)}</td><td data-label="点位">${number(v.level)}</td><td data-label="确认条件">${esc(v.note)}</td></tr>`).join('')}</tbody></table></div>
            <details class="review-wave-limits" ${state.invalid?'open':''}><summary>判断依据与模型限制</summary>
                ${state.violations.length?`<h4>规则反证</h4><ul>${state.violations.map(v=>`<li>${esc(v)}</li>`).join('')}</ul>`:''}
                <p>形态是有条件的结构假设，模型置信度不代表次日涨跌概率。判断以实际价格、支撑压力与量价验证为准。</p>
                ${state.gaps.length?`<h4>尚缺的证据</h4><ul>${state.gaps.map(v=>`<li>${esc(v)}</li>`).join('')}</ul>`:''}
            </details></details>`;
        }).join('')}</section>`;
    }

    function renderGenealogy(genealogy) {
        const leaders = genealogy?.leaders || [];
        if (!leaders.length) return `<div class="review-empty">最近数据窗口没有可形成谱系的二板以上标的。</div>`;
        return `<div class="review-genealogy">
            ${leaders.slice(0, 12).map((item) => `<article>
                <div><strong>${esc(item.name || item.ticker)}</strong>${chip(`${number(item.latest_board, 0)}板`, item.status === "current" ? "above_expectation" : "insufficient_samples")}</div>
                <span>${esc(item.sector_name)} · ${esc(item.latest_date)}</span>
                <p>${(item.path || []).map((point) => `${esc(point.date.slice(5))} ${number(point.board_level, 0)}板`).join(" → ")}</p>
            </article>`).join("")}
        </div>`;
    }

    function renderHeatHistory(history) {
        const days = history?.days || [];
        const max = Math.max(1, ...days.map(d => Number(d.limit_up) || 0));
        if (!days.length) return '<p class="review-inline-empty">近5日定稿热度尚未生成，请生成所选日复盘。</p>';
        return `<section class="review-heat-history" aria-label="近5天情绪热度"><h4>近5天情绪热度</h4>
            <table><thead><tr><th>日期</th><th>涨停家数</th><th>最高板</th><th>炸板率</th><th>并列最高标</th></tr></thead>
            <tbody>${days.map(d => `<tr><th scope="row" title="${esc(d.date)}">${esc(d.date?.slice(5))}</th>
                <td class="review-heat-bar-cell"><span class="review-heat-bar" style="--heat-width:${Math.max(0, Math.min(100, (Number(d.limit_up) || 0) / max * 100))}%"></span><b>${number(d.limit_up, 0)}</b></td>
                <td>${number(d.highest_consec, 0)}</td><td>${percent(d.broken_rate, 0)}</td><td class="review-heat-names">${(d.leaders || []).map(r => esc(r.name || r.ticker)).join('、') || (d.leaders_complete ? '无' : '名单待补')}</td></tr>`).join('')}</tbody></table>
            <p class="review-source-note">截至所选复盘日 · 炸板率＝未回封炸板 / 全部尝试封板；与日趋势统一为沪深非ST范围。涨停家数与最高板保留原始涨停池范围。</p>
        </section>`;
    }

    function renderLineage(history) {
        const rows = history?.leader_lineage || [];
        return `<details class="review-p2-details review-lineage-history" open><summary>龙头谱系 · 前几天最高标现在怎样</summary>
            <p class="review-source-note">按 Vibe 的每日并列最高板追踪，不等于题材龙头；累计自登顶收盘起算，回撤以其后最高收盘点为基准。</p>
            <div class="review-lineage-ledger">${rows.map(r => {
                const points = (r.series || []).filter(p => p.cum_return != null);
                const values = points.map(p => Number(p.cum_return));
                const low = Math.min(0, ...values), high = Math.max(.001, ...values);
                const line = points.map((p, i) => `${i / Math.max(1, points.length - 1) * 160},${36 - (Number(p.cum_return) - low) / Math.max(.001, high - low) * 32}`).join(' ');
                return `<article><header><strong>${esc(r.name || r.ticker)}</strong>${r.is_current_top ? chip('当前最高标', 'normal') : ''}</header>
                    <small>${esc(r.ticker)} · ${esc(r.theme)} · ${esc(r.appear_date)} 登顶${r.price_basis === 'raw_close_fallback' ? ' · 未复权' : ''}</small>
                    <div class="review-lineage-values"><span>登顶以来 <b>${percent(r.cum_return_since, 2)}</b></span><span>最高 <b>${percent(r.peak_cum_return, 2)}</b></span><span>距高点 <b>${r.drawdown_from_peak === 0 ? '位于高点' : percent(r.drawdown_from_peak, 2)}</b></span></div>
                    ${points.length > 1 && r.complete ? `<svg viewBox="0 0 160 40" role="img" aria-label="${esc(r.name)}登顶后累计收益走势"><polyline points="${line}" /></svg>` : `<p class="review-source-note">${r.complete ? '当日登顶，暂无后续交易日' : `${esc((r.series || []).find(p => p.cum_return == null)?.date || '')} 起行情未齐，不跨缺失日连线`}</p>`}
                </article>`;
            }).join('') || '<p class="review-inline-empty">所选5日窗口没有可核验的最高标名单。</p>'}</div></details>`;
    }

    function renderResonanceEvidence(wave) {
        const relation = wave?.relation || "pending";
        const supplyMove = wave?.supply_change;
        const supplyBase = wave?.supply_vs_baseline;
        const indexMove = wave?.index_change;
        const indexBase = wave?.index_vs_ma20;
        // Frozen snapshots keep the original combined relation. Explain the two
        // time horizons directly from their archived facts without rewriting them.
        const valid = value => typeof value === 'number' && Number.isFinite(value);
        const daily = wave?.daily_relation || (!valid(supplyMove) || !valid(indexMove) ? 'pending' : supplyMove>0&&indexMove>0 ? 'same_up' : supplyMove<0&&indexMove<0 ? 'same_down' : supplyMove*indexMove<0 ? 'opposite' : 'flat');
        const dailyLabel = ({same_up:'当日同涨',same_down:'当日同跌',opposite:'当日供给与指数背离',flat:'当日方向未一致',pending:'当日方向待确认'})[daily] || '当日方向待确认';
        const structureLabel = ({resonance:'20日结构同向',counter_index:'20日结构反向',mixed:'20日结构分歧',pending:'20日结构待确认'})[relation] || '20日结构待确认';
        const supplyContext = !valid(supplyBase) ? '20日供给基准待补齐' : supplyBase>0 ? '涨停供给高于前20日中位数' : supplyBase<0 ? '涨停供给低于前20日中位数' : '涨停供给等于前20日中位数';
        const indexContext = !valid(indexBase) ? '指数MA20证据待补齐' : indexBase<0 ? '指数仍低于MA20' : indexBase>0 ? '指数高于MA20' : '指数位于MA20';
        return `<article class="review-evidence-card is-primary">
            <header><div><span>涨停潮时机</span><h3>${esc(dailyLabel)}</h3></div>${chip(structureLabel, relation)}</header>
            <dl class="review-evidence-metrics">
                <div><dt>涨停供给</dt><dd>${number(wave?.current_count, 0)}<small>家</small></dd><span>较昨日 ${percent(supplyMove, 1)}</span></div>
                <div><dt>20日基准</dt><dd>${number(wave?.baseline_20d, 0)}<small>家</small></dd><span>偏离 ${percent(supplyBase, 1)}</span></div>
                <div><dt>四宽基日涨跌</dt><dd>${percent(indexMove, 2)}</dd><span>距MA20 ${percent(indexBase, 1)}</span></div>
            </dl>
            <p class="review-source-note">20日结构：${esc(supplyContext)}；${esc(indexContext)}。当日同涨或同跌与中期结构分开判断。</p>
            ${wave?.baseline_20d == null ? `<p class="review-baseline-gap">覆盖 ${number(wave?.sample_days, 0)}/20 · 缺 ${esc((wave?.missing_dates || []).map(d => d.slice(5)).join('、') || '历史样本')}。已有样本中位数 ${number(wave?.available_sample_median, 1)}，不冒充20日基准。</p>` : ''}
            <details class="review-p2-details"><summary>共振口径</summary><p>当日方向比较涨停数较昨日变化与四宽基等权日涨跌；20日结构再比较供给基准和指数相对MA20。结构分歧不表示今日一涨一跌。</p><p>${esc(wave?.rule || "只比较涨停供给与指数方向，不混入广度或资金流。")}</p></details>
        </article>`;
    }


    function renderThemeEvidence(evidence, history, analysisDate, archived = false) {
        const themes = evidence?.themes || [];
        const oneTheme = (theme) => {
            const gates = theme.gates || {};
            const gateLabels = {core_qualified: "核心五板资格已核验", two_stocks_two_plus_in_7d: "至少两家2板", capital_improves: "资金同步改善"};
            return `<article class="review-theme-track" aria-label="${esc(theme.name)}周期与承接">
                <div class="review-theme-context">
                    <header><h4>${esc(theme.name)}</h4>${chip(theme.status === "proven" ? "核心与资金条件满足" : "核心条件已满足", theme.status)}</header>
                    ${theme.attention_core ? `<p class="review-source-note">${theme.attention_rank ? `复盘关注 #${number(theme.attention_rank,0)}` : '交叉视角观察'} · 排序参考股：${esc(theme.attention_core.name)} · ${number(theme.attention_core.sessions,0)}日保留涨幅 ${percent(theme.attention_core.return,2)} · 参考股回撤 ${percent(theme.attention_core.drawdown,2)}${theme.attention_core.price_basis === 'raw_close_fallback' ? ' · 未复权' : ''}</p>` : ''}
                    <p class="review-source-note">${esc(theme.source_label || "行业线索 · 待题材交叉验证")}${theme.member_count != null ? ` · 完整成分 ${number(theme.member_count,0)} 家` : ''}</p>
                    ${theme.industry_parents?.length ? `<p class="review-source-note">行业归属：${theme.industry_parents.map(p => esc(p.name)).join(" → ")} → ${esc(theme.name)}</p>` : ""}
                    ${theme.validation_note ? `<p class="review-source-note">${esc(theme.validation_note)}</p>` : ''}<strong class="review-cycle-verdict">${esc(theme.cycle?.label || "周期计算待完成")}</strong>
                    ${theme.cycle?.diagnostic_status && theme.cycle.diagnostic_status !== 'matched' ? `<p class="review-source-note">${esc(theme.cycle.reason)} ${esc(theme.cycle.next_action)}</p>` : ''}
                    <dl class="review-track-facts">
                        <div><dt>今日 / 昨日涨停</dt><dd>${number(theme.current_count, 0)} / ${number(theme.previous_count, 0)}</dd></div>
                        <div><dt>今日首板</dt><dd>${number(theme.first_board_count, 0)} 家</dd></div>
                        <div><dt>本轮最高 / 近7日二板以上</dt><dd>${theme.round?.max_board != null && !theme.round.complete ? '≥' : ''}${number(theme.round?.max_board, 0)} 板 / ${number(theme.two_plus_stock_count_7d, 0)} 家</dd></div>
                        <div><dt>成交额较昨日</dt><dd>${theme.amount_ratio_vs_previous == null ? "—" : percent(theme.amount_ratio_vs_previous - 1, 1)}</dd></div>
                    </dl>
                    <div class="review-theme-leader"><span>题材龙头 · 候选</span>${theme.round?.leaders?.length ? `<strong>${esc(theme.round.leaders[0].name)}</strong><b>启动以来 ${percent(theme.round.leaders[0].startup_return, 2)}</b><small>${esc(theme.round.leaders[0].start_date || '启动日待核验')} 首板起算 · ${theme.round.leaders[0].price_basis === 'raw_close_fallback' ? '未复权' : '昨收收益连乘'} · 高度 ${theme.round.complete ? '' : '≥'}${number(theme.round.leaders[0].max_board, 0)} 板</small>` : '<strong>候选待核验</strong>'}</div>
                    <details class="review-p2-details review-theme-method"><summary>本轮与龙头依据</summary><p>${esc(theme.cycle?.reason || '周期证据待补')}</p><p>${esc(theme.round?.rule || '本轮历史尚未生成。')}</p><p>${esc(theme.round?.leaders?.[0]?.reason || '')}</p><p>连续可核验历史起点 ${esc(theme.round?.window_start || '待确认')} · ${theme.round?.complete ? '已识别静默分轮边界' : '左侧历史未闭合，不能认证完整本轮最高'}。启动收益从首板前收盘起算，含首板当天；不是登顶以来收益。昨收缺失时回退相邻收盘价并标注未复权，除权事件可能影响结果。</p><p>${(theme.round?.leaders || []).map(r => `${esc(r.name)}：高度${theme.round.complete ? '' : '≥'}${number(r.max_board, 0)}板，启动以来${percent(r.startup_return, 2)}`).join('；')}</p></details>
                    <div class="review-gate-list">${Object.entries(gateLabels).map(([key, label]) => `<span class="is-${gates[key] === true ? "met" : "pending"}">${gates[key] === true ? "✓" : gates[key] === false ? "未满足" : "待补"} ${label}</span>`).join("")}</div>
                    <p class="review-source-note">${esc(theme.capital_signal || "资金待补齐")} · ${esc(theme.capital_scope || "统计范围待确认")}${theme.agreement_count ? ` · 一致标签：${esc((theme.agreement_labels || []).join("、"))}` : ""}</p>
                </div>
                ${renderFrontSupport(theme.front_support || {}, theme.name)}
                ${theme.membership_note ? `<p class="review-source-note">${esc(theme.membership_note)}</p>` : ''}
                ${theme.code ? `<details class="review-p2-details"><summary>分钟资金流向</summary><p class="review-source-note">东方财富个股累计主力净流入，按该视角成分汇总；缺失分钟不补零。</p><button type="button" data-flow-code="${esc(theme.code)}" data-flow-date="${esc(evidence.date || analysisDate)}" onclick="loadTopicMinuteFlow(this)">读取分钟曲线</button><div class="review-topic-flow" role="status" aria-live="polite"></div></details>` : ''}
                ${(theme.new_members || []).length ? `<p class="review-source-note">今日新涨停（昨日无涨停活跃记录）：${theme.new_members.map(r => esc(r.name || r.ticker)).join('、')}</p>` : ''}
            </article>`;
        };
        let topicRequest = 0;
        window.selectTopicPerspective = async (code) => {
            const root = document.getElementById('sentimentPerspectiveDetail');
            const theme = themes.find(r => (r.code || r.name) === code);
            const request = ++topicRequest;
            if (!root || !theme) return;
            if (!theme.detail_id) { root.innerHTML = oneTheme(theme); return; }
            root.innerHTML = '<p class="review-inline-empty" role="status">正在读取该题材的完整成分与承接…</p>';
            try {
                const detail = await window.fetchJson('/api/v1/review/topic-detail?detail_id='+encodeURIComponent(theme.detail_id), {timeoutMs:45000});
                if (request !== topicRequest || !root.isConnected) return;
                root.innerHTML = oneTheme({...detail, ...theme, front_support: detail.front_support});
            } catch (error) {
                if (request === topicRequest && root.isConnected) root.innerHTML = `<p class="review-inline-empty" role="alert">${esc(error.message || '题材读取失败')}<button type="button" onclick="selectTopicPerspective(document.getElementById('sentimentPerspectiveSelect').value)">重试</button></p>`;
            }
        };
        return `<section class="review-parallel-themes" aria-label="题材周期与分化">
            <header class="review-p2-subhead"><h3>${archived ? '单日题材归档证据' : '题材周期与分化'}</h3><span>${Math.min(themes.length, 3)} 个${archived ? '视角 · 历史排序' : '方向 · 按复盘关注排序'}</span></header>
            <p class="review-source-note">准入：真实题材、完整成分；有已取得五板资格且近7日仍活跃的核心，并有连板跟随股。${esc(evidence?.data_note || "按同一日期比较各方向，不自动指定唯一主线。")}</p>
            ${evidence.attention_rule ? `<details class="review-p2-details"><summary>排序依据 · 近期核心与主线延续</summary><p>${esc(evidence.attention_rule)}</p></details>` : ''}
            ${evidence.perspective_rule ? `<details class="review-p2-details"><summary>概念题材与细分行业 · 归档筛选来源 · ${number(evidence.shortlist_count,0)}个候选视角</summary><p>${esc(evidence.perspective_rule)}</p>${evidence.attention_rule ? '<p>以上保留当次采集口径，当前展示顺序采用近期核心与主线延续规则。</p>' : ''}<div class="review-table-wrap"><table class="review-table"><thead><tr><th>视角</th><th>成分数</th><th>收益中位数</th><th>上涨占比</th><th>涨停数</th><th>今日强度</th><th>关注顺序</th><th>选择</th></tr></thead><tbody>${themes.map(r=>`<tr><td>${esc(r.name)} · ${esc(r.source_label?.split(' · ')[0] || r.granularity_label || (r.type === 'N' ? '概念题材' : '细分行业'))}</td><td>${number(r.member_count,0)}</td><td>${percent(r.return_median,2)}</td><td>${percent(r.up_ratio,1)}</td><td>${number(r.limit_up_count,0)}</td><td>${number(r.strength_score,1)}</td><td>${number(r.attention_rank,0)}</td><td>${r.selected ? '保留' : `并入${esc(r.overlap_with)}观察`}</td></tr>`).join('')}</tbody></table></div></details>` : ''}
            ${themes.slice(0, 3).map(oneTheme).join("") || `<p class="review-inline-empty">${evidence.status === 'pending' ? `题材快照待重建：${esc(evidence.data_note || '所选日成分或题材证据缺失，请点击顶部“生成所选日复盘”。')}` : '暂无同时具备五板核心、近期跟随活动和完整成分的题材；请查看核心资格与数据覆盖。'}</p>`}
            ${themes.length > 3 ? `<details class="review-p2-details" ontoggle="if(this.open)selectTopicPerspective(document.getElementById('sentimentPerspectiveSelect').value)"><summary>选择其他视角（${themes.length - 3}）</summary><label for="sentimentPerspectiveSelect">概念题材 / 细分行业<select id="sentimentPerspectiveSelect" onchange="selectTopicPerspective(this.value)">${themes.slice(3).map(r=>`<option value="${esc(r.code || r.name)}">${esc(r.name)}${r.type ? ` · ${esc(r.source_label?.split(' · ')[0] || r.granularity_label || (r.type === 'N' ? '概念题材' : '细分行业'))}` : ''}${r.overlap_with ? ` · 并入${esc(r.overlap_with)}观察` : ''}</option>`).join('')}</select></label><div id="sentimentPerspectiveDetail">${themes[3].detail_id ? '' : oneTheme(themes[3])}</div></details>` : ""}
            ${archived ? '' : renderLineage(history)}
        </section>`;
    }

    window.loadTopicMinuteFlow = async (button) => {
        const root = button.nextElementSibling, day = button.dataset.flowDate;
        const params = new URLSearchParams({date:day,code:button.dataset.flowCode});
        button.disabled = true; root.textContent = '正在读取分钟资金流…';
        try {
            let response = await fetch(`/api/v1/review/topic-flow?${params}`);
            let data = await response.json();
            if (!response.ok) throw new Error(data.detail || '分钟流向读取失败');
            const currentDay = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai'}).format(new Date());
            if ((!data.available || Date.now() - Date.parse(data.fetched_at) > 60000) && day === currentDay) {
                const controller = new AbortController(), timer = setTimeout(()=>controller.abort(),180000);
                try { response = await fetch(`/api/v1/review/topic-flow/refresh?${params}`,{method:'POST',signal:controller.signal}); data = await response.json(); }
                finally { clearTimeout(timer); }
                if (!response.ok) throw new Error(data.detail || '分钟流向采集失败');
            }
            if (!data.available) { root.textContent = data.reason || '没有完整分钟数据'; return; }
            const rows = data.rows || [], finite = rows.filter(r=>r.main_net_yuan != null);
            const scale = Math.max(1,...finite.map(r=>Math.abs(r.main_net_yuan))), step = 600 / Math.max(1,rows.length-1);
            let path = '', connected = false;
            rows.forEach((r,i)=>{ if(r.main_net_yuan == null) { connected=false; return; } path+=`${connected?'L':'M'}${(i*step).toFixed(1)},${(70-r.main_net_yuan/scale*60).toFixed(1)} `; connected=true; });
            root.innerHTML = `<p class="review-source-note">${esc(data.source)} · 成分 ${number(data.observed_members,0)}/${number(data.members,0)} · ${esc(data.fetched_at)}</p><svg viewBox="0 0 600 140" role="img" aria-label="累计主力净流入，单位元，缺失分钟断线"><path d="M0,70 H600" stroke="var(--review-line)"/><path d="${path}" fill="none" stroke="var(--review-blue)" stroke-width="2"/></svg><p class="review-source-note">${esc(finite[0]?.time)} — ${esc(finite.at(-1)?.time)} · 最后完整分钟 ${number(finite.at(-1)?.main_net_yuan/1e8,2)} 亿 · 纵轴 ±${number(scale/1e8,2)} 亿</p><p class="review-source-note">${esc(data.semantics)}</p>`;
        } catch(error) { root.textContent = error.name === 'AbortError' ? '采集超时，请稍后重试' : error.message; }
        finally { button.disabled=false; }
    };

    function renderFrontSupport(support, themeName) {
        const rows = support.rows || [];
        const front = rows.filter(row => row.front === true);
        const back = rows.filter(row => row.front === false);
        const unranked = rows.filter(row => row.front == null);
        const statusLabels = {pending: "数据待补", locked: "封死·换手未验证", supported: "有价格承接", weak: "价格承接弱"};
        const table = rows.length ? `<details class="review-p2-details"><summary>全部样本与承接指标（${rows.length}）</summary>
            <div class="review-table-wrap"><table class="review-table review-support-table"><caption>${esc(themeName)} · 截至昨日冻结角色的固定成员</caption>
            <thead><tr><th scope="col">股票 / 昨日板位</th><th scope="col">今日表现</th><th scope="col">高低价差 / 收盘位置</th><th scope="col">成交量较昨 / 开板</th><th scope="col">最后封板 / 判断</th></tr></thead>
            <tbody>${rows.map(row => `<tr>
                <td data-label="股票 / 昨日板位"><strong>${esc(row.name || row.ticker)}</strong><small>${esc(row.ticker)} · ${esc(row.role_label || (row.front == null ? "同梯队" : row.front ? "前排" : "后排"))} · 昨${number(row.boards, 0)}板</small><small>${esc(row.role_reason || "")}</small><small>${esc(row.membership_note || "")}</small>${row.price_evidence ? `<small>截至昨日：10日 ${percent(row.price_evidence.return_10d,1)} · 20日 ${percent(row.price_evidence.return_20d,1)} · 相对分位 ${percent(row.price_evidence.relative_rank,0)} · 成交额 ${number(row.price_evidence.amount_yi,2)}亿 · ${row.price_evidence.price_basis === 'raw_close_fallback' ? '部分昨收缺失，未复权重建' : '昨收收益连乘'}</small>` : ''}</td>
                <td data-label="今日表现">${percent(row.return_1d, 2)}<small>${row.relimit ? `今日${number(row.today_boards, 0)}板` : row.relimit == null ? "当日板池待补" : row.return_1d == null ? "行情待补" : "未再封板"}</small></td>
                <td data-label="高低价差 / 收盘位置">${percent(row.intraday_drawdown, 1)} / ${row.one_price ? "一字" : percent(row.close_position, 0)}</td>
                <td data-label="成交量较昨 / 开板">${row.volume_ratio == null ? "—" : `${number(row.volume_ratio + 1)}×`} / ${number(row.broken_count, 0)}</td>
                <td data-label="最后封板 / 判断">${sealClock(row.seal_time)}<small>${esc(statusLabels[row.status] || "待确认")}</small></td>
            </tr>`).join("")}</tbody></table></div>
            <p class="review-source-note">${esc(support.rule || "承接指标缺失时保留空值。")}</p></details>` : "";
        const roster = (items, title) => `<div><h5>${title}名单 · ${items.length}</h5><ul class="review-front-roster">${items.map(row => `<li><span><b>${esc(row.name || row.ticker)}</b><small>昨${number(row.boards, 0)}板 → ${row.relimit ? `今${number(row.today_boards, 0)}板` : row.relimit == null ? '板池待补' : '未再板'}</small></span><span class="review-roster-return">${percent(row.return_1d, 2)}<small>${esc(statusLabels[row.status] || '待确认')}</small></span></li>`).join('')}</ul>${!items.length ? `<p class="review-inline-empty">无${title}样本${title === '后排' ? '，不计算后排中位数' : ''}</p>` : ''}</div>`;
        return `<section class="review-theme-support" aria-label="${esc(themeName)}前排承接">
            <header class="review-p2-subhead"><h4>${esc(themeName)} · 前排承接</h4>${chip(support.label || "承接待确认", support.status || "pending")}</header>
            <p class="review-source-note">固定样本 ${esc(support.cohort_date || "昨日")} · 前排 ${number(support.front_count, 0)} / 后排 ${number(support.back_count, 0)} 家${unranked.length ? ` · 角色待确认 ${unranked.length} 家` : ''}</p>
            ${support.rank_note ? `<p class="review-source-note">${esc(support.rank_note)}</p>` : ''}
            <dl class="review-track-facts">
                <div><dt>前排中位收益</dt><dd>${percent(support.front_median_return, 2)}</dd></div>
                <div><dt>后排中位收益</dt><dd>${support.back_count === 0 ? '无后排样本' : support.back_median_return == null ? '行情待补' : percent(support.back_median_return, 2)}</dd></div>
                ${unranked.length ? `<div><dt>同梯队中位收益</dt><dd>${percent(support.unranked_median_return, 2)}</dd></div>` : ''}
                <div><dt>前后排收益差（百分点）</dt><dd>${support.back_count === 0 ? '不适用' : number(support.front_back_spread == null ? null : support.front_back_spread * 100, 2)}</dd></div>
                <div><dt>可观察前排承接率</dt><dd>${percent(support.support_rate, 0)}<small>${number(support.supported_count, 0)} / ${number(support.observable_count, 0)}</small></dd></div>
                <div><dt>前排一字封死</dt><dd>${number(support.locked_count, 0)} 家</dd></div>
                <div><dt>前排待补数据</dt><dd>${number(support.missing_count, 0)} 家</dd><small>${support.missing_count === 0 ? '无缺失样本' : '日线或封板证据待补'}</small></div>
                <div><dt>换手晋级 · 一进二 / 二进三</dt><dd>${(support.promotions || []).map(p => p.sample === 0 ? '无样本' : p.rate == null ? '待补' : percent(p.rate, 0)).join(" / ") || "待补"}</dd><small>${(support.promotions || []).map(p => `${p.label} ${p.promoted}/${p.sample}（行情${p.coverage}）`).map(esc).join(" · ")}</small></div>
            </dl>
            <div class="review-cohort-rosters" tabindex="0" aria-label="固定样本名单，可滚动">${roster(front, '前排候选') + roster(back, '后排候选')}${unranked.length ? roster(unranked, '角色待确认') : ''}</div>
            ${table}
        </section>`;
    }

    function renderDownsideRisk(risk) {
        const counts = risk?.position_counts || {};
        const concentration = risk?.theme_concentration || [];
        const stocks = risk?.stocks || [];
        const zero = risk?.limit_down_count === 0 && risk?.count_ready === true;
        const state = risk?.status === "confirmed" ? risk.risk_type : "pending";
        const positionLabels = {high: "高位", middle: "中位", low: "低位", unknown: "未知"};
        return `<section class="review-downside-strip" aria-label="跌停风险定位">
            <header class="review-p2-subhead"><h3>跌停风险定位</h3>${chip(risk?.penalty == null ? "证据待补齐" : risk.penalty ? `扣 ${number(risk.penalty, 0)} 分` : zero ? "无跌停样本" : "未达阈值", state)}</header>
            <strong class="review-cycle-verdict">${esc(risk?.label || "数据不足，暂不判断扩散")}</strong>
            <dl class="review-downside-facts">
                <div><dt>跌停 / 20日基准</dt><dd>${number(risk?.limit_down_count, 0)} / ${number(risk?.baseline_20d, 1)}</dd><small>基准覆盖 ${number(risk?.baseline_sample_days, 0)}/20 日</small></div>
                <div><dt>跌停位置 · 家数</dt><dd class="review-inline-positions">${Object.entries(positionLabels).map(([key, label]) => `<span>${label} <b>${number(counts[key], 0)}</b></span>`).join("")}</dd></div>
                <div><dt>下跌广度</dt><dd>${percent(risk?.down_breadth, 1)}</dd></div>
                <div><dt>跌停集中度 · Top1 / Top3</dt><dd>${zero ? "不适用" : `${percent(risk?.top1_share, 1)} / ${percent(risk?.top3_share, 1)}`}</dd><small>${esc(risk?.concentration_source || "行业代理")}</small></div>
            </dl>
            ${concentration.length ? `<p class="review-source-note">${concentration.map(r => `${esc(r.theme)} ${number(r.count, 0)}家（${percent(r.share, 0)}）`).join(" · ")}</p>` : ""}
            <p class="review-source-note">${esc(risk?.data_note || "当日跌停价尚未核验，无法确认跌停数与高、中、低位分布。")}</p>
            <details class="review-p2-details"><summary>风险口径${stocks.length ? `与样本（${stocks.length}）` : ""}</summary><p>${esc(risk?.rule || "缺少证据时不判零风险。")}</p>
                <dl class="review-risk-rules">${(risk?.state_rules || []).map(r => `<div><dt>${esc(r.label)} · 扣 ${number(r.penalty, 0)} 分${r.code === risk?.risk_type ? ' · 当前命中' : ''}</dt><dd>${esc(r.rule)}</dd></div>`).join('')}</dl>
                ${stocks.length ? `<div class="review-table-wrap review-risk-crosstab" tabindex="0" aria-label="跌停行业与位置交叉表"><table class="review-table"><thead><tr><th>行业</th>${Object.values(positionLabels).map(v=>`<th>${v}</th>`).join('')}</tr></thead><tbody>${[...new Set(stocks.map(r=>r.theme))].map(theme=>`<tr><th scope="row">${esc(theme)}</th>${Object.keys(positionLabels).map(position=>`<td data-label="${positionLabels[position]}">${stocks.filter(r=>r.theme===theme && r.position===position).map(r=>`<span title="${esc(r.position_note || (r.missing_dates || []).join('、'))}">${esc(r.name || r.ticker)}</span>`).join('、') || '—'}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : ""}
            </details>
            <section class="review-theme-clusters"><h3>题材集中跌停</h3><p>${esc(risk?.theme_cluster_note || '题材归属尚未核验，不能把行业集中度直接当成题材。')}</p>
                <p class="review-source-note">事件归因覆盖 ${number(risk?.event_attribution?.covered, 0)} / ${number(risk?.event_attribution?.total, 0)} 家${risk?.event_attribution?.historical ? ` · ${number(risk.event_attribution.historical, 0)} 家沿用已注明日期的历史原因` : ''}</p>
                ${(risk?.event_clusters || []).length ? `<div class="review-table-wrap"><table class="review-table"><thead><tr><th>事件题材</th><th>跌停家数</th><th>跌停股票与归因日</th></tr></thead><tbody>${risk.event_clusters.map(r => `<tr><th scope="row">${esc(r.theme)}</th><td>${number(r.count, 0)}</td><td>${(r.stocks || []).map(stock => `${esc(stock.name || stock.ticker)}（${esc(stock.reason_date || '待归因')}）`).join('、')}</td></tr>`).join('')}</tbody></table></div>` : `<p class="review-source-note">${zero ? '无跌停样本。' : risk?.event_attribution?.covered ? '已覆盖样本中暂未发现同一事件题材至少两家跌停。' : '暂无可核验的事件归因，不能判断题材集中程度。'}</p>`}
                ${risk?.event_attribution?.rows?.length ? `<details class="review-p2-details"><summary>查看个股事件归因 · ${risk.event_attribution.rows.length} 家</summary><div class="review-table-wrap"><table class="review-table"><thead><tr><th>跌停股票</th><th>事件题材</th><th>归因日</th></tr></thead><tbody>${risk.event_attribution.rows.map(stock => `<tr><th scope="row">${esc(stock.name || stock.ticker)}</th><td>${(stock.event_tags || []).map(esc).join('、')}</td><td>${esc(stock.reason_date)}</td></tr>`).join('')}</tbody></table></div></details>` : ''}
                ${risk?.event_attribution?.missing?.length ? `<details class="review-p2-details"><summary>待归因样本 · ${risk.event_attribution.missing.length} 家</summary>${risk.event_attribution.missing.map(stock => `<p>${esc(stock.name || stock.ticker)} · ${esc(stock.missing_reason)}</p>`).join('')}</details>` : ''}
            </section>
        </section>`;
    }

    function renderSentiment(data) {
        const sentiment = data.sentiment || {};
        return `<section id="review-step-sentiment" class="review-section review-scroll-target review-p2-main">
            <header class="review-section-head"><h2>收盘验证</h2><p>同日涨停供给与指数表现</p></header>
            ${renderResonanceEvidence(sentiment.limit_up_wave || {})}
        </section><section id="sentiment-downside-panel" class="review-section" aria-label="跌停风险">
            <header class="review-section-head"><h2>跌停风险</h2><p>风险状态 → 位置分布 → 行业聚集</p></header>
            ${renderDownsideRisk(sentiment.downside_risk || {})}
        </section>`;
    }

    function syncMarketMood(mood) {
        const value = document.getElementById("sentimentMarketMoodValue");
        const score = document.getElementById("sentimentMarketMoodScore");
        if (!value || !score) return;
        value.textContent = mood?.label || "待判断";
        value.dataset.state = mood?.code || "pending";
        score.textContent = mood?.score == null
            ? "数据待补齐"
            : `评分 ${number(mood.score, 0)} / 100${mood.downside_penalty ? ` · 跌停扣 ${number(mood.downside_penalty, 0)}` : ""}`;
        value.title = mood?.reason || "市场情绪状态待确认";
        const stages = document.querySelectorAll?.('#sentimentMarketMood [data-mood-stage]') || [];
        for (const stage of stages) {
            const current = stage.dataset.moodStage === mood?.code;
            stage.classList.toggle('is-current', current);
            if (current) stage.setAttribute('aria-current', 'step');
            else stage.removeAttribute('aria-current');
            const marker = stage.querySelector('.sentiment-stage-current');
            if (marker) marker.hidden = !current;
        }
        const section = document.getElementById('sentimentMarketMood');
        if (section) section.dataset.mood = mood?.code || 'pending';
        const date = document.getElementById('sentimentMarketMoodAsOf');
        if (date) date.textContent = mood?.date || document.getElementById('sentimentDate')?.value || '';
    }

    function renderLiveEmotion(data) {
        const root = byId("reviewLiveEmotionRoot");
        if (!data || data.available !== true) {
            return `<div class="review-live-emotion-empty"><strong>实时打板情绪暂不可用</strong><p>${esc(data?.reason || "行情源尚未返回有效盘中快照")}</p><small>数据失败不按零涨停、零跌停处理；可稍后点击刷新重试。</small></div>`;
        }
        const metrics = [
            ["涨停", number(data.zt_count, 0), data.dt_count == null ? "跌停池待取" : `跌停 ${number(data.dt_count, 0)}`, "is-up"],
            ["跌停", number(data.dt_count, 0), data.downside_scope || (data.dt_count == null ? "跌停池待取" : "东方财富原始板池范围"), "is-down"],
            ["最高连板", `${number(data.max_boards, 0)} 板`, `连板（2板+）${number(data.lianban_count, 0)} 家`, "is-up"],
            ["连板（2板+）", `${number(data.lianban_count, 0)} 家`, "前排接力宽度", "is-up"],
            ["封板率", percent(data.seal_rate, 1), data.zb_count == null ? "炸板池待取" : `封住 ${number(data.zt_count, 0)} / 尝试 ${number((data.zt_count || 0) + (data.zb_count || 0), 0)}`, "is-up"],
            ["炸板率", percent(data.break_rate, 1), data.zb_count == null ? "炸板池待取" : `炸板 ${number(data.zb_count, 0)} 家`, "is-down"],
            ["晋级率", percent(data.promotion_rate, 1), data.promotion_base_label ? esc(data.promotion_base_label) : data.promotion_base_date ? `${esc(data.promotion_base_date)} 涨停样本` : "上一交易日样本待取", "is-up"],
            ["炸板家数", number(data.zb_count, 0), "炸板未回封", "is-down"],
        ];
        const modeLabel = data.snapshot_mode === "settled" ? `最近交易日收盘状态 · ${esc(data.snapshot_date || data.date || "日期待确认")}` : "盘中实时快照";
        return `<div class="review-live-emotion-meta"><span>${modeLabel}</span><span>${esc(data.phase || "连续交易")}</span><span>读取 ${esc(data.as_of || data.fetched_at || "时间待确认")}</span><span>${esc(data.scope_label || "统计范围待确认")}</span></div>
            <div class="review-live-emotion-grid">${metrics.map(([label, value, note, tone]) => `<article class="${tone}"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("")}</div>
            <p class="review-source-note">${esc(data.note || "盘中快照；指标随板池变化，不替代收盘复盘。")}${data.raw_pool_counts && data.raw_pool_counts.zt_count !== data.zt_count ? ` 原始池涨停 ${number(data.raw_pool_counts.zt_count, 0)}，按上述范围纳入 ${number(data.zt_count, 0)}。` : ""}</p>
            ${data.down_stocks?.length ? `<details class="review-p2-details review-down-roster"><summary>查看跌停名单（${data.down_stocks.length}家）</summary>
                <div class="review-table-wrap"><table class="review-table review-down-roster-table"><caption>${esc(data.snapshot_date || data.date || "日期待确认")} · ${data.snapshot_mode === "settled" ? "收盘跌停核验" : "盘中跌停池"} · ${data.down_stocks.length}家</caption><thead><tr><th scope="col">股票</th><th scope="col">实际涨跌幅</th><th scope="col">跌停状态</th><th scope="col">数据依据</th></tr></thead><tbody>${data.down_stocks.map(r=>{
                    const validPct=typeof r.pct === "number" && Number.isFinite(r.pct);
                    const sources={daily_pre_close:"同日日线／前收参考价",previous_session_close:"同日日线／前一交易日收盘",eastmoney_dated_pool:"东方财富同日跌停池"};
                    return `<tr><th scope="row"><strong>${esc(r.name || r.code)}</strong><small>${esc(r.code || r.ticker || "代码待核验")}</small></th><td data-label="实际涨跌幅" class="${validPct && r.pct < 0 ? "review-roster-negative" : ""}">${validPct ? `${number(r.pct,2)}%` : "—"}</td><td data-label="跌停状态">已确认跌停${!validPct ? '<small>实际涨跌幅缺失</small>' : ''}</td><td data-label="数据依据">${esc(validPct ? sources[r.pct_source] || "原始跌停池涨跌幅" : r.pct_missing_reason || "跌停池已确认；涨跌幅字段缺失")}</td></tr>`;
                }).join("")}</tbody></table></div>
                <p>跌停身份和实际涨跌幅分别核验：不同板块、ST及价格取整会产生不同跌幅，不统一填成−10%。收盘名单按同日全市场核验，盘中名单按原始跌停池。</p></details>` : ""}`;
    }

    let liveEmotionLoading = false;
    let liveEmotionLoadedAt = 0;
    let liveEmotionTimer = null;

    async function loadLiveEmotion(options = {}) {
        const root = byId("reviewLiveEmotionRoot");
        if (!root || liveEmotionLoading || (!options.force && Date.now() - liveEmotionLoadedAt < 12000)) return;
        const button = byId("reviewLiveEmotionRefresh");
        const status = byId("reviewLiveEmotionStatus");
        liveEmotionLoading = true;
        root.setAttribute("aria-busy", "true");
        if (button) { button.disabled = true; button.textContent = "读取中…"; }
        if (status) status.textContent = "正在读取最新涨停、炸板、跌停池；失败不会按 0 处理。";
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 30000);
        try {
            const response = await fetch(`/api/v1/review/live-emotion${options.force ? "?force=true" : ""}`, {signal: controller.signal});
            const data = await response.json();
            if (!response.ok) throw new Error(data?.detail || `实时情绪接口暂不可用（HTTP ${response.status}）`);
            root.innerHTML = renderLiveEmotion(data);
            liveEmotionLoadedAt = Date.now();
            if (status) status.textContent = data.available
                ? `${data.source_label || "东方财富实时板池"} · ${String(data.fetched_at || data.as_of || "时间待确认").replace("T", " ")} · ${data.cache_ttl_seconds || 15} 秒缓存`
                : (data.reason || "实时情绪暂不可用");
        } catch (error) {
            root.innerHTML = renderLiveEmotion({available: false, reason: error.name === "AbortError" ? "读取超时" : error.message});
            if (status) status.textContent = "实时打板情绪读取失败；历史收盘复盘不受影响。";
        } finally {
            clearTimeout(timer);
            liveEmotionLoading = false;
            root.setAttribute("aria-busy", "false");
            if (button) { button.disabled = false; button.textContent = "刷新实时"; }
        }
    }

    function startLiveEmotionPolling() {
        if (isIndex || liveEmotionTimer || typeof setInterval !== "function") return;
        liveEmotionTimer = setInterval(() => {
            const panel = document.getElementById("tab-sentiment");
            if (!document.hidden && panel && !panel.hidden) void loadLiveEmotion();
        }, 15000);
    }

    let marketFactsRequest = 0;
    function mergeMarketFacts(data, facts) {
        const day=data.analysis_date;
        if(!facts || facts.analysis_date!==day || facts.status!=="ready" || !facts.market?.volume || !facts.market?.breadth)return null;
        const all=(facts.market.breadth.markets || []).find(row=>row.key==='ALL');
        const breadth={...data.market?.breadth,...facts.market.breadth};
        if(all)for(const key of ['up_count','down_count','flat_count'])breadth[key]=all[key];
        const total=all?all.up_count+all.down_count+all.flat_count:0;
        if(total>0)breadth.up_ratio=all.up_count/total;
        return {...data,market:{...data.market,volume:facts.market.volume,breadth,
            posture:{...data.market?.posture,...(all?{amount_change_yi:all.amount_change_yi,expanded_by_1000:all.expanded}:{})},
            chart_facts_note:`补充统计截至 ${day} · 按复盘日证券范围回看历史；原复盘结论保持其冻结版本。${facts.cache_status==='stale'?' 正在更新补充缓存。':''}`}};
    }
    async function loadMarketFacts(data) {
        const sequence = ++marketFactsRequest;
        const day = data.analysis_date;
        const selection = requestDate();
        if (!/^\d{4}-\d{2}-\d{2}$/.test(day || "")) return;
        const status = byId("reviewMarketFactsStatus");
        if (status) status.textContent = "正在核对同口径成交额与涨跌分档…";
        const controller = typeof AbortController === "function" ? new AbortController() : null;
        const timer = controller && typeof setTimeout === "function" ? setTimeout(() => controller.abort(), 60000) : null;
        try {
            const facts = await jsonRequest(`/api/v1/review/market-facts?date=${encodeURIComponent(day)}`, controller ? {signal:controller.signal} : {});
            if (sequence !== marketFactsRequest || selection !== requestDate()) return;
            if (facts.analysis_date !== day || facts.status !== "ready" || !facts.market?.volume || !facts.market?.breadth) {
                if (status) status.textContent = "补充统计尚未就绪，先保留原快照已有数据。";
                return;
            }
            const section = document.getElementById("review-step-facts");
            if (!section) return;
            const combined=mergeMarketFacts(data,facts);
            if(!combined)return;
            section.outerHTML = renderMarket(combined);
            bindVolumeChart(facts.market.volume);
        } catch (error) {
            if (sequence !== marketFactsRequest || selection !== requestDate()) return;
            if (status) status.textContent = `补充统计${error.name === "AbortError" ? "读取超时" : "暂不可用"}，保留原快照；重新读取可重试。`;
        } finally {
            if (timer !== null) clearTimeout(timer);
        }
    }

    function renderDashboard(data) {
        const root = byId("reviewDashboardRoot");
        if (!root) return;
        root.classList.remove("is-loading");
        ++marketFactsRequest;
        const supplement=data.market_facts_supplement;
        const chartData=isIndex ? mergeMarketFacts(data,supplement) || data : data;
        const sections = isIndex ? [renderAction(data), renderMarket(chartData), renderPatterns(data)] : [
            renderSentiment(data),
        ];
        const freshness = renderFreshness(data);
        const dataStatus = byId("reviewDataStatus");
        if (dataStatus) dataStatus.innerHTML = freshness;
        root.innerHTML = (dataStatus ? "" : freshness) + sections.join("");
        if (!isIndex) syncMarketMood(data.sentiment?.market_mood || {});
        if (isIndex) {
            bindVolumeChart(chartData.market?.volume || {});
            if(!supplement || supplement.cache_status!=='fresh' || chartData===data)void loadMarketFacts(data);
        }
        root.setAttribute("aria-busy", "false");
    }

    function renderNotMaterialized(data) {
        const root = byId("reviewDashboardRoot");
        if (!root) return;
        root.classList.remove("is-loading");
        root.innerHTML = `<div class="review-empty review-empty-primary"><strong>${esc(data.analysis_date || "所选日期")} 尚无复盘快照</strong><p>${esc(data.message || "请手动生成，或等待收盘任务。")}</p><button id="${elementId("reviewEmptyBuildButton")}" type="button" onclick="${handlerName("materializeReviewDashboard")}()">生成所选日复盘</button></div>`;
        root.setAttribute("aria-busy", "false");
    }

    const autoRepairDates = new Set();
    async function loadReviewDashboard(options = {}) {
        if (isIndex && typeof window.loadReviewOverseas === "function") void window.loadReviewOverseas();
        if (!isIndex) {
            startLiveEmotionPolling();
            void loadLiveEmotion(options);
        }
        if (!validDate()) return;
        const date = requestDate();
        if (!isIndex) void window.ThemeCycles?.mount("sentimentThemeCycles", date);
        if (!isIndex && date) void window.SentimentWorkspace?.mount(date, !!options.force);
        if (loading) return;
        let completedDate = date;
        let autoRepair = false;
        if (!options.force && loadedRequestDate === date && byId("reviewDashboardRoot")?.dataset.loaded === "true") return;
        setBusy(true, "正在读取...");
        setNotice("");
        try {
            const query = date ? `?date=${encodeURIComponent(date)}` : "";
            const data = await jsonRequest(`/api/v1/review/dashboard${query}`);
            if (date !== requestDate()) return;
            renderDateContext(data, date);
            if (data.status === "not_materialized") renderNotMaterialized(data);
            else renderDashboard(data);
            if (!isIndex && !date) void window.ThemeCycles?.mount("sentimentThemeCycles", requestDate());
            if (!isIndex && !date) void window.SentimentWorkspace?.mount(requestDate(), !!options.force);
            completedDate = requestDate();
            loadedRequestDate = requestDate();
            if (byId("reviewDashboardRoot")) byId("reviewDashboardRoot").dataset.loaded = "true";
            const wave = data.sentiment?.limit_up_wave;
            if (!isIndex && (data.status === 'not_materialized' || (wave && wave.baseline_20d == null))) {
                const repairKey = requestDate();
                if (!autoRepairDates.has(repairKey)) {
                    autoRepairDates.add(repairKey);
                    autoRepair = true;
                }
            }
        } catch (error) {
            if (date !== requestDate()) return;
            setNotice(`复盘快照读取失败：${error.message}`, "error");
            const root = byId("reviewDashboardRoot");
            if (root) {
                root.dataset.loaded = "false";
                root.innerHTML = `<div class="review-empty review-empty-primary"><strong>暂时无法读取复盘</strong><p>${esc(error.message)}。请先检查“数据同步”和“系统状态”。</p></div>`;
            }
        } finally {
            setBusy(false);
            // A date can be edited during the request. Never paint the old date
            // under the new selection; read the latest selection next.
            if (completedDate !== requestDate()) await loadReviewDashboard({force: true});
            else if (autoRepair) await materializeReviewDashboard({automatic: true});
        }
    }

    async function materializeReviewDashboard(options = {}) {
        if (loading || !validDate()) return;
        const date = requestDate();
        let completedDate = date;
        setBusy(true, "正在扫描全市场...");
        setNotice(`${options.automatic ? '发现历史基准缺口，正在自动补齐并更新' : '正在生成'} ${date || "最近交易日"} 复盘：复用历史板池，仅补缺失数据，可能需要几分钟。`, "info");
        try {
            const query = date ? `?date=${encodeURIComponent(date)}` : "";
            const data = await jsonRequest(`/api/v1/review/refresh${query}`, {method: "POST"});
            if (date !== requestDate()) return;
            renderDateContext(data, date);
            renderDashboard(data);
            if (!isIndex) void window.SentimentWorkspace?.mount(requestDate(), true);
            completedDate = requestDate();
            loadedRequestDate = requestDate();
            if (byId("reviewDashboardRoot")) byId("reviewDashboardRoot").dataset.loaded = "true";
            setNotice(`已生成 ${data.analysis_date} 复盘快照。${date && date !== data.analysis_date ? `所选 ${date} 暂无完整数据，请留意上方日期说明。` : "可直接读取，无需重复生成。"}`, "success");
        } catch (error) {
            if (date !== requestDate()) return;
            setNotice(`复盘生成失败：${error.message}`, "error");
        } finally {
            setBusy(false);
            if (completedDate !== requestDate()) await loadReviewDashboard({force: true});
        }
    }

    function changeReviewDate() {
        loadedRequestDate = "";
        const trends = byId("reviewBoardDynamics");
        if (!isIndex && trends) { trends.bdRequest = {}; trends.innerHTML = '<p role="status">正在切换日趋势日期…</p>'; }
        const root = byId("reviewDashboardRoot");
        if (root) {
            root.dataset.loaded = "false";
            root.innerHTML = `<div class="review-empty review-empty-primary"><strong>正在切换复盘日期</strong><p>将读取 ${esc(requestDate() || "最近交易日")} 的快照。</p></div>`;
        }
        if (byId("reviewFreshnessText")) byId("reviewFreshnessText").textContent = "正在读取所选日期...";
        return loadReviewDashboard({force: true});
    }

    function latestReviewDate() {
        if (loading) return;
        if (byId("reviewDate")) byId("reviewDate").value = "";
        return changeReviewDate();
    }

    function selectReviewFutures(product) {
        const selected = String(product || "TOTAL").toUpperCase();
        document.querySelectorAll("[data-review-futures-tab]").forEach((button) => {
            const active = button.dataset.reviewFuturesTab === selected;
            button.classList.toggle("is-active", active);
            button.setAttribute("aria-selected", active ? "true" : "false");
        });
        document.querySelectorAll("[data-review-futures-panel]").forEach((panel) => {
            panel.hidden = panel.dataset.reviewFuturesPanel !== selected;
        });
        const status = byId("reviewFuturesSyncStatus");
        if (status) status.textContent = `当前${selected === 'TOTAL' ? '渠道合计' : '品种 '+selected} · 当前持仓与净空日增减`;
    }

    async function syncReviewFutures() {
        if (loading || window.reviewFuturesSyncing || !validDate()) return;
        const date = requestDate();
        const button = byId("reviewFuturesSyncButton");
        const status = byId("reviewFuturesSyncStatus");
        window.reviewFuturesSyncing = true;
        if (button) {
            button.disabled = true;
            button.setAttribute("aria-busy", "true");
            button.textContent = "同步中...";
        }
        if (status) status.textContent = `正在同步 ${date || "最近交易日"} 的期指缺口...`;
        setNotice(`正在同步 ${date || "最近交易日"} 的股指期货增量缺口，完成后刷新日趋势。`, "info");
        try {
            const query = date ? `?date=${encodeURIComponent(date)}` : "";
            const result = await jsonRequest(`/api/v1/review/futures/refresh${query}`, {method: "POST"});
            await loadReviewDashboard({force: true});
            if (result.status !== "ok" || result.refresh_errors) throw new Error(result.message || "部分期指数据源未返回所选日数据，已有归档已保留。");
            if (status) status.textContent = "期指缺口同步完成 · 已刷新日趋势";
            setNotice("");
        } catch (error) {
            if (status) status.textContent = "同步未完成 · 已保留可用归档";
            setNotice(`股指期货同步失败：${error.message}`, "error");
        } finally {
            window.reviewFuturesSyncing = false;
            if (button) {
                button.disabled = false;
                button.setAttribute("aria-busy", "false");
                button.textContent = "同步期指缺口";
            }
        }
    }

    window[handlerName("loadReviewDashboard")] = loadReviewDashboard;
    window[handlerName("materializeReviewDashboard")] = materializeReviewDashboard;
    window[handlerName("changeReviewDate")] = changeReviewDate;
    window[handlerName("latestReviewDate")] = latestReviewDate;
    if (isIndex) {
        window.selectReviewFutures = selectReviewFutures;
        window.syncReviewFutures = syncReviewFutures;
    } else {
        window.loadSentimentLiveEmotion = loadLiveEmotion;
        // Preserve existing deep links while the workspace owns the three tabs.
        window.selectSentimentSection = section => {
            window.SentimentWorkspace?.select(section === 'theme' ? 'rotation' : 'today');
        };

    }
    }
    createWorkspace("review");
    createWorkspace("sentiment");
})();
