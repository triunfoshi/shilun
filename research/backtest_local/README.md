# Backtest Local 目录说明

`research/backtest_local/` 用于承接石论二期的本地研究型回测。

这一层的目标是：

- 在本地完成候选筛选、排序、仓位、调仓、交易成本等完整研究闭环
- 形成收益率、alpha、回撤、Sharpe、分组收益和归因拆解等统一输出

建议后续优先放入以下脚本：

- `candidate_pool_backtest.py`：从 Mongo `candidate_pool_states` 和 `market_snapshot_records` 回测候选池/策略组合
- `backtest_attribution.py`：从持仓明细拆解行业暴露、候选标签贡献和市场状态表现
- 交易成本与滑点模拟
- 调仓频率实验
- 股票池过滤实验
- 组合结果报告输出

原则：

- 本地回测是二期收益证明主战场
- 聚宽脚本只负责轻策略执行验证，不替代本地研究回测
- 三期回测默认 Mongo-first，不读取 Excel/CSV，也不重新调用 Tushare

候选池组合回测示例：

```bash
python -m research.backtest_local.candidate_pool_backtest \
  --start-date 2026-03-01 \
  --end-date 2026-05-16 \
  --pool-status buy_pool \
  --horizon 5 \
  --top-n 10
```

第一版指标包括累计收益、年化收益、胜率、跑赢率、波动率、Sharpe、最大回撤、alpha、beta，以及行业/候选标签/市场状态归因。

真实日频净值回放示例：

```bash
python -m research.backtest_local.candidate_pool_backtest \
  --mode daily_nav \
  --start-date 2026-03-01 \
  --end-date 2026-05-16 \
  --pool-status buy_pool \
  --rebalance daily \
  --top-n 10 \
  --cost-bps 20 \
  --slippage-bps 5
```

`daily_nav` 模式读取 Mongo `market_daily_bars` 生成连续净值曲线，输出日频 alpha、beta、Calmar 和 information ratio。

回测会额外落盘：

- `*_industry_attribution.csv`：按行业统计平均权重、最大权重、收益贡献、胜率和行业 HHI。
- `*_tag_attribution.csv`：按候选标签统计暴露和贡献；标签允许重叠，不能直接相加为组合总收益。
- `*_regime_attribution.csv`：按 `market_trend_score` 分桶统计不同市场状态下的收益、超额收益和换手。
- `*_attribution_summary.md`：归因说明与三张表的 Markdown 汇总。
