"""Opponent effects on attendance — away team rankings, home team view, distance effect.

New Streamlit / pandas / plotly patterns introduced here:
  - Attendance lift computation: matchup avg minus home team baseline
  - Haversine distance (pure Python math, no external library)
  - pd.cut() for distance bucketing
  - px.bar sorted descending with conditional coloring (lift pos/neg)
  - Scatter plot gated on single-team selection
"""

# ── Path setup (same boilerplate on every page) ───────────────────────────────
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import math

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.db import query_df
from utils.filters import game_type_filter, game_type_sql
from utils.footer import render_footer
from utils.navigation import see_also

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Opponents | MiLB", page_icon="⚔️", layout="wide")

LEVEL_ORDER = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}

# Distance buckets (miles) for the Geographic Effect tab
DIST_BINS   = [0, 100, 300, 600, 1000, 99999]
DIST_LABELS = ["< 100 mi", "100-300 mi", "300-600 mi", "600-1000 mi", "> 1000 mi"]


# ── Haversine formula (pure Python, no external library) ─────────────────────
# Returns the great-circle distance in miles between two lat/lon points.
def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi       = math.radians(lat2 - lat1)
    dlambda    = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=600)
def load_teams() -> pd.DataFrame:
    """All MiLB teams with level label and home venue coordinates.

    Venue lat/lon may be NULL for a handful of teams — callers should handle NaN.
    Returns: team_id, team_name, sport_id, level_label, venue_id, latitude, longitude
    """
    df = query_df("""
        SELECT
            t.team_id,
            t.team_name,
            t.sport_id,
            t.venue_id,
            v.latitude,
            v.longitude
        FROM milb.teams t
        LEFT JOIN milb.venues v ON t.venue_id = v.venue_id
        WHERE t.sport_id IN (11, 12, 13, 14)
        ORDER BY t.sport_id, t.team_name
    """)
    # Attach the friendly level label used throughout the dashboard
    df["level_label"] = df["sport_id"].map(LEVEL_ORDER).fillna("Unknown")
    return df


@st.cache_data(ttl=600)
def load_matchups(game_types: tuple = ("R",)) -> pd.DataFrame:
    """All completed games (with attendance + venue coords) for selected game types.

    We join the away team back to milb.teams and milb.venues so we can compute
    the Haversine distance between the home stadium and the away team's home city.

    Returns columns:
        game_pk, home_team_id, home_team_name, home_sport_id,
        away_team_id, away_team_name, away_sport_id,
        season, attendance,
        home_lat, home_lon,   ← home venue coords
        away_lat, away_lon    ← away team's home venue coords
    """
    return query_df(f"""
        SELECT
            g.game_pk,
            g.home_team_id,
            g.home_team_name,
            g.sport_id                          AS home_sport_id,
            g.away_team_id,
            g.away_team_name,
            awt.sport_id                         AS away_sport_id,
            g.season,
            g.attendance,
            hv.latitude                         AS home_lat,
            hv.longitude                        AS home_lon,
            av.latitude                         AS away_lat,
            av.longitude                        AS away_lon
        FROM milb.games g
        -- Join home team's venue for home coords
        LEFT JOIN milb.teams  ht ON ht.team_id  = g.home_team_id
        LEFT JOIN milb.venues hv ON hv.venue_id = ht.venue_id
        -- Join away team → their home venue for distance calculation
        LEFT JOIN milb.teams  awt ON awt.team_id = g.away_team_id
        LEFT JOIN milb.venues av  ON av.venue_id = awt.venue_id
        WHERE g.abstract_game_state = 'Final'
          AND {game_type_sql(game_types, col="g.game_type")}
          AND g.attendance IS NOT NULL
          AND g.attendance > 0
          AND g.sport_id IN (11, 12, 13, 14)
    """)


# ── Lift computation helper ───────────────────────────────────────────────────
def compute_lift(df: pd.DataFrame) -> pd.DataFrame:
    """Given a matchup DataFrame, compute attendance lift per away team.

    Steps:
      1. home_baseline: per home_team_id, average attendance across ALL their games
      2. matchup_avg:   per (home_team_id, away_team_id) pair, average attendance
      3. lift:          matchup_avg − home_baseline  (per pairing)
      4. away_team_lift: average lift across all home venues that hosted that away team

    Returns a DataFrame indexed by away_team_id with summary stats.
    """
    if df.empty:
        return pd.DataFrame()

    # Step 1: home baseline — one number per home team
    home_baseline = (
        df.groupby("home_team_id")["attendance"]
        .mean()
        .rename("home_baseline")
        .reset_index()
    )

    # Step 2 & 3: per matchup pair lift
    matchup = (
        df.groupby(["home_team_id", "away_team_id", "away_team_name"])
        .agg(
            matchup_avg=("attendance", "mean"),
            games=("game_pk", "count"),
            std=("attendance", "std"),
        )
        .reset_index()
    )
    matchup = matchup.merge(home_baseline, on="home_team_id", how="left")
    matchup["lift"] = matchup["matchup_avg"] - matchup["home_baseline"]

    # Step 4: aggregate lift across hosting venues for each away team
    away_lift = (
        matchup.groupby(["away_team_id", "away_team_name"])
        .agg(
            avg_lift=("lift", "mean"),
            avg_raw_attendance=("matchup_avg", "mean"),
            games=("games", "sum"),
            std=("std", "mean"),
        )
        .reset_index()
        .sort_values("avg_lift", ascending=False)
    )
    away_lift["avg_lift"] = away_lift["avg_lift"].round(0)
    away_lift["avg_raw_attendance"] = away_lift["avg_raw_attendance"].round(0)
    return away_lift


# ── Sidebar ───────────────────────────────────────────────────────────────────
teams_df = load_teams()

with st.sidebar:
    st.header("Filters")

    # Level multiselect — defaults to all four levels
    selected_levels = st.multiselect(
        "Level",
        options=list(LEVEL_ORDER.values()),
        default=list(LEVEL_ORDER.values()),
    )

    # Which team_ids belong to the chosen levels?
    level_teams = teams_df[teams_df["level_label"].isin(selected_levels)]

    # Home team selectbox — "— All teams —" keeps league-wide view
    team_options = ["— All teams —"] + (
        level_teams.sort_values("team_name")["team_name"].tolist()
    )
    _default_idx = team_options.index("Binghamton Rumble Ponies") if "Binghamton Rumble Ponies" in team_options else 0
    selected_team_name = st.selectbox("Home team (for deep-dive)", options=team_options, index=_default_idx)

    st.divider()
    selected_game_types = game_type_filter()

    st.divider()
    st.caption(
        "Lift = avg attendance for a specific matchup minus that home team's "
        "overall average. Positive lift means the away team draws extra fans."
    )


# ── Resolve selected home team ────────────────────────────────────────────────
# home_team_id is None when "— All teams —" is selected.
home_team_id = None
home_team_row = None
if selected_team_name != "— All teams —":
    mask = teams_df["team_name"] == selected_team_name
    if mask.any():
        home_team_row = teams_df[mask].iloc[0]
        home_team_id  = int(home_team_row["team_id"])


# ── Load matchup data and filter to selected levels ───────────────────────────
matchups_raw = load_matchups(game_types=selected_game_types)

# Keep only games where the home team is in one of the selected levels.
# We use the home_sport_id column for this; same approach as other pages.
level_sport_ids = set(
    sid for sid, label in LEVEL_ORDER.items() if label in selected_levels
)
matchups = matchups_raw[
    matchups_raw["home_sport_id"].isin(level_sport_ids)
].copy()


# ── Page title ────────────────────────────────────────────────────────────────
st.title("⚔️ Opponent Effects on Attendance")
st.caption(
    "How much does the away team change the crowd size? "
    "Lift = matchup average minus the home team's overall baseline."
)

if matchups.empty:
    st.warning("No game data found for the selected levels.")
    st.stop()


# ── Tabs ──────────────────────────────────────────────────────────────────────
# st.tabs() returns one context manager per label.
# Only the active tab's content is rendered, keeping the page fast.
tab_ranking, tab_home, tab_dist = st.tabs(
    ["Away Team Rankings", "Home Team View", "Distance Effect"]
)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Away Team Rankings
# Show which away teams consistently lift (or drag) attendance across the league.
# We restrict to same-level matchups so Triple-A crowds don't inflate rankings.
# ══════════════════════════════════════════════════════════════════════════════
with tab_ranking:

    st.subheader("Which away teams draw the biggest crowds?")
    st.caption(
        "Only same-level matchups are counted so Triple-A and Single-A games "
        "don't compete with each other. Lift = avg attendance for that matchup "
        "minus the home team's overall average."
    )

    # Same-level filter: home and away sport_id must match
    same_level = matchups[
        matchups["home_sport_id"] == matchups["away_sport_id"]
    ].copy()

    if same_level.empty:
        st.info("No same-level matchup data for the selected levels.")
    else:
        lift_df = compute_lift(same_level)

        if lift_df.empty:
            st.info("Could not compute lift — not enough game data.")
        else:
            # ── Top 20 away teams by lift ─────────────────────────────────────
            st.markdown("#### Top 20 — crowd boosters")
            top20 = lift_df.nlargest(20, "avg_lift").sort_values(
                "avg_lift", ascending=True   # ascending so chart bars grow right
            )

            # Color bars: green for positive lift, red for negative
            top20["color"] = top20["avg_lift"].apply(
                lambda x: "#1a9850" if x >= 0 else "#d73027"
            )

            fig_top = px.bar(
                top20,
                x="avg_lift",
                y="away_team_name",
                orientation="h",
                error_x="std",
                text=top20["avg_lift"].apply(lambda x: f"{x:+,.0f}"),
                labels={
                    "avg_lift":       "Avg Lift (attendance)",
                    "away_team_name": "Away Team",
                    "std":            "Std Dev",
                },
                color="color",
                color_discrete_map="identity",   # use the hex strings directly
                height=max(300, len(top20) * 26),
            )
            fig_top.update_traces(textposition="outside")
            # Add a vertical line at 0 to make the neutral point obvious
            fig_top.add_vline(x=0, line_dash="dash", line_color="gray", line_width=1)
            fig_top.update_layout(
                showlegend=False,
                xaxis_title="Avg Attendance Lift",
                yaxis_title=None,
                margin={"t": 10, "b": 20, "l": 10},
            )
            st.plotly_chart(fig_top, use_container_width=True)

            st.divider()

            # ── Bottom 10 — crowd killers ─────────────────────────────────────
            st.markdown("#### Bottom 10 — crowd detractors")
            bot10 = lift_df.nsmallest(10, "avg_lift").sort_values(
                "avg_lift", ascending=False   # largest negative value first
            )
            bot10["color"] = bot10["avg_lift"].apply(
                lambda x: "#1a9850" if x >= 0 else "#d73027"
            )

            fig_bot = px.bar(
                bot10,
                x="avg_lift",
                y="away_team_name",
                orientation="h",
                text=bot10["avg_lift"].apply(lambda x: f"{x:+,.0f}"),
                labels={
                    "avg_lift":       "Avg Lift (attendance)",
                    "away_team_name": "Away Team",
                },
                color="color",
                color_discrete_map="identity",
                height=max(200, len(bot10) * 30),
            )
            fig_bot.update_traces(textposition="outside")
            fig_bot.add_vline(x=0, line_dash="dash", line_color="gray", line_width=1)
            fig_bot.update_layout(
                showlegend=False,
                xaxis_title="Avg Attendance Lift",
                yaxis_title=None,
                margin={"t": 10, "b": 20, "l": 10},
            )
            st.plotly_chart(fig_bot, use_container_width=True)

            st.divider()

            # ── Full sortable table ───────────────────────────────────────────
            st.markdown("#### Full rankings table")
            table_df = lift_df[
                ["away_team_name", "games", "avg_lift", "avg_raw_attendance"]
            ].rename(columns={
                "away_team_name":      "Away Team",
                "games":               "Games",
                "avg_lift":            "Avg Lift",
                "avg_raw_attendance":  "Avg Attendance",
            })

            def color_lift(val):
                """Return green for positive lift, red for negative."""
                if pd.isna(val):  return ""
                if val > 0:       return "color: #1a9850"
                if val < 0:       return "color: #d73027"
                return ""

            styled_table = (
                table_df.style
                .map(color_lift, subset=["Avg Lift"])
                .format({
                    "Avg Lift":        "{:+,.0f}",
                    "Avg Attendance":  "{:,.0f}",
                    "Games":           "{:,}",
                })
            )
            # hide_index=True removes the default 0-based integer index column
            st.dataframe(styled_table, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Home Team View
# Ranks opponents by how much they lift (or hurt) attendance at a specific park.
# ══════════════════════════════════════════════════════════════════════════════
with tab_home:

    if home_team_id is None:
        # Friendly prompt — user hasn't picked a team yet
        st.info("Select a home team in the sidebar to see its opponent breakdown.")
    else:
        # Filter to games where selected team was the home team
        home_games = matchups[matchups["home_team_id"] == home_team_id].copy()

        if home_games.empty:
            st.warning(f"No home game data found for {selected_team_name}.")
        else:
            # Compute this team's baseline attendance (all home games)
            baseline = home_games["attendance"].mean()

            # Per-opponent stats
            opp_stats = (
                home_games.groupby(["away_team_id", "away_team_name"])
                .agg(
                    games=("game_pk", "count"),
                    avg_attendance=("attendance", "mean"),
                    std=("attendance", "std"),
                )
                .reset_index()
            )
            opp_stats["lift"] = opp_stats["avg_attendance"] - baseline
            opp_stats = opp_stats.sort_values("lift", ascending=False)
            opp_stats["avg_attendance"] = opp_stats["avg_attendance"].round(0)
            opp_stats["lift"]           = opp_stats["lift"].round(0)
            opp_stats["std"]            = opp_stats["std"].round(0).fillna(0)

            st.subheader(f"{selected_team_name} — opponents ranked by lift")
            st.caption(
                f"Home baseline (avg across all home games): "
                f"{baseline:,.0f} fans.  "
                f"Lift = opponent average minus that baseline."
            )

            # ── Bar chart: opponents sorted by lift (best at top) ─────────────
            chart_df = opp_stats.sort_values("lift", ascending=True)  # ascending for horizontal
            chart_df["color"] = chart_df["lift"].apply(
                lambda x: "#1a9850" if x >= 0 else "#d73027"
            )

            fig_opp = px.bar(
                chart_df,
                x="lift",
                y="away_team_name",
                orientation="h",
                error_x="std",
                text=chart_df["lift"].apply(lambda x: f"{x:+,.0f}"),
                labels={
                    "lift":           "Attendance Lift",
                    "away_team_name": "Opponent",
                    "std":            "Std Dev",
                },
                color="color",
                color_discrete_map="identity",
                height=max(300, len(chart_df) * 24),
            )
            fig_opp.update_traces(textposition="outside")
            fig_opp.add_vline(x=0, line_dash="dash", line_color="gray", line_width=1)
            fig_opp.update_layout(
                showlegend=False,
                xaxis_title="Attendance Lift vs Home Baseline",
                yaxis_title=None,
                margin={"t": 10, "b": 20, "l": 10},
            )
            st.plotly_chart(fig_opp, use_container_width=True)

            st.divider()

            # ── Sortable detail table ─────────────────────────────────────────
            st.markdown("#### Opponent detail table")
            detail = opp_stats[
                ["away_team_name", "games", "avg_attendance", "lift"]
            ].rename(columns={
                "away_team_name": "Opponent",
                "games":          "Home Games",
                "avg_attendance": "Avg Attendance",
                "lift":           "Lift vs Baseline",
            })

            def color_lift_cell(val):
                if pd.isna(val):  return ""
                if val > 0:       return "color: #1a9850"
                if val < 0:       return "color: #d73027"
                return ""

            styled_detail = (
                detail.style
                .map(color_lift_cell, subset=["Lift vs Baseline"])
                .format({
                    "Avg Attendance":  "{:,.0f}",
                    "Lift vs Baseline": "{:+,.0f}",
                })
            )
            st.dataframe(styled_detail, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Distance Effect
# Does travel distance between the two teams' home cities affect attendance?
# Closer rivals may bring more visiting fans; very distant teams feel exotic.
# ══════════════════════════════════════════════════════════════════════════════
with tab_dist:

    st.subheader("Does geographic distance affect attendance lift?")
    st.caption(
        "Distance = Haversine (straight-line) miles between the home stadium "
        "and the away team's home venue. Games where either venue has no GPS "
        "coordinates are excluded."
    )

    # ── Compute Haversine distance row-by-row ─────────────────────────────────
    # We use .apply(axis=1) so each row passes through the Python function.
    # Rows with any missing lat/lon are skipped (dropna first).
    dist_df = matchups.dropna(
        subset=["home_lat", "home_lon", "away_lat", "away_lon"]
    ).copy()

    if dist_df.empty:
        st.info("No venue coordinate data available to compute distances.")
    else:
        dist_df["distance_miles"] = dist_df.apply(
            lambda row: haversine_miles(
                row["home_lat"], row["home_lon"],
                row["away_lat"], row["away_lon"],
            ),
            axis=1,
        )

        # ── Compute lift per game (game attendance minus home team baseline) ──
        home_baselines = (
            dist_df.groupby("home_team_id")["attendance"]
            .mean()
            .rename("home_baseline")
            .reset_index()
        )
        dist_df = dist_df.merge(home_baselines, on="home_team_id", how="left")
        dist_df["lift"] = dist_df["attendance"] - dist_df["home_baseline"]

        # ── Bin distances into labeled buckets using pd.cut() ─────────────────
        # right=False means each bin is [left, right), so 100 miles lands in
        # "100-300 mi" rather than "< 100 mi".
        dist_df["dist_bucket"] = pd.cut(
            dist_df["distance_miles"],
            bins=DIST_BINS,
            labels=DIST_LABELS,
            right=False,
        )

        # ── Aggregate lift per distance bucket ────────────────────────────────
        bucket_agg = (
            dist_df.dropna(subset=["dist_bucket"])
            .groupby("dist_bucket", observed=True)["lift"]
            .agg(avg_lift="mean", std="std", n="count")
            .reset_index()
        )
        bucket_agg["avg_lift"] = bucket_agg["avg_lift"].round(0)
        bucket_agg["std"]      = bucket_agg["std"].round(0).fillna(0)

        # ── Bar chart: avg lift by distance bucket ────────────────────────────
        # error_y="std" adds ±1 std dev bars so we can see variability.
        bucket_agg["color"] = bucket_agg["avg_lift"].apply(
            lambda x: "#1a9850" if x >= 0 else "#d73027"
        )

        fig_dist = px.bar(
            bucket_agg,
            x="dist_bucket",
            y="avg_lift",
            error_y="std",
            text="n",
            labels={
                "dist_bucket": "Distance Bucket",
                "avg_lift":    "Avg Attendance Lift",
                "std":         "Std Dev",
                "n":           "Games",
            },
            color="color",
            color_discrete_map="identity",
            category_orders={"dist_bucket": DIST_LABELS},
            height=400,
        )
        fig_dist.update_traces(textposition="outside", texttemplate="%{text} games")
        fig_dist.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1)
        fig_dist.update_layout(
            showlegend=False,
            xaxis_title="Distance Between Home Venues",
            yaxis_title="Avg Attendance Lift",
            margin={"t": 30, "b": 20},
        )
        st.plotly_chart(fig_dist, use_container_width=True)

        st.divider()

        # ── Scatter: distance vs attendance (single home team only) ──────────
        # Showing every game league-wide produces tens of thousands of dots
        # and overwhelms the chart — gate it on a single team selection.
        if home_team_id is not None:
            scatter_data = dist_df[
                dist_df["home_team_id"] == home_team_id
            ].copy()

            if scatter_data.empty:
                st.info(f"No distance data available for {selected_team_name}.")
            else:
                st.subheader(
                    f"{selected_team_name} — distance vs attendance (per game)"
                )
                st.caption(
                    "Each dot is one home game. Hover to see the away team name, "
                    "distance, and attendance."
                )

                # Cast season to string so Plotly uses categorical colors
                scatter_data["season"] = scatter_data["season"].astype(str)

                fig_scatter = px.scatter(
                    scatter_data.sort_values("distance_miles"),
                    x="distance_miles",
                    y="attendance",
                    color="season",
                    hover_data={
                        "away_team_name":  True,
                        "distance_miles":  ":.0f",
                        "attendance":      ":,",
                        "season":          False,
                    },
                    labels={
                        "distance_miles":  "Distance (miles)",
                        "attendance":      "Attendance",
                        "away_team_name":  "Away Team",
                        "season":          "Season",
                    },
                    color_discrete_sequence=px.colors.qualitative.Set2,
                    category_orders={"season": sorted(scatter_data["season"].unique())},
                    height=400,
                )
                fig_scatter.update_traces(marker_size=8, opacity=0.8)
                fig_scatter.update_layout(
                    xaxis_title="Distance Between Home Venues (miles)",
                    yaxis_title="Attendance",
                    legend_title="Season",
                    margin={"t": 10, "b": 20},
                )
                st.plotly_chart(fig_scatter, use_container_width=True)
        else:
            # League-wide: skip the scatter, show an explanation instead
            st.info(
                "Select a specific home team in the sidebar to see a per-game "
                "scatter plot of distance vs attendance."
            )

        # ── Distance summary table ────────────────────────────────────────────
        st.divider()
        st.markdown("#### Distance bucket summary")
        dist_table = bucket_agg[["dist_bucket", "n", "avg_lift", "std"]].rename(columns={
            "dist_bucket": "Distance",
            "n":           "Games",
            "avg_lift":    "Avg Lift",
            "std":         "Std Dev",
        })

        def color_lift_dist(val):
            if pd.isna(val):  return ""
            if val > 0:       return "color: #1a9850"
            if val < 0:       return "color: #d73027"
            return ""

        styled_dist = (
            dist_table.style
            .map(color_lift_dist, subset=["Avg Lift"])
            .format({
                "Avg Lift": "{:+,.0f}",
                "Std Dev":  "{:,.0f}",
                "Games":    "{:,}",
            })
        )
        st.dataframe(styled_dist, use_container_width=True, hide_index=True)


# ── Cross-page navigation + footer ───────────────────────────────────────────
see_also([
    ("Scheduling",       "pages/6_Scheduling.py",       "homestand position, win streaks, DoW"),
    ("Attendance",       "pages/1_Attendance.py",       "baseline per-team trends"),
])
render_footer(scripts=["build_features"])
