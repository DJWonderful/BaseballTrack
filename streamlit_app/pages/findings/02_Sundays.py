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

st.title("2. Sundays")
st.markdown(
    "### Binghamton's Sunday attendance has fallen every year since 2023. "
    "The rest of Double-A has held steady."
)
st.caption(
    "Unlike Saturday, the Sunday gap doesn't have one tidy cause yet. This "
    "page shows the gap clearly and lays out the most likely explanations, "
    "honestly labeled as hypotheses rather than conclusions."
)

with st.container(border=True):
    st.markdown(
        "**About this page.** The first chart compares Binghamton to the "
        "league-wide Double-A average. After that, the page returns to "
        "Binghamton-only numbers and finishes with a small list of "
        "high-performing Double-A teams worth studying."
    )

st.divider()

# ─── Section 1: What we see ────────────────────────────────────────────────
st.subheader("What the data shows")

rp = load_rp_sundays()
da = load_doublea_sundays()

if not isinstance(rp, pd.DataFrame) or rp.empty:
    st.warning("No Sunday data available.")
    st.stop()

rp = rp.assign(label="Binghamton")
da = da.assign(label="Double-A average")
combo = pd.concat([rp[["season", "avg_cap", "label"]], da[["season", "avg_cap", "label"]]])
combo["cap_pct"] = (combo["avg_cap"] * 100).round(1)
combo["season"] = combo["season"].astype(str)

st.markdown(
    "**Chart 1 — Binghamton vs. the Double-A league average.** Each line is "
    "the share of seats filled on Sundays, season by season."
)

fig = px.line(
    combo, x="season", y="cap_pct", color="label",
    markers=True,
    color_discrete_map={"Binghamton": RP_COLOR, "Double-A average": PEER_COLOR},
    labels={"cap_pct": "% of seats filled on Sundays",
            "season": "Season", "label": ""},
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
    "How to read it: Binghamton (purple) is the lower line, and it's falling. "
    "The Double-A average (grey) wobbles but stays in the 50% range. 2026 is "
    "partial (through mid-June)."
)

st.divider()

# ─── Section 2: Why it matters ─────────────────────────────────────────────
st.subheader("Why it matters — in seats")

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
    c1.metric("Binghamton Sunday avg, 2025", f"{rp_avg:,.0f} fans")
    c2.metric("Double-A Sunday avg, 2025", f"{da_avg:,.0f} fans",
              f"+{gap:,.0f} more than Binghamton")
    c3.metric("If Sundays matched the league",
              f"~{annual:,.0f} more fans",
              f"across {n_sun} home Sundays")

st.markdown(
    "Sunday is supposed to be the family-day matinee. League-wide it holds "
    "at roughly half-full. Binghamton has fallen below a third — and it has "
    "slipped every year. The opportunity is large. The question is what "
    "to do about it."
)

st.divider()

# ─── Section 3: What's behind it ───────────────────────────────────────────
st.subheader("What's behind it — three hypotheses, not yet proven")

st.markdown(
    "There are a few likely explanations. None of them is locked in the way "
    "the Saturday fireworks finding is. Listing them honestly so they can "
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
    st.markdown(
        "**Table — Binghamton only. Sunday promotions by year:**"
    )
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.caption(
        "Kids and Family promotions are on most Sundays — but the attendance "
        "numbers above suggest that isn't enough on its own."
    )

st.markdown("""
**Hypothesis 1 — the start time.**
Most Binghamton Sundays start at 1 p.m. Matinees draw less than evening
games at most ballparks. Worth testing whether one or two Sundays moved to
a 6 p.m. start would lift the gates.

**Hypothesis 2 — the promotional mix isn't enough.**
Kids and Family promotions are on the calendar most Sundays. They probably
help. But the Double-A teams that *win* on Sunday pair family programming
with bigger draws (concerts, marquee giveaways, after-game events). The mix
may matter more than any one promotion on its own.

**Hypothesis 3 — Saturday is bleeding into Sunday.**
If Saturday underdelivers (see the previous page), some of that weekend
fatigue may carry into Sunday. The two days may need to be diagnosed as
one weekend rather than two separate problems.
""")

# Peer inspiration -- who's killing Sundays
leaders = load_doublea_sunday_leaders()
if not leaders.empty:
    st.markdown(
        "**Table — five Double-A teams who treat Sunday very differently.** "
        "The best Sunday performers in 2025."
    )
    show = leaders.copy()
    show["Average attendance"] = show["avg_att"].round(0).astype(int)
    show["% of seats filled"] = (show["avg_cap"] * 100).round(0).astype(int).astype(str) + "%"
    show = show.rename(columns={"team_name": "Team", "n": "Sundays"})
    show = show[["Team", "Sundays", "Average attendance", "% of seats filled"]]
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.caption(
        "Portland and Somerset are running Sunday at close to a full house. "
        "Their Sunday playbooks are the most useful place to look for "
        "borrowable ideas."
    )

st.divider()

# ─── Section 4: What to do ────────────────────────────────────────────────
st.subheader("What to do with this")

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Bigger picture — needs another round of analysis**")
    st.markdown(
        "- Take a closer look at Portland's and Somerset's Sunday promotional "
        "calendars side-by-side with Binghamton's — what specifically do they "
        "run on Sunday that Binghamton doesn't?\n"
        "- Decide whether Sunday should be looked at on its own, or as the "
        "back half of a weekend (since the Saturday finding may be bleeding "
        "into here).\n"
        "- This finding earns a follow-up project. It isn't ready for a "
        "direct prescription yet."
    )

with c2:
    st.markdown("**For the rest of 2026 — what's testable now**")
    st.markdown(
        "- Pick one upcoming Sunday and over-program it. Bring forward a "
        "promotion that would normally land midweek (theme night, autograph "
        "session, big giveaway). Compare against the season-average Sunday.\n"
        "- If a 6 p.m. start is possible on even a single Sunday, that's a "
        "clean test of the matinee hypothesis.\n"
        "- One game won't prove the story — but it puts a real data point "
        "on the board to build on."
    )

st.info(
    "**Honest read:** this page raises the right questions but does not yet "
    "answer them. If Sunday becomes a meeting topic, the next step is a "
    "focused side-by-side study of Portland's and Somerset's playbook — "
    "not a campaign rollout.",
    icon=":material/lightbulb:",
)

st.divider()

# ─── Next + See also ──────────────────────────────────────────────────────
st.markdown("**Next page in the walk-through:**")
st.page_link("pages/findings/03_The_League_Is_Shifting.py",
             label="Next: 3. The League is Shifting →")

st.markdown("")
see_also([
    ("Peer Playbook",
     "pages/12_Peer_Playbook.py",
     "what other teams do that Binghamton could borrow"),
    ("Scheduling",
     "pages/6_Scheduling.py",
     "calendar effects including day-of-week and start time"),
    ("Promo Strategy",
     "pages/7_Promo_Strategy.py",
     "the full promotional strategy view"),
])

render_footer(scripts=["build_features"])
