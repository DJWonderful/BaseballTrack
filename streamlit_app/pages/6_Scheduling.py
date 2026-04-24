"""Multi-game and homestand scheduling effects on attendance.

New Streamlit / pandas patterns introduced here:
  - Vectorized homestand detection with groupby + shift (no Python loops)
  - np.clip to cap outlier positions before aggregation
  - pd.cut with integer bins to bucket "game number in season"
  - px.scatter + fig.add_scatter() to overlay a rolling-average line on dots
  - color_discrete_map="identity" for pre-computed hex bar colors
  - Custom streak accumulator (groupby + apply) for win/loss streaks
  - Same-date game-pair detection for doubleheader comparison
"""

# ── Path setup ────────────────────────────────────────────────────────────────
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from utils.db import query_df
from utils.filters import game_type_filter, game_type_sql
from utils.theme import SEASON_COLORS
from utils.footer import render_footer
from utils.navigation import see_also

st.set_page_config(page_title="Scheduling | MiLB", page_icon="📅", layout="wide")

LEVEL_ORDER = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}
MONTH_ABBR  = {4: "Apr", 5: "May", 6: "Jun", 7: "Jul", 8: "Aug", 9: "Sep"}

# ── School calendar approximation ─────────────────────────────────────────────
# Maps each US state abbreviation to the approximate month the local schools
# let out for summer (release_month) and return in fall (return_month).
# Two tiers:
#   "early" states (South/Sun Belt): release ≈ May, return ≈ Aug
#   "late"  states (Northeast/Midwest):  release ≈ Jun, return ≈ Sep
# A game date is considered "summer break" if:
#   game.month >= release_month  AND  game.month < return_month
SCHOOL_CALENDAR: dict[str, dict] = {
    # Early-release states (Southern / Sun Belt)
    "AL": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "AR": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "AZ": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "FL": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "GA": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "LA": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "MS": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "NC": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "NM": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "OK": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "SC": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "TN": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "TX": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    # Late-release states (Northeast / Midwest)
    "CA": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "CO": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "CT": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "DE": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "IA": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "ID": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "IL": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "IN": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "KS": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "KY": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "MA": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "MD": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "ME": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "MI": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "MN": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "MO": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "MT": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "NE": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "NH": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "NJ": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "NV": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "NY": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "OH": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "OR": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "PA": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "RI": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "SD": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "UT": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "VA": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "VT": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "WA": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "WI": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "WV": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "WY": {"release_month": 6, "return_month": 9, "tier": "Late (Jun)"},
    "ND": {"release_month": 5, "return_month": 9, "tier": "Early (May)"},
    "HI": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
    "AK": {"release_month": 5, "return_month": 8, "tier": "Early (May)"},
}

# Home game number buckets (right=False → left-inclusive intervals)
GAME_NUM_BINS   = [1,   2,  6,  16,  31,  51, 999]
GAME_NUM_LABELS = [
    "Game 1\n(Opener)", "Games 2-5", "Games 6-15",
    "Games 16-30", "Games 31-50", "Games 51+",
]

# Cap homestand position at this value (e.g. "Game 5+" groups 5,6,7,…)
HOMESTAND_CAP = 5


# ── Data loading ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def load_teams() -> pd.DataFrame:
    return query_df("""
        SELECT t.team_id, t.team_name, t.sport_id,
               COALESCE(sp.sport_name, 'Unknown') AS level
        FROM milb.teams t
        LEFT JOIN milb.sports sp ON t.sport_id = sp.sport_id
        WHERE t.sport_id IN (11,12,13,14)
        ORDER BY t.sport_id, t.team_name
    """)


@st.cache_data(ttl=600)
def load_team_results(game_types: tuple = ("R",)) -> pd.DataFrame:
    """All game results from both the home and away team's perspective.

    Returns one row per team per game with columns:
        team_id, game_pk, game_date, season, is_home, result ('W'/'L'), attendance
    Used to compute each team's win/loss streak entering each home game.
    """
    _gtype = game_type_sql(game_types)
    return query_df(f"""
        SELECT home_team_id AS team_id,
               game_pk,
               game_date::date AS game_date,
               season,
               TRUE  AS is_home,
               CASE WHEN home_score > away_score THEN 'W' ELSE 'L' END AS result,
               attendance
        FROM milb.games
        WHERE abstract_game_state = 'Final'
          AND {_gtype}
          AND sport_id IN (11,12,13,14)
          AND home_score IS NOT NULL
        UNION ALL
        SELECT away_team_id AS team_id,
               game_pk,
               game_date::date AS game_date,
               season,
               FALSE AS is_home,
               CASE WHEN away_score > home_score THEN 'W' ELSE 'L' END AS result,
               NULL  AS attendance   -- gate belongs to home team
        FROM milb.games
        WHERE abstract_game_state = 'Final'
          AND {_gtype}
          AND sport_id IN (11,12,13,14)
          AND away_score IS NOT NULL
        ORDER BY team_id, season, game_date
    """)


@st.cache_data(ttl=600)
def compute_streaks(results_df: pd.DataFrame) -> pd.DataFrame:
    """Add a pre-game win/loss streak column to every row.

    Algorithm:
      - Sort by (team_id, season, game_date).
      - Walk forward game-by-game within each (team, season) group.
      - Record the streak *before* processing the current game, then update.
      - Positive streak = winning (W1, W2, …), negative = losing (L1, L2, …).
      - Streak resets to ±1 whenever the result changes (not accumulates from 0).

    This uses groupby + apply with a custom accumulator function instead of
    a vectorized shift because consecutive-streak logic is inherently stateful
    (each value depends on the previous streak, not just the previous result).
    """
    def _streak_series(group: pd.DataFrame) -> pd.Series:
        """Returns the streak ENTERING each game (before result is known)."""
        streak = 0
        streaks = []
        for r in group["result"]:
            streaks.append(streak)          # record entering-streak first …
            if r == "W":
                streak = max(1, streak + 1) # … then update: W continues / starts win streak
            else:
                streak = min(-1, streak - 1)
        return pd.Series(streaks, index=group.index)

    results_df = results_df.sort_values(["team_id", "season", "game_date"]).copy()
    results_df["pre_game_streak"] = (
        results_df.groupby(["team_id", "season"], group_keys=False)
        .apply(_streak_series)
    )
    return results_df


@st.cache_data(ttl=600)
def compute_win_pct(results_df: pd.DataFrame) -> pd.DataFrame:
    """Add cumulative win% ENTERING each game (before result is known).

    Uses the same sorted results_df produced by load_team_results().
    Vectorized: cumulative sum of (result == 'W') divided by cumulative game count,
    all shifted by one so the current game's result is not included.

    Returns columns added to results_df:
        pre_game_wins   - cumulative wins before this game
        pre_game_games  - cumulative games before this game
        pre_game_win_pct - pre_game_wins / pre_game_games (NaN for game 1)
    """
    df = results_df.sort_values(["team_id", "season", "game_date"]).copy()
    df["is_win"] = (df["result"] == "W").astype(int)

    g = df.groupby(["team_id", "season"])

    # cumsum then shift → entering-game totals (game 1 → NaN)
    df["pre_game_wins"]  = g["is_win"].cumsum().shift(1)
    df["pre_game_games"] = g["is_win"].transform("cumcount")   # 0-indexed game number
    df["pre_game_win_pct"] = df["pre_game_wins"] / df["pre_game_games"]

    return df


@st.cache_data(ttl=3600)
def load_home_venue_states() -> pd.DataFrame:
    """Map each home team to its venue's state abbreviation.

    Returns: team_id, state_abbrev
    Used to join school calendar tier onto home games.
    """
    return query_df("""
        SELECT t.team_id, v.state_abbrev
        FROM milb.teams t
        JOIN milb.venues v ON t.venue_id = v.venue_id
        WHERE t.sport_id IN (11,12,13,14)
          AND v.state_abbrev IS NOT NULL
    """)


@st.cache_data(ttl=600)
def load_all_games(game_types: tuple = ("R",)) -> pd.DataFrame:
    """Every final game (home + away IDs) for the selected game types.

    We need both IDs so that each team's *full* schedule (home + away) can be
    reconstructed. That full schedule is what lets us detect homestand boundaries.
    """
    return query_df(f"""
        SELECT game_pk,
               home_team_id,
               away_team_id,
               game_date::date AS game_date,
               season,
               attendance,
               sport_id
        FROM milb.games
        WHERE abstract_game_state = 'Final'
          AND {game_type_sql(game_types)}
          AND sport_id IN (11,12,13,14)
        ORDER BY game_date
    """)


# ── Homestand computation (vectorized) ────────────────────────────────────────
@st.cache_data(ttl=600)
def compute_schedule_features(all_games: pd.DataFrame) -> pd.DataFrame:
    """Add homestand position and home-game-number to every home game row.

    Algorithm (fully vectorized — no Python loops over teams):

    1.  Build a "home perspective" row for each game (team_id = home_team_id,
        is_home = True, attendance carried over).
    2.  Build an "away perspective" row for each game (team_id = away_team_id,
        is_home = False, no attendance — the gate belongs to the home team).
    3.  Union both sets and sort by (team_id, season, game_date).
    4.  Within each (team, season) group, shift is_home by 1 to get
        the *previous* game's location flag.
    5.  A new homestand starts when: current game is Home AND previous game
        was Away (or it was the first game of the season → prev is NaN).
    6.  cumsum() on the new-homestand flag gives a unique homestand_id per group.
    7.  cumcount() within (team, season, homestand_id) gives position in homestand.
    8.  cumcount() within (team, season) for home-only rows gives game number.
    """
    g = all_games.copy()
    g["game_date"] = pd.to_datetime(g["game_date"])

    # ── Step 1 & 2: build dual-perspective rows ──────────────────────────────
    home_rows = (
        g[["game_pk", "home_team_id", "game_date", "season", "attendance"]]
        .rename(columns={"home_team_id": "team_id"})
        .assign(is_home=True)
    )

    away_rows = (
        g[["game_pk", "away_team_id", "game_date", "season"]]
        .rename(columns={"away_team_id": "team_id"})
        .assign(is_home=False, attendance=np.nan)
    )

    # ── Step 3: union and sort ────────────────────────────────────────────────
    sched = (
        pd.concat([home_rows, away_rows], ignore_index=True)
        .sort_values(["team_id", "season", "game_date"])
        .reset_index(drop=True)
    )

    # ── Step 4: previous game's is_home flag ─────────────────────────────────
    # groupby().shift(1) keeps boundaries between (team, season) groups clean —
    # the first game of each season won't inherit the previous season's last flag.
    # shift(1, fill_value=False) avoids the pandas FutureWarning about
    # object-dtype downcasting that .fillna(False) triggers on boolean columns.
    sched["prev_is_home"] = (
        sched.groupby(["team_id", "season"])["is_home"]
        .shift(1, fill_value=False)
        .astype(bool)
    )

    # ── Step 5 & 6: homestand ID ──────────────────────────────────────────────
    sched["new_homestand"] = sched["is_home"] & ~sched["prev_is_home"]
    sched["homestand_id"]  = sched.groupby(["team_id", "season"])["new_homestand"].cumsum()

    # ── Step 7 & 8: positions (home games only) ───────────────────────────────
    home_only = sched[sched["is_home"]].sort_values(["team_id", "season", "game_date"]).copy()

    home_only["position_in_homestand"] = (
        home_only.groupby(["team_id", "season", "homestand_id"]).cumcount() + 1
    )
    home_only["home_game_num"] = (
        home_only.groupby(["team_id", "season"]).cumcount() + 1
    )

    return home_only[
        ["game_pk", "team_id", "season", "game_date", "attendance",
         "homestand_id", "position_in_homestand", "home_game_num"]
    ].reset_index(drop=True)


# ── Sidebar ────────────────────────────────────────────────────────────────────
teams_df = load_teams()
teams_df["level_label"] = teams_df["sport_id"].map(LEVEL_ORDER).fillna(teams_df["level"])

with st.sidebar:
    st.header("Filters")

    selected_levels = st.multiselect(
        "Level",
        options=list(LEVEL_ORDER.values()),
        default=list(LEVEL_ORDER.values()),
    )

    level_teams = teams_df[teams_df["level_label"].isin(selected_levels)]
    team_options = ["— All teams —"] + level_teams.sort_values("team_name")["team_name"].tolist()
    _default_idx = team_options.index("Binghamton Rumble Ponies") if "Binghamton Rumble Ponies" in team_options else 0
    selected_team_name = st.selectbox("Team", options=team_options, index=_default_idx)

    st.divider()
    selected_game_types = game_type_filter()

    st.divider()
    st.caption(
        "**Homestand** = consecutive home games with no away games in between. "
        "Position 1 is the first game after the team returns home."
    )


# ── Load and filter ────────────────────────────────────────────────────────────
raw = load_all_games(game_types=selected_game_types)

# compute_schedule_features is @st.cache_data so it runs once and reuses the result.
# Filtering happens in-memory after the cache lookup.
hs = compute_schedule_features(raw)

level_ids = set(level_teams["team_id"])
hs = hs[hs["team_id"].isin(level_ids)].copy()

if selected_team_name != "— All teams —":
    team_id = int(teams_df.loc[teams_df["team_name"] == selected_team_name, "team_id"].iloc[0])
    hs = hs[hs["team_id"] == team_id].copy()

# ── Page header ────────────────────────────────────────────────────────────────
scope = selected_team_name if selected_team_name != "— All teams —" else "All selected teams"
st.title("📅 Scheduling Effects on Attendance")
st.caption(f"Showing {len(hs):,} home games · {scope}")

if hs.empty:
    st.warning("No data for the selected filters.")
    st.stop()

hs["attendance"] = pd.to_numeric(hs["attendance"], errors="coerce")

# ── Metric row ─────────────────────────────────────────────────────────────────
season_avg   = hs["attendance"].mean()
opener_avg   = hs[hs["home_game_num"] == 1]["attendance"].mean()
opener_delta = opener_avg - season_avg if pd.notna(opener_avg) else None

g1_avg       = hs[hs["position_in_homestand"] == 1]["attendance"].mean()
g5plus_avg   = hs[hs["position_in_homestand"] >= HOMESTAND_CAP]["attendance"].mean()
hs_delta     = (g5plus_avg - g1_avg) if (pd.notna(g5plus_avg) and pd.notna(g1_avg)) else None

c1, c2, c3, c4 = st.columns(4)
c1.metric("Season avg attendance",  f"{season_avg:,.0f}" if pd.notna(season_avg) else "—")
c2.metric(
    "Opening Day avg",
    f"{opener_avg:,.0f}" if pd.notna(opener_avg) else "—",
    delta=f"{opener_delta:+,.0f} vs season avg" if opener_delta is not None else None,
)
c3.metric("Homestand game 1 avg",   f"{g1_avg:,.0f}"    if pd.notna(g1_avg) else "—")
c4.metric(
    f"Homestand game {HOMESTAND_CAP}+ avg",
    f"{g5plus_avg:,.0f}" if pd.notna(g5plus_avg) else "—",
    delta=f"{hs_delta:+,.0f} vs game 1" if hs_delta is not None else None,
)

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
tab_arc, tab_hs, tab_cal, tab_perf = st.tabs(
    ["📈 Season Arc", "🏠 Homestand Position", "📆 Calendar Effects", "⚡ Performance Effects"]
)


# ── TAB 1: Season Arc ──────────────────────────────────────────────────────────
with tab_arc:
    st.subheader("Attendance by home game number in season")
    st.caption(
        "Home game #1 is Opening Day. MiLB teams play ~65-75 home games per season. "
        "Games are grouped into buckets to smooth single-game variance. "
        "Error bars show ±1 standard deviation across individual games."
    )

    arc_df = hs.dropna(subset=["home_game_num", "attendance"]).copy()
    arc_df["home_game_num"] = arc_df["home_game_num"].astype(int)

    # pd.cut with right=False creates left-inclusive intervals:
    #   [1, 2) → game 1 only   [2, 6) → games 2-5   [6, 16) → games 6-15 …
    # include_lowest=True ensures the minimum value (1) is caught by the first bin.
    arc_df["game_bucket"] = pd.cut(
        arc_df["home_game_num"],
        bins=GAME_NUM_BINS,
        labels=GAME_NUM_LABELS,
        right=False,
        include_lowest=True,
    )

    arc_agg = (
        arc_df.dropna(subset=["game_bucket"])
        .groupby("game_bucket", observed=True)["attendance"]
        .agg(avg="mean", std="std", n="count")
        .reset_index()
    )
    arc_agg["avg"] = arc_agg["avg"].round(0)
    arc_agg["std"] = arc_agg["std"].round(0).fillna(0)

    fig_arc = px.bar(
        arc_agg,
        x="game_bucket",
        y="avg",
        error_y="std",
        text="n",
        labels={"game_bucket": "Home Game Number", "avg": "Avg Attendance", "n": "Games"},
        color="avg",
        color_continuous_scale="Blues",
        height=420,
    )
    fig_arc.update_traces(textposition="outside", texttemplate="%{text} games")
    fig_arc.update_layout(
        coloraxis_showscale=False,
        margin={"t": 30, "b": 20},
    )
    st.plotly_chart(fig_arc, use_container_width=True)

    # For a single team, also show the game-by-game line chart with rolling avg
    if selected_team_name != "— All teams —" and len(arc_df) > 5:
        st.subheader("Game-by-game attendance arc")
        st.caption(
            "Each dot is one home game. Solid lines are 5-game rolling averages. "
            "Gaps indicate doubleheaders or schedule anomalies."
        )

        # Cast season to string so plotly treats it as categorical (distinct colors)
        arc_line = arc_df.sort_values(["season", "home_game_num"]).copy()
        arc_line["season"] = arc_line["season"].astype(str)

        # groupby().transform() applies the rolling avg within each season group,
        # keeping the result aligned with the original DataFrame index.
        arc_line["rolling_avg"] = (
            arc_line.groupby("season")["attendance"]
            .transform(lambda x: x.rolling(5, min_periods=2).mean())
            .round(0)
        )

        fig_line = px.scatter(
            arc_line,
            x="home_game_num",
            y="attendance",
            color="season",
            opacity=0.30,
            labels={"home_game_num": "Home Game Number", "attendance": "Attendance"},
            color_discrete_map=SEASON_COLORS,
            height=360,
        )
        fig_line.update_traces(marker_size=5)

        # Overlay rolling average as a solid line for each season.
        # fig.add_scatter() appends a new trace to an existing figure —
        # useful when you want to mix chart types (scatter dots + line).
        for szn in sorted(arc_line["season"].unique()):
            szn_data = arc_line[arc_line["season"] == szn].dropna(subset=["rolling_avg"])
            fig_line.add_scatter(
                x=szn_data["home_game_num"],
                y=szn_data["rolling_avg"],
                mode="lines",
                name=f"{szn} trend",
                line={"color": SEASON_COLORS.get(szn, "#95a5a6"), "width": 2},
                showlegend=True,
            )

        fig_line.update_layout(margin={"t": 10, "b": 10})
        st.plotly_chart(fig_line, use_container_width=True)


# ── TAB 2: Homestand Position ──────────────────────────────────────────────────
with tab_hs:
    st.subheader("Attendance by position in homestand")
    st.caption(
        "Position 1 = first game after the team returns home from a road trip. "
        f"Positions {HOMESTAND_CAP}+ are grouped. "
        "Green bars = same or higher than game 1; red = lower (homestand fatigue)."
    )

    pos_df = hs.dropna(subset=["position_in_homestand", "attendance"]).copy()
    pos_df["position_in_homestand"] = pos_df["position_in_homestand"].astype(int)

    # np.clip caps all values > HOMESTAND_CAP to exactly HOMESTAND_CAP.
    # This groups "Game 5, 6, 7, …" into a single "Game 5+" bucket.
    pos_df["pos_capped"] = np.clip(pos_df["position_in_homestand"], 1, HOMESTAND_CAP)
    pos_df["pos_label"] = pos_df["pos_capped"].apply(
        lambda x: f"Game {x}+" if x == HOMESTAND_CAP else f"Game {x}"
    )

    pos_order = [f"Game {i}" for i in range(1, HOMESTAND_CAP)] + [f"Game {HOMESTAND_CAP}+"]

    pos_agg = (
        pos_df.groupby("pos_label")["attendance"]
        .agg(avg="mean", std="std", n="count")
        .reset_index()
    )
    pos_agg["avg"] = pos_agg["avg"].round(0)
    pos_agg["std"] = pos_agg["std"].round(0).fillna(0)
    pos_agg["_sort"] = pos_agg["pos_label"].map({p: i for i, p in enumerate(pos_order)})
    pos_agg = pos_agg.sort_values("_sort")

    # Compute lift vs Game 1 baseline to drive bar colors
    g1_baseline = pos_agg.loc[pos_agg["pos_label"] == "Game 1", "avg"].values
    if len(g1_baseline) > 0:
        pos_agg["lift_vs_g1"] = (pos_agg["avg"] - g1_baseline[0]).round(0)
    else:
        pos_agg["lift_vs_g1"] = 0.0

    # color_discrete_map="identity" tells plotly to use the column values directly
    # as CSS color strings (hex codes), bypassing plotly's normal color mapping.
    pos_agg["bar_color"] = pos_agg["lift_vs_g1"].apply(
        lambda x: "#2ecc71" if x >= 0 else "#e74c3c"
    )

    fig_pos = px.bar(
        pos_agg,
        x="pos_label",
        y="avg",
        error_y="std",
        text="n",
        labels={"pos_label": "Game in Homestand", "avg": "Avg Attendance", "n": "Games"},
        color="bar_color",
        color_discrete_map="identity",
        category_orders={"pos_label": pos_order},
        height=420,
    )
    fig_pos.update_traces(textposition="outside", texttemplate="%{text} games")
    fig_pos.update_layout(showlegend=False, margin={"t": 30, "b": 20})
    st.plotly_chart(fig_pos, use_container_width=True)

    # Summary table
    display = pos_agg[["pos_label", "avg", "n", "lift_vs_g1"]].copy()
    display.columns = ["Position", "Avg Attendance", "Games", "vs Game 1"]
    display["Avg Attendance"] = display["Avg Attendance"].map("{:,.0f}".format)
    display["vs Game 1"]      = display["vs Game 1"].map("{:+,.0f}".format)
    st.dataframe(display.set_index("Position"), use_container_width=True)

    st.divider()

    # Homestand length distribution
    st.subheader("Homestand length distribution")
    st.caption(
        "How long are typical MiLB homestands? "
        "Most run 4-7 games, matching a 7-game series structure."
    )

    hs_len = (
        hs.dropna(subset=["attendance"])
        .groupby(["team_id", "season", "homestand_id"])["position_in_homestand"]
        .max()
        .reset_index()
        .rename(columns={"position_in_homestand": "homestand_length"})
    )
    hs_len["homestand_length"] = hs_len["homestand_length"].clip(upper=8).astype(int)

    len_counts = (
        hs_len["homestand_length"]
        .value_counts()
        .sort_index()
        .reset_index()
    )
    len_counts.columns = ["Length", "Count"]
    len_counts["label"] = len_counts["Length"].apply(
        lambda x: f"{x}+" if x == 8 else str(x)
    )

    fig_len = px.bar(
        len_counts,
        x="label",
        y="Count",
        text="Count",
        labels={"label": "Homestand Length (games)", "Count": "# Homestands"},
        color="Count",
        color_continuous_scale="Blues",
        height=300,
    )
    fig_len.update_traces(textposition="outside")
    fig_len.update_layout(coloraxis_showscale=False, margin={"t": 10, "b": 10})
    st.plotly_chart(fig_len, use_container_width=True)


# ── TAB 3: Calendar Effects ────────────────────────────────────────────────────
with tab_cal:
    st.subheader("Attendance by month")
    st.caption(
        "The MiLB regular season runs April through September. "
        "The dip in July often coincides with the All-Star break (typically mid-July). "
        "Late-season changes may reflect playoff races, back-to-school, or weather."
    )

    cal_df = hs.dropna(subset=["game_date", "attendance"]).copy()
    cal_df["month"] = pd.to_datetime(cal_df["game_date"]).dt.month
    cal_df = cal_df[cal_df["month"].between(4, 9)].copy()
    cal_df["month_label"] = cal_df["month"].map(MONTH_ABBR)

    month_order = list(MONTH_ABBR.values())

    month_agg = (
        cal_df.groupby("month_label")["attendance"]
        .agg(avg="mean", std="std", n="count")
        .reset_index()
    )
    month_agg["avg"] = month_agg["avg"].round(0)
    month_agg["std"] = month_agg["std"].round(0).fillna(0)

    fig_cal = px.bar(
        month_agg,
        x="month_label",
        y="avg",
        error_y="std",
        text="n",
        labels={"month_label": "Month", "avg": "Avg Attendance", "n": "Games"},
        color="avg",
        color_continuous_scale="Oranges",
        category_orders={"month_label": month_order},
        height=420,
    )
    fig_cal.update_traces(textposition="outside", texttemplate="%{text} games")
    # add_vline on a categorical x-axis requires a numeric bar *index*, not the
    # category label string.  "Jul" is the 4th bar (0-indexed → 3) in month_order.
    if "Jul" in month_order:
        fig_cal.add_vline(
            x=month_order.index("Jul"),
            line_dash="dot",
            line_color="rgba(100,100,100,0.5)",
            annotation_text="All-Star Break",
            annotation_position="top right",
        )
    fig_cal.update_layout(
        coloraxis_showscale=False,
        margin={"t": 30, "b": 20},
    )
    st.plotly_chart(fig_cal, use_container_width=True)

    # By-season month breakdown (when multiple seasons present)
    seasons = sorted(hs["season"].dropna().unique())
    if len(seasons) > 1:
        st.subheader("Month trends by season")
        st.caption("Each line is one season — reveals whether the seasonal arc shifted year to year.")

        # Group by numeric month so we can sort correctly before plotting.
        # Grouping by month_label (string) and relying on category_orders alone
        # doesn't guarantee the rows are in calendar order — plotly connects
        # points in the order they appear in the DataFrame.
        szn_month = (
            cal_df.groupby(["season", "month"])["attendance"]
            .mean()
            .round(0)
            .reset_index()
            .sort_values(["season", "month"])   # ← ensures Apr→May→…→Sep order
        )
        szn_month["month_label"] = szn_month["month"].map(MONTH_ABBR)
        szn_month["season"] = szn_month["season"].astype(str)

        fig_szn = px.line(
            szn_month,
            x="month_label",
            y="attendance",
            color="season",
            markers=True,
            labels={"month_label": "Month", "attendance": "Avg Attendance"},
            color_discrete_map=SEASON_COLORS,
            category_orders={"month_label": month_order},
            height=360,
        )
        fig_szn.update_traces(marker_size=8, line_width=2)
        fig_szn.update_layout(margin={"t": 10, "b": 10})
        st.plotly_chart(fig_szn, use_container_width=True)

    st.divider()

    # ── School calendar: summer break vs. school-year attendance ─────────────
    st.subheader("School calendar effect")
    st.caption(
        "Games are tagged 'Summer Break' when the home venue's state has "
        "released schools for summer (typically late May or June, depending on state). "
        "Early-release states (South/Sun Belt) let out ~May; late-release "
        "(Northeast/Midwest) let out ~June. Return is August or September respectively."
    )

    # Join state_abbrev onto each home game using the SCHOOL_CALENDAR lookup.
    venue_states = load_home_venue_states()
    cal_with_state = cal_df.merge(
        venue_states.rename(columns={"team_id": "team_id"}),
        on="team_id",
        how="left",
    )

    def is_summer_break(row) -> str:
        state = row["state_abbrev"]
        if pd.isna(state) or state not in SCHOOL_CALENDAR:
            return "Unknown"
        cal = SCHOOL_CALENDAR[state]
        m = row["month"]
        if cal["release_month"] <= m < cal["return_month"]:
            return "Summer Break"
        return "School Year"

    cal_with_state["school_period"] = cal_with_state.apply(is_summer_break, axis=1)
    cal_with_state = cal_with_state[cal_with_state["school_period"] != "Unknown"].copy()

    if not cal_with_state.empty:
        school_agg = (
            cal_with_state.groupby("school_period")["attendance"]
            .agg(avg="mean", std="std", n="count")
            .reset_index()
        )
        school_agg["avg"] = school_agg["avg"].round(0)
        school_agg["std"] = school_agg["std"].round(0).fillna(0)

        fig_school = px.bar(
            school_agg,
            x="school_period",
            y="avg",
            error_y="std",
            text="n",
            labels={"school_period": "", "avg": "Avg Attendance", "n": "Games"},
            color="school_period",
            color_discrete_map={
                "Summer Break": "#f5a623",
                "School Year":  "#3a9bd5",
            },
            height=320,
            category_orders={"school_period": ["School Year", "Summer Break"]},
        )
        fig_school.update_traces(textposition="outside", texttemplate="%{text} games")
        fig_school.update_layout(showlegend=False, margin={"t": 20, "b": 10})
        st.plotly_chart(fig_school, use_container_width=True)

        # Breakdown by tier (early vs late release states)
        cal_with_state["school_tier"] = cal_with_state["state_abbrev"].map(
            {s: v["tier"] for s, v in SCHOOL_CALENDAR.items()}
        )
        tier_agg = (
            cal_with_state.groupby(["school_tier", "school_period"])["attendance"]
            .agg(avg="mean", n="count")
            .reset_index()
        )
        tier_agg["avg"] = tier_agg["avg"].round(0)

        with st.expander("Breakdown by state tier"):
            fig_tier = px.bar(
                tier_agg,
                x="school_period",
                y="avg",
                color="school_tier",
                barmode="group",
                text="n",
                labels={
                    "school_period": "",
                    "avg": "Avg Attendance",
                    "school_tier": "State Tier",
                    "n": "Games",
                },
                height=320,
                category_orders={"school_period": ["School Year", "Summer Break"]},
            )
            fig_tier.update_traces(textposition="outside", texttemplate="%{text}g")
            fig_tier.update_layout(margin={"t": 20, "b": 10})
            st.plotly_chart(fig_tier, use_container_width=True)
    else:
        st.info("No state data available to compute school calendar periods.")


# ── TAB 4: Performance Effects ─────────────────────────────────────────────────
with tab_perf:

    # ── Section A: Win/loss streak ─────────────────────────────────────────────
    st.subheader("Attendance by team's win/loss streak entering the game")
    st.caption(
        "Positive streak = winning; negative = losing. "
        "Does a hot streak bring more fans to the ballpark, or does attendance lead performance?"
    )

    # Load and compute streaks (cached — expensive groupby+apply runs once)
    results_raw = load_team_results(game_types=selected_game_types)
    streaks_df  = compute_streaks(results_raw)

    # Filter to the same level/team selection as the rest of the page
    streaks_df = streaks_df[streaks_df["team_id"].isin(level_ids)].copy()
    if selected_team_name != "— All teams —":
        streaks_df = streaks_df[streaks_df["team_id"] == team_id].copy()

    # Keep only home games with attendance (these are the games fans attend)
    home_streaks = streaks_df[
        streaks_df["is_home"] & streaks_df["attendance"].notna()
    ].copy()
    home_streaks["attendance"] = pd.to_numeric(home_streaks["attendance"], errors="coerce")

    if not home_streaks.empty:
        # Bucket the pre-game streak:
        #   -5 or worse → "L5+",  -4 → "L4", …, -1 → "L1"
        #    0 (first game of season) → "Neutral"
        #   +1 → "W1", …, +5 or better → "W5+"
        STREAK_CAP = 5

        def streak_label(s: int) -> str:
            if s == 0:
                return "Neutral"
            elif s > 0:
                return f"W{min(s, STREAK_CAP)}+" if s >= STREAK_CAP else f"W{s}"
            else:
                return f"L{min(-s, STREAK_CAP)}+" if -s >= STREAK_CAP else f"L{-s}"

        home_streaks["streak_label"] = home_streaks["pre_game_streak"].apply(streak_label)

        # Natural sort order: L5+ … L1 · Neutral · W1 … W5+
        streak_order = (
            [f"L{STREAK_CAP}+"] +
            [f"L{i}" for i in range(STREAK_CAP - 1, 0, -1)] +
            ["Neutral"] +
            [f"W{i}" for i in range(1, STREAK_CAP)] +
            [f"W{STREAK_CAP}+"]
        )

        streak_agg = (
            home_streaks.groupby("streak_label")["attendance"]
            .agg(avg="mean", std="std", n="count")
            .reset_index()
        )
        streak_agg["avg"] = streak_agg["avg"].round(0)
        streak_agg["std"] = streak_agg["std"].round(0).fillna(0)
        streak_agg["_sort"] = streak_agg["streak_label"].map(
            {s: i for i, s in enumerate(streak_order)}
        )
        streak_agg = streak_agg.sort_values("_sort")

        # Color: winning streak = green gradient, losing = red gradient, neutral = grey
        def streak_color(label: str) -> str:
            if label == "Neutral":
                return "#aaaaaa"
            return "#2ecc71" if label.startswith("W") else "#e74c3c"

        streak_agg["bar_color"] = streak_agg["streak_label"].apply(streak_color)

        fig_streak = px.bar(
            streak_agg,
            x="streak_label",
            y="avg",
            error_y="std",
            text="n",
            labels={"streak_label": "Team Streak Entering Game", "avg": "Avg Attendance", "n": "Games"},
            color="bar_color",
            color_discrete_map="identity",
            category_orders={"streak_label": streak_order},
            height=400,
        )
        fig_streak.update_traces(textposition="outside", texttemplate="%{text} games")
        fig_streak.update_layout(showlegend=False, margin={"t": 30, "b": 20})
        st.plotly_chart(fig_streak, use_container_width=True)
    else:
        st.info("No streak data available for the selected filters.")

    st.divider()

    # ── Section B: Doubleheader comparison ────────────────────────────────────
    st.subheader("Doubleheader: game 1 vs game 2")
    st.caption(
        "When a team plays two games on the same day, does the second game draw fewer fans? "
        "Game order is determined by game_datetime within each (team, date) pair."
    )

    # Identify same-team same-date pairs (the reliable doubleheader signal).
    # doubleheader='Y' in the API is inconsistently tagged, so we detect pairs directly.
    dh_raw = load_all_games(game_types=selected_game_types)
    dh_raw = dh_raw[dh_raw["home_team_id"].isin(level_ids)].copy()
    if selected_team_name != "— All teams —":
        dh_raw = dh_raw[dh_raw["home_team_id"] == team_id].copy()

    dh_raw["game_date"] = pd.to_datetime(dh_raw["game_date"])
    dh_raw["attendance"] = pd.to_numeric(dh_raw["attendance"], errors="coerce")
    dh_raw = dh_raw.dropna(subset=["attendance"])

    # Count games per (home_team_id, game_date) — keep only dates with exactly 2
    pair_counts = (
        dh_raw.groupby(["home_team_id", "game_date"])["game_pk"]
        .count()
        .reset_index()
        .rename(columns={"game_pk": "games_on_day"})
    )
    dh_dates = pair_counts[pair_counts["games_on_day"] == 2][["home_team_id", "game_date"]]
    dh_games = dh_raw.merge(dh_dates, on=["home_team_id", "game_date"])

    if len(dh_games) >= 10:
        # Rank within each pair by game_pk (a reliable proxy for game order)
        dh_games["game_rank"] = (
            dh_games.groupby(["home_team_id", "game_date"])["game_pk"]
            .rank(method="first")
            .astype(int)
        )
        dh_games["game_label"] = dh_games["game_rank"].map({1: "Game 1", 2: "Game 2"})

        dh_agg = (
            dh_games.groupby("game_label")["attendance"]
            .agg(avg="mean", std="std", n="count")
            .reset_index()
        )
        dh_agg["avg"] = dh_agg["avg"].round(0)
        dh_agg["std"] = dh_agg["std"].round(0).fillna(0)

        fig_dh = px.bar(
            dh_agg,
            x="game_label",
            y="avg",
            error_y="std",
            text="n",
            labels={"game_label": "", "avg": "Avg Attendance", "n": "Games"},
            color="game_label",
            color_discrete_map={"Game 1": "#3a9bd5", "Game 2": "#e07b39"},
            height=320,
        )
        fig_dh.update_traces(textposition="outside", texttemplate="%{text} doubleheaders")
        fig_dh.update_layout(showlegend=False, margin={"t": 30, "b": 10})
        st.plotly_chart(fig_dh, use_container_width=True)

        # Show a per-pair scatter (single team only — otherwise too many points)
        if selected_team_name != "— All teams —":
            g1 = dh_games[dh_games["game_rank"] == 1][["home_team_id", "game_date", "attendance"]].rename(columns={"attendance": "game1_att"})
            g2 = dh_games[dh_games["game_rank"] == 2][["home_team_id", "game_date", "attendance"]].rename(columns={"attendance": "game2_att"})
            pairs_df = g1.merge(g2, on=["home_team_id", "game_date"])
            pairs_df["delta"] = pairs_df["game2_att"] - pairs_df["game1_att"]

            fig_pair = px.scatter(
                pairs_df,
                x="game1_att",
                y="game2_att",
                labels={"game1_att": "Game 1 Attendance", "game2_att": "Game 2 Attendance"},
                title="Each dot = one doubleheader. Below diagonal = game 2 drew less.",
                height=340,
                color_discrete_sequence=["#3a9bd5"],
            )
            # Diagonal reference line (game 1 == game 2)
            max_val = max(pairs_df[["game1_att", "game2_att"]].max())
            fig_pair.add_scatter(
                x=[0, max_val], y=[0, max_val],
                mode="lines",
                line={"dash": "dash", "color": "grey"},
                name="Equal",
                showlegend=True,
            )
            fig_pair.update_traces(marker_size=8, selector={"mode": "markers"})
            fig_pair.update_layout(margin={"t": 40, "b": 10})
            st.plotly_chart(fig_pair, use_container_width=True)
    else:
        st.info(
            f"Only {len(dh_games)} doubleheader games found for the selected filters "
            f"(need ≥ 10). Try expanding the level selection."
        )

    st.divider()

    # ── Section C: Win% at game time ──────────────────────────────────────────
    st.subheader("Attendance by team win% entering the game")
    st.caption(
        "A team's cumulative W/L record before each home game, bucketed into "
        "win% ranges. Does a winning record draw bigger crowds?"
    )

    # Reuse results_raw already loaded for the streak section above.
    # compute_win_pct is cached — if streaks_df was already computed from
    # the same results_raw, compute_win_pct also hits the cache immediately.
    win_pct_df = compute_win_pct(results_raw)

    # Filter to the same level/team selection
    win_pct_df = win_pct_df[win_pct_df["team_id"].isin(level_ids)].copy()
    if selected_team_name != "— All teams —":
        win_pct_df = win_pct_df[win_pct_df["team_id"] == team_id].copy()

    # Home games with known attendance and at least 10 games played (% is meaningful)
    home_wp = win_pct_df[
        win_pct_df["is_home"]
        & win_pct_df["attendance"].notna()
        & (win_pct_df["pre_game_games"] >= 10)
        & win_pct_df["pre_game_win_pct"].notna()
    ].copy()
    home_wp["attendance"] = pd.to_numeric(home_wp["attendance"], errors="coerce")
    home_wp = home_wp.dropna(subset=["attendance"])

    if not home_wp.empty:
        # Bucket win% into 10-point bands
        WP_BINS   = [0, 0.30, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 1.01]
        WP_LABELS = ["<.300", ".300-.399", ".400-.449", ".450-.499",
                     ".500-.549", ".550-.599", ".600-.699", ".700+"]

        home_wp["wp_bucket"] = pd.cut(
            home_wp["pre_game_win_pct"],
            bins=WP_BINS,
            labels=WP_LABELS,
            right=False,
        )

        wp_agg = (
            home_wp.groupby("wp_bucket", observed=True)["attendance"]
            .agg(avg="mean", std="std", n="count")
            .reset_index()
        )
        wp_agg["avg"] = wp_agg["avg"].round(0)
        wp_agg["std"] = wp_agg["std"].round(0).fillna(0)

        fig_wp = px.bar(
            wp_agg,
            x="wp_bucket",
            y="avg",
            error_y="std",
            text="n",
            labels={"wp_bucket": "Win% Entering Game", "avg": "Avg Attendance", "n": "Games"},
            color_discrete_sequence=["#3a9bd5"],
            category_orders={"wp_bucket": WP_LABELS},
            height=380,
        )
        fig_wp.update_traces(textposition="outside", texttemplate="%{text} games")
        fig_wp.update_layout(showlegend=False, margin={"t": 30, "b": 20})
        # Add .500 reference line
        if ".500-.549" in WP_LABELS:
            five_hundred_idx = WP_LABELS.index(".500-.549")
            fig_wp.add_vline(
                x=five_hundred_idx,
                line_dash="dash",
                line_color="grey",
                annotation_text=".500",
                annotation_position="top",
            )
        st.plotly_chart(fig_wp, use_container_width=True)
    else:
        st.info("Not enough data to compute win% buckets for the selected filters.")


# ── Cross-page navigation + footer ───────────────────────────────────────────
see_also([
    ("Attendance",       "pages/1_Attendance.py",       "baseline per-team trends"),
    ("Promotions",       "pages/2_Promotions.py",       "promo lift by day-of-week"),
    ("Opponents",        "pages/4_Opponents.py",        "which opponents move the needle"),
])
render_footer(scripts=["build_features"])
