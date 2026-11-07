// Offline chart tests: real renderer/handlers, labelled fixtures, no production requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
let widget;
const context = {document: {getElementById: () => widget}};
context.window = context;
const source = fs.readFileSync(path.join(__dirname, '../shilun/static/review-dashboard.js'), 'utf8');
// Expose closure-local functions only inside this test VM, not in the shipped UI.
vm.runInNewContext(source.replace('    window[handlerName("loadReviewDashboard")] =',
  '    if (isIndex) window.chartTest = {volumeChart, bindVolumeChart, renderBreadth, validChartValue};\n    window[handlerName("loadReviewDashboard")] ='), context);
const {volumeChart, bindVolumeChart, renderBreadth, validChartValue} = context.chartTest;
const volume = {ma20_yi: 21000, ma60_yi: 19000, series: Array.from({length: 20}, (_, index) => ({
  date: `2026-08-${String(index + 1).padStart(2, '0')}`, amount_yi: index === 2 ? null : 18000 + index * 100,
}))};
const original = JSON.stringify(volume);
for (const size of [5, 10, 20]) {
  const html = volumeChart(volume, size);
  assert(html.includes(`data-volume-window="${size}" aria-pressed="true"`));
  assert.equal((html.match(/data-label="交易日"/g) || []).length, size);
  assert(html.includes(`共${size}个交易日`));
  assert(!html.includes('type="range"'));
  assert(html.includes('review-volume-rule is-baseline'));
  assert(html.includes('review-volume-rule is-ma60'));
  assert(html.includes('2万亿'));
  assert(html.includes('20日均 <b>21,000亿'));
  assert(html.includes('均线截至复盘日'));
  assert(html.includes('review-volume-bar'));
  assert(html.includes('y1="240"')); // bar chart starts at zero
  assert(!html.includes('NaN') && !html.includes('Infinity'));
}
assert.equal(JSON.stringify(volume), original); // View-only, never sorts/mutates the snapshot.
const precise = {...volume, series: [{date: '2026-08-01', amount_yi: 18730.67}, {date: '2026-08-02', amount_yi: 19782.17}]};
assert(volumeChart(precise).includes('<strong data-volume-value>19,782.17</strong>'));
assert(volumeChart(precise).includes('data-label="成交额">18,730.67</td>'));
for (const invalid of [null, undefined, '', ' ', false, true, [], {}, NaN, Infinity, -1]) assert.equal(validChartValue(invalid), false);
for (const valid of [0, 21000, '21000.5']) assert.equal(validChartValue(valid), true);
const gap = volumeChart(volume, 20);
assert.equal((gap.match(/class="review-volume-bar"/g) || []).length, 19); // missing observation is not a zero bar
assert(gap.includes('缺 1 日成交额'));
assert(!volumeChart({series: []}).includes('input id="reviewVolumeDay"'));
assert.equal((volumeChart({series: []}).match(/disabled/g) || []).length, 4);
const empty = volumeChart({series: [{date: '<script>', amount_yi: null}]});
assert(empty.includes('有效数据不足') && empty.includes('&lt;script&gt;'));
assert(!empty.includes('<script>'));
const breadth = renderBreadth({up_ratio: .322, up_count: 1787, down_count: 3647, flat_count: 114});
assert(breadth.includes('统计 5,548 家'));
for (const value of ['32.2%', '65.7%', '2.1%', '1,787', '3,647', '114']) assert(breadth.includes(value));
assert(breadth.includes('本快照仅保存涨跌总家数'));
assert(!breadth.includes('review-histogram-bin')); // no fabricated distribution from three totals
const bins = Array.from({length:23},(_,i)=>({key:i<11?'up_'+i:i===11?'flat':'down_'+i,label:'区间'+i,count:i}));
const fine = renderBreadth({up_ratio:.5,up_count:100,down_count:153,flat_count:0,
 distribution:{bins,note:'测试逐档样本'},markets:[{key:'ALL',name:'全市场',up_count:100,down_count:153,flat_count:0,amount_yi:18000,amount_change_yi:1000,expansion_threshold_yi:1000,expanded:true}]});
assert.equal((fine.match(/class="review-histogram-bin /g)||[]).length,23);
assert(fine.includes('区间22：22家'));
assert(fine.includes('data-label="放量判断">达到门槛'));
assert(fine.includes('data-label="成交额">18,000'));
for (const missing of [null, '', ' ', false, -1, 2.5]) {
  const html = renderBreadth({up_count: 10, down_count: missing, flat_count: 0});
  assert(!html.includes('review-breadth-distribution'));
  assert(html.includes('家数未齐'));
}
assert(renderBreadth({up_count: 0, down_count: 0, flat_count: 0}).includes('统计家数为 0'));
assert(renderBreadth({up_count: 0, down_count: 10, flat_count: 0}).includes('100.0%'));

function element(extra = {}) {
  return {listeners: {}, dataset: {}, attributes: {}, style: {}, textContent: '',
    classList: {toggle(name, active) { this[name] = active; }},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    setAttribute(name, value) { this.attributes[name] = value; },
    focus(options) { this.focusOptions = options; }, ...extra};
}
// Minimal DOM adapter for event wiring; real browser checks cover actual layout and native keys.
widget = {
  set innerHTML(html) {
    this.html = html;
    this.buttons = [...html.matchAll(/data-volume-window="(\d+)"/g)].map(match => element({dataset: {volumeWindow: match[1]}}));
    this.points = [...html.matchAll(/data-volume-point="(\d+)"/g)].map(match => element({dataset: {volumePoint: match[1]}}));
    this.children = {
      details: html.includes('<details') ? element({open: false}) : null,
      'input[type=range]': html.includes('id="reviewVolumeDay"') ? element() : null,
      '.review-volume-canvas': element({getBoundingClientRect: () => ({left: 100, width: 1000})}),
      '.review-volume-cursor': element(),
      '[data-volume-date]': element(), '[data-volume-value]': element(), '[data-volume-unit]': element(),
    };
  },
  querySelector(selector) {
    const size = selector.match(/^\[data-volume-window="(\d+)"\]$/);
    return size ? this.buttons.find(button => button.dataset.volumeWindow === size[1]) : this.children[selector];
  },
  querySelectorAll(selector) { return selector === '[data-volume-window]' ? this.buttons : this.points; },
};
widget.innerHTML = volumeChart(volume);
bindVolumeChart(volume);
const canvas = widget.querySelector('.review-volume-canvas');
canvas.listeners.click({clientX:225});
assert.equal(widget.querySelector('[data-volume-date]').textContent, '2026-08-03');
assert.equal(widget.querySelector('[data-volume-value]').textContent, '数据缺失');
assert.equal(widget.querySelector('[data-volume-unit]').textContent, '');
canvas.listeners.pointermove({pointerType:'touch',clientX:0});
assert.equal(widget.querySelector('[data-volume-date]').textContent,'2026-08-03');
canvas.listeners.keydown({key:'Home',preventDefault(){}});
assert.equal(widget.querySelector('[data-volume-date]').textContent,'2026-08-01');
assert.equal(widget.querySelector('[data-volume-value]').textContent,'18,000');
canvas.listeners.keydown({key:'End',preventDefault(){}});
assert.equal(widget.querySelector('[data-volume-date]').textContent,'2026-08-20');
assert.equal(widget.querySelector('.review-volume-cursor').style.left,'97.5%');
canvas.listeners.pointerleave();
assert.equal(widget.querySelector('.review-volume-cursor').hidden,true);
widget.querySelector('details').open = true;
widget.buttons[0].listeners.click();
assert(widget.html.includes('共5个交易日'));
assert.equal(widget.querySelector('details').open, true);
assert.equal(widget.buttons[0].focusOptions.preventScroll, true);
widget.buttons[2].listeners.click();
assert(widget.html.includes('共20个交易日'));
// A sparse 5-day view can return to 20 days instead of losing its range controls.
const sparse = {...volume, series: volume.series.map((day, index) => ({...day, amount_yi: index >= 15 ? null : day.amount_yi}))};
widget.innerHTML = volumeChart(sparse);
bindVolumeChart(sparse);
widget.buttons[0].listeners.click();
assert(widget.html.includes('有效数据不足'));
widget.buttons[2].listeners.click();
assert(widget.html.includes('review-volume-bar'));
console.log('review chart rendering, gaps, distribution, range and input assertions passed');
