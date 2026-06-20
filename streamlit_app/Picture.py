"""The Picture -- landing page for the briefing book.

Above the fold:
  - headline + sub-headline (the answer in one breath)
  - 3 KPI tiles (Binghamton-anchored)
  - 3 finding-card teasers (clickable to /findings/*)

Below the fold:
  - league map (proof of homework + peer-discovery tool)
  - top 5 Double-A by capacity utilization (peer inspiration shortlist)

Replaces the legacy map-first Home as the default st.navigation landing.
Home.py still works as a standalone entry for the deep-dive flat-nav view.
"""

# -- Path setup ---------------------------------------------------------------
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from utils.db import query_df
from utils.footer import render_footer
from utils.theme import LEVEL_COLORS, MOMENTUM_COLORS

st.set_page_config(page_title="The Picture", layout="wide")

RUMBLE_PONIES_ID = 505
DOUBLE_A_SPORT_ID = 12
LEVEL_NAMES = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}


# -- Data loaders -------------------------------------------------------------

@st.cache_data(ttl=600)
def load_rp_kpis() -> dict:
    """The three headline numbers, computed at query time so they stay current."""
    out: dict = {}

    # 1. RP 2026 cap util (current season-to-date)
    cur = query_df(f"""
        SELECT AVG(capacity_utilization) AS cap, COUNT(*) AS n
          FROM milb.game_features
         WHERE team_id = {RUMBLE_PONIES_ID}
           AND season = 2026
           AND attendance IS NOT NULL
    """)
    out["cap_util_2026"] = float(cur["cap"].iloc[0]) if not cur.empty else None
    out["n_games_2026"] = int(cur["n"].iloc[0]) if not cur.empty else 0

    # 2023 baseline for the delta
    prev = query_df(f"""
        SELECT AVG(capacity_utilization) AS cap
          FROM milb.game_features
         WHERE team_id = {RUMBLE_PONIES_ID}
           AND season = 2023
           AND attendance IS NOT NULL
    """)
    out["cap_util_2023"] = float(prev["cap"].iloc[0]) if not prev.empty else None

    # 2. Sat vs Fri gap (2025 last complete year)
    gap = query_df(f"""
        SELECT day_of_week, AVG(attendance) AS avg_att, COUNT(*) AS n
          FROM milb.game_features
         WHERE team_id = {RUMBLE_PONIES_ID}
           AND season = 2025
           AND day_of_week IN (4, 5)
           AND game_type = 'R'
           AND attendance IS NOT NULL
         GROUP BY day_of_week
    """)
    if not gap.empty:
        fri = gap.loc[gap["day_of_week"] == 4, "avg_att"]
        sat = gap.loc[gap["day_of_week"] == 5, "avg_att"]
        out["sat_gap"] = float(sat.iloc[0] - fri.iloc[0]) if len(sat) and len(fri) else None
    else:
        out["sat_gap"] = None

    # 3. RP rank in Double-A by cap util (2025 last complete year)
    rank = query_df(f"""
        WITH t AS (
          SELECT team_id, AVG(capacity_utilization) AS cap
            FROM milb.game_features
           WHERE sport_id = {DOUBLE_A_SPORT_ID}
             AND season = 2025
             AND attendance IS NOT NULL
           GROUP BY team_id
        ),
        ranked AS (
          SELECT team_id, cap,
                 RANK() OVER (ORDER BY cap DESC) AS rk,
                 COUNT(*) OVER () AS total
            FROM t
        )
        SELECT rk, total FROM ranked WHERE team_id = {RUMBLE_PONIES_ID}
    """)
    out["rank"] = int(rank["rk"].iloc[0]) if not rank.empty else None
    out["rank_total"] = int(rank["total"].iloc[0]) if not rank.empty else None

    return out


@st.cache_data(ttl=600)
def load_homework_stats() -> dict:
    """The 'we did our homework' stats line — seasons, teams, games."""
    return query_df("""
        SELECT
            (SELECT COUNT(DISTINCT season) FROM milb.games WHERE game_type='R') AS n_seasons,
            (SELECT COUNT(DISTINCT team_id) FROM milb.teams WHERE sport_id IN (11,12,13,14)) AS n_teams,
            (SELECT COUNT(*) FROM milb.games WHERE attendance IS NOT NULL) AS n_games
    """).iloc[0].to_dict()


@st.cache_data(ttl=600)
def load_map_data() -> pd.DataFrame:
    """One row per team with location, capacity, current-season metrics, momentum."""
    return query_df("""
        WITH last_season AS (
          SELECT team_id,
                 AVG(capacity_utilization) AS cap_util,
                 AVG(attendance) AS avg_att
            FROM milb.game_features
           WHERE attendance IS NOT NULL
             AND season = (SELECT MAX(season) FROM milb.game_features WHERE attendance IS NOT NULL)
           GROUP BY team_id
        ),
        recent_momentum AS (
          SELECT team_id, momentum_label
            FROM milb.team_momentum
           WHERE season = (SELECT MAX(season) FROM milb.team_momentum)
        )
        SELECT t.team_id,
               t.team_name,
               t.sport_id,
               COALESCE(sp.sport_name, 'Unknown') AS level,
               v.venue_name,
               v.city,
               v.state,
               v.latitude::float  AS latitude,
               v.longitude::float AS longitude,
               v.capacity,
               ls.cap_util,
               ls.avg_att,
               COALESCE(rm.momentum_label, 'unknown') AS momentum_label
          FROM milb.teams t
          JOIN milb.venues v ON v.venue_id = t.venue_id
          LEFT JOIN milb.sports sp ON sp.sport_id = t.sport_id
          LEFT JOIN last_season ls ON ls.team_id = t.team_id
          LEFT JOIN recent_momentum rm ON rm.team_id = t.team_id
         WHERE v.latitude IS NOT NULL
           AND v.capacity IS NOT NULL AND v.capacity > 0
           AND t.sport_id IN (11, 12, 13, 14)
    """)


@st.cache_data(ttl=600)
def load_doublea_leaders() -> pd.DataFrame:
    """Top Double-A teams by capacity util — peer inspiration shortlist."""
    return query_df(f"""
        SELECT t.team_name,
               t.team_id,
               AVG(f.capacity_utilization) AS cap_util,
               AVG(f.attendance) AS avg_att,
               COUNT(*) AS n
          FROM milb.game_features f
          JOIN milb.teams t ON t.team_id = f.team_id
         WHERE f.sport_id = {DOUBLE_A_SPORT_ID}
           AND f.season = 2025
           AND f.attendance IS NOT NULL
           AND f.capacity_utilization IS NOT NULL
         GROUP BY t.team_id, t.team_name
         HAVING COUNT(*) >= 40
         ORDER BY cap_util DESC
         LIMIT 5
    """)


# -- Render -------------------------------------------------------------------

st.title("The Picture")
st.markdown(
    "#### Four seasons of attendance, promotions, weather, and demographics on every MiLB team."
)
st.markdown(
    "Here's where the Rumble Ponies sit — and what the data says is behind it."
)

# Homework signal line
stats = load_homework_stats()
n_teams = int(stats.get("n_teams") or 0)
n_seasons = int(stats.get("n_seasons") or 0)
n_games = int(stats.get("n_games") or 0)
st.caption(
    f"{n_seasons} seasons · {n_teams} teams · {n_games:,} games · attendance, weather, "
    "promotions, demographics, peer comparisons."
)

st.divider()

# ─── 3 KPI tiles ───────────────────────────────────────────────────────────
k = load_rp_kpis()

c1, c2, c3 = st.columns(3)

if k.get("cap_util_2026") is not None:
    pct_now = k["cap_util_2026"] * 100
    if k.get("cap_util_2023") is not None:
        delta_pp = pct_now - k["cap_util_2023"] * 100
        c1.metric(
            "Seats filled, 2026 to date",
            f"{pct_now:.0f}%",
            f"{delta_pp:+.1f}pp vs 2023",
            delta_color="inverse",
        )
    else:
        c1.metric("Seats filled, 2026 to date", f"{pct_now:.0f}%")
else:
    c1.metric("Seats filled, 2026", "—")

if k.get("sat_gap") is not None:
    c2.metric(
        "Saturday vs Friday gap",
        f"{int(round(k['sat_gap'])):+,} fans",
        "4 years running",
        delta_color="inverse",
    )
else:
    c2.metric("Saturday vs Friday gap", "—")

if k.get("rank") is not None and k.get("rank_total"):
    c3.metric(
        "Double-A capacity rank, 2025",
        f"#{k['rank']} of {k['rank_total']}",
        "lower numbers fill more seats",
    )
else:
    c3.metric("Double-A capacity rank", "—")

st.divider()

# ─── 3 finding-card teasers ────────────────────────────────────────────────
st.markdown("#### What the data says")
st.caption("Click a card to read the full finding.")

f1, f2, f3 = st.columns(3)
with f1:
    with st.container(border=True):
        st.markdown("**Saturdays**")
        st.markdown(
            "Friday outdraws Saturday at home — every season since 2023. "
            "The promo calendar is inverted."
        )
        st.page_link("pages/findings/01_Saturdays.py", label="Read →")

with f2:
    with st.container(border=True):
        st.markdown("**Sundays**")
        st.markdown(
            "Sunday attendance has fallen every year since 2023, and the "
            "league has not. The cause is less settled."
        )
        st.page_link("pages/findings/02_Sundays.py", label="Read →")

with f3:
    with st.container(border=True):
        st.markdown("**The League is Shifting**")
        st.markdown(
            "The weekend premium is compressing across MiLB. Weekday "
            "attendance is holding. That reframes the playbook."
        )
        st.page_link("pages/findings/03_The_League_Is_Shifting.py", label="Read →")

st.divider()

# ─── Below the fold: map + peer leaders ────────────────────────────────────
st.markdown("#### Who else is out there")
st.caption(
    "Every MiLB team in one view, so peer comparison takes a click rather than "
    "a spreadsheet. Use this when you want to see what teams in your "
    "neighborhood are doing."
)

map_df = load_map_data()
leaders = load_doublea_leaders()

if map_df.empty:
    st.warning("Map data unavailable.")
else:
    map_df["level_label"] = map_df["sport_id"].map(LEVEL_NAMES).fillna(map_df["level"])

    # Color toggle (just two modes, per the refactor plan)
    color_choice = st.radio(
        "Color teams by",
        options=["Capacity utilization", "Momentum"],
        horizontal=True,
        index=0,
    )

    map_col, side_col = st.columns([3, 1])

    with map_col:
        # Bubble size: proportional to capacity, with floor
        max_cap = map_df["capacity"].max() or 1
        map_df["bubble"] = ((map_df["capacity"].fillna(2000) / max_cap) * 30 + 6).round(1)
        # Highlight RP
        map_df["is_rp"] = (map_df["team_id"] == RUMBLE_PONIES_ID)

        if color_choice == "Capacity utilization":
            plot = map_df[map_df["cap_util"].notna()].copy()
            plot["cap_pct"] = (plot["cap_util"] * 100).round(1)
            try:
                fig = px.scatter_map(
                    plot, lat="latitude", lon="longitude",
                    size="bubble", color="cap_pct",
                    color_continuous_scale="Viridis",
                    hover_name="team_name",
                    hover_data={
                        "venue_name": True, "city": True, "level_label": True,
                        "capacity": True, "cap_pct": ":.1f",
                        "latitude": False, "longitude": False, "bubble": False,
                    },
                    labels={"cap_pct": "% seats filled", "venue_name": "Venue",
                            "city": "City", "level_label": "Level",
                            "capacity": "Capacity"},
                    center={"lat": 38.5, "lon": -96.0}, zoom=3.0,
                    map_style="open-street-map", size_max=35, height=520,
                )
            except AttributeError:
                fig = px.scatter_mapbox(
                    plot, lat="latitude", lon="longitude",
                    size="bubble", color="cap_pct",
                    color_continuous_scale="Viridis",
                    hover_name="team_name",
                    center={"lat": 38.5, "lon": -96.0}, zoom=3.0,
                    mapbox_style="open-street-map", size_max=35, height=520,
                )
        else:  # Momentum
            plot = map_df.copy()
            try:
                fig = px.scatter_map(
                    plot, lat="latitude", lon="longitude",
                    size="bubble", color="momentum_label",
                    color_discrete_map=MOMENTUM_COLORS,
                    category_orders={"momentum_label": list(MOMENTUM_COLORS.keys()) + ["unknown"]},
                    hover_name="team_name",
                    hover_data={
                        "venue_name": True, "city": True, "level_label": True,
                        "momentum_label": True, "capacity": True,
                        "latitude": False, "longitude": False, "bubble": False,
                    },
                    labels={"momentum_label": "Momentum",
                            "venue_name": "Venue", "city": "City",
                            "level_label": "Level", "capacity": "Capacity"},
                    center={"lat": 38.5, "lon": -96.0}, zoom=3.0,
                    map_style="open-street-map", size_max=35, height=520,
                )
            except AttributeError:
                fig = px.scatter_mapbox(
                    plot, lat="latitude", lon="longitude",
                    size="bubble", color="momentum_label",
                    color_discrete_map=MOMENTUM_COLORS,
                    hover_name="team_name",
                    center={"lat": 38.5, "lon": -96.0}, zoom=3.0,
                    mapbox_style="open-street-map", size_max=35, height=520,
                )

        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("How to read this map"):
            st.markdown(
                "- **Each bubble is one MiLB ballpark.** Bubble size scales "
                "with venue capacity.\n"
                "- **Capacity utilization** mode: brighter = higher % of seats "
                "filled. Dark = team is filling a smaller share of its venue.\n"
                "- **Momentum** mode: green = surging or improving, yellow = "
                "stable, orange/red = declining or struggling. Based on year-"
                "over-year and intra-season trends.\n"
                "- Hover any team to see venue, city, level, and current "
                "metrics. For a full team report, open Methodology → Team Report."
            )

    with side_col:
        st.markdown("**Top 5 Double-A by capacity (2025)**")
        st.caption("Teams worth a peer study.")
        if not leaders.empty:
            for _, row in leaders.iterrows():
                pct = row["cap_util"] * 100
                st.markdown(
                    f"**{row['team_name']}**  \n"
                    f"<small>{pct:.0f}% seats filled · "
                    f"{row['avg_att']:,.0f} avg</small>",
                    unsafe_allow_html=True,
                )
                st.divider()
        else:
            st.caption("No leaderboard data available.")

render_footer()
