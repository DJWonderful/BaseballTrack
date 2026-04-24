# MLB Stats API: Data Availability Audit

## Overview

The MLB Stats API (`https://statsapi.mlb.com/api/v1`) is a public, unauthenticated
API maintained by Major League Baseball Advanced Media (MLBAM). It powers MLB.com
and MiLB.com and is the authoritative source for all Minor League Baseball game data.

This document catalogs what data is and isn't available through the API.

---

## Currently Used Endpoints

| Endpoint | Collector | Data Collected | Notes |
|----------|-----------|----------------|-------|
| `/teams?sportId={11-14}&season={year}&hydrate=venue(location)` | `src/collectors/teams.py` | Teams, venues, leagues, divisions, parent orgs | Venue coordinates included via hydration |
| `/schedule?sportId={id}&season={year}` | `src/collectors/schedule.py` | Game schedules for all game types (R, F, D, L, W) | Includes game_pk, date, teams, status |
| `/schedule?hydrate=game(promotions)` | `src/collectors/promotions.py` | Per-game promotion listings | Only available for 2025+ season data |
| `/game/{gamePk}/feed/live` (v1.1) | `src/collectors/game_feed.py` | Attendance, scores, duration, API weather, venue dimensions | ~3.5 hours for full collection |
| `/attendance?teamId={id}&season={year}` | `src/collectors/attendance.py` | Official season totals, averages, highs, lows | Sometimes differs from sum of game feeds |
| `/transactions?teamId={id}&startDate=&endDate=` | `src/collectors/transactions.py` | Rehab assignments, options, callups, DFAs | Uses MLB parent club team IDs |
| `/people/{playerId}` | `src/collectors/transactions.py` | Player MLB debut date, veteran status | Used to classify rehab assignments |

---

## Available But Not Yet Used

| Endpoint | Description | Potential Value | Effort |
|----------|-------------|-----------------|--------|
| `/standings?leagueId={id}&season={year}` | Division/league standings, win/loss records | **High** -- adds playoff race context to late-season attendance. Could explain attendance spikes when teams are in contention. | Medium |
| `/teams/{id}/stats?stats=season&group=hitting,pitching` | Team batting and pitching statistics | **Medium** -- correlate roster quality (team ERA, batting avg) with attendance. "Are fans showing up for good teams?" | Medium |
| `/draft/{year}` | Draft picks, prospect rankings | **Low** -- could measure "prospect hype factor" for affiliate teams. Very niche. | Low |
| `/awards?season={year}&sportId={id}` | League awards (MVP, pitcher of year, etc.) | **Low** -- small sample, limited analytical value. | Low |
| `/sports` | List of all sport types in the API | Already captured via `/teams` query. | N/A |

### Recommended Next Addition: Standings

The `/standings` endpoint would add the most analytical value. Win/loss record and
games-back-from-first could be powerful attendance predictors, especially for
late-season games where playoff contention drives engagement. This data is small
(one row per team per season) and fast to collect.

---

## Confirmed NOT Available

The following data categories are **not exposed** by the MLB Stats API. They would
require separate data partnerships, third-party APIs, or manual collection.

| Data | Why It's Unavailable |
|------|---------------------|
| **Ticket pricing / price tiers** | Controlled by individual team ticketing systems (Ticketmaster, SeatGeek, etc.). No public API. |
| **Ticket inventory / sales volume** | Proprietary business data per team. |
| **Group sales data** | Internal team sales system. |
| **Concession revenue** | Point-of-sale systems, not connected to MLB API. |
| **Merchandise sales** | Separate retail/e-commerce systems. |
| **Marketing / advertising spend** | Internal budget data. |
| **TV ratings / streaming viewership** | Nielsen/media partners, not MLB API. |
| **Social media metrics** | Platform-specific APIs (Twitter/X, Instagram, etc.). |
| **Parking revenue / lot data** | Venue operations, not baseball data. |
| **Sponsorship details** | Contract data, not public. |

### Alternative Approaches for Ticket Pricing

If ticket pricing becomes a priority, potential paths include:

1. **Ticketmaster Discovery API** -- Requires developer account approval. Provides
   event listings with price ranges for events sold through Ticketmaster. Many MiLB
   teams use Ticketmaster as their primary vendor.

2. **Team website scraping** -- Individual team sites sometimes display ticket tiers.
   Fragile, labor-intensive, and may violate terms of service.

3. **Manual survey** -- Collect published ticket prices from team media guides or
   press releases. One-time effort, not automated.

4. **SeatGeek API** -- Some MiLB teams use SeatGeek. Requires partnership application.

None of these are drop-in replacements for the MLB Stats API pattern used in this project.

---

## Hydration Parameters

The MLB Stats API supports a `hydrate` query parameter that nests related data
into the response, reducing the number of API calls needed.

| Hydration | Used In | What It Adds |
|-----------|---------|-------------|
| `venue(location)` | `/teams` | Venue coordinates (lat/lon), city, state |
| `game(promotions)` | `/schedule` | Per-game promotion listings nested in schedule response |
| `team` | Various | Full team details nested in parent response |
| `person` | Various | Player details nested in transactions/roster |

Example: `/schedule?sportId=12&season=2025&hydrate=game(promotions)` returns both
the schedule AND all promotion data in a single call.

---

## Rate Limits and Best Practices

- **Rate limit**: 2 requests/second (self-imposed, configurable in `.env`)
- **No authentication required**: The API is fully public
- **Retry logic**: 3 attempts with exponential backoff (up to 30s wait)
- **Timeout**: 30 seconds per request
- **Batch strategy**: Schedule and promotions are fetched month-by-month to keep
  response sizes manageable
- **API versions**: v1 for most endpoints, v1.1 for `/game/{pk}/feed/live` (richer data)

---

## Data Coverage Summary

| What We Have | Source | Coverage |
|-------------|--------|----------|
| Game schedules, scores, attendance | MLB Stats API | 24,840+ games across 3 seasons |
| Promotions (types, descriptions) | MLB Stats API | 2025 season only (50,000+ records) |
| Demographics (population, income, poverty) | US Census ACS 5-Year | 2015-2024, 95% of venues matched |
| Historical weather | Open-Meteo | 99% of completed games |
| Roster transactions (rehab, options) | MLB Stats API | All 30 MLB orgs per season |
| Venue details (capacity, dimensions) | MLB Stats API | All 130+ ballparks |
