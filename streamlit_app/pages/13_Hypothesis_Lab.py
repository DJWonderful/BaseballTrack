"""Hypothesis Lab — where BI tests ideas before the GM meeting.

Each tab is one falsifiable hypothesis, backed by a counterfactual or lift
estimate with confidence intervals:

  1. Fireworks Swap      -- move fireworks Fri -> Sat, stack Fri with giveaway
                            / kids / celebrity / entertainment. Projects annual
                            gate delta with 95% bootstrap CI.

  2. Stack Effects       -- which combinations of promo flags produce
                            super-additive lift (synergy) vs diminishing returns.

  3. DOW x Promo Heatmap -- where each promo type lifts most and least.

  4. Calendar Simulator  -- WHAT-IF: tag a day-of-week with a promo set and
                            see the projected per-game gate.

Backing tables: milb.fireworks_swap, milb.promo_stack_effects,
                milb.dow_promo_lift, milb.game_features

Source scripts: scripts/analyze_fireworks_swap.py,
                scripts/analyze_stack_effects.py,
                scripts/analyze_dow_promo_heatmap.py
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

st.set_page_config(page_title="Hypothesis Lab | MiLB", page_icon="HL", layout="wide")

RUMBLE_PONIES_ID = 505
RUMBLE_PONIES_NAME = "Binghamton Rumble Ponies"
LEVEL_NAMES = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}
DOW_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

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


# -- Data loaders -------------------------------------------------------------

@st.cache_data(ttl=300)
def load_fireworks_swap(team_id: int) -> pd.DataFrame:
    return query_df(f"""
        SELECT fs.*, t.team_name
          FROM milb.fireworks_swap fs
          JOIN milb.teams t ON t.team_id = fs.team_id
         WHERE fs.team_id = {team_id}
         ORDER BY fs.season DESC, fs.scenario
    """)


@st.cache_data(ttl=300)
def load_stack_effects(sport_id: int) -> pd.DataFrame:
    return query_df(f"""
        SELECT * FROM milb.promo_stack_effects
         WHERE sport_id = {sport_id}
         ORDER BY lift_fans DESC
    """)


@st.cache_data(ttl=300)
def load_dow_promo_lift(sport_id: int) -> pd.DataFrame:
    return query_df(f"""
        SELECT * FROM milb.dow_promo_lift
         WHERE sport_id = {sport_id}
         ORDER BY dow_label, lift_fans DESC
    """)


@st.cache_data(ttl=300)
def load_team_baseline(team_id: int, season: int) -> pd.DataFrame:
    flag_cols = ", ".join(f"f.{c}" for c in PROMO_FLAGS)
    return query_df(f"""
        SELECT f.day_of_week, f.attendance, {flag_cols}
          FROM milb.game_features f
         WHERE f.team_id = {team_id}
           AND f.season = {season}
           AND f.game_type = 'R'
           AND f.attendance IS NOT NULL
    """)


@st.cache_data(ttl=300)
def resolve_hero() -> tuple[int, int]:
    df = query_df(f"""
        SELECT team_id, sport_id FROM milb.teams
         WHERE team_id = {RUMBLE_PONIES_ID}
    """)
    if df.empty:
        return (RUMBLE_PONIES_ID, 12)
    return (int(df.iloc[0]["team_id"]), int(df.iloc[0]["sport_id"]))


@st.cache_data(ttl=300)
def latest_season_for_team(team_id: int) -> int:
    df = query_df(f"""
        SELECT MAX(season) AS s
          FROM milb.game_features
         WHERE team_id = {team_id} AND attendance IS NOT NULL
    """)
    if df.empty or pd.isna(df.iloc[0]["s"]):
        return 2025
    return int(df.iloc[0]["s"])


# -- Sidebar ------------------------------------------------------------------

hero_id, hero_sport = resolve_hero()

with st.sidebar:
    st.header("Scope")
    # This page is always hero-centric, but we let the user compare at a
    # different level if they want.
    st.caption(f"Hero team: **{RUMBLE_PONIES_NAME}** ({LEVEL_NAMES[hero_sport]})")

    sport_for_level = st.selectbox(
        "League-wide lifts reference level",
        options=list(LEVEL_NAMES.keys()),
        index=list(LEVEL_NAMES.keys()).index(hero_sport),
        format_func=lambda s: LEVEL_NAMES[s],
        help="DOW x promo heatmap and stack effects are shown at this level.",
    )

    st.divider()
    st.caption(
        "Every tab is a testable hypothesis. Numbers come from the `analyze_*` "
        "scripts — re-run them if you change the data."
    )


# -- Intro --------------------------------------------------------------------

st.title("Hypothesis Lab")
st.markdown(
    "**Main hypothesis for the GM meeting:** Move fireworks from Friday to "
    "Saturday. Stack Friday with giveaway + celebrity + kids event + "
    "entertainment. Let fireworks carry Saturday."
)
st.caption(
    "**How to read this page.** Tab 1 is the actual proposal — a causal "
    "counterfactual with confidence intervals, backed by the S-learner. "
    "Tabs 2–3 are diagnostic *evidence* — observed correlations used to "
    "design the proposal, not to claim causal effects on their own. "
    "Tab 4 is a sandbox for what-if exploration."
)

tab_hero, tab_heat, tab_stack, tab_sim = st.tabs([
    "Proposed play: Fireworks swap",
    "Evidence: Day × promo map",
    "Evidence: Stack combinations",
    "Sandbox: Custom what-if",
])


# =============================================================================
# TAB 1: Fireworks Swap
# =============================================================================

with tab_hero:
    st.header("Proposed play: move fireworks from Friday to Saturday")
    st.caption(
        "Backed by the S-learner counterfactual (`milb.promo_lift_cf`). "
        "Lift estimands are chosen to match direction of change: **ATT** for "
        "flags we're removing (effect on games that currently HAVE the promo), "
        "**ATU** for flags we're adding (effect on games that currently DON'T). "
        "Confidence intervals reflect the S-learner's standard error, not "
        "the raw spread of weekly attendance."
    )
    fs = load_fireworks_swap(RUMBLE_PONIES_ID)
    if fs.empty:
        st.warning(
            "No fireworks_swap rows yet. Run:\n\n"
            "```\npython scripts/analyze_fireworks_swap.py\n```"
        )
    else:
        latest = fs["season"].max()
        fs = fs[fs["season"] == latest].copy()

        current = fs[fs["scenario"] == "current"].iloc[0] if not fs[fs["scenario"] == "current"].empty else None
        peer = fs[fs["scenario"] == "peer_baseline"].iloc[0] if not fs[fs["scenario"] == "peer_baseline"].empty else None
        cf = fs[fs["scenario"] == "counterfactual"].iloc[0] if not fs[fs["scenario"] == "counterfactual"].empty else None

        # Top-line KPIs
        if cf is not None and current is not None:
            annual = int(cf["projected_annual_delta"]) if cf["projected_annual_delta"] is not None else 0
            lo = int(cf["projected_annual_ci_lo"]) if cf["projected_annual_ci_lo"] is not None else 0
            hi = int(cf["projected_annual_ci_hi"]) if cf["projected_annual_ci_hi"] is not None else 0
            fri_delta = float(cf["projected_fri_delta"]) if cf["projected_fri_delta"] is not None else 0
            sat_delta = float(cf["projected_sat_delta"]) if cf["projected_sat_delta"] is not None else 0

            c1, c2, c3 = st.columns(3)
            c1.metric(
                "Projected annual Δ fans",
                f"{annual:+,}",
                delta=f"95% CI: {lo:+,} .. {hi:+,}",
                delta_color="off",
            )
            c2.metric("Per-Fri Δ", f"{fri_delta:+,.0f}")
            c3.metric("Per-Sat Δ", f"{sat_delta:+,.0f}")

        # Scenario comparison bar
        if current is not None and cf is not None:
            sc_long = []
            for row, label in [(current, "Current"), (peer, "Peer Sat-winners"), (cf, "Counterfactual")]:
                if row is None:
                    continue
                sc_long.append({"Scenario": label, "Day": "Fri", "Att": float(row["fri_avg_att"] or 0),
                                "CI_lo": float(row["fri_avg_att_ci_lo"] or 0),
                                "CI_hi": float(row["fri_avg_att_ci_hi"] or 0)})
                sc_long.append({"Scenario": label, "Day": "Sat", "Att": float(row["sat_avg_att"] or 0),
                                "CI_lo": float(row["sat_avg_att_ci_lo"] or 0),
                                "CI_hi": float(row["sat_avg_att_ci_hi"] or 0)})
            sc_df = pd.DataFrame(sc_long)
            fig = px.bar(
                sc_df, x="Scenario", y="Att", color="Day", barmode="group",
                error_y=sc_df["CI_hi"] - sc_df["Att"],
                error_y_minus=sc_df["Att"] - sc_df["CI_lo"],
                title="Fri vs Sat avg attendance — current vs peers vs counterfactual",
                color_discrete_map={"Fri": "#2c7fb8", "Sat": "#d7301f"},
            )
            fig.update_layout(height=400, yaxis_title="Avg attendance")
            st.plotly_chart(fig, use_container_width=True)

        # Narrative
        if cf is not None:
            st.subheader("What the model says")
            st.markdown(
                f"- **Current Fri: {int(current['fri_avg_att']):,}** "
                f"(fireworks on {current['fri_has_fireworks_pct']:.0%} of Fris)  \n"
                f"- **Current Sat: {int(current['sat_avg_att']):,}** "
                f"(fireworks on {current['sat_has_fireworks_pct']:.0%} of Sats)  \n"
                f"- **Counterfactual Fri: {int(cf['fri_avg_att']):,}** "
                "(no fireworks, stacked giveaway + kids + celebrity + entertainment)  \n"
                f"- **Counterfactual Sat: {int(cf['sat_avg_att']):,}** "
                f"(fireworks on {cf['sat_has_fireworks_pct']:.0%} of Sats)"
            )
            st.info(cf["notes"] or "")

        st.subheader("Assumptions worth pressure-testing")
        st.markdown(
            "1. **Lifts come from the S-learner counterfactual** "
            "(`milb.promo_lift_cf`), scoped level-wide for the ATT (removal) "
            "and ATU (addition) estimates. Team-scoped ATE exists for "
            "context but isn't used here because we need direction-specific "
            "estimands that only exist at level/league scope.  \n"
            "2. **Friday baseline = RP's observed Fri average**, not a "
            "synthetic control. We anchor on what actually happened and "
            "adjust by the CF lifts. That's safer than constructing a "
            "Friday from the 2 non-fireworks Fris we have on record.  \n"
            "3. **Independent additive lifts.** The S-learner is trained "
            "jointly on all flags, so the individual ATE/ATT/ATU values "
            "already account for typical co-occurrence. Synergy beyond "
            "that is shown observationally on the Stack Combinations tab.  \n"
            "4. **No promo budget constraint.** Moving fireworks is a "
            "~$25–40K budget item per night — operational cost is out of "
            "scope for this analysis."
        )


# =============================================================================
# TAB 2: Stack Effects
# =============================================================================

with tab_stack:
    st.header("Evidence: which stacked combinations draw together?")
    st.caption(
        "**Observational, not causal.** These are raw differences — "
        "`avg(att | combo active) − avg(att | no promos)` — pooled across "
        "all teams at the selected level. Use to spot *which* combos tend "
        "to co-occur with big gates, then pressure-test them before "
        "committing. Selection bias applies: teams choose combos for a "
        "reason, and that reason is often the attendance itself."
    )
    stacks = load_stack_effects(sport_for_level)
    if stacks.empty:
        st.warning("No stack_effects rows yet. Run `python scripts/analyze_stack_effects.py`.")
    else:
        dow_opts = ["All"] + sorted([d for d in stacks["dow_label"].unique() if d != "All"],
                                    key=lambda x: DOW_ORDER.index(x) if x in DOW_ORDER else 99)
        col_a, col_b = st.columns([1, 2])
        with col_a:
            dow_pick = st.selectbox(
                "Day of week",
                options=dow_opts,
                index=dow_opts.index("Fri") if "Fri" in dow_opts else 0,
            )
            min_games = st.slider("Min games per combo", 20, 200, 25, step=5)
            only_synergy = st.checkbox("Only show synergistic combos", value=False)
            n_flag_range = st.slider("Stack size range", 1, 4, (2, 4))

        sub = stacks[stacks["dow_label"] == dow_pick].copy()
        sub = sub[sub["n_games"] >= min_games]
        sub = sub[sub["n_flags"].between(n_flag_range[0], n_flag_range[1])]
        if only_synergy:
            sub = sub[sub["is_synergistic"]]
        sub = sub.sort_values("lift_fans", ascending=False).head(30)

        with col_b:
            if sub.empty:
                st.info("No combos match the filters. Loosen the constraints.")
            else:
                sub["label"] = sub["flag_combo"].apply(
                    lambda s: " + ".join(PROMO_LABELS.get(f, f) for f in s.split("+"))
                )
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=sub["lift_fans"].astype(float),
                    y=sub["label"],
                    orientation="h",
                    marker_color=[
                        "#1a9850" if s else "#2c7fb8"
                        for s in sub["is_synergistic"]
                    ],
                    error_x=dict(
                        type="data", symmetric=False,
                        array=sub["lift_ci_hi"].astype(float) - sub["lift_fans"].astype(float),
                        arrayminus=sub["lift_fans"].astype(float) - sub["lift_ci_lo"].astype(float),
                    ),
                    hovertemplate="<b>%{y}</b><br>Lift: %{x:+,.0f}<br>Expected: %{customdata[0]:+,.0f}<br>Synergy: %{customdata[1]:+,.0f}<br>Games: %{customdata[2]}<extra></extra>",
                    customdata=sub[["expected_additive", "synergy_fans", "n_games"]].astype(float).values,
                ))
                fig.update_layout(
                    title=f"Top stacks on {dow_pick} at {LEVEL_NAMES[sport_for_level]}",
                    xaxis_title="Lift (fans) with 95% CI",
                    height=30 * len(sub) + 100,
                    yaxis=dict(autorange="reversed"),
                    margin=dict(l=200, r=20, t=60, b=40),
                )
                st.plotly_chart(fig, use_container_width=True)

        if not sub.empty:
            st.caption(
                f"Green bars = synergistic (combo beats sum of single-flag lifts by 5%+). "
                "Blue bars = lifts but not super-additive."
            )
            with st.expander("Raw data"):
                st.dataframe(
                    sub[["flag_combo", "n_games", "n_teams", "avg_att", "baseline_att",
                         "lift_fans", "expected_additive", "synergy_fans", "is_synergistic"]],
                    hide_index=True, use_container_width=True,
                )


# =============================================================================
# TAB 3: DOW × Promo Heatmap
# =============================================================================

with tab_heat:
    st.header("Evidence: where each promo lands on the calendar")
    st.caption(
        "**Observational, not causal.** Lift = avg attendance WITH the flag "
        "minus avg WITHOUT, at the selected level. Treat red cells as "
        "*suspicion*, not proof: a \"negative lift\" usually means teams "
        "deploy that promo on already-weak slots, not that the promo "
        "backfires. For causal estimates, use the Proposed play tab or "
        "`milb.promo_lift_cf` directly."
    )

    heat = load_dow_promo_lift(sport_for_level)
    if heat.empty:
        st.warning("No dow_promo_lift rows yet. Run `python scripts/analyze_dow_promo_heatmap.py`.")
    else:
        metric = st.radio(
            "Metric",
            options=["lift_fans", "cap_util_lift", "lift_pct"],
            format_func={"lift_fans": "Lift (fans)", "cap_util_lift": "Lift (cap %)", "lift_pct": "Lift %"}.get,
            horizontal=True,
        )

        heat["promo_label"] = heat["promo_type"].map(PROMO_LABELS)
        pivot = heat.pivot(index="promo_label", columns="dow_label", values=metric)
        pivot = pivot.reindex(columns=[d for d in DOW_ORDER if d in pivot.columns])
        pivot = pivot.reindex(index=[PROMO_LABELS[f] for f in PROMO_FLAGS if PROMO_LABELS[f] in pivot.index])

        vmax = float(np.nanmax(np.abs(pivot.values)))
        fig = px.imshow(
            pivot,
            color_continuous_scale="RdBu",
            color_continuous_midpoint=0,
            zmin=-vmax, zmax=vmax,
            aspect="auto",
            labels=dict(color=metric),
            text_auto=".0f" if metric != "lift_pct" else ".1%",
        )
        fig.update_layout(height=500, xaxis_title="Day of week", yaxis_title="",
                          margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)

        # Fireworks-on-Saturday call-out
        fw_sat = heat[(heat["promo_type"] == "has_fireworks") & (heat["dow_label"] == "Sat")]
        fw_fri = heat[(heat["promo_type"] == "has_fireworks") & (heat["dow_label"] == "Fri")]
        if not fw_sat.empty and not fw_fri.empty:
            st.info(
                f"**Fireworks lift check:** "
                f"Fri = {float(fw_fri.iloc[0]['lift_fans']):+,.0f} fans, "
                f"Sat = {float(fw_sat.iloc[0]['lift_fans']):+,.0f} fans "
                f"at {LEVEL_NAMES[sport_for_level]}."
            )


# =============================================================================
# TAB 4: Calendar Simulator
# =============================================================================

with tab_sim:
    st.header("Sandbox: plug in a promo stack, see the projected gate")
    st.caption(
        "Exploratory. Baseline is Binghamton's own no-promo DOW average; "
        "lifts come from the DOW × promo map (observational — see tab 2 "
        "caveats). Use to compare *relative* scenarios, not to forecast a "
        "single night's gate."
    )

    c1, c2 = st.columns([1, 2])
    with c1:
        sim_dow = st.selectbox("Day of week", DOW_ORDER, index=4)
        sim_flags = st.multiselect(
            "Promo stack",
            options=PROMO_FLAGS,
            default=["has_giveaway", "has_kids_event", "has_celebrity", "has_entertain"],
            format_func=lambda f: PROMO_LABELS.get(f, f),
        )
        use_synergy = st.checkbox(
            "Use stack synergy when present",
            value=True,
            help="If the exact combo exists in stack_effects, use its observed lift instead of summing single-flag lifts.",
        )

    hero_games = load_team_baseline(RUMBLE_PONIES_ID, season=latest_season_for_team(RUMBLE_PONIES_ID))
    if hero_games.empty:
        with c2:
            st.warning("No baseline data for Binghamton.")
    else:
        hero_games = hero_games.copy()
        dow_num = DOW_ORDER.index(sim_dow)
        hero_dow = hero_games[hero_games["day_of_week"] == dow_num]
        hero_no_promo = hero_dow[~hero_dow[PROMO_FLAGS].fillna(False).any(axis=1)]
        baseline = float(hero_no_promo["attendance"].mean()) if len(hero_no_promo) >= 3 else float(hero_dow["attendance"].mean() if not hero_dow.empty else 0)

        # Get lifts
        heat = load_dow_promo_lift(sport_for_level)
        heat_dow = heat[heat["dow_label"] == sim_dow]

        total_lift = 0.0
        per_flag_rows = []
        synergy_used = False
        if use_synergy and sim_flags:
            stacks = load_stack_effects(sport_for_level)
            combo_key = "+".join(sorted(sim_flags))
            exact = stacks[(stacks["flag_combo"] == combo_key) & (stacks["dow_label"] == sim_dow)]
            if not exact.empty:
                total_lift = float(exact.iloc[0]["lift_fans"])
                synergy_used = True
                per_flag_rows.append({"Source": f"Exact stack match ({combo_key})", "Lift": total_lift})

        if not synergy_used:
            for flag in sim_flags:
                row = heat_dow[heat_dow["promo_type"] == flag]
                if row.empty:
                    continue
                lift = float(row.iloc[0]["lift_fans"])
                total_lift += lift
                per_flag_rows.append({"Source": PROMO_LABELS.get(flag, flag), "Lift": lift})

        projected = baseline + total_lift

        with c2:
            st.metric(
                f"Projected {sim_dow} gate",
                f"{projected:,.0f} fans",
                delta=f"{total_lift:+,.0f} vs no-promo baseline",
            )
            if synergy_used:
                st.caption("Using observed stack synergy (exact combo match).")
            else:
                st.caption(
                    "Using additive single-flag lifts. "
                    "Tick the synergy box to use the observed combo lift when available."
                )
            if per_flag_rows:
                st.dataframe(
                    pd.DataFrame(per_flag_rows),
                    hide_index=True, use_container_width=True,
                    column_config={"Lift": st.column_config.NumberColumn(format="%+.0f")},
                )
            st.caption(
                f"Baseline (no-promo {sim_dow}): {baseline:,.0f} fans "
                f"(n = {len(hero_no_promo)} games)."
            )


# -- Footer -------------------------------------------------------------------

render_footer(scripts=["fireworks_swap", "stack_effects", "dow_promo_heatmap"])
