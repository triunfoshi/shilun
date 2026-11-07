/* Dated, read-only attribution acceptance. Existing production ranking stays independently labelled. */
(() => {
  'use strict';
  const S = {manifest:null, data:null, tab:'members', topic:'BK0825.DC', query:'', filter:'all', page:0, request:0, controller:null, reveal:false};
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const text = value => esc(value || '—');
  const count = value => Array.isArray(value) ? value.length : 0;
  const measured = (row, key) => row.diffusion_evaluated ? count(row[key]) : '未计算';
  const coreState = value => ({qualified:'已认证',pending:'未完成认证',not_qualified:'未认证',inactive:'本段未认证'}[value] || '未完成认证');
  const dayState = value => ({sealed:'收盘涨停',not_sealed:'未封住涨停',suspended:'停牌',unknown:'行情待补'}[value] || '行情待补');
  const status = row => row?.selected ? '入选' : ({eligible:'通过准入，名额外',pending:'待核验',rejected:'未通过准入'}[row?.status] || '—');
  const join = values => values?.length ? values.map(esc).join('、') : '—';
  const percent = value => Number.isFinite(value) ? `${value>0?'+':''}${(value*100).toFixed(2)}%` : '行情待补';
  const attributionLabel = value => ({matched:'原文归因已确认',ambiguous:'涨停归因待核验',missing:'涨停原因待补',unmapped:'原文未明确该题材'}[value] || '涨停归因待核验');
  const linkageLabel = value => ({both_up:'与参照核心同涨',not_both_up:'未与参照核心同时上涨',missing_prices:'联动行情待补',self_reference:'本身为参照核心',no_reference:'缺少已确认的参照核心'}[value] || '联动数据待补');
  function observationRows() {
    return S.data.candidates.filter(c=>!S.topic||c.code===S.topic).flatMap(c=>(c.observation?.members||[]).map(m=>({...m,topic:c.name,code:c.code}))).filter(m=>{
      if(S.query&&![m.name,m.ticker,m.reason,m.topic].join(' ').toLowerCase().includes(S.query.toLowerCase()))return false;
      if(S.filter==='matched')return m.attribution.status==='matched';
      if(S.filter==='pending')return ['ambiguous','missing'].includes(m.attribution.status);
      if(S.filter==='unmapped')return m.attribution.status==='unmapped';
      return true;
    });
  }
  function memberTable() {
    const candidates=S.data.candidates.filter(c=>!S.topic||c.code===S.topic);
    if(candidates.some(c=>!c.observation))return empty('当前归档尚未包含成员观察，请重新读取最新归档。');
    const rows=observationRows(),pages=pager(rows.length);
    const gaps=candidates.filter(c=>c.observation.status!=='ready');
    const scope=candidates.find(c=>c.observation?.scope_note)?.observation.scope_note || '成员观察范围待核验。';
    const note=`<p class="ta-note">${esc(scope)}涨停原因和复盘日联动分别核验。核心不重复计入跟随。${gaps.length?` ${gaps.length}个题材存在窗口或事件样本缺口，名单可能不完整。`:''}</p>`;
    if(!rows.length)return note+empty('当前条件没有成员观察记录。可清空搜索、切换题材或查看“缺口与规则”。');
    return note+`<div class="ta-scroll"><table class="ta-table ta-members"><caption class="ta-sr-only">题材成员观察与归因证据</caption><thead><tr><th>成员 / 身份</th><th>最近涨停原因</th><th>题材归属 / 归因</th><th>复盘日联动旁证</th><th>统计用途</th><th>证据</th></tr></thead><tbody>${rows.slice(S.page*12,S.page*12+12).map(m=>`<tr><td data-label="成员"><strong>${esc(m.name)}</strong><small>${esc(m.ticker)}</small><small>${m.role==='core_candidate'?'核心候选':'观察成员'}${m.qualified_at?' · '+esc(m.qualified_at.slice(5))+'认证':''}</small></td><td data-label="原因" class="ta-reason" title="${esc(m.reason)}">${text(m.reason)}<small>${text(m.reason_date)}</small></td><td data-label="归属"><strong>${esc(m.topic)} · 成员已确认</strong><small>${esc(attributionLabel(m.attribution.status))}</small></td><td data-label="联动">${esc(linkageLabel(m.linkage.status))}<small>本股 ${percent(m.linkage.stock_return)}${m.linkage.reference_core?' · '+esc(m.linkage.reference_core.name)+' '+percent(m.linkage.reference_core.return):''}</small></td><td data-label="统计">${esc(m.counting_note)}</td><td data-label="证据"><button type="button" data-ta-action="member" data-code="${esc(m.code)}" data-id="${esc(m.ticker)}" aria-label="查看${esc(m.name)} ${esc(m.topic)}成员证据">查看</button></td></tr>`).join('')}</tbody></table></div>${pages}`;
  }
  function attributes(event) { return S.topic ? event.attributions.filter(a => a.code === S.topic) : event.attributions; }
  function matchingEvents() {
    return (S.data?.events || []).filter(event => {
      const attrs = attributes(event);
      if (S.topic && !attrs.length) return false;
      const hay = [event.name,event.ticker,event.reason,...event.attributions.map(a=>a.name)].join(' ').toLowerCase();
      if (S.query && !hay.includes(S.query.toLowerCase())) return false;
      if (S.filter === 'changed') return attrs.some(a => a.before_matched !== (a.status === 'matched'));
      if (S.filter === 'matched') return attrs.some(a => a.status === 'matched');
      if (S.filter === 'pending') return attrs.some(a => ['ambiguous','missing','membership_missing'].includes(a.status));
      if (S.filter === 'unmapped') return attrs.length && attrs.every(a => a.status === 'unmapped');
      return true;
    });
  }
  function pager(total) {
    const last = Math.max(0, Math.ceil(total / 12) - 1); S.page = Math.min(S.page,last);
    return `<div class="ta-pager"><span>共 ${total} 条 · 第 ${S.page+1} / ${last+1} 页</span><button type="button" data-ta-action="prev" ${S.page===0?'disabled':''}>上一页</button><button type="button" data-ta-action="next" ${S.page===last?'disabled':''}>下一页</button></div>`;
  }
  function empty(message) { return `<p class="ta-empty">${esc(message)}</p>`; }
  function eventTable() {
    const rows = matchingEvents(), pages = pager(rows.length);
    if (!rows.length) return empty('当前筛选没有记录。可选择“全部状态”或清空股票搜索。');
    return `<div class="ta-scroll"><table class="ta-table ta-events"><caption class="ta-sr-only">近3个交易日的逐股归因对照</caption><thead><tr><th>涨停日期</th><th>股票 / 身份</th><th>原始涨停原因</th><th>修正前</th><th>修正后</th><th>证据</th></tr></thead><tbody>${rows.slice(S.page*12,S.page*12+12).map(e=>{
      const attrs=attributes(e), matched=attrs.filter(a=>a.status==='matched'), waiting=attrs.filter(a=>['ambiguous','missing'].includes(a.status));
      return `<tr><td data-label="日期">${esc(e.date)}</td><td data-label="股票"><strong>${esc(e.name)}</strong><small>${esc(e.ticker)}</small><small>${e.qualification_date?`核心 · ${esc(e.qualification_date.slice(5))}认证`:'未认证核心'}</small></td><td data-label="原始原因" class="ta-reason" title="${esc(e.reason)}">${text(e.reason)}</td><td data-label="修正前">${join(attrs.filter(a=>a.before_matched).map(a=>a.name))}</td><td data-label="修正后">${matched.length?join(matched.map(a=>a.name)):waiting.length?'<span class="ta-wait">待核验</span>':'未计入'}${waiting.length&&matched.length?'<small>另有待核验标签</small>':''}</td><td data-label="证据"><button type="button" data-ta-action="event" data-id="${esc(e.id)}" aria-label="查看${esc(e.name)} ${esc(e.date)}归因证据">查看</button></td></tr>`;
    }).join('')}</tbody></table></div>${pages}`;
  }
  function candidateTable() {
    const rows=S.data.candidates.filter(c=>(!S.topic||c.code===S.topic)&&(!S.query||[c.name,...(c.observation?.members||c.cores||[]).map(r=>r.name+' '+r.ticker)].join(' ').toLowerCase().includes(S.query.toLowerCase()))&&(S.filter!=='changed'||c.changed));
    const pages=pager(rows.length);
    if(!rows.length) return empty('当前条件没有方向。可改选“全部候选题材”。');
    return `<div class="ta-scroll"><table class="ta-table ta-candidates"><caption class="ta-sr-only">归因修正前后方向准入对照</caption><thead><tr><th>题材</th><th>主核心候选</th><th>已确认跟随</th><th>活跃日</th><th>结果变化</th><th>依据</th></tr></thead><tbody>${rows.slice(S.page*12,S.page*12+12).map(c=>`<tr><td data-label="题材"><strong>${esc(c.name)}</strong><small>${esc(c.code)} · ${c.member_count} 家归因样本</small></td><td data-label="核心">${text(c.cores?.[0]?.name)}<small>${c.cores?.[0]?.qualified_at?esc(c.cores[0].qualified_at)+' 认证':''}</small></td><td data-label="跟随">${measured(c.before,'followers')} → <strong>${measured(c,'followers')}</strong></td><td data-label="活跃日">${measured(c.before,'active_dates')} → ${measured(c,'active_dates')}</td><td data-label="结果">${esc(status(c.before))} → <strong>${esc(status(c))}</strong></td><td data-label="依据"><button type="button" data-ta-action="candidate" data-id="${esc(c.code)}">展开</button></td></tr>`).join('')}</tbody></table></div>${pages}`;
  }
  function rulesPanel() {
    const rules=S.manifest.mapping_rules.filter(r=>!S.topic||r.code===S.topic);
    return `<div class="ta-diagnostics"><strong>本日待核验事项</strong><ul>${S.data.diagnostics.map(v=>`<li>${esc(v)}</li>`).join('')||'<li>当前筛选的关键输入完整；含义未命中的词仍保留在逐股证据中。</li>'}</ul></div><div class="ta-scroll"><table class="ta-table ta-rules"><thead><tr><th>统一题材</th><th>关系</th><th>允许词组</th><th>边界</th></tr></thead><tbody>${rules.map(r=>`<tr><td data-label="题材">${esc(r.name)}<small>${esc(r.code)}</small></td><td data-label="关系">${esc(r.relation_label)}</td><td data-label="词组">${join(r.phrases)}</td><td data-label="边界">${esc(r.explanation)}<small>待核验词：${join(r.uncertain)}</small></td></tr>`).join('')||'<tr><td colspan="4">所选题材沿用名称精确匹配，没有额外业务映射。</td></tr>'}</tbody></table></div><details class="ta-method"><summary>版本、数据范围与解释边界</summary><p>本区展示涨停归因与新方向准入对照；保留、回流、退出后的当前名单统一看“短线题材周期”。两者回答不同问题。</p><ul>${S.manifest.limitations.map(v=>`<li>${esc(v)}</li>`).join('')}</ul><p>映射 ${esc(S.manifest.versions.attribution)} · 核心 ${esc(S.manifest.versions.core)} · 生成 ${esc(S.manifest.generated_at)}</p><p>题材按同日涨停原因拆分事件标签，与Vibe-Astock一致；历史归因样本不代表全板块成分。</p></details>`;
  }
  function renderBody() {
    if(!S.data) return;
    $('ta-detail').hidden=true;
    $('ta-panel').innerHTML=S.tab==='members'?memberTable():S.tab==='events'?eventTable():S.tab==='directions'?candidateTable():rulesPanel();
    document.querySelectorAll('[data-ta-tab]').forEach(b=>{
      const active=b.dataset.taTab===S.tab;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;
    });
    $('ta-panel').setAttribute('aria-labelledby',`ta-tab-${S.tab}`);
    $('ta-filter').disabled=S.tab==='rules';$('ta-search').disabled=S.tab==='rules';
    for(const option of $('ta-filter').options) option.disabled=(S.tab==='directions'&&!['all','changed'].includes(option.value))||(S.tab==='members'&&option.value==='changed');
  }
  function renderData() {
    const summary=S.data.summary;
    $('ta-summary').innerHTML=`<div><span>字面匹配初稿</span><p>${join(summary.before)}</p></div><div><span>归因修正后 · 新方向筛选</span><p><strong>${join(summary.after)}</strong></p></div><p class="ta-note">近3个交易日 · 至少2只独立跟随 · 涨停跨至少2日。核心须三连板及本段十日五板；这是新入选对照，单日未入选不等于旧周期退出。</p>`;
    const topics=S.data.candidates.slice().sort((a,b)=>Number(b.changed)-Number(a.changed)||a.name.localeCompare(b.name,'zh-CN'));
    $('ta-topic').innerHTML='<option value="">全部候选题材</option>'+topics.map(r=>`<option value="${esc(r.code)}">${esc(r.name)}${r.changed?' · 有变化':''}</option>`).join('');
    if(S.topic&&!topics.some(r=>r.code===S.topic))S.topic=topics.find(r=>r.source_topics?.some(s=>s.code===S.topic))?.code||'';$('ta-topic').value=S.topic;
    $('ta-status').textContent=`${S.data.date} · 回放归档已读取 · ${summary.changed_candidates} 个题材发生变化${S.data.diagnostics.length?' · 有待核验事项':''}`;
    $('ta-status').dataset.state='success';$('ta-content').hidden=false;renderBody();
    if(S.reveal){$('themeAcceptance').scrollIntoView({block:'start'});S.reveal=false;}
  }
  async function request(url, signal) {
    const response=await fetch(url,{signal});const data=await response.json();
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'读取失败，请重试。');return data;
  }
  async function load(day, refresh=false) {
    const sequence=++S.request; S.controller?.abort(); S.controller=new AbortController();
    const controller=S.controller, timeout=setTimeout(()=>controller.abort(),20000), signal=controller.signal;
    S.data=null;$('ta-content').hidden=true;$('ta-root').setAttribute('aria-busy','true');
    $('ta-status').textContent='正在读取归因验收归档…';$('ta-status').dataset.state='loading';$('ta-retry').disabled=true;
    try {
      if(!S.manifest||refresh){S.manifest=await request('/api/v1/review/theme-selection',signal);if(sequence!==S.request)return;
        $('ta-date').innerHTML=S.manifest.dates.slice().reverse().map(d=>`<option value="${esc(d)}">${esc(d)}</option>`).join('');}
      day=day||S.manifest.default_date;
      if(!S.manifest.dates.includes(day))throw new Error('该日期没有验收归档，请选择列表中的交易日。');
      $('ta-date').value=day;
      const data=await request(`/api/v1/review/theme-selection/day?date=${encodeURIComponent(day)}&revision=${S.manifest.revision}`,signal);
      if(sequence!==S.request)return;S.data=data;S.page=0;renderData();
    } catch(error) {
      if(sequence!==S.request)return;
      $('ta-status').textContent=error.name==='AbortError'?'读取超时，请重试。':error.message;
      $('ta-status').dataset.state='error';
    } finally {clearTimeout(timeout);if(sequence===S.request){$('ta-root').setAttribute('aria-busy','false');$('ta-retry').disabled=false;}}
  }
  function detailsEvent(id) {
    const e=S.data.events.find(r=>r.id===id);if(!e)return;
    const attrs=attributes(e), core=S.data.samples.find(s=>s.ticker===e.ticker);
    showDetail(`<h4>${esc(e.name)} · ${esc(e.date)} · 归因证据</h4><p><strong>原文：</strong>${text(e.reason)}</p><p>${esc(e.ticker)} · ${esc(e.role)} · 涨停当日：${esc(coreState(e.event_core_status))}${core?` · 所选复盘日：${esc(coreState(core.status))}`:''}</p><div class="ta-scroll"><table class="ta-table"><thead><tr><th>题材</th><th>结论与依据</th><th>成分与规则</th></tr></thead><tbody>${attrs.map(a=>`<tr><td data-label="题材">${esc(a.name)}</td><td data-label="结论">${esc(a.explanation)}${[...new Set(a.matches.map(m=>m.explanation))].map(v=>`<small>${esc(v)}</small>`).join('')}</td><td data-label="证据">${esc(a.membership_date)} · ${a.member_count} 家<small>${join([...new Set(a.matches.map(m=>m.rule_id))])}</small></td></tr>`).join('')||'<tr><td colspan="3">该股票未关联到本次已归档的核心候选题材，不能据此认定它没有其他题材。</td></tr>'}</tbody></table></div><p class="ta-note">原因来源：${text(e.source)} · 采集：${text(e.fetched_at)}。本区只核验同日事件样本和原文依据。</p>`);
  }
  function detailsMember(code,ticker) {
    const c=S.data.candidates.find(r=>r.code===code),m=c?.observation?.members.find(r=>r.ticker===ticker);if(!m)return;
    const l=m.linkage;
    const sources=(m.source_topics?.length?m.source_topics:[{code,name:c.name}]).map(s=>`${s.name} ${s.code}`).join('、');
    showDetail(`<h4>${esc(m.name)} · ${esc(c.name)}成员观察</h4><p><strong>历史事件归因：</strong>同日原因标签 ${esc(sources)} · 观察归档日 ${esc(m.membership_date)}。${m.role==='core_candidate'?'已通过独立核心资格，认证日 '+esc(m.qualified_at)+'。':'保留为观察成员，尚未完成本段核心认证。'}</p><p><strong>最近涨停：</strong>${text(m.reason_date)} · ${text(m.reason)}</p><p><strong>${esc(attributionLabel(m.attribution.status))}：</strong>${esc(m.attribution.explanation)}</p><p><strong>统计用途：</strong>${esc(m.counting_note)}。</p><p><strong>${esc(l.date)} 联动旁证：</strong>${esc(linkageLabel(l.status))}；本股 ${percent(l.stock_return)}${l.reference_core?'，'+esc(l.reference_core.name)+' '+percent(l.reference_core.return):''}。当日该题材成员涨停 ${l.member_limit_count??'待补'} 家（成员事实统计）。${esc(l.note)}</p><details><summary>观察窗口内涨停记录（${m.events.length}条）</summary><ul>${m.events.map(e=>`<li>${esc(e.date)} · ${text(e.reason)} · ${esc(attributionLabel(e.attribution?.status))}</li>`).join('')||'<li>历史成分或原因记录待补，当前成员与资格仍保留。</li>'}</ul></details>`);
  }
  function detailsCandidate(code) {
    const c=S.data.candidates.find(r=>r.code===code);if(!c)return;
    const f=c.feedback||{},cohort=f.cohort||[];
    showDetail(`<h4>${esc(c.name)} · 筛选依据</h4><p><strong>修正前：</strong>${join(c.before.reasons)}</p><p><strong>修正后：</strong>${join(c.reasons)}</p><p>已认证且归因支持的核心候选：${join((c.cores||[]).map(r=>`${r.name}（${r.qualified_at}认证）`))}。主核心候选按近7日归因支持的涨停次数、走势最高连板、走势涨幅依次比较。</p><p>成员观察中的核心候选（含归因待核验）：${join((c.observation?.members||[]).filter(r=>r.role==='core_candidate').map(r=>r.name))}。<button type="button" data-ta-action="members" data-id="${esc(c.code)}">查看成员观察</button></p><p>计入的独立跟随：${join([...new Set((c.events||[]).map(e=>e.name+' '+e.ticker))])}。活跃日期：${join(c.active_dates)}。</p><p>${esc(S.data.ranking_note)}</p><details><summary>固定昨日跟随反馈（${cohort.length}只，掉队者保留）</summary><ul>${cohort.map(r=>`<li>${esc(r.name)} · ${esc(r.ticker)} · ${esc(dayState(r.state))} · ${Number.isFinite(r.return)?(r.return*100).toFixed(2)+'%':'行情待补'}</li>`).join('')||'<li>暂无已确认归因的昨日跟随样本。</li>'}</ul></details>`);
  }
  function showDetail(html) {$('ta-detail').innerHTML=`<button type="button" class="ta-close" data-ta-action="close">收起证据</button>${html}`;$('ta-detail').hidden=false;$('ta-detail').focus({preventScroll:true});$('ta-detail').scrollIntoView({block:'nearest'});}
  function open(day) {
    window.switchTab?.('sectors');const section=$('themeAcceptance');if(!section)return;
    S.reveal=true;
    section.open=true;section.scrollIntoView({block:'start'});
    if(day||!S.data)load(day);
  }
  function init() {
    if(!$('themeAcceptance'))return;
    $('themeAcceptance').addEventListener('toggle',()=>{if($('themeAcceptance').open&&!S.data&&!($('ta-root').getAttribute('aria-busy')==='true'))load();});
    $('ta-date').addEventListener('change',e=>load(e.target.value));
    $('ta-topic').addEventListener('change',e=>{S.topic=e.target.value;S.page=0;renderBody();});
    $('ta-search').addEventListener('input',e=>{S.query=e.target.value;S.page=0;renderBody();});
    $('ta-filter').addEventListener('change',e=>{S.filter=e.target.value;S.page=0;renderBody();});
    $('ta-retry').addEventListener('click',()=>load($('ta-date').value||undefined,true));
    $('ta-root').addEventListener('click',e=>{
      const tab=e.target.closest('[data-ta-tab]');if(tab){S.tab=tab.dataset.taTab;S.page=0;if((S.tab==='directions'&&!['all','changed'].includes(S.filter))||(S.tab==='members'&&S.filter==='changed')){S.filter='all';$('ta-filter').value='all';}renderBody();return;}
      const b=e.target.closest('[data-ta-action]');if(!b)return;
      if(b.dataset.taAction==='prev'||b.dataset.taAction==='next'){S.page+=b.dataset.taAction==='prev'?-1:1;renderBody();}
      if(b.dataset.taAction==='event')detailsEvent(b.dataset.id);
      if(b.dataset.taAction==='member')detailsMember(b.dataset.code,b.dataset.id);
      if(b.dataset.taAction==='members'){S.tab='members';S.topic=b.dataset.id;S.page=0;S.filter='all';S.query='';$('ta-filter').value='all';$('ta-topic').value=S.topic;$('ta-search').value='';renderBody();$('ta-tab-members').focus();}
      if(b.dataset.taAction==='candidate')detailsCandidate(b.dataset.id);
      if(b.dataset.taAction==='close'){$('ta-detail').hidden=true;$('ta-panel').focus({preventScroll:true});}
    });
    $('ta-tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
      const tabs=[...$('ta-tabs').querySelectorAll('button')],index=tabs.indexOf(document.activeElement);if(index<0)return;
      e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].click();tabs[next].focus();});
    const url=new URL(location.href);if(url.searchParams.get('theme_acceptance')==='1'){
      const view=url.searchParams.get('view');if(['members','events','directions','rules'].includes(view))S.tab=view;
      S.query=(url.searchParams.get('stock')||'').slice(0,80);$('ta-search').value=S.query;
      open(url.searchParams.get('date')||undefined);
    }
  }
  window.ThemeAcceptance={open,load};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
