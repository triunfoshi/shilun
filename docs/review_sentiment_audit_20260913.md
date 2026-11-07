# 昨日涨停样本核验

核验时间：2026-09-13 13:57:45 +08:00。范围：当前展示的 2026-09-09 收盘快照及前一交易日 2026-09-08。直接只读查询当前配置指向的 MongoDB；未调用自动建索引的存储初始化器，未补数或重新生成快照。

## 结论

数据不完整。“昨日涨停数 0”来自前一日涨停池为空，不是已确认的市场零涨停。中位溢价算法已存在，但没有昨日涨停名单作为样本，不能计算。

| 交易日 | market_daily_bars 日线记录 | limit_up_pool 记录 / 唯一股票 | daily_price_limits |
| --- | ---: | ---: | ---: |
| 2026-09-07 | 5,556 | 0 / 0 | 5,637 |
| 2026-09-08 | 5,556 | 0 / 0 | 0 |
| 2026-09-09 | 5,557 | 48 / 48 | 0 |

日线记录包含库内不同证券/指数记录，并非纯 A 股股票家数。存在日线不等于涨停样本完整，也不证明每只股票的涨停价和前收盘已齐全。

快照生成于 2026-09-09 20:38:47 +08:00：

- `previous_trade_date = 2026-09-08`。
- `source_sync` 对 9 月 8 日记录 `status=unavailable, error_type=AkshareFetchError`；9 月 9 日为 `cached`。
- `previous_limit_up_count=0`，溢价 `sample_size=0`，`mean/median/positive_rate/relimit_rate=null`。
- 9 月 9 日涨停池同步记录为 `success, row_count=48`，没有重复股票。

未根据空池推断真实涨停数；没有昨日名单，跨日价格匹配覆盖率也无法估计。需要先恢复可信的 9 月 8 日涨停样本，核对样本两日价格，再重算反馈。9 月 9 日涨跌停价也缺失，跌停类指标另需补齐后验证。

## 复现与实现

在项目根目录执行 `.venv312/bin/python scripts/audit_review_sentiment.py --date 2026-09-09`。脚本仅输出覆盖、同步状态和聚合校验结果，不输出连接凭据。

计算路径为 `shilun/review/service.py` 的 `_sentiment`：昨日涨停名单 → 两日收盘价匹配 → 当日相对前收收益 → 中位数。页面迁到 P2 后，针对快照明确记录的昨日采集失败显示“待补齐”，本轮未修改数据库或计算口径。

## 隔夜外围复用来源

Vibe-Astock 当前主分支核验提交 `d7a0e568424f5c0b1de9e39b1ddd733ecdbf9edb`，腾讯适配来自 `duanxian/overseas.py`；本地参考的 `frontend/src/pages/DailyReview.tsx` 与 `vr/gstock.py` 提供三组卡片及东方财富备用指数逻辑。保留 Apache-2.0 许可于 `licenses/vibe-astock.txt`。

VC-Astock 的适配增加独立只读接口、有限值检查、请求超时、来源及缺失状态；无数据库持久化。隔夜行情是最新报价，不是所选 A 股历史复盘日的历史报价。
