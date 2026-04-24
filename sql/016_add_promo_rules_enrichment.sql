-- Two-tier enrichment tracking. Rules classifier runs first and covers
-- unambiguous cases; LLM handles the rest.
--
-- rules_enriched_at  - timestamp when rules produced a full classification
-- enrichment_method  - which path produced the currently stored flags
--                      ('rules' | 'llm')
--
-- Existing llm_enriched_at is retained as-is so historical audit still works.

ALTER TABLE milb.game_promotions
    ADD COLUMN IF NOT EXISTS rules_enriched_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS enrichment_method TEXT;

-- Backfill existing LLM-enriched rows so the method column is populated.
UPDATE milb.game_promotions
   SET enrichment_method = 'llm'
 WHERE llm_enriched_at IS NOT NULL
   AND enrichment_method IS NULL;

CREATE INDEX IF NOT EXISTS idx_gp_enrichment_method
    ON milb.game_promotions (enrichment_method);
