"""Shared visual theme.

Single source of truth for non-map colors so every page looks like one product.
Map palettes are intentionally NOT defined here -- they live in Home.py where
they were tuned for legibility on white map tiles. Do not port those here.

Usage:
    from utils.theme import SEASON_COLORS, LEVEL_COLORS, DIVERGING, SEQUENTIAL

    # Categorical by season (locked-by-year -- stable under filtering):
    px.line(df, color="season", color_discrete_map=SEASON_COLORS)

    # Categorical by level:
    px.bar(df, color="level_label", color_discrete_map=LEVEL_COLORS)

    # Diverging (lift, trend %, residuals):
    px.imshow(df, color_continuous_scale=DIVERGING, color_continuous_midpoint=0)

    # Sequential (counts, densities not on maps):
    px.bar(df, color="n_games", color_continuous_scale=SEQUENTIAL)
"""

# Season -> locked color. Passed as color_discrete_map so colors stay stable
# when users filter seasons in/out. Extended 5 years out from 2023.
SEASON_COLORS = {
    "2023": "#e07b39",  # orange
    "2024": "#3a9bd5",  # blue
    "2025": "#6fcf6f",  # green
    "2026": "#b064a0",  # purple
    "2027": "#e8c547",  # gold
    # Integer keys too, since some pages don't cast season to str
    2023: "#e07b39",
    2024: "#3a9bd5",
    2025: "#6fcf6f",
    2026: "#b064a0",
    2027: "#e8c547",
}

# Level -> color. Warm = higher level, cool = lower, so Triple-A reads as
# "big league adjacent" and Single-A reads as "bottom of the ladder".
LEVEL_COLORS = {
    "Triple-A": "#d4572e",
    "Double-A": "#e8a23e",
    "High-A":   "#5aa9d9",
    "Single-A": "#3d6fb5",
}

# Fallback discrete sequence for ad-hoc categorical charts (teams, promo types).
# Set2 with a couple of custom hues at the front for brand consistency.
DISCRETE_SEQ = [
    "#e07b39", "#3a9bd5", "#6fcf6f", "#b064a0",
    "#e8c547", "#66c2a5", "#fc8d62", "#8da0cb",
    "#a6d854", "#ffd92f",
]

# Momentum labels -> colors (9_Competitive_Intel, Team Report)
MOMENTUM_COLORS = {
    "surging":    "#1a9850",
    "improving":  "#66bd63",
    "stable":     "#fee08b",
    "declining":  "#fdae61",
    "struggling": "#d73027",
}

# Priority pills on recommendations / team report
PRIORITY_COLORS = {
    "P1": "#d73027",  # red -- high priority, act now
    "P2": "#fdae61",  # amber -- medium
    "P3": "#5aa9d9",  # blue -- low / informational
}

# Semantic up/down colors. Use wherever "good direction" is positive.
POSITIVE = "#1a9850"
NEGATIVE = "#d73027"
NEUTRAL  = "#95a5a6"

# Continuous scales -- NOT FOR MAPS.
DIVERGING  = "RdYlGn"   # lift, trend %, residuals, anything centered on 0
SEQUENTIAL = "Blues"    # counts, densities -- avoid on the map page


def priority_pill(priority: str) -> str:
    """HTML pill for priority display. Needs unsafe_allow_html=True at call site."""
    c = PRIORITY_COLORS.get(priority, NEUTRAL)
    return (
        f'<span style="background:{c};color:white;padding:2px 8px;'
        f'border-radius:10px;font-size:0.8em;font-weight:600">{priority}</span>'
    )


def momentum_pill(label: str) -> str:
    """HTML pill for momentum label. Needs unsafe_allow_html=True at call site."""
    c = MOMENTUM_COLORS.get((label or "").lower(), NEUTRAL)
    return (
        f'<span style="background:{c};color:white;padding:2px 8px;'
        f'border-radius:10px;font-size:0.85em">{label}</span>'
    )
