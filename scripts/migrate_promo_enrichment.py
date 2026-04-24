"""One-time migration: add LLM enrichment columns to milb.game_promotions.

Run once before enrich_promotions.py. Safe to re-run (uses IF NOT EXISTS).
"""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from rich.console import Console
from sqlalchemy import text

from src.db.connection import get_session
from src.utils.logger import get_logger

logger = get_logger("migrate_promo_enrichment")
console = Console()


def run():
    console.print("\n[bold blue]═══ Promotion Enrichment Migration ═══[/bold blue]\n")
    session = get_session()
    try:
        console.print("Adding LLM enrichment columns to milb.game_promotions...")
        session.execute(text("""
            ALTER TABLE milb.game_promotions
              ADD COLUMN IF NOT EXISTS promo_category        TEXT,
              ADD COLUMN IF NOT EXISTS is_fireworks          BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS is_giveaway_item      BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS is_food_deal          BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS is_ticket_deal        BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS is_theme_night        BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS is_heritage_night     BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS is_kids_event         BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS is_community_event    BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS is_autographs         BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS is_entertainment      BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS is_recurring          BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS is_dog_friendly       BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS has_celebrity         BOOLEAN DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS giveaway_limit        INTEGER,
              ADD COLUMN IF NOT EXISTS target_audience       TEXT,
              ADD COLUMN IF NOT EXISTS llm_notes             TEXT,
              ADD COLUMN IF NOT EXISTS llm_model             TEXT,
              ADD COLUMN IF NOT EXISTS llm_enriched_at       TIMESTAMPTZ
        """))

        # Partial index — makes the "WHERE llm_enriched_at IS NULL" query in enrich_promotions fast
        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_game_promotions_unenriched
              ON milb.game_promotions (promotion_id)
              WHERE llm_enriched_at IS NULL
        """))

        session.commit()

        result = session.execute(text("SELECT COUNT(*) FROM milb.game_promotions"))
        console.print(f"[green]Migration complete.[/green] Total promotions: {result.scalar()}")

    finally:
        session.close()


if __name__ == "__main__":
    run()
