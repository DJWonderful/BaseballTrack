"""Promotional strategy profiles and clustering.

Groups MiLB teams by their promotional philosophy -- how they use
fireworks, giveaways, food deals, theme nights, recurring series,
etc.  Orthogonal to market-based peer clusters (demographics).
"""

# ── Path setup ────────────────────────────────────────────────────────────────
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils.db import query_df
from utils.footer import render_footer
from utils.navigation import see_also

st.set_page_config(page_title="Promo Strategy | MiLB", page_icon="Strategy", layout="wide")

LEVEL_ORDER = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}

GREEN = "#2ecc71"
RED = "#e74c3c"

# Dimension labels for radar charts and display
FEATURE_COLS = [
    "promo_coverage",
    "promos_per_promo_game",
    "pct_recurring",
    "promo_entropy",
    "pct_giveaway",
    "pct_fireworks",
    "pct_food_deal",
    "pct_theme_night",
    "pct_weekend_promos",
    "pct_kids_event",
]

FEATURE_LABELS = {
    "promo_coverage":       "Coverage",
    "promos_per_promo_game": "Stacking",
    "pct_recurring":        "Recurring %",
    "promo_entropy":        "Diversity",
    "pct_giveaway":         "Giveaway %",
    "pct_fireworks":        "Fireworks %",
    "pct_food_deal":        "Food Deal %",
    "pct_theme_night":      "Theme Night %",
    "pct_weekend_promos":   "Weekend %",
    "pct_kids_event":       "Kids Event %",
}

DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DOW_COLS = ["mon_promos", "tue_promos", "wed_promos", "thu_promos",
            "fri_promos", "sat_promos", "sun_promos"]


# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=600)
def load_promo_profiles() -> pd.DataFrame:
    return query_df("""
        SELECT p.*, s.sport_name AS level
        FROM milb.v_team_promo_profile p
        LEFT JOIN milb.sports s ON p.sport_id = s.sport_id
    """)


@st.cache_data(ttl=600)
def load_promo_clusters() -> pd.DataFrame:
    return query_df("SELECT * FROM milb.team_promo_clusters")


@st.cache_data(ttl=600)
def load_dayofweek() -> pd.DataFrame:
    return query_df("SELECT * FROM milb.v_team_promo_dayofweek")


@st.cache_data(ttl=600)
def load_intensity() -> pd.DataFrame:
    return query_df("SELECT * FROM milb.v_team_promo_intensity")


@st.cache_data(ttl=600)
def load_cluster_descriptions() -> pd.DataFrame:
    return query_df("SELECT * FROM milb.promo_cluster_descriptions")


@st.cache_data(ttl=600)
def load_team_attendance() -> pd.DataFrame:
    return query_df("""
        SELECT team_id,
               AVG(attendance)::int AS avg_attendance,
               AVG(capacity_utilization) AS avg_cap_util
        FROM milb.game_features
        WHERE season = (SELECT MAX(season) FROM milb.game_features)
          AND game_type = 'R'
        GROUP BY team_id
    """)


@st.cache_data(ttl=600)
def load_top_promos(team_id: int) -> pd.DataFrame:
    return query_df(f"""
        SELECT p.offer_name,
               COUNT(*) AS occurrences,
               MAX(g.game_date::date) AS last_used,
               p.promo_category
        FROM milb.game_promotions p
        JOIN milb.games g ON p.game_pk = g.game_pk
        WHERE g.home_team_id = {team_id}
          AND p.enrichment_method IS NOT NULL
          AND g.game_type = 'R'
        GROUP BY p.offer_name, p.promo_category
        ORDER BY occurrences DESC
        LIMIT 20
    """)


# ── Load data ─────────────────────────────────────────────────────────────────

profiles = load_promo_profiles()
clusters = load_promo_clusters()
cluster_descs = load_cluster_descriptions()
dow_data = load_dayofweek()
intensity = load_intensity()
attendance = load_team_attendance()

# Merge cluster labels into profiles
if not clusters.empty:
    profiles = profiles.merge(
        clusters[["team_id", "promo_cluster_id", "promo_cluster_label"]],
        on="team_id", how="left",
    )
else:
    profiles["promo_cluster_id"] = None
    profiles["promo_cluster_label"] = "Unclustered"

# Merge attendance
profiles = profiles.merge(attendance, on="team_id", how="left")


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Filters")

    # Level filter
    selected_levels = st.multiselect(
        "Level",
        options=list(LEVEL_ORDER.values()),
        default=list(LEVEL_ORDER.values()),
    )

    level_ids = set()
    for sid, name in LEVEL_ORDER.items():
        if name in selected_levels:
            level_ids.add(sid)

    # Quality filter
    show_low = st.checkbox("Include low-quality teams (<30 promos)", value=False)

    st.divider()

    # Team selector for profile tab
    filtered = profiles[profiles["sport_id"].isin(level_ids)].copy()
    if not show_low:
        filtered = filtered[filtered["promo_quality"] == "normal"]

    team_options = ["-- Overview --"] + sorted(filtered["team_name"].tolist())
    _default_idx = team_options.index("Binghamton Rumble Ponies") if "Binghamton Rumble Ponies" in team_options else 0
    selected_team = st.selectbox("Team (Profile tab)", team_options, index=_default_idx)


# ── Apply filters ─────────────────────────────────────────────────────────────

df = filtered.copy()

if df.empty:
    st.warning("No teams match the selected filters.")
    st.stop()


# ── Page header ───────────────────────────────────────────────────────────────

st.title("Promotional Strategy Analysis")
st.caption(
    "Teams grouped by how they promote -- coverage, mix, diversity, "
    "and recurring patterns.  Based on 2025 LLM-enriched promotion data."
)


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_overview, tab_profile, tab_intensity, tab_deep = st.tabs([
    "Strategy Overview", "Team Profiles", "Intensity Tiers", "Cluster Deep Dive",
])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 1: Strategy Overview
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_overview:
    clustered = df[df["promo_cluster_label"].notna()].copy()

    if clustered.empty:
        st.warning("No cluster data available. Run `scripts/cluster_promo_strategy.py` first.")
        st.stop()

    # Metrics
    n_teams = len(clustered)
    n_clusters = clustered["promo_cluster_label"].nunique()
    most_common = clustered["promo_cluster_label"].value_counts().index[0]
    avg_coverage = clustered["promo_coverage"].mean()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Teams Analyzed", n_teams)
    c2.metric("Strategy Clusters", n_clusters)
    c3.metric("Largest Cluster", most_common)
    c4.metric("Avg Coverage", f"{avg_coverage:.0%}")

    st.divider()

    # Scatter plot: coverage vs entropy colored by cluster
    st.subheader("Strategy landscape")
    st.caption(
        "Each dot is a team. X-axis = what fraction of home games have a promotion. "
        "Y-axis = Shannon entropy of promo names (higher = more diverse programming). "
        "Color = strategy cluster assignment."
    )

    fig_scatter = px.scatter(
        clustered,
        x="promo_coverage",
        y="promo_entropy",
        color="promo_cluster_label",
        size="total_promos",
        hover_name="team_name",
        hover_data={"promo_coverage": ":.0%", "promo_entropy": ":.2f",
                    "total_promos": True, "promo_cluster_label": True},
        labels={
            "promo_coverage": "Promo Coverage (% home games)",
            "promo_entropy": "Promo Diversity (Shannon Entropy)",
            "promo_cluster_label": "Strategy Cluster",
            "total_promos": "Total Promos",
        },
        height=500,
    )
    fig_scatter.update_layout(
        legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        margin={"t": 20, "b": 10},
    )
    st.plotly_chart(fig_scatter, use_container_width=True)

    # Cluster summary table
    st.subheader("Cluster profiles")
    st.caption("Average values for key dimensions within each cluster.")

    summary_cols = ["promo_cluster_label", "promo_coverage", "promos_per_promo_game",
                    "pct_recurring", "promo_entropy", "pct_giveaway", "pct_fireworks",
                    "pct_food_deal", "pct_theme_night", "pct_weekend_promos", "pct_kids_event"]

    cluster_summary = (
        clustered
        .groupby("promo_cluster_label")[summary_cols[1:]]
        .agg(["mean", "count"])
    )
    # Flatten multi-index columns
    display_summary = pd.DataFrame()
    display_summary["Cluster"] = cluster_summary.index
    display_summary["Teams"] = cluster_summary[("promo_coverage", "count")].values
    for col in summary_cols[1:]:
        label = FEATURE_LABELS.get(col, col)
        vals = cluster_summary[(col, "mean")].values
        if col == "promo_entropy" or col == "promos_per_promo_game":
            display_summary[label] = [f"{v:.2f}" for v in vals]
        else:
            display_summary[label] = [f"{v:.0%}" for v in vals]

    display_summary = display_summary.sort_values("Teams", ascending=False)
    st.dataframe(display_summary, use_container_width=True, hide_index=True)

    # Cluster descriptions
    if not cluster_descs.empty:
        st.subheader("What each cluster means")
        for _, desc_row in cluster_descs.sort_values("promo_cluster_id").iterrows():
            label = desc_row["promo_cluster_label"]
            n = len(clustered[clustered["promo_cluster_label"] == label])
            st.markdown(f"**{label}** ({n} teams)")
            st.caption(desc_row["description"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 2: Team Profiles
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_profile:
    if selected_team == "-- Overview --":
        st.info("Select a team in the sidebar to see its promotional profile.")
        st.stop()

    team_row = df[df["team_name"] == selected_team]
    if team_row.empty:
        st.warning(f"No profile data for {selected_team}.")
        st.stop()

    team_row = team_row.iloc[0]
    team_id = int(team_row["team_id"])
    cluster_label = team_row.get("promo_cluster_label", "Unknown")

    st.subheader(f"{selected_team}")
    st.caption(f"Strategy cluster: **{cluster_label}** | "
               f"Total promos: {int(team_row['total_promos'])} | "
               f"Coverage: {team_row['promo_coverage']:.0%}")

    if not cluster_descs.empty:
        desc_match = cluster_descs[cluster_descs["promo_cluster_label"] == cluster_label]
        if not desc_match.empty:
            st.info(desc_match.iloc[0]["description"])

    # Radar chart: team vs cluster avg vs league avg
    st.subheader("Strategy profile")
    st.caption(
        "Each axis represents a promotional dimension. "
        "Values are min-max normalized (0-1) across all teams for comparability."
    )

    # Normalize features to 0-1
    normal_df = df[df["promo_quality"] == "normal"]
    mins = normal_df[FEATURE_COLS].min()
    maxs = normal_df[FEATURE_COLS].max()
    ranges = (maxs - mins).replace(0, 1)

    team_vals = [(team_row[c] - mins[c]) / ranges[c] for c in FEATURE_COLS]
    league_vals = [(normal_df[c].mean() - mins[c]) / ranges[c] for c in FEATURE_COLS]

    # Cluster average
    if pd.notna(team_row.get("promo_cluster_label")):
        cluster_peers = normal_df[normal_df["promo_cluster_label"] == cluster_label]
        cluster_vals = [(cluster_peers[c].mean() - mins[c]) / ranges[c] for c in FEATURE_COLS]
    else:
        cluster_vals = league_vals

    theta_labels = [FEATURE_LABELS[c] for c in FEATURE_COLS]

    fig_radar = go.Figure()
    fig_radar.add_trace(go.Scatterpolar(
        r=team_vals + [team_vals[0]],
        theta=theta_labels + [theta_labels[0]],
        name=selected_team,
        fill="toself",
        fillcolor="rgba(46, 204, 113, 0.15)",
        line=dict(color=GREEN, width=2),
    ))
    fig_radar.add_trace(go.Scatterpolar(
        r=cluster_vals + [cluster_vals[0]],
        theta=theta_labels + [theta_labels[0]],
        name=f"Cluster: {cluster_label}",
        line=dict(color="#3498db", width=2, dash="dash"),
    ))
    fig_radar.add_trace(go.Scatterpolar(
        r=league_vals + [league_vals[0]],
        theta=theta_labels + [theta_labels[0]],
        name="League Average",
        line=dict(color="#95a5a6", width=1, dash="dot"),
    ))
    fig_radar.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
        height=450,
        margin={"t": 30, "b": 30},
        legend=dict(orientation="h", yanchor="bottom", y=-0.15),
    )
    st.plotly_chart(fig_radar, use_container_width=True)

    # Day-of-week distribution
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Day-of-week distribution")
        team_dow = dow_data[dow_data["team_id"] == team_id]
        if not team_dow.empty:
            dow_row = team_dow.iloc[0]
            dow_vals = [int(dow_row[c]) for c in DOW_COLS]
            dow_df = pd.DataFrame({"Day": DOW_LABELS, "Promos": dow_vals})
            fig_dow = px.bar(
                dow_df, x="Day", y="Promos",
                color_discrete_sequence=[GREEN],
                text="Promos",
                height=300,
            )
            fig_dow.update_traces(textposition="outside")
            fig_dow.update_layout(margin={"t": 10, "b": 10}, showlegend=False)
            st.plotly_chart(fig_dow, use_container_width=True)
        else:
            st.info("No day-of-week data for this team.")

    with col_right:
        st.subheader("Top promotions")
        top_promos = load_top_promos(team_id)
        if not top_promos.empty:
            st.dataframe(
                top_promos.rename(columns={
                    "offer_name": "Promotion",
                    "occurrences": "Times",
                    "last_used": "Last Used",
                    "promo_category": "Category",
                }),
                use_container_width=True,
                hide_index=True,
                height=300,
            )
        else:
            st.info("No promotion data for this team.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 3: Intensity Tiers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_intensity:
    tier_df = intensity[intensity["sport_id"].isin(level_ids)].copy()

    # Merge attendance
    tier_df = tier_df.merge(attendance, on="team_id", how="left")

    tier_colors = {"High": GREEN, "Medium": "#f39c12", "Low": "#e74c3c", "None": "#95a5a6"}

    st.subheader("Teams ranked by promotional intensity")
    st.caption(
        "Promos per home game, colored by tier (High/Medium/Low). "
        "Tiers are based on percentile breakpoints across all teams."
    )

    ranked = tier_df[tier_df["intensity_tier"] != "None"].sort_values("promos_per_game")

    fig_rank = px.bar(
        ranked,
        y="team_name",
        x="promos_per_game",
        color="intensity_tier",
        color_discrete_map=tier_colors,
        orientation="h",
        labels={"team_name": "", "promos_per_game": "Promos per Home Game",
                "intensity_tier": "Tier"},
        height=max(500, len(ranked) * 18),
    )
    fig_rank.update_layout(
        yaxis={"categoryorder": "total ascending"},
        margin={"t": 20, "b": 20, "l": 180},
        legend=dict(orientation="h", yanchor="bottom", y=-0.05),
    )
    st.plotly_chart(fig_rank, use_container_width=True)

    # Box plot: attendance by tier
    st.subheader("Attendance by promotional intensity")
    st.caption(
        "Do teams that promote more heavily draw more fans? "
        "Box plots show attendance distribution for each tier."
    )

    tier_att = tier_df[tier_df["avg_attendance"].notna() & (tier_df["intensity_tier"] != "None")]

    if not tier_att.empty:
        fig_box = px.box(
            tier_att,
            x="intensity_tier",
            y="avg_attendance",
            color="intensity_tier",
            color_discrete_map=tier_colors,
            category_orders={"intensity_tier": ["Low", "Medium", "High"]},
            labels={"intensity_tier": "Intensity Tier",
                    "avg_attendance": "Avg Attendance (2025)"},
            points="all",
            height=400,
        )
        fig_box.update_layout(showlegend=False, margin={"t": 20, "b": 20})
        st.plotly_chart(fig_box, use_container_width=True)
    else:
        st.info("No attendance data available for tier comparison.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 4: Cluster Deep Dive
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_deep:
    clustered = df[df["promo_cluster_label"].notna()].copy()

    if clustered.empty:
        st.warning("No cluster data available.")
        st.stop()

    cluster_options = sorted(clustered["promo_cluster_label"].unique())
    selected_cluster = st.selectbox("Select Cluster", cluster_options)

    cluster_teams = clustered[clustered["promo_cluster_label"] == selected_cluster]
    league_means = clustered[FEATURE_COLS].mean()
    cluster_means = cluster_teams[FEATURE_COLS].mean()

    st.subheader(f"{selected_cluster} ({len(cluster_teams)} teams)")

    # Show cluster description
    if not cluster_descs.empty:
        desc_match = cluster_descs[cluster_descs["promo_cluster_label"] == selected_cluster]
        if not desc_match.empty:
            st.info(desc_match.iloc[0]["description"])

    # Diverging bar: deviation from league average
    st.caption(
        "How this cluster differs from the league average on each dimension. "
        "Green = above average, red = below."
    )

    deviations = cluster_means - league_means
    dev_df = pd.DataFrame({
        "dimension": [FEATURE_LABELS[c] for c in FEATURE_COLS],
        "deviation": [deviations[c] for c in FEATURE_COLS],
    }).sort_values("deviation")

    dev_df["bar_color"] = dev_df["deviation"].apply(lambda v: GREEN if v >= 0 else RED)

    # Format for display
    dev_df["dev_label"] = dev_df["deviation"].apply(
        lambda v: f"{v:+.1%}" if abs(v) < 1 else f"{v:+.2f}"
    )

    fig_dev = px.bar(
        dev_df,
        x="deviation",
        y="dimension",
        orientation="h",
        text="dev_label",
        color="bar_color",
        color_discrete_map="identity",
        labels={"deviation": "Difference from League Average", "dimension": ""},
        height=max(350, len(dev_df) * 35),
    )
    fig_dev.update_traces(textposition="outside")
    fig_dev.update_layout(
        showlegend=False,
        margin={"t": 20, "b": 20, "l": 130},
        xaxis_zeroline=True,
        xaxis_zerolinewidth=2,
        xaxis_zerolinecolor="#333",
    )
    st.plotly_chart(fig_dev, use_container_width=True)

    # Team list
    st.subheader("Teams in this cluster")

    display_cols = ["team_name", "level", "total_promos", "promo_coverage",
                    "promos_per_promo_game", "promo_entropy"]
    if "avg_attendance" in cluster_teams.columns:
        display_cols.append("avg_attendance")

    team_display = cluster_teams[display_cols].copy()
    team_display = team_display.rename(columns={
        "team_name": "Team",
        "level": "Level",
        "total_promos": "Promos",
        "promo_coverage": "Coverage",
        "promos_per_promo_game": "Stacking",
        "promo_entropy": "Diversity",
        "avg_attendance": "Avg Att",
    })
    team_display = team_display.sort_values("Promos", ascending=False)

    st.dataframe(team_display, use_container_width=True, hide_index=True)


# ── Cross-page navigation + footer ───────────────────────────────────────────
see_also([
    ("Promotions",       "pages/2_Promotions.py",       "promo lift and stacking details"),
    ("Competitive Intel","pages/9_Competitive_Intel.py","compare your strategy to peer teams"),
    ("Recommendations",  "pages/10_Recommendations.py", "prioritized promo-strategy actions"),
])
render_footer(scripts=["cluster_promo_strategy"])
