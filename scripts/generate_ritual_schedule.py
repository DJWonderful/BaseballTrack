"""Generate an LLM-proposed weekly ritual schedule for Binghamton.

The output renders on the Rituals briefing page (pages/findings/04_Rituals.py).
This is explicitly a hypothesis, not a directive. The page caption labels it
that way; this script keeps the prompt aligned with the briefing book voice.

Inputs (all read from milb at run time):
  - Binghamton's current 2025 recurring rituals (offer name, day of week,
    games run, category flags)
  - Top recurring rituals across Double-A, with how many teams run each
    family
  - The locked Saturday = Fireworks constraint (see Finding 1, Saturdays)

Output:
  Row in milb.group_narratives with
    group_type = 'ritual_schedule'
    group_key  = 'binghamton'
    season     = 2027 (target year for the proposal)
    kpi_json   = {
      "headline": str,
      "days": [
        {
          "day":              "Mon" | "Tue" | ... | "Sun",
          "status":           "keep" | "add" | "test" | "skip",
          "current_ritual":   str | null,
          "proposed_ritual":  str | null,
          "category":         "kids" | "family/community" | "food/drink"
                              | "ticket deal" | "theme" | "fireworks" | "rest",
          "rationale":        str  (one short sentence)
        },
        ... 7 entries total
      ]
    }

Usage:
    ollama serve   # in another shell
    python scripts/generate_ritual_schedule.py
    python scripts/generate_ritual_schedule.py --force      # regenerate
    python scripts/generate_ritual_schedule.py --model qwen3:8b
"""

import argparse
import json
import sys
from pathlib import Path

import httpx
import pandas as pd
from rich.console import Console
from sqlalchemy import text

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from src.db.connection import engine

console = Console()

OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:8b"
BINGHAMTON_ID = 505
DOUBLE_A_SPORT_ID = 12
TARGET_SEASON = 2027

# PostgreSQL EXTRACT(DOW FROM date) returns Sunday=0 .. Saturday=6.
# (Do NOT confuse with game_features.day_of_week which is Mon=0..Sun=6.)
DOW_FROM_INT = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}

RITUAL_FAMILY_CASE = """
  CASE
    WHEN offer_name ILIKE 'thirsty thursday%' OR offer_name ILIKE '%thirsty thursday%' THEN 'Thirsty Thursday'
    WHEN offer_name ILIKE 'three dollar thursday%' OR offer_name ILIKE '$3 thursday%' THEN '$3 Thursday'
    WHEN offer_name ILIKE 'taco tuesday%' THEN 'Taco Tuesday'
    WHEN offer_name ILIKE 'twofer tuesday%' OR offer_name ILIKE 'two-for-tuesday%' OR offer_name ILIKE '%two for tuesday%' THEN 'Twofer Tuesday'
    WHEN offer_name ILIKE 'wine wednesday%' THEN 'Wine Wednesday'
    WHEN offer_name ILIKE 'fryday%' THEN 'Fryday'
    WHEN offer_name ILIKE 'trivia tuesday%' THEN 'Trivia Tuesday'
    WHEN offer_name ILIKE '%kids club%' OR offer_name ILIKE 'kids%sunday%' OR offer_name ILIKE 'kids day%' THEN 'Kids Day / Club'
    WHEN offer_name ILIKE '%family funday%' OR offer_name ILIKE 'family day%' THEN 'Family Funday'
    WHEN offer_name ILIKE 'senior%' THEN 'Seniors Day'
    WHEN offer_name ILIKE 'we care%' THEN 'We Care Wednesday'
    WHEN offer_name ILIKE 'throwback%' THEN 'Throwback Thursday'
    WHEN offer_name ILIKE 'sunday funday%' THEN 'Sunday Funday'
    WHEN offer_name ILIKE 'fireworks%' THEN 'Fireworks Night'
    WHEN offer_name ILIKE '%koozie%' THEN 'Koozie Klub'
    WHEN offer_name ILIKE '%weenie%whiskey%' THEN 'Weenie & Whiskey'
    ELSE NULL
  END
"""


SYSTEM_PROMPT = """You are a Minor League Baseball promotions consultant writing a briefing for a team's front office. The user gives you a day-by-day picture of what is already on the team's weekly schedule. Your job is to recommend, per day, whether to keep what is there, pilot something new, or stay quiet.

You MUST return ONLY a single JSON object with this shape:

{
  "headline": "one-sentence summary of the schedule's intent (under 30 words)",
  "days": [
    {
      "day": "Mon" | "Tue" | "Wed" | "Thu" | "Fri" | "Sat" | "Sun",
      "status": "keep" | "test" | "skip",
      "proposed_ritual": proposed new ritual name or null,
      "category": "kids" | "family/community" | "food/drink" | "ticket deal" | "theme" | "fireworks" | "rest",
      "rationale": "one short sentence under 25 words"
    }
  ]
}

Rules:
- Return exactly 7 day entries, in order Mon, Tue, Wed, Thu, Fri, Sat, Sun.
- DO NOT include a "current_ritual" field. The reader will be shown what is currently on each day from the data; you do not need to repeat it.
- Saturday is LOCKED. Always return: status="keep", category="fireworks", proposed_ritual=null, rationale referencing fireworks.
- Use "keep" when the day already has a working ritual and you do not propose to change anything.
- Use "test" only on the ONE day where you are proposing a new pilot ritual. Set proposed_ritual to the suggested name (a short branded name, e.g. "Thirsty Thursday" or "Trivia Tuesday"). Set category to one of the allowed values. Set proposed_ritual=null on every other day.
- Use "skip" when the day should remain unbranded with no recurring promo, and explain in one sentence why.
- The biggest gap in the team's current slate is adult weeknight food and drink formats. Favor Tuesday, Wednesday, or Thursday for that kind of pilot.
- Recommend AT MOST ONE pilot ritual across the entire week. Habit takes a season to build; asking the front office to launch multiple new programs in one year is not realistic.
- Be honest about uncertainty. Use phrases like "worth piloting" or "candidate test" in the rationale. Avoid imperatives like "must" or "should immediately".
- Do NOT mention machine learning, XGBoost, SHAP, OLS, or any model internals.
- Do NOT name any individual person.
- Return ONLY the JSON object. No commentary, no markdown fences."""


# -- Ollama plumbing ----------------------------------------------------------

def check_ollama(model: str) -> bool:
    try:
        resp = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        available = [m["name"] for m in resp.json().get("models", [])]
        if not any(model in m or m.startswith(model.split(":")[0]) for m in available):
            console.print(f"[red]Model '{model}' not found in Ollama. Available:[/red]")
            for m in available:
                console.print(f"  {m}")
            return False
        return True
    except httpx.ConnectError:
        console.print("[red]Cannot reach Ollama at localhost:11434.[/red]")
        console.print("  Start it with: ollama serve")
        return False


def call_ollama(client: httpx.Client, user_content: str, model: str) -> dict | None:
    options = {"temperature": 0.3, "num_predict": 4096, "num_ctx": 8192}
    if "qwen3" in model:
        options["think"] = False
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
        "stream": False,
        "format": "json",
        "options": options,
    }
    try:
        resp = client.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=600)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        eval_count = data.get("eval_count", "?")
        prompt_count = data.get("prompt_eval_count", "?")
        done_reason = data.get("done_reason", "?")
        console.print(
            f"  Ollama eval_count={eval_count}, prompt_eval={prompt_count}, done_reason={done_reason}"
        )
        if not content.strip():
            console.print("[red]Ollama returned empty content.[/red]")
            return None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            console.print(f"[red]Failed to parse Ollama output as JSON: {e}[/red]")
            console.print(f"  First 400 chars of response: {content[:400]!r}")
            return None
        if not isinstance(parsed, dict):
            console.print("[red]Ollama returned non-dict JSON.[/red]")
            return None
        return parsed
    except (httpx.HTTPError, KeyError) as e:
        console.print(f"[red]Ollama call failed: {e}[/red]")
        return None


# -- Data loading -------------------------------------------------------------

def load_rp_current_rituals() -> list[dict]:
    df = pd.read_sql(text(f"""
        SELECT p.offer_name,
               COUNT(*) AS n_games,
               MODE() WITHIN GROUP (ORDER BY EXTRACT(DOW FROM g.game_date)) AS dow_raw,
               BOOL_OR(p.is_kids_event)         AS is_kids,
               BOOL_OR(p.is_community_event OR p.is_heritage_night) AS is_family,
               BOOL_OR(p.is_food_deal)          AS is_food_drink,
               BOOL_OR(p.is_ticket_deal)        AS is_ticket,
               BOOL_OR(p.is_theme_night)        AS is_theme
          FROM milb.game_promotions p
          JOIN milb.games g ON g.game_pk = p.game_pk
         WHERE p.is_recurring = TRUE
           AND p.enrichment_method IS NOT NULL
           AND g.home_team_id = {BINGHAMTON_ID}
           AND g.season IN (2025, 2026)
         GROUP BY p.offer_name
        HAVING COUNT(*) >= 2
         ORDER BY n_games DESC
    """), engine)
    return [
        {
            "name":     row["offer_name"],
            "day":      DOW_FROM_INT.get(int(row["dow_raw"]), ""),
            "n_games":  int(row["n_games"]),
            "category": _categorize(row),
        }
        for _, row in df.iterrows()
    ]


def _categorize(row) -> str:
    if row.get("is_kids"):       return "kids"
    if row.get("is_family"):     return "family/community"
    if row.get("is_food_drink"): return "food/drink"
    if row.get("is_ticket"):     return "ticket deal"
    if row.get("is_theme"):      return "theme"
    return "other"


def load_peer_top_rituals() -> list[dict]:
    df = pd.read_sql(text(f"""
        WITH classified AS (
          SELECT p.*, g.home_team_id, g.game_date,
                 {RITUAL_FAMILY_CASE} AS ritual_family
            FROM milb.game_promotions p
            JOIN milb.games g ON g.game_pk = p.game_pk
           WHERE p.is_recurring = TRUE
             AND p.enrichment_method IS NOT NULL
             AND g.sport_id = {DOUBLE_A_SPORT_ID}
             AND g.season = 2025
        )
        SELECT ritual_family,
               COUNT(DISTINCT home_team_id) AS n_teams,
               COUNT(*) AS n_games,
               BOOL_OR(home_team_id = {BINGHAMTON_ID}) AS rp_runs,
               MODE() WITHIN GROUP (ORDER BY EXTRACT(DOW FROM game_date)) AS dow_raw
          FROM classified
         WHERE ritual_family IS NOT NULL
         GROUP BY ritual_family
         ORDER BY n_teams DESC, n_games DESC
    """), engine)
    return [
        {
            "name":      row["ritual_family"],
            "typical_day": DOW_FROM_INT.get(int(row["dow_raw"]), ""),
            "n_teams":   int(row["n_teams"]),
            "rp_runs":   bool(row["rp_runs"]),
        }
        for _, row in df.iterrows()
    ]


# -- Prompt construction ------------------------------------------------------

def build_day_map(current: list[dict]) -> dict[str, list[str]]:
    """Group current ritual names by day-of-week label so the prompt can show
    an explicit per-day picture without making the model infer day mappings.
    """
    by_day: dict[str, list[str]] = {d: [] for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
    for r in current:
        day = r.get("day")
        if day in by_day:
            by_day[day].append(r["name"])
    return by_day


def build_user_prompt(current: list[dict], peers: list[dict]) -> str:
    day_map = build_day_map(current)
    day_state_lines = []
    for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
        if d == "Sat":
            day_state_lines.append(f"- {d}: LOCKED to Fireworks (do not change).")
            continue
        names = day_map.get(d, [])
        if names:
            day_state_lines.append(f"- {d}: currently runs {', '.join(names)}.")
        else:
            day_state_lines.append(f"- {d}: no current recurring ritual.")
    day_state = "\n".join(day_state_lines)

    return f"""TEAM: Binghamton Rumble Ponies (Double-A, NY)

CURRENT STATE BY DAY OF WEEK (2025 and 2026 combined):
{day_state}

PEER RITUAL FAMILIES ACROSS DOUBLE-A IN 2025
(ordered by number of teams running each, with whether Binghamton runs it):
{json.dumps(peers, indent=2)}

KEY OBSERVATION:
The current slate covers most days with kids, family, and ticket-deal
formats. The biggest gap is adult-oriented weeknight food or drink, the
kind of weekly habit other Double-A clubs use to anchor Tuesday,
Wednesday, or Thursday gates. Thirsty Thursday alone runs at 13 of about
30 Double-A teams; Binghamton does not run an equivalent.

YOUR TASK:
Return the 7-day JSON schedule. Use "keep" on days that already run a
ritual unless you have a clear reason to recommend otherwise. Use "test"
on AT MOST ONE day to propose a new pilot ritual targeting the
adult-weeknight gap. Use "skip" for days that should remain unbranded.
Saturday is locked. Return only the JSON object specified in the system
prompt; do not include a current_ritual field.
"""


# -- DB write -----------------------------------------------------------------

def should_run(force: bool) -> bool:
    if force:
        return True
    with engine.connect() as conn:
        existing = conn.execute(text("""
            SELECT 1 FROM milb.group_narratives
             WHERE group_type = 'ritual_schedule'
               AND group_key  = 'binghamton'
        """)).fetchone()
        return existing is None


def write_schedule(parsed: dict, model: str) -> None:
    headline = parsed.get("headline", "")
    narrative = headline or "LLM-proposed weekly ritual schedule for Binghamton."
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO milb.group_narratives
                (group_type, group_key, season, narrative_text, kpi_json,
                 llm_model, generated_at)
            VALUES
                ('ritual_schedule', 'binghamton', :season, :nar, :kpi,
                 :model, NOW())
            ON CONFLICT (group_type, group_key, season) DO UPDATE
                SET narrative_text = EXCLUDED.narrative_text,
                    kpi_json       = EXCLUDED.kpi_json,
                    llm_model      = EXCLUDED.llm_model,
                    generated_at   = NOW()
        """), {
            "season": TARGET_SEASON,
            "nar":    narrative,
            "kpi":    json.dumps(parsed),
            "model":  model,
        })


# -- Main ---------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate LLM ritual schedule for Binghamton")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate even if a schedule already exists")
    args = parser.parse_args()

    if not should_run(args.force):
        console.print("[green]Ritual schedule already exists. Use --force to regenerate.[/green]")
        return 0

    if not check_ollama(args.model):
        return 1

    console.print("[cyan]Loading Binghamton ritual context...[/cyan]")
    current = load_rp_current_rituals()
    peers = load_peer_top_rituals()
    console.print(f"  Current rituals: {len(current)}, peer families: {len(peers)}")

    prompt = build_user_prompt(current, peers)

    console.print(f"[cyan]Calling Ollama ({args.model})...[/cyan]")
    with httpx.Client() as client:
        parsed = call_ollama(client, prompt, args.model)

    if parsed is None:
        console.print("[red]LLM call failed. Nothing written.[/red]")
        return 2

    days = parsed.get("days")
    if not isinstance(days, list) or len(days) != 7:
        console.print(f"[red]Invalid response shape: days={days!r}. Nothing written.[/red]")
        return 3

    # Fill current_ritual deterministically from the data. The LLM is good at
    # creative recommendations and bad at bookkeeping; this guarantees we
    # never display a misplaced existing ritual name on the page.
    day_map = build_day_map(current)
    for day_entry in days:
        d = day_entry.get("day")
        names = day_map.get(d, []) if isinstance(d, str) else []
        # Dedupe near-duplicate names (e.g. "We Care Wednesday" and
        # "We Care Wednesdays") while preserving order.
        seen, deduped = set(), []
        for n in names:
            key = n.lower().rstrip("s")
            if key not in seen:
                seen.add(key)
                deduped.append(n)
        if deduped:
            day_entry["current_ritual"] = ", ".join(deduped)
        else:
            day_entry["current_ritual"] = None
        # Saturday is locked to fireworks; the fireworks promo type is not
        # flagged is_recurring=TRUE in the source data, so it would not have
        # shown up in day_map. Pin it explicitly here.
        if d == "Sat" and not day_entry.get("current_ritual"):
            day_entry["current_ritual"] = "Fireworks Night"
    parsed["days"] = days

    console.print(f"[cyan]Storing schedule into milb.group_narratives...[/cyan]")
    write_schedule(parsed, args.model)

    console.print("[bold green]Done.[/bold green]")
    console.print(f"  Headline: {parsed.get('headline', '(none)')}")
    for d in days:
        console.print(
            f"  {d.get('day', '???'):>3}  [{d.get('status', '?'):>5}]  "
            f"{d.get('proposed_ritual') or d.get('current_ritual') or '(none)'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
