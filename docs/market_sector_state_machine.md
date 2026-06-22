# PART2 板块动向状态机

本模块实现 `GET /api/v1/market/sectors`，引擎版本为 `market_sector_v1`。

## 数据口径

第一版使用 `Tushare stock_basic.industry` 聚合个股日线，是行业代理板块，不是申万行业指数，也不是同花顺概念板块。

已实现数据：

- `daily_bars`：板块收益、成交额、上涨占比、涨停代理、趋势和分歧/修复代理。
- `stock_basic.industry`：行业归属，作为板块划分代理。
- `daily_basic`：流通市值、换手率，用于中军代理评分。
- `moneyflow`：Tushare 官方资金流向，用于大单+特大单净流入、净流入占比、净流入扩散率。

待接入数据：

- `minute_bars`：启动时间、盘中分歧、率先修复。
- `stk_limit/orderbook`：封单、炸板、回封质量。
- `index_classify/index_member_all`：申万或主题指数成分。

## moneyflow 接入口径

Tushare 官方提供 `moneyflow` 接口，用于获取沪深 A 股资金流向数据，单次最大提取 6000 行。官方字段包含小单、中单、大单、特大单主动买入/卖出金额，以及 `net_mf_amount`。

当前实现已在 `TushareDailyClient`、Mongo `moneyflow` 集合、`TushareSyncJob`、`evaluate_sector_trends` 和 UI 中接入。统一字段如下：

```plain text
large_net_amount = buy_lg_amount + buy_elg_amount - sell_lg_amount - sell_elg_amount
large_net_ratio = large_net_amount / (daily.amount / 10)
sector_large_net_amount = sum(large_net_amount)
sector_large_net_ratio = sector_large_net_amount / (sector_amount / 10)
positive_moneyflow_count = count(large_net_amount > 0)
positive_moneyflow_ratio = positive_moneyflow_count / sector_stock_count
moneyflow_persistence_3d = count(sector_large_net_amount > 0 over last 3 trade days)
```

其中 `large_net_amount` 作为主力净流入代理，`net_mf_amount` 作为全单净流入参考。Tushare 日线 `amount` 单位是千元，moneyflow 金额单位按万元口径处理，所以净流入占比使用 `amount / 10` 做单位转换。只有满足 `sector_large_net_amount > 0` 且 `moneyflow_persistence_3d >= 2`，才允许在解释里写“主力资金持续净流入”；否则只能写“成交活跃”“主力净流入初现”“主力净流出”或“资金中性”。

同步策略：

- `latest`、`incremental`、`history_year` 都会尝试同步 `moneyflow`。
- 如果日线已存在但 `moneyflow` 缺失，不能直接跳过；同步任务会补 `moneyflow`。
- `moneyflow` 权限或网关失败不阻断日线同步，但会记录 `moneyflow_count=0` 和失败项，页面继续显示 `moneyflow_data_pending`。

Tushare 官方也提供 `limit_list_d`，可补充涨跌停、炸板、首次封板时间、最后封板时间、开板次数、封单金额等字段，用于替代当前日线代理的封板质量。

## 核心指标

```plain text
sector_amount = sum(stock.amount)
market_share = sector_amount / market_amount
amount_ratio_5 = sector_amount / MA5(sector_amount)
return_5d = product(1 + sector_return_1d) - 1
outperform_days_5 = count(sector_return_1d > benchmark_return_1d over last 5 days)
```

成交额只代表活跃度、容量和博弈强度，不代表净流入。

## 龙头评分

```plain text
leader_score = 0.25 * startup_score
             + 0.25 * strength_score
             + 0.20 * drive_score
             + 0.15 * resilience_score
             + 0.15 * board_quality_score
```

第一版中 `startup_score` 和 `board_quality_score` 使用日线代理。接入分钟线和盘口数据后再升级。

## 中军评分

```plain text
zhongjun_score = 0.30 * capacity_score
               + 0.25 * amount_stability_score
               + 0.10 * net_flow_score
               + 0.20 * trend_stability_score
               + 0.15 * turnover_stability_score
```

moneyflow 有数据时纳入 `net_flow_score`；没有 moneyflow 时按中性分处理，页面必须明确标记数据待接入。

## 分歧定义

分歧是涨幅、成交额、广度、核心股反馈之间出现不一致。

```plain text
divergence_score =
  price_volume_divergence
  + breadth_divergence
  + core_divergence
  + intraday_divergence_proxy
  + backrow_divergence
```

其中：

- `price_volume_divergence`：`amount_ratio_5 >= 1.3` 且板块涨幅弱或收盘位置低。
- `breadth_divergence`：板块跑赢但 `up_ratio < 50%`，或上涨占比较前一日下降超过 15pct。
- `core_divergence`：板块上涨但龙头/中军下跌或跌破 MA10。
- `intraday_divergence_proxy`：日线收盘位置 `close_position <= 0.35`。
- `backrow_divergence`：涨停减少且上涨占比下降。

## 修复定义

修复必须有前置分歧。没有前置分歧的上涨叫继续走强，不叫修复。

```plain text
repair_confirmed =
  prior_divergence_in_3d
  and sector_return_1d > benchmark_return_1d
  and up_ratio >= 0.55
  and amount_ratio_5 >= 0.8
  and core_repair_count >= 1
  and no_core_hard_break
```

反包是强修复的一种，突破压力位也是强修复的一种，但普通修复至少要满足“板块强于大盘 + 广度改善 + 核心先修复”。

## 生命周期状态

- `start`：少数核心点火，成交额开始放大。
- `confirm`：近 5 日跑赢大盘，成交额占比上升，核心未破。
- `main_uptrend`：确认之上，龙头/中军保持趋势。
- `accelerate`：成交额显著放大，涨幅扩散，追高风险升高。
- `divergence`：成交额、广度或核心反馈出现不一致。
- `repair`：分歧后板块强于大盘，核心率先修复。
- `decline`：龙头/中军同步破位，板块跑输大盘。
- `watch`：暂未满足主线候选条件。
