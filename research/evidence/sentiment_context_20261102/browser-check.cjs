// Real dated GETs. The already completed cloud repair is inspected without starting another run.
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('/Users/shibicheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out=__dirname;
(async()=>{const browser=await chromium.launch({headless:true,channel:'chrome'});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],writes=[],responses=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('response',async r=>{const u=new URL(r.url());if(!u.pathname.startsWith('/api/v1/review/'))return;
  let d;try{d=await r.json();}catch{}
  responses.push({url:u.pathname+u.search,status:r.status()});
  if(d&&['sentiment-context','theme-cycles','strong-stocks'].includes(u.pathname.split('/').at(-1)))fs.writeFileSync(`${out}/${u.pathname.split('/').at(-1)}.json`,JSON.stringify(d,null,2));
 });
 await page.route('**/api/**',r=>{
  const q=r.request(),u=new URL(q.url());
  if(q.method()!=='GET'){writes.push(u.pathname);return r.fulfill({status:400,json:{detail:'Read-only browser validation'}});}
  return u.pathname.startsWith('/api/v1/review/')&&u.pathname!='/api/v1/review/overseas'?r.continue():r.fulfill({json:{status:'missing',dates:[],rows:[],data:[],items:[],candidates:[]}});
 });
 await page.goto('http://127.0.0.1:8000/ui',{waitUntil:'domcontentloaded'});
 await page.locator('#nav-sentiment').click();
 await page.waitForFunction(()=>document.getElementById('sentimentContext').dataset.date==='2026-11-02',null,{timeout:75000});
 await page.waitForSelector('.review-legacy-topic-evidence',{timeout:60000});
 await page.waitForFunction(()=>document.getElementById('sentimentThemeCycles').getAttribute('aria-busy')==='false',null,{timeout:60000});
 assert.equal(await page.locator('.rsc-panel').count(),2);
 assert.equal(await page.locator('.rsc-score-day').count(),10);
 assert.equal(await page.locator('.rsc-time-chart>div').count(),5);
 assert((await page.locator('.rsc-cycle-facts').innerText()).includes('0.95'));
 assert((await page.locator('.rsc-cycle-facts').innerText()).includes('第3天'));
 assert((await page.locator('.rsc-coverage').first().innerText()).includes('125/125'));
 assert.equal(await page.locator('.review-legacy-topic-evidence').evaluate(e=>e.open),true);
 assert((await page.locator('.review-legacy-topic-evidence>summary').innerText()).startsWith('当日题材证据 · 2026-11-02'));
 const resonance=await page.locator('.review-evidence-card.is-primary').innerText();
 assert(resonance.includes('当日同涨')&&resonance.includes('20日结构分歧')&&resonance.includes('指数仍低于MA20'),resonance);
 const repair=page.locator('[data-tc-repair]');
 assert.equal(await repair.innerText(),'回补并分析');assert.equal(await repair.isEnabled(),true);
 const archive=JSON.parse(fs.readFileSync(out+'/theme-cycles.json','utf8'));
 assert.equal(archive.date,'2026-11-02');assert.notEqual(archive.status,'not_materialized');assert.equal(archive.repair_job.status,'complete');
 assert(archive.available_dates.includes('2026-10-08')&&archive.available_dates.includes('2026-11-02'));
 assert.equal(archive.repair_job.revision,archive.revision);
 const layouts=[];
 for(const width of [320,375,414,768,1440,1920]){
  await page.setViewportSize({width,height:1000});
  await page.locator('#sentimentContext').evaluate(e=>e.scrollIntoView({block:'start',behavior:'instant'}));
  const layout=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
   panels:[...document.querySelectorAll('.rsc-panel')].map(e=>{const r=e.getBoundingClientRect();return{x:r.x,right:r.right,top:r.top,bottom:r.bottom};}),
   moodBottom:document.getElementById('sentimentMarketMood').getBoundingClientRect().bottom,
   contextTop:document.getElementById('sentimentContext').getBoundingClientRect().top,
   liveTop:document.getElementById('sentimentLiveEmotion').getBoundingClientRect().top,
   contextBottom:document.getElementById('sentimentContext').getBoundingClientRect().bottom
  }));
  assert(layout.scroll<=width+1,JSON.stringify(layout));assert(layout.panels.every(p=>p.x>=0&&p.right<=width+1),JSON.stringify(layout));
  assert(layout.contextTop>=layout.moodBottom,JSON.stringify(layout));assert(layout.liveTop>=layout.contextBottom,JSON.stringify(layout));
  if(width<=900)assert(layout.panels[1].top>=layout.panels[0].bottom,JSON.stringify(layout));else assert(Math.abs(layout.panels[1].top-layout.panels[0].top)<1,JSON.stringify(layout));
  await page.screenshot({path:`${out}/context-${width}.png`});
  if(width===1440)await page.locator('#sentimentContext').screenshot({path:out+'/context-detail-1440.png'});
  await page.locator('.sentiment-section-nav a[href="#sentimentThemeCycles"]').click();
  const actions=await page.locator('#sentimentThemeCycles .tc-actions button').evaluateAll(es=>es.map(e=>({text:e.innerText,height:e.getBoundingClientRect().height,whiteSpace:getComputedStyle(e).whiteSpace})));
  if(width<=480)assert(actions.every(a=>a.height>=44),JSON.stringify(actions));
  if(width===375||width===1440)await page.locator('#sentimentThemeCycles').screenshot({path:`${out}/theme-archive-${width}.png`});
  layouts.push({...layout,actions});
 }
 await page.setViewportSize({width:1440,height:1000});
 const raw=page.locator('.rsc-details').last();await raw.locator('summary').focus();await page.keyboard.press('Enter');assert.equal(await raw.evaluate(e=>e.open),true);await page.keyboard.press('Enter');
 await page.locator('#nav-firstboard').click();await page.locator('#focus-strong-tab').click();
 await page.waitForSelector('.ss-table tbody tr',{timeout:75000});
 const stocks=await page.locator('.ss-table tbody tr').evaluateAll(es=>es.map(e=>({name:e.querySelector('[data-ss-stock]').innerText,drawdown:parseFloat(e.querySelector('td[data-label="收盘回撤"]').innerText)})));
 assert.equal(stocks.length,12);
 for(let i=1;i<stocks.length;i++)assert(Math.abs(stocks[i-1].drawdown)>=Math.abs(stocks[i].drawdown),JSON.stringify(stocks));
 assert(stocks[0].name.includes('华瓷股份'));assert.equal(stocks[0].drawdown,-35.79);
 assert.equal(await page.locator('.ss-table th[aria-sort="descending"]').innerText(),'收盘回撤');
 await page.locator('#firstboardStrong').screenshot({path:out+'/strong-drawdown-1440.png'});
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 fs.writeFileSync(out+'/verification.json',JSON.stringify({status:'passed',realDatedReads:true,newRepairNotTriggered:true,layouts,resonance,stocks,archive:{date:archive.date,status:archive.status,revision:archive.revision,repair:archive.repair_job},responses,errors,writes},null,2));
 console.log(JSON.stringify({status:'passed',widths:layouts.map(r=>r.width),resonance,firstStocks:stocks.slice(0,3),archive:{date:archive.date,status:archive.status,revision:archive.revision},errors}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
