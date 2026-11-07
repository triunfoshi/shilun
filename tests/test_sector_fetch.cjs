const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync(require('node:path').join(__dirname, '../shilun/static/app.js'), 'utf8');
const nodes = {globalDate:{value:'2026-09-21'}, globalBenchmark:{value:'000001.SH'},
  sectorPrecomputeButton:{disabled:false}};
let phase, html, calls;
const context = {
  AbortController, console, Date, encodeURIComponent,
  setTimeout:(fn,ms)=>setTimeout(fn,[1000,2000,5000].includes(ms)?0:ms), clearTimeout,
  setInterval, clearInterval, sectorRequestVersion:0, selectedSectorIndex:0,
  document:{getElementById:id=>nodes[id]}, escapeHtml:value=>String(value??'').replace(/</g,'&lt;'),
  startSectorProgress:()=>{}, failSectorProgress:(_v,p)=>{phase=p||'error';},
  startSectorPrecomputeProgress:()=>{}, finishSectorPrecomputeProgress:(_v,ok,p)=>{phase=ok?'complete':p;},
  completeSectorProgress:()=>{phase='complete';}, showHtml:(_id,value)=>{html=value;},
  syncBenchmarkSelects:()=>{},syncMarketDates:()=>{},renderSectorTrends:()=>'<p>已读取归档</p>',
  renderCandidates:()=>{}, DEFAULT_CANDIDATE_STRATEGY_ID:'test',
};
vm.createContext(context);
for(const [start,end] of [['const formatError =','function marketDataGapMessage('],
 ['async function fetchJson(', 'async function loadStatus('],
 ['async function runSectorTrends(', 'async function runTelegramText(']]) {
 vm.runInContext(script.slice(script.indexOf(start),script.indexOf(end)),context);
}
const response=(body,ok=true)=>({ok,text:async()=>JSON.stringify(body)});
const network=()=>new TypeError('Failed to fetch');
(async()=>{
 calls=0;
 context.fetch=async()=>{if(++calls<3)throw network();return response({ok:true});};
 assert.equal((await context.fetchSectorJson('/read')).ok,true);
 assert.equal(calls,3);
 calls=0;context.fetch=async()=>{calls++;throw network();};
 await assert.rejects(context.fetchSectorJson('/start',{method:'POST'}),/Failed to fetch/);
 assert.equal(calls,1,'never replay a POST automatically');
 calls=0;context.fetch=async()=>{calls++;return response({detail:'板块动向数据未预计算'},false);};
 await context.runSectorTrends();
 assert.equal(calls,1);assert.equal(phase,'empty');
 assert(html.includes('立即预计算'));assert(!html.includes('<h3>查询失败</h3>'));
 calls=0;context.fetch=async()=>{calls++;throw network();};
 await context.runSectorTrends();
 assert.equal(calls,3);assert.equal(phase,'connection');
 assert(html.includes('与本机后台的连接暂时中断'));assert(!html.includes('Failed to fetch'));
 // The timeout must cover reading the response body, not only its headers.
 context.fetch=async(_url,options)=>({ok:true,text:()=>new Promise((_resolve,reject)=>{
  options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})));
 })});
 await assert.rejects(context.fetchJson('/slow-body',{timeoutMs:5}),e=>e.name==='AbortError');
 let posts=0,polls=0;
 context.fetch=async(url)=>{
  if(url.includes('/start?')){posts++;return response({status:'queued',job_id:'a'.repeat(24)});}
  if(url.includes('/status/')){if(++polls<3)throw network();return response({status:'complete'});}
  return response({analysis_date:'2026-09-21',benchmark_ticker:'000001.SH',candidates:[]});
 };
 await context.runPrecomputeSectorsFromPage();
 assert.equal(posts,1);assert.equal(polls,3);assert.equal(phase,'complete');
 assert(html.includes('已读取归档'));assert.equal(nodes.sectorPrecomputeButton.disabled,false);
 context.fetch=async(url)=>{
  if(url.includes('/start?'))return response({status:'queued',job_id:'b'.repeat(24)});
  throw network();
 };
 await context.runPrecomputeSectorsFromPage();
 assert.equal(phase,'connection');assert(html.includes('后台计算可能仍在运行'));
 assert.equal(nodes.sectorPrecomputeButton.disabled,false);
 context.fetch=async()=>response({detail:'暂时无法确认板块任务提交状态'},false);
 await context.runPrecomputeSectorsFromPage();
 assert.equal(phase,'connection');assert(html.includes('提交状态'));
 console.log('sector fetch, missing cache, timeout body, and task recovery assertions passed');
})().catch(error=>{console.error(error);process.exit(1);});
