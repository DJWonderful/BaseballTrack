"""Peer Playbook — curated peer comps for Binghamton, what to steal from each.

This is the "what do successful small-market / cold-weather teams do that we
don't" page. It pairs hard numbers (side-by-side stats, fri/sat profile,
recurring-promo audit) with an LLM-generated "what to steal" brief per peer.

Data sources:
  milb.peer_playbook       -- one row per peer + Binghamton (LLM narrative lives here)
  milb.game_features       -- promo calendars for the diff visual
  milb.dow_promo_lift      -- per-DOW, per-promo lift (for recurring-ritem audit)

Source script: scripts/analyze_peer_playbook.py
"""

# -- Path setup ---------------------------------------------------------------
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils.db import query_df
from utils.footer import render_footer

st.set_page_config(page_title="Peer Playbook | MiLB", page_icon="PB", layout="wide")

RUMBLE_PONIES_ID = 505
RUMBLE_PONIES_NAME = "Binghamton Rumble Ponies"

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
DOW_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DOW_MAP = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

ROLE_LABELS = {
    "small_market_cold": "Small Market • Cold Weather",
    "small_market_warm": "Small Market • Warm",
    "large_market_model": "Large Market Reference",
    "hero": "Binghamton (hero)",
}


# -- Data loaders -------------------------------------------------------------

@st.cache_data(ttl=300)
def load_playbook() -> pd.DataFrame:
    df = query_df("""
        SELECT pp.*, sp.sport_name AS level
          FROM milb.peer_playbook pp
          LEFT JOIN milb.teams t  ON t.team_id = pp.team_id
          LEFT JOIN milb.sports sp ON sp.sport_id = t.sport_id
         ORDER BY (pp.peer_role = 'hero') DESC,
                  pp.peer_role,
                  pp.avg_attendance DESC
    """)
    # Parse what_to_steal JSON -- it's JSONB from Postgres.
    if "what_to_steal" in df.columns:
        df["what_to_steal"] = df["what_to_steal"].apply(
            lambda v: v if isinstance(v, list) else (
                json.loads(v) if isinstance(v, str) and v else []
            )
        )
    return df


@st.cache_data(ttl=300)
def load_promo_calendar(team_ids: tuple[int, ...], season: int) -> pd.DataFrame:
    if not team_ids:
        return pd.DataFrame()
    flag_cols = ", ".join(f"f.{c}" for c in PROMO_FLAGS)
    placeholders = ", ".join(str(t) for t in team_ids)
    return query_df(f"""
        SELECT f.team_id, t.team_name, f.day_of_week, f.attendance, f.capacity_utilization,
               {flag_cols}
          FROM milb.game_features f
          JOIN milb.teams t ON t.team_id = f.team_id
         WHERE f.team_id IN ({placeholders})
           AND f.season = {season}
           AND f.game_type = 'R'
           AND f.attendance IS NOT NULL
    """)


@st.cache_data(ttl=300)
def load_dow_promo_lift(sport_id: int) -> pd.DataFrame:
    return query_df(f"""
        SELECT * FROM milb.dow_promo_lift
         WHERE sport_id = {sport_id}
    """)


# -- Sidebar ------------------------------------------------------------------

playbook = load_playbook()
if playbook.empty:
    st.title("Peer Playbook")
    st.error(
        "No peer_playbook rows yet. Run "
        "`python scripts/analyze_peer_playbook.py` first."
    )
    st.stop()

hero_row = playbook[playbook["peer_role"] == "hero"]
if hero_row.empty:
    st.title("Peer Playbook")
    st.error(f"Hero team ({RUMBLE_PONIES_NAME}) missing from peer_playbook.")
    st.stop()
hero = hero_row.iloc[0]
season = int(hero["season"])

peers_df = playbook[playbook["peer_role"] != "hero"].copy()

with st.sidebar:
    st.header("Lens")
    role_filter = st.multiselect(
        "Peer role",
        options=list(ROLE_LABELS.keys())[:-1],  # exclude hero from filter
        default=list(ROLE_LABELS.keys())[:-1],
        format_func=lambda r: ROLE_LABELS[r],
    )
    filtered_peers = peers_df[peers_df["peer_role"].isin(role_filter)]

    st.divider()
    team_names = filtered_peers["team_name"].tolist()
    default_name = "Portland Sea Dogs" if "Portland Sea Dogs" in team_names else (team_names[0] if team_names else None)
    default_idx = team_names.index(default_name) if default_name in team_names else 0
    focus_name = st.selectbox(
        "Focus peer",
        options=team_names,
        index=default_idx,
        help="The peer whose 'what to steal' brief we feature below.",
    )
    st.divider()
    st.caption(f"Season {season}. Peers are a curated set — see the script for the rationale.")


# -- Intro --------------------------------------------------------------------

st.title("Peer Playbook")
st.markdown(
    f"**{RUMBLE_PONIES_NAME}** vs. a curated peer set: small-market cold-weather "
    "teams (Portland, Erie, New Hampshire, Reading), post-industrial small markets "
    "(Akron), and large-market reference points (Richmond, Frisco). Every tile asks "
    "the same question: **what are they doing that Binghamton isn't?**"
)


# -- Act 1: Side-by-side scorecard --------------------------------------------

st.header("Act 1 — Side-by-side scorecard")

_table = filtered_peers.copy()
hero_display = hero_row.copy()
scorecard = pd.concat([hero_display, _table], ignore_index=True)[[
    "team_name", "peer_role", "league_rank", "avg_attendance", "cap_utilization",
    "yoy_change_pct", "msa_population", "stadium_year",
    "promos_per_game", "fri_avg_att", "sat_avg_att",
    "top_promo_flag", "top_promo_lift", "has_recurring_promo",
]].copy()

scorecard["peer_role"] = scorecard["peer_role"].map(ROLE_LABELS)
scorecard["top_promo_flag"] = scorecard["top_promo_flag"].map(PROMO_LABELS).fillna("")
scorecard["cap_utilization"] = (scorecard["cap_utilization"].astype(float) * 100).round(1)
scorecard["yoy_change_pct"] = scorecard["yoy_change_pct"].astype(float).round(1)
scorecard.rename(columns={
    "team_name": "Team", "peer_role": "Role", "league_rank": "Rank",
    "avg_attendance": "Avg Att", "cap_utilization": "Cap %",
    "yoy_change_pct": "YoY %", "msa_population": "MSA Pop",
    "stadium_year": "Stadium", "promos_per_game": "Promos/Game",
    "fri_avg_att": "Fri Avg", "sat_avg_att": "Sat Avg",
    "top_promo_flag": "Top Promo", "top_promo_lift": "Lift",
    "has_recurring_promo": "Recurring?",
}, inplace=True)

st.dataframe(
    scorecard,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Avg Att": st.column_config.NumberColumn(format="%d"),
        "Fri Avg": st.column_config.NumberColumn(format="%d"),
        "Sat Avg": st.column_config.NumberColumn(format="%d"),
        "Lift": st.column_config.NumberColumn(format="%+d"),
        "MSA Pop": st.column_config.NumberColumn(format="%d"),
        "Stadium": st.column_config.NumberColumn(format="%d"),
        "Cap %": st.column_config.NumberColumn(format="%.1f%%"),
        "YoY %": st.column_config.NumberColumn(format="%+.1f%%"),
    },
)

st.caption(
    "Binghamton is always row 1 (hero). Sort any column to see the gap to close. "
    "Portland is the single strongest comp — same stadium vintage, cold-weather, "
    "small market."
)


# -- Act 2: Friday vs Saturday — the weekend pattern --------------------------

st.header("Act 2 — The weekend pattern")

fri_sat = scorecard[["Team", "Role", "Fri Avg", "Sat Avg"]].copy()
fri_sat["Sat - Fri"] = fri_sat["Sat Avg"] - fri_sat["Fri Avg"]
fri_sat = fri_sat.sort_values("Sat - Fri", ascending=True)

fig = go.Figure()
for _, r in fri_sat.iterrows():
    color = "#d73027" if r["Team"] == RUMBLE_PONIES_NAME else (
        "#1a9850" if r["Sat - Fri"] > 0 else "#fdae61"
    )
    fig.add_trace(go.Bar(
        x=[r["Sat - Fri"]], y=[r["Team"]], orientation="h",
        marker_color=color, showlegend=False,
        hovertemplate=f"{r['Team']}<br>Fri: {r['Fri Avg']:,.0f}<br>Sat: {r['Sat Avg']:,.0f}<br>Gap: %{{x:+,.0f}}<extra></extra>",
    ))
fig.update_layout(
    title="Saturday minus Friday avg attendance — positive = Sat wins",
    xaxis_title="Fans (Sat - Fri)",
    yaxis_title="",
    height=40 * len(fri_sat) + 100,
    margin=dict(l=200, r=20, t=60, b=40),
)
st.plotly_chart(fig, use_container_width=True)

if hero["sat_avg_att"] and hero["fri_avg_att"] and hero["sat_avg_att"] < hero["fri_avg_att"]:
    st.info(
        f"**Binghamton's Saturday ({int(hero['sat_avg_att']):,}) is LESS than its Friday "
        f"({int(hero['fri_avg_att']):,}).** In the peer set most teams run the other way. "
        f"This is the core of the fireworks-swap hypothesis — see Hypothesis Lab."
    )


# -- Act 3: Promo calendar diff (focus peer vs hero) --------------------------

st.header(f"Act 3 — Promo calendar diff: {focus_name} vs {RUMBLE_PONIES_NAME}")

focus_row = peers_df[peers_df["team_name"] == focus_name].iloc[0]
focus_id = int(focus_row["team_id"])

cal = load_promo_calendar((RUMBLE_PONIES_ID, focus_id), season)
if cal.empty:
    st.warning("No game-level data available for this comparison.")
else:
    cal["dow_label"] = cal["day_of_week"].map(DOW_MAP)
    # Build a matrix: rows = promo, cols = DOW, cell = pct of games using that promo
    rows = []
    for tid, tname in [(RUMBLE_PONIES_ID, RUMBLE_PONIES_NAME), (focus_id, focus_name)]:
        sub = cal[cal["team_id"] == tid]
        for flag in PROMO_FLAGS:
            for dow in DOW_ORDER:
                dow_sub = sub[sub["dow_label"] == dow]
                if dow_sub.empty:
                    continue
                pct = float(dow_sub[flag].fillna(False).astype(int).mean())
                rows.append({
                    "team": tname,
                    "promo": PROMO_LABELS[flag],
                    "dow": dow,
                    "pct": pct,
                })
    mix = pd.DataFrame(rows)

    col1, col2 = st.columns(2)
    for col, tname in [(col1, RUMBLE_PONIES_NAME), (col2, focus_name)]:
        sub = mix[mix["team"] == tname]
        pivot = sub.pivot(index="promo", columns="dow", values="pct")
        pivot = pivot.reindex(columns=DOW_ORDER).reindex(
            index=[PROMO_LABELS[f] for f in PROMO_FLAGS]
        )
        fig = px.imshow(
            pivot, aspect="auto", color_continuous_scale="Blues",
            zmin=0, zmax=1,
            labels=dict(x="Day", y="Promo", color="Coverage %"),
            title=tname,
        )
        fig.update_layout(height=460, margin=dict(l=10, r=10, t=50, b=10))
        col.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Look for *cells* present on one side but not the other. Recurring rituals "
        "show up as a solid-color column on a single DOW; one-offs are scattered."
    )


# -- Act 4: Recurring-ritual audit -------------------------------------------

st.header("Act 4 — Recurring-ritual audit")

# Proxy: a recurring ritual is a (team, dow, flag) where flag appears on >= 60%
# of games that DOW. We surface the top rituals across the peer set.
ritual_rows = []
for _, p in peers_df.iterrows():
    pid = int(p["team_id"])
    sub = cal[cal["team_id"] == pid] if not cal.empty else pd.DataFrame()
    if sub.empty:
        # Load on demand so the page is complete
        sub = load_promo_calendar((pid,), season)
    if sub.empty:
        continue
    sub = sub.copy()
    sub["dow_label"] = sub["day_of_week"].map(DOW_MAP)
    for flag in PROMO_FLAGS:
        for dow in DOW_ORDER:
            dsub = sub[sub["dow_label"] == dow]
            if len(dsub) < 3:
                continue
            pct = float(dsub[flag].fillna(False).astype(int).mean())
            if pct >= 0.6:
                ritual_rows.append({
                    "Team": p["team_name"],
                    "Role": ROLE_LABELS.get(p["peer_role"], p["peer_role"]),
                    "Day": dow,
                    "Promo": PROMO_LABELS[flag],
                    "Coverage": pct,
                    "Games": len(dsub),
                })

rituals = pd.DataFrame(ritual_rows)
if rituals.empty:
    st.caption("No recurring-ritual candidates found in the peer set.")
else:
    rituals = rituals.sort_values(["Team", "Day"])
    # Binghamton's rituals (for comparison)
    rp_sub = cal[cal["team_id"] == RUMBLE_PONIES_ID].copy() if not cal.empty else load_promo_calendar((RUMBLE_PONIES_ID,), season)
    rp_sub["dow_label"] = rp_sub["day_of_week"].map(DOW_MAP)
    rp_rituals = []
    for flag in PROMO_FLAGS:
        for dow in DOW_ORDER:
            dsub = rp_sub[rp_sub["dow_label"] == dow]
            if len(dsub) < 3:
                continue
            pct = float(dsub[flag].fillna(False).astype(int).mean())
            if pct >= 0.6:
                rp_rituals.append((dow, PROMO_LABELS[flag], pct))

    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown(f"**Peer rituals** (coverage ≥ 60% on a given DOW)")
        st.dataframe(
            rituals,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Coverage": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=1),
            },
        )
    with c2:
        st.markdown(f"**Binghamton's current rituals**")
        if rp_rituals:
            for dow, promo, pct in rp_rituals:
                st.write(f"• {dow} — {promo} ({pct:.0%})")
        else:
            st.warning(
                "Binghamton has **no recurring rituals** at the 60% coverage bar. "
                "This is the single biggest absence vs the peer set."
            )


# -- Act 5: LLM narrative for the focus peer ----------------------------------

st.header(f"Act 5 — What to steal from {focus_name}")

if focus_row.get("narrative_text"):
    st.markdown(focus_row["narrative_text"])
else:
    st.info(
        "No LLM narrative for this peer yet. Run "
        "`python scripts/analyze_peer_playbook.py` with Ollama available."
    )

steal = focus_row.get("what_to_steal") or []
if isinstance(steal, str):
    try:
        steal = json.loads(steal)
    except Exception:
        steal = []

if steal:
    st.subheader("What to steal")
    for item in steal:
        impact = (item.get("est_impact") or "").lower()
        impact_color = {"high": "🟢", "medium": "🟡", "low": "⚪"}.get(impact, "⚪")
        st.markdown(f"{impact_color} **{item.get('action', '')}**  \n*{item.get('reason', '')}*")


# -- All peer briefs at a glance ----------------------------------------------

with st.expander("All peer briefs (read the room)", expanded=False):
    for _, p in peers_df.iterrows():
        st.markdown(f"### {p['team_name']}  *({ROLE_LABELS.get(p['peer_role'], '')})*")
        if p.get("narrative_text"):
            st.markdown(p["narrative_text"])
        st_items = p.get("what_to_steal") or []
        if isinstance(st_items, str):
            try:
                st_items = json.loads(st_items)
            except Exception:
                st_items = []
        for item in st_items or []:
            st.markdown(f"- **{item.get('action', '')}** — {item.get('reason', '')}")
        st.divider()


render_footer(scripts=["peer_playbook", "dow_promo_heatmap"])
