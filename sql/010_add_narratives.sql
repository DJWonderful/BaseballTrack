-- ============================================================
-- 010_add_narratives.sql
-- LLM-generated executive narrative summaries.
-- Team-level briefs and group-level rollups.
-- ============================================================

-- Team-level narratives (one per team per season)
CREATE TABLE IF NOT EXISTS milb.team_narratives (
    team_id         INTEGER NOT NULL REFERENCES milb.teams(team_id),
    season          SMALLINT NOT NULL,
    narrative_text  TEXT NOT NULL,
    kpi_json        JSONB,
    goals_json      JSONB,
    risks_json      JSONB,
    llm_model       TEXT,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (team_id, season)
);

-- Group-level rollup narratives (by level, market cluster, promo cluster, league)
CREATE TABLE IF NOT EXISTS milb.group_narratives (
    group_type      TEXT NOT NULL,
    group_key       TEXT NOT NULL,
    season          SMALLINT NOT NULL,
    narrative_text  TEXT NOT NULL,
    kpi_json        JSONB,
    llm_model       TEXT,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_type, group_key, season)
);
