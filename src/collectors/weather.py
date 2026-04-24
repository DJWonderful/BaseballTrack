"""Collect historical weather data from Open-Meteo for each game."""

import json
import os
import time
from datetime import date, timedelta

import httpx
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from sqlalchemy import text
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.db.connection import get_session
from src.utils.logger import get_logger

logger = get_logger("collectors.weather")

OPENMETEO_API = os.getenv("OPENMETEO_API_BASE", "https://archive-api.open-meteo.com/v1/archive")
RATE_LIMIT = float(os.getenv("WEATHER_RATE_LIMIT", "10"))

DAILY_PARAMS = [
    "temperature_2m_max", "temperature_2m_min",
    "apparent_temperature_max", "apparent_temperature_min",
    "precipitation_sum", "rain_sum", "snowfall_sum", "precipitation_hours",
    "windspeed_10m_max", "windgusts_10m_max", "winddirection_10m_dominant",
    "weathercode",
    "sunrise", "sunset", "sunshine_duration",
]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectTimeout)),
)
def fetch_weather(client: httpx.Client, lat: float, lon: float,
                  start_date: str, end_date: str) -> dict | None:
    """Fetch weather from Open-Meteo for a date range at a location."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": ",".join(DAILY_PARAMS),
        "temperature_unit": "fahrenheit",
        "windspeed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "America/New_York",
    }
    resp = client.get(OPENMETEO_API, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def collect_weather(session: Session, team_id: int | None = None,
                    season: int | None = None) -> int:
    """Collect weather for games that don't have weather data yet.

    Groups by (venue, month) to batch API calls — one call per venue per month
    instead of one per game.

    Returns number of weather records upserted.
    """
    # Find games needing weather, grouped by venue and month
    query = """
        SELECT DISTINCT g.venue_id, v.latitude, v.longitude,
               DATE_TRUNC('month', g.game_date)::date AS month_start
        FROM milb.games g
        JOIN milb.venues v ON g.venue_id = v.venue_id
        LEFT JOIN milb.game_weather w ON g.game_pk = w.game_pk
        WHERE w.game_pk IS NULL
          AND g.abstract_game_state = 'Final'
          AND v.latitude IS NOT NULL
          AND v.longitude IS NOT NULL
    """
    params = {}
    if team_id:
        query += " AND g.home_team_id = :team_id"
        params["team_id"] = team_id
    if season:
        query += " AND g.season = :season"
        params["season"] = season
    query += " ORDER BY month_start, g.venue_id"

    result = session.execute(text(query), params)
    venue_months = result.fetchall()

    if not venue_months:
        logger.info("All games already have weather data")
        return 0

    logger.info(f"Fetching weather for {len(venue_months)} venue-month batches")
    total_upserted = 0
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
            task = progress.add_task("Weather", total=len(venue_months))

            for venue_id, lat, lon, month_start in venue_months:
                # Calculate month end
                if month_start.month == 12:
                    month_end = date(month_start.year + 1, 1, 1) - timedelta(days=1)
                else:
                    month_end = date(month_start.year, month_start.month + 1, 1) - timedelta(days=1)

                try:
                    weather_data = fetch_weather(
                        client, float(lat), float(lon),
                        month_start.isoformat(), month_end.isoformat()
                    )

                    if not weather_data or "daily" not in weather_data:
                        progress.advance(task)
                        continue

                    daily = weather_data["daily"]
                    dates = daily.get("time", [])

                    # Build a date → index lookup
                    date_index = {d: i for i, d in enumerate(dates)}

                    # Find all games at this venue in this month that need weather
                    games_result = session.execute(text("""
                        SELECT g.game_pk, g.game_date::text
                        FROM milb.games g
                        LEFT JOIN milb.game_weather w ON g.game_pk = w.game_pk
                        WHERE g.venue_id = :venue_id
                          AND g.game_date >= :start
                          AND g.game_date <= :end
                          AND g.abstract_game_state = 'Final'
                          AND w.game_pk IS NULL
                    """), {
                        "venue_id": venue_id,
                        "start": month_start.isoformat(),
                        "end": month_end.isoformat(),
                    })

                    for game_pk, game_date_str in games_result:
                        idx = date_index.get(game_date_str)
                        if idx is None:
                            continue

                        def val(key: str, i: int = idx):
                            arr = daily.get(key, [])
                            return arr[i] if i < len(arr) else None

                        session.execute(text("""
                            INSERT INTO milb.game_weather (
                                game_pk, venue_id, weather_date,
                                temperature_max_f, temperature_min_f,
                                apparent_temperature_max_f, apparent_temperature_min_f,
                                precipitation_sum_in, rain_sum_in, snowfall_sum_in,
                                precipitation_hours,
                                windspeed_max_mph, windgusts_max_mph,
                                winddirection_dominant_deg, weathercode,
                                sunrise, sunset, sunshine_duration_sec,
                                raw_json, created_at
                            ) VALUES (
                                :game_pk, :venue_id, :weather_date,
                                :temp_max, :temp_min,
                                :apparent_max, :apparent_min,
                                :precip_sum, :rain_sum, :snow_sum,
                                :precip_hours,
                                :wind_max, :gust_max,
                                :wind_dir, :weathercode,
                                :sunrise, :sunset, :sunshine,
                                CAST(:raw_json AS jsonb), NOW()
                            )
                            ON CONFLICT (game_pk) DO UPDATE SET
                                temperature_max_f = EXCLUDED.temperature_max_f,
                                temperature_min_f = EXCLUDED.temperature_min_f,
                                apparent_temperature_max_f = EXCLUDED.apparent_temperature_max_f,
                                apparent_temperature_min_f = EXCLUDED.apparent_temperature_min_f,
                                precipitation_sum_in = EXCLUDED.precipitation_sum_in,
                                rain_sum_in = EXCLUDED.rain_sum_in,
                                snowfall_sum_in = EXCLUDED.snowfall_sum_in,
                                precipitation_hours = EXCLUDED.precipitation_hours,
                                windspeed_max_mph = EXCLUDED.windspeed_max_mph,
                                windgusts_max_mph = EXCLUDED.windgusts_max_mph,
                                winddirection_dominant_deg = EXCLUDED.winddirection_dominant_deg,
                                weathercode = EXCLUDED.weathercode,
                                sunrise = EXCLUDED.sunrise,
                                sunset = EXCLUDED.sunset,
                                sunshine_duration_sec = EXCLUDED.sunshine_duration_sec,
                                raw_json = EXCLUDED.raw_json,
                                updated_at = NOW()
                        """), {
                            "game_pk": game_pk,
                            "venue_id": venue_id,
                            "weather_date": game_date_str,
                            "temp_max": val("temperature_2m_max"),
                            "temp_min": val("temperature_2m_min"),
                            "apparent_max": val("apparent_temperature_max"),
                            "apparent_min": val("apparent_temperature_min"),
                            "precip_sum": val("precipitation_sum"),
                            "rain_sum": val("rain_sum"),
                            "snow_sum": val("snowfall_sum"),
                            "precip_hours": val("precipitation_hours"),
                            "wind_max": val("windspeed_10m_max"),
                            "gust_max": val("windgusts_10m_max"),
                            "wind_dir": val("winddirection_10m_dominant"),
                            "weathercode": val("weathercode"),
                            "sunrise": val("sunrise"),
                            "sunset": val("sunset"),
                            "sunshine": val("sunshine_duration"),
                            "raw_json": json.dumps({
                                k: (daily[k][idx] if idx < len(daily.get(k, [])) else None)
                                for k in daily if k != "time"
                            }),
                        })
                        total_upserted += 1

                    session.commit()

                except Exception as e:
                    logger.warning(f"Weather fetch failed for venue {venue_id}, {month_start}: {e}")
                    session.rollback()

                progress.advance(task)
                time.sleep(delay)

    logger.info(f"Weather collection complete: {total_upserted} records")
    return total_upserted


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    session = get_session()
    try:
        collect_weather(session)
    finally:
        session.close()
