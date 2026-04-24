"""Promo-stack effects: measures whether combining multiple promo flags
produces super-additive lift (synergy) or diminishing returns.

Writes milb.promo_stack_effects — one row per (sport_id, dow_label, flag_combo).
flag_combo is a sorted '+'-joined string of the flags that are TRUE on a game.

For each combo computes:
  - avg_att and baseline_att (no-promo games at same sport/dow)
  - lift_fans = avg_att - baseline_att
  - expected_additive = sum of each single-flag lift on the same (sport, dow)
  - synergy_fans = lift_fans - expected_additive
  - is_synergistic flag

Enumeration is bounded: we keep combos with n_games >= MIN_OBS so tiny combos
don't pollute the table. All DOWs are written plus a pooled 'All' row.

Usage:
    python scripts/analyze_stack_effects.py
    python scripts/analyze_stack_effects.py --season 2025 --force
"""

import argparse
import sys
from itertools import combinations
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

PROMO_FLAGS = [
    "has_fireworks", "has_giveaway", "has_food_deal", "has_ticket_deal",
    "has_theme_night", "has_kids_event", "has_heritage", "has_community",
    "has_entertain", "has_dog", "has_celebrity", "has_recurring",
]

DOW_LABELS = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

MIN_OBS = 20                       # combos with fewer games are dropped
MAX_COMBO_SIZE = 4                 # stack up to this many flags
BOOTSTRAP_ITERS = 1500
RNG = np.random.default_rng(42)

ANALYSIS_NAME = "stack_effects"


def should_run(force: bool) -> bool:
    if force:
        return True
    with engine.connect() as conn:
        last = conn.execute(text(f"""
            SELECT input_max_updated FROM milb.analysis_runs
            WHERE analysis_name = '{ANALYSIS_NAME}' AND status = 'completed'
            ORDER BY completed_at DESC LIMIT 1
        """)).fetchone()
        if last is None:
            return True
        current = conn.execute(text(
            "SELECT MAX(created_at) FROM milb.game_features"
        )).fetchone()
        return current[0] is None or last[0] is None or current[0] > last[0]


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


def load_games(season: int) -> pd.DataFrame:
    flag_cols = ", ".join(f"f.{c}" for c in PROMO_FLAGS)
    return pd.read_sql(text(f"""
        SELECT f.game_pk, f.team_id, f.sport_id, f.season, f.game_date,
               f.day_of_week, f.attendance, f.capacity_utilization,
               {flag_cols}
          FROM milb.game_features f
         WHERE f.season = :season
           AND f.game_type = 'R'
           AND f.attendance IS NOT NULL AND f.attendance > 0
    """), engine, params={"season": season})


def bootstrap_ci(vals: np.ndarray) -> tuple[float, float]:
    if len(vals) < 3:
        m = float(vals.mean()) if len(vals) else 0.0
        return (m, m)
    idx = RNG.integers(0, len(vals), size=(BOOTSTRAP_ITERS, len(vals)))
    means = vals[idx].mean(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def combo_label(flags: tuple[str, ...]) -> str:
    return "+".join(sorted(flags))


def single_flag_lifts(df_dow: pd.DataFrame, baseline: float) -> dict[str, float]:
    """Return single-flag lift (avg_with - baseline) for each flag, on this DOW subset."""
    out: dict[str, float] = {}
    for f in PROMO_FLAGS:
        if f not in df_dow.columns:
            continue
        sub = df_dow[df_dow[f].fillna(False).astype(bool)]
        if len(sub) < MIN_OBS:
            out[f] = 0.0
            continue
        out[f] = float(sub["attendance"].mean()) - baseline
    return out


def compute_for_partition(df: pd.DataFrame, sport_id: int, dow_label: str,
                          run_id: int) -> list[dict]:
    """df is filtered to this (sport, dow). Returns rows for promo_stack_effects."""
    # Baseline: no promo flags at all
    no_promo = df[~df[PROMO_FLAGS].fillna(False).any(axis=1)]
    if len(no_promo) < MIN_OBS:
        return []
    baseline = float(no_promo["attendance"].mean())

    singles = single_flag_lifts(df, baseline)

    rows: list[dict] = []

    # Identify candidate flags (those that appear often enough to stack)
    candidates = [f for f in PROMO_FLAGS
                  if f in df.columns
                  and df[f].fillna(False).astype(bool).sum() >= MIN_OBS]

    for size in range(1, min(MAX_COMBO_SIZE, len(candidates)) + 1):
        for combo in combinations(candidates, size):
            # Games where EXACTLY these flags are true (not superset)
            # Strictness: has every flag in combo AND no OTHER candidate flag.
            others = [f for f in candidates if f not in combo]
            mask_in = df[list(combo)].fillna(False).astype(bool).all(axis=1)
            if others:
                mask_out = ~df[others].fillna(False).astype(bool).any(axis=1)
            else:
                mask_out = pd.Series(True, index=df.index)
            sub = df[mask_in & mask_out]
            if len(sub) < MIN_OBS:
                continue

            att = sub["attendance"].astype(float).to_numpy()
            avg_att = float(att.mean())
            lo, hi = bootstrap_ci(att)

            expected_additive = sum(singles.get(f, 0.0) for f in combo)
            lift = avg_att - baseline
            synergy = lift - expected_additive

            rows.append({
                "sport_id": sport_id,
                "dow_label": dow_label,
                "flag_combo": combo_label(combo),
                "n_flags": size,
                "n_games": len(sub),
                "n_teams": int(sub["team_id"].nunique()),
                "avg_att": round(avg_att, 1),
                "baseline_att": round(baseline, 1),
                "lift_fans": round(lift, 1),
                "lift_pct": round(lift / baseline, 4) if baseline > 0 else None,
                "lift_ci_lo": round(lo - baseline, 1),
                "lift_ci_hi": round(hi - baseline, 1),
                "expected_additive": round(expected_additive, 1),
                "synergy_fans": round(synergy, 1),
                "is_synergistic": bool(synergy > 0 and lift > expected_additive * 1.05),
                "run_id": run_id,
            })

    return rows


def write_rows(rows: list[dict]):
    if not rows:
        console.print("[yellow]No rows produced.[/yellow]")
        return
    df = pd.DataFrame(rows)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE milb.promo_stack_effects"))
        df.to_sql("promo_stack_effects", conn, schema="milb",
                  if_exists="append", index=False)
    console.print(f"  Wrote {len(df):,} promo_stack_effects rows")


def print_top_synergies(rows: list[dict]):
    synergies = [r for r in rows if r.get("is_synergistic")]
    synergies.sort(key=lambda r: r["synergy_fans"], reverse=True)
    synergies = synergies[:15]
    if not synergies:
        console.print("[yellow]No synergistic combos found.[/yellow]")
        return
    t = Table(title="Top synergistic stacks (super-additive lift)")
    for col in ("Sport", "DOW", "Combo", "Games", "Lift", "Additive", "Synergy"):
        t.add_column(col)
    for r in synergies:
        t.add_row(
            str(r["sport_id"]), r["dow_label"],
            r["flag_combo"], str(r["n_games"]),
            f"{r['lift_fans']:+,.0f}",
            f"{r['expected_additive']:+,.0f}",
            f"[green]{r['synergy_fans']:+,.0f}[/green]",
        )
    console.print(t)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--season", type=int, default=None)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    if not should_run(args.force):
        console.print("[yellow]No new input data. Use --force to re-run.[/yellow]")
        return 0

    session = get_session()
    run_id = log_run_start(session)

    try:
        if args.season is None:
            # Prefer the latest COMPLETE season (>= 4000 games) — stack
            # combos need many samples.
            r = pd.read_sql(text("""
                SELECT season FROM milb.game_features
                 WHERE attendance IS NOT NULL
                 GROUP BY season HAVING COUNT(*) >= 4000
                 ORDER BY season DESC LIMIT 1
            """), engine)
            if r.empty:
                r = pd.read_sql(text(
                    "SELECT MAX(season) AS season FROM milb.game_features WHERE attendance IS NOT NULL"
                ), engine)
            args.season = int(r.iloc[0]["season"])
            console.print(f"Using latest complete season: [bold]{args.season}[/bold]")

        games = load_games(args.season)
        console.print(f"Loaded {len(games):,} games")

        games = games.copy()
        games["dow_label"] = games["day_of_week"].map(DOW_LABELS)

        all_rows: list[dict] = []

        for sport_id, level_df in games.groupby("sport_id"):
            # Per-DOW rows
            for dow_label, dow_df in level_df.groupby("dow_label"):
                all_rows.extend(compute_for_partition(dow_df, int(sport_id), dow_label, run_id))
            # Pooled-DOW row for this level
            all_rows.extend(compute_for_partition(level_df, int(sport_id), "All", run_id))

        write_rows(all_rows)
        print_top_synergies(all_rows)

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
