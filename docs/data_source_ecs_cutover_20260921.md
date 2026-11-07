# 数据源更迭与 ECS 切换验收（2026-09-21）

## 当前状态

数据源更迭、虎扑单实例切换、ECS 数据调度部署已完成。验收使用实际服务器、实际授权数据源和云端 MongoDB；本机后台已能读取新生成的复盘归档。历史“昨日涨停池”仍有一项可选数据缺口，流水线如实记录为 `degraded`。

| 项目 | 当前结果 |
|---|---|
| 虎扑 | ECS 仅一个 `hpcrawler-monitor-1`；Docker 健康，4 个帖子、6 个用户全部抓取成功，待推送为 0 |
| 数据调度 | ECS 仅一个 `shilun-data-data-sync-1`，以 `--data-only` 常驻，重任务使用短生命周期子进程 |
| 本机重复任务 | `cn.crossmelo.hpcrawler.monitor`、`cn.shilun.tushare-daily`、`cn.shilun.minute-bars` 已持久禁用并卸载 |
| 本机保留服务 | 后台、Mongo 隧道、持仓检查、结构样本、盘中监控仍保留；相关服务已加载新代码 |
| 管理通道 | Workbench CLI v1.0.1 已安装；未配置登录档案。本次部署使用已验证的专用 SSH 管理密钥 |

## 数据源与下线范围

- 日线、基本面、资金流、交易日历等继续使用 Tushare。
- 涨停池改为东方财富直连；行情报价使用明确的东方财富适配器。
- 分钟线同步保留股票 KlineShare 主通道，空结果、错误日期或请求失败时回退；通用分钟通道采用 Tushare → 东方财富。
- 指数分钟线：本账户标准 `idx_mins` 实测无权限，私有网关的 `stk_mins` 实测支持指数。因此本机和 ECS 显式设置 `SHILUN_TUSHARE_INDEX_MINUTE_API=stk_mins`；通用默认值仍为 `idx_mins`，不能假定其他账户也支持相同覆盖。
- 移除 AKShare 运行适配器和 `akshare_sync_job`。新构建容器没有安装 AKShare，实际同步和复盘均可运行。
- 下线旧概念快照、北向同步入口；数据同步页调整为“总览、基础行情、分钟行情、涨停池、题材与复盘”。旧 `/api/v1/data/sync-akshare` 返回 410，提示迁移。
- 历史数据库、历史来源标签保留；读取旧涨停池同步状态的兼容逻辑保留。复盘验证及有实际调用的研究、交易功能继续使用。

## 虎扑修复与切换证据

ECS 原本只有一个爬虫容器，重复来自本机同时运行。容器桥接网络解析报 `EAI_AGAIN`，宿主机网络请求正常。部署改为 `network_mode: host`，不新增监听端口。

切换前的独立探针使用不发送消息的通知器，4/4 帖子和 6/6 用户通过。停止两端后迁入本机较新的状态游标（`2026-09-20T16:10:25.100Z`），合并历史语料，保留 2,049 条唯一记录，随后只启动 ECS。容器持续轮询正常、队列为空。本次没有额外发送测试通知。

服务器回退备份：

- `/opt/hpcrawler/compose.pre-20260921.yaml`
- `/opt/hpcrawler/monitor-data/backup-20260921/`

若回退本机，必须先停止 ECS，再将 ECS 最新游标和语料同步回本机，最后启用本机启动项；不要用旧游标直接重启。

## ECS 定时表

全部时间为北京时间（`Asia/Shanghai`）。共 7 个调度项。

| 时间 | 频率 | 内容 |
|---|---|---|
| 05:00 | 每日 | 前一日补偿，解析实际交易日后同步并生成同日复盘 |
| 07:00 | 每日 | 同上，补偿重试 |
| 08:45 | 周一至周五 | 当日涨跌停价格 |
| 15:15 | 周一至周五 | 候选股票及基准指数分钟线 |
| 17:15 | 周一至周五 | 收盘数据同步 → 同日复盘 |
| 19:15 | 周一至周五 | 收盘数据同步 → 同日复盘补偿 |
| 23:20 | 周一至周五 | 整条流水线补偿 |

已删除独立 19:45 复盘任务。调度器串行执行批量任务，子进程最长 45 分钟，过期容错 15 分钟。父进程文件锁防止共享运行目录启动重复调度器；完整日线复盘流水线另有 Mongo 原子租约防止跨主机重叠，租约有效期 2 小时、执行结束释放。其他独立任务没有统一跨主机锁，因此保留本机相同启动项的禁用状态。

## 实际数据验收

使用 2026-09-18（最近已收盘交易日）执行完整流水线，耗时 450.9 秒。

| 检查 | 实测结果 |
|---|---|
| 日线 | 5,560 条：5,553 只股票及 7 项基准 |
| 题材与行业视角 | 874 项，`missing_members=[]` |
| 复盘归档 | 日期 2026-09-18；生成于 2026-09-21 00:41:38 +08:00 |
| 复盘引擎 | `daily_review_v1.8_unified_mood_topics` |
| 核心质量 | 8/8 检查通过，`quality_status=ready` |
| 流水线状态 | `stage=complete`、`status=degraded`、退出码 2；原因见下文 |
| 租约 | 运行结束后无残留 |
| 本机读取 | 后台成功读到本次云端生成的复盘 |

已知缺口：东方财富不能确认所请求历史日期的“昨日涨停池”，记录为 `previous_limit_pool / unavailable / ValueError`。当日涨停池成功获得 78 条；核心质量检查通过。缺失没有被伪装为零或空池，完整流水线因此保留降级状态。

本次题材成员快照日期为 2026-09-21；用它重建 9 月 18 日的复盘属于历史重建，不代表严格使用当时已知成员的回测。

分钟线真实补齐及复跑：

| 标的 | 来源 | 条数 | 最后一根时间 |
|---|---|---|---|
| 000001.SZ | KlineShare | 48 | 2026-09-18 15:00 +08:00 |
| 000001.SH | Tushare `stk_mins` | 49 | 2026-09-18 15:00 |
| 399006.SZ | Tushare `stk_mins` | 49 | 2026-09-18 15:00 |

补齐运行 `missing=2 fetched=2 failed=0`；再次运行 `missing=0 fetched=0 failed=0`，验证同日数据不重复抓取。东方财富在 ECS 上存在间歇连接失败，已验证 Tushare 指数通道能补齐所缺数据。

## 部署与运维

当前代码目录为 `/opt/shilun-data/releases/20260920`，`/opt/shilun-data/current` 指向该目录。环境文件 `/opt/shilun-data/shared/data.env` 权限 0600、父目录 0700，只包含 Mongo 和所需数据源配置，通知关闭。18 个部署文件的 SHA-256 与本机一致。

服务器查看运行状态：

```sh
cd /opt/shilun-data/current
export SHILUN_ENV_FILE=/opt/shilun-data/shared/data.env
docker compose -f deploy/data-sync.compose.yaml ps
docker compose -f deploy/data-sync.compose.yaml logs --tail=100 data-sync
docker ps --filter name=hpcrawler-monitor-1
```

必要时手工补一个日期（会真实写入云端数据库）：

```sh
docker compose -f deploy/data-sync.compose.yaml run --rm data-sync \
  python -m shilun.jobs.cloud_scheduler --run-once --date 2026-09-18
```

退出码：0 成功，2 有可选数据缺口，1 失败，75 已被其他流水线持有租约。先查看日志和 `sync_state`，不要仅看进程是否仍在运行。

ECS 在线安装依赖较慢，本次使用本机下载的 Linux CPython 3.12 wheel 离线构建；wheel 已放在服务器发布目录的 `deploy/wheels/`，不提交 Git。重建方式：

```sh
SHILUN_OFFLINE_WHEELS=1 docker compose -f deploy/data-sync.compose.yaml build
docker compose -f deploy/data-sync.compose.yaml up -d --no-build
```

证据文件：`/opt/shilun-data/shared/acceptance-20260918.log`、`acceptance-20260918.exit`、`build.log`、`shilun-source-manifest.json`。

若将数据调度回退本机，先停止 ECS `data-sync`，确认没有仍执行的手动任务，再启用本机两项启动项。不要同时启动两端的相同定时任务。此操作不需要恢复或删除 Mongo 数据。

## 代码与页面验证

- 相关 Python 回归：107 passed，1 deselected，7 subtests passed。
- 排除的是原有 `test_control_center_page_is_available`，它仍断言此前已移除的 R1 页面标记和旧静态版本；因此不声称全仓测试全部通过。
- 新测试覆盖数据源单位及证券隔离、错误日期拒绝、分钟线回退、流水线租约、同步失败阻止归档、日期一致性、降级退出码、云端任务范围。
- 同步页在 320、375、414、768、1440 像素宽度验证：无水平溢出，提交时禁用按钮，成功及错误状态可恢复，无旧 AKShare 标签及 JavaScript 错误；页面测试拦截写入接口，没有借页面测试触发真实任务。
- JavaScript 语法检查和 `git diff --check` 通过。
- Graphify AST 图及报告已更新：7,001 节点、48,675 边、464 社区；完整 HTML 超过工具 5,000 节点上限，未重新生成 HTML。

这些结果证明部署、手工执行及幂等复跑通过；下一次自然定时触发尚不在本次验收时间范围内。
