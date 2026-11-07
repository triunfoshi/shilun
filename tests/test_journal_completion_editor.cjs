/* Exercise the shipped completion editor with isolated DOM/API doubles; no live writes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../shilun/static/trade-journal.js'), 'utf8');
const decode = value => String(value ?? '').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
const attributes = text => Object.fromEntries(Array.from(text.matchAll(/([\w-]+)="([^"]*)"/g), m => [m[1], decode(m[2])]));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const review = (revision = 1, digest = 'facts-1', extra = {}) => ({
  latest_revision: revision, fact_digest: digest, current_state: 'draft', current_confirmed: false,
  record: {revision, state: 'draft', summary: '服务器旧总结', judgments: {}, evidence_refs: []}, ...extra,
});
const progress = {status: 'reviewed', current_confirmed: true};

function harness() {
  const nodes = {}, calls = [], events = {}, refreshedProgress = [];
  function node(id) {
    let html = '';
    return {
      id, textContent: '', dataset: {}, attributes: {}, disabled: false,
      setAttribute(key, value) { this.attributes[key] = value; },
      querySelector() { return null; }, querySelectorAll() { return []; },
      get innerHTML() { return html; },
      set innerHTML(value) {
        html = value;
        if (id !== 'tradeJournalReviewEditor') return;
        delete nodes.tradeJournalReviewForm;
        const match = value.match(/<form\b([^>]*)>([\s\S]*?)<\/form>/);
        if (match) nodes.tradeJournalReviewForm = parseForm(match[1], match[2]);
      },
    };
  }
  function parseForm(rawAttributes, html) {
    const attrs = attributes(rawAttributes), result = node(attrs.id), elements = [];
    for (const [key, value] of Object.entries(attrs)) {
      if (key.startsWith('data-')) result.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
    }
    for (const match of html.matchAll(/<input\b([^>]*)>|<textarea\b([^>]*)>([\s\S]*?)<\/textarea>|<select\b([^>]*)>([\s\S]*?)<\/select>/g)) {
      const field = attributes(match[1] || match[2] || match[4]);
      if (!field.name) continue;
      let value = field.value || '';
      if (match[2] !== undefined) value = decode(match[3]);
      if (match[4] !== undefined) {
        const options = Array.from(match[5].matchAll(/<option\b([^>]*)>/g));
        value = attributes((options.find(m => /\bselected\b/.test(m[1])) || options[0] || ['', ''])[1]).value || '';
      }
      elements.push({...field, value, checked: /\bchecked\b/.test(match[1] || '')});
    }
    elements.namedItem = name => elements.find(field => field.name === name);
    result.elements = elements;
    result.buttons = Array.from(html.matchAll(/<button\b([^>]*)>/g), m => ({...attributes(m[1]), disabled: /\bdisabled\b/.test(m[1])})).filter(b => b.type === 'submit');
    result.querySelectorAll = selector => selector === '[type=submit]' ? result.buttons : [];
    result.querySelector = selector => selector === '[type=submit]' ? result.buttons[0] || null : null;
    return result;
  }
  for (const id of ['tradeJournalReviewStatus', 'tradeJournalReview', 'tradeJournalReviewEditor', 'tradeJournalReviewSaveStatus', 'tradeJournalList', 'tradeJournalPagination', 'tradeJournalDetail', 'tradeJournalStatus']) nodes[id] = node(id);
  const badge = {outerHTML: ''};
  nodes.tradeJournalDetail.querySelector = selector => selector.includes('tj-review-progress') ? badge : null;
  const h = {nodes, calls, events, badge, refreshedProgress, wire: review(), handle: null};
  const context = {
    console, URLSearchParams, location: {search: ''},
    document: {getElementById: id => nodes[id], addEventListener: (name, listener) => (events[name] ||= []).push(listener)},
    JournalWorkspace: {setReviewProgress: value => refreshedProgress.push(value)},
    fetch: async (url, options) => {
      calls.push({url, options});
      if (h.handle) { const handled = await h.handle(url, options); if (handled) return handled; }
      if (options.method === 'POST' && url.endsWith('/review')) return response({record: {state: 'confirmed'}, review: h.wire});
      if (url.endsWith('/review')) return response(h.wire);
      if (url.includes('/sources?')) return response({items: [{source_id: 'source-1', filename: '交割单.csv'}]});
      if (url.endsWith('/episodes/ep-a')) return response({episode: {episode_id: 'ep-a', review_progress: progress}, review_progress: progress});
      throw new Error('Unexpected test request: ' + url);
    },
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(source, context, {filename: 'trade-journal.js'});
  h.ui = context.TradeJournal;
  h.ui.state.selected = 'ep-a';
  h.ui.state.detail = {episode: {episode_id: 'ep-a', name: '交易 A'}, fills: [], decisions: []};
  h.ui.state.items = [{episode_id: 'ep-a', name: '交易 A'}];
  h.postCalls = () => calls.filter(c => c.options.method === 'POST' && c.url.endsWith('/review'));
  h.submit = (form, action) => Promise.all(events.submit.map(listener => listener({target: form, submitter: form.buttons.find(b => b.value === action), preventDefault() {}})));
  return h;
}

test('both submit actions lock all buttons and reject repeated requests while saving', async t => {
  for (const action of ['confirmed', 'draft']) await t.test(action, async () => {
    const h = harness(), gate = deferred(), started = deferred();
    await h.ui.editProcessReview();
    const form = h.nodes.tradeJournalReviewForm;
    form.elements.namedItem('summary').value = '本轮已核对';
    form.elements.namedItem('process_confirmation').checked = true;
    h.handle = async (url, options) => {
      if (options.method === 'POST') { started.resolve(); await gate.promise; return response({record: {state: action}}); }
    };
    const saving = h.submit(form, action);
    await started.promise;
    assert.equal(form.dataset.saving, 'true');
    assert.equal(form.attributes['aria-busy'], 'true');
    assert.equal(form.buttons.length, 2);
    assert(form.buttons.every(button => button.disabled), 'both actions must be disabled together');
    await h.submit(form, action === 'draft' ? 'confirmed' : 'draft');
    assert.equal(h.postCalls().length, 1, 'repeated submit cannot race confirmed against draft');
    assert.equal(JSON.parse(h.postCalls()[0].options.body).state, action, 'the original submitter determines the state');
    gate.resolve();
    await saving;
    assert.equal(form.dataset.saving, 'false');
  });
});

test('a conflict preserves edited text, reloads the latest baseline and requires a fresh confirmation', async () => {
  const h = harness();
  await h.ui.editProcessReview();
  const first = h.nodes.tradeJournalReviewForm;
  const edits = {
    summary: '成交已逐笔核对\n未来预期继续观察 <保留原文>',
    overall_conclusion: '暂不能判断模式优势', judgment_entry: 'deviation', reason_entry: '未等原确认信号',
    evidence_dimension: 'entry', evidence_source_id: 'source-1', evidence_row: '8', evidence_text: '用户补充\n原始想法',
    revision_reason: '补充原始回忆',
  };
  for (const [name, value] of Object.entries(edits)) first.elements.namedItem(name).value = value;
  first.elements.namedItem('process_confirmation').checked = true;
  h.handle = async (url, options) => options.method === 'POST' ? response({detail: '事实指纹冲突'}, 409) : null;
  await h.submit(first, 'confirmed');
  assert.equal(first.dataset.stale, 'true');
  assert(first.buttons.every(button => button.disabled));
  assert(h.nodes.tradeJournalReviewSaveStatus.innerHTML.includes('重新读取并保留输入'));
  assert.equal(first.elements.namedItem('summary').value, edits.summary);

  h.wire = review(2, 'facts-2', {record: {revision: 2, state: 'draft', summary: '其他客户端的新总结', judgments: {}, evidence_refs: [{text: '最新版本已有证据'}]}});
  h.handle = null;
  await h.ui.editProcessReview();
  const restored = h.nodes.tradeJournalReviewForm;
  assert.notEqual(restored, first);
  assert.equal(restored.dataset.revision, '2');
  assert.equal(restored.dataset.factDigest, 'facts-2');
  assert.equal(restored.dataset.stale, 'false');
  for (const [name, value] of Object.entries(edits)) assert.equal(restored.elements.namedItem(name).value, value, name + ' survives reopening');
  assert.equal(restored.elements.namedItem('process_confirmation').checked, false, 'an old confirmation must not apply to new facts');
  await h.submit(restored, 'confirmed');
  assert.equal(h.postCalls().length, 1, 'restored inputs cannot silently confirm a changed baseline');
  restored.elements.namedItem('process_confirmation').checked = true;
  await h.submit(restored, 'confirmed');
  const body = JSON.parse(h.postCalls()[1].options.body);
  assert.equal(body.expected_revision, 2);
  assert.equal(body.expected_fact_digest, 'facts-2');
  assert.equal(body.summary, edits.summary);
  assert.equal(body.judgments.entry.reason, edits.reason_entry);
  assert.equal(body.judgments.entry.evidence_refs[0].text, edits.evidence_text);
  assert.equal(body.evidence_refs[0].text, '最新版本已有证据', 'recovery keeps references from the newly read record');
  assert.equal(h.ui.state.reviewPendingEdits['ep-a'], undefined, 'successful save clears only this round\'s recovery buffer');
  assert.equal(h.ui.state.items[0].review_progress.status, 'reviewed');
  assert.equal(h.ui.state.detail.review_progress.status, 'reviewed');
  assert(h.badge.outerHTML.includes('已复盘'));
  assert.equal(h.refreshedProgress.at(-1).status, 'reviewed');
});

test('switching rounds during the post-save refresh cannot publish the old round status to the new round', async () => {
  const h = harness(), reading = deferred(), gate = deferred();
  await h.ui.editProcessReview();
  const form = h.nodes.tradeJournalReviewForm;
  form.elements.namedItem('summary').value = 'A 已核对';
  form.elements.namedItem('process_confirmation').checked = true;
  h.handle = async (url, options) => {
    if (!options.method && url.endsWith('/review')) { reading.resolve(); await gate.promise; return response(review(2, 'facts-a-saved', {current_state: 'confirmed', current_confirmed: true})); }
  };
  const saving = h.submit(form, 'confirmed');
  await reading.promise;
  const b = {episode: {episode_id: 'ep-b', name: '交易 B'}, fills: [], decisions: [], review_progress: {status: 'not_started'}};
  h.ui.state.selected = 'ep-b';
  h.ui.state.detail = b;
  h.nodes.tradeJournalReviewStatus.textContent = 'B 尚未确认';
  gate.resolve();
  await saving;
  assert.equal(h.postCalls().length, 1);
  assert.equal(h.nodes.tradeJournalReviewStatus.textContent, 'B 尚未确认');
  assert.equal(h.ui.state.detail, b);
  assert.equal(h.ui.state.detail.review_progress.status, 'not_started');
  assert.equal(h.refreshedProgress.length, 0, 'the old round cannot refresh the new round overview');
  assert.equal(h.badge.outerHTML, '', 'the old round cannot replace the new round badge');
});
