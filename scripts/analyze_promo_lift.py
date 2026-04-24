"""Compute marginal attendance lift per promotion type using controlled OLS regression.

Answers: "Fireworks add +823 fans (95% CI: 612-1034, p<0.001)" with statistical rigor.
Controls for day-of-week, month, weather, homestand position, school calendar.

Usage:
    python scripts/analyze_promo_lift.py            # normal run
    python scripts/analyze_promo_lift.py --force     # rebuild even if data unchanged
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
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

CONTROL_COLS = [
    "day_of_week", "month", "is_weekend", "homestand_game_number",
    "school_in_session", "temp_max_f", "precip_inches", "wind_max_mph",
]

PROMO_LABELS = {
    "has_fireworks": "Fireworks",
    "has_giveaway": "Giveaway",
    "has_food_deal": "Food Deal",
    "has_ticket_deal": "Ticket Deal",
    "has_theme_night": "Theme Night",
    "has_kids_event": "Kids Event",
    "has_heritage": "Heritage Night",
    "has_community": "Community",
    "has_entertain": "Entertainment",
    "has_dog": "Dog Friendly",
    "has_celebrity": "Celebrity",
    "has_recurring": "Recurring",
}


def should_run(force: bool) -> bool:
    if force:
        return True
    with engine.connect() as conn:
        last = conn.execute(text("""
            SELECT input_max_updated FROM milb.analysis_runs
            WHERE analysis_name = 'promo_lift' AND status = 'completed'
            ORDER BY completed_at DESC LIMIT 1
        """)).fetchone()
        if last is None:
            return True
        current = conn.execute(text("""
            SELECT MAX(created_at) FROM milb.game_features
        """)).fetchone()
        return current[0] is None or last[0] is None or current[0] > last[0]


def log_run_start(session) -> int:
    with engine.connect() as conn:
        current = conn.execute(text(
            "SELECT MAX(created_at) FROM milb.game_features"
        )).fetchone()
    result = session.execute(text("""
        INSERT INTO milb.analysis_runs (analysis_name, input_max_updated, status)
        VALUES ('promo_lift', :max_up, 'running')
        RETURNING run_id
    """), {"max_up": current[0]})
    session.commit()
    return result.fetchone()[0]


def load_features() -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT game_pk, team_id, season, sport_id,
               attendance, attendance_lift, capacity_utilization, venue_capacity,
               day_of_week, month, is_weekend, homestand_game_number,
               school_in_session, temp_max_f, precip_inches, wind_max_mph,
               promo_count, has_any_promo,
               has_fireworks, has_giveaway, has_food_deal, has_ticket_deal,
               has_theme_night, has_kids_event, has_heritage, has_community,
               has_entertain, has_dog, has_celebrity, has_recurring,
               has_limited_giveaway, days_since_last_fw, days_since_last_give
        FROM milb.game_features
    """), engine)


def prepare_regression_data(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare data for OLS: encode categoricals, fill NAs, filter to promo-era games."""
    # Only use games from seasons with promo data
    seasons_with_promos = df[df["has_any_promo"]]["season"].unique()
    df = df[df["season"].isin(seasons_with_promos)].copy()

    if df.empty:
        return df

    # One-hot encode day_of_week and month (drop first to avoid multicollinearity)
    dow_dummies = pd.get_dummies(df["day_of_week"], prefix="dow", drop_first=True, dtype=int)
    month_dummies = pd.get_dummies(df["month"], prefix="month", drop_first=True, dtype=int)
    df = pd.concat([df, dow_dummies, month_dummies], axis=1)

    # Fill NAs in controls
    df["school_in_session"] = df["school_in_session"].fillna(True).astype(int)
    df["is_weekend"] = df["is_weekend"].astype(int)
    df["temp_max_f"] = df["temp_max_f"].fillna(df["temp_max_f"].median())
    df["precip_inches"] = df["precip_inches"].fillna(0)
    df["wind_max_mph"] = df["wind_max_mph"].fillna(df["wind_max_mph"].median())
    df["homestand_game_number"] = df["homestand_game_number"].fillna(1).clip(upper=7)

    # Convert promo flags to int
    for flag in PROMO_FLAGS:
        df[flag] = df[flag].fillna(False).astype(int)

    return df


def run_ols(y: np.ndarray, X: np.ndarray, feature_names: list[str]) -> pd.DataFrame:
    """Run OLS regression and return coefficient estimates with CIs and p-values."""
    # Add intercept
    n, k = X.shape
    X_int = np.column_stack([np.ones(n), X])

    try:
        beta = np.linalg.lstsq(X_int, y, rcond=None)[0]
        y_hat = X_int @ beta
        residuals = y - y_hat
        dof = n - k - 1

        if dof <= 0:
            return pd.DataFrame()

        mse = np.sum(residuals ** 2) / dof
        # Variance-covariance matrix
        XtX_inv = np.linalg.inv(X_int.T @ X_int)
        se = np.sqrt(np.diag(XtX_inv) * mse)

        t_stats = beta / se
        p_values = 2 * stats.t.sf(np.abs(t_stats), dof)

        # 95% CI
        t_crit = stats.t.ppf(0.975, dof)
        ci_lower = beta - t_crit * se
        ci_upper = beta + t_crit * se

        names = ["intercept"] + feature_names
        results = pd.DataFrame({
            "feature": names,
            "coefficient": beta,
            "std_error": se,
            "t_stat": t_stats,
            "p_value": p_values,
            "ci_lower": ci_lower,
            "ci_upper": ci_upper,
        })
        return results

    except np.linalg.LinAlgError:
        console.print("[yellow]  Warning: singular matrix, skipping this regression[/yellow]")
        return pd.DataFrame()


def analyze_scope(df: pd.DataFrame, scope: str, team_id=None, sport_id=None, season=None) -> list[dict]:
    """Run regression for a given scope and return lift estimates."""
    results = []

    # Build feature matrix
    control_feature_names = []
    control_cols_data = []

    # Day-of-week dummies
    dow_cols = [c for c in df.columns if c.startswith("dow_")]
    for c in dow_cols:
        control_feature_names.append(c)
        control_cols_data.append(df[c].values)

    # Month dummies
    month_cols = [c for c in df.columns if c.startswith("month_")]
    for c in month_cols:
        control_feature_names.append(c)
        control_cols_data.append(df[c].values)

    # Numeric controls
    for c in ["is_weekend", "homestand_game_number", "school_in_session",
              "temp_max_f", "precip_inches", "wind_max_mph"]:
        if c in df.columns:
            control_feature_names.append(c)
            control_cols_data.append(df[c].values)

    # Promo flags
    promo_feature_names = []
    promo_cols_data = []
    for flag in PROMO_FLAGS:
        if df[flag].sum() >= 5:  # Need at least 5 games with this promo
            promo_feature_names.append(flag)
            promo_cols_data.append(df[flag].values)

    if not promo_feature_names:
        return results

    all_names = promo_feature_names + control_feature_names
    X = np.column_stack(promo_cols_data + control_cols_data)
    y = df["attendance_lift"].values

    # Remove rows with NaN
    mask = ~np.isnan(y) & ~np.any(np.isnan(X), axis=1)
    X, y = X[mask], y[mask]

    if len(y) < len(all_names) + 10:
        return results

    ols_results = run_ols(y, X, all_names)
    if ols_results.empty:
        return results

    # Extract promo flag results
    for flag in promo_feature_names:
        row = ols_results[ols_results["feature"] == flag]
        if row.empty:
            continue
        row = row.iloc[0]

        n_with = int(df[flag].sum())
        n_without = int(len(df) - n_with)

        results.append({
            "team_id": team_id,
            "sport_id": sport_id,
            "season": season,
            "scope": scope,
            "promo_type": flag,
            "marginal_lift": round(float(row["coefficient"]), 1),
            "ci_lower": round(float(row["ci_lower"]), 1),
            "ci_upper": round(float(row["ci_upper"]), 1),
            "p_value": round(float(row["p_value"]), 6),
            "n_games_with": n_with,
            "n_games_without": n_without,
        })

    return results


def print_results_table(results: list[dict], title: str):
    """Pretty-print lift results to console."""
    if not results:
        return

    table = Table(title=title, show_lines=False)
    table.add_column("Promo Type", style="bold")
    table.add_column("Lift", justify="right")
    table.add_column("95% CI", justify="right")
    table.add_column("p-value", justify="right")
    table.add_column("Games", justify="right")

    sorted_results = sorted(results, key=lambda r: r["marginal_lift"], reverse=True)
    for r in sorted_results:
        lift_str = f"{r['marginal_lift']:+.0f}"
        ci_str = f"[{r['ci_lower']:+.0f}, {r['ci_upper']:+.0f}]"
        p_str = f"{r['p_value']:.4f}" if r["p_value"] >= 0.0001 else "<0.0001"
        sig = "*" if r["p_value"] < 0.05 else ""

        color = "green" if r["marginal_lift"] > 0 and r["p_value"] < 0.05 else (
            "red" if r["marginal_lift"] < 0 and r["p_value"] < 0.05 else "dim"
        )
        label = PROMO_LABELS.get(r["promo_type"], r["promo_type"])

        table.add_row(
            f"[{color}]{label}{sig}[/{color}]",
            f"[{color}]{lift_str}[/{color}]",
            ci_str,
            p_str,
            str(r["n_games_with"]),
        )

    console.print(table)
    console.print()


def main():
    parser = argparse.ArgumentParser(description="Compute marginal promo lift via OLS")
    parser.add_argument("--force", action="store_true", help="Rebuild even if data unchanged")
    args = parser.parse_args()

    console.print("\n[bold blue]--- Marginal Promotion Lift Analysis ---[/bold blue]\n")

    if not should_run(args.force):
        console.print("[green]Data unchanged since last run. Use --force to rebuild.[/green]")
        return

    session = get_session()
    run_id = log_run_start(session)

    try:
        start = time.time()
        raw = load_features()
        console.print(f"Loaded {len(raw):,} games from game_features")

        df = prepare_regression_data(raw)
        console.print(f"After filtering to promo-era seasons: {len(df):,} games")

        if df.empty:
            console.print("[yellow]No promo data available yet. Run enrich_promotions.py first.[/yellow]")
            session.execute(text("""
                UPDATE milb.analysis_runs SET status = 'completed', completed_at = NOW(),
                record_count = 0 WHERE run_id = :rid
            """), {"rid": run_id})
            session.commit()
            return

        all_results = []

        # 1. League-wide per level
        console.print("\n[bold yellow]1. League-wide analysis per level[/bold yellow]")
        for sid, level_name in [(11, "Triple-A"), (12, "Double-A"), (13, "High-A"), (14, "Single-A")]:
            subset = df[df["sport_id"] == sid]
            if len(subset) < 100:
                continue
            results = analyze_scope(subset, "league_level", sport_id=sid)
            all_results.extend(results)
            print_results_table(results, f"{level_name} (n={len(subset):,})")

        # 2. Per-team all-seasons (for teams with enough promo games)
        console.print("[bold yellow]2. Per-team analysis (all seasons pooled)[/bold yellow]")
        team_counts = df.groupby("team_id")["has_any_promo"].sum()
        teams_with_promos = team_counts[team_counts >= 20].index

        for tid in teams_with_promos:
            subset = df[df["team_id"] == tid]
            sid = subset["sport_id"].mode().iloc[0] if not subset.empty else None
            results = analyze_scope(subset, "team_all", team_id=int(tid), sport_id=int(sid) if sid else None)
            all_results.extend(results)

            # Only print for teams with significant results
            sig_results = [r for r in results if r["p_value"] < 0.1]
            if sig_results:
                team_name_lookup = raw[raw["team_id"] == tid]
                label = f"Team {tid}"
                print_results_table(sig_results, f"{label} (n={len(subset):,})")

        console.print(f"\nTotal lift estimates: {len(all_results):,}")

        # Write to DB
        if all_results:
            console.print(f"\n[bold yellow]Writing {len(all_results):,} lift estimates to milb.promo_lift...[/bold yellow]")
            lift_df = pd.DataFrame(all_results)
            lift_df["run_id"] = run_id
            lift_df["computed_at"] = pd.Timestamp.now()

            with engine.begin() as conn:
                # Clear old results for this run's scopes
                conn.execute(text("DELETE FROM milb.promo_lift WHERE run_id != :rid"), {"rid": run_id})
                lift_df.to_sql("promo_lift", conn, schema="milb", if_exists="append", index=False)

        elapsed = time.time() - start
        console.print(f"\n[bold green]Done! {len(all_results)} estimates in {elapsed:.1f}s[/bold green]")

        # Summary
        sig_count = sum(1 for r in all_results if r["p_value"] < 0.05)
        console.print(f"  Statistically significant (p<0.05): {sig_count}/{len(all_results)}")

        # Log success
        session.execute(text("""
            UPDATE milb.analysis_runs
            SET status = 'completed', completed_at = NOW(), record_count = :n
            WHERE run_id = :rid
        """), {"n": len(all_results), "rid": run_id})
        session.commit()

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
