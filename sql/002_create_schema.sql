-- ============================================================
-- 002_create_schema.sql
-- Creates the milb schema, all tables, and all indexes.
-- Run this connected to the 'baseball' database.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS milb;

-- ============================================================
-- REFERENCE / DIMENSION TABLES
-- ============================================================

-- 1. milb.sports — MiLB classification levels
CREATE TABLE IF NOT EXISTS milb.sports (
    sport_id        INTEGER PRIMARY KEY,
    sport_name      TEXT NOT NULL,
    sport_code      TEXT,
    sort_order      SMALLINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

-- 2. milb.leagues
CREATE TABLE IF NOT EXISTS milb.leagues (
    league_id       INTEGER PRIMARY KEY,
    league_name     TEXT NOT NULL,
    sport_id        INTEGER REFERENCES milb.sports(sport_id),
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

-- 3. milb.divisions
CREATE TABLE IF NOT EXISTS milb.divisions (
    division_id     INTEGER PRIMARY KEY,
    division_name   TEXT NOT NULL,
    league_id       INTEGER REFERENCES milb.leagues(league_id),
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

-- 4. milb.organizations — MLB parent clubs
CREATE TABLE IF NOT EXISTS milb.organizations (
    org_id          INTEGER PRIMARY KEY,
    org_name        TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

-- 5. milb.venues — Physical ballparks
CREATE TABLE IF NOT EXISTS milb.venues (
    venue_id        INTEGER PRIMARY KEY,
    venue_name      TEXT NOT NULL,
    city            TEXT,
    state           TEXT,
    state_abbrev    TEXT,
    postal_code     TEXT,
    country         TEXT,
    latitude        NUMERIC(10, 6),
    longitude       NUMERIC(10, 6),
    capacity        INTEGER,
    turf_type       TEXT,
    roof_type       TEXT,
    left_line       INTEGER,
    left_center     INTEGER,
    center_field    INTEGER,
    right_center    INTEGER,
    right_line      INTEGER,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

-- 6. milb.teams
CREATE TABLE IF NOT EXISTS milb.teams (
    team_id         INTEGER PRIMARY KEY,
    team_name       TEXT NOT NULL,
    short_name      TEXT,
    abbreviation    TEXT,
    location_name   TEXT,
    team_code       TEXT,
    sport_id        INTEGER REFERENCES milb.sports(sport_id),
    league_id       INTEGER REFERENCES milb.leagues(league_id),
    division_id     INTEGER REFERENCES milb.divisions(division_id),
    org_id          INTEGER REFERENCES milb.organizations(org_id),
    venue_id        INTEGER REFERENCES milb.venues(venue_id),
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

-- ============================================================
-- FACT TABLES
-- ============================================================

-- 7. milb.games — Central fact table
CREATE TABLE IF NOT EXISTS milb.games (
    game_pk             INTEGER PRIMARY KEY,
    game_date           DATE NOT NULL,
    game_datetime       TIMESTAMPTZ,
    season              SMALLINT NOT NULL,
    game_type           TEXT NOT NULL DEFAULT 'R',
    day_night           TEXT,
    doubleheader        TEXT,
    game_number         SMALLINT,
    scheduled_innings   SMALLINT DEFAULT 9,

    -- status
    status_code         TEXT,
    status_detail       TEXT,
    abstract_game_state TEXT,

    -- teams (denormalized)
    home_team_id        INTEGER NOT NULL REFERENCES milb.teams(team_id),
    home_team_name      TEXT,
    away_team_id        INTEGER NOT NULL REFERENCES milb.teams(team_id),
    away_team_name      TEXT,
    home_score          SMALLINT,
    away_score          SMALLINT,

    -- venue (denormalized)
    venue_id            INTEGER REFERENCES milb.venues(venue_id),
    venue_name          TEXT,

    -- series
    series_description  TEXT,

    -- game feed enrichment
    attendance          INTEGER,
    game_duration_minutes INTEGER,
    first_pitch         TIMESTAMPTZ,

    -- API-reported weather from game feed
    weather_condition   TEXT,
    weather_temp_f      SMALLINT,
    weather_wind        TEXT,

    -- level (denormalized)
    sport_id            INTEGER REFERENCES milb.sports(sport_id),

    raw_json            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ
);

-- 8. milb.game_promotions
CREATE TABLE IF NOT EXISTS milb.game_promotions (
    promotion_id    BIGSERIAL PRIMARY KEY,
    game_pk         INTEGER NOT NULL REFERENCES milb.games(game_pk) ON DELETE CASCADE,
    offer_id        INTEGER,
    offer_name      TEXT,
    offer_type      TEXT,
    description     TEXT,
    distribution    TEXT,
    presented_by    TEXT,
    image_url       TEXT,
    thumbnail_url   TEXT,
    display_order   SMALLINT,

    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,

    CONSTRAINT uq_game_promotion UNIQUE (game_pk, offer_id)
);

-- 9. milb.game_weather — Open-Meteo daily weather per game
CREATE TABLE IF NOT EXISTS milb.game_weather (
    weather_id                  BIGSERIAL PRIMARY KEY,
    game_pk                     INTEGER NOT NULL REFERENCES milb.games(game_pk) ON DELETE CASCADE,
    venue_id                    INTEGER REFERENCES milb.venues(venue_id),
    weather_date                DATE NOT NULL,

    -- temperatures (Fahrenheit)
    temperature_max_f           NUMERIC(5, 1),
    temperature_min_f           NUMERIC(5, 1),
    apparent_temperature_max_f  NUMERIC(5, 1),
    apparent_temperature_min_f  NUMERIC(5, 1),

    -- precipitation (inches)
    precipitation_sum_in        NUMERIC(6, 3),
    rain_sum_in                 NUMERIC(6, 3),
    snowfall_sum_in             NUMERIC(6, 3),
    precipitation_hours         NUMERIC(4, 1),

    -- wind (mph)
    windspeed_max_mph           NUMERIC(5, 1),
    windgusts_max_mph           NUMERIC(5, 1),
    winddirection_dominant_deg  SMALLINT,

    -- WMO weather code
    weathercode                 SMALLINT,

    -- sun
    sunrise                     TIMESTAMPTZ,
    sunset                      TIMESTAMPTZ,
    sunshine_duration_sec       NUMERIC(8, 1),

    raw_json                    JSONB,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ,

    CONSTRAINT uq_game_weather UNIQUE (game_pk)
);

-- 10. milb.season_attendance
CREATE TABLE IF NOT EXISTS milb.season_attendance (
    season_attendance_id    BIGSERIAL PRIMARY KEY,
    team_id                 INTEGER NOT NULL REFERENCES milb.teams(team_id),
    season                  SMALLINT NOT NULL,
    game_type_id            TEXT,

    openings_total          INTEGER,
    openings_total_home     INTEGER,
    openings_total_away     INTEGER,

    games_total             INTEGER,
    games_home_total        INTEGER,
    games_away_total        INTEGER,

    attendance_total        INTEGER,
    attendance_total_home   INTEGER,
    attendance_total_away   INTEGER,

    attendance_avg_home     INTEGER,
    attendance_avg_away     INTEGER,
    attendance_avg_ytd      INTEGER,
    attendance_opening_avg  INTEGER,

    attendance_high         INTEGER,
    attendance_high_date    DATE,
    attendance_high_game_pk INTEGER,

    attendance_low          INTEGER,
    attendance_low_date     DATE,
    attendance_low_game_pk  INTEGER,

    raw_json                JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ,

    CONSTRAINT uq_team_season_attendance UNIQUE (team_id, season, game_type_id)
);

-- 11. milb.data_sync_log — ETL pipeline tracking
CREATE TABLE IF NOT EXISTS milb.data_sync_log (
    sync_id         BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,
    sport_id        INTEGER,
    season          SMALLINT,
    sync_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sync_ended_at   TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running',
    records_fetched INTEGER DEFAULT 0,
    records_upserted INTEGER DEFAULT 0,
    error_message   TEXT,
    parameters      JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

-- ============================================================
-- INDEXES
-- ============================================================

-- teams
CREATE INDEX IF NOT EXISTS idx_teams_sport_id       ON milb.teams (sport_id);
CREATE INDEX IF NOT EXISTS idx_teams_org_id         ON milb.teams (org_id);
CREATE INDEX IF NOT EXISTS idx_teams_league_id      ON milb.teams (league_id);
CREATE INDEX IF NOT EXISTS idx_teams_venue_id       ON milb.teams (venue_id);

-- venues
CREATE INDEX IF NOT EXISTS idx_venues_state         ON milb.venues (state_abbrev);
CREATE INDEX IF NOT EXISTS idx_venues_coords        ON milb.venues (latitude, longitude);

-- games
CREATE INDEX IF NOT EXISTS idx_games_home_team      ON milb.games (home_team_id);
CREATE INDEX IF NOT EXISTS idx_games_away_team      ON milb.games (away_team_id);
CREATE INDEX IF NOT EXISTS idx_games_date           ON milb.games (game_date);
CREATE INDEX IF NOT EXISTS idx_games_season         ON milb.games (season);
CREATE INDEX IF NOT EXISTS idx_games_sport_id       ON milb.games (sport_id);
CREATE INDEX IF NOT EXISTS idx_games_venue_id       ON milb.games (venue_id);
CREATE INDEX IF NOT EXISTS idx_games_status         ON milb.games (status_detail);
CREATE INDEX IF NOT EXISTS idx_games_home_season    ON milb.games (home_team_id, season);
CREATE INDEX IF NOT EXISTS idx_games_date_sport     ON milb.games (game_date, sport_id);
CREATE INDEX IF NOT EXISTS idx_games_attendance_nn  ON milb.games (attendance) WHERE attendance IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_games_home_date_att  ON milb.games (home_team_id, game_date) INCLUDE (attendance, venue_id);
CREATE INDEX IF NOT EXISTS idx_games_raw_json       ON milb.games USING GIN (raw_json jsonb_path_ops);

-- game_promotions
CREATE INDEX IF NOT EXISTS idx_promotions_game      ON milb.game_promotions (game_pk);
CREATE INDEX IF NOT EXISTS idx_promotions_type      ON milb.game_promotions (offer_type);
CREATE INDEX IF NOT EXISTS idx_promotions_type_game ON milb.game_promotions (offer_type, game_pk);

-- game_weather
CREATE INDEX IF NOT EXISTS idx_weather_game         ON milb.game_weather (game_pk);
CREATE INDEX IF NOT EXISTS idx_weather_venue_date   ON milb.game_weather (venue_id, weather_date);

-- season_attendance
CREATE INDEX IF NOT EXISTS idx_season_att_team      ON milb.season_attendance (team_id, season);

-- data_sync_log
CREATE INDEX IF NOT EXISTS idx_sync_source_status   ON milb.data_sync_log (source, status);
CREATE INDEX IF NOT EXISTS idx_sync_started         ON milb.data_sync_log (sync_started_at);
