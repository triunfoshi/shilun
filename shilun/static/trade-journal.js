/* Versioned review notes over archived fills. This module never writes orders or live holdings. */
(() => {
  'use strict';
  const BASE = '/api/v1/trade-journal';
  const state = {definitions:[], definitionVersion:'', definitionBoundary:'', definitionsError:'', summary:null, items:[], total:0, offset:0, limit:30, selected:'', detail:null, listSeq:0, detailSeq:0, loaded:false, saving:false, sources:[],sourceOffset:0,sourceTotal:0,observations:[],observationOffset:0,observationTotal:0,observationDraft:null,researchSelection:null,plans:[],planOffset:0,planTotal:0,planSaving:false,bindPlans:[]};
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const array = value => Array.isArray(value) ? value : [];
  const text = value => value == null || value === '' ? '待补' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  const num = (value, decimals=2) => value != null && value !== '' && Number.isFinite(Number(value)) ? Number(value).toLocaleString('zh-CN', {minimumFractionDigits:decimals,maximumFractionDigits:decimals}) : '—';
  const ratio = value => value==null ? '—' : num(Number(value)*100)+'%';
  const episodeFee = r => r.unknown_fees ? '—（费用待核）' : num(r.fees);
  const definition = id => state.definitions.find(d => d.id === id);
  const label = id => definition(id)?.label || id || '未知';
  const frozenLabel = (record,id) => {const d=recordDefinitions(record).find(d=>d.id===id);return d?(d.label||id)+(id?.startsWith('mode.user.')?' · '+d.rule_version:''):label(id);};
  const definitionRefs = (ids,exact={}) => ids.filter(Boolean).map(id=>id.startsWith('mode.user.')?{id,rule_version:exact[id]||definition(id)?.rule_version}:id);
  const recordDefinitions = record => array(record?.definition_snapshots).concat(array(record?.definition_refs),array(record?.content?.definition_refs)).filter(d=>d&&typeof d==='object'&&d.id);
  function normalizeModeSelection(values,form) {
    const exact={};let selected;
    try{selected=JSON.parse(values.mode_id);}catch {}
    if(selected&&typeof selected==='object'&&selected.id?.startsWith('mode.user.')){values.mode_id=selected.id;if(selected.rule_version)exact[selected.id]=selected.rule_version;else values._modeSelectionError='原自建模式版本未知，请明确选择一个可恢复版本后保存。';}
    else if(values.mode_id===form.dataset.originalMode){try {const old=JSON.parse(form.dataset.definitionSnapshots||'[]').find(d=>d.id===values.mode_id);if(old?.rule_version)exact[old.id]=old.rule_version;else if(values.mode_id?.startsWith('mode.user.'))values._modeSelectionError='原自建模式版本未知，请明确选择可恢复的定义版本。';}catch {}}
    return exact;
  }
  const term = (id, fallback='口径') => `<button class="tj-term" type="button" data-tj-action="definition" data-id="${esc(id)}">${esc(definition(id)?.label || fallback)} <span aria-hidden="true">?</span></button>`;
  const status = (message, tone='') => { const n=$('tradeJournalStatus'); if(n){n.textContent=message;n.dataset.state=tone;} };
  const section = (title, body, extra='') => `<section class="tj-section ${extra}" data-journal-section="${esc(title)}"><h3>${esc(title)}</h3>${body}</section>`;
  const field = (title, value) => `<div><dt>${esc(title)}</dt><dd>${esc(text(value))}</dd></div>`;
  const shortId = value => {const s=String(value||'');return s.length>30?s.slice(0,18)+'…'+s.slice(-8):s;};
  const narrative = (value, fallback='待补') => {
    if(value==null||value==='')return fallback;
    if(typeof value!=='object')return String(value);
    if(Array.isArray(value))return value.length&&value.every(v=>v==null||typeof v!=='object')?value.filter(v=>v!=null).join('、'):fallback;
    for(const key of ['text','raw','content','description','summary'])if(typeof value[key]==='string'&&value[key])return value[key];
    return '已保存结构化内容，展开原始字段查看';
  };
  const rawFields = value => `<details class="tj-raw"><summary>原始字段与引用</summary><pre class="tj-raw-json">${esc(JSON.stringify(value,null,2))}</pre></details>`;
  const fillTimeLabel = f => {
    const kind=f.time_kind||f.time_precision;
    const meaning=({execution_time:'撮合成交时间',statement_event_time:'交割单发生时间',date_only:'仅日期；撮合时点未知',unknown:'时间性质待核'})[kind];
    const precision=({second:'精确到秒',minute:'精确到分钟',day:'仅日期'})[f.time_precision];
    return [meaning||'时间性质待核',precision].filter(Boolean).join(' · ');
  };
  function expectationFields(value,verificationAt) {
    const structured=value&&typeof value==='object'&&!Array.isArray(value)?value:{};
    const targetDate=structured.target_trade_date||structured.target_date;
    return field('预期',narrative(value))+(targetDate?field('目标交易日',targetDate):'')+field('验证时点',verificationAt);
  }
  function riskFields(value) {
    if(!value||typeof value!=='object'||Array.isArray(value))return field('风险约束',narrative(value));
    const cap=value.planned_position_ceiling;
    const ceiling=cap&&typeof cap==='object'?[narrative(cap,'上限口径待补'),cap.type==='account_position_fraction_ceiling'&&cap.value!=null?`${cap.single_security?'单一证券 · ':''}${ratio(cap.value)}`:'',cap.planned_ceiling_not_actual_weight?'计划上限，不代表实际仓位':''].filter(Boolean).join('；'):narrative(cap,'');
    return (value.text||value.raw?field('风险原文',narrative(value)):'')+(ceiling?field('计划仓位上限',ceiling):'')+(value.change_date?field('上限变更日',value.change_date):'')+field('整轮最大亏损预算',narrative(value.max_loss_budget))+(value.scope?field('风险适用范围',narrative(value.scope)):'')+(!ceiling&&!value.text&&!value.raw?field('风险约束','已保存结构化内容，展开原始字段查看'):'');
  }
  function fillSummary(ids) {
    const rows=array(ids).map(id=>array(state.detail?.fills).find(f=>f.fill_id===id)).filter(Boolean);
    if(!array(ids).length)return '库存轮整体；未指定逐笔';
    if(!rows.length)return `${ids.length} 笔成交；精确标识见原始字段`;
    const summary=rows.slice(0,2).map(f=>`${f.time||f.executed_at?.split('T')[1]?.slice(0,8)||f.date||'时间待核'} ${f.side==='buy'?'买入':f.side==='sell'?'卖出':'方向待核'} ${num(f.quantity,0)} 股`).join('；');
    return summary+(ids.length>2?`；共 ${ids.length} 笔`:'');
  }
  function decisionEndpoint(id,revision) {
    const cache=state.linkedDecisions?.[id+'@'+(revision||'latest')];
    const d=array(state.detail?.decisions).find(d=>d.decision_id===id&&(!revision||Number(d.revision)===Number(revision))),r=d?state.detail?.episode:cache?.episode,cached=d||cache?.record;
    const title=cached?narrative(cached.action,cached.action_id?frozenLabel(cached,cached.action_id):'原决策'):'打开原决策';
    return `<button type="button" class="tj-related" data-tj-action="linked-decision" data-id="${esc(id)}" data-revision="${esc(revision||'')}" title="${esc(id)}"><strong>${esc([r?.name||r?.ticker,title].filter(Boolean).join(' · '))}</strong><small>${esc(shortId(id))}${revision?' · 版本 '+esc(revision):''}</small></button>`;
  }
  const missingLabel = key => ({accounting:'成交、库存与成本核对',evidence_time_or_provenance:'证据时点或来源',plan_reference:'原计划引用核对',confirmed_process_review:'已确认的过程复盘',expectation_review:'原版本预期核验',episode_source:'库存轮来源',mode:'交易模式',trigger:'触发 / 形态',reason:'独立理由',expectation:'正常预期',verification_at:'验证时点',invalidation:'失效条件',holding_horizon:'持有期限',risk_constraints:'风险预算与边界',reference_tickers:'参照证券',evidence:'可定位证据',target_date:'目标交易日',purpose:'模式用途',allowed_environments:'允许环境',blocked_environments:'禁止环境',entry_triggers:'入场触发',verification:'验证方式与窗口',sources:'来源引用',rationale:'独立理由',question:'研究问题',development_window:'开发区间',validation_window:'验证区间',universe:'研究范围与分母',comparison_group:'对照组',exclusion_policy:'排除与缺数政策',target_metrics:'目标指标',entry_rule_refs:'入场规则版本',exit_rule_refs:'退出规则版本',execution_constraints:'交易执行约束'}[key]||key);
  const missing = rows => {const items=Array.isArray(rows)?rows.map(r=>typeof r==='string'?missingLabel(r):r):rows&&typeof rows==='object'?Object.entries(rows).map(([key,count])=>`${missingLabel(key)}：${count}`):[];return items.length ? `<ul class="tj-missing">${items.map(r=>`<li>${esc(text(r))}</li>`).join('')}</ul>` : '<p class="tj-muted">没有返回额外缺项；资格仍以各维核验为准。</p>';};
  const table = (heads, rows, caption) => `<div class="tj-table-wrap" tabindex="0" role="region" aria-label="${esc(caption)}"><table><caption>${esc(caption)}</caption><thead><tr>${heads.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.length ? rows.map(r=>`<tr>${r.map((v,i)=>`<td data-label="${esc(heads[i])}">${v}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${heads.length}">此库存轮没有对应记录；请核对归档来源。</td></tr>`}</tbody></table></div>`;

  async function api(path, options={}) {
    const response = await fetch(BASE+path, {credentials:'same-origin', ...options, headers:{Accept:'application/json', ...(options.body ? {'Content-Type':'application/json'} : {}), ...(options.headers||{})}});
    let data; try { data=await response.json(); } catch { throw new Error(`服务返回了非 JSON 内容（${response.status}），请检查服务状态后重试。`); }
    if (!response.ok) {
      const detail=data.detail || data.message || data.error;
      throw new Error(typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `账本服务返回 ${response.status}，请稍后重试。`);
    }
    return data;
  }
  async function loadDefinitions() {
    try {const d=await api('/definitions'); state.definitions=array(d.definitions);state.definitionVersion=d.version||'';state.definitionBoundary=d.boundary||'';state.definitionsError='';}
    catch(error){state.definitionsError=error.message;}
  }
  function readFilters() {
    const form=$('tradeJournalFilters');
    const value=name=>form?.elements?.namedItem(name)?.value?.trim()||'';
    return {query:value('query'),start_date:value('start_date'),end_date:value('end_date'),status:value('status')};
  }
  async function load({reset=false}={}) {
    mountReviewWorkflows();
    const filters=readFilters();
    if(filters.start_date && filters.end_date && filters.start_date>filters.end_date){status('开始日期晚于结束日期，请调整日期后读取。','error');return;}
    if(reset)state.offset=0;
    const seq=++state.listSeq;
    status('正在读取归档成交与库存轮…','loading');
    $('tradeJournalList').setAttribute('aria-busy','true');
    $('tradeJournalList').innerHTML='<div class="tj-skeleton" role="status">正在读取库存轮名单…</div>';
    const params=new URLSearchParams({...filters,limit:String(state.limit),offset:String(state.offset)});
    const results=await Promise.allSettled([api('/episodes?'+params),api('/summary'),state.definitions.length?Promise.resolve():loadDefinitions()]);
    if(seq!==state.listSeq)return;
    $('tradeJournalList').setAttribute('aria-busy','false');
    if(results[0].status==='rejected'){
      state.items=[];state.total=0;
      $('tradeJournalList').innerHTML=`<div class="tj-empty"><strong>库存轮未读取</strong><p>${esc(results[0].reason.message)}</p><button type="button" data-tj-action="reload">重新读取</button></div>`;
      $('tradeJournalPagination').innerHTML='';
      $('tradeJournalSummary').innerHTML='';
      state.detail=null;window.JournalWorkspace?.setEpisode(null);$('tradeJournalDetail').innerHTML='<p class="tj-empty">名单未读取成功。重新读取后再核对库存轮详情。</p>';
      status('归档账本未读取成功。已有决策详情可能属于上一次读取，请重试。','error');
      return;
    }
    const payload=results[0].value;
    state.items=array(payload.items);state.total=Number(payload.total)||0;state.loaded=true;
    state.summary=results[1].status==='fulfilled'?results[1].value:null;
    renderSummary(results[1].status==='rejected'?results[1].reason.message:'');renderList();
    status(`已读取 ${state.total} 轮库存${state.definitionsError?'；术语口径暂不可用，补记前请重试读取。':''}。归档数据与当前持仓分别核对。`,state.definitionsError?'error':'ready');
    if(state.selected && state.items.some(r=>r.episode_id===state.selected)) await selectEpisode(state.selected,{focus:false});
    else {state.selected='';state.detail=null;window.JournalWorkspace?.setEpisode(null);$('tradeJournalDetail').innerHTML='<p class="tj-empty">选取一轮库存，核对逐笔成交、决策版本与缺项。</p>';}
  }
  function renderSummary(error='') {
    const s=state.summary;
    if(!s){$('tradeJournalSummary').innerHTML=`<p class="tj-muted">汇总未读取：${esc(error||'服务未返回汇总')}。名单仍可逐轮核对。</p>`;return;}
    const counts=s.counts||{};
    const names={sources:'来源',batches:'导入批次',fills:'成交',financial_events:'财务事件',episodes:'库存轮',securities:'证券',decisions:'决策',observations:'观察记录',plans:'独立预案',plan_revisions:'预案版本',mode_cards:'自建模式',hypotheses:'研究假设'};
    $('tradeJournalSummary').innerHTML=`<div class="tj-summary-line"><strong>归档概览</strong>${Object.entries(counts).map(([key,value])=>`<span>${esc(names[key]||definition('metric.'+key)?.label||'其他记录')} <b>${esc(text(value))}</b></span>`).join('')}<details><summary>来源覆盖与缺项</summary><dl class="tj-facts">${field('覆盖区间',s.coverage?.from||s.coverage?.to?[s.coverage.from||'未知',s.coverage.to||'未知'].join(' → '):s.coverage)}${field('覆盖范围',s.coverage?.scope)}${Object.entries(s.qualification_counts||{}).map(([key,count])=>field(label('qualification.'+key),count)).join('')}</dl>${missing(s.missing_fields)}</details></div>`;
  }
  function reviewProgressBadge(progress={}) {
    const labels={reviewed:'✓ 已复盘',in_progress:'复盘中',needs_update:'待更新',not_started:'未复盘'};
    const status=Object.prototype.hasOwnProperty.call(labels,progress.status)?progress.status:'not_started';
    const text=status==='in_progress'&&progress.has_saved_notes===true?'已填写 · 待确认':labels[status];
    return `<small class="tj-review-progress" data-review-status="${status}" aria-label="复盘状态：${esc(text)}" title="${esc([progress.note,progress.scope].filter(Boolean).join('；')||'尚未保存并确认本轮过程复盘')}">${esc(text)}</small>`;
  }
  function renderList() {
    if(!state.items.length){$('tradeJournalList').innerHTML='<div class="tj-empty"><strong>筛选范围内没有库存轮</strong><p>可清除股票或日期筛选；若仍为空，请检查成交归档是否已导入。</p><button type="button" class="secondary" data-tj-action="reset">清除筛选</button></div>';}
    else $('tradeJournalList').innerHTML=table(['股票','交易区间','已实现盈亏（元）'],state.items.map(r=>[
      `<button type="button" class="tj-episode${r.episode_id===state.selected?' is-selected':''}" data-tj-action="episode" data-id="${esc(r.episode_id)}" aria-pressed="${r.episode_id===state.selected}"><strong>${esc(r.name||r.ticker)}</strong><small>${esc(r.ticker||r.code)} · ${r.status==='closed'?'已清仓':'尚有库存'}</small></button>${reviewProgressBadge(r.review_progress)}`,
      `${esc(r.start_date||'待核')}<br>${esc(r.end_date||'至今')}`,
      num(r.realized_pnl)
    ]),'点击股票名称，开始这一轮复盘');
    $('tradeJournalPagination').innerHTML=`<button type="button" class="secondary" data-tj-action="previous" ${state.offset===0?'disabled':''}>上一页</button><span>${state.total?`${state.offset+1}–${Math.min(state.offset+state.items.length,state.total)} / ${state.total}`:'0 / 0'}</span><button type="button" class="secondary" data-tj-action="next" ${state.offset+state.limit>=state.total?'disabled':''}>下一页</button>`;
  }
  async function selectEpisode(id,{focus=true}={}) {
    state.selected=id;window.JournalWorkspace?.setEpisode(null);const seq=++state.detailSeq;renderList();
    const selected=state.items.find(r=>r.episode_id===id),name=selected?.name||selected?.ticker||'该笔交易';
    status('正在打开'+name+'的复盘…','loading');
    const root=$('tradeJournalDetail');root.setAttribute('aria-busy','true');root.innerHTML='<div class="tj-skeleton" role="status">正在核对该轮成交、决策版本与证据…</div>';
    try {const detail=await api('/episodes/'+encodeURIComponent(id));if(seq!==state.detailSeq)return;state.detail=detail;if(detail.episode?.review_progress){const listed=state.items.find(r=>r.episode_id===id);if(listed)listed.review_progress=detail.episode.review_progress;renderList();}renderDetail();status('已打开'+(detail.episode?.name||name)+'的复盘。','ready');if(focus){const target=window.JournalWorkspace?$('journalWorkspaceOverview'):root;const focusTarget=window.JournalWorkspace?target?.querySelector?.('h2'):root;focusTarget?.setAttribute?.('tabindex','-1');focusTarget?.focus?.({preventScroll:true});target?.scrollIntoView?.({block:'start'});}}
    catch(error){if(seq!==state.detailSeq)return;state.detail=null;window.JournalWorkspace?.setEpisode(null);status(name+'的复盘未打开：'+error.message+'。请重新点击股票名称重试。','error');root.innerHTML=`<div class="tj-empty"><strong>该轮详情未读取</strong><p>${esc(error.message)}</p><button type="button" data-tj-action="episode" data-id="${esc(id)}">重新读取详情</button></div>`;}
    finally {if(seq===state.detailSeq)root.setAttribute('aria-busy','false');}
  }
  function qualifications(q={}) {
    return `<div class="tj-qualifications">${Object.entries(q).map(([key,value])=>`<div class="${value.eligible?'is-eligible':'is-incomplete'}"><strong>${value.eligible?'✓':'○'} ${term(value.definition_ref||'qualification.'+key,key)}</strong><span>${value.eligible?'符合当前资格':'尚有缺项'}</span>${array(value.reasons).length?missing(value.reasons):'<p class="tj-muted">本维没有返回额外缺项。</p>'}</div>`).join('')||'<p>资格结果未返回，不能推断已通过。</p>'}</div>`;
  }
  function renderDecision(d,{readOnly=false}={}) {
    const gaps=[...new Set(array(d.missing_fields).concat(array(d.temporal_errors),array(d.note?.remaining_gaps)).map(v=>typeof v==='string'?missingLabel(v):narrative(v)))];
    const mode=d.hasContent===false?'按需读取该版本正文':[d.mode_id?frozenLabel(d,d.mode_id):'',narrative(d.mode,'')].filter(Boolean).filter((v,i,a)=>a.indexOf(v)===i).join(' · ');
    const trigger=d.hasContent===false?'按需读取该版本正文':[d.trigger_id?frozenLabel(d,d.trigger_id):'',narrative(d.trigger,'')].filter(Boolean).filter((v,i,a)=>a.indexOf(v)===i).join(' · ');
    return `<article class="tj-decision" data-decision-id="${esc(d.decision_id)}"><header><strong>${esc(narrative(d.action,d.action_id?frozenLabel(d,d.action_id):'动作待补'))}</strong><span>版本 ${esc(d.revision??'待核')} · ${esc(d.evidence_class?label('evidence.'+d.evidence_class):'证据类别未知')}</span></header>
      <dl class="tj-facts jw-decision-brief">${field('交易模式',mode||'尚未标记')}${field('操作理由',narrative(d.reason))}${field('预期记录',window.JournalWorkspace?.expectationText?.(d.expectation)||narrative(d.expectation))}${field('仓位计划',narrative(d.risk_constraints,'当时未设或尚未补充'))}</dl><details class="jw-record-more"><summary>完整记录与证据</summary><dl class="tj-facts tj-decision-summary">${field('关联成交',fillSummary(d.fill_ids))}${field('模式',mode)}${field('触发 / 形态',trigger)}${field('理由',narrative(d.reason))}${expectationFields(d.expectation,d.verification_at)}${field('失效条件',narrative(d.invalidation))}${field('持有期限',narrative(d.holding_horizon))}${riskFields(d.risk_constraints)}${array(d.reference_tickers).length?field('参照证券',d.reference_tickers.join('、')):''}${field('所述观察时点',d.observed_at)}${field('实际记录时点',d.recorded_at)}${d.available_at?field('信息可用时点',d.available_at):''}${d.revision_reason?field('本次修订理由',narrative(d.revision_reason)):''}</dl>
      ${gaps.length?`<div class="tj-decision-gaps"><strong>待补与核验边界</strong>${missing(gaps)}</div>`:''}${rawFields(d)}</details>
      ${readOnly?'':`<div class="tj-actions"><button type="button" class="secondary" data-tj-action="edit" data-id="${esc(d.decision_id)}">修改复盘</button><button type="button" class="secondary" data-tj-action="history" data-id="${esc(d.decision_id)}">回看旧记录</button><button type="button" class="secondary" data-tj-action="check" data-id="${esc(d.decision_id)}">补充预期核验</button>${d.frozen_plan?`<button type="button" class="secondary" data-tj-action="plan" data-id="${esc(d.decision_id)}">查看冻结预案</button>`:''}</div><div id="tj-history-${esc(d.decision_id)}" class="tj-history" hidden></div>`}</article>`;
  }
  function renderDetail() {
    const d=state.detail,r=d?.episode;if(!r)return;
    const fills=array(d.fills), decisions=array(d.decisions);
    $('tradeJournalDetail').innerHTML=`<header class="tj-detail-head"><div><h2>${esc(r.name||r.ticker)} <small>${esc(r.ticker||r.code)}</small></h2><p>${esc(r.start_date)} → ${esc(r.end_date||'尚有库存')} · <span title="${esc(r.episode_id)}">${esc(shortId(r.episode_id))}</span></p>${reviewProgressBadge(r.review_progress||state.detail.review_progress)}</div><div class="tj-actions"><button type="button" class="secondary jw-open-archive" data-jw-action="archive">完整档案</button><button type="button" class="secondary jw-close-archive" data-jw-action="notes">返回简洁复盘</button></div></header>
      <dl class="tj-facts tj-round-facts">${field('归档剩余库存',num(r.quantity,0)+' 股')}${field('期初库存 / 成本',`${num(r.opening_quantity,0)} 股 / ${r.opening_cost_status==='known'?'已知':'待核'}`)}${field('已实现损益',num(r.realized_pnl))}${field('完整闭合轮损益',num(r.closed_pnl))}${field('本轮费用',episodeFee(r))}${field('剩余库存成本',r.opening_cost_status!=='known'&&r.remaining_cost==null?'—（成本待核）':num(r.remaining_cost))}</dl>
      <p class="tj-muted">金额单位：元。现金净变动与损益分别记录；未核实的期初成本不补为零。</p>
      ${section('四维资格与缺项',qualifications(d.qualification||r.qualification)+`<details><summary>全部待补字段</summary>${missing(d.missing_fields||r.missing_fields)}</details>`)}
      ${section('逐次成交',table(['时间 / 来源','方向','数量','价格 / 金额','费用 / 净现金','库存前 → 后','决策'],fills.map(f=>[
        `${esc(f.executed_at||[f.date,f.time].filter(Boolean).join(' '))}<small>${esc(fillTimeLabel(f))} · ${esc(typeof f.source_ref==='object'?(f.source_ref?.source_id?'已归档来源':'来源定位待核'):f.source_ref||'来源待核')}</small>${f.statement_event_time?`<small>交割单发生时间：${esc(narrative(f.statement_event_time))}</small>`:''}${f.source_ref?.source_id?`<button type="button" class="tj-term" data-tj-action="source" data-id="${esc(f.source_ref.source_id)}">查看来源原件${f.source_ref.row!=null?' · 第 '+esc(f.source_ref.row)+' 行':f.source_ref.record_id?' · '+esc(shortId(f.source_ref.record_id)):''}</button>`:''}${rawFields(f)}`,
        esc(f.side==='buy'?'买入':f.side==='sell'?'卖出':f.side),num(f.quantity,0),`${num(f.price)}<small>${num(f.amount)}</small>`,`${num(f.fees)}<small>${num(f.net_cash)}</small>`,`${num(f.quantity_before,0)} → ${num(f.quantity_after,0)}`,
        `<button type="button" class="secondary" data-tj-action="new" data-fill="${esc(f.fill_id)}">补记本笔</button><small>${decisions.filter(x=>array(x.fill_ids).includes(f.fill_id)).length} 条决策</small>`
      ]),'归档成交事实；补记仅增加决策版本'))}
      ${section('当时的操作与预期',`${decisions.length?`<div class="tj-actions"><button type="button" data-tj-action="new">新增一条操作记录</button><button type="button" class="secondary" data-tj-action="bind-plan">绑定已冻结预案</button></div><p class="tj-muted">先记下当时的想法。记不清的留空，补记和原记录分别保留。</p>${decisions.map(renderDecision).join('')}`:`<div class="tj-empty"><h4>${esc(r.name||r.ticker)} · 开始复盘</h4><p>成交记录已打开。这轮还没有填写复盘，请从下面选择一次买入、加仓或卖出，补记当时的想法。</p></div>${fills.length?table(['成交时间','操作','数量','成交价','开始复盘'],fills.map(f=>[`${esc(String(f.executed_at||f.date||'日期待核').replace('T',' ').replace(/(?:Z|[+-]\d{2}:\d{2})$/,''))}<small>${esc(fillTimeLabel(f))}</small>`,esc(f.side==='buy'?'买入':f.side==='sell'?'卖出':f.side),num(f.quantity,0)+' 股',num(f.price)+' 元',`<button type="button" class="secondary" data-tj-action="new" data-fill="${esc(f.fill_id)}">从本笔开始复盘</button>`]),'这轮的真实成交；选择本笔补记当时想法'):'<button type="button" data-tj-action="new">补记本轮想法</button>'}`}<div id="tradeJournalEditor"></div><div id="tradeJournalBindEditor"></div>`)}
      ${section('预期核验',array(d.expectation_checks).length?array(d.expectation_checks).map(c=>`<article class="tj-check"><strong>${esc(c.status_id?label(c.status_id):label('expectation_check.'+c.status))}</strong><dl class="tj-facts">${field('核验类别',c.machine_verified?'机器数值比较':c.evaluation_class==='numeric_comparison'?'已归档数值比较；尚未机器核验通过':'人工复盘评估')}${field('决策 / 版本',[shortId(c.decision_id),c.decision_revision].filter(x=>x!=null).join(' / '))}${field('实际结果',narrative(c.actual))}${field('核验边界',narrative(c.reasons))}${field('记录时点',c.recorded_at)}</dl>${rawFields(c)}</article>`).join(''):'<p class="tj-muted">尚无预期核验。支持、失效和缺数应分别保留证据；不能由盈亏倒推。</p>')}
      ${section('跨票决策关联',`${array(d.links).length?table(['起点决策','终点决策','关系与理由'],array(d.links).map(l=>[decisionEndpoint(l.from_decision_id,l.from_revision),decisionEndpoint(l.to_decision_id,l.to_revision),`<strong>${esc(l.relation_id?label(l.relation_id):narrative(l.relation))}</strong><p>${esc(narrative(l.reason))}</p>${rawFields(l)}`]),'跨票关联；打开原决策核对各自库存轮'):'<p class="tj-muted">尚未记录跨票关联。腾出资金与原交易逻辑失效分别记录。</p>'}<button type="button" class="secondary" data-tj-action="link">追加跨票关联</button><div id="tradeJournalLinkEditor"></div>`,'tj-link-section')}
      ${section('研究适配与归档边界','<button type="button" class="secondary" data-tj-action="context">核对研究适配</button><p class="tj-muted">引用已读取的确切研究快照作当前或事后对照，不把盘后结果当作历史成交时已知事实。</p><div id="tradeJournalContextEditor"></div><div id="tradeJournalContextResult"></div>')}
      ${section('逐笔模式股数归属','<p class="tj-muted">标签与股数分配分开。每笔真实股数只归属一次，卖出多成本批需明确实际FIFO批次；未分配不平均分利润。</p><button type="button" class="secondary" data-tj-action="allocations">读取数量分配</button><div id="tradeJournalAllocation"></div><div id="tradeJournalAllocationEditor"></div>')}
      ${section('过程复盘','<p class="tj-muted">入场、持有、退出、调整、仓位与信息分别记录判断。未判断保留未知；已记录复盘不证明归因、纪律结论或收益优势。</p><div class="tj-actions"><button type="button" class="secondary" data-tj-action="process-review">读取复盘状态</button><button type="button" class="secondary" data-tj-action="process-review-history">复盘历史</button></div><p id="tradeJournalReviewStatus" class="tj-form-status" role="status" aria-live="polite"></p><div id="tradeJournalReview"></div><div id="tradeJournalReviewEditor"></div>')}
      ${section('既有成交回写的事实关联','<p class="tj-muted">逐笔核对既有成交回写与归档原行，明确属于同一账户的同一成交后才关联。同价同量不自动匹配；关联不再次改变现金、持仓或损益。</p><button type="button" class="secondary" data-tj-action="execution-matches">读取同日同证券的回写候选</button><p id="tradeJournalExecutionStatus" class="tj-form-status" role="status" aria-live="polite"></p><div id="tradeJournalExecutionMatches"></div><div id="tradeJournalExecutionEditor"></div><div id="tradeJournalExecutionPreview"></div>')}
      <details class="tj-section"><summary>财务事件与既有预案引用</summary><dl class="tj-facts">${field('财务事件',d.financial_events)}${field('只读引用',d.live_references)}</dl></details>`;
    window.JournalWorkspace?.setEpisode(d);
  }
  function options(category, selected='',frozen=[]) {
    const original=array(frozen).find(d=>d.id===selected&&d.id?.startsWith('mode.user.'))||(selected?.startsWith('mode.user.')?{id:selected,label:'历史定义版本待核',rule_version:''}:null),choices=state.definitions.filter(d=>d.category===category);
    if(original&&!choices.some(d=>d.id===original.id&&d.rule_version===original.rule_version))choices.unshift({...original,label:'原冻结版本 · '+original.label});
    return '<option value="">未知 / 保留原文</option>'+choices.map(d=>{const dynamic=d.id.startsWith('mode.user.'),value=dynamic?JSON.stringify({id:d.id,rule_version:d.rule_version}):d.id,chosen=d.id===selected&&(!dynamic||!original||d.rule_version===original.rule_version);return `<option value="${esc(value)}"${chosen?' selected':''}>${esc(d.label+(dynamic?' · '+d.rule_version:''))}</option>`;}).join('');
  }
  const input=(name,title,value='',type='text')=>`<label>${esc(title)}<input name="${name}" type="${type}" value="${esc(value)}" /></label>`;
  const area=(name,title,value='')=>`<label class="tj-span">${esc(title)}<textarea name="${name}" rows="2">${esc(value==null?'':typeof value==='object'?JSON.stringify(value,null,2):value)}</textarea></label>`;
  function editDecision(id='', fill='') {
    if(state.definitionsError || !state.definitions.length){status('术语未读取，暂不能保存带定义引用的补记。请先重新读取账本。','error');return;}
    const d=array(state.detail?.decisions).find(r=>r.decision_id===id)||{};
    const actualFill=fill?array(state.detail?.fills).find(r=>r.fill_id===fill):null;
    const actualAction=!id?({buy:'买入',sell:'卖出'}[actualFill?.side]||''):'';
    const actualActionId=!id&&actualFill?.side&&definition('action.'+actualFill.side)?'action.'+actualFill.side:'';
    const originalExpectation=d.expectation;
    const expectationNoteKey=originalExpectation&&typeof originalExpectation==='object'&&!Array.isArray(originalExpectation)&&!originalExpectation.metric&&!originalExpectation.operator&&originalExpectation.target==null?(typeof originalExpectation.text==='string'?'text':typeof originalExpectation.raw==='string'?'raw':''):'';
    const structuredEditors=[];
    const simpleField=(name,title,value)=>{
      if(value&&typeof value==='object'){
        structuredEditors.push(area(name,title+'（原结构化内容）',value));
        if(name==='expectation'&&expectationNoteKey)return area('expectation_note',title,value[expectationNoteKey]);
        let description=narrative(value,'');
        if(name==='expectation'&&value.metric&&value.operator&&value.target!=null){
          const metricName=({close_return_pct:'收盘涨幅',close_price:'收盘价'})[value.metric];
          if(metricName){const unit=value.metric==='close_return_pct'?'%':'元',basis=({entry_price:'相对买入价',previous_close:'相对前收盘价'})[value.baseline]||'';description=[value.text||value.raw||'',value.target_date||'',metricName+' '+value.operator+' '+value.target+unit,basis].filter(Boolean).join(' · ');}
        }
        if(!description||description==='已保存结构化内容，展开原始字段查看')description='已保存具体条件，可在“更多信息”中查看或修改。';
        return `<div class="tj-span">${field(title,description)}<p class="tj-muted">保留原有条件；需要修改时展开下方“更多信息”。</p></div>`;
      }
      return area(name,title,value);
    };
    const root=$('tradeJournalEditor');
    root.innerHTML=`<form id="tradeJournalDecisionForm" class="tj-editor" data-decision="${esc(id)}" data-revision="${esc(d.revision??'')}" data-original-mode="${esc(d.mode_id||'')}" data-definition-snapshots="${esc(JSON.stringify(recordDefinitions(d)))}" data-fills="${esc(JSON.stringify(fill?[fill]:array(d.fill_ids)))}" data-expectation-note-key="${esc(expectationNoteKey)}"><h4>${id?'补充这次交易':'补记这次交易'}${fill?' · 本笔成交':''}</h4><p class="tj-muted">先写记得的内容，不确定的留空。这里保存的是历史补记，原记录会保留。</p><div class="tj-form-grid">
      ${area('action','操作摘要（买入／加仓／减仓／卖出）',d.action||actualAction)}<p class="tj-muted tj-span">只写实际动作，例如“买入大业股份1000股，17.48元”。操作依据写在下面。</p>
      <label class="tj-span">交易模式<select name="mode_id">${options('mode',d.mode_id,recordDefinitions(d))}</select>${d.mode?`<small>原记录：${esc(narrative(d.mode))}</small>`:''}</label>
      ${area('reason','为什么做这笔交易（必填）',d.reason)}<p class="tj-muted tj-span">这里写判断依据。例如“板块走强、回踩承接恢复，判断有反弹机会”；这是填写示例，不代表这笔交易当时的理由。以上文本框均可直接按 Enter 换行。</p>${simpleField('expectation','当时期待什么、何时发生',d.expectation)}${simpleField('risk_constraints','原先打算买多少、最多承担多少损失',d.risk_constraints)}${simpleField('invalidation','什么情况出现就减仓或退出',d.invalidation)}
      </div><details class="tj-decision-more"><summary>更多信息</summary><div class="tj-form-grid">
      ${id?area('revision_reason','变更说明（可选）'):''}
      <label>操作类型<select name="action_id">${options('action',d.action_id||actualActionId)}</select></label>
      ${structuredEditors.join('')}${input('mode','原始模式描述',d.mode||'')}
      <label>触发 / 形态<select name="trigger_id">${options('trigger',d.trigger_id)}</select></label>${input('trigger','原始触发描述',d.trigger||'')}
      ${input('observed_at','实际观察时点（带时区，未知留空）',d.observed_at||'')}${input('available_at','信息当时可用时点（未知留空）',d.available_at||'')}${input('verification_at','下一验证时点（带时区）',d.verification_at||'')}${input('reference_tickers','参照证券（多个以逗号分隔）',array(d.reference_tickers).join(', '))}${input('holding_horizon','原定持有期限',typeof d.holding_horizon==='object'?text(d.holding_horizon):d.holding_horizon||'')}
      ${input('plan_id','既有预案标识（可选）',d.frozen_plan?.plan_id||'')}${input('plan_reference','精确版本 / 来源引用（可选）',d.frozen_plan?.raw_reference||'')}${area('evidence','证据来源与原文（可选）',d.evidence)}
      </div><p class="tj-muted">记录性质：${esc(label('evidence.retrospective_recall'))}。服务器生成记录时点；填写历史观察时间不会升级为事前记录。</p></details><p id="tradeJournalSaveStatus" class="tj-form-status" role="status" aria-live="polite"></p><div class="tj-actions"><button type="submit">保存这次补记</button><button type="button" class="secondary" data-tj-action="cancel-edit">收起</button></div></form>`;
    root.querySelector('textarea, select, input')?.focus({preventScroll:true});
    root.scrollIntoView?.({block:'start'});
  }
  function formValues(form) {const values={};for(const element of Array.from(form.elements||[])){if(element.name)values[element.name]=element.value.trim();}return values;}
  function evidenceItems(value, evidenceClass='retrospective_recall', observedAt='', availableAt='') {
    if(!value)return [];
    try {const parsed=JSON.parse(value);if(Array.isArray(parsed)&&parsed.every(x=>x&&typeof x==='object'))return parsed;}catch {}
    return [{content:value,evidence_class:evidenceClass,observed_at:observedAt||null,available_at:availableAt||null}];
  }
  function structuredValue(value) {try {const parsed=JSON.parse(value);if(parsed&&typeof parsed==='object')return parsed;}catch {}return value;}
  async function saveDecision(form) {
    if(state.saving)return;const v=formValues(form),exact=normalizeModeSelection(v,form), note=$('tradeJournalSaveStatus');
    if(v._modeSelectionError){note.textContent=v._modeSelectionError;return;}
    if(!v.reason?.trim()||(!v.action?.trim()&&!v.action_id)){note.textContent='请填写决策理由，并选择动作口径或填写原始动作。';note.dataset.state='error';form.elements.namedItem('reason')?.setAttribute('aria-invalid','true');return;}
    const id=form.dataset.decision;
    if(id&&!v.revision_reason)v.revision_reason='补充交易复盘内容';
    v.evidence_class='retrospective_recall';
    const body={...v, fill_ids:JSON.parse(form.dataset.fills||'[]'),definition_refs:definitionRefs([v.action_id,v.mode_id,v.trigger_id,'evidence.retrospective_recall'],exact),frozen_plan:v.plan_id||v.plan_reference?{plan_id:v.plan_id,raw_reference:v.plan_reference}:null};
    const previous=array(state.detail?.decisions).find(r=>r.decision_id===id);
    if(previous?.frozen_plan)body.frozen_plan={...previous.frozen_plan,...(body.frozen_plan||{})};
    for(const field of ['expectation','invalidation','holding_horizon','risk_constraints'])body[field]=structuredValue(v[field]);
    const expectationNote=form.elements?.namedItem('expectation_note'),noteKey=form.dataset.expectationNoteKey;
    if(expectationNote&&['text','raw'].includes(noteKey)&&typeof previous?.expectation?.[noteKey]==='string'&&expectationNote.value!==previous.expectation[noteKey]){
      const original=body.expectation;
      if(!original||typeof original!=='object'||Array.isArray(original)||original.metric||original.operator||original.target!=null){note.textContent='预期格式已在“更多信息”中改变，请只在该处编辑，或重新打开本次补记。';note.dataset.state='error';return;}
      body.expectation={...original,[noteKey]:v.expectation_note};
    }
    delete body.expectation_note;
    body.evidence=evidenceItems(v.evidence,v.evidence_class,v.observed_at,v.available_at);
    body.reference_tickers=(v.reference_tickers||'').split(/[,，\s]+/).filter(Boolean);
    delete body.plan_id;delete body.plan_reference;
    if(!body.observed_at)delete body.observed_at;if(!body.available_at)delete body.available_at;if(!body.verification_at)delete body.verification_at;
    if(id){body.decision_id=id;body.expected_revision=Number(form.dataset.revision);}
    await saveForm(form,note,()=>api('/episodes/'+encodeURIComponent(state.selected)+'/decisions',{method:'POST',body:JSON.stringify(body)}),'已追加新版本；原记录保留。');
  }
  async function saveForm(form,note,request,message) {
    state.saving=true;form.setAttribute('aria-busy','true');form.querySelector('[type=submit]').disabled=true;note.textContent='正在保存…';note.dataset.state='loading';
    const episode=state.selected;
    try {await request();if(episode===state.selected){await selectEpisode(episode,{focus:false});status(message,'success');}}
    catch(error){note.textContent=`未保存：${error.message}。请保留本页内容，核对后重试。`;note.dataset.state='error';}
    finally {state.saving=false;form.setAttribute('aria-busy','false');const submit=form.querySelector('[type=submit]');if(submit)submit.disabled=false;}
  }
  async function showHistory(id) {
    const root=$('tj-history-'+id);if(!root)return;root.hidden=false;root.textContent='正在读取历史版本…';
    try{const p=await api('/decisions/'+encodeURIComponent(id)+'/history');root.innerHTML=array(p.items||p.history||p.records||p).map(d=>`<details><summary>版本 ${esc(d.revision)} · ${esc(d.recorded_at||'记录时点待核')}</summary>${renderDecision(d,{readOnly:true})}</details>`).join('')||'没有返回历史版本；请核对决策标识。';}
    catch(error){root.textContent='历史版本未读取：'+error.message;}
  }
  async function showLinkedDecision(id,revision) {
    artifact('关联的原决策','<p role="status">正在读取关联决策的原版本…</p>');
    try {
      const p=await api('/decisions/'+encodeURIComponent(id)+'/history'),records=array(p.items||p.history||p.records),record=revision?records.find(d=>Number(d.revision)===Number(revision)):records[records.length-1];
      if(!record)throw new Error(revision?'该关联的确切版本未返回，未以最新版本替代。':'没有返回原决策。');
      const episodeId=p.episode_id||record.episode_id;
      let episode=state.detail?.episode?.episode_id===episodeId?state.detail.episode:null,episodeError='';
      if(!episode&&episodeId)try{episode=(await api('/episodes/'+encodeURIComponent(episodeId))).episode;}catch(error){episodeError=error.message;}
      state.linkedDecisions=state.linkedDecisions||{};state.linkedDecisions[id+'@'+(revision||'latest')]={record,episode};
      artifact('关联的原决策',`<p><strong>${esc(episode?.name||episode?.ticker||'证券信息待核')}</strong> ${esc(episode?.ticker||'')} · ${revision?'关联时的确切版本':'关联未指定版本，当前读取版本'} ${esc(record.revision)}</p>${episodeError?`<p class="tj-muted">库存轮信息未读取：${esc(episodeError)}</p>`:''}${renderDecision(record,{readOnly:true})}${episodeId?`<button type="button" class="secondary" data-tj-action="linked-episode" data-id="${esc(episodeId)}">打开所在库存轮</button>`:''}`);
    } catch(error){artifact('原决策未读取',`<p role="alert">${esc(error.message)}</p><button type="button" data-tj-action="linked-decision" data-id="${esc(id)}" data-revision="${esc(revision||'')}">重新读取原决策</button>`);}
  }
  function checkEditor(id) {
    const root=$('tradeJournalEditor');
    const d=array(state.detail?.decisions).find(d=>d.decision_id===id);
    root.innerHTML=`<form id="tradeJournalCheckForm" class="tj-editor" data-decision="${esc(id)}" data-revision="${esc(d?.revision||'')}"><h4>补充预期核验</h4><label>核验方式<select name="evaluation_class" id="tradeJournalCheckClass"><option value="manual_assessment">人工复盘评估</option><option value="numeric_comparison">已归档数值比较</option></select></label><p class="tj-muted">人工评估保留判断；数值比较必须与已归档来源的字段和冻结目标核对。缺来源、时点未到或条件不完整时保持未知。</p><dl class="tj-facts">${field('本版本预期',d?.expectation)}${field('本版本验证时点',d?.verification_at)}</dl><div class="tj-form-grid" id="tradeJournalManualCheck"><label>人工核验结论<select name="status"><option value="" selected>请选择结论</option>${state.definitions.filter(d=>d.category==='expectation_check').map(d=>`<option value="${esc(d.id.split('.').slice(1).join('.'))}">${esc(d.label)}</option>`).join('')}</select></label>${area('actual','实际结果与观察窗口')}</div><div class="tj-form-grid" id="tradeJournalNumericCheck" hidden>${input('actual_source_id','已归档来源标识')}${input('value_path','原件JSON字段路径（例如items.0.close）')}${input('actual_metric','实际指标标识',typeof d?.expectation==='object'?d.expectation?.metric||'':'')}${input('actual_value','原件中的实际数值','','number')}${input('actual_observed_at','实际值观察时点（带时区）')}${input('actual_available_at','实际值可用时点（带时区）')}<p class="tj-muted tj-span">此处数值须与来源原文一致；服务端重新读取来源后比较，不以手填数字直接判通过。</p></div>${area('evidence','证据或缺数说明')}<p id="tradeJournalSaveStatus" class="tj-form-status" role="status"></p><button type="submit">追加核验记录</button></form>`;
  }
  function linkEditor() {
    const root=$('tradeJournalLinkEditor');
    root.innerHTML=`<form id="tradeJournalLinkForm" class="tj-editor"><h4>追加跨票决策关联</h4><div class="tj-form-grid"><label>起点决策<select name="from_decision_id">${array(state.detail?.decisions).map(d=>`<option value="${esc(d.decision_id)}">${esc(d.decision_id)} · ${esc(d.action_id?label(d.action_id):text(d.action))}</option>`).join('')}</select></label>${input('to_decision_id','终点决策标识')}${input('relation','关系原文（例如资金腾挪）')}${area('reason','关联理由')}${area('evidence','关联证据')}</div><p class="tj-muted">在另一库存轮复制终点决策标识后填写。关联保留原有库存轮，不合并损益。</p><p id="tradeJournalLinkStatus" class="tj-form-status" role="status"></p><button type="submit">保存关联</button></form>`;
  }
  const planRecord = payload => payload?.record||payload?.plan||payload;
  const planContent = record => record?.content && typeof record.content==='object' ? record.content : record||{};
  async function loadPlans({offset=state.planOffset}={}) {
    state.planOffset=offset;const note=$('tradeJournalPlanStatus');note.textContent='正在读取独立预案归档…';
    if(!state.definitions.length)await loadDefinitions();
    try {const f=readFilters(),p=await api('/plans?'+new URLSearchParams({query:f.query,start_date:f.start_date,end_date:f.end_date,limit:'20',offset:String(offset)}));state.plans=array(p.items);state.planTotal=Number(p.total)||0;
      $('tradeJournalPlans').innerHTML=state.plans.map(record=>{const d={...planContent(record),definition_snapshots:record.definition_snapshots,hasContent:!!record.content},revisions=array(record.revisions).length?record.revisions:[record.revision];return `<article class="tj-plan"><header><strong>${esc(record.name||d.name||record.ticker||d.ticker)} · ${esc(record.target_date||d.target_date||'目标日待补')}</strong><span>版本 ${esc(record.revision)}</span></header><dl class="tj-facts">${field('预案标识',record.plan_id)}${field('模式',d.hasContent===false?'按需读取该版本正文':d.mode_id?frozenLabel(d,d.mode_id):d.mode)}${field('触发 / 形态',d.hasContent===false?'按需读取该版本正文':d.trigger_id?frozenLabel(d,d.trigger_id):d.trigger)}${field('服务器保存时点',record.captured_at)}${field('字段完整度',record.complete===true?'字段齐备':record.complete===false?'仍有缺项':'未返回')}${field('事前证据',record.ex_ante_verifiable===true?'当前保存的证据满足核验要求':record.ex_ante_verifiable===false?'尚不满足事前核验要求':'资格未知')}</dl>${missing(record.missing_fields)}<div class="tj-actions">${revisions.map(revision=>`<button type="button" class="secondary" data-tj-action="view-preplan" data-id="${esc(record.plan_id)}" data-revision="${esc(revision)}">查看版本 ${esc(revision)}</button>`).join('')}<button type="button" class="secondary" data-tj-action="revise-preplan" data-id="${esc(record.plan_id)}" data-revision="${esc(record.revision)}">追加新版本</button></div></article>`;}).join('')||'<p class="tj-empty">当前筛选范围没有独立预案。首买前即可新建，未知字段可留空。</p>';
      $('tradeJournalPlans').innerHTML+=`<div class="tj-pagination"><button type="button" class="secondary" data-tj-action="plans-previous" ${offset===0?'disabled':''}>上一页</button><span>${state.planTotal?`${offset+1}–${offset+state.plans.length} / ${state.planTotal}`:'0 / 0'}</span><button type="button" class="secondary" data-tj-action="plans-next" ${offset+20>=state.planTotal?'disabled':''}>下一页</button></div>`;
      note.textContent=`已读取 ${state.planTotal} 份独立预案。字段齐备与事前证据分别显示，尚未绑定成交不代表执行过。`;note.dataset.state='ready';
    }catch(error){note.textContent='预案未读取：'+error.message;note.dataset.state='error';}
  }
  async function editPreplan(id='',revision='') {
    const note=$('tradeJournalPlanStatus');if(!state.definitions.length)await loadDefinitions();
    if(!state.definitions.length){note.textContent='定义尚未读取，请重试读取预案后再新建。';return;}
    let record={};
    if(id){try {record=planRecord(await api('/plans/'+encodeURIComponent(id)+'?revision='+encodeURIComponent(revision)));}catch(error){note.textContent='指定预案版本未读取：'+error.message;return;}}
    let sourceChoices=[],sourceError='';try {sourceChoices=array((await api('/sources?limit=1000&offset=0')).items);}catch(error){sourceError=error.message;}
    const d=planContent(record),q=readFilters().query;
    $('tradeJournalPlanEditor').innerHTML=`<form id="tradeJournalPlanForm" class="tj-editor" data-plan="${esc(id)}" data-revision="${esc(record.revision||'')}" data-original-mode="${esc(d.mode_id||'')}" data-definition-snapshots="${esc(JSON.stringify(recordDefinitions(record)))}" data-portfolio-snapshot-ref="${esc(JSON.stringify(d.portfolio_snapshot_ref||null))}"><h4>${id?'追加独立预案版本':'新建交易前预案'}</h4><p class="tj-muted">这是当下新保存的意图；服务器记录时点不可编辑。未知可保留，是否达到事前证据要求由保存结果说明。</p><div class="tj-form-grid">${input('ticker','股票代码（必填）',record.ticker||d.ticker||(/^\d{6}(\.(SZ|SH|BJ))?$/i.test(q)?q:''))}${input('name','证券名称',record.name||d.name||'')}${input('target_date','目标交易日（未知可留空）',record.target_date||d.target_date||'','date')}<label>交易模式<select name="mode_id">${options('mode',d.mode_id,recordDefinitions(record))}</select></label>${input('mode','原始模式描述',d.mode||'')}<label>触发 / 形态<select name="trigger_id">${options('trigger',d.trigger_id)}</select></label>${input('trigger','原始触发描述',d.trigger||'')}${id?area('revision_reason','本次修订原因（必填）'):''}${area('reason','独立理由',d.reason)}${area('expectation','正常预期：结果、窗口、比较基准',d.expectation)}${input('verification_at','预期验证时点（带时区）',d.verification_at||'')}${area('invalidation','失效与退出条件',d.invalidation)}${input('holding_horizon','模式绑定的持有期限',typeof d.holding_horizon==='object'?text(d.holding_horizon):d.holding_horizon||'')}${area('risk_constraints','风险预算 / 数量与最大亏损边界',d.risk_constraints)}${input('reference_tickers','参照证券（逗号分隔）',array(d.reference_tickers).join(', '))}${area('evidence','证据说明或已有结构化引用（可选）',d.evidence)}${area('observation_source_text','保存本次观察原文作为证据（人工观察，非行情实测）')}<label>或选择已有ECS来源<select name="evidence_source_id"><option value="">不引用已有来源</option>${sourceChoices.map(s=>`<option value="${esc(s.source_id)}">${esc(s.filename||s.name||s.source_id)} · ${esc(s.recorded_at||'时点待核')}</option>`).join('')}</select></label>${sourceError?`<p class="tj-muted">已有来源未读取：${esc(sourceError)}。可保留未知或保存本次观察原文。</p>`:''}${input('evidence_row','已有来源行号（或填写下方记录定位）','','number')}${input('evidence_record_id','已有来源记录定位（可选）')}${input('evidence_observed_at','所述证据发生时点（未知留空）')}${input('evidence_available_at','所述证据可用时点（未知留空）')}${input('live_plan_id','引用既有执行预案标识（可选）',d.live_plan_ref?.plan_id||'')}${input('live_plan_version','既有执行预案确切版本（可选）',d.live_plan_ref?.version||'')}</div><p class="tj-muted">观察原文按实际保存时点归档，可恢复和核对校验；手工观察不等于机器行情验证。预案保存与下单分别进行，未知时间不会自动补成已知。</p><div class="tj-section"><p id="tradeJournalPlanPortfolioStatus" class="tj-muted">${esc(d.portfolio_snapshot_ref?'已关联当前事实归档 · '+d.portfolio_snapshot_ref.captured_at:'尚未关联账户事实快照；当前事实不能回填历史。')}</p><div class="tj-actions"><button type="button" class="secondary" data-tj-action="attach-portfolio">附当前账户事实</button><button type="button" class="secondary" data-tj-action="view-plan-portfolio">查看已附事实</button><button type="button" class="secondary" data-tj-action="clear-plan-portfolio">清除关联</button></div><p class="tj-muted">保存服务器当前事实后才建立确切引用；此处不包含未执行情景，也不修改生产风险设置。</p></div><p id="tradeJournalPlanSaveStatus" class="tj-form-status" role="status" aria-live="polite"></p><div class="tj-actions"><button type="submit">保存预案版本</button><button type="button" class="secondary" data-tj-action="cancel-preplan">收起</button></div></form>`;
  }
  async function savePreplan(form) {
    if(state.planSaving)return;
    const v=formValues(form),exact=normalizeModeSelection(v,form),note=$('tradeJournalPlanSaveStatus'),id=form.dataset.plan;
    if(v._modeSelectionError){note.textContent=v._modeSelectionError;return;}
    if(!v.ticker){note.textContent='请填写股票代码；其余未知条件可以留空并保留为缺项。';return;}
    if(id&&!v.revision_reason){note.textContent='追加预案版本须填写本次修订原因。';return;}
    const body={};for(const key of ['ticker','name','target_date','mode_id','mode','trigger_id','trigger','reason','expectation','verification_at','invalidation','holding_horizon','risk_constraints','revision_reason'])if(key in v)body[key]=['expectation','invalidation','holding_horizon','risk_constraints'].includes(key)?structuredValue(v[key]):v[key];
    if(!body.verification_at)body.verification_at=null;
    try{body.portfolio_snapshot_ref=JSON.parse(form.dataset.portfolioSnapshotRef||'null');}catch{note.textContent='账户事实引用无效，请重新归档并关联。';return;}
    body.reference_tickers=(v.reference_tickers||'').split(/[,，\s]+/).filter(Boolean);body.definition_refs=definitionRefs([v.mode_id,v.trigger_id],exact);
    body.evidence=evidenceItems(v.evidence,'contemporaneous',v.evidence_observed_at,v.evidence_available_at);
    if(v.evidence_source_id){const source={source_id:v.evidence_source_id};if(v.evidence_row)source.row=Number(v.evidence_row);if(v.evidence_record_id)source.record_id=v.evidence_record_id;if(!body.evidence.length)body.evidence=evidenceItems('已归档来源引用','contemporaneous',v.evidence_observed_at,v.evidence_available_at);for(const item of body.evidence)if(!item.source_ref)item.source_ref=source;}
    if(v.live_plan_id||v.live_plan_version){if(!v.live_plan_id||!v.live_plan_version){note.textContent='引用既有执行预案须同时填写标识与确切版本。';return;}body.live_plan_ref={plan_id:v.live_plan_id,version:/^\d+$/.test(v.live_plan_version)?Number(v.live_plan_version):v.live_plan_version};}else body.live_plan_ref=null;
    if(id){body.plan_id=id;body.expected_revision=Number(form.dataset.revision);}
    state.planSaving=true;form.setAttribute('aria-busy','true');const button=form.querySelector('[type=submit]');button.disabled=true;note.textContent='正在保存独立预案版本…';
    try {
      const rawSource=Array.from(form.elements||[]).find(e=>e.name==='observation_source_text')?.value||'';
      if(rawSource.trim()){
        if(!globalThis.crypto?.subtle)throw new Error('当前浏览器无法计算原文校验，请使用本机安全入口访问，或先选择已有来源');
        const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(rawSource))),v=>v.toString(16).padStart(2,'0')).join(''),sourceId='src-'+hash;
        body.sources=[{source_id:sourceId,sha256:hash,filename:'用户观察原文.txt',content_encoding:'utf-8',content:rawSource,evidence_class:'contemporaneous',metadata:{provenance_class:'manual_observation'}}];
        const reference={source_id:sourceId,row:1};let attached=false;
        for(const item of body.evidence)if(!item.source_ref){item.source_ref=reference;attached=true;}
        if(!attached)body.evidence.push({text:'本次保存的用户观察原文；不是机器行情实测',source_ref:reference,evidence_class:'contemporaneous',observed_at:v.evidence_observed_at||null,available_at:v.evidence_available_at||null});
      }
      const result=await api('/plans',{method:'POST',body:JSON.stringify(body)});$('tradeJournalPlanEditor').innerHTML='';await loadPlans({offset:0});const record=result.record||{};$('tradeJournalPlanStatus').textContent=`已保存 ${result.plan_id} · 版本 ${result.revision}；服务器时点 ${record.captured_at||'以详情为准'}。${result.ex_ante_verifiable===true?'事前证据核验结果可在详情查看。':'当前仍有事前证据或条件缺项。'} 未发送任何订单。`;await viewPreplan(result.plan_id,result.revision);
    }
    catch(error){note.textContent='预案未保存：'+error.message;note.dataset.state='error';}
    finally {state.planSaving=false;form.setAttribute('aria-busy','false');button.disabled=false;}
  }
  async function viewPreplan(id,revision) {
    artifact('独立预案版本','<p role="status">正在读取确切预案版本…</p>');
    try {const record=planRecord(await api('/plans/'+encodeURIComponent(id)+'?revision='+encodeURIComponent(revision)));
      artifact('独立预案 · 版本 '+record.revision,`<p class="tj-muted">这是单独归档的预案意图，不是订单或持仓事实。绑定历史成交不会改写原保存时点。</p><dl class="tj-facts">${field('预案标识',record.plan_id)}${field('证券',record.ticker)}${field('目标交易日',record.target_date)}${field('版本',record.revision)}${field('服务器保存时点',record.captured_at)}${field('字段齐备',record.complete)}${field('事前可核验证据',record.ex_ante_verifiable)}${field('本版本内容',record.content)}${field('确切执行预案引用',record.live_reference)}${field('内容校验',record.content_digest||record.content_hash)}${field('时点问题',record.temporal_errors)}</dl>${missing(record.missing_fields)}`);
    }catch(error){artifact('预案版本未读取',`<p>${esc(error.message)}</p>`);}
  }
  async function bindPlanEditor() {
    const ep=state.detail?.episode;if(!ep)return;
    const root=$('tradeJournalBindEditor');root.innerHTML='<p role="status">正在读取同证券的已存预案版本…</p>';
    try {const payload=await api('/plans?'+new URLSearchParams({ticker:ep.ticker,limit:'100',offset:'0'}));state.bindPlans=array(payload.items).filter(p=>p.ticker===ep.ticker);const choices=state.bindPlans.flatMap(p=>(array(p.revisions).length?p.revisions:[p.revision]).map(revision=>({plan_id:p.plan_id,revision,target_date:p.target_date})));
      if(!choices.length){root.innerHTML='<p class="tj-empty">该证券尚无已冻结预案。可以在顶部独立预案区新建；新建记录不能追溯成为过去成交前的计划。</p>';return;}
      root.innerHTML=`<form id="tradeJournalBindPlanForm" class="tj-editor" data-episode="${esc(ep.episode_id)}"><h4>绑定已冻结预案 · ${esc(ep.name||ep.ticker)}</h4><label>确切预案版本<select name="plan_choice"><option value="" selected>请选择预案与版本</option>${choices.map(p=>`<option value="${esc(JSON.stringify({plan_id:p.plan_id,revision:p.revision}))}">${esc(p.target_date||'目标日待补')} · ${esc(p.plan_id)} · v${esc(p.revision)}</option>`).join('')}</select></label><fieldset class="tj-fill-choices"><legend>明确关联的逐笔成交（至少一笔）</legend>${array(state.detail.fills).map(f=>`<label><input type="checkbox" name="fill_ids" value="${esc(f.fill_id)}" />${esc(f.executed_at)} · ${esc(f.side==='buy'?'买入':'卖出')} ${num(f.quantity,0)} 股 · ${num(f.price)}</label>`).join('')}</fieldset>${area('reason','本次绑定的依据（必填）')}<p class="tj-muted">只绑定所选版本和成交；不自动使用最新预案，不表示成交符合计划。原冻结时间晚于成交时，仍属于事后关联。</p><p id="tradeJournalBindStatus" class="tj-form-status" role="status" aria-live="polite"></p><button type="submit">确认绑定该版本</button></form>`;
    }catch(error){root.innerHTML=`<p class="tj-empty">预案未读取：${esc(error.message)}</p>`;}
  }
  async function savePlanBinding(form) {
    const v=formValues(form),note=$('tradeJournalBindStatus'),fillIds=Array.from(form.elements||[]).filter(e=>e.name==='fill_ids'&&e.checked).map(e=>e.value);let choice;
    try {choice=JSON.parse(v.plan_choice);}catch {note.textContent='请选择确切的预案与版本。';return;}
    if(!fillIds.length||!v.reason){note.textContent='请至少选一笔成交，并填写本次绑定依据。';return;}
    const episode=state.detail?.episode;if(!episode||form.dataset.episode!==episode.episode_id){note.textContent='库存轮已切换，请在当前轮重新选择。';return;}
    await saveForm(form,note,async()=>{const record=planRecord(await api('/plans/'+encodeURIComponent(choice.plan_id)+'?revision='+encodeURIComponent(choice.revision)));if(record.ticker!==episode.ticker||record.revision!==choice.revision)throw new Error('预案证券或版本不一致');return api('/plans/'+encodeURIComponent(choice.plan_id)+'/bind',{method:'POST',body:JSON.stringify({episode_id:episode.episode_id,fill_ids:fillIds,plan_revision:choice.revision,revision:choice.revision,reason:v.reason})});},'已绑定指定预案版本与所选成交；事前资格仍按原冻结时点和来源核验。');
  }
  function artifact(title, html) {
    $('tradeJournalArtifactTitle').textContent=title;$('tradeJournalArtifactBody').innerHTML=html;
    if(!$('tradeJournalArtifact').open)$('tradeJournalArtifact').showModal();
  }
  function researchLink(ref) {
    return ref?.analysis_date && ref?.snapshot_id ? `<a class="tj-link" href="/ui?decision=research&amp;date=${encodeURIComponent(ref.analysis_date)}&amp;snapshot=${encodeURIComponent(ref.snapshot_id)}">查看引用的固定研究快照</a>` : '';
  }
  function showPlan(id) {
    const d=array(state.detail?.decisions).find(r=>r.decision_id===id), p=d?.frozen_plan;
    if(!p)return;
    if(p.origin==='journal_plan_reference'&&p.plan_id&&p.revision){viewPreplan(p.plan_id,p.revision);return;}
    artifact('该决策的冻结预案',`<p class="tj-muted">展示本条决策保存的确切版本；当前预案的后来修改不覆盖此记录。</p><dl class="tj-facts">${Object.entries(p).map(([key,value])=>field({plan_id:'预案标识',captured_at:'本版本保存时点',content_hash:'内容校验',content:'预案内容',external_reference:'外部引用',historical_freeze_verified:'历史冻结已核验',reference_only:'仅有引用',snapshot_complete:'内容完整'}[key]||key,value)).join('')}</dl>${researchLink(p.mode_research_ref||p.content?.mode_research_ref||p.external_reference?.mode_research_ref)}`);
  }
  async function loadSources({offset=state.sourceOffset}={}) {
    state.sourceOffset=offset;const note=$('tradeJournalSourceStatus');note.textContent='正在读取来源与导入批次…';
    const result=await Promise.allSettled([api(`/sources?limit=20&offset=${offset}`),api('/batches?limit=20&offset=0')]);
    if(result[0].status==='rejected') {note.textContent='来源未读取：'+result[0].reason.message;note.dataset.state='error';return;}
    const p=result[0].value;state.sources=array(p.items);state.sourceTotal=Number(p.total)||0;
    $('tradeJournalSources').innerHTML=table(['来源原件','类型 / 时点','校验 / 字节','查看'],state.sources.map(s=>[esc(s.filename||s.name||shortId(s.source_id)),`${esc(s.source_type||'类型待核')}<small>${esc(s.recorded_at||'时点待核')}</small>`,`<span title="${esc(s.sha256||s.content_hash||'校验待核')}">${esc(shortId(s.sha256||s.content_hash||'校验待核'))}</span><small>${num(s.bytes??s.size_bytes??s.byte_count??s.content_bytes,0)} 字节</small>${rawFields(s)}`,`<button type="button" class="secondary" data-tj-action="source" data-id="${esc(s.source_id)}">查看与下载</button>`]),'已导入原件；不以派生表替代来源')+`<div class="tj-pagination"><button type="button" class="secondary" data-tj-action="sources-previous" ${offset===0?'disabled':''}>上一页</button><span>${state.sourceTotal?`${offset+1}–${offset+state.sources.length} / ${state.sourceTotal}`:'0 / 0'}</span><button type="button" class="secondary" data-tj-action="sources-next" ${offset+20>=state.sourceTotal?'disabled':''}>下一页</button></div>`+
      `<details><summary>最近导入批次</summary>${result[1].status==='fulfilled'?array(result[1].value.items).map(b=>`<dl class="tj-facts">${field('批次',b.batch_id)}${field('记录时点',b.recorded_at)}${field('导入状态',b.status)}${field('来源标识',b.source_ids)}</dl>${importCountSummary(b.counts)}${rawFields(b)}`).join('')||'<p>尚无导入批次。</p>':`<p>批次未读取：${esc(result[1].reason.message)}</p>`}</details>`;
    note.textContent=`已读取 ${state.sourceTotal} 份来源；原件内容按需读取。`;note.dataset.state='ready';
  }
  async function showSource(id) {
    artifact('来源原件','<p role="status">正在读取来源元信息…</p>');
    try {const p=await api('/sources/'+encodeURIComponent(id)),s=p.source||p.record||p;
      const metadata=Object.fromEntries(Object.entries(s).filter(([key])=>key!=='content'));
      artifact('来源原件',`<dl class="tj-facts">${field('原始文件名',s.filename||s.name)}${field('来源类型',s.source_type)}${field('归档时点',s.recorded_at)}${field('内容编码',s.content_encoding)}${field('字节数',num(s.bytes??s.size_bytes??s.byte_count??s.content_bytes,0))}${field('内容校验',shortId(s.sha256||s.content_hash))}</dl>${rawFields(metadata)}<button type="button" data-tj-action="download-source" data-id="${esc(id)}">下载已归档原件</button><p id="tradeJournalDownloadStatus" role="status" aria-live="polite"></p>`);
    }catch(error){artifact('来源未读取',`<p>${esc(error.message)}</p><button type="button" data-tj-action="source" data-id="${esc(id)}">重试读取</button>`);}
  }
  async function downloadSource(id) {
    const note=$('tradeJournalDownloadStatus');if(note)note.textContent='正在读取已归档原件…';
    try {const p=await api('/sources/'+encodeURIComponent(id)+'?include_content=true'),s=p.source||p.record||p;
      if(s.content==null)throw new Error('服务未返回原件内容，请检查来源归档');
      let bytes=s.content;
      if(s.content_encoding==='base64'){const raw=atob(s.content);bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));}
      else if(s.content_encoding!=='utf-8' && s.content_encoding!=='utf8')throw new Error('原件编码未声明，暂不能恢复下载');
      const url=URL.createObjectURL(new Blob([bytes],{type:'application/octet-stream'})),link=document.createElement('a');
      link.href=url;link.download=String(s.filename||s.name||s.source_id+'.bin').replace(/[/\\]/g,'_');link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      if(note)note.textContent='已读取原件并发起本地保存。';
    }catch(error){if(note)note.textContent='原件未下载：'+error.message;}
  }
  async function loadObservations({offset=state.observationOffset}={}) {
    const f=readFilters();state.observationOffset=offset;const note=$('tradeJournalObservationStatus');note.textContent='正在读取独立观察归档…';
    try {const p=await api('/observations?'+new URLSearchParams({query:f.query,start_date:f.start_date,end_date:f.end_date,limit:'10',offset:String(offset)}));state.observations=array(p.items);state.observationTotal=Number(p.total)||0;
      $('tradeJournalObservations').innerHTML=state.observations.map(o=>`<article class="tj-observation"><header><strong>${esc(o.name||o.ticker||'研究候选名单')}</strong><span>${esc(label('observation_state.'+o.state))}</span></header><dl class="tj-facts">${field('观察标识',o.observation_id)}${field('父观察标识',o.parent_observation_id)}${field('范围',label('scope.'+o.scope))}${field('分母边界',o.denominator_note)}${field('研究样本形成 / 反馈日',[o.source_ref?.formation_date||'待核',o.source_ref?.feedback_date||'待核'].join(' → '))}${field('所述观察时点',o.observed_at)}${field('实际记录时点',o.recorded_at)}${field('记录类别',label('evidence.'+o.evidence_class))}${field('理由',o.reason)}${field('成员数',o.member_count??array(o.members).length)}</dl>${researchLink(o.mode_research_ref)}${o.scope==='strategy_cohort'||o.member_count>0?`<button type="button" class="secondary" data-tj-action="observation-members" data-id="${esc(o.observation_id)}">读取原样成员</button><div id="tj-observation-members-${esc(o.observation_id)}"></div>`:`<button type="button" class="secondary" data-tj-action="observe-status" data-id="${esc(o.observation_id)}" data-ticker="${esc(o.ticker||'')}">追加个人观察状态</button>`}</article>`).join('')||'<p class="tj-empty">此筛选范围没有观察归档。可先读取模式收益快照，再保存候选名单。</p>';
      $('tradeJournalObservations').innerHTML+=`<div class="tj-pagination"><button type="button" class="secondary" data-tj-action="observations-previous" ${offset===0?'disabled':''}>上一页</button><span>${state.observationTotal?`${offset+1}–${offset+state.observations.length} / ${state.observationTotal}`:'0 / 0'}</span><button type="button" class="secondary" data-tj-action="observations-next" ${offset+10>=state.observationTotal?'disabled':''}>下一页</button></div>`;
      note.textContent=`已读取 ${state.observationTotal} 条观察记录；不计入实际成交收益。`;note.dataset.state='ready';
    }catch(error){note.textContent='观察归档未读取：'+error.message;note.dataset.state='error';}
  }
  async function loadObservationMembers(id) {
    const root=$('tj-observation-members-'+id);if(!root)return;root.innerHTML='<p role="status">正在恢复该观察的精确名单…</p>';
    try {const payload=await api('/observations/'+encodeURIComponent(id)),o=payload.record||payload.observation||payload;let members=array(o.members);
      if(o.snapshot_ref?.snapshot_id){const p=await api('/snapshots/'+encodeURIComponent(o.snapshot_ref.snapshot_id)+'?include_content=true'),snapshot=p.record||p.snapshot||p;members=array(snapshot.content?.daily?.members);}
      const index=state.observations.findIndex(x=>x.observation_id===id);if(index>=0)state.observations[index]={...o,members};
      root.innerHTML=table(['证券','个人观察状态'],members.map(m=>[esc(m.name||m.ticker),`<button type="button" class="secondary" data-tj-action="observe-status" data-id="${esc(id)}" data-ticker="${esc(m.ticker)}">追加个人观察状态</button>`]),'恢复自归档原件的完整名单；不补造个人收益');
    }catch(error){root.innerHTML=`<p>观察名单未恢复：${esc(error.message)}</p><button type="button" class="secondary" data-tj-action="observation-members" data-id="${esc(id)}">重试读取</button>`;}
  }
  function previewResearchObservation() {
    const r=window.TradeDecision?.state?.research,owner=window.TradeDecision?.state;
    if(!r){$('tradeJournalObservationEditor').innerHTML='<div class="tj-empty"><p>尚未读取模式收益研究快照。先打开模式收益页读取已有评估，再返回保存候选。</p><button type="button" data-tj-action="research-page">打开模式收益</button></div>';return;}
    const windows=r.assessments?.windows||{};
    const windowKey=state.researchSelection?.snapshot_id===r.snapshot_id?state.researchSelection.window:String(owner.evalWindow||Object.keys(windows)[0]||'');
    const modes=windows[windowKey]||{},modeKey=state.researchSelection?.snapshot_id===r.snapshot_id?state.researchSelection.mode:owner.mode||Object.keys(modes)[0];
    const mode=modes[modeKey]||Object.values(modes)[0],days=array(mode?.daily);
    const chosenDate=state.researchSelection?.snapshot_id===r.snapshot_id?state.researchSelection.date:'';
    const day=days.find(d=>d.date===chosenDate)||days.at(-1);
    if(!day||!Array.isArray(day.members)){$('tradeJournalObservationEditor').innerHTML='<p class="tj-empty">当前快照没有可恢复的逐日成员名单；无法将目录或汇总当作完整候选保存。请读取含成员的评估版本。</p>';return;}
    const ref=Object.fromEntries(['analysis_date','version','snapshot_id','built_at'].map(k=>[k,r[k]]));
    const complete=Object.values(ref).every(Boolean)&&day.formation_date&&day.date;
    state.researchSelection={snapshot_id:r.snapshot_id,window:windowKey,mode:mode.key,date:day.date};
    state.observationDraft={scope:complete?'strategy_cohort':'manual_watchlist',state:'watching',name:`${mode.name||mode.key} · ${day.formation_date||day.date}`,members:JSON.parse(JSON.stringify(day.members)),...(complete?{mode_research_ref:ref}:{}),source_ref:{snapshot_id:r.snapshot_id,mode_key:mode.key,window:windowKey,formation_date:day.formation_date,feedback_date:day.date},evidence_class:'historical_reconstruction',definition_refs:[]};
    $('tradeJournalObservationEditor').innerHTML=`<form id="tradeJournalObservationForm" class="tj-editor"><h4>保存研究快照候选</h4><div class="tj-form-grid"><label>研究窗口<select id="tradeJournalObservationWindow">${Object.keys(windows).map(key=>`<option value="${esc(key)}"${key===windowKey?' selected':''}>${esc(key)} 日</option>`).join('')}</select></label><label>玩法<select id="tradeJournalObservationMode">${Object.values(modes).map(m=>`<option value="${esc(m.key)}"${m.key===mode.key?' selected':''}>${esc(m.name||m.key)}</option>`).join('')}</select></label><label>样本反馈日<select id="tradeJournalObservationDay">${days.map(d=>`<option value="${esc(d.date)}"${d.date===day.date?' selected':''}>${esc(d.formation_date||'形成日未知')} → ${esc(d.date)}</option>`).join('')}</select></label>${area('reason','保存该候选范围的理由（必填）')}</div><p class="tj-muted">原样保留 ${day.members.length} 个成员。${complete?'服务端将核验完整快照与全部成员；不通过就不保存。':'快照引用不完整，本次仅保存为人工观察名单。'} 此次为历史研究重建，记录时点为保存当下，不冒充过去已选中。</p><details><summary>查看将保存的原始成员</summary><dl class="tj-facts">${field('快照引用',ref)}${field('成员',day.members)}</dl></details><p id="tradeJournalObservationSaveStatus" class="tj-form-status" role="status"></p><div class="tj-actions"><button type="submit">确认保存观察名单</button><button type="button" class="secondary" data-tj-action="cancel-observation">收起</button></div></form>`;
  }
  function observationStatusEditor(id,ticker) {
    const parent=state.observations.find(o=>o.observation_id===id);if(!parent||!ticker)return;
    const m=array(parent.members).find(m=>m.ticker===ticker)||parent;
    state.observationDraft={scope:'manual_watchlist',ticker,name:m.name,parent_observation_id:id,source_ref:parent.source_ref||null,evidence_class:'retrospective_recall',definition_refs:[]};
    $('tradeJournalObservationEditor').innerHTML=`<form id="tradeJournalObservationForm" class="tj-editor"><h4>${esc(m.name||ticker)} · 追加个人观察状态</h4><label>状态<select name="state"><option value="" selected>请选择状态</option>${state.definitions.filter(d=>d.category==='observation_state').map(d=>`<option value="${esc(d.id.split('.').slice(1).join('.'))}">${esc(d.label)}</option>`).join('')}</select></label>${area('reason','未买 / 未触发 / 未成交等实际原因（必填）')}<p class="tj-muted">追加个人观察记录，保留原候选队列；不会创建模拟成交或填写假设收益。</p><p id="tradeJournalObservationSaveStatus" class="tj-form-status" role="status"></p><button type="submit">保存观察状态</button></form>`;
  }
  async function saveObservation(form) {
    const v=formValues(form),note=$('tradeJournalObservationSaveStatus');if(!v.reason||!state.observationDraft){note.textContent='请填写观察理由，再确认当前候选范围。';return;}
    if(state.observationDraft.scope==='manual_watchlist'&&!v.state&&!state.observationDraft.state){note.textContent='观察状态未读取，请重新读取术语后再保存。';return;}
    const button=form.querySelector('[type=submit]');button.disabled=true;note.textContent='正在保存独立观察记录…';
    try {await api('/observations',{method:'POST',body:JSON.stringify({...state.observationDraft,...v})});$('tradeJournalObservationEditor').innerHTML='';await loadObservations({offset:0});$('tradeJournalObservationStatus').textContent+=' 已保存新观察记录。';}
    catch(error){note.textContent='观察未保存：'+error.message;note.dataset.state='error';}
    finally {button.disabled=false;}
  }
  function contextEditor() {
    const r=window.TradeDecision?.state?.research,owner=window.TradeDecision?.state,root=$('tradeJournalContextEditor');
    if(!r?.analysis_date||!r?.snapshot_id){root.innerHTML='<p class="tj-empty">尚无确切研究快照。先在模式收益读取指定日期评估，再返回核对。</p><button type="button" class="secondary" data-tj-action="research-page">打开模式收益</button>';return;}
    const windows=r.assessments?.windows||{},windowKey=String(owner.evalWindow||Object.keys(windows)[0]||''),modes=windows[windowKey]||{};
    state.contextResearch=r;
    root.innerHTML=`<form id="tradeJournalContextForm" class="tj-editor" data-episode="${esc(state.selected)}" data-date="${esc(r.analysis_date)}" data-snapshot="${esc(r.snapshot_id)}"><h4>核对指定研究归档</h4><p class="tj-muted">研究日 ${esc(r.analysis_date)} · ${esc(r.snapshot_id)}。此处是当前 / 事后对照，不能证明历史成交当时已知。</p><div class="tj-form-grid"><label>对照决策版本<select name="decision_choice"><option value="">${array(state.detail?.decisions).length?'请选择决策版本':'尚无决策，仅作研究对照'}</option>${array(state.detail?.decisions).map(d=>`<option value="${esc(JSON.stringify({decision_id:d.decision_id,decision_revision:d.revision}))}">${esc(d.decision_id)} · v${esc(d.revision)}</option>`).join('')}</select></label><label>研究窗口<select name="window" id="tradeJournalContextWindow">${Object.keys(windows).map(key=>`<option value="${esc(key)}"${key===windowKey?' selected':''}>${esc(key)} 日</option>`).join('')}</select></label><label>研究玩法（显式对照，不改绑模式）<select name="mode_key" id="tradeJournalContextMode">${Object.values(modes).map(m=>`<option value="${esc(m.key)}"${m.key===owner.mode?' selected':''}>${esc(m.name||m.key)}</option>`).join('')}</select></label>${input('previous_analysis_date','可选前照研究日','','date')}${input('previous_snapshot_id','可选前照确切快照标识')}</div><p id="tradeJournalContextStatus" class="tj-form-status" role="status" aria-live="polite"></p><button type="submit">读取该版本适配证据</button></form>`;
  }
  async function loadContext(form) {
    const v=formValues(form),note=$('tradeJournalContextStatus'),root=$('tradeJournalContextResult');
    if(form.dataset.episode!==state.selected){note.textContent='库存轮已切换，请重新选择研究对照。';return;}
    if(array(state.detail?.decisions).length&&!v.decision_choice){note.textContent='请明确选择决策版本，不自动选取最新决策。';return;}
    if(Boolean(v.previous_analysis_date)!==Boolean(v.previous_snapshot_id)){note.textContent='前照必须同时提供研究日与确切快照标识。';return;}
    const params={analysis_date:form.dataset.date,snapshot_id:form.dataset.snapshot,mode_key:v.mode_key,window:v.window};
    if(v.decision_choice){try {Object.assign(params,JSON.parse(v.decision_choice));}catch {note.textContent='决策版本选择无效，请重新选择。';return;}}
    if(v.previous_snapshot_id){params.previous_analysis_date=v.previous_analysis_date;params.previous_snapshot_id=v.previous_snapshot_id;}
    const episode=state.selected;note.textContent='正在读取确切研究适配证据…';root.innerHTML='';
    try {const p=await api('/episodes/'+encodeURIComponent(episode)+'/context?'+new URLSearchParams(params));if(episode!==state.selected)return;
      const fit=p.plan_fit||{},eventTable=rows=>table(['字段 / 范围','前照','当前','来源与边界'],array(rows).map(e=>[esc(e.field||e.kind),esc(text(e.before)),esc(text(e.after)),esc(text({before:e.before_ref,after:e.after_ref,inference:e.inference}))]),'仅描述明确归档间的所有者输出差异');
      root.innerHTML=`<article class="tj-context"><p class="tj-muted">研究适配不改变交易轮资格，也不授予下单权限。采用情况和历史可知时点分别核验。</p>${researchLink(p.source_refs?.research)}<dl class="tj-facts">${field('引用的决策版本',p.decision_ref)}${field('既有玩法判断',fit.status_label||fit.status)}${field('既有判断依据',fit.reasons)}${field('既有反证',fit.risks)}${field('后续核对条件',fit.next_checks)}${field('当时已知边界',p.time_qualification)}${field('实际采用已核验',fit.adoption_verified===true?'已核验':'尚未核验')}</dl>${section('反证与限制',table(['范围','既有反证','精确来源'],array(p.counters).map(c=>[esc(c.scope),esc(c.text),esc(text(c.source_ref))]),'原所有者反证，保留来源'))}${section('市场与玩法归档差异',array(p.market_events).length?eventTable(p.market_events):'<p class="tj-muted">没有返回可比较的变化记录；不据此断言市场未变化。</p>')}${section('原固定队列角色与变化',`<dl class="tj-facts">${field('当期角色',p.roles)}</dl>${array(p.role_events).length?eventTable(p.role_events):'<p class="tj-muted">没有返回可核验的角色变化；核心、前排与后排仍受归档范围约束。</p>'}`)}<details><summary>精确引用与归档边界</summary><dl class="tj-facts">${field('全部来源引用',p.source_refs)}${field('输入存储与ECS核验边界',p.input_storage)}${field('既有交易轮资格（本桥不调整）',p.journal_qualification)}${field('回放证据',p.replay_evidence)}${field('限制',p.limitations)}</dl>${missing(p.gaps)}</details></article>`;
      note.textContent='已读取指定版本。缺归档与不适配分别保留，未回退到最新快照。';
    }catch(error){note.textContent='研究适配未读取：'+error.message;note.dataset.state='error';}
  }
  async function loadAllocations() {
    const episode=state.selected,root=$('tradeJournalAllocation');root.innerHTML='<p role="status">正在读取真实股数与既有FIFO归属…</p>';
    try{if(!state.definitions.length)await loadDefinitions();const p=await api('/episodes/'+encodeURIComponent(episode)+'/allocations');if(episode!==state.selected)return;state.allocationEpisode=episode;state.allocationRecord=p.record||null;state.allocation=p.allocation;renderAllocations();const editor=$('tradeJournalAllocationForm');if(editor&&Number(editor.dataset.baseRevision||0)!==(p.record?.revision||0)){editor.dataset.stale='true';editor.querySelector('[type=submit]').disabled=true;$('tradeJournalAllocationStatus').textContent='已读取其他分配版本；当前编辑表仍保留原基线，须重新打开完整分配表后保存。';}}
    catch(error){root.innerHTML=`<p>数量分配未读取：${esc(error.message)}</p><button type="button" class="secondary" data-tj-action="allocations">重试读取</button>`;}
  }
  function renderAllocations() {
    const a=state.allocation||{},r=state.allocationRecord;
    $('tradeJournalAllocation').innerHTML=`<p class="tj-muted">${r?'数量分配版本 '+r.revision+' · '+r.recorded_at:'尚无明确数量分配；不从模式标签分配收益。'}</p>${table(['真实成交','方向','真实股数','已明确分配','未分配'],array(a.fill_quantities).map(f=>[esc(f.fill_id),esc(f.side==='buy'?'买入':'卖出'),num(f.quantity,0),num(f.allocated_quantity,0),num(f.unallocated_quantity,0)]),'真实股数与未分配数分别核对')}${table(['模式 / 版本','卖出归属股数 / 已知','模式净额','含费成本 / 费用','证据与缺项'],array(a.mode_results).map(m=>[`${esc(m.mode_label)}<small>${esc(text(m.mode_ref))}</small>`,`${num(m.quantity,0)} / ${num(m.known_quantity,0)}`,`${num(m.net_pnl)}<small>已知分量 ${num(m.known_pnl_component)}</small>`,`${num(m.buy_cost)} / ${num(m.fees)}`,esc(m.attribution_known?'股数与成本归属可核对':array(m.reasons).join('；')||'归属未知')]),'模式数量切片；未知总额不以已知分量代替')}<details><summary>FIFO切片、未归属与核对</summary><dl class="tj-facts">${field('卖出切片',a.sell_attributions)}${field('尚未归属的真实FIFO切片',a.unassigned_fifo_slices)}${field('账户与切片核对',a.reconciliation)}${field('范围',a.scope)}${field('资格边界',a.qualification)}</dl>${missing(a.warnings)}</details><div class="tj-actions"><button type="button" data-tj-action="edit-allocations">编辑完整分配表</button><button type="button" class="secondary" data-tj-action="allocation-history">查看分配历史</button></div>`;
  }
  function allocationModeOptions(row) {
    const choices=state.definitions.filter(d=>d.category==='mode'),old=row.definition_snapshot||row.mode_ref;
    if(old?.id&&old.rule_version&&!choices.some(d=>d.id===old.id&&d.rule_version===old.rule_version))choices.unshift({...old,label:'原冻结定义 · '+(old.label||old.id)});
    return '<option value="">请选择确切模式版本</option>'+choices.map(d=>`<option value="${esc(JSON.stringify({id:d.id,rule_version:d.rule_version}))}"${row.mode_ref?.id===d.id&&row.mode_ref?.rule_version===d.rule_version?' selected':''}>${esc(d.label||d.id)} · ${esc(d.rule_version)}</option>`).join('');
  }
  function editAllocations({reuse=false}={}) {
    if(state.allocationEpisode!==state.selected)return;
    if(!reuse){state.allocationRows=JSON.parse(JSON.stringify(state.allocationRecord?.assignments||[]));state.allocationReason='';state.allocationBaseRevision=state.allocationRecord?.revision||0;state.allocationBaseLabels=JSON.parse(JSON.stringify(state.allocationRecord?.labels||[]));}
    const fills=array(state.detail?.fills),matches=array(state.detail?.episode?.matches),rows=array(state.allocationRows),stale=state.allocationBaseRevision!==(state.allocationRecord?.revision||0);
    $('tradeJournalAllocationEditor').innerHTML=`<form id="tradeJournalAllocationForm" class="tj-editor" data-episode="${esc(state.selected)}" data-base-revision="${esc(state.allocationBaseRevision||0)}" data-stale="${stale}"><h4>完整逐笔分配表 · 基线版本 ${esc(state.allocationBaseRevision||'尚无分配')}</h4>${table(['真实成交','模式版本','股数','既有FIFO成本批','行'],rows.map((r,index)=>{const f=fills.find(f=>f.fill_id===r.fill_id),lots=matches.filter(m=>m.sell_fill_id===r.fill_id);return [
      `<select aria-label="第${index+1}行真实成交" data-allocation-row="${index}" data-allocation-field="fill_id"><option value="">请选择成交</option>${fills.map(f=>`<option value="${esc(f.fill_id)}"${f.fill_id===r.fill_id?' selected':''}>${esc(f.executed_at)} · ${f.side==='buy'?'买':'卖'} ${num(f.quantity,0)} · ${esc(f.fill_id)}</option>`).join('')}</select>`,
      `<select aria-label="第${index+1}行模式版本" data-allocation-row="${index}" data-allocation-field="mode_ref">${allocationModeOptions(r)}</select>`,
      `<input aria-label="第${index+1}行股数" type="number" min="1" step="1" value="${esc(r.quantity??'')}" data-allocation-row="${index}" data-allocation-field="quantity" />`,
      f?.side==='buy'?'<span>本笔真实买入成本批</span>':`<select aria-label="第${index+1}行FIFO成本批" data-allocation-row="${index}" data-allocation-field="buy_lot_id"><option value="">未明确（多批次须选择）</option>${lots.map(l=>`<option value="${esc(l.buy_lot_id)}"${l.buy_lot_id===r.buy_lot_id?' selected':''}>${esc(l.buy_lot_id)} · 实际匹配 ${num(l.quantity,0)} 股</option>`).join('')}</select>`,
      `<button type="button" class="secondary" data-tj-action="allocation-remove" data-index="${index}">移除本行</button>`];}),'同一份真实股数不得重复归属；买入与卖出均须明确分配')}<button type="button" class="secondary" data-tj-action="allocation-add">增加分配行</button>${area('reason','本次完整表保存的理由（必填）',state.allocationReason)}<p class="tj-muted">只保存人工股数归属。未分配保留未知，模式收益仍由真实FIFO成本、费用及买入归属校验决定。</p><p id="tradeJournalAllocationStatus" class="tj-form-status" role="status" aria-live="polite">${stale?'基线版本已变化，请重新打开完整表核对后保存。':''}</p><button type="submit"${stale?' disabled':''}>保存完整分配新版本</button></form>`;
  }
  async function saveAllocations(form) {
    const v=formValues(form),note=$('tradeJournalAllocationStatus'),episode=state.detail?.episode;
    if(!episode||form.dataset.episode!==state.selected||state.allocationEpisode!==state.selected){note.textContent='库存轮已切换，请重新读取分配表。';return;}
    const baseRevision=Number(form.dataset.baseRevision||0);
    if(form.dataset.stale==='true'||baseRevision!==(state.allocationRecord?.revision||0)){note.textContent='编辑基线已过期，请重新打开完整分配表核对新版本；不会借用新版本号覆盖。';return;}
    if(!v.reason){note.textContent='请填写本次保存理由；完整表可以清空，但不删除旧版本。';return;}
    let assignments;try {const sums={};assignments=array(state.allocationRows).map(r=>{const f=array(state.detail.fills).find(f=>f.fill_id===r.fill_id),quantity=Number(r.quantity);if(!f||!r.mode_ref?.id||!r.mode_ref?.rule_version||!Number.isInteger(quantity)||quantity<=0)throw new Error('每行须选择真实成交、确切模式版本和正整数股数');sums[f.fill_id]=(sums[f.fill_id]||0)+quantity;if(sums[f.fill_id]>f.quantity)throw new Error('分配股数超过本笔真实成交股数');const lots=array(episode.matches).filter(m=>m.sell_fill_id===f.fill_id);if(f.side==='sell'&&lots.length>1&&!r.buy_lot_id)throw new Error('本笔卖出涉及多个FIFO成本批，请明确选择批次');return {fill_id:f.fill_id,mode_ref:{id:r.mode_ref.id,rule_version:r.mode_ref.rule_version},quantity,...(f.side==='buy'?{buy_lot_id:f.fill_id}:r.buy_lot_id?{buy_lot_id:r.buy_lot_id}:{})};});}
    catch(error){note.textContent='尚不能保存：'+error.message;return;}
    const body={reason:v.reason,assignments,labels:state.allocationBaseLabels||[],evidence_class:'retrospective_recall'};if(baseRevision)body.expected_revision=baseRevision;
    const button=form.querySelector('[type=submit]');button.disabled=true;note.textContent='正在追加股数分配版本…';
    try{const p=await api('/episodes/'+encodeURIComponent(state.selected)+'/allocations',{method:'POST',body:JSON.stringify(body)});if(episode.episode_id!==state.selected)return;state.allocationRecord=p.record;state.allocation=p.allocation;renderAllocations();$('tradeJournalAllocationEditor').innerHTML='';status('已保存数量分配版本 '+p.revision+'；未改变真实成交或库存。','success');}
    catch(error){note.textContent='分配未保存：'+error.message;note.dataset.state='error';}finally{button.disabled=false;}
  }
  async function allocationHistory() {
    artifact('数量分配历史','<p role="status">正在读取不可变分配版本…</p>');
    try{const p=await api('/episodes/'+encodeURIComponent(state.selected)+'/allocations/history');artifact('数量分配历史',array(p.items).map(r=>`<details><summary>版本 ${esc(r.revision)} · ${esc(r.recorded_at)}</summary><dl class="tj-facts">${field('理由',r.reason)}${field('完整逐笔分配',r.assignments)}${field('FIFO校验',r.fifo_match_digest)}</dl></details>`).join('')||'<p>尚无分配历史。</p>');}catch(error){artifact('分配历史未读取',`<p>${esc(error.message)}</p>`);}
  }
  const dimensionNames={mode:'模式数量归属',month:'月份',environment:'记录环境',role:'记录角色',execution_difference:'执行差异',security_type:'证券类型'};
  const dimensionValue = value => ({unknown:'未知',mixed:'记录冲突 / 混合',ipo:'IPO',etf:'ETF',secondary_equity:'普通二级证券','mode.unallocated':'尚未数值归属'}[value]||value);
  function statisticsFacts(s={},parts=false) {
    return `<dl class="tj-facts">${field(parts?'数量切片数':'已清仓且成本已知的库存轮数',s.n)}${field('涉及真实库存轮数',s.episode_n)}${field('净额',num(s.net_pnl))}${field('净额中位数',num(s.median_pnl))}${field(parts?'切片净额为正占比':'完整轮净额为正占比',ratio(s.win_rate))}${field('正 / 负 / 零净额个数',[s.win_n,s.loss_n,s.flat_n].map(v=>num(v,0)).join(' / '))}${field('含费买入成本',num(s.buy_cost))}${field('净额 / 含费买入成本',ratio(s.return_on_cost))}${field('收益率分母',s.return_denominator)}${field('获利 / 亏损额比',s.profit_factor==null?(s.profit_factor_status==='no_loss_denominator'?'无亏损分母，不报比值':'—'):num(s.profit_factor))}${field('最差净额',num(s.tail?.worst_pnl))}${field('最好净额',num(s.tail?.best_pnl))}${field('最差净额 / 含费成本',ratio(s.tail?.worst_return))}</dl>${rawFields(s)}`;
  }
  function analyticsMembers(rows,excluded=false) {
    return table(['真实库存轮 / 证券','清仓 / 起始日','净额 / 含费成本','统计单位 / 归属','核对'],array(rows).map(r=>[`${esc(r.name||r.ticker)}<small>${esc(r.ticker)} · ${esc(r.episode_id)}</small>`,`${esc(r.end_date||'未清仓')}<small>${esc(r.start_date||'起始未知')}</small>`,`${num(r.net_pnl)} / ${num(r.buy_cost)}`,`${esc(r.unit||'单位待核')}<small>${esc(excluded?array(r.exclusion_reasons).join('；'):r.attribution_note||r.mode_label||dimensionValue(r.mode))}</small>`,`<button type="button" class="secondary" data-tj-action="episode" data-id="${esc(r.episode_id)}">核对库存轮</button>`]),excluded?'排除原始轮及具体原因；不填造净额':'原始成员；数量切片不能冒充独立股票交易次数');
  }
  async function loadAnalytics(form=$('tradeJournalAnalyticsForm')) {
    const note=$('tradeJournalAnalyticsStatus'),root=$('tradeJournalAnalytics'),f=readFilters(),select=form?.elements?.namedItem('group_by');
    const dimensions=select?.selectedOptions?Array.from(select.selectedOptions).map(o=>o.value):String(formValues(form||{}).group_by||'security_type,month').split(',').filter(Boolean);
    if(f.start_date&&f.end_date&&f.start_date>f.end_date){note.textContent='请先调整上方日期范围，开始日不能晚于结束日。';return;}
    const params=new URLSearchParams({query:f.query,start_date:f.start_date,end_date:f.end_date,group_by:dimensions.join(',')});note.textContent='正在核对完整轮、排除项与各组分母…';root.setAttribute('aria-busy','true');
    try{const p=await api('/analytics?'+params);state.analytics=p;state.analyticsParams=params.toString();const d=p.denominator||{},parts=array(p.group_by).includes('mode');
      root.innerHTML=`<p class="tj-muted">${esc(d.source_scope||'返回范围待核')}。${esc(d.group_unit||'统计单位待核')}</p><div class="tj-summary-line"><strong>完整分母</strong><span>筛选库存轮 ${num(d.selected_episode_n,0)}</span><span>已清仓且成本已知 ${num(d.closed_known_n,0)}</span><span>排除 ${num(d.excluded_n,0)}</span><span>未闭 ${num(d.open_n,0)}</span><span>闭合但未知 ${num(d.closed_unknown_n,0)}</span></div>${section('已清仓且成本已知的库存轮整体',statisticsFacts(p.overall))}${section('分组描述统计',table(['分组范围',parts?'切片数 / 涉及轮数':'完整轮数','排除项','净额','净额为正占比','净额 / 成本','展开'],array(p.groups).map(g=>[esc(Object.entries(g.dimensions||{}).map(([key,value])=>(dimensionNames[key]||key)+'：'+dimensionValue(value)).join(' · ')),`${num(g.statistics?.n,0)} / ${num(g.statistics?.episode_n,0)}`,num(g.excluded_n,0),num(g.statistics?.net_pnl),ratio(g.statistics?.win_rate),ratio(g.statistics?.return_on_cost),`<button type="button" class="secondary" data-tj-action="analytics-group" data-id="${esc(g.group_id)}">原始成员与分母</button>`]),parts?'模式维度含数量切片；这里的占比不是股票交易胜率':'同一完整轮在整体只计一次'))}<details><summary>尾部与剔除最大单敏感性</summary>${Object.entries(p.overall?.largest_trade_sensitivity||{}).map(([key,value])=>`<h4>${key==='drop_largest_gain'?'剔除最大获利成员':'剔除绝对净额最大成员'}</h4>${value?`<dl class="tj-facts">${field('剔除成员',value.removed_member)}${field('剔除净额',num(value.removed_pnl))}</dl>${statisticsFacts(value.remaining)}`:'<p>没有可比较成员。</p>'}`).join('')}</details><details><summary>全部真实成员（${array(p.members).length}）</summary>${analyticsMembers(p.members)}</details><details><summary>全部排除项（${array(p.excluded).length}）</summary>${analyticsMembers(p.excluded,true)}</details><details><summary>统计边界与净值缺口</summary><dl class="tj-facts">${field('分母',p.denominator)}${field('历史权益与回撤',p.historical_portfolio)}${field('限制',p.limitations)}</dl></details>`;
      note.textContent=`已读取 ${num(d.closed_known_n,0)} 个已清仓且成本已知的库存轮；排除与未知已单列，未由标签推定模式收益。`;
    }catch(error){root.innerHTML='';note.textContent='统计未读取：'+error.message;note.dataset.state='error';}finally{root.setAttribute('aria-busy','false');}
  }
  async function showAnalyticsGroup(id) {
    artifact('统计分组原始成员','<p role="status">正在读取同一筛选范围的分组…</p>');
    try{const g=await api('/analytics/groups/'+encodeURIComponent(id)+'?'+(state.analyticsParams||'')),parts=array(g.group_by).includes('mode');artifact('统计分组原始成员',`<p>${esc(Object.entries(g.dimensions||{}).map(([k,v])=>(dimensionNames[k]||k)+'：'+dimensionValue(v)).join(' · '))}</p><p class="tj-muted">${parts?'统计单位包含模式数量切片；切片占比不叫股票交易胜率。':'统计单位为已清仓且成本已知的库存轮。'}</p>${statisticsFacts(g.statistics,parts)}${analyticsMembers(g.members)}<details><summary>本组排除项</summary>${analyticsMembers(g.excluded,true)}</details><dl class="tj-facts">${field('限制',g.limitations)}</dl>`);}catch(error){artifact('分组未读取',`<p>${esc(error.message)}</p>`);}
  }
  function portfolioProfileId() {return $('tradeJournalPortfolioForm')?.elements?.namedItem('profile_id')?.value?.trim()||'default';}
  function renderPortfolio(p) {
    $('tradeJournalPortfolio').innerHTML=`<p class="tj-muted"><strong>当前读到的账户存档 · 服务器读取时点 ${esc(p.captured_at||'未返回')}</strong><br>${esc(p.scope||'范围待核')}</p><dl class="tj-facts">${field('有效持仓记录数',p.holding_n)}${field('可用现金（配置口径）',num(p.available_cash))}${field('已保存总资产配置',num(p.configured_total_assets))}${field('当前已核验净资产',num(p.current_verified_net_asset_value))}${field('持仓市值',num(p.market_value))}${field('已知市值分量（非完整总额）',num(p.known_market_value_component))}${field('账户配置原更新时间',p.profile_updated_at)}</dl>${table(['已保存证券','股数 / 可卖','成本 / 存档报价','报价原时点 / 状态','市值 / 配置资产占比','已有止损 / 风险缺项'],array(p.holdings).map(h=>[esc(h.name||h.ticker),`${num(h.quantity,0)} / ${num(h.available_quantity,0)}`,`${num(h.cost_basis)} / ${num(h.current_price)}`,`${esc(h.quote_at||'报价时点未知')}<small>${esc(h.quote_status==='owner_snapshot_unassessed_freshness'?'存档报价，时效未核验':h.quote_status||'状态待核')}</small>`,`${num(h.market_value)} / ${ratio(h.weight_on_configured_assets)}`,`${num(h.effective_stop)}<small>${esc(array(h.risk_gaps).join('；'))}</small>`]),'已保存的账户记录；读取时点不代表行情时点，报价时效尚须核验')}<details><summary>已有配置、单票风险与行业敞口</summary><dl class="tj-facts">${field('只读账户配置',p.profile)}${field('已有止损风险汇总',p.configured_risk_aggregation)}${field('逐票已有风险计算',array(p.holdings).map(h=>({ticker:h.ticker,dynamic_risk:h.dynamic_risk})))}${field('行业敞口',p.sector_exposure)}${field('所有者',p.owners)}</dl></details>${p.scenario?section('用户提议逐腿投影',`${table(['顺序 / 证券','提议方向 / 股数','用户假定价格 / 费用','数量前 → 后','假设现金变动','可成交缺项'],array(p.scenario.legs).map((l,index)=>[`${index+1} · ${esc(l.ticker)}`,`${esc(l.side==='buy'?'买入':'卖出')} / ${num(l.quantity,0)}`,`${num(l.user_price)} / ${num(l.fees)}`,`${num(l.before_quantity,0)} → ${num(l.after_quantity,0)}`,num(l.cash_delta),esc(array(l.flags).join('；')||'以原返回边界为准')]),'按用户顺序与价格的假设投影；executed=false')}<dl class="tj-facts">${field('全情景现金变动',num(p.scenario.cash_delta))}${field('已知现金变动分量',num(p.scenario.known_cash_delta_component))}${field('投影后可用现金',num(p.scenario.after_available_cash))}${field('执行状态',p.scenario.executed===false?'未执行的用户提议':'状态待核')}${field('边界',p.scenario.note)}${field('既有数量算法预览',p.sizing_preview)}</dl>`):''}<details><summary>历史权益与当前缺项</summary><dl class="tj-facts">${field('历史权益',p.historical)}${field('限制',p.limitations)}</dl>${missing(p.gaps)}</details>`;
  }
  async function loadPortfolio() {
    const note=$('tradeJournalPortfolioStatus');note.textContent='正在读取已保存账户持仓与风险配置…';
    try{const p=await api('/portfolio-context?'+new URLSearchParams({profile_id:portfolioProfileId()}));state.portfolioContext=p;renderPortfolio(p);if(!state.scenarioLegs){state.scenarioLegs=[];renderScenarioLegs();}note.textContent='已读到账户存档；读取时点不代表行情时点，不能回填为历史交易日账户状态。';}
    catch(error){note.textContent='当前事实未读取：'+error.message;note.dataset.state='error';}
  }
  function renderScenarioLegs() {
    $('tradeJournalScenarioLegs').innerHTML=table(['用户提议顺序','证券代码','方向','股数','用户假定价格','费用（未知留空）','行'],array(state.scenarioLegs).map((l,index)=>[String(index+1),`<input aria-label="第${index+1}腿代码" value="${esc(l.ticker||'')}" data-scenario-row="${index}" data-scenario-field="ticker" />`,`<select aria-label="第${index+1}腿方向" data-scenario-row="${index}" data-scenario-field="side"><option value="buy"${l.side==='buy'?' selected':''}>提议买入</option><option value="sell"${l.side==='sell'?' selected':''}>提议卖出</option></select>`,`<input aria-label="第${index+1}腿股数" type="number" min="1" step="1" value="${esc(l.quantity??'')}" data-scenario-row="${index}" data-scenario-field="quantity" />`,`<input aria-label="第${index+1}腿价格" type="number" min="0" step="any" value="${esc(l.price??'')}" data-scenario-row="${index}" data-scenario-field="price" />`,`<input aria-label="第${index+1}腿费用" type="number" min="0" step="any" value="${esc(l.fees??'')}" data-scenario-row="${index}" data-scenario-field="fees" />`,`<button type="button" class="secondary" data-tj-action="scenario-remove" data-index="${index}">移除本腿</button>`]),'顺序是用户提议的成交假设；不创建订单');
  }
  async function previewPortfolio(form) {
    const v=formValues(form),note=$('tradeJournalScenarioStatus');let legs;
    try{legs=array(state.scenarioLegs).map(l=>{const quantity=Number(l.quantity),price=Number(l.price),fees=l.fees==null||l.fees===''?null:Number(l.fees);if(!l.ticker||!Number.isInteger(quantity)||quantity<=0||!Number.isFinite(price)||price<=0||fees!=null&&(!Number.isFinite(fees)||fees<0))throw new Error('每腿须有证券、正整数股数和假定价格，费用未知可留空');return {ticker:l.ticker,side:l.side,quantity,price,fees};});}catch(error){note.textContent=error.message;return;}
    const scenario={action:v.action||'',reason:v.reason||'',user_limits:structuredValue(v.user_limits||''),legs},profile_id=portfolioProfileId();note.textContent='正在只读投影，不扣减任何持仓或现金…';
    try{const p=await api('/portfolio-context/preview',{method:'POST',body:JSON.stringify({profile_id,scenario})});state.portfolioScenario=scenario;state.portfolioScenarioProfile=profile_id;state.portfolioContext=p;renderPortfolio(p);note.textContent='已读取用户情景投影；没有执行或修改生产设置。';}catch(error){note.textContent='情景未完成：'+error.message;note.dataset.state='error';}
  }
  function planPortfolioRef(ref,form=$('tradeJournalPlanForm')) {
    if(!form||$('tradeJournalPlanForm')!==form)return false;
    form.dataset.portfolioSnapshotRef=JSON.stringify(ref||null);
    $('tradeJournalPlanPortfolioStatus').textContent=ref?`已附当前事实快照 ${ref.snapshot_id} · 服务器时点 ${ref.captured_at}；以确切hash保留。`:'尚未关联当前账户事实快照。';
    return true;
  }
  async function capturePortfolio({forPlan=false}={}) {
    const note=forPlan?$('tradeJournalPlanPortfolioStatus'):$('tradeJournalScenarioStatus'),profile_id=portfolioProfileId(),targetForm=forPlan?$('tradeJournalPlanForm'):null,targetPlan=targetForm?.dataset.plan,targetRevision=targetForm?.dataset.revision;
    if(forPlan&&!targetForm)return;
    if(state.portfolioCapturing){note.textContent='当前事实快照正在保存，请等待本次返回后再操作。';return;}
    if(!forPlan&&state.portfolioScenario&&state.portfolioScenarioProfile!==profile_id){note.textContent='配置标识已变化，请先重新预览该配置下的情景。';return;}
    const button=forPlan?targetForm.querySelector('[data-tj-action="attach-portfolio"]'):$('tradeJournalScenarioForm')?.querySelector('[data-tj-action="portfolio-capture"]'),previousLabel=button?.textContent;
    note.textContent='正在按服务器当前时点保存事实快照…';state.portfolioCapturing=true;if(button){button.disabled=true;button.setAttribute?.('aria-busy','true');button.textContent='正在归档…';}
    try{const p=await api('/portfolio-snapshots',{method:'POST',body:JSON.stringify({profile_id,scenario:forPlan?null:state.portfolioScenario||null,reason:forPlan?'用户将当前账户事实附到待保存预案':'用户保存当前事实及最近已预览情景'})});if(forPlan){if($('tradeJournalPlanForm')!==targetForm||targetForm.dataset.plan!==targetPlan||targetForm.dataset.revision!==targetRevision||portfolioProfileId()!==profile_id){status(`事实快照 ${p.snapshot_ref.snapshot_id} 已归档；预案表单或账户配置已切换，未自动附到其他预案。`);return;}planPortfolioRef(p.snapshot_ref,targetForm);}else note.textContent=`已保存当前事实快照 ${p.snapshot_ref.snapshot_id} · ${p.snapshot_ref.captured_at}。未执行任何提议。`;}
    catch(error){note.textContent='事实快照未保存：'+error.message;note.dataset.state='error';}finally{state.portfolioCapturing=false;if(button){button.disabled=false;button.setAttribute?.('aria-busy','false');if(previousLabel!=null)button.textContent=previousLabel;}}
  }
  async function portfolioHistory() {
    const root=$('tradeJournalPortfolioHistory');root.innerHTML='<p role="status">正在读取服务器事实快照目录…</p>';
    try{const p=await api('/portfolio-snapshots?limit=100&offset=0');root.innerHTML=table(['快照','实际服务器时点','内容校验','查看'],array(p.items).map(r=>[esc(r.snapshot_id),esc(r.captured_at),esc(r.content_digest||r.content_hash),`<button type="button" class="secondary" data-tj-action="portfolio-snapshot" data-id="${esc(r.snapshot_id)}">读取归档事实</button>`]),'实际当前事实的历史归档，不能改称指定过去日期的账户状态');}catch(error){root.innerHTML=`<p>事实快照目录未读取：${esc(error.message)}</p>`;}
  }
  async function showPortfolioSnapshot(id) {
    artifact('账户事实归档','<p role="status">正在恢复确切事实快照…</p>');
    try{const p=await api('/portfolio-snapshots/'+encodeURIComponent(id)+'?include_content=true'),r=p.record||p;artifact('当前事实的确切归档',`<dl class="tj-facts">${field('快照',r.snapshot_id)}${field('服务器保存时点',r.captured_at)}${field('原文事实与提议',r.content)}${field('内容校验',r.content_digest||r.content_hash)}${field('时间边界',r.temporal_note)}</dl>`);}catch(error){artifact('事实快照未恢复',`<p>${esc(error.message)}</p>`);}
  }
  const methodStatus = value => ({draft:'草稿',trial:'试用',active:'使用中',retired:'已退役'}[value]||value||'未知');
  const methodStatusOptions = selected => ['draft','trial','active','retired'].map(value=>`<option value="${value}"${value===(selected||'draft')?' selected':''}>${methodStatus(value)}</option>`).join('');
  async function loadMethods() {
    const note=$('tradeJournalMethodStatus'),form=$('tradeJournalMethodFilters'),values=form?formValues(form):{};
    const params=new URLSearchParams({query:values.query||'',limit:'100',offset:'0'});if(values.status)params.set('status',values.status);
    note.textContent='正在读取当前模式与假设目录…';
    const result=await Promise.allSettled([api('/mode-cards?'+params),api('/hypotheses?'+params)]);
    for(const [index,kind] of [[0,'mode'],[1,'hypothesis']]){
      const root=$(kind==='mode'?'tradeJournalModeCards':'tradeJournalHypotheses');
      if(result[index].status==='rejected'){root.innerHTML=`<p class="tj-empty">${kind==='mode'?'模式卡':'研究假设'}未读取：${esc(result[index].reason.message)}</p>`;continue;}
      const payload=result[index].value,rows=array(payload.items);if(kind==='mode')state.modeCards=rows;
      root.innerHTML=`<h3>${kind==='mode'?'自建模式卡':'研究假设'} · ${esc(payload.total??rows.length)}</h3>${rows.map(record=>{const id=record[kind==='mode'?'mode_id':'hypothesis_id'],revisions=array(record.revisions).length?record.revisions:Array.from({length:record.revision||1},(_,i)=>i+1);return `<article class="tj-method"><header><strong>${esc(record.name||record.question||id)}</strong><span>${esc(methodStatus(record.status))} · v${esc(record.revision)}</span></header><p class="tj-muted">${esc(record.state_note||'使用 / 实验状态不证明收益优势。')}</p><dl class="tj-facts">${field('记录标识',shortId(id))}${field('服务器记录时点',record.captured_at)}${kind==='hypothesis'?field('协议登记性质',registrationLabel(record.preregistration)):''}</dl>${missing(record.missing_fields)}<div class="tj-actions">${revisions.map(revision=>`<button type="button" class="secondary" data-tj-action="view-method" data-kind="${kind}" data-id="${esc(id)}" data-revision="${esc(revision)}">查看版本 ${esc(revision)}</button>`).join('')}<button type="button" class="secondary" data-tj-action="edit-method" data-kind="${kind}" data-id="${esc(id)}" data-revision="${esc(record.revision)}">追加修订 / 退役</button></div></article>`;}).join('')||`<p class="tj-empty">当前筛选范围尚无${kind==='mode'?'自建模式卡':'研究假设'}。可先保存草稿，保留未知项。</p>`}`;
      if(payload.total>rows.length)root.innerHTML+=`<p class="tj-muted">本次显示 ${rows.length} / ${payload.total} 条，请用名称或使用状态缩小范围。</p>`;
    }
    note.textContent=result.every(r=>r.status==='fulfilled')?'已读取当前目录；历史查看使用确切版本，旧版本不会删除。':'目录部分未读取，可重新读取。';
  }
  async function methodRecord(kind,id,revision) {return planRecord(await api((kind==='mode'?'/mode-cards/':'/hypotheses/')+encodeURIComponent(id)+'?revision='+encodeURIComponent(revision)));}
  const registrationLabel = value => ({incomplete:'研究协议尚有缺项',prospective:'前瞻登记；仍须核对数据可用时点',historical_reconstruction:'历史区间重建，属于事后登记',retrospective:'事后登记'}[value]||'登记性质见原始记录');
  const windowText = value => value&&typeof value==='object'?[value.start||'开始日待补',value.end||'结束日待补'].join(' → '):narrative(value);
  function methodSummary(kind,record) {
    const d=record.content||{},mode=kind==='mode';
    const common=field(mode?'模式名称':'研究问题',mode?(d.name||record.name):(d.question||record.question))+field(mode?'使用状态':'实验状态',methodStatus(d.status||record.status))+field('版本',record.revision)+field('服务器保存时点',record.captured_at);
    if(mode)return `<dl class="tj-facts">${common}${field('模式用途',narrative(d.purpose))}${field('允许环境',narrative(d.allowed_environments))}${field('禁止或等待环境',narrative(d.blocked_environments))}${field('入场触发',narrative(d.entry_triggers))}${field('正常预期',narrative(d.expectation))}${field('验证方式与窗口',narrative(d.verification))}${field('失效与退出条件',narrative(d.invalidation))}${field('持有周期',narrative(d.holding_horizon))}${riskFields(d.risk_constraints)}${field('独立理由',narrative(d.rationale))}${field('支持证据摘要',narrative(d.supporting_evidence))}${field('反证与已知失败',narrative(d.counter_evidence))}${field('机器策略绑定',d.strategy_id?'已绑定确切策略版本 · '+(d.strategy_version||'版本待核'):'未绑定机器策略')}${field('收益优势核验',record.advantage_verified===true?'原记录标为已核验；核验范围见原始字段':'未验证；使用状态不证明收益优势')}</dl>${missing(record.missing_fields)}${rawFields(record)}`;
    const attempts=array(d.parameter_attempts||record.parameter_attempts),results=array(d.validation_results||record.validation_results);
    return `<dl class="tj-facts">${common}${field('协议登记性质',registrationLabel(record.preregistration))}${field('本协议登记时点',record.protocol_registered_at)}${field('开发样本区间',windowText(d.development_window))}${field('验证样本区间',windowText(d.validation_window))}${field('研究范围与完整分母',narrative(d.universe))}${field('对照组',narrative(d.comparison_group))}${field('排除与缺数政策',narrative(d.exclusion_policy))}${field('目标指标及统计口径',narrative(d.target_metrics))}${field('执行约束',narrative(d.execution_constraints))}${field('研究理由',narrative(d.rationale))}${field('精确规则引用',`入场 ${array(d.entry_rule_refs).length} 条 / 退出 ${array(d.exit_rule_refs).length} 条；版本见原始字段`)}${field('绑定模式版本',d.mode_ref?'已绑定 · 版本 '+(d.mode_ref.revision||'待核'):'未绑定自建模式')}${field('已保留尝试 / 结果',`${attempts.length} 次参数尝试 / ${results.length} 项结果；只追加保留`)}</dl>${results.length?`<details><summary>逐项结果摘要（${results.length}）</summary>${results.map((r,index)=>{const v=r.content||r;return `<div class="tj-result-summary"><h4>结果 ${index+1} · ${esc(({actual_trades:'真实成交报告值',simulation:'模拟结果报告值',observation:'观察反馈',inconclusive:'无结论 / 缺证据'})[v.outcome_type]||'结果类型待核')}</h4><dl class="tj-facts">${field('说明',narrative(v.notes))}${field('数据截止日',v.data_cutoff)}${field('报告样本数',v.sample_count)}${field('更正关系',v.supersedes_result_id?'本项更正一个既有结果；原结果保留':'未声明更正关系')}</dl></div>`;}).join('')}</details>`:''}${missing(record.missing_fields)}${rawFields(record)}`;
  }
  async function viewMethod(kind,id,revision) {
    artifact(kind==='mode'?'模式卡版本':'研究假设版本','<p role="status">正在读取确切版本…</p>');
    try {const record=await methodRecord(kind,id,revision);artifact((kind==='mode'?'模式卡':'研究假设')+' · v'+record.revision,`<p class="tj-muted">${esc(record.state_note||'记录状态不证明优势，历史协议和尝试保留。')}</p>${methodSummary(kind,record)}`);}catch(error){artifact('确切版本未读取',`<p>${esc(error.message)}</p>`);}
  }
  async function editModeCard(id='',revision='') {
    if(!state.definitions.length)await loadDefinitions();let record={};
    if(id){try{record=await methodRecord('mode',id,revision);}catch(error){$('tradeJournalMethodStatus').textContent='模式版本未读取：'+error.message;return;}}
    const d=record.content||{},strategyDefs=state.definitions.filter(d=>d.category==='strategy'),binding=d.strategy_id?JSON.stringify({strategy_id:d.strategy_id,strategy_version:d.strategy_version}):'';
    const strategies=strategyDefs.map(d=>({value:JSON.stringify({strategy_id:d.parameters?.strategy_id||d.id.slice(9),strategy_version:d.rule_version}),label:d.label+' · '+d.rule_version}));if(binding&&!strategies.some(s=>s.value===binding))strategies.unshift({value:binding,label:'原版本绑定 · '+d.strategy_id+' @'+d.strategy_version});
    const fields=[['purpose','模式要解决什么问题'],['allowed_environments','允许的环境与适用条件'],['blocked_environments','禁止或等待的环境'],['entry_triggers','入场触发（与交易模式分开）'],['expectation','正常预期与比较基准'],['verification','验证窗口与具体方式'],['invalidation','失效与退出条件'],['holding_horizon','持有周期'],['risk_constraints','数量、仓位与亏损预算'],['sources','来源与原文引用'],['rationale','独立理由'],['supporting_evidence','支持证据'],['counter_evidence','反证与已知失败情形']];
    $('tradeJournalMethodEditor').innerHTML=`<form id="tradeJournalModeCardForm" class="tj-editor" data-id="${esc(id)}" data-revision="${esc(record.revision||'')}"><h4>${id?'追加模式卡版本':'新建模式卡'}</h4><div class="tj-form-grid">${input('name','模式名称（必填）',d.name||record.name||'')}<label>使用状态<select name="status">${methodStatusOptions(d.status||record.status)}</select></label>${fields.map(([key,title])=>area(key,title,d[key])).join('')}<label class="tj-span">可选代码策略绑定（明确版本）<select name="strategy_binding"><option value="">未绑定机器策略</option>${strategies.map(s=>`<option value="${esc(s.value)}"${s.value===binding?' selected':''}>${esc(s.label)}</option>`).join('')}</select></label>${id?area('revision_reason','修订或退役原因（必填）'):''}</div><p class="tj-muted">字段可保留未知；active仅为使用状态。规则与数据尚不完整时，不声称可回测，不启用策略或下单。</p><p id="tradeJournalMethodSaveStatus" class="tj-form-status" role="status"></p><div class="tj-actions"><button type="submit">保存模式卡版本</button><button type="button" class="secondary" data-tj-action="cancel-method">收起</button></div></form>`;
  }
  const ruleRefsValue = (form,name) => {const element=form.elements?.namedItem?.(name);if(element?.selectedOptions)return Array.from(element.selectedOptions).map(o=>JSON.parse(o.value));const raw=formValues(form)[name];if(!raw)return [];const parsed=JSON.parse(raw);return Array.isArray(parsed)?parsed:[parsed];};
  function ruleRefOptions(selected=[]) {
    const choices=state.definitions.filter(d=>['trigger','action','strategy','mode','technical_feature'].includes(d.category)).map(d=>({id:d.id,rule_version:d.rule_version,label:d.label}));
    for(const ref of array(selected))if(!choices.some(d=>d.id===ref.id&&d.rule_version===ref.rule_version))choices.unshift({...ref,label:'已存历史引用 · '+ref.id});
    return choices.map(d=>`<option value="${esc(JSON.stringify({id:d.id,rule_version:d.rule_version}))}"${array(selected).some(s=>s.id===d.id&&s.rule_version===d.rule_version)?' selected':''}>${esc(d.label)} · ${esc(d.rule_version)}</option>`).join('');
  }
  async function editHypothesis(id='',revision='') {
    if(!state.definitions.length)await loadDefinitions();let record={},sources=[];
    try{if(id)record=await methodRecord('hypothesis',id,revision);sources=array((await api('/sources?limit=1000&offset=0')).items);state.modeCards=array((await api('/mode-cards?limit=1000&offset=0')).items);}catch(error){$('tradeJournalMethodStatus').textContent='部分定义或来源未读取：'+error.message;if(id&&!record.hypothesis_id)return;}
    const d=record.content||{},modeChoices=array(state.modeCards).flatMap(c=>(array(c.revisions).length?c.revisions:[c.revision]).map(revision=>({mode_id:c.mode_id,revision,label:c.name}))),fields=[['universe','研究范围 / 完整分母'],['comparison_group','对照组'],['exclusion_policy','排除条件与缺数政策'],['target_metrics','目标指标及统计口径'],['execution_constraints','交易成本、可成交性与可卖库存约束'],['sources','来源'],['rationale','研究理由']];
    if(d.mode_ref?.mode_id&&d.mode_ref?.revision&&!modeChoices.some(c=>c.mode_id===d.mode_ref.mode_id&&c.revision===d.mode_ref.revision))modeChoices.unshift({...d.mode_ref,label:'原冻结模式引用 · '+d.mode_ref.mode_id});
    $('tradeJournalMethodEditor').innerHTML=`<form id="tradeJournalHypothesisForm" class="tj-editor" data-id="${esc(id)}" data-revision="${esc(record.revision||'')}"><h4>${id?'追加研究协议 / 实验记录':'登记研究假设'}</h4><div class="tj-form-grid">${area('question','研究问题（必填）',d.question||record.question)}<label>实验状态<select name="status">${methodStatusOptions(d.status||record.status)}</select></label>${input('development_start','开发样本开始',d.development_window?.start||'','date')}${input('development_end','开发样本结束',d.development_window?.end||'','date')}${input('validation_start','验证样本开始',d.validation_window?.start||'','date')}${input('validation_end','验证样本结束',d.validation_window?.end||'','date')}${fields.map(([key,title])=>area(key,title,d[key])).join('')}<label>入场规则引用（可多选）<select name="entry_rule_refs" multiple size="4">${ruleRefOptions(d.entry_rule_refs)}</select></label><label>退出规则引用（可多选）<select name="exit_rule_refs" multiple size="4">${ruleRefOptions(d.exit_rule_refs)}</select></label><label class="tj-span">可选模式卡确切版本<select name="mode_ref"><option value="">未绑定自建模式</option>${modeChoices.map(c=>`<option value="${esc(JSON.stringify({mode_id:c.mode_id,revision:c.revision}))}"${c.mode_id===d.mode_ref?.mode_id&&c.revision===d.mode_ref?.revision?' selected':''}>${esc(c.label)} · v${esc(c.revision)}</option>`).join('')}</select></label>${id?area('revision_reason','本次协议或实验追加原因（必填）'):''}</div><details><summary>本次追加阈值尝试（旧项只读保留）</summary><div class="tj-form-grid">${area('attempt_parameters','本次参数（可填JSON）')}${area('attempt_reason','尝试理由与失败情况')}${area('attempt_data_refs','尝试数据引用（可填JSON数组）')}</div></details><details><summary>本次追加验证结果（没有新结果可留空）</summary><div class="tj-form-grid"><label>报告结果类型<select name="outcome_type" id="tradeJournalOutcomeType"><option value="">本次不追加结果</option><option value="actual_trades">真实成交报告值</option><option value="simulation">模拟结果报告值</option><option value="observation">观察反馈</option><option value="inconclusive">无结论 / 缺证据</option></select></label><label>已有数据来源<select name="result_source_id"><option value="">未引用来源</option>${sources.map(s=>`<option value="${esc(s.source_id)}">${esc(s.filename||s.source_id)}</option>`).join('')}</select></label>${input('result_snapshot_id','或引用已存快照标识')}${input('data_cutoff','结果数据截止日','','date')}${input('sample_count','样本分母（有数值时须为正整数）','','number')}${input('winning_count','获利样本数（仅真实 / 模拟报告）','','number')}${area('result_metrics','其他报告指标（可填JSON；不在此生成回测）')}${input('supersedes_result_id','更正哪一旧结果标识（可选）')}${area('result_notes','结果与缺证据说明')}</div></details><p class="tj-muted">开发与验证区间严格分开；历史区间登记不冒充事前预登记。尝试与结果只追加，旧项不能编辑删除。观察反馈不填写交易胜率或已实现盈亏；有数值必须引用实际归档来源并明确分母。</p><p id="tradeJournalMethodSaveStatus" class="tj-form-status" role="status"></p><div class="tj-actions"><button type="submit">保存研究版本与追加项</button><button type="button" class="secondary" data-tj-action="cancel-method">收起</button></div></form>`;
  }
  async function saveMethod(form,kind) {
    const v=formValues(form),note=$('tradeJournalMethodSaveStatus'),id=form.dataset.id,body={};
    if(!(kind==='mode'?v.name:v.question)){note.textContent=kind==='mode'?'请填写模式名称，其他未知项可留空。':'请填写要验证的研究问题。';return;}
    if(id&&!v.revision_reason){note.textContent='修订、追加或退役须填写原因，旧版本保留。';return;}
    try {
      const fields=kind==='mode'?['name','status','purpose','allowed_environments','blocked_environments','entry_triggers','expectation','verification','invalidation','holding_horizon','risk_constraints','sources','rationale','supporting_evidence','counter_evidence']:['question','status','universe','comparison_group','exclusion_policy','target_metrics','execution_constraints','sources','rationale'];
      for(const key of fields)if(key in v)body[key]=['name','status','question'].includes(key)?v[key]:structuredValue(v[key]);
      if(kind==='mode'){Object.assign(body,v.strategy_binding?JSON.parse(v.strategy_binding):{strategy_id:null,strategy_version:null});}
      else {
        for(const kind of ['development','validation']){const start=v[kind+'_start'],end=v[kind+'_end'];if(Boolean(start)!==Boolean(end))throw new Error('样本区间须同时填写开始与结束日期');body[kind+'_window']=start?{start,end}:null;}
        body.entry_rule_refs=ruleRefsValue(form,'entry_rule_refs');body.exit_rule_refs=ruleRefsValue(form,'exit_rule_refs');body.mode_ref=v.mode_ref?JSON.parse(v.mode_ref):null;
        if(v.attempt_parameters||v.attempt_reason)body.parameter_attempts=[{parameters:structuredValue(v.attempt_parameters||''),reason:v.attempt_reason||'',data_refs:v.attempt_data_refs?JSON.parse(v.attempt_data_refs):[]}];
        if(v.outcome_type){const result={outcome_type:v.outcome_type,notes:v.result_notes||'',data_refs:[],metrics:v.result_metrics?JSON.parse(v.result_metrics):{}};if(v.result_source_id)result.data_refs.push({source_id:v.result_source_id});if(v.result_snapshot_id)result.data_refs.push({snapshot_id:v.result_snapshot_id});if(v.data_cutoff)result.data_cutoff=v.data_cutoff;if(v.sample_count)result.sample_count=Number(v.sample_count);if(v.winning_count!=null&&v.winning_count!==''){result.winning_count=Number(v.winning_count);if(!['actual_trades','simulation'].includes(v.outcome_type))throw new Error('观察 / 无结论不能填交易获利数');if(!Number.isInteger(result.sample_count)||result.sample_count<=0)throw new Error('交易胜率需要正整数样本分母');result.metrics.win_rate=result.winning_count/result.sample_count;}if(v.supersedes_result_id)result.supersedes_result_id=v.supersedes_result_id;body.validation_results=[result];}
      }
      if(id){body[kind==='mode'?'mode_id':'hypothesis_id']=id;body.expected_revision=Number(form.dataset.revision);body.revision_reason=v.revision_reason;}
    }catch(error){note.textContent='尚不能保存：'+error.message;return;}
    const button=form.querySelector('[type=submit]');button.disabled=true;note.textContent='正在追加不可变版本…';
    try {const result=await api(kind==='mode'?'/mode-cards':'/hypotheses',{method:'POST',body:JSON.stringify(body)});$('tradeJournalMethodEditor').innerHTML='';if(kind==='mode')await loadDefinitions();await loadMethods();await viewMethod(kind,result[kind==='mode'?'mode_id':'hypothesis_id'],result.revision);$('tradeJournalMethodStatus').textContent='已保存新版本。状态及报告值不证明策略优势，未启用策略或下单。';}
    catch(error){note.textContent='版本未保存：'+error.message;note.dataset.state='error';}
    finally {button.disabled=false;}
  }
  function renderDefinitions(query='') {
    const root=$('tradeJournalDefinitionBody');
    if(state.definitionsError){root.innerHTML=`<p>${esc(state.definitionsError)}</p><button type="button" data-tj-action="retry-definitions">重新读取口径</button>`;return;}
    const q=query.trim().toLowerCase(),rows=state.definitions.filter(d=>!q||JSON.stringify(d).toLowerCase().includes(q));
    root.innerHTML=`<p class="tj-muted">统一版本 ${esc(state.definitionVersion)} · ${rows.length} 条</p>${state.definitionBoundary?`<p>${esc(text(state.definitionBoundary))}</p>`:''}${rows.map(d=>`<details${rows.length===1?' open':''}><summary>${esc(d.label)} <small>${esc(d.id)}</small></summary><p>${esc(d.definition)}</p><dl class="tj-facts">${field('状态 / 规则版本',[d.status,d.rule_version].filter(Boolean).join(' / '))}${field('适用范围',d.scope)}${field('公式',d.formula)}${field('分子',d.numerator)}${field('分母',d.denominator)}${field('单位 / 窗口',[d.unit,d.window].filter(Boolean).join(' / '))}${field('时间口径',d.time_basis)}${field('缺数处理',d.missing_policy)}${field('来源',d.sources)}${field('限制',d.limitations)}${Object.entries(d).filter(([key])=>!['id','label','definition','status','rule_version','scope','formula','numerator','denominator','unit','window','time_basis','missing_policy','sources','limitations'].includes(key)).map(([key,value])=>field({category:'类别',axis:'所属轴',aliases:'别名',parameters:'实际规则参数'}[key]||key,value)).join('')}</dl></details>`).join('')||'<p>当前搜索未匹配术语，请清除关键词。</p>'}`;
  }
  function openDefinitions(id='') {
    const dialog=$('tradeJournalDefinitions');$('tradeJournalDefinitionSearch').value=id;renderDefinitions(id);dialog.showModal();$('tradeJournalDefinitionSearch').focus();
  }
  async function openForTicker(ticker='') {
    const form=$('tradeJournalFilters');form.reset();form.elements.namedItem('query').value=ticker;
    state.offset=0;state.loaded=false;window.switchTab?.('trade-journal');
  }
  function mountReviewWorkflows() {
    if(!$('tradeJournalTasksSection'))$('tradeJournalSummary')?.insertAdjacentHTML?.('afterend',`<details id="tradeJournalTasksSection" class="tj-section tj-archive-section"><summary>缺项待办与处置记录</summary><p class="tj-muted">按根因核对缺项。记录可确认的未知原因或暂忽略，只保存处置，不填造成本、来源或事前条件，不改变资格；相关事实变化时会重新打开。</p><form id="tradeJournalTaskFilters" class="tj-toolbar"><label>待办状态<select name="status"><option value="open">待处理</option><option value="">全部</option><option value="confirmed_unknown">已确认未知</option><option value="ignored">暂忽略</option><option value="resolved">事实已补齐</option></select></label><button type="submit">读取缺项待办</button></form><p id="tradeJournalTaskStatus" class="tj-form-status" role="status" aria-live="polite">尚未读取；股票与日期沿用上方筛选。</p><div id="tradeJournalTasks"></div><div id="tradeJournalTaskPagination" class="tj-pagination"></div><div id="tradeJournalTaskEditor"></div></details>`);
    if(!$('tradeJournalImportSection'))$('tradeJournalSourceSection')?.querySelector?.('summary')?.insertAdjacentHTML?.('afterend',`<details id="tradeJournalImportSection" class="tj-import"><summary>从东财原始文件导入</summary><p class="tj-muted">选择实际 XLSX 或 CSV，逐表确认表头与字段含义，再核对规范预览和重复冲突。最后明确确认才写入归档账本，不改生产账户或持仓。</p><form id="tradeJournalImportFileForm" class="tj-form-grid"><label class="tj-span">原始文件<input id="tradeJournalImportFile" name="file" type="file" accept=".xlsx,.csv" /></label><label>归档账户代号<input name="account_alias" value="primary" autocomplete="off" /><small>只填代号，不填姓名、券商账号或凭证。</small></label><label>CSV 编码<select name="encoding"><option value="">自动识别</option><option value="utf-8-sig">UTF-8</option><option value="gb18030">GB18030 / GBK</option></select></label><div class="tj-actions tj-span"><button type="submit">检查文件与工作表</button><button type="button" class="secondary" data-tj-action="import-reset">清除本次导入</button></div></form><p id="tradeJournalImportStatus" class="tj-form-status" role="status" aria-live="polite">尚未选择文件。原件上限 8 MiB；公式不执行。</p><div id="tradeJournalImportMapping"></div><div id="tradeJournalImportPreview"></div><div id="tradeJournalImportReceipt"></div></details>`);
  }
  const taskRoundDates = task => (task.start_date||'起始日期未提供')+' → '+(task.end_date||'结束日期未提供');
  const taskMissingLabel = key => missingLabel(key)===key?'未映射字段':missingLabel(key);
  const taskStateLabel = value => ({open:'待处理',confirmed_unknown:'已确认未知',ignored:'暂忽略',resolved:'事实已补齐'}[value]||'状态待核');
  const taskRootLabel = value => ({accounting:'成交与成本',decision_context:'决策依据',plan_conditions:'预期与退出条件',evidence:'证据与时点',plan_link:'预案关联',review:'复盘核验',process_review:'过程复盘'}[value]||value||'根因待核');
  async function loadTasks({offset=0}={}) {
    const note=$('tradeJournalTaskStatus'),root=$('tradeJournalTasks'),filters=readFilters(),taskStatus=$('tradeJournalTaskFilters')?.elements?.namedItem('status')?.value||'';
    if(filters.start_date&&filters.end_date&&filters.start_date>filters.end_date){note.textContent='请先调整上方日期范围，开始日不能晚于结束日。';return;}
    const params=new URLSearchParams({query:filters.query,start_date:filters.start_date,end_date:filters.end_date,status:taskStatus,limit:'30',offset:String(offset)}),seq=(state.taskSeq||0)+1;state.taskSeq=seq;
    note.textContent='正在按当前事实核对缺项根因与既有处置…';note.dataset.state='loading';root.setAttribute('aria-busy','true');
    try {
      const p=await api('/tasks?'+params);if(seq!==state.taskSeq)return;state.tasks=array(p.items);state.taskOffset=offset;state.taskTotal=p.total;
      const groups=new Map();for(const task of state.tasks){const key=task.root_cause||task.category||'unknown';if(!groups.has(key))groups.set(key,[]);groups.get(key).push(task);}
      root.innerHTML=`<div class="tj-summary-line">${Object.entries(p.state_counts||{}).map(([key,count])=>`<span>${esc(taskStateLabel(key))} <b>${num(count,0)}</b></span>`).join('')}</div>${Array.from(groups,([key,rows])=>`<details class="tj-task-group" open><summary>${esc(taskRootLabel(key))} · 本页 ${rows.length} 项</summary>${table(['证券 / 根因事项','状态与当前缺项','影响与下一步','处理'],rows.map(t=>[`${esc(t.name||t.ticker||'证券待核')}<small>${esc(t.ticker||'')}</small><div>${esc(taskRoundDates(t))}</div><small title="${esc(t.episode_id||'')}">库存轮：${esc(shortId(t.episode_id)||'标识未提供')}</small><strong>${esc(t.title||'缺项待核')}</strong>`,`${esc(taskStateLabel(t.state))}${t.auto_reopened?'<small>相关事实变化，已重新打开</small>':''}<p>${esc(array(t.missing_fields).map(taskMissingLabel).join('、')||'缺项由当前事实核对')}</p>${rawFields({episode_id:t.episode_id,start_date:t.start_date,end_date:t.end_date,missing_fields:t.missing_fields})}`,`${esc(narrative(t.impact))}<small>${esc(narrative(t.resolution_hint))}</small>${t.auto_reopened?`<p>${esc(narrative(t.reopen_reason))}</p>`:''}`,`<button type="button" class="secondary" data-tj-action="task" data-id="${esc(t.task_id)}">处理与历史</button><button type="button" class="tj-term" data-tj-action="episode" data-id="${esc(t.episode_id)}">核对库存轮</button>`]),'本页按根因合并；用户处置不改变四维资格')}</details>`).join('')||'<p class="tj-empty">此筛选范围没有待办。可切换全部状态查看已确认、暂忽略和事实已补齐的记录。</p>'}`;
      $('tradeJournalTaskPagination').innerHTML=`<button type="button" class="secondary" data-tj-action="tasks-previous" ${offset===0?'disabled':''}>上一页</button><span>${p.total?`${offset+1}–${offset+state.tasks.length} / ${p.total}`:'0 / 0'}</span><button type="button" class="secondary" data-tj-action="tasks-next" ${offset+state.tasks.length>=p.total?'disabled':''}>下一页</button>`;
      const form=$('tradeJournalTaskForm'),fresh=state.tasks.find(t=>t.task_id===form?.dataset.task);
      if(form&&fresh&&(Number(form.dataset.revision)!==Number(fresh.revision)||form.dataset.fingerprint!==fresh.fact_fingerprint)){form.dataset.stale='true';form.querySelector('[type=submit]').disabled=true;$('tradeJournalTaskSaveStatus').textContent='相关事实或处置版本已变化；请重新打开该待办核对，不会用新版本覆盖旧表单。';}
      note.textContent=`已读取 ${num(p.total,0)} 项；按本页根因合并。已确认未知与暂忽略仍保留真实缺项。`;note.dataset.state='ready';
    }catch(error){if(seq!==state.taskSeq)return;root.innerHTML='<p class="tj-empty">待办未读取。请重新读取，现有资格与缺项记录不受影响。</p>';note.textContent='待办未读取：'+error.message;note.dataset.state='error';}finally{if(seq===state.taskSeq)root.setAttribute('aria-busy','false');}
  }
  async function editTask(id) {
    const root=$('tradeJournalTaskEditor'),seq=(state.taskEditSeq||0)+1;state.taskEditSeq=seq;root.innerHTML='<p role="status">正在读取当前根因、事实指纹与处置历史…</p>';
    try {
      const p=await api('/tasks/'+encodeURIComponent(id)),t=p.task;if(seq!==state.taskEditSeq)return;if(!t)throw new Error('服务未返回待办事实');
      root.innerHTML=`<form id="tradeJournalTaskForm" class="tj-editor" data-task="${esc(t.task_id)}" data-revision="${esc(p.latest_revision??t.revision??0)}" data-fingerprint="${esc(t.fact_fingerprint||'')}" data-stale="false"><h4>${esc(t.name||t.ticker||'')} · ${esc(t.title||'根因事项')}</h4><dl class="tj-facts">${field('当前状态',taskStateLabel(t.state))}${field('库存轮区间',taskRoundDates(t))}${field('库存轮标识',shortId(t.episode_id)||'标识未提供')}${field('当前事实说明',narrative(t.reasons))}${field('影响',narrative(t.impact))}${field('处置建议',narrative(t.resolution_hint))}</dl>${t.auto_reopened?`<p class="tj-muted">相关事实已变化并重开：${esc(narrative(t.reopen_reason))}</p>`:''}${t.state==='resolved'?'<p class="tj-muted">此项已由真实字段补齐而关闭；这里不提供手动标记补齐。</p>':`<div class="tj-form-grid"><label>本次处置<select name="action" id="tradeJournalTaskAction"><option value="confirm_unknown">确认未知</option><option value="ignore">说明理由，暂忽略</option><option value="reopen">重新打开</option></select></label><label id="tradeJournalTaskUnknownLabel">确认未知的情况<select name="unknown_basis"><option value="">请选择</option><option value="原始资料无法恢复">原始资料无法恢复</option><option value="现有证据不足以核实">现有证据不足以核实</option><option value="当时未设">当时未设（仅适用于已确认的主观预案）</option><option value="无法回忆">无法回忆</option></select></label>${area('reason','本次处置理由（必填）')}</div><p class="tj-muted">这项操作只保存未知原因与处置理由，不补造期初成本、原始来源、事前预期或风险预算，也不提高资格。“当时未设”只适用于用户明确确认的主观预案。</p><p id="tradeJournalTaskSaveStatus" class="tj-form-status" role="status" aria-live="polite"></p><button type="submit" ${!t.fact_fingerprint?'disabled':''}>保存本次处置</button>`}</form><details><summary>处置历史（${array(p.history).length}）</summary>${array(p.history).map(h=>`<dl class="tj-facts">${field('版本',h.revision)}${field('动作',({confirm_unknown:'确认未知',ignore:'暂忽略',reopen:'重新打开'})[h.action]||h.action)}${field('理由',narrative(h.reason))}${field('记录时点',h.recorded_at)}</dl>`).join('')||'<p class="tj-muted">尚无人工处置历史。</p>'}</details>${rawFields(p)}`;
    }catch(error){if(seq===state.taskEditSeq)root.innerHTML=`<p class="tj-empty">待办详情未读取：${esc(error.message)}</p><button type="button" data-tj-action="task" data-id="${esc(id)}">重新读取待办</button>`;}
  }
  async function saveTaskResolution(form) {
    const v=formValues(form),note=$('tradeJournalTaskSaveStatus'),action=v.action;
    if(form.dataset.stale==='true'){note.textContent='编辑基线已过期，请重新打开待办核对当前事实。';return;}
    if(!['confirm_unknown','ignore','reopen'].includes(action)||!v.reason||(action==='confirm_unknown'&&!v.unknown_basis)){note.textContent='请选择处置及未知情况，并填写本次理由；不会自动填补原字段。';return;}
    if(!form.dataset.fingerprint){note.textContent='当前事实指纹缺失，请重新读取待办。';return;}
    const body={expected_revision:Number(form.dataset.revision||0),expected_fingerprint:form.dataset.fingerprint,action,reason:action==='confirm_unknown'?`确认情况：${v.unknown_basis}。理由：${v.reason}`:v.reason},button=form.querySelector('[type=submit]');button.disabled=true;form.setAttribute('aria-busy','true');note.textContent='正在保存本次处置，原缺项保持不变…';note.dataset.state='loading';
    try {const p=await api('/tasks/'+encodeURIComponent(form.dataset.task)+'/resolutions',{method:'POST',body:JSON.stringify(body)});if($('tradeJournalTaskForm')===form){await editTask(form.dataset.task);await loadTasks({offset:state.taskOffset||0});}status(`已保存处置版本 ${p.latest_revision??p.record?.revision??'待核'}；这不改变资格或填补原始事实。`,'success');}
    catch(error){note.textContent='处置未保存：'+error.message+'。请保留理由，重新核对当前事实后重试。';note.dataset.state='error';}finally{form.setAttribute('aria-busy','false');if(form.dataset.stale!=='true')button.disabled=false;}
  }
  function invalidateImport(message='文件或映射已变化，请重新生成规范预览。',{keepIdentityLinks=false}={}) {
    state.importGeneration=(state.importGeneration||0)+1;state.importParsed=null;state.importConfirmed=false;
    if(!keepIdentityLinks)state.importIdentityLinks=[];
    const preview=$('tradeJournalImportPreview');if(preview)preview.innerHTML=`<p class="tj-muted">${esc(message)}</p>`;
  }
  function resetImport() {
    invalidateImport('已清除本次导入；已归档数据不受影响。');state.importFile=null;state.importInspection=null;state.importMapping=null;
    $('tradeJournalImportFileForm')?.reset?.();$('tradeJournalImportMapping').innerHTML='';$('tradeJournalImportReceipt').innerHTML='';$('tradeJournalImportStatus').textContent='请选择实际原始文件；不会删除已有来源。';
  }
  async function inspectImport(form) {
    const note=$('tradeJournalImportStatus'),file=form.elements?.namedItem('file')?.files?.[0],v=formValues(form),button=form.querySelector('[type=submit]');
    if(!file){note.textContent='尚未选择文件。请选择实际东财 XLSX 或 CSV 原件。';return;}
    if(!/\.(xlsx|csv)$/i.test(file.name)||!file.size||file.size>8*1024*1024){note.textContent='文件须为非空 XLSX / CSV，且不超过 8 MiB。请换原始文件后重试。';note.dataset.state='error';return;}
    invalidateImport('正在检查原件；尚未生成可确认的导入预览。');const generation=state.importGeneration;state.importFile=null;state.importInspection=null;state.importMapping=null;$('tradeJournalImportMapping').innerHTML='';button.disabled=true;form.setAttribute('aria-busy','true');note.textContent='正在读取文件并检查工作表、表头与原件限制…';note.dataset.state='loading';
    try {
      const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
      const body={filename:file.name,content_base64:btoa(binary),...(v.encoding?{encoding:v.encoding}:{})},p=await api('/import-files/inspect',{method:'POST',body:JSON.stringify(body)});
      if(generation!==state.importGeneration)return;state.importFile=body;state.importInspection=p;state.importMapping=JSON.parse(JSON.stringify(p.suggested_mapping||{tables:[],ignored_sheets:[],ignored_rows:[],corroboration_pairs:[]}));state.importAccount=v.account_alias||'primary';renderImportMapping();
      note.textContent=`已识别 ${file.name} · ${array(p.sheets).length} 张表。请核对映射、表头与时间含义。`;note.dataset.state='ready';$('tradeJournalImportPreview').innerHTML='<p class="tj-muted">原件已识别。请核对映射后生成规范预览；尚未导入。</p>';
    }catch(error){if(generation===state.importGeneration){note.textContent='文件未检查：'+error.message+'。保留原件，修正后重新检查。';note.dataset.state='error';$('tradeJournalImportPreview').innerHTML='<p class="tj-muted">原件检查未完成，请修正后重新检查；尚未导入。</p>';}}finally{button.disabled=false;form.setAttribute('aria-busy','false');}
  }
  function importSheetColumns(sheet,headerRow) {
    return array(sheet.candidate_headers).find(h=>Number(h.row)===Number(headerRow))?.columns||Array.from({length:sheet.column_count||0},(_,i)=>({index:i+1,label:'第 '+(i+1)+' 列'}));
  }
  function importRawRows(sheet) {
    return `<details><summary>逐行查看原表（脱敏）</summary><div class="tj-toolbar" data-import-rows="${esc(sheet.name)}"><label>从原行号开始<input name="row_start" type="number" min="1" value="${esc(sheet.row_start||1)}" /></label><button type="button" data-tj-action="import-read-rows">读取100行原文</button></div>${table(['原行号','原单元格'],array(sheet.row_previews||sheet.sample_rows).map(r=>[esc(r.row),`<span class="tj-cell-values">${esc(array(r.cells).map(v=>v==null?'（空）':String(v)).join(' | '))}</span>`]),'只读原行；账户字段由服务端脱敏，不执行公式')}${rawFields({candidate_headers:sheet.candidate_headers})}</details>`;
  }
  async function loadImportRows(form) {
    const sheet=form.dataset.importRows,row=Number(form.elements?formValues(form).row_start:form.querySelector('[name=row_start]')?.value),note=$('tradeJournalImportStatus'),generation=state.importGeneration,button=form.querySelector('[data-tj-action=import-read-rows]');
    if(!state.importFile||!sheet||!Number.isInteger(row)||row<1){note.textContent='请先检查文件，并填写正整数原行号。';return;}
    button.disabled=true;note.textContent='正在仅读指定原行；现有映射与预览不变…';
    try {const p=await api('/import-files/inspect',{method:'POST',body:JSON.stringify({...state.importFile,sheet,row_start:row,row_limit:100})});if(generation!==state.importGeneration)return;const incoming=array(p.sheets).find(s=>s.name===sheet);if(!incoming)throw new Error('服务未返回这张表');state.importInspection.sheets=state.importInspection.sheets.map(s=>s.name===sheet?{...s,...incoming}:s);renderImportMapping();note.textContent='已读取 '+sheet+' 从原行 '+row+' 开始的脱敏原文；没有改变映射。';}catch(error){if(generation===state.importGeneration){note.textContent='原行未读取：'+error.message+'。请核对行号后重试。';note.dataset.state='error';}}finally{button.disabled=false;}
  }
  function focusIgnoredRow(sheet,row) {
    const form=$('tradeJournalImportIgnoreForm');if(!form)return;form.closest?.('details')?.setAttribute('open','');const sheetField=form.elements?.namedItem('sheet'),rowField=form.elements?.namedItem('row'),reason=form.elements?.namedItem('reason');if(sheetField)sheetField.value=sheet;if(rowField)rowField.value=row;if(reason)reason.focus?.();$('tradeJournalImportStatus').textContent='已定位原行，请填写忽略理由后再加入清单；目前没有忽略或导入这行。';
  }
  function renderImportMapping() {
    const p=state.importInspection,m=state.importMapping;if(!p||!m)return;
    const sheets=array(p.sheets),sheetOptions=selected=>sheets.map(s=>`<option value="${esc(s.name)}"${s.name===selected?' selected':''}>${esc(s.name)}</option>`).join('');
    $('tradeJournalImportMapping').innerHTML=`<form id="tradeJournalImportMappingForm" class="tj-editor"><h4>工作表与列映射</h4><p class="tj-muted">真实零值保留为 0，空费用保留未知；不按现金差额倒算费用。未使用的工作表须说明忽略理由。成交对照表只核对事件表，不重复记账。</p>${sheets.map((s,index)=>{const t=array(m.tables).find(t=>t.sheet===s.name),ignored=array(m.ignored_sheets).find(t=>t.sheet===s.name),columns=importSheetColumns(s,t?.header_row);return `<details class="tj-import-sheet" open><summary>${esc(s.name)} · ${num(s.row_count,0)} 行 / ${num(s.column_count,0)} 列</summary><div class="tj-form-grid"><label>这张表的用途<select data-import-sheet="${esc(s.name)}" data-import-field="role"><option value="">尚未决定</option><option value="events"${t?.role==='events'?' selected':''}>成交与财务事件表</option><option value="corroborating_executions"${t?.role==='corroborating_executions'?' selected':''}>真实成交对照表</option><option value="ignore"${ignored?' selected':''}>忽略此表（必须理由）</option></select></label>${ignored?`<label>忽略此表的理由<input data-import-sheet="${esc(s.name)}" data-import-field="ignore_reason" value="${esc(ignored.reason||'')}" /></label>`:t?`<label>表头所在行<input type="number" min="1" max="${esc(s.row_count||50000)}" value="${esc(t.header_row||'')}" data-import-sheet="${esc(s.name)}" data-import-field="header_row" /></label><label>时间列的含义<select data-import-sheet="${esc(s.name)}" data-import-field="time_role"><option value="">请选择时间含义</option><option value="statement_event_time"${t.time_role==='statement_event_time'?' selected':''}>交割单发生时间</option><option value="execution_time"${t.time_role==='execution_time'?' selected':''}>撮合成交时间</option><option value="date_only"${t.time_role==='date_only'?' selected':''}>仅日期，撮合时点未知</option></select></label><label>现金符号口径<select data-import-sheet="${esc(s.name)}" data-import-field="cash_sign"><option value="signed"${t.cash_sign==='signed'?' selected':''}>原列已带收付正负号</option><option value="directional_magnitude"${t.cash_sign==='directional_magnitude'?' selected':''}>原列为绝对值，按明确业务方向</option></select></label><label>仅在原表没有日期列时的显式日期<input type="date" value="${esc(t.date_default||'')}" data-import-sheet="${esc(s.name)}" data-import-field="date_default" /></label>`:''}</div>${t?table(['规范字段','原表列（可留空）'],array(p.fields).map(f=>[esc(f.label||f.id),`<select aria-label="${esc(s.name+' · '+(f.label||f.id))}" data-import-sheet="${esc(s.name)}" data-import-column="${esc(f.id)}"><option value="">未映射</option>${columns.map(c=>`<option value="${esc(c.index)}"${Number(t.columns?.[f.id])===Number(c.index)?' selected':''}>${esc(c.index+' · '+(c.label||'空表头'))}</option>`).join('')}</select>`]),'显式列映射；同一表内的账号姓名没有规范映射字段'):''}${importRawRows(s)}</details>`;}).join('')}<p class="tj-muted">解析器提示：${esc(array(p.warnings).map(v=>narrative(v)).join('；')||'没有返回额外提示，仍须核对映射。')}</p><div class="tj-actions"><button type="submit">生成规范预览并检查重复冲突</button></div></form><details><summary>说明并忽略指定原行</summary><form id="tradeJournalImportIgnoreForm" class="tj-form-grid"><label>工作表<select name="sheet">${sheetOptions('')}</select></label>${input('row','原行号','','number')}${area('reason','忽略理由（必填；原行仍在原件中保留）')}<button type="submit">加入本次忽略清单</button></form>${table(['工作表 / 原行','理由','操作'],array(m.ignored_rows).map((r,i)=>[esc(r.sheet+' / '+r.row),esc(r.reason),`<button type="button" class="secondary" data-tj-action="import-remove-ignore" data-index="${i}">移除此项忽略</button>`]),'只忽略显式选中的原行')}</details><details><summary>明确交割行与真实成交行的对应</summary><form id="tradeJournalImportPairForm" class="tj-form-grid"><label>事件表<select name="primary_sheet">${sheetOptions('')}</select></label>${input('primary_row','事件表原行号','','number')}<label>成交对照表<select name="execution_sheet">${sheetOptions('')}</select></label>${input('execution_row','成交表原行号','','number')}<button type="submit">加入明确行对</button></form>${table(['事件表 / 原行','成交表 / 原行','操作'],array(m.corroboration_pairs).map((r,i)=>[esc(r.primary?.sheet+' / '+r.primary?.row),esc(r.execution?.sheet+' / '+r.execution?.row),`<button type="button" class="secondary" data-tj-action="import-remove-pair" data-index="${i}">移除此行对</button>`]),'歧义由显式原行对应解决，不按相近金额猜测')}</details>${rawFields({file_id:p.file_id,sha256:p.sha256,parser_version:p.parser_version,limits:p.limits,mapping:m})}`;
  }
  function updateImportMapping(el) {
    const m=state.importMapping,sheet=el.dataset.importSheet;if(!m||!sheet)return;
    invalidateImport();m.tables=array(m.tables);m.ignored_sheets=array(m.ignored_sheets);
    const field=el.dataset.importField;let t=m.tables.find(t=>t.sheet===sheet);
    if(field==='role') {
      const old=t;m.tables=m.tables.filter(t=>t.sheet!==sheet);m.ignored_sheets=m.ignored_sheets.filter(s=>s.sheet!==sheet);
      if(el.value==='ignore')m.ignored_sheets.push({sheet,reason:''});else if(el.value)m.tables.push({...old,sheet,role:el.value,header_row:old?.header_row||array(state.importInspection?.sheets).find(s=>s.name===sheet)?.candidate_headers?.[0]?.row||1,columns:old?.columns||{},time_role:old?.time_role||'',cash_sign:old?.cash_sign||'signed'});renderImportMapping();
    }else if(field==='ignore_reason'){const row=m.ignored_sheets.find(s=>s.sheet===sheet);if(row)row.reason=el.value;}
    else if(t&&el.dataset.importColumn){t.columns=t.columns||{};if(el.value)t.columns[el.dataset.importColumn]=Number(el.value);else delete t.columns[el.dataset.importColumn];}
    else if(t&&field){t[field]=field==='header_row'?Number(el.value):el.value;if(field==='header_row'){t.columns={};renderImportMapping();$('tradeJournalImportStatus').textContent='表头行已变化，请重新核对每个规范列映射。';}}
  }
  async function parseImport(form) {
    const note=$('tradeJournalImportStatus'),m=state.importMapping,p=state.importInspection;
    if(!state.importFile||!p||!m){note.textContent='请先选择并检查实际原件。';return;}
    if(array(p.sheets).some(s=>!array(m.tables).some(t=>t.sheet===s.name)&&!array(m.ignored_sheets).some(t=>t.sheet===s.name&&t.reason?.trim()))||array(m.ignored_sheets).some(s=>!s.reason?.trim())||array(m.tables).some(t=>!t.time_role||!t.header_row)){note.textContent='请逐表选择用途、表头及时间含义；忽略的工作表必须说明理由。';return;}
    invalidateImport('正在规范解析并检查重复冲突…',{keepIdentityLinks:true});const generation=state.importGeneration,button=form.querySelector('[type=submit]'),file={...state.importFile},mapping=JSON.parse(JSON.stringify(m));state.importAccount=formValues($('tradeJournalImportFileForm')).account_alias||'primary';
    button.disabled=true;form.setAttribute('aria-busy','true');note.textContent='正在按已核对映射解析原行，再与既有归档检查重复和冲突…';note.dataset.state='loading';
    try {const result=await api('/import-files/parse',{method:'POST',body:JSON.stringify({...file,mapping,account_alias:state.importAccount,identity_links:array(state.importIdentityLinks)})});if(generation!==state.importGeneration)return;state.importParsed=result;state.importPreviewGeneration=generation;state.importRowOffset=0;renderImportPreview();note.textContent=result.can_preview&&result.preview?.can_import?'规范预览已就绪。请核对行数、费用未知、重复和冲突后明确确认导入。':'尚不能确认导入。请处理未解析行、映射或归档冲突后重新生成预览。';note.dataset.state=result.can_preview&&result.preview?.can_import?'ready':'error';}
    catch(error){if(generation===state.importGeneration){note.textContent='规范预览未生成：'+error.message+'。请核对映射后重试。';note.dataset.state='error';}}finally{button.disabled=false;form.setAttribute('aria-busy','false');}
  }
  const importCountLabels={sources:'来源数',fills:'规范成交数',financial_events:'财务事件数',new_sources:'新增来源',new_fills:'新增成交',duplicate_fills:'已有成交',new_financial_events:'新增财务事件',duplicate_financial_events:'已有财务事件',input_fill_rows:'输入成交行',payload_duplicate_fill_rows:'文件内重复成交行',conflicts:'冲突数'};
  const importCountSummary = counts => `<div class="tj-summary-line">${Object.entries(counts||{}).map(([key,value])=>`<span>${esc(importCountLabels[key]||'其他计数')} <b>${num(value,0)}</b></span>`).join('')||'<span>服务未返回计数；请核对原始回执。</span>'}</div>`;
  function renderImportPreview() {
    const p=state.importParsed;if(!p)return;const preview=p.preview||{},rows=array(p.payload?.fills),financial=array(p.payload?.financial_events),offset=state.importRowOffset||0,canImport=p.can_preview&&p.payload&&preview.can_import&&preview.preview_digest&&state.importPreviewGeneration===state.importGeneration;
    $('tradeJournalImportPreview').innerHTML=`<section class="tj-section"><h4>规范预览与确认</h4><p class="tj-muted">${esc(state.importFile?.filename)} · 只预览，尚未写入。账户仅使用归档代号，成交与现金事件分别入账。</p>${importCountSummary(preview.counts)}${array(p.issues).length?section('需处理的原行',table(['原位置','原因与脱敏原文','处理'],array(p.issues).map(i=>[esc([i.sheet,i.row!=null?'原行 '+i.row:''].filter(Boolean).join(' · ')),`${esc(i.message||'未解析，详见原字段')}${i.cells?`<details><summary>查看这行原文</summary><p class="tj-cell-values">${esc(array(i.cells).map((v,n)=>(array(i.columns)[n]||'第'+(n+1)+'列')+'：'+(v??'（空）')).join('；'))}</p></details>`:''}`,i.sheet&&i.row?`<button type="button" class="secondary" data-tj-action="import-ignore-row" data-sheet="${esc(i.sheet)}" data-row="${esc(i.row)}">说明并忽略本行</button>`:'请先核对工作表映射']),'所有未处理行由服务端决定是否阻止预览')):''}${array(preview.conflicts).length?section('归档冲突',table(['类型','必须核对的原因'],array(preview.conflicts).map(c=>[esc(c.kind||'冲突'),esc(c.message||'冲突详情见原字段')]),'冲突不能通过确认按钮覆盖原事实')):''}${array(preview.warnings).length?`<p class="tj-muted">${esc(preview.warnings.join('；'))}</p>`:''}${rows.length?table(['原行 / 证券','日期 / 时间性质','方向 / 股数','价格 / 金额','费用 / 净收付','股票余额'],rows.slice(offset,offset+100).map(f=>[`${esc(f.name||f.ticker||f.code)}<small>${esc(f.ticker||f.code)} · ${esc(f.source_ref?.sheet||'原表')} / ${esc(f.source_ref?.row??'行号待核')}</small>`,`${esc(f.executed_at||[f.date,f.time].filter(Boolean).join(' '))}<small>${esc(fillTimeLabel(f))}</small>`,`${esc(f.side==='buy'?'买入':f.side==='sell'?'卖出':f.side)} / ${num(f.quantity,0)}`,`${num(f.price)} / ${num(f.amount)}`,`${num(f.fees)} / ${num(f.net_cash)}`,num(f.quantity_after??f.reported_quantity_after,0)]),'规范成交；费用空值保持未知，完整行可分页核对'):'<p class="tj-muted">没有返回可预览成交行；可能尚有解析问题，或仅有财务事件。</p>'}${rows.length?`<div class="tj-pagination"><button type="button" class="secondary" data-tj-action="import-rows-previous" ${offset===0?'disabled':''}>前100行</button><span>${offset+1}–${Math.min(rows.length,offset+100)} / ${rows.length} 行</span><button type="button" class="secondary" data-tj-action="import-rows-next" ${offset+100>=rows.length?'disabled':''}>后100行</button></div>`:''}${financial.length?`<details><summary>财务事件（${financial.length}）</summary>${table(['证券 / 日期','事件','数量 / 现金','来源原行'],financial.map(f=>[esc([f.name||f.ticker,f.date].filter(Boolean).join(' · ')),esc(f.event_type||f.kind||f.description||'事件类型待核'),`${num(f.quantity,0)} / ${num(f.net_cash??f.amount)}`,esc(f.source_ref?.row??'行号待核')]),'IPO、现金与公司行为单列，不补造成二级成交')}</details>`:''}${importIdentityChoices(preview)}${rawFields({audit:p.audit,mapping_digest:p.mapping_digest,mapping:state.importMapping,issues:p.issues,preview})}<form id="tradeJournalImportConfirmForm" class="tj-import-confirm"><label class="tj-check-label"><input type="checkbox" name="confirmed" id="tradeJournalImportConfirmed"${state.importConfirmed?' checked':''}${canImport?'':' disabled'} />我已核对这份规范预览，确认导入归档账本</label><p class="tj-muted">${canImport?'确认只对应当前文件、映射和预览摘要；修改后须重新预览。':'尚有解析问题、冲突或缺预览摘要，不能确认导入。'}</p><button type="submit"${canImport?'':' disabled'}>确认导入这份预览</button></form></section>`;
  }
  function importIdentityChoices(preview) {
    const candidates=array(preview.identity_candidates),groups=new Map();candidates.forEach((c,index)=>{const key=JSON.stringify(c.incoming_source_ref);if(!groups.has(key))groups.set(key,[]);groups.get(key).push({candidate:c,index});});
    const choices=candidates.length?`<form id="tradeJournalImportIdentityForm" class="tj-editor" data-candidates="${esc(JSON.stringify(candidates))}" data-preview-digest="${esc(preview.preview_digest||'')}"><h4>逐原行核对既有成交身份</h4><p class="tj-muted">以下只是候选，不自动删除编号前导零或按同价同量合并。请比对两侧完整成交号和原行；每个新来源行最多选择一份既有成交，确认后只重新预览。</p>${Array.from(groups.values(),(rows,group)=>`<fieldset class="tj-fill-choices"><legend>${esc(rows[0].candidate.incoming_source_ref?.sheet||'来源原表')} · 原行 ${esc(rows[0].candidate.incoming_source_ref?.row??'待核')}</legend>${rows.map(({candidate:c,index})=>`<label class="tj-check-label"><input type="radio" name="identity_choice_${group}" value="${index}" />逐原行核对后，确认属于这份既有成交</label><dl class="tj-facts">${field('新原件完整成交编号',c.incoming_broker_execution_id)}${field('既有完整成交编号',c.existing_broker_execution_id)}${field('既有事实标识',shortId(c.existing_fact_id))}${field('候选说明（不代表已确认）',narrative(c.reason))}</dl>${c.incoming||c.existing?importIdentityFacts(c):''}`).join('')}${area('identity_reason_'+group,'本原行确认同一成交的理由（必填；不预填）')}</fieldset>`).join('')}<button type="submit">按明确身份对应重新核对</button>${rawFields(candidates)}</form>`:'';
    return choices+(array(state.importIdentityLinks).length?`<details><summary>本次已明确的身份对应（${state.importIdentityLinks.length}）</summary><p class="tj-muted">这些确认仍须随当前原件与映射通过服务端重新预览，尚未执行导入。</p>${state.importIdentityLinks.map((link,index)=>`<p>${esc(link.incoming_source_ref?.sheet||'原表')} · 原行 ${esc(link.incoming_source_ref?.row)} → ${esc(shortId(link.existing_fact_id))} · ${esc(link.reason)} <button type="button" class="secondary" data-tj-action="import-remove-identity" data-index="${index}">撤回这项身份确认</button></p>`).join('')}${rawFields(state.importIdentityLinks)}</details>`:'');
  }
  function importIdentityFacts(c) {
    const fields=[['ticker','证券'],['date','原字段日期'],['time','原字段时间'],['side','方向'],['quantity','股数'],['price','价格（元）'],['amount','成交金额（元）'],['fees','费用（元）'],['net_cash','净收付（元）'],['reported_quantity_after','股份余额']];
    const value=(record,key)=>['quantity','price','amount','fees','net_cash','reported_quantity_after'].includes(key)?num(record?.[key],['quantity','reported_quantity_after'].includes(key)?0:2):key==='side'?record?.side==='buy'?'买入':record?.side==='sell'?'卖出':'待核':text(record?.[key]);
    return table(['核对字段','新来源事实','既有归档事实'],fields.map(([key,title])=>[esc(title),esc(value(c.incoming,key)),esc(value(c.existing,key))]),'核心字段逐项比对；未知不补零，候选仍须人工明确确认');
  }
  async function saveImportIdentityChoices(form) {
    const p=state.importParsed,note=$('tradeJournalImportStatus');let candidates=[];try{candidates=JSON.parse(form.dataset.candidates||'[]');}catch{}
    if(!p||p.preview?.preview_digest!==form.dataset.previewDigest){note.textContent='候选预览已变化，请重新核对当前文件。';return;}
    const selected=Array.from(form.elements||[]).filter(el=>el.name?.startsWith('identity_choice_')&&el.checked),links=[];
    if(!selected.length){note.textContent='尚未选择同一事实。请逐原行选择候选并填写理由，或保留未确认状态。';return;}
    for(const choice of selected){const c=candidates[Number(choice.value)],reason=form.elements?.namedItem('identity_reason_'+choice.name.split('_').at(-1))?.value?.trim();if(!c||!reason){note.textContent='每个选中原行都须填写明确核对理由；不把候选说明当用户确认。';return;}links.push({incoming_source_ref:c.incoming_source_ref,existing_fact_id:c.existing_fact_id,expected_existing_hash:c.expected_existing_hash,reason,identity_confirmation:true});}
    const combined=new Map(array(state.importIdentityLinks).map(link=>[JSON.stringify(link.incoming_source_ref),link]));for(const link of links)combined.set(JSON.stringify(link.incoming_source_ref),link);state.importIdentityLinks=Array.from(combined.values());invalidateImport('身份对应已明确，正在重新检查原件与既有事实…',{keepIdentityLinks:true});await parseImport($('tradeJournalImportMappingForm'));
  }
  async function commitImport(form) {
    if(state.importCommitting)return;
    const p=state.importParsed,note=$('tradeJournalImportStatus'),checked=form.elements?.namedItem('confirmed')?.checked;
    if(!checked){note.textContent='尚未明确确认。请核对当前规范预览后勾选确认。';return;}
    if(!p?.can_preview||!p.payload||!p.preview?.can_import||!p.preview.preview_digest||state.importPreviewGeneration!==state.importGeneration||state.importAccount!==(formValues($('tradeJournalImportFileForm')).account_alias||'primary')){note.textContent='预览已失效或仍有未处理项，请重新生成规范预览。';return;}
    const generation=state.importGeneration,filename=state.importFile.filename,body={...JSON.parse(JSON.stringify(p.payload)),preview_digest:p.preview.preview_digest},button=form.querySelector('[type=submit]');state.importCommitting=true;button.disabled=true;form.setAttribute('aria-busy','true');note.textContent='正在按已确认摘要导入归档账本…';note.dataset.state='loading';
    try {const receipt=await api('/imports',{method:'POST',body:JSON.stringify(body)});$('tradeJournalImportReceipt').innerHTML=`<section class="tj-section"><h4>导入回执 · ${esc(filename)}</h4><p>${receipt.idempotent?'相同批次此前已导入；本次未重复新增。':'服务端已完成本批次归档。'}</p>${importCountSummary(receipt.counts)}<dl class="tj-facts">${field('导入库存轮数',receipt.episode_count)}${field('警告',narrative(receipt.warnings,'没有返回额外警告'))}</dl>${rawFields(receipt)}<button type="button" class="secondary" data-tj-action="reload">读取导入后的账本</button><button type="button" class="secondary" data-tj-action="sources">核对来源与批次</button></section>`;if(generation===state.importGeneration){state.importParsed=null;state.importConfirmed=false;$('tradeJournalImportPreview').innerHTML='<p class="tj-muted">这份预览已完成导入。需要重新检查时请再次生成预览，不重复使用确认按钮。</p>';note.textContent='已导入 '+filename+'；真实来源与回执可继续核对，未改变生产账户。';note.dataset.state='success';}}
    catch(error){if(generation===state.importGeneration){note.textContent='导入未确认完成：'+error.message+'。请先重新生成预览核对服务端状态；相同批次会幂等处理。';note.dataset.state='error';state.importParsed=null;state.importConfirmed=false;$('tradeJournalImportPreview').innerHTML='<p class="tj-muted">请重新生成预览后再明确确认，避免使用过期摘要。</p>';}}finally{state.importCommitting=false;button.disabled=false;form.setAttribute('aria-busy','false');}
  }
  function appendImportMappingRow(form,kind) {
    const v=formValues(form),m=state.importMapping;if(!m){$('tradeJournalImportStatus').textContent='请先检查原始文件。';return;}
    const positive=value=>Number.isInteger(Number(value))&&Number(value)>0;
    if(kind==='ignore') {
      if(!v.sheet||!positive(v.row)||!v.reason){$('tradeJournalImportStatus').textContent='请明确工作表、正整数原行号和忽略理由。';return;}
      m.ignored_rows=array(m.ignored_rows);if(m.ignored_rows.some(r=>r.sheet===v.sheet&&Number(r.row)===Number(v.row))){$('tradeJournalImportStatus').textContent='该原行已在忽略清单；需更正时先移除原项。';return;}
      m.ignored_rows.push({sheet:v.sheet,row:Number(v.row),reason:v.reason});
    } else {
      if(!v.primary_sheet||!v.execution_sheet||!positive(v.primary_row)||!positive(v.execution_row)){ $('tradeJournalImportStatus').textContent='请明确双方工作表和正整数原行号。';return;}
      m.corroboration_pairs=array(m.corroboration_pairs);m.corroboration_pairs.push({primary:{sheet:v.primary_sheet,row:Number(v.primary_row)},execution:{sheet:v.execution_sheet,row:Number(v.execution_row)}});
    }
    invalidateImport();renderImportMapping();$('tradeJournalImportStatus').textContent='本次原行处理清单已变化，请重新生成规范预览。';
  }
  const executionStateLabel = value => ({unmatched:'尚未关联',confirmed:'已明确关联',stale:'原回写已变化，关联待重核',unlinked:'已解除关联'}[value]||'状态待核');
  async function loadExecutionMatches() {
    const episode=state.selected,note=$('tradeJournalExecutionStatus');note.textContent='正在仅读同日同证券的既有成交回写…';note.dataset.state='loading';
    try {const p=await api('/episodes/'+encodeURIComponent(episode)+'/execution-matches');if(episode!==state.selected)return;state.executionCandidates=p;state.executionEpisode=episode;
      $('tradeJournalExecutionMatches').innerHTML=table(['既有回写时间 / 证券','方向 / 股数 / 价格','费用 / 现金变化','事实关联状态','核对'],array(p.items).map(r=>{const e=r.live_execution||{};return [esc(e.executed_at||'时点未知')+`<small>${esc(e.ticker||'证券待核')}</small>`,`${esc(e.side==='buy'?'买入':e.side==='sell'?'卖出':'方向待核')} / ${num(e.quantity,0)} / ${num(e.price)}`,`${num(e.fees)} / ${num(e.cash_delta)}`,`${esc(executionStateLabel(r.state))}<small>版本 ${esc(r.revision)}</small>`,`<button type="button" class="secondary" data-tj-action="execution-edit" data-id="${esc(r.execution_id)}">选择归档成交核对</button><button type="button" class="tj-term" data-tj-action="execution-history" data-id="${esc(r.execution_id)}">关联历史</button>${r.record?.episode_id===episode&&['confirmed','stale'].includes(r.state)?`<button type="button" class="secondary" data-tj-action="execution-unlink" data-id="${esc(r.execution_id)}">说明理由解除</button>`:''}`];}),'同日同证券仅为候选；必须由用户确认同一事实')+`<p class="tj-muted">${esc(p.scope||'候选范围待核')}</p>`;
      const form=$('tradeJournalExecutionConfirmForm')||$('tradeJournalExecutionUnlinkForm'),row=array(p.items).find(r=>r.execution_id===form?.dataset.execution);
      if(form&&row&&(Number(form.dataset.revision)!==Number(row.revision)||form.dataset.liveDigest&&form.dataset.liveDigest!==row.live_digest)){form.dataset.stale='true';form.querySelector('[type=submit]').disabled=true;note.textContent='既有回写或关联版本已变化；旧核对表已停用，请重新选择并预览。';}else note.textContent=`已读取 ${array(p.items).length} 条候选；未做自动关联。`;note.dataset.state='ready';
    }catch(error){if(episode!==state.selected)return;note.textContent='候选未读取：'+error.message+'。请重试读取后核对。';note.dataset.state='error';}
  }
  function editExecutionMatch(id) {
    const p=state.executionCandidates,r=array(p?.items).find(r=>r.execution_id===id);if(!r||state.executionEpisode!==state.selected){$('tradeJournalExecutionStatus').textContent='候选已切换，请重新读取本轮回写候选。';return;}
    state.executionPreview=null;$('tradeJournalExecutionPreview').innerHTML='';
    const e=r.live_execution||{};$('tradeJournalExecutionEditor').innerHTML=`<form id="tradeJournalExecutionSelectForm" class="tj-editor" data-episode="${esc(state.selected)}" data-execution="${esc(id)}"><h4>明确选择同一成交的全部归档分笔</h4><dl class="tj-facts">${field('既有回写',`${e.ticker||'待核'} · ${e.executed_at||'时间未知'}`)}${field('方向 / 数量 / 价格',`${e.side==='buy'?'买入':e.side==='sell'?'卖出':'待核'} / ${num(e.quantity,0)} / ${num(e.price)}`)}${field('归档账户代号',p.account_alias)}${field('既有回写账户代号',e.account_alias||'原回写未带代号，须人工核对账户')}</dl><fieldset class="tj-fill-choices"><legend>本轮归档成交（不预选）</legend>${array(p.fills).map(f=>`<label><input type="checkbox" name="fill_ids" value="${esc(f.fill_id)}" />${esc(f.executed_at||[f.date,f.time].filter(Boolean).join(' '))} · ${esc(f.side==='buy'?'买入':'卖出')} ${num(f.quantity,0)} 股 @ ${num(f.price)} · ${esc(fillTimeLabel(f))}</label>`).join('')}</fieldset><button type="submit">只读预览事实差异</button>${rawFields(r)}</form>`;
  }
  async function previewExecutionMatch(form) {
    const note=$('tradeJournalExecutionStatus'),ids=Array.from(form.elements||[]).filter(e=>e.name==='fill_ids'&&e.checked).map(e=>e.value);
    if(form.dataset.episode!==state.selected||!ids.length){note.textContent='请选择本轮属于同一成交的完整归档分笔；尚未选择不生成匹配。';return;}
    const episode=state.selected,id=form.dataset.execution,button=form.querySelector('[type=submit]');button.disabled=true;note.textContent='正在仅读两侧事实差异与既有占用…';
    try {const p=await api('/episodes/'+encodeURIComponent(episode)+'/execution-matches/preview',{method:'POST',body:JSON.stringify({execution_id:id,fill_ids:ids})});if(episode!==state.selected||$('tradeJournalExecutionSelectForm')!==form)return;state.executionPreview=p;
      $('tradeJournalExecutionPreview').innerHTML=`<form id="tradeJournalExecutionConfirmForm" class="tj-editor" data-episode="${esc(episode)}" data-execution="${esc(id)}" data-revision="${esc(p.expected_revision)}" data-digest="${esc(p.preview_digest||'')}" data-live-digest="${esc(p.live_digest||'')}" data-fills="${esc(JSON.stringify(p.fill_ids||ids))}" data-time-difference="${p.requires_time_difference_confirmation===true}" data-stale="false"><h4>两侧事实核对结果</h4>${array(p.conflicts).length?missing(p.conflicts):'<p>服务端未返回阻止关联的事实冲突；仍须人工确认同一账户与同一成交。</p>'}${array(p.warnings).length?missing(p.warnings):''}${table(['归档分笔','方向 / 股数','价格 / 费用 / 净收付'],array(p.fills).map(f=>[esc(f.executed_at||[f.date,f.time].filter(Boolean).join(' ')),`${esc(f.side==='buy'?'买入':'卖出')} / ${num(f.quantity,0)}`,`${num(f.price)} / ${num(f.fees)} / ${num(f.net_cash)}`]),'完整分笔清单；不通过关联补齐未知费用')}<label class="tj-check-label"><input type="checkbox" name="identity_confirmation" />已核对原件与既有回写，明确是同一真实成交</label><label class="tj-check-label"><input type="checkbox" name="account_confirmation" />已核对属于同一账户</label>${p.requires_time_difference_confirmation?'<label class="tj-check-label"><input type="checkbox" name="time_difference_confirmation" />已核对两侧具体时刻或分笔粒度差异，仍能确认同一成交</label>':''}${area('reason','本次确认理由（必填）')}<button type="submit"${p.can_confirm&&p.preview_digest?'':' disabled'}>确认事实关联</button>${rawFields(p)}</form>`;note.textContent=p.can_confirm?'请核对两侧事实并逐项明确确认；不会再次回写账户。':'存在事实冲突，不能确认关联。';
    }catch(error){note.textContent='匹配预览未生成：'+error.message+'。重新选择归档分笔后再预览。';note.dataset.state='error';}finally{button.disabled=false;}
  }
  function unlinkExecutionEditor(id) {
    const r=array(state.executionCandidates?.items).find(r=>r.execution_id===id);if(!r||r.record?.episode_id!==state.selected){$('tradeJournalExecutionStatus').textContent='这轮没有可解除的关联，请重新读取核对。';return;}
    state.executionPreview=null;$('tradeJournalExecutionPreview').innerHTML='';$('tradeJournalExecutionEditor').innerHTML=`<form id="tradeJournalExecutionUnlinkForm" class="tj-editor" data-episode="${esc(state.selected)}" data-execution="${esc(id)}" data-revision="${esc(r.revision)}" data-live-digest="${esc(r.live_digest)}" data-stale="false"><h4>说明理由解除事实关联</h4><p class="tj-muted">仅解除本轮关联，保留所有历史版本和真实成交，不冲销持仓、现金或损益。</p>${area('reason','解除理由（必填）')}<button type="submit">追加解除记录</button>${rawFields(r)}</form>`;
  }
  async function saveExecutionMatch(form,{unlink=false}={}) {
    const note=$('tradeJournalExecutionStatus'),v=formValues(form),checked=name=>!!form.elements?.namedItem(name)?.checked;
    if(form.dataset.episode!==state.selected||form.dataset.stale==='true'){note.textContent='核对基线已变化，请重新读取候选并预览。';return;}
    if(!v.reason||!unlink&&(!checked('identity_confirmation')||!checked('account_confirmation')||form.dataset.timeDifference==='true'&&!checked('time_difference_confirmation'))){note.textContent='请填写理由并逐项明确确认同一账户、同一真实成交及必要的时刻差异。';return;}
    let ids=[];try{ids=JSON.parse(form.dataset.fills||'[]');}catch{}
    if(!unlink&&(!state.executionPreview?.can_confirm||state.executionPreview.preview_digest!==form.dataset.digest)){note.textContent='匹配预览已失效，请重新预览后确认。';return;}
    const episode=state.selected,body={action:unlink?'unlink':'confirm',execution_id:form.dataset.execution,expected_revision:Number(form.dataset.revision),reason:v.reason,...(unlink?{live_digest:form.dataset.liveDigest}:{fill_ids:ids,preview_digest:form.dataset.digest,identity_confirmation:checked('identity_confirmation'),account_confirmation:checked('account_confirmation'),time_difference_confirmation:checked('time_difference_confirmation')})},button=form.querySelector('[type=submit]');button.disabled=true;form.setAttribute('aria-busy','true');note.textContent='正在追加事实关联记录，不再次改变账户…';
    try {await api('/episodes/'+encodeURIComponent(episode)+'/execution-matches',{method:'POST',body:JSON.stringify(body)});if(episode!==state.selected||$(unlink?'tradeJournalExecutionUnlinkForm':'tradeJournalExecutionConfirmForm')!==form)return;state.executionPreview=null;$('tradeJournalExecutionEditor').innerHTML='';$('tradeJournalExecutionPreview').innerHTML='';await loadExecutionMatches();note.textContent=unlink?'已追加解除记录；归档与生产账户事实保持原样。':'已保存事实关联；未再次改变持仓、现金或损益。';note.dataset.state='success';}
    catch(error){note.textContent='事实关联未保存：'+error.message+'。请重新读取候选并预览，不使用旧摘要确认。';note.dataset.state='error';form.dataset.stale='true';}finally{form.setAttribute('aria-busy','false');if(form.dataset.stale!=='true')button.disabled=false;}
  }
  async function showExecutionMatchHistory(id) {
    artifact('成交事实关联历史','<p role="status">正在读取不可变关联历史…</p>');
    try{const p=await api('/execution-matches/'+encodeURIComponent(id));artifact('成交事实关联历史',`<p>${esc(executionStateLabel(p.state))} · 版本 ${esc(p.revision)}</p><p class="tj-muted">${esc(p.accounting_scope||'仅关联事实，不再次回写账户')}</p>${array(p.history).map(r=>`<details><summary>版本 ${esc(r.revision)} · ${esc(executionStateLabel(r.state))}</summary><dl class="tj-facts">${field('理由',r.reason)}${field('服务器记录时点',r.captured_at)}${field('关联完整分笔数',array(r.fill_ids).length)}</dl>${rawFields(r)}</details>`).join('')||'<p class="tj-muted">尚无事实关联历史。</p>'}${rawFields(p)}`);}catch(error){artifact('关联历史未读取',`<p>${esc(error.message)}</p>`);}
  }
  const reviewDimensions={entry:'入场',hold:'持有',exit:'退出',adjustment:'加减仓与调整',sizing:'仓位规模',information:'信息依据'};
  const reviewJudgmentLabel = value => ({unknown:'未判断',as_planned:'符合原计划',deviation:'偏离原计划',not_applicable:'不适用'}[value]||'未判断');
  const reviewStateLabel = value => ({missing:'尚未记录',draft:'复盘草稿',confirmed:'已记录并核对过程',stale:'原事实已更新，待重新核对'}[value]||'状态待核');
  function reviewSummary(record) {
    const d=record?.content||record||{};
    return `<dl class="tj-facts">${field('复盘摘要',narrative(d.summary))}${field('总体结论',d.overall_conclusion==='unknown'?'未知':narrative(d.overall_conclusion))}${field('记录版本',record?.revision)}${field('服务器记录时点',record?.captured_at||record?.recorded_at)}</dl><details><summary>六维过程判断</summary>${table(['维度','用户判断','理由'],Object.entries(reviewDimensions).map(([key,title])=>[esc(title),esc(reviewJudgmentLabel(d.judgments?.[key]?.judgment)),esc(narrative(d.judgments?.[key]?.reason))]),'各维判断独立保留；不从盈亏倒推是否违规')}</details>`;
  }
  function retainReviewInputs(form) {state.reviewPendingEdits=state.reviewPendingEdits||{};state.reviewPendingEdits[form.dataset.episode]=formValues(form);}
  function reviewSubmitButtons(form) {const buttons=Array.from(form.querySelectorAll?.('[type=submit]')||[]);return buttons.length?buttons:[form.querySelector('[type=submit]')].filter(Boolean);}
  async function loadProcessReview() {
    const episode=state.selected,note=$('tradeJournalReviewStatus');note.textContent='正在核对本轮复盘记录与当前事实版本…';
    try{const p=await api('/episodes/'+encodeURIComponent(episode)+'/review');if(episode!==state.selected)return;state.processReview=p;state.processReviewEpisode=episode;
      $('tradeJournalReview').innerHTML=`<p><strong>${esc(reviewStateLabel(p.current_state))}</strong></p>${p.record?reviewSummary(p.record):'<p class="tj-muted">这轮尚无过程复盘；可以先保存草稿，所有维度保留未知。</p>'}<p class="tj-muted">“已记录并核对过程”只描述复盘事实，不证明结论、因果或收益优势。</p><button type="button" class="secondary" data-tj-action="process-review-edit">${p.record?'追加复盘版本':'记录本轮过程'}</button>${rawFields(p)}`;
      const form=$('tradeJournalReviewForm');if(form&&form.dataset.episode===episode&&(Number(form.dataset.revision)!==Number(p.latest_revision)||form.dataset.factDigest!==p.fact_digest)){form.dataset.stale='true';retainReviewInputs(form);reviewSubmitButtons(form).forEach(button=>{button.disabled=true;});note.textContent='本轮事实或复盘版本已更新；旧表单已停用，请重新打开核对。';}else note.textContent='已读取当前复盘状态；用户处置待办与真实复盘记录分别保留。';
    }catch(error){if(episode===state.selected){note.textContent='复盘记录未读取：'+error.message+'。请重试读取。';note.dataset.state='error';}}
  }
  async function editProcessReview() {
    const episode=state.selected,root=$('tradeJournalReviewEditor'),seq=(state.reviewEditSeq||0)+1;const previous=$('tradeJournalReviewForm');if(previous?.dataset.episode===episode&&previous.dataset.stale==='true')retainReviewInputs(previous);state.reviewEditSeq=seq;root.innerHTML='<p role="status">正在读取本轮记录…</p>';
    const results=await Promise.allSettled([api('/episodes/'+encodeURIComponent(episode)+'/review'),api('/sources?limit=1000&offset=0')]);if(episode!==state.selected||seq!==state.reviewEditSeq)return;
    if(results[0].status==='rejected'){root.innerHTML=`<p class="tj-empty">复盘记录未读取：${esc(results[0].reason.message)}。请重新点击“完成本次复盘”。</p>`;return;}
    const p=results[0].value,r=p.record,d=r?.content||r||{},sources=results[1].status==='fulfilled'?array(results[1].value.items):[];state.processReview=p;state.processReviewEpisode=episode;
    const fills=array(state.detail?.fills),decisions=array(state.detail?.decisions);
    $('tradeJournalReview').innerHTML='';$('tradeJournalReviewStatus').textContent='';
    const context=`<details class="tj-review-context"><summary>回看本轮成交与已填想法（${fills.length}笔成交 · ${decisions.length}条操作记录）</summary>${table(['成交时间','方向','数量','价格'],fills.map(f=>[esc(String(f.executed_at||[f.date,f.time].filter(Boolean).join(' ')).replace('T',' ').replace(/(?:Z|[+-]\d{2}:\d{2})$/,'')),esc(f.side==='buy'?'买入':f.side==='sell'?'卖出':f.side),num(f.quantity,0)+' 股',num(f.price)+' 元']),'本次确认所依据的已导入成交')}${decisions.map(item=>`<div class="tj-review-note"><strong>${esc(narrative(item.action,'操作待补'))}</strong><dl class="tj-facts">${field('操作理由',narrative(item.reason))}${field('预期',window.JournalWorkspace?.expectationText?.(item.expectation)||narrative(item.expectation))}${field('仓位',narrative(item.risk_constraints,'当时未设或尚未补充'))}</dl></div>`).join('')}</details>`;
    root.innerHTML=`<form id="tradeJournalReviewForm" class="tj-editor tj-completion-form" data-episode="${esc(episode)}" data-revision="${esc(p.latest_revision||0)}" data-fact-digest="${esc(p.fact_digest||'')}" data-original="${esc(JSON.stringify(d))}" data-stale="false">
      <h4>${p.current_confirmed?'更新已确认复盘':'完成本次复盘'} · ${esc(state.detail?.episode?.name||state.detail?.episode?.ticker||'')}</h4>
      <ol class="tj-completion-steps"><li>核对已导入成交的方向、数量和价格。</li><li>确认操作理由、预期和仓位记录符合当时想法；当时没设、记不清的如实保留。</li><li>写一句总结，勾选下方确认，再点击“确认并标记已复盘”。</li></ol>
      <p class="tj-muted">只确认当前已记录的过程。未清仓、未来预期尚未到期、行情缺数或分项未判断，都不影响完成。</p>${context}
      <input type="hidden" name="state" value="confirmed">
      <label class="tj-span">一句复盘总结（完成时必填）<textarea name="summary" rows="3" placeholder="例如：买入理由和仓位记录已核对；三天内上涨的预期尚未到期，继续观察。">${esc(d.summary||'')}</textarea></label>
      <label class="tj-check-label"><input type="checkbox" name="process_confirmation">我已核对当前成交和已填想法；未知项与未来结果继续保留。</label>
      <div class="tj-actions"><button type="submit" name="review_action" value="confirmed"${p.fact_digest?'':' disabled'}>确认并标记已复盘</button>${p.current_confirmed?'':`<button type="submit" class="secondary" name="review_action" value="draft"${p.fact_digest?'':' disabled'}>先保存草稿</button>`}</div>
      <p id="tradeJournalReviewSaveStatus" class="tj-form-status" role="status" aria-live="polite"></p>
      <details class="tj-review-optional"><summary>分项判断与证据（选填）</summary>${input('overall_conclusion','总体结论（未知可留空）',d.overall_conclusion==='unknown'?'':d.overall_conclusion||'')}
      ${Object.entries(reviewDimensions).map(([key,title])=>{const j=d.judgments?.[key]||{};return `<details class="tj-review-dimension"><summary>${esc(title)}判断</summary><label>本维判断<select name="judgment_${key}">${['unknown','as_planned','deviation','not_applicable'].map(value=>`<option value="${value}"${value===(j.judgment||'unknown')?' selected':''}>${esc(reviewJudgmentLabel(value))}</option>`).join('')}</select></label>${area('reason_'+key,'判断理由（未知可留空）',j.reason||'')}<p class="tj-muted">已有引用 ${array(j.evidence_refs).length} 项；完整引用在原始字段保留。</p></details>`;}).join('')}
      <details><summary>补充引用（选填）</summary><div class="tj-form-grid"><label>引用用于<select name="evidence_dimension"><option value="overall">总体复盘</option>${Object.entries(reviewDimensions).map(([key,title])=>`<option value="${key}">${esc(title)}</option>`).join('')}</select></label><label>已有原件来源<select name="evidence_source_id"><option value="">暂不引用来源</option>${sources.map(s=>`<option value="${esc(s.source_id)}">${esc(s.filename||shortId(s.source_id))}</option>`).join('')}</select></label>${input('evidence_row','来源原行号（可选）','','number')}${input('evidence_record_id','或来源记录定位（可选）')}${area('evidence_text','本次证据说明或用户复盘原文')}</div>${results[1].status==='rejected'?'<p class="tj-muted">来源目录未读取；可稍后再补引用。</p>':''}</details>
      ${r?area('revision_reason','修改说明（选填，留空由系统记录）'):''}</details>${rawFields(p)}</form>`;
    const pending=state.reviewPendingEdits?.[episode],form=$('tradeJournalReviewForm');
    if(pending&&form?.dataset.episode===episode&&form.dataset.factDigest===p.fact_digest&&Number(form.dataset.revision)===Number(p.latest_revision||0)){
      for(const [name,value] of Object.entries(pending)){if(['state','process_confirmation','review_action'].includes(name))continue;const field=form.elements?.namedItem(name);if(field)field.value=value;}
      $('tradeJournalReviewSaveStatus').textContent='已恢复上次未保存的输入，并重新读取当前记录。请回看后重新勾选确认。';
    }
  }
  async function saveProcessReview(form,{targetState}={}) {
    if(form.dataset.saving==='true')return;
    const v=formValues(form),note=$('tradeJournalReviewSaveStatus');v.state=targetState||v.state||'confirmed';let original={};try{original=JSON.parse(form.dataset.original||'{}');}catch{}
    if(form.dataset.episode!==state.selected||form.dataset.stale==='true'||!form.dataset.factDigest){note.textContent='事实基线已变化或缺失，请重新打开复盘核对。';return;}
    if(!['draft','confirmed'].includes(v.state)||v.state==='confirmed'&&(!v.summary||!form.elements?.namedItem('process_confirmation')?.checked)){note.textContent='还差最后一步：请写一句复盘总结，并勾选“我已核对当前成交和已填想法”。暂不确认可先保存草稿。';return;}
    const body={expected_revision:Number(form.dataset.revision||0),expected_fact_digest:form.dataset.factDigest,state:v.state,summary:v.summary||'',overall_conclusion:v.overall_conclusion||'unknown',judgments:{},evidence_refs:array(original.evidence_refs)};
    for(const key of Object.keys(reviewDimensions)){const judgment=v['judgment_'+key]||'unknown',reason=v['reason_'+key]||'',refs=array(original.judgments?.[key]?.evidence_refs);if(judgment!=='unknown'||reason||refs.length||original.judgments?.[key]?.user_supplied===true)body.judgments[key]={judgment,reason,evidence_refs:refs};}
    if(v.evidence_text||v.evidence_source_id){const ref={text:v.evidence_text||'本次复盘引用'};if(v.evidence_source_id){ref.source_ref={source_id:v.evidence_source_id};if(v.evidence_row)ref.source_ref.row=Number(v.evidence_row);if(v.evidence_record_id)ref.source_ref.record_id=v.evidence_record_id;}const dimension=v.evidence_dimension;if(reviewDimensions[dimension]){body.judgments[dimension]=body.judgments[dimension]||{judgment:'unknown',reason:'',evidence_refs:[]};body.judgments[dimension].evidence_refs.push(ref);}else body.evidence_refs.push(ref);}
    if(Number(form.dataset.revision)>0)body.revision_reason=v.revision_reason||(v.state==='confirmed'?'确认当前记录，完成本次复盘':'保存本次复盘草稿');const episode=state.selected,buttons=reviewSubmitButtons(form);form.dataset.saving='true';buttons.forEach(button=>{button.disabled=true;});form.setAttribute('aria-busy','true');note.textContent='正在按事实基线保存复盘版本…';
    try {await api('/episodes/'+encodeURIComponent(episode)+'/review',{method:'POST',body:JSON.stringify(body)});if(episode!==state.selected||$('tradeJournalReviewForm')!==form)return;if(state.reviewPendingEdits)delete state.reviewPendingEdits[episode];$('tradeJournalReviewEditor').innerHTML='';await loadProcessReview();try{const refreshed=await api('/episodes/'+encodeURIComponent(episode));if(episode===state.selected){const progress=refreshed.review_progress||refreshed.episode?.review_progress;state.items=array(state.items).map(item=>item.episode_id===episode?{...item,review_progress:progress}:item);if(state.detail){state.detail.review_progress=progress;state.detail.episode.review_progress=progress;}renderList();const badge=$('tradeJournalDetail')?.querySelector?.('.tj-detail-head .tj-review-progress');if(badge)badge.outerHTML=reviewProgressBadge(progress);window.JournalWorkspace?.setReviewProgress?.(progress);}}catch{status('复盘已保存，列表标记刷新失败；重新读取账本后可查看。');}if(episode!==state.selected)return;$('tradeJournalReviewStatus').textContent=v.state==='confirmed'?'已标记“已复盘”。未来预期继续观察；后续记录变化会提示重新确认。':'草稿已保存，仍待确认。准备好后点击“确认并标记已复盘”。';if(state.tasks)await loadTasks({offset:state.taskOffset||0});}
    catch(error){retainReviewInputs(form);note.innerHTML=`复盘未保存：${esc(error.message)}。未保存输入已保留，请重新读取后再次确认。<button type="button" class="secondary" data-tj-action="process-review-edit">重新读取并保留输入</button>`;note.dataset.state='error';form.dataset.stale='true';}finally{form.dataset.saving='false';form.setAttribute('aria-busy','false');if(form.dataset.stale!=='true')buttons.forEach(button=>{button.disabled=false;});}
  }
  async function showProcessReviewHistory() {
    const episode=state.selected;artifact('过程复盘历史','<p role="status">正在读取不可变复盘版本…</p>');
    try{const p=await api('/episodes/'+encodeURIComponent(episode)+'/review/history');artifact('过程复盘历史',array(p.items).map(r=>`<details><summary>版本 ${esc(r.revision)} · ${esc(reviewStateLabel(r.state))}</summary>${reviewSummary(r)}${rawFields(r)}</details>`).join('')||'<p>尚无过程复盘版本。</p>');}catch(error){artifact('复盘历史未读取',`<p>${esc(error.message)}</p>`);}
  }
  async function enter() {mountReviewWorkflows();if(!state.loaded)await load();}
  document.addEventListener('submit', async event=>{
    const f=event.target;
    if(f.id==='tradeJournalFilters'){event.preventDefault();await load({reset:true});}
    if(f.id==='tradeJournalDecisionForm'){event.preventDefault();await saveDecision(f);}
    if(f.id==='tradeJournalCheckForm'){
      event.preventDefault();const v=formValues(f),numeric=v.evaluation_class==='numeric_comparison';let body;
      if(numeric){if(!v.actual_source_id||!v.value_path||v.actual_value===''||!Number.isFinite(Number(v.actual_value))||!v.actual_observed_at||!v.actual_available_at){$('tradeJournalSaveStatus').textContent='请补齐已归档来源、字段路径、实际数值及两个事实时点。';return;}body={evaluation_class:'numeric_comparison',status:'unknown',actual:{value:Number(v.actual_value),metric:v.actual_metric,source_ref:{source_id:v.actual_source_id},value_path:v.value_path,observed_at:v.actual_observed_at,available_at:v.actual_available_at},evidence:evidenceItems(v.evidence)};}
      else {if(!v.actual||!v.evidence||!v.status){$('tradeJournalSaveStatus').textContent='请选择核验结论并填写实际结果及证据；缺数据也应说明缺少什么。';return;}body={evaluation_class:'manual_assessment',status:v.status,actual:v.actual,evidence:evidenceItems(v.evidence)};}
      if(f.dataset.revision)body.decision_revision=Number(f.dataset.revision);
      await saveForm(f,$('tradeJournalSaveStatus'),()=>api('/decisions/'+encodeURIComponent(f.dataset.decision)+'/expectation-checks',{method:'POST',body:JSON.stringify(body)}),numeric?'已保存归档数值比较结果；支持或缺数以服务端核验为准。':'已追加人工预期核验。');
    }
    if(f.id==='tradeJournalLinkForm'){event.preventDefault();const v=formValues(f);if(!v.from_decision_id||!v.to_decision_id||!v.reason||!v.relation){$('tradeJournalLinkStatus').textContent='请填写双方决策标识、关系及理由。';return;}const body={...v,evidence:evidenceItems(v.evidence),evidence_class:'retrospective_recall'};await saveForm(f,$('tradeJournalLinkStatus'),()=>api('/links',{method:'POST',body:JSON.stringify(body)}),'已保存跨票决策关联。');}
    if(f.id==='tradeJournalObservationForm'){event.preventDefault();await saveObservation(f);}
    if(f.id==='tradeJournalPlanForm'){event.preventDefault();await savePreplan(f);}
    if(f.id==='tradeJournalBindPlanForm'){event.preventDefault();await savePlanBinding(f);}
    if(f.id==='tradeJournalContextForm'){event.preventDefault();await loadContext(f);}
    if(f.id==='tradeJournalMethodFilters'){event.preventDefault();await loadMethods();}
    if(f.id==='tradeJournalModeCardForm'){event.preventDefault();await saveMethod(f,'mode');}
    if(f.id==='tradeJournalHypothesisForm'){event.preventDefault();await saveMethod(f,'hypothesis');}
    if(f.id==='tradeJournalAllocationForm'){event.preventDefault();await saveAllocations(f);}
    if(f.id==='tradeJournalAnalyticsForm'){event.preventDefault();await loadAnalytics(f);}
    if(f.id==='tradeJournalPortfolioForm'){event.preventDefault();await loadPortfolio();}
    if(f.id==='tradeJournalScenarioForm'){event.preventDefault();await previewPortfolio(f);}
    if(f.id==='tradeJournalTaskFilters'){event.preventDefault();await loadTasks();}
    if(f.id==='tradeJournalTaskForm'){event.preventDefault();await saveTaskResolution(f);}
    if(f.id==='tradeJournalImportFileForm'){event.preventDefault();await inspectImport(f);}
    if(f.id==='tradeJournalImportMappingForm'){event.preventDefault();await parseImport(f);}
    if(f.id==='tradeJournalImportIgnoreForm'){event.preventDefault();appendImportMappingRow(f,'ignore');}
    if(f.id==='tradeJournalImportPairForm'){event.preventDefault();appendImportMappingRow(f,'pair');}
    if(f.id==='tradeJournalImportConfirmForm'){event.preventDefault();await commitImport(f);}
    if(f.id==='tradeJournalImportIdentityForm'){event.preventDefault();await saveImportIdentityChoices(f);}
    if(f.id==='tradeJournalExecutionSelectForm'){event.preventDefault();await previewExecutionMatch(f);}
    if(f.id==='tradeJournalExecutionConfirmForm'){event.preventDefault();await saveExecutionMatch(f);}
    if(f.id==='tradeJournalExecutionUnlinkForm'){event.preventDefault();await saveExecutionMatch(f,{unlink:true});}
    if(f.id==='tradeJournalReviewForm'){event.preventDefault();await saveProcessReview(f,{targetState:event.submitter?.name==='review_action'?event.submitter.value:undefined});}
  });
  document.addEventListener('click', async event=>{
    const button=event.target.closest?.('[data-tj-action]');if(!button||button.disabled)return;
    const a=button.dataset.tjAction,id=button.dataset.id||'';
    if(a==='episode')await selectEpisode(id);
    if(a==='reload')await load();
    if(a==='reset'){$('tradeJournalFilters').reset();await load({reset:true});}
    if(a==='previous'||a==='next'){state.offset=Math.max(0,state.offset+(a==='next'?state.limit:-state.limit));await load();}
    if(a==='definitions'||a==='definition')openDefinitions(id);
    if(a==='close-definitions')$('tradeJournalDefinitions').close();
    if(a==='retry-definitions'){await loadDefinitions();renderDefinitions();}
    if(a==='new')editDecision('',button.dataset.fill||'');
    if(a==='edit')editDecision(id);
    if(a==='cancel-edit')$('tradeJournalEditor').innerHTML='';
    if(a==='history')await showHistory(id);
    if(a==='task')await editTask(id);
    if(a==='tasks-previous'||a==='tasks-next')await loadTasks({offset:Math.max(0,(state.taskOffset||0)+(a==='tasks-next'?30:-30))});
    if(a==='import-reset')resetImport();
    if(a==='import-remove-identity'){state.importIdentityLinks=array(state.importIdentityLinks).filter((_,i)=>i!==Number(button.dataset.index));invalidateImport('已撤回这项身份确认，请重新核对。',{keepIdentityLinks:true});await parseImport($('tradeJournalImportMappingForm'));}
    if(a==='import-read-rows')await loadImportRows(button.closest('[data-import-rows]'));
    if(a==='import-ignore-row')focusIgnoredRow(button.dataset.sheet,button.dataset.row);
    if(a==='import-remove-ignore'||a==='import-remove-pair'){const key=a==='import-remove-ignore'?'ignored_rows':'corroboration_pairs';if(state.importMapping?.[key]){state.importMapping[key].splice(Number(button.dataset.index),1);invalidateImport();renderImportMapping();}}
    if(a==='import-rows-previous'||a==='import-rows-next'){state.importRowOffset=Math.max(0,(state.importRowOffset||0)+(a==='import-rows-next'?100:-100));renderImportPreview();}
    if(a==='execution-matches')await loadExecutionMatches();
    if(a==='execution-edit')editExecutionMatch(id);
    if(a==='execution-unlink')unlinkExecutionEditor(id);
    if(a==='execution-history')await showExecutionMatchHistory(id);
    if(a==='process-review')await loadProcessReview();
    if(a==='process-review-edit')await editProcessReview();
    if(a==='process-review-history')await showProcessReviewHistory();
    if(a==='linked-decision')await showLinkedDecision(id,button.dataset.revision);
    if(a==='linked-episode'){$('tradeJournalArtifact').close();await selectEpisode(id);}
    if(a==='check')checkEditor(id);
    if(a==='link')linkEditor();
    if(a==='research')window.openStockPage?.(id);
    if(a==='records')window.openJournalSection?.('journalRecords');
    if(a==='verification')window.openJournalSection?.('journalWorkspaceVerification');
    if(a==='ledger')window.switchTab?.('holdings');
    if(a==='plan')showPlan(id);
    if(a==='sources')await loadSources({offset:0});
    if(a==='sources-previous'||a==='sources-next')await loadSources({offset:Math.max(0,state.sourceOffset+(a==='sources-next'?20:-20))});
    if(a==='source')await showSource(id);
    if(a==='download-source')await downloadSource(id);
    if(a==='close-artifact')$('tradeJournalArtifact').close();
    if(a==='observations')await loadObservations({offset:0});
    if(a==='observations-previous'||a==='observations-next')await loadObservations({offset:Math.max(0,state.observationOffset+(a==='observations-next'?10:-10))});
    if(a==='observe-research')previewResearchObservation();
    if(a==='observe-status')observationStatusEditor(id,button.dataset.ticker);
    if(a==='research-page')window.switchTab?.('decision-research');
    if(a==='cancel-observation')$('tradeJournalObservationEditor').innerHTML='';
    if(a==='plans')await loadPlans({offset:0});
    if(a==='plans-previous'||a==='plans-next')await loadPlans({offset:Math.max(0,state.planOffset+(a==='plans-next'?20:-20))});
    if(a==='new-plan')await editPreplan();
    if(a==='revise-preplan')await editPreplan(id,button.dataset.revision);
    if(a==='view-preplan')await viewPreplan(id,button.dataset.revision);
    if(a==='cancel-preplan')$('tradeJournalPlanEditor').innerHTML='';
    if(a==='bind-plan')await bindPlanEditor();
    if(a==='context')contextEditor();
    if(a==='observation-members')await loadObservationMembers(id);
    if(a==='new-mode')await editModeCard();
    if(a==='new-hypothesis')await editHypothesis();
    if(a==='view-method')await viewMethod(button.dataset.kind,id,button.dataset.revision);
    if(a==='edit-method')await (button.dataset.kind==='mode'?editModeCard(id,button.dataset.revision):editHypothesis(id,button.dataset.revision));
    if(a==='cancel-method')$('tradeJournalMethodEditor').innerHTML='';
    if(a==='allocations')await loadAllocations();
    if(a==='edit-allocations')editAllocations();
    if(a==='allocation-history')await allocationHistory();
    if(a==='allocation-add'){state.allocationRows=state.allocationRows||[];state.allocationRows.push({fill_id:'',mode_ref:null,quantity:'',buy_lot_id:''});editAllocations({reuse:true});}
    if(a==='allocation-remove'){state.allocationRows.splice(Number(button.dataset.index),1);editAllocations({reuse:true});}
    if(a==='analytics-group')await showAnalyticsGroup(id);
    if(a==='scenario-add'){state.scenarioLegs=state.scenarioLegs||[];state.scenarioLegs.push({ticker:'',side:'buy',quantity:'',price:'',fees:''});renderScenarioLegs();}
    if(a==='scenario-remove'){state.scenarioLegs.splice(Number(button.dataset.index),1);renderScenarioLegs();}
    if(a==='portfolio-history')await portfolioHistory();
    if(a==='portfolio-capture')await capturePortfolio();
    if(a==='portfolio-snapshot')await showPortfolioSnapshot(id);
    if(a==='attach-portfolio')await capturePortfolio({forPlan:true});
    if(a==='clear-plan-portfolio')planPortfolioRef(null);
    if(a==='view-plan-portfolio'){let ref;try{ref=JSON.parse($('tradeJournalPlanForm')?.dataset.portfolioSnapshotRef||'null');}catch{}if(ref?.snapshot_id)await showPortfolioSnapshot(ref.snapshot_id);else $('tradeJournalPlanPortfolioStatus').textContent='尚未关联事实快照。';}
  });
  document.addEventListener('change',event=>{
    const id=event.target.id;
    if(id==='tradeJournalTaskAction'&&$('tradeJournalTaskUnknownLabel'))$('tradeJournalTaskUnknownLabel').hidden=event.target.value!=='confirm_unknown';
    if(id==='tradeJournalImportConfirmed')state.importConfirmed=event.target.checked;
    if(event.target.name==='fill_ids'&&event.target.closest?.('#tradeJournalExecutionSelectForm')){state.executionPreview=null;$('tradeJournalExecutionPreview').innerHTML='<p class="tj-muted">所选归档分笔已变化，请重新预览事实差异。</p>';}
    if(event.target.dataset?.importSheet)updateImportMapping(event.target);
    if(id==='tradeJournalImportFile'||event.target.name==='encoding'&&event.target.closest?.('#tradeJournalImportFileForm')){invalidateImport('原件或编码已变化，请重新检查文件。');state.importFile=null;state.importInspection=null;state.importMapping=null;$('tradeJournalImportMapping').innerHTML='';}
    if(event.target.name==='account_alias'&&event.target.closest?.('#tradeJournalImportFileForm'))invalidateImport('归档代号已变化，请重新生成规范预览。');
    if(id==='tradeJournalCheckClass'){$('tradeJournalManualCheck').hidden=event.target.value==='numeric_comparison';$('tradeJournalNumericCheck').hidden=event.target.value!=='numeric_comparison';}
    if(['tradeJournalObservationWindow','tradeJournalObservationMode','tradeJournalObservationDay'].includes(id)&&state.researchSelection){state.researchSelection[id==='tradeJournalObservationWindow'?'window':id==='tradeJournalObservationMode'?'mode':'date']=event.target.value;previewResearchObservation();}
    if(id==='tradeJournalContextWindow'){const modes=state.contextResearch?.assessments?.windows?.[event.target.value]||{};$('tradeJournalContextMode').innerHTML=Object.values(modes).map(m=>`<option value="${esc(m.key)}">${esc(m.name||m.key)}</option>`).join('');}
    if(id==='tradeJournalOutcomeType'){const form=event.target.form,winning=form?.elements?.namedItem('winning_count');if(winning){winning.disabled=!['actual_trades','simulation'].includes(event.target.value);if(winning.disabled)winning.value='';}}
    const el=event.target;
    if(el.dataset?.allocationRow!=null){const row=state.allocationRows?.[Number(el.dataset.allocationRow)],field=el.dataset.allocationField;if(row){if(field==='mode_ref'){try{row.mode_ref=JSON.parse(el.value);}catch{row.mode_ref=null;}}else row[field]=el.value;if(field==='fill_id'){row.buy_lot_id='';editAllocations({reuse:true});}}}
    if(el.dataset?.scenarioRow!=null&&state.scenarioLegs?.[Number(el.dataset.scenarioRow)])state.scenarioLegs[Number(el.dataset.scenarioRow)][el.dataset.scenarioField]=el.value;
  });
  document.addEventListener('input',event=>{const el=event.target;if(el.id==='tradeJournalDefinitionSearch')renderDefinitions(el.value);if(el.closest?.('#tradeJournalDecisionForm'))el.removeAttribute?.('aria-invalid');if(el.dataset?.allocationField==='quantity'&&state.allocationRows?.[Number(el.dataset.allocationRow)])state.allocationRows[Number(el.dataset.allocationRow)].quantity=el.value;if(el.name==='reason'&&el.closest?.('#tradeJournalAllocationForm'))state.allocationReason=el.value;if(el.dataset?.scenarioRow!=null&&state.scenarioLegs?.[Number(el.dataset.scenarioRow)])state.scenarioLegs[Number(el.dataset.scenarioRow)][el.dataset.scenarioField]=el.value;});
  document.addEventListener('keydown',async event=>{if(event.key==='Enter'&&event.target.name==='row_start'){const region=event.target.closest?.('[data-import-rows]');if(region){event.preventDefault();await loadImportRows(region);}}});
  document.addEventListener('DOMContentLoaded',()=>{
    const params=new URLSearchParams(location.search);
    if(params.get('journal')==='1'){
      const form=$('tradeJournalFilters');for(const key of ['query','start_date','end_date'])if(params.has(key))form.elements.namedItem(key).value=params.get(key);
      window.switchTab?.('trade-journal');
    }
    for(const id of ['tradeJournalDefinitions','tradeJournalArtifact'])$(id)?.addEventListener('click',event=>{if(event.target===$(id)){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
  });
  window.TradeJournal={enter,load,openForTicker,selectEpisode,renderDetail,renderDefinitions,editDecision,saveDecision,showHistory,showLinkedDecision,mountReviewWorkflows,loadTasks,editTask,saveTaskResolution,inspectImport,renderImportMapping,updateImportMapping,parseImport,renderImportPreview,commitImport,appendImportMappingRow,invalidateImport,resetImport,saveImportIdentityChoices,loadImportRows,focusIgnoredRow,loadExecutionMatches,editExecutionMatch,previewExecutionMatch,unlinkExecutionEditor,saveExecutionMatch,showExecutionMatchHistory,loadProcessReview,editProcessReview,saveProcessReview,showProcessReviewHistory,checkEditor,loadSources,showSource,showPlan,loadObservations,loadObservationMembers,previewResearchObservation,observationStatusEditor,saveObservation,loadPlans,editPreplan,savePreplan,viewPreplan,bindPlanEditor,savePlanBinding,contextEditor,loadContext,loadMethods,editModeCard,editHypothesis,saveMethod,viewMethod,loadAllocations,editAllocations,saveAllocations,loadAnalytics,showAnalyticsGroup,loadPortfolio,renderPortfolio,previewPortfolio,capturePortfolio,portfolioHistory,showPortfolioSnapshot,planPortfolioRef,state};
})();
