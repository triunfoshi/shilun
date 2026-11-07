const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = {};
const node = id => nodes[id] ||= {value:'', innerHTML:'', textContent:'', hidden:false, disabled:false, listeners:{},
  setAttribute(){}, addEventListener(name, callback){this.listeners[name]=callback;}};
for(const id of ['firstboardDate','firstboardStrong','firstboardRefresh','firstboardStatus','strongStockFilter']) node(id);
const payload = date => ({status:'ready',date,counts:{qualified:1,pending:1},scanned_stocks:2,latest_known_stocks:2,
  history_sessions:120,rows:[{ticker:'600001.SH',name:'<img onerror=1>',industry:'行业',status:'qualified',tracking_complete:true,
    cycle:{start_date:'2026-08-06',boundary_confirmed:true,cumulative_return:0,drawdown:0},
    leg:{start_date:'2026-08-17',three_board_date:'2026-08-19',qualified_at:'2026-08-25',qualification_sessions:7,qualification_board_count:5,board_count:6,observed_sessions:10,window_end:'2026-08-28'},detail_id:'one'},
  {ticker:'600002.SH',name:'待确认',status:'pending',cycle:{},leg:{reasons:['本段未完成三连板']},detail_id:'two'}]});
let responses = [];
const context = {document:{getElementById:node,addEventListener(){}},URLSearchParams,AbortController,setTimeout,clearTimeout,
  fetch:async()=>({ok:true,json:()=>responses.shift()})}; context.window=context;
vm.createContext(context);
vm.runInContext(fs.readFileSync('shilun/static/strong-stocks.js','utf8'),context);
(async()=>{
  nodes.firstboardDate.value='2026-09-21'; responses.push(payload('2026-09-21'));
  await context.StrongStocks.load();
  let html=nodes.firstboardStrong.innerHTML;
  assert.ok(html.includes('7天5板') && html.includes('2026-08-06') && html.includes('2026-08-25'));
  assert.ok(html.includes('0.00%'));
  assert.ok(html.includes('&lt;img onerror=1&gt;') && !html.includes('<img onerror=1>'));
  assert.equal((html.match(/data-ss-detail=/g)||[]).length,1);
  nodes.strongStockFilter.listeners.change({target:{value:'pending'}});
  assert.ok(nodes.firstboardStrong.innerHTML.includes('本段未完成三连板'));
  assert.ok(!nodes.firstboardStrong.innerHTML.includes('7天5板'));
  let release;
  responses.push(new Promise(resolve=>{release=resolve;}));
  nodes.firstboardDate.value='2026-09-18'; const old=context.StrongStocks.load();
  await Promise.resolve();
  nodes.firstboardDate.value='2026-09-22'; responses.push(payload('2026-09-22'));
  await context.StrongStocks.load(); release(payload('2026-09-18')); await old;
  assert.equal(nodes.firstboardDate.value,'2026-09-22');
  assert.ok(nodes.firstboardStrong.innerHTML.includes('2026-09-22 收盘'));
  // Stored drawdowns are negative returns; larger magnitude goes first, while
  // unknown evidence follows a real zero. Sorting must not mutate the snapshot.
  const ranked=payload('2026-09-22');
  const template=ranked.rows[0];
  ranked.rows=[{...template,ticker:'000001.SZ',cycle:{drawdown:0}},
    {...template,ticker:'000002.SZ',cycle:{drawdown:-.35}},
    {...template,ticker:'000003.SZ',cycle:{drawdown:-.05}},
    {...template,ticker:'000004.SZ',cycle:{drawdown:null}}];
  responses.push(ranked);await context.StrongStocks.load();
  nodes.strongStockFilter.listeners.change({target:{value:'qualified'}});
  const sortedHtml=nodes.firstboardStrong.innerHTML;
  const order=[...sortedHtml.matchAll(/data-ss-stock="([^"]+)"/g)].map(m=>m[1]);
  assert.deepEqual(order,['000002.SZ','000003.SZ','000001.SZ','000004.SZ']);
  assert.deepEqual(ranked.rows.map(r=>r.ticker),['000001.SZ','000002.SZ','000003.SZ','000004.SZ']);
  assert(sortedHtml.includes('aria-sort="descending"') && sortedHtml.includes('按收盘回撤幅度从高到低'));
  context.StrongStocks.deactivate();
  console.log('Strong-stock UI: qualification dates, zero/missing, escaping, filters and out-of-order responses passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
