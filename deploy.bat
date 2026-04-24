@echo off
setlocal
cd /d "%~dp0"

REM ─────────────────────────────────────────────────────────────────────
REM  deploy.bat  --  One-shot refresh + push to Streamlit Cloud.
REM
REM  Usage:
REM    deploy.bat              full refresh, Parquet export, git push
REM    deploy.bat --fast       SKIP Phase 1 + Phase 2 pipeline; only
REM                            regenerate narratives, re-export, push.
REM                            Use this when the local Postgres is already
REM                            up-to-date and you just need to redeploy.
REM    deploy.bat --force      full refresh with --force passed through
REM                            (rebuilds everything even if unchanged).
REM ─────────────────────────────────────────────────────────────────────

set MODE=%1

echo ========================================
echo  Deploy Pipeline  (MiLB Streamlit Cloud)
echo ========================================
echo.

REM ── Step 1: local pipeline (optional) ─────────────────────────────
if "%MODE%"=="--fast" (
    echo [1/4] SKIPPED pipeline -- --fast mode.
    echo       Re-running CI narrative only, in case it was missing.
    echo ----------------------------------------
    python scripts/generate_narratives.py --competitive-intel
    if errorlevel 1 (
        echo WARNING: generate_narratives.py --competitive-intel failed ^(is Ollama running?^)
    )
) else (
    echo [1/4] Running full refresh.bat %MODE%...
    echo ----------------------------------------
    call refresh.bat %MODE%
    if errorlevel 1 (
        echo ERROR: refresh.bat failed -- aborting deploy
        exit /b 1
    )
)

REM ── Step 2: Postgres -> Parquet ──────────────────────────────────
echo.
echo [2/4] Exporting Postgres -^> data/app/*.parquet...
echo ----------------------------------------
python scripts/export_for_app.py
if errorlevel 1 (
    echo ERROR: export_for_app.py failed -- aborting deploy
    exit /b 1
)

REM ── Step 3: commit ───────────────────────────────────────────────
echo.
echo [3/4] Committing data snapshot...
echo ----------------------------------------
git add data/app/
REM Bail out gracefully if nothing changed (diff --cached = 0 exit when changes exist)
git diff --cached --quiet
if not errorlevel 1 (
    echo No Parquet changes -- nothing to commit. Skipping push.
    goto :done
)

for /f "tokens=2 delims==" %%d in ('"wmic os get localdatetime /value | findstr ="') do set LDT=%%d
set TIMESTAMP=%LDT:~0,4%-%LDT:~4,2%-%LDT:~6,2%
git commit -m "refresh data %TIMESTAMP%"
if errorlevel 1 (
    echo ERROR: git commit failed -- aborting deploy
    exit /b 1
)

REM ── Step 4: push ─────────────────────────────────────────────────
echo.
echo [4/4] Pushing to origin...
echo ----------------------------------------
git push
if errorlevel 1 (
    echo ERROR: git push failed. Fix and push manually.
    exit /b 1
)

:done
echo.
echo ========================================
echo  Deploy Complete!
echo ========================================
echo  Streamlit Cloud auto-redeploys in ~30s.
echo.

endlocal
