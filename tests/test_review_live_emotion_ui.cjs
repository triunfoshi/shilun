// Verify the actual live renderer, including missing data and manual refresh.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const nodes = Object.fromEntries(['sentimentLiveEmotionRoot', 'sentimentLiveEmotionRefresh', 'sentimentLiveEmotionStatus'].map(id => [id, {
  innerHTML: '', textContent: '', setAttribute() {},
}]));
let payload = {
  available: true, snapshot_mode: 'settled', snapshot_date: '2026-09-14',
  zt_count: 55, dt_count: 16, zb_count: 30, max_boards: 4, lianban_count: 11,
  promotion_rate: .2683, promotion_base_label: '2026-09-14 昨日涨停池',
  scope_label: '东方财富原始板池口径，包含主板、创业板和科创板', raw_pool_counts: {zt_count: 55},
  down_stocks: [{name: '曲江文旅', code: '600706', pct: -9.98937}, {name: '<img onerror=1>', code: '000001', pct: -10}],
};
const requests = [];
const context = {
  document: {getElementById: id => nodes[id]}, console, AbortController, setTimeout, clearTimeout,
  fetch: async url => {requests.push(url); return {ok: true, json: async () => payload};},
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../shilun/static/review-dashboard.js'), 'utf8'), context);
(async () => {
  await context.loadSentimentLiveEmotion({force: true});
  let html = nodes.sentimentLiveEmotionRoot.innerHTML;
  assert.equal(requests.at(-1), '/api/v1/review/live-emotion?force=true');
  assert(html.includes('<span>涨停</span><strong>55</strong>'));
  assert(html.includes('<span>跌停</span><strong>16</strong>'));
  assert(html.includes('包含主板、创业板和科创板'));
  assert(html.includes('2026-09-14 昨日涨停池'));
  assert(!html.includes('按上述范围纳入')); // No redundant 55-to-55 explanation.
  assert(html.includes('<strong>曲江文旅</strong><small>600706</small>'));
  assert(html.includes('data-label="实际涨跌幅" class="review-roster-negative">-9.99%'));
  assert(html.includes('review-down-roster-table'));
  assert.equal((html.match(/<td data-label="跌停状态">已确认跌停/g) || []).length,2);
  assert(html.includes('&lt;img onerror=1&gt;'));
  assert(!html.includes('<img onerror=1>'));
  assert.equal(nodes.sentimentLiveEmotionRefresh.disabled, false);
  payload = {...payload, down_stocks:[{name:'ST测试',code:'600003',pct:-4.98,pct_source:'daily_pre_close'},
    {name:'科创测试',code:'688001',pct:-19.99,pct_source:'daily_pre_close'},
    {name:'缺参考价',code:'002001',pct:null,verification_source:'全市场收盘价与跌停价核验'}]};
  await context.loadSentimentLiveEmotion({force:true});
  html=nodes.sentimentLiveEmotionRoot.innerHTML;
  assert(html.includes('-4.98%') && html.includes('-19.99%'));
  assert(html.includes('同日日线／前收参考价'));
  assert(html.includes('已确认跌停<small>实际涨跌幅缺失</small>'));
  assert(!html.includes('涨幅待取'));
  payload = {...payload, dt_count: null, down_stocks: null};
  await context.loadSentimentLiveEmotion({force: true});
  html = nodes.sentimentLiveEmotionRoot.innerHTML;
  assert(html.includes('<span>跌停</span><strong>—</strong>'));
  assert(html.includes('跌停池待取'));
  assert(!html.includes('查看跌停名单'));
  payload = {available: false, reason: '日期与名单数量不匹配'};
  await context.loadSentimentLiveEmotion({force: true});
  assert(nodes.sentimentLiveEmotionRoot.innerHTML.includes('实时打板情绪暂不可用'));
  console.log('live emotion UI: scope, rounded limits, missing data, escaping and refresh passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
