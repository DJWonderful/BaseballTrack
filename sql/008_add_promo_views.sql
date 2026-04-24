-- ============================================================
-- 008_add_promo_views.sql
-- Views for team-level promotional strategy profiling,
-- intensity tiering, day-of-week distribution, and promo
-- novelty tracking.  Plus a table for promo-based clustering.
-- ============================================================

-- Helper: latest season with a substantial body of enriched promo data on
-- completed games. Auto-advances to 2026, 2027, etc. once that season matures,
-- but stays on 2025 early in 2026 when only a few weeks of games have played.
-- Threshold: >= 5000 enriched promos on completed games (~40/team across ~120
-- teams), which is comfortably above the 30-promo "normal" quality cutoff used
-- by the clustering pipeline.


-- ──────────────────────────────────────────────────────────────
-- 1. Team promo profile: one row per team, all strategy dimensions
-- ──────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW milb.v_team_promo_profile AS
WITH latest_season AS (
    SELECT MAX(season) AS season FROM (
        SELECT g.season
        FROM milb.game_promotions p
        JOIN milb.games g ON p.game_pk = g.game_pk
        WHERE p.enrichment_method IS NOT NULL
          AND g.sport_id IN (11,12,13,14)
          AND g.game_type = 'R'
          AND g.abstract_game_state = 'Final'
        GROUP BY g.season
        HAVING COUNT(*) >= 5000
    ) s
),
home_games AS (
    -- Total home games per team in the latest promo season
    SELECT g.home_team_id AS team_id,
           COUNT(*)       AS total_home_games
    FROM milb.games g, latest_season ls
    WHERE g.season = ls.season
      AND g.sport_id IN (11,12,13,14)
      AND g.abstract_game_state = 'Final'
      AND g.attendance IS NOT NULL
      AND g.attendance > 0
      AND g.game_type = 'R'
    GROUP BY g.home_team_id
),
promo_agg AS (
    -- Per-team promo stats from enriched promotions
    SELECT g.home_team_id                                          AS team_id,
           COUNT(*)                                                AS total_promos,
           COUNT(DISTINCT p.game_pk)                               AS games_with_promos,
           COUNT(DISTINCT p.offer_name)                            AS distinct_promo_names,
           -- Flag counts
           SUM(CASE WHEN p.is_fireworks      THEN 1 ELSE 0 END)   AS fireworks_count,
           SUM(CASE WHEN p.is_giveaway_item  THEN 1 ELSE 0 END)   AS giveaway_count,
           SUM(CASE WHEN p.is_recurring      THEN 1 ELSE 0 END)   AS recurring_count,
           SUM(CASE WHEN p.is_food_deal      THEN 1 ELSE 0 END)   AS food_deal_count,
           SUM(CASE WHEN p.is_ticket_deal    THEN 1 ELSE 0 END)   AS ticket_deal_count,
           SUM(CASE WHEN p.is_theme_night    THEN 1 ELSE 0 END)   AS theme_night_count,
           SUM(CASE WHEN p.is_kids_event     THEN 1 ELSE 0 END)   AS kids_event_count,
           SUM(CASE WHEN p.is_heritage_night THEN 1 ELSE 0 END)   AS heritage_count,
           SUM(CASE WHEN p.is_community_event THEN 1 ELSE 0 END)  AS community_count,
           SUM(CASE WHEN p.is_entertainment  THEN 1 ELSE 0 END)   AS entertain_count,
           SUM(CASE WHEN p.is_dog_friendly   THEN 1 ELSE 0 END)   AS dog_count,
           -- Weekend promos (PG DOW: 0=Sun, 5=Fri, 6=Sat)
           SUM(CASE WHEN EXTRACT(DOW FROM g.game_date) IN (0, 5, 6)
                    THEN 1 ELSE 0 END)                             AS weekend_promo_count
    FROM milb.game_promotions p
    JOIN milb.games g ON p.game_pk = g.game_pk,
         latest_season ls
    WHERE p.enrichment_method IS NOT NULL
      AND g.season = ls.season
      AND g.sport_id IN (11,12,13,14)
      AND g.abstract_game_state = 'Final'
      AND g.game_type = 'R'
    GROUP BY g.home_team_id
),
name_freq AS (
    -- Per (team, offer_name) frequency for entropy calc
    SELECT g.home_team_id AS team_id,
           p.offer_name,
           COUNT(*)       AS cnt
    FROM milb.game_promotions p
    JOIN milb.games g ON p.game_pk = g.game_pk,
         latest_season ls
    WHERE p.enrichment_method IS NOT NULL
      AND g.season = ls.season
      AND g.sport_id IN (11,12,13,14)
      AND g.abstract_game_state = 'Final'
      AND g.game_type = 'R'
    GROUP BY g.home_team_id, p.offer_name
),
team_entropy AS (
    -- Shannon entropy: -SUM(p * ln(p))
    SELECT nf.team_id,
           -SUM(
               (nf.cnt::double precision / pa.total_promos)
               * LN(nf.cnt::double precision / pa.total_promos)
           ) AS promo_entropy
    FROM name_freq nf
    JOIN promo_agg pa ON nf.team_id = pa.team_id
    WHERE pa.total_promos > 0
    GROUP BY nf.team_id
)
SELECT t.team_id,
       t.team_name,
       t.sport_id,
       COALESCE(hg.total_home_games, 0)            AS total_home_games,
       COALESCE(pa.games_with_promos, 0)            AS games_with_promos,
       COALESCE(pa.total_promos, 0)                 AS total_promos,
       -- Coverage & intensity
       ROUND(pa.games_with_promos::numeric
             / NULLIF(hg.total_home_games, 0), 3)   AS promo_coverage,
       ROUND(pa.total_promos::numeric
             / NULLIF(hg.total_home_games, 0), 3)   AS promos_per_game,
       ROUND(pa.total_promos::numeric
             / NULLIF(pa.games_with_promos, 0), 3)  AS promos_per_promo_game,
       -- Flag percentages (fraction of total promos)
       ROUND(pa.recurring_count::numeric
             / NULLIF(pa.total_promos, 0), 3)        AS pct_recurring,
       ROUND(pa.fireworks_count::numeric
             / NULLIF(pa.total_promos, 0), 3)        AS pct_fireworks,
       ROUND(pa.giveaway_count::numeric
             / NULLIF(pa.total_promos, 0), 3)        AS pct_giveaway,
       ROUND(pa.food_deal_count::numeric
             / NULLIF(pa.total_promos, 0), 3)        AS pct_food_deal,
       ROUND(pa.ticket_deal_count::numeric
             / NULLIF(pa.total_promos, 0), 3)        AS pct_ticket_deal,
       ROUND(pa.theme_night_count::numeric
             / NULLIF(pa.total_promos, 0), 3)        AS pct_theme_night,
       ROUND(pa.kids_event_count::numeric
             / NULLIF(pa.total_promos, 0), 3)        AS pct_kids_event,
       ROUND(pa.heritage_count::numeric
             / NULLIF(pa.total_promos, 0), 3)        AS pct_heritage,
       ROUND(pa.community_count::numeric
             / NULLIF(pa.total_promos, 0), 3)        AS pct_community,
       ROUND(pa.entertain_count::numeric
             / NULLIF(pa.total_promos, 0), 3)        AS pct_entertain,
       ROUND(pa.dog_count::numeric
             / NULLIF(pa.total_promos, 0), 3)        AS pct_dog,
       -- Weekend concentration
       ROUND(pa.weekend_promo_count::numeric
             / NULLIF(pa.total_promos, 0), 3)        AS pct_weekend_promos,
       -- Diversity
       COALESCE(pa.distinct_promo_names, 0)          AS distinct_promo_names,
       ROUND(te.promo_entropy::numeric, 3)           AS promo_entropy,
       -- Absolutes
       COALESCE(pa.fireworks_count, 0)               AS fireworks_count,
       COALESCE(pa.giveaway_count, 0)                AS giveaway_count,
       -- Quality tier for filtering
       CASE
           WHEN COALESCE(pa.total_promos, 0) < 10 THEN 'exclude'
           WHEN pa.total_promos < 30              THEN 'low'
           ELSE 'normal'
       END                                           AS promo_quality
FROM milb.teams t
JOIN home_games hg ON t.team_id = hg.team_id
LEFT JOIN promo_agg pa ON t.team_id = pa.team_id
LEFT JOIN team_entropy te ON t.team_id = te.team_id
WHERE t.sport_id IN (11,12,13,14);


-- ──────────────────────────────────────────────────────────────
-- 2. Intensity tiers (High / Medium / Low / None)
-- ──────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW milb.v_team_promo_intensity AS
WITH thresholds AS (
    SELECT
        PERCENTILE_CONT(0.33) WITHIN GROUP (ORDER BY promos_per_game) AS p33,
        PERCENTILE_CONT(0.66) WITHIN GROUP (ORDER BY promos_per_game) AS p66
    FROM milb.v_team_promo_profile
    WHERE promo_quality != 'exclude'
)
SELECT p.team_id,
       p.team_name,
       p.sport_id,
       p.promos_per_game,
       CASE
           WHEN p.promo_quality = 'exclude'    THEN 'None'
           WHEN p.promos_per_game <= t.p33     THEN 'Low'
           WHEN p.promos_per_game <= t.p66     THEN 'Medium'
           ELSE 'High'
       END AS intensity_tier
FROM milb.v_team_promo_profile p, thresholds t;


-- ──────────────────────────────────────────────────────────────
-- 3. Day-of-week promo distribution per team
-- ──────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW milb.v_team_promo_dayofweek AS
WITH latest_season AS (
    SELECT MAX(season) AS season FROM (
        SELECT g.season
        FROM milb.game_promotions p
        JOIN milb.games g ON p.game_pk = g.game_pk
        WHERE p.enrichment_method IS NOT NULL
          AND g.sport_id IN (11,12,13,14)
          AND g.game_type = 'R'
          AND g.abstract_game_state = 'Final'
        GROUP BY g.season
        HAVING COUNT(*) >= 5000
    ) s
)
SELECT g.home_team_id AS team_id,
       COUNT(*) AS total_promos,
       -- Counts
       COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 1) AS mon_promos,
       COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 2) AS tue_promos,
       COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 3) AS wed_promos,
       COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 4) AS thu_promos,
       COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 5) AS fri_promos,
       COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 6) AS sat_promos,
       COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 0) AS sun_promos,
       -- Percentages
       ROUND(COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 1)::numeric
             / NULLIF(COUNT(*), 0), 3) AS pct_mon,
       ROUND(COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 2)::numeric
             / NULLIF(COUNT(*), 0), 3) AS pct_tue,
       ROUND(COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 3)::numeric
             / NULLIF(COUNT(*), 0), 3) AS pct_wed,
       ROUND(COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 4)::numeric
             / NULLIF(COUNT(*), 0), 3) AS pct_thu,
       ROUND(COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 5)::numeric
             / NULLIF(COUNT(*), 0), 3) AS pct_fri,
       ROUND(COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 6)::numeric
             / NULLIF(COUNT(*), 0), 3) AS pct_sat,
       ROUND(COUNT(*) FILTER (WHERE EXTRACT(DOW FROM g.game_date) = 0)::numeric
             / NULLIF(COUNT(*), 0), 3) AS pct_sun
FROM milb.game_promotions p
JOIN milb.games g ON p.game_pk = g.game_pk,
     latest_season ls
WHERE p.enrichment_method IS NOT NULL
  AND g.season = ls.season
  AND g.sport_id IN (11,12,13,14)
  AND g.abstract_game_state = 'Final'
  AND g.game_type = 'R'
GROUP BY g.home_team_id;


-- ──────────────────────────────────────────────────────────────
-- 4. Promo novelty: occurrence number within team-season
-- ──────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW milb.v_game_promo_novelty AS
SELECT p.promotion_id,
       p.game_pk,
       p.offer_name,
       g.home_team_id AS team_id,
       g.season,
       g.game_date,
       ROW_NUMBER() OVER (
           PARTITION BY g.home_team_id, g.season, p.offer_name
           ORDER BY g.game_date, g.game_pk
       ) AS occurrence_num
FROM milb.game_promotions p
JOIN milb.games g ON p.game_pk = g.game_pk
WHERE p.enrichment_method IS NOT NULL
  AND g.sport_id IN (11,12,13,14)
  AND g.abstract_game_state = 'Final'
  AND g.game_type = 'R';


-- ──────────────────────────────────────────────────────────────
-- 5. Promo strategy cluster assignments (populated by Python)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS milb.team_promo_clusters (
    team_id              INTEGER PRIMARY KEY,
    promo_cluster_id     INTEGER NOT NULL,
    promo_cluster_label  TEXT,
    centroid_distance    NUMERIC(8,4),
    computed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id               INTEGER REFERENCES milb.analysis_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_tpc_cluster
    ON milb.team_promo_clusters (promo_cluster_id);


-- ──────────────────────────────────────────────────────────────
-- 6. Cluster descriptions (human-written, one row per cluster)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS milb.promo_cluster_descriptions (
    promo_cluster_id    INTEGER PRIMARY KEY,
    promo_cluster_label TEXT NOT NULL,
    description         TEXT,
    key_traits          TEXT,
    example_teams       TEXT,
    generated_at        TIMESTAMPTZ DEFAULT NOW()
);
