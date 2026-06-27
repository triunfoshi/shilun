# Graph Report - /Users/shibicheng/shilun_standalone  (2026-06-25)

## Corpus Check
- 86 files · ~3,078,114 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1818 nodes · 4605 edges · 91 communities detected
- Extraction: 69% EXTRACTED · 31% INFERRED · 0% AMBIGUOUS · INFERRED: 1434 edges (avg confidence: 0.71)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 83|Community 83]]
- [[_COMMUNITY_Community 84|Community 84]]
- [[_COMMUNITY_Community 85|Community 85]]
- [[_COMMUNITY_Community 86|Community 86]]
- [[_COMMUNITY_Community 87|Community 87]]
- [[_COMMUNITY_Community 88|Community 88]]
- [[_COMMUNITY_Community 89|Community 89]]
- [[_COMMUNITY_Community 90|Community 90]]

## God Nodes (most connected - your core abstractions)
1. `MongoSnapshotStore` - 142 edges
2. `DailyPushJob` - 72 edges
3. `ShilunPipeline` - 67 edges
4. `AppConfig` - 67 edges
5. `DailyPushRequest` - 56 edges
6. `TushareSyncJob` - 55 edges
7. `mean()` - 44 edges
8. `RawMarketDataStore` - 42 edges
9. `Project helper scripts.` - 40 edges
10. `TushareSyncRequest` - 39 edges

## Surprising Connections (you probably didn't know these)
- `ShilunPipeline` --uses--> `Run full-market feature, structure, and recommendation snapshots in batch.`  [INFERRED]
  /Users/shibicheng/shilun_standalone/shilun/pipeline.py → /Users/shibicheng/shilun_standalone/shilun/jobs/snapshot_job.py
- `AppConfig` --uses--> `Build daily candidate pool states from Mongo market snapshot records.`  [INFERRED]
  /Users/shibicheng/shilun_standalone/shilun/common/config.py → /Users/shibicheng/shilun_standalone/shilun/jobs/candidate_pool_job.py
- `Project helper scripts.` --uses--> `PipelineConfig`  [INFERRED]
  /Users/shibicheng/shilun_standalone/scripts/__init__.py → /Users/shibicheng/shilun_standalone/shilun/pipeline.py
- `Project helper scripts.` --uses--> `ShilunPipeline`  [INFERRED]
  /Users/shibicheng/shilun_standalone/scripts/__init__.py → /Users/shibicheng/shilun_standalone/shilun/pipeline.py
- `Project helper scripts.` --uses--> `StructureFeatureBuilder`  [INFERRED]
  /Users/shibicheng/shilun_standalone/scripts/__init__.py → /Users/shibicheng/shilun_standalone/shilun/features/structure_features.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.02
Nodes (82): AkshareClient, AkshareConfig, _bypass_proxy_if_needed(), AkShare 数据源（单一来源策略）。  对每类增强数据只保留一个最稳定的 akshare 接口，不做多源回退： - 涨停池：stock_zt_pool_em, 获取指定日期的涨停池（东方财富，含连板数/封板时间/炸板次数/所属行业）。          Args:             trade_date: yyy, 获取所有概念板块的当日涨跌幅、领涨股、资金流。          akshare 1.18 的 stock_board_concept_name_em 没传 R, 获取北向资金历史净流入。          注：接口最近若干天可能返回 nan（接口本身延迟），         调用方应对 nan 做空值过滤，不要直接报错。, 临时屏蔽所有代理（环境变量 + macOS 系统代理）。      macOS 上即使没有 http_proxy 环境变量，requests/urllib 仍会 (+74 more)

### Community 1 - "Community 1"
Cohesion: 0.04
Nodes (69): AkshareFetchError, AutomationPushJob, AutomationPushRequest, AutomationPushResult, discover_telegram_chat_ids(), main(), _today_text(), _validate_required_channels() (+61 more)

### Community 2 - "Community 2"
Cohesion: 0.03
Nodes (53): _env(), load_config(), _parse_chat_ids(), _read_env_file(), analyze(), AnalyzeRequest, _build_market_overview(), build_telegram_reply() (+45 more)

### Community 3 - "Community 3"
Cohesion: 0.03
Nodes (72): build_label_lookup(), _build_model(), _clip_prob(), _coalesce(), DatasetBuilder, DatasetSplit, EntryCurvePrediction, EventPrediction (+64 more)

### Community 4 - "Community 4"
Cohesion: 0.04
Nodes (120): etf_after_close_regime_statistics(), etf_afternoon_routine(), etf_apply_filters(), etf_build_short_momentum_3day_pattern_str(), etf_calculate_all_metrics_for_etf(), etf_calculate_and_log_ranked_etfs(), etf_calculate_global_etf_threshold(), etf_calculate_momentum_score() (+112 more)

### Community 5 - "Community 5"
Cohesion: 0.04
Nodes (68): Placeholder importer so Mongo-first analysis never builds a Tushare client., Single-stock analysis service that reads market data from Mongo first., DS-friendly entry:     user only provides ticker + date; pipeline handles data->, main(), parse_args(), _clip(), compute_entry_features(), Compute online trigger features using only the current and prior bars. (+60 more)

### Community 6 - "Community 6"
Cohesion: 0.06
Nodes (94): activeTabId(), advanceSectorProgressToLeaders(), chineseJsonValue(), colorPercent(), completeSectorProgress(), definitionBubble(), divergenceExplanation(), escapeHtml() (+86 more)

### Community 7 - "Community 7"
Cohesion: 0.04
Nodes (58): Bi, BiBuilder, _build_segment(), Center, CenterDetector, _clip(), _clip_int(), _collect_confirmation_evidence() (+50 more)

### Community 8 - "Community 8"
Cohesion: 0.05
Nodes (57): ActionDecision, ActionMapper, _build_action_block(), build_case_review(), _build_conclusion_line(), build_llm_payload(), build_payload(), _build_probability_block() (+49 more)

### Community 9 - "Community 9"
Cohesion: 0.06
Nodes (56): BacktestAttributionResult, build_backtest_attribution(), build_industry_attribution(), build_regime_attribution(), build_tag_attribution(), _compound(), _format_table_value(), _markdown_table() (+48 more)

### Community 10 - "Community 10"
Cohesion: 0.04
Nodes (34): JoinQuantStrategyExportConfig, main(), parse_args(), render_joinquant_strategy(), _render_stock_pool_literal(), CandidatePoolDecision, CandidatePoolStatus, classify_candidate_pool() (+26 more)

### Community 11 - "Community 11"
Cohesion: 0.05
Nodes (38): build_default_strategy_registry(), format_candidate_tag_reasons(), format_candidate_tags(), format_strategy_ids(), format_strategy_signal_reasons(), format_strategy_validation_paths(), format_strategy_versions(), Ordered registry for versioned strategy definitions. (+30 more)

### Community 12 - "Community 12"
Cohesion: 0.07
Nodes (61): _action_permission(), _amount_yi_text(), benchmark_index_meta(), _breadth_count_judgement(), _breadth_state_conclusion(), _breadth_state_section(), _build_breadth_context(), _build_hard_triggers() (+53 more)

### Community 13 - "Community 13"
Cohesion: 0.07
Nodes (59): account_profile(), analyze_security(), average_true_range(), build_analysis_map(), build_target_weights(), clip_float(), clip_int(), current_position_amount() (+51 more)

### Community 14 - "Community 14"
Cohesion: 0.07
Nodes (58): account_profile(), analyze_security(), average_true_range(), build_analysis_map(), build_target_weights(), clip_float(), clip_int(), current_position_amount() (+50 more)

### Community 15 - "Community 15"
Cohesion: 0.1
Nodes (50): mean(), _attach_sector_rolling(), _attach_stock_leader_rolling(), _build_daily_leaders(), _build_leader_summary(), _build_sector_history(), _build_stock_profiles(), _build_summary() (+42 more)

### Community 16 - "Community 16"
Cohesion: 0.15
Nodes (28): classify_strategy(), _copy_validation_metrics(), expand_strategy_signals(), _format_percent(), _join_values(), load_strategy_records_from_mongo(), main(), normalize_date_text() (+20 more)

### Community 17 - "Community 17"
Cohesion: 0.13
Nodes (19): AtomicRule, build_default_candidate_tag_registry(), CandidateTag, _evaluate_dividend_quality(), _evaluate_earnings_surprise(), _evaluate_high_tight_flag(), _evaluate_rps_breakout(), _evaluate_turtle_breakout() (+11 more)

### Community 18 - "Community 18"
Cohesion: 0.22
Nodes (12): build_target_weights(), build_trade_signal(), JoinQuantSignal, JoinQuantSignalPolicyConfig, normalize_joinquant_bars(), _resolve_target_weight(), _split_code(), to_joinquant_symbol() (+4 more)

### Community 19 - "Community 19"
Cohesion: 0.22
Nodes (4): classify_market_permission(), MarketPermission, MarketScores, Part1HardTrigger

### Community 20 - "Community 20"
Cohesion: 0.39
Nodes (8): build_candidates(), _calc_rsi(), detect_ma5_signal(), _f(), _ma20_slope_up(), 从 top_sectors 的 leader_candidates + zhongjun_candidates 中筛出 MA5 趋势战法候选。     stoc, MA20[today] > MA20[5日前]，确认主升段。, 输入：按日期升序的近 20 根日线，每个 dict 含 close/ma5/ma10/ma20/volume/volume_ma5。     输出：signal

### Community 21 - "Community 21"
Cohesion: 1.0
Nodes (0): 

### Community 22 - "Community 22"
Cohesion: 1.0
Nodes (0): 

### Community 23 - "Community 23"
Cohesion: 1.0
Nodes (1): 输入：按日期升序的近 10 根日线，每个 dict 含 close/ma5/ma10/ma20/volume/volume_ma5。     输出：signal

### Community 24 - "Community 24"
Cohesion: 1.0
Nodes (1): 从 top_sectors 的 leader_candidates + zhongjun_candidates 中筛出 5日线战法候选。     stock_f

### Community 25 - "Community 25"
Cohesion: 1.0
Nodes (1): Precompute rolling leader features once for multi-day leaderboard queries.

### Community 26 - "Community 26"
Cohesion: 1.0
Nodes (1): Select sectors worth stock-level deep scoring before expensive profile builds.

### Community 27 - "Community 27"
Cohesion: 1.0
Nodes (1): Build a 5-day trend board without recomputing stock-level profiles.

### Community 28 - "Community 28"
Cohesion: 1.0
Nodes (1): Evaluate Part2 sector/theme momentum from synced daily data.      v1 intentional

### Community 29 - "Community 29"
Cohesion: 1.0
Nodes (1): Select sectors worth stock-level deep scoring before expensive profile builds.

### Community 30 - "Community 30"
Cohesion: 1.0
Nodes (1): Build a 5-day trend board without recomputing stock-level profiles.

### Community 31 - "Community 31"
Cohesion: 1.0
Nodes (1): Build a 5-day trend board without recomputing stock-level profiles.

### Community 32 - "Community 32"
Cohesion: 1.0
Nodes (1): Evaluate Part2 sector/theme momentum from synced daily data.      v1 intentional

### Community 33 - "Community 33"
Cohesion: 1.0
Nodes (1): Mongo interface for ranked market snapshots and analysis payloads.

### Community 34 - "Community 34"
Cohesion: 1.0
Nodes (1): Mongo interface for candidate-pool states and events.

### Community 35 - "Community 35"
Cohesion: 1.0
Nodes (1): Evaluate Part2 sector/theme momentum from synced daily data.      v1 intentional

### Community 36 - "Community 36"
Cohesion: 1.0
Nodes (1): Evaluate PART1 market permission from daily bars.      This is the active Python

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (1): Normalize and validate daily OHLCV bars before analysis.

### Community 38 - "Community 38"
Cohesion: 1.0
Nodes (1): Normalize and validate daily OHLCV bars before analysis.

### Community 39 - "Community 39"
Cohesion: 1.0
Nodes (1): Mongo interface for raw synced market data.      M5 P3 改进点：同步任务和 Mongo-first pro

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (1): Mongo interface for ranked market snapshots and analysis payloads.

### Community 41 - "Community 41"
Cohesion: 1.0
Nodes (1): Mongo interface for candidate-pool states and events.

### Community 42 - "Community 42"
Cohesion: 1.0
Nodes (1): DS-friendly Mongo-first entry:     user provides ticker + date; synced Mongo dat

### Community 43 - "Community 43"
Cohesion: 1.0
Nodes (1): Single-entry analysis pipeline for data-science-friendly usage.

### Community 44 - "Community 44"
Cohesion: 1.0
Nodes (1): Shared final assembly for Tushare-loaded and Mongo-first analyses.

### Community 45 - "Community 45"
Cohesion: 1.0
Nodes (1): Binary LightGBM models for continuation, breakout success, and failure risk.

### Community 46 - "Community 46"
Cohesion: 1.0
Nodes (1): Filesystem-based registry for trained LightGBM artifacts.

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (1): Fit an entry probability curve from semantic states and model outputs.

### Community 48 - "Community 48"
Cohesion: 1.0
Nodes (1): Deterministic scoring layer that mimics model outputs before trained models exis

### Community 49 - "Community 49"
Cohesion: 1.0
Nodes (1): Build supervised modeling datasets from feature and label tables.

### Community 50 - "Community 50"
Cohesion: 1.0
Nodes (1): Multiclass regime classifier backed by LightGBM.

### Community 51 - "Community 51"
Cohesion: 1.0
Nodes (1): Regression models for expected return and expected drawdown.

### Community 52 - "Community 52"
Cohesion: 1.0
Nodes (1): Render constrained analyst text from structured fields.

### Community 53 - "Community 53"
Cohesion: 1.0
Nodes (1): Return a plain dict that can be sent to OpenClaw/LLM.

### Community 54 - "Community 54"
Cohesion: 1.0
Nodes (1): Map semantic decision snapshots to constrained actions.

### Community 55 - "Community 55"
Cohesion: 1.0
Nodes (1): 先匹配结论         再补理由码         再补确认事项         再生成风险总结         最后拼成 ActionDecision

### Community 56 - "Community 56"
Cohesion: 1.0
Nodes (1): Single-entry analysis pipeline for data-science-friendly usage.

### Community 57 - "Community 57"
Cohesion: 1.0
Nodes (1): Shared final assembly for Tushare-loaded and Mongo-first analyses.

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (1): 计算趋势类特征。      设计原则：     1. 保留项目原来已经在用的字段名，避免后面 pipeline 接不上     2. 同时新增更明确的 5/20

### Community 59 - "Community 59"
Cohesion: 1.0
Nodes (1): 定义波动率和量能     ATR14（平均真实波动幅度）：14日移动平均TR     TR:max(当日最高-最低，abs(最高-昨日收盘),abs(最低-昨日

### Community 60 - "Community 60"
Cohesion: 1.0
Nodes (1): Build higher-level segments from contiguous bis with the same direction.

### Community 61 - "Community 61"
Cohesion: 1.0
Nodes (1): Detect pivot highs and pivot lows.

### Community 62 - "Community 62"
Cohesion: 1.0
Nodes (1): Pseudocode:         1. Slide a fixed window across rows         2. Mark pivot hi

### Community 63 - "Community 63"
Cohesion: 1.0
Nodes (1): Detect overlap zones from consecutive swings.

### Community 64 - "Community 64"
Cohesion: 1.0
Nodes (1): Compatibility layer.         New code should use ``CenterDetector`` directly.

### Community 65 - "Community 65"
Cohesion: 1.0
Nodes (1): Adaptive structure evaluator built on price, structure objects, and market conte

### Community 66 - "Community 66"
Cohesion: 1.0
Nodes (1): Detect simple momentum divergence between the latest same-direction segments.

### Community 67 - "Community 67"
Cohesion: 1.0
Nodes (1): Detect breakout and failure events from active zones.

### Community 68 - "Community 68"
Cohesion: 1.0
Nodes (1): Pseudocode:         1. Pick the latest active zone         2. Check close agains

### Community 69 - "Community 69"
Cohesion: 1.0
Nodes (1): Build swings from alternating pivots.

### Community 70 - "Community 70"
Cohesion: 1.0
Nodes (1): Compatibility layer.         New code should use ``BiBuilder`` directly.

### Community 71 - "Community 71"
Cohesion: 1.0
Nodes (1): Detect overlap centers from rolling segment windows.

### Community 72 - "Community 72"
Cohesion: 1.0
Nodes (1): Build simplified Chan-style bi structures from alternating pivots.

### Community 73 - "Community 73"
Cohesion: 1.0
Nodes (1): Classify a ranked snapshot record into one of the four phase-3 pools.

### Community 74 - "Community 74"
Cohesion: 1.0
Nodes (1): Runtime inputs shared by atomic candidate rules.

### Community 75 - "Community 75"
Cohesion: 1.0
Nodes (1): A strategy-version hit for one snapshot record.

### Community 76 - "Community 76"
Cohesion: 1.0
Nodes (1): Versioned strategy definition built from fields and candidate tags.

### Community 77 - "Community 77"
Cohesion: 1.0
Nodes (1): Ordered registry for atomic rules.      The registry is deliberately lightweight

### Community 78 - "Community 78"
Cohesion: 1.0
Nodes (1): Compatibility module that re-exports JoinQuant adapter and policy helpers.

### Community 79 - "Community 79"
Cohesion: 1.0
Nodes (1): A non-ranking observation tag produced by the phase-3 rule center.

### Community 80 - "Community 80"
Cohesion: 1.0
Nodes (1): Smallest explainable rule unit for candidate tag generation.

### Community 81 - "Community 81"
Cohesion: 1.0
Nodes (1): Build a simple equal-weight portfolio from ranked candidates.

### Community 82 - "Community 82"
Cohesion: 1.0
Nodes (1): Convert snapshot rows into rankable portfolio candidates.

### Community 83 - "Community 83"
Cohesion: 1.0
Nodes (1): 1.关键列不缺失         2.按股票代码和日期去重         3.去掉没有关键数据的行         4.OHLC规则         5.按股

### Community 84 - "Community 84"
Cohesion: 1.0
Nodes (1): Pseudocode:         1. Convert date to datetime         2. Group by ticker and w

### Community 85 - "Community 85"
Cohesion: 1.0
Nodes (1): 二期主链路统一为 universe -> features -> factors -> ranking -> portfolio -> execution/backtest。

### Community 86 - "Community 86"
Cohesion: 1.0
Nodes (1): 研究型回测是二期的研究证明主战场。

### Community 87 - "Community 87"
Cohesion: 1.0
Nodes (1): 聚宽侧只承载单文件、轻策略、偏执行型脚本，并与本地研究线拆开。

### Community 88 - "Community 88"
Cohesion: 1.0
Nodes (1): 系统应从单票分析引擎升级为轻量组合策略系统。

### Community 89 - "Community 89"
Cohesion: 1.0
Nodes (1): 策略深化围绕因子、量价、筹码、风险与市场/板块过滤展开。

### Community 90 - "Community 90"
Cohesion: 1.0
Nodes (1): 代码减法优先，避免继续堆叠平行评分链与临时旁路。

## Knowledge Gaps
- **155 isolated node(s):** `Single-entry analysis pipeline for data-science-friendly usage.`, `Shared final assembly for Tushare-loaded and Mongo-first analyses.`, `Classify a ranked snapshot record into one of the four phase-3 pools.`, `Runtime record used by strategy definitions.`, `A strategy-version hit for one snapshot record.` (+150 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 21`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 22`** (1 nodes): `lib.rs`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 23`** (1 nodes): `输入：按日期升序的近 10 根日线，每个 dict 含 close/ma5/ma10/ma20/volume/volume_ma5。     输出：signal`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 24`** (1 nodes): `从 top_sectors 的 leader_candidates + zhongjun_candidates 中筛出 5日线战法候选。     stock_f`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 25`** (1 nodes): `Precompute rolling leader features once for multi-day leaderboard queries.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 26`** (1 nodes): `Select sectors worth stock-level deep scoring before expensive profile builds.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 27`** (1 nodes): `Build a 5-day trend board without recomputing stock-level profiles.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 28`** (1 nodes): `Evaluate Part2 sector/theme momentum from synced daily data.      v1 intentional`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 29`** (1 nodes): `Select sectors worth stock-level deep scoring before expensive profile builds.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 30`** (1 nodes): `Build a 5-day trend board without recomputing stock-level profiles.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 31`** (1 nodes): `Build a 5-day trend board without recomputing stock-level profiles.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 32`** (1 nodes): `Evaluate Part2 sector/theme momentum from synced daily data.      v1 intentional`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 33`** (1 nodes): `Mongo interface for ranked market snapshots and analysis payloads.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 34`** (1 nodes): `Mongo interface for candidate-pool states and events.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 35`** (1 nodes): `Evaluate Part2 sector/theme momentum from synced daily data.      v1 intentional`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 36`** (1 nodes): `Evaluate PART1 market permission from daily bars.      This is the active Python`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (1 nodes): `Normalize and validate daily OHLCV bars before analysis.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 38`** (1 nodes): `Normalize and validate daily OHLCV bars before analysis.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (1 nodes): `Mongo interface for raw synced market data.      M5 P3 改进点：同步任务和 Mongo-first pro`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `Mongo interface for ranked market snapshots and analysis payloads.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (1 nodes): `Mongo interface for candidate-pool states and events.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (1 nodes): `DS-friendly Mongo-first entry:     user provides ticker + date; synced Mongo dat`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (1 nodes): `Single-entry analysis pipeline for data-science-friendly usage.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (1 nodes): `Shared final assembly for Tushare-loaded and Mongo-first analyses.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (1 nodes): `Binary LightGBM models for continuation, breakout success, and failure risk.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 46`** (1 nodes): `Filesystem-based registry for trained LightGBM artifacts.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (1 nodes): `Fit an entry probability curve from semantic states and model outputs.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (1 nodes): `Deterministic scoring layer that mimics model outputs before trained models exis`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 49`** (1 nodes): `Build supervised modeling datasets from feature and label tables.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (1 nodes): `Multiclass regime classifier backed by LightGBM.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 51`** (1 nodes): `Regression models for expected return and expected drawdown.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 52`** (1 nodes): `Render constrained analyst text from structured fields.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 53`** (1 nodes): `Return a plain dict that can be sent to OpenClaw/LLM.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 54`** (1 nodes): `Map semantic decision snapshots to constrained actions.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 55`** (1 nodes): `先匹配结论         再补理由码         再补确认事项         再生成风险总结         最后拼成 ActionDecision`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 56`** (1 nodes): `Single-entry analysis pipeline for data-science-friendly usage.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 57`** (1 nodes): `Shared final assembly for Tushare-loaded and Mongo-first analyses.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 58`** (1 nodes): `计算趋势类特征。      设计原则：     1. 保留项目原来已经在用的字段名，避免后面 pipeline 接不上     2. 同时新增更明确的 5/20`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 59`** (1 nodes): `定义波动率和量能     ATR14（平均真实波动幅度）：14日移动平均TR     TR:max(当日最高-最低，abs(最高-昨日收盘),abs(最低-昨日`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 60`** (1 nodes): `Build higher-level segments from contiguous bis with the same direction.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 61`** (1 nodes): `Detect pivot highs and pivot lows.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 62`** (1 nodes): `Pseudocode:         1. Slide a fixed window across rows         2. Mark pivot hi`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 63`** (1 nodes): `Detect overlap zones from consecutive swings.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 64`** (1 nodes): `Compatibility layer.         New code should use ``CenterDetector`` directly.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 65`** (1 nodes): `Adaptive structure evaluator built on price, structure objects, and market conte`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 66`** (1 nodes): `Detect simple momentum divergence between the latest same-direction segments.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 67`** (1 nodes): `Detect breakout and failure events from active zones.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 68`** (1 nodes): `Pseudocode:         1. Pick the latest active zone         2. Check close agains`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 69`** (1 nodes): `Build swings from alternating pivots.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 70`** (1 nodes): `Compatibility layer.         New code should use ``BiBuilder`` directly.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 71`** (1 nodes): `Detect overlap centers from rolling segment windows.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 72`** (1 nodes): `Build simplified Chan-style bi structures from alternating pivots.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 73`** (1 nodes): `Classify a ranked snapshot record into one of the four phase-3 pools.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 74`** (1 nodes): `Runtime inputs shared by atomic candidate rules.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 75`** (1 nodes): `A strategy-version hit for one snapshot record.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 76`** (1 nodes): `Versioned strategy definition built from fields and candidate tags.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 77`** (1 nodes): `Ordered registry for atomic rules.      The registry is deliberately lightweight`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 78`** (1 nodes): `Compatibility module that re-exports JoinQuant adapter and policy helpers.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 79`** (1 nodes): `A non-ranking observation tag produced by the phase-3 rule center.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 80`** (1 nodes): `Smallest explainable rule unit for candidate tag generation.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 81`** (1 nodes): `Build a simple equal-weight portfolio from ranked candidates.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 82`** (1 nodes): `Convert snapshot rows into rankable portfolio candidates.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 83`** (1 nodes): `1.关键列不缺失         2.按股票代码和日期去重         3.去掉没有关键数据的行         4.OHLC规则         5.按股`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 84`** (1 nodes): `Pseudocode:         1. Convert date to datetime         2. Group by ticker and w`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 85`** (1 nodes): `二期主链路统一为 universe -> features -> factors -> ranking -> portfolio -> execution/backtest。`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 86`** (1 nodes): `研究型回测是二期的研究证明主战场。`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 87`** (1 nodes): `聚宽侧只承载单文件、轻策略、偏执行型脚本，并与本地研究线拆开。`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 88`** (1 nodes): `系统应从单票分析引擎升级为轻量组合策略系统。`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 89`** (1 nodes): `策略深化围绕因子、量价、筹码、风险与市场/板块过滤展开。`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 90`** (1 nodes): `代码减法优先，避免继续堆叠平行评分链与临时旁路。`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Project helper scripts.` connect `Community 10` to `Community 0`, `Community 1`, `Community 2`, `Community 3`, `Community 5`, `Community 7`, `Community 8`, `Community 11`, `Community 18`?**
  _High betweenness centrality (0.204) - this node is a cross-community bridge._
- **Why does `MongoSnapshotStore` connect `Community 0` to `Community 1`, `Community 2`, `Community 5`, `Community 9`, `Community 10`, `Community 11`, `Community 16`?**
  _High betweenness centrality (0.175) - this node is a cross-community bridge._
- **Why does `mean()` connect `Community 15` to `Community 2`, `Community 3`, `Community 4`, `Community 5`, `Community 7`, `Community 9`, `Community 11`, `Community 12`, `Community 14`, `Community 16`, `Community 20`?**
  _High betweenness centrality (0.118) - this node is a cross-community bridge._
- **Are the 108 inferred relationships involving `MongoSnapshotStore` (e.g. with `TelegramBotClient` and `TelegramChat`) actually correct?**
  _`MongoSnapshotStore` has 108 INFERRED edges - model-reasoned connections that need verification._
- **Are the 59 inferred relationships involving `DailyPushJob` (e.g. with `TelegramBotClient` and `TelegramChat`) actually correct?**
  _`DailyPushJob` has 59 INFERRED edges - model-reasoned connections that need verification._
- **Are the 33 inferred relationships involving `ShilunPipeline` (e.g. with `Project helper scripts.` and `SnapshotJobRequest`) actually correct?**
  _`ShilunPipeline` has 33 INFERRED edges - model-reasoned connections that need verification._
- **Are the 65 inferred relationships involving `AppConfig` (e.g. with `SnapshotJobRequest` and `SnapshotJobResult`) actually correct?**
  _`AppConfig` has 65 INFERRED edges - model-reasoned connections that need verification._