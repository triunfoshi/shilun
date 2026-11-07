# 瑞势投研迁移阿里云实施手册

## 1. 目标架构

第一阶段采用最小、可回滚的结构：

```text
公网用户
  ↓ HTTPS 443
Nginx 或阿里云负载均衡
  ↓ 私网 8000
Web 容器（只读复盘快照和业务 API）
  ↓ VPC
云数据库 MongoDB

轻量调度容器
  ↓ 启动短生命周期子任务
行情同步 / 盘中同步 / 日终复盘

大体积导出与历史归档
  ↓
OSS（第二阶段）
```

Web 与重计算任务分开。浏览器只读 `daily_review_snapshots`，全市场扫描由收盘任务完成，避免每次访问都把大量日线载入 Web 进程。

## 2. 当前迁移基线

迁移前本机审计结果：

- Mongo 约 47 个集合；逻辑数据约 1.37 GB；存储约 444 MB；索引约 238 MB；
- `market_daily_bars` 约 159 万条；`moneyflow` 约 142 万条；
- `outputs/` 约 1 GB，主要是历史 pickle 文件；
- 本机长期常驻 6 个 Python 调度进程，是内存占用的重要来源；
- 当前个股日线停留在 2026-08-12，连板增强数据停留在 2026-08-07，期指持仓停留在 2026-08-05；切换生产前必须先补齐数据水位。

初始资源建议基于当前负载而非阿里云强制规格：

- 使用云数据库 MongoDB：ECS 从 4 vCPU / 8 GB 内存起步；
- Mongo 也自建在同一台 ECS：至少 4 vCPU / 16 GB，且只作为过渡方案；
- 系统盘 40–80 GB；应用数据盘按备份和日志需要另配；
- MongoDB、ECS 与 OSS 放在同一区域，MongoDB 与 ECS 放在同一 VPC。

## 3. 上线前安全动作

1. 立即轮换曾写入源码的 Tushare token；当前代码已不再提供默认 token；
2. 为云数据库创建最小权限应用账号，不使用管理员账号运行应用；
3. 生成至少 24 位随机后台密码，设置 `SHILUN_AUTH_REQUIRED=true`；
4. 公网只开放 80/443；22 端口只允许固定管理 IP；27017/3717 不对公网开放；
5. Basic Auth 只允许在 HTTPS 后使用；
6. Telegram、飞书、数据库密码只写入 `deploy/.env` 或阿里云密钥服务，不进入镜像和 Git；
7. 开启云数据库自动备份，并在迁移前额外创建一次可恢复备份。

## 4. 阿里云资源准备

### 4.1 网络与 ECS

1. 选择区域并创建 VPC、交换机；
2. 创建 ECS；
3. 安全组仅放行：
   - `443/tcp`：公网；
   - `80/tcp`：公网，仅用于跳转 HTTPS；
   - `22/tcp`：固定管理 IP；
4. 不开放 MongoDB 端口；
5. 安装 Docker Engine 与 Docker Compose 插件。

### 4.2 云数据库 MongoDB

1. 创建与 ECS 同 VPC 的云数据库 MongoDB；
2. 创建数据库 `shilun` 与应用用户 `shilun_app`；
3. 白名单只允许 ECS 私网地址或所属安全组；
4. 记录私网连接串；
5. 开启自动备份并确认恢复窗口。

### 4.3 域名与 HTTPS

两种方式二选一：

- 直接在 ECS 使用 `edge` 容器：将证书放入 `deploy/certs/fullchain.pem` 和 `deploy/certs/privkey.key`；
- 在阿里云负载均衡终止 HTTPS：负载均衡转发到 ECS 私网端口，并只允许负载均衡访问后端端口。

## 5. Mongo 数据迁移

### 5.1 一次性停机窗口：mongodump / mongorestore

适合当前数据量和允许短暂停写的情况。

本机导出：

```bash
mongodump \
  --uri "$LOCAL_SHILUN_MONGO_URI" \
  --db shilun \
  --archive=shilun.archive.gz \
  --gzip
```

先把归档复制到 ECS 的受限目录，再导入云数据库：

```bash
mongorestore \
  --uri "$ALIYUN_SHILUN_MONGO_URI" \
  --archive=shilun.archive.gz \
  --gzip \
  --stopOnError
```

归档导入完成并核对后，应从 ECS 删除；本地副本保留到云端稳定运行至少 7 天。

### 5.2 低停机迁移：DTS

如果迁移期间还要继续在本机写入，使用阿里云 DTS 做全量加增量同步。先完成全量复制，再观察增量延迟归零，最后短暂停本机调度器、等待最后增量同步完成并切换连接串。

### 5.3 数据核对

至少核对以下集合的文档数量与最新日期：

- `market_daily_bars`；
- `daily_basic`；
- `moneyflow`；
- `daily_price_limits`；
- `limit_up_pool`；
- `cffex_positions_daily`；
- `holdings`、`trade_orders`、`trade_executions`；
- `daily_review_snapshots`。

迁移后运行：

```bash
docker compose exec web python -m shilun.jobs.daily_review_job
```

确认快照日期、四大指数、成交额、涨跌停数、三战法数量和本机同口径结果一致。

## 6. 应用部署

在 ECS 上进入代码目录：

```bash
cp deploy/.env.example deploy/.env
```

填写以下必需项：

- `SHILUN_TUSHARE_TOKEN`；
- `SHILUN_MONGO_URI`；
- `SHILUN_AUTH_USERNAME`；
- `SHILUN_AUTH_PASSWORD`；
- 如需消息推送，再填写飞书与 Telegram 配置。

构建并启动 Web 与统一调度器：

```bash
docker compose -f deploy/compose.yaml up -d --build web scheduler
```

如果证书已经放到 `deploy/certs/`，再启用 HTTPS 边缘代理：

```bash
docker compose -f deploy/compose.yaml --profile edge up -d edge
```

检查：

```bash
docker compose -f deploy/compose.yaml ps
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
```

公网验证必须使用 HTTPS，并确认未登录时返回 401。

## 7. 调度整合与内存控制

云端只保留一个常驻调度器。它本身不导入 pandas、LightGBM 和行情服务，而是在预定时点启动短生命周期子进程：

- 早晨补齐昨日数据；
- 盘前同步涨跌停价；
- 交易时段运行盘中同步；
- 收盘后同步分钟线、持仓检查和结构样本；
- 19:45 生成日终复盘，23:20 失败重试。

子任务退出后操作系统可以回收其内存。为防止同一任务重叠，统一调度器启用单实例与合并执行。

上线后观察 3–5 个交易日，再根据容器峰值调整 ECS。不要一开始把 Web 开成多个 worker；当前单用户后台从单 Web worker 起步更稳妥。

## 8. outputs 与 OSS

第一阶段 `outputs/` 使用 Docker volume，确保程序兼容。第二阶段把历史 pickle、日报附件和长期归档迁入 OSS：

1. 创建私有 Bucket；
2. ECS 使用 RAM 角色访问，不在 `.env` 写永久 AccessKey；
3. 本地只保留最近 7–30 天热文件；
4. 对旧文件设置生命周期规则，自动转低频或归档存储；
5. 数据库中只保存对象键、哈希、生成时间和版本。

MongoDB 备份与 OSS 业务文件是两套不同的恢复链，不能互相替代。

## 9. 切换顺序

1. 本机执行最后一次完整数据同步；
2. 暂停本机所有写入调度器；
3. 完成 Mongo 最终增量或最终归档导入；
4. 云端运行数据核对和日终复盘；
5. 开启云端调度器；
6. 绑定域名并切换 DNS；
7. 观察 48 小时日志、数据水位和消息去重；
8. 再停止本机后台自启动，但保留本机数据库和归档至少 7 天。

不要在云端验证完成前卸载本机 Mongo 或删除本地数据。

## 10. 回滚方案

触发任一条件即回滚：

- 云端日线或资金流日期落后；
- 关键集合数量明显不一致；
- 同一交易日复盘关键指标与本机不一致；
- 调度任务重复运行或重复推送；
- HTTPS、鉴权或数据库私网隔离不符合要求。

回滚步骤：

1. 暂停云端 scheduler；
2. DNS 指回原入口或继续使用本机；
3. 恢复本机写入调度；
4. 导出云端切换后产生的交易、持仓和事件增量；
5. 查明原因后重新执行数据核对，不覆盖本机原始备份。

## 11. 上线验收清单

- [ ] `/healthz` 返回 `ok`；
- [ ] `/readyz` 能连接云 Mongo；
- [ ] 未登录访问业务页面返回 401；
- [ ] HTTPS 证书有效，HTTP 自动跳转 HTTPS；
- [ ] 公网无法连接 MongoDB 端口；
- [ ] 首页默认为“复盘决策”；
- [ ] 快照明确显示日线、连板和期指数据日期；
- [ ] 三战法只包含红K净流入、结构修复520、周K突破；
- [ ] 原有单票、选品池、持仓和数据同步功能可用；
- [ ] 云端 scheduler 只有一个常驻父进程；
- [ ] 云数据库备份和恢复演练通过；
- [ ] 本机回滚入口仍可用。
