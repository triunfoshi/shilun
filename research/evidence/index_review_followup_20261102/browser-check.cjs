// Real local /dashboard + /market-facts responses; unrelated workspaces are isolated.
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('/Users/shibicheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out=__dirname;
(async()=>{const browser=await chromium.launch({headless:true,channel:'chrome'});
try{
 const page=await browser.newPage();const errors=[],writes=[],responses=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('response',async response=>{const url=new URL(response.url());if(['/api/v1/review/dashboard','/api/v1/review/market-facts','/api/v1/review/overseas'].includes(url.pathname)){
  let data;try{data=await response.json();}catch{}
  responses.push({url:url.pathname+url.search,status:response.status(),date:data?.analysis_date,supplement:data?.market_facts_supplement?.cache_status});
  if(url.pathname==='/api/v1/review/dashboard'&&data?.analysis_date==='2026-10-30')fs.writeFileSync(out+'/live-dashboard.json',JSON.stringify(data));
 }});
 await page.route('**/api/**',route=>{
 const req=route.request(),url=new URL(req.url());
 if(req.method()!=='GET'){writes.push(url.pathname);return route.fulfill({status:400,json:{detail:'Read-only verification'}});}
 if(['/api/v1/review/dashboard','/api/v1/review/market-facts','/api/v1/review/overseas'].includes(url.pathname))return route.continue();
 return route.fulfill({json:{status:'missing',dates:[],rows:[],data:[],items:[],candidates:[]}});
 });
 await page.goto('http://127.0.0.1:8000/ui',{waitUntil:'domcontentloaded'});
 await page.waitForSelector('.review-index-close',{timeout:60000});
 await page.waitForFunction(()=>document.getElementById('reviewCommandBar').getAttribute('aria-busy')==='false');
 // Reproduce the initially blank/latest date path that previously discarded the asynchronous supplement.
 await page.evaluate(()=>window.latestReviewDate());
 await page.waitForFunction(()=>/^\d{4}-\d{2}-\d{2}$/.test(document.getElementById('reviewDate').value));
 await page.waitForSelector('.review-histogram-bin',{timeout:65000});
 assert.equal(await page.locator('.review-market-breadth-table tbody tr').count(),4);
 await page.evaluate(async()=>{document.getElementById('reviewDate').value='2026-10-30';await window.loadReviewDashboard({force:true});});
 await page.waitForFunction(()=>document.querySelector('.review-volume-toolbar').innerText.includes('2026-10-30'),{timeout:65000});
 await page.waitForSelector('.review-volume-rule.is-ma60',{state:'attached',timeout:65000});
 assert.equal(await page.locator('.review-histogram-bin').count(),23);
 assert.equal(await page.locator('.review-market-breadth-table tbody tr').count(),4);
 assert.equal(await page.locator('.review-volume-bar').count(),60);
 assert.equal(await page.locator('.review-volume-rule').count(),3);
 assert.equal(await page.locator('#reviewVolumeDay').count(),0);
 assert.equal(await page.locator('.review-posture-evidence').count(),1);
 assert.equal(await page.locator('.review-index-evidence').count(),0);
 const rows=await page.locator('.review-market-breadth-table').innerText();
 assert(rows.includes('沪市')&&rows.includes('深市')&&rows.includes('全市场'));
 const layouts=[];
 for(const width of [320,375,414,768,1440,1920]){
  await page.setViewportSize({width,height:1000});
  await page.locator('.review-loop-nav a[href="#review-step-facts"]').click();
  await page.waitForTimeout(100);
  const measured=await page.evaluate(()=>{
   const canvas=document.querySelector('.review-volume-canvas').getBoundingClientRect();
   const dates=document.querySelector('.review-volume-dates').getBoundingClientRect();
   const bars=[...document.querySelectorAll('.review-volume-bar')].map(e=>e.getBoundingClientRect());
   const rules=[...document.querySelectorAll('.review-volume-rule')].map(e=>({dash:getComputedStyle(e).strokeDasharray,color:getComputedStyle(e).stroke}));
   return {width:innerWidth,scroll:document.documentElement.scrollWidth,plotHeight:canvas.height,
    maxBarWidth:Math.max(...bars.map(r=>r.width)),barBottom:Math.max(...bars.map(r=>r.bottom)),datesTop:dates.top,rules};
  });
  assert(measured.scroll<=width+1,JSON.stringify(measured));
  assert(measured.plotHeight===220,JSON.stringify(measured));
  assert(measured.maxBarWidth<=10,JSON.stringify(measured));
  assert(measured.barBottom<measured.datesTop,JSON.stringify(measured));
  assert.equal(new Set(measured.rules.map(r=>r.dash)).size,1);
  assert.equal(new Set(measured.rules.map(r=>r.color)).size,3);
  await page.locator('.review-volume-canvas').focus();const prior=await page.locator('[data-volume-date]').innerText();await page.keyboard.press('ArrowLeft');assert.notEqual(await page.locator('[data-volume-date]').innerText(),prior);await page.keyboard.press('End');await page.locator('.review-volume-canvas').evaluate(e=>e.blur());
  await page.screenshot({path:`${out}/liquidity-${width}.png`});
  if(width===1440){await page.locator('#reviewVolumeWidget').screenshot({path:`${out}/volume-1440.png`});await page.locator('.review-breadth-strip').screenshot({path:`${out}/breadth-1440.png`});await page.locator('#review-step-conclusion').screenshot({path:`${out}/conclusion-1440.png`});}
  layouts.push(measured);
 }
 // Check the actual overseas result (or its honest failure state) rather than a mock quote payload.
 await page.evaluate(()=>window.loadReviewOverseas({force:true}));
 assert(!(await page.locator('#reviewOverseasRoot').innerText()).includes('行情场次待确认'));
 const overseas=await page.locator('#reviewOverseasStatus').innerText();
 assert(overseas.includes('10-30')||overseas.includes('超时')||overseas.includes('失败')||overseas.includes('HTTP'));
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 fs.writeFileSync(out+'/verification.json',JSON.stringify({status:'passed',real_review_requests:true,latest_blank_date_path:true,layouts,responses,overseas,errors,writes},null,2));
 console.log(JSON.stringify({status:'passed',widths:layouts.map(r=>r.width),responses,overseas,errors}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
