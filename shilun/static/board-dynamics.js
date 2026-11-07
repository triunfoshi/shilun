/* Polanyi: put persistence next to today's facts. Hallmark: existing Workbench tokens, component scope. */
(() => {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const valid = v => typeof v === 'number' && Number.isFinite(v);
  const pct = v => valid(v) ? `${(v*100).toFixed(1)}%` : '—';
  const count = v => valid(v) ? String(v) : '—';
  const returnPct = v => valid(v) ? `${v>0?'+':''}${v.toFixed(2)}%` : '—';
  const direction = v => !valid(v) || v===0 ? '' : v>0 ? 'bd-is-up' : 'bd-is-down';
  const factHeader = (title, note) => `<header class="bd-fact-head"><h3>${title}</h3><details class="bd-fact-help"><summary aria-label="${title}统计口径">口径</summary><p>${esc(note)}</p></details></header>`;
  function dayFacts(data) {
    const d = data.selected_day;
    if (!d || d.available===false || d.date !== data.date) return `<p class="bd-facts-unavailable">${esc(data.date)} 当日三项统计暂不可用。${esc(d?.reason || '尚未读取到所选日完整统计；请刷新趋势。')}</p>`;
    const m=d.money_effect || {}, p=d.promotion || {}, q=d.seal_quality || {}, overall=p.overall || {};
    const moneyGap = m.status==='missing' ? '昨日完整涨停名单缺失或范围不可比，不能确认赚钱效应。' : m.missing_n ? `${m.missing_n}只未计收益，其中停牌${m.suspended_n ?? 0}只；只描述已覆盖样本。` : m.total_n===0 ? '昨日无涨停样本。' : '';
    const qualityGap = q.total==null ? '当日完整收盘涨停池缺失或范围不可比。' : [
      q.break_count_valid_n !== q.total ? `炸板次数覆盖${count(q.break_count_valid_n)}/${count(q.total)}，未炸/回封总数及平均炸次待确认。` : '',
      q.seal_time_valid_n !== q.total ? `首封时间覆盖${count(q.seal_time_valid_n)}/${count(q.total)}，早封/尾封待确认。` : '',
      q.broken_pool==null ? '当日炸板未回封池未齐，炸板率待确认。' : ''
    ].filter(Boolean).join(' ');
    return `<div class="bd-facts-caption"><strong>当日评估 · ${esc(d.date)}</strong><span>${esc(d.scope)} · 昨日名单 ${esc(d.cohort_date || '未确认')} · 收盘事实</span></div>
      <div class="bd-day-facts">
        <article class="bd-fact-card bd-money-effect">
          ${factHeader('赚钱效应',m.note || '')}
          <p class="bd-fact-description">昨日涨停股，今天兑现得如何</p>
          <div class="bd-return-pair"><div><strong class="${direction(m.median_pct)}">${returnPct(m.median_pct)}</strong><span>收盘反馈中位数</span></div><div><strong class="${direction(m.mean_pct)}">${returnPct(m.mean_pct)}</strong><span>均值</span></div></div>
          <dl class="bd-fact-strip"><div><dt>翻红</dt><dd>${pct(m.up_rate)}<small>${count(m.up_count)}/${count(m.sample_n)}</small></dd></div><div><dt>再次涨停</dt><dd>${pct(m.repeat_limit_rate)}<small>${count(m.repeat_limit_n)}/${count(m.repeat_limit_base)}</small></dd></div></dl>
          <p class="bd-fact-coverage">收益有效 ${count(m.sample_n)} / ${count(m.total_n)} 只</p>
          ${moneyGap ? `<p class="bd-fact-gap">${esc(moneyGap)}</p>` : ''}
          ${m.exceptions?.length ? `<details class="bd-fact-exceptions"><summary>未计入收益的样本 · ${m.exceptions.length}只</summary><ul>${m.exceptions.map(r=>`<li>${esc(r.name)} <span>${esc(r.ticker)} · ${esc(r.reason)}</span></li>`).join('')}</ul></details>` : ''}
        </article>
        <article class="bd-fact-card bd-promotion">
          ${factHeader('晋级率','昨日完整收盘涨停名单冻结后，今天仍收盘涨停的比例。1进2、2进3、3板及以上按昨日连板高度分组；包含一字板，晋级不等于可成交机会。')}
          <p class="bd-fact-description">昨日梯队，今天有多少继续封板</p>
          <div class="bd-overall"><strong>${pct(overall.rate)}</strong><span>整体晋级 ${count(overall.promoted)}/${count(overall.base)}</span></div>
          <dl class="bd-promotion-stages">${(p.stages || []).map(r=>`<div><dt>${esc(r.label)}</dt><dd><span class="bd-progress ${valid(r.rate)?'':'is-unknown'}" aria-hidden="true">${valid(r.rate)?`<i style="width:${Math.max(0,Math.min(100,r.rate*100))}%"></i>`:''}</span><span>${count(r.promoted)}/${count(r.base)} <b>${pct(r.rate)}</b></span></dd></div>`).join('')}</dl>
          ${p.status==='missing' ? '<p class="bd-fact-gap">前后两日完整收盘池缺失或范围不可比，晋级率待确认。</p>' : p.unknown_height_n ? `<p class="bd-fact-gap">昨日${count(p.unknown_height_n)}只缺连板高度，未分档；整体仍保留。</p>` : '<p class="bd-fact-coverage">分母为昨日各档全部涨停股</p>'}
        </article>
        <article class="bd-fact-card bd-seal-quality">
          ${factHeader('封板质量',q.note || '')}
          <p class="bd-fact-description">同样封住涨停，过程是否稳定</p>
          <div class="bd-seal-pair"><div><strong>${count(q.never_broken)}</strong><span>未炸过 <b>${pct(q.never_broken_rate)}</b></span></div><div><strong>${count(q.reopened)}</strong><span>炸后回封</span></div></div>
          <p class="bd-fact-coverage">${valid(q.never_broken)&&valid(q.reopened)?`${q.never_broken} + ${q.reopened} = `:''}当日收盘涨停 ${count(q.total)} 家</p>
          <dl class="bd-seal-cross"><div><dt>首封≤09:35</dt><dd>${count(q.opening_seconds)}家</dd></div><div><dt>首封≥14:30</dt><dd>${count(q.late_seal)}家</dd></div></dl>
          <p class="bd-seal-cross-note">早封／尾封按首封时间交叉统计，不与上方相加；09:35仅为早封试算口径。</p>
          <dl class="bd-fact-strip"><div><dt>炸板率</dt><dd>${pct(q.broken_rate)}<small>未回封${count(q.broken_pool)} / 触板${count(q.attempted)}</small></dd></div><div><dt>平均炸次</dt><dd>${valid(q.avg_broken_times)?q.avg_broken_times.toFixed(2):'—'}<small>最终封板组</small></dd></div></dl>
          ${qualityGap ? `<p class="bd-fact-gap">${esc(qualityGap)}</p>` : `<p class="bd-fact-coverage">炸次／首封时间有效 ${count(q.break_count_valid_n)}／${count(q.seal_time_valid_n)}，共${count(q.total)}只</p>`}
        </article>
      </div>`;
  }
  const cache = new Map();
  function load(date, force=false) {
    const old = cache.get(date);
    if (!force && old && Date.now()-old.time < 60000) return old.promise;
    const promise = window.fetchJson(`/api/v1/review/board-dynamics?date=${encodeURIComponent(date)}`, {timeoutMs:75000});
    const entry = {time:Date.now(), promise};
    cache.set(date, entry);
    promise.catch(() => { if (cache.get(date) === entry) cache.delete(date); });
    return promise;
  }
  function plot(rows, field, title, index) {
    const xy = (r,i) => `${rows.length === 1 ? 500 : i/(rows.length-1)*1000},${100-r[field]*100}`;
    const segments = []; let segment = [];
    rows.forEach((r,i) => {
      if (valid(r[field])) {
        if (segment.length && r.scope !== rows[i-1]?.scope) { segments.push(segment); segment=[]; }
        segment.push(xy(r,i));
      }
      else if (segment.length) { segments.push(segment); segment=[]; }
    });
    if (segment.length) segments.push(segment);
    const meanRows = rows.slice(-7).filter(r=>valid(r[field]) && r.scope === rows.at(-1)?.scope);
    const mean = meanRows.length ? meanRows.reduce((s,r)=>s+r[field],0)/meanRows.length : null;
    const current = rows[index];
    const covered = rows.filter(r=>valid(r[field])).length;
    const count = field === 'promotion_rate' ? `${current?.promoted ?? '—'}/${current?.previous_up ?? '—'} 晋级` : `${current?.broken ?? '—'}/${current?.attempted ?? '—'} 炸板`;
    return `<article class="bd-metric ${field === 'broken_rate' ? 'bd-broken' : ''}"><header><h3>${title}</h3><strong>${pct(current?.[field])}</strong><span>${count} · 覆盖 ${covered}/${rows.length}日</span></header><div class="bd-chart"><div class="bd-axis"><span>100%</span><span>50%</span><span>0%</span></div><svg viewBox="-10 -4 1020 108" preserveAspectRatio="none" role="img" aria-label="${title}日趋势，缺失日期断开；逐日数值见下方表格">${[0,50,100].map(y=>`<path class="bd-gridline" d="M0 ${y} H1000"/>`).join('')}${segments.map(points=>`<polyline class="bd-line" points="${points.join(' ')}"/>`).join('')}${rows.map((r,i)=>valid(r[field]) ? `<circle class="bd-dot" cx="${xy(r,i).split(',')[0]}" cy="${100-r[field]*100}" r="2"><title>${esc(r.date)} ${pct(r[field])}</title></circle>` : '').join('')}${current && valid(current[field]) ? `<path class="bd-cursor" d="M${xy(current,index).split(',')[0]} 0 V100"/>` : ''}</svg></div><footer><span>${esc(rows[0]?.date?.slice(5) || '—')}</span><span>近7日同口径均值 ${pct(mean)} · 有效 ${meanRows.length}/7</span><span>${esc(rows.at(-1)?.date?.slice(5) || '—')}</span></footer></article>`;
  }
  function draw(root, data, days=30, index=null, includeFacts=true) {
    const rows = data.daily.slice(-days);
    index = index == null ? rows.length-1 : Math.max(0, Math.min(index,rows.length-1));
    root.innerHTML = `<header class="review-section-head"><div><h2>接力与封板 · 日趋势</h2><p>截至 ${esc(data.window_end || '尚无收盘归档')} · 收盘口径 · 有效观测不会随盘中快照变动</p></div><div class="bd-actions"><label>观察窗口<select data-bd-window aria-label="日趋势观察窗口"><option value="30" ${days===30?'selected':''}>近30日</option><option value="20" ${days===20?'selected':''}>近20日</option></select></label><button type="button" data-bd-refresh>刷新趋势</button></div></header>${includeFacts ? dayFacts(data) : ""}<div class="bd-charts">${plot(rows,'promotion_rate','晋级率',index)}${plot(rows,'broken_rate','炸板率',index)}</div><label class="bd-scrub">历史趋势查数 <output>${esc(rows[index]?.date || '暂无数据')}</output><input data-bd-day type="range" min="0" max="${Math.max(0,rows.length-1)}" value="${Math.max(0,index)}" aria-label="选择历史趋势查数日期；不改变上方当日评估" ${!rows.length?'disabled':''}></label><p class="review-source-note">晋级率＝昨日涨停且今日继续封板／昨日涨停；炸板率＝触板未封／（收盘封板＋触板未封）。统一沪深非ST范围，含首板、创业板、科创板；回封只计封板。只使用交易日，缺池仍断线，不补零。</p><details><summary>逐日数值 · 交易日 ${rows.length}/${days}日</summary><div class="review-table-wrap"><table class="review-table"><thead><tr><th>日期</th><th>晋级率</th><th>晋级／昨日涨停</th><th>炸板率</th><th>炸板／触板</th><th>来源与范围</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.date)}</td><td>${pct(r.promotion_rate)}</td><td>${r.promoted ?? '—'} / ${r.previous_up ?? '—'}</td><td>${pct(r.broken_rate)}</td><td>${r.broken ?? '—'} / ${r.attempted ?? '—'}</td><td>${esc(r.scope || '缺失')} · ${esc(r.source || '无归档')}</td></tr>`).join('')}</tbody></table></div></details>`;
    root.dataset.loadedDate = data.date;
    root.querySelector('[data-bd-window]').onchange = e => draw(root,data,Number(e.target.value),null,includeFacts);
    root.querySelector('[data-bd-refresh]').onclick = () => mountTrend(root,data.date,true,{facts:includeFacts});
    root.querySelector('[data-bd-day]').oninput = e => {
      const value = Number(e.target.value);
      root.querySelector('.bd-charts').innerHTML = plot(rows,'promotion_rate','晋级率',value)+plot(rows,'broken_rate','炸板率',value);
      root.querySelector('output').textContent = rows[value]?.date || '暂无数据';
    };
  }
  async function mountTrend(root, date, force=false, options={}) {
    if (!root || !date) return;
    const token = {}; root.bdRequest = token;
    root.setAttribute('aria-busy','true'); root.innerHTML = '<p role="status">正在读取接力与封板日趋势…</p>';
    try {
      const data = await load(date,force);
      if (root.bdRequest !== token) return;
      if (data.date !== date) throw new Error('返回日期不一致');
      draw(root,data,30,null,options.facts!==false);
    } catch (error) {
      if (root.bdRequest !== token) return;
      root.innerHTML = `<p role="alert">趋势读取失败：${esc(error.message)}</p><button type="button">重试日趋势</button>`;
      root.querySelector('button').onclick = () => mountTrend(root,date,true,options);
    } finally { if (root.bdRequest === token) root.setAttribute('aria-busy','false'); }
  }
  async function mountProbability(root,date) {
    if (!root || !date) return;
    const token = {}; root.bdRequest = token;
    const slots = [...root.querySelectorAll('[data-board-probability]')];
    if (!slots.length) return;
    try {
      const data = await load(date);
      if (root.bdRequest !== token || root.dataset.date !== date) return;
      if (data.date !== date) throw new Error('返回日期不一致');
      const note = root.querySelector('[data-probability-note]');
      if (note) note.innerHTML = `<summary>次日封板估计 · ${esc(data.next_date || '下一交易日')} · 计算口径</summary><p>${esc(data.probability_method)} 反馈窗口 ${esc(data.window_start)} 至 ${esc(data.window_end)}。</p><p>${esc(data.interval_note)}</p><p>* 表示低样本（不足30例）。同一分组共用估计；今日强弱、题材、封单与次日开盘均未纳入。历史回看也只使用截至所选日的数据。</p>`;
      if (note) {
        const groups = [...new Map(Object.values(data.estimates).map(r=>[`${r.market_group}/${r.boards}`,r])).values()].sort((a,b)=>b.boards-a.boards);
        note.innerHTML += `<div class="review-table-wrap"><table class="review-table"><thead><tr><th>分组</th><th>历史封板／有效</th><th>平滑估计</th><th>95%参考区间</th><th>缺失／停牌</th></tr></thead><tbody>${groups.map(r=>`<tr><td>${esc(r.market_group)} ${r.boards}板</td><td>${r.success} / ${r.sample}</td><td>${pct(r.probability)}</td><td>${r.interval ? r.interval.map(pct).join('—') : '样本不足'}</td><td>${r.missing} / ${r.suspended}</td></tr>`).join('')}</tbody></table></div>`;
      }
      slots.forEach(slot => {
        const row = data.estimates[slot.dataset.boardProbability];
        slot.textContent = row ? `${row.probability == null ? '样本不足' : '封板概率 '+pct(row.probability)} · ${row.sample}例${row.status==='low_sample'?'*':''}` : '收盘归档未齐';
        slot.title = row ? `${row.market_group} ${row.boards}板；封板 ${row.success}/${row.sample}；${row.interval ? '95%参考区间 '+row.interval.map(pct).join('—')+'；' : ''}缺失 ${row.missing}，停牌 ${row.suspended}` : '仅使用所选日完整收盘池';
      });
    } catch (error) {
      if (root.bdRequest !== token) return;
      slots.forEach(slot => { slot.textContent='估计暂不可用'; slot.title=error.message; });
      const note = root.querySelector('[data-probability-note]');
      if (note) { note.innerHTML='<summary>次日封板估计读取失败</summary><button type="button">重试估计</button>'; note.querySelector('button').onclick=()=>mountProbability(root,date); }
    }
  }
  async function mountFacts(root, date, force=false) {
    if (!root || !date) return;
    const token={}; root.bdRequest=token;
    root.setAttribute('aria-busy','true');
    root.innerHTML='<p role="status">正在读取当日赚钱效应、晋级率与封板质量…</p>';
    try {
      const data=await load(date,force);
      if(root.bdRequest!==token)return;
      if(data.date!==date)throw new Error('返回日期不一致');
      root.innerHTML=dayFacts(data);
      root.dataset.loadedDate=date;
    } catch(error) {
      if(root.bdRequest!==token)return;
      root.innerHTML=`<p role="alert">当日统计读取失败：${esc(error.message)}</p><button type="button">重试读取</button>`;
      root.querySelector('button').onclick=()=>mountFacts(root,date,true);
    } finally {if(root.bdRequest===token)root.setAttribute('aria-busy','false');}
  }
  window.BoardDynamics = {mountTrend,mountFacts,mountProbability,invalidate:date=>cache.delete(date)};
})();
