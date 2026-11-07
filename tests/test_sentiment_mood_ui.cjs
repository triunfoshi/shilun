const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const stages=['cold','normal','active','hot'].map(code=>({
  dataset:{moodStage:code},attrs:{},marker:{hidden:true},current:false,
  classList:{toggle(_name,value){this.owner.current=value;}},
  setAttribute(key,value){this.attrs[key]=value;},removeAttribute(key){delete this.attrs[key];},
  querySelector(){return this.marker;},
}));
for(const stage of stages)stage.classList.owner=stage;
const nodes={sentimentMarketMoodValue:{dataset:{}},sentimentMarketMoodScore:{},sentimentMarketMoodAsOf:{},sentimentMarketMood:{dataset:{}},sentimentDate:{value:'2026-11-02'}};
const ctx={document:{getElementById:id=>nodes[id],querySelectorAll:()=>stages}};ctx.window=ctx;
const source=fs.readFileSync('shilun/static/review-dashboard.js','utf8');
vm.runInNewContext(source.replace('    window[handlerName("loadReviewDashboard")] =','    if (!isIndex) window.moodTest={syncMarketMood};\n    window[handlerName("loadReviewDashboard")] ='),ctx);
for(const code of ['hot','cold','normal','active','hot']){
 ctx.moodTest.syncMarketMood({code,label:code,score:82,date:'2026-11-02',downside_penalty:12});
 assert.equal(stages.filter(s=>s.current).length,1);
 assert.equal(stages.find(s=>s.current).dataset.moodStage,code);
 assert.equal(stages.filter(s=>s.attrs['aria-current']==='step').length,1);
 assert.equal(stages.filter(s=>!s.marker.hidden).length,1);
 assert(nodes.sentimentMarketMoodScore.textContent.includes('82 / 100'));
 assert.equal(nodes.sentimentMarketMoodAsOf.textContent,'2026-11-02');
}
ctx.moodTest.syncMarketMood(null);
assert.equal(stages.filter(s=>s.current).length,0,'unknown mood never leaves the previous stage highlighted');
assert(stages.every(s=>s.marker.hidden));
assert.equal(nodes.sentimentMarketMoodValue.textContent,'待判断');
assert.equal(nodes.sentimentMarketMoodScore.textContent,'数据待补齐');
console.log('sentiment mood: unique selected stage, accessible marker, date and unknown reset passed');
