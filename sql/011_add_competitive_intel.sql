-- ============================================================
-- 011_add_competitive_intel.sql
-- Tables for weather-aware peer similarity, team momentum,
-- and competitive intelligence analytics.
-- ============================================================

-- 1. Team weather profile (season-averaged weather conditions)
CREATE TABLE IF NOT EXISTS milb.team_weather_profile (
    team_id             INTEGER NOT NULL,
    season              SMALLINT NOT NULL,
    avg_temp_f          NUMERIC(5,1),
    avg_precip_in       NUMERIC(6,3),
    avg_wind_mph        NUMERIC(5,1),
    pct_rain_games      NUMERIC(4,3),
    total_home_games    INTEGER,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id              INTEGER REFERENCES milb.analysis_runs(run_id),
    PRIMARY KEY (team_id, season)
);


-- 2. Team momentum (YoY trends + within-season trajectory)
CREATE TABLE IF NOT EXISTS milb.team_momentum (
    team_id                 INTEGER NOT NULL,
    season                  SMALLINT NOT NULL,
    avg_attendance          INTEGER,
    avg_cap_util            NUMERIC(6,3),
    yoy_attendance_change   INTEGER,
    yoy_attendance_pct      NUMERIC(6,3),
    yoy_cap_util_change     NUMERIC(6,3),
    first_half_avg_att      INTEGER,
    second_half_avg_att     INTEGER,
    intra_season_trend      NUMERIC(6,3),
    momentum_label          TEXT,
    momentum_score          NUMERIC(6,3),
    multi_season_slope      NUMERIC(8,4),
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id                  INTEGER REFERENCES milb.analysis_runs(run_id),
    PRIMARY KEY (team_id, season)
);

CREATE INDEX IF NOT EXISTS idx_momentum_label
    ON milb.team_momentum (momentum_label, season);


-- 3. Weather-aware peer similarity (top-N per team, pairwise)
CREATE TABLE IF NOT EXISTS milb.weather_peer_similarity (
    team_id             INTEGER NOT NULL,
    peer_team_id        INTEGER NOT NULL,
    similarity_score    NUMERIC(8,4),
    distance            NUMERIC(8,4),
    weather_dist        NUMERIC(8,4),
    demo_dist           NUMERIC(8,4),
    season              SMALLINT NOT NULL,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id              INTEGER REFERENCES milb.analysis_runs(run_id),
    PRIMARY KEY (team_id, peer_team_id, season)
);

CREATE INDEX IF NOT EXISTS idx_wps_team
    ON milb.weather_peer_similarity (team_id, season, similarity_score DESC);
