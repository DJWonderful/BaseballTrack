"""Rehab assignment impact on attendance.

New Streamlit / plotly patterns introduced here:
  - fig.add_vrect()   → shaded vertical band on a chart (highlights a time window)
  - fig.add_vline()   → vertical reference line
  - Before/during/after window comparison (±14 days around a rehab window)
  - Player ranking by attendance lift, sorted by MLB debut (proxy for notoriety)
"""

# ── Path setup ────────────────────────────────────────────────────────────────
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils.db import query_df
from utils.filters import game_type_filter, game_type_sql
from utils.theme import SEASON_COLORS
from utils.footer import render_footer
from utils.navigation import see_also

st.set_page_config(page_title="Rehab | MiLB", page_icon="🏥", layout="wide")

LEVEL_ORDER   = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}
WINDOW_DAYS   = 14   # days before/after a rehab window used for baseline comparison


# ── Data loading ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def load_teams() -> pd.DataFrame:
    return query_df("""
        SELECT t.team_id, t.team_name, t.sport_id,
               COALESCE(sp.sport_name, 'Unknown') AS level,
               v.capacity
        FROM milb.teams t
        JOIN milb.venues v ON t.venue_id = v.venue_id
        LEFT JOIN milb.sports sp ON t.sport_id = sp.sport_id
        WHERE t.sport_id IN (11,12,13,14)
          AND v.capacity IS NOT NULL
        ORDER BY t.sport_id, t.team_name
    """)


@st.cache_data(ttl=600)
def load_games(game_types: tuple = ("R",)) -> pd.DataFrame:
    """All completed home games (with attendance) for the selected game types."""
    df = query_df(f"""
        SELECT game_pk, home_team_id AS team_id, game_date, season, attendance
        FROM milb.games
        WHERE abstract_game_state = 'Final'
          AND {game_type_sql(game_types)}
          AND attendance IS NOT NULL
          AND attendance > 0
          AND sport_id IN (11,12,13,14)
    """)
    df["game_date"] = pd.to_datetime(df["game_date"])
    return df


@st.cache_data(ttl=600)
def load_rehab_assignments() -> pd.DataFrame:
    """All rehab assignments with player info and resolved windows.

    mlb_debut_date is used as a notoriety proxy — earlier debut = longer career
    = player more likely to be recognized by fans.
    """
    df = query_df("""
        SELECT
            transaction_id,
            player_id,
            player_name,
            player_position,
            mlb_debut_date,
            is_mlb_veteran,
            to_team_id                                                              AS team_id,
            to_team_name                                                            AS team_name,
            transaction_date::date                                                  AS window_start,
            COALESCE(resolution_date, transaction_date + INTERVAL '30 days')::date AS window_end,
            description
        FROM milb.transactions
        WHERE is_rehab = TRUE
          AND to_team_id IS NOT NULL
          AND transaction_date IS NOT NULL
        ORDER BY transaction_date DESC
    """)
    df["window_start"]   = pd.to_datetime(df["window_start"])
    df["window_end"]     = pd.to_datetime(df["window_end"])
    df["mlb_debut_date"] = pd.to_datetime(df["mlb_debut_date"])
    df["window_days"]    = (df["window_end"] - df["window_start"]).dt.days
    return df


# ── Before / during / after lift computation ──────────────────────────────────
def compute_rehab_lift(rehab: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """For each rehab assignment, compute attendance before / during / after.

    Logic:
      - "before":  games at the same team in the WINDOW_DAYS days before window_start
      - "during":  games at the same team between window_start and window_end
      - "after":   games at the same team in the WINDOW_DAYS days after window_end
      - lift = during_avg - mean(before_avg, after_avg)

    Returns one row per rehab assignment with period averages and lift.
    """
    rows = []
    for _, r in rehab.iterrows():
        tg = games[games["team_id"] == r["team_id"]]
        if tg.empty:
            continue

        before = tg[(tg["game_date"] >= r["window_start"] - pd.Timedelta(days=WINDOW_DAYS))
                  & (tg["game_date"] <  r["window_start"])]
        during = tg[(tg["game_date"] >= r["window_start"])
                  & (tg["game_date"] <= r["window_end"])]
        after  = tg[(tg["game_date"] >  r["window_end"])
                  & (tg["game_date"] <= r["window_end"] + pd.Timedelta(days=WINDOW_DAYS))]

        if during.empty:
            continue    # no home games actually played during this window

        before_avg = before["attendance"].mean() if not before.empty else None
        during_avg = during["attendance"].mean()
        after_avg  = after["attendance"].mean()  if not after.empty  else None

        # Baseline = average of whatever before/after periods we have data for
        baseline_vals = [v for v in [before_avg, after_avg] if v is not None]
        baseline = sum(baseline_vals) / len(baseline_vals) if baseline_vals else None
        lift = (during_avg - baseline) if baseline is not None else None

        rows.append({
            "transaction_id": r["transaction_id"],
            "player_name":    r["player_name"],
            "player_position":r["player_position"],
            "mlb_debut_date": r["mlb_debut_date"],
            "is_mlb_veteran": r["is_mlb_veteran"],
            "team_id":        r["team_id"],
            "team_name":      r["team_name"],
            "window_start":   r["window_start"],
            "window_end":     r["window_end"],
            "window_days":    r["window_days"],
            "before_avg":     round(before_avg, 0) if before_avg else None,
            "during_avg":     round(during_avg, 0),
            "after_avg":      round(after_avg, 0)  if after_avg  else None,
            "baseline":       round(baseline, 0)   if baseline   else None,
            "lift":           round(lift, 0)        if lift       else None,
            "games_during":   len(during),
            "games_before":   len(before),
            "games_after":    len(after),
        })

    return pd.DataFrame(rows)


# ── Sidebar ───────────────────────────────────────────────────────────────────
teams_df  = load_teams()
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
        f"Before/after baseline = avg attendance in the {WINDOW_DAYS} days "
        "before and after each rehab window. "
        "Lift = during window avg minus that baseline."
    )


# ── Resolve selected team ─────────────────────────────────────────────────────
team_id   = None
team_row  = None
if selected_team_name != "— All teams —":
    mask = teams_df["team_name"] == selected_team_name
    if mask.any():
        team_row = teams_df[mask].iloc[0]
        team_id  = int(team_row["team_id"])


# ── Load data ─────────────────────────────────────────────────────────────────
games_df  = load_games(game_types=selected_game_types)
rehab_all = load_rehab_assignments()

# Filter both datasets to selected levels / team
level_ids = set(level_teams["team_id"])
games_df  = games_df[games_df["team_id"].isin(level_ids)].copy()
rehab_all = rehab_all[rehab_all["team_id"].isin(level_ids)].copy()

if team_id is not None:
    games_df  = games_df[games_df["team_id"] == team_id].copy()
    rehab_all = rehab_all[rehab_all["team_id"] == team_id].copy()


# ── Page header ───────────────────────────────────────────────────────────────
scope = selected_team_name if team_id else "All selected teams"
st.title("🏥 Rehab Assignment Impact")
st.caption(
    f"{len(rehab_all):,} rehab assignments · {scope}. "
    "Does hosting an MLB player on rehab boost attendance?"
)

if rehab_all.empty or games_df.empty:
    st.warning("No data for the selected filters.")
    st.stop()


# ── Compute lift for all matching assignments ─────────────────────────────────
# This loops over every rehab window, so cache the result inside a memo pattern:
# we hash on the team filter to avoid recomputing when nothing changed.
@st.cache_data(ttl=600)
def cached_lift(team_filter, level_filter):
    r = rehab_all if team_id is None else rehab_all[rehab_all["team_id"] == team_filter]
    g = games_df
    return compute_rehab_lift(r, g)

lift_df = cached_lift(team_id, tuple(sorted(selected_levels)))

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_rank, tab_timeline, tab_notoriety = st.tabs(
    ["Player Rankings", "Team Timeline", "Notoriety Effect"]
)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Player Rankings
# ══════════════════════════════════════════════════════════════════════════════
with tab_rank:
    st.subheader("Which players boosted attendance the most?")
    st.caption(
        "Each row = one rehab stint. A player with multiple stints appears multiple times. "
        f"Lift = attendance during window minus average of the {WINDOW_DAYS} days before/after."
    )

    if lift_df.empty:
        st.info("Not enough game data to compute lift for any rehab windows.")
    else:
        # Sort by lift descending — top boosters first
        ranked = lift_df.dropna(subset=["lift"]).sort_values("lift", ascending=False)

        # ── Metric cards ──────────────────────────────────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Rehab windows w/ data",  f"{len(ranked):,}")
        c2.metric("Positive lift",
                  f"{(ranked['lift'] > 0).sum():,}",
                  help="Windows where attendance was above baseline")
        c3.metric("Avg lift",
                  f"{ranked['lift'].mean():+,.0f}",
                  help="Across all windows with enough surrounding games")
        c4.metric("Best single window",
                  f"+{ranked['lift'].max():,.0f}" if ranked['lift'].max() > 0 else "—",
                  help=f"{ranked.iloc[0]['player_name']} at {ranked.iloc[0]['team_name']}")

        st.divider()

        # ── Bar chart: top 25 by lift ─────────────────────────────────────────
        top25 = ranked.head(25).sort_values("lift", ascending=True)
        top25["label"] = top25["player_name"] + " @ " + top25["team_name"]
        top25["color"] = top25["lift"].apply(lambda x: "#1a9850" if x >= 0 else "#d73027")

        fig_rank = px.bar(
            top25,
            x="lift",
            y="label",
            orientation="h",
            text=top25["lift"].apply(lambda x: f"{x:+,.0f}"),
            labels={"lift": "Attendance Lift", "label": "Player @ Team"},
            color="color",
            color_discrete_map="identity",
            hover_data={"window_start": True, "window_end": True,
                        "during_avg": True, "baseline": True, "games_during": True},
            height=max(350, len(top25) * 28),
        )
        fig_rank.update_traces(textposition="outside")
        fig_rank.add_vline(x=0, line_dash="dash", line_color="gray", line_width=1)
        fig_rank.update_layout(
            showlegend=False,
            xaxis_title="Attendance Lift vs ±14-day Baseline",
            yaxis_title=None,
            margin={"t": 10, "b": 20, "l": 10},
        )
        st.plotly_chart(fig_rank, use_container_width=True)

        st.divider()

        # ── Full sortable table ───────────────────────────────────────────────
        st.subheader("All rehab windows")
        tbl = ranked[[
            "player_name", "player_position", "team_name",
            "window_start", "window_end", "games_during",
            "before_avg", "during_avg", "after_avg", "lift",
        ]].rename(columns={
            "player_name":    "Player",
            "player_position":"Pos",
            "team_name":      "Team",
            "window_start":   "Start",
            "window_end":     "End",
            "games_during":   "Games",
            "before_avg":     "Before Avg",
            "during_avg":     "During Avg",
            "after_avg":      "After Avg",
            "lift":           "Lift",
        })

        def color_lift(val):
            if pd.isna(val): return ""
            return "color: #1a9850" if val > 0 else "color: #d73027"

        styled = (
            tbl.style
            .map(color_lift, subset=["Lift"])
            .format({
                "Before Avg":  "{:,.0f}",
                "During Avg":  "{:,.0f}",
                "After Avg":   "{:,.0f}",
                "Lift":        "{:+,.0f}",
            }, na_rep="—")
            .format({"Start": "{:%Y-%m-%d}", "End": "{:%Y-%m-%d}"})
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Team Timeline
# ══════════════════════════════════════════════════════════════════════════════
with tab_timeline:

    if team_id is None:
        st.info("Select a specific team in the sidebar to see the attendance timeline.")
    else:
        st.subheader(f"{selected_team_name} — attendance timeline with rehab windows")
        st.caption(
            "Each dot = one home game. Shaded bands = active rehab assignment windows. "
            "Hover a band or dot to see player name and attendance."
        )

        tg = games_df[games_df["team_id"] == team_id].sort_values("game_date").copy()
        tr = rehab_all[rehab_all["team_id"] == team_id].copy()

        if tg.empty:
            st.warning(f"No game data for {selected_team_name}.")
        else:
            tg["season"] = tg["season"].astype(str)

            # Build the scatter plot first
            fig_tl = px.scatter(
                tg,
                x="game_date",
                y="attendance",
                color="season",
                labels={"attendance": "Attendance", "game_date": "Date", "season": "Season"},
                color_discrete_map=SEASON_COLORS,
                category_orders={"season": sorted(tg["season"].unique())},
                height=450,
            )
            fig_tl.update_traces(marker_size=7, opacity=0.85)

            # ── add_vrect: shaded vertical bands for each rehab window ────────
            # This is the key new pattern: you can annotate any plotly figure
            # with rectangular overlays. x0/x1 are the left/right edges (dates here),
            # y0/y1 are in "paper" coordinates (0=bottom, 1=top of plot area).
            for _, rw in tr.iterrows():
                fig_tl.add_vrect(
                    x0=rw["window_start"],
                    x1=rw["window_end"],
                    fillcolor="rgba(255, 200, 50, 0.18)",  # translucent yellow
                    layer="below",   # draw behind the scatter dots
                    line_width=1,
                    line_color="rgba(255, 180, 0, 0.5)",
                    annotation_text=rw["player_name"].split()[-1],  # last name only
                    annotation_position="top left",
                    annotation_font_size=9,
                    annotation_font_color="#b8860b",
                )

            fig_tl.update_layout(
                xaxis_title=None,
                yaxis_title="Attendance",
                legend_title="Season",
                margin={"t": 30, "b": 20},
            )
            st.plotly_chart(fig_tl, use_container_width=True)

            # ── Per-window summary below the chart ────────────────────────────
            if not lift_df.empty:
                team_lift = lift_df[lift_df["team_id"] == team_id].sort_values(
                    "window_start"
                )
                if not team_lift.empty:
                    st.subheader("Rehab window summary")
                    tbl2 = team_lift[[
                        "player_name", "window_start", "window_end",
                        "games_during", "before_avg", "during_avg", "after_avg", "lift",
                    ]].rename(columns={
                        "player_name":  "Player",
                        "window_start": "Start",
                        "window_end":   "End",
                        "games_during": "Games",
                        "before_avg":   "Before",
                        "during_avg":   "During",
                        "after_avg":    "After",
                        "lift":         "Lift",
                    })

                    def clift(v):
                        if pd.isna(v): return ""
                        return "color: #1a9850" if v > 0 else "color: #d73027"

                    st.dataframe(
                        tbl2.style
                        .map(clift, subset=["Lift"])
                        .format({
                            "Before": "{:,.0f}", "During": "{:,.0f}",
                            "After":  "{:,.0f}", "Lift":   "{:+,.0f}",
                        }, na_rep="—")
                        .format({"Start": "{:%Y-%m-%d}", "End": "{:%Y-%m-%d}"}),
                        use_container_width=True,
                        hide_index=True,
                    )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Notoriety Effect
# ══════════════════════════════════════════════════════════════════════════════
with tab_notoriety:
    st.subheader("Does player notoriety drive the lift?")
    st.caption(
        "MLB debut year used as a notoriety proxy: the earlier the debut, "
        "the longer the player's career, the more fans likely recognize them. "
        "Rookies on rehab draws little extra interest; veterans draw fans."
    )

    if lift_df.empty or lift_df["lift"].isna().all():
        st.info("Not enough data to compute notoriety analysis.")
    else:
        plot_n = lift_df.dropna(subset=["lift", "mlb_debut_date"]).copy()

        if plot_n.empty:
            st.info("No players have both lift data and a recorded MLB debut date.")
        else:
            plot_n["debut_year"] = plot_n["mlb_debut_date"].dt.year
            plot_n["years_in_mlb"] = pd.Timestamp.now().year - plot_n["debut_year"]
            plot_n["season"] = plot_n["window_start"].dt.year.astype(str)

            # Scatter: years in MLB vs lift, colored by season
            fig_not = px.scatter(
                plot_n.sort_values("years_in_mlb"),
                x="years_in_mlb",
                y="lift",
                color="season",
                hover_data={
                    "player_name":    True,
                    "team_name":      True,
                    "during_avg":     True,
                    "games_during":   True,
                    "years_in_mlb":   True,
                    "season":         False,
                },
                labels={
                    "years_in_mlb": "Years in MLB at time of rehab",
                    "lift":         "Attendance Lift",
                    "player_name":  "Player",
                    "team_name":    "Team",
                    "during_avg":   "Avg During",
                    "games_during": "Games",
                },
                color_discrete_map=SEASON_COLORS,
                category_orders={"season": sorted(plot_n["season"].unique())},
                height=400,
            )
            fig_not.update_traces(marker_size=9, opacity=0.8)
            # Zero lift reference line
            fig_not.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1)
            fig_not.update_layout(
                xaxis_title="Years in MLB (at time of rehab assignment)",
                yaxis_title="Attendance Lift vs ±14-day Baseline",
                legend_title="Season",
                margin={"t": 10, "b": 20},
            )
            st.plotly_chart(fig_not, use_container_width=True)

            # ── Avg lift by veteran status ─────────────────────────────────────
            st.subheader("Veteran vs non-veteran")
            vet = (
                plot_n.groupby("is_mlb_veteran")["lift"]
                .agg(avg_lift="mean", n="count", std="std")
                .reset_index()
            )
            vet["label"] = vet["is_mlb_veteran"].map(
                {True: "MLB Veteran", False: "Non-Veteran / Prospect"}
            )
            vet["avg_lift"] = vet["avg_lift"].round(0)
            vet["std"]      = vet["std"].round(0).fillna(0)
            vet["color"]    = vet["avg_lift"].apply(
                lambda x: "#1a9850" if x >= 0 else "#d73027"
            )

            fig_vet = px.bar(
                vet,
                x="label",
                y="avg_lift",
                error_y="std",
                text="n",
                color="color",
                color_discrete_map="identity",
                labels={"label": "", "avg_lift": "Avg Attendance Lift", "n": "Windows"},
                height=320,
            )
            fig_vet.update_traces(textposition="outside", texttemplate="%{text} windows")
            fig_vet.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1)
            fig_vet.update_layout(
                showlegend=False,
                margin={"t": 30, "b": 10},
            )
            st.plotly_chart(fig_vet, use_container_width=True)


# ── Cross-page navigation + footer ───────────────────────────────────────────
see_also([
    ("Attendance",  "pages/1_Attendance.py",  "rehab games are flagged on the scatter there too"),
    ("Promotions",  "pages/2_Promotions.py",  "check whether rehab nights coincided with promos"),
])
render_footer(scripts=["build_features"])
