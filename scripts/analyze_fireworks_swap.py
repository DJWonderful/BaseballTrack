"""Fireworks-swap counterfactual: move fireworks Fri -> Sat, stack
giveaway/kids/celebrity/entertainment on Fri. Projects annual gate impact
for Binghamton with bootstrap 95% CIs.

The hypothesis the manager wants to take into the meeting:
  "Fireworks should carry Saturday. Friday should stack giveaway,
   kids events, celebrity appearances, and entertainment acts."

The data says RP currently has an inverted Fri/Sat pattern (Fri > Sat) while
Sat-winner peers show Sat > Fri by 10-20%. Fireworks lift is ~+50% at RP but
they run most of their fireworks Fri -- putting the biggest lever on the
already-stronger day. Swapping should both raise Saturday AND preserve Friday
if the stack is designed to replace the fireworks draw with family-friendly
event volume.

Writes milb.fireworks_swap with 3 scenario rows per team/season:
    current         observed baseline
    peer_baseline   what peer Sat-winners achieve
    counterfactual  projected RP gate after the swap

Usage:
    python scripts/analyze_fireworks_swap.py
    python scripts/analyze_fireworks_swap.py --season 2025
    python scripts/analyze_fireworks_swap.py --force
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

RUMBLE_PONIES_ID = 505
FRI_DOW = 4
SAT_DOW = 5

STACK_FLAGS = ["has_giveaway", "has_kids_event", "has_celebrity", "has_entertain"]
ALL_FLAGS = STACK_FLAGS + ["has_fireworks"]

BOOTSTRAP_ITERS = 2000
RNG = np.random.default_rng(42)


def should_run(force: bool) -> bool:
    if force:
        return True
    with engine.connect() as conn:
        last = conn.execute(text("""
            SELECT input_max_updated FROM milb.analysis_runs
            WHERE analysis_name = 'fireworks_swap' AND status = 'completed'
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
        VALUES ('fireworks_swap', :max_up, 'running')
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


def bootstrap_ci(vals: np.ndarray, n_iter: int = BOOTSTRAP_ITERS,
                 alpha: float = 0.05) -> tuple[float, float, float]:
    """Return (mean, ci_lo, ci_hi)."""
    if len(vals) == 0:
        return (float("nan"), float("nan"), float("nan"))
    mean = float(np.mean(vals))
    if len(vals) < 3:
        return (mean, mean, mean)
    idx = RNG.integers(0, len(vals), size=(n_iter, len(vals)))
    means = vals[idx].mean(axis=1)
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return (mean, lo, hi)


def load_games(season: int, sport_id: int) -> pd.DataFrame:
    """Pool the target season plus up to 2 prior complete seasons.

    Pooling matters: with ~20 Fri games per season, a single-season fireworks
    lift estimate has a 95% CI wider than the projected effect. Three seasons
    of the same team/level tightens the lift estimate substantially.
    """
    flag_cols = ", ".join(f"f.{c}" for c in ALL_FLAGS)
    seasons = tuple(s for s in (season, season - 1, season - 2) if s >= 2023)
    placeholders = ", ".join(str(s) for s in seasons)
    return pd.read_sql(text(f"""
        SELECT f.game_pk, f.team_id, f.season, f.sport_id, f.game_date,
               f.day_of_week, f.attendance, f.capacity_utilization,
               {flag_cols}
          FROM milb.game_features f
         WHERE f.season IN ({placeholders})
           AND f.sport_id = :sport_id
           AND f.game_type = 'R'
           AND f.attendance IS NOT NULL
           AND f.attendance > 0
    """), engine, params={"sport_id": sport_id})


def drop_doubleheaders(df: pd.DataFrame) -> pd.DataFrame:
    counts = df.groupby(["team_id", "game_date"]).size()
    dh_keys = counts[counts > 1].index
    if len(dh_keys) == 0:
        return df
    dh_set = set(dh_keys)
    mask = df.set_index(["team_id", "game_date"]).index.isin(dh_set)
    return df[~mask].copy()


def peer_sat_winner_ids(season: int, sport_id: int) -> list[int]:
    """Teams classified sat_winner by the weekend_gap analysis."""
    df = pd.read_sql(text("""
        SELECT team_id FROM milb.weekend_gap
         WHERE season = :s AND sport_id = :sid AND gap_camp = 'sat_winner'
    """), engine, params={"s": season, "sid": sport_id})
    return df["team_id"].tolist()


def flag_pct(df: pd.DataFrame, flag: str) -> float:
    if df.empty or flag not in df.columns:
        return 0.0
    return float(df[flag].fillna(False).astype(int).mean())


def dow_summary(df: pd.DataFrame, dow: int) -> dict:
    sub = df[df["day_of_week"] == dow]
    att = sub["attendance"].astype(float).to_numpy()
    mean, lo, hi = bootstrap_ci(att)
    return {
        "games": len(sub),
        "avg": mean,
        "ci_lo": lo,
        "ci_hi": hi,
        **{f"{f}_pct": flag_pct(sub, f) for f in ALL_FLAGS},
    }


def load_cf_lifts(team_id: int, sport_id: int) -> dict:
    """Load S-learner CF lifts for all flags we care about.

    Returns a dict keyed by (scope, promo, estimand) -> dict with keys
    mean_lift, std_lift, n_games. Team scope only has ATE (by design of
    analyze_promo_lift_counterfactual.py); level scope has ATE/ATT/ATU.

    Estimand semantics:
        ATE — average effect across all games at this scope
        ATT — effect on games that WERE treated (→ cost of removing the flag)
        ATU — effect on games that were NOT treated (→ benefit of adding it)
    """
    flags = tuple(ALL_FLAGS)
    df = pd.read_sql(text("""
        SELECT scope, team_id, sport_id, promo_type, estimand,
               mean_lift, std_lift, n_games
          FROM milb.promo_lift_cf
         WHERE promo_type IN :flags
           AND ((scope = 'team'  AND team_id  = :tid)
             OR (scope = 'level' AND sport_id = :sid)
             OR (scope = 'league'))
    """), engine, params={"flags": flags, "tid": team_id, "sid": sport_id})
    out: dict[tuple[str, str, str], dict] = {}
    for _, r in df.iterrows():
        key = (r["scope"], r["promo_type"], r["estimand"])
        out[key] = {
            "mean_lift": float(r["mean_lift"]) if pd.notna(r["mean_lift"]) else 0.0,
            "std_lift": float(r["std_lift"]) if pd.notna(r["std_lift"]) else 0.0,
            "n_games": int(r["n_games"]) if pd.notna(r["n_games"]) else 0,
        }
    return out


def cf_lift(cache: dict, promo: str, estimand: str) -> tuple[float, float, str]:
    """Look up a CF lift, preferring team scope then level then league.

    Returns (mean_lift, half_width_95ci, source_label).
    half_width is 1.96 * std_lift / sqrt(n_games), the 95% CI on the mean.
    """
    # Team scope only stores ATE. If the caller asked for ATT/ATU, fall
    # through to level/league.
    for scope in ("team", "level", "league"):
        if scope == "team" and estimand != "ATE":
            continue
        row = cache.get((scope, promo, estimand))
        if row and row["n_games"] >= 20:
            n = max(row["n_games"], 1)
            half = 1.96 * row["std_lift"] / (n ** 0.5)
            return (row["mean_lift"], half, scope)
    return (0.0, 0.0, "none")


def build_current(team_id: int, season: int, sport_id: int,
                  games: pd.DataFrame) -> dict:
    # "Current" reflects the latest complete season only -- that's the state
    # we'd be changing. Pooled data is only used for lift estimation.
    hero = games[(games["team_id"] == team_id) & (games["season"] == season)]
    fri = dow_summary(hero, FRI_DOW)
    sat = dow_summary(hero, SAT_DOW)
    return {
        "team_id": team_id,
        "season": season,
        "sport_id": sport_id,
        "scenario": "current",
        "fri_games": fri["games"],
        "fri_avg_att": fri["avg"],
        "fri_avg_att_ci_lo": fri["ci_lo"],
        "fri_avg_att_ci_hi": fri["ci_hi"],
        "fri_has_fireworks_pct": fri["has_fireworks_pct"],
        "fri_has_giveaway_pct": fri["has_giveaway_pct"],
        "fri_has_kids_pct": fri["has_kids_event_pct"],
        "fri_has_celebrity_pct": fri["has_celebrity_pct"],
        "fri_has_entertain_pct": fri["has_entertain_pct"],
        "sat_games": sat["games"],
        "sat_avg_att": sat["avg"],
        "sat_avg_att_ci_lo": sat["ci_lo"],
        "sat_avg_att_ci_hi": sat["ci_hi"],
        "sat_has_fireworks_pct": sat["has_fireworks_pct"],
        "sat_has_giveaway_pct": sat["has_giveaway_pct"],
        "sat_has_kids_pct": sat["has_kids_event_pct"],
        "sat_has_celebrity_pct": sat["has_celebrity_pct"],
        "sat_has_entertain_pct": sat["has_entertain_pct"],
        "projected_fri_delta": None,
        "projected_sat_delta": None,
        "projected_annual_delta": None,
        "projected_annual_ci_lo": None,
        "projected_annual_ci_hi": None,
        "notes": "Observed baseline for the hero team.",
    }


def build_peer_baseline(team_id: int, season: int, sport_id: int,
                        games: pd.DataFrame, peer_ids: list[int]) -> dict:
    peers = games[games["team_id"].isin(peer_ids)]
    fri = dow_summary(peers, FRI_DOW)
    sat = dow_summary(peers, SAT_DOW)
    notes = (
        f"Peer set = {len(peer_ids)} Sat-winner teams at this level. "
        "Shows the Fri/Sat profile Binghamton is targeting."
    )
    return {
        "team_id": team_id,
        "season": season,
        "sport_id": sport_id,
        "scenario": "peer_baseline",
        "fri_games": fri["games"],
        "fri_avg_att": fri["avg"],
        "fri_avg_att_ci_lo": fri["ci_lo"],
        "fri_avg_att_ci_hi": fri["ci_hi"],
        "fri_has_fireworks_pct": fri["has_fireworks_pct"],
        "fri_has_giveaway_pct": fri["has_giveaway_pct"],
        "fri_has_kids_pct": fri["has_kids_event_pct"],
        "fri_has_celebrity_pct": fri["has_celebrity_pct"],
        "fri_has_entertain_pct": fri["has_entertain_pct"],
        "sat_games": sat["games"],
        "sat_avg_att": sat["avg"],
        "sat_avg_att_ci_lo": sat["ci_lo"],
        "sat_avg_att_ci_hi": sat["ci_hi"],
        "sat_has_fireworks_pct": sat["has_fireworks_pct"],
        "sat_has_giveaway_pct": sat["has_giveaway_pct"],
        "sat_has_kids_pct": sat["has_kids_event_pct"],
        "sat_has_celebrity_pct": sat["has_celebrity_pct"],
        "sat_has_entertain_pct": sat["has_entertain_pct"],
        "projected_fri_delta": None,
        "projected_sat_delta": None,
        "projected_annual_delta": None,
        "projected_annual_ci_lo": None,
        "projected_annual_ci_hi": None,
        "notes": notes,
    }


def build_counterfactual(team_id: int, season: int, sport_id: int,
                         games: pd.DataFrame, peer_ids: list[int],
                         current: dict) -> dict:
    """Project RP Fri/Sat after the swap using S-learner CF lifts.

    Model (marginal, anchored to current observed averages):
        new_fri = current_fri
                  - fri_fw_lift_ATT * current_fri_fw_coverage    (remove FW)
                  + sum(flag_lift_ATU * incremental_coverage)    (add stack)
        new_sat = current_sat
                  + sat_fw_lift_ATU * incremental_sat_fw_coverage (add FW)

    Estimand choice is deliberate:
      - Removing a flag we currently USE → ATT (effect on treated, i.e. what
        we'd lose by de-treating those games).
      - Adding a flag we currently DON'T use → ATU (effect on untreated,
        i.e. what we'd gain by treating those games).

    Scope preference: team-scoped ATE when the team has one; otherwise fall
    back to level scope, then league. Team-scoped rows only exist for ATE
    (by design of analyze_promo_lift_counterfactual.py), so ATT/ATU queries
    always fall through to level/league. That's the correct behavior — the
    raw-lift approach it replaces was trying (badly) to synthesize a
    team-specific counterfactual from ~2-3 no-FW Friday games, which is
    exactly the selection-bias trap the S-learner fixes.
    """
    cf_cache = load_cf_lifts(team_id, sport_id)
    hero = games[games["team_id"] == team_id]
    peers = games[games["team_id"].isin(peer_ids)]

    fri_current = float(current["fri_avg_att"] or 0)
    sat_current = float(current["sat_avg_att"] or 0)
    fri_fw_cov_now = float(current["fri_has_fireworks_pct"] or 0)
    sat_fw_cov_now = float(current["sat_has_fireworks_pct"] or 0)

    lift_sources: list[str] = []

    # --- Friday: remove fireworks (ATT), add stack (ATU) ---
    fri_fw_lift, fri_fw_half, src = cf_lift(cf_cache, "has_fireworks", "ATT")
    lift_sources.append(f"FW-remove:{src}/ATT")
    fri_remove_fw = -fri_fw_lift * fri_fw_cov_now
    fri_remove_fw_half = fri_fw_half * fri_fw_cov_now

    fri_stack_lift = 0.0
    fri_stack_var = 0.0
    for flag in STACK_FLAGS:
        hero_cov = flag_pct(hero[hero["day_of_week"] == FRI_DOW], flag)
        target_cov = max(0.85, flag_pct(peers[peers["day_of_week"] == FRI_DOW], flag))
        incremental_cov = max(0.0, target_cov - hero_cov)
        lift, half, src = cf_lift(cf_cache, flag, "ATU")
        lift_sources.append(f"{flag.replace('has_','')}-add:{src}/ATU")
        fri_stack_lift += lift * incremental_cov
        fri_stack_var  += (half * incremental_cov) ** 2

    new_fri_mean = fri_current + fri_remove_fw + fri_stack_lift
    new_fri_half = float(np.sqrt(fri_remove_fw_half ** 2 + fri_stack_var))

    # --- Saturday: add fireworks (ATU) ---
    target_sat_fw_cov = max(0.75, flag_pct(peers[peers["day_of_week"] == SAT_DOW], "has_fireworks"))
    incremental_fw_cov = max(0.0, target_sat_fw_cov - sat_fw_cov_now)
    sat_fw_lift, sat_fw_half, src = cf_lift(cf_cache, "has_fireworks", "ATU")
    lift_sources.append(f"FW-add:{src}/ATU")
    sat_add_fw = sat_fw_lift * incremental_fw_cov

    new_sat_mean = sat_current + sat_add_fw
    new_sat_half = float(sat_fw_half * incremental_fw_cov)

    # --- Annual delta ---
    fri_delta = new_fri_mean - fri_current
    sat_delta = new_sat_mean - sat_current
    annual = fri_delta * current["fri_games"] + sat_delta * current["sat_games"]
    annual_half = float(np.sqrt(
        (new_fri_half * current["fri_games"]) ** 2
        + (new_sat_half * current["sat_games"]) ** 2
    ))

    notes = (
        f"Counterfactual (S-learner backed): strip fireworks from Fri at "
        f"{fri_fw_cov_now:.0%} coverage, stack {', '.join(STACK_FLAGS)} to "
        f"target coverage; add fireworks to Sat up to "
        f"{target_sat_fw_cov:.0%} coverage. "
        f"Fri net per game {fri_delta:+.0f} (remove FW {fri_remove_fw:+.0f}, "
        f"add stack {fri_stack_lift:+.0f}), "
        f"Sat net per game {sat_delta:+.0f} (add FW {sat_add_fw:+.0f}). "
        f"Lift sources: {', '.join(lift_sources)}."
    )

    return {
        "team_id": team_id,
        "season": season,
        "sport_id": sport_id,
        "scenario": "counterfactual",
        "fri_games": current["fri_games"],
        "fri_avg_att": new_fri_mean,
        "fri_avg_att_ci_lo": new_fri_mean - new_fri_half,
        "fri_avg_att_ci_hi": new_fri_mean + new_fri_half,
        "fri_has_fireworks_pct": 0.0,  # projected: stripped
        "fri_has_giveaway_pct": max(0.85, flag_pct(peers[peers["day_of_week"] == FRI_DOW], "has_giveaway")),
        "fri_has_kids_pct": max(0.85, flag_pct(peers[peers["day_of_week"] == FRI_DOW], "has_kids_event")),
        "fri_has_celebrity_pct": max(0.85, flag_pct(peers[peers["day_of_week"] == FRI_DOW], "has_celebrity")),
        "fri_has_entertain_pct": max(0.85, flag_pct(peers[peers["day_of_week"] == FRI_DOW], "has_entertain")),
        "sat_games": current["sat_games"],
        "sat_avg_att": new_sat_mean,
        "sat_avg_att_ci_lo": new_sat_mean - new_sat_half,
        "sat_avg_att_ci_hi": new_sat_mean + new_sat_half,
        "sat_has_fireworks_pct": target_sat_fw_cov,
        "sat_has_giveaway_pct": current["sat_has_giveaway_pct"],
        "sat_has_kids_pct": current["sat_has_kids_pct"],
        "sat_has_celebrity_pct": current["sat_has_celebrity_pct"],
        "sat_has_entertain_pct": current["sat_has_entertain_pct"],
        "projected_fri_delta": fri_delta,
        "projected_sat_delta": sat_delta,
        "projected_annual_delta": int(round(annual)),
        "projected_annual_ci_lo": int(round(annual - annual_half)),
        "projected_annual_ci_hi": int(round(annual + annual_half)),
        "notes": notes,
    }


def write_rows(rows: list[dict], season: int, run_id: int):
    df = pd.DataFrame(rows)
    for col in ("fri_avg_att", "fri_avg_att_ci_lo", "fri_avg_att_ci_hi",
                "sat_avg_att", "sat_avg_att_ci_lo", "sat_avg_att_ci_hi",
                "projected_fri_delta", "projected_sat_delta"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(1)
    pct_cols = [c for c in df.columns if c.endswith("_pct")]
    for col in pct_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").round(4)
    df["run_id"] = run_id

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM milb.fireworks_swap WHERE season = :s"), {"s": season})
        df.to_sql("fireworks_swap", conn, schema="milb", if_exists="append", index=False)
    console.print(f"  Wrote {len(df)} fireworks_swap rows")


def print_summary(rows: list[dict]):
    t = Table(title="Fireworks swap projection")
    t.add_column("Scenario"); t.add_column("Fri avg", justify="right")
    t.add_column("Sat avg", justify="right"); t.add_column("Annual delta", justify="right")
    t.add_column("Notes")
    for r in rows:
        annual = ""
        if r.get("projected_annual_delta") is not None:
            lo = r["projected_annual_ci_lo"]; hi = r["projected_annual_ci_hi"]
            annual = f"{r['projected_annual_delta']:+,}  ({lo:+,} .. {hi:+,})"
        t.add_row(
            r["scenario"],
            f"{(r['fri_avg_att'] or 0):,.0f}",
            f"{(r['sat_avg_att'] or 0):,.0f}",
            annual,
            (r.get("notes") or "")[:80],
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
        hero = pd.read_sql(text(
            "SELECT team_id, sport_id FROM milb.teams WHERE team_id = :id"
        ), engine, params={"id": RUMBLE_PONIES_ID})
        if hero.empty:
            raise RuntimeError(f"Rumble Ponies ({RUMBLE_PONIES_ID}) not found")
        sport_id = int(hero.iloc[0]["sport_id"])

        if args.season is None:
            # Prefer the latest COMPLETE season for the hero team (>= 40 home
            # games). Current year is often partial and understates the true
            # Fri/Sat base rates.
            r = pd.read_sql(text("""
                SELECT season FROM milb.game_features
                 WHERE team_id = :t AND attendance IS NOT NULL
                 GROUP BY season HAVING COUNT(*) >= 40
                 ORDER BY season DESC LIMIT 1
            """), engine, params={"t": RUMBLE_PONIES_ID})
            if r.empty:
                r = pd.read_sql(text(
                    "SELECT MAX(season) AS season FROM milb.game_features "
                    " WHERE team_id = :t AND attendance IS NOT NULL"
                ), engine, params={"t": RUMBLE_PONIES_ID})
            args.season = int(r.iloc[0]["season"])
            console.print(f"Using latest complete season: [bold]{args.season}[/bold]")

        console.print(f"Loading {args.season} {sport_id=} games...")
        games = load_games(args.season, sport_id)
        games = drop_doubleheaders(games)
        console.print(f"  {len(games):,} games after DH drop")

        peer_ids = peer_sat_winner_ids(args.season, sport_id)
        peer_ids = [p for p in peer_ids if p != RUMBLE_PONIES_ID]
        if not peer_ids:
            console.print("[red]No Sat-winner peers found. Run analyze_weekend_gap.py first.[/red]")
            finalize_run(session, run_id, "failed", err="No Sat-winner peers")
            return 1
        console.print(f"  {len(peer_ids)} Sat-winner peers available")

        current = build_current(RUMBLE_PONIES_ID, args.season, sport_id, games)
        peer = build_peer_baseline(RUMBLE_PONIES_ID, args.season, sport_id, games, peer_ids)
        cf = build_counterfactual(RUMBLE_PONIES_ID, args.season, sport_id, games, peer_ids, current)

        rows = [current, peer, cf]
        write_rows(rows, args.season, run_id)
        print_summary(rows)

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
