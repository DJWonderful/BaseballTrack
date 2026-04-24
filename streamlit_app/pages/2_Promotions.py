"""Promotion impact on attendance.

New Streamlit / pandas patterns introduced here:
  - Aggregating multi-row promotion data to game level with BOOL_OR (in SQL)
    and then computing attendance lift vs. each team's seasonal baseline
  - Computing "lift" = attendance minus team-season mean (controls for park size)
  - px.bar with pre-computed hex color column + color_discrete_map="identity"
  - px.box for comparing attendance distributions across groups
  - "Multi-hot to combinations" pattern:
      pivot boolean flag columns → filter rows where sum >= 2 → build combo labels
      to discover the most common and highest-lift promotion pairings
  - px.imshow for 2-D heatmap (promo type × day/month, color = avg lift)
  - pd.melt to convert wide boolean flag columns to long (promo_type, active) rows
  - color_continuous_midpoint to center a diverging colorscale at zero lift
"""

# ── Path setup ────────────────────────────────────────────────────────────────
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.db import query_df
from utils.filters import (
    game_type_filter, game_type_sql, operator_filter,
    promo_exclude_filter, PROMO_CATEGORIES,
)
from utils.theme import SEASON_COLORS, DIVERGING
from utils.footer import render_footer
from utils.navigation import see_also

st.set_page_config(page_title="Promotions | MiLB", page_icon="📣", layout="wide")

LEVEL_ORDER = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}
MONTH_ABBR  = {4: "Apr", 5: "May", 6: "Jun", 7: "Jul", 8: "Aug", 9: "Sep"}
DOW_ORDER   = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# ── Promotion flag columns and their display names ────────────────────────────
# These match the aliased BOOL_OR columns returned by the main SQL query.
FLAG_COLS = [
    "has_fireworks",
    "has_giveaway",
    "has_food_deal",
    "has_ticket_deal",
    "has_theme_night",
    "has_kids_event",
    "has_heritage",
    "has_community",
    "has_entertain",
    "has_dog",
    "has_celebrity",
    "has_recurring",
]

FLAG_NAMES = {
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

# Color constants shared across charts
GREEN = "#2ecc71"
RED   = "#e74c3c"


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
def load_promo_games(game_types: tuple = ("R",)) -> pd.DataFrame:
    """
    Return one row per home game, with game-level promotion flags aggregated
    via BOOL_OR in SQL.  The LEFT JOINs mean that games with zero promotions
    in the database still appear (all flags = FALSE, enriched_count = 0).
    """
    return query_df(f"""
        SELECT
            g.game_pk,
            g.home_team_id            AS team_id,
            g.game_date::date         AS game_date,
            g.season,
            g.attendance,
            g.sport_id,
            g.day_night,
            -- Enriched promo flags aggregated per game
            COALESCE(e.has_fireworks,   FALSE) AS has_fireworks,
            COALESCE(e.has_giveaway,    FALSE) AS has_giveaway,
            COALESCE(e.has_food_deal,   FALSE) AS has_food_deal,
            COALESCE(e.has_ticket_deal, FALSE) AS has_ticket_deal,
            COALESCE(e.has_theme_night, FALSE) AS has_theme_night,
            COALESCE(e.has_kids_event,  FALSE) AS has_kids_event,
            COALESCE(e.has_heritage,    FALSE) AS has_heritage,
            COALESCE(e.has_community,   FALSE) AS has_community,
            COALESCE(e.has_entertain,   FALSE) AS has_entertain,
            COALESCE(e.has_dog,         FALSE) AS has_dog,
            COALESCE(e.has_celebrity,   FALSE) AS has_celebrity,
            COALESCE(e.has_recurring,   FALSE) AS has_recurring,
            e.giveaway_limit,
            COALESCE(e.enriched_count, 0)      AS enriched_count,
            COALESCE(total.total_count, 0)     AS total_promo_count
        FROM milb.games g
        LEFT JOIN (
            SELECT game_pk,
                   BOOL_OR(is_fireworks)      AS has_fireworks,
                   BOOL_OR(is_giveaway_item)  AS has_giveaway,
                   BOOL_OR(is_food_deal)      AS has_food_deal,
                   BOOL_OR(is_ticket_deal)    AS has_ticket_deal,
                   BOOL_OR(is_theme_night)    AS has_theme_night,
                   BOOL_OR(is_kids_event)     AS has_kids_event,
                   BOOL_OR(is_heritage_night) AS has_heritage,
                   BOOL_OR(is_community_event)AS has_community,
                   BOOL_OR(is_entertainment)  AS has_entertain,
                   BOOL_OR(is_dog_friendly)   AS has_dog,
                   BOOL_OR(has_celebrity)     AS has_celebrity,
                   BOOL_OR(is_recurring)      AS has_recurring,
                   MAX(giveaway_limit)        AS giveaway_limit,
                   COUNT(*)                   AS enriched_count
            FROM milb.game_promotions
            WHERE enrichment_method IS NOT NULL
            GROUP BY game_pk
        ) e ON g.game_pk = e.game_pk
        LEFT JOIN (
            SELECT game_pk, COUNT(*) AS total_count
            FROM milb.game_promotions GROUP BY game_pk
        ) total ON g.game_pk = total.game_pk
        WHERE g.abstract_game_state = 'Final'
          AND {game_type_sql(game_types, col="g.game_type")}
          AND g.sport_id IN (11,12,13,14)
          AND g.attendance IS NOT NULL AND g.attendance > 0
    """)


@st.cache_data(ttl=600)
def load_promo_cluster_info() -> pd.DataFrame:
    """Team cluster assignment + cluster description, for the context banner."""
    return query_df("""
        SELECT c.team_id,
               c.promo_cluster_label,
               d.description       AS cluster_description,
               d.key_traits
        FROM milb.team_promo_clusters c
        LEFT JOIN milb.promo_cluster_descriptions d
            ON c.promo_cluster_id = d.promo_cluster_id
    """)


@st.cache_data(ttl=600)
def load_top_giveaways(game_types: tuple = ("R",)) -> pd.DataFrame:
    """
    Named giveaway promotions ranked by average attendance.
    Requires >= 3 occurrences so one-off novelties don't dominate.
    """
    return query_df(f"""
        SELECT p.offer_name,
               p.giveaway_limit,
               AVG(g.attendance) AS avg_att,
               COUNT(*)          AS n
        FROM milb.game_promotions p
        JOIN milb.games g ON p.game_pk = g.game_pk
        WHERE p.is_giveaway_item = TRUE
          AND p.enrichment_method IS NOT NULL
          AND g.abstract_game_state = 'Final'
          AND {game_type_sql(game_types, col="g.game_type")}
          AND g.attendance IS NOT NULL AND g.attendance > 0
        GROUP BY p.offer_name, p.giveaway_limit
        HAVING COUNT(*) >= 3
        ORDER BY avg_att DESC
        LIMIT 20
    """)


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
    excluded_promos = promo_exclude_filter(key="promos_page_exclude")
    if excluded_promos:
        _excluded_labels = ", ".join(PROMO_CATEGORIES[f] for f in excluded_promos)
        st.caption(f"Excluding: {_excluded_labels}")
    else:
        st.caption(
            "Tip: exclude categories you already know work (e.g. Fireworks) "
            "to surface less-obvious opportunities."
        )

    st.divider()
    st.caption(
        "Promotions are enriched by an LLM that classifies each offer name "
        "into boolean flags (fireworks, giveaway, food deal, etc.). "
        "Only enriched promotions are used in lift calculations."
    )


# Promo flags that should be included in charts after the exclude filter.
# Fireworks stays in by default -- users opt in to exclusion when digging.
active_flags = [c for c in FLAG_COLS if c not in excluded_promos]
active_flag_names = [FLAG_NAMES[c] for c in active_flags]

# ── Load and filter ───────────────────────────────────────────────────────────
df = load_promo_games(game_types=selected_game_types)

level_ids = set(level_teams["team_id"])
df = df[df["team_id"].isin(level_ids)].copy()

if selected_team_name != "— All teams —":
    team_id = int(teams_df.loc[teams_df["team_name"] == selected_team_name, "team_id"].iloc[0])
    df = df[df["team_id"] == team_id].copy()

# ── Compute lift = attendance above the team-season baseline ──────────────────
# groupby(...).transform("mean") broadcasts the group mean back to every row,
# so subtraction works element-wise without a merge step.
# This controls for park size: a +400 lift at a 3,000-seat A-ball park is just
# as meaningful as a +400 lift at a 10,000-seat Triple-A park.
baseline = df.groupby(["team_id", "season"])["attendance"].transform("mean")
df["lift"] = df["attendance"] - baseline

# ── Classify each game by promotion status ────────────────────────────────────
# Three buckets based on what the database knows about a game's promotions:
#   "No Promotion"              -> game_pk never appeared in game_promotions
#   "Has Promotion (enriched)"  -> at least one promo row has been LLM-classified
#   "Has Promotion (pending)"   -> promo rows exist but none are enriched yet
def classify_game(row):
    if row["total_promo_count"] == 0:
        return "No Promotion"
    elif row["enriched_count"] > 0:
        return "Has Promotion (enriched)"
    else:
        return "Has Promotion (pending)"

df["promo_status"] = df.apply(classify_game, axis=1)

# ── Page header ───────────────────────────────────────────────────────────────
scope = selected_team_name if selected_team_name != "— All teams —" else "All selected teams"
st.title("📣 Promotions & Attendance Lift")
st.caption(f"Showing {len(df):,} games · {scope}")

# ── Promo strategy cluster context ───────────────────────────────────────────
if selected_team_name != "— All teams —":
    _cluster_info = load_promo_cluster_info()
    _team_cluster = _cluster_info[_cluster_info["team_id"] == team_id]
    if not _team_cluster.empty:
        _row = _team_cluster.iloc[0]
        _label = _row["promo_cluster_label"]
        _desc = _row.get("cluster_description") or ""
        _traits = _row.get("key_traits") or ""
        _banner = f"**Promo strategy cluster: {_label}**"
        if _desc:
            _banner += f"  \n{_desc}"
        if _traits:
            _banner += f"  \n*Key traits: {_traits}*"
        st.info(_banner)

if df.empty:
    st.warning("No game data for the selected filters.")
    st.stop()

# ── Metric row ────────────────────────────────────────────────────────────────
enriched_df = df[df["promo_status"] == "Has Promotion (enriched)"].copy()
no_promo_df = df[df["promo_status"] == "No Promotion"].copy()

# Most common promo type: count games where each flag is TRUE, pick the max
flag_counts  = {FLAG_NAMES[c]: int(enriched_df[c].sum()) for c in active_flags if c in enriched_df.columns}
most_common  = max(flag_counts, key=flag_counts.get) if flag_counts and max(flag_counts.values()) > 0 else "—"

avg_lift_promo    = enriched_df["lift"].mean() if not enriched_df.empty else 0.0
avg_lift_no_promo = no_promo_df["lift"].mean()  if not no_promo_df.empty  else 0.0
lift_delta        = avg_lift_promo - avg_lift_no_promo

c1, c2, c3, c4 = st.columns(4)
c1.metric("Games with enriched promos", f"{len(enriched_df):,}")
c2.metric("Games with no promo",        f"{len(no_promo_df):,}")
c3.metric(
    "Avg lift: promo vs no-promo",
    f"{lift_delta:+,.0f} fans",
    help="Positive = promo games draw more fans than the team's seasonal average",
)
c4.metric("Most common promo type", most_common)

# Flag pending LLM enrichment so game counts appear consistent
_pending_n = int((df["promo_status"] == "Has Promotion (pending)").sum())
if _pending_n:
    st.caption(
        f"{_pending_n:,} games have promos that haven't been LLM-classified yet; "
        f"they're excluded from promo-type charts below. "
        f"(Run `scripts/enrich_promotions.py` to process them.)"
    )

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
tab_compare, tab_stack, tab_giveaway, tab_heat = st.tabs(
    ["📣 Promo vs No-Promo", "📦 Stacking", "🎁 Giveaways", "🗺️ Heatmap"]
)


# ────────────────────────────────────────────────────────────────────────────
# TAB 1: PROMO vs NO-PROMO
# ────────────────────────────────────────────────────────────────────────────
with tab_compare:
    st.subheader("Avg attendance: promoted vs non-promoted games")
    st.caption(
        "Only 'No Promotion' and 'Has Promotion (enriched)' are compared. "
        "Pending games (promos recorded but not yet LLM-classified) are excluded."
    )

    # ── Bar chart: avg attendance per status group ────────────────────────────
    compare_df = df[df["promo_status"].isin(["No Promotion", "Has Promotion (enriched)"])].copy()
    att_agg = (
        compare_df
        .groupby("promo_status")["attendance"]
        .agg(avg="mean", std="std", n="count")
        .reset_index()
    )
    att_agg["avg"] = att_agg["avg"].round(0)
    att_agg["std"] = att_agg["std"].round(0).fillna(0)

    # Assign a bar color: green for the promoted group, grey for no-promo.
    # color_discrete_map="identity" tells Plotly to use the hex string in the
    # "bar_color" column directly, rather than treating it as a category label.
    att_agg["bar_color"] = att_agg["promo_status"].map({
        "Has Promotion (enriched)": GREEN,
        "No Promotion":             "#95a5a6",
    })

    fig_att = px.bar(
        att_agg,
        x="promo_status",
        y="avg",
        error_y="std",
        text="n",
        color="bar_color",
        color_discrete_map="identity",
        labels={
            "promo_status": "Promotion Status",
            "avg":          "Avg Attendance",
            "n":            "Games",
        },
        height=380,
    )
    fig_att.update_traces(textposition="outside", texttemplate="%{text} games")
    fig_att.update_layout(showlegend=False, margin={"t": 30, "b": 20})
    st.plotly_chart(fig_att, use_container_width=True)

    # ── Box plot: attendance distribution per group ───────────────────────────
    st.subheader("Attendance distribution (box plot)")
    st.caption(
        "The box spans the 25th-75th percentile. The line inside is the median. "
        "Whiskers extend to 1.5x IQR; dots beyond are outliers."
    )

    # px.box — a box-and-whisker chart. points=False hides individual dots
    # (too noisy at this scale). color_discrete_map matches the bar chart above.
    fig_box = px.box(
        compare_df,
        x="promo_status",
        y="attendance",
        color="promo_status",
        points=False,
        color_discrete_map={
            "Has Promotion (enriched)": GREEN,
            "No Promotion":             "#95a5a6",
        },
        labels={"promo_status": "", "attendance": "Attendance"},
        height=360,
    )
    fig_box.update_layout(showlegend=False, margin={"t": 10, "b": 20})
    st.plotly_chart(fig_box, use_container_width=True)

    # ── Bar chart: avg lift per promo TYPE ────────────────────────────────────
    st.subheader("Avg attendance lift by promotion type")
    st.caption(
        "Lift = average attendance above this team's seasonal baseline on games "
        "with that promotion. Only types with >= 20 games shown. "
        "Green bars = positive lift (drew more fans than usual); "
        "red bars = negative (possibly correlated with weaker scheduling)."
    )

    # For each flag column, filter to games where that flag is TRUE and
    # compute the mean lift.  This produces one summary row per promo type.
    lift_rows = []
    for col in active_flags:
        subset = enriched_df[enriched_df[col] == True]  # noqa: E712
        if len(subset) >= 20:
            lift_rows.append({
                "promo_type": FLAG_NAMES[col],
                "avg_lift":   subset["lift"].mean(),
                "std_lift":   subset["lift"].std(),
                "n":          len(subset),
            })

    if lift_rows:
        lift_type_df = (
            pd.DataFrame(lift_rows)
            .sort_values("avg_lift", ascending=False)
        )
        lift_type_df["avg_lift"] = lift_type_df["avg_lift"].round(0)
        lift_type_df["std_lift"] = lift_type_df["std_lift"].round(0).fillna(0)
        # Pre-compute color so we can use color_discrete_map="identity"
        lift_type_df["bar_color"] = lift_type_df["avg_lift"].apply(
            lambda v: GREEN if v >= 0 else RED
        )

        fig_lift = px.bar(
            lift_type_df,
            x="promo_type",
            y="avg_lift",
            error_y="std_lift",
            text="n",
            color="bar_color",
            color_discrete_map="identity",
            labels={
                "promo_type": "Promotion Type",
                "avg_lift":   "Avg Lift (fans above baseline)",
                "n":          "Games",
            },
            height=420,
        )
        fig_lift.update_traces(textposition="outside", texttemplate="%{text} games")
        fig_lift.update_layout(
            showlegend=False,
            xaxis_tickangle=-30,
            margin={"t": 30, "b": 60},
        )
        st.plotly_chart(fig_lift, use_container_width=True)
    else:
        st.info("Not enough games with enriched promotions to show per-type lift (need >= 20 per type).")


# ────────────────────────────────────────────────────────────────────────────
# TAB 2: STACKING
# ────────────────────────────────────────────────────────────────────────────
with tab_stack:
    st.subheader("Do more promotions on one night compound each other?")
    st.caption(
        "Each game is scored by how many distinct promotion types it has "
        "(fireworks + giveaway = 2). The chart shows whether stacking "
        "promotions delivers increasing returns or diminishing ones."
    )

    # ── Count active flags per game ───────────────────────────────────────────
    # .sum(axis=1) on boolean columns counts how many are TRUE for each row.
    enriched_df["promo_count"] = enriched_df[active_flags].sum(axis=1)

    # Bucket 3+ together so the chart stays readable.
    # pd.cut() divides a continuous column into labeled intervals.
    # right=True means each bin is (left, right].
    enriched_df["promo_bucket"] = pd.cut(
        enriched_df["promo_count"],
        bins=[-1, 0, 1, 2, 999],
        labels=["0", "1", "2", "3+"],
    )

    stack_agg = (
        enriched_df
        .groupby("promo_bucket", observed=True)["lift"]
        .agg(avg_lift="mean", std_lift="std", n="count")
        .reset_index()
    )
    stack_agg["avg_lift"] = stack_agg["avg_lift"].round(0)
    stack_agg["std_lift"] = stack_agg["std_lift"].round(0).fillna(0)
    stack_agg["bar_color"] = stack_agg["avg_lift"].apply(
        lambda v: GREEN if v >= 0 else RED
    )

    fig_stack = px.bar(
        stack_agg,
        x="promo_bucket",
        y="avg_lift",
        error_y="std_lift",
        text="n",
        color="bar_color",
        color_discrete_map="identity",
        labels={
            "promo_bucket": "Number of Promotion Types",
            "avg_lift":     "Avg Lift (fans above baseline)",
            "n":            "Games",
        },
        height=380,
    )
    fig_stack.update_traces(textposition="outside", texttemplate="%{text} games")
    fig_stack.update_layout(showlegend=False, margin={"t": 30, "b": 20})
    st.plotly_chart(fig_stack, use_container_width=True)

    # ── Most common + highest-lift promotion pairings ─────────────────────────
    st.subheader("Top promotion combinations (games with 2+ types)")
    st.caption(
        "Multi-hot to combinations pattern: for each game, collect the names of "
        "all TRUE flag columns and join them into a label like 'Fireworks + Giveaway'. "
        "sorted() ensures column order doesn't create duplicate combo labels. "
        "Groups with >= 5 games are shown, sorted by avg lift."
    )

    # Filter to games that have at least 2 promotion types active.
    # This is the "multi-hot to combinations" pattern:
    #   1. Filter rows where sum(flags) >= 2
    #   2. For each row, collect the human-readable names of TRUE flags
    #   3. Sort and join them so order doesn't create duplicates
    #   4. GroupBy the resulting label to aggregate lift stats
    multi = enriched_df[enriched_df["promo_count"] >= 2].copy()

    if len(multi) < 5:
        st.info("Not enough games with 2+ promotion types to show combination analysis.")
    else:
        def combo_label(row):
            active = [FLAG_NAMES[c] for c in active_flags if row[c]]
            # sorted() ensures "Fireworks + Giveaway" and "Giveaway + Fireworks"
            # collapse to the same string regardless of column order.
            return " + ".join(sorted(active))

        multi["combo"] = multi.apply(combo_label, axis=1)

        combo_agg = (
            multi
            .groupby("combo")
            .agg(n=("lift", "count"), avg_lift=("lift", "mean"))
            .reset_index()
        )
        # Require at least 5 games per combo to avoid noise from one-off events
        combo_agg = (
            combo_agg[combo_agg["n"] >= 5]
            .sort_values("avg_lift", ascending=False)
            .head(15)
        )
        combo_agg["avg_lift"] = combo_agg["avg_lift"].round(0)
        combo_agg["bar_color"] = combo_agg["avg_lift"].apply(
            lambda v: GREEN if v >= 0 else RED
        )

        if combo_agg.empty:
            st.info("No promotion combinations with >= 5 games found.")
        else:
            fig_combo = px.bar(
                combo_agg,
                x="avg_lift",
                y="combo",
                orientation="h",
                text="n",
                color="bar_color",
                color_discrete_map="identity",
                labels={
                    "combo":    "Promotion Combination",
                    "avg_lift": "Avg Lift (fans above baseline)",
                    "n":        "Games",
                },
                height=max(350, len(combo_agg) * 30),
            )
            fig_combo.update_traces(textposition="outside", texttemplate="%{text} games")
            fig_combo.update_layout(
                showlegend=False,
                yaxis={"categoryorder": "total ascending"},
                margin={"t": 20, "b": 20, "r": 80},
            )
            st.plotly_chart(fig_combo, use_container_width=True)


# ────────────────────────────────────────────────────────────────────────────
# TAB 3: GIVEAWAYS
# ────────────────────────────────────────────────────────────────────────────
with tab_giveaway:
    st.subheader("Giveaway promotions deep dive")

    giveaway_df = enriched_df[enriched_df["has_giveaway"] == True].copy()  # noqa: E712

    if giveaway_df.empty:
        st.warning("No giveaway games found for the selected filters.")
        st.stop()

    st.caption(
        f"{len(giveaway_df):,} giveaway games · "
        f"avg lift {giveaway_df['lift'].mean():+,.0f} fans vs. team-season baseline"
    )

    # ── Limited vs unlimited giveaway analysis ───────────────────────────────
    st.subheader("Limited vs unlimited giveaways")
    st.caption(
        "A limited giveaway (e.g. 'first 1,000 fans') creates scarcity — "
        "does that drive more attendance than unlimited giveaways?"
    )

    giveaway_df["limit_group"] = giveaway_df["giveaway_limit"].apply(
        lambda v: "Limited" if pd.notna(v) else "Unlimited"
    )

    limit_agg = (
        giveaway_df
        .groupby("limit_group")["lift"]
        .agg(avg_lift="mean", std_lift="std", n="count")
        .reset_index()
    )
    limit_agg["avg_lift"] = limit_agg["avg_lift"].round(0)
    limit_agg["std_lift"] = limit_agg["std_lift"].round(0).fillna(0)
    limit_agg["bar_color"] = limit_agg["avg_lift"].apply(
        lambda v: GREEN if v >= 0 else RED
    )

    fig_limit = px.bar(
        limit_agg,
        x="limit_group",
        y="avg_lift",
        error_y="std_lift",
        text="n",
        color="bar_color",
        color_discrete_map="identity",
        labels={
            "limit_group": "Giveaway Type",
            "avg_lift":    "Avg Lift (fans above baseline)",
            "n":           "Games",
        },
        height=340,
    )
    fig_limit.update_traces(textposition="outside", texttemplate="%{text} games")
    fig_limit.update_layout(showlegend=False, margin={"t": 30, "b": 20})
    st.plotly_chart(fig_limit, use_container_width=True)

    # ── Scatter: giveaway limit quantity vs lift ──────────────────────────────
    # Does a smaller cap (e.g. "first 500 fans") create more urgency and
    # therefore higher attendance than a generous cap (e.g. "first 3,000 fans")?
    limited_subset = giveaway_df[giveaway_df["giveaway_limit"].notna()].copy()
    if len(limited_subset) >= 10:
        st.subheader("Does a smaller limit create more urgency?")
        st.caption(
            "Each point is one game. The x-axis is the cap (e.g. 1000 = "
            "'first 1,000 fans get the item'). A downward slope would suggest "
            "scarcity drives attendance; an upward slope the opposite."
        )

        # trendline="ols" overlays an Ordinary Least Squares regression line,
        # which makes direction and magnitude of correlation immediately visible.
        fig_scatter = px.scatter(
            limited_subset,
            x="giveaway_limit",
            y="lift",
            opacity=0.5,
            trendline="ols",
            labels={
                "giveaway_limit": "Giveaway Limit (# fans)",
                "lift":           "Attendance Lift",
            },
            color_discrete_sequence=[GREEN],
            height=360,
        )
        fig_scatter.update_traces(marker_size=5)
        fig_scatter.update_layout(margin={"t": 10, "b": 20})
        st.plotly_chart(fig_scatter, use_container_width=True)
    else:
        st.info("Not enough games with a specified giveaway limit to show the scatter plot.")

    # ── Giveaways by day of week ──────────────────────────────────────────────
    st.subheader("Giveaway games by day of week")
    st.caption(
        "Are giveaways concentrated on weekends (when kids attend) "
        "or spread evenly through the week?"
    )

    # pd.to_datetime handles date strings; dt.day_name() returns "Monday" etc.
    giveaway_df["game_date"] = pd.to_datetime(giveaway_df["game_date"])
    giveaway_df["dow"] = giveaway_df["game_date"].dt.day_name()

    DOW_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    dow_agg = (
        giveaway_df
        .groupby("dow")["lift"]
        .agg(n="count", avg_lift="mean")
        .reset_index()
    )
    dow_agg["bar_color"] = dow_agg["avg_lift"].apply(lambda v: GREEN if v >= 0 else RED)

    fig_dow = px.bar(
        dow_agg,
        x="dow",
        y="n",
        text="n",
        color="bar_color",
        color_discrete_map="identity",
        category_orders={"dow": DOW_ORDER},
        labels={"dow": "Day of Week", "n": "# Giveaway Games"},
        height=340,
    )
    fig_dow.update_traces(textposition="outside", texttemplate="%{text}")
    fig_dow.update_layout(showlegend=False, margin={"t": 30, "b": 20})
    st.plotly_chart(fig_dow, use_container_width=True)

    # ── Top named giveaway promotions ─────────────────────────────────────────
    st.subheader("Top giveaway promotions by avg attendance")
    st.caption(
        "Individual offer names from the database (e.g. 'Bobblehead Night', "
        "'Jersey Giveaway'). Requires >= 3 occurrences. Sorted by avg attendance."
    )

    top_giveaways = load_top_giveaways(game_types=selected_game_types)

    if top_giveaways.empty:
        st.info("No named giveaway promotions with >= 3 occurrences found.")
    else:
        top_giveaways["avg_att"] = top_giveaways["avg_att"].round(0)
        top_giveaways["limit_label"] = top_giveaways["giveaway_limit"].apply(
            lambda v: f"First {int(v):,}" if pd.notna(v) else "Unlimited"
        )

        fig_top = px.bar(
            top_giveaways,
            x="avg_att",
            y="offer_name",
            orientation="h",
            text="n",
            color_discrete_sequence=[GREEN],
            labels={
                "offer_name": "Promotion Name",
                "avg_att":    "Avg Attendance",
                "n":          "Occurrences",
            },
            height=max(350, len(top_giveaways) * 28),
        )
        # "x" after the count makes it read naturally: "23x" = "23 occurrences"
        fig_top.update_traces(textposition="outside", texttemplate="%{text}x")
        fig_top.update_layout(
            yaxis={"categoryorder": "total ascending"},
            margin={"t": 20, "b": 20, "r": 80},
        )
        st.plotly_chart(fig_top, use_container_width=True)

        # Show a compact table so the user can read the full offer names
        st.dataframe(
            top_giveaways[["offer_name", "limit_label", "avg_att", "n"]]
            .rename(columns={
                "offer_name":  "Promotion Name",
                "limit_label": "Limit",
                "avg_att":     "Avg Attendance",
                "n":           "# Games",
            }),
            use_container_width=True,
            hide_index=True,
        )


# ────────────────────────────────────────────────────────────────────────────
# TAB 4: HEATMAP
# ────────────────────────────────────────────────────────────────────────────
with tab_heat:
    st.subheader("Promotion effectiveness heatmap")
    st.caption(
        "Average attendance lift for each promotion type × day-of-week (or month) combination. "
        "Green = above team baseline; red = below. "
        "Cells with fewer than 5 games are greyed out — not enough data."
    )

    if enriched_df.empty:
        st.info("No enriched promotion data for the selected filters.")
        st.stop()

    # ── Build long-form data via pd.melt ──────────────────────────────────────
    # pd.melt() is the inverse of pivot_table: it converts multiple columns into rows.
    # FROM: game_pk | has_fireworks | has_giveaway | lift | dow …
    # TO:   game_pk | promo_col     | active        | lift | dow …
    # This lets us groupby(promo_type, dow) without a separate loop per flag.
    hm_df = enriched_df.copy()
    hm_df["game_date"]  = pd.to_datetime(hm_df["game_date"])
    hm_df["dow"]        = hm_df["game_date"].dt.day_name()
    hm_df["month"]      = hm_df["game_date"].dt.month
    hm_df["month_abbr"] = hm_df["month"].map(MONTH_ABBR)
    # Week of season: ISO week minus the team-season's first week, per team.
    # Keeps weeks comparable across seasons and levels (1 = opening week).
    hm_df["iso_week"]   = hm_df["game_date"].dt.isocalendar().week.astype(int)
    first_week = hm_df.groupby("season")["iso_week"].transform("min")
    hm_df["season_week"] = (hm_df["iso_week"] - first_week + 1).clip(lower=1, upper=26)
    hm_df["season_week_label"] = hm_df["season_week"].apply(lambda w: f"W{int(w):02d}")

    melted = hm_df.melt(
        id_vars=["game_pk", "dow", "month_abbr", "season_week_label", "lift"],
        value_vars=active_flags,
        var_name="promo_col",
        value_name="active",
    )
    # Keep only rows where this promo type was active for the game
    melted = melted[melted["active"]].copy()
    melted["promo_type"] = melted["promo_col"].map(FLAG_NAMES)

    if melted.empty:
        st.info("No enriched promotion rows after filtering.")
        st.stop()

    # ── X-axis selector ───────────────────────────────────────────────────────
    hm_axis = st.radio("X-axis", ["Day of week", "Month", "Week of season"], horizontal=True)

    if hm_axis == "Day of week":
        x_col   = "dow"
        x_order = [d for d in DOW_ORDER if d in melted[x_col].unique()]
    elif hm_axis == "Month":
        x_col   = "month_abbr"
        x_order = [MONTH_ABBR[m] for m in range(4, 10) if MONTH_ABBR[m] in melted[x_col].unique()]
    else:
        x_col   = "season_week_label"
        x_order = sorted(melted[x_col].unique())

    # ── Aggregate: avg lift and game count per (promo_type, x_col) ───────────
    heat_agg = (
        melted.groupby(["promo_type", x_col])["lift"]
        .agg(avg_lift="mean", n="count")
        .reset_index()
    )

    # Mask cells with < 5 games — they're too noisy to be meaningful
    heat_agg.loc[heat_agg["n"] < 5, "avg_lift"] = None

    # Pivot to matrix: rows = promo type, cols = day/month
    heat_pivot = (
        heat_agg
        .pivot(index="promo_type", columns=x_col, values="avg_lift")
        .reindex(columns=x_order)   # enforce calendar column order
    )

    # Sort rows by mean lift (best promos at the top)
    heat_pivot = heat_pivot.loc[
        heat_pivot.mean(axis=1).sort_values(ascending=False).index
    ]

    # Count pivot for the companion chart
    count_pivot = (
        heat_agg
        .pivot(index="promo_type", columns=x_col, values="n")
        .reindex(columns=x_order)
        .reindex(index=heat_pivot.index)   # same row order as lift pivot
        .fillna(0)
        .astype(int)
    )

    # ── px.imshow — lift heatmap ──────────────────────────────────────────────
    # px.imshow() renders a 2-D DataFrame as a colour grid.
    # text_auto=".0f"            → show lift as integers inside each cell
    # color_continuous_midpoint=0 → centres the diverging RdYlGn scale at zero,
    #   so cells exactly at baseline appear yellow, positives green, negatives red.
    # NaN cells render as a neutral grey (Plotly's default for missing data).
    fig_heat = px.imshow(
        heat_pivot,
        color_continuous_scale="RdYlGn",
        color_continuous_midpoint=0,
        text_auto=".0f",
        labels={"color": "Avg Lift", "x": "", "y": ""},
        aspect="auto",
        height=max(420, len(heat_pivot) * 46),
    )
    fig_heat.update_traces(textfont_size=12)
    fig_heat.update_layout(
        coloraxis_colorbar_title_text="Avg Lift",
        margin={"t": 20, "b": 10, "l": 130},
        xaxis_tickangle=0,
    )
    st.plotly_chart(fig_heat, use_container_width=True)

    # ── Companion count grid ──────────────────────────────────────────────────
    with st.expander("Show game counts per cell"):
        st.caption("Number of games behind each lift estimate. Cells with < 5 were hidden above.")
        fig_count = px.imshow(
            count_pivot,
            color_continuous_scale="Blues",
            text_auto=True,
            labels={"color": "Games"},
            aspect="auto",
            height=max(420, len(count_pivot) * 46),
        )
        fig_count.update_traces(textfont_size=11)
        fig_count.update_layout(margin={"t": 10, "b": 10, "l": 130}, xaxis_tickangle=0)
        st.plotly_chart(fig_count, use_container_width=True)

    # ── Opportunity Finder: best promo per day ────────────────────────────────
    st.divider()
    st.subheader("Opportunity Finder: what to run on which day")
    st.caption(
        "For each "
        + ("day of the week" if hm_axis == "Day of week" else "month")
        + ", the top-3 highest-lift promos (with >= 5 games of evidence). "
        "Use the sidebar exclude filter to hide promos you already know work "
        "(e.g. Fireworks) -- this surface is about the *next* lever to pull."
    )

    # Re-aggregate in day-first form so we can rank promos within each day/month.
    opp_rows = (
        heat_agg.dropna(subset=["avg_lift"])
        .sort_values(["avg_lift"], ascending=False)
        .groupby(x_col)
        .head(3)
        .sort_values([x_col, "avg_lift"], ascending=[True, False])
    )

    if opp_rows.empty:
        st.info(
            "Not enough data to build the opportunity table -- need at least 5 games "
            "per (promo, day) combination."
        )
    else:
        # Build one row per day with the top 3 promos spelled out
        dow_blocks = []
        for day in x_order:
            day_df = opp_rows[opp_rows[x_col] == day]
            if day_df.empty:
                continue
            picks = [
                f"**{int(r['avg_lift']):+,}** {r['promo_type']} ({int(r['n'])})"
                for _, r in day_df.iterrows()
            ]
            dow_blocks.append({"Day": day, "Top promos (lift / games)": "  ·  ".join(picks)})

        opp_table = pd.DataFrame(dow_blocks)
        st.dataframe(opp_table, use_container_width=True, hide_index=True)

        # Also a compact bar chart: best lift per day across all active promos
        best_per_day = (
            heat_agg.dropna(subset=["avg_lift"])
            .sort_values("avg_lift", ascending=False)
            .groupby(x_col).head(1)
        )
        best_per_day = best_per_day.set_index(x_col).reindex(x_order).reset_index()
        best_per_day["label"] = best_per_day["promo_type"].fillna("--")

        fig_best = px.bar(
            best_per_day,
            x=x_col,
            y="avg_lift",
            color="avg_lift",
            color_continuous_scale=DIVERGING,
            color_continuous_midpoint=0,
            text="label",
            labels={x_col: "", "avg_lift": "Best Promo Lift"},
            category_orders={x_col: x_order},
            height=320,
        )
        fig_best.update_traces(textposition="outside", textfont_size=11)
        fig_best.update_layout(
            coloraxis_showscale=False,
            margin={"t": 20, "b": 20},
        )
        st.plotly_chart(fig_best, use_container_width=True)


# ── Cross-page navigation + footer ───────────────────────────────────────────
see_also([
    ("Promo Strategy",   "pages/7_Promo_Strategy.py",     "which archetype each team fits"),
    ("Recommendations",  "pages/10_Recommendations.py",   "actionable promo suggestions per team"),
    ("Attendance",       "pages/1_Attendance.py",         "underlying per-game attendance"),
])
render_footer(scripts=["promo_lift"])
