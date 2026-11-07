# 石论飞书 Python SDK 配置

本项目通过官方 `lark-oapi==1.7.5` 接入企业自建应用。SDK 独立于 Web 和调度器启动；配置检查不会发送消息。

## 安装

在项目根目录运行：

```bash
./.venv312/bin/pip install '.[feishu]'
```

当前本机 `.venv312` 已安装 SDK。可以直接使用模块入口，不必重新安装项目：

```bash
./.venv312/bin/python -m shilun.feishu_sdk --check
./.venv312/bin/python -m shilun.feishu_sdk --connect-check --timeout 20
```

`--check` 验证应用凭据及机器人基本信息。`--connect-check` 另做一次长连接握手，随后关闭连接；诊断子进程有硬超时。连接期间可能短暂收到消息事件，因此不要与同一应用的正式消费者同时测试。

## 凭据来源

优先读取环境变量或项目根目录的 `.env`：

```dotenv
SHILUN_FEISHU_APP_ID=
SHILUN_FEISHU_APP_SECRET=
SHILUN_FEISHU_BRAND=feishu
```

App Secret 只能由本机配置或部署环境注入，不写入源码或命令行参数。Linux/ECS 使用这组配置；`lark` 品牌可将 `SHILUN_FEISHU_BRAND` 改为 `lark`。

本机 macOS 未提供 App Secret 时，自动复用已有 `lark-cli` 应用。选择顺序为显式 App ID、CLI 的 `currentApp`、首个应用。SDK 从 CLI 加密文件和 macOS 钥匙串在内存中解密凭据，不导出主密钥，不创建明文 App Secret 文件，也不降级钥匙串。

此兼容适配已按 CLI v1.0.98 的存储格式核对；遇到不支持的格式会明确失败。升级 CLI 后若格式变化，可改用显式环境配置。桌面沙箱若无法访问钥匙串，应允许该检查命令访问钥匙串或在普通终端运行。

## 接收消息事件

在飞书开发者后台确认应用具备机器人能力、相应收发消息权限，并添加 `im.message.receive_v1` 事件。选择“使用长连接接收事件”，发布应用并设置用户可用范围。保存长连接订阅方式时，需要有客户端在线。

本地运行以下命令，可以保持连接并检查消息能否送达：

```bash
./.venv312/bin/python -m shilun.feishu_sdk --listen
```

此入口仅接收消息并打印消息 ID 和类型，不记录消息正文，也不会自动回复。按 `Ctrl+C` 结束。不要与 CLI 或其他 SDK 客户端同时消费同一正式应用：飞书的多长连接按集群模式分摊事件，不会向每个客户端广播。

### 本机持续连接

本机已安装用户级服务 `cn.shilun.feishu-sdk`。配置模板为 `deploy/cn.shilun.feishu-sdk.plist`，已安装到 `~/Library/LaunchAgents/`，登录后启动，进程退出后自动重启。服务使用项目的 `.venv312` 和工作目录，通过 `--auto-reply` 接入 Codex，不包含明文凭据。

正式服务使用 `SHILUN_FEISHU_STORE=mongo`，状态和脱敏错误写入 ECS MongoDB 的 `feishu_codex_logs`；launchd 的标准输出和错误输出均指向 `/dev/null`。只有该集合出现 `status: connected` 才表示长连接握手成功；`authenticated: true` 和 `starting_listener` 只说明认证通过、开始连接。连接断开会记录 `disconnected`，不记录签名连接地址。手动诊断模式仍可向终端输出状态。

飞书开发者后台保存长连接方式时，必须保持此服务在线。仅运行 `--connect-check` 会在握手后断开，不能用来维持后台配置所需的在线状态。已有本机服务时，不要再手动启动第二个监听器。

当前采用“本机运行、ECS 持久化”：飞书监听和 Codex 调用保留在 Mac，日志、消息队列、会话历史和用量保存在 ECS。本次检查中 ECS 无法连接 ChatGPT 入口，因此尚未迁移计算服务；本机休眠或关机期间机器人无法回复。

## Codex 自动回复

正式入口为：

```bash
./.venv312/bin/python -m shilun.feishu_sdk --auto-reply
```

已有后台服务时，不要重复手动启动。`--listen` 仍是仅接收事件的诊断模式；只有 `--auto-reply` 才发送回复。

支持允许用户的私聊文字，以及该用户在群聊中明确 @ 当前机器人的文字问题，兼容纯文本（`text`）和富文本（`post`）。群内未 @、@ 其他人、@ 所有人、仅手打机器人显示名，以及其他用户的消息均忽略。判断使用飞书提供的真实机器人 `open_id` 和提及实体，不按显示名称匹配。富文本中的标题、换行、编号、链接及提及会转换为文字；图片等附件不交给模型，纯图片消息暂不处理。若 CLI 配置包含多个用户，必须显式指定 `SHILUN_FEISHU_ALLOWED_OPEN_IDS`。可选配置如下：

当前正式服务已在 `.env` 及 launchd 配置中明确固定唯一允许用户 `ou_f77ab28802dcb7f20aa9383f8c86fa81`，不依赖 CLI 当前用户的自动选择。接收、处理旧队列和最终发送均检查此白名单；其他用户不入队、不调用模型，也不会收到确认消息。

SDK 启动时取得机器人的真实 `bot_open_id` 并注入接收器；身份未知时拒绝群聊触发。应用已具备 `im:message.group_at_msg:readonly` 群 @ 事件权限。将“诗人”加入目标群后，由允许用户使用飞书的 @ 功能选中它并输入问题。

```dotenv
SHILUN_FEISHU_STORE=mongo
SHILUN_FEISHU_ALLOWED_OPEN_IDS=
SHILUN_CODEX_BIN=/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex
SHILUN_CODEX_TIMEOUT_SECONDS=600
SHILUN_CODEX_MODEL=gpt-6-luna
SHILUN_CODEX_REASONING_EFFORT=low
SHILUN_CODEX_SERVICE_TIER=priority
SHILUN_CODEX_PROXY=http://127.0.0.1:7897
SHILUN_CODEX_REVIEW_API_BASE=http://127.0.0.1:8000
```

Codex 沿用本机已有登录，不复制认证文件或令牌。若需要登录，在普通终端执行以下命令并按提示完成：

```bash
/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex login
```

会话历史按应用、聊天 ID 和用户 ID 隔离，私聊和各个群之间不互相引用。每次调用通过 `--ephemeral` 和 `history.persistence="none"` 启动临时 Codex 会话，将服务器保存的最近 8 轮对话按长度限制加入上下文，不依赖本地 Codex 会话续接，也不创建该次调用的本地会话记录。Codex 登录认证仍保留在本机；此设置仅针对机器人调用，不更改桌面其他聊天的存储。

接收回调将消息写入 ECS 队列，后台任务随后回复“收到，正在处理。”并调用 Codex。消息 ID 去重，生成结果和用量先保存后投递；分片使用稳定 UUID，投递重试不会重复调用模型。远端持久化失败时停止处理并由后台服务重启，不回退到本地文件。

心跳遇到短暂传输故障时，在已确认租约的有效期内每 2 秒重试；租约过期或被其他实例接管后立即停止。监督线程同时检查租约健康，及时取消失去存储能力的模型调用。显式重新生成已发出的失败回答时使用新的投递版本；同一版本的普通投递重试保持 UUID 不变。

服务器复用石论的 MongoDB 连接，按应用 ID 隔离以下集合：

| 集合 | 内容 |
| --- | --- |
| `feishu_codex_inbox` | 待处理消息、回复、投递进度和每次调用用量 |
| `feishu_codex_sessions` | 私聊会话映射和上下文重置位置 |
| `feishu_codex_logs` | 连接状态、错误类别和处理状态 |
| `feishu_codex_leases` | 单实例租约，防止多个工作进程重复处理 |

MongoDB 数据目录为 ECS 的 `/data/mongodb/db`。2026-11-03 检查时，数据盘总容量 80 GB、剩余约 77 GB。集合没有自动清理规则，历史记录会持续保留。

既有本地机器人日志、SQLite 队列和按机器人会话 ID 精确匹配的 Codex 会话记录，由迁移脚本先存档到 ECS 的 `/data/feishu-codex/archive/`，校验 SHA-256、导入 MongoDB，并确认新服务已连接后才清理本地副本；应用密钥、Codex 认证文件和其他聊天不在迁移范围。脚本为 `scripts/migrate_feishu_cloud_storage.py`，需显式传入 `--app-id` 和 `--apply`。

旧会话在 Codex 共享 SQLite 中的标题、摘要等索引，由 `scripts/migrate_feishu_native_index.py` 按精确会话 ID 单独归档和清理。该脚本先验证服务器上的会话归属及存档校验和，再核对本地行未变化后删除；不复制或清空整个共享数据库。

可发送 `/reset` 或“新对话”重置后续调用使用的历史上下文。重置不会删除服务器审计记录，也不会继承桌面上当前聊天的历史。

自动回复以受控配置调用 Codex：只读沙箱、不请求自动提权，不加载用户的 MCP/插件/外部应用工具；仅挂载本项目的专用 `shilun_readonly` 查询服务，并保留机器人的模型、推理强度与服务等级。提示词通过 stdin 传递，CLI 原始诊断不发送到飞书。进程超时或服务停止时会清理该次调用的进程组。

### 全模块只读目录

统一目录覆盖当前 `/api/v1` 全部 158 个操作：开放 97 个 GET 和 7 个纯查询／预览 POST，共 104 项查询，归入 24 个模块。54 个写入口未开放。模块包括复盘、指数量能情绪、首板、打板效应、强势股、事件题材、情绪历史、题材周期、题材归因评估、交易预案、交易日志各子模块、持仓、执行工作台、策略、板块动向、候选池、盘中行情、个股分析和数据状态。

模型通过三个专用工具完成发现与读取：`list_backend_modules` 列举模块，`search_backend_queries` 返回查询 ID 与参数 schema，`query_backend` 执行审定查询。常见复盘与打板统计保留直接附加上下文的快速路径；其他问题由模型使用同一目录选择接口。无需为每种自然语言问法新增路由。既有错误历史不能覆盖本次实际查询结果。

目录只允许审定的精确方法与路径，所有参数按展开的 JSON Schema 校验。六个曾有隐式写入的 GET 强制 `read_only=true`：市场事实补充、大盘权限、龙头榜、盘中监控、股票搜索、个股面板。执行前还核对运行中服务的 OpenAPI 确实支持这些只读参数；旧服务或验证失败时拒绝业务请求。只读模式不建索引、不写缓存、不落盘归档、不同步行情、不记决策或触发推送。缺少已生成数据时说明缺口，不自动生成。

查询结果有 5 MB 读取上限、字段投影和明确的分页／省略标记；一次调用最多 12 个后台请求，最近 4 个响应仅缓存在该临时进程内。模型不会获得后台认证，也不能选择任意网址、HTTP 方法或未审路径。工具再次核对允许用户与本聊天审计身份，摘要审计写 ECS 的 `feishu_codex_logs`，没有本地日志回退。

查询进程使用独立的 `.venv-feishu-query`，不修改后台运行环境的 FastAPI 等依赖。安装方式：

```bash
./.venv312/bin/python -m venv .venv-feishu-query
./.venv-feishu-query/bin/python -m pip install -r deploy/feishu-query-requirements.txt
```

启动命令由机器人固定为该环境的 Python 加 `-m shilun.feishu_readonly_mcp`，工作目录为项目根目录。四项非密钥审计身份通过子进程环境传入，不写入提示词或持久的 Codex 配置。CLI 临时忽略项目配置并显式禁用其他 MCP，避免继承用户或项目中的额外工具；桌面其他聊天配置不变。

当前模型的工具模式为 `code_mode_only`，因此后台查询调用单独启用 `code_mode_host`。该宿主使用纯 V8 执行工具编排，不能导入文件系统、进程或网络模块；本次调用另排除原生 `functions`、`clock` 工具命名空间并关闭图片读取，仅让审定的 MCP 查询进入编排。Shell、插件、浏览器和交易写入等功能保持关闭。宿主不可用时明确按工具故障停止，不能继续生成“后台未接入”的错误回答。

目录与审核证据保存在 `shilun/config/feishu_readonly_catalog.json` 和 `docs/evidence/feishu_readonly_*.json`。增加或修改后台接口后，先更新只读审核，再运行 `./.venv312/bin/python scripts/build_feishu_readonly_catalog.py`。未知接口会令生成失败，避免未经确认自动开放；新模块通过维护这一份目录接入，无需等用户提问才接线。

大请求通过独立输入写线程完整写入 stdin 并关闭管道，输出读取、超时和取消检查独立进行，避免分段等待时遗漏未写完的数据。运行阶段、重试错误计数和固定错误类别写入服务器日志；这些状态不包含 CLI 原始错误文字或消息正文。本机当前调用等待上限为 600 秒。

阶段日志带进程 ID，便于区分进程恢复后的各次调用。传输协议由官方 CLI 选择；内置 `openai` 提供方不接受自定义提供方覆盖，因此不再传递此前无效的 `supports_websockets` 设置。认证文件和服务地址保持原配置。

### 每次调用的模型与用量

机器人当前使用独立配置 `gpt-6-luna`、推理强度 `low`、服务等级 `priority`，优先降低回复延迟。这三个 `SHILUN_CODEX_*` 环境变量只影响机器人调用；不更改桌面 Codex 的全局模型配置。未设置某项时，该项沿用本机 `config.toml`，已有进程环境优先于 `.env`。模型及推理档位需在当前账号的模型目录中可用；本机 CLI 将 Fast 档标识为 `priority`。

本机系统现有代理为 `127.0.0.1:7897`。后台 Codex 未继承该代理时，曾在回答前连续重试连接 4 次；同模型、同推理档位的一次简短连通性对照测试从约 115 秒降至 7.6 秒。`SHILUN_CODEX_PROXY` 只向 Codex 子进程注入代理环境，并保留本地地址的直连；不会改变系统代理、飞书 SDK 或 MongoDB 连接。该配置需要相应的本机代理服务已经运行，不同机器应按实际配置设置或省略。该次实测仅代表简短检测的耗时，长篇复盘仍受输入、输出长度和网络状态影响。

正式回复使用飞书 Card 2.0，正文下方的用量栏为 `notation` 小号灰字（约 12px），采用两行格式。例如，切换前一次真实调用的数据会显示为：

```text
已完成 · 耗时 119.9s · gpt-6.1-sol
↑ 16.2k ↓ 157 · 缓存 8.4k/— (52%) · 上下文占用 未返回
```

`↑` 为本次调用的累计输入 token，`↓` 为累计输出 token；工具往返会多次处理输入，累计量不能当作当前上下文占用。缓存栏按“读取/写入”排列，括号内为缓存读取占输入的比例。当前 CLI 没有返回缓存写入量或最新上下文占用/容量，分别显示“—”和“上下文占用 未返回”；这不表示没有历史，也不根据模型目录容量推算占用比例。真实返回的零仍显示为零。窄屏可以自然换行，不强行缩放整张卡片。

用量取自本次 CLI 的 `turn.completed` 事件，不使用历史累计值。合计、推理输出、推理强度、服务等级及模型来源等完整信息仍保存在服务器的 `usage` 中；模型值优先使用运行结果，缺失时使用本次配置。缓存输入属于输入的一部分，推理输出属于输出的一部分，不重复相加。重置命令、数据缺失提示等未调用模型的回复不附用量。长回答仅在最后一片附用量栏，正文历史不包含卡片或用量文案。

可验证：

- `你好`：验证正常对话。
- `总结20261102复盘`：读取石论对应日期的已生成快照，再生成总结。支持 `2026-11-02`、`2026/11/02` 和 `2026年11月2日`。
- `读取最高板效应最近30天的期望收益均值，比普通涨停高还是低？`：读取“打板效应”页面的 30 个交易日统计。也支持 20 个交易日和明确的截止日期；未给日期时以北京时间当天为请求截止日，并在回答中列明后台实际窗口。
- `新对话`：重置当前私聊的后续上下文。

复盘读取使用 `/api/v1/review/dashboard`；当旧题材字段标记为 `event_theme_views` 时，另外只读同日 `/api/v1/review/shortline/themes`，与页面的题材事件树使用同一来源。两者均不会生成快照或同步行情，返回日期必须与请求日期一致。旧字段的空占位数组及旧题材模块的补数告警不能用于判断新事件题材是否缺失。真正缺数据、日期回退或授权失败时直接说明原因，不让模型补造内容。石论接口的 BasicAuth 仅在服务侧使用，不提供给 Codex。

复盘总结优先遵循用户指定的段落结构；未指定时才使用默认的市场状态、指数量能、情绪题材、机会风险和后续验证条件五部分。

24,000 字符是服务向模型传递直接复盘摘要的预算，与模型真实上下文窗口不同。摘要按关键模块优先保留日期、指数、量能、情绪、事件题材及代表个股，并明确标记省略的明细。极端情况下摘要仍无法装入预算，会回退到已接入的只读工具按字段投影或分页读取；日期、授权和数据缺失错误不会通过这一兜底改写。

策略候选池必须明确选择对应的 `strategy_id`。例如“周K突破套利／周K突破战法”对应 `weekly_breakout`，不能省略参数后读取 API 默认的 `ma5_v0_2`，也不能把一个策略的缺缓存错误解释成另一个策略没有数据。目录向模型提供完整有效标识及中文说明，读取前校验所选策略。400/422 参数错误会返回经过脱敏的业务原因，查询审计仅额外保留经校验的策略标识和日期，不记录任意参数、原始数据或认证信息。

打板效应通过固定的 `GET /api/v1/review/board-effects` 读取，不调用刷新、补数或其他写接口。模型接收聚合统计、组别定义、实际窗口及样本覆盖，不接收原始股票明细或后台认证。期望收益均值直接使用各组 `overall.avg`，是有效“股票 × 反馈日”样本的池化均值，不是每日均值再做平均；数值单位已经是百分数。“普通涨停”默认对照页面的“全体涨停”，该组包含最高板，一字板及回封股也保留。两组差值使用百分点，缺失收益不当作零。

最高板按昨日非 ST 主板完整涨停池的最高连续板数选取，包含并列最高；细分统计要求最终封板时间有效。它不等同于额外排除一字板的“最高换手板”。30 天按本模块页面的 30 个交易日解释，回答会注明这一口径；不支持的窗口会明确说明。历史条件收益不含费用或成交模拟，不代表未来可实现收益。接口未就绪或访问失败时直接回复可核验的原因，避免让模型猜数或泛称整个后台未接入。

## 离线验证

```bash
./.venv312/bin/python -m unittest discover -s tests -p 'test_feishu*.py' -v
```

测试仅使用合成密钥、合成密文及 mock，不读取真实钥匙串或调用飞书。

## 官方资料

- [服务端 SDK 概述](https://open.feishu.cn/document/server-docs/server-side-sdk)
- [Python SDK 开发前准备](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/preparations-before-development)
- [使用长连接接收事件](https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-/request-url-configuration-case)
- [CLI v1.0.98 凭据存储实现](https://github.com/larksuite/cli/blob/v1.0.98/internal/keychain/keychain_darwin.go)
- [Codex 非交互调用](https://learn.chatgpt.com/docs/non-interactive-mode)
