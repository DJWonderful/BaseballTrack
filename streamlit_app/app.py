"""Grouped-navigation entry point.

Run with:
    streamlit run streamlit_app/app.py

Groups the sidebar into five sections so the user can tell at a glance which
pages are descriptive ("what happened"), which are narrative investigations,
and which turn into actions. The flat auto-discovery from `pages/` is still
intact -- running `Home.py` directly gives the legacy flat sidebar -- but this
app.py is now the default entry point.

Adding a new page: append one `st.Page(...)` line to the right section.
"""
from pathlib import Path

import streamlit as st

BASE = Path(__file__).parent


def P(rel: str, title: str, icon: str, default: bool = False) -> st.Page:
    """Shorthand. `rel` is a path relative to streamlit_app/."""
    return st.Page(str(BASE / rel), title=title, icon=icon, default=default)


NAV = {
    "Overview": [
        P("Home.py",                         "Home",               ":material/map:", default=True),
        P("pages/0_Executive_Overview.py",   "Executive Overview", ":material/dashboard:"),
    ],
    "Review": [
        P("pages/1_Attendance.py",           "Attendance",         ":material/groups:"),
        P("pages/2_Promotions.py",           "Promotions",         ":material/celebration:"),
        P("pages/3_Weather.py",              "Weather",            ":material/thermostat:"),
        P("pages/4_Opponents.py",            "Opponents",          ":material/sports_baseball:"),
        P("pages/5_Rehab_Assignments.py",    "Rehab Assignments",  ":material/medical_services:"),
        P("pages/6_Scheduling.py",           "Scheduling",         ":material/calendar_month:"),
        P("pages/7_Promo_Strategy.py",       "Promo Strategy",     ":material/category:"),
        P("pages/8_Team_Report.py",          "Team Report",        ":material/description:"),
        P("pages/9_Competitive_Intel.py",    "Competitive Intel",  ":material/insights:"),
    ],
    "Data Stories": [
        P("pages/11_Weekend_Playbook.py",    "Weekend Playbook",   ":material/chat:"),
        P("pages/12_Peer_Playbook.py",       "Peer Playbook",      ":material/compare_arrows:"),
    ],
    "Prescriptive": [
        P("pages/10_Recommendations.py",     "Recommendations",    ":material/checklist:"),
        P("pages/13_Hypothesis_Lab.py",      "Hypothesis Lab",     ":material/science:"),
    ],
    "Admin": [
        P("pages/99_Admin.py",               "Admin",              ":material/admin_panel_settings:"),
    ],
}


pg = st.navigation(NAV)
pg.run()
