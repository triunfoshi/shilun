-- Report source queries for the SSE Composite big-up-day intraday study.
-- Run with: sqlite3 output/sse_big_up_intraday_YYYYMMDD_YYYYMMDD/analysis.sqlite < this_file

-- Individual event paths, sampled at five-minute anchors for the report chart.
SELECT trade_date, minute_index, minute_label, return_vs_prev_close
FROM chart_individual_paths
ORDER BY trade_date, minute_index;

-- Median and interquartile normalized-body paths for the report chart.
SELECT minute_index, minute_label, sample_count, statistic, normalized_body
FROM chart_aggregate_paths
ORDER BY statistic, minute_index;

-- Per-event audit table.
SELECT trade_date, daily_return, body_return, gap_return,
       first_30_contribution, morning_contribution, last_30_contribution,
       max_drawdown, close_location, above_open_share,
       hit_50_time, hit_80_time, peak_time, trough_time,
       path_efficiency, path_type
FROM event_metrics
ORDER BY trade_date;

-- Coarse path-type counts, derived from morning/afternoon contribution.
SELECT path_type, sample_count, share
FROM path_type_counts
ORDER BY sample_count DESC;

-- Threshold sensitivity table.
SELECT threshold_label, sample_count, median_daily_return, median_body_return,
       median_gap_return, median_first_30_contribution,
       median_morning_contribution, median_close_location,
       median_last_30_contribution, median_max_drawdown,
       median_above_open_share, median_pairwise_path_correlation,
       close_high_share, tail_positive_share, trough_in_morning_share,
       peak_after_1445_share, afternoon_dominant_share
FROM threshold_sensitivity
ORDER BY threshold;

-- Common-feature prevalence table.
SELECT feature, matching_days, sample_count, share, meets_70pct_rule
FROM common_features
ORDER BY share DESC, feature;

-- Cross-source validation summary can be recomputed from the detailed rows.
SELECT COUNT(DISTINCT trade_date) AS overlap_trade_days,
       MAX(absolute_difference) AS max_absolute_difference_points,
       AVG(absolute_difference) AS mean_absolute_difference_points,
       MAX(relative_difference_bp) AS max_relative_difference_bp
FROM local_daily_comparison;
