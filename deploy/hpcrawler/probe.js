'use strict';

const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createService } = require('./monitor');
const config = require('./monitor-config');

// Use disposable state and an in-memory notifier. This command never sends to Feishu.
async function main() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'hpcrawler-probe-'));
  try {
    process.env.HUPU_MONITOR_DATA_DIR = directory;
    const service = await createService({ notifier: { async send() {} } });
    await service.pollOnce();
    const state = service.stateStore.state;
    console.log(`抓取验证：帖子 ${state.successfulThreads}/${config.threads.length}，用户 ${state.successfulUsers}/${config.users.length}；未发送消息`);
    if (state.successfulThreads !== config.threads.length || state.successfulUsers !== config.users.length) {
      process.exitCode = 1;
    }
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
}

main().catch(() => {
  console.error('抓取验证未完成');
  process.exitCode = 1;
});
