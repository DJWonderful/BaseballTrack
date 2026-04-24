"""Populate milb.venues.timezone from (latitude, longitude) via timezonefinder.

One-time enrichment. Safe to re-run -- only fills NULL rows unless --force.

Usage:
    python scripts/enrich_venue_timezones.py
    python scripts/enrich_venue_timezones.py --force
"""

import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from rich.console import Console
from timezonefinder import TimezoneFinder

from src.db.connection import engine

console = Console()


def main():
    parser = argparse.ArgumentParser(description="Fill venues.timezone from lat/lon")
    parser.add_argument("--force", action="store_true",
                        help="Re-derive tz for all venues (default: only NULL)")
    args = parser.parse_args()

    console.print("\n[bold blue]--- Venue Timezone Enrichment ---[/bold blue]\n")

    where = "" if args.force else "WHERE timezone IS NULL"
    venues = pd.read_sql(text(f"""
        SELECT venue_id, venue_name, city, state_abbrev, latitude, longitude
          FROM milb.venues {where}
    """), engine)

    if venues.empty:
        console.print("[green]All venues already have timezones. Use --force to re-run.[/green]")
        return

    console.print(f"Deriving timezones for {len(venues):,} venues...")

    tf = TimezoneFinder()

    def lookup(row) -> str | None:
        lat, lon = row["latitude"], row["longitude"]
        if pd.isna(lat) or pd.isna(lon):
            return None
        return tf.timezone_at(lat=float(lat), lng=float(lon))

    venues["timezone"] = venues.apply(lookup, axis=1)
    missing = venues[venues["timezone"].isna()]
    if not missing.empty:
        console.print(f"[yellow]Could not resolve {len(missing)} venues:[/yellow]")
        console.print(missing[["venue_id", "venue_name", "city", "state_abbrev",
                               "latitude", "longitude"]].to_string(index=False))

    tz_counts = venues["timezone"].value_counts()
    console.print(f"\nTimezone distribution ({len(tz_counts)} unique):")
    for tz, n in tz_counts.items():
        console.print(f"  {tz:<25} {n:>4}")

    with engine.begin() as conn:
        for _, row in venues.iterrows():
            if row["timezone"] is None:
                continue
            conn.execute(text("""
                UPDATE milb.venues SET timezone = :tz WHERE venue_id = :vid
            """), {"tz": row["timezone"], "vid": int(row["venue_id"])})

    resolved = len(venues) - len(missing)
    console.print(f"\n[bold green]Done. Wrote {resolved:,} timezones.[/bold green]")


if __name__ == "__main__":
    main()
