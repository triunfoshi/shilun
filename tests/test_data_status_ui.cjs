// Offline checks of the production renderer; fixtures are not live data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('shilun/static/app.js', 'utf8');
const nodes = Object.fromEntries(['globalDate', 'dataStatus', 'dataPipelineStatus', 'dataScheduleStatus', 'dataStatusGrid', 'dataStatusRefresh', 'dataSyncSummary'].map(id => [id, {
  value: '2026-11-02', textContent: '', innerHTML: '', disabled: false, attributes: {},
  setAttribute(key, value) { this.attributes[key] = value; },
}]));
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const requests = [];
const context = {document: {getElementById: id => nodes[id]}, runtimeStatus: {}, escapeHtml,
  selectedBenchmarkTicker: () => '000001.SH', formatError: error => error.message,
  fetchJson: url => new Promise((resolve, reject) => requests.push({url, resolve, reject})), console};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('let dataStatusRequestId = 0;'), source.indexOf('async function syncIndexHistoryYear()')), context);
const minute = {key: 'minute_bars', label: '分钟行情', tier: 'enhanced', ok: false, status: 'partial',
  detail: '120/126 个窗口', source: 'fixture', impact_on_miss: '基准缺口',
  storage: {status: 'available', earliest_date: '2026-10-28', latest_date: '2026-11-02', record_count: 488, date_count: null},
  coverage: {scope: '候选与基准', granularity: '5分钟'},
  usage: {status: 'integrated', label: '源码已接入', consumers: ['候选评分', '盘中回放'], note: '源码接入不代表当前页面统计了实际调用次数。'},
  schedule: {status: 'enabled', location: 'ECS', jobs: [{id: 'minute', schedule: '周一至周五 15:15'}]},
  last_run: {status: 'partial', updated_at: '2026-11-02T07:15:29Z', fetched_count: 120, failed_count: 6},
  extras: {settled: 0}};
const html = context.renderDataStatusCard(minute);
assert(html.includes('部分成功'));
assert(html.includes('2026-10-28 至 2026-11-02'));
assert(html.includes('488 个证券日窗口'));
assert(!html.includes('null 个归档日'));
assert(html.includes('周一至周五 15:15'));
assert(html.includes('2026-11-02 15:15'));
assert(html.includes('失败 6'));
assert(html.includes('已完成追踪</dt><dd>0</dd>'));
assert(html.includes('候选与基准 · 5分钟'));
assert(html.includes('候选评分 · 盘中回放'));
assert(html.includes('源码已接入'));
assert(html.includes('aria-controls="sync-detail-minute_bars"'));
const unknown = context.renderDataStatusCard({...minute, key: 'unregistered', storage: {status: 'unknown'}, schedule: {status: 'configured'}, last_run: {}, label: '<img src=x onerror=1>', extras: {description: '<script>x</script>'}});
assert(unknown.includes('日期范围待核验'));
assert(unknown.includes('已配置 · 待确认'));
assert(unknown.includes('暂无执行回执'));
assert(!unknown.includes('云端定时'));
assert(!unknown.includes('<img'));
assert(!unknown.includes('<script>'));
const pending = context.renderDataStatusCard({...minute, status: 'scheduled', storage: {status: 'empty'}, schedule: {status: 'manual'}});
assert(pending.includes('待运行'));
assert(pending.includes('尚无归档'));
assert(!pending.includes('影响：'));
const stale = context.renderDataStatusCard({...minute, schedule: {status: 'stale', jobs: [{hour: '17', minute: '40', day_of_week: 'mon-fri', next_run_at: '2026-11-02T17:40:00+08:00'}]}});
assert(stale.includes('曾启用 · 待确认'));
assert(stale.includes('候选评分 · 盘中回放'));
assert(!stale.includes('下次运行'));
const grouped = context.syncScheduleGroups([{hour: '23', minute: '40', day_of_week: 'mon-fri'}, {hour: '17', minute: '40', day_of_week: 'mon-fri'}, {hour: '19', minute: '40', day_of_week: 'mon-fri'}]);
assert.equal(grouped[0], '工作日 17:40 / 19:40 / 23:40');
nodes['sync-detail-minute_bars'] = {hidden: true};
const disclosure = {attributes: {'aria-controls': 'sync-detail-minute_bars', 'aria-expanded': 'false'}, dataset: {label: '5分钟行情'}, getAttribute(key) { return this.attributes[key]; }, setAttribute(key, value) { this.attributes[key] = value; }};
context.toggleSyncDetails(disclosure);
assert.equal(nodes['sync-detail-minute_bars'].hidden, false);
assert.equal(disclosure.attributes['aria-expanded'], 'true');
assert(context.renderDataStatusCard(minute).includes('aria-expanded="true"'));
context.toggleSyncDetails(disclosure);
assert.equal(nodes['sync-detail-minute_bars'].hidden, true);

(async () => {
  const old = context.loadDataStatus();
  nodes.globalDate.value = '2026-11-03';
  const fresh = context.loadDataStatus();
  requests[1].resolve({overall_status: 'scheduled', message: 'new date', datasets: [{...minute, detail: 'fresh response'}]});
  await fresh;
  requests[0].resolve({overall_status: 'blocked', message: 'old date', datasets: [{...minute, detail: 'stale response'}]});
  await old;
  assert(nodes.dataStatusGrid.innerHTML.includes('fresh response'));
  assert(!nodes.dataStatusGrid.innerHTML.includes('stale response'));
  assert.equal(nodes.dataStatusRefresh.disabled, false);
  assert.equal(nodes.dataStatusGrid.attributes['aria-busy'], 'false');
  const failed = context.loadDataStatus();
  requests[2].reject(new Error('network failed'));
  await failed;
  assert(nodes.dataStatus.textContent.includes('可点击刷新重试'));
  assert.equal(nodes.dataStatusGrid.innerHTML, '');
  assert.equal(nodes.dataPipelineStatus.textContent, '');
  assert.equal(nodes.dataStatusRefresh.disabled, false);
  console.log('Data status UI: dated inventory, schedule evidence, partial/unknown, escaping and request ordering passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
