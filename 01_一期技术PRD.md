# 石论交易决策系统 - 一期技术 PRD

版本：V1.1

阶段：一期 MVP

目标周期：6-8 周

## 1. 项目定位

石论不是价格预测系统，也不是自动交易系统。

一期目标是构建一个可运行、可复盘、可解释的交易决策引擎，完成：

`行情接入 -> 数据清洗 -> 指标计算 -> 结构识别 -> 策略映射 -> LLM解释 -> 查询输出`

系统输出的核心不是“明天涨到几块”，而是：

- 当前市场状态
- 关键结构区间
- 结构失效条件
- 未持仓/已持仓建议
- 风险提示

## 2. 一期目标

一期要证明的不是收益稳定性，而是以下四点：

- 能稳定读取和管理股票行情数据
- 能以规则方式识别趋势、波段、中枢/均衡区和关键事件
- 能输出结构化决策结果
- 能通过 OpenClaw/LLM 将结构化结果翻译为统一风格的分析文本

## 3. 一期边界

### 纳入一期

- 日线行情为主，周线作为高周期确认
- 本地 CSV 导入或本地数据库读取
- 趋势分类
- 结构识别
- 关键位生成
- 建议模板生成
- JSON 结果输出
- OpenClaw/LLM 文本解释接口
- 每日快照落库
- 单票查询接口

### 不纳入一期

- 自动下单
- 实时逐笔/分钟级结构判断
- 完整缠论原教义复刻
- 新闻公告深度接入
- 完整基本面打分体系
- 大规模组合回测平台
- 多端复杂前端产品化

## 4. 用户与使用场景

### 研究者

- 导入或同步行情后，希望系统自动给出单票结构判断和关键区间

### 持仓者

- 希望知道当前更适合持有、等待回踩、减仓还是退出观察

### 远程查询用户

- 希望通过接口或 Bot 输入股票代码，返回结构化分析结果和自然语言解释

### 策略维护者

- 希望沉淀每日输出快照，用于复盘、误判分析和规则修订

## 5. 系统总体架构

### L1 数据层

- 行情导入
- 数据清洗
- 多周期聚合
- 特征缓存

### L2 指标层

- MA、ATR、涨跌幅、振幅、量比、成交额变化、波动率等

### L3 结构层

- Pivot 检测
- Swing 构建
- Zone 检测
- Breakout / Pullback / Failure 事件识别

### L4 决策层

- 状态分类
- 关键位生成
- 风险标签生成
- 未持仓/已持仓建议映射

### L5 解释层

- 结构化 JSON -> OpenClaw/LLM 输入
- 文本生成
- 查询接口返回

## 6. 一期核心设计原则

- 结构识别必须由规则引擎完成，不交给 LLM
- LLM 只做翻译、解释、组织语言
- 所有输出都必须可追溯到字段和规则
- 所有结果必须每日快照化，支持复盘
- 一期优先“稳定和一致”，而不是“高度智能”

## 7. 功能需求

### 7.1 数据接入

支持以下数据输入方式：

- 本地 CSV 批量导入
- 本地数据库读取
- 后续预留 API/数据源接入能力

#### 最小字段集

- `ticker`
- `date`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `amount`

#### 可选字段

- `turnover`
- `adj_factor`
- `industry`
- `market_cap`

#### 数据校验规则

- 日期升序
- OHLC 不能为空
- `high >= max(open, close, low)`
- `low <= min(open, close, high)`
- 重复日期去重
- 停牌/异常值标记
- 可选前复权处理

### 7.2 多周期处理

一期默认：

- 日线为主分析周期
- 周线为辅助趋势确认周期

处理方式：

- 从日线聚合周线
- 每个分析对象同时保留 `weekly_trend` 与 `daily_state`

### 7.3 指标计算

一期要求实现以下基础指标：

#### 趋势类

- `ma5`
- `ma10`
- `ma20`
- `ma60`
- `ma_slope`

#### 波动类

- `atr14`
- `return_1d`
- `return_5d`
- `volatility_10d`

#### 量能类

- `volume_ma5`
- `volume_ratio`
- `amount_ratio`

#### 相对位置类

- `close_to_ma20`
- `close_to_recent_high`
- `close_to_recent_low`

### 7.4 趋势分类

#### 主标签

- `up`
- `down`
- `range`

#### 子标签

- `strong_up`
- `weak_up`
- `rebound`
- `exhaustion`
- `consolidation`

判定依据：

- 均线多空关系
- 均线斜率
- 收盘价相对中期均线的位置
- 波动率状态
- 周线和日线是否共振

### 7.5 结构识别

一期不完整复刻缠论，采用工程化简化规则。

#### 7.5.1 Pivot 检测

识别 `pivot high / pivot low`

建议规则：

- 某高点是左右 `k` 根内最高
- 某低点是左右 `k` 根内最低
- 与前一个异类拐点距离 >= `a * ATR`
- 至少间隔 `min_bars`

#### 7.5.2 Swing 构建

由交替的高低拐点构成波段

输出字段：

- `direction`
- `start_date`
- `end_date`
- `high`
- `low`
- `amplitude`
- `bars`

#### 7.5.3 Zone 检测

一期“中枢”采用“连续波段重叠区”简化定义

建议规则：

- 至少 3 个连续波段
- 取价格重叠区
- 若上沿大于下沿，则形成有效均衡区

输出字段：

- `zone_upper`
- `zone_lower`
- `zone_width`
- `touch_count`
- `zone_strength`

#### 7.5.4 事件识别

一期必须实现以下结构事件：

- `breakout_up`
- `breakout_down`
- `pullback_confirm`
- `failed_breakout`

事件要有：

- 触发条件
- 有效条件
- 失效条件
- 优先级

### 7.6 关键位生成

一期必须输出：

- `support_main`
- `support_secondary`
- `pressure_main`
- `breakout_level`
- `invalidation_level`

来源：

- 最近有效 zone 上下沿
- 最近 swing high / low
- ATR buffer

### 7.7 风险标签

一期风险标签至少包括：

- `volume_divergence`
- `sector_weakening`
- `weekly_daily_conflict`
- `failed_breakout_risk`
- `back_to_zone_risk`
- `high_extension_risk`

每条建议必须至少附带 1 个风险标签。

### 7.8 策略表达

一期只做“动作建议”，不做买卖指令。

#### 未持仓动作

- `wait`
- `watch`
- `test_position`
- `buy_on_pullback`
- `avoid_chasing`

#### 已持仓动作

- `hold`
- `hold_above_support`
- `trim_on_resistance`
- `exit_on_invalidation`
- `reobserve`

### 7.9 LLM / OpenClaw 解释

LLM 不直接读取 K 线，只读取结构化字段。

最小输入字段：

- `ticker`
- `weekly_trend`
- `daily_state`
- `structure_type`
- `support_main`
- `pressure_main`
- `breakout_level`
- `invalidation_level`
- `volume_state`
- `risk_tags`
- `evidence`

LLM 输出要求：

- 客观
- 克制
- 不承诺收益
- 不虚构关键位
- 必须包含风险提示

## 8. 数据库与存储设计

一期建议采用：

- SQLite 作为本地开发默认数据库
- 结构清晰后可迁移 PostgreSQL

核心表设计详见 `02_数据库建表.sql`。

## 9. 接口设计

### 9.1 数据导入

`POST /api/v1/data/import`

输入：CSV 文件或路径

输出：导入结果、错误数、覆盖数

### 9.2 单票分析

`GET /api/v1/analyze?ticker=600744.SH&date=2026-03-10`

输出：结构化 JSON + 可选解释文本

### 9.3 批量生成快照

`POST /api/v1/snapshot/run`

输入：日期、股票池

输出：任务 ID

### 9.4 查询任务状态

`GET /api/v1/tasks/{task_id}`

输出：进度、日志、结果摘要

### 9.5 查询推荐结果

`GET /api/v1/recommendation?ticker=600744.SH&date=2026-03-10`

## 10. 技术实现建议

- Python 3.11+
- FastAPI
- Pandas / NumPy
- Pydantic
- APScheduler
- SQLAlchemy 或轻量 repository 实现

## 11. 验收标准

### 功能验收

- 能导入指定格式日线数据
- 能生成周线聚合结果
- 能对单票输出趋势标签
- 能识别至少 4 类结构事件
- 能输出关键位和失效位
- 能生成未持仓/已持仓建议
- 能把结果落盘/落库
- 能通过 API 查询结果
- 能调用 LLM 输出解释文本

### 技术验收

- 同一输入数据，多次运行结果一致
- 快照可回溯
- 结构输出有 `evidence` 字段
- API 响应时间在单票查询场景下可接受
- 至少具备一批人工校验样本

## 12. 一期排期

### 第 1 周

- 明确字段标准
- 建库建表
- 数据导入与清洗框架
- 完成日线/周线数据链路

### 第 2 周

- 完成基础指标计算
- 完成特征快照生成
- 建立单票分析调试脚本

### 第 3 周

- 实现 pivot 检测
- 实现 swing 构建
- 初版 zone 检测

### 第 4 周

- 实现 breakout / pullback / failed_breakout
- 实现关键位生成
- 输出 structure snapshot

### 第 5 周

- 实现 risk tagger
- 实现 action mapper
- 输出 recommendation snapshot

### 第 6 周

- 完成 API
- 完成任务与日志
- 接 OpenClaw/LLM bridge
- 实现文本输出守卫

### 第 7 周

- 构建样本集
- 调参
- 误判复盘
- 修正结构规则

### 第 8 周

- 稳定性修复
- 文档补齐
- 一期验收演示

## 13. 一期交付物

- 可运行的本地分析服务
- 数据导入与清洗模块
- 指标计算模块
- 结构识别模块
- 策略映射模块
- JSON 输出 schema
- OpenClaw/LLM 输入桥接
- 单票查询 API
- 每日快照落库能力
- 一批复盘样本
- 一份一期验收报告
