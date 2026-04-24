"""Audit the rules-tier promo classifier against real data.

This runs the rules layer over every promo (regardless of what's stored in
the DB currently) and prints:
  1. Overall coverage: how many promos get a definitive classification.
  2. Per-category hit counts and sample names (so you can eyeball that e.g.
     'heritage_night' matches are in fact heritage events).
  3. Residuals: the first N names that get no rule match. These would all
     fall through to the LLM.
  4. Disagreements vs the current stored classification (for rows already
     enriched by the LLM): flags where rules and LLM disagree by type.

No writes to the DB. Use this before trusting the rules tier or before
committing new rules.

Usage:
    python scripts/audit_promo_rules.py
    python scripts/audit_promo_rules.py --category heritage_night
    python scripts/audit_promo_rules.py --sample 50
    python scripts/audit_promo_rules.py --disagreement giveaway
"""

import argparse
import random
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from rich.console import Console
from rich.table import Table

from src.db.connection import engine
from src.enrichers.promo_rules import classify, PROMO_RULES

console = Console()

FLAG_COLS = [
    "is_fireworks", "is_giveaway_item", "is_food_deal", "is_ticket_deal",
    "is_theme_night", "is_heritage_night", "is_kids_event",
    "is_community_event", "is_autographs", "is_entertainment",
    "is_recurring", "is_dog_friendly", "has_celebrity",
]


def load_all_promos() -> pd.DataFrame:
    return pd.read_sql(text(f"""
        SELECT promotion_id, offer_name, offer_type, description,
               promo_category AS stored_category,
               target_audience AS stored_audience,
               llm_enriched_at, rules_enriched_at, enrichment_method,
               {', '.join(FLAG_COLS)}
          FROM milb.game_promotions
    """), engine)


def run_classifier(df: pd.DataFrame) -> pd.DataFrame:
    results = []
    for _, row in df.iterrows():
        out = classify(row["offer_name"], row["offer_type"], row["description"])
        rec = {
            "promotion_id": row["promotion_id"],
            "offer_name":   row["offer_name"],
            "rules_hit":    out is not None,
            "rules_category": out["promo_category"] if out else None,
            "rules_audience": out["target_audience"] if out else None,
            "rules_matched":  ",".join(out["rules_matched"]) if out else None,
        }
        for flag in FLAG_COLS:
            rec[f"rules_{flag}"] = bool(out[flag]) if out else None
        results.append(rec)
    return pd.DataFrame(results)


def overall_coverage(rules: pd.DataFrame):
    total = len(rules)
    hit = int(rules["rules_hit"].sum())
    t = Table(title="Rules-tier coverage")
    t.add_column("Metric"); t.add_column("Count", justify="right"); t.add_column("Pct", justify="right")
    t.add_row("Total promotions", f"{total:,}", "-")
    t.add_row("Rules classified",  f"{hit:,}",  f"{hit/total*100:.1f}%")
    t.add_row("LLM residuals",     f"{total - hit:,}", f"{(total - hit)/total*100:.1f}%")
    console.print(t); console.print()


def per_category(rules: pd.DataFrame, sample_n: int, category_filter: str | None):
    hits = rules[rules["rules_hit"]].copy()
    if category_filter:
        hits = hits[hits["rules_category"] == category_filter]
    if hits.empty:
        console.print("[yellow]No rule matches for that filter.[/yellow]"); return

    by_cat = hits.groupby("rules_category").size().sort_values(ascending=False)
    t = Table(title="Rule matches by category")
    t.add_column("Category"); t.add_column("Count", justify="right")
    for cat, n in by_cat.items():
        t.add_row(str(cat), f"{int(n):,}")
    console.print(t); console.print()

    # Samples per category
    rng = random.Random(42)
    console.print(f"[bold]Sample of {sample_n} hit names per category:[/bold]\n")
    for cat in by_cat.index:
        sub = hits[hits["rules_category"] == cat]
        pick = sub.sample(min(sample_n, len(sub)), random_state=rng.randint(0, 10**9))
        console.print(f"[cyan]{cat}[/cyan] ({len(sub):,} total)")
        for _, row in pick.iterrows():
            matched = row["rules_matched"] or ""
            console.print(f"  * {row['offer_name']}  [dim]({matched})[/dim]")
        console.print()


def residual_sample(rules: pd.DataFrame, sample_n: int):
    res = rules[~rules["rules_hit"]].copy()
    if res.empty:
        console.print("[green]No residuals. Rules cover everything.[/green]"); return
    console.print(f"[bold]Sample of {sample_n} unmatched (LLM-bound) names:[/bold]\n")
    pick = res.sample(min(sample_n, len(res)), random_state=42)
    for _, row in pick.iterrows():
        console.print(f"  * {row['offer_name']}")
    console.print()


def disagreement_check(all_df: pd.DataFrame, rules: pd.DataFrame,
                       flag: str | None):
    """Where rules and previously-stored LLM flags disagree, on each flag
    column. Useful to spot false positives in rules."""
    merged = all_df.merge(rules, on="promotion_id", how="inner")
    # Only check rows that have BOTH a rules classification AND a prior LLM result
    merged = merged[(merged["rules_hit"]) & (merged["llm_enriched_at"].notna())]
    if merged.empty:
        console.print("[yellow]No overlap with prior LLM-classified rows.[/yellow]"); return

    flags = [flag] if flag else FLAG_COLS
    console.print("[bold]Rules vs LLM disagreement (prior LLM classifications as ground truth reference):[/bold]")
    t = Table()
    t.add_column("Flag"); t.add_column("Rules=T LLM=F", justify="right")
    t.add_column("Rules=F LLM=T", justify="right"); t.add_column("Rules ok", justify="right")
    for f in flags:
        rcol = f"rules_{f}"
        if rcol not in merged.columns or f not in merged.columns:
            continue
        rules_true_llm_false = int(((merged[rcol] == True) & (merged[f] == False)).sum())
        rules_false_llm_true = int(((merged[rcol] == False) & (merged[f] == True)).sum())
        agree = int(((merged[rcol] == merged[f]) & merged[rcol].notna()).sum())
        t.add_row(f, str(rules_true_llm_false), str(rules_false_llm_true), str(agree))
    console.print(t); console.print()

    if flag:
        # Print a few disagreeing rows for the specified flag
        sub_tf = merged[(merged[f"rules_{flag}"] == True) & (merged[flag] == False)]
        sub_ft = merged[(merged[f"rules_{flag}"] == False) & (merged[flag] == True)]
        if not sub_tf.empty:
            console.print(f"[bold red]Rules set {flag}=True but LLM said False ({len(sub_tf)} rows):[/bold red]")
            for _, row in sub_tf.head(10).iterrows():
                console.print(f"  * {row['offer_name']}  [dim]({row.get('rules_matched')})[/dim]")
        if not sub_ft.empty:
            console.print(f"[bold yellow]Rules missed {flag} that LLM set ({len(sub_ft)} rows):[/bold yellow]")
            for _, row in sub_ft.head(10).iterrows():
                console.print(f"  * {row['offer_name']}")


def main():
    parser = argparse.ArgumentParser(description="Audit the rules-tier promo classifier")
    parser.add_argument("--sample", type=int, default=5,
                        help="Rows to print per category (default 5)")
    parser.add_argument("--category", type=str, default=None,
                        help="Limit sample display to one category")
    parser.add_argument("--disagreement", type=str, default=None,
                        help="Show rules-vs-LLM disagreement table; optionally "
                             "focus on one flag (e.g. is_fireworks)")
    parser.add_argument("--residuals", type=int, default=20,
                        help="Rows to show from the LLM-bound residuals")
    args = parser.parse_args()

    console.print("\n[bold blue]=== Promo Rules Audit ===[/bold blue]")
    console.print(f"Loaded {len(PROMO_RULES)} rules; running over all stored promotions.\n")

    df = load_all_promos()
    rules = run_classifier(df)

    overall_coverage(rules)
    per_category(rules, args.sample, args.category)
    residual_sample(rules, args.residuals)

    if args.disagreement is not None:
        # Empty string -> all flags; otherwise focus on one
        focus = args.disagreement if args.disagreement else None
        disagreement_check(df, rules, focus)
    else:
        disagreement_check(df, rules, None)


if __name__ == "__main__":
    main()
