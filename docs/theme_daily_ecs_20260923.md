# 题材每日归档：ECS 部署与验收

展示范围更新：按用户要求，“短线题材周期”仅在短线情绪页展示。板块研究、选品研究、涨停聚焦的重复组件及加载调用已移除；下文四页验收为部署时的历史记录。云端采集、统一归档与收盘调度继续沿用。

## 当前结果（2026-09-23）

- ECS 已部署 `shilun-data:theme-daily-20260923`，发布目录 `/opt/shilun-data/releases/20260923-theme-daily-v1`，`/opt/shilun-data/current` 已切换。
- 9月1—23日共17个交易日的题材周期和归因明细已发布到云端 Mongo。验收修订号均为 `84fc284237c8af4b`；以后成功重跑可生成新修订号。
- 9月23日周期质量状态 `ready`，诊断缺项为0。零售合并、近10个交易日涨停成员观察、退出后的独立核心跟踪继续沿用。
- 短线情绪、板块研究、选品研究、涨停聚焦已在实际浏览器逐一核对：选择9月23日时显示同日同版本。
- 单独题材任务退出码0。通过 `cloud_scheduler --run-once` 执行完整收盘流程，83.5秒完成，题材阶段成功。
- 完整流程退出码2、总状态 `degraded`：原有可选数据集 `previous_limit_pool` 在9月23日不可用（`ValueError`）。这项缺口与题材归档状态分别记录，不能把总流程说成全部通过。
- 本次验收为人工触发完整流程，尚未把下一次自然定时触发计入验收。

## 验收入口

[打开9月23日](http://127.0.0.1:8000/ui?theme_cycles=1&date=2026-09-23)，进入 **短线情绪 → 短线题材周期**。

1. 页脚为9月23日，显示归档修订号。当前观察方向为 AI应用、光通信模块、PCB。
2. 展开“已退出的历史周期”，零售主线、粮食等保留退出结论，并继续展示核心、基准及后排的退出后表现。
3. 板块研究、选品研究、涨停聚焦不再展示周期组件；只在短线情绪核对日期和修订号。
4. 未发布日期明确提示尚未生成；不会借用其他日期的结果。计算失败时显示失败阶段和缺项，已发布版本保留。

## 调度与数据流

沿用唯一常驻容器 `shilun-data-data-sync-1` 和原来的7个调度时点；没有新增独立题材计时器。完整收盘同步在日线、核心资格、龙虎榜和同日复盘校验后，进入 `theme_cycles` 阶段。

题材任务依次执行：市场采集、题材成分采集、必需输入校验、周期计算、归因计算、共同发布。市场输入从已验收的9月1日起点连续推进，预热起点保持8月21日。已有历史输入保留，新交易日补齐后重算整个既定窗口，避免改变入选和退出的历史起点。

两个归档使用同一修订号，逐日压缩文件上传后回读校验哈希，最后一次性切换 `theme_archive_control/current`。周期、归因消费者共同读取这个发布指针。中途失败不会发布一半结果，也不会删除旧版。

当前任务状态保存在 `theme_archive_control/job` 和 `sync_state`（`theme_daily/latest`）。状态包含目标日期、阶段、缺项、重试命令；输入检查失败时保留上次成功的检查点。题材任务使用独立 Mongo 租约并定期续期，完整流水线仍保留原有跨主机租约。

Mac 后台现用 `SHILUN_THEME_ARCHIVE_BACKEND=mongo`，读取云端发布；显式指定归档目录的离线研究及测试仍可使用本地文件。云端读取失败不会静默回退到旧的本地归档。

## 运维与重试

仅题材阶段失败时，可在 ECS 使用已部署镜像独立续跑，免去重做所有行情同步：

```sh
cd /opt/shilun-data/current
export SHILUN_ENV_FILE=/opt/shilun-data/shared/data.env
docker compose -f deploy/data-sync.compose.yaml run --rm --no-deps data-sync python -m shilun.jobs.theme_daily_job --date 2026-09-23
```

需要补全上游数据时，使用原有完整流水线：

```sh
docker compose -f deploy/data-sync.compose.yaml run --rm --no-deps data-sync python -m shilun.jobs.cloud_scheduler --run-once --date 2026-09-23
```

运行时仍使用 `shilun-data_sync-runtime`、`shilun-data_sync-logs` 两个原卷，凭据由既有受限环境文件提供。没有新增端口、数据库权限或凭据。

首次部署到全新运行卷时，须放入已验收的 `runtime/theme_daily/seed/{market_inputs,taxonomy_inputs,benchmark_inputs}.json.gz`；不能从最后一天空建历史周期。成功后输入检查点位于 `runtime/theme_daily/inputs.json`，后续自动增量采集。

本次部署源码哈希清单位于发布目录的 `theme-daily-source-manifest.json`。旧发布 `/opt/shilun-data/releases/20260923-strength-review` 和旧镜像 `shilun-data:strength-review-20260922` 保留；需要回滚时，先确认无运行中子任务，再将 current 指向旧发布并用旧 Compose 重建同一个调度服务。云端已发布归档无需删除。

## 验证证据

- 121项相关 Python 测试通过，覆盖同日同版本发布、缺项、失败保留、重试、并发租约、上传中断、内容校验、基线连续性和流水线错误传播。
- Node 周期组件测试通过；失败阶段、缺项和错误文本转义已检查，`app.js` 语法检查通过。
- ECS 独立验收容器 `shilun-theme-acceptance-20260923` 退出0；完整流程容器 `shilun-theme-pipeline-acceptance-20260923` 因上述可选缺口退出2。两者均已结束。
- 最终只见一个数据调度常驻容器，进程为 init 与调度父进程，无遗留验收子任务；原虎扑容器健康。
- Graphify AST、JSON和报告已更新：8219节点、61546边、539社区。图超过 HTML 可视化容量，保留原 HTML。

本次完成采集、归档、分发和调度可靠性验证，不代表题材规则已通过收益回测。
