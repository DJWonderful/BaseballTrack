"""Weather impact on attendance.

New Streamlit / pandas patterns introduced here:
  - st.tabs() with 3 tabs (Temp / Conditions / Wind)
  - Binning continuous data into labeled buckets (pd.cut)
  - px.bar with error bars (showing variance, not just averages)
  - px.box (box-and-whisker plots — shows spread + outliers)
"""

# ── Path setup ────────────────────────────────────────────────────────────────
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import re
import pandas as pd
import plotly.express as px
import streamlit as st

from utils.db import query_df
from utils.filters import game_type_filter, game_type_sql
from utils.footer import render_footer
from utils.navigation import see_also

st.set_page_config(page_title="Weather | MiLB", page_icon="🌤️", layout="wide")

LEVEL_ORDER = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}

# ── Condition buckets ─────────────────────────────────────────────────────────
# Map raw API strings → cleaner display labels
CONDITION_MAP = {
    "Clear":         "Clear",
    "Sunny":         "Clear",
    "Partly Cloudy": "Partly Cloudy",
    "Cloudy":        "Cloudy/Overcast",
    "Overcast":      "Cloudy/Overcast",
    "Drizzle":       "Drizzle",
    "Rain":          "Rain",
    "Roof Closed":   "Roof Closed",
    "Snow":          "Snow/Other",
}
CONDITION_ORDER = ["Clear", "Partly Cloudy", "Cloudy/Overcast", "Drizzle", "Rain"]

# Wind speed buckets
WIND_BINS   = [0, 3, 8, 14, 20, 999]
WIND_LABELS = ["Calm (0-3)", "Light (4-8)", "Moderate (9-14)", "Brisk (15-20)", "Strong (21+)"]

# Temperature bins (5 °F each), filtered to realistic baseball range
TEMP_BINS   = list(range(45, 106, 5))   # 45,50,55,...,100,105
TEMP_LABELS = [f"{t}-{t+4}°" for t in TEMP_BINS[:-1]]


# ── Data loading ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def load_teams() -> pd.DataFrame:
    return query_df("""
        SELECT t.team_id, t.team_name, t.sport_id,
               COALESCE(sp.sport_name, 'Unknown') AS level
        FROM milb.teams t
        LEFT JOIN milb.sports sp ON t.sport_id = sp.sport_id
        WHERE t.sport_id IN (11,12,13,14)
        ORDER BY t.sport_id, t.team_name
    """)


@st.cache_data(ttl=600)
def load_weather_games(game_types: tuple = ("R",)) -> pd.DataFrame:
    """All home games (with attendance + weather) for the selected game types.

    Left-joins game_features so every row also carries start_time_bucket and
    venue_capacity. R games get a bucket; postseason stays NULL.
    """
    df = query_df(f"""
        SELECT g.game_pk, g.home_team_id AS team_id, g.game_date, g.season,
               g.attendance, g.weather_temp_f, g.weather_condition, g.weather_wind,
               f.start_time_bucket, f.venue_capacity
          FROM milb.games g
          LEFT JOIN milb.game_features f ON f.game_pk = g.game_pk
         WHERE g.abstract_game_state = 'Final'
           AND {game_type_sql(game_types, 'g.game_type')}
           AND g.attendance     IS NOT NULL AND g.attendance > 0
           AND g.weather_temp_f IS NOT NULL
           AND g.sport_id IN (11,12,13,14)
    """)

    # ── Parse wind speed from "7 mph, R To L" → 7
    # re.match pulls the leading digits before " mph"
    def parse_wind(s):
        if not isinstance(s, str):
            return None
        m = re.match(r"(\d+)\s+mph", s)
        return int(m.group(1)) if m else None

    df["wind_mph"] = df["weather_wind"].apply(parse_wind)

    # ── Filter obviously bad temp readings (sensor errors)
    df = df[(df["weather_temp_f"] >= 40) & (df["weather_temp_f"] <= 110)].copy()

    # ── Bin temperature into 5-degree buckets
    # pd.cut() converts a continuous column into labeled intervals.
    # right=False means each bin is [left, right) — 55 lands in "55-59°", not "50-54°".
    df["temp_bucket"] = pd.cut(
        df["weather_temp_f"],
        bins=TEMP_BINS,
        labels=TEMP_LABELS,
        right=False,
    )

    # ── Map raw condition strings to clean buckets
    df["condition_bucket"] = df["weather_condition"].map(CONDITION_MAP).fillna("Other")

    # ── Bin wind speed
    df["wind_bucket"] = pd.cut(
        df["wind_mph"].fillna(0),
        bins=WIND_BINS,
        labels=WIND_LABELS,
        right=True,
    )

    return df


# ── Sidebar ───────────────────────────────────────────────────────────────────
teams_df = load_teams()
teams_df["level_label"] = teams_df["sport_id"].map(LEVEL_ORDER).fillna(teams_df["level"])

with st.sidebar:
    st.header("Filters")

    selected_levels = st.multiselect(
        "Level",
        options=list(LEVEL_ORDER.values()),
        default=list(LEVEL_ORDER.values()),
    )

    level_teams = teams_df[teams_df["level_label"].isin(selected_levels)]
    team_options = ["— All teams —"] + level_teams.sort_values("team_name")["team_name"].tolist()
    _default_idx = team_options.index("Binghamton Rumble Ponies") if "Binghamton Rumble Ponies" in team_options else 0
    selected_team_name = st.selectbox("Team", options=team_options, index=_default_idx)

    st.divider()
    selected_game_types = game_type_filter()

    st.divider()
    st.caption(
        "Weather data comes from the MLB API at game time. "
        "~99% of regular-season games have weather records."
    )


# ── Load and filter ───────────────────────────────────────────────────────────
wx = load_weather_games(game_types=selected_game_types)

level_ids = set(level_teams["team_id"])
wx = wx[wx["team_id"].isin(level_ids)].copy()

if selected_team_name != "— All teams —":
    team_id = int(teams_df.loc[teams_df["team_name"] == selected_team_name, "team_id"].iloc[0])
    wx = wx[wx["team_id"] == team_id].copy()

# ── Page header ───────────────────────────────────────────────────────────────
scope = selected_team_name if selected_team_name != "— All teams —" else "All selected teams"
st.title("🌤️ Weather Impact on Attendance")
st.caption(f"Showing {len(wx):,} games · {scope}")

if wx.empty:
    st.warning("No weather data for the selected filters.")
    st.stop()

# ── Metric row ────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Games with weather data",  f"{len(wx):,}")
c2.metric("Avg temperature",          f"{wx['weather_temp_f'].mean():.0f}°F")
c3.metric("Rain / Drizzle games",
          f"{wx['condition_bucket'].isin(['Rain','Drizzle']).sum():,}",
          help="Games with Rain or Drizzle at game time")
c4.metric("Avg wind speed",
          f"{wx['wind_mph'].mean():.1f} mph" if wx["wind_mph"].notna().any() else "—")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
tab_temp, tab_cond, tab_wind = st.tabs(["🌡️ Temperature", "☁️ Conditions", "💨 Wind"])


# ── TAB 1: Temperature ────────────────────────────────────────────────────────
with tab_temp:
    st.subheader("Attendance by game-time temperature")
    st.caption(
        "Games binned into 5°F buckets. Bar height = average attendance. "
        "Error bars show ±1 standard deviation (spread of individual games)."
    )

    # Aggregate per bucket: mean + std + count
    temp_agg = (
        wx.dropna(subset=["temp_bucket"])
        .groupby("temp_bucket", observed=True)["attendance"]
        .agg(avg=("mean"), std=("std"), n=("count"))
        .reset_index()
    )
    temp_agg["avg"] = temp_agg["avg"].round(0)
    temp_agg["std"] = temp_agg["std"].round(0).fillna(0)

    # px.bar with error_y adds ±std error bars to each column.
    # This shows not just the average but how much games vary at each temperature.
    fig_temp = px.bar(
        temp_agg,
        x="temp_bucket",
        y="avg",
        error_y="std",
        text="n",    # show game count on each bar
        labels={"temp_bucket": "Temperature (°F)", "avg": "Avg Attendance", "n": "Games"},
        color="avg",
        color_continuous_scale="RdYlGn",   # red (low) → yellow → green (high)
        height=420,
    )
    fig_temp.update_traces(textposition="outside", texttemplate="%{text} games")
    fig_temp.update_layout(
        coloraxis_showscale=False,
        xaxis_tickangle=-45,
        margin={"t": 30, "b": 60},
    )
    st.plotly_chart(fig_temp, use_container_width=True)

    # Box plot option — shows full distribution at each temp bucket
    st.subheader("Attendance distribution by temperature (box plot)")
    st.caption(
        "Each box shows the median (middle line), 25th-75th percentile range (box), "
        "and outliers (dots beyond the whiskers). Reveals whether high attendance "
        "games cluster at certain temperatures."
    )

    # px.box — a box-and-whisker chart. Each category gets its own box.
    # points=False hides individual dots (too many at this scale).
    fig_box = px.box(
        wx.dropna(subset=["temp_bucket"]),
        x="temp_bucket",
        y="attendance",
        color="temp_bucket",
        points=False,
        category_orders={"temp_bucket": TEMP_LABELS},
        labels={"temp_bucket": "Temperature (°F)", "attendance": "Attendance"},
        color_discrete_sequence=px.colors.qualitative.Pastel,
        height=380,
    )
    fig_box.update_layout(
        showlegend=False,
        xaxis_tickangle=-45,
        margin={"t": 10, "b": 60},
    )
    st.plotly_chart(fig_box, use_container_width=True)

    # ── Temp x start-time heatmap ─────────────────────────────────────────────
    # Answers: does a hot matinee hurt worse than a hot evening? (Yes.)
    st.subheader("Temperature x start-time bucket")
    st.caption(
        "Average attendance at each (temperature band x start-time bucket). "
        "Buckets come from venue-local game_datetime."
    )
    BUCKET_ORDER = ["morning", "noon", "matinee", "early_evening", "evening", "late"]
    BUCKET_LABEL = {
        "morning": "Morning (<11am)", "noon": "Noon (11-1pm)",
        "matinee": "Matinee (1-4pm)", "early_evening": "Early eve (4-6pm)",
        "evening": "Evening (6-8pm)", "late": "Late (8pm+)",
    }
    # Coarser temperature bands so cells have enough games
    TEMP_BANDS = [(-1, 55, "Cold (<55)"), (55, 70, "Cool (55-70)"),
                  (70, 85, "Warm (70-85)"), (85, 999, "Hot (85+)")]
    TEMP_BAND_ORDER = [b[2] for b in TEMP_BANDS]

    def _temp_band(f):
        if pd.isna(f):
            return None
        for lo, hi, name in TEMP_BANDS:
            if lo <= f < hi:
                return name
        return None

    tx = wx.dropna(subset=["start_time_bucket"]).copy()
    tx["temp_band"] = tx["weather_temp_f"].apply(_temp_band)
    tx = tx[tx["temp_band"].notna()]
    if tx.empty:
        st.caption("No start-time bucket data for the current filter.")
    else:
        agg = (tx.groupby(["temp_band", "start_time_bucket"])
                 .agg(avg_att=("attendance", "mean"), n=("game_pk", "count"))
                 .reset_index())
        agg.loc[agg["n"] < 10, "avg_att"] = None  # suppress noisy cells
        heat = (agg.pivot(index="temp_band", columns="start_time_bucket",
                          values="avg_att")
                    .reindex(index=TEMP_BAND_ORDER, columns=BUCKET_ORDER))
        fig_txb = px.imshow(
            heat.values,
            x=[BUCKET_LABEL[b] for b in BUCKET_ORDER],
            y=TEMP_BAND_ORDER,
            color_continuous_scale="RdYlGn",
            labels={"x": "Start-time bucket", "y": "Temperature band",
                    "color": "Avg attendance"},
            text_auto=".0f", aspect="auto",
        )
        fig_txb.update_layout(height=320, margin={"t": 10, "b": 10})
        fig_txb.update_traces(hovertemplate="%{y}, %{x}<br>%{z:,.0f} fans<extra></extra>")
        st.plotly_chart(fig_txb, use_container_width=True)
        st.caption("Cells with fewer than 10 games are blank to avoid noise.")


# ── TAB 2: Conditions ─────────────────────────────────────────────────────────
with tab_cond:
    st.subheader("Attendance by weather condition")
    st.caption(
        "Conditions reported by the MLB API at game time. "
        "Only conditions with at least 10 games are shown."
    )

    cond_agg = (
        wx[wx["condition_bucket"].isin(CONDITION_ORDER)]
        .groupby("condition_bucket")["attendance"]
        .agg(avg=("mean"), std=("std"), n=("count"))
        .reset_index()
        .query("n >= 10")
    )
    cond_agg["avg"] = cond_agg["avg"].round(0)
    cond_agg["std"] = cond_agg["std"].round(0).fillna(0)

    # Sort by the CONDITION_ORDER list so bars go Clear→Rain (not alphabetical)
    cond_agg["_sort"] = cond_agg["condition_bucket"].map(
        {c: i for i, c in enumerate(CONDITION_ORDER)}
    )
    cond_agg = cond_agg.sort_values("_sort")

    fig_cond = px.bar(
        cond_agg,
        x="condition_bucket",
        y="avg",
        error_y="std",
        text="n",
        labels={
            "condition_bucket": "Condition",
            "avg": "Avg Attendance",
            "n": "Games",
        },
        color="condition_bucket",
        color_discrete_map={
            "Clear":            "#f9c74f",
            "Partly Cloudy":    "#90be6d",
            "Cloudy/Overcast":  "#577590",
            "Drizzle":          "#4d908e",
            "Rain":             "#277da1",
        },
        category_orders={"condition_bucket": CONDITION_ORDER},
        height=400,
    )
    fig_cond.update_traces(textposition="outside", texttemplate="%{text} games")
    fig_cond.update_layout(
        showlegend=False,
        margin={"t": 30, "b": 20},
    )
    st.plotly_chart(fig_cond, use_container_width=True)

    # Box plot per condition
    fig_cond_box = px.box(
        wx[wx["condition_bucket"].isin(CONDITION_ORDER)],
        x="condition_bucket",
        y="attendance",
        color="condition_bucket",
        points=False,
        category_orders={"condition_bucket": CONDITION_ORDER},
        color_discrete_map={
            "Clear":            "#f9c74f",
            "Partly Cloudy":    "#90be6d",
            "Cloudy/Overcast":  "#577590",
            "Drizzle":          "#4d908e",
            "Rain":             "#277da1",
        },
        labels={"condition_bucket": "Condition", "attendance": "Attendance"},
        height=350,
    )
    fig_cond_box.update_layout(showlegend=False, margin={"t": 10, "b": 20})
    st.plotly_chart(fig_cond_box, use_container_width=True)


# ── TAB 3: Wind ───────────────────────────────────────────────────────────────
with tab_wind:
    st.subheader("Attendance by wind speed")
    st.caption(
        "Wind speed parsed from the MLB API string (e.g. '7 mph, R To L'). "
        "Direction is ignored — only speed matters for attendance comfort."
    )

    wind_agg = (
        wx.dropna(subset=["wind_bucket"])
        .groupby("wind_bucket", observed=True)["attendance"]
        .agg(avg=("mean"), std=("std"), n=("count"))
        .reset_index()
        .query("n >= 5")
    )
    wind_agg["avg"] = wind_agg["avg"].round(0)
    wind_agg["std"] = wind_agg["std"].round(0).fillna(0)

    fig_wind = px.bar(
        wind_agg,
        x="wind_bucket",
        y="avg",
        error_y="std",
        text="n",
        labels={"wind_bucket": "Wind Speed", "avg": "Avg Attendance", "n": "Games"},
        color="avg",
        color_continuous_scale="Blues",
        category_orders={"wind_bucket": WIND_LABELS},
        height=380,
    )
    fig_wind.update_traces(textposition="outside", texttemplate="%{text} games")
    fig_wind.update_layout(
        coloraxis_showscale=False,
        margin={"t": 30, "b": 20},
    )
    st.plotly_chart(fig_wind, use_container_width=True)

    # Scatter: raw wind_mph vs attendance (only if reasonable # of games)
    if len(wx) <= 5000:
        st.subheader("Wind speed vs attendance (individual games)")
        fig_ws = px.scatter(
            wx.dropna(subset=["wind_mph"]),
            x="wind_mph",
            y="attendance",
            opacity=0.3,
            labels={"wind_mph": "Wind Speed (mph)", "attendance": "Attendance"},
            color_discrete_sequence=["#3a9bd5"],
            height=320,
        )
        fig_ws.update_traces(marker_size=4)
        fig_ws.update_layout(margin={"t": 10, "b": 10})
        st.plotly_chart(fig_ws, use_container_width=True)
    else:
        st.caption(
            "Scatter plot hidden for league-wide view (too many points). "
            "Select a single team to see individual game scatter."
        )


# ── Cross-page navigation + footer ───────────────────────────────────────────
see_also([
    ("Peer Playbook",    "pages/12_Peer_Playbook.py",   "what cold-weather small-market peers do differently"),
    ("Competitive Intel","pages/9_Competitive_Intel.py","find teams with similar weather profiles"),
    ("Attendance",       "pages/1_Attendance.py",       "baseline trends that factor out weather"),
])
render_footer(scripts=["build_features"])
