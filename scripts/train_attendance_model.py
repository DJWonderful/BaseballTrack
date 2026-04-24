"""Train XGBoost attendance model with Optuna hyperparameter tuning and SHAP explanations.

Trains per-level models (AAA, AA, A+, A) to predict raw attendance from ~40 game features.
Stores model artifacts, feature importance (SHAP), and per-game predictions in the DB.

Usage:
    python scripts/train_attendance_model.py              # full run
    python scripts/train_attendance_model.py --force       # rebuild even if data unchanged
    python scripts/train_attendance_model.py --trials 20   # fewer Optuna trials (faster)
    python scripts/train_attendance_model.py --level 12    # only train Double-A
"""

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
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
MODEL_DIR.mkdir(exist_ok=True)

LEVEL_NAMES = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}

# Columns that are NOT features (identifiers, targets, metadata)
EXCLUDE_COLS = {
    "game_pk", "game_date", "attendance", "capacity_utilization",
    "attendance_lift", "run_id", "created_at", "census_year",
}

# Categorical columns to encode
CAT_COLS = ["team_id", "opponent_team_id", "weather_bucket", "day_night", "game_type", "population_trend", "start_time_bucket"]


def should_run(force: bool) -> bool:
    if force:
        return True
    with engine.connect() as conn:
        last = conn.execute(text("""
            SELECT MAX(created_at) FROM milb.model_runs
        """)).fetchone()
        if last is None or last[0] is None:
            return True
        current = conn.execute(text(
            "SELECT MAX(created_at) FROM milb.game_features"
        )).fetchone()
        return current[0] is None or current[0] > last[0]


def load_features(sport_id: int) -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT * FROM milb.game_features
        WHERE sport_id = :sid
        ORDER BY game_date
    """), engine, params={"sid": sport_id})


def prepare_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Prepare feature matrix: encode categoricals, convert booleans, drop excluded cols."""
    feature_df = df.drop(columns=[c for c in EXCLUDE_COLS if c in df.columns], errors="ignore")

    # Convert booleans to int (including object-typed bool columns with None/True/False)
    bool_cols = feature_df.select_dtypes(include=["bool"]).columns
    for col in bool_cols:
        feature_df[col] = feature_df[col].astype(int)

    # Handle object columns that are actually boolean (e.g., school_in_session)
    for col in feature_df.select_dtypes(include=["object"]).columns:
        unique_vals = set(feature_df[col].dropna().unique())
        if unique_vals.issubset({True, False}):
            feature_df[col] = feature_df[col].astype(float)  # True→1.0, False→0.0, None→NaN

    # Encode categoricals
    for col in CAT_COLS:
        if col in feature_df.columns:
            feature_df[col] = feature_df[col].astype("category")

    feature_names = feature_df.columns.tolist()
    return feature_df, feature_names


def split_train_val(df: pd.DataFrame, val_frac: float = 0.2):
    """Temporal split: last val_frac of 2025 games as validation, rest as training.

    If 2025 data exists, holdout is the last 20% of 2025 by date.
    Otherwise fall back to last 20% of all data by date.
    """
    df_2025 = df[df["season"] == 2025]
    if len(df_2025) > 50:
        cutoff_idx = int(len(df_2025) * (1 - val_frac))
        # df is already sorted by game_date
        val_pks = set(df_2025.iloc[cutoff_idx:]["game_pk"])
        train = df[~df["game_pk"].isin(val_pks)]
        val = df[df["game_pk"].isin(val_pks)]
    else:
        cutoff_idx = int(len(df) * (1 - val_frac))
        train = df.iloc[:cutoff_idx]
        val = df.iloc[cutoff_idx:]

    return train, val


def train_model(X_train, y_train, X_val, y_val, feature_names, n_trials: int):
    """Train XGBoost with Optuna tuning, return best model and metrics."""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # Identify categorical feature indices for XGBoost native handling
    cat_indices = [i for i, name in enumerate(feature_names) if name in CAT_COLS]

    def objective(trial):
        params = {
            "objective": "reg:squarederror",
            "eval_metric": "mae",
            "tree_method": "hist",
            "enable_categorical": True,
            "verbosity": 0,
            "n_estimators": trial.suggest_int("n_estimators", 100, 1000),
            "max_depth": trial.suggest_int("max_depth", 3, 10),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
            "min_child_weight": trial.suggest_int("min_child_weight", 1, 20),
        }

        model = xgb.XGBRegressor(**params)
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )

        preds = model.predict(X_val)
        return mean_absolute_error(y_val, preds)

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    console.print(f"  Best trial MAE: {study.best_value:,.0f}")
    console.print(f"  Best params: {json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in study.best_params.items()}, indent=2)}")

    # Retrain with best params
    best_params = {
        "objective": "reg:squarederror",
        "tree_method": "hist",
        "enable_categorical": True,
        "verbosity": 0,
        **study.best_params,
    }
    best_model = xgb.XGBRegressor(**best_params)
    best_model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    return best_model, study.best_params


def compute_shap(model, X_val, feature_names) -> pd.DataFrame:
    """Compute SHAP values and return feature importance summary."""
    import shap
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_val)

    # Mean absolute SHAP per feature
    mean_abs_shap = np.abs(shap_values).mean(axis=0)
    importance_df = pd.DataFrame({
        "feature_name": feature_names,
        "shap_mean_abs": mean_abs_shap,
    }).sort_values("shap_mean_abs", ascending=False)
    importance_df["shap_rank"] = range(1, len(importance_df) + 1)

    # Also include XGBoost native gain importance
    gain = model.get_booster().get_score(importance_type="gain")
    importance_df["gain_importance"] = importance_df["feature_name"].map(
        lambda f: gain.get(f, 0.0)
    )

    return importance_df, shap_values


def train_for_level(sport_id: int, n_trials: int) -> dict | None:
    """Train a model for one classification level. Returns results dict or None."""
    level_name = LEVEL_NAMES[sport_id]
    console.print(f"\n[bold yellow]{'='*60}[/bold yellow]")
    console.print(f"[bold yellow]Training {level_name} (sport_id={sport_id})[/bold yellow]")
    console.print(f"[bold yellow]{'='*60}[/bold yellow]\n")

    df = load_features(sport_id)
    console.print(f"  Total games: {len(df):,}")

    if len(df) < 200:
        console.print(f"  [yellow]Skipping -- too few games ({len(df)})[/yellow]")
        return None

    # Prepare features
    X_df, feature_names = prepare_features(df)
    y = df["attendance"].values

    # Split
    train_df, val_df = split_train_val(df)
    X_train_df, _ = prepare_features(train_df)
    X_val_df, _ = prepare_features(val_df)
    y_train = train_df["attendance"].values
    y_val = val_df["attendance"].values

    console.print(f"  Train: {len(train_df):,} games ({sorted(train_df['season'].unique())})")
    console.print(f"  Val:   {len(val_df):,} games (late 2025)")

    # Train
    console.print(f"\n  [bold]Optuna tuning ({n_trials} trials)...[/bold]")
    model, best_params = train_model(X_train_df, y_train, X_val_df, y_val, feature_names, n_trials)

    # Predictions on validation set
    val_preds = model.predict(X_val_df)
    mae = mean_absolute_error(y_val, val_preds)
    rmse = np.sqrt(mean_squared_error(y_val, val_preds))
    r2 = r2_score(y_val, val_preds)
    # MAPE (avoid div by zero)
    nonzero = y_val > 0
    mape = np.mean(np.abs((y_val[nonzero] - val_preds[nonzero]) / y_val[nonzero]))

    console.print(f"\n  [bold green]Validation Metrics:[/bold green]")
    console.print(f"    MAE:  {mae:,.0f} fans")
    console.print(f"    RMSE: {rmse:,.0f} fans")
    console.print(f"    MAPE: {mape:.1%}")
    console.print(f"    R2:   {r2:.4f}")

    # SHAP
    console.print(f"\n  Computing SHAP values...")
    importance_df, shap_values = compute_shap(model, X_val_df, feature_names)

    # Print top features
    table = Table(title=f"Top 15 Features ({level_name})")
    table.add_column("Rank", justify="right")
    table.add_column("Feature", style="bold")
    table.add_column("SHAP (mean |val|)", justify="right")
    for _, row in importance_df.head(15).iterrows():
        table.add_row(str(int(row["shap_rank"])), row["feature_name"], f"{row['shap_mean_abs']:,.0f}")
    console.print(table)

    # Save model
    model_filename = f"xgb_{level_name.lower().replace('-', '')}_attendance.json"
    model_path = MODEL_DIR / model_filename
    model.save_model(str(model_path))
    console.print(f"  Model saved to {model_path}")

    # Full-dataset predictions (for residuals / anomaly detection)
    console.print(f"  Generating predictions for all {len(df):,} games...")
    X_all, _ = prepare_features(df)
    all_preds = model.predict(X_all)

    return {
        "sport_id": sport_id,
        "model": model,
        "model_path": str(model_path),
        "best_params": best_params,
        "mae": mae,
        "rmse": rmse,
        "mape": mape,
        "r2": r2,
        "n_train": len(train_df),
        "n_val": len(val_df),
        "importance_df": importance_df,
        "game_pks": df["game_pk"].values,
        "predictions": all_preds.astype(int),
        "actuals": y,
        "val_shap_values": shap_values,
        "val_feature_names": feature_names,
        "val_pks": val_df["game_pk"].values,
    }


def store_results(results: dict, session):
    """Write model run, feature importance, and predictions to DB."""
    sport_id = results["sport_id"]

    # 1. model_runs
    row = session.execute(text("""
        INSERT INTO milb.model_runs
            (sport_id, model_type, train_seasons, val_season, mae, mape, rmse,
             r_squared, n_train, n_val, model_path, parameters)
        VALUES (:sid, 'xgboost', '2023,2024,2025', 2025,
                :mae, :mape, :rmse, :r2, :nt, :nv, :path, :params)
        RETURNING run_id
    """), {
        "sid": int(sport_id),
        "mae": float(round(results["mae"], 1)),
        "mape": float(round(results["mape"], 4)),
        "rmse": float(round(results["rmse"], 1)),
        "r2": float(round(results["r2"], 4)),
        "nt": int(results["n_train"]),
        "nv": int(results["n_val"]),
        "path": results["model_path"],
        "params": json.dumps({k: round(v, 6) if isinstance(v, float) else v
                              for k, v in results["best_params"].items()}),
    })
    session.commit()
    run_id = row.fetchone()[0]

    # 2. feature_importance
    imp = results["importance_df"].copy()
    imp["run_id"] = run_id
    imp = imp[["run_id", "feature_name", "shap_mean_abs", "shap_rank", "gain_importance"]]
    with engine.begin() as conn:
        imp.to_sql("feature_importance", conn, schema="milb", if_exists="append", index=False)
    console.print(f"  Wrote {len(imp)} feature importance rows")

    # 3. game_predictions (with per-game SHAP for validation set)
    preds_df = pd.DataFrame({
        "game_pk": results["game_pks"],
        "run_id": run_id,
        "predicted_attendance": results["predictions"],
        "residual": results["actuals"].astype(int) - results["predictions"],
    })

    # Add SHAP values as JSONB for validation games only
    val_pks_set = set(results["val_pks"])
    val_shap = results["val_shap_values"]
    val_names = results["val_feature_names"]

    # Build a map: val_pk -> shap dict
    shap_map = {}
    for i, pk in enumerate(results["val_pks"]):
        top_indices = np.argsort(np.abs(val_shap[i]))[-10:][::-1]
        shap_map[int(pk)] = {val_names[j]: round(float(val_shap[i][j]), 1) for j in top_indices}

    preds_df["shap_values"] = preds_df["game_pk"].map(
        lambda pk: json.dumps(shap_map[pk]) if pk in shap_map else None
    )

    # Delete old predictions for this sport_id before inserting
    with engine.begin() as conn:
        conn.execute(text("""
            DELETE FROM milb.game_predictions
            WHERE run_id IN (SELECT run_id FROM milb.model_runs WHERE sport_id = :sid)
        """), {"sid": sport_id})
        preds_df.to_sql("game_predictions", conn, schema="milb", if_exists="append", index=False)
    console.print(f"  Wrote {len(preds_df):,} prediction rows")

    return run_id


def main():
    parser = argparse.ArgumentParser(description="Train XGBoost attendance model")
    parser.add_argument("--force", action="store_true", help="Rebuild even if data unchanged")
    parser.add_argument("--trials", type=int, default=50, help="Optuna trials per model (default 50)")
    parser.add_argument("--level", type=int, default=0, help="Train single level (11-14), 0=all")
    args = parser.parse_args()

    console.print("\n[bold blue]--- XGBoost Attendance Model Training ---[/bold blue]\n")

    if not should_run(args.force):
        console.print("[green]Data unchanged since last run. Use --force to rebuild.[/green]")
        return

    session = get_session()
    levels = [args.level] if args.level else [11, 12, 13, 14]
    all_run_ids = []

    try:
        start = time.time()

        for sid in levels:
            result = train_for_level(sid, args.trials)
            if result is None:
                continue
            run_id = store_results(result, session)
            all_run_ids.append(run_id)

        elapsed = time.time() - start
        console.print(f"\n[bold green]All done! {len(all_run_ids)} models trained in {elapsed:.0f}s[/bold green]")

        # Print summary
        summary = pd.read_sql(text("""
            SELECT sport_id, mae, mape, r_squared, n_train, n_val, model_path
            FROM milb.model_runs
            WHERE run_id = ANY(:ids)
            ORDER BY sport_id
        """), engine, params={"ids": all_run_ids})

        if not summary.empty:
            table = Table(title="Model Summary")
            table.add_column("Level")
            table.add_column("MAE", justify="right")
            table.add_column("MAPE", justify="right")
            table.add_column("R2", justify="right")
            table.add_column("Train", justify="right")
            table.add_column("Val", justify="right")
            for _, row in summary.iterrows():
                table.add_row(
                    LEVEL_NAMES.get(int(row["sport_id"]), "?"),
                    f"{row['mae']:,.0f}",
                    f"{row['mape']:.1%}",
                    f"{row['r_squared']:.4f}",
                    f"{row['n_train']:,}",
                    f"{row['n_val']:,}",
                )
            console.print(table)

    except Exception as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
