"""Phase 1 checkpoint for the Game Times analysis.

Console output only, no DB writes. Goals:
  1. Sat start-time bucket distribution per weekend_gap camp, per level.
     Does the user's camp-level hypothesis (winners run later) hold at bucket
     granularity, or is it still flat like it was at day/night?
  2. RP's Sat clock vs Double-A sat_winner Sat clock. How outlier is RP?
  3. Within-team attendance lift per bucket. Same team, same DOW, different
     buckets -- what does a matinee cost vs an evening?
  4. day_night vs start_time_bucket disagreement rate (data-quality note).

Usage: python scripts/analyze_game_times.py
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

from src.db.connection import engine

console = Console()

LEVEL_NAMES = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}
BUCKET_ORDER = ["morning", "noon", "matinee", "early_evening", "evening", "late"]
DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
RUMBLE_PONIES_ID = 505
ANALYSIS_SEASON = 2025


def load_games(season: int) -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT f.game_pk, f.team_id, f.season, f.sport_id, f.game_date,
               f.day_of_week, f.day_night, f.local_start_hour, f.start_time_bucket,
               f.attendance, f.capacity_utilization, f.venue_capacity,
               t.team_name,
               wg.gap_camp
          FROM milb.game_features f
          JOIN milb.teams t ON t.team_id = f.team_id
          LEFT JOIN milb.weekend_gap wg ON wg.team_id = f.team_id AND wg.season = f.season
         WHERE f.season = :season
           AND f.game_type = 'R'
           AND f.attendance IS NOT NULL
    """), engine, params={"season": season})


def bucket_mix_per_camp(df: pd.DataFrame, dow: int, dow_label: str):
    sub = df[(df["day_of_week"] == dow) & df["start_time_bucket"].notna()].copy()
    if sub.empty:
        return

    for sid in (11, 12, 13, 14):
        level_df = sub[sub["sport_id"] == sid]
        if level_df.empty:
            continue
        xtab = pd.crosstab(level_df["gap_camp"], level_df["start_time_bucket"],
                           normalize="index").reindex(
            index=["sat_winner", "neutral", "sat_loser"],
            columns=BUCKET_ORDER, fill_value=0,
        )
        counts = level_df["gap_camp"].value_counts().to_dict()
        t = Table(title=f"{dow_label} bucket % per camp -- {LEVEL_NAMES[sid]}")
        t.add_column("Camp"); t.add_column("n", justify="right")
        for b in BUCKET_ORDER:
            t.add_column(b, justify="right")
        for camp in ["sat_winner", "neutral", "sat_loser"]:
            if camp not in xtab.index:
                continue
            row = [camp, str(counts.get(camp, 0))]
            for b in BUCKET_ORDER:
                val = xtab.loc[camp, b] * 100
                row.append(f"{val:.0f}%")
            t.add_row(*row)
        console.print(t)
    console.print()


def rp_clock(df: pd.DataFrame):
    rp = df[df["team_id"] == RUMBLE_PONIES_ID]
    if rp.empty:
        console.print("[yellow]No RP data.[/yellow]"); return

    console.print("[bold]Binghamton Rumble Ponies start clock by DOW[/bold]")
    t = Table()
    t.add_column("DOW"); t.add_column("n", justify="right")
    for b in BUCKET_ORDER:
        t.add_column(b, justify="right")
    t.add_column("Avg att", justify="right"); t.add_column("Cap util", justify="right")

    for dow in range(7):
        sub = rp[rp["day_of_week"] == dow]
        if sub.empty:
            continue
        counts = sub["start_time_bucket"].value_counts(normalize=True).to_dict()
        row = [DOW_NAMES[dow], str(len(sub))]
        for b in BUCKET_ORDER:
            pct = counts.get(b, 0) * 100
            row.append(f"{pct:.0f}%" if pct > 0 else "-")
        row.append(f"{sub['attendance'].mean():,.0f}")
        row.append(f"{sub['capacity_utilization'].mean()*100:.0f}%")
        t.add_row(*row)
    console.print(t)
    console.print()


def within_team_bucket_lift(df: pd.DataFrame, level_id: int | None = None, dow_filter: list[int] | None = None):
    """For each team, compute avg cap_util per bucket. Then compute bucket mean
    within that team and subtract team-mean to get relative lift. Aggregate across
    teams to get a "within-team" bucket effect."""
    sub = df[df["start_time_bucket"].notna() & df["capacity_utilization"].notna()].copy()
    if level_id is not None:
        sub = sub[sub["sport_id"] == level_id]
    if dow_filter is not None:
        sub = sub[sub["day_of_week"].isin(dow_filter)]
    if sub.empty:
        return

    team_means = sub.groupby("team_id")["capacity_utilization"].mean()
    sub["cap_util_rel"] = sub["capacity_utilization"] - sub["team_id"].map(team_means)

    agg = (sub.groupby("start_time_bucket")
              .agg(mean_rel=("cap_util_rel", "mean"),
                   mean_abs=("capacity_utilization", "mean"),
                   n_games=("game_pk", "count"),
                   n_teams=("team_id", "nunique"))
              .reindex(BUCKET_ORDER).dropna(how="all"))

    label = "ALL" if level_id is None else LEVEL_NAMES[level_id]
    dow_str = "all DOWs" if dow_filter is None else ",".join(DOW_NAMES[d] for d in dow_filter)

    t = Table(title=f"Within-team cap-util lift by bucket -- {label}, {dow_str}")
    t.add_column("Bucket")
    t.add_column("Rel vs team avg", justify="right")
    t.add_column("Abs cap util", justify="right")
    t.add_column("Games", justify="right")
    t.add_column("Teams", justify="right")
    for bucket, row in agg.iterrows():
        rel = row["mean_rel"] * 100
        color = "green" if rel > 1 else ("red" if rel < -1 else "dim")
        t.add_row(
            bucket,
            f"[{color}]{rel:+.1f}pp[/{color}]",
            f"{row['mean_abs']*100:.1f}%",
            f"{int(row['n_games']):,}",
            f"{int(row['n_teams']):,}",
        )
    console.print(t)
    console.print()


def day_night_disagreement(df: pd.DataFrame):
    # Define "bucket-night" as evening/late, "bucket-day" as morning/noon/matinee
    def bucket_class(b):
        if b in ("evening", "late"):
            return "night"
        if b in ("morning", "noon", "matinee"):
            return "day"
        return None  # early_evening straddles; treat as ambiguous

    sub = df[df["start_time_bucket"].notna() & df["day_night"].notna()].copy()
    sub["bucket_class"] = sub["start_time_bucket"].apply(bucket_class)
    sub = sub[sub["bucket_class"].notna()]
    mismatches = (sub["day_night"] != sub["bucket_class"]).sum()
    total = len(sub)
    console.print(f"[bold]day_night vs bucket disagreement:[/bold] {mismatches:,} / {total:,} "
                  f"({mismatches/total*100:.1f}%)  "
                  f"(excludes early_evening which straddles the boundary)")
    xtab = pd.crosstab(sub["day_night"], sub["bucket_class"], margins=True)
    console.print(xtab.to_string())
    console.print()


def rp_vs_double_a_winners(df: pd.DataFrame):
    """The user's original hypothesis: RP runs early Sat games, winners don't."""
    aa = df[(df["sport_id"] == 12) & (df["day_of_week"] == 5) & df["start_time_bucket"].notna()]
    if aa.empty:
        return
    winners = aa[aa["gap_camp"] == "sat_winner"]
    rp = aa[aa["team_id"] == RUMBLE_PONIES_ID]

    def counts(x):
        return x["start_time_bucket"].value_counts(normalize=True).reindex(BUCKET_ORDER, fill_value=0)

    win_pct = counts(winners) * 100
    rp_pct = counts(rp) * 100

    t = Table(title="Saturday start-time mix: RP vs Double-A sat_winners")
    t.add_column("Bucket")
    t.add_column(f"RP (n={len(rp)})", justify="right")
    t.add_column(f"AA sat_winners (n={len(winners)})", justify="right")
    t.add_column("RP - Winner", justify="right")
    for b in BUCKET_ORDER:
        rv = rp_pct[b]
        wv = win_pct[b]
        diff = rv - wv
        color = "red" if diff > 10 else ("green" if diff < -10 else "dim")
        t.add_row(b, f"{rv:.0f}%", f"{wv:.0f}%", f"[{color}]{diff:+.0f}pp[/{color}]")
    console.print(t)
    console.print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=ANALYSIS_SEASON)
    args = parser.parse_args()

    console.print(f"\n[bold blue]--- Game Times Phase 1 Checkpoint (season {args.season}) ---[/bold blue]\n")

    df = load_games(args.season)
    console.print(f"Loaded {len(df):,} regular-season games ({len(df[df['gap_camp'].notna()]):,} have weekend_gap camp)")
    console.print()

    day_night_disagreement(df)

    console.print("[bold magenta]== Saturday bucket mix per camp (hypothesis test) ==[/bold magenta]")
    bucket_mix_per_camp(df, dow=5, dow_label="Sat")

    console.print("[bold magenta]== Friday bucket mix per camp ==[/bold magenta]")
    bucket_mix_per_camp(df, dow=4, dow_label="Fri")

    console.print("[bold magenta]== Sunday bucket mix per camp ==[/bold magenta]")
    bucket_mix_per_camp(df, dow=6, dow_label="Sun")

    rp_vs_double_a_winners(df)
    rp_clock(df)

    console.print("[bold magenta]== Within-team cap-util lift by bucket ==[/bold magenta]")
    within_team_bucket_lift(df, level_id=None, dow_filter=None)
    within_team_bucket_lift(df, level_id=12, dow_filter=[5])  # Double-A Saturdays
    within_team_bucket_lift(df, level_id=12, dow_filter=[4])  # Double-A Fridays
    within_team_bucket_lift(df, level_id=12, dow_filter=[6])  # Double-A Sundays

    console.print("[bold green]Phase 1 checkpoint complete.[/bold green]")


if __name__ == "__main__":
    main()
