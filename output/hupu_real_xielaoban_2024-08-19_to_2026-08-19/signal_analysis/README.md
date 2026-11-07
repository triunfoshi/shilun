# “猛干 / 打满”语料事件：方法与文件说明

## 口径

- `reply_signals.csv`：所有包含“猛干”或“打满”的公开回复；不等同于买入信号。
- `entity_signal_candidates.csv`：从命中回复中找到的当前 Tushare 名称或透明的简称映射；`mention_context_unreviewed` 不代表目标个股。
- `target_signal_records.csv`：逐条语境复核后的候选目标、条件表达以及清仓/讽刺反例。仅 `chart_marker=true` 的记录被绘入深色菱形标记。
- `effective_trade_date`：用于和日K对齐的日期，交易日 15:00 前为当日，其余为下一交易日；并非真实成交日。

## 个股标准化

原句中没有显式六位证券代码。代码来自当前 Tushare `stock_basic` 快照；全名原样匹配为高置信度，社区简称为中/低置信度。未安全匹配的候选（例如“中超”）保留原词且不写代码。

## 图表

`sh_index_kline_keyword_markers.png` / `.svg` 使用 Tushare `000001.SH` 日线。圆点表示全部关键词出现日；空心菱形表示已经过语境复核、且可标准化为个股的直接或条件式正向表达。标记不是交易建议、成交记录、持仓证明或收益归因。
