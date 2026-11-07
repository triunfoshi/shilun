// Exercise the real renderer: missing classifications must not look like empty days.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ids = ['boardeffectsDate','boardeffectsDays','boardeffectsMetric','boardeffectsRoot',
  'boardeffectsStatus','boardeffectsRefresh','boardeffectsLabels','effects-returns-tab','effects-labels-tab','br-samples-industry'];
const nodes = Object.fromEntries(ids.map(id=>[id,{value:'',innerHTML:'',textContent:'',setAttribute(){}}]));
nodes.boardeffectsDays.value='30'; nodes.boardeffectsMetric.value='avg';
const stat={sample:0,sample_total:0,avg:null,median:null,win_rate:null};
const coverage={days:0,expected:1,prior_expected:0,missing:['2026-08-13']};
const daily={...stat,date:'2026-08-13',cohort_date:'2026-08-12',available:false,
  missing_reason:'首板日行业分类待补齐',rolling:{...stat,expected_days:1,covered_days:0},
  industry_coverage:{top_industries:[]}};
const group={id:'industry',name:'首板·涨停数前二行业',description:'昨日名单',overall:stat,recent:stat,
  prior:stat,change:stat,coverage,by_regime:{},segments:{open_ret:stat,intraday_ret:stat},
  daily:[daily],rows:[],industry_note:'首板发生日的行业'};
group.regime_coverage={classified:0,total:1,missing_cohort_dates:['2026-08-12']};
const payload={available:true,seal_curve:[],strategies:[group],missing_days:[],calendar_shortfall:0,
  regime_dates:{'2026-08-13':{cohort_date:'2026-08-12',regime:'环境未覆盖',
    reason:'待补齐：<跌停价>',score:null}}};
const events={};
const context={document:{getElementById:id=>nodes[id],addEventListener(name,fn){events[name]=fn;}},AbortController,
  URLSearchParams,setTimeout,clearTimeout,fetch:async()=>({ok:true,json:async()=>payload})};
context.window=context;vm.createContext(context);
vm.runInContext(fs.readFileSync('shilun/static/board-research.js','utf8'),context);
(async()=>{
  await context.BoardResearch.load('boardeffects');
  let html=nodes.boardeffectsRoot.innerHTML;
  assert.ok(html.includes('行业分组可计算 0/1 日'));
  assert.ok(html.includes('首板日行业分类待补齐'));
  assert.ok(!html.includes('>无样本</td>'));
  assert.ok(html.includes('情绪日期与缺口'));
  assert.ok(html.includes('已覆盖 0/1 日'));
  assert.ok(html.includes('待补齐：&lt;跌停价&gt;'));
  assert.ok(html.includes('名单日待补齐 2026-08-12'));
  daily.available=true;daily.missing_reason='';coverage.days=1;coverage.missing=[];
  daily.industry_coverage.top_industries=[{name:'<img onerror=1>',limit_up_count:3}];
  context.BoardResearch.renderEffects();html=nodes.boardeffectsRoot.innerHTML;
  assert.ok(html.includes('>无样本</td>'));
  assert.ok(html.includes('首板日 / 前二行业'));
  assert.ok(html.includes('&lt;img onerror=1&gt;（3只）'));
  assert.ok(!html.includes('行业分组可计算'));
  assert.ok(!html.includes('<img onerror=1>'));
  group.regime_coverage={classified:1,total:1,missing_cohort_dates:[]};
  payload.regime_dates['2026-08-13']={cohort_date:'2026-08-12',regime:'情绪中',label:'普通',score:50};
  context.BoardResearch.renderEffects();html=nodes.boardeffectsRoot.innerHTML;
  assert.ok(html.includes('已覆盖 1/1 日'));
  assert.ok(html.includes('按昨日名单日'));
  assert.ok(!html.includes('名单日待补齐'));
  const suspended={date:'2026-09-22',cohort_date:'2026-09-21',code:'002860',name:'星帅尔',
    price_status:'suspended',price_reason:'全天停牌，未产生次日收益',ret:null,
    price_evidence_source:'tushare_suspend_d',price_evidence_date:'2026-09-22',groups:['首板打板']};
  payload.price_exceptions=[suspended];group.rows=[suspended];
  daily.sample_total=1;daily.sample=0;
  daily.price_coverage=group.price_coverage={observed:0,total:1,suspended:1,unresolved:0,conflicts:0};
  context.BoardResearch.renderEffects();html=nodes.boardeffectsRoot.innerHTML;
  assert.ok(html.includes('行情核验 · 全天停牌 1 · 待核验 0'));
  assert.ok(html.includes('Tushare 停复牌记录 · 2026-09-22'));
  assert.ok(html.includes('全天停牌 1 · 行情待补 0'));
  assert.ok(!html.includes('部分缺失'));
  events.toggle({target:{open:true,dataset:{samples:'industry'}}});
  assert.ok(nodes['br-samples-industry'].innerHTML.includes('行情状态'));
  assert.ok(nodes['br-samples-industry'].innerHTML.includes('全天停牌，未产生次日收益'));
  assert.ok(!nodes['br-samples-industry'].innerHTML.includes('0.00%'));
  suspended.price_reason='<img onerror=1>';
  context.BoardResearch.renderEffects();html=nodes.boardeffectsRoot.innerHTML;
  assert.ok(html.includes('&lt;img onerror=1&gt;'));
  assert.ok(!html.includes('<img onerror=1>'));
  console.log('Industry UI: classifications, suspensions, sample status, dates and escaping passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
