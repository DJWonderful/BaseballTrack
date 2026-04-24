-- ============================================================
-- 003_seed_reference.sql
-- Seeds the milb.sports table with the 4 MiLB levels.
-- ============================================================

INSERT INTO milb.sports (sport_id, sport_name, sport_code, sort_order)
VALUES
    (11, 'Triple-A',  'aaa', 1),
    (12, 'Double-A',  'afa', 2),
    (13, 'High-A',    'afx', 3),
    (14, 'Single-A',  'asx', 4)
ON CONFLICT (sport_id) DO UPDATE SET
    sport_name = EXCLUDED.sport_name,
    sport_code = EXCLUDED.sport_code,
    sort_order = EXCLUDED.sort_order,
    updated_at = NOW();
