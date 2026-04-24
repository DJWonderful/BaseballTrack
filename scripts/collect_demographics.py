"""Collect Census demographics for each MiLB venue location.

Uses the Census Bureau ACS 5-Year API to fetch population, income, and poverty
data at both the city (place) and metro area (MSA/CBSA) level.

Supports multi-year collection (2015-2024) to enable time-varying demographics
and trend analysis in the ML pipeline.

Delta loading: caches FIPS geocode results from DB and skips census years
that are already fully populated. Use --force to re-fetch everything.

Requires:
    pip install census us censusgeocode
    CENSUS_API_KEY in .env (free: https://api.census.gov/data/key_signup.html)

Usage:
    python scripts/collect_demographics.py                          # delta load
    python scripts/collect_demographics.py --years 2022 2023 2024   # specific years
    python scripts/collect_demographics.py --force                  # full refresh
"""

import argparse
import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

import censusgeocode as cg
import pandas as pd
from census import Census
from rich.console import Console
from rich.progress import Progress
from sqlalchemy import text
from us import states

from src.db.connection import get_session
from src.utils.logger import get_logger

logger = get_logger("collect_demographics")
console = Console()

CENSUS_API_KEY = os.getenv("CENSUS_API_KEY", "")
DEFAULT_YEARS = list(range(2015, 2025))  # ACS 5-Year available annually 2009-2024

# ACS 5-Year variable codes
ACS_FIELDS = (
    "NAME",
    "B01003_001E",  # Total population
    "B19013_001E",  # Median household income
    "B19301_001E",  # Per capita income
    "B17001_001E",  # Poverty universe (denominator)
    "B17001_002E",  # Below poverty level (numerator)
)


def load_venues(session) -> pd.DataFrame:
    """Load all MiLB venues with coordinates."""
    result = session.execute(text("""
        SELECT v.venue_id, v.city, v.state_abbrev, v.latitude::float, v.longitude::float
        FROM milb.venues v
        JOIN milb.teams t ON t.venue_id = v.venue_id
        WHERE v.latitude IS NOT NULL
          AND v.longitude IS NOT NULL
          AND t.sport_id IN (11, 12, 13, 14)
    """))
    return pd.DataFrame(result.fetchall(), columns=result.keys())


def geocode_venue(lat: float, lon: float) -> dict:
    """Reverse-geocode a lat/lon to Census FIPS codes."""
    try:
        result = cg.coordinates(x=lon, y=lat)
        geo = result.get("2020 Census Blocks", [{}])[0]
        state_fips = geo.get("STATE", "")
        county_fips = geo.get("COUNTY", "")

        # Get place FIPS from incorporated places
        places = result.get("Incorporated Places", [])
        place_fips = places[0].get("PLACE", "") if places else ""

        return {
            "state_fips": state_fips,
            "county_fips": county_fips,
            "place_fips": place_fips,
        }
    except Exception as e:
        logger.warning(f"Geocode failed for ({lat}, {lon}): {e}")
        return {"state_fips": "", "county_fips": "", "place_fips": ""}


def fetch_county_to_cbsa() -> dict[str, str]:
    """Download the Census county-to-CBSA delineation and return a lookup dict.

    Keys are 5-digit state+county FIPS (e.g. '36007' for Broome County, NY).
    Values are CBSA codes (e.g. '13780' for Binghamton MSA).
    """
    import io
    import httpx

    url = "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/delineation-files/list1_2023.xlsx"
    logger.info(f"Downloading CBSA delineation file from {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()

    df = pd.read_excel(io.BytesIO(resp.content), skiprows=2, dtype=str)
    lookup = {}
    for _, row in df.iterrows():
        state_fips = str(row.get("FIPS State Code", "")).strip().zfill(2)
        county_fips = str(row.get("FIPS County Code", "")).strip().zfill(3)
        cbsa_code = str(row.get("CBSA Code", "")).strip()
        if state_fips and county_fips and cbsa_code and cbsa_code != "nan":
            lookup[state_fips + county_fips] = cbsa_code
    logger.info(f"  Loaded {len(lookup)} county-to-CBSA mappings")
    return lookup


def fetch_place_data(c: Census, year: int) -> pd.DataFrame:
    """Fetch ACS place-level demographics for all US states."""
    all_rows = []
    for st in states.STATES_AND_TERRITORIES:
        if not st.fips:
            continue
        try:
            data = c.acs5.state_place(ACS_FIELDS, st.fips, Census.ALL, year=year)
            all_rows.extend(data)
        except Exception as e:
            logger.warning(f"Failed to fetch places for {st.name} ({year}): {e}")
    df = pd.DataFrame(all_rows)
    if df.empty:
        return df
    df["lookup_key"] = df["state"].astype(str).str.zfill(2) + df["place"].astype(str).str.zfill(5)
    return df


def fetch_msa_data(c: Census, year: int) -> pd.DataFrame:
    """Fetch ACS MSA-level demographics in one call."""
    try:
        data = c.acs5.get(
            ACS_FIELDS,
            {"for": "metropolitan statistical area/micropolitan statistical area:*"},
            year=year,
        )
        return pd.DataFrame(data)
    except Exception as e:
        logger.error(f"Failed to fetch MSA data ({year}): {e}")
        return pd.DataFrame()


def safe_int(val) -> int | None:
    """Convert Census value to int, treating negatives as null (suppressed data)."""
    try:
        v = int(float(val))
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None


def compute_poverty_rate(universe, below) -> float | None:
    u = safe_int(universe)
    b = safe_int(below)
    if u and u > 0 and b is not None:
        return round(b / u * 100, 2)
    return None


def upsert_year(session, geo_df, place_lookup, msa_lookup, census_year):
    """Upsert demographics for a single census year."""
    upsert_count = 0
    for _, geo in geo_df.iterrows():
        venue_id = geo["venue_id"]
        state_fips = geo["state_fips"]
        county_fips = geo["county_fips"]
        place_fips = geo["place_fips"]
        cbsa_code = geo["cbsa_code"]

        # Place-level
        place_key = str(state_fips).zfill(2) + str(place_fips).zfill(5)
        place_row = place_lookup.get(place_key)

        place_name = None
        place_pop = None
        place_med_income = None
        place_per_capita = None
        place_poverty = None

        if place_row is not None:
            place_name = str(place_row.get("NAME", ""))
            place_pop = safe_int(place_row.get("B01003_001E"))
            place_med_income = safe_int(place_row.get("B19013_001E"))
            place_per_capita = safe_int(place_row.get("B19301_001E"))
            place_poverty = compute_poverty_rate(
                place_row.get("B17001_001E"), place_row.get("B17001_002E")
            )

        # MSA-level
        msa_row = msa_lookup.get(str(cbsa_code)) if cbsa_code else None
        msa_name = None
        msa_pop = None
        msa_med_income = None
        msa_per_capita = None
        msa_poverty = None

        if msa_row is not None:
            msa_name = str(msa_row.get("NAME", ""))
            msa_pop = safe_int(msa_row.get("B01003_001E"))
            msa_med_income = safe_int(msa_row.get("B19013_001E"))
            msa_per_capita = safe_int(msa_row.get("B19301_001E"))
            msa_poverty = compute_poverty_rate(
                msa_row.get("B17001_001E"), msa_row.get("B17001_002E")
            )

        session.execute(text("""
            INSERT INTO milb.venue_demographics (
                venue_id, census_year,
                state_fips, county_fips, place_fips, cbsa_code,
                place_name, place_population, place_median_income,
                place_per_capita_income, place_poverty_rate,
                msa_name, msa_population, msa_median_income,
                msa_per_capita_income, msa_poverty_rate
            ) VALUES (
                :venue_id, :census_year,
                :state_fips, :county_fips, :place_fips, :cbsa_code,
                :place_name, :place_pop, :place_med_income,
                :place_per_capita, :place_poverty,
                :msa_name, :msa_pop, :msa_med_income,
                :msa_per_capita, :msa_poverty
            )
            ON CONFLICT (venue_id, census_year) DO UPDATE SET
                state_fips            = EXCLUDED.state_fips,
                county_fips           = EXCLUDED.county_fips,
                place_fips            = EXCLUDED.place_fips,
                cbsa_code             = EXCLUDED.cbsa_code,
                place_name            = EXCLUDED.place_name,
                place_population      = EXCLUDED.place_population,
                place_median_income   = EXCLUDED.place_median_income,
                place_per_capita_income = EXCLUDED.place_per_capita_income,
                place_poverty_rate    = EXCLUDED.place_poverty_rate,
                msa_name              = EXCLUDED.msa_name,
                msa_population        = EXCLUDED.msa_population,
                msa_median_income     = EXCLUDED.msa_median_income,
                msa_per_capita_income = EXCLUDED.msa_per_capita_income,
                msa_poverty_rate      = EXCLUDED.msa_poverty_rate,
                updated_at            = NOW()
        """), {
            "venue_id": int(venue_id),
            "census_year": census_year,
            "state_fips": state_fips or None,
            "county_fips": county_fips or None,
            "place_fips": place_fips or None,
            "cbsa_code": cbsa_code or None,
            "place_name": place_name,
            "place_pop": place_pop,
            "place_med_income": place_med_income,
            "place_per_capita": place_per_capita,
            "place_poverty": place_poverty,
            "msa_name": msa_name,
            "msa_pop": msa_pop,
            "msa_med_income": msa_med_income,
            "msa_per_capita": msa_per_capita,
            "msa_poverty": msa_poverty,
        })
        upsert_count += 1

    session.commit()
    return upsert_count


def load_fips_cache(session) -> dict[int, dict]:
    """Load cached FIPS geocode results from existing venue_demographics rows."""
    result = session.execute(text("""
        SELECT DISTINCT ON (venue_id)
               venue_id, state_fips, county_fips, place_fips, cbsa_code
        FROM milb.venue_demographics
        WHERE state_fips IS NOT NULL
        ORDER BY venue_id, census_year DESC
    """))
    cache = {}
    for row in result:
        cache[row[0]] = {
            "state_fips": row[1] or "",
            "county_fips": row[2] or "",
            "place_fips": row[3] or "",
            "cbsa_code": row[4] or "",
        }
    return cache


def load_year_counts(session) -> dict[int, int]:
    """Check how many venues are populated per census year."""
    result = session.execute(text("""
        SELECT census_year, COUNT(*) FROM milb.venue_demographics
        GROUP BY census_year
    """))
    return {row[0]: row[1] for row in result}


def run(years: list[int] | None = None, force: bool = False):
    if years is None:
        years = DEFAULT_YEARS

    mode = "Full Refresh" if force else "Delta Load"
    console.print(f"\n[bold blue]--- Census Demographics Collector ({mode}) ---[/bold blue]\n")
    console.print(f"Census years requested: {years}")

    if not CENSUS_API_KEY:
        console.print("[red]CENSUS_API_KEY not set in .env. Get one free at https://api.census.gov/data/key_signup.html[/red]")
        return

    session = get_session()
    c = Census(CENSUS_API_KEY)

    # 1. Load venues
    venues = load_venues(session)
    venue_count = len(venues)
    console.print(f"Loaded {venue_count} venues from DB")

    # 2. Geocode venues -- use cached FIPS from DB when available
    console.print("\n[bold]Step 1: Geocoding venues to Census FIPS codes...[/bold]")
    fips_cache = {} if force else load_fips_cache(session)
    if fips_cache:
        console.print(f"  [dim]Loaded {len(fips_cache)} cached FIPS from DB[/dim]")

    geo_results = []
    geocode_count = 0
    with Progress() as progress:
        task = progress.add_task("Geocoding", total=len(venues))
        for _, row in venues.iterrows():
            vid = row["venue_id"]
            if vid in fips_cache:
                geo = dict(fips_cache[vid])
                geo["venue_id"] = vid
            else:
                geo = geocode_venue(row["latitude"], row["longitude"])
                geo["venue_id"] = vid
                geocode_count += 1
                time.sleep(0.5)  # Census geocoder rate limit
            geo_results.append(geo)
            progress.advance(task)

    geo_df = pd.DataFrame(geo_results)
    matched_places = (geo_df["place_fips"] != "").sum()
    console.print(f"  {matched_places} matched to a Census place ({geocode_count} geocoded, {len(fips_cache)} cached)")

    # 2b. Map counties to CBSAs using Census delineation file
    # Only need to download if we have venues without cached cbsa_code
    needs_cbsa = geo_df["venue_id"].isin(
        [vid for vid, fips in fips_cache.items() if fips.get("cbsa_code")]
    ).sum() < len(geo_df) if not force else True

    if needs_cbsa or force:
        console.print("\n[bold]Step 1b: Mapping counties to MSAs/CBSAs...[/bold]")
        county_to_cbsa = fetch_county_to_cbsa()
        geo_df["county_key"] = geo_df["state_fips"].str.zfill(2) + geo_df["county_fips"].str.zfill(3)
        geo_df["cbsa_code"] = geo_df["county_key"].map(county_to_cbsa).fillna("")
    else:
        console.print("\n[bold]Step 1b: Using cached CBSA mappings[/bold]")
        # cbsa_code already in geo_df from cache

    matched_cbsa = (geo_df["cbsa_code"] != "").sum()
    console.print(f"  Matched {matched_cbsa} venues to an MSA/CBSA")

    # 3. Filter years -- skip years that already have all venues populated
    year_counts = load_year_counts(session)
    years_to_fetch = []
    for y in years:
        existing = year_counts.get(y, 0)
        if force or existing < venue_count:
            years_to_fetch.append(y)
        else:
            console.print(f"  [dim]Skipping ACS {y} -- already has {existing}/{venue_count} venues[/dim]")

    if not years_to_fetch:
        console.print("\n[bold green]All requested years already fully populated. Nothing to do.[/bold green]")
        console.print("  Use --force to re-fetch everything.")
        session.close()
        return

    console.print(f"\nYears to fetch: {years_to_fetch}")

    # 4. Loop over census years
    total_upserts = 0
    for census_year in years_to_fetch:
        console.print(f"\n[bold]--- ACS {census_year} ---[/bold]")

        console.print(f"  Fetching ACS 5-Year place data ({census_year})...")
        place_df = fetch_place_data(c, census_year)
        console.print(f"    {len(place_df)} places")

        console.print(f"  Fetching ACS 5-Year MSA data ({census_year})...")
        msa_df = fetch_msa_data(c, census_year)
        console.print(f"    {len(msa_df)} MSAs/micropolitan areas")

        # Build lookups for this year
        place_lookup = {}
        if not place_df.empty:
            for _, row in place_df.iterrows():
                place_lookup[row["lookup_key"]] = row

        msa_lookup = {}
        msa_col = "metropolitan statistical area/micropolitan statistical area"
        if not msa_df.empty and msa_col in msa_df.columns:
            for _, row in msa_df.iterrows():
                msa_lookup[str(row[msa_col])] = row

        # Upsert
        n = upsert_year(session, geo_df, place_lookup, msa_lookup, census_year)
        total_upserts += n
        console.print(f"  [green]Upserted {n} rows for {census_year}[/green]")

    # Summary
    result = session.execute(text("""
        SELECT
            COUNT(*) AS total,
            COUNT(DISTINCT venue_id) AS venues,
            COUNT(DISTINCT census_year) AS years,
            COUNT(place_population) AS with_place,
            COUNT(msa_population) AS with_msa
        FROM milb.venue_demographics
    """))
    row = result.fetchone()
    console.print(f"\n  Total rows: {row[0]}, venues: {row[1]}, years: {row[2]}")
    console.print(f"  With place data: {row[3]}, with MSA data: {row[4]}")

    session.close()
    console.print(f"\n[bold green]Done! Upserted {total_upserts} total rows.[/bold green]")


def main():
    parser = argparse.ArgumentParser(description="Collect Census demographics for MiLB venues")
    parser.add_argument("--years", nargs="+", type=int, default=DEFAULT_YEARS,
                        help=f"ACS 5-Year years to collect (default: {DEFAULT_YEARS[0]}-{DEFAULT_YEARS[-1]})")
    parser.add_argument("--force", action="store_true",
                        help="Re-geocode all venues and re-fetch all Census years")
    args = parser.parse_args()
    run(years=args.years, force=args.force)


if __name__ == "__main__":
    main()
