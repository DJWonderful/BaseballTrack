"""Collect per-game details (attendance, weather, duration) from MLB Stats API game feeds."""

import json
import os
import time

import httpx
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from sqlalchemy import text
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.db.connection import get_session
from src.utils.logger import get_logger

logger = get_logger("collectors.game_feed")

RATE_LIMIT = float(os.getenv("MLB_RATE_LIMIT", "2"))
FIELDS = (
    "gameData,gameInfo,attendance,gameDurationMinutes,firstPitch,"
    "weather,condition,temp,wind,"
    "datetime,dateTime,dayNight,time,ampm,"
    "venue,id,name,fieldInfo,capacity,turfType,roofType,"
    "leftLine,leftCenter,center,rightCenter,rightLine"
)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectTimeout)),
)
def fetch_game_feed(client: httpx.Client, game_pk: int) -> dict | None:
    """Fetch a single game feed with retry logic."""
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live?fields={FIELDS}"
    resp = client.get(url, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def parse_temp(temp_str: str | None) -> int | None:
    """Parse temperature string like '82' to integer."""
    if not temp_str:
        return None
    try:
        return int(temp_str)
    except (ValueError, TypeError):
        return None


def collect_game_feeds(session: Session, team_id: int | None = None,
                       season: int | None = None,
                       batch_size: int = 100) -> int:
    """Collect game feed details for games that haven't been enriched yet.

    Args:
        session: SQLAlchemy session
        team_id: Optional - only collect for this team's home games
        season: Optional - only collect for this season
        batch_size: Commit every N games

    Returns:
        Number of games updated.
    """
    # Find games needing enrichment (Final games without attendance data)
    query = """
        SELECT game_pk FROM milb.games
        WHERE abstract_game_state = 'Final'
          AND attendance IS NULL
    """
    params = {}
    if team_id:
        query += " AND home_team_id = :team_id"
        params["team_id"] = team_id
    if season:
        query += " AND season = :season"
        params["season"] = season
    query += " ORDER BY game_date"

    result = session.execute(text(query), params)
    game_pks = [row[0] for row in result]

    if not game_pks:
        logger.info("No games need enrichment")
        return 0

    logger.info(f"Enriching {len(game_pks)} games from game feeds")
    updated = 0
    delay = 1.0 / RATE_LIMIT

    with httpx.Client() as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Game feeds", total=len(game_pks))

            for i, game_pk in enumerate(game_pks):
                try:
                    data = fetch_game_feed(client, game_pk)
                    if data is None:
                        progress.advance(task)
                        continue

                    game_data = data.get("gameData", {})
                    game_info = game_data.get("gameInfo", {})
                    weather = game_data.get("weather", {})
                    dt = game_data.get("datetime", {})

                    attendance = game_info.get("attendance")
                    # Some games report 0 attendance — keep it, it's valid data
                    duration = game_info.get("gameDurationMinutes")
                    first_pitch = game_info.get("firstPitch")

                    session.execute(text("""
                        UPDATE milb.games SET
                            attendance = :attendance,
                            game_duration_minutes = :duration,
                            first_pitch = :first_pitch,
                            day_night = COALESCE(:day_night, day_night),
                            weather_condition = :weather_condition,
                            weather_temp_f = :weather_temp,
                            weather_wind = :weather_wind,
                            raw_json = COALESCE(raw_json, CAST('{}' AS jsonb)) || CAST(:feed_json AS jsonb),
                            updated_at = NOW()
                        WHERE game_pk = :game_pk
                    """), {
                        "game_pk": game_pk,
                        "attendance": attendance,
                        "duration": duration,
                        "first_pitch": first_pitch,
                        "day_night": dt.get("dayNight"),
                        "weather_condition": weather.get("condition"),
                        "weather_temp": parse_temp(weather.get("temp")),
                        "weather_wind": weather.get("wind"),
                        "feed_json": json.dumps({"gameInfo": game_info, "weather": weather}),
                    })
                    updated += 1

                except Exception as e:
                    logger.warning(f"Failed to fetch game {game_pk}: {e}")

                # Commit in batches
                if (i + 1) % batch_size == 0:
                    session.commit()

                progress.advance(task)
                time.sleep(delay)

    session.commit()
    logger.info(f"Game feed collection complete: {updated}/{len(game_pks)} games enriched")
    return updated


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    session = get_session()
    try:
        collect_game_feeds(session)
    finally:
        session.close()
