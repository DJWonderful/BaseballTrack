"""Counterfactual promo lift via S-learner over the trained XGBoost models.

For every game in a promo-era season, predict attendance twice:
  - with the target promo flag forced ON
  - with it forced OFF
Lift = pred_on - pred_off (per game). The baseline is the model's estimate of
the *specific* game's attendance, not the team-season mean, so "rescue" promos
on slow Tuesdays get a fair counterfactual.

Three estimands per flag:
  ATE  - average over all games (the "generic X% boost" the business asks for)
  ATT  - average over games where the flag was actually on (effect on treated)
  ATU  - average over games where the flag was actually off (what-if analysis)

Aggregation scopes: league-wide, per level, per team.

Usage:
    python scripts/analyze_promo_lift_counterfactual.py
    python scripts/analyze_promo_lift_counterfactual.py --force
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sqlalchemy import text

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from rich.console import Console
from rich.table import Table
from src.db.connection import engine, get_session

console = Console()

MODEL_DIR = project_root / "models"

LEVEL_NAMES = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}

# Same feature-prep contract as train_attendance_model.py so the model sees
# rows that match what it was trained on.
EXCLUDE_COLS = {
    "game_pk", "game_date", "attendance", "capacity_utilization",
    "attendance_lift", "run_id", "created_at", "census_year",
}
CAT_COLS = ["team_id", "opponent_team_id", "weather_bucket", "day_night", "game_type", "population_trend", "start_time_bucket"]

PROMO_FLAGS = [
    "has_fireworks", "has_giveaway", "has_food_deal", "has_ticket_deal",
    "has_theme_night", "has_kids_event", "has_heritage", "has_community",
    "has_entertain", "has_dog", "has_celebrity", "has_recurring",
]

PROMO_LABELS = {
    "has_fireworks": "Fireworks", "has_giveaway": "Giveaway",
    "has_food_deal": "Food Deal", "has_ticket_deal": "Ticket Deal",
    "has_theme_night": "Theme Night", "has_kids_event": "Kids Event",
    "has_heritage": "Heritage", "has_community": "Community",
    "has_entertain": "Entertainment", "has_dog": "Dog Friendly",
    "has_celebrity": "Celebrity", "has_recurring": "Recurring",
}

MIN_TEAM_GAMES = 30  # skip per-team rollups below this


def should_run(force: bool) -> bool:
    if force:
        return True
    with engine.connect() as conn:
        last = conn.execute(text("""
            SELECT input_max_updated FROM milb.analysis_runs
            WHERE analysis_name = 'promo_lift_cf' AND status = 'completed'
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
    result = session.execute(text("""
        INSERT INTO milb.analysis_runs (analysis_name, input_max_updated, status)
        VALUES ('promo_lift_cf', :max_up, 'running')
        RETURNING run_id
    """), {"max_up": current[0]})
    session.commit()
    return result.fetchone()[0]


def load_features(sport_id: int) -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT * FROM milb.game_features
        WHERE sport_id = :sid
        ORDER BY game_date
    """), engine, params={"sid": sport_id})


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Mirror train_attendance_model.prepare_features exactly."""
    feat = df.drop(columns=[c for c in EXCLUDE_COLS if c in df.columns], errors="ignore")

    for col in feat.select_dtypes(include=["bool"]).columns:
        feat[col] = feat[col].astype(int)

    for col in feat.select_dtypes(include=["object"]).columns:
        uniq = set(feat[col].dropna().unique())
        if uniq.issubset({True, False}):
            feat[col] = feat[col].astype(float)

    for col in CAT_COLS:
        if col in feat.columns:
            feat[col] = feat[col].astype("category")

    return feat


def load_model(sport_id: int) -> xgb.XGBRegressor | None:
    level = LEVEL_NAMES[sport_id].lower().replace("-", "")
    path = MODEL_DIR / f"xgb_{level}_attendance.json"
    if not path.exists():
        return None
    m = xgb.XGBRegressor(enable_categorical=True)
    m.load_model(str(path))
    return m


def counterfactual_features(X: pd.DataFrame, flag: str, turn_on: bool) -> pd.DataFrame:
    """Return a copy of X with `flag` forced on/off and derived promo features
    adjusted so the row is internally consistent.

    We flip only the target flag, recompute promo_count and has_any_promo from
    all flag columns, and if the target is has_giveaway we also flip
    has_limited_giveaway (the limit is meaningless without a giveaway). We do
    NOT recompute days_since_last_fw / days_since_last_give — that would
    require re-simulating the full season schedule, which is out of scope for
    a per-game S-learner.
    """
    X2 = X.copy()
    new_val = 1 if turn_on else 0

    if flag not in X2.columns:
        return X2
    X2[flag] = new_val

    if flag == "has_giveaway" and "has_limited_giveaway" in X2.columns:
        # Turning giveaway off forces limited_giveaway off; turning it on
        # leaves limited flag at 0 (we don't fabricate a "limited" giveaway).
        if not turn_on:
            X2["has_limited_giveaway"] = 0

    present_flags = [f for f in PROMO_FLAGS if f in X2.columns]
    if "promo_count" in X2.columns:
        X2["promo_count"] = X2[present_flags].sum(axis=1).astype(X["promo_count"].dtype)
    if "has_any_promo" in X2.columns:
        any_promo = (X2[present_flags].sum(axis=1) > 0).astype(int)
        X2["has_any_promo"] = any_promo

    return X2


def summarize(lift: np.ndarray, baseline: np.ndarray) -> dict:
    """Compute the summary stats we store per (scope, flag, estimand)."""
    if len(lift) == 0:
        return {}
    pct = np.where(baseline > 0, lift / baseline, np.nan)
    return {
        "mean_lift": float(np.mean(lift)),
        "median_lift": float(np.median(lift)),
        "std_lift": float(np.std(lift, ddof=1)) if len(lift) > 1 else 0.0,
        "p10_lift": float(np.percentile(lift, 10)),
        "p90_lift": float(np.percentile(lift, 90)),
        "mean_pct_lift": float(np.nanmean(pct)) if np.isfinite(pct).any() else None,
        "pct_positive": float(np.mean(lift > 0)),
        "n_games": int(len(lift)),
    }


def build_records_for_flag(
    df: pd.DataFrame,
    lift: np.ndarray,
    actual_flag: np.ndarray,
    baseline: np.ndarray,
    sport_id: int,
    flag: str,
) -> list[dict]:
    """Emit league / level / per-team rows for one promo flag.

    At the league and level scopes we store all three estimands; at the team
    scope we store only ATE so the table stays readable.
    """
    rows = []
    base = {"promo_type": flag, "sport_id": sport_id, "team_id": None}

    for estimand, mask in [
        ("ATE", np.ones_like(actual_flag, dtype=bool)),
        ("ATT", actual_flag == 1),
        ("ATU", actual_flag == 0),
    ]:
        s = summarize(lift[mask], baseline[mask])
        if s:
            rows.append({**base, "scope": "level", "estimand": estimand, **s})

    team_ids = df["team_id"].values
    for tid in np.unique(team_ids):
        m = team_ids == tid
        if m.sum() < MIN_TEAM_GAMES:
            continue
        s = summarize(lift[m], baseline[m])
        if s:
            rows.append({
                "promo_type": flag, "sport_id": sport_id, "team_id": int(tid),
                "scope": "team", "estimand": "ATE", **s,
            })

    return rows


def print_league_table(league_rows: list[dict]):
    by_flag = {}
    for r in league_rows:
        by_flag.setdefault(r["promo_type"], {})[r["estimand"]] = r

    table = Table(title="League-wide counterfactual lift (all levels pooled)")
    table.add_column("Promo", style="bold")
    table.add_column("ATE (fans)", justify="right")
    table.add_column("ATE %", justify="right")
    table.add_column("ATT (fans)", justify="right")
    table.add_column("% pos", justify="right")
    table.add_column("p10 / p90", justify="right")
    table.add_column("n", justify="right")

    ordered = sorted(
        by_flag.items(),
        key=lambda kv: kv[1].get("ATE", {}).get("mean_lift", 0),
        reverse=True,
    )
    for flag, estimands in ordered:
        ate = estimands.get("ATE", {})
        att = estimands.get("ATT", {})
        if not ate:
            continue
        ate_lift = ate["mean_lift"]
        color = "green" if ate_lift > 50 else ("red" if ate_lift < -50 else "dim")
        table.add_row(
            f"[{color}]{PROMO_LABELS.get(flag, flag)}[/{color}]",
            f"[{color}]{ate_lift:+,.0f}[/{color}]",
            f"{ate.get('mean_pct_lift', 0)*100:+.1f}%" if ate.get("mean_pct_lift") is not None else "-",
            f"{att['mean_lift']:+,.0f}" if att else "-",
            f"{ate['pct_positive']*100:.0f}%",
            f"{ate['p10_lift']:+,.0f} / {ate['p90_lift']:+,.0f}",
            f"{ate['n_games']:,}",
        )
    console.print(table)
    console.print()


def compute_league_pool(level_results: list[dict]) -> list[dict]:
    """Pool per-level per-game lifts into a single league row per flag/estimand.

    level_results carries the raw (lift, actual_flag, baseline) arrays so we
    can concatenate rather than average-of-averages across levels.
    """
    by_flag = {}
    for r in level_results:
        by_flag.setdefault(r["promo_type"], []).append(r)

    rows = []
    for flag, parts in by_flag.items():
        lift = np.concatenate([p["_lift"] for p in parts])
        actual = np.concatenate([p["_actual"] for p in parts])
        baseline = np.concatenate([p["_baseline"] for p in parts])

        for estimand, mask in [
            ("ATE", np.ones_like(actual, dtype=bool)),
            ("ATT", actual == 1),
            ("ATU", actual == 0),
        ]:
            s = summarize(lift[mask], baseline[mask])
            if s:
                rows.append({
                    "promo_type": flag, "sport_id": None, "team_id": None,
                    "scope": "league", "estimand": estimand, **s,
                })
    return rows


def process_level(sport_id: int) -> tuple[list[dict], list[dict]]:
    """Returns (db_rows, raw_arrays_for_league_pool)."""
    level_name = LEVEL_NAMES[sport_id]
    console.print(f"\n[bold yellow]{level_name} (sport_id={sport_id})[/bold yellow]")

    model = load_model(sport_id)
    if model is None:
        console.print(f"  [yellow]No trained model found, skipping[/yellow]")
        return [], []

    df = load_features(sport_id)
    promo_seasons = df.loc[df["has_any_promo"].fillna(False), "season"].unique()
    if len(promo_seasons) == 0:
        console.print(f"  [yellow]No promo-era seasons for this level[/yellow]")
        return [], []

    df = df[df["season"].isin(promo_seasons)].reset_index(drop=True)
    console.print(f"  {len(df):,} games in promo-era seasons {sorted(promo_seasons)}")

    X = prepare_features(df)

    all_rows = []
    raw_for_pool = []

    for flag in PROMO_FLAGS:
        if flag not in X.columns:
            continue

        X_on = counterfactual_features(X, flag, turn_on=True)
        X_off = counterfactual_features(X, flag, turn_on=False)

        pred_on = model.predict(X_on)
        pred_off = model.predict(X_off)
        lift = pred_on - pred_off
        actual_flag = X[flag].values
        baseline = pred_off

        flag_rows = build_records_for_flag(df, lift, actual_flag, baseline, sport_id, flag)
        all_rows.extend(flag_rows)
        raw_for_pool.append({
            "promo_type": flag,
            "_lift": lift, "_actual": actual_flag, "_baseline": baseline,
        })

        # Inline ATE summary for the console
        s = summarize(lift, baseline)
        pct = s.get("mean_pct_lift")
        pct_str = f"{pct*100:+.1f}%" if pct is not None else "-"
        console.print(
            f"    {PROMO_LABELS.get(flag, flag):<14} "
            f"ATE {s['mean_lift']:+7,.0f} fans ({pct_str})  "
            f"pos {s['pct_positive']*100:3.0f}%  n={s['n_games']:,}"
        )

    return all_rows, raw_for_pool


def write_results(rows: list[dict], run_id: int, session):
    if not rows:
        return
    df = pd.DataFrame(rows)
    df["run_id"] = run_id
    # round floats for storage
    for c in ["mean_lift", "median_lift", "std_lift", "p10_lift", "p90_lift"]:
        df[c] = df[c].round(1)
    df["mean_pct_lift"] = df["mean_pct_lift"].round(4)
    df["pct_positive"] = df["pct_positive"].round(4)

    cols = ["team_id", "sport_id", "scope", "promo_type", "estimand",
            "mean_lift", "median_lift", "std_lift", "p10_lift", "p90_lift",
            "mean_pct_lift", "pct_positive", "n_games", "run_id"]
    df = df[cols]

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM milb.promo_lift_cf"))
        df.to_sql("promo_lift_cf", conn, schema="milb", if_exists="append", index=False)
    console.print(f"\n  Wrote {len(df):,} rows to milb.promo_lift_cf")


def main():
    parser = argparse.ArgumentParser(description="S-learner counterfactual promo lift")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    console.print("\n[bold blue]--- Counterfactual Promo Lift (S-learner) ---[/bold blue]\n")

    if not should_run(args.force):
        console.print("[green]Data unchanged since last run. Use --force to rebuild.[/green]")
        return

    session = get_session()
    run_id = log_run_start(session)

    try:
        start = time.time()
        all_db_rows = []
        all_raw = []

        for sid in (11, 12, 13, 14):
            rows, raw = process_level(sid)
            all_db_rows.extend(rows)
            all_raw.extend(raw)

        console.print("\n[bold yellow]Pooling league-wide...[/bold yellow]")
        league_rows = compute_league_pool(all_raw)
        all_db_rows.extend(league_rows)
        print_league_table(league_rows)

        write_results(all_db_rows, run_id, session)

        session.execute(text("""
            UPDATE milb.analysis_runs
            SET status = 'completed', completed_at = NOW(), record_count = :n
            WHERE run_id = :rid
        """), {"n": len(all_db_rows), "rid": run_id})
        session.commit()

        elapsed = time.time() - start
        console.print(f"\n[bold green]Done in {elapsed:.1f}s ({len(all_db_rows):,} rows)[/bold green]")

    except Exception as e:
        session.execute(text("""
            UPDATE milb.analysis_runs
            SET status = 'failed', completed_at = NOW(), error_message = :err
            WHERE run_id = :rid
        """), {"err": str(e), "rid": run_id})
        session.commit()
        console.print(f"[bold red]Error: {e}[/bold red]")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
