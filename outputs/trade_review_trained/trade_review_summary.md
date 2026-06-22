# 交易复盘与石论评分对照

- 闭环交易数: 210
- 亏损交易数: 130
- 买入日完整版石论给出正向结论且最终亏损: 11
- 其中买入日强看多且最终亏损: 0
- 卖出日系统仍偏正向但实际已亏损离场: 32
- 新 trigger 层理论可过滤的亏损交易: 129
- 新 trigger 层会同时过滤掉的盈利交易: 80

## 重点案例

| ticker | name | buy_date | sell_date | hold_days | net_pnl | gross_return | buy_conclusion_label | buy_structure_score | buy_trigger_score | buy_trigger_state | buy_opportunity_type | buy_p_continue_10d | buy_p_acceptance_1d | buy_p_fail_fast_3d | buy_risk_score | buy_breakout_quality | buy_volume_pattern | buy_rank_score | sell_conclusion_label | sell_structure_score | sell_p_continue_10d | sell_risk_score | mismatch_type |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 600580.SH | 卧龙电驱 | 2025-02-13 | 2025-02-14 | 1 | -124.0 | -2.44% | confirmation_needed | 100 | 100 | watch | observe | 91.25% | 0.1264 | 0.0983 | 69 | suspicious | neutral | 116.352 | confirmation_needed | 100 | 88.69% | 69 | system_stayed_positive_but_loss |
| 000932.SZ | 华菱钢铁 | 2025-07-08 | 2025-07-16 | 8 | -96.0 | -2.30% | confirmation_needed | 100 | 100 | watch | observe | 90.54% | 0.0337 | 0.0404 | 59 | suspicious | gentle_expand | 129.116 | confirmation_needed | 86 | 81.94% | 58 | system_stayed_positive_but_loss |
| 000564.SZ | 供销大集 | 2025-09-05 | 2025-09-08 | 3 | -90.0 | -2.23% | confirmation_needed | 100 | 100 | watch | observe | 78.88% | 0.0341 | 0.1606 | 69 | valid | neutral | 100.244 | defense_first | 100 | 85.75% | 74 | buy_looked_good_then_broke |
| 600487.SH | 亨通光电 | 2025-12-23 | 2025-12-26 | 3 | -79.0 | -3.02% | confirmation_needed | 100 | 100 | watch | observe | 76.59% | 0.5454 | 0.1149 | 72 | suspicious | impulsive_spike | 97.696 | defense_first | 100 | 94.67% | 75 | buy_looked_good_then_broke |
| 603659.SH | 璞泰来 | 2025-02-27 | 2025-02-28 | 1 | -30.0 | -0.86% | confirmation_needed | 100 | 100 | watch | observe | 84.54% | 0.1055 | 0.0421 | 57 | valid | neutral | 125.014 | momentum_but_heavy_overhead | 100 | 38.78% | 58 | other |
| 600021.SH | 上海电力 | 2025-09-03 | 2025-09-04 | 1 | -24.0 | -0.63% | confirmation_needed | 100 | 100 | watch | observe | 73.38% | 0.0188 | 0.3326 | 67 | valid | gentle_expand | 86.424 | defense_first | 100 | 56.73% | 68 | buy_looked_good_then_broke |
| 600895.SH | 张江高科 | 2025-08-08 | 2025-08-11 | 3 | -17.0 | -0.49% | confirmation_needed | 100 | 100 | watch | observe | 93.09% | 0.9549 | 0.0225 | 61 | suspicious | neutral | 130.74 | confirmation_needed | 100 | 67.73% | 60 | system_stayed_positive_but_loss |
| 002555.SZ | 三七互娱 | 2025-09-10 | 2025-09-11 | 1 | -15.0 | -0.67% | confirmation_needed | 100 | 100 | watch | observe | 95.39% | 0.0542 | 0.0883 | 57 | valid | neutral | 133.092 | defense_first | 100 | 37.83% | 57 | buy_looked_good_then_broke |
| 000959.SZ | 首钢股份 | 2025-07-08 | 2025-07-10 | 2 | -13.64 | -0.50% | confirmation_needed | 100 | 100 | watch | observe | 96.22% | 0.0743 | 0.1046 | 62 | suspicious | gentle_expand | 127.944 | confirmation_needed | 100 | 73.65% | 59 | system_stayed_positive_but_loss |
| 600111.SH | 北方稀土 | 2025-07-21 | 2025-07-23 | 2 | -6.0 | -0.17% | confirmation_needed | 100 | 100 | watch | observe | 90.21% | 0.0842 | 0.096 | 62 | valid | neutral | 122.45 | defense_first | 100 | 87.61% | 65 | buy_looked_good_then_broke |
| 000959.SZ | 首钢股份 | 2025-07-09 | 2025-07-10 | 1 | -1.36 | 1.26% | confirmation_needed | 100 | 93 | watch | observe | 92.77% | 0.8881 | 0.0808 | 59 | suspicious | neutral | 125.072 | confirmation_needed | 100 | 73.65% | 59 | system_stayed_positive_but_loss |