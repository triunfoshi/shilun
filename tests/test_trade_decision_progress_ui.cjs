const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || '/Users/shibicheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const research=JSON.parse(fs.readFileSync('runtime/trade_decision/2026-09-23/mode-research.json','utf8'));
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try {
    const page=await browser.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    let result={status:'running',date:'2026-09-23',message:'读取历史换手与市值',elapsed_seconds:42};
    let requests=0;
    await page.route('**/api/**',route=>{
      const url=new URL(route.request().url());
      assert.equal(route.request().method(),'GET');
      if(url.pathname==='/api/v1/trade-decision/mode-research'){requests++;return route.fulfill({json:result});}
      if(url.pathname.endsWith('/dates'))return route.fulfill({json:{dates:['2026-09-23']}});
      return route.fulfill({json:{status:'not_materialized',dates:[],rows:[],candidates:[]}});
    });
    await page.goto('http://127.0.0.1:8000/ui?decision=research&date=2026-09-23',{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.getElementById('decisionResearchStatus').textContent.includes('已用时 42 秒'));
    assert((await page.locator('#decisionResearchStatus').innerText()).includes('读取历史换手与市值'));
    assert.equal(await page.locator('#decisionResearchResult').getAttribute('aria-busy'),'true');
    result={status:'error',date:'2026-09-23',message:'读取历史换手与市值未完成，请检查数据连接后重新生成'};
    await page.evaluate(()=>window.TradeDecision.loadResearch());
    assert((await page.locator('#decisionResearchStatus').innerText()).includes('未完成'));
    assert.equal(await page.locator('#decisionResearchResult').getAttribute('aria-busy'),'false');
    const afterError=requests;
    await page.waitForTimeout(5100);
    assert.equal(requests,afterError,'failure stops automatic polling');
    result={status:'ready',research};
    await page.evaluate(()=>window.TradeDecision.loadResearch());
    assert((await page.locator('#decisionResearchStatus').innerText()).includes('2026-09-23 · 最新评估已读取'));
    assert.equal(await page.locator('[data-decision-mode]').count(),3);
    assert.equal(await page.locator('#decisionResearchResult').getAttribute('aria-busy'),'false');
    assert.deepEqual(errors,[]);
    fs.mkdirSync('research/evidence/mode_generation_20260923',{recursive:true});
    await page.screenshot({path:'research/evidence/mode_generation_20260923/ready.png',fullPage:false});
    console.log(JSON.stringify({running_progress:true,error_stops_polling:true,ready_23:true,errors}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
