# MiLB Attendance & Promotions Analysis Plan

## Objective

Help the Binghamton Rumble Ponies ticketing office (and any MiLB team) understand what drives attendance, which promotions work, and how to schedule them optimally. The analysis applies to all 120 teams but Rumble Ponies (team_id=505) is the primary focus.

---

## Phase 0.5 — LLM Promotion Enrichment (Pre-Analysis)

**Goal:** Before any analysis can happen, the raw promotion data needs meaningful structure. The existing `offer_type` field is almost useless — 58% of all 14,235 promotions are tagged "Day of Game Highlights", a catch-all that means nothing. The name and description fields contain all the real meaning. We use a local Ollama LLM to extract that meaning into structured, queryable columns.

### Why This Matters

Promotions are the core of this project. But "Fireworks Friday", "$2 Tuesdays", "Harry Potter Night", and "Kids Run the Bases" are all labeled identically in the raw data. Without enrichment, Phase 1's promotion effectiveness analysis compares apples to aircraft carriers. Every downstream analysis — which promotion type lifts attendance most, the predictive model in Phase 2, the optimizer in Phase 3 — depends on clean promotion categories.

### 0.1 Proposed Category Taxonomy

Promotions stack (one game can have fireworks + a giveaway + Thirsty Thursday), so categories are **boolean flags, not a single label**. This is more flexible for multi-label games and avoids forcing an arbitrary hierarchy.

| Flag | Captures | Examples from real data |
|------|---------|------------------------|
| `is_fireworks` | Post-game fireworks show | Fireworks Friday, Post-Game Fireworks, MAX Fireworks |
| `is_giveaway_item` | Physical item given to fans | Hat giveaway, bobblehead, magnet schedule, jersey |
| `is_food_deal` | Food or drink discount | $2 hot dogs, Thirsty Thursday beer, Taco Tuesday, Wine Wednesday |
| `is_ticket_deal` | Discounted admission pricing | $2 Tuesday tickets, One-Price Wednesday ($18 all tickets), $5 kids |
| `is_theme_night` | Themed event with costumes/décor | Star Wars Night, Harry Potter Night, 80s Night, Western Night |
| `is_heritage_night` | Cultural/community identity night | Latino night (Llamas de Hickory), Irish Heritage Night |
| `is_kids_event` | Kid-targeted activity | Kids Run the Bases, Kids Eat Free, pregame clinics, kids ticket deal |
| `is_community_event` | Charity, military, education, local org | Military Appreciation, Education Day, food drive, alumni night |
| `is_autographs` | Player/celebrity autograph session | Pre-Game Autograph Session, player meet & greet |
| `is_entertainment` | Non-fireworks post-game show | Drone show, live concert, DJ appearance, comedy night |
| `is_recurring` | Weekly/season-long recurring promo | Thirsty Thursday (every Thursday), Tito's Vodka Happy Half Hour (every game) |
| `is_dog_friendly` | Bark in the Park / dog night | Bark in the Park (161 occurrences — very common) |

**Primary category** (`promo_category` TEXT): A single best-fit label for cases needing one grouping. Values: `fireworks`, `giveaway`, `food_deal`, `ticket_deal`, `theme_night`, `heritage_night`, `kids_event`, `community_event`, `entertainment`, `recurring`, `other`.

### 0.2 Additional Fields the LLM Can Extract

Beyond categories, the LLM can pull structured signal that would otherwise require complex regex:

| Field | Type | Purpose |
|-------|------|---------|
| `giveaway_limit` | INTEGER | "First 1,000 fans" → 1000. NULL if no limit. Drives urgency / early arrivals |
| `target_audience` | TEXT | `kids`, `families`, `adults`, `seniors`, `military`, `students`, `all` |
| `has_celebrity` | BOOLEAN | Cal Ripken Jr. appearance, DJ, special guest |
| `llm_notes` | TEXT | Anything unusual the model flags (e.g., "this is a multi-promo event") |
| `llm_model` | TEXT | Which Ollama model was used (for reproducibility) |
| `llm_enriched_at` | TIMESTAMPTZ | When enriched — allows re-running with a better model |

### 0.3 Storage — New Columns on `game_promotions`

Add these columns directly to `milb.game_promotions` (not a separate table — keep enrichment collocated with the source data for simpler queries).

```sql
ALTER TABLE milb.game_promotions
  ADD COLUMN IF NOT EXISTS promo_category        TEXT,
  ADD COLUMN IF NOT EXISTS is_fireworks          BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_giveaway_item      BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_food_deal          BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_ticket_deal        BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_theme_night        BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_heritage_night     BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_kids_event         BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_community_event    BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_autographs         BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_entertainment      BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_recurring          BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_dog_friendly       BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS has_celebrity         BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS giveaway_limit        INTEGER,
  ADD COLUMN IF NOT EXISTS target_audience       TEXT,
  ADD COLUMN IF NOT EXISTS llm_notes             TEXT,
  ADD COLUMN IF NOT EXISTS llm_model             TEXT,
  ADD COLUMN IF NOT EXISTS llm_enriched_at       TIMESTAMPTZ;
```

Migration script: `scripts/migrate_promo_enrichment.py` — run once before enrichment.

### 0.4 Implementation — `scripts/enrich_promotions.py`

**Approach:** Batch 25 promotions per Ollama call (reduces 14,235 calls to ~570 batches). Ask the LLM to return a JSON array — one classification object per promo.

**Model recommendation:** `qwen3:8b` (5.2 GB, best reasoning/speed tradeoff for classification). Fallback: `llama3.2:latest` (2 GB, faster but less nuanced).

**Prompt strategy:**
- System prompt defines the taxonomy and output schema exactly
- Each batch includes: `promotion_id`, `offer_name`, `offer_type`, `description`
- LLM returns JSON array matching input order
- Parse and upsert results back to DB
- Idempotent: `WHERE llm_enriched_at IS NULL` — safe to re-run with a better model later

**Rate limiting:** Ollama is local — no API limits, but batching still speeds processing significantly. Expect ~1-2 minutes per 100 records on a mid-range GPU.

**Script structure:**
```
scripts/
├── migrate_promo_enrichment.py   # One-time: ALTER TABLE to add columns
└── enrich_promotions.py          # Main enrichment loop — run after collect_all.py
```

### 0.5 What This Unlocks in Downstream Phases

Once enriched, Phase 1 analyses become dramatically more precise:

- **Promotion effectiveness** can now compare `is_fireworks=TRUE AND is_giveaway_item=TRUE` vs fireworks alone vs neither — real stacking analysis
- **Giveaway limit** lets us study whether limited-quantity giveaways (first 1,000) drive more early arrivals than unlimited ones
- **Recurring vs one-off** separates "Thirsty Thursday baseline effect" from the premium lift of a special event
- **Phase 2 model features** get multi-hot encoded flags (12 boolean columns) instead of a useless single `offer_type` string
- **Phase 3 optimizer** can reason about stacking rules ("fireworks already planned — does adding a giveaway item on the same night have diminishing returns?")

### 0.6 Execution Order

```
python scripts/collect_all.py          # already running
python scripts/migrate_promo_enrichment.py   # adds columns to DB
python scripts/enrich_promotions.py          # LLM enrichment (~15-30 min with qwen3:8b)
# → Now ready for Phase 1 analysis
```

---

## Phase 1 — Descriptive Analytics & Dashboarding

**Goal:** Understand the data. Build interactive dashboards that answer "what happened?" and surface patterns.

### 1.1 Tooling (all free/local)

| Tool | Purpose | Why This One |
|------|---------|--------------|
| **Python 3.12** | All analysis code | Already installed |
| **pandas** | Data manipulation, aggregation | Industry standard, fast enough for 25K rows |
| **plotly** | Interactive charts AND maps | One library for everything — scatter maps, bar, scatter, line, all render natively in Streamlit via `st.plotly_chart`. Free OpenStreetMap tiles, no API key. |
| **Streamlit** | Interactive dashboards | Free, Python-native, no server needed, runs locally. Deploys free to Streamlit Cloud if needed |
| **Jupyter Notebooks** | Exploratory analysis, one-offs | Quick iteration, inline charts |
| **SQLAlchemy** | DB access from Python | Already in the project |
| **scipy.stats** | Statistical tests (t-test, chi-square) | Built-in to Python scientific stack |

**Single `requirements.txt`** at project root covers both data collection and dashboard packages.
Run once inside the venv: `pip install -r requirements.txt`

**How to start the dashboard:**
```bash
# From project root, with venv active:
streamlit run streamlit_app/Home.py
```

**Normalization approach:** Use **capacity utilization** (attendance ÷ venue capacity) rather than raw attendance when comparing teams of different market sizes. A team filling 95% of a 4,000-seat ballpark is outperforming one filling 60% of a 12,000-seat park.

### 1.2 Analyses to Build

#### G. Geographic League Overview (Landing Page — build first)

**Goal:** A map of all ~120 MiLB teams as the dashboard entry point. Visually impressive, teaches Streamlit fundamentals, and gives immediate league-wide context before diving into per-team analysis.

- All teams plotted on a US map using venue `latitude`/`longitude` (124 of 127 venues already have coords)
- Bubble **size** = average home attendance for the selected season
- Bubble **color** = attendance trend 2023→2025 (green = growing, red = shrinking, grey = flat)
- **Color-by toggle:** switch between trend %, capacity utilization %, and raw attendance
- **Region presets** in sidebar: Full USA / Northeast / Southeast / Midwest / West (just recenters map)
- **Level filter:** checkboxes for Triple-A, Double-A, High-A, Single-A
- Hover tooltip: team name, venue, city, level, capacity, avg attendance, capacity utilization, trend
- Sortable table below map showing all teams with the same data

**Why first:** uses only data we already have (venues + season_attendance), teaches the core Streamlit pattern (`sidebar → filter → query → chart`), and is a high-impact "wow" page that motivates further development.

**Output:** `streamlit_app/Home.py` — the dashboard landing page

#### A. Baseline Attendance Profile
- Per-team average attendance by season (trend: growing? shrinking?)
- Day-of-week distribution (Sat/Sun vs weekday, which weekday is worst?)
- Month-by-month seasonality curve
- Day vs night split
- Distribution histograms (are there attendance "tiers"?)
- Capacity utilization (attendance / venue capacity)

**Output:** Streamlit page with team selector dropdown, 6-8 charts auto-updating

#### B. Promotion Effectiveness
- Average attendance: games WITH any promotion vs WITHOUT
- Breakdown by enriched category flags (fireworks, giveaway_item, food_deal, theme_night, etc.) — **requires Phase 0.5 complete**
- Promotion stacking: does fireworks + giveaway > fireworks alone? Diminishing returns curve
- Limited vs unlimited giveaways: does "first 1,000 fans" (giveaway_limit IS NOT NULL) drive more attendance than open giveaways?
- Recurring vs one-off: is "Thirsty Thursday" a baseline lift or does it wear off mid-season?
- Top 10 highest-lift promotions (named) vs team's baseline
- Promotion saturation: teams with 50+ promo games/season vs teams with 20. Does more = better?
- "Giveaway fatigue" — plot promotion count per month vs attendance delta from baseline
- Sponsor value: which `presented_by` sponsors appear on highest-attendance games?

**Statistical rigor:** Use t-tests and confidence intervals, not just averages. A "bobblehead lifts attendance by 800" claim needs a p-value.

**Output:** Streamlit page with team filter, promotion type filter, sortable tables, bar charts

#### C. Weather Impact
- Scatter plot: temperature vs attendance (expect inverted-U — too hot or too cold hurts)
- Rain impact: bucket games by precipitation (0, 0-0.1", 0.1-0.5", 0.5"+) and show attendance distribution
- Wind thresholds
- "Misery index": combine heat index + precipitation + wind into one score, plot vs attendance
- Compare MLB API weather (at game time) vs Open-Meteo (daily) — which predicts attendance better?

**Output:** Streamlit page with scatter plots, box plots by weather bucket

#### D. Opponent Effects
- Rank opponents by the average attendance they draw as the away team
- "Rivalry games" — do certain matchups consistently draw more?
- Geographic proximity effect (closer opponents = more visiting fans?)

**Output:** Sortable table of opponents ranked by attendance impact

#### E. Rehab Assignment Impact (new data)
- Flag games where an MLB veteran was on rehab assignment with the home team
- Compare attendance during rehab windows vs the 2 weeks before/after
- Rank by player notoriety (those with earlier MLB debut dates may be more well-known)
- Control for day-of-week and promotions — isolate the "star player" effect

**Output:** Timeline chart showing attendance with rehab windows highlighted

#### F. Multi-Game & Scheduling Effects
- "Honeymoon" effect: do Opening Day and first few home games spike?
- Post-break attendance: does the All-Star break reset demand?
- Consecutive home game fatigue: does game 5 of a 7-game homestand drop off?
- "Promo hangover": does a big Saturday giveaway hurt Sunday's attendance?
- Fireworks Friday → next day attendance: spillover or cannibalization?

**Output:** Line charts showing attendance trends across homestands

### 1.3 Streamlit App Structure

```
streamlit_app/
├── Home.py                      # Landing page: geographic map of all MiLB teams
├── pages/
│   ├── 1_Attendance.py          # Baseline attendance profile (per-team deep dive)
│   ├── 2_Promotions.py          # Promotion effectiveness (requires LLM enrichment)
│   ├── 3_Weather.py             # Weather impact analysis
│   ├── 4_Opponents.py           # Opponent effects & rivalry draws
│   ├── 5_Rehab_Assignments.py   # Rehab assignment attendance impact
│   └── 6_Scheduling.py          # Multi-game & homestand effects
└── utils/
    └── db.py                    # DB engine (cached) + query_df() helper
```

**How Streamlit multipage works:** Streamlit automatically reads `pages/` and builds the sidebar navigation from filenames. `Home.py` is always the entry point. The number prefix (`1_`, `2_`) controls sort order. Underscores become spaces in the nav. That's it — no routing config needed.

**Core Streamlit concepts used across the app:**

| Concept | What it does |
|---|---|
| `st.set_page_config()` | Page title, icon, wide layout |
| `@st.cache_data` | Cache query results so the DB isn't hit on every widget interaction |
| `@st.cache_resource` | Cache the DB engine itself (shared across all reruns) |
| `st.sidebar.*` | Everything in `.sidebar` goes in the left panel |
| `st.columns([1,2,1])` | Divide the page into proportional columns |
| `st.metric()` | KPI card with a value and optional delta arrow |
| `st.plotly_chart()` | Render any Plotly figure, full width |
| `st.dataframe()` | Interactive sortable table |
| `st.selectbox/multiselect/radio/slider` | The widgets that trigger reruns |

### 1.4 Deliverables

1. **Jupyter notebook** — full exploratory analysis with commentary (the "data science narrative")
2. **Streamlit dashboard** — interactive, team-selectable, filterable
3. **PDF/HTML summary report** — key findings for the ticketing office (exported from notebook or generated)

### 1.5 Estimated Effort

- Exploratory notebook: 1-2 sessions
- Streamlit dashboard: 2-3 sessions
- Statistical testing: 1 session
- Total: ~4-6 working sessions

---

## Phase 2 — Predictive Modeling (Outline)

**Goal:** Build a model that predicts attendance for a future game given known factors. Use this to answer "if we add a bobblehead giveaway to Tuesday June 10, how much attendance do we gain?"

### 2.1 Approach

- **Model type:** Start with gradient-boosted trees (XGBoost or LightGBM) — handles mixed numeric/categorical features well, interpretable via SHAP
- **Fallback:** Linear regression with interaction terms if interpretability is paramount
- **Features:**
  - Day of week, month, time of day (day/night)
  - Temperature, precipitation probability, wind
  - Opponent team ID (or opponent historical draw strength)
  - Promotion count, LLM-enriched boolean flags (one-hot): is_fireworks, is_giveaway_item, is_food_deal, is_ticket_deal, is_theme_night, is_kids_event, is_recurring, etc.
  - Giveaway limit (is it a limited-quantity item that drives urgency?)
  - Is it a one-off special event vs a recurring weekly deal?
  - Days since last home game (homestand position)
  - Rehab assignment flag (MLB veteran present?)
  - Season trend (early/mid/late season)
  - Venue capacity (normalization factor)
  - Prior game attendance (lag feature)
  - Is it a weekend?
  - School in session vs summer break

### 2.2 Training / Validation

- Train on 2023-2024, validate on 2025 (temporal split — no data leakage)
- Or: 3-fold cross-validation with time-aware folds
- Metric: MAE (Mean Absolute Error) — "we're off by X fans on average"
- Secondary metric: MAPE — "we're off by X% on average"

### 2.3 Tooling (all free)

| Tool | Purpose |
|------|---------|
| scikit-learn | Baseline models, feature engineering |
| XGBoost or LightGBM | Main model (pip install) |
| SHAP | Feature importance & "what-if" explanations |
| Optuna | Hyperparameter tuning (free) |

### 2.4 Key Outputs

- Feature importance ranking (what matters most for attendance?)
- "What-if" simulator: change one input, see predicted attendance change
- Per-team model accuracy report
- Residual analysis: which games did the model miss badly? (outlier detection)

### 2.5 Known Challenges

- Small sample size per team (~200 home games over 3 years)
- Possible solution: train a league-wide model with team as a feature, then fine-tune per team
- Attendance caps at capacity — need to handle censored data (right-censored at capacity)
- Unobserved confounders: community events, weather forecasts (not just actual weather), local school schedules

---

## Phase 3 — Optimization & Recommendations (Outline)

**Goal:** Given the predictive model, optimize the promotion schedule to maximize total season attendance (or revenue).

### 3.1 Approach

- **Constraint-based optimization:** Given a budget of N giveaway nights, M fireworks nights, etc., which dates maximize total expected attendance?
- **Marginal lift estimation:** For each promotion type, estimate the incremental fans it brings (from Phase 2's what-if analysis)
- **Diminishing returns modeling:** Account for promotion fatigue — the 5th bobblehead night has less lift than the 1st
- **Day-type interaction:** Giveaways on Tuesdays might lift +500 but on Saturdays only +200 (already near capacity)

### 3.2 Optimization Methods

| Method | Complexity | Notes |
|--------|-----------|-------|
| **Greedy heuristic** | Low | Assign highest-lift promo to lowest-baseline date. Simple, fast, 80% of optimal. |
| **Integer linear programming (PuLP)** | Medium | Exact solution with constraints (budget, spacing rules). Free Python library. |
| **Genetic algorithm (DEAP)** | Medium | Good for complex constraints. Free Python library. |
| **Monte Carlo simulation** | Low | Simulate many random schedules, keep the best. Good for uncertainty quantification. |

### 3.3 Constraints to Model

- Budget: max N promotions of each type per season
- Spacing: no two giveaway nights within X days (prevent fatigue)
- Sponsor commitments: certain sponsors require specific dates
- Fireworks: only on Friday/Saturday (noise ordinances)
- Theme nights: must be on specific dates (Star Wars Day = May 4th)

### 3.4 Deliverables

- **Promotion calendar optimizer** — input constraints, output recommended schedule
- **Scenario comparison** — "Option A vs Option B" with expected total attendance for each
- **Revenue estimator** — combine attendance prediction with per-cap spending estimates
- **Presentation-ready report** for front office

### 3.5 Tooling (all free)

| Tool | Purpose |
|------|---------|
| PuLP | Linear programming optimization |
| scipy.optimize | General optimization |
| DEAP | Genetic algorithms (if needed) |
| Streamlit | Interactive "what-if" scenario builder |

---

## What We Might Be Missing

### Data we have but haven't exploited yet
- **Game duration** — do longer games correlate with lower next-game attendance?
- **Score differentials** — does a blowout loss hurt next-game attendance?
- **Doubleheaders** — how does DH attendance compare to single games?

### Data we could add (future collection)
- **Local school calendars** — summer break dates vary by district; a strong attendance predictor
- **Competing local events** — concerts, festivals, other sports (hard to collect systematically)
- **Ticket pricing data** — not available from MLB API; would need team cooperation
- **Social media buzz** — Twitter/X mentions, Facebook event RSVPs (API access varies)
- **Historical roster data** — which players were on the active roster each game (beyond just rehab assignments)
- **Win/loss streaks** — does a winning team draw better? (we have scores, so we can calculate this)
- **Team record at time of game** — another angle on team performance as a draw

### Data we can derive from what we have
- **Win/loss streaks** — calculate from game scores already in DB
- **Homestand position** — game 1 vs game 5 of a homestand (from schedule gaps)
- **Days since last promotion** — "promo cooldown" feature
- **Season progress** — game number / total games (early vs late season)
- **Historical promotion frequency** — how often this team runs promos
