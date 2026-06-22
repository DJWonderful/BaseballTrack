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

st.title("1. Saturdays")
st.markdown(
    "### Friday outdraws Saturday at home in Binghamton — every season since 2023."
)
st.caption(
    "Four straight years, the same pattern: Friday is the bigger night and "
    "Saturday underperforms. The most likely cause shows up in the promotional "
    "calendar."
)

with st.container(border=True):
    st.markdown(
        "**About this page.** This is the headline finding. Most of the numbers "
        "on this page are about Binghamton specifically. Two charts compare "
        "Binghamton to other Double-A teams so you can see how unusual the "
        "pattern is. Each chart is labeled with what you're looking at."
    )

st.divider()

# ─── Section 1: What we see ────────────────────────────────────────────────
st.subheader("What the data shows")

rp = load_rp_fri_sat()
if not isinstance(rp, pd.DataFrame) or rp.empty:
    st.warning("No Friday/Saturday attendance data available.")
    st.stop()

rp["day"] = rp["day_of_week"].map({FRI_DOW: "Friday", SAT_DOW: "Saturday"})
rp["season"] = rp["season"].astype(str)

st.markdown(
    "**Chart 1 — Binghamton only.** Average attendance at home Friday games "
    "vs. home Saturday games, season by season."
)

fig = px.bar(
    rp,
    x="season", y="avg_att", color="day",
    barmode="group",
    color_discrete_map={"Friday": "#3a9bd5", "Saturday": RP_COLOR},
    labels={"avg_att": "Average fans per game", "season": "Season", "day": ""},
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
    "How to read it: each pair of bars is one season. Friday in blue, "
    "Saturday in purple. The Friday bar is taller every year — even in "
    "2024 when overall attendance climbed. 2026 is partial (through June). "
    "The July 3-5 holiday window is excluded so a July 4 fireworks night "
    "isn't comparing apples to oranges."
)

st.divider()

# ─── Section 2: Why it matters ─────────────────────────────────────────────
st.subheader("Why it matters — in seats")

# Use the most recent complete season (2025) for the seats translation
yr_2025 = rp[rp["season"] == "2025"]
if not yr_2025.empty:
    fri_2025 = float(yr_2025[yr_2025["day"] == "Friday"]["avg_att"].iloc[0])
    sat_2025 = float(yr_2025[yr_2025["day"] == "Saturday"]["avg_att"].iloc[0])
    n_sat_2025 = int(yr_2025[yr_2025["day"] == "Saturday"]["n_games"].iloc[0])
    gap_2025 = fri_2025 - sat_2025
    annual_fans_2025 = gap_2025 * n_sat_2025

    col1, col2, col3 = st.columns(3)
    col1.metric("Friday average, 2025", f"{fri_2025:,.0f} fans")
    col2.metric("Saturday average, 2025", f"{sat_2025:,.0f} fans",
                f"−{gap_2025:,.0f} vs. Friday",
                delta_color="inverse")
    col3.metric("If Saturday matched Friday",
                f"~{annual_fans_2025:,.0f} more fans",
                f"across {n_sat_2025} home Saturdays")

st.markdown(
    "If Saturdays drew like Fridays in 2025, the ballpark would have welcomed "
    "roughly **{:,} more fans through the gates over the season** — without "
    "scheduling a single extra game.".format(int(annual_fans_2025))
)

st.divider()

# ─── Section 3: What's behind it ───────────────────────────────────────────
st.subheader("What's behind it — the promo calendar")
st.markdown(
    "**The promo calendar is flipped.** Across Double-A, the teams that draw "
    "well on Saturday put **fireworks on Saturday**. Binghamton does the "
    "opposite — fireworks on Friday, a giveaway on Saturday. The pattern "
    "started in 2024 and has held since."
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
        "sat_winner": "Double-A teams that win Saturday",
        "neutral":    "Double-A teams: neutral",
        "sat_loser":  "Double-A teams that lose Saturday",
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

    order = ["Double-A teams that win Saturday",
             "Double-A teams: neutral",
             "Double-A teams that lose Saturday",
             "Binghamton"]
    long["group"] = pd.Categorical(long["group"], categories=order, ordered=True)
    long = long.sort_values("group")
    long["rate_pct"] = (long["rate"] * 100).round(0)

    st.markdown(
        "**Chart 2 — comparing Binghamton to the rest of Double-A.** What share "
        "of each group's Saturdays carry fireworks vs. a giveaway, in 2025."
    )

    fig2 = px.bar(
        long, x="group", y="rate_pct", color="promo",
        barmode="group",
        color_discrete_map={"Fireworks": "#d4572e", "Giveaway": "#5aa9d9"},
        labels={"rate_pct": "% of Saturday games", "group": "", "promo": ""},
        text=long["rate_pct"].astype(int).astype(str) + "%",
    )
    fig2.update_traces(textposition="outside")
    fig2.update_layout(
        yaxis_range=[0, 110],
        yaxis_ticksuffix="%",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=40, b=20),
        title_text=f"Saturday promotions, Double-A teams in {latest}",
    )
    st.plotly_chart(fig2, use_container_width=True)
    st.caption(
        "How to read it: each pair of bars is one group of teams. The left "
        "group — teams that draw well on Saturday — runs fireworks on roughly "
        "half their Saturdays. Binghamton (far right) ran fireworks on **zero** "
        "Saturdays, and a giveaway on most of them. That's the inversion."
    )

# RP's own multi-year promo mix table
if not rp_promo.empty:
    show = rp_promo[rp_promo["season"] >= 2025].copy()
    show["Fireworks %"] = (show["pct_fireworks"] * 100).round(0).astype(int).astype(str) + "%"
    show["Giveaway %"]  = (show["pct_giveaway"]  * 100).round(0).astype(int).astype(str) + "%"
    show = show.rename(columns={"season": "Season", "n_sat": "Saturdays"})
    show = show[["Season", "Saturdays", "Fireworks %", "Giveaway %"]]
    st.markdown("**Table — Binghamton only. Saturday promotions by year:**")
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.caption(
        "Promotional data is reliable from 2025 onward (the league started "
        "exposing it publicly that year). The pattern hasn't changed in 2026."
    )

st.divider()

# ─── Section 4: What to do ────────────────────────────────────────────────
st.subheader("What to do with this")

c1, c2 = st.columns(2)
with c1:
    st.markdown("**For 2027 — the big move**")
    st.markdown(
        "- When the 2027 fireworks calendar is negotiated with the city, the "
        "data supports moving the standing fireworks night from **Friday to "
        "Saturday**.\n"
        "- A simple swap — fireworks on Saturday, giveaway on Friday — is "
        "projected to bring in **a few hundred more fans per home weekend**. "
        "That estimate comes from comparing every 2025 game against itself "
        "with the promotional flag flipped on and off, using a forecasting "
        "model trained on the full league.\n"
        "- This is the single biggest change the data points to."
    )

with c2:
    st.markdown("**For the rest of 2026 — smaller moves**")
    st.markdown(
        "- The 2026 fireworks calendar is already locked with the city, so "
        "Saturday fireworks aren't available this season.\n"
        "- The remaining Saturdays without fireworks are still strong "
        "candidates for any other high-draw promotion you have on hand — "
        "concerts, marquee giveaways, theme nights.\n"
        "- The single best Saturday to test a bigger-than-usual promotion is "
        "**July 18**, currently a 1 p.m. matinee with no headline event."
    )

st.divider()

# ─── Next + See also ──────────────────────────────────────────────────────
st.markdown("**Next page in the walk-through:**")
st.page_link("pages/findings/02_Sundays.py", label="Next: 2. Sundays →")

st.markdown("")
see_also([
    ("Weekend Playbook",
     "pages/11_Weekend_Playbook.py",
     "the deeper analysis this finding came out of"),
    ("Hypothesis Lab",
     "pages/13_Hypothesis_Lab.py",
     "how individual promotions affect attendance, modeled per game"),
    ("Recommendations",
     "pages/10_Recommendations.py",
     "the prioritized recommendation list for Binghamton"),
])

render_footer(scripts=["build_features", "promo_lift_cf"])
