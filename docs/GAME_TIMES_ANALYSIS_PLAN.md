# Game Times Analysis Plan

Status: **Delivered 2026-04-17.** Phase 1 refuted the original camp-level
hypothesis but surfaced a strong within-team scheduling cost. Phase 2 was
reframed and the page shipped.

## Background

User hypothesis: sat_winners run evening Saturday games, sat_losers run early
Saturday games, so sat_losers can't run fireworks. Binghamton in particular.

## Data probe before planning (2026-04-17)

Checked Double-A Saturday day/night mix for the two camps:

| Camp         | Day | Night | % Night |
|--------------|----:|------:|--------:|
| sat_winner   |  14 |   136 |     91% |
| sat_loser    |  13 |   140 |     91% |

**Camp-level hypothesis is NOT supported.** Both camps run ~91% night Saturdays.

**But Binghamton individually is an outlier.** ~43% of RP's Saturday games are
day games (6 day / 8 night in the 2025 probe). The refined hypothesis: *RP is
an individual outlier on Saturday start times, regardless of camp patterns.*
This is more interesting than the original question because it's an actionable
RP-specific finding.

## Data gotchas

- No timezone column on `milb.venues`. Have lat/lon only. Need to derive tz
  for local-time analysis.
- `milb.games.day_night` is unreliable. 22:07 UTC games (6pm ET) labeled as
  `day` were observed. Local-hour-based classification will be the source of
  truth going forward.

## Time buckets

Fixed upfront so every page tells the same story:

| Bucket          | Local hour range |
|-----------------|-----------------:|
| `morning`       | < 11am           |
| `noon`          | 11am - 12:59pm   |
| `matinee`       | 1pm - 3:59pm     |
| `early_evening` | 4pm - 5:59pm     |
| `evening`       | 6pm - 7:59pm     |
| `late`          | 8pm+             |

## Phase 0 - Data foundations

1. Add a `timezone` column (TEXT, IANA tz name) to `milb.venues`.
2. Write `scripts/enrich_venue_timezones.py` using `timezonefinder` (lat/lon
   -> tz name). One-time population.
3. Extend `scripts/build_features.py`:
   - Add `local_start_hour` (smallint) and `start_time_bucket` (text) columns
     to `milb.game_features`.
   - Compute from `games.game_datetime` + `venues.timezone`.
4. Stop relying on `day_night`. Classify from `local_start_hour`:
   buckets <= `matinee` are "day", rest are "night" (only when needed).

## Phase 1 - Hypothesis test (CHECKPOINT)

`scripts/analyze_game_times.py` (console output only, no DB writes at this
phase):

- Sat start-time bucket distribution per camp at each level
- RP's Sat clock vs Double-A winner Sat clock
- Attendance lift per bucket within-team (same team, same DOW, different
  buckets) -- "matinee games cost this team X% vs their evening games"
- Same numbers for Fri, Sun, weekdays

Stop and review with user. Go/no-go on page build.

## Phase 1 verdict (2026-04-17)

- **Camp-level hypothesis refuted.** Across all four levels, sat_winners and
  sat_losers run Saturday evening games at indistinguishable rates (Double-A
  88% vs 94%, High-A 69% vs 75%, Single-A 93% vs 97% -- losers *higher* at
  three levels).
- **RP-specific hypothesis refuted.** The earlier "43% day games" probe was
  built on the unreliable `day_night` flag. True bucket-based reading: RP
  runs 91% Saturday evenings, which matches the Double-A sat_winner average
  of 88%. RP's Saturday problem is not game time -- the fireworks/giveaway
  story from Weekend Playbook still stands.
- **But within-team bucket effects are substantial.** Double-A Saturday
  matinee cap util is -20.8pp vs the team's own average, early_evening is
  -16.6pp. Few games, big cost. This is an individual scheduling finding,
  not a camp-level one.
- **`day_night` is 1.5% systematically wrong** (87 "day"-labeled games are
  actually 6-8pm evening per local start hour). Retired from display on the
  new page; to be migrated on Attendance and Weather pages next.

## Phase 2 - Page `12_Game_Times.py` (SHIPPED, revised scope)

Tab 5 was killed as the "weekend-gap mediator" it cannot be, and replaced
with a per-team Schedule Audit that quantifies seats left on the table by
non-evening games against the team's own evening baseline.

1. **League clock** - DOW x start_time_bucket heatmap, count of games.
   "When does MiLB actually play?" Includes a `day_night` disagreement
   callout.
2. **Scheduling cost** - within-team cap-util residual (subtract each
   team's season average before aggregating) per DOW x bucket. This is the
   headline: concrete penalty per slot. Per-level breakdown in an expander.
3. **Time x temperature interaction** - start_bucket x temp_bucket heatmap
   of avg cap util, plus a matinee-vs-evening point estimate table at each
   temperature band.
4. **Team clock** - selected team's DOW x bucket mix side-by-side with its
   level's average; outlier flags for non-evening slots >10pp above level
   average. Catches RP's Wed noon / Wed matinee habit.
5. **Schedule audit** - for the selected team: every non-evening home game,
   compared against that team's own evening cap-util baseline for the same
   DOW. Deficit x venue capacity = estimated lost seats per game. Season
   total at the top. Uses the team's own baseline, not a model, so the
   number is defensible ("you normally fill 45% on Wednesday evenings; this
   Wednesday matinee filled 22%, that is ~1,900 empty seats").

**No new aggregation table.** The page reads `game_features` directly with
`@st.cache_data(ttl=300)`. 25k rows is fine in pandas -- avoiding the
pre-aggregation table simplifies the surface.

## Phase 3 - Cross-page integration (SHIPPED 2026-04-17)

- **Weekend Playbook Act 2** `Sat day-game %` column: SKIPPED. Phase 1
  showed RP's Saturday bucket mix matches sat_winners (both ~91% evening),
  so this column would be misleading noise at Double-A.
- **Attendance page** (`1_Attendance.py`): "Day vs Night games" subsection
  replaced with "Attendance by start-time bucket" metric row. Query now
  LEFT JOINs game_features for `start_time_bucket`. `day_night` still
  selected for scatter-plot hover but no longer drives any analysis.
- **Weather page** (`3_Weather.py`): Temperature tab gained a
  "Temperature x start-time bucket" heatmap of avg attendance. Weather
  query LEFT JOINs game_features for bucket + capacity. New see_also entry
  to Game Times.
- **Promotions page** (`2_Promotions.py`): `day_night` selected in query
  but never used downstream -- left alone.
- **Training contract gotcha (found the hard way):** adding a new
  categorical column (here: `start_time_bucket`) to `build_features.py`
  MUST be matched in `CAT_COLS` of both `train_attendance_model.py` AND
  `analyze_promo_lift_counterfactual.py`. Otherwise the object-dtype
  column either blows up training, or the old saved models predict with
  mismatched category positions. Symptom: XGBoost raises "Found a category
  not in the training set" at predict time. Fix: add to CAT_COLS in both
  scripts and retrain with `--force`.

Do NOT touch `day_night` in the database schema or in the XGBoost model --
the column stays in the feature table and is used by the trained model as
one of its categorical features. Only change display/analysis surfaces.

## Phase 5 - Recommendation engine wiring (SHIPPED 2026-04-17)

Originally flagged as "not in this plan", done alongside Phase 3 because
the rec engine displays negative OLS lift that the CF work has invalidated.

- `generate_recommendations.py` now reads `milb.promo_lift_cf` as primary.
- Old "Re-evaluate X" path on OLS significant-negative lift: DELETED.
- `promo_roi_recs` uses CF ATE (`mean_lift >= 50`, `pct_positive >= 0.60`).
- `promo_strategy_recs.strategy_mismatch` uses CF ATE (`mean_lift < 25`,
  `pct_positive < 0.55` = near-zero, inconsistent direction).
- New `missing_promo_opportunity_recs` category. For each promo flag,
  flags teams under-indexing vs promo-cluster peers when the CF ATU
  (effect on untreated games) says adding the flag would lift attendance.
  20 recs generated on 2026-04-17 regen.
- Binghamton specifically is NOT flagged: RP's pct_fireworks=0.077 vs
  cluster peer avg 0.097 sits just above the 0.75 under-index gate. RP's
  fireworks gap is Saturday-specific (0% vs 53%) and that's a DOW signal
  that belongs in Weekend Playbook or dow_strategy_recs, not season-level
  emphasis.

## Phase 4 - Nav reorganization (SHIPPED)

`streamlit_app/app.py` created as the grouped-navigation entry point using
`st.navigation`. Sidebar now has five sections:

- **Overview** - Home, Executive Overview
- **Review** - Attendance, Promotions, Weather, Opponents, Rehab Assignments,
  Scheduling, Promo Strategy, Team Report, Competitive Intel
- **Data Stories** - Weekend Playbook, Game Times
- **Prescriptive** - Recommendations
- **Admin** - Admin

Legacy flat nav via `streamlit run streamlit_app/Home.py` still works.
Grouped nav is the new default: `streamlit run streamlit_app/app.py`.

## Phase 4 - Pipeline + housekeeping

- `refresh.bat` gets `enrich_venue_timezones.py` before `build_features.py`
  (it is a no-op after first run due to delta-check).
- `build_features.py` delta check must detect the new columns being missing
  to trigger a rebuild; `--force` if the new cols exist but are NULL.
- `analyze_game_times.py` runs after `build_features.py`.
- Memory update: new page, new tables, bucket definitions, hypothesis
  verdict.

## Scope decisions (locked in)

- **One page, not two.** Five tabs cover the arc. Two pages would fragment
  "game time" from "game time x temperature."
- **`timezonefinder` package** accepted as a dependency. State-abbrev
  fallback is wrong for FL/TN/ID/OR/KY/NE/ND/SD and rejected.
- **Tab 5 mediation stays simple.** Tab 2 coefficient adjustment, not a new
  XGBoost pass. We already have CF lift in `promo_lift_cf`; that's enough
  modeling complexity.
- **Weekend Playbook stays intact.** One new column in Act 2, nothing else
  in that page changes.

## Not in this plan (separate tracks)

- Wiring `promo_lift_cf` into `generate_recommendations.py` to replace the
  OLS-based negative-lift recs, and adding `missing_promo_opportunity` rec
  category. Clean scope but separate PR to keep the game-time story
  uncluttered.

## Open questions (answered at plan approval)

1. One vs two pages? -> One with tabs.
2. `timezonefinder` dependency? -> Accept.
3. Tab 5 mediation complexity? -> Keep simple (Tab 2 coefficients).
4. Weekend Playbook tie-in? -> New column in Act 2's sat_loser table only.
