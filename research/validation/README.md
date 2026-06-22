# Validation 目录说明

`research/validation/` 用于承接石论二期的因子有效性与收益证明分析。

这一层的目标不是直接下单，而是回答：

- 哪些字段是真正有效的因子
- 哪些因子只是样本内有效
- 哪些因子在不同市场环境下会失效

建议后续优先放入以下脚本：

- `strategy_validation.py`：从 Mongo `market_snapshot_records` 按 `strategy_id + strategy_version` 聚合收益、胜率、超额收益和回撤
- 单因子分组收益分析
- IC / RankIC 分析
- 分行业、分阶段验证
- Walk-forward 验证
- 因子漂移分析

原则：

- 任何准备进入主策略链的新因子，都应先在这里验证
- 这里输出的结论，应服务 `ranker`、`portfolio` 和回测，不直接耦合解释层
- 三期改进点：验证脚本默认只读取 Mongo 快照记录，不从 Excel/CSV 取数，也不重新调用 Tushare

策略级验证示例：

```bash
python -m research.validation.strategy_validation --start-date 2026-03-01 --end-date 2026-05-16
```
