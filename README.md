# 石论独立项目

这是从当前大仓库中拆出的石论一期独立项目目录。它保留了原先的 PRD、SQL、规则配置、测试和 `shilun` Python 包，可以直接单独初始化为新仓库。

## 目录

- `shilun/`：主 Python 包，包含数据接入、结构识别、决策规则、LLM bridge、API
- `tests/`：当前的单元测试
- `01_一期技术PRD.md`：一期产品与技术范围
- `05_二期纲领文件.md`：二期方向讨论稿与纲领
- `06_二期技术PRD.md`：二期正式 PRD
- `07_二期实施清单.md`：二期落地实施清单
- `08_三期迭代.md`：三期候选池、规则中心、验证门禁与产品化迭代方案
- `09_四期技术方案.md`：四期 Rust/强类型系统内核、Python 策略层与任务状态化架构方案
- `02_数据库建表.sql`：SQLite 建表脚本
- `03_核心模块接口与伪代码.md`：模块接口说明
- `04_前端方案说明.md`：前端规划说明
- `docs/volume_stage_quant_spec.md`：量价模式、筹码地形与趋势初中末期的量化规范
- `docs/trading_system_detailed_spec.md`：当前交易系统的详细实现方案文档，包含指标公式、权重口径、模型与执行层规则
- `docs/market_snapshot_records_spec.md`：二期研究输入表 `market_snapshot_records` 的字段规范与索引策略
- `docs/factor_pool.md`：三期因子池清单，约束新增字段必须归属到因子组
- `docs/atomic_rules.md`：三期原子规则规范，约束候选标签必须有规则定义和验证路径
- `docs/strategy_layer.md`：三期策略层规范，约束策略 ID、版本、候选标签组合和验证路径
- `docs/mongo_data_flow.md`：三期 Mongo-first 数据链路，明确同步层、策略层、推送层的数据边界
- `docs/candidate_pools.md`：三期候选池状态系统，定义四池、状态迁移和 Mongo 集合
- `research/validation/`：因子有效性与收益证明分析入口
- `research/backtest_local/`：本地研究型回测入口
- `shilun/portfolio/`：二期组合排序与仓位规划骨架
- `scripts/export_joinquant_strategy.py`：聚宽路线 A 单文件策略导出脚本

## 独立运行

1. 创建虚拟环境并安装依赖
2. 在项目根目录配置环境变量，参考 `.env.example`
3. 启动 API：

```bash
python -m shilun
```

或：

```bash
shilun-api
```

默认服务地址为 `http://127.0.0.1:8000`。

启动后可直接打开控制台页面：

```text
http://127.0.0.1:8000/
```

控制台目前支持单票分析、Telegram 文本预览、每日日报预览、飞书推送和通道状态查看。
数据链路区会先检查 Mongo 是否已有目标日期数据：已有则提示“已更新”，没有则可手动同步 Tushare 最新交易日，或切换到 Mongo 最近历史日期分析。

## 当前能力

- 通过 Tushare 私有网关拉取日线数据并归一化为石论字段
- 对结构/强弱/空间/风控做规则映射
- 生成受约束的解释文本
- 提供最小化分析接口 `/api/v1/analyze`
- 提供单页控制台 `/` 和 `/ui`，用于点击访问单票分析、日报预览和飞书推送
- 提供 PART1 大盘权限接口 `/api/v1/market/permission`，输出 `attack/hold/defense/empty`、五维评分、硬否决项和状态机解释
- 提供 Telegram 友好的接口 `/api/v1/telegram/analyze`
- 提供可直接接 Telegram webhook 的接口 `/api/v1/telegram/webhook`
- 提供面向聚宽回测的路线 A 单文件策略模板和导出脚本，见 [docs/joinquant_backtest.md](/Users/shibicheng/shilun_standalone/docs/joinquant_backtest.md)
- 提供三期候选标签观察字段，首批规则集中在 `shilun/rules/`，当前不改变榜单排序
- 提供三期策略层观察字段，首批策略集中在 `shilun/strategies/`，用 `strategy_id + version` 做收益验证
- `DailyPushJob` 默认从 Mongo `market_snapshot_records` 读取日报数据，不再隐式触发 Tushare 快照生成
- `SnapshotJob` 默认从 Mongo 原始数据集合读取行情；Tushare 只由 `TushareSyncJob` 或显式 fallback 使用
- `/api/v1/analyze` 和 Telegram 单票分析默认从 Mongo 原始数据集合读取行情，不再隐式触发 Tushare
- 提供策略级收益验证入口，默认从 Mongo `market_snapshot_records` 按 `strategy_id + version` 聚合收益表现
- 提供候选池状态生成入口，默认从 Mongo `market_snapshot_records` 生成四池状态和状态变化事件
- 每日日报默认尝试读取候选池状态/事件，形成“候选池状态 + Top 榜单”的摘要文本
- 提供候选池组合回测入口，默认从 Mongo 生成收益、alpha、beta、Sharpe、回撤和归因报告
- 提供 `shilun-daily-push` 日报入口，默认只读 Mongo；真实推送必须配置飞书或 Telegram 通道

同步 Tushare 到 Mongo：

```bash
shilun-sync-tushare --latest-only --skip-if-exists --date 2026-05-19
```

导入最近一年历史行情：

```bash
shilun-sync-tushare --history-year --date 2026-05-19
```

启动本地同步调度器：

```bash
shilun-sync-scheduler
```

验证策略收益：

```bash
python -m research.validation.strategy_validation --start-date 2026-03-01 --end-date 2026-05-16
```

生成候选池状态：

```bash
shilun-candidate-pools --date 2026-05-16
```

回测候选池组合：

```bash
python -m research.backtest_local.candidate_pool_backtest --start-date 2026-03-01 --end-date 2026-05-16 --pool-status buy_pool --horizon 5 --top-n 10
```

真实日频净值回放：

```bash
python -m research.backtest_local.candidate_pool_backtest --mode daily_nav --start-date 2026-03-01 --end-date 2026-05-16 --pool-status buy_pool --rebalance daily --top-n 10
```

回测报告会同时输出行业归因、候选标签归因和市场状态归因，用于判断收益来自行业暴露、测试标签还是市场环境。

## 每日日报与推送

当前 `shilun-daily-push` 的稳定能力是：从 Mongo 读取已生成日榜，组装每日摘要文本。只有配置了飞书 webhook 或 Telegram 主动推送目标后，才会发生真实推送；未配置通道时会直接报错，避免“命令跑完但实际没发出去”的假成功。自动化入口 `shilun-automation-push` 默认也只接受“当天结果”，不会再悄悄回退到旧的本地 CSV。

```bash
shilun-daily-push
```

它的默认策略是：
- 导出完整 `Top500` 到 `outputs/`
- 消息默认包含 `Top20`，可通过 `--message-top-k` 调整
- 如果当天已生成候选池状态，会自动追加新进、升池、淘汰、买入池和观察池区块
- 如果当天未生成候选池状态，只提示先运行 `shilun-candidate-pools`，不会自动补跑候选池或调用 Tushare
- Mongo 没有当天结果时直接报错，避免隐式触发 Tushare
- Mongo 缺数据且本地已有导出时，可复用本地最新一份快照作为应急
- 文本消息默认限制在 `3500` 字以内，避免 Telegram 因超长拒发
- 未配置真实推送通道时，必须使用 `--dry-run` 预览消息，否则命令会失败

本地只检查日报内容：

```bash
shilun-daily-push --date 2026-05-16 --dry-run
```

配置通道后真实推送：

```bash
shilun-daily-push --date 2026-05-16
```

如需临时关闭候选池区块：

```bash
shilun-daily-push --date 2026-05-16 --no-candidate-pool
```

如果确实需要在 Mongo 缺数据时临时生成快照，需要显式允许：

```bash
shilun-daily-push --date 2026-05-16 --allow-snapshot-fallback
```

如果你要给自动化任务跑“当天结果并主动推送飞书 + Telegram”，用这个入口：

```bash
shilun-automation-push --date 2026-05-16
```

说明：
- `shilun-automation-push` 默认顺序是：Mongo 当天结果 -> 当天 snapshot fallback
- 如果当天结果拿不到，它会直接失败，不会再拿历史本地 CSV 冒充今天日报
- 只有你明确接受旧结果时，才加 `--allow-stale-local-fallback`

所需环境变量：

```bash
SHILUN_FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your_webhook
```

说明：
- 当前先按飞书单通道执行；只要配置了 `SHILUN_FEISHU_WEBHOOK_URL`，`shilun-daily-push` 会向飞书发送日报
- `shilun-daily-push` 下 Telegram 日报推送已收紧为显式 opt-in；只有同时配置 `SHILUN_TELEGRAM_BOT_TOKEN` 和 `SHILUN_TELEGRAM_PUSH_CHAT_IDS` 才会发送 Telegram
- `shilun-automation-push` 在未显式配置 `SHILUN_TELEGRAM_PUSH_CHAT_IDS` 时，会尝试从 Bot 最近更新里自动发现 chat id
- 如果你已经有 Mongo 日榜数据，建议补上 `SHILUN_MONGO_URI`
- 只想本地检查不真正发送时，加 `--dry-run`

## Telegram 接口

如果你要从 Telegram Bot 调这个项目，先用这两个接口：

1. 获取 Telegram 友好的文本：

```bash
curl "http://127.0.0.1:8000/api/v1/telegram/analyze?ticker=600132.SH&date=2026-03-25"
```

返回值里会有：
- `text`：适合直接发到 Telegram 的文本
- `result`：完整原始分析结果

2. 直接接 Telegram webhook：

```bash
POST /api/v1/telegram/webhook
```

当前支持的命令格式：

```text
/analyze 600132.SH
/analyze 600132.SH 2026-03-25
```

该 webhook 会返回 Telegram `sendMessage` 兼容 payload，方便你后面直接挂到 Bot 的 webhook 链路上。

如果你要把 Bot 真正接到 Telegram HTTP API，还需要配置这几个环境变量：

```bash
SHILUN_TELEGRAM_BOT_TOKEN=你的bot token
SHILUN_TELEGRAM_WEBHOOK_BASE_URL=https://你的公网HTTPS地址
SHILUN_TELEGRAM_WEBHOOK_SECRET=你自定义的一段随机字符串
SHILUN_TELEGRAM_ALLOWED_CHAT_IDS=你的telegram chat id
```

注意两点：
- Telegram webhook 不能直接指向 `127.0.0.1`，你需要一个公网 `HTTPS` 地址，可以用反向代理、云服务器、`ngrok` 或 `cloudflared tunnel`
- 当前项目现在会自动读取项目根目录 `.env`，不需要你手工 `export`

接入顺序建议是：

1. 把上面几个变量填到 `.env`
2. 启动服务：

```bash
source .venv312/bin/activate
python -m shilun
```

3. 验证 Bot token：

```bash
curl "http://127.0.0.1:8000/api/v1/telegram/get-me"
```

4. 设置 webhook：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/telegram/set-webhook" \
  -H "Content-Type: application/json" \
  -d '{"public_base_url":"https://你的公网HTTPS地址"}'
```

设置成功后，Telegram 会把消息推到：

```text
https://你的公网HTTPS地址/api/v1/telegram/webhook
```

然后你在 Telegram 里给 Bot 发：

```text
/analyze 600132.SH
```

服务端就会主动调用 Telegram `sendMessage` 把分析结果回给你。

## 更简单的开发入口（面向数据科学家）

底层分析仍然由单入口 pipeline 完成，但三期以后推荐从 Mongo-first 服务层进入：

- 文件：`shilun/pipeline.py`
- 主类：`ShilunPipeline`
- 一次分析：`ShilunPipeline().run(ticker="000001.SZ", analysis_date="2026-03-18")`
- API 服务入口：`shilun/services/analysis_service.py`

它会按固定流程执行：
1. 从 Mongo 原始数据集合读取日线数据
2. 计算趋势和波动特征
3. 组装 `snapshot`
4. 执行决策映射
5. 生成解释文本

API 路由 `/api/v1/analyze` 现在调用 `MongoFirstAnalysisService`，默认不再隐式触发 Tushare。

## 面向初学者的改写骨架

如果你准备以“一个人、本地、最小代价”继续改写项目，可以从新的简化骨架开始：

- 说明文档：[docs/beginner_rewrite_plan.md](/Users/shibicheng/shilun_standalone/docs/beginner_rewrite_plan.md)
- 新骨架目录：`shilun/simple/`

设计原则是：

- 减少目录层级
- 把流程编排和业务规则拆开
- 让 `pipeline.py` 只负责串流程
- 让初学者优先读懂入口，再逐步补实现

建议先在 `shilun/simple/pipeline.py` 上开发，再按文档把旧逻辑一点点迁过去。

## 自检

```bash
python -m unittest discover -s tests
```

## 后续建议

- 继续把 `snapshot_job` 产物作为 Mongo 中心化数据底座使用
- 把 `analyze` 路由从示例数据切换到真实数据流
- 单独建立 Git 仓库时，只提交本目录内容即可
