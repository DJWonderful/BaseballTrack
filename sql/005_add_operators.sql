-- ============================================================
-- 005_add_operators.sql
-- Adds team operator/ownership tracking.
-- ============================================================

-- 1. Operator lookup table
CREATE TABLE IF NOT EXISTS milb.team_operators (
    operator_id     SERIAL PRIMARY KEY,
    operator_name   TEXT NOT NULL UNIQUE,
    operator_type   TEXT,          -- 'conglomerate', 'independent', etc.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

-- 2. Add operator FK to teams
ALTER TABLE milb.teams
    ADD COLUMN IF NOT EXISTS operator_id INTEGER REFERENCES milb.team_operators(operator_id);

CREATE INDEX IF NOT EXISTS idx_teams_operator ON milb.teams (operator_id);

-- 3. Seed Diamond Baseball Holdings
INSERT INTO milb.team_operators (operator_name, operator_type)
VALUES ('Diamond Baseball Holdings', 'conglomerate')
ON CONFLICT (operator_name) DO NOTHING;

-- 4. Tag all known DBH teams (48 teams, ~47 in our DB)
UPDATE milb.teams
SET    operator_id = (SELECT operator_id FROM milb.team_operators WHERE operator_name = 'Diamond Baseball Holdings'),
       updated_at  = NOW()
WHERE  team_id IN (
    -- Triple-A (14)
    342,   -- Albuquerque Isotopes
    494,   -- Charlotte Knights
    431,   -- Gwinnett Stripers
    451,   -- Iowa Cubs
    416,   -- Louisville Bats
    235,   -- Memphis Redbirds
    568,   -- Norfolk Tides
    238,   -- Oklahoma City Comets
    541,   -- Omaha Storm Chasers
    531,   -- Scranton/Wilkes-Barre RailRiders
    1960,  -- St. Paul Saints
    5434,  -- Sugar Land Space Cowboys
    552,   -- Syracuse Mets
    533,   -- Worcester Red Sox
    -- Double-A (13)
    452,   -- Altoona Curve
    574,   -- Arkansas Travelers
    505,   -- Binghamton Rumble Ponies
    247,   -- Birmingham Barons
    6325,  -- Columbus Clingstones
    482,   -- Corpus Christi Hooks
    547,   -- Harrisburg Senators
    237,   -- Midland RockHounds
    463,   -- New Hampshire Fisher Cats
    546,   -- Portland Sea Dogs
    522,   -- Reading Fightin Phils
    440,   -- Springfield Cardinals
    260,   -- Tulsa Drillers
    3898,  -- Wichita Wind Surge
    -- High-A (9)
    453,   -- Brooklyn Cyclones
    459,   -- Dayton Dragons
    448,   -- Hickory Crawdads
    537,   -- Hudson Valley Renegades
    499,   -- Lansing Lugnuts
    432,   -- Rome Emperors
    435,   -- Vancouver Canadians
    580,   -- Winston-Salem Dash
    572,   -- Wisconsin Timber Rattlers
    -- Single-A (11)
    478,   -- Augusta GreenJackets
    3712,  -- Fayetteville Woodpeckers
    436,   -- Fredericksburg Nationals
    259,   -- Fresno Grizzlies
    6324,  -- Hub City Spartanburgers
    401,   -- Inland Empire 66ers
    526,   -- Rancho Cucamonga Quakes
    414,   -- Salem Red Sox / RidgeYaks
    476,   -- San Jose Giants
    460    -- Tri-City Dust Devils
);
