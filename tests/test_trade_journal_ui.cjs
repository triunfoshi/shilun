/* VM contract checks exercise the shipped renderer without browser automation or live writes. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const crypto=require('node:crypto');
const events={},calls=[];
const node=()=>({innerHTML:'',textContent:'',value:'',dataset:{},hidden:false,attributes:{},setAttribute(k,v){this.attributes[k]=v;},querySelector(){return {disabled:false,focus(){},setAttribute(){}};},focus(){},addEventListener(){},showModal(){this.open=true;},close(){this.open=false;}});
const ids=['tradeJournalStatus','tradeJournalList','tradeJournalSummary','tradeJournalPagination','tradeJournalDetail','tradeJournalEditor','tradeJournalSaveStatus','tradeJournalDefinitions','tradeJournalDefinitionSearch','tradeJournalDefinitionBody','tradeJournalArtifact','tradeJournalArtifactTitle','tradeJournalArtifactBody','tradeJournalSourceStatus','tradeJournalSources','tradeJournalObservationStatus','tradeJournalObservationEditor','tradeJournalObservations','tradeJournalObservationSaveStatus','tradeJournalManualCheck','tradeJournalNumericCheck','tradeJournalPlanStatus','tradeJournalPlanEditor','tradeJournalPlans','tradeJournalPlanSaveStatus','tradeJournalBindEditor','tradeJournalBindStatus','tradeJournalContextEditor','tradeJournalContextResult','tradeJournalContextStatus','tradeJournalMethodStatus','tradeJournalMethodEditor','tradeJournalModeCards','tradeJournalHypotheses','tradeJournalMethodSaveStatus'];
const nodes=Object.fromEntries(ids.map(id=>[id,node()]));
for(const id of ['tradeJournalAllocation','tradeJournalAllocationEditor','tradeJournalAllocationStatus','tradeJournalAnalyticsStatus','tradeJournalAnalytics','tradeJournalPortfolioStatus','tradeJournalPortfolio','tradeJournalScenarioStatus','tradeJournalScenarioLegs','tradeJournalPortfolioHistory','tradeJournalPlanPortfolioStatus'])nodes[id]=node();
for(const id of ['tradeJournalTasksSection','tradeJournalTasks','tradeJournalTaskStatus','tradeJournalTaskEditor','tradeJournalTaskSaveStatus','tradeJournalTaskPagination','tradeJournalImportSection','tradeJournalImportStatus','tradeJournalImportMapping','tradeJournalImportPreview','tradeJournalImportReceipt'])nodes[id]=node();
for(const id of ['tradeJournalExecutionStatus','tradeJournalExecutionMatches','tradeJournalExecutionEditor','tradeJournalExecutionPreview'])nodes[id]=node();
for(const id of ['tradeJournalReviewStatus','tradeJournalReview','tradeJournalReviewEditor','tradeJournalReviewSaveStatus'])nodes[id]=node();
nodes.tradeJournalTaskFilters={elements:{namedItem:()=>({value:''})}};
const filterFields=Object.fromEntries(['query','start_date','end_date','status'].map(k=>[k,{name:k,value:''}]));
nodes.tradeJournalFilters={elements:{namedItem:k=>filterFields[k]},reset(){Object.values(filterFields).forEach(x=>x.value='');}};
const defs=['mode','trigger','action','evidence','qualification','expectation_check'].flatMap(category=>{
  const id={mode:'mode.custom_from_server',trigger:'trigger.server_shape',action:'action.buy',evidence:'evidence.retrospective_recall',qualification:'qualification.accounting',expectation_check:'expectation_check.supported'}[category];
  return [{id,category,label:'服务端_'+category,definition:'口径<&>',status:'research',formula:'分子 / 分母',time_basis:'截至决策时点',missing_policy:'缺数保留未知',sources:[{path:'docs/source.md'}],aliases:['别名检查'],parameters:{exact:1000}}];
});
defs.push({id:'observation_state.not_bought',category:'observation_state',label:'用户未买',definition:'仅此观察记录'},{id:'scope.strategy_cohort',category:'scope',label:'冻结候选范围'},{id:'scope.manual_watchlist',category:'scope',label:'个人观察名单'});
const episode={episode_id:'ep-1',ticker:'000636.SZ',code:'000636',name:'风华<script>',status:'open',start_date:'2026-10-23',quantity:1700,opening_quantity:0,opening_cost_status:'known',fees:81.39,realized_pnl:null,closed_pnl:null,remaining_cost:12345,buy_count:7,sell_count:0,qualification:{accounting:{eligible:true,reasons:[],definition_ref:'qualification.accounting'},ex_ante:{eligible:false,reasons:['补记不等于事前证据'],definition_ref:'qualification.ex_ante'}}};
const decision={decision_id:'d-1',revision:2,fill_ids:['f-1'],mode_id:'mode.custom_from_server',mode:'抱团原文',action_id:'action.buy',action:'买入',trigger_id:'trigger.server_shape',reason:'量能判断<&>',expectation:'周一反弹',invalidation:'继续跌条件未知',evidence_class:'retrospective_recall',recorded_at:'2026-10-26T01:00:00+08:00'};
const externalId='archive-note-reviewed-user-recollection-20261023-joy-sell-000000000000000000000000000001';
const externalDecision={...decision,decision_id:externalId,episode_id:'ep-external',revision:1,action:'腾资金卖出',reason:'为风华后续补仓腾资',expectation:'原版本目标'};
const detail={episode,fills:[{fill_id:'f-1',executed_at:'2026-10-23T09:36:00+08:00',side:'buy',quantity:1000,price:1.23,fees:2,net_cash:-1232,quantity_before:0,quantity_after:1000,source_ref:'页面整理',time_precision:'second'}],decisions:[decision],qualification:episode.qualification,missing_fields:['低开等待期限未知'],expectation_checks:[],links:[],live_references:[{plan_id:'old-plan'}]};
const observation={observation_id:'obs-1',scope:'strategy_cohort',state:'watching',name:'候选冻结',members:[{ticker:'000636.SZ',name:'风华'}],member_count:1,mode_research_ref:{analysis_date:'2026-10-23',version:'mode-research-v5',snapshot_id:'fixed-research',built_at:'2026-10-23T16:00:00+08:00'},denominator_note:'仅本固定队列'};
const preplan={plan_id:'pre-1',revision:2,revisions:[1,2],ticker:'000636.SZ',target_date:'2026-10-28',captured_at:'2026-10-27T08:00:00+08:00',complete:false,missing_fields:['risk_constraints'],ex_ante_verifiable:false,content_hash:'immutable-plan-hash',content:{ticker:'000636.SZ',name:'风华',mode_id:'mode.custom_from_server',trigger_id:'trigger.server_shape',reason:'首买前理由',expectation:{metric:'close',operator:'>=',target:10},verification_at:'2026-10-28T15:00:00+08:00',holding_horizon:'次日',reference_tickers:['000001.SZ']}};
const card={mode_id:'mode.user.ui-card',name:'用户待验证模式',revision:2,revisions:[1,2],rule_version:'mode-card-v2',status:'draft',missing_fields:['purpose'],captured_at:'2026-10-27T08:00:00+08:00',state_note:'状态不证明收益优势',content:{name:'用户待验证模式',status:'draft'}};
const hypothesis={hypothesis_id:'hypothesis.user.ui-h',revision:2,revisions:[1,2],question:'为什么部分交易超出计划',status:'draft',captured_at:'2026-10-27T08:00:00+08:00',preregistration:'incomplete',content:{question:'为什么部分交易超出计划',status:'draft',entry_rule_refs:[],exit_rule_refs:[],parameter_attempts:[{attempt_id:'old-a',content:{parameters:{old:1}}}],validation_results:[{result_id:'old-r',content:{outcome_type:'inconclusive',notes:'旧失败保留'}}]}};
let failList=false,failSave=false,total=1,allocationWire=null,analyticsWire=null,portfolioWire={},portfolioSnapshotWire=null,portfolioCaptureGate=null,sourcesWire=null,tasksWire=null,taskDetailWire=null,importInspectionWire=null,importParseWire=null,importLinkedParseWire=null,importReceiptWire=null,importInspectGate=null,importCommitGate=null,executionCandidatesWire=null,executionPreviewWire=null,executionHistoryWire=null,reviewWire=null;
const context={console,URLSearchParams,TextEncoder,Uint8Array,btoa:s=>Buffer.from(s,'binary').toString('base64'),crypto:crypto.webcrypto,location:{search:''},document:{getElementById:id=>nodes[id],addEventListener:(event,fn)=>events[event]=fn},fetch:async(url,options)=>{
  calls.push({url,options});
  if(options.method==='POST'&&url.endsWith('/review'))return {ok:true,json:async()=>({record:{revision:1,state:'confirmed'},review:reviewWire})};
  if(options.method==='POST'&&url.endsWith('/execution-matches/preview'))return {ok:true,json:async()=>executionPreviewWire};
  if(options.method==='POST'&&url.endsWith('/execution-matches'))return {ok:true,json:async()=>({record:{state:'confirmed',revision:1}})};
  if(options.method==='POST'&&url.endsWith('/import-files/inspect')){if(importInspectGate)await importInspectGate;return {ok:true,json:async()=>importInspectionWire};}
  if(options.method==='POST'&&url.endsWith('/import-files/parse'))return {ok:true,json:async()=>JSON.parse(options.body).identity_links?.length&&importLinkedParseWire?importLinkedParseWire:importParseWire};
  if(options.method==='POST'&&url.endsWith('/imports')){if(importCommitGate)await importCommitGate;return {ok:true,json:async()=>importReceiptWire};}
  if(options.method==='POST'&&url.includes('/tasks/')&&url.endsWith('/resolutions')){const b=JSON.parse(options.body),record={...b,revision:taskDetailWire.latest_revision+1,recorded_at:'2026-10-28T09:00:00+08:00'};taskDetailWire={...taskDetailWire,latest_revision:record.revision,task:{...taskDetailWire.task,revision:record.revision,state:b.action==='confirm_unknown'?'confirmed_unknown':b.action==='ignore'?'ignored':'open'},history:[...taskDetailWire.history,record]};tasksWire.items=tasksWire.items.map(t=>t.task_id===taskDetailWire.task.task_id?taskDetailWire.task:t);return {ok:true,json:async()=>({record,task:taskDetailWire.task,latest_revision:record.revision})};}
  if(options.method==='POST'&&url.endsWith('/allocations')){const body=JSON.parse(options.body),revision=(body.expected_revision||0)+1;return {ok:true,json:async()=>({record:{revision,assignments:body.assignments,labels:[],recorded_at:'2026-10-27T12:00:00+08:00'},allocation:allocationWire.allocation,revision})};}
  if(options.method==='POST'&&url.endsWith('/portfolio-context/preview'))return {ok:true,json:async()=>portfolioWire};
  if(options.method==='POST'&&url.endsWith('/portfolio-snapshots')){if(portfolioCaptureGate)await portfolioCaptureGate;return {ok:true,json:async()=>portfolioSnapshotWire};}
  if(options.method==='POST'&&url.endsWith('/mode-cards'))return {ok:true,json:async()=>({mode_id:card.mode_id,revision:2,record:card})};
  if(options.method==='POST'&&url.endsWith('/hypotheses'))return {ok:true,json:async()=>({hypothesis_id:hypothesis.hypothesis_id,revision:2,record:hypothesis})};
  if(options.method==='POST'&&url.endsWith('/plans'))return {ok:!failSave,status:failSave?409:200,json:async()=>failSave?{detail:'plan_revision_conflict'}:{plan_id:'pre-1',revision:3,record:{...preplan,revision:3},missing_fields:['risk_constraints'],ex_ante_verifiable:false}};
  if(options.method==='POST')return {ok:!failSave,status:failSave?409:200,json:async()=>failSave?{detail:'revision_conflict'}:{decision_id:'d-1',revision:3}};
  if(url.includes('/plans?'))return {ok:true,json:async()=>({items:[preplan,{...preplan,plan_id:'other-ticker',ticker:'000001.SZ'}],total:2})};
  if(url.includes('/plans/pre-1?'))return {ok:true,json:async()=>({...preplan,revision:Number(new URL(url,'http://localhost').searchParams.get('revision'))})};
  if(url.includes('/mode-cards?'))return {ok:true,json:async()=>({items:[card],total:1})};
  if(url.includes('/mode-cards/mode.user.ui-card?'))return {ok:true,json:async()=>({...card,revision:Number(new URL(url,'http://localhost').searchParams.get('revision'))})};
  if(url.includes('/hypotheses?'))return {ok:true,json:async()=>({items:[hypothesis],total:1})};
  if(url.includes('/hypotheses/hypothesis.user.ui-h?'))return {ok:true,json:async()=>({...hypothesis,revision:Number(new URL(url,'http://localhost').searchParams.get('revision'))})};
  if(url.endsWith('/allocations'))return {ok:true,json:async()=>allocationWire};
  if(url.endsWith('/allocations/history'))return {ok:true,json:async()=>({items:allocationWire?.record?[allocationWire.record]:[],total:0})};
  if(url.includes('/analytics?'))return {ok:true,json:async()=>analyticsWire};
  if(url.includes('/analytics/groups/'))return {ok:true,json:async()=>({...analyticsWire.groups[0],group_by:analyticsWire.group_by,limitations:analyticsWire.limitations})};
  if(url.includes('/portfolio-context?'))return {ok:true,json:async()=>portfolioWire};
  if(url.includes('/portfolio-snapshots?'))return {ok:true,json:async()=>({items:[portfolioSnapshotWire.record],total:1})};
  if(url.includes('/portfolio-snapshots/'))return {ok:true,json:async()=>portfolioSnapshotWire.record};
  if(url.endsWith('/definitions'))return {ok:true,json:async()=>({version:'registry-test',definitions:defs})};
  if(url.endsWith('/review/history'))return {ok:true,json:async()=>({items:reviewWire?.record?[reviewWire.record]:[]})};
  if(url.endsWith('/review'))return {ok:true,json:async()=>reviewWire};
  if(url.endsWith('/execution-matches'))return {ok:true,json:async()=>executionCandidatesWire};
  if(url.includes('/execution-matches/'))return {ok:true,json:async()=>executionHistoryWire};
  if(url.includes('/tasks?'))return {ok:true,json:async()=>tasksWire};
  if(url.includes('/tasks/'))return {ok:true,json:async()=>taskDetailWire};
  if(url.endsWith('/summary'))return {ok:true,json:async()=>({counts:{episodes:1},coverage:{broker:1},missing_fields:[]})};
  if(url.includes('/sources?'))return {ok:true,json:async()=>sourcesWire||({items:[{source_id:'src-1',filename:'原件.txt',bytes:2048,sha256:'a'.repeat(64),source_type:'broker_page_archive',recorded_at:'2026-10-26T00:00:00+08:00'}],total:1})};
  if(url.endsWith('/sources/src-1'))return {ok:true,json:async()=>({source_id:'src-1',filename:'原件.txt',sha256:'abc',content_encoding:'utf-8'})};
  if(url.includes('/batches?'))return {ok:true,json:async()=>({items:[{batch_id:'batch-1',status:'committed',source_ids:['src-1']}],total:1})};
  if(url.includes('/observations?'))return {ok:true,json:async()=>({items:[observation],total:1})};
  if(url.endsWith('/observations/obs-1'))return {ok:true,json:async()=>({...observation,members:null,snapshot_ref:{snapshot_id:'cohort-1'}})};
  if(url.includes('/snapshots/cohort-1?'))return {ok:true,json:async()=>({snapshot_id:'cohort-1',content:{daily:{members:[{ticker:'000636.SZ',name:'风华'}]}}})};
  if(url.includes('/context?'))return {ok:true,json:async()=>({plan_fit:{status:'unknown',adoption_verified:false,reasons:['沿用原评估']},counters:[{scope:'market',text:'市场反证',source_ref:{snapshot_id:'fixed-research'}}],market_events:[],role_events:[],source_refs:{research:observation.mode_research_ref},gaps:['题材归档缺失'],input_storage:{research:{ecs_archive_verified:false}}})};
  if(url.includes('/episodes?'))return {ok:!failList,status:503,json:async()=>failList?{detail:'ECS Mongo不可用'}:{items:total?[episode]:[],total,limit:30,offset:0}};
  if(url.endsWith('/decisions/'+externalId+'/history'))return {ok:true,json:async()=>({episode_id:'ep-external',items:[externalDecision,{...externalDecision,revision:2,expectation:'后来改变的目标'}]})};
  if(url.endsWith('/episodes/ep-external'))return {ok:true,json:async()=>({episode:{episode_id:'ep-external',ticker:'002242.SZ',name:'九阳'},decisions:[externalDecision]})};
  if(url.endsWith('/history'))return {ok:true,json:async()=>({items:[{...decision,revision:1,expectation:'原预期'}]})};
  return {ok:true,json:async()=>detail};
}};
context.window=context;vm.createContext(context);vm.runInContext(fs.readFileSync('shilun/static/trade-journal.js','utf8'),context);
function form(values,dataset={}){
  const elements=Object.entries(values).map(([name,value])=>({name,value,setAttribute(){}}));elements.namedItem=k=>elements.find(x=>x.name===k);
  const button={disabled:false,textContent:'保存',attributes:{},setAttribute(k,v){this.attributes[k]=v;}};
  return {elements,dataset,attributes:{},button,setAttribute(k,v){this.attributes[k]=v;},querySelector(){return button;}};
}
const withoutRaw=html=>html.replace(/<details class="tj-raw">[\s\S]*?<\/details>/g,'');
const rawRecords=html=>Array.from(html.matchAll(/<pre class="tj-raw-json">([\s\S]*?)<\/pre>/g),m=>JSON.parse(m[1].replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&')));
function assertNoNestedForms(html){let depth=0;for(const m of html.matchAll(/<\/?form(?:\s|>)/g)){depth+=m[0].startsWith('</')?-1:1;assert(depth>=0&&depth<=1,'HTML form must not nest or close another form');}assert.equal(depth,0);}
(async()=>{
  const ui=context.TradeJournal;
  filterFields.query.value='风华';filterFields.start_date.value='2026-10-19';filterFields.end_date.value='2026-10-23';
  await ui.load();
  assert(calls.some(c=>c.url.includes('start_date=2026-10-19')&&c.url.includes('end_date=2026-10-23')));
  assert(calls.every(c=>!c.options.method),'enter is read-only');
  assert(nodes.tradeJournalList.innerHTML.includes('风华&lt;script&gt;'));
  assert(!nodes.tradeJournalList.innerHTML.includes('风华<script>'));
  assert(nodes.tradeJournalList.innerHTML.includes('已实现盈亏（元）'));assert(/<td[^>]*>—<\/td>/.test(nodes.tradeJournalList.innerHTML),'missing PnL must stay unknown');
  await ui.selectEpisode('ep-1');
  let html=nodes.tradeJournalDetail.innerHTML;
  for(const str of ['逐次成交','服务端_mode','服务端_trigger','抱团原文','低开等待期限未知','补记不等于事前证据','old-plan','d-1'])assert(html.includes(str),str);
  assert(html.includes('量能判断&lt;&amp;&gt;'));
  assert(!html.includes('/trading/executions'));
  // Main reading layer is human text; every complete original object remains recoverable.
  const savedDecision=JSON.parse(JSON.stringify(decision)),savedSource=detail.fills[0].source_ref;
  Object.assign(decision,{expectation:{text:'周一一字涨停，在板上出货',target_trade_date:'2026-10-26'},verification_at:null,risk_constraints:{planned_position_ceiling:{type:'account_position_fraction_ceiling',value:1,single_security:true,raw:'全仓满仓单调',planned_ceiling_not_actual_weight:true},change_date:'2026-10-22',max_loss_budget:null,scope:'用户本轮计划；不是全局纪律'},evidence:[{text:'用户事后回忆',source_ref:{source_id:'a-very-long-content-hash',record_id:'decisions[1]'}}],note:{remaining_gaps:['盘中量能比较口径待补']}});
  detail.fills[0].source_ref={source_id:'src-1',row:3,sha256:'hash-must-only-be-folded',nested:{original:'完整来源字段'}};
  detail.links=[{from_decision_id:externalId,from_revision:1,to_decision_id:'d-1',to_revision:2,relation:'跨票资金调度',reason:'为后续风华补仓腾资'}];
  ui.renderDetail();html=nodes.tradeJournalDetail.innerHTML;const main=withoutRaw(html);
  for(const summary of ['周一一字涨停，在板上出货','目标交易日','2026-10-26','全仓满仓单调','100.00%','计划上限，不代表实际仓位','上限变更日','整轮最大亏损预算','盘中量能比较口径待补','查看来源原件 · 第 3 行'])assert(main.includes(summary),summary);
  for(const internal of ['target_trade_date','planned_position_ceiling','hash-must-only-be-folded','完整来源字段','a-very-long-content-hash'])assert(!main.includes(internal),internal+' must be folded');
  assert(main.includes('<dt>验证时点</dt><dd>待补</dd>'),'target trade date cannot fill a missing verification time');
  detail.fills[0].time_kind='execution_time';detail.fills[0].statement_event_time='2026-10-23T09:38:00+08:00';ui.renderDetail();let timeMain=withoutRaw(nodes.tradeJournalDetail.innerHTML);assert(timeMain.includes('撮合成交时间'));assert(timeMain.includes('交割单发生时间：2026-10-23T09:38:00+08:00'));delete detail.fills[0].time_kind;delete detail.fills[0].statement_event_time;
  detail.fills[0].time_precision='statement_event_time';ui.renderDetail();assert(withoutRaw(nodes.tradeJournalDetail.innerHTML).includes('交割单发生时间'));assert(!withoutRaw(nodes.tradeJournalDetail.innerHTML).includes('撮合成交时间'));detail.fills[0].time_precision='second';
  const recovered=rawRecords(html);assert.deepEqual(recovered.find(r=>r.decision_id==='d-1').risk_constraints,decision.risk_constraints);assert.equal(recovered.find(r=>r.fill_id==='f-1').source_ref.nested.original,'完整来源字段');
  assert(main.includes('data-tj-action="linked-decision"'));assert(main.includes('data-revision="1"'));assert(!main.replace(/<[^>]+>/g,'').includes(externalId),'full endpoint ID must not stretch the table');
  await ui.showLinkedDecision(externalId,1);assert(withoutRaw(nodes.tradeJournalArtifactBody.innerHTML).includes('九阳'));assert(withoutRaw(nodes.tradeJournalArtifactBody.innerHTML).includes('原版本目标'));assert(!nodes.tradeJournalArtifactBody.innerHTML.includes('后来改变的目标'));assert(!nodes.tradeJournalArtifactBody.innerHTML.includes('data-tj-action="edit"'),'linked decision is read-only');
  await ui.showLinkedDecision(externalId,99);assert(nodes.tradeJournalArtifactBody.innerHTML.includes('未以最新版本替代'));
  for(const key of Object.keys(decision))delete decision[key];Object.assign(decision,savedDecision);detail.fills[0].source_ref=savedSource;detail.links=[];ui.renderDetail();
  ui.editDecision('d-1');
  assert(nodes.tradeJournalEditor.innerHTML.includes('mode.custom_from_server'));
  assert(!nodes.tradeJournalEditor.innerHTML.includes('mode.ma5_trend'),'mode options come only from registry');
  assert(!nodes.tradeJournalEditor.innerHTML.includes('name="evidence_class"'),'a recall form cannot offer an ex-ante status menu');
  const values={reason:'补记理由',revision_reason:'补上失效条件',action_id:'action.buy',action:'用户原文动作',mode_id:'mode.custom_from_server',mode:'模式原文',trigger_id:'trigger.server_shape',trigger:'触发原文',evidence_class:'retrospective_recall',observed_at:'',available_at:'',expectation:'周一',invalidation:'未知',plan_id:'dated-plan',plan_reference:'v1',evidence:'用户事后补记',reference_tickers:'000001.SZ, 399001.SZ'};
  const edit=form(values,{decision:'d-1',revision:'2',fills:'["f-1"]'});
  await ui.saveDecision(edit);
  const write=calls.find(c=>c.options.method==='POST');const body=JSON.parse(write.options.body);
  assert.equal(write.url,'/api/v1/trade-journal/episodes/ep-1/decisions');
  assert.equal(body.expected_revision,2);assert.equal(body.mode,'模式原文');assert.equal(body.mode_id,'mode.custom_from_server');assert.deepEqual(body.fill_ids,['f-1']);
  assert(body.definition_refs.includes('evidence.retrospective_recall'));assert(!('available_at' in body));assert.equal(body.frozen_plan.plan_id,'dated-plan');
  assert.equal(body.evidence[0].content,'用户事后补记');assert.equal(body.evidence[0].evidence_class,'retrospective_recall');assert.deepEqual(body.reference_tickers,['000001.SZ','399001.SZ']);
  assert(nodes.tradeJournalStatus.textContent.includes('原记录保留'));
  const beforeDefault=calls.filter(c=>c.options.method==='POST').length;await ui.saveDecision(form({...values,revision_reason:''},{decision:'d-1',revision:'2',fills:'[]'}));assert.equal(calls.filter(c=>c.options.method==='POST').length,beforeDefault+1);assert.equal(JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body).revision_reason,'补充交易复盘内容');
  const checkForm=form({status:'supported',actual:'实际观察',evidence:'归档证据'},{decision:'d-1'});checkForm.id='tradeJournalCheckForm';await events.submit({target:checkForm,preventDefault(){}});
  const checkCall=calls.filter(c=>c.options.method==='POST').at(-1);const checkBody=JSON.parse(checkCall.options.body);assert.equal(checkBody.status,'supported');assert.equal(checkBody.evaluation_class,'manual_assessment');assert(Array.isArray(checkBody.evidence));assert(!('comparison' in checkBody));
  ui.checkEditor('d-1');assert(nodes.tradeJournalEditor.innerHTML.includes('numeric_comparison'));assert(nodes.tradeJournalEditor.innerHTML.includes('原件JSON字段路径'));
  const numericForm=form({evaluation_class:'numeric_comparison',status:'supported',actual_source_id:'src-1',value_path:'items.0.close',actual_metric:'close',actual_value:'0',actual_observed_at:'2026-10-26T15:00:00+08:00',actual_available_at:'2026-10-26T15:01:00+08:00',evidence:'原件数值'},{decision:'d-1',revision:'2'});numericForm.id='tradeJournalCheckForm';await events.submit({target:numericForm,preventDefault(){}});
  const numericBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.equal(numericBody.evaluation_class,'numeric_comparison');assert.equal(numericBody.status,'unknown','a typed status cannot bypass server comparison');assert.equal(numericBody.actual.value,0);assert.equal(numericBody.decision_revision,2);assert.equal(numericBody.actual.source_ref.source_id,'src-1');
  events.change({target:{id:'tradeJournalCheckClass',value:'numeric_comparison'}});assert.equal(nodes.tradeJournalManualCheck.hidden,true);assert.equal(nodes.tradeJournalNumericCheck.hidden,false);
  failSave=true;await ui.saveDecision(edit);assert(nodes.tradeJournalSaveStatus.textContent.includes('revision_conflict'));assert.equal(edit.attributes['aria-busy'],'false');failSave=false;
  ui.renderDefinitions('time');assert(nodes.tradeJournalDefinitionBody.innerHTML.includes('registry-test'));
  ui.renderDefinitions('mode.custom_from_server');assert(nodes.tradeJournalDefinitionBody.innerHTML.includes('缺数保留未知'));assert(nodes.tradeJournalDefinitionBody.innerHTML.includes('docs/source.md'));assert(!nodes.tradeJournalDefinitionBody.innerHTML.includes('口径<&>'));assert(nodes.tradeJournalDefinitionBody.innerHTML.includes('别名检查'));assert(nodes.tradeJournalDefinitionBody.innerHTML.includes('1000'));
  episode.unknown_fees=true;episode.fees=0;episode.opening_cost_status='unknown';episode.remaining_cost=null;ui.renderDetail();assert(nodes.tradeJournalDetail.innerHTML.includes('—（费用待核）'));assert(nodes.tradeJournalDetail.innerHTML.includes('—（成本待核）'));episode.unknown_fees=false;
  detail.fills[0].source_ref={source_id:'src-1',row:3};decision.frozen_plan={plan_id:'fixed-plan',content:{mode:'原计划内容'},content_hash:'exact-hash'};ui.renderDetail();assert(nodes.tradeJournalDetail.innerHTML.includes('查看来源原件'));assert(nodes.tradeJournalDetail.innerHTML.includes('查看冻结预案'));
  ui.showPlan('d-1');assert(nodes.tradeJournalArtifactBody.innerHTML.includes('exact-hash'));assert(nodes.tradeJournalArtifactBody.innerHTML.includes('原计划内容'));assert.equal(nodes.tradeJournalArtifact.open,true);
  await ui.loadSources();assert(nodes.tradeJournalSources.innerHTML.includes('原件.txt'));assert(nodes.tradeJournalSources.innerHTML.includes('batch-1'));assert(withoutRaw(nodes.tradeJournalSources.innerHTML).includes('2,048 字节'));assert(!withoutRaw(nodes.tradeJournalSources.innerHTML).replace(/<[^>]+>/g,'').includes('a'.repeat(64)));assert.equal(rawRecords(nodes.tradeJournalSources.innerHTML)[0].sha256,'a'.repeat(64));await ui.showSource('src-1');assert(nodes.tradeJournalArtifactBody.innerHTML.includes('下载已归档原件'));
  await ui.loadObservations();assert(nodes.tradeJournalObservations.innerHTML.includes('仅本固定队列'));assert(nodes.tradeJournalObservations.innerHTML.includes('snapshot=fixed-research'));
  nodes['tj-observation-members-obs-1']=node();await ui.loadObservationMembers('obs-1');assert(nodes['tj-observation-members-obs-1'].innerHTML.includes('风华'));assert(calls.some(c=>c.url.endsWith('/snapshots/cohort-1?include_content=true')));
  ui.previewResearchObservation();assert(nodes.tradeJournalObservationEditor.innerHTML.includes('尚未读取模式收益'));
  const members=[{ticker:'000636.SZ',name:'风华',opening:2.1},{ticker:'000001.SZ',name:'平安',opening:null}];
  context.TradeDecision={state:{evalWindow:'5',mode:'trend',research:{...observation.mode_research_ref,assessments:{windows:{'5':{trend:{key:'trend',name:'首板样本',daily:[{date:'2026-10-26',formation_date:'2026-10-23',members}]}}}}}}};
  ui.previewResearchObservation();assert(nodes.tradeJournalObservationEditor.innerHTML.includes('原样保留 2 个成员'));assert(nodes.tradeJournalObservationEditor.innerHTML.includes('历史研究重建'));assert.equal(ui.state.observationDraft.scope,'strategy_cohort');
  const obsForm=form({reason:'固定名单用于未买对照'});await ui.saveObservation(obsForm);const cohortCall=calls.filter(c=>c.options.method==='POST').at(-1),cohortBody=JSON.parse(cohortCall.options.body);assert(cohortCall.url.endsWith('/observations'));assert.deepEqual(cohortBody.members,members);assert.equal(cohortBody.source_ref.mode_key,'trend');assert.equal(cohortBody.source_ref.window,'5');assert.equal(cohortBody.mode_research_ref.snapshot_id,'fixed-research');assert(!('observed_at' in cohortBody));assert.equal(cohortBody.evidence_class,'historical_reconstruction');
  ui.observationStatusEditor('obs-1','000636.SZ');assert(nodes.tradeJournalObservationEditor.innerHTML.includes('用户未买'));assert.equal(ui.state.observationDraft.scope,'manual_watchlist');const notBought=form({state:'not_bought',reason:'未达实际触发条件'});await ui.saveObservation(notBought);const notBoughtBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.equal(notBoughtBody.parent_observation_id,'obs-1');assert.equal(notBoughtBody.state,'not_bought');assert(!('price' in notBoughtBody));assert(!('return' in notBoughtBody));assert(!('members' in notBoughtBody));assert(!('mode_research_ref' in notBoughtBody),'personal state must not claim a full research denominator');
  await ui.loadPlans();assert(nodes.tradeJournalPlans.innerHTML.includes('查看版本 1'));assert(nodes.tradeJournalPlans.innerHTML.includes('查看版本 2'));assert(nodes.tradeJournalPlans.innerHTML.includes('风险预算与边界'));
  ui.state.detail=null;ui.state.selected='';await ui.editPreplan();assert(nodes.tradeJournalPlanEditor.innerHTML.includes('新建交易前预案'));assert(!nodes.tradeJournalPlanEditor.innerHTML.includes('name="captured_at"'));assert(!nodes.tradeJournalPlanEditor.innerHTML.includes('name="evidence_class"'));
  const preValues={ticker:'000636.SZ',name:'风华',target_date:'2026-10-28',mode_id:'',mode:'',trigger_id:'',trigger:'',reason:'待进一步完善的前瞻意图',expectation:'',verification_at:'',invalidation:'',holding_horizon:'',risk_constraints:'',reference_tickers:'',evidence:'当前来源原文',evidence_source_id:'src-1',evidence_row:'3',evidence_observed_at:'2026-10-27T07:55:00+08:00',evidence_available_at:'2026-10-27T07:56:00+08:00',captured_at:'1999-01-01T00:00:00+08:00'};
  await ui.savePreplan(form(preValues,{plan:'',revision:''}));const preCall=calls.filter(c=>c.options.method==='POST').at(-1),preBody=JSON.parse(preCall.options.body);assert(preCall.url.endsWith('/plans'));assert(!('episode_id' in preBody));assert(!('fill_ids' in preBody));assert(!('captured_at' in preBody));assert.equal(preBody.mode_id,'');assert.equal(preBody.evidence[0].source_ref.row,3);assert.equal(preBody.evidence[0].evidence_class,'contemporaneous');assert(nodes.tradeJournalPlanStatus.textContent.includes('仍有事前证据或条件缺项'));assert(nodes.tradeJournalArtifactBody.innerHTML.includes('immutable-plan-hash'));
  assert(!('sources' in preBody),'empty observation original must not manufacture a source');
  const rawObservation='  用户实际观察原文\n保留原始换行与空格  ';
  await ui.savePreplan(form({...preValues,evidence_source_id:'',observation_source_text:rawObservation},{plan:'',revision:''}));const sourcePlanBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body),sourceHash=crypto.createHash('sha256').update(rawObservation).digest('hex');assert.equal(sourcePlanBody.sources[0].content,rawObservation);assert.equal(sourcePlanBody.sources[0].sha256,sourceHash);assert.equal(sourcePlanBody.sources[0].source_id,'src-'+sourceHash);assert.equal(sourcePlanBody.evidence[0].source_ref.source_id,'src-'+sourceHash);assert(!('recorded_at' in sourcePlanBody.sources[0]));assert.equal(sourcePlanBody.sources[0].metadata.provenance_class,'manual_observation');
  await ui.editPreplan('pre-1',2);assert(nodes.tradeJournalPlanEditor.innerHTML.includes('追加独立预案版本'));assert(calls.some(c=>c.url.endsWith('/plans/pre-1?revision=2')));const writesBefore=calls.filter(c=>c.options.method==='POST').length;await ui.savePreplan(form(preValues,{plan:'pre-1',revision:'2'}));assert.equal(calls.filter(c=>c.options.method==='POST').length,writesBefore);assert(nodes.tradeJournalPlanSaveStatus.textContent.includes('修订原因'));
  await ui.savePreplan(form({...preValues,revision_reason:'明确当前仍未知条件'},{plan:'pre-1',revision:'2'}));const revisedBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.equal(revisedBody.expected_revision,2);assert.equal(revisedBody.verification_at,null,'clearing a prior condition remains explicit, not inherited');
  ui.state.detail=detail;ui.state.selected='ep-1';await ui.bindPlanEditor();assert(nodes.tradeJournalBindEditor.innerHTML.includes('pre-1'));assert(!nodes.tradeJournalBindEditor.innerHTML.includes('other-ticker'));assert(nodes.tradeJournalBindEditor.innerHTML.includes('请选择预案与版本'));assert(nodes.tradeJournalBindEditor.innerHTML.includes('name="fill_ids"'));
  const bindForm=form({plan_choice:JSON.stringify({plan_id:'pre-1',revision:1}),reason:'关联原版本'},{episode:'ep-1'});bindForm.elements.push({name:'fill_ids',value:'f-1',checked:true});await ui.savePlanBinding(bindForm);const boundCall=calls.filter(c=>c.options.method==='POST').at(-1),boundBody=JSON.parse(boundCall.options.body);assert(boundCall.url.endsWith('/plans/pre-1/bind'));assert.equal(boundBody.plan_revision,1);assert.deepEqual(boundBody.fill_ids,['f-1']);assert.equal(boundBody.episode_id,'ep-1');assert(calls.some(c=>c.url.endsWith('/plans/pre-1?revision=1')));
  ui.contextEditor();assert(nodes.tradeJournalContextEditor.innerHTML.includes('fixed-research'));assert(nodes.tradeJournalContextEditor.innerHTML.includes('不能证明历史成交当时已知'));
  const contextForm=form({decision_choice:JSON.stringify({decision_id:'d-1',decision_revision:2}),mode_key:'trend',window:'5',previous_analysis_date:'',previous_snapshot_id:''},{episode:'ep-1',date:'2026-10-23',snapshot:'fixed-research'});await ui.loadContext(contextForm);assert(nodes.tradeJournalContextResult.innerHTML.includes('市场反证'));assert(nodes.tradeJournalContextResult.innerHTML.includes('题材归档缺失'));assert(nodes.tradeJournalContextResult.innerHTML.includes('ecs_archive_verified'));assert(calls.some(c=>c.url.includes('/context?')&&c.url.includes('snapshot_id=fixed-research')&&c.url.includes('decision_revision=2')));
  await ui.loadMethods();assert(nodes.tradeJournalModeCards.innerHTML.includes('用户待验证模式'));assert(nodes.tradeJournalHypotheses.innerHTML.includes('为什么部分交易'));await ui.editModeCard();assert(nodes.tradeJournalMethodEditor.innerHTML.includes('入场触发（与交易模式分开）'));assert(nodes.tradeJournalMethodEditor.innerHTML.includes('未绑定机器策略'));
  card.content={...card.content,purpose:'检验抱团股超跌后次日修复',entry_triggers:'持续下跌后未放量',counter_evidence:'低开继续走弱',holding_horizon:'次日',risk_constraints:'预算未知先保留'};card.advantage_verified=false;card.content_hash='mode-exact-hash';
  await ui.viewMethod('mode',card.mode_id,1);const modeMain=withoutRaw(nodes.tradeJournalArtifactBody.innerHTML);for(const value of ['模式名称','草稿','模式用途','检验抱团股超跌后次日修复','反证与已知失败','未绑定机器策略','未验证；使用状态不证明收益优势'])assert(modeMain.includes(value),value);assert(!modeMain.includes('advantage_verified'));assert(!modeMain.includes('mode-exact-hash'));assert(!modeMain.includes('"content"'));assert.equal(rawRecords(nodes.tradeJournalArtifactBody.innerHTML)[0].content_hash,'mode-exact-hash');
  hypothesis.content.development_window={start:'2026-09-01',end:'2026-09-30'};hypothesis.content.validation_window={start:'2026-10-01',end:'2026-10-23'};hypothesis.content.universe='固定候选全集';hypothesis.content.validation_results[0].content.notes='旧失败保留';hypothesis.preregistration='retrospective';
  await ui.viewMethod('hypothesis',hypothesis.hypothesis_id,2);const hypothesisMain=withoutRaw(nodes.tradeJournalArtifactBody.innerHTML);for(const value of ['研究问题','开发样本区间','2026-09-01 → 2026-09-30','事后登记','固定候选全集','旧失败保留','无结论 / 缺证据'])assert(hypothesisMain.includes(value),value);assert(!hypothesisMain.includes('protocol_digest'));assert.equal(rawRecords(nodes.tradeJournalArtifactBody.innerHTML)[0].content.validation_results[0].attempt_id,undefined);assert.equal(rawRecords(nodes.tradeJournalArtifactBody.innerHTML)[0].content.validation_results[0].result_id,'old-r');
  await ui.saveMethod(form({name:'UI创建的待验证模式',status:'draft',purpose:'',strategy_binding:''},{id:'',revision:''}),'mode');const cardBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.equal(cardBody.strategy_id,null);assert.equal(cardBody.strategy_version,null);assert(!('advantage_verified' in cardBody));
  await ui.editHypothesis();assert(nodes.tradeJournalMethodEditor.innerHTML.includes('旧项只读保留'));assert(nodes.tradeJournalMethodEditor.innerHTML.includes('观察反馈'));const hypothesisValues={question:'胜负之外需要检验什么',status:'draft',development_start:'',development_end:'',validation_start:'',validation_end:'',entry_rule_refs:'[]',exit_rule_refs:'[]',mode_ref:'',attempt_parameters:'{"threshold":5}',attempt_reason:'记录第一次尝试',attempt_data_refs:'[]',outcome_type:'inconclusive',winning_count:'',result_notes:'暂缺可比较数据'};await ui.saveMethod(form(hypothesisValues,{id:'',revision:''}),'hypothesis');const hypoBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.equal(hypoBody.validation_results[0].outcome_type,'inconclusive');assert.deepEqual(hypoBody.parameter_attempts[0].parameters,{threshold:5});assert(!JSON.stringify(hypoBody).includes('old-a'));assert(!('protocol_registered_at' in hypoBody));
  const beforeBadReport=calls.filter(c=>c.options.method==='POST').length;await ui.saveMethod(form({...hypothesisValues,outcome_type:'observation',winning_count:'2',sample_count:'3'},{id:'',revision:''}),'hypothesis');assert.equal(calls.filter(c=>c.options.method==='POST').length,beforeBadReport);assert(nodes.tradeJournalMethodSaveStatus.textContent.includes('不能填交易获利数'));
  ui.state.definitions.push({id:'mode.user.exact',label:'精确自建模式',category:'mode',rule_version:'mode-card-v7'});await ui.savePreplan(form({...preValues,mode_id:'mode.user.exact'},{plan:'',revision:''}));const dynamicBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.deepEqual(dynamicBody.definition_refs,[{id:'mode.user.exact',rule_version:'mode-card-v7'}]);
  // Editing a reason cannot silently substitute catalogue v2 for a frozen v1.
  const oldMode='mode.user.history',oldDefinition={id:oldMode,label:'原定义',category:'mode',rule_version:'mode-card-v1'};
  ui.state.definitions.push({...oldDefinition,label:'当前新定义',rule_version:'mode-card-v2'});decision.mode_id=oldMode;decision.definition_snapshots=[oldDefinition];ui.state.detail=detail;ui.state.selected='ep-1';ui.editDecision('d-1');assert(nodes.tradeJournalEditor.innerHTML.includes('原冻结版本'));assert(nodes.tradeJournalEditor.innerHTML.includes('mode-card-v1'));assert(nodes.tradeJournalEditor.innerHTML.includes('mode-card-v2'));
  await ui.saveDecision(form({...values,mode_id:JSON.stringify({id:oldMode,rule_version:'mode-card-v1'}),revision_reason:'只补理由'},{decision:'d-1',revision:'2',fills:'["f-1"]',originalMode:oldMode,definitionSnapshots:JSON.stringify([oldDefinition])}));const oldDecisionBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert(oldDecisionBody.definition_refs.some(r=>r.id===oldMode&&r.rule_version==='mode-card-v1'));assert.equal(oldDecisionBody.mode_id,oldMode);
  preplan.content.mode_id=oldMode;preplan.definition_snapshots=[oldDefinition];await ui.editPreplan('pre-1',2);assert(nodes.tradeJournalPlanEditor.innerHTML.includes('原冻结版本'));await ui.savePreplan(form({...preValues,mode_id:oldMode,revision_reason:'仅完善理由'},{plan:'pre-1',revision:'2',originalMode:oldMode,definitionSnapshots:JSON.stringify([oldDefinition])}));const oldPlanBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.deepEqual(oldPlanBody.definition_refs,[{id:oldMode,rule_version:'mode-card-v1'}]);
  hypothesis.content.mode_ref={mode_id:'mode.user.archived',revision:1};ui.state.modeCards=[];await ui.editHypothesis('hypothesis.user.ui-h',2);assert(nodes.tradeJournalMethodEditor.innerHTML.includes('原冻结模式引用 · mode.user.archived'));assert(nodes.tradeJournalMethodEditor.innerHTML.includes('&quot;mode_id&quot;:&quot;mode.user.archived&quot;,&quot;revision&quot;:1}'));await ui.saveMethod(form({...hypothesisValues,mode_ref:JSON.stringify(hypothesis.content.mode_ref),revision_reason:'只补问题说明'},{id:hypothesis.hypothesis_id,revision:'2'}),'hypothesis');const retainedModeBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.deepEqual(retainedModeBody.mode_ref,{mode_id:'mode.user.archived',revision:1});
  nodes['tj-history-d-1']=node();await ui.showHistory('d-1');assert(nodes['tj-history-d-1'].innerHTML.includes('原预期'));
  filterFields.start_date.value='2026-10-24';const before=calls.length;await ui.load();assert.equal(calls.length,before);assert(nodes.tradeJournalStatus.textContent.includes('开始日期晚于'));
  filterFields.start_date.value='';failList=true;await ui.load();assert(nodes.tradeJournalList.innerHTML.includes('ECS Mongo不可用'));assert(nodes.tradeJournalList.innerHTML.includes('重新读取'));assert.equal(nodes.tradeJournalList.attributes['aria-busy'],'false');
  failList=false;total=0;await ui.load();assert(nodes.tradeJournalList.innerHTML.includes('筛选范围内没有库存轮'));assert(!nodes.tradeJournalList.innerHTML.includes('ECS Mongo不可用'));
  const htmlSource=fs.readFileSync('shilun/static/index.html','utf8');
  assert(htmlSource.indexOf('nav-trade-journal')<htmlSource.indexOf('data-nav-group="research"'),'entry belongs to review domain');
  assert(htmlSource.includes('aria-controls="tab-trade-journal"'));assert(htmlSource.includes('/static/trade-journal.js'));
  const css=fs.readFileSync('shilun/static/trade-journal.css','utf8');assert(css.includes('prefers-reduced-motion'));assert(css.includes('minmax(0,1fr)'));assert(!css.includes('transition: all'));
  assert(calls.filter(c=>c.options.method==='POST').every(c=>c.url.startsWith('/api/v1/trade-journal/')));
  // Cross the actual domain contract using only its in-memory FakeStore. No Mongo connection or production writes.
  const {spawnSync}=require('node:child_process'),path=require('node:path');
  const domain=spawnSync(path.join(__dirname,'../.venv312/bin/python'),['-c',`
import json,runpy,sys
from datetime import datetime
t=runpy.run_path('tests/test_trade_journal.py')
store=t['FakeStore']()
svc=t['TradeJournalService'](store,now_factory=lambda:datetime(2026,10,27,8,tzinfo=t['TZ']))
inputs=json.load(sys.stdin)
payload=inputs['plan']
created=svc.freeze_plan(payload)
assert svc.list_episodes()['total']==0
assert not created['ex_ante_verifiable']
source=svc.get_source(payload['sources'][0]['source_id'],include_content=True)
assert source['content']==payload['sources'][0]['content']
assert source['metadata']['provenance_class']=='manual_observation'
from copy import deepcopy
complete=deepcopy(payload)
complete.update(mode_id='mode.reversal_pry',mode='原始模式',trigger='明确触发',reason='事前独立理由',expectation='下一日验证条件',verification_at='2026-10-28T15:00:00+08:00',invalidation='明确失效',holding_horizon='次日',risk_constraints='明确风险预算',reference_tickers=['000001.SZ'])
checked=svc.freeze_plan(complete)
assert checked['ex_ante_verifiable']
future=deepcopy(complete)
future['evidence'][0].update(observed_at='2026-10-29T15:00:00+08:00',available_at='2026-10-29T15:01:00+08:00')
assert not svc.freeze_plan(future)['ex_ante_verifiable']
t['import_ready'](svc,t['bundle']([t['fill']('ui-real-fee-unknown','2026-10-23','buy',100,10,100,fees=None)]))
ep=svc.list_episodes()['items'][0]
svc.bind_plan(created['plan_id'],ep['episode_id'],{'revision':created['revision'],'fill_ids':ep['fill_ids'],'reason':'测试明确关联'})
svc.append_decision(ep['episode_id'],{'action':'买入','reason':'真实领域目标日与验证时点分离测试','expectation':{'text':'目标日的用户预期','target_trade_date':'2026-10-26'},'verification_at':None})
detail=svc.get_episode(ep['episode_id'])
assert detail['fills'][0]['time_precision']=='execution_time'
assert detail['decisions'][-1]['verification_at'] is None
assert not detail['qualification']['ex_ante']['eligible']
assert all(name.startswith('trade_journal_') for name,_,_ in store.writes)
methods=runpy.run_path('tests/test_trade_journal_mode_cards.py')
method_store=methods['Store']()
method_service=methods['ModeCardService'](method_store,now_factory=lambda:datetime(2026,10,27,8,tzinfo=t['TZ']))
mode=method_service.save_card(inputs['mode'])
hypothesis=method_service.save_hypothesis(inputs['hypothesis'])
assert not mode['record']['advantage_verified'] and not hypothesis['record']['advantage_verified']
assert hypothesis['record']['content']['validation_results'][0]['content']['outcome_type']=='inconclusive'
assert all(name.startswith('trade_journal_') for name in method_store.writes)
from shilun.trade_journal.allocations import TradeAllocationService
from shilun.trade_journal.analytics import JournalAnalyticsService,PortfolioRiskService
from shilun.trade_journal.definitions import get_definition
allocation_store=t['FakeStore']()
allocation_journal=t['TradeJournalService'](allocation_store,now_factory=lambda:datetime(2026,10,27,12,tzinfo=t['TZ']))
t['import_ready'](allocation_journal,t['bundle']([t['fill']('alloc-b','2026-10-19','buy',100,10,100,fees=10),t['fill']('alloc-s','2026-10-20','sell',100,12,0,fees=20)]))
allocation_episode=allocation_journal.list_episodes()['items'][0]['episode_id']
allocation_detail=allocation_journal.get_episode(allocation_episode)
allocation_wire={'record':None,'allocation':TradeAllocationService(allocation_store,journal=allocation_journal).calculate(allocation_episode)}
sys.path.insert(0,'tests')
a=runpy.run_path('tests/test_trade_journal_analytics.py')
risk_store=t['FakeStore']()
a['risk_facts'](risk_store)
risk_journal=t['TradeJournalService'](risk_store,now_factory=lambda:datetime(2026,10,27,12,tzinfo=t['TZ']))
risk=PortfolioRiskService(risk_store,journal=risk_journal)
portfolio=risk.context()
portfolio_snapshot=risk.capture_snapshot({'reason':'真实领域UI契约测试'})
from shilun.trade_journal.tasks import JournalTaskService
task_service=JournalTaskService(allocation_store,journal=allocation_journal)
tasks=task_service.list_tasks()
task=next(r for r in tasks['items'] if r['root_cause']=='decision_context')
from shilun.trade_journal.import_files import inspect_file,parse_file
import base64
csv='''发生日期,发生时间,证券代码,证券名称,业务名称,成交数量,成交价格,成交金额,发生金额,手续费,股票余额,成交编号
2026-10-23,09:36:25,000636,风华高科,证券买入,100,10,1000,-1000,,100,000000123
'''
file_input={'filename':'东财原件.csv','content_base64':base64.b64encode(csv.encode()).decode()}
inspection=inspect_file(**file_input)
parsed=parse_file(**file_input,mapping=inspection['suggested_mapping'],account_alias='primary')
assert parsed['can_preview'],parsed
assert parsed['payload']['fills'][0]['fees'] is None
import_store=t['FakeStore']()
import_journal=t['TradeJournalService'](import_store,now_factory=lambda:datetime(2026,10,28,9,tzinfo=t['TZ']))
parsed['preview']=import_journal.preview_import(parsed['payload'])
assert not import_store.writes
receipt=import_journal.import_batch(parsed['payload'],parsed['preview']['preview_digest'])
execution_tests=runpy.run_path('tests/test_trade_journal_execution_matches.py')
_,execution_journal,execution_owner,execution_episode,execution_live=execution_tests['setup_link'](partial=True)
execution_candidates=execution_owner.candidates(execution_episode)
execution_preview=execution_owner.preview(execution_episode,'manual-1',['s1','s2'])
from shilun.trade_journal.reviews import JournalReviewService
review_owner=JournalReviewService(allocation_store,journal=allocation_journal)
review=review_owner.get_review(allocation_episode)
review_owner.save_review(allocation_episode,{'expected_revision':0,'expected_fact_digest':review['fact_digest'],'state':'draft','summary':'','overall_conclusion':'unknown','judgments':{},'evidence_refs':[]})
review_draft=review_owner.get_review(allocation_episode)
identity_store=t['FakeStore']()
identity_journal=t['TradeJournalService'](identity_store,now_factory=lambda:datetime(2026,10,28,9,tzinfo=t['TZ']))
t['import_ready'](identity_journal,t['bundle']([t['fill']('canonical-old','2026-10-19','buy',100,10,100,broker_execution_id='1234')]))
identity_csv='''成交日期,成交时间,证券代码,证券名称,业务名称,成交数量,成交价格,成交金额,发生金额,手续费,股票余额,成交编号
2026-10-19,10:00:00,000636,风华高科,证券买入,100,10,1000,-1001,1,100,00001234
'''
identity_file={'filename':'新增真实成交原件.csv','content_base64':base64.b64encode(identity_csv.encode()).decode()}
identity_inspection=inspect_file(**identity_file)
identity_parsed=parse_file(**identity_file,mapping=identity_inspection['suggested_mapping'])
identity_preview=identity_journal.preview_import(identity_parsed['payload'])
assert not identity_preview['can_import'] and len(identity_preview['identity_candidates'])==1
c=identity_preview['identity_candidates'][0]
identity_link={k:c[k] for k in ('incoming_source_ref','existing_fact_id','expected_existing_hash')}
identity_link.update(reason='逐原行核对两侧完整编号与核心事实，明确同一成交',identity_confirmation=True)
identity_payload={**deepcopy(identity_parsed['payload']),'identity_links':[identity_link]}
identity_linked_preview=identity_journal.preview_import(identity_payload)
assert identity_linked_preview['can_import'] and identity_linked_preview['counts']['duplicate_fills']==1
identity_receipt=identity_journal.import_batch(identity_payload,identity_linked_preview['preview_digest'])
print(json.dumps({'detail':detail,'sources':svc.list_sources(),'plan':svc.get_plan(created['plan_id'],created['revision']),
'allocation_detail':allocation_detail,'allocation_wire':allocation_wire,'allocation_definition':get_definition('mode.relay'),
'analytics':JournalAnalyticsService(allocation_store,journal=allocation_journal).report(group_by=['mode']),
'portfolio':portfolio,'portfolio_snapshot':portfolio_snapshot,'portfolio_snapshot_record_json':json.dumps(portfolio_snapshot['record'],ensure_ascii=False),
'tasks':tasks,'task_detail':task_service.get_task(task['task_id']),'import_file':file_input,'inspection':inspection,'parsed':parsed,'import_receipt':receipt,
'execution_candidates':execution_candidates,'execution_preview':execution_preview,'execution_history':execution_owner.get_match('manual-1'),'review':review,'review_draft':review_draft,
'identity_file':identity_file,'identity_inspection':identity_inspection,'identity_unlinked':{**identity_parsed,'preview':identity_preview},'identity_linked':{**identity_parsed,'payload':identity_payload,'preview':identity_linked_preview},'identity_receipt':identity_receipt},ensure_ascii=False))
  `],{input:JSON.stringify({plan:sourcePlanBody,mode:cardBody,hypothesis:hypoBody}),encoding:'utf8',maxBuffer:4*1024*1024});
  assert.equal(domain.status,0,domain.stderr);const real=JSON.parse(domain.stdout);ui.state.detail=real.detail;ui.renderDetail();assert(nodes.tradeJournalDetail.innerHTML.includes('—（费用待核）'));assert(nodes.tradeJournalDetail.innerHTML.includes('绑定已冻结预案'));assert.equal(real.plan.content.sources,undefined);assert(real.plan.captured_at.startsWith('2026-10-27'));
  const realMain=withoutRaw(nodes.tradeJournalDetail.innerHTML);assert(realMain.includes('撮合成交时间'),'real service execution_time is a time kind, not second/minute precision');assert(realMain.includes('<dt>目标交易日</dt><dd>2026-10-26</dd>'));assert(realMain.includes('<dt>验证时点</dt><dd>待补</dd>'));assert(!realMain.includes('<dt>验证时点</dt><dd>2026-10-26</dd>'));
  sourcesWire=real.sources;assert(sourcesWire.items.length>0);assert(sourcesWire.items.every(s=>Number.isInteger(s.bytes)));await ui.loadSources();for(const source of sourcesWire.items)assert(withoutRaw(nodes.tradeJournalSources.innerHTML).includes(source.bytes.toLocaleString('zh-CN')+' 字节'),'real list_sources bytes contract');
  allocationWire=real.allocation_wire;analyticsWire=real.analytics;portfolioWire=real.portfolio;portfolioSnapshotWire=real.portfolio_snapshot;
  const archivedPortfolio={...portfolioWire,holdings:[{ticker:'000636.SZ',name:'风华',quote_at:'2026-08-27T15:00:00+08:00',quote_status:'owner_snapshot_unassessed_freshness'}]};ui.renderPortfolio(archivedPortfolio);assert(nodes.tradeJournalPortfolio.innerHTML.includes('当前读到的账户存档'));assert(nodes.tradeJournalPortfolio.innerHTML.includes('有效持仓记录数'));assert(nodes.tradeJournalPortfolio.innerHTML.includes('已保存证券'));assert(nodes.tradeJournalPortfolio.innerHTML.includes('存档报价，时效未核验'));assert(nodes.tradeJournalPortfolio.innerHTML.includes('2026-08-27T15:00:00+08:00'));assert(nodes.tradeJournalPortfolio.innerHTML.includes('读取时点不代表行情时点'));assert(!nodes.tradeJournalPortfolio.innerHTML.includes('owner_snapshot_unassessed_freshness'));
  ui.state.definitions.push(real.allocation_definition);ui.state.detail=real.allocation_detail;ui.state.selected=real.allocation_detail.episode.episode_id;await ui.loadAllocations();ui.editAllocations();assert(nodes.tradeJournalAllocation.innerHTML.includes('未分配'));assert(nodes.tradeJournalAllocationEditor.innerHTML.includes('完整逐笔分配表'));
  const modeRef={id:real.allocation_definition.id,rule_version:real.allocation_definition.rule_version};ui.state.allocationRows=[{fill_id:'alloc-b',mode_ref:modeRef,quantity:100,buy_lot_id:'alloc-b'},{fill_id:'alloc-s',mode_ref:modeRef,quantity:100,buy_lot_id:'alloc-b'}];await ui.saveAllocations(form({reason:'逐笔明确模式股数'},{episode:ui.state.selected}));const allocationBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.deepEqual(allocationBody.assignments[1],{fill_id:'alloc-s',mode_ref:modeRef,quantity:100,buy_lot_id:'alloc-b'});assert(!('split_ratio' in allocationBody));
  ui.editAllocations();ui.state.allocationRows[0].quantity=101;const beforeOverfill=calls.filter(c=>c.options.method==='POST').length;await ui.saveAllocations(form({reason:'超过真实数'},{episode:ui.state.selected,baseRevision:'1'}));assert.equal(calls.filter(c=>c.options.method==='POST').length,beforeOverfill);assert(nodes.tradeJournalAllocationStatus.textContent.includes('超过本笔真实成交'));
  ui.state.allocationRows[0].quantity=100;const oldAllocationForm=form({reason:'旧表单理由'},{episode:ui.state.selected,baseRevision:'1'});nodes.tradeJournalAllocationForm=oldAllocationForm;allocationWire.record={revision:2,assignments:allocationBody.assignments,labels:[],recorded_at:'2026-10-27T12:01:00+08:00'};await ui.loadAllocations();assert.equal(oldAllocationForm.dataset.stale,'true');const beforeStale=calls.filter(c=>c.options.method==='POST').length;await ui.saveAllocations(oldAllocationForm);assert.equal(calls.filter(c=>c.options.method==='POST').length,beforeStale);assert(nodes.tradeJournalAllocationStatus.textContent.includes('基线已过期'));ui.editAllocations();assert(nodes.tradeJournalAllocationEditor.innerHTML.includes('data-base-revision="2"'));await ui.saveAllocations(form({reason:'重新审阅版本2'},{episode:ui.state.selected,baseRevision:'2'}));const renewedAllocationBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.equal(renewedAllocationBody.expected_revision,2);
  filterFields.start_date.value='';filterFields.end_date.value='';await ui.loadAnalytics(form({group_by:'mode'}));assert(nodes.tradeJournalAnalytics.innerHTML.includes('已清仓且成本已知的库存轮整体'));assert(nodes.tradeJournalAnalytics.innerHTML.includes('数量切片'));assert(nodes.tradeJournalAnalytics.innerHTML.includes('既有FIFO实际含费买入成本'));await ui.showAnalyticsGroup(analyticsWire.groups[0].group_id);assert(nodes.tradeJournalArtifactBody.innerHTML.includes('切片占比不叫股票交易胜率'));
  await ui.loadPortfolio();assert(nodes.tradeJournalPortfolio.innerHTML.includes('当前读到的账户存档 · 服务器读取时点'));assert(nodes.tradeJournalPortfolio.innerHTML.includes('当前已核验净资产'));assert(!nodes.tradeJournalPortfolio.innerHTML.includes('最大回撤 0'));
  ui.state.scenarioLegs=[{ticker:'000636.SZ',side:'buy',quantity:'300',price:'37',fees:''},{ticker:'000636.SZ',side:'sell',quantity:'1200',price:'37.2',fees:'20'}];await ui.previewPortfolio(form({action:'腾资后补仓',reason:'用户提议',user_limits:'{"max_proposed_add_cash":15000}'}));const scenarioBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.equal(scenarioBody.scenario.legs[0].fees,null);assert.equal(scenarioBody.scenario.legs[1].quantity,1200);assert(!('executed' in scenarioBody.scenario));
  const attachedPlanForm=form({...preValues,observation_source_text:rawObservation},{plan:'',revision:'',portfolioSnapshotRef:'null'});nodes.tradeJournalPlanForm=attachedPlanForm;await ui.capturePortfolio({forPlan:true});const captureBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.equal(captureBody.scenario,null);assert(!('captured_at' in captureBody));assert(!('historical_at' in captureBody));await ui.savePreplan(attachedPlanForm);const attachedPlanBody=JSON.parse(calls.filter(c=>c.options.method==='POST').at(-1).options.body);assert.equal(attachedPlanBody.portfolio_snapshot_ref.snapshot_id,portfolioSnapshotWire.snapshot_ref.snapshot_id);assert.equal(attachedPlanBody.portfolio_snapshot_ref.content_hash,portfolioSnapshotWire.snapshot_ref.content_hash);
  let releaseCapture;portfolioCaptureGate=new Promise(resolve=>releaseCapture=resolve);const planFormA=form(preValues,{plan:'A',revision:'1',portfolioSnapshotRef:'null'});nodes.tradeJournalPlanForm=planFormA;const pendingCapture=ui.capturePortfolio({forPlan:true});assert.equal(planFormA.button.disabled,true);assert.equal(planFormA.button.attributes['aria-busy'],'true');const planFormB=form(preValues,{plan:'B',revision:'2',portfolioSnapshotRef:'null'});nodes.tradeJournalPlanForm=planFormB;releaseCapture();await pendingCapture;portfolioCaptureGate=null;assert.equal(planFormB.dataset.portfolioSnapshotRef,'null');assert.equal(planFormA.dataset.portfolioSnapshotRef,'null');assert(nodes.tradeJournalStatus.textContent.includes('未自动附到其他预案'));assert.equal(ui.state.portfolioCapturing,false);assert.equal(planFormA.button.disabled,false);assert.equal(planFormA.button.attributes['aria-busy'],'false');assert.equal(planFormB.button.disabled,false);
  const quant=spawnSync(path.join(__dirname,'../.venv312/bin/python'),['-c',`
import json,runpy,sys
from datetime import datetime
from copy import deepcopy
sys.path.insert(0,'tests')
t=runpy.run_path('tests/test_trade_journal.py')
a=runpy.run_path('tests/test_trade_journal_analytics.py')
from shilun.trade_journal.allocations import TradeAllocationService
from shilun.trade_journal.analytics import JournalAnalyticsService,PortfolioRiskService
inputs=json.load(sys.stdin)
store=t['FakeStore']()
journal=t['TradeJournalService'](store,now_factory=lambda:datetime(2026,10,27,12,tzinfo=t['TZ']))
t['import_ready'](journal,t['bundle']([t['fill']('alloc-b','2026-10-19','buy',100,10,100,fees=10),t['fill']('alloc-s','2026-10-20','sell',100,12,0,fees=20)]))
episode=journal.list_episodes()['items'][0]['episode_id']
matches=deepcopy(journal.get_episode(episode)['episode']['matches'])
allocated=TradeAllocationService(store,journal=journal).append_allocations(episode,inputs['allocation'])
assert allocated['allocation']['mode_results'][0]['net_pnl']==170
assert allocated['allocation']['reconciliation']['balanced']
assert journal.get_episode(episode)['episode']['matches']==matches
report=JournalAnalyticsService(store,journal=journal).report(group_by=['mode'])
assert report['denominator']['closed_known_n']==1 and report['overall']['net_pnl']==170
risk_store=t['FakeStore']()
a['risk_facts'](risk_store)
risk_journal=t['TradeJournalService'](risk_store,now_factory=lambda:datetime(2026,10,27,12,tzinfo=t['TZ']))
risk=PortfolioRiskService(risk_store,journal=risk_journal)
holdings=deepcopy(risk_store.collection('holdings').documents)
preview=risk.context(**inputs['scenario'])
assert preview['scenario']['cash_delta'] is None and not preview['scenario']['executed']
assert preview['scenario']['legs'][1]['flags']
assert risk_store.writes==[]
captured=risk.capture_snapshot(inputs['capture'])
assert captured['record']['captured_at'].startswith('2026-10-27')
snapshot=json.loads(inputs['snapshot_json'])
risk_store.collection('trade_journal_portfolio_snapshots').documents.append(snapshot)
plan=risk_journal.freeze_plan(inputs['plan'])
assert plan['record']['content']['portfolio_snapshot_ref']['snapshot_id']==snapshot['snapshot_id']
assert risk_store.collection('holdings').documents==holdings
assert all(name.startswith('trade_journal_') for name,_,_ in risk_store.writes)
print('quantity/analytics/current-risk contract passed')
`],{input:JSON.stringify({allocation:allocationBody,scenario:scenarioBody,capture:captureBody,plan:attachedPlanBody,snapshot_json:real.portfolio_snapshot_record_json}),encoding:'utf8',maxBuffer:4*1024*1024});
  assert.equal(quant.status,0,quant.stderr);
  // Two in-place workflows share real domain contracts, without production writes.
  const inserted=[];delete nodes.tradeJournalTasksSection;delete nodes.tradeJournalImportSection;nodes.tradeJournalSummary.insertAdjacentHTML=(where,html)=>inserted.push({where,html});nodes.tradeJournalSourceSection={querySelector:()=>({insertAdjacentHTML:(where,html)=>inserted.push({where,html})})};ui.mountReviewWorkflows();assert(inserted[0].html.includes('缺项待办与处置记录'));assert(inserted[1].html.includes('从东财原始文件导入'));assert(inserted.every(r=>r.where==='afterend'));nodes.tradeJournalTasksSection=node();nodes.tradeJournalImportSection=node();
  tasksWire=JSON.parse(JSON.stringify(real.tasks));taskDetailWire=JSON.parse(JSON.stringify(real.task_detail));await ui.loadTasks();assert(nodes.tradeJournalTasks.innerHTML.includes('本页'));assert(nodes.tradeJournalTasks.innerHTML.includes('决策依据'));await ui.editTask(taskDetailWire.task.task_id);assert(nodes.tradeJournalTaskEditor.innerHTML.includes('当时未设'));assert(nodes.tradeJournalTaskEditor.innerHTML.includes('无法回忆'));assert(nodes.tradeJournalTaskEditor.innerHTML.includes('value="原始资料无法恢复"'));assert(nodes.tradeJournalTaskEditor.innerHTML.includes('value="现有证据不足以核实"'));assert(nodes.tradeJournalTaskEditor.innerHTML.includes('不补造期初成本、原始来源'));assert(nodes.tradeJournalTaskEditor.innerHTML.includes('仅适用于已确认的主观预案'));assert(!nodes.tradeJournalTaskEditor.innerHTML.includes('value="resolved"'));
  const baseTask=real.task_detail.task,taskKeys=['accounting','evidence_time_or_provenance','plan_reference','confirmed_process_review','expectation_review','episode_source','new_owner_field_without_mapping'];assert.equal(baseTask.start_date,'2026-10-19');assert.equal(baseTask.end_date,'2026-10-20');tasksWire={items:[{...baseTask,missing_fields:taskKeys},{...baseTask,task_id:'task-second-same-security',episode_id:'ep-second-same-security',start_date:'2026-10-22',end_date:'2026-10-23',missing_fields:['plan_reference']}],total:2,state_counts:{open:2}};await ui.loadTasks();const datedTasksMain=withoutRaw(nodes.tradeJournalTasks.innerHTML);assert(datedTasksMain.includes('2026-10-19 → 2026-10-20'));assert(datedTasksMain.includes('2026-10-22 → 2026-10-23'));assert(datedTasksMain.includes('库存轮：ep-second-same-security'));for(const label of ['成交、库存与成本核对','证据时点或来源','原计划引用核对','已确认的过程复盘','原版本预期核验','库存轮来源','未映射字段'])assert(datedTasksMain.includes(label),label);for(const key of taskKeys)assert(!datedTasksMain.includes(key),key+' must be translated or folded');assert.deepEqual(rawRecords(nodes.tradeJournalTasks.innerHTML)[0].missing_fields,taskKeys);tasksWire=JSON.parse(JSON.stringify(real.tasks));await ui.loadTasks();
  const taskForm=form({action:'confirm_unknown',unknown_basis:'当时未设',reason:'当时没有保存，不能重建事前依据'},{task:taskDetailWire.task.task_id,revision:'0',fingerprint:taskDetailWire.task.fact_fingerprint,stale:'false'});nodes.tradeJournalTaskForm=taskForm;await ui.saveTaskResolution(taskForm);const taskBody=JSON.parse(calls.filter(c=>c.url.endsWith('/resolutions')).at(-1).options.body);assert.equal(taskBody.expected_revision,0);assert.equal(taskBody.expected_fingerprint,real.task_detail.task.fact_fingerprint);assert(taskBody.reason.includes('当时未设'));assert(!('mode' in taskBody));assert(!('qualification' in taskBody));
  const beforeMissingBasis=calls.length;await ui.saveTaskResolution(form({action:'confirm_unknown',unknown_basis:'',reason:'原始依据无法恢复'},{task:taskDetailWire.task.task_id,revision:'1',fingerprint:taskDetailWire.task.fact_fingerprint}));assert.equal(calls.length,beforeMissingBasis);await ui.saveTaskResolution(form({action:'confirm_unknown',unknown_basis:'原始资料无法恢复',reason:''},{task:taskDetailWire.task.task_id,revision:'1',fingerprint:taskDetailWire.task.fact_fingerprint}));assert.equal(calls.length,beforeMissingBasis,'objective unknown still requires an explicit basis and reason');
  const staleTaskForm=form({action:'ignore',reason:'旧表单'},{task:taskDetailWire.task.task_id,revision:'1',fingerprint:taskDetailWire.task.fact_fingerprint,stale:'false'});nodes.tradeJournalTaskForm=staleTaskForm;tasksWire.items=tasksWire.items.map(t=>t.task_id===staleTaskForm.dataset.task?{...t,fact_fingerprint:'new-facts'}:t);await ui.loadTasks();assert.equal(staleTaskForm.dataset.stale,'true');const beforeStaleTask=calls.length;await ui.saveTaskResolution(staleTaskForm);assert.equal(calls.length,beforeStaleTask);assert(nodes.tradeJournalTaskSaveStatus.textContent.includes('基线已过期'));
  importInspectionWire=real.inspection;importParseWire=real.parsed;importReceiptWire=real.import_receipt;const csvBytes=Buffer.from(real.import_file.content_base64,'base64'),csvFile={name:real.import_file.filename,size:csvBytes.length,arrayBuffer:async()=>csvBytes.buffer.slice(csvBytes.byteOffset,csvBytes.byteOffset+csvBytes.byteLength)},fileForm=form({file:'',encoding:'',account_alias:'primary'});fileForm.elements.namedItem('file').files=[csvFile];nodes.tradeJournalImportFileForm=fileForm;
  await ui.inspectImport(fileForm);assert(nodes.tradeJournalImportMapping.innerHTML.includes('证券代码'));assert(nodes.tradeJournalImportMapping.innerHTML.includes('交割单发生时间'));assert(nodes.tradeJournalImportMapping.innerHTML.includes('data-import-column="fees"'));assert(!nodes.tradeJournalImportMapping.innerHTML.includes('name="mapping_json"'));assert(!nodes.tradeJournalImportMapping.innerHTML.includes(real.import_file.content_base64));assertNoNestedForms(nodes.tradeJournalImportMapping.innerHTML);assert(nodes.tradeJournalImportMapping.innerHTML.includes('type="button" data-tj-action="import-read-rows"'));assert(nodes.tradeJournalImportStatus.textContent.includes('已识别'));assert(nodes.tradeJournalImportPreview.innerHTML.includes('请核对映射'));assert(!nodes.tradeJournalImportPreview.innerHTML.includes('正在检查原件'));
  const inspectedBody=JSON.parse(calls.filter(c=>c.url.endsWith('/import-files/inspect')).at(-1).options.body);assert.equal(inspectedBody.content_base64,real.import_file.content_base64);await ui.parseImport(form({}));const parsedBody=JSON.parse(calls.filter(c=>c.url.endsWith('/import-files/parse')).at(-1).options.body);assert.deepEqual(parsedBody.mapping,real.inspection.suggested_mapping);assert.equal(parsedBody.account_alias,'primary');assert(nodes.tradeJournalImportPreview.innerHTML.includes('费用空值保持未知'));assert(nodes.tradeJournalImportPreview.innerHTML.includes('确认导入这份预览'));assert.equal(ui.state.importParsed.payload.fills[0].fees,null);assert.equal(ui.state.importParsed.payload.fills[0].broker_execution_id,'000000123');
  const confirmForm=form({confirmed:''});confirmForm.elements.namedItem('confirmed').checked=false;const beforeConfirm=calls.filter(c=>c.url.endsWith('/imports')).length;await ui.commitImport(confirmForm);assert.equal(calls.filter(c=>c.url.endsWith('/imports')).length,beforeConfirm);confirmForm.elements.namedItem('confirmed').checked=true;await ui.commitImport(confirmForm);const importBody=JSON.parse(calls.filter(c=>c.url.endsWith('/imports')).at(-1).options.body);assert.equal(importBody.preview_digest,real.parsed.preview.preview_digest);assert.equal(importBody.sources[0].content,real.parsed.payload.sources[0].content);assert.equal(importBody.fills[0].fees,null);assert(nodes.tradeJournalImportReceipt.innerHTML.includes('服务端已完成本批次归档'));const receiptMain=withoutRaw(nodes.tradeJournalImportReceipt.innerHTML);for(const [key,label] of [['new_fills','新增成交'],['duplicate_fills','已有成交'],['new_sources','新增来源'],['new_financial_events','新增财务事件'],['duplicate_financial_events','已有财务事件'],['conflicts','冲突数']])assert(receiptMain.includes(`${label} <b>${real.import_receipt.counts[key].toLocaleString('zh-CN')}</b>`),label+' must be visible outside raw receipt');assert.equal(ui.state.importParsed,null);
  await ui.parseImport(form({}));ui.updateImportMapping({dataset:{importSheet:real.inspection.sheets[0].name,importColumn:'fees'},value:''});assert.equal(ui.state.importParsed,null);const beforeInvalidPreview=calls.filter(c=>c.url.endsWith('/imports')).length;await ui.commitImport(confirmForm);assert.equal(calls.filter(c=>c.url.endsWith('/imports')).length,beforeInvalidPreview);assert(nodes.tradeJournalImportStatus.textContent.includes('预览已失效'));
  ui.state.importParsed={can_preview:false,payload:null,issues:[{sheet:'原表',row:2,message:'业务类型未映射'}],audit:{unresolved_rows:1},preview:{can_import:false,conflicts:[{kind:'fills',message:'身份冲突不得覆盖'}]}};ui.state.importPreviewGeneration=ui.state.importGeneration;ui.renderImportPreview();assert(nodes.tradeJournalImportPreview.innerHTML.includes('业务类型未映射'));assert(nodes.tradeJournalImportPreview.innerHTML.includes('身份冲突不得覆盖'));assert(nodes.tradeJournalImportPreview.innerHTML.includes('type="submit" disabled'));
  let releaseInspect;importInspectGate=new Promise(resolve=>releaseInspect=resolve);const pendingInspect=ui.inspectImport(fileForm);await Promise.resolve();ui.resetImport();releaseInspect();await pendingInspect;importInspectGate=null;assert.equal(ui.state.importFile,null);assert.equal(ui.state.importInspection,null);assert(nodes.tradeJournalImportStatus.textContent.includes('请选择实际原始文件'));
  executionCandidatesWire=JSON.parse(JSON.stringify(real.execution_candidates));executionPreviewWire=real.execution_preview;executionHistoryWire=real.execution_history;ui.state.selected=executionCandidatesWire.episode_id;await ui.loadExecutionMatches();assert(nodes.tradeJournalExecutionMatches.innerHTML.includes('尚未关联'));ui.editExecutionMatch('manual-1');assert(nodes.tradeJournalExecutionEditor.innerHTML.includes('本轮归档成交（不预选）'));assert(!nodes.tradeJournalExecutionEditor.innerHTML.includes('name="fill_ids" checked'));
  const executionSelect=form({},{episode:ui.state.selected,execution:'manual-1'});executionSelect.elements.push({name:'fill_ids',value:'s1',checked:true},{name:'fill_ids',value:'s2',checked:true});nodes.tradeJournalExecutionSelectForm=executionSelect;await ui.previewExecutionMatch(executionSelect);assert(nodes.tradeJournalExecutionPreview.innerHTML.includes('已核对属于同一账户'));assert(nodes.tradeJournalExecutionPreview.innerHTML.includes('具体时刻或分笔粒度差异'));assert(nodes.tradeJournalExecutionPreview.innerHTML.includes('data-revision="0"'));
  const executionConfirm=form({reason:'已逐行核对原件，确认同一账户的同一成交',identity_confirmation:'',account_confirmation:'',time_difference_confirmation:''},{episode:ui.state.selected,execution:'manual-1',revision:String(executionPreviewWire.expected_revision),digest:executionPreviewWire.preview_digest,liveDigest:executionPreviewWire.live_digest,fills:JSON.stringify(executionPreviewWire.fill_ids),timeDifference:'true',stale:'false'});nodes.tradeJournalExecutionConfirmForm=executionConfirm;let beforeMatch=calls.filter(c=>c.options.method==='POST'&&c.url.endsWith('/execution-matches')).length;await ui.saveExecutionMatch(executionConfirm);assert.equal(calls.filter(c=>c.options.method==='POST'&&c.url.endsWith('/execution-matches')).length,beforeMatch);for(const key of ['identity_confirmation','account_confirmation','time_difference_confirmation'])executionConfirm.elements.namedItem(key).checked=true;await ui.saveExecutionMatch(executionConfirm);const executionBody=JSON.parse(calls.filter(c=>c.options.method==='POST'&&c.url.endsWith('/execution-matches')).at(-1).options.body);assert.deepEqual(executionBody.fill_ids,['s1','s2']);assert.equal(executionBody.time_difference_confirmation,true);assert.equal(executionBody.expected_revision,0);assert(!('quantity' in executionBody));assert(!('cash_delta' in executionBody));
  executionCandidatesWire.items[0]={...executionCandidatesWire.items[0],revision:1,state:'confirmed',record:{episode_id:ui.state.selected,fill_ids:['s1','s2']}};await ui.loadExecutionMatches();ui.unlinkExecutionEditor('manual-1');assert(nodes.tradeJournalExecutionEditor.innerHTML.includes('不冲销持仓、现金或损益'));const unlinkForm=form({reason:'重新核对发现需要解除'},{episode:ui.state.selected,execution:'manual-1',revision:'1',liveDigest:executionCandidatesWire.items[0].live_digest,stale:'false'});nodes.tradeJournalExecutionConfirmForm=undefined;nodes.tradeJournalExecutionUnlinkForm=unlinkForm;await ui.saveExecutionMatch(unlinkForm,{unlink:true});const unlinkBody=JSON.parse(calls.filter(c=>c.options.method==='POST'&&c.url.endsWith('/execution-matches')).at(-1).options.body);assert.equal(unlinkBody.action,'unlink');assert.equal(unlinkBody.live_digest,executionCandidatesWire.items[0].live_digest);assert(!('preview_digest' in unlinkBody));
  const staleMatchForm=form({reason:'旧核对表'},{episode:ui.state.selected,execution:'manual-1',revision:'1',liveDigest:'old-digest',stale:'false'});nodes.tradeJournalExecutionUnlinkForm=staleMatchForm;await ui.loadExecutionMatches();assert.equal(staleMatchForm.dataset.stale,'true');beforeMatch=calls.length;await ui.saveExecutionMatch(staleMatchForm,{unlink:true});assert.equal(calls.length,beforeMatch);await ui.showExecutionMatchHistory('manual-1');assert(nodes.tradeJournalArtifactBody.innerHTML.includes('不回写生产账户'));
  reviewWire=real.review;ui.state.selected=real.allocation_detail.episode.episode_id;ui.state.detail=real.allocation_detail;await ui.loadProcessReview();assert(nodes.tradeJournalReview.innerHTML.includes('尚未记录'));await ui.editProcessReview();assert(nodes.tradeJournalReviewEditor.innerHTML.includes('入场判断'));assert(nodes.tradeJournalReviewEditor.innerHTML.includes('信息依据判断'));assert(nodes.tradeJournalReviewEditor.innerHTML.includes('value="unknown" selected'));assertNoNestedForms(nodes.tradeJournalReviewEditor.innerHTML);
  const completionHtml=nodes.tradeJournalReviewEditor.innerHTML;
  assert(completionHtml.includes('确认并标记已复盘'));assert(completionHtml.includes('先保存草稿'));
  assert(!completionHtml.includes('<select name="state"'));assert(completionHtml.includes('name="state" value="confirmed"'));
  assert(completionHtml.indexOf('name="summary"')<completionHtml.indexOf('分项判断与证据（选填）'));
  assert(completionHtml.indexOf('name="process_confirmation"')<completionHtml.indexOf('分项判断与证据（选填）'));
  assert(completionHtml.includes('未来预期尚未到期'));assert(completionHtml.includes('回看本轮成交与已填想法'));
  const reviewDraftForm=form({state:'draft',summary:'',overall_conclusion:''},{episode:ui.state.selected,revision:'0',factDigest:real.review.fact_digest,original:'{}',stale:'false'});nodes.tradeJournalReviewForm=reviewDraftForm;await ui.saveProcessReview(reviewDraftForm);const reviewDraftBody=JSON.parse(calls.filter(c=>c.options.method==='POST'&&c.url.endsWith('/review')).at(-1).options.body);assert.deepEqual(reviewDraftBody.judgments,{});assert.equal(reviewDraftBody.overall_conclusion,'unknown');assert.equal(reviewDraftBody.expected_fact_digest,real.review.fact_digest);assert(!('closed_pnl' in reviewDraftBody));
  reviewWire=real.review_draft;await ui.editProcessReview();const reviewConfirmForm=form({state:'confirmed',summary:'本轮资料不足，六维暂不作归因判断；已核对可恢复的过程记录',overall_conclusion:'',revision_reason:'记录本轮过程，未知维度继续保留',process_confirmation:''},{episode:ui.state.selected,revision:'1',factDigest:real.review_draft.fact_digest,original:JSON.stringify(real.review_draft.record),stale:'false'});nodes.tradeJournalReviewForm=reviewConfirmForm;let beforeReview=calls.filter(c=>c.options.method==='POST'&&c.url.endsWith('/review')).length;await ui.saveProcessReview(reviewConfirmForm);assert.equal(calls.filter(c=>c.options.method==='POST'&&c.url.endsWith('/review')).length,beforeReview);reviewConfirmForm.elements.namedItem('process_confirmation').checked=true;await ui.saveProcessReview(reviewConfirmForm);const reviewConfirmBody=JSON.parse(calls.filter(c=>c.options.method==='POST'&&c.url.endsWith('/review')).at(-1).options.body);assert.equal(reviewConfirmBody.expected_revision,1);assert.deepEqual(reviewConfirmBody.judgments,{});assert.equal(reviewConfirmBody.state,'confirmed');
  reviewWire={...reviewWire,current_confirmed:true};await ui.editProcessReview();
  assert(nodes.tradeJournalReviewEditor.innerHTML.includes('更新已确认复盘'));
  assert(!nodes.tradeJournalReviewEditor.innerHTML.includes('先保存草稿'),'confirmed editing cannot silently downgrade to a draft');
  const autoReasonForm=form({state:'confirmed',summary:'已核对当前记录；目标尚未到期',process_confirmation:''},{episode:ui.state.selected,revision:'1',factDigest:real.review_draft.fact_digest,original:'{}',stale:'false'});
  autoReasonForm.elements.namedItem('process_confirmation').checked=true;nodes.tradeJournalReviewForm=autoReasonForm;const beforeDoubleReview=calls.filter(c=>c.options.method==='POST'&&c.url.endsWith('/review')).length;await Promise.all([ui.saveProcessReview(autoReasonForm),ui.saveProcessReview(autoReasonForm,{targetState:'draft'})]);assert.equal(calls.filter(c=>c.options.method==='POST'&&c.url.endsWith('/review')).length,beforeDoubleReview+1,'pending confirmation blocks a second draft or confirmation request');
  assert.equal(JSON.parse(calls.filter(c=>c.options.method==='POST'&&c.url.endsWith('/review')).at(-1).options.body).revision_reason,'确认当前记录，完成本次复盘');
  nodes.tradeJournalReviewForm=reviewConfirmForm;

  reviewWire={...reviewWire,current_state:'stale',latest_revision:2,fact_digest:'new-review-facts'};await ui.loadProcessReview();assert.equal(reviewConfirmForm.dataset.stale,'true');assert(nodes.tradeJournalReview.innerHTML.includes('原事实已更新，待重新核对'));beforeReview=calls.length;await ui.saveProcessReview(reviewConfirmForm);assert.equal(calls.length,beforeReview);await ui.showProcessReviewHistory();assert(nodes.tradeJournalArtifactBody.innerHTML.includes('复盘草稿'));
  importInspectionWire=real.identity_inspection;importParseWire=real.identity_unlinked;importLinkedParseWire=real.identity_linked;importReceiptWire=real.identity_receipt;const identityBytes=Buffer.from(real.identity_file.content_base64,'base64'),identityFileForm=form({file:'',encoding:'',account_alias:'primary'});identityFileForm.elements.namedItem('file').files=[{name:real.identity_file.filename,size:identityBytes.length,arrayBuffer:async()=>identityBytes.buffer.slice(identityBytes.byteOffset,identityBytes.byteOffset+identityBytes.byteLength)}];nodes.tradeJournalImportFileForm=identityFileForm;nodes.tradeJournalImportMappingForm=form({});await ui.inspectImport(identityFileForm);await ui.parseImport(nodes.tradeJournalImportMappingForm);assert(nodes.tradeJournalImportPreview.innerHTML.includes('00001234'));assert(nodes.tradeJournalImportPreview.innerHTML.includes('1234'));assert(nodes.tradeJournalImportPreview.innerHTML.includes('新来源事实'));assert(nodes.tradeJournalImportPreview.innerHTML.includes('既有归档事实'));assert(!nodes.tradeJournalImportPreview.innerHTML.includes('name="identity_choice_0" value="0" checked'));assertNoNestedForms(nodes.tradeJournalImportPreview.innerHTML);
  const identityForm=form({identity_reason_0:''},{candidates:JSON.stringify(real.identity_unlinked.preview.identity_candidates),previewDigest:real.identity_unlinked.preview.preview_digest});identityForm.elements.push({name:'identity_choice_0',value:'0',type:'radio',checked:false});let beforeIdentity=calls.length;await ui.saveImportIdentityChoices(identityForm);assert.equal(calls.length,beforeIdentity);identityForm.elements.namedItem('identity_choice_0').checked=true;await ui.saveImportIdentityChoices(identityForm);assert.equal(calls.length,beforeIdentity);identityForm.elements.namedItem('identity_reason_0').value='逐原行核对两侧完整编号与核心事实，明确同一成交';await ui.saveImportIdentityChoices(identityForm);const identityParseBody=JSON.parse(calls.filter(c=>c.url.endsWith('/import-files/parse')).at(-1).options.body);assert.equal(identityParseBody.identity_links[0].existing_fact_id,'canonical-old');assert.equal(identityParseBody.identity_links[0].expected_existing_hash,real.identity_unlinked.preview.identity_candidates[0].expected_existing_hash);assert.equal(identityParseBody.identity_links[0].identity_confirmation,true);assert.equal(ui.state.importIdentityLinks.length,1);assert(nodes.tradeJournalImportPreview.innerHTML.includes('本次已明确的身份对应（1）'));
  const identityConfirm=form({confirmed:''});identityConfirm.elements.namedItem('confirmed').checked=true;await ui.commitImport(identityConfirm);const identityImportBody=JSON.parse(calls.filter(c=>c.options.method==='POST'&&c.url.endsWith('/imports')).at(-1).options.body);assert.deepEqual(identityImportBody.identity_links,identityParseBody.identity_links);assert.equal(identityImportBody.fills[0].broker_execution_id,'00001234');ui.invalidateImport();assert.equal(ui.state.importIdentityLinks.length,0,'changed file/mapping invalidates all prior row identity confirmations');
  const duplicateReceiptMain=withoutRaw(nodes.tradeJournalImportReceipt.innerHTML);assert(duplicateReceiptMain.includes('新增成交 <b>0</b>'));assert(duplicateReceiptMain.includes('已有成交 <b>1</b>'));assert(duplicateReceiptMain.includes('冲突数 <b>0</b>'));
  const workflows=spawnSync(path.join(__dirname,'../.venv312/bin/python'),['-c',`
import json,runpy,sys
from datetime import datetime
from copy import deepcopy
sys.path.insert(0,'tests')
t=runpy.run_path('tests/test_trade_journal.py')
from shilun.trade_journal.tasks import JournalTaskService,task_id_for
from shilun.trade_journal.import_files import parse_file
inputs=json.load(sys.stdin)
store=t['FakeStore']()
journal=t['TradeJournalService'](store,now_factory=lambda:datetime(2026,10,27,12,tzinfo=t['TZ']))
t['import_ready'](journal,t['bundle']([t['fill']('alloc-b','2026-10-19','buy',100,10,100,fees=10),t['fill']('alloc-s','2026-10-20','sell',100,12,0,fees=20)]))
episode=journal.list_episodes()['items'][0]['episode_id']
tasks=JournalTaskService(store,journal=journal)
before=deepcopy(journal.get_episode(episode))
saved=tasks.append_resolution(task_id_for(episode,'decision_context'),inputs['resolution'])
assert saved['task']['state']=='confirmed_unknown'
assert journal.get_episode(episode)['qualification']==before['qualification']
assert journal.get_episode(episode)['episode']['closed_pnl']==before['episode']['closed_pnl']
from shilun.trade_journal.reviews import JournalReviewService
reviews=JournalReviewService(store,journal=journal)
draft=reviews.save_review(episode,inputs['review_draft'])
assert all(v['judgment']=='unknown' and not v['user_supplied'] for v in draft['record']['judgments'].values())
confirmed=reviews.save_review(episode,inputs['review_confirm'])
assert confirmed['review']['current_confirmed']
assert all(not v['user_supplied'] for v in confirmed['record']['judgments'].values())
assert tasks.get_task(task_id_for(episode,'process_review'))['task']['state']=='resolved'
assert journal.get_episode(episode)['qualification']==before['qualification']
journal.append_decision(episode,t['decision_payload'](reason='找到新的事后理由'))
assert tasks.get_task(saved['task']['task_id'])['task']['auto_reopened']
assert reviews.get_review(episode)['current_state']=='stale'
assert tasks.get_task(task_id_for(episode,'process_review'))['task']['auto_reopened']
args=deepcopy(inputs['parse'])
identity_links=args.pop('identity_links',[])
parsed=parse_file(**args)
if identity_links: parsed['payload']['identity_links']=identity_links
assert parsed['can_preview'] and parsed['payload']['fills'][0]['fees'] is None
import_store=t['FakeStore']()
import_journal=t['TradeJournalService'](import_store,now_factory=lambda:datetime(2026,10,28,9,tzinfo=t['TZ']))
assert not import_store.writes
body=deepcopy(inputs['import'])
digest=body.pop('preview_digest')
result=import_journal.import_batch(body,digest)
assert result['counts']['new_fills']==1
assert import_journal.import_batch(body,digest)['idempotent']
source=import_journal.get_source(body['sources'][0]['source_id'],include_content=True)
assert source['content']==body['sources'][0]['content']
assert import_journal.list_fills()['items'][0]['broker_execution_id']=='000000123'
assert all(name.startswith('trade_journal_') for name,_,_ in import_store.writes)
execution_tests=runpy.run_path('tests/test_trade_journal_execution_matches.py')
execution_store,execution_journal,execution_owner,execution_episode,execution_live=execution_tests['setup_link'](partial=True)
live_before=deepcopy(execution_live)
pnl_before=execution_journal.get_episode(execution_episode)['episode']['closed_pnl']
matched=execution_owner.append_match(execution_episode,inputs['match'])
assert matched['record']['state']=='confirmed'
unlinked=execution_owner.append_match(execution_episode,inputs['unlink'])
assert unlinked['record']['state']=='unlinked'
assert execution_live==live_before
assert execution_journal.get_episode(execution_episode)['episode']['closed_pnl']==pnl_before
assert all(name.startswith('trade_journal_') for name,_,_ in execution_store.writes)
identity_store=t['FakeStore']()
identity_journal=t['TradeJournalService'](identity_store,now_factory=lambda:datetime(2026,10,28,9,tzinfo=t['TZ']))
t['import_ready'](identity_journal,t['bundle']([t['fill']('canonical-old','2026-10-19','buy',100,10,100,broker_execution_id='1234')]))
identity_args=deepcopy(inputs['identity_parse'])
links=identity_args.pop('identity_links')
identity_parsed=parse_file(**identity_args)
assert not identity_journal.preview_import(identity_parsed['payload'])['can_import']
identity_parsed['payload']['identity_links']=links
linked_preview=identity_journal.preview_import(identity_parsed['payload'])
assert linked_preview['can_import'] and linked_preview['counts']['duplicate_fills']==1
identity_body=deepcopy(inputs['identity_import'])
identity_digest=identity_body.pop('preview_digest')
linked=identity_journal.import_batch(identity_body,identity_digest)
assert linked['counts']['new_fills']==0
assert identity_journal.list_fills()['total']==1
assert identity_journal.list_fills()['items'][0]['broker_execution_id']=='1234'
assert identity_journal.import_batch(identity_body,identity_digest)['idempotent']
assert all(name.startswith('trade_journal_') for name,_,_ in identity_store.writes)
print('task roots/CAS/fingerprint and actual CSV/mapping/preview/import contract passed')
`],{input:JSON.stringify({resolution:taskBody,parse:parsedBody,import:importBody,match:executionBody,unlink:unlinkBody,review_draft:reviewDraftBody,review_confirm:reviewConfirmBody,identity_parse:identityParseBody,identity_import:identityImportBody}),encoding:'utf8',maxBuffer:4*1024*1024});assert.equal(workflows.status,0,workflows.stderr);
  for(const asset of ['app.js','trade-journal.js','trade-journal.css'])assert(htmlSource.includes('/static/'+asset+'?v='),'journal assets must retain explicit cache versions: '+asset);
  console.log('Trade journal UI + real domains passed: plans, immutable modes, source clocks, quantities/FIFO, statistical units, current-risk proposals/snapshots, root-cause task CAS, actual-file imports, explicit execution matching, process-review facts and no production writes.');
})().catch(error=>{console.error(error);process.exitCode=1;});
