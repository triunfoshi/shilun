/* Selection research: shared evidence table, tactic-specific columns, explicit scope. */
(() => {
    'use strict';
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const numeric = value => value == null || value === '' || typeof value === 'boolean' ? null : Number.isFinite(Number(value)) ? Number(value) : null;
    const num = (value, digits = 2) => numeric(value) === null ? '—' : Number(value).toLocaleString('zh-CN', {minimumFractionDigits:digits,maximumFractionDigits:digits});
    const pct = value => numeric(value) === null ? '—' : `${num(Number(value) * 100)}%`;
    const amount = value => numeric(value) === null ? '—' : `${num(value)}亿`;
    const stock = row => `<strong>${esc(row.name || row.ticker)}</strong><small>${esc(row.ticker)}</small>`;
    const valueCell = (main, sub = '') => `<strong>${main}</strong>${sub ? `<small>${sub}</small>` : ''}`;
    function table(headers, rows, extra = '') {
        return `<div class="selection-evidence-scroll" role="region" aria-label="研究结果" tabindex="0"><table class="selection-evidence-table ${extra}"><thead><tr>${headers.map(h => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(cells => `<tr>${cells.map((cell, i) => `<td data-label="${esc(headers[i])}"${i ? '' : ' class="selection-identity"'}>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    }
    function scope(data, strategy) {
        const research = ['red_k_net_inflow','structure_repair_520','weekly_breakout'].includes(strategy);
        const ma5 = strategy === 'ma5_v0_2';
        const names = data?.selection_scope?.sector_names || [];
        const fullMarket = ma5 && data?.selection_scope?.kind === 'full_market';
        const liquidity = data?.selection_scope?.liquidity;
        const label = fullMarket ? '全市场 MA5 筛选' : ma5 ? '趋势板块内选股' : '全市场形态扫描';
        const description = ma5
            ? fullMarket ? `全市场 ${num(data?.selection_scope?.universe_count, 0)} 只独立扫描，先验证个股硬条件，再核对板块趋势与资金。大盘当日成交 ${num(numeric(liquidity?.market_amount_cny) === null ? null : liquidity.market_amount_cny / 1e12, 4)} 万亿；${esc(liquidity?.label || '量能待核查')}，保留 8.8 亿下限。`
                : `先取趋势榜前 ${data?.selection_scope?.sector_count ?? 8} 个板块的候选，再验证个股硬条件；与板块榜共享上游结果。`
            : research ? '从全市场已覆盖日线扫描，叠加战法的行业共振与个股条件；不受趋势榜前 8 名限制。'
                : '从全市场快照读取最近一次入选信号，再交由下一交易日盘中条件验证。';
        const capabilities = research ? '研究候选 · 尚未接入执行验证' : '已接入策略规则 · 入池后仍需验证触发条件';
        return `<section class="selection-scope" aria-label="候选范围"><p><strong>${label}</strong><span>${capabilities}</span></p><p>${description}</p>${names.length ? `<details><summary>查看候选板块范围</summary><p>${names.map(esc).join('、')}</p></details>` : ''}</section>`;
    }
    function research(data) {
        const id = data.strategy_id;
        const items = data.candidates || [];
        const intro = scope(data, id);
        if (data.status === 'not_materialized') return `${intro}<div class="candidate-terminal-empty"><strong>该日选品结果尚未生成</strong><p>请先在指数复盘或短线情绪生成 ${esc(data.analysis_date)} 的复盘，再刷新选品池。</p></div>`;
        const stat = `<section class="candidate-terminal-summary" aria-label="选品池摘要"><div><span>满足形态</span><strong>${num(data.total_count ?? items.length, 0)}</strong></div><div><span>当前展示</span><strong>${items.length}</strong></div><p>数据截至 ${esc(data.analysis_date)} · 按该战法排序展示归档前列</p></section>`;
        const rule = `<p class="selection-method">${esc(data.rule || '')}</p>`;
        if (!items.length) return `${intro}${stat}${rule}<div class="candidate-terminal-empty"><strong>该日没有满足条件的候选</strong><p>形态扫描已完成，可切换战法或研究日查看。</p></div>`;
        const common = row => [stock(row), esc(row.sector_name || '—')];
        let headers, rows;
        if (id === 'red_k_net_inflow') {
            headers = ['股票','行业','净流入','成交额','阳线实体占比','收盘位置','共振线索'];
            rows = items.map(r => [...common(r), amount(r.net_inflow_yi), amount(r.amount_yi), pct(r.signal?.body_ratio), pct(r.signal?.close_position), valueCell(esc(r.role?.label || '—'), esc(r.tape_strength?.label || '盘口待确认'))]);
        } else if (id === 'structure_repair_520') {
            headers = ['股票','行业','修复状态','MA5 / MA20','MA5上行幅度','成交额','净流入'];
            rows = items.map(r => [...common(r), esc(r.signal?.state === 'cross_confirmed' ? '上穿已确认' : r.signal?.state ? '转向待确认' : '—'), valueCell(num(r.signal?.ma5), `MA20 ${num(r.signal?.ma20)}`), valueCell(num(r.signal?.ma5_slope), '价格差，非涨幅'), amount(r.amount_yi), amount(r.net_inflow_yi)]);
        } else {
            headers = ['股票','行业','收盘','突破参考位','突破幅度','成交额','容量提示'];
            rows = items.map(r => [...common(r), num(r.signal?.close), valueCell(num(r.signal?.breakout_level), '前100交易日高点'), pct(r.signal?.breakout_pct), amount(r.amount_yi), valueCell(r.cap_fit === 'avoid_large_cap' ? '大市值，需复核' : r.cap_fit === 'fit' ? '容量适配' : '容量待复核', amount(r.market_cap_yi))]);
        }
        const method = id === 'red_k_net_inflow' ? '实体占比 = 实体 / 日内振幅；收盘位置越接近 100%，越靠近当日高点。净流入采用 Tushare 主动买卖净值。'
            : id === 'structure_repair_520' ? 'MA5 上行幅度以价格差展示；是否形成可交易修复仍需结合量能、位置和后续确认。'
                : '当前突破检测以约 20 周的前 100 个交易日高点作代理，尚非独立周线 K 线模型。';
        return `${intro}${stat}${rule}${table(headers, rows)}<p class="candidate-terminal-footnote">${method}</p>`;
    }
    function excluded(rows, selectedTicker) {
        if (!rows.length) return '';
        const reason = r => (r.selection_gate?.failure_reasons || []).join('；') || r.ranking_exclusion_label || r.ranking_explanation || '未通过入池条件';
        const cells = rows.map(r => [stock(r), esc(r.sector_name || '—'), esc(reason(r)), `<button type="button" data-candidate-detail="${esc(r.ticker)}">查看依据</button>`]);
        return `<details class="selection-excluded"${rows.some(r => r.ticker === selectedTicker) ? ' open' : ''}><summary>未入选 ${rows.length} 只 · 展开查看条件缺口</summary><p>未通过硬条件的股票不列为机会，不展示买点、目标或零分排名。</p>${table(['股票','板块','未入选原因','依据'], cells, 'is-exclusions')}</details>`;
    }
    // A capital ranking answers a different question from a 20/60-day trend score.
    // Never reuse stale flow or coerce missing values to zero. Board memberships
    // overlap; the amounts are comparable per view but must never be added.
    const topicAllowed=s=>!/^昨日|最近多板|历史新高|百日新高|百元股|连板股|强势股|涨停板|近期新高|^20\d{2}.*(报|扭亏|首亏)|风格$|^[高中小大微]盘(精选|股|价值|成长)$|^(券商金股|低市净率|高市净率|高成长股|题材股|反转股|趋势股|密集调研|股权分散|股权集中|近期摘帽|破增发价股|破发股|超跌股|权重股|行业龙头|微利股|红利股|价值股|周期股|东方财富热股|红利破净股|长期破净|破净股|低价股|宁组合|茅指数|标准普尔|GDR|富时罗素|养老金|MSCI中国|深证100R|创业板综|上证380|中证500|创业成份|B股|上证180_|上证50_|央视50_|深成500|机构重仓|基金重仓|QFII重仓|转债标的|社保重仓|ST股|次新股|HS300_|AH股|AB股|科创板做市股)$/.test(s.sector_name||'');
    function capitalRows(data, metric = 'main_net_inflow') {
        const fields = ['main_net_inflow','large_order_net','amount'];
        const sortKey = fields.includes(metric) ? metric : fields[0];
        return (data.all_sectors || []).filter(s => {
            const m = s.metrics || {};
            const count = numeric(s.member_count);
            return topicAllowed(s) && count !== null && count > 0
                && m.moneyflow_asof_date === data.analysis_date && m.moneyflow_is_stale === false
                && fields.every(k => numeric(m[k]) !== null);
        }).sort((a,b) => b.metrics[sortKey] - a.metrics[sortKey] || String(a.sector_code).localeCompare(String(b.sector_code)));
    }
    function capital(data, metric = 'main_net_inflow') {
        const rows = capitalRows(data, metric);
        const allCount = (data.all_sectors || []).filter(s => topicAllowed(s) && numeric(s.member_count) !== null && s.member_count > 0).length;
        const headers = ['板块 / 成分数','成交额','成交额占全市场','净流入','大单净额','趋势排名'];
        const cells = rows.slice(0, 15).map(s => {
            const m = s.metrics;
            return [valueCell(esc(s.sector_name), `${num(s.member_count,0)} 家`), amount(m.amount / 100000), pct(m.market_share), amount(m.main_net_inflow / 10000), amount(m.large_order_net / 10000), valueCell(num(m.mainline_rank,0), `20日 ${pct(m.return_20d)}`)];
        });
        return `<section id="sector-capital" class="sector-feature selection-capital"><header><div><h3>当日资金对照</h3><p>${esc(data.analysis_date)} · ${rows.length} / ${allCount} 个合规方向有同日资金归档 · 展示前 ${Math.min(15,rows.length)} 个</p></div><label>排序指标 <select id="sectorCapitalMetric" onchange="changeSectorCapitalMetric(this.value)">${[['main_net_inflow','净流入'],['large_order_net','大单净额'],['amount','成交额']].map(([k,v]) => `<option value="${k}"${metric === k ? ' selected' : ''}>${v}</option>`).join('')}</select></label></header><p>完整成分已归档的方向参与资金比较，不受趋势榜前 8 名限制。净流入、大小单结构和成交热度回答不同问题；成分重叠的板块金额不能相加。</p>${rows.length ? table(headers,cells,'is-capital') : '<p class="candidate-terminal-empty">所选日缺少成分已核验的同日资金归档，请补齐数据后重新计算板块。</p>'}<details><summary>资金口径与边界</summary><p>来源：Tushare moneyflow。净流入为 net_mf_amount；大单净额为大单与特大单买入减卖出。成交额表示交易活跃度，不表示净流入。此处按已有成分数据汇总，旧归档未保存逐股资金覆盖率；缺失或过期的板块不参与排名。成分数量按完整名单统计，排除行情筛选标签。</p><p>趋势榜的 20 / 60 日相对强度权重合计 60%，高分可能伴随当日资金流出。将趋势、资金、个股硬条件分别确认；同源候选重合不能作为独立验证。</p></details></section>`;
    }
    window.SelectionResearch = {scope,research,excluded,capital,capitalRows};
    document.addEventListener('click', event => {
        const button = event.target.closest('[data-candidate-detail]');
        if (button) window.selectCandidateDetail(button.dataset.candidateDetail);
    });
})();
