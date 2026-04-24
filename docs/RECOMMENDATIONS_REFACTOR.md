# Recommendations Engine Refactor: Promo Strategy Integration

Reference doc for enhancing `scripts/generate_recommendations.py` and
`streamlit_app/pages/7_Recommendations.py` with promo strategy cluster data.

---

## Current State

**`scripts/generate_recommendations.py`** generates 4 categories of recs per team:
1. `promo_roi` -- marginal lift from OLS regression (under-used high-lift promos, negative-lift promos)
2. `peer_gap` -- team vs market-based cluster benchmarks (attendance, cap util, promo rate, weak DOW)
3. `scheduling` -- homestand fatigue, school calendar effects
4. `anomaly` -- games that significantly over/under-performed XGBoost predictions

**`streamlit_app/pages/7_Recommendations.py`** displays these in Tab 4, with 5 total tabs:
Promotion ROI, Peer Comparison, What-If Simulator, Recommendations, Model Performance.

The peer comparison (Tab 2) uses **market-based** clusters from `milb.team_clusters`
(demographics + venue size). These answer "who are your market peers?" but say nothing
about promotional philosophy.

---

## New Data Available

### Tables
- `milb.team_promo_clusters` -- team_id, promo_cluster_id, promo_cluster_label, centroid_distance
- `milb.promo_cluster_descriptions` -- promo_cluster_id, promo_cluster_label, description, key_traits, example_teams

### Views
- `milb.v_team_promo_profile` -- one row per team with all strategy dimensions:
  promo_coverage, promos_per_game, promos_per_promo_game, pct_recurring, pct_fireworks,
  pct_giveaway, pct_food_deal, pct_ticket_deal, pct_theme_night, pct_kids_event,
  pct_heritage, pct_community, pct_entertain, pct_dog, pct_weekend_promos,
  distinct_promo_names, promo_entropy, promo_quality
- `milb.v_team_promo_intensity` -- team_id, promos_per_game, intensity_tier (High/Medium/Low/None)
- `milb.v_team_promo_dayofweek` -- per-team day-of-week promo distribution (7 count + 7 pct cols)

### Cluster Archetypes (as of April 2026, 5 clusters)
| Label | N | Key Trait |
|-------|---|-----------|
| Stack & Pack | 53 | High stacking (3+ promos/game), broad coverage |
| Value Play | 18 | Heavy food/ticket deals, price-conscious |
| Theme Park | 18 | Theme nights, entertainment-focused |
| Family Hub | 15 | Kids events, family programming |
| Weekend Warrior | 5 | Promos concentrated on Fri/Sat/Sun |

---

## Proposed New Recommendation Types

### 1. Promo Strategy Peer Comparison (`promo_peer`)

Compare a team against its **promo strategy cluster** peers (not market peers).
This answers: "Among teams with the same promotional philosophy, how does this team perform?"

**Data needed:**
```sql
-- Cluster peer benchmarks (compute in generate_recommendations.py)
SELECT pc.promo_cluster_id,
       AVG(sa.attendance_avg_home) AS cluster_avg_att,
       AVG(gf.capacity_utilization) AS cluster_avg_cap_util,
       AVG(pp.promo_coverage) AS cluster_avg_coverage,
       AVG(pp.promos_per_promo_game) AS cluster_avg_stacking
FROM milb.team_promo_clusters pc
JOIN milb.season_attendance sa ON pc.team_id = sa.team_id
JOIN milb.game_features gf ON pc.team_id = gf.team_id
JOIN milb.v_team_promo_profile pp ON pc.team_id = pp.team_id
WHERE sa.game_type_id = 'R'
GROUP BY pc.promo_cluster_id
```

**Rec logic:**
- If team attendance < cluster avg by 15%+: "Your promo strategy peers average X fans;
  you average Y. Teams in the {cluster_label} cluster with similar attendance use [specific
  differentiators from the cluster profile]."
- If team's promo coverage is 20%+ below cluster peers: "Other {cluster_label} teams run
  promos on {peer_coverage}% of games vs your {team_coverage}%."

### 2. Strategy-Lift Mismatch (`strategy_mismatch`)

Cross-reference a team's promo strategy cluster with its OLS lift results.
This catches cases where a team's strategy emphasis doesn't match what actually works.

**Logic:**
- Load team's cluster profile (which dimensions are above/below league average)
- Load team's promo_lift results (which promos have significant positive/negative lift)
- Flag mismatches:
  - Team is in "Giveaway Shop" cluster but giveaways have negative lift -> "Your strategy
    emphasizes giveaways, but they show negative lift for your team. Consider shifting
    budget to [highest-lift promo type]."
  - Team is in "Minimalist" cluster but fireworks show +500 lift -> "You run few promos,
    but fireworks show strong lift (+500 fans). Even 5 fireworks nights could add ~2,500 fans."

### 3. Cluster Migration Suggestion (`cluster_opportunity`)

When a team is at the edge of its cluster (high centroid_distance), suggest which
neighboring cluster's strategy might work better.

**Logic:**
- Teams with centroid_distance > 75th percentile of their cluster
- Compute distance to other cluster centroids
- If a neighboring cluster has higher avg attendance with similar market conditions:
  "Your promo strategy is an outlier in the {current_cluster} group. Teams in the
  {neighbor_cluster} cluster (which emphasizes {key_traits}) average {X} more fans
  in similar markets."

### 4. Day-of-Week Strategy Gaps (`dow_strategy`)

Use `v_team_promo_dayofweek` to find days where the team runs few promos but has
low attendance.

**Logic:**
```python
# From v_team_promo_dayofweek: team's promo distribution
# From game_features: team's attendance by DOW
# If a day has <10% of promos but <80% of avg attendance:
#   "Tuesdays account for only 5% of your promos but attendance is 25% below average.
#    Consider adding a recurring Tuesday promotion."
```

---

## Changes to `scripts/generate_recommendations.py`

### New imports / data loaders

```python
def load_promo_strategy() -> pd.DataFrame:
    """Team promo cluster + profile for strategy-aware recs."""
    return pd.read_sql(text("""
        SELECT pc.team_id,
               pc.promo_cluster_id,
               pc.promo_cluster_label,
               pc.centroid_distance,
               pp.promo_coverage,
               pp.promos_per_promo_game,
               pp.pct_recurring,
               pp.promo_entropy,
               pp.pct_giveaway,
               pp.pct_fireworks,
               pp.pct_food_deal,
               pp.pct_theme_night,
               pp.pct_weekend_promos,
               pp.pct_kids_event,
               cd.description AS cluster_description,
               cd.key_traits
        FROM milb.team_promo_clusters pc
        JOIN milb.v_team_promo_profile pp ON pc.team_id = pp.team_id
        LEFT JOIN milb.promo_cluster_descriptions cd
            ON pc.promo_cluster_id = cd.promo_cluster_id
    """), engine)


def load_promo_dow() -> pd.DataFrame:
    return pd.read_sql(text("SELECT * FROM milb.v_team_promo_dayofweek"), engine)
```

### New generator functions

Add these to the recommendation pipeline:

```python
def promo_strategy_recs(team_id, team_info, promo_strategy, promo_lift, features, season):
    """Promo strategy cluster-aware recommendations."""
    recs = []
    team_strat = promo_strategy[promo_strategy["team_id"] == team_id]
    if team_strat.empty:
        return recs

    ts = team_strat.iloc[0]
    cluster_id = ts["promo_cluster_id"]
    cluster_label = ts["promo_cluster_label"]

    # 1. Peer comparison within promo cluster
    cluster_peers = promo_strategy[promo_strategy["promo_cluster_id"] == cluster_id]
    # ... (compare team's attendance vs cluster peer avg)

    # 2. Strategy-lift mismatch
    team_lift = promo_lift[
        (promo_lift["team_id"] == team_id) & (promo_lift["scope"] == "team_all")
    ]
    # ... (cross-reference cluster emphasis with lift data)

    # 3. Cluster edge / migration
    if ts["centroid_distance"] > cluster_peers["centroid_distance"].quantile(0.75):
        # ... (suggest neighboring cluster's strategy)

    return recs


def dow_strategy_recs(team_id, promo_dow, features, season):
    """Day-of-week promo gap recommendations."""
    recs = []
    # ... (compare DOW promo distribution vs DOW attendance)
    return recs
```

### Wire into `generate_for_team()`

```python
def generate_for_team(team_id, team_info, features, promo_lift, benchmarks,
                      teams_info, promo_strategy, promo_dow):
    season = int(features[features["team_id"] == team_id]["season"].max())

    all_recs = []
    all_recs.extend(promo_roi_recs(...))
    all_recs.extend(peer_gap_recs(...))
    all_recs.extend(scheduling_recs(...))
    all_recs.extend(anomaly_recs(...))
    # NEW:
    all_recs.extend(promo_strategy_recs(team_id, team_info, promo_strategy, promo_lift, features, season))
    all_recs.extend(dow_strategy_recs(team_id, promo_dow, features, season))
    # ...
```

### Update `should_run()` to include promo cluster data

```python
current = conn.execute(text("""
    SELECT GREATEST(
        (SELECT MAX(computed_at) FROM milb.promo_lift),
        (SELECT MAX(computed_at) FROM milb.team_clusters),
        (SELECT MAX(computed_at) FROM milb.team_promo_clusters),  -- NEW
        (SELECT MAX(created_at) FROM milb.model_runs),
        (SELECT MAX(created_at) FROM milb.game_features)
    )
""")).fetchone()
```

---

## Changes to `streamlit_app/pages/7_Recommendations.py`

### Add promo strategy context to page header

When a team is selected, show their promo cluster alongside their market cluster:

```python
# After existing cluster_info line (~line 224):
promo_cluster = load_promo_cluster_info()  # reuse from 2_Promotions pattern
team_promo = promo_cluster[promo_cluster["team_id"] == selected_team_id]
if not team_promo.empty:
    promo_label = team_promo.iloc[0]["promo_cluster_label"]
    st.caption(f"Promo strategy: **{promo_label}**")
```

### Add new category labels

```python
CATEGORY_LABELS = {
    "promo_roi": "Promotion ROI",
    "peer_gap": "Peer Gap",
    "scheduling": "Scheduling",
    "anomaly": "Anomaly",
    "promo_peer": "Promo Strategy Peers",       # NEW
    "strategy_mismatch": "Strategy Mismatch",    # NEW
    "cluster_opportunity": "Cluster Opportunity", # NEW
    "dow_strategy": "Day-of-Week Strategy",      # NEW
}
```

### Enhance Peer Comparison tab (Tab 2)

Add a toggle or second section showing promo strategy peers alongside market peers:

```python
with tab_peer:
    peer_type = st.radio("Peer type", ["Market Peers", "Promo Strategy Peers"], horizontal=True)

    if peer_type == "Market Peers":
        # ... existing market cluster comparison code ...
    else:
        # Load promo cluster peers
        # Show bar chart ranked by attendance, colored by selected team
        # Show promo profile comparison (radar chart like 7_Promo_Strategy.py Tab 2)
```

---

## Execution Order

1. Add `load_promo_strategy()` and `load_promo_dow()` to `generate_recommendations.py`
2. Implement `promo_strategy_recs()` and `dow_strategy_recs()`
3. Wire into `generate_for_team()` and update `should_run()`
4. Run `python scripts/generate_recommendations.py --force`
5. Update `7_Recommendations.py` with new categories and promo peer comparison
6. Verify all 5 tabs still work, new rec categories appear in Tab 4

---

## Notes

- The promo strategy page is `streamlit_app/pages/7_Promo_Strategy.py` (shares number 7
  with Recommendations -- may want to renumber to 8)
- The 2_Promotions.py page now shows a promo cluster context banner when a team is selected
- Home.py map now has a "Promo strategy cluster" color mode
- Cluster assignments come from `scripts/cluster_promo_strategy.py` (K-Means, 10 features,
  silhouette-optimized). Rerun after new promo data is collected.
- The `promo_quality` field in `v_team_promo_profile` filters out teams with <10 promos
  from clustering. These teams won't have promo strategy recs.
