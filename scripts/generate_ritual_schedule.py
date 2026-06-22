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


SYSTEM_PROMPT = """You are a Minor League Baseball promotions consultant writing a briefing for the front office of one specific team. The user gives you that team's full weekly promo placement, plus what high-attendance teams across MiLB do on each day. Your job is to recommend, per day, whether to keep what is there, change the placement, pilot something new, or stay quiet.

You MUST return ONLY a single JSON object with this shape:

{
  "headline": "one-sentence summary of the schedule's overall intent (under 30 words)",
  "days": [
    {
      "day": "Mon" | "Tue" | "Wed" | "Thu" | "Fri" | "Sat" | "Sun",
      "status": "off" | "keep" | "change" | "test" | "skip",
      "proposed_ritual": proposed ritual name or null,
      "category": "fireworks" | "giveaway" | "kids" | "family/community" | "food/drink" | "ticket deal" | "theme" | "rest",
      "rationale": "one short sentence under 30 words explaining the call"
    }
  ]
}

Status definitions:
- "off"    : The team does not schedule home games on this day. Reserved for Monday at this team.
- "keep"   : The day already has a working format that matches what successful peers do; no change.
- "change" : The day has something today, but successful peers anchor it with a different format. Propose what the day should become.
- "test"   : The day currently has no anchor format; propose a category-level pilot for what to try.
- "skip"   : The day should remain unbranded. Used sparingly and only with a clear reason.

Rules:
- Return exactly 7 day entries in order Mon, Tue, Wed, Thu, Fri, Sat, Sun.
- DO NOT include a "current_ritual" field. The reader sees that separately from the data; you do not need to repeat it.
- Monday: this specific team plays no home games on Monday. Always return status="off", category="rest", proposed_ritual=null.
- "proposed_ritual" can be either a specific named ritual (e.g. "Thirsty Thursday") OR a category-level description (e.g. "Adult food/drink night" or "Theme/heritage night"). When you are not confident in a specific name, prefer the category-level description.
- The user will give you a Saturday recommendation grounded in a separate prior finding (the Saturdays page). Treat that recommendation as the right answer for Saturday and Friday: status="change" on both, with the format swap explained.
- For other days, look at what high-attendance teams do on that day-of-week vs what this team is doing. If the placement diverges meaningfully and the data points to a better format, recommend change.
- Be honest about uncertainty. Use language like "worth piloting" or "candidate test" or "in line with what peers do". Avoid imperatives like "must" or "should immediately".
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
    options = {"temperature": 0.3, "num_predict": 8192, "num_ctx": 12288}
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
            "category": _categorize(row, row["offer_name"]),
        }
        for _, row in df.iterrows()
    ]


# Offer-name keywords that override the LLM-enriched flag categorization.
# Many "two-for-one" drink rituals get tagged is_ticket_deal=TRUE by the
# enrichment pipeline because the deal mechanic looks like a ticket promo;
# the actual product is drinks, so this should land as food/drink.
DRINK_KEYWORDS = ("twofer", "two-for", "two for", "thirsty", "wine wednesday",
                  "taco tuesday", "fryday", "weenie")


def _categorize(row, name: str = "") -> str:
    name_l = (name or "").lower()
    if any(k in name_l for k in DRINK_KEYWORDS):
        return "food/drink"
    if row.get("is_kids"):       return "kids"
    if row.get("is_family"):     return "family/community"
    if row.get("is_food_drink"): return "food/drink"
    if row.get("is_ticket"):     return "ticket deal"
    if row.get("is_theme"):      return "theme"
    return "other"


def load_rp_full_dow_profile() -> list[dict]:
    """Binghamton's actual 2025 promo placement by day of week.

    This is the full picture, not just recurring promos. Tells the LLM
    where fireworks, giveaways, and food/drink deals currently land so it
    can recommend "change" status on days where the placement does not
    match what successful teams do.

    Uses game_features.day_of_week (Mon=0..Sun=6).
    """
    df = pd.read_sql(text(f"""
        SELECT day_of_week,
               COUNT(*) AS n_games,
               SUM(CASE WHEN has_fireworks   THEN 1 ELSE 0 END) AS n_fireworks,
               SUM(CASE WHEN has_giveaway    THEN 1 ELSE 0 END) AS n_giveaways,
               SUM(CASE WHEN has_recurring   THEN 1 ELSE 0 END) AS n_recurring,
               SUM(CASE WHEN has_food_deal   THEN 1 ELSE 0 END) AS n_food_drink,
               SUM(CASE WHEN has_kids_event  THEN 1 ELSE 0 END) AS n_kids,
               SUM(CASE WHEN has_community   THEN 1 ELSE 0 END) AS n_community,
               SUM(CASE WHEN has_theme_night THEN 1 ELSE 0 END) AS n_theme
          FROM milb.game_features
         WHERE team_id = {BINGHAMTON_ID}
           AND season = 2025
           AND game_type = 'R'
           AND attendance IS NOT NULL
         GROUP BY day_of_week
         ORDER BY day_of_week
    """), engine)
    dow_to_label = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    by_day = {label: {"n_games": 0} for label in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
    for _, row in df.iterrows():
        label = dow_to_label.get(int(row["day_of_week"]))
        if not label:
            continue
        n = int(row["n_games"]) or 1
        by_day[label] = {
            "n_games":     int(row["n_games"]),
            "fireworks":   f"{int(row['n_fireworks'])}/{int(row['n_games'])}",
            "giveaways":   f"{int(row['n_giveaways'])}/{int(row['n_games'])}",
            "recurring":   f"{int(row['n_recurring'])}/{int(row['n_games'])}",
            "food_drink":  f"{int(row['n_food_drink'])}/{int(row['n_games'])}",
            "kids":        f"{int(row['n_kids'])}/{int(row['n_games'])}",
            "community":   f"{int(row['n_community'])}/{int(row['n_games'])}",
            "theme":       f"{int(row['n_theme'])}/{int(row['n_games'])}",
        }
    return [{"day": d, **by_day[d]} for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]]


def load_winning_team_dow_patterns() -> list[dict]:
    """For top-cap-util teams across all MiLB (top quartile per level),
    what share of their games on each day-of-week carry each promo type.

    Gives the LLM "this is what winning clubs do on each day" context that
    isn't restricted to Double-A and isn't biased by branded-recurring
    naming conventions.
    """
    df = pd.read_sql(text("""
        WITH season_avg AS (
          SELECT team_id, sport_id, AVG(capacity_utilization) AS cap_util
            FROM milb.game_features
           WHERE season = 2025 AND game_type = 'R' AND attendance IS NOT NULL
           GROUP BY team_id, sport_id
          HAVING COUNT(*) >= 50
        ),
        winners AS (
          SELECT team_id FROM (
            SELECT team_id, NTILE(4) OVER (PARTITION BY sport_id ORDER BY cap_util DESC) AS q
              FROM season_avg
          ) r WHERE q = 1
        )
        SELECT gf.day_of_week,
               ROUND(100.0 * AVG(CASE WHEN gf.has_fireworks   THEN 1 ELSE 0 END), 0) AS fw_pct,
               ROUND(100.0 * AVG(CASE WHEN gf.has_giveaway    THEN 1 ELSE 0 END), 0) AS gv_pct,
               ROUND(100.0 * AVG(CASE WHEN gf.has_recurring   THEN 1 ELSE 0 END), 0) AS rec_pct,
               ROUND(100.0 * AVG(CASE WHEN gf.has_food_deal   THEN 1 ELSE 0 END), 0) AS food_pct,
               ROUND(100.0 * AVG(CASE WHEN gf.has_kids_event  THEN 1 ELSE 0 END), 0) AS kids_pct,
               ROUND(100.0 * AVG(CASE WHEN gf.has_community   THEN 1 ELSE 0 END), 0) AS comm_pct,
               ROUND(100.0 * AVG(CASE WHEN gf.has_theme_night THEN 1 ELSE 0 END), 0) AS theme_pct,
               COUNT(*) AS n
          FROM milb.game_features gf
          JOIN winners w ON w.team_id = gf.team_id
         WHERE gf.season = 2025 AND gf.game_type = 'R' AND gf.attendance IS NOT NULL
         GROUP BY gf.day_of_week
         ORDER BY gf.day_of_week
    """), engine)
    dow_to_label = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    out = []
    for _, row in df.iterrows():
        label = dow_to_label.get(int(row["day_of_week"]))
        if not label or int(row["n"]) < 20:
            # Skip Mondays / sparse days where the sample is too small to mean anything
            continue
        out.append({
            "day":           label,
            "fireworks_pct": int(row["fw_pct"]),
            "giveaway_pct":  int(row["gv_pct"]),
            "recurring_pct": int(row["rec_pct"]),
            "food_drink_pct": int(row["food_pct"]),
            "kids_pct":      int(row["kids_pct"]),
            "community_pct": int(row["comm_pct"]),
            "theme_pct":     int(row["theme_pct"]),
            "n_games":       int(row["n"]),
        })
    return out


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


def build_user_prompt(
    current: list[dict],
    peers: list[dict],
    rp_dow: list[dict],
    winners_dow: list[dict],
) -> str:
    # 1. A clean per-day picture of what RP is currently doing
    day_map = build_day_map(current)
    rp_dow_by_day = {d["day"]: d for d in rp_dow}
    winners_by_day = {d["day"]: d for d in winners_dow}

    rp_state_lines = []
    for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
        if d == "Mon":
            rp_state_lines.append(f"- Mon: NO home games scheduled (team convention).")
            continue
        rp_day = rp_dow_by_day.get(d, {})
        n = rp_day.get("n_games", 0)
        ritual_names = day_map.get(d, [])
        pieces = []
        if ritual_names:
            pieces.append("recurring ritual: " + ", ".join(ritual_names))
        # Show what the day's promo placement looks like
        for label, key in [("fireworks", "fireworks"), ("giveaway", "giveaways"),
                           ("food/drink", "food_drink"), ("kids", "kids"),
                           ("community", "community"), ("theme", "theme")]:
            val = rp_day.get(key)
            if val and val != f"0/{n}":
                pieces.append(f"{label} on {val} of games")
        if not pieces:
            pieces.append("no anchor format")
        rp_state_lines.append(f"- {d}: {'; '.join(pieces)} ({n} home games).")
    rp_state = "\n".join(rp_state_lines)

    # 2. Winning-team patterns by day (what % of high-attendance team games carry each flag)
    winners_lines = []
    for d in ["Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
        wd = winners_by_day.get(d)
        if not wd:
            continue
        # Highlight the dominant format(s) for the day
        breakdown = []
        for label, key in [("fireworks", "fireworks_pct"), ("giveaway", "giveaway_pct"),
                           ("food/drink", "food_drink_pct"), ("kids", "kids_pct"),
                           ("community", "community_pct"), ("theme", "theme_pct"),
                           ("recurring", "recurring_pct")]:
            pct = wd.get(key, 0)
            if pct >= 25:
                breakdown.append(f"{label} {pct}%")
        winners_lines.append(f"- {d}: " + (", ".join(breakdown) if breakdown else "no dominant format"))
    winners_state = "\n".join(winners_lines)

    return f"""TEAM: Binghamton Rumble Ponies (Double-A, NY)

BINGHAMTON CURRENT STATE BY DAY OF WEEK (2025):
{rp_state}

KEY PRIOR FINDING (the Saturdays page) -- THESE ANSWERS ARE FIXED:
Binghamton currently runs Fireworks on Friday and Giveaways on Saturday.
A separate analysis on the same data showed that across teams where
Saturday outdraws Friday, Fireworks lands on Saturday and the standard
Giveaway lands on Friday. The recommendation is to swap the two.

You MUST return exactly these two entries for Friday and Saturday:

  - day Fri: status="change", category="giveaway",
    proposed_ritual="Giveaway night (moved from Saturday)",
    rationale references the Saturdays finding swap.
  - day Sat: status="change", category="fireworks",
    proposed_ritual="Fireworks night (moved from Friday)",
    rationale references the Saturdays finding swap.

Do not deviate on Friday or Saturday. Use your judgment only on Tue, Wed,
Thu, and Sun.

WHAT HIGH-ATTENDANCE TEAMS DO ON EACH DAY
(top quartile by capacity utilization across all of MiLB in 2025;
share of their home games carrying each flag, only formats >= 25% shown):
{winners_state}

PEER RITUAL FAMILIES ACROSS DOUBLE-A IN 2025
(only included for context; do not infer fireworks placement from this
because branded recurring fireworks names are biased toward Friday):
{json.dumps(peers, indent=2)}

YOUR TASK:
Return the 7-day JSON schedule.

- Monday: status="off", category="rest", proposed_ritual=null,
  rationale="No home games scheduled."

- Friday and Saturday: use the FIXED entries given above (the swap).

- For Tue, Wed, Thu, Sun: compare the Binghamton current state to what
  the high-attendance cohort does on the same day. Pick the call that
  best matches:
    keep   -- the current placement already matches what peers do.
    change -- the current placement diverges and a different format
              would be a better fit. Propose the new format.
    test   -- the day has no anchor format and peer data suggests a
              clear category to try.
    skip   -- the day genuinely should remain unbranded.

  Important constraints and observations:
  * Tuesday at this team is already a drink ritual (Twofer Tuesday is
    a two-for-one drink deal, not a ticket deal). High-attendance
    teams also lean food/drink on Tuesday. Tuesday should be "keep".
  * Wednesday at this team is We Care Wednesday (community / heritage).
    High-attendance teams are balanced on Wednesday across community,
    theme, recurring, and food/drink. Wednesday should be "keep".
  * Thursday at this team is Throwback Thursday (theme). At high-
    attendance teams, Thursday is dominated by food/drink at 51% of
    games (much higher than theme at 35%). This is a meaningful
    divergence. Recommend "change" on Thursday with a category-level
    proposal for an adult food/drink format such as "Thirsty Thursday"
    or "Adult food/drink night", noting that the existing Throwback
    Thursday could co-exist or be reformatted.
  * Sunday at high-attendance teams is dominated by kids and family
    formats (about 60% kids). Binghamton already runs Family Funday,
    Kids' Club Sundays, and Senior Sundays. Sunday should be "keep".

- Category-level proposals are encouraged. If you cannot confidently
  name a specific ritual, use a short category-level description in
  proposed_ritual (e.g. "Adult food/drink night" or "Community / heritage
  feature").
- Allowed category values exactly: "fireworks", "giveaway", "kids",
  "family/community", "food/drink", "ticket deal", "theme", "rest".
  Do not invent new categories.
- Return only the JSON object specified in the system prompt; do not
  include a current_ritual field.
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
    rp_dow = load_rp_full_dow_profile()
    winners_dow = load_winning_team_dow_patterns()
    console.print(
        f"  Current rituals: {len(current)}, peer families: {len(peers)}, "
        f"rp dow rows: {len(rp_dow)}, winners dow rows: {len(winners_dow)}"
    )

    prompt = build_user_prompt(current, peers, rp_dow, winners_dow)

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

    # Build a quick "what's actually placed today" descriptor from the data
    # for days that don't have a recurring ritual name. RP runs fireworks on
    # Friday and giveaways on Saturday today; the schedule should show that
    # as the current state even though those aren't tagged recurring.
    rp_dow_by_day = {d["day"]: d for d in rp_dow}

    def current_descriptor(day: str) -> str | None:
        names = day_map.get(day, [])
        seen, deduped = set(), []
        for n in names:
            key = n.lower().rstrip("s")
            if key not in seen:
                seen.add(key)
                deduped.append(n)
        if deduped:
            return ", ".join(deduped)
        info = rp_dow_by_day.get(day) or {}
        n = info.get("n_games", 0)
        if not n:
            return None
        # Surface placements that dominate the day (>= 50% of games)
        candidates = [
            ("Fireworks night", info.get("fireworks")),
            ("Giveaway night",  info.get("giveaways")),
            ("Food/drink night", info.get("food_drink")),
        ]
        for label, ratio in candidates:
            if ratio and "/" in ratio:
                num, den = ratio.split("/")
                if int(den) and int(num) / int(den) >= 0.5:
                    return label
        return None

    allowed_categories = {
        "fireworks", "giveaway", "kids", "family/community",
        "food/drink", "ticket deal", "theme", "rest",
    }
    allowed_statuses = {"off", "keep", "change", "test", "skip"}

    for day_entry in days:
        d = day_entry.get("day")
        day_entry["current_ritual"] = current_descriptor(d) if isinstance(d, str) else None

        # Validate / coerce status and category
        if day_entry.get("status") not in allowed_statuses:
            day_entry["status"] = "skip"
        if day_entry.get("category") not in allowed_categories:
            day_entry["category"] = "rest" if day_entry["status"] == "off" else "theme"

        if d == "Mon":
            # RP convention: no Monday home games. Force "off" regardless of
            # what the LLM returned.
            day_entry["status"] = "off"
            day_entry["category"] = "rest"
            day_entry["proposed_ritual"] = None
            day_entry["current_ritual"] = "No home game"
            if not day_entry.get("rationale"):
                day_entry["rationale"] = "Binghamton does not schedule home games on Monday."

        if d == "Fri":
            # The Saturdays finding gives the answer; override LLM if it
            # picked anything else.
            day_entry["status"] = "change"
            day_entry["category"] = "giveaway"
            day_entry["proposed_ritual"] = "Giveaway night (moved from Saturday)"
            if not day_entry.get("rationale") or "moved" not in str(day_entry.get("rationale", "")).lower():
                day_entry["rationale"] = (
                    "Per the Saturdays finding: move the standard giveaway to Friday "
                    "so Saturday can host fireworks."
                )

        if d == "Sat":
            day_entry["status"] = "change"
            day_entry["category"] = "fireworks"
            day_entry["proposed_ritual"] = "Fireworks night (moved from Friday)"
            if not day_entry.get("rationale") or "moved" not in str(day_entry.get("rationale", "")).lower():
                day_entry["rationale"] = (
                    "Per the Saturdays finding: move fireworks to Saturday for the "
                    "bigger draw, swap with Friday's giveaway."
                )

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
