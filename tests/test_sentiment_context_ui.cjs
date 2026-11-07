const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const requests=[];
const ctx={window:{fetchJson:url=>new Promise((resolve,reject)=>requests.push({url,resolve,reject}))},Number,String,Math,encodeURIComponent};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('shilun/static/review-sentiment-context.js','utf8'),ctx);
const api=ctx.window.SentimentContext;
const payload=date=>({date,cycle:{available:true,window:3,trough_date:'2026-10-28',current_score:.6,day_n:3,range_position:.8,trend:'今日走强',tie_count:1,
 series:[{date:'2026-10-28',score:.1,limit_up:10,highest_consec:2,broken_rate:.3},{date,score:.6,limit_up:20,highest_consec:3,broken_rate:.1}]},
 industry:{available:true,total_limit_up:20,industry_valid_n:19,time_valid_n:18,missing_time_n:2,industry_count:1,concentration:.5,broken_available:false,
 themes:[{sector:'<芯片>',limit_up:10,highest:3,first_boards:8,consec_boards:2,broken:null,first_seal:'093000',last_seal:'143000',names:[{name:'<样本>',boards:3}]}],timeline:[{slot:'09:25—09:35',count:10}]}});
const html=api.render(payload('2026-10-30'));
assert(html.includes('情绪周期')&&html.includes('行业结构 · 发酵节奏'));
assert(html.includes('0.60')&&html.includes('80.0%')&&html.includes('第3天'));
assert(html.includes('&lt;芯片&gt;')&&!html.includes('<芯片>'));
assert(html.includes('&lt;样本&gt;')&&!html.includes('<样本>'));
assert(html.includes('炸—')&&html.includes('炸板池缺失'));
assert(html.includes('缺2只未分时段')&&html.includes('行业有效 19/20'));
const missing=api.render({date:'2026-10-30',cycle:{available:false,reason:'历史缺失<日期>',missing:[{date:'2026-10-29',reason:'炸板池缺失'}]},industry:{available:false,reason:'同日池缺失'}});
assert(missing.includes('历史缺失&lt;日期&gt;')&&missing.includes('同日池缺失'));
assert(!missing.includes('第0天')&&!missing.includes('当前情绪分 0'));
function root(){return {innerHTML:'',dataset:{},attrs:{},setAttribute(k,v){this.attrs[k]=v;},querySelector(){return {};}};}
(async()=>{
 const r=root(),first=api.mount(r,'2026-10-29'),second=api.mount(r,'2026-10-30');
 requests[1].resolve(payload('2026-10-30'));await second;const current=r.innerHTML;
 requests[0].resolve(payload('2026-10-29'));await first;assert.equal(r.innerHTML,current);
 const wrong=api.mount(r,'2026-11-02');requests[2].resolve(payload('2026-10-30'));await wrong;
 assert(r.innerHTML.includes('返回日期与所选日不一致'));
 assert.equal(r.attrs['aria-busy'],'false');
 console.log('sentiment context UI: exact dates, unknowns, return units, escaping and race passed');
})().catch(error=>{console.error(error);process.exit(1)});
