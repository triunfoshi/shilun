/* Run with Playwright on NODE_PATH and BOARD_TEST_URL pointing at local Shilun.
 * All mutation/AI responses are isolated browser fixtures; no model is called. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const base = process.env.BOARD_TEST_URL || 'http://127.0.0.1:18000';
const stock = {code:'000001',ticker:'000001.SZ',name:'测试股份',price:11,pct:10,amount:2e9,float_cap:1e10,seal_time:'093000',break_count:1,industry:'电子',reason:'<img src=x onerror="window.injected=1">',boards:1};
let saved = [], aiMode = 'complete', aiCalls = 0;
const first = day => ({available:true,date:day,total_zt:2,first_count:2,stocks:[stock,{...stock,code:'000002',ticker:'000002.SZ',name:'第二只'}],ai:{ready:true,model:'fixture-model'},notes:saved});
const wait = ms => new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const unrelatedWrites=[];
  await page.route('**/api/**',route=>{
   if(!['GET','HEAD'].includes(route.request().method())) {
    unrelatedWrites.push(route.request().url());
    return route.fulfill({status:200,json:{status:'isolated-test',items:[]}});
   }
   return route.continue();
  });
  await page.route('**/api/v1/review/first-board*',async route=>{
   const url=new URL(route.request().url()), day=url.searchParams.get('date')||'2026-09-14';
   if (day==='2026-09-10') await wait(250);
   if (day==='2026-09-09') return route.fulfill({status:503,json:{detail:'来源暂不可用'}});
   await route.fulfill({json:first(day)});
  });
  await page.route('**/api/v1/review/research/analyze',async route=>{
   aiCalls++;
   if(aiMode==='hold') {await wait(800); return route.abort().catch(()=>{});}
   const body=route.request().postDataJSON();
   const note={...body,kind:'firstboard',name:'测试股份',model:'fixture-model',text:'## 已知事实\n成交额20亿。<script>window.injected=1</script>',completed:true};
   const events=[{type:'start'},{type:'delta',text:note.text}];
   if(aiMode==='complete') { saved=[...saved.filter(n=>n.code!==body.code),note]; events.push({type:'done',note}); }
   const sse=events.map(e=>'data: '+JSON.stringify(e)+'\r\n\r\n').join('');
   await route.fulfill({status:200,contentType:'text/event-stream',body:sse});
  });
  await page.goto(base+'/ui',{waitUntil:'domcontentloaded'});
  await page.locator('#nav-firstboard').click();
  await page.locator('#firstboardRoot .br-stock').first().waitFor();
  assert.equal(await page.locator('#firstboardRoot img').count(),0);
  await page.locator('[data-action="toggle"][data-code="000001"]').click();
  await page.waitForFunction(()=>document.querySelector('#br-ai-status-000001').textContent.includes('已完成'));
  assert.equal(await page.evaluate(()=>window.injected),undefined);
  assert.equal(await page.locator('#br-analysis-000001 script').count(),0);
  const download=page.waitForEvent('download');
  await page.locator('[data-action="export"][data-code="000001"]').click();
  assert.equal((await download).suggestedFilename(),'2026-09-14-000001-首板研究.md');
  // Batch skips completed notes; a reload restores them from the shared API.
  await page.locator('#br-batch').click();
  await page.waitForFunction(()=>document.querySelector('#br-batch-status').textContent.includes('完成 1 只'));
  assert.equal(aiCalls,2);
  await page.evaluate(()=>BoardResearch.load('firstboard'));
  assert.equal(await page.locator('button[data-action="toggle"]').filter({hasText:'查看研究'}).count(),2);
  aiMode='incomplete';
  await page.locator('[data-action="toggle"][data-code="000001"]').click();
  if(!await page.locator('#br-analysis-000001').isVisible()) await page.locator('[data-action="toggle"][data-code="000001"]').click();
  await page.locator('[data-action="analyze"][data-code="000001"]').click();
  await page.waitForFunction(()=>document.querySelector('#br-ai-status-000001').textContent.includes('未完整完成'));
  assert.equal(saved.length,2);
  aiMode='hold';
  await page.locator('[data-action="analyze"][data-code="000001"]').click();
  await page.locator('#br-stop').click();
  await page.waitForFunction(()=>document.querySelector('#br-ai-status-000001').textContent.includes('中止'));
  // An older slow response must not overwrite the newer date.
  await page.evaluate(()=>{
   document.querySelector('#firstboardDate').value='2026-09-10';BoardResearch.load('firstboard');
   document.querySelector('#firstboardDate').value='2026-09-11';BoardResearch.load('firstboard');
  });
  await page.waitForFunction(()=>document.querySelector('#firstboardStatus').textContent.includes('2026-09-11'));
  await wait(300);
  assert.equal(await page.locator('#firstboardDate').inputValue(),'2026-09-11');
  // A failed new date cannot continue to display the old date's stocks.
  await page.locator('#firstboardDate').fill('2026-09-09');
  await page.locator('#firstboardDate').dispatchEvent('change');
  await page.locator('#firstboardRoot .is-error').waitFor();
  assert.equal(await page.locator('#firstboardRoot .br-stock').count(),0);
  await page.locator('#firstboardDate').fill('2026-09-14');await page.locator('#firstboardDate').dispatchEvent('change');
  await page.locator('#firstboardRoot .br-stock').first().waitFor();
  for(const width of [320,375,414,768,1440]) {
   await page.setViewportSize({width,height:1000});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`overflow ${width}`);
  }
  assert.deepEqual(errors,[]);
  assert.deepEqual(unrelatedWrites,[],'Opening research must not write the shared watchlist');
  console.log('First-board UI: completed/reloaded/batch/stop/incomplete/export/date race/failure/XSS/responsive passed.');
  // Effects uses actual API data; requests are read-only and inspectable.
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#nav-boardeffects').click();
  await page.locator('#boardeffectsRoot article').first().waitFor({timeout:90000});
  assert.deepEqual(await page.locator('#boardeffectsDays option').allTextContents(),['近30个交易日','近20个交易日']);
  await page.locator('#boardeffectsMetric').selectOption('median');
  assert.match(await page.locator('#boardeffectsRoot svg').first().getAttribute('aria-label'),/中位数/);
  await page.locator('#boardeffectsRoot .br-card-details > summary').first().click();
  await page.locator('details[data-samples="first"] summary').click();
  await page.locator('#br-samples-first tbody tr').first().waitFor();
  assert.ok(await page.locator('#br-samples-first tbody tr').count()>0);
  for(const width of [320,375,414,768,1440]) {
   await page.setViewportSize({width,height:1000});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`effects overflow ${width}`);
  }
  console.log('Effects UI: window choices/metric switch/sample disclosure/responsive passed.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
