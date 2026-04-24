"""Run a .sql file (or all pending migrations) against the project DB.

This is the psql-free way to run migrations. Reads DB credentials from .env,
executes the SQL through SQLAlchemy, prints what it did. No PATH fiddling,
no password prompts.

Typical uses:
    python scripts/run_sql.py sql/017_add_hypothesis_tables.sql
    python scripts/run_sql.py --all                  # runs every sql/*.sql in order
    python scripts/run_sql.py --pending              # only files not yet recorded
    python scripts/run_sql.py -c "SELECT count(*) FROM milb.games"
    python scripts/run_sql.py --dry-run sql/017_add_hypothesis_tables.sql

Notes:
    - Multi-statement files are executed as a single transaction (psycopg2
      auto-splits on `;`). On error, NOTHING is committed.
    - `--all` / `--pending` track applied files in `milb.schema_migrations`
      (auto-created on first use). `--all` replays everything idempotently;
      `--pending` skips files already applied.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

from sqlalchemy import text
from rich.console import Console
from rich.table import Table

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from src.db.connection import engine

console = Console()
SQL_DIR = project_root / "sql"

MIGRATIONS_TABLE = """
CREATE SCHEMA IF NOT EXISTS milb;
CREATE TABLE IF NOT EXISTS milb.schema_migrations (
    filename    TEXT PRIMARY KEY,
    sha256      TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_ms INTEGER
);
"""


def ensure_migrations_table() -> None:
    with engine.begin() as conn:
        conn.execute(text(MIGRATIONS_TABLE))


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def already_applied(filename: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT sha256 FROM milb.schema_migrations WHERE filename = :f"),
            {"f": filename},
        ).fetchone()
    return row[0] if row else None


def record_applied(filename: str, sha: str, duration_ms: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO milb.schema_migrations (filename, sha256, duration_ms)
            VALUES (:f, :h, :d)
            ON CONFLICT (filename) DO UPDATE
               SET sha256 = EXCLUDED.sha256,
                   applied_at = NOW(),
                   duration_ms = EXCLUDED.duration_ms
        """), {"f": filename, "h": sha, "d": duration_ms})


def run_sql_file(path: Path, dry_run: bool = False) -> tuple[bool, int]:
    """Execute a SQL file. Returns (success, duration_ms)."""
    if not path.exists():
        console.print(f"[red]File not found:[/red] {path}")
        return (False, 0)

    sql = path.read_text(encoding="utf-8")
    if dry_run:
        console.print(f"[cyan]--- DRY RUN: {path.name} ({len(sql)} chars) ---[/cyan]")
        console.print(sql[:800] + ("…" if len(sql) > 800 else ""))
        return (True, 0)

    t0 = time.perf_counter()
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    except Exception as e:
        console.print(f"[red]FAILED[/red] {path.name}: {e}")
        return (False, 0)
    dur = int((time.perf_counter() - t0) * 1000)
    console.print(f"[green]ok[/green] {path.name}  ({dur} ms)")
    return (True, dur)


def run_inline(sql: str) -> int:
    """Run a one-off SQL snippet. Returns exit code."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            if result.returns_rows:
                rows = result.fetchall()
                if rows:
                    t = Table(show_header=True, header_style="bold")
                    for col in result.keys():
                        t.add_column(str(col))
                    for row in rows[:100]:
                        t.add_row(*(str(v) if v is not None else "" for v in row))
                    console.print(t)
                    if len(rows) > 100:
                        console.print(f"[yellow]... {len(rows) - 100} more rows[/yellow]")
                else:
                    console.print("[dim](0 rows)[/dim]")
            else:
                console.print("[green]ok[/green]")
    except Exception as e:
        console.print(f"[red]error:[/red] {e}")
        return 1
    return 0


def list_sql_files() -> list[Path]:
    return sorted(SQL_DIR.glob("*.sql"))


def cmd_all(pending_only: bool, dry_run: bool) -> int:
    ensure_migrations_table()
    files = list_sql_files()
    if not files:
        console.print("[yellow]No .sql files in sql/[/yellow]")
        return 0
    failed = 0
    skipped = 0
    for f in files:
        if pending_only:
            existing = already_applied(f.name)
            if existing == file_sha(f):
                skipped += 1
                continue
        ok, dur = run_sql_file(f, dry_run=dry_run)
        if not ok:
            failed += 1
            break
        if not dry_run:
            record_applied(f.name, file_sha(f), dur)
    console.print(
        f"\n[bold]Done.[/bold] "
        f"{len(files) - failed - skipped} applied, "
        f"{skipped} already-applied, "
        f"{failed} failed."
    )
    return 1 if failed else 0


def cmd_one(path_arg: str, dry_run: bool) -> int:
    ensure_migrations_table()
    path = (project_root / path_arg) if not Path(path_arg).is_absolute() else Path(path_arg)
    if not path.exists():
        alt = SQL_DIR / path_arg
        if alt.exists():
            path = alt
    ok, dur = run_sql_file(path, dry_run=dry_run)
    if ok and not dry_run:
        record_applied(path.name, file_sha(path), dur)
    return 0 if ok else 1


def cmd_status() -> int:
    ensure_migrations_table()
    with engine.connect() as conn:
        applied = {r[0]: r for r in conn.execute(text(
            "SELECT filename, applied_at, duration_ms FROM milb.schema_migrations"
        ))}
    files = list_sql_files()
    t = Table(title="SQL migrations status")
    for col in ("File", "Applied", "Duration", "State"):
        t.add_column(col)
    for f in files:
        row = applied.get(f.name)
        if row:
            t.add_row(f.name, str(row[1])[:19], f"{row[2] or 0} ms", "[green]applied[/green]")
        else:
            t.add_row(f.name, "-", "-", "[yellow]pending[/yellow]")
    console.print(t)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Psql-free SQL runner for this project.")
    p.add_argument("file", nargs="?", help="Path to .sql file (absolute, or relative to project / sql/)")
    p.add_argument("-c", "--command", help="Run an inline SQL snippet")
    p.add_argument("--all", action="store_true", help="Run every sql/*.sql file in order")
    p.add_argument("--pending", action="store_true", help="Only run files not already applied")
    p.add_argument("--status", action="store_true", help="Show applied/pending state")
    p.add_argument("--dry-run", action="store_true", help="Print SQL without executing")
    args = p.parse_args()

    if args.status:
        return cmd_status()
    if args.command:
        return run_inline(args.command)
    if args.all or args.pending:
        return cmd_all(pending_only=args.pending, dry_run=args.dry_run)
    if args.file:
        return cmd_one(args.file, dry_run=args.dry_run)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
