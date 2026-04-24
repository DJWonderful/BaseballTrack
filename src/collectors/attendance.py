"""Collect season-level attendance aggregates from MLB Stats API."""

import json
import os
import time

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.connection import get_session
from src.utils.logger import get_logger

logger = get_logger("collectors.attendance")

MLB_API = os.getenv("MLB_API_BASE", "https://statsapi.mlb.com/api/v1")


def collect_season_attendance(session: Session, seasons: list[int] | None = None,
                              team_id: int | None = None,
                              force: bool = False) -> int:
    """Collect season-level attendance aggregates for all teams.

    Returns number of records upserted.
    """
    from src.collectors.delta import active_seasons, parse_seasons_env

    if seasons is None:
        seasons = parse_seasons_env()
    seasons, skipped = active_seasons(seasons, force=force)
    if skipped:
        logger.info(f"Attendance: skipping completed seasons {skipped}")
    if not seasons:
        logger.info("Attendance: no active seasons to collect")
        return 0

    # Get all team IDs
    if team_id:
        team_ids = [team_id]
    else:
        result = session.execute(text("SELECT team_id FROM milb.teams ORDER BY team_id"))
        team_ids = [row[0] for row in result]

    total_upserted = 0

    for season in seasons:
        logger.info(f"Collecting season attendance for {season} ({len(team_ids)} teams)")

        for tid in team_ids:
            url = f"{MLB_API}/attendance"
            params = {"teamId": tid, "season": season}

            try:
                resp = httpx.get(url, params=params, timeout=30)
                if resp.status_code != 200:
                    continue

                data = resp.json()
                records = data.get("records", [])

                for record in records:
                    att = record.get("attendanceOpeningAverage")
                    high_game = record.get("attendanceHighGame", {})
                    low_game = record.get("attendanceLowGame", {})

                    session.execute(text("""
                        INSERT INTO milb.season_attendance (
                            team_id, season, game_type_id,
                            openings_total, openings_total_home, openings_total_away,
                            games_total, games_home_total, games_away_total,
                            attendance_total, attendance_total_home, attendance_total_away,
                            attendance_avg_home, attendance_avg_away, attendance_avg_ytd,
                            attendance_opening_avg,
                            attendance_high, attendance_high_date, attendance_high_game_pk,
                            attendance_low, attendance_low_date, attendance_low_game_pk,
                            raw_json, created_at
                        ) VALUES (
                            :team_id, :season, :game_type_id,
                            :openings_total, :openings_total_home, :openings_total_away,
                            :games_total, :games_home_total, :games_away_total,
                            :att_total, :att_total_home, :att_total_away,
                            :att_avg_home, :att_avg_away, :att_avg_ytd,
                            :att_opening_avg,
                            :att_high, :att_high_date, :att_high_gpk,
                            :att_low, :att_low_date, :att_low_gpk,
                            CAST(:raw_json AS jsonb), NOW()
                        )
                        ON CONFLICT (team_id, season, game_type_id) DO UPDATE SET
                            openings_total = EXCLUDED.openings_total,
                            openings_total_home = EXCLUDED.openings_total_home,
                            openings_total_away = EXCLUDED.openings_total_away,
                            games_total = EXCLUDED.games_total,
                            games_home_total = EXCLUDED.games_home_total,
                            games_away_total = EXCLUDED.games_away_total,
                            attendance_total = EXCLUDED.attendance_total,
                            attendance_total_home = EXCLUDED.attendance_total_home,
                            attendance_total_away = EXCLUDED.attendance_total_away,
                            attendance_avg_home = EXCLUDED.attendance_avg_home,
                            attendance_avg_away = EXCLUDED.attendance_avg_away,
                            attendance_avg_ytd = EXCLUDED.attendance_avg_ytd,
                            attendance_opening_avg = EXCLUDED.attendance_opening_avg,
                            attendance_high = EXCLUDED.attendance_high,
                            attendance_high_date = EXCLUDED.attendance_high_date,
                            attendance_high_game_pk = EXCLUDED.attendance_high_game_pk,
                            attendance_low = EXCLUDED.attendance_low,
                            attendance_low_date = EXCLUDED.attendance_low_date,
                            attendance_low_game_pk = EXCLUDED.attendance_low_game_pk,
                            raw_json = EXCLUDED.raw_json,
                            updated_at = NOW()
                    """), {
                        "team_id": tid,
                        "season": season,
                        "game_type_id": record.get("gameType", {}).get("id", "R"),
                        "openings_total": record.get("openingsTotal"),
                        "openings_total_home": record.get("openingsTotalHome"),
                        "openings_total_away": record.get("openingsTotalAway"),
                        "games_total": record.get("gamesTotal"),
                        "games_home_total": record.get("gamesHomeTotal"),
                        "games_away_total": record.get("gamesAwayTotal"),
                        "att_total": record.get("attendanceTotal"),
                        "att_total_home": record.get("attendanceTotalHome"),
                        "att_total_away": record.get("attendanceTotalAway"),
                        "att_avg_home": record.get("attendanceAverageHome"),
                        "att_avg_away": record.get("attendanceAverageAway"),
                        "att_avg_ytd": record.get("attendanceAverageYtd"),
                        "att_opening_avg": record.get("attendanceOpeningAverage"),
                        "att_high": record.get("attendanceHigh"),
                        "att_high_date": record.get("attendanceHighDate"),
                        "att_high_gpk": high_game.get("gamePk"),
                        "att_low": record.get("attendanceLow"),
                        "att_low_date": record.get("attendanceLowDate"),
                        "att_low_gpk": low_game.get("gamePk"),
                        "raw_json": json.dumps(record),
                    })
                    total_upserted += 1

            except Exception as e:
                logger.warning(f"Failed attendance for team {tid}, season {season}: {e}")

            time.sleep(0.5)

        session.commit()
        logger.info(f"Season {season} attendance complete")

    logger.info(f"Season attendance collection complete: {total_upserted} records")
    return total_upserted


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    session = get_session()
    try:
        collect_season_attendance(session)
    finally:
        session.close()
