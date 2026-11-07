/* A round without any previous notes must visibly open and offer its real fills. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source=fs.readFileSync('shilun/static/trade-journal.js','utf8');
function node(){return {innerHTML:'',textContent:'',dataset:{},attributes:{},setAttribute(k,v){this.attributes[k]=v;},querySelector(){return null;},scrollIntoView(){this.scrolled=true;}};}
const detail={episode:{episode_id:'shanghai',ticker:'600838.SH',name:'上海九百',start_date:'2026-10-29',end_date:'2026-11-02',status:'closed',quantity:0,opening_quantity:0,opening_cost_status:'known',fees:33.02,realized_pnl:-5440.02,closed_pnl:-5440.02,remaining_cost:0,qualification:{},review_progress:{status:'not_started',label:'待复盘'}},decisions:[],expectation_checks:[],links:[],fills:[{fill_id:'buy1',side:'buy',date:'2026-10-29',executed_at:'2026-10-29T09:32:00+08:00',quantity:1300,price:14.20,time_precision:'execution_time'},{fill_id:'buy2',side:'buy',date:'2026-10-29',quantity:300,price:13.66,time_precision:'date_only'},{fill_id:'buy3',side:'buy',date:'2026-10-29',quantity:600,price:13,time_precision:'date_only'},{fill_id:'sell1',side:'sell',date:'2026-11-02',quantity:2200,price:11.34,time_precision:'date_only'}]};
function setup(fetcher){
  const nodes={},handoffs=[],events={};
  const get=id=>nodes[id]||(nodes[id]=node());
  const context={console,URLSearchParams,document:{getElementById:get,addEventListener:(event,handler)=>events[event]=handler},fetch:fetcher,JournalWorkspace:{setEpisode:d=>handoffs.push(d)},location:{search:''}};
  context.window=context;vm.runInNewContext(source,context);const ui=context.TradeJournal;ui.state.items=[detail.episode];return {ui,nodes,get,handoffs,events};
}
test('Shanghai with no notes shows loading immediately then a real-fill review entry',async()=>{
  let resolve;const pending=new Promise(r=>resolve=r);
  const {ui,get,handoffs}=setup(async()=>pending);
  const selecting=ui.selectEpisode('shanghai');
  assert.equal(get('tradeJournalStatus').dataset.state,'loading');assert.match(get('tradeJournalStatus').textContent,/正在打开上海九百的复盘/);assert.equal(get('tradeJournalDetail').attributes['aria-busy'],'true');
  resolve({ok:true,json:async()=>detail});await selecting;
  assert.equal(ui.state.items[0].review_progress.status,'not_started');assert.equal(ui.state.selected,'shanghai');assert.equal(handoffs.at(-1).episode.episode_id,'shanghai');assert.equal(get('tradeJournalDetail').attributes['aria-busy'],'false');
  const main=get('tradeJournalDetail').innerHTML.match(/data-journal-section="当时的操作与预期"([\s\S]*?)data-journal-section="预期核验"/)[1];
  assert.match(main,/上海九百 · 开始复盘/);assert.match(main,/成交记录已打开/);assert.match(main,/还没有填写复盘/);
  for(const id of ['buy1','buy2','buy3','sell1'])assert(main.includes('data-fill="'+id+'"'));
  assert.equal((main.match(/从本笔开始复盘/g)||[]).length,4);assert.match(main,/1,300 股/);assert.match(main,/2,200 股/);assert.match(main,/撮合成交时间/);assert.match(main,/仅日期；撮合时点未知/);
});
test('failure is visible in the list status instead of only in a hidden detail pane',async()=>{
  const {ui,get,handoffs}=setup(async()=>({ok:false,status:503,json:async()=>({detail:'连接暂不可用'})}));
  await ui.selectEpisode('shanghai');
  assert.equal(ui.state.detail,null);assert.equal(handoffs.at(-1),null);assert.equal(get('tradeJournalStatus').dataset.state,'error');assert.match(get('tradeJournalStatus').textContent,/上海九百的复盘未打开.*连接暂不可用/);assert.match(get('tradeJournalStatus').textContent,/重新点击股票名称重试/);
});
test('a late selection cannot replace the current round or its status',async()=>{
  let resolve;const pending=new Promise(r=>resolve=r);let count=0;
  const other={...detail,episode:{...detail.episode,episode_id:'other',name:'另一笔交易'}};
  const {ui,get,handoffs}=setup(async()=>++count===1?pending:{ok:true,json:async()=>other});
  ui.state.items.push(other.episode);const first=ui.selectEpisode('shanghai');await ui.selectEpisode('other');resolve({ok:true,json:async()=>detail});await first;
  assert.equal(ui.state.detail.episode.episode_id,'other');assert.equal(handoffs.at(-1).episode.episode_id,'other');assert.match(get('tradeJournalStatus').textContent,/已打开另一笔交易的复盘/);
});
