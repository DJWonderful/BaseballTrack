"""Home page — Geographic overview of all MiLB teams.

This is your Streamlit introduction. Read the comments — they explain
every pattern you'll use across all pages in this app.

Run from project root:
    streamlit run streamlit_app/Home.py
"""

# ── Path setup ────────────────────────────────────────────────────────────────
# Streamlit runs each file in isolation, so we must tell Python where to find
# our local modules (utils/db.py). This block adds the streamlit_app/ folder
# to the module search path. You'll see this at the top of every page file.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))        # → streamlit_app/
sys.path.insert(0, str(Path(__file__).parent.parent)) # → project root (for .env)

# ── Imports ───────────────────────────────────────────────────────────────────
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from utils.db import query_df, load_game_attendance  # our cached query helpers
from utils.filters import operator_filter
from utils.footer import render_footer
from utils.navigation import see_also

# ── Page config ───────────────────────────────────────────────────────────────
# MUST be the very first Streamlit call in the file. Sets the browser tab title,
# icon, and layout. "wide" uses the full browser width — better for maps/charts.
st.set_page_config(
    page_title="MiLB Dashboard",
    page_icon="⚾",
    layout="wide",
)

# ── Constants ─────────────────────────────────────────────────────────────────
LEVEL_ORDER = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}

# Map region presets — sidebar radio button switches between these
REGIONS = {
    "Full USA":  {"lat": 38.5, "lon": -96.0, "zoom": 3.0},
    "Northeast": {"lat": 42.8, "lon": -73.5, "zoom": 5.5},
    "Southeast": {"lat": 32.5, "lon": -83.0, "zoom": 5.0},
    "Midwest":   {"lat": 41.5, "lon": -87.5, "zoom": 5.0},
    "West":      {"lat": 37.5, "lon": -120.0, "zoom": 4.5},
}

# ── Data loading ──────────────────────────────────────────────────────────────
# @st.cache_data means Streamlit runs this function ONCE and caches the result.
# On subsequent reruns (every time a user touches a widget) it returns the
# cached DataFrame instantly without hitting the DB again.
# ttl=600 means the cache expires after 10 minutes, then the query re-runs.

@st.cache_data(ttl=600)
def load_teams() -> pd.DataFrame:
    """All teams with venue coordinates, operator, and demographics."""
    return query_df("""
        SELECT
            t.team_id,
            t.team_name,
            t.sport_id,
            COALESCE(sp.sport_name, 'Unknown') AS level,
            v.venue_name,
            v.city,
            v.state,
            v.latitude::float  AS latitude,
            v.longitude::float AS longitude,
            v.capacity,
            COALESCE(op.operator_name, 'Independent') AS operator,
            d.msa_name,
            d.msa_population,
            d.msa_median_income,
            d.msa_poverty_rate,
            d.place_population,
            d.place_median_income,
            d.place_poverty_rate
        FROM milb.teams t
        JOIN milb.venues v ON t.venue_id = v.venue_id
        LEFT JOIN milb.sports sp ON t.sport_id = sp.sport_id
        LEFT JOIN milb.team_operators op ON t.operator_id = op.operator_id
        LEFT JOIN LATERAL (
            SELECT * FROM milb.venue_demographics d2
            WHERE d2.venue_id = v.venue_id
            ORDER BY d2.census_year DESC LIMIT 1
        ) d ON TRUE
        WHERE v.latitude IS NOT NULL
          AND v.capacity IS NOT NULL
          AND v.capacity > 0
          AND t.sport_id IN (11, 12, 13, 14)
        ORDER BY t.sport_id, t.team_name
    """)


@st.cache_data(ttl=600)
def load_attendance() -> pd.DataFrame:
    """Season attendance averages per team."""
    return query_df("""
        SELECT
            team_id,
            season,
            attendance_avg_home,
            attendance_total_home,
            games_home_total
        FROM milb.season_attendance
        WHERE game_type_id = 'R'
          AND attendance_avg_home IS NOT NULL
          AND attendance_avg_home > 0
        ORDER BY team_id, season
    """)


def build_map_df(teams: pd.DataFrame, attendance: pd.DataFrame,
                 season_mode: str = "all") -> pd.DataFrame:
    """Merge teams + attendance, compute trend and capacity utilization.

    season_mode: "historical" | "all" | "current"
    """
    att_wide = attendance.pivot_table(
        index="team_id", columns="season", values="attendance_avg_home"
    ).reset_index()

    all_seasons = sorted([c for c in att_wide.columns if isinstance(c, int)])
    max_season = all_seasons[-1] if all_seasons else None

    if season_mode == "historical" and max_season and len(all_seasons) >= 2:
        # Exclude current (in-progress) year
        seasons = [s for s in all_seasons if s != max_season]
        last_s = seasons[-1] if seasons else None
        first_s = seasons[0] if seasons else None
        if last_s:
            att_wide["avg_attendance"] = att_wide[last_s]
        else:
            att_wide["avg_attendance"] = None
        if first_s and last_s and first_s != last_s:
            att_wide["trend_pct"] = (
                (att_wide[last_s] - att_wide[first_s]) / att_wide[first_s] * 100
            ).round(1)
        else:
            att_wide["trend_pct"] = None

    elif season_mode == "current" and max_season:
        # Current year only -- no trend available
        att_wide["avg_attendance"] = att_wide.get(max_season)
        att_wide["trend_pct"] = None

    else:
        # "all" mode (default): latest season avg, trend from first to last
        first_s = all_seasons[0] if len(all_seasons) >= 2 else None
        last_s = all_seasons[-1] if all_seasons else None
        if last_s:
            att_wide["avg_attendance"] = att_wide[last_s]
        else:
            att_wide["avg_attendance"] = None
        if first_s and last_s and first_s != last_s:
            att_wide["trend_pct"] = (
                (att_wide[last_s] - att_wide[first_s]) / att_wide[first_s] * 100
            ).round(1)
        else:
            att_wide["trend_pct"] = None

    # Merge with team/venue info
    df = teams.merge(att_wide[["team_id", "avg_attendance", "trend_pct"]], on="team_id", how="left")

    # Capacity utilization
    df["capacity_util"] = (df["avg_attendance"] / df["capacity"] * 100).round(1)

    # Friendly level label
    df["level_label"] = df["sport_id"].map(LEVEL_ORDER).fillna(df["level"])

    # Bubble size: proportional to avg attendance, with a floor
    max_att = df["avg_attendance"].max() or 1
    df["bubble_size"] = ((df["avg_attendance"].fillna(500) / max_att) * 35 + 5).round(1)

    return df


# ── Sidebar ───────────────────────────────────────────────────────────────────
# st.sidebar.anything puts controls in the left panel.
# Streamlit reruns the whole script top-to-bottom when any widget changes.

with st.sidebar:
    st.header("Filters")

    # multiselect returns a list of the selected strings
    selected_levels = st.multiselect(
        "Level",
        options=list(LEVEL_ORDER.values()),
        default=list(LEVEL_ORDER.values()),
    )

    selected_operators = operator_filter()

    st.divider()
    exclude_rehab = st.checkbox(
        "Exclude rehab-window games",
        value=False,
        help=(
            "Drops games where an MLB player was on active rehab assignment "
            "with the home team before computing season averages. "
            "Uses a 30-day window when the rehab end date is unknown."
        ),
    )

    st.divider()
    st.header("Map view")

    season_view = st.radio(
        "Season view",
        options=["Historical (completed)", "All seasons (incl. current)", "Current year only"],
        index=1,
        help="Historical excludes the in-progress season. Current year shows only the latest season.",
    )

    region_name = st.radio("Region", options=list(REGIONS.keys()), index=0)

    st.divider()
    st.header("Color bubbles by")

    color_options = [
        "Attendance trend %",
        "Capacity utilization %",
        "Avg attendance",
        "MSA population",
        "Median income",
        "Poverty rate %",
        "Promo strategy cluster",
    ]
    color_by = st.radio("Color by", options=color_options, index=0)

    robust_scale = st.checkbox(
        "Robust color scale (clip 2–98%)",
        value=True,
        help=(
            "Clips the color palette to the 2nd–98th percentile of the "
            "filtered data. Extreme teams (e.g. Florida hurricane years) "
            "still show as the darkest/lightest color but stop stretching "
            "the palette. No teams or data are removed."
        ),
    )

# ── Load & filter data ────────────────────────────────────────────────────────
teams_df      = load_teams()
attendance_df = load_game_attendance(exclude_rehab=exclude_rehab)

# Map season view toggle to mode parameter
_mode_map = {
    "Historical (completed)": "historical",
    "All seasons (incl. current)": "all",
    "Current year only": "current",
}
_season_mode = _mode_map.get(season_view, "all")
df = build_map_df(teams_df, attendance_df, season_mode=_season_mode)

# Merge promo strategy clusters (for the "Promo strategy cluster" color mode)
_promo_clusters = query_df("""
    SELECT c.team_id, c.promo_cluster_label
    FROM milb.team_promo_clusters c
""")
df = df.merge(_promo_clusters, on="team_id", how="left")
df["promo_cluster_label"] = df["promo_cluster_label"].fillna("No data")

# Apply level filter from sidebar
df = df[df["level_label"].isin(selected_levels)]

# Apply operator filter
if selected_operators is not None:
    df = df[df["operator"].isin(selected_operators)]

# ── Page header ───────────────────────────────────────────────────────────────
st.title("⚾ MiLB League Overview")
st.caption(
    "All Minor League Baseball teams by location. "
    "Bubble size = average home attendance. Use the sidebar to filter and zoom."
)

# ── Prepare plot data (used by metrics, map, AND table) ─────────────────────
color_cfg = {
    "Attendance trend %": {
        "col":   "trend_pct",
        "scale": [[0, "#d73027"], [0.5, "#ffffbf"], [1, "#1a9850"]],
        "mid":   0,
        "label": "Trend %",
        "clip":  True,   # symmetric around 0
    },
    "Capacity utilization %": {
        "col":   "capacity_util",
        # Banded scale (red p10 / yellow-green mid / blue p90) is built
        # after plot_df is filtered — placeholder here.
        "scale": "Viridis",
        "mid":   None,
        "label": "Cap util %",
    },
    "Avg attendance": {
        "col":   "avg_attendance",
        "scale": "Viridis",
        "mid":   None,
        "label": "Avg attendance",
        "log":   True,   # attendance spans 500–11k, log makes lower tiers readable
        "clip":  True,   # asymmetric on log col
    },
    "MSA population": {
        "col":   "msa_population",
        "scale": "Plasma",
        "mid":   None,
        "label": "MSA Pop.",
        "log":   True,   # population is heavily log-distributed (150k–20M)
    },
    "Median income": {
        # Diverging around US national median household income. Teal = above,
        # brown = below — distinct from the red/yellow/green trend palette.
        "col":   "msa_median_income",
        "scale": [[0, "#8c510a"], [0.5, "#f5f5f5"], [1, "#01665e"]],
        "mid":   75000,
        "label": "Med. Income<br><sub>vs $75k US median</sub>",
        "clip":  True,   # symmetric around $75k
    },
    "Poverty rate %": {
        "col":   "msa_poverty_rate",
        "scale": [[0, "#1a9850"], [0.5, "#ffffbf"], [1, "#d73027"]],
        "mid":   12.5,  # US national poverty rate ≈ 12.5%
        "label": "Poverty %<br><sub>vs 12.5% US avg</sub>",
    },
}

# Auto-switch from trend % when trend data is unavailable (current year only mode)
if color_by == "Attendance trend %" and df["trend_pct"].isna().all():
    st.info("Trend data not available in single-season view. Showing average attendance instead.")
    color_by = "Avg attendance"

is_categorical = color_by == "Promo strategy cluster"


def _log_colorbar_ticks(raw_min: float, raw_max: float) -> tuple[list, list]:
    """Return (tickvals_log10, ticktext_raw) for a log-color colorbar."""
    targets = [100, 250, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 100_000,
               250_000, 500_000, 1_000_000, 2_500_000, 5_000_000,
               10_000_000, 20_000_000, 50_000_000]
    picks = [t for t in targets if raw_min <= t <= raw_max]
    if not picks:
        return [], []
    def fmt(v: float) -> str:
        if v >= 1_000_000: return f"{v/1_000_000:.1f}M".replace(".0M", "M")
        if v >= 1_000:     return f"{v/1_000:.0f}K"
        return f"{v:,.0f}"
    return [float(np.log10(t)) for t in picks], [fmt(t) for t in picks]


if is_categorical:
    plot_df = df.copy()
else:
    cc = color_cfg[color_by]
    plot_df = df[df[cc["col"]].notna()].copy()

    # Log-transform columns that span multiple orders of magnitude.
    if cc.get("log"):
        raw = plot_df[cc["col"]].astype(float)
        plot_df[cc["col"] + "_log"] = np.log10(raw.clip(lower=1))

    # Robust color scale: clip the color palette domain to p2–p98 of the
    # filtered data. Extremes still render (saturated at the endpoint) but
    # stop warping the palette for the middle 96%.
    cc["range_color"] = None
    if robust_scale and cc.get("clip") and len(plot_df) >= 20:
        clip_col = cc["col"] + "_log" if cc.get("log") else cc["col"]
        vals = plot_df[clip_col].astype(float).dropna()
        p2, p98 = vals.quantile(0.02), vals.quantile(0.98)
        if cc["mid"] is not None:
            # Symmetric clip around the midpoint for diverging palettes.
            half = max(abs(p2 - cc["mid"]), abs(p98 - cc["mid"]))
            cc["range_color"] = (cc["mid"] - half, cc["mid"] + half)
        else:
            cc["range_color"] = (float(p2), float(p98))

    # Capacity utilization: hard-banded scale with red bottom-10%, yellow→green
    # middle, blue top-10%. Thresholds recomputed from the filtered set.
    if color_by == "Capacity utilization %" and len(plot_df) >= 10:
        vals = plot_df["capacity_util"].astype(float)
        vmin, vmax = vals.min(), vals.max()
        p10, p90 = vals.quantile(0.10), vals.quantile(0.90)
        if vmax > vmin:
            s10 = max(0.001, min(0.999, (p10 - vmin) / (vmax - vmin)))
            s90 = max(s10 + 0.001, min(0.999, (p90 - vmin) / (vmax - vmin)))
            cc["scale"] = [
                [0.0, "#67000d"],   # deep red — worst of the worst
                [s10, "#fc9272"],   # light coral — just below p10 (gradient)
                [s10, "#ffffbf"],   # yellow — middle band start (hard jump)
                [s90, "#1a9850"],   # green — middle band end
                [s90, "#2c7fb8"],   # hard cutoff — top 10%
                [1.0, "#2c7fb8"],   # blue ceiling
            ]
            cc["label"] = f"Cap util %<br><sub>p10={p10:.0f} · p90={p90:.0f}</sub>"

# ── Metric cards (use plot_df so counts match map + table) ───────────────────
c1, c2, c3, c4 = st.columns(4)

teams_with_att = plot_df[plot_df["avg_attendance"].notna()]
growing = (plot_df["trend_pct"] > 0).sum()
shrinking = (plot_df["trend_pct"] < 0).sum()
avg_util = teams_with_att["capacity_util"].mean()

c1.metric("Teams shown",       f"{len(plot_df)}")
c2.metric("Growing (↑)",       f"{growing}",   delta=f"+{growing}")
c3.metric("Shrinking (↓)",     f"{shrinking}",  delta=f"-{shrinking}", delta_color="inverse")
c4.metric("Avg capacity util", f"{avg_util:.0f}%" if pd.notna(avg_util) else "—")

st.divider()

# ── Map ───────────────────────────────────────────────────────────────────────
region = REGIONS[region_name]

if plot_df.empty:
    st.warning("No data available for the selected filters.")
else:
    # Shared hover config
    hover_data = {
        "venue_name": True, "city": True, "level_label": True,
        "operator": True, "capacity": True, "avg_attendance": True,
        "capacity_util": True, "trend_pct": True,
        "msa_population": True, "msa_median_income": True,
        "msa_poverty_rate": True,
        "latitude": False, "longitude": False, "bubble_size": False,
    }
    labels = {
        "venue_name": "Venue", "city": "City", "level_label": "Level",
        "operator": "Operator", "capacity": "Capacity",
        "avg_attendance": "Avg Attendance", "capacity_util": "Cap Util %",
        "trend_pct": "Trend %", "msa_population": "MSA Pop.",
        "msa_median_income": "Med. Income $", "msa_poverty_rate": "Poverty %",
        "promo_cluster_label": "Promo Strategy",
    }

    if is_categorical:
        hover_data["promo_cluster_label"] = True
        # px.scatter_map with discrete color (string column)
        try:
            fig = px.scatter_map(
                plot_df,
                lat="latitude", lon="longitude",
                size="bubble_size",
                color="promo_cluster_label",
                hover_name="team_name",
                hover_data=hover_data,
                labels=labels,
                map_style="open-street-map",
                center={"lat": region["lat"], "lon": region["lon"]},
                zoom=region["zoom"],
                size_max=40, height=600,
            )
        except AttributeError:
            fig = px.scatter_mapbox(
                plot_df,
                lat="latitude", lon="longitude",
                size="bubble_size",
                color="promo_cluster_label",
                hover_name="team_name",
                hover_data=hover_data,
                labels=labels,
                mapbox_style="open-street-map",
                center={"lat": region["lat"], "lon": region["lon"]},
                zoom=region["zoom"],
                size_max=40, height=600,
            )
    else:
        # Decide whether to color by original or log-transformed column.
        if cc.get("log"):
            color_col = cc["col"] + "_log"
            hover_data[color_col] = False   # hide log col from tooltip
            hover_data[cc["col"]] = True    # keep raw visible
        else:
            color_col = cc["col"]
            hover_data[cc["col"]] = False

        # px.scatter_map with continuous color scale
        try:
            fig = px.scatter_map(
                plot_df,
                lat="latitude", lon="longitude",
                size="bubble_size",
                color=color_col,
                color_continuous_scale=cc["scale"],
                color_continuous_midpoint=cc["mid"],
                range_color=cc.get("range_color"),
                hover_name="team_name",
                hover_data=hover_data,
                labels=labels,
                map_style="open-street-map",
                center={"lat": region["lat"], "lon": region["lon"]},
                zoom=region["zoom"],
                size_max=40, height=600,
            )
        except AttributeError:
            fig = px.scatter_mapbox(
                plot_df,
                lat="latitude", lon="longitude",
                size="bubble_size",
                color=color_col,
                color_continuous_scale=cc["scale"],
                color_continuous_midpoint=cc["mid"],
                range_color=cc.get("range_color"),
                hover_name="team_name",
                hover_data=hover_data,
                labels=labels,
                mapbox_style="open-street-map",
                center={"lat": region["lat"], "lon": region["lon"]},
                zoom=region["zoom"],
                size_max=40, height=600,
            )

    fig.update_traces(marker_opacity=0.65)
    fig.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0})
    if not is_categorical:
        cbar = dict(title=cc["label"])
        if cc.get("log"):
            raw = plot_df[cc["col"]].astype(float)
            tvals, ttext = _log_colorbar_ticks(raw.min(), raw.max())
            if tvals:
                cbar["tickvals"] = tvals
                cbar["ticktext"] = ttext
        fig.update_layout(coloraxis_colorbar=cbar)

    st.plotly_chart(fig, use_container_width=True)

# ── Data table ────────────────────────────────────────────────────────────────
st.subheader(f"Team data ({len(plot_df)} teams)")

# Use plot_df so the table reflects exactly what the map shows
table_df = plot_df[[
    "team_name", "level_label", "operator", "city", "state", "venue_name",
    "capacity", "avg_attendance", "capacity_util", "trend_pct",
    "msa_population", "msa_median_income", "msa_poverty_rate",
]].copy()

table_df.columns = [
    "Team", "Level", "Operator", "City", "State", "Venue",
    "Capacity", "Avg Attendance", "Cap Util %", "Trend %",
    "MSA Pop.", "Med. Income $", "Poverty %",
]

# Coerce to numeric and round for display
for col in ["Avg Attendance", "Cap Util %", "Trend %", "Poverty %",
            "Capacity", "MSA Pop.", "Med. Income $"]:
    table_df[col] = pd.to_numeric(table_df[col], errors="coerce").round(1)

# st.dataframe renders an interactive sortable table.
# hide_index=True removes the pandas row numbers from the display.
st.dataframe(
    table_df.sort_values("Avg Attendance", ascending=False),
    use_container_width=True,
    hide_index=True,
)


# ── Cross-page navigation + footer ───────────────────────────────────────────
see_also([
    ("Executive Overview","pages/0_Executive_Overview.py","start here -- what this tool answers"),
    ("Attendance",        "pages/1_Attendance.py",        "baseline per-team trends"),
    ("Team Report",       "pages/8_Team_Report.py",       "per-team written brief"),
])
render_footer()
