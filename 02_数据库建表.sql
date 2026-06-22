CREATE TABLE IF NOT EXISTS bars_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    amount REAL,
    turnover REAL,
    adj_factor REAL,
    source TEXT NOT NULL DEFAULT 'csv',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_bars_daily_ticker_date
ON bars_daily (ticker, date);

CREATE INDEX IF NOT EXISTS idx_bars_daily_date
ON bars_daily (date);

CREATE TABLE IF NOT EXISTS bars_weekly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    week_end_date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    amount REAL,
    source TEXT NOT NULL DEFAULT 'aggregated',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_bars_weekly_ticker_week_end_date
ON bars_weekly (ticker, week_end_date);

CREATE TABLE IF NOT EXISTS feature_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    ma5 REAL,
    ma10 REAL,
    ma20 REAL,
    ma60 REAL,
    ma_slope REAL,
    atr14 REAL,
    return_1d REAL,
    return_5d REAL,
    volatility_10d REAL,
    volume_ma5 REAL,
    volume_ratio REAL,
    amount_ratio REAL,
    close_to_ma20 REAL,
    close_to_recent_high REAL,
    close_to_recent_low REAL,
    weekly_trend_raw TEXT,
    daily_trend_raw TEXT,
    feature_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_feature_snapshot_ticker_date_version
ON feature_snapshot (ticker, date, feature_version);

CREATE TABLE IF NOT EXISTS structure_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    trend_weekly TEXT NOT NULL,
    trend_daily TEXT NOT NULL,
    structure_type TEXT NOT NULL,
    structure_score INTEGER NOT NULL DEFAULT 0,
    pivot_json TEXT,
    swing_json TEXT,
    zone_json TEXT,
    event_json TEXT,
    support_main TEXT,
    support_secondary TEXT,
    pressure_main TEXT,
    breakout_level REAL,
    invalidation_level REAL,
    volume_state TEXT,
    breakout_quality TEXT,
    pullback_quality TEXT,
    price_action_quality TEXT,
    volume_score INTEGER NOT NULL DEFAULT 0,
    chip_pressure TEXT,
    chip_support TEXT,
    chip_vacuum TEXT,
    chip_score INTEGER NOT NULL DEFAULT 0,
    risk_score INTEGER NOT NULL DEFAULT 0,
    risk_tags_json TEXT,
    reason_codes_json TEXT,
    evidence_json TEXT,
    raw_json TEXT,
    rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_structure_snapshot_ticker_date_rule
ON structure_snapshot (ticker, date, rule_version);

CREATE TABLE IF NOT EXISTS recommendation_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    conclusion_label TEXT NOT NULL,
    watching_action TEXT NOT NULL,
    holding_action TEXT NOT NULL,
    confirmation_needed TEXT,
    invalidation_level REAL,
    reason_codes_json TEXT,
    risk_summary TEXT,
    summary_text TEXT,
    llm_status TEXT NOT NULL DEFAULT 'pending',
    model_version TEXT,
    template_version TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_recommendation_snapshot_ticker_date_rule_model
ON recommendation_snapshot (ticker, date, rule_version, model_version);

CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    target_date TEXT,
    scope TEXT,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    log_path TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS case_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    case_type TEXT NOT NULL,
    structure_label TEXT,
    strength_label TEXT,
    space_label TEXT,
    support_main REAL,
    pressure_main REAL,
    invalidation_level REAL,
    decision_summary TEXT,
    expected_label TEXT,
    actual_label TEXT,
    outcome_verdict TEXT,
    outcome_window INTEGER,
    outcome_return REAL,
    review_status TEXT NOT NULL DEFAULT 'pending',
    misjudgment_reason TEXT,
    rule_revision_note TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
