/* Dated Vibe-inspired context; independent from the four-stage snapshot score. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const valid=v=>typeof v==='number'&&Number.isFinite(v);
  const number=v=>valid(v)?String(v):'—';
  const rate=v=>valid(v)?`${(v*100).toFixed(1)}%`:'—';
  const time=v=>/^\d{6}$/.test(v||'')?`${v.slice(0,2)}:${v.slice(2,4)}`:'—';
  const help=(label,note)=>`<details class="rsc-help"><summary aria-label="${esc(label)}统计口径">口径</summary><p>${esc(note)}</p></details>`;
  const head=(title,note)=>`<header class="rsc-head"><h3>${title}</h3>${help(title,note)}</header>`;
  function cyclePanel(c,date) {
    const series=c.series||[], scores=series.filter(row=>valid(row.score));
    return `<article class="rsc-panel rsc-cycle">${head('情绪周期',c.note||'仅使用所选日及以前完整收盘池；不以盘中快照代替。')}
      <p class="rsc-subtitle">近${number(c.window||c.requested_window||10)}个交易日 · 相对热度，分值0—1</p>
      ${c.available?`<div class="rsc-cycle-facts"><div><strong>第${number(c.day_n)}天</strong><span>窗口低点 ${esc(c.trough_date)}</span></div><div><strong>${valid(c.current_score)?c.current_score.toFixed(2):'—'}</strong><span>${esc(c.trend)}</span></div></div>
      <p class="rsc-cycle-position">${valid(c.range_position)?`位于窗口高低区间的 ${rate(c.range_position)} 位置`:'窗口分值相同，区间位置无定义'}${c.tie_count>1?`；${number(c.tie_count)}日并列低点，取最早一日`:''}</p>`:
      `<p class="rsc-missing" role="status">${esc(c.reason||'同日历史数据不足，周期位置待确认。')}</p>`}
      ${scores.length?`<div class="rsc-score-chart" aria-label="情绪相对分逐日变化">${series.map(row=>`<div class="rsc-score-day ${row.date===c.trough_date?'is-trough':''} ${row.date===date?'is-current':''}"><div class="rsc-score-track" tabindex="0" title="${esc(row.date)} · 相对分 ${valid(row.score)?row.score.toFixed(3):'缺失'} · 涨停 ${number(row.limit_up)} · 最高 ${number(row.highest_consec)}板 · 炸板率 ${rate(row.broken_rate)}" aria-label="${esc(row.date)}情绪相对分${valid(row.score)?row.score.toFixed(3):'缺失'}"><i style="height:${valid(row.score)?Math.max(3,Math.min(100,row.score*100)):0}%"></i></div><span class="rsc-date">${esc(row.date.slice(5,7))}<br>${esc(row.date.slice(8))}</span></div>`).join('')}</div><p class="rsc-chart-key"><span>■</span> 窗口低点 · 末列为${esc(date)}</p>`:''}
      <p class="rsc-caveat">窗口低点仅作启发式起点，不是精确周期划分；不同题材可以同时启动、高潮或退潮。</p>
      ${(c.missing||[]).length?`<details class="rsc-details"><summary>缺失日期与字段 · ${c.missing.length}日</summary><ul>${c.missing.map(row=>`<li>${esc(row.date)} · ${esc(row.reason)}</li>`).join('')}</ul></details>`:''}
      ${series.length?`<details class="rsc-details"><summary>逐日原始读数 · ${series.length}日</summary><div class="rsc-table-scroll" role="region" aria-label="情绪周期逐日读数" tabindex="0"><table><thead><tr><th>日期</th><th>涨停</th><th>最高板</th><th>炸板率</th><th>相对分</th></tr></thead><tbody>${series.map(row=>`<tr><th scope="row">${esc(row.date)}</th><td>${number(row.limit_up)}</td><td>${number(row.highest_consec)}</td><td>${rate(row.broken_rate)}</td><td>${valid(row.score)?row.score.toFixed(3):'—'}</td></tr>`).join('')}</tbody></table></div></details>`:''}
    </article>`;
  }
  function industryPanel(s,date) {
    const rows=s.themes||[], timeline=s.timeline||[], max=Math.max(1,...timeline.map(row=>valid(row.count)?row.count:0));
    return `<article class="rsc-panel rsc-industry">${head('行业结构 · 发酵节奏',s.note||'按当日涨停池所属行业归组，不把行业分类当作真实事件题材。')}
      <p class="rsc-subtitle">${esc(date)} · 所属行业视角，非事件题材</p>
      ${!s.available?`<p class="rsc-missing" role="status">${esc(s.reason||'同日完整涨停池待补齐。')}</p>`:
      `${rows.length?`<div class="rsc-industry-list">${rows.map(row=>`<div class="rsc-industry-row"><div class="rsc-industry-name"><strong>${esc(row.sector)}</strong><span>${number(row.limit_up)}只 · 最高${number(row.highest)}板</span></div><div class="rsc-industry-track" aria-hidden="true"><i style="width:${valid(s.total_limit_up)&&s.total_limit_up>0?Math.max(0,Math.min(100,row.limit_up/s.total_limit_up*100)):0}%"></i></div><div class="rsc-industry-meta"><span>首板${number(row.first_boards)} / 连板${number(row.consec_boards)}${row.unknown_height_n?` / 高度缺${number(row.unknown_height_n)}`:''} · 炸${number(row.broken)} · ${row.time_valid_n<row.limit_up?"已知首封":"首封"} ${time(row.first_seal)}${row.last_seal&&row.last_seal!==row.first_seal?`—${time(row.last_seal)}`:''}</span><details class="rsc-members"><summary>样本</summary><p>${row.names.map(stock=>`${esc(stock.name)} ${stock.boards==null?'板数缺失':`${number(stock.boards)}板`}`).join('、')}${row.limit_up>row.names.length?'；其余样本未列':''}</p></details></div></div>`).join('')}</div>`:'<p class="rsc-missing">当日完整池内无涨停样本。</p>'}
      ${timeline.length?`<div class="rsc-timeline"><strong>首次封板时间分布</strong><div class="rsc-time-chart">${timeline.map(row=>`<div><div class="rsc-time-track" title="${esc(row.slot)}：${number(row.count)}只"><i style="height:${valid(row.count)?row.count/max*100:0}%"></i></div><b>${number(row.count)}</b><span>${esc(row.slot)}</span></div>`).join('')}</div></div>`:''}
      <p class="rsc-coverage">行业有效 ${number(s.industry_valid_n)}/${number(s.total_limit_up)} · 首封时间有效 ${number(s.time_valid_n)}/${number(s.total_limit_up)}${s.missing_time_n?`，缺${number(s.missing_time_n)}只未分时段`:''}${!s.broken_available?' · 炸板池缺失，炸板家数未知':''}。</p>
      <p class="rsc-coverage">共${number(s.industry_count)}个已知行业${s.unclassified_n?`，${number(s.unclassified_n)}只未分类`:""}${rows.length<s.industry_count?`，展示前${rows.length}个`:''}${valid(s.concentration)?`；头部占全部涨停 ${rate(s.concentration)}`:''} · ${esc(s.source||'来源待核验')}</p>`}
    </article>`;
  }
  function render(data) {
    return `<div class="rsc-layout">${cyclePanel(data.cycle||{},data.date)}${industryPanel(data.industry||{},data.date)}</div>`;
  }
  async function mount(target,date,force=false) {
    const root=typeof target==='string'?document.getElementById(target):target;
    if(!root||!date)return;
    const token={};root.rscRequest=token;root.setAttribute('aria-busy','true');
    root.innerHTML='<p class="review-source-note" role="status">正在读取同日情绪周期与行业结构…</p>';
    try {
      const data=await window.fetchJson(`/api/v1/review/sentiment-context?date=${encodeURIComponent(date)}`,{timeoutMs:45000});
      if(root.rscRequest!==token)return;
      if(data.date!==date)throw new Error('返回日期与所选日不一致');
      root.innerHTML=render(data);root.dataset.date=data.date;
    }catch(error) {
      if(root.rscRequest!==token)return;
      root.innerHTML=`<p class="rsc-missing" role="alert">情绪周期与行业结构读取失败：${esc(error.message)}</p><button class="secondary" type="button">重试读取</button>`;
      root.querySelector('button').onclick=()=>mount(root,date,true);
    }finally{if(root.rscRequest===token)root.setAttribute('aria-busy','false');}
  }
  window.SentimentContext={mount,render};
})();
