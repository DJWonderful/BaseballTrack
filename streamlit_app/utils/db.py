"""Database helpers for the Streamlit dashboard.

Two key ideas:
  - @st.cache_resource  →  the DB engine is created ONCE and reused across all reruns
  - @st.cache_data      →  query results are cached for `ttl` seconds so the DB
                           isn't hit every time a user moves a slider

Usage from any page:
    from utils.db import query_df

    df = query_df("SELECT * FROM milb.teams WHERE sport_id = :sid", {"sid": 11})
"""

import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Load .env from the project root (two levels up from this file)
load_dotenv(Path(__file__).parent.parent.parent / ".env")


@st.cache_resource
def _engine():
    """Create (and cache) the SQLAlchemy engine. Called once per app session."""
    url = (
        f"postgresql://{os.getenv('DB_USERNAME', 'postgres')}:"
        f"{os.getenv('DB_PASSWORD', 'postgres')}@"
        f"{os.getenv('DB_HOST', '127.0.0.1')}:"
        f"{os.getenv('DB_PORT', '5432')}/"
        f"{os.getenv('DB_NAME', 'baseball')}"
    )
    return create_engine(url, pool_pre_ping=True)


@st.cache_data(ttl=600)
def query_df(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Run a SQL query and return a pandas DataFrame.

    Results are cached for 5 minutes (ttl=600). Streamlit will re-run the
    query automatically when the cache expires or when you call
    st.cache_data.clear().

    Args:
        sql:    SQL string, use :name placeholders for parameters
        params: dict of parameter values, e.g. {"team_id": 505}

    Returns:
        pandas DataFrame with query results
    """
    with _engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def execute(sql: str, params: dict | None = None) -> None:
    """Run a write statement (INSERT / UPDATE / DELETE) in a transaction.

    Use for UI-triggered writes such as the recommendation feedback loop.
    Not cached -- the caller is responsible for clearing cached reads if
    subsequent reads should reflect the write.
    """
    with _engine().begin() as conn:
        conn.execute(text(sql), params or {})


@st.cache_data(ttl=600)
def load_rehab_windows() -> pd.DataFrame:
    """All rehab assignment windows, with a 30-day fallback for missing end dates.

    Returns columns: team_id, window_start, window_end (both datetime)
    """
    return query_df("""
        SELECT
            to_team_id                                                              AS team_id,
            transaction_date::date                                                  AS window_start,
            COALESCE(resolution_date, transaction_date + INTERVAL '30 days')::date  AS window_end
        FROM milb.transactions
        WHERE is_rehab = TRUE
          AND to_team_id IS NOT NULL
          AND transaction_date IS NOT NULL
    """)


@st.cache_data(ttl=600)
def load_game_attendance(exclude_rehab: bool = False) -> pd.DataFrame:
    """Per-game attendance rolled up to season averages per team.

    When exclude_rehab=True, any game whose date falls inside an active rehab
    assignment window for the home team is dropped before computing the average.
    The window runs from transaction_date to resolution_date, or +30 days when
    resolution_date is NULL.

    Returns columns: team_id, season, attendance_avg_home
    """
    # Pull every completed home game that has an attendance figure
    games = query_df("""
        SELECT game_pk, home_team_id AS team_id, game_date, season, attendance
        FROM milb.games
        WHERE abstract_game_state = 'Final'
          AND attendance IS NOT NULL
          AND attendance > 0
    """)

    if exclude_rehab and not games.empty:
        rehab = load_rehab_windows()

        if not rehab.empty:
            # Merge every game against every rehab window that shares the same team_id.
            # This creates multiple rows for a game when several players were rehabbing
            # at the same time — that's fine; we only need the game_pk set.
            merged = games.merge(rehab, on="team_id", how="left")

            # Ensure date types are comparable
            merged["game_date"]    = pd.to_datetime(merged["game_date"])
            merged["window_start"] = pd.to_datetime(merged["window_start"])
            merged["window_end"]   = pd.to_datetime(merged["window_end"])

            # A game is "in a rehab window" if its date falls between start and end
            in_window = (
                merged["window_start"].notna()
                & (merged["game_date"] >= merged["window_start"])
                & (merged["game_date"] <= merged["window_end"])
            )

            rehab_pks = set(merged.loc[in_window, "game_pk"])
            games = games[~games["game_pk"].isin(rehab_pks)].copy()

    # Roll up to season averages — same shape as the season_attendance table
    return (
        games.groupby(["team_id", "season"])["attendance"]
        .mean()
        .round(0)
        .reset_index()
        .rename(columns={"attendance": "attendance_avg_home"})
    )
