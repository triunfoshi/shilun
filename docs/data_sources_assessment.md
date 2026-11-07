# 数据源补充评估报告

> 评估两件事：(1) Mongo 现有数据能否算出 MA50/MA120；(2) daily_stock_analysis 用的免费数据源是否可行。
> 评估时间：2026-06-25

---

## 一、Mongo 现有指数数据的真实情况

### 1.1 结论：现有数据不够

实际查询 Mongo `market_daily_bars` 集合（字段名是 `ticker` + `date`，不是 `ts_code` + `trade_date`）：

| 指数 | 当前条数 | 最早日期 | 最新日期 |
|---|---|---|---|
| 000001.SH 上证 | **91 根** | 2026-02-03 | 2026-06-24 |
| 000300.SH 沪深300 | 92 根 | 2026-02-03 | 2026-06-24 |
| 399001.SZ 深成指 | 91 根 | 2026-02-03 | 2026-06-24 |
| 399006.SZ 创业板 | 91 根 | 2026-02-03 | 2026-06-24 |
| 000905.SH 中证500 | **0 根** | - | - |
| 000852.SH 中证1000 | **0 根** | - | - |

只有约 4 个月（91 根日线）：
- ✅ MA5、MA10、MA20 都可以算（早就在算）
- ⚠️ MA50 勉强可以算（需 50 根，当前 91 根够，但年初没数据）
- ❌ MA120 不够（需 120 根）
- ❌ MA250 远远不够

### 1.2 解决方案有两条路

**路径 A：** 用 Tushare 历史回填（已有 token，但 Tushare 的 `index_daily` 接口对积分有要求，上证完整历史需要至少 2000 积分）

**路径 B：** 用免费源（akshare、efinance 等）补齐历史，**不依赖任何 token**

下面评估路径 B。

---

## 二、daily_stock_analysis 项目的数据源体系

### 2.1 项目数据源全景

来自项目 `data_provider/__init__.py` 的优先级声明（**未配置 TUSHARE_TOKEN 的免费链路**）：

| 优先级 | 数据源 | 库名 | 需要 Token | 用途 |
|---|---|---|---|---|
| P0 | 东方财富 | `efinance` | ❌ 免费 | 日线、实时、板块、指数 |
| P1 | AkShare 聚合 | `akshare` | ❌ 免费 | 日线、北向、涨停、概念、人气榜 |
| P2 | 通达信 | `pytdx` | ❌ 免费 | 日线、实时 |
| P3 | 证券宝 | `baostock` | ❌ 免费（需 login） | 日线、基本面 |
| P4 | Yahoo Finance | `yfinance` | ❌ 免费 | 美股、港股 |

**关键发现：A 股全部数据可以完全免 token、免付费。** Tushare 在该项目里是"如果你有 token 就提优先级"，并非必需。

### 2.2 重点数据源能力对比

#### efinance（东方财富）
- 主要指数实时（`get_main_indices`）
- 市场涨跌统计（`get_market_stats`，相当于我们的"广度"）
- **板块涨跌榜（`get_sector_rankings`，对标我们当前用 stock_basic.industry 的代理）**
- 股票所属板块（`get_belong_board`）
- 历史 K 线 + 实时 + 基本信息（`get_enhanced_data`）

#### akshare（最强综合源）
- A 股历史日线（4 个数据源轮转：东财/新浪/腾讯/etc.）
- **筹码分布（`get_chip_distribution`） ← 当前缺口"筹码峰"对应**
- 板块涨跌榜（`get_sector_rankings`）
- **概念/题材涨跌榜（`get_concept_rankings`） ← 当前缺口"主线代理升级"对应**
- **人气股榜（`get_hot_stocks`，雪球关注度兜底）**
- **涨停池（`get_limit_up_pool`，含连板/封板时间） ← 当前缺口"涨停开板比例"对应**

#### pytdx（通达信）
- 直连通达信行情服务器
- 实时数据稳定，无配额
- 主要用于实时和兜底

#### baostock（证券宝）
- A 股完整历史日线（**最早可到 1990 年**）
- 基本面数据
- 需要 `bs.login()` 但完全免费

### 2.3 本地实测结果

**测试1：akshare 拿上证完整历史**

```python
import akshare as ak
df = ak.stock_zh_index_daily(symbol='sh000001')
# 8668 根日线，最早 1990-12-19，最新 2026-06-25，耗时 3.1 秒
# 字段：date, open, high, low, close, volume
```

**结论：** 一次免费调用 3 秒就能拿到上证 36 年完整日线，足以算 MA5/10/20/50/120/250 任何均线。

**测试2：akshare 拿涨停板池**

```python
df = ak.stock_zt_pool_em(date='20260624')
# 98 只涨停，1.9 秒
# 字段：代码, 名称, 涨跌幅, 成交额, 流通市值, 封板资金, 
#       首次封板时间, 最后封板时间, 炸板次数, 涨停统计, 连板数, 所属行业
```

**结论：** 不仅有涨停名单，**连板数 + 炸板次数 + 封板资金 + 首/末封板时间**全部都有。这是当前完全缺失的高价值数据，可以直接用于：
- "炸板"风险标记（用真实炸板次数，不是估算）
- 情绪温度（连板高度、首封时间分布）
- 主线确认（涨停股的"所属行业"集中度）

**测试3：akshare 北向资金历史**

```python
df = ak.stock_hsgt_hist_em(symbol='北向资金')
# 2696 条历史记录，约 10 年
# 但部分字段最近几天是 nan（接口返回不全）
```

**结论：** 接口可用但要做空值过滤。北向资金的"净买额"是关键指标，目前 nan 情况需要在实现时回退到其他源（akshare 还有 `stock_hsgt_north_net_flow_in_em` 等接口）。

**测试4：东方财富概念板块**

某些子接口（`79.push2.eastmoney.com`）走系统代理失败。属于本地网络配置问题，可通过 `unset http_proxy` 或在请求里加 `proxies={}` 绕过。

---

## 三、数据源补充优先级方案

### 3.1 第一优先级（解决 MA50/MA120 缺口）

**用 akshare 一次性补齐主流指数的完整历史日线。**

需要补的指数（按重要性）：
1. `000001.SH` 上证 → `ak.stock_zh_index_daily('sh000001')`
2. `000300.SH` 沪深300 → `ak.stock_zh_index_daily('sh000300')`
3. `399001.SZ` 深成指 → `ak.stock_zh_index_daily('sz399001')`
4. `399006.SZ` 创业板 → `ak.stock_zh_index_daily('sz399006')`
5. `000905.SH` 中证500 → `ak.stock_zh_index_daily('sh000905')`
6. `000852.SH` 中证1000 → `ak.stock_zh_index_daily('sh000852')`

**实现路径：**
- 写一个 `scripts/sync_index_history_akshare.py` 一次性脚本
- 把 akshare 数据落到 Mongo `market_daily_bars` 集合（保持字段名 `ticker`/`date` 一致）
- 6 个指数大约 20 秒能拿完
- 之后 PART1 的 `_prepare_index_frame()` 完全不用改，自动就能算 MA50/MA120/MA250

**风险评估：**
- ⚠️ akshare 接口字段是中文（date/open/high/low/close/volume），需要做字段对齐
- ⚠️ akshare 没有 `amount` 字段（只有 volume），但 PART1 需要 `amount`（用于"成交额温度"判断）
- ✅ 解决：amount 可以用 `volume × close × 100`（成交额≈成交量×均价）估算，或换 `ak.stock_zh_index_spot_em` 拿当日含 amount 的数据组合

### 3.2 第二优先级（升级板块识别）

**用 akshare `stock_board_concept_name_em` + `stock_board_industry_name_em` 替换 stock_basic.industry 代理。**

当前 PART1 用 `stock_basic.industry` 做主线代理，板块划分是固定的二级行业分类。akshare 的"概念板块"是**动态的题材**（AI、机器人、芯片等），更接近用户感知。

风险：需要解决代理问题（unset 系统代理或加 `proxies={}`），其余技术上完全可行。

### 3.3 第三优先级（涨停情绪和炸板）

**用 akshare `stock_zt_pool_em` 替换当前的"涨停家数估算"。**

当前 PART1 是用 `pct_chg >= 0.095` 估算涨停（不准，可能漏算 ST、北交所）。akshare 直接返回真实涨停名单 + 连板数 + 炸板次数：
- 可以做"今日连板梯队"（5板/4板/3板/2板分布）
- "炸板率" = 炸板次数 / 涨停次数
- 板块涨停集中度 = 涨停股按行业聚合

### 3.4 第四优先级（北向资金）

**用 akshare `stock_hsgt_north_net_flow_in_em` 接入北向资金净流入。**

PART1 加一个新维度："北向资金分"（持续净流入加分、急速流出扣分）。

### 3.5 第五优先级（筹码分布，单票精化）

**用 akshare `get_chip_distribution`（单票筹码峰）改进 PART3。**

PART3 当前的支撑位是 MA5 × 0.98，太机械。真正的支撑往往在筹码密集区（最大持仓成本）。但这个调用是按票一只一只查的，需要权衡性能。

---

## 四、可行性结论

### 4.1 直接可执行的最小改动

**只做"补指数历史"这一件事（路径 A）：**

| 项目 | 评估 |
|---|---|
| 依赖增加 | 只加 `akshare` 一个库（已测试可用）|
| 代码改动 | 一个 sync 脚本 + 一个 Mongo 写入函数 |
| 数据落地 | 6 个指数 × 约 8000 根日线 = 5 万条文档，Mongo 几乎无压力 |
| 风险 | amount 字段需要补算或回退 Tushare |
| 效果 | 立刻支持 MA50/MA120/MA250，波浪理论关键位精度大幅提升 |

**预期工时：** 0.5-1 天

### 4.2 完整升级的全景方案

| 阶段 | 接入 | 解决的缺口 | 工时 |
|---|---|---|---|
| 阶段1 | akshare 指数历史 | MA50/MA120/MA250 | 1天 |
| 阶段2 | akshare 涨停池 + 概念板块 | 涨停开板率、动态主线 | 2天 |
| 阶段3 | akshare 北向资金 | 北向流入维度 | 1天 |
| 阶段4 | akshare 筹码分布（按需） | PART3 真实支撑位 | 2天 |
| 阶段5 | 波浪文档导入接口（最高优先级） | 人工波浪位 | 3天 |

### 4.3 不建议立刻做的事

- ❌ **不建议接入 pytdx**：通达信连接不稳定，且我们目前不需要分时数据
- ❌ **不建议接入 yfinance**：A 股场景用不上
- ❌ **不建议引入完整的 DataFetcherManager 多源回退框架**：daily_stock_analysis 的多源切换是为了应对单点故障，我们目前 Tushare 已经稳定，只需要补缺失数据，引入完整框架反而增加复杂度

---

## 五、推荐下一步

**最小可执行步骤（先确认数据落地，再谈集成）：**

1. 写一个测试脚本：拿 akshare 的上证 + 沪深300 历史，对齐字段后落到 Mongo
2. 验证 `_prepare_index_frame()` 能正确读到完整历史并算出 MA50/MA120
3. 测一下 PART1 的"明日关键点位"在有 MA120 之后预判精度的变化
4. 如果效果好，扩展到 6 个指数 + 加入定时同步

**等你确认是否启动这条路径，还是先做其他优先级（例如择时系统或波浪文档导入）。**
