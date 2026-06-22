# 聚宽回测接入说明

二期关于聚宽的落地口径已经明确为路线 A：

- 不再尝试把整个 `shilun/` 包迁到聚宽
- 不再依赖本地环境每天先跑完石论再让聚宽读结果
- 直接维护一份“可单文件粘贴进聚宽”的轻策略脚本

这意味着，聚宽承担的是“策略执行与收益验证”，不是“完整运行石论项目”。

## 当前已经落地的形态

现在仓库里的聚宽相关能力分成两层：

1. 本地研究层
   - `shilun/backtest/`
   - 用来做符号转换、bars 标准化、轻策略规则抽象
   - 主要服务本地研究、测试和策略压缩

2. 聚宽执行层
   - `examples/joinquant_shilun_standalone.py`
   - `examples/joinquant_strategy.py`
   - `scripts/export_joinquant_strategy.py`
   - 目标是产出可直接粘贴到聚宽策略编辑器的单个 Python 文件

## 路线 A 的真实边界

路线 A 能解决的是：

- 把石论的一部分核心选股与交易逻辑压缩成单文件策略
- 在聚宽内补上股票池、仓位控制、交易成本、涨跌停、持仓数量等执行细节
- 在聚宽上完成策略收益率、回撤、交易记录等账户级验证

路线 A 不能解决的是：

- 把整个 `shilun` 工程无损搬到聚宽
- 在聚宽里直接运行本地训练流程
- 在聚宽里完成完整因子研究、alpha 归因、样本外稳定性分析

所以二期的回测一定要拆成两部分：

1. 本地研究型回测
   - 放在 `research/validation/` 和 `research/backtest_local/`
   - 负责因子有效性、alpha、收益分布、样本内外稳定性

2. 聚宽执行型回测
   - 放在 `examples/` 和 `scripts/export_joinquant_strategy.py`
   - 负责轻策略落地、调仓、成交与收益曲线验证

## 当前主入口

优先使用下面两个文件：

- `examples/joinquant_shilun_standalone.py`
  - 仓库里的单文件基准版
  - 不依赖 `shilun import`
  - 可以直接贴进聚宽
- `scripts/export_joinquant_strategy.py`
  - 从单文件基准版导出一份可定制脚本
  - 可替换基准、股票白名单、回看窗口、最大持仓、候选池规模

默认导出命令示例：

```bash
python scripts/export_joinquant_strategy.py \
  --output examples/joinquant_strategy.py \
  --benchmark 000300.XSHG \
  --lookback 120 \
  --max-positions 4 \
  --target-weight 0.25 \
  --trim-weight 0.125
```

如果想把策略收敛到一组固定股票白名单，可以继续追加：

```bash
--stock 000001.XSHE --stock 600519.XSHG
```

## 当前单文件策略里已经补到的执行约束

`examples/joinquant_shilun_standalone.py` 当前已经包含：

- 基准设置
- 交易成本
- 股票池筛选
- ST/停牌过滤
- 新股上市天数过滤
- 流通市值与换手率约束
- 涨停不追、跌停不砍
- 最大持仓数控制
- 仓位上限与总仓位缩放
- ATR 波动率联合仓位缩放
- 浮盈后的 ATR/回撤联合利润保护退出
- 持仓元信息同步
- `invalidation_level` 生成

其中要特别注意：

- `invalidation_level` 已经在单文件策略的 `analyze_security(...)` 中生成
- 现在已经开始接入执行层：
  - 跌破失效位直接退出
  - 临近失效位会把持仓压到更小权重
  - 原本不做老仓位微调的逻辑，也已经放开到“有意义的减仓”层面
  - 浮盈达到一定幅度后，如果从阶段高点回撤过深，会直接触发利润保护退出

## Mongo 与回测结果沉淀

本地 `snapshot_job` 现在已经不是只写 CSV 和 Markdown。

如果配置了 Mongo：

- `analysis_snapshots`
  - 保存逐票分析结果
- `market_snapshot_records`
  - 保存全市场逐票排名明细
  - 字段规范见 [docs/market_snapshot_records_spec.md](/Users/shibicheng/shilun_standalone/docs/market_snapshot_records_spec.md)
- `market_snapshots`
  - 保存某天榜单摘要和 topN 结果

这部分的意义是：

- 本地研究回测可以直接读取数据库结果
- 后续因子有效性、收益证明和策略复盘不必反复重算文本产物
- 可以把“每日结果层”变成二期真正的数据资产

## 现在的结论

当前状态不应再表述为“聚宽最小接入骨架”。

更准确的说法是：

- 本地研究回测与聚宽执行回测已经正式分轨
- 聚宽方向按路线 A 直接落单文件策略
- Mongo 结果沉淀开始作为二期数据底座优先建设

后续二期继续补强的重点是：

- 本地收益率与 alpha 研究框架
- 因子有效性与样本外验证
- 单文件策略中的仓位与退出规则继续强化
- `invalidation_level` 继续强化为更稳定的减仓/止损动作
- 把 `market_snapshot_records` 上的未来收益、最大涨幅、最大回撤标签进一步接进本地研究回测
