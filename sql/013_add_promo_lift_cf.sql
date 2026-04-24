-- Counterfactual promo lift table (S-learner over trained XGBoost models).
-- Complements milb.promo_lift (OLS) with model-based causal estimates that
-- use each game's own predicted baseline instead of a team-season mean.

CREATE TABLE IF NOT EXISTS milb.promo_lift_cf (
    lift_id         SERIAL PRIMARY KEY,
    team_id         INTEGER,            -- NULL unless scope='team'
    sport_id        INTEGER,            -- NULL unless scope='level' or 'team'
    scope           TEXT NOT NULL,      -- 'league' | 'level' | 'team'
    promo_type      TEXT NOT NULL,      -- e.g. 'has_fireworks'
    estimand        TEXT NOT NULL,      -- 'ATE' | 'ATT' | 'ATU'
    mean_lift       DOUBLE PRECISION,   -- avg (pred_on - pred_off) in fans
    median_lift     DOUBLE PRECISION,
    std_lift        DOUBLE PRECISION,
    p10_lift        DOUBLE PRECISION,
    p90_lift        DOUBLE PRECISION,
    mean_pct_lift   DOUBLE PRECISION,   -- avg lift / pred_baseline
    pct_positive    NUMERIC(5,4),       -- share of games where lift > 0
    n_games         INTEGER,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id          INTEGER REFERENCES milb.analysis_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_plcf_scope ON milb.promo_lift_cf (scope, sport_id, team_id);
CREATE INDEX IF NOT EXISTS idx_plcf_promo ON milb.promo_lift_cf (promo_type, estimand);
