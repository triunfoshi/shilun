// Reconcile cached malformed hypotheses without changing stored snapshots.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const app = fs.readFileSync('shilun/static/app.js','utf8');
const context = {escapeHtml:esc,document:{getElementById(){return null;}}};context.window=context;
vm.runInNewContext(app.slice(app.indexOf('function getWavePresentation('),app.indexOf('\nfunction compactMarketStoryLabel(')),context);
const evidence = {
  status:'ready',benchmark_name:'测试指数',
  pattern_forecast:{wave_analysis:{current_wave:'wave_5',current_wave_label:'第5浪上行',wave_phase:'impulse',reasoning:'完整结构，进入第5浪',
    wave_points:[['2026-09-01',100,'L','wave_1_start'],['2026-09-02',120,'H','wave_1_end'],['2026-09-03',95,'L','wave_2_end'],['2026-09-04',115,'H','wave_3_end'],['2026-09-05',85,'L','wave_4_end']].map(([date,price,kind,label])=>({date,price,kind,label})),
    wave_rule_violations:['第2浪跌破第1浪起点','第4浪侵入第1浪区间'],fibonacci_levels:[{label:'第5浪目标位',level:160,kind:'target'}]},
    primary_pattern:{display_name:'回落',description:'仅为测试'},tomorrow_guide:'核对价格与量能',
    key_levels_tomorrow:[{label:'观察支撑',level:85,direction:'support',note:'测试支撑条件'},{label:'另一条同价支撑',level:85,direction:'support'}],
    data_gaps:[{field:'可用指标',status:'integrated',note:'无需再次提醒'},{field:'尚缺指标',status:'missing',note:'需要补充证据'},{field:'外部波浪理论文档（人工标注位）',status:'missing',note:'旧上传提示'}]},
  chart_data:{wave_series:[100,120,95,115,85].map((close,i)=>({date:`2026-09-0${i+1}`,open:close-1,high:close+2,low:close-3,close}))}
};
const before = JSON.stringify(evidence), state=context.getWavePresentation(evidence), html=context.renderWaveKlineChart(evidence);
assert(state.invalid);
assert.equal(state.title,'推动浪假设未通过');
assert.equal(state.levels.length,1);
assert.equal(state.gaps.length,1);
assert(state.gaps[0].includes('尚缺指标：需要补充证据'));
assert(!html.includes('wave_1_start') && !html.includes('第5浪目标位'));
assert(html.includes('观察高点') && html.includes('观察低点'));
assert(html.includes('data-wave-structure="invalidated"'));
assert(!html.includes('[object Object]') && !html.includes('NaN'));
assert.equal((html.match(/class="rw-point-number"/g)||[]).length,5);
assert.equal(JSON.stringify(evidence),before,'Rendering must not rewrite archived evidence');
const dashboard=fs.readFileSync('shilun/static/review-dashboard.js','utf8');
vm.runInNewContext(dashboard.replace('    window[handlerName("loadReviewDashboard")] =', '    if(isIndex)window.renderPatternsTest=renderPatterns;\n    window[handlerName("loadReviewDashboard")] ='),context);
const rendered=context.renderPatternsTest({market:{indices:[{ticker:'000001.SH',name:'测试指数',pattern_evidence:evidence}]}});
assert(rendered.includes('推动浪假设未通过'));
assert(!rendered.includes('完整结构，进入第5浪'));
assert(!rendered.includes('[object Object]'));
assert(!rendered.includes('旧上传提示') && !rendered.includes('无需再次提醒'));
assert(rendered.includes('规则反证') && rendered.includes('需要补充证据'));
const noViolations=JSON.parse(before);noViolations.pattern_forecast.wave_analysis.wave_rule_violations=[];
assert(context.getWavePresentation(noViolations).invalid,'Old hypotheses without findings still need endpoint rule checks');
const noPrices={...evidence,chart_data:{wave_series:[{date:'bad',open:null,high:0,low:0,close:0}]}};
assert(context.renderWaveKlineChart(noPrices).includes('数据不足'));
evidence.pattern_forecast.wave_analysis.wave_rule_violations.push('<script>bad()</script>');
assert(!context.renderPatternsTest({market:{indices:[{ticker:'000001.SH',pattern_evidence:evidence}]}}).includes('<script>'));
console.log('wave: rule gate, Chinese labels, immutable snapshots, structured gaps, escaped text passed');
