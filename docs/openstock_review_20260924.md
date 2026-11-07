# OpenStock代码审阅及VC-stock可借鉴内容

审阅日期：2026-09-24。来源为[Open-Dev-Society/OpenStock](https://github.com/Open-Dev-Society/OpenStock)，固定提交`7570db299aa9846094c90d8ca72db77f56e6a0e0`。已读取源码；未安装执行外部代码，也未用付费API密钥验证线上服务。

## 项目定位与实际能力

它是可自行部署的股票信息工作台，整合行情图表、自选股、新闻、情绪来源与价格提醒。其优势在信息产品组织，而不是A股短线模式评估或交易执行。审阅范围未发现首板／接力／抱团规则、策略回测或券商订单执行链。

| 功能 | 代码中的实际实现 | 边界 |
|---|---|---|
| 市场首页 | TradingView市场概览、股票热力图、报价和新闻组件 | 主要是嵌入展示，不能直接当成本系统可审计行情事实库 |
| 股票详情 | K线、技术分析、公司资料、财务组件，自选按钮 | 依赖第三方数据覆盖和组件 |
| 股票搜索／自选 | 快捷搜索、添加删除、自选排序、相关股票新闻 | 信息观察，不含模式入选与失效理由 |
| 多来源情绪 | 可选Adanos接入Reddit、X、新闻、Polymarket，显示热度、看多比例、方向、来源分歧和覆盖数 | 是舆论／渠道反馈，不等于我们的短线赚钱效应 |
| 价格提醒 | 可设置上／下阈值、有效期；每5分钟检查，触发后更新数据库状态 | 当前触发处理只记录日志，未实际发送价格通知 |
| AI与邮件 | 注册欢迎邮件；每周读取市场新闻、生成摘要并尝试Kit广播 | 周报是一般市场新闻，不是逐个用户的个性化决策计划 |
| 基础工程 | 登录、密码重置、数据库、自选与提醒持久化、Docker、部分单元测试 | 不能仅凭README推断所有依赖服务已部署可用 |

源码：[首页](https://github.com/Open-Dev-Society/OpenStock/blob/7570db299aa9846094c90d8ca72db77f56e6a0e0/app/%28root%29/page.tsx)、[股票详情](https://github.com/Open-Dev-Society/OpenStock/blob/7570db299aa9846094c90d8ca72db77f56e6a0e0/app/%28root%29/stocks/%5Bsymbol%5D/page.tsx)、[自选页](https://github.com/Open-Dev-Society/OpenStock/blob/7570db299aa9846094c90d8ca72db77f56e6a0e0/app/%28root%29/watchlist/page.tsx)、[情绪卡片](https://github.com/Open-Dev-Society/OpenStock/blob/7570db299aa9846094c90d8ca72db77f56e6a0e0/components/stocks/StockSentimentCard.tsx)、[后台任务](https://github.com/Open-Dev-Society/OpenStock/blob/7570db299aa9846094c90d8ca72db77f56e6a0e0/lib/inngest/functions.ts)。

## 架构

技术栈是Next.js 15、React 19、TypeScript、Tailwind／Radix、Better Auth、MongoDB／Mongoose、Inngest。Finnhub用于行情／搜索／新闻，TradingView用于展示，Adanos提供可选情绪数据。AI调用封装支持多提供方。[依赖声明](https://github.com/Open-Dev-Society/OpenStock/blob/7570db299aa9846094c90d8ca72db77f56e6a0e0/package.json)

```text
app页面：组织用户所需信息
  ├─ components：行情、自选、提醒、新闻、情绪卡片
  └─ lib/actions：查行情、查新闻、规范化数据、读写自选
       ├─ 第三方服务
       └─ database/models：用户关联的自选与提醒

lib/inngest：定时事件 → 读取 → 计算／摘要 → 持久化或投递
```

对我们有用的是职责划分和任务组织，不需要为此把现有Python后端改写成Next.js。

## 值得采用的做法

### 1. 数据按变化速度缓存，生成任务分步骤

Finnhub适配层将报价设为不缓存，公司资料缓存24小时，新闻缓存5分钟；独立读取使用并发。Inngest任务使用命名步骤，分开读取、计算和处理结果。[行情适配代码](https://github.com/Open-Dev-Society/OpenStock/blob/7570db299aa9846094c90d8ca72db77f56e6a0e0/lib/actions/finnhub.actions.ts)、[任务步骤](https://github.com/Open-Dev-Society/OpenStock/blob/7570db299aa9846094c90d8ca72db77f56e6a0e0/lib/inngest/functions.ts)

**我们的P0：** 在现有进度提示基础上，把任务阶段和错误持久化；历史核心资格、每个交易日的冻结样本及反馈先形成可复用事实。以数据修订版本和规则版本控制失效，受影响的日期重算；不能只按日期缓存。研究页读取已完成结果，失败只重试必要阶段，最后原子发布新评估版本。预案继续锁定旧引用，不能随最新评估悄悄变化。

这正对准当前一次生成仍需数分钟读取大量历史的瓶颈。该设计尚未实施，不能承诺采用某个框架便能秒级生成。

### 2. 把“来源分歧与覆盖”放到结论旁边

其情绪卡片把可用来源数量与Source Alignment并列展示，并保留各来源明细。适配层单来源失败可以返回空值，避免整个区块必然失败。[情绪接口](https://github.com/Open-Dev-Society/OpenStock/blob/7570db299aa9846094c90d8ca72db77f56e6a0e0/lib/actions/adanos.actions.ts)、[规范化规则](https://github.com/Open-Dev-Society/OpenStock/blob/7570db299aa9846094c90d8ca72db77f56e6a0e0/lib/actions/adanos.helpers.ts)

**我们的P1：** 每个模式和方向直接列支持、反证、未知以及卡在哪一层。例如23日首板反馈正，但大盘量能与指数不通过；抱团收益正，但完整换手基准仅13/14。仍遵守“大盘 > 板块 > 个股”，不用平均分抵消上游否决条件。

不应照搬它对社交看多比例的固定分歧阈值；不同来源也并非天然独立，热度更不能充当首板溢价或真实承接。

### 3. 自选列表升级为带理由的观察对象

OpenStock把自选、相关新闻、提醒放在同一个工作区，减少来回找信息。[自选页实现](https://github.com/Open-Dev-Society/OpenStock/blob/7570db299aa9846094c90d8ca72db77f56e6a0e0/app/%28root%29/watchlist/page.tsx)

**我们的P1：** 用同一个观察对象连接明日预案与今日跟踪：所属方向、适用玩法、角色（核心参照／主选／备选）、入选证据、正常预期、确认条件、失效条件、证据日期与来源。首板套利、接力、抱团分别使用自己的反馈定义，MA5只在执行策略层出现。

主观察最多3个、条件备选最多2个继续保留；不新增完整MA5股票墙。方向暂不适用时，观察卡仍说明升级条件，而不是看起来像已经推荐交易。

### 4. 提醒围绕条件变化，AI负责解释已核实事实

它的提醒已具备有效期、触发状态和定时检查，适合作为观察条件生命周期的参考；但当前只是价格是否越过阈值，还没有完整通知交付。[提醒模型](https://github.com/Open-Dev-Society/OpenStock/blob/7570db299aa9846094c90d8ca72db77f56e6a0e0/database/models/alert.model.ts)

**后续实时阶段：** 检查“昨天首板反馈是否兑现”“板块和参照核心是否仍助攻”“主选弱、备选是否真正满足原条件”。信号附带时间、原始数据和失效原因，避免重复提醒。盘中预估量能与已确认成交额必须分开。此项仅为后续设计，不在本轮接入实时提醒或交易。

AI可以将结构化结果写成简明复盘，引用支持和反证；模式是否适用、缺失如何处理、是否允许执行，仍由可追溯规则负责。

## 不能直接照搬的细节

- `getWatchlistData`将缺失报价和涨跌幅退回0，并把未获取的市盈率设为0。我们应保留null和缺失原因，避免重现“抱团收益都是0”的混淆。
- TradingView技术评级、社交看多比例不是我们三玩法的收益证据，不能直接用来驱动模式选择。
- 价格任务每5分钟全时段执行，逐股票顺序取价；现有代码不足以覆盖A股竞价／交易日历、行情时效、重复触发、真实通知成功等要求。
- 框架和丰富页面不证明策略有效；本项目没有可直接迁入的A股决策闭环。借鉴交互与架构优先于复制交易判断。

建议顺序：**可靠且可复用的评估生成 → 证据／反证／未知说明 → 明日预案与今日跟踪共用观察卡 → 再做实时条件核验。** 这样借鉴OpenStock的信息组织优势，同时保持我们的决策规则、数据边界和历史快照可审计。
