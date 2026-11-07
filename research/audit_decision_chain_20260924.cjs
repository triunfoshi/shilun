// Read-only UI acceptance against the actual local frozen-snapshot API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('/Users/shibicheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const plan = JSON.parse(fs.readFileSync('runtime/trade_decision/2026-09-23/plan.json'));
const out = 'research/evidence/decision_chain_20260924';
(async () => {
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  try {
    const page = await browser.newPage();
    const errors = [], decisionRequests = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/api/**', route => {
      const req = route.request(), url = new URL(req.url());
      assert.equal(req.method(), 'GET', 'acceptance must never issue writes');
      if (url.pathname.startsWith('/api/v1/trade-decision/')) {
        decisionRequests.push(url.pathname + url.search);
        return route.continue();
      }
      return route.fulfill({json:{status:'not_materialized', dates:[], rows:[], candidates:[]}});
    });
    await page.goto('http://127.0.0.1:8000/ui?decision=plan&date=2026-09-23', {waitUntil:'domcontentloaded'});
    await page.waitForSelector('#decisionPlanResult .decision-chain');
    assert.equal(await page.locator('.decision-chain li').count(), 7);
    const text = await page.locator('#decisionPlanResult').innerText();
    assert(text.includes('等待'));
    assert(text.includes('0个主观察') && text.includes('2个条件备选'));
    assert(!text.includes('完整MA5观察池'));
    assert.equal(await page.locator('.decision-direction-roster button').count(), 2);
    assert((await page.locator('[data-check="first-board"]').innerText()).includes('39只'));
    assert.equal(await page.locator('[data-branch="wait"]>.decision-flow-node.is-current').count(), 1);
    fs.mkdirSync(out, {recursive:true});
    const layouts=[];
    for (const width of [375, 1440]) {
      await page.setViewportSize({width, height:1000});
      await page.evaluate(() => window.scrollTo(0,0));
      const scroll = await page.evaluate(() => document.documentElement.scrollWidth);
      assert(scroll <= width+1, `horizontal overflow ${width}/${scroll}`);
      layouts.push({width, scroll, overflow:false});
      await page.screenshot({path:`${out}/plan-${width}.png`, fullPage:true});
    }
    await page.locator('.decision-flow').screenshot({path:`${out}/flow-1440.png`});
    for (const d of plan.directions) {
      await page.locator(`[data-decision-direction="${d.code}"]`).click();
      const content = await page.locator('.decision-direction-detail').innerText();
      assert(content.includes(d.name));
      assert(content.includes(d.upgrade) && content.includes(d.failure));
    }
    const ref = page.locator('[data-mode-research-link]');
    assert((await ref.getAttribute('href')).includes(plan.mode_research_ref.snapshot_id));
    await ref.click();
    await page.waitForSelector('#decisionResearchResult #decisionEvalWindow');
    assert((await page.locator('#decisionResearchStatus').innerText()).includes('预案引用版本'));
    assert.equal(await page.locator('[data-decision-mode]').count(), 3);
    await page.locator('[data-decision-mode="crowding"]').click();
    const coreText = await page.locator('[data-mode="crowding"]').innerText();
    assert(coreText.includes('未知'));
    assert(coreText.includes('13/14'));
    await page.screenshot({path:`${out}/research-crowding-1440.png`, fullPage:true});
    assert.deepEqual(errors, []);
    fs.writeFileSync(`${out}/ui-audit.json`, JSON.stringify({status:'passed', real_snapshot_api:true,
      reference:plan.mode_research_ref, layouts, seven_steps:true, next_first_boards:39,
      direction_condition_text:true, unknown_coverage_visible:true, errors, decisionRequests},null,2));
    console.log(JSON.stringify({status:'passed', layouts, snapshot:plan.mode_research_ref.snapshot_id, errors}));
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exitCode=1;});
