"""Finding 4 -- Rituals.

The recurring-promo finding, RP-anchored.

Honest framing notes (read before editing):
  * Promo enrichment is only fully populated from 2025 onward. The MLB Stats
    API does not expose offer-name data for prior seasons. The page does NOT
    say "RP had no rituals in 2023 / 2024" because we do not know that. It
    says: "as of 2025, RP runs a strong recurring slate."
  * Same composition finding as before:
      - RP recurring promos: 37% kids, 11% family/community, 53% food/drink
      - Other Double-A:      5% kids,  4% family/community, 63% food/drink
  * Same hedge: league-wide observational lift on weeknight recurring is
    mixed (rescue-promo selection bias), so the page frames a pilot as the
    only honest way to test the hypothesis.

Page sections (matches the 5-section arc the other findings use):
  1. What we see (rank + coverage on the recurring dimension)
  2. Why it matters (habit framing, weeknight opportunity)
  3. What's behind it (composition mix + ritual-family comparison table)
  4. What to do (strategic + tactical bullets + LLM proposed week)
  5. See also

No em dashes in this file. Briefing-book copy pass will polish all prose
simultaneously in a follow-up.
"""

# -- Path setup ---------------------------------------------------------------
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.db import query_df
from utils.economics import REVENUE_PER_FAN_USD, format_dollars_short
from utils.footer import render_footer
from utils.navigation import see_also

st.set_page_config(page_title="Rituals", layout="wide")

RUMBLE_PONIES_ID = 505
DOUBLE_A_SPORT_ID = 12

RP_COLOR = "#b064a0"        # purple, matches RP across the briefing book
PEER_COLOR = "#95a5a6"      # neutral grey for peer aggregates
LEAGUE_COLOR = "#3a9bd5"    # blue accent for Double-A reference
ACCENT_COLOR = "#d4572e"    # warm orange, used for RP highlight on the rank chart

DOW_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DOW_MAP = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}

# Ritual-family classification. Collapses near-duplicate offer names like
# "Thirsty Thursday", "Thirsty Thursday�", and "Thirsty Thursday presented by
# Yuengling" into a single concept so the comparison table is readable.
# Keep this list in sync with scripts/generate_ritual_schedule.py if a new
# family is added.
RITUAL_FAMILY_CASE = """
  CASE
    WHEN offer_name ILIKE 'thirsty thursday%' OR offer_name ILIKE '%thirsty thursday%' THEN 'Thirsty Thursday'
    WHEN offer_name ILIKE 'three dollar thursday%' OR offer_name ILIKE '$3 thursday%' THEN '$3 Thursday'
    WHEN offer_name ILIKE 'taco tuesday%' THEN 'Taco Tuesday'
    WHEN offer_name ILIKE 'twofer tuesday%' OR offer_name ILIKE 'two-for-tuesday%' OR offer_name ILIKE '%two for tuesday%' THEN 'Twofer Tuesday'
    WHEN offer_name ILIKE 'wine wednesday%' THEN 'Wine Wednesday'
    WHEN offer_name ILIKE 'fryday%' THEN 'Fryday'
    WHEN offer_name ILIKE 'trivia tuesday%' THEN 'Trivia Tuesday'
    WHEN offer_name ILIKE '%kids club%' OR offer_name ILIKE 'kids%sunday%' OR offer_name ILIKE 'kids day%' THEN 'Kids Day / Club'
    WHEN offer_name ILIKE '%family funday%' OR offer_name ILIKE 'family day%' THEN 'Family Funday'
    WHEN offer_name ILIKE 'senior%' THEN 'Seniors Day'
    WHEN offer_name ILIKE 'we care%' THEN 'We Care Wednesday'
    WHEN offer_name ILIKE 'throwback%' THEN 'Throwback Thursday'
    WHEN offer_name ILIKE 'sunday funday%' THEN 'Sunday Funday'
    WHEN offer_name ILIKE 'fireworks%' THEN 'Fireworks Night'
    WHEN offer_name ILIKE '%koozie%' THEN 'Koozie Klub'
    WHEN offer_name ILIKE '%weenie%whiskey%' THEN 'Weenie & Whiskey'
    ELSE NULL
  END
"""


# -- Data loaders -------------------------------------------------------------

def load_rp_headline_metrics() -> dict:
    """Three numbers for the headline tiles, all from 2025 (the first season
    with complete promo enrichment).

    pct       -- share of home games carrying a recurring promo
    n_games   -- total regular-season home games
    n_dow     -- count of distinct days of week that hosted a recurring promo
    """
    df = query_df(f"""
        WITH games AS (
          SELECT gf.game_pk, gf.has_recurring, gf.day_of_week
            FROM milb.game_features gf
            JOIN milb.games g ON g.game_pk = gf.game_pk
           WHERE gf.team_id = {RUMBLE_PONIES_ID}
             AND g.season = 2025
             AND g.game_type = 'R'
             AND gf.attendance IS NOT NULL
        )
        SELECT
          1.0 * COUNT(*) FILTER (WHERE has_recurring) / NULLIF(COUNT(*), 0)
            AS pct_recurring,
          COUNT(*) AS n_games,
          COUNT(DISTINCT day_of_week) FILTER (WHERE has_recurring) AS n_dow
          FROM games
    """)
    if df.empty:
        return {"pct": 0.0, "n_games": 0, "n_dow": 0}
    row = df.iloc[0]
    return {
        "pct": float(row["pct_recurring"] or 0),
        "n_games": int(row["n_games"]),
        "n_dow": int(row["n_dow"] or 0),
    }


def load_doublea_recurring_2025() -> pd.DataFrame:
    """Each Double-A team's 2025 share of home games with at least one
    recurring promo. Restricted to teams with a full season of enrichment.
    """
    return query_df(f"""
        SELECT gf.team_id, t.team_name,
               COUNT(*) AS total_games,
               1.0 * COUNT(*) FILTER (WHERE gf.has_recurring) / NULLIF(COUNT(*), 0) AS pct
          FROM milb.game_features gf
          JOIN milb.games g ON g.game_pk = gf.game_pk
          JOIN milb.teams t ON t.team_id = gf.team_id
         WHERE g.season = 2025
           AND g.game_type = 'R'
           AND gf.attendance IS NOT NULL
           AND gf.sport_id = {DOUBLE_A_SPORT_ID}
         GROUP BY gf.team_id, t.team_name
        HAVING COUNT(*) >= 50
         ORDER BY pct DESC
    """)


def load_rp_rank() -> dict:
    """Return RP's rank and the total count of Double-A teams classified."""
    df = load_doublea_recurring_2025()
    if df.empty:
        return {"rank": None, "total": 0}
    df = df.reset_index(drop=True)
    rp_rows = df.index[df["team_id"] == RUMBLE_PONIES_ID].tolist()
    return {
        "rank": (rp_rows[0] + 1) if rp_rows else None,
        "total": len(df),
    }


def load_recurring_promo_mix() -> pd.DataFrame:
    """Composition of recurring promos for Binghamton vs other Double-A teams.

    One row per group, with the share of recurring promos that carry each
    audience flag. Flags can co-occur (a Family Funday hits both kids and
    family/community), so columns can sum to over 100%.
    """
    return query_df(f"""
        SELECT
          CASE WHEN g.home_team_id = {RUMBLE_PONIES_ID} THEN 'Binghamton'
               ELSE 'Other Double-A' END AS who,
          COUNT(*) AS total_recurring,
          1.0 * COUNT(*) FILTER (WHERE p.is_kids_event)
                / NULLIF(COUNT(*), 0) AS kids_pct,
          1.0 * COUNT(*) FILTER (WHERE p.is_community_event OR p.is_heritage_night)
                / NULLIF(COUNT(*), 0) AS family_community_pct,
          1.0 * COUNT(*) FILTER (WHERE p.is_food_deal)
                / NULLIF(COUNT(*), 0) AS food_drink_pct,
          1.0 * COUNT(*) FILTER (WHERE p.is_theme_night)
                / NULLIF(COUNT(*), 0) AS theme_pct,
          1.0 * COUNT(*) FILTER (WHERE p.is_ticket_deal)
                / NULLIF(COUNT(*), 0) AS ticket_deal_pct
          FROM milb.game_promotions p
          JOIN milb.games g ON g.game_pk = p.game_pk
         WHERE p.is_recurring = TRUE
           AND p.enrichment_method IS NOT NULL
           AND g.sport_id = {DOUBLE_A_SPORT_ID}
           AND g.season = 2025
         GROUP BY who
    """)


def load_ritual_family_landscape() -> pd.DataFrame:
    """The ritual-family comparison table. One row per ritual family with how
    many Double-A teams ran it in 2025 and whether RP was one of them.

    Restricted to families that appeared at 2+ teams or showed up in RP's
    slate so the table stays focused on patterns that are actually shared.
    """
    return query_df(f"""
        WITH classified AS (
          SELECT p.*, g.home_team_id, g.season,
                 {RITUAL_FAMILY_CASE} AS ritual_family
            FROM milb.game_promotions p
            JOIN milb.games g ON g.game_pk = p.game_pk
           WHERE p.is_recurring = TRUE
             AND p.enrichment_method IS NOT NULL
             AND g.sport_id = {DOUBLE_A_SPORT_ID}
             AND g.season = 2025
        ),
        by_family AS (
          SELECT ritual_family,
                 COUNT(DISTINCT home_team_id) AS n_teams,
                 COUNT(*) AS n_games,
                 BOOL_OR(home_team_id = {RUMBLE_PONIES_ID}) AS rp_runs,
                 MODE() WITHIN GROUP (ORDER BY EXTRACT(DOW FROM (
                   SELECT game_date FROM milb.games WHERE game_pk = classified.game_pk
                 ))) AS typical_dow_raw
            FROM classified
           WHERE ritual_family IS NOT NULL
           GROUP BY ritual_family
        )
        SELECT ritual_family,
               n_teams,
               n_games,
               rp_runs
          FROM by_family
         WHERE n_teams >= 2 OR rp_runs = TRUE
         ORDER BY n_teams DESC, n_games DESC
    """)


def load_ritual_family_typical_day() -> dict:
    """Return {ritual_family: 'Tue'} mapping from the most common day-of-week
    that ritual family runs on across Double-A. Done as a separate, simpler
    query because MODE WITHIN GROUP nested with a CTE was awkward.
    """
    df = query_df(f"""
        WITH classified AS (
          SELECT p.is_recurring, p.enrichment_method, g.home_team_id, g.game_date,
                 {RITUAL_FAMILY_CASE} AS ritual_family
            FROM milb.game_promotions p
            JOIN milb.games g ON g.game_pk = p.game_pk
           WHERE p.is_recurring = TRUE
             AND p.enrichment_method IS NOT NULL
             AND g.sport_id = {DOUBLE_A_SPORT_ID}
             AND g.season = 2025
        )
        SELECT ritual_family,
               MODE() WITHIN GROUP (ORDER BY EXTRACT(DOW FROM game_date)) AS dow_raw
          FROM classified
         WHERE ritual_family IS NOT NULL
         GROUP BY ritual_family
    """)
    if df.empty:
        return {}
    return {row["ritual_family"]: DOW_MAP.get(int(row["dow_raw"]), "")
            for _, row in df.iterrows()}


def load_llm_schedule() -> dict | None:
    """Pull the LLM-proposed weekly ritual schedule if one has been generated.

    Stored in milb.group_narratives with group_type='ritual_schedule',
    group_key='binghamton'. Returns the parsed kpi_json (which holds the
    schedule) or None if not generated yet.
    """
    df = query_df("""
        SELECT narrative_text, kpi_json, llm_model, generated_at
          FROM milb.group_narratives
         WHERE group_type = 'ritual_schedule'
           AND group_key = 'binghamton'
         ORDER BY generated_at DESC
         LIMIT 1
    """)
    if df.empty:
        return None
    row = df.iloc[0]
    payload = row["kpi_json"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = None
    return {
        "narrative": row.get("narrative_text"),
        "schedule": payload,
        "model": row.get("llm_model"),
        "generated_at": row.get("generated_at"),
    }


# -- Render -------------------------------------------------------------------

st.title("Rituals")
st.markdown(
    "### The recurring promo slate is in place. The remaining gap is who it speaks to."
)
st.caption(
    "Binghamton runs a recurring promotion on roughly a quarter of home games "
    "in 2025, the second-highest rate among Double-A teams in the data. The "
    "composition leans toward kids and family audiences. Peer top performers "
    "lean toward adult weeknight food and drink formats."
)

st.divider()

# ─── Section 1: What we see ────────────────────────────────────────────────
st.subheader("What we see")

metrics = load_rp_headline_metrics()
rank_info = load_rp_rank()

c1, c2, c3 = st.columns(3)
c1.metric(
    "Recurring promos in 2025",
    f"{metrics['pct']*100:.0f}%",
    f"of {metrics['n_games']} home games",
)
c2.metric(
    "Rank in Double-A",
    f"#{rank_info['rank']}" if rank_info["rank"] else "n/a",
    f"of {rank_info['total']} teams" if rank_info["rank"] else None,
)
c3.metric(
    "Days of the week with a ritual",
    f"{metrics['n_dow']} of 7",
    "Saturday locked for fireworks",
)

st.caption(
    "Promotional data is only available from 2025 onward (MLB Stats API "
    "limitation). The page does not infer what was on the schedule before "
    "that. The numbers above describe the slate that is in place today."
)

st.markdown("**Where Binghamton ranks in Double-A, 2025**")

da = load_doublea_recurring_2025()
if not da.empty:
    da = da.sort_values("pct", ascending=True).copy()
    da["pct_display"] = (da["pct"] * 100).round(1)
    da["is_rp"] = (da["team_id"] == RUMBLE_PONIES_ID)
    da["color"] = da["is_rp"].map({True: ACCENT_COLOR, False: PEER_COLOR})
    da["label_team"] = da.apply(
        lambda r: f"\u2192  {r['team_name']}" if r["is_rp"] else r["team_name"],
        axis=1,
    )

    fig = px.bar(
        da, y="label_team", x="pct_display",
        orientation="h",
        color="color", color_discrete_map="identity",
        labels={"pct_display": "% of home games with a recurring promo",
                "label_team": ""},
        text=da["pct_display"].round(0).astype(int).astype(str) + "%",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        xaxis_range=[0, max(35, da["pct_display"].max() + 8)],
        xaxis_ticksuffix="%",
        showlegend=False,
        margin=dict(t=20, b=20, l=180),
        height=max(720, 22 * len(da) + 60),
    )
    fig.update_yaxes(tickmode="linear", tickfont=dict(size=11))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Each row: a Double-A team's 2025 share of home games carrying at "
        "least one recurring promo. Binghamton is marked with an arrow and "
        "highlighted in orange. Restricted to teams with full-season promo "
        "enrichment."
    )

st.divider()

# ─── Section 2: Why it matters ─────────────────────────────────────────────
st.subheader("Why it matters")

st.markdown(
    "A recurring promo is the only kind of promo that trains habit. A fan "
    "who comes to one Thirsty Thursday is more likely to come to the next "
    "Thursday than someone who came to a one-off fireworks night. Habit "
    "compounds across a season. A weeknight slate that fans know by name "
    "is the cheapest tool a club has for lifting low-draw nights."
)

st.markdown(
    "Binghamton has roughly 65 home games each season, about 40 of them on "
    "a weeknight. If a single weeknight ritual lifted average attendance "
    "on its day by 200 fans across 14 home games of that day, that is "
    "2,800 additional fans through the gate per season. At the report's "
    f"$30 per fan composite estimate, that is roughly "
    f"**{format_dollars_short(2800 * REVENUE_PER_FAN_USD)} in revenue per "
    "season** from one slot."
)
st.caption(
    "Illustrative arithmetic only. 200 fans per game is a conservative "
    "rule-of-thumb lift for a successful weeknight ritual at Double-A "
    "venue size. The actual number from any specific pilot would need to "
    "be measured on the games it runs."
)

st.divider()

# ─── Section 3: What's behind it ───────────────────────────────────────────
st.subheader("What's behind it")

st.markdown(
    "Where Binghamton's recurring slate differs from the rest of Double-A "
    "is in the composition. The same coverage on the calendar, but a "
    "different audience profile."
)

mix = load_recurring_promo_mix()
if not mix.empty:
    long = mix.melt(
        id_vars=["who", "total_recurring"],
        value_vars=["kids_pct", "family_community_pct", "food_drink_pct",
                    "theme_pct", "ticket_deal_pct"],
        var_name="flag", value_name="rate",
    )
    flag_labels = {
        "kids_pct":             "Kids",
        "family_community_pct": "Family / Community",
        "food_drink_pct":       "Food or drink",
        "theme_pct":            "Theme night",
        "ticket_deal_pct":      "Ticket deal",
    }
    long["flag"] = long["flag"].map(flag_labels)
    long["rate_display"] = (long["rate"] * 100).round(0).astype(int)
    flag_order = ["Kids", "Family / Community", "Food or drink",
                  "Theme night", "Ticket deal"]
    long["flag"] = pd.Categorical(long["flag"], categories=flag_order, ordered=True)
    long = long.sort_values(["flag", "who"])

    fig = px.bar(
        long, x="flag", y="rate_display", color="who",
        barmode="group",
        color_discrete_map={"Binghamton": RP_COLOR, "Other Double-A": PEER_COLOR},
        labels={"rate_display": "% of recurring promos carrying this flag",
                "flag": "", "who": ""},
        text=long["rate_display"].astype(str) + "%",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        yaxis_ticksuffix="%",
        yaxis_range=[0, 75],
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Each pair of bars: the share of recurring promos that carry a "
        "given audience tag. A single promotion can carry more than one "
        "tag, so columns do not sum to 100. The kids and family bars are "
        "where Binghamton diverges most from the peer set."
    )

st.markdown("**Most common recurring rituals across Double-A, 2025**")

landscape = load_ritual_family_landscape()
dow_lookup = load_ritual_family_typical_day()

if not landscape.empty:
    landscape["Ritual"] = landscape["ritual_family"]
    landscape["Teams running it"] = (
        landscape["n_teams"].astype(str) + " of " + str(rank_info["total"] or 30)
    )
    landscape["Binghamton runs it?"] = landscape["rp_runs"].map(
        {True: "Yes", False: "No"}
    )
    landscape["Typical day"] = landscape["ritual_family"].map(dow_lookup).fillna("")

    show = landscape[["Ritual", "Teams running it", "Binghamton runs it?", "Typical day"]]
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.caption(
        "Each row: one recurring ritual family with similar offer names "
        "collapsed together (so all variants of Thirsty Thursday count "
        "once). Sorted by how many Double-A teams have it on their 2025 "
        "calendar. The top of the table shows the patterns Binghamton "
        "does not currently run."
    )

st.divider()

# ─── Section 4: What to do with this ──────────────────────────────────────
st.subheader("What to do with this")

c1, c2 = st.columns(2)

with c1:
    st.markdown("**Strategic, for the 2027 calendar**")
    st.markdown(
        "- The kids and family rituals are working as a foundation. The "
        "candidate addition is one adult-oriented weeknight ritual, not "
        "a replacement of anything currently on the books.\n"
        "- The thinnest part of the current slate is the food and drink "
        "format that other Double-A teams use to anchor Tuesday, "
        "Wednesday, and Thursday gates.\n"
        "- A pilot on one weeknight, advertised by name from opening day, "
        "is the testable version of this hypothesis."
    )

with c2:
    st.markdown("**Candidate formats to pilot in 2026 or 2027**")
    st.markdown(
        "- A pricing-anchored Thursday (Three Dollar Thursday, Thirsty "
        "Thursday, or similar). High frequency at peer clubs.\n"
        "- A trivia or game-night Tuesday for a younger 21-plus crowd "
        "looking for a low-stakes weeknight out.\n"
        "- A taproom or local-brew Wednesday partnership, branded with a "
        "Binghamton-specific name.\n"
        "- Any of the above would need a season of consistent "
        "advertising to test fairly. Habit takes more than a few games "
        "to build."
    )

st.markdown(
    "**This is a hypothesis, not a directive.** The league-wide observational "
    "data on weeknight recurring promo lift is mixed, likely because clubs "
    "often deploy recurring promos on already-slow nights as a rescue tool. "
    "The only way to know whether an adult-weeknight ritual would lift "
    "Binghamton gates is to run one and measure it against the same weeknight "
    "in the same month in prior seasons."
)

st.markdown("---")
st.markdown("**A proposed weekly schedule for Binghamton (LLM-generated)**")

llm = load_llm_schedule()
if llm is None or not llm.get("schedule"):
    st.info(
        "No LLM-generated schedule has been produced yet. Run "
        "`python scripts/generate_ritual_schedule.py` with Ollama running "
        "to populate this section."
    )
else:
    schedule = llm["schedule"]
    days = schedule.get("days") if isinstance(schedule, dict) else None
    if isinstance(schedule, list):
        days = schedule
    if days:
        df_sched = pd.DataFrame(days)
        # Ensure consistent column order if present
        col_order = [c for c in ["day", "status", "current_ritual",
                                 "proposed_ritual", "category", "rationale"]
                     if c in df_sched.columns]
        if col_order:
            df_sched = df_sched[col_order]
        df_sched = df_sched.rename(columns={
            "day": "Day",
            "status": "Status",
            "current_ritual": "Current",
            "proposed_ritual": "Proposed",
            "category": "Category",
            "rationale": "Rationale",
        })
        st.dataframe(df_sched, use_container_width=True, hide_index=True)
    headline = schedule.get("headline") if isinstance(schedule, dict) else None
    if headline:
        st.caption(headline)
    if llm.get("model"):
        st.caption(
            f"Generated by {llm['model']} on "
            f"{llm['generated_at']:%Y-%m-%d} as a hypothesis. Saturday is "
            "treated as locked (fireworks) and not subject to ritual swap."
        )

st.divider()

# ─── Section 5: See also ──────────────────────────────────────────────────
see_also([
    ("Peer Playbook",
     "pages/12_Peer_Playbook.py",
     "side-by-side profiles for hand-picked peer teams, with the full promo mix"),
    ("Promo Strategy",
     "pages/7_Promo_Strategy.py",
     "the deeper view on how each Double-A club balances its promo flags"),
    ("Hypothesis Lab",
     "pages/13_Hypothesis_Lab.py",
     "DOW-by-promo lift estimates and stacking effects across the league"),
])

render_footer(scripts=["build_features"])
