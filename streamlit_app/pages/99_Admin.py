"""Admin & data-freshness dashboard.

Non-analytical page for operators: what's the last pipeline run, what's the
row count in every key table, where are the gaps, is the LLM enrichment
caught up. Put at page number 99 so it sorts to the bottom of the sidebar.
"""

# -- Path setup ---------------------------------------------------------------
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import streamlit as st

from utils.db import query_df
from utils.footer import render_footer

st.set_page_config(page_title="Admin | MiLB", page_icon="Admin", layout="wide")

st.title("Admin & Data Freshness")
st.caption("Operational view: pipeline run log, row counts, coverage gaps. Not for analysis.")


# -- Analysis runs ------------------------------------------------------------

@st.cache_data(ttl=300)
def load_all_runs():
    return query_df("""
        SELECT DISTINCT ON (analysis_name)
               analysis_name, started_at, completed_at, status, record_count, error_message
          FROM milb.analysis_runs
         ORDER BY analysis_name, started_at DESC
    """)


@st.cache_data(ttl=300)
def load_run_history():
    return query_df("""
        SELECT analysis_name, status, started_at, completed_at, record_count
          FROM milb.analysis_runs
         ORDER BY started_at DESC
         LIMIT 50
    """)


st.subheader("Latest analysis runs")
runs = load_all_runs()
if runs.empty:
    st.info("No analysis runs logged yet.")
else:
    display = runs.copy()
    display["started_at"] = pd.to_datetime(display["started_at"]).dt.strftime("%Y-%m-%d %H:%M")
    display["completed_at"] = pd.to_datetime(display["completed_at"]).dt.strftime("%Y-%m-%d %H:%M")
    display.columns = ["Analysis", "Started", "Completed", "Status", "Rows", "Error"]
    st.dataframe(display, use_container_width=True, hide_index=True)

with st.expander("Recent run history (last 50)"):
    hist = load_run_history()
    if not hist.empty:
        hist["started_at"]   = pd.to_datetime(hist["started_at"]).dt.strftime("%Y-%m-%d %H:%M")
        hist["completed_at"] = pd.to_datetime(hist["completed_at"]).dt.strftime("%Y-%m-%d %H:%M")
        hist.columns = ["Analysis", "Status", "Started", "Completed", "Rows"]
        st.dataframe(hist, use_container_width=True, hide_index=True)

st.divider()


# -- Row counts ---------------------------------------------------------------

@st.cache_data(ttl=300)
def load_row_counts():
    # Each table tracks freshness under a different column name -- these
    # expressions match the actual schema per information_schema.
    return query_df("""
        SELECT 'games'                AS table_name, COUNT(*) AS n, MAX(updated_at)  AS last_update FROM milb.games
        UNION ALL
        SELECT 'game_promotions',     COUNT(*), MAX(updated_at)  FROM milb.game_promotions
        UNION ALL
        SELECT 'game_features',       COUNT(*), MAX(created_at)  FROM milb.game_features
        UNION ALL
        SELECT 'team_recommendations',COUNT(*), MAX(computed_at) FROM milb.team_recommendations
        UNION ALL
        SELECT 'team_momentum',       COUNT(*), MAX(computed_at) FROM milb.team_momentum
        UNION ALL
        SELECT 'team_narratives',     COUNT(*), MAX(generated_at)FROM milb.team_narratives
        UNION ALL
        SELECT 'group_narratives',    COUNT(*), MAX(generated_at)FROM milb.group_narratives
        UNION ALL
        SELECT 'venue_demographics',  COUNT(*), MAX(updated_at)  FROM milb.venue_demographics
        UNION ALL
        SELECT 'promo_lift',          COUNT(*), MAX(computed_at) FROM milb.promo_lift
        UNION ALL
        SELECT 'transactions',        COUNT(*), MAX(updated_at)  FROM milb.transactions
    """)


st.subheader("Row counts & last update")
counts = load_row_counts()
if not counts.empty:
    counts["last_update"] = pd.to_datetime(counts["last_update"]).dt.strftime("%Y-%m-%d %H:%M")
    counts.columns = ["Table", "Rows", "Last update"]
    counts["Rows"] = counts["Rows"].apply(lambda n: f"{int(n):,}")
    st.dataframe(counts, use_container_width=True, hide_index=True)

st.divider()


# -- Coverage / gaps ----------------------------------------------------------

@st.cache_data(ttl=300)
def load_coverage():
    return query_df("""
        SELECT
            season,
            COUNT(*)                                     AS total_games,
            SUM((attendance IS NULL)::int)               AS missing_attendance,
            SUM((attendance IS NOT NULL)::int)           AS has_attendance,
            ROUND(
                SUM((attendance IS NOT NULL)::int)::numeric / COUNT(*)::numeric * 100,
                1
            )                                            AS pct_with_attendance
          FROM milb.games
         WHERE sport_id IN (11,12,13,14)
           AND abstract_game_state = 'Final'
           AND game_type = 'R'
         GROUP BY season
         ORDER BY season DESC
    """)


@st.cache_data(ttl=300)
def load_enrichment_coverage():
    return query_df("""
        SELECT
            COUNT(*)                                              AS total_promos,
            SUM((enrichment_method IS NOT NULL)::int)             AS enriched,
            SUM((enrichment_method IS NULL)::int)                 AS pending,
            SUM((enrichment_method = 'rules')::int)               AS enriched_rules,
            SUM((enrichment_method = 'llm')::int)                 AS enriched_llm
          FROM milb.game_promotions
    """)


@st.cache_data(ttl=300)
def load_narrative_coverage():
    return query_df("""
        SELECT
            (SELECT COUNT(*) FROM milb.team_narratives)              AS team_narratives,
            (SELECT COUNT(*) FROM milb.group_narratives
              WHERE group_type = 'competitive_intel')                AS ci_briefs,
            (SELECT COUNT(DISTINCT team_id) FROM milb.teams
              WHERE sport_id IN (11,12,13,14))                       AS total_teams
    """)


st.subheader("Coverage & gaps")

cov_col, enr_col, nar_col = st.columns(3)

with cov_col:
    st.markdown("**Attendance coverage by season**")
    cov = load_coverage()
    if not cov.empty:
        cov["pct_with_attendance"] = cov["pct_with_attendance"].apply(lambda v: f"{v:.1f}%")
        cov.columns = ["Season", "Total games", "Missing att", "Has att", "Coverage %"]
        st.dataframe(cov, use_container_width=True, hide_index=True)

with enr_col:
    st.markdown("**LLM promo enrichment**")
    enr = load_enrichment_coverage()
    if not enr.empty:
        e = enr.iloc[0]
        total = int(e["total_promos"] or 0)
        enriched = int(e["enriched"] or 0)
        pending = int(e["pending"] or 0)
        pct = (enriched / total * 100) if total else 0
        st.metric("Enriched", f"{enriched:,} / {total:,}", delta=f"{pct:.1f}% complete")
        st.metric("Pending", f"{pending:,}",
                  help="Run `python scripts/enrich_promotions.py` to process.")

with nar_col:
    st.markdown("**Narrative coverage**")
    nar = load_narrative_coverage()
    if not nar.empty:
        n = nar.iloc[0]
        st.metric("Team briefs", f"{int(n['team_narratives']):,}",
                  help="LLM narratives are intentionally generated for the hero team only.")
        st.metric("CI briefs", f"{int(n['ci_briefs']):,}")
        st.caption(f"out of {int(n['total_teams']):,} teams total")

st.divider()

# -- How to regenerate --------------------------------------------------------

st.subheader("How to refresh each pipeline stage")
st.markdown("""
| Stage | Command |
|-------|---------|
| Collect raw game + promo data | `python scripts/collect_all.py` |
| Enrich promos with LLM        | `python scripts/enrich_promotions.py` |
| Build feature table           | `python scripts/build_features.py --force` |
| Promo lift analysis           | `python scripts/analyze_promo_lift.py --force` |
| Peer clustering               | `python scripts/cluster_peers.py --force` |
| Promo strategy clustering     | `python scripts/cluster_promo_strategy.py --force` |
| Train attendance models       | `python scripts/train_attendance_model.py` |
| Generate recommendations      | `python scripts/generate_recommendations.py --force` |
| Competitive intel             | `python scripts/build_competitive_intel.py --force` |
| LLM narratives (hero + groups)| `python scripts/generate_narratives.py --force` |
""")


render_footer()
