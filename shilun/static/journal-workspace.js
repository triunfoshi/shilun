/* Review workspace: existing journal facts and explicit user-initiated verification. */
(function (global) {
  'use strict';
  const BASE = '/api/v1/trade-journal';
  const state = {stage:'home', archive:false, flowEpisodeId:'', resumeStage:null, detail:null, generation:0, decisionKey:'', market:null, marketKey:'', comparison:null, busy:false, reviewContext:null, contextEpisode:'', contextError:'', scopeFillId:'', conditionDirty:false, statementDismissed:{}, tasks:null, taskError:'', taskLoading:false, taskGeneration:0};
  const list = value => Array.isArray(value) ? value : [];
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const $ = id => global.document?.getElementById(id);
  const number = (value, digits=2) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value).toLocaleString('zh-CN',{minimumFractionDigits:digits,maximumFractionDigits:digits}) : '—';
  const short = value => {const s=String(value||''); return s.length>32?s.slice(0,19)+'…'+s.slice(-8):s;};
  const text = value => value == null ? '' : typeof value === 'string' ? value : JSON.stringify(value);
  const keyOf = row => JSON.stringify([row.decision_id,row.revision]);
  const classes = {contemporaneous:'当时记录',retrospective_recall:'事后补记',historical_reconstruction:'历史重建',historical_replay:'历史规则回放'};
  const metricLabel = Object.fromEntries([['open','开盘'],['high','盘中最高'],['low','盘中最低'],['close','收盘']].flatMap(([key,label])=>[[key+'_return_pct',label+'涨幅'],[key+'_price',label+'价']]));
  const metricChoiceLabel = {open_return_pct:'开盘时涨幅就达到目标（%）',high_return_pct:'盘中最高涨到目标（%）',low_return_pct:'盘中最低到达目标（%）',close_return_pct:'收盘时涨幅仍达到目标（%）',open_price:'开盘股价就达到目标（元）',high_price:'盘中最高股价达到目标（元）',low_price:'盘中最低股价到达目标（元）',close_price:'收盘股价仍达到目标（元）'};
  function targetTimingHelp(metric) {const field=String(metric||'').split('_')[0];return ({open:'检查目标日开盘的价格是否达到你填写的目标。',high:'检查目标日盘中是否曾涨到你填写的目标，使用当天最高价。',low:'检查目标日盘中最低价与你填写的目标比较；下跌目标通常搭配“不高于目标”。',close:'检查目标日收盘时是否仍达到你填写的目标。'})[field]||'例如期待次日盘中涨到+5%，选择“盘中最高涨到目标”；期待收盘仍有+5%，选择“收盘时涨幅仍达到目标”。';}
  const baselineLabel = {entry_price:'关联买入价',previous_close:'目标日前收盘价'};
  function decisions(detail=state.detail) {return list(detail?.decisions).filter(d=>d.decision_id&&Number.isInteger(d.revision));}
  function chosenDecision() {return decisions().find(d=>keyOf(d)===state.decisionKey)||null;}
  function contextKey() {return JSON.stringify([state.detail?.episode?.episode_id,state.decisionKey,state.generation]);}
  function facts(items) {return '<dl class="jw-facts">'+items.map(([label,value])=>`<div><dt>${escape(label)}</dt><dd>${escape(value == null || value === '' ? '—' : value)}</dd></div>`).join('')+'</dl>';}
  function notes(rows) {return list(rows).length?`<ul class="jw-notes">${rows.map(r=>`<li>${escape(text(r))}</li>`).join('')}</ul>`:'';}
  function sourceDetails(sources) {return list(sources).length?`<details class="jw-evidence"><summary>查看取证来源（${sources.length}）</summary><pre>${escape(JSON.stringify(sources,null,2))}</pre></details>`:'';}
  function fullValue(value) {return value==null?'—':typeof value==='object'?JSON.stringify(value):String(value);}
  function expectationText(value) {
    if(!value||typeof value!=='object')return fullValue(value);
    const metric=metricLabel[value.metric],op=({'gte':'>=','lte':'<='})[value.operator]||value.operator;
    const unit=value.metric?.endsWith('_return_pct')?'%':value.metric?.endsWith('_price')?' 元':'';
    const rule=metric&&op&&value.target!=null?`${metric} ${op} ${number(value.target)}${unit}`:'';
    return [value.text||value.raw,rule,value.baseline?baselineLabel[value.baseline]||'基准待核':'',value.target_date?'目标 '+value.target_date:''].filter(Boolean).join('；')||'原结构化预期尚缺明确指标、阈值或基准';
  }
  function actualText(result) {
    if(result.actual==null)return '—';
    const value=typeof result.actual==='object'?result.actual.value:result.actual;
    const metric=result.actual?.metric||result.metric;
    return number(value)+(metric?.endsWith('_return_pct')?'%':metric?.endsWith('_price')?' 元':'');
  }
  function baselineText(result) {
    if(result.baseline==null)return '—';
    const value=typeof result.baseline==='object'?result.baseline.value:result.baseline;
    return [number(value)+' 元',baselineLabel[result.baseline?.kind]||'',result.baseline?.date].filter(Boolean).join(' · ');
  }
  function realizedProfitText() {const e=state.detail?.episode; if(!e)return '待成交与费用记录补齐';if(!list(state.detail?.fills).some(f=>f.side==='sell'))return '尚无已导入卖出，最终收益待核';return (e.status==='closed'?'':'本轮未确认清仓；已存卖出实现 ')+(e.realized_pnl!=null?number(e.realized_pnl)+' 元':'费用记录待补齐');}
  function renderMarketResult(result) {
    if(!result)return '';
    const label = {supported:'行情达到目标',refuted:'行情未达到目标',unknown:'行情暂不能判断'}[result.status]||'行情暂不能判断';
    const tone = ['supported','refuted'].includes(result.status)?result.status:'unknown';
    const execution=result.execution_evaluation||{};
    const executionLabel={supported:'已导入卖出价达到目标',refuted:'已导入卖出价未达到目标',partial:'部分数量或部分卖出价达到目标',not_recorded:'未找到这批股票在目标日的卖出记录',unknown:'实际执行暂不能判断'}[execution.status]||'实际执行暂不能判断';
    const prices=list(result.price_observations);
    const sells=list(execution.fills);
    const legacyUnclear=!result.expectation?.origin&&noOriginalPlan(result.expectation);
    const reference=result.interprets_original_expectation===false||result.evaluation_purpose==='post_hoc_reference'||result.expectation?.origin==='post_hoc_review';
    return `<article class="jw-result" data-state="${tone}"><div class="jw-result-heading"><strong>${label}</strong><span class="jw-badge">${reference?'现在设定的复盘参考':legacyUnclear?'后补数值条件（来源待确认）':escape(classes[result.evidence_class]||'记录性质待核')}</span></div>${reference?'<p class="jw-muted">这是现在设定的对照条件，不能据此判定当时有没有按计划交易。</p>':legacyUnclear?'<p class="jw-muted">原文说当时未设具体预期。这里只按后来保存的数值条件比较行情，目标来源尚待确认，不能评价原计划。</p>':''}<h4>行情是否给过机会</h4>${facts([
      ['核对日期',result.target_date],['核对口径',expectationText(result.expectation)],['该口径的实际值',actualText(result)],['涨幅基准',baselineText(result)]
    ])}${prices.length?facts(prices.map(p=>[p.label,`${number(p.price)} 元${p.comparison_value!=null&&result.metric?.endsWith('_return_pct')?' · '+number(p.comparison_value)+'%':''} · ${({supported:'达标',refuted:'未达',unknown:'缺数据'})[p.status]||'待核'}`])):''}<p class="jw-muted">最高价只证明当天到过该价；日线不能给出具体触达分钟，也不能证明你在该价成交。</p>${notes(result.reasons)}<h4>实际卖出是否做到</h4><p><strong>${escape(executionLabel)}</strong></p>${execution.reason?`<p class="jw-muted">${escape(execution.reason)}</p>`:''}${sells.length?facts(sells.map(s=>[s.executed_at||s.date,`${number(s.quantity,0)} 股 × ${number(s.price)} 元 · ${s.condition_met?'达到目标价':'未达到目标价'}`])):''}<p class="jw-muted">这里只比较目标日卖出价，是否恰在开盘或触达最高价的分钟成交尚未自动判断。按已导入交割单的批次数量归属核对；记录不完整时，不能把“没有卖单”当成“没有卖出”。</p><h4>最终赚了多少</h4>${facts([['本轮已实现盈亏（含已存费用）',realizedProfitText()]])}<p class="jw-muted">本轮盈亏与这条目标的行情、卖出执行分别展示。涨幅达标不等于已经实现该收益。</p>${sourceDetails(result.sources)}</article>`;
  }
  function renderComparisonResult(result) {
    if(!result)return '';
    const compared=result.status==='compared';
    const metrics = [['实际净收益',number(result.actual?.net_pnl)+' 元'],['实际收益率',number(result.actual?.return_pct)+'%'],['规则参考净收益',number(result.reference?.net_pnl)+' 元'],['规则参考收益率',number(result.reference?.return_pct)+'%'],['实际 − 参考',number(result.difference_pp)+' 个百分点'],['规则退出日',result.reference?.exit_date]];
    return `<article class="jw-result" data-state="${compared?'compared':'unknown'}"><div class="jw-result-heading"><strong>${compared?'同一入场的退出对照':'暂不能完成收益对照'}</strong><span class="jw-badge">历史规则回放</span></div>${facts(metrics)}${facts([['对照数量',number(result.actual?.quantity??result.entry?.quantity,0)+' 股'],['含费买入成本',number(result.actual?.buy_cost)+' 元'],['参考卖出费用',number(result.reference?.estimated_sell_fee)+' 元'],['滑点成本',number(result.reference?.slippage_cost)+' 元']])}${notes(result.reasons)}${notes(result.limitations)}${sourceDetails(result.sources)}<p class="jw-muted">这是后设规则的收盘价情景参考；不代表当时已有此计划，也不证明能够成交或模式已验证。</p></article>`;
  }
  const STAGES = ['pick','notes','check'];
  function applyFlow() {
    const root=$('tradeJournalRoot');
    if(root){root.dataset.jwStage=state.stage;root.dataset.jwArchive=String(state.archive);root.dataset.jwFocus=state.focusSection||'';}
  }
  function showStage(stage,{scroll=true}={}) {
    state.focusSection=null;state.archive=stage==='archive';
    stage=stage==='archive'?'notes':stage;
    if(!['home',...STAGES].includes(stage))stage='home';
    if(['notes','check'].includes(stage)&&!state.detail?.episode)stage='pick';
    state.stage=stage;state.resumeStage=null;applyFlow();renderOverviewDOM();void loadReviewContext();
    if(scroll){const overview=$('journalWorkspaceOverview');overview?.scrollIntoView?.({block:'start'});const heading=overview?.querySelector?.('h2');heading?.setAttribute?.('tabindex','-1');heading?.focus?.({preventScroll:true});}
  }
  function openSection(id) {
    if(id==='journalRecords'){showStage('pick');return true;}
    if(id==='journalWorkspaceVerification'){showStage('check');return true;}
    return false;
  }
  function renderCompletion() {
    if(!state.detail?.episode||!['notes','check'].includes(state.stage)||state.focusSection==='process')return '';
    const progress=state.detail.review_progress||state.detail.episode.review_progress||{},done=progress.status==='reviewed',changed=progress.status==='needs_update';
    const title=done?'本次复盘已完成':changed?'记录有更新，请重新确认':'最后一步：确认本次复盘';
    return `<aside class="jw-completion" aria-label="复盘完成条件"><div><strong>${title}</strong><p>${done?'你已确认当前记录。新增成交、修改预期或保存新的核对结果后，会提示重新确认。':'核对已导入成交和已填想法，写一句总结并勾选确认，即可标记“已复盘”。'}</p>${done?'':'<p class="jw-muted">尚未清仓、未来预期未到期、未知项未补齐，都可以先完成本次复盘。</p>'}</div><button type="button"${done?' class="secondary"':''} data-jw-action="process">${done?'查看已确认复盘':changed?'重新确认本次复盘':'完成本次复盘'}</button></aside>`;
  }
  function renderOverview(tasks,error='',loading=false) {
    const rows=list(tasks?.items),total=tasks?.total,stage=state.stage;
    const titles={home:'从一笔交易开始复盘',pick:'选一笔交易',notes:'补齐当时的想法',check:'核对预期和结果'};
    const descriptions={home:'先选交易，写清原先预期，再回看仓位、卖点和收益。',pick:'最近的交易排在前面。点击股票名称，开始这一轮复盘。',notes:'先看为什么操作、期待什么、仓位怎么安排。记不清的可以留空。',check:'分别核对预期是否发生、是否按计划执行，以及实际收益。'};
    const steps=STAGES.map((id,i)=>`<li${stage===id?' aria-current="step"':''}><b>${i+1}</b><span>${['选交易','补预期','核对结果'][i]}</span></li>`).join('');
    const action=stage==='home'?'<button type="button" class="jw-start" data-jw-action="start">开始复盘</button>':stage==='pick'?'<button type="button" class="secondary" data-jw-action="home">返回首页</button>':`<div class="jw-actions"><button type="button" class="secondary" data-jw-action="${stage==='check'?'notes':'pick'}">${stage==='check'?'上一步：补预期':'换一笔交易'}</button><button type="button" data-jw-action="${stage==='notes'?'check':'pick'}">${stage==='notes'?'下一步：核对结果':'返回交易列表'}</button></div>`;
    return `<section class="jw-overview"><header><div><h2>${state.focusSection==='process'?'确认本次复盘':titles[stage]||titles.home}${state.archive&&state.focusSection!=='process'?' · 完整档案':''}</h2><p>${state.focusSection==='process'?'核对已发生、已记录的过程，未来结果继续观察。':descriptions[stage]||descriptions.home}</p></div>${state.focusSection==='process'?'<button type="button" class="secondary" data-jw-action="notes">返回操作记录</button>':action}</header>${state.focusSection==='process'?'':`<ol class="jw-workflow" aria-label="复盘顺序">${steps}</ol>${renderContext()}`}${renderCompletion()}${stage==='home'?'<p class="jw-home-tip">每次只复盘一轮交易；模式统计和导入对账收在下方的更多工具中。</p>':''}<div id="journalWorkspaceTaskStatus" class="jw-inline-status" role="status" aria-live="polite" data-state="${error?'error':loading?'loading':'ready'}">${escape(error?'待办未读取：'+error:loading?'正在读取待办…':tasks?`本次读取 ${rows.length} 项${Number.isInteger(total)?`，筛选范围共 ${total} 项`:''}`:'')}</div>${rows.length?`<ul class="jw-task-list">${rows.map(t=>`<li><div><strong>${escape(t.name||t.ticker||'证券待核')}</strong><span>${escape(t.title||'待复查事项')}</span></div><button type="button" class="secondary" data-jw-action="task" data-id="${escape(t.task_id)}" data-episode="${escape(t.episode_id)}">核对记录</button></li>`).join('')}</ul>`:''}</section>`;
  }
  function renderOverviewDOM() {const root=$('journalWorkspaceOverview');if(root)root.innerHTML=renderOverview(state.tasks,state.taskError,state.taskLoading);}
  function input(name,label,value='',type='text',extra='') {return `<label>${escape(label)}<input name="${escape(name)}" type="${type}" value="${escape(value)}" ${extra}></label>`;}
  function select(name,label,values,selected) {return `<label>${escape(label)}<select name="${escape(name)}">${values.map(([value,title])=>`<option value="${escape(value)}"${value===selected?' selected':''}>${escape(title)}</option>`).join('')}</select></label>`;}
  function buyFills() {return list(state.detail?.fills).filter(f=>f.side==='buy');}
  function scopedFill(decision=chosenDecision()) {
    const buys=buyFills(),condition=decision?.expectation||{};
    const id=state.scopeFillId||condition.scope_fill_id||condition.baseline_fill_id;
    if(id)return buys.find(f=>f.fill_id===id)||null;
    const linked=buys.filter(f=>list(decision?.fill_ids).includes(f.fill_id));
    return linked.length===1?linked[0]:buys.length===1?buys[0]:null;
  }
  function fillLabel(fill) {return [String(fill.executed_at||fill.date||'日期待核').replace('T',' ').replace(/(?:Z|[+-]\d{2}:\d{2})$/,''),number(fill.quantity,0)+'股',number(fill.price)+'元'].join(' · ');}
  function noOriginalPlan(value) {return /(?:没有|未|没)(?:有)?(?:(?:制定|设定|设置|设|定|具体|明确|清晰)){0,2}预期|当时未设|没有计划/.test(expectationText(value));}
  function statementPromptKey(c=state.reviewContext) {const f=c?.freshness||{};return JSON.stringify([f.account_alias,c?.as_of_date,f.latest_expected_trade_date,f.latest_fill_date,f.latest_imported_at]);}
  function statementIsDismissed(key) {if(state.statementDismissed[key])return true;try{return global.sessionStorage?.getItem('journal-statement-prompt:'+key)==='collapsed';}catch{return false;}}
  function dismissStatementPrompt() {const key=statementPromptKey();state.statementDismissed[key]=true;try{global.sessionStorage?.setItem('journal-statement-prompt:'+key,'collapsed');}catch{}const panel=$('journalReviewContext');if(panel)panel.open=false;}
  function renderContext() {
    const c=state.reviewContext;
    if(!state.detail?.episode||!['notes','check'].includes(state.stage))return '';
    if(!c)return '<aside class="jw-context"><p>'+escape(state.contextError||'正在核对交割单日期…')+'</p><div id="journalReviewContext"></div></aside>';
    const f=c.freshness||{},current=f.date_is_current===true;
    const expanded=!current&&!statementIsDismissed(statementPromptKey(c));
    const title=current?'交割单日期已到最新交易日':'交割单更新提示';
    return `<details class="jw-context" id="journalReviewContext"${expanded?' open':''}><summary><strong>${title}</strong><span>已存成交至 ${escape(f.latest_fill_date||'未知')}</span></summary><p>今天 ${escape(c.as_of_date)} · 最近交易日 ${escape(f.latest_expected_trade_date||'待核')} · 本轮首次买入 ${escape(c.episode_start_date)}。</p><p class="jw-muted">${current?'已存成交日期已到最近交易日。':'目前的交割单日期落后于最近交易日，是否需要补充？'}日期相同仍不能证明当天全部成交已导入。${f.latest_imported_at?' 最近一次导入 '+escape(f.latest_imported_at.slice(0,16).replace('T',' '))+'。':''}</p><div class="jw-actions"><button type="button" class="secondary" data-jw-action="import">导入新的交割单</button><button type="button" class="secondary" data-jw-action="continue-recorded">先按已有记录复盘</button></div><p class="jw-muted">自动导入：${c.import_automation?.enabled?'已启用':c.import_automation?.configured?'文件夹已建，等待首份文件核对格式；尚未自动入账':'尚未启用，文件夹脚本待配置'}。</p></details>`;
  }
  async function loadReviewContext() {
    const episode=state.detail?.episode?.episode_id;if(!episode||state.contextEpisode===episode||!$('journalReviewContext'))return;
    state.contextEpisode=episode;state.contextError='';
    try{const value=await api('/episodes/'+encodeURIComponent(episode)+'/review-context');if(state.detail?.episode?.episode_id!==episode)return;state.reviewContext=value;renderOverviewDOM();if(!state.conditionDirty)renderDetail();}
    catch(error){if(state.detail?.episode?.episode_id===episode){state.contextError='交割单覆盖情况未读取：'+error.message;state.contextEpisode='';renderOverviewDOM();}}
  }
  function renderDetail() {
    const root=$('journalWorkspaceVerification');if(!root)return;
    const detail=state.detail,episode=detail?.episode;
    if(!episode){root.innerHTML='<div class="jw-empty"><strong>选择一轮交易，核对预期与收益</strong><p>原决策版本、后续行情和实际成交将在这里对照。</p></div>';return;}
    const rows=decisions(),decision=chosenDecision(),condition=decision?.expectation&&typeof decision.expectation==='object'?decision.expectation:{};
    const fill=scopedFill(decision),sameScope=!condition.scope_fill_id||condition.scope_fill_id===fill?.fill_id;
    const fillContext=list(state.reviewContext?.buy_fills).find(f=>f.fill_id===fill?.fill_id);
    const targetDate=sameScope&&(condition.target_date||condition.target_trade_date)||fillContext?.next_trading_day||'';
    const hasRule=!!(metricLabel[condition.metric]&&condition.target!=null&&targetDate&&sameScope);
    const oldText=typeof decision?.expectation==='string'?decision.expectation:condition.text||condition.raw||'';
    const originalAbsent=noOriginalPlan(decision?.expectation)||!oldText&&!hasRule;
    const choices=rows.map(d=>[keyOf(d),`${d.observed_at?.slice(0,16)||list(d.fill_ids).map(id=>list(detail.fills).find(f=>f.fill_id===id)?.date).filter(Boolean).join('、')||'日期待核'} · ${d.action||d.action_id||'操作'} · v${d.revision}`]);
    const metric=sameScope?condition.metric||'':'';
    root.innerHTML=`<section class="jw-review-panel"><header><div><h3>${escape(episode.name||episode.ticker)} · 核对这次操作</h3><p>先确认哪次买入，再分开看行情和实际卖出。</p></div></header>${rows.length?`<section class="jw-module" id="journalMarketCheckPanel"><h4>1. 核对哪次买入或加仓</h4><div class="jw-form-grid">${rows.length>1?select('jw_decision','选择操作记录',choices,state.decisionKey):facts([['操作记录',decision?.action||'买入']])}${buyFills().length>1?select('jw_scope','这条预期针对哪批买入',[['','请选择具体买入'],...buyFills().map(f=>[f.fill_id,fillLabel(f)])],fill?.fill_id||''):facts([['所选买入',fill?fillLabel(fill):'买入记录待补']])}</div>${fill?`<p class="jw-muted">本次核对：${escape(fillLabel(fill))}。${fillContext?.next_trading_day?'这批最早可卖日 '+escape(fillContext.next_trading_day)+'（T+1）。':'T+1日期待交易日历核实。'} 新加仓可以单独设预期；不会自动推迟旧批次的目标日。</p>`:'<p class="jw-muted">多次买入时请明确选择；不能用第一次买入日期代表所有加仓。</p>'}<h4>2. 当时有什么预期</h4><div class="jw-original">${facts([['原记录',oldText||'当时未设或尚未补充']])}</div>${originalAbsent?'<p class="jw-plan-absent">当时未设明确预期，不能判断“原计划未达标”。下面可以建立现在的复盘参考。</p>':''}<form id="journalConditionForm" class="jw-condition-form"><div class="jw-form-grid">${select('expectation_origin','这项目标从哪里来',[['post_hoc_review','当时未设，现在建立复盘参考'],['recalled_plan','当时已有，现在补记']],condition.origin||(originalAbsent?'post_hoc_review':'recalled_plan'))}${input('target_date','在哪个交易日核对',targetDate,'date','required')}<p class="jw-muted jw-span">默认建议所选买入的下一交易日。T+1是最早可卖日，预期也可以约定更晚日期；每次加仓分别设置。</p>${select('metric','目标在什么时候达到才算数？', [['','请选择目标的判断时点'],...Object.entries(metricChoiceLabel)],metric)}${input('target',metric.endsWith('_price')?'目标股价（元）':'目标涨幅（%）',sameScope?condition.target??'':'','number','step="any" required')}<p id="journalTimingHelp" class="jw-muted jw-span">${escape(targetTimingHelp(metric))} 这里只核对行情，不代表你在这个价格卖出了。</p><p id="journalTargetHelp" class="jw-muted jw-span">${metric.endsWith('_price')?'填写具体股价，例如18表示18元。':'填写百分比，例如2表示相对下方基准上涨2%，不是2元。'}</p>${select('baseline','涨幅相对什么',[['entry_price','所选这批买入价'],['previous_close','目标交易日前收盘价']],condition.baseline||'entry_price')}${select('operator','怎样算达到',[['>=','不低于目标'],['<=','不高于目标']],({'gte':'>=','lte':'<='})[condition.operator]||condition.operator||'>=')}</div><input type="hidden" name="baseline_fill_id" value="${escape(fill?.fill_id||'')}"><input type="hidden" name="original_text" value="${escape(oldText)}"><p class="jw-muted">这里只保存明确的对照条件。原记录保留，变更说明由系统生成，不需要再写一遍买入理由。</p><button type="submit">${hasRule?'保存目标的新版本':'保存核对目标'}</button><p id="journalConditionStatus" class="jw-inline-status" role="status" aria-live="polite"></p></form><h4>3. 分开看行情与实际执行</h4>${hasRule?`<p>${escape(expectationText(condition))}</p>`:'<p class="jw-muted">先保存上面的核对目标，再读取行情。没有具体条件时保留“无法判断”。</p>'}<form id="journalMarketCheckForm" class="jw-check-form"><div class="jw-actions"><button type="submit"${hasRule?'':' disabled'}>读取行情与成交</button><button type="button" class="secondary" data-jw-action="save-check" disabled>保存核对结果</button></div><p id="journalMarketCheckStatus" class="jw-inline-status" role="status" aria-live="polite"></p></form><div id="journalMarketCheckResult">${state.market?renderMarketResult(state.market):facts([['行情是否给过机会','尚未核对；会区分开盘、盘中最高/最低和收盘'],['实际卖出是否做到','按这批对应卖单判断；交割单不齐时保留待核'],['本轮已实现盈亏',realizedProfitText()]])}</div></section>`:'<p class="jw-empty">这轮还没有操作记录。先补记买入理由；当时没设预期可以直接注明。</p><button type="button" data-jw-action="decision">补记这次操作</button>'}<details class="jw-module" id="journalRuleComparisonPanel"><summary>研究参考：假设次日收盘卖出</summary><p class="jw-muted">这是另一项研究：首笔买入如果在T+1收盘卖出会怎样。它不代表你的原预期，后续加仓不混入这项对照。</p><form id="journalRuleComparisonForm"><div class="jw-form-grid">${input('sell_fee_bps','参考卖出费率（基点）','','number','min="0" step="any" placeholder="1基点 = 0.01%" required')}${input('slippage_bps','参考滑点（基点）','','number','min="0" step="any" placeholder="无滑点假设填0" required')}</div><button type="submit">计算研究参考</button><p id="journalRuleComparisonStatus" class="jw-inline-status" role="status" aria-live="polite"></p></form><div id="journalRuleComparisonResult">${renderComparisonResult(state.comparison)}</div></details></section>`;
    const save=root.querySelector('[data-jw-action="save-check"]');if(save)save.disabled=!state.market||state.marketKey!==contextKey()||!state.market.preview_digest;
  }
  function formValues(form) {const values={};for(const el of Array.from(form.elements||[]))if(el.name)values[el.name]=String(el.value??'').trim();return values;}
  function numeric(value,label) {if(value===null||value===undefined||String(value).trim()===''||!Number.isFinite(Number(value)))throw new Error('请明确填写'+label+'，未知不能按 0 计算。');return Number(value);}
  function buildRulePayload(values) {
    const fee=numeric(values.sell_fee_bps,'参考卖出费率'),slippage=numeric(values.slippage_bps,'参考滑点');
    if(fee<0||slippage<0)throw new Error('卖出费率和滑点不能为负。');
    if(fee>=10000||slippage>=10000)throw new Error('卖出费率和滑点必须小于 10000 基点。');
    return {rule:{kind:'next_trading_day_close',sell_fee_bps:fee,slippage_bps:slippage}};
  }
  function buildConditionPayload(decision,values) {
    if(!decision?.decision_id||!Number.isInteger(decision.revision))throw new Error('请先选择一个已保存的操作记录。');
    if(!/^\d{4}-\d{2}-\d{2}$/.test(values.target_date||''))throw new Error('请明确在哪个交易日核对。');
    if(!values.baseline_fill_id)throw new Error('请先明确这条预期针对哪批买入。');
    if(!metricLabel[values.metric]||!['entry_price','previous_close'].includes(values.baseline)||!['>=','<='].includes(values.operator))throw new Error('核验条件选项无效，请先选择要看的价格。');
    const origin=values.expectation_origin||'recalled_plan';
    if(!['recalled_plan','post_hoc_review'].includes(origin))throw new Error('请选择这项目标从哪里来。');
    const previous=decision.expectation&&typeof decision.expectation==='object'&&!Array.isArray(decision.expectation)?decision.expectation:{};
    const expectation={...previous,metric:values.metric,baseline:values.baseline,operator:values.operator,target:numeric(values.target,'目标阈值'),target_date:values.target_date,text:values.original_text||'',baseline_fill_id:values.baseline_fill_id,scope_fill_id:values.baseline_fill_id,origin,origin_decision_ref:{decision_id:decision.decision_id,revision:decision.revision}};
    delete expectation.target_trade_date;delete expectation.trading_day_offset;
    const body={};
    for(const k of ['action','action_id','mode','mode_id','trigger','trigger_id','reason','observed_at','available_at','invalidation','holding_horizon','risk_constraints','reference_tickers','environment','emotion','cycle','role','author','note','strategy_id','mode_allocation','execution_difference','evidence','fill_ids','definition_refs'])if(decision[k]!==undefined)body[k]=decision[k];
    const oldScope=previous.scope_fill_id||previous.baseline_fill_id;
    const separate=oldScope&&oldScope!==values.baseline_fill_id||!list(decision.fill_ids).includes(values.baseline_fill_id);
    if(separate)body.fill_ids=[values.baseline_fill_id];
    if(body.definition_refs)body.definition_refs=list(body.definition_refs).filter(r=>!String(typeof r==='string'?r:r.id||'').startsWith('evidence.'));
    const revision_reason=values.revision_reason?.trim()||`补充${metricLabel[values.metric]}目标、核对日期${values.target_date}和买入批次`;
    return {...body,...(separate?{}:{decision_id:decision.decision_id,expected_revision:decision.revision}),revision_reason,evidence_class:origin==='post_hoc_review'?'historical_reconstruction':'retrospective_recall',expectation,verification_at:values.target_date+'T15:30:00+08:00',frozen_plan_ref:null,frozen_plan:null};
  }
  async function api(path,body) {
    const response=await global.fetch(BASE+path,{credentials:'same-origin',method:body?'POST':'GET',headers:{Accept:'application/json',...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
    let result;try{result=await response.json();}catch{throw new Error('服务未返回可读取的数据，请检查连接后重试。');}
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:result.detail?JSON.stringify(result.detail):'服务返回 '+response.status+'，请稍后重试。');return result;
  }
  function status(id,message,tone='ready') {const root=$(id);if(root){root.textContent=message;root.dataset.state=tone;}}
  async function busy(form,statusId,work) {
    if(state.busy)return;state.busy=true;form.setAttribute('aria-busy','true');const buttons=Array.from(form.querySelectorAll('button'));buttons.forEach(b=>b.disabled=true);status(statusId,'正在读取与核对…','loading');
    try{await work();}catch(error){if(form.isConnected!==false)status(statusId,error.message,'error');}finally{state.busy=false;form.setAttribute('aria-busy','false');buttons.forEach(b=>b.disabled=false);const save=$('journalWorkspaceVerification')?.querySelector('[data-jw-action="save-check"]');if(save)save.disabled=!state.market||state.marketKey!==contextKey()||!state.market.preview_digest;}
  }
  async function check(form) {
    const decision=chosenDecision();if(!decision)return;
    if(state.conditionDirty){status('journalMarketCheckStatus','目标已修改，请先保存核对目标，再读取行情。','error');return;}
    const episode=state.detail.episode.episode_id,key=contextKey(),body={decision_id:decision.decision_id,decision_revision:decision.revision};
    state.market=null;state.marketKey='';const resultNode=$('journalMarketCheckResult');if(resultNode)resultNode.innerHTML='';
    await busy(form,'journalMarketCheckStatus',async()=>{const result=await api('/episodes/'+encodeURIComponent(episode)+'/market-checks/preview',body);if(key!==contextKey())return;state.market=result;state.marketKey=key;$('journalMarketCheckResult').innerHTML=renderMarketResult(result);status('journalMarketCheckStatus','行情核验已读取；尚未保存。缺项按服务端证据保留。');});
  }
  async function saveCheck(button) {
    if(!state.market||state.marketKey!==contextKey()||!state.market.preview_digest)return;
    const form=$('journalMarketCheckForm'),decision=chosenDecision(),episode=state.detail.episode.episode_id,key=contextKey();
    const body={...state.market.preview_request,decision_id:decision.decision_id,decision_revision:decision.revision,preview_digest:state.market.preview_digest};
    await busy(form,'journalMarketCheckStatus',async()=>{await api('/episodes/'+encodeURIComponent(episode)+'/market-checks',body);if(key!==contextKey())return;await global.TradeJournal?.selectEpisode?.(episode,{focus:false});status('journalMarketCheckStatus','本次行情核验已保存，原预期版本和取证来源均保留。','success');});
  }
  async function saveCondition(form) {
    const decision=chosenDecision(),episode=state.detail?.episode?.episode_id,key=contextKey();if(!episode)return;
    let body;try{body=buildConditionPayload(decision,formValues(form));}catch(error){status('journalConditionStatus',error.message,'error');return;}
    await busy(form,'journalConditionStatus',async()=>{await api('/episodes/'+encodeURIComponent(episode)+'/decisions',body);if(key!==contextKey())return;state.conditionDirty=false;const saved=await global.TradeJournal?.selectEpisode?.(episode,{focus:false});const match=decisions().find(d=>d.expectation?.scope_fill_id===body.expectation.scope_fill_id&&d.expectation?.metric===body.expectation.metric&&d.expectation?.target_date===body.expectation.target_date&&d.expectation?.target===body.expectation.target);if(match){state.decisionKey=keyOf(match);state.scopeFillId=body.expectation.scope_fill_id;renderDetail();}status('journalConditionStatus','核对目标已保存。现在可以读取行情与成交；原记录仍可回查。','success');});
  }
  async function compare(form) {
    const episode=state.detail?.episode?.episode_id,key=contextKey();if(!episode)return;
    let body;try{body=buildRulePayload(formValues(form));}catch(error){status('journalRuleComparisonStatus',error.message,'error');return;}
    state.comparison=null;const resultNode=$('journalRuleComparisonResult');if(resultNode)resultNode.innerHTML='';
    await busy(form,'journalRuleComparisonStatus',async()=>{const result=await api('/episodes/'+encodeURIComponent(episode)+'/rule-comparison/preview',body);if(key!==contextKey())return;state.comparison=result;$('journalRuleComparisonResult').innerHTML=renderComparisonResult(result);status('journalRuleComparisonStatus','对照已计算；这是明确费用与滑点假设下的历史参考。');});
  }
  function filters() {const form=$('tradeJournalFilters');const out={};for(const k of ['query','start_date','end_date']){const v=form?.elements?.namedItem(k)?.value;if(v)out[k]=v;}return out;}
  async function loadOverviewTasks() {
    if(state.taskLoading)return;const seq=++state.taskGeneration;state.taskLoading=true;state.taskError='';renderOverviewDOM();
    try{const result=await api('/tasks?'+new URLSearchParams({...filters(),status:'open',limit:'5',offset:'0'}));if(seq===state.taskGeneration)state.tasks=result;}catch(error){if(seq===state.taskGeneration){state.tasks=null;state.taskError=error.message;}}finally{if(seq===state.taskGeneration){state.taskLoading=false;renderOverviewDOM();}}
  }
  async function processReview() {
    if(!state.detail?.episode){status('journalWorkspaceTaskStatus','先从名单选择一轮交易，再核对历史仓位与卖点。');return;}
    const episode=state.detail.episode.episode_id;showStage('archive');state.focusSection='process';applyFlow();renderOverviewDOM();
    await global.TradeJournal?.editProcessReview?.();if(state.detail?.episode?.episode_id!==episode)return;$('tradeJournalReviewEditor')?.scrollIntoView?.({block:'nearest'});
  }
  function setReviewProgress(progress) {
    if(!state.detail?.episode)return;
    state.detail.review_progress=progress;state.detail.episode.review_progress=progress;renderOverviewDOM();
  }
  function setEpisode(detail) {
    if(!detail)state.contextEpisode='';
    if(!detail&&['notes','check'].includes(state.stage)){state.resumeStage=state.stage;state.stage='pick';}
    if(detail?.episode&&detail.episode.episode_id!==state.flowEpisodeId){state.reviewContext=null;state.contextEpisode='';state.contextError='';state.scopeFillId='';state.conditionDirty=false;}
    state.detail=detail;state.generation++;state.market=null;state.marketKey='';state.comparison=null;
    const rows=decisions(detail);state.decisionKey=rows.some(d=>keyOf(d)===state.decisionKey)?state.decisionKey:rows.length?keyOf(rows[0]):'';
    if(detail?.episode){const same=state.flowEpisodeId===detail.episode.episode_id;state.stage=same&&state.resumeStage?state.resumeStage:'notes';if(!same||state.stage!=='notes')state.focusSection=null;state.flowEpisodeId=detail.episode.episode_id;state.resumeStage=null;state.archive=state.focusSection==='process';}
    renderDetail();applyFlow();renderOverviewDOM();if(detail?.episode)void loadReviewContext();
  }
  function enter() {applyFlow();renderOverviewDOM();if(state.detail?.episode?.episode_id!==global.TradeJournal?.state?.detail?.episode?.episode_id)setEpisode(global.TradeJournal?.state?.detail||null);else renderDetail();}
  function mount() {
    global.document?.addEventListener('submit',async event=>{const form=event.target;if(!['journalMarketCheckForm','journalConditionForm','journalRuleComparisonForm'].includes(form?.id))return;event.preventDefault();if(form.id==='journalMarketCheckForm')await check(form);if(form.id==='journalConditionForm')await saveCondition(form);if(form.id==='journalRuleComparisonForm')await compare(form);});
    global.document?.addEventListener('change',event=>{if(event.target?.closest?.('form')?.id==='tradeJournalFilters'){state.taskGeneration++;state.taskLoading=false;state.tasks=null;state.taskError='';renderOverviewDOM();}if(event.target?.name==='jw_decision'){state.decisionKey=event.target.value;state.scopeFillId='';state.conditionDirty=false;state.generation++;state.market=null;state.marketKey='';renderDetail();}if(event.target?.name==='jw_scope'){state.scopeFillId=event.target.value;state.conditionDirty=false;state.generation++;state.market=null;state.marketKey='';renderDetail();}if(event.target?.name==='metric'){const form=$('journalConditionForm'),price=event.target.value.endsWith('_price'),target=form?.elements?.namedItem('target');if(target?.parentElement?.firstChild)target.parentElement.firstChild.textContent=price?'目标股价（元）':'目标涨幅（%）';const timing=$('journalTimingHelp');if(timing)timing.textContent=targetTimingHelp(event.target.value)+' 这里只核对行情，不代表你在这个价格卖出了。';const help=$('journalTargetHelp');if(help)help.textContent=price?'填写具体股价，例如18表示18元。':'填写百分比，例如2表示相对基准上涨2%，不是2元。';}if(['sell_fee_bps','slippage_bps'].includes(event.target?.name)){state.comparison=null;const result=$('journalRuleComparisonResult');if(result)result.innerHTML='';status('journalRuleComparisonStatus','假设已变更，请重新计算对照。');}});
    global.document?.addEventListener('input',event=>{if(event.target?.closest?.('form')?.id==='journalConditionForm'){state.conditionDirty=true;state.market=null;state.marketKey='';const result=$('journalMarketCheckResult');if(result)result.innerHTML='';const save=$('journalWorkspaceVerification')?.querySelector('[data-jw-action="save-check"]');if(save)save.disabled=true;status('journalMarketCheckStatus','目标已修改，请先保存后再读取行情。');}});
    global.document?.addEventListener('click',async event=>{const button=event.target?.closest?.('[data-jw-action]');if(!button)return;const action=button.dataset.jwAction;if(['start','home','pick','notes','check','archive'].includes(action)){showStage(action==='start'?'pick':action);return;}if(action==='import'){dismissStatementPrompt();global.openJournalSection?.('tradeJournalSourceSection');}if(action==='continue-recorded'){dismissStatementPrompt();status('journalWorkspaceTaskStatus','继续按已导入记录复盘。后续买卖尚未确认，执行结论仍可能缺记录。');}if(action==='tasks')await loadOverviewTasks();if(action==='process')await processReview();if(action==='decision'){showStage('notes');global.TradeJournal?.editDecision?.();$('tradeJournalEditor')?.scrollIntoView?.({block:'nearest'});}if(action==='save-check')await saveCheck(button);if(action==='task'){await global.TradeJournal?.selectEpisode?.(button.dataset.episode);if(button.dataset.id){global.openJournalSection?.('tradeJournalTasksSection');await global.TradeJournal?.editTask?.(button.dataset.id);}}});
  }
  const exported={enter,setEpisode,setReviewProgress,showStage,openSection,loadOverviewTasks,renderOverview,renderCompletion,renderMarketResult,renderComparisonResult,buildRulePayload,buildConditionPayload,expectationText,renderContext,dismissStatementPrompt,scopedFill,escape,state};
  global.JournalWorkspace=exported;if(typeof module!=='undefined'&&module.exports)module.exports=exported;
  if(global.document)mount();
})(typeof window!=='undefined'?window:globalThis);
