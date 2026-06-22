# 三期策略层规范

策略层用于把“石论当前基准策略”和“待测试标签组合”显式版本化。后续收益验证不直接按孤立候选标签下结论，而是按 `strategy_id + strategy_version` 对比。

## 设计目标

- 冻结当前石论基准策略，形成可对照的 `shilun_v1`。
- 把候选标签放进不同测试策略，避免标签散点实验。
- 每条快照记录都能追溯命中的策略、策略版本、使用因子和验证报告。
- 策略进入排序、仓位或执行前，必须先有策略级验证报告。

## 记录层级

```text
snapshot 原始字段
-> candidate_tags 候选标签
-> strategy 策略定义
-> strategy_signals 策略命中记录
-> validation/backtest 收益验证
```

## 策略定义字段

| 字段 | 含义 |
| --- | --- |
| `strategy_id` | 策略唯一编码 |
| `strategy_version` | 策略版本 |
| `status` | `baseline` / `testing` / `promoted` / `retired` |
| `factor_groups` | 策略使用的因子组 |
| `factor_fields` | 策略依赖的字段 |
| `candidate_tags` | 策略依赖的候选标签 |
| `candidate_tag_mode` | 标签匹配模式，`all` 或 `any` |
| `ranking_policy` | 排序口径说明 |
| `validation_report_path` | 收益验证报告路径 |

## 首批策略

| 策略ID | 状态 | 用途 |
| --- | --- | --- |
| `shilun_v1` | `baseline` | 当前石论排序体系，作为所有测试策略的收益对照组 |
| `shilun_v1_rps_breakout_test` | `testing` | 验证 RPS/突破标签是否贡献超额收益 |
| `shilun_v1_turtle_breakout_test` | `testing` | 验证海龟突破过滤是否改善收益/回撤 |
| `shilun_v1_high_tight_test` | `testing` | 验证强势整理/高位紧凑结构是否有延续优势 |
| `shilun_v1_event_quality_test` | `testing` | 验证业绩超预期和分红质量因子组合 |

## SnapshotJob 输出

`market_snapshot_records` 新增以下字段：

| 字段 | 含义 |
| --- | --- |
| `strategy_ids` | 命中的策略ID，逗号分隔 |
| `strategy_versions` | 命中的策略版本，格式 `strategy_id@version` |
| `strategy_signal_count` | 命中的策略数量 |
| `strategy_signal_reasons` | 命中原因 |
| `strategy_validation_paths` | 后续验证报告路径 |
| `strategy_signals` | 结构化策略命中记录，便于 Mongo 查询和后续研究 |

## 收益验证原则

- `shilun_v1` 是基准，不与候选标签绑定。
- `_test` 策略只用于验证，不影响当前榜单排序。
- 同一只股票可以同时命中多个策略。
- 验证报告按策略ID聚合，不按孤立标签下最终结论。
- 只有 `testing` 策略通过样本内、样本外、分市场阶段验证后，才能升级为 `promoted`。

## 验证入口

三期策略级收益验证入口为：

```bash
python -m research.validation.strategy_validation --start-date 2026-03-01 --end-date 2026-05-16
```

它只读取 Mongo `market_snapshot_records`，按 `strategy_id + strategy_version` 聚合未来收益、超额收益、胜率、跑赢率和最大回撤，输出到 `research/validation/strategies/`。
