// Offline regression check of the real renderer; fixture values are test data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const staticDir = path.join(__dirname, '../shilun/static');
const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[x]));
const nodes = Object.fromEntries(['globalDate', 'reviewDate', 'reviewDateHint', 'reviewCommandBar', 'reviewReadButton', 'reviewLatestButton', 'reviewDashboardRoot', 'reviewBuildButton', 'reviewNotice', 'reviewFreshnessText', 'reviewFuturesSyncButton', 'reviewFuturesSyncStatus', 'sentimentMarketMoodValue', 'sentimentMarketMoodScore'].map(id => [id, {
  value: '', innerHTML: '', textContent: '', dataset: {}, disabled: false,
  setAttribute() {}, classList: {remove() {}, toggle() {}},
}]));
for (const [id, node] of Object.entries(nodes)) {
  if (id.startsWith('review')) nodes[id.replace('review', 'sentiment')] = {...node, dataset: {}};
}
const futuresTabs = ['TOTAL', 'IF', 'IH', 'IC', 'IM'].map(product => ({dataset: {reviewFuturesTab: product}, classList: {toggle(_name, active) { this.active = active; }}, setAttribute(_name, value) { this.selected = value; }}));
const futuresPanels = ['TOTAL', 'IF', 'IH', 'IC', 'IM'].map(product => ({dataset: {reviewFuturesPanel: product}, hidden: product !== 'TOTAL'}));
const dates = ['2026-08-04', '2026-08-05', '2026-08-06', '2026-08-07', '2026-08-10', '2026-08-11', '2026-08-12'];
const payload = {
  analysis_date: dates.at(-1), action: {headline: '测试市场', priorities: ['不应重复的旧结论']},
  generated_at: '2026-08-12T19:15:00+08:00',
  freshness: {alerts: ['东方财富行业映射缺口 1 只；未使用旧来源补齐。', '2026-08-12 previous_limit_pool 上游补数不可用']},
  market: {
    posture: {doctrine: '趋势观察', cap_scope: '量能不足，回避300亿以上大票，优先情绪前排', reasons: ['证据一', '证据二']},
    volume: {current_yi: 18731, ma20_yi: 21248, series: dates.map((date, i) => ({date, amount_yi: i === 2 ? null : 18731 + i * 500}))},
    breadth: {up_ratio: .322, up_count: 1787, down_count: 3647, flat_count: 114},
    indices: [{name: '测试指数', close: 4000, pct_chg: .01, ma20: null}],
    futures: {history: dates.map((date, i) => ({analysis_date: date, products: i === 1 ? [{
      status: 'ok', product: 'IF', contract: 'IF2608', data_date: date,
      top20_long: 100, top20_short: 140, top20_long_change: 10, top20_short_change: 15,
      citic_long: null, citic_short: null,
    }] : []}))},
  },
  sentiment: {market_mood: {label: '活跃', code: 'active', score: 72}, ladder: {source_date: dates.at(-1), source_label: '本地日线与涨停价核验', levels: [{level: 2, count: 9,
    stocks: Array.from({length: 9}, (_, i) => ({name: i === 8 ? '<img onerror=1>' : `股票${i}`, ticker: `00000${i}`}))}]}},
};
payload.sentiment.limit_up_wave={relation:'mixed',label:'供给与指数信号混合',current_count:125,previous_count:47,baseline_20d:48.5,supply_change:125/47-1,supply_vs_baseline:125/48.5-1,index_change:.013431,index_vs_ma20:-.06058475,sample_days:20};
payload.sentiment.downside_risk = {stocks: [{name: '<img onerror=1>', theme: '农业', position: 'high'}]};
const context = {document: {getElementById: id => nodes[id], querySelectorAll: selector => selector.includes('data-mood-stage') ? [] : selector.includes('futures-tab') ? futuresTabs : futuresPanels}, escapeHtml: esc,
  fetch: async () => ({ok: true, json: async () => payload}), console};
context.window = context;
vm.createContext(context);
const app = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8');
vm.runInContext(app.slice(app.indexOf('function formatSignedContracts('), app.indexOf('function marketV2Tone(')), context);
vm.runInContext(fs.readFileSync(path.join(staticDir, 'review-dashboard.js'), 'utf8'), context);
(async () => {
  await context.loadReviewDashboard({force: true});
  const html = nodes.reviewDashboardRoot.innerHTML;
  assert(!html.includes('review-expectation-stack'));
  assert(!html.includes('不应重复的旧结论'));
  assert(!html.includes('只执行红K净流入、结构修复520、周K突破三种战法'));
  assert(html.includes('review-posture-evidence'));
  assert(html.includes('1项当前提醒'));
  assert(!html.includes('2项当前提醒'));
  assert(html.includes('<summary>生成时补数记录 · 2026-08-12 19:15:00+08:00</summary>'));
  assert(html.includes('前一交易日涨停样本 上游补数不可用'));
  payload.pipeline_status = {tasks:[{stage:'dependency:industry_taxonomy',status:'retry',attempts:1,max_attempts:5,
    error_message:'分类返回日期不一致',next_retry_at:'2026-08-12T12:05:00+00:00'},
    {stage:'review',status:'waiting',blocked_by:['dependency:industry_taxonomy']}]};
  await context.loadReviewDashboard({force:true});
  assert(nodes.reviewDashboardRoot.innerHTML.includes('2项同步待处理'));
  assert(nodes.reviewDashboardRoot.innerHTML.includes('等待：行业分类'));
  assert(nodes.reviewDashboardRoot.innerHTML.includes('下次重试：'));
  delete payload.pipeline_status;
  assert(!html.includes('review-index-evidence'));
  assert(html.indexOf('判断依据') < html.indexOf('review-index-recap-grid'));
  assert(html.includes('<p>量能不足，回避300亿以上大票，优先情绪前排</p>'));
  assert(!html.includes('review-market-grid'));
  assert(html.includes('review-breadth-strip'));
  assert(html.includes('上涨占比 <strong>32.2%</strong>'));
  assert(html.includes('共7个交易日'));
  assert(html.includes('review-volume-gap">缺 1 日成交额'));
  assert.equal((html.match(/class="review-volume-bar"/g) || []).length, 6); // Missing day has no bar.
  assert(!html.includes('NaN'));
  assert(html.includes('review-volume-ref-labels')); // Names sit in reserved right margin.
  assert(html.includes('MA20 —'));
  assert.equal((html.match(/class="market-v2-futures-product-card"/g) || []).length, 5);
  for (const date of dates) assert(html.includes(date));
  assert(html.includes('IF2608'));
  assert(html.includes('data-label="净空日增减" class="is-risk">+5'));
  assert(html.includes('data-label="中信多单持仓" class="">—'));
  for (const title of ['市场情绪与龙头谱系', '板块现金流与情绪周期', '标签溢价与预期管理', '三战法候选', '成交额20亿新面孔', '复盘摘要']) assert(!html.includes(title));
  await context.loadSentimentDashboard({force: true});
  const sentimentHtml = nodes.sentimentDashboardRoot.innerHTML;
  assert.equal(nodes.sentimentMarketMoodValue.textContent, '活跃');
  assert.equal(nodes.sentimentMarketMoodScore.textContent, '评分 72 / 100');
  assert(sentimentHtml.includes('收盘验证'));
  const mainP2 = sentimentHtml;
  assert(!sentimentHtml.includes('review-v2-archive'));
  assert(!sentimentHtml.includes('连板梯队'));
  for (const title of ['板块现金流与情绪周期', '标签溢价与预期管理', '三战法候选', '成交额20亿新面孔', '复盘摘要']) assert(!sentimentHtml.includes(title));
  assert(!mainP2.includes('龙头谱系'));
  assert(!mainP2.includes('role="tablist"')); // Three shared-date tabs live outside the snapshot root.
  assert(mainP2.includes('id="sentiment-downside-panel"'));
  const selections=[];
  context.SentimentWorkspace={select:key=>selections.push(key),mount:()=>{}};
  context.selectSentimentSection('theme');
  context.selectSentimentSection('downside');
  assert.deepEqual(selections,['rotation','today']);
  for (const title of ['大盘与量能', '<h3>姿态依据</h3>', 'review-futures-tab-IF']) assert(!sentimentHtml.includes(title));
  assert(sentimentHtml.includes('&lt;img onerror=1&gt;')); // Cross-tab stock names are escaped.
  assert(!sentimentHtml.includes('<img onerror=1>'));
  assert(!sentimentHtml.includes('review-evidence-rail'));
  assert(sentimentHtml.includes('review-downside-strip'));
  assert(!sentimentHtml.includes('当前短线方向及周期见上方'));
  assert(!sentimentHtml.includes('review-legacy-topic-evidence'));
  assert(!sentimentHtml.includes('当日题材证据 · '));
  assert(sentimentHtml.includes('当日同涨') && sentimentHtml.includes('20日结构分歧'));
  assert(sentimentHtml.includes('指数仍低于MA20') && sentimentHtml.includes('结构分歧不表示今日一涨一跌'));
  assert(!sentimentHtml.includes('供给与指数信号混合'));
  assert(sentimentHtml.includes('跌停集中度'));
  payload.previous_trade_date = '2026-08-11';
  payload.source_sync = [{date:'2026-08-11', status:'unavailable'}];
  payload.sentiment.previous_limit_up_count = null;
  payload.sentiment.yesterday_limit_up_feedback = {sample_size:0, median:null};
  await context.loadSentimentDashboard({force: true});
  assert(!nodes.sentimentDashboardRoot.innerHTML.includes('昨日 0')); // Current-day cards have an independent dated endpoint.
  payload.source_sync = [{date:'2026-08-11', status:'cached'}];
  payload.sentiment.previous_limit_up_count = 0;
  await context.loadSentimentDashboard({force: true});
  assert(!nodes.sentimentDashboardRoot.innerHTML.includes('昨日涨停数据待补齐'));
  payload.sentiment.theme_evidence = {themes: ['A', 'B', 'C', 'D'].map(name => ({
    name, current_count: 3, previous_count: 2, gates: {}, member_count:50,
    attention_core:{name:`参考${name}<script>`,sessions:17,return:1.33,drawdown:-.02}, attention_rank:1,
    round: {max_board:4, complete:false, leaders:[{name:`龙头${name}<script>`, max_board:4, startup_return:.25, price_basis:'raw_close_fallback'}]},
    front_support: {theme: name, front_count: 1, rows: [{ticker: name, name: `${name}<script>`, front:true, boards:2, return_1d:-.1}]},
  }))};
  payload.sentiment.heat_history = {days:[{date:'2026-08-12',limit_up:4,highest_consec:3,broken_rate:.2,
    leaders:[{name:'并列甲'},{name:'并列乙<script>'}], leaders_complete:true}], leader_lineage:[]};
  payload.sentiment.limit_up_wave = {sample_days:18,baseline_20d:null,missing_dates:['2026-08-01','2026-08-02'],available_sample_median:69};
  payload.sentiment.limit_down_count = 0;
  payload.sentiment.downside_risk = {count_ready:true, limit_down_count:0, status:'confirmed', penalty:0,
    position_counts:{high:0,middle:0,low:0,unknown:0}};
  await context.loadSentimentDashboard({force:true});
  const revised = nodes.sentimentDashboardRoot.innerHTML;
  assert(!revised.includes('review-theme-track')); // Legacy DC topics never leak into today's snapshot.
  assert(!revised.includes('参考A'));
  assert(revised.includes('覆盖 18/20'));
  assert(revised.includes('已有样本中位数 69'));
  assert(!revised.includes('A<script>'));
  assert(revised.includes('高位 <b>0</b>'));
  assert(revised.includes('不适用'));
  assert(!revised.includes('NaN'));
  assert(html.includes('review-futures-tab-IF'));
  context.selectReviewFutures('IH');
  assert.equal(futuresTabs[2].classList.active, true);
  assert.equal(futuresPanels[2].hidden, false);
  assert.equal(futuresPanels[0].hidden, true);
  assert.equal(nodes.reviewBuildButton.disabled, false);
  // The page owns its date, independent of the hidden global controls.
  nodes.globalDate.value = '2026-09-09';
  nodes.reviewDate.value = '2026-08-12';
  const requests = [];
  context.fetch = async (url, options) => {
    // Supplementary chart reads are tested separately from snapshot command routing.
    if (!url.includes("/review/market-facts")) requests.push({url, options});
    return {ok: true, json: async () => payload};
  };
  nodes.sentimentDate.value = '2026-08-11';
  await context.changeSentimentDate();
  assert.equal(requests[0].url, '/api/v1/review/dashboard?date=2026-08-11');
  assert.equal(requests.at(-1).url, '/api/v1/review/refresh?date=2026-08-11');
  assert.equal(requests.at(-1).options.method, 'POST');
  const repairs = requests.filter(r=>r.options?.method==='POST').length;
  await context.loadSentimentDashboard({force:true});
  assert.equal(requests.filter(r=>r.options?.method==='POST').length,repairs); // No automatic retry loop on an unresolved gap.
  assert.equal(nodes.reviewDate.value, '2026-08-12');
  await context.materializeSentimentDashboard();
  assert.equal(requests.at(-1).url, '/api/v1/review/refresh?date=2026-08-11');
  await context.materializeReviewDashboard();
  assert.equal(requests.filter(r=>r.url.includes('/review/refresh')).at(-1).url, '/api/v1/review/refresh?date=2026-08-12');
  assert.equal(requests.filter(r=>r.url.includes('/review/refresh')).at(-1).options.method, 'POST');
  await context.syncReviewFutures();
  assert(requests.some(item => item.url === '/api/v1/review/futures/refresh?date=2026-08-12'));
  assert.equal(nodes.reviewFuturesSyncButton.disabled, false);
  const originalVolume = payload.market.volume;
  payload.market.volume = {ma20_yi: null, series: [{date: dates[0], amount_yi: null}, {date: dates[1], amount_yi: ''}]};
  await context.loadReviewDashboard({force: true});
  assert(nodes.reviewDashboardRoot.innerHTML.includes('成交额有效数据不足'));
  assert(!nodes.reviewDashboardRoot.innerHTML.includes('review-volume-line'));
  assert(nodes.reviewDashboardRoot.innerHTML.includes('每日数据'));
  payload.market.volume = {ma20_yi: null, series: [{date: dates[0], amount_yi: 21000}, {date: dates[1], amount_yi: 21000}]};
  await context.loadReviewDashboard({force: true});
  assert(!nodes.reviewDashboardRoot.innerHTML.includes('review-volume-rule is-ma20'));
  assert(!nodes.reviewDashboardRoot.innerHTML.includes('NaN'));
  assert(!nodes.reviewDashboardRoot.innerHTML.includes('Infinity'));
  payload.market.volume = originalVolume;
  nodes.reviewDate.value = '2026-08-15'; // Weekend: explain which session is displayed.
  await context.changeReviewDate();
  assert(nodes.reviewDateHint.textContent.includes('所选 2026-08-15'));
  assert(nodes.reviewDateHint.textContent.includes('实际展示 2026-08-12'));
  await context.latestReviewDate();
  assert.equal(requests.at(-1).url, '/api/v1/review/dashboard');
  assert.equal(nodes.reviewDate.value, '2026-08-12');
  context.fetch = async () => ({ok: true, json: async () => ({status:'not_materialized', analysis_date: '2026-08-11'})});
  nodes.reviewDate.value = '2026-08-11';
  await context.changeReviewDate();
  assert(nodes.reviewDashboardRoot.innerHTML.includes('生成所选日复盘'));
  assert(!nodes.reviewDashboardRoot.innerHTML.includes('生成今日复盘'));
  context.fetch = async () => ({ok: false, status:503, json: async () => ({detail:'云端数据库读取超时，请检查网络后重试。'})});
  await context.materializeReviewDashboard();
  assert(nodes.reviewNotice.textContent.includes('检查网络后重试'));
  assert.equal(nodes.reviewBuildButton.disabled, false);
  // Editing a date mid-read must not display a stale response under the new date.
  let finishOldRead;
  context.fetch = url => url.endsWith('2026-08-10')
    ? new Promise(resolve => {finishOldRead = resolve;})
    : Promise.resolve({ok:true, json:async () => ({status:'not_materialized', analysis_date:'2026-08-11'})});
  nodes.reviewDate.value = '2026-08-10';
  const pendingRead = context.changeReviewDate();
  nodes.reviewDate.value = '2026-08-11';
  await context.changeReviewDate();
  finishOldRead({ok:true, json:async () => ({status:'not_materialized', analysis_date:'2026-08-10'})});
  await pendingRead;
  assert(nodes.reviewDashboardRoot.innerHTML.includes('2026-08-11 尚无复盘快照'));
  assert(!nodes.reviewDashboardRoot.innerHTML.includes('2026-08-10 尚无复盘快照'));
  if (process.env.REVIEW_PREVIEW_HTML) {
    const styles = ['tokens.css', 'app.css', 'market-v2.css', 'review-dashboard.css']
      .filter(file => fs.existsSync(path.join(staticDir, file)))
      .map(file => `<link rel="stylesheet" href="file://${path.join(staticDir, file)}">`).join('');
    // Preview is generated from a labelled fixture, never connected to production APIs.
    process.stdout.write(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<body><main class="review-dashboard" style="padding:16px;max-width:1440px;margin:auto"><p>离线测试数据 · 仅用于版式验收</p><div class="review-dashboard-root">${html}</div></main></body></html>`);
  } else console.log('review UI assertions passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
