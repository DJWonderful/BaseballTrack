-- Adds venue timezone and game-time bucketing.
--
-- venues.timezone is derived once from (latitude, longitude) via timezonefinder.
-- game_features gains local_start_hour + start_time_bucket, computed from
-- games.game_datetime converted into venue-local time. These displace the
-- unreliable games.day_night column on display/analysis surfaces.
--
-- Fixed buckets (locked in GAME_TIMES_ANALYSIS_PLAN.md):
--   morning        < 11
--   noon           11, 12
--   matinee        13, 14, 15
--   early_evening  16, 17
--   evening        18, 19
--   late           >= 20

ALTER TABLE milb.venues
    ADD COLUMN IF NOT EXISTS timezone TEXT;

ALTER TABLE milb.game_features
    ADD COLUMN IF NOT EXISTS local_start_hour SMALLINT,
    ADD COLUMN IF NOT EXISTS start_time_bucket TEXT;

CREATE INDEX IF NOT EXISTS idx_gf_time_bucket
    ON milb.game_features (start_time_bucket);
