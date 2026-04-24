"""Main orchestrator -- collects all MiLB data for all teams across all seasons.

Usage:
    python scripts/collect_all.py            # delta load (skip completed seasons)
    python scripts/collect_all.py --force    # full refresh of all seasons
"""

import argparse
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from rich.console import Console
from sqlalchemy import text

from src.collectors.delta import parse_seasons_env, active_seasons
from src.db.connection import get_session
from src.collectors.teams import collect_teams, collect_venue_details
from src.collectors.schedule import collect_schedule
from src.collectors.promotions import collect_promotions
from src.collectors.game_feed import collect_game_feeds
from src.collectors.weather import collect_weather
from src.collectors.attendance import collect_season_attendance
from src.collectors.transactions import collect_transactions
from src.utils.logger import get_logger

logger = get_logger("collect_all")
console = Console()


def main():
    parser = argparse.ArgumentParser(description="MiLB Data Pipeline")
    parser.add_argument("--force", action="store_true",
                        help="Force full refresh of all seasons (ignore delta logic)")
    args = parser.parse_args()
    force = args.force

    start_time = time.time()
    seasons = parse_seasons_env()
    to_fetch, skipped = active_seasons(seasons, force=force)

    mode = "Full Collection" if force else "Delta Collection"
    console.print(f"\n[bold blue]{'=' * 3} MiLB Data Pipeline -- {mode} {'=' * 3}[/bold blue]\n")
    console.print(f"  Configured seasons: {seasons}")
    if not force and skipped:
        console.print(f"  [dim]Skipping completed: {skipped}[/dim]")
    if to_fetch:
        console.print(f"  Active seasons: {to_fetch}")
    console.print()

    session = get_session()
    try:
        # Step 1: Reference data -- always collect (cheap, catches name changes)
        console.print("[bold yellow]Step 1/8: Collecting teams, venues, leagues...[/bold yellow]")
        total_counts = {"organizations": 0, "leagues": 0, "divisions": 0, "venues": 0, "teams": 0}
        for season in seasons:
            console.print(f"  Season {season}...")
            counts = collect_teams(session, season=season)
            for k in total_counts:
                total_counts[k] += counts.get(k, 0)
        console.print(f"  Teams: {total_counts['teams']}, Venues: {total_counts['venues']}, "
                      f"Orgs: {total_counts['organizations']}, Leagues: {total_counts['leagues']}\n")

        # Step 2: Schedules (delta: skip completed seasons)
        console.print("[bold yellow]Step 2/8: Collecting schedules...[/bold yellow]")
        game_count = collect_schedule(session, force=force)
        console.print(f"  Games upserted: {game_count}\n")

        # Step 3: Promotions (delta: skip completed seasons)
        console.print("[bold yellow]Step 3/8: Collecting promotions...[/bold yellow]")
        promo_count = collect_promotions(session, force=force)
        console.print(f"  Promotions upserted: {promo_count}\n")

        # Step 4: Game feeds (already delta: only enriches Final games with NULL attendance)
        console.print("[bold yellow]Step 4/8: Collecting game feeds (attendance, duration, weather)...[/bold yellow]")
        feed_count = collect_game_feeds(session)
        console.print(f"  Games enriched: {feed_count}\n")

        # Step 5: Venue field dimensions (already delta: only WHERE capacity IS NULL)
        console.print("[bold yellow]Step 5/8: Collecting venue field dimensions...[/bold yellow]")
        collect_venue_details(session)
        console.print()

        # Step 6: Weather (already delta: only games missing weather)
        console.print("[bold yellow]Step 6/8: Collecting detailed weather...[/bold yellow]")
        weather_count = collect_weather(session)
        console.print(f"  Weather records: {weather_count}\n")

        # Step 7: Season attendance (delta: skip completed seasons)
        console.print("[bold yellow]Step 7/8: Collecting season attendance aggregates...[/bold yellow]")
        att_count = collect_season_attendance(session, force=force)
        console.print(f"  Season records: {att_count}\n")

        # Step 8: Transactions (delta: skip completed seasons)
        console.print("[bold yellow]Step 8/8: Collecting transactions (rehab, options, callups)...[/bold yellow]")
        txn_count = collect_transactions(session, force=force)
        console.print(f"  Transaction records: {txn_count}\n")

        # Summary
        elapsed = time.time() - start_time
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)

        console.print("[bold green]═══ Collection Complete ═══[/bold green]\n")
        console.print(f"  Total time: {hours}h {minutes}m\n")

        for query, label in [
            ("SELECT COUNT(*) FROM milb.teams", "Teams"),
            ("SELECT COUNT(*) FROM milb.venues", "Venues"),
            ("SELECT COUNT(*) FROM milb.games", "Total games"),
            ("SELECT COUNT(*) FROM milb.games WHERE attendance IS NOT NULL", "Games with attendance"),
            ("SELECT COUNT(*) FROM milb.game_promotions", "Promotions"),
            ("SELECT COUNT(*) FROM milb.game_weather", "Weather records"),
            ("SELECT COUNT(*) FROM milb.season_attendance", "Season attendance records"),
            ("SELECT COUNT(*) FROM milb.transactions", "Transactions"),
            ("SELECT COUNT(*) FROM milb.transactions WHERE is_rehab = TRUE", "Rehab assignments"),
        ]:
            result = session.execute(text(query))
            console.print(f"  {label}: {result.scalar()}")

        console.print()

    finally:
        session.close()


if __name__ == "__main__":
    main()
