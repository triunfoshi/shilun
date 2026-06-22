# Mongo-first 数据链路

三期数据链路统一为：

```text
TushareSyncJob
-> Mongo 原始数据集合
-> SnapshotJob / Strategy
-> Mongo 研究结果集合
-> CandidatePool / DailyPush / Validation / Backtest / API / Telegram
```

## 同步层

`TushareSyncJob` 是唯一默认允许触达 Tushare 的每日同步入口。

它只做四件事：

- 同步 `stock_basic`
- 同步 `trade_calendar`
- 同步 `market_daily_bars`
- 同步 `daily_basic`

它不做：

- 不运行 `ShilunPipeline`
- 不生成策略排名
- 不写 `market_snapshot_records`
- 不发送推送

## 原始数据集合

| 集合 | 唯一键 | 用途 |
| --- | --- | --- |
| `market_daily_bars` | `ticker + date` | 日线行情、基准行情 |
| `stock_basic` | `ts_code` | 股票名称、行业、市场板块 |
| `trade_calendar` | `exchange + cal_date` | 交易日历 |
| `daily_basic` | `ts_code + trade_date` | 每日基础指标 |

## 策略层

`SnapshotJob` 默认使用 `MongoMarketDataProvider` 读取 Mongo 原始数据，不再默认调用 Tushare。

只有显式开启 `allow_tushare_fallback=True` 或 CLI 参数 `--allow-tushare-fallback` 时，才允许在 Mongo 缺失时临时回退 Tushare。

## 下游层

`DailyPushJob` 默认从 `market_snapshot_records` 读取推送数据，不再默认运行 `SnapshotJob`。

`/api/v1/analyze` 和 `/api/v1/telegram/analyze` 默认通过 `MongoFirstAnalysisService` 读取 Mongo 原始行情，再调用 `ShilunPipeline.run_with_bars`。

`research/validation/strategy_validation.py` 默认从 `market_snapshot_records` 读取策略信号和未来收益标签，用于按策略版本做收益验证。

`CandidatePoolJob` 默认从 `market_snapshot_records` 读取当日快照，写入 `candidate_pool_states` 和 `candidate_pool_events`，用于候选池状态迁移。

`DailyPushJob` 默认尝试读取 `candidate_pool_states` 和 `candidate_pool_events` 生成候选池日报区块；缺失时只提示，不自动补跑。

`research/backtest_local/candidate_pool_backtest.py` 默认读取 `candidate_pool_states` 和 `market_snapshot_records`，生成候选池/策略组合收益、alpha、beta 和回撤报告；`--mode daily_nav` 时会额外读取 `market_daily_bars` 生成连续日频净值曲线。

收益验证、候选池状态、本地回测、API 单票分析也应优先读取：

- `market_snapshot_records`
- `analysis_snapshots`
- `candidate_pool_states`
- `candidate_pool_events`
- `market_daily_bars`
- `stock_basic`
- `daily_basic`
- 后续候选池状态集合

CSV、Markdown、pickle 的定位：

- CSV/Markdown 只作为人工查看导出物
- pickle 只作为本地临时缓存，后续应逐步弱化
- 下游自动化任务不应默认依赖 CSV/Markdown/pickle

## 显式 fallback

允许 fallback 的位置必须暴露显式参数：

- `SnapshotJobRequest.allow_tushare_fallback`
- `DailyPushRequest.allow_snapshot_fallback`
- `/api/v1/analyze?allow_tushare_fallback=true`
- `/api/v1/telegram/analyze?allow_tushare_fallback=true`

默认路径不应隐式触发 Tushare。
