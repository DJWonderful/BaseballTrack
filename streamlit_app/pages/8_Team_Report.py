"""Executive team report with LLM-generated narrative summaries.

Displays a polished, one-page executive brief per team with KPIs,
goals, risks, and supporting charts. Group rollup tabs provide
level, cluster, and league-wide context.

Tabs:
  1. Team Report      - Full executive brief for selected team
  2. Level Overview    - Aggregate view of the team's classification level
  3. Peer Group        - Market cluster comparison
  4. Promo Strategy    - Promo cluster comparison
  5. League Wide       - All-MiLB summary
"""

# -- Path setup ---------------------------------------------------------------
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.db import query_df
from utils.footer import render_footer
from utils.navigation import see_also
from utils.season_compare import season_delta_metric
from utils.theme import (
    SEASON_COLORS, POSITIVE, NEGATIVE, NEUTRAL,
    PRIORITY_COLORS, priority_pill, momentum_pill,
)

st.set_page_config(page_title="Team Report | MiLB", page_icon="Report", layout="wide")

LEVEL_ORDER = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}
HERO_TEAM = "Binghamton Rumble Ponies"   # team that gets a full LLM brief

# Legacy color aliases -- kept for backwards-compat within this file.
GREEN = POSITIVE
RED = NEGATIVE
GREY = NEUTRAL

CATEGORY_LABELS = {
    "promo_roi": "Promotion ROI",
    "peer_gap": "Peer Gap",
    "scheduling": "Scheduling",
    "anomaly": "Anomaly",
    "promo_peer": "Promo Peer",
    "strategy_mismatch": "Strategy Mismatch",
    "cluster_opportunity": "Cluster Opportunity",
    "dow_strategy": "Day-of-Week",
}

CONFIDENCE_COLORS = {"high": POSITIVE, "medium": "#f39c12", "low": NEGATIVE}


# -- Data loaders -------------------------------------------------------------

@st.cache_data(ttl=600)
def load_teams():
    return query_df("""
        SELECT t.team_id, t.team_name, t.sport_id,
               s.sport_name,
               tc.cluster_label,
               pcd.promo_cluster_label AS promo_cluster_label
        FROM milb.teams t
        JOIN milb.sports s ON t.sport_id = s.sport_id
        LEFT JOIN milb.team_clusters tc ON t.team_id = tc.team_id
        LEFT JOIN milb.team_promo_clusters tpc ON t.team_id = tpc.team_id
        LEFT JOIN milb.promo_cluster_descriptions pcd
            ON tpc.promo_cluster_id = pcd.promo_cluster_id
        WHERE t.sport_id IN (11, 12, 13, 14)
        ORDER BY t.sport_id, t.team_name
    """)


@st.cache_data(ttl=600)
def load_team_narrative(team_id: int, season: int):
    df = query_df(f"""
        SELECT narrative_text, kpi_json, goals_json, risks_json, llm_model, generated_at
        FROM milb.team_narratives
        WHERE team_id = {team_id} AND season = {season}
    """)
    return df.iloc[0] if not df.empty else None


@st.cache_data(ttl=600)
def load_group_narrative(group_type: str, group_key: str, season: int):
    df = query_df("""
        SELECT narrative_text, kpi_json, llm_model, generated_at
        FROM milb.group_narratives
        WHERE group_type = :gtype AND group_key = :gkey AND season = :season
    """, params={"gtype": group_type, "gkey": group_key, "season": season})
    return df.iloc[0] if not df.empty else None


@st.cache_data(ttl=600)
def load_team_recs(team_id: int):
    return query_df(f"""
        SELECT priority, category, title, detail, expected_impact,
               confidence, evidence
        FROM milb.team_recommendations
        WHERE team_id = {team_id}
        ORDER BY priority
    """)


@st.cache_data(ttl=600)
def load_attendance_trend(team_id: int):
    return query_df(f"""
        SELECT season, month,
               AVG(attendance)::int                      AS avg_att,
               AVG(capacity_utilization)::float          AS avg_cap_util,
               AVG(promo_count::float)                   AS avg_promo_count,
               COUNT(*)                                  AS games
        FROM milb.game_features
        WHERE team_id = {team_id} AND attendance IS NOT NULL AND game_type = 'R'
        GROUP BY season, month
        ORDER BY season, month
    """)


@st.cache_data(ttl=600)
def load_available_seasons():
    df = query_df("SELECT DISTINCT season FROM milb.game_features ORDER BY season DESC")
    return df["season"].tolist() if not df.empty else []


@st.cache_data(ttl=600)
def load_momentum_kpis(team_id: int, season: int):
    """Fallback KPI row for teams that don't have an LLM-generated narrative."""
    return query_df(f"""
        SELECT avg_attendance, avg_cap_util, yoy_attendance_pct,
               momentum_label, first_half_avg_att, second_half_avg_att
          FROM milb.team_momentum
         WHERE team_id = {team_id} AND season = {season}
    """)


@st.cache_data(ttl=600)
def load_group_team_stats(group_type: str, group_key: str, season: int):
    """Load per-team stats for a group, used for ranking charts."""
    if group_type == "level":
        sid_map = {v: k for k, v in LEVEL_ORDER.items()}
        sid = sid_map.get(group_key)
        if sid is None:
            return pd.DataFrame()
        where = f"t.sport_id = {sid}"
    elif group_type == "market_cluster":
        where = f"tc.cluster_label = '{group_key}'"
    elif group_type == "promo_cluster":
        where = f"pcd.promo_cluster_label = '{group_key}'"
    else:
        where = "t.sport_id IN (11,12,13,14)"

    return query_df(f"""
        SELECT t.team_id, t.team_name,
               AVG(gf.attendance)::int AS avg_att,
               AVG(gf.capacity_utilization)::float AS avg_cap_util,
               COUNT(*) AS games
        FROM milb.game_features gf
        JOIN milb.teams t ON gf.team_id = t.team_id
        LEFT JOIN milb.team_clusters tc ON t.team_id = tc.team_id
        LEFT JOIN milb.team_promo_clusters tpc ON t.team_id = tpc.team_id
        LEFT JOIN milb.promo_cluster_descriptions pcd
            ON tpc.promo_cluster_id = pcd.promo_cluster_id
        WHERE gf.season = {season} AND gf.attendance IS NOT NULL AND {where}
        GROUP BY t.team_id, t.team_name
        ORDER BY avg_cap_util DESC
    """)


# -- Helper renderers ---------------------------------------------------------

def parse_json_col(val):
    """Parse a JSONB column that might be a string or already a list/dict."""
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


def render_kpis(kpis_raw):
    """Render KPI metric cards from kpi_json."""
    kpis = parse_json_col(kpis_raw)
    if not kpis:
        return
    cols = st.columns(len(kpis))
    for col, kpi in zip(cols, kpis):
        trend = kpi.get("trend", "stable")
        context = kpi.get("context", "")
        delta_color = "normal" if trend == "up" else ("inverse" if trend == "down" else "off")
        col.metric(
            kpi.get("label", ""),
            kpi.get("value", ""),
            delta=context if context else None,
            delta_color=delta_color,
        )


def render_goals(goals_raw):
    """Render goals with progress bars."""
    goals = parse_json_col(goals_raw)
    if not goals:
        return
    st.subheader("Goals and Targets")
    for goal in goals:
        target = goal.get("target", 0)
        current = goal.get("current", 0)
        if target and target > 0:
            progress_val = min(current / target, 1.0)
            st.progress(progress_val, text=f"{goal.get('goal', '')} ({current:.2f}/{target:.2f})"
                        if isinstance(current, float) else
                        f"{goal.get('goal', '')} ({current}/{target})")
        else:
            st.markdown(f"- {goal.get('goal', '')}")


def render_risks(risks_raw):
    """Render key risks as bullet list."""
    risks = parse_json_col(risks_raw)
    if not risks:
        return
    st.subheader("Key Risks")
    for risk in risks:
        st.markdown(f"- {risk}")


def render_fallback_kpis(team_id: int, season: int):
    """KPI row computed directly from team_momentum when no narrative exists."""
    km = load_momentum_kpis(team_id, season)
    if km.empty:
        st.caption("No momentum snapshot yet for this team.")
        return
    k = km.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Avg Attendance",
              f"{int(k['avg_attendance']):,}" if pd.notna(k.get('avg_attendance')) else "-")
    c2.metric("Capacity Utilization",
              f"{float(k['avg_cap_util']):.0%}" if pd.notna(k.get('avg_cap_util')) else "-")
    yoy = k.get("yoy_attendance_pct")
    c3.metric("YoY Change",
              f"{float(yoy):+.1%}" if pd.notna(yoy) else "-")
    label = k.get("momentum_label") or "-"
    c4.metric("Momentum", label)


def render_recommendations(recs_df):
    """Render recommendation list with priority pills + confidence chips."""
    if recs_df.empty:
        st.info("No recommendations available.")
        return

    with st.expander(f"Detailed Recommendations ({len(recs_df)} total)", expanded=False):
        for _, rec in recs_df.iterrows():
            prio_int = int(rec["priority"]) if pd.notna(rec.get("priority")) else None
            prio_key = f"P{prio_int}" if prio_int else "P?"
            impact_str = (
                f"+{rec['expected_impact']:,.0f} fans"
                if pd.notna(rec.get("expected_impact")) else ""
            )
            cat_label = CATEGORY_LABELS.get(rec["category"], rec["category"])

            header = f"[{prio_key}]  {rec['title']}  ({cat_label})"
            if impact_str:
                header += f"  -- {impact_str}"

            with st.expander(header):
                # Colored priority + confidence pills at the top
                pill_html = priority_pill(prio_key)
                conf = (rec.get("confidence") or "").lower()
                if conf in CONFIDENCE_COLORS:
                    c = CONFIDENCE_COLORS[conf]
                    pill_html += (
                        f' <span style="background:{c};color:white;padding:2px 8px;'
                        f'border-radius:10px;font-size:0.8em">{conf} confidence</span>'
                    )
                st.markdown(pill_html, unsafe_allow_html=True)
                st.markdown(rec["detail"])


def render_group_tab(group_type: str, group_key: str, season: int,
                     highlight_team_id: int | None = None):
    """Render a group rollup tab."""
    narrative = load_group_narrative(group_type, group_key, season)

    if narrative is None:
        st.warning(f"No narrative generated for this group yet. "
                   f"Run: `python scripts/generate_narratives.py`")
        return

    # KPIs
    render_kpis(narrative.get("kpi_json"))
    st.divider()

    # Narrative
    st.markdown(narrative["narrative_text"])

    # Ranking chart
    team_stats = load_group_team_stats(group_type, group_key, season)
    if not team_stats.empty:
        st.divider()
        st.subheader("Capacity Utilization Ranking")

        team_stats = team_stats.sort_values("avg_cap_util", ascending=True)
        colors = [
            "#3498db" if tid == highlight_team_id else GREY
            for tid in team_stats["team_id"]
        ]
        team_stats["color"] = colors

        fig = px.bar(
            team_stats,
            x="avg_cap_util",
            y="team_name",
            orientation="h",
            color="color",
            color_discrete_map="identity",
            labels={"avg_cap_util": "Capacity Utilization", "team_name": ""},
            height=max(300, len(team_stats) * 22),
        )
        fig.update_layout(
            showlegend=False,
            margin=dict(t=10, b=20),
            xaxis_tickformat=".0%",
        )
        st.plotly_chart(fig, use_container_width=True)

    st.caption(f"Generated by {narrative.get('llm_model', 'LLM')} "
               f"on {str(narrative.get('generated_at', ''))[:19]}")


# -- Sidebar ------------------------------------------------------------------

teams_df = load_teams()
seasons = load_available_seasons()

with st.sidebar:
    st.header("Select Team")

    selected_levels = st.multiselect(
        "Level",
        options=list(LEVEL_ORDER.values()),
        default=["Double-A"],
    )
    level_ids = [k for k, v in LEVEL_ORDER.items() if v in selected_levels]
    filtered = teams_df[teams_df["sport_id"].isin(level_ids)] if level_ids else teams_df

    team_names = sorted(filtered["team_name"].tolist())
    default_idx = team_names.index("Binghamton Rumble Ponies") if "Binghamton Rumble Ponies" in team_names else 0
    selected_team_name = st.selectbox("Team", team_names, index=default_idx)

    season = st.selectbox("Season", seasons, index=0) if seasons else None

# Resolve selection
selected_team = teams_df[teams_df["team_name"] == selected_team_name]
if selected_team.empty or season is None:
    st.warning("Select a team and season from the sidebar.")
    st.stop()

team_info = selected_team.iloc[0]
team_id = int(team_info["team_id"])
level_name = team_info["sport_name"]
cluster_label = team_info.get("cluster_label")
promo_label = team_info.get("promo_cluster_label")

# -- Page header --------------------------------------------------------------

st.title(f"{selected_team_name} -- Season Report")
subtitle_parts = [level_name]
if pd.notna(cluster_label):
    subtitle_parts.append(f"Peer group: {cluster_label}")
if pd.notna(promo_label):
    subtitle_parts.append(f"Promo strategy: {promo_label}")
if season:
    subtitle_parts.append(f"Season {season}")
st.caption(" | ".join(subtitle_parts))

# -- Tabs ---------------------------------------------------------------------

tab_labels = ["Team Report"]
if level_name:
    tab_labels.append(f"{level_name} Overview")
if pd.notna(cluster_label):
    tab_labels.append(f"Peer Group")
if pd.notna(promo_label):
    tab_labels.append(f"Promo Strategy")
tab_labels.append("League Wide")

tabs = st.tabs(tab_labels)
tab_idx = 0

# -- Tab 1: Team Report -------------------------------------------------------
with tabs[tab_idx]:
    tab_idx += 1

    is_hero = (selected_team_name == HERO_TEAM)
    narrative = load_team_narrative(team_id, season) if is_hero else None

    # --- KPI row: narrative if available, otherwise compute from momentum ---
    if narrative is not None and narrative.get("kpi_json"):
        render_kpis(narrative.get("kpi_json"))
    else:
        render_fallback_kpis(team_id, season)

    st.divider()

    # --- Written brief: hero team only --------------------------------------
    if narrative is not None:
        st.subheader("Executive Summary")
        st.markdown(narrative["narrative_text"])
        st.divider()

        col_goals, col_risks = st.columns([2, 1])
        with col_goals:
            render_goals(narrative.get("goals_json"))
        with col_risks:
            render_risks(narrative.get("risks_json"))
        st.divider()
    else:
        st.info(
            f"**Written briefs are generated only for {HERO_TEAM}.** "
            f"The data sections below (attendance trend, peer group, recommendations) "
            f"work for any team -- use them to explore. "
            f"To generate a brief for {selected_team_name}, run: "
            f"`python scripts/generate_narratives.py --team {team_id}`"
        )

    # --- Season-over-season TL;DR (comparator sparklines) --------------------
    trend = load_attendance_trend(team_id)
    if not trend.empty and trend["season"].nunique() >= 2:
        st.subheader("This season vs last season")
        st.caption("Monthly sparkline compares the latest season to the prior one.")
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            season_delta_metric(
                "Avg attendance", trend, value_col="avg_att",
                fmt="{:,.0f}",
                help="Mean per-game attendance, averaged across months.",
            )
        with sc2:
            season_delta_metric(
                "Capacity utilization", trend, value_col="avg_cap_util",
                fmt="{:.0%}",
                help="Fans / seats. Normalizes for venue size.",
            )
        with sc3:
            season_delta_metric(
                "Promos per game", trend, value_col="avg_promo_count",
                fmt="{:.1f}",
                help="How active the promotional calendar is.",
            )
        st.divider()

    # --- Attendance trend (all teams) ---------------------------------------
    if not trend.empty:
        st.subheader("Attendance Trend")
        month_labels = {4: "Apr", 5: "May", 6: "Jun", 7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct"}
        trend["month_label"] = trend["month"].map(month_labels).fillna(trend["month"].astype(str))
        trend["season_str"] = trend["season"].astype(str)

        fig = px.line(
            trend,
            x="month_label",
            y="avg_att",
            color="season_str",
            markers=True,
            labels={"month_label": "Month", "avg_att": "Avg Attendance", "season_str": "Season"},
            color_discrete_map=SEASON_COLORS,
            height=350,
        )
        fig.update_layout(margin=dict(t=10, b=20))
        st.plotly_chart(fig, use_container_width=True)

        st.divider()

    # --- Recommendations (all teams) ----------------------------------------
    recs = load_team_recs(team_id)
    st.subheader("Recommendations")
    render_recommendations(recs)

    if narrative is not None:
        st.caption(
            f"Narrative generated by {narrative.get('llm_model', 'LLM')} "
            f"on {str(narrative.get('generated_at', ''))[:19]}"
        )

# -- Tab 2: Level Overview -----------------------------------------------------
if level_name:
    with tabs[tab_idx]:
        tab_idx += 1
        render_group_tab("level", level_name, season, highlight_team_id=team_id)

# -- Tab 3: Peer Group --------------------------------------------------------
if pd.notna(cluster_label):
    with tabs[tab_idx]:
        tab_idx += 1
        render_group_tab("market_cluster", cluster_label, season, highlight_team_id=team_id)

# -- Tab 4: Promo Strategy ----------------------------------------------------
if pd.notna(promo_label):
    with tabs[tab_idx]:
        tab_idx += 1
        render_group_tab("promo_cluster", promo_label, season, highlight_team_id=team_id)

# -- Tab 5: League Wide -------------------------------------------------------
with tabs[tab_idx]:
    render_group_tab("league", "all", season, highlight_team_id=team_id)

st.divider()
st.caption(
    "Narratives generated by local LLM from analytics pipeline outputs. "
    "Regenerate with: `python scripts/generate_narratives.py --force`"
)


# ── Cross-page navigation + footer ───────────────────────────────────────────
see_also([
    ("Recommendations",  "pages/10_Recommendations.py", "prioritized actions for this team"),
    ("Competitive Intel","pages/9_Competitive_Intel.py","peer comparisons and momentum"),
    ("Executive Overview","pages/0_Executive_Overview.py","league-level summary of findings"),
])
render_footer(scripts=["recommendations"])
