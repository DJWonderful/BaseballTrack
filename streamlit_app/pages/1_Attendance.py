"""Attendance baseline profile — league overview and per-team deep dive.

New Streamlit patterns introduced here (read the comments!):
  - st.tabs()          → tabbed layout so one page does two things
  - st.selectbox()     → dropdown picker
  - st.metric(delta=)  → number card with a change arrow
  - px.bar()           → bar charts
  - px.line()          → line / trend charts
  - px.scatter()       → individual game dots
"""

# ── Path setup (same boilerplate on every page) ───────────────────────────────
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.db import query_df
from utils.filters import game_type_filter, game_type_sql
from utils.theme import SEASON_COLORS
from utils.footer import render_footer
from utils.navigation import see_also

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Attendance | MiLB", page_icon="📈", layout="wide")

LEVEL_ORDER = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}
DOW_ORDER   = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
MONTH_ABBR  = {4:"Apr",5:"May",6:"Jun",7:"Jul",8:"Aug",9:"Sep",10:"Oct"}

# ── Data loading ──────────────────────────────────────────────────────────────
# These two functions load once and cache; widgets changing won't re-query the DB.

@st.cache_data(ttl=600)
def load_teams() -> pd.DataFrame:
    return query_df("""
        SELECT t.team_id, t.team_name, t.sport_id,
               COALESCE(sp.sport_name, 'Unknown') AS level,
               v.venue_name, v.capacity
        FROM milb.teams t
        JOIN milb.venues v ON t.venue_id = v.venue_id
        LEFT JOIN milb.sports sp ON t.sport_id = sp.sport_id
        WHERE t.sport_id IN (11,12,13,14)
          AND v.capacity IS NOT NULL
        ORDER BY t.sport_id, t.team_name
    """)


@st.cache_data(ttl=600)
def load_rehab_players(team_id: int) -> pd.DataFrame:
    """Rehab windows for one team, with player names.

    MLB Stats API stores resolution_date = transaction_date on rehab rows -- it
    marks the *assignment* event, not the stint end. Real stint end is inferred
    from the player's next transaction anywhere (sent to another affiliate,
    activated from IL, etc). Falls back to +20 days (rough MiLB rehab cap) when
    no next transaction exists.

    Returns: player_name, window_start, window_end (dates)
    """
    return query_df("""
        WITH rehab_starts AS (
            SELECT DISTINCT
                player_id,
                player_name,
                transaction_date::date AS window_start
            FROM milb.transactions
            WHERE is_rehab = TRUE
              AND to_team_id = :tid
              AND transaction_date IS NOT NULL
        )
        SELECT
            r.player_name,
            r.window_start,
            GREATEST(
                r.window_start,
                COALESCE(
                    (SELECT MIN(t2.transaction_date::date) - 1
                     FROM milb.transactions t2
                     WHERE t2.player_id = r.player_id
                       AND t2.transaction_date::date > r.window_start),
                    r.window_start + 20
                )
            ) AS window_end
        FROM rehab_starts r
        ORDER BY r.window_start
    """, {"tid": team_id})


@st.cache_data(ttl=600)
def load_fireworks_games(team_id: int) -> set:
    """game_pk set for one team's home games that had fireworks."""
    df = query_df("""
        SELECT DISTINCT g.game_pk
        FROM milb.games g
        JOIN milb.game_promotions gp ON g.game_pk = gp.game_pk
        WHERE g.home_team_id = :tid
          AND gp.is_fireworks = TRUE
    """, {"tid": team_id})
    return set(df["game_pk"].tolist())


@st.cache_data(ttl=600)
def load_games(game_types: tuple = ("R",)) -> pd.DataFrame:
    """Every completed home game (with attendance) for the selected game types.

    Left-joins game_features for the start_time_bucket (local-time-based,
    replaces the unreliable day_night). Regular-season rows get a bucket;
    postseason rows fall back to NULL since game_features is R-only.
    """
    df = query_df(f"""
        SELECT g.game_pk, g.home_team_id AS team_id, g.game_date, g.season,
               g.attendance, g.day_night, g.doubleheader,
               f.start_time_bucket
          FROM milb.games g
          LEFT JOIN milb.game_features f ON f.game_pk = g.game_pk
         WHERE g.abstract_game_state = 'Final'
           AND {game_type_sql(game_types, 'g.game_type')}
           AND g.attendance IS NOT NULL
           AND g.attendance > 0
           AND g.sport_id IN (11,12,13,14)
    """)
    # Convert game_date once here so all downstream code can use .dt accessors
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["month"]    = df["game_date"].dt.month
    df["dow_name"] = df["game_date"].dt.day_name()   # "Monday", "Tuesday" …
    return df


# ── Sidebar ───────────────────────────────────────────────────────────────────
teams_df = load_teams()

# Map sport_id → friendly label once
teams_df["level_label"] = teams_df["sport_id"].map(LEVEL_ORDER).fillna(teams_df["level"])

with st.sidebar:
    st.header("Filters")

    selected_levels = st.multiselect(
        "Level",
        options=list(LEVEL_ORDER.values()),
        default=list(LEVEL_ORDER.values()),
    )

    # Build team list restricted to the selected levels
    level_teams = (
        teams_df[teams_df["level_label"].isin(selected_levels)]
        .sort_values(["level_label","team_name"])
    )

    # st.selectbox — a single-choice dropdown.
    # We prepend "— All teams —" so the user can stay in league-wide view.
    team_options = ["— All teams —"] + level_teams["team_name"].tolist()
    _default_idx = team_options.index("Binghamton Rumble Ponies") if "Binghamton Rumble Ponies" in team_options else 0
    selected_team_name = st.selectbox("Team (for deep-dive tab)", options=team_options, index=_default_idx)

    st.divider()
    selected_game_types = game_type_filter()


# ── Resolve selected team ─────────────────────────────────────────────────────
team_id = None
team_row = None
if selected_team_name != "— All teams —":
    mask = teams_df["team_name"] == selected_team_name
    if mask.any():
        team_row = teams_df[mask].iloc[0]
        team_id  = int(team_row["team_id"])


# ── Load and filter games ─────────────────────────────────────────────────────
games_df = load_games(game_types=selected_game_types)

# Which team_ids are in the selected levels?
level_ids = set(level_teams["team_id"])
games_df  = games_df[games_df["team_id"].isin(level_ids)].copy()

# Join team name + level so charts can use them
games_df = games_df.merge(
    teams_df[["team_id","team_name","level_label","capacity"]],
    on="team_id", how="left",
)


# ── Page title ────────────────────────────────────────────────────────────────
st.title("📈 Attendance Baseline")
st.caption("League-wide trends and per-team breakdowns for the 2023–2025 seasons.")

# ── Tabs ──────────────────────────────────────────────────────────────────────
# st.tabs() returns a list of context managers — one per tab label.
# Code inside `with tab1:` only renders when that tab is active.
tab1, tab2 = st.tabs(["League Overview", "Team Deep-Dive"])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — League Overview
# ═══════════════════════════════════════════════════════════════════════════════
with tab1:

    # ── Average attendance by season & level ──────────────────────────────────
    st.subheader("Average home attendance by season")

    league_avg = (
        games_df
        .groupby(["season","level_label"])["attendance"]
        .mean()
        .round(0)
        .reset_index()
        .rename(columns={"attendance":"avg_attendance"})
    )

    # px.bar with barmode="group" puts bars side-by-side instead of stacked.
    # category_orders forces the legend into Triple-A → Single-A order.
    fig_bar = px.bar(
        league_avg,
        x="season",
        y="avg_attendance",
        color="level_label",
        barmode="group",
        text_auto=True,
        category_orders={"level_label": list(LEVEL_ORDER.values())},
        labels={"avg_attendance":"Avg Attendance","season":"Season","level_label":"Level"},
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig_bar.update_traces(textposition="outside")
    fig_bar.update_layout(
        xaxis=dict(tickmode="array", tickvals=league_avg["season"].unique()),
        yaxis_title="Avg Attendance",
        legend_title="Level",
        margin={"t":20,"b":20},
        height=380,
    )
    st.plotly_chart(fig_bar, use_container_width=True)

    st.divider()

    # ── Year-over-year monthly trend ──────────────────────────────────────────
    st.subheader("Year-over-year — average attendance by month")
    st.caption("One line per season. Hover for exact values.")

    yoy_level = st.radio(
        "Level",
        options=["All levels"] + list(LEVEL_ORDER.values()),
        horizontal=True,
        key="yoy_level",
    )

    yoy_src = games_df if yoy_level == "All levels" else games_df[games_df["level_label"] == yoy_level]

    if yoy_src.empty:
        st.info("No games in the current filter.")
    else:
        yoy = (
            yoy_src
            .groupby(["season", "month"])
            .agg(avg_attendance=("attendance", "mean"), games=("game_pk", "count"))
            .reset_index()
        )
        yoy["avg_attendance"] = yoy["avg_attendance"].round(0)
        yoy = yoy.sort_values("month")
        yoy["month_name"] = yoy["month"].map(MONTH_ABBR)
        yoy["season"] = yoy["season"].astype(str)

        fig_yoy = px.line(
            yoy,
            x="month_name",
            y="avg_attendance",
            color="season",
            markers=True,
            category_orders={
                "month_name": list(MONTH_ABBR.values()),
                "season": sorted(yoy["season"].unique()),
            },
            labels={"avg_attendance": "Avg Attendance", "month_name": "", "season": "Season"},
            color_discrete_map=SEASON_COLORS,
            hover_data={"games": True, "month_name": False},
            height=360,
        )
        fig_yoy.update_traces(line=dict(width=3), marker=dict(size=9))
        fig_yoy.update_layout(
            legend_title="Season",
            margin={"t": 10, "b": 20},
            hovermode="x unified",
        )
        st.plotly_chart(fig_yoy, use_container_width=True)

    st.divider()

    # ── Team leaderboard ──────────────────────────────────────────────────────
    st.subheader("Team season averages")

    # Build one row per team with a column per season + trend
    team_seasons = (
        games_df
        .groupby(["team_id","team_name","level_label","capacity","season"])["attendance"]
        .mean()
        .round(0)
        .reset_index()
    )

    # Pivot so each season is its own column
    pivot = team_seasons.pivot_table(
        index=["team_id","team_name","level_label","capacity"],
        columns="season",
        values="attendance",
    ).reset_index()
    pivot.columns.name = None

    # Rename season columns to "2023 avg" etc.
    seasons = sorted([c for c in pivot.columns if isinstance(c, (int, float)) and c > 2000])
    season_rename = {s: f"{int(s)} avg" for s in seasons}
    pivot = pivot.rename(columns=season_rename)

    # Trend: first→last season %
    first_col, last_col = f"{int(seasons[0])} avg", f"{int(seasons[-1])} avg"
    if first_col != last_col:
        pivot["trend %"] = (
            (pivot[last_col] - pivot[first_col]) / pivot[first_col] * 100
        ).round(1)
    else:
        pivot["trend %"] = None

    # Capacity utilization on the most recent season
    pivot["cap util %"] = (pivot[last_col] / pivot["capacity"] * 100).round(1)

    display = pivot[
        ["team_name","level_label"] + list(season_rename.values()) + ["trend %","cap util %"]
    ].rename(columns={"team_name":"Team","level_label":"Level"})

    # Color the trend % column: negative = red, positive = green.
    # st.dataframe accepts a Styler object.
    def color_trend(val):
        if pd.isna(val):       return ""
        if val > 0:            return "color: #1a9850"
        if val < 0:            return "color: #d73027"
        return ""

    styled = (
        display.sort_values("trend %", ascending=False)
        .style
        .map(color_trend, subset=["trend %"])
        .format({c: "{:,.0f}" for c in season_rename.values()}, na_rep="—")
        .format({"trend %": "{:+.1f}%", "cap util %": "{:.1f}%"}, na_rep="—")
    )

    st.dataframe(styled, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Team Deep-Dive
# ═══════════════════════════════════════════════════════════════════════════════
with tab2:

    if team_id is None:
        st.info("Select a specific team from the sidebar to see the deep-dive charts.")
        st.stop()   # st.stop() halts rendering for this tab — nothing below runs

    tg = games_df[games_df["team_id"] == team_id].copy()

    if tg.empty:
        st.warning(f"No game data found for {selected_team_name}.")
        st.stop()

    st.subheader(f"{selected_team_name} — {team_row['level_label']}")
    st.caption(f"Venue: {team_row['venue_name']}  |  Capacity: {int(team_row['capacity']):,}")

    # ── Season metric cards ───────────────────────────────────────────────────
    # One card per season showing avg attendance and change from prior year.
    season_avgs = (
        tg.groupby("season")["attendance"].mean().round(0).astype(int)
    )
    all_seasons = sorted(season_avgs.index.tolist())

    # st.columns(N) splits the row into N equal-width columns
    cols = st.columns(len(all_seasons))
    for i, s in enumerate(all_seasons):
        avg = int(season_avgs[s])
        cap = int(team_row["capacity"])
        util = f"  ({avg/cap*100:.0f}% capacity)"
        prior = season_avgs.get(all_seasons[i-1]) if i > 0 else None
        delta = int(avg - prior) if prior is not None else None
        # st.metric delta= shows a green↑ or red↓ arrow automatically
        cols[i].metric(
            label=f"{s} avg",
            value=f"{avg:,}",
            delta=f"{delta:+,}" if delta is not None else None,
            help=util,
        )

    st.divider()

    # ── Per-game attendance scatter ───────────────────────────────────────────
    st.subheader("Individual game attendance")
    st.caption("Each dot is one home game. Color = season.")

    # ── Flag rehab and fireworks games ────────────────────────────────────────
    rehab_df = load_rehab_players(team_id)
    fw_games = load_fireworks_games(team_id)

    plot_tg = tg.sort_values("game_date").copy()
    plot_tg["rehab_players"] = ""
    plot_tg["is_fireworks"] = plot_tg["game_pk"].isin(fw_games)

    if not rehab_df.empty:
        rehab_df["window_start"] = pd.to_datetime(rehab_df["window_start"])
        rehab_df["window_end"]   = pd.to_datetime(rehab_df["window_end"])

        def players_on_date(game_date):
            mask = (rehab_df["window_start"] <= game_date) & (rehab_df["window_end"] >= game_date)
            return ", ".join(rehab_df.loc[mask, "player_name"].tolist())

        plot_tg["rehab_players"] = plot_tg["game_date"].apply(players_on_date)

    # Rehab duration now reads off a shaded band (add_vrect loop below), so
    # the symbol dimension collapses to Fireworks vs Normal -- tidier legend.
    plot_tg["game_type_label"] = plot_tg["is_fireworks"].map(
        {True: "Fireworks", False: "Normal"}
    )
    plot_tg["fireworks_flag"] = plot_tg["is_fireworks"].map({True: "Yes", False: "No"})
    plot_tg["game_date_str"] = plot_tg["game_date"].dt.strftime("%a %b %d, %Y")

    # Casting season to str keeps plotly colors categorical (3 bright hues)
    # instead of a continuous gradient.
    plot_tg["season"] = plot_tg["season"].astype(str)

    label_order = [l for l in ["Normal", "Fireworks"]
                   if l in plot_tg["game_type_label"].unique()]

    fig_scatter = px.scatter(
        plot_tg,
        x="game_date",
        y="attendance",
        color="season",
        symbol="game_type_label",
        symbol_map={
            "Normal":    "circle",
            "Fireworks": "star",
        },
        hover_data={
            "game_date_str":   True,
            "day_night":       True,
            "doubleheader":    True,
            "rehab_players":   True,
            "fireworks_flag":  True,
            "game_type_label": False,
            "is_fireworks":    False,
            "game_date":       False,
        },
        labels={
            "attendance":     "Attendance",
            "game_date_str":  "Date",
            "season":         "Season",
            "rehab_players":  "Rehab",
            "fireworks_flag": "Fireworks",
            "day_night":      "D/N",
            "doubleheader":   "DH",
        },
        color_discrete_map=SEASON_COLORS,
        category_orders={
            "season": sorted(plot_tg["season"].unique()),
            "game_type_label": label_order,
        },
        height=380,
    )

    # Collapse off-season dead space so Apr-Sep games get the full chart width.
    # Bounds derived from actual game dates to survive postseason games in Oct.
    season_bounds = (
        plot_tg.groupby("season")["game_date"].agg(["min", "max"]).sort_index()
    )
    seasons_sorted = season_bounds.index.tolist()
    rangebreaks = []
    for i in range(len(seasons_sorted) - 1):
        gap_start = season_bounds.loc[seasons_sorted[i], "max"] + pd.Timedelta(days=2)
        gap_end   = season_bounds.loc[seasons_sorted[i + 1], "min"] - pd.Timedelta(days=2)
        if gap_start < gap_end:
            rangebreaks.append(dict(
                bounds=[gap_start.strftime("%Y-%m-%d"), gap_end.strftime("%Y-%m-%d")],
            ))
    fig_scatter.update_xaxes(
        rangebreaks=rangebreaks,
        dtick="M1",
        tickformat="%b<br>%Y",
    )

    # Rehab windows as amber bands with a dotted border -- amber reads on
    # both Streamlit themes, and the border keeps short stints visible even
    # when the fill is subtle. Overlapping stints stack opacity.
    if not rehab_df.empty:
        for _, r in rehab_df.iterrows():
            fig_scatter.add_vrect(
                x0=r["window_start"], x1=r["window_end"],
                fillcolor="#ffa726", opacity=0.22,
                line_color="#ffa726", line_width=1, line_dash="dot",
                layer="below",
            )

    for trace in fig_scatter.data:
        sym = trace.marker.symbol or ""
        if "star" in sym:
            trace.marker.size = 12
            trace.marker.line.width = 1
        else:
            trace.marker.size = 8
    fig_scatter.update_traces(opacity=0.85)
    fig_scatter.update_layout(
        xaxis_title=None,
        margin={"t": 10, "b": 30},
        legend_title="Season / Type",
    )
    st.plotly_chart(fig_scatter, use_container_width=True)

    fw_count = int(plot_tg["is_fireworks"].sum())
    rehab_games = int((plot_tg["rehab_players"] != "").sum())
    rehab_stints = len(rehab_df) if not rehab_df.empty else 0
    legend_parts = [f"★ = fireworks ({fw_count} games)"]
    if rehab_stints > 0:
        legend_parts.append(
            f"shaded band = rehab window ({rehab_stints} stint"
            f"{'' if rehab_stints == 1 else 's'}, {rehab_games} games during rehab)"
        )
    legend_parts.append("● = normal")
    st.caption(
        "  •  ".join(legend_parts)
        + ". Hover shows day-of-week + rehab player(s)."
    )

    st.divider()

    # ── Day-of-week and Monthly seasonality (side by side) ───────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Day-of-week")
        dow_avg = (
            tg.groupby("dow_name")["attendance"]
            .mean().round(0).reset_index()
            .rename(columns={"attendance":"avg_attendance"})
        )
        # category_orders forces the days into Mon→Sun sequence
        fig_dow = px.bar(
            dow_avg,
            x="dow_name",
            y="avg_attendance",
            text_auto=True,
            category_orders={"dow_name": DOW_ORDER},
            labels={"dow_name":"","avg_attendance":"Avg Attendance"},
            color="avg_attendance",
            color_continuous_scale="Blues",
            height=320,
        )
        fig_dow.update_traces(textposition="outside")
        fig_dow.update_layout(
            showlegend=False,
            coloraxis_showscale=False,
            margin={"t":10,"b":10},
        )
        st.plotly_chart(fig_dow, use_container_width=True)

    with col_right:
        st.subheader("Month of season")
        month_avg = (
            tg.groupby("month")["attendance"]
            .mean().round(0).reset_index()
            .rename(columns={"attendance":"avg_attendance"})
        )
        month_avg["month_name"] = month_avg["month"].map(MONTH_ABBR)
        fig_month = px.bar(
            month_avg,
            x="month_name",
            y="avg_attendance",
            text_auto=True,
            # Sort by the numeric month so Apr→Oct stays in order
            category_orders={"month_name": list(MONTH_ABBR.values())},
            labels={"month_name":"","avg_attendance":"Avg Attendance"},
            color="avg_attendance",
            color_continuous_scale="Oranges",
            height=320,
        )
        fig_month.update_traces(textposition="outside")
        fig_month.update_layout(
            showlegend=False,
            coloraxis_showscale=False,
            margin={"t":10,"b":10},
        )
        st.plotly_chart(fig_month, use_container_width=True)

    st.divider()

    # ── Start-time buckets ────────────────────────────────────────────────────
    # Replaces the legacy Day vs Night cut (games.day_night is ~1.5% wrong).
    # Buckets are venue-local.
    st.subheader("Attendance by start-time bucket")
    bucket_order = ["morning", "noon", "matinee", "early_evening", "evening", "late"]
    bucket_label = {
        "morning": "Morning (<11am)", "noon": "Noon (11-1pm)",
        "matinee": "Matinee (1-4pm)", "early_evening": "Early eve (4-6pm)",
        "evening": "Evening (6-8pm)", "late": "Late (8pm+)",
    }
    tb = tg[tg["start_time_bucket"].notna()]
    if tb.empty:
        st.caption("No bucket data for this team (older seasons or venues missing timezones).")
    else:
        b_avg = (
            tb.groupby("start_time_bucket")
              .agg(avg_att=("attendance", "mean"), games=("game_pk", "count"))
              .round(0).astype(int).reset_index()
        )
        b_avg["order"] = b_avg["start_time_bucket"].apply(
            lambda x: bucket_order.index(x) if x in bucket_order else 99
        )
        b_avg = b_avg.sort_values("order").drop(columns="order")
        cols = st.columns(len(b_avg))
        for i, row in b_avg.iterrows():
            cols[i].metric(
                label=f"{bucket_label.get(row['start_time_bucket'], row['start_time_bucket'])} "
                      f"({row['games']})",
                value=f"{row['avg_att']:,}",
            )
        st.caption(
            "Buckets come from venue-local game_datetime."
        )


# ── Cross-page navigation + footer ───────────────────────────────────────────
see_also([
    ("Promotions",       "pages/2_Promotions.py",      "what promotions drove each game"),
    ("Weather",          "pages/3_Weather.py",         "how weather shaped the scatter you're looking at"),
    ("Peer Playbook",    "pages/12_Peer_Playbook.py",  "what successful small-market peers do differently"),
    ("Rehab Assignments","pages/5_Rehab_Assignments.py","deep-dive on rehab-game attendance"),
])
render_footer(scripts=["build_features"])
