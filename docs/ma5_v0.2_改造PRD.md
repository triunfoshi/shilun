# 石论 MA5 v0.2 完整改造 PRD

> 基于 `docs/ma5趋势线战法` v0.2 文档全文，落地为工程化改造清单。
> 每日按本 PRD 的阶段推进，每个阶段都有明确的字段清单、验收标准和依赖关系。
>
> 编写时间：2026-07-05
> 目标：把当前 30% 对齐度的实现推到 95% 对齐度

---

## 0. 现状快照与差距分析

### 0.1 当前对齐度评分（按 v0.2 章节分层）

| v0.2 章节 | 我们已实现 | 对齐度 | 主要缺口 |
|---|---|---|---|
| 第 4 层 · market_gate | `_build_market_gate` 完整 | 🟢 95% | up_ratio 阈值需按文档校准 |
| 第 5 层 · sector_multiplier | 有乘数但公式错 | 🟡 50% | 权重公式与文档不同 |
| 第 6 层 · MA5 趋势 4 状态 | 只有 aligned + slope | 🔴 15% | 缺 early/weaken/downtrend 分类 |
| 第 7 层 · 回踩 6 规则 | 部分硬编码 | 🟡 40% | 缺动态阈值 + 6 规则齐全 |
| 第 8 层 · 突破确认 | 有 3% + 1.5x 硬编码 | 🟡 45% | 缺 close_position / real_body |
| 第 9 层 · 真假突破 | 完全无 | 🔴 0% | 缺 previous_high / box_upper 全套 |
| 第 10 层 · 量价健康 | 3 个 risk_flag | 🟡 30% | 缺 volume_price_score 加权体系 |
| 第 11 层 · 卖出信号 | 完全无 | 🔴 0% | **最大缺口** |
| 第 12 层 · 账户破窗 | 完全无 | 🔴 0% | 依赖持仓功能 |
| 动态阈值（ATR） | 全部硬编码 | 🔴 0% | 需 atr_pct_14 全表 |
| 分位数指标 | 无 | 🔴 0% | 需 percentile_120 全套 |
| 三分制评分 | 单分 entry_quality | 🔴 10% | 需拆 stock_q / timing_q / risk_adj |
| build_trade_plan | 部分 | 🟡 40% | 缺 add_point / target_price |

**总对齐度：约 30%**

### 0.2 v0.2 的三个核心哲学（改造必须遵守）

1. **规则语言 → 指标系统**：不再说"回踩健康"，改说"pullback_depth 在 3-12% 内且 volume_ratio_20 < 0.8"
2. **股票质量分 ≠ 买点质量分**：值得关注 ≠ 现在能买
3. **所有阈值必须能被 ATR/分位数动态适配**：波动小的票和波动大的票不能用同一阈值

---

## 1. 阶段总览与依赖图

```
阶段 1 特征层（3-4 天）
  ├─ 1a. _prepare_stock_frame 加 ATR + 分位数列
  ├─ 1b. build_ma_features 提取 20 个 MVP 字段
  └─ 1c. 单元测试特征计算
        ↓
阶段 2 三分制评分（2-3 天）
  ├─ 2a. stock_quality_score 计算
  ├─ 2b. trade_timing_score 计算
  ├─ 2c. risk_adjustment 系数体系
  └─ 2d. final_trade_score 联乘公式
        ↓
阶段 3 三大买点指标化（3-4 天）
  ├─ 3a. MA5 回踩确认（含动态阈值）
  ├─ 3b. MA5 突破确认（close_position / real_body）
  └─ 3c. MA5 假跌破重新站回
        ↓
阶段 4 真假突破（3-4 天）
  ├─ 4a. previous_high 计算与维护
  ├─ 4b. box_upper 平台识别算法
  ├─ 4c. previous_high_hold_ratio 4 档判定
  └─ 4d. breakout_quality 综合标签
        ↓
阶段 5 量价健康完整化（2 天）
  ├─ 5a. 6 个量价标签补齐
  └─ 5b. volume_price_score 加权
        ↓
阶段 6 板块公式重构（2 天）
  ├─ 6a. sector_mainline_score 权重表落地
  └─ 6b. sector_state 4 分类
        ↓
阶段 7 卖出信号 + 持仓（5-7 天）★关键缺口
  ├─ 7a. 持仓集合 + CRUD API
  ├─ 7b. 每日持仓状态判定
  ├─ 7c. 9 个卖出信号识别
  ├─ 7d. 减仓/清仓分层动作
  └─ 7e. 08 持仓管理 Tab 前端
        ↓
阶段 8 账户破窗（3-4 天）
  ├─ 8a. 交易记录集合 + 统计
  ├─ 8b. account_window_state 4 分类
  └─ 8c. 熔断规则
        ↓
阶段 9 交易计划 build_trade_plan（3 天）
  ├─ 9a. entry_price / confirm_point / target_price
  ├─ 9b. support 三档 + stop_loss 两档
  ├─ 9c. add_point + reward_risk_ratio
  └─ 9d. invalid_conditions 触发列表
        ↓
阶段 10 回测框架（5-7 天）
  ├─ 10a. 历史信号扫描器
  ├─ 10b. 分位数阈值拟合
  └─ 10c. 每种信号的胜率/盈亏比统计
```

**总工期估算：30-45 天**（按每天专注 4 小时算），实际按项目节奏可拉长到 2-3 个月。

---

## 2. 阶段 1：特征层建设（3-4 天）

### 2.1 目标

在每只票的日线上补齐 v0.2 需要的所有基础特征列，做到"一次计算，多次使用"。

### 2.2 文件改动清单

**主文件**：`shilun/market/sector.py` 的 `_prepare_stock_frame`
**新文件**：`shilun/market/ma5_features.py`（特征计算入口）
**测试文件**：`tests/test_ma5_features.py`

### 2.3 阶段 1a：`_prepare_stock_frame` 加基础动态列

在 `_prepare_stock_frame` 里对每只票的时间序列增加以下列：

```python
# 已有：ma5 / ma10 / ma20 / pct_chg / amount / volume

# 新增波动率
frame["atr_14"] = _compute_atr(frame, period=14)            # 绝对 ATR
frame["atr_pct_14"] = frame["atr_14"] / frame["close"]      # 相对 ATR（用于动态阈值）
frame["median_abs_return_20d"] = frame.groupby("ticker")["pct_chg"].transform(
    lambda s: s.abs().rolling(20, min_periods=5).median()
)

# 新增历史相对值
frame["volume_ratio_20"] = frame["volume"] / frame.groupby("ticker")["volume"].transform(
    lambda s: s.rolling(20, min_periods=5).median()
)
frame["amount_ratio_60"] = frame["amount"] / frame.groupby("ticker")["amount"].transform(
    lambda s: s.rolling(60, min_periods=15).median()
)
frame["range_ratio_20"] = (frame["high"] - frame["low"]) / frame.groupby("ticker").apply(
    lambda g: (g["high"] - g["low"]).rolling(20, min_periods=5).median()
)

# 新增分位数
frame["volume_percentile_120"] = frame.groupby("ticker")["volume"].transform(
    lambda s: s.rolling(120, min_periods=30).rank(pct=True)
)
frame["return_percentile_120"] = frame.groupby("ticker")["pct_chg"].transform(
    lambda s: s.rolling(120, min_periods=30).rank(pct=True)
)
frame["extension_percentile_120"] = _rolling_rank_pct(
    frame.groupby("ticker").apply(lambda g: g["close"]/g["ma5"] - 1), 120
)

# 新增 K 线质量
frame["close_position"] = _safe_div(frame["close"] - frame["low"], frame["high"] - frame["low"])
frame["real_body_ratio"] = _safe_div(
    (frame["close"] - frame["open"]).abs(), frame["high"] - frame["low"]
)
frame["upper_shadow_ratio"] = _safe_div(
    frame["high"] - frame[["open", "close"]].max(axis=1), frame["high"] - frame["low"]
)
frame["lower_shadow_ratio"] = _safe_div(
    frame[["open", "close"]].min(axis=1) - frame["low"], frame["high"] - frame["low"]
)
```

**辅助函数** `_compute_atr`：

```python
def _compute_atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR。"""
    def _one(group):
        high = group["high"]
        low = group["low"]
        prev_close = group["close"].shift(1)
        tr = pd.concat([
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()
    return frame.groupby("ticker", group_keys=False).apply(_one)
```

### 2.4 阶段 1b：`ma5_features.py` 建立特征提取器

新建 `shilun/market/ma5_features.py`，暴露 `build_ma_features(bars)`：

```python
def build_ma_features(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """从近 60 根日线中提取 v0.2 MVP 20 字段（+ 扩展字段）。

    要求 bars 已经含 ma5/ma10/ma20/volume/amount 等基础列，
    以及阶段 1a 计算好的 atr_pct_14 / volume_ratio_20 等动态列。

    输出扁平字典，方便存入 candidate 记录、写入 signal_events 集合。
    """
    if len(bars) < 20:
        return {"insufficient_bars": True}

    latest = bars[-1]
    prev = bars[-2]

    return {
        # 一、MA5 趋势结构（5）
        "close_ma5_ratio": _f(latest["close"]) / _f(latest["ma5"]) - 1,
        "ma5_slope_3d": _slope(bars, "ma5", 3),
        "ma5_slope_5d": _slope(bars, "ma5", 5),
        "ma5_distance": _f(latest["close"]) / _f(latest["ma5"]) - 1,
        "close_ma10_ratio": _f(latest["close"]) / _f(latest["ma10"]) - 1,
        "close_ma20_ratio": _f(latest["close"]) / _f(latest["ma20"]) - 1,
        "ma20_slope_5d": _slope(bars, "ma20", 5),
        "ma5_extension_percentile_120": _f(latest.get("extension_percentile_120")),
        "ma_alignment_score": _ma_alignment_score(latest),

        # 二、MA5 回踩确认（4）
        "prior_extension_from_ma5": _prior_extension(bars, lookback=8),
        "pullback_depth": _pullback_depth(bars, lookback=8),
        "pullback_to_ma5_distance": _f(latest["low"]) / _f(latest["ma5"]) - 1,
        "pullback_volume_ratio": _f(latest.get("volume_ratio_20")),
        "ma5_reclaim_flag": _reclaim_flag(latest, prev),
        "bullish_engulf_flag": _engulf_flag(latest, prev),
        "ma5_hold_flag": _f(latest["low"]) / _f(latest["ma5"]) - 1 > -0.02
                         and _f(latest["close"]) >= _f(latest["ma5"]),

        # 三、MA5 突破确认（4）
        "ma5_breakout_flag": _f(latest["close"]) > _f(latest["ma5"])
                              and _f(prev["close"]) <= _f(prev["ma5"]),
        "breakout_volume_ratio": _f(latest.get("volume_ratio_20")),
        "close_position": _f(latest.get("close_position")),
        "real_body_ratio": _f(latest.get("real_body_ratio")),

        # 四、真假突破（3 + 3）
        "previous_high_20": max(_f(b["high"]) for b in bars[-20:]),
        "previous_high_60": max(_f(b["high"]) for b in bars[-60:]),
        "previous_high_hold_ratio": _previous_high_hold_ratio(bars),
        "fall_back_into_box_flag": _fall_back_into_box(bars),
        "post_breakout_drawdown_5d": _post_breakout_drawdown(bars, n=5),

        # 五、量价风险（3 + 3）
        "upper_shadow_ratio": _f(latest.get("upper_shadow_ratio")),
        "high_volume_stall_flag": _high_volume_stall(latest),
        "volume_break_ma5_flag": _f(latest["close"]) < _f(latest["ma5"])
                                  and _f(latest.get("volume_ratio_20")) > 1.3,
        "mild_volume_up_flag": _f(latest["pct_chg"]) > 0
                                and 1.1 <= _f(latest.get("volume_ratio_20")) <= 1.8,
        "shrink_pullback_flag": _f(latest["pct_chg"]) < 0
                                 and _f(latest.get("volume_ratio_20")) < 0.8,
        "new_high_without_volume_flag": _new_high_without_volume(bars),
        "volume_price_divergence_flag": _volume_price_divergence(bars),

        # 六、动态阈值（辅助）
        "atr_pct_14": _f(latest.get("atr_pct_14")),
        "median_abs_return_20d": _f(latest.get("median_abs_return_20d")),
        "dynamic_pullback_min": 0.5 * _f(latest.get("median_abs_return_20d")),
        "dynamic_pullback_max": 2.5 * _f(latest.get("median_abs_return_20d")),
        "dynamic_tolerance": max(0.01, 0.3 * _f(latest.get("atr_pct_14"))),
    }
```

### 2.5 关键辅助函数明细

| 函数 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `_slope(bars, col, n)` | bars, "ma5", 3 | float | `bars[-1][col] / bars[-1-n][col] - 1` |
| `_prior_extension(bars, N)` | bars, 8 | float | `max(close in bars[-N:-1]) / ma5_at_that_peak - 1` |
| `_pullback_depth(bars, N)` | bars, 8 | float | `1 - min(low in bars[-N:]) / max(high in bars[-N:])` |
| `_reclaim_flag(latest, prev)` | K 线 | bool | `latest.close > ma5 && prev.close <= prev.ma5` |
| `_engulf_flag(latest, prev)` | K 线 | bool | 阳克阴：今日实体反包昨日实体 |
| `_ma_alignment_score(latest)` | K 线 | 0-100 | MA5>MA10>MA20 得 100，反之递减 |
| `_previous_high_hold_ratio(bars)` | bars | float | `low_after_breakout / previous_high - 1` |
| `_fall_back_into_box(bars)` | bars | bool | 突破后收盘跌回箱体上沿以内 |
| `_high_volume_stall(latest)` | K 线 | bool | `volume_ratio>1.3` 但 `close_position<0.4` |
| `_new_high_without_volume(bars)` | bars | bool | 价格创 20 日新高但成交量分位 <60% |
| `_volume_price_divergence(bars)` | bars | bool | 最近 5 天价格创新高但成交量连续下降 |

### 2.6 阶段 1c：单元测试

`tests/test_ma5_features.py` 覆盖：
- 5 组模拟数据（强上涨 / 弱回踩 / 假突破 / 缩量整理 / 破位下跌）
- 每种情况验证输出字段的期望值
- 极端边界（数据不足、缺失列、连续涨停）

### 2.7 阶段 1 验收标准

- [ ] `_prepare_stock_frame` 新增 12 列全部正确
- [ ] `build_ma_features` 输出 30+ 字段，无 NaN
- [ ] 单元测试全部通过
- [ ] 跑一次全市场 candidates 耗时不超过原来的 1.5 倍

---

## 3. 阶段 2：三分制评分体系（2-3 天）

### 3.1 目标

把当前的单一 `entry_quality` 拆成 v0.2 明确要求的**三个分**，每个分回答不同问题。

### 3.2 三分制定义（引自 v0.2 第 13 章）

**stock_quality_score**（0-100）：**"这只票值不值得关注"**
```
stock_quality_score =
  35% * trend_structure_score
+ 25% * sector_score
+ 20% * volume_price_score
+ 20% * relative_strength_score
```

**trade_timing_score**（0-100）：**"现在是不是舒服的买点"**
```
trade_timing_score =
  35% * ma5_pullback_score
+ 25% * ma5_breakout_score
+ 20% * breakout_quality_score
+ 20% * volume_confirmation_score
```

**risk_adjustment**（0.0-1.0）：**"这笔有没有明显风险"**
```
risk_adjustment = 1.0 * 各降权系数：
  high_volume_stall_flag:     × 0.80
  upper_shadow_warning:       × 0.85
  new_high_without_volume:    × 0.85
  volume_break_ma5_flag:      × 0.60
  market_defense:             × 0.70
  sector_retreat:             × 0.60
  account_cracked:            × 0.70
  account_broken:             × 0.00
```

**最终交易分**：
```
final_trade_score = trade_timing_score * market_multiplier * sector_multiplier * risk_adjustment
```

### 3.3 各子分详细计算

**trend_structure_score**（0-100）：

```python
def compute_trend_structure_score(features):
    return (
        30 * _score_close_above_ma5(features)      # 0-100
        + 25 * _score_ma5_slope(features)
        + 20 * _score_ma_alignment(features)
        + 15 * _score_ma10_buffer(features)
        + 10 * _score_ma20_background(features)
    ) / 100

def _score_close_above_ma5(f):
    r = f["close_ma5_ratio"]
    if r > 0.03: return 100
    if r > 0: return 60 + r * 1333
    if r > -0.02: return 40 + (r + 0.02) * 1000
    if r > -0.05: return 20
    return 0

def _score_ma5_slope(f):
    s = f["ma5_slope_3d"]
    if s > 0.02: return 100
    if s > 0: return 50 + s * 2500
    if s > -0.01: return 30
    return 0

def _score_ma_alignment(f):
    return f["ma_alignment_score"]   # 已在 features 里算好

def _score_ma10_buffer(f):
    r = f["close_ma10_ratio"]
    if r > 0: return 100
    if r > -0.02: return 60
    if r > -0.05: return 30
    return 0

def _score_ma20_background(f):
    r = f["close_ma20_ratio"]
    if r > 0.05: return 100
    if r > 0: return 70
    if r > -0.03: return 40
    return 0
```

**volume_price_score**（-100 到 +40）：

```python
def compute_volume_price_score(features):
    score = 0
    if features.get("mild_volume_up_flag"): score += 20
    if features.get("shrink_pullback_flag"): score += 20
    if features.get("high_volume_stall_flag"): score -= 25
    if features.get("volume_break_ma5_flag"): score -= 30
    if features.get("new_high_without_volume_flag"): score -= 20
    if features.get("upper_shadow_ratio", 0) > 0.4: score -= 15
    return score
```

**relative_strength_score**（0-100）：

```python
def compute_relative_strength_score(features, benchmark_return_20d):
    excess_20d = features.get("return_20d", 0) - benchmark_return_20d
    # 用 excess return 的分位数
    return min(100, max(0, 50 + excess_20d * 500))
```

**ma5_pullback_score / ma5_breakout_score / breakout_quality_score / volume_confirmation_score**：见阶段 3 与阶段 4 明细。

### 3.4 文件改动清单

**改动 `shilun/market/candidates.py`**：
- 新增 `compute_stock_quality_score(features, sector_ctx, benchmark_ctx) -> dict`
- 新增 `compute_trade_timing_score(features) -> dict`
- 新增 `compute_risk_adjustment(features, market_gate, sector_state, account_state) -> dict`
- 新增 `compute_final_trade_score(...)` 联乘
- 每个 candidate 记录追加 5 个字段：
  - `stock_quality_score`
  - `trade_timing_score`
  - `risk_adjustment`
  - `final_trade_score`
  - `score_breakdown`（每个子分的贡献明细）

### 3.5 前端联动

**候选表新增两列**：
- 股票质量分（大数字 + 迷你 sparkline）
- 买点质量分（大数字 + 状态色）

**Hover 显示 `score_breakdown`**：
```
stock_quality_score = 78
  ├─ trend_structure: 85 × 0.35 = 29.75
  ├─ sector: 72 × 0.25 = 18.00
  ├─ volume_price: +20 × 0.20 = 4.00
  └─ relative_strength: 65 × 0.20 = 13.00
```

**排序切换**：候选表列头点击可切换按 `stock_quality` 还是 `trade_timing` 排序。

### 3.6 阶段 2 验收标准

- [ ] 三个分独立可查、各自 0-100 或 0-1
- [ ] `final_trade_score` 联乘公式与文档完全一致
- [ ] 前端能看到 `score_breakdown` 细分
- [ ] 修改任一 flag 能看到 risk_adjustment 数值变化

---

## 4. 阶段 3：三大买点指标化（3-4 天）

### 4.1 目标

按 v0.2 第 7、8 章把三种买点识别升级为完整的多规则判定，全部支持动态阈值。

### 4.2 A 类：MA5 回踩确认（第 7 章）

**6 条规则（v0.2 原文）**：
1. `prior_extension_from_ma5 >= 3%-5%`
2. `pullback_depth ∈ [3%, 12%]`
3. `pullback_to_ma5_distance ∈ [-2%, +2%]`
4. `pullback_volume_ratio < 0.8`
5. 次日重新站上 MA5 或阳克阴
6. 大盘至少 hold，板块不是 retreat

**动态阈值化改造**：

```python
def detect_ma5_pullback_confirm(features, market_gate, sector_state):
    dyn_min = features["dynamic_pullback_min"]   # = 0.5 * median_abs_return_20d
    dyn_max = features["dynamic_pullback_max"]   # = 2.5 * median_abs_return_20d
    tolerance = features["dynamic_tolerance"]    # = max(1%, 0.3 * atr_pct_14)

    prior_ok = features["prior_extension_from_ma5"] >= max(0.03, dyn_min * 0.6)
    depth_ok = dyn_min <= features["pullback_depth"] <= dyn_max
    distance_ok = abs(features["pullback_to_ma5_distance"]) <= max(0.02, tolerance * 2)
    shrink_ok = features["pullback_volume_ratio"] < 0.8
    reclaim_ok = features["ma5_reclaim_flag"] or features["bullish_engulf_flag"]
    macro_ok = market_gate["permission"] in ("attack", "hold") and sector_state != "retreat"

    # 6 条全部满足 → 强
    passed = sum([prior_ok, depth_ok, distance_ok, shrink_ok, reclaim_ok, macro_ok])

    return {
        "signal": "ma5_pullback_confirm" if passed >= 5 else "watch",
        "signal_status": "buyable" if passed == 6 else "confirm_next_day" if passed == 5 else "watch",
        "passed_rules": passed,
        "rule_details": {
            "prior_extension": prior_ok,
            "pullback_depth": depth_ok,
            "distance_to_ma5": distance_ok,
            "shrink_volume": shrink_ok,
            "reclaim_or_engulf": reclaim_ok,
            "macro_allow": macro_ok,
        },
    }
```

**回踩分**（v0.2 第 7.4 节）：
```python
def compute_ma5_pullback_score(features):
    return (
        25 * _score_prior_extension(features)
        + 25 * _score_pullback_depth(features)
        + 20 * _score_pullback_to_ma5(features)
        + 20 * _score_shrink_volume(features)
        + 10 * _score_reclaim_or_engulf(features)
    ) / 100
```

### 4.3 B 类：MA5 突破确认（第 8 章）

**有效突破规则（v0.2 原文）**：
```
valid_ma5_breakout:
1. close > MA5
2. ma5_breakout_flag = true  （昨日 close <= ma5，今日 close > ma5）
3. breakout_volume_ratio > 1.2
4. close_position > 0.65
5. real_body_ratio > 0.45
6. 突破前高/箱体 → 加分
```

**强突破规则**：
```
strong_breakout:
- breakout_volume_ratio > 1.5
- close_position > 0.75
- real_body_ratio > 0.55
- previous_high_break_flag 或 box_break_flag = true
```

**实现**：

```python
def detect_ma5_breakout_confirm(features):
    valid = (
        features["close_ma5_ratio"] > 0
        and features["ma5_breakout_flag"]
        and features["breakout_volume_ratio"] > 1.2
        and features["close_position"] > 0.65
        and features["real_body_ratio"] > 0.45
    )
    strong = (
        valid
        and features["breakout_volume_ratio"] > 1.5
        and features["close_position"] > 0.75
        and features["real_body_ratio"] > 0.55
        and (features.get("previous_high_break_flag") or features.get("box_break_flag"))
    )
    if strong:
        return {"signal": "ma5_breakout_confirm", "signal_strength": "strong"}
    if valid:
        return {"signal": "ma5_breakout_confirm", "signal_strength": "valid"}
    return {"signal": "watch", "signal_strength": None}
```

### 4.4 C 类：MA5 假跌破重新站回

v0.2 简要提及但未展开。补充：

```python
def detect_ma5_reclaim(features):
    """昨日跌破 MA5、今日重新站回。用于识别短线洗盘结束。"""
    if not features["ma5_reclaim_flag"]:
        return {"signal": "watch"}
    # 需满足：跌破当日缩量、今日放量重站
    yesterday_shrunk = features.get("prev_volume_ratio_20", 1.0) < 0.9
    today_expanded = features["volume_ratio_20"] > 1.1
    if yesterday_shrunk and today_expanded:
        return {"signal": "ma5_reclaim", "signal_strength": "confirmed"}
    return {"signal": "ma5_reclaim", "signal_strength": "pending"}
```

### 4.5 三大买点合并

`detect_ma5_trade_signal(features, macro_ctx)` 顺序尝试：
1. 先测 `ma5_pullback_confirm`（最高优先，A 类）
2. 再测 `ma5_breakout_confirm`（B 类）
3. 再测 `ma5_reclaim`（C 类）
4. 都不满足 → `watch`

输出 `buy_point_type ∈ {ma5_pullback, ma5_breakout, ma5_reclaim, watch}`。

### 4.6 阶段 3 验收标准

- [ ] 三种买点识别 100% 复现 v0.2 定义
- [ ] 所有阈值走动态适配（ATR + median_abs_return）
- [ ] 每种买点输出 `passed_rules` 明细
- [ ] 单元测试覆盖每种买点的边界条件

---

## 5. 阶段 4：真假突破（3-4 天）

### 5.1 目标

按 v0.2 第 9 章新建真假突破识别器，这是**减少追高亏损的关键**。

### 5.2 关键概念定义

**previous_high**：最近 N 天（20 或 60）的最高价，作为突破参考位。

**box_upper**：箱体上沿。识别算法：
```python
def identify_box_upper(bars, min_days=10, tolerance=0.03):
    """识别最近的震荡箱体上沿。

    定义：过去 N 天里，多次触碰某一价位但未有效突破。
    算法：
    1. 找到最近 min_days 天的最高点集合
    2. 聚类：距离最大值不超过 tolerance 的点算同一聚类
    3. 若聚类内点数 >= 3，视为箱体
    4. 返回该聚类的中位数作为 box_upper
    """
```

### 5.3 `previous_high_hold_ratio` 4 档判定（v0.2 原文）

```python
def classify_previous_high_hold(features):
    r = features["previous_high_hold_ratio"]
    tolerance = features["dynamic_tolerance"]

    # v0.2 定义：
    # >= 0：前高完全守住，可信度高
    # -1% 到 0：轻微跌破，可接受但需二次确认
    # -2% 到 -1%：可疑，降低评分
    # < -2%：明显跌破，可信度大幅下降

    # 动态适配版本：
    if r >= 0:
        return {"grade": "hold", "score": 100}
    if r >= -tolerance:
        return {"grade": "minor_break", "score": 70}
    if r >= -tolerance * 2:
        return {"grade": "suspicious", "score": 40}
    return {"grade": "clear_break", "score": 10}
```

### 5.4 `breakout_quality` 综合标签

```python
def classify_breakout_quality(features):
    """输出 valid / pending_confirmation / suspicious / failed"""
    hold_grade = classify_previous_high_hold(features)["grade"]
    fell_into_box = features.get("fall_back_into_box_flag", False)
    volume_shrink_after = features.get("post_breakout_shrink_ratio", 1.0) < 0.8
    next_day_hold = features.get("next_day_hold_flag", None)

    # v0.2 原文规则
    if hold_grade == "hold" and volume_shrink_after and next_day_hold:
        return "valid"
    if next_day_hold is None:
        return "pending_confirmation"
    if hold_grade == "minor_break" and features.get("recovered_flag"):
        return "suspicious"
    if fell_into_box or hold_grade == "clear_break":
        return "failed"
    return "suspicious"
```

**真假突破分**：
```python
def compute_breakout_quality_score(features):
    return (
        35 * _score_previous_high_hold(features)
        + 25 * _score_fall_back_into_box(features)
        + 20 * _score_post_breakout_shrink(features)
        + 20 * _score_next_day_hold(features)
    ) / 100
```

### 5.5 特殊处理：突破后跟踪窗口

`breakout_quality` 需要跟踪突破后 5-10 天的表现。为此需要一个新集合 `breakout_events`：

```python
{
    "ticker": "603986.SH",
    "breakout_date": "2026-06-15",
    "breakout_type": "ma5" | "previous_high" | "box",
    "breakout_price": 518.00,
    "breakout_volume_ratio": 1.8,
    "tracking_days": [
        {"date": "2026-06-16", "close": 525.0, "low": 515.0, "volume_ratio": 1.5},
        # ... 后续 10 天
    ],
    "final_quality": "valid" | "failed" | ... ,
    "closed_at": "2026-06-25",
}
```

每日跑 `update_breakout_events` 更新未收盘事件的追踪数据。

### 5.6 阶段 4 验收标准

- [ ] `previous_high_hold_ratio` 4 档正确
- [ ] `box_upper` 算法能识别典型震荡区间
- [ ] `breakout_quality` 4 分类可视化
- [ ] `breakout_events` 集合追踪最近 30 天所有突破事件
- [ ] 前端候选表能显示 `breakout_quality` 徽章

---

## 6. 阶段 5：量价健康完整化（2 天）

### 6.1 目标

按 v0.2 第 10 章补齐 6 个量价标签，并加入到 `volume_price_score`。

### 6.2 8 个量价标签定义

| 标签 | 判定条件 |
|---|---|
| `mild_volume_up_flag` | `return > 0 && 1.1 <= volume_ratio_20 <= 1.8` |
| `shrink_pullback_flag` | `return < 0 && volume_ratio_20 < 0.8` |
| `high_volume_stall_flag` | `volume_ratio_20 > 1.3 && close_position < 0.4` |
| `volume_down_risk_flag` | `return < -0.02 && volume_ratio_20 > 1.3` |
| `new_high_without_volume_flag` | 20 日新高 + `volume_percentile_120 < 0.6` |
| `long_upper_shadow_flag` | `upper_shadow_ratio > 0.4` |
| `strong_real_body_flag` | `real_body_ratio > 0.6` |
| `volume_price_divergence_flag` | 近 5 天价格新高但成交量连续下降 |

### 6.3 `volume_price_score` 加权（v0.2 原文）

```
+20 if mild_volume_up_flag
+20 if shrink_pullback_flag
-25 if high_volume_stall_flag
-30 if volume_down_risk_flag
-20 if new_high_without_volume_flag
-15 if upper_shadow_ratio > 0.4
```

范围：-90 到 +40，用于加入 `stock_quality_score` 和 `risk_adjustment`。

### 6.4 前端联动

候选表每只票旁显示量价标签 chip 列表（emoji + 短文本），如：
- `🟢 温和放量` `🟢 缩量回踩`
- `🟠 高位滞涨` `🔴 放量下跌`

### 6.5 阶段 5 验收标准

- [ ] 8 个标签独立可查
- [ ] `volume_price_score` 值域正确
- [ ] 前端候选表标签展示美观

---

## 7. 阶段 6：板块公式重构（2 天）

### 7.1 目标

把当前 `resonance_score`（0-100，几乎所有板块都是 70）重构为 v0.2 第 5 章的加权公式，让板块区分度显著。

### 7.2 新公式（v0.2 原文）

```
sector_mainline_score =
  35% * sector_excess_return_20d_score
+ 25% * sector_excess_return_60d_score
+ 15% * outperform_days_5_score
+ 15% * sector_amount_ratio_score
+ 10% * leader_zhongjun_score
```

**每个子分标准化**：都用相对分位数（0-100），避免绝对值失真。

### 7.3 状态分类（v0.2 原文）

```
mainline_top3:  sector_mainline_score >= 75 && sector_rank <= 3
mainline_top8:  sector_mainline_score >= 60 && sector_rank <= 8
short_hot:      5 日强但 20/60 日不强
retreat:        龙头破位或板块放量下跌
```

**`sector_multiplier` 映射（v0.2 原文）**：
```
mainline_top3:  1.30
mainline_top8:  1.10
hot:            1.00
neutral:        0.85
retreat:        0.60
```

### 7.4 龙头/中军强度

**leader_score**（每个板块）：
```
leader_score = 40% return_5d + 30% volume_ratio + 20% MA5_hold + 10% new_high_flag
```

**zhongjun_score**：
```
zhongjun_score = 板块中"多头排列 + 收盘站 MA5"个股比例
```

**leader_zhongjun_score**（合并）：
```
= 60% * leader_score + 40% * zhongjun_score
```

### 7.5 阶段 6 验收标准

- [ ] 新公式落地后 `sector_multiplier` 有 5 档分化
- [ ] `mainline_top3` 板块（半导体/元器件）明确高于 `hot` 板块
- [ ] `retreat` 板块自动降权 0.6

---

## 8. 阶段 7：卖出信号 + 持仓管理（5-7 天）★关键缺口

### 8.1 目标

这是**当前系统最大的功能缺口**。v0.2 第 11 章明确："卖出比买入更需要规则化"。

### 8.2 数据结构

**新集合 `holdings`**：
```json
{
    "ticker": "603986.SH",
    "name": "兆易创新",
    "entry_date": "2026-06-15",
    "entry_price": 518.00,
    "entry_signal": "ma5_pullback_confirm",
    "entry_size": 0.3,        // 仓位比例
    "target_price": 620.00,    // 期望止盈价
    "stop_loss_1": 495.00,     // MA5 * 0.98
    "stop_loss_2": 470.00,     // MA10 保护位
    "breakdown_level": 445.00, // MA20 主升破坏位
    "add_point": 555.00,       // 加仓触发价
    "invalid_conditions": [    // 交易失效条件
        "close < signal_candle_open (518.00)",
        "MA20 * 0.99",
    ],
    "note": "半导体主线 + 主升 + 主动买入",
    "status": "active" | "reduced" | "closed",
    "created_at": "...",
    "updated_at": "...",
}
```

**新集合 `holding_events`**：每日每只持仓生成事件记录：
```json
{
    "ticker": "603986.SH",
    "date": "2026-06-24",
    "holding_state": "strong_hold" | "normal_pullback" | "weaken_reduce" | "invalid_exit",
    "triggered_sell_signals": [
        "close_below_ma5",
        "ma5_lost_2d",
    ],
    "recommended_action": "hold" | "reduce_20pct" | "reduce_50pct" | "sell_all",
    "reason": "MA5 失守且量能放大 1.5x",
    "current_price": 677.77,
    "unrealized_pnl": 0.309,   // (677.77 - 518) / 518
}
```

### 8.3 9 个卖出信号（v0.2 原文）

| 信号 | 判定条件 | 动作 |
|---|---|---|
| `close_below_ma5` | `close < MA5` | 警惕/减仓 |
| `ma5_lost_2d` | 连续 2 日 `close < MA5` | 减仓 |
| `volume_break_ma5_flag` | `close < MA5 && volume_ratio_20 > 1.3` | 减仓/清仓 |
| `close_below_ma10` | `close < MA10` | 大幅降仓 |
| `volume_break_ma20_flag` | `close < MA20 && volume_ratio_20 > 1.3` | 清仓 |
| `breakout_failed_flag` | `close < breakout_level` | 清仓/退出 |
| `previous_high_lost_flag` | `close < previous_high` | 降权/退出 |
| `signal_candle_open_lost` | `close < signal_candle_open` | 逻辑失效 → 清仓 |
| `breakeven_stop_triggered` | 曾浮盈后回到成本线 | 保护账户 → 清仓 |

### 8.4 4 状态持仓判定（v0.2 原文）

```python
def classify_holding_state(holding, latest_bar, features):
    """输出 strong_hold / normal_pullback / weaken_reduce / invalid_exit"""

    # invalid_exit（清仓触发）：任何一个致命信号
    if (
        features.get("volume_break_ma5_flag")
        or features.get("close_below_ma10") and features.get("no_recovery")
        or features.get("volume_break_ma20_flag")
        or features.get("breakout_failed_flag")
        or latest_bar["close"] < holding["invalid_price"]
    ):
        return "invalid_exit"

    # weaken_reduce（减仓）
    if (
        latest_bar["close"] < features["ma5"]
        or features.get("ma5_lost_2d")
        or features.get("sector_state") == "retreat"
    ):
        return "weaken_reduce"

    # normal_pullback（正常回踩，保持仓位）
    if (
        abs(latest_bar["close"] / features["ma5"] - 1) < 0.02
        and features.get("shrink_pullback_flag")
        and (latest_bar["close"] > features["ma10"] or latest_bar["close"] > features["ma20"])
    ):
        return "normal_pullback"

    # strong_hold（强持有）
    if (
        latest_bar["close"] > features["ma5"]
        and features["ma5_slope_3d"] > 0
        and features.get("volume_price_score", 0) >= 0
        and macro_ctx["permission"] in ("attack", "hold")
        and macro_ctx["sector_state"] != "retreat"
    ):
        return "strong_hold"

    return "normal_pullback"  # 默认
```

### 8.5 分层减仓/清仓动作

**减仓触发**（v0.2 原文）：
```
- 收盘跌破 MA5
- MA5 次日不能收回
- 高位放量滞涨
- 大盘从 attack 转 hold/defense
```
建议动作：卖 20-30%

**清仓触发**（v0.2 原文）：
```
- 放量跌破 MA5
- 跌破 MA10 后不能收回
- 放量跌破 MA20
- 跌破突破阳线开盘价
- 跌回箱体
- 大盘 empty
- 板块退潮
```
建议动作：卖 50-100%

### 8.6 API 设计

```
POST /api/v1/holdings                      # 添加持仓
GET  /api/v1/holdings                      # 列出全部
GET  /api/v1/holdings/{ticker}             # 单只详情
PATCH /api/v1/holdings/{ticker}            # 修改止损/加仓价等
DELETE /api/v1/holdings/{ticker}           # 关闭持仓
GET  /api/v1/holdings/{ticker}/events      # 每日事件历史

POST /api/v1/holdings/daily-check          # 每日跑一次全量检测
```

### 8.7 前端新 Tab：08 持仓管理

顶部：
- 添加持仓表单（ticker / 买入价 / 买入日期 / 买入信号）
- 汇总统计（活跃 X 只 / 总浮盈 +Y% / 触发卖出 Z 只）

中部：持仓列表卡片，每张：
```
┌────────────────────────────────────────────────┐
│ 兆易创新 603986.SH  半导体      浮盈 +30.9%     │
│                                                │
│ 状态：⚠️ weaken_reduce                          │
│                                                │
│ 触发信号：                                      │
│   🔴 close_below_ma5（收盘 665 < MA5 675）      │
│   🟠 ma5_lost_2d（连续 2 日失守）              │
│                                                │
│ 建议动作：减仓 30%                             │
│                                                │
│ 关键价位：                                      │
│   入场 518  当前 665  距止损 -3.5%              │
│   目标 620  已到达 ✅                           │
│                                                │
│ [记录卖出] [调整止损] [查看日志]                │
└────────────────────────────────────────────────┘
```

### 8.8 阶段 7 验收标准

- [ ] 持仓可增删改查
- [ ] 每日自动跑 `daily-check`，触发卖出信号写入 `holding_events`
- [ ] 4 状态分类正确
- [ ] 减仓/清仓分层动作明确
- [ ] 前端 08 Tab 完整可用
- [ ] 触发卖出时推送到飞书（可选）

---

## 9. 阶段 8：账户破窗（3-4 天）

### 9.1 目标

按 v0.2 第 12 章"用系统管住冲动"实现账户熔断。

### 9.2 数据结构

**新集合 `trades`**：完整交易记录（买入 + 卖出）
```json
{
    "trade_id": "uuid",
    "ticker": "...",
    "entry_date": "...",
    "entry_price": ...,
    "exit_date": "...",
    "exit_price": ...,
    "size_pct": 0.3,
    "pnl_pct": 0.15,             // 收益率
    "pnl_amount": 15000,         // 收益金额
    "hold_days": 12,
    "exit_signal": "ma5_lost_2d",
    "had_plan": true,             // 是否按计划执行
    "impulse_flag": false,        // 是否冲动交易
    "market_permission_at_entry": "hold",
    "sector_state_at_entry": "mainline_top8",
}
```

**新集合 `account_state`**：账户状态每日快照
```json
{
    "date": "2026-06-24",
    "window_state": "intact" | "cracked" | "broken" | "repairing",
    "recent_pnl_5trades": [0.15, -0.08, -0.05, 0.20, -0.12],
    "consecutive_loss_count": 2,
    "profit_giveback_ratio": 0.35,
    "profit_to_loss_flag": false,
    "impulse_trade_count_7d": 1,
    "reason": "连续 2 笔亏损 + 利润回吐 35%",
    "allowed_actions": ["A_class_buy_only", "reduce_size"],
    "block_reason": null,
}
```

### 9.3 破窗状态分类（v0.2 原文）

```python
def classify_account_window(recent_trades, current_holdings):
    # broken：暂停交易
    if (
        _single_trade_loss_pct(recent_trades[-1]) > 0.05
        or consecutive_loss_count(recent_trades) >= 3
        or _has_impulse_trade(recent_trades, days=7)
    ):
        return {
            "state": "broken",
            "allowed_actions": [],
            "block_reason": "触发熔断",
        }

    # cracked：只允许 A 类 + 降仓
    if (
        _profit_giveback_ratio(recent_trades) > 0.30
        or consecutive_loss_count(recent_trades) >= 2
        or _profit_to_loss_flag(current_holdings)
    ):
        return {
            "state": "cracked",
            "allowed_actions": ["A_class_buy_only", "reduce_size_50pct"],
        }

    # repairing：熔断恢复期
    if _was_recently_broken(days=7) and consecutive_win_count(recent_trades) < 2:
        return {
            "state": "repairing",
            "allowed_actions": ["A_class_buy_only", "light_size_30pct"],
        }

    return {"state": "intact", "allowed_actions": ["all"]}
```

### 9.4 联动机制

**候选池筛选**：
- `broken` 状态下所有 `final_trade_score` 归 0（`risk_adjustment × 0.0`）
- `cracked` 状态下只显示 `buy_point_type == "ma5_pullback_confirm"`（A 类）
- `repairing` 状态下 `size_hint` 上限 30%

**PART1 顶部横幅**：
```
🔴 账户破窗状态：BROKEN
   触发原因：连续 3 笔亏损（-8% / -5% / -12%）
   系统强制暂停交易，可用日期：2026-07-01 后
```

### 9.5 阶段 8 验收标准

- [ ] 4 状态自动判定
- [ ] 破窗强制降权 candidates
- [ ] 前端顶部横幅明显
- [ ] 可查历史破窗时段

---

## 10. 阶段 9：build_trade_plan（3 天）

### 10.1 目标

按 v0.2 第 16 章为每个买入信号自动生成完整交易计划。

### 10.2 输出结构（v0.2 原文）

```python
trade_plan = {
    "entry_price": 518.00,            # 建议买入价（当前收盘）
    "confirm_point": 525.00,          # 次日需站稳的价位
    "support_1": 510.00,              # 短期支撑（MA5）
    "support_2": 495.00,              # 中期支撑（MA10）
    "support_3": 470.00,              # 长期支撑（MA20）
    "stop_loss_1": 505.00,            # 短线止损（跌破 support_1 触发）
    "stop_loss_2": 490.00,            # 中线止损（跌破 support_2 触发）
    "breakdown_level": 465.00,        # 主升破坏位（跌破 support_3）
    "target_price": 620.00,           # 目标止盈（分位数或前高）
    "add_point": 555.00,              # 加仓价（有效突破新高）
    "reward_risk_ratio": 3.5,         # 盈亏比 = (target - entry) / (entry - stop_loss_1)
    "invalid_conditions": [
        "close < 505 且 volume_ratio_20 > 1.3",
        "MA5 连续 2 日失守",
        "MA20 × 0.99 = 465 跌破",
        "板块 sector_state 变为 retreat",
        "大盘 gate 变为 empty",
    ],
    "sell_triggers": [
        "close_below_ma5",
        "ma5_lost_2d",
        "volume_break_ma20_flag",
        "breakout_failed_flag",
    ],
}
```

### 10.3 计算算法

**entry_price**：当前收盘价
**confirm_point**：次日收盘不能跌破的价位 = MA5 或买入 K 线开盘价（取较高）
**support_1/2/3**：MA5、MA10、MA20 当前值
**stop_loss_1**：MA5 × 0.98（现有逻辑）
**stop_loss_2**：MA10 × 0.98
**breakdown_level**：MA20 × 0.99
**target_price**：
- 优先取近 60 日前高
- 若已站上前高，取 `entry × (1 + 2.5 × median_abs_return_20d × 20)`（相当于 20 日期望走势）
**add_point**：
- 突破 target_price 后 3% 放量 → 加仓
- 或 `entry × 1.07`
**reward_risk_ratio**：`(target - entry) / (entry - stop_loss_1)`

### 10.4 前端展示

单票面板"止损价格"卡片扩展为"交易计划"卡：

```
交易计划
─────────────────────────────
入场价    518.00 元
确认价    525.00 元（次日收盘）
目标价    620.00 元
加仓点    555.00 元（放量突破前高后）
─────────────────────────────
支撑体系
  MA5   510.00  短线支撑
  MA10  495.00  中期支撑
  MA20  470.00  破位关键
─────────────────────────────
止损体系
  短线止损    505.00 元
  中线止损    490.00 元
  破位止损    465.00 元
─────────────────────────────
盈亏比      3.5:1
```

### 10.5 阶段 9 验收标准

- [ ] 每个买入信号自动生成 trade_plan
- [ ] `reward_risk_ratio > 2` 才建议入场
- [ ] `invalid_conditions` 列表清晰
- [ ] 前端可点击加入持仓（自动填充所有字段）

---

## 11. 阶段 10：回测框架（5-7 天）

### 11.1 目标

按 v0.2 第 1 章"先让系统能回测，再优化阈值"，用历史数据拟合参数。

### 11.2 回测流程

```
历史日线（1 年）
    ↓
每日跑 build_ma_features + detect_ma5_trade_signal
    ↓
收集所有信号（买点、卖点）
    ↓
模拟交易（假设按信号买入/卖出）
    ↓
统计每种信号的：
  - 触发次数
  - 胜率（正 PnL 的比例）
  - 平均收益率
  - 最大回撤
  - 盈亏比
  - 持有天数分布
    ↓
输出到 signal_stats 集合
```

### 11.3 参数拟合

对每个动态阈值做**网格搜索**：
- `pullback_depth min/max`：在 `[0.3, 0.8]` × `[1.5, 3.5]` 网格
- `breakout_volume_ratio` 阈值：在 `[1.0, 2.0]` 网格
- `close_position` 阈值：在 `[0.55, 0.80]` 网格

选出**加权目标函数最大**的参数组：
```
objective = 0.5 * win_rate + 0.3 * reward_risk_ratio + 0.2 * sharpe_ratio
```

### 11.4 前端展示

新 Tab **09 回测报告**：
- 参数配置界面
- 每种信号的历史统计（胜率 / 盈亏比 / 触发次数）
- 优化建议

### 11.5 阶段 10 验收标准

- [ ] 1 年历史扫描 60 秒内跑完
- [ ] 每种信号有完整统计
- [ ] 参数拟合结果可写回 `system_config` 集合

---

## 12. 附录 A：v0.2 完整字段清单（60+ 字段）

**趋势结构（9）**：
`close_ma5_ratio`, `ma5_slope_3d`, `ma5_slope_5d`, `ma5_distance`, `ma5_extension_percentile_120`, `close_ma10_ratio`, `close_ma20_ratio`, `ma20_slope_5d`, `ma_alignment_score`

**回踩（7）**：
`prior_extension_from_ma5`, `pullback_depth`, `pullback_to_ma5_distance`, `pullback_volume_ratio`, `pullback_vs_breakout_volume`, `ma5_hold_flag`, `ma5_reclaim_flag`, `bullish_engulf_flag`

**突破（8）**：
`ma5_breakout_flag`, `ma5_breakout_distance`, `breakout_volume_ratio`, `close_position`, `real_body_ratio`, `previous_high_break_flag`, `box_break_flag`, `next_day_hold_flag`

**真假突破（6）**：
`previous_high_20`, `previous_high_60`, `previous_high_hold_ratio`, `fall_back_into_box_flag`, `post_breakout_drawdown_5d`, `post_breakout_shrink_ratio`, `breakout_quality`

**量价（8）**：
`mild_volume_up_flag`, `shrink_pullback_flag`, `high_volume_stall_flag`, `volume_down_risk_flag`, `new_high_without_volume_flag`, `upper_shadow_ratio`, `real_body_ratio`, `volume_price_divergence_flag`, `volume_price_score`

**卖出（9）**：
`close_below_ma5`, `ma5_lost_2d`, `volume_break_ma5_flag`, `close_below_ma10`, `volume_break_ma20_flag`, `breakout_failed_flag`, `previous_high_lost_flag`, `signal_candle_open_lost`, `breakeven_stop_triggered`

**账户破窗（7）**：
`single_trade_loss_pct`, `single_trade_loss_amount`, `consecutive_loss_count`, `profit_giveback_ratio`, `profit_to_loss_flag`, `no_plan_trade_flag`, `impulse_trade_flag`, `account_window_state`

**评分（4）**：
`stock_quality_score`, `trade_timing_score`, `risk_adjustment`, `final_trade_score`

**动态阈值（4）**：
`atr_pct_14`, `median_abs_return_20d`, `dynamic_pullback_min/max`, `dynamic_tolerance`

**分位数（4）**：
`volume_percentile_120`, `return_percentile_120`, `extension_percentile_120`, `amount_percentile_120`

---

## 13. 附录 B：新增集合清单

| 集合 | 用途 | 主键 | 保留期 |
|---|---|---|---|
| `sector_trends_cache` | 板块动向预计算缓存 | date + benchmark | 30 天 |
| `market_part1_cache` | 大盘权限缓存 | date + benchmark | 30 天 |
| `signal_events` | 每日信号事件流 | ticker + date | 永久 |
| `breakout_events` | 突破事件追踪 | ticker + breakout_date | 90 天 |
| `holdings` | 持仓记录 | ticker | 永久 |
| `holding_events` | 每日持仓状态 | ticker + date | 永久 |
| `trades` | 完成的交易记录 | trade_id | 永久 |
| `account_state` | 账户状态每日快照 | date | 永久 |
| `signal_stats` | 信号历史统计（回测输出） | signal_code | 更新覆盖 |
| `system_config` | 拟合后的阈值/参数 | config_key | 永久 |

---

## 14. 附录 C：API 端点清单

**新增**：
- `POST /api/v1/data/precompute-sectors`（已有）
- `POST /api/v1/holdings`
- `GET /api/v1/holdings`
- `PATCH /api/v1/holdings/{ticker}`
- `DELETE /api/v1/holdings/{ticker}`
- `POST /api/v1/holdings/daily-check`
- `GET /api/v1/holdings/{ticker}/events`
- `GET /api/v1/account/state`
- `POST /api/v1/backtest/run`
- `GET /api/v1/backtest/report/{run_id}`
- `GET /api/v1/signal-stats/{signal_code}`

**扩展现有**：
- `GET /api/v1/market/candidates` → 返回 `stock_quality_score` + `trade_timing_score` + `trade_plan`
- `GET /api/v1/stock/panel` → 增加 `trade_plan` + `holding_state`（如果在持仓中）

---

## 15. 附录 D：前端新增 Tab

- **08 持仓管理**（新）：持仓列表 + 每日状态 + 卖出信号
- **09 回测报告**（新）：参数拟合 + 信号统计

---

## 16. 优先级建议与日程

按用户日常价值排序：

**Week 1**（周一到周五，每天 4 小时）
- 阶段 1a-1b：特征层（3 天）
- 阶段 2：三分制评分（2 天）

**Week 2**
- 阶段 3：三大买点（3 天）
- 阶段 5：量价健康（2 天）

**Week 3**
- 阶段 4：真假突破（3 天）
- 阶段 6：板块公式（2 天）

**Week 4-5**（核心缺口）
- 阶段 7：卖出信号 + 持仓管理（5-7 天）

**Week 6**
- 阶段 9：build_trade_plan（3 天）
- 阶段 8：账户破窗（2 天）

**Week 7-8**
- 阶段 10：回测框架（5-7 天）

---

## 17. 每日开发方法

1. **早上**：看 PRD 当天要做的阶段
2. **写代码前**：先写单元测试用例（信号触发的正例 + 负例）
3. **写完代码**：跑测试，再手工验证一次
4. **收盘**：跑一次全市场 candidates，比较改动前后的排序变化
5. **每周复盘**：胜率 / 盈亏比是否符合预期

---

## 18. 备注与例外

- 每个阶段的**验收标准**都是"能用"的最低要求，不追求完美
- 阈值可以先用文档默认值，回测出来再调
- 破窗信号需要用户维护一段时间交易记录才有意义
- 回测需要至少 250 天历史数据（Tushare 已经补齐）
- 所有改动**先落地字段，再改判定，再改前端**，避免一次改太多回归

---

## 19. 最后原则（引自 v0.2 第 18 章）

1. 系统仍然以 MA5 为核心，不改成 MA10/MA20 战法
2. MA10/MA20 只用于缓冲、背景、破位
3. 所有交易语言逐步转成指标字段
4. 不迷信固定阈值，保留历史均值/中位数/分位数/ATR
5. 股票质量分和买点质量分必须分开
6. 买点质量高，也必须经大盘、板块、风险三层过滤
7. 破窗状态必须指标化
8. 先完成 20 个 MVP 字段，再做复杂拟合
9. 先让系统能回测，再优化阈值
10. 最终目标：让"我感觉能买/不能买" → "指标显示可交易/不可交易"

---

**PRD 完。每日按上面阶段推进即可。**

---

## 20. 实施记录

### 2026-07-05 · MVP-1：特征层 + 三分制评分 + 候选池联动

**完成状态**：已完成后端 MVP 与轻量前端展示，待进一步扩展三大买点完整判定、真假突破追踪、持仓管理和回测。

**完成内容**：

| PRD 阶段 | 状态 | 修改文件 | 修改内容 |
|---|---|---|---|
| 阶段 1a：`_prepare_stock_frame` 动态列 | 已完成 MVP | `shilun/market/sector.py` | 在全市场股票日线预处理阶段新增 `ma8`、`volume_ma5`、`atr_14`、`atr_pct_14`、`median_abs_return_20d`、`volume_ratio_20`、`amount_ratio_60`、`range_ratio_20`、`volume_percentile_120`、`return_percentile_120`、`extension_percentile_120`、`real_body_ratio`、`lower_shadow_ratio` 等基础列。 |
| 阶段 1b：`ma5_features.py` 特征提取器 | 已完成 MVP | `shilun/market/ma5_features.py` | 新增 `build_ma_features()`，输出 MA5 趋势结构、回踩、突破、真假突破代理、量价、动态阈值、分位数和收益字段；字段缺失时给安全默认值，保证候选池不中断。 |
| 阶段 2：三分制评分 | 已完成 MVP | `shilun/market/ma5_features.py` | 新增 `compute_stock_quality_score()`、`compute_trade_timing_score()`、`compute_risk_adjustment()`、`compute_final_trade_score()`，按“股票质量分 ≠ 买点质量分”的口径拆分评分，并输出 `score_breakdown`。 |
| 阶段 9：交易计划基础版 | 部分完成 | `shilun/market/ma5_features.py` | 新增 `build_trade_plan()`，输出 `entry_price`、`confirm_point`、`support_1/2/3`、`stop_loss_1/2`、`breakdown_level`、`target_price`、`add_point`、`reward_risk_ratio`、`invalid_conditions`。当前为 MVP，尚未接持仓与完整卖出信号。 |
| 候选池联动 | 已完成 MVP | `shilun/market/candidates.py` | 候选池读取 140 根历史日线构建 MA5 特征，返回 `stock_quality_score`、`trade_timing_score`、`risk_adjustment`、`final_trade_score`、`buy_point_type`、`buy_point_label`、`score_breakdown`、`ma5_feature_snapshot`、`trade_plan`；排序优先使用 `final_trade_score`，旧 `entry_quality` 保留兼容。 |
| 前端轻量展示 | 已完成 MVP | `shilun/static/app.js` | 候选池卡片新增“评分”行，展示最终交易分、股票质量分、买点质量分和风险系数；旧质量分改为“旧质量”，方便对比新旧口径。 |
| 静态资源版本 | 已完成 | `shilun/static/index.html`、`tests/test_ui_route.py` | 静态资源版本更新为 `20260705-ma5-v02-mvp1`，避免浏览器缓存旧 JS；同步更新 UI 路由测试断言。 |
| 单元测试 | 已完成 MVP | `tests/test_ma5_features.py`、`tests/test_market_sector.py` | 新增 MA5 特征/评分/交易计划测试；现有板块测试新增候选池三分制字段断言。 |

### 2026-07-06 · MVP-1 补强：60 日趋势板块公式 + 图表可读性

**完成状态**：已完成阶段 6 的 MVP 级公式切换与趋势图展示修复，解决旧公式、旧缓存和 60 日标签堆叠导致的解释断层。

**完成内容**：

| PRD 阶段 | 状态 | 修改文件 | 修改内容 |
|---|---|---|---|
| 阶段 6：板块公式重构 | 已完成 MVP | `shilun/market/sector.py` | `sector_mainline_score` 切换为 PRD v0.2 公式：`35%*20日相对强度分位 + 25%*60日相对强度分位 + 15%*近5日跑赢分 + 15%*成交额活跃分位 + 10%*龙头/中军反馈分位`；所有横截面项改用分位数，避免多个板块同时 100 分。 |
| 阶段 6：状态分类 | 已完成 MVP | `shilun/market/sector.py` | 排序后再按名次和分数给出 `mainline_top3 / mainline_top8 / hot / neutral / retreat`，并输出 `mainline_rank`、`sector_state_label`、`sector_multiplier` 与排名证据。 |
| 阶段 6：缓存失效 | 已完成 | `shilun/market/sector.py` | `SECTOR_ENGINE_VERSION` 升级为 `market_sector_v3_ma5_v02_mainline`，触发板块预计算重新生成，避免页面继续读取旧公式缓存。 |
| 趋势板块展示 | 已完成 | `shilun/static/app.js`、`shilun/static/app.css` | 趋势卡片新增 5 项子分拆解；60 日趋势图改为“单日相对收益柱 + 累计相对收益线”，增加横轴/纵轴标题，只标注关键点，完整明细放入 hover，解决横坐标和值标签堆叠。 |
| 静态资源版本 | 已完成 | `shilun/static/index.html`、`tests/test_ui_route.py` | 静态资源版本更新为 `20260706-ma5-v02-sector-trend`，避免浏览器继续使用旧趋势图脚本和样式。 |
| 单元测试 | 已完成 MVP | `tests/test_market_sector.py` | 更新 engine version 断言，并新增 `mainline_rank`、20 日相对强度分、成交额活跃分等字段断言，保证趋势板块输出能解释排名。 |

**本次未完成但已留好接口的内容**：

- 阶段 3：三大买点完整指标化，目前仅完成 `trade_timing_score` 的 MVP 评分和 `buy_point_type` 基础分类，尚未输出 PRD 要求的完整 6 条规则明细。
- 阶段 4：真假突破完整追踪，尚未新增 `breakout_events` 集合。
- 阶段 5：量价健康完整化已在特征层与评分层接入核心标签，但前端 chip 展示还未完整设计。
- 阶段 7：卖出信号 + 持仓管理，未开始。
- 阶段 8：账户破窗，未开始。
- 阶段 10：回测框架，未开始。

**验收口径**：

- 候选池 API 返回的每个候选应包含 `stock_quality_score`、`trade_timing_score`、`risk_adjustment`、`final_trade_score` 和 `trade_plan`。
- 页面候选卡片应展示“最终 / 股票 / 买点 / 风险系数”四个新评分字段。
- `final_trade_score` 使用公式：`trade_timing_score * market_multiplier * sector_multiplier * risk_adjustment`。
- `graphify update .` 已纳入本次完成后的必跑步骤，用于同步知识图谱。
