const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('shilun/static/journal-workspace.js','utf8');
function node() {return {innerHTML:'',textContent:'',dataset:{},disabled:false,isConnected:true,setAttribute(k,v){this[k]=v;},querySelector(){return null;},querySelectorAll(){return []}};}
function setup(fetcher=async()=>({ok:true,json:async()=>({})})) {
  const nodes={}; for(const id of ['journalWorkspaceOverview','journalWorkspaceVerification','journalMarketCheckResult','journalMarketCheckStatus','journalConditionStatus','journalRuleComparisonStatus','journalRuleComparisonResult'])nodes[id]=node();
  const save=node();nodes.journalWorkspaceVerification.querySelector=selector=>selector.includes('save-check')?save:null;
  const handlers={};const context={console,URLSearchParams,document:{getElementById:id=>nodes[id],addEventListener:(name,fn)=>handlers[name]=fn},fetch:fetcher};context.window=context;vm.runInNewContext(source,context);
  return {ui:context.JournalWorkspace,context,nodes,save,handlers};
}
const decision={decision_id:'d1',revision:2,action:'买入',reason:'原买入依据',expectation:'期待次日反弹<&>',evidence_class:'retrospective_recall',observed_at:'2026-10-23T09:36:00+08:00',risk_constraints:{quantity:1000},fill_ids:['f1'],definition_refs:[{id:'mode.user.a',rule_version:'mode-card-v1'}],frozen_plan_ref:{plan_id:'before',revision:1}};
const detail={episode:{episode_id:'ep1',name:'风华<script>',ticker:'000636.SZ'},decisions:[decision],fills:[{fill_id:'f1',side:'buy',quantity:1000,price:38.41,date:'2026-10-23'}]};
const condition={target_date:'2026-10-26',metric:'close_return_pct',baseline:'entry_price',operator:'>=',target:'2',baseline_fill_id:'f1',original_text:'期待次日反弹<&>',revision_reason:'补充比较基准与时限'};
function form(id,values={}) {return {id,isConnected:true,elements:Object.entries(values).map(([name,value])=>({name,value})),setAttribute(){},querySelectorAll(){return []}};}

test('explicit fee and slippage preserve zero but reject missing assumptions',()=>{
  const {ui}=setup();assert.deepEqual(JSON.parse(JSON.stringify(ui.buildRulePayload({sell_fee_bps:'0',slippage_bps:'0'}))),{rule:{kind:'next_trading_day_close',sell_fee_bps:0,slippage_bps:0}});
  for(const bad of ['',undefined,null,'NaN','Infinity'])assert.throws(()=>ui.buildRulePayload({sell_fee_bps:bad,slippage_bps:'0'}),/明确填写/);
  assert.throws(()=>ui.buildRulePayload({sell_fee_bps:'2',slippage_bps:'-1'}),/不能为负/);
});
test('condition supplement preserves original decision and exact version but removes inherited ex ante claim',()=>{
  const {ui}=setup(),original=JSON.stringify(decision),payload=ui.buildConditionPayload(decision,condition);
  assert.equal(JSON.stringify(decision),original);assert.equal(payload.decision_id,'d1');assert.equal(payload.expected_revision,2);assert.equal(payload.evidence_class,'retrospective_recall');assert.equal(payload.expectation.target,2);assert.equal(payload.expectation.operator,'>=');assert.equal(payload.expectation.target_date,'2026-10-26');assert.equal(payload.expectation.baseline_fill_id,'f1');assert.equal(payload.expectation.text,decision.expectation);assert.equal(payload.reason,decision.reason);assert.equal(payload.definition_refs[0].rule_version,'mode-card-v1');assert.equal(payload.frozen_plan_ref,null);assert.equal(payload.frozen_plan,null);assert.equal(payload.verification_at,'2026-10-26T15:30:00+08:00');
  assert.match(ui.buildConditionPayload(decision,{...condition,revision_reason:''}).revision_reason,/补充.*目标/);assert.throws(()=>ui.buildConditionPayload(decision,{...condition,target:''}),/阈值/);
});
test('overview and evidence escape source text and never invent zero tasks',()=>{
  const {ui}=setup();const empty=ui.renderOverview(null);assert.match(empty,/data-jw-action="start"/);assert.doesNotMatch(empty,/共 0 项/);
  const html=ui.renderOverview({total:12,items:[{task_id:'t"x',episode_id:'e1',name:'<img onerror=alert(1)>',title:'风险<&>'}]});assert.match(html,/筛选范围共 12 项/);assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img/);
  const result=ui.renderMarketResult({status:'unknown',evidence_class:'retrospective_recall',actual:{value:0},reasons:['<script>x</script>'],sources:[{data:'</pre><img>'}]});assert.match(result,/暂不能判断/);assert.match(result,/事后补记/);assert.match(result,/0\.00/);assert.match(result,/&lt;\/pre&gt;/);assert.doesNotMatch(result,/<script>/);
});
test('rule comparison labels historical reference and missing values stay unknown',()=>{
  const {ui}=setup();const unknown=ui.renderComparisonResult({status:'unknown',reasons:['费用未知'],actual:{},reference:{}});assert.match(unknown,/暂不能完成收益对照/);assert.match(unknown,/—%/);assert.match(unknown,/不证明能够成交或模式已验证/);
  const html=ui.renderComparisonResult({status:'compared',actual:{net_pnl:100,return_pct:1,buy_cost:10000,quantity:1000},reference:{net_pnl:200,return_pct:2,exit_date:'2026-10-26',estimated_sell_fee:0,slippage_cost:0},difference_pp:-1});assert.match(html,/-1\.00 个百分点/);assert.match(html,/1,000 股/);assert.match(html,/历史规则回放/);
});
test('selecting a new episode ignores a late market response and cannot save it',async()=>{
  let resolve;const pending=new Promise(r=>resolve=r),calls=[];
  const env=setup(async(url,options)=>{calls.push({url,options});return pending;});const {ui,handlers,nodes,save}=env;
  ui.setEpisode(detail);const submit=handlers.submit({target:form('journalMarketCheckForm'),preventDefault(){}});
  ui.setEpisode({...detail,episode:{...detail.episode,episode_id:'ep2'}});resolve({ok:true,json:async()=>({status:'supported',actual:3,preview_digest:'old'})});await submit;
  assert.equal(ui.state.market,null);assert.equal(save.disabled,true);assert.equal(nodes.journalMarketCheckResult.innerHTML,'');assert.equal(calls.length,1);assert.equal(JSON.parse(calls[0].options.body).decision_revision,2);
});
test('market save retains preview as-of and digest and does not send new conditions',async()=>{
  const calls=[],preview={status:'supported',target_date:'2026-10-26',actual:3,preview_digest:'frozen-preview',preview_request:{decision_revision:2,as_of:'2026-11-02T21:00:00+08:00'}};
  const env=setup(async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>url.endsWith('/preview')?preview:{saved:true}};});const {ui,handlers,context,save}=env;let refreshed='';context.TradeJournal={selectEpisode:async id=>{refreshed=id;}};
  ui.setEpisode(detail);await handlers.submit({target:form('journalMarketCheckForm'),preventDefault(){}});assert.equal(save.disabled,false);
  const saveForm=form('journalMarketCheckForm');env.nodes.journalMarketCheckForm=saveForm;
  await handlers.click({target:{closest:()=>({dataset:{jwAction:'save-check'}})}});
  const body=JSON.parse(calls[1].options.body);assert.deepEqual(body,{decision_revision:2,as_of:'2026-11-02T21:00:00+08:00',decision_id:'d1',preview_digest:'frozen-preview'});assert.equal(refreshed,'ep1');assert.equal(body.expectation,undefined);
});
test('no polling or data fetch on workspace mount and entry',()=>{
  let calls=0;const {ui}=setup(async()=>{calls++;throw new Error('unexpected');});ui.enter();ui.setEpisode(detail);ui.enter();assert.equal(calls,0);
});
test('missing plan is unknown rather than a discipline violation; selected HTML escaped',()=>{
  const {ui,nodes}=setup();ui.setEpisode(detail);assert.match(nodes.journalWorkspaceVerification.innerHTML,/风华&lt;script&gt;/);assert.match(nodes.journalWorkspaceVerification.innerHTML,/当时未设|未设计划|没有记录/);assert.doesNotMatch(nodes.journalWorkspaceVerification.innerHTML,/自动判定违规/);ui.setEpisode(null);assert.match(nodes.journalWorkspaceVerification.innerHTML,/选择一轮交易/);
});
test('saved comparison operators match numeric owner and market facts remain readable',()=>{
  const {ui}=setup();assert.throws(()=>ui.buildConditionPayload(decision,{...condition,operator:'gte'}),/选项无效/);
  const output=ui.renderMarketResult({status:'supported',evidence_class:'retrospective_recall',expectation:{metric:'close_return_pct',baseline:'entry_price',operator:'>=',target:2,target_date:'2026-10-26',text:'次日反弹'},actual:{metric:'close_return_pct',value:2.3,ticker:'000636.SZ',close:10.23},baseline:{kind:'entry_price',value:10},target_date:'2026-10-26'});
  assert.match(output,/2\.30%/);assert.match(output,/关联买入价/);assert.match(output,/收盘涨幅 &gt;= 2\.00%/);assert.doesNotMatch(output,/"ticker"/);
});
test('changing filter scope invalidates a late task response',async()=>{
  let resolve;const pending=new Promise(r=>resolve=r);const {ui,handlers,nodes}=setup(async()=>pending);
  const loading=ui.loadOverviewTasks();handlers.change({target:{name:'query',closest:()=>({id:'tradeJournalFilters'})}});
  resolve({ok:true,json:async()=>({total:42,items:[{name:'旧筛选股票',task_id:'old',episode_id:'old'}]})});await loading;
  assert.equal(ui.state.tasks,null);assert.doesNotMatch(nodes.journalWorkspaceOverview.innerHTML,/旧筛选股票|共 42 项/);
});

test('guided home has one start action and starting does not create or fetch records',()=>{
  let requests=0;const {ui,nodes}=setup(async()=>{requests++;return {ok:true,json:async()=>({})};});
  nodes.tradeJournalRoot=node();ui.enter();
  assert.equal(nodes.tradeJournalRoot.dataset.jwStage,'home');
  const home=nodes.journalWorkspaceOverview.innerHTML;
  assert.equal((home.match(/data-jw-action="start"/g)||[]).length,1);
  assert(!home.includes('data-jw-action="tasks"'));
  ui.showStage('pick',{scroll:false});assert.equal(nodes.tradeJournalRoot.dataset.jwStage,'pick');assert.equal(requests,0);
});
test('one selected round drives notes and results; refreshing same round preserves the active step',()=>{
  const {ui,nodes}=setup();nodes.tradeJournalRoot=node();ui.showStage('pick',{scroll:false});ui.setEpisode(detail);
  assert.equal(ui.state.stage,'notes');assert.equal(nodes.tradeJournalRoot.dataset.jwStage,'notes');
  ui.showStage('check',{scroll:false});ui.setEpisode(null);ui.setEpisode(detail);assert.equal(ui.state.stage,'check');
  ui.setEpisode(null);ui.setEpisode({...detail,episode:{...detail.episode,episode_id:'ep2'}});assert.equal(ui.state.stage,'notes');
  assert.equal(ui.state.market,null);assert.equal(ui.state.comparison,null);
});
test('full archive and legacy section links require explicit navigation',()=>{
  const {ui,nodes}=setup();nodes.tradeJournalRoot=node();ui.setEpisode(detail);
  assert.equal(nodes.tradeJournalRoot.dataset.jwArchive,'false');
  ui.showStage('archive',{scroll:false});assert.equal(nodes.tradeJournalRoot.dataset.jwArchive,'true');
  assert(ui.openSection('journalWorkspaceVerification'));assert.equal(ui.state.stage,'check');assert.equal(ui.state.archive,false);
  assert(ui.openSection('journalRecords'));assert.equal(ui.state.stage,'pick');
  assert.equal(ui.openSection('journalAccountTools'),false);
});


test('goals bind a specific lot, separate additions and label post-hoc targets honestly',()=>{
  const {ui}=setup();
  const original={...decision,expectation:{metric:'close_price',target:40,target_date:'2026-10-26',scope_fill_id:'f1',trading_day_offset:1},definition_refs:[{id:'evidence.retrospective_recall'},{id:'mode.user.a',rule_version:'v1'}]};
  const p=ui.buildConditionPayload(original,{...condition,baseline_fill_id:'f2',metric:'high_return_pct',expectation_origin:'post_hoc_review',revision_reason:''});
  assert.equal(p.decision_id,undefined);assert.deepEqual(Array.from(p.fill_ids),['f2']);assert.equal(p.evidence_class,'historical_reconstruction');assert.equal(p.expectation.origin,'post_hoc_review');assert.equal(p.expectation.scope_fill_id,'f2');assert.equal(p.expectation.trading_day_offset,undefined);assert.equal(p.definition_refs.length,1);assert.equal(original.expectation.scope_fill_id,'f1');
  assert.throws(()=>ui.buildConditionPayload(decision,{...condition,baseline_fill_id:''}),/哪批买入/);
});
test('primary fields are expanded, missing plans are not failed expectations, and threshold has units',()=>{
  const {ui,nodes}=setup();ui.setEpisode({...detail,decisions:[{...decision,expectation:'当时没有具体预期，只有个期望次日能够获得5%以上的幻想'}]});
  const html=nodes.journalWorkspaceVerification.innerHTML;
  assert.match(html,/当时未设明确预期，不能判断/);assert.match(html,/目标涨幅（%）/);assert.match(html,/填写百分比/);assert.match(html,/先保存上面的核对目标/);assert.doesNotMatch(html,/<details[^>]*class="jw-condition/);assert.doesNotMatch(html,/本次补充理由/);assert.match(html,/研究参考：假设次日收盘卖出/);
});
test('market opportunity and actual sell price have independent conclusions',()=>{
  const {ui}=setup();
  const html=ui.renderMarketResult({status:'supported',metric:'high_return_pct',actual:{metric:'high_return_pct',value:3},expectation:{origin:'post_hoc_review'},execution_evaluation:{status:'refuted',reason:'卖出价未满足',fills:[{executed_at:'2026-10-27T09:42:00+08:00',price:17.4,quantity:100,condition_met:false}]},price_observations:[{label:'全日最高价',price:18,comparison_value:3,status:'supported'}]});
  assert.match(html,/行情达到目标/);assert.match(html,/已导入卖出价未达到目标/);assert.match(html,/现在设定的复盘参考/);assert.match(html,/具体触达分钟/);assert.match(html,/09:42/);
});
test('editing unsaved target blocks checking the old target',async()=>{
  let requests=0;const {ui,handlers,nodes}=setup(async()=>{requests++;return {ok:true,json:async()=>({})};});
  ui.setEpisode(detail);handlers.input({target:{closest:()=>({id:'journalConditionForm'})}});
  await handlers.submit({target:form('journalMarketCheckForm'),preventDefault(){}});assert.equal(requests,0);assert.match(nodes.journalMarketCheckStatus.textContent,/先保存/);
});

test('statement dates control initial folding; choosing either action folds without claiming complete coverage',async()=>{
  const {ui,nodes,handlers,context}=setup();ui.setEpisode(detail);
  const c={as_of_date:'2026-11-03',episode_start_date:'2026-10-23',freshness:{account_alias:'a',latest_fill_date:'2026-10-26',latest_imported_at:'2026-10-29',latest_expected_trade_date:'2026-11-03',date_is_current:false}};
  ui.state.reviewContext=c;assert.match(ui.renderContext(),/id="journalReviewContext" open/);
  nodes.journalReviewContext={open:true};
  await handlers.click({target:{closest:()=>({dataset:{jwAction:'continue-recorded'}})}});
  assert.equal(nodes.journalReviewContext.open,false);assert.doesNotMatch(ui.renderContext(),/id="journalReviewContext" open/);assert.match(ui.renderContext(),/日期相同仍不能证明/);
  ui.state.reviewContext={...c,freshness:{...c.freshness,latest_imported_at:'2026-11-03'}};
  assert.match(ui.renderContext(),/id="journalReviewContext" open/,'new source date invalidates prior dismissal');
  let opened='';context.openJournalSection=id=>{opened=id;};nodes.journalReviewContext.open=true;
  await handlers.click({target:{closest:()=>({dataset:{jwAction:'import'}})}});
  assert.equal(nodes.journalReviewContext.open,false);assert.equal(opened,'tradeJournalSourceSection');
  ui.state.reviewContext={...c,freshness:{...c.freshness,latest_fill_date:'2026-11-03',date_is_current:true}};
  assert.doesNotMatch(ui.renderContext(),/id="journalReviewContext" open/);assert.match(ui.renderContext(),/日期已到最新交易日/);
});
test('target timing is stated as the goal check, rather than an unexplained quote selector',()=>{
 const {ui,nodes}=setup();ui.setEpisode(detail);const html=nodes.journalWorkspaceVerification.innerHTML;
 assert.match(html,/目标在什么时候达到才算数/);assert.match(html,/盘中最高涨到目标/);assert.match(html,/收盘时涨幅仍达到目标/);assert.match(html,/这里只核对行情/);assert.doesNotMatch(html,/看哪个价格/);
});


test('saved notes have a direct completion entry with explicit conditions independent of future results',async()=>{
 const {ui,context,handlers,nodes}=setup();nodes.tradeJournalRoot=node();
 ui.setEpisode({...detail,review_progress:{status:'in_progress',has_saved_notes:true}});
 const html=nodes.journalWorkspaceOverview.innerHTML;
 assert.match(html,/最后一步：确认本次复盘/);assert.equal((html.match(/data-jw-action="process"/g)||[]).length,1);
 for(const text of ['核对已导入成交和已填想法','写一句总结并勾选确认','尚未清仓','未来预期未到期','未知项未补齐'])assert(html.includes(text));
 let edited=0,loaded=0;context.TradeJournal={editProcessReview:async()=>{edited++;},loadProcessReview:async()=>{loaded++;}};
 await handlers.click({target:{closest:()=>({dataset:{jwAction:'process'}})}});
 assert.equal(edited,1);assert.equal(loaded,0);assert.equal(nodes.tradeJournalRoot.dataset.jwFocus,'process');
 assert.match(nodes.journalWorkspaceOverview.innerHTML,/确认本次复盘/);assert.doesNotMatch(nodes.journalWorkspaceOverview.innerHTML,/data-jw-action="process"/);
 ui.setReviewProgress({status:'reviewed'});ui.showStage('notes',{scroll:false});
 assert.match(nodes.journalWorkspaceOverview.innerHTML,/本次复盘已完成/);assert.match(nodes.journalWorkspaceOverview.innerHTML,/查看已确认复盘/);
 ui.setReviewProgress({status:'needs_update'});assert.match(nodes.journalWorkspaceOverview.innerHTML,/重新确认本次复盘/);
 ui.showStage('pick',{scroll:false});assert.doesNotMatch(nodes.journalWorkspaceOverview.innerHTML,/data-jw-action="process"/);
});
