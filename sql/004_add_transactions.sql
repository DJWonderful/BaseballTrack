-- ============================================================
-- 004_add_transactions.sql
-- Adds milb.transactions table for roster moves (rehab, options, callups).
-- Run against the 'baseball' database.
-- ============================================================

CREATE TABLE IF NOT EXISTS milb.transactions (
    transaction_id      BIGSERIAL PRIMARY KEY,
    mlb_transaction_id  INTEGER,                      -- API transaction id (not always unique across seasons)
    transaction_date    DATE NOT NULL,
    effective_date      DATE,
    resolution_date     DATE,

    -- Player info
    player_id           INTEGER NOT NULL,              -- MLB Stats API person.id
    player_name         TEXT NOT NULL,                  -- person.fullName
    player_position     TEXT,                          -- parsed from description (e.g., "LHP", "SS", "C")

    -- Player notability (enriched from /people endpoint)
    mlb_debut_date      DATE,                          -- NULL = never debuted in MLB
    is_mlb_veteran      BOOLEAN DEFAULT FALSE,         -- has mlbDebutDate (played in MLB before)

    -- Teams
    from_team_id        INTEGER,                       -- team.id of origin
    from_team_name      TEXT,
    to_team_id          INTEGER,                       -- team.id of destination
    to_team_name        TEXT,

    -- Transaction type
    type_code           TEXT NOT NULL,                  -- ASG, OPT, CU, DES, SC, etc.
    type_desc           TEXT,                          -- "Assigned", "Optioned", "Recalled", etc.
    is_rehab            BOOLEAN DEFAULT FALSE,         -- TRUE if description contains "rehab"
    description         TEXT,                          -- full natural-language description

    raw_json            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ,

    -- Prevent duplicates on re-ingest
    CONSTRAINT uq_transaction UNIQUE (mlb_transaction_id, player_id, transaction_date, type_code)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_txn_to_team       ON milb.transactions (to_team_id);
CREATE INDEX IF NOT EXISTS idx_txn_from_team     ON milb.transactions (from_team_id);
CREATE INDEX IF NOT EXISTS idx_txn_date          ON milb.transactions (transaction_date);
CREATE INDEX IF NOT EXISTS idx_txn_player        ON milb.transactions (player_id);
CREATE INDEX IF NOT EXISTS idx_txn_type          ON milb.transactions (type_code);
CREATE INDEX IF NOT EXISTS idx_txn_rehab         ON milb.transactions (is_rehab) WHERE is_rehab = TRUE;
CREATE INDEX IF NOT EXISTS idx_txn_veteran       ON milb.transactions (is_mlb_veteran) WHERE is_mlb_veteran = TRUE;

-- Composite: "find rehab assignments to team X in date range"
CREATE INDEX IF NOT EXISTS idx_txn_team_date_rehab ON milb.transactions (to_team_id, transaction_date)
    WHERE is_rehab = TRUE;
