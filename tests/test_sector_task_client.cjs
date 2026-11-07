/* Shared task lifecycle checks; no live requests, writes, or browser required. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../shilun/static/app.js'), 'utf8');
const jobId = 'a'.repeat(24);
const pending = status => ({status, job_id: jobId});

function harness() {
    const nodes = {
        globalDate: {value: '2026-11-03'}, globalBenchmark: {value: '000001.SH'},
        'tab-selection': {hidden: false}, 'tab-sectors': {hidden: false},
        sectorPrecomputeButton: {disabled: false},
    };
    const intervals = new Set();
    const rendered = [];
    const phases = [];
    let intervalId = 0;
    const context = {
        console, Date, encodeURIComponent, document: {getElementById: id => nodes[id]},
        setTimeout: fn => {queueMicrotask(fn);},
        setInterval: () => {const id = ++intervalId; intervals.add(id); return id;},
        clearInterval: id => intervals.delete(id),
        escapeHtml: value => String(value ?? ''), formatError: error => error.message,
        selectionPoolRequestVersion: 0, sectorRequestVersion: 0,
        selectedCandidateStrategyId: () => 'ma5_v0_2',
        showHtml: (id, html) => rendered.push({id, html}),
        startSectorPrecomputeProgress: () => {},
        finishSectorPrecomputeProgress: (...args) => phases.push(args),
        isFetchConnectionError: error => error.name === 'TypeError',
    };
    vm.createContext(context);
    for (const [start, end] of [
        ['function sectorTaskStateLabel(', 'async function loadStatus('],
        ['async function precomputeSelectionPool(', 'function renderCandidatesLegacy('],
        ['let precomputeTimer = null;', 'async function runTelegramText('],
    ]) vm.runInContext(script.slice(script.indexOf(start), script.indexOf(end)), context);
    return {context, nodes, intervals, rendered, phases};
}

(async () => {
    // A scheduled retry and lost-worker recovery remain pending; neither resubmits.
    {
        const {context} = harness();
        let posts = 0;
        const urls = [];
        const states = [];
        const statuses = [pending('retry'), {...pending('running'), recovery_pending: true}, pending('complete')];
        context.fetchJson = async (_url, options) => {posts++; assert.equal(options.method, 'POST'); return pending('queued');};
        context.fetchSectorJson = async url => {urls.push(url); return statuses.shift();};
        const result = await context.submitSectorPrecomputeTask({
            date: '2026-11-03', benchmark: '000001.SH', stillCurrent: () => true,
            onProgress: task => states.push(context.sectorTaskStateLabel(task)),
        });
        assert.equal(result.status, 'complete'); assert.equal(posts, 1); assert.equal(urls.length, 3);
        assert(urls.every(url => url.endsWith(jobId)));
        assert.deepEqual(states, ['等待服务器执行', '等待服务器自动重试', '等待服务器恢复任务']);
    }
    // An already published result does not start a polling loop.
    {
        const {context} = harness();
        context.fetchJson = async () => pending('complete');
        context.fetchSectorJson = () => {throw new Error('unexpected status read');};
        const task = await context.submitSectorPrecomputeTask({date: '2026-11-03', benchmark: '000001.SH', stillCurrent: () => true, onProgress: () => {throw new Error('unexpected progress');}});
        assert.equal(task.status, 'complete');
    }
    // Losing the status connection does not imply the server computation failed.
    {
        const {context} = harness();
        let posts = 0;
        context.fetchJson = async () => {posts++; return pending('running');};
        context.fetchSectorJson = async () => {throw new TypeError('Failed to fetch');};
        await assert.rejects(context.submitSectorPrecomputeTask({date: '2026-11-03', benchmark: '000001.SH', stillCurrent: () => true, onProgress: () => {}}), error => error.taskStateUnknown && /后台计算可能仍在运行/.test(error.message));
        assert.equal(posts, 1);
    }
    // A failed submission is never automatically replayed.
    {
        const {context} = harness();
        let posts = 0;
        context.fetchJson = async () => {posts++; throw new TypeError('Failed to fetch');};
        await assert.rejects(context.submitSectorPrecomputeTask({date: '2026-11-03', benchmark: '000001.SH', stillCurrent: () => true, onProgress: () => {}}), /Failed to fetch/);
        assert.equal(posts, 1);
    }
    // Leaving during the wait stops reads, without a cancellation request.
    {
        const {context} = harness();
        let current = true, reads = 0;
        context.fetchJson = async () => pending('queued');
        context.fetchSectorJson = async () => {reads++; return pending('complete');};
        const result = await context.submitSectorPrecomputeTask({date: '2026-11-03', benchmark: '000001.SH', stillCurrent: () => current, onProgress: () => {current = false;}});
        assert.equal(result, null); assert.equal(reads, 0);
    }
    // Both pages ignore an old completion after date, strategy, or page changes.
    for (const page of ['selection', 'sectors']) for (const change of ['date', 'benchmark', 'page', 'strategy']) {
        if (page === 'sectors' && change === 'strategy') continue;
        const {context, nodes, intervals, rendered, phases} = harness();
        let completeRead, readStarted;
        const started = new Promise(resolve => {readStarted = resolve;});
        let loads = 0;
        context.loadSelectionPool = async () => {loads++;};
        context.runSectorTrends = async () => {loads++;};
        context.fetchJson = async () => pending('queued');
        context.fetchSectorJson = async () => {
            readStarted();
            return new Promise(resolve => {completeRead = resolve;});
        };
        const run = page === 'selection' ? context.precomputeSelectionPool() : context.runPrecomputeSectorsFromPage();
        await started;
        const renderCount = rendered.length;
        if (change === 'date') nodes.globalDate.value = '2026-11-02';
        if (change === 'benchmark') nodes.globalBenchmark.value = '000300.SH';
        if (change === 'page') nodes[`tab-${page}`].hidden = true;
        if (change === 'strategy') context.selectedCandidateStrategyId = () => 'another_strategy';
        completeRead(pending('complete'));
        await run;
        assert.equal(loads, 0, `${page}/${change} must not load stale results`);
        assert.equal(rendered.length, renderCount, `${page}/${change} must not replace current content`);
        assert.equal(phases.length, 0); assert.equal(intervals.size, 0);
        assert.equal(nodes.sectorPrecomputeButton.disabled, false);
    }
    // An explicit server failure is distinct from an unknown connection state.
    {
        const {context, rendered, intervals, phases} = harness();
        context.fetchJson = async () => ({...pending('failed'), error: '依赖未就绪'});
        await context.runPrecomputeSectorsFromPage();
        assert.equal(phases[0][2], 'error'); assert.equal(intervals.size, 0);
        assert(rendered.at(-1).html.includes('依赖未就绪'));
        assert(rendered.at(-1).html.includes('runPrecomputeSectorsFromPage(true)'));
    }
    // Only the explicit restart action uses force; reconnects reuse existing work.
    for (const page of ['selection', 'sectors']) {
        const {context, rendered} = harness();
        const submissions = [];
        context.fetchJson = async url => {submissions.push(url); return {...pending('failed'), error: '已耗尽自动重试'};};
        if (page === 'selection') await context.precomputeSelectionPool();
        else await context.runPrecomputeSectorsFromPage();
        assert(!submissions[0].includes('force=true'));
        if (page === 'selection') {
            assert(rendered.at(-1).html.includes('precomputeSelectionPool(true)'));
            await context.precomputeSelectionPool(true);
        } else await context.runPrecomputeSectorsFromPage(true);
        assert(submissions[1].includes('force=true'));
        context.fetchJson = async () => {throw new TypeError('Failed to fetch');};
        if (page === 'selection') {
            await context.precomputeSelectionPool();
            assert(!rendered.at(-1).html.includes('precomputeSelectionPool(true)'));
        } else {
            await context.runPrecomputeSectorsFromPage();
            assert(!rendered.at(-1).html.includes('runPrecomputeSectorsFromPage(true)'));
        }
    }
    console.log('shared sector task retries, recovery, submission safety, stale results, and timer cleanup passed');
})().catch(error => {console.error(error); process.exit(1);});
