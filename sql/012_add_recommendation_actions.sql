-- 012_add_recommendation_actions.sql
-- Track which recommendations the team has acted on, for the feedback loop.
-- Rows are created lazily when a user clicks the "acted on" checkbox.

CREATE TABLE IF NOT EXISTS milb.recommendation_actions (
    action_id        SERIAL PRIMARY KEY,
    team_id          INTEGER NOT NULL REFERENCES milb.teams(team_id),

    -- Identity of the recommendation. team_recommendations.rec_id if present,
    -- otherwise (category, title) as a soft key so re-generated recs can be
    -- re-associated with prior decisions.
    rec_id           INTEGER,
    rec_category     TEXT NOT NULL,
    rec_title        TEXT NOT NULL,

    -- The action itself
    acted_on         BOOLEAN NOT NULL DEFAULT TRUE,
    status           TEXT NOT NULL DEFAULT 'planned',     -- planned | in_progress | done | rejected
    notes            TEXT,
    acted_by         TEXT,

    -- When the decision was made
    acted_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Soft-key uniqueness: one action row per (team, category, title)
    UNIQUE (team_id, rec_category, rec_title)
);

CREATE INDEX IF NOT EXISTS idx_rec_actions_team
    ON milb.recommendation_actions (team_id, status);

CREATE INDEX IF NOT EXISTS idx_rec_actions_status
    ON milb.recommendation_actions (status, acted_at DESC);
