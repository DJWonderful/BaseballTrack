"""Executive overview -- what this tool answers, in baseball-team language.

Opener for manager walkthroughs. The page is ordered for a first-time viewer:
1. What questions this tool answers (1 sentence)
2. What we've learned this season (plain-English insight cards)
3. What you can do with this (capability cards with page links)
4. See it for your team (team picker + jump to Team Report)
5. How we know this (collapsible -- model fit, methodology, data quality)
"""

# -- Path setup ---------------------------------------------------------------
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

st.set_page_config(page_title="Executive Overview | MiLB", page_icon="Overview", layout="wide")

LEVEL_NAMES = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}
LEVEL_ORDER = [11, 12, 13, 14]
HERO_TEAM = "Binghamton Rumble Ponies"


# -- Data loaders -------------------------------------------------------------

@st.cache_data(ttl=600)
def load_platform_kpis():
    return query_df("""
        SELECT
            COUNT(DISTINCT team_id) AS teams,
            COUNT(*)                AS games,
            COUNT(DISTINCT season)  AS seasons
        FROM milb.game_features
    """)


@st.cache_data(ttl=600)
def load_model_performance():
    return query_df("""
        SELECT sport_id, mae, mape, r_squared
          FROM milb.model_runs
         ORDER BY sport_id
    """)


@st.cache_data(ttl=600)
def load_rec_count():
    return query_df("SELECT COUNT(*) AS n FROM milb.team_recommendations")


@st.cache_data(ttl=600)
def load_top_promo_lifts():
    """Top positive promo lifts at the league level -- our 'wins'."""
    return query_df("""
        SELECT promo_type, sport_id,
               marginal_lift::float AS marginal_lift,
               p_value::float       AS p_value
          FROM milb.promo_lift
         WHERE p_value < 0.05 AND scope = 'league_level' AND marginal_lift > 0
         ORDER BY marginal_lift DESC
         LIMIT 4
    """)


@st.cache_data(ttl=600)
def load_negative_promo_lifts():
    """Promotions that hurt attendance -- the surprising finds."""
    return query_df("""
        SELECT promo_type, sport_id,
               marginal_lift::float AS marginal_lift,
               p_value::float       AS p_value
          FROM milb.promo_lift
         WHERE p_value < 0.05 AND scope = 'league_level' AND marginal_lift < 0
         ORDER BY marginal_lift ASC
         LIMIT 3
    """)


@st.cache_data(ttl=600)
def load_weekend_vs_weekday():
    return query_df("""
        SELECT
            CASE WHEN is_weekend = TRUE THEN 'Weekend' ELSE 'Weekday' END AS day_type,
            AVG(attendance)::int AS avg_att,
            COUNT(*) AS games
          FROM milb.game_features
         WHERE attendance IS NOT NULL
         GROUP BY 1
    """)


@st.cache_data(ttl=600)
def load_stacking_effect():
    return query_df("""
        SELECT
            CASE WHEN promo_count >= 3 THEN '3+ promos' ELSE '1-2 promos' END AS stacking,
            AVG(capacity_utilization)::float AS avg_cap_util,
            COUNT(*) AS games
          FROM milb.game_features
         WHERE has_any_promo = TRUE AND capacity_utilization IS NOT NULL
         GROUP BY 1
    """)


@st.cache_data(ttl=600)
def load_rp_monthly_trend():
    """Monthly attendance for the hero team, last 2 seasons. Powers the comparator."""
    return query_df(f"""
        SELECT gf.season, gf.month, AVG(gf.attendance)::int AS avg_att
          FROM milb.game_features gf
          JOIN milb.teams t ON gf.team_id = t.team_id
         WHERE t.team_name = '{HERO_TEAM}'
           AND gf.attendance IS NOT NULL
           AND gf.game_type = 'R'
         GROUP BY gf.season, gf.month
         ORDER BY gf.season, gf.month
    """)


@st.cache_data(ttl=600)
def load_rp_snapshot():
    """One-line snapshot for the hero team (latest season)."""
    return query_df(f"""
        SELECT t.team_id, t.team_name,
               COALESCE(sp.sport_name, '') AS level,
               tm.avg_attendance, tm.avg_cap_util,
               tm.yoy_attendance_pct, tm.momentum_label,
               tm.season
          FROM milb.teams t
          LEFT JOIN milb.sports sp ON t.sport_id = sp.sport_id
          LEFT JOIN milb.team_momentum tm
                 ON t.team_id = tm.team_id
                AND tm.season = (SELECT MAX(season) FROM milb.team_momentum WHERE team_id = t.team_id)
         WHERE t.team_name = '{HERO_TEAM}'
         LIMIT 1
    """)


# -- Helpers ------------------------------------------------------------------

def _promo_label(promo_type: str) -> str:
    return promo_type.replace("has_", "").replace("_", " ").title()


# -- Page layout --------------------------------------------------------------

st.title("MiLB Attendance Intelligence")
st.markdown(
    "**This tool answers three questions about minor-league baseball attendance:**  \n"
    "1. Which ballparks are leaving seats empty, and which are outperforming?  \n"
    "2. Which promotions actually move the needle -- and which only look like they do?  \n"
    "3. What should each team do differently next season?"
)

st.divider()

# =============================================================================
# Section 1: What we've learned this season
# =============================================================================
st.subheader("What we've learned this season")
st.caption(
    "Findings below come from regression analysis across 120+ teams and three seasons. "
    "Numbers are marginal impact -- controlling for day of week, month, weather, and team baseline."
)

top_lifts = load_top_promo_lifts()
neg_lifts = load_negative_promo_lifts()
wk = load_weekend_vs_weekday()
stacking = load_stacking_effect()

# Row 1 -- 4 cards of baseball-language findings
r1c1, r1c2, r1c3, r1c4 = st.columns(4)

with r1c1:
    if not top_lifts.empty:
        best = top_lifts.iloc[0]
        st.success(
            f"**{_promo_label(best['promo_type'])} nights add "
            f"~{int(best['marginal_lift']):,} fans** at the "
            f"{LEVEL_NAMES.get(int(best['sport_id']), '?')} level.  \n"
            f"Every team already knows this works -- so we can *filter it out* "
            f"in the Recommendations page to surface the next-best levers."
        )
    else:
        st.info("Promo lift analysis pending -- run `analyze_promo_lift.py`.")

with r1c2:
    if len(top_lifts) >= 2:
        runner_up = top_lifts.iloc[1]
        st.info(
            f"**{_promo_label(runner_up['promo_type'])} adds "
            f"~{int(runner_up['marginal_lift']):,} fans** at the "
            f"{LEVEL_NAMES.get(int(runner_up['sport_id']), '?')} level.  \n"
            f"The second-biggest lever after the obvious one -- this is where "
            f"most teams have untapped headroom."
        )

with r1c3:
    if not neg_lifts.empty:
        worst = neg_lifts.iloc[0]
        st.warning(
            f"**{_promo_label(worst['promo_type'])} games draw "
            f"{int(worst['marginal_lift']):,} *fewer* fans** on average.  \n"
            f"Running a discount when the game would have sold anyway -- classic "
            f"cannibalization. Counter-intuitive finds like this are what "
            f"justify the analysis."
        )
    elif len(stacking) == 2:
        high = stacking[stacking["stacking"] == "3+ promos"]["avg_cap_util"]
        low  = stacking[stacking["stacking"] == "1-2 promos"]["avg_cap_util"]
        if not high.empty and not low.empty:
            gap = float(high.iloc[0]) - float(low.iloc[0])
            st.info(
                f"**Stacking 3+ promos lifts capacity utilization by {gap:.1%}.**  \n"
                f"Combining promotions beats running them alone -- but only up to a point. "
                f"See the Promotions page for the diminishing-returns curve."
            )

with r1c4:
    if len(wk) == 2:
        wkend = wk[wk["day_type"] == "Weekend"]["avg_att"]
        wkday = wk[wk["day_type"] == "Weekday"]["avg_att"]
        if not wkend.empty and not wkday.empty:
            gap = int(wkend.iloc[0]) - int(wkday.iloc[0])
            pct = gap / max(int(wkday.iloc[0]), 1) * 100
            st.info(
                f"**Weekends draw +{gap:,} more fans** ({pct:.0f}% higher) "
                f"than weekdays league-wide.  \n"
                f"Weekday strategy is where the gap gets closed -- or not."
            )

st.divider()

# =============================================================================
# Section 2: What you can do with this
# =============================================================================
st.subheader("What you can do with this")
st.caption("The descriptive dashboards (1-6) show what's happening. The ML pages (7-10) tell you what to do about it.")

cap1, cap2, cap3 = st.columns(3)

with cap1:
    st.markdown("#### Explore the data")
    st.page_link("pages/1_Attendance.py",   label="Attendance -- baselines & trends per team")
    st.page_link("pages/2_Promotions.py",   label="Promotions -- lift by type + Opportunity Finder")
    st.page_link("pages/3_Weather.py",      label="Weather -- temp, conditions, wind effects")
    st.page_link("pages/4_Opponents.py",    label="Opponents -- which visitors draw crowds")
    st.page_link("pages/5_Rehab_Assignments.py", label="Rehab -- MLB stars on rehab stints")
    st.page_link("pages/6_Scheduling.py",   label="Scheduling -- homestand, streaks, school calendar")

with cap2:
    st.markdown("#### Pattern recognition")
    st.page_link("pages/7_Promo_Strategy.py",
                 label="Promo Strategy -- your team's promotional archetype")
    st.page_link("pages/9_Competitive_Intel.py",
                 label="Competitive Intel -- find your weather/market twin")
    st.caption(
        "These pages cluster teams by their behavior and market, so you can see "
        "which peers you should be learning from."
    )

with cap3:
    st.markdown("#### Act on it")
    st.page_link("pages/8_Team_Report.py",
                 label="Team Report -- the written brief (Binghamton)")
    st.page_link("pages/10_Recommendations.py",
                 label="Recommendations -- prioritized actions per team")
    st.caption(
        "Each recommendation ties back to the evidence it's drawn from. "
        "The What-If simulator predicts attendance before a game is played."
    )

st.divider()

# =============================================================================
# Section 3: See it for your team
# =============================================================================
st.subheader("See it for your team")

rp = load_rp_snapshot()
st.caption(
    f"Full written briefs are currently generated for **{HERO_TEAM}** (the hero team). "
    "The data-view sections of Team Report work for any team via the dropdown there."
)

rp_cols = st.columns([3, 2])
with rp_cols[0]:
    if not rp.empty and pd.notna(rp.iloc[0]["avg_attendance"]):
        row = rp.iloc[0]
        rp1, rp2, rp3, rp4 = st.columns(4)
        rp1.metric("Avg Attendance",
                   f"{int(row['avg_attendance']):,}" if pd.notna(row['avg_attendance']) else "-")
        rp2.metric("Cap Utilization",
                   f"{float(row['avg_cap_util']):.0%}" if pd.notna(row['avg_cap_util']) else "-")
        yoy = row.get("yoy_attendance_pct")
        rp3.metric("YoY Change",
                   f"{float(yoy):+.1%}" if pd.notna(yoy) else "-")
        rp4.metric("Momentum", row.get("momentum_label", "-") or "-")
        st.caption(f"Latest snapshot from season {int(row['season'])}.")
    else:
        st.info("No momentum snapshot yet for the hero team. Run `build_competitive_intel.py`.")

with rp_cols[1]:
    st.page_link("pages/8_Team_Report.py", label=f"Open the {HERO_TEAM} brief")
    st.page_link("pages/10_Recommendations.py", label="See the recommendation queue")
    st.page_link("pages/9_Competitive_Intel.py", label="Find peer teams to emulate")

# Season-over-season comparator sparkline for the hero team
rp_trend = load_rp_monthly_trend()
if not rp_trend.empty and rp_trend["season"].nunique() >= 2:
    st.markdown("**Attendance: this season vs last**")
    season_delta_metric(
        HERO_TEAM, rp_trend, value_col="avg_att", fmt="{:,.0f}",
        help="Monthly sparkline -- latest two seasons stacked.",
    )

st.divider()

# =============================================================================
# Section 4: How we know this (collapsible, translated stats)
# =============================================================================
with st.expander("How we know this -- model fit, methodology, data quality"):

    # --- Model fit across all 4 levels (the 4-level row) ---------------------
    st.markdown("### Prediction accuracy")
    st.caption(
        "One attendance-prediction model is trained per classification level. "
        "Mean Absolute Error (MAE) is how many fans off, on average, a prediction is. "
        "R-squared is the share of attendance variation the model explains -- higher is better, 1.0 would be perfect."
    )

    model_perf = load_model_performance()
    if not model_perf.empty:
        # Build 4 columns matching LEVEL_ORDER so readers can compare fairly
        cols = st.columns(len(LEVEL_ORDER))
        perf_map = {int(r["sport_id"]): r for _, r in model_perf.iterrows()}
        for i, sid in enumerate(LEVEL_ORDER):
            r = perf_map.get(sid)
            if r is None:
                cols[i].metric(LEVEL_NAMES[sid], "no model")
                continue
            mae = int(r["mae"]) if pd.notna(r["mae"]) else None
            r2 = float(r["r_squared"]) if pd.notna(r["r_squared"]) else None
            cols[i].metric(
                LEVEL_NAMES[sid],
                f"within ~{mae:,} fans" if mae is not None else "-",
                help=f"R-squared = {r2:.2f}" if r2 is not None else None,
            )
        st.caption(
            "Translation: at Double-A we predict a typical game's crowd within about "
            f"{int(perf_map.get(12, {}).get('mae', 0)):,} fans -- roughly one section of the ballpark."
            if 12 in perf_map else ""
        )
    else:
        st.info("No models trained yet. Run `scripts/train_attendance_model.py`.")

    st.markdown("### Data coverage")
    kpis = load_platform_kpis()
    rec_count = load_rec_count()
    k1, k2, k3, k4 = st.columns(4)
    if not kpis.empty:
        k1.metric("Teams tracked", f"{int(kpis['teams'].iloc[0]):,}")
        k2.metric("Games analyzed", f"{int(kpis['games'].iloc[0]):,}")
        k3.metric("Seasons", f"{int(kpis['seasons'].iloc[0])}")
    if not rec_count.empty:
        k4.metric("Recommendations generated", f"{int(rec_count['n'].iloc[0]):,}")

    st.markdown("### Methodology")
    st.markdown("""
**Promotion lift** -- OLS regression measures the marginal attendance change
from each promotion type, holding day of week, month, weather, opponent, and
team baseline constant. Values with *p* &lt; 0.05 are what we trust.

**Peer clustering** -- K-Means groups teams by market size, demographics, and
venue capacity. A separate strategy clustering groups by promotional philosophy.
Both exist so comparisons are apples-to-apples.

**Attendance prediction** -- XGBoost, one model per classification level, tuned
with Optuna. SHAP values explain which features drove each prediction.

**Demographics** -- Census ACS 5-year estimates (2015-2024), refreshed annually.
Games are joined to the census year *before* their season -- that's what a team
could have known at scheduling time.
    """)

    st.markdown("### Sources")
    st.markdown("""
- **MLB Stats API** -- game schedules, attendance, scores, promotions, roster moves (the same feed MLB.com and MiLB.com use).
- **U.S. Census Bureau ACS** -- population, median income, poverty rate at city and metro level.
- **Open-Meteo** -- temperature, precipitation, wind at game date and venue location.
    """)

    st.markdown("### Known limits")
    st.markdown("""
- ~20-30% of games have no attendance reported (rainouts, postponements). These are excluded from analysis.
- Promotion data from the MLB API only flows back for the current season -- 2023/2024 promos aren't available.
- Written briefs are LLM-generated and produced for league rollups and the hero team only.
    """)


# ── Cross-page navigation + footer ───────────────────────────────────────────
see_also([
    ("Team Report",       "pages/8_Team_Report.py",       f"the {HERO_TEAM} brief"),
    ("Recommendations",   "pages/10_Recommendations.py",  "prioritized actions across the league"),
    ("Competitive Intel", "pages/9_Competitive_Intel.py", "peer comparisons and momentum"),
])
render_footer(scripts=["promo_lift", "recommendations", "cluster_peers", "competitive_intel"])
