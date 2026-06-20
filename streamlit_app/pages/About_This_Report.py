"""About This Report.

Honest framing: who built this, why, what's in it, what's not in it.
No team logos, no team-sanctioned implication. Plain English.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

from utils.footer import render_footer

st.set_page_config(page_title="About This Report", layout="wide")

st.title("About this report")

st.markdown("""
This is an independent, hobby-scale analytics project on Minor League
Baseball attendance. It was built by a single analyst with an interest in
sports data, not by a team, league, or vendor. There is no commercial
relationship with any of the organizations referenced.

The goal is to take publicly available MLB Stats API data, combine it with
publicly available demographic and weather data, and ask honest questions
about why some teams fill seats and others don't. The Rumble Ponies are the
running case study because of personal interest, and because Double-A is a
level where promotional decisions visibly move attendance.
""")

st.divider()

st.subheader("How to read this site")

st.markdown("""
The site is organized as a small briefing book, not a dashboard:

- **The Picture** is the landing page. It gives you the headline numbers
  and three finding cards.
- **What the Data Says** holds the three findings — Saturdays, Sundays,
  and the league-wide weekend shift. Each follows the same arc: *what we
  see → why it matters → what's behind it → what to do.*
- **Methodology & Deeper Analyses** holds every page that was built before
  this refactor. If you want to slice attendance, promotions, weather,
  opponents, demographics, or competitive intel directly, that's where to
  go. These pages are not pruned or simplified — they are the underlying
  analysis the findings are drawn from.

Every page ends with a *See also* block linking to related deeper pages,
and a data-freshness footer showing when the source data was last refreshed.
""")

st.divider()

st.subheader("What's in the data")

c1, c2 = st.columns(2)
with c1:
    st.markdown("""
**Included for every MiLB team, all four levels:**

- Game-by-game attendance, 2023–2026
- Game-time weather (temperature, precipitation, conditions)
- Promotional schedule (giveaways, fireworks, theme nights, etc.)
- Venue capacity, location, market demographics (ACS)
- Opponent and game-context metadata
- Rehab-assignment windows for MLB parent clubs
""")

with c2:
    st.markdown("""
**Not in the data:**

- Ticket prices (not exposed by the MLB Stats API)
- Pre-sale, walk-up, comp split (only total paid attendance)
- Revenue, concessions, parking
- Player payroll or roster economics
- Anything about marketing spend or media reach
""")

st.divider()

st.subheader("How the findings were arrived at")

st.markdown("""
- **Counterfactual lift estimates** for individual promotions come from an
  XGBoost model trained on the four-season game-level dataset. The model
  is evaluated by running each game twice — once with a promo flag on,
  once with it off — and reporting the average difference. This is more
  defensible than a simple "games with fireworks averaged X" because it
  controls for slot quality, opponent, weather, and day of week.
- **Peer groups** are built two ways: cluster-based (K-Means on market
  and operations variables) and weather-similarity (StandardScaler on
  climate and demographic factors).
- **Momentum labels** (surging / improving / stable / declining /
  struggling) combine year-over-year change, intra-season trend, and
  multi-season slope into a single z-score.
- See the Methodology pages for the underlying numbers behind any
  specific chart.
""")

st.divider()

st.subheader("Caveats")

st.markdown("""
- **2026 is in flight.** Numbers from this season are partial and will
  shift as the season completes. Where the analysis is sensitive to this,
  the page calls out the comparison window explicitly.
- **Promotion enrichment is imperfect.** Promo flags are derived from
  offer names using a two-tier pipeline (rules + LLM). High-confidence
  rules cover ~45% of promos; the rest go through an LLM classifier.
  Disagreements are rare on the headline flags (fireworks, giveaways) but
  do exist.
- **Correlation is not causation.** Where a finding leans on a
  counterfactual model, that's stated. Where it doesn't — for example,
  the league-wide weekend compression — the page is honest about it
  being a pattern without a proven cause.
- **This is not sanctioned by any team.** Take it as a second opinion to
  cross-reference, not as a directive.
""")

st.divider()

st.subheader("Who built this and why")

st.markdown("""
Built by Bill Sica (bill2@thesicas.com). Independent project, no
commercial relationship to any of the teams referenced.

The work is in service of a longer-term career direction toward
data science, with applications to graduate study planned in the next
year or so. The repo and methodology are happy to be discussed with
anyone who wants to look under the hood.
""")

render_footer()
