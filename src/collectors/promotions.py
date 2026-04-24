"""Collect game promotions from MLB Stats API."""

import json
import os
import time

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.connection import get_session
from src.utils.logger import get_logger

logger = get_logger("collectors.promotions")

MLB_API = os.getenv("MLB_API_BASE", "https://statsapi.mlb.com/api/v1")
SPORT_IDS = [11, 12, 13, 14]


def collect_promotions(session: Session, seasons: list[int] | None = None,
                       sport_ids: list[int] | None = None,
                       team_id: int | None = None,
                       force: bool = False) -> int:
    """Collect promotions for all games via hydrated schedule endpoint.

    Returns total number of promotions upserted.
    """
    from src.collectors.delta import active_seasons, parse_seasons_env

    if seasons is None:
        seasons = parse_seasons_env()
    seasons, skipped = active_seasons(seasons, force=force)
    if skipped:
        logger.info(f"Promotions: skipping completed seasons {skipped}")
    if not seasons:
        logger.info("Promotions: no active seasons to collect")
        return 0
    if sport_ids is None:
        sport_ids = SPORT_IDS

    total_upserted = 0

    for season in seasons:
        for sport_id in sport_ids:
            # Fetch schedule with promotions hydrated, month by month to keep payloads manageable
            for month in range(3, 10):  # March through September covers MiLB season
                start = f"{season}-{month:02d}-01"
                if month == 9:
                    end = f"{season}-09-30"
                else:
                    end = f"{season}-{month + 1:02d}-01"

                params = {
                    "sportId": sport_id,
                    "startDate": start,
                    "endDate": end,
                    "gameType": "R",
                    "hydrate": "game(promotions)",
                }
                if team_id:
                    params["teamId"] = team_id

                url = f"{MLB_API}/schedule"
                resp = httpx.get(url, params=params, timeout=60)
                if resp.status_code != 200:
                    logger.warning(f"Schedule fetch failed: {resp.status_code} for {start}")
                    continue

                data = resp.json()
                count = 0

                for date_entry in data.get("dates", []):
                    for game in date_entry.get("games", []):
                        game_pk = game.get("gamePk")
                        promotions = game.get("promotions", [])

                        if not game_pk or not promotions:
                            continue

                        for promo in promotions:
                            offer_id = promo.get("offerId")
                            image = promo.get("imageUrl")
                            # Filter out "undefined" string images
                            if image == "undefined":
                                image = None

                            thumbnail = promo.get("thumbnailUrl")
                            if thumbnail == "undefined":
                                thumbnail = None

                            try:
                                session.execute(text("""
                                    INSERT INTO milb.game_promotions (
                                        game_pk, offer_id, offer_name, offer_type,
                                        description, distribution, presented_by,
                                        image_url, thumbnail_url, display_order,
                                        raw_json, created_at
                                    ) VALUES (
                                        :game_pk, :offer_id, :offer_name, :offer_type,
                                        :description, :distribution, :presented_by,
                                        :image_url, :thumbnail_url, :display_order,
                                        CAST(:raw_json AS jsonb), NOW()
                                    )
                                    ON CONFLICT (game_pk, offer_id) DO UPDATE SET
                                        offer_name = EXCLUDED.offer_name,
                                        offer_type = EXCLUDED.offer_type,
                                        description = EXCLUDED.description,
                                        distribution = EXCLUDED.distribution,
                                        presented_by = EXCLUDED.presented_by,
                                        image_url = EXCLUDED.image_url,
                                        thumbnail_url = EXCLUDED.thumbnail_url,
                                        display_order = EXCLUDED.display_order,
                                        raw_json = EXCLUDED.raw_json,
                                        updated_at = NOW()
                                """), {
                                    "game_pk": game_pk,
                                    "offer_id": offer_id,
                                    "offer_name": promo.get("name"),
                                    "offer_type": promo.get("offerType"),
                                    "description": promo.get("description"),
                                    "distribution": promo.get("distribution"),
                                    "presented_by": promo.get("presentedBy"),
                                    "image_url": image,
                                    "thumbnail_url": thumbnail,
                                    "display_order": promo.get("order"),
                                    "raw_json": json.dumps(promo),
                                })
                                count += 1
                            except Exception as e:
                                # FK violation if game_pk not in games table yet — skip
                                session.rollback()
                                logger.debug(f"Skipped promo for game {game_pk}: {e}")

                session.commit()
                total_upserted += count
                time.sleep(0.5)

            logger.info(f"Promotions: sportId={sport_id}, season={season} — {total_upserted} total")

    logger.info(f"Promotions collection complete: {total_upserted} total promotions")
    return total_upserted


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    session = get_session()
    try:
        collect_promotions(session)
    finally:
        session.close()
