"""DOW x Promo lift heatmap: per-level, per-day-of-week, per-promo-flag,
compute the lift (avg attendance with flag minus avg without flag).

Writes milb.dow_promo_lift — answers "which day of week does fireworks hit
hardest at Double-A?" type questions. Powers the heatmap on the Hypothesis
Lab page.

Also stores the cap_util version so the page can normalize out venue size.

Usage:
    python scripts/analyze_dow_promo_heatmap.py
    python scripts/analyze_dow_promo_heatmap.py --season 2025 --force
"""

import argparse
import sys
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
LEVEL_NAMES = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}

MIN_OBS = 25
BOOTSTRAP_ITERS = 1500
RNG = np.random.default_rng(42)

ANALYSIS_NAME = "dow_promo_heatmap"


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


def bootstrap_diff_ci(with_vals: np.ndarray, without_vals: np.ndarray) -> tuple[float, float]:
    if len(with_vals) < 3 or len(without_vals) < 3:
        return (0.0, 0.0)
    wi = RNG.integers(0, len(with_vals),    size=(BOOTSTRAP_ITERS, len(with_vals)))
    ni = RNG.integers(0, len(without_vals), size=(BOOTSTRAP_ITERS, len(without_vals)))
    diffs = with_vals[wi].mean(axis=1) - without_vals[ni].mean(axis=1)
    return (float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5)))


def load_games(season: int) -> pd.DataFrame:
    flag_cols = ", ".join(f"f.{c}" for c in PROMO_FLAGS)
    return pd.read_sql(text(f"""
        SELECT f.game_pk, f.team_id, f.sport_id, f.day_of_week,
               f.attendance, f.capacity_utilization,
               {flag_cols}
          FROM milb.game_features f
         WHERE f.season = :season
           AND f.game_type = 'R'
           AND f.attendance IS NOT NULL AND f.attendance > 0
    """), engine, params={"season": season})


def compute_cell(df: pd.DataFrame, sport_id: int, dow_label: str,
                 flag: str, run_id: int) -> dict | None:
    if flag not in df.columns:
        return None
    has = df[flag].fillna(False).astype(bool)
    with_df = df[has]
    without_df = df[~has]
    if len(with_df) < MIN_OBS or len(without_df) < MIN_OBS:
        return None

    with_att = with_df["attendance"].astype(float).to_numpy()
    without_att = without_df["attendance"].astype(float).to_numpy()
    avg_with = float(with_att.mean())
    avg_without = float(without_att.mean())
    lift = avg_with - avg_without
    lo, hi = bootstrap_diff_ci(with_att, without_att)

    cap_with = with_df["capacity_utilization"].dropna().astype(float)
    cap_without = without_df["capacity_utilization"].dropna().astype(float)
    cap_with_mean = float(cap_with.mean()) if len(cap_with) else None
    cap_without_mean = float(cap_without.mean()) if len(cap_without) else None
    cap_lift = (
        cap_with_mean - cap_without_mean
        if cap_with_mean is not None and cap_without_mean is not None
        else None
    )

    return {
        "sport_id": sport_id,
        "dow_label": dow_label,
        "promo_type": flag,
        "n_games_with": len(with_df),
        "n_games_without": len(without_df),
        "avg_with": round(avg_with, 1),
        "avg_without": round(avg_without, 1),
        "lift_fans": round(lift, 1),
        "lift_pct": round(lift / avg_without, 4) if avg_without > 0 else None,
        "lift_ci_lo": round(lo, 1),
        "lift_ci_hi": round(hi, 1),
        "cap_util_with": round(cap_with_mean, 4) if cap_with_mean is not None else None,
        "cap_util_without": round(cap_without_mean, 4) if cap_without_mean is not None else None,
        "cap_util_lift": round(cap_lift, 4) if cap_lift is not None else None,
        "run_id": run_id,
    }


def write_rows(rows: list[dict]):
    df = pd.DataFrame([r for r in rows if r is not None])
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE milb.dow_promo_lift"))
        if not df.empty:
            df.to_sql("dow_promo_lift", conn, schema="milb",
                      if_exists="append", index=False)
    console.print(f"  Wrote {len(df):,} dow_promo_lift rows")


def print_aa_summary(rows: list[dict]):
    aa = [r for r in rows if r and r["sport_id"] == 12]
    if not aa:
        return
    t = Table(title="Double-A: lift_fans per DOW x promo (positive = promo wins)")
    t.add_column("DOW")
    for f in PROMO_FLAGS:
        t.add_column(f.replace("has_", ""))
    by_dow: dict[str, dict[str, float]] = {}
    for r in aa:
        by_dow.setdefault(r["dow_label"], {})[r["promo_type"]] = r["lift_fans"]
    for dow in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        if dow not in by_dow:
            continue
        cells = [f"{by_dow[dow].get(f, 0):+,.0f}" for f in PROMO_FLAGS]
        t.add_row(dow, *cells)
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
            # Prefer the latest COMPLETE season (>= 4000 games league-wide) so
            # per-DOW×promo cells have enough samples. Current year is often
            # partial and would produce mostly empty cells.
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
        games["dow_label"] = games["day_of_week"].map(DOW_LABELS)
        console.print(f"Loaded {len(games):,} games")

        rows: list[dict] = []
        for sport_id, level_df in games.groupby("sport_id"):
            for dow_label, dow_df in level_df.groupby("dow_label"):
                for flag in PROMO_FLAGS:
                    row = compute_cell(dow_df, int(sport_id), dow_label, flag, run_id)
                    if row is not None:
                        rows.append(row)

        write_rows(rows)
        print_aa_summary(rows)

        finalize_run(session, run_id, "completed", n=len(rows))
        return 0
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        finalize_run(session, run_id, "failed", err=str(e))
        raise
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
