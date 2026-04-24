"""One-off: is Saturday an opportunity day for the Rumble Ponies?

The thesis: RP's Saturday attendance underperforms Friday. At peer teams
(improving Double-A, similar market), Friday ~= Saturday. If RP's Saturday
rose to match its Friday, the annual fan uplift could be material.

This script computes:
  1. RP Friday vs Saturday gap by season
  2. Peer-set (improving Double-A, similar market) Friday vs Saturday gap
  3. Implied annual uplift if RP Saturday matched RP Friday
  4. Which promo categories the peer-set runs on Saturdays that RP does not
"""

import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

HERO_TEAM = "Binghamton Rumble Ponies"
PROMO_COLS = [
    "has_fireworks", "has_giveaway", "has_food_deal", "has_ticket_deal",
    "has_theme_night", "has_kids_event", "has_heritage", "has_community",
    "has_entertain", "has_dog", "has_celebrity", "has_recurring",
]


def engine():
    url = (
        f"postgresql://{os.getenv('DB_USERNAME', 'postgres')}:"
        f"{os.getenv('DB_PASSWORD', 'postgres')}@"
        f"{os.getenv('DB_HOST', '127.0.0.1')}:"
        f"{os.getenv('DB_PORT', '5432')}/"
        f"{os.getenv('DB_NAME', 'baseball')}"
    )
    return create_engine(url, pool_pre_ping=True)


def q(eng, sql: str, params: dict | None = None) -> pd.DataFrame:
    with eng.connect() as c:
        return pd.read_sql(text(sql), c, params=params or {})


def main() -> int:
    eng = engine()

    # -- Resolve hero team_id --------------------------------------------------
    hero = q(eng, "SELECT team_id, sport_id FROM milb.teams WHERE team_name = :n",
             {"n": HERO_TEAM})
    if hero.empty:
        print(f"Team '{HERO_TEAM}' not found.")
        return 1
    hero_id = int(hero.iloc[0]["team_id"])
    hero_sport = int(hero.iloc[0]["sport_id"])

    # -- 1. Hero Friday vs Saturday by season ---------------------------------
    print("=" * 72)
    print(f"STEP 1 -- {HERO_TEAM} Friday vs Saturday attendance, by season")
    print("=" * 72)
    rp_dow = q(eng, f"""
        SELECT season,
               TO_CHAR(game_date, 'Dy') AS dow,
               COUNT(*) AS games,
               AVG(attendance)::int AS avg_att
          FROM milb.games
         WHERE home_team_id = {hero_id}
           AND abstract_game_state = 'Final'
           AND game_type = 'R'
           AND attendance IS NOT NULL AND attendance > 0
           AND TO_CHAR(game_date, 'Dy') IN ('Fri', 'Sat')
         GROUP BY 1, 2
         ORDER BY 1, 2
    """)
    pivot = rp_dow.pivot(index="season", columns="dow", values="avg_att")
    if "Fri" in pivot.columns and "Sat" in pivot.columns:
        pivot["Sat - Fri"] = pivot["Sat"] - pivot["Fri"]
        pivot["Sat / Fri"] = (pivot["Sat"] / pivot["Fri"] * 100).round(1)
    print(pivot.to_string())

    # Game counts for uplift math
    gc = rp_dow.pivot(index="season", columns="dow", values="games")
    print("\nGame counts:")
    print(gc.to_string())

    # -- 2. Peer set: improving/stable Double-A, MSA size similar -------------
    print()
    print("=" * 72)
    print("STEP 2 -- Peer set (Double-A, improving/stable momentum) Fri vs Sat")
    print("=" * 72)

    # Get hero MSA size for market-similar filter
    hero_msa = q(eng, f"""
        SELECT vd.msa_population
          FROM milb.teams t
          JOIN milb.venues v ON t.venue_id = v.venue_id
          LEFT JOIN LATERAL (
              SELECT * FROM milb.venue_demographics
               WHERE venue_id = v.venue_id
               ORDER BY census_year DESC LIMIT 1
          ) vd ON TRUE
         WHERE t.team_id = {hero_id}
    """)
    hero_msa_pop = float(hero_msa.iloc[0]["msa_population"] or 0)
    msa_low, msa_high = hero_msa_pop * 0.5, hero_msa_pop * 2.0
    print(f"Hero MSA population: {int(hero_msa_pop):,}")
    print(f"Peer MSA window:    {int(msa_low):,} to {int(msa_high):,}")

    peer_ids_df = q(eng, f"""
        SELECT t.team_id, t.team_name, tm.momentum_label
          FROM milb.teams t
          JOIN milb.venues v ON t.venue_id = v.venue_id
          LEFT JOIN LATERAL (
              SELECT msa_population FROM milb.venue_demographics
               WHERE venue_id = v.venue_id
               ORDER BY census_year DESC LIMIT 1
          ) vd ON TRUE
          LEFT JOIN milb.team_momentum tm
                 ON t.team_id = tm.team_id
                AND tm.season = (SELECT MAX(season) FROM milb.team_momentum)
         WHERE t.sport_id = {hero_sport}
           AND t.team_id != {hero_id}
           AND vd.msa_population BETWEEN {msa_low} AND {msa_high}
           AND tm.momentum_label IN ('improving', 'surging', 'stable')
    """)
    peer_ids = tuple(peer_ids_df["team_id"].tolist())
    if not peer_ids:
        print("No peers match. Loosening to all Double-A improving/stable.")
        peer_ids_df = q(eng, f"""
            SELECT t.team_id, t.team_name, tm.momentum_label
              FROM milb.teams t
              LEFT JOIN milb.team_momentum tm
                     ON t.team_id = tm.team_id
                    AND tm.season = (SELECT MAX(season) FROM milb.team_momentum)
             WHERE t.sport_id = {hero_sport}
               AND t.team_id != {hero_id}
               AND tm.momentum_label IN ('improving', 'surging', 'stable')
        """)
        peer_ids = tuple(peer_ids_df["team_id"].tolist())

    print(f"\nPeers in set ({len(peer_ids)}):")
    print(peer_ids_df.to_string(index=False))

    if not peer_ids:
        print("Still no peers. Abort step 2.")
        return 0

    ids_sql = ",".join(str(i) for i in peer_ids)
    peer_dow = q(eng, f"""
        SELECT TO_CHAR(game_date, 'Dy') AS dow,
               COUNT(*) AS games,
               AVG(attendance)::int AS avg_att
          FROM milb.games
         WHERE home_team_id IN ({ids_sql})
           AND abstract_game_state = 'Final'
           AND game_type = 'R'
           AND attendance IS NOT NULL AND attendance > 0
           AND TO_CHAR(game_date, 'Dy') IN ('Fri', 'Sat')
         GROUP BY 1
    """)
    print("\nPeer-set Fri vs Sat (pooled across peers):")
    print(peer_dow.to_string(index=False))

    peer_fri = peer_dow[peer_dow["dow"] == "Fri"]["avg_att"]
    peer_sat = peer_dow[peer_dow["dow"] == "Sat"]["avg_att"]
    if not peer_fri.empty and not peer_sat.empty:
        peer_gap = int(peer_sat.iloc[0]) - int(peer_fri.iloc[0])
        print(f"\nPeer Sat - Fri gap: {peer_gap:+,} fans")
    else:
        peer_gap = None

    # -- 3. Implied annual uplift if RP Sat matched RP Fri --------------------
    print()
    print("=" * 72)
    print("STEP 3 -- Implied annual uplift if Saturday matched Friday")
    print("=" * 72)
    if "Fri" in pivot.columns and "Sat" in pivot.columns and "Sat" in gc.columns:
        for season in pivot.index:
            fri = pivot.loc[season, "Fri"] if pd.notna(pivot.loc[season, "Fri"]) else None
            sat = pivot.loc[season, "Sat"] if pd.notna(pivot.loc[season, "Sat"]) else None
            sat_games = gc.loc[season, "Sat"] if pd.notna(gc.loc[season, "Sat"]) else 0
            if fri is not None and sat is not None and sat_games:
                per_game_gap = fri - sat
                annual = per_game_gap * sat_games
                print(f"  {int(season)}: Fri={int(fri):,}, Sat={int(sat):,}, "
                      f"gap/game=+{int(per_game_gap):,}, "
                      f"Sat games={int(sat_games)}, annual uplift=+{int(annual):,} fans")

    # -- 4. Promo categories peers run on Saturdays that RP doesn't -----------
    print()
    print("=" * 72)
    print("STEP 4 -- Promo categories peers run on Saturdays vs Hero Saturdays")
    print("=" * 72)

    promo_avg_sql = ", ".join(f"AVG({c}::int) AS {c}" for c in PROMO_COLS)

    def _sat_usage(team_filter_sql: str) -> dict[str, float]:
        df = q(eng, f"""
            SELECT {promo_avg_sql}
              FROM milb.game_features
             WHERE TO_CHAR(game_date, 'Dy') = 'Sat'
               AND season = (SELECT MAX(season) FROM milb.game_features)
               AND {team_filter_sql}
        """)
        if df.empty:
            return {}
        return {c: float(df.iloc[0][c] or 0) for c in PROMO_COLS}

    hero_sat = _sat_usage(f"team_id = {hero_id}")
    peer_sat = _sat_usage(f"team_id IN ({ids_sql})")

    rows = []
    for col in PROMO_COLS:
        mine, theirs = hero_sat.get(col, 0), peer_sat.get(col, 0)
        rows.append((col, mine, theirs, theirs - mine))
    rows.sort(key=lambda r: r[3], reverse=True)

    print(f"{'Promo':<20} {'Hero Sat':>10} {'Peer Sat':>10} {'Gap':>10}")
    for name, mine, theirs, gap in rows:
        tag = "  <-- peers lean here" if gap > 0.10 else ""
        print(f"{name:<20} {mine:>10.0%} {theirs:>10.0%} {gap:>+10.0%}{tag}")

    print()
    print("Silver-bullet verdict:")
    if pivot.get("Sat - Fri") is not None:
        recent_gap = pivot["Sat - Fri"].dropna()
        if not recent_gap.empty:
            latest = int(recent_gap.iloc[-1])
            if latest < -300:
                print(f"  STRONG: Latest season Sat is {-latest:,} fans BELOW Fri. "
                      f"Peer gap above tells you how much of that is fixable.")
            elif latest < 0:
                print(f"  MODEST: Sat is {-latest:,} below Fri -- closable but not huge.")
            else:
                print(f"  WEAK: Sat already >= Fri ({latest:+,}). Silver bullet is elsewhere.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
