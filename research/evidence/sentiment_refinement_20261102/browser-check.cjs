// Actual read-only review APIs; isolate unrelated workspaces during UI verification.
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('/Users/shibicheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out=__dirname;
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],writes=[],responses=[];
  const paths=['/api/v1/review/dashboard','/api/v1/review/market-facts','/api/v1/review/live-emotion','/api/v1/review/board-dynamics','/api/v1/review/theme-cycles'];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',async r=>{
   const u=new URL(r.url());if(!paths.includes(u.pathname))return;
   let data;try{data=await r.json();}catch{}
   responses.push({url:u.pathname+u.search,status:r.status(),date:data?.date||data?.analysis_date});
   if(data)fs.writeFileSync(`${out}/${u.pathname.split('/').at(-1)}.json`,JSON.stringify(data,null,2));
  });
  await page.route('**/api/**',r=>{
   const req=r.request(),u=new URL(req.url());
   if(req.method()!=='GET'){writes.push(u.pathname);return r.fulfill({status:400,json:{detail:'Read-only validation'}});}
   return paths.includes(u.pathname)?r.continue():r.fulfill({json:{status:'missing',dates:[],rows:[],data:[],items:[],candidates:[]}});
  });
  await page.goto('http://127.0.0.1:8000/ui',{waitUntil:'domcontentloaded'});
  await page.locator('#nav-sentiment').click();
  await page.waitForFunction(()=>document.getElementById('sentimentCommandBar').getAttribute('aria-busy')==='false',{timeout:80000});
  await page.waitForSelector('#sentimentMarketMood [data-mood-stage].is-current',{timeout:80000});
  await page.waitForSelector('.bd-day-facts',{timeout:85000});
  await page.waitForSelector('.review-down-roster-table tbody tr',{state:'attached',timeout:85000});
  await page.waitForFunction(()=>document.getElementById('sentimentThemeCycles').getAttribute('aria-busy')==='false',{timeout:30000});
  assert.equal(await page.locator('#sentimentDate').inputValue(),'2026-11-02');
  assert.equal(await page.locator('#sentimentMarketMood .is-current').count(),1);
  assert.equal(await page.locator('#sentimentMarketMood .is-current').getAttribute('data-mood-stage'),'hot');
  assert((await page.locator('#sentimentMarketMoodValue').innerText()).includes('亢奋'));
  assert.equal(await page.locator('.sentiment-cycle-explainer').getAttribute('open'),null);
  const summary=page.locator('.sentiment-cycle-explainer>summary');await summary.focus();await page.keyboard.press('Enter');
  assert.equal(await page.locator('.sentiment-cycle-explainer').evaluate(e=>e.open),true);await page.keyboard.press('Enter');
  assert.equal(await page.locator('.sentiment-cycle-explainer').evaluate(e=>e.open),false);
  assert.equal(await page.locator('.bd-fact-card').count(),3);
  assert.equal(await page.locator('.bd-promotion-stages>div').count(),3);
  assert.equal(await page.locator('.bd-money-effect .bd-return-pair strong').first().evaluate(e=>getComputedStyle(e).fontSize),'28px');
  assert((await page.locator('.bd-facts-caption').innerText()).includes('2026-11-02'));
  const selectedDay=JSON.parse(fs.readFileSync(out+'/board-dynamics.json','utf8')).selected_day;
  const expected=(selectedDay.money_effect.median_pct>0?'+':'')+selectedDay.money_effect.median_pct.toFixed(2)+'%';
  assert((await page.locator('.bd-money-effect').innerText()).includes(expected));
  const cardDate=await page.locator('.bd-facts-caption').innerText();
  await page.locator('[data-bd-day]').evaluate(e=>{e.value='0';e.dispatchEvent(new Event('input',{bubbles:true}));});
  assert.equal(await page.locator('.bd-facts-caption').innerText(),cardDate);
  const roster=page.locator('.review-down-roster-table');await roster.locator('xpath=ancestor::details').evaluate(e=>e.open=true);
  const rows=await roster.locator('tbody tr').allTextContents();
  assert.equal(rows.length,11);assert(rows.every(r=>/-\d+(?:\.\d+)?%/.test(r)),rows.join('\n'));
  assert(!rows.join('').includes('涨幅待取'));
  const layouts=[];
  for(const width of [320,375,414,768,1440,1920]){
   await page.setViewportSize({width,height:1000});
   await page.locator('.sentiment-section-nav a[href="#sentimentMarketMood"]').click();
   await page.waitForFunction(()=>document.querySelector('.sentiment-section-nav a[aria-current="location"]')?.hash==='#sentimentMarketMood');
   const layout=await page.evaluate(()=>{
    const nav=document.querySelector('.sentiment-section-nav').getBoundingClientRect(),bar=document.querySelector('.topbar').getBoundingClientRect();
    const current=document.querySelector('.review-market-mood-current').getBoundingClientRect(),context=document.querySelector('.review-market-mood-context').getBoundingClientRect();
    const mood=document.getElementById('sentimentMarketMood').getBoundingClientRect();
    return {width:innerWidth,scroll:document.documentElement.scrollWidth,navTop:nav.top,barBottom:bar.bottom,expectedTop:['sticky','fixed'].includes(getComputedStyle(document.querySelector('.topbar')).position)?bar.bottom:0,moodTop:mood.top,navBottom:nav.bottom,currentX:current.x,contextX:context.x};
   });
   assert(layout.scroll<=width+1,JSON.stringify(layout));
   assert(layout.navTop>=layout.expectedTop-1&&layout.navTop<=layout.expectedTop+12,JSON.stringify(layout));
   assert(layout.moodTop>=layout.navBottom-1,JSON.stringify(layout));
   if(width>768)assert(layout.currentX<layout.contextX,JSON.stringify(layout));
   await page.screenshot({path:`${out}/mood-${width}.png`});
   await page.locator('.sentiment-section-nav a[href="#sentimentBoardDynamics"]').click();
   await page.waitForFunction(()=>document.querySelector('.sentiment-section-nav a[aria-current="location"]')?.hash==='#sentimentBoardDynamics');
   const stuck=await page.locator('.sentiment-section-nav').boundingBox();assert(Math.abs(stuck.y-layout.expectedTop)<2,JSON.stringify(stuck));
   const cards=await page.locator('.bd-fact-card').evaluateAll(es=>es.map(e=>{const r=e.getBoundingClientRect();return {x:r.x,right:r.right,top:r.top,bottom:r.bottom};}));
   assert(cards.every(r=>r.x>=0&&r.right<=width+1),JSON.stringify(cards));
   if(width===1440)await page.locator('#sentimentBoardDynamics').screenshot({path:out+'/day-facts-1440.png'});
   if(width===375)await page.screenshot({path:out+'/day-facts-375.png'});
   await page.locator('.sentiment-section-nav a[href="#sentimentThemeCycles"]').click();
   await page.locator('.sentiment-section-nav a[href="#sentiment-downside-panel"]').click();
   await page.waitForFunction(()=>!document.getElementById('sentiment-downside-panel').hidden);
   await page.waitForFunction(()=>document.querySelector('.sentiment-section-nav a[aria-current="location"]')?.hash==='#sentiment-downside-panel');
   assert.equal(await page.locator('#sentiment-downside-tab').getAttribute('aria-selected'),'true');
   layouts.push({...layout,cards});
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('.sentiment-section-nav a[href="#sentimentLiveEmotion"]').click();
  await roster.screenshot({path:out+'/down-roster-1440.png'});
  await page.locator('.sentiment-section-nav a[href="#sentimentThemeCycles"]').click();
  const theme=await page.locator('#sentimentThemeCycles').innerText();
  await page.locator('#sentimentThemeCycles').screenshot({path:out+'/theme-status.png'});
  assert(theme.includes('10-08')&&theme.includes('10-14')&&theme.includes('周期归档更新受阻')&&theme.includes('只检查现有归档'),theme);
  // P1 retains sticky navigation after the shared navigation utility was generalized.
  await page.locator('#nav-review').click();
  await page.locator('#tab-review .review-loop-nav a[href="#review-step-facts"]').click();
  await page.waitForFunction(()=>document.querySelector('#tab-review .review-loop-nav a[aria-current="location"]')?.hash==='#review-step-facts');
  const contrast=await page.evaluate(()=>{
   const canvas=document.createElement('canvas');canvas.width=canvas.height=1;const c=canvas.getContext('2d');
   const rgb=color=>{c.clearRect(0,0,1,1);c.fillStyle=color;c.fillRect(0,0,1,1);return [...c.getImageData(0,0,1,1).data].slice(0,3);};
   const l=arr=>arr.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
   return [...document.querySelectorAll('[data-mood-stage]')].map(e=>{const color=getComputedStyle(e.querySelector('small')).color,bg=getComputedStyle(e).backgroundColor;const a=l(rgb(color)),b=l(rgb(bg));return {stage:e.dataset.moodStage,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};});
  });
  assert(contrast.every(r=>r.ratio>=4.5),JSON.stringify(contrast));assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  fs.writeFileSync(out+'/verification.json',JSON.stringify({status:'passed',date:'2026-11-02',realReviewApis:true,layouts,rows,selectedDay,theme,contrast,responses,errors,writes},null,2));
  console.log(JSON.stringify({status:'passed',widths:layouts.map(r=>r.width),roster:rows.length,selectedDay,theme,contrast,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
