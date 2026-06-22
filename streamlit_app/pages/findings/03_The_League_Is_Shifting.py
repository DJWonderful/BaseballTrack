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

st.title("3. The League is Shifting")
st.markdown(
    "### Friday and Saturday used to be the league's best nights. They still "
    "are — but the gap is shrinking, and weekdays are holding up."
)
st.caption(
    "This is the only finding on the site that's about *all* of Minor League "
    "Baseball, not just Binghamton. The ground is moving under everyone's "
    "feet, which makes the choices on the previous two pages more important, "
    "not less."
)

with st.container(border=True):
    st.markdown(
        "**About this page.** Every number here is league-wide — every team, "
        "every level, four seasons. Binghamton is not called out specifically. "
        "The point is to give you the backdrop the Saturdays and Sundays "
        "findings sit on top of."
    )

st.divider()

# ─── Section 1: What we see ────────────────────────────────────────────────
st.subheader("What the data shows")

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

st.markdown(
    "**Chart 1 — league-wide.** Average share of seats filled, by day of the "
    "week, with one line per season."
)

fig = px.line(
    plot_df, x="day", y="cap_pct", color="season",
    markers=True,
    color_discrete_map={k: v for k, v in SEASON_COLORS.items() if isinstance(k, str)},
    labels={"cap_pct": "% of seats filled", "day": "Day of week",
            "season": "Season"},
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
    "How to read it: each line is one season, across the week. Tuesday, "
    "Wednesday and Thursday barely move year over year. Friday and Saturday "
    "are the ones falling — that's the shift. All four seasons are measured "
    "across the same March-to-June window so 2026 (still in progress) is "
    "fairly comparable to prior years."
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
c1.metric("Friday: share of seats filled",
          f"{fri_26:.1f}%",
          f"{fri_26 - fri_23:+.1f} points vs. 2023",
          delta_color="inverse")
c2.metric("Saturday: share of seats filled",
          f"{sat_26:.1f}%",
          f"{sat_26 - sat_23:+.1f} points vs. 2023",
          delta_color="inverse")
c3.metric("Weekday share of seats filled",
          "Barely changed",
          "Tue / Wed / Thu have held flat")

st.markdown(
    "The old rule of thumb — *Friday and Saturday are reliable big nights, "
    "so cluster the marquee promotions there* — was true in 2023. It's "
    "noticeably less true in 2026.\n\n"
    "This doesn't excuse Binghamton's Saturday gap (that's still on top of "
    "the league-wide trend, not explained by it). What it does mean is "
    "every team has to work harder to hit the attendance numbers it "
    "used to. The right promotion on the right night matters more than "
    "it used to."
)

st.divider()

# ─── Section 3: What's behind it ───────────────────────────────────────────
st.subheader("What's behind it — best guesses")
st.markdown(
    "There's no clean single answer for why Friday and Saturday are softening "
    "league-wide. The most plausible explanations — none of them proven "
    "inside this dataset — include:\n"
    "- Households filling Saturday with other things (kids' sports, travel "
    "sports, more competing entertainment)\n"
    "- Streaming (MLB.tv, etc.) substituting for live attendance, more on "
    "weekends than weekdays\n"
    "- Post-pandemic patterns taking longer to settle than people expected\n"
    "- General economic pressure on discretionary spending\n\n"
    "What we *can* say with confidence: this pattern is everywhere, not just "
    "Binghamton."
)

st.divider()

# ─── Section 4: What to do ────────────────────────────────────────────────
st.subheader("What to do with this")

c1, c2 = st.columns(2)
with c1:
    st.markdown("**For multi-year planning**")
    st.markdown(
        "- Don't assume the weekend bounces back. Plan as if Friday and "
        "Saturday continue to soften a point or two each year.\n"
        "- Getting the right promotion onto the right weekend night (see the "
        "Saturdays finding) becomes *more* valuable in this environment, "
        "not less.\n"
        "- Weekdays haven't fallen. There's a defensible argument for "
        "shifting some marketing spend from a saturated Saturday onto a "
        "Thursday or Tuesday that hasn't declined."
    )

with c2:
    st.markdown("**For in-season conversations**")
    st.markdown(
        "- Useful when ownership, sponsors, or partners ask *\"why is "
        "attendance softer this year?\"* — it's the industry, not Binghamton.\n"
        "- If a sponsor is asking about Saturday performance, the chart "
        "above is the honest frame: Saturdays everywhere are less of a lock "
        "than they used to be.\n"
        "- Don't use this to lower the bar internally. The Binghamton "
        "Saturday gap is on top of the league trend, not explained by it."
    )

st.divider()

# ─── Next + See also ──────────────────────────────────────────────────────
st.markdown("**Next page in the walk-through:**")
st.page_link("pages/findings/04_Rituals.py", label="Next: 4. Rituals →")

st.markdown("")
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
