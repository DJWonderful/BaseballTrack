"""Collect teams, venues, leagues, divisions, and organizations from MLB Stats API."""

import json
import os
import time

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.connection import get_session
from src.utils.logger import get_logger

logger = get_logger("collectors.teams")

MLB_API = os.getenv("MLB_API_BASE", "https://statsapi.mlb.com/api/v1")
SPORT_IDS = [11, 12, 13, 14]


def collect_teams(session: Session, season: int = 2025) -> dict[str, int]:
    """Collect all MiLB teams and related reference data for a given season.

    Returns dict with counts of upserted records by table.
    """
    counts = {"organizations": 0, "leagues": 0, "divisions": 0, "venues": 0, "teams": 0}

    for sport_id in SPORT_IDS:
        url = f"{MLB_API}/teams?sportId={sport_id}&season={season}&hydrate=venue(location)"
        logger.info(f"Fetching teams for sportId={sport_id}, season={season}")

        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for team_data in data.get("teams", []):
            # Organization (parent MLB club)
            parent = team_data.get("parentOrgName")
            parent_id = team_data.get("parentOrgId")
            if parent_id:
                session.execute(text("""
                    INSERT INTO milb.organizations (org_id, org_name, created_at)
                    VALUES (:org_id, :org_name, NOW())
                    ON CONFLICT (org_id) DO UPDATE SET
                        org_name = EXCLUDED.org_name, updated_at = NOW()
                """), {"org_id": parent_id, "org_name": parent or "Unknown"})
                counts["organizations"] += 1

            # League
            league = team_data.get("league", {})
            league_id = league.get("id")
            if league_id:
                session.execute(text("""
                    INSERT INTO milb.leagues (league_id, league_name, sport_id, raw_json, created_at)
                    VALUES (:league_id, :league_name, :sport_id, CAST(:raw_json AS jsonb), NOW())
                    ON CONFLICT (league_id) DO UPDATE SET
                        league_name = EXCLUDED.league_name,
                        sport_id = EXCLUDED.sport_id,
                        raw_json = EXCLUDED.raw_json,
                        updated_at = NOW()
                """), {
                    "league_id": league_id,
                    "league_name": league.get("name", "Unknown"),
                    "sport_id": sport_id,
                    "raw_json": json.dumps(league) if league else None,
                })
                counts["leagues"] += 1

            # Division
            division = team_data.get("division", {})
            div_id = division.get("id")
            if div_id:
                session.execute(text("""
                    INSERT INTO milb.divisions (division_id, division_name, league_id, raw_json, created_at)
                    VALUES (:division_id, :division_name, :league_id, CAST(:raw_json AS jsonb), NOW())
                    ON CONFLICT (division_id) DO UPDATE SET
                        division_name = EXCLUDED.division_name,
                        league_id = EXCLUDED.league_id,
                        raw_json = EXCLUDED.raw_json,
                        updated_at = NOW()
                """), {
                    "division_id": div_id,
                    "division_name": division.get("name", "Unknown"),
                    "league_id": league_id,
                    "raw_json": json.dumps(division) if division else None,
                })
                counts["divisions"] += 1

            # Venue
            venue = team_data.get("venue", {})
            venue_id = venue.get("id")
            if venue_id:
                location = venue.get("location", {})
                coords = location.get("defaultCoordinates", {})
                session.execute(text("""
                    INSERT INTO milb.venues (
                        venue_id, venue_name, city, state, state_abbrev,
                        postal_code, country, latitude, longitude, created_at
                    ) VALUES (
                        :venue_id, :venue_name, :city, :state, :state_abbrev,
                        :postal_code, :country, :latitude, :longitude, NOW()
                    )
                    ON CONFLICT (venue_id) DO UPDATE SET
                        venue_name = EXCLUDED.venue_name,
                        city = EXCLUDED.city,
                        state = EXCLUDED.state,
                        state_abbrev = EXCLUDED.state_abbrev,
                        postal_code = EXCLUDED.postal_code,
                        country = EXCLUDED.country,
                        latitude = EXCLUDED.latitude,
                        longitude = EXCLUDED.longitude,
                        updated_at = NOW()
                """), {
                    "venue_id": venue_id,
                    "venue_name": venue.get("name", "Unknown"),
                    "city": location.get("city"),
                    "state": location.get("state"),
                    "state_abbrev": location.get("stateAbbrev"),
                    "postal_code": location.get("postalCode"),
                    "country": location.get("country"),
                    "latitude": coords.get("latitude"),
                    "longitude": coords.get("longitude"),
                })
                counts["venues"] += 1

            # Team
            team_id = team_data.get("id")
            if team_id:
                session.execute(text("""
                    INSERT INTO milb.teams (
                        team_id, team_name, short_name, abbreviation,
                        location_name, team_code, sport_id, league_id,
                        division_id, org_id, venue_id, raw_json, created_at
                    ) VALUES (
                        :team_id, :team_name, :short_name, :abbreviation,
                        :location_name, :team_code, :sport_id, :league_id,
                        :division_id, :org_id, :venue_id, CAST(:raw_json AS jsonb), NOW()
                    )
                    ON CONFLICT (team_id) DO UPDATE SET
                        team_name = EXCLUDED.team_name,
                        short_name = EXCLUDED.short_name,
                        abbreviation = EXCLUDED.abbreviation,
                        location_name = EXCLUDED.location_name,
                        team_code = EXCLUDED.team_code,
                        sport_id = EXCLUDED.sport_id,
                        league_id = EXCLUDED.league_id,
                        division_id = EXCLUDED.division_id,
                        org_id = EXCLUDED.org_id,
                        venue_id = EXCLUDED.venue_id,
                        raw_json = EXCLUDED.raw_json,
                        updated_at = NOW()
                """), {
                    "team_id": team_id,
                    "team_name": team_data.get("name", "Unknown"),
                    "short_name": team_data.get("shortName"),
                    "abbreviation": team_data.get("abbreviation"),
                    "location_name": team_data.get("locationName"),
                    "team_code": team_data.get("teamCode"),
                    "sport_id": sport_id,
                    "league_id": league_id,
                    "division_id": div_id,
                    "org_id": parent_id,
                    "venue_id": venue_id,
                    "raw_json": json.dumps(team_data),
                })
                counts["teams"] += 1

        session.commit()
        time.sleep(0.5)

    logger.info(f"Teams collection complete: {counts}")
    return counts


def collect_venue_details(session: Session, season: int = 2025):
    """Fetch field dimensions (capacity, turf, roof, distances) from one game feed per venue."""
    import json

    # Get all venues that are missing capacity
    result = session.execute(text("""
        SELECT v.venue_id, v.venue_name
        FROM milb.venues v
        WHERE v.capacity IS NULL
    """))
    venues_needing_details = result.fetchall()

    if not venues_needing_details:
        logger.info("All venues already have field dimensions")
        return

    logger.info(f"Fetching field dimensions for {len(venues_needing_details)} venues")

    for venue_id, venue_name in venues_needing_details:
        # Find one completed game at this venue
        game_result = session.execute(text("""
            SELECT game_pk FROM milb.games
            WHERE venue_id = :venue_id AND status_detail = 'Final'
            LIMIT 1
        """), {"venue_id": venue_id})
        row = game_result.fetchone()

        if not row:
            logger.warning(f"No completed games found for venue {venue_name} ({venue_id})")
            continue

        game_pk = row[0]
        url = (
            f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
            f"?fields=gameData,venue,fieldInfo,capacity,turfType,roofType,"
            f"leftLine,leftCenter,center,rightCenter,rightLine"
        )

        try:
            resp = httpx.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            field_info = data.get("gameData", {}).get("venue", {}).get("fieldInfo", {})
            if field_info:
                session.execute(text("""
                    UPDATE milb.venues SET
                        capacity = :capacity,
                        turf_type = :turf_type,
                        roof_type = :roof_type,
                        left_line = :left_line,
                        left_center = :left_center,
                        center_field = :center_field,
                        right_center = :right_center,
                        right_line = :right_line,
                        updated_at = NOW()
                    WHERE venue_id = :venue_id
                """), {
                    "venue_id": venue_id,
                    "capacity": field_info.get("capacity"),
                    "turf_type": field_info.get("turfType"),
                    "roof_type": field_info.get("roofType"),
                    "left_line": field_info.get("leftLine"),
                    "left_center": field_info.get("leftCenter"),
                    "center_field": field_info.get("center"),
                    "right_center": field_info.get("rightCenter"),
                    "right_line": field_info.get("rightLine"),
                })
                session.commit()
                logger.info(f"Updated field dims for {venue_name} (capacity: {field_info.get('capacity')})")
        except Exception as e:
            logger.warning(f"Failed to get field info for {venue_name}: {e}")

        time.sleep(0.5)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    session = get_session()
    try:
        collect_teams(session)
    finally:
        session.close()
