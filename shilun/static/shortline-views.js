/* Vibe AStock e0d08d1 · FactsPanels / EmotionCards / ThemeRotationPanel / HistoryTab.
 * Apache-2.0: licenses/vibe-astock.txt. Lucide SVG geometry: ISC, licenses/lucide.txt.
 * Native DOM adapter; financial groupings and readings are supplied by dated APIs.
 */
(() => {
  'use strict';
  const arr = value => Array.isArray(value) ? value : [];
  const finite = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const plain = value => String(value ?? '').replace(/\*\*/g, '');
  const count = value => finite(value) == null ? '—' : String(value);
  const rate = value => finite(value) == null ? '—' : `${Math.round(value * 100)}%`;
  const signed = value => finite(value) == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
  const hhmm = value => { const s = String(value ?? '').replace(/:/g, ''); return s.length < 4 ? '—' : `${s.slice(0,2)}:${s.slice(2,4)}`; };
  const pctTone = value => finite(value) == null || value === 0 ? 'slv-muted' : value > 0 ? 'slv-up' : 'slv-down';
  const trendTone = value => String(value).includes('走强') ? 'slv-up' : String(value).includes('转弱') ? 'slv-down' : '';
  const reading = (value, kind, unit = '') => finite(value) == null ? '—' : kind === 'rate' || kind === 'share' ? rate(value) : kind === 'pct' ? signed(value) : `${Number.isInteger(value) ? value : value.toFixed(1)}${esc(unit)}`;
  const requests = new Map();
  let nextId = 0;
  // Paths copied from lucide-react's installed source, not approximate glyphs.
  const icons = {
    network: '<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3M12 12V8"/>',
    calendar: '<path d="M21 7.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3.5M16 2v4M8 2v4M3 10h5M17.5 17.5 16 16.3V14"/><circle cx="16" cy="16" r="6"/>',
    layers: '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m6.08 9.5-3.5 1.6a1 1 0 0 0 0 1.81l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9a1 1 0 0 0 0-1.83l-3.5-1.59"/><path d="m6.08 14.5-3.5 1.6a1 1 0 0 0 0 1.81l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9a1 1 0 0 0 0-1.83l-3.5-1.59"/>',
    grid: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    gauge: '<path d="m12 14 4-4M3.34 19a10 10 0 1 1 17.32 0"/>',
    arrows: '<path d="M8 3 4 7l4 4M4 7h16m-4 14 4-4-4-4M20 17H4"/>',
    table: '<path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18"/>',
    help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01"/>'
  };
  const icon = name => `<svg class="slv-icon${name === 'help' ? ' slv-icon-help' : ''}" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${icons[name] || icons.table}</svg>`;
  function caliber(text) {
    const id = `slv-caliber-${++nextId}`;
    return `<span class="slv-caliber"><button class="slv-caliber-button" type="button" title="这个数怎么算的" aria-expanded="false" aria-controls="${id}">${icon('help')}口径</button><span class="slv-caliber-note" id="${id}" role="note" aria-label="口径说明" hidden>${esc(plain(text))}</span></span>`;
  }
  function section(name, title, hint, content, options = {}) {
    return `<article class="slv-panel ${options.className || ''}"><header class="slv-panel-heading">${icon(name)}<h3>${esc(title)}</h3>${options.caliber ? caliber(options.caliber) : ''}</header>${hint ? `<p class="slv-hint">${esc(hint)}</p>` : ''}${options.available === false ? `<p class="slv-unavailable" role="status">这一项没算出来:${esc(plain(options.reason) || '原因不明')}</p>` : content}</article>`;
  }
  const TREE_HINT = '按涨停原因题材聚合(公开涨停揭秘或问财,源站归因非公司确认),不是行业分类 —— 「兵装重组」「间接投资长鑫科技」这类事件在行业分类里找不到。每行按复盘顺序摆:昨日反馈 → 几点首封 → 扩散几只 → 梯队多高 → 炸了几个。';
  const CYCLE_CALIBER = '情绪分 = 涨停家数、最高连板、炸板率三项在窗口内归一化后的平均,只用涨停池算。\n炸板率是「越高越冷」,所以取反后再平均 —— 三项都指向同一个方向,分越高越热。\n「第 N 天」= 这一天距窗口最低点第几个交易日,不是自然日。\n🔴 这是可解释的启发式,不是精确的周期划分:全市场压成一个数,而不同题材完全可能同时处在启动、高潮和退潮。窗口内一路走低时,起点会落在最后一天(= 仍在探底、记为第 1 天)。当参考,别当结论。';
  const ROTATION_CALIBER = '题材 = 同花顺涨停揭秘的涨停原因按「+」拆出的标签(去掉国企改革、破净这类属性词),和题材事件树同一套;一只票可以同时算进几个题材。\n列头涨停数沿用当日原始池总数;题材归因仅统计沪深非ST，北交所不计题材或缺原因。\n格子里的数 = 那天这个题材的涨停只数(东财涨停池里、有涨停原因的),颜色越深越多;小角标 = 当天按只数排第几(并列同名次),前三加粗。\n「连续 N 天」数到今天为止每天都有涨停;「隔 k 天回流」= 中间断了 k 天今天又有;「窗口内首次出现」= 这几天里今天才第一次有。\n某天东财涨停池或涨停原因没取到,那一列画「—」,按缺数处理(不是 0),跨过它的「连续」「回流」「首次出现」都会注明说不准。\n某天有沪深涨停股没有涨停原因:那天没出现的题材也画「—」(可能有),出现的只数是下限。北交所的涨停股同花顺不收录,不算进任何题材。\n表里先列今天有涨停的前 12 个题材,再补最多 6 个昨天至少 2 只、今天一只都没有的(退下去的)。\n涨停原因是源站编辑归因,不是公司确认。';
  const stateTone = state => ['扩散中','延续'].includes(state) ? 'slv-badge-up' : ['接力断档','昨日题材未再涨停'].includes(state) ? 'slv-badge-down' : ['分歧加大','高标独活'].includes(state) ? 'slv-badge-warning' : state === '今日新出现' ? 'slv-badge-new' : 'slv-badge-muted';
  function treePanel(tree = {}) {
    const themes = arr(tree.themes);
    const content = themes.length ? `${tree.source_note ? `<p class="slv-source-note">${esc(tree.source_note)}</p>` : ''}<div class="slv-tree-list">${themes.map(n => `<div class="slv-tree-row"><div class="slv-tree-main"><strong>${esc(n.tag)}</strong><span class="slv-badge ${stateTone(n.state)}">${esc(n.state)}</span><span class="slv-tree-count">涨停 <b>${count(n.limit_up)}</b>(首板${count(n.first_boards)}/连板${count(n.consec_boards)})· 最高 <b>${count(n.highest)}</b> 板</span>${n.broken > 0 ? `<span class="slv-warning slv-small">炸${count(n.broken)}</span>` : ''}${n.limit_down > 0 ? `<span class="slv-down slv-small">跌停${count(n.limit_down)}</span>` : ''}</div><div class="slv-tree-details">${n.first_seal ? `<span>首封 ${hhmm(n.first_seal)}${n.last_seal && n.last_seal !== n.first_seal ? ` → 末封 ${hhmm(n.last_seal)}` : ''}</span>` : ''}${n.prev_members > 0 ? `<span>昨日 ${count(n.prev_members)} 只 → 今仍涨停 <b class="${finite(n.continuation_rate) == null ? '' : n.continuation_rate >= .5 ? 'slv-up' : 'slv-down'}">${count(n.prev_still_up)}</b>${finite(n.continuation_rate) != null ? `(延续 ${rate(n.continuation_rate)})` : ''}</span>` : ''}${arr(n.members).length ? `<span class="slv-member-preview" title="${esc(arr(n.members).map(m => `${m.name}${m.label ?? `${count(m.boards)}板`}`).join('、'))}">${arr(n.members).slice(0,4).map(m => `${esc(m.name)}${esc(m.label ?? `${count(m.boards)}板`)}`).join('、')}</span>` : ''}</div></div>`).join('')}</div><p class="slv-footnote">共 ${count(tree.tag_count)} 个事件题材 · 题材串覆盖 ${count(tree.covered)}/${count(tree.total_limit_up)} 只涨停${finite(tree.coverage_rate) != null ? `(${rate(tree.coverage_rate)})` : ''}${finite(tree.concentration) != null ? ` · 头部占全市场涨停 ${rate(tree.concentration)}` : ''}</p>` : '<p class="slv-unavailable">没有可聚合的题材</p>';
    return section('network','题材事件树',TREE_HINT,content,{available:tree.available,reason:tree.reason,className:'slv-tree-panel'});
  }
  function cyclePanel(cycle = {}) {
    const series = arr(cycle.series), pctile = cycle.pctile ?? cycle.range_position;
    const content = `<div class="slv-cycle-stats"><div><strong class="slv-stat">第 ${count(cycle.day_n)} 天</strong><span>起点 ${esc(cycle.trough_date || '—')}</span></div><div><strong class="slv-stat ${trendTone(cycle.trend)}">${finite(cycle.current_score) != null ? cycle.current_score.toFixed(2) : '—'}</strong><span>当前情绪分</span></div></div><p class="slv-cycle-warning">⚠️ 启发式,不是精确周期划分。全市场压成一个数,而不同题材可能同时处在启动 / 高潮 / 退潮。</p>${series.length ? `<div class="slv-cycle-chart"><div class="slv-cycle-bars">${series.map(d => `<div title="${esc(d.date)} 情绪分 ${finite(d.score) ?? '—'}|涨停 ${count(d.limit_up)} 家|最高 ${count(d.highest_consec)} 板" class="slv-cycle-bar${d.date === cycle.trough_date ? ' is-trough' : ''}${finite(d.score) == null ? ' is-missing' : ''}"${finite(d.score) != null ? ` style="height:${Math.max(6,Math.min(100,Math.round(d.score*100)))}%"` : ''}></div>`).join('')}</div><div class="slv-cycle-dates">${series.map((d,i) => `<span class="${d.date === cycle.trough_date ? 'slv-up slv-bold' : i === series.length - 1 ? 'slv-bold' : ''}">${esc(d.date.slice(5).replace('-','/'))}</span>`).join('')}</div></div>` : ''}<p class="slv-cycle-caption">近 ${count(cycle.window)} 个交易日 · 距窗口低点第 ${count(cycle.day_n)} 天${cycle.trend ? ` · <b class="${trendTone(cycle.trend)}">${esc(cycle.trend)}</b>` : ''}${finite(pctile) != null ? ` · 位于近 ${count(cycle.window)} 日区间 ${rate(pctile)} 分位` : ''}${series.length ? ' <span class="slv-trough-key" aria-hidden="true"></span> 起点 <b>加粗</b> 这一天' : ''}</p>${finite(cycle.requested_window) != null && finite(cycle.window) != null && cycle.window < cycle.requested_window ? `<p class="slv-warning slv-small" role="status">要的是 ${cycle.requested_window} 天,只取到 ${cycle.window} 天</p>` : ''}`;
    return section('calendar','情绪周期','窗口内情绪分最低的那天视为本轮起点',content,{caliber:CYCLE_CALIBER,available:cycle.available === true,reason:cycle.reason,className:'slv-cycle-panel'});
  }
  function industryPanel(industry = {}) {
    const themes = arr(industry.themes), timeline = arr(industry.timeline), max = Math.max(1,...timeline.map(x => finite(x.count) ?? 0));
    const content = `<div class="slv-industry-list">${themes.map(t => `<div class="slv-industry-row"><strong title="${esc(t.sector)}">${esc(t.sector)}</strong><span class="slv-industry-ticks" aria-hidden="true">${Array.from({length:Math.min(12,Math.max(0,finite(t.limit_up) ?? 0))},() => '<i></i>').join('')}</span><span class="slv-industry-meta">${count(t.limit_up)}只 · 最高${count(t.highest)}板${t.broken > 0 ? ` <span class="slv-warning">· 炸${count(t.broken)}</span>` : ''}${t.first_seal ? ` · 首封 ${hhmm(t.first_seal)}` : ''}</span>${arr(t.names).length ? `<span class="slv-industry-members" title="${esc(arr(t.names).map(x => `${x.name}(${count(x.boards)}板)`).join('、'))}">${arr(t.names).slice(0,3).map(x => `${esc(x.name)}${count(x.boards)}板`).join(' ')}</span>` : ''}</div>`).join('')}</div>${timeline.length ? `<div class="slv-timeline"><strong>涨停时间分布(按首次封板)</strong><div class="slv-time-bars">${timeline.map(x => `<div><i title="${esc(x.slot)}：${count(x.count)}只" style="height:${Math.max(3,((finite(x.count) ?? 0)/max)*44)}px"></i><b>${count(x.count)}</b><span>${esc(x.slot)}</span></div>`).join('')}</div></div>` : ''}${finite(industry.concentration) != null ? `<p class="slv-industry-caption">共 ${count(industry.theme_count ?? industry.industry_count)} 个方向,头部占全市场涨停 <b>${rate(industry.concentration)}</b>${industry.concentration >= .3 ? '(一枝独秀)' : industry.concentration <= .15 ? '(高度分散)' : ''}</p>` : ''}${industry.industry_valid_n != null && industry.industry_valid_n < industry.total_limit_up ? `<p class="slv-warning slv-small">所属行业覆盖 ${count(industry.industry_valid_n)}/${count(industry.total_limit_up)}，缺少行业的股票未分组。</p>` : ''}${industry.missing_time_n ? `<p class="slv-warning slv-small">${count(industry.missing_time_n)}只缺少首封时间，未计入时间分布。</p>` : ''}`;
    return section('layers','行业结构 · 发酵节奏','⚠️ 这是「行业分类」视角(兜底)—— 真正的题材看题材事件树。行业≠题材:同一行业可能装着完全不同的炒作原因。这里的价值主要在「涨停时间分布」。',content,{available:industry.available === true,reason:industry.reason,className:'slv-industry-panel'});
  }
  function renderTree(data = {}, context = {}) {
    return `<div class="slv slv-tree-layout">${treePanel(data.tree || {})}<div class="slv-context-stack">${cyclePanel(context.cycle || data.cycle || {})}${industryPanel(context.industry || data.industry || {})}</div></div>`;
  }
  function rotationCell(value, rank, max) {
    const n = finite(value), r = finite(rank);
    if (n == null) return '<td class="slv-rotation-missing" title="这天没取到或说不准,按缺数处理(不是 0)">—</td>';
    if (n === 0) return '<td class="slv-rotation-zero">·</td>';
    const alpha = .18 + .62*Math.min(1,n/Math.max(1,max));
    return `<td class="slv-rotation-cell"><span class="slv-heat${r != null && r <= 3 ? ' is-top3' : ''}${alpha >= .45 ? ' is-dark' : ''}" style="--slv-heat-alpha:${alpha.toFixed(2)}">${n}${r != null ? `<sup>#${r}</sup>` : ''}</span></td>`;
  }
  function rotationRow(t,max) {
    const tone = !t.today ? 'slv-down' : String(t.status).startsWith('连续') ? 'slv-up slv-bold' : String(t.status).includes('回流') || String(t.status).includes('首次') ? 'slv-warning' : 'slv-muted';
    return `<tr><th scope="row">${esc(t.tag)}</th>${arr(t.series).map((v,i) => rotationCell(v,arr(t.ranks)[i],max)).join('')}<td class="slv-rotation-status ${tone}">${esc(t.today ? t.status : '今天退下去了')}</td><td class="slv-center">${count(t.active_days)}</td><td class="slv-center">${count(t.top3_days)}</td><td class="slv-rotation-members" title="${esc(arr(t.members).join('、'))}">${esc(arr(t.members).join('、') || '—')}</td></tr>`;
  }
  function renderRotation(data = {}) {
    const rot = data.rotation || {}, dates = arr(rot.dates), days = arr(rot.days), themes = arr(rot.themes), active = themes.filter(t => t.today), faded = themes.filter(t => !t.today);
    const max = Math.max(1,...themes.flatMap(t => arr(t.series).map(v => finite(v) ?? 0))), cols = dates.length + 5;
    const content = `${arr(rot.missing_days).length ? `<p class="slv-warning" role="status">${esc(rot.missing_days.join('、'))} 没取到:那几列画「—」,按缺数处理(不是 0),跨过它们的「连续」「回流」会注明说不准。</p>` : ''}${arr(rot.partial_days).length ? `<p class="slv-warning" role="status">${esc(rot.partial_days.join('、'))} 有沪深涨停股没有涨停原因:那几天没出现的题材画「—」(可能有),出现的只数是下限。</p>` : ''}<div class="slv-table-scroll" role="region" aria-label="近五个交易日题材轮动" tabindex="0"><table class="slv-table slv-rotation-table"><thead><tr><th>题材</th>${dates.map((d,i) => `<th class="slv-center" title="${esc(days[i]?.scope_note || days[i]?.reason || '')}">${esc(d.slice(5))}<small>${days[i]?.available ? `涨停 ${count(days[i].limit_up)}` : '没取到'}</small>${days[i]?.available && days[i].unknown ? `<small class="slv-warning">缺原因 ${count(days[i].unknown)}</small>` : ''}</th>`).join('')}<th>状态</th><th class="slv-center">有涨停天数</th><th class="slv-center">进前三天数</th><th>今天板数最高的</th></tr></thead><tbody>${!active.length ? `<tr><td colspan="${cols}" class="slv-empty-cell">今天没有能归到题材的涨停股</td></tr>` : active.map(t => rotationRow(t,max)).join('')}${faded.length ? `<tr><td colspan="${cols}" class="slv-faded-label">昨天还有 2 只以上、今天一只都没有的:</td></tr>${faded.map(t => rotationRow(t,max)).join('')}` : ''}</tbody></table></div><p class="slv-small slv-muted">这几天一共 ${count(rot.tag_count)} 个题材标签,表里只列今天前 ${active.length} 个${faded.length ? `和退下去的 ${faded.length} 个` : ''}。客观公开榜单,非推荐 / 非预测。</p>`;
    return `<div class="slv">${section('grid','题材轮动 · 近 5 天哪些题材在前排','',content,{caliber:ROTATION_CALIBER,available:rot.available === true,reason:rot.reason})}</div>`;
  }
  function deltaText(delta,kind,unit) {
    const n = finite(delta); if (n == null) return '—';
    const sign = n > 0 ? '+' : n < 0 ? '-' : '', a = Math.abs(n);
    return kind === 'rate' ? `${sign}${Math.round(a*100)} 个百分点` : kind === 'pct' ? `${sign}${a.toFixed(2)} 个百分点` : `${sign}${Number.isInteger(a) ? a : a.toFixed(1)}${esc(unit || '')}`;
  }
  function statsPanel(c = {}) {
    const items = c.available ? arr(c.items).filter(it => finite(it.value) != null) : [];
    const content = `<div class="slv-stats-rows">${items.map(it => `<div class="slv-stats-row"><span class="slv-reading-label">${esc(it.label)}</span><b class="slv-reading-value">${reading(it.value,it.kind,it.unit)}</b>${finite(it.percentile) != null ? `<span class="slv-percentile-track" aria-label="历史分位${rate(it.percentile)}"><i class="${it.extreme === '偏热' ? 'is-hot' : it.extreme === '偏冷' ? 'is-cold' : ''}" style="width:${Math.max(2,Math.min(100,it.percentile*100))}%"></i></span><span class="slv-percentile-value">${rate(it.percentile)}</span>${it.extreme ? `<span class="slv-badge ${it.extreme === '偏热' ? 'slv-badge-up' : 'slv-badge-down'}">${esc(it.extreme)}</span>` : ''}${finite(it.hist_median) != null ? `<span class="slv-history-median">历史中位 ${reading(it.hist_median,it.kind,it.unit)}</span>` : ''}` : `<span class="slv-small slv-muted">样本不足 ${count(c.min_samples)} 天,暂不给分位</span>`}</div>`).join('')}</div><p class="slv-footnote">对比复盘日之前的 <b>${count(c.sample_days)}</b> 个交易日${c.extreme_count > 0 ? ` · ${count(c.extreme_count)} 项处在两头` : ' · 各项都不极端(平淡的一天,这本身也是信息)'}${c.sample_days < 30 ? ' ⚠️ 样本还短,现在的分位仅供粗判' : ''}</p>`;
    return section('gauge','历史统计位置','「涨停 40 家」是多还是少?光看绝对值判断不了 —— 要看它在历史上排第几。每行第二个百分数是历史分位(77% = 比 77% 的日子高),不是胜率;两头(≥85% 或 ≤15%)才额外标偏热 / 偏冷。炸板率、跌超5%家数是越高越冷,偏热 / 偏冷已经按这个方向判过。',content,{available:c.available === true,reason:c.reason});
  }
  function diffPanel(d = {}) {
    const changes = d.available ? arr(d.changes) : [];
    const content = changes.length ? `<div class="slv-diff-rows">${changes.map(c => `<div class="slv-diff-row"><span class="slv-reading-label">${esc(c.label)}</span><span class="slv-muted">${reading(c.prev,c.kind,c.unit)}</span><b class="${c.hotter ? 'slv-up' : 'slv-down'}">${c.delta > 0 ? '↑' : '↓'}</b><b>${reading(c.cur,c.kind,c.unit)}</b><span class="slv-small slv-muted">(${deltaText(c.delta,c.kind,c.unit)})</span><span class="slv-badge ${c.hotter ? 'slv-badge-up' : 'slv-badge-down'}">${c.hotter ? '转热' : '转冷'}</span></div>`).join('')}${d.unchanged_count > 0 ? `<p class="slv-small slv-muted">另有 ${count(d.unchanged_count)} 项变化在阈值内,未列出</p>` : ''}</div>` : '<p class="slv-unavailable">关键读数全部在噪声阈值内 —— 这一天与前一天没有实质变化。</p>';
    return section('arrows',d.available ? `复盘日 vs ${d.prev_date}` : '复盘日 vs 前一交易日','复盘最该先回答的是「今天和昨天有什么不同」。只列超过噪声阈值的变化 —— 涨停 40→43 不算变化。',content,{available:d.available === true,reason:d.reason});
  }
  function seriesLines(series) {
    const out = [];
    for (const row of [...arr(series)].reverse()) {
      const last = out[out.length-1];
      if (row.status === 'missing') {
        if (last?.kind === 'gap' && last.note === row.note) { last.from = row.date; last.count++; }
        else out.push({kind:'gap',from:row.date,to:row.date,count:1,note:row.note});
      } else out.push({kind:'day',row});
    }
    return out;
  }
  function historyControls(days) {
    return `<div class="slv-history-toolbar"><p>六项读数在近 ${days} 个交易日里的位置和走势</p><div class="slv-window-picker" role="group" aria-label="情绪历史看多少个交易日">${[10,20,30,60].map(n => `<button type="button" data-slv-window="${n}" aria-pressed="${days === n}">近 ${n} 天</button>`).join('')}</div></div>`;
  }
  function renderHistory(result = {}) {
    const readings = arr(result.readings), lines = seriesLines(result.series), days = result.days || 30;
    const table = `<div class="slv-table-scroll" role="region" aria-label="情绪历史逐日读数" tabindex="0"><table class="slv-table slv-history-table"><thead><tr><th>日期</th>${readings.map(x => `<th class="slv-right">${esc(x.label)}</th>`).join('')}</tr></thead><tbody>${lines.length ? lines.map(ln => ln.kind === 'gap' ? `<tr class="slv-gap-row"><td>${esc(ln.count > 1 ? `${ln.from} ~ ${ln.to}` : ln.to)}</td><td colspan="${Math.max(1,readings.length)}">${ln.count > 1 ? `${ln.count} 个交易日` : ''}没取到${ln.note ? `:${esc(ln.note)}` : ''}</td></tr>` : `<tr${ln.row.date === result.date ? ' class="is-selected"' : ''}><th scope="row">${esc(ln.row.date)}${ln.row.settled === false ? '<span class="slv-warning slv-small"> 未收盘</span>' : ''}${ln.row.status === 'partial' ? `<span class="slv-warning slv-small" title="${esc(ln.row.note)}"> 部分</span>` : ''}</th>${readings.map(x => { const v = finite(ln.row.values?.[x.key]); return `<td class="slv-right ${x.kind === 'pct' ? pctTone(v) : ''}">${reading(v,x.kind,x.unit)}</td>`; }).join('')}</tr>`).join('') : `<tr><td colspan="${Math.max(1,readings.length+1)}" class="slv-empty-cell">所选窗口尚无已归档读数</td></tr>`}</tbody></table></div>`;
    return `<div class="slv slv-history">${historyControls(days)}<div class="slv-history-summary">${statsPanel(result.context)}${diffPanel(result.diff)}</div>${section('table','逐日读数','从新到旧。取不到的日子写原因、不填 0;没收盘的日子数字还会变。',table)}${arr(result.gaps).length || result.source ? `<div class="slv-gaps-source">${arr(result.gaps).map(g => `<p class="slv-warning" role="status">⚠️ ${esc(g)}</p>`).join('')}${result.source ? `<p class="slv-muted">来源:${esc(result.source)}</p>` : ''}</div>` : ''}</div>`;
  }
  function renderEvidence(data = {}) {
    const evidence = data.evidence || data, rows = arr(evidence.rows), tags = [...new Set(rows.map(r => r.tag).filter(Boolean))].sort();
    const unique = new Set(rows.map(r => r.ticker || r.code)).size;
    const content = `<p class="slv-source-note">按所选日涨停原因拆分题材，与事件树、轮动及短线题材周期使用同一口径。一只股票可属于多个题材；此处 ${rows.length} 条题材归属对应 ${unique} 只股票。</p><div class="slv-evidence-toolbar"><label>题材 <select data-slv-evidence-filter aria-label="筛选同日题材证据"><option value="">全部题材</option>${tags.map(tag => `<option value="${esc(tag)}">${esc(tag)}</option>`).join('')}</select></label><span class="slv-muted">原因覆盖 ${count(evidence.covered)}/${count(evidence.total_limit_up)} 只涨停</span></div><div class="slv-table-scroll" role="region" aria-label="当日题材归档证据" tabindex="0"><table class="slv-table slv-evidence-table"><thead><tr><th>题材</th><th>股票</th><th>连板</th><th>首封</th><th>末封</th><th>炸板次数</th><th>同日涨停原因</th><th>来源</th></tr></thead><tbody>${rows.length ? rows.map(row => `<tr data-slv-evidence-tag="${esc(row.tag)}"><th scope="row">${esc(row.tag || '未归入题材')}</th><td><b>${esc(row.name)}</b><small>${esc(row.ticker || row.code)}</small></td><td>${count(row.boards)}板</td><td>${hhmm(row.first_seal)}</td><td>${hhmm(row.last_seal)}</td><td>${count(row.broken_times)}</td><td class="slv-evidence-reason">${esc(row.reason || '同日涨停原因缺失')}</td><td>${esc(row.reason_source || evidence.source || '—')}</td></tr>`).join('') : '<tr><td colspan="8" class="slv-empty-cell">所选日尚无可核验的题材归属，不用其他日期替代。</td></tr>'}</tbody></table></div>${arr(evidence.missing_codes).length ? `<details class="slv-evidence-missing"><summary>缺少同日涨停原因 · ${evidence.missing_codes.length}只</summary><p>${evidence.missing_codes.map(esc).join('、')}</p></details>` : ''}${evidence.source ? `<p class="slv-footnote">来源:${esc(evidence.source)}</p>` : ''}`;
    return `<div class="slv">${section('table',`当日题材证据 · ${evidence.date || data.date || '日期待核验'}`,'',content,{available:evidence.available,reason:evidence.reason})}</div>`;
  }
  function getRoot(target) { return typeof target === 'string' ? document.getElementById(target) : target; }
  function fetchDated(url,date,force) {
    if (force) requests.delete(url);
    if (!requests.has(url)) {
      const requestUrl = force ? `${url}${url.includes('?') ? '&' : '?'}refresh=true` : url;
      const promise = window.fetchJson(requestUrl,{timeoutMs:60000}).then(data => {
        if (data.date !== date) throw new Error('返回日期与所选日不一致');
        return data;
      });
      requests.set(url,promise);
      promise.catch(() => { if (requests.get(url) === promise) requests.delete(url); });
    }
    return requests.get(url);
  }
  function closeCalibers(root) {
    root.querySelectorAll('.slv-caliber.is-open').forEach(el => {
      el.classList.remove('is-open');
      const button = el.querySelector('button'), note = el.querySelector('[role="note"]');
      button?.setAttribute('aria-expanded','false');
      if (note) note.hidden = true;
      el.slvPinned = false;
    });
  }
  function openCaliber(holder,root,pinned=false) {
    closeCalibers(root);
    const button = holder.querySelector('button'), note = holder.querySelector('[role="note"]');
    holder.classList.add('is-open'); holder.slvPinned = pinned;
    button.setAttribute('aria-expanded','true'); note.hidden = false;
    // Viewport-clamped position keeps a right-column tooltip inside narrow windows.
    const bounds = button.getBoundingClientRect(), viewport = document.documentElement.clientWidth;
    note.style.position = 'fixed'; note.style.right = 'auto';
    note.style.left = `${Math.max(8,Math.min(bounds.left,viewport-note.getBoundingClientRect().width-8))}px`;
    note.style.top = `${bounds.bottom+4}px`;
    note.style.maxHeight = `calc(100dvh - ${bounds.bottom+16}px)`;
    note.style.overflowY = 'auto';
  }
  function wire(root,date) {
    if (root.slvWired) return;
    root.slvWired = true;
    root.addEventListener('click',event => {
      const button = event.target.closest('.slv-caliber-button');
      if (button) {
        const holder = button.closest('.slv-caliber'), open = !holder.classList.contains('is-open');
        if (open || !holder.slvPinned) openCaliber(holder,root,true);
        else closeCalibers(root);
        return;
      }
      const windowButton = event.target.closest('[data-slv-window]');
      if (windowButton) { void mountHistory(root,root.dataset.date || date,Number(windowButton.dataset.slvWindow)); return; }
      const retry = event.target.closest('[data-slv-retry]');
      if (retry && root.slvRetry) void root.slvRetry();
    });
    root.addEventListener('change',event => {
      if (!event.target.matches('[data-slv-evidence-filter]')) return;
      const value = event.target.value;
      root.querySelectorAll('[data-slv-evidence-tag]').forEach(row => { row.hidden = !!value && row.dataset.slvEvidenceTag !== value; });
    });
    root.addEventListener('keydown',event => {
      if (event.key === 'Escape') closeCalibers(root);
    });
    root.addEventListener('pointerover',event => {
      if (event.pointerType !== 'mouse') return;
      const holder = event.target.closest('.slv-caliber');
      if (holder && !holder.classList.contains('is-open')) openCaliber(holder,root);
    });
    root.addEventListener('pointerout',event => {
      const holder = event.target.closest('.slv-caliber');
      if (holder && !holder.slvPinned && !holder.contains(event.relatedTarget)) closeCalibers(root);
    });
    root.addEventListener('focusout',event => {
      if (!root.contains(event.relatedTarget)) closeCalibers(root);
    });
  }
  const loading = (label,days) => `<div class="slv">${days ? historyControls(days) : ''}<div class="slv-panel slv-loading" role="status">${esc(label)}加载中…</div></div>`;
  const failure = (label,error,days) => `<div class="slv">${days ? historyControls(days) : ''}<div class="slv-panel slv-failure" role="alert"><p>${esc(label)}这次没取到:${esc(error.message || '原因不明')}。不代表没有。</p><button type="button" data-slv-retry>重新读取</button></div></div>`;
  async function mountHistory(target,date,days=30,force=false) {
    const root = getRoot(target); if (!root || !date) return;
    days = [10,20,30,60].includes(days) ? days : 30;
    const token = {}; root.slvRequest = token; root.dataset.date = date; root.dataset.days = String(days);
    root.setAttribute('aria-busy','true'); root.innerHTML = loading('情绪历史',days); wire(root,date);
    root.slvRetry = () => mountHistory(root,date,days,true);
    try {
      const data = await fetchDated(`/api/v1/review/shortline/history?date=${encodeURIComponent(date)}&days=${days}`,date,force);
      if (root.slvRequest === token) root.innerHTML = renderHistory(data);
    } catch (error) { if (root.slvRequest === token) root.innerHTML = failure('情绪历史',error,days); }
    finally { if (root.slvRequest === token) root.setAttribute('aria-busy','false'); }
  }
  async function mount(options = {}) {
    const {date,force=false} = options; if (!date) return;
    const roots = {tree:getRoot(options.treeTarget),rotation:getRoot(options.rotationTarget),evidence:getRoot(options.evidenceTarget)};
    const token = {};
    for (const [kind,root] of Object.entries(roots)) {
      if (!root) continue;
      root.slvRequest = token; root.dataset.date = date; root.setAttribute('aria-busy','true');
      root.innerHTML = loading(kind === 'tree' ? '题材事件树' : kind === 'rotation' ? '题材轮动' : '当日题材证据'); wire(root,date);
      root.slvRetry = () => mount({...options,force:true});
    }
    const history = getRoot(options.historyTarget);
    const historyTask = history ? mountHistory(history,date,Number(history.dataset.days) || 30,force) : Promise.resolve();
    const themeTask = (async () => {
      if (!Object.values(roots).some(Boolean)) return;
      try {
        const themes = await fetchDated(`/api/v1/review/shortline/themes?date=${encodeURIComponent(date)}`,date,force);
        if (roots.rotation?.slvRequest === token) roots.rotation.innerHTML = renderRotation(themes);
        if (roots.evidence?.slvRequest === token) roots.evidence.innerHTML = renderEvidence(themes);
        if (roots.tree?.slvRequest === token) {
          roots.tree.innerHTML = renderTree(themes,{cycle:{available:false,reason:'情绪周期读取中…'},industry:{available:false,reason:'行业结构读取中…'}});
          try {
            const context = await fetchDated(`/api/v1/review/sentiment-context?date=${encodeURIComponent(date)}`,date,force);
            if (roots.tree.slvRequest === token) roots.tree.innerHTML = renderTree(themes,context);
          } catch (error) {
            if (roots.tree.slvRequest === token) roots.tree.innerHTML = renderTree(themes,{cycle:{available:false,reason:error.message},industry:{available:false,reason:error.message}});
          }
        }
      } catch (error) {
        for (const [kind,root] of Object.entries(roots)) if (root?.slvRequest === token) root.innerHTML = failure(kind === 'tree' ? '题材事件树' : kind === 'rotation' ? '题材轮动' : '当日题材证据',error);
      } finally { for (const root of Object.values(roots)) if (root?.slvRequest === token) root.setAttribute('aria-busy','false'); }
    })();
    await Promise.allSettled([themeTask,historyTask]);
  }
  if (typeof document !== 'undefined') document.addEventListener('click',event => {
    if (event.target.closest('.slv-caliber')) return;
    document.querySelectorAll('.slv').forEach(closeCalibers);
  });
  window.ShortlineViews = {mount,mountHistory,renderTree,renderRotation,renderEvidence,renderHistory,seriesLines,deltaText,ROTATION_CALIBER};
})();
