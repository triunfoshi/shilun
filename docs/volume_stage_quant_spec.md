# 量价与趋势阶段量化规范

这份文档用于把量价分析、筹码分布与趋势阶段判断统一成石论 1.0 可复用的工程口径。

目标不是复述术语，而是把以下内容落成可计算定义：

- 原子指标
- 模式分数
- 趋势阶段分数
- 筹码地形指标
- `reason_code`
- 与现有代码的对接关系

## 1. 适用范围

适用于石论 1.0 的以下模块：

- `shilun/indicators/`
- `shilun/features/`
- `shilun/pipeline.py`
- `shilun/decision/execution_engine.py`
- 后续排序、仓位、支撑压力位与执行建议模块

本规范优先服务日线系统，分钟级和逐笔级能力不在 1.0 必做范围。

## 2. 设计原则

### 2.1 不直接使用叙事词，先还原为指标

例如：

- “温和放量”不是结论，而是相对量能、收盘质量、推进效率、位置约束的组合
- “趋势末期”不是涨幅大，而是高位、分歧、效率下降、筹码拥挤的组合
- “上方压力重”不是主观感受，而是价格上方筹码密度和成本分布的量化结果

### 2.2 不把模式定义成单阈值，优先做分数

所有模式优先输出：

- `xxx_score`: `0~100`
- `xxx_reason_codes`: 原因码列表

标签只作为分数的离散化结果：

- `>= 70`: strong
- `50~69`: mild
- `< 50`: weak / none

### 2.3 位置、量能、效率、筹码必须一起看

量价模式的统一表达：

```text
Pattern = f(position, relative_volume, price_efficiency, closing_quality, chip_topology)
```

其中：

- `position` 解决“在哪个位置发生”
- `relative_volume` 解决“量能是否异常”
- `price_efficiency` 解决“价格推进是否匹配”
- `closing_quality` 解决“当天收盘强不强”
- `chip_topology` 解决“上面堵不堵、下面托不托”

## 3. 原子指标定义

以下指标是 1.0 建议统一使用的基础字段。

### 3.1 量能类

```text
rv5 = volume / MA(volume, 5)
rv20 = volume / MA(volume, 20)
vol_pct_60 = 当前成交量在过去 60 日中的分位数
pullback_shrink = MA(volume, 5) / MA(volume, 20)
obv_slope_10 = OBV 的 10 日斜率
breakout_volume_percentile = 当前量在近 60 日滚动窗口中的分位
```

说明：

- `rv20` 是日线主量能指标
- `vol_pct_60` 适合识别极端量和舒适区量
- `pullback_shrink` 适合识别回调是否健康

### 3.2 位置与趋势类

```text
ret1 = close / close[-1] - 1
ret3 = close / close[-3] - 1
ret5 = close / close[-5] - 1
ret20 = close / close[-20] - 1

close_to_high20 = close / HHV(high, 20) - 1
close_to_low20 = close / LLV(low, 20) - 1
close_to_ma20 = close / MA(close, 20) - 1
close_to_ma60 = close / MA(close, 60) - 1

ma20_slope = MA20[t] - MA20[t-1]
ma60_slope = MA60[t] - MA60[t-1]

price_vs_ma20_z = (close - MA20) / STD(close, 20)
price_vs_ma60_z = (close - MA60) / STD(close, 60)

r2_20 = 20 日滚动线性拟合 R^2
r2_60 = 60 日滚动线性拟合 R^2
eff20 = efficiency_ratio_20
eff60 = efficiency_ratio_60
```

说明：

- `close_to_high20` 反映是否逼近阶段前高
- `eff20` 反映上涨是否顺畅，适合区分健康推进和高位折返
- `r2_20` 反映趋势是否“直”

### 3.3 K 线质量类

```text
intraday_range = high - low
upper_shadow = (high - max(open, close)) / intraday_range
lower_shadow = (min(open, close) - low) / intraday_range
body_ratio = abs(close - open) / intraday_range
close_near_high_pct = (high - close) / intraday_range
close_near_high_inv = 1 - close_near_high_pct
gap_pct = open / prev_close - 1
```

说明：

- `upper_shadow` 过长常对应高位分歧或冲高受阻
- `close_near_high_inv` 越高说明收盘越强

### 3.4 波动类

```text
atr14 = ATR(14)
atr_pct = atr14 / close
volatility_10d = 10 日收益率标准差
realized_vol_20 = 20 日收益率标准差
vol_compression_ratio = volatility_10d / realized_vol_20
```

说明：

- `atr_pct` 既用于阶段识别，也用于支撑/压力带缓冲
- `vol_compression_ratio` 可辅助识别平台压缩

### 3.5 筹码类

若有 `cyq_perf / cyq_chips`，优先使用真实筹码数据；若没有，则使用近似版。

```text
cost_15 / cost_50 / cost_85 / cost_95
avg_cost
winner_ratio = 当前价以下筹码占比
overhang_ratio = 当前价上方筹码占比或距离型压制强度
support_density = 当前价下方近邻成本峰密度
pressure_density = 当前价上方近邻成本峰密度
cost_band_width = (cost_85 - cost_15) / close
peak_shift_5d = 主成本峰近 5 日迁移速率
chip_concentration = 主峰附近筹码集中度
vacuum_up_ratio = 上方目标区间内低密度区比例
```

说明：

- `winner_ratio` 很高时，不一定利好，可能代表趋势已经偏后段
- `overhang_ratio` 越高，上方解套卖压越大
- `peak_shift_5d` 向上说明平均成本在抬高

## 4. 统一量化函数

### 4.1 梯形隶属函数

所有模式建议用梯形隶属函数表达“最舒适区间”，避免单阈值过硬。

```text
trap(x; a, b, c, d)
= 0, x <= a or x >= d
= (x - a) / (b - a), a < x < b
= 1, b <= x <= c
= (d - x) / (d - c), c < x < d
```

输出范围为 `0~1`，落库前可转成 `0~100`。

### 4.2 反向指标

对于“越低越好”的指标，先做反向量：

```text
body_ratio_inv = 1 - body_ratio
eff20_inv = 1 - eff20
close_near_high_pct 直接用于衡量收盘偏弱
```

## 5. 量价模式定义

## 5.1 `gentle_expand` 温和放量

定义目标：

- 识别适合趋势初期和中期的健康放量
- 排除极端天量、长上影冲高、末期分歧

建议分数：

```text
gentle_expand_score =
0.30 * trap(rv20; 1.00, 1.10, 1.80, 2.30)
+ 0.20 * trap(vol_pct_60; 0.55, 0.65, 0.90, 0.97)
+ 0.20 * trap(close_near_high_inv; 0.55, 0.70, 1.00, 1.00)
+ 0.15 * trap(body_ratio; 0.30, 0.45, 0.80, 0.95)
+ 0.15 * trap(eff20; 0.20, 0.35, 0.70, 0.90)
- 0.15 * trap(upper_shadow; 0.25, 0.40, 1.00, 1.00)
- 0.15 * high_zone_penalty
```

补充约束：

- 最好 `ret1 >= 0`
- 最好 `close_to_ma20 >= 0`
- 若 `close_to_high20 > -0.03` 且 `ret20 > 0.18`，需要降低分数

推荐 `reason_code`：

- `RV20_IN_COMFORT_ZONE`
- `VOLUME_NOT_EXTREME`
- `CLOSE_NEAR_DAILY_HIGH`
- `BODY_SOLID`
- `EFFICIENCY_OK`
- `UPPER_SHADOW_TOO_LONG`
- `HIGH_ZONE_PENALTY`

## 5.2 `pullback_shrink` 回调缩量

定义目标：

- 识别趋势中的健康回踩，而不是破位下行

建议分数：

```text
pullback_shrink_score =
0.30 * trap(pullback_shrink; 0.45, 0.55, 0.85, 0.95)
+ 0.20 * trap(close_to_ma20; -0.03, -0.01, 0.02, 0.05)
+ 0.20 * trap(eff20; 0.25, 0.35, 0.75, 0.90)
+ 0.15 * trap(lower_shadow; 0.05, 0.15, 0.50, 0.70)
+ 0.15 * trap(support_density; 0.55, 0.70, 1.00, 1.00)
```

补充约束：

- `ma20_slope > 0`
- `ret20 > 0`
- 不允许同时满足明显破位特征

推荐 `reason_code`：

- `PULLBACK_VOLUME_SHRINK`
- `NEAR_MA20_SUPPORT`
- `LOWER_SHADOW_SUPPORTIVE`
- `SUPPORT_DENSITY_HIGH`
- `TREND_BACKGROUND_OK`

## 5.3 `impulsive_spike` 脉冲放量

定义目标：

- 识别天量、情绪化放量、加速冲高
- 这类状态不默认看强，通常偏风险

建议分数：

```text
impulsive_spike_score =
0.40 * trap(rv20; 2.00, 2.50, 10.0, 10.0)
+ 0.20 * trap(vol_pct_60; 0.92, 0.97, 1.00, 1.00)
+ 0.20 * trap(abs(ret1); 0.04, 0.07, 0.15, 0.20)
+ 0.20 * trap(upper_shadow; 0.20, 0.35, 1.00, 1.00)
```

推荐 `reason_code`：

- `RV20_EXTREME`
- `TOP_VOLUME_PERCENTILE`
- `ONE_DAY_SURGE`
- `UPPER_SHADOW_PRESENT`

## 5.4 `distribution` 高位分布

定义目标：

- 识别高位放量但推进效率变差的分歧或派发状态

建议分数：

```text
distribution_score =
0.25 * high_zone_flag
+ 0.25 * trap(rv20; 1.40, 1.80, 10.0, 10.0)
+ 0.20 * trap(upper_shadow; 0.30, 0.45, 1.00, 1.00)
+ 0.15 * trap(body_ratio_inv; 0.50, 0.65, 1.00, 1.00)
+ 0.15 * trap(eff20_inv; 0.30, 0.45, 1.00, 1.00)
```

建议高位标志：

```text
high_zone_flag = 1
if close_to_high20 > -0.03 and ret20 > 0.12
else 0
```

推荐 `reason_code`：

- `HIGH_ZONE`
- `VOLUME_UP_BUT_EFFICIENCY_DOWN`
- `UPPER_SHADOW_DISTRIBUTION`
- `SMALL_BODY_AT_HIGH`

## 5.5 `high_level_stall` 高位滞涨

定义目标：

- 识别高位推进停滞、量能转弱、上方重压的状态

建议分数：

```text
stall_score =
0.30 * high_zone_flag
+ 0.20 * trap(rv20; 0.40, 0.55, 0.95, 1.05)
+ 0.20 * trap(ret5; -0.01, 0.00, 0.02, 0.03)
+ 0.15 * trap(pressure_density; 0.60, 0.75, 1.00, 1.00)
+ 0.15 * trap(eff20_inv; 0.20, 0.35, 1.00, 1.00)
```

推荐 `reason_code`：

- `HIGH_ZONE`
- `RET5_STALLED`
- `PRESSURE_DENSITY_HIGH`
- `EFFICIENCY_DECAY`
- `VOLUME_NOT_EXPANDING`

## 6. 趋势阶段定义

趋势阶段不直接由涨幅决定，而是由位置、量价、效率、筹码共同决定。

最终输出：

- `early`
- `mid`
- `late`
- `transition`

当三类阶段分数都不高时，使用 `transition`。

## 6.1 `early` 趋势初期

定义目标：

- 刚脱离低位平台或低位震荡区
- 放量是温和的，不是极端天量
- 成本峰刚开始上移

建议分数：

```text
early_score =
0.20 * low_base_flag
+ 0.20 * breakout_recently_flag
+ 0.15 * gentle_expand_score
+ 0.15 * trap(ma20_slope; 0.00, 0.00+, small+, mid+)
+ 0.10 * trap(winner_ratio; 0.25, 0.35, 0.70, 0.80)
+ 0.10 * trap(overhang_ratio_inv; 0.50, 0.70, 1.00, 1.00)
+ 0.10 * peak_shift_up_flag
```

建议辅助定义：

```text
low_base_flag = 1
if close_to_high20 <= -0.08 and close_to_ma20 >= -0.03
else 0

breakout_recently_flag = 1
if close_to_high20 > -0.03 and ret20 <= 0.12
else 0
```

典型特征：

- 低位蓄势后刚启动
- `winner_ratio` 还没有到极高
- 上方压力不算重
- MA20 刚转上

## 6.2 `mid` 趋势中期

定义目标：

- 趋势方向已确立
- 回调缩量、上冲温和放量
- 筹码支撑抬高

建议分数：

```text
mid_score =
0.20 * trend_alignment_flag
+ 0.20 * pullback_shrink_score
+ 0.15 * gentle_expand_score
+ 0.15 * trap(eff20; 0.35, 0.45, 0.80, 0.95)
+ 0.10 * trap(r2_20; 0.45, 0.55, 0.90, 1.00)
+ 0.10 * trap(support_density; 0.60, 0.75, 1.00, 1.00)
+ 0.10 * trap(overhang_ratio_inv; 0.60, 0.75, 1.00, 1.00)
```

建议辅助定义：

```text
trend_alignment_flag = 1
if ma20_slope > 0 and ma60_slope > 0 and close_to_ma20 > 0
else 0
```

典型特征：

- 趋势顺
- 回踩健康
- 支撑密度高
- 上方筹码不是特别拥挤

## 6.3 `late` 趋势末期

定义目标：

- 已进入高位区或高获利盘区
- 分歧和效率下降增多
- 容易冲高回落或滞涨

建议分数：

```text
late_score =
0.20 * high_zone_flag
+ 0.20 * distribution_score
+ 0.15 * stall_score
+ 0.15 * impulsive_spike_score
+ 0.10 * trap(winner_ratio; 0.75, 0.85, 1.00, 1.00)
+ 0.10 * trap(upper_shadow; 0.25, 0.40, 1.00, 1.00)
+ 0.10 * trap(eff20_inv; 0.20, 0.35, 1.00, 1.00)
```

典型特征：

- 获利盘很高
- 高位量价分歧增多
- 上影、滞涨、脉冲量更常见
- 推进效率下降

## 6.4 趋势阶段标签输出

建议逻辑：

```text
stage_scores = {
  "early": early_score,
  "mid": mid_score,
  "late": late_score,
}

if max(stage_scores.values()) < 55:
    trend_stage = "transition"
else:
    trend_stage = argmax(stage_scores)
```

补充约束：

- 若 `late_score >= 70`，优先压制追涨动作
- 若 `early_score` 和 `mid_score` 接近，优先结合筹码支撑与市场/板块阶段判别

## 7. 筹码地形定义

## 7.1 1.0 真实筹码版

若接入 `cyq_perf / cyq_chips`，建议统一输出：

- `cost_15`
- `cost_50`
- `cost_85`
- `cost_95`
- `avg_cost`
- `winner_ratio`
- `overhang_ratio`
- `support_density`
- `pressure_density`
- `cost_band_width`
- `vacuum_up_ratio`
- `peak_shift_5d`
- `chip_concentration`

## 7.2 1.0 近似版

若无 `cyq_chips`，可先用：

- `cost_50 ~= avg_cost`
- `pressure_main ~= max(cost_85, recent_high, center_upper)`
- `support_main ~= min(cost_50, ma20, center_lower)`

并保留 `chip_source` 标识：

- `cyq_perf`
- `approx_daily_basic`
- `structure_proxy`

## 7.3 筹码模式

建议增加以下标签：

- `support_peak`
- `pressure_peak`
- `vacuum_up`
- `profit_crowded`
- `peak_upshift`

判定思路：

```text
support_peak:
  support_density 高，且 cost_50 接近当前价下方

pressure_peak:
  pressure_density 高，且 cost_85/cost_95 接近当前价上方

vacuum_up:
  vacuum_up_ratio 高，且 pressure_density 低

profit_crowded:
  winner_ratio 高，但 eff20 下降或 distribution_score 上升

peak_upshift:
  peak_shift_5d > 0
```

## 8. 动作层使用原则

模式分数和阶段分数不是直接买卖信号，而是执行层输入。

建议动作层遵守：

- `gentle_expand_score` 高，不代表追涨；需要结合 `trend_stage`
- `pullback_shrink_score` 高，是趋势回踩加分项
- `impulsive_spike_score`、`distribution_score`、`stall_score` 高，应抬高风控
- `late_score` 高时，默认禁止把强势日解释成“主升新起点”

## 9. `reason_code` 规范

所有模式建议输出原因码，便于排序解释和误判复盘。

建议格式：

```text
<CATEGORY>_<DETAIL>
```

示例：

- `VOLUME_RV20_IN_COMFORT_ZONE`
- `VOLUME_TOP_PERCENTILE_TOO_HIGH`
- `PRICE_CLOSE_NEAR_HIGH`
- `PRICE_UPPER_SHADOW_TOO_LONG`
- `TREND_EFFICIENCY_DECAY`
- `CHIP_SUPPORT_DENSITY_HIGH`
- `CHIP_OVERHANG_HEAVY`
- `STAGE_HIGH_ZONE`

## 10. 与现有代码的映射

### 10.1 已有字段可直接复用

来自 [shilun/indicators/volume.py](/Users/shibicheng/shilun_standalone/shilun/indicators/volume.py)：

- `obv_slope_10`
- `vwap_distance`
- `breakout_volume_percentile`
- `pullback_volume_shrink_ratio`

来自 [shilun/indicators/trend.py](/Users/shibicheng/shilun_standalone/shilun/indicators/trend.py)：

- `ma20_slope`
- `ma60_slope`
- `return_1d/5d/10d/20d`
- `close_to_ma20`
- `close_to_recent_high_20`
- `close_to_recent_low_20`
- `trend_r2_20`
- `efficiency_ratio_20`

来自 [shilun/indicators/volatility.py](/Users/shibicheng/shilun_standalone/shilun/indicators/volatility.py)：

- `atr_14`
- `atr_pct`
- `volatility_10d`
- `realized_vol_20`
- `volume_ratio`
- `volume_ratio_20`

来自 [shilun/features/entry_features.py](/Users/shibicheng/shilun_standalone/shilun/features/entry_features.py)：

- `close_near_high_pct`
- `upper_shadow_ratio`
- `lower_shadow_ratio`
- `body_ratio`
- `volume_spike_ratio`
- `position_state`
- `volume_pattern`
- `breakout_confirm_flag`
- `false_breakout_risk_flag`
- `distribution_risk_flag`
- `stall_risk_flag`

来自 [shilun/indicators/chips.py](/Users/shibicheng/shilun_standalone/shilun/indicators/chips.py)：

- `cost_15`
- `cost_50`
- `cost_85`
- `cost_95`
- `winner_rate`
- `overhang_ratio`
- `support_density`
- `pressure_density`
- `cost_band_width`

### 10.2 当前建议补充的新字段

- `vol_pct_60`
- `close_near_high_inv`
- `body_ratio_inv`
- `eff20_inv`
- `high_zone_flag`
- `low_base_flag`
- `breakout_recently_flag`
- `trend_alignment_flag`
- `peak_shift_5d`
- `chip_concentration`
- `vacuum_up_ratio`

### 10.3 当前建议补充的新分数

- `gentle_expand_score`
- `pullback_shrink_score`
- `impulsive_spike_score`
- `distribution_score`
- `stall_score`
- `early_score`
- `mid_score`
- `late_score`

## 11. 1.0 与 1.5 的边界

### 1.0 必做

- 日线相对量能
- 日线位置和趋势效率
- 日线 K 线质量
- 趋势阶段分数
- 基于 `cyq_perf` 的轻量筹码地形
- `reason_code`

### 1.5 再做

- 每笔均量
- 分时均价线
- 盘口挂单与委托差
- 分钟级回踩承接质量
- 基于 `cyq_chips` 的精细密度曲线

## 12. 禁止事项

以下内容不能直接作为系统结论：

- “主力吸筹”
- “庄家洗盘”
- “绝对控盘”
- “放量必涨”

筹码模块只解释：

- 成本结构
- 支撑压力
- 空间顺畅度

不能越权解释不可验证意图。

## 13. 建议的实施顺序

1. 先补原子指标缺口
2. 再实现 5 个量价模式分数
3. 再实现 `early / mid / late / transition`
4. 再把筹码地形接入阶段识别和执行引擎
5. 最后再把这些分数用于排序、仓位和解释文本

## 14. 版本说明

- 当前版本：`v0.1`
- 性质：规范文档
- 目标：统一石论 1.0 中“量价模式”和“趋势阶段”的工程口径
