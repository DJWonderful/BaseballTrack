# BaseballTrack: Project Overview

## Executive Summary

BaseballTrack is a data analytics platform for Minor League Baseball (MiLB)
attendance intelligence. The system collects game-level data across all 120+
affiliated MiLB teams, enriches it with U.S. Census demographics and historical
weather, and serves it through an interactive Streamlit dashboard.

The platform answers questions like: What drives attendance? How effective are
promotions? Does weather matter? What is the impact of MLB rehab assignments?
How do scheduling decisions affect turnout? How do market demographics relate to
attendance performance?

All data is sourced from authoritative, publicly available APIs and federal
datasets. The pipeline is fully automated, idempotent, and auditable.

---

## Data Sources

### 1. MLB Stats API

**Provider:** Major League Baseball Advanced Media (MLBAM)
**URL:** `https://statsapi.mlb.com/api/v1`
**Authentication:** None required (public API)
**Authority:** This is the same API that powers MLB.com and MiLB.com. It is the
official, authoritative source for all MiLB game data.

| Data Collected | API Endpoint | What It Provides |
|---|---|---|
| Teams, venues, leagues, divisions, organizations | `/teams?sportId={11-14}&season={year}` | Full organizational hierarchy for all affiliated MiLB teams |
| Game schedules | `/schedule?sportId={id}&season={year}` | Every game across all game types (regular season, playoffs) |
| Promotions | `/schedule?hydrate=game(promotions)` | Per-game promotion listings (giveaways, theme nights, fireworks, etc.) |
| Game results & attendance | `/game/{gamePk}/feed/live` | Per-game attendance, final score, duration, API-reported weather |
| Season attendance aggregates | `/attendance?teamId={id}&season={year}` | Official season totals, averages, highs, and lows |
| Roster transactions | `/transactions?teamId={id}&startDate=&endDate=` | Rehab assignments, options, callups for all 30 MLB parent clubs |
| Player details | `/people/{playerId}` | MLB debut dates (used to classify veteran rehab assignments) |

**Sport ID mapping:**
- 11 = Triple-A, 12 = Double-A, 13 = High-A, 14 = Single-A

### 2. U.S. Census Bureau -- American Community Survey (ACS) 5-Year Estimates

**Provider:** U.S. Census Bureau
**API:** `https://api.census.gov/data/{year}/acs/acs5`
**Authentication:** Free API key (https://api.census.gov/data/key_signup.html)
**Authority:** Federal statistical data. The ACS 5-Year is the Census Bureau's
most reliable dataset for small-area estimates, combining five years of survey
responses for statistical stability.

| Variable Code | Metric | Level |
|---|---|---|
| B01003_001E | Total population | Place (city) and MSA |
| B19013_001E | Median household income | Place and MSA |
| B19301_001E | Per capita income | Place and MSA |
| B17001_001E / B17001_002E | Poverty rate (computed) | Place and MSA |

**Geographic matching process:**
1. Each venue's latitude/longitude is reverse-geocoded to Census FIPS codes
   using the `censusgeocode` library (calls the Census Bureau's geocoding service)
2. Counties are mapped to Metropolitan Statistical Areas (MSAs) using the
   official CBSA delineation file from https://www2.census.gov/
3. Demographics are fetched at both the city (Census "place") and metro area
   (MSA/CBSA) level

**Data years:** 2015-2024 ACS 5-Year estimates (collected annually to enable
time-varying demographic features and trend analysis)

### 3. Open-Meteo Historical Weather Archive

**Provider:** Open-Meteo (open-source weather API)
**URL:** `https://archive-api.open-meteo.com/v1/archive`
**Authentication:** None required (free tier)
**Data source:** Open-Meteo aggregates data from national weather services
(NOAA, ECMWF, DWD) and provides it through a standardized API.

| Metric | Unit |
|---|---|
| Temperature (min, max, feels-like) | Fahrenheit |
| Precipitation (total, rain, snow) | Inches |
| Wind (max sustained, gusts, direction) | MPH / degrees |
| WMO weather code | Numeric (0=clear, 61=rain, etc.) |
| Sunrise, sunset, sunshine duration | Timestamps / seconds |

Weather is collected by venue location and game date. Requests are batched by
venue and month to minimize API calls.

---

## Data Coverage

| Dimension | Coverage |
|---|---|
| Teams | All 120+ affiliated MiLB teams across 4 levels |
| Seasons | 2023, 2024, 2025 |
| Games | ~8,280 per season (~24,840 total) |
| Game types | Regular season (R), plus playoffs (F, D, L, W) |
| Venues | ~130 ballparks with coordinates, capacity, and field dimensions |
| Parent organizations | All 30 MLB clubs |
| Promotions | 50,000+ individual promotion records |
| Demographics | ~95% of venues matched to Census place and MSA data |
| Weather | ~99% of completed games with daily weather |
| Transactions | All roster moves across all 30 MLB organizations per season |

---

## Data Quality and Trust

### Collection safeguards

- **Idempotent pipeline:** Every insert uses `ON CONFLICT ... DO UPDATE`,
  meaning the pipeline can be re-run safely without creating duplicates.
- **Referential integrity:** Foreign key constraints enforce that every game
  references a valid team, every promotion references a valid game, etc.
- **Unique constraints:** Composite keys prevent duplicate promotions per game,
  duplicate weather records, and duplicate season attendance rows.
- **Retry logic:** HTTP requests use exponential backoff (3 attempts, up to 30s
  wait) to handle transient API failures.
- **Rate limiting:** Configurable per-source rate limits (MLB API: 2 req/s,
  Open-Meteo: 10 req/s) prevent throttling.

### Data validation

- **Null handling:** Attendance is NULL for rainouts, postponements, and
  suspended games. All analytical queries filter on
  `WHERE attendance IS NOT NULL` to exclude these.
- **Census data cleaning:** Negative values (Census-suppressed data) are
  converted to NULL rather than being stored as misleading numbers.
- **String sanitization:** Invalid values like the literal string "undefined"
  in promotion image URLs are set to NULL before storage.
- **Parameterized queries:** All database queries use parameterized placeholders
  (`:variable` syntax) rather than string interpolation to prevent SQL injection.

### Audit trail

- **Raw JSON storage:** Every table includes a `raw_json` column preserving the
  original API response. This allows any derived field to be verified against
  the source data at any time.
- **Sync log:** The `milb.data_sync_log` table records every collection run with
  start/end timestamps, record counts (fetched vs. upserted), status
  (running/completed/failed), and error messages.
- **Timestamps:** Every row carries `created_at` and `updated_at` timestamps
  for change tracking.

### Known limitations

| Limitation | Impact | How we handle it |
|---|---|---|
| Some games lack attendance figures | ~20-30% of games have NULL attendance | Filtered out of all analytical calculations |
| Promotion descriptions can be sparse | Some teams provide minimal detail | LLM enrichment in progress to standardize into boolean flags |
| Doubleheader attendance reporting | Game 2 sometimes reported as 0 or combined | Doubleheader status flagged; can be excluded from analysis |
| Rehab assignment end dates sometimes missing | Affects window calculations | Fallback to transaction date + 30 days |
| Census data has ~1 year lag | 2023 estimates are latest available | Acceptable for demographic context; refreshed annually |

---

## Analytics Dashboard

The Streamlit dashboard provides ten analytical views, each with interactive
filters for classification level, team operator, individual team, and game type.

| Page | What it shows |
|---|---|
| **Executive Overview** | Platform summary with live KPIs, key findings, data sources, and methodology overview (C-suite friendly with technical details toggle) |
| **Home** | Map of all MiLB teams with color modes for trend %, capacity utilization, average attendance, MSA population, median income, and poverty rate |
| **Attendance** | Season trends, month-over-month patterns, day/night splits, homestand analysis, capacity utilization |
| **Promotions** | Attendance lift by promotion type (fireworks, giveaways, theme nights, etc.), promotion pairing effects, day-of-week patterns |
| **Weather** | Temperature and precipitation correlation with attendance, rain-day impact analysis |
| **Opponents** | Home attendance variation by visiting team, opponent attractiveness ranking |
| **Rehab Assignments** | MLB veteran rehab assignment windows overlaid on attendance timelines, lift calculation during vs. outside rehab windows |
| **Scheduling** | Day-of-week patterns, doubleheader impact, game-count heatmaps, school calendar alignment |
| **Promo Strategy** | Promotional strategy clustering, team profiles, radar charts, cluster comparison |
| **Recommendations** | OLS promo lift, peer comparison, What-If simulator, actionable recommendations, model diagnostics |
| **Team Report** | LLM-generated executive narrative per team with KPIs, goals, risks, and group rollup views (level, cluster, league-wide) |

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| Database | PostgreSQL 13+ (schema: `milb`) |
| ORM | SQLAlchemy 2.0+ |
| HTTP client | httpx 0.27+ with tenacity retry |
| Dashboard | Streamlit 1.35+ |
| Visualization | Plotly 5.22+ |
| Data processing | Pandas 2.2+, SciPy 1.13+ |
| Census integration | census, censusgeocode, us (Python libraries) |
| ML / prediction | XGBoost 2.0+, Optuna, SHAP |
| LLM narratives | Ollama (local, qwen3:8b) |
| Configuration | python-dotenv (.env file) |

---

## Database Schema

The PostgreSQL database uses a `milb` schema with 14 tables organized into
dimension tables (reference data) and fact tables (event data).

**Dimension tables:** sports, leagues, divisions, organizations, venues, teams,
team_operators, venue_demographics

**Fact tables:** games (central fact table), game_promotions, game_weather,
season_attendance, transactions

**Analytics tables:** game_features, promo_lift, team_clusters, cluster_benchmarks,
model_runs, feature_importance, game_predictions, team_recommendations,
team_promo_clusters, team_narratives, group_narratives

**Operational tables:** data_sync_log (ETL audit trail), analysis_runs

Key design decisions:
- **Selective denormalization:** The games table includes team names, venue name,
  and sport_id directly, enabling fast queries without joins for common access
  patterns.
- **Comprehensive indexing:** Composite indexes on common query patterns
  (team + season, date + sport), partial index on non-null attendance, and
  covering indexes that include attendance for index-only scans.
- **JSONB audit columns:** Raw API responses stored alongside parsed columns
  for full traceability.

A complete data dictionary is available in [DATA_DICTIONARY.md](DATA_DICTIONARY.md).

---

## Data Pipeline

The collection pipeline (`scripts/collect_all.py`) runs 8 sequential steps:

1. **Reference data** -- teams, venues, leagues, divisions, organizations
2. **Game schedules** -- all games across all levels and game types
3. **Promotions** -- per-game promotion listings, fetched month by month
4. **Game feeds** -- per-game attendance, scores, duration (~3.5 hours)
5. **Venue details** -- capacity, field dimensions, turf/roof type
6. **Weather** -- Open-Meteo historical daily weather by venue and date
7. **Season attendance** -- official season aggregates from MLB API
8. **Transactions** -- rehab assignments, options, callups

Total runtime is approximately 4-5 hours for a full collection cycle. The
pipeline is designed to be run nightly; the upsert pattern ensures that only
changed or new data is written.

Demographics are collected separately (`scripts/collect_demographics.py`) and
typically refreshed annually when new ACS estimates are released.

---

## Glossary

| Term | Definition |
|---|---|
| ACS 5-Year | American Community Survey 5-Year Estimates -- Census Bureau dataset averaging survey data over 5 years for statistical reliability |
| CBSA | Core Based Statistical Area -- Census Bureau definition of a metro or micro area |
| FIPS | Federal Information Processing Standards codes -- numeric identifiers for states, counties, and places |
| MSA | Metropolitan Statistical Area -- a CBSA with a core urban area of 50,000+ population |
| MiLB | Minor League Baseball -- the system of professional baseball leagues affiliated with MLB |
| Upsert | INSERT ... ON CONFLICT DO UPDATE -- a database operation that inserts new rows or updates existing ones, preventing duplicates |
| Game type R/F/D/L/W | Regular season / Wild Card / Division Series / League Championship / Championship |
| Sport ID | MLB's numeric identifier for classification level (11=AAA, 12=AA, 13=A+, 14=A) |
