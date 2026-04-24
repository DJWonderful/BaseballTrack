# Data Dictionary — MiLB Data Pipeline

**Schema:** `milb` (PostgreSQL database: `baseball`)

---

## Relationship Diagram

```
                    milb.sports (4 rows)
                    ├── sport_id PK
                    │
          ┌─────────┼──────────┐
          ▼         ▼          ▼
    milb.leagues  milb.teams  milb.games
    ├── league_id  ├── team_id  ├── game_pk PK
    │              │            ├── home_team_id FK → teams
    ▼              │            ├── away_team_id FK → teams
  milb.divisions   │            ├── venue_id FK → venues
  ├── division_id  │            ├── sport_id FK → sports
                   │            │
                   │            ├──→ milb.game_promotions (0..N)
                   │            ├──→ milb.game_weather (0..1)
                   │            │
                   │            └── milb.season_attendance
                   │                ├── team_id FK → teams
                   │
    milb.organizations              milb.venues
    ├── org_id PK ←── org_id FK     ├── venue_id PK
                                    ├── lat/lon (for weather API)

    milb.transactions
    ├── transaction_id PK
    ├── player_id
    ├── from_team_id, to_team_id
    ├── is_rehab, is_mlb_veteran
    └── type_code (ASG, OPT, CU, etc.)

    milb.data_sync_log (operational — no FKs)
```

---

## Table: `milb.sports`

MiLB classification levels. Seeded with 4 rows.

| Column | Type | Nullable | Example | Description |
|--------|------|----------|---------|-------------|
| sport_id | INTEGER | NO | `12` | MLB Stats API sport ID. PK. |
| sport_name | TEXT | NO | `"Double-A"` | Human-readable level name |
| sport_code | TEXT | YES | `"afa"` | API sport code |
| sort_order | SMALLINT | YES | `2` | Display ordering (1=AAA, 4=A) |
| created_at | TIMESTAMPTZ | NO | | Row creation time |
| updated_at | TIMESTAMPTZ | YES | | Last update time |

**Rows:** 4 (AAA=11, AA=12, A+=13, A=14)

---

## Table: `milb.leagues`

Leagues within each level.

| Column | Type | Nullable | Example | Description |
|--------|------|----------|---------|-------------|
| league_id | INTEGER | NO | `113` | MLB Stats API league ID. PK. |
| league_name | TEXT | NO | `"Eastern League"` | League name |
| sport_id | INTEGER | YES | `12` | FK → sports. Which level |
| raw_json | JSONB | YES | | Raw API response |
| created_at | TIMESTAMPTZ | NO | | Row creation time |
| updated_at | TIMESTAMPTZ | YES | | Last update time |

**Rows:** ~8 (2 leagues per level)

---

## Table: `milb.divisions`

Divisions within leagues.

| Column | Type | Nullable | Example | Description |
|--------|------|----------|---------|-------------|
| division_id | INTEGER | NO | `5820` | MLB Stats API division ID. PK. |
| division_name | TEXT | NO | `"Eastern League Northeast"` | Division name |
| league_id | INTEGER | YES | `113` | FK → leagues |
| raw_json | JSONB | YES | | Raw API response |
| created_at | TIMESTAMPTZ | NO | | Row creation time |
| updated_at | TIMESTAMPTZ | YES | | Last update time |

**Rows:** ~16

---

## Table: `milb.organizations`

MLB parent clubs that own MiLB affiliates.

| Column | Type | Nullable | Example | Description |
|--------|------|----------|---------|-------------|
| org_id | INTEGER | NO | `121` | MLB Stats API parent org ID. PK. |
| org_name | TEXT | NO | `"New York Mets"` | MLB parent team name |
| created_at | TIMESTAMPTZ | NO | | Row creation time |
| updated_at | TIMESTAMPTZ | YES | | Last update time |

**Rows:** 30

---

## Table: `milb.venues`

Physical ballparks with location and dimensions.

| Column | Type | Nullable | Example | Description |
|--------|------|----------|---------|-------------|
| venue_id | INTEGER | NO | `2836` | MLB Stats API venue ID. PK. |
| venue_name | TEXT | NO | `"Mirabito Stadium"` | Current stadium name |
| city | TEXT | YES | `"Binghamton"` | City |
| state | TEXT | YES | `"New York"` | Full state name |
| state_abbrev | TEXT | YES | `"NY"` | Two-letter state abbreviation |
| postal_code | TEXT | YES | `"13901"` | ZIP code |
| country | TEXT | YES | `"US"` | Country code |
| latitude | NUMERIC(10,6) | YES | `42.098650` | Decimal degrees north. Used for Open-Meteo calls. |
| longitude | NUMERIC(10,6) | YES | `-75.968430` | Decimal degrees (negative = west) |
| capacity | INTEGER | YES | `6012` | Seating capacity from game feed fieldInfo |
| turf_type | TEXT | YES | `"Grass"` | "Grass" or "Artificial" |
| roof_type | TEXT | YES | `"Open"` | "Open", "Retractable", or "Dome" |
| left_line | INTEGER | YES | `330` | LF foul line distance (feet) |
| left_center | INTEGER | YES | `371` | Left-center distance |
| center_field | INTEGER | YES | `400` | Center field distance |
| right_center | INTEGER | YES | `371` | Right-center distance |
| right_line | INTEGER | YES | `330` | RF foul line distance |
| raw_json | JSONB | YES | | Merged API JSON (teams + fieldInfo) |
| created_at | TIMESTAMPTZ | NO | | Row creation time |
| updated_at | TIMESTAMPTZ | YES | | Last update time |

**Rows:** ~130

---

## Table: `milb.teams`

One row per MiLB team.

| Column | Type | Nullable | Example | Description |
|--------|------|----------|---------|-------------|
| team_id | INTEGER | NO | `505` | MLB Stats API team ID. PK. |
| team_name | TEXT | NO | `"Binghamton Rumble Ponies"` | Full team name |
| short_name | TEXT | YES | `"Binghamton"` | Short form |
| abbreviation | TEXT | YES | `"BNG"` | 2-3 letter abbreviation |
| location_name | TEXT | YES | `"Binghamton"` | City/location |
| team_code | TEXT | YES | `"bng"` | API team code |
| sport_id | INTEGER | YES | `12` | FK → sports. Current level |
| league_id | INTEGER | YES | `113` | FK → leagues. Current league |
| division_id | INTEGER | YES | `5820` | FK → divisions. Current division |
| org_id | INTEGER | YES | `121` | FK → organizations. Parent MLB club |
| venue_id | INTEGER | YES | `2836` | FK → venues. Home venue |
| raw_json | JSONB | YES | | Raw team API JSON |
| created_at | TIMESTAMPTZ | NO | | Row creation time |
| updated_at | TIMESTAMPTZ | YES | | Last update time |

**Rows:** ~120

---

## Table: `milb.games`

Central fact table. One row per game across all levels and seasons. Denormalized with team/venue names for zero-join analytical queries.

| Column | Type | Nullable | Example | Description |
|--------|------|----------|---------|-------------|
| game_pk | INTEGER | NO | `751189` | MLB Stats API gamePk. Globally unique. PK. |
| game_date | DATE | NO | `2024-07-03` | Official game date |
| game_datetime | TIMESTAMPTZ | YES | `2024-07-03T23:05:00Z` | Full ISO timestamp |
| season | SMALLINT | NO | `2024` | Year (2023, 2024, 2025) |
| game_type | TEXT | NO | `"R"` | "R" = Regular Season |
| day_night | TEXT | YES | `"night"` | "day" or "night" |
| doubleheader | TEXT | YES | `"N"` | "Y", "N", or "S" (split) |
| game_number | SMALLINT | YES | `1` | 1 or 2 for doubleheaders |
| scheduled_innings | SMALLINT | YES | `9` | 7 for some DH games |
| status_code | TEXT | YES | `"F"` | Coded game state |
| status_detail | TEXT | YES | `"Final"` | "Final", "Postponed", "Suspended" |
| abstract_game_state | TEXT | YES | `"Final"` | High-level state |
| home_team_id | INTEGER | NO | `505` | FK → teams. Home team |
| home_team_name | TEXT | YES | `"Binghamton Rumble Ponies"` | Denormalized for queries |
| away_team_id | INTEGER | NO | `549` | FK → teams. Away team |
| away_team_name | TEXT | YES | `"Portland Sea Dogs"` | Denormalized for queries |
| home_score | SMALLINT | YES | `5` | Final score (NULL if unplayed) |
| away_score | SMALLINT | YES | `3` | Final score |
| venue_id | INTEGER | YES | `2836` | FK → venues |
| venue_name | TEXT | YES | `"Mirabito Stadium"` | Denormalized for queries |
| series_description | TEXT | YES | `"Regular Season"` | Series type |
| attendance | INTEGER | YES | `5448` | Reported attendance. NULL if not reported. |
| game_duration_minutes | INTEGER | YES | `179` | Total game time in minutes |
| first_pitch | TIMESTAMPTZ | YES | `2024-07-03T23:07:00Z` | Actual first pitch time |
| weather_condition | TEXT | YES | `"Clear"` | MLB API weather at game time |
| weather_temp_f | SMALLINT | YES | `82` | Temperature from API |
| weather_wind | TEXT | YES | `"6 mph, Out To CF"` | Wind string from API |
| sport_id | INTEGER | YES | `12` | FK → sports. Denormalized level |
| raw_json | JSONB | YES | | Merged schedule + game feed JSON |
| created_at | TIMESTAMPTZ | NO | | Row creation time |
| updated_at | TIMESTAMPTZ | YES | | Last update time |

**Rows:** ~24,840 (3 seasons x ~8,280 games/season)

---

## Table: `milb.game_promotions`

Per-game promotions. One game can have 0 to many promotions.

| Column | Type | Nullable | Example | Description |
|--------|------|----------|---------|-------------|
| promotion_id | BIGSERIAL | NO | `1` | Auto-increment PK |
| game_pk | INTEGER | NO | `751189` | FK → games. Which game |
| offer_id | INTEGER | YES | `7396068` | MLB API offer ID |
| offer_name | TEXT | YES | `"Fireworks Night"` | Promotion title |
| offer_type | TEXT | YES | `"Giveaway"` | Category: "Giveaway", "Theme Days", "Day of Game Highlights", "Ticket Offer" |
| description | TEXT | YES | `"Post-game fireworks show"` | Full description (often missing) |
| distribution | TEXT | YES | `"First 1,750 Fans"` | Who gets it |
| presented_by | TEXT | YES | `"Visions FCU"` | Sponsor name |
| image_url | TEXT | YES | `"https://img.mlb..."` | Promotion image URL |
| thumbnail_url | TEXT | YES | `"https://img.mlb..."` | Thumbnail URL |
| display_order | SMALLINT | YES | `1` | Display ordering within game |
| raw_json | JSONB | YES | | Individual promotion JSON |
| created_at | TIMESTAMPTZ | NO | | Row creation time |
| updated_at | TIMESTAMPTZ | YES | | Last update time |

**Unique Constraint:** `(game_pk, offer_id)` — prevents duplicates on re-ingest
**Rows:** ~50,000+ estimated

---

## Table: `milb.game_weather`

Daily weather from Open-Meteo, keyed per game. Separate from games table because it's from a different source and has many columns.

| Column | Type | Nullable | Example | Description |
|--------|------|----------|---------|-------------|
| weather_id | BIGSERIAL | NO | `1` | Auto-increment PK |
| game_pk | INTEGER | NO | `751189` | FK → games. One weather row per game |
| venue_id | INTEGER | YES | `2836` | FK → venues. For direct venue-weather queries |
| weather_date | DATE | NO | `2024-07-03` | Weather observation date |
| temperature_max_f | NUMERIC(5,1) | YES | `85.2` | Daily high (Fahrenheit) |
| temperature_min_f | NUMERIC(5,1) | YES | `62.8` | Daily low (Fahrenheit) |
| apparent_temperature_max_f | NUMERIC(5,1) | YES | `89.5` | "Feels like" high |
| apparent_temperature_min_f | NUMERIC(5,1) | YES | `60.1` | "Feels like" low |
| precipitation_sum_in | NUMERIC(6,3) | YES | `0.120` | Total precipitation (inches) |
| rain_sum_in | NUMERIC(6,3) | YES | `0.120` | Rain only (inches) |
| snowfall_sum_in | NUMERIC(6,3) | YES | `0.000` | Snowfall (inches) |
| precipitation_hours | NUMERIC(4,1) | YES | `2.0` | Hours with precipitation |
| windspeed_max_mph | NUMERIC(5,1) | YES | `12.3` | Max sustained wind (mph) |
| windgusts_max_mph | NUMERIC(5,1) | YES | `22.1` | Max gust (mph) |
| winddirection_dominant_deg | SMALLINT | YES | `225` | Dominant direction 0-360 degrees |
| weathercode | SMALLINT | YES | `1` | WMO code (0=clear, 61=rain, etc.) |
| sunrise | TIMESTAMPTZ | YES | `2024-07-03T09:32:00Z` | Sunrise time |
| sunset | TIMESTAMPTZ | YES | `2024-07-04T00:45:00Z` | Sunset time |
| sunshine_duration_sec | NUMERIC(8,1) | YES | `48200.5` | Seconds of sunshine |
| raw_json | JSONB | YES | | Full Open-Meteo daily response |
| created_at | TIMESTAMPTZ | NO | | Row creation time |
| updated_at | TIMESTAMPTZ | YES | | Last update time |

**Unique Constraint:** `(game_pk)` — one weather record per game
**Rows:** ~24,840

---

## Table: `milb.season_attendance`

Pre-aggregated season attendance from MLB attendance endpoint. For validation and reporting.

| Column | Type | Nullable | Example | Description |
|--------|------|----------|---------|-------------|
| season_attendance_id | BIGSERIAL | NO | `1` | Auto-increment PK |
| team_id | INTEGER | NO | `505` | FK → teams |
| season | SMALLINT | NO | `2024` | Year |
| game_type_id | TEXT | YES | `"R"` | Game type |
| openings_total | INTEGER | YES | `135` | Total games with gates open |
| openings_total_home | INTEGER | YES | `68` | Home openings |
| openings_total_away | INTEGER | YES | `67` | Away openings |
| games_total | INTEGER | YES | `138` | Total games played |
| games_home_total | INTEGER | YES | `69` | Home games |
| games_away_total | INTEGER | YES | `69` | Away games |
| attendance_total | INTEGER | YES | `278543` | Total attendance (home+away) |
| attendance_total_home | INTEGER | YES | `146061` | Total home attendance |
| attendance_total_away | INTEGER | YES | `132482` | Total away attendance |
| attendance_avg_home | INTEGER | YES | `2318` | Average home attendance |
| attendance_avg_away | INTEGER | YES | `1977` | Average away attendance |
| attendance_avg_ytd | INTEGER | YES | `2148` | Year-to-date average |
| attendance_opening_avg | INTEGER | YES | `2063` | Opening day average |
| attendance_high | INTEGER | YES | `5448` | Highest single-game |
| attendance_high_date | DATE | YES | `2024-07-03` | Date of highest |
| attendance_high_game_pk | INTEGER | YES | `751189` | Game PK of highest |
| attendance_low | INTEGER | YES | `993` | Lowest single-game |
| attendance_low_date | DATE | YES | `2024-06-28` | Date of lowest |
| attendance_low_game_pk | INTEGER | YES | `751156` | Game PK of lowest |
| raw_json | JSONB | YES | | Full attendance API response |
| created_at | TIMESTAMPTZ | NO | | Row creation time |
| updated_at | TIMESTAMPTZ | YES | | Last update time |

**Unique Constraint:** `(team_id, season, game_type_id)`
**Rows:** ~360 (120 teams x 3 seasons)

---

## Table: `milb.transactions`

MLB roster transactions including rehab assignments, options, callups, and designations. Used to flag games where an MLB veteran was on rehab assignment — a known confounding factor for attendance spikes.

| Column | Type | Nullable | Example | Description |
|--------|------|----------|---------|-------------|
| transaction_id | BIGSERIAL | NO | `1` | Auto-increment PK |
| mlb_transaction_id | INTEGER | YES | `609234` | API transaction ID (not always unique across seasons) |
| transaction_date | DATE | NO | `2024-06-15` | Date of the transaction |
| effective_date | DATE | YES | `2024-06-15` | When the move takes effect |
| resolution_date | DATE | YES | `2024-06-30` | When the move ends (rehab return, etc.) |
| player_id | INTEGER | NO | `660271` | MLB Stats API person.id |
| player_name | TEXT | NO | `"Pete Alonso"` | person.fullName |
| player_position | TEXT | YES | `"1B"` | Parsed from description (LHP, SS, C, etc.) |
| mlb_debut_date | DATE | YES | `2019-06-14` | NULL = never debuted in MLB |
| is_mlb_veteran | BOOLEAN | NO | `TRUE` | TRUE if player has mlbDebutDate |
| from_team_id | INTEGER | YES | `121` | Team ID of origin |
| from_team_name | TEXT | YES | `"New York Mets"` | Origin team name |
| to_team_id | INTEGER | YES | `505` | Team ID of destination |
| to_team_name | TEXT | YES | `"Binghamton Rumble Ponies"` | Destination team name |
| type_code | TEXT | NO | `"ASG"` | Transaction type: ASG, OPT, CU, DES, OUT, SE |
| type_desc | TEXT | YES | `"Assigned"` | Human-readable type |
| is_rehab | BOOLEAN | NO | `TRUE` | TRUE if description contains "rehab" |
| description | TEXT | YES | `"New York Mets sent 1B Pete Alonso on a rehab assignment to Binghamton Rumble Ponies."` | Full natural-language description |
| raw_json | JSONB | YES | | Full API transaction JSON |
| created_at | TIMESTAMPTZ | NO | | Row creation time |
| updated_at | TIMESTAMPTZ | YES | | Last update time |

**Unique Constraint:** `(mlb_transaction_id, player_id, transaction_date, type_code)` — prevents duplicates
**Partial Indexes:** `is_rehab = TRUE`, `is_mlb_veteran = TRUE`, `(to_team_id, transaction_date) WHERE is_rehab`
**Rows:** Estimated ~5,000-15,000 across 3 seasons

### Transaction Type Codes

| Code | Meaning | Example |
|------|---------|---------|
| ASG | Assigned | Rehab assignment, minor league assignment |
| OPT | Optioned | Sent down from MLB roster to MiLB |
| CU | Called Up | Promoted from MiLB to MLB roster |
| DES | Designated | Designated for assignment |
| OUT | Outrighted | Outrighted off 40-man roster |
| SE | Selected | Contract selected (added to 40-man) |

---

## Table: `milb.data_sync_log`

Tracks ETL pipeline runs for idempotency, debugging, and monitoring.

| Column | Type | Nullable | Example | Description |
|--------|------|----------|---------|-------------|
| sync_id | BIGSERIAL | NO | `1` | Auto-increment PK |
| source | TEXT | NO | `"game_feed"` | Data source name |
| sport_id | INTEGER | YES | `12` | Which level (NULL for cross-level) |
| season | SMALLINT | YES | `2024` | Which season |
| sync_started_at | TIMESTAMPTZ | NO | `2026-04-14T12:00:00Z` | Start time |
| sync_ended_at | TIMESTAMPTZ | YES | `2026-04-14T15:30:00Z` | End time |
| status | TEXT | NO | `"completed"` | "running", "completed", "failed" |
| records_fetched | INTEGER | YES | `8280` | Records from API |
| records_upserted | INTEGER | YES | `8280` | Records written to DB |
| error_message | TEXT | YES | | Error details if failed |
| parameters | JSONB | YES | `{"teamId": 505}` | API call params |
| created_at | TIMESTAMPTZ | NO | | Row creation time |
| updated_at | TIMESTAMPTZ | YES | | Last update time |

---

## Sample Analytical Queries

### Attendance by team by season (zero joins)
```sql
SELECT home_team_name, season, AVG(attendance)::int AS avg_att, COUNT(*) AS games
FROM milb.games
WHERE attendance IS NOT NULL
GROUP BY home_team_name, season
ORDER BY season, avg_att DESC;
```

### Attendance by promotion type (one join)
```sql
SELECT p.offer_type, AVG(g.attendance)::int AS avg_att, COUNT(DISTINCT g.game_pk) AS games
FROM milb.games g
JOIN milb.game_promotions p ON g.game_pk = p.game_pk
WHERE g.attendance IS NOT NULL
GROUP BY p.offer_type
ORDER BY avg_att DESC;
```

### Attendance vs. weather (one join)
```sql
SELECT g.home_team_name,
       w.temperature_max_f,
       w.precipitation_sum_in,
       g.attendance
FROM milb.games g
JOIN milb.game_weather w ON g.game_pk = w.game_pk
WHERE g.attendance IS NOT NULL
ORDER BY w.precipitation_sum_in DESC;
```

### Games with giveaways vs. without
```sql
SELECT
    CASE WHEN giveaway_count > 0 THEN 'Has Giveaway' ELSE 'No Giveaway' END AS promo_status,
    AVG(attendance)::int AS avg_att,
    COUNT(*) AS games
FROM (
    SELECT g.game_pk, g.attendance,
           COUNT(p.promotion_id) FILTER (WHERE p.offer_type = 'Giveaway') AS giveaway_count
    FROM milb.games g
    LEFT JOIN milb.game_promotions p ON g.game_pk = p.game_pk
    WHERE g.attendance IS NOT NULL
    GROUP BY g.game_pk, g.attendance
) sub
GROUP BY promo_status;
```

### Day vs. night attendance
```sql
SELECT day_night, season, AVG(attendance)::int AS avg_att, COUNT(*) AS games
FROM milb.games
WHERE attendance IS NOT NULL AND day_night IS NOT NULL
GROUP BY day_night, season
ORDER BY season, day_night;
```

### Weekend vs. weekday attendance
```sql
SELECT
    CASE WHEN EXTRACT(DOW FROM game_date) IN (0, 6) THEN 'Weekend' ELSE 'Weekday' END AS day_type,
    AVG(attendance)::int AS avg_att,
    COUNT(*) AS games
FROM milb.games
WHERE attendance IS NOT NULL
GROUP BY day_type;
```

### Rehab assignments to a team
```sql
SELECT t.player_name, t.player_position, t.transaction_date, t.resolution_date,
       t.from_team_name, t.description
FROM milb.transactions t
WHERE t.to_team_id = 505 AND t.is_rehab = TRUE
ORDER BY t.transaction_date DESC;
```

### Attendance spike around rehab assignments
```sql
SELECT g.game_date, g.attendance, t.player_name, t.player_position
FROM milb.games g
LEFT JOIN milb.transactions t
    ON g.home_team_id = t.to_team_id
    AND t.is_rehab = TRUE
    AND g.game_date BETWEEN t.transaction_date AND COALESCE(t.resolution_date, t.transaction_date + 30)
WHERE g.home_team_id = 505 AND g.attendance IS NOT NULL
ORDER BY g.game_date;
```
