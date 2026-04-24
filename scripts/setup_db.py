"""Create the baseball database, milb schema, tables, indexes, and seed data."""

import sys
from pathlib import Path

import sqlalchemy
from sqlalchemy import create_engine, text
from rich.console import Console

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.db.connection import get_database_url
from src.utils.logger import get_logger

logger = get_logger("setup_db")
console = Console()
sql_dir = project_root / "sql"


def create_database():
    """Create the 'baseball' database if it doesn't exist."""
    engine = create_engine(get_database_url(database="postgres"), isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'baseball'")
        )
        if result.fetchone() is None:
            conn.execute(text("CREATE DATABASE baseball"))
            logger.info("Created database 'baseball'")
        else:
            logger.info("Database 'baseball' already exists")
    engine.dispose()


def run_sql_file(engine, filepath: Path):
    """Execute a SQL file against the given engine."""
    sql = filepath.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(sql))
    logger.info(f"Executed {filepath.name}")


def main():
    console.print("\n[bold blue]MiLB Data Pipeline — Database Setup[/bold blue]\n")

    # Step 1: Create database
    console.print("[yellow]1. Creating database...[/yellow]")
    create_database()

    # Step 2: Connect to baseball database
    engine = create_engine(get_database_url())

    # Step 3: Create schema + tables + indexes
    console.print("[yellow]2. Creating schema, tables, and indexes...[/yellow]")
    run_sql_file(engine, sql_dir / "002_create_schema.sql")

    # Step 4: Seed reference data
    console.print("[yellow]3. Seeding reference data...[/yellow]")
    run_sql_file(engine, sql_dir / "003_seed_reference.sql")

    # Step 5: Verify
    console.print("[yellow]4. Verifying...[/yellow]")
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'milb'
            ORDER BY table_name
        """))
        tables = [row[0] for row in result]
        console.print(f"   Tables created: {len(tables)}")
        for t in tables:
            console.print(f"   - milb.{t}")

        result = conn.execute(text("SELECT COUNT(*) FROM milb.sports"))
        count = result.scalar()
        console.print(f"   Sports seeded: {count}")

    engine.dispose()
    console.print("\n[bold green]Database setup complete![/bold green]\n")


if __name__ == "__main__":
    main()
