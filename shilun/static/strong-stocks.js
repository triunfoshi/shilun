/* Independent post-close certification. Uses the existing board-workspace UI. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct = v => typeof v === 'number' && Number.isFinite(v) ? `${v > 0 ? '+' : ''}${(v*100).toFixed(2)}%` : '—';
  const label = {qualified:'已认证', pending:'条件形成中', failed:'未通过', unverified:'证据待补齐', inactive:'历史认证'};
  const states = {sealed:'收盘涨停', not_sealed:'未涨停', suspended:'全天停牌', unrestricted:'无涨跌幅限制', missing:'证据缺失'};
  let data = null, filter = 'qualified', seq = 0, timer, controller;
  const root = () => $('firstboardStrong');
  function summary(row) {
    const leg = row.leg || {}, cycle = row.cycle || {};
    if (row.status === 'qualified') return `${leg.qualification_sessions ?? '—'}天${leg.qualification_board_count ?? '—'}板${!row.tracking_complete ? ' · 当前走势证据待补齐' : cycle.drawdown_warning ? ' · 收盘回撤达到15%' : ''}`;
    if (row.status === 'unverified' && leg.qualified_at) return '历史资格保留；当前走势有缺失证据，暂不列入已认证名单';
    return (leg.reasons || []).join('；') || '旧走势已结束，资格记录保留';
  }
  function render() {
    if (!data || data.status !== 'ready') {
      root().innerHTML = `<p class="br-notice">${esc(data?.reason || '尚无所选日认证记录。')}</p>`;
      return;
    }
    const drawdownSize = row => typeof row.cycle?.drawdown === 'number' && Number.isFinite(row.cycle.drawdown) ? Math.abs(row.cycle.drawdown) : null;
    const rows = data.rows.filter(r => filter === 'all' || r.status === filter).sort((a,b)=>{
      const x=drawdownSize(a),y=drawdownSize(b);
      return x===null ? y===null ? 0 : 1 : y===null ? -1 : y-x;
    });
    const headers = ['股票 / 行业','认证状态 / 形成日','资格段 / 三连板','十日窗口','走势首板','走势累计涨幅','收盘回撤','逐日证据'];
    root().innerHTML = `<section class="ss-certification"><header class="ss-intro"><h3>核心资格</h3><p>${esc(data.rule)}</p><p>行业为复盘日东方财富三级行业；事件题材另见题材事件树。分类缺失不改变核心资格认证结果。</p></header>
      <div class="ss-controls"><label>名单<select id="strongStockFilter">${[['qualified','已认证'],['pending','条件形成中'],['failed','未通过'],['unverified','证据待补齐'],['inactive','历史认证'],['all','全部记录']].map(([v,n])=>`<option value="${v}" ${filter===v?'selected':''}>${n}${v==='all'?'':`（${data.counts[v] || 0}）`}</option>`).join('')}</select></label><span>${esc(data.date)} 收盘 · 扫描 ${data.scanned_stocks} 只 · 当日封板证据覆盖 ${data.latest_known_stocks}/${data.scanned_stocks}</span><span>按收盘回撤幅度从高到低 · 缺失置后</span></div>
      <div class="br-ledger"><div class="br-ledger-scroll"><table class="br-table ss-table"><thead><tr>${headers.map(h=>`<th scope="col"${h==='收盘回撤'?' aria-sort="descending"':''}>${h}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>{
        const l=r.leg || {}, c=r.cycle || {};
        return `<tr><td data-label="股票 / 行业"><button class="br-stock" type="button" data-ss-stock="${esc(r.ticker)}">${esc(r.name)}<span>${esc(r.ticker)}</span></button><small>${esc(r.industry || '分类待补齐')}</small></td>
          <td data-label="认证状态 / 形成日" class="ss-reason"><strong>${label[r.status] || '待核验'}</strong><small>${esc(summary(r))}</small>${l.qualified_at?`<small class="ss-date">${esc(l.qualified_at)}</small>`:''}</td>
          <td data-label="资格段 / 三连板">${esc(l.start_date || '—')}<small class="ss-date">三板 ${esc(l.three_board_date || '尚未形成')}</small></td>
          <td data-label="十日窗口">${l.observed_sessions ?? '—'}/10日 · ${l.board_count ?? '—'}板<small class="ss-date">截止 ${esc(l.window_end || '日历待补齐')}</small></td>
          <td data-label="走势首板">${esc(c.start_date || '—')}${c.start_date && !c.boundary_confirmed?'<small>起点边界待核验</small>':''}</td>
          <td data-label="走势累计涨幅">${pct(c.cumulative_return)}</td><td data-label="收盘回撤">${pct(c.drawdown)}${c.drawdown_warning?'<small>转弱提醒</small>':''}</td>
          <td data-label="逐日证据"><button type="button" data-ss-detail="${esc(r.detail_id)}" data-ss-ticker="${esc(r.ticker)}">查看证据</button></td></tr>`;
      }).join('')}</tbody></table></div>${rows.length?'':`<p class="br-notice">该日没有“${esc(label[filter] || '此类')}”记录。</p>`}</div>
      <section id="strongStockDetail" class="ss-detail" aria-live="polite" hidden></section>
      <p class="br-footnote">${esc(data.scope)} 回看 ${data.history_sessions} 个交易日，起于 ${esc(data.history_from)}；早于可核验窗口的起点保留缺口。${esc(data.cycle_rule)} ${esc(data.source_note)}</p></section>`;
    $('strongStockFilter').addEventListener('change', e => {filter=e.target.value; render();});
  }
  async function request(path, options={}) {
    const response = await fetch(path, options), body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail==='string'?body.detail:'请求未完成，请稍后重试。');
    return body;
  }
  async function load(options={}) {
    clearTimeout(timer);
    if (options.latest) $('firstboardDate').value='';
    const requested = $('firstboardDate').value;
    if (options.cached && data?.date===requested && !['queued','running'].includes(data.job?.status)) { render(); return; }
    controller?.abort(); controller = new AbortController();
    const currentController=controller, current=++seq, signal=controller.signal, params=new URLSearchParams();
    if (requested) params.set('date',requested);
    if (data?.date!==requested) {data=null; root().innerHTML='<p class="br-skeleton" role="status">正在读取所选日认证…</p>';}
    root().setAttribute('aria-busy','true'); $('firstboardRefresh').disabled=true;
    const timeout=setTimeout(()=>currentController.abort(),75000);
    try {
      if (options.refresh) await request(`/api/v1/review/strong-stocks/rebuild?${params}`,{method:'POST',signal});
      const next=await request(`/api/v1/review/strong-stocks?${params}`,{signal});
      if (current!==seq) return;
      data=next; $('firstboardDate').value=data.date; render();
      const running=['queued','running'].includes(data.job?.status);
      $('firstboardStatus').textContent=running?'正在后台认证；完成后自动更新，关闭页面不取消计算。':data.job?.status==='failed'?data.job.error:data.status==='ready'?`${data.date} 核心资格核验完成 · ${data.counts.qualified || 0}只已认证`:data.reason;
      if (running && !root().hidden) timer=setTimeout(()=>load(),3500);
    } catch (error) {
      if (current!==seq) return;
      const message=error.name==='AbortError'?'读取超时，请重试。':error.message;
      $('firstboardStatus').textContent=message;
      if (!data) root().innerHTML=`<p class="br-notice is-error" role="alert">${esc(message)}</p>`;
    } finally {
      clearTimeout(timeout);
      if (current===seq) {root().setAttribute('aria-busy','false'); $('firstboardRefresh').disabled=false;}
    }
  }
  async function showDetail(id, button) {
    const current=seq, panel=$('strongStockDetail');
    panel.hidden=false; panel.innerHTML='<p role="status">正在读取逐日证据…</p>'; button.disabled=true;
    try {
      const d=await request(`/api/v1/review/strong-stocks/detail?${new URLSearchParams({detail_id:id})}`);
      if (current!==seq || panel!==$('strongStockDetail')) return;
      panel.innerHTML=`<h3>${esc(d.name)} · 逐段认证记录</h3><p>当前连板 ${d.current_streak}${d.current_streak_boundary_confirmed?'':'（起点待核验）'} · 最近涨停 ${esc(d.latest_limit_date || '—')} · ${esc(d.latest_shape || '当日非涨停')}</p>${d.cycles.map(c=>`<details ${c.status==='active'?'open':''}><summary>走势首板 ${esc(c.start_date)} · ${c.status==='active'?'跟踪中':`结束于 ${esc(c.ended_at)}`} · 累计 ${pct(c.cumulative_return)}</summary><p>${esc(c.end_reason || '')} 走势最高连板 ${c.max_streak}；收盘峰值 ${c.peak_close_on_start_scale?.toFixed(2) || '—'}（首板价格尺度），回撤 ${pct(c.drawdown)}。${c.price_missing_dates?.length?`昨收或价格缺口：${esc(c.price_missing_dates.join('、'))}。`:''}${c.missing_dates.length?`日级证据缺口：${esc(c.missing_dates.join('、'))}。`:''}${!c.boundary_confirmed?'走势起点未闭合，累计涨幅不作完整认证。':''}</p></details>`).join('')}
        ${d.legs.slice().reverse().map(l=>`<details ${l.qualified_at?'open':''}><summary>${esc(l.start_date)} 资格段 · ${label[l.status]}${l.qualified_at?` · ${l.qualification_sessions}天${l.qualification_board_count}板，${esc(l.qualified_at)}认证`:''}</summary><p>窗口 ${esc(l.start_date)} 至 ${esc(l.window_end || '待日历补齐')}；三连板确认 ${esc(l.three_board_date || '尚未形成')}。${esc(l.reasons.join('；'))}</p><div class="br-detail-scroll"><table class="br-table ss-evidence"><thead><tr>${['交易日','状态','收盘 / 涨停价','当日昨收','一字 / 非一字','量比（较上个有量日）'].map(t=>`<th scope="col">${t}</th>`).join('')}</tr></thead><tbody>${l.evidence.map(e=>`<tr><td data-label="交易日">${esc(e.date)}</td><td data-label="状态">${esc(states[e.state])}</td><td data-label="收盘 / 涨停价">${e.close?.toFixed(2) || '—'} / ${e.up_limit?.toFixed(2) || '—'}</td><td data-label="当日昨收">${e.pre_close?.toFixed(2) || '—'}</td><td data-label="一字 / 非一字">${esc(e.seal_shape || '—')}</td><td data-label="量比">${e.volume_ratio?.toFixed(2) || '—'}</td></tr>`).join('')}</tbody></table></div></details>`).join('')}`;
      panel.scrollIntoView({block:'nearest'});
    } catch(error) {if(current===seq) panel.innerHTML=`<p class="br-notice is-error">${esc(error.message)}</p>`;}
    finally {button.disabled=false;}
  }
  document.addEventListener('click',event=>{
    const stock=event.target.closest('[data-ss-stock]'), detail=event.target.closest('[data-ss-detail]');
    if (stock) {window.syncMarketDates?.(data.date); window.jumpToAnalysis?.(stock.dataset.ssStock);}
    if (detail) showDetail(detail.dataset.ssDetail,detail);
  });
  function shiftDate(direction) {
    const day=direction<0?data?.previous_date:data?.next_date;
    if(day && day<=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai'}).format(new Date())) {$('firstboardDate').value=day; load();}
  }
  function deactivate() {clearTimeout(timer); controller?.abort(); seq++; root().setAttribute('aria-busy','false'); $('firstboardRefresh').disabled=false;}
  window.StrongStocks={load,shiftDate,deactivate};
})();
