-- Hypothesis lab + peer playbook analytics tables.
--
-- Feeds two pages:
--   12_Peer_Playbook.py   -- small-market / cold-weather peers vs Binghamton
--   13_Hypothesis_Lab.py  -- fireworks swap, stack effects, DOW x promo heatmap
--
-- All tables are run-based (wiped + rewritten each run) with a run_id FK
-- back to milb.analysis_runs for audit.

-- -----------------------------------------------------------------------------
-- 1. Fireworks-swap counterfactual
-- -----------------------------------------------------------------------------
-- One row per (team, season, scenario) telling the story of:
--   "If RP moved fireworks from Friday to Saturday, what happens?"
--
-- scenario values:
--   'current'         - observed Fri/Sat averages with existing promo mix
--   'peer_baseline'   - what Sat-winner peers achieve on Fri/Sat
--   'counterfactual'  - projected Fri/Sat if RP swapped fireworks -> Sat and
--                       stacked giveaway/kids/celebrity/entertainment on Fri
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS milb.fireworks_swap CASCADE;

CREATE TABLE milb.fireworks_swap (
    team_id                INTEGER NOT NULL,
    season                 SMALLINT NOT NULL,
    sport_id               INTEGER NOT NULL,
    scenario               TEXT NOT NULL,  -- 'current' | 'peer_baseline' | 'counterfactual'

    -- Friday metrics
    fri_games              INTEGER,
    fri_avg_att            NUMERIC(8,1),
    fri_avg_att_ci_lo      NUMERIC(8,1),
    fri_avg_att_ci_hi      NUMERIC(8,1),
    fri_has_fireworks_pct  NUMERIC(5,4),
    fri_has_giveaway_pct   NUMERIC(5,4),
    fri_has_kids_pct       NUMERIC(5,4),
    fri_has_celebrity_pct  NUMERIC(5,4),
    fri_has_entertain_pct  NUMERIC(5,4),

    -- Saturday metrics
    sat_games              INTEGER,
    sat_avg_att            NUMERIC(8,1),
    sat_avg_att_ci_lo      NUMERIC(8,1),
    sat_avg_att_ci_hi      NUMERIC(8,1),
    sat_has_fireworks_pct  NUMERIC(5,4),
    sat_has_giveaway_pct   NUMERIC(5,4),
    sat_has_kids_pct       NUMERIC(5,4),
    sat_has_celebrity_pct  NUMERIC(5,4),
    sat_has_entertain_pct  NUMERIC(5,4),

    -- Net effect (counterfactual only)
    projected_fri_delta    NUMERIC(8,1),  -- projected - current (per Fri game)
    projected_sat_delta    NUMERIC(8,1),
    projected_annual_delta INTEGER,       -- (fri_delta * n_fri) + (sat_delta * n_sat)
    projected_annual_ci_lo INTEGER,
    projected_annual_ci_hi INTEGER,

    notes                  TEXT,
    computed_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id                 INTEGER REFERENCES milb.analysis_runs(run_id),

    PRIMARY KEY (team_id, season, scenario)
);

CREATE INDEX idx_fswap_team ON milb.fireworks_swap (team_id, season);


-- -----------------------------------------------------------------------------
-- 2. Promo stack effects
-- -----------------------------------------------------------------------------
-- Each row = a specific combination of promo flags and its observed lift.
-- Computed within a (sport_id, dow_label) partition so the "Friday stack"
-- question has a matching row set.
--
-- flag_combo is a sorted, underscore-joined string of the flags present:
--   'has_fireworks'
--   'has_giveaway+has_kids_event'
--   'has_celebrity+has_entertain+has_giveaway+has_kids_event'
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS milb.promo_stack_effects CASCADE;

CREATE TABLE milb.promo_stack_effects (
    sport_id            INTEGER NOT NULL,
    dow_label           TEXT NOT NULL,  -- 'Mon'..'Sun', or 'All'
    flag_combo          TEXT NOT NULL,
    n_flags             SMALLINT NOT NULL,
    n_games             INTEGER NOT NULL,
    n_teams             INTEGER NOT NULL,

    avg_att             NUMERIC(8,1),
    baseline_att        NUMERIC(8,1),       -- avg of no-promo games, same (sport, dow)
    lift_fans           NUMERIC(8,1),       -- avg_att - baseline_att
    lift_pct            NUMERIC(7,4),       -- lift_fans / baseline_att
    lift_ci_lo          NUMERIC(8,1),
    lift_ci_hi          NUMERIC(8,1),

    -- Additivity check: does this combo beat the sum of its single-flag lifts?
    expected_additive   NUMERIC(8,1),
    synergy_fans        NUMERIC(8,1),       -- lift_fans - expected_additive
    is_synergistic      BOOLEAN,

    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id              INTEGER REFERENCES milb.analysis_runs(run_id),

    PRIMARY KEY (sport_id, dow_label, flag_combo)
);

CREATE INDEX idx_stack_lift ON milb.promo_stack_effects (sport_id, dow_label, lift_fans DESC);
CREATE INDEX idx_stack_nflags ON milb.promo_stack_effects (n_flags);


-- -----------------------------------------------------------------------------
-- 3. DOW x Promo heatmap
-- -----------------------------------------------------------------------------
-- Per-level, per-DOW, per-promo-flag lift. One row per
-- (sport_id, dow_label, promo_type). Two modes:
--   n_games_with = games where flag is TRUE
--   n_games_without = games where flag is FALSE (same sport, dow)
-- lift_fans = avg_with - avg_without.
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS milb.dow_promo_lift CASCADE;

CREATE TABLE milb.dow_promo_lift (
    sport_id            INTEGER NOT NULL,
    dow_label           TEXT NOT NULL,
    promo_type          TEXT NOT NULL,

    n_games_with        INTEGER,
    n_games_without     INTEGER,
    avg_with            NUMERIC(8,1),
    avg_without         NUMERIC(8,1),
    lift_fans           NUMERIC(8,1),
    lift_pct            NUMERIC(7,4),
    lift_ci_lo          NUMERIC(8,1),
    lift_ci_hi          NUMERIC(8,1),

    -- Also compute cap-util version (removes venue-size confounding)
    cap_util_with       NUMERIC(6,4),
    cap_util_without    NUMERIC(6,4),
    cap_util_lift       NUMERIC(6,4),

    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id              INTEGER REFERENCES milb.analysis_runs(run_id),

    PRIMARY KEY (sport_id, dow_label, promo_type)
);

CREATE INDEX idx_dow_promo_lift ON milb.dow_promo_lift (sport_id, lift_fans DESC);


-- -----------------------------------------------------------------------------
-- 4. Peer playbook
-- -----------------------------------------------------------------------------
-- For each hand-picked peer (Portland, Richmond, Akron, Erie, New Hampshire,
-- Reading), store a side-by-side profile vs Binghamton + an LLM narrative of
-- "what to steal" for the RP manager.
--
-- peer_role:
--   'small_market_cold'  - Portland, Erie, New Hampshire, Reading
--   'small_market_warm'  - Akron
--   'large_market_model' - Richmond, Frisco
--   'hero'               - Binghamton itself (reference row)
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS milb.peer_playbook CASCADE;

CREATE TABLE milb.peer_playbook (
    team_id             INTEGER NOT NULL,
    season              SMALLINT NOT NULL,
    team_name           TEXT NOT NULL,
    peer_role           TEXT NOT NULL,

    -- Market / venue context
    msa_population      INTEGER,
    median_income       INTEGER,
    poverty_rate        NUMERIC(6,2),       -- stored as percent (e.g. 32.60), not fraction
    venue_name          TEXT,
    venue_capacity      INTEGER,
    stadium_year        INTEGER,

    -- Performance
    avg_attendance      NUMERIC(8,1),
    cap_utilization     NUMERIC(6,4),       -- can exceed 1.0 (standing-room sellouts)
    yoy_change_pct      NUMERIC(6,3),
    league_rank         SMALLINT,
    total_home_games    SMALLINT,

    -- Promo profile
    promos_per_game     NUMERIC(4,2),
    fri_avg_att         NUMERIC(8,1),
    sat_avg_att         NUMERIC(8,1),
    fri_fireworks_pct   NUMERIC(5,4),
    sat_fireworks_pct   NUMERIC(5,4),
    has_recurring_promo BOOLEAN,
    top_promo_flag      TEXT,           -- highest-lift flag for this team
    top_promo_lift      NUMERIC(8,1),

    -- LLM output
    narrative_text      TEXT,           -- 2-3 paragraph writeup
    what_to_steal       JSONB,          -- array of {action, reason, est_impact}
    llm_model           TEXT,
    llm_generated_at    TIMESTAMPTZ,

    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id              INTEGER REFERENCES milb.analysis_runs(run_id),

    PRIMARY KEY (team_id, season)
);

CREATE INDEX idx_peer_role ON milb.peer_playbook (peer_role, season);


COMMENT ON TABLE milb.fireworks_swap          IS 'Fri->Sat fireworks swap counterfactual per team/season.';
COMMENT ON TABLE milb.promo_stack_effects     IS 'Lift per promo-flag combination, by sport and DOW. Captures synergy between stacked promos.';
COMMENT ON TABLE milb.dow_promo_lift          IS 'Per-DOW, per-promo lift, computed league-wide by level.';
COMMENT ON TABLE milb.peer_playbook           IS 'Side-by-side peer profiles + LLM narrative of what to steal for Binghamton.';
