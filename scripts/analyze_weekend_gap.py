"""Weekend gap analysis: classify teams by Sat-vs-Fri attendance pattern and
compare the promo mix of Sat-winners vs Sat-losers on each weekend night.

Fills two tables:
  - milb.weekend_gap          (one row per team-season)
  - milb.weekend_promo_mix    (per camp x DOW x promo flag, per level + pooled)

Classification lives on gap_pct = (sat_avg - fri_avg) / season_avg:
  sat_winner  gap_pct >= +5%
  sat_loser   gap_pct <= -5%
  neutral     otherwise

Capacity-utilization metrics are stored alongside so the page can use cap util
as the denominator where appropriate.

Usage:
    python scripts/analyze_weekend_gap.py
    python scripts/analyze_weekend_gap.py --force
    python scripts/analyze_weekend_gap.py --season 2025
"""

import argparse
import sys
import time
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

LEVEL_NAMES = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A"}

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

# day_of_week: Mon=0..Sun=6
FRI_DOW = 4
SAT_DOW = 5

# Qualifier thresholds
MIN_FRI_GAMES = 8
MIN_SAT_GAMES = 8

# Camp thresholds (fraction of season avg)
SAT_WINNER_THRESH = 0.05
SAT_LOSER_THRESH = -0.05

RUMBLE_PONIES_ID = 505


def should_run(force: bool) -> bool:
    if force:
        return True
    with engine.connect() as conn:
        last = conn.execute(text("""
            SELECT input_max_updated FROM milb.analysis_runs
            WHERE analysis_name = 'weekend_gap' AND status = 'completed'
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
    row = session.execute(text("""
        INSERT INTO milb.analysis_runs (analysis_name, input_max_updated, status)
        VALUES ('weekend_gap', :max_up, 'running')
        RETURNING run_id
    """), {"max_up": current[0]})
    session.commit()
    return row.fetchone()[0]


def load_season_games(season: int) -> pd.DataFrame:
    """All regular-season games for the season, plus team metadata."""
    flag_cols = ", ".join(f"f.{c}" for c in PROMO_FLAGS)
    return pd.read_sql(text(f"""
        SELECT f.game_pk, f.team_id, f.season, f.sport_id, f.game_date,
               f.day_of_week, f.attendance, f.capacity_utilization,
               f.venue_capacity, f.promo_count,
               {flag_cols},
               t.team_name,
               o.operator_name,
               m.momentum_label
          FROM milb.game_features f
          JOIN milb.teams t              ON t.team_id = f.team_id
          LEFT JOIN milb.team_operators o ON o.operator_id = t.operator_id
          LEFT JOIN milb.team_momentum m  ON m.team_id = f.team_id AND m.season = f.season
         WHERE f.season = :season
           AND f.game_type = 'R'
           AND f.attendance IS NOT NULL
    """), engine, params={"season": season})


def drop_doubleheaders(df: pd.DataFrame) -> pd.DataFrame:
    """Same home team + same date → doubleheader. Drop both halves; the
    reported gate on each pk is unreliable."""
    counts = df.groupby(["team_id", "game_date"]).size()
    dh_keys = counts[counts > 1].index
    if len(dh_keys) == 0:
        return df
    dh_set = set(dh_keys)
    mask = df.set_index(["team_id", "game_date"]).index.isin(dh_set)
    return df[~mask].copy()


def build_weekend_gap(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (team_id, season) with Fri/Sat aggregates and classification."""
    season_agg = df.groupby(["team_id", "season", "sport_id"], as_index=False).agg(
        season_avg=("attendance", "mean"),
        season_cap_util=("capacity_utilization", "mean"),
        venue_capacity=("venue_capacity", "max"),
        team_name=("team_name", "first"),
        operator_name=("operator_name", "first"),
        momentum_label=("momentum_label", "first"),
    )

    fri_df = df[df["day_of_week"] == FRI_DOW]
    sat_df = df[df["day_of_week"] == SAT_DOW]

    fri_agg = fri_df.groupby(["team_id", "season"], as_index=False).agg(
        n_fri=("game_pk", "count"),
        fri_avg=("attendance", "mean"),
        fri_cap_util=("capacity_utilization", "mean"),
    )
    sat_agg = sat_df.groupby(["team_id", "season"], as_index=False).agg(
        n_sat=("game_pk", "count"),
        sat_avg=("attendance", "mean"),
        sat_cap_util=("capacity_utilization", "mean"),
    )

    out = (season_agg
           .merge(fri_agg, on=["team_id", "season"], how="inner")
           .merge(sat_agg, on=["team_id", "season"], how="inner"))

    # Qualifier
    out = out[(out["n_fri"] >= MIN_FRI_GAMES) & (out["n_sat"] >= MIN_SAT_GAMES)].copy()

    out["gap_fans"] = out["sat_avg"] - out["fri_avg"]
    out["gap_pct"] = np.where(out["season_avg"] > 0, out["gap_fans"] / out["season_avg"], np.nan)
    out["gap_cap_util_pts"] = out["sat_cap_util"] - out["fri_cap_util"]

    def classify(p: float) -> str:
        if pd.isna(p):
            return "neutral"
        if p >= SAT_WINNER_THRESH:
            return "sat_winner"
        if p <= SAT_LOSER_THRESH:
            return "sat_loser"
        return "neutral"

    out["gap_camp"] = out["gap_pct"].apply(classify)

    return out


def build_promo_mix(df: pd.DataFrame, camps: pd.DataFrame) -> pd.DataFrame:
    """For each (sport_id incl pooled) x camp x DOW x promo_flag, compute usage."""
    # Attach camp and filter to qualifying teams
    keyed = df.merge(camps[["team_id", "season", "gap_camp"]],
                     on=["team_id", "season"], how="inner")
    keyed = keyed[keyed["day_of_week"].isin((FRI_DOW, SAT_DOW))].copy()
    keyed["dow_label"] = np.where(keyed["day_of_week"] == FRI_DOW, "Fri", "Sat")

    rows = []

    def summarize(sub: pd.DataFrame, season: int, sport_id, camp: str, dow: str):
        if sub.empty:
            return
        n_games = len(sub)
        n_teams = sub["team_id"].nunique()
        avg_pc = float(sub["promo_count"].mean()) if "promo_count" in sub else None
        for flag in PROMO_FLAGS:
            if flag not in sub.columns:
                continue
            pct = float(sub[flag].fillna(False).astype(int).mean())
            rows.append({
                "season": season,
                "sport_id": sport_id,
                "gap_camp": camp,
                "dow_label": dow,
                "promo_type": flag,
                "pct_games_with_promo": round(pct, 4),
                "avg_promo_count": round(avg_pc, 2) if avg_pc is not None else None,
                "n_games": n_games,
                "n_teams": n_teams,
            })

    season = int(keyed["season"].iloc[0]) if not keyed.empty else None

    for sport_id in (None, 11, 12, 13, 14):
        sub_level = keyed if sport_id is None else keyed[keyed["sport_id"] == sport_id]
        for camp in ("sat_winner", "neutral", "sat_loser"):
            for dow in ("Fri", "Sat"):
                sub = sub_level[(sub_level["gap_camp"] == camp) & (sub_level["dow_label"] == dow)]
                summarize(sub, season, sport_id, camp, dow)

    return pd.DataFrame(rows)


def write_tables(gap_df: pd.DataFrame, mix_df: pd.DataFrame, run_id: int):
    gap_out = gap_df[[
        "team_id", "season", "sport_id",
        "n_fri", "n_sat",
        "season_avg", "fri_avg", "sat_avg", "gap_fans", "gap_pct",
        "venue_capacity", "season_cap_util", "fri_cap_util", "sat_cap_util", "gap_cap_util_pts",
        "gap_camp", "momentum_label", "operator_name",
    ]].copy()

    for c in ("season_avg", "fri_avg", "sat_avg", "gap_fans"):
        gap_out[c] = gap_out[c].round(1)
    for c in ("gap_pct", "season_cap_util", "fri_cap_util", "sat_cap_util", "gap_cap_util_pts"):
        gap_out[c] = gap_out[c].round(4)
    gap_out["run_id"] = run_id

    with engine.begin() as conn:
        season = int(gap_out["season"].iloc[0])
        conn.execute(text("DELETE FROM milb.weekend_gap WHERE season = :s"), {"s": season})
        conn.execute(text("DELETE FROM milb.weekend_promo_mix WHERE season = :s"), {"s": season})
        gap_out.to_sql("weekend_gap", conn, schema="milb", if_exists="append", index=False)
        if not mix_df.empty:
            mix_df = mix_df.copy()
            mix_df["run_id"] = run_id
            mix_df.to_sql("weekend_promo_mix", conn, schema="milb", if_exists="append", index=False)

    console.print(f"  Wrote {len(gap_out)} weekend_gap rows, {len(mix_df)} weekend_promo_mix rows")


# ---------- Checkpoint printouts ----------

def print_camp_summary(gap: pd.DataFrame):
    t = Table(title="Camp counts (gap_pct thresholds ±5% of season avg)")
    t.add_column("Level"); t.add_column("sat_winner", justify="right")
    t.add_column("neutral", justify="right"); t.add_column("sat_loser", justify="right")
    t.add_column("total", justify="right")
    for sid in (11, 12, 13, 14):
        sub = gap[gap["sport_id"] == sid]
        counts = sub["gap_camp"].value_counts().to_dict()
        t.add_row(LEVEL_NAMES[sid],
                  str(counts.get("sat_winner", 0)),
                  str(counts.get("neutral", 0)),
                  str(counts.get("sat_loser", 0)),
                  str(len(sub)))
    tot = gap["gap_camp"].value_counts().to_dict()
    t.add_row("[bold]LEAGUE[/bold]",
              f"[bold]{tot.get('sat_winner',0)}[/bold]",
              f"[bold]{tot.get('neutral',0)}[/bold]",
              f"[bold]{tot.get('sat_loser',0)}[/bold]",
              f"[bold]{len(gap)}[/bold]")
    console.print(t); console.print()


def print_sensitivity(gap: pd.DataFrame):
    """Show how camp counts shift at ±3% / ±5% / ±8% so we can sanity-check."""
    t = Table(title="Sensitivity: loser count at different gap_pct thresholds")
    t.add_column("Threshold")
    for sid in (11, 12, 13, 14):
        t.add_column(LEVEL_NAMES[sid], justify="right")
    t.add_column("LEAGUE", justify="right")
    for thresh in (0.03, 0.05, 0.08):
        row = [f"gap_pct <= -{thresh*100:.0f}%"]
        for sid in (11, 12, 13, 14):
            sub = gap[gap["sport_id"] == sid]
            row.append(str(int((sub["gap_pct"] <= -thresh).sum())))
        row.append(str(int((gap["gap_pct"] <= -thresh).sum())))
        t.add_row(*row)
    console.print(t); console.print()


def print_camp_agreement(gap: pd.DataFrame):
    """How often does cap-util-based classification disagree with fan-based?"""
    def cap_camp(x):
        if pd.isna(x):
            return "neutral"
        if x >= 0.025:   # cap util points; ~half of 5% fan threshold for a 50% utilized team
            return "sat_winner"
        if x <= -0.025:
            return "sat_loser"
        return "neutral"

    g = gap.copy()
    g["cap_camp"] = g["gap_cap_util_pts"].apply(cap_camp)
    cross = pd.crosstab(g["gap_camp"], g["cap_camp"], margins=True)
    agree = int((g["gap_camp"] == g["cap_camp"]).sum())
    console.print(f"[bold]Fan-camp vs cap-util-camp agreement:[/bold] {agree}/{len(g)} "
                  f"({agree/len(g)*100:.0f}%)")
    console.print(cross.to_string()); console.print()


def print_binghamton(gap: pd.DataFrame):
    rp = gap[gap["team_id"] == RUMBLE_PONIES_ID]
    if rp.empty:
        console.print("[yellow]Binghamton not in qualifying set[/yellow]"); return
    r = rp.iloc[0]

    # Where does RP sit in the league distribution?
    rank = int((gap["gap_pct"] < r["gap_pct"]).sum()) + 1
    pct = rank / len(gap) * 100

    level_df = gap[gap["sport_id"] == int(r["sport_id"])]
    lvl_rank = int((level_df["gap_pct"] < r["gap_pct"]).sum()) + 1
    lvl_pct = lvl_rank / len(level_df) * 100

    t = Table(title="Binghamton Rumble Ponies — Fri/Sat profile")
    t.add_column("Metric"); t.add_column("Value", justify="right")
    t.add_row("Season avg",              f"{r['season_avg']:,.0f}")
    t.add_row("Friday avg   (n=%d)" % r["n_fri"], f"{r['fri_avg']:,.0f}  ({r['fri_cap_util']*100:.1f}% cap)")
    t.add_row("Saturday avg (n=%d)" % r["n_sat"], f"{r['sat_avg']:,.0f}  ({r['sat_cap_util']*100:.1f}% cap)")
    t.add_row("Gap (fans)",              f"{r['gap_fans']:+,.0f}")
    t.add_row("Gap (% of season avg)",   f"{r['gap_pct']*100:+.1f}%")
    t.add_row("Gap (cap util pts)",      f"{r['gap_cap_util_pts']*100:+.2f} pts")
    t.add_row("Camp",                    r["gap_camp"])
    t.add_row("League rank (asc)",       f"{rank}/{len(gap)} ({pct:.0f}%ile)")
    t.add_row(f"{LEVEL_NAMES[int(r['sport_id'])]} rank (asc)", f"{lvl_rank}/{len(level_df)} ({lvl_pct:.0f}%ile)")
    console.print(t); console.print()


def print_sat_loser_club(gap: pd.DataFrame):
    losers = gap[gap["gap_camp"] == "sat_loser"].sort_values("gap_pct")
    if losers.empty:
        console.print("[yellow]No sat_loser teams — story may not hold[/yellow]"); return

    t = Table(title=f"Sat-loser club (n={len(losers)})")
    t.add_column("Team"); t.add_column("Level")
    t.add_column("Season avg", justify="right")
    t.add_column("Fri", justify="right"); t.add_column("Sat", justify="right")
    t.add_column("Gap %", justify="right"); t.add_column("Cap pts", justify="right")
    t.add_column("Momentum"); t.add_column("Operator")
    for _, r in losers.iterrows():
        t.add_row(
            r["team_name"][:28],
            LEVEL_NAMES.get(int(r["sport_id"]), "?"),
            f"{r['season_avg']:,.0f}",
            f"{r['fri_avg']:,.0f}",
            f"{r['sat_avg']:,.0f}",
            f"{r['gap_pct']*100:+.1f}%",
            f"{r['gap_cap_util_pts']*100:+.2f}",
            r["momentum_label"] if pd.notna(r["momentum_label"]) else "-",
            (r["operator_name"] if pd.notna(r["operator_name"]) else "-")[:18],
        )
    console.print(t); console.print()


def print_promo_mix_diff(mix: pd.DataFrame):
    """For each level (pooled first), show winner - loser spread on Sat and Fri."""
    console.print("[bold yellow]Promo mix gap: sat_winner − sat_loser, in percentage points[/bold yellow]")
    for sport_id in (None, 11, 12, 13, 14):
        label = "LEAGUE (pooled)" if sport_id is None else LEVEL_NAMES[sport_id]
        sub = mix[mix["sport_id"].isna() if sport_id is None else mix["sport_id"] == sport_id]
        if sub.empty:
            continue

        wide = sub.pivot_table(
            index="promo_type", columns=["dow_label", "gap_camp"],
            values="pct_games_with_promo", aggfunc="first"
        )
        # Safely compute winner-loser for Sat and Fri
        def diff(dow):
            if (dow, "sat_winner") in wide.columns and (dow, "sat_loser") in wide.columns:
                return (wide[(dow, "sat_winner")] - wide[(dow, "sat_loser")]) * 100
            return pd.Series(dtype=float)

        sat_diff = diff("Sat")
        fri_diff = diff("Fri")

        t = Table(title=label)
        t.add_column("Promo"); t.add_column("Sat: W%", justify="right")
        t.add_column("Sat: L%", justify="right"); t.add_column("Sat: W−L", justify="right")
        t.add_column("Fri: W−L", justify="right")
        ordered = sat_diff.reindex(PROMO_FLAGS).sort_values(ascending=False, na_position="last")
        for flag in ordered.index:
            w = wide.get(("Sat", "sat_winner"), pd.Series()).get(flag, np.nan)
            l = wide.get(("Sat", "sat_loser"), pd.Series()).get(flag, np.nan)
            s = sat_diff.get(flag, np.nan)
            f = fri_diff.get(flag, np.nan)
            if pd.isna(s):
                continue
            color = "green" if s >= 10 else ("red" if s <= -10 else "dim")
            t.add_row(
                f"[{color}]{PROMO_LABELS.get(flag, flag)}[/{color}]",
                f"{w*100:.0f}%" if pd.notna(w) else "-",
                f"{l*100:.0f}%" if pd.notna(l) else "-",
                f"[{color}]{s:+.0f}pp[/{color}]",
                f"{f:+.0f}pp" if pd.notna(f) else "-",
            )
        console.print(t)
    console.print()


def print_binghamton_overlay(df: pd.DataFrame, mix: pd.DataFrame):
    """RP's Sat/Fri promo mix vs sat_winner and sat_loser averages for its level."""
    rp = df[df["team_id"] == RUMBLE_PONIES_ID]
    if rp.empty:
        return
    sport_id = int(rp["sport_id"].iloc[0])

    rp_fri = rp[rp["day_of_week"] == FRI_DOW]
    rp_sat = rp[rp["day_of_week"] == SAT_DOW]
    if rp_fri.empty or rp_sat.empty:
        return

    def rp_pct(sub, flag):
        return sub[flag].fillna(False).astype(int).mean() if flag in sub else np.nan

    level_mix = mix[mix["sport_id"] == sport_id]

    t = Table(title=f"Binghamton overlay — Saturdays vs {LEVEL_NAMES[sport_id]} camps")
    t.add_column("Promo")
    t.add_column("RP Sat %", justify="right")
    t.add_column("Winner Sat", justify="right"); t.add_column("Loser Sat", justify="right")
    t.add_column("RP vs Winner", justify="right"); t.add_column("Pattern-match")
    for flag in PROMO_FLAGS:
        rp_val = rp_pct(rp_sat, flag)
        w = level_mix[(level_mix["gap_camp"] == "sat_winner") & (level_mix["dow_label"] == "Sat") &
                      (level_mix["promo_type"] == flag)]["pct_games_with_promo"]
        l = level_mix[(level_mix["gap_camp"] == "sat_loser") & (level_mix["dow_label"] == "Sat") &
                      (level_mix["promo_type"] == flag)]["pct_games_with_promo"]
        w_val = float(w.iloc[0]) if not w.empty else np.nan
        l_val = float(l.iloc[0]) if not l.empty else np.nan
        if pd.isna(rp_val) or pd.isna(w_val) or pd.isna(l_val):
            continue
        match = "winner" if abs(rp_val - w_val) < abs(rp_val - l_val) else "loser"
        color = "green" if match == "winner" else "red"
        t.add_row(
            PROMO_LABELS.get(flag, flag),
            f"{rp_val*100:.0f}%",
            f"{w_val*100:.0f}%", f"{l_val*100:.0f}%",
            f"{(rp_val - w_val)*100:+.0f}pp",
            f"[{color}]{match}[/{color}]",
        )
    console.print(t); console.print()


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description="Weekend gap analysis")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--season", type=int, default=2025)
    args = parser.parse_args()

    console.print(f"\n[bold blue]--- Weekend Gap Analysis (season {args.season}) ---[/bold blue]\n")

    if not should_run(args.force):
        console.print("[green]Data unchanged since last run. Use --force to rebuild.[/green]")
        return

    session = get_session()
    run_id = log_run_start(session)

    try:
        start = time.time()
        df = load_season_games(args.season)
        console.print(f"Loaded {len(df):,} regular-season games")

        df = drop_doubleheaders(df)
        console.print(f"After dropping doubleheaders: {len(df):,}")

        gap = build_weekend_gap(df)
        console.print(f"Qualifying teams (>={MIN_FRI_GAMES} Fri and >={MIN_SAT_GAMES} Sat): {len(gap):,}")
        console.print()

        mix = build_promo_mix(df, gap)
        write_tables(gap, mix, run_id)
        console.print()

        # Checkpoint tables
        print_camp_summary(gap)
        print_sensitivity(gap)
        print_camp_agreement(gap)
        print_sat_loser_club(gap)
        print_binghamton(gap)
        print_promo_mix_diff(mix)
        print_binghamton_overlay(df, mix)

        session.execute(text("""
            UPDATE milb.analysis_runs
            SET status='completed', completed_at=NOW(), record_count=:n
            WHERE run_id=:rid
        """), {"n": len(gap) + len(mix), "rid": run_id})
        session.commit()

        elapsed = time.time() - start
        console.print(f"[bold green]Done in {elapsed:.1f}s[/bold green]")

    except Exception as e:
        session.execute(text("""
            UPDATE milb.analysis_runs
            SET status='failed', completed_at=NOW(), error_message=:err
            WHERE run_id=:rid
        """), {"err": str(e), "rid": run_id})
        session.commit()
        console.print(f"[bold red]Error: {e}[/bold red]")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
