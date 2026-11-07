/* Browser verification of the actual local pilot snapshot; no source mutations. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || '/Users/shibicheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const plan=JSON.parse(fs.readFileSync(process.env.DECISION_PLAN_FILE || 'research/evidence/trade_decision_v7_20260921/plan.json','utf8'));
const research=JSON.parse(fs.readFileSync('research/evidence/trade_decision_v7_20260921/mode-research.json','utf8'));
const tracking=JSON.parse(fs.readFileSync('research/evidence/trade_decision_v2_20260921/tracking_20260922.json','utf8'));
const base=process.env.DECISION_TEST_URL || 'http://127.0.0.1:8000';
const output='research/evidence/trade_decision_v7_20260921/ui';
fs.mkdirSync(output,{recursive:true});
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try {
    const page=await browser.newPage();
    const errors=[];page.on('pageerror',err=>errors.push(err.message));
    let planRequests=0,researchRequests=0,referenceRequests=0,writeRequests=0,failTracking=false;
    const intercept=async route=>{
      const url=new URL(route.request().url());
      if(route.request().method()!=='GET')writeRequests++;
      if(url.pathname==='/api/v1/trade-decision/plan'){
        planRequests++;
        if(url.searchParams.get('date')==='2026-09-18')return route.fulfill({json:{status:'missing'}});
        return route.fulfill({json:{status:'ready',plan}});
      }
      if(url.pathname==='/api/v1/trade-decision/mode-research'){
        researchRequests++;
        const sid=url.searchParams.get('snapshot_id');
        if(url.searchParams.get('date')!=='2026-09-21'||(sid&&sid!==research.snapshot_id))return route.fulfill({json:{status:'missing'}});
        if(sid)referenceRequests++;
        return route.fulfill({json:{status:'ready',research}});
      }
      if(['/api/v1/trade-decision/dates','/api/v1/trade-decision/mode-research/dates'].includes(url.pathname))return route.fulfill({json:{dates:['2026-09-21']}});
      if(url.pathname==='/api/v1/trade-decision/tracking')return failTracking?route.fulfill({status:503,json:{detail:'测试：分钟缓存连接失败'}}):route.fulfill({json:tracking});
      return route.fulfill({json:{status:'not_materialized',dates:[],rows:[],candidates:[]}});
    };
    await page.route('**/api/**',intercept);
    await page.goto(`${base}/ui?decision=plan&date=2026-09-21`,{waitUntil:'domcontentloaded'});
    await page.waitForSelector('#decisionPlanResult .decision-chain');
    assert.equal(await page.locator('.decision-chain li').count(),7);
    assert((await page.locator('#decisionPlanResult').innerText()).includes('2.0481万亿'));
    assert((await page.locator('#decisionPlanResult').innerText()).includes('中位数'));
    assert(!(await page.locator('#decisionPlanResult').innerText()).includes('完整MA5观察池'));
    assert.equal(await page.locator('.decision-direction-roster button').count(),plan.directions.length);
    assert(plan.directions.length<=5);
    assert.equal(await page.locator('.decision-flow').count(),1);
    assert((await page.locator('#decisionPlanResult').innerText()).includes('45,029'));
    assert((await page.locator('#decisionPlanResult').innerText()).includes('+996'));
    assert((await page.locator('#decisionPlanResult').innerText()).includes('不作模式排名'));
    assert.equal(await page.locator('[data-branch="A"]>.decision-flow-node.is-unmet').count(),2);
    assert.equal(await page.locator(`[data-branch="${plan.route.mode||'wait'}"]>.decision-flow-node.is-current`).count(),1);
    assert((await page.locator('[data-check="first-board"]').innerText()).includes(`${plan.next_day_first_boards.cohort_n}只`));
    assert((await page.locator('[data-check="first-board"]').innerText()).includes('正溢价占比'));
    assert.equal(await page.locator('#decisionPlanResult #decisionBand').count(),0,'history filters belong to research');
    assert.equal(await page.locator('#decisionPlanResult .decision-help').count(),0);
    for(const width of [320,375,414,768,1440]){
      await page.setViewportSize({width,height:1000});
      await page.screenshot({path:path.join(output,`plan-${width}.png`),fullPage:true});
      await page.screenshot({path:path.join(output,`plan-${width}-top.png`)});
      await page.locator('.decision-flow').screenshot({path:path.join(output,`flow-${width}.png`)});
      const layout=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
        title:document.getElementById('tab-decision-plan').getBoundingClientRect().width}));
      assert(layout.scroll<=width+1,`page overflow at ${width}: ${JSON.stringify(layout)}`);
      if(width>=768){
        const aligned=await page.evaluate(()=>{
          const branches=[...document.querySelectorAll('.decision-flow-branch')];
          const spread=xs=>Math.max(...xs)-Math.min(...xs);
          return [0,2].every(i=>spread(branches.map(b=>b.children[i].getBoundingClientRect().top))<=1&&spread(branches.map(b=>b.children[i].getBoundingClientRect().bottom))<=1);
        });
        assert(aligned,`flow branch rows must align at ${width}`);
      }
    }
    const contrasts=await page.evaluate(()=>{
      const ctx=document.createElement('canvas').getContext('2d');
      const rgb=c=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=c;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data];};
      const lum=xs=>xs.slice(0,3).map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4;}).reduce((a,x,i)=>a+x*[.2126,.7152,.0722][i],0);
      return ['.decision-positive','.decision-negative','.decision-muted','.decision-verdict','.decision-flow-node.is-unmet','.decision-flow-checks .is-unmet','.decision-flow-node.is-current>.decision-flow-state'].map(sel=>{const el=document.querySelector(sel);let bg=el;while(bg&&rgb(getComputedStyle(bg).backgroundColor)[3]===0)bg=bg.parentElement;const a=lum(rgb(getComputedStyle(el).color)),b=lum(rgb(bg?getComputedStyle(bg).backgroundColor:"white"));return {sel,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};});
    });
    assert(contrasts.every(r=>r.ratio>=4.5),JSON.stringify(contrasts));
    fs.writeFileSync(path.join(output,'contrast.json'),JSON.stringify(contrasts,null,2));
    await page.locator('.decision-direction-detail').screenshot({path:path.join(output,'directions-1440.png')});
    for (const d of plan.directions) {
      await page.locator(`[data-decision-direction="${d.code}"]`).click();
      assert((await page.locator('.decision-direction-detail').innerText()).includes(d.name));
    }
    const referenceLink=page.locator('[data-mode-research-link]');
    assert((await referenceLink.getAttribute('href')).includes(research.snapshot_id));
    await referenceLink.click();
    await page.waitForSelector('#decisionResearchResult #decisionEvalWindow');
    assert((await page.locator('#decisionResearchStatus').innerText()).includes('预案引用版本'));
    assert.equal(await page.inputValue('#decisionEvalWindow'),'5');
    assert.equal(await page.locator('.decision-playbook').count(),1);
    assert.equal(await page.locator('[data-decision-mode]').count(),3);
    assert.equal(await page.locator('#decisionHistory').count(),0,'historical return appendix removed');
    assert.equal(await page.locator('#decisionHorizon, #decisionBand, #decisionPeriod').count(),0);
    for(const [key,m] of Object.entries(research.assessments.windows['5'])){
      await page.locator(`[data-decision-mode="${key}"]`).click();
      assert((await page.locator(`[data-mode="${key}"]`).innerText()).includes(m.status_label));
      assert.equal(await page.locator('[role=tablist] [aria-selected=true][data-decision-mode]').count(),1);
    }
    assert((await page.locator('.decision-zero-note').innerText()).includes('+5.89%'));
    assert((await page.locator('.decision-zero-note').innerText()).includes('不是收益为零'));
    await page.locator('[data-decision-mode="crowding"]').focus();
    await page.keyboard.press('Home');
    assert.equal(await page.locator('[data-decision-mode="trend"]').getAttribute('aria-selected'),'true');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('[data-decision-mode="relay"]').getAttribute('aria-selected'),'true');
    await page.keyboard.press('End');
    assert.equal(await page.locator('[data-decision-mode="crowding"]').getAttribute('aria-selected'),'true');
    await page.locator('[data-decision-mode="trend"]').click();
    await page.selectOption('#decisionSectorScope','day');
    const samples=page.locator('.decision-samples');
    assert((await samples.innerText()).includes('首板日流通市值'));
    assert((await samples.innerText()).includes('首板日成交额'));
    assert((await page.locator('.decision-diagnostic').innerText()).includes('最新日换手基准 65/67'));
    await page.locator('.decision-diagnostic summary').click();
    assert((await page.locator('.decision-diagnostic').innerText()).includes('尚未上市'));
    assert((await page.locator('.decision-diagnostic').innerText()).includes('参考价与前日收盘不连续'));
    await page.locator('.decision-diagnostic summary').click();
    assert((await page.locator('.decision-comparison').innerText()).includes('风险收敛'));
    assert((await page.locator('.decision-comparison').innerText()).includes('收益覆盖'));
    assert((await samples.innerText()).includes('华天科技'));
    assert((await samples.innerText()).includes('18.03'));
    assert((await samples.innerText()).includes('+3.61%'));
    assert((await samples.innerText()).includes('−4.85%')||(await samples.innerText()).includes('-4.85%'));
    assert((await samples.innerText()).includes('0.00%'),'real zero premium must remain zero');
    await page.selectOption('#decisionSampleDate','2026-09-18');
    assert((await samples.innerText()).includes('2026-09-17 首板 → 2026-09-18'));
    await page.selectOption('#decisionSampleDate','2026-09-21');
    assert.equal(await page.locator('.decision-help').first().innerText(),'口径');
    assert.equal(await page.locator('.decision-help').first().locator('svg').count(),1);
    assert(await page.locator('.decision-help').first().evaluate(el=>getComputedStyle(el).borderTopWidth==='0px'&&getComputedStyle(el).boxShadow==='none'));
    for(const width of [320,375,414,768,1440]){
      await page.setViewportSize({width,height:1000});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`research overflow ${width}`);
      await page.evaluate(()=>window.scrollTo(0,0));
      await page.screenshot({path:path.join(output,`research-${width}-top.png`)});
      await page.screenshot({path:path.join(output,`research-${width}.png`),fullPage:true});
      for(const key of ['relay','crowding']){
        await page.locator(`[data-decision-mode="${key}"]`).click();
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${key} overflow ${width}`);
        await page.evaluate(()=>window.scrollTo(0,0));
        await page.screenshot({path:path.join(output,`${key}-${width}-top.png`)});
      }
      await page.locator('[data-decision-mode="trend"]').click();
    }
    await page.locator('[data-mode="trend"]').screenshot({path:path.join(output,'first-board-1440.png')});
    await page.selectOption('#decisionEvalWindow','3');
    assert((await page.locator('[data-mode="trend"]').innerText()).includes('近3日'));
    assert.equal(await page.evaluate(()=>TradeDecision.state.research.conclusion.evaluation_window),5);
    await page.selectOption('#decisionEvalWindow','5');
    await page.selectOption('#decisionSectorScope','recent');
    const sector=research.first_board_sectors.groups.find(g=>g.name==='半导体概念');
    await page.fill('#decisionSectorSearch',sector.name);
    await page.locator('#decisionSectorSearch').press('Tab');
    const sectorButton=page.locator(`[data-decision-sector="${sector.code}"]`);
    await sectorButton.click();
    assert((await page.locator('.decision-sector-selection').innerText()).includes('42/43'));
    assert((await page.locator('.decision-sector-selection').innerText()).includes('+1.26%'));
    assert((await samples.innerText()).includes('43 个事件'));
    assert((await page.locator('.decision-sector-research').innerText()).includes('板块全部个股数'));
    assert((await page.locator('.decision-sector-research').innerText()).includes('同日首板数量'));
    assert((await page.locator('.decision-sector-research').innerText()).includes('板块当日涨幅中位数'));
    assert((await page.locator('.decision-sector-research').innerText()).includes('540 家'));
    const lastCoordRow=page.locator('.decision-sector-research table').nth(1).locator('tbody tr').last();
    assert((await lastCoordRow.innerText()).includes('已确认 12 家'));
    assert((await lastCoordRow.innerText()).includes('+2.74%'));
    await lastCoordRow.locator('summary').click();
    assert((await lastCoordRow.innerText()).includes('华天科技'));
    await lastCoordRow.locator('summary').click();
    const allDetail=samples.locator('details').filter({has:page.locator('summary', {hasText:'查看全部'})});
    await allDetail.locator(':scope > summary').click();
    const sampleRows=allDetail.locator('tbody tr');
    assert.equal(await sampleRows.count(),43);
    for(const row of await sampleRows.all()) {
      const text=await row.innerText();
      assert(sector.members.some(t=>text.includes(t)), 'every sample belongs to selected sector');
    }
    await allDetail.locator(':scope > summary').click();
    await page.locator('.decision-sector-research').screenshot({path:path.join(output,'sector-1440.png')});
    await page.setViewportSize({width:375,height:1000});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'selected sector does not overflow at 375');
    await page.locator('.decision-sector-research').screenshot({path:path.join(output,'sector-375.png')});
    await page.setViewportSize({width:1440,height:1000});
    const aligns=await page.evaluate(()=>{
      const rect=s=>document.querySelector(s).getBoundingClientRect();
      const a=rect('#decisionResearchDate'),b=rect('[data-decision-action="load-research"]'),c=rect('[data-decision-action="build-research"]');
      const title=rect('.decision-title-with-help .decision-heading-label'),help=rect('.decision-title-with-help .decision-help');
      return {toolbar:Math.max(a.top,b.top,c.top)-Math.min(a.top,b.top,c.top)<=1&&Math.max(a.bottom,b.bottom,c.bottom)-Math.min(a.bottom,b.bottom,c.bottom)<=1,
        title:Math.abs((title.top+title.bottom-help.top-help.bottom)/2)<=1};
    });
    assert(aligns.toolbar, 'date and evaluation buttons must have aligned top and bottom edges');
    assert(aligns.title,'caliber and five-dimension title must be vertically centered');
    await page.selectOption('#decisionEvalWindow','3');
    assert((await samples.innerText()).includes(`${sector.windows['3'].total} 个事件`));
    await page.selectOption('#decisionSectorScope','day');
    await page.selectOption('#decisionSampleDate','2026-09-18');
    assert((await samples.innerText()).includes(`${sector.daily['2026-09-18'].total} 个事件`));
    await page.selectOption('#decisionSectorScope','recent');
    await page.selectOption('#decisionEvalWindow','5');
    await page.fill('#decisionSectorSearch','');
    await page.locator('#decisionSectorSearch').press('Tab');
    await page.locator('[data-sector-page="1"]').click();
    assert((await page.locator('.decision-sector-pager').innerText()).includes('2 /'));
    await page.selectOption('#decisionSectorKind','industry');
    assert.equal(await page.locator('[data-decision-sector][aria-pressed=true]').getAttribute('data-decision-sector'),'');
    await page.selectOption('#decisionSectorSort','p25');
    const sortedCodes=await page.locator('.decision-sector-research table').first().locator('[data-decision-sector]').evaluateAll(xs=>xs.map(x=>x.dataset.decisionSector));
    const p25s=sortedCodes.map(code=>research.first_board_sectors.groups.find(g=>g.code===code).windows['5'].p25);
    assert(p25s.every((v,i)=>i===0||p25s[i-1]>=v),'sort by low quartile must sort numeric values');
    await page.fill('#decisionSectorSearch','查无此板块');
    await page.locator('#decisionSectorSearch').press('Tab');
    assert((await page.locator('.decision-sector-research').innerText()).includes('暂无符合条件的记录'));
    await page.fill('#decisionSectorSearch','');
    await page.locator('#decisionSectorSearch').press('Tab');
    await page.selectOption('#decisionSectorKind','concept');
    await page.selectOption('#decisionSectorMinimum','1');
    assert(!(await page.locator('#decisionResearchResult').innerText()).includes('NaN'));
    const help=page.locator('.decision-help').first();
    await help.focus();
    await page.waitForSelector('#decisionTooltip:popover-open');
    assert((await page.locator('#decisionTooltip').innerText()).includes('此前10日'));
    assert.equal(await help.getAttribute('aria-expanded'),'true');
    await page.screenshot({path:path.join(output,'caliber-1440.png')});
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#decisionTooltip:popover-open').count(),0);
    assert.equal(await help.getAttribute('aria-expanded'),'false');
    await help.hover();
    await page.waitForSelector('#decisionTooltip:popover-open');
    await page.keyboard.press('Escape');
    // Research filters must leave the assessment unchanged, and reads never build.
    assert.equal(await page.evaluate(()=>JSON.stringify(TradeDecision.state.research.conclusion)),JSON.stringify(plan.conclusion));
    await page.locator('[data-decision-action="load-research"]').click();
    await page.waitForFunction(()=>document.getElementById('decisionResearchStatus').textContent.includes('最新评估已读取'));
    await page.fill('#decisionResearchDate','2026-09-18');
    await page.locator('[data-decision-action="load-research"]').click();
    await page.waitForFunction(()=>document.getElementById('decisionResearchResult').textContent.includes('暂无评估'));
    assert(!(await page.locator('#decisionResearchResult').innerText()).includes('2.0481'));
    await page.evaluate(()=>switchTab('decision-plan'));
    await page.waitForSelector('#decisionPlanResult .decision-chain');
    assert((await page.locator('#decisionPlanResult').innerText()).includes(plan.conclusion.headline));
    await page.fill('#decisionPlanDate','2026-09-18');
    await page.locator('[data-decision-action="load-plan"]').click();
    await page.waitForFunction(()=>document.getElementById('decisionPlanResult').textContent.includes('所选日期尚无'));
    assert(!(await page.locator('#decisionPlanResult').innerText()).includes('2.0481'));
    await page.evaluate(()=>switchTab('decision-live'));
    await page.waitForSelector('#decisionLiveResult .decision-summary');
    for(const width of [320,375,414,768,1440]){
      await page.setViewportSize({width,height:1000});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`tracking overflow ${width}`);
      await page.screenshot({path:path.join(output,`tracking-${width}-top.png`)});
    }
    failTracking=true;
    await page.locator('[data-decision-action="load-live"]').click();
    await page.waitForFunction(()=>document.getElementById('decisionLiveStatus').textContent.includes('测试：'));
    assert((await page.locator('#decisionLiveResult').innerText()).includes('当前跟踪不可用'));
    await page.locator('#decisionLiveDate').focus();
    assert((await page.locator('#decisionLiveDate').evaluate(el=>getComputedStyle(el).outlineStyle))!=='none');
    const touch=await browser.newPage({viewport:{width:375,height:900},hasTouch:true,isMobile:true});
    await touch.route('**/api/**',intercept);
    await touch.goto(`${base}/ui?decision=research&date=2026-09-21`,{waitUntil:'domcontentloaded'});
    await touch.waitForSelector('#decisionResearchResult #decisionEvalWindow');
    assert(await touch.locator('#decisionResearchDate').evaluate(el=>el.getBoundingClientRect().height>=44),'touch date control needs 44px');
    assert(await touch.locator('[data-decision-action="load-research"]').evaluate(el=>el.getBoundingClientRect().height>=44),'touch button needs 44px');
    await touch.locator('.decision-help').first().tap();
    await touch.waitForSelector('#decisionTooltip:popover-open');
    assert(await touch.locator('#decisionTooltip').evaluate(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight;}));
    assert(await touch.locator('.decision-help').first().evaluate(el=>el.getBoundingClientRect().height>=44));
    await touch.screenshot({path:path.join(output,'caliber-375.png')});
    await touch.locator('.decision-help').first().tap();
    assert.equal(await touch.locator('#decisionTooltip:popover-open').count(),0,'second tap dismisses caliber');
    await touch.locator('[data-mode="trend"]').screenshot({path:path.join(output,'first-board-375.png')});
    await touch.close();
    assert(referenceRequests>0,'plan link must load the exact snapshot');
    assert.equal(writeRequests,0,'reading plans must not write');
    assert.equal(errors.length,0,errors.join('\n'));
    fs.writeFileSync(path.join(output,'checks.json'),JSON.stringify({widths:[320,375,414,768,1440],planRequests,researchRequests,referenceRequests,writeRequests,errors,status:'pass'},null,2));
    console.log('Independent mode research, exact snapshot links, date isolation, filters that preserve assessments, five widths, aligned condition rows, neutral unmet states, frozen first-board checks, no cross-mode ranking, Vibe caliber interactions, date isolation and no-write checks passed.');
  } finally {await browser.close();}
})().catch(err=>{console.error(err);process.exit(1);});
