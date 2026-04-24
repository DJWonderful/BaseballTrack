"""Database helpers for the Streamlit dashboard.

Two backends, selected by the APP_BACKEND env var:
  - "postgres" (default)  : SQLAlchemy + local Postgres (local dev)
  - "duckdb"              : DuckDB reading Parquet files in data/app/ (deployed)

Both expose the same API (query_df, execute, load_rehab_windows,
load_game_attendance, is_read_only) so page code is backend-agnostic.

Caching:
  @st.cache_resource  -> connection/engine created ONCE per app session
  @st.cache_data      -> query results cached for `ttl` seconds
"""

import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

BACKEND = os.getenv("APP_BACKEND", "postgres").lower()
DATA_DIR = Path(__file__).parent.parent.parent / "data" / "app"


def is_read_only() -> bool:
    """True when the backend cannot persist writes (deployed DuckDB mode)."""
    return BACKEND == "duckdb"


# ════════════════════════════════════════════════════════════════════
# Postgres backend
# ════════════════════════════════════════════════════════════════════

@st.cache_resource
def _pg_engine():
    from sqlalchemy import create_engine
    url = (
        f"postgresql://{os.getenv('DB_USERNAME', 'postgres')}:"
        f"{os.getenv('DB_PASSWORD', 'postgres')}@"
        f"{os.getenv('DB_HOST', '127.0.0.1')}:"
        f"{os.getenv('DB_PORT', '5432')}/"
        f"{os.getenv('DB_NAME', 'baseball')}"
    )
    return create_engine(url, pool_pre_ping=True)


def _pg_query(sql: str, params: dict | None) -> pd.DataFrame:
    from sqlalchemy import text
    with _pg_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def _pg_execute(sql: str, params: dict | None) -> None:
    from sqlalchemy import text
    with _pg_engine().begin() as conn:
        conn.execute(text(sql), params or {})


# ════════════════════════════════════════════════════════════════════
# DuckDB backend (reads Parquet in data/app/)
# ════════════════════════════════════════════════════════════════════

@st.cache_resource
def _duck_conn():
    """DuckDB in-memory connection with milb.* views over the Parquet files."""
    import duckdb

    if not DATA_DIR.exists():
        raise FileNotFoundError(
            f"APP_BACKEND=duckdb but {DATA_DIR} is missing. "
            "Run `python scripts/export_for_app.py` to generate it."
        )

    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA IF NOT EXISTS milb")

    for pq in sorted(DATA_DIR.glob("*.parquet")):
        name = pq.stem
        # read_parquet with a literal path is the DuckDB idiomatic registration
        con.execute(
            f"CREATE OR REPLACE VIEW milb.\"{name}\" AS "
            f"SELECT * FROM read_parquet('{pq.as_posix()}')"
        )
    return con


def _duck_query(sql: str, params: dict | None) -> pd.DataFrame:
    """Run SQL against DuckDB. Translates :name placeholders to $name for DuckDB."""
    con = _duck_conn()
    if params:
        # DuckDB uses $name for named params; SQLAlchemy-style :name also works
        # in recent versions, but be explicit to avoid version drift.
        translated = sql
        for k in params.keys():
            translated = translated.replace(f":{k}", f"${k}")
        return con.execute(translated, params).fetch_df()
    return con.execute(sql).fetch_df()


def _duck_execute(sql: str, params: dict | None) -> None:
    """Writes are a no-op in the deployed read-only build."""
    # Intentionally silent: pages should check is_read_only() before offering
    # write actions. Any write that slips through is dropped rather than
    # erroring the UI.
    return None


# ════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=600)
def query_df(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Run a SQL query and return a DataFrame. Results cached for 10 min."""
    if BACKEND == "duckdb":
        return _duck_query(sql, params)
    return _pg_query(sql, params)


def execute(sql: str, params: dict | None = None) -> None:
    """Run a write statement. No-op under the DuckDB (deployed) backend."""
    if BACKEND == "duckdb":
        _duck_execute(sql, params)
        return
    _pg_execute(sql, params)


@st.cache_data(ttl=600)
def load_rehab_windows() -> pd.DataFrame:
    """Rehab assignment windows, with 30-day fallback for missing end dates."""
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

    When exclude_rehab=True, games falling inside an active rehab window for
    the home team are dropped before averaging.
    """
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
            merged = games.merge(rehab, on="team_id", how="left")
            merged["game_date"]    = pd.to_datetime(merged["game_date"])
            merged["window_start"] = pd.to_datetime(merged["window_start"])
            merged["window_end"]   = pd.to_datetime(merged["window_end"])
            in_window = (
                merged["window_start"].notna()
                & (merged["game_date"] >= merged["window_start"])
                & (merged["game_date"] <= merged["window_end"])
            )
            rehab_pks = set(merged.loc[in_window, "game_pk"])
            games = games[~games["game_pk"].isin(rehab_pks)].copy()

    return (
        games.groupby(["team_id", "season"])["attendance"]
        .mean()
        .round(0)
        .reset_index()
        .rename(columns={"attendance": "attendance_avg_home"})
    )
