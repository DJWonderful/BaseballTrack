"""Throwaway — pull context stats on Binghamton for hypothesis generation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from streamlit_app.utils.db import query_df

r = query_df("""
SELECT t.team_name, v.capacity,
       ROUND(d.msa_population::numeric/1000) AS msa_k,
       ROUND(d.msa_median_income::numeric) AS income,
       ROUND(d.msa_poverty_rate::numeric, 1) AS poverty_pct,
       gf.population_trend,
       gf.population_change_5yr_pct
FROM milb.teams t
JOIN milb.venues v ON t.venue_id=v.venue_id
LEFT JOIN LATERAL (SELECT * FROM milb.venue_demographics d2
                   WHERE d2.venue_id=v.venue_id ORDER BY d2.census_year DESC LIMIT 1) d ON TRUE
LEFT JOIN LATERAL (SELECT * FROM milb.game_features gf2 WHERE gf2.team_id=t.team_id ORDER BY season DESC LIMIT 1) gf ON TRUE
WHERE t.team_id=505
ORDER BY t.team_name
LIMIT 1
""")
print("Binghamton baseline:")
print(r.to_string(index=False))
print()

# Double-A market spread
r_peers = query_df("""
SELECT
  MIN(d.msa_population)/1000.0 AS min_msa_k,
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY d.msa_population)/1000.0 AS med_msa_k,
  MAX(d.msa_population)/1000.0 AS max_msa_k,
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY d.msa_median_income) AS med_income,
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY v.capacity) AS med_cap
FROM milb.teams t
JOIN milb.venues v ON t.venue_id=v.venue_id
LEFT JOIN LATERAL (SELECT * FROM milb.venue_demographics d2
                   WHERE d2.venue_id=v.venue_id ORDER BY d2.census_year DESC LIMIT 1) d ON TRUE
WHERE t.sport_id=12 AND t.team_id != 505
""")
print("Double-A league median (for context):")
print(r_peers.to_string(index=False))
print()

# RP year-over-year
r2 = query_df("""
SELECT season,
       COUNT(*) AS games,
       ROUND(AVG(attendance)::numeric) AS avg_att,
       ROUND(AVG(attendance/NULLIF(v.capacity,0))::numeric, 3) AS cap_util,
       ROUND(AVG(CASE WHEN home_score > away_score THEN 1.0 ELSE 0.0 END)::numeric, 3) AS home_win_pct
FROM milb.games g
JOIN milb.teams t ON g.home_team_id=t.team_id
JOIN milb.venues v ON t.venue_id=v.venue_id
WHERE g.home_team_id=505 AND g.abstract_game_state='Final'
  AND g.game_type='R' AND g.attendance > 0
GROUP BY season ORDER BY season
""")
print("RP year-over-year:")
print(r2.to_string(index=False))
print()

# Demographics trend
r3 = query_df("""
SELECT census_year, msa_population, msa_median_income, msa_poverty_rate
FROM milb.venue_demographics d JOIN milb.teams t ON t.venue_id=d.venue_id
WHERE t.team_id=505 ORDER BY census_year
""")
print("Binghamton MSA demographic trend:")
print(r3.to_string(index=False))
print()

# Momentum
r4 = query_df("""
SELECT team_id, momentum_label, momentum_score, yoy_cap_util_change
FROM milb.team_momentum WHERE team_id=505
""")
print("Momentum:")
print(r4.to_string(index=False))
