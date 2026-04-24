@echo off
REM psql wrapper: finds psql.exe, reads .env, sets PGPASSWORD, connects.
REM Pass any psql arg you want, e.g.:
REM     scripts\psql.bat                         (interactive shell on the project DB)
REM     scripts\psql.bat -c "\dt milb.*"         (list milb tables)
REM     scripts\psql.bat -f sql\017_add_hypothesis_tables.sql
REM     scripts\psql.bat -d postgres             (override DB for admin tasks)

setlocal EnableDelayedExpansion

REM --- Find psql.exe -----------------------------------------------------------
set "PSQL="
for %%V in (18 17 16 15 14 13 12) do (
    if exist "C:\Program Files\PostgreSQL\%%V\bin\psql.exe" (
        set "PSQL=C:\Program Files\PostgreSQL\%%V\bin\psql.exe"
        goto :found
    )
)
where psql >nul 2>&1 && for /f "delims=" %%P in ('where psql') do set "PSQL=%%P"
:found
if not defined PSQL (
    echo [psql.bat] Could not find psql.exe. Install PostgreSQL or put psql on PATH.
    exit /b 1
)

REM --- Load .env --------------------------------------------------------------
set "ENV_FILE=%~dp0..\.env"
if not exist "%ENV_FILE%" (
    echo [psql.bat] .env not found at %ENV_FILE%
    exit /b 1
)

for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    set "KEY=%%A"
    set "VAL=%%B"
    REM strip leading spaces
    for /f "tokens=* delims= " %%C in ("!KEY!") do set "KEY=%%C"
    if /i "!KEY!"=="DB_HOST"     set "DB_HOST=!VAL!"
    if /i "!KEY!"=="DB_PORT"     set "DB_PORT=!VAL!"
    if /i "!KEY!"=="DB_NAME"     set "DB_NAME=!VAL!"
    if /i "!KEY!"=="DB_USERNAME" set "DB_USERNAME=!VAL!"
    if /i "!KEY!"=="DB_PASSWORD" set "DB_PASSWORD=!VAL!"
)

if not defined DB_HOST     set "DB_HOST=127.0.0.1"
if not defined DB_PORT     set "DB_PORT=5432"
if not defined DB_NAME     set "DB_NAME=baseball"
if not defined DB_USERNAME set "DB_USERNAME=postgres"

REM --- Connect ----------------------------------------------------------------
set "PGPASSWORD=%DB_PASSWORD%"
"%PSQL%" -h %DB_HOST% -p %DB_PORT% -U %DB_USERNAME% -d %DB_NAME% %*
set EXITCODE=%ERRORLEVEL%
set "PGPASSWORD="
exit /b %EXITCODE%
