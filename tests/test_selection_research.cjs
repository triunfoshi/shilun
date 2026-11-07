/* Offline renderer and request-state checks; no browser, live writes or orders. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window:{},document:{addEventListener:()=>{}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('shilun/static/selection-research.js','utf8'),context);
const ui = context.window.SelectionResearch;
const day='2026-09-21';
const sector=(code,flow,extra={})=>({sector_code:code,sector_name:code,member_count:5,metrics:{main_net_inflow:flow,large_order_net:flow*2,amount:100000,moneyflow_asof_date:day,moneyflow_is_stale:false,...extra}});
const data={analysis_date:day,all_sectors:[sector('negative',-3),sector('highest',50),sector('zero',0),sector('stale',100,{moneyflow_is_stale:true}),sector('old',200,{moneyflow_asof_date:'2026-09-18'}),sector('missing',300,{large_order_net:null}),sector('empty',400,{main_net_inflow:''})]};
assert.deepEqual(Array.from(ui.capitalRows(data),s=>s.sector_code),['highest','zero','negative']);
assert.equal(ui.capitalRows({analysis_date:day,all_sectors:[{...sector('fifty',1),member_count:50},{...sector('broad',999),member_count:51}]}).length,2,'large real topics remain eligible with known membership');
assert.equal(ui.capitalRows(data,'amount')[0].metrics.amount,100000);
assert(ui.capital(data).includes('1.00亿'),'daily amount is thousands, not yuan');
assert(ui.capital(data).includes('0.01亿'),'moneyflow is ten-thousand yuan');
const row={ticker:'000001.SZ',name:'<img src=x onerror=1>',sector_name:'电子',signal:{ma5:10,ma20:9,ma5_slope:.3,breakout_level:11,close:12,breakout_pct:.09,body_ratio:.8,close_position:.9},amount_yi:2,net_inflow_yi:0};
for(const [strategy,expected,absent] of [['red_k_net_inflow','阳线实体占比','MA5上行幅度'],['structure_repair_520','MA5上行幅度','突破幅度'],['weekly_breakout','突破幅度','阳线实体占比']]) {
 const html=ui.research({strategy_id:strategy,candidates:[row],total_count:1,analysis_date:day});
 assert(html.includes(expected)); assert(!html.includes(absent)); assert(!html.includes('<img')); assert(html.includes('&lt;img'));
 assert(!html.includes('undefined')); assert(!html.includes('NaN'));
}
assert(ui.research({strategy_id:'red_k_net_inflow',candidates:[row]}).includes('0.00亿'));
assert(ui.research({strategy_id:'red_k_net_inflow',candidates:[{...row,net_inflow_yi:null}]}).includes('data-label="净流入">—'));
assert(ui.research({status:'not_materialized',strategy_id:'red_k_net_inflow'}).includes('尚未生成'));
assert(ui.research({status:'ok',strategy_id:'weekly_breakout',candidates:[]}).includes('没有满足条件'));
const excluded=ui.excluded([{...row,selection_gate:{failure_reasons:['成交额不足','均线不满足']}}],'');
assert(!excluded.includes('selection-excluded" open'));assert(excluded.includes('成交额不足；均线不满足'));
assert(!excluded.includes('data-label="买点"'));assert(!excluded.includes('0.0</strong>'));
assert(ui.excluded([row],row.ticker).includes('selection-excluded" open'));
assert(ui.scope({selection_scope:{sector_count:2,sector_names:['瓷砖','芯片']}},'ma5_v0_2').includes('前 2 个板块'));
assert(ui.scope({},'ma5_v0_2').includes('共享上游结果'));
// Request cancellation and cache keys must include benchmark as well as date.
const app=fs.readFileSync('shilun/static/app.js','utf8');
const detailContext={isOversoldFirstBoardStrategy:()=>false,candidateIsExcluded:()=>true,
 selectedTrendPool:{},normalizeTrendPoolTicker:t=>t,candidateSelectionGate:c=>c.selection_gate,
 candidateExclusionLabel:()=> '条件未满足',escapeHtml:String,formatCandidatePrice:v=>String(v??'—'),colorPercent:()=> '—'};
vm.createContext(detailContext);
vm.runInContext(app.slice(app.indexOf('function renderCandidateDetailPanel('),app.indexOf('function renderCandidates(')),detailContext);
const failedDetail=detailContext.renderCandidateDetailPanel({...row,selection_gate:{criteria:[{label:'成交额',actual:'7亿',passed:false}]},ranking_status:'excluded',buy_point:999});
assert(failedDetail.includes('逐项核对')); assert(!failedDetail.includes('计划买点')); assert(!failedDetail.includes('最终评分')); assert(!failedDetail.includes('加入持仓'));
const nodes={globalDate:{value:day},globalBenchmark:{value:'000001.SH'},selectionResearchDate:{},candidateStrategySelect:{value:'ma5_v0_2'}};
let count=0,rendered;
const requests=[];
const pool={window:{},document:{getElementById:id=>nodes[id]},candidateRowsByStrategy:{},candidateEvidenceByStrategy:{},selectionQueryDate:'',selectionPoolRequestVersion:0,
 selectedCandidateStrategyId:()=>nodes.candidateStrategySelect.value,syncCandidateStrategyChrome:()=>{},isOversoldFirstBoardStrategy:()=>false,REVIEW_TACTICS:{},showHtml:()=>{},
 fetchJson:()=>{count++;return new Promise(resolve=>requests.push(resolve));},candidatePayloadRows:d=>d.candidates,renderCandidates:r=>{rendered=r;pool.candidateRowsByStrategy.ma5_v0_2=r;},escapeHtml:String,formatError:String};
vm.createContext(pool);
vm.runInContext(app.slice(app.indexOf('async function loadSelectionPool('),app.indexOf('async function precomputeSelectionPool(')),pool);
(async()=>{
 const old=pool.loadSelectionPool();nodes.globalBenchmark.value='000300.SH';const fresh=pool.loadSelectionPool();
 requests[1]({candidates:['fresh']});await fresh;requests[0]({candidates:['old']});await old;assert.deepEqual(rendered,['fresh']);
 await pool.loadSelectionPool();assert.equal(count,2,'same date/benchmark uses cached rows');
 nodes.globalDate.value='2026-09-18';const historical=pool.loadSelectionPool();assert.equal(count,3);requests[2]({candidates:[]});await historical;
 console.log('selection fields, escaping, null/zero, exclusions, fund dates/units, and cache race checks passed');
})().catch(error=>{console.error(error);process.exit(1);});

const fullScope=ui.scope({selection_scope:{kind:'full_market',universe_count:5579,liquidity:{market_amount_cny:2048133676508.85,label:'大盘不足2.1万亿：7日均额低于50亿'}}},'ma5_v0_2');
assert(fullScope.includes('全市场') && fullScope.includes('2.0481') && fullScope.includes('50亿'));
assert(!fullScope.includes('前 8 个板块'));
