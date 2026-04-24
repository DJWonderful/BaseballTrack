-- ============================================================
-- 006_add_demographics.sql
-- Stores Census demographic data per venue location.
-- Populated by scripts/collect_demographics.py.
-- ============================================================

CREATE TABLE IF NOT EXISTS milb.venue_demographics (
    venue_id                INTEGER PRIMARY KEY REFERENCES milb.venues(venue_id),
    census_year             INTEGER NOT NULL,

    -- FIPS codes for joining to Census data
    state_fips              TEXT,
    county_fips             TEXT,
    place_fips              TEXT,
    cbsa_code               TEXT,

    -- Place-level (city/town) demographics
    place_name              TEXT,
    place_population        INTEGER,
    place_median_income     INTEGER,
    place_per_capita_income INTEGER,
    place_poverty_rate      NUMERIC(5, 2),

    -- MSA-level demographics
    msa_name                TEXT,
    msa_population          INTEGER,
    msa_median_income       INTEGER,
    msa_per_capita_income   INTEGER,
    msa_poverty_rate        NUMERIC(5, 2),

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ
);
