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

### 4.7 五买点形态体系（用户表达层）

**背景**：§4.2-4.5 定义的三大买点（回踩/突破/假跌破站回）是**指标层**——它们决定 `trade_timing_score` 的分数、决定候选池排序。但对用户而言，日常语言里描述买点是"抄底 / 回踩 / 起涨 / 突破 / 追涨"这五类，跟 MA5 战法自然形态一致。§4.7 在指标层之上加一层**形态识别（用户表达层）**，把每张候选卡对外展示的买点分类升级到这五类。

**五个买点定义（用户口径）**：

| # | 买点 | 定义 | 关键形态 |
|---|---|---|---|
| 1 | **抄底点** `chao_di` | 股价在 MA5 下方形成的阶段性谷底 | MA5 下方（`close < MA5`）+ 谷底（最近 10 根 low 是局部最低）+ MA7 确认（`close > MA7`）+ MACD 走出向上 W 形（DIF 转向 + DEA 上翘）。理论存在、实战难捕捉，系统只标注不主推。 |
| 2 | **起涨点** `qi_zhang` | 谷底之后阳克阴的第一根阳线，或回踩 MA5 完毕阳克阴的第一根阳线 | `bullish_engulf_flag = True` + 处于「抄底点/回踩点」之后 1-3 根内。 |
| 3 | **回踩点** `hui_cai` | 有效突破 MA5 后，上涨中回踩 MA5 时的那根 K 线 | 前置：已发生 `ma5_breakout_flag`（近 20 根内）；当前：`pullback_to_ma5_distance ∈ [-2%, +2%]`；量能：`pullback_volume_ratio < 0.9`。 |
| 4 | **突破点** `tu_po` | 上涨中向上突破 MA5 的第一根阳线 | `ma5_breakout_flag = True`（昨日 close ≤ prev_ma5，今日 close > ma5）+ v0.2 第 8 章的有效突破规则（`breakout_volume_ratio > 1.2` / `close_position > 0.65` / `real_body_ratio > 0.45`）。 |
| 5 | **追涨点** `zhui_zhang` | 起涨/突破之后上涨途中任一 K 线 | 前置：起涨点或突破点在近 5 根内已确立；当前：`close > MA5` + `MA5_slope_3d > 0`。**默认不推荐**，除非板块极强 or 个股利好，前端用灰色"仅记录"标注。 |

**为什么保留 §4.2-4.5 的三大买点不动**：

三大买点是**指标层**，负责评分和排序，跟评分函数（`compute_trade_timing_score`）耦合紧密，动它会引起评分体系震荡。五买点是**形态识别层**，只影响用户看到的分类标签，不影响分数。两层通过映射表关联：

| 指标层 `buy_point_type` | 五买点形态 `buy_point_pattern` | 何时对应 |
|---|---|---|
| `ma5_pullback` | `hui_cai` | 直接映射（几乎语义一致） |
| `ma5_breakout` | `tu_po` | 直接映射 |
| `ma5_reclaim` | `qi_zhang`（如果谷底之后）or `hui_cai`（如果突破后回踩）| 语义细化——同一 reclaim 落在不同位置时形态不同 |
| `watch` | `chao_di`（如果在谷底 W 形）or `zhui_zhang`（如果在上涨延续）or `none` | 提供可执行的建议，不再一律"仅关注" |

**关键新增字段（`ma5_features`）**：

以下字段需要 `build_ma_features()` 补充输出，供形态识别使用：

| 字段 | 计算 | 用途 |
|---|---|---|
| `ma7` | `close.rolling(7).mean()` | 抄底点判定 |
| `is_local_low_10d` | `low == min(low[-10:])` | 抄底点判定 |
| `macd_dif`、`macd_dea`、`macd_hist` | 标准 MACD (12, 26, 9) | 抄底点 MACD W 形 |
| `macd_w_pattern_flag` | `dif` 从下方转向上 + `dea` 上翘 + `hist` 连续 2 根 > 0 | 抄底点 MACD 确认 |
| `days_since_bullish_engulf` | 距最近一根 `bullish_engulf_flag=True` 的日数 | 起涨点判定 |
| `days_since_breakout` | 距最近一根 `ma5_breakout_flag=True` 的日数 | 追涨点判定 |
| `days_since_pullback_low` | 距最近一根 `pullback_depth` 满足动态区间的日数 | 起涨点上下文（是不是"回踩完毕") |
| `days_since_chao_di` | 距最近一根 `chao_di_flag=True` 的日数 | 起涨点上下文（是不是"抄底之后"） |
| `chao_di_flag` | 抄底点判定合规布尔 | 五买点形态 |
| `qi_zhang_flag` | 起涨点判定合规布尔 | 五买点形态 |
| `hui_cai_flag` | 回踩点判定合规布尔 | 五买点形态 |
| `tu_po_flag` | 突破点判定合规布尔 | 五买点形态 |
| `zhui_zhang_flag` | 追涨点判定合规布尔 | 五买点形态 |

**核心识别函数（伪代码）**：

```python
def detect_buy_point_pattern(features: dict) -> dict:
    """按优先级 突破点 > 起涨点 > 回踩点 > 抄底点 > 追涨点 判定五买点形态。

    优先级按"确认度递减"排序：突破/起涨/回踩都是明确入场点；
    抄底信号弱、追涨最后（因为要落到其他四个之后才成立）。
    """

    # 1. 突破点：直接用 §4.3 已有的判定
    if features["ma5_breakout_flag"] and features["breakout_volume_ratio"] > 1.2 \
        and features["close_position"] > 0.65 and features["real_body_ratio"] > 0.45:
        return {"pattern": "tu_po", "label": "突破点",
                "strength": "strong" if features.get("previous_high_break_flag") else "valid"}

    # 2. 起涨点：阳克阴 + 位置上下文
    if features["bullish_engulf_flag"]:
        # (A) 谷底之后
        if features.get("days_since_chao_di", 999) <= 3:
            return {"pattern": "qi_zhang", "label": "起涨点", "context": "谷底反转"}
        # (B) 回踩 MA5 完毕之后
        if features.get("days_since_pullback_low", 999) <= 3 \
            and features["ma5_reclaim_flag"]:
            return {"pattern": "qi_zhang", "label": "起涨点", "context": "回踩确认"}

    # 3. 回踩点：已突破且当前贴 MA5
    if features.get("days_since_breakout", 999) <= 20 \
        and abs(features["pullback_to_ma5_distance"]) <= 0.02 \
        and features["pullback_volume_ratio"] < 0.9:
        return {"pattern": "hui_cai", "label": "回踩点"}

    # 4. 抄底点：MA5 下方 + 局部谷底 + MA7 确认 + MACD W
    if features["close_ma5_ratio"] < 0 and features.get("is_local_low_10d") \
        and features["close"] > features.get("ma7", 0) \
        and features.get("macd_w_pattern_flag"):
        return {"pattern": "chao_di", "label": "抄底点",
                "note": "实战难捕捉，仅提示"}

    # 5. 追涨点：突破/起涨之后 5 根内
    if (features.get("days_since_breakout", 999) <= 5
        or features.get("days_since_chao_di", 999) <= 8) \
        and features["close_ma5_ratio"] > 0 \
        and features["ma5_slope_3d"] > 0:
        return {"pattern": "zhui_zhang", "label": "追涨点",
                "note": "不推荐，除非板块极强或有重大利好"}

    return {"pattern": "none", "label": "-"}
```

**优先级顺序解释**：

1. **突破点 → 起涨点 → 回踩点**：三个都是明确入场点，按确认度递减排。突破点最硬（当日就完成有效突破）；起涨点次之（依赖前置事件）；回踩点第三（因为可能是"半程回踩" 未完成）。
2. **抄底点最靠后**：文档明确说"理论存在实战难捕捉"，即使 MACD W 确认也可能是弱反弹。放最后避免误导用户高抛低吸。
3. **追涨点必须最后**：语义要求是"其他买点已经确立后的延续"。如果放在前面会把突破点/起涨点也一律标成追涨。

**前端展示口径**：

候选卡评分行现在是：

```
最终 82 · 股票 75 · 买点 88 · 风险 0.85 · [突破有效]
```

加上五买点形态徽章后变成：

```
最终 82 · 股票 75 · 买点 88 · 风险 0.85 · 【突破点】 · [突破有效]
```

五个形态按颜色分档：
- 突破点 → 红（强势入场）
- 起涨点 → 橙红（确认入场）
- 回踩点 → 蓝（波段入场）
- 抄底点 → 灰（谨慎标注）
- 追涨点 → 灰（不推荐但记录）

**跟已有信号的解耦保证**：

- `signal_detector.py` 里现有的 `breakout_confirm` / `pullback_to_ma5` / `gentle_rise` 保留不动——它们是"事件流"（日线时间轴上标注哪一天发生了什么），对应盘面回顾场景。
- `candidates.py` 里的 `buy_point_type` 保留不动——它是评分层。
- 新加 `buy_point_pattern` 字段——它是**用户展示层**，映射自评分层 + 位置上下文。
- 阶段 3 的 §4.5 三大买点合并逻辑不动，`buy_point_type` 依然只有 `{ma5_pullback, ma5_breakout, ma5_reclaim, watch}`。§4.7 是在此之上再算一次 `buy_point_pattern`。

**实施顺序建议**（等阶段 7 Job 完成后回过头做）：

1. **§4.7.1 特征补齐**（1 天）：在 `_prepare_stock_frame` / `build_ma_features` 里加 MA7、MACD、`is_local_low_10d`、四个 `days_since_*` 字段。
2. **§4.7.2 形态识别函数**（1 天）：`detect_buy_point_pattern(features)`，输出 5 挡 + `none`。加单测覆盖每挡的正/反例。
3. **§4.7.3 候选池接入**（0.5 天）：`build_candidates` 里给每张候选卡输出 `buy_point_pattern` 字段。
4. **§4.7.4 前端徽章**（0.5 天）：候选卡评分行加五色徽章，静态资源升版。

**§4.7 验收标准**：

- [ ] 五买点形态识别函数覆盖所有 5 挡 + `none`，每挡至少 3 条单测（正例、反例、边界）
- [ ] 每张候选卡输出 `buy_point_pattern` + `buy_point_pattern_label` + `buy_point_pattern_context`（可选，例如"谷底反转"/"回踩确认"）
- [ ] 前端候选卡评分行显示五色形态徽章
- [ ] 抄底点和追涨点必须显示 `note` 提醒"仅标注不推荐"或"实战不推荐"
- [ ] 单票分析面板（`stock_panel`）也输出 `buy_point_pattern`
- [ ] 优先级顺序（突破 > 起涨 > 回踩 > 抄底 > 追涨）在单测里锁定

**已知歧义与决策**：

- **追涨点跟"散户情绪追涨" 冲突**：现有 `_active_buy_label` 里"散户情绪追涨"是**风险信号**（散户接盘），跟五买点里"追涨点"是**入场信号**（仅限强势），语义不同。前端 label 分开：`buy_point_pattern="zhui_zhang"` 是买点分类，`active_buy_label="散户情绪追涨"` 是资金结构。两者可以同时存在，用户看到就知道"这是追涨点但要小心，散户在接盘"。
- **抄底点的 MA7 与 MACD**：v0.2 战法主线是 MA5，MA7 和 MACD 只在抄底点判定时用。不进入其他评分函数，也不影响 `trade_timing_score`。这样保持 MA5 战法的核心地位。

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
| `breakout_events` | 突破事件追踪（状态机：pending/tracking/settled/expired），见 20 章 Job 2 | ticker + breakout_date | 30 天（expired 之后不再回填） |
| `holdings` | 持仓记录（状态机：active/reduced/closed），见 20 章阶段 7 Job 1 | ticker | 永久（closed 后仍保留，供阶段 8 归档到 trades） |
| `holding_events` | 每日持仓状态 | ticker + date | 永久 |
| `trades` | 完成的交易记录 | trade_id | 永久 |
| `account_state` | 账户状态每日快照 | date | 永久 |
| `signal_stats` | 信号历史统计（回测输出） | signal_code | 更新覆盖 |
| `system_config` | 拟合后的阈值/参数 | config_key | 永久 |

---

## 14. 附录 C：API 端点清单

**新增**：
- `POST /api/v1/data/precompute-sectors`（已有）
- `POST /api/v1/data/precompute-breakout-events`（Job 8，已上线）
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

### 2026-07-06 · MVP-1 修正：主线分改为全板块参考值评分

**完成状态**：已完成。修正“多个趋势板块主线分同时显示 100、20/60 日相对子分为空时仍像有效结果”的误导问题。

**完成内容**：

| PRD 阶段 | 状态 | 修改文件 | 修改内容 |
|---|---|---|---|
| 阶段 6：主线分参考系 | 已完成 | `shilun/market/sector.py` | `sector_mainline_score` 从分位/绝对阈值感评分升级为“全板块中位数/均值参考评分”：每个子分以横截面中位数为 50 分锚点，结合均值、标准差与 MAD 离散度计算相对强弱，避免所有热门板块同时被顶到 100。 |
| 阶段 6：评分证据 | 已完成 | `shilun/market/sector.py` | 新增 `score_references`，返回每个子分的当前值、样本数、中位数、均值、标准差、MAD、标准化强度；证据文案同步说明参考系。 |
| 阶段 6：缓存失效 | 已完成 | `shilun/market/sector.py` | `SECTOR_ENGINE_VERSION` 升级为 `market_sector_v4_ma5_v02_relative_mainline`，强制板块预计算重新生成，避免读取旧缓存。 |
| 趋势板块展示 | 已完成 | `shilun/static/app.js`、`shilun/static/app.css` | 子分 chip hover 展示全板块参考值；若接口返回旧 `market_sector_v1` 或缺少新字段，页面展示旧缓存/旧后台警告，避免空指标继续误导。 |
| 静态资源版本与测试 | 已完成 | `shilun/static/index.html`、`tests/test_ui_route.py`、`tests/test_market_sector.py` | 静态资源版本更新为 `20260706-ma5-v02-sector-relative`；测试新增 v4 引擎和 `score_references` 断言。 |

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

---

### 2026-07-06 · 阶段 4 Job 1：拆开 `ma5_breakout_flag` 与 `ma5_reclaim_flag`

**背景**：在阅读现有实现时发现 `shilun/market/ma5_features.py:213` 处 `ma5_breakout_flag` 直接复用了 `_reclaim_flag()`，与 `ma5_reclaim_flag` 完全同源。这导致 `compute_trade_timing_score()` 里的 `buy_point_type` 分支永远走不到 `ma5_reclaim`（因为一旦 reclaim 成立、breakout 也一定成立，且 breakout 判定分支在前）。这是 v0.2 战法文档本身留下的一个歧义（第 8 章表格里两者的判定公式写法相同），需要在实现层拉开语义。

**判定拆分**：

| Flag | 语义 | 判定条件 |
|---|---|---|
| `ma5_reclaim_flag` | 从下方站回 MA5（假跌破修复） | `prev_close <= prev_ma5` 且 `close > ma5` |
| `ma5_breakout_flag` | 已在 MA5 上方进一步加速走强 | `prev_close > prev_ma5` **且** `close > ma5 * 1.005` **且** `real_body_ratio > 0.35` **且** `close_position > 0.55` |

两者语义严格互斥（`prev_close` 相对 `prev_ma5` 的相对位置决定命中哪一个），保证任何一天最多命中其中一个。突破 flag 的四条约束对应 v0.2 第 8 章"有效 MA5 突破规则"的第 2/4/5 条（第 3 条 `breakout_volume_ratio > 1.2` 在 `compute_ma5_breakout_score()` 里作为独立子项打分，不再做硬门槛，避免因为量能贴阈值就把整个 flag 打成 False）。

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/market/ma5_features.py` | 新增 `_breakout_flag(latest, prev)`（line 102-127）；`build_ma_features()` 里 `ma5_breakout_flag` 从 `_reclaim_flag` 改为 `_breakout_flag`；`compute_trade_timing_score()` 的 `elif breakout["score"] >= 60` 分支加 `features.get("ma5_breakout_flag")` 前置条件，确保 `ma5_reclaim` 分支能被命中 |
| `tests/test_ma5_features.py`（新） | 7 条测试覆盖：`_reclaim_flag` 正例、`_breakout_flag` 正例、`_breakout_flag` 的三条反例（收盘位置低 / 实体薄 / 突破幅度不足）、`compute_trade_timing_score` 在两种 flag 下的 buy_point_type 分支 |

**验证**：
- `pytest tests/test_ma5_features.py` 7 passed
- `pytest tests/test_market_sector.py tests/test_candidate_rules.py` 5 passed（无回归）

**遗留说明**：`ma5_features.py:225` 的 `post_breakout_shrink_ratio = volume_ratio_20`（当日量比伪装成突破后缩量）和 `ma5_features.py:385` 的 `next_day_score = 50`（固定值）两处占位仍在，需要 Job 2 引入 `breakout_events` 集合后才能替换成真实追踪数据。

---

### 2026-07-06 · 阶段 4 Job 2：新建 `breakout_events` 集合与数据层

**背景**：Job 1 拆分了两个 flag，但 `compute_breakout_quality_score()` 里的 `next_day_score = 50` 和 `build_ma_features()` 里 `post_breakout_shrink_ratio = volume_ratio_20` 仍是占位。这两处都需要跨日追踪，必须先落地一张事件表。

**Schema 与状态机**：

| 字段类 | 字段 | 说明 |
|---|---|---|
| 基线 | `ticker` + `breakout_date`（唯一主键） | 突破发生日 |
| 基线 | `breakout_close`、`breakout_ma5`、`breakout_volume`、`breakout_volume_ratio` | 突破当日基线 |
| 基线 | `previous_high_20`、`previous_high_60`、`box_upper` | 突破前的阻力位 |
| 基线 | `close_position`、`real_body_ratio` | 突破日 K 线质量 |
| 追踪 | `post_bars: [{date, n, close, low, high, volume, volume_shrink_ratio, hold_ratio, fell_back_into_box}]` | T+1~T+5 逐日追加 |
| 追踪 | `status ∈ {pending, tracking, settled, expired}` | 状态机 |
| 聚合 | `post_breakout_drawdown_5d`、`post_breakout_shrink_ratio`、`next_day_hold_flag`、`previous_high_hold_ratio`、`fall_back_into_box_flag` | 每次追加 `post_bar` 时重算 |
| 聚合 | `breakout_quality ∈ {valid, pending_confirmation, suspicious, failed}` | 四挡分类 |
| 元 | `created_at`、`updated_at`、`engine_version = "breakout_v1"` | 版本管理 |

**状态机迁移规则**：

```
Job 3 落库 baseline           →  status = "pending" (tracked_days=0)
Job 4 追加第 1 根 post_bar    →  status = "tracking"
Job 4 追加第 5 根 post_bar    →  status = "settled"
Job 4 扫到超 30 自然日未 settle →  status = "expired"
```

只有 `status ∈ {pending, tracking}` 的事件会被 Job 4 每日回填任务扫到；`settled` 与 `expired` 冻结不动。

**`breakout_quality` 分档规则**（Job 5 会直接读这个字段）：

```
failed              fall_back_into_box_flag == True 或 previous_high_hold_ratio < -2%
suspicious          next_day_hold_flag == False 或 previous_high_hold_ratio 在 [-2%, -1%)
pending_confirmation tracked_days < 5 且没有明确坏信号
valid                前高守住 (>= 0) + 缩量 (< 1.0) + 次日守住 + tracked_days == 5
```

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/common/db.py` | `ensure_indexes()` 新增三条：`uniq_breakout_event_scope (ticker, breakout_date)`、`idx_breakout_event_status_date`、`idx_breakout_event_date_desc` |
| `shilun/market/breakout_events.py`（新） | 数据层：`BreakoutBaseline` / `PostBreakoutBar` dataclass；`upsert_breakout_event`（幂等）、`append_post_bar`（含状态迁移与聚合重算）、`classify_breakout_quality`、`get_breakout_event`、`get_latest_breakout_event`、`find_events_needing_backfill`（自动 expire 超龄事件）、`bulk_insert_baselines`；导出常量 `BREAKOUT_ENGINE_VERSION`、`TRACK_DAYS`、`COLLECTION_NAME` |
| `tests/test_breakout_events.py`（新） | 13 条测试，用 in-memory `FakeCollection` 桩覆盖：baseline 首次 upsert / 重跑不清空 post_bars、批量插入计数、append 迁移状态、5 根后 settle、重复 T+n 拒绝、settled 事件拒绝再追加、`classify_breakout_quality` 四种分档、超龄 expire 逻辑 |

**关键设计取舍**：

1. **幂等 upsert**：`upsert_breakout_event()` 检测到已存在事件时只更新基线字段（不动 `post_bars/status/tracked_days/created_at`）。这是为了让 Job 3 可以安全重跑（例如脚本挂掉重启），不会把已经追踪到的 T+n 数据抹掉。
2. **聚合字段在写入时算，不在读取时算**：每次 `append_post_bar()` 重算全部聚合字段。理由是 Job 5 和 Job 6 每天要读几百到几千次，写入侧算好比读取侧临时算更划算，也让 `breakout_quality` 字段本身就是"权威结论"。
3. **`box_upper` 暂时等于 `previous_high_20`**：v0.2 战法文档里的箱体上沿定义偏模糊，MVP 期先用 20 日前高近似；Job 4 完成后再看是否要引入独立的箱体识别算法。
4. **过期阈值 30 自然日**：`find_events_needing_backfill(max_age_days=30)` 会把超过 30 天仍未 settle 的事件标记为 `expired`。这个阈值考虑到节假日 + 停牌，比 T+5 * 5（周一到周五 = 7 自然日）宽松很多。

**验证**：
- `pytest tests/test_breakout_events.py` 13 passed
- `pytest tests/test_ma5_features.py tests/test_market_sector.py tests/test_candidate_rules.py tests/test_analysis_service.py` 14 passed（无回归）

**Job 3 待办**：写一个函数 `detect_daily_breakouts(market_bars, analysis_date)` 遍历全市场，在 `analysis_date` 当天满足 `ma5_breakout_flag=True` 且量能达标的票，构造 `BreakoutBaseline` 批量入库。

---

### 2026-07-06 · 阶段 4 Job 3：突破日检测 + 事件落库

**背景**：Job 2 完成了数据层，但没人往里写。Job 3 负责在每个交易日结束后扫全市场，找出当天新触发的突破，构造 `BreakoutBaseline` 落库。

**判定口径**（比 `ma5_features._breakout_flag` 更严格：多加一条量能硬门槛）：

| 阈值常量 | 值 | 说明 |
|---|---|---|
| `BREAKOUT_MIN_DISTANCE` | 0.005 | `close / ma5 - 1 >= 0.5%` |
| `BREAKOUT_MIN_REAL_BODY_RATIO` | 0.35 | 阳线实体健康 |
| `BREAKOUT_MIN_CLOSE_POSITION` | 0.55 | 收在当日中枢以上 |
| `BREAKOUT_MIN_VOLUME_RATIO` | 1.2 | v0.2 第 8 章"有效 MA5 突破"第 3 条 |
| `BREAKOUT_MIN_HISTORY_BARS` | 25 | 需足够历史算 `previous_high_20` |

**为什么 Job 3 加量能硬门槛、`ma5_features._breakout_flag` 不加**：`breakout_events` 是要长期追踪的持久事件，宁缺毋滥；而 per-bar 特征提取里，量能作为 `compute_ma5_breakout_score()` 的独立子项参与打分，如果量能贴阈值就把整个 flag 打成 False，会让评分失去梯度。两处判定共用前四条约束，量能约束只加在落库侧。

**函数分层**：

```
detect_daily_breakouts(stock_frame, analysis_date) -> list[BreakoutBaseline]   # 纯 pandas 逻辑，无 IO
    ↓ 调用方决定是否落库
bulk_insert_baselines(collection, baselines) -> {inserted, existing}           # 已有，Job 2
    ↑ 上层入口
precompute_breakout_events(store, analysis_date, ...) -> dict                  # 组装：读 Mongo → _prepare_stock_frame → detect → 落库
```

这样纯逻辑层独立可测（走 in-memory DataFrame），上层入口只是薄薄一层 IO 组装，Job 8 的 API 端点直接调 `precompute_breakout_events` 就行。

**关键实现细节**：

1. **只扫"当日就是这只票最新一根"的股票**：`if pd.Timestamp(latest_row["date"]) != target_ts: continue`，避免把停牌票的历史突破日误当成"今天新触发"。
2. **`_prepare_stock_frame` 复用**：`precompute_breakout_events` 直接调 `sector._prepare_stock_frame`，共享 sector 已经算好的 `ma5/volume_ratio_20/close_position/real_body_ratio` 列，不会重复计算。
3. **`box_upper = previous_high_20`**：MVP 期简化，Job 4 完成后再决定要不要独立识别箱体。
4. **保护 dataclass 模块无 pandas 依赖**：`detect_daily_breakouts` 和 `precompute_breakout_events` 里的 `import pandas as pd` 是函数内局部导入，让 `breakout_events` 模块在没有 pandas 的环境（例如单纯需要状态机的场景）依然可以 import。

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/market/breakout_events.py` | 顶部新增 5 个阈值常量；追加 `_previous_high`、`_is_breakout_bar`、`detect_daily_breakouts(stock_frame, analysis_date)`、`precompute_breakout_events(store, analysis_date, ...)` 四个函数 |
| `tests/test_breakout_events.py` | 新增 `DetectDailyBreakoutsTest` 6 条测试：正例、量能不足、prev 破位、历史不足、跳过非当日最新的票、检测+落库联动 |

**验证**：
- `pytest tests/test_breakout_events.py` 19 passed
- `pytest tests/test_ma5_features.py tests/test_market_sector.py tests/test_candidate_rules.py tests/test_analysis_service.py tests/test_ui_route.py` 19 passed（无回归）

**Job 4 待办**：写 `backfill_post_bars(store, up_to_date)` —— 从 Mongo 读所有 `status ∈ {pending, tracking}` 的事件，按 `breakout_date` 后的每根新交易日追加到 `post_bars`，直至 T+5 或 30 日过期。这个函数应该幂等，可以每日多次跑。

---

### 2026-07-07 · 阶段 4 Job 4：T+1~T+5 日线回填

**背景**：Job 3 落库了突破基线，但 `post_bars` 是空的、`breakout_quality` 只能是 `pending_confirmation`。Job 4 负责每日扫所有 pending/tracking 事件，把突破日之后的日线追加进去，直到 T+5 结算或 30 天过期。

**关键实现细节**：

1. **T+n 用交易日序号，不用日期差**：如果按 `date - breakout_date` 算 n，遇到周末/节假日就会跳号。正确做法是：取该 ticker 在 `breakout_date` 之后（严格大于）的所有 Mongo 日线，按日期升序，第一根就是 T+1，第二根 T+2……取前 5 根即为 T+1..T+5。这样节假日不影响 n 的连续性。

2. **批量拉日线，一次 mongo query**：先从事件列表算出 `tickers` 集合与 `earliest_breakout`，然后 `store.raw_market.find_daily_bars(start_date=earliest_breakout, end_date=up_to_date, tickers=[...])` 一次拉全。避免 N 个事件 N 次查询。

3. **幂等**：`append_post_bar` 会在同一 `n` 已存在时返回 False，函数内部也用 `existing_ns` 集合提前过滤。同一天多次跑不会重复写、不会破坏聚合字段。

4. **超龄自动 expire**：`find_events_needing_backfill(max_age_days=30)` 会在扫描时就把超过 30 自然日仍未 settle 的事件置为 `expired`（Job 2 逻辑），Job 4 就自动不处理它们了。所以本函数返回的 `scanned` 数已经排除掉超龄事件。

5. **`settled` 计数在最后重新读一次**：`append_post_bar` 内部会推进 status，但函数级返回需要单独统计"本次 backfill 让多少事件从 tracking 走到 settled"，所以在追加完所有 bar 后 `get_breakout_event` 复查一次。

**函数签名与返回**：

```python
def backfill_post_bars(
    store: Any,
    *,
    up_to_date: str,           # 追赶到哪天为止（含）
    max_age_days: int = 30,    # 突破日距 up_to_date 超过这个天数就 expire
) -> dict[str, Any]:
    # 返回：{up_to_date, scanned, appended, settled}
    #   scanned  - 本次实际处理的事件数（不含 expired）
    #   appended - 本次新追加的 post_bar 总数（跨所有事件）
    #   settled  - 本次从 tracking 迈进 settled 的事件数
```

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/market/breakout_events.py` | 新增 `backfill_post_bars(store, *, up_to_date, max_age_days=30)`（约 70 行），插在 `precompute_breakout_events` 上方 |
| `tests/test_breakout_events.py` | 新增 `FakeRawMarket` / `FakeStore` 桩 + `BackfillPostBarsTest` 7 条测试：一次跑完 pending→settled、幂等、部分回填、T+5 截断、多事件批处理、无日线时保持 pending、超龄事件自动 expire |

**验证**：
- `pytest tests/test_breakout_events.py` 26 passed（7 条新增全绿）
- `pytest tests/test_ma5_features.py tests/test_market_sector.py tests/test_candidate_rules.py tests/test_analysis_service.py tests/test_ui_route.py` 19 passed（无回归）

**Job 5 待办**：`compute_breakout_quality_score()` 在 `ma5_features.py:385` 处的 `next_day_score = 50` 是常量占位，需要改成读 `breakout_events` 集合里的 `breakout_quality` 字段（Job 2 已经算好了 valid/pending/suspicious/failed）。这要求 `compute_breakout_quality_score` 能拿到 store 或者预先注入的事件字典。设计选项在下一 Job 时再谈。

---

### 2026-07-07 · 阶段 4 Job 5：`compute_breakout_quality_score` 接入 `breakout_events`

**背景**：`ma5_features.py` 里的 `compute_breakout_quality_score(features)` 有 4 个子分，其中 3 个原本读 features 层的当日快照字段（`previous_high_hold_ratio` / `fall_back_into_box_flag` / `post_breakout_shrink_ratio`），最后一个 `next_day_score` 直接是常量 50。这四个子分全部都应反映"突破后 5 天的真实追踪表现"，而不是"当日快照"。Job 5 把它们全部改成能从 `breakout_events` 读。

**设计选择：keyword-only 可选参数注入，而不是让评分函数持有 store 句柄**

方案对比：

| 方案 | 优点 | 缺点 |
|---|---|---|
| A. 评分函数直接持 store 句柄 | 调用方省事 | 评分函数变成有状态、难单测，且每次评分都读 Mongo |
| B. 上层调用方查好事件、注入 dict（选定） | 评分函数纯计算、可单测；调用方可批量查一次 Mongo | 调用方多一步组装 |

选 B：`compute_breakout_quality_score(features, *, breakout_event=None)`。`breakout_event=None` 时保留原逻辑，作为"未追踪 / 系统刚上线 / 事件表还没建"时的 fallback，避免破坏没有事件的候选票。

**改造细节**：

| 子分 | 原来 | 现在（有 event 时） |
|---|---|---|
| `hold_score` | 读 `features["previous_high_hold_ratio"]`（当日 `low/前高-1`） | 读 `event["previous_high_hold_ratio"]`（追踪后 `min(low_n)/前高-1`） |
| `box_score` | 读 `features["fall_back_into_box_flag"]`（当日是否跌回） | 读 `event["fall_back_into_box_flag"]`（追踪 5 天内是否跌回） |
| `shrink_score` | 读 `features["post_breakout_shrink_ratio"]`（当日量比） | 读 `event["post_breakout_shrink_ratio"]`（追踪后 5 天均量/突破日量） |
| `next_day_score` | 固定 50 | `next_day_hold_flag=True → 100, False → 0, None → 50` |

输出多两个字段供前端和调试使用：

```python
{
    "score": ...,
    "grade": ...,
    "source": "event" | "features",        # 新增：这次评分是不是接了真实追踪
    "tracked_days": int,                   # 新增：event 里已经追踪几天
    "breakout_quality": "valid" | "pending_confirmation" | "suspicious" | "failed" | None,  # 新增：直接透传 Job 2 分档
    "parts": {...},
}
```

`compute_trade_timing_score(features, *, breakout_event=None)` 也加了同样的 keyword-only 参数，把 event 透传给 `compute_breakout_quality_score`。因为是 keyword-only，`candidates.py:502` 现有的 `compute_trade_timing_score(ma5_features)` 单参数调用完全向后兼容，Job 6 再让 candidates 从 Mongo 查 event 传进来。

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/market/ma5_features.py` | `compute_breakout_quality_score()` 与 `compute_trade_timing_score()` 各加 `breakout_event` keyword-only 参数；四个子分改成事件优先、features 回退；输出多 3 字段 |
| `tests/test_ma5_features.py` | 新增 `BreakoutQualityScoreTest` 5 条测试：无事件时 fallback、有事件时覆盖 features、next_day_hold_flag=False→0、next_day_hold_flag=None→50、`compute_trade_timing_score` 正确透传 event |

**验证**：
- `pytest tests/test_ma5_features.py` 12 passed（5 条新增全绿）
- `pytest tests/test_ma5_features.py tests/test_breakout_events.py tests/test_market_sector.py tests/test_candidate_rules.py tests/test_analysis_service.py tests/test_ui_route.py` 50 passed（无回归）

**遗留说明**：`ma5_features.py:225` 的 `post_breakout_shrink_ratio = volume_ratio_20` 仍是 features 层占位。Job 6 会让 candidates.py 层在拿到 event 后直接把 event 的 `post_breakout_shrink_ratio` 覆写进 features，或者在评分时同时走 event 通道——两条路径必须一致，避免特征层和评分层数据不同步。

**Job 6 待办**：`build_candidates()`（`shilun/market/candidates.py:361`）里，在跑 `compute_trade_timing_score(ma5_features)` 之前，用 `get_latest_breakout_event(collection, ticker, on_or_before=analysis_date)` 查一下最近事件，传给评分函数；同时把 event 里的 `post_breakout_shrink_ratio` 覆写到 `ma5_features`，让特征快照对外展示时也是真实值。

---

### 2026-07-07 · 阶段 4 Job 6：候选池接入 `breakout_events`

**背景**：Job 5 让评分函数支持事件注入，但真正在 `build_candidates` 里跑评分的调用还是单参数（无事件）。同时 `ma5_features` 输出的 `post_breakout_shrink_ratio = volume_ratio_20`（当日量比）和 `previous_high_hold_ratio`（当日 `low/前高`）也仍是"当日快照"，前端看不到追踪后的真实值。Job 6 把 Job 2/4 落库+回填出来的 event 数据打通到候选池评分层和前端快照。

**关键设计**：不让评分函数持 store 句柄，也不让 `build_candidates` 内部逐票发 Mongo query。改成 API 层批量拉一次 lookup，函数层只做纯查表 + 注入。

```
API 层 (shilun/api/__init__.py)                              纯 IO
    ↓ build_latest_events_lookup(collection, on_or_before=date, lookback=30) → {ticker: event}
sector.evaluate_sector_trends(..., breakout_events_lookup=lookup)   透传
    ↓
candidates.build_candidates(..., breakout_events_lookup=lookup)     纯查表 + 注入
    ↓ 每只票：
      event = lookup.get(ticker)
      if event:
          ma5_features[<追踪字段>] = event[<追踪字段>]   # 覆写快照
      trade_timing = compute_trade_timing_score(ma5_features, breakout_event=event)
```

**覆写到 features 的字段**（Job 6 明确的 3 项）：

| 字段 | features 层（覆写前） | event 层（覆写后） |
|---|---|---|
| `previous_high_hold_ratio` | 当日 `low / 前高 - 1` | 追踪后 `min(low_1..low_n) / 前高 - 1` |
| `fall_back_into_box_flag` | 当日 `close < box_upper` | 追踪 5 天内是否至少有一天 `close < box_upper` |
| `post_breakout_shrink_ratio` | 当日 `volume_ratio_20`（占位） | 追踪后 5 天均量 / 突破日量 |

覆写发生在 `build_trade_plan()` 与 `compute_stock_quality_score()` 之前，让整条评分链条都看到真实值，避免"评分接了 event、特征快照还是老数据"的不一致。

**候选卡输出新增字段 `breakout_tracking`**（供 Job 7 前端徽章使用）：

```json
{
  "breakout_date": "2026-06-20",
  "status": "settled" | "tracking" | "pending" | "expired",
  "tracked_days": 5,
  "breakout_quality": "valid" | "pending_confirmation" | "suspicious" | "failed",
  "next_day_hold_flag": true,
  "previous_high_hold_ratio": 0.008,
  "post_breakout_shrink_ratio": 0.7,
  "fall_back_into_box_flag": false
}
```

无事件时 `breakout_tracking = None`（前端徽章直接不显示）。

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/market/breakout_events.py` | 顶部 `datetime` import 补 `timedelta`；新增 `build_latest_events_lookup(collection, tickers=None, on_or_before=None, lookback_days=30)`：一次 Mongo query 拉出多只票各自最近一条事件，按 `breakout_date` 倒序遍历，同一 ticker 只保留最新一条 |
| `shilun/market/candidates.py` | `build_candidates()` 加 keyword-only 参数 `breakout_events_lookup`；跑评分前把 event 的三个追踪字段覆写到 `ma5_features`；`compute_trade_timing_score(ma5_features, breakout_event=event)`；候选卡 dict 加 `breakout_tracking` 字段；`ma5_feature_snapshot` 补出三个追踪字段方便前端查看 |
| `shilun/market/sector.py` | `evaluate_sector_trends()` 加 `breakout_events_lookup` 参数并透传给 `build_candidates()` |
| `shilun/api/__init__.py` | `_compute_sector_trends_full()` 在调 `evaluate_sector_trends` 前调用新增的 `_load_breakout_events_lookup(store, target_date)`（内部封装 `build_latest_events_lookup`，集合不存在时静默返回 `{}`） |
| `tests/test_breakout_events.py` | `FakeCollection._match` 补 `$gte / $gt` 支持；新增 `BuildLatestEventsLookupTest` 3 条测试：返回最新一条、超期过滤、ticker 过滤 |
| `tests/test_market_sector.py` | `test_sector_trends_support_lightweight_initial_response` 增加"默认无 lookup 时 `breakout_tracking=None` 且评分 `source="features"`"断言；新增 `test_sector_trends_injects_breakout_events_lookup` 端到端验证注入路径 |

**关键设计取舍**：

1. **API 层批量查、函数层无 IO**：`_load_breakout_events_lookup` 用 `try/except` 包住 Mongo 调用，集合不存在或读失败静默返回 `{}`——这样 Job 3/4 还没跑过、事件表还空的时候 candidates 完全兼容，返回的候选池行为跟改造前一致（评分层走 features fallback）。
2. **`ma5_features` 覆写而不是新增 `_from_event` 字段**：如果覆写而不是新增，`compute_stock_quality_score` / `compute_ma5_pullback_score` / `build_trade_plan` 等所有下游函数都自动看到真实值，不需要一处处改。
3. **候选卡加独立 `breakout_tracking` 字段**：前端不用去 `score_breakdown` 深处翻，Job 7 加徽章直接读顶层这个字段即可。
4. **lookup 用 `lookback_days=30`**：与 Job 2 的 `max_age_days` 保持一致，超期事件本来就是 `expired`，也不该覆写候选票的评分。

**验证**：
- `pytest tests/test_breakout_events.py` 29 passed（3 条新增全绿）
- `pytest tests/test_market_sector.py` 5 passed（1 条新增 + 现有测试补断言全绿）
- `pytest tests/test_ma5_features.py tests/test_breakout_events.py tests/test_market_sector.py tests/test_candidate_rules.py tests/test_analysis_service.py tests/test_ui_route.py` 54 passed（无回归；`tests/test_market_part1.py::test_attack_permission_...` 单独在本次修改前就已存在的失败，与 Job 6 无关）

**Job 7 待办**：前端候选池卡片：读 `candidate.breakout_tracking`，加一个色块徽章（`valid=绿`、`pending_confirmation=灰`、`suspicious=橙`、`failed=红`），hover 展示 `tracked_days / next_day_hold_flag / previous_high_hold_ratio / post_breakout_shrink_ratio` 明细。同时 `index.html` 静态资源版本号要升级避免浏览器缓存。

---

### 2026-07-07 · 阶段 4 Job 7：前端真假突破徽章

**背景**：Job 6 已经把 `breakout_tracking` 顶层字段挂到候选卡上，但前端还没渲染。用户看不到"这只票的突破是不是真的稳住了"。Job 7 加一颗色块徽章。

**渲染位置**：候选卡评分行末尾，紧跟"风险系数"。选这里因为：

- `breakout_quality` 语义上是评分类信息（"这次突破算多有效"），跟旁边的 `最终 / 股票 / 买点 / 风险系数` 是同一维度
- 不占独立行，紧凑模式下也不额外挤空间
- 只有在有事件时才出现，没有事件时评分行行长完全不变，视觉稳定

**四挡颜色与文案**：

| breakout_quality | 徽章文案 | 颜色（背景 / 文字） |
|---|---|---|
| `valid` | 突破有效 | 绿（`rgba(46,204,113,0.18)` / `#10b981`） |
| `pending_confirmation` | 突破待确认 | 灰（`rgba(157,152,143,0.14)` / `var(--muted)`） |
| `suspicious` | 突破可疑 | 橙（`rgba(255,160,80,0.18)` / `#f5a623`） |
| `failed` | 突破失败 | 红（`rgba(232,67,67,0.18)` / `var(--color-up)`） |

（红对应"失败"、绿对应"有效"，与 A 股 UI 上"绿跌红涨"的口径无关——这里是通用状态色。）

**Hover tooltip**（用 `title` 原生实现，避免额外弹层组件）：

```
突破日 2026-06-20 · T+5 · 前高守 0.80% · 缩量比 0.70 · 次日 守 · 未跌回 · 状态 settled
```

包含 7 个字段：突破日、追踪进度、前高守 %、缩量比、次日守/破/-、是否跌回箱体、当前状态。

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/static/app.js` | `renderCandidates()` 里新增局部函数 `breakoutQualityBadge(tracking)`，接受 `candidate.breakout_tracking`；候选卡评分行末尾拼接徽章 HTML |
| `shilun/static/app.css` | 新增 5 条样式：`.breakout-badge` 基础样式 + 四挡颜色 `.breakout-valid` / `.breakout-pending_confirmation` / `.breakout-suspicious` / `.breakout-failed` |
| `shilun/static/index.html` | 静态资源版本 `20260706-ma5-v02-sector-relative` → `20260707-ma5-v02-breakout-tracking`（CSS + JS 两处） |
| `tests/test_ui_route.py` | 更新版本号断言 |

**关键设计取舍**：

1. **`title` 原生 tooltip 而不是 hover 弹层**：不引入新的 JS 组件，PC 端就能用；移动端长按也能触发。够用不折腾。
2. **CSS class 名直接拼 `breakout_quality` 值**：`breakout-${grade}`，字段和类名一一对应，将来后端 `breakout_quality` 枚举扩展时前端只加 CSS 就行，不用改 JS 逻辑。
3. **`escapeHtml(tooltipParts.join(" · "))`**：拼 tooltip 之前统一 escape，避免用户数据里出现 `<` 之类字符污染 DOM。
4. **有 `tracking` 但 `breakout_quality` 空时返回空串**：保守起见，只信任事件里明确写了 `breakout_quality` 分档的情况才展示徽章。刚落库还没算过分档的 pending 事件不显示徽章。

**验证**：
- `node --check shilun/static/app.js`：语法 OK
- `pytest tests/test_ui_route.py tests/test_ma5_features.py tests/test_breakout_events.py tests/test_market_sector.py tests/test_candidate_rules.py tests/test_analysis_service.py` 54 passed（无回归）

**Job 8 待办**：加 `POST /api/v1/data/precompute-breakout-events` 端点，方便日终手动触发 Job 3 检测和 Job 4 回填。返回 `{detected, inserted, existing, appended, settled}`，供运维日志留档。同时补一条 `/api/v1/data/status` 里的 `breakout_events` 集合健康度指标。

---

### 2026-07-07 · 阶段 4 Job 8：API 触发端点 + 集合健康度

**背景**：Job 3/4 已经有独立的 Python 函数 `precompute_breakout_events` / `backfill_post_bars`，但没有 HTTP 入口，日终必须写脚本才能触发。同时 `/api/v1/data/status` 只报了 Tushare / akshare 相关的原始数据集，PART3 是否有可用的 `breakout_events` 表在页面上看不见。Job 8 把这两个洞补上，收尾整个阶段 4。

**新端点：`POST /api/v1/data/precompute-breakout-events`**

Query 参数（都可选，均有合理默认值）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `date` | 今天（避周末） | 分析日 |
| `lookback_days` | 90 | 检测时向前拉多少个自然日日线（够算 `previous_high_60`） |
| `max_age_days` | 30 | 回填时事件超龄阈值，超过就 `expired` 并跳过 |
| `exclude_st` | true | 是否剔除 ST |
| `skip_detect` | false | 只跑回填，不检测新突破（快速补 T+n 用） |
| `skip_backfill` | false | 只跑检测，不做回填（突破日当晚，T+1 还没数据） |

工作流：

```
1. precompute_breakout_events(store, date, ...)   → Job 3 检测 + 落库
   ↓ 返回 {detected, inserted, existing, ...}
2. backfill_post_bars(store, up_to_date=date, ...) → Job 4 回填 T+1..T+5
   ↓ 返回 {scanned, appended, settled}
3. HTTP 200: {status, detect_time_seconds, backfill_time_seconds, detect, backfill, message}
```

`message` 字段合成一句人类可读日志，例如：

```
检测 12 条（新增 5，已存在 7） · 回填扫描 34 条，追加 42 根 T+n，本次 6 条 settled
```

方便运维直接把 message 贴到日志系统里做检索。

**扩展 `/api/v1/data/status` 的数据集清单**

新增一条 dataset 项：

```json
{
  "key": "breakout_events",
  "label": "突破事件追踪（Job 3/4 输出）",
  "tier": "enhanced",
  "source": "shilun.market.breakout_events",
  "value": <total>,
  "ok": <total > 0 且有 latest_breakout_date>,
  "detail": "共 N 条（settled S · 追踪中 T），最新突破日 YYYY-MM-DD",
  "impact_on_miss": "PART3 候选池 breakout_quality 徽章缺失，评分退化为 features 层 fallback",
  "extras": {
    "settled": <settled_count>,
    "tracking": <pending_or_tracking_count>,
    "latest_breakout_date": "YYYY-MM-DD"
  }
}
```

`tier = enhanced` 有意选的：事件表缺失不会阻断 PART3 主流程（Job 5 的 features fallback 兜底），所以它归到"缺失后功能降级"档，而不是"blocked"。

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/api/__init__.py` | 新增 `@ui_router.post("/api/v1/data/precompute-breakout-events")` 端点函数 `precompute_breakout_events_endpoint`（约 80 行），内部按顺序调 `precompute_breakout_events` + `backfill_post_bars`，支持 `skip_detect / skip_backfill` 分支；`data_status()` 里加 `breakout_events_col.count_documents` 三条统计 + 一条 dataset entry |
| `tests/test_breakout_events_api.py`（新） | 4 条集成测试：`skip_both`、`skip_detect` 仍跑回填、Mongo 未配置返回 400、事件表全空时 `message` 说明；`FakeStore` 打桩不需要真 Mongo |
| `docs/ma5_v0.2_改造PRD.md` | 附录 C 补 `POST /api/v1/data/precompute-breakout-events`；Section 20 追加本 Job 记录 |

**关键设计取舍**：

1. **检测在前、回填在后，一次 HTTP 请求跑完两阶段**：日终只需要一次调用。运维不用记两个端点，也避免检测/回填之间时间差导致的时序 bug。
2. **`skip_detect` / `skip_backfill` 双开关**：给两种真实场景各留一个入口——白天想快速补 T+n 时 `skip_detect=true`；突破日当晚想立刻检测但当日还没结算日线时 `skip_backfill=true`。默认两个都跑，最常用。
3. **两阶段独立计时和返回**：`detect_time_seconds` / `backfill_time_seconds` 分开，方便定位到底是哪一阶段慢。
4. **`data_status` 里 breakout_events 归为 enhanced**：事件表缺失只导致 `breakout_quality` 徽章消失、评分降级为 features 层 fallback，不阻断候选池主流程——归 `blocked` 会误导用户以为系统不能用。
5. **端点函数不写缓存**：Job 3/4 的落库本身就是"权威结果"，不需要 sector_trends_cache 那种额外的 payload 缓存。

**验证**：
- `pytest tests/test_breakout_events_api.py` 4 passed
- `pytest tests/test_ma5_features.py tests/test_breakout_events.py tests/test_breakout_events_api.py tests/test_market_sector.py tests/test_candidate_rules.py tests/test_analysis_service.py tests/test_ui_route.py` 58 passed（无回归）
- `ui_router.routes` 里能查到 `/api/v1/data/precompute-breakout-events` 路径注册成功

**阶段 4 收尾**：Job 1-8 全部完成。整条链路打通：

```
每日交易日结束：
    POST /api/v1/data/precompute-breakout-events?date=YYYY-MM-DD
        ↓ Job 3：detect_daily_breakouts()  → breakout_events 落库
        ↓ Job 4：backfill_post_bars()      → 回填 T+1..T+5
        ↓ Job 2：状态推进 pending → tracking → settled
        ↓ classify_breakout_quality()      → valid/pending/suspicious/failed
    ↓
    GET /api/v1/market/sectors?date=...
        ↓ Job 6：_load_breakout_events_lookup(store, date) → {ticker: event}
        ↓ build_candidates(..., breakout_events_lookup)
        ↓ Job 5：compute_breakout_quality_score(features, breakout_event=event)
        ↓ 每个候选卡输出 breakout_tracking 字段
    ↓
    前端候选池 Tab：
        ↓ Job 7：candidate.breakout_tracking → 色块徽章 + tooltip
```

Job 1 拆开的 flag 保证突破/站回分辨清；Job 2-4 让追踪数据落库；Job 5-6 让评分接真实数据；Job 7 让用户能看见；Job 8 提供触发和监控入口。三处占位（`ma5_breakout_flag = _reclaim_flag` / `post_breakout_shrink_ratio = volume_ratio_20` / `next_day_score = 50`）全部被真实追踪数据替换。

**下一阶段建议**（PRD 里未做的）：
- 阶段 3 补齐：三大买点 6 条规则明细化（`buy_point_type` 从 MVP 分类升到详细命中规则）
- 阶段 5：前端量价 8 个 chip 标签
- 阶段 7（★关键缺口）：卖出信号 + 持仓管理，让系统从"选股"跨到"交易闭环"
- 阶段 8：账户破窗
- 阶段 10：回测框架

---

### 2026-07-08 · 买点口径统一到 PRD 阶段 9 · Bug 修复

**背景**：前端候选池 `603986.SH`（兆易创新，当前 close=603 元）显示"推荐买点 243.90 元"，明显不合理。

**根因分析**：

系统里存在**两套买点计算逻辑**，语义严重错位：

| 来源 | 字段 | 语义 | PRD 状态 |
|---|---|---|---|
| `_build_trading_levels()` | `predicted_buy_price` | 按 signal 类型给出的"信号命中参考价"（老逻辑） | ❌ PRD 里没提到，是遗留代码 |
| `build_trade_plan()` | `trade_plan.entry_price` | v0.2 阶段 9 规定 = **当前 close** | ✅ 权威口径 |

老代码里 `_build_trading_levels` 的支撑候选池包含"10日低点"，`watch` 分支 `buy_price = support_price` 直接取历史低点；再加上 `_enrich_intraday_candidate_plans` 的 `_nearest_below` fallback 无差别接受所有 `>0` 的候选，让**旧 sector_trends_cache 缓存里的 243 元级支撑（那时兆易还在 240 元区间）**在当前 close=603 时被选出。

同时 PRD 里明明写好了权威买点口径，但前端 `candidateDisplayPlan` 优先读的是 `predicted_buy_price`，把权威的 `trade_plan.entry_price` 埋在了深处。

**修复方案（对齐 PRD 阶段 9）**：

不再单独设计买点。让候选池对外暴露的所有买点/支撑/止损/目标价字段**全部指向 `trade_plan`**：

| 字段 | 修复前来源 | 修复后来源 |
|---|---|---|
| `entry_price` | `trading_levels["predicted_buy_price"]`（信号价） | `trade_plan["entry_price"]`（= close） |
| `predicted_buy_price` | 同上 | `trade_plan["entry_price"]` |
| `support_price` | `trading_levels["support_price"]` | `trade_plan["support_1"]`（= MA5）+ 老逻辑兜底 |
| `stop_loss` | 老逻辑 `support * 0.98` | `trade_plan["stop_loss_1"]`（= MA5*0.98）+ 老逻辑兜底 |
| `expected_sell_price` | `trading_levels["expected_sell_price"]` | `trade_plan["target_price"]` + 老逻辑兜底 |
| `risk_reward_ratio` | 老逻辑算 | `trade_plan["reward_risk_ratio"]` + 老逻辑兜底 |

`_build_trading_levels()` 保留，但语义降级为"信号命中参考价"，只服务：
1. 支撑/压力兜底（当 trade_plan 没有对应字段时）
2. 老 API 消费方（盘中监控）向后兼容

`buy_label` 从"回踩确认买点"改为"回踩确认信号价"，避免继续误导前端把它当作"推荐买点"。

**盘中监控（`_enrich_intraday_candidate_plans`）的额外硬化**：

即使权威买点走 trade_plan，盘中监控仍会消费老候选缓存里的 `support_price` / `pressure_price` / `predicted_buy_price` 字段。这些老缓存字段可能是几周前不同价格区间时算的老数据，直接用会产生 close=603 时误取 support=243 的荒谬结果。加两条硬化：

1. `_sanity_price(value, close, tolerance=0.15)`：老缓存价格偏离当前 close 超过 15% 视为陈旧，返回 None。用户口径：±15% 严格。
2. `_nearest_below / _nearest_above` 的 fallback 收紧：不再无差别接受所有 `>0` 的候选，找不到符合条件的支撑/压力就返回 None，让上游走 MA5 / close*1.08 兜底。
3. 支撑候选池加 MA10、MA20 作为均线兜底（原来只有 MA5、MA8），完全符合 PRD 阶段 9 的支撑分层。20 日低点从候选池移除。

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/market/candidates.py` | `_build_trading_levels` 支撑候选池移除"10日低点"，`buy_label` 全部改为"信号价"；`build_candidates` 里候选卡的 6 个价位字段全部改从 `trade_plan` 取，`trading_levels` 降为兜底 |
| `shilun/api/__init__.py` | 新增 `_sanity_price(value, close, tolerance=0.15)`；`_nearest_below / _nearest_above` 的 fallback 改为返回 None；`_enrich_intraday_candidate_plans` 的 support/pressure/buy 用 `_sanity_price` 过老缓存，支撑候选加 MA10/MA20 |
| `tests/test_trading_levels.py`（新） | 9 条测试：`_build_trading_levels` 不再取 20 日低点、`build_trade_plan.entry_price` 权威口径（= close）、`support_1/2/3` 分层、`_sanity_price` 边界（含 243/603 真实 bug 场景）、`_nearest_below` fallback 收紧 |

**验证**：

- `pytest tests/test_trading_levels.py` 9 passed
- `pytest tests/test_trading_levels.py tests/test_ma5_features.py tests/test_breakout_events.py tests/test_breakout_events_api.py tests/test_market_sector.py tests/test_candidate_rules.py tests/test_analysis_service.py tests/test_ui_route.py` 67 passed（无回归）

**遗留和后续**：

- 前端候选卡 / 单票分析页 / 盘中监控现在都直接从 `trade_plan.entry_price` 拿权威买点，不再有第二个入口，出错概率显著降低。
- `_build_trading_levels` 的语义降级只是过渡态，等阶段 9 完全跑通、`trade_plan` 所有字段都稳定后可以彻底删除这个函数。
- 单票分析面板 `stock_panel` 也需要检查一遍是否引用了老 `predicted_buy_price` 字段——目前 `_enrich_intraday_candidate_plans` 会处理老缓存，但 `stock_panel` 主路径应该直接读 `trade_plan`，这块留到阶段 7 集成时一并做。

---

### 2026-07-08 · 阶段 7 Job 1：`holdings` 集合与数据层

**背景**：阶段 4 完成了从"选股"到"追踪突破"的链路，但用户选出票之后系统还不知道他是否买了、买了多少、什么时候平的。要跨到"交易闭环"（PRD 阶段 7 的目标），首先要有一张持仓表来承载状态。Job 1 只做数据模型层（dataclass + CRUD + 索引 + 单测），Job 2 会加 REST API。

**Schema（对应 PRD 8.2）**：

| 字段类 | 字段 | 说明 |
|---|---|---|
| 必填 | `ticker`（唯一主键） / `entry_date` / `entry_price` / `entry_signal` / `entry_size` | 入场基本信息，用户手动录入或前端从候选池一键带入 |
| 名称 | `name` / `sector_name` | 展示用，缺失时前端从 stock_basic 补齐 |
| 决策价位 | `target_price` / `stop_loss_1` / `stop_loss_2` / `breakdown_level` / `add_point` / `signal_candle_open` | 对应 v0.2 战法 11 章的止损/止盈体系。默认可空，Job 5 的 daily-check 会从 `trade_plan` 兜底 |
| 失效条件 | `invalid_conditions` | 文案数组，用户可读 |
| 生命周期 | `status ∈ {active, reduced, closed}` / `realized_size` / `realized_pnl` / `exit_date` / `exit_price` / `close_reason` | 状态机，见下方 |
| 元 | `engine_version = "holdings_v1"` / `created_at` / `updated_at` / `note` | 版本、审计 |

`signal_candle_open` 字段是 PRD 8.4 判定函数里出现的 `holding["invalid_price"]`——语义是"信号 K 线的开盘价"，对应 v0.2 的 `signal_candle_open_lost` 卖出信号。PRD 8.2 里没显式列，Job 1 补上。

**状态机**：

```
        active   ── 常态。daily-check 每日扫描并写 holding_events
           │
           ├──> reduced（减仓）：realized_size ∈ (0, entry_size)
           │        weaken_reduce 状态触发后用户执行了部分平仓；
           │        剩余仓位继续跟踪
           │
           └──> closed（清仓）：realized_size == entry_size
                    invalid_exit 触发用户完全平仓，或用户主动结束
                    此后 daily-check 不再扫描
```

**单向状态迁移**：`active → reduced → closed`。回退需要新建持仓（重新开仓时按 upsert 覆盖老 closed 记录）。

**CRUD 接口**（`shilun/market/holdings.py`）：

| 函数 | 语义 |
|---|---|
| `upsert_holding(collection, holding)` | 首次开仓 or 已 closed 后重新开仓；同 ticker 存在 active/reduced 时抛错 |
| `get_holding(collection, ticker)` | 按 ticker 查最新一条 |
| `list_holdings(collection, statuses=None)` | 默认返回 active + reduced；显式指定 `["closed"]` 才拿平仓记录 |
| `patch_holding(collection, ticker, updates)` | 仅允许改价位/仓位/note；改 `realized_size` 自动推进 status；不允许直接改 `status/entry_*` |
| `close_holding(collection, ticker, exit_price=None, reason=None)` | 平仓；自动算 `realized_pnl = (exit_price - entry_price) / entry_price`；已 closed 时幂等返回 |
| `delete_holding(collection, ticker)` | 物理删除，仅供录入错误时使用；正常关闭走 `close_holding` |

**关键设计取舍**：

1. **`upsert_holding` 对已存在 active/reduced 抛错，而不是覆盖**：防止用户手滑重新调 API 覆盖了跟踪中的止损设置。要重新开仓必须先 `close_holding`。
2. **`patch_holding` 不允许改 `status` 和 `entry_*`**：状态机严格通过 `realized_size` 或 `close_holding` 驱动，避免绕过。改 `entry_price` 会破坏 `realized_pnl` 的可解释性——想要改必须删除重建。
3. **`realized_size` 达到 `entry_size` 时自动闭仓**：用户可以只调 `patch_holding({realized_size: entry_size})`，不用记两个 API。
4. **`close_holding` 幂等**：同一持仓多次调用不会覆盖已有 `exit_price` 或改变 `realized_pnl`。这个语义跟阶段 4 `append_post_bar` 的重复拒绝一致，避免定时任务/API 重复调导致数据错乱。
5. **`ticker` 唯一索引 + 老 closed 记录允许被新开仓覆盖**：单表内一只票只保留最新一条。阶段 8 会引入 `trades` 集合，负责在 close 时归档老记录；Job 1 不做归档，只暴露接口。

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/market/holdings.py`（新，约 240 行） | `HoldingRecord` dataclass + `HoldingsError` + `HOLDING_STATUSES` 常量 + 6 个 CRUD 函数 + 状态机自动推进逻辑 |
| `shilun/common/db.py` | `ensure_indexes()` 新增 2 条：`uniq_holdings_ticker(ticker)` + `idx_holdings_status_entry_date(status, entry_date DESC)` |
| `tests/test_holdings.py`（新） | 24 条测试：upsert 首次/重复/重开/非法 status/全字段 roundtrip、list 默认过滤/显式筛/排序、patch 单字段/禁改 status/禁改 entry_price/realized_size 三种边界（部分/全部/负数/超限）/closed 不可 patch/空 update、close 计算 PNL/幂等/不存在报错/不填 exit_price 也能关、delete 存在/不存在、常量集合锁定 |

**验证**：
- `pytest tests/test_holdings.py` 24 passed
- `pytest tests/test_holdings.py tests/test_trading_levels.py tests/test_ma5_features.py tests/test_breakout_events.py tests/test_breakout_events_api.py tests/test_market_sector.py tests/test_candidate_rules.py tests/test_analysis_service.py tests/test_ui_route.py` 91 passed（无回归）
- Import smoke test：`HoldingRecord`、`HoldingsError`、`HOLDING_STATUSES` 等全部符号可正常导入

**Job 2 待办**：暴露 REST API：
- `POST /api/v1/holdings`（新建）
- `GET /api/v1/holdings`（列表）
- `GET /api/v1/holdings/{ticker}`（详情）
- `PATCH /api/v1/holdings/{ticker}`（改价位/仓位）
- `DELETE /api/v1/holdings/{ticker}`（关闭；带 `?force=true` 时物理删除）

映射规则：`HoldingsError` → HTTP 400；`ticker` 不存在 → 404；成功 → 200 + 完整持仓 dict。同时暴露一个 `POST /api/v1/holdings/{ticker}/close` 专门走 `close_holding`，与 DELETE 分开，避免语义混淆。

---

### 2026-07-08 · 买点 bug 修复补丁 · `stock_panel` 路径

**背景**：候选池路径已经改成走 `trade_plan.entry_price`，但用户在**单票分析 Tab** 打开 `002384.SZ（东山精密）` 发现"买点 126.11 / 支撑MA 兜底"，而当前价 237.56。追踪发现 `stock_panel` 端点自己算 levels 时也犯了同样的错：`recent_low = float(lows.tail(60).min())` 取 60 日历史最低当作 `levels.support`，前端 `stockStrategySnapshot` 里 `firstFinitePositive(lv.support, lv.ma5, rt.current)` 把它当买点显示。东山精密 60 日最低 126 元区间正好来源于此。

**根因**：`stock_panel` 是候选池之外的独立路径，之前的修复只改了 `candidates.py` + `_enrich_intraday_candidate_plans`，`stock_panel` 里的 `levels` 计算和前端 `stockStrategySnapshot` 都还是老口径。上次 PRD 里也确实标注了"stock_panel 主路径应该直接读 trade_plan，这块留到阶段 7 集成时一并做"——现在必须提前修，不能拖到 Job 5。

**修复方案（严格对齐 PRD 阶段 9）**：

后端 `stock_panel`：
1. `levels.support` 从"60 日最低"改为**均线支撑**：MA5/MA10/MA20 里 close 之下最近的一层。全在 close 上方（弱势反弹）时退回 MA5。同时输出 `levels.support_source` 供前端展示"MA5/MA10/MA20"。
2. `levels` 里新增 `entry_price / ma10 / ma20` 三字段。
3. **直接在 stock_panel 内部调 `build_trade_plan(features)` 生成完整 trade_plan**，作为顶层字段返回。features 从当日 close/open/ma5/ma8/ma10/ma20/previous_high_60/median_abs_return_20d 组装。

前端 `stockStrategySnapshot`：
1. 买点：`firstFinitePositive(tp.entry_price, lv.entry_price, rt.current, lv.ma5)`——权威值 = trade_plan.entry_price，绝不用 lv.support 兜底。
2. 目标：`tp.target_price` 优先，兜底 `lv.resistance`。
3. 止损：`tp.stop_loss_1` 优先（= MA5*0.98），兜底 `lv.stop_loss_long`。
4. 盈亏比：`tp.reward_risk_ratio` 优先。

前端 `renderStockStrategyCard`：小字副标题从"支撑/MA 兜底"改为"交易计划入场价"，从"压力位优先"改为"60日前高/波动率"，从"跌破则退出"改为"MA5 × 0.98"——让用户看到的口径与后端算法一致。

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/api/__init__.py` | `stock_panel` 里 `levels` 支撑口径改用均线；新增 `trade_plan` 顶层字段（`build_trade_plan(features)` 生成）；`levels` 补 `support_source`/`entry_price`/`ma10`/`ma20` 字段 |
| `shilun/static/app.js` | `stockStrategySnapshot` 买点/目标/止损/盈亏比全部改从 `data.trade_plan` 取，兼容 `levels.entry_price` fallback；`renderStockStrategyCard` 三条副标题文案更新 |
| `shilun/static/index.html` | 静态资源版本 `20260707-monitor-chart-fix` → `20260708-authoritative-buy-price` |
| `tests/test_ui_route.py` | 版本号断言同步 |

**验证**：
- 用东山精密真实数据（close=237.56, MA5=240）调 `build_trade_plan`：
  - `entry_price = 237.56` ✅
  - `support_1 = 240.0`（MA5）✅
  - `stop_loss_1 = 235.2`（MA5*0.98）✅
  - `target_price = 260.0`（60日前高）✅
- `pytest tests/test_ui_route.py tests/test_holdings.py tests/test_trading_levels.py tests/test_ma5_features.py tests/test_breakout_events.py tests/test_breakout_events_api.py tests/test_market_sector.py tests/test_candidate_rules.py tests/test_analysis_service.py` 91 passed（无回归）
- `node --check app.js`：语法 OK

**验收口径统一**：**候选池 candidate.trade_plan.entry_price = 单票分析 stock_panel.trade_plan.entry_price = 当前 close**。任何一处再出现"买点显著低于 close"必定说明有第三条路径没接入，是新 bug。

---

### 2026-07-08 · §4.7 五买点形态体系设计（仅设计、未实施）

**背景**：用户提出按 MA5 战法传统的五买点体系（抄底/起涨/回踩/突破/追涨）识别买点。这跟 PRD §4.2-4.5 已经定义的三大买点（回踩/突破/假跌破站回）不完全对齐——三大买点是**指标层**（评分用），五买点是**形态识别层**（用户表达用）。经检查现有代码，`signal_detector.py` 里的 `breakout_confirm` / `pullback_to_ma5` / `gentle_rise` / `engulf_bullish` 覆盖了突破点、回踩点和起涨点的元素，但**抄底点和追涨点完全缺失**，且没有一个统一的形态分类字段。

**决策**：不改动 §4.2-4.5（三大买点指标层），新增 §4.7 描述五买点形态体系。两层通过映射表关联：
- `buy_point_type`（指标层） → 评分和排序
- `buy_point_pattern`（形态层） → 用户看到的分类标签 + 徽章

**§4.7 已写入 PRD**，包含：
1. 五买点定义 + 每个买点的形态判定条件（`ma5_features` 需补齐 MA7、MACD、四个 `days_since_*` 等 11 个新字段）
2. `detect_buy_point_pattern(features)` 伪代码，按优先级 突破 > 起涨 > 回踩 > 抄底 > 追涨 判定
3. 前端展示口径（候选卡评分行加五色徽章）
4. 与 §4.2-4.5 三大买点的解耦保证（映射表 + 指标层不动）
5. 实施顺序建议（4 个小 Job，2-3 天）
6. 已知歧义与决策（追涨点 vs 散户情绪追涨、MA7/MACD 只服务抄底点）

**本次不实施**：按用户要求先做方案设计，等 PRD 评审通过后再排期实施。放在阶段 7（持仓管理）完成之后回过头补，避免打断当前的关键路径。

**与现有代码的映射记录**（供后续实施时参考）：

| 现有代码 | 语义 | 是否覆盖五买点 |
|---|---|---|
| `candidates.detect_ma5_signal` 里 `breakout_confirm` | 突破确认 | ✅ 覆盖突破点 |
| `candidates.detect_ma5_signal` 里 `pullback_to_ma5` | 回踩 MA5 | ✅ 覆盖回踩点 |
| `candidates.detect_ma5_signal` 里 `gentle_rise` | 缩量上涨 | 部分覆盖追涨点（但语义偏保守） |
| `ma5_features._bullish_engulf_flag` | 阳克阴 | ✅ 起涨点核心元素 |
| `ma5_features._reclaim_flag` | MA5 假跌破站回 | 部分覆盖起涨点（回踩确认场景） |
| `signal_detector.engulf_bullish` | 阳线吞噬（K 线时间轴事件） | ✅ 起涨点元素 |
| — | — | ❌ 抄底点：MA7、MACD 完全缺失 |
| — | — | ❌ 追涨点：`days_since_breakout` / `days_since_chao_di` 上下文缺失 |

---

### 2026-07-09 · §4.7 五买点体系 · Job A：特征层补齐 10 个新字段

**背景**：§4.7 五买点体系里，抄底点/起涨点/追涨点识别需要的特征字段现有代码完全没有。Job A 只做特征层补齐，不动识别函数（Job B）和候选池接入（Job C）。

**新增字段清单（10 个，第 11 项 `chao_di_flag` 等 5 个形态 flag 由 Job B 输出）**：

| 字段 | 层级 | 计算 | 服务对象 |
|---|---|---|---|
| `ma7` | `_prepare_stock_frame` | `close.rolling(7).mean()` | 抄底点确认 |
| `macd_dif` / `macd_dea` / `macd_hist` | `_prepare_stock_frame` | 标准 MACD (12,26,9) EWM | 抄底点 W 形 |
| `is_local_low_10d` | `build_ma_features` | 当日 low = 最近 10 根内最低 | 抄底点谷底 |
| `macd_w_pattern_flag` | `build_ma_features` | dif 转向 + dea 上翘 + hist 连续 2 根 > 0 | 抄底点 MACD 确认 |
| `days_since_bullish_engulf` | `build_ma_features` | 回溯 `_bullish_engulf_flag` 命中位置 | 起涨点上下文 |
| `days_since_breakout` | `build_ma_features` | 回溯 `_breakout_flag` 命中位置 | 追涨点上下文 |
| `days_since_pullback_low` | `build_ma_features` | 回溯 `pullback_depth` 落入动态区间的位置 | 起涨点上下文 |
| `days_since_chao_di` | `build_ma_features` | **占位 999，Job B 会填** | 起涨点上下文 |

**关键工具函数**：

- `_days_since(bars, predicate, max_lookback=20)`：回溯 bars 里最近一次 `predicate(latest, prev)=True` 的距离。0=当日，1=昨日，999=近 20 根无命中。签名与 `_breakout_flag / _bullish_engulf_flag` 一致，可以直接复用。
- `_days_since_pullback_low(bars, features_snapshot)`：pullback_depth 是窗口内最大回撤，跟 `_days_since` 的 `(latest, prev)` 签名不匹配，独立一个函数用 `bars[start:end+1]` 窗口回算。动态阈值从 features 快照传入（保证回溯时用同一套阈值）。
- `_is_local_low_10d(bars)`：判定当日 low 是否 = 最近 10 根内最低。
- `_macd_w_pattern_flag(bars, lookback=5)`：三条约束（hist 连续 2 根 > 0、dea 上翘、dif 在近 `lookback` 根内出现"降后升"拐点或当日仍在向上）。上游没喂 MACD 列时保守返回 False，不阻断主流程。

**MACD 三件套的实现细节**：

```python
ema12 = close.ewm(span=12, adjust=False).mean()
ema26 = close.ewm(span=26, adjust=False).mean()
frame["macd_dif"] = ema12 - ema26
frame["macd_dea"] = macd_dif.ewm(span=9, adjust=False).mean()
frame["macd_hist"] = 2.0 * (macd_dif - macd_dea)
```

用 `adjust=False` 是为了跟同花顺/东方财富看盘软件的 MACD 显示对齐（同花顺用递归 EMA）。跟 pandas 默认的 `adjust=True` 会差一个前 26 根的过渡期，但从第 27 根起完全一致。

**关键设计取舍**：

1. **MA7 和 MACD 在 `_prepare_stock_frame` 层算，不在 `build_ma_features` 层**：全市场 5000+ 只票如果每只在 `build_ma_features` 里独立算 MACD 会重复很多 EWM，pandas groupby.transform 一次算完更高效。
2. **`_days_since` 用回调谓词而不是 flag 列**：一开始想法是"在 `_prepare_stock_frame` 里把 `bullish_engulf_flag` 也算成一列，然后 features 层直接读列"。但 flag 逻辑（`_bullish_engulf_flag(latest, prev)`）依赖前一根的信息，在 pandas 里得用 `shift` 组合表达，不如直接在 features 层回溯清晰。且 features 层每票只跑一次，性能没问题。
3. **`days_since_chao_di` 用 999 占位**：`chao_di_flag` 是 Job B 才输出的形态识别结果，Job A 阶段无法回填历史 `chao_di_flag`——因为形态识别函数还没写。Job B 会用 bars 里回溯识别历史 chao_di 事件并覆盖此值。这样保持 Job A 独立可测，也不阻塞 Job B。
4. **`_macd_w_pattern_flag` 兜底逻辑**：如果 dif 没出现明确"降后升"拐点但当前正在向上（`dif_now > dif_prev`）也算，覆盖"已经跨过底部" 的情形——用户实盘可能在拐点后 1-2 根才看到，不该把它拒绝。

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/market/sector.py` | `_prepare_stock_frame` 里 MA 窗口 tuple 加 7；追加 MACD 三件套（EMA12/26 + DIF + DEA + HIST） |
| `shilun/market/ma5_features.py` | 新增 `_DAYS_SINCE_NOT_FOUND=999`、`_days_since`、`_pullback_depth_at`、`_pullback_low_predicate`、`_days_since_pullback_low`、`_is_local_low_10d`、`_macd_w_pattern_flag` 7 个工具函数；`build_ma_features` 输出末尾追加 10 个新字段 |
| `tests/test_five_buy_points_features.py`（新） | 15 条测试：`_days_since` 4 个边界（0/2/999/lookback cap/empty）、`_is_local_low_10d` 3 挡、`_macd_w_pattern_flag` 4 场景（有效 W / hist 负 / dea 平 / MACD 列缺失）、`build_ma_features` 3 条集成 |

**验证**：
- `pytest tests/test_five_buy_points_features.py` 15 passed
- `pytest tests/test_five_buy_points_features.py tests/test_trading_levels.py tests/test_holdings.py tests/test_ma5_features.py tests/test_breakout_events.py tests/test_breakout_events_api.py tests/test_market_sector.py tests/test_candidate_rules.py tests/test_analysis_service.py tests/test_ui_route.py` 106 passed（无回归）

**Job B 待办**：`detect_buy_point_pattern(features)` 识别函数，按优先级 突破 > 起涨 > 回踩 > 抄底 > 追涨 输出 `buy_point_pattern` 分类 + `buy_point_pattern_label` + 可选 `buy_point_pattern_context`。同时回填 `days_since_chao_di`（在识别函数内回溯 bars 找历史 `chao_di_flag` 命中位置）。

---

### 2026-07-09 · §4.7 五买点体系 · Job B：形态识别函数

**背景**：Job A 补齐了 10 个特征字段。Job B 用这些字段实现真正的五买点识别，输出 `{pattern, label, context, note, strength}`。

**新模块 `shilun/market/buy_point_patterns.py`**：跟 breakout_events / holdings 一样独立成文件，避免 `ma5_features` 越来越臃肿；也让"形态识别"跟"指标评分" 在物理层面分离，符合 §4.7 里两层解耦的设计。

**5 个判定函数（按优先级递减）**：

| 优先级 | 函数 | 条件 |
|---|---|---|
| 1 | `_is_tu_po` | `ma5_breakout_flag` + `breakout_volume_ratio > 1.2` + `close_position > 0.65` + `real_body_ratio > 0.45`；`previous_high_break_flag` 或 `box_break_flag` 时 `strength=strong` |
| 2 | `_is_qi_zhang` | `bullish_engulf_flag` + 上下文任一：(A) `days_since_chao_di <= 3` → context="谷底反转"；(B) `days_since_pullback_low <= 3` 且 `ma5_reclaim_flag` → context="回踩确认" |
| 3 | `_is_hui_cai` | `days_since_breakout <= 20` + `abs(pullback_to_ma5_distance) <= 2%` + `pullback_volume_ratio < 0.9` |
| 4 | `_is_chao_di` | `close_ma5_ratio < 0` + `is_local_low_10d` + `close > ma7` + `macd_w_pattern_flag`；输出带 note "实战难捕捉，仅提示" |
| 5 | `_is_zhui_zhang` | (`days_since_breakout <= 5` 或 `days_since_chao_di <= 8`) + `close_ma5_ratio > 0` + `ma5_slope_3d > 0`；输出带 note "不推荐，除非板块极强或有重大利好" |

**主入口 `detect_buy_point_pattern(features)`**：顺序遍历 `_PATTERN_DETECTORS` 元组（按优先级排好），命中即返回，全部不命中返回 `{pattern: "none", label: "-"}`。**纯函数、无 IO**，可以直接单测。

**`backfill_days_since_chao_di(bars, max_lookback=20)`**：Job A 里 `build_ma_features` 输出的 `days_since_chao_di` 是占位 999——因为形态识别函数当时还没写。上层（Job C 的 `candidates.py`）拿到 features + bars 后，调这个函数回扫历史 bars 里每根位置是否命中 `_is_chao_di`，找到最近的距离并覆写 `features["days_since_chao_di"]`。这样起涨点判定"距最近抄底 3 根内" 才能生效。

**关键设计取舍**：

1. **判定函数按元组顺序遍历，避免 if-elif 长链**：
   ```python
   _PATTERN_DETECTORS = (
       ("tu_po", _is_tu_po),
       ("qi_zhang", _is_qi_zhang),
       ("hui_cai", _is_hui_cai),
       ("chao_di", _is_chao_di),
       ("zhui_zhang", _is_zhui_zhang),
   )
   ```
   将来加新的形态或调整优先级只改这个元组，主入口逻辑不动。
2. **抄底点回填单独抽函数**：不做进 `build_ma_features` 是为了避免循环依赖（features → patterns → features 里的 flag）。上层调用方（Job C）负责组装：先 `build_ma_features`，再 `backfill_days_since_chao_di` 覆写 `days_since_chao_di`，最后 `detect_buy_point_pattern`。
3. **`_chao_di_snapshot_from_bar` 构造最小特征集**：回填时不需要重跑整个 `build_ma_features`（那是 O(bars) 的成本），只需要 `_is_chao_di` 用到的 5 个字段。每根重构 snapshot 是 O(1) 成本，回填整体 O(max_lookback)。
4. **`strength` 只在突破点输出**：其他形态目前没有强弱区分。将来若要给回踩点/起涨点也分档，模式一致。
5. **`_is_chao_di` 保持严格**：4 个条件缺一不可。用户明确说"实战难捕捉"，宁可少标注也不误导。测试里 `close < MA7` / 没有 MACD W / 位于 MA5 上方 各设一条反例。

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/market/buy_point_patterns.py`（新，约 240 行） | 5 个判定函数 + `detect_buy_point_pattern` 主入口 + `backfill_days_since_chao_di` + `BUY_POINT_PATTERNS` / `BUY_POINT_LABELS` / `BUY_POINT_NOTES` 常量 |
| `tests/test_buy_point_patterns.py`（新） | 29 条测试：5 挡形态各自的正例 + 反例、优先级顺序（tu_po > qi_zhang > hui_cai > zhui_zhang）、回填函数的 3 种边界（空 bars、无抄底、有抄底不崩溃） |

**验证**：
- `pytest tests/test_buy_point_patterns.py` 29 passed
- 全量回归 135 passed（无回归）

**Job C 待办**：在 `candidates.py` 的 `build_candidates` 里给每张候选卡加 `buy_point_pattern` 字段。集成步骤：
1. 拿到 `ma5_features`
2. `features["days_since_chao_di"] = backfill_days_since_chao_di(feature_bars)`
3. `pattern_result = detect_buy_point_pattern(features)`
4. 候选卡输出 `buy_point_pattern` / `buy_point_pattern_label` / `buy_point_pattern_context` / `buy_point_pattern_note`

同时 `stock_panel` 也接入（单票分析面板也展示形态徽章）。

---

### 2026-07-09 · §4.7 五买点体系 · Job C：候选池与单票分析接入

**背景**：Job B 提供了纯函数 `detect_buy_point_pattern(features)` 和 `backfill_days_since_chao_di(bars)`。Job C 负责在两条入口（候选池、单票分析）集成调用，把 5 个新字段挂到 API 响应上，供 Job D 前端徽章消费。

**候选池 `build_candidates` 集成三步**（`shilun/market/candidates.py`）：

```python
ma5_features = build_ma_features(feature_bars)
# ...（现有逻辑：breakout_event 覆写 ma5_features 的追踪字段）
# Job C：先回填抄底点上下文，再识别形态
ma5_features["days_since_chao_di"] = backfill_days_since_chao_di(feature_bars)
buy_point_pattern_info = detect_buy_point_pattern(ma5_features)
```

**候选卡 dict 新增 5 个字段**：

| 字段 | 说明 |
|---|---|
| `buy_point_pattern` | `tu_po / qi_zhang / hui_cai / chao_di / zhui_zhang / none` |
| `buy_point_pattern_label` | 中文标签（"突破点" / "起涨点" / ...） |
| `buy_point_pattern_context` | 上下文（"谷底反转" / "回踩确认" / ...），可为 None |
| `buy_point_pattern_note` | 提醒（"实战难捕捉" / "不推荐" 等），仅抄底点和追涨点有 |
| `buy_point_pattern_strength` | 强度（仅突破点有 "strong" / "valid"） |

**单票分析 `stock_panel` 集成**（`shilun/api/__init__.py`）：不重复计算完整 `ma5_features`（stock_panel 只有原始日线，缺 volume_ratio_20 等 candidates 用的字段），改为**从 `sector_trends_cache` 里查这只票的候选卡缓存**：

```python
cached_sectors = _load_sector_cache(store, target_date, bm_ticker, trend_lookback_days=60)
for c in (cached_sectors["payload"].get("candidates") or []):
    if c["ticker"].upper() == ticker_norm:
        buy_point_pattern_info = {
            "pattern": c.get("buy_point_pattern") or "none",
            "label": c.get("buy_point_pattern_label"),
            "context": c.get("buy_point_pattern_context"),
            "note": c.get("buy_point_pattern_note"),
            "strength": c.get("buy_point_pattern_strength"),
        }
        break
```

缓存里没有对应票（例如不在板块龙头/中军候选池）→ `pattern="none"`，Job D 前端徽章不显示。

**关键设计取舍**：

1. **单票分析走缓存查表，不重跑识别函数**：如果两处都跑，一是重复算 pattern 浪费算力；二是可能因两处 features 组装口径细微差异出现结果不一致，用户看到"候选池说是突破点，单票分析说是回踩点" 会困惑。用缓存查表强制两处必然一致。
2. **`backfill_days_since_chao_di` 必须在 `detect_buy_point_pattern` 之前调**：qi_zhang 判定依赖 `days_since_chao_di <= 3` 这个条件。如果不回填，Job A 输出的 999 占位会让"谷底反转起涨点" 永远识别不到。
3. **breakout_event 覆写在识别函数之前**：Job 6 已经把 event 覆写到 `previous_high_hold_ratio` / `fall_back_into_box_flag` / `post_breakout_shrink_ratio` 三个字段。这些不影响形态识别（识别用的是 ma5_breakout_flag / bullish_engulf_flag 这些日线原始信号），顺序上没有依赖关系，但仍保持"先覆写、再识别" 的编排更清晰。
4. **候选卡 `buy_point_pattern` 与 `buy_point_type` 并存**：`buy_point_type`（指标层，`ma5_pullback / ma5_breakout / ma5_reclaim / watch`）保留不动，继续服务评分。`buy_point_pattern`（形态层，五买点）是新增字段，服务用户表达。前端 Job D 决定优先展示哪个。

**修改文件**：

| 文件 | 修改 |
|---|---|
| `shilun/market/candidates.py` | 顶部 `from shilun.market.buy_point_patterns import backfill_days_since_chao_di, detect_buy_point_pattern`；`build_candidates` 里 `build_ma_features` 后加两行调用；候选卡 dict 加 5 个字段 |
| `shilun/api/__init__.py` | `stock_panel` 初始化 `buy_point_pattern_info = {"pattern": "none", "label": "-"}`；在算完 trade_plan 后加缓存查表逻辑；返回体加 5 个字段 |
| `tests/test_market_sector.py` | `test_sector_trends_support_lightweight_initial_response` 加 3 条断言：`buy_point_pattern` 字段存在、值在合法枚举内、`buy_point_pattern_label` 存在 |

**验证**：
- `pytest tests/test_market_sector.py` 5 passed（1 条已有测试加 3 条断言，其他 4 条无回归）
- 全量回归 135 passed

**Job D 待办**：前端候选卡评分行加五色徽章。CSS 5 个 class 对应 5 种颜色：
- `tu_po` → 红（强势入场）
- `qi_zhang` → 橙红（确认入场）
- `hui_cai` → 蓝（波段入场）
- `chao_di` → 灰（谨慎标注）
- `zhui_zhang` → 灰（不推荐但记录）

Hover 徽章展示 `context` / `note` / `strength`。同时候选卡 `stockStrategySnapshot`（单票分析）也读 `data.buy_point_pattern` 出徽章。静态资源升版。

---

### 2026-07-09 · §4.7 五买点体系 · Job D：前端五色徽章

**背景**：Job C 已经把 5 个字段挂到候选卡和单票分析响应上。Job D 前端读这些字段渲染徽章。

**候选池评分行**（原来）：
```
最终 82 · 股票 75 · 买点 88 · 风险 0.85 · [突破有效]
```

**改造后**：
```
最终 82 · 股票 75 · 买点 88 · 风险 0.85 · [突破点] [突破有效]
```

五色徽章按 v0.2 战法自然分层：

| 挡位 | 颜色 | 心理暗示 |
|---|---|---|
| `tu_po` 突破点 | 红（`#df6553`） | 强势入场 |
| `qi_zhang` 起涨点 | 橙红（`#ff7850`） | 确认入场 |
| `hui_cai` 回踩点 | 蓝（`#2f80ed`） | 波段入场 |
| `chao_di` 抄底点 | 灰 | 谨慎标注 |
| `zhui_zhang` 追涨点 | 灰 + 斜体 | 不推荐但记录 |

Hover tooltip 展示 `context`（"谷底反转" / "回踩确认"）+ `strength`（tu_po 独有）+ `note`（chao_di / zhui_zhang 独有），三行拼接成 title 字符串，走浏览器原生 tooltip（跟 breakout badge 同一套模式）。

**单票分析集成**：`renderStockStrategyCard` 里在 `<h3>` verdict 后追加 `stockPanelPatternBadge(data)`，跟候选池独立但逻辑一致——保证一个 ticker 在候选池和单票分析看到同一个徽章。

**#2（同步/预计算）交互优化并入 Job D**：
- `[查询板块动向 · 读缓存 · 秒回]`（主按钮）
- `[强制重新计算 · 跑全量 · 60-120s]`（次按钮）
- CSS 新增 `.button-hint` 胶囊 chip，让"读缓存/跑全量" 说明视觉上区隔
- 每个按钮加 `title` 悬停提示，说明用途和耗时
- JS 里 5 处 `textContent` 改 `innerHTML`，保留 hint 结构

**修改文件**：

| 文件 | 改动 |
|---|---|
| `shilun/static/app.js` | `renderCandidates` 内新增局部函数 `buyPointPatternBadge(c)`；评分行拼接徽章；新增 `stockPanelPatternBadge(data)` 供单票分析用；`renderStockStrategyCard` 追加徽章；#2 按钮文案改造 5 处 |
| `shilun/static/app.css` | 新增 `.pattern-badge` 基类 + 5 种颜色 class；新增 `.button-hint` 胶囊样式 |
| `shilun/static/index.html` | 两个按钮加语义化文案 + hint chip + title；静态资源版本 → `20260709-buy-point-pattern-badges` |
| `tests/test_ui_route.py` | 版本号 + 按钮文案断言更新 |

**验证**：
- `node --check app.js`：语法 OK
- `pytest tests/test_ui_route.py tests/test_buy_point_patterns.py tests/test_five_buy_points_features.py tests/test_market_sector.py` 54 passed
- 全项目回归 211 passed（4 条 pre-existing 失败与本次改动无关）

**§4.7 五买点体系整体完成**：Job A（特征层）+ Job B（识别函数）+ Job C（候选池/单票分析接入）+ Job D（前端徽章）四步全部收尾。任何一张候选卡或单票分析页现在都能显示 `buy_point_pattern` 的对应徽章，用户能一眼看出"这只票是突破点、起涨点、回踩点、抄底点、追涨点"的哪一种。

---

