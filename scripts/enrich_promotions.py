"""Enrich game promotions with LLM-generated semantic categories via local Ollama.

Usage:
    python scripts/enrich_promotions.py                  # full run with qwen3:8b
    python scripts/enrich_promotions.py --limit 50       # test on 50 promos first
    python scripts/enrich_promotions.py --model llama3.2 # use a different model
    python scripts/enrich_promotions.py --batch-size 15  # smaller batches

Prerequisites:
    python scripts/migrate_promo_enrichment.py   (run once first)
    Ollama running: ollama serve
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from sqlalchemy import text

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from src.db.connection import get_session
from src.enrichers.promo_rules import classify as rules_classify
from src.utils.logger import get_logger

logger = get_logger("enrich_promotions")
console = Console()

OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_BATCH_SIZE = 20

# Valid values for promo_category — used to sanitize LLM output
VALID_CATEGORIES = {
    "fireworks", "giveaway", "food_deal", "ticket_deal", "theme_night",
    "heritage_night", "kids_event", "community_event", "entertainment",
    "recurring", "other",
}

VALID_AUDIENCES = {"kids", "families", "adults", "seniors", "military", "students", "all"}

SYSTEM_PROMPT = """You are a Minor League Baseball promotion classifier. Given a JSON array of promotions, return a JSON object with a "results" array containing one classification per promotion, in the same order.

CATEGORY FLAGS (boolean — multiple can be true for one promotion):
- is_fireworks: post-game or in-game fireworks show
- is_giveaway_item: physical item distributed to fans (hat, bobblehead, shirt, bag, magnet, jersey replica, etc.)
- is_food_deal: discounted or special food/drink offer ($2 hot dogs, Thirsty Thursday beer specials, Taco Tuesday, Wine Wednesday, etc.)
- is_ticket_deal: discounted admission price ($2 Tuesday, One-Price night, kids $5, etc.)
- is_theme_night: event built around a theme with costumes/décor (Star Wars Night, Harry Potter, 80s Night, Western Night, decade nights, etc.)
- is_heritage_night: cultural or community identity celebration (Latino Night, Irish Heritage, etc.)
- is_kids_event: activity specifically targeting children (Kids Run the Bases, Kids Eat Free, pregame clinics, Kids Club, etc.)
- is_community_event: charity drives, military appreciation, education days, local school/org nights, alumni nights
- is_autographs: player or celebrity autograph session or scheduled meet & greet
- is_entertainment: non-fireworks post-game entertainment (drone show, live concert, DJ, comedy night, magic show, etc.)
- is_recurring: explicitly a weekly or every-game recurring feature — NOT a one-off event (Thirsty Thursday every Thursday, Tito's Vodka pregame every game, Sunday Funday every Sunday, etc.)
- is_dog_friendly: pets or dogs allowed at the game, or Bark in the Park event

ADDITIONAL FIELDS:
- promo_category: single best-fit label. Must be exactly one of: fireworks, giveaway, food_deal, ticket_deal, theme_night, heritage_night, kids_event, community_event, entertainment, recurring, other
- giveaway_limit: integer if "first N fans" is stated (e.g. "first 1000 fans" → 1000), otherwise null
- target_audience: one of exactly: kids, families, adults, seniors, military, students, all
- has_celebrity: true if a named real person (MLB player, celebrity, public figure) is scheduled to appear
- llm_notes: very brief note only if the promotion is ambiguous or unusual — otherwise null

IMPORTANT RULES:
- is_recurring should only be true for things that happen every week/game, not one-off annual events like "Star Wars Night"
- A giveaway combined with fireworks gets BOTH is_giveaway_item=true AND is_fireworks=true
- Thirsty Thursday is both is_food_deal=true and is_recurring=true
- Kids Run the Bases after a Fireworks Friday = is_fireworks=true AND is_kids_event=true
- Return ONLY the JSON object. No explanation, no markdown fences, no preamble.

Output format:
{"results": [{"promotion_id": <int>, "promo_category": <str>, "is_fireworks": <bool>, "is_giveaway_item": <bool>, "is_food_deal": <bool>, "is_ticket_deal": <bool>, "is_theme_night": <bool>, "is_heritage_night": <bool>, "is_kids_event": <bool>, "is_community_event": <bool>, "is_autographs": <bool>, "is_entertainment": <bool>, "is_recurring": <bool>, "is_dog_friendly": <bool>, "has_celebrity": <bool>, "giveaway_limit": <int|null>, "target_audience": <str>, "llm_notes": <str|null>}, ...]}"""


def sanitize(result: dict) -> dict:
    """Coerce LLM output to valid types. Returns cleaned dict."""
    def b(v):
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        return bool(v) if v is not None else False

    cat = result.get("promo_category", "other")
    if cat not in VALID_CATEGORIES:
        cat = "other"

    audience = result.get("target_audience", "all")
    if audience not in VALID_AUDIENCES:
        audience = "all"

    limit = result.get("giveaway_limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = None

    return {
        "promotion_id":     result.get("promotion_id"),
        "promo_category":   cat,
        "is_fireworks":     b(result.get("is_fireworks")),
        "is_giveaway_item": b(result.get("is_giveaway_item")),
        "is_food_deal":     b(result.get("is_food_deal")),
        "is_ticket_deal":   b(result.get("is_ticket_deal")),
        "is_theme_night":   b(result.get("is_theme_night")),
        "is_heritage_night":b(result.get("is_heritage_night")),
        "is_kids_event":    b(result.get("is_kids_event")),
        "is_community_event":b(result.get("is_community_event")),
        "is_autographs":    b(result.get("is_autographs")),
        "is_entertainment": b(result.get("is_entertainment")),
        "is_recurring":     b(result.get("is_recurring")),
        "is_dog_friendly":  b(result.get("is_dog_friendly")),
        "has_celebrity":    b(result.get("has_celebrity")),
        "giveaway_limit":   limit,
        "target_audience":  audience,
        "llm_notes":        result.get("llm_notes") or None,
    }


def call_ollama(client: httpx.Client, batch_input: list[dict], model: str) -> list[dict] | None:
    """Send a batch to Ollama, return list of sanitized result dicts or None on failure."""
    options = {"temperature": 0.1, "num_predict": 8192}
    # Disable chain-of-thought for qwen3 — not needed for classification, saves time
    if "qwen3" in model:
        options["think"] = False

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(batch_input, ensure_ascii=False)},
        ],
        "stream": False,
        "format": "json",
        "options": options,
    }

    try:
        resp = client.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=180)
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        parsed = json.loads(content)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Ollama call failed: {e}")
        return None

    # Extract the results array — handle {"results": [...]} or bare [...]
    if isinstance(parsed, list):
        results_list = parsed
    elif isinstance(parsed, dict):
        results_list = parsed.get("results") or next(
            (v for v in parsed.values() if isinstance(v, list)), None
        )
    else:
        results_list = None

    if not results_list:
        logger.warning("Ollama returned no parseable results array")
        return None

    # Align by promotion_id — build a lookup in case model reorders
    id_to_result = {}
    for item in results_list:
        if isinstance(item, dict) and item.get("promotion_id") is not None:
            id_to_result[int(item["promotion_id"])] = sanitize(item)

    # Return in original order, skipping any the model missed
    ordered = []
    for row in batch_input:
        pid = row["promotion_id"]
        if pid in id_to_result:
            ordered.append(id_to_result[pid])
        else:
            logger.debug(f"Model skipped promotion_id {pid}")

    return ordered if ordered else None


def upsert_results(session, results: list[dict], model: str):
    """Write LLM enrichment results back to the DB."""
    for r in results:
        if not r.get("promotion_id"):
            continue
        session.execute(text("""
            UPDATE milb.game_promotions SET
                promo_category      = :promo_category,
                is_fireworks        = :is_fireworks,
                is_giveaway_item    = :is_giveaway_item,
                is_food_deal        = :is_food_deal,
                is_ticket_deal      = :is_ticket_deal,
                is_theme_night      = :is_theme_night,
                is_heritage_night   = :is_heritage_night,
                is_kids_event       = :is_kids_event,
                is_community_event  = :is_community_event,
                is_autographs       = :is_autographs,
                is_entertainment    = :is_entertainment,
                is_recurring        = :is_recurring,
                is_dog_friendly     = :is_dog_friendly,
                has_celebrity       = :has_celebrity,
                giveaway_limit      = :giveaway_limit,
                target_audience     = :target_audience,
                llm_notes           = :llm_notes,
                llm_model           = :llm_model,
                llm_enriched_at     = NOW(),
                enrichment_method   = 'llm'
            WHERE promotion_id = :promotion_id
        """), {**r, "llm_model": model})


def upsert_rules_results(session, results: list[dict]):
    """Write rules-tier enrichment results. Updates the flag columns + sets
    rules_enriched_at and enrichment_method='rules'. Deliberately leaves
    llm_enriched_at / llm_model / llm_notes alone -- if the row was
    previously LLM-classified and --force caused rules to take over, we
    still want the historical LLM timestamp available for audit. The
    `enrichment_method` column is the single source of truth for which
    tier's flags are currently stored."""
    for r in results:
        if not r.get("promotion_id"):
            continue
        session.execute(text("""
            UPDATE milb.game_promotions SET
                promo_category      = :promo_category,
                is_fireworks        = :is_fireworks,
                is_giveaway_item    = :is_giveaway_item,
                is_food_deal        = :is_food_deal,
                is_ticket_deal      = :is_ticket_deal,
                is_theme_night      = :is_theme_night,
                is_heritage_night   = :is_heritage_night,
                is_kids_event       = :is_kids_event,
                is_community_event  = :is_community_event,
                is_autographs       = :is_autographs,
                is_entertainment    = :is_entertainment,
                is_recurring        = :is_recurring,
                is_dog_friendly     = :is_dog_friendly,
                has_celebrity       = :has_celebrity,
                giveaway_limit      = :giveaway_limit,
                target_audience     = :target_audience,
                rules_enriched_at   = NOW(),
                enrichment_method   = 'rules'
            WHERE promotion_id = :promotion_id
        """), r)


def check_ollama(model: str) -> bool:
    """Verify Ollama is running and the model is available."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        available = [m["name"] for m in resp.json().get("models", [])]
        # Match by prefix (e.g. "qwen3:8b" matches "qwen3:8b")
        if not any(model in m or m.startswith(model.split(":")[0]) for m in available):
            console.print(f"[red]Model '{model}' not found in Ollama. Available:[/red]")
            for m in available:
                console.print(f"  {m}")
            return False
        return True
    except httpx.ConnectError:
        console.print("[red]Cannot connect to Ollama at localhost:11434. Is it running?[/red]")
        console.print("  Start it with: ollama serve")
        return False


def _rules_row_to_upsert(promotion_id: int, rules_out: dict) -> dict:
    """Map the rules classifier's output into the same shape upsert expects."""
    flag_keys = [
        "is_fireworks", "is_giveaway_item", "is_food_deal", "is_ticket_deal",
        "is_theme_night", "is_heritage_night", "is_kids_event",
        "is_community_event", "is_autographs", "is_entertainment",
        "is_recurring", "is_dog_friendly", "has_celebrity",
    ]
    return {
        "promotion_id":    promotion_id,
        "promo_category":  rules_out["promo_category"],
        "giveaway_limit":  rules_out.get("giveaway_limit"),
        "target_audience": rules_out["target_audience"],
        **{k: bool(rules_out.get(k, False)) for k in flag_keys},
    }


def main(model: str = DEFAULT_MODEL, batch_size: int = DEFAULT_BATCH_SIZE,
         limit: int | None = None, force: bool = False,
         rules_only: bool = False, skip_rules: bool = False):
    console.print(f"\n[bold blue]=== Promotion Enrichment (rules + LLM) ===[/bold blue]")
    console.print(f"  Model:      {model}")
    console.print(f"  Batch size: {batch_size}")
    if limit:
        console.print(f"  Limit:      {limit} (test mode)")
    if force:
        console.print(f"  [yellow]--force: re-enriching ALL promotions (ignoring existing results)[/yellow]")
    if rules_only:
        console.print(f"  [yellow]--rules-only: skipping LLM; only writing rules-tier matches[/yellow]")
    if skip_rules:
        console.print(f"  [yellow]--skip-rules: bypassing rules layer, LLM only (legacy behaviour)[/yellow]")
    console.print()

    if not rules_only and not check_ollama(model):
        sys.exit(1)

    session = get_session()
    try:
        # Check migration has been run
        try:
            session.execute(text(
                "SELECT llm_enriched_at, rules_enriched_at FROM milb.game_promotions LIMIT 1"
            ))
        except Exception:
            console.print("[red]Migration not yet run. Apply sql/016_add_promo_rules_enrichment.sql[/red]")
            sys.exit(1)

        # Select rows that still need enrichment. Skip if either tier has
        # already produced a classification, unless --force.
        if force:
            where = "offer_name IS NOT NULL"
        else:
            where = ("offer_name IS NOT NULL "
                    "AND llm_enriched_at IS NULL "
                    "AND rules_enriched_at IS NULL")
        query = f"""
            SELECT promotion_id, offer_name, offer_type, description
            FROM milb.game_promotions
            WHERE {where}
            ORDER BY promotion_id
        """
        if limit:
            query += f" LIMIT {limit}"

        rows = session.execute(text(query)).fetchall()

        if not rows:
            console.print("[green]All promotions already enriched -- nothing to do.[/green]")
            return

        start = time.time()

        # ===== Phase 1: rules pre-pass ================================
        rules_batch = []  # list[dict] ready for upsert
        llm_rows = []      # list[Row] needing LLM
        if skip_rules:
            llm_rows = list(rows)
        else:
            console.print(f"[cyan]Rules pre-pass on {len(rows):,} promotions...[/cyan]")
            for row in rows:
                classification = rules_classify(row[1], row[2], row[3])
                if classification is None:
                    llm_rows.append(row)
                else:
                    rules_batch.append(_rules_row_to_upsert(row[0], classification))

            if rules_batch:
                console.print(f"  Rules matched: {len(rules_batch):,} "
                              f"({len(rules_batch)/len(rows)*100:.1f}%)")
                try:
                    upsert_rules_results(session, rules_batch)
                    session.commit()
                except Exception as e:
                    session.rollback()
                    logger.error(f"Rules batch write failed: {e}")
                    raise

        if rules_only:
            elapsed = time.time() - start
            console.print(f"\n[bold green]Rules-only pass complete[/bold green]")
            console.print(f"  Matched:   {len(rules_batch):,}")
            console.print(f"  Residuals: {len(llm_rows):,}  (would go to LLM)")
            console.print(f"  Elapsed:   {elapsed:.1f}s")
            return

        # ===== Phase 2: LLM fallback for residuals ====================
        if not llm_rows:
            elapsed = time.time() - start
            console.print(f"\n[bold green]All promotions covered by rules -- no LLM calls needed.[/bold green]")
            console.print(f"  Elapsed: {elapsed:.1f}s")
            return

        total = len(llm_rows)
        total_batches = (total + batch_size - 1) // batch_size
        console.print(f"[cyan]LLM on residuals: {total:,} promotions in {total_batches} batches[/cyan]\n")

        enriched = 0
        failed_batches = 0

        with httpx.Client() as client:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
            ) as progress:
                task = progress.add_task("LLM enriching", total=total_batches)

                for i in range(0, total, batch_size):
                    chunk = llm_rows[i : i + batch_size]

                    batch_input = [
                        {
                            "promotion_id": row[0],
                            "offer_name":   row[1] or "",
                            "offer_type":   row[2] or "",
                            # Truncate long descriptions -- model doesn't need every word
                            "description":  (row[3] or "")[:400],
                        }
                        for row in chunk
                    ]

                    results = call_ollama(client, batch_input, model)

                    if results:
                        try:
                            upsert_results(session, results, model)
                            session.commit()
                            enriched += len(results)
                        except Exception as e:
                            session.rollback()
                            logger.warning(f"Batch {i // batch_size + 1} DB write failed: {e}")
                            failed_batches += 1
                    else:
                        failed_batches += 1

                    progress.advance(task)

        elapsed = time.time() - start
        console.print(f"\n[bold green]Enrichment complete[/bold green]")
        console.print(f"  Rules matched:  {len(rules_batch):,}")
        console.print(f"  LLM enriched:   {enriched:,} / {total:,}")
        console.print(f"  Failed batches: {failed_batches}")
        console.print(f"  Elapsed:        {elapsed / 60:.1f} min")

        # Summary breakdown
        r = session.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE enrichment_method = 'rules') AS rules_cnt,
                COUNT(*) FILTER (WHERE enrichment_method = 'llm')   AS llm_cnt,
                COUNT(*) FILTER (WHERE is_fireworks)      AS fireworks,
                COUNT(*) FILTER (WHERE is_giveaway_item)  AS giveaways,
                COUNT(*) FILTER (WHERE is_food_deal)      AS food_deals,
                COUNT(*) FILTER (WHERE is_ticket_deal)    AS ticket_deals,
                COUNT(*) FILTER (WHERE is_theme_night)    AS theme_nights,
                COUNT(*) FILTER (WHERE is_kids_event)     AS kids_events,
                COUNT(*) FILTER (WHERE is_recurring)      AS recurring,
                COUNT(*) FILTER (WHERE is_dog_friendly)   AS dog_friendly
            FROM milb.game_promotions
        """)).fetchone()

        if r and (r[0] or r[1]):
            total_enriched = (r[0] or 0) + (r[1] or 0)
            console.print(f"\n  [bold]Enrichment method (all rows):[/bold]")
            console.print(f"    Rules:        {r[0] or 0:,}  "
                          f"({(r[0] or 0)/max(total_enriched,1)*100:.1f}%)")
            console.print(f"    LLM:          {r[1] or 0:,}  "
                          f"({(r[1] or 0)/max(total_enriched,1)*100:.1f}%)")
            console.print(f"\n  [bold]Category breakdown (all enriched):[/bold]")
            console.print(f"    Fireworks:    {r[2]}")
            console.print(f"    Giveaways:    {r[3]}")
            console.print(f"    Food deals:   {r[4]}")
            console.print(f"    Ticket deals: {r[5]}")
            console.print(f"    Theme nights: {r[6]}")
            console.print(f"    Kids events:  {r[7]}")
            console.print(f"    Recurring:    {r[8]}")
            console.print(f"    Dog friendly: {r[9]}")

    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich promotions: rules tier + LLM fallback")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Ollama model name (default: {DEFAULT_MODEL})")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Promotions per LLM call (default: {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process N promotions (useful for testing)")
    parser.add_argument("--force", action="store_true",
                        help="Re-enrich all promotions, overwriting existing results")
    parser.add_argument("--rules-only", action="store_true",
                        help="Run only the rules tier; skip LLM entirely. Useful for "
                             "populating rules-tier on existing data without spending LLM time.")
    parser.add_argument("--skip-rules", action="store_true",
                        help="Skip the rules tier entirely; LLM handles every promo "
                             "(legacy pre-rules behaviour).")
    args = parser.parse_args()
    main(
        model=args.model, batch_size=args.batch_size,
        limit=args.limit, force=args.force,
        rules_only=args.rules_only, skip_rules=args.skip_rules,
    )
