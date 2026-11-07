/* Navigation integration: retired entry points share one journal, with explicit legacy access. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('shilun/static/app.js', 'utf8');
const ids = ['review','trade-journal','holdings','trading','permission','decision-plan','decision-live','decision-research'];
const counts = {journal:0, workspace:0, holdings:0, trading:0, decision:0};
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) nodes.set(id, {id, tagName:id.startsWith('journal')||id==='tradeJournalPlanSection'?'DETAILS':'DIV', hidden:false, open:false, textContent:'', classList:{toggle(){}}, scrollIntoView(){this.scrolled=true;}});
  return nodes.get(id);
}
const buttons = ids.map(id=>({dataset:{tab:id}, hidden:['holdings','trading','permission','decision-plan','decision-live'].includes(id), classList:{toggle(){}}, setAttribute(){}, closest(){return null;}}));
const panels = ids.filter(id=>id!=='holdings').map(id=>node('tab-'+id));
const ctx = {document:{querySelectorAll(selector){return selector==='.tab-button'?buttons:panels;},querySelector(){return {classList:{toggle(){}}};},getElementById:node,body:{dataset:{},classList:{toggle(){}}}}, holdingsState:{loading:false}, scheduleStorylineDocking(){},loadHoldings(){counts.holdings++;},loadTradingWorkspace(){counts.trading++;},loadSelectionPool(){},window:{TradeJournal:{enter(){counts.journal++;}},JournalWorkspace:{enter(){counts.workspace++;}},TradeDecision:{enter(){counts.decision++;}}}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('const tabPresentation ='),source.indexOf('function handleWorkspaceTabKeydown')),ctx);
for(const id of ['holdings','trading','permission','decision-plan','decision-live']) {
  ctx.switchTab(id);
  assert.equal(node('pageTitle').textContent,'交易记录与复盘');
  assert.equal(node('tab-trade-journal').hidden,false);
  assert.equal(node('tab-trading').hidden,true);
}
assert.equal(counts.trading,0);
assert.equal(counts.decision,0);
assert.equal(counts.holdings,1,'account facts read only on explicit account-tool entry');
assert.equal(node('journalAccountTools').open,true);
assert.equal(node('tradeJournalPlanSection').open,true);
assert.equal(counts.journal,5);
ctx.switchTab('decision-research');
assert.equal(node('pageTitle').textContent,'市场模式观察');
assert.equal(ctx.document.body.dataset.workspace,'research');
ctx.switchTab('decision-plan',{legacy:true});
assert.equal(counts.decision,2,'legacy research remains explicitly reachable');
const html=fs.readFileSync('shilun/static/index.html','utf8');
const idsInHtml=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
assert.equal(new Set(idsInHtml).size,idsInHtml.length,'moving account tools must not duplicate form IDs');
const journal=html.slice(html.indexOf('<section id="tab-trade-journal"'),html.indexOf('<section id="tab-analysis"'));
assert(journal.includes('id="journalAccountTools"'));
assert(journal.includes('id="tradingOrderPanel"'));
assert(!html.includes('id="tab-holdings" class="tab-panel"'));
console.log('journal navigation integration passed');
