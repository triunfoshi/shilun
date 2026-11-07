// Read-only acceptance against real dated API payloads; no materialization POSTs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('/Users/shibicheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out = path.join(__dirname, 'evidence/sentiment_acceptance_20261103');
fs.mkdirSync(out, {recursive:true});
(async () => {
 let ready=false;
 for(let attempt=0;attempt<60;attempt++) {
  try {ready=(await fetch('http://127.0.0.1:8000/readyz')).ok;} catch {}
  if(ready)break;
  await new Promise(resolve=>setTimeout(resolve,500));
 }
 assert(ready,'Backend did not become ready after restart');
 const browser = await chromium.launch({channel:'chrome',headless:true});
 const errors=[],writes=[],checks=[];
 try {
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const panelShot=async (selector,file)=>{
   // Component exports exclude floating navigation, whose sticky behavior is
   // checked separately below against the unmodified running page.
   await page.evaluate(()=>document.querySelectorAll('.sentiment-workspace-nav,.topbar').forEach(el=>el.style.visibility='hidden'));
   try {await page.locator(selector).screenshot({path:path.join(out,file)});}
   finally {await page.evaluate(()=>document.querySelectorAll('.sentiment-workspace-nav,.topbar').forEach(el=>el.style.removeProperty('visibility')));}
  };
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',route=>{
   const request=route.request(),url=new URL(request.url());
   if(request.method()!=='GET'){writes.push(url.pathname);return route.fulfill({status:400,json:{detail:'Read-only acceptance'}});}
   if(url.pathname.startsWith('/api/v1/review/') && url.pathname!='/api/v1/review/overseas')return route.continue();
   return route.fulfill({json:{status:'missing',dates:[],rows:[],data:[],items:[],candidates:[]}});
  });
  await page.goto('http://127.0.0.1:8000/ui',{waitUntil:'domcontentloaded'});
  await page.evaluate(()=>{document.getElementById('sentimentDate').value='2026-11-02';});
  await page.locator('#nav-sentiment').click();
  if(await page.locator('#sentimentDate').inputValue()!=='2026-11-02'){
   await page.locator('#sentimentDate').fill('2026-11-02');
   await page.evaluate(()=>changeSentimentDate());
  }
  await page.waitForSelector('#sentimentThemeTree .slv-tree-row',{timeout:95000});
  await page.waitForFunction(()=>document.querySelector('#sentimentThemeTree')?.getAttribute('aria-busy')==='false',null,{timeout:95000});
  await page.waitForSelector('#sentimentEmotionHistory .slv-history-table',{state:'attached',timeout:95000});
  assert((await page.locator('#sentimentThemeTree').innerText()).includes('三季报增长'));
  assert((await page.locator('#sentimentThemeTree').innerText()).includes('125/125'));
  assert((await page.locator('#sentimentThemeTree').innerText()).includes('8天6板'));
  assert((await page.locator('#sentimentThemeTree').innerText()).includes('4天2板'));
  const light=await page.locator('#sentimentThemeTree .slv').evaluate(root=>{
   const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
   const ctx=canvas.getContext('2d');
   const rgba=color=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data];};
   return {scheme:getComputedStyle(root).colorScheme,panels:[...root.querySelectorAll('.slv-panel')].map(el=>rgba(getComputedStyle(el).backgroundColor)),ink:rgba(getComputedStyle(root).color)};
  });
  assert.equal(light.scheme,'light');
  assert(light.panels.every(rgb=>rgb.slice(0,3).every(channel=>channel>235)));
  assert(light.ink.slice(0,3).every(channel=>channel<80));
  assert.equal(await page.locator('#sentiment-pane-today').isVisible(),true);
  assert.equal(await page.locator('#sentiment-pane-rotation').isVisible(),false);
  assert.equal(await page.locator('.review-legacy-topic-evidence').count(),0);
  await page.waitForSelector('#sentimentDashboardRoot .review-theme-clusters',{timeout:60000});
  const downside=page.locator('#sentimentDashboardRoot .review-theme-clusters');
  assert((await downside.innerText()).includes('事件归因覆盖'));
  assert(!(await downside.innerText()).includes('行业同向聚集'));
  await downside.locator('details').first().evaluate(el=>{el.open=true;});
  assert((await downside.innerText()).includes('归因日'));
  await panelShot('#sentimentDashboardRoot .review-theme-clusters','downside-events-1440.png');
  await panelShot('#sentimentThemeTree','event-tree-1440.png');
  const selectedDate=await page.locator('#sentimentDate').inputValue();
  await page.locator('#sentiment-view-rotation').click();
  await page.waitForSelector('#sentimentThemeCycles .tc-cycle',{state:'attached',timeout:60000});
  const aligned=await page.locator('#sentimentThemeCycles .tc-post-exit .tc-scroll table :is(th,td)').evaluateAll(cells=>cells.map(el=>getComputedStyle(el).textAlign));
  assert(aligned.length>0 && aligned.every(align=>align==='center'));
  assert((await page.locator('#sentimentThemeCycles').innerText()).includes('人形机器人'));
  assert((await page.locator('#sentimentThemeCycles').innerText()).includes('“羊”字辈'));
  assert((await page.locator('#sentimentThemeCycles').innerText()).includes('三季报增长'));
  await page.evaluate(()=>{[...document.querySelectorAll('#sentimentThemeCycles .tc-cycle')].find(el=>el.querySelector('summary b')?.textContent==='“羊”字辈').open=true;});
  const activeCore=page.locator('#sentimentThemeCycles .tc-cycle[open]');
  assert((await activeCore.innerText()).includes('入选锚点'));
  assert((await activeCore.innerText()).includes('当日合格核心对照'));
  assert((await activeCore.innerText()).includes('九阳股份'));
  await activeCore.locator('.tc-detail>.tc-scroll').first().screenshot({path:path.join(out,'core-comparison-1440.png')});
  await activeCore.evaluate(el=>{el.open=false;});
  await panelShot('#sentimentThemeCycles','cycles-1440.png');
  assert.equal(await page.locator('#sentiment-pane-today').isVisible(),false);
  assert.equal(await page.locator('#sentimentDate').inputValue(),selectedDate);
  assert((await page.locator('#sentimentThemeRotation').innerText()).includes('缺原因 1'));
  assert((await page.locator('#sentimentThemeRotation').innerText()).includes('涨停 72'));
  assert.equal(await page.locator('#sentimentThemeEvidence .slv-evidence-table').isVisible(),true);
  await panelShot('#sentimentThemeRotation','rotation-1440.png');
  const filter=page.locator('[data-slv-evidence-filter]');
  await filter.selectOption({label:'AI漫剧'});
  assert((await page.locator('#sentimentThemeEvidence tbody tr:visible').count())>0);
  const visibleTags=await page.locator('#sentimentThemeEvidence tbody tr:visible th').allTextContents();
  assert(visibleTags.every(t=>t==='AI漫剧'));
  await filter.selectOption('');
  await page.locator('#sentiment-view-history').click();
  await page.locator('[data-slv-window="10"]').click();
  await page.waitForFunction(()=>document.querySelector('#sentimentEmotionHistory [data-slv-window="10"]')?.getAttribute('aria-pressed')==='true',null,{timeout:95000});
  await page.locator('[data-slv-window="30"]').click();
  await page.waitForFunction(()=>document.querySelector('#sentimentEmotionHistory [data-slv-window="30"]')?.getAttribute('aria-pressed')==='true',null,{timeout:95000});
  assert((await page.locator('#sentimentEmotionHistory').innerText()).includes('历史统计位置'));
  assert.equal(await page.locator('#sentimentDate').inputValue(),selectedDate);
  await panelShot('#sentimentEmotionHistory','history-1440.png');
  await page.locator('#sentiment-view-today').focus();
  await page.keyboard.press('End');
  assert.equal(await page.locator('#sentiment-view-history').getAttribute('aria-selected'),'true');
  await page.keyboard.press('Home');
  assert.equal(await page.locator('#sentiment-view-today').getAttribute('aria-selected'),'true');
  for(const width of [320,375,414,768,1440,1920]) {
   await page.setViewportSize({width,height:1000});
   for(const view of ['today','rotation','history']) {
    await page.locator(`#sentiment-view-${view}`).click();
    await page.evaluate(()=>window.scrollTo(0,0));
    const size=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth}));
    assert(size.scroll<=size.client+1,`${width} ${view}: page overflow ${size.scroll}/${size.client}`);
    checks.push({width,view,...size});
   }
   if(width===375 || width===1920) {
    await page.locator('#sentiment-view-today').click();
    await panelShot('#sentimentThemeTree',`event-tree-${width}.png`);
   }
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#sentiment-view-today').click();
  await page.locator('#sentimentThemeTree').scrollIntoViewIfNeeded();
  const sticky=await page.locator('.sentiment-workspace-nav').evaluate(el=>({top:el.getBoundingClientRect().top,position:getComputedStyle(el).position}));
  assert.equal(sticky.position,'sticky');
  assert(sticky.top>=0 && sticky.top<100);
  const help=page.locator('#sentimentThemeTree .slv-caliber-button').first();
  await help.click();
  assert.equal(await help.getAttribute('aria-expanded'),'true');
  await page.keyboard.press('Escape');
  assert.equal(await help.getAttribute('aria-expanded'),'false');
  await page.evaluate(()=>{document.getElementById('firstboardDate').value='2026-11-02';});
  await page.locator('#nav-firstboard').click();
  await page.waitForSelector('#firstboardRoot .br-stock',{timeout:90000});
  assert((await page.locator('#firstboardRoot .br-stock').first().innerText()).includes('襄阳轴承'));
  assert.equal(await page.locator('#firstboardRoot th[aria-sort="descending"]').innerText(),'竞价成交金额');
  await page.locator('#firstboardRoot .br-ledger-scroll').screenshot({path:path.join(out,'first-board-priority-1440.png')});
  assert.deepEqual(errors,[]);
  assert.deepEqual(writes,[]);
  const result={date:selectedDate,light,centeredPostExitCells:aligned.length,checks,sticky,errors,writes};
  fs.writeFileSync(path.join(out,'browser-verification.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
