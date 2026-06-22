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
ACCENT_COLOR = "#d4572e"    # warm orange, used for RP highlight on the rank chart

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


# SQL CASE that mirrors deal_subtype() in scripts/generate_ritual_schedule.py.
# Splits the legacy is_food_deal flag into 'alcohol' vs 'food' using offer-
# name keywords. Used by load_winning_teams_week() to assign dominant
# category per (team, day).
DEAL_TYPE_SQL = """
  CASE
    WHEN offer_name ILIKE '%thirsty%' OR offer_name ILIKE '%beer%'
      OR offer_name ILIKE '%wine%' OR offer_name ILIKE '%vodka%'
      OR offer_name ILIKE '%whiskey%' OR offer_name ILIKE '%margarita%'
      OR offer_name ILIKE '%mimosa%' OR offer_name ILIKE '%tito%'
      OR offer_name ILIKE '%koozie%' OR offer_name ILIKE '%white claw%'
      OR offer_name ILIKE '%landon winery%' OR offer_name ILIKE '%pbr%'
      OR offer_name ILIKE '%yuengling%' OR offer_name ILIKE '%happy hour%'
      OR offer_name ILIKE '%tall boys%' OR offer_name ILIKE '%bullpen party%'
      OR offer_name ILIKE '%weenie%whiskey%'
      OR offer_name ILIKE '%$3 thursday%'
      OR offer_name ILIKE '%three dollar thursday%'
      OR offer_name ILIKE '%twofer%' OR offer_name ILIKE '%two-for%'
      OR offer_name ILIKE '%two for tuesday%'
    THEN 'alcohol'
    WHEN offer_name ILIKE '%taco%' OR offer_name ILIKE '%hot dog%'
      OR offer_name ILIKE '%burger%' OR offer_name ILIKE '%cheeseburger%'
      OR offer_name ILIKE '%pizza%' OR offer_name ILIKE '%fryday%'
      OR offer_name ILIKE '%bell value%' OR offer_name ILIKE '%lemon chill%'
      OR offer_name ILIKE '%chewsday%'
    THEN 'food'
    ELSE NULL
  END
"""


def load_winning_team_sat_fri_camps() -> dict[int, dict]:
    """Classify each of the 12 winning teams as sat_winner / sat_loser /
    neutral based on the 2025 Sat-vs-Fri attendance gap (July 3-5 excluded
    to match the Saturdays page convention).

    Returns {team_id: {camp, gap_pct, fri_avg, sat_avg}}.
    """
    df = query_df(f"""
        WITH season_avg AS (
          SELECT team_id, sport_id, AVG(capacity_utilization) AS cap_util
            FROM milb.game_features
           WHERE season = 2025 AND game_type = 'R' AND attendance IS NOT NULL
           GROUP BY team_id, sport_id HAVING COUNT(*) >= 50
        ),
        winners AS (
          SELECT t.team_id
            FROM season_avg sa JOIN milb.teams t ON t.team_id = sa.team_id
           WHERE sa.cap_util >= 0.65 AND sa.sport_id IN (11, 12, 13)
           ORDER BY sa.cap_util DESC LIMIT 12
        )
        SELECT gf.team_id, gf.day_of_week, AVG(gf.attendance) AS avg_att
          FROM milb.game_features gf
          JOIN winners w ON w.team_id = gf.team_id
         WHERE gf.season = 2025 AND gf.game_type = 'R'
           AND gf.attendance IS NOT NULL
           AND gf.day_of_week IN (4, 5)
           AND NOT (EXTRACT(MONTH FROM gf.game_date) = 7
                    AND EXTRACT(DAY FROM gf.game_date) IN (3, 4, 5))
         GROUP BY gf.team_id, gf.day_of_week
    """)
    if df.empty:
        return {}
    by_team: dict[int, dict] = {}
    for tid, sub in df.groupby("team_id"):
        rows = {int(r["day_of_week"]): float(r["avg_att"]) for _, r in sub.iterrows()}
        fri = rows.get(4)
        sat = rows.get(5)
        if fri and sat:
            gap = (sat - fri) / ((fri + sat) / 2) * 100
            if gap >= 5:
                camp = "sat_winner"
            elif gap <= -5:
                camp = "sat_loser"
            else:
                camp = "neutral"
            by_team[int(tid)] = {
                "camp":     camp,
                "gap_pct":  round(gap, 1),
                "fri_avg":  int(fri),
                "sat_avg":  int(sat),
            }
    return by_team


def load_winning_teams_week() -> pd.DataFrame:
    """The 12 top-cap-util teams (AAA/AA/A+, cap_util >= 0.65) and their
    dominant ritual category per day of week in 2025.

    Returns a wide-format DataFrame with columns:
        Team | Level | Cap util | Sat vs Fri | Mon | Tue | Wed | Thu | Fri | Sat | Sun
    Where each day cell shows the dominant category at >= 50% of games,
    or "-" if no single category dominates. "Sat vs Fri" is the percent
    gap between Saturday and Friday average attendance (positive means
    Sat outdraws Fri, the sat_winner pattern Pensacola has cracked).
    """
    df = query_df(f"""
        WITH season_avg AS (
          SELECT team_id, sport_id, AVG(capacity_utilization) AS cap_util
            FROM milb.game_features
           WHERE season = 2025 AND game_type = 'R'
             AND attendance IS NOT NULL
           GROUP BY team_id, sport_id HAVING COUNT(*) >= 50
        ),
        winners AS (
          SELECT t.team_id, t.team_name, t.sport_id, sa.cap_util
            FROM season_avg sa
            JOIN milb.teams t ON t.team_id = sa.team_id
           WHERE sa.cap_util >= 0.65 AND sa.sport_id IN (11, 12, 13)
           ORDER BY sa.cap_util DESC
           LIMIT 12
        ),
        per_day AS (
          SELECT gf.team_id, gf.day_of_week,
                 COUNT(*) AS n,
                 AVG(CASE WHEN gf.has_fireworks   THEN 1.0 ELSE 0 END) AS fw,
                 AVG(CASE WHEN gf.has_giveaway    THEN 1.0 ELSE 0 END) AS gv,
                 AVG(CASE WHEN gf.has_kids_event  THEN 1.0 ELSE 0 END) AS kids,
                 AVG(CASE WHEN gf.has_community   THEN 1.0 ELSE 0 END) AS comm,
                 AVG(CASE WHEN gf.has_theme_night THEN 1.0 ELSE 0 END) AS theme
            FROM milb.game_features gf
            JOIN winners w ON w.team_id = gf.team_id
           WHERE gf.season = 2025 AND gf.game_type = 'R'
             AND gf.attendance IS NOT NULL
           GROUP BY gf.team_id, gf.day_of_week
        ),
        deal_per_day AS (
          SELECT g.home_team_id AS team_id, gf.day_of_week,
                 {DEAL_TYPE_SQL} AS subtype,
                 COUNT(*) AS n
            FROM milb.game_promotions p
            JOIN milb.games g ON g.game_pk = p.game_pk
            JOIN milb.game_features gf ON gf.game_pk = g.game_pk
            JOIN winners w ON w.team_id = g.home_team_id
           WHERE p.is_recurring = TRUE
             AND p.enrichment_method IS NOT NULL
             AND g.season = 2025
           GROUP BY g.home_team_id, gf.day_of_week, {DEAL_TYPE_SQL}
        ),
        deal_shares AS (
          SELECT team_id, day_of_week,
                 SUM(CASE WHEN subtype = 'alcohol' THEN n ELSE 0 END) AS alc_n,
                 SUM(CASE WHEN subtype = 'food'    THEN n ELSE 0 END) AS food_n
            FROM deal_per_day GROUP BY team_id, day_of_week
        )
        SELECT w.team_id, w.team_name, w.sport_id, w.cap_util,
               pd.day_of_week, pd.n,
               pd.fw, pd.gv, pd.kids, pd.comm, pd.theme,
               COALESCE(ds.alc_n, 0) * 1.0 / NULLIF(pd.n, 0)  AS alcohol,
               COALESCE(ds.food_n, 0) * 1.0 / NULLIF(pd.n, 0) AS food
          FROM winners w
          JOIN per_day pd ON pd.team_id = w.team_id
          LEFT JOIN deal_shares ds
                 ON ds.team_id = pd.team_id
                AND ds.day_of_week = pd.day_of_week
         ORDER BY w.cap_util DESC, pd.day_of_week
    """)
    if df.empty:
        return df

    def dominant(row) -> str:
        candidates = [
            ("Fireworks", float(row.get("fw") or 0)),
            ("Giveaway",  float(row.get("gv") or 0)),
            ("Alcohol",   float(row.get("alcohol") or 0)),
            ("Food",      float(row.get("food") or 0)),
            ("Kids",      float(row.get("kids") or 0)),
            ("Community", float(row.get("comm") or 0)),
            ("Theme",     float(row.get("theme") or 0)),
        ]
        candidates.sort(key=lambda c: c[1], reverse=True)
        top_name, top_share = candidates[0]
        return top_name if top_share >= 0.5 else "-"

    df["dom"] = df.apply(dominant, axis=1)
    df["dow_label"] = df["day_of_week"].astype(int).map(
        {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri",5:"Sat",6:"Sun"})

    # Pivot to wide
    wide = df.pivot_table(
        index=["team_id", "team_name", "sport_id", "cap_util"],
        columns="dow_label", values="dom", aggfunc="first",
    ).reset_index()
    sport_label = {11:"AAA", 12:"AA", 13:"A+"}
    wide["Level"] = wide["sport_id"].map(sport_label)
    wide["Cap util"] = (wide["cap_util"] * 100).round(0).astype(int).astype(str) + "%"
    wide = wide.rename(columns={"team_name": "Team"})

    # Annotate with Sat-vs-Fri pattern so the reader can see most winning
    # teams are NOT sat_winners and the swap is specifically about closing
    # a sat_loser gap, which Binghamton has.
    camps = load_winning_team_sat_fri_camps()
    def sat_label(tid: int) -> str:
        info = camps.get(int(tid))
        if not info:
            return "-"
        sign = "+" if info["gap_pct"] > 0 else ""
        return f"{sign}{info['gap_pct']:.0f}% ({info['camp'].replace('_', '-')})"
    wide["Sat vs Fri"] = wide["team_id"].apply(sat_label)

    day_cols = [d for d in ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"] if d in wide.columns]
    for d in day_cols:
        wide[d] = wide[d].fillna("-")
    final = wide[["Team", "Level", "Cap util", "Sat vs Fri"] + day_cols].sort_values(
        "Cap util", key=lambda s: s.str.rstrip("%").astype(int), ascending=False
    )
    return final


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
    "in 2025. That is ahead of well-known clubs like the Portland Sea Dogs and "
    "the Erie SeaWolves, but well below the league leaders who run rituals on "
    "more than half their schedule. The composition leans toward kids and "
    "family audiences. Peer top performers lean toward adult weeknight food "
    "and drink formats."
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
    f"#{rank_info['rank']} of {rank_info['total']}" if rank_info["rank"] else "n/a",
    "ahead of Portland, Erie, Reading" if rank_info["rank"] else None,
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

st.markdown("**How 12 high-attendance teams structure their week, 2025**")

winners_week = load_winning_teams_week()
if not winners_week.empty:
    st.dataframe(winners_week, use_container_width=True, hide_index=True)
    st.caption(
        "Each row is one team in the top of the league on 2025 capacity "
        "utilization (cap_util at or above 65%, Triple-A / Double-A / "
        "High-A only). Each day-of-week cell shows the dominant promo "
        "category at 50% or more of that team's home games on that day. "
        "Alcohol is split from food using offer-name keywords so a "
        "Thirsty Thursday counts as alcohol and a Taco Tuesday counts as "
        "food. A dash means no single category covers a majority of that "
        "day for that team."
    )

    st.markdown(
        "**A note on fireworks placement before reading further.** Most "
        "teams in this table run fireworks on Friday rather than Saturday, "
        "which looks at first glance like it contradicts the Saturdays "
        "finding. The reconciliation matters:"
    )
    st.markdown(
        "- High capacity utilization and the sat-winner pattern are "
        "**different things**. Of the 12 teams above, only 2 (Pensacola "
        "+6%, Portland +12%) draw better on Saturday than Friday. Six are "
        "neutral. Four are themselves sat-losers (South Bend, Greenville, "
        "Corpus Christi, Tulsa). Those four fill seats overall but still "
        "leave the Saturday upside on the table.\n"
        "- The Saturdays finding's recommendation applies specifically to "
        "teams with the sat-loser pattern. Binghamton is in that group. "
        "South Bend, Greenville, and Corpus Christi are too. None of them "
        "has tried the swap.\n"
        "- The teams that match Binghamton's profile AND have flipped to "
        "sat-winner are rare. Pensacola is the cleanest one: Double-A, "
        "similar climate, similar capacity utilization, and the exact "
        "swap pattern (Giveaway Fri 100%, Fireworks Sat 100%)."
    )

    st.info(
        "**The case study:** Pensacola Blue Wahoos is the closest direct "
        "comp for Binghamton in this table. AA, 78% capacity utilization, "
        "and the swap pattern in operation. The Saturdays-finding "
        "recommendation is what Pensacola is already doing."
    )

    st.markdown(
        "**Reading the table for the alcohol-night call:** Of the 12 teams "
        "above, eight anchor Thursday with an alcohol ritual (Thirsty "
        "Thursday, $3 Thursday, Three Dollar Thursday, Dollar Drink Night, "
        "Twisted Thursday, Tito's). Tuesday alcohol anchors show up at "
        "only two teams in the wider sample. Two distinct alcohol nights "
        "per week show up at exactly two of 25 high-attendance teams "
        "league-wide. The honest read is: at most one alcohol night, and "
        "Thursday is where it belongs."
    )

st.divider()

# ─── Section 4: What to do with this ──────────────────────────────────────
st.subheader("What to do with this")

st.markdown(
    "**About Tuesday before the recommendations.** Tuesday has been "
    "Binghamton's worst day of the week in 2025 at roughly 24% capacity "
    "utilization, and the trend across recent seasons has been downward, "
    "not flat. The current Twofer Tuesday ritual has been on the calendar "
    "for some time and has not stopped the decline. The implication is "
    "that no single ritual change is likely to reverse Tuesday on its "
    "own. Tuesday belongs in a longer-horizon conversation about pricing, "
    "marketing, and whether the day-of-week has structural demand at "
    "this market size. The schedule below treats the Tuesday format as "
    "one of several pilots, not as the lever that fixes Tuesday."
)

c1, c2 = st.columns(2)

with c1:
    st.markdown("**Strategic, for the 2027 calendar**")
    st.markdown(
        "- The Saturdays-finding swap (Fireworks to Saturday, Giveaway to "
        "Friday) is the single most evidence-backed change. A direct "
        "Double-A peer (Pensacola) already runs it.\n"
        "- The Thursday alcohol anchor is the second most evidence-backed "
        "change. Eight of the top 12 winning teams already run one.\n"
        "- The kids and family rituals on Sunday are working and should "
        "stay. Wednesday community / heritage matches the peer pattern."
    )

with c2:
    st.markdown("**Candidate formats to pilot**")
    st.markdown(
        "- **Thursday (alcohol pilot, highest evidence):** Thirsty "
        "Thursday, Three Dollar Thursday, or a Binghamton-branded "
        "equivalent. Eight winning teams already do this.\n"
        "- **Tuesday (lower-evidence pilot):** Shift Tuesday away from "
        "alcohol toward food or community. Taco Tuesday, Doggone Tuesday, "
        "or a community-anchored Tuesday. Not expected to fix the "
        "Tuesday decline on its own, but the current format is non-"
        "standard relative to winning teams.\n"
        "- One alcohol night per week, not two. Of 25 winning teams, "
        "only two run two distinct alcohol nights. The default is "
        "Thursday alcohol; Tuesday food."
    )

st.markdown(
    "**This is a hypothesis-driven schedule, not a directive.** The page "
    "shows what successful peer teams structure their week to look like "
    "and where Binghamton currently diverges from that pattern. It does "
    "not prove that adopting these formats causes attendance to rise. "
    "The Saturdays finding is the most direct evidence-based call. The "
    "weeknight pilots are candidate tests, and the only way to know "
    "whether any of them moves Binghamton's gates is to run them and "
    "measure them against the same day in the same month in prior "
    "seasons."
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
            f"{llm['generated_at']:%Y-%m-%d} as a hypothesis. "
            "Status legend: off = no home games scheduled, keep = current "
            "placement matches what successful peers do, change = current "
            "placement diverges from the peer pattern, test = day has no "
            "anchor format and a pilot is proposed, skip = day should "
            "remain unbranded."
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
