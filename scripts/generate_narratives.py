"""Generate LLM executive narrative summaries for team reports.

Uses local Ollama to produce structured narrative briefs from
recommendation data, peer comparisons, and season stats.

Usage:
    python scripts/generate_narratives.py                  # Binghamton + group rollups
    python scripts/generate_narratives.py --team 505       # specific team
    python scripts/generate_narratives.py --force           # regenerate even if exists
    python scripts/generate_narratives.py --model llama3.2  # different Ollama model
    python scripts/generate_narratives.py --competitive-intel          # CI for Binghamton
    python scripts/generate_narratives.py --competitive-intel --all-teams  # CI for all teams

Prerequisites:
    psql -f sql/010_add_narratives.sql   (run once first)
    psql -f sql/011_add_competitive_intel.sql  (for --competitive-intel)
    Ollama running: ollama serve
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import pandas as pd
from rich.console import Console
from rich.progress import Progress
from sqlalchemy import text

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from src.db.connection import engine, get_session
from src.utils.logger import get_logger

logger = get_logger("generate_narratives")
console = Console()

OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:8b"
BINGHAMTON_ID = 505

LEVEL_NAMES = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}

PROMO_LABELS = {
    "has_fireworks": "Fireworks", "has_giveaway": "Giveaway",
    "has_food_deal": "Food Deal", "has_ticket_deal": "Ticket Deal",
    "has_theme_night": "Theme Night", "has_kids_event": "Kids Event",
    "has_heritage": "Heritage Night", "has_community": "Community Event",
    "has_entertain": "Entertainment", "has_dog": "Dog Friendly",
    "has_celebrity": "Celebrity", "has_recurring": "Recurring",
}

TEAM_SYSTEM_PROMPT = """You are a Minor League Baseball analytics consultant writing an executive briefing for a team's front office. Given JSON data about one team's performance, recommendations, and peer comparisons, produce a structured JSON response.

The response MUST contain exactly these keys:
- "executive_summary": 2-3 paragraphs of narrative text (under 300 words). Lead with the most impactful finding. Reference specific numbers. Write in professional but accessible language -- no jargon, no model names, no statistical terms.
- "kpis": array of exactly 3-4 KPI objects, each with: "label" (short name), "value" (formatted string with commas/%), "trend" (one of "up", "down", "stable"), "context" (brief comparison, e.g. "vs 2,890 last season" or "peer avg: 71%")
- "goals": array of 2-3 goal objects, each with: "goal" (specific actionable sentence), "metric" (snake_case metric name), "target" (number), "current" (number)
- "key_risks": array of 1-3 short risk sentences (factors outside the team's control)

Rules:
- KPIs should cover: attendance, capacity utilization, and one promotion-related metric
- Goals should be specific, measurable, achievable within one season
- Risks should focus on external factors (demographics, weather patterns, competition)
- Do NOT mention machine learning, XGBoost, SHAP, OLS, or any model internals
- Return ONLY the JSON object, no markdown or explanation"""

GROUP_SYSTEM_PROMPT = """You are a Minor League Baseball analytics consultant writing a group-level overview. Given JSON data about a group of teams (a classification level, peer cluster, or the whole league), produce a structured JSON response.

The response MUST contain exactly these keys:
- "executive_summary": 2-3 paragraphs summarizing trends across the group (under 250 words). Highlight top/bottom performers. Reference specific numbers.
- "kpis": array of 3-4 KPI objects, each with: "label", "value", "trend" (up/down/stable), "context"

Rules:
- Write for a non-technical executive audience
- Focus on actionable group-level insights, not individual team details
- Do NOT mention machine learning, XGBoost, SHAP, OLS, or any model internals
- Return ONLY the JSON object"""

CI_SYSTEM_PROMPT = """You are a Minor League Baseball analytics consultant writing a competitive intelligence brief for a team's front office. Given JSON data about the team's performance, weather-similar peers, momentum trends, and promotion effectiveness, produce a structured JSON response.

The response MUST contain exactly these keys:
- "executive_summary": 3-4 paragraphs of narrative text (under 400 words). Lead with the most surprising peer comparison finding. Name specific teams to emulate and explain why they are relevant peers (similar weather, market size). Highlight momentum trends. Close with 2-3 concrete promo tactics borrowed from high-performing peers.
- "kpis": array of exactly 4 KPI objects, each with: "label" (short name), "value" (formatted string), "trend" (one of "up", "down", "stable"), "context" (brief comparison)
  Must include: weather-peer rank, cap util vs peer avg, YoY change, top promo gap
- "headlines": array of 3 strings -- punchy one-line takeaways for a slide deck
- "teams_to_watch": array of 2-3 objects: "team_name" (string), "why" (1 sentence), "key_tactic" (what to borrow from them)

Rules:
- Write for a non-technical executive audience
- Name specific teams and specific promotions
- Reference numbers (attendance gains, percentages)
- Do NOT mention machine learning, models, or statistical methods
- Return ONLY the JSON object"""


# -- Ollama helpers -----------------------------------------------------------

def check_ollama(model: str) -> bool:
    """Verify Ollama is running and the model is available."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        available = [m["name"] for m in resp.json().get("models", [])]
        if not any(model in m or m.startswith(model.split(":")[0]) for m in available):
            console.print(f"[red]Model '{model}' not found. Available:[/red]")
            for m in available:
                console.print(f"  {m}")
            return False
        return True
    except httpx.ConnectError:
        console.print("[red]Cannot connect to Ollama at localhost:11434. Is it running?[/red]")
        console.print("  Start it with: ollama serve")
        return False


def call_ollama(client: httpx.Client, system_prompt: str, user_content: str,
                model: str) -> dict | None:
    """Call Ollama and return parsed JSON dict or None on failure."""
    options = {"temperature": 0.3, "num_predict": 4096}
    if "qwen3" in model:
        options["think"] = False

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "format": "json",
        "options": options,
    }

    try:
        resp = client.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=300)
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            logger.warning("Ollama returned non-dict JSON")
            return None
        return parsed
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Ollama call failed: {e}")
        return None


# -- Data loaders (all upfront for efficiency) --------------------------------

def load_all_data(session) -> dict:
    """Load all data needed for narrative generation in bulk."""
    data = {}

    data["teams"] = pd.read_sql(text("""
        SELECT t.team_id, t.team_name, t.sport_id,
               s.sport_name,
               tc.cluster_id, tc.cluster_label,
               tpc.promo_cluster_id,
               pcd.promo_cluster_label AS promo_cluster_label,
               v.capacity, v.venue_name,
               vd.msa_population, vd.msa_name, vd.msa_median_income,
               vd.msa_poverty_rate, vd.place_population
        FROM milb.teams t
        JOIN milb.sports s ON t.sport_id = s.sport_id
        LEFT JOIN milb.team_clusters tc ON t.team_id = tc.team_id
        LEFT JOIN milb.team_promo_clusters tpc ON t.team_id = tpc.team_id
        LEFT JOIN milb.promo_cluster_descriptions pcd
            ON tpc.promo_cluster_id = pcd.promo_cluster_id
        LEFT JOIN milb.venues v ON t.venue_id = v.venue_id
        LEFT JOIN LATERAL (
            SELECT * FROM milb.venue_demographics vd2
            WHERE vd2.venue_id = v.venue_id
            ORDER BY vd2.census_year DESC LIMIT 1
        ) vd ON TRUE
        WHERE t.sport_id IN (11, 12, 13, 14)
    """), engine)

    data["features"] = pd.read_sql(text("""
        SELECT team_id, season, game_pk, attendance, capacity_utilization,
               is_weekend, has_any_promo, promo_count, month, day_of_week,
               population_change_5yr_pct, income_change_5yr_pct, population_trend
        FROM milb.game_features
        WHERE attendance IS NOT NULL
    """), engine)

    data["recommendations"] = pd.read_sql(text("""
        SELECT team_id, season, priority, category, title, detail,
               expected_impact, confidence
        FROM milb.team_recommendations
        ORDER BY team_id, priority
    """), engine)

    # Primary promo-lift signal is the counterfactual S-learner table.
    # The old OLS `milb.promo_lift` table is no longer consulted here -- its
    # negatives were mostly selection bias (rescue promos deployed on weak
    # slots), and letting the LLM see them led to briefs that contradicted
    # the Recommendations page.
    data["promo_lift"] = pd.read_sql(text("""
        SELECT team_id, sport_id, scope, promo_type, estimand,
               mean_lift::float, pct_positive::float, mean_pct_lift::float,
               n_games
          FROM milb.promo_lift_cf
    """), engine)

    data["benchmarks"] = pd.read_sql(text("""
        SELECT cluster_id, metric_name, metric_value::float
        FROM milb.cluster_benchmarks
    """), engine)

    return data


# -- Context assembly ---------------------------------------------------------

def build_team_context(team_id: int, data: dict) -> dict | None:
    """Assemble all context for a single team's narrative."""
    team_row = data["teams"][data["teams"]["team_id"] == team_id]
    if team_row.empty:
        return None
    team = team_row.iloc[0]

    features = data["features"][data["features"]["team_id"] == team_id]
    if features.empty:
        return None

    latest_season = int(features["season"].max())
    season_games = features[features["season"] == latest_season]

    # Season stats
    avg_att = float(season_games["attendance"].mean())
    cap_util = float(season_games["capacity_utilization"].mean()) if "capacity_utilization" in season_games else None
    total_games = len(season_games)
    weekend_att = float(season_games[season_games["is_weekend"] == 1]["attendance"].mean()) if (season_games["is_weekend"] == 1).any() else None
    weekday_att = float(season_games[season_games["is_weekend"] == 0]["attendance"].mean()) if (season_games["is_weekend"] == 0).any() else None

    # Year-over-year
    prev_season = features[features["season"] == latest_season - 1]
    yoy_change = None
    if not prev_season.empty:
        prev_avg = float(prev_season["attendance"].mean())
        yoy_change = round((avg_att - prev_avg) / prev_avg * 100, 1) if prev_avg > 0 else None

    # Recommendations (top 10)
    recs = data["recommendations"][
        (data["recommendations"]["team_id"] == team_id) &
        (data["recommendations"]["season"] == latest_season)
    ].head(10)
    rec_list = []
    for _, r in recs.iterrows():
        rec_list.append({
            "priority": int(r["priority"]),
            "category": r["category"],
            "title": r["title"],
            "detail": r["detail"],
            "expected_impact": int(r["expected_impact"]) if pd.notna(r["expected_impact"]) else None,
            "confidence": r["confidence"],
        })

    # Promo lift from the counterfactual S-learner. We take team-scoped ATE
    # rows when available, fall back to level-scoped ATE. Only surface the
    # top positive lifts -- the CF data shows negatives as noise (collapsed
    # selection bias), so we deliberately don't feed the LLM a "these promos
    # hurt you" list anymore.
    sid = int(team["sport_id"])
    cf = data["promo_lift"]
    team_lift = cf[
        (cf["team_id"] == team_id)
        & (cf["scope"] == "team")
        & (cf["estimand"] == "ATE")
    ]
    if team_lift.empty:
        team_lift = cf[
            (cf["sport_id"] == sid)
            & (cf["scope"] == "level")
            & (cf["estimand"] == "ATE")
        ]
    top_pos = team_lift[
        (team_lift["mean_lift"] >= 50) & (team_lift["pct_positive"] >= 0.60)
    ].nlargest(3, "mean_lift")

    lifts = []
    for _, row in top_pos.iterrows():
        lifts.append({
            "promo_type": PROMO_LABELS.get(row["promo_type"], row["promo_type"]),
            "lift_fans": int(row["mean_lift"]),
            "lift_pct": round(float(row["mean_pct_lift"]) * 100, 1)
                        if pd.notna(row.get("mean_pct_lift")) else None,
            "consistency_pct": round(float(row["pct_positive"]) * 100, 0),
        })

    # Peer comparison
    cluster_id = team.get("cluster_id")
    peer_avg_att = None
    peer_cap_util = None
    if pd.notna(cluster_id):
        bm = data["benchmarks"][data["benchmarks"]["cluster_id"] == int(cluster_id)]
        bm_dict = {r["metric_name"]: r["metric_value"] for _, r in bm.iterrows()}
        peer_avg_att = bm_dict.get("avg_attendance")
        peer_cap_util = bm_dict.get("capacity_utilization")

    # Demographic trends
    pop_trend = season_games["population_trend"].mode()
    pop_change = season_games["population_change_5yr_pct"].median()

    context = {
        "team_name": team["team_name"],
        "level": team["sport_name"],
        "season": latest_season,
        "venue": team["venue_name"],
        "capacity": int(team["capacity"]) if pd.notna(team["capacity"]) else None,
        "market_cluster": team.get("cluster_label"),
        "promo_cluster": team.get("promo_cluster_label"),
        "msa_name": team.get("msa_name"),
        "msa_population": int(team["msa_population"]) if pd.notna(team.get("msa_population")) else None,
        "median_income": int(team["msa_median_income"]) if pd.notna(team.get("msa_median_income")) else None,
        "avg_attendance": round(avg_att),
        "capacity_utilization": round(cap_util, 3) if cap_util else None,
        "total_home_games": total_games,
        "weekend_avg": round(weekend_att) if weekend_att else None,
        "weekday_avg": round(weekday_att) if weekday_att else None,
        "yoy_change_pct": yoy_change,
        "population_trend": pop_trend.iloc[0] if not pop_trend.empty else None,
        "population_change_5yr_pct": round(float(pop_change), 1) if pd.notna(pop_change) else None,
        "recommendations": rec_list,
        "promo_lifts": lifts,
        "peer_avg_attendance": round(peer_avg_att) if peer_avg_att else None,
        "peer_capacity_utilization": round(peer_cap_util, 3) if peer_cap_util else None,
    }
    return context


def build_group_context(group_type: str, group_key: str, data: dict) -> dict | None:
    """Assemble context for a group-level narrative."""
    features = data["features"]
    teams = data["teams"]

    latest_season = int(features["season"].max())
    season_feat = features[features["season"] == latest_season]

    if group_type == "level":
        sid_map = {v: k for k, v in LEVEL_NAMES.items()}
        sid = sid_map.get(group_key)
        if sid is None:
            return None
        group_teams = teams[teams["sport_id"] == sid]["team_id"].tolist()
    elif group_type == "market_cluster":
        group_teams = teams[teams["cluster_label"] == group_key]["team_id"].tolist()
    elif group_type == "promo_cluster":
        group_teams = teams[teams["promo_cluster_label"] == group_key]["team_id"].tolist()
    elif group_type == "league":
        group_teams = teams["team_id"].tolist()
    else:
        return None

    group_games = season_feat[season_feat["team_id"].isin(group_teams)]
    if group_games.empty:
        return None

    # Per-team averages for ranking
    team_stats = group_games.groupby("team_id").agg(
        avg_att=("attendance", "mean"),
        avg_cap=("capacity_utilization", "mean"),
        games=("game_pk", "count"),
    ).reset_index()
    team_stats = team_stats.merge(teams[["team_id", "team_name"]], on="team_id")

    top_3 = team_stats.nlargest(3, "avg_cap")[["team_name", "avg_att", "avg_cap"]].to_dict("records")
    bot_3 = team_stats.nsmallest(3, "avg_cap")[["team_name", "avg_att", "avg_cap"]].to_dict("records")
    for row in top_3 + bot_3:
        row["avg_att"] = round(row["avg_att"])
        row["avg_cap"] = round(row["avg_cap"], 3)

    # YoY for group
    prev_feat = features[(features["season"] == latest_season - 1) & features["team_id"].isin(group_teams)]
    yoy = None
    if not prev_feat.empty:
        prev_avg = float(prev_feat["attendance"].mean())
        curr_avg = float(group_games["attendance"].mean())
        yoy = round((curr_avg - prev_avg) / prev_avg * 100, 1) if prev_avg > 0 else None

    context = {
        "group_type": group_type,
        "group_name": group_key,
        "season": latest_season,
        "team_count": len(group_teams),
        "total_games": len(group_games),
        "avg_attendance": round(float(group_games["attendance"].mean())),
        "avg_capacity_utilization": round(float(group_games["capacity_utilization"].mean()), 3),
        "yoy_change_pct": yoy,
        "top_performers": top_3,
        "bottom_performers": bot_3,
    }
    return context


# -- Competitive Intelligence context ----------------------------------------

def build_ci_context(team_id: int, data: dict) -> dict | None:
    """Assemble context for a competitive intelligence narrative."""
    team_row = data["teams"][data["teams"]["team_id"] == team_id]
    if team_row.empty:
        return None
    team = team_row.iloc[0]

    features = data["features"][data["features"]["team_id"] == team_id]
    if features.empty:
        return None
    latest_season = int(features["season"].max())

    # Team's own momentum
    momentum = data.get("momentum", pd.DataFrame())
    team_mom = momentum[(momentum["team_id"] == team_id) &
                        (momentum["season"] == latest_season)]

    # Weather peers + their momentum
    peers = data.get("weather_peers", pd.DataFrame())
    team_peers = peers[peers["team_id"] == team_id].head(10)
    peer_ids = team_peers["peer_team_id"].tolist() if not team_peers.empty else []

    peer_details = []
    for pid in peer_ids[:5]:
        pr = data["teams"][data["teams"]["team_id"] == pid]
        pm = momentum[(momentum["team_id"] == pid) & (momentum["season"] == latest_season)]
        if pr.empty:
            continue
        pr = pr.iloc[0]
        sim_row = team_peers[team_peers["peer_team_id"] == pid].iloc[0]
        detail = {
            "team_name": pr["team_name"],
            "level": LEVEL_NAMES.get(int(pr["sport_id"]), "?"),
            "similarity": round(float(sim_row["similarity_score"]), 3),
        }
        if not pm.empty:
            pm = pm.iloc[0]
            detail["avg_attendance"] = int(pm["avg_attendance"]) if pd.notna(pm.get("avg_attendance")) else None
            detail["cap_util"] = round(float(pm["avg_cap_util"]), 3) if pd.notna(pm.get("avg_cap_util")) else None
            detail["momentum"] = pm.get("momentum_label")
            detail["yoy_pct"] = round(float(pm["yoy_attendance_pct"]), 3) if pd.notna(pm.get("yoy_attendance_pct")) else None
        peer_details.append(detail)

    # Peer promo lift comparison (CF version). Same shape as before --
    # peer_avg_lift is now an average across peers' team-scoped CF ATE.
    cf = data.get("promo_lift", pd.DataFrame())
    team_lift = cf[(cf["team_id"] == team_id)
                   & (cf["scope"] == "team")
                   & (cf["estimand"] == "ATE")]
    peer_lift = cf[(cf["team_id"].isin(peer_ids))
                   & (cf["scope"] == "team")
                   & (cf["estimand"] == "ATE")
                   & (cf["mean_lift"] > 50)
                   & (cf["pct_positive"] >= 0.60)]
    peer_lift_summary = []
    if not peer_lift.empty:
        avg_by_type = peer_lift.groupby("promo_type")["mean_lift"].mean()
        for ptype, avg in avg_by_type.nlargest(5).items():
            own = team_lift[team_lift["promo_type"] == ptype]
            own_lift = int(own["mean_lift"].iloc[0]) if not own.empty else None
            peer_lift_summary.append({
                "promo_type": PROMO_LABELS.get(ptype, ptype),
                "peer_avg_lift": int(avg),
                "team_lift": own_lift,
            })

    # Weather profile
    wp = data.get("weather_profiles", pd.DataFrame())
    team_wp = wp[(wp["team_id"] == team_id) & (wp["season"] == latest_season)]

    season_games = features[features["season"] == latest_season]
    avg_att = round(float(season_games["attendance"].mean()))
    cap_util = round(float(season_games["capacity_utilization"].mean()), 3)

    context = {
        "team_name": team["team_name"],
        "level": LEVEL_NAMES.get(int(team["sport_id"]), "?"),
        "season": latest_season,
        "avg_attendance": avg_att,
        "capacity_utilization": cap_util,
        "capacity": int(team["capacity"]) if pd.notna(team.get("capacity")) else None,
        "msa_name": team.get("msa_name"),
        "msa_population": int(team["msa_population"]) if pd.notna(team.get("msa_population")) else None,
    }
    if not team_mom.empty:
        tm = team_mom.iloc[0]
        context["momentum_label"] = tm.get("momentum_label")
        context["momentum_score"] = round(float(tm["momentum_score"]), 3) if pd.notna(tm.get("momentum_score")) else None
        context["yoy_attendance_pct"] = round(float(tm["yoy_attendance_pct"]), 3) if pd.notna(tm.get("yoy_attendance_pct")) else None
        context["intra_season_trend"] = round(float(tm["intra_season_trend"]), 3) if pd.notna(tm.get("intra_season_trend")) else None
    if not team_wp.empty:
        twp = team_wp.iloc[0]
        context["avg_temp_f"] = float(twp["avg_temp_f"]) if pd.notna(twp.get("avg_temp_f")) else None
        context["pct_rain_games"] = float(twp["pct_rain_games"]) if pd.notna(twp.get("pct_rain_games")) else None

    context["weather_peers"] = peer_details
    context["promo_comparison"] = peer_lift_summary
    # Peer rank (where does this team sit among its peers by cap_util?)
    if peer_details:
        peer_caps = [p.get("cap_util", 0) for p in peer_details if p.get("cap_util")]
        better = sum(1 for pc in peer_caps if pc and pc > cap_util)
        context["peer_rank"] = f"{better + 1} of {len(peer_caps) + 1}"

    return context


def load_ci_data() -> dict:
    """Load competitive intel tables for narrative generation."""
    ci_data = {}
    try:
        ci_data["weather_peers"] = pd.read_sql(text("""
            SELECT team_id, peer_team_id, similarity_score, weather_dist, demo_dist
            FROM milb.weather_peer_similarity
            ORDER BY team_id, similarity_score DESC
        """), engine)
    except Exception:
        ci_data["weather_peers"] = pd.DataFrame()

    try:
        ci_data["momentum"] = pd.read_sql(text("""
            SELECT team_id, season, avg_attendance, avg_cap_util,
                   yoy_attendance_pct, yoy_cap_util_change,
                   intra_season_trend, momentum_label, momentum_score
            FROM milb.team_momentum
        """), engine)
    except Exception:
        ci_data["momentum"] = pd.DataFrame()

    try:
        ci_data["weather_profiles"] = pd.read_sql(text("""
            SELECT team_id, season, avg_temp_f, pct_rain_games
            FROM milb.team_weather_profile
        """), engine)
    except Exception:
        ci_data["weather_profiles"] = pd.DataFrame()

    return ci_data


# -- DB writers ---------------------------------------------------------------

def upsert_team_narrative(session, team_id: int, season: int, result: dict, model: str):
    session.execute(text("""
        INSERT INTO milb.team_narratives
            (team_id, season, narrative_text, kpi_json, goals_json, risks_json, llm_model, generated_at)
        VALUES
            (:team_id, :season, :text, CAST(:kpis AS jsonb), CAST(:goals AS jsonb),
             CAST(:risks AS jsonb), :model, NOW())
        ON CONFLICT (team_id, season) DO UPDATE SET
            narrative_text = EXCLUDED.narrative_text,
            kpi_json       = EXCLUDED.kpi_json,
            goals_json     = EXCLUDED.goals_json,
            risks_json     = EXCLUDED.risks_json,
            llm_model      = EXCLUDED.llm_model,
            generated_at   = NOW()
    """), {
        "team_id": team_id,
        "season": season,
        "text": result.get("executive_summary", ""),
        "kpis": json.dumps(result.get("kpis", [])),
        "goals": json.dumps(result.get("goals", [])),
        "risks": json.dumps(result.get("key_risks", [])),
        "model": model,
    })
    session.commit()


def upsert_group_narrative(session, group_type: str, group_key: str, season: int,
                           result: dict, model: str):
    session.execute(text("""
        INSERT INTO milb.group_narratives
            (group_type, group_key, season, narrative_text, kpi_json, llm_model, generated_at)
        VALUES
            (:gtype, :gkey, :season, :text, CAST(:kpis AS jsonb), :model, NOW())
        ON CONFLICT (group_type, group_key, season) DO UPDATE SET
            narrative_text = EXCLUDED.narrative_text,
            kpi_json       = EXCLUDED.kpi_json,
            llm_model      = EXCLUDED.llm_model,
            generated_at   = NOW()
    """), {
        "gtype": group_type,
        "gkey": group_key,
        "season": season,
        "text": result.get("executive_summary", ""),
        "kpis": json.dumps(result.get("kpis", [])),
        "model": model,
    })
    session.commit()


# -- Main ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate LLM executive narratives")
    parser.add_argument("--team", type=int, default=BINGHAMTON_ID,
                        help=f"Team ID to generate for (default: {BINGHAMTON_ID} = Binghamton)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model (default: {DEFAULT_MODEL})")
    parser.add_argument("--force", action="store_true", help="Regenerate even if exists")
    parser.add_argument("--skip-groups", action="store_true", help="Skip group rollup narratives")
    parser.add_argument("--competitive-intel", action="store_true",
                        help="Generate competitive intelligence narratives")
    parser.add_argument("--all-teams", action="store_true",
                        help="With --competitive-intel, generate for ALL teams")
    args = parser.parse_args()

    console.print(f"\n[bold blue]--- Narrative Generator ---[/bold blue]\n")
    console.print(f"  Model:  {args.model}")
    console.print(f"  Team:   {args.team}")

    if not check_ollama(args.model):
        sys.exit(1)

    session = get_session()
    start = time.time()

    # Verify migration
    try:
        session.execute(text("SELECT 1 FROM milb.team_narratives LIMIT 0"))
    except Exception:
        console.print("[red]Table milb.team_narratives not found. Run: psql -f sql/010_add_narratives.sql[/red]")
        sys.exit(1)

    console.print("\n  Loading data...")
    data = load_all_data(session)
    console.print(f"  Loaded {len(data['teams'])} teams, {len(data['features']):,} games, "
                  f"{len(data['recommendations'])} recommendations")

    # Get team info for group lookups
    team_row = data["teams"][data["teams"]["team_id"] == args.team]
    if team_row.empty:
        console.print(f"[red]Team {args.team} not found[/red]")
        sys.exit(1)
    team = team_row.iloc[0]
    latest_season = int(data["features"]["season"].max())

    # Build task list
    tasks = []

    # 1. Team narrative
    tasks.append(("team", args.team, team["team_name"]))

    # 2. Group rollups (based on team's clusters)
    if not args.skip_groups:
        level_name = LEVEL_NAMES.get(int(team["sport_id"]))
        if level_name:
            tasks.append(("level", level_name, f"{level_name} Level"))
        cluster_label = team.get("cluster_label")
        if pd.notna(cluster_label):
            tasks.append(("market_cluster", cluster_label, f"Market: {cluster_label}"))
        promo_label = team.get("promo_cluster_label")
        if pd.notna(promo_label):
            tasks.append(("promo_cluster", promo_label, f"Promo: {promo_label}"))
        tasks.append(("league", "all", "League-Wide"))

    client = httpx.Client()
    success = 0
    failed = 0

    # Competitive intelligence mode
    if args.competitive_intel:
        console.print("\n  [bold]Competitive Intelligence mode[/bold]")
        console.print("  Loading CI data...")
        ci_data = load_ci_data()
        data.update(ci_data)

        if ci_data["weather_peers"].empty:
            console.print("[red]No weather peer data found. Run build_competitive_intel.py first.[/red]")
            client.close()
            session.close()
            sys.exit(1)

        if args.all_teams:
            ci_team_ids = sorted(data["teams"]["team_id"].unique())
        else:
            ci_team_ids = [args.team]

        console.print(f"  Generating CI narratives for {len(ci_team_ids)} team(s)...\n")

        with Progress() as progress:
            task_bar = progress.add_task("CI Narratives", total=len(ci_team_ids))
            for tid in ci_team_ids:
                tname = data["teams"][data["teams"]["team_id"] == tid]
                label = tname.iloc[0]["team_name"] if not tname.empty else str(tid)
                progress.update(task_bar, description=f"[cyan]{label}[/cyan]")

                context = build_ci_context(tid, data)
                if context is None:
                    console.print(f"  [yellow]Skipping {label}: no data[/yellow]")
                    failed += 1
                    progress.advance(task_bar)
                    continue

                result = call_ollama(client, CI_SYSTEM_PROMPT,
                                     json.dumps(context, default=str), args.model)
                if result and "executive_summary" in result:
                    # Store as group narrative with type 'competitive_intel'
                    # Include extra fields in kpi_json
                    enriched_kpis = {
                        "kpis": result.get("kpis", []),
                        "headlines": result.get("headlines", []),
                        "teams_to_watch": result.get("teams_to_watch", []),
                    }
                    session.execute(text("""
                        INSERT INTO milb.group_narratives
                            (group_type, group_key, season, narrative_text, kpi_json,
                             llm_model, generated_at)
                        VALUES
                            ('competitive_intel', :gkey, :season, :text,
                             CAST(:kpis AS jsonb), :model, NOW())
                        ON CONFLICT (group_type, group_key, season) DO UPDATE SET
                            narrative_text = EXCLUDED.narrative_text,
                            kpi_json       = EXCLUDED.kpi_json,
                            llm_model      = EXCLUDED.llm_model,
                            generated_at   = NOW()
                    """), {
                        "gkey": str(tid),
                        "season": context["season"],
                        "text": result["executive_summary"],
                        "kpis": json.dumps(enriched_kpis),
                        "model": args.model,
                    })
                    session.commit()
                    console.print(f"  [green]{label}[/green] -- {len(result['executive_summary'])} chars")
                    success += 1
                else:
                    console.print(f"  [red]{label}: LLM returned invalid response[/red]")
                    failed += 1

                progress.advance(task_bar)

        client.close()
        elapsed = time.time() - start
        console.print(f"\n[bold green]Done! {success} CI narratives generated, {failed} failed "
                      f"({elapsed:.1f}s)[/bold green]")
        session.close()
        return

    # Standard narrative mode
    console.print(f"\n  Generating {len(tasks)} narratives...\n")

    with Progress() as progress:
        task_bar = progress.add_task("Generating", total=len(tasks))

        for task_type, task_key, task_label in tasks:
            progress.update(task_bar, description=f"[cyan]{task_label}[/cyan]")

            if task_type == "team":
                context = build_team_context(task_key, data)
                if context is None:
                    console.print(f"  [yellow]Skipping {task_label}: no data[/yellow]")
                    failed += 1
                    progress.advance(task_bar)
                    continue

                result = call_ollama(client, TEAM_SYSTEM_PROMPT,
                                     json.dumps(context, default=str), args.model)
                if result and "executive_summary" in result:
                    upsert_team_narrative(session, task_key, context["season"], result, args.model)
                    console.print(f"  [green]{task_label}[/green] -- {len(result['executive_summary'])} chars")
                    success += 1
                else:
                    console.print(f"  [red]{task_label}: LLM returned invalid response[/red]")
                    failed += 1
            else:
                context = build_group_context(task_type, task_key, data)
                if context is None:
                    console.print(f"  [yellow]Skipping {task_label}: no data[/yellow]")
                    failed += 1
                    progress.advance(task_bar)
                    continue

                result = call_ollama(client, GROUP_SYSTEM_PROMPT,
                                     json.dumps(context, default=str), args.model)
                if result and "executive_summary" in result:
                    upsert_group_narrative(session, task_type, task_key,
                                           context["season"], result, args.model)
                    console.print(f"  [green]{task_label}[/green] -- {len(result['executive_summary'])} chars")
                    success += 1
                else:
                    console.print(f"  [red]{task_label}: LLM returned invalid response[/red]")
                    failed += 1

            progress.advance(task_bar)

    client.close()
    elapsed = time.time() - start
    console.print(f"\n[bold green]Done! {success} narratives generated, {failed} failed "
                  f"({elapsed:.1f}s)[/bold green]")
    session.close()


if __name__ == "__main__":
    main()
