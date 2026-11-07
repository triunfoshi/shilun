/* Shared dated cycle ledger. No page owns a second cycle calculation. */
(() => {
  'use strict';
  const states = new WeakMap();
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct = value => typeof value === 'number' && Number.isFinite(value) ? `${value > 0 ? '+' : ''}${(value*100).toFixed(2)}%` : '缺数据';
  const labels = {fermentation:'发酵', strengthening:'加强', disagreement:'分歧', recovery:'回流', retreat:'退潮'};
  const diagnosticLabels = {data_missing:'数据待补齐', mapping_failed:'事件归因待修复', attribution_pending:'原因待核验', evidence_conflict:'来源有冲突', rule_unmatched:'阶段规则未满足', task_incomplete:'归档未完成'};
  function fieldText(field) {
    const names = {settled_up_pool:'收盘涨停池',dc_members:'旧版东方财富成分',event_reason_members:'同日事件归因样本',daily_bar:'日线',official_up_limit:'官方涨停价',trading_volume:'成交量',adjusted_close_chain:'复权收盘链',fixed_cohort_three_session_returns:'固定样本近三日收益',pool_vs_daily_limit:'收盘涨停池与日线核验',limit_reason:'涨停原因','core_mapping/three_session_pools':'核心事件原因或近三日板池',suspended:'停牌反馈',missing:'行情缺失'};
    if (typeof field !== 'object') return String(field);
    if (!field.field) return `核心 ${pct(field.core_return)}，后排 ${pct(field.back_return)}，已确认跟随 ${field.new_follower_count ?? '缺数据'} 只`;
    const fields = Array.isArray(field.field) ? field.field : [field.field];
    return [field.date,field.ticker,field.code,fields.map(f=>names[f] || f).join('、')].filter(Boolean).join(' · ');
  }
  function diagnostics(rows) {
    return rows.length ? `<ul class="tc-issues">${rows.map(r => `<li><b>${esc(diagnosticLabels[r.code] || r.code)}</b> · ${esc(r.date)}：${esc(r.action)}<small>${esc(r.missing_or_conflicting_fields.map(fieldText).join('；'))}</small>${r.last_checked_at ? `<small>最近核验 ${esc(r.last_checked_at)}</small>` : ''}</li>`).join('')}</ul>` : '';
  }
  function table(headings, rows) {
    return `<div class="tc-scroll" tabindex="0" role="region" aria-label="可滚动的周期明细"><table><thead><tr>${headings.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map((v,i)=>`<td data-label="${esc(headings[i])}">${v}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  function feedback(cycle) {
    const rows = [];
    for (const [key,label] of [['front_feedback','固定前排'],['back_feedback','固定后排']]) {
      for (const r of cycle[key]?.rows || []) rows.push([esc(label), `${esc(r.name)}<small>${esc(r.ticker)}</small>`,pct(r.return),
        esc(({sealed:'涨停',not_sealed:'未涨停',missing:'缺数据',suspended:'停牌',unrestricted:'无涨跌幅限制'})[r.state] || r.state),r.opened == null ? '缺数据' : r.opened ? '触板未封住' : '—']);
    }
    return table(['队列','股票','当日涨幅','收盘状态','开板反馈'], rows);
  }
  function coreComparison(cycle) {
    const observation = cycle.member_observation;
    if (observation?.date !== cycle.date) return '';
    const cores = (observation.members || []).filter(r => r.role === 'core_candidate'
      && r.qualified_at && r.qualified_at <= cycle.date);
    if (!cores.length) return '';
    return `<h4>当日合格核心对照 · ${esc(cycle.date)}</h4><p>入选锚点用于跟踪本轮固定样本，不代表当前唯一龙头。下表保留同日已认证核心，分别展示各自表现；不按单日涨幅自动更换锚点。</p>${table(['股票','用途','认证日','最近涨停','当日涨幅'], cores.map(r => [
      `${esc(r.name)}<small>${esc(r.ticker)}</small>`,r.ticker === cycle.core.ticker ? '入选锚点' : '合格核心对照',
      esc(r.qualified_at),esc(r.latest_limit_date || '未确认'),
      pct(r.linkage?.date === cycle.date ? r.linkage.stock_return : null),
    ]))}`;
  }
  function excess(value, unit=true) {
    return !Number.isFinite(value) ? '待补' : `${value>0?'+':''}${(value*100).toFixed(2)}${unit?' 个百分点':''}`;
  }
  function postExitDetail(p) {
    if (!p) return '';
    return `<h4>退出后跟踪 · 截至 ${esc(p.date)}</h4><p>${esc(p.label)}。${esc(p.note)}</p><p>起点 ${esc(p.anchor_date)} 收盘 · ${esc(p.sessions)} 个交易日；同期 ${esc(p.benchmark.name)} ${pct(p.benchmark.return)}。</p>${table(['原样本','股票','退出后累计','较大盘超额','复盘日涨幅'],p.rows.map(r=>[esc(r.role),esc(r.name),pct(r.return),excess(r.excess_return),pct(r.today_return)]))}${p.status==='partial'?'<p>部分行情待补；保留已知数据，不据此改写板块退出结论。</p>':''}`;
  }
  function postExitOverview(data) {
    const cycles = data.archived.filter(c=>c.post_exit);
    if (!cycles.length) return '';
    return `<section class="tc-history tc-post-exit"><h4>板块退潮后 · 核心与后排跟踪</h4><p>截至 ${esc(data.date)}；以各方向退出日收盘为起点。板块退潮结论保留，核心相对强势单独观察。</p>${table(['方向 / 退出日','入选锚点','退出后累计','同期上证','超额（百分点）','原后排中位数','当前观察'],cycles.map(c=>{
      const p=c.post_exit;return [`${esc(c.name)}<small>${esc(c.exit_date)}</small>`,esc(p.core.name),pct(p.core.return),pct(p.benchmark.return),excess(p.core.excess_return,false),pct(p.back_return),esc(p.label)];
    }))}<p>抱团观察依据核心上涨、跑赢上证且强于原后排；只描述价格表现，不证明资金流入。逐股对照见下方历史周期。</p></section>`;
  }
  function cycleRow(c) {
    const m = c.metrics || {};
    const stage = c.cycle_stage ? labels[c.cycle_stage] : '待核验';
    const past = !c.cycle_stage && c.last_confirmed_date ? `上次确认 ${c.last_confirmed_date} ${labels[c.last_confirmed_stage] || ''}` : '';
    return `<details class="tc-cycle" data-cycle-id="${esc(c.id)}"><summary><span><b>${esc(c.name)}</b><small>入选锚点 ${esc(c.core.name)} · ${esc(c.entered_at)} 起${c.exit_date ? ` · ${esc(c.exit_date)} 退出` : ''}</small></span><span class="tc-stage" data-stage="${esc(c.cycle_stage || 'pending')}">${esc(stage)}</span><span>${c.exit_date?'退出日':''}固定核心 ${pct(m.core_return)}<small>固定后排 ${pct(m.back_return)}</small></span><span>${m.drawdown == null ? '回撤缺数据' : `收盘回撤 ${(m.drawdown*100).toFixed(2)}%`}<small>${m.drawdown_warning ? '达到15%预警' : '查看固定名单与历程'}</small></span></summary>
      <div class="tc-detail"><p>${esc(c.transition_reason)}${past ? `；${esc(past)}` : ''}</p>
      <p>${esc(c.date)} 近3日累计：固定核心 ${pct(m.core_window_return)} · 固定后排中位数 ${pct(m.back_window_return)}；该日新增归因明确的跟随 ${m.new_follower_count == null ? '缺数据' : `${esc(m.new_follower_count)} 只`}。${c.exit_reason ? `退出理由：${esc(c.exit_reason)}` : ''}</p>
      ${diagnostics(c.diagnostics || [])}
      ${coreComparison(c)}
      <h4>固定前后排 · ${esc(c.cohort_date)} 形成</h4><p>后续即使掉队、移出事件样本或停牌也保留；缺行情不计零。开板反馈依据当日日线最高价与官方涨停价，只判定触板未封住，不推算开板次数。</p>${feedback(c)}
      <details><summary>逐日阶段与转换理由（${c.history.length} 日）</summary>${table(['交易日','阶段','核心涨幅','后排中位涨幅','理由'], c.history.map(r=>[esc(r.date),esc(r.stage_label),pct(r.metrics.core_return),pct(r.metrics.back_return),esc(r.reason)]))}</details>
      <details><summary>入选证据与原始原因（${c.entry_evidence.length} 条）</summary>${table(['日期','股票','原始原因'], c.entry_evidence.map(e=>[esc(e.date),esc(e.name),esc(e.reason)]))}</details>
      <details><summary>${c.exit_date?'退出日':'当前'}成员观察（${c.member_observation?.members.length || 0} 只）</summary><p>${esc(c.member_observation?.scope_note || '')}历史事件样本与本次涨停归因分开；同日上涨只作旁证，不自动增加跟随数。</p>${table(['股票','最近涨停','原因状态','原始原因'], (c.member_observation?.members || []).map(r=>[`${esc(r.name)}<small>${esc(r.ticker)}</small>`,esc(r.latest_limit_date),esc(r.counting_note),esc(r.reason || '缺原因')]))}</details>
      ${postExitDetail(c.post_exit)}
      <small>题材 ${esc(c.primary_code)} · 周期 ${esc(c.id)}；来源标签：${esc(c.branches.flatMap(b=>b.source_topics?.length?b.source_topics.map(s=>`${s.name} ${s.code}`):[b.name]).join('、'))}</small></div></details>`;
  }
  function jobNotice(job) {
    if (!job || !['failed','running'].includes(job.status)) return '';
    if (job.status === 'running') return `<p class="tc-message" role="status">${esc(job.date || '')} 后台正在更新 · ${esc(job.stage_label || '')}</p>`;
    const missing=job.missing || [], groups=new Map();
    for (const row of missing) {
      const label=row.dataset==='limit_reasons' ? '同日涨停原因' : row.reason || row.dataset || '所需资料';
      if (!groups.has(label)) groups.set(label,[]);
      if (row.date) groups.get(label).push(row.date);
    }
    return `<div class="tc-job-status" role="status"><strong>${esc(job.date || '')} 周期归档更新受阻</strong><p>停止环节：${esc(job.stage_label || '数据核验')}。</p>${groups.size ? `<ul>${[...groups].map(([label,dates])=>`<li>${esc(label)}缺口：${esc([...new Set(dates)].join('、'))}</li>`).join('')}</ul><p>以上为当前任务报告的缺口；周期需要连续回放历史，补齐同日归档后再重新生成。</p>` : `<p>${esc(job.message || '查看任务诊断以确认缺失资料。')}</p>`}<small>上一完整版本保留。“重新读取”只检查现有归档，不会补齐历史资料。</small></div>`;
  }
  function repairNotice(job) {
    if (!job || !['queued','running','failed','complete'].includes(job.status)) return '';
    const labels={queued:'等待云端启动',running:'正在回补分析',failed:'回补分析未完成',complete:'回补分析已完成'};
    const p=job.progress;
    const progress=p ? Number(p.total)>0 ? ` · ${esc(p.done)}/${esc(p.total)}日${p.date ? ` · ${esc(p.date)}`:''}` : ' · 复用已有同日原因，无需新增回补' : '';
    return `<div class="tc-repair-status" role="status"><strong>${esc(job.date)} · ${labels[job.status]}</strong><p>${esc(job.stage_label || '')}${progress}</p>${job.message ? `<p>${esc(job.message)}</p>`:''}${job.missing?.length ? `<ul>${job.missing.map(r=>`<li>${esc(r.date)} · ${esc(r.reason || r.dataset)}</li>`).join('')}</ul>`:''}${job.reports?.length ? `<details><summary>已核验原因归档 · ${job.reports.length}日</summary><ul>${job.reports.map(r=>`<li>${esc(r.date)} · ${esc(r.count)}只 · ${esc(r.source)}</li>`).join('')}</ul></details>`:''}<small>云端后台任务；关闭页面不取消计算，完成后自动读取新归档。</small></div>`;
  }
  function taskNotice(data) {
    return repairNotice(data.repair_job)+(['queued','running'].includes(data.repair_job?.status) ? '' : jobNotice(data.job))+(data.repair_unavailable_reason ? `<p class="tc-message">${esc(data.repair_unavailable_reason)}</p>`:'');
  }
  function render(data, compact=false) {
    if (data.status === 'not_materialized') return `<p class="tc-message" role="status">${esc(data.message)}</p>${taskNotice(data)}`;
    const content = `${data.active.length ? data.active.map(cycleRow).join('') : '<p class="tc-message">没有已确认的当前方向；不为凑满三个而补入。</p>'}
      ${diagnostics(data.diagnostics.filter(r=>r.topic_id==='universe'))}
      ${postExitOverview(data)}
      <details class="tc-history"><summary>已退出的历史周期（${data.archived.length}）</summary>${data.archived.length ? data.archived.map(cycleRow).join('') : '<p>这段回放内尚无已确认退出的方向。</p>'}</details>
      <details class="tc-history"><summary>候选等待与筛选缺口（${data.waiting.length}）</summary>${data.waiting.length ? table(['候选','暂未进入当前名单的原因'], data.waiting.map(r=>[esc(r.name),esc(r.reason)])) : '<p>当日没有等待名额的合格新方向。</p>'}<p>${esc(data.selection_diagnostics.join('；') || '未发现额外筛选缺口')}</p></details>
      <details class="tc-history"><summary>周期规则与数据范围</summary><p>最多三个方向；新候选不挤掉正常观察中的方向。近三日核心与固定后排累计收益均为负、后排当天无涨停接力，连续三次观察后退出；收盘回撤达到15%且连续两次走弱，也确认退出。只有回撤预警不退出。连续两日修复才确认回流。</p><p>替换须旧方向连续两次走弱，新方向连续两日准入且当日有接力；退出后须连续两日重新准入且承接改善；若股票已进入新连板段，必须通过该段自身资格认证。固定后排仅指入选时归因明确的跟随队列，新增成员另列。</p><p>${esc(data.note)} 预热起点 ${esc(data.warmup_start)}。</p><p>${esc(data.scope_note)}</p></details>`;
    return `${taskNotice(data)}${compact ? `<details class="tc-compact"><summary>${esc(data.date)} · 当前 ${data.active.length} 个方向：${esc(data.active.map(c=>`${c.name}·${c.stage_label}`).join(' / ') || '无')} · 展开查看</summary>${content}</details>` : content}
      <p class="tc-meta">${esc(data.date)} · ${esc(data.rules.version)} · 归档 ${esc(data.revision)}</p>`;
  }
  async function mount(target, date='', options={}) {
    const root = typeof target === 'string' ? document.getElementById(target) : target;
    if (!root) return;
    const previous = states.get(root);
    previous?.controller.abort();
    previous?.postController?.abort();
    clearTimeout(previous?.pollTimer);
    const controller = new AbortController(), serial = (previous?.serial || 0)+1;
    const compact = options.compact ?? root.dataset.compact === 'true';
    const state = {controller,serial,date,compact,supported:previous?.supported,repairRunning:previous?.repairRunning};states.set(root,state);
    root.classList.add('theme-cycles');root.setAttribute('aria-busy','true');root.dataset.state='loading';
    if (!options.polling) root.innerHTML = '<header><h3>短线题材周期</h3><div class="tc-actions"><button type="button" data-tc-retry disabled>读取中…</button><button type="button" data-tc-repair disabled>回补并分析</button></div></header><div class="tc-body"><p role="status">正在读取所选日的周期归档…</p></div>';
    root.querySelector('[data-tc-retry]').onclick=()=>mount(root,state.date,{compact});
    root.querySelector('[data-tc-repair]').onclick=()=>repair(root,state);
    const timer = setTimeout(()=>controller.abort(),20000);
    try {
      const response = await fetch(`/api/v1/review/theme-cycles${date ? `?date=${encodeURIComponent(date)}` : ''}`,{signal:controller.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `读取失败（${response.status}）`);
      if (states.get(root)?.serial !== serial) return;
      if (date && data.date !== date) throw new Error('归档日期不一致，请重试');
      const body=root.querySelector('.tc-body');
      const key=e=>e.dataset?.cycleId || e.querySelector('summary')?.textContent.replace(/·\s*\d+日$/,'').trim();
      const opened=[...(body.querySelectorAll?.('details[open]') || [])].map(key);
      const active=document.activeElement, focusKey=active?.tagName==='SUMMARY' && body.contains?.(active) ? key(active.parentElement):null;
      body.innerHTML = render(data,compact);
      for (const detail of body.querySelectorAll?.('details') || []) {
        if (opened.includes(key(detail))) detail.open=true;
        if (focusKey && key(detail)===focusKey) detail.querySelector('summary')?.focus({preventScroll:true});
      }
      root.dataset.state=data.status === 'not_materialized' ? 'empty' : data.status === 'partial' ? 'partial' : 'success';
      root.dataset.date=data.date || '';root.dataset.revision=data.revision || '';
      state.date=data.date || state.date;
      state.supported=data.repair_supported===true;
      state.repairRunning=['queued','running'].includes(data.repair_job?.status);
      if (state.repairRunning) state.pollTimer=setTimeout(()=>mount(root,state.date,{compact,polling:true}),2500);
    } catch(error) {
      if (states.get(root)?.serial !== serial) return;
      if (options.polling) {
        const body=root.querySelector('.tc-body');
        if (!body.querySelector?.('.tc-poll-error')) body.insertAdjacentHTML?.('beforeend','<p class="tc-message tc-poll-error" role="status">进度读取暂时失败，云端任务继续运行；正在重新读取。</p>');
        state.pollTimer=setTimeout(()=>mount(root,state.date,{compact,polling:true}),5000);
      } else {
        root.dataset.state='error';root.querySelector('.tc-body').innerHTML=`<p class="tc-message" role="alert">${esc(error.name === 'AbortError' ? '读取超时，请重试。' : error.message)}</p>`;
      }
    } finally {
      clearTimeout(timer);
      if (states.get(root)?.serial === serial) {
        root.setAttribute('aria-busy','false');const button=root.querySelector('[data-tc-retry]');button.disabled=false;button.textContent='重新读取';
        const action=root.querySelector('[data-tc-repair]');action.disabled=!state.supported || state.repairRunning;
        action.textContent=state.repairRunning ? '云端回补分析中…':'回补并分析';
        action.title=state.supported ? '补齐缺失的同日涨停原因，再连续回放并发布题材周期':'云端归档或任务状态尚未读取，请先重新读取';
      }
    }
  }
  async function repair(root,state) {
    if (states.get(root)!==state || !state.date || !state.supported || state.repairRunning) return;
    state.repairRunning=true;
    const button=root.querySelector('[data-tc-repair]');button.disabled=true;button.textContent='正在提交回补…';
    const controller=new AbortController();state.postController=controller;
    const timeout=setTimeout(()=>controller.abort(),25000);
    try {
      const response=await fetch(`/api/v1/review/theme-cycles/repair?date=${encodeURIComponent(state.date)}`,{method:'POST',signal:controller.signal});
      const job=await response.json();
      if (!response.ok) throw new Error(job.detail || `任务提交失败（${response.status}）`);
      if (states.get(root)!==state) return;
      if (job.date && job.date!==state.date) throw new Error(`${job.date} 已有回补任务运行，请待其完成后再分析所选日。`);
      await mount(root,state.date,{compact:state.compact});
    } catch(error) {
      if (states.get(root)!==state) return;
      if (error.name==='AbortError') {
        // Submission may already have reached ECS: read the durable state before
        // allowing a retry. Every subsequent POST gets a fresh signal.
        await mount(root,state.date,{compact:state.compact});
        return;
      }
      state.repairRunning=false;button.disabled=false;button.textContent='重试回补分析';
      root.querySelector('.tc-body').innerHTML=`<p class="tc-message" role="alert">${esc(error.name==='AbortError'?'任务提交超时；请重新读取确认云端是否已经启动，避免重复提交。':error.message)}</p>`;
    } finally { clearTimeout(timeout); }
  }
  window.ThemeCycles = {mount, render};
  document.addEventListener('DOMContentLoaded',()=>{
    const url=new URLSearchParams(location.search);
    if(url.get('theme_cycles')==='1') {
      document.getElementById('nav-sentiment')?.click();
      const input=document.getElementById('sentimentDate');
      if(input && /^\d{4}-\d{2}-\d{2}$/.test(url.get('date')||'')) input.value=url.get('date');
      void mount('sentimentThemeCycles',input?.value || '');
      document.getElementById('sentimentThemeCycles')?.scrollIntoView({block:'start'});
    }
  });
})();
