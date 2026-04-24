"""Shared page footer with data-freshness indicator.

Every page ends with `render_footer()` so the user always knows how fresh the
data is and which pipeline run the page reflects.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from utils.db import query_df


@st.cache_data(ttl=600)
def _latest_runs() -> pd.DataFrame:
    """One row per analysis script, most recent run."""
    return query_df("""
        SELECT DISTINCT ON (analysis_name)
               analysis_name, started_at, status
          FROM milb.analysis_runs
         ORDER BY analysis_name, started_at DESC
    """)


@st.cache_data(ttl=600)
def _collect_freshness() -> pd.Timestamp | None:
    """Most recent game whose updated_at is populated -- proxy for last collect."""
    df = query_df("""
        SELECT MAX(updated_at) AS last_update
          FROM milb.games
         WHERE updated_at IS NOT NULL
    """)
    if df.empty or pd.isna(df.iloc[0]["last_update"]):
        return None
    return pd.to_datetime(df.iloc[0]["last_update"])


def _fmt_ago(ts: pd.Timestamp | None) -> str:
    if ts is None:
        return "unknown"
    # strip tz so arithmetic works regardless of source
    now = pd.Timestamp.utcnow().tz_localize(None)
    try:
        ref = ts.tz_localize(None) if ts.tzinfo else ts
    except (AttributeError, TypeError):
        ref = ts
    delta = now - ref
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return ts.strftime("%Y-%m-%d %H:%M")
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 14:
        return f"{days}d ago"
    return ts.strftime("%Y-%m-%d")


def render_footer(scripts: list[str] | None = None) -> None:
    """Render a one-line 'data as of' footer.

    scripts: optional list of analysis_runs script_names this page depends on.
             If given, the footer shows the oldest of those, so staleness
             reflects the weakest link.
    """
    st.divider()

    collect_ts = _collect_freshness()
    runs = _latest_runs()

    pieces = []
    if collect_ts is not None:
        pieces.append(f"Games last collected: **{_fmt_ago(collect_ts)}**")

    if scripts and not runs.empty:
        mask = runs["analysis_name"].isin(scripts)
        subset = runs[mask]
        if not subset.empty:
            oldest = subset["started_at"].min()
            pieces.append(f"Analytics last run: **{_fmt_ago(oldest)}**")
    elif not runs.empty:
        oldest = runs["started_at"].min()
        pieces.append(f"Oldest analytics run: **{_fmt_ago(oldest)}**")

    if pieces:
        st.caption("  •  ".join(pieces))
    else:
        st.caption("Data freshness: unknown")
