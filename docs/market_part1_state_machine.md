# PART1 大盘权限状态机实现说明

本文档记录“实盘交易执行系统 PART1：大盘环境与交易权限”的产品化实现口径。代码入口为 `shilun/market/part1.py`，API 入口为：

```text
GET /api/v1/market/permission?date=YYYY-MM-DD&benchmark_ticker=000001.SH
```

默认基准为上证指数 `000001.SH`，页面也支持切换沪深300 `000300.SH`、深成指 `399001.SZ`、创业板指 `399006.SZ`。

## 1. 模块定位

PART1 不预测指数单一路径，而是给交易系统发放当天或当前截面的交易权限。

核心输出：

```text
market_permission = attack / hold / defense / empty
```

中文语义：

| 权限 | 中文 | 交易含义 |
| --- | --- | --- |
| `attack` | 进攻 | 大盘、量能、广度、主线共振，可执行计划内买点 |
| `hold` | 持有 | 市场未坏但存在分歧，有先手观察，无先手不追 |
| `defense` | 防守 | 风险升高，降低仓位，只处理持仓，不开新重仓 |
| `empty` | 空仓 | 放量破位、主线退潮或广度极差，禁止开仓 |

## 2. 状态机总结构

状态机采用“硬否决 + 加权确认”。

```text
第一层：硬否决条件
第二层：五维评分
第三层：权限输出
第四层：动作约束
```

总分公式：

```text
total_score = trend_score + volume_score + breadth_score + theme_score - risk_score
```

核心原则：

```text
风险信号优先于机会信号。
大盘权限决定个股买点上限。
从防守升级到进攻必须逐级确认；从进攻降级到防守/空仓可以跳级。
```

## 3. 输入指标与数据来源

| 指标组 | 字段 | 定义 | 数据来源 | 当前状态 |
| --- | --- | --- | --- | --- |
| 趋势结构 | `index_close` | 指数当前价/收盘价 | Mongo `market_daily_bars` 中基准指数 | `implemented` |
| 趋势结构 | `index_ma5/ma10/ma20` | 指数 5/10/20 日均线 | 基准指数日线滚动计算 | `implemented` |
| 趋势结构 | `support_1/support_2/pressure_1` | 自动支撑/压力 | 上一交易日 MA5/MA10/MA20/20日高低点 | `implemented/manual_adjust` |
| 成交额 | `amount` | 指数成交额 | 基准指数日线 | `implemented` |
| 成交额 | `amount_ma5/ma20` | 指数成交额均值 | 滚动计算 | `implemented` |
| 成交额 | `amount_ratio_5` | 今日成交额 / 5 日成交额均值 | 滚动计算 | `implemented` |
| 市场广度 | `up_count/down_count` | 全市场上涨/下跌家数 | 股票日线 `pct_chg` 横截面统计 | `implemented` |
| 市场广度 | `up_count_ma5` | 前 5 个交易日上涨家数均值 | 横截面统计后滚动计算 | `implemented` |
| 市场广度 | `limit_up/down_count` | 涨跌停家数代理 | `pct_chg >= 9.5% / <= -9.5%` | `proxy_only` |
| 主线质量 | `main_theme_status` | 主线强弱代理 | 行业收益、行业上涨占比、成交额占比 | `proxy_only` |
| 权重护盘 | `weight_support_flag` | 权重强但非权重扩散弱 | 行业聚合代理 | `proxy_only` |
| 核心反馈 | `core_stock_feedback` | 龙头/中军是否破位 | 后续接入 A池/Part2 | `manual_only/proxy_pending` |

## 4. 数学表达式

### 4.1 均线

```text
MA_n(t) = mean(close[t-n+1 : t])
```

### 4.2 成交额温度

```text
amount_ratio_5 = amount_t / MA_5(amount)
amount_ratio_20 = amount_t / MA_20(amount)
```

中文释义：

```text
amount_ratio_5 在 1.05-1.30 之间，且指数上涨，视为健康放量。
amount_ratio_5 > 1.50 时，不直接视为强进攻，而是进入加速/过热检查。
```

### 4.3 市场广度

```text
up_count = count(stock_pct_chg > 0)
down_count = count(stock_pct_chg < 0)
up_ratio = up_count / stock_count
up_count_ratio_ma5 = up_count_t / mean(up_count[t-5 : t-1])
```

中文释义：

```text
上涨家数代表赚钱效应是否扩散。
绝对上涨家数和相对最近 5 日均值要一起看，避免只用固定阈值误判。
```

### 4.4 主线代理

当前 v1 使用行业聚合做代理：

```text
industry_return = sum(stock_pct_chg_i * amount_i) / sum(amount_i)
industry_up_ratio = count(stock_pct_chg_i > 0) / industry_stock_count
industry_market_share = sum(industry_amount) / sum(total_market_amount)
```

主线候选排序：

```text
theme_rank_score =
  industry_return * 100
  + industry_up_ratio * 2
  + min(industry_market_share, 0.12) * 10
```

中文释义：

```text
如果一个行业涨幅强于指数、上涨家数扩散、成交额占比足够高，则视为“主线代理成立”。
这不是最终 Part2 主线识别，只是 PART1 判断大盘上涨是否真实可交易的代理层。
```

### 4.5 支撑与压力

v1 自动位：

```text
候选位 = 上一交易日 {MA5, MA10, MA20, 20日低点, 20日高点}
support_1 = 当前价下方最近的候选位
support_2 = 当前价下方第二近的候选位
pressure_1 = 当前价上方最近的候选位
```

中文释义：

```text
自动位只作为初始版本。实盘中可由人工覆盖关键支撑、压力和失效位。
```

## 5. 五维评分规则

### 5.1 trend_score

| 条件 | 分数 | 中文释义 |
| --- | ---: | --- |
| `close > MA5 and MA5 > MA10` | `+2` | 指数站上短线趋势，短线向上 |
| `close > MA10 and MA10 > MA20` | `+2` | 中短趋势未破 |
| `MA5 > MA10 > MA20` | `+2` | 均线多头排列 |
| `close < MA5 and close >= MA10` | `-1` | 跌破短线但结构未坏 |
| `close < MA10` | `-2` | 跌破中短趋势，权限降级检查 |
| `close < MA20` | `-3` | 中期结构转弱 |
| `close < support_1` | `-2` | 跌破第一支撑 |

### 5.2 volume_score

| 条件 | 分数 | 中文释义 |
| --- | ---: | --- |
| `1.05 <= amount_ratio_5 <= 1.30 and pct_chg > 0` | `+2` | 健康放量上涨 |
| `0.85 <= amount_ratio_5 < 1.05 and pct_chg >= -0.5%` | `+1` | 健康震荡 |
| `amount_ratio_5 > 1.50 and pct_chg > 0` | `+1` | 加速上涨，但不作为加仓信号 |
| `amount_ratio_5 > 1.50 and upper_shadow_ratio > 45%` | `-2` | 爆量上影，疑似兑现 |
| `amount_ratio_5 > 1.30 and pct_chg < 0` | `-3` | 放量下跌 |
| `amount_ratio_5 < 0.80 and pct_chg > 0` | `-1` | 缩量上涨，持续性打折 |
| `amount_ratio_5 < 0.80 and pct_chg < 0` | `0` | 缩量回踩，不直接空仓 |

### 5.3 breadth_score

| 条件 | 分数 | 中文释义 |
| --- | ---: | --- |
| `up_count >= 3000 and up_count_ratio_ma5 > 1.10` | `+2` | 赚钱效应明显扩散 |
| `up_count >= 2500 and up_count_ratio_ma5 >= 1.00` | `+1` | 广度较好 |
| `1800 <= up_count < 2500` | `0` | 中性震荡 |
| `up_count < 1800` | `-1` | 赚钱效应不足 |
| `up_count < 1200` | `-2` | 广度极差 |
| `limit_down_count > max(3, limit_down_ma5 * 1.5)` | `-2` | 跌停风险扩散 |

### 5.4 theme_score

| 条件 | 分数 | 中文释义 |
| --- | ---: | --- |
| `industry_return > index_pct_chg + 0.5% and industry_up_ratio >= 60% and market_share >= 3%` | `+2` | 主线代理确认 |
| `industry_return > index_pct_chg and industry_up_ratio >= 50%` | `+1` | 主线候选 |
| `index_pct_chg > 0 and best_industry_up_ratio < 45%` | `-2` | 指数涨但题材扩散弱 |
| `weight_support_flag = true` | `<= -2` | 疑似权重护盘，不判进攻 |

### 5.5 risk_score

| 条件 | 分数 | 中文释义 |
| --- | ---: | --- |
| `close < support_1 and amount_ratio_5 > 1.30` | `+3` | 放量跌破第一支撑 |
| `close < support_2` | `+4` | 跌破第二支撑 |
| `theme_score <= -3` | `+3` | 主线代理退潮 |
| `limit_down_count > max(5, limit_down_ma5 * 1.5)` | `+2` | 跌停家数快速增加 |
| `amount_ratio_5 > 1.50 and upper_shadow_ratio > 45%` | `+2` | 爆量冲高回落 |
| `weight_support_flag = true` | `+2` | 疑似权重护盘 |

## 6. 权限判定

### 6.1 进攻 attack

定义：

```text
指数、量能、广度、主线共振，且没有明显硬风险。
```

数学表达式：

```text
risk_score <= 1
and trend_score >= 3
and breadth_score >= 1
and theme_score >= 1
and total_score >= 5
```

动作：

```text
可执行计划内买点，优先主线龙头/中军；核心仓仍必须由失效位反推。
```

### 6.2 持有 hold

定义：

```text
趋势未破，但量能、广度或主线有分歧。
```

数学表达式：

```text
1 <= total_score <= 4 and risk_score <= 2
```

动作：

```text
有先手观察，无先手不追；只处理持仓或等待支撑低吸确认。
```

### 6.3 防守 defense

定义：

```text
风险升高但未彻底破坏，或出现主线分歧/权重护盘。
```

数学表达式：

```text
-3 <= total_score <= 0 or risk_score >= 3
```

动作：

```text
降低仓位，禁止新增重仓；高位持仓进入利润保护或风险升级。
```

### 6.4 空仓 empty

定义：

```text
放量破位、主线退潮或市场广度极差。
```

硬触发：

```text
close < support_2 and amount_ratio_5 > 1.30
or close < MA20 and amount_ratio_5 > 1.30
or theme_score <= -3
or up_count < 1200 and limit_down_count >= 10
or total_score < -3
```

动作：

```text
禁止开仓，只允许止损、降仓、等待止跌。
```

## 7. 当前完成情况

| 功能 | 状态 | 说明 |
| --- | --- | --- |
| 大盘权限状态机 | 已实现 | `evaluate_market_permission` 输出 `attack/hold/defense/empty` |
| 趋势分 | 已实现 | 指数均线、支撑、压力 |
| 量能分 | 已实现 | 成交额 MA5/MA20、量能温度、爆量上影 |
| 广度分 | 已实现 | 上涨家数、下跌家数、涨跌停代理 |
| 主线分 | 初版代理 | 行业聚合 proxy，后续接 Part2 主线状态机 |
| 风险分 | 已实现 | 放量破位、跌停扩散、权重护盘代理 |
| API 输出 | 已实现 | `/api/v1/market/permission` |
| 控制台页面 | 已实现 | 首页新增“PART1 大盘权限”卡片 |
| Rust 契约 | 已建立 | `docs/contracts/market_part1.schema.json` |
| Rust 内核骨架 | 已建立 | `shilun-core/` 包含同名状态枚举与分类函数 |

## 8. 后续补强项

1. 接入 Part2 主线状态机，替换当前行业聚合 proxy。
2. 接入 A池龙头/中军核心反馈，让 `core_stock_feedback` 从 `manual_only` 升级为 `proxy_only/implemented`。
3. 支撑/压力支持人工覆盖，并记录覆盖来源。
4. 涨跌停统计改用 Tushare `limit_list_d` 或 `stk_limit`，替代 `pct_chg >= 9.5%` 的粗代理。
5. 将 Python 输出和 Rust `shilun-core` 的 JSON schema 做一致性校验。
