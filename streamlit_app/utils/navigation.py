"""Cross-page navigation cues.

Usage at bottom of a page, just above render_footer():

    from utils.navigation import see_also
    see_also([
        ("Promotions",    "pages/2_Promotions.py",  "dig into promo lift and stacking"),
        ("Recommendations","pages/10_Recommendations.py", "see prioritized actions for this team"),
    ])
"""
from __future__ import annotations

import streamlit as st


def see_also(items: list[tuple[str, str, str]]) -> None:
    """Render a 'See also' block of page_links.

    items: list of (label, page_path, hint) tuples.
           page_path is relative to the streamlit root, e.g. "pages/2_Promotions.py"
    """
    if not items:
        return
    st.markdown("**See also**")
    for label, path, hint in items:
        try:
            st.page_link(path, label=f"{label} -- {hint}")
        except Exception:
            # Older Streamlit or missing page: fall back to plain caption
            st.caption(f"- {label} -- {hint}  ({path})")
