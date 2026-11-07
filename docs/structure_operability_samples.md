# 结构可操作性与 A/B/C 模板样本

> 状态：2026-07-22 已上线首版采样；只积累证据，不改变选品排名和交易动作。

## 目标

把“图形好看”拆成可以回放和结算的事实。每天从选品页同源的
`sector_trends_cache.payload.candidates` 冻结一份不可回写的当日快照，记录：

1. 方向清晰度
2. 调整质量
3. 抛压结构
4. 相对强弱
5. 量价确认
6. 风险几何
7. A 上升趋势延续、B 收敛整理突破、C 下跌后结构修复的匹配与触发状态
8. 市场权限、板块背景和数据覆盖
9. T+1/T+3/T+5/T+10 收益、超额收益、MFE、MAE和模板失效情况

## 数据边界

- 样本池只读取选品页完整候选池，不做全市场扫描。
- 日线、指数和板块事实使用冻结日及之前的数据，禁止未来函数。
- 同一 `sample_version + analysis_date + ticker` 只有一条冻结样本。
- 重跑不会改写六维结构、模板匹配或当日市场权限，只补后续结果。
- B 在突破事件追踪未就绪时固定为 `observation_only`。
- C 首版始终为 `observation_only`，不得进入买入池。
- 筹码分布没有稳定全池落库，首版只记录缺口，不作为硬门。
- 本集合不修改候选排名、持仓、委托、飞书或自动下单权限。

## 模板语义

| 模板 | 样本用途 | 首版执行权限 |
| --- | --- | --- |
| A 上升趋势延续 | 验证强趋势中的回踩、站回和延续买点 | 市场为进攻/持有且候选原权限允许时，记录当时是否具备交易资格；不直接下单 |
| B 收敛整理突破 | 验证收敛、突破、回踩保持和真假突破 | 突破追踪缺失时只观察；补齐事件后才允许记录交易资格 |
| C 下跌后结构修复 | 验证停止创新低、低点抬高和右侧修复 | 永远只观察，待样本验证后另行评审 |

模板之间不做跨模板总排名。`fit_score` 只用于描述同一模板内部的匹配程度。

## 落库

Mongo 集合：`structure_operability_samples`

关键字段：

- `sample_id` / `sample_version` / `template_version`
- `analysis_date` / `ticker` / `pool_rank`
- `market_context` / `sector_context`
- `feature_snapshot` / `operability_dimensions`
- `common_gate` / `risk_geometry`
- `templates` / `matched_template_ids` / `triggered_template_ids`
- `source_coverage`
- `outcomes` / `settled_horizons` / `pending_horizons` / `settlement_status`
- `ranking_effect=none` / `execution_effect=none`

同步状态写入 `sync_state`，`dataset=structure_operability_samples`。

## 运行方式

```bash
shilun-structure-samples
shilun-structure-samples --date 2026-07-21
```

后台调度器在交易日 15:40 运行一次，并在 23:05 做夜间兜底；服务启动时还会
幂等补跑最新候选缓存。每次运行先冻结最新候选，再尝试结算所有未完成样本。

## 首批真实样本

2026-07-22 首次冻结 2026-07-21 选品池：

- 候选 95 只；成功写入 95，跳过 0。
- 通用结构门通过 73 只。
- A 匹配 44、触发 11。
- B 匹配 17、触发 1；由于突破追踪为 0，全部保持观察。
- C 匹配 23、修复确认 15；全部保持观察。
- 当日市场权限为防守，因此没有模板被记录为可交易。
- T+1/T+3/T+5/T+10 尚未到期，95 条均为 `pending`。
- 同日重跑新增 0、识别已有 95，幂等通过。

## 进入策略前的门槛

任何模板进入排名或交易权限前，至少需要：

1. 足够的独立交易日和不同市场阶段样本；
2. 分模板统计胜率、期望收益、MFE/MAE、失效率和超额收益；
3. 样本内、样本外及滚动窗口验证；
4. 明确交易成本、涨跌停和不可成交约束；
5. 人工复核典型成功与失败案例；
6. 单独评审市场权限与模板联动，不允许模板分数绕过空仓/防守门。
