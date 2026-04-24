"""Group MiLB teams into peer clusters based on market similarity.

Clusters are built from demographics (MSA population, income, poverty rate),
venue capacity, and classification level. This lets recommendations be
contextualized -- Binghamton compares to Erie and Akron, not Frisco.

Usage:
    python scripts/cluster_peers.py            # normal run
    python scripts/cluster_peers.py --force     # rebuild even if data unchanged
    python scripts/cluster_peers.py --k 10      # force a specific number of clusters
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
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

# Metrics to compute as cluster benchmarks (from game_features)
BENCHMARK_METRICS = [
    ("avg_attendance", "AVG(attendance)"),
    ("capacity_utilization", "AVG(capacity_utilization)"),
    ("pct_fireworks", "AVG(has_fireworks::int)"),
    ("pct_giveaway", "AVG(has_giveaway::int)"),
    ("pct_theme_night", "AVG(has_theme_night::int)"),
    ("pct_food_deal", "AVG(has_food_deal::int)"),
    ("pct_kids_event", "AVG(has_kids_event::int)"),
    ("pct_community", "AVG(has_community::int)"),
    ("promo_rate", "AVG(has_any_promo::int)"),
    ("avg_promo_count", "AVG(promo_count)"),
    ("games_per_season", "COUNT(*)::float / COUNT(DISTINCT season)"),
]


def should_run(force: bool) -> bool:
    if force:
        return True
    with engine.connect() as conn:
        last = conn.execute(text("""
            SELECT input_max_updated FROM milb.analysis_runs
            WHERE analysis_name = 'cluster_peers' AND status = 'completed'
            ORDER BY completed_at DESC LIMIT 1
        """)).fetchone()
        if last is None:
            return True
        current = conn.execute(text("""
            SELECT GREATEST(
                (SELECT MAX(updated_at) FROM milb.teams),
                (SELECT MAX(updated_at) FROM milb.venues),
                (SELECT MAX(updated_at) FROM milb.venue_demographics),
                (SELECT MAX(created_at) FROM milb.game_features)
            )
        """)).fetchone()
        return current[0] is None or last[0] is None or current[0] > last[0]


def log_run_start(session) -> int:
    with engine.connect() as conn:
        current = conn.execute(text("""
            SELECT GREATEST(
                (SELECT MAX(updated_at) FROM milb.teams),
                (SELECT MAX(updated_at) FROM milb.venue_demographics)
            )
        """)).fetchone()
    result = session.execute(text("""
        INSERT INTO milb.analysis_runs (analysis_name, input_max_updated, status)
        VALUES ('cluster_peers', :max_up, 'running')
        RETURNING run_id
    """), {"max_up": current[0]})
    session.commit()
    return result.fetchone()[0]


def load_team_profiles() -> pd.DataFrame:
    """Load one row per team with clustering features."""
    return pd.read_sql(text("""
        SELECT t.team_id, t.team_name, t.sport_id,
               s.sport_name,
               v.capacity,
               vd.msa_population, vd.place_population,
               vd.msa_median_income, vd.msa_poverty_rate
        FROM milb.teams t
        JOIN milb.sports s ON t.sport_id = s.sport_id
        LEFT JOIN milb.venues v ON t.venue_id = v.venue_id
        LEFT JOIN LATERAL (
            SELECT * FROM milb.venue_demographics vd2
            WHERE vd2.venue_id = v.venue_id
            ORDER BY vd2.census_year DESC LIMIT 1
        ) vd ON TRUE
        WHERE t.sport_id IN (11, 12, 13, 14)
    """), engine)


def load_team_performance() -> pd.DataFrame:
    """Load avg attendance per team from game_features (latest season)."""
    return pd.read_sql(text("""
        SELECT team_id,
               AVG(attendance)::int AS avg_attendance,
               AVG(capacity_utilization) AS avg_cap_util
        FROM milb.game_features
        WHERE season = (SELECT MAX(season) FROM milb.game_features)
        GROUP BY team_id
    """), engine)


def find_optimal_k(X_scaled: np.ndarray, k_range: range) -> int:
    """Find optimal k via silhouette score."""
    best_k, best_score = k_range.start, -1
    scores = {}
    for k in k_range:
        km = KMeans(n_clusters=k, n_init=10, random_state=42)
        labels = km.fit_predict(X_scaled)
        score = silhouette_score(X_scaled, labels)
        scores[k] = score
        if score > best_score:
            best_score = score
            best_k = k
    console.print(f"  Silhouette scores: {', '.join(f'k={k}:{s:.3f}' for k, s in scores.items())}")
    console.print(f"  Best k={best_k} (silhouette={best_score:.3f})")
    return best_k


def auto_label_cluster(cluster_df: pd.DataFrame) -> str:
    """Generate a human-readable label for a cluster based on its characteristics."""
    n = len(cluster_df)
    avg_pop = cluster_df["msa_population"].median()
    avg_income = cluster_df["msa_median_income"].median()
    sport_mode = cluster_df["sport_name"].mode().iloc[0] if not cluster_df.empty else "?"

    # Market size
    if avg_pop > 1_500_000:
        size = "Large Market"
    elif avg_pop > 500_000:
        size = "Mid Market"
    elif avg_pop > 200_000:
        size = "Small Market"
    else:
        size = "Micro Market"

    # Level
    level = sport_mode.replace("-", "")

    return f"{size} {level}"


def compute_benchmarks(run_id: int):
    """Compute cluster-level benchmark averages from game_features."""
    console.print("\n[bold yellow]Computing cluster benchmarks...[/bold yellow]")

    # Build SELECT clauses for each metric
    metric_selects = ", ".join(
        f"{agg}::numeric(10,4) AS {name}" for name, agg in BENCHMARK_METRICS
    )

    benchmarks = pd.read_sql(text(f"""
        SELECT tc.cluster_id, tc.cluster_label,
               COUNT(DISTINCT gf.team_id) AS n_teams,
               {metric_selects}
        FROM milb.game_features gf
        JOIN milb.team_clusters tc ON gf.team_id = tc.team_id
        GROUP BY tc.cluster_id, tc.cluster_label
        ORDER BY tc.cluster_id
    """), engine)

    if benchmarks.empty:
        return

    # Melt into long format for storage
    rows = []
    for _, cluster_row in benchmarks.iterrows():
        cid = int(cluster_row["cluster_id"])
        n_teams = int(cluster_row["n_teams"])
        for metric_name, _ in BENCHMARK_METRICS:
            val = cluster_row.get(metric_name)
            if pd.notna(val):
                rows.append({
                    "cluster_id": cid,
                    "metric_name": metric_name,
                    "metric_value": round(float(val), 4),
                    "n_teams": n_teams,
                    "run_id": run_id,
                })

    if rows:
        bm_df = pd.DataFrame(rows)
        bm_df["computed_at"] = pd.Timestamp.now()
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM milb.cluster_benchmarks"))
            bm_df.to_sql("cluster_benchmarks", conn, schema="milb", if_exists="append", index=False)
        console.print(f"  Wrote {len(rows)} benchmark rows")

    # Print summary table
    table = Table(title="Cluster Benchmarks")
    table.add_column("Cluster", style="bold")
    table.add_column("Teams", justify="right")
    table.add_column("Avg Att", justify="right")
    table.add_column("Cap Util", justify="right")
    table.add_column("Promo Rate", justify="right")
    table.add_column("FW Rate", justify="right")

    for _, row in benchmarks.iterrows():
        table.add_row(
            str(row["cluster_label"]),
            str(int(row["n_teams"])),
            f"{row['avg_attendance']:,.0f}",
            f"{row['capacity_utilization']:.1%}" if pd.notna(row["capacity_utilization"]) else "-",
            f"{row['promo_rate']:.1%}" if pd.notna(row["promo_rate"]) else "-",
            f"{row['pct_fireworks']:.1%}" if pd.notna(row["pct_fireworks"]) else "-",
        )
    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Cluster MiLB teams into peer groups")
    parser.add_argument("--force", action="store_true", help="Rebuild even if data unchanged")
    parser.add_argument("--k", type=int, default=0, help="Force specific number of clusters (0=auto)")
    args = parser.parse_args()

    console.print("\n[bold blue]--- Peer Clustering ---[/bold blue]\n")

    if not should_run(args.force):
        console.print("[green]Data unchanged since last run. Use --force to rebuild.[/green]")
        return

    session = get_session()
    run_id = log_run_start(session)

    try:
        start = time.time()

        # Load team profiles
        profiles = load_team_profiles()
        console.print(f"Loaded {len(profiles)} team profiles")

        perf = load_team_performance()
        profiles = profiles.merge(perf, on="team_id", how="left")

        # Drop teams missing critical clustering features
        required = ["msa_population", "msa_median_income", "msa_poverty_rate", "capacity"]
        before = len(profiles)
        profiles = profiles.dropna(subset=required)
        if len(profiles) < before:
            console.print(f"  Dropped {before - len(profiles)} teams with missing demographics/capacity")

        console.print(f"  Clustering {len(profiles)} teams")

        # Prepare features
        features = profiles[["msa_population", "msa_median_income", "msa_poverty_rate",
                             "capacity", "sport_id"]].copy()

        # Log-transform population (heavy-tailed distribution)
        features["log_msa_pop"] = np.log1p(features["msa_population"])
        features = features.drop(columns=["msa_population"])

        # Scale
        scaler = StandardScaler()
        X = scaler.fit_transform(features.values)

        # Find optimal k
        console.print("\n[bold yellow]Finding optimal cluster count...[/bold yellow]")
        if args.k > 0:
            best_k = args.k
            console.print(f"  Using user-specified k={best_k}")
        else:
            best_k = find_optimal_k(X, range(6, 16))

        # Fit final model
        km = KMeans(n_clusters=best_k, n_init=20, random_state=42)
        profiles["cluster_id"] = km.fit_predict(X)

        # Compute distances from centroid
        distances = np.linalg.norm(X - km.cluster_centers_[profiles["cluster_id"].values], axis=1)
        profiles["centroid_distance"] = distances.round(4)

        # Auto-label clusters
        cluster_labels = {}
        label_counts = {}
        for cid in sorted(profiles["cluster_id"].unique()):
            cluster_df = profiles[profiles["cluster_id"] == cid]
            base_label = auto_label_cluster(cluster_df)
            # Disambiguate duplicate labels by appending a number
            if base_label in label_counts:
                label_counts[base_label] += 1
                label = f"{base_label} {label_counts[base_label]}"
            else:
                label_counts[base_label] = 1
                label = base_label
            cluster_labels[cid] = label

        profiles["cluster_label"] = profiles["cluster_id"].map(cluster_labels)

        # Print cluster summary
        console.print(f"\n[bold yellow]Cluster assignments ({best_k} clusters)[/bold yellow]\n")
        for cid in sorted(profiles["cluster_id"].unique()):
            cluster = profiles[profiles["cluster_id"] == cid]
            label = cluster_labels[cid]
            n = len(cluster)
            avg_pop = cluster["msa_population"].median()
            avg_income = cluster["msa_median_income"].median()
            avg_cap = cluster["capacity"].median()
            avg_att = cluster["avg_attendance"].median() if "avg_attendance" in cluster else 0
            sports = cluster["sport_name"].value_counts().to_dict()
            sport_str = ", ".join(f"{v}x {k}" for k, v in sports.items())

            console.print(f"  [bold]{label}[/bold] ({n} teams)")
            console.print(f"    MSA pop: {avg_pop:,.0f} | Income: ${avg_income:,.0f} | "
                          f"Cap: {avg_cap:,.0f} | Avg Att: {avg_att:,.0f}")
            console.print(f"    Levels: {sport_str}")

            # Show example teams
            examples = cluster.nsmallest(3, "centroid_distance")["team_name"].tolist()
            console.print(f"    Core members: {', '.join(examples)}")
            console.print()

        # Show where Binghamton landed
        bing = profiles[profiles["team_id"] == 505]
        if not bing.empty:
            bing_cluster = bing.iloc[0]
            peers = profiles[profiles["cluster_id"] == bing_cluster["cluster_id"]]
            console.print(f"[bold cyan]Binghamton Rumble Ponies -> {bing_cluster['cluster_label']}[/bold cyan]")
            console.print(f"  Peers: {', '.join(peers['team_name'].tolist())}")
            console.print()

        # Write to DB
        console.print("[bold yellow]Writing cluster assignments to milb.team_clusters...[/bold yellow]")
        out = profiles[["team_id", "cluster_id", "cluster_label", "centroid_distance"]].copy()
        out["run_id"] = run_id
        out["computed_at"] = pd.Timestamp.now()

        with engine.begin() as conn:
            conn.execute(text("TRUNCATE milb.team_clusters"))
            out.to_sql("team_clusters", conn, schema="milb", if_exists="append", index=False)

        console.print(f"  Wrote {len(out)} cluster assignments")

        # Compute and store benchmarks
        compute_benchmarks(run_id)

        elapsed = time.time() - start
        console.print(f"\n[bold green]Done! {best_k} clusters, {len(out)} teams in {elapsed:.1f}s[/bold green]")

        # Log success
        session.execute(text("""
            UPDATE milb.analysis_runs
            SET status = 'completed', completed_at = NOW(),
                record_count = :n, parameters = :params
            WHERE run_id = :rid
        """), {"n": len(out), "rid": run_id,
               "params": f'{{"k": {best_k}, "silhouette": {silhouette_score(X, km.labels_):.3f}}}'})
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
