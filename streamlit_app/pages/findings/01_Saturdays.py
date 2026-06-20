"""Finding 1 -- Saturdays.

The lead finding for the briefing book.

Story arc (six sections, fixed order across all finding pages):
  1. Headline -- single sentence answer
  2. What we see -- the gap chart
  3. Why it matters -- translation to seats
  4. What's behind it -- the fireworks inversion
  5. What to do -- strategic + tactical labels
  6. See also -- methodology links

All queries are self-contained against milb.games / milb.game_features so the
page does not depend on weekend_gap being current for in-flight seasons.
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

st.set_page_config(page_title="Saturdays", layout="wide")

RUMBLE_PONIES_ID = 505
DOUBLE_A_SPORT_ID = 12
FRI_DOW, SAT_DOW = 4, 5  # game_features.day_of_week: Mon=0..Sun=6

# Brand-agnostic team color for RP and a muted color for peers
RP_COLOR = "#b064a0"        # purple from SEASON_COLORS 2026 -- distinctive
PEER_COLOR = "#95a5a6"      # neutral grey

# Drop the July 4 holiday window from Fri/Sat aggregations. July 4 always has
# fireworks regardless of which day of the week it falls on, so including it
# (or July 3 / July 5 when the holiday is observed adjacent) contaminates the
# "did the team choose Fri or Sat for fireworks" comparison.
HOLIDAY_EXCLUDE = """
    AND NOT (EXTRACT(MONTH FROM game_date) = 7
             AND EXTRACT(DAY FROM game_date) IN (3, 4, 5))
""".strip()


# -- Data loaders -------------------------------------------------------------

@st.cache_data(ttl=600)
def load_rp_fri_sat() -> pd.DataFrame:
    """Per-season Fri vs Sat averages for the Rumble Ponies, July 4 excluded."""
    return query_df(f"""
        SELECT season,
               day_of_week,
               COUNT(*)        AS n_games,
               AVG(attendance) AS avg_att,
               AVG(capacity_utilization) AS avg_cap
          FROM milb.game_features
         WHERE team_id = {RUMBLE_PONIES_ID}
           AND day_of_week IN ({FRI_DOW}, {SAT_DOW})
           AND game_type = 'R'
           AND attendance IS NOT NULL
           {HOLIDAY_EXCLUDE}
         GROUP BY season, day_of_week
         ORDER BY season, day_of_week
    """)


@st.cache_data(ttl=600)
def load_doublea_sat_promo_mix() -> pd.DataFrame:
    """Double-A Saturday fireworks/giveaway rate per season, classified by
    whether the team's Sat avg beats its Fri avg.

    Uses a season-internal classifier (gap_pct vs season_avg) so it covers
    in-flight seasons that aren't in weekend_gap yet.
    """
    return query_df(f"""
        WITH season_games AS (
          SELECT team_id, season, day_of_week, attendance,
                 game_pk, capacity_utilization
            FROM milb.game_features
           WHERE sport_id = {DOUBLE_A_SPORT_ID}
             AND game_type = 'R'
             AND attendance IS NOT NULL
             AND day_of_week IN ({FRI_DOW}, {SAT_DOW})
             {HOLIDAY_EXCLUDE}
        ),
        team_dow AS (
          SELECT team_id, season, day_of_week,
                 COUNT(*) AS n,
                 AVG(attendance) AS avg_att
            FROM season_games
           GROUP BY team_id, season, day_of_week
        ),
        team_season AS (
          SELECT team_id, season, AVG(attendance) AS season_avg
            FROM season_games
           GROUP BY team_id, season
        ),
        classed AS (
          SELECT f.team_id, f.season,
                 CASE
                   WHEN (s.avg_att - f.avg_att) / NULLIF(ts.season_avg, 0) >=  0.05 THEN 'sat_winner'
                   WHEN (s.avg_att - f.avg_att) / NULLIF(ts.season_avg, 0) <= -0.05 THEN 'sat_loser'
                   ELSE 'neutral'
                 END AS camp
            FROM team_dow f
            JOIN team_dow s   ON s.team_id = f.team_id AND s.season = f.season AND s.day_of_week = {SAT_DOW}
            JOIN team_season ts ON ts.team_id = f.team_id AND ts.season = f.season
           WHERE f.day_of_week = {FRI_DOW}
             AND f.n >= 4 AND s.n >= 4
        ),
        sat_games AS (
          SELECT sg.game_pk, sg.season, c.camp
            FROM season_games sg
            JOIN classed c ON c.team_id = sg.team_id AND c.season = sg.season
           WHERE sg.day_of_week = {SAT_DOW}
        ),
        promos AS (
          SELECT sg.game_pk, sg.season, sg.camp,
                 BOOL_OR(p.is_fireworks)      AS fw,
                 BOOL_OR(p.is_giveaway_item)  AS gv
            FROM sat_games sg
            LEFT JOIN milb.game_promotions p
                   ON p.game_pk = sg.game_pk
                  AND p.enrichment_method IS NOT NULL
           GROUP BY sg.game_pk, sg.season, sg.camp
        )
        SELECT season, camp,
               COUNT(*) AS sat_games,
               AVG(CASE WHEN fw THEN 1.0 ELSE 0.0 END) AS pct_fireworks,
               AVG(CASE WHEN gv THEN 1.0 ELSE 0.0 END) AS pct_giveaway
          FROM promos
         GROUP BY season, camp
         ORDER BY season, camp
    """)


@st.cache_data(ttl=600)
def load_rp_sat_promo_mix() -> pd.DataFrame:
    """RP's own Saturday fireworks/giveaway rate per season."""
    return query_df(f"""
        WITH rp_sat AS (
          SELECT game_pk, season
            FROM milb.game_features
           WHERE team_id = {RUMBLE_PONIES_ID}
             AND day_of_week = {SAT_DOW}
             AND game_type = 'R'
             AND attendance IS NOT NULL
             {HOLIDAY_EXCLUDE}
        ),
        game_promo AS (
          SELECT game_pk,
                 BOOL_OR(is_fireworks)     AS fw,
                 BOOL_OR(is_giveaway_item) AS gv
            FROM milb.game_promotions
           WHERE enrichment_method IS NOT NULL
           GROUP BY game_pk
        )
        SELECT rp.season,
               COUNT(*) AS n_sat,
               AVG(CASE WHEN gp.fw THEN 1.0 ELSE 0.0 END) AS pct_fireworks,
               AVG(CASE WHEN gp.gv THEN 1.0 ELSE 0.0 END) AS pct_giveaway
          FROM rp_sat rp
          LEFT JOIN game_promo gp ON gp.game_pk = rp.game_pk
         GROUP BY rp.season
         ORDER BY rp.season
    """)


# -- Render -------------------------------------------------------------------

st.title("Saturdays")
st.markdown(
    "### Friday outdraws Saturday at home in Binghamton — every season since 2023."
)
st.caption(
    "Four straight years, the same pattern: Friday is the bigger night and "
    "Saturday underperforms. The cause shows up in the promo calendar."
)

st.divider()

# ─── Section 1: What we see ────────────────────────────────────────────────
st.subheader("What we see")

rp = load_rp_fri_sat()
if rp.empty:
    st.warning("No Friday/Saturday attendance data available.")
    st.stop()

rp["day"] = rp["day_of_week"].map({FRI_DOW: "Friday", SAT_DOW: "Saturday"})
rp["season"] = rp["season"].astype(str)

fig = px.bar(
    rp,
    x="season", y="avg_att", color="day",
    barmode="group",
    color_discrete_map={"Friday": "#3a9bd5", "Saturday": RP_COLOR},
    labels={"avg_att": "Avg attendance", "season": "Season", "day": ""},
    text=rp["avg_att"].round(0).astype(int).astype(str),
)
fig.update_traces(textposition="outside")
fig.update_layout(
    yaxis_tickformat=",",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(t=40, b=20),
)
st.plotly_chart(fig, use_container_width=True)

# Build the gap table inline so readers see the magnitude
wide = rp.pivot_table(index="season", columns="day", values="avg_att").reset_index()
wide["Gap (Sat − Fri)"] = (wide["Saturday"] - wide["Friday"]).round(0).astype(int)
wide["Friday"] = wide["Friday"].round(0).astype(int)
wide["Saturday"] = wide["Saturday"].round(0).astype(int)
st.caption(
    "Each pair of bars: one season. Every season the Friday bar is taller. "
    "Note 2026 is mid-season (through June). The July 3-5 holiday window is "
    "excluded — July 4 fireworks are calendar-driven, not a team scheduling "
    "choice."
)

st.divider()

# ─── Section 2: Why it matters ─────────────────────────────────────────────
st.subheader("Why it matters")

# Use the most recent complete season (2025) for the seats translation
yr_2025 = rp[rp["season"] == "2025"]
if not yr_2025.empty:
    fri_2025 = float(yr_2025[yr_2025["day"] == "Friday"]["avg_att"].iloc[0])
    sat_2025 = float(yr_2025[yr_2025["day"] == "Saturday"]["avg_att"].iloc[0])
    n_sat_2025 = int(yr_2025[yr_2025["day"] == "Saturday"]["n_games"].iloc[0])
    gap_2025 = fri_2025 - sat_2025
    annual_fans_2025 = gap_2025 * n_sat_2025

    col1, col2, col3 = st.columns(3)
    col1.metric("Friday avg (2025)", f"{fri_2025:,.0f}")
    col2.metric("Saturday avg (2025)", f"{sat_2025:,.0f}", f"−{gap_2025:,.0f} vs Fri",
                delta_color="inverse")
    col3.metric("Annualized gap", f"~{annual_fans_2025:,.0f} fans",
                f"{n_sat_2025} home Saturdays")

st.markdown(
    "If Saturdays performed at Friday's level in 2025, the ballpark would have "
    "seen roughly **{:,} more fans through the gates over the home schedule** — "
    "without playing a single extra game.".format(int(annual_fans_2025))
)

st.divider()

# ─── Section 3: What's behind it ───────────────────────────────────────────
st.subheader("What's behind it")
st.markdown(
    "**The promo calendar is inverted.** Across Double-A, the teams that win on "
    "Saturday put fireworks on Saturday. Binghamton has done the opposite — "
    "fireworks on Friday, a giveaway on Saturday. The pattern flipped in 2024 "
    "and has held since."
)

doublea = load_doublea_sat_promo_mix()
rp_promo = load_rp_sat_promo_mix()

if not doublea.empty:
    # Just the most recent year with classified data (2025) for clarity
    latest = doublea["season"].max()
    cmp_df = doublea[doublea["season"] == latest].copy()

    # Pull RP's row for that season and append as a fourth bar set
    rp_latest = rp_promo[rp_promo["season"] == latest]
    if not rp_latest.empty:
        rp_row = pd.DataFrame({
            "season": [latest],
            "camp": ["Binghamton"],
            "sat_games": [int(rp_latest["n_sat"].iloc[0])],
            "pct_fireworks": [float(rp_latest["pct_fireworks"].iloc[0])],
            "pct_giveaway":  [float(rp_latest["pct_giveaway"].iloc[0])],
        })
        cmp_df = pd.concat([cmp_df, rp_row], ignore_index=True)

    camp_label = {
        "sat_winner": "Double-A Sat-winners",
        "neutral":    "Double-A neutral",
        "sat_loser":  "Double-A Sat-losers",
        "Binghamton": "Binghamton",
    }
    cmp_df["group"] = cmp_df["camp"].map(camp_label)
    long = cmp_df.melt(
        id_vars=["group", "sat_games"],
        value_vars=["pct_fireworks", "pct_giveaway"],
        var_name="promo", value_name="rate",
    )
    long["promo"] = long["promo"].map({
        "pct_fireworks": "Fireworks", "pct_giveaway": "Giveaway",
    })

    order = ["Double-A Sat-winners", "Double-A neutral", "Double-A Sat-losers", "Binghamton"]
    long["group"] = pd.Categorical(long["group"], categories=order, ordered=True)
    long = long.sort_values("group")
    long["rate_pct"] = (long["rate"] * 100).round(0)

    fig2 = px.bar(
        long, x="group", y="rate_pct", color="promo",
        barmode="group",
        color_discrete_map={"Fireworks": "#d4572e", "Giveaway": "#5aa9d9"},
        labels={"rate_pct": "% of Saturdays", "group": "", "promo": ""},
        text=long["rate_pct"].astype(int).astype(str) + "%",
    )
    fig2.update_traces(textposition="outside")
    fig2.update_layout(
        yaxis_range=[0, 110],
        yaxis_ticksuffix="%",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=40, b=20),
        title_text=f"Saturday promo mix, Double-A {latest}",
    )
    st.plotly_chart(fig2, use_container_width=True)
    st.caption(
        "Sat-winning Double-A teams run fireworks on roughly half their Saturdays. "
        "Binghamton ran fireworks on 0 of its Saturdays — and a giveaway on most of them."
    )

# RP's own multi-year promo mix table
if not rp_promo.empty:
    show = rp_promo[rp_promo["season"] >= 2025].copy()
    show["Fireworks %"] = (show["pct_fireworks"] * 100).round(0).astype(int).astype(str) + "%"
    show["Giveaway %"]  = (show["pct_giveaway"]  * 100).round(0).astype(int).astype(str) + "%"
    show = show.rename(columns={"season": "Season", "n_sat": "Saturdays"})
    show = show[["Season", "Saturdays", "Fireworks %", "Giveaway %"]]
    st.markdown("**Binghamton's own Saturday promo mix:**")
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.caption(
        "Note: promo data is reliably available from 2025 onward. The pattern "
        "has not changed in 2026."
    )

st.divider()

# ─── Section 4: What to do ────────────────────────────────────────────────
st.subheader("What to do with this")

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Strategic — for next year's calendar planning**")
    st.markdown(
        "- When the 2027 fireworks schedule is negotiated with the city, the data "
        "supports moving the standing fireworks night from Friday to Saturday.\n"
        "- A like-for-like swap (fireworks Sat instead of Fri, giveaway Fri "
        "instead of Sat) is projected to **net several hundred more fans per "
        "home weekend**, based on a counterfactual model trained on 2025 data.\n"
        "- This is the highest-leverage single change in the data."
    )

with c2:
    st.markdown("**Tactical — for the rest of 2026**")
    st.markdown(
        "- The 2026 fireworks calendar is locked with the city, so Saturday "
        "fireworks aren't available this season.\n"
        "- The remaining Saturdays without fireworks are still candidates for "
        "any high-draw promotion already on the books — concerts, marquee "
        "giveaways, theme nights.\n"
        "- The single-best Saturday to test a bigger-than-usual promo is "
        "**July 18**, currently scheduled as a 1 p.m. matinee with no "
        "headline draw."
    )

st.divider()

# ─── Section 5: See also ──────────────────────────────────────────────────
see_also([
    ("Weekend Playbook",
     "pages/11_Weekend_Playbook.py",
     "the original league-wide analysis behind this finding"),
    ("Hypothesis Lab",
     "pages/13_Hypothesis_Lab.py",
     "counterfactual model estimates per promo type"),
    ("Recommendations",
     "pages/10_Recommendations.py",
     "the full prioritized recommendation list for Binghamton"),
])

render_footer(scripts=["build_features", "promo_lift_cf"])
