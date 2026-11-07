// The independent chart supplement must never replace another date or erase a snapshot on error.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('shilun/static/review-dashboard.js', 'utf8');
let paints = [], requests = [], response;
const nodes = {
  reviewDate: {value:'2026-10-30'},
  reviewMarketFactsStatus: {textContent:''},
  'review-step-facts': {set outerHTML(html) {paints.push(html);}},
};
const context = {document:{getElementById:id=>nodes[id]},
  fetch:async url=>{requests.push(url);return {ok:true,json:async()=>response};}};
context.window = context;
vm.runInNewContext(source.replace('    window[handlerName("loadReviewDashboard")] =',
  '    if (isIndex) window.marketFactsTest = {loadMarketFacts};\n    window[handlerName("loadReviewDashboard")] ='), context);
const snapshot = {analysis_date:'2026-10-30',market:{volume:{current_yi:100},breadth:{up_count:1,down_count:2,flat_count:0}}};
const original = JSON.stringify(snapshot);
const facts = {status:'ready',analysis_date:'2026-10-30',market:{
  volume:{current_yi:18000,series:[]},breadth:{distribution:{bins:[]},markets:[{
    key:'ALL',name:'全市场',up_count:3,down_count:4,flat_count:0,
    amount_yi:18000,amount_change_yi:1200,expansion_threshold_yi:1000,expanded:true,
  }]},
}};
(async()=>{
  response = facts;
  await context.marketFactsTest.loadMarketFacts(snapshot);
  assert.equal(paints.length,1);
  assert(paints[0].includes('18,000'));
  assert(paints[0].includes('补充统计截至 2026-10-30'));
  assert.equal(JSON.stringify(snapshot),original,'frozen snapshot stays unchanged');
  response = {...facts,analysis_date:'2026-10-29'};
  await context.marketFactsTest.loadMarketFacts(snapshot);
  assert.equal(paints.length,1,'wrong-date response cannot repaint');
  assert(nodes.reviewMarketFactsStatus.textContent.includes('保留原快照'));
  context.fetch = async()=>{throw new Error('offline');};
  await context.marketFactsTest.loadMarketFacts(snapshot);
  assert.equal(paints.length,1,'read failure cannot erase existing chart');
  assert(nodes.reviewMarketFactsStatus.textContent.includes('暂不可用'));
  let resolve;
  context.fetch = async()=>({ok:true,json:()=>new Promise(r=>{resolve=r;})});
  const pending = context.marketFactsTest.loadMarketFacts(snapshot);
  await new Promise(setImmediate);
  nodes.reviewDate.value = '2026-10-29';
  resolve(facts);
  await pending;
  assert.equal(paints.length,1,'changing selection rejects in-flight prior-date result');
  assert(requests.every(url=>url.startsWith('/api/v1/review/market-facts?date=')));
  console.log('market supplement: same-date merge, immutable snapshot, error retention and date race passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
