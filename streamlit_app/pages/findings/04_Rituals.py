"""Finding 4 -- Rituals.

The recurring-promo finding.

What the data shows (2025 Double-A, 30 teams, 4,000+ home games):
  1. Binghamton built a recurring-promo slate from scratch over two seasons.
     0% of home games carried a recurring promo in 2023 and 2024;
     25% did in 2025 and 31% so far in 2026. That is the second-highest rate
     in the Double-A teams with comparable promo enrichment.
  2. The slate Binghamton built leans toward kids and family audiences.
     Twofer Tuesday, Throwback Thursday, We Care Wednesdays, Family Funday,
     Kids' Club Sundays, Senior Sundays. Across all recurring promos
     league-wide, 37% of Binghamton's carry a kids flag and 11% carry a
     family/community flag. The Double-A peer average is 5% and 4%.
  3. Peer Double-A recurring slates lean toward adult food and drink
     formats. Thirsty Thursday, Wine Wednesday, Taco Tuesday, Three Dollar
     Thursday, Trivia Tuesday. 63% of peer recurring promos carry a food
     or drink flag vs 53% of Binghamton's.

What that means for the page: this finding does not say Binghamton is doing
the wrong thing. It says Binghamton has done one thing well and has not yet
tried the other thing peers are doing. The recommendation is a pilot test,
not a calendar overhaul, and the page is honest that league-wide observational
data on weeknight recurring lift is mixed (likely selection bias from rescue-
promo deployment patterns).

No em dashes in this file -- the briefing book copy pass will polish all
prose simultaneously in a follow-up.

Structure (same arc as findings 1-3):
  1. Headline + caption
  2. What we see (two charts: rate over time, peer ranking)
  3. Why it matters (habit framing, weeknight opportunity)
  4. What's behind it (composition mix vs peers, named rituals)
  5. What to do with this (pilot hypothesis, candidate formats)
  6. See also
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
from utils.economics import REVENUE_PER_FAN_USD, format_dollars_short
from utils.footer import render_footer
from utils.navigation import see_also

st.set_page_config(page_title="Rituals", layout="wide")

RUMBLE_PONIES_ID = 505
DOUBLE_A_SPORT_ID = 12

RP_COLOR = "#b064a0"        # purple, matches RP across the briefing book
PEER_COLOR = "#95a5a6"      # neutral grey for peer aggregates
LEAGUE_COLOR = "#3a9bd5"    # blue accent for Double-A reference

DOW_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DOW_MAP = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}


# -- Data loaders -------------------------------------------------------------

def load_rp_recurring_by_season() -> pd.DataFrame:
    """RP's share of home games with at least one recurring promo, by season.

    Promo enrichment is only fully populated from 2025 onward, but the 2023
    and 2024 zero values are real (those seasons had no recurring promos on
    the offer schedule, not a data gap).
    """
    return query_df(f"""
        SELECT g.season,
               COUNT(*) FILTER (WHERE gf.has_recurring) AS games_with_recurring,
               COUNT(*) AS total_games,
               1.0 * COUNT(*) FILTER (WHERE gf.has_recurring) / NULLIF(COUNT(*), 0) AS pct
          FROM milb.game_features gf
          JOIN milb.games g ON g.game_pk = gf.game_pk
         WHERE gf.team_id = {RUMBLE_PONIES_ID}
           AND g.game_type = 'R'
           AND gf.attendance IS NOT NULL
         GROUP BY g.season
         ORDER BY g.season
    """)


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


def load_rp_named_rituals() -> pd.DataFrame:
    """The actual recurring promo names Binghamton has run in 2025 and 2026,
    plus the day-of-week they typically land on.
    """
    return query_df(f"""
        SELECT p.offer_name,
               COUNT(*) AS n_games,
               MIN(g.season) AS first_season,
               MODE() WITHIN GROUP (ORDER BY EXTRACT(DOW FROM g.game_date)) AS typical_dow
          FROM milb.game_promotions p
          JOIN milb.games g ON g.game_pk = p.game_pk
         WHERE p.is_recurring = TRUE
           AND p.enrichment_method IS NOT NULL
           AND g.home_team_id = {RUMBLE_PONIES_ID}
           AND g.season IN (2025, 2026)
         GROUP BY p.offer_name
        HAVING COUNT(*) >= 2
         ORDER BY n_games DESC
    """)


def load_peer_named_rituals() -> pd.DataFrame:
    """Most-run recurring promos across Double-A peers (excluding Binghamton),
    2025 only, where the same offer name ran at least 5 times for one team.
    Used to anchor the "what peers run" section with concrete names.
    """
    return query_df(f"""
        SELECT p.offer_name, t.team_name,
               COUNT(*) AS n_games
          FROM milb.game_promotions p
          JOIN milb.games g ON g.game_pk = p.game_pk
          JOIN milb.teams t ON t.team_id = g.home_team_id
         WHERE p.is_recurring = TRUE
           AND p.enrichment_method IS NOT NULL
           AND g.sport_id = {DOUBLE_A_SPORT_ID}
           AND g.season = 2025
           AND g.home_team_id != {RUMBLE_PONIES_ID}
           AND (p.is_food_deal OR p.is_theme_night OR p.is_ticket_deal)
           AND NOT p.is_kids_event
         GROUP BY p.offer_name, t.team_name
        HAVING COUNT(*) >= 5
         ORDER BY n_games DESC
         LIMIT 12
    """)


# -- Render -------------------------------------------------------------------

st.title("Rituals")
st.markdown(
    "### The recurring promo slate has been rebuilt from zero. "
    "What it's missing now is who it speaks to."
)
st.caption(
    "Two seasons ago Binghamton had no weekly traditions on the schedule. "
    "Today the calendar is dotted with them, and the team sits second among "
    "Double-A peers on how many home games carry a recurring promo. The "
    "remaining gap is in the mix, not the count."
)

st.divider()

# ─── Section 1: What we see ────────────────────────────────────────────────
st.subheader("What we see")

c1, c2 = st.columns([1, 1])

with c1:
    st.markdown("**Recurring promos at Binghamton, by season**")
    rp_season = load_rp_recurring_by_season()
    if not rp_season.empty:
        rp_season["season"] = rp_season["season"].astype(str)
        rp_season["pct_display"] = (rp_season["pct"] * 100).round(0).astype(int)
        fig = px.bar(
            rp_season,
            x="season", y="pct_display",
            color_discrete_sequence=[RP_COLOR],
            labels={"pct_display": "% of home games", "season": "Season"},
            text=rp_season["pct_display"].astype(str) + "%",
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(
            yaxis_range=[0, 45],
            yaxis_ticksuffix="%",
            showlegend=False,
            margin=dict(t=20, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Each bar: the share of regular-season home games that carried at "
            "least one recurring promotion. Two seasons of zeros, then a step "
            "change. 2026 is in-season (through June)."
        )

with c2:
    st.markdown("**Where Binghamton ranks in Double-A, 2025**")
    da = load_doublea_recurring_2025()
    if not da.empty:
        da = da.sort_values("pct", ascending=True).copy()
        da["pct_display"] = (da["pct"] * 100).round(1)
        da["is_rp"] = (da["team_id"] == RUMBLE_PONIES_ID)
        da["color"] = da["is_rp"].map({True: RP_COLOR, False: PEER_COLOR})

        fig = px.bar(
            da, y="team_name", x="pct_display",
            orientation="h",
            color="color", color_discrete_map="identity",
            labels={"pct_display": "% of home games with a recurring promo",
                    "team_name": ""},
            text=da["pct_display"].round(0).astype(int).astype(str) + "%",
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(
            xaxis_range=[0, 35],
            xaxis_ticksuffix="%",
            showlegend=False,
            margin=dict(t=20, b=20),
            height=420,
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Each row: a Double-A team's 2025 recurring-promo rate. Binghamton "
            "is highlighted. Restricted to teams with a full season of promo "
            "enrichment available."
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
    "Binghamton has 60 to 70 home games each season. Roughly 40 of those "
    "fall on a weeknight. If a single weeknight ritual lifted average "
    "attendance on its day by 200 fans, across the 14 home games of that "
    "day, that is 2,800 additional fans through the gate per season. At "
    f"the report's $30 per fan composite estimate, that is roughly "
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
    "is in the composition. The same number of recurring promo nights, "
    "but a different audience profile."
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

c1, c2 = st.columns(2)

with c1:
    st.markdown("**The Binghamton recurring slate**")
    rp_rituals = load_rp_named_rituals()
    if not rp_rituals.empty:
        rp_rituals["Day"] = rp_rituals["typical_dow"].astype(int).map(DOW_MAP)
        rp_rituals = rp_rituals.rename(columns={
            "offer_name": "Promotion",
            "n_games": "Games run",
            "first_season": "First season",
        })
        show = rp_rituals[["Promotion", "Day", "Games run", "First season"]]
        st.dataframe(show, use_container_width=True, hide_index=True)
        st.caption(
            "Recurring promotions Binghamton has run at least twice across "
            "2025 and 2026. The slate is family- and kid-anchored, with "
            "Twofer Tuesday as the lone ticket-deal-style ritual."
        )

with c2:
    st.markdown("**Examples of adult-oriented peer rituals**")
    peer = load_peer_named_rituals()
    if not peer.empty:
        peer = peer.rename(columns={
            "offer_name": "Promotion",
            "team_name":  "Team",
            "n_games":    "Games (2025)",
        })
        st.dataframe(peer.head(12), use_container_width=True, hide_index=True)
        st.caption(
            "Recurring promos that other Double-A teams ran at least five "
            "times in 2025 and that target food, drink, or theme-night "
            "formats. These are the kind of weekly habits Binghamton's "
            "calendar does not yet carry."
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
