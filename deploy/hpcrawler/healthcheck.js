'use strict';

const fs = require('node:fs');
const path = require('node:path');
const config = require('./monitor-config');

// Report stalled polling and overdue deliveries; never print message bodies or credentials.
const maxAgeMs = 15 * 60 * 1000;
const now = Date.now();
const fresh = (value) => {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && now - timestamp >= -60000 && now - timestamp < maxAgeMs;
};

try {
  const dataDir = process.env.HUPU_MONITOR_DATA_DIR || config.dataDir;
  const state = JSON.parse(fs.readFileSync(path.join(dataDir, 'state.json'), 'utf8'));
  if (!fresh(state.lastCycleCompletedAt)) throw new Error('轮询长时间未完成');
  for (const [kind, targets] of [['threads', config.threads], ['users', config.users]]) {
    for (const target of targets) {
      if (!fresh(state[kind]?.[target.id]?.lastPollAt)) throw new Error('部分监控对象长时间未抓取成功');
    }
  }
  if (!Array.isArray(state.pending)) throw new Error('缺少推送队列');
  if (state.pending.some((record) => !fresh(record.capturedAt))) throw new Error('推送队列积压超过15分钟');
  console.log('轮询及推送队列正常');
} catch (error) {
  // Avoid surfacing arbitrary file contents from parse errors.
  console.error(error instanceof SyntaxError ? '监控状态不是有效 JSON' :
    error.code ? '无法读取监控状态' : error.message);
  process.exitCode = 1;
}
