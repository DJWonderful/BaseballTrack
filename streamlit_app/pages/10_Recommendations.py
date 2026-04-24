"""Analytics recommendations dashboard.

Brings together promo lift analysis, peer clustering, XGBoost predictions,
and scheduling patterns into actionable, per-team recommendations.

Tabs:
  1. Promotion ROI     - Marginal lift per promo type with CIs
  2. Peer Comparison   - Selected team vs cluster peers
  3. What-If Simulator - Toggle promos on/off to see predicted attendance
  4. Recommendations   - Prioritized actionable items per team
  5. Model Performance - Actual vs predicted, residuals, feature importance
"""

# -- Path setup ---------------------------------------------------------------
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils.db import query_df, execute, is_read_only
from utils.filters import game_type_filter, game_type_sql, operator_filter
from utils.theme import (
    SEASON_COLORS, DIVERGING, POSITIVE, NEGATIVE, NEUTRAL,
    PRIORITY_COLORS, priority_pill,
)
from utils.footer import render_footer
from utils.navigation import see_also

st.set_page_config(page_title="Recommendations | MiLB", page_icon="*", layout="wide")

LEVEL_ORDER = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}
LEVEL_FILES = {11: "triplea", 12: "doublea", 13: "higha", 14: "singlea"}
GREEN = "#2ecc71"
RED = "#e74c3c"
GREY = "#95a5a6"

PROMO_LABELS = {
    "has_fireworks": "Fireworks",
    "has_giveaway": "Giveaway",
    "has_food_deal": "Food Deal",
    "has_ticket_deal": "Ticket Deal",
    "has_theme_night": "Theme Night",
    "has_kids_event": "Kids Event",
    "has_heritage": "Heritage Night",
    "has_community": "Community",
    "has_entertain": "Entertainment",
    "has_dog": "Dog Friendly",
    "has_celebrity": "Celebrity",
    "has_recurring": "Recurring",
}

PROMO_FLAGS = list(PROMO_LABELS.keys())

CATEGORY_LABELS = {
    "promo_roi": "Promotion ROI",
    "peer_gap": "Peer Gap",
    "scheduling": "Scheduling",
    "anomaly": "Anomaly",
    "what_if": "What-If",
    "promo_peer": "Promo Strategy Peers",
    "strategy_mismatch": "Strategy Mismatch",
    "cluster_opportunity": "Cluster Opportunity",
    "dow_strategy": "Day-of-Week Strategy",
}

CONFIDENCE_COLORS = {
    "high": GREEN,
    "medium": "#f39c12",
    "low": GREY,
}


# -- Data loading -------------------------------------------------------------

@st.cache_data(ttl=600)
def load_teams():
    return query_df("""
        SELECT t.team_id, t.team_name, t.sport_id,
               COALESCE(sp.sport_name, 'Unknown') AS level,
               tc.cluster_id, tc.cluster_label,
               tow.operator_name
        FROM milb.teams t
        LEFT JOIN milb.sports sp ON t.sport_id = sp.sport_id
        LEFT JOIN milb.team_clusters tc ON t.team_id = tc.team_id
        LEFT JOIN milb.team_operators tow ON t.operator_id = tow.operator_id
        WHERE t.sport_id IN (11,12,13,14)
        ORDER BY t.sport_id, t.team_name
    """)


@st.cache_data(ttl=600)
def load_promo_lift():
    return query_df("SELECT * FROM milb.promo_lift ORDER BY marginal_lift DESC")


@st.cache_data(ttl=600)
def load_promo_lift_cf():
    """Counterfactual (S-learner) promo lift from milb.promo_lift_cf.

    Primary signal for the What-If simulator and recommendations since
    2026-04-17. OLS estimates (load_promo_lift) are retained only for
    diagnostic surfaces -- CF collapsed the OLS negatives as selection bias.
    """
    return query_df("""
        SELECT team_id, sport_id, scope, promo_type, estimand,
               mean_lift, mean_pct_lift, pct_positive, n_games
        FROM milb.promo_lift_cf
        WHERE estimand = 'ATE'
    """)


@st.cache_data(ttl=600)
def load_cluster_benchmarks():
    return query_df("SELECT * FROM milb.cluster_benchmarks")


@st.cache_data(ttl=600)
def load_team_recommendations():
    return query_df("""
        SELECT * FROM milb.team_recommendations
        ORDER BY team_id, priority
    """)


@st.cache_data(ttl=600)
def load_model_runs():
    return query_df("SELECT * FROM milb.model_runs ORDER BY sport_id")


@st.cache_data(ttl=600)
def load_feature_importance():
    return query_df("""
        SELECT fi.*, mr.sport_id
        FROM milb.feature_importance fi
        JOIN milb.model_runs mr ON fi.run_id = mr.run_id
        ORDER BY fi.run_id, fi.shap_rank
    """)


@st.cache_data(ttl=600)
def load_predictions():
    return query_df("""
        SELECT gp.game_pk, gp.run_id, gp.predicted_attendance, gp.residual,
               gf.team_id, gf.season, gf.game_date, gf.attendance,
               gf.sport_id, gf.capacity_utilization
        FROM milb.game_predictions gp
        JOIN milb.game_features gf ON gp.game_pk = gf.game_pk
    """)


@st.cache_data(ttl=600)
def load_game_features_for_whatif(team_id: int):
    return query_df("""
        SELECT * FROM milb.game_features
        WHERE team_id = :tid
        ORDER BY game_date
    """, {"tid": team_id})


@st.cache_data(ttl=600)
def load_cluster_teams(cluster_id: int):
    return query_df("""
        SELECT t.team_id, t.team_name, tc.cluster_label,
               tc.centroid_distance
        FROM milb.team_clusters tc
        JOIN milb.teams t ON tc.team_id = t.team_id
        WHERE tc.cluster_id = :cid
        ORDER BY tc.centroid_distance
    """, {"cid": cluster_id})


@st.cache_data(ttl=600)
def load_cluster_team_stats(cluster_id: int):
    return query_df("""
        SELECT gf.team_id, t.team_name,
               AVG(gf.attendance)::int AS avg_attendance,
               AVG(gf.capacity_utilization) AS avg_cap_util,
               AVG(gf.has_any_promo::int) AS promo_rate,
               AVG(gf.has_fireworks::int) AS fw_rate,
               AVG(gf.has_giveaway::int) AS giveaway_rate,
               COUNT(*) AS total_games,
               gf.venue_capacity,
               gf.msa_population,
               gf.median_income,
               gf.poverty_rate
        FROM milb.game_features gf
        JOIN milb.teams t ON gf.team_id = t.team_id
        JOIN milb.team_clusters tc ON gf.team_id = tc.team_id
        WHERE tc.cluster_id = :cid
          AND gf.season = (SELECT MAX(season) FROM milb.game_features)
        GROUP BY gf.team_id, t.team_name, gf.venue_capacity,
                 gf.msa_population, gf.median_income, gf.poverty_rate
        ORDER BY avg_attendance DESC
    """, {"cid": cluster_id})


@st.cache_data(ttl=60)
def load_rec_actions(team_id: int):
    """Actions previously recorded for this team's recommendations.

    Short TTL (60s) so a freshly-saved action shows up immediately after
    the user clicks 'Save'.
    """
    return query_df("""
        SELECT rec_category, rec_title, status, notes, acted_at
          FROM milb.recommendation_actions
         WHERE team_id = :tid
    """, {"tid": team_id})


def save_rec_action(team_id: int, category: str, title: str,
                    status: str, notes: str = "") -> None:
    """Upsert a (team, category, title) action row."""
    execute("""
        INSERT INTO milb.recommendation_actions
              (team_id, rec_category, rec_title, status, notes, acted_on, acted_at, updated_at)
        VALUES (:tid, :cat, :title, :status, :notes, TRUE, NOW(), NOW())
        ON CONFLICT (team_id, rec_category, rec_title) DO UPDATE
           SET status     = EXCLUDED.status,
               notes      = EXCLUDED.notes,
               updated_at = NOW()
    """, {"tid": team_id, "cat": category, "title": title,
          "status": status, "notes": notes})
    # Invalidate the cache so the next render picks up the new row
    load_rec_actions.clear()


ACTION_STATUSES = ["not yet", "planned", "in_progress", "done", "rejected"]
ACTION_STATUS_COLORS = {
    "not yet":     NEUTRAL,
    "planned":     "#5aa9d9",
    "in_progress": "#e8a23e",
    "done":        POSITIVE,
    "rejected":    NEGATIVE,
}


@st.cache_data(ttl=600)
def load_team_promo_usage(team_id: int):
    """Per-category % of games that had this promo for this team, latest season.

    Returns a dict like {"has_fireworks": 0.42, "has_celebrity": 0.02, ...}.
    Used to flag recommendations about categories the team rarely runs
    ('novelty') -- the exact opposite of 'buy more fireworks'.
    """
    df = query_df("""
        SELECT
            AVG(has_fireworks::int)   AS has_fireworks,
            AVG(has_giveaway::int)    AS has_giveaway,
            AVG(has_food_deal::int)   AS has_food_deal,
            AVG(has_ticket_deal::int) AS has_ticket_deal,
            AVG(has_theme_night::int) AS has_theme_night,
            AVG(has_kids_event::int)  AS has_kids_event,
            AVG(has_heritage::int)    AS has_heritage,
            AVG(has_community::int)   AS has_community,
            AVG(has_entertain::int)   AS has_entertain,
            AVG(has_dog::int)         AS has_dog,
            AVG(has_celebrity::int)   AS has_celebrity,
            AVG(has_recurring::int)   AS has_recurring
          FROM milb.game_features
         WHERE team_id = :tid
           AND season = (SELECT MAX(season) FROM milb.game_features WHERE team_id = :tid)
    """, {"tid": team_id})
    if df.empty:
        return {}
    return {k: float(v) if pd.notna(v) else 0.0 for k, v in df.iloc[0].items()}


NOVELTY_THRESHOLD = 0.10  # <=10% of games means "team rarely runs this"


def _extract_promo_type(evidence) -> str | None:
    """Fish a promo_type flag name out of the evidence JSON (if any)."""
    if evidence is None:
        return None
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(evidence, dict):
        return None
    # Common keys across categories: promo_type, promo_col, flag, gap_promo
    for k in ("promo_type", "promo_col", "flag", "gap_promo", "missing_promo"):
        v = evidence.get(k)
        if isinstance(v, str) and v.startswith("has_"):
            return v
    return None


@st.cache_data(ttl=600)
def load_promo_cluster_info():
    return query_df("""
        SELECT pc.team_id, pc.promo_cluster_id, pc.promo_cluster_label,
               cd.description, cd.key_traits
        FROM milb.team_promo_clusters pc
        LEFT JOIN milb.promo_cluster_descriptions cd
            ON pc.promo_cluster_id = cd.promo_cluster_id
    """)


@st.cache_data(ttl=600)
def load_promo_cluster_team_stats(promo_cluster_id: int):
    return query_df("""
        SELECT gf.team_id, t.team_name,
               AVG(gf.attendance)::int AS avg_attendance,
               AVG(gf.capacity_utilization) AS avg_cap_util,
               AVG(gf.has_any_promo::int) AS promo_rate,
               COUNT(*) AS total_games,
               gf.venue_capacity
        FROM milb.game_features gf
        JOIN milb.teams t ON gf.team_id = t.team_id
        JOIN milb.team_promo_clusters pc ON gf.team_id = pc.team_id
        WHERE pc.promo_cluster_id = :pcid
          AND gf.season = (SELECT MAX(season) FROM milb.game_features)
        GROUP BY gf.team_id, t.team_name, gf.venue_capacity
        ORDER BY avg_cap_util DESC
    """, {"pcid": promo_cluster_id})


# -- Sidebar -------------------------------------------------------------------

teams_df = load_teams()
teams_df["level_label"] = teams_df["sport_id"].map(LEVEL_ORDER).fillna(teams_df["level"])

with st.sidebar:
    st.header("Filters")

    selected_levels = st.multiselect(
        "Level",
        options=list(LEVEL_ORDER.values()),
        default=list(LEVEL_ORDER.values()),
    )

    selected_operators = operator_filter()

    level_teams = teams_df[teams_df["level_label"].isin(selected_levels)]
    if selected_operators:
        level_teams = level_teams[level_teams["operator_name"].isin(selected_operators)]

    team_options = ["-- All teams --"] + level_teams.sort_values("team_name")["team_name"].tolist()
    _default_idx = team_options.index("Binghamton Rumble Ponies") if "Binghamton Rumble Ponies" in team_options else 0
    selected_team_name = st.selectbox("Team", options=team_options, index=_default_idx)

    st.divider()
    st.caption(
        "Analytics powered by OLS regression (promo lift), "
        "K-Means clustering (peer groups), and XGBoost (predictions)."
    )


# Resolve selected team
selected_team_id = None
if selected_team_name != "-- All teams --":
    row = teams_df[teams_df["team_name"] == selected_team_name]
    if not row.empty:
        selected_team_id = int(row.iloc[0]["team_id"])

level_ids = set(level_teams["team_id"])


# -- Page header ---------------------------------------------------------------

st.title("Analytics & Recommendations")

if selected_team_id:
    team_row = teams_df[teams_df["team_id"] == selected_team_id].iloc[0]
    cluster_info = f" | Peer group: {team_row['cluster_label']}" if pd.notna(team_row.get("cluster_label")) else ""
    promo_clusters = load_promo_cluster_info()
    team_promo = promo_clusters[promo_clusters["team_id"] == selected_team_id]
    promo_info = f" | Promo strategy: {team_promo.iloc[0]['promo_cluster_label']}" if not team_promo.empty else ""
    st.caption(f"{selected_team_name} | {team_row['level_label']}{cluster_info}{promo_info}")
else:
    st.caption(f"Showing {len(level_teams)} teams across {len(selected_levels)} levels")


# -- Tabs ----------------------------------------------------------------------

tab_promo, tab_peer, tab_whatif, tab_recs, tab_model = st.tabs([
    "Promotion ROI",
    "Peer Comparison",
    "What-If Simulator",
    "Recommendations",
    "Model Performance",
])


# ==============================================================================
# TAB 1: PROMOTION ROI
# ==============================================================================

with tab_promo:
    st.subheader("Marginal Promotion Lift (OLS Regression)")
    st.caption(
        "Each bar shows how many additional fans a promotion type adds, "
        "controlling for day-of-week, month, weather, homestand position, "
        "and school calendar. Error bars = 95% confidence intervals."
    )

    lift_df = load_promo_lift()
    if lift_df.empty:
        st.warning("No promo lift data. Run `python scripts/analyze_promo_lift.py` first.")
        st.stop()

    # Scope selector
    if selected_team_id:
        # Try team-specific, fall back to league-level
        team_lift = lift_df[
            (lift_df["team_id"] == selected_team_id) & (lift_df["scope"] == "team_all")
        ]
        team_sid = int(teams_df[teams_df["team_id"] == selected_team_id]["sport_id"].iloc[0])
        league_lift = lift_df[
            (lift_df["sport_id"] == team_sid) & (lift_df["scope"] == "league_level")
        ]

        scope_options = []
        if not team_lift.empty:
            scope_options.append("Team-specific")
        if not league_lift.empty:
            scope_options.append("League-wide")

        if not scope_options:
            st.info("No lift data for this team or its level.")
        else:
            scope_choice = st.radio("Scope", scope_options, horizontal=True)
            show_lift = team_lift if scope_choice == "Team-specific" else league_lift
    else:
        # No team selected -> show league-level per level
        sport_ids = [k for k, v in LEVEL_ORDER.items() if v in selected_levels]
        show_lift = lift_df[
            (lift_df["scope"] == "league_level") & (lift_df["sport_id"].isin(sport_ids))
        ]
        if show_lift.empty:
            st.info("No league-level lift data for selected levels.")

    if not show_lift.empty:
        # Build chart data
        chart_df = show_lift.copy()
        chart_df["label"] = chart_df["promo_type"].map(PROMO_LABELS)
        chart_df["error_low"] = chart_df["marginal_lift"] - chart_df["ci_lower"]
        chart_df["error_high"] = chart_df["ci_upper"] - chart_df["marginal_lift"]
        chart_df["sig"] = chart_df["p_value"] < 0.05
        chart_df["bar_color"] = chart_df.apply(
            lambda r: GREEN if r["marginal_lift"] > 0 and r["sig"] else (
                RED if r["marginal_lift"] < 0 and r["sig"] else GREY
            ), axis=1
        )

        # If multiple levels shown (no team selected), group by level
        if selected_team_id is None and chart_df["sport_id"].nunique() > 1:
            chart_df["level"] = chart_df["sport_id"].map(LEVEL_ORDER)
            for level_name in sorted(chart_df["level"].unique()):
                ldf = chart_df[chart_df["level"] == level_name].sort_values(
                    "marginal_lift", ascending=False
                )
                st.markdown(f"**{level_name}**")
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=ldf["label"],
                    y=ldf["marginal_lift"],
                    error_y=dict(type="data", symmetric=False,
                                 array=ldf["error_high"].values,
                                 arrayminus=ldf["error_low"].values),
                    marker_color=ldf["bar_color"],
                    text=ldf["n_games_with"],
                    texttemplate="%{text} games",
                    textposition="outside",
                ))
                fig.update_layout(
                    yaxis_title="Marginal Lift (fans)",
                    height=380,
                    margin=dict(t=30, b=60),
                    xaxis_tickangle=-30,
                )
                st.plotly_chart(fig, use_container_width=True)
        else:
            chart_df = chart_df.sort_values("marginal_lift", ascending=False)
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=chart_df["label"],
                y=chart_df["marginal_lift"],
                error_y=dict(type="data", symmetric=False,
                             array=chart_df["error_high"].values,
                             arrayminus=chart_df["error_low"].values),
                marker_color=chart_df["bar_color"],
                text=chart_df["n_games_with"],
                texttemplate="%{text} games",
                textposition="outside",
            ))
            fig.update_layout(
                yaxis_title="Marginal Lift (fans)",
                height=420,
                margin=dict(t=30, b=60),
                xaxis_tickangle=-30,
            )
            st.plotly_chart(fig, use_container_width=True)

        # Data table
        with st.expander("Show detailed lift data"):
            display_df = chart_df[["label", "marginal_lift", "ci_lower", "ci_upper",
                                   "p_value", "n_games_with", "n_games_without"]].copy()
            display_df.columns = ["Promo Type", "Lift", "CI Lower", "CI Upper",
                                  "p-value", "Games With", "Games Without"]
            display_df["Lift"] = display_df["Lift"].apply(lambda v: f"{v:+,.0f}")
            display_df["CI Lower"] = display_df["CI Lower"].apply(lambda v: f"{v:+,.0f}")
            display_df["CI Upper"] = display_df["CI Upper"].apply(lambda v: f"{v:+,.0f}")
            display_df["p-value"] = display_df["p-value"].apply(
                lambda v: f"{v:.4f}" if v >= 0.0001 else "<0.0001"
            )
            st.dataframe(display_df, use_container_width=True, hide_index=True)


# ==============================================================================
# TAB 2: PEER COMPARISON
# ==============================================================================

with tab_peer:
    st.subheader("Peer Cluster Comparison")

    if not selected_team_id:
        st.info("Select a team from the sidebar to see peer comparisons.")
    else:
        team_row = teams_df[teams_df["team_id"] == selected_team_id].iloc[0]
        cluster_id = team_row.get("cluster_id")

        # Determine available peer types
        _promo_clusters = load_promo_cluster_info()
        _team_promo = _promo_clusters[_promo_clusters["team_id"] == selected_team_id]
        has_promo_cluster = not _team_promo.empty

        peer_options = ["Market Peers"]
        if has_promo_cluster:
            peer_options.append("Promo Strategy Peers")

        peer_type = st.radio("Peer type", peer_options, horizontal=True)

        if peer_type == "Market Peers":
            if pd.isna(cluster_id):
                st.warning("This team has not been assigned to a peer cluster. Run `cluster_peers.py`.")
            else:
                cluster_id = int(cluster_id)
                st.caption(f"Peer group: **{team_row['cluster_label']}**")

                peer_metric = st.radio(
                    "Compare by",
                    ["% Capacity Utilization", "Raw Attendance"],
                    horizontal=True,
                    help="Capacity utilization normalizes for venue size, making comparison fairer across different-sized parks.",
                )
                use_cap_util = peer_metric == "% Capacity Utilization"

                peer_stats = load_cluster_team_stats(cluster_id)

                if peer_stats.empty:
                    st.info("No peer stats available.")
                else:
                    peer_stats["is_selected"] = peer_stats["team_id"] == selected_team_id
                    team_stats = peer_stats[peer_stats["is_selected"]]
                    peer_avg = peer_stats.mean(numeric_only=True)

                    if not team_stats.empty:
                        ts = team_stats.iloc[0]
                        c1, c2, c3, c4, c5 = st.columns(5)
                        c1.metric(
                            "Avg Attendance",
                            f"{ts['avg_attendance']:,.0f}",
                            delta=f"{ts['avg_attendance'] - peer_avg['avg_attendance']:+,.0f} vs peers",
                        )
                        c2.metric(
                            "Capacity Util",
                            f"{ts['avg_cap_util']:.1%}" if pd.notna(ts['avg_cap_util']) else "N/A",
                            delta=f"{(ts['avg_cap_util'] - peer_avg['avg_cap_util']):.1%} vs peers"
                            if pd.notna(ts['avg_cap_util']) else None,
                        )
                        c3.metric(
                            "Venue Capacity",
                            f"{ts['venue_capacity']:,.0f}" if pd.notna(ts.get('venue_capacity')) else "N/A",
                        )
                        c4.metric(
                            "Promo Rate",
                            f"{ts['promo_rate']:.0%}" if pd.notna(ts['promo_rate']) else "N/A",
                        )
                        c5.metric("Peer Group Size", f"{len(peer_stats)} teams")

                    # -- Cluster benchmark strip (from milb.cluster_benchmarks) --
                    bm_all = load_cluster_benchmarks()
                    if not bm_all.empty:
                        bm = bm_all[bm_all["cluster_id"] == cluster_id]
                        if not bm.empty:
                            BM_LABELS = {
                                "avg_attendance":      ("Avg Att (cluster)",  "{:,.0f}"),
                                "capacity_utilization":("Cap Util (cluster)", "{:.1%}"),
                                "promo_rate":          ("Promo Rate (cluster)","{:.0%}"),
                                "pct_fireworks":       ("% Fireworks",        "{:.0%}"),
                                "pct_giveaway":        ("% Giveaway",         "{:.0%}"),
                                "pct_theme_night":     ("% Theme Night",      "{:.0%}"),
                            }
                            bm_row = dict(zip(bm["metric_name"], bm["metric_value"]))
                            with st.expander("Cluster benchmarks (official metrics for your peer group)", expanded=False):
                                cols = st.columns(len(BM_LABELS))
                                for i, (mname, (label, fmt)) in enumerate(BM_LABELS.items()):
                                    v = bm_row.get(mname)
                                    cols[i].metric(label, fmt.format(float(v)) if pd.notna(v) else "-")
                                st.caption(
                                    "Benchmarks computed once per run by `cluster_peers.py` and cached in "
                                    "`milb.cluster_benchmarks`. These are the 'official' peer-group averages."
                                )

                    st.divider()

                    metric_col = "avg_cap_util" if use_cap_util else "avg_attendance"
                    metric_label = "Capacity Utilization" if use_cap_util else "Avg Attendance"
                    peer_chart = peer_stats.sort_values(metric_col, ascending=True).copy()
                    peer_chart["bar_color"] = peer_chart["is_selected"].map(
                        {True: "#3498db", False: GREY}
                    )

                    fig = go.Figure()
                    fig.add_trace(go.Bar(
                        y=peer_chart["team_name"],
                        x=peer_chart[metric_col],
                        orientation="h",
                        marker_color=peer_chart["bar_color"],
                        text=peer_chart[metric_col],
                        texttemplate="%{text:.1%}" if use_cap_util else "%{text:,.0f}",
                        textposition="outside",
                    ))
                    avg_val = peer_avg[metric_col]
                    fig.add_vline(
                        x=avg_val,
                        line_dash="dash",
                        line_color=RED,
                        annotation_text=f"Peer avg: {avg_val:.1%}" if use_cap_util else f"Peer avg: {avg_val:,.0f}",
                    )
                    fig.update_layout(
                        height=max(400, len(peer_chart) * 28),
                        margin=dict(t=20, b=20, r=100),
                        xaxis_title=metric_label,
                        xaxis_tickformat=".0%" if use_cap_util else ",",
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    with st.expander("Full peer data table"):
                        display = peer_stats[[
                            "team_name", "avg_attendance", "avg_cap_util", "venue_capacity",
                            "msa_population", "median_income",
                            "promo_rate", "fw_rate", "giveaway_rate", "total_games"
                        ]].copy()
                        display.columns = [
                            "Team", "Avg Att", "Cap Util", "Capacity",
                            "MSA Pop", "Median Income",
                            "Promo Rate", "Fireworks %", "Giveaway %", "Games"
                        ]
                        for col in ["Cap Util", "Promo Rate", "Fireworks %", "Giveaway %"]:
                            display[col] = display[col].apply(
                                lambda v: f"{v:.1%}" if pd.notna(v) else "-"
                            )
                        display["Avg Att"] = display["Avg Att"].apply(lambda v: f"{v:,.0f}")
                        display["Capacity"] = display["Capacity"].apply(
                            lambda v: f"{v:,.0f}" if pd.notna(v) else "-"
                        )
                        display["MSA Pop"] = display["MSA Pop"].apply(
                            lambda v: f"{v:,.0f}" if pd.notna(v) else "-"
                        )
                        display["Median Income"] = display["Median Income"].apply(
                            lambda v: f"${v:,.0f}" if pd.notna(v) else "-"
                        )
                        st.dataframe(display, use_container_width=True, hide_index=True)

        else:
            # Promo Strategy Peers
            pcid = int(_team_promo.iloc[0]["promo_cluster_id"])
            pc_label = _team_promo.iloc[0]["promo_cluster_label"]
            pc_desc = _team_promo.iloc[0].get("description", "")
            st.caption(f"Promo strategy cluster: **{pc_label}**")
            if pc_desc:
                st.caption(pc_desc)

            peer_metric = st.radio(
                "Compare by",
                ["% Capacity Utilization", "Raw Attendance"],
                horizontal=True,
                key="promo_peer_metric",
            )
            use_cap_util = peer_metric == "% Capacity Utilization"

            promo_peer_stats = load_promo_cluster_team_stats(pcid)

            if promo_peer_stats.empty:
                st.info("No promo peer stats available.")
            else:
                promo_peer_stats["is_selected"] = promo_peer_stats["team_id"] == selected_team_id
                team_stats = promo_peer_stats[promo_peer_stats["is_selected"]]
                peer_avg = promo_peer_stats.mean(numeric_only=True)

                if not team_stats.empty:
                    ts = team_stats.iloc[0]
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric(
                        "Avg Attendance",
                        f"{ts['avg_attendance']:,.0f}",
                        delta=f"{ts['avg_attendance'] - peer_avg['avg_attendance']:+,.0f} vs peers",
                    )
                    c2.metric(
                        "Capacity Util",
                        f"{ts['avg_cap_util']:.1%}" if pd.notna(ts['avg_cap_util']) else "N/A",
                        delta=f"{(ts['avg_cap_util'] - peer_avg['avg_cap_util']):.1%} vs peers"
                        if pd.notna(ts['avg_cap_util']) else None,
                    )
                    c3.metric(
                        "Promo Rate",
                        f"{ts['promo_rate']:.0%}" if pd.notna(ts['promo_rate']) else "N/A",
                    )
                    c4.metric("Strategy Peers", f"{len(promo_peer_stats)} teams")

                st.divider()

                metric_col = "avg_cap_util" if use_cap_util else "avg_attendance"
                metric_label = "Capacity Utilization" if use_cap_util else "Avg Attendance"
                peer_chart = promo_peer_stats.sort_values(metric_col, ascending=True).copy()
                peer_chart["bar_color"] = peer_chart["is_selected"].map(
                    {True: "#3498db", False: GREY}
                )

                fig = go.Figure()
                fig.add_trace(go.Bar(
                    y=peer_chart["team_name"],
                    x=peer_chart[metric_col],
                    orientation="h",
                    marker_color=peer_chart["bar_color"],
                    text=peer_chart[metric_col],
                    texttemplate="%{text:.1%}" if use_cap_util else "%{text:,.0f}",
                    textposition="outside",
                ))
                avg_val = peer_avg[metric_col]
                fig.add_vline(
                    x=avg_val,
                    line_dash="dash",
                    line_color=RED,
                    annotation_text=f"Peer avg: {avg_val:.1%}" if use_cap_util else f"Peer avg: {avg_val:,.0f}",
                )
                fig.update_layout(
                    height=max(400, len(peer_chart) * 28),
                    margin=dict(t=20, b=20, r=100),
                    xaxis_title=metric_label,
                    xaxis_tickformat=".0%" if use_cap_util else ",",
                )
                st.plotly_chart(fig, use_container_width=True)


# ==============================================================================
# TAB 3: WHAT-IF SIMULATOR
# ==============================================================================

with tab_whatif:
    st.subheader("What-If Promotion Simulator")

    if not selected_team_id:
        st.info("Select a team from the sidebar to use the simulator.")
    else:
        st.caption(
            "Both baseline and promo effects come from the trained XGBoost model. "
            "We run it twice per scenario -- once with the promos off, once on -- "
            "and take the difference. This is the same S-learner counterfactual "
            "used in `analyze_promo_lift_counterfactual.py`, which replaced OLS "
            "after CF showed most OLS negatives were selection bias."
        )

        team_features = load_game_features_for_whatif(selected_team_id)

        if team_features.empty:
            st.warning("No game features data for this team.")
        else:
            # Get the team's average game profile for simulation
            latest_season = int(team_features["season"].max())
            season_games = team_features[team_features["season"] == latest_season]

            if season_games.empty:
                st.info("No games in latest season.")
            else:
                # Try to load the XGBoost model
                team_sid = int(teams_df[teams_df["team_id"] == selected_team_id]["sport_id"].iloc[0])
                model_file = Path(__file__).parent.parent.parent / "models" / f"xgb_{LEVEL_FILES.get(team_sid, 'triplea')}_attendance.json"

                if not model_file.exists():
                    st.warning(f"Model file not found: {model_file.name}. Run `train_attendance_model.py`.")
                else:
                    import xgboost as xgb

                    model = xgb.XGBRegressor()
                    model.load_model(str(model_file))

                    # Use median game as baseline; mode() for categoricals
                    baseline_game = season_games.median(numeric_only=True).to_frame().T
                    # Restore categorical cols as int (median makes them float)
                    baseline_game["team_id"] = selected_team_id
                    for cat_col in ["opponent_team_id", "sport_id"]:
                        if cat_col in baseline_game.columns:
                            baseline_game[cat_col] = int(baseline_game[cat_col].iloc[0])
                    # Per-column safe defaults -- a single "stable" fallback
                    # wrote "stable" into weather_bucket for teams with no
                    # weather rows, which is not a trained category (values
                    # are clear/rain/snow) and XGBoost errors on predict.
                    CAT_DEFAULTS = {
                        "game_type":         "R",
                        "day_night":         "night",
                        "weather_bucket":    "clear",
                        "population_trend":  "stable",
                        "start_time_bucket": "evening",
                    }
                    for cat_col, default in CAT_DEFAULTS.items():
                        if cat_col in season_games.columns:
                            mode_val = season_games[cat_col].mode()
                            baseline_game[cat_col] = (
                                mode_val.iloc[0] if not mode_val.empty else default
                            )

                    st.markdown("**Configure a hypothetical game:**")

                    col_left, col_right = st.columns(2)

                    with col_left:
                        sim_dow = st.selectbox(
                            "Day of week",
                            options=[0, 1, 2, 3, 4, 5, 6],
                            format_func=lambda d: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d],
                            index=4,  # Default Friday
                        )
                        sim_month = st.selectbox(
                            "Month",
                            options=[4, 5, 6, 7, 8, 9],
                            format_func=lambda m: ["", "", "", "", "Apr", "May", "Jun", "Jul", "Aug", "Sep"][m],
                            index=3,  # Default July
                        )
                        sim_weekend = 1 if sim_dow >= 4 else 0

                    with col_right:
                        sim_promos = st.multiselect(
                            "Active promotions",
                            options=PROMO_FLAGS,
                            format_func=lambda f: PROMO_LABELS[f],
                            default=[],
                        )

                    # Demographics scenario (advanced)
                    pop_change = 0
                    income_change = 0
                    with st.expander("Demographics scenario (advanced)"):
                        st.caption(
                            "Simulate how demographic changes affect predicted attendance. "
                            "Useful for understanding the model's sensitivity to market factors "
                            "outside your control."
                        )
                        base_pop = baseline_game.get("msa_population")
                        base_pop = int(base_pop.iloc[0]) if base_pop is not None and pd.notna(base_pop.iloc[0]) else None
                        base_income = baseline_game.get("median_income")
                        base_income = int(base_income.iloc[0]) if base_income is not None and pd.notna(base_income.iloc[0]) else None

                        if base_pop and base_income:
                            pop_change = st.slider("MSA Population change %", -20, 20, 0, step=5)
                            income_change = st.slider("Median income change %", -20, 20, 0, step=5)
                            if pop_change != 0:
                                st.caption(f"MSA pop: {base_pop:,} -> {int(base_pop * (1 + pop_change / 100)):,}")
                            if income_change != 0:
                                st.caption(f"Income: ${base_income:,} -> ${int(base_income * (1 + income_change / 100)):,}")
                        else:
                            st.caption("Demographics data not available for this team.")

                    # Build simulation row from baseline
                    sim_row = baseline_game.copy()
                    sim_row["day_of_week"] = sim_dow
                    sim_row["month"] = sim_month
                    sim_row["is_weekend"] = sim_weekend

                    for flag in PROMO_FLAGS:
                        sim_row[flag] = 1 if flag in sim_promos else 0
                    sim_row["has_any_promo"] = 1 if sim_promos else 0
                    sim_row["promo_count"] = len(sim_promos)

                    # Apply demographics scenario
                    if pop_change != 0 and "msa_population" in sim_row.columns:
                        sim_row["msa_population"] = int(sim_row["msa_population"].iloc[0] * (1 + pop_change / 100))
                        if "population_change_5yr_pct" in sim_row.columns:
                            sim_row["population_change_5yr_pct"] = pop_change / 100
                    if income_change != 0 and "median_income" in sim_row.columns:
                        sim_row["median_income"] = int(sim_row["median_income"].iloc[0] * (1 + income_change / 100))
                        if "income_change_5yr_pct" in sim_row.columns:
                            sim_row["income_change_5yr_pct"] = income_change / 100

                    # Also build a no-promo baseline for comparison (same demographics scenario)
                    no_promo_row = sim_row.copy()
                    for flag in PROMO_FLAGS:
                        no_promo_row[flag] = 0
                    no_promo_row["has_any_promo"] = 0
                    no_promo_row["promo_count"] = 0

                    # Prepare features matching model expectations
                    EXCLUDE_COLS = {
                        "game_pk", "game_date", "attendance", "capacity_utilization",
                        "attendance_lift", "run_id", "created_at", "census_year",
                    }

                    # Derive categorical cols from the trained model (not hardcoded)
                    model_features = model.get_booster().feature_names
                    model_ftypes = model.get_booster().feature_types
                    model_cat_cols = set()
                    if model_features and model_ftypes:
                        model_cat_cols = {
                            model_features[i]
                            for i, t in enumerate(model_ftypes)
                            if t == "c"
                        }

                    # String-typed categoricals need a string default, not 0 --
                    # otherwise astype("category") below produces int-indexed
                    # categories and XGBoost errors on dtype mismatch at predict.
                    STR_CAT_DEFAULTS = {
                        "population_trend":  "stable",
                        "start_time_bucket": "evening",
                        "weather_bucket":    "clear",
                        "day_night":         "night",
                        "game_type":         "R",
                    }

                    def prep_for_predict(row_df):
                        fdf = row_df.drop(
                            columns=[c for c in EXCLUDE_COLS if c in row_df.columns],
                            errors="ignore"
                        )
                        # Filter to model features first, then set types
                        if model_features:
                            for col in model_features:
                                if col not in fdf.columns:
                                    fdf[col] = STR_CAT_DEFAULTS.get(col, 0)
                            fdf = fdf[model_features]
                        for col in fdf.select_dtypes(include=["bool"]).columns:
                            fdf[col] = fdf[col].astype(int)
                        for col in fdf.select_dtypes(include=["object"]).columns:
                            unique_vals = set(fdf[col].dropna().unique())
                            if unique_vals.issubset({True, False}):
                                fdf[col] = fdf[col].astype(float)
                        for col in model_cat_cols:
                            if col in fdf.columns:
                                if fdf[col].dtype in ("float64", "float32"):
                                    fdf[col] = fdf[col].fillna(-1).astype(int)
                                fdf[col] = fdf[col].astype("category")
                        return fdf

                    # Team-level CF context for historical pct_positive/n_games
                    # on the per-promo table. Primary lift is the per-game S-
                    # learner (predict twice); this just adds confidence metadata.
                    cf_df = load_promo_lift_cf()
                    cf_team = cf_df[
                        (cf_df["team_id"] == selected_team_id)
                        & (cf_df["scope"] == "team")
                    ]
                    cf_level = cf_df[
                        (cf_df["sport_id"] == team_sid)
                        & (cf_df["scope"] == "level")
                    ]
                    cf_context = {}
                    context_source = "level"
                    if not cf_team.empty:
                        context_source = "team"
                        for _, r in cf_team.iterrows():
                            cf_context[r["promo_type"]] = {
                                "mean_lift":    r["mean_lift"],
                                "pct_positive": r["pct_positive"],
                                "n_games":      r["n_games"],
                            }
                    if not cf_level.empty:
                        for _, r in cf_level.iterrows():
                            cf_context.setdefault(r["promo_type"], {
                                "mean_lift":    r["mean_lift"],
                                "pct_positive": r["pct_positive"],
                                "n_games":      r["n_games"],
                            })

                    # Helper: flip a flag on sim_row and recompute derived fields
                    # per the CF rules memoed on 2026-04-17 (promo_count,
                    # has_any_promo re-derived; has_limited_giveaway zeroed
                    # when giveaway is off).
                    def apply_flags(row, active_flags):
                        r = row.copy()
                        for f in PROMO_FLAGS:
                            r[f] = 1 if f in active_flags else 0
                        r["promo_count"] = len(active_flags)
                        r["has_any_promo"] = 1 if active_flags else 0
                        if "has_limited_giveaway" in r.columns and "has_giveaway" not in active_flags:
                            r["has_limited_giveaway"] = 0
                        return r

                    try:
                        # S-learner: predict the SAME game config with promos
                        # off vs on. Delta = counterfactual lift for THIS game.
                        no_promo_row_cf = apply_flags(sim_row, [])
                        X_base = prep_for_predict(no_promo_row_cf)
                        pred_without = int(model.predict(X_base)[0])

                        X_with = prep_for_predict(apply_flags(sim_row, sim_promos))
                        pred_with = int(model.predict(X_with)[0])
                        promo_effect = pred_with - pred_without

                        # Per-promo CF: flip each flag solo vs no-promo baseline.
                        per_promo_lift = {}
                        for flag in sim_promos:
                            X_solo = prep_for_predict(apply_flags(sim_row, [flag]))
                            per_promo_lift[flag] = int(model.predict(X_solo)[0]) - pred_without

                        # Venue capacity for % util display
                        venue_cap = season_games["venue_capacity"].median()
                        venue_cap = int(venue_cap) if pd.notna(venue_cap) and venue_cap > 0 else None

                        # Compute demographic impact if scenario is active
                        demo_impact = None
                        if pop_change != 0 or income_change != 0:
                            # Original demographics, no promos
                            orig_demo_row = apply_flags(baseline_game.copy(), [])
                            orig_demo_row["day_of_week"] = sim_dow
                            orig_demo_row["month"] = sim_month
                            orig_demo_row["is_weekend"] = sim_weekend
                            X_orig = prep_for_predict(orig_demo_row)
                            pred_orig = int(model.predict(X_orig)[0])
                            demo_impact = pred_without - pred_orig

                        # Display results
                        st.divider()
                        if demo_impact is not None:
                            c1, c2, c3, c4 = st.columns(4)
                        else:
                            c1, c2, c3 = st.columns(3)
                        base_label = f"{pred_without:,}"
                        with_label = f"{pred_with:,}"
                        if venue_cap:
                            base_label += f" ({pred_without/venue_cap:.0%} cap)"
                            with_label += f" ({pred_with/venue_cap:.0%} cap)"
                        c1.metric("Baseline (no promos)", base_label)
                        c2.metric("With promos", with_label)
                        c3.metric("Promo lift (CF)", f"{promo_effect:+,}")
                        if demo_impact is not None:
                            c4.metric("Demographics effect", f"{demo_impact:+,}")

                        if sim_promos:
                            source_label = "team history" if context_source == "team" else f"{team_row['level_label']} history"
                            st.markdown(
                                f"**Per-promotion lift** (this game via S-learner; "
                                f"context from {source_label}):"
                            )
                            rows = []
                            for flag in sim_promos:
                                lift_here = per_promo_lift.get(flag, 0)
                                ctx = cf_context.get(flag)
                                if ctx is not None and pd.notna(ctx["mean_lift"]):
                                    avg_lift = f"{int(round(ctx['mean_lift'])):+,}"
                                    consistency = (
                                        f"{ctx['pct_positive']*100:.0f}%"
                                        if pd.notna(ctx["pct_positive"]) else "—"
                                    )
                                    n = int(ctx["n_games"]) if pd.notna(ctx["n_games"]) else 0
                                else:
                                    avg_lift = "—"
                                    consistency = "—"
                                    n = 0
                                rows.append({
                                    "Promotion":     PROMO_LABELS[flag],
                                    "Lift this game": f"{lift_here:+,}",
                                    "Avg lift (CF)": avg_lift,
                                    "Consistency":   consistency,
                                    "Games":         n,
                                })
                            st.dataframe(
                                pd.DataFrame(rows),
                                use_container_width=True,
                                hide_index=True,
                            )

                        st.caption(
                            "Baseline and promo effects both from the trained "
                            "XGBoost model (S-learner counterfactual: predict "
                            "the same game with promos off vs on). Consistency "
                            "= share of historical games where the flag raised "
                            "predicted attendance."
                        )

                    except Exception as e:
                        st.error(f"Prediction error: {e}")
                        st.caption("The model may expect features not available in the current data.")


# ==============================================================================
# TAB 4: RECOMMENDATIONS
# ==============================================================================

with tab_recs:
    st.subheader("Actionable Recommendations")

    recs_df = load_team_recommendations()

    if recs_df.empty:
        st.warning("No recommendations generated. Run `python scripts/generate_recommendations.py`.")
    else:
        # Filter to selected team or all
        if selected_team_id:
            recs_df = recs_df[recs_df["team_id"] == selected_team_id]
        else:
            recs_df = recs_df[recs_df["team_id"].isin(level_ids)]

        if recs_df.empty:
            st.info("No recommendations for the selected team/level.")
        else:
            # Summary metrics
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Recommendations", len(recs_df))
            c2.metric(
                "High Confidence",
                len(recs_df[recs_df["confidence"] == "high"]),
            )
            total_impact = recs_df["expected_impact"].dropna().sum()
            c3.metric(
                "Total Potential Impact",
                f"+{total_impact:,.0f} fans" if total_impact > 0 else "-",
            )

            # Progress across all teams currently in view
            acted_count = 0
            for tid_check in recs_df["team_id"].unique():
                acts_df = load_rec_actions(int(tid_check))
                if not acts_df.empty:
                    acted_count += len(acts_df[acts_df["status"].isin(
                        ["planned", "in_progress", "done"]
                    )])
            c4.metric(
                "Tracked (planned / in-progress / done)",
                f"{acted_count}",
                help="Count of recommendations this team has picked up via the tracker below.",
            )

            st.divider()

            # Category filter
            categories = sorted(recs_df["category"].unique())
            colA, colB = st.columns([3, 2])
            with colA:
                cat_filter = st.multiselect(
                    "Filter by category",
                    options=categories,
                    default=categories,
                    format_func=lambda c: CATEGORY_LABELS.get(c, c),
                )
            with colB:
                novelty_only = st.checkbox(
                    "Only show 'novelty' picks",
                    value=False,
                    help=(
                        "When checked, hides recs about promo categories the team "
                        "already runs often. Surfaces the 'what are we NOT doing' list."
                    ),
                )

            recs_df = recs_df[recs_df["category"].isin(cat_filter)]

            # Pre-compute team promo usage for novelty detection (per-team only)
            team_usage: dict[int, dict[str, float]] = {}
            team_actions: dict[int, pd.DataFrame] = {}
            if selected_team_id:
                team_usage[selected_team_id] = load_team_promo_usage(selected_team_id)
                team_actions[selected_team_id] = load_rec_actions(selected_team_id)

            # Display each recommendation
            for _, rec in recs_df.iterrows():
                tid = int(rec["team_id"])
                if tid not in team_usage:
                    team_usage[tid] = load_team_promo_usage(tid)
                if tid not in team_actions:
                    team_actions[tid] = load_rec_actions(tid)
                usage = team_usage[tid]
                actions = team_actions[tid]

                # Is this rec about a category the team rarely runs?
                promo_flag = _extract_promo_type(rec.get("evidence"))
                is_novelty = (
                    promo_flag is not None
                    and usage.get(promo_flag, 1.0) <= NOVELTY_THRESHOLD
                )

                if novelty_only and not is_novelty:
                    continue

                prio_int = int(rec["priority"]) if pd.notna(rec.get("priority")) else None
                prio_key = f"P{prio_int}" if prio_int else "P?"
                impact_str = (
                    f"+{rec['expected_impact']:,.0f} fans"
                    if pd.notna(rec["expected_impact"]) and rec["expected_impact"]
                    else ""
                )
                cat_label = CATEGORY_LABELS.get(rec["category"], rec["category"])

                # Team name if showing all teams
                team_prefix = ""
                if not selected_team_id:
                    tname = teams_df[teams_df["team_id"] == tid]["team_name"]
                    if not tname.empty:
                        team_prefix = f"{tname.iloc[0]}  -  "

                novelty_tag = "  [NOVELTY]" if is_novelty else ""

                # Look up any previous action recorded for this (category, title)
                existing_status = "not yet"
                existing_notes = ""
                if not actions.empty:
                    match = actions[
                        (actions["rec_category"] == rec["category"])
                        & (actions["rec_title"] == rec["title"])
                    ]
                    if not match.empty:
                        existing_status = match.iloc[0]["status"] or "not yet"
                        existing_notes = match.iloc[0]["notes"] or ""

                # Small status tag in the expander title so status is visible at a glance
                status_tag = ""
                if existing_status != "not yet":
                    status_tag = f"  -- {existing_status}"

                with st.expander(
                    f"[{prio_key}]  {team_prefix}{rec['title']}  "
                    f"({cat_label}){novelty_tag}  {impact_str}{status_tag}"
                ):
                    # Priority + confidence + novelty pills
                    pills = [priority_pill(prio_key)]
                    conf = (rec.get("confidence") or "").lower() if pd.notna(rec.get("confidence")) else ""
                    if conf in CONFIDENCE_COLORS:
                        c = CONFIDENCE_COLORS[conf]
                        pills.append(
                            f'<span style="background:{c};color:white;padding:2px 8px;'
                            f'border-radius:10px;font-size:0.8em">{conf} confidence</span>'
                        )
                    if is_novelty:
                        pct = usage.get(promo_flag, 0.0)
                        label_pretty = PROMO_LABELS.get(promo_flag, promo_flag or "")
                        pills.append(
                            f'<span style="background:#b064a0;color:white;padding:2px 8px;'
                            f'border-radius:10px;font-size:0.8em">novelty: '
                            f'{label_pretty} used {pct:.0%} of games</span>'
                        )
                    st.markdown(" ".join(pills), unsafe_allow_html=True)

                    st.markdown(rec["detail"])

                    if pd.notna(rec.get("evidence")):
                        try:
                            evidence = rec["evidence"] if isinstance(rec["evidence"], dict) else json.loads(rec["evidence"])
                            with st.container():
                                st.json(evidence)
                        except (json.JSONDecodeError, TypeError):
                            pass

                    # -- Feedback loop: record what was done about this rec ----
                    if is_read_only():
                        # Deployed demo uses a read-only Parquet snapshot — no
                        # persistent writes available. Show current status but
                        # skip the editor.
                        if existing_status:
                            st.divider()
                            st.caption(f"Status (read-only): **{existing_status}**"
                                       + (f" — {existing_notes}" if existing_notes else ""))
                    else:
                        st.divider()
                        st.markdown("**Track this recommendation**")
                        action_key = f"act_{tid}_{rec['category']}_{rec['title']}"
                        col_s, col_n = st.columns([1, 2])
                        with col_s:
                            new_status = st.selectbox(
                                "Status",
                                ACTION_STATUSES,
                                index=ACTION_STATUSES.index(existing_status) if existing_status in ACTION_STATUSES else 0,
                                key=f"status_{action_key}",
                            )
                        with col_n:
                            new_notes = st.text_input(
                                "Notes (what did you do?)",
                                value=existing_notes,
                                key=f"notes_{action_key}",
                                placeholder="e.g. Added to August calendar, first test weekend",
                            )
                        if st.button("Save", key=f"save_{action_key}"):
                            save_rec_action(tid, rec["category"], rec["title"], new_status, new_notes)
                            st.success(f"Saved as '{new_status}'.")
                            st.rerun()


# ==============================================================================
# TAB 5: MODEL PERFORMANCE
# ==============================================================================

with tab_model:
    st.subheader("XGBoost Model Diagnostics")

    # External factors accounting
    with st.expander("What external factors does the model account for?", expanded=False):
        st.markdown("""
**Controlled for in the model (via game_features):**

| Factor | How it's captured | Feature(s) |
|--------|-------------------|------------|
| Weather | Daily temp, precip, wind from game data | `temp_max_f`, `precip_inches`, `wind_max_mph`, `weather_bucket` |
| Market size | Census Bureau ACS demographics | `msa_population`, `place_population` |
| Income / poverty | Census Bureau ACS demographics | `median_income`, `poverty_rate` |
| Venue size | Venue capacity (normalizes team baseline) | `venue_capacity`, `team_id` (learns per-team baseline) |
| School calendar | State-level school break lookup | `school_in_session` |
| Day/time | Game scheduling context | `day_of_week`, `month`, `is_weekend`, `day_night` |
| Opponent draw | Historical avg attendance when opponent visits | `opponent_hist_draw`, `distance_miles`, `is_same_division` |
| Team momentum | Cumulative season performance | `win_pct_entering`, `streak`, `prior_game_attendance` |
| Homestand fatigue | Position within a homestand | `homestand_game_number`, `homestand_length`, `days_since_last_home` |
| Rehab players | MLB player rehabbing with team | `has_rehab_player` |
| Season timing | How far into the season | `season_progress`, `game_number_in_season` |

**Not yet captured (potential improvements):**

| Factor | Why it matters | Possible data source |
|--------|---------------|---------------------|
| Local competing events | Concerts, festivals, college football | Event APIs, manual calendar |
| Gas prices | Travel cost to ballpark | EIA data |
| Ticket pricing | Dynamic pricing affects demand | Team ticketing systems |
| TV broadcast conflicts | Nationally televised MLB games | MLB schedule API |
| Team roster changes | Star callups/demotions mid-season | Transactions data (partially via rehab) |
| Marketing spend | Ad campaigns, group sales effort | Internal team data |

The model explains 75-83% of attendance variance (R-squared). The remaining 17-25% is likely
a mix of these unmeasured factors plus inherent randomness.
""")

    model_runs = load_model_runs()
    if model_runs.empty:
        st.warning("No model runs found. Run `python scripts/train_attendance_model.py`.")
    else:
        # Model metrics summary
        st.markdown("**Model Performance Summary**")
        summary = model_runs[["sport_id", "mae", "mape", "rmse", "r_squared",
                              "n_train", "n_val"]].copy()
        summary["Level"] = summary["sport_id"].map(LEVEL_ORDER)
        summary = summary[["Level", "mae", "mape", "rmse", "r_squared", "n_train", "n_val"]]
        summary.columns = ["Level", "MAE", "MAPE", "RMSE", "R-squared", "Train N", "Val N"]
        summary["MAE"] = summary["MAE"].apply(lambda v: f"{v:,.0f}")
        summary["MAPE"] = summary["MAPE"].apply(lambda v: f"{v:.1%}")
        summary["RMSE"] = summary["RMSE"].apply(lambda v: f"{v:,.0f}")
        summary["R-squared"] = summary["R-squared"].apply(lambda v: f"{v:.4f}")
        summary["Train N"] = summary["Train N"].apply(lambda v: f"{v:,}")
        summary["Val N"] = summary["Val N"].apply(lambda v: f"{v:,}")
        st.dataframe(summary, use_container_width=True, hide_index=True)

        st.divider()

        # Level selector for detailed view
        detail_level = st.selectbox(
            "Detailed view for level",
            options=list(LEVEL_ORDER.values()),
            index=1,  # Default Double-A
        )
        detail_sid = {v: k for k, v in LEVEL_ORDER.items()}[detail_level]

        # Feature importance
        fi_df = load_feature_importance()
        level_fi = fi_df[fi_df["sport_id"] == detail_sid].head(20)

        if not level_fi.empty:
            st.markdown(f"**Top 20 Features ({detail_level}) - SHAP Importance**")
            fig_fi = px.bar(
                level_fi.sort_values("shap_mean_abs"),
                x="shap_mean_abs",
                y="feature_name",
                orientation="h",
                color_discrete_sequence=["#3498db"],
                labels={"shap_mean_abs": "Mean |SHAP|", "feature_name": ""},
                height=max(400, len(level_fi) * 25),
            )
            fig_fi.update_layout(margin=dict(t=10, b=20, l=140))
            st.plotly_chart(fig_fi, use_container_width=True)

        # Actual vs Predicted scatter
        preds_df = load_predictions()
        level_preds = preds_df[preds_df["sport_id"] == detail_sid]

        if selected_team_id:
            level_preds = level_preds[level_preds["team_id"] == selected_team_id]

        if not level_preds.empty:
            st.markdown(f"**Actual vs Predicted ({detail_level})**")
            fig_scatter = px.scatter(
                level_preds,
                x="predicted_attendance",
                y="attendance",
                opacity=0.4,
                color_discrete_sequence=["#3498db"],
                labels={
                    "predicted_attendance": "Predicted",
                    "attendance": "Actual",
                },
                height=450,
            )
            # Perfect prediction line
            max_val = max(level_preds["attendance"].max(), level_preds["predicted_attendance"].max())
            fig_scatter.add_trace(go.Scatter(
                x=[0, max_val], y=[0, max_val],
                mode="lines",
                line=dict(dash="dash", color=RED),
                name="Perfect prediction",
            ))
            fig_scatter.update_traces(marker_size=4)
            fig_scatter.update_layout(margin=dict(t=10, b=20))
            st.plotly_chart(fig_scatter, use_container_width=True)

            # Residual distribution
            st.markdown("**Residual Distribution (Actual - Predicted)**")
            fig_hist = px.histogram(
                level_preds,
                x="residual",
                nbins=60,
                color_discrete_sequence=["#3498db"],
                labels={"residual": "Residual (Actual - Predicted)"},
                height=350,
            )
            fig_hist.add_vline(x=0, line_dash="dash", line_color=RED)
            fig_hist.update_layout(margin=dict(t=10, b=20))
            st.plotly_chart(fig_hist, use_container_width=True)

            # Worst predictions table
            with st.expander("Largest prediction errors"):
                worst = level_preds.nlargest(15, "residual", keep="first")[
                    ["game_date", "team_id", "attendance", "predicted_attendance", "residual"]
                ].copy()
                worst = worst.merge(
                    teams_df[["team_id", "team_name"]], on="team_id", how="left"
                )
                worst["game_date"] = pd.to_datetime(worst["game_date"]).dt.strftime("%Y-%m-%d")
                display_worst = worst[["game_date", "team_name", "attendance",
                                       "predicted_attendance", "residual"]]
                display_worst.columns = ["Date", "Team", "Actual", "Predicted", "Residual"]
                st.dataframe(display_worst, use_container_width=True, hide_index=True)


# ── Cross-page navigation + footer ───────────────────────────────────────────
see_also([
    ("Team Report",      "pages/8_Team_Report.py",      "the written brief for this team"),
    ("Competitive Intel","pages/9_Competitive_Intel.py","peer comparisons and momentum"),
    ("Promo Strategy",   "pages/7_Promo_Strategy.py",   "archetype context behind the recs"),
])
render_footer(scripts=["promo_lift", "recommendations", "cluster_peers"])
