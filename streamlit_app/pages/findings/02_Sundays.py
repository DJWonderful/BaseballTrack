"""Finding 2 -- Sundays.

New 2026 finding: RP Sunday attendance has dropped every season since 2023
and is now falling faster than the league.

The honest answer on "what's behind it" is: we don't have a single causal
story yet, the way we do for Saturdays. So this page lays out the evidence
and names the gap as a follow-up question rather than fabricating a
prescription.
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

st.set_page_config(page_title="Sundays", layout="wide")

RUMBLE_PONIES_ID = 505
DOUBLE_A_SPORT_ID = 12
SUN_DOW = 6  # game_features.day_of_week: Mon=0..Sun=6

RP_COLOR = "#b064a0"
PEER_COLOR = "#95a5a6"
LEAGUE_COLOR = "#3a9bd5"


# -- Data loaders -------------------------------------------------------------

@st.cache_data(ttl=600)
def load_rp_sundays() -> pd.DataFrame:
    return query_df(f"""
        SELECT season,
               COUNT(*) AS n,
               AVG(attendance) AS avg_att,
               AVG(capacity_utilization) AS avg_cap
          FROM milb.game_features
         WHERE team_id = {RUMBLE_PONIES_ID}
           AND day_of_week = {SUN_DOW}
           AND game_type = 'R'
           AND attendance IS NOT NULL
         GROUP BY season
         ORDER BY season
    """)


@st.cache_data(ttl=600)
def load_doublea_sundays() -> pd.DataFrame:
    return query_df(f"""
        SELECT season,
               COUNT(*) AS n,
               AVG(attendance) AS avg_att,
               AVG(capacity_utilization) AS avg_cap
          FROM milb.game_features
         WHERE sport_id = {DOUBLE_A_SPORT_ID}
           AND day_of_week = {SUN_DOW}
           AND game_type = 'R'
           AND attendance IS NOT NULL
         GROUP BY season
         ORDER BY season
    """)


@st.cache_data(ttl=600)
def load_doublea_sunday_leaders() -> pd.DataFrame:
    """Top Double-A Sunday performers in the most recent complete season (2025).

    Excludes teams with missing capacity (venue not in our capacity table).
    """
    return query_df(f"""
        SELECT t.team_name,
               COUNT(*) AS n,
               AVG(f.attendance) AS avg_att,
               AVG(f.capacity_utilization) AS avg_cap
          FROM milb.game_features f
          JOIN milb.teams t ON t.team_id = f.team_id
         WHERE f.sport_id = {DOUBLE_A_SPORT_ID}
           AND f.day_of_week = {SUN_DOW}
           AND f.season = 2025
           AND f.game_type = 'R'
           AND f.attendance IS NOT NULL
           AND f.capacity_utilization IS NOT NULL
         GROUP BY t.team_name
         HAVING COUNT(*) >= 8
         ORDER BY avg_cap DESC
         LIMIT 5
    """)


@st.cache_data(ttl=600)
def load_rp_sun_promo_mix() -> pd.DataFrame:
    return query_df(f"""
        WITH rp_sun AS (
          SELECT game_pk, season FROM milb.game_features
           WHERE team_id = {RUMBLE_PONIES_ID}
             AND day_of_week = {SUN_DOW}
             AND game_type = 'R'
             AND attendance IS NOT NULL
        ),
        gp AS (
          SELECT game_pk,
                 BOOL_OR(is_fireworks)      AS fw,
                 BOOL_OR(is_giveaway_item)  AS gv,
                 BOOL_OR(is_kids_event)     AS kids,
                 BOOL_OR(is_community_event) AS comm
            FROM milb.game_promotions
           WHERE enrichment_method IS NOT NULL
           GROUP BY game_pk
        )
        SELECT rp.season,
               COUNT(*) AS n,
               AVG(CASE WHEN gp.fw   THEN 1.0 ELSE 0.0 END) AS pct_fireworks,
               AVG(CASE WHEN gp.gv   THEN 1.0 ELSE 0.0 END) AS pct_giveaway,
               AVG(CASE WHEN gp.kids THEN 1.0 ELSE 0.0 END) AS pct_kids,
               AVG(CASE WHEN gp.comm THEN 1.0 ELSE 0.0 END) AS pct_community
          FROM rp_sun rp
          LEFT JOIN gp ON gp.game_pk = rp.game_pk
         GROUP BY rp.season
         ORDER BY rp.season
    """)


# -- Render -------------------------------------------------------------------

st.title("Sundays")
st.markdown(
    "### Sunday attendance has fallen every season since 2023, and the league "
    "has not."
)
st.caption(
    "Unlike the Saturday gap, the Sunday story does not have a single tidy "
    "cause yet. This page lays out the evidence; the prescription needs "
    "another round of analysis before it's actionable."
)

st.divider()

# ─── Section 1: What we see ────────────────────────────────────────────────
st.subheader("What we see")

rp = load_rp_sundays()
da = load_doublea_sundays()

if rp.empty:
    st.warning("No Sunday data available.")
    st.stop()

rp = rp.assign(label="Binghamton")
da = da.assign(label="Double-A average")
combo = pd.concat([rp[["season", "avg_cap", "label"]], da[["season", "avg_cap", "label"]]])
combo["cap_pct"] = (combo["avg_cap"] * 100).round(1)
combo["season"] = combo["season"].astype(str)

fig = px.line(
    combo, x="season", y="cap_pct", color="label",
    markers=True,
    color_discrete_map={"Binghamton": RP_COLOR, "Double-A average": PEER_COLOR},
    labels={"cap_pct": "% of seats filled", "season": "Season", "label": ""},
)
fig.update_traces(line=dict(width=3), marker=dict(size=10))
fig.update_layout(
    yaxis_ticksuffix="%",
    yaxis_range=[0, max(70, combo["cap_pct"].max() + 10)],
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(t=40, b=20),
)
st.plotly_chart(fig, use_container_width=True)

# Inline number table
rp_t = rp[["season", "n", "avg_att", "avg_cap"]].copy()
rp_t["Avg attendance"] = rp_t["avg_att"].round(0).astype(int)
rp_t["% of seats filled"] = (rp_t["avg_cap"] * 100).round(0).astype(int).astype(str) + "%"
rp_t = rp_t.rename(columns={"season": "Season", "n": "Sundays played"})
rp_t = rp_t[["Season", "Sundays played", "Avg attendance", "% of seats filled"]]
st.dataframe(rp_t, use_container_width=True, hide_index=True)

st.caption(
    "Binghamton's Sunday line is the lower one, and it's falling. The league "
    "line wobbles but stays in the 50s. Note 2026 is partial (mid-June)."
)

st.divider()

# ─── Section 2: Why it matters ─────────────────────────────────────────────
st.subheader("Why it matters")

# 2025 numbers for the dollars/seats translation
rp_2025 = rp[rp["season"] == 2025]
da_2025 = da[da["season"] == 2025]
if not rp_2025.empty and not da_2025.empty:
    rp_avg = float(rp_2025["avg_att"].iloc[0])
    da_avg = float(da_2025["avg_att"].iloc[0])
    n_sun = int(rp_2025["n"].iloc[0])
    gap = da_avg - rp_avg
    annual = gap * n_sun

    c1, c2, c3 = st.columns(3)
    c1.metric("Binghamton Sunday avg (2025)", f"{rp_avg:,.0f}")
    c2.metric("Double-A Sunday avg (2025)", f"{da_avg:,.0f}",
              f"+{gap:,.0f} vs Binghamton")
    c3.metric("Annualized gap", f"~{annual:,.0f} fans",
              f"{n_sun} home Sundays")

st.markdown(
    "Sunday is supposed to be a family day. League-wide it holds at about "
    "half capacity. Binghamton has fallen below a third — and it has fallen "
    "every year. The opportunity is large; the question is what to do about it."
)

st.divider()

# ─── Section 3: What's behind it ───────────────────────────────────────────
st.subheader("What's behind it (working hypotheses)")

st.markdown(
    "We have a few candidate explanations. None of them is yet supported the "
    "way the Saturday fireworks finding is. Listing them honestly so they can "
    "be tested:"
)

rp_promos = load_rp_sun_promo_mix()
if not rp_promos.empty:
    show = rp_promos[rp_promos["season"] >= 2025].copy()
    for col, label in [("pct_fireworks", "Fireworks %"),
                        ("pct_giveaway", "Giveaway %"),
                        ("pct_kids", "Kids / Family %"),
                        ("pct_community", "Community %")]:
        show[label] = (show[col] * 100).round(0).astype(int).astype(str) + "%"
    show = show.rename(columns={"season": "Season", "n": "Sundays"})
    show = show[["Season", "Sundays", "Fireworks %", "Giveaway %",
                 "Kids / Family %", "Community %"]]
    st.markdown("**Binghamton's Sunday promo mix:**")
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.caption(
        "Kids / Family is consistently on the calendar — but the attendance "
        "numbers above suggest it isn't enough on its own."
    )

st.markdown("""
**Hypothesis 1 — game start time.**
Most Binghamton Sundays are 1 p.m. matinees. Matinees underperform evening
games across the league. Worth testing whether a different start time on
selected Sundays moves the needle.

**Hypothesis 2 — promo ceiling.**
Kids / Family promos are present most Sundays. They may help, but other
Double-A teams that win on Sunday combine family programming with bigger
draws (concerts, marquee giveaways, post-game events). The mix matters as
much as any single flag.

**Hypothesis 3 — the Saturday spillover.**
If Saturday is the underwhelming night (see the Saturdays page), Sunday
may inherit some of that weekend disappointment. The two should be diagnosed
together, not separately.
""")

# Peer inspiration -- who's killing Sundays
leaders = load_doublea_sunday_leaders()
if not leaders.empty:
    st.markdown("**Some Double-A teams treat Sunday very differently:**")
    show = leaders.copy()
    show["Avg attendance"] = show["avg_att"].round(0).astype(int)
    show["% of seats filled"] = (show["avg_cap"] * 100).round(0).astype(int).astype(str) + "%"
    show = show.rename(columns={"team_name": "Team", "n": "Sundays"})
    show = show[["Team", "Sundays", "Avg attendance", "% of seats filled"]]
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.caption(
        "Top 5 Double-A by Sunday capacity, 2025. Portland and Somerset run "
        "Sunday at near-capacity. Their Sunday playbooks are worth a closer look."
    )

st.divider()

# ─── Section 4: What to do ────────────────────────────────────────────────
st.subheader("What to do with this")

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Strategic — needs another pass of analysis**")
    st.markdown(
        "- Build a peer-study cut comparing Binghamton's Sunday promo card to "
        "Portland's and Somerset's — what specifically do they put on Sunday "
        "that Binghamton doesn't?\n"
        "- Decide whether to diagnose Sunday on its own or as the back half "
        "of a weekend, since the Saturday finding may bleed in here.\n"
        "- This finding earns a follow-up project, not an immediate prescription."
    )

with c2:
    st.markdown("**Tactical — what's testable now**")
    st.markdown(
        "- Pick one upcoming Sunday and over-program it: bring forward a "
        "promotion that would normally land midweek (theme night, autograph "
        "session, big giveaway). Measure against the season-average Sunday.\n"
        "- If a 6 p.m. start is logistically possible on any single Sunday, "
        "that's a clean A/B test on the matinee hypothesis.\n"
        "- Don't draw conclusions from one game — but one game gets a real "
        "data point on the board."
    )

st.info(
    "**Honest framing:** This page raises the right questions but does not yet "
    "answer them. If Sunday is a meeting topic, the next step is a focused "
    "peer-study analysis — not a campaign roll-out.",
    icon=":material/lightbulb:",
)

st.divider()

# ─── Section 5: See also ──────────────────────────────────────────────────
see_also([
    ("Peer Playbook",
     "pages/12_Peer_Playbook.py",
     "what other teams do that Binghamton could borrow"),
    ("Scheduling",
     "pages/6_Scheduling.py",
     "calendar effects including day-of-week and start time"),
    ("Promo Strategy",
     "pages/7_Promo_Strategy.py",
     "the full promo strategy view"),
])

render_footer()
