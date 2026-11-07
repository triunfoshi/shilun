/* Vibe-AStock research views, adapted for Shilun's shared dated evidence API.
 * Apache-2.0 attribution: licenses/vibe-astock.txt. No second app runtime. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const valid = v => typeof v === 'number' && Number.isFinite(v);
  const pct = v => valid(v) ? `${v > 0 ? '+' : ''}${v.toFixed(2)}%` : '—';
  const rate = v => valid(v) ? `${Math.round(v * 100)}%` : '—';
  const tone = v => !valid(v) || v === 0 ? 'is-flat' : v > 0 ? 'is-up' : 'is-down';
  const colored = v => `<span class="${tone(v)}">${pct(v)}</span>`;
  const money = v => valid(v) ? `${(v / 1e8).toFixed(2)}亿` : '—';
  const orderMoney = v => !valid(v) || v < 0 ? '—' : v >= 1e8 ? `${(v / 1e8).toFixed(2)}亿` : v >= 1e4 ? `${(v / 1e4).toFixed(2)}万` : `${v.toFixed(2)}元`;
  const time = v => v ? `${String(v).slice(0,2)}:${String(v).slice(2,4)}` : '—';
  const notice = (text, kind = '') => `<div class="br-notice ${kind}" role="status">${esc(text)}</div>`;
  const pages = {firstboard: {}, boardeffects: {}};
  let notes = new Map(), active = null, batch = false, expanded = new Set();
  let batchProgress = '';
  const sourceName = value => ({eastmoney_dated_pool:'东财日期归档', tushare_limit_list_d:'Tushare历史涨停池', shilun_limit_up_pool:'已有涨停归档', vibe_settled_previous_pool:'Vibe历史反馈归档', eastmoney_dated_previous_pool:'东财昨日涨停反馈', dated_pool_and_daily_bars:'历史名单与日线'}[value] || value || '暂无来源');
  function queryKey(page) { return `${$(page+'Date').value}|${page === 'boardeffects' ? $('boardeffectsDays').value : ''}`; }
  function stop() {
    batch = false;
    if (active) active.controller.abort();
    batchProgress = '已停止；完成的记录已保留。';
    updateControls();
  }
  async function load(page, options = {}) {
    if (page === 'firstboard' && focusView === 'strength') return window.StrengthReview?.load(options);
    if (page === 'firstboard' && focusView === 'strong') return window.StrongStocks?.load(options);
    const state = pages[page];
    if (!state) return;
    if (options.latest) $(page+'Date').value = '';
    const key = queryKey(page);
    if (options.cached && state.key === key && (state.data || state.controller)) return;
    if (page === 'firstboard' && (state.key !== key || options.refresh) && (active || batch)) stop();
    state.controller?.abort();
    const controller = new AbortController(), seq = (state.seq || 0) + 1;
    state.seq = seq; state.controller = controller;
    if (state.key !== key) { state.data = null; if (page === 'firstboard') { notes = new Map(); expanded.clear(); } }
    if (page === 'firstboard' && !state.data && $('firstboardLadder')) {
      $('firstboardLadder').innerHTML = notice('正在读取所选日期天梯…');
      $('firstboardNext').disabled = true;
    }
    state.key = key;
    const root = $(page+'Root'), status = $(page+'Status'), button = $(page+'Refresh');
    root.setAttribute('aria-busy', 'true'); button.disabled = true;
    if (!state.data) root.innerHTML = '<div class="br-skeleton" role="status">正在读取对应日期的样本…</div>';
    if (page === 'boardeffects') {
      $('boardeffectsLabels').setAttribute('aria-busy', 'true');
      if (!state.data) $('boardeffectsLabels').innerHTML = notice('正在读取所选日期的标签预期…');
    }
    status.textContent = options.refresh ? '正在核验数据源并更新归档，历史缺失日可能需要稍久…' : '正在读取归档…';
    const params = new URLSearchParams();
    if ($(page+'Date').value) params.set('date', $(page+'Date').value);
    if (page === 'boardeffects') params.set('days', $('boardeffectsDays').value);
    const endpoint = page === 'firstboard' ? 'first-board' : 'board-effects';
    const timer = setTimeout(() => controller.abort(), options.refresh ? 240000 : 75000);
    try {
      const response = await fetch(`/api/v1/review/${endpoint}${options.refresh ? '/refresh' : ''}?${params}`, {method: options.refresh ? 'POST' : 'GET', signal: controller.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求未完成，请检查日期或稍后重试。');
      if (seq !== state.seq) return;
      state.data = data;
      $(page+'Date').value = data.date || data.as_of || data.window_to || $(page+'Date').value;
      state.key = queryKey(page);
      if (page === 'firstboard') {
        notes = new Map((data.notes || []).map(n => [n.code, n]));
        if (options.refresh) window.BoardDynamics?.invalidate(data.date);
        renderFirst(); renderLadder();
      } else renderEffects();
      const failed = (data.refresh_report || []).filter(r => ['unavailable','partial'].includes(r.status)).length;
      status.textContent = page === 'firstboard' ? `${data.date || '—'} · ${data.snapshot_mode === 'live' ? '盘中快照' : '已归档'} · ${sourceName(data.source)}` : `${data.window_from || '—'} 至 ${data.window_to || '—'} · 有效反馈 ${data.days_used}/${data.calendar_days} 个交易日`;
      if (failed) status.textContent += ` · ${failed}项来源未补齐，缺失情况见下方。`;
    } catch (error) {
      if (seq !== state.seq) return;
      const message = error.name === 'AbortError' ? '读取超时，请重试。' : error.message;
      status.textContent = `${message}${state.data ? ' 当前保留同一查询上次成功的数据。' : ''}`;
      if (!state.data) root.innerHTML = notice(message, 'is-error');
      if (page === 'boardeffects' && !state.data) $('boardeffectsLabels').innerHTML = notice(message, 'is-error');
      if (page === 'firstboard' && !state.data && $('firstboardLadder')) $('firstboardLadder').innerHTML = notice(message, 'is-error');
    } finally {
      clearTimeout(timer);
      if (seq === state.seq) { state.controller = null; root.setAttribute('aria-busy', 'false'); button.disabled = false; if (page === 'boardeffects') $('boardeffectsLabels').setAttribute('aria-busy', 'false'); }
    }
  }
  function markdown(text) {
    return esc(text).split('\n').map(line => {
      const body = line.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      return /^#{1,4} /.test(line) ? `<h4>${body.replace(/^#{1,4} /, '')}</h4>` : `<p>${body || '<br>'}</p>`;
    }).join('');
  }
  function analysisRow(code) {
    const note = notes.get(code), running = active?.code === code;
    return `<tr class="br-analysis" id="br-analysis-${code}" ${expanded.has(code) ? '' : 'hidden'}><td colspan="12"><div class="br-analysis-head"><strong>首板证据研究</strong><span id="br-ai-status-${code}">${running ? '分析中…' : note ? `已归档 · ${esc(note.model)}` : '仅依据当日表内证据；本次不检索新闻。'}</span><button type="button" data-action="analyze" data-code="${code}">${note ? '重新分析' : '开始分析'}</button><button type="button" data-action="export" data-code="${code}" ${note ? '' : 'disabled'}>保存研究</button></div><div class="br-analysis-text" id="br-ai-text-${code}">${markdown(note?.text || '')}</div></td></tr>`;
  }
  function orderedFirstBoards(rows) {
    const amount = value => valid(value) && value >= 0 ? value : -1;
    // Stable ties preserve the dated API order (first seal, then stock code).
    return rows.slice().sort((a,b) => amount(b.auction_amount)-amount(a.auction_amount)
      || amount(b.seal_amount)-amount(a.seal_amount));
  }
  function renderFirst() {
    const d = pages.firstboard.data;
    if (!d) return;
    if (!d.available) { $('firstboardRoot').innerHTML = notice(d.reason || '暂无对应日期的数据。'); return; }
    $('firstboardRoot').innerHTML = `<div class="br-summary"><div><span>交易日</span><strong class="br-date">${esc(d.date)}</strong></div><div><span>涨停家数</span><strong>${d.total_zt}</strong></div><div><span>首板家数</span><strong>${d.first_count}</strong></div></div>${d.reason_note ? notice(d.reason_note) : ''}<div class="br-ledger"><div class="br-ledger-head"><div><h3>首板清单</h3><span>竞价成交金额↓ → 封单金额↓ · ${esc(d.ai?.model || '研究AI未配置')}</span></div><div><button type="button" id="br-batch" data-action="batch">逐个分析全部</button> <button type="button" id="br-stop" data-action="stop">停止</button></div><span id="br-batch-status" role="status" aria-live="polite"></span></div><div class="br-ledger-scroll"><table class="br-table"><thead><tr>${['股票','首次封板','炸板','价格','涨幅','成交额','竞价成交金额','封单金额','流通市值','涨停原因','行业','AI研究'].map(t=>`<th scope="col"${t==='竞价成交金额'?' aria-sort="descending" data-sort-priority="1" title="优先按竞价成交金额降序"':t==='封单金额'?' data-sort-priority="2" title="竞价成交金额相同时按封单金额降序"':''}>${t}</th>`).join('')}</tr></thead><tbody>${orderedFirstBoards(d.stocks).map(r => `<tr><td data-label="股票"><button type="button" class="br-stock" data-action="stock" data-code="${r.code}">${esc(r.name)}<span>${r.code}</span></button></td><td data-label="首次封板">${time(r.seal_time)}${r.one_price === true ? '<small>一字板</small>' : ''}</td><td data-label="炸板">${r.break_count ?? '—'}</td><td data-label="价格">${valid(r.price) ? r.price.toFixed(2) : '—'}</td><td data-label="涨幅">${colored(r.pct)}</td><td data-label="成交额">${money(r.amount)}</td><td data-label="竞价成交金额" title="09:25开盘集合竞价实际成交金额">${orderMoney(r.auction_amount)}</td><td data-label="封单金额" title="${d.snapshot_mode === 'live' ? '采集时' : '收盘归档'}涨停价买入挂单金额">${orderMoney(r.seal_amount)}</td><td data-label="流通市值">${money(r.float_cap)}</td><td data-label="涨停原因" class="br-reason">${esc(r.reason || '当日原因未覆盖')}</td><td data-label="行业">${esc(r.industry || '—')}</td><td data-label="AI研究"><button type="button" class="br-analyze" data-action="toggle" data-code="${r.code}" aria-expanded="${expanded.has(r.code)}" aria-controls="br-analysis-${r.code}">${notes.has(r.code) ? '查看研究' : '深入分析'}</button></td></tr>${analysisRow(r.code)}`).join('')}</tbody></table></div>${d.stocks.length ? '' : '<div class="br-empty">该日没有首板样本。</div>'}</div><p class="br-footnote">排序优先级：竞价成交金额降序，再按封单金额降序；缺失排在已知值之后。竞价成交金额：09:25开盘集合竞价实际成交；封单金额：${d.snapshot_mode === 'live' ? '采集时快照' : '收盘归档'}的涨停价买入挂单资金。${d.amount_coverage ? ` 竞价覆盖 ${d.amount_coverage.auction}/${d.amount_coverage.total}，封单覆盖 ${d.amount_coverage.seal}/${d.amount_coverage.total}。` : ''} 缺失显示“—”，可点击刷新首板补齐。 ${esc(d.reason_source_note || '涨停原因来自对应日期归档；未覆盖时留空。')} 首次封板时间与打板效应中的最终封板时间口径不同。未核验的形态不标记为换手确认。</p>`;
    updateControls();
  }
  function updateControls() {
    const ready = pages.firstboard.data?.ai?.ready;
    if ($('br-batch')) $('br-batch').disabled = !ready || !!active || batch;
    if ($('br-stop')) $('br-stop').disabled = !active && !batch;
    if ($('br-batch-status')) $('br-batch-status').textContent = batchProgress;
    $('firstboardRoot')?.querySelectorAll('[data-action="analyze"]').forEach(b => b.disabled = !ready || !!active || batch);
  }
  async function analyze(code, fromBatch = false) {
    if (active || (!fromBatch && batch) || !pages.firstboard.data?.ai?.ready) return false;
    const day = pages.firstboard.data.date, controller = new AbortController();
    const run = {code, day, controller}; active = run;
    expanded.add(code);
    const row = $('br-analysis-'+code); if (row) row.hidden = false;
    const toggle = $('firstboardRoot').querySelector(`[data-action="toggle"][data-code="${code}"]`);
    toggle?.setAttribute('aria-expanded', 'true');
    updateControls();
    const textNode = $('br-ai-text-'+code), statusNode = $('br-ai-status-'+code);
    statusNode.textContent = '分析中…';
    statusNode.classList.remove('br-analysis-error');
    let output = '', completed = false;
    const timer = setTimeout(() => controller.abort(), 150000);
    try {
      const response = await fetch('/api/v1/review/research/analyze', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({date:day,code}), signal:controller.signal});
      if (!response.ok || !response.body) throw new Error('研究服务未能启动，请稍后重试。');
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let pending = '';
      const consume = block => {
        const payload = block.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim()).join('\n');
        if (!payload) return;
        const e = JSON.parse(payload);
        if (e.type === 'error') throw new Error(e.message);
        if (e.type === 'delta') { output += e.text; textNode.innerHTML = markdown(output); }
        if (e.type === 'done' && e.note?.completed) {
          completed = true;
          if (pages.firstboard.data?.date === day && active === run) notes.set(code, e.note);
          statusNode.textContent = '已完成并归档';
        }
      };
      while (true) {
        const {value, done} = await reader.read();
        pending += decoder.decode(value, {stream:!done});
        pending = pending.replace(/\r\n/g, '\n');
        let split;
        while ((split = pending.indexOf('\n\n')) >= 0) { consume(pending.slice(0,split)); pending = pending.slice(split+2); }
        if (done) { if (pending.trim()) consume(pending); break; }
      }
      if (!completed) throw new Error('分析未完整完成，之前的研究记录仍保留。');
    } catch (error) {
      controller.abort();
      statusNode.textContent = error.name === 'AbortError' ? '已中止或超时，未完成内容不存档。' : error.message;
      statusNode.classList.add('br-analysis-error');
    } finally {
      clearTimeout(timer);
      if (active === run) active = null;
      if (completed && pages.firstboard.data?.date === day) {
        const button = $('firstboardRoot').querySelector(`[data-action="export"][data-code="${code}"]`);
        if (button) button.disabled = false;
        if (toggle?.isConnected) toggle.textContent = '查看研究';
      }
      updateControls();
    }
    return completed;
  }
  async function analyzeAll() {
    if (active || batch || !pages.firstboard.data?.ai?.ready) return;
    const day = pages.firstboard.data.date;
    const todo = orderedFirstBoards(pages.firstboard.data.stocks).filter(r=>!notes.has(r.code));
    batch = true; let done = 0;
    for (const row of todo) {
      if (!batch || pages.firstboard.data?.date !== day) break;
      batchProgress = `正在分析 ${done+1}/${todo.length} · ${row.name}`;
      updateControls();
      if (!await analyze(row.code, true)) { batchProgress = '当前分析未完成，批量已暂停，可重试。'; break; }
      done += 1;
    }
    if (batch && done === todo.length) batchProgress = `完成 ${done} 只，已有研究已跳过。`;
    batch = false; updateControls();
  }
  function saveNote(code) {
    const n = notes.get(code); if (!n) return;
    const blob = new Blob([`# ${n.name}（${code}）首板研究\n\n交易日：${n.date} · 模型：${n.model}\n\n${n.text}\n`], {type:'text/markdown;charset=utf-8'});
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = `${n.date}-${code}-首板研究.md`; link.click();
    setTimeout(()=>URL.revokeObjectURL(url), 1000);
  }
  function chart(group, metric) {
    const daily = group.daily, values = daily.flatMap(d => [d[metric], d.rolling[metric]]).filter(valid);
    const scale = Math.max(1, ...values.map(Math.abs)), step = 600/Math.max(1,daily.length);
    let path = '', drawing = false;
    const bars = daily.map((d,i) => {
      const x = (i+.5)*step;
      const roll = d.rolling;
      const complete = roll.expected_days === 7 && roll.covered_days === 7 && roll.sample === roll.sample_total;
      if (complete && valid(roll[metric])) { path += `${drawing ? 'L' : 'M'}${x.toFixed(2)},${(50-roll[metric]/scale*44).toFixed(2)} `; drawing = true; } else drawing = false;
      if (!d.available || !valid(d[metric])) return '';
      const y = 50-d[metric]/scale*44;
      return `<rect class="${d[metric] >= 0 ? 'br-bar-up' : 'br-bar-down'}" x="${(x-step*.44).toFixed(2)}" y="${Math.min(y,50).toFixed(2)}" width="${(step*.88).toFixed(2)}" height="${Math.max(.6,Math.abs(y-50)).toFixed(2)}"><title>${d.date} · ${pct(d[metric])} · n=${d.sample}</title></rect>`;
    }).join('');
    return `<div class="br-chart"><svg viewBox="0 0 600 100" role="img" aria-label="${esc(group.name)}每日${metric === 'avg' ? '均值' : '中位数'}和七日滚动，详见日数据"><line class="br-zero" x1="0" y1="50" x2="600" y2="50"/>${bars}<path class="br-roll" d="${path}"/></svg><div class="br-chart-caption"><span>${daily[0]?.date.slice(5) || '—'} — ${daily.at(-1)?.date.slice(5) || '—'}</span><span>红涨绿跌 · 蓝线7日滚动 · ±${scale.toFixed(1)}%</span></div></div>`;
  }
  function sparkline(g, metric) {
    return `<div class="br-sparkline" role="img" aria-label="${esc(g.name)}逐日${metric === 'avg' ? '均收益' : '收益中位数'}，详细数据可展开查看">${g.daily.map(d => `<span class="br-spark-day" title="${esc(d.date)} · ${pct(d[metric])} · n=${d.sample}"><i class="${tone(d[metric])}" style="height:${valid(d[metric]) ? Math.min(50, Math.abs(d[metric])*5) : 0}%;${d[metric] >= 0 ? 'bottom:50%' : 'top:50%'}"></i></span>`).join('')}</div><p class="br-spark-caption">逐日${metric === 'avg' ? '均收益' : '收益中位数'}（上红下绿 · 红涨绿跌）</p>`;
  }
  let effectsView = 'returns';
  function setEffectsView(view) {
    effectsView = view === 'labels' ? 'labels' : 'returns';
    $('boardeffectsRoot').hidden = effectsView !== 'returns';
    $('boardeffectsLabels').hidden = effectsView !== 'labels';
    for (const id of ['returns','labels']) {
      const tab = $('effects-'+id+'-tab');
      tab.setAttribute('aria-selected', String(effectsView === id)); tab.tabIndex = effectsView === id ? 0 : -1;
    }
  }
  document.addEventListener('keydown', e => {
    const tab = e.target.closest('.br-view-tabs [role="tab"]');
    if (!tab || !['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return;
    e.preventDefault();
    const tabs = [...tab.parentElement.querySelectorAll('[role="tab"]')], i = tabs.indexOf(tab);
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length-1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].click(); tabs[next].focus();
  });
  let focusView = 'list';
  function setView(view) {
    if (focusView === 'strength' && view !== 'strength') window.StrengthReview?.deactivate();
    if (focusView === 'strong' && view !== 'strong') window.StrongStocks?.deactivate();
    if (['strong','strength'].includes(view) && pages.firstboard.controller) {
      pages.firstboard.controller.abort(); pages.firstboard.controller=null; pages.firstboard.seq++;
      $('firstboardRoot').setAttribute('aria-busy','false');
    }
    focusView = view;
    if (view === 'strength') void window.StrengthReview?.load();
    if (view === 'strong') void window.StrongStocks?.load({cached:true});
    if (view === 'ladder') void window.BoardDynamics?.mountProbability($('firstboardLadder'), pages.firstboard.data?.date);
    $('firstboardRoot').hidden = view !== 'list';
    $('firstboardLadder').hidden = view !== 'ladder';
    $('firstboardStrong').hidden = view !== 'strong';
    $('firstboardStrength').hidden = view !== 'strength';
    $('firstboardRefresh').textContent = view === 'strong' ? '重新认证' : view === 'strength' ? '查询涨幅榜' : '刷新数据';
    for (const id of ['list','ladder','strong','strength']) { const tab=$('focus-'+id+'-tab'); tab.setAttribute('aria-selected',String(view === id)); tab.tabIndex=view===id ? 0 : -1; }
  }
  function shiftDate(direction) {
    if (focusView === 'strength') return window.StrengthReview?.shiftDate(direction);
    if (focusView === 'strong') return window.StrongStocks?.shiftDate(direction);
    const ladder = pages.firstboard.data?.ladder;
    const day = direction < 0 ? ladder?.previous_date : ladder?.next_date;
    if (!day || day > new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai'}).format(new Date())) return;
    $('firstboardDate').value = day; load('firstboard');
  }
  function renderLadder() {
    const d = pages.firstboard.data?.ladder;
    const root = $('firstboardLadder'); if (!root) return;
    $('firstboardNext').disabled = !d?.next_date || d.next_date > new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai'}).format(new Date());
    if (!d?.available) { root.innerHTML = notice(d?.reason || '天梯尚未归档'); return; }
    root.dataset.date = d.date;
    const showNext = d.next_status === 'observed';
    const stocks = (rows,current) => rows?.length ? `<div class="br-ladder-stock-grid">${rows.map(r=>`<div class="br-ladder-stock"><button type="button" data-action="stock" data-code="${esc(r.code)}" title="${esc(r.name)}"><span>${esc(r.name)}</span></button>${r.quote_status === 'suspended' ? '<span class="is-flat">停牌</span>' : colored(r.pct)}${current ? `<small class="bd-probability" data-board-probability="${esc(r.ticker)}">次日封板估计加载中…</small>` : ''}</div>`).join('')}</div>` : '<p class="br-ladder-empty">无晋级</p>';
    const cell = (title, rows, current=false) => `<div class="br-ladder-cell${current ? ' is-current' : ''}"><h3>${title} (${rows?.length || 0})</h3>${stocks(rows,current)}</div>`;
    const arrow = '<span class="br-ladder-arrow" aria-hidden="true">→</span>';
    root.innerHTML = `<p class="br-footnote">${esc(d.previous_date)} → ${esc(d.date)} · 昨日名单保留掉队股，两栏均展示 ${esc(d.date)} 的实际涨跌幅。${showNext ? ` 次日栏展示 ${esc(d.next_date)} 的晋级与涨跌幅。` : ''}</p><details class="bd-probability-note" data-probability-note><summary>次日封板估计 · 同板数、同市场类别的历史样本</summary></details><div class="br-ladder${showNext ? ' has-next' : ''}">${d.rows.map(r=>`<section class="br-ladder-level" aria-label="${r.level}进${r.level+1}">${cell('昨日'+(r.level === 1 ? '首板' : r.level+'板'),r.yesterday)}${arrow}${cell('今日'+(r.level+1)+'板',r.today,true)}${showNext ? arrow+cell('次日'+(r.level+2)+'板',r.next) : ''}</section>`).join('')}</div><details><summary>天梯来源与覆盖</summary><p class="br-footnote">${esc(d.source)}。${esc(d.reference?.note || '')}${d.reference?.available ? ` 同花顺返回${d.reference.returned_count}只连板，与完整池匹配${d.reference.matched_count}只。` : ' 同花顺参照待刷新，当前使用完整归档。'}</p>${d.rows.map(r=>`<p class="br-coverage">${r.level}进${r.level+1}：价格 ${r.observed}/${r.yesterday.length} · 晋级 ${rate(r.promotion_rate)}</p>`).join('')}</details>`;
    setView(focusView);
  }
  function dataTable(headers, rows) {
    return `<div class="br-detail-scroll"><table class="br-table"><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map((cell,i)=>`<td data-label="${esc(headers[i])}">${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  function priceCoverageText(q) {
    if (!q) return '';
    return `全天停牌 ${q.suspended} · 行情待补 ${q.unresolved}${q.conflicts ? ` · 记录冲突 ${q.conflicts}` : ''}`;
  }
  function renderPriceExceptions(d) {
    const rows = d.price_exceptions;
    if (!rows) return '';
    const suspended = rows.filter(r=>r.price_status === 'suspended').length;
    return `<details class="br-notice"><summary>行情核验 · 全天停牌 ${suspended} · 待核验 ${rows.length-suspended}</summary><p>按股票与反馈日去重。停牌仍保留在昨日名单里，次日收益显示“—”，不按0计算，也不挪用复牌日收益。</p>${rows.length ? dataTable(['股票','昨日名单日','反馈日','原因','涉及分组','核验依据'],rows.map(r=>[`${esc(r.name)} ${esc(r.code)}`,esc(r.cohort_date),esc(r.date),esc(r.price_reason || '次日行情待补齐'),esc((r.groups || []).join('、')),r.price_evidence_source === 'tushare_suspend_d' ? `Tushare 停复牌记录 · ${esc(r.price_evidence_date)}` : '日线及收益计价基准'])) : '<p>当前统计样本没有未解释的收益缺口。</p>'}</details>`;
  }
  function renderEffects() {
    const d = pages.boardeffects.data; if (!d) return;
    const metric = $('boardeffectsMetric').value, metricName = metric === 'avg' ? '均值' : '中位数';
    const curveMax = Math.max(1,...d.seal_curve.map(r=>Math.abs(r.avg || 0)));
    const curve = `<details class="br-card br-seal" open><summary>首板最终封板时间与次日收益</summary>${d.seal_curve.map(r=>`<div class="br-seal-row"><span>${esc(r.bucket)}</span><div class="br-seal-track">${valid(r.avg) ? `<i class="br-seal-bar ${tone(r.avg)}" style="width:${Math.abs(r.avg)/curveMax*70}%"></i>` : ''}${colored(r.avg)}</div><small>胜 ${rate(r.win_rate)} · n=${r.sample}</small></div>`).join('')}</details>`;
    $('boardeffectsRoot').innerHTML = `${d.available ? `<details class="br-notice"><summary>统计口径与数据说明${d.quality_note ? ' · 封板时间覆盖说明' : ''}</summary><p>${esc(d.sample_caveat)}</p><p>${esc(d.regime_source || "")}</p>${d.quality_note ? `<p>${esc(d.quality_note)}</p>` : ''}</details>` : notice(d.reason)}${d.missing_days.length || d.calendar_shortfall ? notice(`实际反馈 ${d.days_used}/${d.days_requested} 日。${d.calendar_shortfall ? `交易日历还缺 ${d.calendar_shortfall} 日。` : ''}${d.missing_days.length ? `尚无有效反馈：${d.missing_days.join('、')}` : ''}`, 'is-warning') : ''}${renderPriceExceptions(d)}${curve}<div class="br-grid">${d.strategies.map(g => {
      const o = g.overall, r = g.recent, p = g.prior, c = g.coverage;
      const segments = [['open_ret','开盘溢价'],['intraday_ret','开盘至收盘'],...(g.id === 'broken' ? [['limit_cost_ret','按昨日涨停价计价（理论）']] : [])];
      const industryGaps = g.id === 'industry' ? (g.daily || []).filter(day => day.available === false) : [];
      const industryCoverage = g.id === 'industry' && c.missing.length ? `${notice(`近${c.expected}个反馈交易日中，${c.days}日可按名单日的行业归属计算，另${c.missing.length}日证据未齐，未纳入行业组收益，也未记作0收益。`, 'is-warning')}<details class="br-card-details"><summary>查看行业分组缺口 · ${c.missing.length}日</summary><p>按首板发生日的完整涨停池，选出涨停数最多的两个东方财富三级行业，再计算其中首板股的次日表现；不是事件题材分组。</p><ul>${industryGaps.map(day => `<li>名单日 ${esc(day.cohort_date)} → 反馈日 ${esc(day.date)}：${esc(day.missing_reason || '名单或同日行业分类待核验')}</li>`).join('')}</ul></details>` : '';
      return `<article class="br-card"><header><h3>${esc(g.name)}</h3>${g.id === 'all' ? '' : g.id === 'broken' ? '<span class="br-excess">独立风险对照</span>' : valid(g.excess) ? `<span class="br-excess ${tone(g.excess)}">超额 ${pct(g.excess)}</span>` : '<span class="br-excess">超额待补齐</span>'}</header><p class="br-card-description">${esc(g.description).replaceAll('**','')}</p>${industryCoverage}
        <div class="br-main-values"><div><strong class="${tone(o.avg)}">${pct(o.avg)}</strong><small>期望（均值）</small></div><div><strong class="${tone(o.median)}">${pct(o.median)}</strong><small>中位数</small></div><div><strong>${rate(o.win_rate)}</strong><small>胜率</small></div></div>
        <div class="br-secondary"><span>样本 ${o.sample}</span><span>再涨停 <b>${rate(o.limit_up_rate)}</b></span><span>最好 ${colored(o.best)}</span><span>最差 ${colored(o.worst)}</span></div>
        <table class="br-regimes"><caption>分情绪环境 · 昨日名单日</caption><tbody>${Object.entries(g.by_regime).map(([name,s])=>`<tr><th scope="row">${esc(name)}</th><td>${colored(s.avg)}</td><td>胜 ${rate(s.win_rate)} · n=${s.sample}</td></tr>`).join('')}</tbody></table>
        ${g.regime_coverage ? `<p class="br-coverage">环境归组 ${g.regime_coverage.classified}/${g.regime_coverage.total} 条${g.regime_coverage.missing_cohort_dates.length ? ` · 名单日情绪待补齐：${g.regime_coverage.missing_cohort_dates.map(esc).join("、")}。这些股票仍计入总体收益，暂未归入强／中／弱环境；不表示缺少涨停名单。` : " · 按昨日名单形成日情绪"}</p>` : ""}
        ${sparkline(g, metric)}
        <div class="br-recent-line"><b>最近7日</b><span class="${tone(r[metric])}">${metricName} ${pct(r[metric])}</span><span>胜 ${rate(r.win_rate)}</span><span class="${tone(g.change[metric])}">较此前 ${pct(g.change[metric]).replace('%','pp')}</span></div>
        <details class="br-card-details"><summary>日趋势、收益分段与样本</summary>${chart(g,metric)}${g.industry_note ? `<p class="br-footnote">${esc(g.industry_note)}</p>` : ''}<p class="br-coverage">最近样本 ${r.sample} / 此前 ${p.sample} · 此前${c.prior_expected}日${metricName} ${pct(p[metric])}<br>价格覆盖 ${o.sample}/${o.sample_total}${g.price_coverage ? ` · ${priceCoverageText(g.price_coverage)}` : ''} · 名单覆盖 ${c.days}/${c.expected} 日${c.missing.length ? ` · 缺失 ${c.missing.join('、')}` : ''}</p>
        ${dataTable(['观测段','均值','中位数','样本'],segments.map(([field,label])=>[esc(label),colored(g.segments[field].avg),colored(g.segments[field].median),String(g.segments[field].sample)]))}
        <p class="br-footnote">开盘溢价=开盘/昨日收盘−1；开盘至收盘=收盘/开盘−1。两段中位数不可相加。超额以全体涨停均值为基准。</p>
        ${dataTable(['反馈日',...(g.id === 'industry' ? ['首板日 / 前二行业'] : []),metricName,'有效/全部','状态'],g.daily.map(s=>[s.date,...(g.id === 'industry' ? [esc(s.cohort_date)+'<br>'+esc((s.industry_coverage?.top_industries || []).map(t=>`${t.name}（${t.limit_up_count}只）`).join('、') || '待补齐')] : []),colored(s[metric]),`${s.sample}/${s.sample_total}`,!s.available ? esc(s.missing_reason || '缺失') : !s.sample_total ? '无样本' : s.price_coverage && (s.price_coverage.suspended || s.price_coverage.unresolved || s.price_coverage.conflicts) ? priceCoverageText(s.price_coverage) : s.sample<s.sample_total ? '部分缺失' : '完整']))}
        <details data-samples="${g.id}"><summary>查看原始样本（${o.sample_total}）</summary><div id="br-samples-${g.id}"></div></details></details></article>`;
    }).join('')}</div><details><summary>情绪日期与缺口 · 已覆盖 ${Object.values(d.regime_dates || {}).filter(r=>r.regime !== '环境未覆盖').length}/${Object.keys(d.regime_dates || {}).length} 日</summary><p class="br-footnote">${esc(d.regime_source || '')} 历史补算沿用同一规则；缺数据不会归为弱情绪。</p>${dataTable(['反馈日','名单日','市场情绪','分组','得分','状态'],Object.entries(d.regime_dates || {}).map(([date,r])=>[esc(date),esc(r.cohort_date),esc(r.label || '待补齐'),esc(r.regime),valid(r.score) ? String(r.score) : '—',esc(r.reason || '已覆盖')]))}</details><details><summary>数据来源与规则版本</summary><p class="br-footnote">规则 ${esc(d.rule_version)} · 反馈归档 ${d.archive?.feedback_days ?? 0}日 · 涨停池 ${d.archive?.up_pool_days ?? 0}日 · 炸板池 ${d.archive?.broken_pool_days ?? 0}日。行业分组采用首板发生日的东方财富三级行业，不能据此识别短线题材。样本量和分组构成变化需与收益一起看。</p>${dataTable(['反馈日','来源'],(d.sources || []).map(s=>[esc(s.date),esc(sourceName(s.source))]))}</details>`;
    if (window.ResearchEvidence) $('boardeffectsLabels').innerHTML = window.ResearchEvidence.expectations(d.label_expectations);
    setEffectsView(effectsView);
  }
  function samples(id, offset=0) {
    const group = pages.boardeffects.data?.strategies.find(g=>g.id===id); if (!group) return;
    const total = group.rows.length;
    $('br-samples-'+id).innerHTML = dataTable(['反馈日','昨日名单','股票',...(id === 'industry' ? ['首板日行业'] : []),'昨日板数','次日收益','行情状态'],group.rows.slice(offset,offset+50).map(r=>[r.date,r.cohort_date,`${esc(r.name)} ${r.code}`,...(id === 'industry' ? [esc(r.sector_name || '待补齐')] : []),r.prev_boards ?? '—',colored(r.ret),esc(r.price_reason || (valid(r.ret) ? '已覆盖' : '次日行情待补齐'))])) + `<p class="br-coverage">${total ? offset+1 : 0}–${Math.min(total,offset+50)} / ${total}</p><button type="button" data-action="samples" data-group="${id}" data-offset="${Math.max(0,offset-50)}" ${offset===0 ? 'disabled' : ''}>上一页</button> <button type="button" data-action="samples" data-group="${id}" data-offset="${offset+50}" ${offset+50>=total ? 'disabled' : ''}>下一页</button>`;
  }
  document.addEventListener('click', event => {
    const b = event.target.closest('.board-workspace [data-action]'); if (!b) return;
    const {action,code} = b.dataset;
    if (action === 'toggle') {
      if (expanded.has(code)) expanded.delete(code); else expanded.add(code);
      $('br-analysis-'+code).hidden = !expanded.has(code); b.setAttribute('aria-expanded',String(expanded.has(code)));
      if (expanded.has(code) && !notes.has(code) && !active && !batch) analyze(code);
    } else if (action === 'analyze') analyze(code);
    else if (action === 'batch') analyzeAll();
    else if (action === 'stop') stop();
    else if (action === 'export') saveNote(code);
    else if (action === 'samples') samples(b.dataset.group,Number(b.dataset.offset));
    else if (action === 'stock') {
      const d = pages.firstboard.data, stock = d?.stocks.find(r=>r.code===code) || d?.ladder?.rows.flatMap(r=>r.yesterday).find(r=>r.code===code);
      if (stock) { window.syncMarketDates?.(d.date); window.jumpToAnalysis?.(stock.ticker); }
    }
  });
  document.addEventListener('toggle', event => {
    const el = event.target;
    if (el.open && el.dataset?.samples) samples(el.dataset.samples);
  }, true);
  window.BoardResearch = {setView, shiftDate,load, renderEffects, setEffectsView, stop};
})();
