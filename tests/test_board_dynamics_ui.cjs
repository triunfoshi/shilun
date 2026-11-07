const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const pending=new Map(), calls=[];
const context={Date,Map,Number,String,encodeURIComponent,console,window:{fetchJson:url=>{
 calls.push(url);return new Promise((resolve,reject)=>pending.set(new URL(url,'http://test').searchParams.get('date'),{resolve,reject}));
}}};
vm.createContext(context);vm.runInContext(fs.readFileSync('shilun/static/board-dynamics.js','utf8'),context);
const api=context.window.BoardDynamics;
function root(){return {innerHTML:'',dataset:{},elements:{},setAttribute(){},querySelector(key){return this.elements[key] ||= {};}};}
const payload=date=>({date,window_start:'2026-09-01',window_end:date,estimates:{},daily:[
 {date:'2026-09-01',promotion_rate:.2,broken_rate:.1,scope:'A'},
 {date:'2026-09-02',promotion_rate:null,broken_rate:.2,scope:'A'},
 {date:'2026-09-03',promotion_rate:.4,broken_rate:.3,scope:'B'},
 {date,promotion_rate:.5,broken_rate:.4,scope:'B',source:'<img src=x>',promoted:1,previous_up:2,broken:2,attempted:5}
],selected_day:{date,cohort_date:'2026-09-09',scope:'沪深非ST',
 money_effect:{status:'partial',median_pct:1.53,mean_pct:2.83,up_rate:.66,up_count:31,sample_n:47,total_n:48,missing_n:1,suspended_n:0,repeat_limit_n:11,repeat_limit_base:48,repeat_limit_rate:11/48,exceptions:[{ticker:'000001.SZ',name:'<img src=x>',reason:'当日行情缺失'}]},
 promotion:{status:'ready',overall:{base:48,promoted:11,rate:11/48},stages:[{label:'1进2',base:38,promoted:6,rate:6/38},{label:'2进3',base:6,promoted:3,rate:.5},{label:'3板及以上晋级',base:3,promoted:2,rate:2/3}],unknown_height_n:0},
 seal_quality:{status:'ready',total:125,never_broken:42,reopened:83,never_broken_rate:42/125,opening_seconds:21,late_seal:9,broken_pool:28,attempted:153,broken_rate:28/153,avg_broken_times:1.81,break_count_valid_n:125,seal_time_valid_n:125}}});
(async()=>{
 const r=root();const old=api.mountTrend(r,'2026-09-10');const fresh=api.mountTrend(r,'2026-09-11');
 pending.get('2026-09-11').resolve(payload('2026-09-11'));await fresh;
 const latest=r.innerHTML;pending.get('2026-09-10').resolve(payload('2026-09-10'));await old;
 assert.equal(r.innerHTML,latest,'old date cannot overwrite newer data');
 assert(!latest.includes('<img'));assert(latest.includes('&lt;img'));assert(latest.includes('覆盖 3/4日'));
 assert(latest.includes('当日评估 · 2026-09-11'));assert(latest.includes('昨日名单 2026-09-09'));
 assert(latest.includes('+1.53%'));assert(latest.includes('+2.83%'),'returns are percent points, not multiplied twice');
 assert(latest.includes('42 + 83 = 当日收盘涨停 125 家'));assert(latest.includes('首封≤09:35'));
 assert(latest.includes('收益有效 47 / 48 只'));assert(latest.includes('21家'));assert(latest.includes('9家'));
 assert.equal((latest.match(/class="bd-line"/g)||[]).length,4,'missing values and scope changes both split paths');
 const r2=root();await api.mountTrend(r2,'2026-09-11');assert.equal(calls.length,2,'cached date shares one request');
 const fail=api.mountTrend(r,'2026-09-12');pending.get('2026-09-12').reject(new Error('fixture unavailable'));await fail;
 assert(r.innerHTML.includes('role="alert"'));assert(r.querySelector('button').onclick);
 const retry=api.mountTrend(r,'2026-09-12');pending.get('2026-09-12').resolve(payload('2026-09-12'));await retry;
 assert(!r.innerHTML.includes('fixture unavailable'),'failure evicts cached promise');
 const partial=payload('2026-09-13');Object.assign(partial.selected_day.seal_quality,{never_broken:null,reopened:null,avg_broken_times:null,opening_seconds:null,late_seal:null,break_count_valid_n:2,seal_time_valid_n:3});
 const loadPartial=api.mountTrend(r,'2026-09-13');pending.get('2026-09-13').resolve(partial);await loadPartial;
 assert(r.innerHTML.includes('炸板次数覆盖2/125'));assert(r.innerHTML.includes('首封时间覆盖3/125'));
 assert(!r.innerHTML.includes('42 + 83 ='),'partial fields do not silently become full counts');
 const future=payload('2026-09-14');future.selected_day={date:'2026-09-13',money_effect:{median_pct:88}};
 const loadFuture=api.mountTrend(r,'2026-09-14');pending.get('2026-09-14').resolve(future);await loadFuture;
 assert(r.innerHTML.includes('2026-09-14 当日三项统计暂不可用'));assert(!r.innerHTML.includes('+88.00%'),'prior day cannot masquerade as selected day');
 console.log('board dynamics UI: dated facts, truthful coverage, return units, seal complements, gaps, XSS, race, cache and retry passed');
})().catch(e=>{console.error(e);process.exit(1)});
