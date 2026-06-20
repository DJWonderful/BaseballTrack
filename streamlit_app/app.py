"""Grouped-navigation entry point.

Run with:
    streamlit run streamlit_app/app.py

Refactor (2026-06): default landing is now `Picture.py`, the briefing-book
overview. The findings group below it tells the story in plain English. Every
prior page (Attendance, Promotions, Weather, etc.) is preserved under
"Methodology & deeper analyses" — nothing was deleted or moved on disk; this
file just groups them differently.

Running `Home.py` directly still gives the legacy map-first flat sidebar.

Adding a new page: append one `st.Page(...)` line to the right section.
"""
from pathlib import Path

import streamlit as st

BASE = Path(__file__).parent


def P(rel: str, title: str, icon: str, default: bool = False) -> st.Page:
    """Shorthand. `rel` is a path relative to streamlit_app/."""
    return st.Page(str(BASE / rel), title=title, icon=icon, default=default)


NAV = {
    "The Picture": [
        P("Picture.py",                                     "The Picture",            ":material/visibility:", default=True),
    ],
    "What the Data Says": [
        P("pages/findings/01_Saturdays.py",                 "Saturdays",              ":material/event:"),
        P("pages/findings/02_Sundays.py",                   "Sundays",                ":material/wb_sunny:"),
        P("pages/findings/03_The_League_Is_Shifting.py",    "The League is Shifting", ":material/trending_down:"),
    ],
    "About": [
        P("pages/About_This_Report.py",                     "About This Report",      ":material/info:"),
    ],
    "Methodology & Deeper Analyses": [
        P("Home.py",                                        "League Map",             ":material/map:"),
        P("pages/0_Executive_Overview.py",                  "Executive Overview",     ":material/dashboard:"),
        P("pages/1_Attendance.py",                          "Attendance",             ":material/groups:"),
        P("pages/2_Promotions.py",                          "Promotions",             ":material/celebration:"),
        P("pages/3_Weather.py",                             "Weather",                ":material/thermostat:"),
        P("pages/4_Opponents.py",                           "Opponents",              ":material/sports_baseball:"),
        P("pages/5_Rehab_Assignments.py",                   "Rehab Assignments",      ":material/medical_services:"),
        P("pages/6_Scheduling.py",                          "Scheduling",             ":material/calendar_month:"),
        P("pages/7_Promo_Strategy.py",                      "Promo Strategy",         ":material/category:"),
        P("pages/8_Team_Report.py",                         "Team Report",            ":material/description:"),
        P("pages/9_Competitive_Intel.py",                   "Competitive Intel",      ":material/insights:"),
        P("pages/10_Recommendations.py",                    "Recommendations",        ":material/checklist:"),
        P("pages/11_Weekend_Playbook.py",                   "Weekend Playbook",       ":material/chat:"),
        P("pages/12_Peer_Playbook.py",                      "Peer Playbook",          ":material/compare_arrows:"),
        P("pages/13_Hypothesis_Lab.py",                     "Hypothesis Lab",         ":material/science:"),
        P("pages/99_Admin.py",                              "Admin",                  ":material/admin_panel_settings:"),
    ],
}


pg = st.navigation(NAV)
pg.run()
