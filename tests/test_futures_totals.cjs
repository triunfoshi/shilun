const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const context = {Number, String};
vm.createContext(context);
const code = fs.readFileSync('shilun/static/app.js', 'utf8');
vm.runInContext(code.slice(code.indexOf('function futuresProductHistory('), code.indexOf('function renderFuturesProductTable(')), context);
const day = '2026-09-18';
const products = ['IF','IH','IC','IM'].map((product,i) => ({status:'ok', product, data_date:day,
  contract:product+'2612', top20_long:100+i, top20_short:150+i,
  top20_long_change:10, top20_short_change:12, citic_long:20, citic_short:30,
  citic_long_change:2, citic_short_change:1}));
const rows = [{analysis_date:day, products}];
let total = context.futuresProductHistory(rows,'TOTAL')[0];
assert.equal(total.top20Long,406); assert.equal(total.top20Short,606);
assert.equal(total.top20NetShort,200); assert.equal(total.top20Change,8);
assert.equal(total.citicShort,120); assert.equal(total.citicNetShort,40); assert.equal(total.citicChange,-4);
assert.equal(context.futuresProductHistory(rows,'IF')[0].top20Short,150);
products[0].citic_short=null;
assert.equal(context.futuresProductHistory(rows,'TOTAL')[0].citicShort,null,'missing seat cannot become zero');
products[0].data_date='2026-09-17';
total=context.futuresProductHistory(rows,'TOTAL')[0];
assert.equal(total.top20Long,null,'a stale channel cannot enter total');
assert.match(total.contract,/3\/4/);
assert.equal(context.futuresProductHistory([{analysis_date:day,products:[]}],'TOTAL')[0].top20Short,null);
console.log('futures totals: positions, changes, missing seats and wrong dates passed');
