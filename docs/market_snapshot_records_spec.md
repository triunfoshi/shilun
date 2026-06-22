# market_snapshot_records 字段规范

本文档定义 `market_snapshot_records` 这张 Mongo 集合在二期中的角色、字段规范与索引策略。

目标只有一个：

- 让它成为本地回测、因子有效性分析、收益证明和每日复盘的统一输入层
- 让策略验证、日报推送等下游动作优先读 Mongo，避免重复调用 Tushare

## 1. 定位

`market_snapshot_records` 不是展示表，也不是临时缓存。

它是“按天、按股票、按排名”固化后的研究输入表，适合直接做：

- 当日横截面因子排序
- 分组收益与分层收益
- TopN / TopK 组合回测
- 行业中性筛选
- 单票时序复盘
- 样本内外稳定性分析
- 日报/推送消息的数据源

一条记录代表：

- 某只股票
- 在某个分析日
- 按当前石论快照排序体系生成的一份扁平化研究记录

## 2. 主键

逻辑唯一键：

- `analysis_date`
- `ticker`
- `exclude_st`

解释：

- `analysis_date`：该条记录对应的分析日期，格式 `YYYY-MM-DD`
- `ticker`：股票代码，例如 `000001.SZ`
- `exclude_st`：是否使用“过滤 ST/*ST”口径跑出来的结果

这三个字段组成唯一索引，保证同一口径不会重复写入。

## 3. 字段分组

### 3.1 主维度字段

- `record_version`
  - 当前固定为 `v1`
- `analysis_date`
  - 研究截面日期
- `ticker`
  - 股票代码
- `name`
  - 股票名称
- `industry`
  - 行业
- `market`
  - 市场板块
- `exclude_st`
  - 是否过滤 ST
- `rank`
  - 当日全市场排序名次，`1` 代表最优

### 3.2 价格与仓位字段

- `close`
  - 分析日收盘价
- `target_position_pct`
  - 石论执行层建议仓位百分比

### 3.3 决策标签字段

- `conclusion_label`
  - 最终结论标签
- `action_label`
  - 动作标签
- `entry_style`
  - 入场风格
- `opportunity_type`
  - 机会类型
- `trigger_state`
  - 触发状态
- `trend_stage`
  - 趋势阶段
- `entry_zone`
  - 入场分区

### 3.4 概率与收益字段

- `entry_probability`
  - 入场概率
- `p_acceptance_1d`
  - 次日承接概率
- `p_fail_fast_3d`
  - 快速失败概率
- `anti_fail_fast`
  - `1 - p_fail_fast_3d`
- `p_continue_10d`
  - 10 日延续概率
- `p_breakout_success`
  - 突破成功率
- `expected_return_10d`
  - 10 日预期收益

### 3.5 结果标签字段

- `future_return_1d / 3d / 5d / 10d`
  - 个股未来 N 个交易日收益
- `future_max_runup_1d / 3d / 5d / 10d`
  - 从分析日收盘价出发，未来 N 个交易日窗口内出现的最大正向波动
- `future_max_drawdown_1d / 3d / 5d / 10d`
  - 从分析日收盘价出发，未来 N 个交易日窗口内出现的最大负向回撤
- `benchmark_future_return_1d / 3d / 5d / 10d`
  - 基准未来 N 个交易日收益
- `excess_return_1d / 3d / 5d / 10d`
  - 个股相对基准超额收益
- `outperform_benchmark_1d / 3d / 5d / 10d`
  - 是否跑赢基准，`1` 表示是，`0` 表示否

### 3.6 评分字段

- `structure_score`
- `risk_score`
- `risk_level`
- `execution_score`
- `execution_risk_score`
- `gentle_expand_score`
- `pullback_shrink_score`
- `distribution_score`
- `stall_score`
- `early_stage_score`
- `mid_stage_score`
- `late_stage_score`
- `market_trend_score`
- `sector_trend_score`
- `sector_strength_score`
- `fundamental_score`
- `overhang_ratio`

### 3.7 排序辅助字段

- `decision_priority`
- `entry_zone_priority`
- `opportunity_priority`

这些字段不是给展示层看的，而是为了：

- 复现实验时保持排序一致
- 做规则切片
- 做二次排序和 TopN 组合构建

### 3.8 候选标签与策略层字段

- `candidate_tags`
  - 三期候选标签，逗号分隔
- `candidate_tag_count`
  - 命中的候选标签数量
- `candidate_tag_reasons`
  - 候选标签命中原因
- `strategy_ids`
  - 命中的策略 ID，逗号分隔
- `strategy_versions`
  - 命中的策略版本，格式为 `strategy_id@strategy_version`
- `strategy_signal_count`
  - 命中的策略数量
- `strategy_signal_reasons`
  - 策略命中原因
- `strategy_validation_paths`
  - 策略级收益验证报告路径
- `strategy_signals`
  - 结构化策略命中记录，保留 `strategy_id`、版本、状态、因子组、命中标签、验证路径等信息

这些字段的边界是：

- 候选标签用于解释策略为什么命中。
- 策略ID用于收益验证、版本对照和后续回测。
- 当前 `_test` 策略不改变 `rank`，只作为研究字段沉淀。

## 4. 统一研究输入的使用建议

研究端只依赖 `market_snapshot_records` 时，建议遵循下面的约定：

1. 横截面研究：
   - 先按 `analysis_date` 取某一天全部记录
   - 再按 `rank`、`execution_score`、`p_continue_10d` 等做切片

2. 时序研究：
   - 按 `ticker + analysis_date` 查询
   - 用于看单票在不同交易日的因子演变

3. 组合构建：
   - 优先使用 `rank`
   - 需要自定义筛选时，再叠加 `industry`、`conclusion_label`、`risk_score`

4. 因子分析：
   - 使用原始数值字段，不要直接用中文展示字段
   - 例如优先用 `execution_score`，不要从 CSV 里的“执行分”反解析

5. 策略验证：
   - 优先按 `strategy_ids` 或 `strategy_signals.strategy_id` 分组
   - `shilun_v1` 作为基准组
   - `_test` 策略与 `shilun_v1` 对比未来收益、胜率、回撤和跑赢基准比例

6. 日报推送：
   - `DailyPushJob` 默认从 `market_snapshot_records` 读取当日记录
   - 只有显式开启 snapshot fallback 时，才允许重新运行 `SnapshotJob`
   - CSV/Markdown 只作为人工查看或本地应急 fallback，不作为默认下游输入

7. 候选池状态：
   - `CandidatePoolJob` 默认从 `market_snapshot_records` 读取当日记录
   - 输出 `candidate_pool_states` 和 `candidate_pool_events`
   - 候选池不改变 `rank`，只把每日榜单升级为可追踪状态

8. 上游数据来源：
   - `SnapshotJob` 默认通过 `MongoMarketDataProvider` 读取 Mongo 原始数据
   - Tushare 原始同步由 `TushareSyncJob` 单独负责
   - 只有显式开启 Tushare fallback 时，策略扫描才允许临时触达 Tushare

## 5. 索引策略

当前代码中已经为这张集合建立以下索引：

1. 唯一索引
   - `(analysis_date, ticker, exclude_st)`
   - 用途：保证单日单票单口径唯一

2. 排名索引
   - `(analysis_date desc, rank asc)`
   - 用途：按天直接取 TopN / BottomN

3. 单票时序索引
   - `(ticker asc, analysis_date desc)`
   - 用途：查看单只股票的时序快照

4. 结论标签索引
   - `(analysis_date desc, conclusion_label asc, rank asc)`
   - 用途：做结论分组与当日筛选

5. 行业索引
   - `(analysis_date desc, industry asc, rank asc)`
   - 用途：做行业内排序、行业中性组合

6. 执行分与风险联合索引
   - `(analysis_date desc, execution_score desc, risk_score asc)`
   - 用途：高执行分、低风险的候选检索

7. 策略命中索引
   - `(analysis_date desc, strategy_signals.strategy_id asc, rank asc)`
   - 用途：按策略ID取当日命中股票，并与 `shilun_v1` 做收益验证对照

## 6. 为什么不直接只用 analysis_snapshots

`analysis_snapshots` 更像“逐票原始分析快照”：

- 信息更全
- 结构更深
- 适合做单票解释和复盘

但它不适合直接做横截面研究，因为：

- 字段层次更深
- 排序不稳定
- 不方便直接做日截面组合

所以二期的建议是：

- 单票解释、结构追踪：优先读 `analysis_snapshots`
- 横截面研究、组合回测、收益证明：优先读 `market_snapshot_records`

## 7. 二期后续扩展建议

下一步如果要继续加强这张表，建议优先补三类字段：

1. 标签字段
   - 更长周期的 `future_return_20d`
   - 更长周期的 `benchmark_future_return_20d`
   - 更长周期的 `excess_return_20d`
   - 更长周期的 `future_max_runup_20d`
   - 更长周期的 `future_max_drawdown_20d`

2. 组合字段
   - 是否入选某个策略组合
   - 分配权重
   - 调仓批次

3. 数据血缘字段
   - `pipeline_version`
   - `feature_version`
   - `rule_version`
   - `strategy_version`

这样后面同一只股票、同一天、不同版本策略的结果也能做严格比较。
