const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('/Users/shibicheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const payload=JSON.parse(fs.readFileSync(require('node:path').join(__dirname,'snapshot.json'),'utf8'));
let facts; try{facts=JSON.parse(fs.readFileSync(require('node:path').join(__dirname,'market-facts.json'),'utf8'));}catch{}
const out='research/evidence/index_review_20261102';fs.mkdirSync(out,{recursive:true});
(async()=>{const browser=await chromium.launch({headless:true,channel:'chrome'});
try{
 const page=await browser.newPage();const errors=[],writes=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',route=>{
 const req=route.request(),url=new URL(req.url());
 if(req.method()!=='GET'){writes.push(url.pathname);return route.fulfill({status:400,json:{detail:'Read-only UI verification'}});}
 if(url.pathname==='/api/v1/review/dashboard')return route.fulfill({json:payload});
 if(url.pathname==='/api/v1/review/market-facts')return route.fulfill({json:facts||{status:'missing'}});
 if(url.pathname.includes('overnight'))return route.fulfill({json:{available:false,groups:[],reason:'隔夜非本次验收范围'}});
 return route.fulfill({json:{status:'missing',dates:[],rows:[],data:[],items:[],candidates:[]}});
 });
 await page.goto('http://127.0.0.1:8000/ui',{waitUntil:'domcontentloaded'});
 await page.waitForSelector('.review-index-close');
 await page.evaluate(async()=>{document.getElementById('reviewDate').value='2026-10-30';await window.loadReviewDashboard({force:true});});
 if(facts?.status==='ready')await page.waitForSelector('.review-histogram-bin');
 const body=await page.locator('#tab-review').innerText();
 assert(!body.includes('[object Object]'));assert(!body.includes('DATA GATE'));
 assert(!body.includes('外部形态证据文档'));assert(body.includes('推动浪假设未通过'));
 assert.equal(await page.locator('#review-step-conclusion .review-index-ohlc').count(),4);
 assert.equal(await page.locator('#review-step-facts .review-index-card').count(),0);
 const layouts=[];
 for(const width of [320,375,414,768,1440]){
  await page.setViewportSize({width,height:1000});await page.evaluate(()=>scrollTo(0,0));
  const overflow=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
  assert(overflow.scroll<=width+1,JSON.stringify(overflow));
  await page.screenshot({path:`${out}/top-${width}.png`});
  for(const id of ['review-step-facts','review-patterns','review-step-conclusion']){
   await page.locator(`.review-loop-nav a[href="#${id}"]`).click();
   const coords=await page.evaluate(id=>{const nav=document.querySelector('#tab-review .review-loop-nav').getBoundingClientRect(),t=document.getElementById(id).getBoundingClientRect(),top=document.querySelector('.topbar').getBoundingClientRect();return {navTop:nav.top,navBottom:nav.bottom,targetTop:t.top,topbarBottom:top.bottom};},id);
   assert(coords.navTop>=coords.topbarBottom-1,JSON.stringify(coords));
   assert(coords.targetTop>=coords.navBottom-2,`target covered ${id}/${width}: ${JSON.stringify(coords)}`);
   await page.waitForFunction(id=>document.querySelector(`.review-loop-nav a[href="#${id}"]`).getAttribute('aria-current')==='location',id,{timeout:2000});
  }
  await page.locator('.review-loop-nav a[href="#review-step-facts"]').click();
  await page.screenshot({path:`${out}/liquidity-${width}.png`});
  if(width===375)await page.locator(".review-breadth-strip").screenshot({path:`${out}/breadth-375.png`});
  await page.locator('#reviewVolumeDay').focus();const prior=await page.locator('[data-volume-date]').innerText();await page.keyboard.press('ArrowLeft');assert.notEqual(await page.locator('[data-volume-date]').innerText(),prior);
  await page.locator('.review-loop-nav a[href="#review-patterns"]').click();await page.screenshot({path:`${out}/wave-${width}.png`});
  layouts.push(overflow);
 }
 await page.setViewportSize({width:1440,height:1000});
 if(facts?.status==='ready'){
  assert.equal(await page.locator('.review-histogram-bin').count(),23);
  assert.equal(await page.locator('.review-market-breadth-table tbody tr').count(),4);
  assert.equal(await page.locator('.review-volume-bar').count(),Math.min(60,facts.market.volume.series.filter(r=>r.amount_yi!==null).length));
  await page.locator('.review-breadth-strip').screenshot({path:`${out}/breadth-1440.png`});
 }
 await page.locator('#review-step-conclusion').screenshot({path:`${out}/indices-1440.png`});
 await page.locator('#reviewVolumeWidget').screenshot({path:`${out}/volume-1440.png`});
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 fs.writeFileSync(`${out}/browser.json`,JSON.stringify({status:'passed',layouts,real_snapshot_date:payload.analysis_date,real_supplement:facts?.source_revision||null,errors,writes},null,2));
 console.log(JSON.stringify({status:'passed',layouts,supplement:!!facts,errors}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
