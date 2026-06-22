# 三期候选池状态系统

候选池用于把每日榜单升级为可追踪的状态机。它不改变 `SnapshotJob` 当前排序，只从 Mongo `market_snapshot_records` 读取当日快照，并把每只股票归入四类池。

## 四类状态

| 状态 | 含义 |
| --- | --- |
| `candidate_pool` | 基础候选池，进入排名或具备候选标签/策略信号，但尚未达到重点观察或买入门槛 |
| `watch_pool` | 重点观察池，执行层仍可观察，命中候选标签或测试策略，风险未明显失控 |
| `buy_pool` | 买入池，执行动作、仓位、执行分、入场概率和快败风险同时达标 |
| `reject_pool` | 淘汰池，执行层观望、风险过高、快败概率过高或分布压力过高 |

## Mongo 集合

| 集合 | 用途 |
| --- | --- |
| `candidate_pool_states` | 每日每票最终池状态 |
| `candidate_pool_events` | 相对上一次状态的变化事件 |

状态事件包括：

- `entered`：首次进入候选池状态系统
- `promoted`：从低优先级池升级到高优先级池
- `demoted`：从高优先级池降级到低优先级池
- `rejected`：进入淘汰池
- `reentered`：从淘汰池重新进入候选/观察/买入池
- `unchanged`：状态未变化，只写入 `candidate_pool_states`，不写入事件集合

## 运行方式

```bash
shilun-candidate-pools --date 2026-05-16
```

输出：

- Mongo `candidate_pool_states`
- Mongo `candidate_pool_events`
- `outputs/candidate_pool_YYYY-MM-DD_no_st.md`
- `outputs/candidate_pool_YYYY-MM-DD_no_st.csv`

## 边界

- 候选池只读取 Mongo，不调用 Tushare。
- 候选池只做状态沉淀，不改变当前榜单排序。
- 日报推送默认读取 `candidate_pool_states/events` 并追加候选池区块。
- 如果当天没有候选池状态，日报只提示先运行 `shilun-candidate-pools`，不会自动生成候选池或调用 Tushare。

## 推送接入

`shilun-daily-push` 默认会追加以下区块：

- 升池
- 重新进入
- 新进入
- 淘汰
- 降级
- 买入池 TopN
- 观察池 TopN

可通过下面参数关闭：

```bash
shilun-daily-push --date 2026-05-16 --no-candidate-pool
```

## 收益证明

候选池组合收益证明入口为：

```bash
python -m research.backtest_local.candidate_pool_backtest \
  --start-date 2026-03-01 \
  --end-date 2026-05-16 \
  --pool-status buy_pool \
  --horizon 5 \
  --top-n 10
```

第一版使用 Mongo `market_snapshot_records` 里的 `future_return_Nd` 和 `benchmark_future_return_Nd` 做持有期组合收益序列，输出 alpha、beta、Sharpe、最大回撤等指标，并额外拆解行业、候选标签和市场状态归因。

如需真实日频净值曲线，使用：

```bash
python -m research.backtest_local.candidate_pool_backtest \
  --mode daily_nav \
  --start-date 2026-03-01 \
  --end-date 2026-05-16 \
  --pool-status buy_pool \
  --rebalance daily \
  --top-n 10
```

`daily_nav` 模式额外读取 Mongo `market_daily_bars`，输出连续组合净值、基准净值、日频 alpha/beta、Calmar 和 information ratio。

归因输出：

- 行业归因：统计行业平均权重、最大权重、贡献、胜率和组合行业 HHI。
- 候选标签归因：统计不同候选标签下的暴露和收益贡献，适合比较 `rps_breakout`、`turtle_breakout` 等测试标签。
- 市场状态归因：根据 `market_trend_score` 分为 `strong`、`neutral`、`weak`，观察策略在不同环境下的收益和超额。

这一层只读取 Mongo 已沉淀数据，不读取 Excel/CSV，也不重新调用 Tushare。
