// Offline contract check: run the production first-board renderer with dated fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = Object.fromEntries(['firstboardDate', 'firstboardRoot', 'firstboardStatus', 'firstboardRefresh', 'firstboardLadder', 'firstboardNext'].map(id => [id, {
  value: '2026-09-21', innerHTML: '', textContent: '', disabled: false,
  setAttribute() {}, querySelectorAll() { return []; },
}]));
let payload = {date: '2026-09-21', available: true, source: 'eastmoney_dated_pool', snapshot_mode: 'settled',
  total_zt: 4, first_count: 4, amount_coverage: {auction: 3, seal: 2, total: 4}, stocks: [
    {code: '000001', name: '样本甲', amount: 8e8, auction_amount: 29723219.01, seal_amount: 234560000},
    {code: '000002', name: '样本乙', auction_amount: 0, seal_amount: null},
    {code: '000003', name: '样本丙', auction_amount: null, seal_amount: 9876},
    {code: '000004', name: '<img onerror=1>', auction_amount: 12.34, seal_amount: null},
  ]};
const context = {document: {getElementById: id => nodes[id], addEventListener() {}},
  AbortController, URLSearchParams, setTimeout, clearTimeout,
  fetch: async () => ({ok: true, json: async () => payload})};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync('shilun/static/board-research.js', 'utf8'), context);
(async () => {
  await context.BoardResearch.load('firstboard');
  let html = nodes.firstboardRoot.innerHTML;
  assert.equal((html.match(/<th scope="col"(?:\s[^>]*)?>/g) || []).length, 12);
  assert.equal((html.match(/colspan="12"/g) || []).length, 4);
  assert.match(html, /aria-sort="descending" data-sort-priority="1"[^>]*>竞价成交金额/);
  assert.match(html, /data-sort-priority="2"[^>]*>封单金额/);
  for (const expected of ['2972.32万', '2.35亿', '0.00元', '9876.00元', '12.34元', '竞价覆盖 3/4，封单覆盖 2/4']) assert.ok(html.includes(expected), expected);
  assert.match(html, /data-label="竞价成交金额"[^>]*>—<\/td>/);
  assert.match(html, /data-label="封单金额"[^>]*>—<\/td>/);
  assert.ok(html.includes('收盘归档的涨停价买入挂单资金'));
  assert.ok(!html.includes('<img onerror=1>'));
  const order = markup => [...markup.matchAll(/class="br-stock" data-action="stock" data-code="([^"]+)"/g)].map(m => m[1]);
  assert.deepEqual(order(html), ['000001','000004','000002','000003']);
  assert.deepEqual(payload.stocks.map(r => r.code), ['000001','000002','000003','000004'], 'Rendering must not mutate the dated payload');
  payload = {...payload, stocks: [
    {code:'tie-first', auction_amount:5, seal_amount:7},
    {code:'missing-auction-high-seal', auction_amount:null, seal_amount:1e12},
    {code:'auction-wins', auction_amount:6, seal_amount:0},
    {code:'tie-second', auction_amount:5, seal_amount:7},
    {code:'second-key-wins', auction_amount:5, seal_amount:8},
    {code:'zero-auction', auction_amount:0, seal_amount:null},
    {code:'missing-both', auction_amount:null, seal_amount:null},
    {code:'negative-is-unknown', auction_amount:-1, seal_amount:null},
  ]};
  await context.BoardResearch.load('firstboard');
  html = nodes.firstboardRoot.innerHTML;
  assert.deepEqual(order(html), ['auction-wins','second-key-wins','tie-first','tie-second','zero-auction','missing-auction-high-seal','missing-both','negative-is-unknown']);
  assert.ok(html.includes('竞价成交金额降序，再按封单金额降序'));
  payload = {...payload, snapshot_mode: 'live'};
  await context.BoardResearch.load('firstboard');
  html = nodes.firstboardRoot.innerHTML;
  assert.ok(html.includes('采集时快照的涨停价买入挂单资金'));
  assert.ok(!html.includes('收盘归档'));
  console.log('First-board amounts UI: amount priorities, stable ties, unknown/zero, units, coverage and escaping passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
