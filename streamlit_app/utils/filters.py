"""Shared sidebar filter helpers for all dashboard pages."""

import pandas as pd
import streamlit as st

from utils.db import query_df

# Valid MiLB/MLB game type codes and their display labels.
# Playoffs (F/D/L/W) will only appear in data after the next collect run.
GAME_TYPE_LABELS: dict[str, str] = {
    "R": "Regular Season",
    "F": "Wild Card",
    "D": "Division Series",
    "L": "League Championship",
    "W": "Championship",
}

GAME_TYPE_OPTIONS: list[str] = list(GAME_TYPE_LABELS.keys())


def game_type_filter(default: list[str] | None = None) -> tuple[str, ...]:
    """Render a game-type multiselect in the sidebar and return selected types.

    Call this inside a ``with st.sidebar:`` block (or anywhere in the sidebar).
    The return value is a sorted tuple so it works as a stable ``@st.cache_data``
    cache key — different selections produce different cached results.

    Args:
        default: List of game type codes to pre-select. Defaults to ``["R"]``.

    Returns:
        Sorted tuple of selected game type codes, e.g. ``("R",)`` or ``("F", "R")``.
    """
    if default is None:
        default = ["R"]

    selected = st.sidebar.multiselect(
        "Game Types",
        options=GAME_TYPE_OPTIONS,
        default=default,
        format_func=lambda t: GAME_TYPE_LABELS.get(t, t),
        help=(
            "Filter by game type. Regular Season is the default. "
            "Playoff rounds (Wild Card, Division Series, etc.) will appear "
            "here after the next data collection run."
        ),
    )

    if not selected:
        st.sidebar.warning("No game types selected — showing Regular Season.")
        selected = ["R"]

    return tuple(sorted(selected))


def game_type_sql(game_types: tuple[str, ...], col: str = "game_type") -> str:
    """Return a SQL IN fragment for the given game types.

    Values come only from the controlled UI — string formatting is safe here.

    Example:
        game_type_sql(("R", "F"))  →  "game_type IN ('F', 'R')"

    Args:
        game_types: Tuple of game type codes (from :func:`game_type_filter`).
        col: Column name to filter on (default ``"game_type"``).

    Returns:
        SQL string like ``"game_type IN ('R')"`` ready to embed in a query.
    """
    escaped = ", ".join(f"'{t}'" for t in sorted(game_types))
    return f"{col} IN ({escaped})"


# ── Operator / ownership filter ──────────────────────────────────────────────

@st.cache_data(ttl=600)
def _load_operators() -> pd.DataFrame:
    return query_df("""
        SELECT operator_id, operator_name
        FROM milb.team_operators
        ORDER BY operator_name
    """)


def operator_filter() -> tuple[str, ...] | None:
    """Render an operator multiselect in the sidebar.

    Returns a tuple of selected operator names, or None for "All".
    """
    ops = _load_operators()
    if ops.empty:
        return None

    names = ["All"] + ops["operator_name"].tolist()
    selected = st.sidebar.multiselect(
        "Team Operator",
        options=names,
        default=["All"],
        help="Filter by team ownership group. 'All' shows every team.",
    )

    if not selected or "All" in selected:
        return None
    return tuple(sorted(selected))


# ── Promo-category exclude filter ────────────────────────────────────────────
# Default INCLUDES all categories (including Fireworks -- we want them visible
# in narratives, just optionally filterable when digging for less-obvious levers).

PROMO_CATEGORIES: dict[str, str] = {
    "has_fireworks":   "Fireworks",
    "has_giveaway":    "Giveaway",
    "has_food_deal":   "Food Deal",
    "has_ticket_deal": "Ticket Deal",
    "has_theme_night": "Theme Night",
    "has_kids_event":  "Kids Event",
    "has_heritage":    "Heritage Night",
    "has_community":   "Community Event",
    "has_entertain":   "Entertainment",
    "has_dog":         "Dog Friendly",
    "has_celebrity":   "Celebrity",
    "has_recurring":   "Recurring",
}


# ── Team selector (Rumble Ponies is the hero team, always preselected) ──────

HERO_TEAM_NAME = "Binghamton Rumble Ponies"
HERO_TEAM_ID = 505


def team_selector(
    team_names: list[str],
    label: str = "Team",
    include_all: bool = False,
    all_label: str = "— All teams —",
    key: str | None = None,
    help: str | None = None,
    sidebar: bool = False,
) -> str:
    """Render a team selectbox with Binghamton Rumble Ponies preselected.

    This is the canonical team picker. Rumble Ponies is the hero team we
    are working for — every selector across the dashboard should default
    to it. Users pick other teams only to compare.

    Args:
        team_names: List of available team names (already filtered by level/etc.).
        label: Selectbox label.
        include_all: If True, prepend `all_label` as an option. Binghamton
            still wins the default when present.
        all_label: Prefix label when `include_all=True`.
        key: Streamlit widget key.
        help: Streamlit help tooltip.
        sidebar: Render in the sidebar if True.

    Returns:
        The selected team name (or `all_label` if the user picked "all").
    """
    options = list(team_names)
    if include_all and all_label not in options:
        options = [all_label] + options

    if HERO_TEAM_NAME in options:
        default_idx = options.index(HERO_TEAM_NAME)
    else:
        default_idx = 0

    target = st.sidebar if sidebar else st
    return target.selectbox(
        label,
        options=options,
        index=default_idx,
        key=key,
        help=help,
    )


def promo_exclude_filter(key: str = "promo_exclude") -> tuple[str, ...]:
    """Render a promo-category exclude multiselect. Returns tuple of excluded flag names.

    Empty return = nothing excluded (default). Users opt in to excluding
    categories they want to ignore (e.g. "show me non-fireworks opportunities").
    """
    label_to_flag = {v: k for k, v in PROMO_CATEGORIES.items()}
    selected_labels = st.sidebar.multiselect(
        "Exclude promo categories",
        options=list(PROMO_CATEGORIES.values()),
        default=[],
        key=key,
        help=(
            "Hide specific promo categories from charts and recommendations. "
            "Useful when you already know something works (e.g. Fireworks) and "
            "want to surface less-obvious opportunities."
        ),
    )
    return tuple(sorted(label_to_flag[l] for l in selected_labels))


# ── Standard sidebar layout ──────────────────────────────────────────────────
# Enforces a consistent visual order across pages:
#   Filters:    Level -> Operator -> Team
#   ─ divider ─
#   View:       Season / Game Type / Promo Exclude
#   ─ divider ─
#   Advanced:   (page-specific; rendered by caller in an expander)
#
# Pages still have full freedom -- this is opt-in. Pages with unique needs
# (Home.py's map controls) are free to diverge.

