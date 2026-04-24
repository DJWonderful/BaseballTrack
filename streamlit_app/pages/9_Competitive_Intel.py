"""Competitive Intelligence -- Who to Watch.

Identifies teams in similar weather/market conditions that are
outperforming or improving. Shows promo strategies that work for
similar teams and generates a manager-ready narrative brief.

Tabs:
  1. Teams to Emulate   - Weather/market peers ranked by performance
  2. Momentum Tracker   - Who is trending up or down league-wide
  3. Promo Playbook     - What works for similar teams
  4. Competitive Brief  - LLM narrative report with KPIs
"""

# -- Path setup ---------------------------------------------------------------
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils.db import query_df
from utils.filters import operator_filter
from utils.footer import render_footer
from utils.navigation import see_also

st.set_page_config(page_title="Competitive Intel | MiLB", page_icon="CI", layout="wide")

LEVEL_ORDER = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}
GREEN = "#2ecc71"
RED = "#e74c3c"
GREY = "#95a5a6"
BLUE = "#3498db"
ORANGE = "#f39c12"

MOMENTUM_COLORS = {
    "surging": "#1a9850",
    "improving": "#66bd63",
    "stable": "#95a5a6",
    "declining": "#f46d43",
    "struggling": "#d73027",
}

PROMO_LABELS = {
    "has_fireworks": "Fireworks", "has_giveaway": "Giveaway",
    "has_food_deal": "Food Deal", "has_ticket_deal": "Ticket Deal",
    "has_theme_night": "Theme Night", "has_kids_event": "Kids Event",
    "has_heritage": "Heritage Night", "has_community": "Community",
    "has_entertain": "Entertainment", "has_dog": "Dog Friendly",
    "has_celebrity": "Celebrity", "has_recurring": "Recurring",
}


# -- Data loaders -------------------------------------------------------------

@st.cache_data(ttl=600)
def load_teams():
    return query_df("""
        SELECT t.team_id, t.team_name, t.sport_id,
               COALESCE(sp.sport_name, 'Unknown') AS level,
               COALESCE(op.operator_name, 'Independent') AS operator,
               v.capacity, v.venue_name, v.city, v.state,
               vd.msa_population, vd.msa_poverty_rate, vd.msa_median_income
        FROM milb.teams t
        JOIN milb.venues v ON t.venue_id = v.venue_id
        LEFT JOIN milb.sports sp ON t.sport_id = sp.sport_id
        LEFT JOIN milb.team_operators op ON t.operator_id = op.operator_id
        LEFT JOIN LATERAL (
            SELECT * FROM milb.venue_demographics vd2
            WHERE vd2.venue_id = v.venue_id
            ORDER BY vd2.census_year DESC LIMIT 1
        ) vd ON TRUE
        WHERE t.sport_id IN (11, 12, 13, 14)
        ORDER BY t.team_name
    """)


@st.cache_data(ttl=600)
def load_weather_peers(team_id: int, season: int):
    return query_df(f"""
        SELECT wps.peer_team_id AS team_id,
               t.team_name, t.sport_id,
               COALESCE(sp.sport_name, 'Unknown') AS level,
               wps.similarity_score, wps.weather_dist, wps.demo_dist,
               wp.avg_temp_f, wp.pct_rain_games,
               v.capacity,
               vd.msa_population, vd.msa_poverty_rate,
               tm.avg_attendance, tm.avg_cap_util,
               tm.yoy_attendance_pct, tm.yoy_cap_util_change,
               tm.momentum_label, tm.momentum_score
        FROM milb.weather_peer_similarity wps
        JOIN milb.teams t ON wps.peer_team_id = t.team_id
        LEFT JOIN milb.sports sp ON t.sport_id = sp.sport_id
        LEFT JOIN milb.venues v ON t.venue_id = v.venue_id
        LEFT JOIN LATERAL (
            SELECT * FROM milb.venue_demographics vd2
            WHERE vd2.venue_id = v.venue_id
            ORDER BY vd2.census_year DESC LIMIT 1
        ) vd ON TRUE
        LEFT JOIN milb.team_weather_profile wp
            ON wps.peer_team_id = wp.team_id AND wp.season = {season}
        LEFT JOIN milb.team_momentum tm
            ON wps.peer_team_id = tm.team_id AND tm.season = {season}
        WHERE wps.team_id = {team_id} AND wps.season = {season}
        ORDER BY wps.similarity_score DESC
        LIMIT 15
    """)


@st.cache_data(ttl=600)
def load_team_momentum(team_id: int, season: int):
    return query_df(f"""
        SELECT * FROM milb.team_momentum
        WHERE team_id = {team_id} AND season = {season}
    """)


@st.cache_data(ttl=600)
def load_team_weather(team_id: int, season: int):
    return query_df(f"""
        SELECT * FROM milb.team_weather_profile
        WHERE team_id = {team_id} AND season = {season}
    """)


@st.cache_data(ttl=600)
def load_all_momentum(season: int):
    return query_df(f"""
        SELECT tm.team_id, tm.season,
               tm.avg_attendance, tm.avg_cap_util,
               tm.yoy_attendance_pct, tm.yoy_cap_util_change,
               tm.intra_season_trend, tm.momentum_label, tm.momentum_score,
               tm.first_half_avg_att, tm.second_half_avg_att,
               t.team_name, t.sport_id,
               COALESCE(sp.sport_name, 'Unknown') AS level
        FROM milb.team_momentum tm
        JOIN milb.teams t ON tm.team_id = t.team_id
        LEFT JOIN milb.sports sp ON t.sport_id = sp.sport_id
        WHERE tm.season = {season}
        ORDER BY tm.momentum_score DESC NULLS LAST
    """)


@st.cache_data(ttl=600)
def load_promo_lift_for_peers(team_id: int, peer_ids: tuple):
    if not peer_ids:
        return pd.DataFrame()
    ids_str = ", ".join(str(p) for p in peer_ids)
    return query_df(f"""
        SELECT team_id, promo_type, marginal_lift::float, p_value::float, scope
        FROM milb.promo_lift
        WHERE scope = 'team_all'
          AND team_id IN ({team_id}, {ids_str})
    """)


@st.cache_data(ttl=600)
def load_promo_profiles(team_ids: tuple):
    if not team_ids:
        return pd.DataFrame()
    ids_str = ", ".join(str(t) for t in team_ids)
    return query_df(f"""
        SELECT * FROM milb.v_team_promo_profile
        WHERE team_id IN ({ids_str})
    """)


@st.cache_data(ttl=600)
def load_ci_narrative(team_id: int, season: int):
    return query_df(f"""
        SELECT narrative_text, kpi_json, llm_model, generated_at
        FROM milb.group_narratives
        WHERE group_type = 'competitive_intel'
          AND group_key = '{team_id}'
          AND season = {season}
    """)


@st.cache_data(ttl=600)
def load_available_seasons():
    df = query_df("SELECT DISTINCT season FROM milb.team_momentum ORDER BY season DESC")
    return df["season"].tolist() if not df.empty else []


@st.cache_data(ttl=600)
def load_funnel_candidates(season: int):
    """One row per team with every dimension the Peer Funnel can filter on.

    population_change_5yr_pct lives on milb.game_features (added in migration
    009) rather than venue_demographics, so we pull it via a LATERAL sample.
    """
    return query_df(f"""
        SELECT t.team_id, t.team_name, t.sport_id,
               COALESCE(sp.sport_name, '') AS level,
               v.capacity AS venue_capacity,
               vd.msa_population,
               gf.population_change_5yr_pct,
               gf.population_trend,
               wp.avg_temp_f, wp.pct_rain_games,
               tpc.promo_cluster_id, tpc.promo_cluster_label,
               tm.avg_attendance, tm.avg_cap_util, tm.yoy_attendance_pct,
               tm.momentum_label, tm.momentum_score
          FROM milb.teams t
          LEFT JOIN milb.sports sp ON t.sport_id = sp.sport_id
          LEFT JOIN milb.venues v ON t.venue_id = v.venue_id
          LEFT JOIN LATERAL (
              SELECT msa_population
                FROM milb.venue_demographics vd2
               WHERE vd2.venue_id = v.venue_id
               ORDER BY vd2.census_year DESC
               LIMIT 1
          ) vd ON TRUE
          LEFT JOIN LATERAL (
              SELECT population_change_5yr_pct, population_trend
                FROM milb.game_features gf2
               WHERE gf2.team_id = t.team_id
                 AND gf2.population_change_5yr_pct IS NOT NULL
               ORDER BY gf2.season DESC, gf2.game_date DESC
               LIMIT 1
          ) gf ON TRUE
          LEFT JOIN milb.team_weather_profile wp
                 ON t.team_id = wp.team_id AND wp.season = {season}
          LEFT JOIN milb.team_promo_clusters tpc ON t.team_id = tpc.team_id
          LEFT JOIN milb.team_momentum tm
                 ON t.team_id = tm.team_id AND tm.season = {season}
         WHERE t.sport_id IN (11,12,13,14)
    """)


@st.cache_data(ttl=600)
def load_weather_sensitivity(team_id: int):
    """Per-team avg attendance in rain vs non-rain games, latest season.

    Also pulls league-wide rain/non-rain averages so we can show a 'you vs peers'
    sensitivity delta -- i.e. does this park draw worse in rain than most parks do?
    """
    team_df = query_df("""
        SELECT
            CASE
                WHEN precip_inches > 0.1 THEN 'rain'
                ELSE 'dry'
            END AS weather_bucket,
            AVG(attendance)::int AS avg_att,
            COUNT(*) AS games
          FROM milb.game_features
         WHERE team_id = :tid
           AND attendance IS NOT NULL
           AND season = (SELECT MAX(season) FROM milb.game_features WHERE team_id = :tid)
         GROUP BY 1
    """, {"tid": team_id})

    league_df = query_df("""
        SELECT
            sport_id,
            CASE
                WHEN precip_inches > 0.1 THEN 'rain'
                ELSE 'dry'
            END AS weather_bucket,
            AVG(attendance)::float AS avg_att
          FROM milb.game_features
         WHERE attendance IS NOT NULL
           AND season = (SELECT MAX(season) FROM milb.game_features)
         GROUP BY 1, 2
    """)
    return team_df, league_df


@st.cache_data(ttl=600)
def load_funnel_promo_usage(season: int):
    """Per-team % of games that ran each promo category, latest season available."""
    return query_df(f"""
        SELECT team_id,
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
         WHERE season = {season}
         GROUP BY team_id
    """)


# -- Helpers -------------------------------------------------------------------

def parse_json_col(val):
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


# -- Sidebar -------------------------------------------------------------------

with st.sidebar:
    st.header("Filters")
    teams_df = load_teams()

    selected_levels = st.multiselect(
        "Level",
        options=list(LEVEL_ORDER.values()),
        default=list(LEVEL_ORDER.values()),
    )
    level_ids = [k for k, v in LEVEL_ORDER.items() if v in selected_levels]

    selected_operators = operator_filter()

    filtered_teams = teams_df[teams_df["sport_id"].isin(level_ids)]
    if selected_operators is not None:
        filtered_teams = filtered_teams[filtered_teams["operator"].isin(selected_operators)]

    team_options = filtered_teams.sort_values("team_name")
    team_names = team_options["team_name"].tolist()
    default_idx = team_names.index("Binghamton Rumble Ponies") if "Binghamton Rumble Ponies" in team_names else 0
    selected_team_name = st.selectbox("Team", team_names, index=default_idx)

    st.divider()

    seasons = load_available_seasons()
    if seasons:
        selected_season = st.selectbox("Season", seasons, index=0)
    else:
        selected_season = 2025

    st.divider()
    st.caption("Data from competitive intelligence pipeline")


# -- Resolve team ID -----------------------------------------------------------

team_row = teams_df[teams_df["team_name"] == selected_team_name]
if team_row.empty:
    st.error("Team not found.")
    st.stop()

team_id = int(team_row.iloc[0]["team_id"])
team_info = team_row.iloc[0]

# -- Page header ---------------------------------------------------------------

st.title("Competitive Intelligence")
st.caption(f"Who to watch and what to borrow -- weather/market peer analysis for **{selected_team_name}**")

# -- Tabs ----------------------------------------------------------------------

tab_emulate, tab_funnel, tab_momentum, tab_playbook, tab_brief = st.tabs([
    "Teams to Emulate", "Peer Funnel", "Momentum Tracker", "Promo Playbook", "Competitive Brief",
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1: Teams to Emulate
# ═══════════════════════════════════════════════════════════════════════════════

with tab_emulate:
    my_momentum = load_team_momentum(team_id, selected_season)
    my_weather = load_team_weather(team_id, selected_season)
    peers = load_weather_peers(team_id, selected_season)

    if peers.empty:
        st.warning("No weather peer data available. Run `build_competitive_intel.py` first.")
        st.stop()

    # KPI row
    c1, c2, c3, c4 = st.columns(4)
    if not my_momentum.empty:
        mm = my_momentum.iloc[0]
        c1.metric("Avg Attendance", f"{int(mm['avg_attendance']):,}" if pd.notna(mm.get("avg_attendance")) else "---")
        c2.metric("Cap Utilization", f"{mm['avg_cap_util']:.1%}" if pd.notna(mm.get("avg_cap_util")) else "---")
        yoy = mm.get("yoy_attendance_pct")
        c3.metric("YoY Change", f"{yoy:+.1%}" if pd.notna(yoy) else "---",
                  delta=f"{yoy:+.1%}" if pd.notna(yoy) else None)
        c4.metric("Momentum", mm.get("momentum_label", "---"))
    else:
        c1.metric("Avg Attendance", "---")
        c2.metric("Cap Utilization", "---")
        c3.metric("YoY Change", "---")
        c4.metric("Momentum", "---")

    st.divider()

    # -- Weather profile card (from milb.team_weather_profile) -----------------
    if not my_weather.empty:
        mw = my_weather.iloc[0]
        wc1, wc2, wc3, wc4, wc5 = st.columns(5)
        wc1.metric("Avg Game Temp",   f"{mw['avg_temp_f']:.0f} F"      if pd.notna(mw.get('avg_temp_f')) else "-")
        wc2.metric("Avg Precip",      f"{mw['avg_precip_in']:.2f} in"  if pd.notna(mw.get('avg_precip_in')) else "-")
        wc3.metric("Avg Wind",        f"{mw['avg_wind_mph']:.1f} mph"  if pd.notna(mw.get('avg_wind_mph')) else "-")
        wc4.metric("Rain-game %",     f"{mw['pct_rain_games']:.0%}"    if pd.notna(mw.get('pct_rain_games')) else "-")
        wc5.metric("Home Games",      f"{int(mw['total_home_games'])}" if pd.notna(mw.get('total_home_games')) else "-")
        st.caption(
            f"Weather profile for {selected_team_name}, {selected_season}. "
            f"Peers below are matched on these dimensions plus MSA population, poverty rate, and venue capacity."
        )
    else:
        st.caption(
            "No weather profile available. Peers are matched on weather, MSA population, "
            "poverty rate, and venue capacity."
        )
    st.caption(
        "Peers are cross-level by design -- the Level filter in the sidebar only limits "
        "which team you can select, not the peer list."
    )

    # -- Weather sensitivity (rain vs dry) -------------------------------------
    with st.expander("Weather sensitivity -- does your park draw worse in rain?"):
        tsens, lsens = load_weather_sensitivity(team_id)
        if tsens.empty or len(tsens) < 2:
            st.caption(
                "Not enough rain/dry split for this team (need games in both buckets)."
            )
        else:
            team_map = {r["weather_bucket"]: r for _, r in tsens.iterrows()}
            dry = team_map.get("dry")
            rain = team_map.get("rain")

            if dry is None or rain is None:
                st.caption("This team has games in only one weather bucket this season.")
            else:
                # League benchmark for the team's level
                sport_id = int(team_info["sport_id"])
                league_sub = lsens[lsens["sport_id"] == sport_id]
                l_dry = league_sub[league_sub["weather_bucket"] == "dry"]["avg_att"]
                l_rain = league_sub[league_sub["weather_bucket"] == "rain"]["avg_att"]

                team_delta = int(rain["avg_att"]) - int(dry["avg_att"])
                team_pct = team_delta / max(int(dry["avg_att"]), 1) * 100

                league_delta = None
                if not l_dry.empty and not l_rain.empty:
                    league_delta = float(l_rain.iloc[0]) - float(l_dry.iloc[0])
                    league_pct = league_delta / max(float(l_dry.iloc[0]), 1) * 100

                s1, s2, s3, s4 = st.columns(4)
                s1.metric("Dry-game avg", f"{int(dry['avg_att']):,}",
                          help=f"{int(dry['games'])} games")
                s2.metric("Rain-game avg", f"{int(rain['avg_att']):,}",
                          help=f"{int(rain['games'])} games")
                s3.metric("Your rain penalty", f"{team_pct:+.0f}%",
                          delta=f"{team_delta:+,} fans",
                          delta_color="normal" if team_delta >= 0 else "inverse")
                if league_delta is not None:
                    vs_league = team_pct - league_pct
                    s4.metric(
                        f"{team_info['level']} benchmark", f"{league_pct:+.0f}%",
                        delta=f"{vs_league:+.0f}pp vs your park",
                        delta_color="normal" if vs_league >= 0 else "inverse",
                        help="Positive = your park is less rain-sensitive than average at this level.",
                    )

                if team_delta < 0:
                    st.caption(
                        "Rain games are drawing fewer fans than dry games here. "
                        "Compare to the level benchmark -- if your penalty is worse than "
                        "peers, a rain-policy change (covered seating upsell, ticket "
                        "exchange) could recover some of the gap."
                    )

    # Add selected team to comparison df
    my_row = pd.DataFrame([{
        "team_id": team_id,
        "team_name": selected_team_name,
        "level": team_info.get("level", ""),
        "avg_cap_util": float(mm["avg_cap_util"]) if not my_momentum.empty and pd.notna(mm.get("avg_cap_util")) else None,
        "avg_attendance": int(mm["avg_attendance"]) if not my_momentum.empty and pd.notna(mm.get("avg_attendance")) else None,
        "momentum_label": mm.get("momentum_label") if not my_momentum.empty else None,
        "yoy_attendance_pct": float(mm["yoy_attendance_pct"]) if not my_momentum.empty and pd.notna(mm.get("yoy_attendance_pct")) else None,
        "is_selected": True,
    }])
    peers_chart = peers.copy()
    peers_chart["is_selected"] = False
    chart_df = pd.concat([my_row, peers_chart], ignore_index=True)

    # Bar chart: peers by cap util
    chart_df = chart_df.dropna(subset=["avg_cap_util"])
    chart_df = chart_df.sort_values("avg_cap_util", ascending=True)

    def bar_color(row):
        if row.get("is_selected"):
            return BLUE
        ml = row.get("momentum_label", "")
        if ml in ("surging", "improving"):
            return GREEN
        if ml in ("declining", "struggling"):
            return RED
        return GREY

    chart_df["bar_color"] = chart_df.apply(bar_color, axis=1)
    chart_df["label"] = chart_df.apply(
        lambda r: f"{r['team_name']} ({r.get('level', '')})", axis=1
    )

    fig = px.bar(
        chart_df, y="label", x="avg_cap_util", orientation="h",
        color="bar_color", color_discrete_map="identity",
        labels={"avg_cap_util": "Capacity Utilization", "label": ""},
    )
    # Peer average line
    peer_avg = peers["avg_cap_util"].mean() if not peers["avg_cap_util"].isna().all() else None
    if peer_avg:
        fig.add_vline(x=peer_avg, line_dash="dash", line_color=RED,
                      annotation_text=f"Peer Avg: {peer_avg:.1%}")
    fig.update_layout(showlegend=False, height=max(400, len(chart_df) * 30),
                      margin=dict(l=0, r=20, t=10, b=10))
    fig.update_xaxes(tickformat=".0%")
    st.plotly_chart(fig, use_container_width=True)

    # Best in class callout
    top_3 = peers.nlargest(3, "avg_cap_util")
    if not top_3.empty:
        st.subheader("Best in Class (your weather-peers)")
        for _, p in top_3.iterrows():
            cap = f"{p['avg_cap_util']:.1%}" if pd.notna(p.get("avg_cap_util")) else "?"
            mom = p.get("momentum_label", "?")
            st.success(
                f"**{p['team_name']}** ({p.get('level', '')}) -- "
                f"{cap} capacity utilization, momentum: {mom}"
            )

    # Detail table
    with st.expander("Peer detail table"):
        display_cols = {
            "team_name": "Team", "level": "Level",
            "avg_temp_f": "Avg Temp F", "pct_rain_games": "Rain %",
            "msa_population": "MSA Pop", "msa_poverty_rate": "Poverty %",
            "capacity": "Venue Cap", "avg_attendance": "Avg Att",
            "avg_cap_util": "Cap Util", "yoy_attendance_pct": "YoY %",
            "momentum_label": "Momentum", "similarity_score": "Similarity",
        }
        cols_available = [c for c in display_cols if c in peers.columns]
        tbl = peers[cols_available].copy()
        tbl = tbl.rename(columns={c: display_cols[c] for c in cols_available})
        for c in ["Rain %", "Cap Util", "YoY %"]:
            if c in tbl.columns:
                tbl[c] = tbl[c].apply(lambda v: f"{v:.1%}" if pd.notna(v) else "")
        st.dataframe(tbl, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2: Peer Funnel  --  layered similarity narrowing + Do/Don't panel
# ═══════════════════════════════════════════════════════════════════════════════

with tab_funnel:
    st.subheader("Peer Funnel")
    st.caption(
        "Narrow the peer pool one dimension at a time. Each checkbox shrinks the "
        "candidate set; the final list is split into 'declining' and 'improving' "
        "peers so you can see what to stop doing and what to start doing."
    )

    cands = load_funnel_candidates(selected_season)
    if cands.empty:
        st.warning("No funnel data available. Run `build_competitive_intel.py` first.")
    else:
        me = cands[cands["team_id"] == team_id]
        if me.empty:
            st.info(f"No funnel row for {selected_team_name} -- momentum or weather profile missing.")
        else:
            me_row = me.iloc[0]

            # -- Filter toggles ------------------------------------------------
            c1, c2 = st.columns(2)
            with c1:
                f_level    = st.checkbox("Same level",              value=True,
                                         help=f"Restrict to {me_row['level']} teams only.")
                f_market   = st.checkbox("Similar market size (MSA pop ±25%)", value=True)
                f_weather  = st.checkbox("Similar weather (avg temp ±5 F)",    value=False)
            with c2:
                f_poptrend = st.checkbox("Similar population trend (same direction)", value=False,
                                         help="Declining, flat, or growing MSA -- same bucket as our team.")
                f_promo    = st.checkbox("Same promo-strategy cluster",         value=False)
                f_exclude_self = True  # always drop our own team

            # -- Apply filters in sequence, tracking funnel counts --------------
            funnel_steps: list[tuple[str, int]] = [("All MiLB teams", len(cands))]
            pool = cands.copy()

            if f_exclude_self:
                pool = pool[pool["team_id"] != team_id]
                funnel_steps.append(("Excluding our team", len(pool)))

            if f_level:
                pool = pool[pool["sport_id"] == me_row["sport_id"]]
                funnel_steps.append((f"Same level ({me_row['level']})", len(pool)))

            if f_market and pd.notna(me_row.get("msa_population")):
                low  = float(me_row["msa_population"]) * 0.75
                high = float(me_row["msa_population"]) * 1.25
                pool = pool[(pool["msa_population"] >= low) & (pool["msa_population"] <= high)]
                funnel_steps.append((f"MSA pop within +/-25% of {int(me_row['msa_population']):,}", len(pool)))

            if f_weather and pd.notna(me_row.get("avg_temp_f")):
                t = float(me_row["avg_temp_f"])
                pool = pool[pool["avg_temp_f"].between(t - 5, t + 5)]
                funnel_steps.append((f"Avg temp within +/-5F of {t:.0f}F", len(pool)))

            if f_poptrend:
                # Prefer the precomputed population_trend text column (written by
                # build_features.py); fall back to bucketing the pct change.
                my_trend = me_row.get("population_trend")
                if pd.notna(my_trend) and my_trend:
                    pool = pool[pool["population_trend"] == my_trend]
                    funnel_steps.append((f"Population trend: {my_trend}", len(pool)))
                elif pd.notna(me_row.get("population_change_5yr_pct")):
                    def _bucket(v):
                        if pd.isna(v): return None
                        if v < -0.02: return "declining"
                        if v > 0.02:  return "growing"
                        return "flat"
                    my_bucket = _bucket(me_row["population_change_5yr_pct"])
                    pool = pool[pool["population_change_5yr_pct"].apply(_bucket) == my_bucket]
                    funnel_steps.append((f"Population trend: {my_bucket}", len(pool)))

            if f_promo and pd.notna(me_row.get("promo_cluster_id")):
                pool = pool[pool["promo_cluster_id"] == me_row["promo_cluster_id"]]
                funnel_steps.append((f"Same promo cluster: {me_row['promo_cluster_label']}", len(pool)))

            # -- Visualize the funnel ------------------------------------------
            st.divider()
            funnel_df = pd.DataFrame(funnel_steps, columns=["Step", "Remaining"])
            st.dataframe(funnel_df, use_container_width=True, hide_index=True)

            if pool.empty:
                st.warning("No peers match all selected dimensions. Loosen a filter above.")
            else:
                # -- Split peers by momentum ----------------------------------
                decliners = pool[pool["momentum_label"].isin(["declining", "struggling"])].copy()
                improvers = pool[pool["momentum_label"].isin(["improving", "surging"])].copy()
                stable    = pool[pool["momentum_label"].isin(["stable"])].copy()

                st.subheader(f"{len(pool)} peers matched")
                mc1, mc2, mc3 = st.columns(3)
                mc1.metric("Declining / Struggling", len(decliners))
                mc2.metric("Stable",                 len(stable))
                mc3.metric("Improving / Surging",    len(improvers))

                with st.expander("Full peer list"):
                    display = pool[[
                        "team_name", "level", "momentum_label", "avg_cap_util",
                        "yoy_attendance_pct", "msa_population", "avg_temp_f",
                        "promo_cluster_label",
                    ]].copy()
                    display.columns = [
                        "Team", "Level", "Momentum", "Cap Util",
                        "YoY %", "MSA Pop", "Avg Temp",
                        "Promo Cluster",
                    ]
                    for c in ["Cap Util", "YoY %"]:
                        if c in display.columns:
                            display[c] = display[c].apply(lambda v: f"{v:.1%}" if pd.notna(v) else "")
                    display["MSA Pop"] = display["MSA Pop"].apply(
                        lambda v: f"{int(v):,}" if pd.notna(v) else ""
                    )
                    display["Avg Temp"] = display["Avg Temp"].apply(
                        lambda v: f"{v:.0f} F" if pd.notna(v) else ""
                    )
                    st.dataframe(display, use_container_width=True, hide_index=True)

                # -- Do / Don't panel -----------------------------------------
                st.divider()
                st.subheader("What to stop doing and what to start doing")
                st.caption(
                    "We compare our promo-category usage to the peer subsets. "
                    "'Stop' = categories we run AND the declining peers also run (failure overlap). "
                    "'Start' = categories we rarely run that improving peers lean into (untapped lever)."
                )

                usage = load_funnel_promo_usage(selected_season)
                if usage.empty:
                    st.info(
                        "No promo usage data for this season yet. Promo data is populated for "
                        "the most recent season after the LLM enrichment step completes."
                    )
                else:
                    promo_cols = [c for c in usage.columns if c.startswith("has_")]

                    def _group_mean(team_ids: list[int]) -> pd.Series:
                        sub = usage[usage["team_id"].isin(team_ids)][promo_cols]
                        if sub.empty:
                            return pd.Series(dtype=float)
                        return sub.mean()

                    my_row = usage[usage["team_id"] == team_id][promo_cols]
                    my_mix = my_row.iloc[0] if not my_row.empty else None

                    dec_mix = _group_mean(decliners["team_id"].tolist()) if not decliners.empty else pd.Series(dtype=float)
                    imp_mix = _group_mean(improvers["team_id"].tolist()) if not improvers.empty else pd.Series(dtype=float)

                    if my_mix is None:
                        st.info("No promo usage row for our team yet.")
                    else:
                        # "Stop": things we do A LOT that decliners also do a lot
                        stop_rows = []
                        if not dec_mix.empty:
                            for col in promo_cols:
                                mine = float(my_mix.get(col, 0.0))
                                theirs = float(dec_mix.get(col, 0.0))
                                # Both running it >20% and decliner usage roughly matches ours
                                if mine > 0.20 and theirs > 0.20 and abs(mine - theirs) < 0.20:
                                    stop_rows.append({
                                        "Category": PROMO_LABELS.get(col, col),
                                        "Our usage": mine,
                                        "Decliners' usage": theirs,
                                    })

                        # "Start": things we rarely do that improvers lean into
                        start_rows = []
                        if not imp_mix.empty:
                            for col in promo_cols:
                                mine = float(my_mix.get(col, 0.0))
                                theirs = float(imp_mix.get(col, 0.0))
                                # We're at 10% or less, improvers are at least 20 pp higher
                                if mine <= 0.10 and theirs - mine >= 0.20:
                                    start_rows.append({
                                        "Category": PROMO_LABELS.get(col, col),
                                        "Our usage": mine,
                                        "Improvers' usage": theirs,
                                        "Gap": theirs - mine,
                                    })

                        col_stop, col_start = st.columns(2)
                        with col_stop:
                            st.markdown("**Consider scaling back** (you + decliners both lean here):")
                            if stop_rows:
                                sdf = pd.DataFrame(stop_rows)
                                sdf["Our usage"] = sdf["Our usage"].apply(lambda v: f"{v:.0%}")
                                sdf["Decliners' usage"] = sdf["Decliners' usage"].apply(lambda v: f"{v:.0%}")
                                st.dataframe(sdf, use_container_width=True, hide_index=True)
                            else:
                                st.caption("No overlap flagged -- nothing to stop based on this peer set.")
                        with col_start:
                            st.markdown("**Consider adding** (improvers do it, you rarely do):")
                            if start_rows:
                                sdf = pd.DataFrame(start_rows).sort_values("Gap", ascending=False)
                                sdf["Our usage"] = sdf["Our usage"].apply(lambda v: f"{v:.0%}")
                                sdf["Improvers' usage"] = sdf["Improvers' usage"].apply(lambda v: f"{v:.0%}")
                                sdf["Gap"] = sdf["Gap"].apply(lambda v: f"+{v:.0%}")
                                st.dataframe(sdf, use_container_width=True, hide_index=True)
                            else:
                                st.caption(
                                    "No gaps flagged -- you already cover the improver playbook, "
                                    "or the peer set is too small."
                                )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3: Momentum Tracker
# ═══════════════════════════════════════════════════════════════════════════════

with tab_momentum:
    all_mom = load_all_momentum(selected_season)

    if all_mom.empty:
        st.warning("No momentum data available. Run `build_competitive_intel.py` first.")
    else:
        # Filter to selected levels
        all_mom_filtered = all_mom[all_mom["sport_id"].isin(level_ids)]

        # Summary metrics
        c1, c2, c3, c4 = st.columns(4)
        labels = all_mom_filtered["momentum_label"].value_counts()
        c1.metric("Surging", labels.get("surging", 0))
        c2.metric("Improving", labels.get("improving", 0))
        c3.metric("Declining", labels.get("declining", 0))
        c4.metric("Struggling", labels.get("struggling", 0))

        st.divider()

        # Scatter: cap util vs YoY change
        scatter_df = all_mom_filtered.dropna(subset=["avg_cap_util", "yoy_cap_util_change"]).copy()
        if not scatter_df.empty:
            scatter_df["color"] = scatter_df["momentum_label"].map(MOMENTUM_COLORS).fillna(GREY)
            scatter_df["size"] = scatter_df["team_id"].apply(
                lambda t: 18 if t == team_id else 8
            )
            scatter_df["label"] = scatter_df.apply(
                lambda r: f"{r['team_name']} ({r['level']})", axis=1
            )

            fig = px.scatter(
                scatter_df, x="avg_cap_util", y="yoy_cap_util_change",
                color="color", color_discrete_map="identity",
                size="size", size_max=18,
                hover_name="label",
                hover_data={"avg_attendance": True, "momentum_label": True,
                            "color": False, "size": False},
                labels={"avg_cap_util": "Capacity Utilization",
                        "yoy_cap_util_change": "YoY Cap Util Change"},
            )
            fig.add_hline(y=0, line_dash="dash", line_color=GREY, opacity=0.5)
            # Annotate selected team
            sel = scatter_df[scatter_df["team_id"] == team_id]
            if not sel.empty:
                s = sel.iloc[0]
                fig.add_annotation(
                    x=s["avg_cap_util"], y=s["yoy_cap_util_change"],
                    text=selected_team_name, showarrow=True, arrowhead=2,
                    font=dict(size=12, color=BLUE),
                )
            fig.update_layout(showlegend=False, height=500,
                              margin=dict(l=0, r=0, t=10, b=0))
            fig.update_xaxes(tickformat=".0%")
            fig.update_yaxes(tickformat=".0%")
            st.plotly_chart(fig, use_container_width=True)

        # Leaderboards
        col_left, col_right = st.columns(2)
        with col_left:
            st.subheader("Most Improved")
            top_10 = all_mom_filtered.nlargest(10, "momentum_score")
            display = top_10[["team_name", "level", "avg_attendance", "avg_cap_util",
                              "yoy_attendance_pct", "momentum_label"]].copy()
            display.columns = ["Team", "Level", "Avg Att", "Cap Util", "YoY %", "Momentum"]
            for c in ["Cap Util", "YoY %"]:
                display[c] = display[c].apply(lambda v: f"{v:.1%}" if pd.notna(v) else "")
            st.dataframe(display, use_container_width=True, hide_index=True)

        with col_right:
            st.subheader("Biggest Declines")
            bot_10 = all_mom_filtered.nsmallest(10, "momentum_score")
            display = bot_10[["team_name", "level", "avg_attendance", "avg_cap_util",
                              "yoy_attendance_pct", "momentum_label"]].copy()
            display.columns = ["Team", "Level", "Avg Att", "Cap Util", "YoY %", "Momentum"]
            for c in ["Cap Util", "YoY %"]:
                display[c] = display[c].apply(lambda v: f"{v:.1%}" if pd.notna(v) else "")
            st.dataframe(display, use_container_width=True, hide_index=True)

        # Selected team context
        my_row_mom = all_mom_filtered[all_mom_filtered["team_id"] == team_id]
        if not my_row_mom.empty:
            m = my_row_mom.iloc[0]
            rank = int((all_mom_filtered["momentum_score"] >= m["momentum_score"]).sum())
            total = len(all_mom_filtered)
            parts = [
                f"**{selected_team_name}** momentum: **{m.get('momentum_label', '?')}** "
                f"(ranked {rank} of {total}).",
            ]
            if pd.notna(m.get("yoy_attendance_pct")):
                parts.append(f"YoY attendance: {m['yoy_attendance_pct']:+.1%}.")
            if pd.notna(m.get("intra_season_trend")):
                parts.append(f"Within-season trend: {m['intra_season_trend']:+.1%}.")
            st.info(" ".join(parts))


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3: Promo Playbook
# ═══════════════════════════════════════════════════════════════════════════════

with tab_playbook:
    # Promo data only for 2025
    if selected_season != 2025:
        st.warning(
            "Detailed promotion data is only available for the 2025 season. "
            "Showing limited data for other seasons."
        )

    peer_ids = tuple(peers["team_id"].tolist()) if not peers.empty else ()
    promo_lift = load_promo_lift_for_peers(team_id, peer_ids)

    if promo_lift.empty:
        st.info("No promo lift data available for this team or its peers.")
    else:
        # Split into team vs peers
        team_lift = promo_lift[promo_lift["team_id"] == team_id].copy()
        peer_lift = promo_lift[promo_lift["team_id"] != team_id].copy()

        # Avg lift per promo type for peers
        peer_avg = peer_lift.groupby("promo_type").agg(
            peer_lift=("marginal_lift", "mean"),
            n_peers=("team_id", "nunique"),
        ).reset_index()

        # Team lift
        team_agg = team_lift[["promo_type", "marginal_lift"]].rename(
            columns={"marginal_lift": "team_lift"}
        )

        # Merge
        compare = peer_avg.merge(team_agg, on="promo_type", how="outer")
        compare["promo_label"] = compare["promo_type"].map(PROMO_LABELS).fillna(compare["promo_type"])
        compare = compare.sort_values("peer_lift", ascending=False, na_position="last")

        # Grouped bar chart
        st.subheader("Promo Lift: You vs Your Peers")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Your Team", x=compare["promo_label"], y=compare["team_lift"],
            marker_color=BLUE, text=compare["team_lift"].apply(
                lambda v: f"{v:+.0f}" if pd.notna(v) else "N/A"),
            textposition="outside",
        ))
        fig.add_trace(go.Bar(
            name="Peer Average", x=compare["promo_label"], y=compare["peer_lift"],
            marker_color=GREEN, text=compare["peer_lift"].apply(
                lambda v: f"{v:+.0f}" if pd.notna(v) else "N/A"),
            textposition="outside",
        ))
        fig.update_layout(
            barmode="group", height=400,
            yaxis_title="Attendance Lift (fans)",
            xaxis_title="",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(l=0, r=0, t=30, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Promo adoption comparison
        all_profile_ids = tuple([team_id] + list(peer_ids))
        profiles = load_promo_profiles(all_profile_ids)

        if not profiles.empty:
            st.subheader("Promo Adoption: Where You Can Do More")
            pct_cols = [c for c in profiles.columns if c.startswith("pct_") and c != "pct_recurring"]
            if pct_cols:
                my_profile = profiles[profiles["team_id"] == team_id]
                peer_profiles = profiles[profiles["team_id"] != team_id]

                if not my_profile.empty and not peer_profiles.empty:
                    my_vals = my_profile[pct_cols].iloc[0]
                    peer_means = peer_profiles[pct_cols].mean()

                    adoption = pd.DataFrame({
                        "Promo Type": [c.replace("pct_", "").replace("_", " ").title() for c in pct_cols],
                        "Your Rate": [f"{v:.0%}" if pd.notna(v) else "0%" for v in my_vals],
                        "Peer Avg": [f"{v:.0%}" if pd.notna(v) else "0%" for v in peer_means],
                        "Gap": [(peer_means[c] - my_vals[c]) if pd.notna(my_vals[c]) and pd.notna(peer_means[c]) else 0
                                for c in pct_cols],
                    })
                    adoption = adoption.sort_values("Gap", ascending=False)
                    st.dataframe(adoption, use_container_width=True, hide_index=True)

        # Actionable plays
        st.subheader("Actionable Plays")
        plays = []
        for _, row in compare.iterrows():
            peer_l = row.get("peer_lift")
            team_l = row.get("team_lift")
            if pd.notna(peer_l) and peer_l > 50:
                plays.append({
                    "promo": row["promo_label"],
                    "peer_lift": int(peer_l),
                    "team_lift": int(team_l) if pd.notna(team_l) else None,
                    "n_peers": int(row.get("n_peers", 0)),
                })

        if plays:
            for i, play in enumerate(plays[:5], 1):
                team_note = f" (your lift: {play['team_lift']:+,})" if play["team_lift"] is not None else ""
                st.success(
                    f"**{i}. {play['promo']}** -- Peers average **+{play['peer_lift']:,}** fans"
                    f"{team_note} ({play['n_peers']} peers with data)"
                )
        else:
            st.info("No strong promo plays identified from peer comparison.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4: Competitive Brief
# ═══════════════════════════════════════════════════════════════════════════════

with tab_brief:
    narrative = load_ci_narrative(team_id, selected_season)

    if narrative.empty:
        st.info(
            "No competitive intelligence narrative has been generated for this team yet. "
            "Run: `python scripts/generate_narratives.py --competitive-intel --team "
            f"{team_id}`"
        )
        # Fallback: show key data points
        st.subheader("Quick Summary (data only)")
        if not my_momentum.empty:
            mm = my_momentum.iloc[0]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Avg Attendance", f"{int(mm['avg_attendance']):,}" if pd.notna(mm.get("avg_attendance")) else "---")
            c2.metric("Cap Util", f"{mm['avg_cap_util']:.1%}" if pd.notna(mm.get("avg_cap_util")) else "---")
            yoy = mm.get("yoy_attendance_pct")
            c3.metric("YoY Change", f"{yoy:+.1%}" if pd.notna(yoy) else "---")
            c4.metric("Momentum", mm.get("momentum_label", "---"))

        if not peers.empty:
            st.subheader("Top Weather Peers")
            top = peers.head(5)
            for _, p in top.iterrows():
                cap = f"{p['avg_cap_util']:.1%}" if pd.notna(p.get("avg_cap_util")) else "?"
                st.write(f"- **{p['team_name']}** ({p.get('level', '')}) -- {cap} cap util, "
                         f"momentum: {p.get('momentum_label', '?')}")
    else:
        nr = narrative.iloc[0]
        kpi_data = parse_json_col(nr.get("kpi_json"))

        # KPI cards
        kpis = kpi_data.get("kpis", []) if isinstance(kpi_data, dict) else []
        if kpis:
            cols = st.columns(len(kpis))
            for col, kpi in zip(cols, kpis):
                trend_map = {"up": "+", "down": "-", "stable": "~"}
                delta = trend_map.get(kpi.get("trend"), "")
                col.metric(
                    kpi.get("label", ""),
                    kpi.get("value", ""),
                    delta=kpi.get("context", ""),
                )
            st.divider()

        # Executive summary
        st.subheader("Executive Summary")
        st.markdown(nr["narrative_text"])

        # Headlines
        headlines = kpi_data.get("headlines", []) if isinstance(kpi_data, dict) else []
        if headlines:
            st.divider()
            st.subheader("Key Takeaways")
            for h in headlines:
                st.success(h)

        # Teams to watch
        teams_to_watch = kpi_data.get("teams_to_watch", []) if isinstance(kpi_data, dict) else []
        if teams_to_watch:
            st.divider()
            st.subheader("Teams to Watch")
            for tw in teams_to_watch:
                st.info(
                    f"**{tw.get('team_name', '?')}** -- {tw.get('why', '')} "
                    f"*Key tactic: {tw.get('key_tactic', '')}*"
                )

        st.divider()
        st.caption(f"Generated by {nr.get('llm_model', '?')} on "
                   f"{nr.get('generated_at', '?')}")

    # Download button for peer data
    if not peers.empty:
        st.divider()
        csv = peers.to_csv(index=False)
        st.download_button(
            "Download peer comparison CSV",
            csv, f"{selected_team_name.replace(' ', '_')}_peers.csv",
            mime="text/csv",
        )


# ── Cross-page navigation + footer ───────────────────────────────────────────
see_also([
    ("Team Report",      "pages/8_Team_Report.py",      "the written brief for this team"),
    ("Recommendations",  "pages/10_Recommendations.py", "prioritized actions to close the peer gap"),
    ("Promo Strategy",   "pages/7_Promo_Strategy.py",   "your promo archetype"),
])
render_footer(scripts=["competitive_intel"])
