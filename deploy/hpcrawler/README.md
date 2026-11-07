# 虎扑监控服务器部署

状态（2026-10-27）：ECS 已运行且健康；4/4 帖子、14/14 用户抓取通过。最新新增福满天下、橘然橙了，验收与回退记录见下文；此前更新见 [火麒麟Kirin999监控验收](../../docs/hpcrawler_kirin_20261007.md)、[赫萝Horoo监控验收](../../docs/hpcrawler_horoo_20261007.md)及[新增监测账号](../../docs/hpcrawler_users_20260922.md)。本机重复启动项已持久禁用并卸载，游标和历史语料已迁入 ECS。桥接网络 DNS 故障通过 host 网络修复。首次切换证据与回退要求见 [切换验收](../../docs/data_source_ecs_cutover_20260921.md)。以下为重新部署操作参考。

目标服务器为现有 ECS `47.110.33.132`。`vc_astock_ecs_ed25519` 密钥仅用于 MongoDB 隧道；已启用的 `shilun_ecs_management_ed25519` 专用管理密钥可执行部署命令。无需改变 MongoDB、开放新的公网端口或迁移 VC-Astock 后台。

## 部署内容

- 爬虫配置中的 14 个全站账号、4 个帖子、北京时间显示和现有飞书机器人（2026-10-07 实测为 1 个去重后的通知渠道，旧文档记载为 2 个）。
- Node.js 24 容器，使用原 `package-lock.json` 安装依赖，构建时运行原项目测试。
- 默认每轮完成后等待 30 秒；实际通知延迟包括抓取和推送时间。
- 运行数据使用持久目录；通知凭据 `.env.local` 单独只读挂载，不写入镜像。监控名单 `monitor-config.js` 位于镜像内，更新名单后需构建并重建服务。
- 进程异常退出自动重启；Docker 随服务器启动后恢复服务。
- 日志最多保留 3 个 10 MB 文件。
- 健康检查检测轮询停滞、目标抓取停滞和推送积压；Docker 的 unhealthy 状态只作标记，本身不会触发自动重启或发送告警。

重启行为及挂载规则参考 [Docker Compose 官方文档](https://docs.docker.com/reference/compose-file/services/)。

## 生成代码包（本机）

```sh
python3 /Users/shibicheng/shilun_standalone/deploy/hpcrawler/prepare_bundle.py \
  --source /Users/shibicheng/Documents/python/hpcrawler \
  --output /Users/shibicheng/shilun_standalone/runtime/hpcrawler-code-20260908.tar.gz
```

代码包使用明确文件清单，不包含 `.env.local`、去重记录、日志或其他项目文件。上传到服务器的受限目录后解压。以下服务器命令假设解压目录为 `/opt/hpcrawler`，且 Docker Engine 与 Compose 插件已安装。

## 先验证服务器抓取能力

本机服务保持运行。在服务器代码目录执行：

```sh
cd /opt/hpcrawler
docker build -t hpcrawler:local .
docker run --rm --network host --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --tmpfs /tmp:size=32m \
  hpcrawler:local node probe.js
```

验证命令使用临时数据和不发送消息的通知器，不读取飞书配置，不修改正式去重记录。必须确认 4/4 帖子及 14/14 用户抓取成功，再进入切换。

## 切换与验收

1. 服务器抓取验证通过后，停止本机这一项服务，并等待进程完成当前轮询后退出。只停 `cn.crossmelo.hpcrawler.monitor`，不影响其他后台服务。

   ```sh
   launchctl disable gui/$(id -u)/cn.crossmelo.hpcrawler.monitor
   launchctl kill SIGTERM gui/$(id -u)/cn.crossmelo.hpcrawler.monitor
   ```

   确认无 `hpcrawler/monitor.js` 进程，且 `monitor-data/state.json` 的轮询开始时间不晚于完成时间。未完整退出时不要继续复制或启动云端。

2. 从 `/Users/shibicheng/Documents/python/hpcrawler/` 单独传输以下三个文件到服务器的 `/opt/hpcrawler/` 对应位置：`.env.local`、`monitor-data/state.json`、`monitor-data/corpus.jsonl`。通过已有管理员 SSH/SFTP 或 Workbench 的受限文件传输，切勿放进公开链接或 Git。
3. 核对两个数据文件的 SHA-256 与本机停机副本一致，确认 JSON 可读、14 个用户与 4 个帖子的 initialized 状态均保留；保留原 pending 队列，不清空游标或历史语料。
4. 设置容器用户可读写数据、只读凭据，再启动：

   ```sh
   cd /opt/hpcrawler
   chown 1000:1000 .env.local monitor-data monitor-data/state.json monitor-data/corpus.jsonl
   chmod 600 .env.local monitor-data/state.json monitor-data/corpus.jsonl
   chmod 700 monitor-data
   systemctl enable --now docker
   docker compose up -d --no-build monitor
   docker compose ps
   docker compose logs --tail=60 monitor
   ```

5. 验证完整轮询持续推进、4/4 帖子与 14/14 用户成功、待推送队列清空，且健康状态正常。用已有飞书测试入口发送一次测试并检查已配置的渠道均成功，然后重启容器验证持久状态和去重仍可用：

   ```sh
   docker compose exec monitor node monitor.js --test-webhook
   docker compose restart monitor
   docker compose logs --tail=60 monitor
   ```

6. 服务器通过验收后卸载已停的本机启动项：`launchctl bootout gui/$(id -u)/cn.crossmelo.hpcrawler.monitor`。保留本机代码、凭据和数据作为备份，不要同时启用两台机器推送。

已有推送采用失败重试；配置多个机器人时，若一个成功、另一个失败，重试可能让已成功的群收到重复内容。切换时也需注意发送成功但状态尚未落盘的短窗口，不能承诺严格的恰好一次投递。

## 回退

云端启动前失败：保持云端未运行，重新启用并启动本机服务。

云端已经抓取或推送后失败：先 `docker compose stop monitor`，将云端最新 `state.json` 和 `corpus.jsonl` 备份并同步回本机，再恢复本机；不要直接用过期本地游标重启。

本机重新启用：

```sh
launchctl enable gui/$(id -u)/cn.crossmelo.hpcrawler.monitor
```

若启动项仍已加载，使用 `launchctl kickstart gui/$(id -u)/cn.crossmelo.hpcrawler.monitor`；若已卸载，使用 `launchctl bootstrap gui/$(id -u) /Users/shibicheng/Library/LaunchAgents/cn.crossmelo.hpcrawler.monitor.plist`。

## 2026-10-16 新增凤鸣路1000号的一只鹬

通过[作者帖子](https://bbs.hupu.com/642786966.html)及公开回复接口交叉核实，主页为 [192881103854049](https://my.hupu.com/192881103854049)，规范 `puid` 为 `26175760`。按规范 ID 配置全站公开回复监测；读取到的 20 条近期回复均匹配该 ID 和昵称。

基于原运行镜像仅覆盖 `monitor-config.js`，既有 23 项测试全部通过，原 11 位账号和 4 个帖子配置逐项比对不变。候选镜像在 ECS 内验证账号抓取后，停止服务、备份状态和语料，再重建容器；保留旧游标和现有通知配置。本机源码同步名单，配置哈希与线上运行镜像一致，本机服务未启动。

2026-10-16 00:18:08 北京时间验收：容器 `healthy`，全站账号 `12/12`、帖子 `4/4` 抓取成功，待推送为 `0`，所有账号与帖子初始化状态保留。新增账号基线已建立；当前最新可见回复时间为 `2026-10-15T08:43:12.000Z`，早于十分钟补推窗口。

- 当前镜像：`hpcrawler:fengming-20261015T160926Z`，同时标记为 `hpcrawler:local`。
- 当前镜像 ID：`sha256:3ee7f03c57e6ef2230e33d7fb49e9fda82eafae1ff6b3645ccb8133911c07d3d`。
- 前一镜像：`sha256:26da600725a32057557e7e66cf91c9dc4319b18ca7dfcbcdba4287ecdbf3bb76`。
- 备份：`/opt/hpcrawler/backups/add-fengming-20261015T160926Z/`，包含原名单、Compose、停机状态和语料、`deployment.json`。
- 本机源码与运行镜像配置 SHA-256：`b221efdc382585e9be0ffb52d14038c02a9bf8e20d5b151a794048f6c3ec2bc4`。

如需回退名单，恢复上述原配置和前一镜像并重建 `monitor`；保留现行持久数据，勿将旧游标覆盖回正式状态，以免重复通知。

已执行 `graphify update .`：362 个代码文件 AST 提取完成，图 JSON 与报告更新；HTML 导出因 8310 个节点超过工具上限而未更新。

## 2026-10-27 新增福满天下、橘然橙了

两位账号均已按规范 `puid` 加入 ECS 全站公开回复监测：

| 账号 | 监测 ID | 身份核验 |
| --- | --- | --- |
| 福满天下 | `6266709` | 从已监控账号 K1ayThompson 的公开回复引用核对昵称与 `puid`，再通过本人回复接口验证 |
| 橘然橙了 | `27010204` | [作者帖子](https://bbs.hupu.com/641716243.html)及[超短楼回复](https://bbs.hupu.com/636037979-94.html)的作者数据一致；主页为 [36333475362142](https://my.hupu.com/36333475362142)，本人回复接口验证通过 |

本机与 ECS 分别成功读取两位账号各 20 条公开回复，返回的作者 ID 和昵称一致。基于当前运行镜像仅覆盖名单，既有 23 项测试全部通过；逐项比较确认原 12 位账号和 4 个帖子配置不变。正式切换前停止服务并备份状态、语料及配置，原有游标和通知配置保留。

2026-10-27 00:24:39 北京时间正式轮询结果：`successfulUsers=14`、`successfulThreads=4`、`pending=0`。容器健康状态为 `healthy`，全部账号与帖子初始化状态保留，新账号基线已建立。首次读取的最新回复均早于十分钟补推窗口，无历史消息补推，也未发送飞书测试消息。本机服务未启动。

- 当前镜像：`hpcrawler:fuman-juran-20261026T162401Z`，同时标记为 `hpcrawler:local`。
- 当前镜像 ID：`sha256:4758f47664ddb62f2a1e689854c2b1ea2890922f264a83309158a1843631bc75`。
- 前一镜像：`sha256:3ee7f03c57e6ef2230e33d7fb49e9fda82eafae1ff6b3645ccb8133911c07d3d`。
- 备份：`/opt/hpcrawler/backups/add-fuman-juran-20261026T162401Z/`，包含原名单、Compose、停机状态和语料、`deployment.json`、`verification.latest.json`。
- 本机源码与运行镜像名单 SHA-256 均为 `de2de407faf71c947724cb1ced81de62ee722827be8cd04e659baecf7c0ec2be`。

如需回退名单，恢复该备份的原配置和前一镜像并重建 `monitor`；保留现行持久数据，勿覆盖回旧游标。

已执行项目要求的 `graphify update .`：400 个代码文件 AST 提取完成；HTML 导出因图含 8681 个节点超过工具上限而报错。
