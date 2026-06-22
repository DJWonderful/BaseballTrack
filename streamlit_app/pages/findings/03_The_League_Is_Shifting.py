"""Finding 3 -- The League is Shifting.

Context for the briefing book. The other two findings are Binghamton-specific;
this one zooms out and says: the entire league's weekend premium is compressing,
weekday attendance is holding. That reframes Saturday/Sunday strategy.

Style is intentionally lighter on prescription -- we don't claim to know
*why* the league shift is happening (economy, demographics, MLB.tv, etc.
are all speculative). We do claim to see it.
"""

# -- Path setup ---------------------------------------------------------------
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils.db import query_df
from utils.footer import render_footer
from utils.navigation import see_also
from utils.theme import SEASON_COLORS

st.set_page_config(page_title="The League is Shifting", layout="wide")

DAY_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# -- Data loaders -------------------------------------------------------------

def load_dow_by_season() -> pd.DataFrame:
    """League-wide attendance and cap util by season x day-of-week.

    Filtered to a common Mar 27 - Jun 20 window across years so the in-flight
    2026 season is comparable to prior years (apples-to-apples).
    Not decorated with @st.cache_data directly — query_df already caches."""
    return query_df("""
        WITH same_window AS (
          SELECT season, day_of_week, attendance, capacity_utilization
            FROM milb.game_features
           WHERE game_type = 'R'
             AND attendance IS NOT NULL
             AND (
                  (season = 2023 AND game_date BETWEEN DATE '2023-03-27' AND DATE '2023-06-20')
               OR (season = 2024 AND game_date BETWEEN DATE '2024-03-27' AND DATE '2024-06-20')
               OR (season = 2025 AND game_date BETWEEN DATE '2025-03-27' AND DATE '2025-06-20')
               OR (season = 2026 AND game_date BETWEEN DATE '2026-03-27' AND DATE '2026-06-20')
             )
        )
        SELECT season,
               day_of_week,
               COUNT(*)                       AS n,
               AVG(attendance)                AS avg_att,
               AVG(capacity_utilization)      AS avg_cap
          FROM same_window
         GROUP BY season, day_of_week
         ORDER BY season, day_of_week
    """)


def load_league_season_trend() -> pd.DataFrame:
    """League average attendance per season -- apples-to-apples window.
    Not decorated with @st.cache_data directly — query_df already caches."""
    return query_df("""
        SELECT season,
               COUNT(*) AS n_games,
               AVG(attendance) AS avg_att,
               AVG(capacity_utilization) AS avg_cap
          FROM milb.game_features
         WHERE game_type = 'R'
           AND attendance IS NOT NULL
           AND (
                (season = 2023 AND game_date BETWEEN DATE '2023-03-27' AND DATE '2023-06-20')
             OR (season = 2024 AND game_date BETWEEN DATE '2024-03-27' AND DATE '2024-06-20')
             OR (season = 2025 AND game_date BETWEEN DATE '2025-03-27' AND DATE '2025-06-20')
             OR (season = 2026 AND game_date BETWEEN DATE '2026-03-27' AND DATE '2026-06-20')
           )
         GROUP BY season
         ORDER BY season
    """)


# -- Render -------------------------------------------------------------------

st.title("The League is Shifting")
st.markdown(
    "### The weekend premium is shrinking across the entire league — and "
    "weekday attendance is holding steady."
)
st.caption(
    "Friday and Saturday used to be the league's seat-fillers. Three years "
    "later that's less true. This is context for the Binghamton story: the "
    "structural ground is moving under everyone's feet."
)

st.divider()

# ─── Section 1: What we see ────────────────────────────────────────────────
st.subheader("What we see")

df = load_dow_by_season()
if not isinstance(df, pd.DataFrame) or df.empty:
    st.warning("League trend data is not available yet. Run `python scripts/export_for_app.py` to refresh the data snapshot.")
    st.stop()

df["day"] = df["day_of_week"].map(DAY_NAMES)
df["cap_pct"] = (df["avg_cap"] * 100).round(1)
df["season"] = df["season"].astype(str)

# Drop Monday — only ~10 games a year, noisy
plot_df = df[df["day"].isin(["Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])].copy()
plot_df["day"] = pd.Categorical(plot_df["day"], categories=DAY_ORDER, ordered=True)
plot_df = plot_df.sort_values(["day", "season"])

fig = px.line(
    plot_df, x="day", y="cap_pct", color="season",
    markers=True,
    color_discrete_map={k: v for k, v in SEASON_COLORS.items() if isinstance(k, str)},
    labels={"cap_pct": "% of seats filled", "day": "", "season": "Season"},
    category_orders={"day": DAY_ORDER},
)
fig.update_traces(line=dict(width=3), marker=dict(size=10))
fig.update_layout(
    yaxis_ticksuffix="%",
    yaxis_range=[35, 75],
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(t=40, b=20),
)
st.plotly_chart(fig, use_container_width=True)

st.caption(
    "Each line is one season's league average. Tuesday/Wednesday/Thursday "
    "barely move year over year. Friday and Saturday are the ones falling. "
    "All four seasons are measured over the same March-to-June window so "
    "2026 (in flight) is fairly comparable."
)

st.divider()

# ─── Section 2: Why it matters ─────────────────────────────────────────────
st.subheader("Why it matters")

# Compute the weekend compression number
fri_23 = float(df[(df["season"] == "2023") & (df["day"] == "Fri")]["cap_pct"].iloc[0])
sat_23 = float(df[(df["season"] == "2023") & (df["day"] == "Sat")]["cap_pct"].iloc[0])
fri_26 = float(df[(df["season"] == "2026") & (df["day"] == "Fri")]["cap_pct"].iloc[0])
sat_26 = float(df[(df["season"] == "2026") & (df["day"] == "Sat")]["cap_pct"].iloc[0])

c1, c2, c3 = st.columns(3)
c1.metric("Friday cap util", f"{fri_26:.1f}%", f"{fri_26 - fri_23:+.1f}pp vs 2023",
          delta_color="inverse")
c2.metric("Saturday cap util", f"{sat_26:.1f}%", f"{sat_26 - sat_23:+.1f}pp vs 2023",
          delta_color="inverse")
c3.metric("Weekend premium", "Compressing",
          "Weekday util barely moved")

st.markdown(
    "The historical assumption — *Friday and Saturday are reliably big nights, "
    "so cluster the marquee promotions there* — was true in 2023. It is "
    "measurably less true in 2026.\n\n"
    "This does not excuse the Binghamton Saturday gap. It reframes how "
    "much the calendar can be expected to lift attendance on its own. If "
    "the weekend keeps compressing, *every* team needs sharper promo "
    "execution to hit historical numbers."
)

st.divider()

# ─── Section 3: What's behind it ───────────────────────────────────────────
st.subheader("What's behind it")
st.markdown(
    "We do not have a definitive answer for why the league-wide weekend "
    "premium is shrinking. Candidate explanations — none yet proven in this "
    "dataset — include:\n"
    "- Households increasingly using Saturday for other commitments (kids' "
    "sports, travel sports, entertainment competition)\n"
    "- MLB.tv and streaming substituting for live attendance, more on "
    "weekends than weekdays\n"
    "- Post-pandemic norms taking longer than expected to normalize\n"
    "- General economic pressure on discretionary spend\n\n"
    "What we *can* say is the pattern is league-wide and consistent, not a "
    "Binghamton-only effect. The Promo Strategy and Competitive Intel pages "
    "have deeper cuts on how individual teams are responding."
)

st.divider()

# ─── Section 4: What to do ────────────────────────────────────────────────
st.subheader("What to do with this")

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Strategic — for multi-year planning**")
    st.markdown(
        "- Don't assume the weekend premium recovers. Plan as if Friday and "
        "Saturday continue to compress 1–2pp per year.\n"
        "- The marginal value of getting a marquee promotion onto the "
        "*right* night (the Saturdays finding) goes up, not down, in this "
        "environment.\n"
        "- Diversification: weekdays haven't fallen. There's defensible "
        "argument for shifting some promotional spend from a saturated "
        "weekend onto a Thursday or Tuesday that hasn't declined."
    )

with c2:
    st.markdown("**Tactical — for in-season conversations**")
    st.markdown(
        "- Use this when peers, ownership, or partners ask *\"why is "
        "attendance softer this year?\"* — it's the industry, not just "
        "Binghamton.\n"
        "- If a sponsor is asking about Saturday ROI, the trend line above "
        "is the honest frame: Saturdays everywhere are less of a lock "
        "than they used to be.\n"
        "- Don't use this to lower the bar internally. The Binghamton "
        "Saturday gap is on top of the league shift, not explained by it."
    )

st.divider()

# ─── Section 5: See also ──────────────────────────────────────────────────
see_also([
    ("Executive Overview",
     "pages/0_Executive_Overview.py",
     "league-wide attendance and momentum at a glance"),
    ("Competitive Intel",
     "pages/9_Competitive_Intel.py",
     "team-level momentum and peer comparisons"),
    ("Attendance",
     "pages/1_Attendance.py",
     "detailed attendance cuts by team and date"),
])

render_footer(scripts=["build_features"])
