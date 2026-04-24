@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo  MiLB Data Pipeline - Full Refresh
echo ========================================
echo.

REM Flags:
REM   (no flag)      normal delta run, every script uses its own skip-if-unchanged logic
REM   --force        force all phases, INCLUDING MLB API re-collection
REM   --analytics    skip Phase 1; force-rerun Phase 2 + 3 only
set FORCE_COLLECT=
set FORCE_ANALYTICS=
set SKIP_COLLECT=
if "%1"=="--force" (
    set FORCE_COLLECT=--force
    set FORCE_ANALYTICS=--force
)
if "%1"=="--analytics" (
    set FORCE_ANALYTICS=--force
    set SKIP_COLLECT=1
)

REM ── Phase 1: Data Collection ──────────────────────────────────
if defined SKIP_COLLECT (
    echo [Phase 1/3] SKIPPED (--analytics mode)
    echo ----------------------------------------
) else (
    echo [Phase 1/3] Data Collection
    echo ----------------------------------------

    echo [1/3] Collecting teams, schedules, game feeds, weather, attendance, transactions...
    python scripts/collect_all.py %FORCE_COLLECT%
    if errorlevel 1 (
        echo ERROR: collect_all.py failed
        exit /b 1
    )

    echo.
    echo [2/3] Collecting Census demographics...
    python scripts/collect_demographics.py %FORCE_COLLECT%
    if errorlevel 1 (
        echo ERROR: collect_demographics.py failed
        exit /b 1
    )

    echo.
    echo [3/3] Enriching promotions (rules + LLM, requires Ollama for LLM tier)...
    python scripts/enrich_promotions.py %FORCE_COLLECT%
    if errorlevel 1 (
        echo WARNING: enrich_promotions.py failed (is Ollama running?)
    )
)

REM ── Phase 2: Analytics / ML ───────────────────────────────────
echo.
echo [Phase 2/3] Analytics and ML
echo ----------------------------------------

echo [0/9] Enriching venue timezones (no-op if already populated)...
python scripts/enrich_venue_timezones.py
if errorlevel 1 (
    echo WARNING: enrich_venue_timezones.py failed
)

echo.
echo [1/9] Building feature table...
python scripts/build_features.py %FORCE_ANALYTICS%
if errorlevel 1 (
    echo ERROR: build_features.py failed
    exit /b 1
)

echo.
echo [2/9] Building competitive intel (weather profiles, momentum, peer similarity)...
python scripts/build_competitive_intel.py %FORCE_ANALYTICS%
if errorlevel 1 (
    echo ERROR: build_competitive_intel.py failed
    exit /b 1
)

echo.
echo [3/9] Analyzing promo lift (OLS, diagnostic only)...
python scripts/analyze_promo_lift.py %FORCE_ANALYTICS%
if errorlevel 1 (
    echo ERROR: analyze_promo_lift.py failed
    exit /b 1
)

echo.
echo [4/9] Clustering peer groups...
python scripts/cluster_peers.py %FORCE_ANALYTICS%
if errorlevel 1 (
    echo ERROR: cluster_peers.py failed
    exit /b 1
)

echo.
echo [5/9] Clustering promo strategies...
python scripts/cluster_promo_strategy.py %FORCE_ANALYTICS%
if errorlevel 1 (
    echo ERROR: cluster_promo_strategy.py failed
    exit /b 1
)

echo.
echo [6/9] Training attendance models (XGBoost + Optuna)...
python scripts/train_attendance_model.py %FORCE_ANALYTICS%
if errorlevel 1 (
    echo ERROR: train_attendance_model.py failed
    exit /b 1
)

echo.
echo [7/9] Computing counterfactual promo lift (S-learner)...
python scripts/analyze_promo_lift_counterfactual.py %FORCE_ANALYTICS%
if errorlevel 1 (
    echo ERROR: analyze_promo_lift_counterfactual.py failed
    exit /b 1
)

echo.
echo [8/9] Analyzing weekend (Fri/Sat) gap...
python scripts/analyze_weekend_gap.py %FORCE_ANALYTICS%
if errorlevel 1 (
    echo ERROR: analyze_weekend_gap.py failed
    exit /b 1
)

echo.
echo [9/9] Generating recommendations...
python scripts/generate_recommendations.py %FORCE_ANALYTICS%
if errorlevel 1 (
    echo ERROR: generate_recommendations.py failed
    exit /b 1
)

REM ── Phase 3: LLM Narratives ──────────────────────────────────
echo.
echo [Phase 3/3] LLM Narrative Generation (requires Ollama)
echo ----------------------------------------

echo [1/2] Team + group rollup narratives...
python scripts/generate_narratives.py %FORCE_ANALYTICS%
if errorlevel 1 (
    echo WARNING: generate_narratives.py failed (is Ollama running?)
)

echo.
echo [2/2] Competitive-intelligence narrative (Binghamton)...
python scripts/generate_narratives.py --competitive-intel %FORCE_ANALYTICS%
if errorlevel 1 (
    echo WARNING: generate_narratives.py --competitive-intel failed (is Ollama running?)
)

echo.
echo ========================================
echo  Pipeline Complete!
echo ========================================
echo.
echo  Usage:
echo    refresh.bat              -- normal delta run
echo    refresh.bat --force      -- force all, including MLB API re-collect
echo    refresh.bat --analytics  -- skip Phase 1; force Phase 2 + 3 only
echo.

endlocal
