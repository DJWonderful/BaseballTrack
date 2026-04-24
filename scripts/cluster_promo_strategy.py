"""Group MiLB teams into promotional strategy clusters.

Clusters are built from promotional behavior dimensions: coverage,
stacking intensity, recurring %, diversity (entropy), flag mix,
and weekend concentration. This is orthogonal to the market-based
peer clusters in cluster_peers.py.

Usage:
    python scripts/cluster_promo_strategy.py            # normal run
    python scripts/cluster_promo_strategy.py --force     # rebuild even if data unchanged
    python scripts/cluster_promo_strategy.py --k 6       # force a specific number of clusters
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

FEATURE_COLS = [
    "promo_coverage",
    "promos_per_promo_game",
    "pct_recurring",
    "promo_entropy",
    "pct_giveaway",
    "pct_fireworks",
    "pct_food_deal",
    "pct_theme_night",
    "pct_weekend_promos",
    "pct_kids_event",
]

# Archetype rules: feature_name -> (label, description)
# When a cluster's z-score for a feature is the highest, it gets that label.
ARCHETYPE_LABELS = {
    "pct_recurring":        "Ritual Machine",
    "promo_entropy":        "Event Curator",
    "promo_coverage":       "Every-Night Carnival",
    "pct_giveaway":         "Giveaway Shop",
    "pct_food_deal":        "Value Play",
    "pct_weekend_promos":   "Weekend Warrior",
    "pct_theme_night":      "Theme Park",
    "pct_kids_event":       "Family Hub",
    "pct_fireworks":        "Pyrotechnic",
    "promos_per_promo_game": "Stack & Pack",
}


def should_run(force: bool) -> bool:
    if force:
        return True
    with engine.connect() as conn:
        last = conn.execute(text("""
            SELECT input_max_updated FROM milb.analysis_runs
            WHERE analysis_name = 'cluster_promo_strategy' AND status = 'completed'
            ORDER BY completed_at DESC LIMIT 1
        """)).fetchone()
        if last is None:
            return True
        current = conn.execute(text("""
            SELECT MAX(llm_enriched_at) FROM milb.game_promotions
        """)).fetchone()
        return current[0] is None or last[0] is None or current[0] > last[0]


def log_run_start(session) -> int:
    with engine.connect() as conn:
        current = conn.execute(text("""
            SELECT MAX(llm_enriched_at) FROM milb.game_promotions
        """)).fetchone()

    result = session.execute(text("""
        INSERT INTO milb.analysis_runs (analysis_name, input_max_updated, status)
        VALUES ('cluster_promo_strategy', :max_up, 'running')
        RETURNING run_id
    """), {"max_up": current[0]})
    session.commit()
    return result.fetchone()[0]


def load_promo_profiles() -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT * FROM milb.v_team_promo_profile
        WHERE promo_quality = 'normal'
    """), engine)


def find_optimal_k(X_scaled: np.ndarray, k_range: range) -> int:
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


def auto_label_cluster(cluster_profiles: pd.DataFrame,
                       overall_means: pd.Series,
                       overall_stds: pd.Series) -> str:
    """Label a cluster by its most distinctive dimension relative to league average."""
    cluster_means = cluster_profiles[FEATURE_COLS].mean()

    # Z-score of each dimension vs league
    z_scores = (cluster_means - overall_means) / overall_stds.replace(0, 1)

    # Check for minimalist: all dimensions below -0.3
    if (z_scores < -0.3).all():
        return "Minimalist"

    # Pick the most distinctive positive feature
    top_feature = z_scores.idxmax()
    top_z = z_scores[top_feature]

    if top_z > 0.3 and top_feature in ARCHETYPE_LABELS:
        return ARCHETYPE_LABELS[top_feature]

    # Fallback: use the highest z-score even if modest
    if top_feature in ARCHETYPE_LABELS:
        return ARCHETYPE_LABELS[top_feature]

    return "Mixed Strategy"


def main():
    parser = argparse.ArgumentParser(description="Cluster MiLB teams by promotional strategy")
    parser.add_argument("--force", action="store_true", help="Rebuild even if data unchanged")
    parser.add_argument("--k", type=int, default=0, help="Force specific number of clusters (0=auto)")
    args = parser.parse_args()

    console.print("\n[bold blue]--- Promotional Strategy Clustering ---[/bold blue]\n")

    if not should_run(args.force):
        console.print("[green]Data unchanged since last run. Use --force to rebuild.[/green]")
        return

    session = get_session()
    run_id = log_run_start(session)

    try:
        start = time.time()

        # Load profiles
        profiles = load_promo_profiles()
        console.print(f"Loaded {len(profiles)} team profiles (promo_quality='normal')")

        # Check for missing feature values
        before = len(profiles)
        profiles = profiles.dropna(subset=FEATURE_COLS)
        if len(profiles) < before:
            console.print(f"  Dropped {before - len(profiles)} teams with missing feature values")

        console.print(f"  Clustering {len(profiles)} teams on {len(FEATURE_COLS)} dimensions")

        # Prepare feature matrix
        X_raw = profiles[FEATURE_COLS].values.astype(float)

        # Scale
        scaler = StandardScaler()
        X = scaler.fit_transform(X_raw)

        # Find optimal k
        console.print("\n[bold yellow]Finding optimal cluster count...[/bold yellow]")
        if args.k > 0:
            best_k = args.k
            console.print(f"  Using user-specified k={best_k}")
        else:
            best_k = find_optimal_k(X, range(4, 10))

        # Fit final model
        km = KMeans(n_clusters=best_k, n_init=20, random_state=42)
        profiles["promo_cluster_id"] = km.fit_predict(X)

        # Compute distances from centroid
        distances = np.linalg.norm(
            X - km.cluster_centers_[profiles["promo_cluster_id"].values], axis=1
        )
        profiles["centroid_distance"] = distances.round(4)

        # Compute league-wide stats for labeling
        overall_means = profiles[FEATURE_COLS].mean()
        overall_stds = profiles[FEATURE_COLS].std()

        # Auto-label clusters
        cluster_labels = {}
        label_counts = {}
        for cid in sorted(profiles["promo_cluster_id"].unique()):
            cluster_df = profiles[profiles["promo_cluster_id"] == cid]
            base_label = auto_label_cluster(cluster_df, overall_means, overall_stds)
            if base_label in label_counts:
                label_counts[base_label] += 1
                label = f"{base_label} {label_counts[base_label]}"
            else:
                label_counts[base_label] = 1
                label = base_label
            cluster_labels[cid] = label

        profiles["promo_cluster_label"] = profiles["promo_cluster_id"].map(cluster_labels)

        # Print cluster summary
        console.print(f"\n[bold yellow]Cluster assignments ({best_k} clusters)[/bold yellow]\n")

        summary_table = Table(title="Promo Strategy Clusters")
        summary_table.add_column("Cluster", style="bold")
        summary_table.add_column("Teams", justify="right")
        summary_table.add_column("Coverage", justify="right")
        summary_table.add_column("Stack", justify="right")
        summary_table.add_column("Recurring%", justify="right")
        summary_table.add_column("Entropy", justify="right")
        summary_table.add_column("Giveaway%", justify="right")
        summary_table.add_column("Fireworks%", justify="right")
        summary_table.add_column("Weekend%", justify="right")

        for cid in sorted(profiles["promo_cluster_id"].unique()):
            cluster = profiles[profiles["promo_cluster_id"] == cid]
            label = cluster_labels[cid]
            n = len(cluster)
            m = cluster[FEATURE_COLS].mean()
            summary_table.add_row(
                label,
                str(n),
                f"{m['promo_coverage']:.1%}",
                f"{m['promos_per_promo_game']:.2f}",
                f"{m['pct_recurring']:.1%}",
                f"{m['promo_entropy']:.2f}",
                f"{m['pct_giveaway']:.1%}",
                f"{m['pct_fireworks']:.1%}",
                f"{m['pct_weekend_promos']:.1%}",
            )

        console.print(summary_table)

        # Print team assignments per cluster
        for cid in sorted(profiles["promo_cluster_id"].unique()):
            cluster = profiles[profiles["promo_cluster_id"] == cid]
            label = cluster_labels[cid]
            teams = cluster.nsmallest(min(8, len(cluster)), "centroid_distance")["team_name"].tolist()
            console.print(f"\n  [bold]{label}[/bold]: {', '.join(teams)}")
            if len(cluster) > 8:
                console.print(f"    ... and {len(cluster) - 8} more")

        # Write to DB
        console.print("\n[bold yellow]Writing cluster assignments to milb.team_promo_clusters...[/bold yellow]")
        out = profiles[["team_id", "promo_cluster_id", "promo_cluster_label", "centroid_distance"]].copy()
        out["run_id"] = run_id
        out["computed_at"] = pd.Timestamp.now()

        with engine.begin() as conn:
            conn.execute(text("TRUNCATE milb.team_promo_clusters"))
            out.to_sql("team_promo_clusters", conn, schema="milb", if_exists="append", index=False)

        console.print(f"  Wrote {len(out)} cluster assignments")

        elapsed = time.time() - start
        sil = silhouette_score(X, km.labels_)
        console.print(f"\n[bold green]Done! {best_k} clusters, {len(out)} teams in {elapsed:.1f}s[/bold green]")

        # Log success
        session.execute(text("""
            UPDATE milb.analysis_runs
            SET status = 'completed', completed_at = NOW(),
                record_count = :n, parameters = :params
            WHERE run_id = :rid
        """), {"n": len(out), "rid": run_id,
               "params": f'{{"k": {best_k}, "silhouette": {sil:.3f}}}'})
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
