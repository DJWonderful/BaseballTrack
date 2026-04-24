"""Build competitive intelligence data: weather profiles, peer similarity, momentum.

Pre-computes weather-aware peer matching and attendance momentum metrics
so the Streamlit Competitive Intel page can render instantly.

Usage:
    python scripts/build_competitive_intel.py            # normal run
    python scripts/build_competitive_intel.py --force     # rebuild even if data unchanged
    python scripts/build_competitive_intel.py --top-n 20  # peers per team (default 20)
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import linregress, zscore
from sklearn.metrics import pairwise_distances
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from rich.console import Console
from rich.table import Table
from src.db.connection import engine, get_session

console = Console()

ANALYSIS_NAME = "competitive_intel"


def should_run(force: bool) -> bool:
    if force:
        return True
    with engine.connect() as conn:
        last = conn.execute(text("""
            SELECT input_max_updated FROM milb.analysis_runs
            WHERE analysis_name = :name AND status = 'completed'
            ORDER BY completed_at DESC LIMIT 1
        """), {"name": ANALYSIS_NAME}).fetchone()
        if last is None:
            return True
        current = conn.execute(text("""
            SELECT GREATEST(
                (SELECT MAX(created_at) FROM milb.game_features),
                (SELECT MAX(updated_at) FROM milb.venues),
                (SELECT MAX(updated_at) FROM milb.venue_demographics)
            )
        """)).fetchone()
        return current[0] is None or last[0] is None or current[0] > last[0]


def log_run_start(session) -> int:
    with engine.connect() as conn:
        current = conn.execute(text("""
            SELECT GREATEST(
                (SELECT MAX(created_at) FROM milb.game_features),
                (SELECT MAX(updated_at) FROM milb.venue_demographics)
            )
        """)).fetchone()
    result = session.execute(text("""
        INSERT INTO milb.analysis_runs (analysis_name, input_max_updated, status)
        VALUES (:name, :max_up, 'running')
        RETURNING run_id
    """), {"name": ANALYSIS_NAME, "max_up": current[0]})
    session.commit()
    return result.fetchone()[0]


# -- Weather Profiles --------------------------------------------------------

def compute_weather_profiles() -> pd.DataFrame:
    console.print("\n[bold yellow]Computing weather profiles...[/bold yellow]")
    df = pd.read_sql(text("""
        SELECT team_id, season,
               AVG(temp_max_f)::numeric(5,1)  AS avg_temp_f,
               AVG(precip_inches)::numeric(6,3) AS avg_precip_in,
               AVG(wind_max_mph)::numeric(5,1) AS avg_wind_mph,
               AVG(CASE WHEN precip_inches > 0.1 THEN 1 ELSE 0 END)::numeric(4,3)
                   AS pct_rain_games,
               COUNT(*) AS total_home_games
        FROM milb.game_features
        WHERE temp_max_f IS NOT NULL AND attendance IS NOT NULL
        GROUP BY team_id, season
    """), engine)
    console.print(f"  {len(df)} team-season weather profiles")
    return df


# -- Weather-Aware Peer Similarity -------------------------------------------

def compute_peer_similarity(weather_profiles: pd.DataFrame,
                            top_n: int = 20) -> pd.DataFrame:
    console.print("\n[bold yellow]Computing weather-aware peer similarity...[/bold yellow]")

    # Use latest season weather
    latest_season = int(weather_profiles["season"].max())
    wp = weather_profiles[weather_profiles["season"] == latest_season].copy()

    # Load demographics (latest census year per venue)
    demos = pd.read_sql(text("""
        SELECT t.team_id,
               vd.msa_population, vd.msa_poverty_rate,
               v.capacity
        FROM milb.teams t
        JOIN milb.venues v ON t.venue_id = v.venue_id
        LEFT JOIN LATERAL (
            SELECT * FROM milb.venue_demographics vd2
            WHERE vd2.venue_id = v.venue_id
            ORDER BY vd2.census_year DESC LIMIT 1
        ) vd ON TRUE
        WHERE t.sport_id IN (11, 12, 13, 14)
          AND v.capacity IS NOT NULL
    """), engine)

    # Merge weather + demographics
    merged = wp.merge(demos, on="team_id", how="inner")
    required = ["avg_temp_f", "avg_precip_in", "pct_rain_games",
                "msa_population", "msa_poverty_rate", "capacity"]
    before = len(merged)
    merged = merged.dropna(subset=required)
    if len(merged) < before:
        console.print(f"  Dropped {before - len(merged)} teams with missing data")

    console.print(f"  Building similarity for {len(merged)} teams")

    # Feature matrix
    features = merged[required].copy()
    features["log_msa_pop"] = np.log1p(features["msa_population"])
    features = features.drop(columns=["msa_population"])

    scaler = StandardScaler()
    X = scaler.fit_transform(features.values)

    # Column indices after transform: avg_temp, avg_precip, pct_rain, poverty, capacity, log_pop
    # Weather = first 3, Demo = last 3
    WEATHER_IDX = [0, 1, 2]
    DEMO_IDX = [3, 4, 5]

    D_full = pairwise_distances(X)
    D_weather = pairwise_distances(X[:, WEATHER_IDX])
    D_demo = pairwise_distances(X[:, DEMO_IDX])

    team_ids = merged["team_id"].values
    rows = []
    for i in range(len(team_ids)):
        sorted_j = np.argsort(D_full[i])
        count = 0
        for j in sorted_j:
            if i == j:
                continue
            rows.append({
                "team_id": int(team_ids[i]),
                "peer_team_id": int(team_ids[j]),
                "distance": round(float(D_full[i, j]), 4),
                "similarity_score": round(1.0 / (1.0 + float(D_full[i, j])), 4),
                "weather_dist": round(float(D_weather[i, j]), 4),
                "demo_dist": round(float(D_demo[i, j]), 4),
                "season": latest_season,
            })
            count += 1
            if count >= top_n:
                break

    result = pd.DataFrame(rows)
    console.print(f"  {len(result)} peer similarity pairs (top {top_n} per team)")
    return result


# -- Momentum Metrics --------------------------------------------------------

def compute_momentum() -> pd.DataFrame:
    console.print("\n[bold yellow]Computing momentum metrics...[/bold yellow]")

    # Season-level averages
    season_stats = pd.read_sql(text("""
        SELECT team_id, season,
               AVG(attendance)::int AS avg_attendance,
               AVG(capacity_utilization) AS avg_cap_util,
               COUNT(*) AS total_games
        FROM milb.game_features
        WHERE attendance IS NOT NULL AND game_type = 'R'
        GROUP BY team_id, season
        ORDER BY team_id, season
    """), engine)

    # YoY changes
    season_stats = season_stats.sort_values(["team_id", "season"])
    g = season_stats.groupby("team_id")
    season_stats["yoy_attendance_change"] = g["avg_attendance"].diff()
    prev_att = g["avg_attendance"].shift(1)
    season_stats["yoy_attendance_pct"] = np.where(
        prev_att > 0,
        ((season_stats["avg_attendance"] - prev_att) / prev_att).round(3),
        np.nan,
    )
    season_stats["yoy_cap_util_change"] = g["avg_cap_util"].diff().round(3)

    # Within-season momentum (first half vs second half)
    halves = pd.read_sql(text("""
        SELECT team_id, season,
               AVG(CASE WHEN season_progress <= 0.5 THEN attendance END)::int
                   AS first_half_avg_att,
               AVG(CASE WHEN season_progress > 0.5 THEN attendance END)::int
                   AS second_half_avg_att
        FROM milb.game_features
        WHERE attendance IS NOT NULL AND game_type = 'R'
        GROUP BY team_id, season
    """), engine)
    season_stats = season_stats.merge(halves, on=["team_id", "season"], how="left")

    season_stats["intra_season_trend"] = np.where(
        season_stats["first_half_avg_att"] > 0,
        ((season_stats["second_half_avg_att"] - season_stats["first_half_avg_att"])
         / season_stats["first_half_avg_att"]).round(3),
        np.nan,
    )

    # Multi-season slope (linear regression of cap_util over seasons)
    slopes = {}
    for tid, grp in season_stats.groupby("team_id"):
        valid = grp.dropna(subset=["avg_cap_util"])
        if len(valid) >= 2:
            slope, _, _, _, _ = linregress(valid["season"], valid["avg_cap_util"])
            slopes[tid] = round(slope, 4)
    season_stats["multi_season_slope"] = season_stats["team_id"].map(slopes)

    # Composite momentum score (z-scored, latest season only)
    max_season = season_stats["season"].max()
    latest = season_stats[season_stats["season"] == max_season].copy()

    for col in ["yoy_cap_util_change", "intra_season_trend", "multi_season_slope"]:
        valid = latest[col].dropna()
        if len(valid) > 1:
            z = pd.Series(zscore(valid), index=valid.index)
            latest.loc[valid.index, f"{col}_z"] = z

    latest["momentum_score"] = (
        0.5 * latest.get("yoy_cap_util_change_z", pd.Series(0, index=latest.index)).fillna(0)
        + 0.3 * latest.get("intra_season_trend_z", pd.Series(0, index=latest.index)).fillna(0)
        + 0.2 * latest.get("multi_season_slope_z", pd.Series(0, index=latest.index)).fillna(0)
    ).round(3)

    def label_momentum(score):
        if pd.isna(score):
            return "stable"
        if score > 1.0:
            return "surging"
        if score > 0.3:
            return "improving"
        if score > -0.3:
            return "stable"
        if score > -1.0:
            return "declining"
        return "struggling"

    latest["momentum_label"] = latest["momentum_score"].apply(label_momentum)

    # Merge momentum score/label back into all-season df
    score_cols = latest[["team_id", "momentum_score", "momentum_label"]].copy()
    # For non-latest seasons, leave score/label null
    season_stats = season_stats.merge(score_cols, on="team_id", how="left", suffixes=("", "_latest"))
    # Only keep score/label for latest season
    season_stats.loc[season_stats["season"] != max_season, "momentum_score"] = np.nan
    season_stats.loc[season_stats["season"] != max_season, "momentum_label"] = None
    if "momentum_score_latest" in season_stats.columns:
        season_stats["momentum_score"] = season_stats["momentum_score"].fillna(
            season_stats.pop("momentum_score_latest")
        )
    if "momentum_label_latest" in season_stats.columns:
        season_stats["momentum_label"] = season_stats["momentum_label"].fillna(
            season_stats.pop("momentum_label_latest")
        )

    # Keep only columns we store
    out_cols = [
        "team_id", "season", "avg_attendance", "avg_cap_util",
        "yoy_attendance_change", "yoy_attendance_pct", "yoy_cap_util_change",
        "first_half_avg_att", "second_half_avg_att", "intra_season_trend",
        "momentum_label", "momentum_score", "multi_season_slope",
    ]
    result = season_stats[[c for c in out_cols if c in season_stats.columns]].copy()

    # Summary
    if not latest.empty:
        labels = latest["momentum_label"].value_counts()
        console.print(f"  {len(result)} team-season momentum rows")
        console.print(f"  Latest season labels: {dict(labels)}")

    return result


# -- Main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build competitive intelligence data")
    parser.add_argument("--force", action="store_true", help="Rebuild even if data unchanged")
    parser.add_argument("--top-n", type=int, default=20, help="Peers per team (default: 20)")
    args = parser.parse_args()

    console.print("\n[bold blue]--- Competitive Intelligence Builder ---[/bold blue]\n")

    if not should_run(args.force):
        console.print("[green]Data unchanged since last run. Use --force to rebuild.[/green]")
        return

    session = get_session()
    run_id = log_run_start(session)

    try:
        start = time.time()

        # 1. Weather profiles
        weather = compute_weather_profiles()
        weather["run_id"] = run_id
        weather["computed_at"] = pd.Timestamp.now()
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE milb.team_weather_profile"))
            weather.to_sql("team_weather_profile", conn, schema="milb",
                           if_exists="append", index=False)
        console.print(f"  Wrote {len(weather)} weather profiles")

        # 2. Peer similarity
        peers = compute_peer_similarity(weather, top_n=args.top_n)
        peers["run_id"] = run_id
        peers["computed_at"] = pd.Timestamp.now()
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE milb.weather_peer_similarity"))
            peers.to_sql("weather_peer_similarity", conn, schema="milb",
                         if_exists="append", index=False)
        console.print(f"  Wrote {len(peers)} peer similarity pairs")

        # 3. Momentum
        momentum = compute_momentum()
        momentum["run_id"] = run_id
        momentum["computed_at"] = pd.Timestamp.now()
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE milb.team_momentum"))
            momentum.to_sql("team_momentum", conn, schema="milb",
                            if_exists="append", index=False)
        console.print(f"  Wrote {len(momentum)} momentum rows")

        elapsed = time.time() - start
        total = len(weather) + len(peers) + len(momentum)
        console.print(f"\n[bold green]Done! {total} total rows in {elapsed:.1f}s[/bold green]")

        # Print Binghamton summary
        bing_peers = peers[peers["team_id"] == 505].head(5)
        if not bing_peers.empty:
            team_names = pd.read_sql(text(
                "SELECT team_id, team_name FROM milb.teams WHERE sport_id IN (11,12,13,14)"
            ), engine)
            bing_peers = bing_peers.merge(
                team_names, left_on="peer_team_id", right_on="team_id", suffixes=("", "_peer")
            )
            console.print("\n[bold cyan]Binghamton's top 5 weather-peers:[/bold cyan]")
            for _, p in bing_peers.iterrows():
                console.print(f"  {p['team_name']} (sim={p['similarity_score']:.3f}, "
                              f"weather_dist={p['weather_dist']:.2f}, "
                              f"demo_dist={p['demo_dist']:.2f})")

        bing_mom = momentum[(momentum["team_id"] == 505) &
                            (momentum["season"] == momentum["season"].max())]
        if not bing_mom.empty:
            bm = bing_mom.iloc[0]
            console.print(f"\n[bold cyan]Binghamton momentum:[/bold cyan] "
                          f"{bm.get('momentum_label', '?')} "
                          f"(score={bm.get('momentum_score', '?')}, "
                          f"YoY={bm.get('yoy_attendance_pct', '?')})")

        # Log success
        session.execute(text("""
            UPDATE milb.analysis_runs
            SET status = 'completed', completed_at = NOW(),
                record_count = :n, parameters = :params
            WHERE run_id = :rid
        """), {
            "n": total, "rid": run_id,
            "params": f'{{"top_n": {args.top_n}, "teams": {len(weather["team_id"].unique())}}}',
        })
        session.commit()

    except Exception as e:
        session.execute(text("""
            UPDATE milb.analysis_runs SET status = 'failed', completed_at = NOW(),
            error_message = :err WHERE run_id = :rid
        """), {"err": str(e), "rid": run_id})
        session.commit()
        console.print(f"[bold red]Error: {e}[/bold red]")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
