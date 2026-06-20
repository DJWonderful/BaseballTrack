"""Run every analytics script in dependency order.

Two-command refresh:
    python scripts/collect_all.py            # raw MLB API data
    python scripts/run_all_analytics.py      # everything that reads from it

Idempotent by default: skips any script whose latest analysis_runs row is
status='completed' and completed today. Pass --force to re-run everything.
Pass --list to preview what would run without executing anything.

LLM-using scripts (peer_playbook) default to --skip-llm so this runs without
Ollama. Pass --with-llm to allow them to call the LLM.

Exit codes:
    0  -- every step succeeded (or was skipped)
    1  -- one step failed; subsequent steps not attempted

Run from repo root.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

# Make the repo root importable so we can read analysis_runs.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from sqlalchemy import create_engine, text  # noqa: E402


# -- Pipeline --------------------------------------------------------------
# (analysis_name in milb.analysis_runs, [argv tail], short description)
# Order = dependency order. Everything before generate_recommendations reads
# from milb.game_features; generate_recommendations reads from everything.

Step = tuple[str, list[str], str]

PIPELINE: list[Step] = [
    # Foundation -- every other step reads from milb.game_features.
    ("build_features",          ["scripts/build_features.py"],
     "flat feature table (one row per home game)"),

    # Independent layer -- all read from game_features; safe to run in any order.
    ("promo_lift",              ["scripts/analyze_promo_lift.py"],
     "OLS marginal lift per promo type (observational)"),
    ("promo_lift_cf",           ["scripts/analyze_promo_lift_counterfactual.py"],
     "counterfactual lift via S-learner (XGBoost)"),
    ("cluster_peers",           ["scripts/cluster_peers.py"],
     "K-Means peer clusters from market + ops variables"),
    ("cluster_promo_strategy",  ["scripts/cluster_promo_strategy.py"],
     "K-Means clusters from promo-strategy dimensions"),
    ("competitive_intel",       ["scripts/build_competitive_intel.py"],
     "weather profiles, momentum, weather-peer similarity"),
    ("weekend_gap",             ["scripts/analyze_weekend_gap.py"],
     "Fri vs Sat classification (sat_winner / loser / neutral)"),
    ("fireworks_swap",          ["scripts/analyze_fireworks_swap.py"],
     "counterfactual: what if fireworks moved Fri -> Sat"),
    ("stack_effects",           ["scripts/analyze_stack_effects.py"],
     "interaction lift from stacking multiple promos"),
    ("dow_promo_heatmap",       ["scripts/analyze_dow_promo_heatmap.py"],
     "DOW x promo-type attendance heatmap"),
    ("peer_playbook",           ["scripts/analyze_peer_playbook.py"],
     "RP vs weather-peer playbook (uses LLM unless --skip-llm)"),

    # Synthesis layer -- depends on everything above.
    ("recommendations",         ["scripts/generate_recommendations.py"],
     "prioritized team recommendations"),
]


# -- DB helpers -----------------------------------------------------------

def _engine():
    """Build a Postgres engine from .env. We only read analysis_runs here so
    we don't depend on streamlit_app/utils/db.py."""
    user = os.getenv("DB_USERNAME", "postgres")
    pwd  = os.getenv("DB_PASSWORD", "postgres")
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "5432")
    db   = os.getenv("DB_NAME", "baseball")
    return create_engine(f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{db}")


def already_ran_today(engine, analysis_name: str) -> bool:
    """True if analysis_name's most recent row is status='completed' today."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT status, completed_at
              FROM milb.analysis_runs
             WHERE analysis_name = :name
             ORDER BY started_at DESC
             LIMIT 1
        """), {"name": analysis_name}).fetchone()
    if not row:
        return False
    if row.status != "completed" or row.completed_at is None:
        return False
    return row.completed_at.date() == date.today()


# -- Runner ---------------------------------------------------------------

def run_step(step: Step, force: bool, with_llm: bool) -> tuple[bool, str]:
    """Returns (success, message). Streams subprocess output live."""
    name, argv_tail, _ = step
    cmd = [sys.executable] + argv_tail
    if force:
        cmd.append("--force")
    if name == "peer_playbook" and not with_llm:
        cmd.append("--skip-llm")

    print(f"\n>>> {name}  ({' '.join(cmd[1:])})", flush=True)
    t0 = time.time()
    result = subprocess.run(cmd, cwd=str(ROOT))
    elapsed = time.time() - t0
    if result.returncode == 0:
        return True, f"done in {elapsed:.1f}s"
    return False, f"exited {result.returncode} after {elapsed:.1f}s"


def main() -> int:
    p = argparse.ArgumentParser(description="Run every analytics script.")
    p.add_argument("--force", action="store_true",
                   help="Re-run every step even if it already completed today.")
    p.add_argument("--list", action="store_true",
                   help="Print the plan and exit without running anything.")
    p.add_argument("--with-llm", action="store_true",
                   help="Allow peer_playbook to call its LLM (default: --skip-llm).")
    args = p.parse_args()

    engine = _engine()

    # Decide skip vs run per step.
    plan: list[tuple[Step, bool]] = []  # (step, will_run)
    for step in PIPELINE:
        name = step[0]
        will_run = args.force or not already_ran_today(engine, name)
        plan.append((step, will_run))

    # Show the plan.
    print("\nAnalytics refresh plan")
    print("=" * 70)
    for step, will_run in plan:
        name, _, desc = step
        flag = "RUN " if will_run else "skip"
        print(f"  [{flag}]  {name:24s}  {desc}")
    print("=" * 70)
    n_run = sum(1 for _, w in plan if w)
    n_skip = len(plan) - n_run
    print(f"  {n_run} to run, {n_skip} to skip"
          + ("  (--force overrides skip)" if not args.force else ""))

    if args.list:
        return 0
    if n_run == 0:
        print("\nAll analytics already current as of today. Nothing to do.")
        return 0

    print()
    overall_t0 = time.time()
    for step, will_run in plan:
        if not will_run:
            continue
        ok, msg = run_step(step, force=args.force, with_llm=args.with_llm)
        if not ok:
            print(f"\nFAILED: {step[0]} ({msg})")
            print("Stopping. Fix the failure and re-run; completed steps will be "
                  "skipped automatically.")
            return 1
        print(f"<<< {step[0]}  {msg}")

    print(f"\nAll done in {time.time() - overall_t0:.1f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
