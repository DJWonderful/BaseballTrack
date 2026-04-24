"""Build the milb.game_features table -- one flat row per home game with all features.

This is the foundation for all downstream analytics: marginal lift analysis,
peer clustering, XGBoost modeling, and recommendation generation.

Usage:
    python scripts/build_features.py            # normal run (skips if data unchanged)
    python scripts/build_features.py --force     # rebuild even if data unchanged
"""

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from rich.console import Console
from src.db.connection import engine, get_session

console = Console()

# ── School calendar (copied from 6_Scheduling.py) ────────────────────────────
SCHOOL_CALENDAR: dict[str, dict] = {
    "AL": {"release": 5, "return": 8}, "AR": {"release": 5, "return": 8},
    "AZ": {"release": 5, "return": 8}, "FL": {"release": 5, "return": 8},
    "GA": {"release": 5, "return": 8}, "LA": {"release": 5, "return": 8},
    "MS": {"release": 5, "return": 8}, "NC": {"release": 5, "return": 8},
    "NM": {"release": 5, "return": 8}, "OK": {"release": 5, "return": 8},
    "SC": {"release": 5, "return": 8}, "TN": {"release": 5, "return": 8},
    "TX": {"release": 5, "return": 8}, "KS": {"release": 5, "return": 8},
    "KY": {"release": 5, "return": 8}, "MO": {"release": 5, "return": 8},
    "NE": {"release": 5, "return": 8}, "SD": {"release": 5, "return": 8},
    "WV": {"release": 5, "return": 8}, "ND": {"release": 5, "return": 9},
    "HI": {"release": 5, "return": 8}, "AK": {"release": 5, "return": 8},
    "CA": {"release": 6, "return": 9}, "CO": {"release": 6, "return": 9},
    "CT": {"release": 6, "return": 9}, "DE": {"release": 6, "return": 9},
    "IA": {"release": 6, "return": 9}, "ID": {"release": 6, "return": 9},
    "IL": {"release": 6, "return": 9}, "IN": {"release": 6, "return": 9},
    "MA": {"release": 6, "return": 9}, "MD": {"release": 6, "return": 9},
    "ME": {"release": 6, "return": 9}, "MI": {"release": 6, "return": 9},
    "MN": {"release": 6, "return": 9}, "MT": {"release": 6, "return": 9},
    "NH": {"release": 6, "return": 9}, "NJ": {"release": 6, "return": 9},
    "NV": {"release": 6, "return": 9}, "NY": {"release": 6, "return": 9},
    "OH": {"release": 6, "return": 9}, "OR": {"release": 6, "return": 9},
    "PA": {"release": 6, "return": 9}, "RI": {"release": 6, "return": 9},
    "UT": {"release": 6, "return": 9}, "VA": {"release": 6, "return": 9},
    "VT": {"release": 6, "return": 9}, "WA": {"release": 6, "return": 9},
    "WI": {"release": 6, "return": 9}, "WY": {"release": 6, "return": 9},
}


def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def should_run(force: bool) -> bool:
    if force:
        return True
    with engine.connect() as conn:
        last = conn.execute(text("""
            SELECT input_max_updated FROM milb.analysis_runs
            WHERE analysis_name = 'build_features' AND status = 'completed'
            ORDER BY completed_at DESC LIMIT 1
        """)).fetchone()
        if last is None:
            return True
        current = conn.execute(text("""
            SELECT GREATEST(
                (SELECT MAX(updated_at) FROM milb.games),
                (SELECT MAX(updated_at) FROM milb.game_promotions),
                (SELECT MAX(updated_at) FROM milb.game_weather),
                (SELECT MAX(updated_at) FROM milb.transactions),
                (SELECT MAX(updated_at) FROM milb.venues),
                (SELECT MAX(updated_at) FROM milb.teams),
                (SELECT MAX(updated_at) FROM milb.venue_demographics)
            ) AS max_updated
        """)).fetchone()
        return current[0] is None or last[0] is None or current[0] > last[0]


def log_run_start(session) -> int:
    with engine.connect() as conn:
        current = conn.execute(text("""
            SELECT GREATEST(
                (SELECT MAX(updated_at) FROM milb.games),
                (SELECT MAX(updated_at) FROM milb.game_promotions),
                (SELECT MAX(updated_at) FROM milb.game_weather),
                (SELECT MAX(updated_at) FROM milb.transactions)
            ) AS max_updated
        """)).fetchone()

    result = session.execute(text("""
        INSERT INTO milb.analysis_runs (analysis_name, input_max_updated, status)
        VALUES ('build_features', :max_up, 'running')
        RETURNING run_id
    """), {"max_up": current[0]})
    session.commit()
    return result.fetchone()[0]


def load_base_games() -> pd.DataFrame:
    """All final home games with attendance across all levels."""
    return pd.read_sql(text("""
        SELECT g.game_pk, g.home_team_id AS team_id, g.away_team_id AS opponent_team_id,
               g.game_date::date AS game_date, g.season, g.sport_id, g.game_type,
               g.day_night, g.game_datetime,
               g.attendance, g.home_score, g.away_score
        FROM milb.games g
        WHERE g.abstract_game_state = 'Final'
          AND g.attendance IS NOT NULL
          AND g.attendance > 0
          AND g.sport_id IN (11,12,13,14)
          AND g.game_type = 'R'
        ORDER BY g.home_team_id, g.season, g.game_date
    """), engine)


def load_all_results() -> pd.DataFrame:
    """All game results from both perspectives (for win/loss streaks)."""
    return pd.read_sql(text("""
        SELECT home_team_id AS team_id, game_pk, game_date::date AS game_date,
               season, TRUE AS is_home,
               CASE WHEN home_score > away_score THEN 'W' ELSE 'L' END AS result
        FROM milb.games
        WHERE abstract_game_state = 'Final' AND game_type = 'R'
          AND sport_id IN (11,12,13,14) AND home_score IS NOT NULL
        UNION ALL
        SELECT away_team_id AS team_id, game_pk, game_date::date AS game_date,
               season, FALSE AS is_home,
               CASE WHEN away_score > home_score THEN 'W' ELSE 'L' END AS result
        FROM milb.games
        WHERE abstract_game_state = 'Final' AND game_type = 'R'
          AND sport_id IN (11,12,13,14) AND away_score IS NOT NULL
        ORDER BY team_id, season, game_date
    """), engine)


def load_promotions() -> pd.DataFrame:
    """Per-game aggregated promotion flags (BOOL_OR)."""
    return pd.read_sql(text("""
        SELECT game_pk,
               COUNT(*)                                    AS promo_count,
               BOOL_OR(COALESCE(is_fireworks, FALSE))      AS has_fireworks,
               BOOL_OR(COALESCE(is_giveaway_item, FALSE))  AS has_giveaway,
               BOOL_OR(COALESCE(is_food_deal, FALSE))      AS has_food_deal,
               BOOL_OR(COALESCE(is_ticket_deal, FALSE))    AS has_ticket_deal,
               BOOL_OR(COALESCE(is_theme_night, FALSE))    AS has_theme_night,
               BOOL_OR(COALESCE(is_kids_event, FALSE))     AS has_kids_event,
               BOOL_OR(COALESCE(is_heritage_night, FALSE)) AS has_heritage,
               BOOL_OR(COALESCE(is_community_event, FALSE)) AS has_community,
               BOOL_OR(COALESCE(is_entertainment, FALSE))  AS has_entertain,
               BOOL_OR(COALESCE(is_dog_friendly, FALSE))   AS has_dog,
               BOOL_OR(COALESCE(has_celebrity, FALSE))     AS has_celebrity,
               BOOL_OR(COALESCE(is_recurring, FALSE))      AS has_recurring,
               BOOL_OR(giveaway_limit IS NOT NULL)         AS has_limited_giveaway
        FROM milb.game_promotions
        WHERE enrichment_method IS NOT NULL
        GROUP BY game_pk
    """), engine)


def load_weather() -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT game_pk, temperature_max_f, precipitation_sum_in, windspeed_max_mph, weathercode
        FROM milb.game_weather
    """), engine)


def load_rehab_windows() -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT to_team_id AS team_id,
               transaction_date::date AS window_start,
               COALESCE(resolution_date, transaction_date + INTERVAL '30 days')::date AS window_end
        FROM milb.transactions
        WHERE is_rehab = TRUE AND to_team_id IS NOT NULL
    """), engine)


def load_venues() -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT v.venue_id, v.latitude, v.longitude, v.capacity, v.state_abbrev,
               v.timezone,
               t.team_id, t.division_id
        FROM milb.venues v
        JOIN milb.teams t ON t.venue_id = v.venue_id
        WHERE t.sport_id IN (11,12,13,14)
    """), engine)


def load_demographics() -> pd.DataFrame:
    """Load all census years for all team venues (for time-varying merge)."""
    return pd.read_sql(text("""
        SELECT t.team_id, vd.census_year,
               vd.msa_population, vd.place_population,
               vd.msa_median_income AS median_income,
               vd.msa_poverty_rate AS poverty_rate
        FROM milb.teams t
        JOIN milb.venue_demographics vd ON t.venue_id = vd.venue_id
        WHERE t.sport_id IN (11,12,13,14)
        ORDER BY t.team_id, vd.census_year
    """), engine)


def map_season_to_census_year(season: int) -> int:
    """Map a game season to the best-matching ACS 5-Year census year.

    ACS data for year X is released Dec of year X+1 and covers years X-4 to X.
    So for season N, the most recent *completed* ACS is year N-1.
    """
    return season - 1


def add_demographic_trends(df: pd.DataFrame, demographics: pd.DataFrame) -> pd.DataFrame:
    """Compute 5-year population/income/poverty trends from multi-year census data."""
    # Build lookup for 5 years prior
    demo_5yr_ago = demographics.copy()
    demo_5yr_ago["census_year"] = demo_5yr_ago["census_year"] + 5
    demo_5yr_ago = demo_5yr_ago.rename(columns={
        "msa_population": "msa_pop_5yr_ago",
        "place_population": "place_pop_5yr_ago",
        "median_income": "income_5yr_ago",
        "poverty_rate": "poverty_5yr_ago",
    })

    df = df.merge(
        demo_5yr_ago[["team_id", "census_year", "msa_pop_5yr_ago",
                       "income_5yr_ago", "poverty_5yr_ago"]],
        on=["team_id", "census_year"],
        how="left",
    )

    # Percentage changes
    df["population_change_5yr_pct"] = np.where(
        df["msa_pop_5yr_ago"].notna() & (df["msa_pop_5yr_ago"] > 0),
        ((df["msa_population"] - df["msa_pop_5yr_ago"]) / df["msa_pop_5yr_ago"]).round(3),
        np.nan,
    )

    df["income_change_5yr_pct"] = np.where(
        df["income_5yr_ago"].notna() & (df["income_5yr_ago"] > 0),
        ((df["median_income"] - df["income_5yr_ago"]) / df["income_5yr_ago"]).round(3),
        np.nan,
    )

    df["poverty_rate_change_5yr"] = np.where(
        df["poverty_5yr_ago"].notna(),
        (df["poverty_rate"] - df["poverty_5yr_ago"]).round(2),
        np.nan,
    )

    # Categorical trend direction
    df["population_trend"] = np.where(
        df["population_change_5yr_pct"].isna(), None,
        np.where(
            df["population_change_5yr_pct"] > 0.03, "growing",
            np.where(df["population_change_5yr_pct"] < -0.03, "shrinking", "stable")
        )
    )

    # Drop intermediate columns
    df.drop(columns=["msa_pop_5yr_ago", "income_5yr_ago", "poverty_5yr_ago"],
            inplace=True)

    return df


# ── Feature computation functions ─────────────────────────────────────────────

def add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["day_of_week"] = df["game_date"].dt.dayofweek  # 0=Mon..6=Sun
    df["month"] = df["game_date"].dt.month
    df["is_weekend"] = df["day_of_week"].isin([5, 6])
    return df


# Start-time buckets. Locked in docs/GAME_TIMES_ANALYSIS_PLAN.md; stay in sync
# with any analysis / page code that references these names.
TIME_BUCKET_BOUNDS = [
    ("morning",        0,  11),
    ("noon",          11,  13),
    ("matinee",       13,  16),
    ("early_evening", 16,  18),
    ("evening",       18,  20),
    ("late",          20,  24),
]


def _bucket_from_hour(hour):
    if pd.isna(hour):
        return None
    h = int(hour)
    for name, lo, hi in TIME_BUCKET_BOUNDS:
        if lo <= h < hi:
            return name
    return None


def add_time_bucket_features(df: pd.DataFrame, venues: pd.DataFrame) -> pd.DataFrame:
    """Derive local_start_hour and start_time_bucket from game_datetime + venue tz.

    game_datetime is timezone-aware (UTC) in Postgres. Convert per-venue.
    Rows with no timezone or no game_datetime get NULL -- downstream code should
    treat NULL bucket as "unknown" and exclude from bucket-based analysis.
    """
    tz_map = venues.set_index("team_id")["timezone"].to_dict()
    df["_venue_tz"] = df["team_id"].map(tz_map)

    # game_datetime comes back as pandas datetime with UTC tz. Convert per-row.
    dt = pd.to_datetime(df["game_datetime"], utc=True, errors="coerce")

    def local_hour(ts, tz):
        if pd.isna(ts) or tz is None or (isinstance(tz, float) and pd.isna(tz)):
            return None
        try:
            return int(ts.tz_convert(tz).hour)
        except Exception:
            return None

    df["local_start_hour"] = [local_hour(ts, tz) for ts, tz in zip(dt, df["_venue_tz"])]
    df["start_time_bucket"] = df["local_start_hour"].apply(_bucket_from_hour)

    df.drop(columns=["_venue_tz", "game_datetime"], inplace=True, errors="ignore")
    return df


def add_scheduling_features(df: pd.DataFrame) -> pd.DataFrame:
    """Homestand detection, days since last home game, season progress."""
    df = df.sort_values(["team_id", "season", "game_date"]).copy()

    # Days since last home game
    df["prev_home_date"] = df.groupby(["team_id", "season"])["game_date"].shift(1)
    df["days_since_last_home"] = (df["game_date"] - df["prev_home_date"]).dt.days
    df.drop(columns=["prev_home_date"], inplace=True)

    # Homestand detection: a new homestand starts when days_since_last_home > 1
    # (i.e., team was away for at least a day)
    df["new_homestand"] = (df["days_since_last_home"].fillna(99) > 1).astype(int)
    df["homestand_id"] = df.groupby(["team_id", "season"])["new_homestand"].cumsum()
    df["homestand_game_number"] = df.groupby(["team_id", "season", "homestand_id"]).cumcount() + 1
    df["homestand_length"] = df.groupby(["team_id", "season", "homestand_id"])["game_pk"].transform("count")
    df.drop(columns=["new_homestand", "homestand_id"], inplace=True)

    # Game number in season and season progress
    df["game_number_in_season"] = df.groupby(["team_id", "season"]).cumcount() + 1
    df["season_total"] = df.groupby(["team_id", "season"])["game_pk"].transform("count")
    df["season_progress"] = (df["game_number_in_season"] / df["season_total"]).round(3)
    df.drop(columns=["season_total"], inplace=True)

    return df


def add_performance_features(df: pd.DataFrame, all_results: pd.DataFrame) -> pd.DataFrame:
    """Win pct entering, streak, prior game attendance, prior margin."""
    # Compute streaks
    res = all_results.sort_values(["team_id", "season", "game_date"]).copy()

    def _streak_series(group):
        streak = 0
        streaks = []
        for r in group["result"]:
            streaks.append(streak)
            if r == "W":
                streak = max(1, streak + 1)
            else:
                streak = min(-1, streak - 1)
        return pd.Series(streaks, index=group.index)

    res["streak"] = res.groupby(["team_id", "season"], group_keys=False).apply(_streak_series)

    # Win pct entering
    res["is_win"] = (res["result"] == "W").astype(int)
    g = res.groupby(["team_id", "season"])
    res["pre_wins"] = g["is_win"].cumsum().shift(1)
    res["pre_games"] = g["is_win"].transform("cumcount")
    res["win_pct_entering"] = (res["pre_wins"] / res["pre_games"]).round(3)

    # Keep only home games and merge
    home_res = res[res["is_home"]].copy()
    home_res = home_res[["game_pk", "streak", "win_pct_entering"]]
    df = df.merge(home_res, on="game_pk", how="left")

    # Prior game attendance and margin (lag-1 within team-season)
    df = df.sort_values(["team_id", "season", "game_date"])
    df["prior_game_attendance"] = df.groupby(["team_id", "season"])["attendance"].shift(1)
    df["margin"] = df["home_score"] - df["away_score"]
    df["prior_game_margin"] = df.groupby(["team_id", "season"])["margin"].shift(1)
    df.drop(columns=["margin"], inplace=True)

    return df


def add_promo_features(df: pd.DataFrame, promos: pd.DataFrame) -> pd.DataFrame:
    """Merge aggregated promo flags; compute cooldown features."""
    promo_cols = [
        "promo_count", "has_fireworks", "has_giveaway", "has_food_deal",
        "has_ticket_deal", "has_theme_night", "has_kids_event", "has_heritage",
        "has_community", "has_entertain", "has_dog", "has_celebrity",
        "has_recurring", "has_limited_giveaway",
    ]
    df = df.merge(promos, on="game_pk", how="left")

    # Fill NaN (games with no promos)
    for col in promo_cols:
        if col == "promo_count":
            df[col] = df[col].fillna(0).astype(int)
        else:
            df[col] = df[col].fillna(False)

    df["has_any_promo"] = df["promo_count"] > 0

    # Promo cooldown: days since last fireworks / giveaway for this team
    df = df.sort_values(["team_id", "season", "game_date"])
    for flag, col_name in [("has_fireworks", "days_since_last_fw"), ("has_giveaway", "days_since_last_give")]:
        # For each team-season, find the last date this promo ran
        last_dates = []
        for _, grp in df.groupby(["team_id", "season"]):
            last_promo_date = pd.NaT
            vals = []
            for idx, row in grp.iterrows():
                if pd.isna(last_promo_date):
                    vals.append(np.nan)
                else:
                    vals.append((row["game_date"] - last_promo_date).days)
                if row[flag]:
                    last_promo_date = row["game_date"]
            last_dates.extend(zip(grp.index, vals))
        cooldown = pd.DataFrame(last_dates, columns=["idx", col_name]).set_index("idx")
        df[col_name] = cooldown[col_name]

    return df


def add_weather_features(df: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    weather = weather.rename(columns={
        "temperature_max_f": "temp_max_f",
        "precipitation_sum_in": "precip_inches",
        "windspeed_max_mph": "wind_max_mph",
        "weathercode": "wmo_code",
    })
    df = df.merge(weather, on="game_pk", how="left")

    # Weather bucket from WMO code
    def wmo_bucket(code):
        if pd.isna(code):
            return None
        code = int(code)
        if code <= 3:
            return "clear"
        if code <= 49:
            return "cloudy"
        if code <= 69:
            return "rain"
        if code <= 79:
            return "snow"
        if code <= 99:
            return "rain"  # showers/thunderstorms
        return None

    df["weather_bucket"] = df["wmo_code"].apply(wmo_bucket)
    df.drop(columns=["wmo_code"], inplace=True)
    return df


def add_opponent_features(df: pd.DataFrame, venues: pd.DataFrame) -> pd.DataFrame:
    """Distance between venues and division membership."""
    venue_coords = venues[["team_id", "latitude", "longitude", "division_id"]].copy()

    # Home team coordinates
    df = df.merge(
        venue_coords.rename(columns={"latitude": "home_lat", "longitude": "home_lon", "division_id": "home_div"}),
        on="team_id", how="left",
    )
    # Opponent coordinates
    df = df.merge(
        venue_coords.rename(columns={
            "team_id": "opponent_team_id", "latitude": "away_lat",
            "longitude": "away_lon", "division_id": "away_div",
        }),
        on="opponent_team_id", how="left",
    )

    # Haversine distance
    def safe_haversine(row):
        if pd.isna(row["home_lat"]) or pd.isna(row["away_lat"]):
            return np.nan
        return round(haversine_miles(row["home_lat"], row["home_lon"], row["away_lat"], row["away_lon"]), 1)

    df["distance_miles"] = df.apply(safe_haversine, axis=1)
    df["is_same_division"] = df["home_div"] == df["away_div"]

    # Opponent historical draw: avg attendance when this opponent visits ANY team
    opp_draw = (
        df.groupby("opponent_team_id")["attendance"]
        .mean().round(1)
        .reset_index()
        .rename(columns={"attendance": "opponent_hist_draw"})
    )
    df = df.merge(opp_draw, on="opponent_team_id", how="left")

    df.drop(columns=["home_lat", "home_lon", "home_div", "away_lat", "away_lon", "away_div"], inplace=True)
    return df


def add_rehab_feature(df: pd.DataFrame, rehab: pd.DataFrame) -> pd.DataFrame:
    if rehab.empty:
        df["has_rehab_player"] = False
        return df

    rehab["window_start"] = pd.to_datetime(rehab["window_start"])
    rehab["window_end"] = pd.to_datetime(rehab["window_end"])

    # Cross-join is expensive; use a merge + filter approach
    merged = df[["game_pk", "team_id", "game_date"]].merge(rehab, on="team_id", how="left")
    in_window = (
        merged["window_start"].notna()
        & (merged["game_date"] >= merged["window_start"])
        & (merged["game_date"] <= merged["window_end"])
    )
    rehab_pks = set(merged.loc[in_window, "game_pk"])
    df["has_rehab_player"] = df["game_pk"].isin(rehab_pks)
    return df


def add_school_calendar(df: pd.DataFrame, venues: pd.DataFrame) -> pd.DataFrame:
    state_map = venues[["team_id", "state_abbrev"]].drop_duplicates()
    df = df.merge(state_map, on="team_id", how="left")

    def is_school_in_session(row):
        cal = SCHOOL_CALENDAR.get(row.get("state_abbrev"))
        if cal is None:
            return None
        m = row["month"]
        # School is out during summer: release_month <= m < return_month
        return not (cal["release"] <= m < cal["return"])

    df["school_in_session"] = df.apply(is_school_in_session, axis=1)
    df.drop(columns=["state_abbrev"], inplace=True)
    return df


def add_targets(df: pd.DataFrame, venues: pd.DataFrame) -> pd.DataFrame:
    """Capacity utilization and attendance lift (vs team-season mean)."""
    cap_map = venues[["team_id", "capacity"]].rename(columns={"capacity": "venue_capacity"})
    df = df.merge(cap_map, on="team_id", how="left")

    df["capacity_utilization"] = np.where(
        df["venue_capacity"].notna() & (df["venue_capacity"] > 0),
        (df["attendance"] / df["venue_capacity"]).round(3),
        np.nan,
    )

    team_season_mean = df.groupby(["team_id", "season"])["attendance"].transform("mean")
    df["attendance_lift"] = (df["attendance"] - team_season_mean).round(1)

    return df


def build() -> pd.DataFrame:
    console.print("[bold yellow]Loading data from database...[/bold yellow]")

    games = load_base_games()
    console.print(f"  Base games: {len(games):,}")

    all_results = load_all_results()
    console.print(f"  All results (for streaks): {len(all_results):,}")

    promos = load_promotions()
    console.print(f"  Enriched promo aggregations: {len(promos):,}")

    weather = load_weather()
    console.print(f"  Weather records: {len(weather):,}")

    rehab = load_rehab_windows()
    console.print(f"  Rehab windows: {len(rehab):,}")

    venues = load_venues()
    console.print(f"  Venues: {len(venues):,}")

    demographics = load_demographics()
    console.print(f"  Demographics: {len(demographics):,}")

    # Build features step by step
    console.print("\n[bold yellow]Computing features...[/bold yellow]")
    df = games.copy()

    console.print("  Calendar features...")
    df = add_calendar_features(df)

    console.print("  Time-bucket features (local start hour)...")
    df = add_time_bucket_features(df, venues)

    console.print("  Scheduling features (homestand, season progress)...")
    df = add_scheduling_features(df)

    console.print("  Performance features (win%, streaks, lag)...")
    df = add_performance_features(df, all_results)

    console.print("  Promotion features (flags + cooldowns)...")
    df = add_promo_features(df, promos)

    console.print("  Weather features...")
    df = add_weather_features(df, weather)

    console.print("  Opponent features (distance, division, draw)...")
    df = add_opponent_features(df, venues)

    console.print("  Rehab assignment feature...")
    df = add_rehab_feature(df, rehab)

    console.print("  School calendar feature...")
    df = add_school_calendar(df, venues)

    console.print("  Demographics (time-varying)...")
    df["census_year"] = df["season"].apply(map_season_to_census_year)
    df = df.merge(demographics, on=["team_id", "census_year"], how="left")

    console.print("  Demographic trends (5-year changes)...")
    df = add_demographic_trends(df, demographics)

    console.print("  Targets (capacity util, lift)...")
    df = add_targets(df, venues)

    # Drop intermediate columns
    df.drop(columns=["home_score", "away_score"], inplace=True, errors="ignore")

    return df


def main():
    parser = argparse.ArgumentParser(description="Build milb.game_features table")
    parser.add_argument("--force", action="store_true", help="Rebuild even if data unchanged")
    args = parser.parse_args()

    console.print("\n[bold blue]--- Build Game Features Table ---[/bold blue]\n")

    if not should_run(args.force):
        console.print("[green]Data unchanged since last run. Use --force to rebuild.[/green]")
        return

    session = get_session()
    run_id = log_run_start(session)

    try:
        start = time.time()
        df = build()

        # Select and order columns for the DB table
        output_cols = [
            "game_pk", "team_id", "season", "game_date", "sport_id", "game_type",
            "day_of_week", "month", "is_weekend", "day_night",
            "local_start_hour", "start_time_bucket",
            "homestand_game_number", "homestand_length", "days_since_last_home",
            "game_number_in_season", "season_progress",
            "win_pct_entering", "streak", "prior_game_attendance", "prior_game_margin",
            "has_any_promo", "promo_count",
            "has_fireworks", "has_giveaway", "has_food_deal", "has_ticket_deal",
            "has_theme_night", "has_kids_event", "has_heritage", "has_community",
            "has_entertain", "has_dog", "has_celebrity", "has_recurring",
            "has_limited_giveaway", "days_since_last_fw", "days_since_last_give",
            "temp_max_f", "precip_inches", "wind_max_mph", "weather_bucket",
            "opponent_team_id", "opponent_hist_draw", "distance_miles", "is_same_division",
            "has_rehab_player", "school_in_session",
            "census_year",
            "msa_population", "place_population", "median_income", "poverty_rate",
            "population_change_5yr_pct", "income_change_5yr_pct",
            "poverty_rate_change_5yr", "population_trend",
            "venue_capacity",
            "attendance", "capacity_utilization", "attendance_lift",
        ]

        # Keep only columns that exist (some may be missing if data is sparse)
        existing_cols = [c for c in output_cols if c in df.columns]
        out = df[existing_cols].copy()

        # Replace inf/-inf with NaN before writing (can arise from division by zero)
        out = out.replace([np.inf, -np.inf], np.nan)

        console.print(f"\n[bold yellow]Writing {len(out):,} rows to milb.game_features...[/bold yellow]")

        # Truncate and insert (full rebuild)
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE milb.game_features"))
            out["run_id"] = run_id
            out.to_sql("game_features", conn, schema="milb", if_exists="append", index=False)

        elapsed = time.time() - start
        console.print(f"\n[bold green]Done! {len(out):,} rows in {elapsed:.1f}s[/bold green]")

        # Log success
        session.execute(text("""
            UPDATE milb.analysis_runs
            SET status = 'completed', completed_at = NOW(), record_count = :n
            WHERE run_id = :rid
        """), {"n": len(out), "rid": run_id})
        session.commit()

        # Summary stats
        console.print(f"\n  Teams:   {out['team_id'].nunique()}")
        console.print(f"  Seasons: {sorted(out['season'].unique())}")
        console.print(f"  Games with promos: {out['has_any_promo'].sum():,} ({out['has_any_promo'].mean():.1%})")
        console.print(f"  Games with rehab:  {out['has_rehab_player'].sum():,}")
        console.print(f"  Avg attendance:    {out['attendance'].mean():,.0f}")

    except Exception as e:
        session.execute(text("""
            UPDATE milb.analysis_runs
            SET status = 'failed', completed_at = NOW(), error_message = :err
            WHERE run_id = :rid
        """), {"err": str(e), "rid": run_id})
        session.commit()
        console.print(f"[bold red]Error: {e}[/bold red]")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
