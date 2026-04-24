-- Weekend gap analysis tables. Feeds the Weekend Playbook page.
--
-- Camp classification lives on gap_pct (fan-based) so the percentages are
-- comparable to how stakeholders already think about attendance. Capacity
-- utilization columns are stored alongside so the page can normalize for
-- venue size (the preferred denominator) without recomputing.

CREATE TABLE IF NOT EXISTS milb.weekend_gap (
    team_id             INTEGER NOT NULL,
    season              SMALLINT NOT NULL,
    sport_id            INTEGER NOT NULL,

    n_fri               SMALLINT,
    n_sat               SMALLINT,

    -- Fan-based metrics
    season_avg          NUMERIC(8,1),
    fri_avg             NUMERIC(8,1),
    sat_avg             NUMERIC(8,1),
    gap_fans            NUMERIC(8,1),     -- sat_avg - fri_avg
    gap_pct             NUMERIC(6,4),     -- gap_fans / season_avg

    -- Capacity-based metrics (fraction of seats filled)
    venue_capacity      INTEGER,
    season_cap_util     NUMERIC(5,4),
    fri_cap_util        NUMERIC(5,4),
    sat_cap_util        NUMERIC(5,4),
    gap_cap_util_pts    NUMERIC(6,4),     -- sat_cap_util - fri_cap_util

    gap_camp            TEXT NOT NULL,    -- 'sat_winner' | 'neutral' | 'sat_loser'

    momentum_label      TEXT,
    operator_name       TEXT,

    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id              INTEGER REFERENCES milb.analysis_runs(run_id),

    PRIMARY KEY (team_id, season)
);

CREATE INDEX IF NOT EXISTS idx_wg_camp ON milb.weekend_gap (gap_camp, sport_id);

CREATE TABLE IF NOT EXISTS milb.weekend_promo_mix (
    season              SMALLINT NOT NULL,
    sport_id            SMALLINT,          -- NULL = pooled across all levels
    gap_camp            TEXT NOT NULL,     -- 'sat_winner' | 'neutral' | 'sat_loser'
    dow_label           TEXT NOT NULL,     -- 'Fri' | 'Sat'
    promo_type          TEXT NOT NULL,     -- one of the 12 has_* flags
    pct_games_with_promo NUMERIC(5,4),
    avg_promo_count     NUMERIC(5,2),
    n_games             INTEGER,
    n_teams             INTEGER,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id              INTEGER REFERENCES milb.analysis_runs(run_id)
);

-- NULL-safe uniqueness: treat NULL sport_id as a distinct pooled bucket.
CREATE UNIQUE INDEX IF NOT EXISTS uq_wpm_key ON milb.weekend_promo_mix (
    season, COALESCE(sport_id, -1), gap_camp, dow_label, promo_type
);
