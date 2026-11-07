const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const requests=[],timers=new Map();let next=0;
const context={window:{},document:{addEventListener(){}},WeakMap,AbortController,URLSearchParams,
 setTimeout(fn,delay){const id=++next;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);},
 fetch(url,options={}){return new Promise((resolve,reject)=>{
  if(options.signal?.aborted)return reject(Object.assign(new Error('aborted'),{name:'AbortError'}));
  options.signal?.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})),{once:true});
  requests.push({url,options,resolve,reject});
 });}};
vm.createContext(context);vm.runInContext(fs.readFileSync('shilun/static/theme-cycles.js','utf8'),context);
function node(){const retry={},repair={},body={innerHTML:''};return {dataset:{},classList:{add(){}},setAttribute(){},
 set innerHTML(value){this.markup=value;this.mounts=(this.mounts||0)+1;},get innerHTML(){return this.markup;},
 querySelector(q){return q==='.tc-body'?body:q==='[data-tc-repair]'?repair:retry;},retry,repair,body};}
const api=context.window.ThemeCycles,day='2026-11-02';
const data=(job={status:'missing'})=>({date:day,status:'not_materialized',message:'尚未归档',repair_supported:true,repair_job:job});
const respond=(index,value,status=200)=>requests[index].resolve({ok:status===200,status,json:async()=>value});
const drain=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
const fire=delay=>{const [id,t]=[...timers].find(([,t])=>t.delay===delay)||[];assert(t,`missing timer ${delay}`);timers.delete(id);t.fn();};
(async()=>{
 const root=node(),read=api.mount(root,day);respond(0,data());await read;
 assert.equal(root.repair.disabled,false);
 const first=root.repair.onclick();root.repair.onclick();assert.equal(requests.length,2,'double click submits one task');
 assert.equal(requests[1].options.method,'POST');fire(25000);await drain();
 assert.equal(requests.length,3,'timed-out POST reads durable state before allowing another submission');
 respond(2,data({date:day,status:'running',stage_label:'回补同日原因',progress:{done:1,total:5}}));await first;
 assert.equal(root.repair.disabled,true);assert(root.body.innerHTML.includes('正在回补分析'));
 const mounts=root.mounts;fire(2500);await drain();
 assert.equal(root.mounts,mounts,'polling keeps the existing page instead of flashing loading markup');
 respond(3,data({date:day,status:'complete',stage_label:'已完成'}));await drain();
 assert.equal(root.repair.disabled,false);
 const retry=root.repair.onclick();assert.equal(requests.length,5);
 assert.equal(requests[4].options.signal.aborted,false,'retry POST gets a fresh signal');
 respond(4,{detail:'连接暂不可用'},503);await retry;
 assert.equal(root.repair.disabled,false);assert(root.body.innerHTML.includes('连接暂不可用'));
 const old=root.repair.onclick(),next=api.mount(root,'2026-11-03');
 assert.equal(requests[5].options.signal.aborted,true,'date change aborts old submission UI');
 respond(6,{...data(),date:'2026-11-03'});await next;await old;
 assert.equal(root.dataset.date,'2026-11-03');assert(!root.body.innerHTML.includes('连接暂不可用'));
 console.log('theme recovery UI: explicit POST, duplicate suppression, timeout readback, fresh retry, quiet polling and date isolation passed');
})().catch(e=>{console.error(e);process.exitCode=1});
