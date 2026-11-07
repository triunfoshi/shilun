const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function node() {
  const button = {disabled:true,textContent:'',addEventListener(){}};
  const repairButton = {disabled:true,textContent:'',addEventListener(){}};
  const body = {innerHTML:''};
  return {dataset:{},innerHTML:'',classList:{add(){}},setAttribute(k,v){this[k]=v;},
    querySelector(q){return q==='.tc-body'?body:q==='[data-tc-repair]'?repairButton:button;},body,button,repairButton};
}
const requests=[];
const context={window:{},document:{addEventListener(){}},WeakMap,AbortController,setTimeout,clearTimeout,URLSearchParams,
  fetch(url){return new Promise((resolve,reject)=>requests.push({url,resolve,reject}));}};
vm.createContext(context);vm.runInContext(fs.readFileSync('shilun/static/theme-cycles.js','utf8'),context);
const api=context.window.ThemeCycles;
const empty=d=>({date:d,status:'not_materialized',message:'所选日尚未生成'});
const history={id:'retail',name:'零售主线',core:{name:'国芳集团'},entered_at:'2026-09-07',exit_date:'2026-09-17',date:'2026-09-17',
  cycle_stage:'retreat',metrics:{core_return:-.1,back_return:-.1},history:[],entry_evidence:[],branches:[{name:'零售主线',source_topics:[{name:'新零售',code:'BK0825.DC'},{name:'零售概念',code:'BK1655.DC'}]}],
  front_feedback:{rows:[]},back_feedback:{rows:[]},post_exit:{date:'2026-09-22',anchor_date:'2026-09-17',sessions:3,
    label:'核心相对强势 · 抱团观察',note:'原样本不变',core:{name:'国芳集团',return:.2,excess_return:.18},
    benchmark:{name:'上证指数',return:.02},back_return:-.1,rows:[{role:'原后排',name:'<后排>',return:-.1,excess_return:-.12,today_return:null}],status:'ready'}};
const rendered=api.render({date:'2026-09-22',status:'ready',active:[],archived:[history],waiting:[],diagnostics:[],selection_diagnostics:[],rules:{version:'test'}});
assert(rendered.includes('板块退潮后 · 核心与后排跟踪'));
assert(rendered.includes('退出日固定核心') && rendered.includes('退潮') && rendered.includes('截至 2026-09-22'));
assert(rendered.includes('+18.00') && rendered.includes('超额（百分点）') && rendered.includes('原后排中位数'));
assert(rendered.includes('&lt;后排&gt;') && !rendered.includes('<后排>'));
assert(rendered.includes('新零售 BK0825.DC') && rendered.includes('零售概念 BK1655.DC'));
const comparison={...history,exit_date:null,date:'2026-11-02',core:{ticker:'002724.SZ',name:'海洋王'},
 member_observation:{date:'2026-11-02',members:[
  {ticker:'002724.SZ',name:'海洋王',role:'core_candidate',qualified_at:'2026-10-22',latest_limit_date:'2026-10-27',linkage:{date:'2026-11-02',stock_return:-.0999}},
  {ticker:'002242.SZ',name:'九阳<script>',role:'core_candidate',qualified_at:'2026-10-09',latest_limit_date:'2026-10-21',linkage:{date:'2026-11-02',stock_return:.0537}},
  {ticker:'000001.SZ',name:'旧日反馈样本',role:'core_candidate',qualified_at:'2026-10-09',linkage:{date:'2026-10-30',stock_return:.1234}},
  {ticker:'000002.SZ',name:'尚未认证',role:'core_candidate',qualified_at:'2026-11-03',linkage:{date:'2026-11-02',stock_return:.9999}},
 ]}};
const compareHtml=api.render({date:'2026-11-02',status:'ready',active:[comparison],archived:[],waiting:[],diagnostics:[],selection_diagnostics:[],rules:{version:'test'}});
assert(compareHtml.includes('当日合格核心对照 · 2026-11-02'));
assert(compareHtml.includes('入选锚点 海洋王') && compareHtml.includes('合格核心对照'));
assert(compareHtml.includes('+5.37%') && compareHtml.includes('-9.99%'));
assert(compareHtml.includes('九阳&lt;script&gt;') && !compareHtml.includes('九阳<script>'));
assert(!compareHtml.includes('+12.34%') && !compareHtml.includes('+99.99%'));
assert(compareHtml.includes('不按单日涨幅自动更换锚点'));
const missing=api.render({...empty('2026-09-23'),job:{date:'2026-09-23',status:'failed',stage_label:'核对数据日期与覆盖',missing:[{date:'2026-09-23',reason:'缺少<涨停原因>'}]}});
assert(missing.includes('核对数据日期与覆盖') && missing.includes('缺少&lt;涨停原因&gt;') && missing.includes('上一完整版本保留'));
assert(missing.includes('周期归档更新受阻') && missing.includes('只检查现有归档，不会补齐历史资料'));
const grouped=api.render({...empty('2026-11-02'),job:{date:'2026-11-02',status:'failed',stage_label:'核对数据日期与覆盖',missing:[{dataset:'limit_reasons',date:'2026-10-08'},{dataset:'limit_reasons',date:'2026-10-09'}]}});
assert(grouped.includes('2026-10-08、2026-10-09') && grouped.includes('当前任务报告的缺口'));
assert(!grouped.includes('后台正在更新'),'failed job cannot look like an ongoing task');
(async()=>{
  const root=node();
  const first=api.mount(root,'2026-09-21');const second=api.mount(root,'2026-09-22');
  requests[1].resolve({ok:true,json:async()=>empty('2026-09-22')});await second;
  requests[0].resolve({ok:true,json:async()=>({...empty('2026-09-21'),message:'旧日期不能出现'})});await first;
  assert.equal(root.dataset.date,'2026-09-22');assert(!root.body.innerHTML.includes('旧日期'));
  const failed=api.mount(root,'2026-09-22');requests[2].reject(new Error('<bad>'));await failed;
  assert.equal(root.dataset.state,'error');assert(root.body.innerHTML.includes('&lt;bad&gt;'));assert.equal(root.button.disabled,false);
  const retried=api.mount(root,'2026-09-22');requests[3].resolve({ok:true,json:async()=>empty('2026-09-22')});await retried;
  assert.equal(root.dataset.state,'empty');assert.equal(root['aria-busy'],'false');
  const wrong=api.mount(root,'2026-09-22');requests[4].resolve({ok:true,json:async()=>empty('2026-09-21')});await wrong;
  assert.equal(root.dataset.state,'error');assert(root.body.innerHTML.includes('日期不一致'));
  console.log('cycle UI: stale response, escaping, errors, retry, exact date passed');
})().catch(e=>{console.error(e);process.exit(1);});
