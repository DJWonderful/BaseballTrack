"""Export Postgres milb.* tables and views to Parquet for the deployed Streamlit app.

Run this after every data refresh (collect_all.py + the analytics pipeline) to
produce the static snapshot that Streamlit Cloud serves. The files live in
data/app/ and are committed to the repo.

    python scripts/export_for_app.py

JSONB columns are cast to TEXT so Parquet can round-trip them. The app code
already handles json.loads on string-valued JSON columns.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).parent.parent / ".env")

OUT_DIR = Path(__file__).parent.parent / "data" / "app"


def connect():
    url = (
        f"postgresql://{os.getenv('DB_USERNAME', 'postgres')}:"
        f"{os.getenv('DB_PASSWORD', 'postgres')}@"
        f"{os.getenv('DB_HOST', '127.0.0.1')}:"
        f"{os.getenv('DB_PORT', '5432')}/"
        f"{os.getenv('DB_NAME', 'baseball')}"
    )
    return create_engine(url)


def list_objects(conn) -> list[tuple[str, str]]:
    """Return [(kind, name)] for every table and view in the milb schema."""
    rows = conn.execute(text("""
        SELECT table_type, table_name
        FROM information_schema.tables
        WHERE table_schema = 'milb'
        ORDER BY table_type, table_name
    """)).fetchall()
    return [(r.table_type, r.table_name) for r in rows]


def get_jsonb_columns(conn, table_name: str) -> list[str]:
    rows = conn.execute(text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'milb' AND table_name = :t
          AND data_type IN ('jsonb', 'json')
    """), {"t": table_name}).fetchall()
    return [r.column_name for r in rows]


def build_select(conn, name: str) -> str:
    """SELECT * but with JSONB columns cast to TEXT so Parquet can store them."""
    jsonb_cols = set(get_jsonb_columns(conn, name))
    if not jsonb_cols:
        return f'SELECT * FROM milb."{name}"'

    cols = conn.execute(text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'milb' AND table_name = :t
        ORDER BY ordinal_position
    """), {"t": name}).fetchall()

    parts = []
    for (col,) in [(r.column_name,) for r in cols]:
        if col in jsonb_cols:
            parts.append(f'"{col}"::text AS "{col}"')
        else:
            parts.append(f'"{col}"')
    return f'SELECT {", ".join(parts)} FROM milb."{name}"'


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    engine = connect()
    with engine.connect() as conn:
        objects = list_objects(conn)
        print(f"Exporting {len(objects)} objects from milb.* to {OUT_DIR}")
        print("-" * 60)

        total_rows = 0
        total_bytes = 0
        t_start = time.time()

        for kind, name in objects:
            sql = build_select(conn, name)
            df = pd.read_sql(text(sql), conn)
            out_path = OUT_DIR / f"{name}.parquet"
            df.to_parquet(out_path, index=False, compression="snappy")

            size = out_path.stat().st_size
            total_rows += len(df)
            total_bytes += size
            tag = "V" if kind == "VIEW" else "T"
            print(f"  [{tag}] {name:40s} {len(df):>8,} rows  {size/1024:>8.1f} KB")

        elapsed = time.time() - t_start
        print("-" * 60)
        print(f"Total: {total_rows:,} rows  {total_bytes/1024/1024:.1f} MB  ({elapsed:.1f}s)")
        print(f"\nNext: git add data/ && git commit -m 'refresh data' && git push")

    return 0


if __name__ == "__main__":
    sys.exit(main())
