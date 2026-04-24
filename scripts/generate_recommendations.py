"""Generate per-team actionable recommendations from analytics pipeline outputs.

Synthesizes promo lift, peer clusters, XGBoost predictions, and scheduling
patterns into prioritized, evidence-backed recommendations per team.

Usage:
    python scripts/generate_recommendations.py            # normal run
    python scripts/generate_recommendations.py --force     # rebuild even if data unchanged
    python scripts/generate_recommendations.py --team 505  # only one team
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from rich.console import Console
from rich.table import Table
from src.db.connection import engine, get_session

console = Console()

LEVEL_NAMES = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}

PROMO_LABELS = {
    "has_fireworks": "Fireworks",
    "has_giveaway": "Giveaway",
    "has_food_deal": "Food Deal",
    "has_ticket_deal": "Ticket Deal",
    "has_theme_night": "Theme Night",
    "has_kids_event": "Kids Event",
    "has_heritage": "Heritage Night",
    "has_community": "Community Event",
    "has_entertain": "Entertainment",
    "has_dog": "Dog Friendly",
    "has_celebrity": "Celebrity",
    "has_recurring": "Recurring",
}


def should_run(force: bool) -> bool:
    if force:
        return True
    with engine.connect() as conn:
        last = conn.execute(text("""
            SELECT input_max_updated FROM milb.analysis_runs
            WHERE analysis_name = 'recommendations' AND status = 'completed'
            ORDER BY completed_at DESC LIMIT 1
        """)).fetchone()
        if last is None:
            return True
        current = conn.execute(text("""
            SELECT GREATEST(
                (SELECT MAX(computed_at) FROM milb.promo_lift),
                (SELECT MAX(computed_at) FROM milb.promo_lift_cf),
                (SELECT MAX(computed_at) FROM milb.team_clusters),
                (SELECT MAX(computed_at) FROM milb.team_promo_clusters),
                (SELECT MAX(created_at) FROM milb.model_runs),
                (SELECT MAX(created_at) FROM milb.game_features)
            )
        """)).fetchone()
        return current[0] is None or last[0] is None or current[0] > last[0]


def log_run_start(session) -> int:
    result = session.execute(text("""
        INSERT INTO milb.analysis_runs (analysis_name, input_max_updated, status)
        VALUES ('recommendations', NOW(), 'running')
        RETURNING run_id
    """))
    session.commit()
    return result.fetchone()[0]


def load_team_info() -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT t.team_id, t.team_name, t.sport_id,
               s.sport_name AS level_name,
               tc.cluster_id, tc.cluster_label,
               v.capacity,
               vd.msa_population, vd.place_population,
               vd.msa_median_income
        FROM milb.teams t
        JOIN milb.sports s ON t.sport_id = s.sport_id
        LEFT JOIN milb.team_clusters tc ON t.team_id = tc.team_id
        LEFT JOIN milb.venues v ON t.venue_id = v.venue_id
        LEFT JOIN LATERAL (
            SELECT * FROM milb.venue_demographics vd2
            WHERE vd2.venue_id = v.venue_id
            ORDER BY vd2.census_year DESC LIMIT 1
        ) vd ON TRUE
        WHERE t.sport_id IN (11,12,13,14)
    """), engine)


def load_game_features() -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT gf.*, gp.predicted_attendance, gp.residual
        FROM milb.game_features gf
        LEFT JOIN milb.game_predictions gp ON gf.game_pk = gp.game_pk
    """), engine)


def load_promo_lift() -> pd.DataFrame:
    """Legacy OLS lift (kept for any diagnostic consumer; not used by recs)."""
    return pd.read_sql(text("SELECT * FROM milb.promo_lift"), engine)


def load_promo_lift_cf() -> pd.DataFrame:
    """Counterfactual promo lift (S-learner over trained XGBoost models).

    Primary rec input. Columns: team_id (NULL for league/level), sport_id,
    scope ('league'|'level'|'team'), promo_type, estimand ('ATE'|'ATT'|'ATU'),
    mean_lift, median_lift, std_lift, p10_lift, p90_lift, mean_pct_lift,
    pct_positive, n_games.
    """
    return pd.read_sql(text("SELECT * FROM milb.promo_lift_cf"), engine)


def _cf_lookup(cf: pd.DataFrame, team_id: int, sport_id: int, promo: str,
               estimand: str = "ATE") -> pd.Series | None:
    """Return one CF row for (team, promo, estimand), falling back from team
    -> level if no team-scoped row exists."""
    team_rows = cf[(cf["team_id"] == team_id) & (cf["scope"] == "team")
                   & (cf["promo_type"] == promo) & (cf["estimand"] == estimand)]
    if not team_rows.empty:
        return team_rows.iloc[0]
    level_rows = cf[(cf["sport_id"] == sport_id) & (cf["scope"] == "level")
                    & (cf["promo_type"] == promo) & (cf["estimand"] == estimand)]
    if not level_rows.empty:
        return level_rows.iloc[0]
    return None


def load_cluster_benchmarks() -> pd.DataFrame:
    return pd.read_sql(text("SELECT * FROM milb.cluster_benchmarks"), engine)


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


DOW_NAMES = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
             4: "Friday", 5: "Saturday", 6: "Sunday"}

# Map v_team_promo_dayofweek column prefixes (PG DOW) to Python dayofweek (0=Mon)
DOW_COL_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


# -- Recommendation generators -----------------------------------------------

def promo_roi_recs(team_id: int, team_info: pd.Series, promo_lift_cf: pd.DataFrame,
                   features: pd.DataFrame, season: int) -> list[dict]:
    """Promotion ROI recommendations from the counterfactual S-learner.

    Uses `milb.promo_lift_cf` (ATE / ATT / ATU per scope+flag) instead of the
    OLS-based `milb.promo_lift` table. The old OLS version generated "Re-evaluate
    X" recs on negative marginal_lift, but those negatives were mostly selection
    bias (rescue promos deployed on already-weak slots). The CF method absorbs
    that through the trained model's controls, so negatives collapse -- we no
    longer generate "Re-evaluate" recs.

    Thresholds (tuned on 2025 data):
        - mean_lift >= 50 fans   -> worth recommending to boost
        - pct_positive >= 0.60  -> consistent direction across games
        - current usage < 30%   -> room to add more
    """
    recs = []
    sid = int(team_info["sport_id"])

    team_games = features[
        (features["team_id"] == team_id) & (features["season"] == season)
    ]
    total_games = len(team_games)
    if total_games == 0:
        return recs

    capacity = team_info.get("capacity")

    positive_candidates = []
    for promo in PROMO_LABELS.keys():
        row = _cf_lookup(promo_lift_cf, team_id, sid, promo, estimand="ATE")
        if row is None:
            continue
        lift_val = float(row["mean_lift"])
        pct_pos = float(row["pct_positive"])
        if lift_val >= 50 and pct_pos >= 0.60:
            positive_candidates.append((promo, row))

    positive_candidates.sort(key=lambda p: p[1]["mean_lift"], reverse=True)

    for promo, row in positive_candidates:
        label = PROMO_LABELS.get(promo, promo)
        lift_val = float(row["mean_lift"])
        pct_pos = float(row["pct_positive"])
        pct_lift = float(row["mean_pct_lift"]) if pd.notna(row.get("mean_pct_lift")) else None
        scope_label = row["scope"]  # 'team' or 'level'

        games_with = int(team_games[promo].sum()) if promo in team_games.columns else 0
        pct_used = games_with / total_games if total_games else 0

        if pct_used < 0.30:
            target_games = int(total_games * 0.30) - games_with
            if target_games > 0:
                impact = int(lift_val * target_games)
                cap_str = (f" ({lift_val / capacity:.1%} of capacity)"
                           if pd.notna(capacity) and capacity > 0 else "")
                pct_str = f" ({pct_lift*100:+.1f}%)" if pct_lift is not None else ""
                recs.append({
                    "team_id": team_id,
                    "season": season,
                    "category": "promo_roi",
                    "priority": 1,
                    "title": f"Increase {label} frequency",
                    "detail": (
                        f"{label} adds ~{lift_val:+,.0f} fans per game{cap_str}{pct_str} "
                        f"({scope_label} scope counterfactual, positive in "
                        f"{pct_pos*100:.0f}% of modeled games). "
                        f"Currently used {games_with}/{total_games} games ({pct_used:.0%}). "
                        f"Adding ~{target_games} more {label.lower()} nights could add "
                        f"~{impact:,} total fans."
                    ),
                    "expected_impact": impact,
                    "confidence": "high" if pct_pos >= 0.80 else "medium",
                    "evidence": {
                        "promo_type": promo,
                        "mean_lift": round(lift_val, 1),
                        "pct_positive": round(pct_pos, 3),
                        "mean_pct_lift": round(pct_lift, 4) if pct_lift is not None else None,
                        "current_usage_pct": round(pct_used, 3),
                        "scope": scope_label,
                        "method": "counterfactual_s_learner",
                    },
                })

    return recs


def peer_gap_recs(team_id: int, team_info: pd.Series, features: pd.DataFrame,
                  benchmarks: pd.DataFrame, teams_info: pd.DataFrame, season: int) -> list[dict]:
    """Peer comparison gap analysis."""
    recs = []
    cluster_id = team_info.get("cluster_id")
    if pd.isna(cluster_id):
        return recs
    cluster_id = int(cluster_id)
    cluster_label = team_info.get("cluster_label", f"Cluster {cluster_id}")

    # Get benchmarks for this cluster
    cb = benchmarks[benchmarks["cluster_id"] == cluster_id]
    if cb.empty:
        return recs

    bm = {row["metric_name"]: float(row["metric_value"]) for _, row in cb.iterrows()}

    # Team's season stats
    team_games = features[
        (features["team_id"] == team_id) & (features["season"] == season)
    ]
    if team_games.empty:
        return recs

    team_avg_att = team_games["attendance"].mean()
    team_cap_util = team_games["capacity_utilization"].mean() if "capacity_utilization" in team_games else None
    team_promo_rate = team_games["has_any_promo"].mean() if "has_any_promo" in team_games else None

    # Capacity utilization gap (PRIMARY comparison -- normalizes for venue size)
    peer_cap = bm.get("capacity_utilization", 0)
    capacity = team_info.get("capacity")
    if team_cap_util is not None and peer_cap > 0 and team_cap_util < peer_cap - 0.05:
        gap_pct = peer_cap - team_cap_util
        extra_fans = int(gap_pct * capacity) if pd.notna(capacity) and capacity > 0 else None
        recs.append({
            "team_id": team_id,
            "season": season,
            "category": "peer_gap",
            "priority": 1,
            "title": f"Capacity utilization {gap_pct:.0%} below peers",
            "detail": (
                f"Your capacity utilization ({team_cap_util:.1%}) trails the "
                f"{cluster_label} average ({peer_cap:.1%}) by {gap_pct:.1%}. "
                + (f"Closing the gap means ~{extra_fans:,} additional fans per game. " if extra_fans else "")
                + "Focus on converting low-attendance weekday games."
            ),
            "expected_impact": extra_fans * len(team_games) if extra_fans else None,
            "confidence": "high",
            "evidence": {
                "team_cap_util": round(float(team_cap_util), 3),
                "peer_cap_util": round(peer_cap, 3),
                "cluster": cluster_label,
                "extra_fans_per_game": extra_fans,
            },
        })

    # Attendance gap (with cap util context)
    peer_avg = bm.get("avg_attendance", 0)
    if peer_avg > 0 and team_avg_att < peer_avg * 0.85:
        gap = int(peer_avg - team_avg_att)
        cap_util_str = f" ({team_cap_util:.1%} cap util)" if team_cap_util is not None else ""
        recs.append({
            "team_id": team_id,
            "season": season,
            "category": "peer_gap",
            "priority": 2,
            "title": f"Attendance {gap:,} below peer average",
            "detail": (
                f"Your avg attendance ({team_avg_att:,.0f}{cap_util_str}) is {gap:,} below "
                f"the {cluster_label} peer cluster average ({peer_avg:,.0f}). "
                f"Closing this gap across {len(team_games)} home games = "
                f"+{gap * len(team_games):,} total fans."
            ),
            "expected_impact": gap * len(team_games),
            "confidence": "high",
            "evidence": {
                "team_avg": round(team_avg_att),
                "peer_avg": round(peer_avg),
                "gap": gap,
                "cluster": cluster_label,
            },
        })

    # Promo rate gap
    peer_promo = bm.get("promo_rate", 0)
    if team_promo_rate is not None and peer_promo > 0 and team_promo_rate < peer_promo - 0.15:
        recs.append({
            "team_id": team_id,
            "season": season,
            "category": "peer_gap",
            "priority": 2,
            "title": "Below-peer promotion frequency",
            "detail": (
                f"Your promo rate ({team_promo_rate:.0%} of games) is below "
                f"the {cluster_label} average ({peer_promo:.0%}). "
                f"Peer teams are running promotions more frequently."
            ),
            "expected_impact": None,
            "confidence": "medium",
            "evidence": {
                "team_promo_rate": round(float(team_promo_rate), 3),
                "peer_promo_rate": round(peer_promo, 3),
            },
        })

    # Day-of-week gap analysis
    dow_names = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
                 4: "Friday", 5: "Saturday", 6: "Sunday"}
    if "day_of_week" in team_games.columns:
        dow_avg = team_games.groupby("day_of_week")["attendance"].mean()
        team_mean = team_games["attendance"].mean()
        worst_days = dow_avg[dow_avg < team_mean * 0.75].sort_values()
        if not worst_days.empty:
            day_list = ", ".join(dow_names.get(int(d), str(d)) for d in worst_days.index[:3])
            worst_pct = (1 - worst_days.iloc[0] / team_mean) if team_mean > 0 else 0
            recs.append({
                "team_id": team_id,
                "season": season,
                "category": "peer_gap",
                "priority": 3,
                "title": f"Weak attendance on {day_list}",
                "detail": (
                    f"Attendance on {day_list} is {worst_pct:.0%} below your season average. "
                    f"Target these days with your highest-impact promotions."
                ),
                "expected_impact": None,
                "confidence": "medium",
                "evidence": {
                    "weak_days": {dow_names.get(int(d), str(d)): round(float(v))
                                  for d, v in worst_days.items()},
                    "team_mean": round(team_mean),
                },
            })

    return recs


def scheduling_recs(team_id: int, features: pd.DataFrame, season: int) -> list[dict]:
    """Scheduling pattern recommendations."""
    recs = []
    team_games = features[
        (features["team_id"] == team_id) & (features["season"] == season)
    ]
    if len(team_games) < 20:
        return recs

    # Homestand fatigue
    if "homestand_game_number" in team_games.columns:
        hs = team_games.dropna(subset=["homestand_game_number"])
        if len(hs) > 20:
            early = hs[hs["homestand_game_number"] <= 2]["attendance"].mean()
            late = hs[hs["homestand_game_number"] >= 5]["attendance"].mean()
            if early > 0 and late > 0 and late < early * 0.82:
                drop_pct = (early - late) / early
                recs.append({
                    "team_id": team_id,
                    "season": season,
                    "category": "scheduling",
                    "priority": 2,
                    "title": f"Homestand fatigue: {drop_pct:.0%} attendance drop",
                    "detail": (
                        f"Games 5+ in a homestand average {late:,.0f} fans vs "
                        f"{early:,.0f} for games 1-2 (a {drop_pct:.0%} drop). "
                        f"Consider shorter homestands or saving top promotions for late-homestand games."
                    ),
                    "expected_impact": None,
                    "confidence": "medium",
                    "evidence": {
                        "early_avg": round(early),
                        "late_avg": round(late),
                        "drop_pct": round(float(drop_pct), 3),
                    },
                })

    # School calendar effect
    if "school_in_session" in team_games.columns:
        school = team_games.dropna(subset=["school_in_session"])
        if len(school) > 20:
            in_sess = school[school["school_in_session"] == True]["attendance"].mean()  # noqa: E712
            out_sess = school[school["school_in_session"] == False]["attendance"].mean()  # noqa: E712
            if in_sess > 0 and out_sess > 0 and out_sess > in_sess * 1.15:
                boost = (out_sess - in_sess) / in_sess
                recs.append({
                    "team_id": team_id,
                    "season": season,
                    "category": "scheduling",
                    "priority": 3,
                    "title": f"School break boost: +{boost:.0%}",
                    "detail": (
                        f"Attendance averages {out_sess:,.0f} during school breaks vs "
                        f"{in_sess:,.0f} during school session (+{boost:.0%}). "
                        f"Front-load premium promotions to summer break."
                    ),
                    "expected_impact": None,
                    "confidence": "medium",
                    "evidence": {
                        "school_in_avg": round(in_sess),
                        "school_out_avg": round(out_sess),
                    },
                })

    return recs


def anomaly_recs(team_id: int, features: pd.DataFrame, season: int) -> list[dict]:
    """Flag games that significantly over/under-performed predictions."""
    recs = []
    team_games = features[
        (features["team_id"] == team_id) & (features["season"] == season)
    ].copy()

    if "residual" not in team_games.columns or team_games["residual"].isna().all():
        return recs

    valid = team_games.dropna(subset=["residual"])
    if len(valid) < 10:
        return recs

    std_resid = valid["residual"].std()
    if std_resid == 0:
        return recs

    # Under-performers (residual < -2*std)
    threshold = -2 * std_resid
    under = valid[valid["residual"] < threshold].sort_values("residual")
    if len(under) >= 2:
        dates = under["game_date"].astype(str).tolist()[:5]
        avg_miss = under["residual"].mean()
        recs.append({
            "team_id": team_id,
            "season": season,
            "category": "anomaly",
            "priority": 3,
            "title": f"{len(under)} games significantly underperformed",
            "detail": (
                f"{len(under)} games had attendance far below model predictions "
                f"(avg {avg_miss:+,.0f} fans vs predicted). "
                f"Dates: {', '.join(dates)}. "
                f"Investigate for operational issues (weather alerts, road closures, competing events)."
            ),
            "expected_impact": None,
            "confidence": "low",
            "evidence": {
                "underperforming_dates": dates,
                "avg_residual": round(float(avg_miss)),
                "threshold": round(float(threshold)),
            },
        })

    # Over-performers
    threshold_hi = 2 * std_resid
    over = valid[valid["residual"] > threshold_hi].sort_values("residual", ascending=False)
    if len(over) >= 2:
        dates = over["game_date"].astype(str).tolist()[:5]
        avg_beat = over["residual"].mean()
        recs.append({
            "team_id": team_id,
            "season": season,
            "category": "anomaly",
            "priority": 4,
            "title": f"{len(over)} games significantly overperformed",
            "detail": (
                f"{len(over)} games beat predictions by avg +{avg_beat:,.0f} fans. "
                f"Dates: {', '.join(dates)}. "
                f"Study what made these games successful and replicate."
            ),
            "expected_impact": None,
            "confidence": "low",
            "evidence": {
                "overperforming_dates": dates,
                "avg_residual": round(float(avg_beat)),
            },
        })

    return recs


def promo_strategy_recs(team_id: int, team_info: pd.Series, promo_strategy: pd.DataFrame,
                        promo_lift_cf: pd.DataFrame, features: pd.DataFrame,
                        season: int) -> list[dict]:
    """Promo strategy cluster-aware recs: promo_peer, strategy_mismatch, cluster_opportunity."""
    recs = []
    team_strat = promo_strategy[promo_strategy["team_id"] == team_id]
    if team_strat.empty:
        return recs

    ts = team_strat.iloc[0]
    cluster_id = ts["promo_cluster_id"]
    cluster_label = ts["promo_cluster_label"]
    cluster_peers = promo_strategy[promo_strategy["promo_cluster_id"] == cluster_id]

    team_games = features[(features["team_id"] == team_id) & (features["season"] == season)]
    if team_games.empty:
        return recs

    team_cap_util = team_games["capacity_utilization"].mean() if "capacity_utilization" in team_games else None

    # 1. Promo peer comparison (cap util focused)
    peer_team_ids = set(cluster_peers["team_id"]) - {team_id}
    peer_games = features[(features["team_id"].isin(peer_team_ids)) & (features["season"] == season)]
    if not peer_games.empty and team_cap_util is not None:
        peer_avg_cap = peer_games.groupby("team_id")["capacity_utilization"].mean().mean()
        if peer_avg_cap > 0 and team_cap_util < peer_avg_cap - 0.10:
            gap_pct = peer_avg_cap - team_cap_util
            recs.append({
                "team_id": team_id,
                "season": season,
                "category": "promo_peer",
                "priority": 2,
                "title": f"Below promo strategy peers by {gap_pct:.0%} cap util",
                "detail": (
                    f"Among {cluster_label} teams (same promo philosophy), your capacity "
                    f"utilization ({team_cap_util:.1%}) lags the group average ({peer_avg_cap:.1%}). "
                    f"Since you share a similar promo approach, the gap likely stems from "
                    f"execution, market factors, or scheduling."
                ),
                "expected_impact": None,
                "confidence": "medium",
                "evidence": {
                    "promo_cluster": cluster_label,
                    "team_cap_util": round(float(team_cap_util), 3),
                    "peer_avg_cap_util": round(float(peer_avg_cap), 3),
                    "n_peers": len(peer_team_ids),
                },
            })

    # 2. Strategy-lift mismatch (CF version)
    # Over-emphasizing a promo whose CF counterfactual says it barely moves
    # the needle. CF absorbed the OLS selection bias that used to flag things
    # as "negative lift", so the filter is now "effectively zero lift and
    # inconsistent direction" rather than "significantly negative".
    sid = int(team_info["sport_id"])
    emphasis_map = {
        "pct_giveaway": "has_giveaway",
        "pct_fireworks": "has_fireworks",
        "pct_food_deal": "has_food_deal",
        "pct_theme_night": "has_theme_night",
        "pct_kids_event": "has_kids_event",
    }

    for profile_col, promo_flag in emphasis_map.items():
        team_emphasis = float(ts.get(profile_col) or 0)
        peer_avg_emphasis = float(cluster_peers[profile_col].mean()) if profile_col in cluster_peers else 0
        if team_emphasis <= peer_avg_emphasis * 1.2 or team_emphasis <= 0.05:
            continue

        lift_row = _cf_lookup(promo_lift_cf, team_id, sid, promo_flag, estimand="ATE")
        if lift_row is None:
            continue

        lift_val = float(lift_row["mean_lift"])
        pct_pos = float(lift_row["pct_positive"])
        label = PROMO_LABELS.get(promo_flag, promo_flag)

        # Flag when the promo is effectively zero-lift AND direction is
        # ambiguous (close to a coin flip). Keeps us from calling Fireworks
        # "mismatched" just because a team over-uses it -- Fireworks lift is
        # real.
        if lift_val < 25 and pct_pos < 0.55:
            # Pick a strong alternative from the CF data to suggest shifting to
            alt_candidates = []
            for alt_promo in PROMO_LABELS.keys():
                alt_row = _cf_lookup(promo_lift_cf, team_id, sid, alt_promo, estimand="ATE")
                if alt_row is not None and float(alt_row["mean_lift"]) >= 100 \
                        and float(alt_row["pct_positive"]) >= 0.70:
                    alt_candidates.append((alt_promo, float(alt_row["mean_lift"])))
            alt_candidates.sort(key=lambda p: p[1], reverse=True)
            alt_label = PROMO_LABELS.get(alt_candidates[0][0], "") if alt_candidates else ""

            recs.append({
                "team_id": team_id,
                "season": season,
                "category": "strategy_mismatch",
                "priority": 2,
                "title": f"{label} emphasis but near-zero modeled lift",
                "detail": (
                    f"Your {cluster_label} strategy emphasizes {label.lower()} "
                    f"({team_emphasis:.0%} of promos), but the counterfactual lift "
                    f"is {lift_val:+,.0f} fans and positive in only "
                    f"{pct_pos*100:.0f}% of modeled games. "
                    + (f"Consider shifting budget toward {alt_label}." if alt_label else "")
                ),
                "expected_impact": None,
                "confidence": "medium",
                "evidence": {
                    "emphasis_pct": round(team_emphasis, 3),
                    "mean_lift": round(lift_val, 1),
                    "pct_positive": round(pct_pos, 3),
                    "promo_cluster": cluster_label,
                    "method": "counterfactual_s_learner",
                },
            })

    # 3. Cluster edge / migration suggestion
    if len(cluster_peers) >= 5:
        p75 = cluster_peers["centroid_distance"].quantile(0.75)
        if ts["centroid_distance"] > p75:
            recs.append({
                "team_id": team_id,
                "season": season,
                "category": "cluster_opportunity",
                "priority": 3,
                "title": f"Promo strategy outlier in {cluster_label}",
                "detail": (
                    f"Your promo mix is at the edge of the {cluster_label} cluster "
                    f"(distance {ts['centroid_distance']:.2f} vs cluster median "
                    f"{cluster_peers['centroid_distance'].median():.2f}). "
                    f"Consider whether your strategy is intentionally differentiated "
                    f"or could benefit from adopting more of the cluster's core traits."
                ),
                "expected_impact": None,
                "confidence": "low",
                "evidence": {
                    "centroid_distance": round(float(ts["centroid_distance"]), 4),
                    "p75_distance": round(float(p75), 4),
                    "cluster_key_traits": ts.get("key_traits", ""),
                },
            })

    return recs


def dow_strategy_recs(team_id: int, promo_dow: pd.DataFrame,
                      features: pd.DataFrame, season: int) -> list[dict]:
    """Day-of-week promo gap recommendations."""
    recs = []
    team_dow = promo_dow[promo_dow["team_id"] == team_id]
    if team_dow.empty:
        return recs

    td = team_dow.iloc[0]
    total_promos = td.get("total_promos", 0)
    if not total_promos or total_promos < 10:
        return recs

    team_games = features[(features["team_id"] == team_id) & (features["season"] == season)]
    if len(team_games) < 20:
        return recs

    dow_att = team_games.groupby("day_of_week")["attendance"].mean()
    team_mean = team_games["attendance"].mean()

    for day_prefix, py_dow in DOW_COL_MAP.items():
        pct_col = f"pct_{day_prefix}"
        promo_pct = float(td.get(pct_col) or 0)
        day_att = dow_att.get(py_dow, team_mean)
        day_name = DOW_NAMES[py_dow]

        if promo_pct < 0.10 and day_att < team_mean * 0.80:
            att_gap_pct = (team_mean - day_att) / team_mean
            recs.append({
                "team_id": team_id,
                "season": season,
                "category": "dow_strategy",
                "priority": 2,
                "title": f"Add promotions on {day_name}s",
                "detail": (
                    f"{day_name} accounts for only {promo_pct:.0%} of your promotions "
                    f"but attendance is {att_gap_pct:.0%} below your season average "
                    f"({day_att:,.0f} vs {team_mean:,.0f}). "
                    f"Consider adding a recurring {day_name} promotion."
                ),
                "expected_impact": None,
                "confidence": "medium",
                "evidence": {
                    "day": day_name,
                    "promo_share": round(promo_pct, 3),
                    "day_avg_attendance": round(float(day_att)),
                    "team_avg_attendance": round(float(team_mean)),
                },
            })

    return recs


def missing_promo_opportunity_recs(team_id: int, team_info: pd.Series,
                                   promo_lift_cf: pd.DataFrame,
                                   features: pd.DataFrame,
                                   promo_strategy: pd.DataFrame,
                                   season: int) -> list[dict]:
    """Flag promo types the team under-uses vs its promo-cluster peers when
    the CF says adding that promo would likely help.

    Triggers when:
      - peer-cluster average usage of the flag is >= 10% (roughly the league
        median for most promo types, tuned to real v_team_promo_profile data)
      - team usage is less than 75% of peer-cluster average (i.e. meaningfully
        under-indexing vs the peer norm)
      - CF ATU (what-if-flag-on for the games where flag was off) says mean_lift
        >= 50 and pct_positive >= 0.55 at the team's scope or level
    """
    recs = []
    ts_rows = promo_strategy[promo_strategy["team_id"] == team_id]
    if ts_rows.empty:
        return recs
    ts = ts_rows.iloc[0]
    cluster_label = ts.get("promo_cluster_label", "")
    cluster_peers = promo_strategy[
        (promo_strategy["promo_cluster_id"] == ts["promo_cluster_id"])
        & (promo_strategy["team_id"] != team_id)
    ]
    if cluster_peers.empty:
        return recs

    team_games = features[(features["team_id"] == team_id) & (features["season"] == season)]
    total_games = len(team_games)
    if total_games == 0:
        return recs

    sid = int(team_info["sport_id"])

    # Which v_team_promo_profile cols map to which has_X flag
    emphasis_map = {
        "pct_fireworks":   "has_fireworks",
        "pct_giveaway":    "has_giveaway",
        "pct_food_deal":   "has_food_deal",
        "pct_theme_night": "has_theme_night",
        "pct_kids_event":  "has_kids_event",
    }

    for profile_col, promo_flag in emphasis_map.items():
        team_emphasis = float(ts.get(profile_col) or 0)
        peer_avg = float(cluster_peers[profile_col].mean()) if profile_col in cluster_peers else 0
        if peer_avg < 0.10:
            continue  # peers don't lean into this either -- no gap to close
        if team_emphasis >= peer_avg * 0.75:
            continue  # using it close to the peer norm -- not a real gap

        # CF ATU = effect on games where flag was OFF, if we turned it on.
        # Exactly the right estimand for "what if we added more of this".
        cf_row = _cf_lookup(promo_lift_cf, team_id, sid, promo_flag, estimand="ATU")
        if cf_row is None:
            continue
        lift_val = float(cf_row["mean_lift"])
        pct_pos = float(cf_row["pct_positive"])
        if lift_val < 50 or pct_pos < 0.55:
            continue

        label = PROMO_LABELS.get(promo_flag, promo_flag)
        games_with = int(team_games[promo_flag].sum()) if promo_flag in team_games.columns else 0
        pct_used = games_with / total_games
        # How many games would get you to peer average?
        target_games = max(0, int(total_games * peer_avg) - games_with)
        impact = int(lift_val * target_games)

        recs.append({
            "team_id": team_id,
            "season": season,
            "category": "missing_promo_opportunity",
            "priority": 2,
            "title": f"Under-use of {label} vs {cluster_label} peers",
            "detail": (
                f"Your {cluster_label} peers run {label.lower()} on "
                f"{peer_avg*100:.0f}% of games; you run it on {pct_used*100:.0f}% "
                f"({games_with}/{total_games}). The counterfactual says adding "
                f"{label.lower()} to your off-nights would add ~{lift_val:+,.0f} fans per game "
                f"(positive in {pct_pos*100:.0f}% of modeled games). "
                f"Matching peer frequency would mean ~{target_games} more "
                f"{label.lower()} nights, ~{impact:,} total fans."
            ),
            "expected_impact": impact if impact > 0 else None,
            "confidence": "medium" if pct_pos >= 0.65 else "low",
            "evidence": {
                "promo_type": promo_flag,
                "team_usage_pct": round(pct_used, 3),
                "peer_avg_usage_pct": round(peer_avg, 3),
                "cf_mean_lift_atu": round(lift_val, 1),
                "cf_pct_positive_atu": round(pct_pos, 3),
                "promo_cluster": cluster_label,
                "method": "counterfactual_s_learner_ATU",
            },
        })

    return recs


def generate_for_team(team_id: int, team_info: pd.Series, features: pd.DataFrame,
                      promo_lift_cf: pd.DataFrame, benchmarks: pd.DataFrame,
                      teams_info: pd.DataFrame,
                      promo_strategy: pd.DataFrame, promo_dow: pd.DataFrame) -> list[dict]:
    """Generate all recommendation types for one team."""
    season = int(features[features["team_id"] == team_id]["season"].max())

    all_recs = []
    all_recs.extend(promo_roi_recs(team_id, team_info, promo_lift_cf, features, season))
    all_recs.extend(peer_gap_recs(team_id, team_info, features, benchmarks, teams_info, season))
    all_recs.extend(scheduling_recs(team_id, features, season))
    all_recs.extend(anomaly_recs(team_id, features, season))
    all_recs.extend(promo_strategy_recs(team_id, team_info, promo_strategy, promo_lift_cf, features, season))
    all_recs.extend(missing_promo_opportunity_recs(team_id, team_info, promo_lift_cf, features, promo_strategy, season))
    all_recs.extend(dow_strategy_recs(team_id, promo_dow, features, season))

    # Re-number priorities within team (1 = highest impact)
    all_recs.sort(key=lambda r: (r["priority"], -(r["expected_impact"] or 0)))
    for i, rec in enumerate(all_recs):
        rec["priority"] = i + 1

    return all_recs


def main():
    parser = argparse.ArgumentParser(description="Generate per-team recommendations")
    parser.add_argument("--force", action="store_true", help="Rebuild even if data unchanged")
    parser.add_argument("--team", type=int, default=0, help="Generate for single team_id (0=all)")
    args = parser.parse_args()

    console.print("\n[bold blue]--- Recommendation Generator ---[/bold blue]\n")

    if not should_run(args.force):
        console.print("[green]Data unchanged since last run. Use --force to rebuild.[/green]")
        return

    session = get_session()
    run_id = log_run_start(session)

    try:
        start = time.time()

        # Load all inputs. promo_lift_cf (counterfactual) is the primary
        # signal for promo-related recs now; the OLS promo_lift is still
        # loaded for diagnostic code paths but no longer feeds recs directly.
        teams_info = load_team_info()
        features = load_game_features()
        promo_lift_cf = load_promo_lift_cf()
        benchmarks = load_cluster_benchmarks()
        promo_strategy = load_promo_strategy()
        promo_dow = load_promo_dow()

        console.print(f"Loaded: {len(teams_info)} teams, {len(features):,} game features, "
                      f"{len(promo_lift_cf)} CF lift rows, {len(benchmarks)} benchmarks, "
                      f"{len(promo_strategy)} promo strategies, {len(promo_dow)} promo DOW")

        # Filter to teams that have game_features data
        teams_with_data = set(features["team_id"].unique())
        teams_to_process = teams_info[teams_info["team_id"].isin(teams_with_data)]

        if args.team:
            teams_to_process = teams_to_process[teams_to_process["team_id"] == args.team]

        console.print(f"Generating recommendations for {len(teams_to_process)} teams...\n")

        all_recs = []
        for _, team_row in teams_to_process.iterrows():
            tid = int(team_row["team_id"])
            recs = generate_for_team(tid, team_row, features, promo_lift_cf, benchmarks,
                                    teams_info, promo_strategy, promo_dow)
            all_recs.extend(recs)

        console.print(f"\nTotal recommendations: {len(all_recs):,}")

        # Category breakdown
        if all_recs:
            recs_df = pd.DataFrame(all_recs)
            cat_counts = recs_df["category"].value_counts()
            for cat, count in cat_counts.items():
                console.print(f"  {cat}: {count}")

        # Write to DB
        if all_recs:
            console.print(f"\n[bold yellow]Writing {len(all_recs):,} recommendations to DB...[/bold yellow]")
            out_df = pd.DataFrame(all_recs)
            out_df["run_id"] = run_id
            out_df["computed_at"] = pd.Timestamp.now()

            # Convert evidence dict to JSON string
            out_df["evidence"] = out_df["evidence"].apply(json.dumps)

            with engine.begin() as conn:
                conn.execute(text("TRUNCATE milb.team_recommendations"))
                out_df.to_sql("team_recommendations", conn, schema="milb",
                              if_exists="append", index=False)

            console.print(f"  Wrote {len(out_df):,} rows")

        # Print sample for Binghamton
        bing_recs = [r for r in all_recs if r["team_id"] == 505]
        if bing_recs:
            console.print(f"\n[bold cyan]Binghamton Rumble Ponies ({len(bing_recs)} recommendations):[/bold cyan]")
            table = Table(show_lines=True)
            table.add_column("#", justify="right", width=3)
            table.add_column("Category", width=12)
            table.add_column("Title", width=40)
            table.add_column("Impact", justify="right", width=10)
            table.add_column("Confidence", width=10)

            for r in bing_recs[:10]:
                impact = f"+{r['expected_impact']:,}" if r["expected_impact"] else "-"
                table.add_row(
                    str(r["priority"]),
                    r["category"],
                    r["title"],
                    impact,
                    r["confidence"] or "-",
                )
            console.print(table)

        elapsed = time.time() - start
        console.print(f"\n[bold green]Done! {len(all_recs)} recommendations in {elapsed:.1f}s[/bold green]")

        # Log success
        session.execute(text("""
            UPDATE milb.analysis_runs
            SET status = 'completed', completed_at = NOW(), record_count = :n
            WHERE run_id = :rid
        """), {"n": len(all_recs), "rid": run_id})
        session.commit()

    except Exception as e:
        session.execute(text("""
            UPDATE milb.analysis_runs SET status = 'failed', completed_at = NOW(),
            error_message = :err WHERE run_id = :rid
        """), {"err": str(e), "rid": run_id})
        session.commit()
        console.print(f"[bold red]Error: {e}[/bold red]")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
