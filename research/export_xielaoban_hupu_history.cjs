'use strict';

// Independent read-only export using the existing hpcrawler parser and transport.
// Does not import monitor.js, notification configuration, or write monitor state.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const crawlerRoot = '/Users/shibicheng/Documents/python/hpcrawler';
const { buildUserRepliesUrl, parseUserRepliesPayload } = require(path.join(crawlerRoot, 'hupu-user.js'));
const { fetchHtml } = require(path.join(crawlerRoot, 'utils.js'));
const subject = { id: '100051653', name: 'real蟹老板' };
const out = path.resolve(process.argv.slice(2).find(arg => !arg.startsWith('--')) || 'output/xielaoban-hupu-history-20260922');
const rawDir = path.join(out, 'pages');
const manifestPath = path.join(out, 'crawl-manifest.json');
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const headers = { accept: 'application/json', 'accept-language': 'zh-CN,zh;q=0.9',
  'user-agent': 'hpcrawler/2.0 (+https://github.com/crossmelo/hpcrawler)' };

// Retain speech/context and public author identity; omit network addresses and profiles.
function cleanReply(r) {
  const keys = ['pid', 'tid', 'puid', 'username', 'content', 'title', 'createTime',
    'formatTime', 'deleted', 'replyFinalStatus', 'visibleRange', 'picInfos', 'images'];
  const result = Object.fromEntries(keys.filter(k => r[k] !== undefined).map(k => [k, r[k]]));
  if (r.quoteInfo) result.quoteInfo = cleanReply(r.quoteInfo);
  return result;
}

async function main() {
  fs.mkdirSync(rawDir, { recursive: true });
  let manifest = fs.existsSync(manifestPath) ? JSON.parse(fs.readFileSync(manifestPath, 'utf8')) : {
    subject, startedAt: new Date().toISOString(), pages: [], status: 'running',
    scope: '公开用户回复接口可访问记录；另行核查主帖。删除、隐藏及平台未返回记录不作完整性保证。',
    crawlerSourceHashes: Object.fromEntries(['hupu-user.js', 'utils.js'].map(f => [f, digest(fs.readFileSync(path.join(crawlerRoot, f)))])),
  };
  if (manifest.status === 'complete') throw new Error('Already complete; choose a new output directory for another snapshot.');
  if (manifest.error) {
    manifest.priorAttemptErrors = [...(manifest.priorAttemptErrors || []), manifest.error];
    delete manifest.error;
  }
  let cursor = manifest.pages.at(-1)?.nextCursor;
  const seenCursors = new Set(manifest.pages.map(p => String(p.cursor ?? 'initial')));
  const save = () => fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  try {
    for (let i = manifest.pages.length; i < 2000; i++) {
      const cursorKey = String(cursor ?? 'initial');
      if (seenCursors.has(cursorKey)) throw new Error('Repeated history cursor; cannot claim exhausted.');
      seenCursors.add(cursorKey);
      const url = buildUserRepliesUrl(subject.id, { pageSize: 20, maxTime: cursor });
      const body = await fetchHtml(url, { headers, timeoutMs: 25000, retries: 2 });
      const payload = JSON.parse(body);
      const replies = parseUserRepliesPayload(payload, subject);
      const raw = payload.data.replyWithQuoteDtoList;
      const sanitized = { code: payload.code, msg: payload.msg,
        data: { nextPage: payload.data.nextPage, maxTime: payload.data.maxTime,
          replyWithQuoteDtoList: raw.map(cleanReply) } };
      const file = `${String(i + 1).padStart(4, '0')}.json`;
      fs.writeFileSync(path.join(rawDir, file), JSON.stringify(sanitized));
      const times = replies.map(r => r.createdAt).filter(Boolean).sort();
      const next = payload.data.maxTime;
      manifest.pages.push({ page: i + 1, cursor: cursor ?? null, nextCursor: next,
        nextPage: payload.data.nextPage, rawCount: raw.length, publicAuthorCount: replies.length,
        earliest: times[0] || null, latest: times.at(-1) || null,
        file, rawResponseSha256: digest(body), fetchedAt: new Date().toISOString() });
      manifest.status = 'running';
      save();
      if ((i + 1) % 25 === 0 || i === 0) console.log(JSON.stringify({ pages: i + 1, oldest: times[0] }));
      if (payload.data.nextPage === false) {
        manifest.status = 'complete';
        manifest.stopReason = 'api_history_exhausted';
        break;
      }
      if (payload.data.nextPage !== true || !next || String(next) === cursorKey) {
        throw new Error('Pagination is ambiguous; cannot claim exhausted.');
      }
      cursor = next;
      await new Promise(resolve => setTimeout(resolve, 750));
    }
    if (manifest.status !== 'complete') throw new Error('Safety page bound reached before exhaustion.');
    const byId = new Map();
    let duplicateCount = 0;
    for (const page of manifest.pages) {
      const payload = JSON.parse(fs.readFileSync(path.join(rawDir, page.file), 'utf8'));
      for (const reply of parseUserRepliesPayload(payload, subject)) {
        const key = `${reply.threadId}:${reply.replyId}`;
        if (byId.has(key)) duplicateCount++;
        else byId.set(key, { ...reply, capturedAt: page.fetchedAt, sourcePage: page.page });
      }
    }
    const rows = [...byId.values()].sort((a,b) => (a.createdAt || '').localeCompare(b.createdAt || '') || a.replyId.localeCompare(b.replyId));
    const jsonl = rows.map(r => JSON.stringify(r)).join('\n') + '\n';
    fs.writeFileSync(path.join(out, 'replies.jsonl'), jsonl);
    Object.assign(manifest, { finishedAt: new Date().toISOString(), recordCount: rows.length,
      duplicateCount, earliest: rows[0]?.createdAt, latest: rows.at(-1)?.createdAt,
      recordsSha256: digest(jsonl), missingTimestampCount: rows.filter(r => !r.createdAt).length });
    save();
    console.log(JSON.stringify({ status: manifest.status, count: rows.length, pages: manifest.pages.length,
      earliest: manifest.earliest, latest: manifest.latest, out }));
  } catch (error) {
    manifest.status = 'incomplete';
    manifest.error = error.message;
    save();
    throw error;
  }
}
async function auditMainPosts() {
  const cheerio = require(path.join(crawlerRoot, 'node_modules/cheerio'));
  const replies = fs.readFileSync(path.join(out, 'replies.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
  const checks = [], ownPosts = [];
  for (const tid of new Set(replies.map(r => r.threadId))) {
    const url = `https://bbs.hupu.com/${tid}.html`;
    try {
      const html = await fetchHtml(url, { headers, timeoutMs: 25000, retries: 1 });
      const $ = cheerio.load(html);
      const thread = JSON.parse($('#__NEXT_DATA__').text()).props.pageProps.detail.thread;
      const isSubject = String(thread.author.puid) === subject.id;
      checks.push({ tid, url, title: thread.title, isSubject, status: 'read', htmlSha256: digest(html), capturedAt: new Date().toISOString() });
      if (isSubject) ownPosts.push({ tid, url, title: thread.title, authorId: String(thread.author.puid), author: thread.author.puname,
        createdAt: new Date(thread.createdAt).toISOString(), text: cheerio.load(thread.content).text(),
        images: cheerio.load(thread.content)('img').map((_, e) => e.attribs.src).get() });
    } catch (e) { checks.push({ tid, url, status: 'unavailable', error: e.message }); }
    await new Promise(resolve => setTimeout(resolve, 750));
  }
  const result = { subject, checks, ownPosts, scope: '仅核查回复档案涉及的9个主题主帖。公开个人主页跳转登录，不能据此宣称已枚举该用户全部自建主帖。' };
  fs.writeFileSync(path.join(out, 'main-post-audit.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ checked: checks.length, read: checks.filter(c => c.status === 'read').length, ownPosts: ownPosts.length }));
}
(process.argv.includes('--audit-main-posts') ? auditMainPosts() : main()).catch(error => { console.error(error.message); process.exitCode = 1; });
