"""Collect game schedules from MLB Stats API."""

import json
import os
import time

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.connection import get_session
from src.utils.logger import get_logger

logger = get_logger("collectors.schedule")

MLB_API = os.getenv("MLB_API_BASE", "https://statsapi.mlb.com/api/v1")
SPORT_IDS = [11, 12, 13, 14]


def _ensure_venue(session: Session, venue_id: int, venue_name: str):
    """Insert a stub venue record if it doesn't exist (for historical games)."""
    session.execute(text("""
        INSERT INTO milb.venues (venue_id, venue_name, created_at)
        VALUES (:id, :name, NOW())
        ON CONFLICT (venue_id) DO NOTHING
    """), {"id": venue_id, "name": venue_name or "Unknown"})


def _ensure_team(session: Session, team_id: int, team_name: str):
    """Insert a stub team record if it doesn't exist (for historical games)."""
    session.execute(text("""
        INSERT INTO milb.teams (team_id, team_name, created_at)
        VALUES (:id, :name, NOW())
        ON CONFLICT (team_id) DO NOTHING
    """), {"id": team_id, "name": team_name or "Unknown"})


def collect_schedule(session: Session, seasons: list[int] | None = None,
                     sport_ids: list[int] | None = None,
                     team_id: int | None = None,
                     force: bool = False) -> int:
    """Collect game schedules and upsert into milb.games.

    Args:
        session: SQLAlchemy session
        seasons: List of years to collect (default: [2023, 2024, 2025])
        sport_ids: List of sport IDs (default: all 4 levels)
        team_id: Optional single team ID to filter by
        force: If True, fetch all seasons; otherwise skip completed ones

    Returns:
        Total number of games upserted.
    """
    from src.collectors.delta import active_seasons, parse_seasons_env

    if seasons is None:
        seasons = parse_seasons_env()
    seasons, skipped = active_seasons(seasons, force=force)
    if skipped:
        logger.info(f"Schedule: skipping completed seasons {skipped}")
    if not seasons:
        logger.info("Schedule: no active seasons to collect")
        return 0
    if sport_ids is None:
        sport_ids = SPORT_IDS

    total_upserted = 0

    for season in seasons:
        for sport_id in sport_ids:
            params = {
                "sportId": sport_id,
                "season": season,
                # No gameType filter → returns R (regular season) + all playoff rounds
                # (F=Wild Card, D=Division Series, L=LCS, W=Championship, S=Spring, E=Exhibition)
            }
            if team_id:
                params["teamId"] = team_id

            url = f"{MLB_API}/schedule"
            logger.info(f"Fetching schedule: sportId={sport_id}, season={season} (all game types)"
                        + (f", teamId={team_id}" if team_id else ""))

            resp = httpx.get(url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            count = 0
            for date_entry in data.get("dates", []):
                for game in date_entry.get("games", []):
                    game_pk = game.get("gamePk")
                    if not game_pk:
                        continue

                    home = game.get("teams", {}).get("home", {})
                    away = game.get("teams", {}).get("away", {})
                    home_team = home.get("team", {})
                    away_team = away.get("team", {})
                    venue = game.get("venue", {})
                    status = game.get("status", {})

                    venue_id = venue.get("id")
                    home_team_id = home_team.get("id")
                    away_team_id = away_team.get("id")

                    # Ensure FK targets exist (handles renamed/moved teams and venues)
                    if venue_id:
                        _ensure_venue(session, venue_id, venue.get("name"))
                    if home_team_id:
                        _ensure_team(session, home_team_id, home_team.get("name"))
                    if away_team_id:
                        _ensure_team(session, away_team_id, away_team.get("name"))

                    session.execute(text("""
                        INSERT INTO milb.games (
                            game_pk, game_date, game_datetime, season, game_type,
                            day_night, doubleheader, game_number, scheduled_innings,
                            status_code, status_detail, abstract_game_state,
                            home_team_id, home_team_name, away_team_id, away_team_name,
                            home_score, away_score,
                            venue_id, venue_name, series_description, sport_id,
                            raw_json, created_at
                        ) VALUES (
                            :game_pk, :game_date, :game_datetime, :season, :game_type,
                            :day_night, :doubleheader, :game_number, :scheduled_innings,
                            :status_code, :status_detail, :abstract_game_state,
                            :home_team_id, :home_team_name, :away_team_id, :away_team_name,
                            :home_score, :away_score,
                            :venue_id, :venue_name, :series_description, :sport_id,
                            CAST(:raw_json AS jsonb), NOW()
                        )
                        ON CONFLICT (game_pk) DO UPDATE SET
                            status_code = EXCLUDED.status_code,
                            status_detail = EXCLUDED.status_detail,
                            abstract_game_state = EXCLUDED.abstract_game_state,
                            home_score = EXCLUDED.home_score,
                            away_score = EXCLUDED.away_score,
                            updated_at = NOW()
                    """), {
                        "game_pk": game_pk,
                        "game_date": game.get("officialDate"),
                        "game_datetime": game.get("gameDate"),
                        "season": season,
                        "game_type": game.get("gameType", "R"),
                        "day_night": game.get("dayNight"),
                        "doubleheader": game.get("doubleHeader"),
                        "game_number": game.get("gameNumber"),
                        "scheduled_innings": game.get("scheduledInnings"),
                        "status_code": status.get("codedGameState"),
                        "status_detail": status.get("detailedState"),
                        "abstract_game_state": status.get("abstractGameState"),
                        "home_team_id": home_team_id,
                        "home_team_name": home_team.get("name"),
                        "away_team_id": away_team_id,
                        "away_team_name": away_team.get("name"),
                        "home_score": home.get("score"),
                        "away_score": away.get("score"),
                        "venue_id": venue_id,
                        "venue_name": venue.get("name"),
                        "series_description": game.get("seriesDescription"),
                        "sport_id": sport_id,
                        "raw_json": json.dumps(game),
                    })
                    count += 1

            session.commit()
            total_upserted += count
            logger.info(f"  Upserted {count} games for sportId={sport_id}, season={season}")
            time.sleep(0.5)

    logger.info(f"Schedule collection complete: {total_upserted} total games")
    return total_upserted


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    session = get_session()
    try:
        collect_schedule(session)
    finally:
        session.close()
