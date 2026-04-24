-- ============================================================
-- 007_add_analytics.sql
-- Tables for the analytics & recommendations engine.
-- Stores cached results from build_features, promo_lift,
-- peer clustering, XGBoost model runs, and recommendations.
-- ============================================================

-- 1. Analysis run log (like data_sync_log but for analytics pipeline)
CREATE TABLE IF NOT EXISTS milb.analysis_runs (
    run_id              SERIAL PRIMARY KEY,
    analysis_name       TEXT NOT NULL,
    sport_id            INTEGER,
    input_max_updated   TIMESTAMPTZ,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'running',
    record_count        INTEGER,
    parameters          JSONB,
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_analysis_runs_name
    ON milb.analysis_runs (analysis_name, status, completed_at DESC);


-- 2. Game features (flat table: one row per home game with attendance)
CREATE TABLE IF NOT EXISTS milb.game_features (
    game_pk                 INTEGER PRIMARY KEY,
    team_id                 INTEGER NOT NULL,
    season                  SMALLINT NOT NULL,
    game_date               DATE NOT NULL,
    sport_id                INTEGER NOT NULL,
    game_type               TEXT,

    -- Calendar
    day_of_week             SMALLINT,       -- 0=Mon..6=Sun
    month                   SMALLINT,
    is_weekend              BOOLEAN,
    day_night               TEXT,

    -- Scheduling
    homestand_game_number   SMALLINT,
    homestand_length        SMALLINT,
    days_since_last_home    SMALLINT,
    game_number_in_season   SMALLINT,
    season_progress         NUMERIC(4,3),   -- 0.000 to 1.000

    -- Team performance
    win_pct_entering        NUMERIC(6,3),
    streak                  SMALLINT,       -- positive=W, negative=L
    prior_game_attendance   INTEGER,
    prior_game_margin       SMALLINT,

    -- Promotions (BOOL_OR aggregated per game)
    has_any_promo           BOOLEAN,
    promo_count             SMALLINT,
    has_fireworks           BOOLEAN,
    has_giveaway            BOOLEAN,
    has_food_deal           BOOLEAN,
    has_ticket_deal         BOOLEAN,
    has_theme_night         BOOLEAN,
    has_kids_event          BOOLEAN,
    has_heritage            BOOLEAN,
    has_community           BOOLEAN,
    has_entertain           BOOLEAN,
    has_dog                 BOOLEAN,
    has_celebrity           BOOLEAN,
    has_recurring           BOOLEAN,
    has_limited_giveaway    BOOLEAN,
    days_since_last_fw      SMALLINT,       -- promo cooldown: fireworks
    days_since_last_give    SMALLINT,       -- promo cooldown: giveaway

    -- Weather
    temp_max_f              NUMERIC(5,1),
    precip_inches           NUMERIC(6,3),
    wind_max_mph            NUMERIC(5,1),
    weather_bucket          TEXT,           -- clear/cloudy/rain/snow

    -- Opponent
    opponent_team_id        INTEGER,
    opponent_hist_draw      NUMERIC(8,1),
    distance_miles          NUMERIC(7,1),
    is_same_division        BOOLEAN,

    -- Rehab
    has_rehab_player        BOOLEAN,

    -- School calendar
    school_in_session       BOOLEAN,

    -- Market context (team-level, denormalized for modeling)
    msa_population          INTEGER,
    place_population        INTEGER,
    median_income           INTEGER,
    poverty_rate            NUMERIC(5,2),
    venue_capacity          INTEGER,

    -- Targets
    attendance              INTEGER NOT NULL,
    capacity_utilization    NUMERIC(6,3),
    attendance_lift         NUMERIC(8,1),   -- attendance minus team_season_mean

    -- Metadata
    run_id                  INTEGER REFERENCES milb.analysis_runs(run_id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gf_team_season ON milb.game_features (team_id, season);
CREATE INDEX IF NOT EXISTS idx_gf_sport       ON milb.game_features (sport_id);


-- 3. Promo marginal lift estimates
CREATE TABLE IF NOT EXISTS milb.promo_lift (
    lift_id             SERIAL PRIMARY KEY,
    team_id             INTEGER,            -- NULL for league-wide
    sport_id            INTEGER,
    season              SMALLINT,           -- NULL for all-seasons pooled
    scope               TEXT NOT NULL,       -- 'league_level', 'team_all', 'team_season'
    promo_type          TEXT NOT NULL,       -- e.g. 'has_fireworks'
    marginal_lift       DOUBLE PRECISION,
    ci_lower            DOUBLE PRECISION,
    ci_upper            DOUBLE PRECISION,
    p_value             NUMERIC(8,6),
    n_games_with        INTEGER,
    n_games_without     INTEGER,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id              INTEGER REFERENCES milb.analysis_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_pl_scope ON milb.promo_lift (scope, sport_id, team_id);


-- 4. Team peer clusters
CREATE TABLE IF NOT EXISTS milb.team_clusters (
    team_id             INTEGER PRIMARY KEY,
    cluster_id          INTEGER NOT NULL,
    cluster_label       TEXT,
    centroid_distance    NUMERIC(8,4),
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id              INTEGER REFERENCES milb.analysis_runs(run_id)
);


-- 5. Cluster benchmark averages
CREATE TABLE IF NOT EXISTS milb.cluster_benchmarks (
    benchmark_id        SERIAL PRIMARY KEY,
    cluster_id          INTEGER NOT NULL,
    metric_name         TEXT NOT NULL,       -- e.g. 'avg_attendance', 'capacity_util'
    metric_value        NUMERIC(10,2),
    n_teams             INTEGER,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id              INTEGER REFERENCES milb.analysis_runs(run_id),
    UNIQUE (cluster_id, metric_name, run_id)
);


-- 6. Model training runs
CREATE TABLE IF NOT EXISTS milb.model_runs (
    run_id              SERIAL PRIMARY KEY,
    sport_id            INTEGER,
    model_type          TEXT NOT NULL,       -- 'xgboost', 'ols', etc.
    train_seasons       TEXT,               -- '2023,2024'
    val_season          SMALLINT,
    mae                 NUMERIC(8,1),
    mape                NUMERIC(6,3),
    rmse                NUMERIC(8,1),
    r_squared           NUMERIC(6,4),
    n_train             INTEGER,
    n_val               INTEGER,
    model_path          TEXT,               -- path to saved model file
    parameters          JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- 7. Feature importance (SHAP values)
CREATE TABLE IF NOT EXISTS milb.feature_importance (
    importance_id       SERIAL PRIMARY KEY,
    run_id              INTEGER REFERENCES milb.model_runs(run_id),
    feature_name        TEXT NOT NULL,
    shap_mean_abs       DOUBLE PRECISION,
    shap_rank           SMALLINT,
    gain_importance     DOUBLE PRECISION,
    UNIQUE (run_id, feature_name)
);


-- 8. Per-game predictions
CREATE TABLE IF NOT EXISTS milb.game_predictions (
    game_pk             INTEGER NOT NULL,
    run_id              INTEGER REFERENCES milb.model_runs(run_id),
    predicted_attendance INTEGER,
    residual            INTEGER,
    shap_values         JSONB,
    PRIMARY KEY (game_pk, run_id)
);


-- 9. Team recommendations
CREATE TABLE IF NOT EXISTS milb.team_recommendations (
    rec_id              SERIAL PRIMARY KEY,
    team_id             INTEGER NOT NULL,
    season              SMALLINT,
    category            TEXT NOT NULL,       -- promo_roi, peer_gap, what_if, scheduling, anomaly
    priority            SMALLINT,
    title               TEXT NOT NULL,
    detail              TEXT,
    expected_impact     INTEGER,            -- estimated attendance gain
    confidence          TEXT,               -- high, medium, low
    evidence            JSONB,
    narrative           TEXT,               -- optional LLM-generated summary
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id              INTEGER REFERENCES milb.analysis_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_rec_team ON milb.team_recommendations (team_id, season, priority);
