# 三期因子池清单

本文档用于约束三期新增字段和策略标签：任何新字段必须先归属到因子组，任何新标签必须能追溯到原子规则和验证路径。

## 因子组

| 因子组 | 职责 | 当前字段示例 | 三期状态 |
| --- | --- | --- | --- |
| 趋势 | 判断趋势方向、阶段、延续概率 | `trend_stage`, `p_continue_10d`, `mid_stage_score` | 保留 |
| 结构 | 判断形态质量、突破/整理结构 | `structure_score`, `entry_zone`, `entry_probability` | 保留 |
| 量价 | 判断放量、缩量、分布、滞涨 | `gentle_expand_score`, `pullback_shrink_score`, `distribution_score`, `stall_score` | 保留 |
| 筹码 | 判断上方压力、筹码拥挤 | `chip_context.overhang_ratio` | 保留观察 |
| 风险 | 判断失败概率、风控压力 | `risk_score`, `risk_level`, `p_fail_fast_3d` | 保留 |
| 环境 | 判断市场、板块、行业背景 | `market_context.market_trend_score`, `sector_context.sector_strength_score` | 保留 |
| 事件 | 业绩预告、公告、价格变化等 | `earnings_surprise_pct`, `profit_growth_yoy` | 新增观察 |
| 质量 | 基本面、分红、股东回报质量 | `fundamental_score`, `cash_dividend_yield`, `dividend_consistency_score` | 新增观察 |

## 首批候选标签

| 标签 | 来源借鉴 | 因子组 | 进入主排序状态 | 验证路径 |
| --- | --- | --- | --- | --- |
| `rps_breakout` | Sequoia-X | 趋势、市场环境、板块环境 | 不参与排序 | `research/validation/candidate_tags/rps_breakout.md` |
| `turtle_breakout` | Sequoia-X | 趋势、量价、风险 | 不参与排序 | `research/validation/candidate_tags/turtle_breakout.md` |
| `high_tight_flag` | Sequoia-X、Alpha | 结构、量价、风险 | 不参与排序 | `research/validation/candidate_tags/high_tight_flag.md` |
| `earnings_surprise` | BaoStock 交易技术商城 | 事件、质量 | 不参与排序 | `research/validation/candidate_tags/earnings_surprise.md` |
| `dividend_quality` | BaoStock 交易技术商城 | 质量、风险 | 不参与排序 | `research/validation/candidate_tags/dividend_quality.md` |

## 当前落地边界

- 三期第一步只把候选标签接入 `SnapshotJob` 输出，字段为 `candidate_tags`、`candidate_tag_count`、`candidate_tag_reasons`。
- 候选标签当前是观察字段，不改变 `rank_snapshot_records` 的排序字段和权重。
- 候选标签不直接作为买点下结论，而是进入 `shilun/strategies/` 中的测试策略，由 `strategy_id + strategy_version` 做收益验证。
- `earnings_surprise`、`dividend_quality` 依赖事件/财务增强数据，当前先保留接口与规则位，等待 M2 验证数据接入。
- 任何候选标签进入排序、组合仓位或执行链路前，必须补充样本内、样本外、分市场阶段验证报告。
