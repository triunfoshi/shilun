# 交易复盘与石论评分对照

- 闭环交易数: 210
- 亏损交易数: 130
- 买入日完整版石论给出正向结论且最终亏损: 49
- 其中买入日强看多且最终亏损: 0
- 卖出日系统仍偏正向但实际已亏损离场: 46
- 新 trigger 层理论可过滤的亏损交易: 130
- 新 trigger 层会同时过滤掉的盈利交易: 80

## 重点案例

| ticker | name | buy_date | sell_date | hold_days | net_pnl | gross_return | buy_conclusion_label | buy_structure_score | buy_trigger_state | buy_opportunity_type | buy_entry_probability | buy_entry_zone | buy_p_continue_10d | buy_p_acceptance_1d | buy_p_fail_fast_3d | buy_risk_score | buy_breakout_quality | buy_volume_pattern | buy_rank_score | sell_conclusion_label | sell_structure_score | sell_p_continue_10d | sell_risk_score | mismatch_type |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 600629.SH | 华建集团 | 2025-10-22 | 2025-10-28 | 6 | -1272.0 | -34.48% | confirmation_needed | 100 | watch | observe | 0.6413 | candidate | 52.79% | 0.653 | 0.1926 | 69 | suspicious | pullback_shrink | -6.1395 | defense_first | 87 | 92.64% | 95 | buy_looked_good_then_broke |
| 000818.SZ | 航锦科技 | 2025-02-21 | 2025-02-26 | 5 | -928.0 | -22.73% | confirmation_needed | 100 | watch | observe | 0.4416 | watch | 3.09% | 0.0318 | 0.8963 | 72 | suspicious | neutral | -70.288 | confirmation_needed | 100 | 68.82% | 75 | system_stayed_positive_but_loss |
| 600120.SH | 浙江东方 | 2025-02-13 | 2025-02-17 | 4 | -590.0 | -13.39% | confirmation_needed | 100 | watch | observe | 0.3709 | avoid | 1.52% | 0.0106 | 0.9514 | 64 | suspicious | neutral | -69.0895 | defense_first | 100 | 54.84% | 86 | buy_looked_good_then_broke |
| 601600.SH | 中国铝业 | 2025-11-13 | 2025-11-17 | 4 | -297.0 | -8.15% | confirmation_needed | 100 | watch | observe | 0.4451 | watch | 3.42% | 0.1641 | 0.8802 | 74 | suspicious | gentle_expand | -71.0985 | defense_first | 86 | 62.37% | 76 | buy_looked_good_then_broke |
| 600588.SH | 用友网络 | 2026-01-15 | 2026-01-16 | 1 | -284.0 | -8.14% | confirmation_needed | 100 | watch | observe | 0.4367 | watch | 42.59% | 0.2359 | 0.7077 | 70 | suspicious | impulsive_spike | -49.2925 | defense_first | 100 | 35.43% | 81 | buy_looked_good_then_broke |
| 002273.SZ | 水晶光电 | 2025-02-27 | 2025-03-03 | 4 | -271.0 | -9.95% | confirmation_needed | 100 | watch | observe | 0.4643 | watch | 3.57% | 0.0171 | 0.9206 | 64 | suspicious | gentle_expand | -62.6285 | defense_first | 77 | 28.60% | 72 | buy_looked_good_then_broke |
| 600875.SH | 东方电气 | 2025-07-24 | 2025-07-25 | 1 | -232.0 | -9.98% | confirmation_needed | 100 | watch | observe | 0.2484 | avoid | 9.74% | 0.1411 | 0.9255 | 64 | suspicious | impulsive_spike | -71.404 | confirmation_needed | 100 | 13.02% | 69 | system_stayed_positive_but_loss |
| 000559.SZ | 万向钱潮 | 2025-05-13 | 2025-05-14 | 1 | -228.0 | -5.99% | confirmation_needed | 100 | watch | observe | 0.2732 | avoid | 8.78% | 0.0209 | 0.8725 | 74 | suspicious | impulsive_spike | -77.3 | confirmation_needed | 100 | 4.22% | 79 | system_stayed_positive_but_loss |
| 600570.SH | 恒生电子 | 2025-02-14 | 2025-02-18 | 4 | -225.0 | -6.43% | confirmation_needed | 100 | watch | observe | 0.3777 | avoid | 10.34% | 0.0348 | 0.918 | 69 | suspicious | impulsive_spike | -70.0155 | defense_first | 96 | 44.14% | 72 | buy_looked_good_then_broke |
| 600549.SH | 厦门钨业 | 2025-10-29 | 2025-10-31 | 2 | -199.0 | -5.30% | confirmation_needed | 100 | watch | observe | 0.451 | watch | 49.87% | 0.0759 | 0.4246 | 68 | suspicious | gentle_expand | -28.207 | defense_first | 100 | 55.10% | 72 | buy_looked_good_then_broke |
| 601608.SH | 中信重工 | 2025-10-23 | 2025-10-24 | 1 | -185.0 | -5.12% | confirmation_needed | 100 | watch | observe | 0.2928 | avoid | 4.23% | 0.1123 | 0.9673 | 79 | suspicious | neutral | -88.016 | confirmation_needed | 100 | 5.02% | 80 | system_stayed_positive_but_loss |
| 601696.SH | 中银证券 | 2025-07-14 | 2025-07-15 | 1 | -180.0 | -4.61% | confirmation_needed | 100 | watch | observe | 0.208 | avoid | 73.34% | 0.0767 | 0.963 | 68 | suspicious | neutral | -66.752 | confirmation_needed | 100 | 74.86% | 69 | system_stayed_positive_but_loss |
| 000630.SZ | 铜陵有色 | 2026-01-30 | 2026-02-03 | 4 | -168.0 | -5.07% | confirmation_needed | 100 | watch | observe | 0.4612 | watch | 36.65% | 0.1003 | 0.9619 | 69 | suspicious | neutral | -63.63 | defense_first | 100 | 38.02% | 73 | buy_looked_good_then_broke |
| 601216.SH | 君正集团 | 2025-02-14 | 2025-02-17 | 3 | -168.0 | -3.35% | confirmation_needed | 100 | watch | observe | 0.3738 | avoid | 12.46% | 0.0489 | 0.952 | 70 | suspicious | neutral | -72.807 | defense_first | 100 | 33.97% | 71 | buy_looked_good_then_broke |
| 600967.SH | 内蒙一机 | 2025-07-22 | 2025-07-23 | 1 | -138.0 | -3.48% | confirmation_needed | 100 | watch | observe | 0.391 | avoid | 97.59% | 0.0594 | 0.7818 | 71 | suspicious | neutral | -45.795 | defense_first | 100 | 86.61% | 72 | buy_looked_good_then_broke |
| 002074.SZ | 国轩高科 | 2025-06-25 | 2025-06-27 | 2 | -137.0 | -4.08% | confirmation_needed | 100 | watch | observe | 0.2707 | avoid | 6.25% | 0.0719 | 0.0859 | 72 | suspicious | impulsive_spike | -28.7225 | defense_first | 100 | 8.48% | 72 | buy_looked_good_then_broke |
| 603529.SH | 爱玛科技 | 2025-03-21 | 2025-03-25 | 4 | -133.0 | -2.92% | confirmation_needed | 100 | watch | observe | 0.4813 | watch | 2.53% | 0.0683 | 0.9025 | 75 | suspicious | neutral | -71.9855 | defense_first | 100 | 73.30% | 67 | buy_looked_good_then_broke |
| 600580.SH | 卧龙电驱 | 2025-02-13 | 2025-02-14 | 1 | -124.0 | -2.44% | confirmation_needed | 100 | watch | observe | 0.6908 | candidate | 91.25% | 0.1264 | 0.0983 | 69 | suspicious | neutral | 9.438 | confirmation_needed | 100 | 88.69% | 69 | system_stayed_positive_but_loss |
| 002709.SZ | 天赐材料 | 2025-09-09 | 2025-09-10 | 1 | -123.0 | -4.24% | confirmation_needed | 100 | watch | observe | 0.4806 | watch | 88.67% | 0.0413 | 0.8445 | 72 | suspicious | impulsive_spike | -48.309 | defense_first | 100 | 85.09% | 73 | buy_looked_good_then_broke |
| 600871.SH | 石化油服 | 2025-10-23 | 2025-10-24 | 1 | -120.0 | -3.21% | confirmation_needed | 100 | watch | observe | 0.2061 | avoid | 28.41% | 0.0406 | 0.6888 | 73 | suspicious | neutral | -64.3715 | confirmation_needed | 100 | 85.83% | 74 | system_stayed_positive_but_loss |
| 000807.SZ | 云铝股份 | 2025-03-07 | 2025-03-11 | 4 | -118.0 | -3.18% | confirmation_needed | 100 | watch | observe | 0.3776 | avoid | 4.59% | 0.0305 | 0.1462 | 59 | suspicious | gentle_expand | -14.862 | defense_first | 94 | 35.19% | 65 | buy_looked_good_then_broke |
| 600547.SH | 山东黄金 | 2025-03-20 | 2025-03-24 | 4 | -111.0 | -4.08% | confirmation_needed | 100 | watch | observe | 0.403 | watch | 1.90% | 0.025 | 0.9215 | 54 | suspicious | neutral | -55.775 | defense_first | 86 | 32.42% | 65 | buy_looked_good_then_broke |
| 600588.SH | 用友网络 | 2025-02-14 | 2025-02-17 | 3 | -108.0 | -2.96% | confirmation_needed | 100 | watch | observe | 0.471 | watch | 3.15% | 0.0401 | 0.937 | 70 | suspicious | impulsive_spike | -69.395 | defense_first | 100 | 12.01% | 74 | buy_looked_good_then_broke |
| 000975.SZ | 山金国际 | 2025-03-20 | 2025-03-24 | 4 | -106.0 | -2.82% | confirmation_needed | 99 | watch | observe | 0.4679 | watch | 1.80% | 0.0772 | 0.8197 | 60 | suspicious | neutral | -53.1165 | defense_first | 90 | 27.51% | 67 | buy_looked_good_then_broke |
| 000932.SZ | 华菱钢铁 | 2025-07-08 | 2025-07-16 | 8 | -96.0 | -2.30% | confirmation_needed | 100 | watch | observe | 0.3645 | avoid | 90.54% | 0.0337 | 0.0404 | 59 | suspicious | gentle_expand | 8.0865 | confirmation_needed | 86 | 81.94% | 58 | system_stayed_positive_but_loss |