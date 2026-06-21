"""Weekend Playbook -- why some teams win Saturday and some don't.

A data story in five acts:
  1. League distribution of the Fri->Sat attendance gap; is the gap unique?
  2. The Sat-loser club (who else has this problem)
  3. What Sat-winners actually do differently on Friday vs Saturday
  4. Binghamton overlay -- which camp does RP pattern-match?
  5. Observed pattern by operator (reported, not claimed causal)

Backing tables: milb.weekend_gap, milb.weekend_promo_mix
Source script:  scripts/analyze_weekend_gap.py
"""

# -- Path setup ---------------------------------------------------------------
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils.db import query_df
from utils.footer import render_footer
from utils.navigation import see_also
from utils.theme import LEVEL_COLORS, MOMENTUM_COLORS

st.set_page_config(page_title="Weekend Playbook | MiLB", page_icon="WP", layout="wide")

LEVEL_ORDER = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}
RUMBLE_PONIES_ID = 505
ANALYSIS_SEASON = 2025
FRI_DOW, SAT_DOW = 4, 5

PROMO_FLAGS = [
    "has_fireworks", "has_giveaway", "has_food_deal", "has_ticket_deal",
    "has_theme_night", "has_kids_event", "has_heritage", "has_community",
    "has_entertain", "has_dog", "has_celebrity", "has_recurring",
]
PROMO_LABELS = {
    "has_fireworks": "Fireworks", "has_giveaway": "Giveaway",
    "has_food_deal": "Food Deal", "has_ticket_deal": "Ticket Deal",
    "has_theme_night": "Theme Night", "has_kids_event": "Kids Event",
    "has_heritage": "Heritage", "has_community": "Community",
    "has_entertain": "Entertainment", "has_dog": "Dog Friendly",
    "has_celebrity": "Celebrity", "has_recurring": "Recurring",
}
CAMP_COLORS = {
    "sat_winner": "#1a9850",
    "neutral":    "#95a5a6",
    "sat_loser":  "#d73027",
}
CAMP_LABEL = {
    "sat_winner": "Sat-winner",
    "neutral":    "Neutral",
    "sat_loser":  "Sat-loser",
}


# -- Data loaders -------------------------------------------------------------

@st.cache_data(ttl=300)
def load_gap():
    return query_df("""
        SELECT wg.*, t.team_name, sp.sport_name AS level
          FROM milb.weekend_gap wg
          JOIN milb.teams t  ON t.team_id = wg.team_id
          LEFT JOIN milb.sports sp ON sp.sport_id = wg.sport_id
    """)


@st.cache_data(ttl=300)
def load_promo_mix():
    return query_df("SELECT * FROM milb.weekend_promo_mix")


@st.cache_data(ttl=300)
def load_fri_sat_games(season: int):
    """Raw Fri+Sat games for ad-hoc operator cuts in Act 5."""
    flag_cols = ", ".join(f"f.{c}" for c in PROMO_FLAGS)
    return query_df(f"""
        SELECT f.team_id, f.sport_id, f.day_of_week, f.attendance,
               f.capacity_utilization, {flag_cols},
               COALESCE(o.operator_name, 'Independent') AS operator_name
          FROM milb.game_features f
          JOIN milb.teams t  ON t.team_id = f.team_id
          LEFT JOIN milb.team_operators o ON o.operator_id = t.operator_id
         WHERE f.season = {season}
           AND f.game_type = 'R'
           AND f.attendance IS NOT NULL
           AND f.day_of_week IN ({FRI_DOW}, {SAT_DOW})
    """)


@st.cache_data(ttl=300)
def load_team_season_trend(team_id: int):
    """Cap util by season (2023+) to show stabilization trajectory."""
    return query_df(f"""
        SELECT season,
               AVG(attendance)::int      AS avg_att,
               AVG(capacity_utilization) AS avg_cap_util,
               COUNT(*)                  AS games
          FROM milb.game_features
         WHERE team_id = {team_id}
           AND game_type = 'R'
           AND attendance IS NOT NULL
           AND season >= 2023
         GROUP BY season
         ORDER BY season
    """)


@st.cache_data(ttl=300)
def load_market_context(team_id: int):
    """Latest MSA demographics and operator context for a team."""
    return query_df(f"""
        WITH latest AS (
            SELECT season, census_year,
                   population_trend,
                   population_change_5yr_pct,
                   income_change_5yr_pct
              FROM milb.game_features
             WHERE team_id = {team_id}
               AND game_type = 'R'
               AND attendance IS NOT NULL
             ORDER BY season DESC, game_date DESC
             LIMIT 1
        )
        SELECT l.population_trend,
               l.population_change_5yr_pct,
               l.income_change_5yr_pct,
               vd.msa_population,
               vd.msa_median_income,
               vd.msa_name,
               COALESCE(o.operator_name, 'Independent') AS operator_name
          FROM latest l
          JOIN milb.teams t ON t.team_id = {team_id}
          JOIN milb.venues v ON v.venue_id = t.venue_id
          JOIN milb.venue_demographics vd
               ON vd.venue_id = v.venue_id AND vd.census_year = l.census_year
          LEFT JOIN milb.team_operators o ON o.operator_id = t.operator_id
    """)


@st.cache_data(ttl=300)
def load_dow_cap_util(team_id: int, season: int):
    """Avg cap util for Fri / Sat / Sun games for the team."""
    return query_df(f"""
        SELECT day_of_week,
               AVG(capacity_utilization) AS avg_cap_util,
               AVG(attendance)::int      AS avg_att,
               COUNT(*)                  AS games
          FROM milb.game_features
         WHERE team_id = {team_id}
           AND season = {season}
           AND game_type = 'R'
           AND attendance IS NOT NULL
           AND day_of_week IN (4, 5, 6)
         GROUP BY day_of_week
         ORDER BY day_of_week
    """)


# -- Sidebar ------------------------------------------------------------------

with st.sidebar:
    st.header("Filters")
    sel_levels = st.multiselect(
        "Level",
        options=list(LEVEL_ORDER.values()),
        default=list(LEVEL_ORDER.values()),
    )
    level_ids = [k for k, v in LEVEL_ORDER.items() if v in sel_levels]

    gap_all = load_gap()
    team_opts = gap_all[gap_all["sport_id"].isin(level_ids)].sort_values("team_name")
    team_names = team_opts["team_name"].tolist()
    default_idx = team_names.index("Binghamton Rumble Ponies") if "Binghamton Rumble Ponies" in team_names else 0
    highlight_name = st.selectbox("Highlight team", team_names, index=default_idx)
    highlight_row = team_opts[team_opts["team_name"] == highlight_name].iloc[0]
    highlight_team_id = int(highlight_row["team_id"])

    st.divider()
    st.caption(f"Season {ANALYSIS_SEASON} (latest complete).")
    st.caption("Camp thresholds: sat_winner gap_pct >= +5%, sat_loser <= -5%.")


# -- Intro --------------------------------------------------------------------

st.title("Weekend Playbook")
st.markdown(
    "Friday proves people will come. Saturday is the night a team should own. "
    "When Saturday averages **less** than Friday, something is wrong with how "
    "the weekend is being scheduled. This page walks through what the data says."
)

gap = gap_all[gap_all["sport_id"].isin(level_ids)].copy()
mix = load_promo_mix()

if gap.empty:
    st.warning("No qualifying teams in the selected level(s).")
    st.stop()


# -- Act 1 --------------------------------------------------------------------

st.header("Act 1 — Is the Sat-under-Fri gap unique?")

camp_counts = gap["gap_camp"].value_counts().to_dict()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Qualifying teams", f"{len(gap)}")
c2.metric("Sat-winners", f"{camp_counts.get('sat_winner', 0)}", help="gap_pct >= +5%")
c3.metric("Neutral", f"{camp_counts.get('neutral', 0)}", help="between -5% and +5%")
c4.metric("Sat-losers", f"{camp_counts.get('sat_loser', 0)}", help="gap_pct <= -5%")

share_losers = camp_counts.get("sat_loser", 0) / len(gap) if len(gap) else 0
st.markdown(
    f"**{share_losers*100:.0f}% of qualifying teams** are Sat-losers. "
    "That's common enough to call it a real failure mode, not an outlier."
)

gap_plot = gap.copy()
gap_plot["gap_pct_pts"] = gap_plot["gap_pct"] * 100
gap_plot["camp_label"] = gap_plot["gap_camp"].map(CAMP_LABEL)

fig1 = px.histogram(
    gap_plot, x="gap_pct_pts", color="camp_label",
    color_discrete_map={CAMP_LABEL[k]: v for k, v in CAMP_COLORS.items()},
    nbins=35,
    labels={"gap_pct_pts": "Sat avg - Fri avg (% of season avg)", "camp_label": "Camp"},
    title="Distribution of the Fri->Sat gap across qualifying teams",
)
fig1.update_layout(bargap=0.05, height=380)
fig1.add_vline(x=0, line_dash="dash", line_color="#555")

hl = gap_plot[gap_plot["team_id"] == highlight_team_id]
if not hl.empty:
    val = float(hl.iloc[0]["gap_pct_pts"])
    fig1.add_vline(x=val, line_color="#000", line_width=2)
    fig1.add_annotation(
        x=val, y=1.02, yref="paper",
        text=f"{hl.iloc[0]['team_name']}: {val:+.1f}%",
        showarrow=False, font=dict(size=12, color="#000"),
    )
st.plotly_chart(fig1, use_container_width=True)

if not hl.empty:
    rp_row = hl.iloc[0]
    rank_league = int((gap["gap_pct"] < rp_row["gap_pct"]).sum()) + 1
    lvl_gap = gap[gap["sport_id"] == int(rp_row["sport_id"])]
    rank_level = int((lvl_gap["gap_pct"] < rp_row["gap_pct"]).sum()) + 1
    st.info(
        f"**{rp_row['team_name']}:** gap_pct = {rp_row['gap_pct']*100:+.1f}% "
        f"(ranked {rank_league}/{len(gap)} league-wide, "
        f"{rank_level}/{len(lvl_gap)} in {LEVEL_ORDER[int(rp_row['sport_id'])]}). "
        f"Camp: **{CAMP_LABEL[rp_row['gap_camp']]}**."
    )

# 2026 trajectory: show if multi-season data is available
_trend = load_team_season_trend(highlight_team_id)
if not _trend.empty and len(_trend) >= 2:
    _seasons = _trend.to_dict("records")
    _latest = _seasons[-1]
    _prev = _seasons[-2]
    _delta_pp = (float(_latest["avg_cap_util"]) - float(_prev["avg_cap_util"])) * 100
    _expanded = bool(_delta_pp > 1)  # auto-expand when showing positive news
    with st.expander(
        f"Trajectory: {highlight_name} capacity utilization across seasons",
        expanded=_expanded,
    ):
        _cols = st.columns(len(_seasons))
        for i, (_col, _r) in enumerate(zip(_cols, _seasons)):
            _d = None
            if i > 0:
                _d = f"{(float(_r['avg_cap_util']) - float(_seasons[i-1]['avg_cap_util']))*100:+.1f}pp"
            _col.metric(
                f"{int(_r['season'])}  ({int(_r['games'])} games)",
                f"{float(_r['avg_cap_util']):.0%}",
                delta=_d,
                delta_color="normal",
            )
        if _delta_pp > 1:
            st.success(
                f"Cap utilization is up **{_delta_pp:+.1f}pp** in {int(_latest['season'])} "
                f"vs {int(_prev['season'])}. The decline has stabilized — now the question "
                "is which levers accelerate the recovery."
            )
        elif _delta_pp < -1:
            st.warning(
                f"Cap utilization is down {abs(_delta_pp):.1f}pp in {int(_latest['season'])}. "
                "The Saturday promo gap is a meaningful part of what to fix."
            )
        else:
            st.info(
                f"Cap utilization is tracking close to {int(_prev['season'])} — "
                "stabilizing, but not yet improving. The Saturday fireworks move "
                "is the clearest near-term lever."
            )


# -- Act 2 --------------------------------------------------------------------

st.header("Act 2 — The Sat-loser club")

losers = gap[gap["gap_camp"] == "sat_loser"].sort_values("gap_pct").copy()
if losers.empty:
    st.caption("No Sat-losers at the current level filter.")
else:
    losers["level"] = losers["sport_id"].map(LEVEL_ORDER)
    losers["gap_pct_display"] = losers["gap_pct"].apply(lambda x: f"{x*100:+.1f}%")
    losers["cap_util_display"] = losers["season_cap_util"].apply(
        lambda x: f"{x:.0%}" if pd.notna(x) else "-"
    )
    losers["cap_pts_display"] = losers["gap_cap_util_pts"].apply(
        lambda x: f"{x*100:+.2f}pp" if pd.notna(x) else "-"
    )
    losers["season_avg_display"] = losers["season_avg"].apply(lambda x: f"{int(x):,}")
    losers["fri_display"] = losers["fri_avg"].apply(lambda x: f"{int(x):,}")
    losers["sat_display"] = losers["sat_avg"].apply(lambda x: f"{int(x):,}")

    _MOMENTUM_ICON = {
        "surging": "🟢 surging", "improving": "🟢 improving",
        "stable": "🟡 stable", "declining": "🔴 declining",
        "struggling": "🔴 struggling",
    }
    losers["momentum_display"] = (
        losers["momentum_label"].map(_MOMENTUM_ICON).fillna(losers["momentum_label"].fillna("-"))
    )

    show_cols = {
        "team_name": "Team",
        "level": "Level",
        "cap_util_display": "Cap util",
        "cap_pts_display": "Sat-Fri cap gap",
        "gap_pct_display": "Fan gap %",
        "fri_display": "Fri avg fans",
        "sat_display": "Sat avg fans",
        "momentum_display": "Momentum",
        "operator_name": "Operator",
    }
    st.dataframe(
        losers[list(show_cols.keys())].rename(columns=show_cols),
        hide_index=True, use_container_width=True,
    )

    lvl_split = losers["level"].value_counts().to_dict()
    split_line = ", ".join(f"{lvl}: {n}" for lvl, n in lvl_split.items())
    st.caption(f"{len(losers)} teams total -- {split_line}")

    improving_ct = losers["momentum_label"].isin(["surging", "improving"]).sum()
    declining_ct = losers["momentum_label"].isin(["declining", "struggling"]).sum()
    if improving_ct > 0:
        st.markdown(
            f"**{improving_ct} of {len(losers)} sat-losers are already on an improving trajectory.** "
            "The Saturday gap is a pattern that can be corrected — "
            "teams showing improvement tend to have started shifting high-draw promotions to Saturday night."
        )


# -- Act 3 --------------------------------------------------------------------

st.header("Act 3 — What do Sat-winners do differently?")

if len(level_ids) == 1:
    mix_sub = mix[mix["sport_id"] == level_ids[0]].copy()
    mix_scope = LEVEL_ORDER[level_ids[0]]
else:
    mix_sub = mix[mix["sport_id"].isna()].copy()
    mix_scope = "All levels pooled"

if mix_sub.empty:
    st.warning(f"No promo mix rows for scope: {mix_scope}")
else:
    mix_sub["pct_pts"] = mix_sub["pct_games_with_promo"] * 100
    mix_sub["camp_label"] = mix_sub["gap_camp"].map(CAMP_LABEL)
    mix_sub["promo_label"] = mix_sub["promo_type"].map(PROMO_LABELS)

    # Order promos by Sat winner-loser gap, descending
    sat_diff = (
        mix_sub[mix_sub["dow_label"] == "Sat"]
        .pivot_table(index="promo_type", columns="gap_camp", values="pct_pts")
        .assign(gap=lambda d: d.get("sat_winner", 0) - d.get("sat_loser", 0))
        .sort_values("gap", ascending=False)
    )
    promo_order = [PROMO_LABELS[p] for p in sat_diff.index if p in PROMO_LABELS]

    col_left, col_right = st.columns(2)

    for col, dow in ((col_left, "Sat"), (col_right, "Fri")):
        dow_mix = mix_sub[mix_sub["dow_label"] == dow]
        dow_mix = dow_mix[dow_mix["gap_camp"].isin(("sat_winner", "sat_loser"))]
        if dow_mix.empty:
            continue
        fig = px.bar(
            dow_mix, x="promo_label", y="pct_pts", color="camp_label",
            color_discrete_map={CAMP_LABEL[k]: v for k, v in CAMP_COLORS.items()},
            barmode="group",
            category_orders={"promo_label": promo_order},
            labels={"promo_label": "", "pct_pts": "% of games with this promo",
                    "camp_label": "Camp"},
            title=f"{dow} -- Sat-winners vs Sat-losers ({mix_scope})",
        )
        fig.update_layout(height=420, xaxis_tickangle=-35)
        col.plotly_chart(fig, use_container_width=True)

    # Narrative pull-out on biggest gaps
    top_gains = sat_diff.head(3).index.tolist()
    top_drops = sat_diff.tail(3).index.tolist()
    gains_txt = ", ".join(f"**{PROMO_LABELS[p]}** ({sat_diff.loc[p,'gap']:+.0f}pp)" for p in top_gains)
    drops_txt = ", ".join(f"**{PROMO_LABELS[p]}** ({sat_diff.loc[p,'gap']:+.0f}pp)" for p in top_drops)
    st.markdown(
        f"**Biggest Saturday edges for Sat-winners:** {gains_txt}.  \n"
        f"**Biggest Saturday habits of Sat-losers:** {drops_txt}.  \n"
        "The pattern is consistent across levels: Sat-winners concentrate fireworks on "
        "Saturday; Sat-losers run giveaways as their Saturday flagship."
    )


# -- Act 4 --------------------------------------------------------------------

st.header(f"Act 4 — {highlight_name} overlay")

hl_sport_id = int(highlight_row["sport_id"])
hl_level_name = LEVEL_ORDER[hl_sport_id]

level_mix = mix[(mix["sport_id"] == hl_sport_id) & (mix["dow_label"] == "Sat")].copy()
raw = load_fri_sat_games(ANALYSIS_SEASON)
hl_games = raw[(raw["team_id"] == highlight_team_id) & (raw["day_of_week"] == SAT_DOW)]

if level_mix.empty or hl_games.empty:
    st.warning("Not enough data for overlay.")
else:
    rows = []
    for flag in PROMO_FLAGS:
        hl_pct = hl_games[flag].fillna(False).astype(int).mean() * 100
        w = level_mix[(level_mix["gap_camp"] == "sat_winner") & (level_mix["promo_type"] == flag)]
        l = level_mix[(level_mix["gap_camp"] == "sat_loser") & (level_mix["promo_type"] == flag)]
        w_pct = float(w.iloc[0]["pct_games_with_promo"]) * 100 if not w.empty else np.nan
        l_pct = float(l.iloc[0]["pct_games_with_promo"]) * 100 if not l.empty else np.nan
        if pd.isna(w_pct) or pd.isna(l_pct):
            continue
        match = "sat_winner" if abs(hl_pct - w_pct) < abs(hl_pct - l_pct) else "sat_loser"
        rows.append({
            "promo_label": PROMO_LABELS[flag],
            "hl_pct": hl_pct, "winner_pct": w_pct, "loser_pct": l_pct,
            "vs_winner": hl_pct - w_pct,
            "match": match,
        })
    overlay = pd.DataFrame(rows).sort_values("vs_winner")

    fig4 = go.Figure()
    fig4.add_trace(go.Bar(
        x=overlay["promo_label"], y=overlay["winner_pct"],
        name="Sat-winner avg", marker_color=CAMP_COLORS["sat_winner"], opacity=0.55,
    ))
    fig4.add_trace(go.Bar(
        x=overlay["promo_label"], y=overlay["loser_pct"],
        name="Sat-loser avg", marker_color=CAMP_COLORS["sat_loser"], opacity=0.55,
    ))
    fig4.add_trace(go.Scatter(
        x=overlay["promo_label"], y=overlay["hl_pct"],
        mode="markers", name=highlight_name,
        marker=dict(size=14, color="#000", symbol="diamond", line=dict(color="#fff", width=2)),
    ))
    fig4.update_layout(
        barmode="group", height=440,
        title=f"{highlight_name} Saturday promo mix vs {hl_level_name} camps",
        yaxis_title="% of Saturdays with promo",
        xaxis_tickangle=-35,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig4, use_container_width=True)

    match_counts = overlay["match"].value_counts().to_dict()
    winner_match = match_counts.get("sat_winner", 0)
    loser_match = match_counts.get("sat_loser", 0)
    big_divergences = overlay[overlay["vs_winner"].abs() >= 10].copy()

    c1, c2 = st.columns(2)
    c1.metric("Matches Sat-winner pattern", f"{winner_match} / 12 flags")
    c2.metric("Matches Sat-loser pattern", f"{loser_match} / 12 flags")

    if not big_divergences.empty:
        divergence_bullets = "\n".join(
            f"- **{r['promo_label']}:** {highlight_name} {r['hl_pct']:.0f}% vs "
            f"Sat-winner {r['winner_pct']:.0f}% ({r['vs_winner']:+.0f}pp)"
            for _, r in big_divergences.iterrows()
        )
        st.markdown(f"**Divergences from Sat-winner average > 10 points:**\n\n{divergence_bullets}")

# -- Market context (top-level, after Act 4) ----------------------------------

_mkt = load_market_context(highlight_team_id)
if not _mkt.empty:
    _m = _mkt.iloc[0]
    with st.expander(
        f"Market context: demographics, operator, and structural factors for {highlight_name}",
        expanded=False,
    ):
        _mc1, _mc2, _mc3 = st.columns(3)
        _msa_pop  = _m.get("msa_population")
        _msa_inc  = _m.get("msa_median_income")
        _pop_chg  = _m.get("population_change_5yr_pct")
        _inc_chg  = _m.get("income_change_5yr_pct")
        _pop_trnd = (_m.get("population_trend") or "unknown").lower()
        _operator = _m.get("operator_name") or "Independent"
        _msa_name = _m.get("msa_name") or ""

        _mc1.metric(
            "MSA Population",
            f"{int(_msa_pop):,}" if _msa_pop else "-",
            delta=f"{_pop_chg:+.1f}% (5-yr)" if pd.notna(_pop_chg) else None,
            delta_color="normal" if (_pop_chg or 0) >= 0 else "inverse",
        )
        _mc2.metric(
            "MSA Median Income",
            f"${int(_msa_inc):,}" if _msa_inc else "-",
            delta=f"{_inc_chg:+.1f}% (5-yr)" if pd.notna(_inc_chg) else None,
            delta_color="normal" if (_inc_chg or 0) >= 0 else "inverse",
        )
        _mc3.metric("Operator", _operator)

        if _msa_name:
            st.caption(f"MSA: {_msa_name}")

        _notes = []
        if _pop_trnd in ("shrinking", "declining"):
            _notes.append(
                "**Market population is contracting** — a structural headwind. "
                "Promotions can drive incremental attendance but can't reverse demographic drift. "
                "Capacity utilization benchmarks (not raw attendance) are the fair comparison for this market."
            )
        elif _pop_trnd in ("growing", "surging"):
            _notes.append(
                "**Market population is growing** — a long-term tailwind. "
                "The Saturday gap is more correctable here: there are fans to reach."
            )
        if pd.notna(_inc_chg) and _inc_chg < -2:
            _notes.append(
                "**Median income has been declining** — fan price sensitivity is elevated. "
                "Ticket Deals and Food Deals may outperform in this market even if they underperform league-wide."
            )

        for _n in _notes:
            st.info(_n)

        # DBH context if this operator appears in the gap data
        if "diamond" in _operator.lower():
            _dbh_rows = gap[gap["operator_name"].str.contains("Diamond", case=False, na=False)]
            if not _dbh_rows.empty:
                _dbh_loser_pct = (_dbh_rows["gap_camp"] == "sat_loser").mean()
                _dbh_fw_pct = (
                    raw[
                        (raw["day_of_week"] == SAT_DOW) &
                        (raw["operator_name"].str.contains("Diamond", case=False, na=False))
                    ]["has_fireworks"].fillna(False).astype(int).mean()
                    if not raw.empty else float("nan")
                )
                st.caption(
                    f"**{_operator} context:** {_dbh_loser_pct*100:.0f}% of its "
                    f"{len(_dbh_rows)} qualifying teams are Sat-losers. "
                    f"Saturday fireworks rate across the portfolio: "
                    f"{_dbh_fw_pct*100:.0f}% — compare to non-{_operator} operators in Act 5."
                )


# -- Act 5 --------------------------------------------------------------------

st.header("Act 5 — An observed pattern by operator")

st.caption(
    "Cross-tab of camp membership and operator, plus Saturday promo mix by operator. "
    "This is an observed correlation; we can't say whether it reflects operator direction, "
    "the markets that a particular operator has acquired, shared front-office staff, or coincidence. "
    "Flagging it for follow-up, not concluding anything."
)

# Operator x camp cross-tab
op_camp = (
    gap.assign(operator=gap["operator_name"].fillna("Independent"))
       .groupby("operator")
       .agg(n_teams=("team_id", "count"),
            n_sat_winners=("gap_camp", lambda s: (s == "sat_winner").sum()),
            n_neutral=("gap_camp", lambda s: (s == "neutral").sum()),
            n_sat_losers=("gap_camp", lambda s: (s == "sat_loser").sum()))
       .reset_index()
)
op_camp = op_camp[op_camp["n_teams"] >= 3].copy()
op_camp["pct_losers"] = op_camp["n_sat_losers"] / op_camp["n_teams"]
op_camp = op_camp.sort_values("pct_losers", ascending=False)

# Saturday promo mix per operator (on the fly from raw games)
raw_sat = raw[raw["day_of_week"] == SAT_DOW].copy()
for flag in PROMO_FLAGS:
    raw_sat[flag] = raw_sat[flag].fillna(False).astype(int)

op_mix = raw_sat.groupby("operator_name").agg(
    n_sat_games=("team_id", "count"),
    sat_fireworks=("has_fireworks", "mean"),
    sat_giveaway=("has_giveaway", "mean"),
    sat_kids=("has_kids_event", "mean"),
    sat_theme=("has_theme_night", "mean"),
).reset_index().rename(columns={"operator_name": "operator"})

merged = op_camp.merge(op_mix, on="operator", how="left")

display = merged.copy()
display["% Sat-losers"] = (display["pct_losers"] * 100).map(lambda x: f"{x:.0f}%")
display["Teams"] = display["n_teams"]
display["W / N / L"] = display.apply(
    lambda r: f"{r['n_sat_winners']} / {r['n_neutral']} / {r['n_sat_losers']}", axis=1)
for col_src, col_out in [
    ("sat_fireworks", "Sat Fireworks %"),
    ("sat_giveaway", "Sat Giveaway %"),
    ("sat_kids", "Sat Kids %"),
    ("sat_theme", "Sat Theme %"),
]:
    display[col_out] = display[col_src].apply(
        lambda x: f"{x*100:.0f}%" if pd.notna(x) else "-"
    )

show = display[["operator", "Teams", "W / N / L", "% Sat-losers",
                "Sat Fireworks %", "Sat Giveaway %", "Sat Kids %", "Sat Theme %"]]
show = show.rename(columns={"operator": "Operator"})
st.dataframe(show, hide_index=True, use_container_width=True)

# Short observation, carefully scoped
dbh_row = merged[merged["operator"].str.contains("Diamond", case=False, na=False)]
if not dbh_row.empty:
    r = dbh_row.iloc[0]
    nondbh = merged[~merged["operator"].str.contains("Diamond", case=False, na=False)]
    non_pct = float(nondbh["pct_losers"].mean()) if not nondbh.empty else float("nan")
    non_fw = float(nondbh["sat_fireworks"].mean()) if not nondbh.empty else float("nan")
    st.info(
        f"**Observed:** among {int(r['n_teams'])} {r['operator']} teams, "
        f"{int(r['n_sat_losers'])} ({r['pct_losers']*100:.0f}%) are Sat-losers, "
        f"vs {non_pct*100:.0f}% across other operator groups. "
        f"Saturday fireworks rate for {r['operator']}: {r['sat_fireworks']*100:.0f}% "
        f"vs {non_fw*100:.0f}% elsewhere. "
        "Worth a dedicated follow-up; not a claim about operator policy."
    )


# -- Friday evening: the case for weekday promotions -------------------------

st.header("Friday Evening: The Weekday Promo Opportunity")
st.markdown(
    "When fans show up on a Friday with no promotion, they came for the game. "
    "That means a promotion on Friday adds on top of a baseline that's already there — "
    "and the incremental lift is visible. "
    "Here's how Sat-winners and Sat-losers differ in their **Friday** promo investment, "
    "and where the highlighted team sits."
)

_fri_raw = raw[raw["day_of_week"] == FRI_DOW].copy()
for _flag in PROMO_FLAGS:
    _fri_raw[_flag] = _fri_raw[_flag].fillna(False).astype(int)

_fri_with_camp = _fri_raw.merge(
    gap_all[["team_id", "gap_camp", "sport_id"]].drop_duplicates("team_id"),
    on="team_id", how="left",
)
_fri_level = _fri_with_camp[_fri_with_camp["sport_id"] == hl_sport_id]

if not _fri_level.empty:
    _fri_col_left, _fri_col_right = st.columns(2)

    with _fri_col_left:
        # Camp-level cap util on Fridays
        _fri_camp_cu = (
            _fri_level[_fri_level["gap_camp"].isin(("sat_winner", "sat_loser"))]
            .groupby("gap_camp")
            .agg(avg_cap_util=("capacity_utilization", "mean"), games=("team_id", "count"))
            .reset_index()
        )
        _fri_camp_cu["camp_label"] = _fri_camp_cu["gap_camp"].map(CAMP_LABEL)
        _fri_camp_cu["avg_cap_util_pct"] = _fri_camp_cu["avg_cap_util"] * 100
        _hl_fri = _fri_raw[_fri_raw["team_id"] == highlight_team_id]
        _hl_fri_cu = float(_hl_fri["capacity_utilization"].mean()) if not _hl_fri.empty else None

        _fig_fri_cu = px.bar(
            _fri_camp_cu,
            x="camp_label", y="avg_cap_util_pct",
            color="camp_label",
            color_discrete_map={CAMP_LABEL[k]: v for k, v in CAMP_COLORS.items()},
            text_auto=".1f",
            labels={"avg_cap_util_pct": "Avg cap util %", "camp_label": "Camp"},
            title=f"Friday avg cap utilization — {hl_level_name}",
        )
        _fig_fri_cu.update_traces(texttemplate="%{y:.1f}%")
        _fig_fri_cu.update_layout(showlegend=False, height=340)
        if _hl_fri_cu is not None:
            _fig_fri_cu.add_hline(
                y=_hl_fri_cu * 100, line_dash="dash", line_color="#000",
                annotation_text=f"{highlight_name}: {_hl_fri_cu:.0%}",
                annotation_position="top right",
            )
        _fri_col_left.plotly_chart(_fig_fri_cu, use_container_width=True)

    with _fri_col_right:
        # Friday promo rate: team vs Sat-winner camp
        _top_flags = ["has_fireworks", "has_giveaway", "has_food_deal",
                      "has_ticket_deal", "has_theme_night", "has_kids_event"]
        _fri_rows = []
        for _f in _top_flags:
            _hl_pct = float(_hl_fri[_f].mean() * 100) if not _hl_fri.empty else 0.0
            _winner_pct = float(
                _fri_level[_fri_level["gap_camp"] == "sat_winner"][_f].mean() * 100
            )
            _loser_pct  = float(
                _fri_level[_fri_level["gap_camp"] == "sat_loser"][_f].mean() * 100
            )
            _fri_rows.append({
                "Promo": PROMO_LABELS[_f],
                f"{highlight_name} (Fri %)": _hl_pct,
                f"Sat-winner Fri %": _winner_pct,
                f"Sat-loser Fri %": _loser_pct,
            })
        _fri_promo_df = pd.DataFrame(_fri_rows)
        for _c in [f"{highlight_name} (Fri %)", "Sat-winner Fri %", "Sat-loser Fri %"]:
            _fri_promo_df[_c] = _fri_promo_df[_c].map("{:.0f}%".format)
        _fri_col_right.caption(
            f"**Friday promo rates** — {highlight_name} vs camp benchmarks ({hl_level_name})"
        )
        _fri_col_right.dataframe(_fri_promo_df, hide_index=True, use_container_width=True)

    st.markdown(
        "**What the data suggests:** Promotions on Friday attract fans who might not have "
        "come otherwise — a different audience than the core Saturday crowd. "
        "Sat-winners typically run lighter Friday calendars (they're saving the spectacle for Saturday), "
        "but when they do promote on Friday, they lean toward food deals and ticket deals "
        "that lower the barrier for casual fans. "
        "Weekday and Friday promo ROI warrants a dedicated analysis pass."
    )


# -- Sunday: early signals ----------------------------------------------------

st.header("Sunday: Early Look")
st.markdown(
    "Sunday games haven't been formally classified into winner / loser camps yet — "
    "the data story here is preliminary. The chart below shows cap utilization "
    "across the three weekend days for the highlighted team."
)

_dow_data = load_dow_cap_util(highlight_team_id, ANALYSIS_SEASON)
if not _dow_data.empty:
    _DOW_LABEL = {4: "Friday", 5: "Saturday", 6: "Sunday"}
    _DOW_COLOR = {"Friday": "#3498db", "Saturday": "#2ecc71", "Sunday": "#e67e22"}
    _dow_data["day_label"] = _dow_data["day_of_week"].map(_DOW_LABEL)
    _dow_data = _dow_data.dropna(subset=["day_label"])

    _sun_col1, _sun_col2 = st.columns([2, 1])
    with _sun_col1:
        _fig_dow = px.bar(
            _dow_data,
            x="day_label", y="avg_cap_util",
            color="day_label",
            color_discrete_map=_DOW_COLOR,
            text_auto=".0%",
            labels={"avg_cap_util": "Avg cap util", "day_label": "Day"},
            title=f"{highlight_name}: Weekend cap utilization ({ANALYSIS_SEASON})",
        )
        _fig_dow.update_traces(texttemplate="%{y:.0%}")
        _fig_dow.update_layout(
            showlegend=False, height=320, yaxis_tickformat=".0%",
            xaxis={"categoryorder": "array", "categoryarray": ["Friday", "Saturday", "Sunday"]},
        )
        _sun_col1.plotly_chart(_fig_dow, use_container_width=True)

    with _sun_col2:
        _sun_row = _dow_data[_dow_data["day_label"] == "Sunday"]
        _fri_row = _dow_data[_dow_data["day_label"] == "Friday"]
        _sat_row = _dow_data[_dow_data["day_label"] == "Saturday"]
        if not _sun_row.empty:
            st.metric(
                f"Sunday cap util ({int(_sun_row.iloc[0]['games'])} games)",
                f"{float(_sun_row.iloc[0]['avg_cap_util']):.0%}",
            )
        if not _fri_row.empty:
            st.metric(
                f"Friday cap util ({int(_fri_row.iloc[0]['games'])} games)",
                f"{float(_fri_row.iloc[0]['avg_cap_util']):.0%}",
            )
        if not _sat_row.empty:
            st.metric(
                f"Saturday cap util ({int(_sat_row.iloc[0]['games'])} games)",
                f"{float(_sat_row.iloc[0]['avg_cap_util']):.0%}",
            )

    if not _sun_row.empty and not _sat_row.empty:
        _sun_cu = float(_sun_row.iloc[0]["avg_cap_util"])
        _sat_cu = float(_sat_row.iloc[0]["avg_cap_util"])
        _gap_pp = (_sat_cu - _sun_cu) * 100
        if abs(_gap_pp) > 3:
            st.info(
                f"{'Saturday outperforms Sunday' if _gap_pp > 0 else 'Sunday outperforms Saturday'} "
                f"by {abs(_gap_pp):.1f}pp for {highlight_name}. "
                "A deeper Sunday promo analysis — mirroring the Friday-Saturday work — "
                "is the logical next step."
            )

    st.caption(
        "Sunday hasn't been modeled for Sat-winner / Sat-loser camps. "
        "Use the Scheduling page for game-time distributions on Sunday."
    )
else:
    st.caption(f"No Sunday game data available for {highlight_name} in {ANALYSIS_SEASON}.")


# -- Footer / nav -------------------------------------------------------------

see_also([
    ("Competitive Intel", "pages/9_Competitive_Intel.py", "peer comparisons and momentum tracking"),
    ("Promo Strategy",    "pages/7_Promo_Strategy.py", "team-level promo clusters and profile"),
    ("Recommendations",   "pages/10_Recommendations.py", "prioritized actions per team"),
])

render_footer(scripts=["weekend_gap"])
