-- ============================================================
-- 009_demographics_multiyear.sql
-- Converts venue_demographics from single-row-per-venue to
-- multi-year storage, enabling time-varying demographics.
-- Also adds trend columns to game_features.
-- ============================================================

-- Step 1: Change PK from (venue_id) to (venue_id, census_year)
-- Existing data (census_year=2023) is preserved.
ALTER TABLE milb.venue_demographics DROP CONSTRAINT venue_demographics_pkey;
ALTER TABLE milb.venue_demographics ADD PRIMARY KEY (venue_id, census_year);

-- Efficient "latest year per venue" lookups
CREATE INDEX IF NOT EXISTS idx_vd_venue_year
    ON milb.venue_demographics (venue_id, census_year DESC);

-- Step 2: New trend columns in game_features
-- (populated by build_features.py via its TRUNCATE + INSERT pattern)
ALTER TABLE milb.game_features ADD COLUMN IF NOT EXISTS census_year SMALLINT;
ALTER TABLE milb.game_features ADD COLUMN IF NOT EXISTS population_change_5yr_pct NUMERIC(6,3);
ALTER TABLE milb.game_features ADD COLUMN IF NOT EXISTS income_change_5yr_pct NUMERIC(6,3);
ALTER TABLE milb.game_features ADD COLUMN IF NOT EXISTS poverty_rate_change_5yr NUMERIC(5,2);
ALTER TABLE milb.game_features ADD COLUMN IF NOT EXISTS population_trend TEXT;
