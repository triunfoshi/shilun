// Latest overseas quotes keep their actual sessions and never inherit the selected A-share date.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = {
  reviewDate: {value:'2026-10-30'},
  reviewOverseasRoot: {innerHTML:'',setAttribute(){}},
  reviewOverseasStatus: {textContent:''},
  reviewOverseasRefresh: {disabled:false,textContent:''},
};
let calls = 0;
let data = {available:true,us_session:'2026-10-30',hk_session:'2026-11-02',
  us_label:'美股 2026-10-30 收盘',hk_label:'港股 2026-11-02 收盘',source_label:'腾讯行情',
  fetched_at:'2026-11-02T17:39:57+08:00',indices:[
    {name:'标普500',region:'美股',ticker:'INX',price:100,change_pct:1,session:'2026-10-30'},
    {name:'恒生指数',region:'港股',ticker:'HSI',price:200,change_pct:-1,session:'2026-11-02'},
  ],mag7:[],ai_hard_tech:[]};
const context = {document:{getElementById:id=>nodes[id]},AbortController,setTimeout,clearTimeout,
  fetch:async()=>{calls++;return {ok:true,json:async()=>data};}};
context.window = context;
vm.runInNewContext(fs.readFileSync('shilun/static/review-overseas.js','utf8'),context);
(async()=>{
  await context.loadReviewOverseas({force:true});
  assert(nodes.reviewOverseasRoot.innerHTML.includes('美股 2026-10-30 收盘'));
  assert(nodes.reviewOverseasRoot.innerHTML.includes('港股 2026-11-02 收盘'));
  assert(nodes.reviewOverseasStatus.textContent.includes('所选复盘日 2026-10-30'));
  assert(nodes.reviewOverseasStatus.textContent.includes('不是该日历史回放'));
  const firstCalls = calls;
  nodes.reviewDate.value = '2026-09-21';
  await context.loadReviewOverseas();
  assert.equal(calls,firstCalls,'changing the comparison date can reuse latest quotes');
  assert(nodes.reviewOverseasStatus.textContent.includes('所选复盘日 2026-09-21'));
  assert(!nodes.reviewOverseasRoot.innerHTML.includes('2026-09-21'),'quote sessions must not be backdated');
  data = {...data,us_session:'2026-10-30',hk_session:'2026-10-30',
    indices:[{name:'<img src=x onerror=1>',region:'美股',price:100,change_pct:0,session:'2026-10-30'}]};
  nodes.reviewDate.value = '2026-10-30';
  await context.loadReviewOverseas({force:true});
  assert(nodes.reviewOverseasStatus.textContent.includes('来源仍为最新报价'));
  assert(nodes.reviewOverseasRoot.innerHTML.includes('&lt;img'));
  assert(!nodes.reviewOverseasRoot.innerHTML.includes('<img'));
  context.fetch = async()=>({ok:false,status:503});
  await context.loadReviewOverseas({force:true});
  assert(nodes.reviewOverseasStatus.textContent.includes('HTTP 503'));
  assert(nodes.reviewOverseasRoot.innerHTML.includes('暂无可核验报价'));
  assert(!nodes.reviewOverseasRoot.innerHTML.includes('行情场次待确认'));
  assert(!nodes.reviewOverseasRoot.innerHTML.includes('标普500'));
  assert.equal(nodes.reviewOverseasRefresh.disabled,false);
  console.log('overseas: actual session dates, latest/history distinction, cached date changes and clear failure state passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
