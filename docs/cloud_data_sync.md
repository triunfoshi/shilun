# ECS 每日核心数据同步

更新（2026-09-23）：同一收盘流水线已接入题材周期和归因共同发布，仍为单实例调度。9月23日归档和四页读取已验收；完整流程仍因原有可选“昨日涨停池”缺口显示降级，题材阶段为就绪。当前发布、独立重试和验收记录见 [题材每日归档部署](theme_daily_ecs_20260923.md)。

状态（2026-09-21）：ECS 已启用单实例数据调度，并完成实际数据验收；本机重复日线和分钟线任务已停用。沿用 TushareSyncJob 与 DailyReviewService，由完整流水线串联同步与同日复盘。任务只处理行情和复盘，不启动交易监控或消息发送。详见 [部署与验收记录](data_source_ecs_cutover_20260921.md)，包括当前历史数据缺口。

在服务器管理员会话内，将当前项目部署到服务器目录；用仅本人可读的环境文件配置云端本机 Mongo（通常端口27017，使用既有应用账号）与已授权数据源。Mac的27018是隧道入口，不能原样复制到ECS。不得把凭据写进Compose或Git。

```sh
cd /opt/shilun-data/current
export SHILUN_ENV_FILE=/opt/shilun-data/shared/data.env
docker compose -f deploy/data-sync.compose.yaml config --quiet
SHILUN_OFFLINE_WHEELS=1 docker compose -f deploy/data-sync.compose.yaml build
```

服务器发布目录已包含离线 wheel；新服务器没有这些文件时，使用普通在线构建。需要手工补数据时，确认目标日期并执行同一容器的完整流水线：

```sh
docker compose -f deploy/data-sync.compose.yaml run --rm data-sync python -m shilun.jobs.cloud_scheduler --run-once --date 2026-09-18
```

核对退出码、目标交易日日线/基本面/资金流/涨跌停价水位、复盘版本与样本覆盖。同步失败不能当作当天零行情。退出码 2 表示可选数据缺口，详细状态在 `sync_state` 和日志中。常驻调度管理命令：

```sh
docker compose -f deploy/data-sync.compose.yaml up -d data-sync
docker compose -f deploy/data-sync.compose.yaml logs --tail=100 data-sync
```

时间为 Asia/Shanghai。每日 05:00/07:00 补前日；工作日 08:45 同步当日涨跌停价，15:15 同步候选及基准分钟线，17:15/19:15 同步收盘数据并生成同日复盘，23:20 重试完整流水线。已移除独立 19:45 复盘任务。单工作线程防止本服务里的批量写入并发，任务过期按 15 分钟容错处理；日志标记超时与非零退出码。调度父进程使用文件锁，完整日线复盘流水线使用 Mongo 租约防止跨主机重叠；其他独立任务没有统一跨主机锁，应保持 Mac 相同启动项停用。

云端单次流水线已实测完成，下一次自然定时触发尚不在本次验收范围内。后续可通过调度日志及数据水位检查。历史存储迁移记录如下。

## 历史记录（当时状态）


2026-09-15：用户确认云端备份正常并要求清理本地库。已停止 `homebrew.mxcl.mongodb-community` 并删除 `/opt/homebrew/var/mongodb`，释放约1.14 GB。操作前后均确认应用经127.0.0.1:27018隧道访问云端shilun数据库；没有删除云端库或停止隧道。Mac定时任务仍保留，迁往ECS需等待管理连接启用及云端调度验收。

管理连接脚本为 [setup-ecs-management.sh](../deploy/setup-ecs-management.sh)，上传至服务器 `/root` 后执行 `bash /root/setup-ecs-management.sh`。脚本只添加专用管理公钥，保留原授权和隧道限制，不重启SSH或开放Mongo公网端口。专用私钥仅在Mac的 `~/.ssh/shilun_ecs_management_ed25519`，勿上传。执行后在Mac运行 `ssh -i ~/.ssh/shilun_ecs_management_ed25519 -o IdentitiesOnly=yes root@47.110.33.132`。

2026-09-15 管理连接验证：用户执行脚本后的 `Installed management public key` 为成功提示；已使用专用管理密钥只读登录，用户为root，主机名与用户提供的ECS一致。管理连接已启用，云端定时同步仍需另行部署和验收。
