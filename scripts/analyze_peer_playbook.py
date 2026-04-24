"""Peer Playbook: hand-picked comp teams for Binghamton, side-by-side profile
plus an LLM-generated "what to steal" brief for the manager.

The comps are curated, not ranked by an algorithm. We want teams the manager
will recognize and that tell a story:

    Portland Sea Dogs    - small market, cold, similar stadium vintage (the
                           strongest comp -- ~3x the gate, almost the same
                           venue age and weather profile)
    Erie SeaWolves       - cold-weather, small-market Double-A
    New Hampshire        - cold-weather, small-market Double-A
    Reading Fightin Phils- regional, cold-weather, small-market
    Akron RubberDucks    - post-industrial small market, recent growth
    Richmond Flying Sqs  - AA #1, the "ritual/theme" ceiling
    Frisco RoughRiders   - AA top-5, big-market reference point

Writes milb.peer_playbook with one row per peer + one for Binghamton.
Calls Ollama per peer to produce a "what to steal" narrative. Ollama is
optional -- without it, the rows still write with narrative_text = NULL.

Usage:
    python scripts/analyze_peer_playbook.py
    python scripts/analyze_peer_playbook.py --season 2025
    python scripts/analyze_peer_playbook.py --skip-llm   (stats only)
    python scripts/analyze_peer_playbook.py --force
"""

import argparse
import json
import sys
from pathlib import Path

import httpx
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

OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:8b"

RUMBLE_PONIES_ID = 505

# Curated peer set. team_name must match milb.teams exactly.
PEERS = [
    ("Portland Sea Dogs",           "small_market_cold"),
    ("Erie SeaWolves",              "small_market_cold"),
    ("New Hampshire Fisher Cats",   "small_market_cold"),
    ("Reading Fightin Phils",       "small_market_cold"),
    ("Akron RubberDucks",           "small_market_warm"),
    ("Richmond Flying Squirrels",   "large_market_model"),
    ("Frisco RoughRiders",          "large_market_model"),
]

PROMO_FLAGS = [
    "has_fireworks", "has_giveaway", "has_food_deal", "has_ticket_deal",
    "has_theme_night", "has_kids_event", "has_heritage", "has_community",
    "has_entertain", "has_dog", "has_celebrity", "has_recurring",
]

DOW_LABELS = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

ANALYSIS_NAME = "peer_playbook"


PEER_SYSTEM_PROMPT = """You are a Minor League Baseball analytics consultant. You are preparing a brief for the General Manager of the Binghamton Rumble Ponies (small-market Double-A in upstate NY, recently struggling with attendance). You will be given JSON data comparing ONE peer team to Binghamton.

Produce a structured JSON response with EXACTLY these keys:

- "narrative_text": 2-3 short paragraphs (under 220 words). Lead with the single most transferable idea. Reference specific numbers from the data. End with a one-sentence "so what for Binghamton".

- "what_to_steal": array of 2-3 objects with:
    "action"      - short imperative sentence (e.g., "Move fireworks from Friday to Saturday")
    "reason"      - 1 sentence rooted in the peer's observed numbers
    "est_impact"  - qualitative tag: one of "high", "medium", "low"

Rules:
- Do not invent numbers; only use what appears in the data.
- Do not recommend things a small-market team cannot do (e.g., don't suggest Binghamton act like a large-market team).
- No machine-learning / statistical jargon. Write for a baseball operator.
- Return ONLY the JSON object, no markdown, no prose outside the JSON."""


def log_run_start(session) -> int:
    with engine.connect() as conn:
        current = conn.execute(text(
            "SELECT MAX(created_at) FROM milb.game_features"
        )).fetchone()
    row = session.execute(text(f"""
        INSERT INTO milb.analysis_runs (analysis_name, input_max_updated, status)
        VALUES ('{ANALYSIS_NAME}', :max_up, 'running')
        RETURNING run_id
    """), {"max_up": current[0]})
    session.commit()
    return row.fetchone()[0]


def finalize_run(session, run_id: int, status: str, n: int = 0, err: str | None = None):
    session.execute(text("""
        UPDATE milb.analysis_runs
           SET status = :s, completed_at = NOW(), record_count = :n,
               error_message = :e
         WHERE run_id = :rid
    """), {"s": status, "n": n, "e": err, "rid": run_id})
    session.commit()


# ------------------------------------------------------------------ Ollama

def ollama_available(model: str) -> bool:
    try:
        resp = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        available = [m["name"] for m in resp.json().get("models", [])]
        return any(model in m or m.startswith(model.split(":")[0]) for m in available)
    except httpx.ConnectError:
        return False


def call_ollama(client: httpx.Client, system: str, user: str, model: str) -> dict | None:
    options = {"temperature": 0.25, "num_predict": 3000}
    if "qwen3" in model:
        options["think"] = False
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        "options": options,
    }
    try:
        resp = client.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=300)
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        return json.loads(content)
    except Exception as e:
        console.print(f"[yellow]Ollama call failed: {e}[/yellow]")
        return None


# ------------------------------------------------------------------ Data

def resolve_team_ids() -> dict[str, int]:
    """Look up team_id for each curated peer name + Binghamton."""
    names = [n for n, _ in PEERS] + ["Binghamton Rumble Ponies"]
    placeholders = ", ".join(f":n{i}" for i in range(len(names)))
    params = {f"n{i}": n for i, n in enumerate(names)}
    df = pd.read_sql(text(f"""
        SELECT team_id, team_name FROM milb.teams
         WHERE team_name IN ({placeholders})
    """), engine, params=params)
    out = {row["team_name"]: int(row["team_id"]) for _, row in df.iterrows()}
    # Sanity-check each peer
    missing = [n for n in names if n not in out]
    if missing:
        console.print(f"[yellow]Peers not found in milb.teams: {missing}[/yellow]")
    return out


def load_team_profile(team_id: int, season: int) -> dict:
    """Pull all numbers we need for one team's profile row."""
    # Base team + venue + demo (latest census year). stadium_year isn't
    # modeled in the venues table so we leave it NULL here; it can be
    # hand-populated later if needed.
    team_row = pd.read_sql(text("""
        SELECT t.team_id, t.team_name, t.sport_id,
               v.venue_name, v.capacity AS venue_capacity,
               NULL::integer AS stadium_year,
               vd.msa_population, vd.msa_median_income, vd.msa_poverty_rate
          FROM milb.teams t
          LEFT JOIN milb.venues v ON t.venue_id = v.venue_id
          LEFT JOIN LATERAL (
              SELECT * FROM milb.venue_demographics vd2
              WHERE vd2.venue_id = v.venue_id
              ORDER BY vd2.census_year DESC LIMIT 1
          ) vd ON TRUE
         WHERE t.team_id = :tid
    """), engine, params={"tid": team_id})
    if team_row.empty:
        return {}
    team = team_row.iloc[0].to_dict()

    # Games for this season
    flag_cols = ", ".join(f"f.{c}" for c in PROMO_FLAGS)
    games = pd.read_sql(text(f"""
        SELECT f.game_pk, f.team_id, f.season, f.game_date, f.day_of_week,
               f.attendance, f.capacity_utilization, f.promo_count, f.has_any_promo,
               {flag_cols}
          FROM milb.game_features f
         WHERE f.team_id = :tid AND f.season = :s
           AND f.game_type = 'R' AND f.attendance IS NOT NULL AND f.attendance > 0
    """), engine, params={"tid": team_id, "s": season})

    if games.empty:
        return {"team_id": team_id, "team_name": team["team_name"], "season": season,
                "empty": True}

    avg_att = float(games["attendance"].mean())
    cap_util = float(games["capacity_utilization"].mean())
    promos_per_game = float(games["promo_count"].mean())

    # YoY
    prev_games = pd.read_sql(text("""
        SELECT AVG(attendance)::float AS att
          FROM milb.game_features
         WHERE team_id = :tid AND season = :s
           AND game_type = 'R' AND attendance IS NOT NULL
    """), engine, params={"tid": team_id, "s": season - 1})
    prev = prev_games.iloc[0]["att"] if not prev_games.empty else None
    yoy = round((avg_att - prev) / prev * 100, 2) if prev else None

    # Fri/Sat
    fri = games[games["day_of_week"] == 4]
    sat = games[games["day_of_week"] == 5]
    fri_avg = float(fri["attendance"].mean()) if not fri.empty else None
    sat_avg = float(sat["attendance"].mean()) if not sat.empty else None
    fri_fw = float(fri["has_fireworks"].fillna(False).astype(int).mean()) if not fri.empty else None
    sat_fw = float(sat["has_fireworks"].fillna(False).astype(int).mean()) if not sat.empty else None

    # Top promo flag by lift
    no_promo = games[~games[PROMO_FLAGS].fillna(False).any(axis=1)]
    baseline = float(no_promo["attendance"].mean()) if len(no_promo) >= 3 else avg_att
    best_flag, best_lift = None, -1e9
    for flag in PROMO_FLAGS:
        sub = games[games[flag].fillna(False).astype(bool)]
        if len(sub) < 3:
            continue
        lift = float(sub["attendance"].mean()) - baseline
        if lift > best_lift:
            best_lift = lift
            best_flag = flag

    # Recurring promo usage
    has_recurring = bool(games["has_recurring"].fillna(False).astype(bool).any()) if "has_recurring" in games.columns else False

    # Rank within level
    rank_df = pd.read_sql(text("""
        SELECT team_id,
               AVG(attendance) AS avg_att,
               RANK() OVER (ORDER BY AVG(attendance) DESC) AS rk
          FROM milb.game_features
         WHERE season = :s AND sport_id = :sid
           AND game_type = 'R' AND attendance IS NOT NULL
         GROUP BY team_id
    """), engine, params={"s": season, "sid": int(team["sport_id"])})
    rk_row = rank_df[rank_df["team_id"] == team_id]
    league_rank = int(rk_row.iloc[0]["rk"]) if not rk_row.empty else None

    return {
        "team_id": int(team_id),
        "team_name": team["team_name"],
        "season": season,
        "venue_name": team.get("venue_name"),
        "venue_capacity": int(team["venue_capacity"]) if team.get("venue_capacity") else None,
        "stadium_year": int(team["stadium_year"]) if team.get("stadium_year") else None,
        "msa_population": int(team["msa_population"]) if team.get("msa_population") else None,
        "median_income": int(team["msa_median_income"]) if team.get("msa_median_income") else None,
        "poverty_rate": float(team["msa_poverty_rate"]) if team.get("msa_poverty_rate") is not None else None,
        "avg_attendance": round(avg_att, 1),
        "cap_utilization": round(cap_util, 4),
        "yoy_change_pct": yoy,
        "league_rank": league_rank,
        "total_home_games": int(len(games)),
        "promos_per_game": round(promos_per_game, 2),
        "fri_avg_att": round(fri_avg, 1) if fri_avg else None,
        "sat_avg_att": round(sat_avg, 1) if sat_avg else None,
        "fri_fireworks_pct": round(fri_fw, 4) if fri_fw is not None else None,
        "sat_fireworks_pct": round(sat_fw, 4) if sat_fw is not None else None,
        "has_recurring_promo": has_recurring,
        "top_promo_flag": best_flag,
        "top_promo_lift": round(best_lift, 1) if best_flag else None,
    }


# ------------------------------------------------------------------ LLM

def build_user_prompt(peer: dict, hero: dict) -> str:
    """Compact JSON to keep context small."""
    payload = {
        "hero_team": {
            k: hero.get(k) for k in (
                "team_name", "avg_attendance", "cap_utilization",
                "yoy_change_pct", "league_rank", "msa_population",
                "promos_per_game", "fri_avg_att", "sat_avg_att",
                "fri_fireworks_pct", "sat_fireworks_pct", "top_promo_flag",
                "top_promo_lift", "has_recurring_promo",
            )
        },
        "peer_team": {
            k: peer.get(k) for k in (
                "team_name", "avg_attendance", "cap_utilization",
                "yoy_change_pct", "league_rank", "msa_population",
                "stadium_year", "promos_per_game", "fri_avg_att", "sat_avg_att",
                "fri_fireworks_pct", "sat_fireworks_pct", "top_promo_flag",
                "top_promo_lift", "has_recurring_promo",
            )
        },
        "peer_role": peer.get("peer_role"),
    }
    return (
        "Compare this ONE peer to Binghamton and tell me what to steal.\n\n"
        + json.dumps(payload, default=str)
    )


# ------------------------------------------------------------------ Write

def write_rows(rows: list[dict], season: int, run_id: int):
    """Write with explicit per-row parameterized INSERT so what_to_steal lands
    in JSONB as a proper array (not a JSON-encoded string scalar)."""
    if not rows:
        return
    cols = [
        "team_id", "season", "team_name", "peer_role",
        "msa_population", "median_income", "poverty_rate",
        "venue_name", "venue_capacity", "stadium_year",
        "avg_attendance", "cap_utilization", "yoy_change_pct",
        "league_rank", "total_home_games", "promos_per_game",
        "fri_avg_att", "sat_avg_att", "fri_fireworks_pct", "sat_fireworks_pct",
        "has_recurring_promo", "top_promo_flag", "top_promo_lift",
        "narrative_text", "what_to_steal", "llm_model", "llm_generated_at",
        "run_id",
    ]
    placeholders = ", ".join(f":{c}" for c in cols)
    col_list = ", ".join(cols)
    stmt = f"""
        INSERT INTO milb.peer_playbook ({col_list})
        VALUES ({placeholders.replace(':what_to_steal', 'CAST(:what_to_steal AS jsonb)')})
    """
    def coerce(v):
        """psycopg2 stringifies numpy scalars as `np.float64(x)` literally."""
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if hasattr(v, "item"):       # numpy scalar -> Python native
            return v.item()
        return v

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM milb.peer_playbook WHERE season = :s"), {"s": season})
        for r in rows:
            params = {c: coerce(r.get(c)) for c in cols}
            params["run_id"] = run_id
            params["what_to_steal"] = (
                json.dumps(params["what_to_steal"]) if params["what_to_steal"] is not None else None
            )
            conn.execute(text(stmt), params)
    console.print(f"  Wrote {len(rows)} peer_playbook rows")


def print_summary(rows: list[dict]):
    t = Table(title="Peer playbook")
    for col in ("Role", "Team", "Rank", "Avg Att", "Cap%", "YoY%", "Top Promo"):
        t.add_column(col)
    for r in rows:
        t.add_row(
            r.get("peer_role") or "",
            r["team_name"],
            str(r.get("league_rank") or ""),
            f"{(r.get('avg_attendance') or 0):,.0f}",
            f"{(r.get('cap_utilization') or 0) * 100:.0f}%",
            f"{r.get('yoy_change_pct') or 0:+.1f}%",
            (r.get("top_promo_flag") or "").replace("has_", ""),
        )
    console.print(t)


# ------------------------------------------------------------------ Main

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--season", type=int, default=None)
    p.add_argument("--skip-llm", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--model", default=DEFAULT_MODEL)
    args = p.parse_args()

    session = get_session()
    run_id = log_run_start(session)

    try:
        if args.season is None:
            # Prefer latest COMPLETE season for peer comparison (>= 40 games
            # so season averages are stable).
            r = pd.read_sql(text("""
                SELECT season FROM milb.game_features
                 WHERE team_id = :t AND attendance IS NOT NULL
                 GROUP BY season HAVING COUNT(*) >= 40
                 ORDER BY season DESC LIMIT 1
            """), engine, params={"t": RUMBLE_PONIES_ID})
            if r.empty:
                r = pd.read_sql(text(
                    "SELECT MAX(season) AS season FROM milb.game_features "
                    " WHERE team_id = :t AND attendance IS NOT NULL"
                ), engine, params={"t": RUMBLE_PONIES_ID})
            args.season = int(r.iloc[0]["season"])
            console.print(f"Using latest complete season: [bold]{args.season}[/bold]")

        ids = resolve_team_ids()
        hero_id = ids.get("Binghamton Rumble Ponies")
        if hero_id is None:
            raise RuntimeError("Binghamton Rumble Ponies not found in milb.teams")

        # Load hero
        hero_profile = load_team_profile(hero_id, args.season)
        hero_profile["peer_role"] = "hero"

        peer_profiles: list[dict] = []
        for name, role in PEERS:
            tid = ids.get(name)
            if tid is None:
                console.print(f"[yellow]Skipping missing peer: {name}[/yellow]")
                continue
            prof = load_team_profile(tid, args.season)
            if prof.get("empty"):
                console.print(f"[yellow]No games for {name} in {args.season}[/yellow]")
                continue
            prof["peer_role"] = role
            peer_profiles.append(prof)

        # LLM
        if not args.skip_llm and ollama_available(args.model):
            with httpx.Client(timeout=300) as client:
                for peer in peer_profiles:
                    user = build_user_prompt(peer, hero_profile)
                    result = call_ollama(client, PEER_SYSTEM_PROMPT, user, args.model)
                    if result:
                        peer["narrative_text"] = result.get("narrative_text")
                        peer["what_to_steal"] = result.get("what_to_steal")
                        peer["llm_model"] = args.model
                        peer["llm_generated_at"] = pd.Timestamp.now(tz="UTC")
                        console.print(f"  [green]ok[/green] narrative for {peer['team_name']}")
                    else:
                        console.print(f"  [yellow]skip[/yellow] narrative skipped for {peer['team_name']}")
        else:
            if not args.skip_llm:
                console.print("[yellow]Ollama unavailable — writing rows without narratives.[/yellow]")

        all_rows = [hero_profile] + peer_profiles
        # Normalize keys across rows
        keys = {
            "team_id", "season", "team_name", "peer_role",
            "msa_population", "median_income", "poverty_rate",
            "venue_name", "venue_capacity", "stadium_year",
            "avg_attendance", "cap_utilization", "yoy_change_pct",
            "league_rank", "total_home_games", "promos_per_game",
            "fri_avg_att", "sat_avg_att", "fri_fireworks_pct", "sat_fireworks_pct",
            "has_recurring_promo", "top_promo_flag", "top_promo_lift",
            "narrative_text", "what_to_steal", "llm_model", "llm_generated_at",
        }
        for r in all_rows:
            for k in keys:
                r.setdefault(k, None)
            r.pop("empty", None)

        write_rows(all_rows, args.season, run_id)
        print_summary(all_rows)

        finalize_run(session, run_id, "completed", n=len(all_rows))
        return 0
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        finalize_run(session, run_id, "failed", err=str(e))
        raise
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
