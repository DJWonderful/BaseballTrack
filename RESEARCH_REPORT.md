# MiLB Data Pipeline — Research Report

**Date:** 2026-04-14
**Objective:** Identify reliable, repeatable data sources for MiLB game-level attendance, promotions, and schedules.

---

## 1. Data Sources

### Tier 1 — High Reliability (Programmatic, Scalable)

| Source | Data Available | Format | Coverage | Extraction Difficulty | Cost |
|--------|---------------|--------|----------|----------------------|------|
| **MLB Stats API** (`statsapi.mlb.com`) | Schedules, per-game attendance, season attendance aggregates, promotions, standings, box scores | JSON | All 120+ affiliated MiLB teams, all levels (AAA/AA/A+/A) | **Low** — clean REST API, no auth | Free |
| **The Baseball Cube** (`thebaseballcube.com`) | Per-game attendance (game logs), season attendance totals, historical data since 1978 | HTML tables + CSV export | All affiliated MiLB teams | **Low-Moderate** — CSV export available | Free |

### Tier 2 — Medium Reliability (Useful for Cross-Referencing)

| Source | Data Available | Format | Coverage | Extraction Difficulty | Cost |
|--------|---------------|--------|----------|----------------------|------|
| **Baseball Reference** (`baseball-reference.com/register/`) | Season-total attendance, team/player stats | HTML tables | All MiLB since 1886 | Moderate — standard HTML scraping | Free |
| **Ballpark Digest** (`ballparkdigest.com/attendance/`) | End-of-season attendance rankings with year-over-year changes | HTML tables in blog posts | All MiLB, annual reports | Moderate — URL patterns change yearly | Free |
| **Number Tamer** (`numbertamer.com/minor-league-baseball`) | Comprehensive annual attendance analysis (326-page reports) | PDF + Excel | All affiliated + independent, 2009–present | High (PDF) / Low (Excel) | Free |
| **milb-data-repository** (GitHub: `armstjc/milb-data-repository`) | Schedules, player stats, game-level stats | CSV via GitHub Releases | All levels | Low — direct CSV download | Free |

### Tier 3 — Low Reliability / Supplementary

| Source | Data Available | Format | Coverage | Extraction Difficulty | Cost |
|--------|---------------|--------|----------|----------------------|------|
| **MiLB.com team pages** | Schedules, promotional calendars, news articles | JavaScript SPA (React) | All affiliated teams | **High** — requires headless browser | Free |
| **OurSports Central** (`oursportscentral.com`) | Press releases with promotional schedules | HTML articles | Some teams | High — unstructured prose | Free |
| **StatsCrew** (`statscrew.com`) | Season-level attendance totals | HTML | Historical | Moderate | Free |
| **Social media** (@MiLBPromos, team accounts) | Promotional highlights, announcements | Unstructured posts | Curated/selective | Very High | Free |

### Not Useful for MiLB

| Source | Why |
|--------|-----|
| ESPN | Only tracks MLB attendance — no MiLB data |
| FanGraphs | Player performance stats only — no attendance |
| Retrosheet | MLB only — no minor league coverage |
| Sportradar / SportsDataIO | Paid APIs, no MiLB attendance data |
| Ticketmaster / SeatGeek | No promotional details for MiLB games |

---

## 2. MLB Stats API — Deep Dive (Primary Source)

The MLB Stats API is the single most important finding. It is the production API powering both MLB.com and MiLB.com, is **free, unauthenticated, and public**.

### Key Parameters

| MiLB Level | `sportId` |
|------------|-----------|
| Triple-A | 11 |
| Double-A | 12 |
| High-A | 13 |
| Single-A | 14 |
| Rookie | 16 |

### Essential Endpoints

**Teams (discover team IDs):**
```
https://statsapi.mlb.com/api/v1/teams?sportId=12&season=2026
```

**Schedule (discover gamePk values):**
```
https://statsapi.mlb.com/api/v1/schedule?sportId=12&teamId=505&startDate=2026-06-01&endDate=2026-06-30
```

**Per-Game Attendance (via live game feed):**
```
https://statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live?fields=gameData,gameInfo,attendance
```
Returns: `{"gameData":{"gameInfo":{"attendance":5448}}}`

**Season Attendance Aggregates:**
```
https://statsapi.mlb.com/api/v1/attendance?teamId=505&season=2024
```
Returns: total home/away attendance, averages, season high/low with dates and gamePks.

**Promotions (per-game):**
```
https://statsapi.mlb.com/api/v1/schedule?sportId=12&date=2026-06-20&hydrate=game(promotions)
```
Returns per-game promotion objects with:
- `name` — promotion title
- `offerType` — "Giveaway", "Theme Days", "Day of Game Highlights", "Ticket Offer"
- `description` — detailed text (sometimes missing)
- `distribution` — "First 1,750 Fans", "Kids ages 3-12", etc.
- `presentedBy` — sponsor name
- `imageUrl` — promotion image

### Binghamton Rumble Ponies Reference IDs
- **Team ID:** 505
- **League:** Eastern League (ID: 113)
- **Sport/Level:** Double-A (sportId: 12)

### Known Existing Libraries

| Library | Language | GitHub |
|---------|----------|--------|
| MLB-StatsAPI | Python | `toddrob99/MLB-StatsAPI` (779 stars) |
| baseballr | R | `billpetti/baseballr` |
| python-mlb-statsapi | Python | `zero-sum-seattle/python-mlb-statsapi` |

---

## 3. Patterns Across Teams

### Website Structure
- **All affiliated MiLB teams share the same website template** under `milb.com/{team-slug}/`
- Common URL patterns: `/schedule/`, `/tickets/promotions`, `/stats`, `/scores`
- All are React SPAs pulling data from the same MLB Stats API
- This means the API is the universal data layer — scraping individual sites is unnecessary

### Attendance Reporting
- **Season aggregates** are consistently available via the `/attendance` API endpoint for all teams
- **Per-game attendance** is embedded in the live game feed (`/game/{gamePk}/feed/live`) — requires two API calls (schedule → game feed)
- Attendance is reported for completed games; some games may have `null` values (rainouts, suspended games)
- The Baseball Cube provides a simpler alternative: one HTML page per team per season with per-game attendance + CSV export

### Promotional Data
- **The API is the only structured source** for promotions across all teams
- `hydrate=game(promotions)` returns data for ~85-95% of games tested
- Data richness varies by team — some provide full descriptions, sponsors, and images; others provide only a name and type
- No third-party aggregator exists for MiLB promotions — this is a market gap

### Best Data Availability (Teams with Rich Promotional Data)
Based on API testing, larger-market and well-resourced teams tend to populate more promotional fields. Teams in Triple-A and Double-A generally have better data than lower levels.

---

## 4. Data Gaps / Challenges

### What is NOT reliably available:

| Gap | Details |
|-----|---------|
| **Weather data** | Not included in MiLB game feeds (MLB feeds sometimes include weather) |
| **Ticket pricing** | Not available via the Stats API; would require scraping ticketing platforms |
| **Real-time social media engagement** | Platform APIs are restricted/paid; data is unstructured |
| **Independent league data** | The Stats API covers affiliated MiLB only; independent leagues (Atlantic League, Frontier League, etc.) have inconsistent data |
| **Historical promotions** | The API serves current/upcoming promotions; historical promotional data is not archived in a structured way |
| **Promotion attendance correlation** | No source directly links a specific promotion to its attendance impact |
| **Some per-game attendance** | Occasional `null` values for rainouts, suspended games, or very early-season games |
| **Promotion descriptions** | Many teams only provide a promotion name without a description, distribution info, or sponsor |
| **Image URLs** | Many promotion `imageUrl` values are the literal string `"undefined"` instead of null |

### Data Quality Issues:
- Promotional `imageUrl` field often contains the string `"undefined"` — requires filtering
- Not all teams populate `distribution`, `description`, or `presentedBy` fields
- Attendance for doubleheader game-2 may be reported as `0` or `null` (combined with game-1)

---

## 5. Recommended Strategy

### Primary Source: MLB Stats API
Use the Stats API for **all three data types** (attendance, promotions, schedules). It is free, unauthenticated, covers all 120+ affiliated teams, and returns clean JSON.

### Fallback/Verification Source: The Baseball Cube
Use The Baseball Cube for **per-game attendance cross-referencing** and as a backup if the API has gaps. CSV export makes this trivial.

### Reference Source: Number Tamer
Use Number Tamer's Excel downloads for **historical attendance benchmarks** and year-over-year analysis.

### Recommended Implementation Order:

**Phase 1 — Start Small (Proof of Concept)**
- Focus on **Binghamton Rumble Ponies** (teamId: 505) as the case study
- Pull 2024 + 2025 schedules, per-game attendance, and promotions via the API
- Cross-reference attendance with The Baseball Cube game logs
- Build the data model and pipeline for one team

**Phase 2 — Scale to Eastern League**
- Expand to all **Eastern League** teams (leagueId: 113, Double-A)
- This is Binghamton's league, so comparisons are meaningful
- ~6 teams, same level, same schedule structure

**Phase 3 — Scale to All Double-A**
- Add Southern League and Texas League (all Double-A)
- ~30 teams total

**Phase 4 — Scale to All MiLB**
- Add Triple-A, High-A, Single-A
- ~120 teams total

### Why This Order:
1. **Binghamton first** — matches the stated interest and provides a focused test case
2. **Eastern League second** — enables peer comparison within the same league
3. **Double-A third** — same competition level means attendance patterns are comparable
4. **All MiLB last** — full scale, but different levels have very different attendance profiles

### Minimum Viable Dataset:
For **one team, one season**, the API can deliver:
- ~70 home games with dates, opponents, and scores
- Per-game attendance for each home game
- Per-game promotions (name, type, description when available)
- Season attendance aggregates (total, average, high, low)

This is achievable with ~75 API calls (1 schedule call + ~70 game feed calls + 1 attendance aggregate + a few team/league lookups) — easily executable in minutes.

### Data Collection Pipeline (High Level):

```
1. GET /teams?sportId=12         → list of team IDs
2. GET /schedule?teamId=X        → list of gamePks for the season
3. For each gamePk:
   GET /game/{gamePk}/feed/live  → attendance, game info
   (promotions already in schedule if hydrated)
4. GET /attendance?teamId=X      → season aggregates for validation
5. Store in database (per-game rows with attendance + promotions)
```

### API Call Budget Estimate (Full Season, One Team):
| Step | Calls | Notes |
|------|-------|-------|
| Team/league lookup | 2-3 | One-time |
| Schedule (full season) | 1-2 | Can request full date range |
| Schedule with promotions | 1-2 | Same call with `hydrate=game(promotions)` |
| Per-game attendance | ~70 | One per home game |
| Attendance aggregate | 1 | Validation |
| **Total** | **~75-80** | Per team per season |

For all 120 MiLB teams: ~9,000-10,000 API calls per season. Very manageable even without parallelism.

---

## END
