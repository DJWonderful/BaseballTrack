"""Test the full pipeline with Binghamton Rumble Ponies (teamId=505) only."""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from rich.console import Console
from src.db.connection import get_session
from src.collectors.teams import collect_teams
from src.collectors.schedule import collect_schedule
from src.collectors.promotions import collect_promotions
from src.collectors.game_feed import collect_game_feeds
from src.collectors.weather import collect_weather
from src.collectors.attendance import collect_season_attendance

console = Console()
TEAM_ID = 505  # Binghamton Rumble Ponies
SEASONS = [2023, 2024, 2025]


def main():
    console.print("\n[bold blue]MiLB Pipeline Test — Binghamton Rumble Ponies[/bold blue]\n")

    session = get_session()
    try:
        # 1. Reference data (all teams needed for away team FKs)
        console.print("[yellow]1. Collecting reference data (all teams)...[/yellow]")
        counts = collect_teams(session)
        console.print(f"   Teams: {counts['teams']}, Venues: {counts['venues']}")

        # 2. Schedules — Rumble Ponies only
        console.print("\n[yellow]2. Collecting schedules...[/yellow]")
        game_count = collect_schedule(session, seasons=SEASONS, sport_ids=[12], team_id=TEAM_ID)
        console.print(f"   Games: {game_count}")

        # 3. Promotions — Rumble Ponies only
        console.print("\n[yellow]3. Collecting promotions...[/yellow]")
        promo_count = collect_promotions(session, seasons=SEASONS, sport_ids=[12], team_id=TEAM_ID)
        console.print(f"   Promotions: {promo_count}")

        # 4. Game feeds — Rumble Ponies home games
        console.print("\n[yellow]4. Collecting game feeds (attendance, weather, duration)...[/yellow]")
        feed_count = collect_game_feeds(session, team_id=TEAM_ID)
        console.print(f"   Games enriched: {feed_count}")

        # 5. Weather — Rumble Ponies
        console.print("\n[yellow]5. Collecting detailed weather...[/yellow]")
        weather_count = collect_weather(session, team_id=TEAM_ID)
        console.print(f"   Weather records: {weather_count}")

        # 6. Season attendance — Rumble Ponies
        console.print("\n[yellow]6. Collecting season attendance...[/yellow]")
        att_count = collect_season_attendance(session, seasons=SEASONS, team_id=TEAM_ID)
        console.print(f"   Season records: {att_count}")

        # 7. Summary
        console.print("\n[bold green]Test complete! Summary:[/bold green]")
        from sqlalchemy import text
        for query, label in [
            ("SELECT COUNT(*) FROM milb.games WHERE home_team_id = 505 OR away_team_id = 505", "Total games"),
            ("SELECT COUNT(*) FROM milb.games WHERE home_team_id = 505 AND attendance IS NOT NULL", "Games with attendance"),
            ("SELECT AVG(attendance)::int FROM milb.games WHERE home_team_id = 505 AND attendance IS NOT NULL", "Avg home attendance"),
            ("SELECT COUNT(*) FROM milb.game_promotions gp JOIN milb.games g ON gp.game_pk = g.game_pk WHERE g.home_team_id = 505", "Promotions"),
            ("SELECT COUNT(*) FROM milb.game_weather gw JOIN milb.games g ON gw.game_pk = g.game_pk WHERE g.home_team_id = 505", "Weather records"),
            ("SELECT COUNT(*) FROM milb.season_attendance WHERE team_id = 505", "Season attendance records"),
        ]:
            result = session.execute(text(query))
            console.print(f"   {label}: {result.scalar()}")

    finally:
        session.close()

    console.print()


if __name__ == "__main__":
    main()
